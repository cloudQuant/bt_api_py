"""Direct (no execution session) write workflow owned by the SDK (iteration 62).

Order of every account-mutating call (D62-03/14, §5.3a item 4):

1. write admission must be enabled for the route (otherwise nothing happens);
2. the provider reserves the native identity (CTP: 12-digit OrderRef bound to
   FrontID/SessionID/TradingDay) without sending;
3. the host-injected :class:`PreDispatchCommitPort` durably records the
   reservation; no durable marker, no send;
4. the session generation is re-checked and a one-shot grant is issued;
5. the provider consumes the grant and sends exactly once.

The SDK never writes host files.  Returns are correlated to host markers by the
original venue identity and de-duplicated; unmatched returns are reported as
unmatched, never as verified facts.

De-duplication scope (iteration 62 review, §4 item 5): a provider whose venue
execution ids (order refs, session ids, venue order ids, trade ids) are unique
only within one venue trading day declares the class or instance attribute
``execution_ids_trading_day_scoped = True`` (bt_api_ctp: CTP reuses
``OrderSysID``/``TradeID`` every trading day).  On such a route the
de-duplication key of a return also holds its trading day, so an equal return
of another trading day is delivered, not dropped as a replay.  A return of such
a route without one readable trading day is never de-duplicated by the SDK (it
is delivered every time it is drained; the host keeps its own fill keys).
Without the declaration (``False`` or absent: OKX, Binance) the key is
unchanged and a return replayed on a later day (e.g. a GTC order's) is still
dropped.  Any other declared value fails closed when the provider is created.
"""

from __future__ import annotations

import threading
from collections import OrderedDict
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Callable, Mapping, Optional

from bt_api_base.direct_write_protocol import (
    DirectExecutionEvent,
    DirectExecutionEventKind,
    DirectWriteError,
    NativeOrderIdentity,
    PreDispatchCommitPort,
    PreDispatchRecord,
    SettlementStatus,
    WriteAdmissionDenied,
    WriteOperation,
)

from .write_admission import WriteAdmission

__all__ = (
    "CorrelatedDirectEvent",
    "DIRECT_WRITE_PROVIDER_GROUP",
    "DirectWriteCoordinator",
    "DirectWriteOutcome",
)

DIRECT_WRITE_PROVIDER_GROUP = "bt_api.direct_write_providers"
_DEDUP_CAPACITY = 100_000
# Optional provider declaration read once when the provider is created; only
# ``True`` scopes de-duplication by trading day (see the module docstring).
_DAY_SCOPED_IDS_ATTRIBUTE = "execution_ids_trading_day_scoped"

# Provider error codes meaning the native call may have happened.
_UNKNOWN_OUTCOME_CODES = frozenset({"ctp_direct_submit_outcome_unknown"})


def _utc_now_text() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="microseconds").replace("+00:00", "Z")


def _declares_day_scoped_ids(provider: Any, exchange_name: str) -> bool:
    """The provider's ``execution_ids_trading_day_scoped`` declaration.

    Absent means ``False``.  A value other than the bools ``True``/``False``
    (or an unreadable attribute) is refused: guessing either way could drop a
    fill or change another route's replay semantics.
    """

    try:
        value = getattr(provider, _DAY_SCOPED_IDS_ATTRIBUTE, False)
    except Exception:
        value = None
    if value is True or value is False:
        return value
    raise DirectWriteError(
        "direct_write_provider_declaration_invalid",
        f"{exchange_name}: {_DAY_SCOPED_IDS_ATTRIBUTE} must be True or False",
    )


def _event_trading_day(event: DirectExecutionEvent) -> Optional[str]:
    """The one ``YYYYMMDD`` trading day a return names, else ``None``.

    Read from the identity fields and the event field; ``None`` when neither
    carries one, when one is not an 8-digit ASCII text, or when the two differ.
    """

    identity = event.identity
    named = [event.trading_day]
    if identity is not None:
        named.append(identity.fields.get("trading_day"))
    days = {day for day in named if day is not None}
    if len(days) != 1:
        return None
    (day,) = days
    if type(day) is not str or len(day) != 8 or not (day.isascii() and day.isdigit()):
        return None
    return day


