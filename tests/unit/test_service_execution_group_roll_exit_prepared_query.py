"""Risk PREPARED query projection and read-only coordinator behavior."""

from __future__ import annotations

from dataclasses import replace
from decimal import Decimal
from pathlib import Path
from types import SimpleNamespace
from typing import Any, Callable

import pytest
from bt_api_execution import (
    ExecutionScope,
    ExecutionState,
    GroupChildOrderStatus,
    GroupDispatchResolutionReceipt,
    GroupLegClaimReceipt,
    GroupLegPermitReference,
    GroupOrderIntent,
    GroupOrderLegPlan,
    OrderIntent,
    PositionEffect,
    Side,
    TimeInForce,
)
from bt_api_execution.group_contracts import group_leg_intent_id

from bt_api_py.service_execution_group import (
    _CURRENT_COORDINATOR_CALL,
    _CoordinatorCall,
    _OrdersRpcPort,
    _roll_exit_query_observation,
)
from bt_api_py.service_group_roll_exit import (
    GroupRollExitActionStore,
    GroupRollExitAuthorizationV1,
    GroupRollExitObservationV1,
    GroupRollExitPhase,
    GroupRollExitRequestV1,
    GroupRollExitSourceFactsV1,
    group_roll_exit_action_id,
)
from bt_api_py.service_risk_group_roll_exit import (
    RiskGroupRollExitActionStore,
    RiskGroupRollExitOwner,
)

_DEADLINE_NS = 1_900_000_000_000_000_100
_PORTFOLIO = "portfolio-prepared-query"
_GROUP_ID = "group-prepared-query"
_SOURCE_LEG = "old-contract"
_ACCOUNT = "account-prepared-query"
_STRATEGY = "strategy-prepared-query"
_INSTRUMENT = "BTC-USD-202612-FUT"
_RISK_INTENT_ID = "risk-roll-exit-intent"
_QUERY_SCOPE = "roll-exit-query-scope"
_MISSING = object()


