from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from scripts.export_dataset import export_jsonl


class DatasetExportTests(unittest.TestCase):
    def test_preserves_records_and_requires_operator_occupancy_label(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / "capture.jsonl"
            output = root / "dataset.jsonl"
            source.write_text(json.dumps({
                "record_type": "window", "session_id": "s1",
                "window_classification": "interference", "window_reason": "rf_shift",
                "raw": {"keep": True},
            }) + "\n", encoding="utf-8")
            self.assertEqual(export_jsonl(source, output), 1)
            record = json.loads(output.read_text(encoding="utf-8"))
            self.assertEqual(record["occupancy_label"], None)
            self.assertEqual(record["nuisance_label"], "rf_interference")
            self.assertFalse(record["training_eligible"])
            self.assertEqual(record["raw"], {"keep": True})

    def test_sidecar_labels_are_explicit_and_invalid_labels_fail(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / "capture.jsonl"
            source.write_text(json.dumps({"session_id": "s1", "value": 1}) + "\n", encoding="utf-8")
            output = root / "dataset.jsonl"
            self.assertEqual(export_jsonl(source, output, {
                "s1": {"occupancy_label": "empty", "nuisance_label": "none"},
            }), 1)
            record = json.loads(output.read_text(encoding="utf-8"))
            self.assertEqual(record["occupancy_label"], "empty")
            self.assertEqual(record["nuisance_label"], "none")
            self.assertTrue(record["training_eligible"])
            with self.assertRaises(ValueError):
                export_jsonl(source, output, {"s1": {"occupancy_label": "person"}})

    def test_interval_labels_join_by_arrival_timestamp(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / "capture.jsonl"
            output = root / "dataset.jsonl"
            source.write_text("\n".join([
                json.dumps({"session_id": "fw", "link_id": "link-1", "arrival_timestamp_us": 150, "value": 1}),
                json.dumps({"session_id": "fw", "link_id": "link-1", "arrival_timestamp_us": 250, "value": 2}),
                json.dumps({"session_id": "fw", "link_id": "link-2", "arrival_timestamp_us": 180, "value": 3}),
            ]) + "\n", encoding="utf-8")
            self.assertEqual(export_jsonl(source, output, {
                "intervals": [
                    {"record_type": "occupancy_interval", "link_id": "link-1", "occupancy": "EMPTY", "t_start_us": 100, "t_end_us": 200},
                    {"record_type": "occupancy_interval", "link_id": "link-1", "occupancy": "HUMAN_PRESENT", "t_start_us": 200, "t_end_us": None},
                ]
            }), 3)
            rows = [json.loads(line) for line in output.read_text(encoding="utf-8").splitlines()]
            self.assertEqual(rows[0]["occupancy_label"], "empty")
            self.assertEqual(rows[0]["label_source"], "operator_dashboard")
            self.assertFalse(rows[0]["training_eligible"])
            self.assertEqual(rows[1]["occupancy_label"], "person_present")
            self.assertIsNone(rows[2]["occupancy_label"])
            self.assertEqual(rows[2]["label_source"], "unlabeled")


if __name__ == "__main__":
    unittest.main()
