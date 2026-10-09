#!/usr/bin/env python3
"""WiEvac Standalone Incident & Guidance Trigger Tool.

Use this tool on Raspberry Pi to test LED signs, speakers, and D* Lite rerouting.

Usage examples:
  python3 trigger_incident.py --status
  python3 trigger_incident.py --test-sign LEFT
  python3 trigger_incident.py --test-sign RIGHT
  python3 trigger_incident.py --test-sign STRAIGHT
  python3 trigger_incident.py --test-sign BACK
  python3 trigger_incident.py --test-sign STOP
  python3 trigger_incident.py --block e_1788183434485
  python3 trigger_incident.py --clear e_1788183434485
  python3 trigger_incident.py --clear-all
"""

import argparse
import json
import os
import sys
import time
import paho.mqtt.client as mqtt

MQTT_HOST = os.getenv("WIEVAC_MQTT_HOST", "127.0.0.1")
MQTT_PORT = int(os.getenv("WIEVAC_MQTT_PORT", "1883"))
SIGN_TOPIC = "building/guidance/sign/sign_p10_01"

COMMAND_PRESETS = {
    "LEFT": {
        "command": "LEFT", "direction": "LEFT",
        "presentation": {"visual_intent": "ARROW_LEFT", "alert_level": "normal", "audio_clip_id": "evacuate_left", "load_level": "clear"}
    },
    "RIGHT": {
        "command": "RIGHT", "direction": "RIGHT",
        "presentation": {"visual_intent": "ARROW_RIGHT", "alert_level": "normal", "audio_clip_id": "evacuate_right", "load_level": "clear"}
    },
    "STRAIGHT": {
        "command": "STRAIGHT", "direction": "STRAIGHT",
        "presentation": {"visual_intent": "ARROW_STRAIGHT", "alert_level": "normal", "audio_clip_id": "evacuate_straight", "load_level": "clear"}
    },
    "BACK": {
        "command": "BACK", "direction": "BACK",
        "presentation": {"visual_intent": "ARROW_BACK", "alert_level": "warning", "audio_clip_id": "turn_back", "load_level": "blocked"}
    },
    "STOP": {
        "command": "DO_NOT_ENTER", "direction": "NO_SAFE_ROUTE",
        "presentation": {"visual_intent": "DO_NOT_ENTER", "alert_level": "critical", "audio_clip_id": "shelter_in_place", "load_level": "blocked"}
    },
    "EXIT": {
        "command": "EXIT", "direction": "EXIT",
        "presentation": {"visual_intent": "EXIT", "alert_level": "normal", "audio_clip_id": "exit_here", "load_level": "clear"}
    },
    "STANDBY": {
        "command": "STANDBY", "direction": "STANDBY",
        "presentation": {"visual_intent": "STANDBY", "alert_level": "none", "audio_clip_id": "", "load_level": "clear"}
    }
}

