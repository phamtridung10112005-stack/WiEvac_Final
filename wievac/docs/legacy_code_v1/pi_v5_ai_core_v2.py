#!/usr/bin/env python3
"""WiEvac Raspberry Pi Protocol V2 ingest, Formula, recorder, and AI inference.

Formula is an uncalibrated statistical heuristic.  It is deliberately separate
from the optional LightGBM inference path and is never used as ground truth.
"""

from __future__ import annotations

import base64
import json
import logging
import math
import os
import queue
import re
import signal
import socket
import statistics
import struct
import threading
import time
import zlib
from collections import deque
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any, Deque, Dict, Iterable, List, Mapping, Optional, Sequence, Tuple


LOG = logging.getLogger("wievac.pi_v2")


# ---------------------------------------------------------------------------
# User/runtime configuration.  These values are transport/resource/statistical
# policy parameters, not universal physical CSI thresholds.  Deployment values
# are read from environment variables so credentials are not embedded or logged.
# ---------------------------------------------------------------------------


def _env_int(name: str, default: int, minimum: int, maximum: int) -> int:
    raw = os.getenv(name)
    value = default if raw is None else int(raw)
    if not minimum <= value <= maximum:
        raise ValueError(f"{name} must be in [{minimum}, {maximum}], got {value}")
    return value


def _env_float(name: str, default: float, minimum: float, maximum: float) -> float:
    raw = os.getenv(name)
    value = default if raw is None else float(raw)
    if not math.isfinite(value) or not minimum <= value <= maximum:
        raise ValueError(f"{name} must be finite and in [{minimum}, {maximum}], got {value}")
    return value


def _env_bool(name: str, default: bool) -> bool:
    raw = os.getenv(name)
    if raw is None:
        return default
    normalized = raw.strip().lower()
    if normalized in {"1", "true", "yes", "on"}:
        return True
    if normalized in {"0", "false", "no", "off"}:
        return False
    raise ValueError(f"{name} must be a boolean value")


def _safe_component(value: str, field_name: str) -> str:
    if value in {"", ".", ".."} or not re.fullmatch(r"[A-Za-z0-9_.-]+", value):
        raise ValueError(f"{field_name} must match [A-Za-z0-9_.-]+")
    return value


@dataclass(frozen=True)
class AppConfig:
    bind_host: str
    bind_port: int
    receive_buffer_bytes: int
    stale_after_ms: int
    warmup_windows: int
    warmup_relative_spread_limit: float
    baseline_history_windows: int
    baseline_update_streak: int
    baseline_update_max_z: float
    evidence_gate_z: float
    evidence_scale_z: float
    evidence_persistence_ms: int
    persistent_shift_ms: int
    formula_attack_ms: int
    formula_release_ms: int
    minimum_samples: int
    minimum_measurements_per_window: int
    minimum_window_ms: int
    maximum_window_ms: int
    minimum_valid_subcarriers: float
    maximum_packet_loss_ratio: float
    maximum_invalid_csi_ratio: float
    maximum_queue_drop_ratio: float
    nuisance_suspect_robust_z: float
    nuisance_confidence_factor: float
    capture_ack_timeout_ms: int
    recording_enabled: bool
    capture_root: Path
    accepted_link_id: int
    dashboard_host: str
    dashboard_port: int
    pi_id: str
    corridor_id: str
    session_id: str
    recorder_queue_depth: int
    model_paths: Mapping[str, Tuple[Optional[Path], Optional[Path]]]

    @classmethod
    def from_environment(cls) -> "AppConfig":
        # This file lives at <project_root>/pi/app/.  Captures must never fall
        # back to pi/app/data because that makes real data easy to misplace.
        project_root = Path(__file__).resolve().parents[2]
        generated_session = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")

        def optional_path(name: str) -> Optional[Path]:
            raw = os.getenv(name, "").strip()
            return Path(raw).expanduser().resolve() if raw else None

        config = cls(
            bind_host=os.getenv("WIEVAC_UDP_BIND", "0.0.0.0"),
            bind_port=_env_int("WIEVAC_UDP_PORT", 8888, 1, 65535),
            receive_buffer_bytes=_env_int("WIEVAC_UDP_RCVBUF", 262144, 65536, 16 * 1024 * 1024),
            stale_after_ms=_env_int("WIEVAC_STALE_AFTER_MS", 3500, 500, 60000),
            warmup_windows=_env_int("WIEVAC_WARMUP_WINDOWS", 60, 5, 1000),
            warmup_relative_spread_limit=_env_float(
                "WIEVAC_WARMUP_RELATIVE_SPREAD_LIMIT", 1.5, 0.05, 20.0
            ),
            baseline_history_windows=_env_int("WIEVAC_BASELINE_HISTORY", 200, 20, 10000),
            baseline_update_streak=_env_int("WIEVAC_BASELINE_UPDATE_STREAK", 5, 2, 1000),
            baseline_update_max_z=_env_float("WIEVAC_BASELINE_UPDATE_MAX_Z", 0.5, 0.0, 5.0),
            evidence_gate_z=_env_float("WIEVAC_EVIDENCE_GATE_Z", 1.5, 0.0, 10.0),
            evidence_scale_z=_env_float("WIEVAC_EVIDENCE_SCALE_Z", 3.0, 0.1, 50.0),
            evidence_persistence_ms=_env_int(
                "WIEVAC_EVIDENCE_PERSISTENCE_MS", 3000, 500, 120000
            ),
            persistent_shift_ms=_env_int("WIEVAC_PERSISTENT_SHIFT_MS", 30000, 1000, 3600000),
            formula_attack_ms=_env_int("WIEVAC_FORMULA_ATTACK_MS", 1500, 50, 60000),
            formula_release_ms=_env_int("WIEVAC_FORMULA_RELEASE_MS", 5000, 50, 120000),
            minimum_samples=_env_int("WIEVAC_MINIMUM_SAMPLES", 20, 1, 100000),
            minimum_measurements_per_window=_env_int(
                "WIEVAC_MINIMUM_MEASUREMENTS_PER_WINDOW", 20, 1, 100000
            ),
            minimum_window_ms=_env_int("WIEVAC_MINIMUM_WINDOW_MS", 500, 1, 60000),
            maximum_window_ms=_env_int("WIEVAC_MAXIMUM_WINDOW_MS", 5000, 1, 120000),
            minimum_valid_subcarriers=_env_float(
                "WIEVAC_MINIMUM_VALID_SUBCARRIERS", 40.0, 1.0, 512.0
            ),
            maximum_packet_loss_ratio=_env_float(
                "WIEVAC_MAXIMUM_PACKET_LOSS_RATIO", 0.25, 0.0, 1.0
            ),
            maximum_invalid_csi_ratio=_env_float(
                "WIEVAC_MAXIMUM_INVALID_CSI_RATIO", 0.25, 0.0, 1.0
            ),
            maximum_queue_drop_ratio=_env_float(
                "WIEVAC_MAXIMUM_QUEUE_DROP_RATIO", 0.10, 0.0, 1.0
            ),
            # These are robust-z policy settings relative to each link's
            # operator-confirmed empty-corridor baseline, not physical radio thresholds.
            nuisance_suspect_robust_z=_env_float(
                "WIEVAC_NUISANCE_SUSPECT_ROBUST_Z", 4.0, 0.5, 50.0
            ),
            nuisance_confidence_factor=_env_float(
                "WIEVAC_NUISANCE_CONFIDENCE_FACTOR", 0.25, 0.0, 1.0
            ),
            capture_ack_timeout_ms=_env_int("WIEVAC_CAPTURE_ACK_TIMEOUT_MS", 1500, 100, 10000),
            recording_enabled=_env_bool("WIEVAC_RECORDING", True),
            capture_root=Path(
                os.getenv("WIEVAC_CAPTURE_ROOT", str(project_root / "data" / "real"))
            ).expanduser().resolve(),
            accepted_link_id=_env_int("WIEVAC_ACCEPTED_LINK_ID", 1, 1, 0xFFFFFFFF),
            dashboard_host=os.getenv("WIEVAC_DASHBOARD_BIND", "0.0.0.0"),
            dashboard_port=_env_int("WIEVAC_DASHBOARD_PORT", 8080, 1, 65535),
            pi_id=_safe_component(os.getenv("WIEVAC_PI_ID", "pi-01"), "WIEVAC_PI_ID"),
            corridor_id=_safe_component(
                os.getenv("WIEVAC_CORRIDOR_ID", "test-corridor-01"), "WIEVAC_CORRIDOR_ID"
            ),
            session_id=_safe_component(
                os.getenv("WIEVAC_SESSION_ID", generated_session), "WIEVAC_SESSION_ID"
            ),
            recorder_queue_depth=_env_int("WIEVAC_RECORDER_QUEUE_DEPTH", 4096, 16, 1000000),
            model_paths={
                "AI-Q": (optional_path("WIEVAC_AI_Q_MODEL"), optional_path("WIEVAC_AI_Q_META")),
                "AI-P": (optional_path("WIEVAC_AI_P_MODEL"), optional_path("WIEVAC_AI_P_META")),
                "AI-C": (optional_path("WIEVAC_AI_C_MODEL"), optional_path("WIEVAC_AI_C_META")),
            },
        )
        if config.minimum_window_ms > config.maximum_window_ms:
            raise ValueError("WIEVAC_MINIMUM_WINDOW_MS must not exceed WIEVAC_MAXIMUM_WINDOW_MS")
        if config.baseline_update_streak > config.baseline_history_windows:
            raise ValueError("WIEVAC_BASELINE_UPDATE_STREAK must not exceed WIEVAC_BASELINE_HISTORY")
        if config.baseline_history_windows < config.warmup_windows:
            raise ValueError("WIEVAC_BASELINE_HISTORY must be at least WIEVAC_WARMUP_WINDOWS")
        if config.baseline_update_max_z >= config.evidence_gate_z:
            raise ValueError("WIEVAC_BASELINE_UPDATE_MAX_Z must be below WIEVAC_EVIDENCE_GATE_Z")
        if {part.lower() for part in config.capture_root.parts} & {"synthetic", "validation", "simulation"}:
            raise ValueError("WIEVAC_CAPTURE_ROOT must not point to synthetic, validation, or simulation data")
        return config


# ---------------------------------------------------------------------------
# WiEvac Protocol V2 wire contract.
#
# All integer fields and IEEE-754 binary32 feature values are big-endian.
# C peers serialize fields explicitly; no native packed-struct layout is used.
#
# Fixed header (68 bytes):
#   0 u32 magic 0x57495632 (ASCII WIV2); 4 u8 version; 5 u8 type;
#   6 u16 flags; 8 u16 header_len; 10 u16 payload_len;
#  12 u16 feature_schema; 14 u16 reserved=0;
#  16/20/24/28 u32 link_id/tx_id/rx_id/sequence;
#  32 u64 sender monotonic timestamp_us;
#  40/44 u32 window_duration_ms/sample_count;
#  48/52/56/60 u32 cumulative invalid/wrong-source/queue-drop/loss;
#  64 u32 CRC-32/ISO-HDLC over header[0:64] followed by payload;
#  68 payload.
# Sender timestamps are boot-relative, not synchronized wall time.  Pi staleness
# therefore uses the local monotonic receive clock and records both timestamps.
# PI_ALIVE is a header-only application liveness acknowledgement.  Pi echoes
# the accepted FEATURE_RUN sequence; RX validates the UDP service endpoint,
# identities, CRC, and a recent sent sequence before clearing unavailable state.
# ---------------------------------------------------------------------------

PROTOCOL_MAGIC = 0x57495632
PROTOCOL_VERSION = 2
PROTOCOL_HEADER_LEN = 68
PROTOCOL_CRC_OFFSET = 64
FEATURE_SCHEMA_VERSION = 2
FORMULA_VERSION = "formula-v2-robust-joint-2-nuisance"
MAX_DATAGRAM_BYTES = 2048

MSG_FEATURE_RUN = 0x20
MSG_CSI_SNAPSHOT = 0x21
MSG_PI_ALIVE = 0x30
MSG_CAPTURE_CONTROL = 0x31
MSG_CAPTURE_ACK = 0x32

CAPTURE_COMMAND_START = 1
CAPTURE_COMMAND_STOP = 2
CAPTURE_MODE_RUN = 0
CAPTURE_MODE_CAPTURE = 1

FLAG_PAIRED = 0x0001
FLAG_RUN = 0x0002
FLAG_CAPTURE = 0x0004
FLAG_WINDOW_VALID = 0x0008
FLAG_CHANNEL_FILTER_ENABLED = 0x0010
FLAG_FIRST_WORD_INVALID_SEEN = 0x0020
FLAG_GAIN_METADATA_VALID = 0x0040
FLAG_SYSTEM_UNAVAILABLE = 0x0080
KNOWN_FLAGS = (
    FLAG_PAIRED
    | FLAG_RUN
    | FLAG_CAPTURE
    | FLAG_WINDOW_VALID
    | FLAG_CHANNEL_FILTER_ENABLED
    | FLAG_FIRST_WORD_INVALID_SEEN
    | FLAG_GAIN_METADATA_VALID
    | FLAG_SYSTEM_UNAVAILABLE
)

HEADER_PREFIX = struct.Struct(">IBBHHHHHIIIIQIIIIII")
CRC_FIELD = struct.Struct(">I")
FEATURE_PAYLOAD = struct.Struct(">12f4I")
CAPTURE_CONTROL_PAYLOAD = struct.Struct(">B")
CAPTURE_ACK_PAYLOAD = struct.Struct(">BBI")
# 16-byte layout descriptor followed by 16 bytes of source/radio metadata.
SNAPSHOT_METADATA = struct.Struct(">IBBBBHHhH6sBBbbBbBBBB")

FEATURE_FLOAT_NAMES = (
    "delta_amp",
    "std_dev",
    "spectral_roughness",
    "rssi_mean_dbm",
    "rssi_std_db",
    "noise_floor_mean_dbm",
    "agc_gain_mean",
    "fft_gain_mean",
    "packet_rate_hz",
    "packet_loss_ratio",
    "jitter_ms",
    "valid_subcarrier_mean",
)
FEATURE_UINT_NAMES = (
    "measurement_rx_window",
    "first_word_invalid_count",
    "tx_firmware_version",
    "rx_firmware_version",
)


class ProtocolError(ValueError):
    """A datagram violates the Protocol V2 contract."""


@dataclass(frozen=True)
class ProtocolHeader:
    message_type: int
    flags: int
    payload_length: int
    feature_schema_version: int
    link_id: int
    tx_id: int
    rx_id: int
    sequence: int
    sender_timestamp_us: int
    window_duration_ms: int
    sample_count: int
    invalid_csi_count: int
    wrong_source_count: int
    queue_drop_count: int
    packet_loss_count: int


@dataclass(frozen=True)
class DecodedPacket:
    header: ProtocolHeader
    kind: str
    values: Mapping[str, Any]


