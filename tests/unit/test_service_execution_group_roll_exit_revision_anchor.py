from __future__ import annotations

from dataclasses import dataclass, replace
from pathlib import Path
import shutil
from types import SimpleNamespace

import pytest
from bt_api_base.service_protocol import WireScope
from bt_api_execution import (
    ExecutionRecord,
    GroupLegOrderReceipt,
    SqliteOrderGroupStore,
)
from bt_api_py.service_execution_group_client import (
    AcceptedGroupBinding,
    GroupOriginRunBinding,
)
from bt_api_py.service_execution_group_roll_exit_source import (
    GroupRollExitAcceptedSourceV1,
    GroupRollExitAuthorizationSourceError,
    GroupRollExitAuthorizationSourceV1,
    _group_event_chain_digest,
)
from bt_api_py.service_group_roll_exit import (
    GroupRollExitAuthorizationV1,
    group_roll_exit_action_id,
)


_GROUP_JOURNAL_FIXTURE = (
    Path(__file__).resolve().parents[1]
    / "fixtures"
    / "arb06_roll_exit_revision_anchor.sqlite3"
)
_GROUP_ID = "group-arb06-two-leg-roll-exit-closed-01"
_PORTFOLIO = "portfolio-group-partial-fill"
_PREPLAN_REVISION = 18


@dataclass(frozen=True)
class _Case:
    adapter: GroupRollExitAuthorizationSourceV1
    authorization: GroupRollExitAuthorizationV1
    query_registration: object
    entry_registration: object
    source: GroupRollExitAcceptedSourceV1
    snapshot: object
    events: tuple[object, ...]
    store: SqliteOrderGroupStore


def _registrations(authorization: GroupRollExitAuthorizationV1) -> tuple[object, object]:
    facts = authorization.source
    context = SimpleNamespace(
        principal_ref=facts.service_principal_ref,
        registration_id=facts.service_registration_id,
        scope=SimpleNamespace(account_ref=facts.account_ref),
    )
    entry = SimpleNamespace(
        context=context,
        instance_id=facts.service_instance_id,
        scope_ref=facts.service_scope_ref,
        service_epoch=facts.service_epoch,
        policy_version=facts.service_policy_version,
        requirements_version=facts.service_requirements_version,
        strategy_write_run_id=facts.service_write_run_id,
    )
    query = SimpleNamespace(context=context)
    return query, entry


