"""ARB02 real account-Risk rejection across the service reservation boundary.

The coordinator first commits account A's real Risk vector, then account B's
DurableRiskGate rejects its USD 100 leg under a configured USD 50 strategy
allocation.  The normal CURVE reservation RPCs, typed preflight reads, and
unclaimed release remain active.  Only observation wrappers are installed at
the account-owned adapter seam; no ReserveRPC, Risk decision, or provider
operation is faked.
"""

from __future__ import annotations

import multiprocessing
import queue
import threading
import time
from decimal import Decimal
from pathlib import Path
from typing import Any, Mapping

import pytest
import zmq
from bt_api_execution import (
    GroupAccountReservationSnapshot,
    GroupBudgetReservationState,
    GroupOrderSnapshot,
    GroupOrderState,
    GroupReservationPhase,
    SqliteExecutionStore,
    SqliteOrderGroupStore,
    group_leg_intent_id,
)
from bt_api_risk import (
    AccountScope,
    DurableRiskGate,
    RiskDeniedError,
    RiskGroupReservationReceipt,
    RiskIntent,
)

from tests.integration import test_arb02_group_budget_service as accepted

_GROUP_RISK_REJECTION = "arb02-account-risk-rejection"
_GROUP_BUDGET_REUSE = "arb02-account-risk-budget-reuse"
_LIMITED_ALLOCATION_VERSION = "arb02-account-b-limit-v2"
_LIMITED_STRATEGY_NOTIONAL = Decimal("50")
_GROUP_NOTIONAL = Decimal("200")


def _risk_receipt_facts(receipt: RiskGroupReservationReceipt) -> dict[str, Any]:
    """Serialize only typed, non-sensitive facts from the real Risk receipt."""

    assert type(receipt) is RiskGroupReservationReceipt
    return {
        "type": type(receipt).__name__,
        "group_id": receipt.group_id,
        "scope": (
            receipt.scope.provider,
            receipt.scope.account_id,
            receipt.scope.environment,
        ),
        "deadline_ns": receipt.deadline_ns,
        "legs": tuple(
            (
                leg.leg_id,
                leg.intent_id,
                leg.permit_id,
                leg.reserved_intent_hash,
                leg.state.name,
                leg.claimed,
                leg.resolved,
            )
            for leg in receipt.legs
        ),
    }


def _service_receipt_facts(receipt: GroupAccountReservationSnapshot) -> dict[str, Any]:
    """Serialize the exact typed account-service projection after its commit."""

    assert type(receipt) is GroupAccountReservationSnapshot
    return {
        "type": type(receipt).__name__,
        "group_id": receipt.group_id,
        "account_ref": receipt.account_ref,
        "phase": receipt.phase.value,
        "legs": tuple(
            (
                leg.leg_id,
                leg.intent_id,
                leg.permit_id,
                leg.reserved_intent_hash,
                leg.state,
                leg.claimed,
                leg.resolved,
            )
            for leg in receipt.legs
        ),
    }


