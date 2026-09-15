"""Deterministic software-only EdgeResult V5 transport simulator.

The simulator exercises Pi-side transport behavior without pretending to model
radio physics. Each virtual node owns one link, one bounded queue, and one
sequence tracker. Faults are injected at transport boundaries so packet loss,
duplicates, reordering, queue pressure, and isolated-link failures remain
observable in the resulting counters.
"""

from __future__ import annotations

import argparse
import json
import math
import random
import sys
from collections import deque
from dataclasses import dataclass, field
from typing import Deque, Dict, Iterable, Mapping, Optional


EDGE_STATES = frozenset({"PASSABLE", "DEGRADED", "BLOCKED", "UNKNOWN"})
DEFAULT_NODE_COUNT = 24
SEQUENCE_HISTORY_LIMIT = 256


@dataclass(frozen=True)
class EdgeResultSample:
    """Small in-memory representation of one per-link EdgeResult window."""

    node_id: int
    link_id: int
    tx_id: int
    rx_id: int
    corridor_id: str
    boot_id: int
    window_seq: int
    window_start_us: int
    window_end_us: int
    score: Optional[float]
    state: str
    quality: float
    uncertainty: float
    reason_code: str = "SIMULATED"
    # Optional for hand-authored samples.  The window end is used when omitted;
    # explicit zero/early values are rejected below.
    rx_timestamp_us: Optional[int] = None


@dataclass
class LinkFault:
    """Transport fault probabilities for one virtual link."""

    loss_probability: float = 0.0
    duplicate_probability: float = 0.0
    reorder_probability: float = 0.0

    def __post_init__(self) -> None:
        for name in ("loss_probability", "duplicate_probability", "reorder_probability"):
            value = float(getattr(self, name))
            if not math.isfinite(value) or not 0.0 <= value <= 1.0:
                raise ValueError(f"{name} must be finite and within [0, 1]")
            setattr(self, name, value)


@dataclass
class LinkSnapshot:
    """Cumulative counters and latest accepted sample for one link."""

    node_id: int
    link_id: int
    generated: int = 0
    network_lost: int = 0
    enqueued: int = 0
    queue_drops: int = 0
    delivered: int = 0
    accepted: int = 0
    rejected: int = 0
    duplicates: int = 0
    out_of_order: int = 0
    sequence_gap: int = 0
    max_queue_depth: int = 0
    last_window_seq: Optional[int] = None
    latest: Optional[EdgeResultSample] = None
    rejection_reasons: Dict[str, int] = field(default_factory=dict)

    def as_dict(self) -> dict:
        latest = None
        if self.latest is not None:
            latest = {
                "node_id": self.latest.node_id,
                "link_id": self.latest.link_id,
                "window_seq": self.latest.window_seq,
                "boot_id": self.latest.boot_id,
                "score": self.latest.score,
                "state": self.latest.state,
                "quality": self.latest.quality,
                "uncertainty": self.latest.uncertainty,
                "window_start_us": self.latest.window_start_us,
                "window_end_us": self.latest.window_end_us,
                "rx_timestamp_us": (
                    self.latest.window_end_us if self.latest.rx_timestamp_us is None
                    else self.latest.rx_timestamp_us
                ),
            }
        return {
            "node_id": self.node_id,
            "link_id": self.link_id,
            "generated": self.generated,
            "network_lost": self.network_lost,
            "enqueued": self.enqueued,
            "queue_drops": self.queue_drops,
            "delivered": self.delivered,
            "accepted": self.accepted,
            "rejected": self.rejected,
            "duplicates": self.duplicates,
            "out_of_order": self.out_of_order,
            "sequence_gap": self.sequence_gap,
            "max_queue_depth": self.max_queue_depth,
            "last_window_seq": self.last_window_seq,
            "latest": latest,
            "rejection_reasons": dict(self.rejection_reasons),
        }


@dataclass
class _LinkRuntime:
    snapshot: LinkSnapshot
    fault: LinkFault
    queue: Deque[EdgeResultSample]
    seen_sequences: set[int] = field(default_factory=set)
    recent_sequences: Deque[int] = field(default_factory=deque)
    reorder_hold: Optional[EdgeResultSample] = None


