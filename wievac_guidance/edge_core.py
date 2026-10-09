import heapq
import json
import math
import os
import random
import threading
import time

import paho.mqtt.client as mqtt

from csi_layer import CSILayer
from behavior import BehaviorConfig, distribute_choices, load_behavior_config
from dynamic_routing import DynamicEvacuationRouter, WeightParameters, corridor_capacity, corridor_flow_capacity, dynamic_edge_cost
from evacuation_optimizer import EvacuationOptimizer, OptimizerConfig, OptimizerError, fallback_routes
from forecasting import ForecastConfig, LoadForecaster
from guidance_controller import GuidanceController


MQTT_BROKER = os.getenv("WIEVAC_MQTT_HOST", "127.0.0.1")
MQTT_PORT = int(os.getenv("WIEVAC_MQTT_PORT", "1883"))
CONFIG_FILE = os.path.join(os.path.dirname(__file__), "config.json")
BEHAVIOR_FILE = os.path.join(os.path.dirname(__file__), "simulation_behavior.json")

# k(e) is the normalized CSI-derived occupancy of corridor e:
# 0.0 = empty corridor, 1.0 = fully occupied corridor.
TICK_SECONDS = 1.0
FREE_WALKING_SPEED = 1.2
DEFAULT_INITIAL_OCCUPANCY = (0.20, 0.75)
GAMMA = max(0.0, float(os.getenv("WIEVAC_GAMMA", "1.0")))
DELTA = max(1.0, float(os.getenv("WIEVAC_DELTA", "2.0")))
ROUTE_CHANGE_PENALTY = float(os.getenv("WIEVAC_ROUTE_CHANGE_PENALTY", "0.5"))
LOGIT_THETA = 0.35
OCCUPANCY_EPSILON = 0.005
DEFAULT_CORRIDOR_WIDTH_METERS = 1.2
CAPACITY_BETA = 1.0
STAIR_CAPACITY_FACTOR = 0.65
MAX_GUIDANCE_ROUTES = 2
MIN_SECONDARY_ROUTE_SHARE = 0.10
MIN_RECEIVING_SCORE = 0.01

map_config = {"areas": [], "edges": []}
routing_parameters = WeightParameters(
    free_walking_speed=float(os.getenv("WIEVAC_FREE_WALKING_SPEED", str(FREE_WALKING_SPEED))),
    gamma=GAMMA,
    delta=DELTA,
    hazard_block_threshold=float(os.getenv("WIEVAC_HAZARD_BLOCK_THRESHOLD", "100")),
    people_per_square_meter=float(os.getenv("WIEVAC_PEOPLE_PER_SQM", "2.0")),
    specific_flow_per_meter=float(os.getenv("WIEVAC_SPECIFIC_FLOW_PER_METER", "1.3")),
    stair_flow_factor=float(os.getenv("WIEVAC_STAIR_FLOW_FACTOR", "0.65")),
).validated()
routing_service = None
simulation_active = False
simulation_thread = None
simulation_stop_event = threading.Event()
shutting_down = False
blocked_edges = set()
blocked_exits = set()
edge_hazards = {}

state_lock = threading.RLock()
edge_occupancy = {}
edge_states = {}
edge_loads = {}
previous_next_edge = {}
previous_route_changed_at = {}
previous_route_decisions = {}
pending_occupancy_updates = []
latest_sensor_occupancy = {}
simulation_started_at = None
elapsed_before_pause = 0
simulation_step = 0
evacuated_load = 0.0
initial_total_load = 0.0
measurement_correction = 0.0
optimizer_status = "fallback"
latest_forecast = {"version": 1, "edges": {}}
behavior_config, behavior_settings = load_behavior_config(BEHAVIOR_FILE)
last_behavior_metrics = {"deviated_load": 0.0, "rejected_unsafe_deviation": 0.0}
last_movement_by_corridor = {}
guidance_controller = GuidanceController()
csi_layer = CSILayer(
    noise_level=0.03,
    ema_alpha=float(os.getenv("WIEVAC_CSI_EMA_ALPHA", "0.35")),
    stale_seconds=float(os.getenv("WIEVAC_CSI_STALE_SECONDS", "8")),
)
optimizer = EvacuationOptimizer(OptimizerConfig(
    horizon_seconds=float(os.getenv("WIEVAC_OPTIMIZER_HORIZON_SECONDS", "30")),
    timeout_seconds=float(os.getenv("WIEVAC_OPTIMIZER_TIMEOUT_SECONDS", "1")),
    route_change_penalty=ROUTE_CHANGE_PENALTY,
    min_route_improvement=float(os.getenv("WIEVAC_MIN_ROUTE_IMPROVEMENT", "0.05")),
))


def clamp_occupancy(value):
    try:
        return max(0.0, min(1.0, float(value)))
    except (TypeError, ValueError):
        return 0.0


def refresh_occupancy_from_loads():
    """Compatibility view for MQTT/UI; movement itself always uses load units."""
    for edge in map_config.get("edges", []):
        edge_id = edge["id"]
        capacity = corridor_capacity(edge, routing_parameters)
        edge_occupancy[edge_id] = 0.0 if capacity <= 0 else clamp_occupancy(edge_loads.get(edge_id, 0.0) / capacity)


def edge_state_snapshot():
    result = csi_layer.all_states(edge["id"] for edge in map_config.get("edges", []))
    for edge in map_config.get("edges", []):
        edge_id = edge["id"]
        state = result.get(edge_id, {})
        capacity = corridor_capacity(edge, routing_parameters)
        result[edge_id] = {**state, "estimated_load": edge_loads.get(edge_id, 0.0), "capacity": capacity}
    return result


def forecast_config() -> ForecastConfig:
    return ForecastConfig(
        horizon_seconds=behavior_settings.get("forecast_horizon_seconds", 60),
        lookahead_seconds=behavior_settings.get("forecast_lookahead_seconds", 15),
        scenarios=behavior_settings.get("forecast_scenarios", 20),
        seed=behavior_settings.get("scenario_seed", 20260813),
    ).validated()