def _install_risk_observers(
    resources: Any, account_ref: str, events: Any
) -> None:
    """Record real adapter results without replacing any business operation."""

    admission = resources.bundle.risk_admission
    sequence = 0

    def record(kind: str, group_id: str, facts: object = None) -> None:
        nonlocal sequence
        sequence += 1
        events.put((account_ref, sequence, kind, group_id, facts))

    original_read = admission.read_group_vector_snapshot

    def observe_read(scope: Any, group_id: str) -> RiskGroupReservationReceipt:
        try:
            receipt = original_read(scope, group_id)
        except Exception as error:
            record(
                "risk_snapshot_denied",
                group_id,
                (type(error).__name__, getattr(error, "code", None)),
            )
            raise
        record("risk_snapshot_returned", group_id, _risk_receipt_facts(receipt))
        return receipt

    admission.read_group_vector_snapshot = observe_read

    original_map = admission.map_group_intent

    def observe_map(intent: Any, *, scope: AccountScope) -> RiskIntent:
        try:
            mapped = original_map(intent, scope=scope)
        except Exception as error:
            # The real mapper may reject before it can return a RiskIntent.
            # Capture the immutable request and a read-only view of the exact
            # configured allowance at that account-owned boundary, then let
            # the original coded rejection continue through the service.
            allocation = resources.risk_gate.get_strategy_allocation(
                scope, intent.scope.strategy_id
            )
            request_price = intent.price
            request_quantity = intent.quantity
            record(
                "risk_intent_mapping_denied",
                intent.intent_id,
                {
                    "request": {
                        "intent_id": intent.intent_id,
                        "wire_scope": (
                            intent.scope.provider,
                            intent.scope.account_ref,
                            intent.scope.environment,
                            intent.scope.strategy_id,
                        ),
                        "instrument": intent.instrument,
                        "side": intent.side.value,
                        "position_effect": intent.position_effect.value,
                        "order_type": intent.order_type.value,
                        "quantity": format(request_quantity, "f"),
                        "price": None
                        if request_price is None
                        else format(request_price, "f"),
                        "request_notional": None
                        if request_price is None
                        else format(request_quantity * request_price, "f"),
                    },
                    "risk_scope": (
                        scope.provider,
                        scope.account_id,
                        scope.environment,
                    ),
                    "allocation": {
                        "strategy_id": allocation.strategy_id,
                        "revision": allocation.revision,
                        "allocated_notional": None
                        if allocation.allocated_notional is None
                        else format(allocation.allocated_notional, "f"),
                        "used_notional": None
                        if allocation.used_notional is None
                        else format(allocation.used_notional, "f"),
                        "reserved_notional": None
                        if allocation.reserved_notional is None
                        else format(allocation.reserved_notional, "f"),
                        "available_notional": None
                        if allocation.available_notional is None
                        else format(allocation.available_notional, "f"),
                        "notional_unit": allocation.notional_unit,
                    },
                    "error_type": type(error).__name__,
                    "error_code": getattr(error, "code", None),
                },
            )
            raise
        assert type(mapped) is RiskIntent
        record(
            "risk_intent_mapped",
            mapped.intent_id,
            {
                "scope": (
                    mapped.scope.provider,
                    mapped.scope.account_id,
                    mapped.scope.environment,
                ),
                "action": mapped.action.value,
                "notional": format(mapped.notional, "f"),
                "notional_unit": mapped.notional_unit,
                "quantity": None
                if mapped.quantity is None
                else format(mapped.quantity, "f"),
                "strategy_id": mapped.strategy_id,
                "allocation_version": mapped.allocation_version,
                "instrument": mapped.instrument,
            },
        )
        return mapped

    admission.map_group_intent = observe_map

    original_reserve = admission.reserve_group_vector

    def observe_reserve(
        scope: Any, group_id: str, members: Any, *, deadline_ns: int
    ) -> RiskGroupReservationReceipt:
        member_facts = tuple(
            (leg_id, intent.intent_id, intent.fingerprint, format(intent.quantity, "f"))
            for leg_id, intent in members
        )
        record("risk_reserve_started", group_id, (deadline_ns, member_facts))
        try:
            receipt = original_reserve(scope, group_id, members, deadline_ns=deadline_ns)
        except Exception as error:
            record(
                "risk_reserve_denied",
                group_id,
                (type(error).__name__, getattr(error, "code", None), member_facts),
            )
            raise
        record("risk_reserve_committed", group_id, _risk_receipt_facts(receipt))
        return receipt

    admission.reserve_group_vector = observe_reserve

    original_release = admission.release_group_unclaimed

    def observe_release(scope: Any, group_id: str, reason: str) -> RiskGroupReservationReceipt:
        record("risk_release_started", group_id, reason)
        try:
            receipt = original_release(scope, group_id, reason)
        except Exception as error:
            record(
                "risk_release_failed",
                group_id,
                (type(error).__name__, getattr(error, "code", None)),
            )
            raise
        record("risk_release_committed", group_id, _risk_receipt_facts(receipt))
        return receipt

    admission.release_group_unclaimed = observe_release

    original_complete = resources.accepted_store.complete_reservation

    def observe_complete(binding: Any, reservation: Any) -> Any:
        completed = original_complete(binding, reservation)
        if completed.reservation is not None:
            record(
                "accepted_reservation_committed",
                binding.group_id,
                _service_receipt_facts(completed.reservation),
            )
        return completed

    resources.accepted_store.complete_reservation = observe_complete


