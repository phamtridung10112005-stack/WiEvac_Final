"""Record a corridor session as raw amplitudes plus the 42-vector.

Live source is opt-in WRAW UDP (--udp-port). --mock and --csi-jsonl stay.
The collector records quality fields and does not drop vectors for them.
"""

from __future__ import annotations

import argparse
import gzip
import json
import math
import random
import select
import signal
import socket
import struct
import sys
import time
import zlib
from datetime import datetime, timedelta, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "pi" / "app"))

from tiny_ai_features import FEATURES, Extractor, amplitudes_from_csi, f32, mul  # noqa: E402

LABELS = {"EMPTY", "OCCUPIED_VAL", "MIXED"}
MOCK_HZ = 8
MOCK_STEP_US = 125_000
SHORT_US = 2_000_000
LONG_US = 30_000_000
BUCKET_US = 50_000
BUCKETS = 40
MAX_SLICE_S = 30 * 60
SCHEDULE_PATH = Path("/home/junpham/wievac/data/capture-schedules.json")
LABELS_DIR = Path("/home/junpham/wievac/data/labels")
_STOP = False


def corridor_dirname(corridor_id: str) -> str:
    text = corridor_id.strip()
    lowered = text.lower()
    for prefix in ("corridor-", "corridor_"):
        if lowered.startswith(prefix):
            text = text[len(prefix):]
            break
    safe = "".join(char if char.isalnum() else "_" for char in text)
    return f"corridor_{safe or 'unknown'}"


def sequence_loss_ratio(sequences: list[int]) -> float:
    lost = 0
    span = 0
    for left, right in zip(sequences, sequences[1:]):
        if right <= left:
            continue
        lost += right - left - 1
        span += right - left
    return lost / span if span else 0.0


# Mean of the 8 lowband (0.1-2 Hz Goertzel) features. Pure noise sits near
# 58 bins * 2/5 (long/short bucket variance) = 23; walking reaches 10^4.
# Empty real corridors (2026-10-17, A/B) reached 181 from ambient drift, so
# 300 keeps margin above that and stays far below motion.
DISTURBANCE_LOWBAND_LIMIT = 300.0


def disturbance_warning(features: list[float], label: str,
                        limit: float | None = None) -> bool:
    if label != "EMPTY" or len(features) != FEATURES:
        return False
    lowband = features[32:40]
    threshold = DISTURBANCE_LOWBAND_LIMIT if limit is None else limit
    return sum(lowband) / len(lowband) > threshold


def mock_frames(duration_min: float, seed: int = 1) -> list[tuple[int, int, list[float]]]:
    rng = random.Random(seed)
    count = max(1, int(round(duration_min * 60 * MOCK_HZ)))
    timestamp = 1_000_000
    frames = []
    for index in range(count):
        amplitudes = [1.0 + 0.02 * rng.gauss(0.0, 1.0) for _ in range(52)]
        frames.append((timestamp, index + 1, amplitudes))
        timestamp += MOCK_STEP_US
    return frames


def _load_jsonl(path: Path) -> list[tuple[int, int, list[float]]]:
    frames = []
    with path.open("r", encoding="utf-8") as handle:
        for line in handle:
            if not line.strip():
                continue
            record = json.loads(line)
            amplitudes = record.get("amp")
            if amplitudes is None:
                continue
            frames.append((int(record["ts_us"]), int(record["seq"]), [float(value) for value in amplitudes]))
    return frames


def link_number(link_id: str) -> int:
    text = str(link_id)
    if not text.startswith("link-"):
        raise ValueError(f"link id must look like link-N, got {link_id}")
    return int(text.split("-", 1)[1])


def short_coverage(samples: list[tuple], t_us: int) -> float:
    """Fraction of the last 2 s (40 x 50 ms) that contains at least one frame."""
    mask = 0
    start = t_us - SHORT_US
    for item in samples:
        ts = item[0]
        if ts <= start or ts > t_us:
            continue
        bucket = (t_us - ts) // BUCKET_US
        if 0 <= bucket < BUCKETS:
            mask |= 1 << bucket
    return bin(mask).count("1") / BUCKETS


def loss_30s(samples: list[tuple], t_us: int) -> float:
    start = t_us - LONG_US
    sequences = [item[1] for item in samples if start <= item[0] <= t_us]
    return sequence_loss_ratio(sequences)


def frame_quality(samples: list[tuple], index: int, first_ts: int, features: list[float]) -> dict:
    ts = samples[index][0]
    window = samples[: index + 1]
    finite = len(features) == FEATURES and all(math.isfinite(value) for value in features)
    return {
        "t_since_start_s": (ts - first_ts) / 1_000_000,
        "short_coverage": short_coverage(window, ts),
        "loss_30s": loss_30s(window, ts),
        "finite": finite,
    }


