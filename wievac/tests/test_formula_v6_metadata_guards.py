import math
import unittest
from collections import deque

from wievac.firmware.receiver.edge_result_v5_reference import FormulaFlex, _LinkState


class FormulaInputGuardTests(unittest.TestCase):
    def test_malformed_direct_inputs_never_escape_or_train(self):
        for payload in (["bad"], [None], [object()], [float("nan")], [float("inf")], [1.0, float("nan")]):
            state = _LinkState()
            formula = FormulaFlex()
            result = formula.update(state, payload, quality=100.0, now_us=1)
            self.assertEqual(result.formula_reason, "invalid_csi")
            self.assertIsNone(result.formula_score)
            self.assertEqual(result.uncertainty, 100.0)
            self.assertEqual(state.baseline_frame_count, 0)
            self.assertFalse(state.baseline_ready)


class WindowBoundTests(unittest.TestCase):
    def test_intervals_are_bounded(self):
        state = _LinkState()
        self.assertIsInstance(state.window_intervals_us, deque)
        self.assertLessEqual(state.window_intervals_us.maxlen, 256)
        state.window_intervals_us.extend(range(10000))
        self.assertLessEqual(len(state.window_intervals_us), 256)
        mean = sum(state.window_intervals_us) / len(state.window_intervals_us)
        variance = sum((v - mean) ** 2 for v in state.window_intervals_us) / len(state.window_intervals_us)
        self.assertTrue(math.isfinite(math.sqrt(variance) / 1000.0))


if __name__ == "__main__":
    unittest.main()
