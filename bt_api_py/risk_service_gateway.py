"""Gateway-side ports of the independent Risk service process (iteration 64, 64.09).

The adapters implement ``bt_api_risk.pretrade.ports`` over the SDK Gateway
clients and run only on the Risk process owner thread:

* :class:`DelegateOrderPort` (``GatewayOrderPort``): one delegate runtime and
  ``GatewayDelegateServiceClient`` per represented (strategy, live run), G64-11.
* :class:`GatewayControlAdapter` (``GatewayControlPort``): the Risk control
  principal (APPLICATION_CLIENT, grant kind ``risk``) on ``application.direct_write``.
* :class:`RiskFactsCache` (``FactsPort``): in-memory facts fed by the Risk market
  subscription, the operator account snapshot and the observation stream; reads
  do no I/O. Instrument multiplier and tick come from the service configuration
  (the Gateway instrument metadata read is granted to strategy principals only).
  Strategy financial facts come from ``account.strategy_financial`` (G64-06 b).
* :class:`ObservationPump`: account observation stream to
  ``engine.apply_observations``; ACK only up to the durably applied sequence.

Outcome mapping is exact: ``TRANSPORT_NOT_STARTED`` and ``REQUEST_DENIED`` only
when the SDK client says so (or when no runtime exists for the route, which is
the same not-sent fact); everything else is ``UNKNOWN``. Reply values are
claims of the Gateway; the engine's proof rules turn them into state.
``bt_api_risk`` is imported lazily: this module runs only in the Risk process.
"""

from __future__ import annotations

import json
import logging
import math
import os
import re
import threading
import time
from collections.abc import Callable, Mapping
from dataclasses import dataclass, replace
from datetime import UTC, date, datetime
from decimal import Decimal, InvalidOperation
from functools import wraps
from typing import Any

from bt_api_base.gateway_delegation_protocol import GatewayDelegatedCommand
from bt_api_base.gateway_direct_protocol import gateway_payload_digest
from bt_api_base.market_protocol import MarketInstrumentKey, MarketSubscriptionKey
from bt_api_base.service_protocol import MAX_REFERENCE_BYTES, WireIdentity, WireScope

from bt_api_py.gateway_delegate_service_client import runtime_failure
from bt_api_py.gateway_execution_stream_client import _detach_wire
from bt_api_py.gateway_execution_stream_client import _epoch as _wire_epoch
from bt_api_py.gateway_execution_stream_client import _sequence as _wire_sequence
from bt_api_py.service_runtime_cleanup import runtime_cleanup_confirmed
from bt_api_py.service_runtime_thread import BoundCallObservation, BoundCallResult, BoundCallRoute

_STATES = frozenset({"RESERVED", "DISPATCHING", "SENT", "NOT_SENT", "UNKNOWN", "RELEASED", "EXPIRED"})
_DELEGATE_OUTCOMES = frozenset(
    {"OBSERVED", "QUEUED", "REQUEST_DENIED", "TRANSPORT_NOT_STARTED", "UNKNOWN"}
)
_ACTOR_OPEN_STATES = frozenset({"DISPATCHING", "SENT", "UNKNOWN"})
CONTROL_OPERATIONS = frozenset(
    {
        "direct.control.identity",
        "direct.control.apply",
        "direct.control.status",
        "direct.control.list",
        "direct.control.cancel_scope",
        "direct.control.cancel_scope_status",
    }
)
CONTROL_INTENT_APPLY_OPERATION = "direct.control.intent.apply"
CONTROL_INTENT_OPERATIONS = CONTROL_OPERATIONS | frozenset(
    {CONTROL_INTENT_APPLY_OPERATION}
)
_CONTROL_BINDING_CAPACITY = 8192
_CANCEL_SCOPE_LIMIT = 256
_OBSERVER_LIMIT = 256
# Observer refusals that mean the reading position is no longer valid: replay from 0.
_REPLAY_CODES = frozenset({"EPOCH_MISMATCH", "INVALID_SEQUENCE", "CONSUMER_REPLAY_REQUIRED"})


_LOG = logging.getLogger(__name__)


def _ref(value: object) -> bool:
    return (
        type(value) is str
        and bool(value)
        and value == value.strip()
        and value.isprintable()
        and len(value.encode("utf-8", errors="replace")) <= 256
    )


def _code(value: object) -> str | None:
    return value if _ref(value) else None


def _utc_ns(text: object) -> int | None:
    if type(text) is not str or not text.endswith("Z"):
        return None
    try:
        value = datetime.fromisoformat(text[:-1] + "+00:00").astimezone(UTC)
    except (ValueError, OverflowError):
        return None
    delta = value - datetime(1970, 1, 1, tzinfo=UTC)
    ns = (delta.days * 86400 + delta.seconds) * 1_000_000_000 + delta.microseconds * 1000
    return ns if ns >= 0 else None


def _day(value: object) -> str | None:
    if type(value) is not str or len(value) != 8 or not value.isascii() or not value.isdigit():
        return None
    try:
        parsed = date.fromisoformat(value)
    except ValueError:
        return None
    return value if parsed.isoformat().replace("-", "") == value else None


def _tick_day(value: object) -> str | None:
    """A tick's ``trading_date`` as ``YYYYMMDD``; None unless it is a real calendar date.

    ``MarketTickPayload`` normalizes the date to ISO ``YYYY-MM-DD``; the compact
    form is accepted as well. Anything else leaves the day unknown.
    """
    if type(value) is not str or len(value) not in (8, 10):
        return None
    try:
        parsed = date.fromisoformat(value)
    except ValueError:
        return None
    compact = parsed.strftime("%Y%m%d")
    return compact if value in (compact, parsed.isoformat()) else None


def _decimal(value: object, *, positive: bool) -> Decimal | None:
    if value is None or type(value) is bool:
        return None
    try:
        if type(value) is float:
            if not math.isfinite(value):
                return None
            number = Decimal(repr(value))
        elif type(value) in (str, int, Decimal):
            number = Decimal(value)
        else:
            return None
    except (InvalidOperation, ValueError):
        return None
    if not number.is_finite() or number < 0 or (positive and number == 0):
        return None
    return number


# ============================================================================ order port


def map_delegate_result(result: Any) -> Any:
    """``GatewayDelegateResult`` -> ``GatewayReply``; anything unproven is UNKNOWN."""
    from bt_api_risk.pretrade import GatewayReply

    outcome = getattr(result, "outcome", None)
    code = _code(getattr(result, "code", None))
    if outcome not in _DELEGATE_OUTCOMES:
        return GatewayReply("UNKNOWN", code="RISK_DELEGATE_REPLY_INVALID")
    if outcome == "OBSERVED":
        return _observation_reply(getattr(result, "observation", None), code)
    if outcome == "QUEUED":
        # Callers handle QUEUED with their own context (dispatch only).
        return GatewayReply("UNKNOWN", code="RISK_DELEGATE_REPLY_INVALID")
    return GatewayReply(outcome, code=code)


def _observation_reply(observation: Any, code: str | None = None) -> Any:
    from bt_api_risk.pretrade import GatewayReply

    try:
        if observation is None or observation.state not in _STATES:
            raise ValueError
        return GatewayReply(
            "OBSERVED",
            state=observation.state,
            reservation_id=observation.reservation_id,
            version=observation.version,
            digest=observation.digest,
            owner_generation=observation.owner_generation,
            reservation_generation=observation.reservation_generation,
            code=code,
        )
    except Exception:
        return GatewayReply("UNKNOWN", code="RISK_DELEGATE_REPLY_INVALID")


@dataclass
class _DelegateSlot:
    run_id: str
    handle: Any = None  # object with .client, .host and .close()
    failed_at: float | None = None
    blocked: bool = False
    invalid: bool = False
    invalid_generation: int = 0
    connection_attempted: bool = False


def _guard_delegate_cleanup(method: Callable[..., Any]) -> Callable[..., Any]:
    """Discard a reply if a reentrant callback makes cleanup uncertain."""
    @wraps(method)
    def guarded(self: Any, *args: Any, **kwargs: Any) -> Any:
        result = method(self, *args, **kwargs)
        if not self._sync_cleanup_uncertain():
            return result
        from bt_api_risk.pretrade import GatewayReply

        return GatewayReply("UNKNOWN", code="ROUTE_UNAVAILABLE")

    return guarded


