"""Software-only contract tests for the EdgeResult V5 codec."""

from __future__ import annotations

import math
import struct
import unittest
from pathlib import Path
import sys


sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "pi" / "app"))

from edge_result_v5 import (  # noqa: E402
    HEADER,
    MAGIC,
    PROTOCOL_VERSION,
    SCHEMA_VERSION,
    EdgeResultProtocolError,
    EdgeResultReason,
    EdgeResultState,
    EdgeResultV5,
    decode_edge_result,
    encode_edge_result,
    MAX_PAYLOAD_BYTES,
)


def result(**changes: object) -> EdgeResultV5:
    values: dict[str, object] = {
        "device_id": "device-1",
        "node_id": "node-1",
        "tx_id": "tx-1",
        "rx_id": "rx-1",
        "link_id": "link-1",
        "corridor_id": "corridor-a",
        "session_id": "session-1",
        "boot_id": 7,
        "window_seq": 42,
        "window_start_us": 1_000_000,
        "window_end_us": 1_050_000,
        "rx_timestamp_us": 1_050_500,
        "age_ms": 4,
        "local_passability_score": 82.5,
        "state": EdgeResultState.PASSABLE,
        "quality": 91.0,
        "uncertainty": 8.0,
        "disagreement": False,
        "reason_code": EdgeResultReason.VALID,
        "formula_version": "formula-flex-1",
        "model_version": "none",
        "model_hash": "",
        "feature_schema_version": "7",
        "sample_count": 25,
        "invalid_count": 1,
        "queue_drop_count": 0,
        "sequence_gap": 0,
        "packet_loss_ratio": 0.02,
        "jitter_ms": 1.25,
    }
    values.update(changes)
    return EdgeResultV5(**values)  # type: ignore[arg-type]


class EdgeResultV5CodecTests(unittest.TestCase):
    def test_round_trip_preserves_identity_and_nullable_fields(self) -> None:
        original = result()
        packet = encode_edge_result(original)
        decoded = decode_edge_result(packet)
        expected = original.normalized()
        for field in ("device_id", "node_id", "tx_id", "rx_id", "link_id", "corridor_id",
                      "session_id", "boot_id", "window_seq", "window_start_us", "window_end_us",
                      "rx_timestamp_us", "age_ms", "local_passability_score", "state", "disagreement",
                      "reason_code", "formula_version", "model_version", "model_hash",
                      "feature_schema_version", "sample_count", "invalid_count", "queue_drop_count",
                      "sequence_gap"):
            self.assertEqual(getattr(decoded, field), getattr(expected, field), field)
        self.assertAlmostEqual(decoded.quality, expected.quality, places=5)
        self.assertAlmostEqual(decoded.uncertainty, expected.uncertainty, places=5)
        self.assertAlmostEqual(decoded.packet_loss_ratio, expected.packet_loss_ratio, places=6)
        self.assertAlmostEqual(decoded.jitter_ms, expected.jitter_ms, places=5)
        self.assertEqual(HEADER.unpack(packet[:HEADER.size])[:5],
                         (MAGIC, PROTOCOL_VERSION, 0x30, len(packet) - HEADER.size, SCHEMA_VERSION))

    def test_unknown_score_is_null_and_not_zero(self) -> None:
        packet = encode_edge_result(result(state="UNKNOWN", local_passability_score=None,
                                           reason_code="STALE"))
        decoded = decode_edge_result(packet)
        self.assertEqual(decoded.state, EdgeResultState.UNKNOWN)
        self.assertIsNone(decoded.local_passability_score)

    def test_unknown_with_score_is_rejected(self) -> None:
        with self.assertRaisesRegex(EdgeResultProtocolError, "unknown_score_must_be_null"):
            encode_edge_result(result(state=EdgeResultState.UNKNOWN, local_passability_score=0.0))

    def test_crc_magic_version_schema_and_length_rejection(self) -> None:
        packet = bytearray(encode_edge_result(result()))
        packet[-1] ^= 0x01
        with self.assertRaisesRegex(EdgeResultProtocolError, "crc_mismatch"):
            decode_edge_result(packet)

        for field, value, reason in ((0, 0, "wrong_magic"), (4, 4, "wrong_protocol_version"), (8, 4, "wrong_schema_version")):
            bad = bytearray(encode_edge_result(result()))
            if field == 0:
                struct.pack_into(">I", bad, field, value)
            elif field == 4:
                bad[field] = value
            else:
                struct.pack_into(">H", bad, field, value)
            with self.subTest(reason=reason):
                with self.assertRaisesRegex(EdgeResultProtocolError, reason):
                    decode_edge_result(bad)

        bad_length = bytearray(encode_edge_result(result()))
        struct.pack_into(">H", bad_length, 6, 1)
        with self.assertRaisesRegex(EdgeResultProtocolError, "payload_length_mismatch"):
            decode_edge_result(bad_length)

    def test_non_finite_and_range_validation(self) -> None:
        for field, value, reason in (("quality", math.nan, "quality_non_finite"),
                                     ("uncertainty", math.inf, "uncertainty_non_finite"),
                                     ("packet_loss_ratio", 1.1, "packet_loss_ratio_out_of_range"),
                                     ("jitter_ms", -1.0, "jitter_ms_out_of_range")):
            with self.subTest(field=field):
                with self.assertRaisesRegex(EdgeResultProtocolError, reason):
                    encode_edge_result(result(**{field: value}))

    def test_identity_and_counter_validation(self) -> None:
        with self.assertRaisesRegex(EdgeResultProtocolError, "link_id_empty"):
            encode_edge_result(result(link_id=""))
        with self.assertRaisesRegex(EdgeResultProtocolError, "invalid_count_exceeds_sample_count"):
            encode_edge_result(result(sample_count=1, invalid_count=2))
        with self.assertRaisesRegex(EdgeResultProtocolError, "link_id_contains_control"):
            encode_edge_result(result(link_id="link\n1"))

    def test_timestamp_must_not_precede_window_end(self) -> None:
        with self.assertRaisesRegex(EdgeResultProtocolError, "rx_timestamp_before_window_end"):
            encode_edge_result(result(rx_timestamp_us=1_049_999))

    def test_firmware_text_and_payload_limits_are_enforced(self) -> None:
        with self.assertRaisesRegex(EdgeResultProtocolError, "link_id_too_long"):
            encode_edge_result(result(link_id="x" * 32))
        # Model hashes use the firmware's dedicated 64-byte buffer.
        packet = encode_edge_result(result(model_hash="a" * 64))
        self.assertLessEqual(len(packet) - HEADER.size, MAX_PAYLOAD_BYTES)
        with self.assertRaisesRegex(EdgeResultProtocolError, "model_hash_too_long"):
            encode_edge_result(result(model_hash="a" * 65))

    def test_decode_rejects_payload_above_firmware_buffer(self) -> None:
        # Construct a syntactically valid header with the maximum uint16
        # length; the decoder must reject it before attempting payload parsing.
        payload = b"x" * (MAX_PAYLOAD_BYTES + 1)
        prefix = struct.pack(">IBBHH", MAGIC, PROTOCOL_VERSION, 0x30,
                             len(payload), SCHEMA_VERSION)
        from edge_result_v5 import crc32
        packet = prefix + struct.pack(">I", crc32(prefix, payload)) + payload
        with self.assertRaisesRegex(EdgeResultProtocolError, "payload_too_large"):
            decode_edge_result(packet)


if __name__ == "__main__":
    unittest.main()
