#!/usr/bin/env python3
"""Send downlink guidance packet to Node via Gateway.

Usage:
  python3 send_guidance.py --target 1 --cmd 11
  python3 send_guidance.py --target 2 --cmd 12
  python3 send_guidance.py --target 0 --cmd 10
"""

import argparse
import socket
import struct
import sys
import time

GUIDANCE_NAMES = {
    10: "Bắt đầu sơ tán (START_EVAC)",
    11: "Đi thẳng (GO_STRAIGHT)",
    12: "Rẽ trái (TURN_LEFT)",
    13: "Rẽ phải (TURN_RIGHT)",
    14: "Đi lên cầu thang (GO_UPSTAIRS)",
    15: "Đi xuống cầu thang (GO_DOWNSTAIRS)",
    16: "Lối thoát an toàn (SAFE_EXIT)",
    17: "Dừng lại - Nguy hiểm (STOP_DANGER)",
    18: "Chờ hướng dẫn (WAIT)",
    19: "Xóa điều hướng (CLEAR)",
}

def main():
    parser = argparse.ArgumentParser(description="Send WiEvac Downlink Guidance Packet")
    parser.add_argument("--gateway-ip", default="10.42.0.218", help="Gateway Node IP address")
    parser.add_argument("--gateway-port", type=int, default=8889, help="Gateway command port (default: 8889)")
    parser.add_argument("--target", type=int, default=1, help="Target Node ID (0=Broadcast, 1=Node 1, 2=Node 2...)")
    parser.add_argument("--cmd", type=int, default=11, help="Command code (10..19)")
    parser.add_argument("--priority", type=int, default=1, help="Priority (1=Normal, 2=Evac, 3=Danger, 4=Emergency)")
    parser.add_argument("--cmd-id", type=int, default=None, help="Command ID (auto-generated if None)")
    args = parser.parse_args()

    cmd_id = args.cmd_id if args.cmd_id is not None else int(time.time()) % 100000

    # Header 16 bytes: magic(4), ver(1), type(1), origin(1), last_hop(1), seq(4), hops(1), ttl(1), flags(1), rssi(1)
    header = struct.pack("<IBBBBIBBBb", 0x57455643, 1, 5, 0, 0, cmd_id, 0, 12, 0, 0)
    # Payload 13 bytes: target(1), cmd(1), prio(1), status(1), cmd_id(4), dur(2), route(2), attempt(1)
    payload = struct.pack("<BBBBIHHB", args.target, args.cmd, args.priority, 0, cmd_id, 30, 1, 1)
    packet = header + payload

    cmd_name = GUIDANCE_NAMES.get(args.cmd, f"Mã {args.cmd}")
    target_str = f"Node {args.target}" if args.target > 0 else "TOÀN MẠNG (Broadcast)"

    print("=" * 60)
    print("        WIEVAC DOWNLINK TRANSMISSION")
    print("=" * 60)
    print(f" Đích đến     : {target_str}")
    print(f" Lệnh         : {cmd_name}")
    print(f" Ưu tiên      : {args.priority}")
    print(f" Command ID   : {cmd_id}")
    print(f" Gateway Đích : {args.gateway_ip}:{args.gateway_port}")
    print("-" * 60)

    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        sock.sendto(packet, (args.gateway_ip, args.gateway_port))
        print(f" [OK] Đã phát gói tin 29 bytes tới Gateway {args.gateway_ip}:{args.gateway_port}")
        print(" Node nhận sẽ in log xác nhận và phản hồi ACK về Pi.")
    finally:
        sock.close()
    print("=" * 60)

if __name__ == "__main__":
    main()
