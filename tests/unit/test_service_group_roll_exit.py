"""Focused source-bound contract and restart tests for DIM05 roll-exit."""

from __future__ import annotations

from dataclasses import replace
from decimal import Decimal
from pathlib import Path

import pytest
from bt_api_base.service_protocol import (
    WireEnvelope,
    WireHeader,
    WireIdentity,
    WireMessageKind,
    WireRole,
    WireScope,
    decode_envelope,
    encode_envelope,
)
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
    SqliteOrderGroupStore,
    TimeInForce,
)
from bt_api_execution.group_contracts import group_leg_intent_id

from bt_api_py.service_group_roll_exit import (
    GroupRollExitActionStore,
    GroupRollExitAuthorizationV1,
    GroupRollExitError,
    GroupRollExitObservationV1,
    GroupRollExitPhase,
    GroupRollExitPriorCloseV1,
    GroupRollExitRequestV1,
    GroupRollExitSourceFactsV1,
    group_roll_exit_action_id,
    group_roll_exit_client_order_id,
)
from bt_api_py.service_execution_group_roll_exit_source import (
    GroupRollExitAuthorizationSourceError,
    _group_event_chain_digest,
)

_DEADLINE_NS = 1_900_000_000_000_000_100
_PORTFOLIO = "portfolio-dim05"
_GROUP_ID = "group-dim05-roll-1"
_SOURCE_LEG = "old-contract"
_ACCOUNT = "account-a"
_STRATEGY = "strategy-a"
_INSTRUMENT = "BTC-USD-202612-FUT"