def _source_and_request() -> tuple[GroupRollExitSourceFactsV1, GroupRollExitRequestV1]:
    scope = ExecutionScope("simnow", "paper", _ACCOUNT, _STRATEGY, "20261215")
    old_intent = OrderIntent.limit(
        intent_id=group_leg_intent_id(_GROUP_ID, _SOURCE_LEG),
        scope=scope,
        signal_id="signal-old-contract",
        instrument=_INSTRUMENT,
        side=Side.BUY,
        quantity=Decimal("2"),
        price=Decimal("100"),
        time_in_force=TimeInForce.IOC,
        metadata_version="source-meta-7",
        deadline_ns=_DEADLINE_NS,
    )
    source_child_intent = replace(old_intent, quantity=Decimal("2"))
    next_intent = OrderIntent.limit(
        intent_id=group_leg_intent_id(_GROUP_ID, "next-contract"),
        scope=scope,
        signal_id="signal-next-contract",
        instrument="BTC-USD-202703-FUT",
        side=Side.SELL,
        quantity=Decimal("2"),
        price=Decimal("100"),
        time_in_force=TimeInForce.IOC,
        metadata_version="source-meta-7",
        deadline_ns=_DEADLINE_NS,
    )
    group_request = GroupOrderIntent(
        group_id=_GROUP_ID,
        portfolio_ref=_PORTFOLIO,
        strategy_id=_STRATEGY,
        coordinator_principal_ref="group-coordinator",
        received_at_ns=_DEADLINE_NS - 1_000,
        deadline_ns=_DEADLINE_NS,
        first_leg_id=_SOURCE_LEG,
        legs=(
            GroupOrderLegPlan(
                leg_id=_SOURCE_LEG,
                account_ref=_ACCOUNT,
                maximum_intent=old_intent,
                base_units_per_order_unit=Decimal("1"),
                quantity_step=Decimal("0.5"),
                metadata_valid_until_ns=_DEADLINE_NS + 100_000,
            ),
            GroupOrderLegPlan(
                leg_id="next-contract",
                account_ref=_ACCOUNT,
                maximum_intent=next_intent,
                base_units_per_order_unit=Decimal("1"),
                quantity_step=Decimal("0.5"),
                metadata_valid_until_ns=_DEADLINE_NS + 100_000,
            ),
        ),
    )
    permit = GroupLegPermitReference(
        group_id=_GROUP_ID,
        leg_id=_SOURCE_LEG,
        portfolio_ref=_PORTFOLIO,
        strategy_id=_STRATEGY,
        account_ref=_ACCOUNT,
        coordinator_principal_ref="group-coordinator",
        risk_scope_fingerprint="a" * 64,
        permit_id="risk-permit-old-leg",
        group_fingerprint="7" * 64,
        policy_fingerprint="b" * 64,
        reserved_intent_hash=old_intent.fingerprint,
        exposure_profile_fingerprint="c" * 64,
        order_intent_hash=source_child_intent.fingerprint,
        deadline_ns=_DEADLINE_NS,
    )
    claim = GroupLegClaimReceipt(
        permit=permit,
        actual_child_intent_hash="9" * 64,
        claim_digest="e" * 64,
        actual_quantity=Decimal("2"),
        actual_notional=Decimal("200"),
    )
    resolution = GroupDispatchResolutionReceipt(
        claim=claim,
        resolution_kind="TERMINAL_EXPOSURE_RESOLVED",
        proof_sha256="f" * 64,
        final_state=ExecutionState.CANCELLED,
        requested_quantity=Decimal("2"),
        filled_quantity=Decimal("1.5"),
        trade_count=1,
        order_revision=4,
        journal_revision=12,
        exposure_proof_sha256="1" * 64,
        exposure_reservation_id="exposure-old-leg",
        exposure_reservation_sha256="2" * 64,
        dispatch_latch_active=False,
        has_other_active_freezes=False,
    )
    status = GroupChildOrderStatus(
        intent_id=old_intent.intent_id,
        state=ExecutionState.CANCELLED,
        filled_quantity=Decimal("1.5"),
        provider_order_id="provider-old-order",
        average_price=Decimal("100"),
        order_revision=4,
        trade_count=1,
    )
    source = GroupRollExitSourceFactsV1(
        portfolio_ref=_PORTFOLIO,
        origin_run_id="strategy-run-202612",
        group_id=_GROUP_ID,
        group_version=1,
        group_revision=9,
        group_fingerprint=group_request.fingerprint,
        source_risk_reservation_fingerprint=permit.group_fingerprint,
        source_risk_intent_fingerprint=claim.actual_child_intent_hash,
        group_request=group_request,
        account_ref=_ACCOUNT,
        strategy_id=_STRATEGY,
        source_leg_id=_SOURCE_LEG,
        source_intent_id=old_intent.intent_id,
        source_request_fingerprint=source_child_intent.fingerprint,
        source_child_intent=source_child_intent,
        source_execution_scope=scope,
        source_terminal_status=status,
        source_requested_quantity=Decimal("2"),
        source_claim=claim,
        source_risk_resolution=resolution,
        source_event_digest="3" * 64,
        source_side=Side.BUY,
        instrument=_INSTRUMENT,
        source_metadata_version="source-meta-7",
        close_metadata_version="close-meta-11",
        close_metadata_digest="4" * 64,
        close_quantity_step=Decimal("0.5"),
        close_tick_size=Decimal("0.25"),
        close_metadata_valid_until_ns=_DEADLINE_NS + 100_000,
        source_position_side="NET",
        source_position_bucket="ALL",
        source_filled_quantity=Decimal("1.5"),
        prior_resolved_closes=(),
        remaining_quantity=Decimal("1.5"),
        closable_position_version="5" * 64,
        service_principal_ref="group-coordinator-service",
        service_instance_id="group-coordinator-instance",
        service_scope_ref="roll-exit-scope-a",
        service_registration_id="risk-order-entry-registration",
        service_operation="orders.submit_group_roll_exit",
        service_port="service.order_entry",
        service_registration_digest="6" * 64,
        service_epoch="service-epoch-3",
        service_policy_version="policy-7",
        service_requirements_version="requirements-4",
        service_write_run_id="strategy-run-202612",
    )
    request = GroupRollExitRequestV1(
        portfolio_ref=_PORTFOLIO,
        group_id=_GROUP_ID,
        source_leg_id=_SOURCE_LEG,
        limit_price=Decimal("102.25"),
    )
    return source, request


