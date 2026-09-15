"""Public BtApi reads needed to validate perpetual demo execution preconditions."""

from __future__ import annotations

from decimal import Decimal
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from bt_api_py._contracts.errors import CapabilityNotSupportedError, NormalizedApiError
from bt_api_py._contracts.models import TransportMode
from bt_api_py.bt_api import BtApi
from bt_api_py.exceptions import ExchangeNotFoundError


@pytest.fixture
def api(monkeypatch):
    # This boundary test needs no plugin discovery, credentials or network.
    monkeypatch.setattr("bt_api_py.bt_api._ensure_plugins_loaded", lambda: None)
    return BtApi(debug=False)


@pytest.mark.parametrize("exchange", ["BINANCE___SWAP", "OKX___SWAP"])
@pytest.mark.parametrize("operation", ["get_exchange_info", "get_funding_rate"])
def test_symbol_reads_preserve_response_and_native_options(api, exchange, operation):
    response = object()
    method = Mock(return_value=response)
    api.exchange_feeds[exchange] = SimpleNamespace(**{operation: method})
    extra = {"trace": "preflight"}

    assert getattr(api, operation)(exchange, "BTC-USDT", extra_data=extra, limit=3) is response
    method.assert_called_once_with("BTC-USDT", extra_data=extra, limit=3)


def test_exchange_info_can_request_all_instruments(api):
    method = Mock(return_value={"symbols": []})
    api.exchange_feeds["BINANCE___SWAP"] = SimpleNamespace(get_exchange_info=method)

    assert api.get_exchange_info("BINANCE___SWAP") == {"symbols": []}
    method.assert_called_once_with(None, extra_data=None)


def test_okx_reads_config_for_account_and_position_mode_without_mutation(api):
    response = {"code": "0", "data": [{"posMode": "net_mode", "acctLv": "2"}]}
    getter = Mock(return_value=response)
    setter = Mock(side_effect=AssertionError("read operation must never change account mode"))
    api.exchange_feeds["OKX___SWAP"] = SimpleNamespace(get_config=getter, set_mode=setter)

    assert api.get_account_config("OKX___SWAP") is response
    assert api.get_position_mode("OKX___SWAP") is response
    assert getter.call_count == 2
    setter.assert_not_called()


def test_binance_preserves_native_position_mode_boolean(api):
    response = {"dualSidePosition": False}
    getter = Mock(return_value=response)
    api.exchange_feeds["BINANCE___SWAP"] = SimpleNamespace(get_position_mode=getter)

    assert api.get_position_mode("BINANCE___SWAP") is response
    getter.assert_called_once_with(extra_data=None)


@pytest.mark.parametrize("can_trade", [True, False])
def test_binance_normalized_account_config_uses_one_consistent_snapshot(api, can_trade):
    account_config = Mock(return_value={"dualSidePosition": True, "canTrade": can_trade})
    api.exchange_feeds["BINANCE___SWAP"] = SimpleNamespace(get_account_config=account_config)

    result = api.get_account_config("BINANCE___SWAP", normalized=True)

    assert result == {
        "exchange_name": "BINANCE___SWAP",
        "position_mode": "dual_side",
        "can_trade": can_trade,
        "trading_permissions": None,
    }
    account_config.assert_called_once_with(extra_data=None)


def test_binance_normalized_account_config_fails_closed_when_permission_read_fails(api):
    api.exchange_feeds["BINANCE___SWAP"] = SimpleNamespace(
        get_position_mode=Mock(return_value={"dualSidePosition": True}),
        get_account_config=Mock(side_effect=TimeoutError("response lost")),
    )

    with pytest.raises(NormalizedApiError, match="get_account_config"):
        api.get_account_config("BINANCE___SWAP", normalized=True)


def test_explicit_account_config_operation_takes_precedence_over_alias(api):
    getter = Mock(return_value={"mode": "native"})
    fallback = Mock(side_effect=AssertionError("explicit operation should be preferred"))
    api.exchange_feeds["OTHER___SWAP"] = SimpleNamespace(
        get_account_config=getter, get_config=fallback
    )

    assert api.get_account_config("OTHER___SWAP", extra_data={"trace": "x"}) == {"mode": "native"}
    getter.assert_called_once_with(extra_data={"trace": "x"})
    fallback.assert_not_called()


