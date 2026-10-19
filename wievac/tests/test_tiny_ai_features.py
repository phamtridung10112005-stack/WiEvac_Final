"""Parity and gate checks for the empty-corridor novelty scaffold."""

from __future__ import annotations

import argparse
import gzip
import io
import json
import math
import os
import re
import shutil
import struct
import subprocess
import sys
import tempfile
import unittest
from contextlib import redirect_stderr, redirect_stdout
from unittest import mock
from datetime import datetime
from pathlib import Path

WIEVAC = Path(__file__).resolve().parents[1]
MAIN = WIEVAC / "firmware" / "receiver" / "main"
sys.path.insert(0, str(WIEVAC / "pi" / "app"))
sys.path.insert(0, str(WIEVAC / "pi" / "training"))
sys.path.insert(0, str(WIEVAC / "scripts"))

import collect_empty_corridor_dataset as collector  # noqa: E402
from collect_empty_corridor_dataset import (  # noqa: E402
    collect, disturbance_warning, encode_wraw, feature_label, loss_30s, mock_frames,
    parse_start_at, parse_wraw, run_udp_session, schedule_slices, sequence_loss_ratio,
    short_coverage, take_datagram, _read_key, _udp_watch,
)
from evaluate_loco import (  # noqa: E402
    auroc, choose_deploy_rung, fit_rung, novelty, pack_blob, score_rung1_f32, score_rung2_f32,
    score_rung3_f32, select_rung, tpr_at_fpr,
)
from tiny_ai_features import FEATURES, GOERTZEL_COS, Extractor, amplitudes_from_csi, f32  # noqa: E402
from train_tiny_ai import (  # noqa: E402
    calibrate_rungs, filter_session, install_candidate, load_datasets, preprocess_corpus, write_artifact,
)

ABC_SHA = "ba7816bf8f01cfea414140de5dae2223b00361a396177a9cb410ff61f20015ad"


def find_cc() -> str | None:
    env = os.environ.get("TINY_AI_CC")
    if env:
        return env
    for name in ("gcc", "clang", "cc"):
        found = shutil.which(name)
        if found:
            return found
    bundled = Path(r"C:\D\devc\Dev-Cpp\MinGW64\bin\gcc.exe")
    if bundled.exists():
        return str(bundled)
    return None


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


def compile_host(cc: str, sources: list[Path], out: Path, include: list[Path], define: str) -> None:
    command = [
        cc, "-std=c11", "-O2", "-ffp-contract=off", "-fno-fast-math", "-msse2", "-mfpmath=sse",
        f"-D{define}",
    ]
    for path in include:
        command.extend(["-I", str(path)])
    command.extend(["-o", str(out), *[str(path) for path in sources]])
    subprocess.check_call(command, cwd=str(WIEVAC))


class TinyAiFeatureParityTests(unittest.TestCase):
    def test_tables_and_python_window_emit(self) -> None:
        source = (MAIN / "tiny_ai_features.c").read_text(encoding="utf-8")
        for bits in GOERTZEL_COS:
            self.assertIn(f"0x{bits:08x}u", source)
        self.assertEqual(len(GOERTZEL_COS), 58)
        extractor = Extractor()
        timestamp = 1_000_000
        emitted = []
        for index in range(510):
            vector = extractor.push(timestamp, [f32(1.0 + 0.01 * (index % 5))] * 52)
            if vector is not None:
                emitted.append(vector)
            timestamp += 125_000
        self.assertGreaterEqual(len(emitted), 1)
        self.assertEqual(len(emitted[0]), FEATURES)
        self.assertTrue(all(value == value for value in emitted[0]))

    def test_c_python_feature_parity(self) -> None:
        cc = find_cc()
        if cc is None:
            self.fail("host C compiler not found")
        frames = []
        timestamp = 1_000_000
        for index in range(530):
            amplitudes = [f32((index * 17 + tone * 3) % 50) / 10.0 for tone in range(52)]
            frames.append((timestamp, amplitudes))
            timestamp += 125_000
        python_vectors = []
        extractor = Extractor()
        for timestamp, amplitudes in frames:
            vector = extractor.push(timestamp, amplitudes)
            if vector is not None:
                python_vectors.append(vector)
        self.assertGreaterEqual(len(python_vectors), 2)
        payload = struct.pack("<I", len(frames))
        for timestamp, amplitudes in frames:
            payload += struct.pack("<Q", timestamp)
            payload += struct.pack("<" + "f" * 52, *amplitudes)
        with tempfile.TemporaryDirectory() as directory:
            exe = Path(directory) / "features.exe"
            compile_host(cc, [MAIN / "tiny_ai_features.c"], exe, [MAIN], "TINY_AI_HOST_MAIN")
            result = subprocess.run([str(exe)], input=payload, capture_output=True, check=True)
        count = struct.unpack_from("<I", result.stdout)[0]
        self.assertEqual(count, len(python_vectors))
        c_vectors = []
        offset = 4
        for _ in range(count):
            c_vectors.append(list(struct.unpack_from("<" + "f" * FEATURES, result.stdout, offset)))
            offset += FEATURES * 4
        for python_row, c_row in zip(python_vectors, c_vectors):
            for python_value, c_value in zip(python_row, c_row):
                self.assertAlmostEqual(python_value, c_value, delta=1e-5)

    def test_tone_map_matches_c_and_rejects_short_csi(self) -> None:
        source = (MAIN / "tiny_ai_features.c").read_text(encoding="utf-8")
        match = re.search(r"k_tone\[TINY_AI_TONES\] = \{([^}]+)\}", source)
        self.assertIsNotNone(match)
        tones = [int(item.strip()) for item in match.group(1).split(",") if item.strip()]
        from tiny_ai_features import TONE_INDEX
        self.assertEqual(tones, list(TONE_INDEX))
        self.assertIsNone(amplitudes_from_csi(b"\x00" * 20))
        amps = amplitudes_from_csi(bytes((index * 13 + 7) & 0xFF for index in range(128)))
        self.assertEqual(len(amps), 52)
        self.assertTrue(all(value == value and value >= 0.0 for value in amps))