class ProtocolV2Decoder:
    @staticmethod
    def _crc(datagram: bytes, payload: bytes) -> int:
        crc = zlib.crc32(datagram[:PROTOCOL_CRC_OFFSET])
        return zlib.crc32(payload, crc) & 0xFFFFFFFF

    def decode(self, datagram: bytes) -> DecodedPacket:
        if len(datagram) < PROTOCOL_HEADER_LEN:
            raise ProtocolError(f"datagram too short: {len(datagram)}")
        if len(datagram) > MAX_DATAGRAM_BYTES:
            raise ProtocolError(f"datagram too large: {len(datagram)}")

        fields = HEADER_PREFIX.unpack_from(datagram, 0)
        (
            magic,
            version,
            message_type,
            flags,
            header_length,
            payload_length,
            feature_schema,
            reserved,
            link_id,
            tx_id,
            rx_id,
            sequence,
            sender_timestamp_us,
            window_duration_ms,
            sample_count,
            invalid_count,
            wrong_source_count,
            queue_drop_count,
            packet_loss_count,
        ) = fields
        if magic != PROTOCOL_MAGIC:
            raise ProtocolError(f"bad magic 0x{magic:08x}")
        if version != PROTOCOL_VERSION:
            raise ProtocolError(f"unsupported protocol version {version}")
        if header_length != PROTOCOL_HEADER_LEN:
            raise ProtocolError(f"unexpected header length {header_length}")
        if reserved != 0:
            raise ProtocolError("reserved header field is non-zero")
        if flags & ~KNOWN_FLAGS:
            raise ProtocolError(f"unknown flags 0x{flags & ~KNOWN_FLAGS:04x}")
        expected_length = PROTOCOL_HEADER_LEN + payload_length
        if len(datagram) != expected_length:
            raise ProtocolError(
                f"payload length mismatch: header={payload_length}, datagram={len(datagram)}"
            )
        payload = datagram[PROTOCOL_HEADER_LEN:]
        expected_crc = CRC_FIELD.unpack_from(datagram, PROTOCOL_CRC_OFFSET)[0]
        actual_crc = self._crc(datagram, payload)
        if actual_crc != expected_crc:
            raise ProtocolError(
                f"CRC mismatch: expected=0x{expected_crc:08x}, actual=0x{actual_crc:08x}"
            )
        if link_id == 0 or tx_id == 0 or rx_id == 0:
            raise ProtocolError("feature/snapshot identity fields must be non-zero")
        if message_type in (MSG_FEATURE_RUN, MSG_CSI_SNAPSHOT):
            if feature_schema != FEATURE_SCHEMA_VERSION:
                raise ProtocolError(
                    f"feature schema {feature_schema} is incompatible with {FEATURE_SCHEMA_VERSION}"
                )
        elif message_type == MSG_CAPTURE_ACK:
            if feature_schema != 0:
                raise ProtocolError("capture ACK must not declare a feature schema")
        else:
            raise ProtocolError(f"unsupported message type 0x{message_type:02x}")

        header = ProtocolHeader(
            message_type=message_type,
            flags=flags,
            payload_length=payload_length,
            feature_schema_version=feature_schema,
            link_id=link_id,
            tx_id=tx_id,
            rx_id=rx_id,
            sequence=sequence,
            sender_timestamp_us=sender_timestamp_us,
            window_duration_ms=window_duration_ms,
            sample_count=sample_count,
            invalid_csi_count=invalid_count,
            wrong_source_count=wrong_source_count,
            queue_drop_count=queue_drop_count,
            packet_loss_count=packet_loss_count,
        )
        if message_type == MSG_FEATURE_RUN:
            mode_flags = flags & (FLAG_RUN | FLAG_CAPTURE)
            if mode_flags not in (FLAG_RUN, FLAG_CAPTURE):
                raise ProtocolError("feature packet must select exactly one RUN/CAPTURE mode")
            return DecodedPacket(header, "feature", self._decode_feature(payload))
        if message_type == MSG_CSI_SNAPSHOT:
            if not (flags & FLAG_CAPTURE) or (flags & FLAG_RUN):
                raise ProtocolError("snapshot packet must select CAPTURE mode only")
            if window_duration_ms != 0 or sample_count != 1:
                raise ProtocolError("snapshot header must describe exactly one instantaneous sample")
            return DecodedPacket(header, "snapshot", self._decode_snapshot(payload))
        if message_type == MSG_CAPTURE_ACK:
            values = self._decode_capture_ack(payload)
            if flags != values["expected_flags"] or window_duration_ms != 0 or sample_count != 0:
                raise ProtocolError("capture ACK header does not describe the confirmed runtime mode")
            if any((invalid_count, wrong_source_count, queue_drop_count, packet_loss_count)):
                raise ProtocolError("capture ACK must not carry measurement counters")
            return DecodedPacket(header, "capture_ack", values)
        raise ProtocolError(f"unsupported message type 0x{message_type:02x}")

    @staticmethod
    def _decode_capture_ack(payload: bytes) -> Mapping[str, Any]:
        if len(payload) != CAPTURE_ACK_PAYLOAD.size:
            raise ProtocolError("capture ACK payload length is invalid")
        command, actual_mode, command_sequence = CAPTURE_ACK_PAYLOAD.unpack(payload)
        if command not in (CAPTURE_COMMAND_START, CAPTURE_COMMAND_STOP):
            raise ProtocolError("capture ACK has an unknown command")
        if actual_mode not in (CAPTURE_MODE_RUN, CAPTURE_MODE_CAPTURE):
            raise ProtocolError("capture ACK has an unknown runtime mode")
        expected_flags = FLAG_CAPTURE if actual_mode == CAPTURE_MODE_CAPTURE else FLAG_RUN
        if command_sequence == 0 or command == CAPTURE_COMMAND_START and actual_mode != CAPTURE_MODE_CAPTURE:
            raise ProtocolError("capture START ACK does not confirm CAPTURE mode")
        if command == CAPTURE_COMMAND_STOP and actual_mode != CAPTURE_MODE_RUN:
            raise ProtocolError("capture STOP ACK does not confirm RUN mode")
        return {
            "command": command,
            "actual_mode": actual_mode,
            "command_sequence": command_sequence,
            "expected_flags": expected_flags,
        }

    @staticmethod
    def _decode_feature(payload: bytes) -> Mapping[str, Any]:
        if len(payload) != FEATURE_PAYLOAD.size:
            raise ProtocolError(f"feature payload must be {FEATURE_PAYLOAD.size} bytes")
        unpacked = FEATURE_PAYLOAD.unpack(payload)
        floats = unpacked[: len(FEATURE_FLOAT_NAMES)]
        if not all(math.isfinite(value) for value in floats):
            raise ProtocolError("feature payload contains NaN or infinity")
        values: Dict[str, Any] = dict(zip(FEATURE_FLOAT_NAMES, floats))
        values.update(dict(zip(FEATURE_UINT_NAMES, unpacked[len(FEATURE_FLOAT_NAMES) :])))
        if not 0.0 <= values["packet_loss_ratio"] <= 1.0:
            raise ProtocolError("packet_loss_ratio is outside [0, 1]")
        if values["packet_rate_hz"] < 0.0 or values["jitter_ms"] < 0.0:
            raise ProtocolError("packet rate and jitter must be non-negative")
        if any(values[name] < 0.0 for name in ("delta_amp", "std_dev", "spectral_roughness", "rssi_std_db")):
            raise ProtocolError("amplitude/variation features must be non-negative")
        if values["valid_subcarrier_mean"] < 0.0:
            raise ProtocolError("valid_subcarrier_mean must be non-negative")
        return values

    @staticmethod
    def _decode_snapshot(payload: bytes) -> Mapping[str, Any]:
        if len(payload) < SNAPSHOT_METADATA.size:
            raise ProtocolError("snapshot metadata is truncated")
        (
            rx_firmware_version,
            ltf_type,
            sample_format,
            decimation,
            capture_flags,
            original_complex_count,
            captured_complex_count,
            first_subcarrier_index,
            reserved,
            source_mac,
            channel,
            bandwidth_mhz,
            rssi_dbm,
            noise_floor_dbm,
            agc_gain,
            fft_gain,
            rx_state,
            sig_mode,
            secondary_channel,
            packet_decimation,
        ) = SNAPSHOT_METADATA.unpack_from(payload, 0)
        if sample_format != 1:
            raise ProtocolError(f"unsupported snapshot sample format {sample_format}")
        if ltf_type not in (1, 2, 3):
            raise ProtocolError(f"unsupported snapshot LTF type {ltf_type}")
        if decimation == 0:
            raise ProtocolError("snapshot decimation must be non-zero")
        if capture_flags & ~0x03:
            raise ProtocolError(f"unknown snapshot capture flags 0x{capture_flags:02x}")
        if reserved != 0:
            raise ProtocolError("snapshot reserved field is non-zero")
        if not any(source_mac) or source_mac[0] & 0x01:
            raise ProtocolError("snapshot source MAC is not a valid unicast address")
        if not 1 <= channel <= 14 or bandwidth_mhz not in (20, 40):
            raise ProtocolError("snapshot channel/bandwidth metadata is invalid")
        if rx_state != 0:
            raise ProtocolError(f"snapshot RX state reports decode error {rx_state}")
        if ltf_type == 2 and sig_mode != 1:
            raise ProtocolError("HT-LTF snapshot does not report an HT packet")
        if bandwidth_mhz == 20 and secondary_channel != 0:
            raise ProtocolError("HT20 snapshot must not report a secondary channel")
        if packet_decimation == 0:
            raise ProtocolError("snapshot packet decimation must be non-zero")
        if original_complex_count == 0 or captured_complex_count == 0:
            raise ProtocolError("snapshot complex sample counts must be non-zero")
        expected = SNAPSHOT_METADATA.size + 2 * captured_complex_count
        if len(payload) != expected:
            raise ProtocolError(
                f"snapshot sample length mismatch: expected {expected}, got {len(payload)}"
            )
        if captured_complex_count > original_complex_count:
            raise ProtocolError("captured complex count exceeds original count")
        raw = payload[SNAPSHOT_METADATA.size :]
        return {
            "rx_firmware_version": rx_firmware_version,
            "ltf_type": ltf_type,
            "sample_format": "int8_imag_then_real",
            "decimation": decimation,
            "capture_flags": capture_flags,
            "original_complex_count": original_complex_count,
            "captured_complex_count": captured_complex_count,
            "first_subcarrier_index": first_subcarrier_index,
            "source_mac": ":".join(f"{octet:02x}" for octet in source_mac),
            "channel": channel,
            "bandwidth_mhz": bandwidth_mhz,
            "rssi_dbm": rssi_dbm,
            "noise_floor_dbm": noise_floor_dbm,
            "agc_gain": agc_gain,
            "fft_gain": fft_gain,
            "rx_state": rx_state,
            "sig_mode": sig_mode,
            "secondary_channel": secondary_channel,
            "packet_decimation": packet_decimation,
            "samples_base64": base64.b64encode(raw).decode("ascii"),
        }


# ---------------------------------------------------------------------------
# Per-link sequence and quality handling.
# ---------------------------------------------------------------------------


@dataclass
class SequenceObservation:
    accepted: bool
    status: str
    newly_lost: int = 0


@dataclass
class SequenceTracker:
    last_sequence: Optional[int] = None
    received: int = 0
    duplicates: int = 0
    reordered: int = 0
    inferred_loss: int = 0

    def observe(self, sequence: int) -> SequenceObservation:
        if self.last_sequence is None:
            self.last_sequence = sequence
            self.received += 1
            return SequenceObservation(True, "FIRST")
        delta = (sequence - self.last_sequence) & 0xFFFFFFFF
        if delta == 0:
            self.duplicates += 1
            return SequenceObservation(False, "DUPLICATE")
        if delta < 0x80000000:
            lost = delta - 1
            self.last_sequence = sequence
            self.received += 1
            self.inferred_loss += lost
            return SequenceObservation(True, "FORWARD", lost)
        self.reordered += 1
        return SequenceObservation(False, "REORDERED")


def _counter_delta(current: int, previous: Optional[int]) -> Tuple[int, bool]:
    if previous is None:
        return 0, False
    delta = (current - previous) & 0xFFFFFFFF
    if delta >= 0x80000000:
        return 0, True
    return delta, False


@dataclass(frozen=True)
class QualityDecision:
    accepted: bool
    state: str
    reason: str
    confidence_factor: float
    deltas: Mapping[str, int]


class QualityGate:
    def __init__(self, config: AppConfig):
        self.config = config

    def evaluate(
        self, header: ProtocolHeader, features: Mapping[str, Any], deltas: Mapping[str, int]
    ) -> QualityDecision:
        if header.flags & FLAG_SYSTEM_UNAVAILABLE:
            return QualityDecision(False, "UNKNOWN", "receiver_reports_system_unavailable", 0.0, deltas)
        if not (header.flags & FLAG_PAIRED):
            return QualityDecision(False, "UNKNOWN", "receiver_not_paired", 0.0, deltas)
        if (header.flags & (FLAG_RUN | FLAG_CAPTURE)) not in (FLAG_RUN, FLAG_CAPTURE):
            return QualityDecision(False, "UNKNOWN", "feature_mode_invalid", 0.0, deltas)
        if not (header.flags & FLAG_WINDOW_VALID):
            return QualityDecision(False, "DEGRADED", "receiver_window_invalid", 0.0, deltas)
        if header.flags & FLAG_CHANNEL_FILTER_ENABLED:
            return QualityDecision(
                False,
                "DEGRADED",
                "adjacent_channel_filter_changes_roughness_schema",
                0.0,
                deltas,
            )
        if header.sample_count < self.config.minimum_samples:
            return QualityDecision(False, "DEGRADED", "insufficient_samples", 0.0, deltas)
        if features["measurement_rx_window"] < self.config.minimum_measurements_per_window:
            return QualityDecision(
                False, "DEGRADED", "insufficient_measurement_packets", 0.0, deltas
            )
        if features["tx_firmware_version"] == 0 or features["rx_firmware_version"] == 0:
            return QualityDecision(False, "UNKNOWN", "firmware_identity_unavailable", 0.0, deltas)
        if not self.config.minimum_window_ms <= header.window_duration_ms <= self.config.maximum_window_ms:
            return QualityDecision(False, "DEGRADED", "window_duration_outside_policy", 0.0, deltas)
        if features["valid_subcarrier_mean"] < self.config.minimum_valid_subcarriers:
            return QualityDecision(False, "DEGRADED", "insufficient_valid_subcarriers", 0.0, deltas)
        if deltas["counter_reset"]:
            return QualityDecision(False, "DEGRADED", "receiver_quality_counter_reset", 0.0, deltas)
        if features["packet_loss_ratio"] > self.config.maximum_packet_loss_ratio:
            return QualityDecision(False, "DEGRADED", "measurement_packet_loss_ratio_exceeds_limit", 0.0, deltas)
        invalid_ratio = deltas["invalid"] / max(1, header.sample_count + deltas["invalid"])
        if invalid_ratio > self.config.maximum_invalid_csi_ratio:
            return QualityDecision(False, "DEGRADED", "invalid_csi_ratio_exceeds_limit", 0.0, deltas)
        queue_drop_ratio = deltas["queue_drop"] / max(1, header.sample_count + deltas["queue_drop"])
        if queue_drop_ratio > self.config.maximum_queue_drop_ratio:
            return QualityDecision(False, "DEGRADED", "queue_drop_ratio_exceeds_limit", 0.0, deltas)

        confidence = 1.0
        if not (header.flags & FLAG_GAIN_METADATA_VALID):
            confidence *= 0.85
        if header.flags & FLAG_FIRST_WORD_INVALID_SEEN:
            confidence *= 0.95
        if features["packet_loss_ratio"] > 0.0:
            confidence *= max(0.0, 1.0 - features["packet_loss_ratio"])
        return QualityDecision(True, "READY", "quality_gate_passed", confidence, deltas)