def planning_occupancy(observed: dict[str, dict], forecast: dict) -> dict[str, dict]:
    """Use forecast P90 for planning while retaining CSI state for observability."""
    result = {edge_id: dict(state) for edge_id, state in observed.items()}
    for edge_id, projection in forecast.get("edges", {}).items():
        state = result.get(edge_id, {})
        if state.get("status") in {"UNKNOWN", "STALE"}:
            continue
        planned = max(clamp_occupancy(state.get("filtered_k", 1.0)), clamp_occupancy(projection.get("planning_k", 1.0)))
        result[edge_id] = {**state, "planning_k": planned, "filtered_k": planned}
    return result


def load_config():
    global map_config, routing_service
    try:
        with open(CONFIG_FILE, "r", encoding="utf-8") as file:
            map_config = json.load(file)
            routing_service = DynamicEvacuationRouter(map_config, routing_parameters)
            guidance_controller.configure(map_config)
            print("Loaded config from file.")
    except Exception as exc:
        print("Could not load config file, starting empty:", exc)
        map_config = {"areas": [], "edges": []}


def save_config(data):
    try:
        with open(CONFIG_FILE, "w", encoding="utf-8") as file:
            json.dump(data, file, indent=2, ensure_ascii=False)
        print("Saved new config to file.")
    except Exception as exc:
        print("Error saving config:", exc)


def calculate_dynamic_weight(edge, k, switching=False):
    """Safe required weight: t0 * [1 + gamma * (c/Cmax)^delta] + H."""
    capacity = corridor_capacity(edge, routing_parameters)
    return dynamic_edge_cost(
        edge,
        current_people=clamp_occupancy(k) * capacity,
        hazard=edge_hazards.get(edge.get("id"), edge.get("hazard", 0.0)),
        blocked=edge.get("id") in blocked_edges,
        parameters=routing_parameters,
    )


def calculate_receiving_capacity(edge, k):
    """Relative ability of an edge to receive more evacuees.

    CSI supplies normalized occupancy k(e), while physical width supplies the
    missing absolute scale. This is intentionally a relative capacity until
    field measurements provide a calibrated people/second coefficient.
    """
    try:
        width = float(edge.get("widthMeters", DEFAULT_CORRIDOR_WIDTH_METERS))
    except (TypeError, ValueError):
        width = DEFAULT_CORRIDOR_WIDTH_METERS
    width = max(0.1, width)
    openness = max(0.0, 1.0 - clamp_occupancy(k)) ** CAPACITY_BETA

    area_map = {
        area["id"]: area for area in map_config.get("areas", [])
    }
    area_a = area_map.get(edge.get("areaA_id"), {})
    area_b = area_map.get(edge.get("areaB_id"), {})
    is_stair_connection = (
        (
            area_a.get("type") == "stairs"
            and area_b.get("type") == "stairs"
        )
        or area_a.get("floor", 1) != area_b.get("floor", 1)
    )
    type_factor = STAIR_CAPACITY_FACTOR if is_stair_connection else 1.0
    return max(MIN_RECEIVING_SCORE, width * openness * type_factor)


def build_graph():
    areas = map_config.get("areas", [])
    edges = map_config.get("edges", [])
    graph = {area["id"]: [] for area in areas}
    edge_map = {edge["id"]: edge for edge in edges}
    for edge in edges:
        edge_id = edge.get("id")
        if edge_id in blocked_edges:
            continue
        area_a = edge.get("areaA_id")
        area_b = edge.get("areaB_id")
        if area_a in graph and area_b in graph:
            graph[area_a].append((area_b, edge_id))
            graph[area_b].append((area_a, edge_id))
    return graph, edge_map


def compute_routes(graph, edge_map, occupancy):
    """Use persistent D* Lite states and only repair changed corridor costs."""
    global routing_service
    if routing_service is None:
        routing_service = DynamicEvacuationRouter(map_config, routing_parameters)
    routing_service.update(occupancy, edge_hazards, blocked_edges, blocked_exits)
    exits = {
        area["id"] for area in map_config.get("areas", [])
        if area.get("type") == "exit" and area["id"] not in blocked_exits
    }
    distances = {}
    route_options = {}
    for area_id, route in routing_service.routes_for_all_areas().items():
        distances[area_id] = route["cost"]
        route_options[area_id] = route.get("route_candidates", [])
    return exits, distances, route_options


def optimize_routes(route_candidates, edge_map):
    """Use global LP ratios when possible; D* Lite remains the safe fallback."""
    global optimizer_status, previous_route_decisions
    areas = {area["id"]: area for area in map_config.get("areas", [])}
    area_loads = {area_id: 0.0 for area_id in areas}
    for edge_id, load in edge_loads.items():
        edge = edge_map.get(edge_id, {})
        for endpoint in (edge.get("areaA_id"), edge.get("areaB_id")):
            if endpoint in area_loads:
                area_loads[endpoint] += load / 2.0
    edge_limits = {
        edge_id: min(corridor_flow_capacity(edge, areas, routing_parameters) * TICK_SECONDS,
                     max(0.0, corridor_capacity(edge, routing_parameters) - edge_loads.get(edge_id, 0.0)))
        for edge_id, edge in edge_map.items() if edge_id not in blocked_edges
    }
    try:
        routes, status = optimizer.optimize(
            route_candidates, area_loads, edge_limits, blocked_edges, previous_route_decisions
        )
        if status != "optimal":
            raise OptimizerError(status)
        minimum_gain = float(os.getenv("WIEVAC_MIN_ROUTE_IMPROVEMENT", "0.05"))
        hold_seconds = float(os.getenv("WIEVAC_REPLAN_INTERVAL_SECONDS", "1"))
        now = time.monotonic()
        for area_id, selected in routes.items():
            old_edge = previous_next_edge.get(area_id)
            if not selected:
                continue
            selected_edge = selected[0]["edge_id"]
            if not old_edge:
                previous_next_edge[area_id] = selected_edge
                continue
            if selected_edge == old_edge:
                continue
            old_candidate = next(
                (item for item in route_candidates.get(area_id, []) if item["edge_id"] == old_edge), None
            )
            new_candidate = next(
                (item for item in route_candidates.get(area_id, []) if item["edge_id"] == selected_edge), None
            )
            if old_candidate and new_candidate and old_edge not in blocked_edges and (
                (old_candidate["cost"] - new_candidate["cost"]) < minimum_gain
                or now - previous_route_changed_at.get(area_id, 0) < hold_seconds
            ):
                routes[area_id] = [{
                    "next_area": old_candidate["next_area"],
                    "edge_id": old_edge,
                    "cost": old_candidate["cost"],
                    "share": 1.0,
                    "allocated_load": None,
                }]
            if routes[area_id][0]["edge_id"] != old_edge:
                previous_route_changed_at[area_id] = now
            previous_next_edge[area_id] = routes[area_id][0]["edge_id"]
        previous_route_decisions = {area: list(items) for area, items in routes.items()}
        optimizer_status = "optimal"
        return routes
    except Exception as exc:
        optimizer_status = "fallback"
        print(f"[OPTIMIZER] fallback: {exc}")
        return fallback_routes(route_candidates)


