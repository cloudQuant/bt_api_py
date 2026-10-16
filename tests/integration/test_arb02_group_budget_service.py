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
import sqlite3
import sys
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
    GroupReservationPhase,
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
    _arm_incomplete_fills,
    _V2SyntheticBtApiFeed,
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
_GROUP_EXPIRED_UNCLAIMED = "arb02-expired-unclaimed-crash"
_GROUP_UNKNOWN_AFTER_TTL = "arb02-unknown-after-ttl"
_GROUP_CRASH_BEFORE_FIRST_LEG = "arb02-crash-before-first-leg"


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
                        failures.append(TimeoutError("Host child did not acknowledge normal close"))
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
    expected_interrupted_calls: Mapping[str, tuple[Any, ...]] | None = None,
    observed_interrupted_calls: list[tuple[str, tuple[Any, ...]]] | None = None,
    allowed_pending_completions: Mapping[str, tuple[tuple[str, str], ...]] | None = None,
    observed_pending_completions: list[tuple[str, tuple[Any, ...]]] | None = None,
) -> None:
    actions: list[Callable[[], object]] = [provider_release.set]
    if client_threads:

        def close_clients() -> None:
            observed = close_client_threads(
                client_threads,
                client_commands,
                client_results,
                allowed_call_errors=expected_interrupted_calls,
                allowed_pending_completions=allowed_pending_completions,
                observed_pending_completions=observed_pending_completions,
            )
            if observed_interrupted_calls is not None:
                observed_interrupted_calls.extend(observed)

        actions.append(close_clients)
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
        failure = _attempt_cleanup(
            (
                self.authority.close,
                self.accepted_store.close,
                self.risk_gate.close,
                self.proof_journal.close,
                self.execution_store.close,
                self.bt_api.close,
            )
        )
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
    reservation_observation_group_id: str | None = None,
    reservation_committed: Any | None = None,
    reservation_reply_entered: Any | None = None,
    reservation_reply_release: Any | None = None,
    reservation_events: Any | None = None,
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
            if reservation_events is not None:
                admission = resources.bundle.risk_admission
                original_reserve = admission.reserve_group_vector

                def observe_reserve(scope, group_id, members, *, deadline_ns):
                    # Test telemetry at the real account-owner adapter seam.
                    # The original durable Risk call remains authoritative.
                    reservation_events.put((account_ref, group_id))
                    return original_reserve(scope, group_id, members, deadline_ns=deadline_ns)

                admission.reserve_group_vector = observe_reserve
            if reservation_observation_group_id is not None and reservation_committed is not None:
                original_complete = resources.accepted_store.complete_reservation

                def complete_and_observe(binding, reservation):
                    completed = original_complete(binding, reservation)
                    if binding.group_id == reservation_observation_group_id:
                        reservation_committed.set()
                        if reservation_reply_entered is not None:
                            reservation_reply_entered.set()
                        if (
                            reservation_reply_release is not None
                            and not reservation_reply_release.wait(60)
                        ):
                            raise TimeoutError(
                                "test-owned reservation reply barrier was not released"
                            )
                    return completed

                # Delegate to the real accepted-store transaction, pausing only
                # after Risk and local receipt commits and before the RPC reply.
                resources.accepted_store.complete_reservation = complete_and_observe
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


def _next_client_call_outcome(messages: queue.Queue, timeout: float = 20) -> tuple[Any, ...]:
    """Read one call completion while allowing an expected lost reply."""

    deadline = time.monotonic() + timeout
    while True:
        remaining = deadline - time.monotonic()
        assert remaining > 0, "client owner did not report the interrupted call"
        message = messages.get(timeout=remaining)
        if message[0] in {"done", "call_error"}:
            return message
        if message[0] in {"thread_error", "close_error"}:
            raise AssertionError(f"strategy client failed before call outcome: {message!r}")
        raise AssertionError(f"unexpected strategy client message: {message!r}")


def _wait_for_deadline(deadline_ns: int) -> None:
    """Wait only for a newly created synthetic group's immutable deadline."""

    assert type(deadline_ns) is int and deadline_ns > 0
    while time.time_ns() <= deadline_ns:
        remaining = deadline_ns - time.time_ns()
        time.sleep(min(0.05, max(0.001, remaining / 1_000_000_000)))
    assert time.time_ns() > deadline_ns


def _group_request(
    group_id: str,
    strategy_ref: str,
    metadata_reference_ns: int,
    *,
    lifetime_ns: int = 120_000_000_000,
) -> dict[str, Any]:
    now_ns = time.time_ns()
    deadline_ns = now_ns + lifetime_ns
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


def _provider_snapshot_with_pending_faults(
    path: Path, expected_faults: tuple[str, ...]
) -> tuple[tuple[str, ...], dict[str, int]]:
    """Read the provider ledger at a phase where a specific fault is pending."""

    with sqlite3.connect(path) as connection:
        writes = tuple(
            str(row[0])
            for row in connection.execute(
                "SELECT client_order_id FROM provider_writes ORDER BY sequence"
            )
        )
        counters = {
            str(row[0]): int(row[1])
            for row in connection.execute(
                "SELECT name, count FROM provider_counters ORDER BY name"
            )
        }
        faults = tuple(
            str(row[0]) for row in connection.execute("SELECT name FROM provider_faults ORDER BY name")
        )
    assert faults == expected_faults, (faults, expected_faults)
    return writes, counters


def _unknown_submit_snapshot_from_outcome(
    outcome: tuple[Any, ...], group_id: str
) -> GroupOrderSnapshot:
    """Validate the exact pending UNKNOWN submit before returning its snapshot."""

    assert len(outcome) == 6 and outcome[0] == "done", outcome
    assert outcome[1:3] == ("submit", group_id), outcome
    assert type(outcome[3]) is int and type(outcome[4]) is int and outcome[4] >= outcome[3]
    snapshot = _snapshot_from_reply(outcome[5])
    assert snapshot.state is GroupOrderState.RECOVERY_REQUIRED
    assert snapshot.budget_reservation is not None
    assert snapshot.budget_reservation.state is GroupBudgetReservationState.HELD
    unknown_leg = next(leg for leg in snapshot.legs if leg.leg_id == "leg-a")
    assert unknown_leg.submitted_intent is not None
    assert unknown_leg.child_status is not None
    assert unknown_leg.child_status.state is ExecutionState.UNKNOWN
    return snapshot


