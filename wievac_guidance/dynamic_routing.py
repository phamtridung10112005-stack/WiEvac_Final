"""Incremental D* Lite routing and safe dynamic corridor weights for WiEvac.

The planner intentionally uses a zero heuristic.  It is admissible and
consistent for every non-negative edge cost, while D* Lite still retains and
repairs ``g``/``rhs`` values when only a subset of corridor costs changes.
"""

from __future__ import annotations

from dataclasses import dataclass
import heapq
import math
from typing import Callable, Iterable


INF = math.inf


@dataclass(frozen=True)
class WeightParameters:
    free_walking_speed: float = 1.2
    gamma: float = 1.0
    delta: float = 2.0
    hazard_block_threshold: float = 100.0
    people_per_square_meter: float = 2.0
    specific_flow_per_meter: float = 1.3
    stair_flow_factor: float = 0.65

    def validated(self) -> "WeightParameters":
        return WeightParameters(
            free_walking_speed=max(0.1, float(self.free_walking_speed)),
            gamma=max(0.0, float(self.gamma)),
            delta=max(1.0, float(self.delta)),
            hazard_block_threshold=max(0.0, float(self.hazard_block_threshold)),
            people_per_square_meter=max(0.1, float(self.people_per_square_meter)),
            specific_flow_per_meter=max(0.01, float(self.specific_flow_per_meter)),
            stair_flow_factor=max(0.05, min(1.0, float(self.stair_flow_factor))),
        )


def corridor_capacity(edge: dict, parameters: WeightParameters) -> float:
    """Return Cmax in people, using an explicit value or a documented estimate."""
    parameters = parameters.validated()
    explicit = edge.get("capacityPeople")
    if explicit is not None:
        try:
            return float(explicit)
        except (TypeError, ValueError):
            return 0.0
    try:
        length = float(edge.get("length", 0))
        width = float(edge.get("widthMeters", 1.2))
    except (TypeError, ValueError):
        return 0.0
    return length * width * parameters.people_per_square_meter


def corridor_flow_capacity(edge: dict, areas: dict[str, dict], parameters: WeightParameters) -> float:
    """Return continuous equivalent load/second, never confusing it with storage."""
    explicit = edge.get("flowCapacity")
    try:
        flow = float(explicit) if explicit not in (None, "") else 0.0
    except (TypeError, ValueError):
        flow = 0.0
    if flow > 0:
        return flow
    try:
        width = max(0.1, float(edge.get("widthMeters", 1.2)))
    except (TypeError, ValueError):
        width = 1.2
    left, right = areas.get(edge.get("areaA_id"), {}), areas.get(edge.get("areaB_id"), {})
    is_stair = left.get("type") == "stairs" or right.get("type") == "stairs" or left.get("floor", 1) != right.get("floor", 1)
    return width * parameters.specific_flow_per_meter * (parameters.stair_flow_factor if is_stair else 1.0)


def occupancy_ratio(value: object) -> float:
    """Read a raw ratio or the normalized CSI state without treating UNKNOWN as empty."""
    if isinstance(value, dict):
        if value.get("status") in {"UNKNOWN", "STALE"}:
            return 1.0
        value = value.get("filtered_k", value.get("measured_k", 1.0))
    try:
        return max(0.0, min(1.0, float(value)))
    except (TypeError, ValueError):
        return 1.0


occupancy_ratio_fn = occupancy_ratio


def dynamic_edge_cost(
    edge: dict,
    current_people: float,
    hazard: float = 0.0,
    blocked: bool = False,
    parameters: WeightParameters | None = None,
) -> float:
    """Compute w(e)=t0*[1+gamma*(c/Cmax)^delta]+H with safe blocking rules."""
    parameters = (parameters or WeightParameters()).validated()
    if blocked:
        return INF
    try:
        people = max(0.0, float(current_people))
        danger = max(0.0, float(hazard))
        length = float(edge.get("length", 0.0))
    except (TypeError, ValueError):
        return INF
    capacity = corridor_capacity(edge, parameters)
    if not math.isfinite(people) or not math.isfinite(danger) or length <= 0 or capacity <= 0:
        return INF
    if people >= capacity or danger >= parameters.hazard_block_threshold:
        return INF
    t0 = length / parameters.free_walking_speed
    ratio = people / capacity
    return t0 * (1.0 + parameters.gamma * (ratio ** parameters.delta)) + danger