def dedupe_dashboard_events(events: list[dict]) -> list[dict]:
    """The dashboard writes each interval twice (open row, then the same start
    closed). Per link its intervals are sequential: a new label closes the
    previous one. Collapse to one event per start, ended by the next start."""
    dashboard = [event for event in events if event.get("source") == "dashboard"]
    if not dashboard:
        return list(events)
    by_start: dict[int, dict] = {}
    for event in dashboard:
        start = int(event["pi_mono_us"])
        row = by_start.setdefault(start, {"state": event["state"], "end": None})
        end = event.get("t_end_us")
        if end is not None and (row["end"] is None or int(end) > row["end"]):
            row["end"] = int(end)
    starts = sorted(by_start)
    merged = []
    for index, start in enumerate(starts):
        end = starts[index + 1] if index + 1 < len(starts) else by_start[start]["end"]
        merged.append({
            "type": "label_event", "pi_mono_us": start, "t_end_us": end,
            "state": by_start[start]["state"], "source": "dashboard",
        })
    return [event for event in events if event.get("source") != "dashboard"] + merged


def _state_at(events: list[dict], t_us: int) -> str:
    keyboard = None
    dashboard = None
    dashboard_conflict = False
    for event in events:
        if event.get("source") == "dashboard":
            start = int(event["pi_mono_us"])
            end = event.get("t_end_us")
            if t_us < start or (end is not None and t_us >= int(end)):
                continue
            state = event["state"]
            if dashboard is None:
                dashboard = state
            elif dashboard != state:
                dashboard_conflict = True
            continue
        if int(event["pi_mono_us"]) <= t_us:
            keyboard = event["state"]
    if dashboard_conflict or keyboard == "UNKNOWN" or dashboard == "UNKNOWN":
        return "UNKNOWN"
    if keyboard is not None and dashboard is not None and keyboard != dashboard:
        return "UNKNOWN"
    if keyboard is not None:
        return keyboard
    if dashboard is not None:
        return dashboard
    return "UNKNOWN"


def _states_between(events: list[dict], start: int, end: int) -> set[str]:
    points = {start, end}
    for event in events:
        stamp = int(event["pi_mono_us"])
        if start < stamp <= end:
            points.add(stamp)
        if event.get("source") == "dashboard" and event.get("t_end_us") is not None:
            stop = int(event["t_end_us"])
            if start < stop <= end:
                points.add(stop)
                points.add(stop - 1)
    return {_state_at(events, point) for point in points if start <= point <= end}


def feature_label(t_us: int, events: list[dict] | None, session_label: str) -> str:
    events = dedupe_dashboard_events(events or [])
    if not events:
        if session_label == "OCCUPIED_VAL":
            return "OCCUPIED"
        if session_label == "EMPTY":
            return "EMPTY"
    if "PERSON" in _states_between(events, t_us - SHORT_US, t_us):
        return "OCCUPIED"
    if _states_between(events, t_us - LONG_US, t_us) == {"EMPTY"}:
        return "EMPTY"
    return "EXCLUDED"


def encode_wraw(rx_id: int, link_id: int, boot_id: int, datagram_seq: int, frames: list[dict],
                version: int = 2, gain_response_s: float | None = None) -> bytes:
    body = bytearray(b"WRAW")
    body += struct.pack("<BBHIII", version, len(frames), rx_id, link_id, boot_id, datagram_seq)
    if version >= 3:
        body += struct.pack("<f", 0.0 if gain_response_s is None else float(gain_response_s))
    for frame in frames:
        csi = bytes(frame["csi"])
        applied = float(frame.get("applied_scale", frame.get("gain_scale", 1.0)))
        body += struct.pack("<IQbBH", int(frame["tx_sequence"]), int(frame["timestamp_us"]),
                            int(frame["rssi"]), 0, len(csi))
        if version >= 2:
            body += struct.pack("<f", applied)
        if version >= 3:
            library = frame.get("library_gain")
            body += struct.pack("<f", applied if library is None else float(library))
        body += csi
    body += struct.pack("<I", zlib.crc32(body) & 0xFFFFFFFF)
    return bytes(body)