def _authorization() -> GroupRollExitAuthorizationV1:
    source, request = _source_and_request()
    action_id = group_roll_exit_action_id(source.digest, request.fingerprint)
    intent = OrderIntent.limit(
        intent_id=action_id,
        scope=source.source_execution_scope,
        signal_id=action_id,
        instrument=source.instrument,
        side=Side.SELL,
        quantity=source.remaining_quantity,
        price=request.limit_price,
        position_effect=PositionEffect.CLOSE,
        time_in_force=TimeInForce.GTC,
        reduce_only=True,
        metadata_version=source.close_metadata_version,
        deadline_ns=_DEADLINE_NS - 500,
        position_side=source.source_position_side,
        position_bucket=source.source_position_bucket,
    )
    return GroupRollExitAuthorizationV1.build(
        request=request,
        source=source,
        intent=intent,
        deadline_ns=intent.deadline_ns,
        risk_intent_id=_RISK_INTENT_ID,
    )


def _prepared_observation(
    authorization: GroupRollExitAuthorizationV1,
) -> GroupRollExitObservationV1:
    return GroupRollExitObservationV1(
        action_id=authorization.action_id,
        phase=GroupRollExitPhase.PREPARED,
        source_digest=authorization.source.digest,
        request_fingerprint=authorization.request.fingerprint,
        authorization_sha256=authorization.authorization_sha256,
        intent_id=authorization.intent.intent_id,
        close_state=None,
        requested_quantity=authorization.intent.quantity,
        filled_quantity=Decimal("0"),
        order_revision=None,
        close_receipt_sha256=None,
        detail_code=None,
        risk_intent_id=authorization.risk_intent_id,
    )


class _RiskQueryTransport:
    """Synthetic RPC leaf that delegates Risk state projection to production code."""

    def __init__(
        self,
        *,
        risk_owner: RiskGroupRollExitOwner,
        authorization: GroupRollExitAuthorizationV1,
        risk_row: dict[str, Any] | None,
        transform: Callable[[dict[str, Any]], dict[str, Any]] | None = None,
        use_missing_row_query: bool = False,
    ) -> None:
        self._risk_owner = risk_owner
        self._authorization = authorization
        self._risk_row = risk_row
        self._transform = transform
        self._use_missing_row_query = use_missing_row_query
        self.calls: list[tuple[str, str]] = []
        self.risk_response: dict[str, Any] | None = None

    def call(self, mount: str, operation: str, arguments: object, **kwargs: object) -> dict[str, Any]:
        assert mount == "upstream.group_roll_exit_order_query"
        assert operation == "orders.query_group_roll_exit"
        assert arguments == {
            "action_id": self._authorization.action_id,
            "authorization_sha256": self._authorization.authorization_sha256,
        }
        assert kwargs == {
            "account_ref": _ACCOUNT,
            "portfolio_ref": _PORTFOLIO,
            "scope_ref": _QUERY_SCOPE,
            "max_reply_message_bytes": 1_000_000,
        }
        self.calls.append((mount, operation))

        if self._use_missing_row_query:
            assert self._risk_row is None
            response = self._risk_owner._query(
                None,
                None,
                {
                    "action_id": self._authorization.action_id,
                    "authorization_sha256": self._authorization.authorization_sha256,
                },
            )
        else:
            assert self._risk_row is not None
            response = self._risk_owner._query_record(
                self._authorization,
                self._risk_row,
                None,
                None,
            )
        self.risk_response = response
        return response if self._transform is None else self._transform(response)


