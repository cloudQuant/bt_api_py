"""Qualify a past local monotonic interval using published clock history.

This component neither authenticates a source nor proves recovery coverage.
Monotonic values and the result are local to the original process and owner;
they are not a wire protocol or cross-process clock comparison.
"""
from __future__ import annotations

import os
import re
import threading
from dataclasses import dataclass
from heapq import heappop, heappush
from time import monotonic_ns

from bt_api_py.clock_verification import ClockEvidence
from bt_api_py.gateway_clock_history import GatewayClockHistoryPage, GatewayClockHistoryRecord
from bt_api_py.gateway_server_clock_refresh import (
    GatewayServerClockRefresher,
    GatewayServerClockSnapshot,
)

_MAX_INT = (1 << 63) - 1
_MIN_INT = -(1 << 63)
_MAX_RECORDS = 4096
_PAGE_SIZE = 256


@dataclass(frozen=True, slots=True)
class GatewayRecoveryClockQualification:
    """Local interval bounds; UNKNOWN never includes projected timestamps."""

    status: str
    reason: str
    start_utc_lower_ns: int | None = None
    start_utc_upper_ns: int | None = None
    end_utc_lower_ns: int | None = None
    end_utc_upper_ns: int | None = None
    source_epoch: str | None = None
    first_sample_sequence: int | None = None
    last_sample_sequence: int | None = None


def _unknown(reason):
    return GatewayRecoveryClockQualification('UNKNOWN', reason)


def _uint(value, *, minimum=0):
    return type(value) is int and minimum <= value <= _MAX_INT


def _signed(value):
    return type(value) is int and _MIN_INT <= value <= _MAX_INT


class _ActiveCalibrations:
    """O(log n) expiry and intersection checks for one continuous segment."""

    def __init__(self):
        self._ids = set()
        self._expiry = []
        self._lower = []
        self._upper = []

    def add(self, record):
        snapshot = record.snapshot
        evidence = snapshot.evidence
        intercept = snapshot.anchor_wall_ns + evidence.offset_ns - snapshot.anchor_monotonic_ns
        seq = record.sequence
        self._ids.add(seq)
        heappush(self._expiry, (evidence.valid_until_monotonic_ns, seq))
        heappush(self._lower, (-(intercept - evidence.error_bound_ns), seq))
        heappush(self._upper, (intercept + evidence.error_bound_ns, seq))

    def expire(self, moment):
        while self._expiry and self._expiry[0][0] <= moment:
            _, seq = heappop(self._expiry)
            self._ids.discard(seq)
        while self._lower and self._lower[0][1] not in self._ids:
            heappop(self._lower)
        while self._upper and self._upper[0][1] not in self._ids:
            heappop(self._upper)

    def intersects(self):
        return not self._ids or -self._lower[0][0] <= self._upper[0][0]

    def first_sequence(self):
        return min(self._ids)