class DelegateOrderPort:
    """``GatewayOrderPort`` over per-(strategy, run) delegate clients (G64-11).

    ``connect(route)`` builds and starts one delegate runtime and returns a
    handle with ``client`` (``GatewayDelegateServiceClient``), ``host``
    (``GatewayRiskDelegateHost``) and ``close()``; it raises when the Gateway
    does not register the delegate. The current run of a strategy is the run of
    its latest authenticated call to this Risk service; a call for any other run
    gets no client (the request never leaves this process).
    """

    def __init__(
        self,
        *,
        account_scope: str,
        execution_domain_ref: str,
        identity: WireIdentity,
        service_id: str,
        instance_id: str,
        scope_ref: str,
        grant_revisions: Mapping[str, str],
        instrument_for: Callable[[object, object], MarketInstrumentKey | None],
        connect: Callable[[Any], Any],
        timeout_seconds: float,
        retry_seconds: float,
        monotonic: Callable[[], float] = time.monotonic,
        on_cleanup_uncertain: Callable[[object, str], None] | None = None,
        is_cleanup_uncertain: Callable[[], bool] | None = None,
    ) -> None:
        self._account, self._domain = account_scope, execution_domain_ref
        self._identity = identity
        self._service_id, self._instance_id, self._scope_ref = service_id, instance_id, scope_ref
        self._revisions = dict(grant_revisions)
        self._instrument_for = instrument_for
        self._connect = connect
        self._timeout = float(timeout_seconds)
        self._retry = float(retry_seconds)
        self._monotonic = monotonic
        self._slots: dict[str, _DelegateSlot] = {}
        self._reserving: set[str] = set()
        self._handling: set[str] = set()
        self._closing = False
        self._cleanup_in_progress = False
        self._cleanup_uncertain = False
        self._cleanup_resource: object | None = None
        self._on_cleanup_uncertain = on_cleanup_uncertain
        self._is_cleanup_uncertain = is_cleanup_uncertain
        self._pid, self._thread = os.getpid(), threading.current_thread()

    def _owned(self) -> None:
        if os.getpid() != self._pid or threading.current_thread() is not self._thread:
            raise RuntimeError("RISK_DELEGATE_WRONG_OWNER")

    @property
    def cleanup_uncertain(self) -> bool:
        self._owned()
        return self._sync_cleanup_uncertain()

    def _sync_cleanup_uncertain(self) -> bool:
        if not self._cleanup_uncertain and self._is_cleanup_uncertain is not None:
            try:
                value = self._is_cleanup_uncertain()
            except BaseException:
                self._cleanup_uncertain = True
                raise
            if type(value) is not bool:
                self._cleanup_uncertain = True
                raise RuntimeError("RISK_DELEGATE_CLEANUP_STATE_INVALID")
            if value:
                self._cleanup_uncertain = True
        return self._cleanup_uncertain

    def _cleanup_failed(self, resource: object, reason: str) -> None:
        self._cleanup_resource = resource
        self._cleanup_uncertain = True
        if self._on_cleanup_uncertain is not None:
            try:
                self._on_cleanup_uncertain(resource, reason)
            except Exception:
                _LOG.warning("Risk delegate cleanup uncertainty callback failed")

    def route_for(self, strategy_ref: str, run_id: str) -> Any:
        from bt_api_py.gateway_delegate_service_client import GatewayDelegateRoute

        scope = WireScope(
            self._domain, account_ref=self._account, strategy_ref=strategy_ref, run_id=run_id
        )
        return GatewayDelegateRoute(
            "upstream.direct_write",
            self._service_id,
            self._instance_id,
            self._scope_ref,
            scope,
            self._identity,
        )

    def note_run(self, strategy_ref: str, run_id: str) -> None:
        """Record the strategy's authenticated run; a new run retires the old client."""
        self._owned()
        if self._sync_cleanup_uncertain() or self._cleanup_in_progress:
            return
        if not _ref(strategy_ref) or not _ref(run_id) or strategy_ref not in self._revisions:
            return
        if strategy_ref in self._reserving or strategy_ref in self._handling or self._closing:
            # A close/connect callback cannot replace a slot while a reserve
            # is acquiring it on the same owner thread.
            return
        slot = self._slots.get(strategy_ref)
        if slot is not None and slot.blocked:
            # A missing handle can still mean an unconfirmed registration or
            # failed cleanup; it is not permission to replace the slot.
            return
        if slot is not None and slot.run_id != run_id:
            if slot.handle is None and not slot.connection_attempted and not slot.invalid:
                # This slot was only named locally; no registration was attempted.
                self._slots[strategy_ref] = _DelegateSlot(run_id)
                return
            slot.blocked = True  # never use the superseded run while cleanup is unresolved
            generation = slot.invalid_generation
            self._handling.add(strategy_ref)
            try:
                if not self._close_slot(slot):
                    return
            finally:
                self._handling.discard(strategy_ref)
            if slot.invalid_generation != generation:
                return
            slot = None
        if slot is None:
            self._slots[strategy_ref] = _DelegateSlot(run_id)

    def current_run(self, strategy_ref: str) -> str | None:
        self._owned()
        if self._sync_cleanup_uncertain() or self._cleanup_in_progress:
            return None
        slot = self._slots.get(strategy_ref)
        return None if slot is None else slot.run_id

    def invalidate_registrations(self) -> None:
        """Retire old Gateway registrations lazily; never resend a command."""
        self._owned()
        if self._sync_cleanup_uncertain():
            return
        for slot in self._slots.values():
            slot.invalid = True
            slot.invalid_generation += 1

    def _close_slot(self, slot: _DelegateSlot) -> bool:
        if self._sync_cleanup_uncertain() or self._cleanup_in_progress:
            return False
        handle = slot.handle
        if handle is not None:
            connection_attempted = slot.connection_attempted
            self._cleanup_in_progress = True
            try:
                try:
                    snapshot = handle.close()
                    if self._sync_cleanup_uncertain():
                        return False
                    confirmed = runtime_cleanup_confirmed(snapshot)
                    if self._sync_cleanup_uncertain():
                        return False
                except BaseException as error:
                    if self._sync_cleanup_uncertain():
                        raise
                    slot.blocked = True
                    self._cleanup_failed(handle, "HANDLE_CLOSE_EXCEPTION")
                    if not isinstance(error, Exception):
                        raise
                    _LOG.warning("Risk delegate client close failed; cleanup uncertain")
                    return False
                if not confirmed:
                    slot.blocked = True
                    self._cleanup_failed(handle, "HANDLE_CLOSE_UNCONFIRMED")
                    return False
            finally:
                self._cleanup_in_progress = False
            if slot.handle is not handle:
                slot.blocked = True
                return False
            slot.handle = None
            if slot.connection_attempted != connection_attempted:
                slot.connection_attempted = True
                slot.blocked = True
                return False
            slot.connection_attempted = False
            return True
        if slot.connection_attempted:
            slot.blocked = True
            self._cleanup_in_progress = True
            try:
                self._cleanup_failed(slot, "SLOT_REGISTRATION_UNCONFIRMED")
            finally:
                self._cleanup_in_progress = False
            return False
        return not slot.blocked

    def _handle(self, strategy_ref: str, run_id: str | None, *, reserve_owner: bool = False) -> Any:
        self._owned()
        if self._sync_cleanup_uncertain() or self._cleanup_in_progress:
            return None
        if not _ref(run_id) or strategy_ref not in self._revisions:
            return None
        if (self._closing or strategy_ref in self._handling
                or (strategy_ref in self._reserving and not reserve_owner)):
            return None
        self._handling.add(strategy_ref)
        try:
            return self._handle_owned(strategy_ref, run_id, reserve_owner=reserve_owner)
        finally:
            self._handling.discard(strategy_ref)

    def _handle_owned(self, strategy_ref: str, run_id: str, *, reserve_owner: bool) -> Any:
        slot = self._slots.get(strategy_ref)
        if slot is None:
            # Recovery before the strategy called: adopt the permit's run.
            slot = self._slots[strategy_ref] = _DelegateSlot(run_id)  # type: ignore[arg-type]
        if slot.run_id != run_id or slot.blocked:
            return None  # a superseded run: the Gateway no longer registers it
        if slot.invalid:
            invalid_generation = slot.invalid_generation
            if not self._close_slot(slot):
                if reserve_owner and not self._sync_cleanup_uncertain():
                    slot.blocked = True
                return None
            if slot.blocked or slot.invalid_generation != invalid_generation:
                slot.blocked = True
                return None
            slot.invalid = False
            slot.failed_at = None
        if slot.handle is not None:
            try:
                runtime = getattr(slot.handle, "runtime", None)
            except Exception:
                if reserve_owner and not self._sync_cleanup_uncertain():
                    slot.blocked = True
                return None
            if self._sync_cleanup_uncertain():
                return None
            if runtime is not None:
                try:
                    state = runtime.status().state
                except Exception:
                    return None
                if self._sync_cleanup_uncertain():
                    return None
                if state in ("failed", "closed", "close_failed", "stopping"):
                    invalid_generation = slot.invalid_generation
                    if not self._close_slot(slot):
                        if reserve_owner and not self._sync_cleanup_uncertain():
                            slot.blocked = True
                        return None
                    if slot.blocked or slot.invalid_generation != invalid_generation:
                        slot.blocked = True
                        return None
                    slot.failed_at = None
        if slot.handle is None:
            failed_at = slot.failed_at
            if failed_at is not None:
                now = self._monotonic()
                if self._sync_cleanup_uncertain():
                    return None
                if now - failed_at < self._retry:
                    return None
            try:
                slot.connection_attempted = True
                slot.handle = self._connect(self.route_for(strategy_ref, run_id))
                if self._sync_cleanup_uncertain():
                    return None
                slot.failed_at = None
                if slot.handle is None:
                    slot.blocked = True
            except BaseException as error:
                if self._sync_cleanup_uncertain():
                    raise
                # A generic connect failure does not prove that an attempted
                # registration was cleaned up, even on a read-only call.
                slot.blocked = True
                slot.handle = None
                if not isinstance(error, Exception):
                    raise
                try:
                    failed_at = self._monotonic()
                except Exception:
                    failed_at = None
                if not self._sync_cleanup_uncertain():
                    slot.failed_at = failed_at
                return None
        if slot.blocked or slot.invalid:
            if slot.invalid:
                slot.blocked = True
            return None
        if slot.handle is not None:
            try:
                client = slot.handle.client
                if self._sync_cleanup_uncertain():
                    return None
                reserve = getattr(client, "reserve", None)
                if self._sync_cleanup_uncertain():
                    return None
                close = getattr(slot.handle, "close", None)
                if self._sync_cleanup_uncertain():
                    return None
                host = getattr(slot.handle, "host", None)
                if self._sync_cleanup_uncertain():
                    return None
                if not callable(reserve) or not callable(close) or host is None:
                    if not self._sync_cleanup_uncertain():
                        slot.blocked = True
                    return None
            except Exception:
                if not self._sync_cleanup_uncertain():
                    slot.blocked = True
                return None
        if self._sync_cleanup_uncertain():
            return None
        return slot.handle

    @staticmethod
    def _no_route() -> Any:
        from bt_api_risk.pretrade import GatewayReply

        return GatewayReply("TRANSPORT_NOT_STARTED", code="ROUTE_UNAVAILABLE")

    def _binding(self, strategy_ref: str, run_id: str | None, handle: Any) -> Callable[[], bool]:
        if self._sync_cleanup_uncertain():
            return lambda: False
        slot = self._slots.get(strategy_ref)
        generation = None if slot is None else slot.invalid_generation

        def same_slot() -> bool:
            return (not self._sync_cleanup_uncertain() and not self._cleanup_in_progress
                    and slot is not None and self._slots.get(strategy_ref) is slot
                    and slot.run_id == run_id and not slot.blocked and not slot.invalid
                    and slot.invalid_generation == generation and slot.handle is handle)

        return same_slot

    @staticmethod
    def _bound_reply(same_slot: Callable[[], bool], reply: Any) -> Any:
        if same_slot():
            return reply
        from bt_api_risk.pretrade import GatewayReply

        return GatewayReply("UNKNOWN", code="ROUTE_UNAVAILABLE")

    @_guard_delegate_cleanup
    def reserve(
        self,
        *,
        strategy_ref: str,
        run_id: str | None,
        request_key: str,
        canonical_payload: str,
        parent_ref: str | None,
        child_id: str | None,
        ttl_seconds: float,
    ) -> Any:
        from bt_api_risk.pretrade import GatewayReply

        from bt_api_py.gateway_order_contract import decode_gateway_order_request

        self._owned()
        unavailable = GatewayReply("UNKNOWN", code="ROUTE_UNAVAILABLE")
        if (self._sync_cleanup_uncertain() or self._cleanup_in_progress
                or not _ref(strategy_ref) or not _ref(run_id) or strategy_ref not in self._revisions
                or strategy_ref in self._reserving):
            return unavailable
        slot = self._slots.get(strategy_ref)
        if slot is not None and slot.run_id != run_id:
            return unavailable
        self._reserving.add(strategy_ref)
        try:
            handle = self._handle(strategy_ref, run_id, reserve_owner=True)
            if self._sync_cleanup_uncertain():
                return unavailable
            slot = self._slots.get(strategy_ref)
            if (handle is None or slot is None or slot.run_id != run_id or slot.blocked
                    or slot.invalid or slot.handle is not handle):
                if slot is not None and (handle is None or slot.invalid):
                    # Do not permit a later read path to replace a handle
                    # invalidated during this acquisition without proof.
                    slot.blocked = True
                return unavailable
            try:
                client = handle.client
                if self._sync_cleanup_uncertain():
                    return unavailable
                reserve = getattr(client, "reserve", None)
                if self._sync_cleanup_uncertain():
                    return unavailable
                close = getattr(handle, "close", None)
                if self._sync_cleanup_uncertain():
                    return unavailable
                host = getattr(handle, "host", None)
                if self._sync_cleanup_uncertain():
                    return unavailable
                if not callable(reserve) or not callable(close) or host is None:
                    slot.blocked = True
                    return unavailable
            except Exception:
                if not self._sync_cleanup_uncertain():
                    slot.blocked = True
                return unavailable
            invalid_generation = slot.invalid_generation

            def same_slot() -> bool:
                return (not self._sync_cleanup_uncertain() and self._slots.get(strategy_ref) is slot and slot.run_id == run_id
                        and not slot.blocked and not slot.invalid
                        and slot.invalid_generation == invalid_generation and slot.handle is handle)

            payload = json.loads(canonical_payload)
            request = decode_gateway_order_request(payload, self._account)
            if self._sync_cleanup_uncertain():
                return unavailable
            instrument = self._instrument_for(request.exchange_id, request.symbol)
            if self._sync_cleanup_uncertain():
                return unavailable
            if type(instrument) is not MarketInstrumentKey:
                raise ValueError("RISK_INSTRUMENT_UNMAPPED")
            # Reentrant callbacks may invalidate a slot while parsing/mapping.
            if not same_slot():
                if not self._sync_cleanup_uncertain() and slot.invalid and slot.handle is not None:
                    slot.blocked = True
                return unavailable
            # parent_ref/child_id stay Risk bookkeeping: a ``risk`` delegate command has none.
            command = GatewayDelegatedCommand(
                self._account,
                strategy_ref,
                self._domain,
                self._identity.principal_ref,
                self._identity.actor_ref,
                "risk",
                run_id,  # type: ignore[arg-type]
                request_key,
                gateway_payload_digest(payload),
                instrument,
                self._revisions[strategy_ref],
            )
            if not same_slot():
                return unavailable
            result = reserve(
                command, request, ttl_seconds=ttl_seconds, timeout_seconds=self._timeout
            )
            # The call may already have reached Gateway.  A callback that
            # invalidated or retired this slot makes its reply inconclusive.
            if not same_slot():
                if not self._sync_cleanup_uncertain() and slot.invalid and slot.handle is not None:
                    slot.blocked = True
                return unavailable
            mapped = map_delegate_result(result)
            if not same_slot():
                if not self._sync_cleanup_uncertain() and slot.invalid and slot.handle is not None:
                    slot.blocked = True
                return unavailable
            return mapped
        finally:
            self._reserving.discard(strategy_ref)

    @_guard_delegate_cleanup
    def dispatch(
        self, *, strategy_ref: str, run_id: str | None, request_key: str, market_ref: dict | None
    ) -> Any:
        from bt_api_risk.pretrade import GatewayReply

        handle = self._handle(strategy_ref, run_id)
        if handle is None:
            return self._no_route()
        same_slot = self._binding(strategy_ref, run_id, handle)
        if not same_slot():
            return GatewayReply("UNKNOWN", code="ROUTE_UNAVAILABLE")
        client = handle.client
        if not same_slot():
            return GatewayReply("UNKNOWN", code="ROUTE_UNAVAILABLE")
        dispatch = client.dispatch
        if not same_slot():
            return GatewayReply("UNKNOWN", code="ROUTE_UNAVAILABLE")
        result = dispatch(
            request_key, market_ref=market_ref, timeout_seconds=self._timeout
        )
        if not same_slot():
            return GatewayReply("UNKNOWN", code="ROUTE_UNAVAILABLE")
        outcome = getattr(result, "outcome", None)
        if not same_slot():
            return GatewayReply("UNKNOWN", code="ROUTE_UNAVAILABLE")
        if outcome == "QUEUED":
            # The Gateway accepted the dispatch into its owner queue behind its own
            # dispatch marker (freeze 5, dispatch-3: QUEUED -> DISPATCHED).
            if not same_slot():
                return GatewayReply("UNKNOWN", code="ROUTE_UNAVAILABLE")
            host = handle.host
            if not same_slot():
                return GatewayReply("UNKNOWN", code="ROUTE_UNAVAILABLE")
            load = host.load_delegate_observation
            if not same_slot():
                return GatewayReply("UNKNOWN", code="ROUTE_UNAVAILABLE")
            observation = load(request_key)
            if not same_slot():
                return GatewayReply("UNKNOWN", code="ROUTE_UNAVAILABLE")
            reservation_id = None if observation is None else observation.reservation_id
            if not same_slot():
                return GatewayReply("UNKNOWN", code="ROUTE_UNAVAILABLE")
            code = _code(getattr(result, "code", None))
            if not same_slot():
                return GatewayReply("UNKNOWN", code="ROUTE_UNAVAILABLE")
            return GatewayReply(
                "QUEUED",
                state="DISPATCHING",
                reservation_id=reservation_id,
                code=code,
            )
        return self._bound_reply(same_slot, map_delegate_result(result))

    def _actor_status(self, handle: Any, request_key: str, same_slot: Callable[[], bool]) -> tuple[Any, Any]:
        from bt_api_risk.pretrade import GatewayReply

        client = handle.client
        if not same_slot():
            return None, GatewayReply("UNKNOWN", code="ROUTE_UNAVAILABLE")
        actor_status = client.actor_status
        if not same_slot():
            return None, GatewayReply("UNKNOWN", code="ROUTE_UNAVAILABLE")
        result = actor_status(request_key, timeout_seconds=self._timeout)
        if not same_slot():
            return result, GatewayReply("UNKNOWN", code="ROUTE_UNAVAILABLE")
        observation = getattr(result, "observation", None)
        if not same_slot():
            return result, GatewayReply("UNKNOWN", code="ROUTE_UNAVAILABLE")
        outcome = getattr(result, "outcome", None)
        if not same_slot():
            return result, GatewayReply("UNKNOWN", code="ROUTE_UNAVAILABLE")
        if outcome == "OBSERVED" and observation is not None:
            return result, self._bound_reply(same_slot, _observation_reply(observation.command))
        return result, self._bound_reply(same_slot, map_delegate_result(result))

    @_guard_delegate_cleanup
    def status(self, *, strategy_ref: str, run_id: str | None, request_key: str) -> Any:
        from bt_api_risk.pretrade import GatewayReply

        handle = self._handle(strategy_ref, run_id)
        if handle is None:
            return self._no_route()
        same_slot = self._binding(strategy_ref, run_id, handle)
        if not same_slot():
            return GatewayReply("UNKNOWN", code="ROUTE_UNAVAILABLE")
        return self._actor_status(handle, request_key, same_slot)[1]

    @_guard_delegate_cleanup
    def release(
        self, *, strategy_ref: str, run_id: str | None, request_key: str, operation_id: str
    ) -> Any:
        from bt_api_risk.pretrade import GatewayReply

        handle = self._handle(strategy_ref, run_id)
        if handle is None:
            return self._no_route()
        same_slot = self._binding(strategy_ref, run_id, handle)
        if not same_slot():
            return GatewayReply("UNKNOWN", code="ROUTE_UNAVAILABLE")
        result, reply = self._actor_status(handle, request_key, same_slot)
        if reply.outcome != "OBSERVED" or reply.state != "RESERVED":
            return reply
        if not same_slot():
            return GatewayReply("UNKNOWN", code="ROUTE_UNAVAILABLE")
        client = handle.client
        if not same_slot():
            return GatewayReply("UNKNOWN", code="ROUTE_UNAVAILABLE")
        observation = result.observation
        if not same_slot():
            return GatewayReply("UNKNOWN", code="ROUTE_UNAVAILABLE")
        release_actor = client.release_actor
        if not same_slot():
            return GatewayReply("UNKNOWN", code="ROUTE_UNAVAILABLE")
        released = release_actor(
            observation, operation_id=operation_id, timeout_seconds=self._timeout
        )
        if not same_slot():
            return GatewayReply("UNKNOWN", code="ROUTE_UNAVAILABLE")
        outcome = getattr(released, "outcome", None)
        if not same_slot():
            return GatewayReply("UNKNOWN", code="ROUTE_UNAVAILABLE")
        if outcome == "OBSERVED" and getattr(released, "state", None) in ("RELEASED", "EXPIRED"):
            # The client stored the verified terminal observation; its generations prove it.
            if not same_slot():
                return GatewayReply("UNKNOWN", code="ROUTE_UNAVAILABLE")
            host = handle.host
            if not same_slot():
                return GatewayReply("UNKNOWN", code="ROUTE_UNAVAILABLE")
            load = host.load_delegate_observation
            if not same_slot():
                return GatewayReply("UNKNOWN", code="ROUTE_UNAVAILABLE")
            observation = load(request_key)
            if not same_slot():
                return GatewayReply("UNKNOWN", code="ROUTE_UNAVAILABLE")
            return self._bound_reply(same_slot, _observation_reply(observation))
        if outcome in ("REQUEST_DENIED", "TRANSPORT_NOT_STARTED"):
            reply = GatewayReply(outcome, code=_code(getattr(released, "code", None)))
        else:
            reply = GatewayReply("UNKNOWN", code=_code(getattr(released, "code", None)))
        return self._bound_reply(same_slot, reply)

    @_guard_delegate_cleanup
    def cancel(
        self, *, strategy_ref: str, run_id: str | None, request_key: str, operation_id: str
    ) -> Any:
        from bt_api_risk.pretrade import GatewayReply

        handle = self._handle(strategy_ref, run_id)
        if handle is None:
            return self._no_route()
        same_slot = self._binding(strategy_ref, run_id, handle)
        if not same_slot():
            return GatewayReply("UNKNOWN", code="ROUTE_UNAVAILABLE")
        result, reply = self._actor_status(handle, request_key, same_slot)
        if reply.outcome != "OBSERVED" or reply.state not in _ACTOR_OPEN_STATES:
            return reply
        if not same_slot():
            return GatewayReply("UNKNOWN", code="ROUTE_UNAVAILABLE")
        client = handle.client
        if not same_slot():
            return GatewayReply("UNKNOWN", code="ROUTE_UNAVAILABLE")
        observation = result.observation
        if not same_slot():
            return GatewayReply("UNKNOWN", code="ROUTE_UNAVAILABLE")
        cancel_actor = client.cancel_actor
        if not same_slot():
            return GatewayReply("UNKNOWN", code="ROUTE_UNAVAILABLE")
        cancelled = cancel_actor(
            observation, operation_id=operation_id, timeout_seconds=self._timeout
        )
        if not same_slot():
            return GatewayReply("UNKNOWN", code="ROUTE_UNAVAILABLE")
        outcome = getattr(cancelled, "outcome", None)
        if not same_slot():
            return GatewayReply("UNKNOWN", code="ROUTE_UNAVAILABLE")
        if outcome in ("QUEUED", "OBSERVED", "REQUEST_DENIED", "TRANSPORT_NOT_STARTED"):
            reply = GatewayReply(outcome, code=_code(getattr(cancelled, "code", None)))
        else:
            reply = GatewayReply("UNKNOWN", code=_code(getattr(cancelled, "code", None)))
        return self._bound_reply(same_slot, reply)

    def ready(self) -> bool:
        self._owned()
        return not (self._sync_cleanup_uncertain() or self._cleanup_in_progress)

    def close(self) -> bool:
        self._owned()
        if self._sync_cleanup_uncertain() or self._cleanup_in_progress:
            return False
        if self._reserving or self._handling or self._closing:
            # Do not drop a slot whose connect callback has not returned its
            # handle yet; retain its cleanup obligation and prevent sending.
            for strategy in self._reserving | self._handling:
                slot = self._slots.get(strategy)
                if slot is not None:
                    slot.blocked = True
                    slot.invalid = True
                    slot.invalid_generation += 1
            return False
        self._closing = True
        try:
            for strategy, slot in list(self._slots.items()):
                if self._slots.get(strategy) is not slot:
                    return False
                if slot.blocked and slot.handle is None and not slot.connection_attempted:
                    del self._slots[strategy]
                    continue
                if self._close_slot(slot):
                    if (not self._sync_cleanup_uncertain()
                            and self._slots.get(strategy) is slot
                            and slot.handle is None and not slot.connection_attempted):
                        del self._slots[strategy]
                    else:
                        return False
                else:
                    return False
        finally:
            self._closing = False
        return not self._slots


