"""Portable RX-side EdgeResult V5 reference pipeline.

The ESP-IDF implementation is kept in ``edge_result_v5_pipeline.c``.  This
small Python reference is intentionally deterministic and is used for replay,
math tests, and model contract tests when no ESP32-S3 is attached.
"""

from __future__ import annotations

import hashlib
import math
import statistics
from collections import deque
from dataclasses import dataclass, field
from typing import Any, Deque, Iterable, Mapping, Optional, Sequence, Tuple

try:
    from wievac.pi.app.edge_result_v5 import EdgeResultReason, EdgeResultState, EdgeResultV5
except ImportError:  # pragma: no cover - direct local execution
    from pi.app.edge_result_v5 import EdgeResultReason, EdgeResultState, EdgeResultV5  # type: ignore


FORMULA_VERSION = "formula-flex-v5.3-rx-median-mad"
FEATURE_SCHEMA_VERSION = "6"
MODEL_TARGET = "rx-tiny-ai"

BASELINE_NO_BASELINE = "NO_BASELINE"
BASELINE_CANDIDATE = "BASELINE_CANDIDATE"
BASELINE_STABLE = "BASELINE_STABLE"
BASELINE_SHIFT_CANDIDATE = "SHIFT_CANDIDATE"
BASELINE_REBASE_PENDING = "REBASE_PENDING"
BASELINE_UPDATED = "BASELINE_UPDATED"
BASELINE_OCCUPIED_OR_BLOCKED = "OCCUPIED_OR_BLOCKED"
BASELINE_UNKNOWN = "UNKNOWN"


@dataclass(frozen=True)
class CsiFrame:
    device_id: str
    node_id: str
    tx_id: str
    rx_id: str
    link_id: str
    corridor_id: str
    session_id: str
    boot_id: int
    sequence: int
    timestamp_us: int
    i_q: Tuple[Tuple[float, float], ...]
    source_mac: str
    expected_source_mac: str
    ltf_valid: bool = True
    layout_valid: bool = True
    first_word_invalid: bool = False
    # Zero means "derive from i_q".  A non-zero declaration must match the
    # copied I/Q bytes exactly; silently accepting a truncated vector corrupts
    # quality and baseline statistics.
    csi_length: int = 0
    rssi_dbm: Optional[float] = None
    noise_floor_dbm: Optional[float] = None

    def __post_init__(self) -> None:
        if self.csi_length == 0:
            object.__setattr__(self, "csi_length", len(self.i_q) * 2)


@dataclass(frozen=True)
class FormulaOutput:
    formula_score: Optional[float]
    formula_state: EdgeResultState
    formula_reason: str
    formula_version: str = FORMULA_VERSION
    baseline_ready: bool = False
    robust_delta: Optional[float] = None
    quality: float = 0.0
    uncertainty: float = 100.0
    raw_evidence_score: Optional[float] = None
    filtered_passability_score: Optional[float] = None
    baseline_state: str = BASELINE_NO_BASELINE
    baseline_version: int = 0
    baseline_update_reason: str = "startup"
    baseline_confidence: float = 0.0
    drift_state: str = "NONE"
    occupancy_evidence: Optional[float] = None
    blocking_evidence: Optional[float] = None


@dataclass(frozen=True)
class TinyAiMetadata:
    model_version: str
    model_hash: str
    feature_schema_version: str
    target: str = MODEL_TARGET
    max_correction: float = 10.0
    ood_delta: float = 12.0

    def validate(self) -> None:
        if self.target != MODEL_TARGET:
            raise ValueError("model_target_mismatch")
        if not self.model_version or not self.model_hash:
            raise ValueError("model_metadata_missing")
        if self.feature_schema_version != FEATURE_SCHEMA_VERSION:
            raise ValueError("model_schema_mismatch")
        if not math.isfinite(self.max_correction) or not 0.0 <= self.max_correction <= 50.0:
            raise ValueError("max_correction_out_of_range")
        if not math.isfinite(self.ood_delta) or self.ood_delta < 0.0 or self.ood_delta > 1000.0:
            raise ValueError("ood_delta_out_of_range")


class TinyAiModel:
    """Interface for a real model artifact; no heuristic is labelled AI."""

    metadata: TinyAiMetadata

    def predict(self, features: Mapping[str, float]) -> float:
        raise NotImplementedError


class TinyAiRunner:
    def __init__(self, model: Optional[TinyAiModel] = None) -> None:
        self.model = model

    def correct(self, features: Mapping[str, float], formula: FormulaOutput) -> Tuple[float, str, bool, float]:
        if self.model is None:
            return 0.0, "NOT_READY", False, formula.uncertainty
        try:
            self.model.metadata.validate()
        except Exception:
            return 0.0, "INCOMPATIBLE", False, 100.0
        required = ("formula_score", "robust_delta", "quality")
        if not isinstance(features, Mapping) or any(name not in features for name in required):
            return 0.0, "INCOMPATIBLE", False, 100.0
        try:
            if any(not math.isfinite(float(value)) for value in features.values()):
                return 0.0, "OOD", False, 100.0
        except Exception:
            return 0.0, "OOD", False, 100.0
        if formula.robust_delta is not None and formula.robust_delta > self.model.metadata.ood_delta:
            return 0.0, "OOD", False, min(100.0, formula.uncertainty + 25.0)
        try:
            # The model output is a bounded correction, not an absolute score.
            correction = float(self.model.predict(features))
        except Exception:
            return 0.0, "INCOMPATIBLE", False, 100.0
        if not math.isfinite(correction):
            return 0.0, "OOD", False, 100.0
        bounded = max(-self.model.metadata.max_correction, min(self.model.metadata.max_correction, correction))
        disagreement = formula.formula_score is not None and abs(bounded) >= self.model.metadata.max_correction * 0.8
        uncertainty = min(100.0, formula.uncertainty + (25.0 if disagreement else 0.0))
        return bounded, "READY", disagreement, uncertainty


