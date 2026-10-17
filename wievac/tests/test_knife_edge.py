"""Knife-edge width/passability and mild sequence-gap scoring."""

from __future__ import annotations

import math
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path

from wievac.pi.app.edge_result_v5 import EdgeResultState, EdgeResultV5
from wievac.pi.app.edge_result_v5_runtime import EdgeResultV5Ingestor
from wievac.pi.app.edge_result_v5_service import EdgeResultV5Service, V5ServiceConfig

WIEVAC = Path(__file__).resolve().parents[1]
MAIN = WIEVAC / "firmware" / "receiver" / "main"

TABLE = {
    0.0: 0.0000,
    1.0: 0.0673,
    3.0: 0.1897,
    6.0: 0.3539,
    10.0: 0.5673,
    13.0: 0.7481,
    16.3: 0.9963,
    20.0: 1.0000,
}


def knife_edge_blocked(attenuation_db: float) -> float:
    if not math.isfinite(attenuation_db) or attenuation_db <= 0.0:
        return 0.0
    y = 10.0 ** ((attenuation_db - 6.9) / 20.0)
    v = 0.1 + (y * y - 1.0) / (2.0 * y)
    v = max(v, -0.7805)
    fresnel = 0.5 + v / (2.0 * math.sqrt(2.0))
    return min(1.0, max(0.0, (fresnel - 0.2241) / (1.0 - 0.2241)))


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


class KnifeEdgeFormulaTests(unittest.TestCase):
    def test_table_matches_python_and_c(self) -> None:
        for attenuation, expected in TABLE.items():
            got = knife_edge_blocked(attenuation)
            self.assertAlmostEqual(got, expected, delta=0.002)
            self.assertAlmostEqual(100.0 * (1.0 - got), 100.0 * (1.0 - expected), delta=0.2)
        with tempfile.TemporaryDirectory() as directory:
            exe = Path(directory) / "knife.exe"
            compile_host(
                [WIEVAC / "tests" / "knife_edge_host_main.c", MAIN / "formula_flex.c"],
                exe,
            )
            result = subprocess.run([str(exe)], capture_output=True, text=True, check=True)
        rows = {}
        fractions = {}
        for line in result.stdout.splitlines():
            parts = line.split()
            if parts[0] == "KE":
                rows[float(parts[1])] = float(parts[2])
            elif parts[0] == "FRAC":
                fractions[parts[1]] = parts[2]
        for attenuation, expected in TABLE.items():
            self.assertAlmostEqual(rows[attenuation], expected, delta=0.002)
            self.assertAlmostEqual(rows[attenuation], knife_edge_blocked(attenuation), delta=0.002)
        self.assertEqual(fractions["stronger"], "0.000000")
        self.assertEqual(fractions["deadband"], "0.000000")
        self.assertAlmostEqual(float(fractions["ten_db"]), knife_edge_blocked(10.0), delta=0.002)
        self.assertEqual(fractions["sparse"], "nan")



class BootSettleTests(unittest.TestCase):
    def _windows(self, settle_s: int) -> list[list[str]]:
        sources = [
            WIEVAC / "tests" / "boot_settle_host_main.c",
            MAIN / "edge_result_v5_pipeline.c",
            MAIN / "formula_flex.c",
            MAIN / "noise_filter.c",
            MAIN / "tiny_ai_gatekeeper.c",
            MAIN / "tiny_ai_features.c",
        ]
        with tempfile.TemporaryDirectory() as directory:
            exe = Path(directory) / "settle.exe"
            compile_host(sources, exe)
            result = subprocess.run([str(exe), str(settle_s)], capture_output=True, text=True, check=True)
        return [line.split() for line in result.stdout.splitlines() if line.startswith("W ")]

    def test_nothing_learns_or_scores_while_settling(self) -> None:
        rows = self._windows(120)
        settling = [row for row in rows if float(row[1]) <= 120.0]
        self.assertGreaterEqual(len(settling), 119)
        for row in settling:
            self.assertEqual(row[2], "3", row)          # UNKNOWN
            self.assertEqual(row[3], "2", row)          # WARMING_UP
            self.assertEqual(row[4], "0", row)          # no score
            self.assertEqual(row[5], "0", row)          # baseline not learned
            self.assertEqual(row[6], "0", row)
            self.assertEqual(row[7], "BOOT_SETTLE", row)
        after = [row for row in rows if float(row[1]) > 120.0]
        self.assertTrue(any(row[5] == "1" for row in after))
        self.assertTrue(any(row[4] == "1" and row[2] == "0" for row in after))
        # No window is dropped across the settle boundary.
        self.assertEqual(len(rows), len(self._windows(0)))

    def test_zero_settle_keeps_previous_behaviour(self) -> None:
        rows = self._windows(0)
        self.assertFalse(any(row[7] == "BOOT_SETTLE" for row in rows))
        self.assertTrue(any(row[4] == "1" for row in rows))


