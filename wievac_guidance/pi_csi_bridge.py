#!/usr/bin/env python3
import json
import time
import urllib.request
import sys
import os

# Ensure paho inside this folder can be imported
sys.path.insert(0, os.path.dirname(__file__))
import paho.mqtt.client as mqtt

PI_URL = "http://127.0.0.1:8080/api/v5/overview"
MQTT_HOST = "127.0.0.1"
MQTT_PORT = 1883
TOPIC = "building/occupancy/input"
CONFIG_FILE = os.path.join(os.path.dirname(__file__), "config.json")

def load_edge_map():
    """Load edge mapping from config.json, supporting comma-separated csiLinkId."""
    try:
        if os.path.exists(CONFIG_FILE):
            with open(CONFIG_FILE, "r", encoding="utf-8") as f:
                cfg = json.load(f)
            dynamic_map = {}
            for e in cfg.get("edges", []):
                link_field = e.get("csiLinkId")
                edge_id = e.get("id")
                if link_field and edge_id:
                    links = [l.strip() for l in str(link_field).split(",") if l.strip()]
                    if links:
                        dynamic_map[edge_id] = links
            if dynamic_map:
                return dynamic_map
            # Fallback: up to 3 edges for 3 nodes
            edges = [e["id"] for e in cfg.get("edges", []) if "id" in e]
            if len(edges) >= 3:
                return {edges[0]: ["link-1"], edges[1]: ["link-2"], edges[2]: ["link-3"]}
            elif len(edges) >= 2:
                return {edges[0]: ["link-1"], edges[1]: ["link-2"]}
    except Exception as err:
        print(f"[CSI-Bridge] Error loading config: {err}", flush=True)

    return {"e_1788183031317": ["link-1"], "e_1788183040256": ["link-2"], "e_1788183041772": ["link-3"]}

def main():
    edge_map = load_edge_map()
    print(f"[CSI-Bridge] Using Link Map: {edge_map}", flush=True)

    client = mqtt.Client(mqtt.CallbackAPIVersion.VERSION2)
    connected = False
    for _ in range(10):
        try:
            client.connect(MQTT_HOST, MQTT_PORT, 60)
            client.loop_start()
            connected = True
            break
        except Exception:
            time.sleep(1)
    if not connected:
        print("[CSI-Bridge] Failed to connect to MQTT broker", flush=True)
        return

    print("[CSI-Bridge] Connected to MQTT broker. Polling CSI API...", flush=True)
    last_reload = 0.0

    while True:
        now = time.time()
        if now - last_reload >= 5.0:
            edge_map = load_edge_map()
            last_reload = now

        try:
            req = urllib.request.Request(PI_URL, headers={"User-Agent": "WiEvac-Bridge/1.0"})
            with urllib.request.urlopen(req, timeout=1.5) as resp:
                if resp.status == 200:
                    data = json.loads(resp.read().decode('utf-8'))
                    links = data.get("links", {})
                    values = {}
                    
                    # Bottleneck principle for multi-link corridors
                    for edge_id, link_ids in edge_map.items():
                        k_list = []
                        is_blocked = False

                        for link_id in link_ids:
                            info = links.get(link_id, {})
                            score = info.get("score")
                            state = info.get("state")

                            if state == "BLOCKED":
                                is_blocked = True
                                break
                            elif score is not None and state not in ("UNKNOWN", "NOT_READY"):
                                # score 0..100 -> k 0..1
                                k = round(max(0.0, min(1.0, (100.0 - float(score)) / 100.0)), 3)
                                k_list.append(k)

                        if is_blocked:
                            values[edge_id] = 1.0
                        elif k_list:
                            values[edge_id] = max(k_list)

                    if values:
                        payload = json.dumps({"values": values})
                        client.publish(TOPIC, payload, qos=1)
        except Exception:
            pass
        time.sleep(1.0)

if __name__ == "__main__":
    main()