class BoundedCsiQueue:
    def __init__(self, capacity: int = 32) -> None:
        if capacity < 1 or capacity > 4096:
            raise ValueError("capacity_out_of_range")
        self.capacity = int(capacity)
        self._queue: Deque[CsiFrame] = deque(maxlen=capacity)
        self.drop_count = 0

    def push(self, frame: CsiFrame) -> bool:
        if len(self._queue) >= self.capacity:
            self.drop_count += 1
            return False
        self._queue.append(frame)
        return True

    def pop(self) -> Optional[CsiFrame]:
        return self._queue.popleft() if self._queue else None

    def __len__(self) -> int:
        return len(self._queue)


@dataclass
class _LinkState:
    baseline_values: Deque[float] = field(default_factory=lambda: deque(maxlen=64))
    baseline_short_values: Deque[float] = field(default_factory=lambda: deque(maxlen=32))
    baseline_long_values: Deque[float] = field(default_factory=lambda: deque(maxlen=128))
    baseline_history: Deque[Tuple[int, float, float]] = field(default_factory=lambda: deque(maxlen=4))
    baseline_frame_count: int = 0
    baseline_learning_enabled: bool = True
    baseline_ready: bool = False
    baseline_state: str = BASELINE_NO_BASELINE
    baseline_version: int = 0
    baseline_update_reason: str = "startup"
    baseline_confidence: float = 0.0
    baseline_created_us: Optional[int] = None
    baseline_updated_us: Optional[int] = None
    explicit_baseline_confirmed: bool = False
    baseline_candidate_windows: int = 0
    rebase_candidate_windows: int = 0
    shift_candidate_windows: int = 0
    occupied_windows: int = 0
    occupancy_memory_windows: int = 0
    previous_baseline_center: Optional[float] = None
    previous_baseline_mad: Optional[float] = None
    filtered_score: Optional[float] = None
    filter_transition_state: str = "STABLE"
    state_candidate: Optional[EdgeResultState] = None
    state_candidate_windows: int = 0
    window_values: Deque[Tuple[int, float]] = field(default_factory=lambda: deque(maxlen=256))
    # Keep per-window evidence bounded even if a producer misbehaves.
    window_scores: Deque[FormulaOutput] = field(default_factory=lambda: deque(maxlen=256))
    window_baseline_candidates: Deque[float] = field(default_factory=lambda: deque(maxlen=256))
    window_baseline_initial_ready: bool = False
    # Keep jitter accounting bounded to the same causal window memory budget.
    window_intervals_us: Deque[int] = field(default_factory=lambda: deque(maxlen=256))
    window_sample_count: int = 0
    window_invalid_count: int = 0
    window_queue_drop_count: int = 0
    window_sequence_gap: int = 0
    window_gap_detected: bool = False
    window_last_timestamp_us: Optional[int] = None
    last_sequence: Optional[int] = None
    last_timestamp_us: Optional[int] = None
    window_start_us: Optional[int] = None
    window_seq: int = 0
    boot_id: Optional[int] = None
    pending_queue_drop_count: int = 0