# ============================================================================ control port


_CANCEL_TARGET_STATES = frozenset({"QUEUED", "SENT", "NOT_SENT", "UNKNOWN", "TERMINAL"})
_CANCEL_ATTEMPT_STATES = frozenset({"REQUESTED", "DISPATCHING", "SENT", "NOT_SENT", "UNKNOWN"})
_CANCEL_INSTRUMENT_SCOPE = re.compile(r"[A-Za-z0-9]{1,16}:[A-Za-z0-9][A-Za-z0-9._-]{0,63}\Z")
_CONTROL_RELEASER = re.compile(r"(?:operator:[0-9a-f]{64}|system:[a-z][a-z0-9-]{0,62}[a-z0-9])\Z")
_CONTROL_CAUSE_FIELDS = frozenset({"account", "cause_id", "scope_kind", "scope_value", "mode", "origin",
                                   "generation", "version", "active", "resume_id"})


def _cancel_status_text(value: object) -> bool:
    # Gateway DirectControl's persisted identifier contract allows 512 characters.
    return type(value) is str and bool(value) and value == value.strip() and len(value) <= 512


def _valid_control_cause(cause: object, cause_id: str, account: str) -> bool:
    if (type(cause) is not dict or set(cause) not in (_CONTROL_CAUSE_FIELDS, _CONTROL_CAUSE_FIELDS | {"releaser"})
            or type(account) is not str or cause["account"] != account
            or not _cancel_status_text(cause["account"])
            or type(cause_id) is not str or cause["cause_id"] != cause_id
            or not all(_cancel_status_text(cause[key]) for key in ("cause_id", "origin", "generation"))
            or type(cause["mode"]) is not str or cause["mode"] not in ("STOP_NEW", "FREEZE", "DRAIN")):
        return False
    kind, value = cause["scope_kind"], cause["scope_value"]
    if (type(kind) is not str or type(value) is not str
            or (kind == "account" and value != "")
            or (kind == "strategy" and not _cancel_status_text(value))
            or (kind == "instrument" and _CANCEL_INSTRUMENT_SCOPE.fullmatch(value) is None)
            or kind not in ("account", "strategy", "instrument")):
        return False
    active, version, resume = cause["active"], cause["version"], cause["resume_id"]
    if (type(active) not in (bool, int) or active not in (0, 1)
            or type(version) is not int or version != (1 if active else 2)
            or (active and resume is not None) or (not active and not _cancel_status_text(resume))):
        return False
    if "releaser" in cause:
        releaser = cause["releaser"]
        if active or type(releaser) is not str or _CONTROL_RELEASER.fullmatch(releaser) is None:
            return False
    return True


def _valid_cancel_status(raw: object, operation_id: str, after: str, limit: int) -> bool:
    if type(raw) is not dict or raw.get("operation_id") != operation_id:
        return False
    if raw.get("status") == "NOT_FOUND":
        return set(raw) == {"status", "operation_id"}
    if (raw.get("status") != "OBSERVED" or set(raw) != {
            "status", "operation_id", "scope_kind", "scope_value", "origin", "targets", "counts",
            "after", "limit", "remaining_at_cursor"}):
        return False
    if (type(raw["after"]) is not str or raw["after"] != after
            or type(raw["limit"]) is not int or raw["limit"] != limit):
        return False
    scope_kind, scope_value = raw["scope_kind"], raw["scope_value"]
    if (scope_kind == "account" and scope_value != "") or (
            scope_kind == "instrument" and (type(scope_value) is not str
                or _CANCEL_INSTRUMENT_SCOPE.fullmatch(scope_value) is None)):
        return False
    if scope_kind not in ("account", "instrument") or not _cancel_status_text(raw["origin"]):
        return False
    counts, targets = raw["counts"], raw["targets"]
    if (type(counts) is not dict or set(counts) != _CANCEL_TARGET_STATES
            or any(type(count) is not int or not 0 <= count <= 2**63 - 1 for count in counts.values())
            or type(targets) is not list or len(targets) > limit):
        return False
    remaining = raw["remaining_at_cursor"]
    total = sum(counts.values())
    if (type(remaining) is not int or not 0 <= remaining <= total
            or (not after and remaining != total)
            or len(targets) != min(remaining, limit)):
        return False
    seen = dict.fromkeys(_CANCEL_TARGET_STATES, 0)
    previous = after
    for target in targets:
        if type(target) is not dict or set(target) != {
                "reservation_id", "strategy", "request_key", "state", "cancel"}:
            return False
        if (not _ref(target["reservation_id"])
                or not all(_cancel_status_text(target[key]) for key in ("strategy", "request_key"))):
            return False
        reservation_id, state, cancel = target["reservation_id"], target["state"], target["cancel"]
        if (reservation_id <= previous or type(state) is not str or state not in _CANCEL_TARGET_STATES
                or (cancel is not None and (type(cancel) is not str or cancel not in _CANCEL_ATTEMPT_STATES))):
            return False
        if ((state == "SENT" and cancel != "SENT")
                or (state == "NOT_SENT" and cancel not in (None, "REQUESTED", "NOT_SENT"))
                or (state == "UNKNOWN" and cancel not in ("DISPATCHING", "UNKNOWN"))):
            return False
        previous = reservation_id
        seen[state] += 1
        if seen[state] > counts[state]:
            return False
    return True


def _valid_cancel_reply(raw: object, operation_id: str, scope_kind: str, scope_value: str) -> bool:
    if (type(raw) is not dict or set(raw) != {
            "status", "operation_id", "scope_kind", "scope_value", "issued", "counts"}
            or raw["status"] != "OBSERVED"
            or type(raw["operation_id"]) is not str or raw["operation_id"] != operation_id
            or type(raw["scope_kind"]) is not str or raw["scope_kind"] != scope_kind
            or type(raw["scope_value"]) is not str or raw["scope_value"] != scope_value):
        return False
    issued, counts = raw["issued"], raw["counts"]
    return (type(issued) is int and 0 <= issued <= _CANCEL_SCOPE_LIMIT
            and type(counts) is dict and set(counts) == _CANCEL_TARGET_STATES
            and all(type(count) is int and 0 <= count <= 2**63 - 1 for count in counts.values())
            and issued <= sum(counts.values()))


@dataclass(frozen=True)
class _ControlBindingProof:
    binding: Any
    observation: BoundCallObservation
    token: object