class EdgeResultV5Simulator:
    """Run deterministic multi-link transport and ingest simulations.

    ``node_count`` defaults to 24, matching the intended 20-30 node load
    exercise. The implementation also permits smaller scenarios for focused
    tests. It never opens sockets or writes captures.
    """

    def __init__(
        self,
        node_count: int = DEFAULT_NODE_COUNT,
        *,
        queue_capacity: int = 8,
        process_budget: int = 1,
        seed: int = 7,
        boot_id: int = 1,
        corridor_id: str = "corridor-sim",
    ) -> None:
        if node_count < 1:
            raise ValueError("node_count must be positive")
        if queue_capacity < 1:
            raise ValueError("queue_capacity must be positive")
        if process_budget < 0:
            raise ValueError("process_budget cannot be negative")
        if boot_id < 1:
            raise ValueError("boot_id must be positive")
        if not corridor_id:
            raise ValueError("corridor_id must not be empty")

        self.node_count = int(node_count)
        self.queue_capacity = int(queue_capacity)
        self.process_budget = int(process_budget)
        self.seed = int(seed)
        self.boot_id = int(boot_id)
        self.corridor_id = corridor_id
        self._rng = random.Random(seed)
        self._step = 0
        self._links: Dict[int, _LinkRuntime] = {
            link_id: _LinkRuntime(
                snapshot=LinkSnapshot(node_id=link_id, link_id=link_id),
                fault=LinkFault(),
                queue=deque(),
            )
            for link_id in range(1, self.node_count + 1)
        }

    @property
    def links(self) -> Mapping[int, LinkSnapshot]:
        """Expose read-only-by-convention snapshots keyed by ``link_id``."""
        return {link_id: runtime.snapshot for link_id, runtime in self._links.items()}

    @property
    def steps(self) -> int:
        return self._step

    def set_link_fault(
        self,
        link_id: int,
        *,
        loss_probability: Optional[float] = None,
        duplicate_probability: Optional[float] = None,
        reorder_probability: Optional[float] = None,
    ) -> None:
        """Replace selected transport fault probabilities for one link."""
        runtime = self._runtime(link_id)
        current = runtime.fault
        runtime.fault = LinkFault(
            loss_probability=current.loss_probability if loss_probability is None else loss_probability,
            duplicate_probability=(
                current.duplicate_probability
                if duplicate_probability is None
                else duplicate_probability
            ),
            reorder_probability=(
                current.reorder_probability
                if reorder_probability is None
                else reorder_probability
            ),
        )

    def submit(self, sample: EdgeResultSample, *, duplicate: bool = False) -> bool:
        """Submit a pre-built sample directly to its bounded link queue."""
        runtime = self._runtime(sample.link_id)
        self._validate_sample(sample)
        accepted = self._offer(runtime, sample)
        if duplicate:
            self._offer(runtime, sample)
        return accepted

    def step(self) -> None:
        """Generate and transport one result window for every virtual link."""
        next_seq = self._step + 1
        for link_id in sorted(self._links):
            runtime = self._links[link_id]
            sample = self._make_sample(link_id, next_seq)
            snapshot = runtime.snapshot
            snapshot.generated += 1

            if self._rng.random() < runtime.fault.loss_probability:
                snapshot.network_lost += 1
                continue

            arrivals = self._faulted_arrivals(runtime, sample)
            for arrival in arrivals:
                self._offer(runtime, arrival)

        self._drain_link_queues(self.process_budget)
        self._step = next_seq

    def run(self, steps: int, *, flush: bool = True) -> dict:
        """Run ``steps`` windows and return cumulative per-link telemetry."""
        if steps < 0:
            raise ValueError("steps cannot be negative")
        for _ in range(steps):
            self.step()
        if flush:
            self.flush()
        return self.report()

    def flush(self) -> None:
        """Release held reorder samples and drain all bounded queues."""
        for runtime in self._links.values():
            if runtime.reorder_hold is not None:
                self._offer(runtime, runtime.reorder_hold)
                runtime.reorder_hold = None
        while any(runtime.queue for runtime in self._links.values()):
            self._drain_link_queues(None)

    def report(self) -> dict:
        """Return JSON-compatible cumulative simulation evidence."""
        links = {str(link_id): runtime.snapshot.as_dict() for link_id, runtime in self._links.items()}
        totals = {
            key: sum(item[key] for item in links.values())
            for key in (
                "generated",
                "network_lost",
                "enqueued",
                "queue_drops",
                "delivered",
                "accepted",
                "rejected",
                "duplicates",
                "out_of_order",
                "sequence_gap",
            )
        }
        return {
            "node_count": self.node_count,
            "steps": self._step,
            "seed": self.seed,
            "queue_capacity": self.queue_capacity,
            "process_budget": self.process_budget,
            "boot_id": self.boot_id,
            "corridor_id": self.corridor_id,
            "sequence_history_limit": SEQUENCE_HISTORY_LIMIT,
            "totals": totals,
            "links": links,
        }

    def _runtime(self, link_id: int) -> _LinkRuntime:
        try:
            return self._links[int(link_id)]
        except (KeyError, TypeError, ValueError) as error:
            raise KeyError(f"unknown link_id={link_id}") from error

    def _make_sample(self, link_id: int, window_seq: int) -> EdgeResultSample:
        # Keep the generated signal deterministic and explicitly non-calibrated.
        score = float(70 + ((window_seq + link_id) % 21))
        return EdgeResultSample(
            node_id=link_id,
            link_id=link_id,
            tx_id=1,
            rx_id=link_id,
            corridor_id=self.corridor_id,
            boot_id=self.boot_id,
            window_seq=window_seq,
            window_start_us=window_seq * 100_000,
            window_end_us=window_seq * 100_000 + 100_000,
            rx_timestamp_us=window_seq * 100_000 + 100_000,
            score=score,
            state="PASSABLE",
            # EdgeResult V5 quality and uncertainty use percentages [0, 100].
            quality=95.0,
            uncertainty=10.0,
        )

    def _faulted_arrivals(
        self, runtime: _LinkRuntime, sample: EdgeResultSample
    ) -> list[EdgeResultSample]:
        arrivals: list[EdgeResultSample] = []
        if self._rng.random() < runtime.fault.reorder_probability:
            if runtime.reorder_hold is None:
                runtime.reorder_hold = sample
                return arrivals
            arrivals.extend((sample, runtime.reorder_hold))
            runtime.reorder_hold = None
        else:
            arrivals.append(sample)

        if self._rng.random() < runtime.fault.duplicate_probability:
            arrivals.append(sample)
        return arrivals

    def _offer(self, runtime: _LinkRuntime, sample: EdgeResultSample) -> bool:
        snapshot = runtime.snapshot
        if len(runtime.queue) >= self.queue_capacity:
            snapshot.queue_drops += 1
            return False
        runtime.queue.append(sample)
        snapshot.enqueued += 1
        snapshot.max_queue_depth = max(snapshot.max_queue_depth, len(runtime.queue))
        return True

    def _drain_link_queues(self, budget: Optional[int]) -> None:
        for runtime in self._links.values():
            count = 0
            while runtime.queue and (budget is None or count < budget):
                self._consume(runtime, runtime.queue.popleft())
                count += 1

    def _consume(self, runtime: _LinkRuntime, sample: EdgeResultSample) -> None:
        snapshot = runtime.snapshot
        snapshot.delivered += 1
        if sample.boot_id != self.boot_id:
            self._reject(runtime, "old_boot")
            return
        if (
            sample.link_id != snapshot.link_id
            or sample.node_id != snapshot.node_id
            or sample.tx_id != 1
            or sample.rx_id != snapshot.link_id
            or sample.corridor_id != self.corridor_id
        ):
            self._reject(runtime, "identity_mismatch")
            return
        if sample.window_seq in runtime.seen_sequences:
            snapshot.duplicates += 1
            self._reject(runtime, "duplicate")
            return
        if snapshot.last_window_seq is not None and sample.window_seq < snapshot.last_window_seq:
            snapshot.out_of_order += 1
            self._reject(runtime, "out_of_order")
            return

        if snapshot.last_window_seq is not None:
            snapshot.sequence_gap += max(0, sample.window_seq - snapshot.last_window_seq - 1)
        runtime.seen_sequences.add(sample.window_seq)
        runtime.recent_sequences.append(sample.window_seq)
        while len(runtime.recent_sequences) > SEQUENCE_HISTORY_LIMIT:
            runtime.seen_sequences.discard(runtime.recent_sequences.popleft())
        snapshot.last_window_seq = sample.window_seq
        snapshot.latest = sample
        snapshot.accepted += 1

    def _reject(self, runtime: _LinkRuntime, reason: str) -> None:
        snapshot = runtime.snapshot
        snapshot.rejected += 1
        snapshot.rejection_reasons[reason] = snapshot.rejection_reasons.get(reason, 0) + 1

    @staticmethod
    def _validate_sample(sample: EdgeResultSample) -> None:
        if sample.state not in EDGE_STATES:
            raise ValueError(f"unsupported state={sample.state!r}")
        if sample.window_seq < 1 or sample.boot_id < 1:
            raise ValueError("boot_id and window_seq must be positive")
        if sample.window_start_us <= 0:
            raise ValueError("window_start_us must be positive")
        if sample.window_end_us <= sample.window_start_us:
            raise ValueError("window_end_us must be after window_start_us")
        receive_timestamp = sample.window_end_us if sample.rx_timestamp_us is None else sample.rx_timestamp_us
        if receive_timestamp <= 0 or receive_timestamp < sample.window_end_us:
            raise ValueError("rx_timestamp_us must be at or after window_end_us")
        if sample.state == "UNKNOWN" and sample.score is not None:
            raise ValueError("UNKNOWN samples must use score=None")
        if sample.score is not None and (
            not math.isfinite(sample.score) or not 0.0 <= sample.score <= 100.0
        ):
            raise ValueError("score must be finite and within [0, 100]")
        for name in ("quality", "uncertainty"):
            value = float(getattr(sample, name))
            if not math.isfinite(value) or not 0.0 <= value <= 100.0:
                raise ValueError(f"{name} must be finite and within [0, 100]")