def parse_wraw(packet: bytes) -> dict | None:
    if len(packet) < 24 or packet[:4] != b"WRAW":
        return None
    version, frame_count, rx_id = struct.unpack_from("<BBH", packet, 4)
    link_id, boot_id, datagram_seq = struct.unpack_from("<III", packet, 8)
    if version not in (1, 2, 3) or not 1 <= frame_count <= 4:
        return None
    offset = 20
    gain_response_s = None
    if version >= 3:
        if offset + 4 > len(packet) - 4:
            return None
        gain_response_s = struct.unpack_from("<f", packet, offset)[0]
        offset += 4
        if not math.isfinite(gain_response_s):
            return None
    frames = []
    for _ in range(frame_count):
        if offset + 16 > len(packet) - 4:
            return None
        tx_sequence, timestamp_us, rssi, _reserved, csi_length = struct.unpack_from("<IQbBH", packet, offset)
        offset += 16
        applied_scale = 1.0
        library_gain = None
        if version >= 2:
            # f32 scale the RX applied to float magnitudes (v1 had none).
            if offset + 4 > len(packet) - 4:
                return None
            applied_scale = struct.unpack_from("<f", packet, offset)[0]
            offset += 4
            if not (math.isfinite(applied_scale) and applied_scale > 0.0):
                return None
        if version >= 3:
            if offset + 4 > len(packet) - 4:
                return None
            library_gain = struct.unpack_from("<f", packet, offset)[0]
            offset += 4
            if not (math.isfinite(library_gain) and library_gain > 0.0):
                return None
        if csi_length < 0 or offset + csi_length > len(packet) - 4:
            return None
        csi = packet[offset:offset + csi_length]
        offset += csi_length
        frames.append({
            "tx_sequence": tx_sequence, "timestamp_us": timestamp_us,
            "rssi": rssi, "csi": csi, "gain_scale": applied_scale,
            "applied_scale": applied_scale, "library_gain": library_gain,
        })
    if offset + 4 != len(packet):
        return None
    expect = struct.unpack_from("<I", packet, offset)[0]
    if (zlib.crc32(packet[:offset]) & 0xFFFFFFFF) != expect:
        return None
    return {
        "rx_id": rx_id, "link_id": link_id, "boot_id": boot_id,
        "datagram_seq": datagram_seq, "frame_count": frame_count, "frames": frames,
        "gain_response_s": gain_response_s,
    }


def scaled_amplitudes(csi: bytes, gain_scale: float = 1.0) -> list[float] | None:
    """Same float32 product as tiny_ai_gate_ingest: amplitude * gain_scale."""
    amp = amplitudes_from_csi(csi)
    if amp is None or gain_scale == 1.0:
        return amp
    scale = f32(gain_scale)
    return [mul(value, scale) for value in amp]


def take_datagram(packet: bytes, link_n: int, stats: dict) -> dict | None:
    parsed = parse_wraw(packet)
    if parsed is None:
        stats["bad_datagrams"] = stats.get("bad_datagrams", 0) + 1
        return None
    if parsed["link_id"] != link_n:
        stats["link_mismatch"] = stats.get("link_mismatch", 0) + parsed["frame_count"]
        return None
    return parsed


def dashboard_events(path: Path, link_id: str, start_us: int, end_us: int) -> list[dict]:
    label_path = path if path.suffix else path / f"{link_id}.jsonl"
    if not label_path.is_file():
        print(f"dashboard labels missing: {label_path}", file=sys.stderr)
        return []
    events = []
    mapping = {"HUMAN_PRESENT": "PERSON", "EMPTY": "EMPTY", "UNKNOWN": "UNKNOWN"}
    with label_path.open("r", encoding="utf-8") as handle:
        for line in handle:
            if not line.strip():
                continue
            row = json.loads(line)
            if row.get("record_type") != "occupancy_interval":
                continue
            start = int(row["t_start_us"])
            end = None if row.get("t_end_us") is None else int(row["t_end_us"])
            if end is not None and end <= start_us:
                continue
            if start >= end_us:
                continue
            events.append({
                "type": "label_event", "pi_mono_us": start, "t_end_us": end,
                "state": mapping.get(str(row.get("occupancy")), "UNKNOWN"),
                "source": "dashboard",
            })
    return dedupe_dashboard_events(events)


def relabel_file(src: Path, dst: Path) -> dict:
    """Recompute feature labels of a saved session from its stored label
    events and per-frame pi_mono_us. Writes a new file; src is untouched."""
    lines = []
    events: list[dict] = []
    pi_mono: dict[int, int] = {}
    session_label = "MIXED"
    with gzip.open(src, "rt", encoding="utf-8") as handle:
        for line in handle:
            if not line.strip():
                continue
            record = json.loads(line)
            lines.append(record)
            kind = record.get("type")
            if kind == "session":
                session_label = str(record.get("session_label", "MIXED"))
            elif kind == "label_event":
                events.append(record)
            elif kind == "csi" and record.get("pi_mono_us") is not None:
                pi_mono[int(record["seq"])] = int(record["pi_mono_us"])
    events = dedupe_dashboard_events(events)
    counts = {"EMPTY": 0, "OCCUPIED": 0, "EXCLUDED": 0}
    dst.parent.mkdir(parents=True, exist_ok=True)
    with gzip.open(dst, "wt", encoding="utf-8") as handle:
        for record in lines:
            kind = record.get("type")
            if kind == "label_event":
                continue
            if kind == "feature":
                stamp = pi_mono.get(int(record["seq"]))
                label = "EXCLUDED" if stamp is None else feature_label(stamp, events, session_label)
                record["label"] = label
                counts[label] += 1
            if kind == "quality":
                for event in events:
                    handle.write(json.dumps(event) + "\n")
                record["empty_vectors"] = counts["EMPTY"]
                record["occupied_vectors"] = counts["OCCUPIED"]
                record["excluded_vectors"] = counts["EXCLUDED"]
                record["relabeled_from"] = src.name
            handle.write(json.dumps(record) + "\n")
    return counts