def test_provider_inflight_snapshot_requires_the_exact_pending_fault(tmp_path: Path) -> None:
    """The in-flight exception is narrow; the ordinary snapshot stays strict."""

    path = tmp_path / "provider.sqlite3"
    with sqlite3.connect(path) as connection:
        connection.execute(
            "CREATE TABLE provider_writes (sequence INTEGER PRIMARY KEY, client_order_id TEXT)"
        )
        connection.execute(
            "CREATE TABLE provider_counters (name TEXT PRIMARY KEY, count INTEGER NOT NULL)"
        )
        connection.execute("CREATE TABLE provider_faults (name TEXT PRIMARY KEY)")
        connection.execute(
            "INSERT INTO provider_counters(name, count) VALUES ('order_queries', 0), ('fill_queries', 0)"
        )
        connection.execute("INSERT INTO provider_writes VALUES (1, 'child-1')")
        connection.execute("INSERT INTO provider_faults VALUES ('incomplete_next_fills')")

    expected = (("child-1",), {"fill_queries": 0, "order_queries": 0})
    assert _provider_snapshot_with_pending_faults(path, ("incomplete_next_fills",)) == expected
    with pytest.raises(AssertionError):
        _provider_snapshot(path)

    with sqlite3.connect(path) as connection:
        connection.execute("INSERT INTO provider_faults VALUES ('unexpected_fault')")
    with pytest.raises(AssertionError):
        _provider_snapshot_with_pending_faults(path, ("incomplete_next_fills",))

    with sqlite3.connect(path) as connection:
        connection.execute("DELETE FROM provider_faults")
    assert _provider_snapshot(path) == expected


def test_failed_cleanup_drains_only_declared_pending_completions() -> None:
    """A failed run may drain exactly the in-flight completion it declares."""

    drained_message = ("done", "submit", _GROUP_EXPIRED_UNCLAIMED, 10, 20, {"ok": True})

    def run_cleanup(
        allowed: Mapping[str, tuple[tuple[str, str], ...]] | None,
    ) -> tuple[BaseException | None, tuple[tuple[str, tuple[Any, ...]], ...]]:
        commands: queue.Queue = queue.Queue()
        results: queue.Queue = queue.Queue()

        def owner() -> None:
            # The queued completion sits ahead of the close acknowledgement,
            # exactly like a submit that resolves during failed cleanup.
            results.put(drained_message)
            commands.get(timeout=10)
            results.put(("closed", "closed", ()))

        thread = threading.Thread(target=owner, daemon=True)
        thread.start()
        observed: list[tuple[str, tuple[Any, ...]]] = []
        try:
            _close_client_threads(
                {_STRATEGIES[0]: thread},
                {_STRATEGIES[0]: commands},
                {_STRATEGIES[0]: results},
                allowed_pending_completions=allowed,
                observed_pending_completions=observed,
            )
        except BaseException as error:
            return error, tuple(observed)
        return None, tuple(observed)

    strict_error, strict_observed = run_cleanup(None)
    assert isinstance(strict_error, AssertionError)
    assert "unexpected" in str(strict_error)
    assert strict_observed == ()

    wrong_group_error, wrong_group_observed = run_cleanup(
        {_STRATEGIES[0]: (("submit", _GROUP_UNKNOWN_AFTER_TTL),)}
    )
    assert isinstance(wrong_group_error, AssertionError)
    assert wrong_group_observed == ()

    drained_error, drained_observed = run_cleanup(
        {_STRATEGIES[0]: (("submit", _GROUP_EXPIRED_UNCLAIMED),)}
    )
    assert drained_error is None
    assert drained_observed == ((_STRATEGIES[0], drained_message),)


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
                add_note(f"coordinator startup cleanup also failed: {type(cleanup_error).__name__}")
        raise


def _terminate_test_coordinator(process: Any, control: Any) -> int:
    """Crash only the coordinator child created by the calling integration test."""

    assert process.is_alive(), "test-owned coordinator must be live before crash injection"
    exitcode: int | None = None
    try:
        process.terminate()
        process.join(15)
        if process.is_alive():
            raise AssertionError("test-owned coordinator survived crash injection")
        exitcode = process.exitcode
    finally:
        control.close()
        if not process.is_alive():
            process.close()
    assert type(exitcode) is int and exitcode != 0
    return exitcode


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
    *,
    allowed_call_errors: Mapping[str, tuple[Any, ...]] | None = None,
    allowed_pending_completions: Mapping[str, tuple[tuple[str, str], ...]] | None = None,
    observed_pending_completions: list[tuple[str, tuple[Any, ...]]] | None = None,
) -> tuple[tuple[str, tuple[Any, ...]], ...]:
    failures: list[BaseException] = []
    closed_threads: set[str] = set()
    interrupted_calls: list[tuple[str, tuple[Any, ...]]] = []
    for strategy_ref in _STRATEGIES:
        thread = threads.get(strategy_ref)
        if thread is not None and thread.is_alive():
            try:
                commands[strategy_ref].put(("close",))
            except BaseException as error:
                failures.append(error)
    for strategy_ref in _STRATEGIES:
        thread = threads.get(strategy_ref)
        if thread is None:
            continue
        try:
            thread.join(20)
            if thread.is_alive():
                failures.append(AssertionError(f"{strategy_ref} client owner did not close"))
        except BaseException as error:
            failures.append(error)
    # Every thread has had its bounded join attempt before queue assertions run,
    # so one expected lost reply cannot prevent the other clients from closing.
    for strategy_ref in _STRATEGIES:
        if strategy_ref not in threads:
            continue
        messages = results[strategy_ref]
        while True:
            try:
                message = messages.get_nowait()
            except queue.Empty:
                break
            if message[0] == "closed":
                if strategy_ref in closed_threads:
                    failures.append(AssertionError(f"{strategy_ref} client closed more than once"))
                closed_threads.add(strategy_ref)
                continue
            if message[0] == "done":
                allowed = (
                    allowed_pending_completions.get(strategy_ref)
                    if allowed_pending_completions is not None
                    else None
                )
                if allowed is not None and message[1:3] in allowed:
                    # A failed run can leave a declared RPC in flight; its
                    # completion reaches the queue before the owner observes
                    # the close command. Record it instead of failing again.
                    if observed_pending_completions is not None:
                        observed_pending_completions.append((strategy_ref, message))
                    continue
            if message[0] == "call_error":
                expected = (
                    allowed_call_errors.get(strategy_ref)
                    if allowed_call_errors is not None
                    else None
                )
                if expected is not None and message[1:] == expected:
                    interrupted_calls.append((strategy_ref, message))
                    continue
            failures.append(
                AssertionError(f"unexpected {strategy_ref} client shutdown message: {message!r}")
            )
        if strategy_ref not in closed_threads:
            failures.append(
                AssertionError(f"{strategy_ref} client owner did not report normal close")
            )
    if failures:
        first_failure = failures[0]
        add_note = getattr(first_failure, "add_note", None)
        if callable(add_note):
            for additional in failures[1:]:
                add_note(
                    f"additional client cleanup failure: {type(additional).__name__}: {additional}"
                )
        raise first_failure
    return tuple(interrupted_calls)


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


