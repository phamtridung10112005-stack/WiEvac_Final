import json
import tempfile
import time
import unittest
from datetime import datetime, timezone
from pathlib import Path

from wievac.pi.app.edge_result_v5_service import EdgeResultV5Service, V5ServiceConfig


ROOT = Path(__file__).resolve().parents[1]
ACTIVE = ROOT / "config" / "corridors" / "corridor-01-edge-result-v5.json"


class EdgeResultV5NodeCatalogTests(unittest.TestCase):
    def test_active_topology_exposes_display_catalog(self):
        service = EdgeResultV5Service(config=V5ServiceConfig(topology_path=str(ACTIVE)))
        overview = service.response("/api/v5/overview")
        catalog = overview["catalog"]
        self.assertEqual(set(catalog), {"link-1", "link-2"})
        self.assertEqual(catalog["link-1"]["display_name"], "Hành lang 1")
        self.assertEqual(catalog["link-2"]["display_name"], "Hành lang 2")
        self.assertTrue(catalog["link-1"]["enabled"])
        self.assertEqual(catalog["link-1"]["sort_order"], 1)
        self.assertTrue(catalog["link-1"]["firmware_supported"])

    def test_create_patch_delete_node_persists_and_hot_reloads(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "topology.json"
            path.write_text(ACTIVE.read_text(encoding="utf-8"), encoding="utf-8")
            service = EdgeResultV5Service(config=V5ServiceConfig(topology_path=str(path)))
            created = service.create_node({
                "link_id": "link-3",
                "rx_id": "rx-3",
                "display_name": "Hành lang 3",
            })
            self.assertEqual(created["node"]["display_name"], "Hành lang 3")
            self.assertTrue(created["node"]["firmware_supported"])
            self.assertIn("link-3", service.ingest.identities)
            overview = service.response("/api/v5/overview")
            self.assertIn("link-3", overview["catalog"])
            self.assertIn("link-3", overview["links"])
            self.assertEqual(overview["catalog"]["link-3"]["display_name"], "Hành lang 3")
            patched = service.patch_node("link-3", {"display_name": "Sảnh 3", "enabled": False})
            self.assertEqual(patched["node"]["display_name"], "Sảnh 3")
            self.assertFalse(patched["node"]["enabled"])
            listed = service.list_nodes()
            names = {row["link_id"]: row["display_name"] for row in listed["nodes"]}
            self.assertEqual(names["link-3"], "Sảnh 3")
            saved = json.loads(path.read_text(encoding="utf-8"))
            self.assertEqual(saved["links"][-1]["display_name"], "Sảnh 3")
            deleted = service.delete_node("link-3")
            self.assertEqual(deleted["deleted"], "link-3")
            self.assertNotIn("link-3", service.ingest.identities)
            self.assertNotIn("link-3", service.catalog())
            remaining = [item["link_id"] for item in json.loads(path.read_text(encoding="utf-8"))["links"]]
            self.assertEqual(remaining, ["link-1", "link-2"])

    def test_duplicate_link_is_rejected(self):
        service = EdgeResultV5Service(config=V5ServiceConfig(topology_path=str(ACTIVE)))
        with self.assertRaises(ValueError) as raised:
            service.create_node({"link_id": "link-1", "rx_id": "rx-9", "display_name": "Trùng"})
        self.assertEqual(str(raised.exception), "duplicate_link_id")

    def test_missing_fields_are_rejected(self):
        service = EdgeResultV5Service(config=V5ServiceConfig(topology_path=str(ACTIVE)))
        with self.assertRaises(ValueError) as raised:
            service.create_node({"link_id": "link-9", "rx_id": "rx-9"})
        self.assertEqual(str(raised.exception), "missing_fields")

    def test_occupancy_interval_is_written_and_shown_on_overview(self):
        with tempfile.TemporaryDirectory() as directory:
            labels = Path(directory) / "labels"
            service = EdgeResultV5Service(config=V5ServiceConfig(
                topology_path=str(ACTIVE),
                label_directory=str(labels),
            ))
            service.start_recording("empty-night")
            labeled = service.set_occupancy_label("link-1", "EMPTY", now_us=1000)
            self.assertEqual(labeled["label"]["occupancy"], "EMPTY")
            overview = service.response("/api/v5/overview")
            self.assertEqual(overview["active_labels"]["link-1"]["occupancy"], "EMPTY")
            self.assertEqual(overview["recording"]["state"], "RECORDING")
            switched = service.set_occupancy_label("link-1", "HUMAN_PRESENT", now_us=2000)
            self.assertEqual(switched["active_labels"]["link-1"]["occupancy"], "HUMAN_PRESENT")
            service.stop_recording()
            rows = [json.loads(line) for line in (labels / "link-1.jsonl").read_text(encoding="utf-8").splitlines()]
            self.assertEqual(rows[0]["occupancy"], "EMPTY")
            self.assertTrue(rows[0]["open"])
            self.assertEqual(rows[1]["occupancy"], "EMPTY")
            self.assertFalse(rows[1]["open"])
            self.assertEqual(rows[1]["t_end_us"], 2000)
            self.assertEqual(rows[-1]["occupancy"], "HUMAN_PRESENT")
            self.assertTrue(rows[-1]["open"])

    def test_recording_timer_stops_without_operator_click(self):
        service = EdgeResultV5Service(identities={"1": {"device_id": "device-1"}})
        service.start_recording("timed", duration_hours=1)
        self.assertEqual(service.recording_status()["state"], "RECORDING")
        service._record_until_mono = time.monotonic() - 1
        self.assertTrue(service.expire_recording())
        self.assertEqual(service.recording_status()["state"], "IDLE")

    def test_schedule_forces_empty_occupancy_for_window(self):
        with tempfile.TemporaryDirectory() as directory:
            labels = Path(directory) / "labels"
            compact = Path(directory) / "compact"
            schedule_path = Path(directory) / "capture-schedules.json"
            service = EdgeResultV5Service(config=V5ServiceConfig(
                topology_path=str(ACTIVE),
                label_directory=str(labels),
                recorder_directory=str(compact),
                schedule_path=str(schedule_path),
            ))
            now = time.time()
            start = datetime.fromtimestamp(now - 30, tz=timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
            end = datetime.fromtimestamp(now + 3600, tz=timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
            created = service.control_recording({
                "action": "schedule",
                "start_at": start,
                "end_at": end,
                "occupancy": "EMPTY",
            })
            self.assertEqual(created["state"], "RECORDING")
            self.assertEqual(created["schedule"]["occupancy"], "EMPTY")
            self.assertEqual(created["schedule"]["phase"], "active")
            self.assertEqual(service.active_labels()["link-1"]["occupancy"], "EMPTY")
            self.assertEqual(service.active_labels()["link-2"]["occupancy"], "EMPTY")
            self.assertEqual(service.active_labels()["link-1"]["label_source"], "scheduled_capture")
            saved = json.loads(schedule_path.read_text(encoding="utf-8"))
            self.assertEqual(saved[0]["occupancy"], "EMPTY")
            service.apply_schedules(now_s=now + 3601)
            self.assertEqual(service.session_id, None)
            self.assertEqual(service.active_labels(), {})
            status = service.recording_status()
            self.assertEqual(status["state"], "IDLE")
            self.assertEqual(status["schedules"][0]["phase"], "done")

    def test_future_schedule_stays_idle_until_window(self):
        with tempfile.TemporaryDirectory() as directory:
            service = EdgeResultV5Service(config=V5ServiceConfig(
                topology_path=str(ACTIVE),
                label_directory=str(Path(directory) / "labels"),
                recorder_directory=str(Path(directory) / "compact"),
                schedule_path=str(Path(directory) / "capture-schedules.json"),
            ))
            now = time.time()
            start = datetime.fromtimestamp(now + 3600, tz=timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
            end = datetime.fromtimestamp(now + 7200, tz=timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
            created = service.control_recording({
                "action": "schedule",
                "start_at": start,
                "end_at": end,
                "occupancy": "EMPTY",
            })
            self.assertEqual(created["state"], "IDLE")
            self.assertEqual(created["schedule"]["phase"], "pending")
            self.assertEqual(created["schedules"][0]["occupancy"], "EMPTY")


if __name__ == "__main__":
    unittest.main()
