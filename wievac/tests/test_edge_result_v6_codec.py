"""V6 adaptive metadata and V5 decode compatibility checks."""

from __future__ import annotations

from pathlib import Path
import json
import sys
import unittest

from wievac.pi.app.edge_result_v5 import (  # noqa: E402
    EdgeResultState,
    EdgeResultV5,
    SCHEMA_V5,
    SCHEMA_V6,
    SCHEMA_V7,
    SCHEMA_VERSION,
    decode_edge_result,
    encode_edge_result,
)
from wievac.firmware.receiver.edge_result_v5_reference import (  # noqa: E402
    CsiFrame,
    FormulaFlex,
    LinkEdgePipeline,
    FORMULA_VERSION,
    FEATURE_SCHEMA_VERSION,
)


def _result(**changes: object) -> EdgeResultV5:
    values: dict[str, object] = {
        "device_id": "device-1", "node_id": "node-1", "tx_id": "tx-1",
        "rx_id": "rx-1", "link_id": "link-1", "corridor_id": "corridor-a",
        "session_id": "session-1", "boot_id": 7, "window_seq": 42,
        "window_start_us": 1_000_000, "window_end_us": 1_050_000,
        "rx_timestamp_us": 1_050_500, "age_ms": 4, "local_passability_score": 82.5,
        "state": EdgeResultState.DEGRADED, "quality": 91.0, "uncertainty": 8.0,
        "disagreement": True, "reason_code": 7,
        "formula_version": "formula-flex-v6", "model_version": "tiny-v6",
        "model_hash": "a" * 64, "feature_schema_version": "7", "sample_count": 25,
        "invalid_count": 1, "queue_drop_count": 2, "sequence_gap": 3,
        "packet_loss_ratio": 0.02, "jitter_ms": 1.25,
        "baseline_state": "SHIFT_CANDIDATE", "baseline_version": 17,
        "baseline_update_reason": "persistent_environment_shift", "baseline_confidence": 73.5,
        "drift_state": "SHIFT_CANDIDATE", "raw_evidence_score": 44.5,
        "filtered_passability_score": 41.25, "transition_state": "TRANSITION",
        "occupancy_evidence": 66.0, "blocking_evidence": 55.0, "model_state": "REJECTED",
    }
    values.update(changes)
    return EdgeResultV5(**values)  # type: ignore[arg-type]


class EdgeResultV6CodecTests(unittest.TestCase):
    def test_active_rx_pipeline_v6_round_trip_contract(self) -> None:
        pipeline = LinkEdgePipeline(
            device_id="device-1", node_id="rx-1", tx_id="tx-1", rx_id="rx-1",
            link_id="link-1", corridor_id="corridor-1", session_id="session-1",
            expected_source_mac="aa:bb", formula=FormulaFlex(warmup_samples=3),
        )
        pipeline.confirm_empty()
        output = None
        for sequence in range(1, 4):
            pipeline.enqueue(CsiFrame(
                device_id="device-1", node_id="rx-1", tx_id="tx-1", rx_id="rx-1",
                link_id="link-1", corridor_id="corridor-1", session_id="session-1",
                boot_id=1, sequence=sequence, timestamp_us=1_000_000 + sequence * 1_000,
                i_q=((1.0, 0.0),) * 8, source_mac="aa:bb", expected_source_mac="aa:bb",
            ))
            output = pipeline.process_one() or output
        output = pipeline.flush() or output
        self.assertIsNotNone(output)
        self.assertEqual(output.feature_schema_version, FEATURE_SCHEMA_VERSION)
        self.assertEqual(output.feature_schema_version, "6")
        self.assertEqual(output.formula_version, FORMULA_VERSION)
        packet = encode_edge_result(output, schema_version=SCHEMA_V6)
        self.assertEqual(int.from_bytes(packet[8:10], "big"), SCHEMA_V6)
        decoded = decode_edge_result(packet)
        self.assertEqual(decoded.feature_schema_version, "6")
        self.assertEqual(decoded.formula_version, FORMULA_VERSION)

    def test_active_corridor_config_matches_codec_schema_and_formula_contract(self) -> None:
        root = Path(__file__).resolve().parents[1]
        config = json.loads((root / "config/corridors/corridor-01-edge-result-v5.json").read_text(encoding="utf-8"))
        formula_source = (root / "firmware/receiver/edge_result_v5_reference.py").read_text(encoding="utf-8")
        self.assertEqual(int(config["feature_schema_version"]), SCHEMA_VERSION)
        self.assertEqual(int(config["tiny_ai"]["feature_schema_version"]), SCHEMA_VERSION)
        self.assertIn(f'FORMULA_VERSION = "{config["formula"]["version"]}"', formula_source)

    def test_v6_round_trip_preserves_adaptive_metadata(self) -> None:
        original = _result()
        packet = encode_edge_result(original)
        self.assertEqual(int.from_bytes(packet[8:10], "big"), SCHEMA_V7)
        decoded = decode_edge_result(packet)
        for field in (
            "baseline_state", "baseline_version", "baseline_update_reason",
            "baseline_confidence", "drift_state", "raw_evidence_score",
            "filtered_passability_score", "transition_state", "occupancy_evidence",
            "blocking_evidence", "model_state", "doppler_valid", "doppler_ratio",
            "doppler_fs_hz", "doppler_samples",
        ):
            self.assertEqual(getattr(decoded, field), getattr(original.normalized(), field), field)

    def test_v5_packet_decodes_with_conservative_metadata_defaults(self) -> None:
        packet = encode_edge_result(_result(feature_schema_version="5"), schema_version=SCHEMA_V5)
        self.assertEqual(int.from_bytes(packet[8:10], "big"), SCHEMA_V5)
        decoded = decode_edge_result(packet)
        self.assertEqual(decoded.feature_schema_version, "5")
        self.assertEqual(decoded.baseline_version, 0)
        self.assertEqual(decoded.baseline_update_reason, "v5_compatibility_decode")
        self.assertIsNone(decoded.raw_evidence_score)
        self.assertIsNone(decoded.filtered_passability_score)
        self.assertIsNone(decoded.occupancy_evidence)
        self.assertIsNone(decoded.blocking_evidence)

    def test_active_v6_rejects_v5_feature_payload(self) -> None:
        with self.assertRaises(Exception):
            encode_edge_result(_result(feature_schema_version="5"), schema_version=SCHEMA_V6)

    def test_schema7_doppler_round_trip(self) -> None:
        original = _result(doppler_valid=True, doppler_ratio=0.42, doppler_fs_hz=100.0, doppler_samples=64)
        packet = encode_edge_result(original)
        self.assertEqual(int.from_bytes(packet[8:10], "big"), SCHEMA_V7)
        decoded = decode_edge_result(packet)
        self.assertTrue(decoded.doppler_valid)
        self.assertAlmostEqual(decoded.doppler_ratio, 0.42, places=5)
        self.assertAlmostEqual(decoded.doppler_fs_hz, 100.0, places=3)
        self.assertEqual(decoded.doppler_samples, 64)
        self.assertAlmostEqual(decoded.occupancy_evidence, 66.0, places=5)


if __name__ == "__main__":
    unittest.main()
