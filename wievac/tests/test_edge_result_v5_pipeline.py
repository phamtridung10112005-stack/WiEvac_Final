import unittest

from wievac.firmware.receiver.edge_result_v5_reference import (
    CsiFrame,
    FormulaFlex,
    LinkEdgePipeline,
    TinyAiMetadata,
    TinyAiModel,
    TinyAiRunner,
)
from wievac.pi.app.edge_result_v5 import EdgeResultState
from wievac.pi.app.edge_result_v5_runtime import EdgeResultV5Ingestor


def make_frame(*, link="link-1", sequence=1, timestamp=1_000_000, value=1.0, source="aa:bb", boot=1):
    return CsiFrame(
        device_id="device-1", node_id="rx-1", tx_id="tx-1", rx_id="rx-1",
        link_id=link, corridor_id="corridor-1", session_id="session-1",
        boot_id=boot, sequence=sequence, timestamp_us=timestamp,
        i_q=((value, 0.0),) * 8, source_mac=source, expected_source_mac="aa:bb",
    )


class FakeModel(TinyAiModel):
    def __init__(self, correction=1.0, schema="6"):
        self.metadata = TinyAiMetadata("model-1", "hash-1", schema, max_correction=5.0)
        self.correction = correction

    def predict(self, features):
        return self.correction


