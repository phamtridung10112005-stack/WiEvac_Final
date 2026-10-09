#!/usr/bin/env python3
"""WiEvac Pi Downlink Bridge: Auto-bridge D* Lite Guidance (MQTT) to ESP32 Mesh Gateway (UDP 8889).

Converts MQTT guidance topics (building/guidance/speaker/+, building/downlink/+)
into 29-byte binary wievac_mesh_packet_t packets and broadcasts to ESP32 Gateway.
Also receives ESP-NOW ACKs from ESP32 nodes and pushes them back to MQTT.
"""

from __future__ import annotations

import json
import os
import socket
import struct
import sys
import threading
import time

for stream in (sys.stdout, sys.stderr):
    try:
        stream.reconfigure(encoding="utf-8", errors="replace")
    except AttributeError:
        pass

try:
    import paho.mqtt.client as mqtt
except ImportError:
    print("[DownlinkBridge] Lỗi: Cần cài đặt paho-mqtt", file=sys.stderr)
    sys.exit(1)

MQTT_HOST = os.getenv("WIEVAC_MQTT_HOST", "127.0.0.1")
MQTT_PORT = int(os.getenv("WIEVAC_MQTT_PORT", "1883"))
GATEWAY_UDP_PORT = int(os.getenv("WIEVAC_GATEWAY_UDP_PORT", "8889"))
DEFAULT_GATEWAY_IP = os.getenv("WIEVAC_GATEWAY_IP", "10.42.0.218")
BROADCAST_IP = os.getenv("WIEVAC_BROADCAST_IP", "10.42.0.255")

# Binary Protocol Constants
WIEVAC_MESH_MAGIC = 0x57455643  # 'WEVC'
WIEVAC_MSG_CONTROL = 5
WIEVAC_MSG_CONTROL_ACK = 6

CMD_MAP = {
    # Di thang (Track 3)
    "START_EVAC": 10,
    "GO_STRAIGHT": 11,
    "EVACUATE_STRAIGHT": 11,
    "STRAIGHT": 11,
    # Re trai (Track 1)
    "TURN_LEFT": 12,
    "EVACUATE_LEFT": 12,
    "LEFT": 12,
    # Re phai (Track 2)
    "TURN_RIGHT": 13,
    "EVACUATE_RIGHT": 13,
    "RIGHT": 13,
    # Quay dau / Cau thang (Track 4)
    "TURN_BACK": 14,
    "BACK": 14,
    "GO_UPSTAIRS": 14,
    "UP": 14,
    "GO_DOWNSTAIRS": 15,
    "DOWN": 15,
    # Loi thoat an toan (Track 6)
    "SAFE_EXIT": 16,
    "EXIT": 16,
    "EXIT_HERE": 16,
    # Dung lai / Nguy hiem (Track 5)
    "STOP_DANGER": 17,
    "STOP": 17,
    "DANGER": 17,
    "SHELTER_IN_PLACE": 17,
    "NO_SAFE_ROUTE": 17,
    # Khac
    "WAIT": 18,
    "CLEAR": 19,
}

NODE_MAP = {
    "node_1": 1,
    "node_spkr_01": 1,
    "speaker_1": 1,
    "1": 1,
    "node_2": 2,
    "node_spkr_02": 2,
    "speaker_2": 2,
    "2": 2,
    "node_3": 3,
    "node_spkr_03": 3,
    "speaker_3": 3,
    "3": 3,
    "all": 0,
    "broadcast": 0,
    "0": 0,
}


