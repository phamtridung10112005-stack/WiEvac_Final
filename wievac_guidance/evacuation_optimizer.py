"""Small continuous MPC flow optimizer layered on top of D* Lite candidates."""

from __future__ import annotations

import math
from dataclasses import dataclass

try:
    from scipy.optimize import linprog
except ImportError:  # The caller must safely fall back when SciPy is unavailable.
    linprog = None


@dataclass(frozen=True)
class OptimizerConfig:
    horizon_seconds: float = 30.0
    timeout_seconds: float = 1.0
    route_change_penalty: float = 0.5
    min_route_improvement: float = 0.05


class OptimizerError(RuntimeError):
    pass


def fallback_routes(candidates: dict[str, list[dict]]) -> dict[str, list[dict]]:
    """Choose the lowest-cost D* Lite candidate when no allocation is available."""
    result = {}
    for area, items in candidates.items():
        if not items:
            result[area] = []
            continue
        best = items[0]
        result[area] = [{
            "next_area": best["next_area"],
            "edge_id": best["edge_id"],
            "cost": float(best["cost"]),
            "share": 1.0,
            "allocated_load": None,
        }]
    return result


class EvacuationOptimizer:
    """Linear first-step flow allocation; all physical movement remains in edge_core."""

    def __init__(self, config: OptimizerConfig | None = None):
        self.config = config or OptimizerConfig()

    @staticmethod
    def _greedy_flows(variables, area_loads, edge_limits):
        """Deterministic degraded solver for installations without SciPy.

        It allocates only D* Lite candidates, never exceeds either source load
        or edge flow limit, and is intentionally conservative rather than a
        replacement route planner.
        """
        remaining_area = {area: max(0.0, float(load)) for area, load in area_loads.items()}
        remaining_edge = {edge: max(0.0, float(limit)) for edge, limit in edge_limits.items()}
        flows = {}
        for area, target, edge, cost in sorted(variables, key=lambda item: (item[3], item[0], item[2])):
            amount = min(remaining_area.get(area, 0.0), remaining_edge.get(edge, 0.0))
            if amount <= 1e-8:
                continue
            remaining_area[area] -= amount
            remaining_edge[edge] -= amount
            flows.setdefault(area, []).append((target, edge, cost, amount))
        return flows

    def optimize(self, candidates, area_loads, edge_limits, blocked_edges=frozenset(), previous_routes=None):
        previous_routes = previous_routes or {}
        variables = []
        for area_id, options in candidates.items():
            usable = [
                item for item in options
                if item["edge_id"] not in blocked_edges and math.isfinite(item["cost"])
            ][:2]
            for item in usable:
                variables.append((area_id, item["next_area"], item["edge_id"], max(0.0, float(item["cost"]))))
        if not variables:
            return {}, "infeasible"
        # Minimize predicted travel, queue pressure, and unnecessary deviations.
        objective = []
        for area, _target, edge, cost in variables:
            prior = {
                item["edge_id"] for item in previous_routes.get(area, [])
                if item.get("share", 0) > 0
            }
            # A large evacuation reward lexicographically prioritizes moving
            # safe load before minimizing travel/route churn.
            objective.append(cost + (self.config.route_change_penalty if prior and edge not in prior else 0.0) - 1_000_000.0)
        a_ub, b_ub = [], []
        for area, load in area_loads.items():
            row = [1.0 if variable[0] == area else 0.0 for variable in variables]
            a_ub.append(row); b_ub.append(max(0.0, float(load)))
        for edge, limit in edge_limits.items():
            row = [1.0 if variable[2] == edge else 0.0 for variable in variables]
            a_ub.append(row); b_ub.append(max(0.0, float(limit)))
        if linprog is None:
            flows = self._greedy_flows(variables, area_loads, edge_limits)
        else:
            result = linprog(objective, A_ub=a_ub, b_ub=b_ub, bounds=(0, None), method="highs", options={"time_limit": self.config.timeout_seconds})
            if not result.success:
                return {}, "infeasible" if result.status == 2 else "error"
            flows = {}
            for variable, amount in zip(variables, result.x):
                if amount > 1e-8:
                    flows.setdefault(variable[0], []).append((variable[1], variable[2], variable[3], float(amount)))
        routes = {}
        for area, items in flows.items():
            total = sum(item[3] for item in items)
            if total > 0:
                routes[area] = [
                    {
                        "next_area": target,
                        "edge_id": edge,
                        "cost": cost,
                        "share": value / total,
                        "allocated_load": value,
                    }
                    for target, edge, cost, value in sorted(items, key=lambda item: -item[3])[:2]
                ]
        # Empty sources still get their safe best route for device guidance.
        for area, options in candidates.items():
            routes.setdefault(area, fallback_routes({area: options})[area])
        return routes, "optimal"