_READS = [
    ("get_exchange_info", ()),
    ("get_funding_rate", ("BTC-USDT",)),
    ("get_account_config", ()),
    ("get_position_mode", ()),
]


@pytest.mark.parametrize("operation,args", _READS)
def test_unsupported_read_fails_explicitly(api, operation, args):
    api.exchange_feeds["OTHER___SWAP"] = SimpleNamespace()

    with pytest.raises(CapabilityNotSupportedError, match=operation):
        getattr(api, operation)("OTHER___SWAP", *args)


@pytest.mark.parametrize("operation,args", _READS)
def test_zmq_read_rejected_before_accessing_direct_feed(api, operation, args):
    api.transport_mode = TransportMode.ZMQ
    api._get_feed = Mock(side_effect=AssertionError("must not access direct feed from ZMQ"))

    with pytest.raises(CapabilityNotSupportedError, match="transport=zmq"):
        getattr(api, operation)("BINANCE___SWAP", *args)
    api._get_feed.assert_not_called()


def test_unknown_exchange_uses_existing_registry_error(api):
    with pytest.raises(ExchangeNotFoundError):
        api.get_exchange_info("MISSING___SWAP")


def test_arbitrary_account_config_is_not_misinterpreted_as_position_mode(api):
    api.exchange_feeds["OTHER___SWAP"] = SimpleNamespace(get_config=Mock())

    with pytest.raises(CapabilityNotSupportedError):
        api.get_position_mode("OTHER___SWAP")


def _okx_readiness_feed(*, instruments=None, leverage=None, maximum=None):
    return SimpleNamespace(
        get_config=Mock(
            return_value={
                "code": "0",
                "data": [
                    {
                        "perm": "trade",
                        "acctLv": "2",
                        "posMode": "long_short_mode",
                    }
                ],
            }
        ),
        get_account_instruments=Mock(
            return_value={
                "code": "0",
                "data": (
                    [
                        {
                            "instId": "BTC-USDT-SWAP",
                            "instType": "SWAP",
                            "state": "live",
                            "lotSz": "1",
                            "minSz": "1",
                        }
                    ]
                    if instruments is None
                    else instruments
                ),
            }
        ),
        get_leverage_info=Mock(
            return_value={
                "code": "0",
                "data": (
                    [
                        {
                            "instId": "BTC-USDT-SWAP",
                            "mgnMode": "cross",
                            "posSide": "long",
                            "lever": "3",
                        },
                        {
                            "instId": "BTC-USDT-SWAP",
                            "mgnMode": "cross",
                            "posSide": "short",
                            "lever": "3",
                        },
                    ]
                    if leverage is None
                    else leverage
                ),
            }
        ),
        get_max_size=Mock(
            return_value={
                "code": "0",
                "data": (
                    [{"instId": "BTC-USDT-SWAP", "maxBuy": "100", "maxSell": "80"}]
                    if maximum is None
                    else maximum
                ),
            }
        ),
    )


def _binance_readiness_feed(*, symbol_config=None):
    return SimpleNamespace(
        get_account_config=Mock(return_value={"canTrade": True, "dualSidePosition": True}),
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
            return_value=(
                [
                    {
                        "symbol": "BTCUSDT",
                        "marginType": "CROSSED",
                        "leverage": "10",
                        "maxNotionalValue": "500000",
                    }
                ]
                if symbol_config is None
                else symbol_config
            )
        ),
        get_environment_info=Mock(
            return_value={
                "environment": "demo",
                "simulated": True,
                "verified": True,
            }
        ),
    )


