"""Explicit Gateway strategy facade over a caller-supplied session.

Returned clients retain their own runtime and ACL checks. Runtime health is not
account readiness or a grant to trade.

With a Risk client (the strategy's configuration selected Risk, D64-01) every
PLACE (reserve, dispatch, release of an unsent reservation) goes to the Risk
service and never to the Gateway directly, also when Risk is unavailable
(D64-02). Status and cancel of an order placed through Risk use this
strategy's delegated Gateway operations and never depend on Risk.
"""

from __future__ import annotations

import os
import threading
from dataclasses import dataclass
from typing import TYPE_CHECKING

from bt_api_py.gateway_strategy_client import GatewayStrategyClientSession
from bt_api_py.service_runtime_thread import _timeout

if TYPE_CHECKING:
    from bt_api_py._contracts import OrderRequest

_NO_ROUTE = object()


@dataclass(frozen=True, slots=True)
class GatewayRiskPermitRecord:
    """Risk's answer for one request key, shaped like the record fields BT reads."""

    request_key: str
    state: str
    permit_id: str | None
    reservation_id: str | None
    view_digest: str | None
    digest: str | None


@dataclass(frozen=True, slots=True)
class GatewayRiskRoutedResult:
    """Result of an operation routed through Risk, in the facade's outcome vocabulary.

    ``code`` is upper-case (a Gateway code, ``RISK_<CODE>`` or a client code).
    ``risk_code`` is the lowercase Risk code of a refusal on the Risk path
    (including ``risk_unavailable`` and ``risk_gateway_refused``);
    ``risk_denied`` is set only when the refusal is Risk's own.
    """

    outcome: str
    record: object | None = None
    code: str | None = None
    marker_id: str | None = None
    risk_code: str | None = None
    risk_denied: str | None = None
    risk_answer: object | None = None


def _risk_upper(code):
    return "RISK_" + code.removeprefix("risk_").upper()


