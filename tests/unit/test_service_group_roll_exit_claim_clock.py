"""Boundary tests for Risk close receipts projected by DIM05 roll exit."""

from __future__ import annotations

from decimal import Decimal

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
from bt_api_execution import ExecutionState

from bt_api_py.service_group_roll_exit import (
    GroupRollExitCloseFeeEvidenceV1,
    GroupRollExitCloseFeeFillV1,
    GroupRollExitObservationV1,
    GroupRollExitPhase,
    _sha256,
)

_DIGEST = "a" * 64
_ACTION_ID = "dim05-action-1"
_RISK_INTENT_ID = "risk-close-intent-1"


def _close_receipt(claimed_at: object) -> dict[str, object]:
    receipt: dict[str, object] = {
        "status": "TERMINAL_CLOSE_RESOLVED",
        "claim": {
            "cause_id": "risk-close-cause-1",
            "claim_digest": "b" * 64,
            "claimed_at": claimed_at,
            "intent_hash": "c" * 64,
            "intent_id": _RISK_INTENT_ID,
            "permit_id": "risk-close-permit-1",
            "scope_key": '{"account_id":"account-a","environment":"paper","provider":"simnow"}',
        },
        "proof_sha256": "d" * 64,
        "exposure_proof_sha256": "e" * 64,
        "final_state": ExecutionState.FILLED.value,
        "requested_quantity": "1",
        "filled_quantity": "1",
        "trade_count": 1,
        "order_revision": 4,
        "journal_revision": 12,
        "exposure_reservation_id": "exposure-reservation-1",
        "exposure_reservation_sha256": "f" * 64,
        "dispatch_latch_active": False,
        "has_other_active_freezes": False,
        "position_effect": "CLOSE",
    }
    receipt["receipt_sha256"] = _sha256(receipt)
    return receipt


def _observation_payload(claimed_at: object) -> dict[str, object]:
    receipt = _close_receipt(claimed_at)
    fee = GroupRollExitCloseFeeEvidenceV1(
        intent_id=_ACTION_ID,
        journal_revision=12,
        fill_observation_sha256="1" * 64,
        fee_currency="USD",
        fee_amount=Decimal("-0.05"),
        fills=(
            GroupRollExitCloseFeeFillV1(
                fill_id="risk-fill-1",
                fill_fingerprint="2" * 64,
                source_row_sha256="3" * 64,
                amount=Decimal("-0.05"),
                currency="USD",
            ),
        ),
    )
    return {
        "schema": "group-roll-exit-observation-v1",
        "action_id": _ACTION_ID,
        "phase": GroupRollExitPhase.RESOLVED.value,
        "source_digest": _DIGEST,
        "request_fingerprint": "4" * 64,
        "authorization_sha256": "5" * 64,
        "intent_id": _ACTION_ID,
        "close_state": ExecutionState.FILLED.value,
        "requested_quantity": "1",
        "filled_quantity": "1",
        "order_revision": 4,
        "close_receipt_sha256": receipt["receipt_sha256"],
        "detail_code": None,
        "risk_intent_id": _RISK_INTENT_ID,
        "close_receipt": receipt,
        "close_fee_evidence": fee.to_payload(),
    }


def _wire_observation(payload: dict[str, object]) -> object:
    envelope = WireEnvelope(
        WireHeader(
            protocol_version=2,
            kind=WireMessageKind.REPLY,
            message_id="dim05-close-observation-1",
            correlation_id="dim05-close-request-1",
            identity=WireIdentity(WireRole.SERVICE, "risk-owner", "risk-owner-1"),
            scope=WireScope(
                "simnow:paper",
                account_ref="account-a",
                strategy_ref="strategy-a",
                run_id="run-a",
            ),
            operation="orders.query_group_roll_exit",
            schema_id="sdk.service.reply",
            schema_version=1,
            sent_at_utc="2026-10-17T12:00:00Z",
        ),
        {"observation": payload},
    )
    decoded = decode_envelope(encode_envelope(envelope))
    return decoded.payload["observation"]


def test_finite_float_claim_timestamp_roundtrips_in_risk_close_receipt() -> None:
    wire_payload = _wire_observation(_observation_payload(1234.5))

    observation = GroupRollExitObservationV1.from_payload(wire_payload)

    claimed_at = observation.close_receipt["claim"]["claimed_at"]
    assert type(claimed_at) is float
    assert claimed_at == 1234.5
    assert observation.risk_intent_id == _RISK_INTENT_ID
    assert observation.close_fee_evidence.intent_id == observation.intent_id


@pytest.mark.parametrize("claimed_at", [True, False])
def test_boolean_claim_timestamp_is_not_an_integer_timestamp(claimed_at: bool) -> None:
    payload = _observation_payload(claimed_at)

    with pytest.raises(ValueError, match="invalid Risk CLOSE claim timestamp"):
        GroupRollExitObservationV1.from_payload(payload)


@pytest.mark.parametrize("claimed_at", [float("nan"), float("inf"), float("-inf")])
def test_nonfinite_claim_timestamp_is_rejected(claimed_at: float) -> None:
    payload = _observation_payload(1.0)
    receipt = payload["close_receipt"]
    receipt["claim"]["claimed_at"] = claimed_at

    with pytest.raises(ValueError, match="non-JSON value"):
        GroupRollExitObservationV1.from_payload(payload)