# ---------------------------------------------------------------------------
# Formula: a per-link robust statistical heuristic, not AI and not a calibrated
# physical percentage.  It uses joint sensing evidence; quality/interference
# counters can only degrade/withhold output and cannot raise congestion.
# ---------------------------------------------------------------------------


def _percentile(values: Sequence[float], fraction: float) -> float:
    ordered = sorted(values)
    if len(ordered) == 1:
        return ordered[0]
    position = fraction * (len(ordered) - 1)
    lower = int(math.floor(position))
    upper = int(math.ceil(position))
    if lower == upper:
        return ordered[lower]
    weight = position - lower
    return ordered[lower] * (1.0 - weight) + ordered[upper] * weight


@dataclass(frozen=True)
class FormulaOutput:
    passability_score: Optional[float]
    congestion_index: Optional[float]
    confidence: float
    state: str
    reason: str
    age_ms: int
    display_semantics: str = "uncalibrated_0_to_100_display_score_not_physical_percent"
    confidence_semantics: str = "heuristic_quality_and_baseline_readiness_not_calibrated_probability"


@dataclass(frozen=True)
class NuisanceDecision:
    state: str
    reason: str
    strongest_metric: Optional[str]
    strongest_robust_z: Optional[float]
    confidence_factor: float


class NuisanceDetector:
    """Per-link robust quality baseline; it never supplies human evidence."""

    NAMES = (
        "rssi_mean_dbm",
        "rssi_std_db",
        "noise_floor_mean_dbm",
        "agc_gain_mean",
        "fft_gain_mean",
        "jitter_ms",
    )
    CHAIN_NAMES = ("noise_floor_mean_dbm", "agc_gain_mean", "fft_gain_mean", "jitter_ms")
    # Numerical robust-scale guards in each metric's native unit. These prevent
    # zero MAD from magnifying quantization noise; detection still uses per-link robust-z.
    SCALE_FLOORS = (0.5, 0.1, 0.5, 0.5, 0.5, 0.1)

    def __init__(self, config: AppConfig):
        self.config = config
        self.warmup: Deque[Tuple[float, ...]] = deque(maxlen=config.warmup_windows)
        self.reference: Deque[Tuple[float, ...]] = deque(maxlen=config.baseline_history_windows)
        self.centers: Optional[Tuple[float, ...]] = None
        self.spreads: Optional[Tuple[float, ...]] = None
        self.last_scores: Dict[str, float] = {}
        self.last_deviating_metrics: Tuple[str, ...] = ()
        self.last_decision = NuisanceDecision("WARMING_UP", "baseline_not_ready", None, None, 1.0)

    @classmethod
    def _vector(cls, values: Mapping[str, float]) -> Tuple[float, ...]:
        vector = tuple(float(values[name]) for name in cls.NAMES)
        if not all(math.isfinite(value) for value in vector):
            raise ValueError("nuisance metric contains NaN or infinity")
        return vector

    @classmethod
    def _center_spread(cls, samples: Iterable[Tuple[float, ...]]) -> Tuple[Tuple[float, ...], Tuple[float, ...]]:
        rows = list(samples)
        centers: List[float] = []
        spreads: List[float] = []
        for index, column in enumerate(zip(*rows)):
            center = statistics.median(column)
            mad = statistics.median(abs(value - center) for value in column)
            centers.append(center)
            spreads.append(max(1.4826 * mad, cls.SCALE_FLOORS[index]))
        return tuple(centers), tuple(spreads)

    def learn_empty_baseline(self, values: Mapping[str, float]) -> None:
        if self.centers is not None:
            return
        self.warmup.append(self._vector(values))
        if len(self.warmup) >= self.config.warmup_windows:
            self.reference.extend(self.warmup)
            self.centers, self.spreads = self._center_spread(self.warmup)
            self.last_decision = NuisanceDecision("READY", "robust_empty_baseline_ready", None, None, 1.0)

    def evaluate(self, values: Mapping[str, float]) -> NuisanceDecision:
        if self.centers is None or self.spreads is None:
            self.last_decision = NuisanceDecision("WARMING_UP", "baseline_not_ready", None, None, 1.0)
            return self.last_decision
        vector = self._vector(values)
        scores = [abs(value - center) / spread for value, center, spread in zip(vector, self.centers, self.spreads)]
        self.last_scores = {name: round(score, 4) for name, score in zip(self.NAMES, scores)}
        chain_indices = [self.NAMES.index(name) for name in self.CHAIN_NAMES]
        deviating = tuple(
            self.NAMES[index]
            for index in chain_indices
            if scores[index] >= self.config.nuisance_suspect_robust_z
        )
        self.last_deviating_metrics = deviating
        index = max(chain_indices, key=scores.__getitem__)
        strongest = scores[index]
        if len(deviating) >= 2:
            self.last_decision = NuisanceDecision(
                "INTERFERENCE_SUSPECTED",
                "coordinated_receive_chain_deviation_from_link_baseline",
                self.NAMES[index],
                round(strongest, 4),
                self.config.nuisance_confidence_factor,
            )
        else:
            reason = (
                "single_receive_chain_deviation_not_sufficient"
                if len(deviating) == 1 else "receive_chain_within_link_robust_baseline"
            )
            self.last_decision = NuisanceDecision("READY", reason, self.NAMES[index], round(strongest, 4), 1.0)
        return self.last_decision

    def update_quiet_baseline(self, values: Mapping[str, float]) -> None:
        if self.centers is None:
            return
        self.reference.append(self._vector(values))
        self.centers, self.spreads = self._center_spread(self.reference)

    def diagnostics(self) -> Mapping[str, Any]:
        return {
            "state": self.last_decision.state,
            "reason": self.last_decision.reason,
            "strongest_metric": self.last_decision.strongest_metric,
            "strongest_robust_z": self.last_decision.strongest_robust_z,
            "robust_z_policy": self.config.nuisance_suspect_robust_z,
            "confidence_factor": self.last_decision.confidence_factor,
            "deviating_receive_chain_metrics": list(self.last_deviating_metrics),
            "robust_z_by_metric": dict(self.last_scores),
            "baseline_count": len(self.warmup),
            "reference_count": len(self.reference),
            "baseline_ready": self.centers is not None,
            "centers": dict(zip(self.NAMES, self.centers)) if self.centers is not None else None,
            "robust_spreads": dict(zip(self.NAMES, self.spreads)) if self.spreads is not None else None,
        }


class FormulaState:
    VECTOR_NAMES = ("delta_amp", "std_dev", "spectral_roughness")

    def __init__(self, config: AppConfig, reset_reason: str = "link_created"):
        self.config = config
        self.warmup: Deque[Tuple[float, float, float]] = deque(maxlen=config.warmup_windows)
        self.reference: Deque[Tuple[float, float, float]] = deque(
            maxlen=config.baseline_history_windows
        )
        self.centers: Optional[Tuple[float, float, float]] = None
        self.spreads: Optional[Tuple[float, float, float]] = None
        self.evidence_history: Deque[Tuple[int, float]] = deque()
        self.quiet_streak = 0
        self.elevated_since_ns: Optional[int] = None
        self.last_update_ns: Optional[int] = None
        self.baseline_ready_ns: Optional[int] = None
        self.last_baseline_update_ns: Optional[int] = None
        self.last_gap_ns: Optional[int] = None
        self.last_reset_reason = reset_reason
        self.last_reset_time_utc = datetime.now(timezone.utc).isoformat()
        self.smoothed_congestion = 0.0
        self.baseline_operator_confirmed = False
        self.nuisance = NuisanceDetector(config)
        self.last_valid_output: Optional[FormulaOutput] = None

    def confirm_operator_baseline(self) -> None:
        self.baseline_operator_confirmed = True

    @staticmethod
    def _vector(features: Mapping[str, Any]) -> Tuple[float, float, float]:
        return tuple(float(features[name]) for name in FormulaState.VECTOR_NAMES)  # type: ignore[return-value]

    @staticmethod
    def _center_spread(
        samples: Iterable[Tuple[float, float, float]]
    ) -> Tuple[Tuple[float, float, float], Tuple[float, float, float]]:
        rows = list(samples)
        columns = list(zip(*rows))
        centers: List[float] = []
        spreads: List[float] = []
        for column in columns:
            center = statistics.median(column)
            mad = statistics.median(abs(value - center) for value in column)
            numeric_floor = max(1e-7, abs(center) * 1e-3)
            centers.append(center)
            spreads.append(max(1.4826 * mad, numeric_floor))
        return tuple(centers), tuple(spreads)  # type: ignore[return-value]

    def _warmup_is_stable(self) -> bool:
        assert self.centers is not None and self.spreads is not None
        for index in range(3):
            column = [row[index] for row in self.warmup]
            robust_width = _percentile(column, 0.75) - _percentile(column, 0.25)
            denominator = max(abs(self.centers[index]), self.spreads[index], 1e-7)
            if robust_width / denominator > self.config.warmup_relative_spread_limit:
                return False
        return True

    def pause(self, now_ns: int) -> None:
        """Exclude rejected/stale time from evidence persistence and smoothing."""
        self.evidence_history.clear()
        self.elevated_since_ns = None
        self.quiet_streak = 0
        self.last_update_ns = now_ns
        self.last_gap_ns = now_ns

    def hold(self, now_ns: int) -> None:
        """Freeze output without erasing prior sensing evidence."""
        self.quiet_streak = 0
        self.last_update_ns = now_ns
        self.last_gap_ns = now_ns

    def diagnostics(self, now_ns: int) -> Mapping[str, Any]:
        centers = None
        spreads = None
        if self.centers is not None and self.spreads is not None:
            centers = dict(zip(self.VECTOR_NAMES, self.centers))
            spreads = dict(zip(self.VECTOR_NAMES, self.spreads))
        return {
            "formula_version": FORMULA_VERSION,
            "baseline_state": (
                "READY" if centers is not None else
                ("WARMING_UP" if self.baseline_operator_confirmed else "WAITING_FOR_BASELINE")
            ),
            "baseline_operator_confirmed": self.baseline_operator_confirmed,
            "centers": centers,
            "robust_spreads": spreads,
            "warmup_count": len(self.warmup),
            "reference_count": len(self.reference),
            "baseline_age_ms": (
                int((now_ns - self.baseline_ready_ns) / 1_000_000)
                if self.baseline_ready_ns is not None
                else None
            ),
            "last_baseline_update_age_ms": (
                int((now_ns - self.last_baseline_update_ns) / 1_000_000)
                if self.last_baseline_update_ns is not None
                else None
            ),
            "evidence_window_count": len(self.evidence_history),
            "last_gap_age_ms": (
                int((now_ns - self.last_gap_ns) / 1_000_000)
                if self.last_gap_ns is not None
                else None
            ),
            "last_reset_reason": self.last_reset_reason,
            "last_reset_time_utc": self.last_reset_time_utc,
            "nuisance": self.nuisance.diagnostics(),
        }

    def update(
        self, features: Mapping[str, Any], quality: QualityDecision, now_ns: int,
        nuisance_values: Mapping[str, float],
    ) -> FormulaOutput:
        if not self.baseline_operator_confirmed:
            return FormulaOutput(
                None, None, 0.0, "WAITING_FOR_BASELINE",
                "operator_confirmation_required_before_baseline", 0,
            )
        if not quality.accepted:
            # Do not let an outage or rejected window count as persistent
            # sensing evidence when good data resumes.
            self.pause(now_ns)
            return FormulaOutput(None, None, 0.0, quality.state, quality.reason, 0)

        vector = self._vector(features)
        if self.centers is None or self.spreads is None:
            self.warmup.append(vector)
            self.nuisance.learn_empty_baseline(nuisance_values)
            if len(self.warmup) < self.config.warmup_windows:
                confidence = len(self.warmup) / self.config.warmup_windows
                return FormulaOutput(
                    None,
                    None,
                    confidence,
                    "WARMING_UP",
                    f"quality_windows={len(self.warmup)}/{self.config.warmup_windows}",
                    0,
                )
            self.centers, self.spreads = self._center_spread(self.warmup)
            if not self._warmup_is_stable():
                self.centers = None
                self.spreads = None
                self.warmup.popleft()
                self.nuisance.warmup.popleft()
                self.nuisance.reference.clear()
                self.nuisance.centers = None
                self.nuisance.spreads = None
                return FormulaOutput(
                    None,
                    None,
                    0.25,
                    "WARMING_UP",
                    "warmup_reference_not_statistically_stable",
                    0,
                )
            self.reference.extend(self.warmup)
            self.last_update_ns = now_ns
            self.baseline_ready_ns = now_ns
            self.last_baseline_update_ns = now_ns
            output = FormulaOutput(
                100.0,
                0.0,
                0.5 * quality.confidence_factor,
                "READY",
                "robust_reference_ready_operator_confirmed_not_ground_truth",
                0,
            )
            self.last_valid_output = output
            return output

        positive_z = [
            max(0.0, (value - center) / spread)
            for value, center, spread in zip(vector, self.centers, self.spreads)
        ]
        # Delta must agree with at least one temporal/subcarrier variation feature.
        # A quality anomaly alone never enters this sensing-evidence expression.
        joint_evidence = min(positive_z[0], max(positive_z[1], positive_z[2]))
        nuisance = self.nuisance.evaluate(nuisance_values)
        if nuisance.state == "INTERFERENCE_SUSPECTED":
            self.hold(now_ns)
            state = "AMBIGUOUS" if joint_evidence > self.config.evidence_gate_z else "INTERFERENCE_SUSPECTED"
            reason = (
                "formula_frozen_ambiguous_sensing_and_nuisance_evidence"
                if state == "AMBIGUOUS" else "formula_frozen_nuisance_evidence_without_human_conclusion"
            )
            frozen = self.last_valid_output
            confidence = quality.confidence_factor * nuisance.confidence_factor
            if frozen is not None:
                confidence *= frozen.confidence
            return FormulaOutput(
                frozen.passability_score if frozen is not None else None,
                frozen.congestion_index if frozen is not None else None,
                round(min(1.0, max(0.0, confidence)), 4), state, reason, 0,
            )
        self.evidence_history.append((now_ns, joint_evidence))
        horizon_ns = self.config.evidence_persistence_ms * 1_000_000
        while self.evidence_history and now_ns - self.evidence_history[0][0] > horizon_ns:
            self.evidence_history.popleft()
        persistent_evidence = statistics.median(value for _, value in self.evidence_history)

        if persistent_evidence <= self.config.evidence_gate_z:
            target = 0.0
        else:
            excess = persistent_evidence - self.config.evidence_gate_z
            target = 100.0 * (1.0 - math.exp(-excess / self.config.evidence_scale_z))

        if self.last_update_ns is None:
            dt_ms = float(self.config.formula_attack_ms)
        else:
            dt_ms = max(0.0, (now_ns - self.last_update_ns) / 1_000_000.0)
        tau_ms = self.config.formula_attack_ms if target > self.smoothed_congestion else self.config.formula_release_ms
        alpha = 1.0 - math.exp(-dt_ms / max(1.0, float(tau_ms)))
        self.smoothed_congestion += alpha * (target - self.smoothed_congestion)
        self.smoothed_congestion = min(100.0, max(0.0, self.smoothed_congestion))
        self.last_update_ns = now_ns

        if persistent_evidence <= self.config.baseline_update_max_z:
            self.quiet_streak += 1
            self.elevated_since_ns = None
            if self.quiet_streak >= self.config.baseline_update_streak:
                self.reference.append(vector)
                self.centers, self.spreads = self._center_spread(self.reference)
                self.last_baseline_update_ns = now_ns
                if self.quiet_streak % self.config.baseline_update_streak == 0:
                    self.nuisance.update_quiet_baseline(nuisance_values)
        else:
            self.quiet_streak = 0
            if self.elevated_since_ns is None:
                self.elevated_since_ns = now_ns

        state = "READY"
        reason = "joint_relative_sensing_evidence"
        confidence = quality.confidence_factor * min(1.0, len(self.reference) / self.config.warmup_windows)
        if self.elevated_since_ns is not None:
            elevated_ms = (now_ns - self.elevated_since_ns) / 1_000_000.0
            if elevated_ms >= self.config.persistent_shift_ms:
                state = "DEGRADED"
                reason = "persistent_shift_baseline_frozen_manual_context_required"
                confidence *= 0.5

        congestion = round(self.smoothed_congestion, 3)
        output = FormulaOutput(
            round(100.0 - congestion, 3),
            congestion,
            round(min(1.0, max(0.0, confidence)), 4),
            state,
            reason,
            0,
        )
        self.last_valid_output = output
        return output


