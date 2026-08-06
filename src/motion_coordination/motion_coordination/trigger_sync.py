"""Execution-local monotonic clock offset estimation for DDS triggers."""

from __future__ import annotations

from dataclasses import dataclass
from statistics import median
from typing import List, Tuple


@dataclass(frozen=True)
class TriggerSyncEstimate:
    """Mapping from coordinator monotonic time to one participant clock."""

    offset_ns: int
    round_trip_ns: int
    uncertainty_ns: int
    sample_count: int

    @property
    def uncertainty_ms(self) -> float:
        return self.uncertainty_ns / 1_000_000.0


class TriggerSyncEstimator:
    """Estimate a clock offset from DDS four-timestamp exchanges."""

    def __init__(self, *, keep_best: int = 3) -> None:
        self.keep_best = max(int(keep_best), 1)
        self._samples: List[Tuple[int, int]] = []

    @property
    def sample_count(self) -> int:
        return len(self._samples)

    def add_exchange(
        self, *, t1_ns: int, t2_ns: int, t3_ns: int, t4_ns: int,
    ) -> bool:
        """Add one coordinator→participant→coordinator exchange."""
        t1_ns = int(t1_ns)
        t2_ns = int(t2_ns)
        t3_ns = int(t3_ns)
        t4_ns = int(t4_ns)
        if t4_ns < t1_ns or t3_ns < t2_ns:
            return False
        round_trip_ns = (t4_ns - t1_ns) - (t3_ns - t2_ns)
        if round_trip_ns < 0:
            return False
        offset_ns = ((t2_ns - t1_ns) + (t3_ns - t4_ns)) // 2
        self._samples.append((round_trip_ns, offset_ns))
        return True

    def estimate(self) -> TriggerSyncEstimate:
        if not self._samples:
            raise ValueError('DDS 트리거 동기화 측정값이 없습니다')
        best = sorted(self._samples)[:self.keep_best]
        offsets = [row[1] for row in best]
        offset_ns = int(median(offsets))
        min_round_trip_ns = int(best[0][0])
        offset_spread_ns = max(abs(value - offset_ns) for value in offsets)
        uncertainty_ns = max(min_round_trip_ns // 2, offset_spread_ns)
        return TriggerSyncEstimate(
            offset_ns=offset_ns,
            round_trip_ns=min_round_trip_ns,
            uncertainty_ns=uncertainty_ns,
            sample_count=len(self._samples),
        )


def coordinator_to_local_ns(coordinator_ns: int, offset_ns: int) -> int:
    """Convert an execution coordinator deadline to a participant deadline."""
    return int(coordinator_ns) + int(offset_ns)


def local_to_coordinator_ns(local_ns: int, offset_ns: int) -> int:
    """Map a participant trigger timestamp onto the coordinator timeline."""
    return int(local_ns) - int(offset_ns)
