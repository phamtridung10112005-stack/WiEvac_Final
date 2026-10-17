"""A/B replay: the same recorded CSI through the real RX pipeline, AI off vs AI on.

Each session is replayed from a cold boot (no preset baseline), frame by frame,
exactly as the RX would see it. The only difference between A and B is the
tiny_ai_model_weights.h compiled in (NOT_READY vs a trained candidate).

Per emitted 1 s window we look at the operator label at that moment and count:
  * learn_while_occupied: baseline_version or baseline_center changed while
    OCCUPIED (the formula learned a person as the empty corridor);
  * passable_while_occupied: PASSABLE shown while a person was there;
  * alarm_while_empty: DEGRADED/BLOCKED shown while the corridor was empty;
  * unknown: windows without a score (cost of being cautious).

Usage:
  python pi/training/evaluate_ab_replay.py --model models/candidates/<dir>
"""
from __future__ import annotations

import argparse
import gzip
import json
import shutil
import struct
import subprocess
import sys
import tempfile
from bisect import bisect_right
from pathlib import Path

WIEVAC = Path(__file__).resolve().parents[2]
MAIN = WIEVAC / "firmware" / "receiver" / "main"
HARNESS = WIEVAC / "tests" / "replay_ab_host_main.c"
LABEL_CODE = {"EMPTY": 0, "OCCUPIED": 1, "EXCLUDED": 2}
NOT_READY_WEIGHTS = """#ifndef WIEVAC_TINY_AI_MODEL_WEIGHTS_H
#define WIEVAC_TINY_AI_MODEL_WEIGHTS_H
#define TINY_AI_MODEL_PRESENT 0
#define TINY_AI_MODEL_RUNG 0
#define TINY_AI_MODEL_VERSION "NOT_READY"
#define TINY_AI_MODEL_HASH ""
static const unsigned tiny_ai_weight_blob_len = 0U;
static const unsigned char tiny_ai_weight_blob[1] = {0};
#endif
"""
FRAME_BYTES = struct.calcsize("<QIf52fB")
STATE_NAME = {0: "PASSABLE", 1: "DEGRADED", 2: "BLOCKED", 3: "UNKNOWN"}


def find_cc() -> str:
    for name in ("gcc", "clang", "cc"):
        found = shutil.which(name)
        if found:
            return found
    bundled = Path(r"C:\D\devc\Dev-Cpp\MinGW64\bin\gcc.exe")
    if bundled.exists():
        return str(bundled)
    raise SystemExit("host C compiler not found")


def build(out_dir: Path, weights: Path | None) -> Path:
    """Compile the harness. With weights, the gatekeeper and its headers are
    copied next to the candidate header so its quoted include picks it up."""
    src = out_dir / "src"
    src.mkdir(parents=True, exist_ok=True)
    gate_files = ["tiny_ai_gatekeeper.c", "tiny_ai_gatekeeper.h", "tiny_ai_features.h"]
    for name in gate_files:
        shutil.copy2(MAIN / name, src / name)
    if weights:
        shutil.copy2(weights, src / "tiny_ai_model_weights.h")
    else:
        (src / "tiny_ai_model_weights.h").write_text(NOT_READY_WEIGHTS, encoding="utf-8")
    exe = out_dir / "replay.exe"
    command = [
        find_cc(), "-std=c11", "-O2", "-ffp-contract=off", "-I", str(src), "-I", str(MAIN),
        "-o", str(exe), str(HARNESS), str(MAIN / "edge_result_v5_pipeline.c"),
        str(MAIN / "formula_flex.c"), str(MAIN / "noise_filter.c"),
        str(src / "tiny_ai_gatekeeper.c"), str(MAIN / "tiny_ai_features.c"), "-lm",
    ]
    subprocess.check_call(command, cwd=str(WIEVAC))
    return exe


def load_session(path: Path) -> tuple[dict, bytes]:
    session: dict = {}
    frames: list[tuple] = []
    label_ts: list[int] = []
    label_val: list[int] = []
    with gzip.open(path, "rt", encoding="utf-8") as handle:
        for line in handle:
            record = json.loads(line)
            kind = record.get("type")
            if kind == "session":
                session = record
            elif kind == "csi":
                frames.append((int(record["ts_us"]), int(record["seq"]),
                               float(record.get("applied_scale") or 1.0), record["amp"]))
            elif kind == "feature":
                label_ts.append(int(record["ts_us"]))
                label_val.append(LABEL_CODE.get(record.get("label", "EXCLUDED"), 2))
    default = 0 if session.get("session_label") == "EMPTY" else 2
    out = bytearray()
    for ts, seq, scale, amp in frames:
        if len(amp) != 52:
            continue
        index = bisect_right(label_ts, ts) - 1
        label = label_val[index] if index >= 0 else default
        out += struct.pack("<QIf52fB", ts, seq & 0xFFFFFFFF, scale, *amp, label)
    session["frames"] = len(frames)
    return session, bytes(out)


