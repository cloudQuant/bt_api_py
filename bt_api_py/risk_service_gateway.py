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
import threading
import time
from collections.abc import Callable, Mapping
from dataclasses import dataclass, replace
from datetime import UTC, datetime
from decimal import Decimal, InvalidOperation
from typing import Any

from bt_api_base.gateway_delegation_protocol import GatewayDelegatedCommand
from bt_api_base.gateway_direct_protocol import gateway_payload_digest
from bt_api_base.market_protocol import MarketInstrumentKey, MarketSubscriptionKey
from bt_api_base.service_protocol import WireIdentity, WireScope

from bt_api_py.gateway_delegate_service_client import runtime_failure
from bt_api_py.gateway_execution_stream_client import _detach_wire

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
    return value if type(value) is str and len(value) == 8 and value.isdigit() else None


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
        slot = self._slots.get(strategy_ref)
        if slot is not None and slot.run_id != run_id:
            self._close_slot(slot)
            slot = None
        if slot is None:
            self._slots[strategy_ref] = _DelegateSlot(run_id)

    def current_run(self, strategy_ref: str) -> str | None:
        slot = self._slots.get(strategy_ref)
        return None if slot is None else slot.run_id

    def _close_slot(self, slot: _DelegateSlot) -> None:
        handle, slot.handle = slot.handle, None
        if handle is not None:
            try:
                handle.close()
            except Exception:
                # A cleanup uncertainty never becomes an order outcome.
                _LOG.warning("Risk delegate client close failed; cleanup uncertain")

    def _handle(self, strategy_ref: str, run_id: str | None) -> Any:
        self._owned()
        if not _ref(run_id) or strategy_ref not in self._revisions:
            return None
        slot = self._slots.get(strategy_ref)
        if slot is None:
            # Recovery before the strategy called: adopt the permit's run.
            slot = self._slots[strategy_ref] = _DelegateSlot(run_id)  # type: ignore[arg-type]
        if slot.run_id != run_id:
            return None  # a superseded run: the Gateway no longer registers it
        if slot.handle is None:
            if slot.failed_at is not None and self._monotonic() - slot.failed_at < self._retry:
                return None
            try:
                slot.handle = self._connect(self.route_for(strategy_ref, run_id))
                slot.failed_at = None
            except Exception:
                slot.handle, slot.failed_at = None, self._monotonic()
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
        from bt_api_py.gateway_order_contract import decode_gateway_order_request

        handle = self._handle(strategy_ref, run_id)
        if handle is None:
            return self._no_route()
        payload = json.loads(canonical_payload)
        request = decode_gateway_order_request(payload, self._account)
        instrument = self._instrument_for(request.exchange_id, request.symbol)
        if type(instrument) is not MarketInstrumentKey:
            raise ValueError("RISK_INSTRUMENT_UNMAPPED")
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
        result = handle.client.reserve(
            command, request, ttl_seconds=ttl_seconds, timeout_seconds=self._timeout
        )
        return map_delegate_result(result)

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

    def close(self) -> None:
        for slot in list(self._slots.values()):
            self._close_slot(slot)
        self._slots.clear()


# ============================================================================ control port


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

    def cancel_scope_status(self, operation_id: str) -> Mapping | None:
        """Latest Gateway view of one cancel operation (first page), or None."""
        raw, error = self._call(
            "direct.control.cancel_scope_status",
            {"operation_id": operation_id, "after": "", "limit": _CANCEL_SCOPE_LIMIT},
        )
        if error is None and raw.get("status") in ("OBSERVED", "NOT_FOUND"):
            self.last_cancel_status[operation_id] = raw
        return self.last_cancel_status.get(operation_id)


# ============================================================================ facts


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
        self._market_day: str | None = None
        self._event_day: str | None = None

    def note_tick(self, instrument_id: str, price: Any, market_ref: dict | None, trading_day: str | None) -> None:
        self._prices[instrument_id] = price
        if market_ref is None:
            self._market_refs.pop(instrument_id, None)
        else:
            self._market_refs[instrument_id] = dict(market_ref)
        day = _day(trading_day)
        if day is not None and (self._market_day is None or day > self._market_day):
            self._market_day = day

    def note_event_day(self, trading_day: str | None) -> None:
        day = _day(trading_day)
        if day is not None and (self._event_day is None or day > self._event_day):
            self._event_day = day

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

    # FactsPort -----------------------------------------------------------------

    def reference_price(self, instrument: Any) -> Any:
        return self._prices.get(getattr(instrument, "instrument_id", None))

    def market_ref(self, instrument: Any) -> dict | None:
        value = self._market_refs.get(getattr(instrument, "instrument_id", None))
        return None if value is None else json.loads(json.dumps(value))

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
        if self._account_max_age is not None and (
            self._account_received_ns is None
            or self._now_ns() - self._account_received_ns > self._account_max_age
        ):
            return replace(snapshot, complete=False)
        return snapshot

    def strategy_financial(self, strategy_ref: str) -> Any:
        fact = self._financials.get(strategy_ref)
        if fact is None:
            return None  # not read, not granted or UNAVAILABLE: unknown
        if self._financial_max_age is None or (
            self._financial_received_ns is None
            or self._now_ns() - self._financial_received_ns > self._financial_max_age
        ):
            return replace(fact, complete=False)
        return fact

    def margin_rate(self, instrument: Any) -> Any:
        return None  # no Gateway-bound margin-rate source for the Risk principal yet

    def trading_day(self) -> str | None:
        days = [day for day in (self._market_day, self._event_day) if day is not None]
        return max(days) if days else None


