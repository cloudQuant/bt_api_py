"""One-account N5 check over two real local ServiceHosts and ZMQ transports.

The strategy, account, coordinator, provider and risk ledger are synthetic. Both
ServiceHosts run on threads in this pytest process; this is a local component
integration check, not a LOAD01 host-topology or load acceptance run.
"""

from __future__ import annotations

import queue
import socket
import threading
import time
import traceback
from collections.abc import Mapping
from decimal import Decimal
from functools import partial
from pathlib import Path
from types import MappingProxyType
from typing import Any, Callable

import zmq
from bt_api_base.service_protocol import WireIdentity, WireRole, WireScope
from bt_api_execution import (
    ExecutionAdmissionValidatorRegistry,
    ExecutionBasicAdmissionGate,
    ExecutionScope,
    ExecutionState,
    ExecutionProviderValidatorV1,
    GroupOrderSnapshot,
    GroupOrderState,
    OrderIntent,
    OrderType,
    PositionEffect,
    ProviderObservation,
    Side,
    SqliteExecutionStore,
    TimeInForce,
    build_instrument_risk_group_budget_quote,
    group_leg_intent_id,
)
from bt_api_risk import (
    AccountScope,
    InstrumentRiskMetadata,
    InstrumentRiskRegistry,
    RiskGroupReservationState,
)

from bt_api_py.service_components import ComponentResources
from bt_api_py.service_config import parse_client_config, parse_service_config
from bt_api_py.service_execution_group import GroupCoordinatorTarget
from bt_api_py.service_execution_group_authority import (
    ExecutionGroupAuthority,
    ExecutionStoreGroupCompletionVerifier,
)
from bt_api_py.service_execution_group_client import (
    AcceptedGroupBindingStore,
    GroupOriginRunBindingStore,
)
from bt_api_py.service_execution_group_proofs import (
    GroupProofSource,
)
from bt_api_py.service_host import ServiceHost, TrustedServiceAuthority, TrustedServiceClients
from bt_api_py.service_registry import (
    PrincipalGrant,
    ResourceGrant,
    TrustedGroupReservationPolicy,
    TrustedGroupResolutionPolicy,
    TrustedGroupWritePolicy,
)
from bt_api_py.service_runtime import ClientCurveKeys, ServiceClientRuntime, ZmqTransportFactory
from tests.integration import _dim09_three_leg_fixtures as dim09
from tests.unit._load01_group_workload import (
    N5_BASE_UNIT_SCALES,
    N5_LEG_IDS,
    N5_LEG_SPECS,
    build_n5_group_request,
    query_n5_group,
    submit_n5_group,
)
from tests.unit.test_load01_group_workload import _n5_instrument_metadata
from tests.unit._load01_profiles import load01_profile


_OWNER_SERVICE_ID = "load01-n5-local-owner-service"
_COORDINATOR_SERVICE_ID = "load01-n5-local-coordinator-service"
# Reuse the durable writer identity already acquired by Dim09ThreeLegCluster
# for this same store/scope. A new identity would be fenced by the still-live
# writer lease during ServiceHost startup recovery.
_OWNER_INSTANCE = dim09._OWNER_INSTANCE
_RISK_INSTANCE = "load01-n5-local-group-risk"
_COORDINATOR_INSTANCE = "load01-n5-local-coordinator"
_STRATEGY_PRINCIPAL = "load01-n5-local-strategy-principal"
_COORDINATOR_PRINCIPAL = "load01-n5-local-coordinator-principal"
_REQUEST_TIMEOUT_MS = 5_000
_MAX_MESSAGE_BYTES = 65_536
_START_FAILURE_DIAGNOSTICS: list[Mapping[str, object]] = []


def _capture_start_error_code(exc: BaseException) -> str:
    original_classifier = _capture_start_error_code.original_classifier
    frames = traceback.extract_tb(exc.__traceback__)[-8:]
    code = getattr(exc, "code", None)
    _START_FAILURE_DIAGNOSTICS.append(
        MappingProxyType(
            {
                "exception_module": type(exc).__module__,
                "exception_type": type(exc).__qualname__,
                "exception_code": code if type(code) is str else None,
                "frames": tuple(
                    (Path(frame.filename).name, frame.lineno, frame.name)
                    for frame in frames
                ),
            }
        )
    )
    return original_classifier(exc)


def _endpoint() -> str:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as probe:
        probe.bind(("127.0.0.1", 0))
        port = probe.getsockname()[1]
    return f"tcp://127.0.0.1:{port}"


def _component(instance_id: str, ports: tuple[str, ...], role: str, **options: object) -> dict[str, Any]:
    return {
        "config_schema_version": 2,
        "settings": {
            "instance_id": instance_id,
            "bind_ports": list(ports),
            "options": {"role": role, **options},
        },
    }