# ---------------------------------------------------------------------------
# Optional real LightGBM inference.  Model metadata is mandatory and binds the
# model to feature_schema_version=2 and an ordered feature list.  No heuristic
# result is substituted when a model is absent or incompatible.
# ---------------------------------------------------------------------------


MODEL_FEATURE_NAMES = FEATURE_FLOAT_NAMES + (
    "sample_count",
    "window_duration_ms",
    "invalid_csi_delta",
    "wrong_source_delta",
    "queue_drop_delta",
    "packet_loss_delta",
    "feature_packet_loss_delta",
    "gain_metadata_valid",
    "first_word_invalid_seen",
    "system_unavailable",
    "channel_filter_enabled",
)


def _ai_result(
    state: str,
    reason: str,
    model_version: Optional[str] = None,
    value: Any = None,
    raw_score: Any = None,
    confidence: Optional[float] = None,
    calibration_state: str = "NOT_AVAILABLE",
    age_ms: int = 0,
) -> Dict[str, Any]:
    return {
        "state": state,
        "reason": reason,
        "value": value,
        "raw_score": raw_score,
        "confidence": confidence,
        "model_version": model_version,
        "calibration_state": calibration_state,
        "age_ms": age_ms,
    }


class LightGBMModel:
    def __init__(self, task: str, model_path: Optional[Path], metadata_path: Optional[Path]):
        self.task = task
        self.model_path = model_path
        self.metadata_path = metadata_path
        self.booster: Any = None
        self.metadata: Dict[str, Any] = {}
        self.load_state = "NOT_READY"
        self.load_reason = "model_path_not_configured"
        self.model_version: Optional[str] = None
        self._load()

    def _load(self) -> None:
        if self.model_path is None:
            return
        if not self.model_path.is_file():
            self.load_reason = f"model_file_missing:{self.model_path}"
            return
        if self.metadata_path is None or not self.metadata_path.is_file():
            self.load_state = "INCOMPATIBLE"
            self.load_reason = "model_metadata_missing"
            return
        try:
            with self.metadata_path.open("r", encoding="utf-8") as handle:
                metadata = json.load(handle)
        except (OSError, json.JSONDecodeError) as exc:
            LOG.error("%s metadata read failed: %s", self.task, exc)
            self.load_state = "INCOMPATIBLE"
            self.load_reason = "model_metadata_unreadable"
            return
        if metadata.get("feature_schema_version") != FEATURE_SCHEMA_VERSION:
            self.load_state = "INCOMPATIBLE"
            self.load_reason = "feature_schema_mismatch"
            return
        if metadata.get("task") != self.task:
            self.load_state = "INCOMPATIBLE"
            self.load_reason = "model_task_mismatch"
            return
        model_version = metadata.get("model_version")
        if not isinstance(model_version, str) or not model_version.strip():
            self.load_state = "INCOMPATIBLE"
            self.load_reason = "model_version_missing"
            return
        feature_names = metadata.get("feature_names")
        if not isinstance(feature_names, list) or not feature_names or not all(
            isinstance(name, str) and name in MODEL_FEATURE_NAMES for name in feature_names
        ):
            self.load_state = "INCOMPATIBLE"
            self.load_reason = "invalid_feature_name_list"
            return
        if len(set(feature_names)) != len(feature_names):
            self.load_state = "INCOMPATIBLE"
            self.load_reason = "duplicate_feature_names"
            return
        if (
            ("agc_gain_mean" in feature_names or "fft_gain_mean" in feature_names)
            and "gain_metadata_valid" not in feature_names
        ):
            self.load_state = "INCOMPATIBLE"
            self.load_reason = "gain_features_require_validity_feature"
            return
        try:
            import lightgbm as lgb  # Imported only when a real model is configured.
        except (ImportError, OSError, RuntimeError) as exc:
            LOG.error("%s LightGBM dependency unavailable: %s", self.task, exc)
            self.load_state = "NOT_READY"
            self.load_reason = "lightgbm_dependency_unavailable"
            return
        try:
            booster = lgb.Booster(model_file=str(self.model_path))
            if booster.num_feature() != len(feature_names):
                raise ValueError(
                    f"model has {booster.num_feature()} features, metadata has {len(feature_names)}"
                )
            model_feature_names = list(booster.feature_name())
            generic_names = all(
                name.startswith("Column_") or name.startswith("feature_")
                for name in model_feature_names
            )
            if model_feature_names and not generic_names and model_feature_names != feature_names:
                raise ValueError("model feature order/names differ from metadata")
        except Exception as exc:
            # LightGBM raises a package-specific exception type.  This boundary
            # is model-local, logs the cause, and must not stop Formula.
            LOG.error("%s model load failed: %s", self.task, exc)
            self.load_state = "INCOMPATIBLE"
            self.load_reason = "model_load_or_feature_count_failed"
            return
        self.booster = booster
        self.metadata = metadata
        self.model_version = model_version
        self.load_state = "READY"
        self.load_reason = "model_loaded"
        LOG.info("%s loaded model_version=%s schema=%d", self.task, self.model_version, FEATURE_SCHEMA_VERSION)

    def manifest(self) -> Mapping[str, Any]:
        return {
            "state": self.load_state,
            "reason": self.load_reason,
            "model_version": self.model_version,
            "model_path": str(self.model_path) if self.model_path else None,
        }

    def infer(self, feature_map: Mapping[str, float], quality: QualityDecision) -> Mapping[str, Any]:
        if self.load_state != "READY" or self.booster is None:
            return _ai_result(self.load_state, self.load_reason, self.model_version)
        if not quality.accepted and self.task != "AI-Q":
            return _ai_result("DEGRADED", f"quality_gate:{quality.reason}", self.model_version)

        names: List[str] = self.metadata["feature_names"]
        vector = [float(feature_map[name]) for name in names]
        if not all(math.isfinite(value) for value in vector):
            return _ai_result("UNKNOWN", "model_input_non_finite", self.model_version)

        bounds = self.metadata.get("ood_bounds")
        bounds_available = isinstance(bounds, dict) and all(
            name in bounds
            and isinstance(bounds[name], list)
            and len(bounds[name]) == 2
            and all(isinstance(value, (int, float)) and math.isfinite(float(value)) for value in bounds[name])
            for name in names
        )
        if bounds_available:
            invalid_bounds = [
                name for name in names if float(bounds[name][0]) > float(bounds[name][1])
            ]
            if invalid_bounds:
                return _ai_result(
                    "INCOMPATIBLE",
                    "invalid_ood_bounds:" + ",".join(invalid_bounds),
                    self.model_version,
                )
            outside = [
                name
                for name, value in zip(names, vector)
                if value < float(bounds[name][0]) or value > float(bounds[name][1])
            ]
            if outside:
                return _ai_result(
                    "OOD", "outside_metadata_bounds:" + ",".join(outside), self.model_version
                )

        try:
            prediction = self.booster.predict([vector])
            first = prediction[0]
            if hasattr(first, "tolist"):
                first = first.tolist()
            if isinstance(first, (list, tuple)):
                scores = [float(value) for value in first]
                if not scores or not all(math.isfinite(value) for value in scores):
                    raise ValueError("non-finite multiclass output")
                if self.metadata.get("output_type") != "multiclass_probability":
                    return _ai_result(
                        "INCOMPATIBLE",
                        "multiclass_output_type_missing",
                        self.model_version,
                    )
                if (
                    not all(0.0 <= score <= 1.0 for score in scores)
                    or abs(sum(scores) - 1.0) > 1e-3
                ):
                    return _ai_result(
                        "UNKNOWN",
                        "multiclass_probabilities_invalid",
                        self.model_version,
                        raw_score=scores,
                    )
                labels = self.metadata.get("class_labels")
                if (
                    not isinstance(labels, list)
                    or len(labels) != len(scores)
                    or not all(isinstance(label, str) for label in labels)
                    or len(set(labels)) != len(labels)
                ):
                    return _ai_result(
                        "INCOMPATIBLE", "class_labels_invalid", self.model_version
                    )
                best = max(range(len(scores)), key=scores.__getitem__)
                value: Any = labels[best]
                raw_score: Any = dict(zip(labels, scores))
                probability = scores[best]
            else:
                score = float(first)
                if not math.isfinite(score):
                    raise ValueError("non-finite model output")
                raw_score = score
                probability = score
                output_type = self.metadata.get("output_type")
                if output_type == "binary_probability":
                    if not 0.0 <= score <= 1.0:
                        return _ai_result(
                            "UNKNOWN", "binary_probability_outside_unit_interval", self.model_version,
                            raw_score=score,
                        )
                    threshold = self.metadata.get("decision_threshold")
                    labels = self.metadata.get("class_labels")
                    if (
                        not isinstance(threshold, (int, float))
                        or not math.isfinite(float(threshold))
                        or not 0.0 <= float(threshold) <= 1.0
                        or not isinstance(labels, list)
                        or len(labels) != 2
                        or not all(isinstance(label, str) for label in labels)
                        or len(set(labels)) != 2
                    ):
                        return _ai_result(
                            "INCOMPATIBLE",
                            "validated_decision_threshold_or_labels_missing",
                            self.model_version,
                            raw_score=score,
                        )
                    positive = score >= float(threshold)
                    value = labels[1] if positive else labels[0]
                    probability = score if positive else 1.0 - score
                elif output_type == "regression":
                    value = score
                    probability = math.nan
                else:
                    return _ai_result(
                        "INCOMPATIBLE", "unsupported_output_type", self.model_version, raw_score=score
                    )
        except (RuntimeError, ValueError, TypeError, IndexError) as exc:
            LOG.error("%s inference failed for model %s: %s", self.task, self.model_version, exc)
            return _ai_result("UNKNOWN", "model_inference_error", self.model_version)

        calibrated = self.metadata.get("calibrated") is True
        calibration_method = self.metadata.get("calibration_method")
        confidence: Optional[float] = None
        calibration_state = "UNCALIBRATED"
        if (
            calibrated
            and isinstance(calibration_method, str)
            and calibration_method.strip()
            and math.isfinite(probability)
        ):
            confidence = min(1.0, max(0.0, probability))
            calibration_state = calibration_method
        elif calibrated:
            calibration_state = "CALIBRATION_METADATA_INCOMPLETE"
        state = "READY"
        reason = "model_inference"
        if not bounds_available:
            state = "UNKNOWN"
            reason = "prediction_available_but_ood_bounds_missing"
        if not quality.accepted:
            state = "DEGRADED"
            reason = f"quality_gate:{quality.reason}"
        return _ai_result(
            state,
            reason,
            self.model_version,
            value=value,
            raw_score=raw_score,
            confidence=confidence,
            calibration_state=calibration_state,
        )


class LightGBMBundle:
    def __init__(self, config: AppConfig):
        self.models: Dict[str, LightGBMModel] = {}
        for task in ("AI-Q", "AI-P", "AI-C"):
            try:
                self.models[task] = LightGBMModel(task, *config.model_paths[task])
            except Exception as exc:
                # A model-specific dependency/runtime failure must not stop Formula.
                LOG.exception("%s model initialization failed independently: %s", task, exc)
                fallback = LightGBMModel(task, None, None)
                fallback.load_state = "NOT_READY"
                fallback.load_reason = "model_initialization_error"
                self.models[task] = fallback

    def manifest(self) -> Mapping[str, Any]:
        return {task: model.manifest() for task, model in self.models.items()}

    def infer(self, features: Mapping[str, float], quality: QualityDecision) -> Mapping[str, Any]:
        results: Dict[str, Any] = {}
        for task, model in self.models.items():
            try:
                results[task] = model.infer(features, quality)
            except Exception as exc:
                LOG.exception("%s inference path failed independently: %s", task, exc)
                results[task] = _ai_result("UNKNOWN", "inference_path_error", model.model_version)
        return results


# ---------------------------------------------------------------------------
# Non-blocking capture recorder.  Active files end in .part and are finalized
# only on a clean shutdown.  Completed directories are never overwritten.
# ---------------------------------------------------------------------------


@dataclass
class CaptureBarrier:
    link_id: int
    capture_session_id: str
    completed: threading.Event = field(default_factory=threading.Event)


@dataclass
class CaptureSession:
    directory: Path
    capture_session_id: str
    metadata: Dict[str, Any]
    capture_part: Path
    capture_handle: Any
    manifest: Dict[str, Any]
    sample_count: int = 0
    snapshot_count: int = 0
    drop_count: int = 0
    failed: bool = False
    accepting: bool = True
    io_lock: Any = field(default_factory=threading.Lock, repr=False)
    stop_lock: Any = field(default_factory=threading.Lock, repr=False)
    force_incomplete_requested: bool = False
    final_result: Optional[Dict[str, Any]] = None
    barrier_timeout_seen: bool = False