class GatewayControlAdapter:
    """``GatewayControlPort`` over the Risk control principal's threaded runtime.

    Only account scope is supported. A contract alias cannot establish a
    unique complete contract across the Gateway account, and this adapter
    does not hold a strategy-scoped registration. Those scopes are refused
    locally with ``RISK_CONTROL_SCOPE_UNSUPPORTED`` and never sent.
    """

    def __init__(
        self,
        runtime: Any,
        *,
        mount: str,
        service_id: str,
        instance_id: str,
        scope_ref: str,
        account_ref: str,
        timeout_seconds: float,
        route: BoundCallRoute | None = None,
        server_key_fingerprint: str | None = None,
        control_intent_enabled: bool = False,
    ) -> None:
        if type(control_intent_enabled) is not bool:
            raise ValueError("RISK_CONTROL_INTENT_CONFIG_INVALID")
        self._runtime = runtime
        self._mount, self._service_id = mount, service_id
        self._instance_id, self._scope_ref, self._account = instance_id, scope_ref, account_ref
        self._timeout = float(timeout_seconds)
        self._route = None if route is None else BoundCallRoute(**vars(route))
        self._server_key_fingerprint = server_key_fingerprint
        self._control_intent_enabled = control_intent_enabled
        self._cause_bindings: dict[str, _ControlBindingProof] = {}
        self._operation_bindings: dict[str, _ControlBindingProof] = {}
        self.last_cancel_status: dict[str, Mapping] = {}

    def _capture_binding(self) -> tuple[_ControlBindingProof | None, Any]:
        from bt_api_risk.pretrade import ControlIntentBinding, ControlReply

        route = self._route
        fingerprint = self._server_key_fingerprint
        if (type(route) is not BoundCallRoute
                or route.scope.account_ref != self._account
                or route.scope_ref != self._scope_ref
                or route.service_id != self._service_id
                or route.instance_id != self._instance_id
                or type(fingerprint) is not str
                or re.fullmatch(r"[0-9a-f]{64}", fingerprint) is None):
            return None, ControlReply("TRANSPORT_NOT_STARTED", code="ROUTE_UNAVAILABLE")
        try:
            result = self._runtime.call_bound(
                self._mount,
                "direct.control.identity",
                {},
                route=route,
                timeout_seconds=self._timeout,
                expected_server_key_fingerprint=fingerprint,
            )
        except Exception as error:
            outcome, code = runtime_failure(error, "direct.control.identity")
            return None, ControlReply(outcome, code=_code(code))
        try:
            if type(result) is not BoundCallResult or result._binding_token is None:
                raise ValueError
            before, after = result.before, result.after
            if (type(before) is not BoundCallObservation or type(after) is not BoundCallObservation
                    or before != after or before.identity != route.identity or before.scope != route.scope
                    or before.peer != route.peer or before.mount != self._mount
                    or before.service_id != self._service_id or before.instance_id != self._instance_id
                    or before.scope_ref != self._scope_ref or before.fingerprint != fingerprint
                    or before.operation != "direct.control.identity" or before.active is not True
                    or before.required is not True):
                raise ValueError
            raw = _detach_wire(result.result)
            if (type(raw) is not dict or set(raw) != {
                    "status", "schema_version", "account_scope", "journal_id", "journal_generation"}
                    or raw["status"] != "OBSERVED"
                    or type(raw["schema_version"]) is not int
                    or raw["schema_version"] != 1
                    or raw["account_scope"] != self._account or not _ref(raw["journal_id"])
                    or not _ref(raw["journal_generation"])):
                raise ValueError
            binding = ControlIntentBinding(
                schema_version=1,
                account_scope=self._account,
                journal_id=raw["journal_id"],
                owner_generation=raw["journal_generation"],
                mount=before.mount,
                service_id=before.service_id,
                instance_id=before.instance_id,
                scope_ref=before.scope_ref,
                registration_id=before.registration_id,
                service_epoch=before.service_epoch,
                peer_principal_ref=before.peer.principal_ref,
                peer_actor_ref=before.peer.actor_ref,
                server_key_fingerprint=before.fingerprint,
                operations=before.operations,
            )
            return _ControlBindingProof(binding, before, result._binding_token), None
        except Exception:
            return None, ControlReply("UNKNOWN", code="INVALID_OR_UNAVAILABLE_REPLY")

    def capture_intent_binding(self) -> Any:
        """Observe the current route/generation; this grants no retry permission."""
        proof, _error = self._capture_binding()
        return None if proof is None else proof.binding

    def _binding_for(self, key: object, *, operation: bool) -> tuple[_ControlBindingProof | None, Any]:
        from bt_api_risk.pretrade import ControlReply

        if not _ref(key):
            return None, ControlReply("REQUEST_DENIED", code="INVALID_REQUEST")
        bindings = self._operation_bindings if operation else self._cause_bindings
        proof = bindings.get(key)
        if proof is not None:
            return proof, None
        if len(bindings) >= _CONTROL_BINDING_CAPACITY:
            return None, ControlReply("TRANSPORT_NOT_STARTED", code="ROUTE_UNAVAILABLE")
        proof, error = self._capture_binding()
        if proof is None:
            return None, error
        bindings[key] = proof
        return proof, None

    def _call(
        self,
        operation: str,
        arguments: dict,
        proof: _ControlBindingProof,
        *,
        rejection_codes: frozenset[str] | None = None,
        expected_unknown: Mapping[str, object] | None = None,
    ) -> tuple[Mapping | None, Any]:
        from bt_api_risk.pretrade import ControlReply

        binding = proof.binding
        wire_arguments = dict(
            arguments,
            expected_journal_id=binding.journal_id,
            expected_generation=binding.owner_generation,
        )
        try:
            result = self._runtime.call_bound(
                    self._mount,
                    operation,
                    wire_arguments,
                    route=self._route,
                    timeout_seconds=self._timeout,
                    expected_binding=proof.observation,
                    _expected_token=proof.token,
                    expected_server_key_fingerprint=self._server_key_fingerprint,
            )
            expected = replace(proof.observation, operation=operation)
            if (type(result) is not BoundCallResult
                    or result.before != expected or result.after != expected):
                raise ValueError
            raw = _detach_wire(result.result)
        except Exception as error:
            outcome, code = runtime_failure(error, operation)
            return None, ControlReply(outcome, code=_code(code))
        if type(raw) is not dict:
            return None, ControlReply("UNKNOWN", code="INVALID_OR_UNAVAILABLE_REPLY")
        if raw.get("status") == "REQUEST_REJECTED":
            if (
                set(raw) != {"status", "code"}
                or not _ref(raw["code"])
                or (rejection_codes is not None and raw["code"] not in rejection_codes)
            ):
                return None, ControlReply("UNKNOWN", code="INVALID_OR_UNAVAILABLE_REPLY")
            return None, ControlReply("REQUEST_DENIED", code=raw["code"])
        if raw.get("status") == "UNKNOWN":
            if expected_unknown is not None and raw != dict(expected_unknown):
                return None, ControlReply(
                    "UNKNOWN", code="INVALID_OR_UNAVAILABLE_REPLY"
                )
            return None, ControlReply("UNKNOWN")
        return raw, None

    def _scope_arguments(self, scope_kind: str, scope_value: str) -> dict | None:
        if scope_kind == "account" and scope_value == "":
            return {}
        return None

    def apply_intent(
        self,
        *,
        binding: Any,
        request_digest: str,
        cause_id: str,
        mode: str,
        scope_kind: str,
        scope_value: str,
        latch_id: str | None,
    ) -> Any:
        """Send one persisted control intent through a fresh same-owner proof.

        The explicit adapter gate is default-off. A fresh identity observation
        must equal the persisted binding byte-for-byte before the request is
        handed to the transport. Neither this method nor the binding grants an
        automatic or cross-generation retry.
        """
        from bt_api_risk.pretrade import ControlIntentBinding, ControlReply

        if not self._control_intent_enabled:
            return ControlReply(
                "TRANSPORT_NOT_STARTED", code="CONTROL_INTENT_UNAVAILABLE"
            )
        if (
            type(binding) is not ControlIntentBinding
            or type(request_digest) is not str
            or re.fullmatch(r"[0-9a-f]{64}", request_digest) is None
            or not _ref(cause_id)
            or type(mode) is not str
            or scope_kind != "account"
            or scope_value != ""
            or (latch_id is not None and not _ref(latch_id))
        ):
            return ControlReply("REQUEST_DENIED", code="INVALID_REQUEST")
        route = self._route
        if (
            type(route) is not BoundCallRoute
            or binding.account_scope != self._account
            or binding.mount != self._mount
            or binding.service_id != self._service_id
            or binding.instance_id != self._instance_id
            or binding.scope_ref != self._scope_ref
            or binding.peer_principal_ref != route.peer.principal_ref
            or binding.peer_actor_ref != route.peer.actor_ref
            or binding.server_key_fingerprint != self._server_key_fingerprint
        ):
            return ControlReply(
                "REQUEST_DENIED", code="CONTROL_INTENT_BINDING_MISMATCH"
            )
        if CONTROL_INTENT_APPLY_OPERATION not in binding.operations:
            return ControlReply(
                "TRANSPORT_NOT_STARTED", code="CONTROL_INTENT_UNAVAILABLE"
            )
        try:
            from bt_api_gateway.control_intent_wire import (
                CONTROL_INTENT_REJECTION_CODES,
                CONTROL_INTENT_WIRE_SCHEMA_VERSION,
            )
            from bt_api_risk.pretrade.account_bound_intents import (
                request_digest_for_control,
            )
            from bt_api_risk.risk_order_journal_contracts import (
                RiskOrderJournalError,
            )
        except Exception:
            return ControlReply("UNKNOWN", code="INVALID_OR_UNAVAILABLE_REPLY")

        try:
            computed_digest = request_digest_for_control(
                cause_id=cause_id,
                mode=mode,
                scope_kind=scope_kind,
                scope_value=scope_value,
                latch_id=latch_id,
            )
        except RiskOrderJournalError:
            return ControlReply("REQUEST_DENIED", code="INVALID_REQUEST")
        except Exception:
            return ControlReply("UNKNOWN", code="INVALID_OR_UNAVAILABLE_REPLY")
        if request_digest != computed_digest:
            return ControlReply(
                "REQUEST_DENIED",
                code="CONTROL_INTENT_REQUEST_DIGEST_MISMATCH",
            )
        proof, error = self._capture_binding()
        if proof is None:
            return error
        if proof.binding != binding:
            return ControlReply(
                "TRANSPORT_NOT_STARTED", code="CONTROL_INTENT_BINDING_MISMATCH"
            )
        if CONTROL_INTENT_APPLY_OPERATION not in proof.observation.operations:
            return ControlReply(
                "TRANSPORT_NOT_STARTED", code="CONTROL_INTENT_UNAVAILABLE"
            )
        raw, error = self._call(
            CONTROL_INTENT_APPLY_OPERATION,
            {
                "schema_version": CONTROL_INTENT_WIRE_SCHEMA_VERSION,
                "cause_id": cause_id,
                "mode": mode,
                "scope_kind": scope_kind,
                "scope_value": scope_value,
                "latch_id": latch_id,
                "request_digest": request_digest,
                "binding_digest": binding.digest,
            },
            proof,
            rejection_codes=CONTROL_INTENT_REJECTION_CODES,
            expected_unknown={"status": "UNKNOWN", "cause_id": cause_id},
        )
        if error is not None:
            return error
        try:
            if (
                set(raw) != {"status", "cause_id", "version", "active"}
                or raw.get("status") != "OBSERVED"
                or type(raw.get("cause_id")) is not str
                or raw["cause_id"] != cause_id
                or type(raw.get("active")) is not bool
                or type(raw.get("version")) is not int
                or raw["version"] != (1 if raw["active"] else 2)
            ):
                raise ValueError
            return ControlReply(
                "OBSERVED",
                cause_id=cause_id,
                active=raw["active"],
                version=raw["version"],
            )
        except Exception:
            return ControlReply("UNKNOWN", code="INVALID_OR_UNAVAILABLE_REPLY")

    def apply(self, *, cause_id: str, mode: str, scope_kind: str, scope_value: str) -> Any:
        from bt_api_risk.pretrade import ControlReply

        extra = self._scope_arguments(scope_kind, scope_value)
        if extra is None:
            return ControlReply("REQUEST_DENIED", code="RISK_CONTROL_SCOPE_UNSUPPORTED")
        proof, error = self._binding_for(cause_id, operation=False)
        if error is not None:
            return error
        raw, error = self._call(
            "direct.control.apply", {"cause_id": cause_id, "mode": mode, **extra}, proof
        )
        if error is not None:
            return error
        try:
            if (set(raw) != {"status", "cause_id", "version", "active"}
                    or raw.get("status") != "OBSERVED" or type(raw.get("cause_id")) is not str
                    or type(cause_id) is not str or raw["cause_id"] != cause_id):
                raise ValueError
            active = raw.get("active")
            if type(active) is not bool:
                raise ValueError
            version = raw.get("version")
            if type(version) is not int or version != (1 if active else 2):
                raise ValueError
            return ControlReply(
                "OBSERVED",
                cause_id=cause_id,
                active=active,
                version=version,
            )
        except Exception:
            return ControlReply("UNKNOWN", code="INVALID_OR_UNAVAILABLE_REPLY")

    def status(self, *, cause_id: str) -> Any:
        from bt_api_risk.pretrade import ControlReply

        proof = self._cause_bindings.get(cause_id) if _ref(cause_id) else None
        if proof is None:
            return ControlReply("UNKNOWN", code="CONTROL_INTENT_BINDING_REQUIRED")
        raw, error = self._call("direct.control.status", {"cause_id": cause_id}, proof)
        if error is not None:
            return error
        try:
            if (raw.get("status") == "NOT_FOUND" and set(raw) == {"status", "cause_id"}
                    and type(cause_id) is str and type(raw["cause_id"]) is str
                    and raw["cause_id"] == cause_id):
                # Not a refusal and not proof of anything: the cause is simply absent.
                return ControlReply("OBSERVED", cause_id=cause_id, active=False)
            cause = raw.get("cause")
            if (raw.get("status") != "OBSERVED" or set(raw) != {"status", "cause"}
                    or not _valid_control_cause(cause, cause_id, self._account)):
                raise ValueError
            return ControlReply(
                "OBSERVED",
                cause_id=cause_id,
                active=bool(cause["active"]),
                version=cause["version"],
                scope_kind=cause["scope_kind"],
                scope_value=cause["scope_value"],
                mode=cause["mode"],
                origin=cause["origin"],
            )
        except Exception:
            return ControlReply("UNKNOWN", code="INVALID_OR_UNAVAILABLE_REPLY")

    def cancel_scope(self, *, operation_id: str, scope_kind: str, scope_value: str) -> Any:
        from bt_api_risk.pretrade import ControlReply

        extra = self._scope_arguments(scope_kind, scope_value)
        if extra is None:
            return ControlReply("REQUEST_DENIED", code="RISK_CONTROL_SCOPE_UNSUPPORTED")
        self.last_cancel_status.pop(operation_id, None)
        proof, error = self._binding_for(operation_id, operation=True)
        if error is not None:
            return error
        raw, error = self._call(
            "direct.control.cancel_scope",
            {"operation_id": operation_id, "limit": _CANCEL_SCOPE_LIMIT, **extra},
            proof,
        )
        if error is not None:
            return error
        if not _valid_cancel_reply(raw, operation_id, scope_kind, scope_value):
            return ControlReply("UNKNOWN", code="INVALID_OR_UNAVAILABLE_REPLY")
        self.last_cancel_status[operation_id] = _detach_wire(raw)
        # SENT and TERMINAL are observations, not confirmation of venue cancellation.
        if any(raw["counts"][state] for state in ("QUEUED", "UNKNOWN", "NOT_SENT")):
            return ControlReply("UNKNOWN", code="CANCEL_SCOPE_INCOMPLETE")
        return ControlReply("OBSERVED")

    def cancel_scope_status(self, operation_id: str, *, after: str = "", limit: int = 256) -> Mapping | None:
        """Read one current page; separate pages are not a consistent snapshot."""
        if not _ref(operation_id):
            return None
        self.last_cancel_status.pop(operation_id, None)
        if (type(after) is not str or (after and not _ref(after))
                or type(limit) is not int or not 1 <= limit <= _CANCEL_SCOPE_LIMIT):
            return None
        proof = self._operation_bindings.get(operation_id)
        if proof is None:
            return None
        raw, error = self._call(
            "direct.control.cancel_scope_status",
            {"operation_id": operation_id, "after": after, "limit": limit},
            proof,
        )
        try:
            if error is not None or not _valid_cancel_status(raw, operation_id, after, limit):
                return None
            self.last_cancel_status[operation_id] = _detach_wire(raw)
            return raw
        except Exception:
            return None


