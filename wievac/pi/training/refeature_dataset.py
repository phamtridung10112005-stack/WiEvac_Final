"""Recompute Tiny-AI feature vectors of recorded sessions with the current
extractor, keeping CSI, labels and label events. Use after a feature rule
changes (e.g. the median noise-reference init) so training matches firmware.

The vectors come from the firmware C extractor (bit-parity with
pi/app/tiny_ai_features.py is covered by tests; the Python one is ~100x
slower). Labels, quality fields and the file layout come from the collector.

  python pi/training/refeature_dataset.py --src data/datasets --dst data/datasets_v2
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
from pathlib import Path

WIEVAC = Path(__file__).resolve().parents[2]
MAIN = WIEVAC / "firmware" / "receiver" / "main"
sys.path.insert(0, str(WIEVAC / "scripts"))
sys.path.insert(0, str(WIEVAC / "pi" / "app"))

import collect_empty_corridor_dataset as collector  # noqa: E402

HOST_MAIN = r"""
#include "tiny_ai_features.h"
#include <stdio.h>
#include <fcntl.h>
#include <io.h>
int main(void)
{
    static tiny_ai_extractor_t extractor;
    unsigned char buf[8 + 4 * TINY_AI_TONES];
    float amplitudes[TINY_AI_TONES];
    float features[TINY_AI_FEATURES];
    unsigned long long timestamp;
    unsigned int index = 0U;
    _setmode(_fileno(stdin), _O_BINARY);
    _setmode(_fileno(stdout), _O_BINARY);
    tiny_ai_extractor_init(&extractor);
    while (fread(buf, 1, sizeof(buf), stdin) == sizeof(buf)) {
        memcpy(&timestamp, buf, 8);
        memcpy(amplitudes, buf + 8, sizeof(amplitudes));
        if (tiny_ai_extractor_push(&extractor, timestamp, amplitudes, features)) {
            fwrite(&index, 4, 1, stdout);
            fwrite(features, sizeof(float), TINY_AI_FEATURES, stdout);
        }
        ++index;
    }
    return 0;
}
"""


def find_cc() -> str:
    for name in ("gcc", "clang", "cc"):
        found = shutil.which(name)
        if found:
            return found
    bundled = Path(r"C:\D\devc\Dev-Cpp\MinGW64\bin\gcc.exe")
    if bundled.exists():
        return str(bundled)
    raise SystemExit("host C compiler not found")


def build(folder: Path) -> Path:
    source = folder / "refeature_main.c"
    source.write_text("#include <string.h>\n" + HOST_MAIN, encoding="utf-8")
    exe = folder / "refeature.exe"
    subprocess.check_call([
        find_cc(), "-std=c11", "-O2", "-ffp-contract=off", "-fno-fast-math", "-msse2", "-mfpmath=sse",
        "-I", str(MAIN), "-o", str(exe), str(source), str(MAIN / "tiny_ai_features.c"), "-lm",
    ])
    return exe


def refeature(src: Path, dst: Path, exe: Path) -> dict:
    meta: dict = {}
    kept: list[tuple] = []
    events: list[dict] = []
    quality: dict = {}
    with gzip.open(src, "rt", encoding="utf-8") as handle:
        for line in handle:
            record = json.loads(line)
            kind = record.get("type")
            if kind == "session":
                meta = {k: v for k, v in record.items()
                        if k not in {"type", "session_id", "gain_response_s"}}
            elif kind == "csi":
                gain = collector._gain_meta(record, record.get("gain_response_s"))
                kept.append((int(record["ts_us"]), int(record["seq"]), record["amp"],
                             record.get("pi_mono_us"), gain))
            elif kind == "label_event":
                events.append(record)
            elif kind == "quality":
                quality = record
    payload = bytearray()
    for ts, _seq, amp, _pi, _gain in kept:
        payload += struct.pack("<Q52f", ts, *amp)
    out = subprocess.run([str(exe)], input=bytes(payload), capture_output=True, check=True).stdout
    step = 4 + 4 * 42
    produced = []
    first_ts = kept[0][0] if kept else 0
    for offset in range(0, len(out) - step + 1, step):
        index = struct.unpack_from("<I", out, offset)[0]
        features = list(struct.unpack_from("<42f", out, offset + 4))
        item = kept[index]
        label_us = item[0] if item[3] is None else int(item[3])
        produced.append({
            "source": item, "features": features, "label_us": label_us,
            # quality looks back at most 30 s; ~5000 frames at 100 Hz is ample
            "quality": collector.frame_quality(kept[max(0, index - 5000): index + 1],
                                               min(index, 5000), first_ts, features),
        })
    fps = float(quality.get("frames_per_s") or 0.0)
    stats = {"dropped": quality.get("dropped", 0),
             "elapsed_s": (len(kept) / fps) if fps > 0 else None}
    dst.parent.mkdir(parents=True, exist_ok=True)
    summary = collector._write_outputs(dst, None, meta, kept, produced, events, stats)
    return {"src": str(src), "dst": str(dst), "vectors": len(produced),
            "empty": summary["empty_vectors"], "occupied": summary["occupied_vectors"],
            "excluded": summary["excluded_vectors"]}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--src", type=Path, required=True)
    parser.add_argument("--dst", type=Path, required=True)
    args = parser.parse_args()
    if args.dst.resolve() == args.src.resolve():
        raise SystemExit("--dst must differ from --src")
    with tempfile.TemporaryDirectory() as tmp:
        exe = build(Path(tmp))
        for path in sorted(args.src.glob("corridor_*/*.jsonl.gz")):
            dst = args.dst / path.parent.name / path.name
            print(json.dumps(refeature(path, dst, exe)), flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