class MildGapPipelineTests(unittest.TestCase):
    def test_mild_gap_scores_and_does_not_learn(self) -> None:
        sources = [
            WIEVAC / "tests" / "mild_gap_host_main.c",
            MAIN / "edge_result_v5_pipeline.c",
            MAIN / "formula_flex.c",
            MAIN / "noise_filter.c",
            MAIN / "tiny_ai_gatekeeper.c",
            MAIN / "tiny_ai_features.c",
        ]
        with tempfile.TemporaryDirectory() as directory:
            exe = Path(directory) / "mild.exe"
            compile_host(sources, exe)
            result = subprocess.run([str(exe)], capture_output=True, text=True, check=True)
        cases = {}
        for line in result.stdout.splitlines():
            parts = line.split()
            if parts[0] != "CASE":
                continue
            fields = dict(item.split("=", 1) for item in parts[2:])
            cases[parts[1]] = fields
        for name in ("mild1", "mild2"):
            row = cases[name]
            self.assertEqual(row["valid"], "1", result.stdout)
            self.assertEqual(row["state"], "0", result.stdout)
            self.assertEqual(row["reason"], "102", result.stdout)
            self.assertEqual(row["learned"], "0", result.stdout)
            score = float(row["score"])
            self.assertGreaterEqual(score, 90.0)
            self.assertLessEqual(score, 100.0)
        for name in ("loss5", "gap200"):
            row = cases[name]
            self.assertEqual(row["valid"], "0", result.stdout)
            self.assertEqual(row["state"], "3", result.stdout)
            self.assertEqual(row["reason"], "102", result.stdout)
            self.assertEqual(row["learned"], "0", result.stdout)
        rebases = {}
        for line in result.stdout.splitlines():
            parts = line.split()
            if parts[0] != "REBASE":
                continue
            rebases[parts[1]] = dict(item.split("=", 1) for item in parts[2:])
        for name in ("clean", "mixed"):
            row = rebases[name]
            self.assertGreaterEqual(int(row["version"]), 4, result.stdout)
            self.assertEqual(row["pause"], "0", result.stdout)
            self.assertEqual(row["dirty"], "0", result.stdout)
        heavy = rebases["heavy"]
        self.assertEqual(heavy["version"], "3", result.stdout)
        self.assertGreater(int(heavy["pause"]), 0, result.stdout)
        self.assertGreater(int(heavy["dirty"]), 0, result.stdout)


def _result(**overrides):
    value = EdgeResultV5(
        device_id="device-1", node_id="node-1", tx_id="tx-1", rx_id="rx-1", link_id="1",
        corridor_id="corridor-1", session_id="session-1", boot_id=1, window_seq=1,
        window_start_us=999_000, window_end_us=1_000_000, rx_timestamp_us=1_000_000,
        age_ms=0, local_passability_score=40.0, state=EdgeResultState.PASSABLE,
        quality=90.0, uncertainty=10.0, disagreement=False, reason_code=1,
        formula_version="formula-flex-v5.4-atten-exp", model_version="NOT_READY",
        model_hash="", feature_schema_version="8", sample_count=10, invalid_count=0,
        queue_drop_count=0, sequence_gap=0, packet_loss_ratio=0.0, jitter_ms=0.5,
        width_score=87.5,
    ).normalized()
    if not overrides:
        return value
    return EdgeResultV5(**{**value.__dict__, **overrides}).normalized()


