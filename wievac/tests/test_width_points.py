"""Operator width in meters, shown as points. 1000 points = 1 m."""

import json
import os
import unittest
from pathlib import Path
from unittest import mock

from wievac.pi.app.corridor_width import round_half_up
from wievac.pi.app.edge_result_v5 import EdgeResultState, EdgeResultV5
from wievac.pi.app.edge_result_v5_service import EdgeResultV5Service, V5ServiceConfig
from wievac.pi.app.guidance_sync import clear_map_cache
from wievac_guidance.pi_csi_bridge import compute_edge_values

ROOT = Path(__file__).resolve().parents[1]
ACTIVE = ROOT / "config" / "corridors" / "corridor-01-edge-result-v5.json"
RUNNER = ROOT / "scripts" / "run_pi_v5_compact.py"


class _FakeClient:
    def __init__(self, sink, fail=False):
        self.sink = sink
        self.fail = fail
        self.target = None

    def connect(self, host, port, keepalive=60):
        self.target = (host, port, keepalive)
        if self.fail:
            raise OSError("refused")

    def loop_start(self):
        return None

    def loop_stop(self):
        return None

    def disconnect(self):
        return None

    def publish(self, topic, payload, qos=0, retain=False):
        self.sink.append({
            "topic": topic,
            "payload": payload,
            "qos": qos,
            "retain": retain,
            "target": self.target,
        })
        return self

    def wait_for_publish(self, timeout=None):
        return None


