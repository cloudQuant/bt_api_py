"""Reusable real Risk ServiceHost fixture for DIM05 group roll/exit tests.

The group and origin proof must already be persisted by the caller.  This
fixture only supplies the Risk owner Host and synthetic provider leaf; it does
not fabricate a Risk reply or seed group authority.
"""

from __future__ import annotations

import queue
import socket
import threading
import time
from contextlib import contextmanager
from dataclasses import dataclass, replace
from decimal import Decimal
from pathlib import Path
from typing import Any, Callable, Iterator, Mapping

import zmq
from bt_api_base.service_protocol import WireIdentity, WireRole, WireScope
from bt_api_execution import SqliteOrderGroupStore, risk_account_scope_fingerprint
from bt_api_risk import (
    AccountScope,
    DurableRiskAdmissionAdapter,
    DurableRiskGate,
    InstrumentRiskAdmissionMapper,
    InstrumentRiskRegistry,
)

from bt_api_py.bt_api import BtApi
from bt_api_py.service_components import ComponentResources
from bt_api_py.service_config import parse_service_config
from bt_api_py.service_execution_group_client import GroupOriginRunBindingStore
from bt_api_py.service_execution_group_roll_exit_source import (
    GroupRollExitAuthorizationSourceV1,
)
from bt_api_py.service_host import ServiceHost, TrustedServiceAuthority
from bt_api_py.service_registry import (
    PrincipalGrant,
    ResourceGrant,
    TrustedGroupWritePolicy,
)
from bt_api_py.service_risk_order_entry import RiskOrderTarget
from bt_api_py.service_risk_order_journal import RiskOrderJournal

from tests.integration import test_arb03_dim05_group_partial_fill_service as dim05
from tests.unit._service_group_provider_fixtures import _V2SyntheticBtApiFeed


_RISK_SERVICE_ID = "dim05-roll-exit-risk-service"
_RISK_INSTANCE = "dim05-roll-exit-risk-owner"
_RISK_PRINCIPAL = "dim05-roll-exit-risk-principal"
_ENTRY_SCOPE_REF = "dim05-roll-exit-entry-v1"
_QUERY_SCOPE_REF = "dim05-roll-exit-query-v1"
_SERVER_SECURITY_REF = "dim05-roll-exit-server-curve-v1"
_CLIENT_SECURITY_REF = "dim05-roll-exit-client-curve-v1"
_LISTENER_REF = "dim05-roll-exit-listener-v1"
_CLIENT_TRANSPORT_REF = "dim05-roll-exit-connect-v1"
_SUBMIT = "orders.submit_group_roll_exit"
_PREPARE = "orders.prepare_group_roll_exit"
_QUERY = "orders.query_group_roll_exit"


def _endpoint() -> str:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as probe:
        probe.bind(("127.0.0.1", 0))
        return f"tcp://127.0.0.1:{probe.getsockname()[1]}"


def _component(instance_id: str, ports: tuple[str, ...], role: str) -> dict[str, Any]:
    return {
        "config_schema_version": 2,
        "settings": {
            "instance_id": instance_id,
            "bind_ports": list(ports),
            "options": {"role": role, "account_policy_ref": dim05._risk_policy().policy_id},
        },
    }


def _risk_host_config(endpoint: str) -> dict[str, Any]:
    return {
        "service_config_schema_version": 1,
        "process_role": "service",
        "service_id": _RISK_SERVICE_ID,
        "scope_ref": "dim05-roll-exit-risk-root-v1",
        "transport_ref": _LISTENER_REF,
        "registration_policy_ref": "dim05-roll-exit-registration-v1",
        "service_requirements_ref": "dim05-roll-exit-requirements-v1",
        "components": {
            "risk": _component(
                _RISK_INSTANCE,
                ("service.order_entry", "service.order_query"),
                "account_owner",
            )
        },
        "communication": {
            "config_schema_version": 1,
            "profiles": {
                _LISTENER_REF: {
                    "backend": "zmq",
                    "config_schema_version": 1,
                    "endpoints": {"rpc_bind": endpoint},
                    "security_profile_ref": _SERVER_SECURITY_REF,
                    "options": {"high_water_mark": 32, "max_message_bytes": 1_000_000},
                }
            },
        },
    }