class DStarLite:
    """A D* Lite implementation for a fixed topology and changing arc costs."""

    def __init__(
        self,
        vertices: Iterable[str],
        arcs: Iterable[tuple[str, str, str]],
        start: str,
        goal: str,
        cost_of: Callable[[str], float],
    ) -> None:
        self.vertices = set(vertices)
        self.start = start
        self.goal = goal
        self.cost_of = cost_of
        self.successors: dict[str, list[tuple[str, str]]] = {vertex: [] for vertex in self.vertices}
        self.predecessors: dict[str, list[tuple[str, str]]] = {vertex: [] for vertex in self.vertices}
        self.edge_to_vertices: dict[str, tuple[str, str]] = {}
        for source, target, edge_id in arcs:
            self.successors.setdefault(source, []).append((target, edge_id))
            self.predecessors.setdefault(target, []).append((source, edge_id))
            self.edge_to_vertices[edge_id] = (source, target)
        self.g: dict[str, float] = {}
        self.rhs: dict[str, float] = {}
        self.queue: list[tuple[float, float, int, str]] = []
        self.queue_keys: dict[str, tuple[float, float]] = {}
        self._sequence = 0
        self.km = 0.0
        self.initialization_count = 0
        self.incremental_update_count = 0
        self.initialize()

    @staticmethod
    def _heuristic(_left: str, _right: str) -> float:
        return 0.0

    def calculate_key(self, vertex: str) -> tuple[float, float]:
        best = min(self.g[vertex], self.rhs[vertex])
        return (best + self._heuristic(self.start, vertex) + self.km, best)

    def initialize(self) -> None:
        self.g = {vertex: INF for vertex in self.vertices}
        self.rhs = {vertex: INF for vertex in self.vertices}
        self.rhs[self.goal] = 0.0
        self.queue = []
        self.queue_keys = {}
        self._sequence = 0
        self.initialization_count += 1
        self._push(self.goal, self.calculate_key(self.goal))

    def _push(self, vertex: str, key: tuple[float, float]) -> None:
        self._sequence += 1
        self.queue_keys[vertex] = key
        heapq.heappush(self.queue, (key[0], key[1], self._sequence, vertex))

    def _pop(self) -> tuple[tuple[float, float], str] | None:
        while self.queue:
            first, second, _sequence, vertex = heapq.heappop(self.queue)
            key = (first, second)
            if self.queue_keys.get(vertex) == key:
                self.queue_keys.pop(vertex, None)
                return key, vertex
        return None

    def _top_key(self) -> tuple[float, float]:
        while self.queue:
            first, second, _sequence, vertex = self.queue[0]
            if self.queue_keys.get(vertex) == (first, second):
                return (first, second)
            heapq.heappop(self.queue)
        return (INF, INF)

    def update_vertex(self, vertex: str) -> None:
        if vertex != self.goal:
            self.rhs[vertex] = min(
                (self.cost_of(edge_id) + self.g[target] for target, edge_id in self.successors.get(vertex, [])),
                default=INF,
            )
        self.queue_keys.pop(vertex, None)
        if not math.isclose(self.g[vertex], self.rhs[vertex], rel_tol=0.0, abs_tol=1e-10):
            self._push(vertex, self.calculate_key(vertex))

    def compute_shortest_path(self) -> bool:
        """Repair the previous search tree until start is locally consistent."""
        while self._top_key() < self.calculate_key(self.start) or not math.isclose(
            self.rhs[self.start], self.g[self.start], rel_tol=0.0, abs_tol=1e-10
        ):
            item = self._pop()
            if item is None:
                break
            old_key, vertex = item
            new_key = self.calculate_key(vertex)
            if old_key < new_key:
                self._push(vertex, new_key)
            elif self.g[vertex] > self.rhs[vertex]:
                self.g[vertex] = self.rhs[vertex]
                for predecessor, _edge_id in self.predecessors.get(vertex, []):
                    self.update_vertex(predecessor)
            else:
                self.g[vertex] = INF
                self.update_vertex(vertex)
                for predecessor, _edge_id in self.predecessors.get(vertex, []):
                    self.update_vertex(predecessor)
        return math.isfinite(self.g[self.start])

    def update_changed_edges(self, edge_ids: Iterable[str]) -> None:
        """Notify D* Lite only about arcs whose cost changed; never reinitialize."""
        affected: set[str] = set()
        for edge_id in edge_ids:
            pair = self.edge_to_vertices.get(edge_id)
            if pair:
                affected.update(pair)
        for vertex in affected:
            self.update_vertex(vertex)
            for predecessor, _edge_id in self.predecessors.get(vertex, []):
                self.update_vertex(predecessor)
        if affected:
            self.incremental_update_count += 1

    def extract_path(self) -> tuple[list[str], float]:
        if not self.compute_shortest_path():
            return [], INF
        route = [self.start]
        current = self.start
        seen = {current}
        while current != self.goal:
            candidates = [
                (self.cost_of(edge_id) + self.g[target], target, edge_id)
                for target, edge_id in self.successors.get(current, [])
            ]
            if not candidates:
                return [], INF
            cost, target, edge_id = min(candidates, key=lambda item: (item[0], item[1], item[2]))
            if not math.isfinite(cost) or target in seen:
                return [], INF
            route.append(edge_id)
            route.append(target)
            seen.add(target)
            current = target
        return route, self.g[self.start]


