"""In-process V5 UDP/API service adapter.

The existing V4 runner remains available for replay and rollback.  This
service is the software-only V5 path: callers feed datagrams from a UDP task,
then expose the returned JSON mappings through their preferred HTTP server.
"""

from __future__ import annotations

import copy
import json
import os
import re
import time
import uuid
from datetime import datetime, timezone
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Any, Mapping, Optional, Tuple

from .edge_result_v5 import EdgeResultV5
from .edge_result_v5_runtime import EdgeResultV5Api, EdgeResultV5Ingestor, EdgeResultRecorder, IngestDecision, jsonl_stem


IDENTITY_FIELDS = ("device_id", "node_id", "tx_id", "rx_id", "corridor_id")
FIRMWARE_RX_MAX = 64
_PATCHABLE_FIELDS = ("display_name", "enabled", "sort_order", "corridor_id")


OCCUPANCY_LABELS = ("EMPTY", "HUMAN_PRESENT", "UNKNOWN")


@dataclass(frozen=True)
class V5ServiceConfig:
    stale_after_ms: int = 3000
    min_quality: float = 25.0
    recorder_directory: Optional[str] = None
    label_directory: Optional[str] = None
    schedule_path: Optional[str] = None
    allow_open_test_only: bool = False
    topology_path: Optional[str] = None
    topology: Optional[Mapping[str, Any]] = None
    max_dynamic_links: int = 64
    enable_reference_modules: bool = False


def _rx_numeric(rx_id: Any) -> Optional[int]:
    match = re.search(r"(\d+)$", str(rx_id or "").strip())
    return int(match.group(1)) if match else None


def _firmware_supported(rx_id: Any) -> bool:
    number = _rx_numeric(rx_id)
    return number is None or 1 <= number <= FIRMWARE_RX_MAX


def _default_display_name(item: Mapping[str, Any], index: int) -> str:
    name = str(item.get("display_name") or "").strip()
    if name:
        return name
    number = _rx_numeric(item.get("rx_id"))
    if number is not None:
        return f"Hành lang {number}"
    link_id = str(item.get("link_id") or "").strip()
    return link_id or f"Node {index + 1}"


def _identity_from(item: Mapping[str, Any]) -> dict[str, str]:
    return {name: str(item[name]) for name in IDENTITY_FIELDS}


def _as_bool(value: Any, default: bool = True) -> bool:
    if value is None:
        return default
    if isinstance(value, str):
        return value.strip().lower() not in {"0", "false", "no", "off"}
    return bool(value)