class GatewayBtApi:
    """Expose selected Gateway operations without constructing a provider."""

    def __init__(
        self, session, *, exchange_name: str, owns_session: bool = False, risk_client=None
    ):
        if type(session) is not GatewayStrategyClientSession:
            raise TypeError("INVALID_GATEWAY_SESSION")
        if (
            type(exchange_name) is not str
            or not exchange_name
            or exchange_name.strip() != exchange_name
        ):
            raise ValueError("INVALID_EXCHANGE_NAME")
        if type(owns_session) is not bool:
            raise TypeError("INVALID_SESSION_OWNERSHIP")
        self._session = session
        self._exchange_name = exchange_name
        self._owns_session = owns_session
        self._pid = os.getpid()
        self._lock = threading.Lock()
        self._state = "open"
        self._binding_object, self._route_object = session._binding, session._route
        self._binding, self._route = session.binding, session.route
        self._runtime = session.runtime
        self._command = session.command_client
        self._stream = session.execution_stream_client
        self._position = session.position_client
        self._delegated = session.delegated_client
        self._financial = session.financial_client
        self._delegated_cancel_host = self._delegated._cancel_host
        self._account_observer = session.account_observer
        self._account_facts = session.account_facts
        self._host, self._cancel_host = self._command._host, self._command._cancel_host
        self._account = session.account_client
        self._market = session.market_client
        self._instrument = session.instruments
        self._subscriptions = dict(self._market._clients) if self._market is not None else {}
        if risk_client is not None:
            from bt_api_py.risk_service_client import RiskServiceClient

            if type(risk_client) is not RiskServiceClient:
                raise TypeError("INVALID_RISK_CLIENT")
            risk_route = risk_client.route
            if (
                risk_route.scope != self._route.scope
                or risk_route.identity.actor_ref != self._binding.strategy_scope
            ):
                raise ValueError("RISK_ROUTE_MISMATCH")
        # The facade owns its Risk client: start and close follow the facade.
        self._risk = risk_client
        self._risk_permits = {}  # request key -> last RESERVED/DISPATCHED Risk answer
        self._risk_keys = set()  # keys this facade sent to Risk (placed through Risk)
        self._risk_start_code = None
        self._risk_close_failures = 0
        self._check_binding()

    def _check_process(self):
        if os.getpid() != self._pid:
            raise RuntimeError("WRONG_PROCESS")

    def _check_binding(self):
        self._check_process()
        session = self._session
        if (
            session._binding is not self._binding_object
            or session._route is not self._route_object
            or session.binding != self._binding
            or session.route != self._route
            or session.runtime is not self._runtime
            or session.command_client is not self._command
            or session.execution_stream_client is not self._stream
            or session.position_client is not self._position
            or session.delegated_client is not self._delegated
            or session.financial_client is not self._financial
            or session.account_observer is not self._account_observer
            or session.account_facts is not self._account_facts
            or session.account_client is not self._account
            or session.market_client is not self._market
            or session.instruments is not self._instrument
        ):
            raise RuntimeError("GATEWAY_BINDING_CHANGED")
        for client in (self._command, self._stream, self._position, self._delegated, self._financial):
            if (
                client._runtime is not self._runtime
                or client._binding is not self._binding_object
                or client._route is not self._route_object
                or client._binding != self._binding
                or client._route != self._route
            ):
                raise RuntimeError("GATEWAY_BINDING_CHANGED")
        if (
            self._command._host is not self._host
            or self._command._cancel_host is not self._cancel_host
            or self._delegated._cancel_host is not self._delegated_cancel_host
        ):
            raise RuntimeError("GATEWAY_BINDING_CHANGED")
        for client in (self._account, self._instrument, *self._subscriptions.values()):
            if client is not None and client._runtime is not self._runtime:
                raise RuntimeError("GATEWAY_BINDING_CHANGED")
        if self._account_observer is not None and (
            self._account_observer._runtime is not self._runtime
            or self._account_observer._route.mount != 'store.account'
            or self._account_observer._route.service_id != self._route.service_id
            or self._account_observer._route.instance_id != self._route.instance_id
            or self._account_observer._route.scope_ref != self._route.scope_ref
            or self._account_observer._route.scope != self._route.scope
            or self._account_observer._route.identity != self._route.identity
        ):
            raise RuntimeError("GATEWAY_BINDING_CHANGED")
        if self._account_facts is not None and (
            self._account_facts._runtime is not self._runtime
            or self._account_facts._route.mount != 'store.account'
            or self._account_facts._route.service_id != self._route.service_id
            or self._account_facts._route.instance_id != self._route.instance_id
            or self._account_facts._route.scope_ref != self._route.scope_ref
            or self._account_facts._route.scope != self._route.scope
            or self._account_facts._route.identity != self._route.identity
        ):
            raise RuntimeError("GATEWAY_BINDING_CHANGED")
        if any(client._name != name for name, client in self._subscriptions.items()):
            raise RuntimeError("GATEWAY_BINDING_CHANGED")
        if self._market is not None and (
            self._market._clients.keys() != self._subscriptions.keys()
            or any(
                self._market._clients[name] is not client
                for name, client in self._subscriptions.items()
            )
        ):
            raise RuntimeError("GATEWAY_BINDING_CHANGED")

    def _check(self, exchange_name=_NO_ROUTE, *, allow_closing=False, allow_closed=False):
        self._check_binding()
        if exchange_name is not _NO_ROUTE and (
            type(exchange_name) is not str or exchange_name != self._exchange_name
        ):
            raise ValueError("GATEWAY_ROUTE_MISMATCH")
        with self._lock:
            if self._state == "closed" and not allow_closed:
                raise RuntimeError("GATEWAY_FACADE_CLOSED")
            if self._state == "closing" and not allow_closing:
                raise RuntimeError("GATEWAY_FACADE_CLOSING")

    def list_exchanges(self):
        self._check()
        return [self._exchange_name]

    def start(self, *, timeout_seconds=5.0):
        self._check()
        snapshot = self._session.start(timeout_seconds=timeout_seconds)
        if self._risk is not None:
            # D64-02: an unreachable Risk service refuses PLACE only; cancels,
            # status and returns still run through the Gateway session.
            try:
                self._risk.start(timeout_seconds=timeout_seconds)
            except Exception as error:
                code = getattr(error, "code", None)
                self._risk_start_code = code if type(code) is str else type(error).__name__
        return snapshot

    @property
    def risk_selected(self):
        """Whether this strategy's configuration routes PLACE through Risk."""
        return self._risk is not None

    @property
    def risk_available(self):
        """Local view of the Risk route (no RPC); None when Risk is not selected."""
        self._check_process()
        if self._risk is None:
            return None
        return self._risk.available

    def status(self):
        """Return the original runtime snapshot, including cleanup uncertainty."""
        self._check_process()
        return self._runtime.status()

    @property
    def lifecycle_state(self):
        """Report facade state separately from the session runtime snapshot."""
        self._check_process()
        with self._lock:
            return self._state

    def close(self, *, timeout_seconds=5.0):
        """Close this facade; a borrowed session and its host remain open.

        Return the unmodified runtime snapshot. Runtime closure does not imply
        clean unregister, account readiness, or permission to trade.
        """
        self._check_process()
        _timeout(timeout_seconds)
        with self._lock:
            already_closed = self._state == "closed"
            if not already_closed:
                self._state = "closing" if self._owns_session else "closed"
        if not already_closed and self._risk is not None:
            try:
                self._risk.close(timeout_seconds=timeout_seconds)
            except Exception:
                # Counted and retried by the next close; the Gateway session
                # closes regardless and a second Risk run would be refused.
                self._risk_close_failures += 1
        if already_closed or not self._owns_session:
            return self._runtime.status()
        # No facade lock is held while the original runtime waits for cleanup.
        result = self._runtime.close(timeout_seconds=timeout_seconds)
        if result.state == "closed" and not result.thread_alive:
            with self._lock:
                self._state = "closed"
        return result

    def reserve_order(
        self, exchange_name, request: OrderRequest, *, ttl_seconds=10.0, timeout_seconds=5.0
    ):
        self._check(exchange_name)
        if self._risk is None:
            return self._command.reserve(
                request, ttl_seconds=ttl_seconds, timeout_seconds=timeout_seconds
            )
        return self._risk_reserve(request, ttl_seconds, timeout_seconds)

    def dispatch_order(self, exchange_name, request_key, *, market_ref=None, timeout_seconds=5.0):
        self._check(exchange_name)
        if self._risk is not None:
            return self._risk_dispatch(request_key, market_ref, timeout_seconds)
        if market_ref is None:
            return self._command.dispatch(request_key, timeout_seconds=timeout_seconds)
        return self._command.dispatch(request_key, market_ref=market_ref, timeout_seconds=timeout_seconds)

    def command_status(self, exchange_name, request_key, *, timeout_seconds=5.0):
        self._check(exchange_name)
        if self._risk is None or self._own_command(request_key):
            return self._command.status(request_key, timeout_seconds=timeout_seconds)
        if request_key not in self._risk_keys:
            # Provenance unknown (for example after a restart): the delegated
            # command is asked first, then the strategy's own command.
            delegated = self._delegated_status(request_key, timeout_seconds)
            if delegated.outcome == "OBSERVED":
                return delegated
            own = self._command.status(request_key, timeout_seconds=timeout_seconds)
            if own.outcome == "OBSERVED":
                return own
            # Never pass NOT_FOUND on: a command placed through Risk may exist
            # while its delegated observation is unavailable.
            return GatewayRiskRoutedResult("UNKNOWN", code="COMMAND_UNAVAILABLE")
        return self._delegated_status(request_key, timeout_seconds)

    # --- Risk routing (D64-01, D64-02); used only when a Risk client is configured ---

    def _own_command(self, request_key):
        """Whether the strategy itself reserved this key at the Gateway (own record)."""
        try:
            return self._host.load_record(request_key) is not None
        except Exception:
            return False

    def _delegated_status(self, request_key, timeout_seconds):
        try:
            result = self._delegated.status(request_key, timeout_seconds=timeout_seconds)
        except Exception:
            return GatewayRiskRoutedResult("UNKNOWN", code="DELEGATED_COMMAND_UNAVAILABLE")
        if result.outcome == "OBSERVED" and result.observation is not None:
            return GatewayRiskRoutedResult("OBSERVED", record=result.observation)
        return GatewayRiskRoutedResult("UNKNOWN", code=result.code or "DELEGATED_COMMAND_UNAVAILABLE")

    def _risk_refusal(self, answer):
        if answer.code == "risk_gateway_refused":
            # The Gateway refused; its own code keeps the existing projection.
            return GatewayRiskRoutedResult(
                "REQUEST_DENIED", code=answer.gateway_code, risk_code=answer.code,
                risk_answer=answer,
            )
        return GatewayRiskRoutedResult(
            "REQUEST_DENIED", code=_risk_upper(answer.code), risk_code=answer.code,
            risk_denied=answer.code, risk_answer=answer,
        )

    @staticmethod
    def _risk_not_sent(call):
        if call.code == "INVALID_REQUEST":
            return GatewayRiskRoutedResult(
                "REQUEST_DENIED", code="RISK_REQUEST_INVALID", risk_code="risk_request_invalid",
                risk_denied="risk_request_invalid",
            )
        # D64-02: Risk selected but unavailable refuses the PLACE; it never
        # falls back to the Gateway. The request provably never left.
        return GatewayRiskRoutedResult(
            "REQUEST_DENIED", code="RISK_UNAVAILABLE", risk_code="risk_unavailable",
            risk_denied="risk_unavailable",
        )

    def _risk_reserve(self, request, ttl_seconds, timeout_seconds):
        from bt_api_py.gateway_direct_client import build_gateway_order_intent

        # The same durable intent as direct.reserve; nothing is sent without it.
        try:
            candidate = build_gateway_order_intent(
                self._binding_object, self._route_object, request, ttl_seconds=ttl_seconds
            )
            key = candidate.request_key
            existing = self._host.load_intent(key)
            if existing is None:
                self._host.save_intent(candidate)
                existing = self._host.load_intent(key)
            if (
                existing is None
                or existing.binding != candidate.binding
                or existing.route != candidate.route
                or existing.request_key != key
                or existing.canonical_payload != candidate.canonical_payload
                or existing.digest != candidate.digest
            ):
                raise ValueError("INTENT_CONFLICT")
        except Exception:
            return GatewayRiskRoutedResult("TRANSPORT_NOT_STARTED", code="INTENT_NOT_DURABLE")
        self._risk_reconnect(timeout_seconds)
        call = self._risk.reserve(request, request_key=key, timeout_seconds=timeout_seconds)
        if call.outcome == "NOT_SENT":
            return self._risk_not_sent(call)
        self._risk_keys.add(key)
        if call.outcome != "ANSWERED":
            return GatewayRiskRoutedResult("UNKNOWN", code="RISK_OUTCOME_UNKNOWN")
        answer = call.answer
        if answer.outcome == "DENIED":
            return self._risk_refusal(answer)
        if answer.outcome == "RESERVED":
            self._risk_permits[key] = answer
            return GatewayRiskRoutedResult(
                "OBSERVED",
                record=GatewayRiskPermitRecord(
                    key, "RESERVED", answer.permit_id, answer.reservation_id,
                    answer.view_digest, candidate.digest,
                ),
                risk_answer=answer,
            )
        # A replayed key answers its durable state; the caller asks by status.
        return GatewayRiskRoutedResult("UNKNOWN", code="RISK_OUTCOME_UNKNOWN", risk_answer=answer)

    def _risk_reconnect(self, timeout_seconds):
        # D64-02: a lost Risk route refuses PLACE now and is re-registered on a
        # later attempt (bounded backoff); nothing in flight is sent again.
        if not self._risk.available:
            self._risk.reconnect(timeout_seconds=timeout_seconds)

    def risk_order_status(self, exchange_name, request_key, *, timeout_seconds=5.0):
        """Risk's record for one key (risk.order.status); NOT_FOUND proves nothing."""
        self._check(exchange_name)
        if self._risk is None:
            raise RuntimeError("RISK_NOT_SELECTED")
        self._risk_reconnect(timeout_seconds)
        call = self._risk.status(request_key, timeout_seconds=timeout_seconds)
        if call.outcome == "ANSWERED" and call.answer.outcome != "NOT_FOUND":
            # Known to Risk: status and cancel use the delegated path. A permit
            # read back here is never dispatched (only one this facade received).
            self._risk_keys.add(request_key)
        return call

    def _risk_permit(self, request_key, timeout_seconds):
        permit = self._risk_permits.get(request_key)
        if permit is not None:
            return permit, None
        self._risk_reconnect(timeout_seconds)
        call = self._risk.status(request_key, timeout_seconds=timeout_seconds)
        if call.outcome == "NOT_SENT":
            return None, self._risk_not_sent(call)
        if (
            call.outcome != "ANSWERED"
            or call.answer.outcome not in ("RESERVED", "DISPATCHED")
            or call.answer.permit_id is None
        ):
            return None, GatewayRiskRoutedResult(
                "TRANSPORT_NOT_STARTED", code="RISK_PERMIT_UNAVAILABLE"
            )
        return call.answer, None

    def _risk_dispatch(self, request_key, market_ref, timeout_seconds):
        permit = self._risk_permits.get(request_key)
        if permit is None or permit.outcome != "RESERVED":
            # Only a reservation this facade received may be dispatched.
            return GatewayRiskRoutedResult("TRANSPORT_NOT_STARTED", code="RISK_PERMIT_UNAVAILABLE")
        self._risk_reconnect(timeout_seconds)
        call = self._risk.dispatch(
            request_key,
            permit_id=permit.permit_id,
            reservation_id=permit.reservation_id,
            view_digest=permit.view_digest,
            market_ref=market_ref,
            timeout_seconds=timeout_seconds,
        )
        if call.outcome == "NOT_SENT":
            return self._risk_not_sent(call)
        if call.outcome != "ANSWERED":
            return GatewayRiskRoutedResult("UNKNOWN", code="RISK_OUTCOME_UNKNOWN")
        answer = call.answer
        if answer.outcome == "DENIED":
            return self._risk_refusal(answer)
        if answer.outcome in ("DISPATCHED", "RELEASED", "TERMINAL"):
            if answer.outcome == "DISPATCHED":
                self._risk_permits[request_key] = answer
            return GatewayRiskRoutedResult(
                "QUEUED" if answer.outcome == "DISPATCHED" else "OBSERVED",
                record=GatewayRiskPermitRecord(
                    request_key, answer.outcome, answer.permit_id or permit.permit_id,
                    answer.reservation_id or permit.reservation_id,
                    answer.view_digest or permit.view_digest, None,
                ),
                risk_answer=answer,
            )
        return GatewayRiskRoutedResult("UNKNOWN", code="RISK_OUTCOME_UNKNOWN", risk_answer=answer)

    def _risk_release(self, request_key, operation_id, timeout_seconds):
        if type(operation_id) is not str or not operation_id or operation_id != operation_id.strip():
            return GatewayRiskRoutedResult("TRANSPORT_NOT_STARTED", code="INVALID_RELEASE")
        permit, failure = self._risk_permit(request_key, timeout_seconds)
        if failure is not None:
            # A release is not a PLACE: an unreachable Risk is not a refusal here.
            if failure.risk_code == "risk_unavailable":
                return GatewayRiskRoutedResult("TRANSPORT_NOT_STARTED", code="RISK_UNAVAILABLE")
            return failure
        call = self._risk.release(
            request_key, permit_id=permit.permit_id, timeout_seconds=timeout_seconds
        )
        if call.outcome == "NOT_SENT":
            return GatewayRiskRoutedResult("TRANSPORT_NOT_STARTED", code="RISK_UNAVAILABLE")
        if call.outcome != "ANSWERED":
            return GatewayRiskRoutedResult("UNKNOWN", code="RISK_OUTCOME_UNKNOWN")
        answer = call.answer
        if answer.outcome == "DENIED":
            return self._risk_refusal(answer)
        if answer.outcome in ("RELEASED", "DISPATCHED", "TERMINAL"):
            self._risk_permits.pop(request_key, None)
            return GatewayRiskRoutedResult(
                "OBSERVED",
                record=GatewayRiskPermitRecord(
                    request_key, answer.outcome, answer.permit_id or permit.permit_id,
                    answer.reservation_id or permit.reservation_id,
                    answer.view_digest or permit.view_digest, None,
                ),
                risk_answer=answer,
            )
        return GatewayRiskRoutedResult("UNKNOWN", code="RISK_OUTCOME_UNKNOWN", risk_answer=answer)

    def delegated_command_status(self, exchange_name, request_key, *, timeout_seconds=5.0):
        """Observe a command submitted for this strategy by another principal."""
        self._check(exchange_name)
        return self._delegated.status(request_key, timeout_seconds=timeout_seconds)

    def cancel_delegated_order(self, exchange_name, observation, *, operation_id, timeout_seconds=5.0):
        """Create one durable cancel attempt for a delegated command."""
        self._check(exchange_name)
        return self._delegated.cancel(observation, operation_id=operation_id, timeout_seconds=timeout_seconds)

    def delegated_cancel_status(self, exchange_name, operation_id, *, timeout_seconds=5.0):
        """Recover a prior delegated cancel without issuing another cancellation."""
        self._check(exchange_name)
        return self._delegated.cancel_status(operation_id, timeout_seconds=timeout_seconds)

    def release_order(self, exchange_name, request_key, *, operation_id, timeout_seconds=5.0):
        self._check(exchange_name)
        if self._risk is not None and not self._own_command(request_key):
            # Risk holds the reservation of an order placed through it.
            return self._risk_release(request_key, operation_id, timeout_seconds)
        return self._command.release(
            request_key, operation_id=operation_id, timeout_seconds=timeout_seconds
        )

    def cancel_order(self, exchange_name, request_key, *, operation_id, timeout_seconds=5.0):
        self._check(exchange_name)
        if self._risk is not None and not self._own_command(request_key):
            # An order placed through Risk is cancelled as this strategy's
            # delegated command, never by direct.cancel and never via Risk.
            from bt_api_py.gateway_delegated_cancel import GatewayDelegatedCancelResult

            status = self._delegated_status(request_key, timeout_seconds)
            if status.outcome != "OBSERVED":
                return GatewayDelegatedCancelResult("TRANSPORT_NOT_STARTED", code=status.code)
            try:
                return self._delegated.cancel(
                    status.record, operation_id=operation_id, timeout_seconds=timeout_seconds
                )
            except Exception:
                return GatewayDelegatedCancelResult(
                    "TRANSPORT_NOT_STARTED", code="DELEGATED_CANCEL_NOT_STARTED"
                )
        return self._command.cancel(
            request_key, operation_id=operation_id, timeout_seconds=timeout_seconds
        )

    def cancel_status(self, exchange_name, operation_id, *, timeout_seconds=5.0):
        self._check(exchange_name)
        if self._risk is not None:
            try:
                own = self._cancel_host is not None and (
                    self._cancel_host.load_cancel_plan(operation_id) is not None
                )
            except Exception:
                own = True  # the own path validates and fails closed
            if not own:
                return self._delegated.cancel_status(operation_id, timeout_seconds=timeout_seconds)
        return self._command.cancel_status(operation_id, timeout_seconds=timeout_seconds)

    def gateway_execution_stream(self, exchange_name):
        self._check(exchange_name)
        return self._stream

    def get_strategy_position(self, exchange_name, instrument, *, quantity_unit, timeout_seconds=5.0):
        """Read an explicitly granted strategy observation; it is not a reservation."""
        self._check(exchange_name)
        return self._position.position(
            instrument, quantity_unit=quantity_unit, timeout_seconds=timeout_seconds
        )

    def get_strategy_close_capacity(
        self, exchange_name, instrument, *, quantity_unit, side, offset, quantity,
        position_side=None, timeout_seconds=5.0,
    ):
        """Read strategy attribution capacity; provider close capacity remains separate."""
        self._check(exchange_name)
        return self._position.close_capacity(
            instrument, quantity_unit=quantity_unit, side=side, offset=offset,
            quantity=quantity, position_side=position_side, timeout_seconds=timeout_seconds,
        )

    def strategy_position_is_current(self, exchange_name, observation, *, max_age_seconds):
        """Check local observation freshness, not write authorization."""
        self._check(exchange_name)
        return self._position.is_current(observation, max_age_seconds=max_age_seconds)

    def get_strategy_financial(self, exchange_name, currency, *, timeout_seconds=5.0):
        self._check(exchange_name)
        return self._financial.snapshot(currency, timeout_seconds=timeout_seconds)

    def strategy_financial_is_current(self, exchange_name, observation, *, max_age_seconds):
        self._check(exchange_name)
        return self._financial.is_current(observation, max_age_seconds=max_age_seconds)

    def account_read(self, exchange_name):
        self._check(exchange_name)
        if self._account is None:
            raise RuntimeError("ACCOUNT_CLIENT_UNAVAILABLE")
        return self._account

    def gateway_account_observer(self, exchange_name):
        """Return an explicitly configured account event observer, without auto-ACK."""
        self._check(exchange_name)
        if self._account_observer is None:
            raise RuntimeError("ACCOUNT_OBSERVER_UNAVAILABLE")
        return self._account_observer

    def gateway_account_facts(self, exchange_name):
        """Return the explicitly configured account fact cursor, independent of events."""
        self._check(exchange_name)
        if self._account_facts is None:
            raise RuntimeError("ACCOUNT_FACTS_UNAVAILABLE")
        return self._account_facts

    def market_subscription(self, exchange_name, name):
        """Select a configured name; the route label is not source proof."""
        self._check(exchange_name)
        if type(name) is not str or name not in self._subscriptions:
            raise RuntimeError("MARKET_SUBSCRIPTION_UNAVAILABLE")
        return self._subscriptions[name]

    def get_instrument_spec(self, exchange_name, symbol, *, timeout_seconds=5.0):
        self._check(exchange_name)
        if self._instrument is None:
            raise RuntimeError("INSTRUMENT_CLIENT_UNAVAILABLE")
        return self._session.get_instrument_spec(
            exchange_name, symbol, timeout_seconds=timeout_seconds
        )