def _source_projection(
    authorization: GroupRollExitAuthorizationV1,
    snapshot: object,
    events: tuple[object, ...],
    *,
    operation: str,
) -> GroupRollExitAcceptedSourceV1:
    facts = authorization.source
    request = facts.group_request
    plan = request.leg(facts.source_leg_id)
    progress = next(item for item in snapshot.legs if item.leg_id == facts.source_leg_id)
    assert progress.child_status is not None
    receipt = GroupLegOrderReceipt(
        status=progress.child_status,
        claim=progress.claim_receipt,
        resolution=progress.resolution_receipt,
    )
    group_scope = WireScope(
        execution_domain_ref="arb06-revision-test",
        account_refs=request.account_refs,
        strategy_ref=request.strategy_id,
        run_id=facts.origin_run_id,
        portfolio_ref=request.portfolio_ref,
    )
    origin = GroupOriginRunBinding(
        request=request,
        origin_run_id=facts.origin_run_id,
        strategy_principal_ref=request.strategy_id,
        strategy_identity={
            "role": "STRATEGY",
            "principal_ref": request.strategy_id,
            "actor_ref": None,
            "delegate_ref": None,
        },
        strategy_scope=group_scope,
        coordinator_principal_ref=request.coordinator_principal_ref,
        instance_id="arb06-revision-test-instance",
        scope_ref="arb06-revision-test-scope",
        policy_version="arb06-revision-test-policy",
        requirements_version="arb06-revision-test-requirements",
        registration_id="arb06-revision-test-registration",
        service_epoch="arb06-revision-test-epoch",
        request_fingerprint=request.fingerprint,
        accepted_request_fingerprint=snapshot.request.fingerprint,
    )
    account_reservations = tuple(
        item for item in snapshot.reservations if item.account_ref == facts.account_ref
    )
    assert len(account_reservations) == 1
    accepted_scope = WireScope(
        execution_domain_ref="arb06-revision-test",
        account_ref=facts.account_ref,
        strategy_ref=facts.strategy_id,
        run_id=facts.origin_run_id,
        portfolio_ref=facts.portfolio_ref,
    )
    accepted = AcceptedGroupBinding(
        identity={
            "role": "STRATEGY",
            "principal_ref": facts.strategy_id,
            "actor_ref": None,
            "delegate_ref": None,
        },
        principal_ref=facts.strategy_id,
        first_registration_id="arb06-revision-test-registration",
        instance_id="arb06-revision-test-instance",
        scope_ref="arb06-revision-test-scope",
        service_epoch="arb06-revision-test-epoch",
        policy_version="arb06-revision-test-policy",
        requirements_version="arb06-revision-test-requirements",
        stable_scope=accepted_scope,
        origin_run_id=facts.origin_run_id,
        group_id=facts.group_id,
        deadline_ns=request.deadline_ns,
        members=tuple(
            (item.leg_id, item.maximum_intent)
            for item in request.legs
            if item.account_ref == facts.account_ref
        ),
        request_fingerprint=request.fingerprint,
        reservation=account_reservations[0],
    )
    status = receipt.status
    execution_record = ExecutionRecord(
        intent_id=plan.maximum_intent.intent_id,
        scope_key=facts.source_execution_scope.key,
        payload_sha256=plan.maximum_intent.fingerprint,
        state=status.state,
        provider_order_id=status.provider_order_id,
        filled_quantity=status.filled_quantity,
        average_price=status.average_price,
        permit_reference=None if receipt.claim is None else receipt.claim.permit.permit_id,
        dispatch_attempts=1,
        unknown_reason=None,
        review_required=False,
        created_at_ns=snapshot.created_at_ns,
        updated_at_ns=snapshot.updated_at_ns,
    )
    chain_events = events
    if snapshot.revision == _PREPLAN_REVISION:
        chain_events = events[: snapshot.revision + 1]
    return GroupRollExitAcceptedSourceV1(
        operation=operation,
        request=authorization.request,
        query_registration_digest="a" * 64,
        entry_registration_digest=facts.service_registration_digest,
        origin_binding=origin,
        accepted_binding=accepted,
        group_snapshot=snapshot,
        source_plan=plan,
        source_progress=progress,
        execution_record=execution_record,
        source_receipt=receipt,
        source_event_digest=facts.source_event_digest,
        group_event_chain_digest=_group_event_chain_digest(chain_events, snapshot),
    )


@pytest.fixture
def roll_exit_case(tmp_path: Path):
    journal_path = tmp_path / "coordinator-groups.sqlite3"
    shutil.copyfile(_GROUP_JOURNAL_FIXTURE, journal_path)
    store = SqliteOrderGroupStore(journal_path)
    try:
        snapshot = store.get(_PORTFOLIO, _GROUP_ID)
        assert snapshot is not None
        events = store.events(_PORTFOLIO, _GROUP_ID)
        plans = tuple(
            event for event in events if event.event_type == "GROUP_ROLL_EXIT_PLAN_RECORDED"
        )
        assert len(plans) == 1
        authorization = GroupRollExitAuthorizationV1.from_payload(
            plans[0].payload["authorization"]
        )
        source = _source_projection(
            authorization,
            snapshot,
            events,
            operation="orders.query_group_roll_exit",
        )
        adapter = object.__new__(GroupRollExitAuthorizationSourceV1)
        adapter._execution_scope = authorization.source.source_execution_scope
        adapter._group_store = store
        # Keep the production assertion and revision-history fold real. Only
        # registration and accepted-source reconstruction are the synthetic
        # fixture leaves for this focused contract test.
        adapter._validate_registration_pair = lambda *_args, **_kwargs: None
        adapter._rebuild_source = lambda *_args, operation, **_kwargs: replace(
            source, operation=operation
        )
        query_registration, entry_registration = _registrations(authorization)
        yield _Case(
            adapter=adapter,
            authorization=authorization,
            query_registration=query_registration,
            entry_registration=entry_registration,
            source=source,
            snapshot=snapshot,
            events=events,
            store=store,
        )
    finally:
        store.close()


def _assert_authorized(case: _Case, operation: str) -> GroupRollExitAcceptedSourceV1:
    return case.adapter.assert_authorization(
        case.query_registration,
        case.authorization,
        operation=operation,
        entry_registration=case.entry_registration,
    )


