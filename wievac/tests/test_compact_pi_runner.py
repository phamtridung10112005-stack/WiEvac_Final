from __future__ import annotations

import ast
import subprocess
import sys
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
RUNNER = ROOT / "scripts" / "run_pi_v5_compact.py"


def _source() -> str:
    return RUNNER.read_text(encoding="utf-8")


class CompactPiRunnerTests(unittest.TestCase):
    def test_active_runner_has_no_legacy_analysis_import_or_symbols(self) -> None:
        source = _source()
        tree = ast.parse(source)
        imports = [node for node in ast.walk(tree) if isinstance(node, (ast.Import, ast.ImportFrom))]
        imported = "\n".join(ast.unparse(node) for node in imports)
        self.assertNotIn("pi_v5_ai_core", imported)
        self.assertNotIn("DualLinkFusion", source)
        self.assertNotIn("QuietReferenceLearner", source)
        self.assertNotIn("TrendForecaster", source)
        self.assertNotIn("DirectionRouter", source)
        self.assertNotIn("DataRoutePolicy", source)
        self.assertNotIn("raw_csi", source)
        self.assertNotIn("dynamic_frame", source)


    def test_active_runner_mounts_compact_service_only(self) -> None:
        source = _source()
        self.assertIn("EdgeResultV5Service", source)
        self.assertIn("enable_reference_modules=False", source)
        self.assertIn("decode_edge_result", source)
        self.assertIn("ingest_datagram", source)
        self.assertIn("render_dashboard_html", source)
        self.assertIn("control_recording", source)
        self.assertIn("stop_recording", source)
        self.assertIn("/api/v5/labels", source)
        self.assertIn("/api/v5/recorder", source)
        self.assertIn('ROOT / "data" / "real" / "compact"', source)
        self.assertIn('ROOT / "data" / "labels"', source)
        self.assertIn("schedule_path", source)
        self.assertIn("capture-schedules.json", source)
        self.assertNotIn("run_pi_v4.py", source)
        self.assertNotIn("pi_v5_ai_core.py", source)

    def test_compact_startup_wrappers_target_compact_runner(self) -> None:
        for name in ("startup_pi_v5_compact.ps1", "startup_pi_v5_compact.sh"):
            source = (ROOT / "scripts" / name).read_text(encoding="utf-8")
            self.assertIn("run_pi_v5_compact.py", source)
            self.assertNotIn("run_pi_v4.py", source)
            self.assertNotIn("pi_v5_ai_core", source)


    def test_compact_check_only_uses_v5_topology_without_starting_legacy_process(self) -> None:
        completed = subprocess.run(
            [sys.executable, str(RUNNER), "--check-only"],
            cwd=str(ROOT.parent),
            capture_output=True,
            text=True,
            timeout=15,
            check=False,
        )
        self.assertEqual(completed.returncode, 0, completed.stdout + completed.stderr)
        self.assertIn("Pi compact V5 checks passed", completed.stdout)
        self.assertNotIn("pi_v5_ai_core", completed.stdout)