def _client_service_config(endpoint: str, account_ref: str) -> dict[str, Any]:
    options = {"request_timeout_ms": 2500, "high_water_mark": 32, "max_message_bytes": 1_000_000}
    bindings = [
        {
            "mount": "upstream.group_roll_exit_orders",
            "service_id": _RISK_SERVICE_ID,
            "instance_id": _RISK_INSTANCE,
            "scope_ref": _ENTRY_SCOPE_REF,
            "account_ref": account_ref,
            "portfolio_ref": dim05._PORTFOLIO,
            "transport_ref": _CLIENT_TRANSPORT_REF,
        },
        {
            "mount": "upstream.group_roll_exit_order_query",
            "service_id": _RISK_SERVICE_ID,
            "instance_id": _RISK_INSTANCE,
            "scope_ref": _QUERY_SCOPE_REF,
            "account_ref": account_ref,
            "portfolio_ref": dim05._PORTFOLIO,
            "transport_ref": _CLIENT_TRANSPORT_REF,
        },
    ]
    return {
        "service_config_schema_version": 1,
        "process_role": "service",
        "service_id": "dim05-roll-exit-coordinator-runtime",
        "scope_ref": "dim05-roll-exit-coordinator-runtime-scope-v1",
        "transport_ref": "dim05-roll-exit-runtime-listener-v1",
        "registration_policy_ref": "dim05-roll-exit-runtime-registration-v1",
        "service_requirements_ref": "dim05-roll-exit-runtime-requirements-v1",
        "components": {
            "monitor": {
                "config_schema_version": 2,
                "settings": {
                    "instance_id": "dim05-roll-exit-unused-monitor",
                    "bind_ports": ["service.monitor_query"],
                    "options": {},
                },
            }
        },
        "clients": {
            "execution": {
                "config_schema_version": 1,
                "settings": {
                    "bindings": bindings,
                    "options": {"request_timeout_ms": 2500, "heartbeat_interval_seconds": 5},
                },
            }
        },
        "communication": {
            "config_schema_version": 1,
            "profiles": {
                "dim05-roll-exit-runtime-listener-v1": {
                    "backend": "zmq",
                    "config_schema_version": 1,
                    "endpoints": {"rpc_bind": "tcp://127.0.0.1:9"},
                    "security_profile_ref": _CLIENT_SECURITY_REF,
                    "options": dict(options),
                },
                _CLIENT_TRANSPORT_REF: {
                    "backend": "zmq",
                    "config_schema_version": 1,
                    "endpoints": {"rpc_connect": endpoint},
                    "security_profile_ref": _CLIENT_SECURITY_REF,
                    "options": dict(options),
                },
            },
        },
    }


class _FilledCloseFeed(_V2SyntheticBtApiFeed):
    """A fixed synthetic provider leaf; Risk still creates and submits the order."""

    def __init__(self, ledger_path: str, provider_account: str) -> None:
        super().__init__(ledger_path, ("open", "open", "filled"), provider_account)
        self.calls: list[tuple[str, Decimal, Decimal, str, str]] = []

    def make_order(
        self,
        symbol: str,
        volume: float,
        price: float,
        order_type: str,
        *,
        client_order_id: str,
        **kwargs: Any,
    ) -> dict[str, Any]:
        quantity = Decimal(str(volume))
        price_value = Decimal(str(price))
        if kwargs != {
            "offset": "close",
            "post_only": False,
            "reduce_only": True,
            "time_in_force": "GTC",
        }:
            raise AssertionError("Risk close must reach the synthetic provider as CLOSE/reduce-only/GTC")
        self.calls.append((symbol, quantity, price_value, order_type, client_order_id))
        return super().make_order(
            symbol,
            volume,
            price,
            order_type,
            client_order_id=client_order_id,
            **kwargs,
        )


