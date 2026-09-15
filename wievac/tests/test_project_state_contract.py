"""Executable checks for the active compact V6 software contract."""

from __future__ import annotations

import json
import re
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT.parent) not in sys.path:
    sys.path.insert(0, str(ROOT.parent))

from wievac.pi.app.edge_result_v5 import SCHEMA_VERSION
from wievac.pi.app.edge_result_v5_service import EdgeResultV5Service, V5ServiceConfig
from wievac.firmware.receiver.edge_result_v5_reference import FORMULA_VERSION


ACTIVE_CONFIG = ROOT / "config" / "corridors" / "corridor-01-edge-result-v5.json"


class ProjectStateContractTests(unittest.TestCase):
    def test_active_topology_matches_codec_and_model_schema(self) -> None:
        config = json.loads(ACTIVE_CONFIG.read_text(encoding="utf-8"))
        self.assertEqual(int(config["feature_schema_version"]), SCHEMA_VERSION)
        self.assertEqual(int(config["tiny_ai"]["feature_schema_version"]), SCHEMA_VERSION)
        # Loading the real topology exercises the same identity validation used
        # by the compact Pi runner, rather than only checking JSON text.
        service = EdgeResultV5Service(config=V5ServiceConfig(topology_path=str(ACTIVE_CONFIG)))
        self.assertEqual(set(service.ingest.identities), {"link-1", "link-2"})

    def test_formula_contract_is_explicit_and_shared(self) -> None:
        config = json.loads(ACTIVE_CONFIG.read_text(encoding="utf-8"))
        formula_version = str(config["formula"]["version"])
        self.assertEqual(formula_version, FORMULA_VERSION)
        self.assertEqual(config["formula"]["score_semantics"], "local_passability_index_uncalibrated")

    def test_project_state_is_utf8_and_has_no_mojibake(self) -> None:
        text = (ROOT / "PROJECT_STATE.md").read_text(encoding="utf-8")
        for marker in ("Ã", "Â", "á", "â", "ð", "�"):
            self.assertNotIn(marker, text)
        for required in (
            "RX-local",
            "adaptive Formula Flex",
            "only records accepted and rejected compact results",
            "old standalone V4 path has been removed",
            "EdgeResult V6 is implemented",
            "Tiny AI is `NOT_READY`",
            "uncalibrated",
            "field accuracy",
            "LightGBM",
        ):
            self.assertIn(required, text)

    def test_blocked_c_parity_is_not_reported_as_ready(self) -> None:
        report = (ROOT / "test-logs" / "codex-result.md").read_text(encoding="utf-8")
        status = dict(re.findall(r"^([A-Z][A-Z0-9_]+)=(.+)$", report, flags=re.MULTILINE))
        if status.get("C_WIRE_HARNESS_STATUS") == "BLOCKED":
            self.assertEqual(status.get("V6_STATUS"), "IMPLEMENTED_PENDING_PARITY")
            self.assertNotEqual(status.get("C_ACTIVE_FIRMWARE_STATUS"), "READY")


if __name__ == "__main__":
    unittest.main()
