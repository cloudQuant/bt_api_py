"""Reply-loss fault injection support for the ARB04 real-chain group scenario.

The coordinator host builds its upstream clients from a
``TrustedServiceClients.transport_factory``. This module wraps the real
``ZmqTransportFactory`` so that, for an armed operation and one configured
number of occurrences, the request is genuinely forwarded to the account host
(which performs its full durable work) and only the *reply* is then discarded:
the wrapper raises the transport's own ``RequestOutcomeUnknownError`` exactly
as a lost socket reply would. The coordinator therefore observes a real
submit-reply loss, not a stubbed failure.

Wire-level per-account operation counters and the drop bookkeeping live in the
coordinator child process and are exposed through the child's control pipe
(``fault-stats`` command), so tests can prove at the wire level that no second
submit was ever sent after the loss.
"""

from __future__ import annotations

import json
import multiprocessing
import sqlite3
import time
from collections.abc import Mapping
from contextlib import suppress
from decimal import Decimal
from pathlib import Path
from typing import Any

import zmq
from bt_api_base.service_protocol import WireEnvelope, WireIdentity, WireRole, WireScope
from bt_api_execution import (
    GroupOrderSnapshot,
    OrderType,
    SqliteOrderGroupStore,
    risk_account_scope_fingerprint,
)
from bt_api_risk import AccountScope
from bt_api_transport_zmq.service_v2 import RequestOutcomeUnknownError

from bt_api_py.service_components import ComponentResources
from bt_api_py.service_config import parse_client_config, parse_service_config
from bt_api_py.service_execution_group import (
    GroupAccountRoute,
    GroupCoordinatorTarget,
    GroupInstrumentRule,
    _OrdersRpcPort,
)
from bt_api_py.service_execution_group_cancel import GroupCancelPlanStore
from bt_api_py.service_execution_group_client import GroupOriginRunBindingStore
from bt_api_py.service_host import (
    ServiceHost,
    TrustedServiceAuthority,
    TrustedServiceClients,
)
from bt_api_py.service_registry import (
    PrincipalGrant,
    ResourceGrant,
)
from bt_api_py.service_runtime import (
    ClientCurveKeys,
    ServiceClientRuntime,
    ZmqTransportFactory,
)
from tests.integration.test_arb03_dim05_group_partial_fill_service import (
    _COORDINATOR_INSTANCE_ID,
    _COORDINATOR_PRINCIPAL,
    _COORDINATOR_SCOPE_REF,
    _COORDINATOR_SERVICE_ID,
    _DOMAIN,
    _PORTFOLIO,
    _REQUEST_TIMEOUT_MS,
    _RUN,
    _STRATEGY,
    _STRATEGY_PRINCIPAL,
    _account_host_process,
    _account_service_config,
    _budget_binding,
    _coordinator_service_config,
    _endpoint,
    _execution_scope,
    _metadata,
    _owner_scope,
    _receive,
    _route_refs,
    _stable_strategy_scope,
    _stop_process,
    _strategy_client_config,
    _plain_runtime_json,
    _submit_and_query,
)


class _WireFaultState:
    """Process-local reply-loss bookkeeping shared by every created transport."""

    def __init__(
        self, operation: str, remaining_drops: int, fault_mode: str = "reply_drop"
    ) -> None:
        assert type(operation) is str and operation
        assert type(remaining_drops) is int and remaining_drops >= 0
        assert fault_mode in {"reply_drop", "socket_disconnect_after_provider_dispatch"}
        self.operation = operation
        self.remaining = remaining_drops
        self.dropped = 0
        self.counts: dict[tuple[str, str], int] = {}
        self.fault_mode = fault_mode
        self.socket_events: list[dict[str, Any]] = []
        self.boundary_events: list[dict[str, Any]] = []
        self._socket_closed_identity: str | None = None
        self._socket_fault_account: str | None = None
        self._reconnect_recorded = False

    def count(self, account_ref: str, operation: str) -> None:
        key = (account_ref, operation)
        self.counts[key] = self.counts.get(key, 0) + 1

    def arm(self, operation: str, remaining_drops: int) -> None:
        assert type(operation) is str and operation
        assert type(remaining_drops) is int and remaining_drops > 0
        if self.dropped or self.remaining:
            raise RuntimeError("reply-loss fault is already active")
        self.operation = operation
        self.remaining = remaining_drops

    def statistics(self) -> tuple[tuple[tuple[str, str, int], ...], int, int]:
        items = tuple(
            (account_ref, operation, count)
            for (account_ref, operation), count in sorted(self.counts.items())
        )
        return items, self.dropped, self.remaining

    def socket_disconnect_armed(self, account_ref: str, operation: str) -> bool:
        return (
            self.fault_mode == "socket_disconnect_after_provider_dispatch"
            and self.remaining > 0
            and self.operation == operation
            and self._socket_fault_account in (None, account_ref)
        )

    def record_socket_close(
        self,
        *,
        account_ref: str,
        operation: str,
        message_id: str,
        socket_identity: str,
        provider_dispatch_count: int,
    ) -> None:
        assert self.socket_disconnect_armed(account_ref, operation)
        assert type(provider_dispatch_count) is int and provider_dispatch_count > 0
        self.remaining -= 1
        self.dropped += 1
        self._socket_closed_identity = socket_identity
        self._socket_fault_account = account_ref
        self.socket_events.append(
            {
                "phase": "socket_closed_before_reply_receive",
                "account_ref": account_ref,
                "operation": operation,
                "message_id": message_id,
                "provider_dispatch_count": provider_dispatch_count,
                "socket_identity": socket_identity,
            }
        )

    def record_socket_error(self, *, errno_value: int, message_id: str) -> None:
        assert self.socket_events
        self.socket_events.append(
            {
                "phase": "native_poll_error_after_close",
                "message_id": message_id,
                "errno": errno_value,
            }
        )

    def record_uncertain_result(
        self, *, message_id: str, cause_code: str, socket_retired: bool
    ) -> None:
        assert self.socket_events
        self.socket_events.append(
            {
                "phase": "client_uncertain_result",
                "message_id": message_id,
                "cause_code": cause_code,
                "socket_retired": socket_retired,
            }
        )

    def record_reconnect(
        self,
        *,
        account_ref: str,
        operation: str,
        old_socket_identity: str,
        new_socket_identity: str,
    ) -> None:
        if (
            self._socket_fault_account != account_ref
            or self._socket_closed_identity != old_socket_identity
            or self._reconnect_recorded
        ):
            return
        self._reconnect_recorded = True
        self.socket_events.append(
            {
                "phase": "same_transport_reconnected",
                "account_ref": account_ref,
                "operation": operation,
                "old_socket_identity": old_socket_identity,
                "new_socket_identity": new_socket_identity,
            }
        )

    def socket_statistics(self) -> tuple[dict[str, Any], ...]:
        return tuple(dict(event) for event in self.socket_events)

    def record_cancel_boundary(
        self,
        phase: str,
        *,
        operation: str,
        group_id: str | None = None,
        cancel_request_id: str | None = None,
        leg_id: str | None = None,
        claimed: bool | None = None,
        error_type: str | None = None,
        error_code: str | None = None,
    ) -> None:
        if len(self.boundary_events) >= 16:
            return
        event: dict[str, Any] = {"phase": phase, "operation": operation}
        for name, value in (
            ("group_id", group_id),
            ("cancel_request_id", cancel_request_id),
            ("leg_id", leg_id),
            ("claimed", claimed),
            ("error_type", error_type),
            ("error_code", error_code),
        ):
            if value is not None:
                event[name] = value
        self.boundary_events.append(event)

    def socket_fault_snapshot(self) -> dict[str, Any]:
        return {
            "fault_mode": self.fault_mode,
            "operation": self.operation,
            "remaining": self.remaining,
            "dropped": self.dropped,
            "counts": self.statistics()[0],
            "events": self.socket_statistics(),
            "boundary_events": tuple(dict(event) for event in self.boundary_events),
        }