@dataclass(frozen=True, slots=True)
class Dim05RiskHostCase:
    endpoint: str
    risk_identity: WireIdentity
    coordinator_identity: WireIdentity
    service_scope: WireScope
    owner_scope: WireScope
    account_scope: AccountScope
    account_ref: str
    provider_account_id: str
    venue: str
    environment: str
    coordinator_principal_ref: str
    coordinator_service_id: str
    coordinator_instance_id: str
    run_id: str
    entry_scope_ref: str
    query_scope_ref: str
    server_public_key: bytes
    server_secret_key: bytes
    client_public_key: bytes
    client_secret_key: bytes
    risk_service_config: Mapping[str, Any]
    risk_client_service_config: Mapping[str, Any]
    entry_client_binding: Mapping[str, Any]
    query_client_binding: Mapping[str, Any]
    account_paths: Mapping[str, str]
    coordinator_paths: Mapping[str, str]
    metadata_reference_ns: int
    group_id: str
    source_leg_id: str
    source_instrument: str
    source_strategy_ref: str
    peer_strategy_ref: str
    risk_scope_fingerprint: str
    source_filled_quantity: Decimal
    peer_quantity: Decimal
    _owner_call: Callable[[Callable[[], Any]], Any]
    _available_reader: Callable[[str], Decimal]
    _provider_reader: Callable[[], tuple[tuple[str, Decimal, Decimal, str, str], ...]]

    def available(self, strategy_ref: str) -> Decimal:
        if strategy_ref not in {self.source_strategy_ref, self.peer_strategy_ref}:
            raise KeyError("unknown strategy in DIM05 synthetic fixture")
        return self._owner_call(lambda: self._available_reader(strategy_ref))

    def provider_calls(self) -> tuple[tuple[str, Decimal, Decimal, str, str], ...]:
        return self._owner_call(self._provider_reader)


class _OwnerLoop:
    """Run Host, store access and fixture observations on one owner thread."""

    def __init__(self, host: ServiceHost, cleanup: list[Callable[[], None]]) -> None:
        self.host = host
        self.cleanup = cleanup
        self.stop = threading.Event()
        self.ready = threading.Event()
        self.finished = threading.Event()
        self.commands: queue.Queue[tuple[Callable[[], Any], threading.Event, list[Any]]] = queue.Queue()
        self.error: BaseException | None = None
        self.cleanup_errors: list[BaseException] = []
        self.thread = threading.Thread(target=self._run, name="dim05-roll-exit-risk-fixture")

    def _run(self) -> None:
        try:
            self.host.start()
            self.ready.set()
            while not self.stop.is_set():
                try:
                    callback, done, result = self.commands.get_nowait()
                except queue.Empty:
                    self.host.serve_once(timeout_ms=15)
                    continue
                try:
                    result.append((True, callback()))
                except BaseException as error:
                    result.append((False, error))
                finally:
                    done.set()
        except BaseException as error:
            self.error = error
        finally:
            self.ready.set()
            if self.host.state not in {"closed", "failed"}:
                try:
                    self.host.close()
                except BaseException as error:
                    self.cleanup_errors.append(error)
            for callback in reversed(self.cleanup):
                try:
                    callback()
                except BaseException as error:
                    self.cleanup_errors.append(error)
            self.finished.set()

    def start(self) -> None:
        self.thread.start()
        assert self.ready.wait(10), "DIM05 Risk Host fixture did not become ready"
        assert self.error is None, (
            type(self.error).__name__ if self.error is not None else None,
            self.host.summary.get("start_failure"),
            self.host.summary.get("component_states"),
        )
        assert self.host.state == "started", self.host.summary

    def call(self, callback: Callable[[], Any]) -> Any:
        if self.error is not None or self.stop.is_set():
            raise RuntimeError("DIM05 Risk Host fixture is not serving")
        done = threading.Event()
        result: list[Any] = []
        self.commands.put((callback, done, result))
        assert done.wait(5), "DIM05 Risk Host owner-thread observation timed out"
        succeeded, value = result[0]
        if not succeeded:
            raise value
        return value

    def close(self) -> None:
        self.stop.set()
        assert self.finished.wait(10), "DIM05 Risk Host fixture did not stop"
        self.thread.join(1)
        assert not self.thread.is_alive()
        assert self.error is None, self.error
        assert self.host.state == "closed", self.host.summary
        assert not self.cleanup_errors, tuple(type(error).__name__ for error in self.cleanup_errors)


