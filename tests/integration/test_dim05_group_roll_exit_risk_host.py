"""Registered DIM05 roll/exit path against an accepted synthetic group source.

Only the provider boundary is synthetic. Group submission, persisted source
proof, Risk ServiceHost registration, CLOSE admission, terminal receipt, and
read-only recovery all use their production implementations.
"""

from __future__ import annotations

import socket
import threading
import time
from dataclasses import replace
from decimal import Decimal
from pathlib import Path
from typing import Any

import zmq
from bt_api_base.service_protocol import WireIdentity, WireRole, WireScope
from bt_api_execution import Side, SqliteOrderGroupStore
from bt_api_risk import (
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
from bt_api_py.service_risk_order_entry import RiskBtApiProviderContract, RiskOrderTarget
from bt_api_py.service_risk_order_journal import RiskOrderJournal
from bt_api_py.service_runtime import ClientCurveKeys, ServiceClientRuntime, ZmqTransportFactory
from tests.integration import test_arb03_dim05_group_partial_fill_service as dim05
from tests.unit._service_group_provider_fixtures import _V2SyntheticBtApiFeed


_ACCOUNT = "account-a"
_RISK_SERVICE_ID = "dim05-roll-exit-risk-service"
_RISK_INSTANCE = "dim05-roll-exit-risk-owner"
_RISK_PRINCIPAL = "dim05-roll-exit-risk-principal"
_ENTRY_SCOPE_REF = "dim05-roll-exit-entry-v1"
_QUERY_SCOPE_REF = "dim05-roll-exit-query-v1"
_SERVER_SECURITY_REF = "dim05-roll-exit-server-curve-v1"
_CLIENT_SECURITY_REF = "dim05-roll-exit-client-curve-v1"
_LISTENER_REF = "dim05-roll-exit-listener-v1"
_CLIENT_TRANSPORT_REF = "dim05-roll-exit-connect-v1"
_STRATEGY_B = "strategy-dim05-peer"
_PREPARE = "orders.prepare_group_roll_exit"
_SUBMIT = "orders.submit_group_roll_exit"
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


def _client_service_config(endpoint: str) -> dict[str, Any]:
    options = {"request_timeout_ms": 2500, "high_water_mark": 32, "max_message_bytes": 1_000_000}
    bindings = [
        {
            "mount": "upstream.group_roll_exit_orders",
            "service_id": _RISK_SERVICE_ID,
            "instance_id": _RISK_INSTANCE,
            "scope_ref": _ENTRY_SCOPE_REF,
            "account_ref": _ACCOUNT,
            "portfolio_ref": dim05._PORTFOLIO,
            "transport_ref": _CLIENT_TRANSPORT_REF,
        },
        {
            "mount": "upstream.group_roll_exit_order_query",
            "service_id": _RISK_SERVICE_ID,
            "instance_id": _RISK_INSTANCE,
            "scope_ref": _QUERY_SCOPE_REF,
            "account_ref": _ACCOUNT,
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
                    "options": {
                        "request_timeout_ms": 2500,
                        "heartbeat_interval_seconds": 5,
                    },
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


def _plain_wire_json(value: object) -> object:
    from collections.abc import Mapping

    if isinstance(value, Mapping):
        return {key: _plain_wire_json(item) for key, item in value.items()}
    if type(value) is tuple:
        return [_plain_wire_json(item) for item in value]
    return value


class _FilledCloseFeed(_V2SyntheticBtApiFeed):
    """Persist one real BtApi order with a fixed full-fill synthetic provider response."""

    def __init__(self, ledger_path: str, provider_account: str) -> None:
        # The accepted group fixture already wrote two provider orders. Its
        # next sequence index is therefore two; only the roll CLOSE is filled.
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
            raise AssertionError("Risk close did not reach the provider with CLOSE/reduce-only/GTC")
        self.calls.append((symbol, quantity, price_value, order_type, client_order_id))
        return super().make_order(
            symbol,
            volume,
            price,
            order_type,
            client_order_id=client_order_id,
            **kwargs,
        )


class _HostOwnerLoop:
    """Keep Host start/serve/close and its SQLite resources on one owner thread."""

    def __init__(self, host: ServiceHost, cleanup: list[Any]) -> None:
        self.host = host
        self.cleanup = cleanup
        self.stop = threading.Event()
        self.ready = threading.Event()
        self.finished = threading.Event()
        self.error: BaseException | None = None
        self.cleanup_errors: list[BaseException] = []
        self.thread = threading.Thread(target=self._run, name="dim05-roll-exit-risk-host")

    def _run(self) -> None:
        try:
            self.host.start()
            self.ready.set()
            while not self.stop.is_set():
                self.host.serve_once(timeout_ms=15)
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
        assert self.ready.wait(10), "DIM05 roll-exit Risk Host did not become ready"
        assert self.error is None, (
            type(self.error).__name__ if self.error is not None else None,
            self.host.summary.get("start_failure"),
            self.host.summary.get("component_states"),
        )
        assert self.host.state == "started", self.host.summary

    def close(self) -> None:
        self.stop.set()
        assert self.finished.wait(10), "DIM05 roll-exit Risk Host did not stop"
        self.thread.join(1)
        assert not self.thread.is_alive()
        if self.error is not None and self.host.state == "failed":
            # Startup failures are reported at the callsite with a bounded
            # fixture diagnostic; the owner thread has already completed its
            # shutdown path.
            assert not self.cleanup_errors, tuple(
                type(error).__name__ for error in self.cleanup_errors
            )
            return
        assert self.error is None, self.error
        assert self.host.state == "closed", self.host.summary
        assert not self.cleanup_errors, tuple(type(error).__name__ for error in self.cleanup_errors)


def _available(gate: DurableRiskGate, scope, instrument: str, strategy_id: str) -> Decimal:
    return gate.get_closable_position(
        scope, instrument, strategy_id, position_side="NET", position_bucket="ALL"
    ).available_quantity


def test_dim05_roll_exit_uses_registered_risk_host_and_preserves_peer_strategy_allocation(
    tmp_path: Path,
) -> None:
    """A source-bound close consumes only A's synthetic shared-account allocation."""

    group_id = "group-dim05-roll-exit-risk-host-01"
    with dim05._GroupCluster(
        tmp_path,
        accounts=(_ACCOUNT,),
        instruments={_ACCOUNT: (dim05._V2_INSTRUMENT, dim05._PERP_INSTRUMENT)},
        initial_states={_ACCOUNT: ("cancelled:1.5", "filled")},
        expected_orders={
            _ACCOUNT: (
                (dim05._V2_INSTRUMENT, "2", "100", "buy-limit"),
                (dim05._PERP_INSTRUMENT, "3", "100", "sell-limit"),
            )
        },
        rule_specs={_ACCOUNT: {dim05._V2_INSTRUMENT: "2", dim05._PERP_INSTRUMENT: "1"}},
    ) as cluster:
        accepted_group = cluster.submit_group(
            dim05._group_request(
                group_id,
                cluster.metadata_reference_ns,
                (
                    ("leg-a", _ACCOUNT, Side.BUY, Decimal("2"), dim05._V2_INSTRUMENT),
                    ("leg-b", _ACCOUNT, Side.SELL, Decimal("4"), dim05._PERP_INSTRUMENT),
                ),
            )
        )
        assert accepted_group.state.value == "PAIRED"
        source_leg = next(row for row in accepted_group.legs if row.leg_id == "leg-a")
        assert source_leg.submitted_intent.position_effect.value == "OPEN"
        assert source_leg.child_status.state.value == "CANCELLED"
        assert source_leg.child_status.filled_quantity == Decimal("1.5")

    account_paths = cluster.account_paths[_ACCOUNT]
    coordinator_paths = cluster.coordinator_paths
    account_ref = _ACCOUNT
    provider_account = f"provider-{account_ref}"
    endpoint = _endpoint()
    server_public, server_secret = zmq.curve_keypair()
    client_public, client_secret = zmq.curve_keypair()
    owner_scope = dim05._owner_scope(account_ref)
    peer_scope = replace(owner_scope, strategy_ref=_STRATEGY_B)
    coordinator_identity = WireIdentity(
        WireRole.SERVICE, dim05._COORDINATOR_PRINCIPAL, dim05._COORDINATOR_SERVICE_ID
    )
    risk_identity = WireIdentity(WireRole.SERVICE, _RISK_PRINCIPAL, _RISK_SERVICE_ID)
    risk_gate_path = str(tmp_path / "dim05-roll-exit-risk-gate.sqlite3")
    risk_journal_path = str(tmp_path / "dim05-roll-exit-risk-journal.sqlite3")
    risk_store_path = str(tmp_path / "dim05-roll-exit-risk-registry.sqlite3")
    close_feed: _FilledCloseFeed | None = None
    close_gate: DurableRiskGate | None = None
    source_holder: dict[str, Any] = {}
    resource_closers: list[Any] = []
    provider_failure: list[str] = []

    def _resource_provider_inner(component, instance_id, options, _scope):
        nonlocal close_feed, close_gate
        assert (component, instance_id, options["role"]) == ("risk", _RISK_INSTANCE, "account_owner")
        graph = dim05._new_account_resource_graph(
            account_ref,
            account_paths,
            ("open", "open", "filled"),
            cluster.metadata_reference_ns,
            (dim05._V2_INSTRUMENT, dim05._PERP_INSTRUMENT),
            (),
        )
        group_store = SqliteOrderGroupStore(coordinator_paths["groups"])
        origin_store = GroupOriginRunBindingStore(coordinator_paths["run_bindings"])
        graph.authority.guard.bind_reconciliation_source(graph.bundle.proof_source)
        source = GroupRollExitAuthorizationSourceV1(
            graph.accepted_store,
            origin_store,
            group_store,
            graph.execution_store,
            graph.bundle.proof_source,
            graph.authority.guard,
            coordinator_principal_ref=dim05._COORDINATOR_PRINCIPAL,
        )
        assert source.ready is True
        close_feed = _FilledCloseFeed(account_paths["provider"], provider_account)
        close_bt_api = BtApi(debug=False)
        from bt_api_py._venue_mappers import MAPPERS

        MAPPERS["FIXTURE___SPOT"] = MAPPERS["BINANCE___SPOT"]
        close_bt_api.exchange_feeds["FIXTURE___SPOT"] = close_feed
        risk_journal = RiskOrderJournal(risk_journal_path, writer_id="dim05-roll-exit-risk")
        close_gate = DurableRiskGate(
            risk_gate_path,
            dim05._risk_policy(),
            execution_journal_authority=risk_journal,
            gross_exposure_authority=risk_journal,
        )
        proof_source = graph.bundle.proof_source
        registry = proof_source.targets[graph.owner_scope].risk_target.registry
        risk_scope = graph.risk_scope
        targets = {}
        admissions = {}
        contracts = {}
        for strategy_id, wire_scope in (
            (dim05._STRATEGY, owner_scope),
            (_STRATEGY_B, peer_scope),
        ):
            close_gate.set_strategy_allocation(
                risk_scope,
                strategy_id,
                f"dim05-roll-exit-allocation-{strategy_id}",
                max_notional=Decimal("100000"),
                notional_unit="USD",
            )
            target = RiskOrderTarget(
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
                allocation_reader=lambda routed_scope: close_gate.get_strategy_allocation(
                    risk_scope, routed_scope.strategy_id
                ),
                wire_account_ref=account_ref,
            )
            targets[wire_scope] = target
            admissions[wire_scope] = DurableRiskAdmissionAdapter(close_gate, mapper)
            contracts[wire_scope] = replace(
                proof_source.targets[graph.owner_scope].provider_contract,
                supported_time_in_force=("GTC", "IOC"),
                supported_position_effects=("OPEN", "CLOSE"),
            )
        close_gate.set_closable_position(
            risk_scope,
            dim05._V2_INSTRUMENT,
            dim05._STRATEGY,
            "dim05-roll-exit-source-position-v1",
            closable_quantity=Decimal("1.5"),
            quantity_unit="base",
        )
        close_gate.set_closable_position(
            risk_scope,
            dim05._V2_INSTRUMENT,
            _STRATEGY_B,
            "dim05-roll-exit-peer-position-v1",
            closable_quantity=Decimal("0.75"),
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
        source_holder.update(
            {
                "graph": graph,
                "group_store": group_store,
                "origin_store": origin_store,
                "source": source,
                "risk_journal": risk_journal,
                "risk_gate": close_gate,
                "bt_api": close_bt_api,
            }
        )
        def close_resources() -> None:
            close_bt_api.close()
            close_gate.close()
            origin_store.close()
            group_store.close()
            graph.close()

        resource_closers.append(close_resources)
        return ComponentResources.borrowed(
            {
                "bt_api": close_bt_api,
                "targets": targets,
                "admission": admissions,
                "journal": risk_journal,
                "gate": close_gate,
                "provider_contracts": contracts,
                "group_roll_exit_authorization_source": source,
            },
            ready=lambda: source.ready is True,
        )

    def resource_provider(component, instance_id, options, scope):
        try:
            return _resource_provider_inner(component, instance_id, options, scope)
        except Exception as error:
            # Production maps provider exceptions to a stable public error.
            # Keep only a bounded synthetic-fixture diagnostic here so a
            # startup failure remains actionable without changing that API.
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
        frozenset({_PREPARE, _QUERY}),
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
    host_loop = _HostOwnerLoop(host, resource_closers)
    runtime: ServiceClientRuntime | None = None
    runtime_started = False
    try:
        try:
            host_loop.start()
        except AssertionError:
            if provider_failure:
                raise AssertionError(("resource_provider", provider_failure[-1]))
            raise
        runtime = ServiceClientRuntime(
            parse_service_config(_client_service_config(endpoint)),
            identity=coordinator_identity,
            scope_resolver=lambda _scope_ref: owner_scope,
            peer_resolver=lambda _service_id: risk_identity,
            transport_factory=ZmqTransportFactory(
                {
                    _CLIENT_SECURITY_REF: ClientCurveKeys(
                        server_key=server_public,
                        public_key=client_public,
                        secret_key=client_secret,
                    )
                }
            ),
            run_id=dim05._RUN,
            requested_operations={
                "upstream.group_roll_exit_orders": frozenset({_SUBMIT}),
                "upstream.group_roll_exit_order_query": frozenset({_PREPARE, _QUERY}),
            },
        )
        receipt = runtime.start()
        runtime_started = True
        assert receipt.degraded_count == 0, receipt
        assert {binding.mount for binding in runtime.bindings} == {
            "upstream.group_roll_exit_orders",
            "upstream.group_roll_exit_order_query",
        }
        entry_binding = next(row for row in runtime.bindings if row.mount == "upstream.group_roll_exit_orders")
        query_binding = next(row for row in runtime.bindings if row.mount == "upstream.group_roll_exit_order_query")
        assert entry_binding.operations == frozenset({_SUBMIT})
        assert query_binding.operations == frozenset({_PREPARE, _QUERY})
        assert entry_binding.scope == query_binding.scope == owner_scope

        def call(mount: str, operation: str, arguments: dict[str, Any], scope_ref: str) -> Any:
            assert runtime is not None
            return runtime.call(
                mount,
                operation,
                arguments,
                account_ref=_ACCOUNT,
                portfolio_ref=dim05._PORTFOLIO,
                scope_ref=scope_ref,
                max_reply_message_bytes=900_000,
            )

        request = {
            "schema": "group-roll-exit-request-v1",
            "portfolio_ref": dim05._PORTFOLIO,
            "group_id": group_id,
            "source_leg_id": "leg-a",
            "limit_price": "101",
        }
        prepared = call(
            "upstream.group_roll_exit_order_query", _PREPARE, request, _QUERY_SCOPE_REF
        )
        if prepared.get("ok") is not True:
            handler = host._components[0].handlers[_PREPARE]
            risk_adapter = handler.__kwdefaults__["_adapter"]
            roll_owner = risk_adapter._group_roll_exit_owner
            registrations = tuple(
                sorted(
                    (
                        row.port,
                        row.scope_ref,
                        tuple(sorted(row.operations)),
                        tuple(sorted(row.write_operations)),
                        row.context.registration_id,
                        row.context.principal_ref,
                        row.context.scope,
                        row.strategy_write_run_id,
                    )
                    for row in roll_owner._registrations.values()
                )
            )
            query_registration = next(
                row for row in roll_owner._registrations.values()
                if row.port == "service.order_query"
            )
            entry_registration = next(
                row for row in roll_owner._registrations.values()
                if row.port == "service.order_entry"
            )

            def check(callable_check) -> str:
                try:
                    callable_check()
                except Exception as error:
                    return f"{type(error).__name__}:{getattr(error, 'code', '')}"
                return "OK"

            checks = (
                check(lambda: roll_owner._registrations_for(_PREPARE, query_registration.context)),
                check(
                    lambda: roll_owner._source._validate_registration_pair(
                        query_registration,
                        entry_registration,
                        operation=_PREPARE,
                    )
                ),
            )
            from bt_api_py.service_group_roll_exit import GroupRollExitRequestV1

            def stage(callable_stage) -> str:
                try:
                    callable_stage()
                except Exception as error:
                    code = getattr(error, "code", None)
                    return f"{type(error).__name__}:{code or ''}"
                return "OK"

            request_value = GroupRollExitRequestV1.from_payload(request)
            source_result: dict[str, Any] = {}

            def resolve_source() -> None:
                source_result["accepted"] = roll_owner._source.resolve_group_roll_exit(
                    query_registration,
                    request_value,
                    operation=_PREPARE,
                    entry_registration=entry_registration,
                )

            source_stage = stage(resolve_source)
            build_stage = stage(
                lambda: roll_owner._build_authorization(
                    source_result["accepted"], request_value, entry_registration
                )
            ) if source_stage == "OK" else "SKIPPED"
            group_facts: dict[str, Any] = {}
            if source_stage.startswith("GroupRollExitAuthorizationSourceError:"):
                origin = source_holder["origin_store"].get(
                    request_value.portfolio_ref, request_value.group_id
                )
                accepted = source_holder["graph"].accepted_store.get_binding(
                    entry_registration,
                    origin_run_id=origin.origin_run_id,
                    group_id=request_value.group_id,
                    operation=_SUBMIT,
                )
                snapshot = source_holder["group_store"].get(
                    request_value.portfolio_ref, request_value.group_id
                )
                group_facts = {
                    "accepted_present": accepted is not None,
                    "snapshot_present": snapshot is not None,
                    "accepted_scope_matches": accepted.stable_scope == entry_registration.context.scope,
                    "accepted_account_matches": accepted.account_ref == entry_registration.context.scope.account_ref,
                    "accepted_portfolio_matches": accepted.portfolio_ref == request_value.portfolio_ref,
                    "accepted_strategy_matches": accepted.strategy_id == origin.request.strategy_id,
                    "accepted_members_present": bool(accepted.members),
                    "accepted_reservation_present": accepted.reservation is not None,
                    "snapshot_request_matches_origin": snapshot.request == origin.request,
                    "snapshot_origin_fingerprint_matches": snapshot.request.fingerprint == origin.request_fingerprint,
                    "snapshot_request_fingerprint_matches": snapshot.request.fingerprint == origin.request.fingerprint,
                    "accepted_fingerprint_present": origin.accepted_request_fingerprint is not None,
                    "accepted_fingerprint_matches": origin.accepted_request_fingerprint == snapshot.request.fingerprint,
                    # This is a wholly synthetic local fixture. Include the
                    # persisted typed snapshot so one failure report can be
                    # checked against every source predicate without another
                    # Host attempt for each individual field.
                    "safe_synthetic_snapshot": snapshot.to_payload(),
                }
                if source_stage.endswith(":GROUP_ROLL_EXIT_RESERVATION_MISMATCH"):
                    account_legs = tuple(
                        (leg.leg_id, leg.maximum_intent)
                        for leg in snapshot.request.legs
                        if leg.account_ref == entry_registration.context.scope.account_ref
                    )
                    reservations = tuple(
                        row for row in snapshot.reservations
                        if row.account_ref == entry_registration.context.scope.account_ref
                    )
                    group_facts.update(
                        {
                            "accepted_members_match": accepted.members == account_legs,
                            "local_reservation_count": len(reservations),
                            "local_reservation_matches_accepted": len(reservations) == 1 and reservations[0] == accepted.reservation,
                            "accepted_reservation_phase": accepted.reservation.phase.value,
                            "local_reservation_phase": reservations[0].phase.value if reservations else None,
                            "accepted_reservation_leg_ids": tuple(leg.leg_id for leg in accepted.reservation.legs),
                            "local_reservation_leg_ids": tuple(leg.leg_id for leg in reservations[0].legs) if reservations else (),
                        }
                    )
                if source_stage.endswith(":GROUP_ROLL_EXIT_SOURCE_NOT_TERMINAL"):
                    from bt_api_execution import GroupLegPhase, PositionEffect

                    plan = next(
                        row for row in snapshot.request.legs
                        if row.leg_id == request_value.source_leg_id
                    )
                    progress = next(
                        row for row in snapshot.legs
                        if row.leg_id == request_value.source_leg_id
                    )
                    child = progress.submitted_intent
                    status = progress.child_status
                    claim = progress.claim_receipt
                    resolution = progress.resolution_receipt
                    group_facts.update(
                        {
                            "child_exact_type": type(child).__name__ == "OrderIntent",
                            "status_exact_type": type(status).__name__ == "GroupChildOrderStatus",
                            "claim_exact_type": type(claim).__name__ == "GroupLegClaimReceipt",
                            "resolution_exact_type": type(resolution).__name__ == "GroupDispatchResolutionReceipt",
                            "phase_observed": progress.phase is GroupLegPhase.OBSERVED,
                            "child_present": child is not None,
                            "status_present": status is not None,
                            "claim_present": claim is not None,
                            "resolution_present": resolution is not None,
                            "plan_is_open": plan.maximum_intent.position_effect is PositionEffect.OPEN,
                            "child_is_open": child is not None and child.position_effect is PositionEffect.OPEN,
                            "status_terminal_positive": status is not None and status.state.value in {"FILLED", "CANCELLED"},
                            "status_intent_matches_child": status is not None and child is not None and status.intent_id == child.intent_id,
                            "status_quantity_within_source": status is not None and status.filled_quantity > 0 and status.filled_quantity <= child.quantity,
                            "status_has_trade_count": status is not None and status.trade_count is not None and status.trade_count > 0,
                            "status_has_revision": status is not None and status.order_revision is not None,
                            "filled_status_full_quantity": status is not None and (status.state.value != "FILLED" or status.filled_quantity == child.quantity),
                            "child_matches_plan": child is not None and replace(plan.maximum_intent, quantity=child.quantity) == child,
                            "claim_matches_request": claim is not None and claim.permit.group_id == request_value.group_id and claim.permit.leg_id == request_value.source_leg_id,
                            "claim_account_matches": claim is not None and claim.permit.account_ref == entry_registration.context.scope.account_ref,
                            "claim_portfolio_matches": claim is not None and claim.permit.portfolio_ref == request_value.portfolio_ref,
                            "claim_strategy_matches": claim is not None and claim.permit.strategy_id == snapshot.request.strategy_id,
                            "claim_principal_matches": claim is not None and claim.permit.coordinator_principal_ref == dim05._COORDINATOR_PRINCIPAL,
                            "claim_group_fingerprint_matches": claim is not None and claim.permit.group_fingerprint == snapshot.request.fingerprint,
                            "claim_plan_fingerprint_matches": claim is not None and claim.permit.order_intent_hash == plan.maximum_intent.fingerprint,
                            "claim_child_fingerprint_matches": claim is not None and claim.actual_child_intent_hash == child.fingerprint,
                            "claim_quantity_matches": claim is not None and claim.actual_quantity == child.quantity,
                            "resolution_matches_status": resolution is not None and status is not None and resolution.claim == claim and resolution.final_state is status.state and resolution.filled_quantity == status.filled_quantity and resolution.trade_count == status.trade_count and resolution.order_revision == status.order_revision,
                            "resolution_terminal_kind": resolution is not None and resolution.resolution_kind == "TERMINAL_EXPOSURE_RESOLVED",
                            "resolution_requested_matches": resolution is not None and resolution.requested_quantity == child.quantity,
                            "resolution_latch_clear": resolution is not None and resolution.dispatch_latch_active is False,
                        }
                    )
            raise AssertionError((prepared, registrations, checks, (source_stage, build_stage), group_facts))
        assert prepared["ok"] is True and prepared["code"] == "OK", prepared
        authorization = _plain_wire_json(prepared["result"]["authorization"])
        assert authorization["source"]["source_filled_quantity"] == "1.5"
        assert authorization["intent"]["position_effect"] == "CLOSE"
        assert authorization["intent"]["reduce_only"] is True
        assert authorization["intent"]["quantity"] == "1.5"

        submitted = call(
            "upstream.group_roll_exit_orders",
            _SUBMIT,
            {"authorization": authorization},
            _ENTRY_SCOPE_REF,
        )
        assert submitted["ok"] is True and submitted["code"] == "OK", submitted
        assert submitted["result"]["phase"] == "RESOLVED", submitted
        observation = _plain_wire_json(submitted["result"]["observation"])
        assert observation["close_state"] == "FILLED"
        assert observation["filled_quantity"] == "1.5"
        assert observation["close_receipt_sha256"]

        queried = call(
            "upstream.group_roll_exit_order_query",
            _QUERY,
            {
                "action_id": authorization["action_id"],
                "authorization_sha256": authorization["authorization_sha256"],
            },
            _QUERY_SCOPE_REF,
        )
        assert queried["ok"] is True and queried["result"]["phase"] == "RESOLVED", queried
        replayed = call(
            "upstream.group_roll_exit_orders",
            _SUBMIT,
            {"authorization": authorization},
            _ENTRY_SCOPE_REF,
        )
        assert replayed["ok"] is True and replayed["result"]["phase"] == "RESOLVED", replayed

        changed_request = {**request, "limit_price": "102"}
        exhausted = call(
            "upstream.group_roll_exit_order_query",
            _PREPARE,
            changed_request,
            _QUERY_SCOPE_REF,
        )
        assert exhausted["ok"] is False
        assert exhausted["code"] == "GROUP_ROLL_EXIT_CLOSE_BOUND_EMPTY", exhausted
        assert close_feed is not None and len(close_feed.calls) == 1
        assert close_feed.calls[0][:4] == (
            dim05._V2_INSTRUMENT,
            Decimal("1.5"),
            Decimal("101"),
            "sell-limit",
        )
    finally:
        if runtime is not None and runtime_started:
            runtime.close()
            assert runtime.state == "closed"
        host_loop.close()

    # Reopen the owner ledger only after normal Host shutdown. A's CLOSE was
    # settled against its own source fill; the peer's seeded share is intact.
    persisted_journal = RiskOrderJournal(
        risk_journal_path, writer_id="dim05-roll-exit-postcheck"
    )
    persisted_gate = DurableRiskGate(
        risk_gate_path,
        dim05._risk_policy(),
        execution_journal_authority=persisted_journal,
        gross_exposure_authority=persisted_journal,
    )
    try:
        source_risk_scope = source_holder["graph"].risk_scope
        assert _available(
            persisted_gate,
            source_risk_scope,
            dim05._V2_INSTRUMENT,
            dim05._STRATEGY,
        ) == Decimal("0")
        assert _available(
            persisted_gate,
            source_risk_scope,
            dim05._V2_INSTRUMENT,
            _STRATEGY_B,
        ) == Decimal("0.75")
    finally:
        persisted_gate.close()