def _trap_stream(quiet: bool) -> list[tuple[int, list[float]]]:
    """100 s of ordinary empty-corridor noise. With quiet=True the 2 s window
    the extractor sees at its first emit (about 30 s) is almost flat, as after
    a power-on. The old one-window rule locked the noise reference there."""
    rng = __import__("random").Random(7)
    frames = []
    timestamp = 1_000_000
    for index in range(800):
        t_s = (timestamp - 1_000_000) / 1e6
        spread = 0.01 if quiet and 27.0 <= t_s <= 33.0 else 0.5
        frames.append((timestamp, [f32(10.0 + rng.gauss(0.0, spread)) for _ in range(52)]))
        timestamp += 125_000
    return frames


class NoiseReferenceTrapTests(unittest.TestCase):
    def _run(self, frames: list) -> tuple[Extractor, list]:
        extractor = Extractor()
        vectors = []
        for timestamp, amplitudes in frames:
            vector = extractor.push(timestamp, amplitudes)
            if vector is not None:
                vectors.append(vector)
        return extractor, vectors

    def test_quiet_first_window_does_not_lock_noise(self) -> None:
        normal, _ = self._run(_trap_stream(quiet=False))
        trapped, vectors = self._run(_trap_stream(quiet=True))
        # The quiet window really is a trap: far below the typical variance.
        self.assertLess(min(row[0] for row in trapped.noise_init[:4]), 0.05 * normal.noise[0])
        for band in range(8):
            ratio = trapped.noise[band] / normal.noise[band]
            self.assertGreater(ratio, 0.5, band)
            self.assertLess(ratio, 2.0, band)
        # var/noise of ordinary windows stays near 1 instead of ~1000x.
        tail = vectors[-10:]
        self.assertTrue(tail)
        for row in tail:
            self.assertLess(max(row[:8]), 4.0)

    def test_no_features_until_reference_ready_and_c_matches(self) -> None:
        cc = find_cc()
        if cc is None:
            self.fail("host C compiler not found")
        frames = _trap_stream(quiet=True)
        extractor = Extractor()
        first = None
        python_vectors = []
        for timestamp, amplitudes in frames:
            vector = extractor.push(timestamp, amplitudes)
            if vector is not None:
                python_vectors.append(vector)
                first = timestamp if first is None else first
        # 30 s long window + 31 one-second emits before the first feature.
        self.assertIsNotNone(first)
        self.assertGreaterEqual((first - frames[0][0]) / 1e6, 59.0)
        payload = struct.pack("<I", len(frames))
        for timestamp, amplitudes in frames:
            payload += struct.pack("<Q", timestamp)
            payload += struct.pack("<" + "f" * 52, *amplitudes)
        with tempfile.TemporaryDirectory() as directory:
            exe = Path(directory) / "features.exe"
            compile_host(cc, [MAIN / "tiny_ai_features.c"], exe, [MAIN], "TINY_AI_HOST_MAIN")
            result = subprocess.run([str(exe)], input=payload, capture_output=True, check=True)
        count = struct.unpack_from("<I", result.stdout)[0]
        self.assertEqual(count, len(python_vectors))
        offset = 4
        for python_row in python_vectors:
            c_row = struct.unpack_from("<" + "f" * FEATURES, result.stdout, offset)
            offset += FEATURES * 4
            for python_value, c_value in zip(python_row, c_row):
                self.assertAlmostEqual(python_value, c_value, delta=1e-4 * max(1.0, abs(python_value)))


class TinyAiGateTests(unittest.TestCase):
    def test_default_weights_bypass_mad_path(self) -> None:
        header = (MAIN / "tiny_ai_model_weights.h").read_text(encoding="utf-8")
        meta = json.loads((MAIN / "tiny_ai_model_weights.json").read_text(encoding="utf-8"))
        pipeline = (MAIN / "edge_result_v5_pipeline.c").read_text(encoding="utf-8")
        # The firmware header is either the NOT_READY default or an installed
        # model whose header, JSON and hash all agree.
        present = "#define TINY_AI_MODEL_PRESENT 1" in header
        self.assertEqual(present, bool(meta["present"]))
        if present:
            self.assertIn(f'#define TINY_AI_MODEL_HASH "{meta["sha256"]}"', header)
            self.assertIn(f'#define TINY_AI_MODEL_VERSION "{meta["version"]}"', header)
        else:
            self.assertIn("#define TINY_AI_MODEL_PRESENT 0", header)
        self.assertIn("tiny_ai_gate_allows_baseline", pipeline)
        self.assertIn("TINY_AI_SCORE_PENALTY * link->ai_confidence", pipeline)
        cc = find_cc()
        if cc is None:
            self.fail("host C compiler not found")
        with tempfile.TemporaryDirectory() as directory:
            folder = Path(directory)
            for name in ("tiny_ai_gatekeeper.c", "tiny_ai_gatekeeper.h", "tiny_ai_features.c", "tiny_ai_features.h"):
                shutil.copy(MAIN / name, folder / name)
            (folder / "tiny_ai_model_weights.h").write_text(NOT_READY_WEIGHTS, encoding="utf-8")
            exe = folder / "gate.exe"
            compile_host(
                cc,
                [folder / "tiny_ai_gatekeeper.c", folder / "tiny_ai_features.c"],
                exe,
                [folder],
                "TINY_AI_GATE_HOST",
            )
            result = subprocess.run([str(exe)], capture_output=True, text=True, check=True)
        self.assertIn("ready 0", result.stdout)
        self.assertIn("baseline 1", result.stdout)
        self.assertIn("boot 1", result.stdout)
        self.assertIn(f"sha {ABC_SHA}", result.stdout)

    def test_rung1_score_matches_c(self) -> None:
        firmware_before = (MAIN / "tiny_ai_model_weights.h").read_text(encoding="utf-8")
        cc = find_cc()
        if cc is None:
            self.fail("host C compiler not found")
        precision = [[1.0 if left == right else 0.0 for right in range(42)] for left in range(42)]
        model = {"rung": 1, "mu": [0.0] * 42, "precision": precision, "threshold": 50.0}
        row = [1.0] * 42
        python_score, python_like = score_rung1_f32(pack_blob(model), row)
        self.assertTrue(python_like)
        self.assertAlmostEqual(python_score, 50.0 / 92.0, delta=1e-5)
        with tempfile.TemporaryDirectory() as directory:
            folder = Path(directory)
            for name in ("tiny_ai_gatekeeper.c", "tiny_ai_gatekeeper.h", "tiny_ai_features.c", "tiny_ai_features.h"):
                shutil.copy(MAIN / name, folder / name)
            write_artifact(model, folder / "tiny_ai_model_weights.h", folder / "tiny_ai_model_weights.json",
                           {"selected": 1, "corridors": ["corridor-01"]})
            exe = folder / "gate.exe"
            compile_host(
                cc,
                [folder / "tiny_ai_gatekeeper.c", folder / "tiny_ai_features.c"],
                exe,
                [folder],
                "TINY_AI_GATE_HOST",
            )
            payload = struct.pack("<" + "f" * 42, *row)
            result = subprocess.run([str(exe)], input=payload, capture_output=True, check=False)
        stdout = result.stdout.decode("ascii", errors="replace")
        self.assertEqual(result.returncode, 0, result.stderr.decode("ascii", errors="replace") + stdout)
        score_line = next(line for line in stdout.splitlines() if line.startswith("score "))
        bits = int(score_line.split()[1], 16)
        like = int(score_line.split()[3])
        c_score = struct.unpack("<f", struct.pack("<I", bits))[0]
        self.assertAlmostEqual(c_score, python_score, delta=1e-5)
        self.assertEqual(like, 1)
        self.assertEqual((MAIN / "tiny_ai_model_weights.h").read_text(encoding="utf-8"), firmware_before)