def _account_host_with_real_risk_limit(
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
    reservation_observation_group_id: str | None,
    reservation_committed: Any | None,
    reservation_reply_entered: Any | None,
    reservation_reply_release: Any | None,
    risk_events: Any,
) -> None:
    """Configure account B's real allocation, then run the accepted Host."""

    original_factory = accepted._new_account_resources

    def configured_resources(
        resource_account_ref: str,
        resource_paths: Mapping[str, str],
        resource_initial_states: tuple[str, ...],
        resource_metadata_ns: int,
        entered: Any,
        release: Any,
    ) -> Any:
        resources = original_factory(
            resource_account_ref,
            resource_paths,
            resource_initial_states,
            resource_metadata_ns,
            entered,
            release,
        )
        if resource_account_ref == "account-b":
            resources.risk_gate.set_strategy_allocation(
                resources.risk_scope,
                accepted._STRATEGIES[0],
                _LIMITED_ALLOCATION_VERSION,
                max_notional=_LIMITED_STRATEGY_NOTIONAL,
                notional_unit="USD",
            )
        _install_risk_observers(resources, resource_account_ref, risk_events)
        return resources

    accepted._new_account_resources = configured_resources
    try:
        accepted._account_host_process(
            control,
            raw_config,
            registry_path,
            paths,
            account_ref,
            server_public,
            server_secret,
            coordinator_client_public,
            metadata_reference_ns,
            initial_states,
            provider_entered,
            provider_release,
            reservation_observation_group_id=reservation_observation_group_id,
            reservation_committed=reservation_committed,
            reservation_reply_entered=reservation_reply_entered,
            reservation_reply_release=reservation_reply_release,
        )
    finally:
        accepted._new_account_resources = original_factory


def _next_risk_event(
    events: Any,
    observed: list[tuple[Any, ...]],
    *,
    account_ref: str,
    group_id: str,
    kind: str,
    timeout: float = 20,
) -> tuple[Any, ...]:
    deadline = time.monotonic() + timeout
    while True:
        remaining = deadline - time.monotonic()
        assert remaining > 0, f"missing {kind} for {account_ref}/{group_id}"
        event = events.get(timeout=remaining)
        observed.append(event)
        if event[0] == account_ref and event[2] == kind and event[3] == group_id:
            return event


def _drain_risk_events(events: Any, observed: list[tuple[Any, ...]]) -> None:
    while True:
        try:
            observed.append(events.get(timeout=0.2))
        except queue.Empty:
            return


def _provider_state(account_paths: Mapping[str, Mapping[str, str]]) -> dict[str, Any]:
    return {
        account_ref: accepted._provider_snapshot(Path(paths["provider"]))
        for account_ref, paths in account_paths.items()
    }


def _expected_b_mapping_denial_facts(group_id: str) -> dict[str, Any]:
    return {
        "request": {
            "intent_id": group_leg_intent_id(group_id, "leg-b"),
            "wire_scope": (
                "fixture",
                "account-b",
                "simulation",
                accepted._STRATEGIES[0],
            ),
            "instrument": accepted._V2_INSTRUMENT,
            "side": "SELL",
            "position_effect": "OPEN",
            "order_type": "LIMIT",
            "quantity": "1",
            "price": "100",
            "request_notional": "100",
        },
        "risk_scope": ("fixture", "provider-account-b", "simulation"),
        "allocation": {
            "strategy_id": accepted._STRATEGIES[0],
            "revision": _LIMITED_ALLOCATION_VERSION,
            "allocated_notional": "50",
            "used_notional": "0",
            "reserved_notional": "0",
            "available_notional": "50",
            "notional_unit": "USD",
        },
        "error_type": "RiskAdmissionDeniedError",
        "error_code": "STRATEGY_ALLOCATION_EXHAUSTED",
    }