@pytest.mark.integration
def test_expired_unclaimed_release_and_dispatched_unknown_hold_across_coordinator_crash(
    tmp_path: Path,
) -> None:
    """Recover only unclaimed expired vectors; keep dispatched UNKNOWN held."""

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
    reservation_committed = {account: context.Event() for account in _ACCOUNTS}
    reservation_reply_entered = context.Event()
    reservation_reply_release = context.Event()
    reservation_events = context.Queue()
    account_processes: dict[str, Any] = {}
    account_controls: dict[str, Any] = {}
    coordinator_process = None
    coordinator_control = None
    client_threads: dict[str, threading.Thread] = {}
    client_commands: dict[str, queue.Queue] = {}
    client_results: dict[str, queue.Queue] = {}
    expired_snapshot: GroupOrderSnapshot | None = None
    unknown_snapshot: GroupOrderSnapshot | None = None
    unknown_recovered: GroupOrderSnapshot | None = None
    cleanup_interrupted_calls: list[tuple[str, tuple[Any, ...]]] = []
    cleanup_pending_completions: list[tuple[str, tuple[Any, ...]]] = []

    try:
        # Account Hosts and the first coordinator are fully ready before either
        # short absolute request deadline is created.
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
                        _GROUP_EXPIRED_UNCLAIMED,
                        reservation_committed[account_ref],
                        reservation_reply_entered if account_ref == "account-b" else None,
                        reservation_reply_release if account_ref == "account-b" else None,
                        reservation_events,
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
        client_threads, client_commands, client_results = _new_client_threads(
            coordinator_endpoint,
            coordinator_keys[0],
            strategy_keys,
        )

        expired_request = _group_request(
            _GROUP_EXPIRED_UNCLAIMED,
            _STRATEGIES[0],
            metadata_reference_ns,
            lifetime_ns=6_000_000_000,
        )
        client_commands[_STRATEGIES[0]].put(("submit", _GROUP_EXPIRED_UNCLAIMED, expired_request))
        expired_started = _next_client_message(client_results[_STRATEGIES[0]], "started")
        assert expired_started[1:3] == ("submit", _GROUP_EXPIRED_UNCLAIMED)
        assert reservation_committed["account-a"].wait(5)
        assert reservation_committed["account-b"].wait(5)
        assert reservation_reply_entered.wait(5), (
            "account B did not reach the post-commit/pre-reply crash barrier"
        )
        assert not provider_entered.is_set(), "an unclaimed reservation reached a provider"
        assert _provider_snapshot(Path(account_paths["account-a"]["provider"])) == (
            (),
            {"order_queries": 0, "fill_queries": 0},
        )

        # B's real Risk vector and local accepted binding have committed. The
        # account handler withholds only its RPC response while the newly
        # created immutable deadline passes; then only this test's coordinator
        # process is crashed.
        _wait_for_deadline(expired_request["deadline_ns"])
        expired_coordinator_exit = _terminate_test_coordinator(
            coordinator_process, coordinator_control
        )
        coordinator_process = None
        coordinator_control = None
        assert expired_coordinator_exit != 0
        reservation_reply_release.set()
        for account_ref in _ACCOUNTS:
            account_controls[account_ref].send(("status",))
            status = _receive(account_controls[account_ref])
            assert status == (
                "status",
                (),
                {"order_queries": 0, "fill_queries": 0},
            )

        lost_reply = _next_client_call_outcome(client_results[_STRATEGIES[0]])
        assert lost_reply == (
            "call_error",
            "submit",
            _GROUP_EXPIRED_UNCLAIMED,
            "RequestOutcomeUnknownError",
            None,
        )
        _close_client_threads(client_threads, client_commands, client_results)
        client_threads = {}
        client_commands = {}
        client_results = {}

        # Reopen the same group/run-binding stores. Exact replay proves each
        # persisted vector is unclaimed before issuing one explicit release.
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
        client_threads, client_commands, client_results = _new_client_threads(
            coordinator_endpoint,
            coordinator_keys[0],
            strategy_keys,
        )
        client_commands[_STRATEGIES[0]].put(("submit", _GROUP_EXPIRED_UNCLAIMED, expired_request))
        _next_client_message(client_results[_STRATEGIES[0]], "started")
        expired_done = _next_client_message(client_results[_STRATEGIES[0]], "done")
        expired_snapshot = _snapshot_from_reply(expired_done[5])
        assert expired_snapshot.state is GroupOrderState.REJECTED_BEFORE_DISPATCH
        assert expired_snapshot.error_code == "reservation_not_active"
        assert expired_snapshot.budget_reservation is not None
        assert expired_snapshot.budget_reservation.state is GroupBudgetReservationState.RELEASED
        assert all(item.phase.value == "RELEASED" for item in expired_snapshot.reservations)
        assert all(leg.submitted_intent is None for leg in expired_snapshot.legs)
        client_commands[_STRATEGIES[0]].put(("query", _GROUP_EXPIRED_UNCLAIMED, None))
        _next_client_message(client_results[_STRATEGIES[0]], "started")
        queried = _next_client_message(client_results[_STRATEGIES[0]], "done")
        assert _query_reply_snapshot(queried[5]) == expired_snapshot

        # The same portfolio does not grant one strategy visibility into the
        # other strategy's durable run binding. The second client must remain
        # unable to query the first client's reservation after reopen.
        client_commands[_STRATEGIES[1]].put(("query", _GROUP_EXPIRED_UNCLAIMED, None))
        _next_client_message(client_results[_STRATEGIES[1]], "started")
        denied_query = _next_client_message(client_results[_STRATEGIES[1]], "done")
        assert denied_query[5] == {
            "ok": False,
            "code": "GROUP_BINDING_UNAVAILABLE",
            "result": {},
        }
        assert all(
            not _provider_snapshot(Path(account_paths[account]["provider"]))[0]
            for account in _ACCOUNTS
        )

        _close_client_threads(client_threads, client_commands, client_results)
        client_threads = {}
        client_commands = {}
        client_results = {}
        _stop_process(coordinator_process, coordinator_control)
        coordinator_process = None
        coordinator_control = None

        # The second branch dispatches one real synthetic provider write while
        # the account owner blocks before returning its observation. Crossing
        # its deadline cannot release its Risk claim or portfolio hold.
        account_controls["account-a"].send(("arm-incomplete-fills",))
        assert _receive(account_controls["account-a"]) == ("armed", "account-a")
        client_commands, client_results = {}, {}
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
        client_threads, client_commands, client_results = _new_client_threads(
            coordinator_endpoint,
            coordinator_keys[0],
            strategy_keys,
        )
        # Start the short absolute deadline only after every owner, coordinator,
        # route, and client runtime needed by this branch is ready.
        unknown_request = _group_request(
            _GROUP_UNKNOWN_AFTER_TTL,
            _STRATEGIES[0],
            metadata_reference_ns,
            lifetime_ns=6_000_000_000,
        )
        client_commands[_STRATEGIES[0]].put(("submit", _GROUP_UNKNOWN_AFTER_TTL, unknown_request))
        unknown_started = _next_client_message(client_results[_STRATEGIES[0]], "started")
        assert unknown_started[1:3] == ("submit", _GROUP_UNKNOWN_AFTER_TTL)
        assert provider_entered.wait(10), "the claimed group never reached its provider write"
        # The write is durably committed while the feed still waits at its
        # release barrier. The barrier outlives both the 5s account writer
        # lease and the 6s group deadline, so when the write resumes the
        # expired writer fence makes the proof guard mark the attempt UNKNOWN
        # (GROUP_OWNER_FENCE_UNAVAILABLE) before any fill observation. The
        # armed incomplete-fills fault therefore stays pending, which is the
        # positive evidence that no get_deals observation ever ran.
        writes_at_dispatch, counters_at_dispatch = _provider_snapshot_with_pending_faults(
            Path(account_paths["account-a"]["provider"]),
            ("incomplete_next_fills",),
        )
        assert len(writes_at_dispatch) == 1
        assert counters_at_dispatch["fill_queries"] == 0
        _wait_for_deadline(unknown_request["deadline_ns"])
        provider_release.set()
        unknown_done = _next_client_message(client_results[_STRATEGIES[0]], "done", timeout=15)
        unknown_snapshot = _unknown_submit_snapshot_from_outcome(
            unknown_done, _GROUP_UNKNOWN_AFTER_TTL
        )
        unknown_leg = next(leg for leg in unknown_snapshot.legs if leg.leg_id == "leg-a")

        _close_client_threads(client_threads, client_commands, client_results)
        client_threads = {}
        client_commands = {}
        client_results = {}
        _stop_process(coordinator_process, coordinator_control)
        coordinator_process = None
        coordinator_control = None

        # A normal close/reopen starts a fresh coordinator epoch over the same
        # SQLite stores. The dispatched-UNKNOWN hold is deliberately preserved,
        # so the account authority refuses every coordinator generation change
        # while that hold exists (GroupAuthorityBusyError: a dispatch hold
        # blocks coordinator change; only a trusted verifier proof may release
        # it first). Query is pure and the exact same-ID submit only reconciles
        # the original child ID through the read-only order route, so this
        # phase performs no owner-route activation; nothing may create a second
        # provider write or release the hold.
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
        client_commands[_STRATEGIES[0]].put(("query", _GROUP_UNKNOWN_AFTER_TTL, None))
        _next_client_message(client_results[_STRATEGIES[0]], "started")
        unknown_query = _next_client_message(client_results[_STRATEGIES[0]], "done")
        assert _query_reply_snapshot(unknown_query[5]) == unknown_snapshot
        client_commands[_STRATEGIES[0]].put(("submit", _GROUP_UNKNOWN_AFTER_TTL, unknown_request))
        _next_client_message(client_results[_STRATEGIES[0]], "started")
        unknown_replay_done = _next_client_message(
            client_results[_STRATEGIES[0]], "done", timeout=15
        )
        unknown_recovered = _snapshot_from_reply(unknown_replay_done[5])
        assert unknown_recovered.state is GroupOrderState.RECOVERY_REQUIRED
        assert unknown_recovered.budget_reservation is not None
        assert unknown_recovered.budget_reservation.state is GroupBudgetReservationState.HELD
        recovered_leg = next(leg for leg in unknown_recovered.legs if leg.leg_id == "leg-a")
        assert recovered_leg.submitted_intent == unknown_leg.submitted_intent
        assert recovered_leg.child_status is not None
        assert recovered_leg.child_status.state is ExecutionState.UNKNOWN
        assert all(
            leg.submitted_intent is None for leg in unknown_recovered.legs if leg.leg_id != "leg-a"
        )
        # The recovery replay is read-only: no second provider write and no
        # order/fill query may happen while the UNKNOWN hold is unresolved,
        # and the never-consumed fault proves no get_deals observation ran.
        final_writes, final_counters = _provider_snapshot_with_pending_faults(
            Path(account_paths["account-a"]["provider"]),
            ("incomplete_next_fills",),
        )
        assert len(final_writes) == 1
        assert final_counters == {"order_queries": 0, "fill_queries": 0}

        _close_client_threads(client_threads, client_commands, client_results)
        client_threads = {}
        client_commands = {}
        client_results = {}
        _stop_process(coordinator_process, coordinator_control)
        coordinator_process = None
        coordinator_control = None
    finally:
        reservation_reply_release.set()
        provider_release.set()
        primary_error = sys.exc_info()[1]
        cleanup_error: BaseException | None = None
        try:
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
                expected_interrupted_calls={
                    _STRATEGIES[0]: (
                        "submit",
                        _GROUP_EXPIRED_UNCLAIMED,
                        "RequestOutcomeUnknownError",
                        None,
                    )
                },
                observed_interrupted_calls=cleanup_interrupted_calls,
                allowed_pending_completions={
                    _STRATEGIES[0]: (
                        ("submit", _GROUP_EXPIRED_UNCLAIMED),
                        ("query", _GROUP_EXPIRED_UNCLAIMED),
                        ("submit", _GROUP_UNKNOWN_AFTER_TTL),
                        ("query", _GROUP_UNKNOWN_AFTER_TTL),
                    ),
                    _STRATEGIES[1]: (("query", _GROUP_EXPIRED_UNCLAIMED),),
                },
                observed_pending_completions=cleanup_pending_completions,
            )
        except BaseException as error:
            cleanup_error = error

        if cleanup_interrupted_calls:
            observed = tuple(item[1] for item in cleanup_interrupted_calls)
            if primary_error is None and cleanup_error is None:
                cleanup_error = AssertionError(
                    "cleanup consumed an interrupted submit that the test did not assert: "
                    f"{observed!r}"
                )
            elif primary_error is not None:
                add_note = getattr(primary_error, "add_note", None)
                if callable(add_note):
                    add_note(f"cleanup observed the exact expected lost reply: {observed!r}")

        if cleanup_pending_completions:
            drained = tuple(
                (strategy_ref, message[1:3])
                for strategy_ref, message in cleanup_pending_completions
            )
            drain_error: BaseException | None = None
            for _strategy_ref, message in cleanup_pending_completions:
                if message[1:3] == ("submit", _GROUP_UNKNOWN_AFTER_TTL):
                    try:
                        _unknown_submit_snapshot_from_outcome(message, _GROUP_UNKNOWN_AFTER_TTL)
                    except BaseException as error:
                        drain_error = error
                        break
            if primary_error is None and cleanup_error is None:
                if drain_error is not None:
                    cleanup_error = drain_error
                else:
                    cleanup_error = AssertionError(
                        "cleanup drained pending client completions that the test did not "
                        f"assert: {drained!r}"
                    )
            elif primary_error is not None:
                add_note = getattr(primary_error, "add_note", None)
                if callable(add_note):
                    drain_note = f"cleanup drained pending client completions: {drained!r}"
                    if drain_error is not None:
                        drain_note = f"{drain_note}; validation failed: {drain_error!r}"
                    add_note(drain_note)
            elif drain_error is not None:
                add_note = getattr(cleanup_error, "add_note", None)
                if callable(add_note):
                    add_note(f"drained completion validation failed: {drain_error!r}")

        if cleanup_error is not None:
            if primary_error is None:
                raise cleanup_error
            add_note = getattr(primary_error, "add_note", None)
            if callable(add_note):
                add_note(
                    "test-owned cleanup also failed after all actions were attempted: "
                    f"{type(cleanup_error).__name__}: {cleanup_error}"
                )

    assert (
        expired_snapshot is not None
        and unknown_snapshot is not None
        and unknown_recovered is not None
    )
    groups = SqliteOrderGroupStore(coordinator_paths["groups"])
    try:
        assert groups.get(_PORTFOLIO, _GROUP_EXPIRED_UNCLAIMED) == expired_snapshot
        assert groups.get(_PORTFOLIO, _GROUP_UNKNOWN_AFTER_TTL) == unknown_recovered
    finally:
        groups.close()

    expected_reservation_calls = sorted(
        [
            (account, group_id)
            for group_id in (_GROUP_EXPIRED_UNCLAIMED, _GROUP_UNKNOWN_AFTER_TTL)
            for account in _ACCOUNTS
        ]
    )
    observed_reservation_calls = [reservation_events.get(timeout=5) for _ in range(4)]
    assert sorted(observed_reservation_calls) == expected_reservation_calls
    with pytest.raises(queue.Empty):
        reservation_events.get(timeout=0.1)

    for account_ref in _ACCOUNTS:
        risk_scope = AccountScope("fixture", f"provider-{account_ref}", "simulation")
        gate = DurableRiskGate(account_paths[account_ref]["risk_gate"], _risk_policy())
        try:
            released = gate.read_group_vector_snapshot(risk_scope, _GROUP_EXPIRED_UNCLAIMED)
            assert all(
                leg.state.value == "expired" and not leg.claimed and not leg.resolved
                for leg in released.legs
            )
            held = gate.read_group_vector_snapshot(risk_scope, _GROUP_UNKNOWN_AFTER_TTL)
            assert len(held.legs) == 1
            held_leg = held.legs[0]
            assert held_leg.expires_at_ns <= time.time_ns()
            assert held_leg.resolved is False
            # An unresolved claimed leg keeps its raw status and the freeze
            # latch: no TTL may settle it while the outcome is unproven.
            assert held_leg.state.value == "active"
            assert held_leg.claimed is (account_ref == "account-a")
        finally:
            gate.close()

        if account_ref == "account-a":
            writes, counters = _provider_snapshot_with_pending_faults(
                Path(account_paths[account_ref]["provider"]),
                ("incomplete_next_fills",),
            )
            assert len(writes) == 1
            assert counters == {"order_queries": 0, "fill_queries": 0}
        else:
            writes, counters = _provider_snapshot(Path(account_paths[account_ref]["provider"]))
            assert writes == ()
            assert counters == {"order_queries": 0, "fill_queries": 0}

        execution = SqliteExecutionStore(account_paths[account_ref]["execution"])
        try:
            expired_first = execution.get(
                group_leg_intent_id(_GROUP_EXPIRED_UNCLAIMED, "leg-a"),
                scope=_execution_scope(account_ref, _STRATEGIES[0]),
            )
            expired_second = execution.get(
                group_leg_intent_id(_GROUP_EXPIRED_UNCLAIMED, "leg-b"),
                scope=_execution_scope(account_ref, _STRATEGIES[0]),
            )
            assert expired_first is None and expired_second is None
            if account_ref == "account-a":
                dispatched = execution.get(
                    group_leg_intent_id(_GROUP_UNKNOWN_AFTER_TTL, "leg-a"),
                    scope=_execution_scope(account_ref, _STRATEGIES[0]),
                )
                # The fence was lost before the observation phase, so the
                # durable record remains DISPATCHING: no lease can move it,
                # and the UNKNOWN outcome lives in the proof journal.
                assert dispatched is not None
                assert dispatched.state is ExecutionState.DISPATCHING
                assert dispatched.unknown_reason is None
                assert dispatched.dispatch_attempts == 1
            else:
                assert (
                    execution.get(
                        group_leg_intent_id(_GROUP_UNKNOWN_AFTER_TTL, "leg-b"),
                        scope=_execution_scope(account_ref, _STRATEGIES[0]),
                    )
                    is None
                )
        finally:
            execution.close()


