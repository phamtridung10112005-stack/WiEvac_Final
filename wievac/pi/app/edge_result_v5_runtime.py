"""Software-only EdgeResult V5 Pi runtime.

This module is deliberately separate from the V4 compatibility runtime.  It
consumes one already-scored EdgeResult per link/window and never recalculates
CSI or a second per-link Formula score.  The same classes are used by replay,
the node simulator, and the dashboard adapter so stale/unknown behavior stays
consistent across those surfaces.
"""

from __future__ import annotations

import heapq
import hashlib
import json
import math
import os
import re
import time
from collections import Counter, deque
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Deque, Dict, List, Mapping, Optional, Tuple

try:  # Package import when launched from the repository root.
    from .edge_result_v5 import (
        EdgeResultProtocolError,
        EdgeResultV5,
        EdgeResultState,
        decode_edge_result,
    )
except ImportError:  # Direct script/module import used by some replay tools.
    from edge_result_v5 import (  # type: ignore
        EdgeResultProtocolError,
        EdgeResultV5,
        EdgeResultState,
        decode_edge_result,
    )


UNKNOWN_STATES = {"UNKNOWN", "STALE"}
VALID_STATES = {"PASSABLE", "DEGRADED", "BLOCKED", "UNKNOWN", "STALE"}
UDP_LOSS_UNVERIFIED = "UNVERIFIED_NO_RX_EMIT_COUNTER"
PACKET_LOSS_RATIO_SOURCE = "legacy_csi_callback_gap_not_udp"
_JSONL_STEM = re.compile(r"^[A-Za-z0-9._-]+$")


def jsonl_stem(name: str) -> str:
    key = str(name or "").strip()
    if not key or any(ch in key for ch in "\\/:\x00") or _JSONL_STEM.fullmatch(key) is None:
        raise ValueError("invalid_name")
    return key


def _state_name(value: Any) -> str:
    return str(getattr(value, "value", value)).upper()


def _finite(value: Any) -> bool:
    return isinstance(value, (int, float)) and math.isfinite(float(value))


def _as_dict(result: EdgeResultV5) -> Dict[str, Any]:
    if hasattr(result, "to_dict"):
        raw = dict(result.to_dict())  # type: ignore[no-any-return]
    else:
        raw = dict(result.__dict__)
    # Enum values must remain JSON-safe for recorder/API callers.
    for key, value in list(raw.items()):
        if hasattr(value, "value"):
            raw[key] = value.value
    raw.setdefault("score", raw.get("local_passability_score"))
    return raw


@dataclass(frozen=True)
class IngestDecision:
    accepted: bool
    reason: str
    link_id: Optional[str] = None
    result: Optional[EdgeResultV5] = None
    arrival_us: Optional[int] = None