def _assert_no_provider_or_child_dispatch(
    account_paths: Mapping[str, Mapping[str, str]],
    group_ids: tuple[str, ...],
    strategy_ref: str,
) -> None:
    for account_ref in accepted._ACCOUNTS:
        writes, counters = accepted._provider_snapshot(
            Path(account_paths[account_ref]["provider"])
        )
        assert writes == ()
        assert counters == {"order_queries": 0, "fill_queries": 0}
        execution = SqliteExecutionStore(account_paths[account_ref]["execution"])
        try:
            leg_id = "leg-a" if account_ref == "account-a" else "leg-b"
            for group_id in group_ids:
                assert (
                    execution.get(
                        group_leg_intent_id(group_id, leg_id),
                        scope=accepted._execution_scope(account_ref, strategy_ref),
                    )
                    is None
                )
        finally:
            execution.close()


def _assert_pre_dispatch_rejection(snapshot: GroupOrderSnapshot, group_id: str) -> None:
    assert snapshot.request.group_id == group_id
    assert snapshot.state is GroupOrderState.REJECTED_BEFORE_DISPATCH
    assert snapshot.error_code == "strategy_allocation_exhausted"
    assert snapshot.budget_reservation is not None
    assert snapshot.budget_reservation.gross_notional == _GROUP_NOTIONAL
    assert snapshot.budget_reservation.valuation_unit == "USD"
    assert snapshot.budget_reservation.state is GroupBudgetReservationState.RELEASED
    assert all(leg.submitted_intent is None for leg in snapshot.legs)
    reservations = {item.account_ref: item for item in snapshot.reservations}
    assert {account_ref: item.phase for account_ref, item in reservations.items()} == {
        "account-a": GroupReservationPhase.RELEASED,
        "account-b": GroupReservationPhase.DENIED,
    }
    denied = reservations["account-b"]
    assert denied.group_id == group_id
    assert denied.group_fingerprint is None
    assert denied.policy_fingerprint is None
    assert denied.deadline_ns is None
    assert denied.legs == ()
    assert all(item.phase is not GroupReservationPhase.UNKNOWN for item in snapshot.reservations)


def _assert_typed_unclaimed_release_order(
    observed: list[tuple[Any, ...]], group_id: str
) -> None:
    account_events = [
        event
        for event in observed
        if event[0] == "account-a" and event[3] == group_id
    ]
    returned = [
        (index, event)
        for index, event in enumerate(account_events)
        if event[2] == "risk_snapshot_returned"
        and event[4]["type"] == "RiskGroupReservationReceipt"
    ]
    assert len(returned) >= 2, account_events
    receipt_index, proof = returned[0]
    assert len(proof[4]["legs"]) == 1
    proof_leg = proof[4]["legs"][0]
    assert proof_leg[0:2] == ("leg-a", group_leg_intent_id(group_id, "leg-a"))
    assert proof_leg[4:] == ("ACTIVE", False, False)
    assert all(
        event[4]["type"] == "RiskGroupReservationReceipt"
        and event[4]["group_id"] == group_id
        and event[4]["scope"] == ("fixture", "provider-account-a", "simulation")
        and event[4]["legs"][0][4:] == ("ACTIVE", False, False)
        for _index, event in returned
    )
    release_started = [
        index
        for index, event in enumerate(account_events)
        if event[2] == "risk_release_started"
    ]
    release_committed = [
        index
        for index, event in enumerate(account_events)
        if event[2] == "risk_release_committed"
    ]
    assert len(release_started) == len(release_committed) == 1, account_events
    assert receipt_index < release_started[0] < release_committed[0], account_events
    assert account_events[release_started[0]][4] == "group_rejected_before_dispatch"
    released = account_events[release_committed[0]][4]
    assert released["type"] == "RiskGroupReservationReceipt"
    assert released["group_id"] == group_id
    assert released["scope"] == ("fixture", "provider-account-a", "simulation")
    assert all(
        leg[4] == "RELEASED" and leg[5] is False and leg[6] is False
        for leg in released["legs"]
    )


