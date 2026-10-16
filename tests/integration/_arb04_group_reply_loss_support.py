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

import multiprocessing
import time
from collections.abc import Mapping
from contextlib import suppress
from decimal import Decimal
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
)
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

    def __init__(self, operation: str, remaining_drops: int) -> None:
        assert type(operation) is str and operation
        assert type(remaining_drops) is int and remaining_drops >= 0
        self.operation = operation
        self.remaining = remaining_drops
        self.dropped = 0
        self.counts: dict[tuple[str, str], int] = {}

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


class _ReplyLossTransport:
    """Forward one request to the real transport, then drop the armed reply."""

    def __init__(
        self, inner: Any, state: _WireFaultState, account_ref: str
    ) -> None:
        self._inner = inner
        self._state = state
        self._account_ref = account_ref

    def request(self, request: WireEnvelope, *, timeout: float) -> WireEnvelope:
        operation = request.header.operation
        self._state.count(self._account_ref, operation)
        reply = self._inner.request(request, timeout=timeout)
        if operation == self._state.operation and self._state.remaining > 0:
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
    ) -> None:
        self._inner = inner
        self._state = state
        self._account_by_endpoint = dict(account_by_endpoint)

    def __call__(self, profile: Mapping[str, Any], expected_peer: WireIdentity) -> Any:
        endpoint = profile["endpoints"].get("rpc_connect")
        account_ref = self._account_by_endpoint.get(str(endpoint), str(endpoint))
        return _ReplyLossTransport(self._inner(profile, expected_peer), self._state, account_ref)


def _arb04_coordinator_host_process(
    control,
    raw_config: dict[str, Any],
    raw_account_configs: dict[str, dict[str, Any]],
    registry_path: str,
    group_store_path: str,
    run_bindings_path: str,
    account_endpoints: dict[str, str],
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

    fault_state = _WireFaultState(fault_operation, fault_drops)
    trusted_clients = TrustedServiceClients(
        scope_resolver=lambda scope_ref: account_scope_by_ref[scope_ref],
        peer_resolver=lambda service_id: peer_by_service[service_id],
        transport_factory=_ReplyLossTransportFactory(
            ZmqTransportFactory(security_profiles),
            fault_state,
            {endpoint: account_ref for account_ref, endpoint in account_endpoints.items()},
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
        enable_group_cancel: bool = False,
    ) -> None:
        self.accounts = accounts
        self.instruments = instruments
        self.initial_states = initial_states
        self.expected_orders = expected_orders
        self.rule_specs = rule_specs
        self.fault_operation = fault_operation
        self.fault_drops = fault_drops
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
        except BaseException:
            with suppress(BaseException):
                self.close()
            raise
        return self

    def __exit__(self, _exc_type, _exc, _traceback) -> bool:
        self.close()
        return False

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
                ),
            )
            try:
                process.start()
            except BaseException:
                child_control.close()
                parent_control.close()
                raise
            child_control.close()
            self.account_processes[account_ref] = process
            self.account_controls[account_ref] = parent_control
            startup = _receive(parent_control)
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
                self.enable_group_cancel,
            ),
        )
        try:
            self.coordinator_process.start()
        except BaseException:
            coordinator_child.close()
            coordinator_parent.close()
            self.coordinator_control = None
            self.coordinator_process = None
            raise
        coordinator_child.close()
        self.coordinator_control = coordinator_parent
        coordinator_ready = _receive(self.coordinator_control)
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
            control.send(("activate", order_registration_ids[account_ref]))
            activation = _receive(control)
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
        strategy_receipt = strategy_runtime.start()
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

    def arm_fault(self, operation: str, drops: int) -> None:
        control = self.coordinator_control
        assert control is not None
        control.send(("arm-fault", operation, drops))
        result = _receive(control)
        assert result == ("fault-armed", operation, drops), result

    def close(self) -> None:
        cleanup_failures: list[BaseException] = []
        if self.strategy_runtime is not None and self.strategy_started:
            runtime = self.strategy_runtime
            try:
                runtime.close()
                assert runtime.state == "closed"
                assert runtime.unregister_uncertainties == 0
            except BaseException as error:
                cleanup_failures.append(error)
            finally:
                self.strategy_started = False
        if self.coordinator_process is not None and self.coordinator_control is not None:
            try:
                _stop_process(self.coordinator_process, self.coordinator_control)
            except BaseException as error:
                cleanup_failures.append(error)
            finally:
                self.coordinator_process = None
                self.coordinator_control = None
        for account_ref in reversed(self.accounts):
            process = self.account_processes.pop(account_ref, None)
            control = self.account_controls.pop(account_ref, None)
            if process is not None and control is not None:
                try:
                    _stop_process(process, control)
                except BaseException as error:
                    cleanup_failures.append(error)
        if cleanup_failures:
            raise AssertionError(
                "one or more normal Host shutdowns failed: "
                + "; ".join(type(error).__name__ for error in cleanup_failures)
            ) from cleanup_failures[0]
