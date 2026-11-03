"""Bounded snapshot/stream reconstruction behind an explicitly trusted port.

This module is not wired into Store, Gateway, or a ledger. Its output records
only what the injected verifier asserts; it is not account authentication or a
provider attestation. Built-in CTP, OKX and Binance currently do not implement
this port and must not use these reconstructed rows as reconciliation proof.
"""

from __future__ import annotations

import threading
import time
from collections.abc import Callable
from dataclasses import dataclass

from bt_api_base.account_protocol import (
    AccountKey,
    AccountPosition,
    account_venue_contains_instrument_venue,
)
from bt_api_base.position_bootstrap_protocol import (
    PositionStreamBinding,
    PositionStreamEvent,
    PositionStreamSnapshot,
    PositionStreamVerifier,
)


class PositionBootstrapError(RuntimeError):
    """A finite, value-free reconstruction failure."""


@dataclass(frozen=True)
class PositionBootstrapProjection:
    """Source-adapter-asserted rows, not an account-completeness receipt."""

    binding: PositionStreamBinding
    snapshot_receipt_ref: str
    snapshot_cut_sequence: int
    sequence: int
    observed_ns: int
    positions: tuple[AccountPosition, ...]


class PositionStreamBootstrap:
    """Subscribe, buffer, install a cut, and project absolute updates.

    A trusted verifier must attest to exact account/session identity, a ready
    full-account subscription, a complete snapshot including an empty one,
    its same-stream cut, each event, and the current lease on every call.
    A ``True`` verifier response is only an assertion by the configured port;
    this class never turns it into provider authority. No compatibility adapter
    for ``AccountPositionReport`` is supplied.

    The verifier runs under the lock and must be bounded and non-reentrant.
    ``interrupt`` sets its cancellation flag before waiting on that lock so a
    concurrent disconnect invalidates an in-progress verifier immediately.
    """

    def __init__(
        self,
        binding: PositionStreamBinding,
        verifier: PositionStreamVerifier,
        *,
        clock_ns: Callable[[], int] = time.time_ns,
        max_buffer: int = 1024,
        max_rows: int = 10000,
    ) -> None:
        if type(binding) is not PositionStreamBinding or not callable(clock_ns):
            raise PositionBootstrapError("BINDING_INVALID")
        if type(max_buffer) is not int or not 1 <= max_buffer <= 100000:
            raise PositionBootstrapError("BUFFER_LIMIT_INVALID")
        if type(max_rows) is not int or not 1 <= max_rows <= 100000:
            raise PositionBootstrapError("ROW_LIMIT_INVALID")
        for name in ("verify_binding", "verify_snapshot", "verify_event", "verify_live"):
            if not callable(getattr(verifier, name, None)):
                raise PositionBootstrapError("VERIFIER_INVALID")
        # Neither the caller nor an adapter receives the internal object.
        self._binding = self._copy_binding(binding)
        self._binding_snapshot = self._copy_binding(self._binding)
        self._verifier = verifier
        self._clock = clock_ns
        self._max_buffer = max_buffer
        self._max_rows = max_rows
        self._lock = threading.Lock()
        self._arrival_lock = threading.Lock()
        self._pending_events = 0
        self._cancelled = threading.Event()
        self._reason: str | None = None
        self._last_clock = 0
        self._buffer: list[tuple[int, int, AccountPosition]] = []
        self._last_received: int | None = None
        self._last_event_time: int | None = None
        self._rows: dict[tuple, AccountPosition] | None = None
        self._snapshot_receipt_ref: str | None = None
        self._snapshot_cut_sequence: int | None = None
        self._sequence: int | None = None
        self._observed_ns = 0
        with self._lock:
            self._verify("verify_binding", self._copy_binding(self._binding))
            self._ensure_live()

    @property
    def interruption(self) -> str | None:
        with self._lock:
            return self._reason

    def interrupt(self, reason: str = "STREAM_INTERRUPTED") -> None:
        """Invalidate even if an adapter call is currently in progress."""
        self._cancelled.set()
        with self._lock:
            if self._reason is None:
                self._reason = reason if type(reason) is str and reason else "STREAM_INTERRUPTED"
            self._rows = None
            self._buffer.clear()

    def push(self, event: PositionStreamEvent) -> None:
        """Buffer or apply one authenticated-by-the-configured-adapter event."""
        # Count ingress before waiting on the state lock. A snapshot verifier
        # can block while a callback is already in flight; such a snapshot is
        # not exposed until the pending callback has actually been applied.
        with self._arrival_lock:
            self._pending_events += 1
        try:
            self._push(event)
        finally:
            with self._arrival_lock:
                self._pending_events -= 1

    def _push(self, event: PositionStreamEvent) -> None:
        with self._lock:
            self._ensure_live()
            if type(event) is not PositionStreamEvent or event.binding != self._binding:
                self._fail("EVENT_BINDING_INVALID")
            event = self._copy_event(event)
            if event.binding != self._binding:
                self._fail("EVENT_BINDING_INVALID")
            self._verify("verify_event", self._copy_binding(self._binding), self._copy_event(event))
            now = self._ensure_live()
            row = self._row(event.position)
            if event.observed_ns > now:
                self._fail("EVENT_TIME_INVALID")
            if self._last_event_time is not None and event.observed_ns < self._last_event_time:
                self._fail("EVENT_TIME_REVERSED")
            if self._last_received is not None and event.sequence != self._last_received + 1:
                self._fail("EVENT_SEQUENCE_GAP")
            if self._rows is None:
                if len(self._buffer) >= self._max_buffer:
                    self._fail("BUFFER_OVERFLOW")
                # Store the validated values, rather than retaining the
                # incoming event object for later snapshot replay.
                self._buffer.append((event.sequence, event.observed_ns, row))
            else:
                if event.sequence != self._sequence + 1:
                    self._fail("EVENT_SEQUENCE_GAP")
                self._apply(self._rows, row)
                self._sequence = event.sequence
                self._observed_ns = max(self._observed_ns, event.observed_ns)
            self._last_received = event.sequence
            self._last_event_time = event.observed_ns

    def install_snapshot(self, snapshot: PositionStreamSnapshot) -> None:
        """Atomically install a validated cut and replay buffered later events."""
        with self._lock:
            self._ensure_live()
            if self._rows is not None:
                self._fail("SNAPSHOT_ALREADY_INSTALLED")
            if type(snapshot) is not PositionStreamSnapshot or snapshot.binding != self._binding:
                self._fail("SNAPSHOT_BINDING_INVALID")
            positions = snapshot.positions
            if type(positions) is not tuple:
                self._fail("SNAPSHOT_INVALID")
            if len(positions) > self._max_rows:
                self._fail("ROW_LIMIT_EXCEEDED")
            snapshot = self._copy_snapshot(snapshot)
            if snapshot.binding != self._binding:
                self._fail("SNAPSHOT_BINDING_INVALID")
            self._verify(
                "verify_snapshot", self._copy_binding(self._binding), self._copy_snapshot(snapshot)
            )
            now = self._ensure_live()
            if snapshot.observed_ns > now:
                self._fail("SNAPSHOT_TIME_INVALID")
            rows: dict[tuple, AccountPosition] = {}
            for position in snapshot.positions:
                row = self._row(position)
                key = self._key(row)
                if key in rows:
                    self._fail("DUPLICATE_SNAPSHOT_ROW")
                rows[key] = row
            sequence = snapshot.cut_sequence
            observed = snapshot.observed_ns
            for event_sequence, event_observed, event_row in self._buffer:
                if event_sequence <= snapshot.cut_sequence:
                    continue
                if event_sequence != sequence + 1:
                    self._fail("SNAPSHOT_STREAM_GAP")
                self._apply(rows, event_row)
                sequence = event_sequence
                observed = max(observed, event_observed)
            self._ensure_live()
            self._rows = rows
            # Correlation fields are copied from the snapshot verified by the
            # adapter, not inferred from subsequent events. They remain mere
            # claims and are not an account-completeness receipt.
            self._snapshot_receipt_ref = snapshot.snapshot_receipt_ref
            self._snapshot_cut_sequence = snapshot.cut_sequence
            self._sequence = sequence
            # Events no newer than the trusted cut are covered by the
            # snapshot; the next live update must follow the installed cut.
            self._last_received = sequence
            self._observed_ns = observed
            self._buffer.clear()

    def projection(self) -> PositionBootstrapProjection:
        """Return a source assertion; never return a ledger-ready report."""
        with self._lock:
            self._ensure_live()
            if self._rows is None:
                raise PositionBootstrapError("SNAPSHOT_NOT_INSTALLED")
            with self._arrival_lock:
                if self._pending_events:
                    raise PositionBootstrapError("EVENTS_PENDING")
            result = PositionBootstrapProjection(
                self._copy_binding(self._binding_snapshot),
                self._snapshot_receipt_ref,
                self._snapshot_cut_sequence,
                self._sequence,
                self._observed_ns,
                tuple(self._row(row) for row in self._rows.values()),
            )
            # Cloning can take time; disconnect, lease expiry or a callback
            # may have arrived during the copy. Recheck at publication.
            self._ensure_live()
            with self._arrival_lock:
                if self._pending_events:
                    raise PositionBootstrapError("EVENTS_PENDING")
                if self._cancelled.is_set():
                    self._fail("STREAM_INTERRUPTED")
            return result

    def _ensure_live(self) -> int:
        if self._cancelled.is_set() or self._reason is not None:
            self._fail(self._reason or "STREAM_INTERRUPTED")
        if self._binding != self._binding_snapshot:
            self._fail("BINDING_CHANGED")
        now = self._read_clock()
        self._verify("verify_live", self._copy_binding(self._binding), now)
        if self._binding != self._binding_snapshot:
            self._fail("BINDING_CHANGED")
        # The adapter may block; the original timestamp is no longer enough
        # to prove that the lease is current at publication time.
        now = self._read_clock()
        if self._cancelled.is_set():
            self._fail("STREAM_INTERRUPTED")
        return now

    def _read_clock(self) -> int:
        try:
            now = self._clock()
        except Exception:
            self._fail("CLOCK_INVALID")
        if type(now) is not int or now <= 0:
            self._fail("CLOCK_INVALID")
        if now < self._last_clock:
            self._fail("CLOCK_REVERSED")
        self._last_clock = now
        if now >= self._binding.valid_until_ns:
            self._fail("LEASE_EXPIRED")
        return now

    def _verify(self, name: str, *args: object) -> None:
        # A verifier may retain or even mutate the detached claims it sees.
        # Detect changes before accepting its answer; otherwise the verifier
        # could inspect different facts from those installed in the stream.
        expected = tuple(self._copy_claim(value) for value in args)
        try:
            accepted = getattr(self._verifier, name)(*args)
        except Exception:
            self._fail("VERIFIER_FAILED")
        if args != expected:
            self._fail("VERIFIER_MUTATED_INPUT")
        if accepted is not True:
            self._fail("VERIFIER_REJECTED")
        if self._cancelled.is_set():
            self._fail("STREAM_INTERRUPTED")

    def _row(self, position: AccountPosition) -> AccountPosition:
        try:
            row = AccountPosition.from_dict(position.to_dict())
        except Exception:
            self._fail("POSITION_ROW_INVALID")
        account = self._binding.account_key
        instrument = row.instrument
        if (
            instrument.provider != account.provider
            or instrument.environment != account.environment
            or not account_venue_contains_instrument_venue(account, instrument.venue)
            or instrument.product_type != row.resource.product_type
        ):
            self._fail("POSITION_SCOPE_MISMATCH")
        return row

    @staticmethod
    def _key(row: AccountPosition) -> tuple:
        return row.resource, row.instrument, row.side

    def _copy_claim(self, value: object) -> object:
        if type(value) is PositionStreamBinding:
            return self._copy_binding(value)
        if type(value) is PositionStreamSnapshot:
            return self._copy_snapshot(value)
        if type(value) is PositionStreamEvent:
            return self._copy_event(value)
        return value

    def _copy_event(self, event: PositionStreamEvent) -> PositionStreamEvent:
        try:
            return PositionStreamEvent(
                binding=self._copy_binding(event.binding),
                sequence=event.sequence,
                observed_ns=event.observed_ns,
                event_receipt_ref=event.event_receipt_ref,
                position=self._row(event.position),
            )
        except (TypeError, ValueError, AttributeError):
            self._fail("EVENT_INVALID")

    def _copy_snapshot(self, snapshot: PositionStreamSnapshot) -> PositionStreamSnapshot:
        try:
            positions = snapshot.positions
            if type(positions) is not tuple:
                self._fail("SNAPSHOT_INVALID")
            if len(positions) > self._max_rows:
                self._fail("ROW_LIMIT_EXCEEDED")
            return PositionStreamSnapshot(
                binding=self._copy_binding(snapshot.binding),
                cut_sequence=snapshot.cut_sequence,
                observed_ns=snapshot.observed_ns,
                snapshot_receipt_ref=snapshot.snapshot_receipt_ref,
                positions=tuple(self._row(row) for row in positions),
            )
        except (TypeError, ValueError, AttributeError):
            self._fail("SNAPSHOT_INVALID")

    @staticmethod
    def _copy_binding(binding: PositionStreamBinding) -> PositionStreamBinding:
        return PositionStreamBinding(
            account_key=AccountKey.from_dict(binding.account_key.to_dict()),
            account_ref=binding.account_ref,
            route_ref=binding.route_ref,
            session_ref=binding.session_ref,
            connection_epoch=binding.connection_epoch,
            subscription_epoch=binding.subscription_epoch,
            subscription_ready_ref=binding.subscription_ready_ref,
            valid_until_ns=binding.valid_until_ns,
        )

    def _apply(self, rows: dict[tuple, AccountPosition], row: AccountPosition) -> None:
        from decimal import Decimal

        key = self._key(row)
        if Decimal(row.quantity) == 0:
            rows.pop(key, None)
        else:
            if key not in rows and len(rows) >= self._max_rows:
                self._fail("ROW_LIMIT_EXCEEDED")
            rows[key] = row

    def _fail(self, code: str) -> None:
        self._reason = code
        self._cancelled.set()
        self._rows = None
        self._buffer.clear()
        raise PositionBootstrapError(code)