@pytest.mark.integration
def test_real_account_risk_rejection_releases_group_budget_before_dispatch(
    tmp_path: Path,
) -> None:
    """A typed Risk denial releases A only after a typed unclaimed proof."""

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
        for account in accepted._ACCOUNTS
    }
    coordinator_paths = {
        "registry": str(tmp_path / "coordinator-registry.sqlite3"),
        "groups": str(tmp_path / "coordinator-groups.sqlite3"),
        "run_bindings": str(tmp_path / "coordinator-run-bindings.sqlite3"),
    }
    account_endpoints = {account: accepted._endpoint() for account in accepted._ACCOUNTS}
    account_keys = {account: zmq.curve_keypair() for account in accepted._ACCOUNTS}
    coordinator_keys = zmq.curve_keypair()
    coordinator_client_keys = zmq.curve_keypair()
    strategy_keys = {strategy: zmq.curve_keypair() for strategy in accepted._STRATEGIES}
    account_configs = {
        account: accepted._account_service_config(
            account, account_endpoints[account], f"arb02-account-server-{account}"
        )
        for account in accepted._ACCOUNTS
    }

    context = multiprocessing.get_context("spawn")
    provider_entered = context.Event()
    provider_release = context.Event()
    reservation_committed = context.Event()
    reservation_reply_entered = context.Event()
    reservation_reply_release = context.Event()
    risk_events = context.Queue()
    account_processes: dict[str, Any] = {}
    account_controls: dict[str, Any] = {}
    coordinator_process = None
    coordinator_control = None
    client_threads: dict[str, threading.Thread] = {}
    client_commands: dict[str, queue.Queue] = {}
    client_results: dict[str, queue.Queue] = {}
    observed_risk_events: list[tuple[Any, ...]] = []
    snapshots: dict[str, GroupOrderSnapshot] = {}
    requests = {
        _GROUP_RISK_REJECTION: accepted._group_request(
            _GROUP_RISK_REJECTION,
            accepted._STRATEGIES[0],
            metadata_reference_ns,
        ),
        _GROUP_BUDGET_REUSE: accepted._group_request(
            _GROUP_BUDGET_REUSE,
            accepted._STRATEGIES[0],
            metadata_reference_ns,
        ),
    }

    try:
        for account_ref in accepted._ACCOUNTS:
            parent_control, child_control = context.Pipe(duplex=True)
            process = None
            process_started = False
            try:
                process = context.Process(
                    name=f"arb02-risk-rejection-{account_ref}",
                    target=_account_host_with_real_risk_limit,
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
                        _GROUP_RISK_REJECTION if account_ref == "account-a" else None,
                        reservation_committed if account_ref == "account-a" else None,
                        reservation_reply_entered if account_ref == "account-a" else None,
                        reservation_reply_release if account_ref == "account-a" else None,
                        risk_events,
                    ),
                )
                process.start()
                process_started = True
                child_control.close()
                ready = accepted._receive(parent_control)
                assert ready == ("ready", account_endpoints[account_ref])
            except BaseException as startup_error:
                cleanup_error = accepted._cleanup_failed_host_start(
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
        ) = accepted._start_coordinator(
            context,
            account_configs,
            coordinator_paths,
            account_endpoints,
            {account: account_keys[account][0] for account in accepted._ACCOUNTS},
            coordinator_keys,
            coordinator_client_keys,
            strategy_keys,
            metadata_reference_ns,
        )
        accepted._activate_owner_routes(account_controls, upstream_bindings)
        client_threads, client_commands, client_results = accepted._new_client_threads(
            coordinator_endpoint,
            coordinator_keys[0],
            strategy_keys,
        )

        client_commands[accepted._STRATEGIES[0]].put(
            ("submit", _GROUP_RISK_REJECTION, requests[_GROUP_RISK_REJECTION])
        )
        first_started = accepted._next_client_message(
            client_results[accepted._STRATEGIES[0]], "started"
        )
        assert first_started[1:3] == ("submit", _GROUP_RISK_REJECTION)
        assert reservation_committed.wait(20), "account A did not commit its Risk reservation"
        assert reservation_reply_entered.wait(20), "account A did not hold the committed reply"

        a_risk_commit = _next_risk_event(
            risk_events,
            observed_risk_events,
            account_ref="account-a",
            group_id=_GROUP_RISK_REJECTION,
            kind="risk_reserve_committed",
        )
        a_store_commit = _next_risk_event(
            risk_events,
            observed_risk_events,
            account_ref="account-a",
            group_id=_GROUP_RISK_REJECTION,
            kind="accepted_reservation_committed",
        )
        assert a_risk_commit[4]["type"] == "RiskGroupReservationReceipt"
        assert a_risk_commit[4]["group_id"] == _GROUP_RISK_REJECTION
        assert a_risk_commit[4]["scope"] == (
            "fixture",
            "provider-account-a",
            "simulation",
        )
        assert a_risk_commit[4]["deadline_ns"] == requests[_GROUP_RISK_REJECTION][
            "deadline_ns"
        ]
        assert a_risk_commit[4]["legs"][0][0:2] == (
            "leg-a",
            group_leg_intent_id(_GROUP_RISK_REJECTION, "leg-a"),
        )
        assert a_risk_commit[4]["legs"][0][2]
        assert len(a_risk_commit[4]["legs"][0][3]) == 64
        assert a_risk_commit[4]["legs"][0][4:] == ("ACTIVE", False, False)
        assert a_store_commit[4]["type"] == "GroupAccountReservationSnapshot"
        assert a_store_commit[4]["phase"] == GroupReservationPhase.ACTIVE.value
        assert a_store_commit[4]["legs"][0][0:2] == (
            "leg-a",
            group_leg_intent_id(_GROUP_RISK_REJECTION, "leg-a"),
        )
        assert a_store_commit[4]["legs"][0][2:4] == a_risk_commit[4]["legs"][0][2:4]
        assert a_store_commit[4]["legs"][0][4:] == ("ACTIVE", False, False)
        assert not any(
            event[0] == "account-b" and event[3] == _GROUP_RISK_REJECTION
            for event in observed_risk_events
        ), "B was contacted before A's committed reservation reply"

        reservation_reply_release.set()
        first_done = accepted._next_client_message(
            client_results[accepted._STRATEGIES[0]], "done"
        )
        assert first_done[1:3] == ("submit", _GROUP_RISK_REJECTION)
        snapshots[_GROUP_RISK_REJECTION] = accepted._snapshot_from_reply(first_done[5])
        _assert_pre_dispatch_rejection(snapshots[_GROUP_RISK_REJECTION], _GROUP_RISK_REJECTION)

        b_mapping_denial = _next_risk_event(
            risk_events,
            observed_risk_events,
            account_ref="account-b",
            group_id=group_leg_intent_id(_GROUP_RISK_REJECTION, "leg-b"),
            kind="risk_intent_mapping_denied",
        )
        assert b_mapping_denial[4] == _expected_b_mapping_denial_facts(
            _GROUP_RISK_REJECTION
        )
        _next_risk_event(
            risk_events,
            observed_risk_events,
            account_ref="account-a",
            group_id=_GROUP_RISK_REJECTION,
            kind="risk_release_committed",
        )
        _assert_typed_unclaimed_release_order(observed_risk_events, _GROUP_RISK_REJECTION)
        assert not any(
            event[0] == "account-b"
            and event[2] in {"risk_release_started", "risk_release_committed"}
            and event[3] == _GROUP_RISK_REJECTION
            for event in observed_risk_events
        )
        assert _provider_state(account_paths) == {
            "account-a": ((), {"order_queries": 0, "fill_queries": 0}),
            "account-b": ((), {"order_queries": 0, "fill_queries": 0}),
        }

        # Persisted rejection remains queryable after the coordinator Host
        # exits normally; the exact same request is never advanced on query.
        client_commands[accepted._STRATEGIES[0]].put(
            ("query", _GROUP_RISK_REJECTION, None)
        )
        accepted._next_client_message(client_results[accepted._STRATEGIES[0]], "started")
        first_query = accepted._next_client_message(
            client_results[accepted._STRATEGIES[0]], "done"
        )
        assert accepted._query_reply_snapshot(first_query[5]) == snapshots[_GROUP_RISK_REJECTION]
        accepted._close_client_threads(client_threads, client_commands, client_results)
        client_threads = {}
        client_commands = {}
        client_results = {}
        accepted._stop_process(coordinator_process, coordinator_control)
        coordinator_process = None
        coordinator_control = None

        (
            coordinator_process,
            coordinator_control,
            coordinator_endpoint,
            _upstream_bindings,
        ) = accepted._start_coordinator(
            context,
            account_configs,
            coordinator_paths,
            account_endpoints,
            {account: account_keys[account][0] for account in accepted._ACCOUNTS},
            coordinator_keys,
            coordinator_client_keys,
            strategy_keys,
            metadata_reference_ns,
        )
        accepted._activate_owner_routes(account_controls, _upstream_bindings)
        client_threads, client_commands, client_results = accepted._new_client_threads(
            coordinator_endpoint,
            coordinator_keys[0],
            strategy_keys,
        )
        client_commands[accepted._STRATEGIES[0]].put(
            ("query", _GROUP_RISK_REJECTION, None)
        )
        accepted._next_client_message(client_results[accepted._STRATEGIES[0]], "started")
        reopened_query = accepted._next_client_message(
            client_results[accepted._STRATEGIES[0]], "done"
        )
        assert accepted._query_reply_snapshot(reopened_query[5]) == snapshots[
            _GROUP_RISK_REJECTION
        ]

        client_commands[accepted._STRATEGIES[0]].put(
            ("submit", _GROUP_BUDGET_REUSE, requests[_GROUP_BUDGET_REUSE])
        )
        second_started = accepted._next_client_message(
            client_results[accepted._STRATEGIES[0]], "started"
        )
        assert second_started[1:3] == ("submit", _GROUP_BUDGET_REUSE)
        second_done = accepted._next_client_message(
            client_results[accepted._STRATEGIES[0]], "done"
        )
        assert second_done[1:3] == ("submit", _GROUP_BUDGET_REUSE)
        snapshots[_GROUP_BUDGET_REUSE] = accepted._snapshot_from_reply(second_done[5])
        _assert_pre_dispatch_rejection(snapshots[_GROUP_BUDGET_REUSE], _GROUP_BUDGET_REUSE)
        second_b_mapping_denial = _next_risk_event(
            risk_events,
            observed_risk_events,
            account_ref="account-b",
            group_id=group_leg_intent_id(_GROUP_BUDGET_REUSE, "leg-b"),
            kind="risk_intent_mapping_denied",
        )
        assert second_b_mapping_denial[4] == _expected_b_mapping_denial_facts(
            _GROUP_BUDGET_REUSE
        )
        _next_risk_event(
            risk_events,
            observed_risk_events,
            account_ref="account-a",
            group_id=_GROUP_BUDGET_REUSE,
            kind="risk_release_committed",
        )
        _assert_typed_unclaimed_release_order(observed_risk_events, _GROUP_BUDGET_REUSE)
        assert _provider_state(account_paths) == {
            "account-a": ((), {"order_queries": 0, "fill_queries": 0}),
            "account-b": ((), {"order_queries": 0, "fill_queries": 0}),
        }
        accepted._close_client_threads(client_threads, client_commands, client_results)
        client_threads = {}
        client_commands = {}
        client_results = {}
        accepted._stop_process(coordinator_process, coordinator_control)
        coordinator_process = None
        coordinator_control = None
    finally:
        reservation_reply_release.set()
        accepted._cleanup_group_hosts(
            provider_release=provider_release,
            client_threads=client_threads,
            client_commands=client_commands,
            client_results=client_results,
            coordinator_process=coordinator_process,
            coordinator_control=coordinator_control,
            account_processes=account_processes,
            account_controls=account_controls,
            close_client_threads=accepted._close_client_threads,
            stop_process=accepted._stop_process,
        )

    _drain_risk_events(risk_events, observed_risk_events)
    risk_events.close()
    risk_events.join_thread()
    assert set(snapshots) == {_GROUP_RISK_REJECTION, _GROUP_BUDGET_REUSE}

    _assert_no_provider_or_child_dispatch(
        account_paths,
        (_GROUP_RISK_REJECTION, _GROUP_BUDGET_REUSE),
        accepted._STRATEGIES[0],
    )

    groups = SqliteOrderGroupStore(coordinator_paths["groups"])
    try:
        for group_id, expected in snapshots.items():
            persisted = groups.get(accepted._PORTFOLIO, group_id)
            assert persisted == expected
            _assert_pre_dispatch_rejection(persisted, group_id)
            events = groups.events(accepted._PORTFOLIO, group_id)
            assert not any(
                event.event_type in {"RESERVATION_UNKNOWN", "GROUP_RECOVERY_REQUIRED"}
                for event in events
            )
            reservation_denied_events = [
                event
                for event in events
                if event.event_type == "RESERVATION_DENIED"
                and event.payload.get("account_ref") == "account-b"
            ]
            assert len(reservation_denied_events) == 1
            reservation_denied = reservation_denied_events[0].payload
            assert reservation_denied["phase"] == GroupReservationPhase.DENIED.value
            assert reservation_denied["prior_phase"] == GroupReservationPhase.REQUESTING.value
            assert reservation_denied["code"] == "strategy_allocation_exhausted"
            accepted_event = next(
                event for event in events if event.event_type == "GROUP_ACCEPTED"
            )
            budget = accepted_event.payload["budget"]
            assert budget["decision"] == "RESERVED"
            assert Decimal(budget["gross_notional"]) == _GROUP_NOTIONAL
            rejection_event = next(
                event
                for event in events
                if event.event_type == "GROUP_REJECTED_BEFORE_DISPATCH"
            )
            assert rejection_event.payload["code"] == "STRATEGY_ALLOCATION_EXHAUSTED"

        reused_budget = next(
            event
            for event in groups.events(accepted._PORTFOLIO, _GROUP_BUDGET_REUSE)
            if event.event_type == "GROUP_ACCEPTED"
        ).payload["budget"]
        assert reused_budget["active_gross_notional_before"] == "0"
        assert reused_budget["active_groups_before"] == 0
    finally:
        groups.close()

    for account_ref in accepted._ACCOUNTS:
        risk_scope = AccountScope("fixture", f"provider-{account_ref}", "simulation")
        gate = DurableRiskGate(account_paths[account_ref]["risk_gate"], accepted._risk_policy())
        try:
            if account_ref == "account-a":
                for group_id in (_GROUP_RISK_REJECTION, _GROUP_BUDGET_REUSE):
                    receipt = gate.read_group_vector_snapshot(risk_scope, group_id)
                    assert type(receipt) is RiskGroupReservationReceipt
                    assert receipt.group_id == group_id
                    assert len(receipt.legs) == 1
                    assert receipt.legs[0].state.name == "RELEASED"
                    assert receipt.legs[0].claimed is False
                    assert receipt.legs[0].resolved is False
                    assert (
                        receipt.legs[0].intent_id
                        == group_leg_intent_id(group_id, "leg-a")
                    )
            else:
                for group_id in (_GROUP_RISK_REJECTION, _GROUP_BUDGET_REUSE):
                    with pytest.raises(RiskDeniedError) as missing:
                        gate.read_group_vector_snapshot(risk_scope, group_id)
                    assert type(missing.value) is RiskDeniedError
                    assert missing.value.code == "GROUP_NOT_FOUND"
                allocation = gate.get_strategy_allocation(
                    risk_scope, accepted._STRATEGIES[0]
                )
                assert allocation.revision == _LIMITED_ALLOCATION_VERSION
                assert allocation.allocated_notional == _LIMITED_STRATEGY_NOTIONAL
                assert allocation.notional_unit == "USD"
                assert allocation.used_notional == Decimal("0")
                assert allocation.reserved_notional == Decimal("0")
                assert allocation.available_notional == _LIMITED_STRATEGY_NOTIONAL
        finally:
            gate.close()