def test_persisted_plan_anchor_allows_query_and_started_submit(roll_exit_case: _Case) -> None:
    case = roll_exit_case
    assert case.snapshot.revision == 22
    assert case.authorization.source.group_revision == _PREPLAN_REVISION
    assert tuple(event.event_type for event in case.events[-4:]) == (
        "GROUP_ROLL_EXIT_PLAN_RECORDED",
        "GROUP_ROLL_EXIT_SUBMIT_STARTED",
        "GROUP_ROLL_EXIT_STATUS_OBSERVED",
        "GROUP_ROLL_EXIT_STATUS_OBSERVED",
    )

    expected_chain_digest = _group_event_chain_digest(case.events, case.snapshot)
    assert (
        _assert_authorized(case, "orders.query_group_roll_exit").group_event_chain_digest
        == expected_chain_digest
    )
    assert (
        _assert_authorized(case, "orders.submit_group_roll_exit").group_event_chain_digest
        == expected_chain_digest
    )


@pytest.mark.parametrize(
    "operation",
    ("orders.prepare_group_roll_exit", "orders.query_group_roll_exit"),
)
def test_preplan_equal_revision_remains_valid_for_prepare_and_query(
    roll_exit_case: _Case,
    operation: str,
) -> None:
    case = roll_exit_case
    preplan_snapshot = replace(case.snapshot, revision=_PREPLAN_REVISION)
    projection = _source_projection(
        case.authorization,
        preplan_snapshot,
        case.events,
        operation=operation,
    )
    case.adapter._rebuild_source = lambda *_args, **_kwargs: projection

    result = case.adapter.assert_authorization(
        case.query_registration,
        case.authorization,
        operation=operation,
        entry_registration=case.entry_registration,
    )
    assert result.group_snapshot.revision == _PREPLAN_REVISION


def test_preplan_equal_revision_cannot_authorize_submit(roll_exit_case: _Case) -> None:
    case = roll_exit_case
    preplan_snapshot = replace(case.snapshot, revision=_PREPLAN_REVISION)
    projection = _source_projection(
        case.authorization,
        preplan_snapshot,
        case.events,
        operation="orders.submit_group_roll_exit",
    )
    case.adapter._rebuild_source = lambda *_args, **_kwargs: projection

    with pytest.raises(GroupRollExitAuthorizationSourceError) as raised:
        case.adapter.assert_authorization(
            case.query_registration,
            case.authorization,
            operation="orders.submit_group_roll_exit",
            entry_registration=case.entry_registration,
        )
    assert raised.value.code == "GROUP_ROLL_EXIT_SOURCE_BINDING_MISMATCH"


def test_changed_authorization_revision_is_not_rebound_to_another_plan(
    roll_exit_case: _Case,
) -> None:
    case = roll_exit_case
    changed_facts = replace(case.authorization.source, group_revision=_PREPLAN_REVISION - 1)
    changed_action_id = group_roll_exit_action_id(
        changed_facts.digest, case.authorization.request.fingerprint
    )
    changed_intent = replace(
        case.authorization.intent,
        intent_id=changed_action_id,
        signal_id=changed_action_id,
    )
    changed_authorization = GroupRollExitAuthorizationV1.build(
        request=case.authorization.request,
        source=changed_facts,
        intent=changed_intent,
        deadline_ns=case.authorization.deadline_ns,
        risk_intent_id=case.authorization.risk_intent_id,
    )

    with pytest.raises(GroupRollExitAuthorizationSourceError) as raised:
        case.adapter.assert_authorization(
            case.query_registration,
            changed_authorization,
            operation="orders.query_group_roll_exit",
            entry_registration=case.entry_registration,
        )
    assert raised.value.code == "GROUP_ROLL_EXIT_SOURCE_BINDING_MISMATCH"


def test_changed_durable_event_chain_projection_is_rejected(roll_exit_case: _Case) -> None:
    case = roll_exit_case
    projection = replace(case.source, group_event_chain_digest="f" * 64)
    case.adapter._rebuild_source = lambda *_args, **_kwargs: projection

    with pytest.raises(GroupRollExitAuthorizationSourceError) as raised:
        case.adapter.assert_authorization(
            case.query_registration,
            case.authorization,
            operation="orders.query_group_roll_exit",
            entry_registration=case.entry_registration,
        )
    assert raised.value.code == "GROUP_ROLL_EXIT_SOURCE_BINDING_MISMATCH"