def _split_frame(frame: tuple) -> tuple:
    pi_mono = None
    gain_raw = None
    if len(frame) >= 5 and isinstance(frame[4], dict):
        pi_mono = frame[3]
        gain_raw = frame[4]
    elif len(frame) >= 4 and isinstance(frame[3], dict):
        gain_raw = frame[3]
    elif len(frame) >= 4:
        pi_mono = frame[3]
    return int(frame[0]), int(frame[1]), list(frame[2]), pi_mono, gain_raw


def _gain_meta(raw: dict | None, response_s=None) -> dict:
    raw = raw or {}
    scale = raw.get("applied_scale", raw.get("gain_scale", 1.0))
    try:
        scale = float(scale)
    except (TypeError, ValueError):
        scale = 1.0
    if not math.isfinite(scale) or scale <= 0.0:
        scale = 1.0
    library = raw.get("library_gain")
    session_s = raw.get("gain_response_s", response_s)
    return {"applied_scale": scale, "library_gain": library, "gain_response_s": session_s}


def _write_outputs(path: Path, raw_out: Path | None, meta: dict, kept: list[tuple],
                   features: list[dict], events: list[dict], stats: dict) -> dict:
    counts = {"EMPTY": 0, "OCCUPIED": 0, "EXCLUDED": 0}
    warnings = 0
    session_label = meta["session_label"]
    for feature in features:
        label = feature_label(feature["label_us"], events, session_label)
        feature["label"] = label
        counts[label] += 1
        warned = disturbance_warning(feature["features"], label)
        feature["disturbance_warning"] = warned
        warnings += int(warned)
    folder = path.parent
    folder.mkdir(parents=True, exist_ok=True)
    sequences = [item[1] for item in kept]
    span_s = (kept[-1][0] - kept[0][0]) / 1_000_000 if len(kept) > 1 else 0.0
    elapsed = stats.get("elapsed_s")
    if not elapsed:
        elapsed = span_s
    loss = sequence_loss_ratio(sequences)
    gain_response_s = None
    for item in kept:
        if len(item) >= 5 and item[4].get("gain_response_s") is not None:
            gain_response_s = item[4]["gain_response_s"]
            break
    quality = {
        "type": "quality", "dropped": stats.get("dropped", 0), "kept": len(kept),
        "loss_ratio": loss, "feature_count": len(features),
        "empty_disturbance_warnings": warnings,
        "frames_per_s": (len(kept) / elapsed) if elapsed else 0.0,
        "bad_datagrams": stats.get("bad_datagrams", 0),
        "link_mismatch": stats.get("link_mismatch", 0),
        "empty_vectors": counts["EMPTY"],
        "occupied_vectors": counts["OCCUPIED"],
        "excluded_vectors": counts["EXCLUDED"],
        "gain_response_s": gain_response_s,
    }
    raw_handle = raw_out.open("w", encoding="utf-8") if raw_out else None
    try:
        with gzip.open(path, "wt", encoding="utf-8") as handle:
            session = {"type": "session", "session_id": path.stem, **meta}
            session["gain_response_s"] = gain_response_s
            handle.write(json.dumps(session) + "\n")
            feature_at = {id(item): feature for feature in features for item in (feature["source"],)}
            for item in kept:
                ts, seq, amp, pi_mono = item[:4]
                gain = item[4] if len(item) >= 5 else _gain_meta(None)
                record = {
                    "type": "csi", "ts_us": ts, "link_id": meta["link_id"], "seq": seq, "amp": amp,
                    "applied_scale": gain["applied_scale"], "library_gain": gain["library_gain"],
                    "gain_response_s": gain["gain_response_s"],
                }
                if pi_mono is not None:
                    record["pi_mono_us"] = pi_mono
                handle.write(json.dumps(record) + "\n")
                if raw_handle is not None:
                    raw_handle.write(json.dumps({
                        "ts_us": ts, "seq": seq, "pi_mono_us": pi_mono, "amp": amp,
                    }) + "\n")
                feature = feature_at.get(id(item))
                if feature is None:
                    continue
                handle.write(json.dumps({
                    "type": "feature", "ts_us": ts, "link_id": meta["link_id"], "seq": seq,
                    "features": feature["features"], "complete": True,
                    "disturbance_warning": feature["disturbance_warning"],
                    "label": feature["label"], **feature["quality"],
                }) + "\n")
            for event in events:
                handle.write(json.dumps(event) + "\n")
            handle.write(json.dumps(quality) + "\n")
    finally:
        if raw_handle is not None:
            raw_handle.close()
    if loss > 0.02:
        print(f"packet loss {loss:.3f}", file=sys.stderr)
    return {"path": path, **quality}


