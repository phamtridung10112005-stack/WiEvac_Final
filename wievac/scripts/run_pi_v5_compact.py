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
from wievac.pi.app.edge_result_v5 import EdgeResultProtocolError, decode_edge_result  # noqa: E402
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
        # An active allow-list must reject a valid packet whose link has no
        # configured endpoint; returning ``None`` would accidentally make it
        # open to every source.
        return self.endpoint_allowlist.get(str(candidate.link_id), ("__unlisted__", -1))

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
                        self._send_json(runtime.service.response("/api/v5/overview"))
                    elif path == "/api/v5/latest":
                        self._send_json(runtime.service.response(path))
                    elif path == "/api/v5/recorder":
                        self._send_json(runtime.service.response(path))
                    elif path == "/api/v5/nodes":
                        self._send_json(runtime.service.list_nodes())
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