def _profile(endpoint: str, *, server: bool, security_ref: str) -> dict[str, Any]:
    options: dict[str, object] = {"high_water_mark": 32, "max_message_bytes": _MAX_MESSAGE_BYTES}
    if not server:
        options["request_timeout_ms"] = _REQUEST_TIMEOUT_MS
    return {
        "backend": "zmq",
        "config_schema_version": 1,
        "endpoints": {"rpc_bind" if server else "rpc_connect": endpoint},
        "security_profile_ref": security_ref,
        "options": options,
    }


def _client_block(bindings: list[dict[str, Any]]) -> dict[str, Any]:
    return {
        "config_schema_version": 1,
        "settings": {
            "bindings": bindings,
            "options": {
                "request_timeout_ms": _REQUEST_TIMEOUT_MS,
                "heartbeat_interval_seconds": 5,
            },
        },
    }


def _owner_service_config(endpoint: str) -> dict[str, Any]:
    return {
        "service_config_schema_version": 1,
        "process_role": "service",
        "service_id": _OWNER_SERVICE_ID,
        "scope_ref": "load01-n5-local-owner-root",
        "transport_ref": "owner-listener",
        "registration_policy_ref": "load01-n5-local-owner-registration",
        "service_requirements_ref": "load01-n5-local-owner-requirements",
        "components": {
            "execution": _component(
                _OWNER_INSTANCE,
                ("service.order_entry", "service.order_query"),
                "account_group_owner",
            ),
            "risk": _component(
                _RISK_INSTANCE,
                ("service.group_reservations",),
                "group_reservations",
            ),
        },
        "communication": {
            "config_schema_version": 1,
            "profiles": {
                "owner-listener": _profile(
                    endpoint, server=True, security_ref="owner-server-security"
                )
            },
        },
    }


def _coordinator_service_config(
    endpoint: str,
    owner_endpoint: str,
    entry_scope_ref: str,
    query_scope_ref: str,
    reservation_scope_ref: str,
) -> dict[str, Any]:
    execution_bindings = [
        {
            "mount": "upstream.orders",
            "service_id": _OWNER_SERVICE_ID,
            "instance_id": _OWNER_INSTANCE,
            "scope_ref": entry_scope_ref,
            "account_ref": dim09._ACCOUNT,
            "transport_ref": "owner-upstream",
        },
        {
            "mount": "upstream.order_query",
            "service_id": _OWNER_SERVICE_ID,
            "instance_id": _OWNER_INSTANCE,
            "scope_ref": query_scope_ref,
            "account_ref": dim09._ACCOUNT,
            "transport_ref": "owner-upstream",
        },
    ]
    reservation_binding = {
        "mount": "upstream.account_reservations",
        "service_id": _OWNER_SERVICE_ID,
        "instance_id": _RISK_INSTANCE,
        "scope_ref": reservation_scope_ref,
        "account_ref": dim09._ACCOUNT,
        "transport_ref": "owner-upstream",
    }
    return {
        "service_config_schema_version": 1,
        "process_role": "service",
        "service_id": _COORDINATOR_SERVICE_ID,
        "scope_ref": "load01-n5-local-coordinator-root",
        "transport_ref": "coordinator-listener",
        "registration_policy_ref": "load01-n5-local-coordinator-registration",
        "service_requirements_ref": "load01-n5-local-coordinator-requirements",
        "components": {
            "execution": _component(
                _COORDINATOR_INSTANCE,
                ("service.order_group_entry",),
                "group_coordinator",
                portfolio_ref=dim09._PORTFOLIO,
            )
        },
        "clients": {
            "execution": _client_block(execution_bindings),
            "risk": _client_block([reservation_binding]),
        },
        "communication": {
            "config_schema_version": 1,
            "profiles": {
                "coordinator-listener": _profile(
                    endpoint, server=True, security_ref="coordinator-server-security"
                ),
                "owner-upstream": _profile(
                    owner_endpoint, server=False, security_ref="owner-client-security"
                ),
            },
        },
    }


def _strategy_client_config(
    owner_endpoint: str,
    coordinator_endpoint: str,
) -> dict[str, Any]:
    return {
        "client_config_schema_version": 1,
        "process_role": "strategy_client",
        "registration": {"strategy_ref": dim09._STRATEGY},
        "clients": {
            "execution": _client_block(
                [
                    {
                        "mount": "broker.orders",
                        "service_id": _OWNER_SERVICE_ID,
                        "instance_id": _OWNER_INSTANCE,
                        "scope_ref": "load01-n5-local-strategy-entry",
                        "account_ref": dim09._ACCOUNT,
                        "transport_ref": "owner-client",
                    },
                    {
                        "mount": "broker.order_query",
                        "service_id": _OWNER_SERVICE_ID,
                        "instance_id": _OWNER_INSTANCE,
                        "scope_ref": "load01-n5-local-strategy-query",
                        "account_ref": dim09._ACCOUNT,
                        "transport_ref": "owner-client",
                    },
                    {
                        "mount": "broker.order_groups",
                        "service_id": _COORDINATOR_SERVICE_ID,
                        "instance_id": _COORDINATOR_INSTANCE,
                        "scope_ref": dim09._COORDINATOR_SCOPE_REF,
                        "portfolio_ref": dim09._PORTFOLIO,
                        "transport_ref": "coordinator-client",
                    },
                ]
            )
        },
        "communication": {
            "config_schema_version": 1,
            "profiles": {
                "owner-client": _profile(
                    owner_endpoint, server=False, security_ref="owner-client-security"
                ),
                "coordinator-client": _profile(
                    coordinator_endpoint,
                    server=False,
                    security_ref="coordinator-client-security",
                ),
            },
        },
    }