def make_log(event_type, message):
    return {
        "type": event_type,
        "message": message,
        "time": time.strftime("%H:%M:%S"),
    }


def publish_log(client, events, step=None):
    if events:
        client.publish(
            "building/log",
            json.dumps({"step": step, "events": events}, ensure_ascii=False),
        )


def queue_occupancy_update(edge_id, value, mode="set"):
    with state_lock:
        pending_occupancy_updates.append(
            {"edge_id": edge_id, "value": float(value), "mode": mode}
        )


def apply_pending_occupancy_updates(client, edge_map):
    global measurement_correction, edge_states
    events = []
    with state_lock:
        updates = list(pending_occupancy_updates)
        pending_occupancy_updates.clear()
        for update in updates:
            edge_id = update.get("edge_id")
            if edge_id not in edge_occupancy:
                continue
            old_load = edge_loads.get(edge_id, 0.0)
            capacity = corridor_capacity(edge_map[edge_id], routing_parameters)
            old_k = 0.0 if capacity <= 0 else old_load / capacity
            if update.get("mode") == "delta":
                new_k = clamp_occupancy(old_k + update.get("value", 0.0))
            else:
                new_k = clamp_occupancy(update.get("value", old_k))
            new_load = new_k * capacity
            edge_loads[edge_id] = new_load
            measurement_correction += new_load - old_load
            csi_layer.update(edge_id, new_k, confidence=1.0)
            refresh_occupancy_from_loads()
            edge_states = edge_state_snapshot()
            events.append(
                make_log(
                    "adjustment",
                    f"{edge_map.get(edge_id, {}).get('name', edge_id)}: k(e) thay đổi từ {old_k * 100:.0f}% thành {new_k * 100:.0f}%.",
                )
            )
    publish_log(client, events)


def publish_state(client, status, step, trapped_ids=None, message=None):
    edge_map = {edge["id"]: edge for edge in map_config.get("edges", [])}
    trapped_ids = trapped_ids or []
    with state_lock:
        values = list(edge_occupancy.values())
        elapsed = elapsed_before_pause + (
            max(0, int(time.time() - simulation_started_at))
            if simulation_started_at
            else 0
        )
        metrics = (
            routing_service.edge_metrics(edge_states or edge_state_snapshot(), edge_hazards)
            if routing_service
            else {}
        )
        payload = {
            "status": status,
            "step": step,
            "elapsedSeconds": elapsed,
            "initialEstimatedLoad": round(initial_total_load, 3),
            "measurementCorrection": round(measurement_correction, 3),
            "evacuatedEstimatedLoad": round(evacuated_load, 3),
            "remainingEstimatedLoad": round(sum(edge_loads.values()), 3),
            "trappedEstimatedLoad": round(sum(edge_loads.get(edge_id, 0.0) for edge_id in trapped_ids), 3),
            "conservationError": round(initial_total_load + measurement_correction - evacuated_load - sum(edge_loads.values()), 8),
            "initialLoad": round(initial_total_load, 3), "evacuatedLoad": round(evacuated_load, 3), "remainingLoad": round(sum(edge_loads.values()), 3),
            "averageOccupancy": round(sum(values) / len(values), 3) if values else 0.0,
            "occupiedCorridors": sum(value > OCCUPANCY_EPSILON for value in values),
            "estimatedPeople": round(sum(item["currentPeople"] for item in metrics.values()), 1),
            "availableExits": sum(
                1 for area in map_config.get("areas", [])
                if area.get("type") == "exit" and area.get("id") not in blocked_exits
            ),
            "hazardousCorridors": sum(
                1 for item in metrics.values()
                if item.get("hazard", 0) > 0 or item.get("blocked")
            ),
            "edgeMetrics": metrics,
            "movementByCorridor": {
                edge_id: {area_id: round(load, 4) for area_id, load in directions.items()}
                for edge_id, directions in last_movement_by_corridor.items()
            },
            "forecast": latest_forecast,
            "behavior": {
                "enabled": behavior_config.enabled,
                "guidanceCompliance": behavior_config.guidance_compliance,
                "automaticMix": {
                    "familiar": round(behavior_config.familiar_route_weight, 3),
                    "followCrowd": round(behavior_config.follow_crowd_weight, 3),
                    "randomSafe": round(behavior_config.random_safe_route_weight, 3),
                },
                **last_behavior_metrics,
            },
            "routingAlgorithm": "D* Lite",
            "optimizerStatus": optimizer_status,
            "lastUpdated": int(time.time()),
            "trappedCorridors": [
                {
                    "id": edge_id,
                    "name": edge_map.get(edge_id, {}).get("name", edge_id),
                    "k": round(edge_occupancy.get(edge_id, 0.0), 3),
                }
                for edge_id in trapped_ids
            ],
            "edgeOccupancy": {
                edge_id: round(value, 3)
                for edge_id, value in edge_occupancy.items()
            },
            "edgeStates": edge_states or edge_state_snapshot(),
        }
        if message:
            payload["message"] = message
    client.publish("building/simulation/state", json.dumps(payload, ensure_ascii=False))