class CaptureRecorder:
    def __init__(self, config: AppConfig, model_manifest: Mapping[str, Any]):
        self.config = config
        self.model_manifest = model_manifest
        self.queue: "queue.Queue[Any]" = queue.Queue(
            maxsize=config.recorder_queue_depth
        )
        self.stop_event = threading.Event()
        self.thread: Optional[threading.Thread] = None
        self.sessions: Dict[int, CaptureSession] = {}
        self.audit_handles: Dict[int, Dict[str, Any]] = {}
        self.drop_count = 0
        self.stats_lock = threading.Lock()
        self.lifecycle_lock = threading.Lock()
        self.accepting = False
        self.clean_shutdown = False
        self.barrier_timeout_s = 5.0

    def start(self) -> None:
        if not self.config.recording_enabled:
            LOG.info("recorder disabled; set WIEVAC_RECORDING=1 for versioned .part capture")
            return
        if self.config.corridor_id == "UNCONFIGURED":
            raise ValueError("WIEVAC_CORRIDOR_ID must be configured before recording")
        self.thread = threading.Thread(target=self._worker, name="capture-recorder", daemon=True)
        self.thread.start()
        with self.lifecycle_lock:
            self.accepting = True
        LOG.info(
            "recorder active root=%s corridor=%s main_session=%s",
            self.config.capture_root,
            self.config.corridor_id,
            self.config.session_id,
        )

    def _directory(self, link_id: int) -> Path:
        directory = (self.config.capture_root / self.config.pi_id / self.config.corridor_id
                     / str(link_id) / self.config.session_id).resolve()
        try:
            directory.relative_to(self.config.capture_root)
        except ValueError as exc:
            raise ValueError("capture session path escapes WIEVAC_CAPTURE_ROOT") from exc
        return directory

    def _write_json(self, path: Path, value: Mapping[str, Any]) -> None:
        temporary = path.with_name(path.name + ".tmp")
        try:
            with temporary.open("w", encoding="utf-8") as handle:
                json.dump(value, handle, ensure_ascii=False, indent=2, sort_keys=True)
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(temporary, path)
        finally:
            if temporary.exists():
                temporary.unlink()

    def start_capture(self, link_id: int, tx_id: int, rx_id: int, metadata: Mapping[str, Any]) -> Dict[str, Any]:
        """Open one explicitly requested real-data capture; never infer its label."""
        with self.lifecycle_lock:
            if link_id in self.sessions:
                raise ValueError("capture session already active for this link")
            capture_session_id = "cap-" + datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ")
            label = "empty" if metadata["person_count_reference"] == 0 else "people_%d" % metadata["person_count_reference"]
            directory = self._directory(link_id)
            directory.mkdir(parents=True, exist_ok=True)
            captures = directory / "captures"
            logs = directory / "logs"
            captures.mkdir(exist_ok=True)
            logs.mkdir(exist_ok=True)
            capture_part = captures / ("real_%s_%s.jsonl.part" % (label, capture_session_id))
            manifest_part = directory / "manifest.json.part"
            if manifest_part.exists():
                with manifest_part.open("r", encoding="utf-8") as handle:
                    manifest = json.load(handle)
            else:
                manifest = {
                    "data_origin": "real", "capture_purpose": "simulator_calibration",
                    "main_session_id": self.config.session_id,
                    "started_at_utc": datetime.now(timezone.utc).isoformat(),
                    "pi_id": self.config.pi_id, "corridor_id": self.config.corridor_id,
                    "link_id": link_id, "protocol_version": PROTOCOL_VERSION,
                    "feature_schema_version": FEATURE_SCHEMA_VERSION,
                    "formula_version": FORMULA_VERSION, "models": self.model_manifest,
                    "formula_is_ground_truth": False, "status": "recording", "capture_sessions": [],
                }
            entry = {
                **dict(metadata), "capture_session_id": capture_session_id,
                "file": str(capture_part.relative_to(directory)),
                "started_at_utc": datetime.now(timezone.utc).isoformat(),
                "ended_at_utc": None, "sample_count": 0, "snapshot_count": 0,
                "lost_sample_count": 0, "status": "recording",
            }
            capture_handle = None
            audit_handles_created = False
            try:
                capture_handle = capture_part.open("x", encoding="utf-8", buffering=1)
                audit_handles_created = self._ensure_audits(link_id, directory)
                manifest["capture_sessions"].append(entry)
                self._write_json(manifest_part, manifest)
                session = CaptureSession(
                    directory, capture_session_id, dict(metadata), capture_part,
                    capture_handle, manifest,
                )
                self.sessions[link_id] = session
            except (OSError, ValueError, KeyError, TypeError, json.JSONDecodeError):
                if audit_handles_created:
                    handles = self.audit_handles.pop(link_id, {})
                    for handle in handles.values():
                        if not handle.closed:
                            handle.close()
                if capture_handle is not None and not capture_handle.closed:
                    capture_handle.close()
                if capture_part.exists():
                    capture_part.unlink()
                raise
        LOG.info("capture started link=%d capture_session_id=%s", link_id, capture_session_id)
        return {"capture_session_id": capture_session_id, "file": str(capture_part)}

    def _ensure_audits(self, link_id: int, directory: Path) -> bool:
        if link_id in self.audit_handles:
            return False
        logs = directory / "logs"
        formula_handle = None
        ai_handle = None
        try:
            formula_handle = (logs / "formula_audit.jsonl.part").open("a", encoding="utf-8", buffering=1)
            ai_handle = (logs / "ai_audit.jsonl.part").open("a", encoding="utf-8", buffering=1)
            self.audit_handles[link_id] = {"formula": formula_handle, "ai": ai_handle}
            return True
        except OSError:
            if formula_handle is not None:
                formula_handle.close()
            if ai_handle is not None:
                ai_handle.close()
            raise

    def stop_capture(
        self, link_id: int, reason: str = "operator_requested", force_incomplete: bool = False
    ) -> Optional[Dict[str, Any]]:
        with self.lifecycle_lock:
            session = self.sessions.get(link_id)
            if session is None:
                return None
            if force_incomplete:
                session.force_incomplete_requested = True

        with session.stop_lock:
            with self.lifecycle_lock:
                current = self.sessions.get(link_id)
                if current is not session:
                    return dict(session.final_result) if session.final_result is not None else None
                if session.final_result is not None:
                    return dict(session.final_result)
                if force_incomplete:
                    session.force_incomplete_requested = True
                session.accepting = False

            barrier = CaptureBarrier(link_id, session.capture_session_id)
            try:
                self.queue.put(barrier, timeout=self.barrier_timeout_s)
            except queue.Full:
                session.failed = True
                barrier = None
            barrier_completed = barrier is not None and barrier.completed.wait(self.barrier_timeout_s)
            if not barrier_completed:
                session.failed = True
                session.barrier_timeout_seen = True
                for entry in session.manifest["capture_sessions"]:
                    if entry["capture_session_id"] == session.capture_session_id:
                        entry.update({
                            "status": "incomplete", "stop_reason": reason,
                            "incomplete_forced": True, "barrier_completed": False,
                            "lost_sample_count": session.drop_count,
                        })
                        break
                try:
                    self._write_json(session.directory / "manifest.json.part", session.manifest)
                except OSError as exc:
                    LOG.error("could not persist barrier timeout for capture %s: %s", session.capture_session_id, exc)
                return {
                    "capture_session_id": session.capture_session_id,
                    "status": "incomplete", "barrier_completed": False,
                }

            with session.io_lock:
                try:
                    session.capture_handle.flush()
                    os.fsync(session.capture_handle.fileno())
                except OSError as exc:
                    session.failed = True
                    LOG.error("capture final flush failed link=%d: %s", link_id, exc)
                try:
                    session.capture_handle.close()
                except OSError as exc:
                    session.failed = True
                    LOG.error("capture close failed link=%d: %s", link_id, exc)

                with self.lifecycle_lock:
                    forced = force_incomplete or session.force_incomplete_requested
                incomplete = forced or session.barrier_timeout_seen or session.failed or session.drop_count > 0
                status = "incomplete" if incomplete else ("empty_no_samples" if session.sample_count == 0 else "complete")
                manifest_part = session.directory / "manifest.json.part"
                for entry in session.manifest["capture_sessions"]:
                    if entry["capture_session_id"] == session.capture_session_id:
                        entry.update({"ended_at_utc": datetime.now(timezone.utc).isoformat(), "sample_count": session.sample_count,
                                      "snapshot_count": session.snapshot_count, "lost_sample_count": session.drop_count,
                                      "status": status, "stop_reason": reason,
                                      "incomplete_forced": forced or session.barrier_timeout_seen,
                                      "barrier_completed": True})
                        break
                manifest_persisted = True
                try:
                    self._write_json(manifest_part, session.manifest)
                except OSError as exc:
                    manifest_persisted = False
                    status = "incomplete"
                    session.failed = True
                    LOG.error("capture final manifest update failed link=%d: %s", link_id, exc)
                if status == "complete" and manifest_persisted:
                    try:
                        os.replace(session.capture_part, session.capture_part.with_suffix(""))
                    except OSError as exc:
                        status = "incomplete"
                        session.failed = True
                        LOG.error("capture final rename failed link=%d: %s", link_id, exc)
                        for entry in session.manifest["capture_sessions"]:
                            if entry["capture_session_id"] == session.capture_session_id:
                                entry["status"] = status
                                break
                        try:
                            self._write_json(manifest_part, session.manifest)
                        except OSError as manifest_exc:
                            LOG.error("could not persist rename failure link=%d: %s", link_id, manifest_exc)

            result = {"capture_session_id": session.capture_session_id, "status": status,
                      "barrier_completed": True}
            with self.lifecycle_lock:
                session.final_result = dict(result)
                if self.sessions.get(link_id) is session:
                    self.sessions.pop(link_id)
            LOG.info("capture stopped link=%d capture_session_id=%s status=%s", link_id, session.capture_session_id, status)
            return dict(result)

    def capture_status(self, link_id: int) -> Optional[Dict[str, Any]]:
        session = self.sessions.get(link_id)
        if session is None:
            return None
        return {"capture_session_id": session.capture_session_id, "file": str(session.capture_part),
                "sample_count": session.sample_count, "snapshot_count": session.snapshot_count,
                "started_at_utc": session.manifest["capture_sessions"][-1]["started_at_utc"],
                "status": "recording" if session.accepting else "stopping"}

    def submit(self, record: Mapping[str, Any]) -> None:
        if not self.config.recording_enabled:
            return
        with self.lifecycle_lock:
            session = self.sessions.get(int(record["link_id"]))
            if not self.accepting or session is None or not session.accepting:
                return
            enriched = dict(record)
            enriched.update({"data_origin": "real", "capture_purpose": "simulator_calibration",
                             "main_session_id": self.config.session_id, "capture_session_id": session.capture_session_id,
                             "person_count_reference": session.metadata["person_count_reference"],
                             "movement_state": session.metadata["movement_state"], "arrangement": session.metadata["arrangement"],
                             "operator_note": session.metadata["operator_note"], "passability_ground_truth": None,
                             "capture_started_at_utc": session.manifest["capture_sessions"][-1]["started_at_utc"],
                             "pi_id": self.config.pi_id, "corridor_id": self.config.corridor_id})
            try:
                self.queue.put_nowait(enriched)
            except queue.Full:
                with self.stats_lock:
                    self.drop_count += 1
                    drop_count = self.drop_count
                session.drop_count += 1
                LOG.error("recorder queue full; dropped record count=%d", drop_count)
                session.failed = True

    def audit(self, link_id: int, formula_entry: Mapping[str, Any], ai_entries: Iterable[Mapping[str, Any]]) -> None:
        session = self.sessions.get(link_id)
        if session is None:
            return
        try:
            with session.io_lock:
                handles = self.audit_handles[link_id]
                handles["formula"].write(json.dumps(formula_entry, ensure_ascii=False, sort_keys=True) + "\n")
                for entry in ai_entries:
                    handles["ai"].write(json.dumps(entry, ensure_ascii=False, sort_keys=True) + "\n")
        except (OSError, TypeError, ValueError) as exc:
            session.failed = True
            LOG.error("audit write failed link=%d: %s", link_id, exc)

    def _write(self, record: Mapping[str, Any]) -> None:
        link_id = int(record["link_id"])
        session = self.sessions.get(link_id)
        capture_session_id = str(record.get("capture_session_id", ""))
        if session is None or session.capture_session_id != capture_session_id:
            with self.stats_lock:
                self.drop_count += 1
                mismatch_count = self.drop_count
            if session is not None:
                session.failed = True
            LOG.error(
                "recorder session mismatch link=%d record_capture=%s active_capture=%s diagnostic_drop_count=%d",
                link_id, capture_session_id,
                session.capture_session_id if session is not None else "none",
                mismatch_count,
            )
            return
        try:
            with session.io_lock:
                session.capture_handle.write(json.dumps(record, ensure_ascii=False, sort_keys=True) + "\n")
                session.sample_count += 1
                if record.get("record_type") == "csi_snapshot":
                    session.snapshot_count += 1
        except (OSError, TypeError, ValueError) as exc:
            session.failed = True
            LOG.error("record write failed link=%d path=%s: %s", link_id, session.capture_part, exc)

    def _worker(self) -> None:
        while True:
            record = self.queue.get()
            try:
                if record is None:
                    return
                if isinstance(record, CaptureBarrier):
                    session = self.sessions.get(record.link_id)
                    if session is None or session.capture_session_id != record.capture_session_id:
                        LOG.error("recorder barrier session mismatch link=%d capture=%s",
                                  record.link_id, record.capture_session_id)
                    else:
                        record.completed.set()
                    continue
                self._write(record)
            except (OSError, KeyError, TypeError, ValueError) as exc:
                LOG.error("recorder could not route record: %s", exc)
            finally:
                self.queue.task_done()

    def close(self, clean: bool) -> None:
        if not self.config.recording_enabled:
            return
        self.clean_shutdown = clean
        with self.lifecycle_lock:
            self.accepting = False
        for link_id in list(self.sessions):
            self.stop_capture(
                link_id, "program_shutdown" if clean else "unclean_shutdown",
                force_incomplete=not clean,
            )
        self.stop_event.set()
        try:
            self.queue.put(None, timeout=self.barrier_timeout_s)
        except queue.Full:
            LOG.error("recorder queue full during shutdown; worker sentinel timed out")
        if self.thread is not None:
            self.thread.join(timeout=10.0)
            if self.thread.is_alive():
                LOG.error("recorder thread did not stop; .part files retained")
                # The daemon still owns the handles.  Do not race it by
                # flushing, closing, or renaming files from this thread.
                return
        for link_id, handles in self.audit_handles.items():
            try:
                for source, handle in handles.items():
                    handle.flush(); os.fsync(handle.fileno()); handle.close()
                    if clean:
                        part = self._directory(link_id) / "logs" / (source + "_audit.jsonl.part")
                        os.replace(part, part.with_suffix(""))
                manifest_part = self._directory(link_id) / "manifest.json.part"
                if clean and manifest_part.exists():
                    with manifest_part.open("r", encoding="utf-8") as handle:
                        manifest = json.load(handle)
                    statuses = [entry.get("status") for entry in manifest.get("capture_sessions", [])]
                    manifest.update({"ended_at_utc": datetime.now(timezone.utc).isoformat(),
                                     "status": "complete" if statuses and all(s == "complete" for s in statuses) else "incomplete"})
                    self._write_json(manifest_part, manifest)
                    if manifest["status"] == "complete":
                        os.replace(manifest_part, manifest_part.with_suffix(""))
            except OSError as exc:
                LOG.error("recorder finalization failed link=%d: %s", link_id, exc)