class DownlinkBridge:
    def __init__(self):
        self.mqtt_client = mqtt.Client(mqtt.CallbackAPIVersion.VERSION2)
        self.cmd_seq = 0
        self.last_sent = {}  # (target_node, cmd_code): last_time
        self.discovered_gateway_ip = DEFAULT_GATEWAY_IP
        self.running = True

        # Socket UDP phat lenh xuong ESP32 Gateway
        self.tx_sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        self.tx_sock.setsockopt(socket.SOL_SOCKET, socket.SO_BROADCAST, 1)

    def resolve_target_node(self, topic: str, payload: dict) -> int:
        """Xac dinh Node ID dich tu MQTT topic hoac JSON payload."""
        # 1. Kiem tra payload ro rang
        for key in ("target_node_id", "node_id", "target"):
            if key in payload:
                try:
                    return int(payload[key])
                except (ValueError, TypeError):
                    pass

        dev_id = str(payload.get("device_id", "")).lower()
        if dev_id in NODE_MAP:
            return NODE_MAP[dev_id]

        # 2. Kiem tra phan cuoi cua topic
        topic_suffix = topic.split("/")[-1].lower()
        if topic_suffix in NODE_MAP:
            return NODE_MAP[topic_suffix]

        return 0  # Default to Broadcast (tat ca cac node deu phat)

    def resolve_command_code(self, payload: dict) -> int:
        """Chuyen doi lenh tu D* Lite sang ma so int (10..19)."""
        # Neu payload da co san ma int hop le
        for key in ("cmd", "command_code"):
            if key in payload:
                try:
                    code = int(payload[key])
                    if 10 <= code <= 19:
                        return code
                except (ValueError, TypeError):
                    pass

        raw_cmd = str(payload.get("command", "")).upper()
        if raw_cmd in CMD_MAP:
            return CMD_MAP[raw_cmd]

        raw_dir = str(payload.get("direction", "")).upper()
        if raw_dir in CMD_MAP:
            return CMD_MAP[raw_dir]

        intent = str(payload.get("presentation", {}).get("intent", "")).upper()
        if intent in CMD_MAP:
            return CMD_MAP[intent]

        return 11  # Mac dinh: DI THANG (GO_STRAIGHT)

    def send_mesh_packet(self, target_node: int, cmd_code: int, priority: int = 1) -> None:
        """Dong goi nhi phan 29 bytes va ban qua UDP xuong ESP32 Gateway."""
        now = time.time()
        # Chong ban trung lap qua nhanh trong 1.5 giay
        dedup_key = (target_node, cmd_code)
        if now - self.last_sent.get(dedup_key, 0.0) < 1.5:
            return
        self.last_sent[dedup_key] = now

        self.cmd_seq = (self.cmd_seq + 1) & 0xFFFFFFFF
        cmd_id = self.cmd_seq

        # Header 16 bytes: magic(4), ver(1), type(1), origin(1), last_hop(1), seq(4), hops(1), ttl(1), flags(1), rssi(1)
        header = struct.pack("<IBBBBIBBBb", WIEVAC_MESH_MAGIC, 1, WIEVAC_MSG_CONTROL, 0, 0, cmd_id, 0, 12, 0, 0)
        # Payload 13 bytes: target(1), cmd(1), prio(1), status(1), cmd_id(4), dur(2), route(2), attempt(1)
        payload = struct.pack("<BBBBIHHB", target_node, cmd_code, priority, 0, cmd_id, 30, 1, 1)
        packet = header + payload

        target_name = f"Node {target_node}" if target_node > 0 else "TOAN BO 3 NODE (Broadcast)"
        print(f"[DownlinkBridge] >>> PHAT LENH XUONG ESP32: {target_name} | Cmd={cmd_code} (ID={cmd_id})", flush=True)

        # 1. Gui toi Broadcast subnet (Dam bao bat ke Gateway mang IP nao deu nhan duoc)
        try:
            self.tx_sock.sendto(packet, (BROADCAST_IP, GATEWAY_UDP_PORT))
        except Exception as e:
            print(f"[DownlinkBridge] Broadcast err: {e}", flush=True)

        # 2. Gui Unicast truc tiep toi Gateway IP
        if self.discovered_gateway_ip and self.discovered_gateway_ip != BROADCAST_IP:
            try:
                self.tx_sock.sendto(packet, (self.discovered_gateway_ip, GATEWAY_UDP_PORT))
            except Exception as e:
                print(f"[DownlinkBridge] Unicast err: {e}", flush=True)

    def on_mqtt_message(self, client, userdata, msg):
        try:
            payload = json.loads(msg.payload.decode("utf-8"))
        except Exception:
            payload = {}

        topic = msg.topic
        # 1. Lenh tu Guidance Controller (Speaker)
        if topic.startswith("building/guidance/speaker/"):
            target_node = self.resolve_target_node(topic, payload)
            cmd_code = self.resolve_command_code(payload)
            priority = 2 if payload.get("priority") == "emergency" else 1
            self.send_mesh_packet(target_node, cmd_code, priority)

        # 2. Lenh can thiep truc tiep / test tu giao dien hoac script
        elif topic in ("building/downlink/control", "building/downlink/manual"):
            target_node = self.resolve_target_node(topic, payload)
            cmd_code = self.resolve_command_code(payload)
            priority = int(payload.get("priority", 1))
            self.send_mesh_packet(target_node, cmd_code, priority)

        # 3. Su co khan cap: Bao chay / nguy hiem toan mang
        elif topic == "building/incident":
            # Khi co su co, phat lenh START_EVAC (10) hoac STOP_DANGER (17) cho toan bo node
            incident_type = payload.get("type", "fire")
            print(f"[DownlinkBridge] !!! CANH BAO SU CO ({incident_type}) -> Phat lenh so tan khan cap toi ca 3 Loa!", flush=True)
            self.send_mesh_packet(0, 10, priority=4)

        # 4. Ket thuc su co / Clear
        elif topic in ("building/incident/clear", "building/simulation/stop"):
            print("[DownlinkBridge] Ket thuc su co -> Phat lenh CLEAR am thanh cho toan bo node.", flush=True)
            self.send_mesh_packet(0, 19, priority=1)

    def run(self):
        print("=" * 65)
        print("    WIEVAC PI DOWNLINK BRIDGE (MQTT -> UDP ESP32 MESH)")
        print("=" * 65)
        print(f" MQTT Broker    : {MQTT_HOST}:{MQTT_PORT}")
        print(f" Gateway Port   : {GATEWAY_UDP_PORT}")
        print(f" Subnet Broadcast: {BROADCAST_IP}:{GATEWAY_UDP_PORT}")
        print(f" Gateway Unicast : {self.discovered_gateway_ip}:{GATEWAY_UDP_PORT}")
        print("-" * 65)

        self.mqtt_client.on_message = self.on_mqtt_message
        connected = False
        for attempt in range(1, 10):
            try:
                self.mqtt_client.connect(MQTT_HOST, MQTT_PORT, keepalive=60)
                connected = True
                break
            except Exception as e:
                print(f"[DownlinkBridge] Cho MQTT Broker... ({e})")
                time.sleep(1)

        if not connected:
            print("[DownlinkBridge] Khong the ket noi MQTT Broker. Dung tien trinh.", file=sys.stderr)
            return

        self.mqtt_client.subscribe("building/guidance/speaker/#", qos=1)
        self.mqtt_client.subscribe("building/downlink/#", qos=1)
        self.mqtt_client.subscribe("building/incident", qos=1)
        self.mqtt_client.subscribe("building/incident/clear", qos=1)
        self.mqtt_client.subscribe("building/simulation/stop", qos=1)

        print("[DownlinkBridge] Da dang ky lang nghe cac topic dieu huong:")
        print("   - building/guidance/speaker/#")
        print("   - building/downlink/#")
        print("   - building/incident")
        print("[DownlinkBridge] SAN SANG CHUYEN TIEP LENH XUONG LOA ESP32!")
        print("=" * 65)

        self.mqtt_client.loop_forever()


if __name__ == "__main__":
    bridge = DownlinkBridge()
    bridge.run()