# ============================================================================ facts


def _market_ref_snapshot(value: object) -> dict | None:
    """Copy the wire reference's fixed shape; field equality is not provenance."""
    try:
        if (type(value) is not dict or any(type(key) is not str for key in value)
                or set(value) != {"key", "epoch", "sequence", "message_id"}):
            return None
        subscription = value["key"]
        if (type(subscription) is not dict or any(type(key) is not str for key in subscription)
                or type(subscription.get("instrument")) is not dict
                or any(type(key) is not str for key in subscription["instrument"])):
            return None
        epoch, sequence, message_id = value["epoch"], value["sequence"], value["message_id"]
        for text in (epoch, message_id):
            if type(text) is not str or not text or text != text.strip():
                return None
            encoded = text.encode("utf-8", errors="strict")
            if len(encoded) > MAX_REFERENCE_BYTES or any(
                    ord(ch) < 32 or 127 <= ord(ch) <= 159 for ch in text):
                return None
        if type(sequence) is not int or sequence <= 0:
            return None
        key = MarketSubscriptionKey.from_dict(subscription)
        return {"key": key.to_dict(), "epoch": epoch, "sequence": sequence, "message_id": message_id}
    except Exception:
        return None


class RiskFactsCache:
    """``FactsPort``: cached Gateway-bound facts; every read is in memory.

    Trading day: the latest of the Risk market subscription's tick trading date
    and the observation stream's event trading day (both Gateway-bound provider
    facts); unknown until one is seen. Account snapshots older than
    ``account_max_age_ns`` are reported incomplete.
    """

    def __init__(
        self,
        instruments: Mapping[str, Any],
        *,
        now_ns: Callable[[], int] = time.time_ns,
        account_max_age_ns: int | None = None,
        financial_max_age_ns: int | None = None,
    ) -> None:
        self._instruments = dict(instruments)  # instrument_id -> RiskInstrumentBinding
        self._now_ns = now_ns
        self._account_max_age = account_max_age_ns
        self._financial_max_age = financial_max_age_ns
        self._financials: dict[str, Any] = {}
        self._financial_received_ns: int | None = None
        self._prices: dict[str, Any] = {}
        self._price_receipts: dict[str, tuple[object, int, int]] = {}
        self._price_receipts_lock = threading.Lock()
        self._market_refs: dict[str, dict] = {}
        self._account: Any = None
        self._account_received_ns: int | None = None
        self._market_days: dict[str, str] = {}
        self._event_day: str | None = None
        self._market_feed: Any = None

    def note_tick(self, instrument_id: str, price: Any, market_ref: dict | None, trading_day: str | None, *, _local_receive: tuple[int, int] | None = None) -> None:
        if type(instrument_id) is not str:
            return
        # A failed reference snapshot must not retain the preceding tick's ref.
        self._market_refs.pop(instrument_id, None)
        # Each assignment gets a new identity even if the exact same fact
        # object is re-used. The primary cache never waits for telemetry.
        entry = (price, object())
        self._prices[instrument_id] = entry
        if self._price_receipts_lock.acquire(False):
            try:
                self._price_receipts.pop(instrument_id, None)
                if _local_receive is not None:
                    self._price_receipts[instrument_id] = (entry, *_local_receive)
            finally:
                self._price_receipts_lock.release()
        snapshot = _market_ref_snapshot(market_ref)
        if snapshot is not None:
            self._market_refs[instrument_id] = snapshot
        day = _day(trading_day)
        if day is None:
            self._market_days.pop(instrument_id, None)
        else:
            self._market_days[instrument_id] = day

    def note_event_day(self, trading_day: str | None) -> None:
        day = _day(trading_day)
        if day is not None and (self._event_day is None or day > self._event_day):
            self._event_day = day

    def forget_event_day(self) -> None:
        """Revoke the observation-derived day without changing market facts."""
        self._event_day = None

    def set_account(self, snapshot: Any) -> None:
        received_ns = self._now_ns()
        self._account = snapshot
        self._account_received_ns = received_ns

    def forget_account(self) -> None:
        """Remove an earlier snapshot after a failed account refresh."""
        self._account = None
        self._account_received_ns = None

    def set_strategy_financials(self, facts: Mapping[str, Any]) -> None:
        """Replace every strategy financial fact at once; an absent strategy is unknown."""
        self._financials = dict(facts)
        self._financial_received_ns = self._now_ns()

    def now_ns(self) -> int:
        return self._now_ns()

    def forget_market(self) -> None:
        self._prices.clear()
        # Replacing the telemetry map is nonblocking; an old row cannot match
        # a later primary entry's distinct identity, even during concurrent writes.
        self._price_receipts = {}
        self._market_refs.clear()
        self._market_days.clear()

    # FactsPort -----------------------------------------------------------------

    def _local_receive_for_entry(self, price: Any, entry: object) -> tuple[int, int] | None:
        """Match only the exact entry read alongside the fact."""
        if not self._price_receipts_lock.acquire(False):
            return None
        try:
            key = getattr(price, "instrument", None)
            if type(key) is not MarketInstrumentKey:
                return None
            row = self._price_receipts.get(key.instrument_id)
            current = self._prices.get(key.instrument_id)
            if (row is None or row[0] is not entry or current is not entry
                    or entry is None or entry[0] is not price
                    or row[2] != os.getpid()):
                return None
            return row[1], row[2]
        finally:
            self._price_receipts_lock.release()

    def reference_price_with_local_receive(self, instrument: Any) -> tuple[Any, tuple[int, int] | None]:
        """Read one entry and its matching local observation, without waiting for telemetry."""
        from bt_api_risk.pretrade import PriceFact

        # An overridden facts reader retains its business semantics; without
        # the native entry snapshot it cannot contribute this telemetry.
        if getattr(self.reference_price, "__func__", None) is not RiskFactsCache.reference_price:
            return self.reference_price(instrument), None
        if type(instrument) is not MarketInstrumentKey:
            return None, None
        entry = self._prices.get(instrument.instrument_id)
        price = None if entry is None else entry[0]
        if type(price) is not PriceFact or price.instrument != instrument:
            return None, None
        try:
            local = self._local_receive_for_entry(price, entry)
        except Exception:
            local = None
        return price, local

    def reference_price(self, instrument: Any) -> Any:
        from bt_api_risk.pretrade import PriceFact

        if type(instrument) is not MarketInstrumentKey:
            return None
        entry = self._prices.get(instrument.instrument_id)
        price = None if entry is None else entry[0]
        if type(price) is not PriceFact or price.instrument != instrument:
            return None
        return price

    def has_attested_market_ref(self, instrument: Any) -> bool:
        """Read-only check whether the latest tick has a Gateway-attested market_ref (no RPC)."""
        if type(instrument) is not MarketInstrumentKey:
            return False
        value = self._market_refs.get(instrument.instrument_id)
        if value is None:
            return False
        snapshot = _market_ref_snapshot(value)
        if snapshot is None:
            return False
        try:
            key = MarketSubscriptionKey.from_dict(snapshot["key"])
        except Exception:
            return False
        return key.instrument == instrument

    def market_ref(self, instrument: Any) -> dict | None:
        if type(instrument) is not MarketInstrumentKey:
            return None
        feed = self._market_feed
        if feed is not None:
            refresh = getattr(feed, "ensure_fresh_market_ref", None)
            if callable(refresh):
                try:
                    refresh(instrument.instrument_id)
                except Exception:
                    pass
        if self.reference_price(instrument) is None:
            logging.getLogger(__name__).warning(
                "risk market_ref unavailable: instrument=%s reason=no_reference_price has_ref=%s",
                instrument.instrument_id,
                instrument.instrument_id in self._market_refs,
            )
            return None
        value = self._market_refs.get(instrument.instrument_id)
        if value is None:
            logging.getLogger(__name__).warning(
                "risk market_ref unavailable: instrument=%s reason=no_attested_ref",
                instrument.instrument_id,
            )
            return None
        snapshot = _market_ref_snapshot(value)
        if snapshot is None:
            logging.getLogger(__name__).warning(
                "risk market_ref unavailable: instrument=%s reason=invalid_ref_snapshot",
                instrument.instrument_id,
            )
            return None
        key = MarketSubscriptionKey.from_dict(snapshot["key"])
        return snapshot if key.instrument == instrument else None

    def instrument_facts(self, instrument: Any) -> Any:
        from bt_api_risk.pretrade import InstrumentFacts

        binding = self._instruments.get(getattr(instrument, "instrument_id", None))
        if binding is None or binding.key != instrument:
            return None
        return InstrumentFacts(
            instrument=binding.key, multiplier=binding.multiplier, tick_size=binding.tick_size
        )

    def account_snapshot(self) -> Any:
        snapshot = self._account
        if snapshot is None:
            return None
        now = self._now_ns()
        if (snapshot.as_of_ns is None or snapshot.valid_until_ns is None
            or not snapshot.as_of_ns <= now <= snapshot.valid_until_ns
            or (self._account_max_age is not None and (
                self._account_received_ns is None
                or now - self._account_received_ns > self._account_max_age
            ))):
            return replace(snapshot, complete=False)
        return snapshot

    def strategy_financial(self, strategy_ref: str) -> Any:
        fact = self._financials.get(strategy_ref)
        if fact is None:
            return None  # not read, not granted or UNAVAILABLE: unknown
        now = self._now_ns()
        if (fact.as_of_ns is None or fact.valid_until_ns is None
            or not fact.as_of_ns <= now <= fact.valid_until_ns
            or self._financial_max_age is None or (
            self._financial_received_ns is None
            or now - self._financial_received_ns > self._financial_max_age
        )):
            return replace(fact, complete=False)
        return fact

    def margin_rate(self, instrument: Any) -> Any:
        return None  # no Gateway-bound margin-rate source for the Risk principal yet

    def trading_day(self) -> str | None:
        days = list(self._market_days.values())
        if self._event_day is not None:
            days.append(self._event_day)
        return max(days) if days else None


def price_fact_from_market_event(received: Any, key: MarketSubscriptionKey) -> tuple[Any, dict, str | None]:
    """Map one received market event of the Risk subscription to ``(PriceFact, market_ref, day)``.

    The market reference is taken from the event header of this principal's
    own subscription (G64-10); a tick of another instrument or key is refused.
    The day is the tick's trading date as ``YYYYMMDD`` (None when not a date).
    """
    from bt_api_base.market_tick_protocol import MarketTickPayload
    from bt_api_risk.pretrade import PriceFact

    event = received.event
    payload = event.payload
    if not isinstance(payload, Mapping) or payload.get("key") != key.to_dict():
        raise ValueError("RISK_MARKET_EVENT_FOREIGN")
    tick = MarketTickPayload.from_dict(payload["data"])
    if tick.instrument != key.instrument:
        raise ValueError("RISK_MARKET_EVENT_FOREIGN")
    header = event.header
    receipt = received.receipt
    quality = (
        tick.quality_contract_ref is not None
        and tick.continuity_status in ("continuous", "CONTINUOUS")
    )
    fact = PriceFact(
        instrument=key.instrument,
        last_price=_decimal(tick.last_price, positive=True),
        source_time_ns=_utc_ns(tick.provider_event_time_utc),
        received_time_ns=None if receipt is None else receipt.received_wall_ns,
        quality_ok=quality,
    )
    market_ref = {
        "key": key.to_dict(),
        "epoch": header.stream_epoch,
        "sequence": header.sequence,
        "message_id": header.message_id,
    }
    return fact, market_ref, _tick_day(tick.trading_date)


RISK_MARKET_CLOCK_OPERATION = "market.clock_observation"


def risk_market_settings(subscriptions: Mapping[str, MarketSubscriptionKey]) -> Any:
    """Market client settings of the Risk principal: its keys, plus the tick attestation.

    G64-10: the Gateway market gate admits a delegated dispatch only for a
    tick that this principal's own market registration had the Gateway attest
    (``market.clock_observation``); the Registry also resolves that
    registration only when it holds the operation, and the client attests a
    tick only on a subscription bound to the Gateway's source descriptor.
    """
    from bt_api_py.gateway_strategy_market import (
        GatewayMarketClientSettings,
        GatewayMarketSubscriptionSettings,
    )

    settings = GatewayMarketClientSettings(
        {
            name: GatewayMarketSubscriptionSettings(
                key,
                require_source_descriptor=True,
                head_check_interval_seconds=15.0,
            )
            for name, key in subscriptions.items()
        }
    )
    return replace(
        settings,
        requested_operations=settings.requested_operations | {RISK_MARKET_CLOCK_OPERATION},
    )