class _OrdinarySyntheticDispatch:
    """The ordinary order path gets a fake provider leaf; group uses DIM09 proof."""

    def __init__(self) -> None:
        self.submitted: list[str] = []
        self.cancel_calls: list[object] = []

    def submit(self, intent: OrderIntent) -> ProviderObservation:
        self.submitted.append(intent.intent_id)
        return ProviderObservation.accepted(intent.intent_id, f"local-{intent.intent_id}")

    def submit_authorized(
        self, intent: OrderIntent, _authorization: object
    ) -> ProviderObservation:
        return self.submit(intent)

    def cancel(self, intent: object) -> object:
        self.cancel_calls.append(intent)
        raise AssertionError("the local Host scenario does not exercise provider cancellation")

    def recover_interrupted_dispatches(self) -> tuple[object, ...]:
        return ()


class _HostThread:
    """Own a ServiceHost on one thread and close it on that same thread."""

    def __init__(
        self,
        factory: Callable[[], ServiceHost],
        *,
        name: str,
    ) -> None:
        self._factory = factory
        self._stop = threading.Event()
        self._ready = threading.Event()
        self._closed: queue.Queue[tuple[str, object]] = queue.Queue()
        self.host: ServiceHost | None = None
        self.thread = threading.Thread(target=self._run, name=name, daemon=False)
        self.thread.start()
        if not self._ready.wait(20):
            self._raise_after_start_failure(
                name,
                AssertionError(f"{name} ServiceHost did not start within 20 seconds"),
            )
        if not self._closed.empty():
            status, value = self._closed.get_nowait()
            if status == "failed":
                self._raise_after_start_failure(
                    name,
                    AssertionError(f"{name} ServiceHost startup failed: {value!r}"),
                )

    def _raise_after_start_failure(self, name: str, failure: AssertionError) -> None:
        cleanup_errors: list[str] = []
        try:
            self._stop.set()
        except BaseException as error:
            cleanup_errors.append(f"stop signal raised {type(error).__name__}")
        try:
            self.thread.join(20)
        except BaseException as error:
            cleanup_errors.append(f"bounded join raised {type(error).__name__}")
        try:
            alive = self.thread.is_alive()
        except BaseException as error:
            alive = True
            cleanup_errors.append(f"thread liveness check raised {type(error).__name__}")
        if alive:
            cleanup_errors.append("owner thread remained alive after bounded join")
        if cleanup_errors:
            raise AssertionError(
                f"{name} ServiceHost startup cleanup_uncertain: "
                + "; ".join(cleanup_errors)
            ) from failure
        raise failure

    def _run(self) -> None:
        host: ServiceHost | None = None
        try:
            host = self._factory()
            self.host = host
            host.start()
            self._ready.set()
            while not self._stop.is_set():
                host.serve_once(10)
            host.close()
            self._closed.put(("closed", host.state))
        except BaseException as error:
            summary = host.summary if host is not None else {}
            self._closed.put(
                (
                    "failed",
                    {
                        "error_type": type(error).__name__,
                        "error_code": getattr(error, "code", None),
                        "host_state": host.state if host is not None else None,
                        "start_failure": summary.get("start_failure"),
                        "cleanup_failures": tuple(summary.get("cleanup_failures", ())),
                        "endpoint_bound": host.endpoint is not None if host is not None else False,
                        "start_exception": tuple(_START_FAILURE_DIAGNOSTICS[-1:]),
                    },
                )
            )
            self._ready.set()
            if host is not None and host.state not in {"closed", "failed"}:
                try:
                    host.close()
                except BaseException as close_error:
                    self._closed.put(("close_failed", close_error))

    def close_normally(self) -> None:
        self._stop.set()
        self.thread.join(20)
        assert not self.thread.is_alive(), "ServiceHost owner thread did not terminate"
        outcomes: list[tuple[str, object]] = []
        while not self._closed.empty():
            outcomes.append(self._closed.get_nowait())
        assert outcomes and outcomes[-1] == ("closed", "closed"), outcomes
        assert self.host is not None and self.host.state == "closed"