class FormulaFlex:
    """Per-link dynamic robust baseline and bounded score calculation.

    Baseline learning starts automatically. ``confirm_empty`` remains an
    optional test/recovery accelerator, but normal RUN processing does not
    require an operator acknowledgement. Learning is frozen for invalid,
    lossy, low-quality or occupancy-like windows.
    """

    def __init__(
        self,
        *,
        warmup_samples: int = 8,
        window_ms: int = 1000,
        short_window_samples: int = 16,
        long_window_samples: int = 64,
        shift_threshold: float = 4.0,
        shift_persistence_windows: int = 3,
        rebase_persistence_windows: int = 12,
    ) -> None:
        if warmup_samples < 3 or warmup_samples > 512:
            raise ValueError("warmup_samples_out_of_range")
        if short_window_samples < 4 or short_window_samples > 128:
            raise ValueError("short_window_samples_out_of_range")
        if long_window_samples < short_window_samples or long_window_samples > 512:
            raise ValueError("long_window_samples_out_of_range")
        if not math.isfinite(shift_threshold) or shift_threshold <= 0.0 or shift_threshold > 100.0:
            raise ValueError("shift_threshold_out_of_range")
        if shift_persistence_windows < 2 or shift_persistence_windows > 32:
            raise ValueError("shift_persistence_windows_out_of_range")
        if rebase_persistence_windows < 2 or rebase_persistence_windows > 32:
            raise ValueError("rebase_persistence_windows_out_of_range")
        self.warmup_samples = int(warmup_samples)
        self.window_ms = max(50, min(10_000, int(window_ms)))
        self.short_window_samples = int(short_window_samples)
        self.long_window_samples = int(long_window_samples)
        # This is a bounded normalized safety guard (robust-delta units), not
        # an absolute CSI amplitude threshold and not a rebase trigger.
        self.shift_threshold = float(shift_threshold)
        self.shift_persistence_windows = int(shift_persistence_windows)
        self.rebase_persistence_windows = int(rebase_persistence_windows)

    def confirm_empty(self, state: _LinkState) -> None:
        """Optionally accelerate baseline candidate collection for tests."""
        state.baseline_values.clear()
        state.baseline_short_values.clear()
        state.baseline_long_values.clear()
        state.baseline_frame_count = 0
        state.baseline_learning_enabled = True
        state.baseline_ready = False
        state.baseline_state = BASELINE_CANDIDATE
        state.baseline_update_reason = "explicit_confirmation"
        state.baseline_confidence = 0.0
        state.baseline_candidate_windows = 0
        state.rebase_candidate_windows = 0
        state.shift_candidate_windows = 0
        state.occupied_windows = 0
        state.occupancy_memory_windows = 0
        state.explicit_baseline_confirmed = True
        state.window_baseline_candidates.clear()

    def revoke_empty(self, state: _LinkState) -> None:
        state.baseline_values.clear()
        state.baseline_short_values.clear()
        state.baseline_long_values.clear()
        state.baseline_frame_count = 0
        state.baseline_learning_enabled = False
        state.baseline_ready = False
        state.baseline_state = BASELINE_UNKNOWN
        state.baseline_update_reason = "baseline_revoked"
        state.baseline_confidence = 0.0
        state.window_baseline_candidates.clear()

    def _baseline(self, state: _LinkState) -> Tuple[float, float]:
        values = list(state.baseline_long_values or state.baseline_values)
        center = statistics.median(values)
        deviations = [abs(value - center) for value in values]
        mad = 1.4826 * statistics.median(deviations)
        # A flat baseline still needs a meaningful absolute shift detector.
        spread = max(1e-3, mad, abs(center) * 0.01)
        return center, spread

    @staticmethod
    def _candidate_stats(values: Sequence[float]) -> Tuple[float, float]:
        center = statistics.median(values)
        deviations = [abs(value - center) for value in values]
        return center, max(1e-3, 1.4826 * statistics.median(deviations))

    @staticmethod
    def _window_profile(state: _LinkState) -> Tuple[float, float, float, float, float, float]:
        """Return normalized short/long center and spread diagnostics."""
        short = list(state.baseline_short_values)
        long = list(state.baseline_long_values or state.baseline_values)
        short_center, short_spread = FormulaFlex._candidate_stats(short)
        long_center, long_spread = FormulaFlex._candidate_stats(long)
        scale = max(short_spread, long_spread, abs(long_center), 1e-6)
        center_shift = abs(short_center - long_center) / scale
        spread_shift = abs(short_spread - long_spread) / max(short_spread, long_spread, 1e-6)
        relative_spread = long_spread / max(abs(long_center), long_spread, 1e-6)
        return short_center, short_spread, long_center, long_spread, center_shift, spread_shift + relative_spread

    def _set_baseline(self, state: _LinkState, *, now_us: int, reason: str, updated: bool) -> None:
        values = list(state.baseline_long_values or state.baseline_values)
        if not values:
            return
        center, spread = self._candidate_stats(values)
        if state.baseline_ready and state.baseline_version:
            state.baseline_history.append((state.baseline_version, state.baseline_center, state.baseline_mad))
        state.baseline_center = center
        state.baseline_mad = spread
        state.baseline_ready = True
        state.baseline_learning_enabled = False
        state.baseline_version += 1
        state.baseline_state = BASELINE_UPDATED if updated else BASELINE_STABLE
        state.baseline_update_reason = reason
        state.baseline_confidence = min(100.0, 40.0 + min(60.0, len(values) * 2.0))
        if state.baseline_created_us is None:
            state.baseline_created_us = int(now_us)
        state.baseline_updated_us = int(now_us)
        state.baseline_candidate_windows = 0
        state.rebase_candidate_windows = 0
        state.shift_candidate_windows = 0
        state.occupied_windows = 0
        state.occupancy_memory_windows = 0

    def on_window_complete(self, state: _LinkState, *, now_us: int, robust_delta: Optional[float], quality: float, safe: bool, temporal_motion: float = 0.0) -> None:
        """Advance baseline/drift state once per completed causal window."""
        if state.baseline_state in {BASELINE_NO_BASELINE, BASELINE_CANDIDATE, BASELINE_UNKNOWN}:
            if safe and state.baseline_learning_enabled:
                state.baseline_candidate_windows += 1
                if state.baseline_frame_count >= self.warmup_samples:
                    if state.explicit_baseline_confirmed or state.baseline_candidate_windows >= 2:
                        _, _, candidate_center, candidate_spread, center_shift, profile_shift = self._window_profile(state)
                        # Use normalized distribution agreement rather than
                        # absolute amplitude gates. Flat and repeatable noisy
                        # links are both valid candidates; only an unstable
                        # short/long profile remains fail-closed.
                        if not state.explicit_baseline_confirmed and (center_shift > 0.75 or profile_shift > 1.50):
                            state.baseline_state = BASELINE_UNKNOWN
                            state.baseline_update_reason = "candidate_contaminated"
                            state.baseline_confidence = 0.0
                            state.baseline_values.clear()
                            state.baseline_short_values.clear()
                            state.baseline_long_values.clear()
                            state.baseline_frame_count = 0
                            state.baseline_candidate_windows = 0
                        else:
                            self._set_baseline(state, now_us=now_us, reason="automatic_stable_candidate", updated=False)
            return

        if not state.baseline_ready:
            return
        if not safe or quality < 70.0:
            state.baseline_update_reason = "learning_frozen_transport"
            return
        delta = float(robust_delta or 0.0)
        if temporal_motion >= 0.20:
            state.occupied_windows += 1
            if state.occupied_windows >= 2:
                state.occupancy_memory_windows = 25
        elif state.occupancy_memory_windows > 0:
            state.occupancy_memory_windows -= 1
        if delta >= self.shift_threshold:
            state.shift_candidate_windows += 1
            state.baseline_state = BASELINE_SHIFT_CANDIDATE
            state.baseline_update_reason = "persistent_shift_candidate"
            state.baseline_confidence = min(state.baseline_confidence, 65.0)
            if state.shift_candidate_windows >= self.shift_persistence_windows:
                if state.occupied_windows >= 2:
                    state.baseline_state = BASELINE_OCCUPIED_OR_BLOCKED
                    state.baseline_update_reason = "persistent_occupancy_evidence"
                elif state.occupancy_memory_windows == 0:
                    state.rebase_candidate_windows += 1
                    state.baseline_state = BASELINE_REBASE_PENDING
                    state.baseline_update_reason = "quiet_shift_rebase_candidate"
                    if state.rebase_candidate_windows >= self.rebase_persistence_windows and len(state.baseline_long_values) >= self.warmup_samples:
                        self._set_baseline(state, now_us=now_us, reason="automatic_environment_rebase", updated=True)
                else:
                    state.baseline_state = BASELINE_REBASE_PENDING
                    state.baseline_update_reason = "ambiguous_shift_no_rebase"
            return

        state.shift_candidate_windows = 0
        if state.occupancy_memory_windows == 0:
            state.occupied_windows = 0
        if state.baseline_state in {BASELINE_SHIFT_CANDIDATE, BASELINE_REBASE_PENDING}:
            if state.rebase_candidate_windows >= self.rebase_persistence_windows and state.occupancy_memory_windows == 0:
                self._set_baseline(state, now_us=now_us, reason="automatic_environment_rebase", updated=True)
            else:
                state.baseline_state = BASELINE_REBASE_PENDING
                state.baseline_update_reason = "ambiguous_shift_no_rebase"
            return
        if state.baseline_state == BASELINE_UPDATED:
            state.baseline_state = BASELINE_STABLE
        state.baseline_update_reason = "stable_baseline"
        state.baseline_confidence = min(100.0, state.baseline_confidence + 0.5)

    def update(self, state: _LinkState, amplitudes: Sequence[float], *, quality: float, now_us: int) -> FormulaOutput:
        # Validate the complete direct-call payload before mutating baseline
        # state.  The receiver must survive malformed values from replay or a
        # future caller just as it does malformed packets on the wire.
        try:
            converted = [float(value) for value in amplitudes]
        except (TypeError, ValueError, OverflowError):
            return FormulaOutput(None, EdgeResultState.UNKNOWN, "invalid_csi",
                                 baseline_ready=state.baseline_ready, quality=0.0,
                                 uncertainty=100.0, baseline_state=state.baseline_state,
                                 baseline_version=state.baseline_version,
                                 baseline_update_reason=state.baseline_update_reason,
                                 baseline_confidence=state.baseline_confidence)
        if not converted or any(not math.isfinite(value) or value < 0.0 for value in converted):
            return FormulaOutput(None, EdgeResultState.UNKNOWN, "invalid_csi",
                                 baseline_ready=state.baseline_ready, quality=0.0,
                                 uncertainty=100.0, baseline_state=state.baseline_state,
                                 baseline_version=state.baseline_version,
                                 baseline_update_reason=state.baseline_update_reason,
                                 baseline_confidence=state.baseline_confidence)
        finite = converted
        if not finite:
            return FormulaOutput(None, EdgeResultState.UNKNOWN, "invalid_csi", baseline_ready=state.baseline_ready, quality=0.0, uncertainty=100.0, baseline_state=state.baseline_state, baseline_version=state.baseline_version, baseline_update_reason=state.baseline_update_reason, baseline_confidence=state.baseline_confidence)
        center = statistics.median(finite)
        if state.baseline_learning_enabled and (state.baseline_frame_count + len(state.window_baseline_candidates)) < self.warmup_samples:
            state.window_baseline_candidates.append(center)
            learned_count = state.baseline_frame_count + len(state.window_baseline_candidates)
            if learned_count < self.warmup_samples:
                state.baseline_state = BASELINE_CANDIDATE
                return FormulaOutput(None, EdgeResultState.UNKNOWN, "warming_up", quality=quality, uncertainty=100.0 - quality, baseline_state=state.baseline_state, baseline_version=state.baseline_version, baseline_update_reason=state.baseline_update_reason, baseline_confidence=state.baseline_confidence)
            state.baseline_state = BASELINE_CANDIDATE
            state.baseline_ready = True
        baseline_values = list(state.baseline_long_values or state.baseline_values or state.window_baseline_candidates)
        baseline, spread = self._candidate_stats(baseline_values) if baseline_values else (center, 1.0)
        robust_delta = abs(center - baseline) / spread
        state.window_values.append((now_us, center))
        state.baseline_short_values.append(center)
        if state.baseline_ready and robust_delta < self.shift_threshold and quality >= 70.0:
            # Stage quiet observations for an atomic, safe-window update.
            state.window_baseline_candidates.append(center)
        score = max(0.0, min(100.0, 100.0 / (1.0 + robust_delta)))
        uncertainty = min(100.0, max(0.0, (100.0 - quality) + min(40.0, robust_delta * 2.0)))
        if quality < 50.0:
            result_state = EdgeResultState.DEGRADED
            reason = "quality_low"
        elif state.baseline_state == BASELINE_OCCUPIED_OR_BLOCKED and robust_delta >= 8.0:
            result_state = EdgeResultState.BLOCKED
            reason = "persistent_occupancy_evidence"
        elif state.occupied_windows > 0:
            result_state = EdgeResultState.BLOCKED if score < 50.0 else EdgeResultState.DEGRADED
            reason = "persistent_occupancy_evidence"
        elif state.baseline_state in {BASELINE_SHIFT_CANDIDATE, BASELINE_REBASE_PENDING}:
            result_state = EdgeResultState.UNKNOWN
            reason = "environment_shift"
        elif robust_delta >= 8.0 and score < 20.0:
            result_state = EdgeResultState.UNKNOWN
            reason = "insufficient_persistence"
        elif robust_delta >= 2.0:
            result_state = EdgeResultState.DEGRADED
            reason = "robust_delta"
        else:
            result_state = EdgeResultState.PASSABLE
            reason = "valid"
        return FormulaOutput(score, result_state, reason, baseline_ready=state.baseline_ready, robust_delta=robust_delta, quality=quality, uncertainty=uncertainty, raw_evidence_score=score, filtered_passability_score=score, baseline_state=state.baseline_state, baseline_version=state.baseline_version, baseline_update_reason=state.baseline_update_reason, baseline_confidence=state.baseline_confidence, drift_state=state.baseline_state)


