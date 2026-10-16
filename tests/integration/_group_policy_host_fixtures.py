"""Fixtures for the CFG06b combo-3 host acceptance slice.

Combo 3 (iteration-10 plan line 538) pairs three pieces inside one deployment:

1. an ``execution`` ``group_coordinator`` component whose trusted resource
   provider also supplies the ``group_admission`` resource,
2. a ``risk`` ``group_policy`` component whose internal
   ``service.group_admission`` port is bound to the *same* ``GroupPolicyService``
   object, and
3. a minimal real account delegation: one spawned CURVE account host running
   ``group_account_owner`` + ``group_reservations``.

The gate is a durable ``GroupPolicyStore``-backed ``GroupPolicyService``: the
policy document is pinned by the test process before the host starts, and the
spawned coordinator host opens the same sqlite file so every submission is
decided against durable state at call time.

The account-side harness (spawned account host, synthetic provider feed,
authority/proof wiring, wire readback helpers) is intentionally reused from
``tests/integration/test_arb03_dim05_group_partial_fill_service.py`` so this
module only adds the gated coordinator host and its cluster.
"""

from __future__ import annotations

import hashlib
import json
import multiprocessing
import sqlite3
import time
import uuid
from contextlib import suppress
from datetime import UTC, datetime
from decimal import Decimal
from typing import Any

import zmq
from bt_api_base.service_protocol import (
    WireEnvelope,
    WireHeader,
    WireIdentity,
    WireMessageKind,
    WireRole,
    WireScope,
    validate_reply,
)
from bt_api_execution import (
    OrderType,
    SqliteOrderGroupStore,
    risk_account_scope_fingerprint,
)
from bt_api_risk import AccountScope
from bt_api_risk.service_component import GroupPolicyService, GroupPolicyStore

from bt_api_py.service_components import ComponentResources
from bt_api_py.service_config import parse_client_config, parse_service_config
from bt_api_py.service_execution_group import (
    GroupAccountRoute,
    GroupCoordinatorTarget,
    GroupInstrumentRule,
)
from bt_api_py.service_execution_group_client import GroupOriginRunBindingStore
from bt_api_py.service_host import ServiceHost, TrustedServiceAuthority, TrustedServiceClients
from bt_api_py.service_registry import PrincipalGrant, ResourceGrant
from bt_api_py.service_runtime import (
    ClientCurveKeys,
    ServiceClientRuntime,
    ZmqTransportFactory,
)
from tests.integration import test_arb03_dim05_group_partial_fill_service as arb03

_DOMAIN = arb03._DOMAIN
_STRATEGY = arb03._STRATEGY
_RUN = arb03._RUN
_PORTFOLIO = arb03._PORTFOLIO
_COORDINATOR_SERVICE_ID = arb03._COORDINATOR_SERVICE_ID
_COORDINATOR_INSTANCE_ID = arb03._COORDINATOR_INSTANCE_ID
_COORDINATOR_PRINCIPAL = arb03._COORDINATOR_PRINCIPAL
_STRATEGY_PRINCIPAL = arb03._STRATEGY_PRINCIPAL
_COORDINATOR_SCOPE_REF = arb03._COORDINATOR_SCOPE_REF
_REQUEST_TIMEOUT_MS = arb03._REQUEST_TIMEOUT_MS

_POLICY_REF = "cfg06b-group-admission-v1"
_POLICY_INSTANCE_ID = "cfg06b-group-policy-risk"
_CLIENT_SECURITY_REF = "strategy-client-security-v1"
_COORDINATOR_SECURITY_REF = "coordinator-server-security-v1"
_OTHER_PORTFOLIO = "cfg06b-group-policy-other-portfolio"