def initialize_simulation():
    global edge_occupancy, edge_loads, edge_states, previous_next_edge, previous_route_changed_at, previous_route_decisions
    global simulation_started_at, elapsed_before_pause, simulation_step
    global evacuated_load, initial_total_load, measurement_correction, behavior_config, behavior_settings, last_movement_by_corridor

    # Operators control only compliance.  Each newly started test run samples
    # a stable mix of the three non-compliance behaviours.
    entropy = random.SystemRandom()
    raw_mix = [entropy.random() for _ in range(3)]
    mix_total = sum(raw_mix) or 1.0
    behavior_settings = {
        **behavior_settings,
        "enabled": True,
        "familiar_route_weight": raw_mix[0] / mix_total,
        "follow_crowd_weight": raw_mix[1] / mix_total,
        "random_safe_route_weight": raw_mix[2] / mix_total,
    }
    behavior_config = BehaviorConfig.from_mapping(behavior_settings)

    edge_occupancy = {}
    edge_loads = {}
    for edge in map_config.get("edges", []):
        edge_id = edge["id"]
        if edge_id in latest_sensor_occupancy:
            value = latest_sensor_occupancy[edge_id]
        elif edge.get("initialOccupancy") is not None:
            value = edge.get("initialOccupancy")
        else:
            value = random.uniform(*DEFAULT_INITIAL_OCCUPANCY)
        ratio = clamp_occupancy(value)
        capacity = corridor_capacity(edge, routing_parameters)
        edge_loads[edge_id] = ratio * capacity
        csi_layer.update(edge_id, ratio, confidence=0.8)
    refresh_occupancy_from_loads()
    edge_states = edge_state_snapshot()

    previous_next_edge = {}
    previous_route_changed_at = {}
    previous_route_decisions = {}
    last_movement_by_corridor = {}
    evacuated_load = 0.0
    measurement_correction = 0.0
    initial_total_load = sum(edge_loads.values())
    simulation_started_at = time.time()
    elapsed_before_pause = 0
    simulation_step = 0


def select_egress_options(edge, exits, distances, route_options):
    """Return both usable endpoints and their probabilities.

    This is especially important after an incident: no new load may enter the
    blocked corridor, but load already present can still retreat to either end.
    """
    endpoints = (edge.get("areaA_id"), edge.get("areaB_id"))
    candidates = []
    for endpoint in endpoints:
        if endpoint in exits:
            candidates.append((endpoint, [], distances.get(endpoint, 0.0)))
            continue
        choices = [
            option
            for option in route_options.get(endpoint, [])
            if option["edge_id"] != edge.get("id")
        ]
        if choices and math.isfinite(distances.get(endpoint, math.inf)):
            candidates.append((endpoint, choices, distances[endpoint]))
    if not candidates:
        return []

    minimum = min(candidate[2] for candidate in candidates)
    scores = [
        math.exp(-LOGIT_THETA * (candidate[2] - minimum))
        for candidate in candidates
    ]
    total_score = sum(scores)
    return [
        (candidate[0], candidate[1], score / total_score)
        for candidate, score in zip(candidates, scores)
    ]


def advance_loads(edge_map_all, exits, distances, route_options):
    """Apply one simultaneous, flow-limited physical tick in equivalent load units."""
    global evacuated_load, last_behavior_metrics, last_movement_by_corridor
    old = dict(edge_loads)
    nxt = dict(old)
    trapped = set()
    intents = []
    last_behavior_metrics = {"deviated_load": 0.0, "rejected_unsafe_deviation": 0.0}
    area_map = {area["id"]: area for area in map_config.get("areas", [])}
    for edge_id, source_load in old.items():
        if source_load <= OCCUPANCY_EPSILON:
            continue
        source = edge_map_all[edge_id]
        options = select_egress_options(source, exits, distances, route_options)
        if not options:
            trapped.add(edge_id); continue
        source_limit = corridor_flow_capacity(source, {a["id"]: a for a in map_config.get("areas", [])}, routing_parameters) * TICK_SECONDS
        budget = min(source_load, source_limit)
        for endpoint, choices, endpoint_share in options:
            requested_endpoint = budget * endpoint_share
            if endpoint in exits:
                intents.append({"source": edge_id, "endpoint": endpoint, "target": None, "requested": requested_endpoint})
                continue
            selected_choices, rejected_fraction = distribute_choices(
                choices[:2], endpoint, behavior_config, random, old
            )
            if rejected_fraction:
                rejected_load = requested_endpoint * rejected_fraction
                last_behavior_metrics["rejected_unsafe_deviation"] += rejected_load
                requested_endpoint -= rejected_load
            for choice, share in selected_choices:
                base_share = max(0.0, float(choice.get("share", 0.0)))
                if abs(share - base_share) > 1e-8:
                    last_behavior_metrics["deviated_load"] += requested_endpoint * abs(share - base_share) / 2.0
                intents.append({
                    "source": edge_id,
                    "endpoint": endpoint,
                    "target": choice["edge_id"],
                    "requested": requested_endpoint * share,
                    "allocation": choice.get("allocated_load"),
                })

    # Optimizer allocation is shared by every source corridor touching an area.
    by_area_target = {}
    for intent in intents:
        if intent["target"] is not None:
            by_area_target.setdefault((intent["endpoint"], intent["target"]), []).append(intent)
    for group in by_area_target.values():
        allocation = next((item.get("allocation") for item in group if item.get("allocation") is not None), None)
        requested = sum(item["requested"] for item in group)
        if allocation is not None and requested > allocation and requested > 0:
            scale = allocation / requested
            for item in group:
                item["requested"] *= scale

    # Storage and flow are both shared across every simultaneous arrival.
    by_target = {}
    for intent in intents:
        if intent["target"] is not None:
            by_target.setdefault(intent["target"], []).append(intent)
    for target_edge_id, group in by_target.items():
        target = edge_map_all[target_edge_id]
        requested = sum(item["requested"] for item in group)
        storage = max(0.0, corridor_capacity(target, routing_parameters) - old.get(target_edge_id, 0.0))
        flow = corridor_flow_capacity(target, area_map, routing_parameters) * TICK_SECONDS
        scale = 0.0 if requested <= 0 else min(requested, storage, flow) / requested
        for item in group:
            item["accepted"] = item["requested"] * scale

    movement_by_corridor = {}
    for intent in intents:
        accepted = intent.get("accepted", intent["requested"] if intent["target"] is None else 0.0)
        if accepted <= 0:
            continue
        endpoint = intent.get("endpoint")
        if endpoint:
            movement_by_corridor.setdefault(intent["source"], {}).setdefault(endpoint, 0.0)
            movement_by_corridor[intent["source"]][endpoint] += accepted
        nxt[intent["source"]] = max(0.0, nxt[intent["source"]] - accepted)
        if intent["target"] is None:
            evacuated_load += accepted
        else:
            nxt[intent["target"]] += accepted
    edge_loads.update(nxt)
    refresh_occupancy_from_loads()
    last_movement_by_corridor = movement_by_corridor
    return trapped