@dataclass(frozen=True)
class DirectWriteOutcome:
    """Result of one coordinated write.

    ``sent`` is ``True`` when the native call returned success, ``False`` when
    the request was definitely not sent, and ``None`` when the outcome is
    unknown (the host must keep its marker UNKNOWN and recover by identity).
    """

    sent: Optional[bool]
    identity: Optional[NativeOrderIdentity]
    marker_id: Optional[str]
    error_code: Optional[str] = None
    detail: Optional[str] = None


@dataclass(frozen=True)
class CorrelatedDirectEvent:
    """A provider return plus the SDK correlation verdict.

    ``evidence`` is ``direct_identity_match`` (``matched``), or for an
    unattributed return ``weak_key_candidate`` (it shares only a weak trading
    day and client order id key with an own order, e.g. another CTP session's
    equal ``OrderRef``) or ``unmatched``.
    """

    event: DirectExecutionEvent
    matched: bool
    evidence: str
    marker_id: Optional[str]
    host_scope: Optional[str]


class _Correlator:
    """Maps venue identities to host markers and de-duplicates returns.

    Strong keys attribute a return: the session triple (``FrontID``,
    ``SessionID``, ``OrderRef``), the venue order id (``ExchangeID``,
    ``OrderSysID``) and the (trading day, client order id) key of an identity
    without session fields (OKX/Binance: client order ids are unique per
    account).  The (trading day, client order id) key of a session-scoped
    identity is weak: CTP ``OrderRef`` is unique only within one
    ``FrontID``/``SessionID``, so another session or terminal of the same
    account may use the same value on the same day.  A weak key never
    attributes a return; a hit is only reported as a candidate (INV63-14).

    ``day_scoped_ids`` is the route provider's declaration: its returns are
    de-duplicated per trading day (:meth:`seen_key`).
    """

    def __init__(self, *, day_scoped_ids: bool = False) -> None:
        self._lock = threading.Lock()
        self._day_scoped_ids = day_scoped_ids
        self._by_key: dict[tuple[Any, ...], tuple[Optional[str], Optional[str]]] = {}
        self._weak_keys: dict[tuple[Any, ...], tuple[Optional[str], Optional[str]]] = {}
        self._seen: OrderedDict[tuple[Any, ...], None] = OrderedDict()

    @staticmethod
    def _keys(identity: NativeOrderIdentity) -> list[tuple[tuple[Any, ...], bool]]:
        """``(key, strong)`` pairs of ``identity``."""
        fields = identity.fields
        exchange = identity.exchange_name
        keys: list[tuple[tuple[Any, ...], bool]] = []
        ref = fields.get("order_ref") or identity.client_order_id
        # Session-scoped as soon as the identity names either session field,
        # even an unreadable one: its client id is then not account-unique.
        session_scoped = "front_id" in fields or "session_id" in fields
        if ref and fields.get("front_id") is not None and fields.get("session_id") is not None:
            keys.append(
                (("session", exchange, fields.get("front_id"), fields.get("session_id"), ref), True)
            )
        if ref and fields.get("trading_day"):
            keys.append((("day", exchange, fields.get("trading_day"), ref), not session_scoped))
        if fields.get("order_sys_id") and fields.get("exchange_id"):
            keys.append(
                (("venue", exchange, fields.get("exchange_id"), fields.get("order_sys_id")), True)
            )
        return keys

    def register(
        self, identity: NativeOrderIdentity, marker_id: Optional[str], host_scope: Optional[str]
    ) -> None:
        with self._lock:
            for key, strong in self._keys(identity):
                if strong:
                    self._by_key[key] = (marker_id, host_scope)
                else:
                    self._weak_keys[key] = (marker_id, host_scope)

    def lookup(
        self, identity: Optional[NativeOrderIdentity]
    ) -> Optional[tuple[Optional[str], Optional[str]]]:
        """The host marker of ``identity`` through a strong key only."""
        if identity is None:
            return None
        with self._lock:
            for key, _strong in self._keys(identity):
                found = self._by_key.get(key)
                if found is not None:
                    return found
        return None

    def weak_candidate(self, identity: Optional[NativeOrderIdentity]) -> bool:
        """``True`` when ``identity`` hits only a weak key (never an attribution)."""
        if identity is None:
            return False
        with self._lock:
            return any(key in self._weak_keys for key, _strong in self._keys(identity))

    def seen_key(self, event: DirectExecutionEvent) -> Optional[tuple[Any, ...]]:
        """The de-duplication key of ``event``; ``None`` when it is never de-duplicated.

        On a route declaring trading-day scoped ids the key holds the return's
        trading day; a return without one readable day gets ``None`` so it is
        never dropped as a replay of another day's return.
        """

        key = event.dedup_key()
        if not self._day_scoped_ids:
            return key
        day = _event_trading_day(event)
        if day is None:
            return None
        return ("trading_day", day, key)

    def first_seen(self, key: tuple[Any, ...]) -> bool:
        with self._lock:
            if key in self._seen:
                return False
            self._seen[key] = None
            if len(self._seen) > _DEDUP_CAPACITY:
                self._seen.popitem(last=False)
            return True