def test_host_thread_start_failure_stops_and_joins(monkeypatch) -> None:
    import sys
    from types import SimpleNamespace

    import pytest

    module = sys.modules[__name__]

    def install_fake_threading(*, run_target: bool, terminate_on_join: bool):
        trace: list[str] = []
        events: list[object] = []
        threads: list[object] = []

        class FakeEvent:
            def __init__(self) -> None:
                self.index = len(events)
                self.signaled = False
                self.set_calls = 0
                self.wait_calls: list[float] = []

            def set(self) -> None:
                self.set_calls += 1
                self.signaled = True
                trace.append(f"event{self.index}.set")

            def wait(self, timeout: float | None = None) -> bool:
                self.wait_calls.append(timeout)
                trace.append(f"event{self.index}.wait")
                return self.signaled

        class FakeThread:
            def __init__(self, *, target, name: str, daemon: bool) -> None:
                assert daemon is False
                self.target = target
                self.name = name
                self.alive = False
                self.join_calls: list[float] = []
                threads.append(self)

            def start(self) -> None:
                trace.append("thread.start")
                self.alive = True
                if run_target:
                    try:
                        self.target()
                    finally:
                        self.alive = False

            def join(self, timeout: float | None = None) -> None:
                trace.append("thread.join")
                self.join_calls.append(timeout)
                if terminate_on_join:
                    self.alive = False

            def is_alive(self) -> bool:
                trace.append("thread.is_alive")
                return self.alive

        def event_factory() -> FakeEvent:
            event = FakeEvent()
            events.append(event)
            return event

        monkeypatch.setattr(
            module,
            "threading",
            SimpleNamespace(Event=event_factory, Thread=FakeThread),
        )
        return trace, events, threads

    timeout_trace, timeout_events, timeout_threads = install_fake_threading(
        run_target=False,
        terminate_on_join=False,
    )
    with pytest.raises(AssertionError, match="cleanup_uncertain") as timeout_failure:
        _HostThread(lambda: None, name="fake-timeout-host")
    assert timeout_events[0].set_calls == 1
    assert timeout_events[1].wait_calls == [20]
    assert timeout_threads[0].join_calls == [20]
    assert timeout_threads[0].alive is True
    assert timeout_trace.index("event0.set") < timeout_trace.index("thread.join")
    assert "did not start within 20 seconds" in str(timeout_failure.value.__cause__)

    failed_trace, failed_events, failed_threads = install_fake_threading(
        run_target=True,
        terminate_on_join=True,
    )

    def fail_during_host_construction():
        raise RuntimeError("synthetic startup failure")

    with pytest.raises(AssertionError, match="ServiceHost startup failed") as start_failure:
        _HostThread(fail_during_host_construction, name="fake-failed-host")
    assert failed_events[0].set_calls == 1
    assert failed_events[1].wait_calls == [20]
    assert failed_threads[0].join_calls == [20]
    assert failed_threads[0].alive is False
    assert failed_trace.index("event0.set") < failed_trace.index("thread.join")
    assert "RuntimeError" in str(start_failure.value)


def _plain_wire_json(value: object) -> object:
    if isinstance(value, Mapping):
        if any(type(key) is not str for key in value):
            raise TypeError("wire JSON object keys must be text")
        return {key: _plain_wire_json(item) for key, item in value.items()}
    if type(value) is tuple:
        return [_plain_wire_json(item) for item in value]
    return value


def _topology_builders():
    import tests.unit._load01_group_topology as topology

    expected_origin = (
        Path(__file__).resolve().parents[2]
        / "tests"
        / "unit"
        / "_load01_group_topology.py"
    )
    assert Path(topology.__file__).resolve() == expected_origin.resolve()
    return topology.build_n5_owner_group_resources, topology.build_n5_coordinator_resources


