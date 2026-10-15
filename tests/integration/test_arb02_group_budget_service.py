"""ARB02 service-path proof for shared portfolio budget competition.

Two independent strategy clients target the same portfolio and two-account
set. The first request reaches a synthetic provider write and is held there;
the second client's request starts while that first RPC is still in flight.
The first group then becomes UNKNOWN and keeps its gross budget hold, while
the second group is denied before any account reservation or provider call.

Only local CURVE ServiceHosts and synthetic SQLite provider leaves are used.
"""

from __future__ import annotations

import multiprocessing
import queue
import threading
import time
from collections.abc import Callable, Mapping
from contextlib import suppress
from decimal import Decimal
from functools import partial
from pathlib import Path
from types import MappingProxyType
from typing import Any

import pytest
import zmq
from bt_api_base.service_protocol import WireIdentity, WireRole, WireScope
from bt_api_execution import (
    AccountScopedGroupAdmissionAdapter,
    ExecutionScope,
    ExecutionState,
    GroupBudgetReservationState,
    GroupOrderSnapshot,
    GroupOrderState,
    OrderIntent,
    OrderType,
    PortfolioGroupBudgetPolicy,
    Side,
    SqliteExecutionStore,
    SqliteOrderGroupStore,
    TimeInForce,
    build_instrument_risk_group_budget_quote,
    group_leg_intent_id,
    risk_account_scope_fingerprint,
)
from bt_api_risk import (
    INSTRUMENT_METADATA_DIGEST_TAG,
    AccountScope,
    DispatchEvidenceClass,
    DurableRiskAdmissionAdapter,
    DurableRiskGate,
    ExposureAccountingMode,
    InstrumentRiskAdmissionMapper,
    InstrumentRiskRegistry,
    RiskDeniedError,
    RiskPolicy,
)

from bt_api_py.bt_api import BtApi
from bt_api_py.service_components import ComponentResources
from bt_api_py.service_config import parse_client_config, parse_service_config
from bt_api_py.service_execution_group import (
    GroupAccountRoute,
    GroupCoordinatorTarget,
    GroupInstrumentRule,
)
from bt_api_py.service_execution_group_authority import (
    ExecutionGroupAuthority,
    ExecutionStoreGroupCompletionVerifier,
)
from bt_api_py.service_execution_group_client import (
    AcceptedGroupBindingStore,
    GroupOriginRunBindingStore,
)
from bt_api_py.service_execution_group_proofs import (
    GroupProofJournal,
    GroupProofRuntimeBundle,
    GroupProofSource,
    GroupProofTarget,
    GroupReservationTarget,
)
from bt_api_py.service_host import (
    ServiceHost,
    TrustedServiceAuthority,
    TrustedServiceClients,
)
from bt_api_py.service_registry import (
    PrincipalGrant,
    ResourceGrant,
    TrustedGroupReservationPolicy,
    TrustedGroupResolutionPolicy,
    TrustedGroupWritePolicy,
)
from bt_api_py.service_risk_order_entry import RiskBtApiProviderContract, RiskOrderTarget
from bt_api_py.service_risk_order_journal import RiskOrderJournal
from bt_api_py.service_runtime import (
    ClientCurveKeys,
    ServiceClientRuntime,
    ZmqTransportFactory,
)
from tests.integration.test_service_execution_group_local_process import (
    _account_service_config,
    _component,
    _endpoint,
    _metadata,
    _plain_runtime_json,
    _profile,
    _provider_snapshot,
    _receive,
    _stop_process,
)
from tests.unit._service_group_provider_fixtures import (
    _V2_FILL_SCHEMA,
    _V2_INSTRUMENT,
    _V2_ORDER_SCHEMA,
    _V2SyntheticBtApiFeed,
    _arm_incomplete_fills,
)

_DOMAIN = "paper:arb02-budget-service"
_PORTFOLIO = "portfolio-arb02-shared"
_ACCOUNTS = ("account-a", "account-b")
_STRATEGIES = ("strategy-arb02-a", "strategy-arb02-b")
_RUNS = {
    "strategy-arb02-a": "run-arb02-strategy-a",
    "strategy-arb02-b": "run-arb02-strategy-b",
}
_PRINCIPALS = {
    "strategy-arb02-a": "principal-arb02-strategy-a",
    "strategy-arb02-b": "principal-arb02-strategy-b",
}
_COORDINATOR_SERVICE_ID = "arb02-group-coordinator-service"
_COORDINATOR_INSTANCE_ID = "arb02-group-coordinator"
_COORDINATOR_PRINCIPAL = "arb02-group-coordinator-principal"
_MESSAGE_LIMIT = 65_536
_REQUEST_TIMEOUT_MS = 8_000
_SERVER_OPTIONS = {"high_water_mark": 32, "max_message_bytes": _MESSAGE_LIMIT}
_GROUP_ACCEPTED = "arb02-last-budget-accepted"
_GROUP_DENIED = "arb02-last-budget-denied"


def _attempt_cleanup(actions: tuple[Callable[[], object], ...]) -> BaseException | None:
    """Run every cleanup action and return the first failure, if any."""

    first_failure: BaseException | None = None
    for action in actions:
        try:
            action()
        except BaseException as error:
            if first_failure is None:
                first_failure = error
    return first_failure


def _cleanup_failed_host_start(
    process: Any | None,
    parent_control: Any,
    child_control: Any,
    *,
    process_started: bool,
) -> BaseException | None:
    """Close a test-owned Host child whose readiness handshake did not complete."""

    failures: list[BaseException] = []

    def attempt(action: Callable[[], object]) -> None:
        try:
            action()
        except BaseException as error:
            failures.append(error)

    started = process_started
    if process is None:
        started = False
    elif not started:
        try:
            started = process.pid is not None
        except BaseException as error:
            failures.append(error)

    if started:
        try:
            alive = bool(process.is_alive())
        except BaseException as error:
            failures.append(error)
            alive = True

        if alive:
            sent_close = False
            try:
                parent_control.send("close")
                sent_close = True
            except BaseException as error:
                failures.append(error)
            if sent_close:
                try:
                    if parent_control.poll(2):
                        response = parent_control.recv()
                        if (
                            not isinstance(response, tuple)
                            or not response
                            or response[0] != "closed"
                        ):
                            failures.append(
                                RuntimeError("Host child did not acknowledge normal close")
                            )
                    else:
                        failures.append(
                            TimeoutError("Host child did not acknowledge normal close")
                        )
                except BaseException as error:
                    failures.append(error)

        attempt(lambda: process.join(2))
        try:
            alive = bool(process.is_alive())
        except BaseException as error:
            failures.append(error)
            alive = True
        if alive:
            attempt(process.terminate)
            attempt(lambda: process.join(5))
            try:
                alive = bool(process.is_alive())
            except BaseException as error:
                failures.append(error)
                alive = True
        if alive:
            kill = getattr(process, "kill", None)
            if callable(kill):
                attempt(kill)
                attempt(lambda: process.join(5))
                try:
                    alive = bool(process.is_alive())
                except BaseException as error:
                    failures.append(error)
                    alive = True
        if alive:
            failures.append(RuntimeError("Host child remained alive after failed startup"))
        else:
            attempt(process.close)
    else:
        # ``Process.start`` can fail before a child PID exists; close the
        # unstarted Process object as well as both pipe endpoints.
        if process is not None:
            attempt(process.close)

    # The child endpoint may already have been closed after a successful start;
    # both close attempts are still required on every failure path.
    attempt(parent_control.close)
    attempt(child_control.close)
    return failures[0] if failures else None


