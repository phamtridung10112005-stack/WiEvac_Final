"""Short-horizon, CSI-corrected corridor-load forecasts for Phase 1.

The module projects equivalent load, never an exact people count.  Callers
provide the physical one-tick transition so forecast and simulation obey the
same capacity and flow limits.
"""

from __future__ import annotations

from dataclasses import dataclass
import math
import random
from typing import Callable


@dataclass(frozen=True)
class ForecastConfig:
    horizon_seconds: int = 60
    lookahead_seconds: int = 15
    scenarios: int = 20
    seed: int = 20260813

    def validated(self) -> "ForecastConfig":
        horizon = max(1, min(300, int(self.horizon_seconds)))
        return ForecastConfig(
            horizon_seconds=horizon,
            lookahead_seconds=max(1, min(horizon, int(self.lookahead_seconds))),
            scenarios=max(1, min(100, int(self.scenarios))),
            seed=int(self.seed),
        )


def _percentile(values: list[float], fraction: float) -> float:
    if not values:
        return 1.0
    ordered = sorted(values)
    index = min(len(ordered) - 1, max(0, math.ceil(fraction * len(ordered)) - 1))
    return ordered[index]


class LoadForecaster:
    """Run deterministic scenario projections and expose median/P90 occupancy."""

    def __init__(self, capacities: dict[str, float], config: ForecastConfig | None = None):
        self.capacities = {edge_id: max(0.0, float(value)) for edge_id, value in capacities.items()}
        self.config = (config or ForecastConfig()).validated()

    def project(
        self,
        observed_loads: dict[str, float],
        transition: Callable[[dict[str, float], random.Random], tuple[dict[str, float], dict]],
    ) -> dict:
        """Project loads using ``transition`` without retaining state between calls.

        Recreating every trajectory from observed CSI on each invocation is
        deliberate: it prevents a stale forecast from becoming a second source
        of truth.
        """
        cfg = self.config
        trajectories: dict[str, list[list[float]]] = {
            edge_id: [[] for _ in range(cfg.horizon_seconds)] for edge_id in self.capacities
        }
        rejected = 0.0
        for scenario in range(cfg.scenarios):
            loads = {edge_id: max(0.0, float(observed_loads.get(edge_id, 0.0))) for edge_id in self.capacities}
            rng = random.Random(cfg.seed + scenario)
            for second in range(cfg.horizon_seconds):
                loads, stats = transition(loads, rng)
                rejected += float(stats.get("rejected_unsafe_deviation", 0.0))
                for edge_id, capacity in self.capacities.items():
                    ratio = 1.0 if capacity <= 0 else min(1.0, max(0.0, loads.get(edge_id, 0.0) / capacity))
                    trajectories[edge_id][second].append(ratio)

        edges = {}
        lookahead = cfg.lookahead_seconds - 1
        for edge_id, samples_by_second in trajectories.items():
            median = [_percentile(samples, 0.5) for samples in samples_by_second]
            p90 = [_percentile(samples, 0.9) for samples in samples_by_second]
            observed = 1.0 if self.capacities[edge_id] <= 0 else min(1.0, max(0.0, observed_loads.get(edge_id, 0.0) / self.capacities[edge_id]))
            edges[edge_id] = {
                "observed_k": round(observed, 4),
                "median": [round(value, 4) for value in median],
                "p90": [round(value, 4) for value in p90],
                "planning_k": round(max(observed, p90[lookahead]), 4),
                "trend": "rising" if median[lookahead] > observed + 0.02 else "falling" if median[lookahead] < observed - 0.02 else "stable",
            }
        return {
            "version": 1,
            "horizon_seconds": cfg.horizon_seconds,
            "lookahead_seconds": cfg.lookahead_seconds,
            "scenarios": cfg.scenarios,
            "confidence": "p90",
            "rejected_unsafe_deviation": round(rejected / cfg.scenarios, 4),
            "edges": edges,
        }