class _CloseAfterProviderDispatchSocket:
    """Close the actual DEALER socket after its request reached the fake provider."""

    def __init__(
        self,
        socket: Any,
        *,
        state: _WireFaultState,
        account_ref: str,
        operation: str,
        message_id: str,
        provider_ledger_path: str,
    ) -> None:
        self._socket = socket
        self._state = state
        self._account_ref = account_ref
        self._operation = operation
        self._message_id = message_id
        self._provider_ledger_path = provider_ledger_path
        self._closed = False

    def __getattr__(self, name: str) -> Any:
        return getattr(self._socket, name)

    def close(self, *args: Any, **kwargs: Any) -> None:
        if self._closed:
            return
        self._closed = True
        self._socket.close(*args, **kwargs)

    def poll(self, timeout_ms: int = -1, flags: int = zmq.POLLIN) -> int:
        if flags != zmq.POLLIN:
            return self._socket.poll(timeout_ms, flags)
        deadline = (
            None
            if timeout_ms < 0
            else time.monotonic() + (timeout_ms / 1000.0)
        )
        while True:
            remaining_ms = (
                20
                if deadline is None
                else max(0, int((deadline - time.monotonic()) * 1000))
            )
            if deadline is not None and remaining_ms == 0:
                return 0
            ready = self._socket.poll(
                remaining_ms if deadline is None else min(remaining_ms, 20),
                flags,
            )
            dispatch_count = self._provider_dispatch_count()
            if dispatch_count > 0:
                identity = bytes(self._socket.getsockopt(zmq.IDENTITY)).hex()
                self.close(linger=0)
                try:
                    # Ask the real closed pyzmq socket to poll. Its native
                    # ZMQError is what drives ZmqServiceClient's uncertain
                    # receive path and normal socket retirement.
                    self._socket.poll(0, flags)
                except zmq.ZMQError as error:
                    self._state.record_socket_close(
                        account_ref=self._account_ref,
                        operation=self._operation,
                        message_id=self._message_id,
                        socket_identity=identity,
                        provider_dispatch_count=dispatch_count,
                    )
                    self._state.record_socket_error(
                        errno_value=int(error.errno or 0), message_id=self._message_id
                    )
                    raise
                raise AssertionError("closed DEALER socket poll unexpectedly succeeded")
            if ready:
                return int(ready)
            if deadline is not None and time.monotonic() >= deadline:
                return 0

    def _provider_dispatch_count(self) -> int:
        table_by_operation = {
            "orders.cancel_group_leg": "provider_cancel_dispatches",
            "orders.submit_group_leg": "provider_writes",
        }
        table = table_by_operation.get(self._operation)
        if table is None:
            raise AssertionError("socket fault operation has no provider dispatch ledger")
        with sqlite3.connect(self._provider_ledger_path, timeout=0.25) as connection:
            return int(
                connection.execute(
                    f"SELECT COUNT(*) FROM {table}"
                ).fetchone()[0]
            )


class _ReplyLossTransport:
    """Forward one request to the real transport, then drop the armed reply."""

    def __init__(
        self,
        inner: Any,
        state: _WireFaultState,
        account_ref: str,
        provider_ledger_path: str | None = None,
    ) -> None:
        self._inner = inner
        self._state = state
        self._account_ref = account_ref
        self._provider_ledger_path = provider_ledger_path

    def request(self, request: WireEnvelope, *, timeout: float) -> WireEnvelope:
        operation = request.header.operation
        self._state.count(self._account_ref, operation)
        client = getattr(self._inner, "_transport", None)
        if self._state.socket_disconnect_armed(self._account_ref, operation):
            if client is None or self._provider_ledger_path is None:
                raise AssertionError("socket fault requires the real ZMQ client and provider ledger")
            socket = getattr(client, "_socket", None)
            if socket is None:
                raise AssertionError("socket fault expected an active upstream DEALER")
            client._socket = _CloseAfterProviderDispatchSocket(
                socket,
                state=self._state,
                account_ref=self._account_ref,
                operation=operation,
                message_id=request.header.message_id,
                provider_ledger_path=self._provider_ledger_path,
            )
            try:
                reply = self._inner.request(request, timeout=timeout)
            except RequestOutcomeUnknownError as error:
                self._state.record_uncertain_result(
                    message_id=error.message_id,
                    cause_code=error.cause_code,
                    socket_retired=getattr(client, "_socket", object()) is None,
                )
                raise
            finally:
                # If no provider side effect occurred, keep the original live
                # socket usable; the test's exact event assertions will fail.
                wrapped = getattr(client, "_socket", None)
                if isinstance(wrapped, _CloseAfterProviderDispatchSocket):
                    client._socket = socket
        else:
            before_socket = getattr(client, "_socket", None)
            before_identity = (
                bytes(before_socket.getsockopt(zmq.IDENTITY)).hex()
                if before_socket is not None
                else None
            )
            reply = self._inner.request(request, timeout=timeout)
            after_socket = getattr(client, "_socket", None)
            after_identity = (
                bytes(after_socket.getsockopt(zmq.IDENTITY)).hex()
                if after_socket is not None
                else None
            )
            if (
                self._state._socket_fault_account == self._account_ref
                and before_identity is None
                and after_identity is not None
                and self._state._socket_closed_identity is not None
            ):
                self._state.record_reconnect(
                    account_ref=self._account_ref,
                    operation=operation,
                    old_socket_identity=self._state._socket_closed_identity,
                    new_socket_identity=after_identity,
                )
        if operation == self._state.operation and self._state.remaining > 0:
            if self._state.fault_mode != "reply_drop":
                return reply
            # The upstream handler executed and answered; this reply is
            # discarded on the wire exactly like a lost socket response.
            self._state.remaining -= 1
            self._state.dropped += 1
            raise RequestOutcomeUnknownError(
                request.header.message_id, "response_timeout"
            )
        return reply

    def close(self) -> None:
        self._inner.close()