def _cleanup_group_hosts(
    *,
    provider_release: Any,
    client_threads: Mapping[str, threading.Thread],
    client_commands: Mapping[str, queue.Queue],
    client_results: Mapping[str, queue.Queue],
    coordinator_process: Any,
    coordinator_control: Any,
    account_processes: Mapping[str, Any],
    account_controls: Mapping[str, Any],
    close_client_threads: Callable[..., object],
    stop_process: Callable[..., object],
) -> None:
    actions: list[Callable[[], object]] = [provider_release.set]
    if client_threads:
        actions.append(
            lambda: close_client_threads(client_threads, client_commands, client_results)
        )
    if coordinator_process is not None and coordinator_control is not None:
        actions.append(lambda: stop_process(coordinator_process, coordinator_control))
    for account_ref in reversed(_ACCOUNTS):
        process = account_processes.get(account_ref)
        control = account_controls.get(account_ref)
        if process is not None and control is not None:
            actions.append(lambda process=process, control=control: stop_process(process, control))
    failure = _attempt_cleanup(tuple(actions))
    if failure is not None:
        raise failure


def _route_refs(account_ref: str, strategy_ref: str) -> tuple[str, str, str]:
    return (
        f"arb02-reservation-{account_ref}-{strategy_ref}",
        f"arb02-order-entry-{account_ref}-{strategy_ref}",
        f"arb02-order-query-{account_ref}-{strategy_ref}",
    )


def _owner_scope(account_ref: str, strategy_ref: str) -> WireScope:
    return WireScope(
        _DOMAIN,
        account_ref=account_ref,
        strategy_ref=strategy_ref,
        portfolio_ref=_PORTFOLIO,
    )


def _stable_scope(strategy_ref: str) -> WireScope:
    return WireScope(
        _DOMAIN,
        account_refs=_ACCOUNTS,
        strategy_ref=strategy_ref,
        portfolio_ref=_PORTFOLIO,
    )


def _execution_scope(account_ref: str, strategy_ref: str) -> ExecutionScope:
    return ExecutionScope("fixture", "simulation", account_ref, strategy_ref, "20261016")


def _coordinator_scope_ref(strategy_ref: str) -> str:
    return f"arb02-coordinator-route-{strategy_ref}"


def _coordinator_client_config(
    endpoint: str, server_security_ref: str, strategy_ref: str
) -> dict[str, Any]:
    security_ref = f"arb02-client-security-{strategy_ref}"
    return {
        "client_config_schema_version": 1,
        "process_role": "strategy_client",
        "registration": {"strategy_ref": strategy_ref},
        "clients": {
            "execution": {
                "config_schema_version": 1,
                "settings": {
                    "bindings": [
                        {
                            "mount": "broker.order_groups",
                            "service_id": _COORDINATOR_SERVICE_ID,
                            "instance_id": _COORDINATOR_INSTANCE_ID,
                            "scope_ref": _coordinator_scope_ref(strategy_ref),
                            "portfolio_ref": _PORTFOLIO,
                            "transport_ref": f"arb02-client-{strategy_ref}",
                        }
                    ],
                    "options": {
                        "request_timeout_ms": _REQUEST_TIMEOUT_MS,
                        "heartbeat_interval_seconds": 5,
                    },
                },
            }
        },
        "communication": {
            "config_schema_version": 1,
            "profiles": {
                f"arb02-client-{strategy_ref}": _profile(
                    endpoint, server=False, security_ref=security_ref
                )
            },
        },
    }


def _coordinator_service_config(
    endpoint: str,
    account_configs: Mapping[str, dict[str, Any]],
    account_endpoints: Mapping[str, str],
) -> dict[str, Any]:
    execution_bindings: list[dict[str, Any]] = []
    risk_bindings: list[dict[str, Any]] = []
    profiles: dict[str, dict[str, Any]] = {
        "arb02-listener": _profile(endpoint, server=True, security_ref="arb02-coordinator-server")
    }
    for strategy_ref in _STRATEGIES:
        for account_ref in _ACCOUNTS:
            account_config = account_configs[account_ref]
            account_service_id = account_config["service_id"]
            execution_settings = account_config["components"]["execution"]["settings"]
            risk_settings = account_config["components"]["risk"]["settings"]
            reservation_ref, entry_ref, query_ref = _route_refs(account_ref, strategy_ref)
            transport_ref = f"arb02-account-client-{account_ref}"
            security_ref = f"arb02-account-security-{account_ref}"
            profiles.setdefault(
                transport_ref,
                _profile(account_endpoints[account_ref], server=False, security_ref=security_ref),
            )
            execution_bindings.extend(
                (
                    {
                        "mount": "upstream.orders",
                        "service_id": account_service_id,
                        "instance_id": execution_settings["instance_id"],
                        "scope_ref": entry_ref,
                        "account_ref": account_ref,
                        "transport_ref": transport_ref,
                    },
                    {
                        "mount": "upstream.order_query",
                        "service_id": account_service_id,
                        "instance_id": execution_settings["instance_id"],
                        "scope_ref": query_ref,
                        "account_ref": account_ref,
                        "transport_ref": transport_ref,
                    },
                )
            )
            risk_bindings.append(
                {
                    "mount": "upstream.account_reservations",
                    "service_id": account_service_id,
                    "instance_id": risk_settings["instance_id"],
                    "scope_ref": reservation_ref,
                    "account_ref": account_ref,
                    "transport_ref": transport_ref,
                }
            )

    def client_block(bindings: list[dict[str, Any]]) -> dict[str, Any]:
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

    return {
        "service_config_schema_version": 1,
        "process_role": "service",
        "service_id": _COORDINATOR_SERVICE_ID,
        "scope_ref": "arb02-coordinator-root",
        "transport_ref": "arb02-listener",
        "registration_policy_ref": "arb02-coordinator-registration-v1",
        "service_requirements_ref": "arb02-coordinator-requirements-v1",
        "components": {
            "execution": _component(
                _COORDINATOR_INSTANCE_ID,
                ("service.order_group_entry",),
                "group_coordinator",
                portfolio_ref=_PORTFOLIO,
            )
        },
        "clients": {
            "execution": client_block(execution_bindings),
            "risk": client_block(risk_bindings),
        },
        "communication": {"config_schema_version": 1, "profiles": profiles},
    }


class _WriteBlockingSyntheticFeed(_V2SyntheticBtApiFeed):
    def __init__(
        self,
        ledger_path: str,
        initial_states: tuple[str, ...],
        provider_account: str,
        entered: Any,
        release: Any,
    ) -> None:
        super().__init__(ledger_path, initial_states, provider_account)
        self._entered_event = entered
        self._release_event = release

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
        result = super().make_order(
            symbol,
            volume,
            price,
            order_type,
            client_order_id=client_order_id,
            **kwargs,
        )
        self._entered_event.set()
        if not self._release_event.wait(30):
            raise TimeoutError("synthetic first provider write was not released")
        return result


class _MultiAccountResources:
    def __init__(
        self,
        *,
        bt_api: BtApi,
        execution_store: SqliteExecutionStore,
        risk_order_journal: RiskOrderJournal,
        proof_journal: GroupProofJournal,
        risk_gate: DurableRiskGate,
        accepted_store: AcceptedGroupBindingStore,
        authority: ExecutionGroupAuthority,
        bundle: GroupProofRuntimeBundle,
        provider: _V2SyntheticBtApiFeed,
        risk_scope: AccountScope,
    ) -> None:
        self.bt_api = bt_api
        self.execution_store = execution_store
        self.risk_order_journal = risk_order_journal
        self.proof_journal = proof_journal
        self.risk_gate = risk_gate
        self.accepted_store = accepted_store
        self.authority = authority
        self.bundle = bundle
        self.provider = provider
        self.risk_scope = risk_scope

    def close(self) -> None:
        # RiskOrderJournal opens SQLite connections per operation and has no
        # persistent close method; only the other fixture-owned resources
        # expose close().
        failure = _attempt_cleanup((
            self.authority.close,
            self.accepted_store.close,
            self.risk_gate.close,
            self.proof_journal.close,
            self.execution_store.close,
            self.bt_api.close,
        ))
        if failure is not None:
            raise RuntimeError("ARB02 synthetic account resources failed to close") from failure


