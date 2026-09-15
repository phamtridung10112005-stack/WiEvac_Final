import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))

from report_metrics import summarize_records  # noqa: E402


class ReportMetricsTests(unittest.TestCase):
    def test_summary_keeps_sessions_and_links_separate(self) -> None:
        records = [
            {"record_type": "dynamic_frame", "session_id": "ignored", "link_id": 1},
            {"record_type": "analysis_metric", "session_id": "s1", "link_id": 1,
             "dynamic_received_rate_hz": 20.0, "dynamic_valid_transport_rate_hz": 19.0,
             "feature_received_rate_hz": 5.0, "features": {"motion_median": 1.0},
             "quality": {"quality_score": 0.9, "packet_loss": 1, "sequence_gap": 1, "invalid_csi": 2},
             "decision": {"link_state": "EMPTY", "baseline_eligible": True}},
            {"record_type": "analysis_metric", "session_id": "s1", "link_id": 2,
             "dynamic_received_rate_hz": 25.0, "features": {"motion_median": 3.0},
             "quality": {"quality_score": 0.7, "packet_loss": 4, "sequence_gap": 2, "invalid_csi": 1},
             "decision": {"link_state": "SIGNAL_INVALID", "baseline_eligible": False}},
        ]
        summary = summarize_records(records)
        self.assertEqual(set(summary["sessions"]), {"s1"})
        self.assertEqual(set(summary["sessions"]["s1"]), {"1", "2"})
        self.assertEqual(summary["sessions"]["s1"]["1"]["record_count"], 1)
        self.assertEqual(summary["sessions"]["s1"]["2"]["dynamic_received_rate_hz"]["median"], 25.0)
        self.assertEqual(summary["sessions"]["s1"]["1"]["feature_rate_hz"]["median"], 5.0)
        self.assertEqual(summary["sessions"]["s1"]["1"]["dynamic_valid_transport_rate_hz"]["median"], 19.0)
        self.assertEqual(summary["sessions"]["s1"]["1"]["packet_loss"], 1)

    def test_null_and_nonfinite_values_are_not_converted_to_zero(self) -> None:
        summary = summarize_records([{
            "record_type": "analysis_metric", "session_id": "s1", "link_id": 1,
            "dynamic_received_rate_hz": None,
            "features": {"motion_median": None},
            "quality": {"quality_score": float("nan")},
            "decision": {"link_state": "WARMING_UP", "baseline_eligible": False},
        }])
        link = summary["sessions"]["s1"]["1"]
        self.assertIsNone(link["dynamic_received_rate_hz"]["median"])
        self.assertIsNone(link["motion_median"]["median"])
        self.assertIsNone(link["quality_score"]["median"])


if __name__ == "__main__":
    unittest.main()