def _session_path(out_root: Path, meta: dict) -> Path:
    folder = out_root / corridor_dirname(meta["corridor_id"])
    folder.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ")
    session_id = f"{meta['session_label'].lower()}-{stamp}"
    path = folder / f"{session_id}.jsonl.gz"
    suffix = 1
    while path.exists():
        path = folder / f"{session_id}-{suffix}.jsonl.gz"
        suffix += 1
    return path


def ingest_frames(frames: list[tuple], meta: dict, events: list[dict] | None = None) -> tuple[list[tuple], list[dict], dict]:
    events = list(events or [])
    kept: list[tuple] = []
    produced: list[dict] = []
    dropped = 0
    last_ts = None
    seen = set()
    extractor = Extractor()
    first_ts = None
    for frame in frames:
        ts, seq, amp, pi_mono, gain_raw = _split_frame(frame)
        gain = _gain_meta(gain_raw)
        if len(amp) != 52 or any(value != value for value in amp):
            dropped += 1
            continue
        if seq in seen or (last_ts is not None and ts <= last_ts):
            dropped += 1
            continue
        seen.add(seq)
        last_ts = ts
        if first_ts is None:
            first_ts = ts
        item = (ts, seq, amp, pi_mono, gain)
        kept.append(item)
        features = extractor.push(ts, amp)
        if features is None:
            continue
        label_us = ts if pi_mono is None else int(pi_mono)
        produced.append({
            "source": item,
            "features": features,
            "label_us": label_us,
            "quality": frame_quality(kept, len(kept) - 1, first_ts, features),
        })
    return kept, produced, {"dropped": dropped}


def collect(frames: list[tuple], meta: dict, out_root: Path, events: list[dict] | None = None,
            raw_out: Path | None = None, stats: dict | None = None) -> dict:
    meta = {"role": meta.get("role", "train"), **meta}
    kept, produced, counted = ingest_frames(frames, meta, events)
    merged = {"dropped": counted["dropped"], "bad_datagrams": 0, "link_mismatch": 0}
    if stats:
        merged.update(stats)
        merged["dropped"] = counted["dropped"] + stats.get("dropped", 0)
    path = _session_path(out_root, meta)
    return _write_outputs(path, raw_out, meta, kept, produced, list(events or []), merged)


def _banner(role: str, label: str, corridor: str, link: str) -> None:
    print(f"ROLE={role} LABEL={label} corridor={corridor} link={link}", flush=True)


def validate_role(role: str, label: str | None) -> tuple[str | None, str | None]:
    if role == "train":
        if label is None:
            return "EMPTY", None
        if label != "EMPTY":
            return None, "role=train only accepts EMPTY (train corridors stay empty)"
        return "EMPTY", None
    if label is None:
        return None, "role=test requires --session-label"
    return label, None


def parse_start_at(text: str, now_s: float) -> float:
    text = text.strip()
    if len(text) <= 5 and text.count(":") == 1 and "T" not in text:
        hour_text, minute_text = text.split(":", 1)
        hour, minute = int(hour_text), int(minute_text)
        if not (0 <= hour <= 23 and 0 <= minute <= 59):
            raise ValueError("bad HH:MM")
        local = datetime.fromtimestamp(now_s).astimezone()
        target = local.replace(hour=hour, minute=minute, second=0, microsecond=0)
        if target.timestamp() <= now_s:
            target += timedelta(days=1)
        return target.timestamp()
    iso = text[:-1] + "+00:00" if text.endswith("Z") else text
    parsed = datetime.fromisoformat(iso)
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=datetime.now().astimezone().tzinfo)
    return parsed.timestamp()


def schedule_slices(start_s: float, end_s: float, now_s: float, cap_s: float | None = None) -> list[tuple[float, float]]:
    if cap_s is None:
        cap_s = MAX_SLICE_S
    cursor = max(now_s, start_s)
    slices = []
    while cursor < end_s - 1e-6:
        stop = min(end_s, cursor + cap_s)
        if stop <= cursor:
            break
        slices.append((cursor, stop))
        cursor = stop
    return slices


def load_schedules(path: Path) -> list[dict]:
    if not path.is_file():
        return []
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return []
    if not isinstance(value, list):
        return []
    rows = []
    for item in value:
        if isinstance(item, dict) and "id" in item and "start_s" in item and "end_s" in item:
            rows.append(item)
    return rows


