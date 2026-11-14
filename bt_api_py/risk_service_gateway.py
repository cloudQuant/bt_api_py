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

_STATES = frozenset({"RESERVED", "DISPATCHING", "SENT", "NOT_SENT", "UNKNOWN", "RELEASED", "EXPIRED"})
_DELEGATE_OUTCOMES = frozenset(
    {"OBSERVED", "QUEUED", "REQUEST_DENIED", "TRANSPORT_NOT_STARTED", "UNKNOWN"}
)
_ACTOR_OPEN_STATES = frozenset({"DISPATCHING", "SENT", "UNKNOWN"})
CONTROL_OPERATIONS = frozenset(
    {
        "direct.control.apply",
        "direct.control.status",
        "direct.control.list",
        "direct.control.cancel_scope",
        "direct.control.cancel_scope_status",
    }
)
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
        self._pid, self._thread = os.getpid(), threading.current_thread()

    def _owned(self) -> None:
        if os.getpid() != self._pid or threading.current_thread() is not self._thread:
            raise RuntimeError("RISK_DELEGATE_WRONG_OWNER")

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
        slot = self._slots.get(strategy_ref)
        return None if slot is None else slot.run_id

    def invalidate_registrations(self) -> None:
        """Retire old Gateway registrations lazily; never resend a command."""
        self._owned()
        for slot in self._slots.values():
            slot.invalid = True
            slot.invalid_generation += 1

    def _close_slot(self, slot: _DelegateSlot) -> bool:
        handle = slot.handle
        if handle is not None:
            try:
                snapshot = handle.close()
                if not runtime_cleanup_confirmed(snapshot):
                    slot.blocked = True
                    return False
            except BaseException as error:
                slot.blocked = True
                if not isinstance(error, Exception):
                    raise
                _LOG.warning("Risk delegate client close failed; cleanup uncertain")
                return False
            slot.handle = None
            slot.connection_attempted = False
            return True
        if slot.connection_attempted:
            slot.blocked = True
            return False
        return not slot.blocked

    def _handle(self, strategy_ref: str, run_id: str | None, *, reserve_owner: bool = False) -> Any:
        self._owned()
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
                if reserve_owner:
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
                if reserve_owner:
                    slot.blocked = True
                return None
            if runtime is not None:
                try:
                    state = runtime.status().state
                except Exception:
                    return None
                if state in ("failed", "closed", "close_failed", "stopping"):
                    invalid_generation = slot.invalid_generation
                    if not self._close_slot(slot):
                        if reserve_owner:
                            slot.blocked = True
                        return None
                    if slot.blocked or slot.invalid_generation != invalid_generation:
                        slot.blocked = True
                        return None
                    slot.failed_at = None
        if slot.handle is None:
            if slot.failed_at is not None and self._monotonic() - slot.failed_at < self._retry:
                return None
            try:
                slot.connection_attempted = True
                slot.handle = self._connect(self.route_for(strategy_ref, run_id))
                slot.failed_at = None
                if slot.handle is None:
                    slot.blocked = True
            except BaseException as error:
                # A generic connect failure does not prove that an attempted
                # registration was cleaned up, even on a read-only call.
                slot.blocked = True
                slot.handle = None
                if not isinstance(error, Exception):
                    raise
                try:
                    slot.failed_at = self._monotonic()
                except Exception:
                    slot.failed_at = None
                return None
        if slot.blocked or slot.invalid:
            if slot.invalid:
                slot.blocked = True
            return None
        if slot.handle is not None:
            try:
                client = slot.handle.client
                if (not callable(getattr(client, "reserve", None))
                        or not callable(getattr(slot.handle, "close", None))
                        or getattr(slot.handle, "host", None) is None):
                    slot.blocked = True
                    return None
            except Exception:
                slot.blocked = True
                return None
        return slot.handle

    @staticmethod
    def _no_route() -> Any:
        from bt_api_risk.pretrade import GatewayReply

        return GatewayReply("TRANSPORT_NOT_STARTED", code="ROUTE_UNAVAILABLE")

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
        if (not _ref(strategy_ref) or not _ref(run_id) or strategy_ref not in self._revisions
                or strategy_ref in self._reserving):
            return unavailable
        slot = self._slots.get(strategy_ref)
        if slot is not None and slot.run_id != run_id:
            return unavailable
        self._reserving.add(strategy_ref)
        try:
            handle = self._handle(strategy_ref, run_id, reserve_owner=True)
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
                if (not callable(getattr(client, "reserve", None))
                        or not callable(getattr(handle, "close", None))
                        or getattr(handle, "host", None) is None):
                    slot.blocked = True
                    return unavailable
            except Exception:
                slot.blocked = True
                return unavailable
            invalid_generation = slot.invalid_generation

            def same_slot() -> bool:
                return (self._slots.get(strategy_ref) is slot and slot.run_id == run_id
                        and not slot.blocked and not slot.invalid
                        and slot.invalid_generation == invalid_generation and slot.handle is handle)

            payload = json.loads(canonical_payload)
            request = decode_gateway_order_request(payload, self._account)
            instrument = self._instrument_for(request.exchange_id, request.symbol)
            if type(instrument) is not MarketInstrumentKey:
                raise ValueError("RISK_INSTRUMENT_UNMAPPED")
            # Reentrant callbacks may invalidate a slot while parsing/mapping.
            if not same_slot():
                if slot.invalid and slot.handle is not None:
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
            result = client.reserve(
                command, request, ttl_seconds=ttl_seconds, timeout_seconds=self._timeout
            )
            # The call may already have reached Gateway.  A callback that
            # invalidated or retired this slot makes its reply inconclusive.
            if not same_slot():
                if slot.invalid and slot.handle is not None:
                    slot.blocked = True
                return unavailable
            mapped = map_delegate_result(result)
            if not same_slot():
                if slot.invalid and slot.handle is not None:
                    slot.blocked = True
                return unavailable
            return mapped
        finally:
            self._reserving.discard(strategy_ref)

    def dispatch(
        self, *, strategy_ref: str, run_id: str | None, request_key: str, market_ref: dict | None
    ) -> Any:
        from bt_api_risk.pretrade import GatewayReply

        handle = self._handle(strategy_ref, run_id)
        if handle is None:
            return self._no_route()
        result = handle.client.dispatch(
            request_key, market_ref=market_ref, timeout_seconds=self._timeout
        )
        if getattr(result, "outcome", None) == "QUEUED":
            # The Gateway accepted the dispatch into its owner queue behind its own
            # dispatch marker (freeze 5, dispatch-3: QUEUED -> DISPATCHED).
            observation = handle.host.load_delegate_observation(request_key)
            return GatewayReply(
                "QUEUED",
                state="DISPATCHING",
                reservation_id=None if observation is None else observation.reservation_id,
                code=_code(getattr(result, "code", None)),
            )
        return map_delegate_result(result)

    def _actor_status(self, handle: Any, request_key: str) -> tuple[Any, Any]:
        result = handle.client.actor_status(request_key, timeout_seconds=self._timeout)
        observation = getattr(result, "observation", None)
        if getattr(result, "outcome", None) == "OBSERVED" and observation is not None:
            return result, _observation_reply(observation.command)
        return result, map_delegate_result(result)

    def status(self, *, strategy_ref: str, run_id: str | None, request_key: str) -> Any:
        handle = self._handle(strategy_ref, run_id)
        if handle is None:
            return self._no_route()
        return self._actor_status(handle, request_key)[1]

    def release(
        self, *, strategy_ref: str, run_id: str | None, request_key: str, operation_id: str
    ) -> Any:
        from bt_api_risk.pretrade import GatewayReply

        handle = self._handle(strategy_ref, run_id)
        if handle is None:
            return self._no_route()
        result, reply = self._actor_status(handle, request_key)
        if reply.outcome != "OBSERVED" or reply.state != "RESERVED":
            return reply
        released = handle.client.release_actor(
            result.observation, operation_id=operation_id, timeout_seconds=self._timeout
        )
        outcome = getattr(released, "outcome", None)
        if outcome == "OBSERVED" and getattr(released, "state", None) in ("RELEASED", "EXPIRED"):
            # The client stored the verified terminal observation; its generations prove it.
            return _observation_reply(handle.host.load_delegate_observation(request_key))
        if outcome in ("REQUEST_DENIED", "TRANSPORT_NOT_STARTED"):
            return GatewayReply(outcome, code=_code(getattr(released, "code", None)))
        return GatewayReply("UNKNOWN", code=_code(getattr(released, "code", None)))

    def cancel(
        self, *, strategy_ref: str, run_id: str | None, request_key: str, operation_id: str
    ) -> Any:
        from bt_api_risk.pretrade import GatewayReply

        handle = self._handle(strategy_ref, run_id)
        if handle is None:
            return self._no_route()
        result, reply = self._actor_status(handle, request_key)
        if reply.outcome != "OBSERVED" or reply.state not in _ACTOR_OPEN_STATES:
            return reply
        cancelled = handle.client.cancel_actor(
            result.observation, operation_id=operation_id, timeout_seconds=self._timeout
        )
        outcome = getattr(cancelled, "outcome", None)
        if outcome in ("QUEUED", "OBSERVED", "REQUEST_DENIED", "TRANSPORT_NOT_STARTED"):
            return GatewayReply(outcome, code=_code(getattr(cancelled, "code", None)))
        return GatewayReply("UNKNOWN", code=_code(getattr(cancelled, "code", None)))

    def ready(self) -> bool:
        return True  # per-run clients are created on demand; readiness is per call

    def close(self) -> bool:
        self._owned()
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
                if slot.blocked and slot.handle is None:
                    continue
                generation = slot.invalid_generation
                if self._close_slot(slot):
                    if slot.invalid_generation == generation:
                        del self._slots[strategy]
                    else:
                        slot.blocked = True
        finally:
            self._closing = False
        return not self._slots