class KnifeEdgePiTests(unittest.TestCase):
    def _service(self) -> EdgeResultV5Service:
        topology = {
            "protocol_version": 5,
            "links": [{
                "link_id": "1", "device_id": "device-1", "node_id": "node-1",
                "tx_id": "tx-1", "rx_id": "rx-1", "corridor_id": "corridor-1",
                "nominal_distance_m": 5.0,
            }],
        }
        return EdgeResultV5Service(config=V5ServiceConfig(topology=topology))

    def test_width_is_percent_and_flow_is_passability(self) -> None:
        service = self._service()
        fresh = service._annotate_capacity({"links": {"1": {
            "width_score": 87.5, "local_passability_score": 40.0, "state": "PASSABLE",
        }}})
        row = fresh["links"]["1"]
        self.assertEqual(row["w_cap"], 87.5)
        self.assertEqual(row["width_score"], 87.5)
        self.assertEqual(row["cap_base"], 100.0)
        self.assertEqual(row["w_base"], 100.0)
        self.assertEqual(row["effective_flow"], 40.0)
        self.assertEqual(row["nominal_distance_m"], 5.0)
        old = service._annotate_capacity({"links": {"1": {
            "width_score": 28674, "local_passability_score": 40.0, "state": "PASSABLE",
        }}})
        hidden = old["links"]["1"]
        self.assertIsNone(hidden["width_score"])
        self.assertIsNone(hidden["w_cap"])
        self.assertIsNone(hidden["cap_base"])
        self.assertEqual(hidden["effective_flow"], 40.0)

    def test_runtime_does_not_scale_flow_by_width(self) -> None:
        ingest = EdgeResultV5Ingestor(identities={"1": {
            "device_id": "device-1", "node_id": "node-1", "tx_id": "tx-1",
            "rx_id": "rx-1", "corridor_id": "corridor-1",
        }})
        self.assertTrue(ingest.ingest(_result().encode(), now_us=1_000_000).accepted)
        link = ingest.latest_by_link(now_us=1_000_000)["1"]
        self.assertEqual(link["w_cap"], 87.5)
        self.assertEqual(link["effective_flow"], 40.0)
        ingest.ingest(_result(window_seq=2, window_end_us=2_000_000, rx_timestamp_us=2_000_000,
                              width_score=28674.0).encode(), now_us=2_000_000)
        old = ingest.latest_by_link(now_us=2_000_000)["1"]
        self.assertIsNone(old["w_cap"])
        self.assertIsNone(old["width_score"])
        self.assertEqual(old["effective_flow"], 40.0)


class KnifeEdgeDashboardTests(unittest.TestCase):
    def test_width_label_is_percent(self) -> None:
        html = (WIEVAC / "pi" / "app" / "dashboard.html").read_text(encoding="utf-8")
        self.assertIn("Độ rộng còn lại (vật cản tĩnh)", html)
        self.assertIn('Math.round(width) + "%"', html)
        self.assertIn('"-" + (100 - width).toFixed(0) + "% do vật cản tĩnh"', html)
        self.assertIn(">HẸP<", html)
        self.assertNotIn("capBase", html)
        self.assertNotIn("Dung lượng tối đa", html)
        self.assertIn("Lưu thông tức thời", html)




class ShiftWindowDropTests(unittest.TestCase):
    """An environment-shift transition must never emit UNKNOWN with a score:
    the encoder rejects it and the window is silently dropped (Pi shows STALE)."""

    def test_transition_never_emits_unknown_with_score(self) -> None:
        sources = [
            WIEVAC / "tests" / "shift_drop_host_main.c",
            MAIN / "edge_result_v5_pipeline.c",
            MAIN / "formula_flex.c",
            MAIN / "noise_filter.c",
            MAIN / "tiny_ai_gatekeeper.c",
            MAIN / "tiny_ai_features.c",
        ]
        with tempfile.TemporaryDirectory() as directory:
            exe = Path(directory) / "shift.exe"
            compile_host(sources, exe)
            result = subprocess.run([str(exe)], capture_output=True, text=True, check=True)
        line = next(item for item in result.stdout.splitlines() if item.startswith("SHIFT "))
        fields = dict(item.split("=", 1) for item in line.split()[1:])
        self.assertGreater(int(fields["shift_windows"]), 0, result.stdout)
        self.assertEqual(fields["unknown_with_score"], "0", result.stdout)
        self.assertGreater(int(fields["held_degraded"]), 0, result.stdout)


if __name__ == "__main__":
    unittest.main()