def _schedule_alive(path: Path, schedule_id: str, now_s: float) -> bool:
    for item in load_schedules(path):
        if str(item.get("id")) != str(schedule_id):
            continue
        if item.get("completed") or str(item.get("occupancy", "")).upper() != "EMPTY":
            return False
        return float(item["start_s"]) <= now_s < float(item["end_s"])
    return False


class Clock:
    def time(self) -> float:
        return time.time()

    def monotonic(self) -> float:
        return time.monotonic()

    def monotonic_ns(self) -> int:
        return time.monotonic_ns()

    def sleep(self, seconds: float) -> None:
        if seconds > 0:
            time.sleep(seconds)


def _wait_until(target: float, clock: Clock) -> None:
    print(f"waiting until unix {target:.0f}", flush=True)
    while clock.time() < target and not _STOP:
        clock.sleep(min(10.0, target - clock.time()))


def _on_sigint(signum, frame) -> None:
    global _STOP
    _STOP = True


def _stdin_is_tty() -> bool:
    try:
        return sys.stdin is not None and not sys.stdin.closed and sys.stdin.isatty()
    except OSError:
        return False


def _udp_watch(sock, stdin_on: bool) -> list:
    watch = [sock]
    if stdin_on and _stdin_is_tty():
        watch.append(sys.stdin)
    return watch


def _read_key(ready: list, stdin_on: list) -> str | None:
    if not stdin_on[0] or sys.stdin not in ready:
        return None
    try:
        line = sys.stdin.readline()
    except OSError as exc:
        stdin_on[0] = False
        print(f"stdin unusable ({exc}); keyboard labels off for this session", file=sys.stderr)
        return None
    if not line:
        return None
    key = line.strip().lower()[:1]
    return {"p": "PERSON", "e": "EMPTY", "u": "UNKNOWN"}.get(key)


def run_udp_session(args, meta: dict, clock: Clock, duration_s: float, schedule_path: Path | None,
                    schedule_id: str | None, select_fn=None) -> dict:
    global _STOP
    stats = {"bad_datagrams": 0, "link_mismatch": 0, "dropped": 0}
    events: list[dict] = []
    kept: list[tuple] = []
    produced: list[dict] = []
    seen = set()
    last_ts = None
    first_ts = None
    boot_id = None
    boot_error = False
    extractor = Extractor()
    link_n = link_number(meta["link_id"])
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    choose = select_fn or select.select
    try:
        sock.bind((args.bind, args.udp_port))
        sock.setblocking(False)
        started = clock.monotonic()
        deadline = started + duration_s
        next_progress = started
        next_schedule = started
        state = "UNKNOWN" if meta["session_label"] == "MIXED" else meta["session_label"]
        counts = {"EMPTY": 0, "OCCUPIED": 0, "EXCLUDED": 0}
        warnings = 0
        stdin_on = [_stdin_is_tty()]
        if meta.get("session_label") == "MIXED" and not args.labels_from_dashboard and not stdin_on[0]:
            print(
                "warning: MIXED session has no label source "
                "(stdin is not a tty and --labels-from-dashboard is off)",
                file=sys.stderr,
            )
        while clock.monotonic() < deadline and not _STOP:
            if schedule_id and schedule_path and clock.monotonic() >= next_schedule:
                if not _schedule_alive(schedule_path, schedule_id, clock.time()):
                    break
                next_schedule = clock.monotonic() + 10.0
            timeout = min(0.2, max(0.0, deadline - clock.monotonic()))
            watch = _udp_watch(sock, stdin_on[0])
            try:
                ready, _, _ = choose(watch, [], [], timeout)
            except (OSError, ValueError):
                ready = []
            key_state = _read_key(ready, stdin_on)
            if key_state is not None:
                stamp = clock.monotonic_ns() // 1000
                events.append({"type": "label_event", "pi_mono_us": stamp, "state": key_state, "source": "keyboard"})
                state = key_state
                print(f"label {key_state} at {stamp}", flush=True)
            if sock not in ready:
                now = clock.monotonic()
                if now >= next_progress:
                    elapsed = max(now - started, 1e-6)
                    loss = sequence_loss_ratio([item[1] for item in kept])
                    print(
                        f"progress frames/s={len(kept) / elapsed:.1f} loss={loss:.4f} "
                        f"EMPTY={counts['EMPTY']} OCCUPIED={counts['OCCUPIED']} EXCLUDED={counts['EXCLUDED']} "
                        f"state={state} disturbances={warnings}",
                        flush=True,
                    )
                    next_progress = now + 10.0
                continue
            packet, _addr = sock.recvfrom(2048)
            pi_mono = clock.monotonic_ns() // 1000
            parsed = take_datagram(packet, link_n, stats)
            if parsed is None:
                continue
            if boot_id is None:
                boot_id = parsed["boot_id"]
            elif parsed["boot_id"] != boot_id:
                boot_error = True
                print(f"boot_id changed {boot_id} -> {parsed['boot_id']}; closing session", file=sys.stderr)
                break
            for frame in parsed["frames"]:
                amp = scaled_amplitudes(frame["csi"], frame.get("applied_scale", frame.get("gain_scale", 1.0)))
                ts = int(frame["timestamp_us"])
                seq = int(frame["tx_sequence"])
                if amp is None or seq in seen or (last_ts is not None and ts <= last_ts):
                    stats["dropped"] += 1
                    continue
                seen.add(seq)
                last_ts = ts
                if first_ts is None:
                    first_ts = ts
                gain = _gain_meta(frame, parsed.get("gain_response_s"))
                item = (ts, seq, amp, pi_mono, gain)
                kept.append(item)
                features = extractor.push(ts, amp)
                if features is None:
                    continue
                label = feature_label(pi_mono, events, meta["session_label"])
                counts[label] += 1
                if disturbance_warning(features, label):
                    warnings += 1
                    print(f"EMPTY disturbance at seq {seq}", file=sys.stderr)
                produced.append({
                    "source": item, "features": features, "label_us": pi_mono,
                    "quality": frame_quality(kept, len(kept) - 1, first_ts, features),
                })
        stats["elapsed_s"] = max(clock.monotonic() - started, 1e-6)
    finally:
        sock.close()
    if args.labels_from_dashboard and kept:
        start_us = min(item[3] for item in kept if item[3] is not None)
        end_us = max(item[3] for item in kept if item[3] is not None)
        events.extend(dashboard_events(LABELS_DIR, meta["link_id"], start_us, end_us + 1))
    path = _session_path(args.out_root, meta)
    result = _write_outputs(path, args.raw_out, meta, kept, produced, events, stats)
    if boot_error:
        result["boot_error"] = True
    return result