def test_n5_group_and_ordinary_orders_cross_real_local_hosts(tmp_path, monkeypatch) -> None:
    """Use real registry/client routing and two local Hosts for one N5 owner."""

    _START_FAILURE_DIAGNOSTICS.clear()
    _capture_start_error_code.original_classifier = ServiceHost._start_error_code
    monkeypatch.setattr(
        ServiceHost,
        "_start_error_code",
        staticmethod(_capture_start_error_code),
    )
    metadata = _n5_instrument_metadata()
    (tmp_path / "graph").mkdir()
    expected_provider_orders = tuple(
        (spec.instrument, spec.quantity, spec.price, "buy-limit" if spec.side is Side.BUY else "sell-limit")
        for spec in N5_LEG_SPECS
    )
    cluster = dim09.Dim09ThreeLegCluster(
        tmp_path / "graph",
        monkeypatch,
        instrument_metadata=metadata,
        base_units_per_order_unit_by_instrument=dict(N5_BASE_UNIT_SCALES),
        expected_provider_orders=expected_provider_orders,
        provider_exchange_name="FIXTURE___SPOT",
        mapper_exchange_name="OKX___SWAP",
    )
    owner_host_thread: _HostThread | None = None
    coordinator_host_thread: _HostThread | None = None
    strategy_runtime: ServiceClientRuntime | None = None
    new_authority: ExecutionGroupAuthority | None = None
    dispatch = _OrdinarySyntheticDispatch()
    try:
        # Retire only the cluster's direct in-process service adapters; its
        # typed graph, proof bundle inputs and SQLite stores remain borrowed by
        # the real ServiceHosts below.
        for closer in reversed(cluster._extra_closers):
            closer()
        cluster._extra_closers.clear()
        cluster.reservation_service.close()

        now_ns = time.time_ns()
        scope = cluster.execution_scope
        owner_scope = cluster.owner_scope
        strategy_scope = cluster.strategy_template
        assert len({item.instrument for item in metadata}) == 3
        assert scope.provider == "fixture" and scope.environment == "simulation"
        proof_target = cluster.proof_source.targets[owner_scope]
        bundle = cluster.proof_source.runtime_bundle
        assert bundle is not None, "DIM09 graph must retain its already-bound group bundle"
        bundle.require_runtime_bundle(
            account_scoped_admission=cluster.account_admission,
            risk_admission=cluster.risk_admission,
        )
        targets = MappingProxyType({owner_scope: scope})
        new_authority = ExecutionGroupAuthority(
            tmp_path / "n5-host-authority.sqlite3",
            completion_verifier=ExecutionStoreGroupCompletionVerifier(
                cluster.execution_store, execution_scopes=(scope,)
            ),
        )

        owner_identity = WireIdentity(
            WireRole.SERVICE, "load01-n5-local-owner-principal", _OWNER_SERVICE_ID
        )
        entry_scope_ref = cluster.entry_scope_ref
        query_scope_ref = cluster.query_scope_ref
        reservation_scope_ref = cluster.reservation_scope_ref
        coordinator_scope_ref = dim09._COORDINATOR_SCOPE_REF
        coordinator_identity = WireIdentity(
            WireRole.SERVICE, _COORDINATOR_PRINCIPAL, _COORDINATOR_SERVICE_ID
        )
        strategy_identity = WireIdentity(
            WireRole.STRATEGY_CLIENT, _STRATEGY_PRINCIPAL, dim09._STRATEGY
        )
        owner_scope_ref_ordinary_entry = "load01-n5-local-strategy-entry"
        owner_scope_ref_ordinary_query = "load01-n5-local-strategy-query"
        coordinator_client_public, coordinator_client_secret = zmq.curve_keypair()
        strategy_client_public, strategy_client_secret = zmq.curve_keypair()
        owner_server_public, owner_server_secret = zmq.curve_keypair()
        coordinator_server_public, coordinator_server_secret = zmq.curve_keypair()
        owner_endpoint = _endpoint()
        coordinator_endpoint = _endpoint()

        owner_raw = _owner_service_config(owner_endpoint)
        owner_config = parse_service_config(owner_raw)
        group_write_policy = TrustedGroupWritePolicy(
            _OWNER_INSTANCE,
            _COORDINATOR_PRINCIPAL,
            coordinator_identity,
            entry_scope_ref,
            owner_scope,
            dim09._RUN,
        )
        group_resolution_policy = TrustedGroupResolutionPolicy(
            _OWNER_INSTANCE,
            _COORDINATOR_PRINCIPAL,
            coordinator_identity,
            entry_scope_ref,
            owner_scope,
            dim09._RUN,
            dim09._ACCOUNT,
        )
        group_reservation_policy = TrustedGroupReservationPolicy(
            _RISK_INSTANCE,
            _COORDINATOR_PRINCIPAL,
            coordinator_identity,
            reservation_scope_ref,
            owner_scope,
            dim09._RUN,
            dim09._ACCOUNT,
        )

        ordinary_entry_grant = ResourceGrant(
            _OWNER_INSTANCE,
            "service.order_entry",
            owner_scope_ref_ordinary_entry,
            owner_scope,
            frozenset({"orders.submit", "orders.cancel"}),
        )
        ordinary_query_grant = ResourceGrant(
            _OWNER_INSTANCE,
            "service.order_query",
            owner_scope_ref_ordinary_query,
            owner_scope,
            frozenset({"orders.query"}),
        )
        owner_grants = {
            _STRATEGY_PRINCIPAL: (
                PrincipalGrant(strategy_identity, (ordinary_entry_grant, ordinary_query_grant)),
            ),
            _COORDINATOR_PRINCIPAL: (
                PrincipalGrant(
                    coordinator_identity,
                    (
                        ResourceGrant(
                            _OWNER_INSTANCE,
                            "service.order_entry",
                            entry_scope_ref,
                            owner_scope,
                            frozenset({"orders.submit_group_leg", "orders.resolve_group_leg"}),
                        ),
                        ResourceGrant(
                            _OWNER_INSTANCE,
                            "service.order_query",
                            query_scope_ref,
                            owner_scope,
                            frozenset({"orders.query_group_leg"}),
                        ),
                        ResourceGrant(
                            _RISK_INSTANCE,
                            "service.group_reservations",
                            reservation_scope_ref,
                            owner_scope,
                            frozenset(
                                {"reservations.reserve", "reservations.query", "reservations.release"}
                            ),
                        ),
                    ),
                ),
            ),
        }
        build_owner_resources, build_coordinator_resources = _topology_builders()
        basic_gate = ExecutionBasicAdmissionGate(
            cluster.execution_store,
            ExecutionAdmissionValidatorRegistry(
                (
                    ExecutionProviderValidatorV1(
                        provider_id=scope.provider,
                        validator_id="load01-n5-local-basic-v1",
                        version="1",
                        dispatch_port=dispatch,
                        instrument_map={item.instrument: item.instrument for item in metadata},
                        quantity_steps={item.instrument: item.quantity_step for item in metadata},
                        max_quantities={item.instrument: Decimal("100") for item in metadata},
                        price_ticks={item.instrument: item.tick_size for item in metadata},
                        supported_order_types=(OrderType.LIMIT,),
                        supported_time_in_force=(TimeInForce.GTC,),
                        supported_actions=(
                            (Side.BUY, PositionEffect.OPEN),
                            (Side.SELL, PositionEffect.OPEN),
                        ),
                        supports_reduce_only=False,
                    ),
                )
            ),
        )
        owner_graph = build_owner_resources(
            store=cluster.execution_store,
            targets=targets,
            dispatch=dispatch,
            admission={"basic_admission_gate": basic_gate},
            accepted_group_store=cluster.accepted_store,
            group_authority=new_authority.guard,
            group_runtime_bundle=bundle,
        )

        def owner_resource_provider(component, _instance_id, _options, _wire_scope):
            if component == "execution":
                return ComponentResources.borrowed(
                    owner_graph.execution_component_resources, ready=lambda: True
                )
            if component == "risk":
                return ComponentResources.borrowed(
                    owner_graph.reservation_component_resources, ready=lambda: True
                )
            raise AssertionError(f"unexpected owner component {component!r}")

        owner_authority = TrustedServiceAuthority(
            identity=owner_identity,
            scope=WireScope(owner_scope.execution_domain_ref),
            scope_ref=owner_config.scope_ref,
            scope_version="load01-n5-owner-scope-v1",
            security_ref="owner-server-security",
            security_version="load01-n5-owner-curve-v1",
            registration_policy_ref=owner_config.registration_policy_ref,
            registration_policy_version="load01-n5-owner-registration-v1",
            service_requirements_ref=owner_config.service_requirements_ref,
            service_requirements_version="load01-n5-owner-requirements-v1",
            curve_public_key=owner_server_public,
            curve_secret_key=owner_server_secret,
            principals={
                coordinator_client_public: _COORDINATOR_PRINCIPAL,
                strategy_client_public: _STRATEGY_PRINCIPAL,
            },
            grants=owner_grants,
            group_write_policies=(group_write_policy,),
            group_resolution_policies=(group_resolution_policy,),
            group_reservation_policies=(group_reservation_policy,),
        )

        owner_host_thread = _HostThread(
            lambda: ServiceHost(
                owner_config,
                owner_authority,
                tmp_path / "owner-registry.sqlite3",
                owner_resource_provider,
            ),
            name="load01-n5-owner-host",
        )
        assert owner_host_thread.host is not None

        coordinator_raw = _coordinator_service_config(
            coordinator_endpoint,
            owner_endpoint,
            entry_scope_ref,
            query_scope_ref,
            reservation_scope_ref,
        )
        coordinator_config = parse_service_config(coordinator_raw)
        coordinator_target = cluster.coordinator_target
        coordinator_graph = build_coordinator_resources(
            group_store=cluster.coordinator_store,
            run_bindings=cluster.origin_run_bindings,
            targets=MappingProxyType({strategy_scope: coordinator_target}),
            expected_target_count=1,
        )

        def coordinator_resource_provider(component, _instance_id, _options, _wire_scope):
            if component != "execution":
                raise AssertionError(f"unexpected coordinator component {component!r}")
            return ComponentResources.borrowed(
                coordinator_graph.component_resources, ready=lambda: True
            )

        coordinator_authority = TrustedServiceAuthority(
            identity=coordinator_identity,
            scope=WireScope(strategy_scope.execution_domain_ref),
            scope_ref=coordinator_config.scope_ref,
            scope_version="load01-n5-coordinator-scope-v1",
            security_ref="coordinator-server-security",
            security_version="load01-n5-coordinator-curve-v1",
            registration_policy_ref=coordinator_config.registration_policy_ref,
            registration_policy_version="load01-n5-coordinator-registration-v1",
            service_requirements_ref=coordinator_config.service_requirements_ref,
            service_requirements_version="load01-n5-coordinator-requirements-v1",
            curve_public_key=coordinator_server_public,
            curve_secret_key=coordinator_server_secret,
            principals={strategy_client_public: _STRATEGY_PRINCIPAL},
            grants={
                _STRATEGY_PRINCIPAL: (
                    PrincipalGrant(
                        strategy_identity,
                        (
                            ResourceGrant(
                                _COORDINATOR_INSTANCE,
                                "service.order_group_entry",
                                coordinator_scope_ref,
                                strategy_scope,
                                frozenset({"groups.submit", "groups.query"}),
                            ),
                        ),
                    ),
                )
            },
        )
        upstream_scopes = {
            entry_scope_ref: owner_scope,
            query_scope_ref: owner_scope,
            reservation_scope_ref: owner_scope,
        }
        coordinator_owner_keys = {
            "owner-client-security": ClientCurveKeys(
                server_key=owner_server_public,
                public_key=coordinator_client_public,
                secret_key=coordinator_client_secret,
            )
        }
        trusted_clients = TrustedServiceClients(
            scope_resolver=lambda scope_ref: upstream_scopes[scope_ref],
            peer_resolver=lambda service_id: owner_identity
            if service_id == _OWNER_SERVICE_ID
            else (_ for _ in ()).throw(AssertionError(f"unexpected peer {service_id!r}")),
            transport_factory=ZmqTransportFactory(coordinator_owner_keys),
            requested_operations={
                "upstream.orders": frozenset(
                    {"orders.submit_group_leg", "orders.resolve_group_leg"}
                ),
                "upstream.order_query": frozenset({"orders.query_group_leg"}),
                "upstream.account_reservations": frozenset(
                    {"reservations.reserve", "reservations.query", "reservations.release"}
                ),
            },
        )
        owner_config_directory = {
            owner_config.service_id: owner_config,
            coordinator_config.service_id: coordinator_config,
        }
        coordinator_host_thread = _HostThread(
            lambda: ServiceHost(
                coordinator_config,
                coordinator_authority,
                tmp_path / "coordinator-registry.sqlite3",
                coordinator_resource_provider,
                service_directory=owner_config_directory,
                trusted_clients=trusted_clients,
            ),
            name="load01-n5-coordinator-host",
        )
        assert coordinator_host_thread.host is not None
        upstream = coordinator_host_thread.host.upstream
        assert upstream is not None and upstream.healthy is True
        group_entry_binding = next(
            binding
            for binding in upstream.bindings
            if binding.active
            and binding.mount == "upstream.orders"
            and binding.scope_ref == entry_scope_ref
        )
        owner_registry = owner_host_thread.host._registry
        assert owner_registry is not None
        active_owner_contexts = owner_registry.active_registration_contexts(
            instance_id=_OWNER_INSTANCE,
            port="service.order_entry",
        )
        owner_registration = next(
            item
            for item in active_owner_contexts
            if item.context.registration_id == group_entry_binding.registration_id
        )
        generation = new_authority.control.activate(
            owner_registration.context,
            execution_scope=scope,
            risk_account_scope_key=cluster.risk_scope.key,
        )
        assert generation.registration_id == group_entry_binding.registration_id

        strategy_raw = _strategy_client_config(owner_endpoint, coordinator_endpoint)
        owner_identity_by_service = {
            _OWNER_SERVICE_ID: owner_identity,
            _COORDINATOR_SERVICE_ID: coordinator_identity,
        }
        security_profiles = {
            "owner-client-security": ClientCurveKeys(
                server_key=owner_server_public,
                public_key=strategy_client_public,
                secret_key=strategy_client_secret,
            ),
            "coordinator-client-security": ClientCurveKeys(
                server_key=coordinator_server_public,
                public_key=strategy_client_public,
                secret_key=strategy_client_secret,
            ),
        }
        strategy_runtime = ServiceClientRuntime(
            parse_client_config(strategy_raw),
            identity=strategy_identity,
            scope_resolver=lambda scope_ref: (
                strategy_scope
                if scope_ref == coordinator_scope_ref
                else owner_scope
            ),
            peer_resolver=lambda service_id: owner_identity_by_service[service_id],
            transport_factory=ZmqTransportFactory(security_profiles),
            client_session_id="load01-n5-local-host-session",
            run_id=dim09._RUN,
            requested_operations={
                "broker.orders": frozenset({"orders.submit"}),
                "broker.order_query": frozenset({"orders.query"}),
                "broker.order_groups": frozenset({"groups.submit", "groups.query"}),
            },
        )
        start_receipt = strategy_runtime.start()
        assert start_receipt.degraded_count == 0
        assert len(start_receipt.bindings) == 3

        ordinary_id = "load01-n5-local-ordinary-order"
        ordinary_intent = OrderIntent.limit(
            intent_id=ordinary_id,
            scope=scope,
            signal_id="load01-n5-local-ordinary-signal",
            instrument=N5_LEG_SPECS[0].instrument,
            side=Side.BUY,
            quantity=Decimal("1"),
            price=Decimal("10"),
            time_in_force=TimeInForce.GTC,
            metadata_version=metadata[0].metadata_version,
            deadline_ns=now_ns + 60_000_000_000,
            tags={},
        )
        ordinary_args = ordinary_intent.to_payload()
        ordinary_args.pop("scope")
        ordinary_submit = strategy_runtime.call(
            "broker.orders",
            "orders.submit",
            ordinary_args,
            account_ref=dim09._ACCOUNT,
            scope_ref=owner_scope_ref_ordinary_entry,
        )
        assert ordinary_submit["ok"] is True and ordinary_submit["code"] == "OK", ordinary_submit
        assert ordinary_submit["result"]["found"] is True
        ordinary_query = strategy_runtime.call(
            "broker.order_query",
            "orders.query",
            {"intent_id": ordinary_id},
            account_ref=dim09._ACCOUNT,
            scope_ref=owner_scope_ref_ordinary_query,
        )
        assert ordinary_query["ok"] is True and ordinary_query["code"] == "OK", ordinary_query
        assert ordinary_query["result"]["found"] is True
        assert ordinary_query["result"]["record"]["intent_id"] == ordinary_id
        assert ordinary_query["result"]["record"]["state"] == ExecutionState.ACKED.value
        assert dispatch.submitted == [ordinary_id]
        assert dispatch.cancel_calls == []

        profile = load01_profile("N5")
        group_id = "load01-n5-local-group-cycle1"
        body = build_n5_group_request(
            group_id=group_id,
            account_ref=dim09._ACCOUNT,
            scope=scope,
            metadata=metadata,
            deadline_ns=cluster.deadline_ns,
        )
        submitted = submit_n5_group(
            strategy_runtime,
            profile,
            strategy_index=0,
            cycle=1,
            request=body,
            account_ref=dim09._ACCOUNT,
            portfolio_ref=dim09._PORTFOLIO,
            scope_ref=coordinator_scope_ref,
        )
        assert submitted["ok"] is True and submitted["code"] == "OK", submitted
        queried = query_n5_group(
            strategy_runtime,
            profile,
            strategy_index=0,
            cycle=1,
            group_id=group_id,
            portfolio_ref=dim09._PORTFOLIO,
            scope_ref=coordinator_scope_ref,
        )
        assert queried["ok"] is True and queried["code"] == "OK", queried
        snapshot = GroupOrderSnapshot.from_payload(
            _plain_wire_json(queried["result"]["snapshot"])
        )
        assert snapshot.state is GroupOrderState.PAIRED
        assert snapshot.residual_vector == (Decimal("0"), Decimal("0"), Decimal("0"))
        assert snapshot.residual_base_units == Decimal("0")
        assert cluster.provider.position_mode_query_count == 0
        assert len(snapshot.legs) == 3
        assert all(leg.child_status is not None for leg in snapshot.legs)
        assert all(leg.child_status.state is ExecutionState.FILLED for leg in snapshot.legs)
        assert [leg.leg_id for leg in snapshot.legs] == list(N5_LEG_IDS)

        durable_intents = []
        for spec in N5_LEG_SPECS:
            intent_id = group_leg_intent_id(group_id, spec.leg_id)
            durable_intent = cluster.execution_store.get_intent(intent_id, scope=scope)
            durable_record = cluster.execution_store.get(intent_id, scope=scope)
            assert durable_intent is not None and durable_record is not None
            assert durable_intent.instrument == spec.instrument
            assert durable_intent.quantity == spec.quantity
            assert durable_intent.price == spec.price
            assert durable_record.state is ExecutionState.FILLED
            assert durable_record.group_binding is not None
            assert durable_record.group_binding.group_id == group_id
            durable_intents.append(durable_intent)
        assert len({intent.instrument for intent in durable_intents}) == 3

        risk_vector = cluster.risk_admission.read_group_vector_snapshot(
            cluster.risk_scope, group_id
        )
        assert len(risk_vector.legs) == 3
        assert {item.state for item in risk_vector.legs} == {
            RiskGroupReservationState.SETTLED
        }
        provider_writes, provider_counters = cluster.provider.snapshot()
        assert len(provider_writes) == 3 and len(set(provider_writes)) == 3
        assert provider_counters == {"order_queries": 3, "fill_queries": 3}
    finally:
        import sys

        primary_error = sys.exc_info()[1]
        cleanup_errors: list[BaseException] = []
        for cleanup in (
            strategy_runtime.close if strategy_runtime is not None else None,
            coordinator_host_thread.close_normally
            if coordinator_host_thread is not None
            else None,
            owner_host_thread.close_normally if owner_host_thread is not None else None,
            new_authority.close if new_authority is not None else None,
            cluster.close,
        ):
            if cleanup is None:
                continue
            try:
                cleanup()
            except BaseException as error:
                cleanup_errors.append(error)
        if strategy_runtime is not None and strategy_runtime.state != "closed":
            cleanup_errors.append(
                AssertionError(f"strategy runtime close state is {strategy_runtime.state!r}")
            )
        if new_authority is not None and new_authority._closed is not True:
            cleanup_errors.append(AssertionError("execution group authority remained open"))
        if cleanup_errors:
            if primary_error is not None:
                for error in cleanup_errors:
                    primary_error.add_note(
                        f"cleanup also failed: {type(error).__name__}: {error}"
                    )
            else:
                raise AssertionError(
                    "one or more N5 local Host resources failed normal close: "
                    + ", ".join(type(error).__name__ for error in cleanup_errors)
                ) from cleanup_errors[0]