class RiskMarketFeed:
    """Drain the Risk principal's own market subscriptions into the facts cache.

    Per subscription the latest received tick sets the price, the trading day
    and, only once the Gateway has attested that exact live tick on this
    principal's registration (``clock_observation`` with its receipt), the
    ``market_ref`` that Risk sends with a dispatch (G64-10). A tick that could
    not be attested (no live receipt, refusal, timeout) leaves no reference:
    the dispatch then carries none and the Gateway gate refuses it.
    """

    def __init__(self, subscriptions: Mapping[str, tuple[Any, MarketSubscriptionKey, str]], facts: RiskFactsCache, *, timeout_seconds: float, max_events: int = 1024) -> None:
        if type(max_events) is not int or max_events <= 0:
            raise ValueError("RISK_MARKET_EVENT_LIMIT_INVALID")
        self._subscriptions = dict(subscriptions)  # name -> (client, key, instrument_id)
        instrument_ids = set()
        for _client, key, instrument_id in self._subscriptions.values():
            # Cache entries are keyed by instrument id. Refuse ambiguous
            # ownership before starting clients rather than revoke another
            # subscription's facts when one subscription fails.
            if (type(key) is not MarketSubscriptionKey or type(instrument_id) is not str
                    or key.instrument.instrument_id != instrument_id or instrument_id in instrument_ids):
                raise ValueError("RISK_MARKET_SUBSCRIPTION_INVALID")
            instrument_ids.add(instrument_id)
        self._facts = facts
        self._timeout = float(timeout_seconds)
        self._max = max_events
        self._poll_lock = threading.Lock()
        self._next_subscription = 0
        self._by_instrument = {
            instrument_id: (client, key)
            for client, key, instrument_id in self._subscriptions.values()
        }
        self._last_received: dict[str, tuple[Any, Any, int]] = {}
        self.failures = 0
        self.attestation_failures = 0
        try:
            self._facts._market_feed = self
        except Exception:
            pass

    def start(self) -> None:
        for client, _key, _instrument in self._subscriptions.values():
            client.start(timeout_seconds=self._timeout)

    def _attested(self, client: Any, received: Any) -> bool:
        receipt = getattr(received, "receipt", None)
        if receipt is None or getattr(receipt, "source", None) != "pub":
            logging.getLogger(__name__).warning(
                "risk market attestation skipped: receipt_source=%s",
                None if receipt is None else getattr(receipt, "source", None),
            )
            return False  # a replayed or unreceipted frame cannot be attested
        try:
            client.clock_observation(receipt=receipt, timeout_seconds=self._timeout)
        except Exception as exc:
            logging.getLogger(__name__).warning(
                "risk market attestation failed: %s (cause=%s)",
                getattr(exc, "code", None) or type(exc).__name__,
                getattr(exc, "cause_code", None),
            )
            return False
        return True

    def _recover_subscription(self, client: Any, exc: Exception) -> bool:
        code = getattr(exc, "code", None)
        if code in ("RESYNC_REQUIRED", "PROVIDER_RESYNC_REQUIRED"):
            accept = getattr(client, "accept_resync", None)
            if callable(accept):
                try:
                    accept(acknowledge_data_loss=True, timeout_seconds=self._timeout)
                    return True
                except Exception:
                    return False
        elif code == "RECOVERY_REQUIRED":
            recover = getattr(client, "recover", None)
            if callable(recover):
                try:
                    recover(timeout_seconds=self._timeout)
                    return True
                except Exception:
                    return False
        return False

    def _poll_one(self, client: Any, key: MarketSubscriptionKey, instrument_id: str, remaining_budget: int) -> tuple[int, int]:
        count = 0
        consumed = 0
        latest = None
        while consumed < remaining_budget:
            try:
                received = client.poll_with_receipt(timeout_seconds=self._timeout)
            except Exception as exc:
                # A broken subscription leaves no usable reference: refuse rather than reuse.
                logging.getLogger(__name__).warning(
                    "risk market poll failed: instrument=%s code=%s",
                    instrument_id,
                    getattr(exc, "code", None) or type(exc).__name__,
                )
                self._facts.note_tick(instrument_id, None, None, None)
                self._last_received.pop(instrument_id, None)
                latest = None
                if self._recover_subscription(client, exc):
                    break
                self.failures += 1
                break
            if received is None:
                break
            consumed += 1
            try:
                mapped = price_fact_from_market_event(received, key)
            except Exception as exc:
                logging.getLogger(__name__).warning(
                    "risk market map failed: instrument=%s err=%s",
                    instrument_id,
                    f"{type(exc).__name__}:{str(exc)[:80]}",
                )
                self.failures += 1
                self._facts.note_tick(instrument_id, None, None, None)
                self._last_received.pop(instrument_id, None)
                latest = None
                continue
            latest = (received, mapped)
            count += 1
        if latest is None:
            return count, consumed
        received, (fact, ref, day) = latest
        # The attestation binds the client's last issued frame: this one.
        attested = self._attested(client, received)
        if not attested:
            self.attestation_failures += 1
            ref = None
        try:
            local_receive = self._local_receive(client, received, key)
        except Exception:
            local_receive = None
        if local_receive is None:
            self._facts.note_tick(instrument_id, fact, ref, day)
        else:
            self._facts.note_tick(instrument_id, fact, ref, day, _local_receive=local_receive)
        self._last_received[instrument_id] = (
            received,
            (fact, ref, day),
            time.monotonic_ns() if attested else 0,
        )
        return count, consumed

    def ensure_fresh_market_ref(self, instrument_id: str) -> None:
        """Before a live delegated dispatch, drain any new PUB tick and ensure a fresh attestation."""
        from bt_api_py.gateway_strategy_market import GatewayMarketSubscriptionClient

        entry = self._by_instrument.get(instrument_id)
        if entry is None:
            return
        client, key = entry
        if type(client) is not GatewayMarketSubscriptionClient:
            return
        if not self._poll_lock.acquire(blocking=False):
            return
        try:
            polled, _ = self._poll_one(client, key, instrument_id, self._max)
            if polled > 0:
                return
            cached = self._last_received.get(instrument_id)
            if cached is None:
                return
            received, (fact, _old_ref, day), attested_ns = cached
            receipt = getattr(received, "receipt", None)
            if receipt is None or getattr(receipt, "source", None) != "pub":
                return
            has_ref = instrument_id in self._facts._market_refs
            if has_ref and attested_ns > 0 and (time.monotonic_ns() - attested_ns) < 1_000_000_000:
                return
            try:
                _fact, fresh_ref, _day = price_fact_from_market_event(received, key)
            except Exception:
                return
            if self._attested(client, received):
                try:
                    local_receive = self._local_receive(client, received, key)
                except Exception:
                    local_receive = None
                if local_receive is None:
                    self._facts.note_tick(instrument_id, fact, fresh_ref, day)
                else:
                    self._facts.note_tick(instrument_id, fact, fresh_ref, day, _local_receive=local_receive)
                self._last_received[instrument_id] = (
                    received,
                    (fact, fresh_ref, day),
                    time.monotonic_ns(),
                )
        finally:
            self._poll_lock.release()

    @staticmethod
    def _local_receive(client: Any, received: Any, key: MarketSubscriptionKey) -> tuple[int, int] | None:
        """Take only a locally issued PUB receipt; this asserts no current liveness."""
        from bt_api_py.gateway_strategy_market import GatewayMarketReceivedEvent, GatewayMarketSubscriptionClient
        from bt_api_py.service_market import MarketReceiveReceipt
        if type(client) is not GatewayMarketSubscriptionClient or type(received) is not GatewayMarketReceivedEvent:
            return None
        receipt = received.receipt
        if type(receipt) is not MarketReceiveReceipt or not client._issuance_lock.acquire(False):
            return None
        try:
            header = received.event.header
            if (client._issued_received_event is not received or receipt.source != "pub"
                    or receipt.pid != os.getpid() or client._owner_pid != os.getpid()
                    or type(receipt.received_monotonic_ns) is not int
                    or receipt.received_monotonic_ns <= 0
                    or receipt.received_monotonic_ns > time.monotonic_ns()
                    or receipt.stream_id != header.stream_id or receipt.stream_id != key.stream_id
                    or receipt.stream_epoch != header.stream_epoch or receipt.sequence != header.sequence
                    or receipt.message_id != header.message_id
                    or received.event.payload.get("key") != key.to_dict()):
                return None
            return receipt.received_monotonic_ns, receipt.pid
        except Exception:
            return None
        finally:
            client._issuance_lock.release()

    def poll(self) -> int:
        if not self._poll_lock.acquire(blocking=False):
            raise RuntimeError("RISK_MARKET_POLL_BUSY")
        try:
            return self._poll_round()
        finally:
            self._poll_lock.release()

    def _poll_round(self) -> int:
        count = 0
        consumed = 0
        subscriptions = tuple(self._subscriptions.values())
        if not subscriptions:
            return 0
        start = self._next_subscription % len(subscriptions)
        self._next_subscription = (start + 1) % len(subscriptions)
        ordered = subscriptions[start:] + subscriptions[:start]
        for client, key, instrument_id in ordered:
            if consumed >= self._max:
                break
            c, used = self._poll_one(client, key, instrument_id, self._max - consumed)
            count += c
            consumed += used
        return count


# ============================================================================ account snapshot


def account_snapshot_from_observation(
    observation: Any,
    *,
    instrument_ids: Mapping[MarketInstrumentKey, str],
    dated_venues: frozenset[str],
) -> Any:
    """Map a typed ``AccountObservation`` to the Risk ``AccountSnapshot``.

    Fields the observation does not carry (trading day, margin, profits,
    commission) stay None (unknown). Only complete configured instrument keys
    can be projected, and rows for the same instrument must use the same
    observed unit. An unrepresentable row makes the whole projection unavailable.
    The resulting Risk DTO does not preserve units and remains incomplete.
    """
    from bt_api_base.account_protocol import MAX_ACCOUNT_ROWS, MAX_DECIMAL_BYTES
    from bt_api_risk.pretrade import AccountSnapshot
    from bt_api_risk.pretrade.controls import RiskControlArithmeticError, _add

    # The observation alone has no source-bound expiry in the Risk UTC clock
    # domain. Keep the data for diagnostics, but it cannot establish a usable
    # position or financial fact until the source lease is bound end to end.
    complete = False
    positions: dict[tuple[str, str], tuple[Decimal, Decimal]] = {}
    units: dict[MarketInstrumentKey, str] = {}
    try:
        if (not isinstance(instrument_ids, Mapping)
                or any(type(key) is not MarketInstrumentKey or not _ref(value)
                       or key.instrument_id != value for key, value in instrument_ids.items())
                or len(set(instrument_ids.values())) != len(instrument_ids)):
            raise ValueError
        rows = observation.positions
        if type(rows) is not tuple or len(rows) > MAX_ACCOUNT_ROWS:
            raise ValueError

        def amount(raw: object) -> Decimal:
            # Provider account DTO quantities are bounded decimal strings.
            if (type(raw) is not str or not raw or len(raw) > MAX_DECIMAL_BYTES
                    or re.fullmatch(r"-?(?:0|[1-9][0-9]*)(?:\.[0-9]+)?", raw, flags=re.ASCII) is None):
                raise ValueError
            number = _decimal(raw, positive=False)
            if number is None:
                raise ValueError
            return _add(Decimal(0), number)

        for row in rows:
            side = {"long": "LONG", "short": "SHORT"}.get(row.side)
            instrument = row.instrument
            if type(instrument) is not MarketInstrumentKey or side is None:
                raise ValueError
            instrument_id = instrument_ids.get(instrument)
            if instrument_id is None or not _ref(row.qty_unit):
                raise ValueError
            previous_unit = units.setdefault(instrument, row.qty_unit)
            if previous_unit != row.qty_unit:
                raise ValueError
            quantity = amount(row.quantity)
            if row.today_quantity is None and row.yesterday_quantity is None:
                if instrument.venue in dated_venues:
                    raise ValueError
                today, yesterday = quantity, Decimal(0)
            else:
                today, yesterday = amount(row.today_quantity), amount(row.yesterday_quantity)
                if _add(today, yesterday) != quantity:
                    raise ValueError
            old = positions.get((instrument_id, side), (Decimal(0), Decimal(0)))
            positions[(instrument_id, side)] = (_add(old[0], today), _add(old[1], yesterday))
    except (AttributeError, KeyError, TypeError, ValueError, RiskControlArithmeticError):
        raise ValueError("RISK_ACCOUNT_POSITION_UNAVAILABLE") from None
    valuation = observation.valuation
    balance = available = equity = None
    currency = None
    if len(observation.balances) == 1:
        only = observation.balances[0]
        balance = _decimal(only.total, positive=False)
        available = _decimal(only.available, positive=False)
        currency = only.currency
    if valuation is not None:
        valuation_currency = valuation.currency if _ref(valuation.currency) else None
        valuation_available = _decimal(valuation.available_cash, positive=False)
        # A zero valuation is an observed value, not a missing one. The balance
        # fallback is usable only when its denomination matches the valuation.
        if valuation_currency is None or currency != valuation_currency:
            balance = available = None
        if valuation_currency is not None and valuation_available is not None:
            available = valuation_available
        elif valuation.available_cash is not None:
            # A malformed value is not an absent value and cannot justify a fallback.
            available = None
        equity = _decimal(valuation.equity, positive=False)
        if valuation_currency is None:
            equity = None
        currency = valuation_currency
    return AccountSnapshot(
        as_of_ns=_utc_ns(observation.completed_at_utc),
        trading_day=None,
        positions=positions,
        balance=balance,
        available=available,
        equity=equity,
        currency=currency if _ref(currency) else None,
        complete=complete,
    )


class RiskAccountSnapshotSource:
    """Refresh the operator account snapshot (``account.snapshot.typed``) into the cache."""

    def __init__(self, client: Any, facts: RiskFactsCache, *, instrument_ids: Mapping[MarketInstrumentKey, str], timeout_seconds: float) -> None:
        self._client, self._facts = client, facts
        # Check the host's complete identities against this cache's bindings.
        full_ids: dict[MarketInstrumentKey, str] = {}
        try:
            for instrument_id, binding in facts._instruments.items():
                key = binding.key
                if (type(key) is not MarketInstrumentKey or not _ref(instrument_id)
                        or binding.instrument_id != instrument_id or key.instrument_id != instrument_id
                        or key in full_ids):
                    raise ValueError
                full_ids[key] = instrument_id
            if not isinstance(instrument_ids, Mapping) or dict(instrument_ids) != full_ids:
                raise ValueError
        except (AttributeError, TypeError, ValueError):
            raise ValueError("RISK_ACCOUNT_POSITION_CONFIG_INVALID") from None
        self._instrument_ids = dict(full_ids)
        self._timeout = float(timeout_seconds)
        self.failures = 0

    def refresh(self) -> bool:
        from bt_api_risk.pretrade import DATED_BUCKET_VENUES

        try:
            view = self._client.refresh(timeout_seconds=self._timeout)
            snapshot = account_snapshot_from_observation(
                view.observation,
                instrument_ids=self._instrument_ids,
                dated_venues=frozenset(DATED_BUCKET_VENUES),
            )
            self._facts.set_account(snapshot)
        except Exception:
            self.failures += 1
            self._facts.forget_account()
            return False
        return True