def _run_one(args, role: str, label: str, clock: Clock, duration_min: float | None = None,
             schedule_id: str | None = None, window: tuple[float, float] | None = None,
             schedule_path: Path | None = None) -> int:
    meta = {
        "corridor_id": args.corridor_id,
        "session_label": label,
        "session_env": args.session_env,
        "tx_h": args.tx_h,
        "rx_h": args.rx_h,
        "duration_min": args.duration_min if duration_min is None else duration_min,
        "link_id": args.link_id,
        "role": role,
    }
    if schedule_id is not None and window is not None:
        meta["schedule_id"] = schedule_id
        meta["window_start_s"] = window[0]
        meta["window_end_s"] = window[1]
    _banner(role, label, args.corridor_id, args.link_id)
    if args.mock:
        frames = mock_frames(meta["duration_min"])
        result = collect(frames, meta, args.out_root, raw_out=args.raw_out)
    elif args.csi_jsonl is not None:
        frames = _load_jsonl(args.csi_jsonl)
        if not frames:
            print("no frames", file=sys.stderr)
            return 1
        result = collect(frames, meta, args.out_root, raw_out=args.raw_out)
    elif args.udp_port is not None:
        result = run_udp_session(args, meta, clock, meta["duration_min"] * 60.0, schedule_path, schedule_id)
        if result.get("boot_error"):
            print(f"wrote {result['path']} boot_id changed", file=sys.stderr)
            return 1
    else:
        print("compact Pi has no raw CSI socket; use --udp-port, --mock or --csi-jsonl", file=sys.stderr)
        return 2
    print(
        f"wrote {result['path']} kept={result['kept']} features={result['feature_count']} "
        f"loss={result['loss_ratio']:.4f}",
        flush=True,
    )
    return 0 if result["kept"] else 1


def follow_dashboard(args, clock: Clock, schedule_path: Path, follow_until: float | None) -> int:
    if args.role != "train" or (args.session_label not in (None, "EMPTY")):
        print("follow-dashboard-schedule only collects role=train label=EMPTY", file=sys.stderr)
        return 2
    logged: set[str] = set()
    ran = 0
    while not _STOP:
        now = clock.time()
        if follow_until is not None and now >= follow_until:
            return 0 if ran or follow_until is not None else 1
        chosen = None
        for item in load_schedules(schedule_path):
            sid = str(item["id"])
            occupancy = str(item.get("occupancy", "")).upper()
            if item.get("completed") or occupancy != "EMPTY":
                if sid not in logged:
                    logged.add(sid)
                    if occupancy != "EMPTY":
                        print(f"schedule {sid} skipped occupancy={occupancy}", flush=True)
                    elif item.get("completed"):
                        print(f"schedule {sid} skipped completed", flush=True)
                continue
            start_s, end_s = float(item["start_s"]), float(item["end_s"])
            if start_s <= now < end_s:
                chosen = item
                break
        if chosen is None:
            clock.sleep(10.0)
            continue
        start_s, end_s = float(chosen["start_s"]), float(chosen["end_s"])
        for window in schedule_slices(start_s, end_s, clock.time()):
            if _STOP:
                break
            if follow_until is not None and clock.time() >= follow_until:
                return 0
            code = _run_one(
                args, "train", "EMPTY", clock,
                duration_min=(window[1] - window[0]) / 60.0,
                schedule_id=str(chosen["id"]), window=window, schedule_path=schedule_path,
            )
            ran += 1
            remain = window[1] - clock.time()
            if remain > 0 and args.mock:
                clock.sleep(remain)
            if code != 0:
                return code
    return 0