# ============================================================================ control port


_CANCEL_TARGET_STATES = frozenset({"QUEUED", "SENT", "NOT_SENT", "UNKNOWN", "TERMINAL"})
_CANCEL_ATTEMPT_STATES = frozenset({"REQUESTED", "DISPATCHING", "SENT", "NOT_SENT", "UNKNOWN"})
_CANCEL_INSTRUMENT_SCOPE = re.compile(r"[A-Za-z0-9]{1,16}:[A-Za-z0-9][A-Za-z0-9._-]{0,63}\Z")


def _cancel_status_text(value: object) -> bool:
    # Gateway DirectControl's persisted identifier contract allows 512 characters.
    return type(value) is str and bool(value) and value == value.strip() and len(value) <= 512


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


class GatewayControlAdapter:
    """``GatewayControlPort`` over the Risk control principal's threaded runtime.

    Scope comes from the account-wide registration plus an optional contract
    (``"<venue>:<native_symbol>"``). A strategy-scope control needs a
    strategy-scoped registration this adapter does not hold; it is refused
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
    ) -> None:
        self._runtime = runtime
        self._mount, self._service_id = mount, service_id
        self._instance_id, self._scope_ref, self._account = instance_id, scope_ref, account_ref
        self._timeout = float(timeout_seconds)
        self._journal_id: str | None = None
        self.last_cancel_status: dict[str, Mapping] = {}

    def _registered(self, operation: str) -> bool:
        try:
            snapshot = self._runtime.status()
            return snapshot.state == "running" and snapshot.healthy is True and any(
                b.active is True
                and b.mount == self._mount
                and b.service_id == self._service_id
                and b.instance_id == self._instance_id
                and b.scope_ref == self._scope_ref
                and type(b.operations) is frozenset
                and operation in b.operations
                for b in snapshot.bindings
            )
        except Exception:
            return False

    def _call(self, operation: str, arguments: dict) -> tuple[Mapping | None, Any]:
        from bt_api_risk.pretrade import ControlReply

        if not self._registered(operation):
            return None, ControlReply("TRANSPORT_NOT_STARTED", code="ROUTE_UNAVAILABLE")
        try:
            raw = _detach_wire(
                self._runtime.call(
                    self._mount,
                    operation,
                    arguments,
                    account_ref=self._account,
                    scope_ref=self._scope_ref,
                    timeout_seconds=self._timeout,
                )
            )
        except Exception as error:
            outcome, code = runtime_failure(error, operation)
            return None, ControlReply(outcome, code=_code(code))
        if type(raw) is not dict:
            return None, ControlReply("UNKNOWN", code="INVALID_OR_UNAVAILABLE_REPLY")
        if raw.get("status") == "REQUEST_REJECTED":
            if set(raw) != {"status", "code"} or not _ref(raw["code"]):
                return None, ControlReply("UNKNOWN", code="INVALID_OR_UNAVAILABLE_REPLY")
            return None, ControlReply("REQUEST_DENIED", code=raw["code"])
        if raw == {"status": "UNKNOWN"} or raw.get("status") == "UNKNOWN":
            return None, ControlReply("UNKNOWN")
        return raw, None

    @staticmethod
    def _scope_arguments(scope_kind: str, scope_value: str) -> dict | None:
        if scope_kind == "account" and scope_value == "":
            return {}
        if scope_kind == "instrument" and _ref(scope_value) and scope_value.count(":") == 1:
            return {"instrument": scope_value}
        return None

    def apply(self, *, cause_id: str, mode: str, scope_kind: str, scope_value: str) -> Any:
        from bt_api_risk.pretrade import ControlReply

        extra = self._scope_arguments(scope_kind, scope_value)
        if extra is None:
            return ControlReply("REQUEST_DENIED", code="RISK_CONTROL_SCOPE_UNSUPPORTED")
        raw, error = self._call(
            "direct.control.apply", {"cause_id": cause_id, "mode": mode, **extra}
        )
        if error is not None:
            return error
        try:
            if raw.get("status") != "OBSERVED" or raw.get("cause_id") != cause_id:
                raise ValueError
            active = raw.get("active")
            active = bool(active) if active in (0, 1, True, False) else None
            version = raw.get("version")
            return ControlReply(
                "OBSERVED",
                cause_id=cause_id,
                active=active,
                version=version if type(version) is int and version >= 0 else None,
            )
        except Exception:
            return ControlReply("UNKNOWN", code="INVALID_OR_UNAVAILABLE_REPLY")

    def status(self, *, cause_id: str) -> Any:
        from bt_api_risk.pretrade import ControlReply

        raw, error = self._call("direct.control.status", {"cause_id": cause_id})
        if error is not None:
            return error
        try:
            if raw.get("status") == "NOT_FOUND" and raw.get("cause_id") == cause_id:
                # Not a refusal and not proof of anything: the cause is simply absent.
                return ControlReply("OBSERVED", cause_id=cause_id, active=False)
            cause = raw.get("cause")
            if raw.get("status") != "OBSERVED" or type(cause) is not dict:
                raise ValueError
            if cause.get("cause_id") != cause_id or cause.get("active") not in (0, 1, True, False):
                raise ValueError
            version = cause.get("version")
            return ControlReply(
                "OBSERVED",
                cause_id=cause_id,
                active=bool(cause["active"]),
                version=version if type(version) is int and version >= 0 else None,
            )
        except Exception:
            return ControlReply("UNKNOWN", code="INVALID_OR_UNAVAILABLE_REPLY")

    def cancel_scope(self, *, operation_id: str, scope_kind: str, scope_value: str) -> Any:
        from bt_api_risk.pretrade import ControlReply

        extra = self._scope_arguments(scope_kind, scope_value)
        if extra is None:
            return ControlReply("REQUEST_DENIED", code="RISK_CONTROL_SCOPE_UNSUPPORTED")
        raw, error = self._call(
            "direct.control.cancel_scope",
            {"operation_id": operation_id, "limit": _CANCEL_SCOPE_LIMIT, **extra},
        )
        if error is not None:
            return error
        if raw.get("status") != "OBSERVED" or raw.get("operation_id") != operation_id:
            return ControlReply("UNKNOWN", code="INVALID_OR_UNAVAILABLE_REPLY")
        self.last_cancel_status[operation_id] = raw
        return ControlReply("OBSERVED")

    def cancel_scope_status(self, operation_id: str, *, after: str = "", limit: int = 256) -> Mapping | None:
        """Read one current page; separate pages are not a consistent snapshot."""
        if not _ref(operation_id):
            return None
        self.last_cancel_status.pop(operation_id, None)
        if (type(after) is not str or (after and not _ref(after))
                or type(limit) is not int or not 1 <= limit <= _CANCEL_SCOPE_LIMIT):
            return None
        raw, error = self._call(
            "direct.control.cancel_scope_status",
            {"operation_id": operation_id, "after": after, "limit": limit},
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
        self._market_refs: dict[str, dict] = {}
        self._account: Any = None
        self._account_received_ns: int | None = None
        self._market_days: dict[str, str] = {}
        self._event_day: str | None = None

    def note_tick(self, instrument_id: str, price: Any, market_ref: dict | None, trading_day: str | None) -> None:
        if type(instrument_id) is not str:
            return
        # A failed reference snapshot must not retain the preceding tick's ref.
        self._market_refs.pop(instrument_id, None)
        self._prices[instrument_id] = price
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
        self._account = snapshot
        self._account_received_ns = self._now_ns()

    def set_strategy_financials(self, facts: Mapping[str, Any]) -> None:
        """Replace every strategy financial fact at once; an absent strategy is unknown."""
        self._financials = dict(facts)
        self._financial_received_ns = self._now_ns()

    def now_ns(self) -> int:
        return self._now_ns()

    def forget_market(self) -> None:
        self._prices.clear()
        self._market_refs.clear()
        self._market_days.clear()

    # FactsPort -----------------------------------------------------------------

    def reference_price(self, instrument: Any) -> Any:
        from bt_api_risk.pretrade import PriceFact

        if type(instrument) is not MarketInstrumentKey:
            return None
        price = self._prices.get(instrument.instrument_id)
        if type(price) is not PriceFact or price.instrument != instrument:
            return None
        return price

    def market_ref(self, instrument: Any) -> dict | None:
        if self.reference_price(instrument) is None:
            return None
        value = self._market_refs.get(instrument.instrument_id)
        if value is None:
            return None
        snapshot = _market_ref_snapshot(value)
        if snapshot is None:
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
            name: GatewayMarketSubscriptionSettings(key, require_source_descriptor=True)
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
        self.failures = 0
        self.attestation_failures = 0

    def start(self) -> None:
        for client, _key, _instrument in self._subscriptions.values():
            client.start(timeout_seconds=self._timeout)

    def _attested(self, client: Any, received: Any) -> bool:
        receipt = getattr(received, "receipt", None)
        if receipt is None or getattr(receipt, "source", None) != "pub":
            return False  # a replayed or unreceipted frame cannot be attested
        try:
            client.clock_observation(receipt=receipt, timeout_seconds=self._timeout)
        except Exception:
            return False
        return True

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
            latest = None
            while consumed < self._max:
                try:
                    received = client.poll_with_receipt(timeout_seconds=self._timeout)
                except Exception:
                    # A broken subscription leaves no usable reference: refuse rather than reuse.
                    self.failures += 1
                    self._facts.note_tick(instrument_id, None, None, None)
                    latest = None
                    break
                if received is None:
                    break
                consumed += 1
                try:
                    mapped = price_fact_from_market_event(received, key)
                except Exception:
                    self.failures += 1
                    self._facts.note_tick(instrument_id, None, None, None)
                    latest = None
                    continue
                latest = (received, mapped)
                count += 1
            if latest is None:
                continue
            received, (fact, ref, day) = latest
            # The attestation binds the client's last issued frame: this one.
            if not self._attested(client, received):
                self.attestation_failures += 1
                ref = None
            self._facts.note_tick(instrument_id, fact, ref, day)
        return count


# ============================================================================ account snapshot


def account_snapshot_from_observation(
    observation: Any,
    *,
    instrument_ids: Mapping[tuple[str, str], str],
    dated_venues: frozenset[str],
) -> Any:
    """Map a typed ``AccountObservation`` to the Risk ``AccountSnapshot``.

    Fields the observation does not carry (trading day, margin, profits,
    commission) stay None (unknown). A net-side row, or a dated-venue row
    without its today/yesterday split, makes the snapshot incomplete.
    """
    from bt_api_risk.pretrade import AccountSnapshot

    # The observation alone has no source-bound expiry in the Risk UTC clock
    # domain. Keep the data for diagnostics, but it cannot establish a usable
    # position or financial fact until the source lease is bound end to end.
    complete = False
    positions: dict[tuple[str, str], tuple[Decimal, Decimal]] = {}
    for row in observation.positions:
        side = {"long": "LONG", "short": "SHORT"}.get(row.side)
        quantity = _decimal(row.quantity, positive=False)
        instrument = row.instrument
        instrument_id = instrument_ids.get((instrument.venue, instrument.native_symbol))
        if instrument_id is None:
            instrument_id = instrument.instrument_id
        if side is None or quantity is None:
            complete = False
            continue
        today = _decimal(row.today_quantity, positive=False)
        yesterday = _decimal(row.yesterday_quantity, positive=False)
        if today is None or yesterday is None:
            if instrument.venue in dated_venues:
                complete = False
                continue
            today, yesterday = quantity, Decimal(0)
        old = positions.get((instrument_id, side), (Decimal(0), Decimal(0)))
        positions[(instrument_id, side)] = (old[0] + today, old[1] + yesterday)
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

    def __init__(self, client: Any, facts: RiskFactsCache, *, instrument_ids: Mapping[tuple[str, str], str], timeout_seconds: float) -> None:
        self._client, self._facts = client, facts
        self._instrument_ids = dict(instrument_ids)
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
        except Exception:
            self.failures += 1
            return False
        self._facts.set_account(snapshot)
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
    Economic fields the event lacks are taken only from Risk's own durable
    delegate intent of the same request key; otherwise the event is refused, except
    that a trade's missing order quantity is bounded below by its trade quantity.
    """
    from bt_api_risk.pretrade import ObservedExecutionEvent

    event = item.event
    observation = event.observation
    ours = event.command_principal == risk_principal
    origin = ("risk:" + risk_principal) if ours else "self"
    kind = observation.kind.value
    payload = intent_payload(event.request_key) if ours else None

    def from_payload(name: str) -> Any:
        return None if payload is None else payload.get(name)

    side = observation.side or from_payload("side")
    offset = observation.offset or from_payload("offset")
    quantity = _decimal(observation.order_quantity, positive=True) or _decimal(
        from_payload("quantity"), positive=True
    )
    limit_price = _decimal(observation.limit_price, positive=True) or _decimal(
        from_payload("price"), positive=True
    )
    filled = _decimal(observation.filled_quantity, positive=False)
    if observation.status is not None:
        status = observation.status.value
    elif kind == "trade":
        status = "partially_filled"
    elif kind == "order_error":
        status = "rejected"
    else:
        status = "accepted"  # a cancel error leaves the order as it was: still working
    trade_quantity = _decimal(observation.trade_quantity, positive=True)
    if quantity is None and kind == "trade":
        # A trade return carries no order quantity, and an order Risk did not place has
        # no Risk intent (a bypass or external order, INV64-10). The trade quantity is a
        # lower bound of the order quantity; the engine keeps the largest order quantity
        # any event of the order reports. Refusing the event instead would leave the
        # projection UNVERIFIED for good: every replay meets the same event again.
        quantity = trade_quantity
    if filled is None:
        filled = Decimal(0) if trade_quantity is None or quantity is None else min(trade_quantity, quantity)
    return ObservedExecutionEvent(
        epoch=epoch,
        sequence=event.sequence - 1,
        kind=kind,
        strategy_ref=item.strategy_scope,
        origin=origin,
        request_key=event.request_key,
        reservation_id=event.reservation_id,
        instrument=observation.instrument,
        side=str(side).upper() if side is not None else "",
        offset=None if offset is None else str(offset).upper(),
        status=status,
        quantity=quantity,  # type: ignore[arg-type]
        filled_quantity=filled,
        limit_price=limit_price,
        trade_id=observation.trade_key_digest if kind == "trade" else None,
        trade_price=_decimal(observation.trade_price, positive=True) if kind == "trade" else None,
        trade_quantity=trade_quantity if kind == "trade" else None,
        trading_day=_day(observation.trading_day),
        order_ref=observation.venue_key_digest,
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
                        ack = observer.acknowledge(
                            sequence, expected_epoch=epoch, timeout_seconds=self._timeout
                        )
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