def _source_and_request(
    *,
    prior: tuple[GroupRollExitPriorCloseV1, ...] = (),
    remaining: Decimal = Decimal("1.5"),
    source_quantity: Decimal = Decimal("2"),
    source_child_quantity: Decimal | None = None,
    source_filled: Decimal = Decimal("1.5"),
    source_step: Decimal = Decimal("0.5"),
    close_step: Decimal = Decimal("0.5"),
) -> tuple[GroupRollExitSourceFactsV1, GroupRollExitRequestV1]:
    child_quantity = source_quantity if source_child_quantity is None else source_child_quantity
    scope = ExecutionScope("simnow", "paper", _ACCOUNT, _STRATEGY, "20261215")
    old_intent = OrderIntent.limit(
        intent_id=group_leg_intent_id(_GROUP_ID, _SOURCE_LEG),
        scope=scope,
        signal_id="signal-old-contract",
        instrument=_INSTRUMENT,
        side=Side.BUY,
        quantity=source_quantity,
        price=Decimal("100"),
        time_in_force=TimeInForce.IOC,
        metadata_version="source-meta-7",
        deadline_ns=_DEADLINE_NS,
    )
    source_child_intent = replace(old_intent, quantity=child_quantity)
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
                quantity_step=source_step,
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
    risk_reservation_fingerprint = "7" * 64
    risk_intent_fingerprint = "9" * 64
    permit = GroupLegPermitReference(
        group_id=_GROUP_ID,
        leg_id=_SOURCE_LEG,
        portfolio_ref=_PORTFOLIO,
        strategy_id=_STRATEGY,
        account_ref=_ACCOUNT,
        coordinator_principal_ref="group-coordinator",
        risk_scope_fingerprint="a" * 64,
        permit_id="risk-permit-old-leg",
        group_fingerprint=risk_reservation_fingerprint,
        policy_fingerprint="b" * 64,
        reserved_intent_hash=old_intent.fingerprint,
        exposure_profile_fingerprint="c" * 64,
        order_intent_hash=source_child_intent.fingerprint,
        deadline_ns=_DEADLINE_NS,
    )
    claim = GroupLegClaimReceipt(
        permit=permit,
        actual_child_intent_hash=risk_intent_fingerprint,
        claim_digest="e" * 64,
        actual_quantity=child_quantity,
        actual_notional=child_quantity * Decimal("100"),
    )
    resolution = GroupDispatchResolutionReceipt(
        claim=claim,
        resolution_kind="TERMINAL_EXPOSURE_RESOLVED",
        proof_sha256="f" * 64,
        final_state=ExecutionState.CANCELLED,
        requested_quantity=child_quantity,
        filled_quantity=source_filled,
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
        filled_quantity=source_filled,
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
        source_requested_quantity=source_quantity,
        source_claim=claim,
        source_risk_resolution=resolution,
        source_event_digest="3" * 64,
        source_side=Side.BUY,
        instrument=_INSTRUMENT,
        source_metadata_version="source-meta-7",
        close_metadata_version="close-meta-11",
        close_metadata_digest="4" * 64,
        close_quantity_step=close_step,
        close_tick_size=Decimal("0.25"),
        close_metadata_valid_until_ns=_DEADLINE_NS + 100_000,
        source_position_side="NET",
        source_position_bucket="ALL",
        source_filled_quantity=source_filled,
        prior_resolved_closes=prior,
        remaining_quantity=remaining,
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


def test_group_roll_exit_rejects_close_origin_source_leg() -> None:
    source, _request = _source_and_request()
    close_child = replace(
        source.source_child_intent,
        position_effect=PositionEffect.CLOSE,
        reduce_only=True,
    )
    with pytest.raises(ValueError, match="accepted group request"):
        replace(source, source_child_intent=close_child)


def test_group_roll_exit_binds_permit_to_scaled_child_not_group_maximum() -> None:
    source, _request = _source_and_request(
        source_quantity=Decimal("2"),
        source_child_quantity=Decimal("1.5"),
        source_filled=Decimal("1"),
        remaining=Decimal("1"),
    )
    accepted = GroupRollExitSourceFactsV1.from_payload(source.to_payload())
    maximum = accepted.group_request.leg(_SOURCE_LEG).maximum_intent
    assert maximum.quantity == Decimal("2")
    assert accepted.source_child_intent.quantity == Decimal("1.5")
    assert accepted.source_terminal_status.filled_quantity == Decimal("1")
    assert accepted.source_claim.permit.order_intent_hash == accepted.source_child_intent.fingerprint
    assert accepted.source_claim.permit.order_intent_hash != maximum.fingerprint

    wrong_permit = replace(
        accepted.source_claim.permit,
        order_intent_hash=maximum.fingerprint,
    )
    wrong_claim = replace(accepted.source_claim, permit=wrong_permit)
    wrong_resolution = replace(accepted.source_risk_resolution, claim=wrong_claim)
    with pytest.raises(ValueError, match="source Risk permit"):
        replace(
            accepted,
            source_claim=wrong_claim,
            source_risk_resolution=wrong_resolution,
        )


def test_group_roll_exit_event_chain_accepts_global_sqlite_sequences(tmp_path: Path) -> None:
    source, _request = _source_and_request()
    store = SqliteOrderGroupStore(tmp_path / "groups.sqlite3")
    try:
        store.create_or_get(source.group_request, now_ns=_DEADLINE_NS - 10_000)
        second_group_id = "group-dim05-roll-sequence-2"
        second_legs = tuple(
            replace(
                plan,
                maximum_intent=replace(
                    plan.maximum_intent,
                    intent_id=group_leg_intent_id(second_group_id, plan.leg_id),
                ),
            )
            for plan in source.group_request.legs
        )
        second_request = replace(
            source.group_request,
            group_id=second_group_id,
            first_leg_id=second_legs[0].leg_id,
            legs=second_legs,
        )
        store.create_or_get(second_request, now_ns=_DEADLINE_NS - 9_000)
        snapshot = store.get(_PORTFOLIO, second_group_id)
        events = store.events(_PORTFOLIO, second_group_id)

        assert snapshot is not None
        assert len(events) == snapshot.revision + 1
        assert events[0].sequence > 1
        digest = _group_event_chain_digest(events, snapshot)
        assert len(digest) == 64

        invalid = (replace(events[0], sequence=0), *events[1:])
        with pytest.raises(GroupRollExitAuthorizationSourceError):
            _group_event_chain_digest(invalid, snapshot)
    finally:
        store.close()


def test_group_roll_exit_remaining_quantity_uses_exact_decimal_arithmetic() -> None:
    source, request = _source_and_request(
        source_quantity=Decimal("2E28"),
        source_filled=Decimal("1E28"),
        remaining=Decimal("1E28"),
        source_step=Decimal("2E28"),
        close_step=Decimal("1E28"),
    )
    action_id = group_roll_exit_action_id(source.digest, request.fingerprint)
    source_with_prior = replace(
        source,
        prior_resolved_closes=(
            GroupRollExitPriorCloseV1(
                action_id=action_id,
                intent_id=action_id,
                client_order_id=group_roll_exit_client_order_id(action_id),
                source_digest=source.digest,
                request_fingerprint=request.fingerprint,
                authorization_sha256="a" * 64,
                receipt_sha256="b" * 64,
                resolution_sha256="c" * 64,
                closing_side=Side.SELL,
                instrument=source.instrument,
                metadata_version=source.close_metadata_version,
                requested_quantity=Decimal("0.1"),
                filled_quantity=Decimal("0.1"),
                terminal_state=ExecutionState.FILLED,
                order_revision=1,
            ),
        ),
        remaining_quantity=Decimal("9999999999999999999999999999.9"),
        close_quantity_step=Decimal("0.1"),
    )
    with pytest.raises(ValueError, match="remaining source exposure"):
        replace(source_with_prior, remaining_quantity=Decimal("1E28"))


def _authorization(
    source: GroupRollExitSourceFactsV1,
    request: GroupRollExitRequestV1,
) -> GroupRollExitAuthorizationV1:
    action_id = group_roll_exit_action_id(source.digest, request.fingerprint)
    close_intent = OrderIntent.limit(
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
        intent=close_intent,
        deadline_ns=close_intent.deadline_ns,
    )


def _observation(
    authorization: GroupRollExitAuthorizationV1,
    *,
    phase: GroupRollExitPhase,
    state: ExecutionState | None,
    filled: Decimal,
    revision: int | None = None,
    receipt: str | None = None,
    detail: str | None = None,
) -> GroupRollExitObservationV1:
    return GroupRollExitObservationV1(
        action_id=authorization.action_id,
        phase=phase,
        source_digest=authorization.source.digest,
        request_fingerprint=authorization.request.fingerprint,
        authorization_sha256=authorization.authorization_sha256,
        intent_id=authorization.intent.intent_id,
        close_state=state,
        requested_quantity=authorization.intent.quantity,
        filled_quantity=filled,
        order_revision=revision,
        close_receipt_sha256=receipt,
        detail_code=detail,
    )


def test_group_roll_exit_authorization_roundtrips_exact_source_and_derived_ids() -> None:
    source, request = _source_and_request()
    authorization = _authorization(source, request)

    restored = GroupRollExitAuthorizationV1.from_payload(authorization.to_payload())

    assert restored == authorization
    assert restored.action_id.startswith("dim05-roll-")
    assert restored.client_order_id.startswith("dim05-client-")
    assert restored.intent.intent_id == restored.action_id
    assert restored.intent.time_in_force is TimeInForce.GTC
    assert restored.intent.tags == {}
    assert restored.source.group_request.fingerprint == source.group_request.fingerprint
    assert restored.source.source_child_intent == source.source_child_intent
    assert restored.source.source_claim == source.source_claim
    assert restored.source.source_risk_resolution == source.source_risk_resolution
    assert restored.source.source_risk_reservation_fingerprint == "7" * 64
    assert restored.source.source_risk_intent_fingerprint == "9" * 64
    with pytest.raises(ValueError, match="source claim/resolution proof"):
        replace(source, source_risk_intent_fingerprint="8" * 64)
    with pytest.raises(ValueError, match="source Risk permit"):
        replace(source, source_risk_reservation_fingerprint="6" * 64)

    tampered = authorization.to_payload()
    tampered["client_order_id"] = "dim05-client-forged"
    with pytest.raises(ValueError, match="client_order_id|authorization"):
        GroupRollExitAuthorizationV1.from_payload(tampered)


def test_group_roll_exit_authorization_parses_real_wire_frozen_arrays() -> None:
    source, request = _source_and_request(prior=())
    authorization = _authorization(source, request)
    envelope = WireEnvelope(
        WireHeader(
            protocol_version=2,
            kind=WireMessageKind.REQUEST,
            message_id="roll-exit-wire-1",
            correlation_id=None,
            identity=WireIdentity(
                WireRole.SERVICE,
                source.service_principal_ref,
                source.service_instance_id,
            ),
            scope=WireScope(
                "simnow:paper",
                account_ref=source.account_ref,
                strategy_ref=source.strategy_id,
                run_id=source.origin_run_id,
            ),
            operation="orders.submit_group_roll_exit",
            schema_id="sdk.service.call",
            schema_version=1,
            sent_at_utc="2026-10-17T12:00:00Z",
        ),
        {"arguments": {"authorization": authorization.to_payload()}},
    )

    decoded = decode_envelope(encode_envelope(envelope))
    frozen_arguments = decoded.payload["arguments"]
    frozen_authorization = frozen_arguments["authorization"]
    assert type(frozen_authorization["source"]["prior_resolved_closes"]) is tuple
    assert type(frozen_authorization["source"]["group_request"]["legs"]) is tuple
    restored = GroupRollExitAuthorizationV1.from_payload(frozen_authorization)

    assert restored == authorization
    assert restored.source.prior_resolved_closes == ()
    assert len(restored.source.group_request.legs) == 2


def test_group_roll_exit_store_is_idempotent_and_never_resubmits_after_restart(
    tmp_path: Path,
) -> None:
    source, request = _source_and_request()
    authorization = _authorization(source, request)
    path = tmp_path / "roll-exit.sqlite"
    store = GroupRollExitActionStore(path)
    try:
        prepared = store.prepare(authorization)
        assert prepared.phase is GroupRollExitPhase.PREPARED
        assert store.prepare(authorization) == prepared
        assert store.claim_submit(authorization.action_id, authorization.authorization_sha256)
        assert not store.claim_submit(authorization.action_id, authorization.authorization_sha256)

        changed_request = replace(request, limit_price=Decimal("102.50"))
        changed_authorization = _authorization(source, changed_request)
        with pytest.raises(GroupRollExitError, match="requires query/recovery"):
            store.prepare(changed_authorization)
    finally:
        store.close()

    recovered = GroupRollExitActionStore(path)
    try:
        record = recovered.get(authorization.action_id)
        assert record is not None
        assert record.phase is GroupRollExitPhase.SUBMIT_STARTED
        assert record.observation is None
        assert not recovered.claim_submit(
            authorization.action_id, authorization.authorization_sha256
        )
    finally:
        recovered.close()


def test_roll_exit_terminal_receipts_and_observations_reject_inconsistent_fills() -> None:
    source, request = _source_and_request()
    authorization = _authorization(source, request)

    with pytest.raises(ValueError, match="terminal state conflicts"):
        GroupRollExitPriorCloseV1(
            action_id=authorization.action_id,
            intent_id=authorization.action_id,
            client_order_id=authorization.client_order_id,
            source_digest=authorization.source.digest,
            request_fingerprint=authorization.request.fingerprint,
            authorization_sha256=authorization.authorization_sha256,
            receipt_sha256="9" * 64,
            resolution_sha256="8" * 64,
            closing_side=Side.SELL,
            instrument=source.instrument,
            metadata_version=source.close_metadata_version,
            requested_quantity=Decimal("1.5"),
            filled_quantity=Decimal("0.5"),
            terminal_state=ExecutionState.FILLED,
            order_revision=8,
        )

    with pytest.raises(ValueError, match="phase conflicts"):
        _observation(
            authorization,
            phase=GroupRollExitPhase.UNKNOWN,
            state=ExecutionState.ACKED,
            filled=Decimal("0"),
            revision=9,
        )

    with pytest.raises(ValueError, match="NOT_FOUND cannot be persisted"):
        _observation(
            authorization,
            phase=GroupRollExitPhase.NOT_FOUND,
            state=None,
            filled=Decimal("0"),
        )

    assert group_roll_exit_client_order_id(authorization.action_id) == authorization.client_order_id

def test_group_roll_exit_unknown_blocks_new_price_until_terminal_close_proof(
    tmp_path: Path,
) -> None:
    source, request = _source_and_request()
    authorization = _authorization(source, request)
    store = GroupRollExitActionStore(tmp_path / "roll-exit-unknown.sqlite")
    try:
        store.prepare(authorization)
        assert store.claim_submit(authorization.action_id, authorization.authorization_sha256)
        unknown = store.record_observation(
            _observation(
                authorization,
                phase=GroupRollExitPhase.UNKNOWN,
                state=ExecutionState.UNKNOWN,
                filled=Decimal("0"),
                detail="CLOSE_STATUS_UNKNOWN",
            )
        )
        assert unknown.phase is GroupRollExitPhase.UNKNOWN
        assert not store.claim_submit(authorization.action_id, authorization.authorization_sha256)

        other_price = _authorization(
            source, replace(request, limit_price=Decimal("102.50"))
        )
        with pytest.raises(GroupRollExitError, match="requires query/recovery"):
            store.prepare(other_price)

        resolved = store.record_observation(
            _observation(
                authorization,
                phase=GroupRollExitPhase.RESOLVED,
                state=ExecutionState.CANCELLED,
                filled=Decimal("0.5"),
                revision=7,
                receipt="7" * 64,
            )
        )
        assert resolved.phase is GroupRollExitPhase.RESOLVED
        assert resolved.observation is not None
        assert resolved.observation.filled_quantity == Decimal("0.5")

        prior = GroupRollExitPriorCloseV1(
            action_id=authorization.action_id,
            intent_id=authorization.intent.intent_id,
            client_order_id=authorization.client_order_id,
            source_digest=authorization.source.digest,
            request_fingerprint=authorization.request.fingerprint,
            authorization_sha256=authorization.authorization_sha256,
            receipt_sha256="7" * 64,
            resolution_sha256="8" * 64,
            closing_side=Side.SELL,
            instrument=source.instrument,
            metadata_version=source.close_metadata_version,
            requested_quantity=Decimal("1.5"),
            filled_quantity=Decimal("0.5"),
            terminal_state=ExecutionState.CANCELLED,
            order_revision=7,
        )
        updated_source, _ = _source_and_request(
            prior=(prior,), remaining=Decimal("1.0")
        )
        assert updated_source.remaining_quantity == Decimal("1.0")
        with pytest.raises(ValueError, match="remaining source exposure"):
            _source_and_request(prior=(prior,), remaining=Decimal("1.5"))

        next_authorization = _authorization(updated_source, request)
        assert next_authorization.action_id != authorization.action_id
        assert next_authorization.intent.quantity == Decimal("1.0")
        next_record = store.prepare(next_authorization)
        assert next_record.phase is GroupRollExitPhase.PREPARED
    finally:
        store.close()


def test_group_roll_exit_sidecar_rejects_stale_status_and_preserves_fill_on_unknown(
    tmp_path: Path,
) -> None:
    source, request = _source_and_request()
    authorization = _authorization(source, request)
    store = GroupRollExitActionStore(tmp_path / "roll-exit-stale.sqlite")
    try:
        store.prepare(authorization)
        assert store.claim_submit(
            authorization.action_id, authorization.authorization_sha256
        )
        partial = store.record_observation(
            _observation(
                authorization,
                phase=GroupRollExitPhase.STATUS_OBSERVED,
                state=ExecutionState.PARTIALLY_FILLED,
                filled=Decimal("0.5"),
                revision=9,
            )
        )
        assert partial.observation is not None

        with pytest.raises(GroupRollExitError, match="revision regressed"):
            store.record_observation(
                _observation(
                    authorization,
                    phase=GroupRollExitPhase.STATUS_OBSERVED,
                    state=ExecutionState.PARTIALLY_FILLED,
                    filled=Decimal("0.75"),
                    revision=8,
                )
            )
        with pytest.raises(GroupRollExitError, match="filled quantity regressed"):
            store.record_observation(
                _observation(
                    authorization,
                    phase=GroupRollExitPhase.STATUS_OBSERVED,
                    state=ExecutionState.PARTIALLY_FILLED,
                    filled=Decimal("0.25"),
                    revision=10,
                )
            )

        unknown = store.record_observation(
            _observation(
                authorization,
                phase=GroupRollExitPhase.UNKNOWN,
                state=ExecutionState.UNKNOWN,
                filled=Decimal("0"),
                detail="RISK_QUERY_UNAVAILABLE",
            )
        )
        assert unknown.observation is not None
        assert unknown.observation.filled_quantity == Decimal("0.5")
        assert unknown.observation.order_revision == 9

        with pytest.raises(GroupRollExitError, match="filled quantity regressed"):
            store.record_observation(
                _observation(
                    authorization,
                    phase=GroupRollExitPhase.SUBMIT_STARTED,
                    state=ExecutionState.DISPATCHING,
                    filled=Decimal("0"),
                )
            )

        with pytest.raises(GroupRollExitError, match="revision regressed"):
            store.record_observation(
                _observation(
                    authorization,
                    phase=GroupRollExitPhase.STATUS_OBSERVED,
                    state=ExecutionState.PARTIALLY_FILLED,
                    filled=Decimal("0.75"),
                    revision=8,
                )
            )
    finally:
        store.close()