@contextmanager
def dim05_roll_exit_risk_host(
    tmp_path: Path,
    *,
    account_paths: Mapping[str, Mapping[str, str]],
    coordinator_paths: Mapping[str, str],
    metadata_reference_ns: int,
    group_id: str,
    source_leg_id: str,
    peer_quantity: Decimal = Decimal("0.75"),
) -> Iterator[Dim05RiskHostCase]:
    """Start the real registered Risk Host for an already-persisted DIM05 group.

    The caller owns the group setup and its SQLite paths.  The only simulated
    edge is the final provider response, fixed to a full fill for a valid CLOSE.
    """

    assert type(metadata_reference_ns) is int and metadata_reference_ns > 0
    assert type(group_id) is str and group_id
    assert type(source_leg_id) is str and source_leg_id
    assert type(peer_quantity) is Decimal and peer_quantity.is_finite() and peer_quantity > 0
    assert "groups" in coordinator_paths and "run_bindings" in coordinator_paths

    group_store = SqliteOrderGroupStore(coordinator_paths["groups"])
    try:
        snapshot = group_store.get(dim05._PORTFOLIO, group_id)
    finally:
        group_store.close()
    assert snapshot is not None, "caller must persist the accepted group before starting Risk Host"
    request = snapshot.request
    source_progress = next((row for row in snapshot.legs if row.leg_id == source_leg_id), None)
    source_plan = next((row for row in request.legs if row.leg_id == source_leg_id), None)
    assert source_progress is not None and source_plan is not None
    assert source_plan.maximum_intent.position_effect.value == "OPEN"
    assert source_progress.child_status is not None
    source_quantity = source_progress.child_status.filled_quantity
    assert type(source_quantity) is Decimal and source_quantity > 0
    account_ref = source_plan.account_ref
    assert set(account_paths) == {account_ref}, "this fixture currently supports one owner account"
    instrument = source_plan.maximum_intent.instrument
    instruments = tuple(dict.fromkeys(leg.maximum_intent.instrument for leg in request.legs))
    assert all(leg.account_ref == account_ref for leg in request.legs)
    assert type(request.strategy_id) is str and request.strategy_id == dim05._STRATEGY

    provider_account = f"provider-{account_ref}"
    endpoint = _endpoint()
    server_public, server_secret = zmq.curve_keypair()
    client_public, client_secret = zmq.curve_keypair()
    owner_scope = dim05._owner_scope(account_ref)
    peer_strategy = "strategy-dim05-peer"
    if peer_strategy == request.strategy_id:
        peer_strategy = "strategy-dim05-peer-other"
    peer_scope = replace(owner_scope, strategy_ref=peer_strategy)
    coordinator_identity = WireIdentity(
        WireRole.SERVICE, dim05._COORDINATOR_PRINCIPAL, dim05._COORDINATOR_SERVICE_ID
    )
    risk_identity = WireIdentity(WireRole.SERVICE, _RISK_PRINCIPAL, _RISK_SERVICE_ID)
    risk_scope = AccountScope("fixture", provider_account, "simulation")
    risk_scope_fingerprint = risk_account_scope_fingerprint(risk_scope.key)

    risk_gate_path = str(tmp_path / "dim05-roll-exit-risk-gate.sqlite3")
    risk_journal_path = str(tmp_path / "dim05-roll-exit-risk-journal.sqlite3")
    risk_store_path = str(tmp_path / "dim05-roll-exit-risk-registry.sqlite3")
    state: dict[str, Any] = {}
    cleanup: list[Callable[[], None]] = []
    provider_failure: list[str] = []
    old_mapper = None
    mapper_installed = False

    def resource_provider_inner(component, instance_id, options, _scope):
        nonlocal old_mapper, mapper_installed
        assert (component, instance_id, options["role"]) == ("risk", _RISK_INSTANCE, "account_owner")
        graph = dim05._new_account_resource_graph(
            account_ref,
            account_paths[account_ref],
            ("open", "open", "filled"),
            metadata_reference_ns,
            instruments,
            (),
        )
        local_group_store = SqliteOrderGroupStore(coordinator_paths["groups"])
        origin_store = GroupOriginRunBindingStore(coordinator_paths["run_bindings"])
        graph.authority.guard.bind_reconciliation_source(graph.bundle.proof_source)
        source = GroupRollExitAuthorizationSourceV1(
            graph.accepted_store,
            origin_store,
            local_group_store,
            graph.execution_store,
            graph.bundle.proof_source,
            graph.authority.guard,
            coordinator_principal_ref=dim05._COORDINATOR_PRINCIPAL,
        )
        assert source.ready is True
        feed = _FilledCloseFeed(account_paths[account_ref]["provider"], provider_account)
        bt_api = BtApi(debug=False)
        from bt_api_py._venue_mappers import MAPPERS

        old_mapper = MAPPERS.get("FIXTURE___SPOT")
        MAPPERS["FIXTURE___SPOT"] = MAPPERS["BINANCE___SPOT"]
        mapper_installed = True
        bt_api.exchange_feeds["FIXTURE___SPOT"] = feed
        journal = RiskOrderJournal(risk_journal_path, writer_id="dim05-roll-exit-risk")
        gate = DurableRiskGate(
            risk_gate_path,
            dim05._risk_policy(),
            execution_journal_authority=journal,
            gross_exposure_authority=journal,
        )
        proof_source = graph.bundle.proof_source
        registry = proof_source.targets[graph.owner_scope].risk_target.registry
        targets: dict[WireScope, RiskOrderTarget] = {}
        admissions: dict[WireScope, DurableRiskAdmissionAdapter] = {}
        contracts = {}
        strategy_scopes = ((request.strategy_id, owner_scope), (peer_strategy, peer_scope))
        for strategy_id, wire_scope in strategy_scopes:
            gate.set_strategy_allocation(
                risk_scope,
                strategy_id,
                f"dim05-roll-exit-allocation-{strategy_id}",
                max_notional=Decimal("100000"),
                notional_unit="USD",
            )
            targets[wire_scope] = RiskOrderTarget(
                account_scope=risk_scope,
                strategy_id=strategy_id,
                exchange_name="FIXTURE___SPOT",
                provider_account_id=provider_account,
                registry=registry,
            )
            mapper = InstrumentRiskAdmissionMapper(
                risk_scope,
                registry,
                clock_ns=time.time_ns,
                allocation_reader=lambda routed_scope: gate.get_strategy_allocation(
                    risk_scope, routed_scope.strategy_id
                ),
                wire_account_ref=account_ref,
            )
            admissions[wire_scope] = DurableRiskAdmissionAdapter(gate, mapper)
            contracts[wire_scope] = replace(
                proof_source.targets[graph.owner_scope].provider_contract,
                supported_time_in_force=("GTC", "IOC"),
                supported_position_effects=("OPEN", "CLOSE"),
            )
        gate.set_closable_position(
            risk_scope,
            instrument,
            request.strategy_id,
            "dim05-roll-exit-source-position-v1",
            closable_quantity=source_quantity,
            quantity_unit="base",
        )
        gate.set_closable_position(
            risk_scope,
            instrument,
            peer_strategy,
            "dim05-roll-exit-peer-position-v1",
            closable_quantity=peer_quantity,
            quantity_unit="base",
        )
        policy = TrustedGroupWritePolicy(
            _RISK_INSTANCE,
            dim05._COORDINATOR_PRINCIPAL,
            coordinator_identity,
            _ENTRY_SCOPE_REF,
            owner_scope,
            dim05._RUN,
            operation=_SUBMIT,
        )
        state.update(
            {
                "graph": graph,
                "group_store": local_group_store,
                "origin_store": origin_store,
                "source": source,
                "risk_journal": journal,
                "risk_gate": gate,
                "feed": feed,
                "bt_api": bt_api,
            }
        )

        def close_resources() -> None:
            nonlocal mapper_installed
            bt_api.close()
            gate.close()
            origin_store.close()
            local_group_store.close()
            graph.close()
            if mapper_installed:
                if old_mapper is None:
                    MAPPERS.pop("FIXTURE___SPOT", None)
                else:
                    MAPPERS["FIXTURE___SPOT"] = old_mapper
                mapper_installed = False

        cleanup.append(close_resources)
        return ComponentResources.borrowed(
            {
                "bt_api": bt_api,
                "targets": targets,
                "admission": admissions,
                "journal": journal,
                "gate": gate,
                "provider_contracts": contracts,
                "group_roll_exit_authorization_source": source,
            },
            ready=lambda: source.ready is True,
        )

    def resource_provider(component, instance_id, options, scope):
        try:
            return resource_provider_inner(component, instance_id, options, scope)
        except Exception as error:
            provider_failure.append(f"{type(error).__name__}:{str(error)[:300]}")
            raise

    entry_grant = ResourceGrant(
        _RISK_INSTANCE,
        "service.order_entry",
        _ENTRY_SCOPE_REF,
        owner_scope,
        frozenset({_SUBMIT}),
    )
    query_grant = ResourceGrant(
        _RISK_INSTANCE,
        "service.order_query",
        _QUERY_SCOPE_REF,
        owner_scope,
        frozenset({"orders.prepare_group_roll_exit", "orders.query_group_roll_exit"}),
    )
    config = parse_service_config(_risk_host_config(endpoint))
    host = ServiceHost(
        config,
        TrustedServiceAuthority(
            identity=risk_identity,
            scope=WireScope(dim05._DOMAIN),
            scope_ref=config.scope_ref,
            scope_version="dim05-roll-exit-service-scope-v1",
            security_ref=_SERVER_SECURITY_REF,
            security_version="dim05-roll-exit-curve-v1",
            registration_policy_ref=config.registration_policy_ref,
            registration_policy_version="dim05-roll-exit-registration-v1",
            service_requirements_ref=config.service_requirements_ref,
            service_requirements_version="dim05-roll-exit-requirements-v1",
            curve_public_key=server_public,
            curve_secret_key=server_secret,
            principals={client_public: dim05._COORDINATOR_PRINCIPAL},
            grants={
                dim05._COORDINATOR_PRINCIPAL: (
                    PrincipalGrant(coordinator_identity, (entry_grant, query_grant)),
                )
            },
            group_write_policies=(
                TrustedGroupWritePolicy(
                    _RISK_INSTANCE,
                    dim05._COORDINATOR_PRINCIPAL,
                    coordinator_identity,
                    _ENTRY_SCOPE_REF,
                    owner_scope,
                    dim05._RUN,
                    operation=_SUBMIT,
                ),
            ),
        ),
        risk_store_path,
        resource_provider,
    )
    loop = _OwnerLoop(host, cleanup)
    client_config = _client_service_config(endpoint, account_ref)
    binding_rows = client_config["clients"]["execution"]["settings"]["bindings"]
    entry_binding = next(row for row in binding_rows if row["mount"] == "upstream.group_roll_exit_orders")
    query_binding = next(row for row in binding_rows if row["mount"] == "upstream.group_roll_exit_order_query")
    case = Dim05RiskHostCase(
        endpoint=endpoint,
        risk_identity=risk_identity,
        coordinator_identity=coordinator_identity,
        service_scope=WireScope(dim05._DOMAIN),
        owner_scope=owner_scope,
        account_scope=risk_scope,
        account_ref=account_ref,
        provider_account_id=provider_account,
        venue="fixture",
        environment="simulation",
        coordinator_principal_ref=dim05._COORDINATOR_PRINCIPAL,
        coordinator_service_id=dim05._COORDINATOR_SERVICE_ID,
        coordinator_instance_id=dim05._COORDINATOR_INSTANCE_ID,
        run_id=dim05._RUN,
        entry_scope_ref=_ENTRY_SCOPE_REF,
        query_scope_ref=_QUERY_SCOPE_REF,
        server_public_key=server_public,
        server_secret_key=server_secret,
        client_public_key=client_public,
        client_secret_key=client_secret,
        risk_service_config=_risk_host_config(endpoint),
        risk_client_service_config=client_config,
        entry_client_binding=dict(entry_binding),
        query_client_binding=dict(query_binding),
        account_paths=dict(account_paths[account_ref]),
        coordinator_paths=dict(coordinator_paths),
        metadata_reference_ns=metadata_reference_ns,
        group_id=group_id,
        source_leg_id=source_leg_id,
        source_instrument=instrument,
        source_strategy_ref=request.strategy_id,
        peer_strategy_ref=peer_strategy,
        risk_scope_fingerprint=risk_scope_fingerprint,
        source_filled_quantity=source_quantity,
        peer_quantity=peer_quantity,
        _owner_call=loop.call,
        _available_reader=lambda strategy_ref: state["risk_gate"].get_closable_position(
            risk_scope,
            instrument,
            strategy_ref,
            position_side="NET",
            position_bucket="ALL",
        ).available_quantity,
        _provider_reader=lambda: tuple(state["feed"].calls),
    )
    loop.start()
    if provider_failure:
        raise AssertionError(("resource_provider", provider_failure[-1]))
    try:
        yield case
    finally:
        loop.close()