def _risk_policy() -> RiskPolicy:
    return RiskPolicy(
        "arb02-account-risk-v1",
        Decimal("100000"),
        max_increase_count=20,
        permit_ttl_seconds=240,
        require_strategy_allocation=False,
        notional_unit="USD",
        exposure_accounting_mode=ExposureAccountingMode.RESERVATIONS_ONLY,
    )


def _new_account_resources(
    account_ref: str,
    paths: Mapping[str, str],
    initial_states: tuple[str, ...],
    metadata_reference_ns: int,
    entered: Any,
    release: Any,
) -> _MultiAccountResources:
    provider_account = f"provider-{account_ref}"
    if account_ref == "account-a":
        provider = _WriteBlockingSyntheticFeed(
            paths["provider"], initial_states, provider_account, entered, release
        )
    else:
        provider = _V2SyntheticBtApiFeed(paths["provider"], initial_states, provider_account)

    from bt_api_py._venue_mappers import MAPPERS

    MAPPERS["FIXTURE___SPOT"] = MAPPERS["BINANCE___SPOT"]
    bt_api = BtApi(debug=False)
    bt_api.exchange_feeds["FIXTURE___SPOT"] = provider
    risk_scope = AccountScope("fixture", provider_account, "simulation")
    metadata = _metadata(metadata_reference_ns)
    registry = InstrumentRiskRegistry((metadata,))
    execution_scopes = {
        strategy_ref: _execution_scope(account_ref, strategy_ref) for strategy_ref in _STRATEGIES
    }
    owner_scopes = {
        strategy_ref: _owner_scope(account_ref, strategy_ref) for strategy_ref in _STRATEGIES
    }
    risk_targets = {
        strategy_ref: RiskOrderTarget(
            account_scope=risk_scope,
            strategy_id=strategy_ref,
            exchange_name="FIXTURE___SPOT",
            provider_account_id=provider_account,
            registry=registry,
        )
        for strategy_ref in _STRATEGIES
    }
    execution_store = SqliteExecutionStore(paths["execution"])
    risk_order_journal = RiskOrderJournal(
        paths["risk_order_journal"], writer_id=f"arb02-risk-order-{account_ref}"
    )
    proof_journal = GroupProofJournal(paths["proof"], writer_id=f"arb02-group-proof-{account_ref}")
    provider_contract = RiskBtApiProviderContract(
        exchange_name="FIXTURE___SPOT",
        profile_id="arb02-group-provider-v2",
        order_schema=_V2_ORDER_SCHEMA,
        fill_schema=_V2_FILL_SCHEMA,
        client_order_id_query=True,
        client_order_id_query_unique=True,
        complete_order_snapshot=True,
        complete_fill_history=True,
        provider_accepts_client_order_id=True,
        provider_status_mapping=(
            ("cancelled", "canceled"),
            ("expired", "expired"),
            ("filled", "completed"),
            ("open", "accepted"),
            ("partial", "partial"),
            ("rejected", "rejected"),
        ),
        supported_order_types=("LIMIT",),
        supported_time_in_force=("IOC",),
        supported_position_effects=("OPEN",),
        normalizer_schema="bt-api-order-source-provenance-v2",
        order_revision_field="order_revision",
        fill_revision_field="fill_revision",
        fill_watermark_field="fill_watermark",
        fee_amount_field="fee_amount",
        fee_currency_field="fee_currency",
        quote_currency_by_instrument=((_V2_INSTRUMENT, "USD"),),
    )
    proof_targets = {
        owner_scopes[strategy_ref]: GroupProofTarget(
            execution_scope=execution_scopes[strategy_ref],
            risk_target=risk_targets[strategy_ref],
            provider_contract=provider_contract,
            bt_api=bt_api,
            execution_store=execution_store,
            risk_order_journal=risk_order_journal,
            evidence_class=DispatchEvidenceClass.SIMULATION_JOURNAL,
        )
        for strategy_ref in _STRATEGIES
    }
    proof_source = GroupProofSource(proof_journal, proof_targets)
    risk_gate = DurableRiskGate(
        paths["risk_gate"], _risk_policy(), execution_journal_authority=proof_source
    )
    for strategy_ref in _STRATEGIES:
        risk_gate.set_strategy_allocation(
            risk_scope,
            strategy_ref,
            "arb02-allocation-v1",
            max_notional=Decimal("100000"),
            notional_unit="USD",
        )
    mapper = InstrumentRiskAdmissionMapper(
        risk_scope,
        registry,
        clock_ns=time.time_ns,
        allocation_reader=lambda routed_scope: risk_gate.get_strategy_allocation(
            risk_scope, routed_scope.strategy_id
        ),
        wire_account_ref=account_ref,
    )
    risk_admission = DurableRiskAdmissionAdapter(risk_gate, mapper)
    account_admission = AccountScopedGroupAdmissionAdapter(risk_admission, account_scope=risk_scope)
    reservation_targets = {
        owner_scope: GroupReservationTarget(risk_scope, risk_admission)
        for owner_scope in owner_scopes.values()
    }
    bundle = GroupProofRuntimeBundle(
        risk_admission,
        account_admission,
        MappingProxyType(reservation_targets),
        MappingProxyType(proof_targets),
        proof_source,
    )
    bundle.require_runtime_bundle(
        account_scoped_admission=account_admission,
        risk_admission=risk_admission,
    )
    accepted_store = AcceptedGroupBindingStore(paths["accepted"])
    authority = ExecutionGroupAuthority(
        paths["group_authority"],
        completion_verifier=ExecutionStoreGroupCompletionVerifier(
            execution_store, execution_scopes=tuple(execution_scopes.values())
        ),
    )
    return _MultiAccountResources(
        bt_api=bt_api,
        execution_store=execution_store,
        risk_order_journal=risk_order_journal,
        proof_journal=proof_journal,
        risk_gate=risk_gate,
        accepted_store=accepted_store,
        authority=authority,
        bundle=bundle,
        provider=provider,
        risk_scope=risk_scope,
    )