# ============================================================================ strategy financial (G64-06 b)

STRATEGY_FINANCIAL_OPERATION = "account.strategy_financial"


def strategy_financials_from_reply(
    raw: object, *, account_scope: str, trading_day: str | None, now_ns: int
) -> dict[str, Any]:
    """Map an ``account.strategy_financial`` reply to ``StrategyFinancialFact`` per strategy.

    The read carries neither a trading day nor a start-of-day value: the fact is
    stamped with the Risk service's current Gateway-bound trading day (None when
    unknown, which makes it unusable). Neither a day-start value nor a complete
    intraday high-water value is provided; both remain None rather than being
    inferred from the first observed value. A reply that is not
    complete, an UNAVAILABLE item, or more than one currency for one strategy
    leave that strategy without a usable fact.
    """
    from bt_api_risk.pretrade import StrategyFinancialFact

    if (
        type(raw) is not dict
        or raw.get("status") != "OBSERVED"
        or raw.get("schema_version") != 1
        or raw.get("account_scope") != account_scope
        or type(raw.get("complete")) is not bool
        or type(raw.get("strategies")) is not list
    ):
        raise ValueError("RISK_STRATEGY_FINANCIAL_REPLY_INVALID")
    # This v1 reply has no source time/expiry. Repeated reads must not turn an
    # old snapshot into a fresh one by stamping it with the receipt time.
    complete = False
    seen: dict[str, int] = {}
    values: dict[str, Any] = {}
    for item in raw["strategies"]:
        if type(item) is not dict or not _ref(item.get("strategy")):
            raise ValueError("RISK_STRATEGY_FINANCIAL_REPLY_INVALID")
        strategy = item["strategy"]
        seen[strategy] = seen.get(strategy, 0) + 1
        value = _decimal_signed(item.get("value")) if item.get("status") == "OBSERVED" else None
        if value is not None:
            values[strategy] = StrategyFinancialFact(
                strategy_ref=strategy,
                trading_day=_day(trading_day),
                value=value,
                as_of_ns=None,
                complete=complete,
                day_start_value=None,
            )
    return {key: fact for key, fact in values.items() if seen[key] == 1}


def _decimal_signed(value: object) -> Decimal | None:
    if type(value) is not str:
        return None
    try:
        number = Decimal(value)
    except InvalidOperation:
        return None
    return number if number.is_finite() else None


class StrategyFinancialSource:
    """Refresh strategy financial facts through the Risk control principal's read registration."""

    def __init__(
        self,
        runtime: Any,
        facts: RiskFactsCache,
        *,
        mount: str,
        service_id: str,
        instance_id: str,
        scope_ref: str,
        account_ref: str,
        timeout_seconds: float,
    ) -> None:
        self._runtime, self._facts = runtime, facts
        self._mount, self._service_id = mount, service_id
        self._instance_id, self._scope_ref, self._account = instance_id, scope_ref, account_ref
        self._timeout = float(timeout_seconds)
        self.failures = 0

    def refresh(self) -> bool:
        try:
            raw = _detach_wire(
                self._runtime.call(
                    self._mount,
                    STRATEGY_FINANCIAL_OPERATION,
                    {},
                    account_ref=self._account,
                    scope_ref=self._scope_ref,
                    timeout_seconds=self._timeout,
                )
            )
            values = strategy_financials_from_reply(
                raw,
                account_scope=self._account,
                trading_day=self._facts.trading_day(),
                now_ns=self._facts.now_ns(),
            )
        except Exception:
            self.failures += 1
            self._facts.set_strategy_financials({})  # fail closed: every strategy unknown
            return False
        self._facts.set_strategy_financials(values)
        return True


# ============================================================================ observation pump


def observed_event_from_account_event(
    item: Any,
    *,
    epoch: str,
    risk_principal: str,
    intent_payload: Callable[[str], Mapping | None],
) -> Any:
    """Map one ``GatewayAccountObservedEvent`` to ``ObservedExecutionEvent``.

    Gateway sequences start at 1; the engine's start at 0 (``sequence - 1``).
    Missing side, offset and quantity may come from Risk's own durable delegate
    intent of the same request key; a trade's missing order quantity can instead
    be bounded below by its trade quantity. A missing observed limit price stays
    unknown: the original intent price does not establish the current order price.
    """
    from bt_api_risk.pretrade import ObservedExecutionEvent, RiskFactError
    from bt_api_risk.pretrade.facts import GatewayObservationIdentity
    from bt_api_base.direct_write_protocol import DirectExecutionEventKind, DirectOrderStatus
    from bt_api_base.gateway_execution_stream_protocol import (
        GatewayExecutionObservation, GatewayExecutionStreamEvent,
    )
    from bt_api_py.gateway_account_observer_client import GatewayAccountObservedEvent

    def source_decimal(value: object, *, positive: bool) -> Decimal | None:
        # Gateway's source fact contract uses canonical bounded decimal text;
        # an explicitly malformed field must never be interpreted as absent.
        if value is None:
            return None
        if (type(value) is not str or len(value) > 128
                or re.fullmatch(r"(?:0|[1-9][0-9]{0,35})(?:\.[0-9]{0,17}[1-9])?", value, re.ASCII) is None
                or (positive and value == "0")):
            raise RiskFactError("RISK_OBSERVATION_INVALID")
        return Decimal(value)

    def payload_decimal(value: object, *, positive: bool) -> Decimal | None:
        result = _decimal(value, positive=positive)
        if value is not None and result is None:
            raise RiskFactError("RISK_OBSERVATION_INVALID")
        return result

    # Preserve the original stream labels as a single structural claim. These
    # values do not authenticate the event or grant an ACK entitlement.
    try:
        if (type(item) is not GatewayAccountObservedEvent
                or type(item.event) is not GatewayExecutionStreamEvent
                or type(item.event.observation) is not GatewayExecutionObservation):
            raise RiskFactError("RISK_OBSERVATION_INVALID")
        # Base serialization revalidates the complete event, including nested
        # attribution associations. Reconstruct before invoking the payload
        # callback so later mutation of the supplied object cannot alter the
        # economic facts while retaining the old digest.
        source_event = item.event
        source_wire = source_event.to_mapping()
        event = GatewayExecutionStreamEvent.from_mapping(source_wire)
        observation = event.observation
        sequence = event.sequence
        request_key, reservation_id, strategy_scope = event.request_key, event.reservation_id, item.strategy_scope
        trading_day = _day(observation.trading_day)
        if (type(sequence) is not int or not 1 <= sequence < 2**63
                or not all(_ref(value) for value in (request_key, reservation_id, strategy_scope))
                or (observation.trade_attribution is not None
                    and observation.trade_attribution.strategy_scope != strategy_scope)
                or trading_day is None or type(observation.instrument) is not MarketInstrumentKey
                or type(observation.kind) is not DirectExecutionEventKind
                or (observation.status is not None and type(observation.status) is not DirectOrderStatus)
                or (observation.side is not None and (type(observation.side) is not str
                    or observation.side not in ("buy", "sell")))
                or (observation.offset is not None and (type(observation.offset) is not str
                    or observation.offset not in ("open", "close", "close_today", "close_yesterday")))
                or (observation.kind is DirectExecutionEventKind.TRADE and observation.status is not None)
                or (observation.kind is not DirectExecutionEventKind.TRADE and any(value is not None
                    for value in (observation.trade_quantity, observation.trade_price,
                                  observation.trade_key_digest, getattr(observation, "trade_time", None))))):
            raise RiskFactError("RISK_OBSERVATION_INVALID")
        instrument = MarketInstrumentKey.from_dict(observation.instrument.to_dict())
        order_quantity = source_decimal(observation.order_quantity, positive=True)
        filled = source_decimal(observation.filled_quantity, positive=False)
        source_decimal(getattr(observation, "remaining_quantity", None), positive=False)
        limit_price = source_decimal(observation.limit_price, positive=True)
        trade_quantity = source_decimal(observation.trade_quantity, positive=True)
        trade_price = source_decimal(observation.trade_price, positive=True)
        gateway_identity = GatewayObservationIdentity(
            account_scope=observation.account_scope,
            route=observation.route,
            command_principal=event.command_principal,
            command_digest=event.command_digest,
            fact_digest=event.fact_digest,
        )
    except (AttributeError, TypeError, ValueError):
        raise RiskFactError("RISK_OBSERVATION_INVALID") from None
    ours = gateway_identity.command_principal == risk_principal
    origin = ("risk:" + risk_principal) if ours else "self"
    kind = observation.kind.value
    payload = intent_payload(request_key) if ours else None

    def from_payload(name: str) -> Any:
        return None if payload is None else payload.get(name)

    side = observation.side or from_payload("side")
    offset = observation.offset or from_payload("offset")
    quantity = (order_quantity if order_quantity is not None
                else payload_decimal(from_payload("quantity"), positive=True))
    if observation.status is not None:
        status = observation.status.value
    elif kind == "trade":
        status = "partially_filled"
    elif kind == "order_error":
        status = "rejected"
    else:
        status = "accepted"  # a cancel error leaves the order as it was: still working
    if quantity is None and kind == "trade":
        # A trade return carries no order quantity, and an order Risk did not place has
        # no Risk intent (a bypass or external order, INV64-10). The trade quantity is a
        # lower bound of the order quantity; the engine keeps the largest order quantity
        # any event of the order reports. Refusing the event instead would leave the
        # projection UNVERIFIED for good: every replay meets the same event again.
        quantity = trade_quantity
    if filled is None:
        filled = Decimal(0) if trade_quantity is None or quantity is None else min(trade_quantity, quantity)
    try:
        # The callback and its returned mapping may both execute code with a
        # reference to the original event; neither may silently alter it.
        if (item.event is not source_event or item.strategy_scope != strategy_scope
                or source_event.to_mapping() != source_wire):
            raise RiskFactError("RISK_OBSERVATION_INVALID")
    except (AttributeError, TypeError, ValueError):
        raise RiskFactError("RISK_OBSERVATION_INVALID") from None
    return ObservedExecutionEvent(
        epoch=epoch,
        sequence=sequence - 1,
        kind=kind,
        strategy_ref=strategy_scope,
        origin=origin,
        request_key=request_key,
        reservation_id=reservation_id,
        instrument=instrument,
        side=str(side).upper() if side is not None else "",
        offset=None if offset is None else str(offset).upper(),
        status=status,
        quantity=quantity,  # type: ignore[arg-type]
        filled_quantity=filled,
        limit_price=limit_price,
        trade_id=observation.trade_key_digest if kind == "trade" else None,
        trade_price=trade_price if kind == "trade" else None,
        trade_quantity=trade_quantity if kind == "trade" else None,
        trading_day=trading_day,
        order_ref=observation.venue_key_digest,
        gateway_identity=gateway_identity,
    )