def compute_forecast(edge_map_all, exits, distances, route_options, observed_loads=None):
    """Project from current CSI-corrected load without allowing forecast drift.

    The transition reuses the physical simulator and restores all live state
    after every scenario step, so storage/flow safety rules remain identical.
    """
    global edge_loads, edge_occupancy, evacuated_load, last_behavior_metrics, last_movement_by_corridor
    capacities = {edge_id: corridor_capacity(edge, routing_parameters) for edge_id, edge in edge_map_all.items()}
    forecaster = LoadForecaster(capacities, forecast_config())
    initial = dict(observed_loads if observed_loads is not None else edge_loads)

    def transition(loads, rng):
        global edge_loads, edge_occupancy, evacuated_load, last_behavior_metrics, last_movement_by_corridor
        saved_loads, saved_occupancy = edge_loads, edge_occupancy
        saved_evacuated, saved_metrics = evacuated_load, last_behavior_metrics
        saved_movement = last_movement_by_corridor
        original_random = random
        try:
            # distribute_choices accepts the module-level random interface;
            # swapping it for a seeded scenario keeps the forecast reproducible.
            globals()["random"] = rng
            edge_loads = dict(loads)
            edge_occupancy = {}
            refresh_occupancy_from_loads()
            trapped = advance_loads(edge_map_all, exits, distances, route_options)
            return dict(edge_loads), {**last_behavior_metrics, "trapped": len(trapped)}
        finally:
            edge_loads, edge_occupancy = saved_loads, saved_occupancy
            evacuated_load, last_behavior_metrics, last_movement_by_corridor = saved_evacuated, saved_metrics, saved_movement
            globals()["random"] = original_random

    return forecaster.project(initial, transition)


def simulation_loop(client, resume=False):
    global simulation_active, evacuated_load, simulation_started_at, simulation_step, edge_states, latest_forecast
    areas = map_config.get("areas", [])
    edges = map_config.get("edges", [])
    edge_map_all = {edge["id"]: edge for edge in edges}

    if not areas or not edges:
        simulation_active = False
        publish_state(client, "error", 0, message="Bản đồ đang trống.")
        return
    if not any(area.get("type") == "exit" for area in areas):
        simulation_active = False
        publish_state(client, "error", 0, message="Bản đồ không có lối thoát.")
        return

    if resume and edge_occupancy:
        simulation_started_at = time.time()
    else:
        initialize_simulation()
    csi = CSILayer(noise_level=0.03)
    step = simulation_step
    last_trapped_ids = set()
    action = "Resumed" if resume else "Started"
    print(f"[SIM] {action} with equivalent corridor load {sum(edge_occupancy.values()):.2f}.")

    with state_lock:
        graph, edge_map = build_graph()
        observed_states = edge_states or edge_state_snapshot()
        exits, distances, route_options = compute_routes(
            graph, edge_map, observed_states
        )
        latest_forecast = compute_forecast(edge_map_all, exits, distances, route_options)
        exits, distances, route_options = compute_routes(
            graph, edge_map, planning_occupancy(observed_states, latest_forecast)
        )
        route_options = optimize_routes(route_options, edge_map_all)

    guidance_controller.update(
        client,
        route_options,
        distances,
        dict(edge_occupancy),
        exits,
        blocked_edges,
    )
    publish_state(client, "running", step)

    while simulation_active and not shutting_down:
        tick_started = time.monotonic()
        step += 1
        simulation_step = step
        events = []
        apply_pending_occupancy_updates(client, edge_map_all)

        with state_lock:
            graph, edge_map = build_graph()
            observed_states = edge_states or edge_state_snapshot()
            exits, distances, route_options = compute_routes(
                graph, edge_map, observed_states
            )
            # The first D* Lite pass supplies safe candidate routes. Forecasts
            # only raise their costs for the second pass; they never invent a
            # route or overrule a blocked/unknown measurement.
            latest_forecast = compute_forecast(edge_map_all, exits, distances, route_options)
            exits, distances, route_options = compute_routes(
                graph, edge_map, planning_occupancy(observed_states, latest_forecast)
            )
            route_options = optimize_routes(route_options, edge_map_all)
            trapped_ids = advance_loads(edge_map_all, exits, distances, route_options)

            for edge_id in trapped_ids - last_trapped_ids:
                events.append(
                    make_log(
                        "trapped",
                        f"CẢNH BÁO: hành lang '{edge_map_all[edge_id].get('name', edge_id)}' có k={edge_occupancy[edge_id] * 100:.0f}% nhưng không còn tuyến tới lối thoát.",
                    )
                )
            for edge_id in last_trapped_ids - trapped_ids:
                events.append(
                    make_log(
                        "routing",
                        f"Đã khôi phục tuyến thoát cho hành lang '{edge_map_all[edge_id].get('name', edge_id)}'.",
                    )
                )
            last_trapped_ids = set(trapped_ids)

            theoretical_occupancy = dict(edge_occupancy)
            edge_states = edge_state_snapshot()

        guidance_controller.update(
            client,
            route_options,
            distances,
            theoretical_occupancy,
            exits,
            blocked_edges,
        )
        csi_layer.process_edge_data(theoretical_occupancy)
        edge_states = edge_state_snapshot()
        sensed_occupancy = {edge_id: round(state.get("filtered_k", 1.0), 3) for edge_id, state in edge_states.items() if state.get("filtered_k") is not None}
        if not simulation_active:
            break
        client.publish(
            "building/occupancy", json.dumps(sensed_occupancy, ensure_ascii=False)
        )
        publish_log(client, events, step)
        publish_state(client, "running", step, sorted(last_trapped_ids))

        with state_lock:
            active_reachable_loads = [
                load
                for edge_id, load in edge_loads.items()
                if edge_id not in last_trapped_ids and load > OCCUPANCY_EPSILON
            ]
            reachable_load = sum(active_reachable_loads)
            trapped_load = sum(
                edge_loads.get(edge_id, 0.0)
                for edge_id in last_trapped_ids
            )

        if reachable_load <= OCCUPANCY_EPSILON:
            simulation_active = False
            if trapped_load > OCCUPANCY_EPSILON:
                message = "Mô phỏng dừng: còn hành lang có độ lấp đầy nhưng không còn tuyến tới lối thoát."
                publish_log(client, [make_log("trapped", message)], step)
                publish_state(
                    client, "trapped", step, sorted(last_trapped_ids), message
                )
            else:
                message = "Sơ tán hoàn tất: 100% người đã đến lối thoát an toàn."
                with state_lock:
                    for edge_id in list(edge_loads.keys()):
                        if edge_id not in last_trapped_ids:
                            evacuated_load += edge_loads[edge_id]
                            edge_loads[edge_id] = 0.0
                    for edge_id in list(edge_occupancy.keys()):
                        if edge_id not in last_trapped_ids:
                            edge_occupancy[edge_id] = 0.0
                publish_log(client, [make_log("complete", message)], step)
                guidance_controller.stop_all(client)
                publish_state(client, "completed", step, message=message)
                client.publish(
                    "building/occupancy",
                    json.dumps({edge["id"]: 0.0 for edge in edges}),
                )

                def schedule_auto_reset(step_to_reset):
                    time.sleep(3.5)
                    with state_lock:
                        if not simulation_active and simulation_step == step_to_reset:
                            reset_simulation(client)

                threading.Thread(
                    target=schedule_auto_reset, args=(step,), daemon=True
                ).start()
            break

        sleep_for = TICK_SECONDS - (time.monotonic() - tick_started)
        if sleep_for > 0:
            simulation_stop_event.wait(timeout=sleep_for)