def canonical_policy_digest(document: object) -> str:
    """The operator-visible digest rule for one group-policy document."""

    canonical = json.dumps(document, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def policy_document(*, admitted: tuple[str, ...], denied: tuple[str, ...] = ()) -> dict:
    return {"admitted_portfolios": list(admitted), "denied_group_ids": list(denied)}


def pin_group_policy(path: str, *, ref: str, admitted: tuple[str, ...], denied: tuple[str, ...] = ()) -> str:
    """Pin one policy document before any host starts; returns its version."""

    document = policy_document(admitted=admitted, denied=denied)
    version = canonical_policy_digest(document)
    store = GroupPolicyStore(path)
    try:
        record = store.pin(ref, version, document)
        assert record["policy_version"] == version
    finally:
        store.close()
    return version


def _risk_policy_component() -> dict[str, Any]:
    return {
        "config_schema_version": 2,
        "settings": {
            "instance_id": _POLICY_INSTANCE_ID,
            "bind_ports": ["service.group_admission"],
            "options": {"role": "group_policy", "group_policy_ref": _POLICY_REF},
        },
    }


def gated_coordinator_service_config(
    endpoint: str,
    server_security_ref: str,
    account_configs: dict[str, dict[str, Any]],
    account_endpoints: dict[str, str],
    accounts: tuple[str, ...],
) -> dict[str, Any]:
    """The combo-3 service: coordinator + internal-only group-policy gate."""

    raw = arb03._coordinator_service_config(
        endpoint, server_security_ref, account_configs, account_endpoints, accounts
    )
    assert raw["components"]["execution"]["settings"]["options"]["role"] == "group_coordinator"
    raw["components"]["risk"] = _risk_policy_component()
    return raw


def _gated_coordinator_host_process(
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
    policy_path: str,
    policy_ref: str,
    policy_version: str,
) -> None:
    config = parse_service_config(raw_config)
    account_configs = {
        account: parse_service_config(raw_account_configs[account]) for account in accounts
    }
    service_identity = WireIdentity(
        WireRole.SERVICE, _COORDINATOR_PRINCIPAL, _COORDINATOR_SERVICE_ID
    )
    strategy_identity = WireIdentity(WireRole.STRATEGY_CLIENT, _STRATEGY_PRINCIPAL, _STRATEGY)
    strategy_scope = arb03._stable_strategy_scope(accounts)
    account_scope_by_ref: dict[str, WireScope] = {}
    peer_by_service: dict[str, WireIdentity] = {}
    security_profiles: dict[str, ClientCurveKeys] = {}
    for account_ref in accounts:
        reservation_ref, entry_ref, query_ref = arb03._route_refs(account_ref)
        account_scope = arb03._owner_scope(account_ref)
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

    trusted_clients = TrustedServiceClients(
        scope_resolver=lambda scope_ref: account_scope_by_ref[scope_ref],
        peer_resolver=lambda service_id: peer_by_service[service_id],
        transport_factory=ZmqTransportFactory(security_profiles),
        requested_operations={
            "upstream.orders": frozenset({"orders.submit_group_leg", "orders.resolve_group_leg"}),
            "upstream.order_query": frozenset({"orders.query_group_leg"}),
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
        execution_scope = arb03._execution_scope(account_ref)
        reservation_ref, entry_ref, query_ref = arb03._route_refs(account_ref)
        instruments = {}
        for instrument, base_units in rule_specs[account_ref].items():
            metadata = arb03._metadata(instrument, metadata_reference_ns)
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
    budget_policy, budget_quote_builder = arb03._budget_binding(
        tuple(arb03._metadata(instrument, metadata_reference_ns) for instrument in budget_instruments)
    )
    coordinator_target = GroupCoordinatorTarget(
        scope_ref=_COORDINATOR_SCOPE_REF,
        accounts=route_targets,
        max_group_lifetime_ns=300_000_000_000,
        portfolio_budget_policy=budget_policy,
        budget_quote_builder=budget_quote_builder,
    )
    resources: dict[str, object] = {}
    gate_holder: dict[str, object] = {}
    provider_calls: list[tuple[str, str, str, bool, bool]] = []
    host: ServiceHost | None = None

    def resource_provider(component, instance_id, options, _scope):
        gate = gate_holder.get("gate")
        if gate is None:
            store = GroupPolicyStore(policy_path)
            gate = GroupPolicyService(store, policy_ref, policy_version)
            gate_holder["store"] = store
            gate_holder["gate"] = gate
        if component == "execution":
            group_store = SqliteOrderGroupStore(group_store_path)
            run_bindings = GroupOriginRunBindingStore(run_bindings_path)
            resources.update({"store": group_store, "run_bindings": run_bindings})
            borrowed = ComponentResources.borrowed(
                {
                    "store": group_store,
                    "targets": {strategy_scope: coordinator_target},
                    "run_bindings": run_bindings,
                    "group_admission": gate,
                },
                ready=lambda: True,
            )
            provider_calls.append(
                (
                    component,
                    instance_id,
                    str(options.get("role")),
                    borrowed.values.get("group_admission") is gate,
                    False,
                )
            )
            return borrowed
        if component == "risk":
            borrowed = ComponentResources.borrowed(
                {},
                ready=lambda: True,
                internal_ports={"service.group_admission": gate},
            )
            provider_calls.append(
                (
                    component,
                    instance_id,
                    str(options.get("role")),
                    False,
                    borrowed.internal_ports.get("service.group_admission") is gate,
                )
            )
            return borrowed
        raise AssertionError("combo host must contain exactly its fixed components")

    try:
        host = ServiceHost(
            config,
            TrustedServiceAuthority(
                identity=service_identity,
                scope=WireScope(_DOMAIN),
                scope_ref=config.scope_ref,
                scope_version="coordinator-scope-v1",
                security_ref=_COORDINATOR_SECURITY_REF,
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
                                    frozenset({"groups.submit", "groups.query"}),
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
        gate = gate_holder.get("gate")
        if type(gate) is not GroupPolicyService or gate.ready is not True:
            raise AssertionError("combo host gate is not the ready pinned service")
        control.send(
            ("ready", host.endpoint, upstream_bindings, tuple(provider_calls), gate.group_policy_ref)
        )
        while True:
            if control.poll(0):
                command = control.recv()
                if command == "close":
                    host.close()
                    control.send(("closed", host.state, upstream.state))
                    return
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
        store = gate_holder.get("store")
        close = getattr(store, "close", None)
        if callable(close):
            close()
        control.close()


class PolicyCluster:
    """Spawned minimal account host + one combo-3 coordinator host + client."""

    def __init__(
        self,
        tmp_path,
        *,
        policy_path: str,
        policy_ref: str,
        policy_version: str,
        accounts: tuple[str, ...],
        instruments: dict[str, tuple[str, ...]],
        initial_states: dict[str, tuple[str, ...]],
        expected_orders: dict[str, tuple[tuple[str, str, str, str], ...]],
        rule_specs: dict[str, dict[str, str]],
    ) -> None:
        self.accounts = accounts
        self.instruments = instruments
        self.initial_states = initial_states
        self.expected_orders = expected_orders
        self.rule_specs = rule_specs
        self.policy_path = policy_path
        self.policy_ref = policy_ref
        self.policy_version = policy_version
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
        self.account_endpoints = {account: arb03._endpoint() for account in accounts}
        self.account_server_keys = {account: zmq.curve_keypair() for account in accounts}
        self.coordinator_server_public, self.coordinator_server_secret = zmq.curve_keypair()
        self.coordinator_client_public, self.coordinator_client_secret = zmq.curve_keypair()
        self.strategy_client_public, self.strategy_client_secret = zmq.curve_keypair()
        self.account_configs = {
            account: arb03._account_service_config(
                account,
                self.account_endpoints[account],
                f"account-server-security-{account}",
            )
            for account in accounts
        }
        self.coordinator_endpoint = arb03._endpoint()
        self.coordinator_raw = gated_coordinator_service_config(
            self.coordinator_endpoint,
            _COORDINATOR_SECURITY_REF,
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
        self.provider_calls: tuple[tuple[str, str, str, bool, bool], ...] = ()
        self.gate_ref: str | None = None

    def __enter__(self) -> PolicyCluster:
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
                name=f"combo3-account-host-{account_ref}",
                target=arb03._account_host_process,
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
            startup = arb03._receive(parent_control)
            assert startup[0] == "ready", startup
            assert startup[1] == self.account_endpoints[account_ref]

        coordinator_context = multiprocessing.get_context("spawn")
        coordinator_parent, coordinator_child = coordinator_context.Pipe(duplex=True)
        self.coordinator_process = coordinator_context.Process(
            name="combo3-coordinator-host",
            target=_gated_coordinator_host_process,
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
                self.policy_path,
                self.policy_ref,
                self.policy_version,
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
        coordinator_ready = arb03._receive(self.coordinator_control)
        assert coordinator_ready[0] == "ready", coordinator_ready
        assert coordinator_ready[1] == self.coordinator_endpoint
        self.upstream_bindings = coordinator_ready[2]
        self.provider_calls = coordinator_ready[3]
        self.gate_ref = coordinator_ready[4]
        expected_upstream = (
            {("upstream.orders", account, arb03._route_refs(account)[1]) for account in self.accounts}
            | {
                ("upstream.order_query", account, arb03._route_refs(account)[2])
                for account in self.accounts
            }
            | {
                ("upstream.account_reservations", account, arb03._route_refs(account)[0])
                for account in self.accounts
            }
        )
        expected_upstream_operations = {
            "upstream.orders": frozenset({"orders.submit_group_leg", "orders.resolve_group_leg"}),
            "upstream.order_query": frozenset({"orders.query_group_leg"}),
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
            activation = arb03._receive(control)
            assert activation[0] == "activated", activation
            assert activation[1] == order_registration_ids[account_ref]
            assert type(activation[2]) is int and activation[2] > 0

        strategy_identity = WireIdentity(WireRole.STRATEGY_CLIENT, _STRATEGY_PRINCIPAL, _STRATEGY)
        coordinator_identity = WireIdentity(
            WireRole.SERVICE, _COORDINATOR_PRINCIPAL, _COORDINATOR_SERVICE_ID
        )
        strategy_runtime = ServiceClientRuntime(
            parse_client_config(
                arb03._strategy_client_config(self.coordinator_endpoint, _CLIENT_SECURITY_REF)
            ),
            identity=strategy_identity,
            scope_resolver=lambda _scope_ref: arb03._stable_strategy_scope(self.accounts),
            peer_resolver=lambda _service_id: coordinator_identity,
            transport_factory=ZmqTransportFactory(
                {
                    _CLIENT_SECURITY_REF: ClientCurveKeys(
                        server_key=self.coordinator_server_public,
                        public_key=self.strategy_client_public,
                        secret_key=self.strategy_client_secret,
                    )
                }
            ),
            run_id=_RUN,
            requested_operations={
                "broker.order_groups": frozenset({"groups.submit", "groups.query"})
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
        assert strategy_binding.operations == frozenset({"groups.submit", "groups.query"})

    def submit_reply(self, request: dict[str, Any]) -> dict[str, Any]:
        assert self.strategy_runtime is not None
        return dict(
            self.strategy_runtime.call(
                "broker.order_groups",
                "groups.submit",
                request,
                portfolio_ref=_PORTFOLIO,
            )
        )

    def query_reply(self, group_id: str) -> dict[str, Any]:
        assert self.strategy_runtime is not None
        return dict(
            self.strategy_runtime.call(
                "broker.order_groups",
                "groups.query",
                {"group_id": group_id},
                portfolio_ref=_PORTFOLIO,
            )
        )

    def submit_group(self, request: dict[str, Any]):
        from bt_api_execution import GroupOrderSnapshot

        reply = self.submit_reply(request)
        assert reply["ok"] is True, reply
        assert reply["code"] == "OK", reply
        adapter_result = reply["result"]
        assert adapter_result["found"] is True, adapter_result
        return GroupOrderSnapshot.from_payload(arb03._plain_runtime_json(adapter_result["snapshot"]))

    def status(self, account_ref: str) -> tuple[Any, ...]:
        control = self.account_controls[account_ref]
        control.send(("status",))
        status = arb03._receive(control)
        assert status[0] == "status", status
        return status

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
                arb03._stop_process(self.coordinator_process, self.coordinator_control)
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
                    arb03._stop_process(process, control)
                except BaseException as error:
                    cleanup_failures.append(error)
        if cleanup_failures:
            raise AssertionError(
                "one or more combo-3 Host shutdowns failed: "
                + "; ".join(type(error).__name__ for error in cleanup_failures)
            ) from cleanup_failures[0]


# ---------------------------------------------------------------------------
# Wire-level probe helpers (raw control envelopes, no SDK capability filter).
# ---------------------------------------------------------------------------


def probe_transport(
    *,
    endpoint: str,
    server_public: bytes,
    client_public: bytes,
    client_secret: bytes,
):
    factory = ZmqTransportFactory(
        {_CLIENT_SECURITY_REF: ClientCurveKeys(server_public, client_public, client_secret)}
    )
    profile = arb03._profile(endpoint, server=False, security_ref=_CLIENT_SECURITY_REF)
    peer = WireIdentity(WireRole.SERVICE, _COORDINATOR_PRINCIPAL, _COORDINATOR_SERVICE_ID)
    return factory(profile, peer), peer


def raw_control_request(
    transport,
    peer: WireIdentity,
    *,
    operation: str,
    payload: dict[str, Any],
    identity: WireIdentity,
    scope: WireScope,
    schema_id: str = "sdk.service.control",
) -> dict[str, Any]:
    request = WireEnvelope(
        WireHeader(
            protocol_version=2,
            kind=WireMessageKind.REQUEST,
            message_id=uuid.uuid4().hex,
            correlation_id=None,
            identity=identity,
            scope=scope,
            operation=operation,
            schema_id=schema_id,
            schema_version=1,
            sent_at_utc=datetime.now(UTC).isoformat().replace("+00:00", "Z"),
        ),
        payload,
    )
    reply = transport.request(request, timeout=_REQUEST_TIMEOUT_MS / 1000)
    validate_reply(reply, request, expected_peer=peer)
    return dict(reply.payload)


def raw_register_payload(
    *,
    client_session_id: str,
    instance_id: str,
    port: str,
    scope_ref: str,
    operations: tuple[str, ...],
) -> dict[str, Any]:
    return {
        "registration_key": hashlib.sha256(
            f"{client_session_id}:{port}:{uuid.uuid4().hex}".encode()
        ).hexdigest(),
        "client_session_id": client_session_id,
        "instance_id": instance_id,
        "port": port,
        "scope_ref": scope_ref,
        "operations": list(operations),
    }


def probe_client_config(*, endpoint: str, instance_id: str, scope_ref: str) -> dict[str, Any]:
    raw = arb03._strategy_client_config(endpoint, _CLIENT_SECURITY_REF)
    bindings = raw["clients"]["execution"]["settings"]["bindings"]
    assert len(bindings) == 1
    bindings[0]["instance_id"] = instance_id
    bindings[0]["scope_ref"] = scope_ref
    return raw


def probe_runtime(
    *,
    endpoint: str,
    instance_id: str,
    scope_ref: str,
    operations: frozenset[str],
    accounts: tuple[str, ...],
    server_public: bytes,
    client_public: bytes,
    client_secret: bytes,
) -> ServiceClientRuntime:
    coordinator_identity = WireIdentity(
        WireRole.SERVICE, _COORDINATOR_PRINCIPAL, _COORDINATOR_SERVICE_ID
    )
    return ServiceClientRuntime(
        parse_client_config(
            probe_client_config(endpoint=endpoint, instance_id=instance_id, scope_ref=scope_ref)
        ),
        identity=WireIdentity(WireRole.STRATEGY_CLIENT, _STRATEGY_PRINCIPAL, _STRATEGY),
        scope_resolver=lambda _scope_ref: arb03._stable_strategy_scope(accounts),
        peer_resolver=lambda _service_id: coordinator_identity,
        transport_factory=ZmqTransportFactory(
            {
                _CLIENT_SECURITY_REF: ClientCurveKeys(
                    server_key=server_public,
                    public_key=client_public,
                    secret_key=client_secret,
                )
            }
        ),
        run_id=_RUN,
        requested_operations={"broker.order_groups": operations},
    )


def read_run_bindings(cluster: PolicyCluster) -> tuple[Any, ...]:
    store = GroupOriginRunBindingStore(cluster.coordinator_paths["run_bindings"])
    try:
        return store.list_all()
    finally:
        store.close()


def read_group_snapshot(cluster: PolicyCluster, group_id: str):
    store = SqliteOrderGroupStore(cluster.coordinator_paths["groups"])
    try:
        return store.get(_PORTFOLIO, group_id)
    finally:
        store.close()


def tamper_policy_document(
    path: str, *, ref: str, admitted: tuple[str, ...], denied: tuple[str, ...] = ()
) -> None:
    """Rewrite the stored document while keeping the pinned version column."""

    document = policy_document(admitted=admitted, denied=denied)
    canonical = json.dumps(document, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    with sqlite3.connect(path) as connection:
        row = connection.execute(
            "SELECT policy_version FROM group_policy_versions WHERE group_policy_ref=?",
            (ref,),
        ).fetchone()
        assert row is not None
        connection.execute(
            "UPDATE group_policy_versions SET document_json=? WHERE group_policy_ref=?",
            (canonical, ref),
        )


def policy_row_count(path: str, *, ref: str) -> int:
    with sqlite3.connect(path) as connection:
        return int(
            connection.execute(
                "SELECT COUNT(*) FROM group_policy_versions WHERE group_policy_ref=?",
                (ref,),
            ).fetchone()[0]
        )


def account_provider_snapshot(cluster: PolicyCluster, account_ref: str) -> tuple[Any, ...]:
    status = cluster.status(account_ref)
    writes = status[1]
    counters = status[2]
    pending_holds = status[3]
    remaining = status[4]
    return writes, counters, pending_holds, remaining


__all__ = [
    "PolicyCluster",
    "account_provider_snapshot",
    "canonical_policy_digest",
    "gated_coordinator_service_config",
    "pin_group_policy",
    "policy_document",
    "policy_row_count",
    "probe_client_config",
    "probe_runtime",
    "probe_transport",
    "raw_control_request",
    "raw_register_payload",
    "read_group_snapshot",
    "read_run_bindings",
    "tamper_policy_document",
]
