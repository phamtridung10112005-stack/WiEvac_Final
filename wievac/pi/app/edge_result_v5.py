"""Versioned EdgeResult V5 codec.

The V5 wire format is intentionally independent from the legacy V4 header.
All integers are unsigned big-endian values and all floating point values are
IEEE-754 binary32 values.  The 14-byte header is::

    magic:u32, protocol_version:u8, message_type:u8,
    payload_length:u16, schema_version:u16, crc32:u32

CRC-32 uses the IEEE/zlib polynomial and covers the header prefix (the first
10 bytes, excluding the CRC field) followed by the payload.  The payload is a
sequence of length-prefixed UTF-8 strings followed by fixed-width fields:

    device_id, node_id, tx_id, rx_id, link_id, corridor_id, session_id
    boot_id:u32, window_seq:u32
    window_start_us:u64, window_end_us:u64, rx_timestamp_us:u64, age_ms:u32
    score_present:u8, score:f32
    state:u8, quality:f32, uncertainty:f32, disagreement:u8, reason_code:u16
    formula_version, model_version, model_hash, feature_schema_version
    sample_count:u32, invalid_count:u32, queue_drop_count:u32,
    sequence_gap:u32, packet_loss_ratio:f32, jitter_ms:f32

``packet_loss_ratio`` is a retained V5 compatibility slot. In the active
receiver it is the RX-side CSI callback sequence-gap ratio only; it is not
RX-to-Pi UDP loss and does not include queue drops. The runtime exposes the
source-specific fields instead of using this legacy name for dashboard loss.

V6 keeps the V5 prefix byte-for-byte and appends bounded adaptive metadata:
    baseline_state, baseline_version:u32, baseline_update_reason,
    baseline_confidence:f32, drift_state, transition_state, model_state,
    nullable raw/filtered/occupancy/blocking evidence scores.

Schema 7 keeps the V6 prefix byte-for-byte and appends shadow Doppler:
    doppler_present:u8, doppler_ratio:f32, doppler_fs_hz:f32, doppler_samples:u16.
    When present is 0, ratio and fs are -1.0. r is walking-band energy
    fraction vs this link, not P(person) and not a house-calibrated %.

Decoders accept schema 5, 6 and 7. New packets use schema 7.

The score value is ``-1.0`` when ``score_present`` is zero and is ignored by
decoders, so UNKNOWN is represented as a real nullable value rather than as
score zero or NaN.
String lengths are one-byte unsigned lengths (maximum 255 bytes).  This codec
does not alter or import the V4 implementation.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass
from enum import IntEnum, Enum
import math
import struct
from typing import Any, ClassVar
import zlib


MAGIC = 0x57495635  # ASCII "WIV5"
PROTOCOL_VERSION = 5
SCHEMA_V5 = 5
SCHEMA_V6 = 6
SCHEMA_V7 = 7
SCHEMA_VERSION = SCHEMA_V7
FEATURE_SCHEMA_VERSION = str(SCHEMA_VERSION)
MESSAGE_TYPE_EDGE_RESULT = 0x30
MSG_EDGE_RESULT = MESSAGE_TYPE_EDGE_RESULT
EDGE_RESULT_MESSAGE_TYPE = MESSAGE_TYPE_EDGE_RESULT

HEADER = struct.Struct(">IBBHHI")
HEADER_SIZE = HEADER.size
CRC_OFFSET = HEADER_SIZE - 4
# Keep these limits equal to the portable C implementation.  The wire format
# uses one-byte lengths, but the firmware stores ordinary text in 32-byte
# buffers (31 bytes plus the NUL terminator) and reserves a larger buffer for
# the model hash.  V6 allows a 700-byte payload; the C packet buffer is bounded
# to 768 bytes including the 14-byte header.
MAX_STRING_BYTES = 31
MAX_MODEL_HASH_BYTES = 64
# V6 carries bounded adaptive metadata in addition to the V5 prefix. Keep
# this limit aligned with the portable C codec's fixed packet buffer.
MAX_PAYLOAD_BYTES = 700
MAX_PACKET_BYTES = HEADER_SIZE + MAX_PAYLOAD_BYTES

_STRING_LIMITS = {
    "model_hash": MAX_MODEL_HASH_BYTES,
}


class EdgeResultProtocolError(ValueError):
    """Raised when an EdgeResult V5 packet or value violates its contract."""

    def __init__(self, reason: str) -> None:
        self.reason = str(reason)
        super().__init__(self.reason)


class EdgeResultState(str, Enum):
    PASSABLE = "PASSABLE"
    DEGRADED = "DEGRADED"
    BLOCKED = "BLOCKED"
    UNKNOWN = "UNKNOWN"


class EdgeResultReason(IntEnum):
    UNKNOWN = 0
    VALID = 1
    WARMING_UP = 2
    INVALID_CSI = 3
    STALE = 4
    OOD = 5
    DISAGREEMENT = 6
    QUALITY_LOW = 7
    FORMULA_AI_DISAGREEMENT = 6
    MODEL_OOD = 5
    WRONG_SOURCE = 100
    TIMESTAMP_INVALID = 101
    SEQUENCE_GAP = 102
    PACKET_LOSS = 103
    MODEL_NOT_READY = 104
    MODEL_REJECTED = 105
    QUEUE_DROP = 106
    ENVIRONMENT_SHIFT = 107
    PERSISTENT_OCCUPANCY = 108
    BLOCKED = 109


_STATE_TO_CODE = {
    EdgeResultState.PASSABLE: 0,
    EdgeResultState.DEGRADED: 1,
    EdgeResultState.BLOCKED: 2,
    EdgeResultState.UNKNOWN: 3,
}
_CODE_TO_STATE = {value: key for key, value in _STATE_TO_CODE.items()}
_REASON_NAMES = {item.name: item for item in EdgeResultReason}


@dataclass(frozen=True)
class EdgeResultV5:
    """One local result for one link and one time window."""

    device_id: str
    node_id: str
    tx_id: str
    rx_id: str
    link_id: str
    corridor_id: str
    session_id: str
    boot_id: int
    window_seq: int
    window_start_us: int
    window_end_us: int
    rx_timestamp_us: int
    age_ms: int
    local_passability_score: float | None
    state: EdgeResultState | str
    quality: float
    uncertainty: float
    disagreement: bool
    reason_code: int | EdgeResultReason | str
    formula_version: str
    model_version: str
    model_hash: str
    feature_schema_version: str | int
    sample_count: int
    invalid_count: int
    queue_drop_count: int
    sequence_gap: int
    packet_loss_ratio: float
    jitter_ms: float
    # Reference/debug metadata for dynamic baseline and causal filtering. These
    # fields are intentionally outside the fixed V5 wire payload.
    baseline_state: str = "NO_BASELINE"
    baseline_version: int = 0
    baseline_update_reason: str = "startup"
    baseline_confidence: float = 0.0
    drift_state: str = "NONE"
    raw_evidence_score: float | None = None
    filtered_passability_score: float | None = None
    # V6 adaptive evidence carried on the wire.  Defaults preserve V5 callers.
    transition_state: str = "STABLE"
    occupancy_evidence: float | None = None
    blocking_evidence: float | None = None
    model_state: str = "NOT_READY"
    doppler_valid: bool = False
    doppler_ratio: float | None = None
    doppler_fs_hz: float | None = None
    doppler_samples: int = 0

    MAGIC: ClassVar[int] = MAGIC
    PROTOCOL_VERSION: ClassVar[int] = PROTOCOL_VERSION
    SCHEMA_VERSION: ClassVar[int] = SCHEMA_VERSION
    MESSAGE_TYPE: ClassVar[int] = MESSAGE_TYPE_EDGE_RESULT

    @property
    def magic(self) -> int:
        return MAGIC

    @property
    def protocol_version(self) -> int:
        return PROTOCOL_VERSION

    @property
    def message_type(self) -> int:
        return MESSAGE_TYPE_EDGE_RESULT

    @property
    def payload_length(self) -> int:
        return len(_pack_payload(self))

    @property
    def crc(self) -> int:
        return int.from_bytes(self.encode()[CRC_OFFSET:HEADER_SIZE], "big")

    def validate(self) -> "EdgeResultV5":
        """Validate fields without changing their values."""
        identifiers = (
            ("device_id", self.device_id),
            ("node_id", self.node_id),
            ("tx_id", self.tx_id),
            ("rx_id", self.rx_id),
            ("link_id", self.link_id),
            ("corridor_id", self.corridor_id),
            ("session_id", self.session_id),
        )
        for name, value in identifiers:
            _validate_identifier(name, value)

        strings = (
            ("formula_version", self.formula_version),
            ("model_version", self.model_version),
            ("model_hash", self.model_hash),
            ("baseline_state", self.baseline_state),
            ("baseline_update_reason", self.baseline_update_reason),
            ("drift_state", self.drift_state),
            ("transition_state", self.transition_state),
            ("model_state", self.model_state),
        )
        for name, value in strings:
            _validate_string(
                name,
                value,
                required=name not in ("model_hash",),
                max_bytes=_STRING_LIMITS.get(name, MAX_STRING_BYTES),
            )

        _validate_uint("boot_id", self.boot_id, 32, nonzero=True)
        _validate_uint("window_seq", self.window_seq, 32, nonzero=True)
        _validate_uint("window_start_us", self.window_start_us, 64, nonzero=True)
        _validate_uint("window_end_us", self.window_end_us, 64, nonzero=True)
        _validate_uint("rx_timestamp_us", self.rx_timestamp_us, 64, nonzero=True)
        _validate_uint("age_ms", self.age_ms, 32)
        if self.window_end_us <= self.window_start_us:
            raise EdgeResultProtocolError("window_duration_not_positive")
        # All producers use the end of the local monotonic window as the
        # earliest receive timestamp.  Rejecting an earlier timestamp keeps
        # future/stale checks consistent across codec and runtime consumers.
        if self.rx_timestamp_us < self.window_end_us:
            raise EdgeResultProtocolError("rx_timestamp_before_window_end")

        state = _coerce_state(self.state)
        score = self.local_passability_score
        if score is not None:
            _validate_finite_range("local_passability_score", score, 0.0, 100.0)
        if state is EdgeResultState.UNKNOWN and score is not None:
            raise EdgeResultProtocolError("unknown_score_must_be_null")

        _validate_finite_range("quality", self.quality, 0.0, 100.0)
        _validate_finite_range("uncertainty", self.uncertainty, 0.0, 100.0)
        if not (isinstance(self.disagreement, bool) or
                (isinstance(self.disagreement, int) and self.disagreement in (0, 1))):
            raise EdgeResultProtocolError("disagreement_not_bool")
        _coerce_reason(self.reason_code)
        _validate_version("feature_schema_version", self.feature_schema_version)
        if str(self.feature_schema_version) not in {str(SCHEMA_V5), str(SCHEMA_V6), str(SCHEMA_V7)}:
            raise EdgeResultProtocolError("feature_schema_version_mismatch")

        _validate_uint("baseline_version", self.baseline_version, 32)
        _validate_finite_range("baseline_confidence", self.baseline_confidence, 0.0, 100.0)
        for name, score in (("raw_evidence_score", self.raw_evidence_score),
                            ("filtered_passability_score", self.filtered_passability_score),
                            ("occupancy_evidence", self.occupancy_evidence),
                            ("blocking_evidence", self.blocking_evidence)):
            if score is not None:
                _validate_finite_range(name, score, 0.0, 100.0)

        _validate_uint("sample_count", self.sample_count, 32)
        _validate_uint("invalid_count", self.invalid_count, 32)
        _validate_uint("queue_drop_count", self.queue_drop_count, 32)
        _validate_uint("sequence_gap", self.sequence_gap, 32)
        if self.invalid_count > self.sample_count:
            raise EdgeResultProtocolError("invalid_count_exceeds_sample_count")
        _validate_finite_range("packet_loss_ratio", self.packet_loss_ratio, 0.0, 1.0)
        _validate_finite_range("jitter_ms", self.jitter_ms, 0.0, float(0xFFFFFFFF))
        _validate_uint("doppler_samples", self.doppler_samples, 16)
        if not (isinstance(self.doppler_valid, bool) or
                (isinstance(self.doppler_valid, int) and self.doppler_valid in (0, 1))):
            raise EdgeResultProtocolError("doppler_valid_not_bool")
        if self.doppler_valid:
            if self.doppler_ratio is None or self.doppler_fs_hz is None:
                raise EdgeResultProtocolError("doppler_present_missing_values")
            _validate_finite_range("doppler_ratio", self.doppler_ratio, 0.0, 1.0)
            if not math.isfinite(self.doppler_fs_hz) or self.doppler_fs_hz <= 0.0:
                raise EdgeResultProtocolError("doppler_fs_hz_invalid")
        elif self.doppler_ratio is not None or self.doppler_fs_hz is not None:
            if self.doppler_ratio is not None:
                _validate_finite_range("doppler_ratio", self.doppler_ratio, 0.0, 1.0)
            if self.doppler_fs_hz is not None and (
                    not math.isfinite(self.doppler_fs_hz) or self.doppler_fs_hz <= 0.0):
                raise EdgeResultProtocolError("doppler_fs_hz_invalid")
        return self

    def normalized(self) -> "EdgeResultV5":
        """Return a copy with enum/string values normalized for callers."""
        self.validate()
        return EdgeResultV5(
            device_id=str(self.device_id), node_id=str(self.node_id),
            tx_id=str(self.tx_id), rx_id=str(self.rx_id), link_id=str(self.link_id),
            corridor_id=str(self.corridor_id), session_id=str(self.session_id),
            boot_id=int(self.boot_id), window_seq=int(self.window_seq),
            window_start_us=int(self.window_start_us), window_end_us=int(self.window_end_us),
            rx_timestamp_us=int(self.rx_timestamp_us), age_ms=int(self.age_ms),
            local_passability_score=(None if self.local_passability_score is None
                                     else float(self.local_passability_score)),
            state=_coerce_state(self.state), quality=float(self.quality),
            uncertainty=float(self.uncertainty), disagreement=bool(self.disagreement),
            reason_code=int(_coerce_reason(self.reason_code)),
            formula_version=str(self.formula_version), model_version=str(self.model_version),
            model_hash=str(self.model_hash),
            feature_schema_version=str(self.feature_schema_version),
            sample_count=int(self.sample_count), invalid_count=int(self.invalid_count),
            queue_drop_count=int(self.queue_drop_count), sequence_gap=int(self.sequence_gap),
            packet_loss_ratio=float(self.packet_loss_ratio), jitter_ms=float(self.jitter_ms),
            baseline_state=str(self.baseline_state), baseline_version=int(self.baseline_version),
            baseline_update_reason=str(self.baseline_update_reason),
            baseline_confidence=float(self.baseline_confidence), drift_state=str(self.drift_state),
            raw_evidence_score=(None if self.raw_evidence_score is None else float(self.raw_evidence_score)),
            filtered_passability_score=(None if self.filtered_passability_score is None else float(self.filtered_passability_score)),
            transition_state=str(self.transition_state),
            occupancy_evidence=(None if self.occupancy_evidence is None else float(self.occupancy_evidence)),
            blocking_evidence=(None if self.blocking_evidence is None else float(self.blocking_evidence)),
            model_state=str(self.model_state),
            doppler_valid=bool(self.doppler_valid),
            doppler_ratio=(None if self.doppler_ratio is None else float(self.doppler_ratio)),
            doppler_fs_hz=(None if self.doppler_fs_hz is None else float(self.doppler_fs_hz)),
            doppler_samples=int(self.doppler_samples),
        )

    def encode(self) -> bytes:
        return encode_edge_result(self)

    def to_dict(self) -> dict[str, Any]:
        """Return a JSON-friendly mapping without changing wire semantics."""
        value = self.normalized()
        data = asdict(value)
        data["state"] = value.state.value
        data["reason_code"] = int(_coerce_reason(value.reason_code))
        data.update({
            "magic": MAGIC,
            "protocol_version": PROTOCOL_VERSION,
            "message_type": MESSAGE_TYPE_EDGE_RESULT,
            "payload_length": value.payload_length,
            "crc32": value.crc,
        })
        return data

    @classmethod
    def decode(cls, datagram: bytes) -> "EdgeResultV5":
        return decode_edge_result(datagram)


def _validate_string(name: str, value: Any, *, required: bool,
                     max_bytes: int | None = None) -> None:
    if not isinstance(value, str):
        raise EdgeResultProtocolError(f"{name}_not_string")
    if required and not value:
        raise EdgeResultProtocolError(f"{name}_empty")
    if "\x00" in value:
        raise EdgeResultProtocolError(f"{name}_contains_nul")
    if any(ord(char) < 0x20 for char in value):
        raise EdgeResultProtocolError(f"{name}_contains_control")
    try:
        encoded = value.encode("utf-8")
    except UnicodeEncodeError as exc:
        raise EdgeResultProtocolError(f"{name}_not_utf8") from exc
    limit = MAX_STRING_BYTES if max_bytes is None else int(max_bytes)
    if len(encoded) > limit:
        raise EdgeResultProtocolError(f"{name}_too_long")


def _validate_identifier(name: str, value: Any) -> None:
    """Accept stable textual IDs and non-negative numeric IDs from node APIs."""
    if isinstance(value, int) and not isinstance(value, bool):
        if 0 <= value <= 0xFFFFFFFF:
            return
        raise EdgeResultProtocolError(f"{name}_out_of_range")
    _validate_string(name, value, required=True,
                     max_bytes=_STRING_LIMITS.get(name, MAX_STRING_BYTES))


def _validate_version(name: str, value: Any) -> None:
    if isinstance(value, bool):
        raise EdgeResultProtocolError(f"{name}_invalid")
    text = str(value)
    if not text or len(text.encode("utf-8")) > MAX_STRING_BYTES:
        raise EdgeResultProtocolError(f"{name}_invalid")


def _validate_uint(name: str, value: Any, bits: int, *, nonzero: bool = False) -> None:
    if isinstance(value, bool) or not isinstance(value, int):
        raise EdgeResultProtocolError(f"{name}_not_uint")
    maximum = (1 << bits) - 1
    if value < (1 if nonzero else 0) or value > maximum:
        raise EdgeResultProtocolError(f"{name}_out_of_range")


def _validate_finite_range(name: str, value: Any, lower: float, upper: float) -> None:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise EdgeResultProtocolError(f"{name}_not_float")
    try:
        numeric = float(value)
    except (TypeError, ValueError) as exc:
        raise EdgeResultProtocolError(f"{name}_not_float") from exc
    if not math.isfinite(numeric):
        raise EdgeResultProtocolError(f"{name}_non_finite")
    if not lower <= numeric <= upper:
        raise EdgeResultProtocolError(f"{name}_out_of_range")


def _coerce_state(value: EdgeResultState | str) -> EdgeResultState:
    if isinstance(value, EdgeResultState):
        return value
    if isinstance(value, str):
        try:
            return EdgeResultState(value.upper())
        except ValueError as exc:
            raise EdgeResultProtocolError("invalid_state") from exc
    raise EdgeResultProtocolError("invalid_state")


def _coerce_reason(value: int | EdgeResultReason | str) -> EdgeResultReason | int:
    if isinstance(value, EdgeResultReason):
        return value
    if isinstance(value, bool):
        raise EdgeResultProtocolError("invalid_reason_code")
    if isinstance(value, int):
        if 0 <= value <= 0xFFFF:
            return value
        raise EdgeResultProtocolError("reason_code_out_of_range")
    if isinstance(value, str):
        key = value.upper()
        if key in _REASON_NAMES:
            return _REASON_NAMES[key]
        try:
            numeric = int(value, 10)
        except ValueError as exc:
            raise EdgeResultProtocolError("invalid_reason_code") from exc
        if 0 <= numeric <= 0xFFFF:
            return numeric
    raise EdgeResultProtocolError("invalid_reason_code")


def _write_string(buffer: bytearray, value: str) -> None:
    encoded = value.encode("utf-8")
    buffer.append(len(encoded))
    buffer.extend(encoded)


def _read_string(payload: bytes, offset: int, name: str, *, required: bool = True) -> tuple[str, int]:
    if offset >= len(payload):
        raise EdgeResultProtocolError("truncated_" + name)
    length = payload[offset]
    offset += 1
    end = offset + length
    if end > len(payload):
        raise EdgeResultProtocolError("truncated_" + name)
    try:
        value = payload[offset:end].decode("utf-8")
    except UnicodeDecodeError as exc:
        raise EdgeResultProtocolError("invalid_utf8_" + name) from exc
    _validate_string(name, value, required=required,
                     max_bytes=_STRING_LIMITS.get(name, MAX_STRING_BYTES))
    return value, end


def _pack_payload_v5(result: EdgeResultV5) -> bytes:
    value = result.normalized()
    payload = bytearray()
    for name in ("device_id", "node_id", "tx_id", "rx_id", "link_id", "corridor_id", "session_id"):
        _write_string(payload, getattr(value, name))
    payload.extend(struct.pack(
        ">IIQQQI",
        value.boot_id, value.window_seq, value.window_start_us,
        value.window_end_us, value.rx_timestamp_us, value.age_ms,
    ))
    score_present = value.local_passability_score is not None
    payload.extend(struct.pack(">Bf", int(score_present),
                               value.local_passability_score if score_present else -1.0))
    payload.extend(struct.pack(">BffBH", _STATE_TO_CODE[value.state], value.quality,
                               value.uncertainty, int(value.disagreement),
                               int(_coerce_reason(value.reason_code))))
    for name in ("formula_version", "model_version", "model_hash"):
        _write_string(payload, getattr(value, name))
    _write_string(payload, value.feature_schema_version)
    payload.extend(struct.pack(
        ">IIIIff", value.sample_count, value.invalid_count,
        value.queue_drop_count, value.sequence_gap, value.packet_loss_ratio, value.jitter_ms,
    ))
    return bytes(payload)


def _write_nullable_score(buffer: bytearray, value: float | None) -> None:
    present = value is not None
    buffer.extend(struct.pack(">Bf", int(present), value if present else -1.0))


def _pack_payload_v6(result: EdgeResultV5) -> bytes:
    """Pack the V5 prefix followed by bounded adaptive V6 metadata."""
    value = result.normalized()
    payload = bytearray(_pack_payload_v5(value))
    for name in ("baseline_state", "baseline_update_reason", "drift_state"):
        _write_string(payload, getattr(value, name))
    payload.extend(struct.pack(">If", value.baseline_version, value.baseline_confidence))
    for name in ("transition_state", "model_state"):
        _write_string(payload, getattr(value, name))
    for name in ("raw_evidence_score", "filtered_passability_score",
                 "occupancy_evidence", "blocking_evidence"):
        _write_nullable_score(payload, getattr(value, name))
    return bytes(payload)


def _pack_payload_v7(result: EdgeResultV5) -> bytes:
    """Pack V6 bytes followed by shadow Doppler fields."""
    value = result.normalized()
    payload = bytearray(_pack_payload_v6(value))
    present = bool(value.doppler_valid)
    payload.extend(struct.pack(
        ">BffH",
        int(present),
        value.doppler_ratio if present else -1.0,
        value.doppler_fs_hz if present else -1.0,
        int(value.doppler_samples if present else 0),
    ))
    return bytes(payload)


def _pack_payload(result: EdgeResultV5, schema_version: int = SCHEMA_VERSION) -> bytes:
    if schema_version == SCHEMA_V5:
        return _pack_payload_v5(result)
    if schema_version == SCHEMA_V6:
        return _pack_payload_v6(result)
    if schema_version == SCHEMA_V7:
        return _pack_payload_v7(result)
    raise EdgeResultProtocolError("unsupported_schema_version")


def encode_edge_result(result: EdgeResultV5, *, schema_version: int = SCHEMA_VERSION) -> bytes:
    """Serialize one EdgeResult using schema 7 by default; 5/6 are compatibility."""
    if not isinstance(result, EdgeResultV5):
        raise TypeError("result must be EdgeResultV5")
    if schema_version not in (SCHEMA_V5, SCHEMA_V6, SCHEMA_V7):
        raise EdgeResultProtocolError("unsupported_schema_version")
    expected_feature_schema = str(schema_version)
    if str(result.feature_schema_version) != expected_feature_schema:
        raise EdgeResultProtocolError("feature_schema_version_mismatch")
    payload = _pack_payload(result, schema_version)
    if len(payload) > MAX_PAYLOAD_BYTES:
        raise EdgeResultProtocolError("payload_too_large")
    prefix = struct.pack(">IBBHH", MAGIC, PROTOCOL_VERSION,
                        MESSAGE_TYPE_EDGE_RESULT, len(payload), schema_version)
    checksum = crc32(prefix, payload)
    return prefix + struct.pack(">I", checksum) + payload


def crc32(prefix: bytes, payload: bytes) -> int:
    """Return the V5 CRC over header prefix and payload."""
    return zlib.crc32(prefix + payload) & 0xFFFFFFFF


def _unpack_payload(payload: bytes, *, schema_version: int = SCHEMA_V7) -> EdgeResultV5:
    offset = 0
    strings: dict[str, str] = {}
    for name in ("device_id", "node_id", "tx_id", "rx_id", "link_id", "corridor_id", "session_id"):
        strings[name], offset = _read_string(payload, offset, name)
    fixed = struct.Struct(">IIQQQI")
    if offset + fixed.size > len(payload):
        raise EdgeResultProtocolError("truncated_timing")
    boot_id, window_seq, window_start_us, window_end_us, rx_timestamp_us, age_ms = fixed.unpack_from(payload, offset)
    offset += fixed.size
    score_struct = struct.Struct(">Bf")
    if offset + score_struct.size > len(payload):
        raise EdgeResultProtocolError("truncated_score")
    score_present, local_score = score_struct.unpack_from(payload, offset)
    offset += score_struct.size
    if score_present not in (0, 1):
        raise EdgeResultProtocolError("invalid_score_presence")
    if not math.isfinite(local_score):
        raise EdgeResultProtocolError("local_passability_score_non_finite")
    if not score_present and local_score != -1.0:
        raise EdgeResultProtocolError("null_score_payload_invalid")
    state_struct = struct.Struct(">BffBH")
    if offset + state_struct.size > len(payload):
        raise EdgeResultProtocolError("truncated_state")
    state_code, quality, uncertainty, disagreement, reason_code = state_struct.unpack_from(payload, offset)
    offset += state_struct.size
    if disagreement not in (0, 1):
        raise EdgeResultProtocolError("disagreement_not_bool")
    state = _CODE_TO_STATE.get(state_code)
    if state is None:
        raise EdgeResultProtocolError("invalid_state_code")
    versions: dict[str, str] = {}
    for name in ("formula_version", "model_version", "model_hash"):
        versions[name], offset = _read_string(payload, offset, name, required=name != "model_hash")
    versions["feature_schema_version"], offset = _read_string(payload, offset, "feature_schema_version")
    if str(versions["feature_schema_version"]) != str(schema_version):
        raise EdgeResultProtocolError("feature_schema_version_mismatch")
    counters = struct.Struct(">IIIIff")
    if offset + counters.size > len(payload):
        raise EdgeResultProtocolError("truncated_counters")
    sample_count, invalid_count, queue_drop_count, sequence_gap, packet_loss_ratio, jitter_ms = counters.unpack_from(payload, offset)
    offset += counters.size
    metadata: dict[str, Any] = {
        "baseline_state": "NO_BASELINE",
        "baseline_version": 0,
        "baseline_update_reason": "v5_compatibility_decode",
        "baseline_confidence": 0.0,
        "drift_state": "NONE",
        "transition_state": "STABLE",
        "model_state": "NOT_READY",
        "raw_evidence_score": None,
        "filtered_passability_score": None,
        "occupancy_evidence": None,
        "blocking_evidence": None,
        "doppler_valid": False,
        "doppler_ratio": None,
        "doppler_fs_hz": None,
        "doppler_samples": 0,
    }
    if schema_version in (SCHEMA_V6, SCHEMA_V7):
        for name in ("baseline_state", "baseline_update_reason", "drift_state"):
            metadata[name], offset = _read_string(payload, offset, name)
        adaptive_header = struct.Struct(">If")
        if offset + adaptive_header.size > len(payload):
            raise EdgeResultProtocolError("truncated_v6_baseline")
        metadata["baseline_version"], metadata["baseline_confidence"] = adaptive_header.unpack_from(payload, offset)
        offset += adaptive_header.size
        for name in ("transition_state", "model_state"):
            metadata[name], offset = _read_string(payload, offset, name)
        for name in ("raw_evidence_score", "filtered_passability_score",
                     "occupancy_evidence", "blocking_evidence"):
            if offset + 5 > len(payload):
                raise EdgeResultProtocolError("truncated_v6_score")
            present, adaptive_score = struct.unpack_from(">Bf", payload, offset)
            offset += 5
            if present not in (0, 1):
                raise EdgeResultProtocolError("invalid_v6_score_presence")
            if not present:
                if adaptive_score != -1.0:
                    raise EdgeResultProtocolError("null_v6_score_payload_invalid")
                metadata[name] = None
            else:
                if not math.isfinite(adaptive_score):
                    raise EdgeResultProtocolError(name + "_non_finite")
                metadata[name] = adaptive_score
        if schema_version == SCHEMA_V7:
            if offset + 11 > len(payload):
                raise EdgeResultProtocolError("truncated_v7_doppler")
            present, ratio, fs_hz, samples = struct.unpack_from(">BffH", payload, offset)
            offset += 11
            if present not in (0, 1):
                raise EdgeResultProtocolError("invalid_doppler_presence")
            if not present:
                if ratio != -1.0 or fs_hz != -1.0:
                    raise EdgeResultProtocolError("null_doppler_payload_invalid")
                metadata["doppler_valid"] = False
                metadata["doppler_ratio"] = None
                metadata["doppler_fs_hz"] = None
                metadata["doppler_samples"] = 0
            else:
                if not math.isfinite(ratio) or not 0.0 <= ratio <= 1.0:
                    raise EdgeResultProtocolError("doppler_ratio_invalid")
                if not math.isfinite(fs_hz) or fs_hz <= 0.0:
                    raise EdgeResultProtocolError("doppler_fs_hz_invalid")
                metadata["doppler_valid"] = True
                metadata["doppler_ratio"] = ratio
                metadata["doppler_fs_hz"] = fs_hz
                metadata["doppler_samples"] = int(samples)
        if offset != len(payload):
            raise EdgeResultProtocolError("payload_trailing_bytes")
    elif offset != len(payload):
        raise EdgeResultProtocolError("payload_trailing_bytes")
    result = EdgeResultV5(
        **strings,
        boot_id=boot_id, window_seq=window_seq,
        window_start_us=window_start_us, window_end_us=window_end_us,
        rx_timestamp_us=rx_timestamp_us, age_ms=age_ms,
        local_passability_score=(local_score if score_present else None),
        state=state, quality=quality, uncertainty=uncertainty,
        disagreement=(disagreement == 1), reason_code=reason_code,
        **versions, sample_count=sample_count, invalid_count=invalid_count,
        queue_drop_count=queue_drop_count, sequence_gap=sequence_gap,
        packet_loss_ratio=packet_loss_ratio, jitter_ms=jitter_ms, **metadata,
    )
    result.validate()
    return result


def decode_edge_result(datagram: bytes) -> EdgeResultV5:
    """Decode and strictly validate a complete EdgeResult V5 datagram."""
    if not isinstance(datagram, (bytes, bytearray, memoryview)):
        raise TypeError("datagram must be bytes-like")
    raw = bytes(datagram)
    if len(raw) < HEADER_SIZE:
        raise EdgeResultProtocolError("short_header")
    magic, protocol_version, message_type, payload_length, schema_version, packet_crc = HEADER.unpack_from(raw)
    if magic != MAGIC:
        raise EdgeResultProtocolError("wrong_magic")
    if protocol_version != PROTOCOL_VERSION:
        raise EdgeResultProtocolError("wrong_protocol_version")
    if message_type != MESSAGE_TYPE_EDGE_RESULT:
        raise EdgeResultProtocolError("wrong_message_type")
    if schema_version not in (SCHEMA_V5, SCHEMA_V6, SCHEMA_V7):
        raise EdgeResultProtocolError("wrong_schema_version")
    if payload_length > MAX_PAYLOAD_BYTES:
        raise EdgeResultProtocolError("payload_too_large")
    if payload_length != len(raw) - HEADER_SIZE:
        raise EdgeResultProtocolError("payload_length_mismatch")
    prefix = raw[:CRC_OFFSET]
    payload = raw[HEADER_SIZE:]
    expected_crc = crc32(prefix, payload)
    if expected_crc != packet_crc:
        raise EdgeResultProtocolError("crc_mismatch")
    return _unpack_payload(payload, schema_version=schema_version)


__all__ = [
    "MAGIC", "PROTOCOL_VERSION", "SCHEMA_VERSION", "SCHEMA_V5", "SCHEMA_V6", "SCHEMA_V7", "FEATURE_SCHEMA_VERSION",
    "MAX_STRING_BYTES", "MAX_MODEL_HASH_BYTES", "MAX_PAYLOAD_BYTES", "MAX_PACKET_BYTES",
    "MESSAGE_TYPE_EDGE_RESULT", "MSG_EDGE_RESULT", "EDGE_RESULT_MESSAGE_TYPE",
    "HEADER", "HEADER_SIZE", "EdgeResultProtocolError", "EdgeResultState",
    "EdgeResultReason", "EdgeResultV5", "crc32", "encode_edge_result", "decode_edge_result",
]