def stop_simulation(client):
    global simulation_active, simulation_thread
    global simulation_started_at, elapsed_before_pause
    was_running = simulation_active
    simulation_stop_event.set()
    with state_lock:
        simulation_active = False
        if simulation_started_at is not None:
            elapsed_before_pause += max(
                0, int(time.time() - simulation_started_at)
            )
            simulation_started_at = None
    if (
        simulation_thread
        and simulation_thread.is_alive()
        and simulation_thread is not threading.current_thread()
    ):
        simulation_thread.join(timeout=1.0)
    if was_running:
        publish_log(
            client,
            [make_log("stopped", "Mô phỏng đã được dừng bởi người vận hành.")],
        )
    # Note: guidance decisions remain frozen/visible during pause so the operator can inspect routes.
    publish_state(
        client, "stopped", simulation_step, message="Mô phỏng đã dừng."
    )


def reset_simulation(client):
    global simulation_active, simulation_thread
    global edge_occupancy, edge_loads, edge_states, previous_next_edge, pending_occupancy_updates
    global latest_sensor_occupancy, simulation_started_at
    global elapsed_before_pause, simulation_step, evacuated_load, initial_total_load, last_movement_by_corridor

    simulation_active = False
    if (
        simulation_thread
        and simulation_thread.is_alive()
        and simulation_thread is not threading.current_thread()
    ):
        simulation_thread.join(timeout=2.0)

    with state_lock:
        edge_occupancy = {
            edge["id"]: 0.0 for edge in map_config.get("edges", [])
        }
        edge_loads = {edge["id"]: 0.0 for edge in map_config.get("edges", [])}
        edge_states = {
            edge["id"]: {"measured_k": 0.0, "filtered_k": 0.0, "status": "OK", "confidence": 1.0, "last_updated": time.time()}
            for edge in map_config.get("edges", [])
        }
        previous_next_edge = {}
        pending_occupancy_updates = []
        latest_sensor_occupancy = {}
        simulation_started_at = None
        elapsed_before_pause = 0
        simulation_step = 0
        evacuated_load = 0.0
        initial_total_load = 0.0
        last_movement_by_corridor = {}

    guidance_controller.stop_all(client)
    client.publish(
        "building/occupancy",
        json.dumps(edge_occupancy, ensure_ascii=False),
    )
    if routing_service:
        client.publish(
            "building/occupancy/state",
            json.dumps({"edgeMetrics": routing_service.edge_metrics(edge_states, edge_hazards)}, ensure_ascii=False),
        )
    publish_log(
        client,
        [make_log("reset", "Mô phỏng đã reset, sẵn sàng cho lượt chạy mới.")],
    )
    publish_state(
        client,
        "idle",
        0,
        message="Đã reset. Nhấn Chạy mô phỏng để bắt đầu lượt mới.",
    )


