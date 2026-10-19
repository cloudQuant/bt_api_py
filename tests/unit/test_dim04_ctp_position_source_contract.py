from __future__ import annotations

import pytest
from bt_api_ctp.containers.ctp.ctp_order import CtpOrderData
from bt_api_ctp.containers.ctp.ctp_trade import CtpTradeData
from bt_api_ctp.gateway import adapter as ctp_adapter
from bt_api_py._normalization import (
    NormalizedRecord,
    _order_source_provenance,
    order as normalize_order,
    trade as normalize_trade,
)
from bt_api_py.service_risk_order_entry import (
    RiskOrderEntryError,
    _validate_source_position_dimensions,
)


_EXCHANGE = "CTP___DIM04_SOURCE_CONTRACT"
_SYMBOL = "IF2610"


def _project(direction: str, offset: str):
    order_row = CtpOrderData(
        {
            "InstrumentID": _SYMBOL,
            "AccountID": "provider-account-a",
            "OrderRef": "source-contract-order",
            "OrderSysID": "ctp-order-1",
            "Direction": direction,
            "CombOffsetFlag": offset,
            "LimitPrice": 4000,
            "VolumeTotalOriginal": 1,
            "VolumeTraded": 1,
            "VolumeTotal": 0,
            "OrderStatus": "0",
            "ExchangeID": "CFFEX",
        },
        _SYMBOL,
        "FUTURE",
        True,
    ).init_data()
    trade_row = CtpTradeData(
        {
            "InstrumentID": _SYMBOL,
            "AccountID": "provider-account-a",
            "OrderRef": "source-contract-order",
            "OrderSysID": "ctp-order-1",
            "TradeID": "ctp-trade-1",
            "Direction": direction,
            "OffsetFlag": offset,
            "Price": 4000,
            "Volume": 1,
            "ExchangeID": "CFFEX",
        },
        _SYMBOL,
        "FUTURE",
        True,
    ).init_data()
    aliases = {_SYMBOL: {_SYMBOL}}
    return (
        ctp_adapter._order(order_row, aliases),
        ctp_adapter._trade(trade_row, aliases),
    )


def _validate_close_projection(
    row: dict[str, object],
    *,
    operation: str,
    requested_side: str,
    requested_bucket: str,
) -> None:
    if operation == "query_order":
        normalized_values = normalize_order(row, _EXCHANGE, symbol=_SYMBOL)
    else:
        normalized_values = normalize_trade(row, _EXCHANGE, symbol=_SYMBOL)
    normalized = NormalizedRecord(
        normalized_values,
        provenance=_order_source_provenance({}, operation, row),
    )
    _validate_source_position_dimensions(
        normalized.normalization_provenance.to_dict(),
        normalized,
        position_side=requested_side,
        position_bucket=requested_bucket,
        position_effect="CLOSE",
        provider_exchange_name=_EXCHANGE,
        require_nondefault=True,
    )


@pytest.mark.parametrize(
    ("direction", "offset", "position_side", "position_bucket"),
    [("1", "3", "LONG", "TODAY"), ("0", "4", "SHORT", "YESTERDAY")],
)
def test_real_ctp_adapter_projection_survives_normalization_and_risk_check(
    direction, offset, position_side, position_bucket
):
    order_row, trade_row = _project(direction, offset)

    for row, operation in (
        (order_row, "query_order"),
        (trade_row, "get_deals"),
    ):
        assert row["position_dimensions_source"] == "ctp_side_offset_v1"
        assert row["position_side"] == position_side
        assert row["position_bucket"] == position_bucket
        _validate_close_projection(
            row,
            operation=operation,
            requested_side=position_side,
            requested_bucket=position_bucket,
        )


@pytest.mark.parametrize(("direction", "offset"), [("9", "3"), ("1", "2")])
def test_unresolved_ctp_side_offset_projection_fails_risk_close_check(direction, offset):
    order_row, _trade_row = _project(direction, offset)

    assert order_row["position_dimensions_source"] == "ctp_side_offset_unresolved_v1"
    with pytest.raises(RiskOrderEntryError, match="PROVIDER_POSITION_DIMENSIONS_UNPROVEN"):
        _validate_close_projection(
            order_row,
            operation="query_order",
            requested_side="LONG",
            requested_bucket="TODAY",
        )
