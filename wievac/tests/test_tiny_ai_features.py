"""Parity and gate checks for the empty-corridor novelty scaffold."""

from __future__ import annotations

import gzip
import json
import os
import re
import shutil
import struct
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

WIEVAC = Path(__file__).resolve().parents[1]
MAIN = WIEVAC / "firmware" / "receiver" / "main"
sys.path.insert(0, str(WIEVAC / "pi" / "app"))
sys.path.insert(0, str(WIEVAC / "pi" / "training"))
sys.path.insert(0, str(WIEVAC / "scripts"))

from collect_empty_corridor_dataset import collect, disturbance_warning, mock_frames, sequence_loss_ratio  # noqa: E402
from evaluate_loco import auroc, pack_blob, score_rung1_f32, select_rung, tpr_at_fpr  # noqa: E402
from tiny_ai_features import FEATURES, GOERTZEL_COS, Extractor, amplitudes_from_csi, f32  # noqa: E402
from train_tiny_ai import write_artifact  # noqa: E402

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
        for index in range(256):
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
        for index in range(280):
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


class TinyAiGateTests(unittest.TestCase):
    def test_default_weights_bypass_mad_path(self) -> None:
        header = (MAIN / "tiny_ai_model_weights.h").read_text(encoding="utf-8")
        meta = json.loads((MAIN / "tiny_ai_model_weights.json").read_text(encoding="utf-8"))
        pipeline = (MAIN / "edge_result_v5_pipeline.c").read_text(encoding="utf-8")
        self.assertIn("#define TINY_AI_MODEL_PRESENT 0", header)
        self.assertFalse(meta["present"])
        self.assertIn("tiny_ai_gate_allows_baseline", pipeline)
        self.assertIn("local_passability_score - TINY_AI_SCORE_PENALTY", pipeline)
        cc = find_cc()
        if cc is None:
            self.fail("host C compiler not found")
        with tempfile.TemporaryDirectory() as directory:
            exe = Path(directory) / "gate.exe"
            compile_host(
                cc,
                [MAIN / "tiny_ai_gatekeeper.c", MAIN / "tiny_ai_features.c"],
                exe,
                [MAIN],
                "TINY_AI_GATE_HOST",
            )
            result = subprocess.run([str(exe)], capture_output=True, text=True, check=True)
        self.assertIn("ready 0", result.stdout)
        self.assertIn("baseline 1", result.stdout)
        self.assertIn("boot 1", result.stdout)
        self.assertIn(f"sha {ABC_SHA}", result.stdout)

    def test_rung1_score_matches_c(self) -> None:
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
        self.assertIn("#define TINY_AI_MODEL_PRESENT 0", (MAIN / "tiny_ai_model_weights.h").read_text(encoding="utf-8"))


class TinyAiLadderTests(unittest.TestCase):
    def test_selection_rule_and_roc(self) -> None:
        self.assertEqual(select_rung([[0.2, 0.2], [0.5, 0.4], [0.5, 0.4], [0.9, 0.9]]), 1)
        self.assertEqual(auroc([0, 0, 1, 1], [0.0, 0.2, 0.1, 1.0]), 0.75)
        self.assertEqual(tpr_at_fpr([0, 0, 1, 1], [0.0, 0.2, 0.1, 1.0], 0.01), 0.5)
        self.assertEqual(sequence_loss_ratio([1, 2, 5]), 0.5)
        self.assertFalse(disturbance_warning([0.0] * 42, "EMPTY"))
        hot = [0.0] * 42
        hot[32:40] = [5.0] * 8
        self.assertTrue(disturbance_warning(hot, "EMPTY"))
        self.assertFalse(disturbance_warning(hot, "OCCUPIED_VAL"))

    def test_mock_collection_and_synthetic_ladder(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            folder = Path(directory)
            summary = collect(mock_frames(0.6), {
                "corridor_id": "corridor-01",
                "session_label": "EMPTY",
                "session_env": "morning",
                "tx_h": 1.2,
                "rx_h": 1.2,
                "duration_min": 0.6,
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


if __name__ == "__main__":
    unittest.main()