def run(exe: Path, payload: bytes, settle_s: int = 0) -> tuple[str, list[dict]]:
    result = subprocess.run([str(exe), str(settle_s)], input=payload, capture_output=True, check=True)
    model = "?"
    rows = []
    for line in result.stdout.decode().splitlines():
        parts = line.split()
        if parts[0] == "MODEL":
            model = parts[1]
        elif parts[0] == "W":
            rows.append({
                "end_us": int(parts[1]), "label": int(parts[2]), "state": int(parts[3]),
                "score_valid": parts[4] == "1", "score": float(parts[5]),
                "bver": int(parts[6]), "bcenter": float(parts[7]), "bready": parts[8] == "1",
                "bstate": int(parts[9]), "cap_locked": parts[10] == "1",
                "width": float(parts[11]), "reason": int(parts[12]), "model_state": int(parts[13]),
                "settling": len(parts) > 14 and parts[14] == "BOOT_SETTLE",
            })
    return model, rows


def score(rows: list[dict]) -> dict:
    stats = {"windows": len(rows), "empty": 0, "occupied": 0, "unknown": 0,
             "learn_while_occupied": 0, "passable_while_occupied": 0,
             "alarm_while_empty": 0, "baseline_updates": 0, "first_ready_s": None}
    previous = None
    stats["settle_windows"] = sum(1 for row in rows if row["settling"])
    rows = [row for row in rows if not row["settling"]]
    stats["windows"] = len(rows)
    start = rows[0]["end_us"] if rows else 0
    for row in rows:
        label = row["label"]
        if row["state"] == 3 or not row["score_valid"]:
            stats["unknown"] += 1
        if previous is not None:
            changed = (row["bver"] != previous["bver"] or
                       abs(row["bcenter"] - previous["bcenter"]) > 1e-4)
            if changed:
                stats["baseline_updates"] += 1
                if label == 1:
                    stats["learn_while_occupied"] += 1
        if row["bready"] and stats["first_ready_s"] is None:
            stats["first_ready_s"] = round((row["end_us"] - start) / 1e6, 1)
        if label == 0:
            stats["empty"] += 1
            if row["score_valid"] and row["state"] in (1, 2):
                stats["alarm_while_empty"] += 1
        elif label == 1:
            stats["occupied"] += 1
            if row["score_valid"] and row["state"] == 0:
                stats["passable_while_occupied"] += 1
        previous = row
    return stats


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--model", required=True, type=Path,
                        help="candidate dir containing tiny_ai_model_weights.h")
    parser.add_argument("--data", type=Path, default=WIEVAC / "data" / "datasets")
    parser.add_argument("--out", type=Path, default=None, help="write JSON report here")
    parser.add_argument("--settle-s", type=int, default=120,
                        help="boot settle seconds, as CONFIG_WIEVAC_BOOT_SETTLE_S")
    parser.add_argument("--boot-offsets", default="0",
                        help="comma list of seconds into each session at which to 'power on'")
    args = parser.parse_args()
    weights = args.model / "tiny_ai_model_weights.h"
    if not weights.exists():
        raise SystemExit(f"missing {weights}")
    sessions = sorted(args.data.glob("corridor_*/*.jsonl.gz"))
    report = {"model": str(args.model), "sessions": []}
    with tempfile.TemporaryDirectory() as tmp:
        exe_off = build(Path(tmp) / "off", None)
        exe_on = build(Path(tmp) / "on", weights)
        offsets = [int(item) for item in args.boot_offsets.split(",") if item.strip()]
        for path in sessions:
            session, payload = load_session(path)
            for offset in offsets:
                start = offset * 100 * FRAME_BYTES  # ~100 frames/s; a boot mid-session
                if start >= len(payload):
                    continue
                entry = {"file": f"{path.parent.name}/{path.name}", "boot_offset_s": offset,
                         "corridor": session.get("corridor_id"), "role": session.get("role"),
                         "link": session.get("link_id"), "frames": session["frames"]}
                for tag, exe in (("ai_off", exe_off), ("ai_on", exe_on)):
                    model, rows = run(exe, payload[start:], args.settle_s)
                    entry[tag] = {"model": model, **score(rows)}
                report["sessions"].append(entry)
                print(json.dumps(entry), flush=True)
    keys = ["windows", "empty", "occupied", "unknown", "learn_while_occupied",
            "passable_while_occupied", "alarm_while_empty", "baseline_updates"]
    for role in ("train", "test"):
        for tag in ("ai_off", "ai_on"):
            total = {key: sum(s[tag][key] for s in report["sessions"] if s["role"] == role)
                     for key in keys}
            report[f"{role}_{tag}"] = total
            print(role, tag, json.dumps(total))
    if args.out:
        args.out.write_text(json.dumps(report, indent=2), encoding="utf-8")
    return 0


if __name__ == "__main__":
    sys.exit(main())