@pytest.mark.integration
def test_all_reservations_submitted_crash_before_first_leg_no_oversell(
    tmp_path: Path,
) -> None:
    """Crash after both account Risk vectors commit, before any dispatch, no TTL.

    Both account reservation vectors are fully committed while the group
    deadline remains strictly in the future, then only the coordinator process
    is crashed before the first leg is dispatched. Reopening the same durable
    stores must show one unspent budget hold, both unclaimed vectors, and zero
    provider effect; the exact replay then continues the group exactly once -
    one dispatch attempt per leg, one HELD budget transition, no duplicate
    hold and no lost or duplicated capacity.
    """

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
    reservation_committed = {account: context.Event() for account in _ACCOUNTS}
    reservation_reply_entered = context.Event()
    reservation_reply_release = context.Event()
    reservation_events = context.Queue()
    account_processes: dict[str, Any] = {}
    account_controls: dict[str, Any] = {}
    coordinator_process = None
    coordinator_control = None
    client_threads: dict[str, threading.Thread] = {}
    client_commands: dict[str, queue.Queue] = {}
    client_results: dict[str, queue.Queue] = {}
    pre_dispatch_snapshot: GroupOrderSnapshot | None = None
    recovered_snapshot: GroupOrderSnapshot | None = None
    recovered_query_snapshot: GroupOrderSnapshot | None = None
    cleanup_interrupted_calls: list[tuple[str, tuple[Any, ...]]] = []
    cleanup_pending_completions: list[tuple[str, tuple[Any, ...]]] = []

    try:
        # Both initial states are "filled", so when recovery continues the
        # group each leg dispatches exactly once and both legs fill. Account
        # A's feed also blocks on the shared write barrier, which lets the
        # test observe that the recovered replay really reached a provider.
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
                        ("filled",),
                        provider_entered,
                        provider_release,
                        _GROUP_CRASH_BEFORE_FIRST_LEG,
                        reservation_committed[account_ref],
                        reservation_reply_entered if account_ref == "account-b" else None,
                        reservation_reply_release if account_ref == "account-b" else None,
                        reservation_events,
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
        client_threads, client_commands, client_results = _new_client_threads(
            coordinator_endpoint,
            coordinator_keys[0],
            strategy_keys,
        )

        # The 120s lifetime is created only after every Host, route and client
        # runtime is ready; no short deadline is ever crossed in this test.
        request = _group_request(
            _GROUP_CRASH_BEFORE_FIRST_LEG,
            _STRATEGIES[0],
            metadata_reference_ns,
            lifetime_ns=120_000_000_000,
        )
        client_commands[_STRATEGIES[0]].put(("submit", _GROUP_CRASH_BEFORE_FIRST_LEG, request))
        started = _next_client_message(client_results[_STRATEGIES[0]], "started")
        assert started[1:3] == ("submit", _GROUP_CRASH_BEFORE_FIRST_LEG)
        assert reservation_committed["account-a"].wait(5)
        assert reservation_committed["account-b"].wait(5)
        assert reservation_reply_entered.wait(5), (
            "account B did not reach the post-commit/pre-reply crash barrier"
        )
        # Both durable Risk vectors are now committed on their account owners
        # and the budget is booked once; the coordinator is still blocked in
        # B's reservation reply, strictly before any first-leg dispatch.
        assert not provider_entered.is_set(), "an undispatched group reached a provider"
        assert request["deadline_ns"] > time.time_ns(), (
            "the immutable group deadline elapsed before crash injection"
        )

        # Crash only the coordinator process; no TTL participates. The never
        # answered reservation RPC reply from B is released only afterwards.
        crashed_coordinator_exit = _terminate_test_coordinator(
            coordinator_process, coordinator_control
        )
        coordinator_process = None
        coordinator_control = None
        assert crashed_coordinator_exit != 0
        reservation_reply_release.set()
        for account_ref in _ACCOUNTS:
            account_controls[account_ref].send(("status",))
            status = _receive(account_controls[account_ref])
            assert status == (
                "status",
                (),
                {"order_queries": 0, "fill_queries": 0},
            )

        lost_reply = _next_client_call_outcome(client_results[_STRATEGIES[0]])
        assert lost_reply == (
            "call_error",
            "submit",
            _GROUP_CRASH_BEFORE_FIRST_LEG,
            "RequestOutcomeUnknownError",
            None,
        )
        _close_client_threads(client_threads, client_commands, client_results)
        client_threads = {}
        client_commands = {}
        client_results = {}

        # Reopen the same group/run-binding stores. The pure query must show
        # the exact pre-dispatch picture: one RESERVED portfolio hold, both
        # vectors committed and unclaimed, zero dispatch and no rejection.
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
        client_threads, client_commands, client_results = _new_client_threads(
            coordinator_endpoint,
            coordinator_keys[0],
            strategy_keys,
        )
        client_commands[_STRATEGIES[0]].put(("query", _GROUP_CRASH_BEFORE_FIRST_LEG, None))
        _next_client_message(client_results[_STRATEGIES[0]], "started")
        pre_dispatch_query = _next_client_message(client_results[_STRATEGIES[0]], "done")
        pre_dispatch_snapshot = _query_reply_snapshot(pre_dispatch_query[5])
        assert pre_dispatch_snapshot.state is GroupOrderState.PREPARING
        assert pre_dispatch_snapshot.error_code is None
        assert pre_dispatch_snapshot.budget_reservation is not None
        assert (
            pre_dispatch_snapshot.budget_reservation.state
            is GroupBudgetReservationState.RESERVED
        )
        assert pre_dispatch_snapshot.budget_reservation.gross_notional == Decimal("200")
        assert len(pre_dispatch_snapshot.reservations) == 2
        assert {
            item.account_ref: item.phase for item in pre_dispatch_snapshot.reservations
        } == {
            "account-a": GroupReservationPhase.ACTIVE,
            "account-b": GroupReservationPhase.REQUESTING,
        }
        assert all(
            not leg.claimed and not leg.resolved
            for item in pre_dispatch_snapshot.reservations
            for leg in item.legs
        )
        assert all(
            leg.submitted_intent is None and leg.child_status is None
            for leg in pre_dispatch_snapshot.legs
        )
        for account_ref in _ACCOUNTS:
            account_controls[account_ref].send(("status",))
            status = _receive(account_controls[account_ref])
            assert status == (
                "status",
                (),
                {"order_queries": 0, "fill_queries": 0},
            )

        # The exact same immutable request is replayed while its deadline is
        # still in the future: recovery continues the prepared group rather
        # than rejecting it. Releasing the write barrier only here keeps every
        # pre-crash phase provably free of provider dispatch.
        assert request["deadline_ns"] > time.time_ns(), (
            "recovery replay must happen strictly before the group deadline"
        )
        provider_release.set()
        client_commands[_STRATEGIES[0]].put(("submit", _GROUP_CRASH_BEFORE_FIRST_LEG, request))
        replay_started = _next_client_message(client_results[_STRATEGIES[0]], "started")
        assert replay_started[1:3] == ("submit", _GROUP_CRASH_BEFORE_FIRST_LEG)
        replay_done = _next_client_message(client_results[_STRATEGIES[0]], "done", timeout=30)
        recovered_snapshot = _snapshot_from_reply(replay_done[5])
        assert recovered_snapshot.state is GroupOrderState.PAIRED
        assert recovered_snapshot.error_code is None
        assert recovered_snapshot.residual_base_units == 0
        assert provider_entered.is_set(), "the recovered replay never dispatched the first leg"
        assert recovered_snapshot.budget_reservation is not None
        # The one admitted budget hold survives the crash: same quote facts,
        # now HELD because the recovered dispatch started exactly once.
        assert recovered_snapshot.budget_reservation.state is GroupBudgetReservationState.HELD
        assert recovered_snapshot.budget_reservation.gross_notional == Decimal("200")
        assert (
            recovered_snapshot.budget_reservation.quote_fingerprint
            == pre_dispatch_snapshot.budget_reservation.quote_fingerprint
        )
        assert (
            recovered_snapshot.budget_reservation.policy_fingerprint
            == pre_dispatch_snapshot.budget_reservation.policy_fingerprint
        )
        assert len(recovered_snapshot.reservations) == 2
        assert all(
            leg.submitted_intent is not None
            and leg.claim_receipt is not None
            and leg.child_status is not None
            and leg.child_status.state is ExecutionState.FILLED
            and leg.resolution_receipt is not None
            and leg.resolution_receipt.resolution_kind == "TERMINAL_EXPOSURE_RESOLVED"
            and leg.resolution_receipt.trade_count == 1
            and leg.resolution_receipt.dispatch_latch_active is False
            for leg in recovered_snapshot.legs
        )

        # Query is a pure projection and must equal the replay receipt exactly.
        client_commands[_STRATEGIES[0]].put(("query", _GROUP_CRASH_BEFORE_FIRST_LEG, None))
        _next_client_message(client_results[_STRATEGIES[0]], "started")
        recovered_query = _next_client_message(client_results[_STRATEGIES[0]], "done")
        recovered_query_snapshot = _query_reply_snapshot(recovered_query[5])
        assert recovered_query_snapshot == recovered_snapshot

        _close_client_threads(client_threads, client_commands, client_results)
        client_threads = {}
        client_commands = {}
        client_results = {}
        _stop_process(coordinator_process, coordinator_control)
        coordinator_process = None
        coordinator_control = None
    finally:
        reservation_reply_release.set()
        provider_release.set()
        primary_error = sys.exc_info()[1]
        cleanup_error: BaseException | None = None
        try:
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
                expected_interrupted_calls={
                    _STRATEGIES[0]: (
                        "submit",
                        _GROUP_CRASH_BEFORE_FIRST_LEG,
                        "RequestOutcomeUnknownError",
                        None,
                    )
                },
                observed_interrupted_calls=cleanup_interrupted_calls,
                allowed_pending_completions={
                    _STRATEGIES[0]: (
                        ("submit", _GROUP_CRASH_BEFORE_FIRST_LEG),
                        ("query", _GROUP_CRASH_BEFORE_FIRST_LEG),
                    )
                },
                observed_pending_completions=cleanup_pending_completions,
            )
        except BaseException as error:
            cleanup_error = error

        if cleanup_interrupted_calls:
            observed = tuple(item[1] for item in cleanup_interrupted_calls)
            if primary_error is None and cleanup_error is None:
                cleanup_error = AssertionError(
                    "cleanup consumed an interrupted submit that the test did not assert: "
                    f"{observed!r}"
                )
            elif primary_error is not None:
                add_note = getattr(primary_error, "add_note", None)
                if callable(add_note):
                    add_note(f"cleanup observed the exact expected lost reply: {observed!r}")

        if cleanup_pending_completions:
            drained = tuple(
                (strategy_ref, message[1:3])
                for strategy_ref, message in cleanup_pending_completions
            )
            if primary_error is None and cleanup_error is None:
                cleanup_error = AssertionError(
                    "cleanup drained pending client completions that the test did not "
                    f"assert: {drained!r}"
                )
            elif primary_error is not None:
                add_note = getattr(primary_error, "add_note", None)
                if callable(add_note):
                    add_note(f"cleanup drained pending client completions: {drained!r}")

        if cleanup_error is not None:
            if primary_error is None:
                raise cleanup_error
            add_note = getattr(primary_error, "add_note", None)
            if callable(add_note):
                add_note(
                    "test-owned cleanup also failed after all actions were attempted: "
                    f"{type(cleanup_error).__name__}: {cleanup_error}"
                )

    assert pre_dispatch_snapshot is not None and recovered_snapshot is not None
    assert recovered_query_snapshot == recovered_snapshot

    groups = SqliteOrderGroupStore(coordinator_paths["groups"])
    try:
        assert groups.get(_PORTFOLIO, _GROUP_CRASH_BEFORE_FIRST_LEG) == recovered_snapshot
        events = groups.events(_PORTFOLIO, _GROUP_CRASH_BEFORE_FIRST_LEG)
    finally:
        groups.close()

    # One durable budget hold: exactly one RESERVED -> HELD transition, one
    # first-leg and one hedge dispatch start, and no rejection or recovery
    # event anywhere in the group's event log.
    budget_transitions = [
        {
            "from": event.payload["budget_transition"].get("from"),
            "to": event.payload["budget_transition"].get("to"),
        }
        for event in events
        if "budget_transition" in event.payload
    ]
    assert budget_transitions == [{"from": "RESERVED", "to": "HELD"}], budget_transitions
    event_types = tuple(event.event_type for event in events)
    assert event_types.count("FIRST_LEG_SUBMIT_STARTED") == 1
    assert event_types.count("HEDGE_LEG_SUBMIT_STARTED") == 1
    assert event_types.count("GROUP_PAIRED") == 1
    assert "GROUP_REJECTED_BEFORE_DISPATCH" not in event_types
    assert "GROUP_RECOVERY_REQUIRED" not in event_types
    assert "GROUP_DEADLINE_ELAPSED" not in event_types

    for account_ref in _ACCOUNTS:
        risk_scope = AccountScope("fixture", f"provider-{account_ref}", "simulation")
        gate = DurableRiskGate(account_paths[account_ref]["risk_gate"], _risk_policy())
        try:
            resolved = gate.read_group_vector_snapshot(risk_scope, _GROUP_CRASH_BEFORE_FIRST_LEG)
            assert len(resolved.legs) == 1
            # The crash never released or duplicated the single committed
            # vector: its only member was claimed and resolved exactly once.
            assert resolved.legs[0].claimed is True
            assert resolved.legs[0].resolved is True
        finally:
            gate.close()

        writes, counters = _provider_snapshot(Path(account_paths[account_ref]["provider"]))
        assert len(writes) == 1
        assert len(set(writes)) == 1
        assert counters == {"order_queries": 1, "fill_queries": 1}

        execution_scope = _execution_scope(account_ref, _STRATEGIES[0])
        execution = SqliteExecutionStore(account_paths[account_ref]["execution"])
        try:
            leg_id = group_leg_intent_id(
                _GROUP_CRASH_BEFORE_FIRST_LEG,
                "leg-a" if account_ref == "account-a" else "leg-b",
            )
            record = execution.get(leg_id, scope=execution_scope)
            assert record is not None
            assert record.state is ExecutionState.FILLED
            # Exactly one dispatch attempt per leg: the recovered replay never
            # produced a second provider write for any leg.
            assert record.dispatch_attempts == 1
        finally:
            execution.close()

        authority_store = SqliteExecutionStore(account_paths[account_ref]["execution"])
        authority = ExecutionGroupAuthority(
            account_paths[account_ref]["group_authority"],
            completion_verifier=ExecutionStoreGroupCompletionVerifier(
                authority_store,
                execution_scopes=(execution_scope,),
            ),
        )
        try:
            # No dispatch hold was lost or stranded by the pre-dispatch crash.
            assert authority.pending_holds() == ()
        finally:
            authority.close()
            authority_store.close()

    # Exactly one Risk reserve RPC per account for the whole group: the
    # recovery replay reconciled through pure queries and never re-reserved.
    expected_reservation_calls = sorted(
        [(account, _GROUP_CRASH_BEFORE_FIRST_LEG) for account in _ACCOUNTS]
    )
    observed_reservation_calls = [reservation_events.get(timeout=5) for _ in range(2)]
    assert sorted(observed_reservation_calls) == expected_reservation_calls
    with pytest.raises(queue.Empty):
        reservation_events.get(timeout=0.1)
