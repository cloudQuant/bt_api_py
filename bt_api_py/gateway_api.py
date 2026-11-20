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
from copy import deepcopy
from dataclasses import dataclass, replace
from types import MethodType
from typing import TYPE_CHECKING

from bt_api_py.gateway_strategy_client import (
    GatewayStrategyClientSession,
    _release_scope_rejected_custody,
)
from bt_api_py.service_runtime_cleanup import runtime_cleanup_confirmed
from bt_api_py.service_runtime_thread import RuntimeSnapshot, ThreadedRuntimeError, _timeout

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
        self, session, *, exchange_name: str, owns_session: bool = False, risk_client=None,
        execution_session=None, execution_router=None
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
        self._close_lock = threading.Lock()
        self._state = "open"
        self._binding_object, self._route_object = session._binding, session._route
        self._binding, self._route = session.binding, session.route
        self._runtime = session.runtime
        if type(session._strict_cleanup) is not bool:
            raise TypeError("INVALID_CLEANUP_POLICY")
        self._gateway_session = session
        self._gateway_runtime = self._runtime
        self._gateway_strict_cleanup = session._strict_cleanup
        self._command = session.command_client
        self._stream = session.execution_stream_client
        self._position = session.position_client
        self._instrument_status = session.instrument_status
        self._instrument_status_peer = (None if self._instrument_status is None else self._instrument_status._peer)
        self._instrument_status_route = (None if self._instrument_status is None else self._instrument_status._route)
        self._instrument_status_route_value = (None if self._instrument_status_route is None else replace(
            self._instrument_status_route, scope=replace(self._instrument_status_route.scope),
            identity=replace(self._instrument_status_route.identity)))
        self._instrument_status_peer_value = (None if self._instrument_status_peer is None else
                                              replace(self._instrument_status_peer))
        self._delegated = session.delegated_client
        self._financial = session.financial_client
        self._delegated_cancel_host = self._delegated._cancel_host
        self._account_observer = session.account_observer
        self._account_facts = session.account_facts
        self._host, self._cancel_host = self._command._host, self._command._cancel_host
        self._account = session.account_client
        self._market = session.market_client
        self._instrument = session.instruments
        self._calendar = session.calendar
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
        # Process-local write guard; the durable intent remains the restart
        # evidence. This set is not a cross-process fencing mechanism.
        self._risk_uncertain_keys = set()
        self._risk_start_code = None
        self._risk_close_failures = 0
        self._risk_cleanup_confirmed = risk_client is None
        if (execution_session is None) != (execution_router is None):
            raise ValueError("INVALID_EXECUTION_CONFIGURATION")
        if execution_session is not None:
            from bt_api_py.execution_parent_strategy_session import ExecutionParentStrategySession
            from bt_api_py.execution_routing import ExecutionOrderRouter
            if (type(execution_session) is not ExecutionParentStrategySession
                    or type(execution_router) is not ExecutionOrderRouter
                    or execution_router._port is not execution_session.client
                    or execution_session.target.scope != self._route.scope
                    or execution_session.target.identity != self._route.identity
                    or execution_router._config.account_ref != self._route.scope.account_ref
                    or execution_router._config.strategy_ref != self._route.scope.strategy_ref
                    or execution_router._config.execution_domain_ref != self._route.scope.execution_domain_ref
                    or execution_router._config.component != execution_session.target.instance_id
                    or execution_router._risk_selected is not (risk_client is not None)):
                raise ValueError("INVALID_EXECUTION_CONFIGURATION")
        self._execution = self._original_execution = execution_session
        self._execution_router = self._original_execution_router = execution_router
        self._execution_client = execution_session.client if execution_session else None
        self._execution_runtime = execution_session.runtime if execution_session else None
        self._execution_target = execution_session.target if execution_session else None
        self._execution_config = execution_router._config if execution_router else None
        self._execution_config_snapshot = (
            deepcopy(self._execution_config.to_plain()) if self._execution_config is not None else None)
        self._execution_db = execution_router._db if execution_router else None
        self._execution_clock = execution_router._clock if execution_router else None
        self._execution_risk = risk_client
        self._execution_start_failed = False
        self._execution_cleanup_confirmed = execution_session is None
        self._gateway_close_snapshot = None
        self._check_binding()

    def _check_process(self):
        if os.getpid() != self._pid:
            raise RuntimeError("WRONG_PROCESS")

    def _check_binding(self):
        self._check_process()
        session = self._session
        market_leases = (
            () if self._market is None else self._market._lease_snapshot()
        )
        if (
            session._binding is not self._binding_object
            or session._route is not self._route_object
            or session.binding != self._binding
            or session.route != self._route
            or session.runtime is not self._runtime
            or session.command_client is not self._command
            or session.execution_stream_client is not self._stream
            or session.position_client is not self._position
            or session.instrument_status is not self._instrument_status
            or session.delegated_client is not self._delegated
            or session.financial_client is not self._financial
            or session.account_observer is not self._account_observer
            or session.account_facts is not self._account_facts
            or session.account_client is not self._account
            or session.market_client is not self._market
            or session.instruments is not self._instrument
            or session.calendar is not self._calendar
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
        for client in (self._account, self._instrument, self._calendar, *self._subscriptions.values()):
            if client is not None and client._runtime is not self._runtime:
                raise RuntimeError("GATEWAY_BINDING_CHANGED")
        if self._instrument_status is not None and (
                self._instrument_status._runtime is not self._runtime
                or self._instrument_status._route is not self._instrument_status_route
                or self._instrument_status._route != self._instrument_status_route_value
                or self._instrument_status._route != self._route
                or self._instrument_status._peer is not self._instrument_status_peer
                or self._instrument_status._peer != self._instrument_status_peer_value):
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
            or any(
                name not in self._subscriptions
                or client._runtime is not self._runtime
                or client._name != name
                or client._lease_manager is not self._market
                for name, client in market_leases
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
        if not self._close_lock.acquire(blocking=False):
            self._check()
            raise RuntimeError("GATEWAY_LIFECYCLE_BUSY")
        try:
            self._check()
            snapshot = self._session.start(timeout_seconds=timeout_seconds)
            # A concurrent close fences the facade even when Gateway start is
            # still in progress. Do not proceed to start Risk after that fence.
            self._check()
            if self._risk is not None:
                # Risk failure does not disable the Gateway's independent cancel
                # and status paths; a key's older history may still be unknown.
                try:
                    self._risk.start(timeout_seconds=timeout_seconds)
                except Exception as error:
                    code = getattr(error, "code", None)
                    self._risk_start_code = code if type(code) is str else type(error).__name__
            self._check()
            if self._execution is not None and not self._execution_start_failed:
                try:
                    self._check_execution_binding()
                    self._execution.start(timeout_seconds=timeout_seconds)
                except Exception:
                    self._execution_start_failed = True
            self._check()
            return snapshot
        finally:
            self._close_lock.release()

    @property
    def risk_selected(self):
        """Whether this strategy's configuration routes PLACE through Risk."""
        return self._risk is not None

    def _check_execution_binding(self):
        session, router = self._original_execution, self._original_execution_router
        if (session is None or self._execution is not session
                or self._execution_router is not router or router is None
                or session.client is not self._execution_client
                or session.runtime is not self._execution_runtime
                or session.target != self._execution_target
                or router._port is not self._execution_client
                or router._config is not self._execution_config
                or router._config.to_plain() != self._execution_config_snapshot
                or router._db is not self._execution_db
                or router._clock is not self._execution_clock
                or self._risk is not self._execution_risk
                or router._risk_selected is not (self._risk is not None)
                or self._execution_client._runtime is not self._execution_runtime
                or self._execution_client._target != self._execution_target):
            raise RuntimeError("EXECUTION_BINDING_CHANGED")

    @property
    def execution_selected(self):
        return self._original_execution is not None

    @property
    def execution_configuration_id(self):
        self._check_process()
        if not self.execution_selected:
            return None
        self._check_binding()
        self._check_execution_binding()
        return self._execution_config.configuration_id

    @property
    def execution_available(self):
        self._check_process()
        if not self.execution_selected:
            return None
        try:
            self._check_execution_binding()
            if self._execution_start_failed or self.lifecycle_state != "open":
                return False
            from bt_api_py.execution_parent_client import ExecutionParentClient
            from bt_api_py.execution_parent_grant import QUERY, SUBMIT
            return all(ExecutionParentClient._registration(self._execution_client, op) for op in (SUBMIT, QUERY))
        except Exception:
            return False

    def execution_route_status(self):
        """Return a finite local observation; it grants no order authority."""
        available = self.execution_available
        return None if available is None else {"available": available}

    def _execution_intent(self, exchange_name, intent, *, allow_historical_run=False):
        self._check(exchange_name)
        self._check_execution_binding()
        from bt_api_py.gateway_direct_client import _check_intent
        try:
            intent = _check_intent(intent)
            candidate_route = intent.route
            if allow_historical_run:
                candidate_route = replace(candidate_route, scope=replace(
                    candidate_route.scope, run_id=self._route.scope.run_id))
            if intent.binding != self._binding or candidate_route != self._route:
                raise ValueError
        except Exception:
            raise ValueError("EXECUTION_INTENT_MISMATCH") from None
        return intent

    def _require_execution_direct_route(self, request_key, *, request=None, ttl_seconds=10.0):
        """Check both locally persisted selections before a direct write."""
        if not self.execution_selected:
            return
        try:
            from bt_api_py.execution_routing import verify_execution_direct_route
            from bt_api_py.gateway_direct_client import _check_intent, build_gateway_order_intent

            self._check_execution_binding()
            if type(request_key) is not str or not request_key:
                raise ValueError
            stored = _check_intent(self._host.load_intent(request_key))
            route = self._host.load_execution_route(request_key)
            if (stored.request_key != request_key or stored.binding != self._binding
                    or stored.route != self._route
                    or route is None or route.kind != ("RISK" if self._risk is not None else "GATEWAY")
                    or route.account != self._binding.account_scope
                    or route.strategy != self._binding.strategy_scope
                    or route.request_key != request_key or route.intent_digest != stored.digest
                    or route.configuration_id != self._execution_config.configuration_id
                    or route.execution_domain_ref != self._route.scope.execution_domain_ref
                    or any(getattr(route, name) is not None for name in
                           ("request_id", "request_sha256", "parent_ref"))):
                raise ValueError
            if request is not None:
                candidate = build_gateway_order_intent(self._binding_object, self._route_object,
                                                       request, ttl_seconds=ttl_seconds)
                if candidate != stored:
                    raise ValueError
            self._check_execution_binding()
            if verify_execution_direct_route(self._execution_router, intent=stored,
                                             configuration_id=self._execution_config.configuration_id) is not True:
                raise ValueError
            self._check_execution_binding()
        except Exception:
            raise RuntimeError("EXECUTION_DIRECT_ROUTE_REQUIRED") from None

    def route_execution_order(self, exchange_name, intent, *, before_submit):
        """Explicitly route an order; ordinary reserve does not call this method."""
        if not callable(before_submit):
            raise ValueError("INVALID_EXECUTION_PERSISTENCE_BARRIER")
        intent = self._execution_intent(exchange_name, intent)
        def barrier(prepared):
            if not self.execution_available:
                return False
            if before_submit(prepared) is not True:
                return False
            return self.execution_available is True
        return self._execution_router.route(intent, before_submit=barrier)

    def execution_order_status(self, exchange_name, intent):
        intent = self._execution_intent(exchange_name, intent, allow_historical_run=True)
        if intent.route.scope.run_id != self._route.scope.run_id:
            # A new registration is only a query transport. The old complete
            # intent and parent identity must still match both durable stores.
            from bt_api_py.execution_routing import ExecutionPreparedRoute
            try:
                if self._host.load_intent(intent.request_key) != intent:
                    raise ValueError
                saved = self._host.load_execution_route(intent.request_key)
                if saved is None or saved.kind != "EXECUTION_PARENT":
                    raise ValueError
                prepared = ExecutionPreparedRoute(
                    account=saved.account, strategy=saved.strategy, request_key=saved.request_key,
                    intent_digest=saved.intent_digest, request_id=saved.request_id,
                    request_sha256=saved.request_sha256, configuration_id=saved.configuration_id,
                    execution_domain_ref=saved.execution_domain_ref, parent_ref=saved.parent_ref)
                if not self.verify_execution_parent_route(intent, prepared):
                    raise ValueError
            except Exception:
                raise ValueError("EXECUTION_PARENT_ROUTE_UNAVAILABLE") from None
        return self._execution_router.status(intent)

    def verify_execution_parent_route(self, intent, prepared):
        """Check a local parent binding without starting a session or issuing RPC."""
        try:
            from bt_api_py.execution_routing import verify_execution_parent_route
            intent = self._execution_intent(self._exchange_name, intent, allow_historical_run=True)
            return verify_execution_parent_route(self._original_execution_router,
                                                 intent=intent, prepared=prepared) is True
        except Exception:
            return False

    def cancel_execution_order(self, exchange_name, intent):
        intent = self._execution_intent(exchange_name, intent)
        return self._execution_router.cancel(intent)

    @property
    def risk_available(self):
        """Return the local Risk reserve-route observation, without RPC.

        None means Risk is not selected; otherwise the client reports whether
        its local route registration and runtime health checks pass. This does
        not verify remote risk sources, order admission, or trading safety.
        """
        self._check_process()
        if self._risk is None:
            return None
        return self._risk.available

    def risk_route_status(self, *, timeout_seconds=5.0):
        """Return why the Risk route is (not) replaceable, without RPC.

        None means Risk is not selected. Otherwise this reports the finite
        recovery code, the maintenance reason (for example ``STALE_EPOCH``) and
        the evidence still missing before a lost route may be re-registered.
        Nothing is sent, resent, or granted by this observation.
        """
        self._check_process()
        if self._risk is None:
            return None
        return self._risk.route_recovery_status(timeout_seconds=timeout_seconds)

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

    @property
    def cleanup_confirmed(self):
        """Whether facade-owned resources have completed their close contracts.

        A borrowed Gateway session is outside this facade's ownership. The
        non-strict Gateway contract does not confirm remote unregister; its
        snapshot still carries the detailed cleanup observations.
        """
        self._check_process()
        with self._lock:
            return self._state == "closed" and self._risk_cleanup_confirmed and (
                not self.execution_selected or (self._execution_cleanup_confirmed
                    and self._execution is self._original_execution
                    and self._original_execution.cleanup_confirmed)
            ) and (
                not self._owns_session or not self._gateway_strict_cleanup
                or (self._strict_gateway_binding_current()
                    and self._gateway_session._closed_snapshot is self._gateway_close_snapshot
                    and self._gateway_session._cleanup_uncertain is False)
            )

    def _strict_gateway_binding_current(self):
        session = self._gateway_session
        return (
            self._session is session and self._runtime is self._gateway_runtime
            and session._runtime is self._gateway_runtime
            and session._original_runtime is self._gateway_runtime
            and session._strict_cleanup is True
        )

    def close(self, *, timeout_seconds=5.0):
        """Close this facade; a borrowed session and its host remain open.

        Return the unmodified Gateway runtime snapshot once Risk cleanup is
        confirmed. Risk cleanup failure raises a finite error after independently
        attempting Gateway closure. Runtime closure does not imply account
        readiness or permission to trade.
        """
        self._check_process()
        _timeout(timeout_seconds)
        with self._lock:
            if (self._state != "closed"
                    or (self._owns_session and self._gateway_strict_cleanup
                        and not self._strict_gateway_binding_current())):
                self._state = "closing"
        if not self._close_lock.acquire(blocking=False):
            raise ThreadedRuntimeError("CLEANUP_FAILED")
        try:
            if not self._execution_cleanup_confirmed:
                try:
                    self._original_execution.close(timeout_seconds=timeout_seconds)
                    self._execution_cleanup_confirmed = (
                        self._execution is self._original_execution
                        and self._original_execution.client is self._execution_client
                        and self._original_execution.runtime is self._execution_runtime
                        and self._original_execution.cleanup_confirmed is True)
                except Exception:
                    self._execution_cleanup_confirmed = False
            if not self._risk_cleanup_confirmed:
                try:
                    risk_result = self._risk.close(timeout_seconds=timeout_seconds)
                    self._risk_cleanup_confirmed = (
                        type(risk_result) is RuntimeSnapshot
                        and type(risk_result.bindings) is tuple and not risk_result.bindings
                        and type(risk_result.cleanup_failures) is int
                        and risk_result.cleanup_failures == 0
                        and runtime_cleanup_confirmed(risk_result)
                    )
                except Exception:
                    self._risk_cleanup_confirmed = False
                if not self._risk_cleanup_confirmed:
                    self._risk_close_failures += 1
            if not self._owns_session:
                result = self._runtime.status()
                gateway_done = True
            elif self._gateway_strict_cleanup and not self._strict_gateway_binding_current():
                raise ThreadedRuntimeError("CLEANUP_FAILED") from None
            elif self._gateway_close_snapshot is not None:
                result = self._gateway_close_snapshot
                gateway_done = True
            elif self._gateway_strict_cleanup:
                # The strict session owns the one-shot cleanup and its retained
                # uncertain resources; do not bypass it through the runtime.
                result = self._gateway_session.close(timeout_seconds=timeout_seconds)
                never_started = (
                    result.identity is None and result.run_id is None
                    and result.strict_unregister_ack is None
                ) if type(result) is RuntimeSnapshot else False
                gateway_done = (
                    self._strict_gateway_binding_current()
                    and type(result) is RuntimeSnapshot
                    and self._gateway_session._closed_snapshot is result
                    and self._gateway_session._cleanup_uncertain is False
                    and (result.strict_unregister_ack is True or never_started)
                    and type(result.bindings) is tuple and not result.bindings
                    and type(result.cleanup_failures) is int and result.cleanup_failures == 0
                    and runtime_cleanup_confirmed(result)
                )
                if gateway_done:
                    self._gateway_close_snapshot = result
            else:
                # Do not hold the state lock while the original runtime waits.
                result = self._gateway_runtime.close(timeout_seconds=timeout_seconds)
                gateway_done = (
                    type(result) is RuntimeSnapshot
                    and result.state in ("closed", "failed")
                    and result.thread_alive is False
                )
                if gateway_done:
                    self._gateway_close_snapshot = result
                    if (self._session is self._gateway_session
                            and self._runtime is self._gateway_runtime):
                        _release_scope_rejected_custody(
                            self._gateway_session, self._gateway_runtime, result)
            if gateway_done and self._risk_cleanup_confirmed and self._execution_cleanup_confirmed:
                with self._lock:
                    self._state = "closed"
            if not self._risk_cleanup_confirmed or not self._execution_cleanup_confirmed:
                raise ThreadedRuntimeError("CLEANUP_FAILED") from None
            return result
        finally:
            self._close_lock.release()

    def risk_first_send_reserve_contract(self):
        """Bind the local first-send-ticket call contract, without I/O.

        This declaration is for the unmodified SDK implementation and the
        currently selected Risk client and explicitly declared host only. The
        host is trusted to provide its declaration without I/O; the SDK cannot
        enforce that for an arbitrary third-party host. This does not establish
        source authority, a cross-host first send, or permission to trade. A
        wrapper must explicitly provide its own contract; it is never inferred
        from a callable's signature. Concurrent arbitrary mutation is not fenced.
        """
        if type(self) is not GatewayBtApi:
            return None
        state = vars(self)
        risk = state.get("_risk")
        host = state.get("_host")
        if not _ticket_reserve_contract_matches(self, risk, host):
            return None
        try:
            declare = getattr(host, "risk_first_send_host_contract", None)
            if type(declare) is not MethodType or declare.__self__ is not host:
                return None
            host_contract = declare()
        except Exception:
            return None
        if (not _ticket_reserve_contract_matches(self, risk, host)
                or not _ticket_host_contract_valid(host, host_contract)):
            return None

        def guard():
            if (not _ticket_reserve_contract_matches(self, risk, host)
                    or not _ticket_host_contract_valid(host, host_contract)):
                raise RuntimeError("RISK_TICKET_CONTRACT_CHANGED")

        def reserve(bound_self, exchange_name, request, *, ttl_seconds=10.0, timeout_seconds=5.0,
                    first_send_ticket=None):
            if bound_self is not self:
                raise RuntimeError("RISK_TICKET_CONTRACT_CHANGED")
            guard()
            # _check and the host may execute external code. Re-check after
            # each boundary and never route this ticket call through the
            # ordinary reserve_order direct-Gateway branch.
            self._check(exchange_name)
            guard()
            self._require_execution_direct_route(getattr(request, "idempotency_key", None),
                                                 request=request, ttl_seconds=ttl_seconds)
            def guarded_route():
                guard()
                self._require_execution_direct_route(getattr(request, "idempotency_key", None),
                                                     request=request, ttl_seconds=ttl_seconds)
            result = _TICKET_RISK_RESERVE_IMPLEMENTATION(
                self, request, ttl_seconds, timeout_seconds, first_send_ticket,
                _ticket_host_contract=host_contract, _ticket_guard=guarded_route,
            )
            guard()
            return result

        return ("local-first-send-ticket-v1", MethodType(reserve, self), host_contract)

    def reserve_order(
        self, exchange_name, request: OrderRequest, *, ttl_seconds=10.0, timeout_seconds=5.0,
        first_send_ticket=None,
    ):
        """Reserve once through Risk using the configured host's first-send ticket.

        A caller that already persisted an intent can provide its one-use host
        ticket. Without one, the host must atomically create a new intent and
        issue a ticket. These process-local tickets are not cross-host fencing.
        """
        risk = self._risk
        self._check(exchange_name)
        if self._risk is not risk:
            if risk is None:
                raise RuntimeError("RISK_SELECTION_CHANGED")
            return GatewayRiskRoutedResult("UNKNOWN", code="RISK_OUTCOME_UNKNOWN")
        self._require_execution_direct_route(getattr(request, "idempotency_key", None),
                                             request=request, ttl_seconds=ttl_seconds)
        if risk is None:
            if first_send_ticket is not None:
                raise ValueError("RISK_TICKET_NOT_APPLICABLE")
            return self._command.reserve(
                request, ttl_seconds=ttl_seconds, timeout_seconds=timeout_seconds
            )
        try:
            contract = _TICKET_FACADE_CONTRACT_IMPLEMENTATION(self)
        except Exception:
            contract = None
        if contract is None:
            return GatewayRiskRoutedResult("UNKNOWN", code="RISK_OUTCOME_UNKNOWN")
        return contract[1](
            exchange_name, request, ttl_seconds=ttl_seconds,
            timeout_seconds=timeout_seconds, first_send_ticket=first_send_ticket,
        )

    def dispatch_order(self, exchange_name, request_key, *, market_ref=None, timeout_seconds=5.0):
        self._check(exchange_name)
        self._require_execution_direct_route(request_key)
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
        # This particular Risk operation provably never left the client.
        return GatewayRiskRoutedResult(
            "REQUEST_DENIED", code="RISK_UNAVAILABLE", risk_code="risk_unavailable",
            risk_denied="risk_unavailable",
        )

    def _risk_reserve(self, request, ttl_seconds, timeout_seconds, first_send_ticket, *,
                      _ticket_host_contract=None, _ticket_guard=None):
        from bt_api_py.gateway_direct_client import build_gateway_order_intent

        # A pre-existing intent is normal for BT: the framework durably saved
        # the submitted order first. Only a host-issued single-use capability
        # distinguishes that initial call from a restored or duplicated key.
        key = None
        try:
            key = getattr(request, "idempotency_key", None)
            if type(key) is not str or not key:
                return GatewayRiskRoutedResult("TRANSPORT_NOT_STARTED", code="INTENT_NOT_DURABLE")
            if _ticket_guard is not None:
                _ticket_guard()
            with self._lock:
                if (key in self._risk_keys or key in self._risk_permits
                    or key in self._risk_uncertain_keys):
                    # A concurrent duplicate gets its own UNKNOWN; it must not
                    # invalidate the original invocation's eventual receipt.
                    return GatewayRiskRoutedResult("UNKNOWN", code="RISK_OUTCOME_UNKNOWN")
                candidate = build_gateway_order_intent(
                    self._binding_object, self._route_object, request, ttl_seconds=ttl_seconds
                )
                if _ticket_guard is not None:
                    _ticket_guard()
                if candidate.request_key != key:
                    raise ValueError("INTENT_CONFLICT")
                ticket = first_send_ticket
                host = self._host if _ticket_host_contract is None else _ticket_host_contract[1].__self__
                if ticket is None:
                    create = (getattr(host, "create_risk_reserve_intent", None)
                              if _ticket_host_contract is None else _ticket_host_contract[2])
                    if not callable(create):
                        return GatewayRiskRoutedResult("UNKNOWN", code="RISK_OUTCOME_UNKNOWN")
                    ticket = create(candidate)
                    if _ticket_guard is not None:
                        _ticket_guard()
                consume = (getattr(host, "consume_risk_reserve_ticket", None)
                           if _ticket_host_contract is None else _ticket_host_contract[3])
                if ticket is None or not callable(consume):
                    return GatewayRiskRoutedResult("UNKNOWN", code="RISK_OUTCOME_UNKNOWN")
                existing = host.load_intent(key)
                if _ticket_guard is not None:
                    _ticket_guard()
                if (
                    existing is None
                    or existing.binding != candidate.binding
                    or existing.route != candidate.route
                    or existing.request_key != key
                    or existing.canonical_payload != candidate.canonical_payload
                    or existing.digest != candidate.digest
                ):
                    raise ValueError("INTENT_CONFLICT")
                consumed = consume(candidate, ticket)
                if _ticket_guard is not None:
                    _ticket_guard()
                if consumed is not True:
                    return GatewayRiskRoutedResult("UNKNOWN", code="RISK_OUTCOME_UNKNOWN")
                # Retain the key as uncertain even though this invocation
                # performs no reservation I/O; an earlier host may have used it.
                self._risk_keys.add(key)
                self._risk_uncertain_keys.add(key)
        except Exception:
            if type(key) is str and key:
                if _ticket_guard is not None:
                    self._risk_uncertain_keys.add(key)
                return GatewayRiskRoutedResult("UNKNOWN", code="RISK_OUTCOME_UNKNOWN")
            return GatewayRiskRoutedResult("TRANSPORT_NOT_STARTED", code="INTENT_NOT_DURABLE")
        # V1 cannot establish first creation in the Gateway journal. Do not
        # create a reservation that this facade could neither dispatch nor
        # release: the key's earlier history remains unknown until the v2
        # first-creation proof is wired end to end.
        return GatewayRiskRoutedResult("UNKNOWN", code="RISK_OUTCOME_UNKNOWN")

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
        # A status response is an observation, not authority to release a
        # reservation that a previous process or host may own.
        return None, GatewayRiskRoutedResult("UNKNOWN", code="RISK_OUTCOME_UNKNOWN")

    def _risk_dispatch(self, request_key, market_ref, timeout_seconds):
        with self._lock:
            if request_key in self._risk_uncertain_keys:
                return GatewayRiskRoutedResult("UNKNOWN", code="RISK_OUTCOME_UNKNOWN")
        permit = self._risk_permits.get(request_key)
        if permit is None or permit.outcome != "RESERVED":
            # No locally held proof means the prior order remains unknown.
            return GatewayRiskRoutedResult("UNKNOWN", code="RISK_OUTCOME_UNKNOWN")
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
        with self._lock:
            if request_key in self._risk_uncertain_keys:
                return GatewayRiskRoutedResult("UNKNOWN", code="RISK_OUTCOME_UNKNOWN")
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

    def query_instrument_status(self, exchange_name, instrument, *, timeout_seconds=5.0):
        """Read an explicitly granted diagnostic; it does not authorize trading."""
        self._check(exchange_name)
        if self._instrument_status is None:
            raise RuntimeError('INSTRUMENT_STATUS_UNAVAILABLE')
        return self._instrument_status.query(instrument, timeout_seconds=timeout_seconds)

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

    def acquire_market_subscription(
        self, exchange_name, name, *, timeout_seconds=5.0
    ):
        """Acquire a distinct market client with an owner-confirmed local lease."""
        self._check(exchange_name)
        if self._market is None or type(name) is not str or name not in self._subscriptions:
            raise RuntimeError("MARKET_SUBSCRIPTION_UNAVAILABLE")
        return self._market.acquire_subscription(name, timeout_seconds=timeout_seconds)

    def trading_calendar_query(self, exchange_name, *, route, venue, kind, product, contract, trading_day, timeout_seconds=5.0):
        """Read the explicitly configured simulation calendar through Gateway."""
        self._check(exchange_name)
        if self._calendar is None:
            raise RuntimeError("CALENDAR_CLIENT_UNAVAILABLE")
        if route != exchange_name:
            raise ValueError("CALENDAR_SCOPE_NOT_CONFIGURED")
        return self._calendar.query(route=route, venue=venue, kind=kind, product=product, contract=contract, trading_day=trading_day, timeout_seconds=timeout_seconds)

    def trading_calendar_windows(self, exchange_name, *, route, venue, kind, product, contract, start_utc, end_utc, timeout_seconds=5.0):
        """Read an explicitly enabled simulation UTC range through Gateway."""
        self._check(exchange_name)
        if self._calendar is None:
            raise RuntimeError("CALENDAR_CLIENT_UNAVAILABLE")
        if route != exchange_name:
            raise ValueError("CALENDAR_SCOPE_NOT_CONFIGURED")
        return self._calendar.windows(route=route, venue=venue, kind=kind, product=product, contract=contract, start_utc=start_utc, end_utc=end_utc, timeout_seconds=timeout_seconds)

    def get_instrument_spec(self, exchange_name, symbol, *, timeout_seconds=5.0):
        self._check(exchange_name)
        if self._instrument is None:
            raise RuntimeError("INSTRUMENT_CLIENT_UNAVAILABLE")
        return self._session.get_instrument_spec(
            exchange_name, symbol, timeout_seconds=timeout_seconds
        )


# Keep the contract tied to the implementation that actually accepts and
# consumes the host ticket, rather than guessing from a callable's signature.
_TICKET_RESERVE_IMPLEMENTATION = GatewayBtApi.reserve_order
_TICKET_RISK_RESERVE_IMPLEMENTATION = GatewayBtApi._risk_reserve
_TICKET_FACADE_CONTRACT_IMPLEMENTATION = GatewayBtApi.risk_first_send_reserve_contract


def _ticket_reserve_contract_matches(api, risk, host):
    if type(api) is not GatewayBtApi or risk is None or host is None:
        return False
    state = vars(api)
    return (
        state.get("_risk") is risk
        and state.get("_host") is host
        and "reserve_order" not in state
        and "_risk_reserve" not in state
        and "risk_first_send_reserve_contract" not in state
        and GatewayBtApi.__dict__.get("reserve_order") is _TICKET_RESERVE_IMPLEMENTATION
        and GatewayBtApi.__dict__.get("_risk_reserve") is _TICKET_RISK_RESERVE_IMPLEMENTATION
        and GatewayBtApi.__dict__.get("risk_first_send_reserve_contract")
        is _TICKET_FACADE_CONTRACT_IMPLEMENTATION
    )


def _ticket_host_contract_valid(host, contract):
    return (
        type(contract) is tuple and len(contract) == 4
        and type(contract[0]) is str and contract[0] == "local-first-send-host-v1"
        and all(type(method) is MethodType and method.__self__ is host for method in contract[1:])
    )
