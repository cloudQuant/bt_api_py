from __future__ import annotations

import pytest
from bt_api_ctp.containers.ctp.ctp_order import CtpOrderData
from bt_api_ctp.containers.ctp.ctp_trade import CtpTradeData
from bt_api_ctp.normalization_policy import normalization_policy as ctp_policy
from bt_api_ctp.order_side_projection import order_side_candidates
from bt_api_py._normalization import (
    NormalizedRecord,
    _dict,
    _order_source_provenance,
    order as normalize_order,
    trade as normalize_trade,
)
from bt_api_py.order_source_alias_policy import resolve_order_source_alias_contract
from bt_api_py.service_risk_order_entry import (
    RiskOrderEntryError,
    _validate_source_position_dimensions,
)
from bt_api_py.venue_normalization_policy import normalization_policy


_EXCHANGE = "CTP___FUTURE"
_SYMBOL = "IF2610"


@pytest.fixture(autouse=True)
def _source_policy_entrypoint(monkeypatch):
    # Exercise the provider's source policy without relying on installed metadata.
    def load(group, exchange_name):
        assert group == "bt_api.normalization_policies"
        assert exchange_name == _EXCHANGE
        return ctp_policy

    def load_side(group, exchange_name):
        assert group == "bt_api.order_side_projections"
        assert exchange_name == _EXCHANGE
        return order_side_candidates

    monkeypatch.setattr("bt_api_py.venue_normalization_policy.load_route_entrypoint", load)
    monkeypatch.setattr(
        "bt_api_py.venue_order_side_projection.load_route_entrypoint", load_side
    )


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
    return _dict(order_row), _dict(trade_row)


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
        provenance=_order_source_provenance(
            {}, operation, row, policy=normalization_policy(_EXCHANGE)
        ),
    )
    _validate_source_position_dimensions(
        normalized.normalization_provenance.to_dict(),
        normalized,
        position_side=requested_side,
        position_bucket=requested_bucket,
        position_effect="CLOSE",
        provider_exchange_name=_EXCHANGE,
        aliases=resolve_order_source_alias_contract(
            _EXCHANGE, "bt-api-order-source-provenance-v6"
        ),
        require_nondefault=True,
    )


@pytest.mark.parametrize(
    ("direction", "offset", "position_side", "position_bucket"),
    [("1", "3", "LONG", "TODAY"), ("0", "4", "SHORT", "YESTERDAY")],
)
def test_ctp_container_projection_survives_normalization_and_risk_check(
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
