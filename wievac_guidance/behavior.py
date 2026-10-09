"""Configurable, reproducible non-compliance choices for load-group simulation."""

from __future__ import annotations

from dataclasses import dataclass, field
import json
from pathlib import Path


@dataclass
class BehaviorConfig:
    enabled: bool = True
    guidance_compliance: float = 0.70
    familiar_route_weight: float = 0.50
    follow_crowd_weight: float = 0.30
    random_safe_route_weight: float = 0.20
    decision_hold_seconds: float = 2.0
    area_overrides: dict = field(default_factory=dict)
    familiar_routes: dict = field(default_factory=dict)

    @classmethod
    def from_mapping(cls, raw: dict | None) -> "BehaviorConfig":
        raw = raw or {}
        return cls(
            enabled=bool(raw.get("enabled", True)),
            guidance_compliance=min(1.0, max(0.0, float(raw.get("guidance_compliance", .70)))),
            familiar_route_weight=max(0.0, float(raw.get("familiar_route_weight", .50))),
            follow_crowd_weight=max(0.0, float(raw.get("follow_crowd_weight", .30))),
            random_safe_route_weight=max(0.0, float(raw.get("random_safe_route_weight", .20))),
            decision_hold_seconds=max(0.0, float(raw.get("decision_hold_seconds", 2))),
            area_overrides=dict(raw.get("area_overrides") or {}),
            familiar_routes=dict(raw.get("familiar_routes") or {}),
        )


def load_behavior_config(path: str | Path) -> tuple[BehaviorConfig, dict]:
    try:
        raw = json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        raw = {}
    return BehaviorConfig.from_mapping(raw), raw


def distribute_choices(options: list[dict], area_id: str, config: BehaviorConfig, rng, edge_loads: dict[str, float]) -> tuple[list[tuple[dict, float]], float]:
    """Return safe choice shares and rejected unsafe deviation load fraction.

    ``options`` are supplied by D* Lite, so every selected edge is already
    usable. A configured familiar edge outside that set is rejected rather
    than being introduced into the physical simulation.
    """
    usable = [item for item in options if item.get("share", 0) > 0 or item.get("edge_id")]
    if not usable:
        return [], 0.0
    base_total = sum(max(0.0, float(item.get("share", 0))) for item in usable)
    base = [max(0.0, float(item.get("share", 0))) / base_total if base_total else 1.0 / len(usable) for item in usable]
    if not config.enabled or config.guidance_compliance >= 1.0:
        return list(zip(usable, base)), 0.0
    override = config.area_overrides.get(area_id, {})
    compliance = min(1.0, max(0.0, float(override.get("guidance_compliance", config.guidance_compliance))))
    weights = [config.familiar_route_weight, config.follow_crowd_weight, config.random_safe_route_weight]
    total = sum(weights)
    if total <= 0:
        return list(zip(usable, base)), 0.0
    weights = [value / total for value in weights]
    chosen_index = max(range(len(usable)), key=lambda index: edge_loads.get(usable[index]["edge_id"], 0.0))
    familiar_edge = override.get("familiar_edge_id", config.familiar_routes.get(area_id))
    rejected = 0.0
    if familiar_edge:
        familiar_index = next((index for index, item in enumerate(usable) if item["edge_id"] == familiar_edge), None)
        if familiar_index is None:
            rejected = 1.0 - compliance
            familiar_index = chosen_index
    else:
        familiar_index = chosen_index
    random_index = rng.randrange(len(usable))
    result = [compliance * value for value in base]
    deviation = 1.0 - compliance
    result[familiar_index] += deviation * weights[0]
    result[chosen_index] += deviation * weights[1]
    result[random_index] += deviation * weights[2]
    normalizer = sum(result)
    return [(item, value / normalizer) for item, value in zip(usable, result)], rejected