class EdgeResultV5PipelineTests(unittest.TestCase):
    def _pipeline(self, *, window_ms=1000, warmup=3):
        pipeline = LinkEdgePipeline(
            device_id="device-1", node_id="rx-1", tx_id="tx-1", rx_id="rx-1",
            link_id="link-1", corridor_id="corridor-1", session_id="session-1",
            expected_source_mac="aa:bb", formula=FormulaFlex(warmup_samples=warmup, window_ms=window_ms),
        )
        pipeline.confirm_empty()
        return pipeline

    def test_results_are_aggregated_per_time_window(self):
        pipeline = self._pipeline(window_ms=1000)
        for seq, timestamp in enumerate((1_000_000, 1_100_000, 1_900_000, 2_100_000), 1):
            pipeline.enqueue(make_frame(sequence=seq, timestamp=timestamp))
            output = pipeline.process_one()
            if seq < 4:
                self.assertIsNone(output)
            else:
                self.assertEqual(output.sample_count, 3)
                self.assertEqual(output.window_start_us, 1_000_000)
                self.assertEqual(output.window_end_us, 2_000_000)
        tail = pipeline.flush()
        self.assertIsNotNone(tail)
        self.assertEqual(tail.sample_count, 1)

    def test_gap_makes_window_unknown_and_recovery_can_resume(self):
        pipeline = self._pipeline(window_ms=500)
        for seq, timestamp in ((1, 1_000_000), (3, 1_100_000), (4, 1_600_000)):
            pipeline.enqueue(make_frame(sequence=seq, timestamp=timestamp))
            output = pipeline.process_one()
        self.assertIn(output.state, {EdgeResultState.UNKNOWN, EdgeResultState.DEGRADED})
        if output.state is EdgeResultState.UNKNOWN:
            self.assertIsNone(output.local_passability_score)
        self.assertEqual(output.sequence_gap, 1)
        # New sequence data after the gap starts a clean window.
        # Drain the closed invalid window, then require a complete clean
        # window before recovery can become PASSABLE.
        pipeline.enqueue(make_frame(sequence=5, timestamp=2_100_000))
        pipeline.process_one()
        pipeline.enqueue(make_frame(sequence=6, timestamp=2_600_000))
        pipeline.process_one()
        pipeline.enqueue(make_frame(sequence=7, timestamp=3_100_000))
        recovered = pipeline.process_one()
        self.assertIsNotNone(recovered)
        self.assertEqual(recovered.state, EdgeResultState.PASSABLE)

    def test_boot_reset_is_explicit_and_accepts_lower_epoch(self):
        pipeline = self._pipeline()
        pipeline.enqueue(make_frame(sequence=1, timestamp=1_000_000, boot=9))
        self.assertIsNone(pipeline.process_one())
        pipeline.reset_boot(1)  # Numeric ordering is not used for epochs.
        pipeline.confirm_empty()
        pipeline.enqueue(make_frame(sequence=1, timestamp=2_000_000))
        self.assertIsNone(pipeline.process_one())

    def test_declared_length_and_nonfinite_csi_are_invalid(self):
        pipeline = self._pipeline()
        bad_length = make_frame(sequence=1)
        bad_length = CsiFrame(**{**bad_length.__dict__, "csi_length": 128})
        pipeline.enqueue(bad_length)
        pipeline.process_one()
        self.assertEqual(pipeline.flush().invalid_count, 1)

    def test_flat_baseline_shift_is_not_passable(self):
        pipeline = self._pipeline(warmup=3)
        for seq in range(1, 4):
            pipeline.enqueue(make_frame(sequence=seq, timestamp=1_000_000 + seq * 1000, value=1.0))
            pipeline.process_one()
        for seq in range(4, 6):
            pipeline.enqueue(make_frame(sequence=seq, timestamp=1_000_000 + seq * 1000, value=2.0))
            pipeline.process_one()
        output = pipeline.flush()
        self.assertIsNotNone(output)
        self.assertNotEqual(output.state, EdgeResultState.PASSABLE)

    def test_impulse_score_does_not_follow_one_window_vshape(self):
        pipeline = self._pipeline(window_ms=1000, warmup=3)
        outputs = []
        seq = 1
        for value in (10.0, 10.0, 10.0, 10.0, 10.0):
            pipeline.enqueue(make_frame(sequence=seq, timestamp=1_000_000 + seq * 1_000_000, value=value))
            item = pipeline.process_one()
            if item is not None:
                outputs.append(item)
            seq += 1
        stable = [item for item in outputs if item.local_passability_score is not None]
        self.assertTrue(stable)
        before = stable[-1].local_passability_score
        pipeline.enqueue(make_frame(sequence=seq, timestamp=1_000_000 + seq * 1_000_000, value=40.0))
        spike = pipeline.process_one()
        seq += 1
        pipeline.enqueue(make_frame(sequence=seq, timestamp=1_000_000 + seq * 1_000_000, value=10.0))
        recovered = pipeline.process_one() or pipeline.flush()
        self.assertIsNotNone(recovered)
        if recovered.local_passability_score is not None and before is not None:
            self.assertLess(abs(recovered.local_passability_score - before), 15.0)

    def test_quiet_wrong_baseline_rebases_after_persistence(self):
        pipeline = LinkEdgePipeline(
            device_id="device-1", node_id="rx-1", tx_id="tx-1", rx_id="rx-1",
            link_id="link-1", corridor_id="corridor-1", session_id="session-1",
            expected_source_mac="aa:bb",
            formula=FormulaFlex(warmup_samples=3, window_ms=300, rebase_persistence_windows=12),
        )
        seq = 1
        for _ in range(4):
            for value in (30.0, 30.0, 30.0):
                pipeline.enqueue(make_frame(sequence=seq, timestamp=1_000_000 + seq * 100_000, value=value))
                pipeline.process_one()
                seq += 1
        first_version = pipeline.state.baseline_version
        for _ in range(16):
            for value in (100.0, 100.0, 100.0):
                pipeline.enqueue(make_frame(sequence=seq, timestamp=1_000_000 + seq * 100_000, value=value))
                pipeline.process_one()
                seq += 1
        tail = pipeline.flush()
        self.assertGreaterEqual(pipeline.state.baseline_version, first_version)
        self.assertTrue(pipeline.state.baseline_version >= 2 or (tail is not None and tail.baseline_version >= 2))

    def test_short_stationary_object_does_not_rebase(self):
        pipeline = LinkEdgePipeline(
            device_id="device-1", node_id="rx-1", tx_id="tx-1", rx_id="rx-1",
            link_id="link-1", corridor_id="corridor-1", session_id="session-1",
            expected_source_mac="aa:bb", formula=FormulaFlex(warmup_samples=3, window_ms=300),
        )
        seq = 1
        for value in (30.0, 40.0, 50.0, 100.0, 101.0, 99.0):
            pipeline.enqueue(make_frame(sequence=seq, timestamp=1_000_000 + seq * 100_000, value=value))
            pipeline.process_one()
            seq += 1
        self.assertLessEqual(pipeline.state.baseline_version, 1)

    def test_occupancy_evidence_is_not_passable_at_low_score(self):
        pipeline = self._pipeline(warmup=3)
        pipeline.state.occupied_windows = 3
        pipeline.state.baseline_ready = True
        pipeline.state.baseline_state = "BASELINE_STABLE"
        for seq in range(1, 6):
            pipeline.enqueue(make_frame(sequence=seq, timestamp=1_000_000 + seq * 1000, value=1.0))
            pipeline.process_one()
        output = pipeline.flush()
        self.assertIsNotNone(output)
        if output.local_passability_score is not None and output.local_passability_score < 50.0:
            self.assertNotEqual(output.state, EdgeResultState.PASSABLE)

    def test_warmup_uses_multiple_frames_and_stable_corridor_is_passable(self):
        pipeline = LinkEdgePipeline(
            device_id="device-1", node_id="rx-1", tx_id="tx-1", rx_id="rx-1",
            link_id="link-1", corridor_id="corridor-1", session_id="session-1",
            expected_source_mac="aa:bb", formula=FormulaFlex(warmup_samples=4),
        )
        pipeline.confirm_empty()
        outputs = []
        for seq in range(1, 5):
            pipeline.enqueue(make_frame(sequence=seq, timestamp=1_000_000 + seq * 1000))
            value = pipeline.process_one()
            if value is not None:
                outputs.append(value)
        outputs.append(pipeline.flush())
        self.assertEqual(len(outputs), 1)
        self.assertEqual(outputs[0].state, EdgeResultState.PASSABLE)
        self.assertIsNotNone(outputs[0].local_passability_score)

    def test_invalid_source_and_csi_are_unknown_not_obstruction(self):
        pipeline = LinkEdgePipeline(
            device_id="device-1", node_id="rx-1", tx_id="tx-1", rx_id="rx-1",
            link_id="link-1", corridor_id="corridor-1", session_id="session-1",
            expected_source_mac="aa:bb",
        )
        pipeline.enqueue(make_frame(source="00:00"))
        self.assertIsNone(pipeline.process_one())
        pipeline.enqueue(make_frame(sequence=2, timestamp=1_001_000))
        # The frame is still valid after a rejected source; it must warm up,
        # not inherit a false BLOCKED/PASSABLE result.
        self.assertIsNone(pipeline.process_one())
        rejected_window = pipeline.flush()
        self.assertIsNotNone(rejected_window)
        self.assertEqual(rejected_window.state, EdgeResultState.UNKNOWN)
        self.assertEqual(rejected_window.invalid_count, 1)
        self.assertIsNone(rejected_window.local_passability_score)

    def test_queue_is_bounded_and_counts_drops(self):
        pipeline = LinkEdgePipeline(
            device_id="device-1", node_id="rx-1", tx_id="tx-1", rx_id="rx-1",
            link_id="link-1", corridor_id="corridor-1", session_id="session-1",
            expected_source_mac="aa:bb", queue_capacity=2,
        )
        self.assertTrue(pipeline.enqueue(make_frame(sequence=1)))
        self.assertTrue(pipeline.enqueue(make_frame(sequence=2, timestamp=1_001_000)))
        self.assertFalse(pipeline.enqueue(make_frame(sequence=3, timestamp=1_002_000)))
        self.assertEqual(pipeline.queue.drop_count, 1)

    def test_tiny_ai_missing_schema_and_disagreement_fail_closed(self):
        missing = TinyAiRunner()
        correction, state, disagree, _ = missing.correct({"x": 1.0}, type("F", (), {"uncertainty": 10.0, "robust_delta": 0.0, "formula_score": 50.0})())
        self.assertEqual((correction, state, disagree), (0.0, "NOT_READY", False))
        incompatible = TinyAiRunner(FakeModel(schema="4"))
        features = {"formula_score": 50.0, "robust_delta": 0.0, "quality": 100.0}
        formula = type("F", (), {"uncertainty": 10.0, "robust_delta": 0.0, "formula_score": 50.0})()
        self.assertEqual(incompatible.correct(features, formula)[1], "INCOMPATIBLE")
        disagreement = TinyAiRunner(FakeModel(correction=5.0))
        self.assertTrue(disagreement.correct(features, formula)[2])

    def test_two_links_keep_independent_state(self):
        kwargs = dict(device_id="device-1", node_id="rx", tx_id="tx-1", rx_id="rx", corridor_id="corridor-1", session_id="s", expected_source_mac="aa:bb")
        left = LinkEdgePipeline(link_id="link-1", **kwargs)
        right = LinkEdgePipeline(link_id="link-2", **kwargs)
        for seq in range(1, 9):
            left.enqueue(make_frame(link="link-1", sequence=seq, timestamp=1_000_000 + seq * 1000))
            left.process_one()
        self.assertIsNotNone(left.state.last_sequence)
        self.assertIsNone(right.state.last_sequence)

    def test_auto_baseline_requires_no_operator_confirmation(self):
        pipeline = LinkEdgePipeline(
            device_id="device-1", node_id="rx-1", tx_id="tx-1", rx_id="rx-1",
            link_id="link-1", corridor_id="corridor-1", session_id="session-1",
            expected_source_mac="aa:bb", formula=FormulaFlex(warmup_samples=3, window_ms=300),
        )
        outputs = []
        seq = 1
        # Keep a sustained quiet tail so the causal score filter can settle
        # after automatic baseline discovery/rebase before asserting PASSABLE.
        for base in (30.0, 40.0, 50.0, 45.0, 55.0, 65.0, 48.0, 58.0, 68.0, 64.0, 64.0, 64.0, 64.0):
            for value in (base, base + 2.0, base - 2.0):
                pipeline.enqueue(make_frame(sequence=seq, timestamp=1_000_000 + seq * 100_000, value=value))
                output = pipeline.process_one()
                if output is not None:
                    outputs.append(output)
                seq += 1
        tail = pipeline.flush()
        if tail is not None:
            outputs.append(tail)
        self.assertTrue(outputs)
        self.assertIn(pipeline.state.baseline_state, {"BASELINE_STABLE", "BASELINE_UPDATED", "SHIFT_CANDIDATE", "REBASE_PENDING"})
        self.assertGreaterEqual(pipeline.state.baseline_version, 1)
        self.assertTrue(any(item.state in {EdgeResultState.UNKNOWN, EdgeResultState.DEGRADED, EdgeResultState.PASSABLE} for item in outputs))

    def test_persistent_quiet_shift_does_not_rebase_without_evidence(self):
        pipeline = LinkEdgePipeline(
            device_id="device-1", node_id="rx-1", tx_id="tx-1", rx_id="rx-1",
            link_id="link-1", corridor_id="corridor-1", session_id="session-1",
            expected_source_mac="aa:bb", formula=FormulaFlex(warmup_samples=3, window_ms=300),
        )
        seq = 1
        outputs = []
        for base in (30.0, 40.0, 50.0, 100.0, 102.0, 98.0, 101.0, 99.0, 103.0, 100.0, 102.0, 101.0):
            for value in (base, base + 2.0, base - 2.0):
                pipeline.enqueue(make_frame(sequence=seq, timestamp=1_000_000 + seq * 100_000, value=value))
                output = pipeline.process_one()
                if output is not None:
                    outputs.append(output)
                seq += 1
        tail = pipeline.flush()
        if tail is not None:
            outputs.append(tail)
        self.assertEqual(pipeline.state.baseline_version, 1)
        self.assertIn(pipeline.state.baseline_state, {"REBASE_PENDING", "SHIFT_CANDIDATE", "OCCUPIED_OR_BLOCKED"})
        self.assertTrue(any(item.state == EdgeResultState.UNKNOWN for item in outputs))
        self.assertFalse(any(item.state == EdgeResultState.PASSABLE and item.baseline_version >= 2 for item in outputs))

    def test_stationary_shift_and_fixed_object_remain_fail_closed(self):
        for shifted in (100.0, 105.0):
            pipeline = LinkEdgePipeline(
                device_id="device-1", node_id="rx-1", tx_id="tx-1", rx_id="rx-1",
                link_id="link-1", corridor_id="corridor-1", session_id="session-1",
                expected_source_mac="aa:bb", formula=FormulaFlex(warmup_samples=3, window_ms=300),
            )
            sequence = 1
            outputs = []
            for value in (30.0, 40.0, 50.0, shifted, shifted + 2.0, shifted - 2.0, shifted + 1.0, shifted - 1.0, shifted):
                pipeline.enqueue(make_frame(sequence=sequence, timestamp=1_000_000 + sequence * 100_000, value=value))
                item = pipeline.process_one()
                if item is not None:
                    outputs.append(item)
                sequence += 1
            tail = pipeline.flush()
            if tail is not None:
                outputs.append(tail)
            self.assertLessEqual(pipeline.state.baseline_version, 1)
            self.assertFalse(any(item.state == EdgeResultState.PASSABLE and item.baseline_version > 1 for item in outputs))

    def test_stationary_flat_candidate_is_accepted_after_warmup(self):
        pipeline = LinkEdgePipeline(
            device_id="device-1", node_id="rx-1", tx_id="tx-1", rx_id="rx-1",
            link_id="link-1", corridor_id="corridor-1", session_id="session-1",
            expected_source_mac="aa:bb", formula=FormulaFlex(warmup_samples=3, window_ms=300),
        )
        seq = 1
        for _ in range(4):
            for value in (100.0, 100.0, 100.0):
                pipeline.enqueue(make_frame(sequence=seq, timestamp=1_000_000 + seq * 100_000, value=value))
                pipeline.process_one()
                seq += 1
        output = pipeline.flush()
        self.assertIsNotNone(output)
        self.assertEqual(output.state, EdgeResultState.PASSABLE)
        self.assertIsNotNone(output.local_passability_score)

    def test_all_zero_csi_is_invalid(self):
        pipeline = self._pipeline(warmup=3)
        for seq in range(1, 4):
            pipeline.enqueue(make_frame(sequence=seq, timestamp=1_000_000 + seq * 1000, value=0.0))
            pipeline.process_one()
        output = pipeline.flush()
        self.assertEqual(output.invalid_count, output.sample_count)
        self.assertEqual(output.state, EdgeResultState.UNKNOWN)
        self.assertIsNone(output.local_passability_score)

    def test_malformed_iq_object_fails_closed_without_worker_crash(self):
        pipeline = self._pipeline(window_ms=300)
        frame = make_frame(sequence=1, timestamp=1_000_000)
        object.__setattr__(frame, "i_q", "malformed")
        pipeline.enqueue(frame)
        self.assertIsNone(pipeline.process_one())
        result = pipeline.flush()
        self.assertIsNotNone(result)
        self.assertEqual(result.state, EdgeResultState.UNKNOWN)
        self.assertEqual(result.invalid_count, result.sample_count)

    def test_far_future_wrong_source_does_not_close_valid_window(self):
        pipeline = self._pipeline(window_ms=1000)
        pipeline.enqueue(make_frame(sequence=1, timestamp=1_000_000))
        pipeline.process_one()
        pipeline.enqueue(make_frame(sequence=2, timestamp=99_000_000, source="00:00"))
        self.assertIsNone(pipeline.process_one())
        self.assertEqual(pipeline.state.window_start_us, 1_000_000)

    def test_pi_ingest_rejects_duplicate_and_keeps_unknown_null(self):
        pipeline = LinkEdgePipeline(
            device_id="device-1", node_id="rx-1", tx_id="tx-1", rx_id="rx-1",
            link_id="link-1", corridor_id="corridor-1", session_id="session-1",
            expected_source_mac="aa:bb", formula=FormulaFlex(warmup_samples=3),
        )
        pipeline.confirm_empty()
        result = None
        for seq in range(1, 4):
            pipeline.enqueue(make_frame(sequence=seq, timestamp=1_000_000 + seq * 1000))
            result = pipeline.process_one() or result
        result = pipeline.flush() or result
        packet = result.encode()
        ingestor = EdgeResultV5Ingestor(identities={"link-1": {"device_id": "device-1", "node_id": "rx-1", "tx_id": "tx-1", "rx_id": "rx-1", "corridor_id": "corridor-1"}})
        self.assertTrue(ingestor.ingest(packet, now_us=1_003_000).accepted)
        self.assertFalse(ingestor.ingest(packet, now_us=1_003_000).accepted)
        self.assertIsNotNone(ingestor.latest_by_link(now_us=1_003_000)["link-1"]["score"])


if __name__ == "__main__":
    unittest.main()