def _query_port(
    authorization: GroupRollExitAuthorizationV1,
    upstream: _RiskQueryTransport,
) -> GroupRollExitObservationV1 | None:
    target = SimpleNamespace(
        accounts={
            _ACCOUNT: SimpleNamespace(roll_exit_query_scope_ref=_QUERY_SCOPE),
        }
    )
    request = SimpleNamespace(account_refs=(_ACCOUNT,), portfolio_ref=_PORTFOLIO)
    current = _CoordinatorCall(
        registration=None,
        target_scope=None,
        target=target,
        binding=SimpleNamespace(request=request),
    )
    token = _CURRENT_COORDINATOR_CALL.set(current)
    try:
        return _OrdersRpcPort(_ACCOUNT, upstream).query_group_roll_exit(authorization)
    finally:
        _CURRENT_COORDINATOR_CALL.reset(token)


def _response_transform(
    *, phase: str | object = _MISSING, observation: object = _MISSING
) -> Callable[[dict[str, Any]], dict[str, Any]]:
    def transform(response: dict[str, Any]) -> dict[str, Any]:
        result = dict(response["result"])
        if phase is not _MISSING:
            result["phase"] = phase
        if observation is not _MISSING:
            result["observation"] = observation
        return {**response, "result": result}

    return transform


def _risk_row(
    tmp_path: Path, authorization: GroupRollExitAuthorizationV1
) -> tuple[RiskGroupRollExitOwner, RiskGroupRollExitActionStore, dict[str, Any]]:
    store = RiskGroupRollExitActionStore(tmp_path / "risk-roll-exit.sqlite3")
    row = store.prepare(authorization)
    owner = object.__new__(RiskGroupRollExitOwner)
    owner._store = store
    return owner, store, row


def test_prepared_null_projects_exact_authorization_and_read_only_query_preserves_unknown(
    tmp_path: Path,
) -> None:
    authorization = _authorization()
    risk_owner, risk_store, risk_row = _risk_row(tmp_path, authorization)
    assert risk_row["phase"] == "PREPARED"
    assert risk_row["observation_json"] is None
    risk_before = risk_store.get(authorization.action_id)

    local_store = GroupRollExitActionStore(tmp_path / "coordinator-roll-exit.sqlite3")
    try:
        local_store.prepare(authorization)
        assert local_store.claim_submit(
            authorization.action_id, authorization.authorization_sha256
        )
        unknown = GroupRollExitObservationV1(
            action_id=authorization.action_id,
            phase=GroupRollExitPhase.UNKNOWN,
            source_digest=authorization.source.digest,
            request_fingerprint=authorization.request.fingerprint,
            authorization_sha256=authorization.authorization_sha256,
            intent_id=authorization.intent.intent_id,
            close_state=ExecutionState.UNKNOWN,
            requested_quantity=authorization.intent.quantity,
            filled_quantity=Decimal("0"),
            order_revision=None,
            close_receipt_sha256=None,
            detail_code="RISK_SUBMIT_RESPONSE_UNAVAILABLE",
            risk_intent_id=authorization.risk_intent_id,
        )
        local_store.record_observation(unknown)
        local_before = local_store.get(authorization.action_id)
        assert local_before is not None
        assert local_before.phase is GroupRollExitPhase.UNKNOWN

        transport = _RiskQueryTransport(
            risk_owner=risk_owner,
            authorization=authorization,
            risk_row=risk_row,
        )
        projected = _query_port(authorization, transport)
        assert transport.risk_response is not None
        assert transport.risk_response["result"] == {
            "phase": "PREPARED",
            "observation": None,
        }
        assert type(projected) is GroupRollExitObservationV1
        assert projected == _prepared_observation(authorization)
        assert projected.action_id == authorization.action_id
        assert projected.source_digest == authorization.source.digest
        assert projected.request_fingerprint == authorization.request.fingerprint
        assert projected.authorization_sha256 == authorization.authorization_sha256
        assert projected.intent_id == authorization.intent.intent_id
        assert projected.risk_intent_id == authorization.risk_intent_id
        assert projected.requested_quantity == authorization.intent.quantity
        assert projected.close_state is None
        assert projected.filled_quantity == 0
        assert projected.order_revision is None
        assert projected.close_receipt_sha256 is None
        assert projected.detail_code is None
        assert transport.calls == [
            ("upstream.group_roll_exit_order_query", "orders.query_group_roll_exit")
        ]

        # This is the read-only query projection. Explicit recovery is a separate
        # API: for exact durable STARTED + Risk PREPARED state it may call submit,
        # with Risk's persisted CAS enforcing at-most-once dispatch.
        assert _roll_exit_query_observation(local_before, projected) == unknown
        assert local_store.get(authorization.action_id) == local_before
        assert risk_store.get(authorization.action_id) == risk_before
    finally:
        local_store.close()