class EdgeResultV5Service:
    """Own one V5 ingest state and expose stable dashboard API payloads."""

    def __init__(
        self,
        *,
        identities: Optional[Mapping[str, Mapping[str, Any]]] = None,
        config: Optional[V5ServiceConfig] = None,
        topology_path: Optional[str] = None,
    ) -> None:
        self.config = config or V5ServiceConfig()
        if topology_path is not None:
            if self.config.topology_path is not None and self.config.topology_path != topology_path:
                raise ValueError("conflicting V5 topology paths")
            self.config = replace(self.config, topology_path=topology_path)
        topology = self._load_topology(self.config)
        # A topology link list is a bounded membership allow-list even when
        # richer device/MAC identity fields are supplied separately.
        configured_identities: Optional[Mapping[str, Mapping[str, Any]]] = identities
        if configured_identities is None and topology:
            links = topology.get("links")
            if isinstance(links, list):
                derived: dict[str, dict[str, Any]] = {}
                for item in links:
                    if not isinstance(item, Mapping) or item.get("link_id") is None:
                        continue
                    if any(item.get(name) in (None, "") for name in IDENTITY_FIELDS):
                        raise ValueError("topology_identity_incomplete")
                    link_key = str(item["link_id"])
                    derived[link_key] = {name: str(item[name]) for name in IDENTITY_FIELDS}
                configured_identities = derived
        self.ingest = EdgeResultV5Ingestor(
            identities=configured_identities,
            stale_after_ms=self.config.stale_after_ms,
            min_quality=self.config.min_quality,
            allow_open_test_only=self.config.allow_open_test_only,
            max_dynamic_links=self.config.max_dynamic_links,
        )
        self._topology = topology
        self.api = EdgeResultV5Api(self.ingest, enable_reference_modules=self.config.enable_reference_modules)
        if self.config.enable_reference_modules:
            self._load_reference_routes(topology)
        self.recorder = EdgeResultRecorder(self.config.recorder_directory) if self.config.recorder_directory else None
        self.session_id: Optional[str] = None
        self._label_directory = Path(self.config.label_directory) if self.config.label_directory else None
        if self._label_directory is not None:
            self._label_directory.mkdir(parents=True, exist_ok=True)
        self._open_labels: dict[str, dict[str, Any]] = {}
        self._label_handles: dict[str, Any] = {}
        self._label_paths: dict[str, Path] = {}
        self._record_started_mono: Optional[float] = None
        self._record_until_mono: Optional[float] = None
        self._record_duration_hours: Optional[float] = None
        self._record_started_utc: Optional[str] = None
        self._active_schedule_id: Optional[str] = None
        if self.config.schedule_path:
            self._schedule_path = Path(self.config.schedule_path)
        elif self._label_directory is not None:
            self._schedule_path = self._label_directory.parent / "capture-schedules.json"
        else:
            self._schedule_path = None
        self._schedules: list[dict[str, Any]] = self._load_schedules()

    @staticmethod
    def _load_topology(config: V5ServiceConfig) -> dict[str, Any]:
        if config.topology is not None:
            if not isinstance(config.topology, Mapping):
                raise ValueError("V5 topology must be an object")
            copied = copy.deepcopy(dict(config.topology))
            links = copied.get("links")
            if isinstance(links, list):
                copied["links"] = [dict(item) if isinstance(item, Mapping) else item for item in links]
            return copied
        if not config.topology_path:
            return {}
        path = Path(config.topology_path)
        try:
            with path.open("r", encoding="utf-8") as handle:
                value = json.load(handle)
        except (OSError, ValueError) as exc:
            raise ValueError(f"cannot load V5 topology: {path}") from exc
        if not isinstance(value, Mapping):
            raise ValueError("V5 topology must be an object")
        if int(value.get("protocol_version", 5)) != 5:
            raise ValueError("V5 topology protocol_version mismatch")
        copied = dict(value)
        links = copied.get("links")
        if isinstance(links, list):
            copied["links"] = [dict(item) if isinstance(item, Mapping) else item for item in links]
        return copied

    def _load_reference_routes(self, topology: Mapping[str, Any]) -> None:
        if self.api.router is None:
            return
        links = topology.get("links") if isinstance(topology, Mapping) else None
        if not isinstance(links, list):
            return
        for item in links:
            if not isinstance(item, Mapping) or item.get("link_id") is None:
                continue
            source = item.get("source", item.get("from", item.get("tx_id")))
            target = item.get("target", item.get("to", item.get("rx_id")))
            if source is None or target is None:
                continue
            self.api.router.add_link(str(source), str(target), str(item["link_id"]))

    def _ensure_links(self) -> list[Any]:
        links = self._topology.get("links")
        if not isinstance(links, list):
            links = []
            self._topology["links"] = links
            self._topology.setdefault("protocol_version", 5)
        return links

    def _catalog_entry(self, item: Mapping[str, Any], index: int) -> dict[str, Any]:
        rx_id = str(item.get("rx_id") or "")
        try:
            sort_order = int(item.get("sort_order"))
        except (TypeError, ValueError):
            sort_order = index + 1
        return {
            "link_id": str(item["link_id"]),
            "display_name": _default_display_name(item, index),
            "enabled": _as_bool(item.get("enabled"), True),
            "sort_order": sort_order,
            "corridor_id": str(item.get("corridor_id") or ""),
            "rx_id": rx_id,
            "node_id": str(item.get("node_id") or ""),
            "device_id": str(item.get("device_id") or ""),
            "tx_id": str(item.get("tx_id") or ""),
            "firmware_supported": _firmware_supported(rx_id),
        }

    def catalog(self) -> dict[str, dict[str, Any]]:
        items: dict[str, dict[str, Any]] = {}
        links = self._topology.get("links") if isinstance(self._topology, Mapping) else None
        if isinstance(links, list):
            for index, item in enumerate(links):
                if not isinstance(item, Mapping) or item.get("link_id") in (None, ""):
                    continue
                entry = self._catalog_entry(item, index)
                items[entry["link_id"]] = entry
        for index, (link_id, ident) in enumerate(self.ingest.identities.items()):
            key = str(link_id)
            if key in items:
                continue
            merged = {"link_id": key, **dict(ident)}
            items[key] = self._catalog_entry(merged, len(items) + index)
        return items

    def _defaults_from_existing(self) -> dict[str, str]:
        for item in self._ensure_links():
            if isinstance(item, Mapping) and item.get("device_id"):
                return {
                    "device_id": str(item.get("device_id") or "device-1"),
                    "tx_id": str(item.get("tx_id") or "tx-1"),
                    "corridor_id": str(item.get("corridor_id") or self._topology.get("corridor_id") or "corridor-01"),
                }
        for ident in self.ingest.identities.values():
            return {
                "device_id": str(ident.get("device_id") or "device-1"),
                "tx_id": str(ident.get("tx_id") or "tx-1"),
                "corridor_id": str(ident.get("corridor_id") or "corridor-01"),
            }
        return {
            "device_id": "device-1",
            "tx_id": "tx-1",
            "corridor_id": str(self._topology.get("corridor_id") or "corridor-01"),
        }

    def _assert_unique(self, item: Mapping[str, Any], *, exclude: Optional[str] = None) -> None:
        link_id = str(item["link_id"])
        rx_id = str(item["rx_id"])
        node_id = str(item["node_id"])
        for existing in self._ensure_links():
            if not isinstance(existing, Mapping):
                continue
            existing_id = str(existing.get("link_id") or "")
            if exclude is not None and existing_id == exclude:
                continue
            if existing_id == link_id:
                raise ValueError("duplicate_link_id")
            if str(existing.get("rx_id") or "") == rx_id:
                raise ValueError("duplicate_rx_id")
            if str(existing.get("node_id") or "") == node_id:
                raise ValueError("duplicate_node_id")
        for existing_id, ident in self.ingest.identities.items():
            if exclude is not None and existing_id == exclude:
                continue
            if existing_id == link_id:
                raise ValueError("duplicate_link_id")
            if str(ident.get("rx_id") or "") == rx_id:
                raise ValueError("duplicate_rx_id")
            if str(ident.get("node_id") or "") == node_id:
                raise ValueError("duplicate_node_id")

    def _persist_topology(self) -> None:
        path_value = self.config.topology_path
        if not path_value:
            return
        path = Path(path_value)
        path.parent.mkdir(parents=True, exist_ok=True)
        encoded = json.dumps(self._topology, ensure_ascii=False, indent=2) + "\n"
        tmp = path.with_name(path.name + ".tmp")
        tmp.write_text(encoded, encoding="utf-8")
        os.replace(tmp, path)

    def _find_link(self, link_id: str) -> Optional[dict[str, Any]]:
        for item in self._ensure_links():
            if isinstance(item, dict) and str(item.get("link_id")) == link_id:
                return item
        return None

    def list_nodes(self, *, now_us: Optional[int] = None) -> dict[str, Any]:
        catalog = self.catalog()
        runtime = self.ingest.latest_by_link(now_us=now_us)
        nodes = []
        for link_id, meta in catalog.items():
            current = runtime.get(link_id, {})
            nodes.append({
                **meta,
                "state": current.get("state"),
                "score": current.get("score"),
                "stale": current.get("stale"),
                "age_ms": current.get("age_ms"),
                "arrival_timestamp_us": current.get("arrival_timestamp_us"),
                "quality": current.get("quality"),
            })
        nodes.sort(key=lambda row: (int(row.get("sort_order") or 0), str(row.get("link_id"))))
        return {"nodes": nodes, "catalog": catalog}

    def create_node(self, payload: Mapping[str, Any]) -> dict[str, Any]:
        if not isinstance(payload, Mapping):
            raise ValueError("invalid_payload")
        display_name = str(payload.get("display_name") or "").strip()
        link_id = str(payload.get("link_id") or "").strip()
        rx_id = str(payload.get("rx_id") or "").strip()
        if not display_name or not link_id or not rx_id:
            raise ValueError("missing_fields")
        defaults = self._defaults_from_existing()
        item = {
            "link_id": link_id,
            "device_id": str(payload.get("device_id") or defaults["device_id"]).strip(),
            "node_id": str(payload.get("node_id") or rx_id).strip(),
            "tx_id": str(payload.get("tx_id") or defaults["tx_id"]).strip(),
            "rx_id": rx_id,
            "corridor_id": str(payload.get("corridor_id") or defaults["corridor_id"]).strip(),
            "display_name": display_name,
            "enabled": _as_bool(payload.get("enabled"), True),
        }
        if any(item.get(name) in (None, "") for name in IDENTITY_FIELDS):
            raise ValueError("topology_identity_incomplete")
        self._assert_unique(item)
        links = self._ensure_links()
        try:
            sort_order = int(payload["sort_order"]) if payload.get("sort_order") is not None else None
        except (TypeError, ValueError) as exc:
            raise ValueError("invalid_payload") from exc
        if sort_order is None:
            existing = [int(row.get("sort_order") or 0) for row in links if isinstance(row, Mapping)]
            sort_order = (max(existing) + 1) if existing else len(links) + 1
        item["sort_order"] = sort_order
        try:
            self.ingest.upsert_identity(link_id, _identity_from(item))
        except RuntimeError as exc:
            raise ValueError(str(exc)) from exc
        links.append(item)
        self._persist_topology()
        return {"node": self._catalog_entry(item, len(links) - 1), "catalog": self.catalog()}

    def patch_node(self, link_id: str, payload: Mapping[str, Any]) -> dict[str, Any]:
        if not isinstance(payload, Mapping):
            raise ValueError("invalid_payload")
        key = str(link_id or "").strip()
        if not key:
            raise ValueError("missing_fields")
        item = self._find_link(key)
        if item is None:
            ident = self.ingest.identities.get(key)
            if ident is None:
                raise ValueError("not_found")
            item = {"link_id": key, **dict(ident)}
            self._ensure_links().append(item)
        for field in payload:
            if field not in _PATCHABLE_FIELDS:
                continue
            if field == "display_name":
                name = str(payload.get("display_name") or "").strip()
                if not name:
                    raise ValueError("missing_fields")
                item["display_name"] = name
            elif field == "enabled":
                item["enabled"] = _as_bool(payload.get("enabled"), True)
            elif field == "sort_order":
                try:
                    item["sort_order"] = int(payload.get("sort_order"))
                except (TypeError, ValueError) as exc:
                    raise ValueError("invalid_payload") from exc
            elif field == "corridor_id":
                corridor = str(payload.get("corridor_id") or "").strip()
                if not corridor:
                    raise ValueError("topology_identity_incomplete")
                item["corridor_id"] = corridor
        if any(item.get(name) in (None, "") for name in IDENTITY_FIELDS):
            raise ValueError("topology_identity_incomplete")
        self.ingest.upsert_identity(key, _identity_from(item))
        self._persist_topology()
        index = next((i for i, row in enumerate(self._ensure_links()) if isinstance(row, Mapping) and str(row.get("link_id")) == key), 0)
        return {"node": self._catalog_entry(item, index), "catalog": self.catalog()}

    def delete_node(self, link_id: str) -> dict[str, Any]:
        key = str(link_id or "").strip()
        if not key:
            raise ValueError("missing_fields")
        links = self._ensure_links()
        kept = [item for item in links if not (isinstance(item, Mapping) and str(item.get("link_id")) == key)]
        existed = len(kept) != len(links) or key in self.ingest.identities
        if not existed:
            raise ValueError("not_found")
        self._topology["links"] = kept
        self.ingest.remove_identity(key)
        self._persist_topology()
        return {"deleted": key, "catalog": self.catalog()}

    def _label_now_us(self, now_us: Optional[int] = None) -> int:
        return int(now_us if now_us is not None else time.monotonic_ns() // 1000)

    def _label_handle_for(self, link_id: str) -> Any:
        if self._label_directory is None:
            return None
        stem = jsonl_stem(link_id)
        handle = self._label_handles.get(stem)
        if handle is not None:
            return handle
        path = self._label_directory / f"{stem}.jsonl"
        handle = path.open("a", encoding="utf-8", newline="\n")
        self._label_handles[stem] = handle
        self._label_paths[stem] = path
        return handle

    def _close_label_handles(self) -> None:
        for handle in self._label_handles.values():
            try:
                handle.flush()
            finally:
                handle.close()
        self._label_handles = {}

    def _write_label_row(self, row: Mapping[str, Any]) -> None:
        handle = self._label_handle_for(str(row.get("link_id") or ""))
        if handle is None:
            return
        handle.write(json.dumps(dict(row), ensure_ascii=False, separators=(",", ":")) + "\n")
        handle.flush()

    def active_labels(self) -> dict[str, dict[str, Any]]:
        return {
            link_id: {
                "link_id": link_id,
                "occupancy": item["occupancy"],
                "t_start_us": item["t_start_us"],
                "t_start_utc": item["t_start_utc"],
                "label_source": item.get("label_source") or "operator_dashboard",
            }
            for link_id, item in self._open_labels.items()
        }

    def set_occupancy_label(
        self,
        link_id: str,
        occupancy: str,
        *,
        now_us: Optional[int] = None,
        label_source: str = "operator_dashboard",
    ) -> dict[str, Any]:
        key = str(link_id or "").strip()
        label = str(occupancy or "").strip().upper()
        if label == "PERSON_PRESENT":
            label = "HUMAN_PRESENT"
        if not key:
            raise ValueError("missing_fields")
        if label not in OCCUPANCY_LABELS:
            raise ValueError("invalid_occupancy")
        catalog = self.catalog()
        if key not in catalog and key not in self.ingest.identities:
            raise ValueError("not_found")
        jsonl_stem(key)
        stamp_us = self._label_now_us(now_us)
        utc = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
        previous = self._open_labels.get(key)
        if previous is not None:
            closed = dict(previous)
            closed["t_end_us"] = stamp_us
            closed["t_end_utc"] = utc
            closed["open"] = False
            self._write_label_row(closed)
        row = {
            "record_type": "occupancy_interval",
            "session_id": self.session_id or "operator",
            "link_id": key,
            "occupancy": label,
            "t_start_us": stamp_us,
            "t_end_us": None,
            "t_start_utc": utc,
            "t_end_utc": None,
            "open": True,
            "label_source": str(label_source or "operator_dashboard"),
        }
        self._open_labels[key] = row
        self._write_label_row(row)
        return {"label": self.active_labels()[key], "active_labels": self.active_labels()}

    @staticmethod
    def _parse_duration_hours(value: Any) -> Optional[float]:
        if value is None or value == "":
            return None
        hours = float(value)
        if hours == 0:
            return None
        if hours != hours or hours < 0 or hours > 72:
            raise ValueError("invalid_duration")
        return hours

    def expire_recording(self) -> bool:
        stopped = False
        if self.session_id is not None and self._record_until_mono is not None and time.monotonic() >= self._record_until_mono:
            schedule_id = self._active_schedule_id
            self.stop_recording(success=True, close_labels=schedule_id is not None)
            if schedule_id:
                self._complete_schedule(schedule_id)
            stopped = True
        self.apply_schedules()
        return stopped

    def _load_schedules(self) -> list[dict[str, Any]]:
        if self._schedule_path is None or not self._schedule_path.is_file():
            return []
        try:
            value = json.loads(self._schedule_path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return []
        if not isinstance(value, list):
            return []
        loaded: list[dict[str, Any]] = []
        for item in value:
            if not isinstance(item, Mapping):
                continue
            row = dict(item)
            try:
                if "start_s" not in row:
                    row["start_s"] = self._parse_iso_epoch(row.get("start_at"))
                if "end_s" not in row:
                    row["end_s"] = self._parse_iso_epoch(row.get("end_at"))
            except ValueError:
                continue
            loaded.append(row)
        return loaded

    def _save_schedules(self) -> None:
        if self._schedule_path is None:
            return
        self._schedule_path.parent.mkdir(parents=True, exist_ok=True)
        self._schedule_path.write_text(
            json.dumps(self._schedules, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )

    @staticmethod
    def _parse_iso_epoch(value: Any) -> float:
        text = str(value or "").strip()
        if not text:
            raise ValueError("invalid_schedule")
        if text.endswith("Z"):
            text = text[:-1] + "+00:00"
        parsed = datetime.fromisoformat(text)
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=datetime.now().astimezone().tzinfo)
        return parsed.timestamp()

    def _schedule_public(self, item: Mapping[str, Any], *, now_s: float) -> dict[str, Any]:
        start_s = float(item["start_s"])
        end_s = float(item["end_s"])
        if item.get("completed") or now_s >= end_s:
            phase = "done"
        elif now_s < start_s:
            phase = "pending"
        else:
            phase = "active"
        return {
            "id": item["id"],
            "start_at": item["start_at"],
            "end_at": item["end_at"],
            "occupancy": item["occupancy"],
            "phase": phase,
        }

    def list_schedules(self, *, now_s: Optional[float] = None) -> list[dict[str, Any]]:
        now = float(now_s if now_s is not None else time.time())
        return [self._schedule_public(item, now_s=now) for item in self._schedules]

    def add_schedule(self, payload: Mapping[str, Any]) -> dict[str, Any]:
        occupancy = str(payload.get("occupancy") or "EMPTY").strip().upper()
        if occupancy == "PERSON_PRESENT":
            occupancy = "HUMAN_PRESENT"
        if occupancy not in OCCUPANCY_LABELS:
            raise ValueError("invalid_occupancy")
        start_s = self._parse_iso_epoch(payload.get("start_at"))
        end_s = self._parse_iso_epoch(payload.get("end_at"))
        if end_s <= start_s:
            raise ValueError("invalid_schedule")
        if end_s - start_s > 72 * 3600:
            raise ValueError("invalid_duration")
        item = {
            "id": uuid.uuid4().hex[:12],
            "start_at": datetime.fromtimestamp(start_s, tz=timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
            "end_at": datetime.fromtimestamp(end_s, tz=timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
            "start_s": start_s,
            "end_s": end_s,
            "occupancy": occupancy,
        }
        self._schedules.append(item)
        self._save_schedules()
        self.apply_schedules()
        return {"schedule": self._schedule_public(item, now_s=time.time()), "schedules": self.list_schedules()}

    def delete_schedule(self, schedule_id: str) -> dict[str, Any]:
        key = str(schedule_id or "").strip()
        kept = [item for item in self._schedules if str(item.get("id")) != key]
        if len(kept) == len(self._schedules):
            raise ValueError("not_found")
        if self._active_schedule_id == key:
            self.stop_recording(success=True, close_labels=True)
        self._schedules = kept
        self._save_schedules()
        return {"deleted": key, "schedules": self.list_schedules()}

    def _complete_schedule(self, schedule_id: str) -> None:
        key = str(schedule_id or "").strip()
        changed = False
        for item in self._schedules:
            if str(item.get("id")) == key and not item.get("completed"):
                item["completed"] = True
                changed = True
        if changed:
            self._save_schedules()

    def _force_occupancy_all(self, occupancy: str, *, label_source: str) -> None:
        for link_id in self.catalog():
            current = self._open_labels.get(str(link_id))
            if current is not None and current.get("occupancy") == occupancy:
                continue
            try:
                self.set_occupancy_label(str(link_id), occupancy, label_source=label_source)
            except ValueError:
                continue

    def _close_open_occupancy(self) -> None:
        if not self._open_labels:
            return
        utc = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
        stamp_us = self._label_now_us()
        for previous in list(self._open_labels.values()):
            closed = dict(previous)
            closed["t_end_us"] = stamp_us
            closed["t_end_utc"] = utc
            closed["open"] = False
            self._write_label_row(closed)
        self._open_labels = {}

    def apply_schedules(self, *, now_s: Optional[float] = None) -> None:
        now = float(now_s if now_s is not None else time.time())
        for item in self._schedules:
            if item.get("completed"):
                continue
            start_s = float(item["start_s"])
            end_s = float(item["end_s"])
            schedule_id = str(item["id"])
            if start_s <= now < end_s:
                if self._active_schedule_id not in {None, schedule_id} and self.session_id is not None:
                    continue
                if self.session_id is None:
                    remaining_hours = max((end_s - now) / 3600.0, 0.01)
                    self.start_recording(f"sched-{schedule_id}", duration_hours=remaining_hours)
                    self._active_schedule_id = schedule_id
                    self._force_occupancy_all(str(item["occupancy"]), label_source="scheduled_capture")
                elif self._active_schedule_id is None:
                    self._active_schedule_id = schedule_id
                    self._force_occupancy_all(str(item["occupancy"]), label_source="scheduled_capture")
            elif now >= end_s:
                if self._active_schedule_id == schedule_id:
                    self.stop_recording(success=True, close_labels=True)
                self._complete_schedule(schedule_id)

    def recording_status(self) -> dict[str, Any]:
        self.expire_recording()
        remaining: Optional[float] = None
        if self.session_id is not None and self._record_until_mono is not None:
            remaining = max(0.0, self._record_until_mono - time.monotonic())
        files = {key: str(path) for key, path in (self.recorder.paths if self.recorder else {}).items()}
        return {
            "state": "RECORDING" if self.session_id else "IDLE",
            "session_id": self.session_id,
            "count": self.recorder.count if self.recorder else 0,
            "duration_hours": self._record_duration_hours,
            "remaining_s": remaining,
            "started_at_utc": self._record_started_utc,
            "schedule_id": self._active_schedule_id,
            "files": files,
            "label_files": {key: str(path) for key, path in self._label_paths.items()},
            "schedules": self.list_schedules(),
        }

    def control_recording(self, payload: Mapping[str, Any]) -> dict[str, Any]:
        action = str(payload.get("action") or "").strip().lower()
        if action == "start":
            if self.session_id is not None:
                raise ValueError("already_recording")
            session_id = str(payload.get("session_id") or "capture").strip() or "capture"
            occupancy = str(payload.get("occupancy") or "").strip().upper()
            self.start_recording(session_id, duration_hours=payload.get("duration_hours"))
            if occupancy:
                self._force_occupancy_all(occupancy, label_source="operator_dashboard")
            return self.recording_status()
        if action == "stop":
            self.stop_recording(success=True, close_labels=self._active_schedule_id is not None)
            return self.recording_status()
        if action == "schedule":
            created = self.add_schedule(payload)
            status = self.recording_status()
            status["schedule"] = created["schedule"]
            return status
        if action == "unschedule":
            deleted = self.delete_schedule(str(payload.get("id") or ""))
            status = self.recording_status()
            status["deleted"] = deleted["deleted"]
            return status
        raise ValueError("invalid_action")

    def start_recording(self, session_id: str, *, duration_hours: Any = None) -> Optional[str]:
        hours = self._parse_duration_hours(duration_hours)
        now = time.monotonic()
        self._record_duration_hours = hours
        self._record_started_mono = now
        self._record_until_mono = None if hours is None else now + hours * 3600.0
        self._record_started_utc = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
        if self.recorder is None:
            self.session_id = session_id
            return None
        path = self.recorder.start(session_id)
        self.session_id = session_id
        for link_id in self.catalog():
            try:
                self.recorder.ensure_link(str(link_id))
            except ValueError:
                continue
        return str(path)

    def stop_recording(self, *, success: bool = True, close_labels: bool = False) -> Optional[str]:
        self._record_started_mono = None
        self._record_until_mono = None
        self._record_duration_hours = None
        self._record_started_utc = None
        self._active_schedule_id = None
        if close_labels:
            self._close_open_occupancy()
        if self.recorder is None:
            self.session_id = None
            self._close_label_handles()
            return None
        path = self.recorder.close(success=success)
        self.session_id = None
        self._close_label_handles()
        return str(path) if path else None

    def ingest_datagram(self, packet: bytes, *, now_us: Optional[int] = None, endpoint: Optional[Tuple[str, int]] = None, expected_endpoint: Optional[Tuple[str, int]] = None) -> IngestDecision:
        self.expire_recording()
        arrival_us = int(now_us if now_us is not None else time.monotonic_ns() // 1000)
        decision = self.ingest.ingest(packet, now_us=arrival_us, endpoint=endpoint, expected_endpoint=expected_endpoint)
        if decision.accepted and decision.result is not None:
            self.api.observe(decision.result, arrival_us=arrival_us)
            if self.recorder is not None and self.session_id is not None:
                self.recorder.append(decision.result, arrival_us=arrival_us)
        elif self.recorder is not None and self.session_id is not None:
            self.recorder.append_rejection(
                reason=decision.reason,
                received_at_us=arrival_us,
                packet=packet,
                endpoint=endpoint,
                result=decision.result,
                link_id=decision.link_id,
            )
        return decision

    def response(self, path: str, *, now_us: Optional[int] = None, source: Optional[str] = None, target: Optional[str] = None) -> Mapping[str, Any]:
        if path == "/api/v5/latest":
            return self.api.latest(now_us=now_us)
        if path == "/api/v5/overview":
            payload = dict(self.api.overview(now_us=now_us))
            payload["catalog"] = self.catalog()
            payload["active_labels"] = self.active_labels()
            payload["recording"] = self.recording_status()
            return payload
        if path == "/api/v5/nodes":
            return self.list_nodes(now_us=now_us)
        if path == "/api/v5/trends" and self.config.enable_reference_modules:
            return {"revision": self.ingest.quality_summary(now_us=now_us)["revision"], "links": self.api.trends(now_us=now_us)}
        if path == "/api/v5/route" and self.config.enable_reference_modules:
            if source is None or target is None:
                return {"route_state": "NO_ROUTE", "reason": "source_target_required", "path": [], "cost": None}
            return self.api.route(source, target, now_us=now_us)
        if path == "/api/v5/transport" and self.config.enable_reference_modules:
            return self.api.transport(now_us=now_us)
        if path in {"/api/v5/trends", "/api/v5/route", "/api/v5/transport"}:
            return {"error": "reference_only_unmounted", "path": path}
        if path == "/api/v5/recorder":
            return self.recording_status()
        return {"error": "not_found"}

    def response_json(self, path: str, **kwargs: Any) -> str:
        return json.dumps(self.response(path, **kwargs), ensure_ascii=False, separators=(",", ":"))


__all__ = ["V5ServiceConfig", "EdgeResultV5Service"]