class GatewayRecoveryClockQualifier:
    """Use one exact owner-bound refresher; callers cannot supply calibrations."""

    def __init__(self, refresher, *, max_interval_ns):
        if (type(refresher) is not GatewayServerClockRefresher
                or not _uint(max_interval_ns, minimum=1)
                or refresher._pid != os.getpid()
                or refresher._owner is not threading.current_thread()):
            raise ValueError('INVALID_CLOCK_QUALIFIER_CONFIGURATION')
        self._source = self._original_source = refresher
        self._pid = os.getpid()
        self._owner = threading.current_thread()
        self._max_interval_ns = max_interval_ns
        self._servers = refresher._allowed_servers
        self._configured_servers = refresher._servers
        self._max_error = refresher._max_error_bound_ns
        self._max_validity = refresher._max_validity_ns

    def _bound(self):
        source = self._source
        return (os.getpid() == self._pid and threading.current_thread() is self._owner
                and source is self._original_source
                and type(source) is GatewayServerClockRefresher
                and source._pid == self._pid and source._owner is self._owner
                and source._allowed_servers is self._servers
                and source._servers is self._configured_servers
                and type(source._max_error_bound_ns) is int
                and source._max_error_bound_ns == self._max_error
                and type(source._max_validity_ns) is int
                and source._max_validity_ns == self._max_validity)

    def _page(self, after, *, epoch=None, high=None):
        page = self._source.read_history(after_sequence=after, limit=_PAGE_SIZE)
        if (type(page) is not GatewayClockHistoryPage or type(page.source_epoch) is not str
                or re.fullmatch(r'[0-9a-f]{32}', page.source_epoch, re.ASCII) is None
                or (epoch is not None and page.source_epoch != epoch)
                or type(page.owner_pid) is not int or page.owner_pid != self._pid
                or type(page.available) is not bool or not page.available
                or type(page.gap) is not bool or type(page.records) is not tuple
                or len(page.records) > _PAGE_SIZE
                or page.after_sequence != after or type(page.after_sequence) is not int
                or not _uint(page.first_available_sequence, minimum=1)
                or not _uint(page.last_sequence) or page.last_sequence < after
                or page.first_available_sequence > page.last_sequence + 1
                or page.gap != (after < page.first_available_sequence - 1)
                or (high is not None and page.last_sequence < high)):
            raise ValueError('HISTORY_UNAVAILABLE')
        expected = max(after + 1, page.first_available_sequence)
        for record in page.records:
            if type(record) is not GatewayClockHistoryRecord or record.sequence != expected:
                raise ValueError('HISTORY_GAP')
            if not _uint(record.sequence, minimum=1) or record.sequence > page.last_sequence:
                raise ValueError('HISTORY_GAP')
            expected += 1
        if (not page.records and max(after + 1, page.first_available_sequence) <= page.last_sequence):
            raise ValueError('HISTORY_GAP')
        return page

    def _snapshot_valid(self, record):
        snapshot = record.snapshot
        if (type(snapshot) is not GatewayServerClockSnapshot or type(snapshot.pid) is not int
                or snapshot.pid != self._pid):
            return False
        evidence = snapshot.evidence
        return not (
            type(evidence) is not ClockEvidence
            or type(evidence.method) is not str or evidence.method != 'sntp'
            or type(evidence.server) is not str or evidence.server not in self._servers
            or evidence.server not in self._configured_servers
            or not _signed(evidence.offset_ns)
            or not _uint(evidence.error_bound_ns) or evidence.error_bound_ns > self._max_error
            or not _uint(evidence.measured_monotonic_ns, minimum=1)
            or not _uint(evidence.valid_until_monotonic_ns, minimum=1)
            or not evidence.measured_monotonic_ns < evidence.valid_until_monotonic_ns
            or evidence.valid_until_monotonic_ns - evidence.measured_monotonic_ns > self._max_validity
            or not _uint(snapshot.anchor_monotonic_ns, minimum=1)
            or not _uint(snapshot.anchor_wall_ns, minimum=1)
            or not evidence.measured_monotonic_ns <= snapshot.anchor_monotonic_ns <= record.observed_monotonic_ns
            or record.observed_monotonic_ns >= evidence.valid_until_monotonic_ns
        )

    @staticmethod
    def _bounds(snapshot, instant):
        evidence = snapshot.evidence
        center = snapshot.anchor_wall_ns + evidence.offset_ns + instant - snapshot.anchor_monotonic_ns
        low, high = center - evidence.error_bound_ns, center + evidence.error_bound_ns
        if not _signed(low) or not _signed(high):
            raise ValueError('CLOCK_RANGE_INVALID')
        return low, high

    def qualify(self, start_monotonic_ns, end_monotonic_ns):
        """Qualify a past interval at the first read's bounded high-water mark."""
        if (not _uint(start_monotonic_ns) or not _uint(end_monotonic_ns)
                or start_monotonic_ns > end_monotonic_ns
                or end_monotonic_ns - start_monotonic_ns > self._max_interval_ns):
            return _unknown('INVALID_INTERVAL')
        try:
            if not self._bound():
                return _unknown('OWNER_CHANGED')
            before_read = monotonic_ns()
            if not _uint(before_read, minimum=1) or end_monotonic_ns > before_read:
                return _unknown('INTERVAL_NOT_PAST')
            first = self._page(0)
            after_read = monotonic_ns()
            if not _uint(after_read, minimum=1) or after_read < before_read:
                return _unknown('CLOCK_DISCONTINUITY')
            epoch, high = first.source_epoch, first.last_sequence
            if not high:
                return _unknown('NO_CLOCK_HISTORY')
            records = []
            cursor, page = 0, first
            while cursor < high:
                if cursor and (page.gap or page.first_available_sequence > cursor + 1):
                    return _unknown('HISTORY_GAP')
                for record in page.records:
                    if record.sequence > high:
                        break
                    records.append(record)
                    cursor = record.sequence
                    if len(records) > _MAX_RECORDS:
                        return _unknown('HISTORY_LIMIT')
                if cursor >= high:
                    break
                if not page.records or cursor < page.first_available_sequence:
                    return _unknown('HISTORY_GAP')
                page = self._page(cursor, epoch=epoch, high=high)
            final = self._page(high, epoch=epoch, high=high)
            if final.gap or not self._bound():
                return _unknown('HISTORY_CHANGED')
            result = self._qualify_records(records, first.gap, start_monotonic_ns, end_monotonic_ns, after_read, epoch)
            if result.status == 'QUALIFIED':
                final = self._page(high, epoch=epoch, high=high)
                if final.gap or not self._bound():
                    return _unknown('HISTORY_CHANGED')
            return result
        except Exception:
            return _unknown('HISTORY_UNAVAILABLE')

    def _qualify_records(self, records, lost_prefix, start, end, read_time, epoch):
        previous_time = 0
        earliest_sample = None
        for record in records:
            if (not _uint(record.observed_monotonic_ns, minimum=1)
                    or record.observed_monotonic_ns < previous_time
                    or record.observed_monotonic_ns > read_time
                    or type(record.kind) is not str
                    or record.kind not in ('SAMPLE', 'UNAVAILABLE', 'STOPPED')):
                return _unknown('INVALID_HISTORY')
            previous_time = record.observed_monotonic_ns
            if record.kind == 'SAMPLE':
                if earliest_sample is None:
                    earliest_sample = record.observed_monotonic_ns
            elif record.snapshot is not None:
                return _unknown('INVALID_HISTORY')
        if earliest_sample is None:
            return _unknown('HISTORY_GAP')
        prefix_unproven = (lost_prefix and start <
                           records[0].observed_monotonic_ns + self._max_validity)
        current = None
        active = _ActiveCalibrations()
        for record in records:
            if record.observed_monotonic_ns > start:
                break
            if record.kind == 'SAMPLE':
                if not self._snapshot_valid(record):
                    current = None
                    active = _ActiveCalibrations()
                    if record.observed_monotonic_ns == start:
                        return _unknown('INVALID_SAMPLE')
                    prefix_unproven = False
                    continue
                active.expire(record.observed_monotonic_ns)
                active.add(record)
                current = record
            else:
                current = None
                active = _ActiveCalibrations()
                if record.observed_monotonic_ns == start:
                    return _unknown('CLOCK_UNAVAILABLE')
                prefix_unproven = False
        if prefix_unproven:
            return _unknown('HISTORY_GAP')
        if current is None:
            return _unknown('NO_START_SAMPLE')
        active.expire(start)
        if not active.intersects():
            return _unknown('CLOCK_DISCONTINUITY')
        if start >= current.snapshot.evidence.valid_until_monotonic_ns:
            return _unknown('SAMPLE_EXPIRED')
        start_sequence = active.first_sequence()
        start_bounds = self._bounds(current.snapshot, start)
        for record in records:
            moment = record.observed_monotonic_ns
            if moment <= start or moment > end:
                continue
            if record.kind != 'SAMPLE':
                return _unknown('CLOCK_UNAVAILABLE')
            if not self._snapshot_valid(record):
                return _unknown('INVALID_SAMPLE')
            if moment >= current.snapshot.evidence.valid_until_monotonic_ns:
                return _unknown('SAMPLE_EXPIRED')
            self._bounds(current.snapshot, moment)
            self._bounds(record.snapshot, moment)
            active.expire(moment)
            active.add(record)
            if not active.intersects():
                return _unknown('CLOCK_DISCONTINUITY')
            current = record
        if end >= current.snapshot.evidence.valid_until_monotonic_ns:
            return _unknown('SAMPLE_EXPIRED')
        end_bounds = self._bounds(current.snapshot, end)
        if start_bounds[0] > end_bounds[1]:
            return _unknown('CLOCK_DISCONTINUITY')
        return GatewayRecoveryClockQualification('QUALIFIED', 'OK', *start_bounds, *end_bounds,
                                                  epoch, start_sequence, current.sequence)