def test_prepared_typed_observation_still_uses_existing_identity_validation(
    tmp_path: Path,
) -> None:
    authorization = _authorization()
    risk_owner, _risk_store, risk_row = _risk_row(tmp_path, authorization)
    prepared = _prepared_observation(authorization)
    transport = _RiskQueryTransport(
        risk_owner=risk_owner,
        authorization=authorization,
        risk_row=risk_row,
        transform=_response_transform(observation=prepared.to_payload()),
    )

    assert _query_port(authorization, transport) == prepared


def test_not_found_null_remains_none_from_risk_query(tmp_path: Path) -> None:
    authorization = _authorization()
    missing_store = RiskGroupRollExitActionStore(tmp_path / "missing-risk.sqlite3")
    risk_owner = object.__new__(RiskGroupRollExitOwner)
    risk_owner._store = missing_store
    transport = _RiskQueryTransport(
        risk_owner=risk_owner,
        authorization=authorization,
        risk_row=None,
        use_missing_row_query=True,
    )

    assert _query_port(authorization, transport) is None


def test_nonprepared_null_observation_is_rejected(tmp_path: Path) -> None:
    authorization = _authorization()
    risk_owner, _risk_store, risk_row = _risk_row(tmp_path, authorization)
    transport = _RiskQueryTransport(
        risk_owner=risk_owner,
        authorization=authorization,
        risk_row=risk_row,
        transform=_response_transform(phase="UNKNOWN", observation=None),
    )

    with pytest.raises(RuntimeError, match="roll-exit query identity is inconsistent"):
        _query_port(authorization, transport)


def test_prepared_observation_with_wrong_identity_is_rejected(tmp_path: Path) -> None:
    authorization = _authorization()
    risk_owner, _risk_store, risk_row = _risk_row(tmp_path, authorization)
    wrong_identity = replace(_prepared_observation(authorization), action_id="different-action")
    transport = _RiskQueryTransport(
        risk_owner=risk_owner,
        authorization=authorization,
        risk_row=risk_row,
        transform=_response_transform(observation=wrong_identity.to_payload()),
    )

    with pytest.raises(RuntimeError, match="roll-exit query identity is inconsistent"):
        _query_port(authorization, transport)


def test_invalid_query_phase_is_rejected(tmp_path: Path) -> None:
    authorization = _authorization()
    risk_owner, _risk_store, risk_row = _risk_row(tmp_path, authorization)
    transport = _RiskQueryTransport(
        risk_owner=risk_owner,
        authorization=authorization,
        risk_row=risk_row,
        transform=_response_transform(phase="NOT_A_PHASE", observation=None),
    )

    with pytest.raises(RuntimeError, match="roll-exit query reply is invalid"):
        _query_port(authorization, transport)
