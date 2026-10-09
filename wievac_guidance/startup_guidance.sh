#!/bin/bash
DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$DIR"

# Kill existing instances if running
pkill -f "python3.*wievac_guidance/(edge_core|pi_csi_bridge|pi_downlink_bridge)\.py" 2>/dev/null || true
sleep 1

echo "Checking mosquitto..."
if ! systemctl is-active --quiet mosquitto 2>/dev/null; then
    sudo systemctl start mosquitto 2>/dev/null || true
fi

echo "Starting edge_core.py in Standalone Mode..."
nohup /usr/bin/python3 "$DIR/edge_core.py" > "$DIR/edge_core.log" 2>&1 &

echo "Starting pi_csi_bridge.py..."
nohup /usr/bin/python3 "$DIR/pi_csi_bridge.py" > "$DIR/pi_csi_bridge.log" 2>&1 &

echo "Starting pi_downlink_bridge.py (Auto MQTT -> ESP32 Downlink)..."
nohup /usr/bin/python3 "$DIR/pi_downlink_bridge.py" > "$DIR/pi_downlink_bridge.log" 2>&1 &

sleep 2
ps aux | grep -E 'edge_core|pi_csi_bridge|pi_downlink_bridge' | grep -v grep
echo "All WiEvac guidance services started on Pi!"