class DynamicEvacuationRouter:
    """Caches one genuine D* Lite state per start area for a shared exit goal."""

    VIRTUAL_EXIT = "__safe_exit__"

    def __init__(self, map_config: dict, parameters: WeightParameters | None = None) -> None:
        self.parameters = (parameters or WeightParameters()).validated()
        self.map_config = map_config
        self.areas = {area["id"]: area for area in map_config.get("areas", []) if area.get("id")}
        self.edges = {edge["id"]: edge for edge in map_config.get("edges", []) if edge.get("id")}
        self.costs: dict[str, float] = {}
        self.planners: dict[str, DStarLite] = {}
        self._build_topology()

    def _build_topology(self) -> None:
        self.vertices = set(self.areas) | {self.VIRTUAL_EXIT}
        self.arcs: list[tuple[str, str, str]] = []
        for edge_id, edge in self.edges.items():
            left, right = edge.get("areaA_id"), edge.get("areaB_id")
            if left in self.areas and right in self.areas:
                self.arcs.extend([(left, right, edge_id), (right, left, edge_id)])
        for area_id, area in self.areas.items():
            if area.get("type") == "exit":
                self.arcs.append((area_id, self.VIRTUAL_EXIT, f"exit::{area_id}"))
        self.costs = {edge_id: INF for edge_id in self.edges}
        self.costs.update({edge_id: INF for _source, _target, edge_id in self.arcs if edge_id.startswith("exit::")})

    def _cost_of(self, edge_id: str) -> float:
        return self.costs.get(edge_id, INF)

    def _planner(self, source: str) -> DStarLite:
        if source not in self.planners:
            self.planners[source] = DStarLite(self.vertices, self.arcs, source, self.VIRTUAL_EXIT, self._cost_of)
        return self.planners[source]

    def update(
        self,
        occupancy_ratio: dict[str, float],
        hazards: dict[str, float] | None = None,
        blocked_edges: set[str] | None = None,
        blocked_exits: set[str] | None = None,
    ) -> set[str]:
        hazards = hazards or {}
        blocked_edges = blocked_edges or set()
        blocked_exits = blocked_exits or set()
        changed: set[str] = set()
        for edge_id, edge in self.edges.items():
            capacity = corridor_capacity(edge, self.parameters)
            try:
                ratio = occupancy_ratio.get(edge_id, 1.0)
                ratio = occupancy_ratio_fn(ratio)
            except (TypeError, ValueError):
                ratio = 1.0
            cost = dynamic_edge_cost(
                edge,
                current_people=ratio * max(capacity, 0.0),
                hazard=hazards.get(edge_id, edge.get("hazard", 0.0)),
                blocked=edge_id in blocked_edges,
                parameters=self.parameters,
            )
            if not math.isclose(self.costs.get(edge_id, INF), cost, rel_tol=0.0, abs_tol=1e-9):
                self.costs[edge_id] = cost
                changed.add(edge_id)
        for area_id, area in self.areas.items():
            if area.get("type") != "exit":
                continue
            edge_id = f"exit::{area_id}"
            cost = INF if area_id in blocked_exits else 0.0
            if self.costs.get(edge_id) != cost:
                self.costs[edge_id] = cost
                changed.add(edge_id)
        for planner in self.planners.values():
            planner.update_changed_edges(changed)
        return changed

    def route_from(self, source: str) -> dict:
        if source not in self.areas:
            return {"path": [], "edge_ids": [], "cost": INF, "next_edge": None, "reachable": False}
        planner = self._planner(source)
        path, cost = planner.extract_path()
        edge_ids = [item for item in path if item in self.edges]
        candidates = []
        for target, edge_id in planner.successors.get(source, []):
            total_cost = self._cost_of(edge_id) + planner.g.get(target, INF)
            if target != self.VIRTUAL_EXIT and math.isfinite(total_cost):
                candidates.append({"next_area": target, "edge_id": edge_id, "cost": total_cost})
        candidates.sort(key=lambda item: (item["cost"], item["next_area"], item["edge_id"]))
        return {
            "path": path,
            "edge_ids": edge_ids,
            "cost": cost,
            "next_edge": edge_ids[0] if edge_ids else None,
            "route_candidates": candidates[:2],
            "reachable": math.isfinite(cost),
            "planner_initializations": planner.initialization_count,
            "planner_incremental_updates": planner.incremental_update_count,
        }

    def routes_for_all_areas(self) -> dict[str, dict]:
        return {area_id: self.route_from(area_id) for area_id in self.areas}

    def edge_metrics(self, occupancy_ratio: dict[str, float], hazards: dict[str, float] | None = None) -> dict[str, dict]:
        hazards = hazards or {}
        result: dict[str, dict] = {}
        for edge_id, edge in self.edges.items():
            capacity = corridor_capacity(edge, self.parameters)
            state = occupancy_ratio.get(edge_id, {})
            ratio = occupancy_ratio_fn(state)
            people = ratio * max(capacity, 0.0)
            result[edge_id] = {
                "measured_k": state.get("measured_k") if isinstance(state, dict) else ratio,
                "filtered_k": state.get("filtered_k") if isinstance(state, dict) else ratio,
                "sensorStatus": state.get("status", "OK") if isinstance(state, dict) else "OK",
                "confidence": state.get("confidence", 1.0) if isinstance(state, dict) else 1.0,
                "lastUpdated": state.get("last_updated", 0) if isinstance(state, dict) else 0,
                "estimated_load": round(people, 3),
                "currentPeople": round(people, 3),
                "capacityPeople": round(capacity, 2),
                "flowCapacity": round(corridor_flow_capacity(edge, self.areas, self.parameters), 3),
                "occupancyRatio": round(ratio, 3),
                "hazard": round(max(0.0, float(hazards.get(edge_id, edge.get("hazard", 0.0)))), 2),
                "weight": None if not math.isfinite(self.costs.get(edge_id, INF)) else round(self.costs[edge_id], 3),
                "blocked": not math.isfinite(self.costs.get(edge_id, INF)),
            }
        return result