def _account_host_process(
    control: Any,
    raw_config: dict[str, Any],
    registry_path: str,
    paths: dict[str, str],
    account_ref: str,
    server_public: bytes,
    server_secret: bytes,
    coordinator_client_public: bytes,
    metadata_reference_ns: int,
    initial_states: tuple[str, ...],
    provider_entered: Any,
    provider_release: Any,
) -> None:
    config = parse_service_config(raw_config)
    coordinator_identity = WireIdentity(
        WireRole.SERVICE, _COORDINATOR_PRINCIPAL, _COORDINATOR_SERVICE_ID
    )
    execution_instance = f"group-execution-owner-{account_ref}"
    risk_instance = f"group-risk-reservations-{account_ref}"
    account_scopes = {strategy: _owner_scope(account_ref, strategy) for strategy in _STRATEGIES}
    write_policies = []
    resolution_policies = []
    reservation_policies = []
    resources_by_strategy: dict[str, tuple[str, str, str]] = {}
    for strategy_ref in _STRATEGIES:
        reservation_ref, entry_ref, query_ref = _route_refs(account_ref, strategy_ref)
        resources_by_strategy[strategy_ref] = (reservation_ref, entry_ref, query_ref)
        run_id = _RUNS[strategy_ref]
        write_policies.append(
            TrustedGroupWritePolicy(
                execution_instance,
                _COORDINATOR_PRINCIPAL,
                coordinator_identity,
                entry_ref,
                account_scopes[strategy_ref],
                run_id,
            )
        )
        resolution_policies.append(
            TrustedGroupResolutionPolicy(
                execution_instance,
                _COORDINATOR_PRINCIPAL,
                coordinator_identity,
                entry_ref,
                account_scopes[strategy_ref],
                run_id,
                account_ref,
            )
        )
        reservation_policies.append(
            TrustedGroupReservationPolicy(
                risk_instance,
                _COORDINATOR_PRINCIPAL,
                coordinator_identity,
                reservation_ref,
                account_scopes[strategy_ref],
                run_id,
                account_ref,
            )
        )
    resources: _MultiAccountResources | None = None
    host: ServiceHost | None = None

    def resource_provider(component, _instance_id, _options, _scope):
        nonlocal resources
        if resources is None:
            resources = _new_account_resources(
                account_ref,
                paths,
                initial_states,
                metadata_reference_ns,
                provider_entered,
                provider_release,
            )
        if component == "execution":
            return ComponentResources.borrowed(
                {
                    "store": resources.execution_store,
                    "dispatch": _UnusedGroupDispatch(),
                    "accepted_group_store": resources.accepted_store,
                    "group_authority": resources.authority.guard,
                    "group_runtime_bundle": resources.bundle,
                },
                ready=lambda: True,
            )
        if component == "risk":
            return ComponentResources.borrowed(
                {
                    "accepted_group_store": resources.accepted_store,
                    "group_runtime_bundle": resources.bundle,
                },
                ready=lambda: True,
            )
        raise AssertionError(f"unexpected account component {component!r}")

    grants: list[ResourceGrant] = []
    for strategy_ref in _STRATEGIES:
        reservation_ref, entry_ref, query_ref = resources_by_strategy[strategy_ref]
        scope = account_scopes[strategy_ref]
        grants.extend(
            (
                ResourceGrant(
                    execution_instance,
                    "service.order_entry",
                    entry_ref,
                    scope,
                    frozenset({"orders.submit_group_leg", "orders.resolve_group_leg"}),
                ),
                ResourceGrant(
                    execution_instance,
                    "service.order_query",
                    query_ref,
                    scope,
                    frozenset({"orders.query_group_leg"}),
                ),
                ResourceGrant(
                    risk_instance,
                    "service.group_reservations",
                    reservation_ref,
                    scope,
                    frozenset(
                        {"reservations.reserve", "reservations.query", "reservations.release"}
                    ),
                ),
            )
        )

    try:
        host = ServiceHost(
            config,
            TrustedServiceAuthority(
                identity=WireIdentity(
                    WireRole.SERVICE,
                    f"arb02-owner-service-principal-{account_ref}",
                    config.service_id,
                ),
                scope=WireScope(_DOMAIN),
                scope_ref=config.scope_ref,
                scope_version="arb02-owner-scope-v1",
                security_ref=f"arb02-account-server-{account_ref}",
                security_version=f"arb02-account-key-{account_ref}-v1",
                registration_policy_ref=config.registration_policy_ref,
                registration_policy_version="arb02-owner-registration-v1",
                service_requirements_ref=config.service_requirements_ref,
                service_requirements_version="arb02-owner-requirements-v1",
                curve_public_key=server_public,
                curve_secret_key=server_secret,
                principals={coordinator_client_public: _COORDINATOR_PRINCIPAL},
                grants={
                    _COORDINATOR_PRINCIPAL: (PrincipalGrant(coordinator_identity, tuple(grants)),)
                },
                group_write_policies=tuple(write_policies),
                group_reservation_policies=tuple(reservation_policies),
                group_resolution_policies=tuple(resolution_policies),
            ),
            registry_path,
            resource_provider,
        )
        host.start()
        control.send(("ready", host.endpoint))
        while True:
            if control.poll(0):
                command = control.recv()
                if command == "close":
                    host.close()
                    control.send(("closed", host.state))
                    return
                if isinstance(command, tuple) and command[0] == "activate":
                    registration_id = command[1]
                    if type(registration_id) is not str or resources is None:
                        raise AssertionError("activation command has no local account resources")
                    registry = host._registry
                    if registry is None:
                        raise AssertionError("Host registry is unavailable")
                    contexts = registry.active_registration_contexts(
                        instance_id=execution_instance,
                        port="service.order_entry",
                    )
                    matches = tuple(
                        item for item in contexts if item.context.registration_id == registration_id
                    )
                    if len(matches) != 1:
                        raise AssertionError("activation registration was not exact")
                    registration = matches[0]
                    strategy_ref = registration.context.scope.strategy_ref
                    if strategy_ref not in _STRATEGIES:
                        raise AssertionError("activation selected an untrusted strategy")
                    assert registration.context.identity == coordinator_identity
                    assert registration.strategy_write_run_id == _RUNS[strategy_ref]
                    generation = resources.authority.control.activate(
                        registration.context,
                        execution_scope=_execution_scope(account_ref, strategy_ref),
                        risk_account_scope_key=resources.risk_scope.key,
                    )
                    control.send(
                        ("activated", registration_id, generation.generation, strategy_ref)
                    )
                elif isinstance(command, tuple) and command[0] == "arm-incomplete-fills":
                    _arm_incomplete_fills(paths["provider"])
                    control.send(("armed", account_ref))
                elif isinstance(command, tuple) and command[0] == "status":
                    if resources is None:
                        raise AssertionError("account resources are unavailable")
                    control.send(("status", *_provider_snapshot(Path(paths["provider"]))))
            host.serve_once(10)
    except BaseException as error:
        if host is not None and host.state not in {"closed", "failed"}:
            with suppress(BaseException):
                host.close()
        with suppress(Exception):
            control.send(("error", getattr(error, "code", type(error).__name__)))
        raise
    finally:
        actions: list[Callable[[], object]] = []
        if host is not None and host.state != "closed":
            actions.append(host.close)
        if resources is not None:
            actions.append(resources.close)
        # Attempt the pipe close even if Host or resource cleanup failed.
        actions.append(control.close)
        failure = _attempt_cleanup(tuple(actions))
        if failure is not None:
            raise failure


class _UnusedGroupDispatch:
    def submit(self, _intent: OrderIntent) -> object:
        raise AssertionError("group proof session must own provider writes")

    def cancel(self, _intent: object) -> object:
        raise AssertionError("group-only account owner cannot cancel here")


def _budget_policy() -> PortfolioGroupBudgetPolicy:
    return PortfolioGroupBudgetPolicy(
        portfolio_ref=_PORTFOLIO,
        policy_id="arb02-shared-gross-budget-v1",
        policy_version=1,
        valuation_unit="USD",
        max_group_gross_notional=Decimal("200"),
        max_active_gross_notional=Decimal("200"),
        max_active_groups=8,
    )


