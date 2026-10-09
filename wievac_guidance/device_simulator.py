"""MQTT simulator for phase-1 evacuation signs and speakers.

Run this beside edge_core.py. It prints every command and returns an ACK using
the same protocol that the real hardware drivers will use in phase 2.
"""

import json
import os
import threading
import time

import paho.mqtt.client as mqtt


MQTT_BROKER = os.getenv("WIEVAC_MQTT_HOST", os.getenv("MQTT_BROKER", "127.0.0.1"))
MQTT_PORT = int(os.getenv("WIEVAC_MQTT_PORT", os.getenv("MQTT_PORT", "1883")))
ACK_DELAY_SECONDS = float(os.getenv("SIMULATOR_ACK_DELAY", "0.15"))
DROP_ACK_FOR = {
    value.strip()
    for value in os.getenv("SIMULATOR_DROP_ACK_FOR", "").split(",")
    if value.strip()
}


def publish_ack(client, command):
    device_id = command.get("device_id")
    if not device_id or device_id in DROP_ACK_FOR:
        if device_id in DROP_ACK_FOR:
            print(f"[SIM DEVICE] Deliberately dropping ACK for {device_id}")
        return
    ack = {
        "device_id": device_id,
        "sequence": command.get("sequence"),
        "status": "ok",
        "detail": "simulated_device_applied_command",
        "applied_at": int(time.time()),
    }
    client.publish(
        f"building/guidance/ack/{device_id}",
        json.dumps(ack, ensure_ascii=False),
        qos=1,
        retain=False,
    )


def on_connect(client, userdata, flags, reason_code, properties=None):
    if reason_code == 0:
        print("[SIM DEVICE] Connected to MQTT broker.")
        client.subscribe("building/guidance/sign/+", qos=1)
        client.subscribe("building/guidance/speaker/+", qos=1)
    else:
        print(f"[SIM DEVICE] MQTT connection failed: {reason_code}")


def on_message(client, userdata, msg):
    try:
        command = json.loads(msg.payload.decode("utf-8"))
        device_type = command.get("device_type", "device").upper()
        print(
            f"[{device_type}] {command.get('device_id')}: "
            f"{command.get('command')} -> {command.get('target_edge')}"
        )
        timer = threading.Timer(
            ACK_DELAY_SECONDS, publish_ack, args=(client, command)
        )
        timer.daemon = True
        timer.start()
    except Exception as exc:
        print("[SIM DEVICE] Invalid command:", exc)


if __name__ == "__main__":
    mqtt_client = mqtt.Client(
        mqtt.CallbackAPIVersion.VERSION2,
        client_id="Evacuation_Device_Simulator",
    )
    mqtt_client.on_connect = on_connect
    mqtt_client.on_message = on_message
    mqtt_client.reconnect_delay_set(min_delay=1, max_delay=10)
    connected = False
    deadline = time.time() + 20.0
    while not connected and time.time() < deadline:
        try:
            mqtt_client.connect(MQTT_BROKER, MQTT_PORT, 60)
            connected = True
        except Exception:
            time.sleep(0.5)
    if not connected:
        mqtt_client.connect(MQTT_BROKER, MQTT_PORT, 60)
    mqtt_client.loop_forever()