class _ReplyLossTransportFactory:
    """Wrap a transport factory with per-account reply-loss accounting."""

    def __init__(
        self,
        inner: Any,
        state: _WireFaultState,
        account_by_endpoint: Mapping[str, str],
        provider_ledger_by_account: Mapping[str, str],
    ) -> None:
        self._inner = inner
        self._state = state
        self._account_by_endpoint = dict(account_by_endpoint)
        self._provider_ledger_by_account = dict(provider_ledger_by_account)

    def __call__(self, profile: Mapping[str, Any], expected_peer: WireIdentity) -> Any:
        endpoint = profile["endpoints"].get("rpc_connect")
        account_ref = self._account_by_endpoint.get(str(endpoint), str(endpoint))
        return _ReplyLossTransport(
            self._inner(profile, expected_peer),
            self._state,
            account_ref,
            self._provider_ledger_by_account.get(account_ref),
        )


def _arb04_coordinator_host_process(
    control,
    raw_config: dict[str, Any],
    raw_account_configs: dict[str, dict[str, Any]],
    registry_path: str,
    group_store_path: str,
    run_bindings_path: str,
    account_endpoints: dict[str, str],
    provider_ledger_by_account: dict[str, str],
    account_server_public: dict[str, bytes],
    coordinator_server_public: bytes,
    coordinator_server_secret: bytes,
    coordinator_client_public: bytes,
    coordinator_client_secret: bytes,
    strategy_client_public: bytes,
    metadata_reference_ns: int,
    accounts: tuple[str, ...],
    rule_specs: dict[str, dict[str, str]],
    fault_operation: str,
    fault_drops: int,
    fault_mode: str,
    enable_group_cancel: bool = False,
) -> None:
    """ARB03 coordinator host with an armed wire reply-loss factory."""

    config = parse_service_config(raw_config)
    account_configs = {
        account: parse_service_config(raw_account_configs[account]) for account in accounts
    }
    service_identity = WireIdentity(
        WireRole.SERVICE, _COORDINATOR_PRINCIPAL, _COORDINATOR_SERVICE_ID
    )
    strategy_identity = WireIdentity(WireRole.STRATEGY_CLIENT, _STRATEGY_PRINCIPAL, _STRATEGY)
    strategy_scope = _stable_strategy_scope(accounts)
    account_scope_by_ref: dict[str, WireScope] = {}
    peer_by_service: dict[str, WireIdentity] = {}
    security_profiles: dict[str, ClientCurveKeys] = {}
    for account_ref in accounts:
        reservation_ref, entry_ref, query_ref = _route_refs(account_ref)
        account_scope = _owner_scope(account_ref)
        for scope_ref in (reservation_ref, entry_ref, query_ref):
            account_scope_by_ref[scope_ref] = account_scope
        account_service_id = account_configs[account_ref].service_id
        peer_by_service[account_service_id] = WireIdentity(
            WireRole.SERVICE,
            f"group-owner-service-principal-{account_ref}",
            account_service_id,
        )
        security_profiles[f"coordinator-client-security-{account_ref}"] = ClientCurveKeys(
            server_key=account_server_public[account_ref],
            public_key=coordinator_client_public,
            secret_key=coordinator_client_secret,
        )

    fault_state = _WireFaultState(fault_operation, fault_drops, fault_mode)
    original_claim_dispatch = GroupCancelPlanStore.claim_dispatch
    original_cancel_group_leg = _OrdersRpcPort.cancel_group_leg
    original_query_group_cancel = _OrdersRpcPort.query_group_cancel

    def traced_claim_dispatch(self, *args, **kwargs):
        claimed = original_claim_dispatch(self, *args, **kwargs)
        fault_state.record_cancel_boundary(
            "plan_claim_dispatch",
            operation="orders.cancel_group_leg",
            claimed=claimed,
        )
        return claimed

    def traced_cancel_group_leg(self, *, group_id, cancel_request_id, leg_id):
        fault_state.record_cancel_boundary(
            "upstream_port_enter",
            operation="orders.cancel_group_leg",
            group_id=group_id,
            cancel_request_id=cancel_request_id,
            leg_id=leg_id,
        )
        try:
            result = original_cancel_group_leg(
                self,
                group_id=group_id,
                cancel_request_id=cancel_request_id,
                leg_id=leg_id,
            )
        except Exception as error:
            code = getattr(error, "code", None)
            fault_state.record_cancel_boundary(
                "upstream_port_error",
                operation="orders.cancel_group_leg",
                group_id=group_id,
                cancel_request_id=cancel_request_id,
                leg_id=leg_id,
                error_type=type(error).__name__,
                error_code=code if type(code) is str and len(code) <= 128 else None,
            )
            raise
        fault_state.record_cancel_boundary(
            "upstream_port_return",
            operation="orders.cancel_group_leg",
            group_id=group_id,
            cancel_request_id=cancel_request_id,
            leg_id=leg_id,
        )
        return result

    def traced_query_group_cancel(
        self, *, group_id, cancel_request_id, cancel_id, leg_id
    ):
        fault_state.record_cancel_boundary(
            "upstream_port_enter",
            operation="orders.query_group_cancel",
            group_id=group_id,
            cancel_request_id=cancel_request_id,
            leg_id=leg_id,
        )
        try:
            result = original_query_group_cancel(
                self,
                group_id=group_id,
                cancel_request_id=cancel_request_id,
                cancel_id=cancel_id,
                leg_id=leg_id,
            )
        except Exception as error:
            code = getattr(error, "code", None)
            fault_state.record_cancel_boundary(
                "upstream_port_error",
                operation="orders.query_group_cancel",
                group_id=group_id,
                cancel_request_id=cancel_request_id,
                leg_id=leg_id,
                error_type=type(error).__name__,
                error_code=code if type(code) is str and len(code) <= 128 else None,
            )
            raise
        fault_state.record_cancel_boundary(
            "upstream_port_return",
            operation="orders.query_group_cancel",
            group_id=group_id,
            cancel_request_id=cancel_request_id,
            leg_id=leg_id,
        )
        return result

    GroupCancelPlanStore.claim_dispatch = traced_claim_dispatch
    _OrdersRpcPort.cancel_group_leg = traced_cancel_group_leg
    _OrdersRpcPort.query_group_cancel = traced_query_group_cancel
    trusted_clients = TrustedServiceClients(
        scope_resolver=lambda scope_ref: account_scope_by_ref[scope_ref],
        peer_resolver=lambda service_id: peer_by_service[service_id],
        transport_factory=_ReplyLossTransportFactory(
            ZmqTransportFactory(security_profiles),
            fault_state,
            {endpoint: account_ref for account_ref, endpoint in account_endpoints.items()},
            provider_ledger_by_account,
        ),
        requested_operations={
            "upstream.orders": frozenset(
                {"orders.submit_group_leg", "orders.resolve_group_leg"}
                | ({"orders.cancel_group_leg"} if enable_group_cancel else set())
            ),
            "upstream.order_query": frozenset(
                {"orders.query_group_leg"}
                | ({"orders.query_group_cancel"} if enable_group_cancel else set())
            ),
            "upstream.account_reservations": frozenset(
                {
                    "reservations.reserve",
                    "reservations.query",
                    "reservations.release",
                }
            ),
        },
    )
    route_targets: dict[str, GroupAccountRoute] = {}
    for account_ref in accounts:
        provider_account = f"provider-{account_ref}"
        risk_scope = AccountScope("fixture", provider_account, "simulation")
        execution_scope = _execution_scope(account_ref)
        reservation_ref, entry_ref, query_ref = _route_refs(account_ref)
        instruments: dict[str, GroupInstrumentRule] = {}
        for instrument, base_units in rule_specs[account_ref].items():
            metadata = _metadata(instrument, metadata_reference_ns)
            instruments[instrument] = GroupInstrumentRule(
                metadata=metadata,
                base_units_per_order_unit=Decimal(base_units),
                allowed_order_types=frozenset({OrderType.LIMIT}),
            )
        route_targets[account_ref] = GroupAccountRoute(
            execution_scope=execution_scope,
            risk_scope_fingerprint=risk_account_scope_fingerprint(risk_scope.key),
            reservation_scope_ref=reservation_ref,
            order_scope_ref=entry_ref,
            query_scope_ref=query_ref,
            instruments=instruments,
        )
    budget_instruments = tuple(
        sorted({instrument for account in accounts for instrument in rule_specs[account]})
    )
    budget_policy, budget_quote_builder = _budget_binding(
        tuple(_metadata(instrument, metadata_reference_ns) for instrument in budget_instruments)
    )
    coordinator_target = GroupCoordinatorTarget(
        scope_ref=_COORDINATOR_SCOPE_REF,
        accounts=route_targets,
        max_group_lifetime_ns=300_000_000_000,
        portfolio_budget_policy=budget_policy,
        budget_quote_builder=budget_quote_builder,
    )
    resources: dict[str, object] = {}
    host: ServiceHost | None = None

    def resource_provider(component, _instance_id, _options, _scope):
        if component != "execution":
            raise AssertionError("coordinator host must contain only its fixed component")
        group_store = SqliteOrderGroupStore(group_store_path)
        run_bindings = GroupOriginRunBindingStore(run_bindings_path)
        resources.update({"store": group_store, "run_bindings": run_bindings})
        return ComponentResources.borrowed(
            {
                "store": group_store,
                "targets": {strategy_scope: coordinator_target},
                "run_bindings": run_bindings,
            },
            ready=lambda: True,
        )

    try:
        host = ServiceHost(
            config,
            TrustedServiceAuthority(
                identity=service_identity,
                scope=WireScope(_DOMAIN),
                scope_ref=config.scope_ref,
                scope_version="coordinator-scope-v1",
                security_ref="coordinator-server-security-v1",
                security_version="coordinator-curve-v1",
                registration_policy_ref=config.registration_policy_ref,
                registration_policy_version="coordinator-registration-v1",
                service_requirements_ref=config.service_requirements_ref,
                service_requirements_version="coordinator-requirements-v1",
                curve_public_key=coordinator_server_public,
                curve_secret_key=coordinator_server_secret,
                principals={strategy_client_public: _STRATEGY_PRINCIPAL},
                grants={
                    _STRATEGY_PRINCIPAL: (
                        PrincipalGrant(
                            strategy_identity,
                            (
                                ResourceGrant(
                                    _COORDINATOR_INSTANCE_ID,
                                    "service.order_group_entry",
                                    _COORDINATOR_SCOPE_REF,
                                    strategy_scope,
                                    frozenset(
                                        {"groups.submit", "groups.query"}
                                        | ({"groups.cancel"} if enable_group_cancel else set())
                                    ),
                                ),
                            ),
                        ),
                    )
                },
            ),
            registry_path,
            resource_provider,
            service_directory={
                _COORDINATOR_SERVICE_ID: config,
                **{
                    account_configs[account].service_id: account_configs[account]
                    for account in accounts
                },
            },
            trusted_clients=trusted_clients,
        )
        host.start()
        upstream = host.upstream
        if upstream is None or not upstream.healthy:
            raise AssertionError("coordinator Host upstream did not become healthy")
        upstream_bindings = tuple(
            (
                binding.mount,
                binding.scope.account_ref,
                binding.scope_ref,
                binding.registration_id,
                tuple(sorted(binding.operations)),
            )
            for binding in upstream.bindings
            if binding.active
        )
        control.send(("ready", host.endpoint, upstream_bindings))
        while True:
            if control.poll(0):
                command = control.recv()
                if command == "close":
                    host.close()
                    control.send(("closed", host.state, upstream.state))
                    return
                if command == "fault-stats":
                    counts, dropped, remaining = fault_state.statistics()
                    control.send(("fault-stats", counts, dropped, remaining))
                elif command == "fault-details":
                    control.send(("fault-details", fault_state.socket_fault_snapshot()))
                elif type(command) is tuple and len(command) == 3 and command[0] == "arm-fault":
                    fault_state.arm(command[1], command[2])
                    control.send(("fault-armed", command[1], command[2]))
            host.serve_once(15)
    except BaseException as error:
        if host is not None and host.state not in {"closed", "failed"}:
            with suppress(BaseException):
                host.close()
        with suppress(Exception):
            control.send(("error", getattr(error, "code", type(error).__name__)))
        raise
    finally:
        if host is not None and host.state != "closed":
            with suppress(BaseException):
                host.close()
        GroupCancelPlanStore.claim_dispatch = original_claim_dispatch
        _OrdersRpcPort.cancel_group_leg = original_cancel_group_leg
        _OrdersRpcPort.query_group_cancel = original_query_group_cancel
        for name in ("run_bindings", "store"):
            resource = resources.get(name)
            close = getattr(resource, "close", None)
            if callable(close):
                close()
        control.close()