def relabel_main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(description="Relabel saved sessions from their stored label events.")
    parser.add_argument("--relabel", type=Path, nargs="+", required=True)
    parser.add_argument("--relabel-out-dir", type=Path, required=True)
    args = parser.parse_args(argv)
    for src in args.relabel:
        dst = args.relabel_out_dir / src.name
        if dst.resolve() == src.resolve():
            print(f"refusing to overwrite {src}", file=sys.stderr)
            return 2
        counts = relabel_file(src, dst)
        print(f"relabeled {src} -> {dst} {counts}")
    return 0


def main(argv: list[str] | None = None, *, clock: Clock | None = None,
         schedule_path: Path | None = None, follow_until: float | None = None) -> int:
    global _STOP, DISTURBANCE_LOWBAND_LIMIT
    if argv is None:
        argv = sys.argv[1:]
    if "--relabel" in argv:
        return relabel_main(list(argv))
    _STOP = False
    clock = clock or Clock()
    parser = argparse.ArgumentParser(description="Collect an empty-corridor feature session.")
    parser.add_argument("--corridor-id", required=True)
    parser.add_argument("--session-label", choices=sorted(LABELS), default=None)
    parser.add_argument("--session-env", required=True)
    parser.add_argument("--tx-h", required=True, type=float)
    parser.add_argument("--rx-h", required=True, type=float)
    parser.add_argument("--duration-min", type=float, default=10.0)
    parser.add_argument("--link-id", default="link-1")
    parser.add_argument("--out-root", type=Path, default=ROOT / "data" / "datasets")
    parser.add_argument("--mock", action="store_true")
    parser.add_argument("--csi-jsonl", type=Path)
    parser.add_argument("--udp-port", type=int)
    parser.add_argument("--bind", default="0.0.0.0")
    parser.add_argument("--role", choices=("train", "test"), default="train")
    parser.add_argument("--raw-out", type=Path)
    parser.add_argument("--labels-from-dashboard", action="store_true")
    parser.add_argument("--start-at")
    parser.add_argument("--repeat", type=int, default=1)
    parser.add_argument("--gap-min", type=float, default=0.0)
    parser.add_argument("--follow-dashboard-schedule", action="store_true")
    parser.add_argument("--disturbance-threshold", type=float, default=DISTURBANCE_LOWBAND_LIMIT)
    args = parser.parse_args(argv)
    DISTURBANCE_LOWBAND_LIMIT = args.disturbance_threshold
    if args.duration_min <= 0.0 or args.duration_min > 180.0:
        print("duration-min must be in (0, 180]", file=sys.stderr)
        return 2
    if args.repeat < 1 or args.gap_min < 0:
        print("repeat must be >= 1 and gap-min >= 0", file=sys.stderr)
        return 2
    if args.start_at and args.follow_dashboard_schedule:
        print("use either --start-at or --follow-dashboard-schedule", file=sys.stderr)
        return 2
    label, error = validate_role(args.role, args.session_label)
    if error and not args.follow_dashboard_schedule:
        print(error, file=sys.stderr)
        return 2
    if args.udp_port is not None and not 1 <= args.udp_port <= 65535:
        print("udp-port out of range", file=sys.stderr)
        return 2
    if args.udp_port == 8888:
        print("refusing WRAW on the WIV5 port 8888", file=sys.stderr)
        return 2
    signal.signal(signal.SIGINT, _on_sigint)
    if args.follow_dashboard_schedule:
        path = schedule_path or SCHEDULE_PATH
        before = path.read_bytes() if path.is_file() else None
        code = follow_dashboard(args, clock, path, follow_until)
        if before is not None and path.read_bytes() != before:
            print("schedule file was modified; that is a bug", file=sys.stderr)
            return 2
        return code
    if args.start_at:
        try:
            target = parse_start_at(args.start_at, clock.time())
        except ValueError as exc:
            print(f"bad --start-at: {exc}", file=sys.stderr)
            return 2
        _wait_until(target, clock)
    for index in range(args.repeat):
        if _STOP:
            return 0
        if index and args.gap_min:
            print(f"gap {args.gap_min} min", flush=True)
            clock.sleep(args.gap_min * 60.0)
        code = _run_one(args, args.role, label or "EMPTY", clock)
        if code != 0:
            return code
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
