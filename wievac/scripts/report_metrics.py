#!/usr/bin/env python3
"""Summarize canonical ``analysis_metric`` JSONL records by session and link.

The recorder JSONL remains the source of truth.  This report only reads it and
keeps RX-1/link 1 and RX-2/link 2 distributions separate.
"""

from __future__ import annotations

import argparse
import json
import math
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any, Iterable, Mapping


NUMERIC_FIELDS = (
    "dynamic_received_rate_hz", "dynamic_valid_transport_rate_hz",
    "dynamic_analysis_accepted_rate_hz", "feature_received_rate_hz",
    "feature_rate_hz", "feature_accepted_rate_hz", "motion_median", "motion_p95", "motion_rms",
    "temporal_spectral_energy", "spectral_entropy", "spectral_bandwidth",
    "coherent_motion", "shape_distance", "quality_score", "packet_loss_ratio",
)
P05_FIELDS = {"dynamic_received_rate_hz", "dynamic_valid_transport_rate_hz",
              "dynamic_analysis_accepted_rate_hz", "feature_received_rate_hz", "feature_rate_hz",
              "feature_accepted_rate_hz", "coherent_motion", "quality_score"}


def _finite(value: Any) -> float | None:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number if math.isfinite(number) else None


def _percentile(values: list[float], fraction: float) -> float | None:
    if not values:
        return None
    ordered = sorted(values)
    if len(ordered) == 1:
        return ordered[0]
    position = (len(ordered) - 1) * fraction
    lower = math.floor(position)
    upper = math.ceil(position)
    if lower == upper:
        return ordered[lower]
    weight = position - lower
    return ordered[lower] + (ordered[upper] - ordered[lower]) * weight


def _metric_value(record: Mapping[str, Any], name: str) -> Any:
    if name == "feature_rate_hz":
        name = "feature_received_rate_hz"
    if name in record:
        return record.get(name)
    features = record.get("features", {})
    if isinstance(features, Mapping) and name in features:
        return features.get(name)
    quality = record.get("quality", {})
    if isinstance(quality, Mapping) and name in quality:
        return quality.get(name)
    return None


def summarize_records(records: Iterable[Mapping[str, Any]]) -> dict[str, Any]:
    groups: dict[tuple[str, int], list[Mapping[str, Any]]] = defaultdict(list)
    for record in records:
        if str(record.get("record_type", "")) != "analysis_metric":
            continue
        session_id = str(record.get("session_id", ""))
        link_id = int(record.get("link_id", 0))
        if session_id and link_id:
            groups[(session_id, link_id)].append(record)

    output: dict[str, Any] = {"schema_version": 1, "sessions": {}}
    for (session_id, link_id), entries in sorted(groups.items()):
        link: dict[str, Any] = {"link_id": link_id, "session_id": session_id,
                                "record_count": len(entries), "state_counts": dict(Counter(
                                    str(item.get("decision", {}).get("link_state", item.get("state", "UNKNOWN")))
                                    for item in entries))}
        for field in NUMERIC_FIELDS:
            values = [value for item in entries if (value := _finite(_metric_value(item, field))) is not None]
            stats: dict[str, Any] = {"count": len(values), "median": _percentile(values, 0.5),
                                     "p95": _percentile(values, 0.95)}
            if field in P05_FIELDS:
                stats["p05"] = _percentile(values, 0.05)
            link[field] = stats
        link["baseline_eligible_ratio"] = sum(
            bool(item.get("decision", {}).get("baseline_eligible", False)) for item in entries
        ) / len(entries)
        link["packet_loss"] = sum(int(item.get("quality", {}).get("packet_loss", 0) or 0) for item in entries)
        link["sequence_gap"] = sum(int(item.get("quality", {}).get("sequence_gap", 0) or 0) for item in entries)
        link["invalid_csi"] = sum(int(item.get("quality", {}).get("invalid_csi", 0) or 0) for item in entries)
        output["sessions"].setdefault(session_id, {})[str(link_id)] = link
    return output


def summarize_jsonl(source: Path, destination: Path | None = None) -> dict[str, Any]:
    records = []
    with source.open("r", encoding="utf-8") as stream:
        for line_number, line in enumerate(stream, 1):
            if not line.strip():
                continue
            value = json.loads(line)
            if not isinstance(value, Mapping):
                raise ValueError(f"line {line_number}: record must be an object")
            records.append(value)
    summary = summarize_records(records)
    if destination is not None:
        destination.parent.mkdir(parents=True, exist_ok=True)
        destination.write_text(json.dumps(summary, ensure_ascii=True, indent=2) + "\n", encoding="utf-8")
    return summary


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("source", type=Path, help="completed session JSONL")
    parser.add_argument("destination", type=Path, nargs="?", help="summary JSON output")
    args = parser.parse_args()
    summary = summarize_jsonl(args.source, args.destination)
    print(json.dumps({"sessions": len(summary["sessions"]),
                      "links": sum(len(links) for links in summary["sessions"].values())},
                     sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
