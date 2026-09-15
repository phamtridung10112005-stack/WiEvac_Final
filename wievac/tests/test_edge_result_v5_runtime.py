import json
import tempfile
import unittest
from pathlib import Path

from wievac.pi.app.edge_result_v5 import EdgeResultState, EdgeResultV5
from wievac.pi.app.edge_result_v5_runtime import EdgeResultRecorder, EdgeResultV5Ingestor
from wievac.pi.app.edge_result_v5_service import EdgeResultV5Service, V5ServiceConfig


def result(*, link="1", boot=1, seq=1, timestamp=1_000_000, state=EdgeResultState.PASSABLE, score=80.0, quality=90.0):
    return EdgeResultV5(
        device_id="device-1", node_id="node-1", tx_id="tx-1", rx_id="rx-1", link_id=link,
        corridor_id="corridor-1", session_id="session-1", boot_id=boot, window_seq=seq,
        window_start_us=timestamp - 1000, window_end_us=timestamp, rx_timestamp_us=timestamp,
        age_ms=0, local_passability_score=None if state is EdgeResultState.UNKNOWN else score,
        state=state, quality=quality, uncertainty=10.0, disagreement=False, reason_code=1,
        formula_version="formula-flex-v5.2-rx-median-mad", model_version="NOT_READY", model_hash="",
        feature_schema_version="7", sample_count=10, invalid_count=0, queue_drop_count=0,
        sequence_gap=0, packet_loss_ratio=0.0, jitter_ms=0.5,
    ).normalized()