class WidthPointTests(unittest.TestCase):
    def setUp(self):
        self._old_env = os.environ.get("WIEVAC_GUIDANCE_CONFIG")
        self.tmp = __import__("tempfile").TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.addCleanup(self._restore_env)
        os.environ["WIEVAC_GUIDANCE_CONFIG"] = str(Path(self.tmp.name) / "missing-map.json")
        clear_map_cache()
        self.published = []

    def _restore_env(self):
        if self._old_env is None:
            os.environ.pop("WIEVAC_GUIDANCE_CONFIG", None)
        else:
            os.environ["WIEVAC_GUIDANCE_CONFIG"] = self._old_env
        clear_map_cache()

    def _service(self, width=None):
        link = {
            "link_id": "link-1",
            "device_id": "device-1",
            "node_id": "rx-1",
            "tx_id": "tx-1",
            "rx_id": "rx-1",
            "corridor_id": "corridor-01",
            "display_name": "Hành lang 1",
            "nominal_distance_m": 5.0,
            "nominal_width_m": width,
        }
        topology = {"protocol_version": 5, "links": [link]}
        return EdgeResultV5Service(config=V5ServiceConfig(topology=topology))

    def _file_service(self):
        path = Path(self.tmp.name) / "topology.json"
        path.write_text(ACTIVE.read_text(encoding="utf-8"), encoding="utf-8")
        service = EdgeResultV5Service(config=V5ServiceConfig(topology_path=str(path)))
        return service, path

    def _row(self, service, **fields):
        payload = {"width_score": 100, "score": 100, "state": "PASSABLE"}
        payload.update(fields)
        return service._annotate_capacity({"links": {"link-1": payload}})["links"]["link-1"]

    def _use_map(self, document):
        path = Path(self.tmp.name) / "guidance.json"
        path.write_text(json.dumps(document), encoding="utf-8")
        os.environ["WIEVAC_GUIDANCE_CONFIG"] = str(path)
        clear_map_cache()
        return path

    def _mqtt(self, fail=False):
        sink = self.published
        return mock.patch(
            "wievac.pi.app.guidance_sync._mqtt_client",
            lambda: _FakeClient(sink, fail=fail),
        )

    def test_formula_and_half_up(self):
        self.assertEqual(round_half_up(0.5), 1)
        self.assertEqual(round_half_up(2.5), 3)
        service = self._service(3.0)
        row = self._row(service, width_score=85, score=40)
        self.assertEqual(row["width_points"], 2550)
        self.assertEqual(row["max_points"], 3000)
        self.assertEqual(row["flow_points"], 1200)
        half = self._service(1.0)
        self.assertEqual(self._row(half, width_score=0.05, score=0.05)["width_points"], 1)

    def test_wider_corridor_outranks_higher_percent(self):
        wide = self._service(10.0)
        narrow = self._service(1.0)
        empty = self._service(5.0)
        self.assertGreater(
            self._row(wide, width_score=50, score=50)["flow_points"],
            self._row(narrow, width_score=70, score=70)["flow_points"],
        )
        self.assertEqual(self._row(wide, width_score=50, score=50)["width_points"], 5000)
        self.assertEqual(self._row(narrow, width_score=70, score=70)["width_points"], 700)
        self.assertEqual(self._row(wide, width_score=100, score=100)["max_points"], 10000)
        self.assertEqual(self._row(empty, width_score=100, score=100)["max_points"], 5000)
        self.assertEqual(self._row(wide)["max_points"], 2 * self._row(empty)["max_points"])

    def test_missing_width_has_no_points_and_sample_topology_is_null(self):
        raw = json.loads(ACTIVE.read_text(encoding="utf-8"))
        for link in raw["links"]:
            self.assertIsNone(link["nominal_width_m"])
            self.assertIsNotNone(link["nominal_distance_m"])
        service = EdgeResultV5Service(config=V5ServiceConfig(topology_path=str(ACTIVE)))
        row = service.response("/api/v5/overview")["links"]["link-1"]
        self.assertIsNone(row["width_m"])
        self.assertIsNone(row["max_points"])
        self.assertIsNone(row["width_points"])
        self.assertIsNone(row["flow_points"])
        self.assertEqual(row["guidance_sync"]["state"], "NO_WIDTH")

    def test_patch_updates_overview_and_rejects_bad_width(self):
        service, path = self._file_service()
        before = path.read_bytes()
        for bad in (0, -1, 150, "abc", True):
            with self.assertRaises(ValueError) as raised:
                service.patch_node("link-1", {"nominal_width_m": bad})
            self.assertEqual(str(raised.exception), "invalid_width")
            self.assertEqual(path.read_bytes(), before)
            self.assertIsNone(service.catalog()["link-1"]["nominal_width_m"])
        patched = service.patch_node("link-1", {"nominal_width_m": 2.5})
        self.assertEqual(patched["node"]["nominal_width_m"], 2.5)
        packet = EdgeResultV5(
            device_id="device-1", node_id="rx-1", tx_id="tx-1", rx_id="rx-1", link_id="link-1",
            corridor_id="corridor-01", session_id="s", boot_id=1, window_seq=1,
            window_start_us=999_000, window_end_us=1_000_000, rx_timestamp_us=1_000_000,
            age_ms=0, local_passability_score=40.0, state=EdgeResultState.PASSABLE,
            quality=90.0, uncertainty=10.0, disagreement=False, reason_code=1,
            formula_version="formula-flex-v5.4-atten-exp", model_version="NOT_READY",
            model_hash="", feature_schema_version="8", sample_count=10, invalid_count=0,
            queue_drop_count=0, sequence_gap=0, packet_loss_ratio=0.0, jitter_ms=0.5,
            width_score=80.0,
        ).normalized().encode()
        self.assertTrue(service.ingest_datagram(packet, now_us=1_000_000).accepted)
        overview = service.response("/api/v5/overview", now_us=1_000_000)["links"]["link-1"]
        latest = service.response("/api/v5/latest", now_us=1_000_000)["links"]["link-1"]
        self.assertEqual(overview["width_m"], 2.5)
        self.assertEqual(overview["max_points"], 2500)
        self.assertEqual(overview["width_points"], 2000)
        self.assertEqual(overview["flow_points"], 1000)
        self.assertEqual(latest["flow_points"], 1000)
        self.assertIn("guidance_sync", latest)
        cleared = service.patch_node("link-1", {"nominal_width_m": None})
        self.assertIsNone(cleared["node"]["nominal_width_m"])
        self.assertEqual(cleared["guidance_sync"]["state"], "NO_WIDTH")
        saved = json.loads(path.read_text(encoding="utf-8"))
        row = next(item for item in saved["links"] if item["link_id"] == "link-1")
        self.assertIsNone(row["nominal_width_m"])
        self.assertIsNone(service.response("/api/v5/overview", now_us=1_000_000)["links"]["link-1"]["flow_points"])

    def test_sync_publishes_one_full_map(self):
        document = {
            "floor": "F1",
            "edges": [
                {"id": "e1", "csiLinkId": "link-1", "widthMeters": 1.2, "widthEstimated": True, "keep": 1},
                {"id": "e2", "csiLinkId": "link-9", "widthMeters": 9.0, "widthEstimated": True, "keep": 2},
            ],
        }
        path = self._use_map(document)
        before = path.read_bytes()
        service, _topology = self._file_service()
        with self._mqtt():
            result = service.patch_node("link-1", {"nominal_width_m": 2.5})
        self.assertEqual(len(self.published), 1)
        sent = self.published[0]
        self.assertEqual(sent["topic"], "building/config")
        self.assertEqual(sent["qos"], 1)
        self.assertFalse(sent["retain"])
        self.assertEqual(sent["target"][:2], ("127.0.0.1", 1883))
        payload = json.loads(sent["payload"])
        self.assertEqual(payload["floor"], "F1")
        self.assertEqual(payload["edges"][1], document["edges"][1])
        changed = payload["edges"][0]
        self.assertEqual(changed["widthMeters"], 2.5)
        self.assertFalse(changed["widthEstimated"])
        self.assertEqual(changed["keep"], 1)
        self.assertEqual(changed["id"], "e1")
        self.assertEqual(path.read_bytes(), before)
        self.assertEqual(result["guidance_sync"]["state"], "MISMATCH")
        path.write_text(json.dumps({
            "floor": "F1",
            "edges": [
                {"id": "e1", "csiLinkId": "link-1", "widthMeters": 2.5, "widthEstimated": True, "keep": 1},
                document["edges"][1],
            ],
        }), encoding="utf-8")
        os.utime(path, ns=(200_000_000_000, 200_000_000_000))
        synced = service.response("/api/v5/overview")["links"]["link-1"]["guidance_sync"]
        self.assertEqual(synced["state"], "SYNCED")
        self.assertEqual(synced["edges"], ["e1"])
        self.assertAlmostEqual(synced["map_width_m"], 2.5)

    def test_unmapped_link_does_not_publish(self):
        self._use_map({"edges": [{"id": "e9", "csiLinkId": "link-9", "widthMeters": 1.2, "widthEstimated": True}]})
        service, _topology = self._file_service()
        with self._mqtt():
            result = service.patch_node("link-1", {"nominal_width_m": 2.5})
        self.assertEqual(self.published, [])
        self.assertEqual(result["guidance_sync"]["state"], "NOT_MAPPED")

    def test_multi_link_uses_min_and_flags_conflict(self):
        self._use_map({
            "floor": "F1",
            "edges": [{
                "id": "e1",
                "csiLinkId": "link-1, link-2",
                "widthMeters": 1.2,
                "widthEstimated": True,
                "keep": "yes",
            }],
        })
        service, _topology = self._file_service()
        with self._mqtt():
            service.patch_node("link-2", {"nominal_width_m": 2.0})
            result = service.patch_node("link-1", {"nominal_width_m": 4.0})
        self.assertEqual(len(self.published), 2)
        payload = json.loads(self.published[-1]["payload"])
        edge = payload["edges"][0]
        self.assertEqual(edge["widthMeters"], 2.0)
        self.assertFalse(edge["widthEstimated"])
        self.assertEqual(edge["keep"], "yes")
        self.assertEqual(payload["floor"], "F1")
        self.assertTrue(result["guidance_sync"]["multi_link_width_conflict"])

    def test_mqtt_failure_keeps_width_and_does_not_write_map(self):
        path = self._use_map({
            "edges": [{"id": "e1", "csiLinkId": "link-1", "widthMeters": 1.2, "widthEstimated": True}],
        })
        before = path.read_bytes()
        service, topology = self._file_service()
        with self._mqtt(fail=True):
            result = service.patch_node("link-1", {"nominal_width_m": 2.5})
        self.assertEqual(result["node"]["nominal_width_m"], 2.5)
        self.assertEqual(result["guidance_sync"]["state"], "MISMATCH")
        self.assertEqual(result["guidance_sync"]["reason"], "mqtt_unavailable")
        self.assertEqual(path.read_bytes(), before)
        saved = json.loads(topology.read_text(encoding="utf-8"))
        row = next(item for item in saved["links"] if item["link_id"] == "link-1")
        self.assertEqual(row["nominal_width_m"], 2.5)
        self.assertEqual(self.published, [])

    def test_bad_or_missing_map_does_not_raise(self):
        service, topology = self._file_service()
        missing = service.patch_node("link-1", {"nominal_width_m": 2.5})
        self.assertEqual(missing["guidance_sync"]["state"], "MAP_UNAVAILABLE")
        self.assertEqual(missing["node"]["nominal_width_m"], 2.5)
        broken = Path(self.tmp.name) / "broken.json"
        broken.write_text("{", encoding="utf-8")
        os.environ["WIEVAC_GUIDANCE_CONFIG"] = str(broken)
        clear_map_cache()
        again = service.patch_node("link-1", {"nominal_width_m": 3.0})
        self.assertEqual(again["guidance_sync"]["state"], "MAP_UNAVAILABLE")
        saved = json.loads(topology.read_text(encoding="utf-8"))
        row = next(item for item in saved["links"] if item["link_id"] == "link-1")
        self.assertEqual(row["nominal_width_m"], 3.0)

    def test_invalid_width_score_and_unknown_or_stale(self):
        service = self._service(3.0)
        old = self._row(service, width_score=28674, score=40)
        self.assertIsNone(old["width_points"])
        self.assertEqual(old["flow_points"], 1200)
        unknown = self._row(service, width_score=85, score=40, state="UNKNOWN")
        self.assertEqual(unknown["width_points"], 2550)
        self.assertIsNone(unknown["flow_points"])
        stale = self._row(service, width_score=85, score=40, state="STALE")
        self.assertIsNone(stale["width_points"])
        self.assertIsNone(stale["flow_points"])
        self.assertIsNone(stale["max_points"])

    def test_points_match_guidance_k(self):
        service = self._service(3.0)
        row = self._row(service, width_score=100, score=40, state="PASSABLE")
        values = compute_edge_values(
            {"link-1": {"score": 40, "state": "PASSABLE"}},
            {"e1": ["link-1"]},
        )
        k = values["e1"]
        self.assertAlmostEqual(row["flow_points"] / 1000, 3.0 * (1 - k), delta=0.001)
        both = compute_edge_values(
            {
                "link-1": {"score": 80, "state": "PASSABLE"},
                "link-2": {"score": 40, "state": "DEGRADED"},
            },
            {"e1": ["link-1", "link-2"]},
        )
        self.assertEqual(both["e1"], 0.6)
        blocked = compute_edge_values(
            {"link-1": {"score": 90, "state": "BLOCKED"}},
            {"e1": ["link-1"]},
        )
        self.assertEqual(blocked["e1"], 1.0)

    def test_sync_route_is_wired(self):
        source = RUNNER.read_text(encoding="utf-8")
        self.assertIn("/sync-width", source)
        self.assertIn("sync_node_width", source)


if __name__ == "__main__":
    unittest.main()
