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
from bt_api_execution import (
    ExecutionState,
    GroupAccountReservationSnapshot,
    GroupChildOrderStatus,
    GroupLegOrderReceipt,
    GroupRiskLegSnapshot,
)
from bt_api_execution.group_order_contracts import GroupReservationPhase

from bt_api_py.service_execution_group import (
    _optional_order_receipt_response,
    _order_receipt_response,
    _rpc_result,
    _snapshot_response,
)


_OWNER = WireIdentity(WireRole.SERVICE, "owner-principal", "owner-service")
_SCOPE = WireScope(
    "paper-domain",
    account_ref="account-a",
    strategy_ref="strategy-a",
    portfolio_ref="portfolio-a",
)


def _runtime_call_result(service_result: dict[str, object]):
    """Model the payload returned by call() after a real wire reply roundtrip."""

    reply = WireEnvelope(
        WireHeader(
            protocol_version=2,
            kind=WireMessageKind.REPLY,
            message_id="reply-a",
            correlation_id="request-a",
            identity=_OWNER,
            scope=_SCOPE,
            operation="reservations.reserve",
            schema_id="sdk.service.call",
            schema_version=1,
            sent_at_utc="2026-10-16T00:00:00Z",
        ),
        {"ok": True, "code": "OK", "result": service_result},
    )
    decoded = decode_envelope(encode_envelope(reply))
    # ServiceClientRuntime._rpc returns the outer payload's result mapping.
    return decoded.payload["result"]


def _active_reservation() -> GroupAccountReservationSnapshot:
    return GroupAccountReservationSnapshot(
        group_id="group-a",
        account_ref="account-a",
        scope_fingerprint="a" * 64,
        phase=GroupReservationPhase.ACTIVE,
        group_fingerprint="b" * 64,
        policy_fingerprint="c" * 64,
        deadline_ns=1_000_000,
        legs=(
            GroupRiskLegSnapshot(
                leg_id="leg-a",
                permit_id="permit-a",
                intent_id="intent-a",
                reserved_intent_hash="d" * 64,
                exposure_profile_fingerprint="e" * 64,
                actual_intent_hash=None,
                state="ACTIVE",
                claimed=False,
                resolved=False,
                expires_at_ns=1_000_000,
            ),
        ),
    )


def test_reservation_snapshot_decodes_real_frozen_wire_legs() -> None:
    reservation = _active_reservation()
    service_result = {
        "ok": True,
        "code": "OK",
        "result": reservation.to_payload(),
    }
    runtime_result = _runtime_call_result(service_result)
    assert type(runtime_result["result"]["legs"]) is tuple

    assert _snapshot_response(runtime_result) == reservation


def test_order_receipt_decodes_frozen_wire_mapping_and_preserves_fees() -> None:
    receipt = GroupLegOrderReceipt(
        status=GroupChildOrderStatus(
            intent_id="intent-a",
            state=ExecutionState.PARTIALLY_FILLED,
            filled_quantity=Decimal("1.25"),
            provider_order_id="provider-order-a",
            average_price=Decimal("100.50"),
            cumulative_commission=Decimal("0.12"),
            cumulative_commission_currency="USD",
            order_revision=1,
            trade_count=1,
        )
    )
    service_result = {
        "ok": True,
        "code": "OK",
        "result": receipt.to_payload(),
    }

    assert _order_receipt_response(_runtime_call_result(service_result)) == receipt


def test_optional_order_query_preserves_wire_null() -> None:
    service_result = {"ok": True, "code": "OK", "result": None}

    assert _optional_order_receipt_response(_runtime_call_result(service_result)) is None


def test_reservation_decoder_still_rejects_unknown_dto_fields() -> None:
    payload = _active_reservation().to_payload()
    payload["unexpected"] = "field"
    service_result = {"ok": True, "code": "OK", "result": payload}

    with pytest.raises(RuntimeError, match="account reservation receipt is invalid"):
        _snapshot_response(_runtime_call_result(service_result))


def test_rpc_result_rejects_non_json_values_from_an_invalid_adapter() -> None:
    response = {"ok": True, "code": "OK", "result": {"invalid": object()}}

    with pytest.raises(RuntimeError, match="group RPC result is invalid"):
        _rpc_result(response)


def test_rpc_result_rejects_values_beyond_the_wire_depth_limit() -> None:
    nested: object = "leaf"
    for _ in range(65):
        nested = {"nested": nested}
    response = {"ok": True, "code": "OK", "result": {"payload": nested}}

    with pytest.raises(RuntimeError, match="group RPC result is invalid"):
        _rpc_result(response)
