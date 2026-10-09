#!/usr/bin/env python3
"""Run the active compact EdgeResult V5 Pi service.

Pi receives already-scored EdgeResult datagrams, validates and records them
through :class:`EdgeResultV5Service`, and serves the compact dashboard/API.
"""

from __future__ import annotations

import argparse
import json
import os
import socket
import struct
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any, Mapping, Optional, Tuple
from urllib.parse import unquote, urlparse
import sys

ROOT = Path(__file__).resolve().parents[1]
STATIC_ROOT = ROOT / "pi" / "app" / "static"
_STATIC_TYPES = {
    ".png": "image/png",
    ".jpg": "image/jpeg",
    ".jpeg": "image/jpeg",
    ".svg": "image/svg+xml",
    ".ico": "image/x-icon",
    ".webp": "image/webp",
}
if str(ROOT.parent) not in sys.path:
    sys.path.insert(0, str(ROOT.parent))

# Keep imports explicit: the active runner only handles compact EdgeResult.
from wievac.pi.app.edge_result_v5 import EdgeResultProtocolError, EdgeResultV5, decode_edge_result  # noqa: E402
from wievac.pi.app.edge_result_v5_dashboard import render_dashboard_html  # noqa: E402
from wievac.pi.app.edge_result_v5_service import EdgeResultV5Service, V5ServiceConfig  # noqa: E402


def _env_int(name: str, default: int, minimum: int = 1, maximum: int = 65535) -> int:
    value = int(os.getenv(name, str(default)))
    if not minimum <= value <= maximum:
        raise ValueError(f"{name} out of bounds")
    return value


def _load_endpoint_allowlist() -> dict[str, Tuple[str, int]]:
    encoded = os.getenv("WIEVAC_RX_ENDPOINTS", "").strip()
    if not encoded:
        return {}
    value = json.loads(encoded)
    if not isinstance(value, Mapping):
        raise ValueError("WIEVAC_RX_ENDPOINTS must be JSON link_id to [host, port]")
    endpoints: dict[str, Tuple[str, int]] = {}
    for link_id, endpoint in value.items():
        if not isinstance(endpoint, (list, tuple)) or len(endpoint) != 2:
            raise ValueError("WIEVAC_RX_ENDPOINTS entries must be [host, port]")
        host = str(endpoint[0]).strip()
        port = int(endpoint[1])
        socket.inet_aton(host)
        if not 1 <= port <= 65535:
            raise ValueError("WIEVAC_RX_ENDPOINTS port out of bounds")
        endpoints[str(link_id)] = (host, port)
    return endpoints


def _config_path() -> Path:
    return Path(os.getenv(
        "WIEVAC_V5_TOPOLOGY_CONFIG",
        os.getenv(
            "WIEVAC_TOPOLOGY_CONFIG",
            str(ROOT / "config" / "corridors" / "corridor-01-edge-result-v5.json"),
        ),
    ))


