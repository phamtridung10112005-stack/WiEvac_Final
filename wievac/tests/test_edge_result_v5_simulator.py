"""Software-only EdgeResult V5 transport simulator coverage."""

from __future__ import annotations

import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from simulation.edge_result_v5_simulator import (  # noqa: E402
    EdgeResultSample,
    EdgeResultV5Simulator,
    run_default_load,
)


class EdgeResultV5SimulatorTests(unittest.TestCase):
    def test_default_load_has_24_independent_links(self) -> None:
        report = run_default_load(steps=20, seed=11)
        self.assertEqual(report["node_count"], 24)
        self.assertEqual(report["totals"]["generated"], 24 * 20)
        self.assertEqual(len(report["links"]), 24)
        self.assertTrue(all(item["link_id"] == int(key) for key, item in report["links"].items()))

    def test_bounded_queue_counts_drops_without_blocking_other_links(self) -> None:
        simulator = EdgeResultV5Simulator(node_count=24, queue_capacity=1, process_budget=0)
        report = simulator.run(steps=6, flush=False)
        self.assertGreater(report["totals"]["queue_drops"], 0)
        self.assertEqual(report["totals"]["accepted"], 0)
        self.assertTrue(all(item["max_queue_depth"] <= 1 for item in report["links"].values()))

    def test_loss_on_one_link_does_not_remove_surviving_link(self) -> None:
        simulator = EdgeResultV5Simulator(node_count=24, queue_capacity=16, process_budget=4)
        simulator.set_link_fault(1, loss_probability=1.0)
        report = simulator.run(steps=8)
        lost = report["links"]["1"]
        healthy = report["links"]["2"]
        self.assertEqual(lost["network_lost"], 8)
        self.assertEqual(lost["accepted"], 0)
        self.assertEqual(healthy["accepted"], 8)
        self.assertIsNotNone(healthy["latest"])

    def test_duplicate_and_reorder_are_rejected_per_link(self) -> None:
        simulator = EdgeResultV5Simulator(node_count=24, queue_capacity=64, process_budget=64)
        simulator.set_link_fault(1, duplicate_probability=1.0, reorder_probability=1.0)
        report = simulator.run(steps=10)
        link = report["links"]["1"]
        self.assertGreater(link["duplicates"], 0)
        self.assertGreater(link["out_of_order"], 0)
        self.assertIn("duplicate", link["rejection_reasons"])
        self.assertIn("out_of_order", link["rejection_reasons"])
        self.assertEqual(report["links"]["2"]["duplicates"], 0)

    def test_runs_are_deterministic_for_same_seed(self) -> None:
        first = run_default_load(steps=12, seed=123)
        second = run_default_load(steps=12, seed=123)
        self.assertEqual(first, second)

    def test_unknown_score_is_null_and_invalid_sample_is_rejected(self) -> None:
        simulator = EdgeResultV5Simulator(node_count=24, queue_capacity=4, process_budget=1)
        unknown = EdgeResultSample(
            node_id=1,
            link_id=1,
            tx_id=1,
            rx_id=1,
            corridor_id="corridor-sim",
            boot_id=1,
            window_seq=1,
            window_start_us=1,
            window_end_us=100_000,
            score=None,
            state="UNKNOWN",
            quality=0.2,
            uncertainty=1.0,
        )
        self.assertTrue(simulator.submit(unknown))
        simulator.flush()
        self.assertIsNone(simulator.report()["links"]["1"]["latest"]["score"])

        invalid = EdgeResultSample(
            **{**unknown.__dict__, "window_seq": 2, "state": "UNKNOWN", "score": 0.0}
        )
        with self.assertRaisesRegex(ValueError, "UNKNOWN"):
            simulator.submit(invalid)

    def test_v5_quality_scale_and_timestamps_are_bounded(self) -> None:
        report = EdgeResultV5Simulator(node_count=1, process_budget=1).run(3)
        latest = report["links"]["1"]["latest"]
        self.assertGreaterEqual(latest["quality"], 0.0)
        self.assertLessEqual(latest["quality"], 100.0)
        self.assertGreaterEqual(latest["rx_timestamp_us"], latest["window_end_us"])

    def test_sequence_history_does_not_grow_without_bound(self) -> None:
        from simulation.edge_result_v5_simulator import SEQUENCE_HISTORY_LIMIT

        simulator = EdgeResultV5Simulator(node_count=1, process_budget=1)
        simulator.run(SEQUENCE_HISTORY_LIMIT * 3)
        runtime = simulator._links[1]
        self.assertLessEqual(len(runtime.seen_sequences), SEQUENCE_HISTORY_LIMIT)
        self.assertLessEqual(len(runtime.recent_sequences), SEQUENCE_HISTORY_LIMIT)


if __name__ == "__main__":
    unittest.main()