def test_binance_readiness_uses_account_mode_symbol_rules_and_leverage(api):
    feed = _binance_readiness_feed()
    api.exchange_feeds["BINANCE___SWAP"] = feed

    result = api.get_order_readiness(
        "BINANCE___SWAP",
        "BTCUSDT",
        "0.002",
        margin_mode="cross",
        position_mode="dual_side",
    )

    assert result["ready"] is True
    assert result["definite_failure"] is False
    assert result["reasons"] == []
    assert result["position_mode"] == "dual_side"
    assert result["max_size"] == 100.0
    assert result["leverage"] == 10.0
    assert all(value is True for value in result["checks"].values())


def test_binance_readiness_keeps_missing_symbol_config_unproven(api):
    feed = _binance_readiness_feed(symbol_config=[])
    api.exchange_feeds["BINANCE___SWAP"] = feed

    result = api.get_order_readiness(
        "BINANCE___SWAP", "BTCUSDT", "0.002", position_mode="dual_side"
    )

    assert result["ready"] is False
    assert result["definite_failure"] is False
    assert set(result["reasons"]) == {"margin_mode_unproven", "leverage_unproven"}


@pytest.mark.asyncio
async def test_typed_binance_readiness_sync_and_async_share_contract(api):
    feed = _binance_readiness_feed()
    api.exchange_feeds["BINANCE___SWAP"] = feed

    sync_result = api.get_trading_readiness(
        "BINANCE___SWAP",
        "BTCUSDT",
        "account-a",
        Decimal("0.002"),
        position_mode="dual_side",
    )
    async_result = await api.async_get_trading_readiness(
        "BINANCE___SWAP",
        "BTCUSDT",
        "account-a",
        Decimal("0.002"),
        position_mode="dual_side",
    )

    assert sync_result.ready and async_result.ready
    assert not sync_result.definite_failure and sync_result.reasons == ()
    assert async_result.position_mode == "dual_side"


def test_okx_readiness_combines_only_read_operations(api):
    feed = _okx_readiness_feed()
    feed.make_order = Mock(side_effect=AssertionError("readiness must not submit"))
    api.exchange_feeds["OKX___SWAP"] = feed

    result = api.get_order_readiness(
        "OKX___SWAP",
        "BTC-USDT-SWAP",
        "2",
        margin_mode="cross",
        position_mode="dual_side",
    )

    assert result == {
        "ready": True,
        "definite_failure": False,
        "reasons": [],
        "execution_unproven": True,
        "exchange_name": "OKX___SWAP",
        "symbol": "BTC-USDT-SWAP",
        "requested_quantity_native": 2.0,
        "quantity_unit": "native_contracts",
        "margin_mode": "cross",
        "position_mode": "dual_side",
        "expected_position_mode": "dual_side",
        "account_level": "2",
        "can_trade": True,
        "trading_permissions": ["trade"],
        "instrument_state": "live",
        "min_size": 1.0,
        "lot_size": 1.0,
        "leverage_by_position_side": {"long": 3.0, "short": 3.0},
        "max_buy": 100.0,
        "max_sell": 80.0,
        "checks": {
            "quantity_native": True,
            "quantity_min_size": True,
            "quantity_lot_size": True,
            "trading_permission": True,
            "derivatives_account_level": True,
            "position_mode": True,
            "instrument_live": True,
            "leverage": True,
            "max_buy": True,
            "max_sell": True,
        },
    }
    feed.get_config.assert_called_once_with(extra_data=None)
    feed.get_account_instruments.assert_called_once_with("BTC-USDT-SWAP", extra_data=None)
    feed.get_leverage_info.assert_called_once_with(
        "BTC-USDT-SWAP", margin_mode="cross", extra_data=None
    )
    feed.get_max_size.assert_called_once_with("BTC-USDT-SWAP", "cross", extra_data=None)
    feed.make_order.assert_not_called()


