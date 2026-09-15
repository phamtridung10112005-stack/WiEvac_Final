"""Static parity checks for the shared Python/C reason registry."""

from __future__ import annotations

import re
from pathlib import Path
import unittest

from wievac.pi.app.edge_result_v5 import EdgeResultReason


class EdgeResultReasonRegistryTests(unittest.TestCase):
    def test_python_and_c_reason_codes_match(self) -> None:
        header = Path("wievac/firmware/receiver/main/edge_result_v5_pipeline.h").read_text(encoding="utf-8")
        c_entries = {
            name: int(value)
            for name, value in re.findall(
                r"EDGE_RESULT_REASON_([A-Z0-9_]+)\s*=\s*(\d+)", header
            )
        }
        self.assertGreaterEqual(len(c_entries), 16)

        # Python retains historical aliases; map those aliases to the C wire
        # spelling while comparing numeric IDs.
        aliases = {
            "UNKNOWN": "NONE",
            "DISAGREEMENT": "FORMULA_AI_DISAGREEMENT",
            "MODEL_OOD": "OOD",
        }
        for py_name, py_member in EdgeResultReason.__members__.items():
            c_name = aliases.get(py_name, py_name)
            self.assertIn(c_name, c_entries, py_name)
            self.assertEqual(int(py_member), c_entries[c_name], py_name)

    def test_required_fail_closed_reasons_are_named(self) -> None:
        required = {
            "VALID", "WARMING_UP", "INVALID_CSI", "STALE", "QUALITY_LOW",
            "SEQUENCE_GAP", "QUEUE_DROP", "MODEL_OOD", "MODEL_REJECTED",
            "FORMULA_AI_DISAGREEMENT", "ENVIRONMENT_SHIFT",
            "PERSISTENT_OCCUPANCY", "BLOCKED",
        }
        self.assertTrue(required.issubset(EdgeResultReason.__members__))

    def test_c_reason_name_switch_covers_wire_registry(self) -> None:
        source = Path("wievac/firmware/receiver/main/edge_result_v5_pipeline.c").read_text(encoding="utf-8")
        switch = source[source.index("const char *edge_result_reason_name"):]
        header = Path("wievac/firmware/receiver/main/edge_result_v5_pipeline.h").read_text(encoding="utf-8")
        names = re.findall(r"EDGE_RESULT_REASON_([A-Z0-9_]+)\s*=\s*(\d+)", header)
        for name, _value in names:
            self.assertIn("case EDGE_RESULT_REASON_" + name + ":", switch, name)


class EdgeResultWireBoundTests(unittest.TestCase):
    def test_python_and_c_v6_payload_bounds_match(self) -> None:
        from wievac.pi.app import edge_result_v5 as codec

        header = Path("wievac/firmware/receiver/main/edge_result_v5_pipeline.h").read_text(encoding="utf-8")
        source = Path("wievac/firmware/receiver/main/edge_result_v5_pipeline.c").read_text(encoding="utf-8")
        packet_limit = int(re.search(r"EDGE_RESULT_V5_MAX_PACKET\s+(\d+)U", header).group(1))
        c_payload_limit = int(re.search(r"EDGE_RESULT_V5_MAX_WIRE_PAYLOAD\s+(\d+)U", source).group(1))
        self.assertEqual(codec.MAX_PAYLOAD_BYTES, c_payload_limit)
        self.assertGreaterEqual(packet_limit, codec.MAX_PACKET_BYTES)


if __name__ == "__main__":
    unittest.main()