def _coordinator_host_process(
    control: Any,
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
    strategy_client_public: dict[str, bytes],
    strategy_client_secret: dict[str, bytes],
    metadata_reference_ns: int,
) -> None:
    config = parse_service_config(raw_config)
    account_configs = {
        account: parse_service_config(raw_account_configs[account]) for account in _ACCOUNTS
    }
    service_identity = WireIdentity(
        WireRole.SERVICE, _COORDINATOR_PRINCIPAL, _COORDINATOR_SERVICE_ID
    )
    account_scope_by_ref: dict[str, WireScope] = {}
    peer_by_service: dict[str, WireIdentity] = {}
    security_profiles: dict[str, ClientCurveKeys] = {}
    for strategy_ref in _STRATEGIES:
        for account_ref in _ACCOUNTS:
            reservation_ref, entry_ref, query_ref = _route_refs(account_ref, strategy_ref)
            account_scope = _owner_scope(account_ref, strategy_ref)
            for scope_ref in (reservation_ref, entry_ref, query_ref):
                account_scope_by_ref[scope_ref] = account_scope
            account_service_id = account_configs[account_ref].service_id
            peer_by_service[account_service_id] = WireIdentity(
                WireRole.SERVICE,
                f"arb02-owner-service-principal-{account_ref}",
                account_service_id,
            )
            security_profiles[f"arb02-account-security-{account_ref}"] = ClientCurveKeys(
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
                {"reservations.reserve", "reservations.query", "reservations.release"}
            ),
        },
    )
    metadata = _metadata(metadata_reference_ns)
    policy = _budget_policy()
    quote_builder = partial(
        build_instrument_risk_group_budget_quote,
        registry=InstrumentRiskRegistry((metadata,)),
    )
    targets: dict[WireScope, GroupCoordinatorTarget] = {}
    for strategy_ref in _STRATEGIES:
        route_targets: dict[str, GroupAccountRoute] = {}
        for account_ref in _ACCOUNTS:
            provider_account = f"provider-{account_ref}"
            risk_scope = AccountScope("fixture", provider_account, "simulation")
            execution_scope = _execution_scope(account_ref, strategy_ref)
            reservation_ref, entry_ref, query_ref = _route_refs(account_ref, strategy_ref)
            route_targets[account_ref] = GroupAccountRoute(
                execution_scope=execution_scope,
                risk_scope_fingerprint=risk_account_scope_fingerprint(risk_scope.key),
                reservation_scope_ref=reservation_ref,
                order_scope_ref=entry_ref,
                query_scope_ref=query_ref,
                instruments={
                    metadata.instrument: GroupInstrumentRule(
                        metadata=metadata,
                        base_units_per_order_unit=Decimal("1"),
                        allowed_order_types=frozenset({OrderType.LIMIT}),
                    )
                },
            )
        targets[_stable_scope(strategy_ref)] = GroupCoordinatorTarget(
            scope_ref=_coordinator_scope_ref(strategy_ref),
            accounts=route_targets,
            max_group_lifetime_ns=300_000_000_000,
            portfolio_budget_policy=policy,
            budget_quote_builder=quote_builder,
        )

    host_resources: dict[str, object] = {}
    host: ServiceHost | None = None

    def resource_provider(component, _instance_id, _options, _scope):
        if component != "execution":
            raise AssertionError("coordinator Host must expose one fixed component")
        group_store = SqliteOrderGroupStore(group_store_path)
        run_bindings = GroupOriginRunBindingStore(run_bindings_path)
        host_resources.update({"store": group_store, "run_bindings": run_bindings})
        return ComponentResources.borrowed(
            {"store": group_store, "targets": targets, "run_bindings": run_bindings},
            ready=lambda: True,
        )

    principals = {
        strategy_client_public[strategy_ref]: _PRINCIPALS[strategy_ref]
        for strategy_ref in _STRATEGIES
    }
    grants: dict[str, tuple[PrincipalGrant, ...]] = {}
    for strategy_ref in _STRATEGIES:
        identity = WireIdentity(WireRole.STRATEGY_CLIENT, _PRINCIPALS[strategy_ref], strategy_ref)
        grant = ResourceGrant(
            _COORDINATOR_INSTANCE_ID,
            "service.order_group_entry",
            _coordinator_scope_ref(strategy_ref),
            _stable_scope(strategy_ref),
            frozenset({"groups.submit", "groups.query"}),
        )
        grants[_PRINCIPALS[strategy_ref]] = (PrincipalGrant(identity, (grant,)),)
    try:
        host = ServiceHost(
            config,
            TrustedServiceAuthority(
                identity=service_identity,
                scope=WireScope(_DOMAIN),
                scope_ref=config.scope_ref,
                scope_version="arb02-coordinator-scope-v1",
                security_ref="arb02-coordinator-server",
                security_version="arb02-coordinator-key-v1",
                registration_policy_ref=config.registration_policy_ref,
                registration_policy_version="arb02-coordinator-registration-v1",
                service_requirements_ref=config.service_requirements_ref,
                service_requirements_version="arb02-coordinator-requirements-v1",
                curve_public_key=coordinator_server_public,
                curve_secret_key=coordinator_server_secret,
                principals=principals,
                grants=grants,
            ),
            registry_path,
            resource_provider,
            service_directory={
                _COORDINATOR_SERVICE_ID: config,
                **{
                    account_configs[account_ref].service_id: account_configs[account_ref]
                    for account_ref in _ACCOUNTS
                },
            },
            trusted_clients=trusted_clients,
        )
        host.start()
        upstream = host.upstream
        if upstream is None or not upstream.healthy:
            raise AssertionError("coordinator upstream client runtime is not healthy")
        bindings = tuple(
            (
                item.mount,
                item.scope.account_ref,
                item.scope.strategy_ref,
                item.scope_ref,
                item.registration_id,
                tuple(sorted(item.operations)),
            )
            for item in upstream.bindings
            if item.active
        )
        control.send(("ready", host.endpoint, bindings))
        while True:
            if control.poll(0):
                command = control.recv()
                if command == "close":
                    host.close()
                    control.send(("closed", host.state, upstream.state))
                    return
            host.serve_once(10)
    except BaseException as error:
        if host is not None and host.state not in {"closed", "failed"}:
            with suppress(BaseException):
                host.close()
        with suppress(Exception):
            control.send(("error", getattr(error, "code", type(error).__name__)))
        raise
    finally:
        actions = []
        if host is not None and host.state != "closed":
            actions.append(host.close)
        for name in ("run_bindings", "store"):
            resource = host_resources.get(name)
            close = getattr(resource, "close", None)
            if callable(close):
                actions.append(close)
        actions.append(control.close)
        failure = _attempt_cleanup(tuple(actions))
        if failure is not None:
            raise failure