# ---------------------------------------------------------------------------
# Thread-safe multi-link runtime.
# ---------------------------------------------------------------------------


@dataclass
class CaptureControlRequest:
    link_id: int
    tx_id: int
    rx_id: int
    endpoint: Tuple[str, int]
    command: int
    sequence: int
    event: threading.Event = field(default_factory=threading.Event)
    ack_mode: Optional[int] = None
    error: Optional[str] = None


@dataclass
class LinkRuntime:
    link_id: int
    tx_id: int
    rx_id: int
    formula: FormulaState
    feature_sequence: SequenceTracker = field(default_factory=SequenceTracker)
    snapshot_sequence: SequenceTracker = field(default_factory=SequenceTracker)
    previous_counters: Dict[str, Optional[int]] = field(
        default_factory=lambda: {"invalid": None, "wrong_source": None, "queue_drop": None, "packet_loss": None}
    )
    last_feature_sender_timestamp_us: Optional[int] = None
    last_snapshot_sender_timestamp_us: Optional[int] = None
    last_snapshot_receive_ns: int = 0
    last_receive_ns: int = 0
    source_endpoint: Optional[Tuple[str, int]] = None
    last_output: Optional[Dict[str, Any]] = None
    stale_reported: bool = False
    rx_capture_mode: str = "UNKNOWN"
    capture_control_error: Optional[str] = None
    capture_control_updated_ns: int = 0


DASHBOARD_HTML = """<!doctype html><html lang="vi"><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>WiEvac dashboard</title><style>body{font:15px system-ui;margin:auto;max-width:1100px;padding:16px;background:#f4f7f8}.card{background:white;padding:14px;margin:12px 0;border-radius:8px}button,input,select{padding:8px;margin:3px}table{width:100%;border-collapse:collapse}td,th{padding:6px;border-bottom:1px solid #ddd;text-align:left}.warn{color:#9a5b00}</style><h1>WiEvac dashboard</h1><div class="card"><h2>Recorder / RX capture</h2><pre id="capture-state">Đang tải…</pre></div><div class="card" id="summary"></div><div class="card"><h2>Hiệu chuẩn baseline</h2><button onclick="post('/api/baseline/confirm',{})">Xác nhận/thu lại baseline</button></div><div class="card"><h2>Thu dữ liệu thật</h2><p class="warn">Nhãn 0–4 người chỉ là metadata hiệu chuẩn simulator, không phải kết quả Formula hay AI.</p><select id="move"><option>stationary</option><option>walking</option></select><select id="arr"><option>longitudinal</option><option>lateral</option><option>random</option></select><input id="note" maxlength="300" placeholder="Ghi chú tùy chọn"><br><button onclick="start(0)">Hành lang trống</button><button onclick="start(1)">1 người</button><button onclick="start(2)">2 người</button><button onclick="start(3)">3 người</button><button onclick="start(4)">4 người</button><button onclick="post('/api/capture/stop',{})">Dừng phiên thu</button><p id="capture"></p></div><div class="card"><h2>Lịch sử Formula</h2><button onclick="audit('formula')">Làm mới</button><table id="formula"></table></div><div class="card"><h2>Lịch sử AI</h2><button onclick="audit('ai')">Làm mới</button><table id="ai"></table></div><script>const q=s=>document.querySelector(s);async function post(u,v){let r=await fetch(u,{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(v)});q('#capture').textContent=JSON.stringify(await r.json());refresh()}async function start(n){post('/api/capture/start',{person_count_reference:n,movement_state:q('#move').value,arrangement:q('#arr').value,operator_note:q('#note').value})}function rows(id,x){q('#'+id).innerHTML='<tr><th>Thời gian</th><th>Link</th><th>Trạng thái</th><th>Kết quả cũ → mới</th><th>Lý do</th></tr>'+x.map(v=>`<tr><td>${v.receive_time_utc||v.time_utc||''}</td><td>${v.link_id||''}</td><td>${v.state||''}</td><td>${v.previous_value??''} → ${v.value??v.result??''}</td><td>${v.explanation_vi||v.reason||''}</td></tr>`).join('')}async function audit(s){rows(s,(await (await fetch('/api/audit?source='+s+'&limit=30')).json()).items)}async function refresh(){let s=await (await fetch('/api/status')).json();q('#capture-state').textContent=JSON.stringify({recorder_state:s.recorder_state,rx_capture_mode:s.rx_capture_mode,capture_control_error:s.capture_control_error,interference:s.interference},null,2);q('#summary').textContent=JSON.stringify(s,null,2);audit('formula');audit('ai')}refresh();setInterval(refresh,1500)</script></html>"""