@pytest.mark.parametrize(
    "quantity,instrument,expected_min,expected_lot,reason",
    [
        (
            "0.5",
            {
                "instId": "BTC-USDT-SWAP",
                "state": "live",
                "lotSz": "1",
                "minSz": "1",
            },
            False,
            False,
            "quantity_below_min_size",
        ),
        (
            "1.1",
            {
                "instId": "BTC-USDT-SWAP",
                "state": "live",
                "lotSz": "0.25",
                "minSz": "0.5",
            },
            True,
            False,
            "quantity_not_multiple_of_lot_size",
        ),
    ],
)
def test_okx_readiness_rejects_native_quantity_outside_instrument_rules(
    api, quantity, instrument, expected_min, expected_lot, reason
):
    feed = _okx_readiness_feed(instruments=[instrument])
    api.exchange_feeds["OKX___SWAP"] = feed

    result = api.get_order_readiness("OKX___SWAP", "BTC-USDT-SWAP", quantity)

    assert result["ready"] is False
    assert result["definite_failure"] is True
    assert reason in result["reasons"]
    assert result["checks"]["quantity_min_size"] is expected_min
    assert result["checks"]["quantity_lot_size"] is expected_lot
    feed.get_leverage_info.assert_not_called()
    feed.get_max_size.assert_not_called()


def test_okx_readiness_accepts_exact_decimal_native_quantity_grid(api):
    feed = _okx_readiness_feed(
        instruments=[
            {
                "instId": "BTC-USDT-SWAP",
                "state": "live",
                "lotSz": "0.25",
                "minSz": "0.5",
            }
        ]
    )
    api.exchange_feeds["OKX___SWAP"] = feed

    result = api.get_order_readiness("OKX___SWAP", "BTC-USDT-SWAP", "1.25")

    assert result["ready"] is True
    assert result["definite_failure"] is False
    assert result["reasons"] == []
    assert result["checks"]["quantity_min_size"] is True
    assert result["checks"]["quantity_lot_size"] is True


@pytest.mark.parametrize("quantity", [0, -1, True, "NaN"])
def test_okx_readiness_never_accepts_invalid_native_quantity(api, quantity):
    feed = _okx_readiness_feed()
    api.exchange_feeds["OKX___SWAP"] = feed

    result = api.get_order_readiness("OKX___SWAP", "BTC-USDT-SWAP", quantity)

    assert result["ready"] is False
    assert result["definite_failure"] is True
    assert "invalid_quantity_native" in result["reasons"]
    assert result["checks"]["quantity_native"] is False
    feed.get_leverage_info.assert_not_called()
    feed.get_max_size.assert_not_called()


def test_okx_readiness_missing_account_instrument_is_definite_and_short_circuits(api):
    feed = _okx_readiness_feed(instruments=[])
    feed.get_leverage_info.side_effect = AssertionError("must stop at missing instrument")
    feed.get_max_size.side_effect = AssertionError("must stop at missing instrument")
    api.exchange_feeds["OKX___SWAP"] = feed

    result = api.get_order_readiness("OKX___SWAP", "BTC-USDT-SWAP", 1)

    assert result["ready"] is False
    assert result["definite_failure"] is True
    assert "instrument_not_enabled" in result["reasons"]
    assert result["execution_unproven"] is True
    feed.get_leverage_info.assert_not_called()
    feed.get_max_size.assert_not_called()


def test_okx_readiness_maximum_native_size_is_checked_for_both_sides(api):
    feed = _okx_readiness_feed(maximum=[{"instId": "BTC-USDT-SWAP", "maxBuy": "1", "maxSell": "4"}])
    api.exchange_feeds["OKX___SWAP"] = feed

    result = api.get_order_readiness("OKX___SWAP", "BTC-USDT-SWAP", 2)

    assert result["ready"] is False
    assert result["definite_failure"] is True
    assert result["max_buy"] == 1.0 and result["max_sell"] == 4.0
    assert result["reasons"] == ["max_buy_insufficient"]


def test_okx_readiness_requires_both_leverage_sides_in_dual_side_mode(api):
    feed = _okx_readiness_feed(
        leverage=[
            {
                "instId": "BTC-USDT-SWAP",
                "mgnMode": "cross",
                "posSide": "long",
                "lever": "3",
            }
        ]
    )
    api.exchange_feeds["OKX___SWAP"] = feed

    result = api.get_order_readiness("OKX___SWAP", "BTC-USDT-SWAP", 2)

    assert result["ready"] is False
    assert result["definite_failure"] is True
    assert result["reasons"] == ["leverage_configuration_missing"]