class EdgeResultV5RuntimeTests(unittest.TestCase):
    def test_topology_path_loads_without_mutating_frozen_config(self):
        path = Path(__file__).parents[1] / "config" / "corridors" / "corridor-01-edge-result-v5.json"
        service = EdgeResultV5Service(topology_path=str(path))
        self.assertEqual(set(service.ingest.identities), {"link-1", "link-2"})

    def test_topology_identity_is_not_open_for_spoofed_metadata(self):
        topology = {
            "protocol_version": 5,
            "links": [{"link_id": "link-1", "source": "A", "target": "B", "device_id": "device-1", "node_id": "rx-1", "tx_id": "tx-1", "rx_id": "rx-1", "corridor_id": "corridor-1"}],
        }
        service = EdgeResultV5Service(config=V5ServiceConfig(topology=topology))
        spoof = result(link="link-1")
        spoof = EdgeResultV5(**{**spoof.__dict__, "device_id": "evil", "node_id": "evil-node"})
        self.assertEqual(service.ingest_datagram(spoof.encode(), now_us=1_000_000).reason, "identity:mismatch")

    def test_duplicate_out_of_order_old_boot_and_isolation(self):
        ingest = EdgeResultV5Ingestor(identities={"1": {"device_id": "device-1", "node_id": "node-1", "tx_id": "tx-1", "rx_id": "rx-1", "corridor_id": "corridor-1"}, "2": {"device_id": "device-1", "node_id": "node-2", "tx_id": "tx-1", "rx_id": "rx-2", "corridor_id": "corridor-1"}})
        first = result(link="1")
        self.assertTrue(ingest.ingest(first.encode(), now_us=1_000_000).accepted)
        self.assertEqual(ingest.ingest(first.encode(), now_us=1_000_000).reason, "duplicate")
        self.assertEqual(ingest.ingest(result(link="1", seq=2, timestamp=999_000).encode(), now_us=1_000_000).reason, "timestamp:nonmonotonic")
        # A bad RX-2 identity is rejected without clearing RX-1.
        bad = result(link="2", timestamp=1_001_000)
        self.assertEqual(ingest.ingest(bad.encode(), now_us=1_001_000).reason, "identity:mismatch")
        self.assertEqual(ingest.latest_by_link(now_us=1_000_000)["1"]["score"], 80.0)

    def test_stale_hides_score_and_route_excludes_unknown(self):
        ingest = EdgeResultV5Ingestor(identities={"1": {"device_id": "device-1", "node_id": "node-1", "tx_id": "tx-1", "rx_id": "rx-1", "corridor_id": "corridor-1"}}, stale_after_ms=100)
        ingest.ingest(result().encode(), now_us=1_000_000)
        self.assertIsNone(ingest.latest_by_link(now_us=1_200_000)["1"]["score"])
        service = EdgeResultV5Service(identities={"1": {"device_id": "device-1", "node_id": "node-1", "tx_id": "tx-1", "rx_id": "rx-1", "corridor_id": "corridor-1"}}, config=V5ServiceConfig(enable_reference_modules=True))
        service.ingest_datagram(result().encode(), now_us=1_000_000)
        service.api.router.add_link("A", "B", "1")
        self.assertEqual(service.response("/api/v5/route", source="A", target="B", now_us=9_000_000)["route_state"], "NO_ROUTE")

    def test_recorder_appends_one_file_per_link(self):
        with tempfile.TemporaryDirectory() as directory:
            recorder = EdgeResultRecorder(directory)
            started = recorder.start("session-1")
            recorder.append(result(), arrival_us=1_000_500)
            recorder.append(result(link="2", timestamp=1_001_000), arrival_us=1_001_500)
            self.assertEqual(started, Path(directory))
            final = recorder.close(success=True)
            self.assertTrue(final.exists() or Path(directory).exists())
            first = json.loads((Path(directory) / "1.jsonl").read_text(encoding="utf-8").splitlines()[0])
            second = json.loads((Path(directory) / "2.jsonl").read_text(encoding="utf-8").splitlines()[0])
            self.assertEqual(first["record_type"], "edge_result_v5")
            self.assertEqual(first["arrival_timestamp_us"], 1_000_500)
            self.assertEqual(first["capture_session"], "session-1")
            self.assertEqual(second["link_id"], "2")
            self.assertNotIn("raw_csi", first)
            self.assertNotIn("doppler_ratio", first)
            doppler = json.loads((Path(directory) / "1-doppler.jsonl").read_text(encoding="utf-8").splitlines()[0])
            self.assertEqual(doppler["record_type"], "doppler_shadow")
            self.assertEqual(doppler["link_id"], "1")
            self.assertEqual(doppler["window_seq"], 1)
            self.assertIn("doppler_valid", doppler)
            self.assertIn("doppler_ratio", doppler)
            self.assertIn("raw_evidence_score", doppler)
            recorder.start("session-2")
            recorder.append(result(seq=2, timestamp=1_002_000), arrival_us=1_002_500)
            recorder.close(success=True)
            lines = (Path(directory) / "1.jsonl").read_text(encoding="utf-8").splitlines()
            self.assertEqual(len(lines), 2)
            self.assertEqual(json.loads(lines[1])["capture_session"], "session-2")

    def test_arrival_clock_is_used_for_freshness_not_node_clock(self):
        service = EdgeResultV5Service(
            identities={"1": {"device_id": "device-1", "node_id": "node-1", "tx_id": "tx-1", "rx_id": "rx-1", "corridor_id": "corridor-1"}},
            config=V5ServiceConfig(stale_after_ms=100),
        )
        # Node timestamp is deliberately from a different boot domain.
        self.assertTrue(service.ingest_datagram(result(timestamp=1_000_000).encode()).accepted)
        self.assertEqual(service.response("/api/v5/latest")["links"]["1"]["state"], "PASSABLE")

    def test_lower_random_boot_recovers_and_retired_boot_is_rejected(self):
        ingest = EdgeResultV5Ingestor(identities={"1": {"device_id": "device-1"}}, boot_recovery_max_seq=4)
        self.assertTrue(ingest.ingest(result(boot=100, seq=1, timestamp=10_000).encode(), now_us=100).accepted)
        self.assertTrue(ingest.ingest(result(boot=1, seq=1, timestamp=1_500).encode(), now_us=200).accepted)
        delayed = ingest.ingest(result(boot=100, seq=2, timestamp=11_000).encode(), now_us=300)
        self.assertEqual(delayed.reason, "old_boot")
        self.assertEqual(ingest.links["1"].boot_id, 1)

    def test_unknown_links_do_not_allocate_state(self):
        ingest = EdgeResultV5Ingestor(identities={"1": {"device_id": "device-1"}})
        decision = ingest.ingest(result(link="forged", timestamp=2_000_000).encode(), now_us=2_000_000)
        self.assertFalse(decision.accepted)
        self.assertEqual(decision.reason, "identity:unknown_link")
        self.assertNotIn("forged", ingest.links)

    def test_topology_route_uses_string_link_ids_and_excludes_blocked(self):
        topology = {"protocol_version": 5, "links": [{"link_id": "link-1", "source": "A", "target": "B"}]}
        service = EdgeResultV5Service(
            identities={"link-1": {"device_id": "device-1"}},
            config=V5ServiceConfig(topology=topology, enable_reference_modules=True),
        )
        service.ingest_datagram(result(link="link-1").encode(), now_us=1_000_000)
        self.assertEqual(service.response("/api/v5/route", source="A", target="B", now_us=1_000_000)["route_state"], "READY")
        service2 = EdgeResultV5Service(
            identities={"link-1": {"device_id": "device-1"}},
            config=V5ServiceConfig(topology=topology, enable_reference_modules=True),
        )
        service2.ingest_datagram(result(link="link-1", state=EdgeResultState.BLOCKED).encode(), now_us=1_000_000)
        self.assertEqual(service2.response("/api/v5/route", source="A", target="B", now_us=1_000_000)["route_state"], "NO_ROUTE")

    def test_rejected_datagram_is_recorded_without_fabricated_result(self):
        with tempfile.TemporaryDirectory() as directory:
            service = EdgeResultV5Service(
                identities={"1": {"device_id": "device-1"}},
                config=V5ServiceConfig(recorder_directory=directory),
            )
            service.start_recording("rejects")
            decision = service.ingest_datagram(b"not-a-v5-packet", now_us=100)
            self.assertFalse(decision.accepted)
            final = service.stop_recording()
            rejected = Path(directory) / "_rejected.jsonl"
            self.assertTrue(rejected.exists())
            rows = [json.loads(line) for line in rejected.read_text(encoding="utf-8").splitlines()]
            self.assertEqual(rows[0]["record_type"], "edge_result_v5_rejection")
            self.assertFalse(rows[0]["accepted"])
            self.assertNotIn("datagram_hex", rows[0])
            self.assertEqual(len(rows[0]["packet_sha256"]), 64)

    def test_stale_trend_becomes_unknown(self):
        service = EdgeResultV5Service(
            identities={"1": {"device_id": "device-1"}},
            config=V5ServiceConfig(stale_after_ms=100, enable_reference_modules=True),
        )
        service.ingest_datagram(result(timestamp=10_000).encode(), now_us=1_000_000)
        trend = service.response("/api/v5/trends", now_us=1_200_000)["links"]["1"]
        self.assertEqual(trend["trend"], "UNKNOWN")

    def test_active_compact_service_unmounts_reference_modules(self):
        service = EdgeResultV5Service(identities={"1": {"device_id": "device-1"}})
        for path in ("/api/v5/trends", "/api/v5/route", "/api/v5/transport"):
            self.assertEqual(service.response(path)["error"], "reference_only_unmounted")

    def test_unknown_only_link_does_not_report_zero_packet_loss(self):
        ingest = EdgeResultV5Ingestor(identities={"1": {"device_id": "device-1"}})
        self.assertTrue(ingest.ingest(result(state=EdgeResultState.UNKNOWN).encode(), now_us=1_000_000).accepted)
        self.assertIsNone(ingest.quality_summary(now_us=1_000_000)["packet_loss_mean"])

    def test_loss_sources_are_not_combined_or_called_udp_loss(self):
        ingest = EdgeResultV5Ingestor(identities={"1": {"device_id": "device-1"}})
        value = EdgeResultV5(**{
            **result(link="1").__dict__,
            "sample_count": 10,
            "invalid_count": 1,
            "queue_drop_count": 3,
            "sequence_gap": 2,
            "packet_loss_ratio": 0.99,
        })
        self.assertTrue(ingest.ingest(value.encode(), now_us=1_000_000).accepted)
        link = ingest.latest_by_link(now_us=1_000_000)["1"]
        self.assertIsNone(link["packet_loss_ratio"])
        self.assertAlmostEqual(link["legacy_packet_loss_ratio"], 0.99, places=6)
        self.assertAlmostEqual(link["csi_gap_ratio"], 2 / 12)
        self.assertAlmostEqual(link["queue_drop_ratio"], 3 / 13)
        self.assertAlmostEqual(link["invalid_csi_ratio"], 1 / 10)
        self.assertIsNone(link["udp_loss_ratio"])
        self.assertEqual(link["udp_loss_status"], "UNVERIFIED_NO_RX_EMIT_COUNTER")
        summary = ingest.quality_summary(now_us=1_000_000)
        self.assertIsNone(summary["packet_loss_mean"])
        self.assertIsNone(summary["udp_loss_mean"])
        self.assertEqual(summary["pi_ingest"]["accepted"], 1)

    def test_pi_ingest_counters_are_explicit(self):
        service = EdgeResultV5Service(identities={"1": {"device_id": "device-1"}})
        packet = result(link="1").encode()
        self.assertTrue(service.ingest_datagram(packet, now_us=1_000_000).accepted)
        self.assertEqual(service.ingest_datagram(packet, now_us=1_000_100).reason, "duplicate")
        self.assertFalse(service.ingest_datagram(b"bad", now_us=1_000_200).accepted)
        pi = service.response("/api/v5/overview", now_us=1_000_200)["pi_ingest"]
        self.assertEqual(pi["received"], 3)
        self.assertEqual(pi["accepted"], 1)
        self.assertEqual(pi["rejected"], 2)
        self.assertEqual(pi["duplicate"], 1)
        self.assertEqual(pi["out_of_order"], 0)
        self.assertEqual(pi["decode_error"], 1)
        self.assertEqual(pi["last_packet_age_ms"], 0)

    def test_pi_window_gap_does_not_become_csi_or_udp_loss(self):
        ingest = EdgeResultV5Ingestor(identities={"1": {"device_id": "device-1"}})
        first = result(link="1", seq=1, timestamp=1_000_000)
        second = result(link="1", seq=3, timestamp=1_002_000)
        self.assertTrue(ingest.ingest(first.encode(), now_us=1_000_000).accepted)
        self.assertTrue(ingest.ingest(second.encode(), now_us=1_002_000).accepted)
        link = ingest.latest_by_link(now_us=1_002_000)["1"]
        self.assertEqual(link["csi_sequence_gap"], 0)
        self.assertEqual(link["sequence_gap"], 0)
        self.assertEqual(link["pi_window_sequence_gap"], 1)
        self.assertEqual(link["csi_gap_ratio"], 0.0)
        self.assertIsNone(link["udp_loss_ratio"])


if __name__ == "__main__":
    unittest.main()