def price_fact_from_market_event(received: Any, key: MarketSubscriptionKey) -> tuple[Any, dict, str | None]:
    """Map one received market event of the Risk subscription to ``(PriceFact, market_ref, day)``.

    The market reference is taken from the event header of this principal's
    own subscription (G64-10); a tick of another instrument or key is refused.
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
    return fact, market_ref, _day(tick.trading_date)


class RiskMarketFeed:
    """Drain the Risk principal's own market subscriptions into the facts cache."""

    def __init__(self, subscriptions: Mapping[str, tuple[Any, MarketSubscriptionKey, str]], facts: RiskFactsCache, *, timeout_seconds: float, max_events: int = 1024) -> None:
        self._subscriptions = dict(subscriptions)  # name -> (client, key, instrument_id)
        self._facts = facts
        self._timeout = float(timeout_seconds)
        self._max = max_events
        self.failures = 0

    def start(self) -> None:
        for client, _key, _instrument in self._subscriptions.values():
            client.start(timeout_seconds=self._timeout)

    def poll(self) -> int:
        count = 0
        for client, key, instrument_id in self._subscriptions.values():
            while count < self._max:
                try:
                    received = client.poll_with_receipt(timeout_seconds=self._timeout)
                except Exception:
                    # A broken subscription leaves no usable reference: refuse rather than reuse.
                    self.failures += 1
                    self._facts.note_tick(instrument_id, None, None, None)
                    break
                if received is None:
                    break
                try:
                    fact, ref, day = price_fact_from_market_event(received, key)
                except Exception:
                    self.failures += 1
                    continue
                self._facts.note_tick(instrument_id, fact, ref, day)
                count += 1
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

    complete = (
        observation.positions_complete is True and observation.balances_complete is True
    )
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
        available = _decimal(valuation.available_cash, positive=False) or available
        equity = _decimal(valuation.equity, positive=False)
        currency = valuation.currency
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
    unknown, which makes it unusable) and ``day_start_value`` stays None, so the
    engine keeps its own day-start mark (a service started mid-day starts its
    mark at its first read: known R-07 limitation). A reply that is not
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
    complete = raw["complete"] is True and _day(trading_day) is not None
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
                as_of_ns=now_ns,
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
    delegate intent of the same request key; otherwise the event is refused.
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
        self._after = 0
        self._needs_reset = True
        self._restarted = False
        self.last_code: str | None = None
        self.acknowledged = 0

    @property
    def verified_epoch(self) -> str | None:
        return None if self._needs_reset else self._epoch

    def _gap(self, reason: str) -> None:
        self.last_code = reason
        if not self._needs_reset:
            try:
                self._engine.mark_observation_gap(reason)
            except Exception:
                # The next poll replays from 0 anyway; storage faults surface on apply.
                _LOG.warning("Risk observation gap could not be recorded: %s", reason)
        self._needs_reset = True

    def _reset(self) -> bool:
        progress = self._observer.progress(timeout_seconds=self._timeout)
        if progress.outcome != "OBSERVED" or progress.progress is None:
            self.last_code = progress.code or progress.outcome
            return False
        current = progress.progress
        # Head: the consumer has been offered at least this many events (lower bound).
        self._engine.reset_observations(current.epoch, current.offered_sequence - 1)
        self._epoch, self._after, self._needs_reset = current.epoch, 0, False
        return True

    def poll(self) -> int:
        """Read and apply until the stream has no more events; return events applied."""
        if not self._restarted:
            # The ledger cursor survives a restart; until this process has replayed the
            # stream from 0, the projection it describes is not verified (freeze 4.2).
            self._engine.mark_observation_gap("observation_restart")
            self._restarted = True
        if self._needs_reset and not self._reset():
            return 0
        applied = 0
        while True:
            result = self._observer.read_after(
                after_sequence=self._after,
                expected_epoch=self._epoch,
                limit=self._limit,
                timeout_seconds=self._timeout,
            )
            if result.outcome != "OBSERVED" or result.page is None:
                if result.outcome == "REQUEST_DENIED" and result.code in _REPLAY_CODES:
                    self._gap("observation_replay_required")
                else:
                    self._gap("observation_read_failed")
                return applied
            page = result.page
            if page.progress.epoch != self._epoch:
                self._gap("epoch_changed")
                return applied
            mapped = []
            broken = False
            for item in page.events:
                try:
                    mapped.append(
                        observed_event_from_account_event(
                            item,
                            epoch=self._epoch,
                            risk_principal=self._principal,
                            intent_payload=self._intent_payload,
                        )
                    )
                except Exception:
                    broken = True
                    break
            if mapped:
                last = self._engine.apply_observations(mapped)  # durable, one transaction
                for event in mapped:
                    if event.sequence <= last:
                        self._facts.note_event_day(event.trading_day)
                if last >= 0:
                    sequence = last + 1
                    ack = self._observer.acknowledge(
                        sequence, expected_epoch=self._epoch, timeout_seconds=self._timeout
                    )
                    if ack.outcome == "OBSERVED":
                        self.acknowledged = max(self.acknowledged, sequence)
                    self._after = max(self._after, sequence)
                applied += len(mapped)
            if broken:
                self._gap("observation_unmappable")
                return applied
            if not page.has_more or not page.events:
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
    "strategy_financials_from_reply",
]