def run_default_load(*, steps: int = 40, seed: int = 7) -> dict:
    """Run the canonical 24-node smoke scenario used by CI/tests."""
    simulator = EdgeResultV5Simulator(node_count=DEFAULT_NODE_COUNT, seed=seed)
    for link_id in range(1, simulator.node_count + 1):
        simulator.set_link_fault(
            link_id,
            loss_probability=0.04,
            duplicate_probability=0.02,
            reorder_probability=0.03,
        )
    return simulator.run(steps)


def main() -> None:
    parser = argparse.ArgumentParser(description="Run a deterministic EdgeResult V5 load simulation.")
    parser.add_argument("--nodes", type=int, default=DEFAULT_NODE_COUNT)
    parser.add_argument("--steps", type=int, default=40)
    parser.add_argument("--seed", type=int, default=7)
    parser.add_argument("--queue-capacity", type=int, default=8)
    parser.add_argument("--process-budget", type=int, default=1)
    args = parser.parse_args()

    simulator = EdgeResultV5Simulator(
        node_count=args.nodes,
        queue_capacity=args.queue_capacity,
        process_budget=args.process_budget,
        seed=args.seed,
    )
    for link_id in range(1, simulator.node_count + 1):
        simulator.set_link_fault(
            link_id,
            loss_probability=0.04,
            duplicate_probability=0.02,
            reorder_probability=0.03,
        )
    json.dump(simulator.run(args.steps), fp=sys.stdout, sort_keys=True)
    sys.stdout.write("\n")


__all__ = [
    "DEFAULT_NODE_COUNT",
    "SEQUENCE_HISTORY_LIMIT",
    "EDGE_STATES",
    "EdgeResultSample",
    "EdgeResultV5Simulator",
    "LinkFault",
    "LinkSnapshot",
    "run_default_load",
]


if __name__ == "__main__":
    main()