def publish_live_guidance(client):
    """Recompute device commands directly from the latest real CSI values."""
    global latest_forecast
    if not map_config.get("areas") or not map_config.get("edges"):
        return
    if simulation_active or simulation_step > 0 or elapsed_before_pause > 0:
        return
    occupancy = csi_layer.all_states(edge["id"] for edge in map_config.get("edges", []))
    # When no live CSI sensor hardware is transmitting, use baseline edge_states so idle / unmonitored
    # corridors are treated with their known baseline occupancy, not falsely assumed to be 100% full/blocked.
    if not any(s.get("status") == "OK" for s in occupancy.values()):
        occupancy = {
            edge["id"]: edge_states.get(
                edge["id"],
                {"measured_k": 0.0, "filtered_k": 0.0, "status": "OK", "confidence": 1.0, "last_updated": time.time()}
            )
            for edge in map_config.get("edges", [])
        }
    graph, edge_map = build_graph()
    exits, distances, route_options = compute_routes(
        graph, edge_map, occupancy
    )
    all_edges = {edge["id"]: edge for edge in map_config.get("edges", [])}
    observed_loads = {
        edge_id: (
            1.0 if state.get("status") in {"UNKNOWN", "STALE"}
            else clamp_occupancy(state.get("filtered_k", 1.0))
        ) * corridor_capacity(all_edges[edge_id], routing_parameters)
        for edge_id, state in occupancy.items()
        if edge_id in all_edges
    }
    latest_forecast = compute_forecast(all_edges, exits, distances, route_options, observed_loads)
    exits, distances, route_options = compute_routes(
        graph, edge_map, planning_occupancy(occupancy, latest_forecast)
    )
    # Real CSI provides no trustworthy area loads for an LP split.  The safe
    # fallback is the single lowest-cost D* Lite route, never a cost-as-share.
    guidance_controller.update(
        client,
        fallback_routes(route_options),
        distances,
        occupancy,
        exits,
        blocked_edges,
    )
    client.publish(
        "building/occupancy/state",
        json.dumps({"edgeMetrics": routing_service.edge_metrics(occupancy, edge_hazards)}, ensure_ascii=False),
    )


standalone_thread = None


def standalone_guidance_loop(client):
    """Continuous evaluation loop for standalone deployment on Raspberry Pi.

    When no external PC simulation is active, this loop runs periodically,
    evaluating real CSI corridor occupancy, updating D* Lite routes, and
    publishing active guidance commands with fresh heartbeats and valid_until
    timestamps to all physical signs and speakers.
    """
    time.sleep(1.0)
    while not shutting_down:
        try:
            if not simulation_active and simulation_step == 0 and elapsed_before_pause == 0:
                publish_live_guidance(client)
        except Exception as exc:
            print("[Standalone Loop] Error in guidance update:", exc, flush=True)
        time.sleep(1.5)


def on_connect(client, userdata, flags, reason_code, properties=None):
    global standalone_thread
    if shutting_down:
        return
    if reason_code == 0:
        print("Connected to MQTT Broker!")
        client.subscribe("building/config")
        client.subscribe("building/simulation/start")
        client.subscribe("building/simulation/resume")
        client.subscribe("building/simulation/stop")
        client.subscribe("building/simulation/reset")
        client.subscribe("building/simulation/behavior")
        client.subscribe("building/occupancy/adjust")
        client.subscribe("building/occupancy/input")
        client.subscribe("building/hazard/adjust")
        client.subscribe("building/incident")
        client.subscribe("building/incident/clear")
        client.subscribe("building/guidance/ack/+")
        client.subscribe("building/guidance/capabilities/+")
        if not simulation_active and simulation_step == 0:
            reset_simulation(client)
            try:
                publish_live_guidance(client)
            except Exception as exc:
                print("[Standalone] Initial guidance publish error:", exc, flush=True)
        if standalone_thread is None or not standalone_thread.is_alive():
            standalone_thread = threading.Thread(
                target=standalone_guidance_loop, args=(client,), daemon=True, name="StandaloneGuidance"
            )
            standalone_thread.start()
            print("[Standalone] Started continuous live guidance evaluation thread.", flush=True)
    else:
        print(f"Failed to connect, return code {reason_code}")