def _strategy_client_thread(
    strategy_ref: str,
    endpoint: str,
    coordinator_server_public: bytes,
    client_public: bytes,
    client_secret: bytes,
    commands: queue.Queue,
    results: queue.Queue,
) -> None:
    identity = WireIdentity(WireRole.STRATEGY_CLIENT, _PRINCIPALS[strategy_ref], strategy_ref)
    coordinator_identity = WireIdentity(
        WireRole.SERVICE, _COORDINATOR_PRINCIPAL, _COORDINATOR_SERVICE_ID
    )
    security_ref = f"arb02-client-security-{strategy_ref}"
    runtime: ServiceClientRuntime | None = None
    closed = False
    try:
        runtime = ServiceClientRuntime(
            parse_client_config(_coordinator_client_config(endpoint, security_ref, strategy_ref)),
            identity=identity,
            scope_resolver=lambda _scope_ref: _stable_scope(strategy_ref),
            peer_resolver=lambda _service_id: coordinator_identity,
            transport_factory=ZmqTransportFactory(
                {
                    security_ref: ClientCurveKeys(
                        server_key=coordinator_server_public,
                        public_key=client_public,
                        secret_key=client_secret,
                    )
                }
            ),
            run_id=_RUNS[strategy_ref],
            requested_operations={
                "broker.order_groups": frozenset({"groups.submit", "groups.query"})
            },
        )
        receipt = runtime.start()
        results.put(("ready", receipt.degraded_count, tuple(receipt.bindings)))
        while True:
            command = commands.get()
            if command[0] == "close":
                runtime.close()
                closed = True
                results.put(("closed", runtime.state, runtime.unregister_uncertainties))
                return
            kind, group_id, payload = command
            started_ns = time.monotonic_ns()
            results.put(("started", kind, group_id, started_ns))
            try:
                if kind == "submit":
                    reply = runtime.call(
                        "broker.order_groups",
                        "groups.submit",
                        payload,
                        portfolio_ref=_PORTFOLIO,
                    )
                elif kind == "query":
                    reply = runtime.call(
                        "broker.order_groups",
                        "groups.query",
                        {"group_id": group_id},
                        portfolio_ref=_PORTFOLIO,
                    )
                else:
                    raise AssertionError("unknown client-thread operation")
                results.put(("done", kind, group_id, started_ns, time.monotonic_ns(), reply))
            except BaseException as error:
                results.put(
                    (
                        "call_error",
                        kind,
                        group_id,
                        type(error).__name__,
                        getattr(error, "code", None),
                    )
                )
    except BaseException as error:
        results.put(("thread_error", type(error).__name__, getattr(error, "code", None)))
    finally:
        if runtime is not None and not closed:
            try:
                runtime.close()
                results.put(("closed", runtime.state, runtime.unregister_uncertainties))
            except BaseException as error:
                results.put(("close_error", type(error).__name__, getattr(error, "code", None)))


def _new_client_threads(
    endpoint: str,
    coordinator_server_public: bytes,
    strategy_keys: Mapping[str, tuple[bytes, bytes]],
) -> tuple[dict[str, threading.Thread], dict[str, queue.Queue], dict[str, queue.Queue]]:
    threads: dict[str, threading.Thread] = {}
    commands: dict[str, queue.Queue] = {}
    results: dict[str, queue.Queue] = {}
    for strategy_ref in _STRATEGIES:
        command_queue: queue.Queue = queue.Queue()
        result_queue: queue.Queue = queue.Queue()
        public_key, secret_key = strategy_keys[strategy_ref]
        thread = threading.Thread(
            name=f"arb02-client-owner-{strategy_ref}",
            target=_strategy_client_thread,
            args=(
                strategy_ref,
                endpoint,
                coordinator_server_public,
                public_key,
                secret_key,
                command_queue,
                result_queue,
            ),
            daemon=True,
        )
        thread.start()
        threads[strategy_ref] = thread
        commands[strategy_ref] = command_queue
        results[strategy_ref] = result_queue
    for strategy_ref in _STRATEGIES:
        ready = _next_client_message(results[strategy_ref], "ready")
        assert ready[1] == 0
        assert len(ready[2]) == 1
    return threads, commands, results


def _next_client_message(
    messages: queue.Queue, expected_kind: str, timeout: float = 30
) -> tuple[Any, ...]:
    deadline = time.monotonic() + timeout
    while True:
        remaining = deadline - time.monotonic()
        assert remaining > 0, f"client owner did not produce {expected_kind!r}"
        message = messages.get(timeout=remaining)
        if (
            message[0] == "thread_error"
            or message[0] == "call_error"
            or message[0] == "close_error"
        ):
            raise AssertionError(f"strategy client failed: {message!r}")
        if message[0] == expected_kind:
            return message
        raise AssertionError(f"unexpected strategy client message: {message!r}")


def _group_request(
    group_id: str,
    strategy_ref: str,
    metadata_reference_ns: int,
) -> dict[str, Any]:
    now_ns = time.time_ns()
    deadline_ns = now_ns + 120_000_000_000
    metadata = _metadata(metadata_reference_ns)
    legs = []
    for leg_id, account_ref, side in (
        ("leg-a", "account-a", Side.BUY),
        ("leg-b", "account-b", Side.SELL),
    ):
        intent = OrderIntent.limit(
            intent_id=group_leg_intent_id(group_id, leg_id),
            scope=_execution_scope(account_ref, strategy_ref),
            signal_id=f"{group_id}-{leg_id}",
            instrument=metadata.instrument,
            side=side,
            quantity=Decimal("1"),
            price=Decimal("100"),
            time_in_force=TimeInForce.IOC,
            metadata_version=metadata.metadata_version,
            deadline_ns=deadline_ns,
            tags={INSTRUMENT_METADATA_DIGEST_TAG: metadata.digest},
        )
        legs.append({"leg_id": leg_id, "account_ref": account_ref, "intent": intent.to_payload()})
    return {
        "group_id": group_id,
        "deadline_ns": deadline_ns,
        "first_leg_id": "leg-a",
        "legs": legs,
    }


def _snapshot_from_reply(reply: Mapping[str, Any]) -> GroupOrderSnapshot:
    assert reply.get("ok") is True, reply
    assert reply.get("code") == "OK", reply
    result = reply.get("result")
    assert isinstance(result, Mapping) and result.get("found") is True, result
    return GroupOrderSnapshot.from_payload(_plain_runtime_json(result["snapshot"]))


def _query_reply_snapshot(reply: Mapping[str, Any]) -> GroupOrderSnapshot:
    assert reply.get("ok") is True, reply
    assert reply.get("code") == "OK", reply
    result = reply.get("result")
    assert isinstance(result, Mapping) and result.get("found") is True, result
    return GroupOrderSnapshot.from_payload(_plain_runtime_json(result["snapshot"]))


def _start_coordinator(
    context: Any,
    account_configs: dict[str, dict[str, Any]],
    paths: dict[str, str],
    account_endpoints: dict[str, str],
    account_server_public: dict[str, bytes],
    coordinator_keys: tuple[bytes, bytes],
    coordinator_client_keys: tuple[bytes, bytes],
    strategy_keys: Mapping[str, tuple[bytes, bytes]],
    metadata_reference_ns: int,
) -> tuple[Any, Any, str, tuple[Any, ...]]:
    endpoint = _endpoint()
    server_public, server_secret = coordinator_keys
    client_public, client_secret = coordinator_client_keys
    # Build the service config for the endpoint supplied by the parent.
    raw_config = _coordinator_service_config(endpoint, account_configs, account_endpoints)
    parent_control, child_control = context.Pipe(duplex=True)
    process = None
    process_started = False
    try:
        process = context.Process(
            name="arb02-group-budget-coordinator",
            target=_coordinator_host_process,
            args=(
                child_control,
                raw_config,
                account_configs,
                paths["registry"],
                paths["groups"],
                paths["run_bindings"],
                account_endpoints,
                account_server_public,
                server_public,
                server_secret,
                client_public,
                client_secret,
                {strategy: strategy_keys[strategy][0] for strategy in _STRATEGIES},
                {strategy: strategy_keys[strategy][1] for strategy in _STRATEGIES},
                metadata_reference_ns,
            ),
        )
        process.start()
        process_started = True
        child_control.close()
        ready = _receive(parent_control)
        assert ready[0] == "ready", ready
        assert ready[1] == endpoint
        bindings = ready[2]
        expected = {
            (mount, account, strategy, scope_ref)
            for strategy in _STRATEGIES
            for account in _ACCOUNTS
            for mount, scope_ref in (
                ("upstream.orders", _route_refs(account, strategy)[1]),
                ("upstream.order_query", _route_refs(account, strategy)[2]),
                ("upstream.account_reservations", _route_refs(account, strategy)[0]),
            )
        }
        assert len(bindings) == len(expected) == 12
        assert {
            (mount, account, strategy, scope_ref)
            for mount, account, strategy, scope_ref, _registration_id, _operations in bindings
        } == expected
        assert all(registration_id for *_, registration_id, _operations in bindings)
        return process, parent_control, endpoint, bindings
    except BaseException as startup_error:
        cleanup_error = _cleanup_failed_host_start(
            process,
            parent_control,
            child_control,
            process_started=process_started,
        )
        if cleanup_error is not None:
            add_note = getattr(startup_error, "add_note", None)
            if callable(add_note):
                add_note(
                    "coordinator startup cleanup also failed: "
                    f"{type(cleanup_error).__name__}"
                )
        raise