class Arb04GroupCluster:
    """Spawned account/coordinator hosts plus the strategy client runtime.

    Identical to the ARB03 cluster except that the coordinator host is the
    reply-loss variant and the wire fault state is inspectable.
    """

    def __init__(
        self,
        tmp_path,
        *,
        accounts: tuple[str, ...],
        instruments: dict[str, tuple[str, ...]],
        initial_states: dict[str, tuple[str, ...]],
        expected_orders: dict[str, tuple[tuple[str, str, str, str], ...]],
        rule_specs: dict[str, dict[str, str]],
        fault_operation: str = "orders.submit_group_leg",
        fault_drops: int = 1,
        fault_mode: str = "reply_drop",
        enable_group_cancel: bool = False,
    ) -> None:
        self._tmp_path = Path(tmp_path)
        self._startup_phase = "cluster-initializing"
        self._startup_failure: dict[str, Any] | None = None
        self._phase_timings: list[dict[str, Any]] = []
        self._owned_children: list[dict[str, Any]] = []
        self._cleanup_failures: list[dict[str, str]] = []
        self.accounts = accounts
        self.instruments = instruments
        self.initial_states = initial_states
        self.expected_orders = expected_orders
        self.rule_specs = rule_specs
        self.fault_operation = fault_operation
        self.fault_drops = fault_drops
        self.fault_mode = fault_mode
        self.enable_group_cancel = enable_group_cancel
        self.metadata_reference_ns = time.time_ns()
        self.account_paths: dict[str, dict[str, str]] = {
            account: {
                name: str(tmp_path / f"{account}-{name}.sqlite3")
                for name in (
                    "registry",
                    "provider",
                    "execution",
                    "risk_order_journal",
                    "proof",
                    "risk_gate",
                    "accepted",
                    "group_authority",
                )
            }
            for account in accounts
        }
        self.coordinator_paths = {
            "registry": str(tmp_path / "coordinator-registry.sqlite3"),
            "groups": str(tmp_path / "coordinator-groups.sqlite3"),
            "run_bindings": str(tmp_path / "coordinator-run-bindings.sqlite3"),
        }
        self.account_endpoints = {account: _endpoint() for account in accounts}
        self.account_server_keys = {account: zmq.curve_keypair() for account in accounts}
        self.coordinator_server_public, self.coordinator_server_secret = zmq.curve_keypair()
        self.coordinator_client_public, self.coordinator_client_secret = zmq.curve_keypair()
        self.strategy_client_public, self.strategy_client_secret = zmq.curve_keypair()
        self.account_configs = {
            account: _account_service_config(
                account,
                self.account_endpoints[account],
                f"account-server-security-{account}",
            )
            for account in accounts
        }
        self.coordinator_endpoint = _endpoint()
        self.coordinator_raw = _coordinator_service_config(
            self.coordinator_endpoint,
            "coordinator-server-security-v1",
            self.account_configs,
            self.account_endpoints,
            accounts,
        )
        self.account_processes: dict[str, Any] = {}
        self.account_controls: dict[str, Any] = {}
        self.coordinator_process: Any = None
        self.coordinator_control: Any = None
        self.strategy_runtime: ServiceClientRuntime | None = None
        self.strategy_started = False
        self.upstream_bindings: tuple[tuple[Any, ...], ...] = ()

    def __enter__(self) -> Arb04GroupCluster:
        try:
            self._start_all()
        except BaseException as startup_error:
            self._startup_failure = {
                "phase": self._startup_phase,
                "type": type(startup_error).__name__,
                "message": str(startup_error),
            }
            try:
                self.close()
            except BaseException as cleanup_error:
                if hasattr(startup_error, "add_note"):
                    startup_error.add_note(
                        "ARB04 startup cleanup also failed: "
                        f"{type(cleanup_error).__name__}: {cleanup_error}"
                    )
            raise
        return self

    def __exit__(self, _exc_type, exc, _traceback) -> bool:
        try:
            self.close()
        except BaseException as cleanup_error:
            if exc is None:
                raise
            if hasattr(exc, "add_note"):
                exc.add_note(
                    "ARB04 normal-close cleanup also failed: "
                    f"{type(cleanup_error).__name__}: {cleanup_error}"
                )
        return False

    def _record_owned_child(self, role: str, process: Any) -> dict[str, Any]:
        record = {
            "role": role,
            "process_name": process.name,
            "pid": process.pid,
            "startup_phase": self._startup_phase,
        }
        self._owned_children.append(record)
        return record

    def _timed_startup_receive(self, control: Any, phase: str) -> tuple[Any, ...]:
        self._startup_phase = phase
        started = time.monotonic()
        try:
            return _receive(control)
        finally:
            finished = time.monotonic()
            self._phase_timings.append(
                {
                    "phase": phase,
                    "started_monotonic": started,
                    "finished_monotonic": finished,
                    "elapsed_seconds": finished - started,
                }
            )

    def _persist_lifecycle_report(self) -> None:
        payload = {
            "startup_phase": self._startup_phase,
            "startup_failure": self._startup_failure,
            "phase_timings": self._phase_timings,
            "children": self._owned_children,
            "cleanup_failures": self._cleanup_failures,
        }
        (self._tmp_path / "arb04-owned-child-lifecycle.json").write_text(
            json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
        )

    def _start_all(self) -> None:
        account_context = multiprocessing.get_context("spawn")
        for account_ref in self.accounts:
            parent_control, child_control = account_context.Pipe(duplex=True)
            process = account_context.Process(
                name=f"group-account-host-{account_ref}",
                target=_account_host_process,
                args=(
                    child_control,
                    self.account_configs[account_ref],
                    self.account_paths[account_ref]["registry"],
                    self.account_paths[account_ref],
                    account_ref,
                    self.account_server_keys[account_ref][0],
                    self.account_server_keys[account_ref][1],
                    self.coordinator_client_public,
                    self.metadata_reference_ns,
                    self.initial_states[account_ref],
                    self.instruments[account_ref],
                    self.expected_orders[account_ref],
                    self.enable_group_cancel,
                    True,
                ),
            )
            self._startup_phase = f"account-process-start:{account_ref}"
            process_started = time.monotonic()
            try:
                process.start()
            except BaseException as start_error:
                process_finished = time.monotonic()
                self._phase_timings.append(
                    {
                        "phase": self._startup_phase,
                        "started_monotonic": process_started,
                        "finished_monotonic": process_finished,
                        "elapsed_seconds": process_finished - process_started,
                    }
                )
                if process.pid is not None:
                    process_facts = self._record_owned_child(
                        f"account-host:{account_ref}", process
                    )
                    process_facts["start_error"] = type(start_error).__name__
                    self.account_processes[account_ref] = process
                    self.account_controls[account_ref] = parent_control
                    endpoints = (("child", child_control),)
                else:
                    endpoints = (
                        ("child", child_control),
                        ("parent", parent_control),
                    )
                for endpoint_name, endpoint in endpoints:
                    try:
                        endpoint.close()
                    except BaseException as cleanup_error:
                        self._cleanup_failures.append(
                            {
                                "stage": f"close-account-{endpoint_name}-pipe:{account_ref}",
                                "type": type(cleanup_error).__name__,
                                "message": str(cleanup_error),
                            }
                        )
                        if hasattr(start_error, "add_note"):
                            start_error.add_note(
                                "ARB04 process-start cleanup also failed: "
                                f"{type(cleanup_error).__name__}: {cleanup_error}"
                            )
                raise
            process_facts = self._record_owned_child(
                f"account-host:{account_ref}", process
            )
            process_finished = time.monotonic()
            self._phase_timings.append(
                {
                    "phase": self._startup_phase,
                    "started_monotonic": process_started,
                    "finished_monotonic": process_finished,
                    "elapsed_seconds": process_finished - process_started,
                }
            )
            self.account_processes[account_ref] = process
            self.account_controls[account_ref] = parent_control
            child_control.close()
            process_facts["ready_wait_phase"] = f"account-host-ready:{account_ref}"
            startup = self._timed_startup_receive(
                parent_control, f"account-host-ready:{account_ref}"
            )
            process_facts["ready_ack"] = startup[0] if startup else None
            assert len(startup) > 2 and isinstance(startup[2], dict), startup
            process_facts["plugin_discovery"] = startup[2]
            assert startup[2]["groups_requested"] == ["bt_api.plugins"], startup[2]
            assert startup[2]["discovery_result_counts"] == {"bt_api.plugins": 0}, startup[2]
            assert startup[2]["loaded_flag_restored"] is True, startup[2]
            assert startup[2]["discover_method_restored"] is True, startup[2]
            self._startup_phase = f"account-host-ready-validate:{account_ref}"
            assert startup[0] == "ready", startup
            assert startup[1] == self.account_endpoints[account_ref]

        coordinator_context = multiprocessing.get_context("spawn")
        coordinator_parent, coordinator_child = coordinator_context.Pipe(duplex=True)
        self.coordinator_process = coordinator_context.Process(
            name="group-coordinator-host",
            target=_arb04_coordinator_host_process,
            args=(
                coordinator_child,
                self.coordinator_raw,
                self.account_configs,
                self.coordinator_paths["registry"],
                self.coordinator_paths["groups"],
                self.coordinator_paths["run_bindings"],
                self.account_endpoints,
                {
                    account: self.account_paths[account]["provider"]
                    for account in self.accounts
                },
                {
                    account: self.account_server_keys[account][0]
                    for account in self.accounts
                },
                self.coordinator_server_public,
                self.coordinator_server_secret,
                self.coordinator_client_public,
                self.coordinator_client_secret,
                self.strategy_client_public,
                self.metadata_reference_ns,
                self.accounts,
                self.rule_specs,
                self.fault_operation,
                self.fault_drops,
                self.fault_mode,
                self.enable_group_cancel,
            ),
        )
        self._startup_phase = "coordinator-process-start"
        coordinator_started = time.monotonic()
        try:
            self.coordinator_process.start()
        except BaseException as start_error:
            coordinator_finished = time.monotonic()
            self._phase_timings.append(
                {
                    "phase": self._startup_phase,
                    "started_monotonic": coordinator_started,
                    "finished_monotonic": coordinator_finished,
                    "elapsed_seconds": coordinator_finished - coordinator_started,
                }
            )
            if self.coordinator_process.pid is not None:
                coordinator_facts = self._record_owned_child(
                    "coordinator-host", self.coordinator_process
                )
                coordinator_facts["start_error"] = type(start_error).__name__
                self.coordinator_control = coordinator_parent
                endpoints = (("child", coordinator_child),)
            else:
                self.coordinator_control = None
                self.coordinator_process = None
                endpoints = (
                    ("child", coordinator_child),
                    ("parent", coordinator_parent),
                )
            for endpoint_name, endpoint in endpoints:
                try:
                    endpoint.close()
                except BaseException as cleanup_error:
                    self._cleanup_failures.append(
                        {
                            "stage": f"close-coordinator-{endpoint_name}-pipe",
                            "type": type(cleanup_error).__name__,
                            "message": str(cleanup_error),
                        }
                    )
                    if hasattr(start_error, "add_note"):
                        start_error.add_note(
                            "ARB04 coordinator-start cleanup also failed: "
                            f"{type(cleanup_error).__name__}: {cleanup_error}"
                        )
            raise
        coordinator_facts = self._record_owned_child(
            "coordinator-host", self.coordinator_process
        )
        coordinator_finished = time.monotonic()
        self._phase_timings.append(
            {
                "phase": self._startup_phase,
                "started_monotonic": coordinator_started,
                "finished_monotonic": coordinator_finished,
                "elapsed_seconds": coordinator_finished - coordinator_started,
            }
        )
        self.coordinator_control = coordinator_parent
        coordinator_child.close()
        coordinator_facts["ready_wait_phase"] = "coordinator-host-ready"
        coordinator_ready = self._timed_startup_receive(
            self.coordinator_control, "coordinator-host-ready"
        )
        coordinator_facts["ready_ack"] = coordinator_ready[0] if coordinator_ready else None
        self._startup_phase = "coordinator-host-ready-validate"
        assert coordinator_ready[0] == "ready", coordinator_ready
        assert coordinator_ready[1] == self.coordinator_endpoint
        self.upstream_bindings = coordinator_ready[2]
        expected_upstream = (
            {("upstream.orders", account, _route_refs(account)[1]) for account in self.accounts}
            | {
                ("upstream.order_query", account, _route_refs(account)[2])
                for account in self.accounts
            }
            | {
                ("upstream.account_reservations", account, _route_refs(account)[0])
                for account in self.accounts
            }
        )
        expected_upstream_operations = {
            "upstream.orders": frozenset(
                {"orders.submit_group_leg", "orders.resolve_group_leg"}
                | ({"orders.cancel_group_leg"} if self.enable_group_cancel else set())
            ),
            "upstream.order_query": frozenset(
                {"orders.query_group_leg"}
                | ({"orders.query_group_cancel"} if self.enable_group_cancel else set())
            ),
            "upstream.account_reservations": frozenset(
                {"reservations.reserve", "reservations.query", "reservations.release"}
            ),
        }
        assert len(self.upstream_bindings) == len(expected_upstream) == 3 * len(self.accounts)
        assert all(
            registration_id
            and operations == tuple(sorted(expected_upstream_operations[mount]))
            for mount, _account, _scope_ref, registration_id, operations in self.upstream_bindings
        )
        assert {
            (mount, account, scope_ref)
            for mount, account, scope_ref, _registration_id, _operations in self.upstream_bindings
        } == expected_upstream

        order_registration_ids = {
            account: next(
                registration_id
                for mount, bound_account, _scope_ref, registration_id, _operations in (
                    self.upstream_bindings
                )
                if mount == "upstream.orders" and bound_account == account
            )
            for account in self.accounts
        }
        for account_ref in self.accounts:
            control = self.account_controls[account_ref]
            self._startup_phase = f"account-host-activate:{account_ref}"
            activation_started = time.monotonic()
            control.send(("activate", order_registration_ids[account_ref]))
            try:
                activation = _receive(control)
            finally:
                activation_finished = time.monotonic()
                self._phase_timings.append(
                    {
                        "phase": self._startup_phase,
                        "started_monotonic": activation_started,
                        "finished_monotonic": activation_finished,
                        "elapsed_seconds": activation_finished - activation_started,
                    }
                )
            self._startup_phase = f"account-host-activate-validate:{account_ref}"
            assert activation[0] == "activated", activation
            assert activation[1] == order_registration_ids[account_ref]
            assert type(activation[2]) is int and activation[2] > 0

        strategy_identity = WireIdentity(WireRole.STRATEGY_CLIENT, _STRATEGY_PRINCIPAL, _STRATEGY)
        coordinator_identity = WireIdentity(
            WireRole.SERVICE, _COORDINATOR_PRINCIPAL, _COORDINATOR_SERVICE_ID
        )
        strategy_runtime = ServiceClientRuntime(
            parse_client_config(
                _strategy_client_config(self.coordinator_endpoint, "strategy-client-security-v1")
            ),
            identity=strategy_identity,
            scope_resolver=lambda _scope_ref: _stable_strategy_scope(self.accounts),
            peer_resolver=lambda _service_id: coordinator_identity,
            transport_factory=ZmqTransportFactory(
                {
                    "strategy-client-security-v1": ClientCurveKeys(
                        server_key=self.coordinator_server_public,
                        public_key=self.strategy_client_public,
                        secret_key=self.strategy_client_secret,
                    )
                }
            ),
            run_id=_RUN,
            requested_operations={
                "broker.order_groups": frozenset(
                    {"groups.submit", "groups.query"}
                    | ({"groups.cancel"} if self.enable_group_cancel else set())
                )
            },
        )
        self.strategy_runtime = strategy_runtime
        self._startup_phase = "strategy-runtime-start"
        strategy_started = time.monotonic()
        try:
            strategy_receipt = strategy_runtime.start()
        finally:
            strategy_finished = time.monotonic()
            self._phase_timings.append(
                {
                    "phase": self._startup_phase,
                    "started_monotonic": strategy_started,
                    "finished_monotonic": strategy_finished,
                    "elapsed_seconds": strategy_finished - strategy_started,
                }
            )
        self.strategy_started = True
        assert strategy_receipt.degraded_count == 0
        assert len(strategy_receipt.bindings) == 1
        strategy_binding = strategy_receipt.bindings[0]
        assert strategy_binding.mount == "broker.order_groups"
        assert strategy_binding.scope.account_refs == self.accounts
        assert strategy_binding.scope.run_id == _RUN
        assert strategy_binding.operations == frozenset(
            {"groups.submit", "groups.query"}
            | ({"groups.cancel"} if self.enable_group_cancel else set())
        )
        self._startup_phase = "cluster-ready"

    def submit_group(self, request: dict[str, Any]) -> GroupOrderSnapshot:
        assert self.strategy_runtime is not None
        return _submit_and_query(self.strategy_runtime, request)

    def cancel_group(self, group_id: str, cancel_request_id: str) -> Mapping[str, Any]:
        assert self.enable_group_cancel
        assert self.strategy_runtime is not None
        reply = self.strategy_runtime.call(
            "broker.order_groups",
            "groups.cancel",
            {"group_id": group_id, "cancel_request_id": cancel_request_id},
            portfolio_ref=_PORTFOLIO,
        )
        assert reply["ok"] is True, reply
        assert reply["code"] == "OK", reply
        return _plain_runtime_json(reply["result"])

    def query_group(self, group_id: str) -> GroupOrderSnapshot:
        assert self.strategy_runtime is not None
        reply = self.strategy_runtime.call(
            "broker.order_groups",
            "groups.query",
            {"group_id": group_id},
            portfolio_ref=_PORTFOLIO,
        )
        assert reply["ok"] is True, reply
        assert reply["code"] == "OK", reply
        result = _plain_runtime_json(reply["result"])
        assert result["found"] is True, result
        return GroupOrderSnapshot.from_payload(result["snapshot"])

    def status(self, account_ref: str) -> tuple[Any, ...]:
        control = self.account_controls[account_ref]
        control.send(("status",))
        status = _receive(control)
        assert status[0] == "status", status
        return status

    def fault_stats(self) -> tuple[dict[tuple[str, str], int], int, int]:
        control = self.coordinator_control
        assert control is not None
        control.send("fault-stats")
        stats = _receive(control)
        assert stats[0] == "fault-stats", stats
        counts = {(account, operation): count for account, operation, count in stats[1]}
        return counts, stats[2], stats[3]

    def fault_details(self) -> dict[str, Any]:
        control = self.coordinator_control
        assert control is not None
        control.send("fault-details")
        details = _receive(control)
        assert details[0] == "fault-details", details
        assert isinstance(details[1], dict), details
        return details[1]

    def arm_fault(self, operation: str, drops: int) -> None:
        control = self.coordinator_control
        assert control is not None
        control.send(("arm-fault", operation, drops))
        result = _receive(control)
        assert result == ("fault-armed", operation, drops), result

    def _stop_owned_child(self, role: str, process: Any, control: Any) -> None:
        pid = process.pid
        facts = next((item for item in self._owned_children if item["pid"] == pid), None)
        if facts is None:
            facts = self._record_owned_child(role, process)
        facts["role"] = role
        facts["cleanup_started_phase"] = self._startup_phase
        try:
            _stop_process(process, control, lifecycle=facts)
        except BaseException as error:
            facts["cleanup_failure"] = {
                "type": type(error).__name__,
                "message": str(error),
            }
            self._cleanup_failures.append(
                {
                    "stage": f"close-child:{role}",
                    "type": type(error).__name__,
                    "message": str(error),
                }
            )
            raise

    def close(self) -> None:
        cleanup_failures: list[BaseException] = []
        if self.strategy_runtime is not None and self.strategy_runtime.state != "closed":
            runtime = self.strategy_runtime
            self._startup_phase = "strategy-runtime-close"
            close_started = time.monotonic()
            try:
                runtime.close()
                assert runtime.state == "closed"
                assert runtime.unregister_uncertainties == 0
            except BaseException as error:
                cleanup_failures.append(error)
                self._cleanup_failures.append(
                    {
                        "stage": "close-strategy-runtime",
                        "type": type(error).__name__,
                        "message": str(error),
                    }
                )
            finally:
                close_finished = time.monotonic()
                self._phase_timings.append(
                    {
                        "phase": self._startup_phase,
                        "started_monotonic": close_started,
                        "finished_monotonic": close_finished,
                        "elapsed_seconds": close_finished - close_started,
                    }
                )
                self.strategy_started = False
        if self.coordinator_process is not None and self.coordinator_control is not None:
            coordinator = self.coordinator_process
            coordinator_pid = coordinator.pid
            coordinator_control = self.coordinator_control
            try:
                self._stop_owned_child(
                    "coordinator-host",
                    coordinator,
                    coordinator_control,
                )
            except BaseException as error:
                cleanup_failures.append(error)
            facts = next(
                (item for item in self._owned_children if item["pid"] == coordinator_pid),
                None,
            )
            if facts is not None and facts.get("alive_after_join") is False and facts.get(
                "process_closed"
            ) is True:
                facts["owner_refs_retained"] = False
                self.coordinator_process = None
                self.coordinator_control = None
            elif facts is not None:
                facts["owner_refs_retained"] = True
        for account_ref in reversed(self.accounts):
            process = self.account_processes.get(account_ref)
            control = self.account_controls.get(account_ref)
            if process is not None and control is not None:
                process_pid = process.pid
                try:
                    self._stop_owned_child(f"account-host:{account_ref}", process, control)
                except BaseException as error:
                    cleanup_failures.append(error)
                facts = next(
                    (item for item in self._owned_children if item["pid"] == process_pid),
                    None,
                )
                if facts is not None and facts.get("alive_after_join") is False and facts.get(
                    "process_closed"
                ) is True:
                    facts["owner_refs_retained"] = False
                    self.account_processes.pop(account_ref, None)
                    self.account_controls.pop(account_ref, None)
                elif facts is not None:
                    facts["owner_refs_retained"] = True
        try:
            self._persist_lifecycle_report()
        except BaseException as error:
            self._cleanup_failures.append(
                {
                    "stage": "persist-lifecycle-report",
                    "type": type(error).__name__,
                    "message": str(error),
                }
            )
            cleanup_failures.append(error)
        if cleanup_failures:
            raise AssertionError(
                "one or more normal Host shutdowns failed: "
                + "; ".join(type(error).__name__ for error in cleanup_failures)
            ) from cleanup_failures[0]