def test_readiness_is_explicitly_unsupported_for_other_venues_and_zmq(api):
    api.exchange_feeds["OTHER___SWAP"] = SimpleNamespace()
    with pytest.raises(CapabilityNotSupportedError, match="OTHER___SWAP"):
        api.get_order_readiness("OTHER___SWAP", "BTCUSDT", 0.001)

    api.transport_mode = TransportMode.ZMQ
    api._get_feed = Mock(side_effect=AssertionError("must not access a direct feed"))
    with pytest.raises(CapabilityNotSupportedError, match="transport=zmq"):
        api.get_order_readiness("OKX___SWAP", "BTC-USDT-SWAP", 1)
    api._get_feed.assert_not_called()


def test_readiness_preserves_an_unknown_vendor_code_without_classifying_it(api):
    error = NormalizedApiError("get_config", "50123")
    feed = _okx_readiness_feed()
    feed.get_config.side_effect = error
    api.exchange_feeds["OKX___SWAP"] = feed

    with pytest.raises(NormalizedApiError) as excinfo:
        api.get_order_readiness("OKX___SWAP", "BTC-USDT-SWAP", 1)

    assert excinfo.value.code == "50123"
    assert excinfo.value.execution_unknown is False
    assert excinfo.value.definite_reject is False
    assert excinfo.value.category is None
    assert excinfo.value.environment_mismatch_possible is False


def test_readiness_enriches_known_auth_error_without_rewrapping(api):
    error = NormalizedApiError("get_config", "50101")
    feed = _okx_readiness_feed()
    feed.get_config.side_effect = error
    api.exchange_feeds["OKX___SWAP"] = feed

    with pytest.raises(NormalizedApiError) as excinfo:
        api.get_order_readiness("OKX___SWAP", "BTC-USDT-SWAP", 1)

    assert excinfo.value is error
    assert error.code == "50101"
    assert error.category == "auth"
    assert error.environment_mismatch_possible is True
    assert error.__context__ is None and error.__cause__ is None


def test_close_stops_streams_and_closes_their_rest_clients_before_feeds(api):
    calls = []
    stream = SimpleNamespace(
        stop=lambda: calls.append("stream_stop"),
        disconnect=lambda: calls.append("stream_http_close"),
    )
    api._subscription_streams.append(stream)
    api._subscription_flags["BINANCE___SWAP_account"] = True
    api.exchange_feeds["BINANCE___SWAP"] = SimpleNamespace(
        disconnect=lambda: calls.append("feed_close")
    )

    api.close()
    assert calls == ["stream_stop", "stream_http_close", "feed_close"]
    assert api._subscription_streams == []
    assert api._subscription_flags == {}
    api.close()
    assert calls.count("stream_stop") == 1


def test_stream_failure_does_not_skip_other_cleanup_and_can_be_retried(api):
    stop = Mock(side_effect=[RuntimeError("stop failed"), None])
    stream_disconnect = Mock()
    other_stop = Mock()
    feed_disconnect = Mock()
    failed_stream = SimpleNamespace(stop=stop, disconnect=stream_disconnect)
    api._subscription_streams.extend([failed_stream, SimpleNamespace(stop=other_stop)])
    api.exchange_feeds["OKX___SWAP"] = SimpleNamespace(disconnect=feed_disconnect)

    with pytest.raises(RuntimeError, match="stop failed"):
        api.close()
    stream_disconnect.assert_called_once()
    other_stop.assert_called_once()
    feed_disconnect.assert_called_once()
    assert api._subscription_streams == [failed_stream]
    api.close()
    assert api._subscription_streams == []


@pytest.mark.asyncio
async def test_async_close_also_stops_subscription_streams(api):
    stop = Mock()
    api._subscription_streams.append(SimpleNamespace(stop=stop))

    await api.async_close()
    stop.assert_called_once()
    assert api._subscription_streams == []