def _activate_owner_routes(account_controls: Mapping[str, Any], bindings: tuple[Any, ...]) -> None:
    entries = tuple(item for item in bindings if item[0] == "upstream.orders")
    assert len(entries) == 4
    for _mount, account_ref, strategy_ref, _scope_ref, registration_id, operations in entries:
        assert operations == tuple(sorted({"orders.submit_group_leg", "orders.resolve_group_leg"}))
        control = account_controls[account_ref]
        control.send(("activate", registration_id))
        activated = _receive(control)
        assert activated == ("activated", registration_id, activated[2], strategy_ref)
        assert type(activated[2]) is int and activated[2] > 0


def _close_client_threads(
    threads: Mapping[str, threading.Thread],
    commands: Mapping[str, queue.Queue],
    results: Mapping[str, queue.Queue],
) -> None:
    for strategy_ref in _STRATEGIES:
        thread = threads.get(strategy_ref)
        if thread is not None and thread.is_alive():
            commands[strategy_ref].put(("close",))
    for strategy_ref in _STRATEGIES:
        thread = threads.get(strategy_ref)
        if thread is None:
            continue
        thread.join(20)
        assert not thread.is_alive(), f"{strategy_ref} client owner did not close"
        if not results[strategy_ref].empty():
            _next_client_message(results[strategy_ref], "closed", timeout=2)


