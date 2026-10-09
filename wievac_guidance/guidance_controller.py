import json
import json
import math
import time
from pathlib import Path


ACK_TIMEOUT_SECONDS = 3.0
DEVICE_OFFLINE_SECONDS = 12.0
COMMAND_HEARTBEAT_SECONDS = 5.0
COMMAND_VALID_SECONDS = 8
CONTENT_FILE = Path(__file__).with_name("guidance_content.json")


def load_content() -> dict:
    try:
        return json.loads(CONTENT_FILE.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {"version": 1, "intents": {}}


class GuidanceController:
    """Convert graph routing decisions into sign/speaker MQTT commands."""

    def __init__(self):
        self.devices = []
        self.area_map = {}
        self.edge_map = {}
        self.sequence = 0
        self.last_commands = {}
        self.pending_acks = {}
        self.last_acks = {}
        self.offline_devices = set()
        self.capabilities = {}
        self.content = load_content()
        self.latest_state = {"decisions": {}, "devices": []}
        self._last_state_signature = None
        self._last_state_published_at = 0.0

    def configure(self, map_config):
        self.devices = list(map_config.get("devices", []))
        self.area_map = {
            area["id"]: area for area in map_config.get("areas", [])
        }
        self.edge_map = {
            edge["id"]: edge for edge in map_config.get("edges", [])
        }
        valid_ids = {device.get("id") for device in self.devices}
        self.last_commands = {
            key: value
            for key, value in self.last_commands.items()
            if key in valid_ids
        }
        self.pending_acks = {
            key: value
            for key, value in self.pending_acks.items()
            if key in valid_ids
        }
        self.offline_devices.intersection_update(valid_ids)
        self.capabilities = {key: value for key, value in self.capabilities.items() if key in valid_ids}

    def set_capability(self, device_id, capability):
        if device_id and isinstance(capability, dict):
            self.capabilities[device_id] = dict(capability)

    def _presentation(self, device, direction, decision, target_edge, valid_until):
        intent = self.content.get("intents", {}).get(direction, self.content.get("intents", {}).get("NO_SAFE_ROUTE", {}))
        occupancy = self._occupancy_ratio((decision or {}).get("k", 1.0))
        load_level = "blocked" if direction == "NO_SAFE_ROUTE" else "congested" if occupancy >= .8 else "busy" if occupancy >= .5 else "clear"
        capability = self.capabilities.get(device.get("id"), {})
        display = capability.get("display", {}) if isinstance(capability.get("display"), dict) else {}
        profile = device.get("presentationProfile") or "max7219"
        supports_load = bool(display.get("supports_load_bar")) or profile == "large_display"
        return {
            "schema_version": 1,
            "content_revision": self.content.get("version", 1),
            "intent": direction,
            "visual_intent": intent.get("visual_intent", "DO_NOT_ENTER"),
            "alert_level": intent.get("alert_level", "critical"),
            "load_level": load_level,
            "audio_clip_id": intent.get("audio_clip_id", ""),
            "min_repeat_seconds": intent.get("min_repeat_seconds", 0),
            "layout": "large_arrow_alert_load" if supports_load else "max7219_arrow_alert",
            "valid_until": valid_until,
        }

    def _other_area(self, edge, area_id):
        if edge.get("areaA_id") == area_id:
            return self.area_map.get(edge.get("areaB_id"))
        if edge.get("areaB_id") == area_id:
            return self.area_map.get(edge.get("areaA_id"))
        return None

    def _infer_direction(self, device, target_edge):
        explicit = (device.get("edgeDirections") or {}).get(target_edge)
        if explicit:
            return explicit.upper()

        area_id = device.get("area_id")
        area = self.area_map.get(area_id)
        edge = self.edge_map.get(target_edge)
        other = self._other_area(edge, area_id) if edge else None
        if not area or not other:
            return "STRAIGHT"

        current_floor = area.get("floor", 1)
        other_floor = other.get("floor", 1)
        if other_floor > current_floor:
            return "UP"
        if other_floor < current_floor:
            return "DOWN"

        dx = float(other.get("x", 0)) - float(area.get("x", 0))
        dy = float(other.get("y", 0)) - float(area.get("y", 0))
        bearing = math.degrees(math.atan2(dx, -dy)) % 360
        orientation = float(device.get("orientation", 0)) % 360
        relative = ((bearing - orientation + 180) % 360) - 180
        if abs(relative) <= 30:
            return "STRAIGHT"
        if 30 < relative < 150:
            return "RIGHT"
        if -150 < relative < -30:
            return "LEFT"
        return "BACK"

    def _edge_width(self, edge_id):
        edge = self.edge_map.get(edge_id, {})
        try:
            return max(0.1, float(edge.get("widthMeters", 1.2)))
        except (TypeError, ValueError):
            return 1.2

    @staticmethod
    def _occupancy_ratio(value):
        if isinstance(value, dict):
            if value.get("status") in {"UNKNOWN", "STALE"}:
                return 1.0
            value = value.get("filtered_k", value.get("measured_k", 1.0))
        try:
            return max(0.0, min(1.0, float(value)))
        except (TypeError, ValueError):
            return 1.0

    def _receiving_capacity(self, edge_id, occupancy):
        edge = self.edge_map.get(edge_id, {})
        width = self._edge_width(edge_id)
        area_a = self.area_map.get(edge.get("areaA_id"), {})
        area_b = self.area_map.get(edge.get("areaB_id"), {})
        is_stair_connection = (
            (
                area_a.get("type") == "stairs"
                and area_b.get("type") == "stairs"
            )
            or area_a.get("floor", 1) != area_b.get("floor", 1)
        )
        type_factor = 0.65 if is_stair_connection else 1.0
        return width * max(0.0, 1.0 - self._occupancy_ratio(occupancy)) * type_factor

    @staticmethod
    def _speaker_command(direction):
        return {
            "LEFT": "EVACUATE_LEFT",
            "RIGHT": "EVACUATE_RIGHT",
            "STRAIGHT": "EVACUATE_STRAIGHT",
            "BACK": "TURN_BACK",
            "UP": "GO_UPSTAIRS",
            "DOWN": "GO_DOWNSTAIRS",
            "EXIT": "EXIT_HERE",
            "NO_SAFE_ROUTE": "SHELTER_IN_PLACE",
        }.get(direction, "EVACUATE_STRAIGHT")

    def handle_ack(self, payload):
        device_id = payload.get("device_id")
        sequence = payload.get("sequence")
        if not device_id:
            return
        now = time.time()
        self.last_acks[device_id] = {
            "sequence": sequence,
            "status": payload.get("status", "ok"),
            "received_at": now,
            "detail": payload.get("detail"),
        }
        self.offline_devices.discard(device_id)
        pending = self.pending_acks.get(device_id)
        if pending and pending.get("sequence") == sequence:
            self.pending_acks.pop(device_id, None)

    def _device_status(self, device_id, now):
        pending = self.pending_acks.get(device_id)
        last_ack = self.last_acks.get(device_id)
        if pending and now - pending["sent_at"] > ACK_TIMEOUT_SECONDS:
            self.offline_devices.add(device_id)
            return "offline"
        if device_id in self.offline_devices:
            return "offline"
        if pending:
            return "waiting_ack"
        if last_ack:
            if now - last_ack["received_at"] <= DEVICE_OFFLINE_SECONDS:
                return "online"
            return "offline"
        if device_id in self.last_commands:
            return "waiting_ack"
        return "idle"

    def _publish_command(
        self, client, device, decision, now, force=False,
        edge_occupancy=None, blocked_edges=None, decisions=None, exits=None
    ):
        device_id = device.get("id")
        device_type = device.get("type", "sign")
        edge_id = device.get("edge_id")

        if edge_id and edge_id in self.edge_map:
            edge = self.edge_map[edge_id]
            is_blocked = bool(blocked_edges and edge_id in blocked_edges)
            raw_occ = (edge_occupancy or {}).get(edge_id, 0.0)
            occ = self._occupancy_ratio(raw_occ)
            is_overloaded = (occ >= 0.85)

            is_evac_route = any(
                (dec or {}).get("next_edge") == edge_id for dec in (decisions or {}).values()
            )
            area_a = self.area_map.get(edge.get("areaA_id"), {})
            area_b = self.area_map.get(edge.get("areaB_id"), {})
            leads_to_exit = (
                area_a.get("type") == "exit"
                or area_b.get("type") == "exit"
                or bool(exits and (edge.get("areaA_id") in exits or edge.get("areaB_id") in exits))
            )

            if is_blocked or is_overloaded:
                direction = "NO_SAFE_ROUTE"
                command = "STOP_DANGER"
            elif is_evac_route and leads_to_exit:
                node_id = device.get("node_id")
                if node_id == 1:
                    direction = "EXIT"
                    command = "SAFE_EXIT"
                else:
                    direction = "STRAIGHT"
                    command = "GO_STRAIGHT"
            elif is_evac_route:
                direction = "STRAIGHT"
                command = "GO_STRAIGHT"
            else:
                direction = "STANDBY"
                command = "STANDBY"

            target_edge = edge_id
            route_commands = []
        else:
            target_edge = decision.get("next_edge") if decision else None
            route_commands = []
            for route in (decision or {}).get("routes", []):
                route_edge = route.get("edge_id")
                route_commands.append(
                    {
                        "target_edge": route_edge,
                        "direction": self._infer_direction(device, route_edge),
                        "probability": route.get("probability", 0),
                    }
                )

            if decision and decision.get("at_exit"):
                direction = "EXIT"
            elif target_edge:
                direction = self._infer_direction(device, target_edge)
            else:
                direction = "NO_SAFE_ROUTE"

            command = (
                direction
                if device_type == "sign"
                else self._speaker_command(direction)
            )
        route_signature = tuple(
            (
                route.get("target_edge"),
                round(float(route.get("probability", 0)) * 20) / 20,
            )
            for route in route_commands
        )
        signature = (command, target_edge, route_signature)
        previous = self.last_commands.get(device_id)
        changed = not previous or previous.get("signature") != signature
        heartbeat_due = (
            previous
            and now - previous.get("sent_at", 0) >= COMMAND_HEARTBEAT_SECONDS
        )
        if not (force or changed or heartbeat_due):
            return previous.get("payload")

        self.sequence += 1
        payload = {
            "sequence": self.sequence,
            "device_id": device_id,
            "device_type": device_type,
            "area_id": device.get("area_id"),
            "edge_id": edge_id,
            "csi_link_id": device.get("csiLinkId"),
            "node_id": device.get("node_id"),
            "command": command,
            "direction": direction,
            "target_edge": target_edge,
            "probability": round(decision.get("probability", 0), 3)
            if decision
            else (1.0 if direction in ("EXIT", "STRAIGHT") else 0.0),
            "routes": route_commands,
            "generated_at": int(now),
            "valid_until": int(now) + COMMAND_VALID_SECONDS,
            "priority": "emergency" if command in ("STOP_DANGER", "SAFE_EXIT") else "normal",
        }
        payload["presentation"] = self._presentation(
            device, direction, decision, target_edge, payload["valid_until"]
        )
        topic = device.get("topic") or (
            f"building/guidance/{device_type}/{device_id}"
        )
        client.publish(
            topic,
            json.dumps(payload, ensure_ascii=False),
            qos=1,
            retain=True,
        )
        self.last_commands[device_id] = {
            "signature": signature,
            "sent_at": now,
            "payload": payload,
        }
        self.pending_acks[device_id] = {
            "sequence": self.sequence,
            "sent_at": now,
        }
        return payload

    def update(
        self,
        client,
        route_options,
        distances,
        edge_occupancy,
        exits,
        blocked_edges,
        force=False,
    ):
        now = time.time()
        previous_decisions = self.latest_state.get("decisions", {})
        decisions = {}
        for area_id in self.area_map:
            if area_id in exits:
                decisions[area_id] = {
                    "at_exit": True,
                    "next_edge": None,
                    "probability": 1.0,
                    "distance": 0.0,
                    "routes": [],
                }
                continue
            options = [
                option
                for option in route_options.get(area_id, [])
                if option["edge_id"] not in blocked_edges
            ]
            if options:
                sorted_options = sorted(
                    options, key=lambda option: option.get("share", 0), reverse=True
                )[:2]
                route_total = sum(option.get("share", 0) for option in sorted_options)
                routes = [
                    {
                        "next_area": option["next_area"],
                        "edge_id": option["edge_id"],
                        "probability": round(option.get("share", 0) / route_total, 3),
                        "k": round(self._occupancy_ratio(edge_occupancy.get(option["edge_id"], 0.0)), 3),
                        "widthMeters": self._edge_width(option["edge_id"]),
                        "receivingCapacity": round(
                            self._receiving_capacity(
                                option["edge_id"],
                                edge_occupancy.get(option["edge_id"], 0.0),
                            ),
                            3,
                        ),
                    }
                    for option in sorted_options
                ]
                primary = routes[0]
                decisions[area_id] = {
                    "at_exit": False,
                    "next_edge": primary["edge_id"],
                    "probability": primary["probability"],
                    "distance": round(distances.get(area_id, 0.0), 2),
                    "k": primary["k"],
                    "routes": routes,
                }
            else:
                decisions[area_id] = {
                    "at_exit": False,
                    "next_edge": None,
                    "probability": 0.0,
                    "distance": None,
                    "routes": [],
                }

        route_events = []
        for area_id, decision in decisions.items():
            previous_edge = previous_decisions.get(area_id, {}).get("next_edge")
            current_edge = decision.get("next_edge")
            if previous_edge != current_edge:
                area_name = self.area_map.get(area_id, {}).get("name", area_id)
                if current_edge:
                    edge_name = self.edge_map.get(current_edge, {}).get("name", current_edge)
                    route_events.append({
                        "type": "routing",
                        "message": f"D* Lite đổi tuyến tại {area_name}: đi qua {edge_name}.",
                        "time": time.strftime("%H:%M:%S"),
                    })
                elif not decision.get("at_exit"):
                    route_events.append({
                        "type": "alert",
                        "message": f"Không còn đường thoát tại {area_name}.",
                        "time": time.strftime("%H:%M:%S"),
                    })

        device_states = []
        device_events = []
        for device in self.devices:
            device_id = device.get("id")
            decision = decisions.get(device.get("area_id"))
            previous_signature = self.last_commands.get(device_id, {}).get("signature")
            command_payload = self._publish_command(
                client, device, decision, now, force=force,
                edge_occupancy=edge_occupancy, blocked_edges=blocked_edges,
                decisions=decisions, exits=exits
            )
            current_signature = self.last_commands.get(device_id, {}).get("signature")
            if command_payload and previous_signature != current_signature:
                target_edge = command_payload.get("target_edge")
                edge_info = self.edge_map.get(target_edge, {})
                edge_title = edge_info.get("name", target_edge) if target_edge else "khu vực"
                loc_desc = f"trên hành lang {edge_title}" if device.get("edge_id") else f"→ {edge_title}"
                device_events.append({
                    "type": "guidance",
                    "message": f"{device.get('name', device_id)}: {command_payload.get('command')} {loc_desc}",
                    "time": time.strftime("%H:%M:%S"),
                })
            device_states.append(
                {
                    "id": device_id,
                    "name": device.get("name", device_id),
                    "type": device.get("type", "sign"),
                    "area_id": device.get("area_id"),
                    "edge_id": device.get("edge_id"),
                    "csi_link_id": device.get("csiLinkId"),
                    "node_id": device.get("node_id"),
                    "status": self._device_status(device_id, now),
                    "last_command": command_payload,
                    "last_ack": self.last_acks.get(device_id),
                }
            )

        self.latest_state = {
            "generated_at": int(now),
            "decisions": decisions,
            "devices": device_states,
        }
        state_signature = (
            tuple(sorted((area_id, item.get("next_edge"), item.get("distance")) for area_id, item in decisions.items())),
            tuple((item["id"], item["status"], (item.get("last_command") or {}).get("sequence")) for item in device_states),
        )
        if state_signature != self._last_state_signature or now - self._last_state_published_at >= 2.0:
            client.publish(
                "building/guidance/state",
                json.dumps(self.latest_state, ensure_ascii=False),
                qos=1,
                retain=True,
            )
            self._last_state_signature = state_signature
            self._last_state_published_at = now
        if route_events or device_events:
            client.publish(
                "building/log",
                json.dumps({"events": route_events + device_events}, ensure_ascii=False),
                qos=1,
            )
        return self.latest_state

    def stop_all(self, client):
        """Put every configured device into a safe standby state."""
        now = time.time()
        for device in self.devices:
            self.sequence += 1
            device_id = device.get("id")
            device_type = device.get("type", "sign")
            payload = {
                "sequence": self.sequence,
                "device_id": device_id,
                "device_type": device_type,
                "area_id": device.get("area_id"),
                "command": "STANDBY",
                "direction": "STANDBY",
                "target_edge": None,
                "routes": [],
                "generated_at": int(now),
                "valid_until": int(now) + COMMAND_VALID_SECONDS,
                "priority": "control",
            }
            topic = device.get("topic") or (
                f"building/guidance/{device_type}/{device_id}"
            )
            client.publish(
                topic,
                json.dumps(payload, ensure_ascii=False),
                qos=1,
                retain=True,
            )
            self.last_commands[device_id] = {
                "signature": ("STANDBY", None),
                "sent_at": now,
                "payload": payload,
            }
            self.pending_acks[device_id] = {
                "sequence": self.sequence,
                "sent_at": now,
            }
        self.latest_state = {
            "generated_at": int(now),
            "decisions": {},
            "devices": [
                {
                    "id": device.get("id"),
                    "name": device.get("name", device.get("id")),
                    "type": device.get("type", "sign"),
                    "area_id": device.get("area_id"),
                    "status": self._device_status(device.get("id"), now),
                    "last_command": self.last_commands.get(
                        device.get("id"), {}
                    ).get("payload"),
                }
                for device in self.devices
            ],
        }
        client.publish(
            "building/guidance/state",
            json.dumps(self.latest_state, ensure_ascii=False),
            qos=1,
            retain=True,
        )