def main():
    parser = argparse.ArgumentParser(description="WiEvac Standalone Guidance Trigger")
    parser.add_argument("--test-sign", choices=list(COMMAND_PRESETS.keys()), help="Directly test LED P10 & Speaker with a preset intent")
    parser.add_argument("--block", type=str, help="Simulate a fire/hazard blocking an edge (e.g. e_1788183434485)")
    parser.add_argument("--clear", type=str, help="Clear incident on a specific edge")
    parser.add_argument("--clear-all", action="store_true", help="Clear all incidents and restore normal routing")
    parser.add_argument("--status", action="store_true", help="Check current sign command and system state")
    args = parser.parse_args()

    client = mqtt.Client(mqtt.CallbackAPIVersion.VERSION2, client_id="WiEvac_Trigger_CLI")
    try:
        client.connect(MQTT_HOST, MQTT_PORT, 10)
    except Exception as exc:
        print(f"[Lỗi] Không thể kết nối tới MQTT Broker tại {MQTT_HOST}:{MQTT_PORT}: {exc}")
        return 1

    client.loop_start()
    time.sleep(0.2)

    if args.test_sign:
        cmd_info = COMMAND_PRESETS[args.test_sign]
        now = int(time.time())
        payload = {
            "sequence": int(now % 100000),
            "device_id": "d_1789053810837_280",
            "command": cmd_info["command"],
            "direction": cmd_info["direction"],
            "target_edge": None,
            "generated_at": now,
            "valid_until": now + 30,  # 30 seconds test display
            "priority": "emergency",
            "presentation": cmd_info["presentation"]
        }
        client.publish(SIGN_TOPIC, json.dumps(payload, ensure_ascii=False), qos=1, retain=True)
        print(f"[OK] Đã gửi lệnh kiểm thử [{args.test_sign}] tới biển báo {SIGN_TOPIC}")
        print(f"     -> Màn hình LED P10: {cmd_info['presentation']['visual_intent']}")
        print(f"     -> Loa phát file track: {cmd_info['presentation']['audio_clip_id']}")

    elif args.block:
        edge_id = args.block.strip()
        incident = {
            "type": "edge",
            "target_id": edge_id,
            "severity": 100,
            "timestamp": int(time.time())
        }
        client.publish("building/incident", json.dumps(incident, ensure_ascii=False), qos=1)
        client.publish("building/hazard/adjust", json.dumps({"edge_id": edge_id, "hazard": 100.0}), qos=1)
        print(f"[OK] Đã gửi tín hiệu phong tỏa hành lang [{edge_id}]!")
        print("     Hệ thống D* Lite sẽ tự động tính đường né tránh và đổi chiều biển báo!")

    elif args.clear:
        edge_id = args.clear.strip()
        incident = {
            "type": "edge",
            "target_id": edge_id
        }
        client.publish("building/incident/clear", json.dumps(incident, ensure_ascii=False), qos=1)
        client.publish("building/hazard/adjust", json.dumps({"edge_id": edge_id, "hazard": 0.0}), qos=1)
        print(f"[OK] Đã xóa sự cố tại hành lang [{edge_id}].")

    elif args.clear_all:
        for eid in ["e_1788183434485", "corr_1788183099728_351y9bdgj", "corr_1788183099728_dmbe9b6ar"]:
            client.publish("building/incident/clear", json.dumps({"type": "edge", "target_id": eid}), qos=1)
            client.publish("building/hazard/adjust", json.dumps({"edge_id": eid, "hazard": 0.0}), qos=1)
        print("[OK] Đã xóa toàn bộ sự cố. Hệ thống trở về trạng thái bình thường!")

    elif args.status or len(sys.argv) == 1:
        latest = {}
        def on_msg(c, u, msg):
            latest[msg.topic] = msg.payload.decode('utf-8', errors='replace')
        client.on_message = on_msg
        client.subscribe("building/guidance/sign/#")
        client.subscribe("building/guidance/state")
        client.subscribe("building/occupancy")
        time.sleep(0.8)
        print("=== TRẠNG THÁI HỆ THỐNG WIEVAC TRÊN PI ===")
        sign_data = latest.get(SIGN_TOPIC)
        if sign_data:
            try:
                parsed = json.loads(sign_data)
                print(f"Biển báo ({SIGN_TOPIC}):")
                print(f"  - Lệnh hiện tại  : {parsed.get('command')}")
                print(f"  - Mũi tên LED    : {parsed.get('presentation', {}).get('visual_intent')}")
                print(f"  - Đoạn âm thanh  : {parsed.get('presentation', {}).get('audio_clip_id')}")
                print(f"  - Hành lang đích : {parsed.get('target_edge')}")
                print(f"  - Thời hạn lệnh  : còn {max(0, parsed.get('valid_until', 0) - int(time.time()))}s (tự làm mới liên tục)")
            except Exception:
                print(f"Biển báo: {sign_data}")
        else:
            print("Chưa nhận được bản tin biển báo.")

    client.loop_stop()
    client.disconnect()
    return 0

if __name__ == "__main__":
    sys.exit(main())