class CompactPiRuntime:
    """Small UDP/API host around the compact service adapter."""

    def __init__(self, *, topology_path: Path, bind_host: str, udp_port: int,
                 dashboard_host: str, dashboard_port: int,
                 recorder_directory: Optional[str] = None,
                 label_directory: Optional[str] = None) -> None:
        self.topology_path = topology_path
        self.bind_host = bind_host
        self.udp_port = udp_port
        self.dashboard_host = dashboard_host
        self.dashboard_port = dashboard_port
        self.service = EdgeResultV5Service(config=V5ServiceConfig(
            topology_path=str(topology_path),
            stale_after_ms=int(os.getenv("WIEVAC_STALE_AFTER_MS", "3000")),
            recorder_directory=recorder_directory,
            label_directory=label_directory,
            schedule_path=str(Path(label_directory).parent / "capture-schedules.json") if label_directory else None,
            enable_reference_modules=False,
        ))
        self.endpoint_allowlist = _load_endpoint_allowlist()
        self.mesh_routes: dict[str, dict[str, Any]] = {}
        self.gateway_ips: dict[int, str] = {}
        self.guidance_history: list[dict[str, Any]] = []
        self.guidance_seq: int = 1000
        self._stop = threading.Event()
        self._lock = threading.RLock()
        self._udp_ready = threading.Event()
        self._udp_thread: Optional[threading.Thread] = None
        self._udp_socket: Optional[socket.socket] = None
        self._http: Optional[ThreadingHTTPServer] = None

    @property
    def udp_running(self) -> bool:
        return self._udp_ready.is_set() and not self._stop.is_set()

    def _expected_endpoint(self, packet: bytes) -> Optional[Tuple[str, int]]:
        if not self.endpoint_allowlist:
            return None
        try:
            candidate = decode_edge_result(packet)
        except (EdgeResultProtocolError, ValueError, TypeError, OverflowError):
            return None
        return self.endpoint_allowlist.get(str(candidate.link_id), ("__unlisted__", -1))

    def _active_gateway_id(self) -> int:
        for link_id, state in getattr(self.service.ingest, "links", {}).items():
            if state.latest and getattr(state.latest, "session_id", "") != "mesh":
                try:
                    return int(link_id.replace("link-", ""))
                except (ValueError, AttributeError):
                    pass
        return 1

    @staticmethod
    def _mesh_header(packet: bytes) -> Optional[tuple]:
        """wievac_mesh_packet_t, packed: magic u32, ver, type, origin, last_hop, seq u32, hops, ttl, flags, rssi i8."""
        if len(packet) < 16:
            return None
        try:
            magic, _version, mtype, origin, last_hop, seq, hops, ttl, flags, rssi = struct.unpack_from(
                "<IBBBBIBBBb", packet, 0
            )
        except struct.error:
            return None
        if magic != 0x57455643:
            return None
        return mtype, origin, last_hop, seq, hops, ttl, flags, rssi

    def _handle_mesh_packet(self, packet: bytes, endpoint: Tuple[str, int]) -> bool:
        if len(packet) < 29:
            return False
        header = self._mesh_header(packet)
        if header is None:
            return False
        mtype, origin_id, last_hop_id, seq, hops, ttl, flags, rssi = header
        if last_hop_id:
            self.gateway_ips[int(last_hop_id)] = str(endpoint[0])

        if mtype == 6:  # WIEVAC_MSG_CONTROL_ACK
            try:
                cmd_id = struct.unpack("<I", packet[20:24])[0]
                status = packet[19]
                print(f"[DOWNLINK ACK] Pi đã nhận phản hồi ACK từ Node {origin_id} cho cmd_id={cmd_id}, status={status}", flush=True)
                for item in reversed(self.guidance_history):
                    if item.get("command_id") == cmd_id:
                        item["ack_received"] = True
                        item["ack_from"] = origin_id
                        item["ack_time"] = time.time()
                        item["rtt_ms"] = round((item["ack_time"] - item["sent_time"]) * 1000, 1)
                        break
            except Exception:
                pass
            return True

        try:
            f_node, std_dev, mean_amp, ts = struct.unpack("<BffI", packet[16:29])
        except Exception:
            std_dev, mean_amp = 0.0, 0.0

        link_id = f"link-{origin_id}"
        now_us = int(time.monotonic_ns() // 1000)
        state = self.service.ingest.links.get(link_id)
        is_direct_active = (
            state is not None
            and state.latest is not None
            and getattr(state.latest, "session_id", "") != "mesh"
            and not state.is_stale(now_us, 3000)
        )
        if is_direct_active:
            # Node has an active direct Wi-Fi link to Pi 5; discard redundant relayed mesh copy
            return True

        if mtype == 2:  # WIEVAC_MSG_HEARTBEAT
            return True

        is_relay = (hops > 0) or bool(flags & 0x02)
        role = "RELAY" if is_relay else "GATEWAY"
        if is_relay:
            gw_id = self._active_gateway_id()
            if last_hop_id != origin_id and last_hop_id != 0:
                if hops > 1 and last_hop_id != gw_id:
                    route_path = f"Node {origin_id} ➔ Node {last_hop_id} ➔ Node {gw_id} (Gateway) ➔ Pi 5"
                    last_hop_str = f"Node {last_hop_id}"
                else:
                    route_path = f"Node {origin_id} ➔ Node {last_hop_id} (Gateway) ➔ Pi 5"
                    last_hop_str = f"Node {last_hop_id}"
            else:
                route_path = f"Node {origin_id} ➔ Node {gw_id} (Gateway) ➔ Pi 5"
                last_hop_str = f"Node {gw_id}"
        else:
            route_path = f"Node {origin_id} ➔ Pi 5 (Direct)"
            last_hop_str = f"Node {origin_id}"
        is_lost = bool(flags & 0x10)
        is_recovered = bool(flags & 0x20)

        self.mesh_routes[link_id] = {
            "is_relay": is_relay,
            "role": role,
            "hop_count": hops,
            "last_hop": last_hop_str,
            "route_path": route_path,
            "link_rssi": rssi,
            "is_lost": is_lost,
            "is_recovered": is_recovered,
            "std_dev": std_dev,
            "mean_amp": mean_amp,
            "last_seen_s": time.time(),
        }

        # Synthesize or update link state in runtime so dashboard sees it immediately
        try:
            state = self.service.ingest._state_for(link_id)
            state.latest_arrival_us = now_us
            state.accepted_count += 1
            state.latest_revision += 1
            calc_score = None if is_lost else max(0.0, min(100.0, 100.0 - std_dev * 25.0))
            calc_state = "STALE" if is_lost else ("PASSABLE" if (calc_score or 0) >= 60.0 else "DEGRADED")
            state.latest = EdgeResultV5(
                device_id="device-1",
                node_id=f"rx-{origin_id}",
                tx_id="tx-1",
                rx_id=f"rx-{origin_id}",
                link_id=link_id,
                corridor_id="corridor-01",
                session_id="mesh",
                boot_id=1,
                window_seq=seq,
                window_start_us=now_us - 1000000,
                window_end_us=now_us,
                rx_timestamp_us=now_us,
                age_ms=0,
                local_passability_score=calc_score,
                state=calc_state,
                quality=max(10.0, min(100.0, 100.0 + rssi)) if rssi < 0 else 85.0,
                uncertainty=0.1,
                disagreement=False,
                reason_code=4 if is_lost else 1,
                formula_version="mesh-v1",
                model_version="NOT_READY",
                model_hash="",
                feature_schema_version="7",
                sample_count=100,
                invalid_count=0,
                queue_drop_count=0,
                sequence_gap=0,
                packet_loss_ratio=0.0,
                jitter_ms=1.5,
            )
        except Exception:
            pass
        return True

    def enrich_overview(self, overview: Mapping[str, Any]) -> dict[str, Any]:
        data = dict(overview)
        links = data.get("links", {})
        now_us = int(time.monotonic_ns() // 1000)
        now_s = time.time()

        # 1. Clean up expired mesh routes (older than 3.0s)
        expired_mesh = [
            lid for lid, meta in self.mesh_routes.items()
            if now_s - meta.get("last_seen_s", 0) > 3.0
        ]
        for lid in expired_mesh:
            self.mesh_routes.pop(lid, None)

        for link_id, link_info in links.items():
            state = self.service.ingest.links.get(link_id)
            node_label = link_id.replace("link-", "Node ")
            is_direct_active = (
                state is not None
                and state.latest is not None
                and getattr(state.latest, "session_id", "") != "mesh"
                and not state.is_stale(now_us, 3000)
            )

            # Check if relay route is valid and gateway node is alive
            has_valid_relay = False
            if not is_direct_active and link_id in self.mesh_routes:
                meta = self.mesh_routes[link_id]
                last_hop_str = meta.get("last_hop", "")
                if last_hop_str.startswith("Node "):
                    gw_link_id = "link-" + last_hop_str.replace("Node ", "").strip()
                    gw_state = self.service.ingest.links.get(gw_link_id)
                    # If the gateway node is stale (e.g. Node 2 is turned off), this relay route is dead!
                    if gw_state is None or gw_state.is_stale(now_us, 3000):
                        self.mesh_routes.pop(link_id, None)
                    else:
                        has_valid_relay = True
                else:
                    has_valid_relay = True

            if is_direct_active or not has_valid_relay:
                link_info["is_relay"] = False
                link_info["role"] = "GATEWAY"
                link_info["hop_count"] = 0
                link_info["last_hop"] = node_label
                link_info["route_path"] = f"{node_label} ➔ Pi 5 (Direct)"
                self.mesh_routes.pop(link_id, None)
            else:
                meta = self.mesh_routes[link_id]
                link_info["is_relay"] = meta.get("is_relay", False)
                link_info["role"] = meta.get("role", "GATEWAY")
                link_info["hop_count"] = meta.get("hop_count", 0)
                link_info["last_hop"] = meta.get("last_hop", "")
                link_info["route_path"] = meta.get("route_path", "")
                link_info["link_rssi"] = meta.get("link_rssi", None)
                if meta.get("is_lost"):
                    link_info["state"] = "STALE"
                    link_info["stale"] = True
                    link_info["reason"] = "node_lost_detected_by_peer"
                    link_info["score"] = None
        return data

    def _udp_loop(self) -> None:
        sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        sock.settimeout(0.25)
        try:
            sock.bind((self.bind_host, self.udp_port))
            self._udp_socket = sock
            self._udp_ready.set()
            while not self._stop.is_set():
                try:
                    packet, endpoint = sock.recvfrom(65535)
                except socket.timeout:
                    continue
                except OSError:
                    break
                with self._lock:
                    if endpoint and len(endpoint) == 2:
                        try:
                            if packet.startswith(b"WIV5"):
                                candidate = decode_edge_result(packet)
                                nid = int(str(candidate.link_id).replace("link-", ""))
                                self.gateway_ips[nid] = str(endpoint[0])
                        except Exception:
                            pass
                    if self._handle_mesh_packet(packet, (str(endpoint[0]), int(endpoint[1]))):
                        continue
                    self.service.ingest_datagram(
                        packet,
                        endpoint=(str(endpoint[0]), int(endpoint[1])),
                        expected_endpoint=self._expected_endpoint(packet),
                    )
        finally:
            self._udp_ready.clear()
            try:
                sock.close()
            except OSError:
                pass
            self._udp_socket = None

    def send_downlink_command(self, target_node_id: int, command: int, priority: int = 1) -> dict[str, Any]:
        self.guidance_seq += 1
        cmd_id = self.guidance_seq
        # Header: magic(4), ver(1), type(1), origin(1), last_hop(1), seq(4), hops(1), ttl(1), flags(1), rssi(1)
        header = struct.pack('<IBBBBIBBBb', 0x57455643, 1, 5, 0, 0, cmd_id, 0, 12, 0, 0)
        # Payload: target(1), cmd(1), prio(1), status(1), cmd_id(4), dur(2), route(2), attempt(1)
        payload = struct.pack('<BBBBIHHB', target_node_id, command, priority, 0, cmd_id, 30, 1, 1)
        packet = header + payload

        # Select active gateway IP to forward down to the mesh
        active_gw_id = self._active_gateway_id()
        gw_ip = self.gateway_ips.get(active_gw_id) or self.gateway_ips.get(1)
        if not gw_ip and self.gateway_ips:
            gw_ip = next(iter(self.gateway_ips.values()))
        if not gw_ip:
            gw_ip = "10.42.0.218"

        sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        try:
            sock.sendto(packet, (gw_ip, 8889))
        finally:
            sock.close()

        record = {
            "command_id": cmd_id,
            "target_node_id": target_node_id,
            "command": command,
            "priority": priority,
            "gateway_ip": gw_ip,
            "sent_time": time.time(),
            "ack_received": False,
            "rtt_ms": None,
        }
        self.guidance_history.append(record)
        if len(self.guidance_history) > 50:
            self.guidance_history.pop(0)
        print(f"[DOWNLINK SEND] Đã gửi lệnh {command} tới Node {target_node_id} qua Gateway {gw_ip}:8889 (cmd_id={cmd_id})", flush=True)
        return record

    def start(self) -> None:
        self._udp_thread = threading.Thread(target=self._udp_loop, name="wievac-v5-udp", daemon=True)
        self._udp_thread.start()
        deadline = time.monotonic() + 5.0
        while not self._udp_ready.is_set() and time.monotonic() < deadline:
            if self._udp_thread and not self._udp_thread.is_alive():
                raise RuntimeError("compact UDP listener failed to start")
            time.sleep(0.01)
        if not self._udp_ready.is_set():
            raise RuntimeError("compact UDP listener did not bind before timeout")

        runtime = self

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, _format: str, *args: Any) -> None:
                return

            def _send_json(self, payload: Mapping[str, Any], status: int = 200) -> None:
                body = json.dumps(payload, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
                self.send_response(status)
                self.send_header("Content-Type", "application/json; charset=utf-8")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)

            def _request_path(self) -> str:
                return urlparse(self.path).path

            def _read_json(self) -> Mapping[str, Any]:
                length = int(self.headers.get("Content-Length", "0") or 0)
                if length < 0 or length > 65_536:
                    raise ValueError("invalid_payload")
                raw = self.rfile.read(length) if length else b"{}"
                if not raw:
                    return {}
                value = json.loads(raw.decode("utf-8"))
                if value is None:
                    return {}
                if not isinstance(value, Mapping):
                    raise ValueError("invalid_payload")
                return value

            def _node_error(self, code: str) -> None:
                status = 404 if code == "not_found" else 409 if code.startswith("duplicate") or code == "dynamic_link_limit" else 400
                self._send_json({"error": code}, status=status)

            def _node_link_id(self, path: str) -> Optional[str]:
                prefix = "/api/v5/nodes/"
                if path.startswith(prefix) and len(path) > len(prefix):
                    return unquote(path[len(prefix):])
                return None

            def _send_static(self, url_path: str) -> None:
                relative = unquote(url_path[len("/static/"):]).replace("\\", "/")
                if not relative or relative.startswith("/") or ".." in relative.split("/"):
                    self._send_json({"error": "not_found"}, status=404)
                    return
                target = (STATIC_ROOT / relative).resolve()
                try:
                    target.relative_to(STATIC_ROOT.resolve())
                except ValueError:
                    self._send_json({"error": "not_found"}, status=404)
                    return
                if not target.is_file():
                    self._send_json({"error": "not_found"}, status=404)
                    return
                body = target.read_bytes()
                content_type = _STATIC_TYPES.get(target.suffix.lower(), "application/octet-stream")
                self.send_response(200)
                self.send_header("Content-Type", content_type)
                self.send_header("Content-Length", str(len(body)))
                self.send_header("Cache-Control", "public, max-age=3600")
                self.end_headers()
                self.wfile.write(body)

            def do_GET(self) -> None:  # noqa: N802
                path = self._request_path()
                if path.startswith("/static/"):
                    self._send_static(path)
                    return
                with runtime._lock:
                    if path == "/api/health":
                        self._send_json({"status": "ok", "udp_running": runtime.udp_running,
                                         "active_scope": "compact_edge_result_only"})
                    elif path in {"/api/status", "/api/v5/overview"}:
                        self._send_json(runtime.enrich_overview(runtime.service.response("/api/v5/overview")))
                    elif path == "/api/v5/latest":
                        self._send_json(runtime.service.response(path))
                    elif path == "/api/v5/recorder":
                        self._send_json(runtime.service.response(path))
                    elif path == "/api/v5/nodes":
                        self._send_json(runtime.service.list_nodes())
                    elif path == "/api/v5/guidance":
                        self._send_json({"history": runtime.guidance_history[-10:], "gateways": runtime.gateway_ips})
                    elif path in {"/", "/v5"}:
                        body = render_dashboard_html().encode("utf-8")
                        self.send_response(200)
                        self.send_header("Content-Type", "text/html; charset=utf-8")
                        self.send_header("Cache-Control", "no-store, no-cache, must-revalidate")
                        self.send_header("Pragma", "no-cache")
                        self.send_header("Content-Length", str(len(body)))
                        self.end_headers()
                        self.wfile.write(body)
                    else:
                        self._send_json({"error": "not_found"}, status=404)

            def do_POST(self) -> None:  # noqa: N802
                path = self._request_path()
                with runtime._lock:
                    if path == "/api/v5/guidance":
                        try:
                            payload = self._read_json()
                            target = int(payload.get("target", 1))
                            command = int(payload.get("command", 11))
                            priority = int(payload.get("priority", 1))
                            res = runtime.send_downlink_command(target, command, priority)
                            self._send_json({"status": "ok", "result": res})
                        except Exception as exc:
                            self._node_error(str(exc) or "guidance_send_failed")
                        return
                    if path == "/api/v5/recorder":
                        try:
                            payload = self._read_json()
                            self._send_json(runtime.service.control_recording(payload))
                        except (ValueError, json.JSONDecodeError) as exc:
                            self._node_error(str(exc) or "invalid_payload")
                        return
                    if path == "/api/v5/labels":
                        try:
                            payload = self._read_json()
                            self._send_json(runtime.service.set_occupancy_label(
                                str(payload.get("link_id") or ""),
                                str(payload.get("occupancy") or ""),
                            ))
                        except (ValueError, json.JSONDecodeError) as exc:
                            self._node_error(str(exc) or "invalid_payload")
                        return
                    if path != "/api/v5/nodes":
                        self._send_json({"error": "not_found"}, status=404)
                        return
                    try:
                        payload = self._read_json()
                        self._send_json(runtime.service.create_node(payload), status=201)
                    except (ValueError, json.JSONDecodeError) as exc:
                        self._node_error(str(exc) or "invalid_payload")

            def do_PATCH(self) -> None:  # noqa: N802
                path = self._request_path()
                link_id = self._node_link_id(path)
                with runtime._lock:
                    if link_id is None:
                        self._send_json({"error": "not_found"}, status=404)
                        return
                    try:
                        payload = self._read_json()
                        self._send_json(runtime.service.patch_node(link_id, payload))
                    except (ValueError, json.JSONDecodeError) as exc:
                        self._node_error(str(exc) or "invalid_payload")

            def do_DELETE(self) -> None:  # noqa: N802
                path = self._request_path()
                link_id = self._node_link_id(path)
                with runtime._lock:
                    if link_id is None:
                        self._send_json({"error": "not_found"}, status=404)
                        return
                    try:
                        if int(self.headers.get("Content-Length", "0") or 0) > 0:
                            self._read_json()
                        self._send_json(runtime.service.delete_node(link_id))
                    except (ValueError, json.JSONDecodeError) as exc:
                        self._node_error(str(exc) or "invalid_payload")

        self._http = ThreadingHTTPServer((self.dashboard_host, self.dashboard_port), Handler)
        self._http.daemon_threads = True
        threading.Thread(target=self._http.serve_forever, name="wievac-v5-dashboard", daemon=True).start()

    def stop(self) -> None:
        self._stop.set()
        if self._http is not None:
            self._http.shutdown()
            self._http.server_close()
            self._http = None
        if self._udp_socket is not None:
            try:
                self._udp_socket.close()
            except OSError:
                pass
        if self._udp_thread is not None:
            self._udp_thread.join(timeout=2.0)
        with self._lock:
            if self.service.session_id is not None:
                self.service.stop_recording(success=True)


def main(argv: Optional[list[str]] = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--check-only", action="store_true", help="validate config and bind ports, then exit")
    parser.add_argument("--startup-timeout", type=float, default=5.0)
    args = parser.parse_args(argv)
    if args.startup_timeout <= 0:
        parser.error("--startup-timeout must be positive")
    try:
        config = _config_path()
        bind_host = os.getenv("WIEVAC_PI_BIND_HOST", "0.0.0.0").strip()
        udp_port = _env_int("WIEVAC_UDP_PORT", 8888)
        dashboard_host = os.getenv("WIEVAC_DASHBOARD_BIND", "0.0.0.0").strip()
        dashboard_port = _env_int("WIEVAC_DASHBOARD_PORT", 8080)
        recorder_directory = (os.getenv("WIEVAC_RECORDER_DIRECTORY") or "").strip() or None
        label_directory = (os.getenv("WIEVAC_LABEL_DIRECTORY") or "").strip() or None
        if not args.check_only:
            if recorder_directory is None:
                recorder_directory = str(ROOT / "data" / "real" / "compact")
            if label_directory is None:
                label_directory = str(ROOT / "data" / "labels")
        runtime = CompactPiRuntime(
            topology_path=config, bind_host=bind_host, udp_port=udp_port,
            dashboard_host=dashboard_host, dashboard_port=dashboard_port,
            recorder_directory=recorder_directory,
            label_directory=label_directory,
        )
        if args.check_only:
            # Bind both sockets briefly so deployment checks catch collisions.
            runtime.start()
            runtime.stop()
            print(f"Pi compact V5 checks passed; udp={bind_host}:{udp_port} dashboard={dashboard_host}:{dashboard_port}")
            return 0
        runtime.start()
        print(f"Pi compact V5 active; udp={bind_host}:{udp_port} dashboard={dashboard_host}:{dashboard_port}", flush=True)
        try:
            while True:
                time.sleep(0.5)
                with runtime._lock:
                    runtime.service.expire_recording()
        except KeyboardInterrupt:
            return 0
        finally:
            runtime.stop()
    except (OSError, ValueError, RuntimeError, json.JSONDecodeError) as exc:
        print(f"Pi compact V5 startup failed: {exc}", flush=True)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())

