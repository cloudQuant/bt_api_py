from __future__ import annotations

from decimal import Decimal

import pytest
from bt_api_risk import AccountScope

from bt_api_py._contracts.models import OrderRequest, OrderType, Side
from bt_api_py._normalization import (
    NormalizedRecord,
    NormalizedRecords,
    _order_source_provenance,
)
from bt_api_py.service_risk_order_entry import (
    RiskBtApiProviderContract,
    RiskOrderEntryError,
    _v2_order_facts,
)
from bt_api_py.service_risk_order_journal import RiskOrderCommand


_ACCOUNT_ID = "provider-account-a"
_CLIENT_ID = "multi-fill-client"
_INSTRUMENT = "BTC-USD"
_ORDER_ID = "provider-order-1"
_QUANTITY = Decimal("2")
_PRICE = Decimal("10")


def _contract() -> RiskBtApiProviderContract:
    return RiskBtApiProviderContract(
        exchange_name="BINANCE___MULTI_FILL_TEST",
        profile_id="multi-fill-v2",
        order_schema="multi-fill-order-v2",
        fill_schema="multi-fill-history-v2",
        client_order_id_query=True,
        client_order_id_query_unique=True,
        complete_order_snapshot=True,
        complete_fill_history=True,
        provider_accepts_client_order_id=True,
        provider_status_mapping=(
            ("accepted", "accepted"),
            ("canceled", "canceled"),
            ("expired", "expired"),
            ("filled", "completed"),
            ("open", "accepted"),
            ("partial", "partial"),
            ("rejected", "rejected"),
            ("unknown", "unknown"),
        ),
        supported_order_types=("LIMIT",),
        supported_time_in_force=("GTC",),
        supported_position_effects=("OPEN",),
        normalizer_schema="bt-api-order-source-provenance-v2",
        order_revision_field="order_revision",
        fill_revision_field="fill_revision",
        fill_watermark_field="fill_watermark",
        fee_amount_field="fee_amount",
        fee_currency_field="fee_currency",
        quote_currency_by_instrument=((_INSTRUMENT, "USD"),),
    )


