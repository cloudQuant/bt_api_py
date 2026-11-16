"""Bounded process-local history of actual clock publications.

This history is neither remote authentication nor a transport contract. Its
monotonic timestamps must be interpreted only inside the originating process.
The refresher serializes all access under its publication lock.
"""
from __future__ import annotations

import os
import uuid
from collections import deque
from dataclasses import dataclass
from time import monotonic_ns
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from bt_api_py.gateway_server_clock_refresh import GatewayServerClockSnapshot

_MAX_SEQUENCE = (1 << 63) - 1


class GatewayClockHistoryError(RuntimeError):
    """Finite diagnostic without input values or exception details."""

    def __init__(self, code: str):
        self.code = code
        super().__init__(code)


@dataclass(frozen=True, slots=True)
class GatewayClockHistoryRecord:
    sequence: int
    kind: str
    observed_monotonic_ns: int
    snapshot: GatewayServerClockSnapshot | None


@dataclass(frozen=True, slots=True)
class GatewayClockHistoryPage:
    source_epoch: str
    owner_pid: int
    after_sequence: int
    first_available_sequence: int
    last_sequence: int
    records: tuple[GatewayClockHistoryRecord, ...]
    gap: bool
    available: bool


class _GatewayClockHistory:
    """Called only while the owning refresher holds its lock."""

    def __init__(self, capacity: int):
        self.source_epoch = uuid.uuid4().hex
        self.owner_pid = os.getpid()
        self._records = deque(maxlen=capacity)
        self._last_sequence = 0
        self._last_time = 0

    def append(self, kind: str, snapshot: object | None) -> None:
        from bt_api_py.gateway_server_clock_refresh import GatewayServerClockSnapshot

        now = monotonic_ns()
        if (type(now) is not int or not 0 < now <= _MAX_SEQUENCE or now < self._last_time
                or self._last_sequence >= _MAX_SEQUENCE
                or kind not in ('SAMPLE', 'UNAVAILABLE', 'STOPPED')
                or (kind == 'SAMPLE' and (type(snapshot) is not GatewayServerClockSnapshot
                    or snapshot.pid != self.owner_pid or type(snapshot.anchor_monotonic_ns) is not int
                    or snapshot.anchor_monotonic_ns > now))
                or (kind != 'SAMPLE' and snapshot is not None)):
            raise GatewayClockHistoryError('CLOCK_HISTORY_UNAVAILABLE')
        record = GatewayClockHistoryRecord(self._last_sequence + 1, kind, now, snapshot)
        self._records.append(record)
        self._last_sequence = record.sequence
        self._last_time = now

    def read(self, after_sequence: int, limit: int, available: bool) -> GatewayClockHistoryPage:
        if (type(after_sequence) is not int or not 0 <= after_sequence <= self._last_sequence
                or type(limit) is not int or not 1 <= limit <= 256):
            raise GatewayClockHistoryError('CLOCK_HISTORY_INVALID_CURSOR')
        first = self._records[0].sequence if self._records else self._last_sequence + 1
        records = tuple(record for record in self._records if record.sequence > after_sequence)[:limit]
        return GatewayClockHistoryPage(self.source_epoch, self.owner_pid, after_sequence,
                                       first, self._last_sequence, records,
                                       after_sequence < first - 1, available)
