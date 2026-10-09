"""Normalized CSI occupancy state for Phase 1.

CSI is only an occupancy-ratio estimate.  This module deliberately preserves
UNKNOWN/STALE data rather than silently converting failed measurements to 0.
"""

from __future__ import annotations

import math
import random
import time


class CSILayer:
    def __init__(self, noise_level: float = 0.0, ema_alpha: float = 1.0, stale_seconds: float = 8.0):
        self.noise_level = max(0.0, noise_level)
        self.ema_alpha = max(0.01, min(1.0, ema_alpha))
        self.stale_seconds = max(0.1, stale_seconds)
        self.states: dict[str, dict] = {}

    @staticmethod
    def _valid(value: object) -> float | None:
        try:
            numeric = float(value)
        except (TypeError, ValueError):
            return None
        if not math.isfinite(numeric):
            return None
        return max(0.0, min(1.0, numeric))

    def update(self, edge_id: str, measured_k: object, confidence: float = 1.0, now: float | None = None) -> dict:
        now = time.time() if now is None else now
        value = self._valid(measured_k)
        previous = self.states.get(edge_id)
        if value is None:
            state = {
                **(previous or {"measured_k": None, "filtered_k": None, "confidence": 0.0}),
                "status": "UNKNOWN",
                "last_updated": previous.get("last_updated", 0) if previous else 0,
            }
            # Keep the failed measurement visible. Returning an UNKNOWN value
            # without storing it would let a previous OK value appear valid.
            self.states[edge_id] = state
            return state
        # Khi alpha=1.0 (dữ liệu đã lọc sạch từ Pi hoặc simulator rút gọn), nhận trực tiếp giá trị k
        filtered = value if (self.ema_alpha >= 0.99 or not previous or previous.get("filtered_k") is None) else (
            self.ema_alpha * value + (1.0 - self.ema_alpha) * previous["filtered_k"]
        )
        state = {"measured_k": value, "filtered_k": round(filtered, 3), "confidence": max(0.0, min(1.0, float(confidence))), "last_updated": now, "status": "OK"}
        self.states[edge_id] = state
        return state

    def state_for(self, edge_id: str, now: float | None = None) -> dict:
        now = time.time() if now is None else now
        state = self.states.get(edge_id)
        if not state:
            return {"measured_k": None, "filtered_k": None, "confidence": 0.0, "last_updated": 0, "status": "UNKNOWN"}
        if state.get("status") == "UNKNOWN":
            return dict(state)
        if now - state["last_updated"] > self.stale_seconds:
            return {**state, "status": "STALE"}
        return dict(state)

    def all_states(self, edge_ids, now: float | None = None) -> dict[str, dict]:
        return {edge_id: self.state_for(edge_id, now) for edge_id in edge_ids}

    def process_edge_data(self, theoretical_edge_fill: dict[str, float]) -> dict[str, dict]:
        """Xử lý trực tiếp dữ liệu sạch đã quy đổi, không thêm nhiễu nhân tạo."""
        result = {}
        for edge_id, fill in theoretical_edge_fill.items():
            base = self._valid(fill)
            if base is None:
                continue
            # Dữ liệu sạch đã xử lý, đưa thẳng vào pipeline với độ tin cậy 1.0
            noisy = base if self.noise_level == 0.0 else max(0.0, min(1.0, base + (random.uniform(-self.noise_level, self.noise_level) if base > 0.05 else 0.0)))
            result[edge_id] = self.update(edge_id, noisy, confidence=1.0)
        return result

    def process_node_data(self, theoretical_node_fill):
        return self.process_edge_data(theoretical_node_fill)