def _observations(
    fill_rows: list[dict[str, str]], *, reported_filled: Decimal | None = None
):
    filled = (
        sum((Decimal(row["quantity"]) for row in fill_rows), Decimal(0))
        if reported_filled is None
        else reported_filled
    )
    order_source = {
        "account_id": _ACCOUNT_ID,
        "symbol": _INSTRUMENT,
        "side": "buy",
        "order_id": _ORDER_ID,
        "client_order_id": _CLIENT_ID,
        "status": "filled",
        "filled_quantity": format(filled, "f"),
        "quantity": format(_QUANTITY, "f"),
        "price": format(_PRICE, "f"),
        "order_revision": 4,
    }
    order_envelope = {
        "schema_version": "multi-fill-order-v2",
        "snapshot_complete": True,
        "watermark": "order-watermark-4",
        "client_order_id": _CLIENT_ID,
        "account_id": _ACCOUNT_ID,
    }
    order_provenance = _order_source_provenance(
        order_envelope, "query_order", order_source
    )
    order = NormalizedRecord(
        {
            "account_id": _ACCOUNT_ID,
            "client_order_id": _CLIENT_ID,
            "order_id": _ORDER_ID,
            "symbol": _INSTRUMENT,
            "side": "buy",
            "status": "completed",
            "quantity": _QUANTITY,
            "filled": filled,
            "price": _PRICE,
            "execution_unknown": False,
        },
        provenance=order_provenance,
        source_row_count=1,
        source_alignment_valid=True,
        collection_provenance=_order_source_provenance(
            order_envelope, "query_order", order_source
        ),
    )

    fill_envelope = {
        "schema_version": "multi-fill-history-v2",
        "history_complete": True,
        "fill_watermark": 12,
        "client_order_id": _CLIENT_ID,
        "account_id": _ACCOUNT_ID,
    }
    normalized_fills = []
    row_provenance = []
    for index, row in enumerate(fill_rows, start=1):
        source = {
            "trade_id": row["trade_id"],
            "order_id": _ORDER_ID,
            "client_order_id": _CLIENT_ID,
            "account_id": _ACCOUNT_ID,
            "symbol": _INSTRUMENT,
            "side": "buy",
            "quantity": row["quantity"],
            "trade_price": format(_PRICE, "f"),
            "fill_revision": index + 4,
            "fee_amount": row.get("fee_amount", "0"),
            "fee_currency": "USD",
        }
        row_provenance.append(
            _order_source_provenance(fill_envelope, "get_deals", source)
        )
        normalized_fills.append(
            {
                "trade_id": row["trade_id"],
                "order_id": _ORDER_ID,
                "client_order_id": _CLIENT_ID,
                "account_id": _ACCOUNT_ID,
                "symbol": _INSTRUMENT,
                "side": "buy",
                "size": Decimal(row["quantity"]),
                "price": _PRICE,
                "fee": Decimal(row.get("fee_amount", "0")),
                "fee_currency": "USD",
            }
        )
    fills = NormalizedRecords(
        normalized_fills,
        provenance=_order_source_provenance(fill_envelope, "get_deals"),
        row_provenance=tuple(row_provenance),
        source_alignment_valid=True,
    )

    command = RiskOrderCommand(
        scope=AccountScope("binance", _ACCOUNT_ID, "test"),
        principal_ref="principal-a",
        intent_id="multi-fill-intent",
        risk_intent_id="risk-multi-fill-intent",
        payload_sha256="a" * 64,
        canonical_payload={"position_effect": "OPEN", "reduce_only": False},
        wire_scope={},
        exchange_name="BINANCE___MULTI_FILL_TEST",
        provider_account_id=_ACCOUNT_ID,
        client_order_id=_CLIENT_ID,
        permit_id="permit-multi-fill",
        order_request={},
        order_request_sha256="b" * 64,
        created_revision=1,
        created_record_sha256="c" * 64,
        created_at_ns=1,
        updated_at_ns=2,
        state="UNKNOWN",
        latest_revision=3,
        latest_record_sha256="d" * 64,
        latest_event={},
    )
    request = OrderRequest(
        symbol=_INSTRUMENT,
        side=Side.BUY,
        order_type=OrderType.LIMIT,
        quantity=_QUANTITY,
        account_id=_ACCOUNT_ID,
        client_order_id=_CLIENT_ID,
        price=_PRICE,
        reduce_only=False,
    )
    return order, fills, command, request


def test_v2_order_facts_accumulates_multiple_distinct_fills():
    order, fills, command, request = _observations(
        [
            {"trade_id": "trade-a", "quantity": "0.75"},
            {"trade_id": "trade-b", "quantity": "1.25"},
        ]
    )

    facts = _v2_order_facts(
        order, fills, command=command, request=request, contract=_contract()
    )

    assert len(facts["fill_facts"]) == 2
    assert {item.trade_id for item in facts["fill_facts"]} == {"trade-a", "trade-b"}
    assert sum((item.quantity for item in facts["fill_facts"]), Decimal(0)) == _QUANTITY
    assert facts["filled_quantity"] == _QUANTITY


def test_v2_order_facts_rejects_duplicate_trade_ids_even_when_sum_matches():
    order, fills, command, request = _observations(
        [
            {"trade_id": "trade-duplicate", "quantity": "1"},
            {"trade_id": "trade-duplicate", "quantity": "1"},
        ]
    )

    with pytest.raises(RiskOrderEntryError, match="PROVIDER_FILL_HISTORY_MISMATCH"):
        _v2_order_facts(
            order, fills, command=command, request=request, contract=_contract()
        )


def test_v2_order_facts_rejects_fill_total_that_differs_from_order_filled():
    order, fills, command, request = _observations(
        [
            {"trade_id": "trade-a", "quantity": "1.2"},
            {"trade_id": "trade-b", "quantity": "1.1"},
        ],
        reported_filled=_QUANTITY,
    )

    with pytest.raises(RiskOrderEntryError, match="PROVIDER_FILL_HISTORY_MISMATCH"):
        _v2_order_facts(
            order, fills, command=command, request=request, contract=_contract()
        )
