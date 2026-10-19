"""Push an operator width onto the guidance map via MQTT building/config.

The file on disk is only read. edge_core writes it after it receives the
message. A failed publish never writes the file.
"""

from __future__ import annotations

import copy
import json
import os
import sys
import threading
from pathlib import Path
from typing import Any, Mapping, Optional

MQTT_HOST = "127.0.0.1"
MQTT_PORT = 1883
MQTT_TOPIC = "building/config"
_MQTT_TIMEOUT_S = 2.0

# ponytail: one cache for this process; the dashboard is a single service
_lock = threading.Lock()
_cache: dict[str, Any] = {"key": None, "data": None, "error": None}


class _MqttUnavailable(Exception):
    def __init__(self, *, map_unavailable: bool) -> None:
        super().__init__("mqtt_unavailable")
        self.map_unavailable = map_unavailable


def clear_map_cache() -> None:
    with _lock:
        _cache["key"] = None
        _cache["data"] = None
        _cache["error"] = None


def config_path() -> Path:
    raw = os.environ.get("WIEVAC_GUIDANCE_CONFIG", "").strip()
    if raw:
        return Path(raw)
    return Path(__file__).resolve().parents[3] / "wievac_guidance" / "config.json"


def _blank(state: str, **extra: Any) -> dict[str, Any]:
    payload: dict[str, Any] = {"state": state, "edges": [], "map_width_m": None}
    payload.update(extra)
    return payload


def _edge_links(edge: Mapping[str, Any]) -> list[str]:
    return [part.strip() for part in str(edge.get("csiLinkId") or "").split(",") if part.strip()]


def _number(value: Any) -> Optional[float]:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    number = float(value)
    if number != number or number in (float("inf"), float("-inf")):
        return None
    return number


def _close(left: float, right: float) -> bool:
    return abs(left - right) <= 1e-6


def read_map() -> tuple[Optional[dict[str, Any]], Optional[str]]:
    path = config_path()
    try:
        stat = path.stat()
    except OSError:
        return None, "missing"
    if not path.is_file():
        return None, "missing"
    key = (str(path), stat.st_mtime_ns, stat.st_size)
    with _lock:
        if _cache["key"] == key:
            data = _cache["data"]
            return (copy.deepcopy(data) if isinstance(data, dict) else None), _cache["error"]
    try:
        loaded = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError, ValueError):
        loaded = None
        error: Optional[str] = "unreadable"
    else:
        if isinstance(loaded, dict):
            error = None
        else:
            loaded = None
            error = "unreadable"
    with _lock:
        _cache["key"] = key
        _cache["data"] = loaded
        _cache["error"] = error
    return (copy.deepcopy(loaded) if isinstance(loaded, dict) else None), error


def _matches(document: Mapping[str, Any], link_id: str) -> list[dict[str, Any]]:
    edges = document.get("edges")
    if not isinstance(edges, list):
        return []
    return [edge for edge in edges if isinstance(edge, dict) and link_id in _edge_links(edge)]


def _conflict(edge: Mapping[str, Any], widths: Mapping[str, Optional[float]]) -> bool:
    values = [float(widths[link]) for link in _edge_links(edge) if widths.get(link) is not None]
    if len(values) < 2:
        return False
    return any(not _close(value, values[0]) for value in values[1:])


def _status(document: Mapping[str, Any], link_id: str, width_m: float, widths: Mapping[str, Optional[float]]) -> dict[str, Any]:
    matched = _matches(document, link_id)
    if not matched:
        return _blank("NOT_MAPPED")
    ids = [str(edge.get("id")) for edge in matched if edge.get("id") not in (None, "")]
    measured = [number for number in (_number(edge.get("widthMeters")) for edge in matched) if number is not None]
    info = _blank("MISMATCH", edges=ids, map_width_m=measured[0] if measured else None)
    if measured and all(_close(number, width_m) for number in measured):
        info["state"] = "SYNCED"
        info["map_width_m"] = measured[0]
    if any(_conflict(edge, widths) for edge in matched):
        info["multi_link_width_conflict"] = True
    return info


def status_for_links(widths: Mapping[str, Optional[float]]) -> dict[str, dict[str, Any]]:
    result: dict[str, dict[str, Any]] = {}
    document: Optional[dict[str, Any]] = None
    error: Optional[str] = None
    if any(width is not None for width in widths.values()):
        try:
            document, error = read_map()
        except Exception:
            document, error = None, "unreadable"
    for link_id, width_m in widths.items():
        if width_m is None:
            result[link_id] = _blank("NO_WIDTH")
        elif document is None:
            result[link_id] = _blank("MAP_UNAVAILABLE", reason=error or "missing")
        else:
            result[link_id] = _status(document, link_id, float(width_m), widths)
    return result


def _mqtt_client() -> Any:
    folder = str(Path(__file__).resolve().parents[3] / "wievac_guidance")
    if not sys.path or sys.path[0] != folder:
        sys.path.insert(0, folder)
    try:
        import paho.mqtt.client as mqtt
    except ImportError as exc:
        raise _MqttUnavailable(map_unavailable=True) from exc
    client = mqtt.Client(mqtt.CallbackAPIVersion.VERSION2)
    client.connect_timeout = _MQTT_TIMEOUT_S
    return client


def publish_building_config(document: Mapping[str, Any]) -> None:
    client = _mqtt_client()
    try:
        client.connect(MQTT_HOST, MQTT_PORT, 10)
        client.loop_start()
        info = client.publish(
            MQTT_TOPIC,
            json.dumps(document, ensure_ascii=False),
            qos=1,
            retain=False,
        )
        info.wait_for_publish(timeout=_MQTT_TIMEOUT_S)
    except _MqttUnavailable:
        raise
    except Exception as exc:
        raise _MqttUnavailable(map_unavailable=False) from exc
    finally:
        try:
            client.loop_stop()
        except Exception:
            pass
        try:
            client.disconnect()
        except Exception:
            pass


def _chosen(edge: Mapping[str, Any], widths: Mapping[str, Optional[float]], fallback: float) -> float:
    values = [float(widths[link]) for link in _edge_links(edge) if widths.get(link) is not None]
    return min(values) if values else float(fallback)


def sync_nominal_width(link_id: str, width_m: Optional[float], widths: Mapping[str, Optional[float]]) -> dict[str, Any]:
    if width_m is None:
        return _blank("NO_WIDTH")
    merged = dict(widths)
    merged[link_id] = float(width_m)
    try:
        document, error = read_map()
    except Exception:
        document, error = None, "unreadable"
    if document is None:
        return _blank("MAP_UNAVAILABLE", reason=error or "missing")
    if not _matches(document, link_id):
        return _blank("NOT_MAPPED")
    updated = copy.deepcopy(document)
    for edge in updated.get("edges") or []:
        if not isinstance(edge, dict) or link_id not in _edge_links(edge):
            continue
        edge["widthMeters"] = _chosen(edge, merged, float(width_m))
        edge["widthEstimated"] = False
    before = _status(document, link_id, float(width_m), merged)
    try:
        publish_building_config(updated)
    except _MqttUnavailable as exc:
        if exc.map_unavailable:
            return _blank("MAP_UNAVAILABLE", reason="mqtt_unavailable")
        before["state"] = "MISMATCH"
        before["reason"] = "mqtt_unavailable"
        return before
    try:
        fresh, error = read_map()
    except Exception:
        fresh, error = None, "unreadable"
    if fresh is None:
        return _blank("MAP_UNAVAILABLE", reason=error or "missing")
    return _status(fresh, link_id, float(width_m), merged)
