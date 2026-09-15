"""Replay EdgeResult V5 datagrams without sockets or hardware."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Iterable

# Permit `python wievac/scripts/replay_edge_result_v5.py ...` from the repo root.
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from pi.app.edge_result_v5 import HEADER_SIZE, decode_edge_result
from pi.app.edge_result_v5_service import EdgeResultV5Service, V5ServiceConfig


def _packets(path: Path) -> Iterable[bytes]:
    raw = path.read_bytes()
    if path.suffix.lower() == ".json":
        row = json.loads(raw.decode("utf-8"))
        yield bytes.fromhex(row["hex"])
        return
    if path.suffix.lower() == ".jsonl":
        for line in raw.decode("utf-8").splitlines():
            if not line.strip():
                continue
            row = json.loads(line)
            value = row.get("datagram_hex") or row.get("edge_result_v5_hex") or row.get("bytes_hex")
            if value:
                yield bytes.fromhex(value)
        return
    offset = 0
    while offset + HEADER_SIZE <= len(raw):
        payload_length = int.from_bytes(raw[offset + 6:offset + 8], "big")
        total = HEADER_SIZE + payload_length
        if total <= HEADER_SIZE or offset + total > len(raw):
            raise ValueError("truncated_or_invalid_v5_packet")
        yield raw[offset:offset + total]
        offset += total
    if offset != len(raw):
        raise ValueError("trailing_bytes")


def replay(path: Path, *, max_packets: int | None = None) -> dict:
    # Replay is explicitly test-only; production service startup requires a
    # configured identity allow-list and must remain fail-closed.
    service = EdgeResultV5Service(config=V5ServiceConfig(allow_open_test_only=True))
    accepted = rejected = 0
    reasons: dict[str, int] = {}
    for index, packet in enumerate(_packets(path)):
        if max_packets is not None and index >= max_packets:
            break
        decoded = decode_edge_result(packet)
        decision = service.ingest_datagram(packet, now_us=decoded.rx_timestamp_us)
        if decision.accepted:
            accepted += 1
        else:
            rejected += 1
            reasons[decision.reason] = reasons.get(decision.reason, 0) + 1
    latest_timestamp = max((state.latest.rx_timestamp_us for state in service.ingest.links.values() if state.latest is not None), default=None)
    return {"packets": accepted + rejected, "accepted": accepted, "rejected": rejected, "rejection_reasons": reasons, "overview": service.response("/api/v5/overview", now_us=latest_timestamp)}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("input", type=Path)
    parser.add_argument("--max-packets", type=int)
    args = parser.parse_args()
    print(json.dumps(replay(args.input, max_packets=args.max_packets), ensure_ascii=False, separators=(",", ":")))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