@pytest.mark.integration
def test_two_strategy_clients_compete_for_one_portfolio_hold_across_reopen(
    tmp_path: Path,
) -> None:
    """An overlapping second submission cannot spend an UNKNOWN-held budget twice."""

    metadata_reference_ns = time.time_ns()
    account_paths = {
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
        for account in _ACCOUNTS
    }
    coordinator_paths = {
        "registry": str(tmp_path / "coordinator-registry.sqlite3"),
        "groups": str(tmp_path / "coordinator-groups.sqlite3"),
        "run_bindings": str(tmp_path / "coordinator-run-bindings.sqlite3"),
    }
    account_endpoints = {account: _endpoint() for account in _ACCOUNTS}
    account_keys = {account: zmq.curve_keypair() for account in _ACCOUNTS}
    coordinator_keys = zmq.curve_keypair()
    coordinator_client_keys = zmq.curve_keypair()
    strategy_keys = {strategy: zmq.curve_keypair() for strategy in _STRATEGIES}
    account_configs = {
        account: _account_service_config(
            account, account_endpoints[account], f"arb02-account-server-{account}"
        )
        for account in _ACCOUNTS
    }
    context = multiprocessing.get_context("spawn")
    provider_entered = context.Event()
    provider_release = context.Event()
    account_processes: dict[str, Any] = {}
    account_controls: dict[str, Any] = {}
    coordinator_process = None
    coordinator_control = None
    client_threads: dict[str, threading.Thread] = {}
    client_commands: dict[str, queue.Queue] = {}
    client_results: dict[str, queue.Queue] = {}
    accepted_snapshot: GroupOrderSnapshot | None = None
    denied_snapshot: GroupOrderSnapshot | None = None
    request_a = _group_request(_GROUP_ACCEPTED, _STRATEGIES[0], metadata_reference_ns)
    request_b = _group_request(_GROUP_DENIED, _STRATEGIES[1], metadata_reference_ns)

    try:
        for account_ref in _ACCOUNTS:
            parent_control, child_control = context.Pipe(duplex=True)
            process = None
            process_started = False
            try:
                process = context.Process(
                    name=f"arb02-account-host-{account_ref}",
                    target=_account_host_process,
                    args=(
                        child_control,
                        account_configs[account_ref],
                        account_paths[account_ref]["registry"],
                        account_paths[account_ref],
                        account_ref,
                        account_keys[account_ref][0],
                        account_keys[account_ref][1],
                        coordinator_client_keys[0],
                        metadata_reference_ns,
                        ("open",) if account_ref == "account-a" else ("filled",),
                        provider_entered,
                        provider_release,
                    ),
                )
                process.start()
                process_started = True
                child_control.close()
                ready = _receive(parent_control)
                assert ready == ("ready", account_endpoints[account_ref])
            except BaseException as startup_error:
                cleanup_error = _cleanup_failed_host_start(
                    process,
                    parent_control,
                    child_control,
                    process_started=process_started,
                )
                if cleanup_error is not None:
                    add_note = getattr(startup_error, "add_note", None)
                    if callable(add_note):
                        add_note(
                            "account Host startup cleanup also failed: "
                            f"{type(cleanup_error).__name__}"
                        )
                raise
            assert process is not None
            account_processes[account_ref] = process
            account_controls[account_ref] = parent_control

        (
            coordinator_process,
            coordinator_control,
            coordinator_endpoint,
            upstream_bindings,
        ) = _start_coordinator(
            context,
            account_configs,
            coordinator_paths,
            account_endpoints,
            {account: account_keys[account][0] for account in _ACCOUNTS},
            coordinator_keys,
            coordinator_client_keys,
            strategy_keys,
            metadata_reference_ns,
        )
        _activate_owner_routes(account_controls, upstream_bindings)

        account_controls["account-a"].send(("arm-incomplete-fills",))
        assert _receive(account_controls["account-a"]) == ("armed", "account-a")

        client_threads, client_commands, client_results = _new_client_threads(
            coordinator_endpoint,
            coordinator_keys[0],
            strategy_keys,
        )
        client_commands[_STRATEGIES[0]].put(("submit", _GROUP_ACCEPTED, request_a))
        first_started = _next_client_message(client_results[_STRATEGIES[0]], "started")
        assert first_started[1:3] == ("submit", _GROUP_ACCEPTED)
        assert provider_entered.wait(20), "first group never reached the synthetic provider"

        # The independent second Runtime begins its normal Curve RPC while the
        # first provider write is durably present but deliberately unfinished.
        client_commands[_STRATEGIES[1]].put(("submit", _GROUP_DENIED, request_b))
        second_started = _next_client_message(client_results[_STRATEGIES[1]], "started")
        assert second_started[1:3] == ("submit", _GROUP_DENIED)
        assert first_started[3] < second_started[3]
        assert not provider_release.is_set()
        time.sleep(0.05)
        provider_release.set()

        first_done = _next_client_message(client_results[_STRATEGIES[0]], "done")
        second_done = _next_client_message(client_results[_STRATEGIES[1]], "done")
        assert first_done[1:3] == ("submit", _GROUP_ACCEPTED)
        assert second_done[1:3] == ("submit", _GROUP_DENIED)
        assert second_done[3] < first_done[4], (
            "the two public client submissions did not overlap while the first "
            "provider write was held"
        )
        accepted_snapshot = _snapshot_from_reply(first_done[5])
        denied_snapshot = _snapshot_from_reply(second_done[5])

        assert accepted_snapshot.request.portfolio_ref == denied_snapshot.request.portfolio_ref
        assert accepted_snapshot.request.strategy_id != denied_snapshot.request.strategy_id
        assert accepted_snapshot.budget_reservation is not None
        assert accepted_snapshot.budget_reservation.gross_notional == Decimal("200")
        assert accepted_snapshot.budget_reservation.state is GroupBudgetReservationState.HELD
        assert accepted_snapshot.state is GroupOrderState.RECOVERY_REQUIRED
        unknown_legs = tuple(leg for leg in accepted_snapshot.legs if leg.child_status is not None)
        assert len(unknown_legs) == 1
        assert unknown_legs[0].child_status.state is ExecutionState.UNKNOWN
        assert unknown_legs[0].phase.value == "UNKNOWN"
        assert unknown_legs[0].resolution_receipt is None

        assert denied_snapshot.state is GroupOrderState.REJECTED_BEFORE_DISPATCH
        assert denied_snapshot.error_code == "group_budget_portfolio_limit_exceeded"
        assert denied_snapshot.budget_reservation is not None
        assert denied_snapshot.budget_reservation.state is GroupBudgetReservationState.DENIED
        assert denied_snapshot.budget_reservation.gross_notional == Decimal("200")
        assert all(item.phase.value == "NOT_STARTED" for item in denied_snapshot.reservations)

        # Exact provider counts for one UNKNOWN first leg: one write and one
        # order/fill observation on account A, and no provider call on account
        # B. The budget-denied second group has no additional provider effect.
        provider_after_competition = {
            account: _provider_snapshot(Path(account_paths[account]["provider"]))
            for account in _ACCOUNTS
        }
        assert (
            provider_after_competition["account-a"][0]
            and len(provider_after_competition["account-a"][0]) == 1
        )
        assert provider_after_competition["account-a"][1] == {
            "order_queries": 1,
            "fill_queries": 1,
        }
        assert provider_after_competition["account-b"] == (
            (),
            {"order_queries": 0, "fill_queries": 0},
        )

        # Query is a pure projection before shutdown; neither account owner is
        # asked to dispatch or release anything for the denied group.
        for strategy_ref, group_id, expected in (
            (_STRATEGIES[0], _GROUP_ACCEPTED, accepted_snapshot),
            (_STRATEGIES[1], _GROUP_DENIED, denied_snapshot),
        ):
            client_commands[strategy_ref].put(("query", group_id, None))
            queried = _next_client_message(client_results[strategy_ref], "started")
            assert queried[1:3] == ("query", group_id)
            done = _next_client_message(client_results[strategy_ref], "done")
            assert _query_reply_snapshot(done[5]) == expected

        _close_client_threads(client_threads, client_commands, client_results)
        client_threads = {}
        client_commands = {}
        client_results = {}
        _stop_process(coordinator_process, coordinator_control)
        coordinator_process = None
        coordinator_control = None

        # Reopen the same durable group budget and run-binding stores under a
        # fresh normal coordinator Host epoch; account-owner Hosts stay live.
        (
            coordinator_process,
            coordinator_control,
            coordinator_endpoint,
            upstream_bindings,
        ) = _start_coordinator(
            context,
            account_configs,
            coordinator_paths,
            account_endpoints,
            {account: account_keys[account][0] for account in _ACCOUNTS},
            coordinator_keys,
            coordinator_client_keys,
            strategy_keys,
            metadata_reference_ns,
        )
        client_threads, client_commands, client_results = _new_client_threads(
            coordinator_endpoint,
            coordinator_keys[0],
            strategy_keys,
        )

        for strategy_ref, group_id, expected in (
            (_STRATEGIES[0], _GROUP_ACCEPTED, accepted_snapshot),
            (_STRATEGIES[1], _GROUP_DENIED, denied_snapshot),
        ):
            client_commands[strategy_ref].put(("query", group_id, None))
            _next_client_message(client_results[strategy_ref], "started")
            done = _next_client_message(client_results[strategy_ref], "done")
            assert _query_reply_snapshot(done[5]) == expected

        # Replaying the exact rejected request after reopen returns the same
        # durable DENIED receipt and cannot rescore, reserve, claim, or dispatch.
        client_commands[_STRATEGIES[1]].put(("submit", _GROUP_DENIED, request_b))
        replay_started = _next_client_message(client_results[_STRATEGIES[1]], "started")
        assert replay_started[1:3] == ("submit", _GROUP_DENIED)
        replay_done = _next_client_message(client_results[_STRATEGIES[1]], "done")
        replayed_denial = _snapshot_from_reply(replay_done[5])
        assert replayed_denial == denied_snapshot

        _close_client_threads(client_threads, client_commands, client_results)
        client_threads = {}
        client_commands = {}
        client_results = {}
        _stop_process(coordinator_process, coordinator_control)
        coordinator_process = None
        coordinator_control = None
    finally:
        _cleanup_group_hosts(
            provider_release=provider_release,
            client_threads=client_threads,
            client_commands=client_commands,
            client_results=client_results,
            coordinator_process=coordinator_process,
            coordinator_control=coordinator_control,
            account_processes=account_processes,
            account_controls=account_controls,
            close_client_threads=_close_client_threads,
            stop_process=_stop_process,
        )

    assert accepted_snapshot is not None and denied_snapshot is not None

    # Reopen durable projections only after every Host owner closed its DB.
    groups = SqliteOrderGroupStore(coordinator_paths["groups"])
    try:
        assert groups.get(_PORTFOLIO, _GROUP_ACCEPTED) == accepted_snapshot
        assert groups.get(_PORTFOLIO, _GROUP_DENIED) == denied_snapshot
    finally:
        groups.close()

    policy = _risk_policy()
    for account_ref in _ACCOUNTS:
        risk_scope = AccountScope("fixture", f"provider-{account_ref}", "simulation")
        gate = DurableRiskGate(account_paths[account_ref]["risk_gate"], policy)
        try:
            admitted = gate.read_group_vector_snapshot(risk_scope, _GROUP_ACCEPTED)
            assert admitted is not None and len(admitted.legs) == 1
            assert admitted.legs[0].expires_at_ns > time.time_ns()
            if account_ref == "account-a":
                assert admitted.legs[0].claimed is True
                assert admitted.legs[0].resolved is False
            else:
                assert admitted.legs[0].claimed is False
                assert admitted.legs[0].resolved is False
            with pytest.raises(RiskDeniedError) as denied_error:
                gate.read_group_vector_snapshot(risk_scope, _GROUP_DENIED)
            assert type(denied_error.value) is RiskDeniedError
            assert denied_error.value.code == "GROUP_NOT_FOUND"
        finally:
            gate.close()

        writes, counters = _provider_snapshot(Path(account_paths[account_ref]["provider"]))
        if account_ref == "account-a":
            assert len(writes) == 1
            assert counters == {"order_queries": 1, "fill_queries": 1}
        else:
            assert writes == ()
            assert counters == {"order_queries": 0, "fill_queries": 0}

        execution_scope = _execution_scope(account_ref, _STRATEGIES[0])
        execution = SqliteExecutionStore(account_paths[account_ref]["execution"])
        try:
            first_leg_id = group_leg_intent_id(_GROUP_ACCEPTED, "leg-a")
            second_leg_id = group_leg_intent_id(_GROUP_ACCEPTED, "leg-b")
            if account_ref == "account-a":
                first_record = execution.get(first_leg_id, scope=execution_scope)
                assert first_record is not None and first_record.state is ExecutionState.UNKNOWN
                assert first_record.dispatch_attempts == 1
                assert execution.get(second_leg_id, scope=execution_scope) is None
            else:
                assert execution.get(second_leg_id, scope=execution_scope) is None
        finally:
            execution.close()
