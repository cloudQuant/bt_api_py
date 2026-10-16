"""Readiness margin-mode contracts through the public BtApi read path.

Only the terminal feed methods are synthetic.  BtApi's readiness dispatch and
the Binance/OKX normalizers remain the code under test; no provider or order
write is invoked.
"""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from bt_api_py.bt_api import BtApi


@pytest.fixture
def api(monkeypatch: pytest.MonkeyPatch) -> BtApi:
    monkeypatch.setattr("bt_api_py.bt_api._ensure_plugins_loaded", lambda: None)
    return BtApi(debug=False)


def _binance_feed(*, margin_type: str = "ISOLATED") -> SimpleNamespace:
    return SimpleNamespace(
        get_account_config=Mock(
            return_value={"canTrade": True, "dualSidePosition": True}
        ),
        get_position_mode=Mock(return_value={"dualSidePosition": True}),
        get_exchange_info=Mock(
            return_value={
                "symbols": [
                    {
                        "symbol": "BTCUSDT",
                        "baseAsset": "BTC",
                        "quoteAsset": "USDT",
                        "contractType": "PERPETUAL",
                        "status": "TRADING",
                        "filters": [
                            {"filterType": "PRICE_FILTER", "tickSize": "0.1"},
                            {
                                "filterType": "LOT_SIZE",
                                "stepSize": "0.001",
                                "minQty": "0.001",
                                "maxQty": "100",
                            },
                            {"filterType": "MIN_NOTIONAL", "notional": "5"},
                        ],
                    }
                ]
            }
        ),
        get_symbol_config=Mock(
            return_value=[
                {
                    "symbol": "BTCUSDT",
                    "marginType": margin_type,
                    "leverage": "5",
                    "maxNotionalValue": "500000",
                }
            ]
        ),
        get_leverage_info=Mock(
            return_value=[
                {
                    "symbol": "BTCUSDT",
                    "marginType": margin_type,
                    "positionSide": "LONG",
                    "leverage": "5",
                }
            ]
        ),
        get_max_size=Mock(return_value=[]),
        make_order=Mock(side_effect=AssertionError("readiness must not place an order")),
    )


def _okx_feed(
    *,
    position_mode: str | None = "long_short_mode",
    leverage: object = "isolated",
) -> SimpleNamespace:
    config_row = {"perm": "trade", "acctLv": "2"}
    if position_mode is not None:
        config_row["posMode"] = position_mode

    if leverage is None:
        leverage_data = None
    else:
        leverage_data = [
            {
                "instId": "BTC-USDT-SWAP",
                "mgnMode": leverage,
                "posSide": "long",
                "lever": "4",
            },
            {
                "instId": "BTC-USDT-SWAP",
                "mgnMode": leverage,
                "posSide": "short",
                "lever": "4",
            },
        ]

    return SimpleNamespace(
        get_config=Mock(return_value={"code": "0", "data": [config_row]}),
        get_account_instruments=Mock(
            return_value={
                "code": "0",
                "data": [
                    {
                        "instId": "BTC-USDT-SWAP",
                        "instType": "SWAP",
                        "state": "live",
                        "lotSz": "1",
                        "minSz": "1",
                    }
                ],
            }
        ),
        get_leverage_info=Mock(
            return_value=(
                {"code": "0", "data": leverage_data}
                if leverage_data is not None
                else None
            )
        ),
        get_max_size=Mock(
            return_value={
                "code": "0",
                "data": [
                    {"instId": "BTC-USDT-SWAP", "maxBuy": "100", "maxSell": "80"}
                ],
            }
        ),
        make_order=Mock(side_effect=AssertionError("readiness must not place an order")),
    )


def test_binance_isolated_margin_mode_is_ready_from_symbol_configuration(api: BtApi) -> None:
    feed = _binance_feed()
    api.exchange_feeds["BINANCE___SWAP"] = feed

    result = api.get_order_readiness(
        "BINANCE___SWAP",
        "BTCUSDT",
        "0.002",
        margin_mode="isolated",
        position_mode="dual_side",
    )

    assert result["ready"] is True
    assert result["definite_failure"] is False
    assert result["execution_unproven"] is True
    assert result["margin_mode"] == result["configured_margin_mode"] == "isolated"
    assert result["checks"]["margin_mode"] is True
    assert result["leverage"] == 5.0
    feed.get_leverage_info.assert_called_once_with(
        "BTCUSDT",
        margin_mode="isolated",
        extra_data=None,
    )
    feed.make_order.assert_not_called()


def test_okx_isolated_margin_mode_is_ready_from_matching_leverage_rows(api: BtApi) -> None:
    feed = _okx_feed(leverage="isolated")
    api.exchange_feeds["OKX___SWAP"] = feed

    result = api.get_order_readiness(
        "OKX___SWAP",
        "BTC-USDT-SWAP",
        "2",
        margin_mode="isolated",
        position_mode="dual_side",
    )

    assert result["ready"] is True
    assert result["definite_failure"] is False
    assert result["execution_unproven"] is True
    assert result["margin_mode"] == "isolated"
    assert result["leverage_by_position_side"] == {"long": 4.0, "short": 4.0}
    assert result["checks"]["leverage"] is True
    feed.get_leverage_info.assert_called_once_with(
        "BTC-USDT-SWAP", margin_mode="isolated", extra_data=None
    )
    feed.get_max_size.assert_called_once_with(
        "BTC-USDT-SWAP", "isolated", extra_data=None
    )
    feed.make_order.assert_not_called()


def test_okx_cross_request_rejects_isolated_leverage_rows(api: BtApi) -> None:
    feed = _okx_feed(leverage="isolated")
    api.exchange_feeds["OKX___SWAP"] = feed

    result = api.get_order_readiness(
        "OKX___SWAP",
        "BTC-USDT-SWAP",
        "2",
        margin_mode="cross",
        position_mode="dual_side",
    )

    assert feed.get_leverage_info.return_value["data"][0]["mgnMode"] == "isolated"
    assert result["ready"] is False
    assert result["definite_failure"] is True
    assert result["execution_unproven"] is True
    assert result["margin_mode"] == "cross"
    assert result["checks"]["leverage"] is False
    assert result["leverage_by_position_side"] == {}
    assert result["reasons"] == ["leverage_configuration_missing"]
    feed.get_leverage_info.assert_called_once_with(
        "BTC-USDT-SWAP", margin_mode="cross", extra_data=None
    )
    feed.make_order.assert_not_called()


def test_okx_missing_position_mode_and_leverage_remain_unproven(api: BtApi) -> None:
    feed = _okx_feed(position_mode=None, leverage=None)
    api.exchange_feeds["OKX___SWAP"] = feed

    result = api.get_order_readiness(
        "OKX___SWAP",
        "BTC-USDT-SWAP",
        "2",
        margin_mode="isolated",
    )

    assert result["ready"] is False
    assert result["definite_failure"] is False
    assert result["execution_unproven"] is True
    assert result["position_mode"] is None
    assert result["margin_mode"] == "isolated"
    assert result["checks"]["position_mode"] is None
    assert result["checks"]["leverage"] is None
    assert set(result["reasons"]) == {"position_mode_unproven", "leverage_unproven"}
    feed.get_leverage_info.assert_called_once_with(
        "BTC-USDT-SWAP", margin_mode="isolated", extra_data=None
    )
    feed.make_order.assert_not_called()
