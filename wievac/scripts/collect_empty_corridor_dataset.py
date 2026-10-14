"""Record an empty-corridor session as raw amplitudes plus the 42-vector.

The compact Pi path does not receive raw CSI. Use --mock, or --csi-jsonl
from a capture that already has ts_us/seq/amp.
"""

from __future__ import annotations

import argparse
import gzip
import json
import random
import sys
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "pi" / "app"))

from tiny_ai_features import FEATURES, Extractor  # noqa: E402

LABELS = {"EMPTY", "OCCUPIED_VAL"}
MOCK_HZ = 8
MOCK_STEP_US = 125_000


def corridor_dirname(corridor_id: str) -> str:
    text = corridor_id.strip()
    lowered = text.lower()
    for prefix in ("corridor-", "corridor_"):
        if lowered.startswith(prefix):
            text = text[len(prefix):]
            break
    safe = "".join(char if char.isalnum() else "_" for char in text)
    return f"corridor_{safe or 'unknown'}"


def sequence_loss_ratio(sequences: list[int]) -> float:
    lost = 0
    span = 0
    for left, right in zip(sequences, sequences[1:]):
        if right <= left:
            continue
        lost += right - left - 1
        span += right - left
    return lost / span if span else 0.0


def disturbance_warning(features: list[float], label: str) -> bool:
    if label != "EMPTY" or len(features) != FEATURES:
        return False
    lowband = features[32:40]
    return sum(lowband) / len(lowband) > 4.0


def mock_frames(duration_min: float, seed: int = 1) -> list[tuple[int, int, list[float]]]:
    rng = random.Random(seed)
    count = max(1, int(round(duration_min * 60 * MOCK_HZ)))
    timestamp = 1_000_000
    frames = []
    for index in range(count):
        amplitudes = [1.0 + 0.02 * rng.gauss(0.0, 1.0) for _ in range(52)]
        frames.append((timestamp, index + 1, amplitudes))
        timestamp += MOCK_STEP_US
    return frames


def _load_jsonl(path: Path) -> list[tuple[int, int, list[float]]]:
    frames = []
    with path.open("r", encoding="utf-8") as handle:
        for line in handle:
            if not line.strip():
                continue
            record = json.loads(line)
            amplitudes = record.get("amp")
            if amplitudes is None:
                continue
            frames.append((int(record["ts_us"]), int(record["seq"]), [float(value) for value in amplitudes]))
    return frames


def collect(frames: list[tuple[int, int, list[float]]], meta: dict, out_root: Path) -> dict:
    folder = out_root / corridor_dirname(meta["corridor_id"])
    folder.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ")
    session_id = f"{meta['session_label'].lower()}-{stamp}"
    path = folder / f"{session_id}.jsonl.gz"
    extractor = Extractor()
    dropped = 0
    kept_seq: list[int] = []
    warnings = 0
    feature_count = 0
    last_ts = None
    seen = set()
    with gzip.open(path, "wt", encoding="utf-8") as handle:
        session = {"type": "session", "session_id": session_id, **meta}
        handle.write(json.dumps(session) + "\n")
        for timestamp, sequence, amplitudes in frames:
            if len(amplitudes) != 52 or any(value != value for value in amplitudes):
                dropped += 1
                continue
            if sequence in seen or (last_ts is not None and timestamp <= last_ts):
                dropped += 1
                continue
            seen.add(sequence)
            last_ts = timestamp
            kept_seq.append(sequence)
            handle.write(json.dumps({
                "type": "csi", "ts_us": timestamp, "link_id": meta["link_id"],
                "seq": sequence, "amp": amplitudes,
            }) + "\n")
            features = extractor.push(timestamp, amplitudes)
            if features is None:
                continue
            feature_count += 1
            warned = disturbance_warning(features, meta["session_label"])
            warnings += int(warned)
            if warned:
                print(f"EMPTY disturbance at seq {sequence}", file=sys.stderr)
            handle.write(json.dumps({
                "type": "feature", "ts_us": timestamp, "link_id": meta["link_id"],
                "seq": sequence, "features": features, "complete": True,
                "disturbance_warning": warned,
            }) + "\n")
        loss = sequence_loss_ratio(kept_seq)
        quality = {
            "type": "quality", "dropped": dropped, "kept": len(kept_seq),
            "loss_ratio": loss, "feature_count": feature_count,
            "empty_disturbance_warnings": warnings,
        }
        handle.write(json.dumps(quality) + "\n")
    if loss > 0.02:
        print(f"packet loss {loss:.3f}", file=sys.stderr)
    return {"path": path, **quality}


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Collect an empty-corridor feature session.")
    parser.add_argument("--corridor-id", required=True)
    parser.add_argument("--session-label", required=True, choices=sorted(LABELS))
    parser.add_argument("--session-env", required=True)
    parser.add_argument("--tx-h", required=True, type=float)
    parser.add_argument("--rx-h", required=True, type=float)
    parser.add_argument("--duration-min", type=float, default=10.0)
    parser.add_argument("--link-id", default="link-1")
    parser.add_argument("--out-root", type=Path, default=ROOT / "data" / "datasets")
    parser.add_argument("--mock", action="store_true")
    parser.add_argument("--csi-jsonl", type=Path)
    args = parser.parse_args(argv)
    if args.duration_min <= 0.0 or args.duration_min > 180.0:
        print("duration-min must be in (0, 180]", file=sys.stderr)
        return 2
    if args.mock:
        frames = mock_frames(args.duration_min)
    elif args.csi_jsonl is not None:
        frames = _load_jsonl(args.csi_jsonl)
    else:
        print("compact Pi has no raw CSI socket; use --mock or --csi-jsonl", file=sys.stderr)
        return 2
    if not frames:
        print("no frames", file=sys.stderr)
        return 1
    meta = {
        "corridor_id": args.corridor_id,
        "session_label": args.session_label,
        "session_env": args.session_env,
        "tx_h": args.tx_h,
        "rx_h": args.rx_h,
        "duration_min": args.duration_min,
        "link_id": args.link_id,
    }
    result = collect(frames, meta, args.out_root)
    print(f"wrote {result['path']} kept={result['kept']} features={result['feature_count']} loss={result['loss_ratio']:.4f}")
    return 0 if result["kept"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
