#!/usr/bin/env python3
"""Export recorder JSONL without mutating captures or inventing labels.

The exporter keeps every source record and adds an explicit dataset view. Labels
come only from an operator sidecar or dashboard occupancy intervals; technical
quality classifications are kept as evidence and are never converted into
occupancy labels.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any, Mapping, Sequence

OCCUPANCY = {"empty", "person_present", "unknown"}
OCCUPANCY_ALIASES = {
    "empty": "empty",
    "person_present": "person_present",
    "human_present": "person_present",
    "unknown": "unknown",
}
NUISANCE = {
    "none", "rf_interference", "packet_loss", "gain_transition",
    "multipath_shift", "environmental_change", "device_activity", "unknown",
}


def _technical_nuisance(record: Mapping[str, Any]) -> str:
    """Return a conservative nuisance hint, never an occupancy label."""
    classification = str(record.get("window_classification", ""))
    reason = str(record.get("window_reason", "")).lower()
    if classification == "interference":
        return "rf_interference"
    if any(token in reason for token in ("packet_loss", "sequence_gap", "udp_loss")):
        return "packet_loss"
    if "gain" in reason:
        return "gain_transition"
    return "unknown"


def _normalize_occupancy(value: Any) -> str | None:
    if value is None:
        return None
    token = str(value).strip().lower()
    if not token:
        return None
    occupancy = OCCUPANCY_ALIASES.get(token)
    if occupancy is None:
        raise ValueError(f"invalid occupancy_label: {value!r}")
    return occupancy


def _collapse_intervals(rows: Sequence[Mapping[str, Any]]) -> list[dict[str, Any]]:
    latest: dict[tuple[str, int], dict[str, Any]] = {}
    for row in rows:
        if not isinstance(row, Mapping):
            continue
        record_type = row.get("record_type")
        if record_type not in (None, "occupancy_interval"):
            continue
        if row.get("occupancy") is None or row.get("t_start_us") is None or row.get("link_id") in (None, ""):
            continue
        key = (str(row["link_id"]), int(row["t_start_us"]))
        latest[key] = dict(row)
    return list(latest.values())


def _extract_intervals(sidecar: Mapping[str, Any] | Sequence[Mapping[str, Any]] | None) -> list[dict[str, Any]]:
    if sidecar is None:
        return []
    if isinstance(sidecar, Sequence) and not isinstance(sidecar, (str, bytes, Mapping)):
        return _collapse_intervals([item for item in sidecar if isinstance(item, Mapping)])
    if isinstance(sidecar, Mapping) and isinstance(sidecar.get("intervals"), list):
        return _collapse_intervals([item for item in sidecar["intervals"] if isinstance(item, Mapping)])
    return []


def _session_entry(sidecar: Mapping[str, Any] | Sequence[Mapping[str, Any]] | None, session: str) -> Mapping[str, Any]:
    if not isinstance(sidecar, Mapping):
        return {}
    entry = sidecar.get(session, sidecar.get("*", {}))
    return entry if isinstance(entry, Mapping) else {}


def _record_stamp_us(record: Mapping[str, Any]) -> int | None:
    for key in ("arrival_timestamp_us", "received_at_us"):
        value = record.get(key)
        if isinstance(value, bool):
            continue
        if isinstance(value, (int, float)):
            return int(value)
    return None


def _interval_occupancy(intervals: Sequence[Mapping[str, Any]], record: Mapping[str, Any]) -> tuple[str | None, bool]:
    link_id = str(record.get("link_id") or "")
    stamp = _record_stamp_us(record)
    if not link_id or stamp is None:
        return None, False
    matches: list[Mapping[str, Any]] = []
    for item in intervals:
        if str(item.get("link_id") or "") != link_id:
            continue
        start = int(item["t_start_us"])
        end = item.get("t_end_us")
        if stamp < start:
            continue
        if end is not None and stamp >= int(end):
            continue
        matches.append(item)
    if not matches:
        return None, False
    chosen = max(matches, key=lambda item: int(item["t_start_us"]))
    return _normalize_occupancy(chosen.get("occupancy")), True


def _labels(sidecar: Mapping[str, Any] | Sequence[Mapping[str, Any]] | None, record: Mapping[str, Any]) -> tuple[str | None, str | None, str]:
    occupancy, from_interval = _interval_occupancy(_extract_intervals(sidecar), record)
    entry = _session_entry(sidecar, str(record.get("session_id", "")))
    if occupancy is None:
        occupancy = _normalize_occupancy(entry.get("occupancy_label"))
    nuisance = entry.get("nuisance_label")
    if nuisance is not None and nuisance not in NUISANCE:
        raise ValueError(f"invalid nuisance_label: {nuisance!r}")
    if occupancy is not None and occupancy not in OCCUPANCY:
        raise ValueError(f"invalid occupancy_label: {occupancy!r}")
    if from_interval:
        source = "operator_dashboard"
    elif occupancy or nuisance:
        source = "operator_sidecar"
    else:
        source = "unlabeled"
    return occupancy, nuisance, source


def load_labels(path: Path) -> Mapping[str, Any] | list[dict[str, Any]]:
    text = path.read_text(encoding="utf-8")
    if path.suffix.lower() == ".jsonl":
        rows: list[dict[str, Any]] = []
        for line_number, line in enumerate(text.splitlines(), 1):
            if not line.strip():
                continue
            row = json.loads(line)
            if not isinstance(row, dict):
                raise ValueError(f"labels line {line_number}: record must be an object")
            rows.append(row)
        return {"intervals": _collapse_intervals(rows)}
    value = json.loads(text)
    if not isinstance(value, dict):
        raise SystemExit("labels sidecar must be a JSON object")
    if isinstance(value.get("intervals"), list):
        copied = dict(value)
        copied["intervals"] = _collapse_intervals(
            [item for item in value["intervals"] if isinstance(item, Mapping)]
        )
        return copied
    return value


def export_jsonl(source: Path, destination: Path, labels: Mapping[str, Any] | Sequence[Mapping[str, Any]] | None = None) -> int:
    """Copy all records and append explicit, nullable training labels."""
    sidecar = labels or {}
    destination.parent.mkdir(parents=True, exist_ok=True)
    count = 0
    with source.open("r", encoding="utf-8") as src, destination.open("w", encoding="utf-8") as dst:
        for line_number, line in enumerate(src, 1):
            if not line.strip():
                continue
            record = json.loads(line)
            if not isinstance(record, dict):
                raise ValueError(f"line {line_number}: record must be an object")
            occupancy, nuisance, source_name = _labels(sidecar, record)
            output = dict(record)
            output["dataset_schema_version"] = 1
            output["occupancy_label"] = occupancy
            output["nuisance_label"] = nuisance or _technical_nuisance(record)
            output["label_source"] = source_name
            output["training_eligible"] = bool(occupancy and nuisance)
            dst.write(json.dumps(output, ensure_ascii=True, separators=(",", ":")) + "\n")
            count += 1
    return count


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("source", type=Path)
    parser.add_argument("destination", type=Path)
    parser.add_argument("--labels", type=Path, help="JSON sidecar or dashboard occupancy JSONL")
    args = parser.parse_args()
    labels: Mapping[str, Any] | list[dict[str, Any]] = load_labels(args.labels) if args.labels else {}
    print(f"exported_records={export_jsonl(args.source, args.destination, labels)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