class LinkEdgePipeline:
    """One RX link; no mutable state is shared with another link."""

    def __init__(
        self,
        *,
        device_id: str,
        node_id: str,
        tx_id: str,
        rx_id: str,
        link_id: str,
        corridor_id: str,
        session_id: str,
        expected_source_mac: str,
        formula: Optional[FormulaFlex] = None,
        ai: Optional[TinyAiRunner] = None,
        queue_capacity: int = 32,
    ) -> None:
        self.identity = dict(device_id=device_id, node_id=node_id, tx_id=tx_id, rx_id=rx_id, link_id=link_id, corridor_id=corridor_id, session_id=session_id)
        self.expected_source_mac = expected_source_mac.lower()
        self.queue = BoundedCsiQueue(queue_capacity)
        self.formula = formula or FormulaFlex()
        self.ai = ai or TinyAiRunner()
        self.state = _LinkState()
        self._pending_results: Deque[EdgeResultV5] = deque()

    def enqueue(self, frame: CsiFrame) -> bool:
        pushed = self.queue.push(frame)
        if not pushed:
            if self.state.window_start_us is not None and self.state.window_sample_count > 0:
                self.state.window_queue_drop_count += 1
            else:
                self.state.pending_queue_drop_count += 1
        return pushed

    def confirm_empty(self) -> None:
        """Explicitly authorize baseline learning for the current link."""
        self.formula.confirm_empty(self.state)

    def revoke_empty(self) -> None:
        # Keep the state-machine reason/state in sync with the Formula API.
        self.formula.revoke_empty(self.state)

    def reset_boot(self, boot_id: int) -> None:
        """Acknowledge a new stream epoch without trusting numeric ordering.

        The caller must perform this only after an authenticated pairing or
        control-plane handshake; keeping it explicit prevents delayed packets
        from an old boot from erasing current state.
        """
        if isinstance(boot_id, bool) or not isinstance(boot_id, int) or boot_id <= 0:
            raise ValueError("boot_id_invalid")
        next_window_seq = max(1, self.state.window_seq + 1)
        self.state = _LinkState(boot_id=boot_id, window_seq=next_window_seq)
        self._pending_results.clear()

    @staticmethod
    def _amplitudes(frame: CsiFrame) -> Tuple[float, ...]:
        try:
            return tuple(math.hypot(float(i), float(q)) for i, q in frame.i_q)
        except (TypeError, ValueError, OverflowError):
            return ()

    def _invalid(self, frame: CsiFrame, reason: str) -> Optional[EdgeResultV5]:
        """Attribute a rejected frame to the active causal window.

        Rejections are surfaced by the eventual UNKNOWN window result rather
        than emitted as extra production windows. The recorder/service layer
        may still persist the original rejection envelope separately.
        """
        try:
            timestamp_us = int(frame.timestamp_us)
        except (TypeError, ValueError, OverflowError):
            timestamp_us = self.state.window_last_timestamp_us or 1
        if self.state.window_start_us is None:
            self._reset_window(max(1, timestamp_us))
        self.state.window_sample_count += 1
        self.state.window_invalid_count += 1
        if self.state.window_last_timestamp_us is not None and timestamp_us > self.state.window_last_timestamp_us:
            self.state.window_intervals_us.append(timestamp_us - self.state.window_last_timestamp_us)
        self.state.window_last_timestamp_us = timestamp_us
        return None

    @staticmethod
    def _reason_code(reason: str) -> EdgeResultReason:
        mapping = {
            "valid": EdgeResultReason.VALID,
            "warming_up": EdgeResultReason.WARMING_UP,
            "invalid_csi": EdgeResultReason.INVALID_CSI,
            "quality_low": EdgeResultReason.QUALITY_LOW,
            "change_point": EdgeResultReason.QUALITY_LOW,
            "robust_delta": EdgeResultReason.QUALITY_LOW,
            "wrong_source_mac": EdgeResultReason.INVALID_CSI,
            "timestamp_or_sequence_invalid": EdgeResultReason.INVALID_CSI,
            "out_of_order": EdgeResultReason.INVALID_CSI,
            "formula_ai_disagreement": EdgeResultReason.DISAGREEMENT,
            "model_ood": EdgeResultReason.OOD,
            "model_incompatible": EdgeResultReason.UNKNOWN,
            "baseline_not_confirmed": EdgeResultReason.WARMING_UP,
            "sequence_gap": EdgeResultReason.QUALITY_LOW,
            "queue_drop": EdgeResultReason.QUALITY_LOW,
            "boot_reset_required": EdgeResultReason.INVALID_CSI,
        }
        return mapping.get(reason, EdgeResultReason.UNKNOWN)

    def _result(self, frame: CsiFrame, score: Optional[float], result_state: EdgeResultState, quality: float, uncertainty: float, disagreement: bool, reason: str, *, formula_version: str = FORMULA_VERSION, model_version: str = "NOT_READY", model_hash: str = "", end_us: Optional[int] = None, raw_score: Optional[float] = None, filtered_score: Optional[float] = None, occupancy_evidence: Optional[float] = None, blocking_evidence: Optional[float] = None, transition_state: Optional[str] = None, model_state: str = "NOT_READY") -> EdgeResultV5:
        start = self.state.window_start_us or frame.timestamp_us
        end = max(start, end_us if end_us is not None else frame.timestamp_us)
        if end <= start:
            end = start + 1
        self.state.window_seq += 1
        safe_score = None if result_state is EdgeResultState.UNKNOWN else score
        return EdgeResultV5(
            **self.identity,
            boot_id=frame.boot_id,
            window_seq=self.state.window_seq,
            window_start_us=start,
            window_end_us=end,
            rx_timestamp_us=frame.timestamp_us,
            age_ms=0,
            local_passability_score=safe_score,
            state=result_state,
            quality=max(0.0, min(100.0, quality)),
            uncertainty=max(0.0, min(100.0, uncertainty)),
            disagreement=disagreement,
            reason_code=self._reason_code(reason),
            formula_version=formula_version,
            model_version=model_version,
            model_hash=model_hash,
            feature_schema_version=FEATURE_SCHEMA_VERSION,
            sample_count=self.state.window_sample_count,
            invalid_count=self.state.window_invalid_count,
            queue_drop_count=self.state.window_queue_drop_count,
            sequence_gap=self.state.window_sequence_gap,
            packet_loss_ratio=(self.state.window_sequence_gap + self.state.window_queue_drop_count) /
                              max(1, self.state.window_sample_count + self.state.window_sequence_gap + self.state.window_queue_drop_count),
            jitter_ms=self._window_jitter_ms(),
            baseline_state=self.state.baseline_state,
            baseline_version=self.state.baseline_version,
            baseline_update_reason=self.state.baseline_update_reason,
            baseline_confidence=self.state.baseline_confidence,
            drift_state=self.state.baseline_state,
            raw_evidence_score=(None if raw_score is None else raw_score),
            filtered_passability_score=(None if filtered_score is None else filtered_score),
            transition_state=transition_state or self.state.filter_transition_state,
            occupancy_evidence=occupancy_evidence,
            blocking_evidence=blocking_evidence,
            model_state=model_state,
        ).normalized()

    def _window_jitter_ms(self) -> float:
        intervals = self.state.window_intervals_us
        if len(intervals) < 2:
            return 0.0
        mean = statistics.fmean(intervals)
        variance = statistics.fmean((value - mean) ** 2 for value in intervals)
        return max(0.0, math.sqrt(variance) / 1000.0)

    def _reset_window(self, start_us: int) -> None:
        self.state.window_start_us = max(1, int(start_us))
        self.state.window_values.clear()
        self.state.window_scores.clear()
        self.state.window_baseline_candidates.clear()
        self.state.window_baseline_initial_ready = self.state.baseline_ready
        self.state.window_intervals_us.clear()
        self.state.filter_transition_state = "STABLE"
        self.state.window_sample_count = 0
        self.state.window_invalid_count = 0
        self.state.window_sequence_gap = 0
        self.state.window_gap_detected = False
        self.state.window_queue_drop_count = self.state.pending_queue_drop_count
        self.state.pending_queue_drop_count = 0
        self.state.window_last_timestamp_us = None

    def _finalize_window(self, frame: CsiFrame, end_us: int) -> EdgeResultV5:
        scores = [item for item in self.state.window_scores if item.formula_score is not None]
        quality = statistics.fmean(item.quality for item in self.state.window_scores) if self.state.window_scores else 0.0
        uncertainty = max((item.uncertainty for item in self.state.window_scores), default=100.0)
        raw_score = (statistics.fmean(float(item.formula_score) for item in scores) if scores else None)
        raw_delta = (statistics.fmean(float(item.robust_delta or 0.0) for item in scores) if scores else None)
        occupancy_evidence = max((item.occupancy_evidence for item in scores if item.occupancy_evidence is not None), default=None)
        blocking_evidence = max((item.blocking_evidence for item in scores if item.blocking_evidence is not None), default=None)
        # Preserve explicit V6 evidence from a model/formula when present;
        # otherwise expose only conservative state-derived evidence for this
        # window (never a cross-window cached value).
        if occupancy_evidence is None and raw_delta is not None:
            occupancy_evidence = min(100.0, max(0.0, raw_delta * 10.0))
        if blocking_evidence is None and self.state.baseline_state == BASELINE_OCCUPIED_OR_BLOCKED:
            blocking_evidence = occupancy_evidence
        temporal_motion = 0.0
        if len(self.state.window_intervals_us) >= 1 and self.state.window_values:
            values = [value for _, value in self.state.window_values]
            if len(values) >= 2:
                temporal_motion = min(1.0, statistics.fmean(abs(b - a) for a, b in zip(values, values[1:])) /
                                     max(1e-6, abs(statistics.median(values))))
        safe = (self.state.window_sample_count > 0 and self.state.window_invalid_count == 0 and
                self.state.window_queue_drop_count == 0 and not self.state.window_gap_detected and quality >= 70.0)
        # Stage baseline candidates until the complete window passes every
        # transport/quality gate. Rejected windows can never train or rebase.
        staged = list(self.state.window_baseline_candidates)
        if safe and staged:
            self.state.baseline_values.extend(staged)
            self.state.baseline_short_values.extend(staged)
            self.state.baseline_long_values.extend(staged)
            self.state.baseline_frame_count = min(65_535, self.state.baseline_frame_count + len(staged))
        else:
            self.state.window_baseline_candidates.clear()
            if not self.state.window_baseline_initial_ready:
                self.state.baseline_ready = False
                self.state.baseline_state = BASELINE_CANDIDATE if self.state.baseline_frame_count else BASELINE_NO_BASELINE
        self.formula.on_window_complete(
            self.state,
            now_us=end_us,
            robust_delta=raw_delta,
            quality=quality,
            safe=safe,
            temporal_motion=temporal_motion,
        )
        if (self.state.window_sample_count == 0 or self.state.window_invalid_count > 0 or
                self.state.window_queue_drop_count > 0 or self.state.window_gap_detected):
            reason = "queue_drop" if self.state.window_queue_drop_count else "sequence_gap" if self.state.window_gap_detected else "invalid_csi"
            if self.state.filtered_score is not None:
                return self._result(frame, self.state.filtered_score, EdgeResultState.DEGRADED, quality, 100.0, False, reason, end_us=end_us,
                                    raw_score=raw_score, filtered_score=self.state.filtered_score)
            return self._result(frame, None, EdgeResultState.UNKNOWN, quality, max(uncertainty, 80.0), False, reason, end_us=end_us)
        if not scores or not self.state.baseline_ready or self.state.baseline_state in {
            BASELINE_NO_BASELINE, BASELINE_CANDIDATE, BASELINE_SHIFT_CANDIDATE, BASELINE_REBASE_PENDING,
        }:
            return self._result(frame, None, EdgeResultState.UNKNOWN, quality, 100.0, False,
                                "environment_shift" if self.state.baseline_state in {BASELINE_SHIFT_CANDIDATE, BASELINE_REBASE_PENDING}
                                else "baseline_not_confirmed" if not self.state.baseline_ready else "warming_up", end_us=end_us,
                                occupancy_evidence=occupancy_evidence, blocking_evidence=blocking_evidence,
                                transition_state=self.state.filter_transition_state, model_state="NOT_READY")
        formula_score = raw_score if raw_score is not None else 0.0
        robust_delta = raw_delta or 0.0
        filtered_score = statistics.median([float(item.formula_score) for item in scores])
        if self.state.filtered_score is None:
            self.state.filtered_score = filtered_score
        else:
            delta = filtered_score - self.state.filtered_score
            corroborated = temporal_motion >= 0.20
            if not corroborated:
                tau = 20.0
            elif self.state.occupied_windows > 0 and delta > 0.0:
                tau = 7.0
            else:
                tau = 3.0
            alpha = min(1.0, 1.0 / tau)
            filtered_score = max(0.0, min(100.0, self.state.filtered_score + alpha * delta))
            self.state.filter_transition_state = "STABLE"
            self.state.filtered_score = filtered_score
        duration_s = max(0.01, (end_us - (self.state.window_start_us or end_us)) / 1_000_000.0)
        baseline_scale = max(1.0, abs(float(getattr(self.state, "baseline_mad", 1.0))))
        filtered_score = self.state.filtered_score
        formula = FormulaOutput(formula_score, max(scores, key=lambda item: item.uncertainty).formula_state,
                                "valid", baseline_ready=True, robust_delta=robust_delta,
                                quality=quality, uncertainty=uncertainty,
                                raw_evidence_score=raw_score,
                                filtered_passability_score=filtered_score,
                                baseline_state=self.state.baseline_state,
                                baseline_version=self.state.baseline_version,
                                baseline_update_reason=self.state.baseline_update_reason,
                                baseline_confidence=self.state.baseline_confidence,
                                drift_state=self.state.baseline_state)
        features = {"formula_score": formula_score, "robust_delta": robust_delta, "quality": quality}
        correction, model_state, disagreement, ai_uncertainty = self.ai.correct(features, formula)
        # AI correction is also causal: it cannot jump over the same
        # evidence/scale-aware transition envelope as Formula output.
        ai_target = max(0.0, min(100.0, filtered_score + correction))
        ai_step = ai_target - filtered_score
        ai_allowed = max(0.5, min(20.0, (4.0 + baseline_scale) * max(0.25, quality / 100.0) * duration_s))
        final_score = filtered_score + math.copysign(min(abs(ai_step), ai_allowed), ai_step) if ai_step else filtered_score
        if abs(ai_step) > ai_allowed:
            self.state.filter_transition_state = "TRANSITION"
            reason = "causal_rate_limit_transition"
        final_state = formula.formula_state
        reason = "valid"
        if model_state in {"OOD", "INCOMPATIBLE"}:
            return self._result(frame, None, EdgeResultState.UNKNOWN, quality, 100.0, False,
                                "model_ood" if model_state == "OOD" else "model_incompatible", end_us=end_us,
                                occupancy_evidence=occupancy_evidence, blocking_evidence=blocking_evidence,
                                transition_state=self.state.filter_transition_state, model_state=model_state)
        if disagreement:
            final_state = EdgeResultState.DEGRADED
            reason = "formula_ai_disagreement"
        elif self.state.occupied_windows > 0:
            final_state = EdgeResultState.BLOCKED if final_score < 50.0 else EdgeResultState.DEGRADED
            reason = "persistent_occupancy_evidence"
        result = self._result(frame, final_score, final_state, quality, max(uncertainty, ai_uncertainty), disagreement,
                            reason, model_version=getattr(getattr(self.ai, "model", None), "metadata", None).model_version
                            if getattr(getattr(self.ai, "model", None), "metadata", None) else "NOT_READY",
                            model_hash=getattr(getattr(self.ai, "model", None), "metadata", None).model_hash
                            if getattr(getattr(self.ai, "model", None), "metadata", None) else "", end_us=end_us,
                            raw_score=raw_score, filtered_score=filtered_score,
                            occupancy_evidence=occupancy_evidence, blocking_evidence=blocking_evidence,
                            transition_state=self.state.filter_transition_state, model_state=model_state)
        return result

    def _accept_frame(self, frame: CsiFrame) -> None:
        self.state.window_sample_count += 1
        if self.state.window_last_timestamp_us is not None:
            delta = frame.timestamp_us - self.state.window_last_timestamp_us
            if delta > 0:
                self.state.window_intervals_us.append(delta)
        self.state.window_last_timestamp_us = frame.timestamp_us
        amplitudes = self._amplitudes(frame)
        finite = tuple(value for value in amplitudes if math.isfinite(value) and value >= 0.0)
        try:
            expected_length = len(frame.i_q) * 2
        except TypeError:
            expected_length = 0
        if (frame.csi_length < 4 or frame.csi_length > 128 or frame.csi_length % 2 or
                frame.csi_length != expected_length or len(finite) != len(amplitudes) or not finite or
                max(finite, default=0.0) <= 0.0):
            self.state.window_invalid_count += 1
            return
        quality = 100.0 * len(finite) / len(amplitudes)
        formula = self.formula.update(self.state, amplitudes, quality=quality, now_us=frame.timestamp_us)
        self.state.window_scores.append(formula)

    def process_one(self) -> Optional[EdgeResultV5]:
        if self._pending_results:
            return self._pending_results.popleft()
        frame = self.queue.pop()
        if frame is None:
            return None
        if frame.link_id != self.identity["link_id"] or frame.source_mac.lower() != self.expected_source_mac:
            self._invalid(frame, "wrong_source_mac")
            return self._pending_results.popleft() if self._pending_results else None
        if not frame.ltf_valid or not frame.layout_valid or frame.first_word_invalid:
            self._invalid(frame, "invalid_csi")
            return self._pending_results.popleft() if self._pending_results else None
        if (isinstance(frame.boot_id, bool) or not isinstance(frame.boot_id, int) or
                isinstance(frame.sequence, bool) or not isinstance(frame.sequence, int) or
                isinstance(frame.timestamp_us, bool) or not isinstance(frame.timestamp_us, int) or
                frame.boot_id <= 0 or frame.sequence <= 0 or frame.timestamp_us <= 0):
            self._invalid(frame, "timestamp_or_sequence_invalid")
            return self._pending_results.popleft() if self._pending_results else None
        if self.state.boot_id is None:
            self.state.boot_id = frame.boot_id
        elif frame.boot_id != self.state.boot_id:
            # A boot transition must be explicitly acknowledged by a new
            # pipeline instance/handshake; accepting it here would let delayed
            # packets erase a valid stream.
            self._invalid(frame, "boot_reset_required")
            return self._pending_results.popleft() if self._pending_results else None
        if self.state.last_sequence is not None:
            if frame.sequence <= self.state.last_sequence or frame.timestamp_us <= (self.state.last_timestamp_us or 0):
                self._invalid(frame, "out_of_order")
                return self._pending_results.popleft() if self._pending_results else None
            gap = max(0, frame.sequence - self.state.last_sequence - 1)
            if gap:
                self.state.window_sequence_gap += gap
                self.state.window_gap_detected = True
        self.state.boot_id = frame.boot_id
        self.state.last_sequence = frame.sequence
        self.state.last_timestamp_us = frame.timestamp_us
        if self.state.window_start_us is None:
            self._reset_window(frame.timestamp_us)
        # Only an admitted, ordered frame may close a window. A malformed
        # future packet cannot reset a valid window and cause a data-loss DoS.
        if self.state.window_sample_count > 0:
            boundary = self.state.window_start_us + self.formula.window_ms * 1000
            if frame.timestamp_us >= boundary:
                previous = self._finalize_window(frame, boundary)
                self._reset_window(boundary)
                self._pending_results.append(previous)
        self._accept_frame(frame)
        return self._pending_results.popleft() if self._pending_results else None

    def flush(self) -> Optional[EdgeResultV5]:
        """Close the current partial window exactly once for shutdown/replay."""
        if self.state.window_start_us is None or self.state.window_sample_count == 0:
            return None
        end = max(self.state.window_start_us + 1, self.state.window_last_timestamp_us or self.state.window_start_us + 1)
        frame = CsiFrame(**self.identity, boot_id=self.state.boot_id or 1,
                         sequence=self.state.last_sequence or 1, timestamp_us=end,
                         i_q=((0.0, 0.0),) * 2, source_mac=self.expected_source_mac,
                         expected_source_mac=self.expected_source_mac, csi_length=4)
        result = self._finalize_window(frame, end)
        self._reset_window(end + 1)
        return result


__all__ = [
    "CsiFrame", "FormulaOutput", "FormulaFlex", "TinyAiMetadata", "TinyAiModel", "TinyAiRunner",
    "BoundedCsiQueue", "LinkEdgePipeline", "FORMULA_VERSION", "FEATURE_SCHEMA_VERSION", "MODEL_TARGET",
]