class TinyAiLadderTests(unittest.TestCase):
    def test_selection_rule_and_roc(self) -> None:
        self.assertEqual(select_rung([[0.2, 0.2], [0.5, 0.4], [0.5, 0.4], [0.9, 0.9]]), 1)
        self.assertEqual(auroc([0, 0, 1, 1], [0.0, 0.2, 0.1, 1.0]), 0.75)
        self.assertEqual(tpr_at_fpr([0, 0, 1, 1], [0.0, 0.2, 0.1, 1.0], 0.01), 0.5)
        self.assertEqual(sequence_loss_ratio([1, 2, 5]), 0.5)
        self.assertFalse(disturbance_warning([0.0] * 42, "EMPTY"))
        noise_floor = [0.0] * 42
        noise_floor[32:40] = [26.0] * 8
        self.assertFalse(disturbance_warning(noise_floor, "EMPTY"))
        drift = [0.0] * 42
        drift[32:40] = [181.0] * 8
        self.assertFalse(disturbance_warning(drift, "EMPTY"))
        hot = [0.0] * 42
        hot[32:40] = [400.0] * 8
        self.assertTrue(disturbance_warning(hot, "EMPTY"))
        self.assertFalse(disturbance_warning(hot, "EMPTY", limit=500.0))
        self.assertFalse(disturbance_warning(hot, "OCCUPIED_VAL"))

    def test_mock_collection_and_synthetic_ladder(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            folder = Path(directory)
            summary = collect(mock_frames(1.2), {
                "corridor_id": "corridor-01",
                "session_label": "EMPTY",
                "session_env": "morning",
                "tx_h": 1.2,
                "rx_h": 1.2,
                "duration_min": 1.2,
                "link_id": "link-1",
            }, folder)
            self.assertGreater(summary["feature_count"], 0)
            self.assertEqual(summary["dropped"], 0)
            with gzip.open(summary["path"], "rt", encoding="utf-8") as handle:
                kinds = [json.loads(line)["type"] for line in handle]
            self.assertEqual(kinds[0], "session")
            self.assertIn("csi", kinds)
            self.assertIn("feature", kinds)
            self.assertEqual(kinds[-1], "quality")
        script = WIEVAC / "pi" / "training" / "train_tiny_ai.py"
        before = (MAIN / "tiny_ai_model_weights.h").read_text(encoding="utf-8")
        result = subprocess.run([sys.executable, str(script), "--synthetic-benchmark"],
                                capture_output=True, text=True, check=False)
        self.assertEqual(result.returncode, 0, result.stderr + result.stdout)
        self.assertIn("selected ", result.stdout)
        self.assertIn("export skipped", result.stdout)
        self.assertEqual(before, (MAIN / "tiny_ai_model_weights.h").read_text(encoding="utf-8"))


class FakeClock(collector.Clock):
    def __init__(self, epoch: float) -> None:
        self.t = float(epoch)
        self.mono = 0.0

    def time(self) -> float:
        return self.t

    def monotonic(self) -> float:
        return self.mono

    def monotonic_ns(self) -> int:
        return int(self.mono * 1_000_000_000)

    def sleep(self, seconds: float) -> None:
        self.t += float(seconds)
        self.mono += float(seconds)


def _dense_frames(count: int, step: int = 50_000) -> list[tuple[int, int]]:
    return [(index * step, index + 1) for index in range(count)]


def _write_dataset(folder: Path, corridor: str, role: str, session_label: str, vectors: list[dict],
                   quality: dict | None = None, filename: str | None = None, include_role: bool = True) -> Path:
    path = folder / collector.corridor_dirname(corridor)
    path.mkdir(parents=True, exist_ok=True)
    target = path / (filename or f"{session_label.lower()}.jsonl.gz")
    session = {"type": "session", "corridor_id": corridor, "session_label": session_label}
    if include_role:
        session["role"] = role
    with gzip.open(target, "wt", encoding="utf-8") as handle:
        handle.write(json.dumps(session) + "\n")
        for index, vector in enumerate(vectors):
            handle.write(json.dumps({
                "type": "feature", "complete": True, "seq": index + 1, "ts_us": index,
                "features": vector["features"], **{key: vector[key] for key in vector if key != "features"},
            }) + "\n")
        handle.write(json.dumps({"type": "quality", **(quality or {"loss_ratio": 0.0, "frames_per_s": 80.0})}) + "\n")
    return target


def _clean_vector(index: int = 0, **extra) -> dict:
    row = {
        "features": [0.0] * 42, "label": "EMPTY", "t_since_start_s": 200.0 + index,
        "short_coverage": 1.0, "loss_30s": 0.0, "finite": True, "disturbance_warning": False,
    }
    row.update(extra)
    return row


class RawCsiCollectorTests(unittest.TestCase):
    def test_wraw_round_trip_and_rejections(self) -> None:
        csi = bytes((index * 13 + 7) & 0xFF for index in range(128))
        other = bytes((index * 3) & 0xFF for index in range(128))
        packet = encode_wraw(1, 1, 9, 3, [
            {"tx_sequence": 10, "timestamp_us": 1000, "rssi": -40, "csi": csi},
            {"tx_sequence": 11, "timestamp_us": 2000, "rssi": -41, "csi": other},
        ])
        parsed = parse_wraw(packet)
        self.assertIsNotNone(parsed)
        assert parsed is not None
        self.assertEqual(parsed["frame_count"], 2)
        self.assertEqual(parsed["frames"][0]["tx_sequence"], 10)
        self.assertEqual(amplitudes_from_csi(parsed["frames"][0]["csi"]), amplitudes_from_csi(csi))
        broken = bytearray(packet)
        broken[30] ^= 0xFF
        self.assertIsNone(parse_wraw(bytes(broken)))
        self.assertIsNone(parse_wraw(packet[:-5]))
        stats: dict = {}
        self.assertIsNone(take_datagram(encode_wraw(1, 2, 9, 1, [
            {"tx_sequence": 1, "timestamp_us": 1, "rssi": -40, "csi": csi},
        ]), 1, stats))
        self.assertEqual(stats["link_mismatch"], 1)

    def test_wraw_v2_gain_scale_and_v1_compat(self) -> None:
        csi = bytes((index * 13 + 7) & 0xFF for index in range(128))
        v2 = parse_wraw(encode_wraw(1, 1, 9, 3, [
            {"tx_sequence": 10, "timestamp_us": 1000, "rssi": -40, "csi": csi, "gain_scale": 1.75},
        ]))
        assert v2 is not None
        self.assertAlmostEqual(v2["frames"][0]["gain_scale"], 1.75)
        v1 = parse_wraw(encode_wraw(1, 1, 9, 3, [
            {"tx_sequence": 10, "timestamp_us": 1000, "rssi": -40, "csi": csi},
        ], version=1))
        assert v1 is not None
        self.assertEqual(v1["frames"][0]["gain_scale"], 1.0)
        self.assertIsNone(parse_wraw(encode_wraw(1, 1, 9, 3, [
            {"tx_sequence": 10, "timestamp_us": 1000, "rssi": -40, "csi": csi, "gain_scale": 0.0},
        ])))
        raw = amplitudes_from_csi(csi)
        scaled = collector.scaled_amplitudes(csi, 1.75)
        self.assertEqual(scaled, [f32(value * f32(1.75)) for value in raw])
        self.assertEqual(collector.scaled_amplitudes(csi, 1.0), raw)

    def test_wraw_v3_round_trip_and_collector_gain(self) -> None:
        csi = bytes((index * 13 + 7) & 0xFF for index in range(128))
        packet = encode_wraw(1, 1, 9, 4, [
            {"tx_sequence": 10, "timestamp_us": 1000, "rssi": -40, "csi": csi,
             "applied_scale": 1.1, "library_gain": 0.84},
        ], version=3, gain_response_s=-0.26)
        parsed = parse_wraw(packet)
        assert parsed is not None
        self.assertAlmostEqual(parsed["gain_response_s"], -0.26)
        frame = parsed["frames"][0]
        self.assertAlmostEqual(frame["applied_scale"], 1.1)
        self.assertAlmostEqual(frame["library_gain"], 0.84)
        raw = amplitudes_from_csi(csi)
        self.assertEqual(collector.scaled_amplitudes(csi, frame["applied_scale"]),
                         [f32(value * f32(1.1)) for value in raw])
        broken = bytearray(packet)
        broken[-2] ^= 0xFF
        self.assertIsNone(parse_wraw(bytes(broken)))
        self.assertIsNone(parse_wraw(packet[:-6]))
        legacy = parse_wraw(encode_wraw(1, 1, 9, 3, [
            {"tx_sequence": 10, "timestamp_us": 1000, "rssi": -40, "csi": csi, "gain_scale": 1.75},
        ]))
        assert legacy is not None
        self.assertIsNone(legacy["gain_response_s"])
        self.assertIsNone(legacy["frames"][0]["library_gain"])
        self.assertAlmostEqual(legacy["frames"][0]["applied_scale"], 1.75)
        v1 = parse_wraw(encode_wraw(1, 1, 9, 3, [
            {"tx_sequence": 10, "timestamp_us": 1000, "rssi": -40, "csi": csi},
        ], version=1))
        assert v1 is not None
        self.assertEqual(v1["frames"][0]["applied_scale"], 1.0)
        self.assertIsNone(v1["frames"][0]["library_gain"])
        self.assertIsNone(v1["gain_response_s"])
        frames = [
            (*item, {"applied_scale": 1.25, "library_gain": 0.9, "gain_response_s": -0.26})
            for item in mock_frames(1.2)
        ]
        summary = collect(frames, {
            "corridor_id": "corridor-01", "session_label": "EMPTY", "session_env": "morning",
            "tx_h": 1.2, "rx_h": 1.2, "duration_min": 1.2, "link_id": "link-1",
        }, Path(tempfile.mkdtemp()))
        with gzip.open(summary["path"], "rt", encoding="utf-8") as handle:
            rows = [json.loads(line) for line in handle if line.strip()]
        session = next(row for row in rows if row["type"] == "session")
        csi_row = next(row for row in rows if row["type"] == "csi")
        quality = next(row for row in rows if row["type"] == "quality")
        self.assertAlmostEqual(session["gain_response_s"], -0.26)
        self.assertAlmostEqual(quality["gain_response_s"], -0.26)
        self.assertAlmostEqual(csi_row["applied_scale"], 1.25)
        self.assertAlmostEqual(csi_row["library_gain"], 0.9)
        self.assertAlmostEqual(csi_row["gain_response_s"], -0.26)

    def test_quality_fields_move_when_a_datagram_is_lost(self) -> None:
        full = _dense_frames(800)
        end = full[-1][0]
        self.assertEqual(short_coverage(full, end), 1.0)
        self.assertEqual(loss_30s(full, end), 0.0)
        partial = [item for item in full if item[1] not in {790, 791, 792, 793}]
        self.assertLess(short_coverage(partial, end), 1.0)
        self.assertGreater(loss_30s(partial, end), 0.0)
        summary = collect(mock_frames(1.2), {
            "corridor_id": "corridor-01", "session_label": "EMPTY", "session_env": "morning",
            "tx_h": 1.2, "rx_h": 1.2, "duration_min": 1.2, "link_id": "link-1",
        }, Path(tempfile.mkdtemp()))
        with gzip.open(summary["path"], "rt", encoding="utf-8") as handle:
            feature = next(json.loads(line) for line in handle if '"type": "feature"' in line)
        for key in ("t_since_start_s", "short_coverage", "loss_30s", "finite", "label"):
            self.assertIn(key, feature)
        self.assertTrue(feature["finite"])
        self.assertEqual(feature["label"], "EMPTY")

    def test_label_windows_and_source_conflict(self) -> None:
        empty = [{"type": "label_event", "pi_mono_us": 0, "state": "EMPTY", "source": "keyboard"}]
        self.assertEqual(feature_label(30_000_000, empty, "MIXED"), "EMPTY")
        self.assertEqual(feature_label(29_999_999, empty, "MIXED"), "EXCLUDED")
        mixed = [
            {"type": "label_event", "pi_mono_us": 10_000_000, "state": "PERSON", "source": "keyboard"},
            {"type": "label_event", "pi_mono_us": 12_000_000, "state": "EMPTY", "source": "keyboard"},
        ]
        self.assertEqual(feature_label(12_000_000, mixed, "MIXED"), "OCCUPIED")
        self.assertEqual(feature_label(14_000_000, mixed, "MIXED"), "EXCLUDED")
        self.assertEqual(feature_label(42_000_000, mixed, "MIXED"), "EMPTY")
        self.assertEqual(feature_label(1_000, [], "MIXED"), "EXCLUDED")
        conflict = empty + [{
            "type": "label_event", "pi_mono_us": 10_000_000, "t_end_us": 15_000_000,
            "state": "PERSON", "source": "dashboard",
        }]
        self.assertEqual(feature_label(12_000_000, conflict, "MIXED"), "EXCLUDED")
        dashboard_only = [{
            "type": "label_event", "pi_mono_us": 10_000_000, "t_end_us": 15_000_000,
            "state": "PERSON", "source": "dashboard",
        }]
        self.assertEqual(feature_label(12_000_000, dashboard_only, "MIXED"), "OCCUPIED")

    def test_role_defaults_and_rejections(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            folder = Path(directory)
            code = collector.main([
                "--corridor-id", "corridor-01", "--session-env", "morning",
                "--tx-h", "1", "--rx-h", "1", "--duration-min", "0.05", "--mock",
                "--out-root", str(folder),
            ])
            self.assertEqual(code, 0)
            path = next(folder.rglob("*.jsonl.gz"))
            with gzip.open(path, "rt", encoding="utf-8") as handle:
                session = json.loads(handle.readline())
            self.assertEqual(session["role"], "train")
            self.assertEqual(session["session_label"], "EMPTY")
            rejected = collector.main([
                "--corridor-id", "corridor-01", "--session-env", "morning",
                "--tx-h", "1", "--rx-h", "1", "--mock", "--role", "train",
                "--session-label", "MIXED", "--out-root", str(folder / "nope"),
            ])
            self.assertEqual(rejected, 2)
            self.assertFalse((folder / "nope").exists())
            missing = collector.main([
                "--corridor-id", "corridor-01", "--session-env", "morning",
                "--tx-h", "1", "--rx-h", "1", "--mock", "--role", "test",
                "--out-root", str(folder / "nope2"),
            ])
            self.assertEqual(missing, 2)

    def test_start_at_repeat_and_dashboard_schedule(self) -> None:
        zone = datetime.now().astimezone().tzinfo
        epoch = datetime(2026, 10, 17, 10, 0, tzinfo=zone).timestamp()
        self.assertAlmostEqual(parse_start_at("10:05", epoch), epoch + 300, delta=1.5)
        self.assertEqual(schedule_slices(0, 45 * 60, 0), [(0, 1800), (1800, 2700)])
        with tempfile.TemporaryDirectory() as directory:
            folder = Path(directory)
            clock = FakeClock(epoch)
            code = collector.main([
                "--corridor-id", "corridor-01", "--session-env", "night",
                "--tx-h", "1", "--rx-h", "1", "--duration-min", "0.05", "--mock",
                "--start-at", "10:05", "--repeat", "2", "--gap-min", "1",
                "--out-root", str(folder / "timed"),
            ], clock=clock)
            self.assertEqual(code, 0)
            self.assertEqual(len(list((folder / "timed").rglob("*.jsonl.gz"))), 2)
            self.assertGreaterEqual(clock.t, epoch + 360 - 1)
            schedule = folder / "capture-schedules.json"
            t0 = 1_700_000_000.0
            schedule.write_text(json.dumps([
                {"id": "done", "start_s": t0, "end_s": t0 + 50, "occupancy": "EMPTY", "completed": True},
                {"id": "person", "start_s": t0 + 100, "end_s": t0 + 200, "occupancy": "HUMAN_PRESENT"},
                {"id": "short", "start_s": t0 + 1000, "end_s": t0 + 1020, "occupancy": "EMPTY"},
                {"id": "long", "start_s": t0 + 2000, "end_s": t0 + 2070, "occupancy": "EMPTY"},
            ]), encoding="utf-8")
            before = schedule.read_bytes()
            previous = collector.MAX_SLICE_S
            collector.MAX_SLICE_S = 30
            try:
                text = io.StringIO()
                with redirect_stdout(text):
                    code = collector.main([
                        "--corridor-id", "corridor-01", "--session-env", "night",
                        "--tx-h", "1", "--rx-h", "1", "--mock",
                        "--follow-dashboard-schedule", "--out-root", str(folder / "sched"),
                    ], clock=FakeClock(t0), schedule_path=schedule, follow_until=t0 + 2071)
            finally:
                collector.MAX_SLICE_S = previous
            self.assertEqual(code, 0, text.getvalue())
            self.assertEqual(schedule.read_bytes(), before)
            self.assertIn("skipped occupancy=HUMAN_PRESENT", text.getvalue())
            files = list((folder / "sched").rglob("*.jsonl.gz"))
            self.assertEqual(len(files), 4)
            windows = []
            for path in files:
                with gzip.open(path, "rt", encoding="utf-8") as handle:
                    session = json.loads(handle.readline())
                windows.append((session["schedule_id"], session["window_end_s"] - session["window_start_s"]))
            self.assertIn(("short", 20), [(item[0], round(item[1])) for item in windows])
            long = sorted(round(item[1]) for item in windows if item[0] == "long")
            self.assertEqual(long, [10, 30, 30])

    def test_background_stdin_does_not_spin_or_crash(self) -> None:
        class DeadStdin:
            closed = False

            def isatty(self) -> bool:
                return True

            def readline(self) -> str:
                raise OSError(9, "Bad file descriptor")

        dead = DeadStdin()
        stdin_on = [True]
        with mock.patch.object(collector.sys, "stdin", dead):
            self.assertIsNone(_read_key([dead], stdin_on))
        self.assertFalse(stdin_on[0])
        class SilentStdin:
            closed = False

            def isatty(self) -> bool:
                return False

        sock = collector.socket.socket(collector.socket.AF_INET, collector.socket.SOCK_DGRAM)
        try:
            with mock.patch.object(collector.sys, "stdin", SilentStdin()):
                self.assertEqual(_udp_watch(sock, True), [sock])
        finally:
            sock.close()
        clock = FakeClock(0.0)
        calls = []

        def fake_select(watch, _write, _error, timeout):
            calls.append((list(watch), timeout))
            if len(calls) > 30 or not timeout:
                raise AssertionError(f"udp loop spun: {calls[-1]}")
            clock.sleep(timeout)
            if len(calls) == 1:
                return [collector.sys.stdin], [], []
            return [], [], []

        with tempfile.TemporaryDirectory() as directory:
            args = argparse.Namespace(
                bind="127.0.0.1", udp_port=0, labels_from_dashboard=False, raw_out=None,
                out_root=Path(directory),
            )
            meta = {
                "corridor_id": "corridor-01", "session_label": "MIXED", "session_env": "t",
                "tx_h": 1.0, "rx_h": 1.0, "duration_min": 0.01, "link_id": "link-1", "role": "test",
            }
            err = io.StringIO()
            with mock.patch.object(collector.sys, "stdin", dead), redirect_stderr(err):
                result = run_udp_session(args, meta, clock, 0.5, None, None, select_fn=fake_select)
            self.assertIn("path", result)
            self.assertIn("stdin unusable", err.getvalue())
            self.assertGreaterEqual(len(calls), 2)
            self.assertNotIn(dead, calls[-1][0])
            self.assertGreater(min(timeout for _watch, timeout in calls), 0.0)

            class SilentStdin:
                closed = False

                def isatty(self) -> bool:
                    return False

                def readline(self) -> str:
                    raise AssertionError("readline on a non-tty")

            def quiet_select(watch, _write, _error, timeout):
                if collector.sys.stdin in watch or not timeout:
                    raise AssertionError("non-tty stdin woke the loop")
                clock.sleep(timeout)
                return [], [], []

            err_quiet = io.StringIO()
            with mock.patch.object(collector.sys, "stdin", SilentStdin()), redirect_stderr(err_quiet):
                run_udp_session(args, meta, clock, 0.4, None, None, select_fn=quiet_select)
            self.assertIn("no label source", err_quiet.getvalue())


class TinyAiPreprocessTests(unittest.TestCase):
    def _corpus(self, folder: Path) -> dict:
        return load_datasets(folder)

    def test_each_filter_step_counts_drops(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            folder = Path(directory)
            rows = [_clean_vector(index, t_since_start_s=10.0) for index in range(3)]
            rows += [_clean_vector(index + 3) for index in range(2)]
            rows.append(_clean_vector(9, features=[math.nan] * 42))
            rows.append(_clean_vector(10, finite=False))
            rows.append(_clean_vector(11, short_coverage=0.5))
            rows.append(_clean_vector(12, loss_30s=0.2))
            _write_dataset(folder, "corridor-A", "train", "EMPTY", rows)
            hot = [0.0] * 32 + [500.0] * 8 + [0.0] * 2
            warned = [_clean_vector(index, features=hot if index == 2 else [0.0] * 42)
                      for index in range(20)]
            _write_dataset(folder, "corridor-W", "train", "EMPTY", warned)
            # Mostly broken: 1 of 4 post-warmup vectors passes -> whole session dropped.
            session_drop = [_clean_vector(0)] + [_clean_vector(index, loss_30s=0.2) for index in range(1, 4)]
            _write_dataset(folder, "corridor-P", "train", "EMPTY", session_drop,
                           quality={"loss_ratio": 0.2, "frames_per_s": 80.0})
            # Bursty but mostly clean: high session-average loss must not throw
            # away the clean vectors; only the burst vectors go.
            bursty = [_clean_vector(index) for index in range(8)]
            bursty += [_clean_vector(index + 8, loss_30s=0.3) for index in range(2)]
            _write_dataset(folder, "corridor-U", "train", "EMPTY", bursty,
                           quality={"loss_ratio": 0.15, "frames_per_s": 90.0})
            slow = [_clean_vector(index) for index in range(4)]
            _write_dataset(folder, "corridor-S", "train", "EMPTY", slow,
                           quality={"loss_ratio": 0.0, "frames_per_s": 10.0})
            heavy = [_clean_vector(index, features=hot) for index in range(5)]
            _write_dataset(folder, "corridor-H", "train", "EMPTY", heavy)
            many = [_clean_vector(index, features=[float(index)] + [0.0] * 41) for index in range(10)]
            few = [_clean_vector(index, features=[float(index)] + [0.0] * 41) for index in range(4)]
            _write_dataset(folder, "corridor-M", "train", "EMPTY", many)
            _write_dataset(folder, "corridor-F", "train", "EMPTY", few)
            old_vectors = [{"features": [1.0] * 42} for _ in range(95)]
            _write_dataset(folder, "corridor-O", "train", "EMPTY", old_vectors, include_role=False)
            raw = load_datasets(folder)
            self.assertEqual(raw["corridor-O"]["role"], "train")
            corpus, report = preprocess_corpus(raw, balance=True)
            drops = report["drops"]
            self.assertGreaterEqual(drops["nonfinite"], 2)
            self.assertGreaterEqual(drops["warmup"], 3 + 90)
            self.assertGreaterEqual(drops["coverage"], 2)
            self.assertGreaterEqual(drops["poor_session"], 1 + 4)
            self.assertFalse(any("corridor_U" in path for path in report["poor_sessions"]))
            self.assertTrue(any("corridor_P" in path for path in report["poor_sessions"]))
            self.assertGreaterEqual(drops["disturbance"], 5)
            kept_warn = len(corpus["corridor-W"]["empty"])
            self.assertEqual(kept_warn, 2)
            self.assertEqual(len(corpus["corridor-M"]["empty"]), len(corpus["corridor-F"]["empty"]))
            again, _report2 = preprocess_corpus(load_datasets(folder), balance=True)
            self.assertEqual(corpus["corridor-M"]["empty"], again["corridor-M"]["empty"])
            self.assertLess(corpus["corridor-A"]["clean_minutes"], 20)
            self.assertTrue(report["warnings"])

    def test_train_occupied_and_mixed_role_stop(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            folder = Path(directory)
            _write_dataset(folder, "corridor-A", "train", "EMPTY", [_clean_vector(label="OCCUPIED")])
            with self.assertRaises(ValueError):
                load_datasets(folder)
        with tempfile.TemporaryDirectory() as directory:
            folder = Path(directory)
            _write_dataset(folder, "corridor-A", "train", "EMPTY", [_clean_vector()])
            _write_dataset(folder, "corridor-A", "test", "MIXED", [_clean_vector(label="OCCUPIED")], filename="mixed.jsonl.gz")
            with self.assertRaises(ValueError):
                load_datasets(folder)

    def test_threshold_adjust_and_deploy_rule(self) -> None:
        self.assertEqual(choose_deploy_rung(
            [[0.10, 0.10], [0.20, 0.20], [0.30, 0.12], [0.40, 0.40]],
            [[0.01, 0.01], [0.02, 0.02], [0.01, 0.01], [0.20, 0.01]],
        ), 1)
        self.assertEqual(choose_deploy_rung(
            [[0.10], [0.90], [0.90], [0.90]],
            [[0.0], [0.9], [0.9], [0.9]],
        ), 0)
        # Baseline at the ceiling: simplest non-inferior rung wins (rung 1 too weak).
        self.assertEqual(choose_deploy_rung(
            [[0.997], [0.959], [0.997], [0.891]],
            [[0.012], [0.048], [0.019], [0.013]],
        ), 2)
        # Simplest qualifying rung is chosen even if a later one is higher.
        self.assertEqual(choose_deploy_rung(
            [[0.50], [0.495], [0.80], [0.90]],
            [[0.01], [0.01], [0.01], [0.01]],
        ), 1)
        self.assertEqual(choose_deploy_rung(
            [[0.50], [0.40], [0.40], [0.495]],
            [[0.01], [0.01], [0.01], [0.04]],
        ), 3)
        models = calibrate_rungs({
            "a": [[0.0] * 42 for _ in range(8)],
            "b": [[1.0] * 42 for _ in range(8)],
        })
        self.assertGreater(models[0]["adjust"], 1.0)
        self.assertGreater(models[0]["threshold"], models[0]["q_in_final"])

    def test_gmm_sign_and_host_parity(self) -> None:
        firmware_before = (MAIN / "tiny_ai_model_weights.h").read_text(encoding="utf-8")
        rng = __import__("random").Random(1)
        rows = [[rng.gauss(0.0, 1.0) for _ in range(42)] for _ in range(80)]
        model = fit_rung(2, rows)
        self.assertIsNotNone(model)
        assert model is not None
        blob = pack_blob(model)
        likes = [score_rung2_f32(blob, row)[1] for row in rows]
        self.assertGreaterEqual(sum(likes) / len(likes), 0.95)
        cc = find_cc()
        if cc is None:
            self.fail("host C compiler not found")
        for rung in (2, 3):
            fitted = fit_rung(rung, rows)
            self.assertIsNotNone(fitted)
            assert fitted is not None
            row = rows[0]
            scorer = score_rung2_f32 if rung == 2 else score_rung3_f32
            python_score, python_like = scorer(pack_blob(fitted), row)
            with tempfile.TemporaryDirectory() as directory:
                host = Path(directory)
                for name in ("tiny_ai_gatekeeper.c", "tiny_ai_gatekeeper.h", "tiny_ai_features.c", "tiny_ai_features.h"):
                    shutil.copy(MAIN / name, host / name)
                write_artifact(fitted, host / "tiny_ai_model_weights.h", host / "tiny_ai_model_weights.json",
                               {"selected": rung, "corridors": ["a"]})
                exe = host / "gate.exe"
                compile_host(cc, [host / "tiny_ai_gatekeeper.c", host / "tiny_ai_features.c"], exe, [host], "TINY_AI_GATE_HOST")
                payload = struct.pack("<" + "f" * 42, *row)
                result = subprocess.run([str(exe)], input=payload, capture_output=True, check=False)
            stdout = result.stdout.decode("ascii", errors="replace")
            self.assertEqual(result.returncode, 0, result.stderr.decode("ascii", errors="replace") + stdout)
            score_line = next(line for line in stdout.splitlines() if line.startswith("score "))
            bits = int(score_line.split()[1], 16)
            like = int(score_line.split()[3])
            c_score = struct.unpack("<f", struct.pack("<I", bits))[0]
            self.assertAlmostEqual(c_score, python_score, delta=1e-4)
            self.assertEqual(like, int(python_like))
        self.assertEqual((MAIN / "tiny_ai_model_weights.h").read_text(encoding="utf-8"), firmware_before)

    def test_install_checks_hash_and_default_does_not_touch_firmware(self) -> None:
        before = (MAIN / "tiny_ai_model_weights.h").read_text(encoding="utf-8")
        meta_before = (MAIN / "tiny_ai_model_weights.json").read_text(encoding="utf-8")
        with tempfile.TemporaryDirectory() as directory:
            folder = Path(directory)
            model = fit_rung(1, [[float(index == tone) for tone in range(42)] for index in range(8)])
            assert model is not None
            model["adjust"] = 1.0
            candidate = folder / "candidate"
            candidate.mkdir()
            write_artifact(model, candidate / "tiny_ai_model_weights.h", candidate / "tiny_ai_model_weights.json",
                           {"selected": 1, "corridors": ["a"], "train_corridors": ["a"], "test_corridors": []})
            text = (candidate / "tiny_ai_model_weights.h").read_text(encoding="utf-8")
            (candidate / "tiny_ai_model_weights.h").write_text(
                text.replace('#define TINY_AI_MODEL_HASH "', '#define TINY_AI_MODEL_HASH "ff', 1),
                encoding="utf-8",
            )
            import train_tiny_ai as trainer
            trainer.HEADER_PATH = folder / "out.h"
            trainer.META_PATH = folder / "out.json"
            try:
                self.assertEqual(install_candidate(candidate), 2)
                self.assertFalse((folder / "out.h").exists())
                (candidate / "tiny_ai_model_weights.h").write_text(text, encoding="utf-8")
                self.assertEqual(install_candidate(candidate), 0)
                self.assertTrue((folder / "out.h").is_file())
            finally:
                trainer.HEADER_PATH = MAIN / "tiny_ai_model_weights.h"
                trainer.META_PATH = MAIN / "tiny_ai_model_weights.json"
        self.assertEqual(before, (MAIN / "tiny_ai_model_weights.h").read_text(encoding="utf-8"))
        self.assertEqual(meta_before, (MAIN / "tiny_ai_model_weights.json").read_text(encoding="utf-8"))


class DashboardLabelDedupeTests(unittest.TestCase):
    """The dashboard writes every interval twice (open row, then closed row).
    The stale open row must not make later intervals look contradictory."""

    @staticmethod
    def _rows() -> list[dict]:
        def row(start, end, state):
            return {"type": "label_event", "pi_mono_us": start, "t_end_us": end,
                    "state": state, "source": "dashboard"}
        return [
            row(0, None, "EMPTY"), row(0, 100_000_000, "EMPTY"),
            row(100_000_000, None, "PERSON"), row(100_000_000, 160_000_000, "PERSON"),
            row(160_000_000, None, "EMPTY"),
        ]

    def test_duplicate_open_rows_do_not_conflict(self) -> None:
        rows = self._rows()
        self.assertEqual(feature_label(90_000_000, rows, "MIXED"), "EMPTY")
        self.assertEqual(feature_label(130_000_000, rows, "MIXED"), "OCCUPIED")
        self.assertEqual(feature_label(161_000_000, rows, "MIXED"), "OCCUPIED")
        self.assertEqual(feature_label(175_000_000, rows, "MIXED"), "EXCLUDED")
        self.assertEqual(feature_label(195_000_000, rows, "MIXED"), "EMPTY")
        merged = collector.dedupe_dashboard_events(rows)
        self.assertEqual([event["pi_mono_us"] for event in merged], [0, 100_000_000, 160_000_000])
        self.assertEqual(merged[0]["t_end_us"], 100_000_000)
        self.assertIsNone(merged[-1]["t_end_us"])

    def test_relabel_file_writes_new_labels(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            folder = Path(directory)
            src = folder / "in" / "mixed.jsonl.gz"
            src.parent.mkdir()
            with gzip.open(src, "wt", encoding="utf-8") as handle:
                handle.write(json.dumps({"type": "session", "corridor_id": "c", "session_label": "MIXED",
                                         "role": "test"}) + "\n")
                for seq, stamp in ((1, 90_000_000), (2, 130_000_000), (3, 195_000_000)):
                    handle.write(json.dumps({"type": "csi", "seq": seq, "ts_us": seq, "pi_mono_us": stamp,
                                             "amp": [1.0] * 52}) + "\n")
                    handle.write(json.dumps({"type": "feature", "seq": seq, "ts_us": seq,
                                             "features": [0.0] * 42, "complete": True,
                                             "label": "EXCLUDED"}) + "\n")
                for event in self._rows():
                    handle.write(json.dumps(event) + "\n")
                handle.write(json.dumps({"type": "quality", "loss_ratio": 0.0}) + "\n")
            out_dir = folder / "out"
            self.assertEqual(collector.main(["--relabel", str(src), "--relabel-out-dir", str(out_dir)]), 0)
            with gzip.open(out_dir / "mixed.jsonl.gz", "rt", encoding="utf-8") as handle:
                records = [json.loads(line) for line in handle if line.strip()]
            labels = [record["label"] for record in records if record["type"] == "feature"]
            self.assertEqual(labels, ["EMPTY", "OCCUPIED", "EMPTY"])
            quality = records[-1]
            self.assertEqual((quality["empty_vectors"], quality["occupied_vectors"]), (2, 1))
            self.assertEqual(sum(1 for record in records if record["type"] == "label_event"), 3)
            self.assertNotEqual(collector.main(["--relabel", str(src), "--relabel-out-dir", str(src.parent)]), 0)


if __name__ == "__main__":
    unittest.main()