def on_message(client, userdata, msg):
    global map_config, simulation_active, simulation_thread, routing_service, edge_states, behavior_config, behavior_settings
    topic = msg.topic
    if topic == "building/config":
        try:
            data = json.loads(msg.payload.decode("utf-8"))
            map_config = data
            routing_service = DynamicEvacuationRouter(map_config, routing_parameters)
            guidance_controller.configure(map_config)
            save_config(data)
        except Exception as exc:
            print("Error parsing map config:", exc)

    elif topic == "building/simulation/start":
        simulation_stop_event.set()
        if simulation_thread and simulation_thread.is_alive():
            simulation_active = False
            simulation_thread.join(timeout=1.0)
        simulation_stop_event.clear()
        simulation_active = True
        simulation_thread = threading.Thread(
            target=simulation_loop, args=(client, False), daemon=True
        )
        simulation_thread.start()

    elif topic == "building/simulation/resume":
        simulation_stop_event.set()
        if simulation_thread and simulation_thread.is_alive():
            simulation_active = False
            simulation_thread.join(timeout=1.0)
        simulation_stop_event.clear()
        simulation_active = True
        simulation_thread = threading.Thread(
            target=simulation_loop, args=(client, True), daemon=True
        )
        simulation_thread.start()

    elif topic == "building/simulation/stop":
        stop_simulation(client)

    elif topic == "building/simulation/reset":
        reset_simulation(client)

    elif topic == "building/simulation/behavior":
        try:
            incoming = json.loads(msg.payload.decode("utf-8"))
            if not isinstance(incoming, dict):
                raise ValueError("behavior_settings_must_be_an_object")
            behavior_settings = {**behavior_settings, **incoming}
            behavior_config = BehaviorConfig.from_mapping(behavior_settings)
            client.publish(
                "building/simulation/behavior/state",
                json.dumps({"success": True, "settings": behavior_settings}, ensure_ascii=False),
                qos=1,
                retain=True,
            )
        except Exception as exc:
            client.publish(
                "building/simulation/behavior/state",
                json.dumps({"success": False, "error": str(exc)}),
                qos=1,
                retain=False,
            )

    elif topic == "building/occupancy/input":
        try:
            data = json.loads(msg.payload.decode("utf-8"))
            values = data.get("values")
            if values is None:
                values = {data.get("edge_id"): data.get("k")}
            if not isinstance(values, dict):
                raise ValueError("values_must_be_an_object")
            known_edges = {edge.get("id") for edge in map_config.get("edges", [])}
            clean_values = {}
            invalid_edges = []
            for edge_id, value in values.items():
                if not edge_id or edge_id not in known_edges:
                    invalid_edges.append(str(edge_id))
                    continue
                normalized = csi_layer._valid(value)
                if normalized is None:
                    csi_layer.update(edge_id, value, confidence=0.0)
                    invalid_edges.append(edge_id)
                    continue
                clean_values[edge_id] = normalized
            with state_lock:
                latest_sensor_occupancy.update(clean_values)
                for edge_id, value in clean_values.items():
                    csi_layer.update(edge_id, value, confidence=1.0)
                edge_states = edge_state_snapshot()
            if simulation_active:
                for edge_id, k in clean_values.items():
                    queue_occupancy_update(edge_id, k, "set")
            client.publish(
                "building/occupancy",
                json.dumps(clean_values, ensure_ascii=False),
            )
            client.publish(
                "building/occupancy/adjust_ack",
                json.dumps({
                    "success": not invalid_edges,
                    "source": "sensor",
                    **({"error": "invalid_or_unknown_edges: " + ", ".join(invalid_edges)} if invalid_edges else {}),
                }),
            )
            if not simulation_active:
                publish_live_guidance(client)
        except Exception as exc:
            client.publish(
                "building/occupancy/adjust_ack",
                json.dumps({"success": False, "error": str(exc)}),
            )

    elif topic == "building/occupancy/adjust":
        try:
            data = json.loads(msg.payload.decode("utf-8"))
            if not simulation_active:
                raise ValueError("simulation_not_running")
            if "values" in data:
                for edge_id, k in data["values"].items():
                    queue_occupancy_update(edge_id, k, "set")
            else:
                edge_id = data.get("edge_id")
                if edge_id not in edge_occupancy:
                    raise ValueError("edge_not_found")
                queue_occupancy_update(
                    edge_id, data.get("delta", 0.0), "delta"
                )
            client.publish(
                "building/occupancy/adjust_ack",
                json.dumps({"success": True}),
            )
        except Exception as exc:
            client.publish(
                "building/occupancy/adjust_ack",
                json.dumps({"success": False, "error": str(exc)}),
            )

    elif topic == "building/hazard/adjust":
        try:
            data = json.loads(msg.payload.decode("utf-8"))
            edge_id = data.get("edge_id")
            hazard = max(0.0, float(data.get("hazard", 0.0)))
            known_edges = {edge.get("id") for edge in map_config.get("edges", [])}
            if edge_id not in known_edges:
                raise ValueError("edge_not_found")
            edge_hazards[edge_id] = hazard
            publish_log(
                client,
                [make_log("alert" if hazard else "routing", f"Nguy cơ tại {edge_id}: {hazard:.0f}")],
            )
            if not simulation_active:
                publish_live_guidance(client)
        except Exception as exc:
            publish_log(client, [make_log("alert", f"Dữ liệu nguy cơ không hợp lệ: {exc}")])

    elif topic.startswith("building/guidance/capabilities/"):
        try:
            device_id = topic.rsplit("/", 1)[-1]
            guidance_controller.set_capability(
                device_id, json.loads(msg.payload.decode("utf-8"))
            )
            if not simulation_active:
                publish_live_guidance(client)
        except Exception as exc:
            print("Error parsing device capability:", exc)

    elif topic in ("building/incident", "building/incident/clear"):
        try:
            data = json.loads(msg.payload.decode("utf-8"))
            target_id = data.get("target_id")
            incident_type = data.get("type")
            is_clear = topic.endswith("/clear")
            target_set = (
                blocked_edges if incident_type == "edge" else blocked_exits
            )
            if is_clear:
                target_set.discard(target_id)
                action = "cleared"
            else:
                target_set.add(target_id)
                action = "blocked"
            client.publish(
                "building/incident_ack",
                json.dumps(
                    {
                        "action": action,
                        "type": incident_type,
                        "target_id": target_id,
                    }
                ),
            )
            if incident_type == "edge":
                edge_name = next(
                    (
                        edge.get("name", target_id)
                        for edge in map_config.get("edges", [])
                        if edge.get("id") == target_id
                    ),
                    target_id,
                )
                message = (
                    f"Đã mở lại hành lang '{edge_name}'. Hệ thống tiếp tục từ k(e) hiện tại, không khôi phục giá trị trước sự cố."
                    if is_clear
                    else f"Hành lang '{edge_name}' bị chặn hoặc mất tín hiệu. Không nhận tải mới; tải hiện có sẽ được ước lượng thoát qua hai đầu hành lang."
                )
                publish_log(
                    client,
                    [make_log("routing" if is_clear else "alert", message)],
                )
            if not simulation_active:
                publish_live_guidance(client)
            else:
                all_edges = {edge["id"]: edge for edge in map_config.get("edges", [])}
                with state_lock:
                    graph, edge_map = build_graph()
                    observed_states = edge_states or edge_state_snapshot()
                    exits, distances, route_options = compute_routes(
                        graph, edge_map, observed_states
                    )
                    route_options = optimize_routes(route_options, all_edges)
                guidance_controller.update(
                    client,
                    route_options,
                    distances,
                    dict(edge_occupancy),
                    exits,
                    blocked_edges,
                    force=True,
                )
        except Exception as exc:
            print("Error parsing incident:", exc)

    elif topic.startswith("building/guidance/ack/"):
        try:
            guidance_controller.handle_ack(
                json.loads(msg.payload.decode("utf-8"))
            )
        except Exception as exc:
            print("Error parsing guidance ACK:", exc)


if __name__ == "__main__":
    load_config()
    client = mqtt.Client(
        mqtt.CallbackAPIVersion.VERSION2, client_id="Pi5_Edge_Node"
    )
    client.on_connect = on_connect
    client.on_message = on_message
    client.reconnect_delay_set(min_delay=1, max_delay=10)
    print(f"Connecting to MQTT Broker at {MQTT_BROKER}:{MQTT_PORT}...")
    try:
        client.connect(MQTT_BROKER, MQTT_PORT, 60)
    except Exception as exc:
        print("Connection failed, starting in offline mode:", exc)
    client.loop_start()
    try:
        while True:
            time.sleep(1)
    except KeyboardInterrupt:
        shutting_down = True
        simulation_active = False
        client.loop_stop()
        client.disconnect()
