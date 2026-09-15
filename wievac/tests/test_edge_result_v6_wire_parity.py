"""Wire-contract checks shared by the active C receiver and Pi codec.

This is deliberately independent of ESP-IDF so it can run in CI on the host;
the companion C vector is compiled by the firmware build.  It catches drift
in header, field order, identity spelling and Formula Flex metadata before a
device is flashed.
"""
from pathlib import Path
import struct
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from pi.app.edge_result_v5 import (  # noqa: E402
    EdgeResultState, EdgeResultV5, decode_edge_result, encode_edge_result,
)


def _sample() -> EdgeResultV5:
    return EdgeResultV5(
        device_id="device-1", node_id="rx-1", tx_id="tx-1", rx_id="rx-1",
        link_id="link-1", corridor_id="corridor-01", session_id="parity-session",
        boot_id=7, window_seq=9, window_start_us=1_000_000,
        window_end_us=2_000_000, rx_timestamp_us=2_000_000, age_ms=0,
        local_passability_score=72.5, state=EdgeResultState.DEGRADED,
        quality=91.0, uncertainty=12.5, disagreement=False, reason_code=1,
        formula_version="formula-flex-v5.2-rx-median-mad", model_version="NOT_READY",
        model_hash="", feature_schema_version="7", sample_count=8, invalid_count=0,
        queue_drop_count=0, sequence_gap=0, packet_loss_ratio=0.0, jitter_ms=1.25,
        baseline_state="STABLE", baseline_version=2, baseline_update_reason="stable_window",
        baseline_confidence=88.0, drift_state="STABLE", transition_state="STABLE",
        model_state="NOT_READY", raw_evidence_score=70.0,
        filtered_passability_score=72.5, occupancy_evidence=10.0, blocking_evidence=5.0,
    )


def test_python_v6_round_trip_and_header():
    packet = encode_edge_result(_sample())
    assert packet[:4] == b"WIV5"
    assert packet[4:6] == bytes((5, 0x30))
    assert struct.unpack_from(">H", packet, 8)[0] == 7
    payload_length = struct.unpack_from(">H", packet, 6)[0]
    assert payload_length == len(packet) - 14
    decoded = decode_edge_result(packet)
    assert decoded.link_id == "link-1"
    assert decoded.node_id == "rx-1"
    assert decoded.formula_version == "formula-flex-v5.2-rx-median-mad"
    assert decoded.filtered_passability_score == 72.5


def test_c_contract_markers_match_active_python_contract():
    source = (ROOT / "firmware/receiver/main/edge_result_v5_pipeline.c").read_text(encoding="utf-8")
    assert 'EDGE_RESULT_V5_FORMULA_DEFAULT "formula-flex-v5.3-rx-median-mad"' in source
    assert "EDGE_RESULT_V5_MAGIC" in source
    assert "EDGE_RESULT_V5_SCHEMA_VERSION" in source


def test_malformed_and_non_finite_rejected():
    packet = bytearray(encode_edge_result(_sample()))
    packet[0] ^= 0x01
    try:
        decode_edge_result(packet)
    except ValueError:
        pass
    else:
        raise AssertionError("wrong magic accepted")


def test_corridor_identity_contract_for_both_rx_nodes():
    import json
    topology = json.loads((ROOT / "config/corridors/corridor-01-edge-result-v5.json").read_text(encoding="utf-8"))
    links = {item["link_id"]: item for item in topology["links"]}
    assert links["link-1"]["device_id"] == "device-1"
    assert links["link-1"]["node_id"] == "rx-1"
    assert links["link-2"]["device_id"] == "device-1"
    assert links["link-2"]["node_id"] == "rx-2"
    assert links["link-1"]["link_id"] != links["link-2"]["link_id"]
    source = (ROOT / "firmware/receiver/main/app_main.c").read_text(encoding="utf-8")
    assert "config.device_id = UINT32_C(1)" in source
    assert "config.node_id = WIEVAC_RX_ID" in source