class ObservationPump:
    """Account observation stream -> engine; ACK only what the engine applied.

    A new epoch, a sequence the Gateway no longer accepts, a read failure or an
    unmappable event marks the projection UNVERIFIED and replays from 0.
    """

    def __init__(
        self,
        engine: Any,
        observer: Any,
        facts: RiskFactsCache,
        *,
        risk_principal: str,
        intent_payload: Callable[[str], Mapping | None],
        timeout_seconds: float,
        limit: int = _OBSERVER_LIMIT,
    ) -> None:
        self._engine, self._observer, self._facts = engine, observer, facts
        self._principal = risk_principal
        self._intent_payload = intent_payload
        self._timeout = float(timeout_seconds)
        self._limit = limit
        self._epoch: str | None = None
        self._cursor_revision: str | None = None
        self._after = 0
        self._needs_reset = True
        self._restarted = False
        self._generation = 0
        self._polling = False
        self._reentered = False
        self._replay_target = 0
        self._event_day_clear_required = True
        self._clearing_event_day = False
        self._marking_gap = False
        self._gap_record_failed = False
        self._gap_record_reason: str | None = None
        self._checking_verified = False
        self._verified_reentered = False
        self.last_code: str | None = None
        self.acknowledged = 0

    @property
    def verified_epoch(self) -> str | None:
        state = object.__getattribute__(self, "__dict__")
        if state["_checking_verified"]:
            state["_verified_reentered"] = True
            return None
        state["_checking_verified"] = True
        state["_verified_reentered"] = False
        try:
            observer, generation, epoch = state["_observer"], state["_generation"], state["_epoch"]
            engine = state["_engine"]
            after, target, revision = state["_after"], state["_replay_target"], state["_cursor_revision"]
            if (state["_polling"] or state["_needs_reset"] or state["_reentered"]
                    or state["_event_day_clear_required"] or state["_gap_record_failed"]
                    or after < target or epoch is None):
                return None
            matches = engine.observation_cursor_matches(epoch, after - 1, revision) is True
            if (matches and engine is state["_engine"] and observer is state["_observer"]
                    and generation == state["_generation"]
                    and epoch == state["_epoch"] and after == state["_after"]
                    and target == state["_replay_target"] and revision == state["_cursor_revision"]
                    and not state["_polling"] and not state["_needs_reset"] and not state["_reentered"]
                    and not state["_verified_reentered"] and not state["_event_day_clear_required"]
                    and not state["_gap_record_failed"]):
                return epoch
            return None
        except Exception:
            return None
        finally:
            state["_checking_verified"] = False

    def replace_observer(self, observer: Any) -> None:
        """Require a durable gap and full replay when a registration is replaced."""
        self._gap("observation_reconnect")
        self._observer = observer

    def _gap(self, reason: str, *, force: bool = False) -> None:
        should_mark = force or not self._needs_reset
        self._generation += 1
        self._needs_reset = True
        self._event_day_clear_required = True
        self.last_code = reason
        generation = self._generation
        if should_mark:
            self._gap_record_failed = True
            self._gap_record_reason = reason
        if not self._clear_event_day() or generation != self._generation:
            return
        if not should_mark or self._marking_gap:
            return
        self._marking_gap = True
        marked = False
        try:
            self._engine.mark_observation_gap(reason)
            marked = True
        except Exception:
            _LOG.warning("Risk observation gap could not be recorded: %s", reason)
        finally:
            self._marking_gap = False
            # The callback may have written a new event day, even on failure.
            self._event_day_clear_required = True
            self._clear_event_day()
        if marked and generation == self._generation and not self._reentered:
            self._gap_record_failed = False

    def _clear_event_day(self) -> bool:
        if self._clearing_event_day:
            return False
        generation = self._generation
        was_reentered = self._reentered
        self._clearing_event_day = True
        try:
            forget = getattr(self._facts, "forget_event_day", None)
            if not callable(forget):
                raise TypeError("facts has no event-day invalidation hook")
            forget()
            if generation != self._generation or (self._reentered and not was_reentered):
                return False
            self._event_day_clear_required = False
            return True
        except Exception:
            self.last_code = "observation_fact_invalidation_failed"
            _LOG.warning("Risk observation event-day invalidation failed")
            return False
        finally:
            if generation != self._generation or (self._reentered and not was_reentered):
                if not self._gap_record_failed:
                    self._gap_record_reason = "observation_reentrant"
                self._gap_record_failed = True
            self._clearing_event_day = False

    def _reset(self) -> bool:
        from bt_api_risk.pretrade.engine import ObservationApplyResult

        if self._gap_record_failed:
            return False
        if self._event_day_clear_required and not self._clear_event_day():
            return False
        observer, generation = self._observer, self._generation
        try:
            progress = observer.progress(timeout_seconds=self._timeout)
        except Exception:
            self._gap("observation_progress_failed")
            return False
        if observer is not self._observer or generation != self._generation or self._reentered:
            self._gap("observation_reentrant")
            return False
        try:
            outcome = getattr(progress, "outcome", None)
            current = getattr(progress, "progress", None)
            code = getattr(progress, "code", None)
            epoch = getattr(current, "epoch", None)
            offered = getattr(current, "offered_sequence", None)
            acknowledged = getattr(current, "acknowledged_sequence", None)
        except Exception:
            self._gap("observation_invalid_progress")
            return False
        if outcome != "OBSERVED" or current is None:
            self.last_code = code or outcome or "observation_invalid_progress"
            return False
        if (not _wire_epoch(epoch) or not _wire_sequence(offered)
                or not _wire_sequence(acknowledged) or acknowledged > offered):
            self.last_code = "observation_invalid_progress"
            return False
        if self._epoch == epoch and acknowledged < self.acknowledged:
            self.last_code = "observation_ack_regressed"
            return False
        # Head: the consumer has been offered at least this many events (lower bound).
        try:
            receipt = self._engine.reset_observations_bound(epoch, offered - 1)
        except Exception:
            self._gap("observation_reset_failed", force=True)
            return False
        if observer is not self._observer or generation != self._generation or self._reentered:
            self._gap("observation_reentrant", force=True)
            return False
        try:
            reset_proven = (type(receipt) is ObservationApplyResult and receipt.epoch == epoch
                            and type(receipt.last_sequence) is int and receipt.last_sequence == -1
                            and type(receipt.applied_count) is int and receipt.applied_count == 0
                            and type(receipt.covered_count) is int and receipt.covered_count == 0
                            and receipt.blocked is False and type(receipt.cursor_revision) is str
                            and bool(receipt.cursor_revision)
                            and self._engine.observation_cursor_matches(
                                epoch, -1, receipt.cursor_revision
                            ) is True)
        except Exception:
            reset_proven = False
        if (not reset_proven or observer is not self._observer or generation != self._generation
                or self._reentered):
            self._gap("observation_reset_unproven", force=True)
            return False
        if self._epoch != epoch:
            self.acknowledged = acknowledged
        else:
            self.acknowledged = max(self.acknowledged, acknowledged)
        self._epoch, self._after, self._needs_reset = epoch, 0, False
        self._cursor_revision = receipt.cursor_revision
        self._replay_target = offered
        return True

    def poll(self) -> int:
        """Read and apply until the stream has no more events; return events applied."""
        if self._checking_verified:
            self._verified_reentered = True
            return 0
        if self._polling:
            self._reentered = True
            return 0
        self._polling, self._reentered = True, False
        try:
            return self._poll()
        finally:
            self._polling = False

    def _poll(self) -> int:
        from bt_api_risk.pretrade.engine import ObservationApplyResult

        if not self._restarted:
            # The ledger cursor survives a restart; until this process has replayed the
            # stream from 0, the projection it describes is not verified (freeze 4.2).
            self._restarted = True
            self._gap("observation_restart", force=True)
            if self._event_day_clear_required:
                return 0
        elif self._gap_record_failed:
            self._gap(self._gap_record_reason or "observation_gap", force=True)
            if self._event_day_clear_required:
                return 0
        if self._needs_reset and not self._reset():
            return 0
        applied = 0
        while True:
            observer, generation, epoch, before = self._observer, self._generation, self._epoch, self._after
            try:
                result = observer.read_after(
                    after_sequence=self._after,
                    expected_epoch=self._epoch,
                    limit=self._limit,
                    timeout_seconds=self._timeout,
                )
            except Exception:
                self._gap("observation_read_failed")
                return applied
            if (observer is not self._observer or generation != self._generation
                    or epoch != self._epoch or self._reentered):
                self._gap("observation_reentrant")
                return applied
            try:
                outcome = getattr(result, "outcome", None)
                page = getattr(result, "page", None)
                code = getattr(result, "code", None)
            except Exception:
                self._gap("observation_read_failed")
                return applied
            if outcome != "OBSERVED" or page is None:
                if outcome == "REQUEST_DENIED" and code in _REPLAY_CODES:
                    self._gap("observation_replay_required")
                else:
                    self._gap("observation_read_failed")
                return applied
            try:
                progress = getattr(page, "progress", None)
                page_epoch = getattr(progress, "epoch", None)
                offered = getattr(progress, "offered_sequence", None)
                page_acknowledged = getattr(progress, "acknowledged_sequence", None)
                events, has_more = getattr(page, "events", None), getattr(page, "has_more", None)
            except Exception:
                self._gap("observation_progress_invalid")
                return applied
            if page_epoch != self._epoch:
                self._gap("epoch_changed")
                return applied
            if (not _wire_sequence(offered) or not _wire_sequence(page_acknowledged)
                    or page_acknowledged > offered or page_acknowledged != self.acknowledged
                    or offered < before or offered < self._replay_target
                    or type(has_more) is not bool or type(events) not in (tuple, list)
                    or len(events) > self._limit):
                self._gap("observation_progress_invalid")
                return applied
            self._replay_target = offered
            mapped = []
            broken = None
            previous, expected = -1, before
            for item in events:
                try:
                    event = observed_event_from_account_event(
                        item, epoch=epoch, risk_principal=self._principal,
                        intent_payload=self._intent_payload,
                    )
                    day = event.trading_day
                    if (type(day) is not str or len(day) != 8 or not day.isascii()
                            or not day.isdigit()):
                        broken = "observation_invalid_trading_day"
                        break
                    try:
                        date.fromisoformat(day)
                    except ValueError:
                        broken = "observation_invalid_trading_day"
                        break
                    if (event.sequence + 1 > offered or event.sequence <= previous
                            or (event.sequence >= before and event.sequence != expected)):
                        broken = "observation_sequence_gap"
                        break
                    mapped.append(event)
                    previous = event.sequence
                    if event.sequence >= before:
                        expected += 1
                except Exception:
                    broken = "observation_unmappable"
                    break
            if (observer is not self._observer or generation != self._generation
                    or epoch != self._epoch or self._reentered):
                self._gap("observation_reentrant")
                return applied
            if broken:
                # The page is already known incomplete. Revoke its observation
                # day before any apply/ACK callback can inspect the old facts.
                self._event_day_clear_required = True
                cleared = self._clear_event_day()
                if (not cleared or observer is not self._observer or generation != self._generation
                        or epoch != self._epoch or self._reentered):
                    self._gap("observation_fact_invalidation_failed" if not cleared
                              else "observation_reentrant")
                    return applied
            if mapped:
                try:
                    receipt = self._engine.apply_observations_bound(mapped)
                except Exception:
                    self._gap("observation_apply_failed")
                    raise
                if (type(receipt) is not ObservationApplyResult or receipt.epoch != epoch
                        or type(receipt.last_sequence) is not int
                        or type(receipt.applied_count) is not int or type(receipt.blocked) is not bool
                        or type(receipt.covered_count) is not int
                        or type(receipt.cursor_revision) is not str or not receipt.cursor_revision
                        or receipt.last_sequence < before - 1
                        or receipt.last_sequence > max(before - 1, mapped[-1].sequence)):
                    self._gap("observation_apply_unproven")
                    return applied
                last = receipt.last_sequence
                covered = [event for event in mapped if before <= event.sequence <= last]
                verified = sum(event.sequence <= last for event in mapped)
                if (receipt.covered_count != verified
                        or not 0 <= receipt.applied_count <= len(covered)):
                    self._gap("observation_apply_unproven")
                    return applied
                # The engine counts only new fingerprints; a matching replay
                # can advance the cursor while contributing zero new events.
                applied += receipt.applied_count
                if (receipt.blocked or (covered and last < mapped[-1].sequence)
                        or (not covered and mapped[-1].sequence >= before)):
                    self._gap("observation_partial_apply")
                    return applied
                if broken:
                    # apply may itself invoke a callback which writes a day.
                    self._event_day_clear_required = True
                    cleared = self._clear_event_day()
                    if (not cleared or observer is not self._observer or generation != self._generation
                            or epoch != self._epoch or self._reentered):
                        self._gap("observation_fact_invalidation_failed" if not cleared
                                  else "observation_reentrant")
                        return applied
                try:
                    cursor_matches = self._engine.observation_cursor_matches(
                        epoch, last, receipt.cursor_revision
                    ) is True
                except Exception:
                    cursor_matches = False
                if (observer is not self._observer or generation != self._generation
                        or epoch != self._epoch or self._reentered or not cursor_matches):
                    self._gap("observation_apply_unproven")
                    return applied
                if (observer is not self._observer or generation != self._generation
                        or epoch != self._epoch or self._reentered):
                    self._gap("observation_reentrant")
                    return applied

                def projection_still_bound() -> bool:
                    if (observer is not self._observer or generation != self._generation
                            or epoch != self._epoch or self._reentered):
                        return False
                    try:
                        matches = self._engine.observation_cursor_matches(
                            epoch, last, receipt.cursor_revision
                        ) is True
                    except Exception:
                        matches = False
                    return (matches and observer is self._observer and generation == self._generation
                            and epoch == self._epoch and not self._reentered)

                if covered and not broken:
                    # RiskFactsCache retains only the maximum; injected facts
                    # receive one projection callback per covered page.
                    days = (_day(event.trading_day) for event in covered)
                    latest_day = max((day for day in days if day is not None), default=None)
                    try:
                        self._facts.note_event_day(latest_day)
                    except Exception:
                        self._gap("observation_fact_projection_failed")
                        return applied
                self._cursor_revision = receipt.cursor_revision
                if last + 1 > before:
                    sequence = last + 1
                    if not projection_still_bound():
                        self._gap("observation_apply_unproven")
                        return applied
                    try:
                        ack_kwargs = {"expected_epoch": epoch, "timeout_seconds": self._timeout}
                        read_token = getattr(result, "_read_token", None)
                        if read_token is not None:
                            ack_kwargs["read_token"] = read_token
                        ack = observer.acknowledge(sequence, **ack_kwargs)
                    except Exception:
                        self._gap("observation_ack_failed")
                        return applied
                    if (observer is not self._observer or generation != self._generation
                            or epoch != self._epoch or self._reentered):
                        self._gap("observation_reentrant")
                        return applied
                    try:
                        progress = getattr(ack, "progress", None)
                        acknowledged = getattr(progress, "acknowledged_sequence", None)
                        offered = getattr(progress, "offered_sequence", None)
                        ack_proven = (getattr(ack, "outcome", None) == "OBSERVED"
                                      and getattr(progress, "epoch", None) == epoch
                                      and _wire_sequence(acknowledged) and _wire_sequence(offered)
                                      and sequence <= acknowledged <= max(sequence, self.acknowledged)
                                      and offered >= acknowledged and offered >= self._replay_target)
                    except Exception:
                        ack_proven = False
                    if not ack_proven:
                        self._gap("observation_ack_unproven")
                        return applied
                    try:
                        cursor_matches = self._engine.observation_cursor_matches(
                            epoch, last, receipt.cursor_revision
                        ) is True
                    except Exception:
                        cursor_matches = False
                    if (not cursor_matches or observer is not self._observer
                            or generation != self._generation or epoch != self._epoch or self._reentered):
                        self._gap("observation_ack_unproven")
                        return applied
                    self.acknowledged = max(self.acknowledged, acknowledged)
                    self._replay_target = max(self._replay_target, offered)
                    self._after = sequence
                elif has_more and not broken:
                    self._gap("observation_no_progress")
                    return applied
            if broken:
                self._gap(broken)
                return applied
            if not events and (has_more or self._after < self._replay_target):
                self._gap("observation_no_progress")
                return applied
            if not has_more or not events:
                if self._after < self._replay_target:
                    self._gap("observation_replay_incomplete")
                return applied


def payload_reader(storage: Any) -> Callable[[str], Mapping | None]:
    """Read Risk's own durable delegate intent payload (``GatewayRiskDelegateHost`` storage)."""

    def read(request_key: str) -> Mapping | None:
        try:
            raw = storage.load("intent", request_key)
            if raw is None:
                return None
            value = json.loads(raw)
            payload = json.loads(value["canonical_payload"])
            return payload if type(payload) is dict else None
        except Exception:
            return None

    return read


__all__ = [
    "CONTROL_OPERATIONS",
    "DelegateOrderPort",
    "GatewayControlAdapter",
    "ObservationPump",
    "RiskAccountSnapshotSource",
    "RiskFactsCache",
    "RiskMarketFeed",
    "STRATEGY_FINANCIAL_OPERATION",
    "StrategyFinancialSource",
    "account_snapshot_from_observation",
    "map_delegate_result",
    "observed_event_from_account_event",
    "payload_reader",
    "price_fact_from_market_event",
    "risk_market_settings",
    "strategy_financials_from_reply",
]