@dataclass
class LinkRuntimeState:
    link_id: str
    latest: Optional[EdgeResultV5] = None
    boot_id: Optional[int] = None
    last_window_seq: Optional[int] = None
    latest_revision: int = 0
    accepted_count: int = 0
    rejected_count: int = 0
    duplicate_count: int = 0
    out_of_order_count: int = 0
    sequence_gap: int = 0
    history: Deque[EdgeResultV5] = field(default_factory=lambda: deque(maxlen=120))
    rejection_reasons: Deque[str] = field(default_factory=lambda: deque(maxlen=64))
    # Boot IDs are random, so numeric ordering cannot identify a reboot. Keep
    # a bounded retired set to reject delayed packets from an older stream.
    retired_boot_ids: Deque[int] = field(default_factory=lambda: deque(maxlen=8))
    retired_boot_lookup: set[int] = field(default_factory=set)
    latest_arrival_us: Optional[int] = None
    # Rejections do not alter the accepted stream. Retain only the most recent
    # bounded metadata so live diagnosis can identify a competing sender.
    last_rejected_packet: Optional[Dict[str, Any]] = None
    # A new producer boot normally starts at a low sequence.  Some receivers
    # restart after CSI has already advanced, so retain a bounded candidate
    # until two validated, monotonic packets confirm a new stream.
    boot_candidate_id: Optional[int] = None
    boot_candidate_last_seq: Optional[int] = None
    boot_candidate_last_timestamp_us: Optional[int] = None
    boot_candidate_count: int = 0

    def is_stale(self, now_us: int, stale_after_ms: int) -> bool:
        if self.latest is None or self.latest_arrival_us is None:
            return True
        age_ms = max(0, (now_us - self.latest_arrival_us) // 1000)
        return age_ms > stale_after_ms

    def age_ms(self, now_us: int) -> Optional[int]:
        if self.latest is None or self.latest_arrival_us is None:
            return None
        return max(0, (now_us - self.latest_arrival_us) // 1000)


class EdgeResultV5Ingestor:
    """Validate and retain V5 results with independent state per link."""

    def __init__(
        self,
        *,
        identities: Optional[Mapping[Any, Mapping[str, Any]]] = None,
        stale_after_ms: int = 3000,
        min_quality: float = 25.0,
        history_size: int = 120,
        allow_open_test_only: bool = False,
        max_dynamic_links: int = 64,
        boot_recovery_max_seq: int = 4,
    ) -> None:
        if stale_after_ms <= 0 or stale_after_ms > 86_400_000:
            raise ValueError("stale_after_ms must be positive and bounded")
        if not _finite(min_quality) or not 0.0 <= float(min_quality) <= 100.0:
            raise ValueError("min_quality must be in [0, 100]")
        if history_size < 4 or history_size > 10_000:
            raise ValueError("history_size out of bounds")
        if max_dynamic_links < 1 or max_dynamic_links > 4096:
            raise ValueError("max_dynamic_links out of bounds")
        if boot_recovery_max_seq < 1 or boot_recovery_max_seq > 1024:
            raise ValueError("boot_recovery_max_seq out of bounds")
        self.stale_after_ms = int(stale_after_ms)
        self.min_quality = float(min_quality)
        self.allow_open_test_only = bool(allow_open_test_only)
        self.max_dynamic_links = int(max_dynamic_links)
        self.boot_recovery_max_seq = int(boot_recovery_max_seq)
        self.history_size = int(history_size)
        self.identities: Dict[str, Dict[str, Any]] = {
            str(k): {str(name): value for name, value in dict(v).items()}
            for k, v in (identities or {}).items()
        }
        self.links: Dict[str, LinkRuntimeState] = {}
        for link_id in self.identities:
            self.links[link_id] = LinkRuntimeState(link_id, history=deque(maxlen=self.history_size))
        self.total_received = 0
        self.total_rejected = 0
        self.rejection_reasons: Deque[str] = deque(maxlen=256)
        self.unknown_link_rejections: Deque[str] = deque(maxlen=256)
        self._rejection_counts: Counter[str] = Counter()
        self._revision = 0

    def _state_for(self, link_id: Any) -> LinkRuntimeState:
        key = str(link_id)
        state = self.links.get(key)
        if state is None:
            if len(self.links) >= self.max_dynamic_links:
                raise RuntimeError("dynamic_link_limit")
            state = LinkRuntimeState(key, history=deque(maxlen=self.history_size))
            self.links[key] = state
        return state

    def upsert_identity(self, link_id: Any, identity: Mapping[str, Any]) -> None:
        """Add or replace one allow-listed link without dropping live ingest."""
        key = str(link_id).strip()
        if not key:
            raise ValueError("missing_fields")
        if key not in self.identities and len(self.identities) >= self.max_dynamic_links:
            raise RuntimeError("dynamic_link_limit")
        self.identities[key] = {str(name): value for name, value in dict(identity).items()}
        if key not in self.links:
            self.links[key] = LinkRuntimeState(key, history=deque(maxlen=self.history_size))
        self._revision += 1

    def remove_identity(self, link_id: Any) -> bool:
        """Drop one allow-listed link so later packets become unknown_link."""
        key = str(link_id)
        existed = key in self.identities or key in self.links
        self.identities.pop(key, None)
        self.links.pop(key, None)
        if existed:
            self._revision += 1
        return existed

    def _reject(
        self,
        reason: str,
        link_id: Optional[Any] = None,
        result: Optional[EdgeResultV5] = None,
        arrival_us: Optional[int] = None,
    ) -> IngestDecision:
        self.total_rejected += 1
        self.rejection_reasons.append(reason)
        self._rejection_counts[reason] += 1
        if link_id is not None and str(link_id) in self.links:
            state = self.links[str(link_id)]
            state.rejected_count += 1
            state.rejection_reasons.append(reason)
            if reason == "duplicate":
                state.duplicate_count += 1
            elif reason in {"out_of_order", "sequence_gap_rejected"}:
                state.out_of_order_count += 1
            candidate: Dict[str, Any] = {
                "reason": str(reason),
                "arrival_timestamp_us": int(arrival_us) if arrival_us is not None else None,
            }
            if result is not None:
                decoded = _as_dict(result)
                for key in (
                    "device_id", "node_id", "tx_id", "rx_id", "link_id",
                    "boot_id", "window_seq", "window_start_us", "window_end_us",
                    "rx_timestamp_us",
                ):
                    candidate[key] = decoded.get(key)
            state.last_rejected_packet = candidate
        return IngestDecision(False, reason, None if link_id is None else str(link_id), result, arrival_us)

    def _identity_ok(self, result: EdgeResultV5) -> bool:
        link_id = str(getattr(result, "link_id"))
        expected = self.identities.get(link_id)
        if expected is None:
            return not self.identities
        for name in ("device_id", "node_id", "tx_id", "rx_id", "corridor_id"):
            if name in expected and str(getattr(result, name)) != str(expected[name]):
                return False
        return True

    def ingest(
        self,
        packet: bytes,
        *,
        now_us: Optional[int] = None,
        expected_endpoint: Optional[Tuple[str, int]] = None,
        endpoint: Optional[Tuple[str, int]] = None,
    ) -> IngestDecision:
        """Decode one datagram and apply identity/sequence/freshness gates."""

        # ``now_us`` is retained as a deterministic test/replay arrival clock;
        # it is never compared with the ESP boot-relative packet timestamp.
        arrival_us = int(now_us if now_us is not None else time.monotonic_ns() // 1000)
        self.total_received += 1
        try:
            result = decode_edge_result(packet)
        except (EdgeResultProtocolError, ValueError, TypeError, OverflowError) as exc:
            return self._reject(f"decode:{type(exc).__name__}", arrival_us=arrival_us)

        link_id = str(getattr(result, "link_id"))
        if expected_endpoint is not None and endpoint != expected_endpoint:
            return self._reject("identity:endpoint", link_id, result, arrival_us)
        if not self._identity_ok(result):
            if self.identities and link_id not in self.identities:
                self.unknown_link_rejections.append(link_id)
                return self._reject("identity:unknown_link", link_id, result, arrival_us)
            return self._reject("identity:mismatch", link_id, result, arrival_us)

        # Never allocate state for an unconfigured/forged link. Open mode is
        # deliberately opt-in for deterministic replay only and remains
        # bounded against arbitrary link-ID allocation.
        if link_id not in self.links:
            if self.identities and link_id not in self.identities:
                self.unknown_link_rejections.append(link_id)
                return self._reject("identity:unknown_link", link_id, result, arrival_us)
            if not self.allow_open_test_only:
                self.unknown_link_rejections.append(link_id)
                return self._reject("identity:allowlist_required", link_id, result, arrival_us)
        try:
            state = self._state_for(link_id)
        except RuntimeError:
            self.unknown_link_rejections.append(link_id)
            return self._reject("identity:dynamic_link_limit", link_id, result, arrival_us)

        boot_id = int(getattr(result, "boot_id"))
        window_seq = int(getattr(result, "window_seq"))
        if boot_id == 0 or window_seq == 0:
            return self._reject("identity:zero_boot_or_sequence", link_id, result, arrival_us)

        # A random boot ID has no numeric ordering. A candidate stream may
        # replace the active stream only at a low sequence (or explicit clock
        # reset), after every packet validation gate below has passed. Retired
        # IDs reject delayed packets from a previous stream.
        boot_switch = state.boot_id is not None and boot_id != state.boot_id
        if boot_switch and boot_id in state.retired_boot_lookup:
            return self._reject("old_boot", link_id, result, arrival_us)
        previous = state.latest
        if state.last_window_seq is not None:
            if not boot_switch:
                if window_seq == state.last_window_seq:
                    return self._reject("duplicate", link_id, result, arrival_us)
                if window_seq < state.last_window_seq:
                    return self._reject("out_of_order", link_id, result, arrival_us)

        rx_timestamp_us = int(getattr(result, "rx_timestamp_us"))
        window_start_us = int(getattr(result, "window_start_us"))
        window_end_us = int(getattr(result, "window_end_us"))
        if window_start_us > window_end_us or window_end_us > rx_timestamp_us:
            return self._reject("timestamp:window_domain", link_id, result, arrival_us)
        if previous is not None and not boot_switch:
            previous_end = int(getattr(state.latest, "window_end_us"))
            previous_rx = int(getattr(state.latest, "rx_timestamp_us"))
            if window_end_us <= previous_end or rx_timestamp_us <= previous_rx:
                return self._reject("timestamp:nonmonotonic", link_id, result, arrival_us)

        quality = getattr(result, "quality", None)
        if quality is not None and (not _finite(quality) or not 0.0 <= float(quality) <= 100.0):
            return self._reject("quality:range", link_id, result, arrival_us)

        score = getattr(result, "local_passability_score", None)
        state_name = _state_name(getattr(result, "state"))
        if state_name not in VALID_STATES:
            return self._reject("state:unknown", link_id, result, arrival_us)
        if state_name in UNKNOWN_STATES and score is not None:
            return self._reject("unknown_score_nonnull", link_id, result, arrival_us)
        if score is not None and (not _finite(score) or not 0.0 <= float(score) <= 100.0):
            return self._reject("score:range", link_id, result, arrival_us)

        if boot_switch and window_seq > self.boot_recovery_max_seq:
            # Do not let one arbitrary high-sequence datagram reset a live
            # stream. Require a second fully validated packet from the same
            # candidate boot with strictly advancing sequence and timestamp.
            if state.boot_candidate_id != boot_id:
                state.boot_candidate_id = boot_id
                state.boot_candidate_last_seq = window_seq
                state.boot_candidate_last_timestamp_us = rx_timestamp_us
                state.boot_candidate_count = 1
                return self._reject("boot_reset_pending", link_id, result, arrival_us)
            if (state.boot_candidate_last_seq is None or
                    state.boot_candidate_last_timestamp_us is None or
                    window_seq <= state.boot_candidate_last_seq or
                    rx_timestamp_us <= state.boot_candidate_last_timestamp_us):
                return self._reject("boot_reset_not_confirmed", link_id, result, arrival_us)
            state.boot_candidate_last_seq = window_seq
            state.boot_candidate_last_timestamp_us = rx_timestamp_us
            state.boot_candidate_count += 1
            if state.boot_candidate_count < 2:
                return self._reject("boot_reset_pending", link_id, result, arrival_us)

        # Commit stream identity and clear old state only after all candidate
        # packet gates have passed (prevents malformed high-boot DoS resets).
        if boot_switch:
            old_boot = state.boot_id
            if old_boot is not None and old_boot not in state.retired_boot_lookup:
                if len(state.retired_boot_ids) >= state.retired_boot_ids.maxlen:
                    evicted = state.retired_boot_ids.popleft()
                    state.retired_boot_lookup.discard(evicted)
                state.retired_boot_lookup.add(old_boot)
                state.retired_boot_ids.append(old_boot)
            state.boot_id = boot_id
            state.last_window_seq = None
            state.history.clear()
            state.latest = None
            state.latest_arrival_us = None
            state.sequence_gap = 0
            state.boot_candidate_id = None
            state.boot_candidate_last_seq = None
            state.boot_candidate_last_timestamp_us = None
            state.boot_candidate_count = 0
        elif state.boot_id is None:
            state.boot_id = boot_id
        else:
            # An accepted packet from the active stream makes an abandoned
            # boot candidate ineligible for a later delayed replay.
            state.boot_candidate_id = None
            state.boot_candidate_last_seq = None
            state.boot_candidate_last_timestamp_us = None
            state.boot_candidate_count = 0

        if state.last_window_seq is not None:
            state.sequence_gap = max(0, window_seq - state.last_window_seq - 1)
        state.latest = result
        state.last_window_seq = window_seq
        state.latest_arrival_us = int(arrival_us if arrival_us is not None else time.monotonic_ns() // 1000)
        state.accepted_count += 1
        state.latest_revision += 1
        state.history.append(result)
        self._revision += 1
        return IngestDecision(True, "accepted", link_id, result, state.latest_arrival_us)

    def _public_link(self, state: LinkRuntimeState, now_us: int) -> Dict[str, Any]:
        latest = state.latest
        age_ms = state.age_ms(now_us)
        stale = state.is_stale(now_us, self.stale_after_ms)
        if latest is None:
            return {
                "link_id": state.link_id,
                "state": "UNKNOWN",
                "score": None,
                "quality": None,
                "uncertainty": None,
                "age_ms": None,
                "arrival_timestamp_us": None,
                "reason": "no_data",
                "revision": state.latest_revision,
                "accepted_count": state.accepted_count,
                "rejected_count": state.rejected_count,
                "received_count": 0,
                "duplicate_count": state.duplicate_count,
                "out_of_order_count": state.out_of_order_count,
                "decode_error_count": 0,
                "sequence_gap": 0,
                "csi_sequence_gap": 0,
                "pi_window_sequence_gap": 0,
                "pi_window_gap_ratio": None,
                "csi_gap_ratio": None,
                "queue_drop_ratio": None,
                "invalid_csi_ratio": None,
                "udp_loss_ratio": None,
                "udp_loss_status": UDP_LOSS_UNVERIFIED,
                "packet_loss_ratio": None,
                "legacy_packet_loss_ratio": None,
                "packet_loss_ratio_source": PACKET_LOSS_RATIO_SOURCE,
                "last_packet_age_ms": None,
                "last_rejection_reason": state.rejection_reasons[-1] if state.rejection_reasons else None,
                "last_rejected_packet": state.last_rejected_packet,
            }
        data = _as_dict(latest)
        if stale:
            data["state"] = "STALE"
            data["local_passability_score"] = None
            reason = "stale"
        else:
            reason = str(data.get("reason_code", data.get("reason", "")))
        # These are different clocks. The wire field is the RX/CSI continuity
        # counter; the Pi counter is only a gap between accepted EdgeResult
        # windows and cannot be called UDP loss without an RX emit counter.
        csi_sequence_gap = max(int(data.get("sequence_gap", 0) or 0), 0)
        pi_window_sequence_gap = max(int(state.sequence_gap), 0)
        sample_count = max(int(data.get("sample_count", 0) or 0), 0)
        queue_drop_count = max(int(data.get("queue_drop_count", 0) or 0), 0)
        csi_sequence_total = sample_count + csi_sequence_gap
        queue_total = sample_count + queue_drop_count
        invalid_csi_ratio = (
            int(data.get("invalid_count", 0) or 0) / sample_count
            if sample_count else 0.0
        )
        csi_gap_ratio = csi_sequence_gap / csi_sequence_total if csi_sequence_total else 0.0
        pi_window_total = state.accepted_count + pi_window_sequence_gap
        pi_window_gap_ratio = (
            pi_window_sequence_gap / pi_window_total if pi_window_total else 0.0
        )
        queue_drop_ratio = queue_drop_count / queue_total if queue_total else 0.0
        legacy_packet_loss_ratio = data.get("packet_loss_ratio")
        data.update(
            {
                "link_id": state.link_id,
                "score": None if stale else data.get("local_passability_score"),
                "age_ms": age_ms,
                "arrival_timestamp_us": state.latest_arrival_us,
                "stale": stale,
                "reason": reason,
                "revision": state.latest_revision,
                "accepted_count": state.accepted_count,
                "rejected_count": state.rejected_count,
                "received_count": state.accepted_count + state.rejected_count,
                "duplicate_count": state.duplicate_count,
                "out_of_order_count": state.out_of_order_count,
                "decode_error_count": 0,
                "last_rejection_reason": state.rejection_reasons[-1] if state.rejection_reasons else None,
                "last_rejected_packet": state.last_rejected_packet,
                "sequence_gap": csi_sequence_gap,
                "csi_sequence_gap": csi_sequence_gap,
                "pi_window_sequence_gap": pi_window_sequence_gap,
                "pi_window_gap_ratio": pi_window_gap_ratio,
                "csi_gap_ratio": csi_gap_ratio,
                "queue_drop_ratio": queue_drop_ratio,
                "invalid_csi_ratio": invalid_csi_ratio,
                "udp_loss_ratio": None,
                "udp_loss_status": UDP_LOSS_UNVERIFIED,
                # Keep the decoded value for forensics, but make the public
                # packet-loss name non-numeric so it cannot be mistaken for
                # RX-to-Pi UDP loss.
                "packet_loss_ratio": None,
                "legacy_packet_loss_ratio": legacy_packet_loss_ratio,
                "packet_loss_ratio_source": PACKET_LOSS_RATIO_SOURCE,
                "last_packet_age_ms": age_ms,
            }
        )
        return data

    def latest_by_link(self, *, now_us: Optional[int] = None) -> Dict[str, Dict[str, Any]]:
        now_us = int(time.monotonic_ns() // 1000 if now_us is None else now_us)
        return {link_id: self._public_link(state, now_us) for link_id, state in sorted(self.links.items())}

    def quality_summary(self, *, now_us: Optional[int] = None) -> Dict[str, Any]:
        latest = self.latest_by_link(now_us=now_us)
        quality = [float(v["quality"]) for v in latest.values() if _finite(v.get("quality")) and not v.get("stale")]
        csi_gaps = [
            float(v["csi_gap_ratio"])
            for v in latest.values()
            if not v.get("stale") and _finite(v.get("csi_gap_ratio"))
        ]
        queue_drops = [
            float(v["queue_drop_ratio"])
            for v in latest.values()
            if not v.get("stale") and _finite(v.get("queue_drop_ratio"))
        ]
        invalid_csi = [
            float(v["invalid_csi_ratio"])
            for v in latest.values()
            if not v.get("stale") and _finite(v.get("invalid_csi_ratio"))
        ]
        legacy_csi_gaps = [
            float(v["legacy_packet_loss_ratio"])
            for v in latest.values()
            if not v.get("stale") and _finite(v.get("legacy_packet_loss_ratio"))
        ]
        pi_ingest = self.pi_ingest_summary(now_us=now_us, latest=latest)
        return {
            "revision": self._revision,
            "link_count": len(latest),
            "valid_link_count": sum(v.get("state") in {"PASSABLE", "DEGRADED", "BLOCKED"} and not v.get("stale") for v in latest.values()),
            "unknown_link_count": sum(v.get("state") in {"UNKNOWN", "STALE"} or v.get("stale") for v in latest.values()),
            "quality_mean": (sum(quality) / len(quality)) if quality else None,
            # Deprecated compatibility key. No source-independent packet loss
            # percentage exists without an RX emitted/sent counter binding.
            "packet_loss_mean": None,
            "legacy_csi_gap_mean": (sum(legacy_csi_gaps) / len(legacy_csi_gaps)) if legacy_csi_gaps else None,
            "csi_gap_mean": (sum(csi_gaps) / len(csi_gaps)) if csi_gaps else None,
            "queue_drop_mean": (sum(queue_drops) / len(queue_drops)) if queue_drops else None,
            "invalid_csi_mean": (sum(invalid_csi) / len(invalid_csi)) if invalid_csi else None,
            "udp_loss_mean": None,
            "udp_loss_status": UDP_LOSS_UNVERIFIED,
            "packet_loss_ratio_source": PACKET_LOSS_RATIO_SOURCE,
            "pi_ingest": pi_ingest,
            "total_received": self.total_received,
            "total_rejected": self.total_rejected,
            "rejection_reasons": dict(sorted(self._rejection_counts.items())),
            "unknown_link_rejections": len(self.unknown_link_rejections),
            "identity_mode": "OPEN_TEST_ONLY" if self.allow_open_test_only else "ALLOWLIST_REQUIRED",
        }

    def pi_ingest_summary(
        self,
        *,
        now_us: Optional[int] = None,
        latest: Optional[Mapping[str, Mapping[str, Any]]] = None,
    ) -> Dict[str, Any]:
        """Return Pi-side UDP ingest counters, separate from RX CSI metrics."""
        current = latest if latest is not None else self.latest_by_link(now_us=now_us)
        ages = [int(v["age_ms"]) for v in current.values() if v.get("age_ms") is not None]
        accepted = sum(int(v.get("accepted_count", 0) or 0) for v in current.values())
        duplicate = sum(int(v.get("duplicate_count", 0) or 0) for v in current.values())
        out_of_order = sum(int(v.get("out_of_order_count", 0) or 0) for v in current.values())
        decode_error = sum(
            count for reason, count in self._rejection_counts.items()
            if str(reason).startswith("decode:")
        )
        return {
            "received": self.total_received,
            "accepted": accepted,
            "rejected": self.total_rejected,
            "duplicate": duplicate,
            "out_of_order": out_of_order,
            "decode_error": decode_error,
            "last_packet_age_ms": min(ages) if ages else None,
            "udp_loss_ratio": None,
            "udp_loss_status": UDP_LOSS_UNVERIFIED,
        }


class TrendForecaster:
    """Deterministic fallback trend; no model is implied when model is absent."""

    def __init__(self, history_size: int = 32) -> None:
        self.history_size = max(4, min(512, int(history_size)))
        self._scores: Dict[str, Deque[Tuple[int, float]]] = {}
        self._arrivals: Dict[str, int] = {}
        self.model_state = "NOT_READY"

    def observe(self, result: EdgeResultV5, *, arrival_us: Optional[int] = None) -> None:
        score = getattr(result, "local_passability_score", None)
        if not _finite(score) or _state_name(getattr(result, "state")) in UNKNOWN_STATES:
            return
        link_id = str(getattr(result, "link_id"))
        values = self._scores.setdefault(link_id, deque(maxlen=self.history_size))
        values.append((int(getattr(result, "window_end_us")), float(score)))
        self._arrivals[link_id] = int(arrival_us if arrival_us is not None else time.monotonic_ns() // 1000)

    def trend(self, link_id: Any, *, now_us: Optional[int] = None, stale_after_ms: int = 3000) -> Dict[str, Any]:
        link_key = str(link_id)
        values = list(self._scores.get(link_key, ()))
        arrival = self._arrivals.get(link_key)
        now_us = int(time.monotonic_ns() // 1000 if now_us is None else now_us)
        age_ms = None if arrival is None else max(0, (now_us - arrival) // 1000)
        if age_ms is None or age_ms > stale_after_ms:
            return {
                "link_id": link_key, "trend": "UNKNOWN", "delta": None,
                "model_state": self.model_state, "age_ms": age_ms, "fresh": False,
            }
        if len(values) < 2:
            return {"link_id": link_key, "trend": "WARMING_UP", "delta": None, "model_state": self.model_state, "age_ms": age_ms, "fresh": True}
        delta = values[-1][1] - values[0][1]
        if abs(delta) < 1.0:
            label = "STABLE"
        elif delta > 0:
            label = "IMPROVING"
        else:
            label = "WORSENING"
        return {"link_id": link_key, "trend": label, "delta": round(delta, 4), "model_state": self.model_state, "age_ms": age_ms, "fresh": True}


class DirectionRouter:
    """Dijkstra over valid links; trend is informational, not a route input."""

    def __init__(self, ingest: EdgeResultV5Ingestor, trend: Optional[TrendForecaster] = None) -> None:
        self.ingest = ingest
        self.trend = trend or TrendForecaster()
        self._edges: Dict[str, List[Tuple[str, str]]] = {}

    def add_link(self, source: str, target: str, link_id: Any) -> None:
        self._edges.setdefault(str(source), []).append((str(target), str(link_id)))

    def _cost(self, link: Mapping[str, Any]) -> Optional[float]:
        if link.get("stale") or _state_name(link.get("state")) in UNKNOWN_STATES or _state_name(link.get("state")) == "BLOCKED":
            return None
        if int(link.get("sequence_gap", 0) or 0) > 0 or int(link.get("queue_drop_count", 0) or 0) > 0:
            return None
        quality = link.get("quality")
        score = link.get("score")
        uncertainty = link.get("uncertainty")
        if not (_finite(quality) and _finite(score)) or float(quality) < self.ingest.min_quality:
            return None
        uncertainty_value = float(uncertainty) if _finite(uncertainty) else 1.0
        return 1.0 + (100.0 - float(score)) / 100.0 + uncertainty_value

    def route(self, source: str, target: str, *, now_us: Optional[int] = None) -> Dict[str, Any]:
        links = self.ingest.latest_by_link(now_us=now_us)
        if not self._edges:
            return {"route_state": "NO_ROUTE", "reason": "config_missing", "algorithm": "DIJKSTRA", "cost_basis": "score_quality_uncertainty", "trend_influence": False, "cost": None, "path": []}
        source, target = str(source), str(target)
        queue: List[Tuple[float, str, List[str]]] = [(0.0, source, [source])]
        seen: Dict[str, float] = {}
        while queue:
            cost, node, path = heapq.heappop(queue)
            if node == target:
                return {"route_state": "READY", "algorithm": "DIJKSTRA", "cost_basis": "score_quality_uncertainty", "trend_influence": False, "cost": cost, "path": path}
            if cost >= seen.get(node, float("inf")):
                continue
            seen[node] = cost
            for neighbor, link_id in self._edges.get(node, []):
                edge = links.get(str(link_id))
                if edge is None:
                    continue
                edge_cost = self._cost(edge)
                if edge_cost is not None:
                    heapq.heappush(queue, (cost + edge_cost, neighbor, path + [neighbor]))
        return {"route_state": "NO_ROUTE", "algorithm": "DIJKSTRA", "cost_basis": "score_quality_uncertainty", "trend_influence": False, "cost": None, "path": []}


class DataRoutePolicy:
    """Telemetry policy snapshot, not an active retry/time-slot scheduler."""

    def __init__(self, *, packet_rate_hz: float = 20.0, retry_limit: int = 2, queue_limit: int = 32) -> None:
        if not _finite(packet_rate_hz) or packet_rate_hz <= 0 or packet_rate_hz > 1000:
            raise ValueError("packet_rate_hz out of bounds")
        if retry_limit < 0 or retry_limit > 8 or queue_limit < 1 or queue_limit > 4096:
            raise ValueError("scheduler bounds invalid")
        self.packet_rate_hz = float(packet_rate_hz)
        self.retry_limit = int(retry_limit)
        self.queue_limit = int(queue_limit)

    def snapshot(self, links: Mapping[Any, Mapping[str, Any]]) -> Dict[str, Any]:
        return {
            "routing_kind": "DATA_TRANSPORT",
            "scheduler_active": False,
            "packet_rate_hz": self.packet_rate_hz,
            "retry_limit": self.retry_limit,
            "queue_limit": self.queue_limit,
            "links": {
                str(link_id): {
                    "csi_gap_ratio": value.get("csi_gap_ratio"),
                    "queue_drop_ratio": value.get("queue_drop_ratio"),
                    "invalid_csi_ratio": value.get("invalid_csi_ratio"),
                    "jitter_ms": value.get("jitter_ms"),
                    "age_ms": value.get("age_ms"),
                    "state": value.get("state"),
                }
                for link_id, value in links.items()
            },
        }


class EdgeResultRecorder:
    """Append compact RUN records into one durable JSONL file per link."""

    def __init__(self, directory: os.PathLike[str] | str) -> None:
        self.directory = Path(directory)
        self.directory.mkdir(parents=True, exist_ok=True)
        self.path: Optional[Path] = None
        self.session_id: Optional[str] = None
        self.active = False
        self.count = 0
        self._handles: dict[str, Any] = {}
        self._paths: dict[str, Path] = {}

    @property
    def paths(self) -> dict[str, Path]:
        return dict(self._paths)

    def start(self, session_id: str) -> Path:
        if not session_id or any(ch in session_id for ch in "\\/:\x00"):
            raise ValueError("invalid session_id")
        if self.active:
            self.close(success=True)
        self.session_id = session_id
        self.active = True
        self.count = 0
        self.path = self.directory
        return self.directory

    def _handle_for(self, link_id: Optional[str]) -> Any:
        stem = jsonl_stem(link_id) if link_id else "_rejected"
        handle = self._handles.get(stem)
        if handle is not None:
            return handle
        path = self.directory / f"{stem}.jsonl"
        handle = path.open("a", encoding="utf-8", newline="\n")
        self._handles[stem] = handle
        self._paths[stem] = path
        self.path = path
        return handle

    def ensure_link(self, link_id: str) -> Path:
        stem = jsonl_stem(link_id)
        self._handle_for(link_id)
        return self._paths[stem]

    def _write_row(self, handle: Any, row: Mapping[str, Any]) -> None:
        if self.session_id:
            row = dict(row)
            row["capture_session"] = self.session_id
        handle.write(json.dumps(row, ensure_ascii=False, separators=(",", ":")) + "\n")
        handle.flush()
        self.count += 1

    def append(self, result: EdgeResultV5, *, rejection: Optional[str] = None, arrival_us: Optional[int] = None) -> None:
        if not self.active:
            raise RuntimeError("recorder is not active")
        row = _as_dict(result)
        row = {key: value for key, value in row.items() if key not in {
            "raw_csi", "shape_bins", "feature_vector",
            "doppler_valid", "doppler_ratio", "doppler_fs_hz", "doppler_samples",
        }}
        row["record_type"] = "edge_result_v5"
        if arrival_us is not None:
            row["arrival_timestamp_us"] = int(arrival_us)
        if rejection is not None:
            row["accepted"] = False
            row["rejection_reason"] = rejection
        else:
            row["accepted"] = True
        handle = self._handle_for(str(row.get("link_id") or "") or None)
        self._write_row(handle, row)
        if rejection is None:
            self._append_doppler(result, arrival_us=arrival_us)

    def _append_doppler(self, result: EdgeResultV5, *, arrival_us: Optional[int] = None) -> None:
        link_id = str(result.link_id or "") or None
        stem = (jsonl_stem(link_id) if link_id else "_rejected") + "-doppler"
        handle = self._handles.get(stem)
        if handle is None:
            path = self.directory / f"{stem}.jsonl"
            handle = path.open("a", encoding="utf-8", newline="\n")
            self._handles[stem] = handle
            self._paths[stem] = path
        row: Dict[str, Any] = {
            "record_type": "doppler_shadow",
            "link_id": result.link_id,
            "window_seq": int(result.window_seq),
            "window_start_us": int(result.window_start_us),
            "window_end_us": int(result.window_end_us),
            "rx_timestamp_us": int(result.rx_timestamp_us),
            "state": result.state.value if hasattr(result.state, "value") else str(result.state),
            "local_passability_score": result.local_passability_score,
            "raw_evidence_score": result.raw_evidence_score,
            "occupancy_evidence": result.occupancy_evidence,
            "doppler_valid": bool(result.doppler_valid),
            "doppler_ratio": None if result.doppler_ratio is None else float(result.doppler_ratio),
            "doppler_fs_hz": None if result.doppler_fs_hz is None else float(result.doppler_fs_hz),
            "doppler_samples": int(result.doppler_samples),
        }
        if arrival_us is not None:
            row["arrival_timestamp_us"] = int(arrival_us)
        if self.session_id:
            row["capture_session"] = self.session_id
        handle.write(json.dumps(row, ensure_ascii=False, separators=(",", ":")) + "\n")
        handle.flush()

    def append_rejection(
        self,
        *,
        reason: str,
        received_at_us: int,
        packet: Optional[bytes] = None,
        endpoint: Optional[Tuple[str, int]] = None,
        result: Optional[EdgeResultV5] = None,
        link_id: Optional[str] = None,
    ) -> None:
        """Persist rejected datagrams without fabricating an EdgeResult.

        A decoded candidate is included only when decoding succeeded; malformed
        packets remain representable through bounded metadata only; the packet
        body is never persisted by the compact recorder.
        """
        if not self.active:
            raise RuntimeError("recorder is not active")
        row: Dict[str, Any] = {
            "record_type": "edge_result_v5_rejection",
            "accepted": False,
            "rejection_reason": str(reason),
            "received_at_us": int(received_at_us),
            "link_id": None if link_id is None else str(link_id),
            "endpoint": list(endpoint) if endpoint is not None else None,
            "packet_length": len(packet) if packet is not None else None,
        }
        if packet is not None:
            # Rejection records retain bounded forensic identity only.  Never
            # persist the datagram body (which may contain sensitive payloads).
            row["packet_sha256"] = hashlib.sha256(bytes(packet)).hexdigest()
        if result is not None:
            decoded = _as_dict(result)
            for key in ("device_id", "node_id", "tx_id", "rx_id", "link_id", "corridor_id", "session_id", "boot_id", "window_seq", "window_start_us", "window_end_us", "rx_timestamp_us"):
                if key in decoded:
                    row[key] = decoded[key]
        routed = str(row.get("link_id") or link_id or "") or None
        handle = self._handle_for(routed)
        self._write_row(handle, row)

    def close(self, *, success: bool = True) -> Optional[Path]:
        if not self.active and not self._handles:
            return None
        for handle in self._handles.values():
            handle.flush()
            os.fsync(handle.fileno())
            handle.close()
        self._handles = {}
        kept = dict(self._paths)
        self._paths = {}
        self.active = False
        self.session_id = None
        if not kept:
            self.path = self.directory
            return self.directory
        self.path = next(iter(kept.values())) if len(kept) == 1 else self.directory
        return self.path


class EdgeResultV5Api:
    """Small dashboard-facing API adapter with revision/sequence freshness."""

    def __init__(self, ingest: EdgeResultV5Ingestor, trend: Optional[TrendForecaster] = None, router: Optional[DirectionRouter] = None, scheduler: Optional[DataRoutePolicy] = None, *, enable_reference_modules: bool = False) -> None:
        self.ingest = ingest
        self.reference_modules_enabled = bool(enable_reference_modules)
        self.trend = (trend or TrendForecaster()) if self.reference_modules_enabled else None
        self.router = (router or DirectionRouter(ingest, self.trend)) if self.reference_modules_enabled else None
        self.scheduler = (scheduler or DataRoutePolicy()) if self.reference_modules_enabled else None

    def observe(self, result: EdgeResultV5, *, arrival_us: Optional[int] = None) -> None:
        if self.trend is not None:
            self.trend.observe(result, arrival_us=arrival_us)

    def latest(self, *, now_us: Optional[int] = None) -> Dict[str, Any]:
        summary = self.ingest.quality_summary(now_us=now_us)
        return {"revision": summary["revision"], "links": self.ingest.latest_by_link(now_us=now_us), "pi_ingest": summary["pi_ingest"]}

    def overview(self, *, now_us: Optional[int] = None) -> Dict[str, Any]:
        links = self.ingest.latest_by_link(now_us=now_us)
        summary = self.ingest.quality_summary(now_us=now_us)
        active = [v for v in links.values() if v.get("state") in {"PASSABLE", "DEGRADED"} and not v.get("stale")]
        blocked = [v for v in links.values() if v.get("state") == "BLOCKED" and not v.get("stale")]
        return {
            "system_state": "READY" if active else ("BLOCKED" if blocked else "UNKNOWN"),
            "last_revision": summary["revision"],
            "links": links,
            "quality": summary,
            "pi_ingest": summary["pi_ingest"],
            "trend_model_state": self.trend.model_state if self.trend is not None else "NOT_MOUNTED",
        }

    def trends(self, *, now_us: Optional[int] = None) -> Dict[str, Any]:
        if self.trend is None:
            return {"state": "REFERENCE_ONLY_UNMOUNTED", "links": {}}
        latest = self.ingest.latest_by_link(now_us=now_us)
        output: Dict[str, Any] = {}
        for link_id in self.ingest.links:
            item = self.trend.trend(link_id, now_us=now_us, stale_after_ms=self.ingest.stale_after_ms)
            current = latest.get(str(link_id), {})
            item["state"] = current.get("state", "UNKNOWN")
            item["stale"] = bool(current.get("stale", current.get("state") in UNKNOWN_STATES))
            output[str(link_id)] = item
        return output

    def route(self, source: str, target: str, *, now_us: Optional[int] = None) -> Dict[str, Any]:
        if self.router is None:
            return {"route_state": "NO_ROUTE", "reason": "reference_only_unmounted", "path": [], "cost": None}
        return self.router.route(source, target, now_us=now_us)

    def transport(self, *, now_us: Optional[int] = None) -> Dict[str, Any]:
        if self.scheduler is None:
            return {"state": "REFERENCE_ONLY_UNMOUNTED", "links": {}}
        return self.scheduler.snapshot(self.ingest.latest_by_link(now_us=now_us))
