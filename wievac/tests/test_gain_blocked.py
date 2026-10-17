"""Gain-response slope and knife-edge gate on published BLOCKED."""

from __future__ import annotations

import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path

WIEVAC = Path(__file__).resolve().parents[1]
MAIN = WIEVAC / "firmware" / "receiver" / "main"
TRUTHS = (0.0, 0.26, -0.26, 0.5, 1.0, -1.0)


def find_cc() -> str:
    for name in ("gcc", "clang", "cc"):
        found = shutil.which(name)
        if found:
            return found
    bundled = Path(r"C:\D\devc\Dev-Cpp\MinGW64\bin\gcc.exe")
    if bundled.exists():
        return str(bundled)
    raise AssertionError("host C compiler not found")


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


def formula_only(sources: list[Path], out: Path) -> tuple[list[Path], list[str]]:
    """These tests cover the formula alone. With a trained model installed the
    gate (correctly) withholds learning until it has attested 30 s of empty
    corridor, so the gatekeeper is rebuilt against NOT_READY weights."""
    gate = MAIN / "tiny_ai_gatekeeper.c"
    if gate not in sources:
        return sources, []
    folder = out.parent / "gate_not_ready"
    folder.mkdir(exist_ok=True)
    for name in ("tiny_ai_gatekeeper.c", "tiny_ai_gatekeeper.h", "tiny_ai_features.h"):
        shutil.copy2(MAIN / name, folder / name)
    (folder / "tiny_ai_model_weights.h").write_text(NOT_READY_WEIGHTS, encoding="utf-8")
    return [folder / "tiny_ai_gatekeeper.c" if path == gate else path for path in sources], ["-I", str(folder)]


def compile_host(sources: list[Path], out: Path) -> None:
    sources, gate_include = formula_only(sources, out)
    command = [
        find_cc(), "-std=c11", "-O2", "-Wall", "-Wextra", *gate_include, "-I", str(MAIN),
        "-o", str(out), *[str(path) for path in sources], "-lm",
    ]
    subprocess.check_call(command, cwd=str(WIEVAC))


class GainResponseTests(unittest.TestCase):
    def test_slope_recovers_and_shrinks_steps(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            exe = Path(directory) / "gain.exe"
            compile_host(
                [WIEVAC / "tests" / "gain_response_host_main.c", MAIN / "formula_flex.c"],
                exe,
            )
            result = subprocess.run([str(exe)], capture_output=True, text=True, check=True)
        rows = {}
        few = None
        for line in result.stdout.splitlines():
            parts = line.split()
            if parts[0] == "FEW":
                few = parts
            elif parts[0] == "CASE":
                rows[parts[1]] = {item.split("=", 1)[0]: item.split("=", 1)[1] for item in parts[2:]}
        self.assertIsNotNone(few, result.stdout)
        assert few is not None
        self.assertEqual(few[1], "0.0000", result.stdout)
        self.assertLess(int(few[2]), 30)
        for index, truth in enumerate(TRUTHS):
            row = rows[f"s{index}"]
            self.assertAlmostEqual(float(row["s"]), truth, delta=0.1, msg=result.stdout)
            hat = float(row["hat"])
            self.assertLessEqual(hat, float(row["raw"]) + 0.02, result.stdout)
            self.assertLessEqual(hat, float(row["full"]) + 0.02, result.stdout)
        self.assertEqual(rows["noise"]["s"], "0.0000", result.stdout)


class KnifeEdgeBlockTests(unittest.TestCase):
    def test_blocked_requires_a_closed_path(self) -> None:
        sources = [
            WIEVAC / "tests" / "ke_block_host_main.c",
            MAIN / "edge_result_v5_pipeline.c",
            MAIN / "formula_flex.c",
            MAIN / "noise_filter.c",
            MAIN / "tiny_ai_gatekeeper.c",
            MAIN / "tiny_ai_features.c",
        ]
        with tempfile.TemporaryDirectory() as directory:
            exe = Path(directory) / "ke.exe"
            compile_host(sources, exe)
            result = subprocess.run([str(exe)], capture_output=True, text=True, check=True)
        cases = {}
        for line in result.stdout.splitlines():
            parts = line.split()
            if parts[0] != "CASE":
                continue
            cases[parts[1]] = {item.split("=", 1)[0]: item.split("=", 1)[1] for item in parts[2:]}
        open_path = cases["open"]
        self.assertEqual(open_path["state"], "0", result.stdout)
        self.assertNotEqual(open_path["reason"], "7", result.stdout)
        self.assertNotEqual(open_path["reason"], "109", result.stdout)
        self.assertGreaterEqual(float(open_path["score"]), 95.0, result.stdout)
        blocked = cases["blocked"]
        self.assertEqual(blocked["state"], "2", result.stdout)
        self.assertEqual(blocked["reason"], "109", result.stdout)
        self.assertLess(float(blocked["score"]), 70.0, result.stdout)
        unlocked = cases["unlocked"]
        self.assertEqual(unlocked["state"], "2", result.stdout)
        self.assertEqual(unlocked["reason"], "109", result.stdout)
        disagree = cases["disagree"]
        self.assertEqual(disagree["state"], "1", result.stdout)
        self.assertEqual(disagree["reason"], "6", result.stdout)