class DirectWriteCoordinator:
    """SDK coordinator for direct writes and their returns on all routes."""

    def __init__(
        self,
        admission: WriteAdmission,
        provider_factory: Callable[[str], Any],
    ) -> None:
        self._admission = admission
        self._provider_factory = provider_factory
        self._providers: dict[str, Any] = {}
        self._lock = threading.RLock()
        self._correlators: dict[str, _Correlator] = {}

    @property
    def admission(self) -> WriteAdmission:
        return self._admission

    def provider(self, exchange_name: str) -> Any:
        # A provider, once created, is never replaced or removed, so a created
        # one is returned without the write lock: polling returns and events
        # must not wait behind a write that holds the lock (a settlement
        # confirmation can hold it for seconds on CTP).  Its correlator is
        # stored first, so a visible provider always has one.
        provider = self._providers.get(exchange_name)
        if provider is not None:
            return provider
        with self._lock:
            provider = self._providers.get(exchange_name)
            if provider is None:
                provider = self._provider_factory(exchange_name)
                if provider is None:
                    raise DirectWriteError("direct_write_provider_unavailable", exchange_name)
                # An invalid declaration raises here: the provider is never
                # registered, so nothing is placed or polled on that route.
                day_scoped_ids = _declares_day_scoped_ids(provider, exchange_name)
                self._correlators[exchange_name] = _Correlator(day_scoped_ids=day_scoped_ids)
                self._providers[exchange_name] = provider
            return provider

    def close(self) -> None:
        """Close every created provider that owns resources (e.g. a monitor thread).

        Providers stay registered (a closed provider refuses or degrades on its
        own); a close failure of one provider never skips the others.
        """
        for exchange_name, provider in tuple(self._providers.items()):
            close = getattr(provider, "close", None)
            if not callable(close):
                continue
            try:
                close()
            except Exception as exc:
                from bt_api_base.logging_factory import get_logger

                get_logger("api").warning(
                    f"direct write provider close failed for {exchange_name}: {type(exc).__name__}"
                )

    def _correlator(self, exchange_name: str) -> _Correlator:
        self.provider(exchange_name)
        return self._correlators[exchange_name]

    # ------------------------------------------------------------------
    @staticmethod
    def _commit(port: PreDispatchCommitPort, record: PreDispatchRecord) -> Optional[str]:
        if port is None or not callable(getattr(port, "commit", None)):
            return None
        try:
            marker = port.commit(record)
        except Exception:
            return None
        return marker if type(marker) is str and marker else None

    def place(
        self,
        exchange_name: str,
        request: Any,
        *,
        commit_port: PreDispatchCommitPort,
        host_scope: str,
        request_summary: Optional[Mapping[str, Any]] = None,
    ) -> DirectWriteOutcome:
        if not self._admission.is_enabled(exchange_name):
            raise WriteAdmissionDenied("write_admission_disabled", exchange_name)
        provider = self.provider(exchange_name)
        with self._lock:
            reservation = provider.reserve_order(request)
            identity = reservation.identity
            record = PreDispatchRecord(
                operation=WriteOperation.PLACE,
                host_scope=host_scope,
                exchange_name=exchange_name,
                symbol=reservation.symbol,
                identity=identity,
                target_identity=None,
                request_summary=dict(request_summary or {}),
                trading_day=identity.fields.get("trading_day"),
                session_generation=identity.session_generation,
                created_at_utc=_utc_now_text(),
            )
            marker = self._commit(commit_port, record)
            if marker is None:
                provider.release_reservation(reservation)
                return DirectWriteOutcome(
                    sent=False,
                    identity=identity,
                    marker_id=None,
                    error_code="pre_dispatch_commit_failed",
                )
            generation = provider.session_generation()
            if generation != identity.session_generation:
                provider.release_reservation(reservation)
                return DirectWriteOutcome(
                    sent=False,
                    identity=identity,
                    marker_id=marker,
                    error_code="session_changed_before_dispatch",
                )
            try:
                grant = self._admission.issue(exchange_name, WriteOperation.PLACE, generation)
            except WriteAdmissionDenied as exc:
                provider.release_reservation(reservation)
                return DirectWriteOutcome(False, identity, marker, exc.code, exc.detail)
            self._correlator(exchange_name).register(identity, marker, host_scope)
            try:
                result = provider.submit_order(reservation, request, grant, self._admission)
            except WriteAdmissionDenied as exc:
                return DirectWriteOutcome(False, identity, marker, exc.code, exc.detail)
            except DirectWriteError as exc:
                if exc.code in _UNKNOWN_OUTCOME_CODES:
                    return DirectWriteOutcome(None, identity, marker, exc.code, exc.detail)
                return DirectWriteOutcome(False, identity, marker, exc.code, exc.detail)
            except Exception as exc:  # the native call may have happened
                return DirectWriteOutcome(
                    None, identity, marker, "direct_submit_outcome_unknown", type(exc).__name__
                )
            return DirectWriteOutcome(
                sent=result.sent,
                identity=result.identity or identity,
                marker_id=marker,
                error_code=result.error_code,
                detail=result.detail,
            )

    def cancel(
        self,
        exchange_name: str,
        target: NativeOrderIdentity,
        symbol: str,
        *,
        commit_port: PreDispatchCommitPort,
        host_scope: str,
    ) -> DirectWriteOutcome:
        if not self._admission.is_enabled(exchange_name):
            raise WriteAdmissionDenied("write_admission_disabled", exchange_name)
        provider = self.provider(exchange_name)
        with self._lock:
            generation = provider.session_generation()
            record = PreDispatchRecord(
                operation=WriteOperation.CANCEL,
                host_scope=host_scope,
                exchange_name=exchange_name,
                symbol=symbol,
                identity=None,
                target_identity=target,
                request_summary={},
                trading_day=target.fields.get("trading_day"),
                session_generation=generation,
                created_at_utc=_utc_now_text(),
            )
            marker = self._commit(commit_port, record)
            if marker is None:
                return DirectWriteOutcome(False, target, None, "pre_dispatch_commit_failed")
            try:
                grant = self._admission.issue(exchange_name, WriteOperation.CANCEL, generation)
                result = provider.cancel_order(target, symbol, grant, self._admission)
            except WriteAdmissionDenied as exc:
                return DirectWriteOutcome(False, target, marker, exc.code, exc.detail)
            except DirectWriteError as exc:
                return DirectWriteOutcome(False, target, marker, exc.code, exc.detail)
            except Exception as exc:
                return DirectWriteOutcome(
                    None, target, marker, "direct_cancel_outcome_unknown", type(exc).__name__
                )
            return DirectWriteOutcome(result.sent, target, marker, result.error_code, result.detail)

    def ensure_settlement(
        self,
        exchange_name: str,
        *,
        commit_port: PreDispatchCommitPort,
        host_scope: str,
    ) -> SettlementStatus:
        """Confirm today's settlement when needed (D62-20; D62-29 exception)."""

        provider = self.provider(exchange_name)
        status_reader = getattr(provider, "settlement_status", None)
        if not callable(status_reader):
            return SettlementStatus.CONFIRMED  # venue has no settlement step
        status = status_reader()
        if status is SettlementStatus.CONFIRMED:
            return status
        if status is SettlementStatus.UNKNOWN:
            return status
        if not self._admission.is_enabled(exchange_name):
            raise WriteAdmissionDenied("write_admission_disabled", exchange_name)
        with self._lock:
            generation = provider.session_generation()
            record = PreDispatchRecord(
                operation=WriteOperation.SETTLEMENT_CONFIRM,
                host_scope=host_scope,
                exchange_name=exchange_name,
                symbol=None,
                identity=None,
                target_identity=None,
                request_summary={},
                trading_day=provider.trading_day(),
                session_generation=generation,
                created_at_utc=_utc_now_text(),
            )
            if self._commit(commit_port, record) is None:
                return SettlementStatus.UNKNOWN
            grant = self._admission.issue(
                exchange_name, WriteOperation.SETTLEMENT_CONFIRM, generation
            )
            try:
                return provider.confirm_settlement(grant, self._admission)
            except (WriteAdmissionDenied, DirectWriteError):
                return SettlementStatus.UNKNOWN

    # ------------------------------------------------------------------
    def register_restored_identity(
        self,
        exchange_name: str,
        identity: NativeOrderIdentity,
        marker_id: Optional[str],
        host_scope: Optional[str],
    ) -> None:
        """Re-attach a persisted host marker after a restart (D62-14/16).

        Pass the identity as reserved: a session-scoped (CTP) identity keeps
        its ``front_id``/``session_id`` so that its (trading day, OrderRef)
        key stays weak; without them that key is taken as account-unique.
        """

        self._correlator(exchange_name).register(identity, marker_id, host_scope)

    def poll_events(self, exchange_name: str, max_events: int = 256) -> tuple[CorrelatedDirectEvent, ...]:
        """Drain the route's returns, drop replays and correlate the rest.

        Replays are dropped per the route's de-duplication scope (module
        docstring): per trading day on a provider declaring trading-day scoped
        ids, where a return without a readable trading day is always delivered.
        """

        provider = self.provider(exchange_name)
        correlator = self._correlator(exchange_name)
        out: list[CorrelatedDirectEvent] = []
        for event in provider.drain_execution_events(max_events):
            seen_key = correlator.seen_key(event)
            if seen_key is not None and not correlator.first_seen(seen_key):
                continue
            found = correlator.lookup(event.identity)
            if found is None and event.exchange_order_id and event.identity is not None:
                venue_key_identity = NativeOrderIdentity(
                    exchange_name=event.exchange_name,
                    client_order_id=None,
                    fields={
                        "exchange_id": event.identity.fields.get("exchange_id"),
                        "order_sys_id": event.exchange_order_id,
                    },
                )
                found = correlator.lookup(venue_key_identity)
            if (
                found is not None
                and event.kind is DirectExecutionEventKind.ORDER
                and event.identity is not None
                and event.exchange_order_id
            ):
                # Learn the venue identity so later trades (no FrontID/SessionID)
                # correlate by ExchangeID + OrderSysID.  Only a strong match
                # teaches it: a weak (trading day, OrderRef) hit may be another
                # session's order, whose fills must stay unattributed.
                correlator.register(event.identity, found[0], found[1])
            if found is not None:
                evidence = "direct_identity_match"
            elif correlator.weak_candidate(event.identity):
                evidence = "weak_key_candidate"
            else:
                evidence = "unmatched"
            out.append(
                CorrelatedDirectEvent(
                    event=event,
                    matched=found is not None,
                    evidence=evidence,
                    marker_id=None if found is None else found[0],
                    host_scope=None if found is None else found[1],
                )
            )
        return tuple(out)

    def dropped_events(self, exchange_name: str) -> Optional[int]:
        """Cumulative count of returns the route's provider lost before any poll.

        The provider's own ``dropped_events`` (queue overflow, projection
        failures).  ``None`` when no provider was created for the route yet or
        its counter is missing, not a non-negative ``int``, or unreadable: an
        unknown count is never reported as zero.  Reading creates nothing.
        """

        provider = self._providers.get(exchange_name)
        if provider is None:
            return None
        try:
            value = getattr(provider, "dropped_events", None)
        except Exception:
            return None
        if type(value) is not int or value < 0:
            return None
        return value