class WiEvacPiCore:
    def __init__(self, config: AppConfig):
        self.config = config
        self.decoder = ProtocolV2Decoder()
        self.quality_gate = QualityGate(config)
        self.models = LightGBMBundle(config)
        self.recorder = CaptureRecorder(config, self.models.manifest())
        self.links: Dict[int, LinkRuntime] = {}
        self.lock = threading.RLock()
        self.stop_event = threading.Event()
        self.socket: Optional[socket.socket] = None
        self.capture_control_lock = threading.Lock()
        self.pending_capture_control: Optional[CaptureControlRequest] = None
        self.capture_control_sequence = 0
        self.stale_thread = threading.Thread(target=self._stale_monitor, name="stale-monitor", daemon=True)
        self.baseline_operator_confirmed = False
        self.dashboard_server: Optional[ThreadingHTTPServer] = None
        self.dashboard_thread: Optional[threading.Thread] = None

    def _dashboard_status(self) -> Dict[str, Any]:
        now_ns = time.monotonic_ns()
        with self.lock:
            link = self.links.get(self.config.accepted_link_id)
            if link is None:
                return {
                    "pi_state": "stopped" if self.stop_event.is_set() else "running",
                    "udp_port": self.config.bind_port,
                    "recording_enabled": self.config.recording_enabled,
                    "capture_root": str(self.config.capture_root),
                    "link_id": self.config.accepted_link_id,
                    "tx_id": None, "rx_id": None, "packet_age_ms": None,
                    "connection_state": "no_data", "quality": {}, "baseline_state": "WAITING_FOR_BASELINE",
                    "baseline_valid_windows": 0, "baseline_required_windows": self.config.warmup_windows,
                    "baseline_operator_confirmed": self.baseline_operator_confirmed,
                    "formula": {}, "ai": {}, "session_id": self.config.session_id,
                    "corridor_id": self.config.corridor_id, "capture": None,
                    "rx_capture_mode": "UNKNOWN", "recorder_state": "idle",
                    "capture_control_error": "no_authenticated_node", "interference": {},
                }
            diagnostics = link.formula.diagnostics(now_ns)
            output = json.loads(json.dumps(link.last_output)) if link.last_output is not None else {}
            age_ms = (int((now_ns - link.last_receive_ns) / 1_000_000)
                      if link.last_receive_ns else None)
            connection = "no_data" if age_ms is None else ("stale" if link.stale_reported else "online")
            formula = dict(output.get("formula", {}))
            if formula.get("state") not in {"READY", "INTERFERENCE_SUSPECTED", "AMBIGUOUS"}:
                formula["passability_score"] = None
                formula["congestion_index"] = None
            return {
                "pi_state": "stopped" if self.stop_event.is_set() else "running",
                "udp_port": self.config.bind_port,
                "recording_enabled": self.config.recording_enabled,
                "capture_root": str(self.config.capture_root),
                "link_id": link.link_id, "tx_id": link.tx_id, "rx_id": link.rx_id,
                "packet_age_ms": age_ms, "connection_state": connection,
                "quality": output.get("quality", {}), "baseline_state": diagnostics["baseline_state"],
                "baseline_valid_windows": diagnostics["warmup_count"],
                "baseline_required_windows": self.config.warmup_windows,
                "baseline_operator_confirmed": link.formula.baseline_operator_confirmed,
                "formula": formula, "ai": output.get("ai", {}),
                "session_id": self.config.session_id, "corridor_id": self.config.corridor_id,
                "capture": self.recorder.capture_status(link.link_id),
                "recorder_state": "recording" if self.recorder.capture_status(link.link_id) else "idle",
                "rx_capture_mode": link.rx_capture_mode,
                "capture_control_error": link.capture_control_error,
                "interference": output.get("interference", diagnostics["nuisance"]),
            }

    def _next_capture_control_sequence(self) -> int:
        self.capture_control_sequence = (self.capture_control_sequence + 1) & 0xFFFFFFFF
        if self.capture_control_sequence == 0:
            self.capture_control_sequence = 1
        return self.capture_control_sequence

    def _request_capture_mode(self, link: LinkRuntime, command: int) -> None:
        sock = self.socket
        if sock is None or link.source_endpoint is None:
            raise ValueError("UDP service socket or authenticated RX endpoint is unavailable")
        sequence = self._next_capture_control_sequence()
        request = CaptureControlRequest(
            link.link_id, link.tx_id, link.rx_id, link.source_endpoint, command, sequence
        )
        prefix = HEADER_PREFIX.pack(
            PROTOCOL_MAGIC, PROTOCOL_VERSION, MSG_CAPTURE_CONTROL, 0,
            PROTOCOL_HEADER_LEN, CAPTURE_CONTROL_PAYLOAD.size, 0, 0,
            link.link_id, link.tx_id, link.rx_id, sequence,
            time.monotonic_ns() // 1_000, 0, 0, 0, 0, 0, 0,
        )
        payload = CAPTURE_CONTROL_PAYLOAD.pack(command)
        datagram = bytearray(PROTOCOL_HEADER_LEN + len(payload))
        datagram[:PROTOCOL_CRC_OFFSET] = prefix
        datagram[PROTOCOL_HEADER_LEN:] = payload
        CRC_FIELD.pack_into(
            datagram, PROTOCOL_CRC_OFFSET, zlib.crc32(payload, zlib.crc32(prefix)) & 0xFFFFFFFF
        )
        with self.lock:
            self.pending_capture_control = request
            link.capture_control_error = None
        last_send_error: Optional[OSError] = None
        for attempt in range(1, 4):
            try:
                sent = sock.sendto(datagram, request.endpoint)
                if sent != len(datagram):
                    raise OSError(f"short capture control send: {sent}/{len(datagram)}")
                last_send_error = None
            except OSError as exc:
                last_send_error = exc
                LOG.warning("capture control send attempt %d/3 failed: %s", attempt, exc)
            if request.event.wait(self.config.capture_ack_timeout_ms / 1000.0):
                break
        else:
            with self.lock:
                if self.pending_capture_control is request:
                    self.pending_capture_control = None
                    link.capture_control_error = (
                        f"capture_control_send_failed:{last_send_error}"
                        if last_send_error is not None else "capture_ack_timeout_after_3_attempts"
                    )
            raise ValueError("RX did not acknowledge capture control after 3 identical attempts")
        with self.lock:
            if self.pending_capture_control is request:
                self.pending_capture_control = None
            if request.error is not None:
                link.capture_control_error = request.error
                raise ValueError(request.error)
            expected_mode = CAPTURE_MODE_CAPTURE if command == CAPTURE_COMMAND_START else CAPTURE_MODE_RUN
            if request.ack_mode != expected_mode:
                link.capture_control_error = "capture_ack_mode_mismatch"
                raise ValueError("RX ACK did not confirm the requested runtime mode")
            link.rx_capture_mode = "CAPTURE" if request.ack_mode == CAPTURE_MODE_CAPTURE else "RUN"
            link.capture_control_updated_ns = time.monotonic_ns()
            link.capture_control_error = None

    def _best_effort_stop_after_failed_start(self, link: LinkRuntime) -> None:
        try:
            self._request_capture_mode(link, CAPTURE_COMMAND_STOP)
        except ValueError as exc:
            LOG.error("best-effort STOP after failed START did not receive ACK: %s", exc)

    def start_capture(self, payload: Mapping[str, Any]) -> Dict[str, Any]:
        allowed_movement = {"stationary", "walking"}
        allowed_arrangement = {"longitudinal", "lateral", "random"}
        try:
            people = int(payload.get("person_count_reference"))
        except (TypeError, ValueError) as exc:
            raise ValueError("person_count_reference must be an integer from 0 to 4") from exc
        movement = payload.get("movement_state", "stationary")
        arrangement = payload.get("arrangement", "random")
        note = payload.get("operator_note", "")
        if people not in range(5) or movement not in allowed_movement or arrangement not in allowed_arrangement:
            raise ValueError("invalid capture metadata")
        if not isinstance(note, str) or len(note) > 300:
            raise ValueError("operator_note must be a string of at most 300 characters")
        with self.capture_control_lock:
            with self.lock:
                link = self.links.get(self.config.accepted_link_id)
                if link is None or link.last_receive_ns == 0 or link.stale_reported:
                    raise ValueError("Chưa nhận dữ liệu hợp lệ từ node; không thể bắt đầu phiên thu")
                if self.recorder.capture_status(link.link_id) is not None:
                    raise ValueError("capture session already active; stop it before starting another")
                baseline_state = link.formula.diagnostics(time.monotonic_ns())["baseline_state"]
                if people > 0 and baseline_state != "READY":
                    raise ValueError(
                        "Không thể thu nhãn có người khi baseline chưa READY; "
                        "chỉ nhãn 0 người được phép trong WAITING_FOR_BASELINE/WARMING_UP"
                    )
            try:
                self._request_capture_mode(link, CAPTURE_COMMAND_START)
            except ValueError:
                self._best_effort_stop_after_failed_start(link)
                raise
            try:
                return self.recorder.start_capture(link.link_id, link.tx_id, link.rx_id, {
                    "data_origin": "real", "capture_purpose": "simulator_calibration",
                    "person_count_reference": people, "movement_state": movement,
                    "arrangement": arrangement, "operator_note": note,
                    "passability_ground_truth": None,
                })
            except (OSError, ValueError, KeyError, TypeError) as exc:
                try:
                    self.recorder.stop_capture(
                        link.link_id, "recorder_start_failed", force_incomplete=True
                    )
                except OSError as cleanup_exc:
                    LOG.error("recorder cleanup after start failure also failed: %s", cleanup_exc)
                self._best_effort_stop_after_failed_start(link)
                raise ValueError(f"recorder start failed after RX entered CAPTURE: {exc}") from exc

    def stop_capture(self, reason: str = "operator_requested") -> Dict[str, Any]:
        with self.capture_control_lock:
            with self.lock:
                link = self.links.get(self.config.accepted_link_id)
            if link is None:
                return {"status": "no_active_capture", "rx_capture_mode": "UNKNOWN"}
            try:
                self._request_capture_mode(link, CAPTURE_COMMAND_STOP)
            except ValueError as exc:
                result = self.recorder.stop_capture(
                    link.link_id, "capture_stop_ack_failed", force_incomplete=True
                )
                return {
                    "status": "incomplete" if result is not None else "stop_ack_failed_no_active_capture",
                    "error": str(exc), "capture": result, "rx_capture_mode": link.rx_capture_mode,
                }
            result = self.recorder.stop_capture(link.link_id, reason)
            return {**(result or {"status": "no_active_capture"}), "rx_capture_mode": "RUN"}

    def read_audit(self, source: str, limit: int) -> List[Mapping[str, Any]]:
        if source not in {"formula", "ai"}:
            raise ValueError("source must be formula or ai")
        path = (self.config.capture_root / self.config.pi_id / self.config.corridor_id
                / str(self.config.accepted_link_id) / self.config.session_id / "logs"
                / (source + "_audit.jsonl.part"))
        if not path.exists():
            path = path.with_suffix("")
        if not path.exists():
            return []
        # Seek backwards by chunks: audit history remains viewable after restart
        # without loading a potentially large JSONL file into RAM.
        with path.open("rb") as handle:
            handle.seek(0, os.SEEK_END); position = handle.tell(); data = b""
            while position > 0 and data.count(b"\n") <= limit:
                size = min(8192, position); position -= size; handle.seek(position)
                data = handle.read(size) + data
        items = []
        for line in data.splitlines()[-limit:]:
            try:
                items.append(json.loads(line))
            except json.JSONDecodeError:
                LOG.warning("skipped malformed %s audit line", source)
        return items

    def capture_list(self) -> Mapping[str, Any]:
        manifest = (self.config.capture_root / self.config.pi_id / self.config.corridor_id
                    / str(self.config.accepted_link_id) / self.config.session_id / "manifest.json.part")
        if not manifest.exists():
            manifest = manifest.with_suffix("")
        if not manifest.exists():
            return {"capture_sessions": []}
        try:
            with manifest.open("r", encoding="utf-8") as handle:
                value = json.load(handle)
            return {"capture_sessions": value.get("capture_sessions", [])}
        except (OSError, json.JSONDecodeError) as exc:
            LOG.error("could not read capture manifest: %s", exc)
            return {"capture_sessions": [], "error": "manifest_unavailable"}

    def confirm_baseline(self) -> None:
        with self.lock:
            self.baseline_operator_confirmed = True
            for link in self.links.values():
                formula = FormulaState(self.config, "operator_baseline_recalibration")
                formula.confirm_operator_baseline()
                link.formula = formula
                link.last_output = None
        LOG.info("operator confirmed baseline; collecting at least %d valid windows", self.config.warmup_windows)

    def _start_dashboard(self) -> None:
        core = self

        class DashboardHandler(BaseHTTPRequestHandler):
            def log_message(self, format: str, *args: Any) -> None:
                LOG.debug("dashboard " + format, *args)

            def _send(self, status: int, content_type: str, body: bytes) -> None:
                self.send_response(status)
                self.send_header("Content-Type", content_type)
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)

            def do_GET(self) -> None:
                if self.path == "/":
                    self._send(200, "text/html; charset=utf-8", DASHBOARD_HTML.encode("utf-8"))
                elif self.path == "/api/status":
                    body = json.dumps(core._dashboard_status(), ensure_ascii=False).encode("utf-8")
                    self._send(200, "application/json; charset=utf-8", body)
                elif self.path.startswith("/api/audit?"):
                    from urllib.parse import parse_qs, urlparse
                    query = parse_qs(urlparse(self.path).query)
                    source = query.get("source", [""])[0]
                    try:
                        limit = min(100, max(1, int(query.get("limit", ["30"])[0])))
                        body = json.dumps({"items": core.read_audit(source, limit)}, ensure_ascii=False).encode("utf-8")
                        self._send(200, "application/json; charset=utf-8", body)
                    except (TypeError, ValueError):
                        self._send(400, "application/json; charset=utf-8", b'{"error":"invalid audit query"}')
                elif self.path == "/api/captures":
                    body = json.dumps(core.capture_list(), ensure_ascii=False).encode("utf-8")
                    self._send(200, "application/json; charset=utf-8", body)
                else:
                    self._send(404, "text/plain; charset=utf-8", b"not found")

            def do_POST(self) -> None:
                length = self.headers.get("Content-Length", "0")
                try:
                    length_int = int(length)
                except ValueError:
                    length_int = -1
                if length_int < 0 or length_int > 4096:
                    self._send(413, "application/json; charset=utf-8", b'{"error":"request too large"}')
                    return
                try:
                    payload = json.loads(self.rfile.read(length_int) or b"{}")
                except (UnicodeDecodeError, json.JSONDecodeError):
                    self._send(400, "application/json; charset=utf-8", b'{"error":"invalid JSON"}')
                    return
                if not isinstance(payload, dict):
                    self._send(400, "application/json; charset=utf-8", b'{"error":"JSON object required"}')
                    return
                if self.path == "/api/baseline/confirm":
                    result = core.stop_capture("baseline_recalibration")
                    recorder_status = core.recorder.capture_status(core.config.accepted_link_id)
                    recorder_not_flushed = (
                        result.get("barrier_completed") is False or recorder_status is not None
                    )
                    if (result.get("rx_capture_mode") != "RUN" or result.get("error")
                            or recorder_not_flushed):
                        error = "Không thể hiệu chuẩn lại: RX chưa xác nhận RUN"
                        if recorder_not_flushed:
                            error = (
                                "Không thể hiệu chuẩn lại: recorder chưa flush xong; "
                                "file đang được giữ dưới dạng .part"
                            )
                        body = json.dumps({
                            "error": error,
                            "capture_stop": result,
                            "recorder": recorder_status,
                        }, ensure_ascii=False).encode("utf-8")
                        self._send(409, "application/json; charset=utf-8", body)
                        return
                    core.confirm_baseline()
                    body = json.dumps(core._dashboard_status(), ensure_ascii=False).encode("utf-8")
                    self._send(200, "application/json; charset=utf-8", body)
                    return
                if self.path == "/api/capture/start":
                    try:
                        body = json.dumps(core.start_capture(payload), ensure_ascii=False).encode("utf-8")
                        self._send(201, "application/json; charset=utf-8", body)
                    except ValueError as exc:
                        self._send(400, "application/json; charset=utf-8", json.dumps({"error": str(exc)}, ensure_ascii=False).encode("utf-8"))
                    return
                if self.path == "/api/capture/stop":
                    body = json.dumps(core.stop_capture(), ensure_ascii=False).encode("utf-8")
                    self._send(200, "application/json; charset=utf-8", body)
                    return
                else:
                    self._send(404, "text/plain; charset=utf-8", b"not found")

        try:
            server = ThreadingHTTPServer((self.config.dashboard_host, self.config.dashboard_port), DashboardHandler)
            server.daemon_threads = True
            self.dashboard_server = server
            self.dashboard_thread = threading.Thread(target=server.serve_forever, name="dashboard", daemon=True)
            self.dashboard_thread.start()
            LOG.info("dashboard listening on http://10.42.0.1:%d", self.config.dashboard_port)
        except OSError as exc:
            LOG.error("dashboard unavailable on %s:%d: %s", self.config.dashboard_host, self.config.dashboard_port, exc)

    def _stop_dashboard(self) -> None:
        server = self.dashboard_server
        if server is not None:
            server.shutdown()
            server.server_close()
            self.dashboard_server = None
        if self.dashboard_thread is not None:
            self.dashboard_thread.join(timeout=2.0)
            self.dashboard_thread = None

    def _get_link(self, header: ProtocolHeader) -> LinkRuntime:
        link = self.links.get(header.link_id)
        if link is None:
            formula = FormulaState(self.config, "link_created")
            if self.baseline_operator_confirmed:
                formula.confirm_operator_baseline()
            link = LinkRuntime(
                header.link_id,
                header.tx_id,
                header.rx_id,
                formula,
            )
            self.links[header.link_id] = link
            LOG.info(
                "new link_id=%d tx_id=%d rx_id=%d (transport endpoint is not identity)",
                header.link_id,
                header.tx_id,
                header.rx_id,
            )
        elif link.tx_id != header.tx_id or link.rx_id != header.rx_id:
            raise ProtocolError(
                f"identity conflict for link_id={header.link_id}: "
                f"expected tx/rx={link.tx_id}/{link.rx_id}, got {header.tx_id}/{header.rx_id}"
            )
        return link

    def _send_pi_alive(self, header: ProtocolHeader, endpoint: Tuple[str, int]) -> None:
        sock = self.socket
        if sock is None:
            LOG.error("cannot send PI_ALIVE before UDP socket is ready")
            return
        prefix = HEADER_PREFIX.pack(
            PROTOCOL_MAGIC,
            PROTOCOL_VERSION,
            MSG_PI_ALIVE,
            0,
            PROTOCOL_HEADER_LEN,
            0,
            0,
            0,
            header.link_id,
            header.tx_id,
            header.rx_id,
            header.sequence,
            time.monotonic_ns() // 1_000,
            0,
            0,
            0,
            0,
            0,
            0,
        )
        datagram = bytearray(PROTOCOL_HEADER_LEN)
        datagram[:PROTOCOL_CRC_OFFSET] = prefix
        CRC_FIELD.pack_into(datagram, PROTOCOL_CRC_OFFSET, zlib.crc32(prefix) & 0xFFFFFFFF)
        try:
            sent = sock.sendto(datagram, endpoint)
            if sent != len(datagram):
                LOG.error(
                    "short PI_ALIVE send to %s:%d: sent=%d expected=%d",
                    endpoint[0],
                    endpoint[1],
                    sent,
                    len(datagram),
                )
        except OSError as exc:
            LOG.error("PI_ALIVE send failed to %s:%d: %s", endpoint[0], endpoint[1], exc)

    def process_capture_ack(self, packet: DecodedPacket, endpoint: Tuple[str, int]) -> None:
        with self.lock:
            request = self.pending_capture_control
            if request is None:
                LOG.warning("unexpected capture ACK from %s:%d", endpoint[0], endpoint[1])
                return
            values = packet.values
            if (
                endpoint != request.endpoint
                or packet.header.link_id != request.link_id
                or packet.header.tx_id != request.tx_id
                or packet.header.rx_id != request.rx_id
                or values["command"] != request.command
                or values["command_sequence"] != request.sequence
            ):
                LOG.warning("rejected capture ACK that does not match the pending authenticated request")
                return
            request.ack_mode = int(values["actual_mode"])
            request.event.set()

    @staticmethod
    def _quality_deltas(link: LinkRuntime, header: ProtocolHeader) -> Dict[str, int]:
        current = {
            "invalid": header.invalid_csi_count,
            "wrong_source": header.wrong_source_count,
            "queue_drop": header.queue_drop_count,
            "packet_loss": header.packet_loss_count,
        }
        result: Dict[str, int] = {}
        reset = False
        for name, value in current.items():
            delta, did_reset = _counter_delta(value, link.previous_counters[name])
            result[name] = delta
            reset = reset or did_reset
            link.previous_counters[name] = value
        result["counter_reset"] = int(reset)
        return result

    @staticmethod
    def _model_feature_map(
        packet: DecodedPacket,
        deltas: Mapping[str, int],
        feature_packet_loss_delta: int,
    ) -> Dict[str, float]:
        values = {name: float(packet.values[name]) for name in FEATURE_FLOAT_NAMES}
        values.update(
            {
                "sample_count": float(packet.header.sample_count),
                "window_duration_ms": float(packet.header.window_duration_ms),
                "invalid_csi_delta": float(deltas["invalid"]),
                "wrong_source_delta": float(deltas["wrong_source"]),
                "queue_drop_delta": float(deltas["queue_drop"]),
                "packet_loss_delta": float(deltas["packet_loss"]),
                "feature_packet_loss_delta": float(feature_packet_loss_delta),
                "gain_metadata_valid": float(bool(packet.header.flags & FLAG_GAIN_METADATA_VALID)),
                "first_word_invalid_seen": float(
                    bool(packet.header.flags & FLAG_FIRST_WORD_INVALID_SEEN)
                ),
                "system_unavailable": float(
                    bool(packet.header.flags & FLAG_SYSTEM_UNAVAILABLE)
                ),
                "channel_filter_enabled": float(
                    bool(packet.header.flags & FLAG_CHANNEL_FILTER_ENABLED)
                ),
            }
        )
        return values

    def process_feature(self, packet: DecodedPacket, endpoint: Tuple[str, int]) -> None:
        now_ns = time.monotonic_ns()
        receive_utc = datetime.now(timezone.utc).isoformat()
        stream_restarted = False
        with self.lock:
            link = self._get_link(packet.header)
            was_stale = (
                link.last_receive_ns != 0
                and (now_ns - link.last_receive_ns) / 1_000_000 > self.config.stale_after_ms
            )
            sender_clock_restarted = (
                link.last_feature_sender_timestamp_us is not None
                and packet.header.sender_timestamp_us < link.last_feature_sender_timestamp_us
            )
            observation = link.feature_sequence.observe(packet.header.sequence)
            # A delayed/reordered UDP datagram also has an older sender clock.
            # Reset only after a local stale gap plus both backward indicators.
            if observation.status == "REORDERED" and sender_clock_restarted and was_stale:
                stream_restarted = True
                LOG.warning(
                    "link_id=%d receiver stream restart detected; sequence, counters, and Formula warmup reset",
                    link.link_id,
                )
                link.feature_sequence = SequenceTracker()
                link.previous_counters = {
                    "invalid": None,
                    "wrong_source": None,
                    "queue_drop": None,
                    "packet_loss": None,
                }
                link.formula = FormulaState(self.config, "receiver_stream_restart")
                if self.baseline_operator_confirmed:
                    link.formula.confirm_operator_baseline()
                observation = link.feature_sequence.observe(packet.header.sequence)
            if not observation.accepted:
                LOG.warning(
                    "link_id=%d feature sequence=%d rejected as %s",
                    link.link_id,
                    packet.header.sequence,
                    observation.status,
                )
                return
            deltas = self._quality_deltas(link, packet.header)
            quality = self.quality_gate.evaluate(packet.header, packet.values, deltas)
            previous_formula = dict(link.last_output["formula"]) if link.last_output else {}
            invalid_csi_ratio = deltas["invalid"] / max(
                1, packet.header.sample_count + deltas["invalid"]
            )
            queue_drop_ratio = deltas["queue_drop"] / max(
                1, packet.header.sample_count + deltas["queue_drop"]
            )
            nuisance_values = {
                name: float(packet.values[name]) for name in NuisanceDetector.NAMES
            }
            formula = link.formula.update(packet.values, quality, now_ns, nuisance_values)
            formula_diagnostics = link.formula.diagnostics(now_ns)
            model_features = self._model_feature_map(packet, deltas, observation.newly_lost)
            ai = self.models.infer(model_features, quality)
            link.last_receive_ns = now_ns
            link.last_feature_sender_timestamp_us = packet.header.sender_timestamp_us
            link.source_endpoint = endpoint
            link.stale_reported = False
            output: Dict[str, Any] = {
                "receive_time_utc": receive_utc,
                "link_id": link.link_id,
                "tx_id": link.tx_id,
                "rx_id": link.rx_id,
                "sequence": packet.header.sequence,
                "tx_firmware_version": packet.values["tx_firmware_version"],
                "rx_firmware_version": packet.values["rx_firmware_version"],
                "formula": asdict(formula),
                "formula_version": FORMULA_VERSION,
                "formula_diagnostics": formula_diagnostics,
                "interference": formula_diagnostics["nuisance"],
                "ai": ai,
                "quality": {
                    "state": quality.state,
                    "reason": quality.reason,
                    "confidence_factor": quality.confidence_factor,
                    "counter_deltas": dict(deltas),
                    "integrity_ratios": {
                        "packet_loss_ratio": packet.values["packet_loss_ratio"],
                        "invalid_csi_ratio": invalid_csi_ratio,
                        "queue_drop_ratio": queue_drop_ratio,
                    },
                    "integrity_limits": {
                        "maximum_packet_loss_ratio": self.config.maximum_packet_loss_ratio,
                        "maximum_invalid_csi_ratio": self.config.maximum_invalid_csi_ratio,
                        "maximum_queue_drop_ratio": self.config.maximum_queue_drop_ratio,
                    },
                    **{name: packet.values[name] for name in FEATURE_FLOAT_NAMES[3:]},
                },
                "sequence_stats": asdict(link.feature_sequence),
                "protocol_version": PROTOCOL_VERSION,
                "feature_schema_version": FEATURE_SCHEMA_VERSION,
            }
            link.last_output = output

        if stream_restarted:
            self.recorder.stop_capture(packet.header.link_id, "receiver_stream_restart", force_incomplete=True)
        LOG.info("output %s", json.dumps(output, ensure_ascii=False, sort_keys=True))
        record = {
            "record_type": "feature_window",
            "baseline_operator_confirmed": link.formula.baseline_operator_confirmed,
            "receive_time_utc": receive_utc,
            "receive_monotonic_ns": now_ns,
            "transport_source_ip": endpoint[0],
            "transport_source_port": endpoint[1],
            "link_id": packet.header.link_id,
            "tx_id": packet.header.tx_id,
            "rx_id": packet.header.rx_id,
            "sequence": packet.header.sequence,
            "sender_timestamp": packet.header.sender_timestamp_us,
            "protocol_version": PROTOCOL_VERSION,
            "feature_schema_version": FEATURE_SCHEMA_VERSION,
            "formula_version": FORMULA_VERSION,
            "header": asdict(packet.header),
            "features": dict(packet.values),
            "formula": output["formula"],
            "formula_diagnostics": output["formula_diagnostics"],
            "ai": output["ai"],
            "quality": output["quality"],
            "quality_state": quality.state,
            "quality_reason": quality.reason,
            "packet_loss_count": packet.header.packet_loss_count,
            "invalid_csi_count": packet.header.invalid_csi_count,
            "queue_drop_count": packet.header.queue_drop_count,
            "wrong_source_count": packet.header.wrong_source_count,
            "sequence_stats": output["sequence_stats"],
            "age_ms": 0,
            "sender_window_start_us": max(
                0,
                packet.header.sender_timestamp_us
                - packet.header.window_duration_ms * 1_000,
            ),
            "sender_window_end_us": packet.header.sender_timestamp_us,
            "tx_firmware_version": packet.values["tx_firmware_version"],
            "rx_firmware_version": packet.values["rx_firmware_version"],
            "ground_truth": None,
            "formula_is_ground_truth": False,
        }
        self.recorder.submit(record)
        formula_value = output["formula"].get("passability_score")
        formula_explanation = (
            "Formula chưa sẵn sàng: " + str(output["formula"].get("reason", "unknown"))
            if output["formula"].get("state") != "READY" else
            "Formula thay đổi theo độ lệch feature so với baseline, evidence gate và smoothing; không khẳng định nguyên nhân là do người."
        )
        formula_audit = {
            "receive_time_utc": receive_utc, "link_id": packet.header.link_id, "sequence": packet.header.sequence,
            "state": output["formula"].get("state"), "previous_value": previous_formula.get("passability_score"),
            "value": formula_value, "delta": (formula_value - previous_formula["passability_score"]
                if isinstance(formula_value, (int, float)) and isinstance(previous_formula.get("passability_score"), (int, float)) else None),
            "features": {name: packet.values[name] for name in FEATURE_FLOAT_NAMES[:3]},
            "baseline": output["formula_diagnostics"], "quality": output["quality"],
            "interference": output["interference"],
            "explanation_vi": formula_explanation,
        }
        ai_audits = [{
            "receive_time_utc": receive_utc, "link_id": packet.header.link_id, "sequence": packet.header.sequence,
            "task": task, "state": result.get("state"), "result": result.get("result", result.get("value")),
            "confidence": result.get("confidence"), "model_version": result.get("model_version"),
            "quality": output["quality"], "feature_contribution": result.get("feature_contribution"),
            "reason": result.get("reason"), "explanation_vi": (
                "AI chưa sẵn sàng vì chưa có model" if result.get("state") == "NOT_READY" else
                ("AI suy luận độc lập từ model; không dùng Formula thay thế AI." if result.get("state") == "READY" else "AI không có kết quả sẵn sàng cho cửa sổ này.")
            ),
        } for task, result in output["ai"].items()]
        self.recorder.audit(packet.header.link_id, formula_audit, ai_audits)

    def process_snapshot(self, packet: DecodedPacket, endpoint: Tuple[str, int]) -> None:
        now_ns = time.monotonic_ns()
        with self.lock:
            link = self._get_link(packet.header)
            sender_clock_restarted = (
                link.last_snapshot_sender_timestamp_us is not None
                and packet.header.sender_timestamp_us < link.last_snapshot_sender_timestamp_us
            )
            was_stale = (
                link.last_snapshot_receive_ns != 0
                and (now_ns - link.last_snapshot_receive_ns) / 1_000_000
                > self.config.stale_after_ms
            )
            observation = link.snapshot_sequence.observe(packet.header.sequence)
            if observation.status == "REORDERED" and sender_clock_restarted and was_stale:
                LOG.warning("link_id=%d snapshot stream restart detected", link.link_id)
                link.snapshot_sequence = SequenceTracker()
                observation = link.snapshot_sequence.observe(packet.header.sequence)
            if not observation.accepted:
                LOG.warning(
                    "link_id=%d snapshot sequence=%d rejected as %s",
                    link.link_id,
                    packet.header.sequence,
                    observation.status,
                )
                return
            link.last_snapshot_sender_timestamp_us = packet.header.sender_timestamp_us
            link.last_snapshot_receive_ns = now_ns
            link.source_endpoint = endpoint
            snapshot_sequence_stats = asdict(link.snapshot_sequence)
        self.recorder.submit(
            {
                "record_type": "csi_snapshot",
                "receive_time_utc": datetime.now(timezone.utc).isoformat(),
                "receive_monotonic_ns": now_ns,
                "transport_source_ip": endpoint[0],
                "transport_source_port": endpoint[1],
                "link_id": packet.header.link_id,
                "tx_id": packet.header.tx_id,
                "rx_id": packet.header.rx_id,
                "sequence": packet.header.sequence,
                "sender_timestamp": packet.header.sender_timestamp_us,
                "protocol_version": PROTOCOL_VERSION,
                "feature_schema_version": FEATURE_SCHEMA_VERSION,
                "formula_version": FORMULA_VERSION,
                "header": asdict(packet.header),
                "snapshot": dict(packet.values),
                "sequence_stats": snapshot_sequence_stats,
                "age_ms": 0,
                "rx_firmware_version": packet.values["rx_firmware_version"],
                "tx_firmware_version": None,
                "ground_truth": None,
                "formula_is_ground_truth": False,
                "baseline_operator_confirmed": link.formula.baseline_operator_confirmed,
            }
        )

    def _stale_monitor(self) -> None:
        while not self.stop_event.wait(0.5):
            now_ns = time.monotonic_ns()
            stale_outputs: List[Mapping[str, Any]] = []
            stale_records: List[Mapping[str, Any]] = []
            stale_capture_links: List[int] = []
            with self.lock:
                for link in self.links.values():
                    if link.last_receive_ns == 0 or link.last_output is None:
                        continue
                    age_ms = int((now_ns - link.last_receive_ns) / 1_000_000)
                    if age_ms > self.config.stale_after_ms and not link.stale_reported:
                        link.formula.pause(now_ns)
                        stale = dict(link.last_output)
                        stale["formula"] = asdict(
                            FormulaOutput(None, None, 0.0, "STALE", "no_fresh_feature_packet", age_ms)
                        )
                        stale["ai"] = {
                            task: _ai_result(
                                "UNKNOWN", "input_stale", result.get("model_version"), age_ms=age_ms
                            )
                            for task, result in link.last_output["ai"].items()
                        }
                        stale["quality"] = {
                            **link.last_output["quality"],
                            "state": "UNKNOWN",
                            "reason": "input_stale",
                            "confidence_factor": 0.0,
                        }
                        stale["formula_diagnostics"] = link.formula.diagnostics(now_ns)
                        stale["age_ms"] = age_ms
                        link.last_output = stale
                        link.stale_reported = True
                        stale_capture_links.append(link.link_id)
                        stale_outputs.append(stale)
                        endpoint = link.source_endpoint or ("", 0)
                        stale_records.append(
                            {
                                "record_type": "link_stale",
                                "receive_time_utc": datetime.now(timezone.utc).isoformat(),
                                "receive_monotonic_ns": now_ns,
                                "transport_source_ip": endpoint[0],
                                "transport_source_port": endpoint[1],
                                "link_id": link.link_id,
                                "tx_id": link.tx_id,
                                "rx_id": link.rx_id,
                                "protocol_version": PROTOCOL_VERSION,
                                "feature_schema_version": FEATURE_SCHEMA_VERSION,
                                "formula_version": FORMULA_VERSION,
                                "tx_firmware_version": stale.get("tx_firmware_version"),
                                "rx_firmware_version": stale.get("rx_firmware_version"),
                                "formula": stale["formula"],
                                "formula_diagnostics": stale["formula_diagnostics"],
                                "ai": stale["ai"],
                                "quality": stale["quality"],
                                "sequence_stats": stale["sequence_stats"],
                                "age_ms": age_ms,
                                "ground_truth": None,
                                "formula_is_ground_truth": False,
                                "baseline_operator_confirmed": link.formula.baseline_operator_confirmed,
                            }
                        )
            for output in stale_outputs:
                LOG.warning("stale %s", json.dumps(output, ensure_ascii=False, sort_keys=True))
            for record in stale_records:
                self.recorder.submit(record)
            for link_id in stale_capture_links:
                self.recorder.stop_capture(link_id, "node_stale", force_incomplete=True)

    def request_stop(self) -> None:
        self.stop_event.set()
        self._stop_dashboard()
        if self.socket is not None:
            try:
                self.socket.close()
            except OSError as exc:
                LOG.error("socket close failed: %s", exc)

    def run(self) -> None:
        clean_shutdown = False
        sock: Optional[socket.socket] = None
        try:
            self.recorder.start()
            self.stale_thread.start()
            sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
            self.socket = sock
            sock.setsockopt(socket.SOL_SOCKET, socket.SO_RCVBUF, self.config.receive_buffer_bytes)
            sock.settimeout(0.5)
            sock.bind((self.config.bind_host, self.config.bind_port))
            self._start_dashboard()
            LOG.info(
                "Protocol V2 listening on %s:%d schema=%d stale_after_ms=%d",
                self.config.bind_host,
                self.config.bind_port,
                FEATURE_SCHEMA_VERSION,
                self.config.stale_after_ms,
            )
            while not self.stop_event.is_set():
                try:
                    datagram, endpoint = sock.recvfrom(MAX_DATAGRAM_BYTES + 1)
                except socket.timeout:
                    continue
                except OSError as exc:
                    if self.stop_event.is_set():
                        break
                    LOG.error("UDP receive failed: %s", exc)
                    continue
                try:
                    packet = self.decoder.decode(datagram)
                    if packet.header.link_id != self.config.accepted_link_id:
                        LOG.warning("ignored packet for link_id=%d; trial accepts only link_id=%d",
                                    packet.header.link_id, self.config.accepted_link_id)
                        continue
                    if packet.kind == "feature":
                        # Bind the liveness response to a validated Protocol V2
                        # identity, but do not make it depend on Formula/model quality.
                        with self.lock:
                            self._get_link(packet.header)
                        self._send_pi_alive(packet.header, endpoint)
                        self.process_feature(packet, endpoint)
                    elif packet.kind == "snapshot":
                        self.process_snapshot(packet, endpoint)
                    elif packet.kind == "capture_ack":
                        self.process_capture_ack(packet, endpoint)
                    else:
                        LOG.error("decoder returned unexpected kind=%s", packet.kind)
                except ProtocolError as exc:
                    LOG.warning("rejected datagram from %s:%d: %s", endpoint[0], endpoint[1], exc)
                except (KeyError, TypeError, ValueError, RuntimeError) as exc:
                    LOG.exception("link processing failed for %s:%d: %s", endpoint[0], endpoint[1], exc)
            clean_shutdown = True
        finally:
            self.stop_event.set()
            self._stop_dashboard()
            if sock is not None:
                try:
                    sock.close()
                except OSError as exc:
                    LOG.error("UDP socket final close failed: %s", exc)
            if self.stale_thread.is_alive():
                self.stale_thread.join(timeout=2.0)
                if self.stale_thread.is_alive():
                    LOG.error("stale monitor did not stop before recorder shutdown")
            self.recorder.close(clean_shutdown)


def main() -> int:
    log_level_name = os.getenv("WIEVAC_LOG_LEVEL", "INFO").upper()
    log_level = getattr(logging, log_level_name, None)
    if not isinstance(log_level, int):
        logging.basicConfig(level=logging.INFO)
        LOG.error("WIEVAC_LOG_LEVEL is invalid: %s", log_level_name)
        return 2
    logging.basicConfig(
        level=log_level,
        format="%(asctime)s %(levelname)s %(name)s %(message)s",
    )
    try:
        config = AppConfig.from_environment()
        core = WiEvacPiCore(config)
    except (OSError, ValueError) as exc:
        LOG.error("configuration/startup failed: %s", exc)
        return 2

    def handle_signal(signum: int, frame: Any) -> None:
        del frame
        LOG.info("received signal %d; stopping", signum)
        core.request_stop()

    signal.signal(signal.SIGINT, handle_signal)
    signal.signal(signal.SIGTERM, handle_signal)
    try:
        core.run()
    except KeyboardInterrupt:
        LOG.info("keyboard interrupt; stopping")
        core.request_stop()
    except (OSError, ValueError) as exc:
        LOG.exception("fatal socket/recorder error: %s", exc)
        core.request_stop()
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
