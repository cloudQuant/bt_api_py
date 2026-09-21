"""Opt-in public contracts, using native payloads without network or credentials."""

from decimal import Decimal
from queue import Queue
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from bt_api_py import BtApi, NormalizedApiError
from bt_api_py._contracts import CapabilityNotSupportedError
from bt_api_py._contracts.models import (
    CancelOrderRequest,
    ForwardingConfig,
    OrderRequest,
    OrderType,
    QueryOrderRequest,
    Side,
)
from bt_api_py._normalization import (
    check_response,
    normalize_error,
    normalize_event,
    normalize_result,
)


@pytest.fixture
def api(monkeypatch):
    monkeypatch.setattr("bt_api_py.bt_api._ensure_plugins_loaded", lambda: None)
    return BtApi(debug=False)


def request(**kwargs):
    return OrderRequest(
        symbol="BTCUSDT",
        quantity=Decimal("0.01"),
        side=Side.BUY,
        order_type=OrderType.LIMIT,
        price=Decimal("50000"),
        account_id="demo",
        client_order_id="123456789012",
        **kwargs,
    )


def attach(api, exchange, **methods):
    api.exchange_feeds[exchange] = SimpleNamespace(**methods)
    api.data_queues[exchange] = Queue()


def test_raw_default_is_unchanged_and_normalized_metadata_is_native(api):
    raw = {
        "symbols": [
            {
                "symbol": "BTCUSDT",
                "contractType": "PERPETUAL",
                "marginAsset": "USDT",
                "baseAsset": "BTC",
                "filters": [
                    {"filterType": "LOT_SIZE", "stepSize": "0.001", "minQty": "0.001"},
                    {"filterType": "PRICE_FILTER", "tickSize": "0.1"},
                    {"filterType": "MIN_NOTIONAL", "notional": "100"},
                ],
            }
        ]
    }
    attach(api, "BINANCE___SWAP", get_exchange_info=Mock(return_value=raw))
    assert api.get_exchange_info("BINANCE___SWAP") is raw
    result = api.get_exchange_info("BINANCE___SWAP", "BTCUSDT", normalized=True)
    assert (result["multiplier"], result["lot_size"], result["min_notional"]) == (
        1,
        0.001,
        100,
    )
    assert result["quantity_unit"] == "base"
    assert "filters" not in result


def test_okx_contract_metadata_and_funding_seconds(api):
    attach(
        api,
        "OKX___SWAP",
        get_exchange_info=Mock(
            return_value={
                "code": "0",
                "data": [
                    {
                        "instId": "BTC-USDT-SWAP",
                        "ctVal": "0.01",
                        "ctMult": "1",
                        "lotSz": "0.01",
                        "minSz": "0.01",
                        "tickSz": "0.1",
                        "ctType": "linear",
                    }
                ],
            }
        ),
        get_funding_rate=Mock(
            return_value={
                "code": "0",
                "data": [
                    {
                        "fundingRate": ".0001",
                        "fundingTime": "1700000000000",
                        "nextFundingTime": "1700028800000",
                    }
                ],
            }
        ),
    )
    row = api.get_exchange_info("OKX___SWAP", "BTC-USDT-SWAP", normalized=True)
    assert row["multiplier"] == 0.01 and row["quantity_unit"] == "contracts"
    assert (
        api.get_funding_rate("OKX___SWAP", "BTC-USDT-SWAP", normalized=True)["next_funding_time"]
        == 1700000000
    )


def test_binance_next_funding_time_keeps_its_native_meaning(api):
    attach(
        api,
        "BINANCE___SWAP",
        get_funding_rate=Mock(
            return_value={
                "symbol": "BTCUSDT",
                "lastFundingRate": ".0001",
                "nextFundingTime": 1700028800000,
            }
        ),
    )
    result = api.get_funding_rate("BINANCE___SWAP", "BTCUSDT", normalized=True)
    assert result["rate"] == 0.0001
    assert result["next_funding_time"] == 1700028800


def test_account_multi_currency_never_sums_unconverted_equities(api):
    raw = [
        {
            "asset": "USDT",
            "availableBalance": "100",
            "balance": "120",
            "crossUnPnl": "3",
        },
        {"asset": "BTC", "availableBalance": "1", "balance": "2", "crossUnPnl": "0"},
    ]
    attach(
        api,
        "BINANCE___SWAP",
        get_account=Mock(return_value=raw),
        get_balance=Mock(return_value=raw),
    )
    with pytest.raises(NormalizedApiError):
        api.get_account("BINANCE___SWAP", normalized=True)
    assert api.get_account("BINANCE___SWAP", "USDT", normalized=True)["value"] == 123
    assert api.get_balance("BINANCE___SWAP", "BTC", normalized=True)["cash"] == 1


def test_zero_balance_is_valid_account(api):
    attach(
        api,
        "BINANCE___SWAP",
        get_account=Mock(
            return_value={
                "assets": [
                    {
                        "asset": "USDT",
                        "availableBalance": "0",
                        "walletBalance": "0",
                        "unrealizedProfit": "0",
                    },
                    {
                        "asset": "BTC",
                        "availableBalance": "0",
                        "walletBalance": "0",
                        "unrealizedProfit": "0",
                    },
                ]
            }
        ),
    )
    row = api.get_account("BINANCE___SWAP", normalized=True)
    assert row["cash"] == row["value"] == 0 and row["currency"] == "USDT"


@pytest.mark.parametrize("can_trade", [True, False, None])
def test_normalized_binance_account_preserves_explicit_trade_permission(api, can_trade):
    raw = {
        "assets": [
            {
                "asset": "USDT",
                "availableBalance": "100",
                "walletBalance": "120",
                "unrealizedProfit": "3",
            }
        ]
    }
    if can_trade is not None:
        raw["canTrade"] = can_trade
    attach(api, "BINANCE___SWAP", get_account=Mock(return_value=raw))

    row = api.get_account("BINANCE___SWAP", "USDT", normalized=True)

    assert row["can_trade"] is can_trade
    assert row["trading_permissions"] is None


def test_mode_is_cached_and_request_not_mutated(api):
    mode = Mock(return_value={"dualSidePosition": True})
    attach(api, "BINANCE___SWAP", get_position_mode=mode)
    api._backend.make_order = Mock(return_value={"orderId": 123, "clientOrderId": "123456789012"})
    req = request(position_side="long", offset="open")
    result = api.make_order("BINANCE___SWAP", req, normalized=True)
    api.make_order("BINANCE___SWAP", req, normalized=True)
    assert mode.call_count == 1 and req.position_mode is None
    assert api._backend.make_order.call_args.args[1].position_mode == "dual_side"
    assert result["order_id"] == "123" and result["status"] == "accepted"


@pytest.mark.parametrize("exchange", ["CTP___FUTURE", "MT5___FX"])
def test_missing_position_mode_is_explicit_capability(api, exchange):
    attach(api, exchange)
    with pytest.raises(CapabilityNotSupportedError):
        api.get_position_mode(exchange, normalized=True)


@pytest.mark.parametrize("status", ["PARTIALLY_FILLED", "CANCELED", "FILLED"])
def test_filled_without_actual_price_remains_unknown(api, status):
    api._backend.query_order = Mock(
        return_value={
            "orderId": 42,
            "status": status,
            "executedQty": "0.005",
            "price": "50000",
            "avgPrice": "0",
        }
    )
    result = api.query_order(
        "BINANCE___SWAP",
        QueryOrderRequest(symbol="BTCUSDT", account_id="demo", client_order_id="123456789012"),
        normalized=True,
    )
    assert result["execution_unknown"] and not result["terminal_confirmed"]
    assert result["filled"] == 0.005 and result["avg_price"] == 0


def test_cumulative_price_and_fee_are_explicit(api):
    api._backend.query_order = Mock(
        return_value={
            "code": "0",
            "data": [
                {
                    "ordId": "42",
                    "state": "canceled",
                    "accFillSz": "2",
                    "avgPx": "50100",
                    "px": "50200",
                    "fee": "-.2",
                    "feeCcy": "USDT",
                }
            ],
        }
    )
    result = api.query_order(
        "OKX___SWAP",
        QueryOrderRequest(symbol="BTC-USDT-SWAP", account_id="demo", order_id="42"),
        normalized=True,
    )
    assert result["filled"] == 2 and result["avg_price"] == 50100
    assert result["cumulative_commission"] == 0.2 and result["terminal_confirmed"]


def test_timeout_error_has_no_signed_url_and_never_retries(api):
    transport_error = TimeoutError("https://venue?signature=PRIVATE")
    api._backend.make_order = Mock(side_effect=transport_error)
    with pytest.raises(NormalizedApiError) as captured:
        api.make_order("BINANCE___SWAP", request(position_mode="net"), normalized=True)
    error = captured.value
    assert error.execution_unknown and not error.definite_reject
    assert (
        "PRIVATE" not in str(error)
        and "PRIVATE" not in repr(error)
        and error.operation == "make_order"
    )
    assert error.__context__ is None
    assert error.__cause__ is None
    assert not hasattr(error, "context")
    assert not hasattr(error, "raw_response")
    assert not hasattr(error, "original_error")
    assert not hasattr(error, "url")
    assert api._backend.make_order.call_count == 1


@pytest.mark.parametrize(
    "exchange_name,raw,expected_code",
    [
        (
            "OKX___SWAP",
            {"code": "50101", "msg": "signature=PRIVATE"},
            "50101",
        ),
        (
            "OKX___SWAP",
            {
                "code": "0",
                "data": [{"sCode": "50119", "sMsg": "signature=PRIVATE"}],
            },
            "50119",
        ),
        (
            "BINANCE___SWAP",
            {"code": -2015, "msg": "signature=PRIVATE"},
            "-2015",
        ),
    ],
)
@pytest.mark.parametrize("write", [False, True])
def test_environment_auth_codes_are_deterministic_and_keep_only_raw_code(
    exchange_name, raw, expected_code, write
):
    with pytest.raises(NormalizedApiError) as captured:
        check_response(
            raw,
            "make_order" if write else "get_account_config",
            exchange_name=exchange_name,
            write=write,
        )

    error = captured.value
    assert error.code == expected_code
    assert error.category == "auth"
    assert error.environment_mismatch_possible is True
    assert error.execution_unknown is False
    assert error.definite_reject is write
    assert "PRIVATE" not in str(error) and "PRIVATE" not in repr(error)


def test_unknown_vendor_code_is_not_misclassified_as_auth():
    with pytest.raises(NormalizedApiError) as captured:
        normalize_result(
            "get_account_config",
            {"code": "50123", "msg": "signature=PRIVATE"},
            "OKX___SWAP",
        )

    error = captured.value
    assert error.code == "50123"
    assert error.category is None
    assert error.environment_mismatch_possible is False
    assert "PRIVATE" not in str(error) and "PRIVATE" not in repr(error)


@pytest.mark.parametrize(
    "exchange_name,code",
    [("OKX___SWAP", -2015), ("BINANCE___SWAP", "50101")],
)
def test_auth_code_classification_is_scoped_to_its_exchange(exchange_name, code):
    with pytest.raises(NormalizedApiError) as captured:
        check_response(
            {"code": code, "msg": "signature=PRIVATE"},
            "get_account_config",
            exchange_name=exchange_name,
        )

    assert captured.value.code == str(code)
    assert captured.value.category is None
    assert captured.value.environment_mismatch_possible is False


def test_unified_error_is_reduced_to_safe_normalized_auth_fields():
    from bt_api_base.error import ErrorCategory, UnifiedError, UnifiedErrorCode

    transport_error = UnifiedError(
        code=UnifiedErrorCode.INVALID_API_KEY,
        category=ErrorCategory.AUTH,
        venue="OKX___SWAP",
        message="https://venue?signature=PRIVATE",
        original_error="PRIVATE original exception",
        context={
            "raw_response": {
                "code": "0",
                "data": [{"sCode": "50119", "sMsg": "PRIVATE"}],
            }
        },
    )

    error = normalize_error(transport_error, "get_account_config")

    assert error.code == "50119"
    assert error.category == "auth"
    assert error.environment_mismatch_possible is True
    assert error.__context__ is None and error.__cause__ is None
    assert error.__traceback__ is None
    assert "PRIVATE" not in str(error) and "PRIVATE" not in repr(error)
    assert not hasattr(error, "context")
    assert not hasattr(error, "original_error")


def test_public_normalized_path_supplies_exchange_for_auth_classification(api):
    attach(
        api,
        "BINANCE___SWAP",
        get_account_config=Mock(return_value={"code": -2015, "msg": "signature=PRIVATE"}),
    )

    with pytest.raises(NormalizedApiError) as captured:
        api.get_account_config("BINANCE___SWAP", normalized=True)

    error = captured.value
    assert error.code == "-2015"
    assert error.category == "auth"
    assert error.environment_mismatch_possible is True
    assert error.__context__ is None and error.__cause__ is None


def test_public_normalized_path_does_not_rewrap_existing_safe_error(api):
    original = NormalizedApiError("make_order", "50119")
    api._backend.make_order = Mock(side_effect=original)

    with pytest.raises(NormalizedApiError) as captured:
        api.make_order("OKX___SWAP", request(position_mode="net"), normalized=True)

    assert captured.value is original
    assert original.category == "auth"
    assert original.environment_mismatch_possible is True
    assert original.__context__ is None and original.__cause__ is None


@pytest.mark.parametrize(
    "code, unknown",
    [("50120", False), ("50123", False), ("51008", False), ("50004", True), ("-1007", True)],
)
def test_explicit_response_error_semantics(api, code, unknown):
    api._backend.make_order = Mock(return_value={"code": code, "msg": "secret signed URL"})
    with pytest.raises(NormalizedApiError) as captured:
        api.make_order("OKX___SWAP", request(position_mode="net"), normalized=True)
    assert captured.value.execution_unknown == unknown
    assert captured.value.definite_reject != unknown
    assert "secret" not in str(captured.value)


def test_common_getters_and_requestdata_are_first_source():
    class GetterOnly:
        def get_event(self):
            return "TradeEvent"

        def get_symbol_name(self):
            return "rb2701"

        def get_trade_id(self):
            return "deal-9"

        def get_order_id(self):
            return "sys-8"

        def get_trade_volume(self):
            return 2

        def get_trade_price(self):
            return 3400

        def get_trade_offset(self):
            return "close_today"

        def get_trade_side(self):
            return "sell"

    class Response:
        def get_input_data(self):
            return {"data": [{"volume": 999}]}

        def get_data(self):
            return [GetterOnly()]

    row = normalize_result("get_deals", Response(), "CTP___FUTURE")[0]
    assert row["size"] == 2 and row["offset"] == "close_today"
    assert row["order_id"] == "sys-8" and row["quantity_unit"] == "contracts"


def test_ctp_real_containers_preserve_native_refs_and_do_not_invent_vwap():
    from bt_api_ctp.containers.ctp.ctp_order import CtpOrderData
    from bt_api_ctp.containers.ctp.ctp_position import CtpPositionData

    raw = {
        "InstrumentID": "rb2701",
        "OrderSysID": "sys-8",
        "OrderRef": "123456789012",
        "OrderStatus": "0",
        "Direction": "0",
        "CombOffsetFlag": "0",
        "VolumeTotalOriginal": 2,
        "VolumeTraded": 2,
        "LimitPrice": 3400,
        "FrontID": 1,
        "SessionID": 2,
        "ExchangeID": "SHFE",
    }
    row = normalize_event(CtpOrderData(raw), "CTP___FUTURE")
    assert row["order_id"] == "sys-8" and row["order_ref"] == "123456789012"
    assert row["front_id"] == 1 and row["session_id"] == 2
    assert row["avg_price"] is None and not row["execution_unknown"]
    assert row["execution_source"] == "trades"
    pos = normalize_result(
        "get_position",
        CtpPositionData(
            {
                "InstrumentID": "rb2701",
                "PosiDirection": "3",
                "Position": 3,
                "TodayPosition": 1,
                "YdPosition": 2,
            }
        ),
        "CTP___FUTURE",
    )[0]
    assert pos["position_side"] == "short" and pos["size"] == 3
    assert pos["today"] == 1 and pos["yesterday"] == 2


def test_ctp_account_equity_is_balance_not_margin_getter():
    from bt_api_ctp.containers.ctp.ctp_account import CtpAccountData

    row = normalize_result(
        "get_account",
        CtpAccountData(
            {
                "AccountID": "A-123",
                "CurrencyID": "CNY",
                "Balance": 100000,
                "Available": 75000,
                "PositionProfit": 1200,
            }
        ),
        "CTP___FUTURE",
    )
    assert row["currency"] == "CNY" and row["value"] == 100000 and row["cash"] == 75000


def test_mt5_lots_position_ticket_and_deal_ticket_are_distinct():
    pos = normalize_result(
        "get_position",
        [
            {
                "symbol": "EURUSD",
                "quantity": 0.02,
                "position_side": "short",
                "position_id": "position-10",
                "price": 1.11,
                "quantity_unit": "lots",
            }
        ],
        "MT5___FX",
    )[0]
    assert pos["size"] == 0.02 and pos["position_id"] == "position-10"
    deal = normalize_event(
        {
            "kind": "trade",
            "symbol": "EURUSD",
            "trade_id": "deal-11",
            "order_id": "order-12",
            "position_id": "position-10",
            "size": 0.01,
            "price": 1.12,
            "offset": "close",
        },
        "MT5___FX",
    )
    assert deal["quantity_unit"] == "lots" and deal["trade_id"] == "deal-11"
    assert deal["position_id"] == "position-10" and deal["offset"] == "close"


def test_mixed_event_queue_keeps_arrival_order_and_seconds(api):
    attach(api, "OKX___SWAP")
    source = api.data_queues["OKX___SWAP"]
    source.put(
        {
            "event": "OrderBookEvent",
            "instId": "BTC-USDT-SWAP",
            "ts": "1700000000000",
            "bids": [["100", "2"]],
            "asks": [["101", "3"]],
        }
    )
    source.put(
        {
            "event": "TradeEvent",
            "instId": "BTC-USDT-SWAP",
            "tradeId": "1",
            "fillSz": "1",
            "fillPx": "100",
        }
    )
    first = api.poll_event("OKX___SWAP")
    second = api.poll_event("OKX___SWAP")
    assert first["kind"] == "orderbook" and first["timestamp"] == 1700000000
    assert first["bids"] == [(100, 2)] and second["kind"] == "trade"
    assert api.poll_event("OKX___SWAP") is None


def _book(symbol, sequence):
    return {
        "kind": "orderbook",
        "symbol": symbol,
        "timestamp": 1700000000 + sequence,
        "bids": [[100 + sequence, 1]],
        "asks": [[101 + sequence, 1]],
        "sequence": sequence,
    }


def test_poll_events_coalesces_complete_books_per_symbol_and_keeps_barriers(api):
    attach(api, "OKX___SWAP")
    source = api.data_queues["OKX___SWAP"]
    source.put(_book("BTC-USDT-SWAP", 1))
    source.put(_book("ETH-USDT-SWAP", 2))
    source.put(_book("BTC-USDT-SWAP", 3))
    source.put(
        {
            "kind": "order",
            "symbol": "BTC-USDT-SWAP",
            "order_id": "order-1",
            "client_order_id": "client-1",
            "status": "accepted",
            "side": "buy",
        }
    )
    source.put(_book("BTC-USDT-SWAP", 4))
    source.put(_book("BTC-USDT-SWAP", 5))
    source.put(
        {
            "kind": "trade",
            "symbol": "BTC-USDT-SWAP",
            "order_id": "order-1",
            "trade_id": "trade-1",
            "side": "buy",
            "size": 1,
            "price": 105,
        }
    )
    source.put({"kind": "account", "currency": "USDT", "cash": 10, "value": 11})
    source.put(
        {
            "kind": "position",
            "symbol": "BTC-USDT-SWAP",
            "position_side": "long",
            "size": 1,
            "price": 105,
        }
    )

    events = api.poll_events(
        "OKX___SWAP",
        max_raw_items=None,
        coalesce_market_snapshots=("orderbook",),
    )

    assert [(event["kind"], event.get("symbol"), event.get("sequence")) for event in events] == [
        ("orderbook", "ETH-USDT-SWAP", 2),
        ("orderbook", "BTC-USDT-SWAP", 3),
        ("order", "BTC-USDT-SWAP", None),
        ("orderbook", "BTC-USDT-SWAP", 5),
        ("trade", "BTC-USDT-SWAP", None),
        ("account", None, None),
        ("position", "BTC-USDT-SWAP", None),
    ]
    assert events[2]["order_id"] == "order-1"
    assert events[4]["trade_id"] == "trade-1"
    assert events[1]["coalesced_count"] == 1
    assert events[3]["coalesced_count"] == 1
    assert api.get_event_metrics() == {
        "raw_ingress_items": 9,
        "normalized_events": 9,
        "delivered_events": 7,
        "coalesced_events": 2,
    }


def test_poll_events_never_coalesces_depth_deltas_or_gap_markers(api):
    attach(api, "OKX___SWAP")
    source = api.data_queues["OKX___SWAP"]
    source.put(
        {
            **_book("BTC-USDT-SWAP", 1),
            "snapshot_or_delta": "delta",
            "continuity_status": "continuous",
        }
    )
    source.put(
        {
            **_book("BTC-USDT-SWAP", 3),
            "snapshot_or_delta": "delta",
            "previous_sequence": 1,
            "continuity_status": "gap",
            "stale": True,
        }
    )

    events = api.poll_events(
        "OKX___SWAP",
        max_raw_items=None,
        coalesce_market_snapshots=("orderbook",),
    )

    assert [event["continuity_status"] for event in events] == ["continuous", "gap"]
    assert events[1]["stale"]
    assert api.get_event_metrics()["coalesced_events"] == 0


def test_poll_events_none_uses_finite_direct_queue_snapshot(api):
    class ProducerQueue(Queue):
        def __init__(self):
            super().__init__()
            self.injected = False

        def get_nowait(self):
            item = super().get_nowait()
            if not self.injected:
                self.injected = True
                self.put(_book("BTC-USDT-SWAP", 3))
            return item

    attach(api, "OKX___SWAP")
    source = ProducerQueue()
    source.put(_book("BTC-USDT-SWAP", 1))
    source.put(_book("BTC-USDT-SWAP", 2))
    api.data_queues["OKX___SWAP"] = source

    first = api.poll_events(
        "OKX___SWAP",
        max_raw_items=None,
        coalesce_market_snapshots=("orderbook",),
    )

    assert [event["sequence"] for event in first] == [2]
    assert source.qsize() == 1
    assert (
        api.poll_events(
            "OKX___SWAP",
            max_raw_items=None,
            coalesce_market_snapshots=("orderbook",),
        )[0]["sequence"]
        == 3
    )


@pytest.mark.parametrize("value", [-1, 1.5, True, "10"])
def test_poll_events_rejects_invalid_raw_item_bound(api, value):
    attach(api, "OKX___SWAP")
    with pytest.raises(ValueError, match="max_raw_items"):
        api.poll_events("OKX___SWAP", max_raw_items=value)


@pytest.mark.parametrize("value", ["orderbook", ("bar",), ("",), 1])
def test_poll_events_rejects_invalid_snapshot_kinds(api, value):
    attach(api, "OKX___SWAP")
    with pytest.raises(ValueError, match="coalesce_market_snapshots"):
        api.poll_events("OKX___SWAP", coalesce_market_snapshots=value)


def test_poll_events_zero_bound_leaves_raw_queue_untouched(api):
    attach(api, "OKX___SWAP")
    api.data_queues["OKX___SWAP"].put(_book("BTC-USDT-SWAP", 1))

    assert api.poll_events("OKX___SWAP", max_raw_items=0) == []
    assert api.data_queues["OKX___SWAP"].qsize() == 1


def test_poll_events_default_scans_at_most_one_hundred_raw_items(api):
    attach(api, "OKX___SWAP")
    source = api.data_queues["OKX___SWAP"]
    for sequence in range(101):
        source.put(_book("BTC-USDT-SWAP", sequence + 1))

    events = api.poll_events("OKX___SWAP")

    assert len(events) == 100
    assert events[-1]["sequence"] == 100
    assert source.qsize() == 1


def test_zmq_initialization_never_creates_direct_feed_and_lifecycle_routes(monkeypatch):
    monkeypatch.setattr("bt_api_py.bt_api._ensure_plugins_loaded", lambda: None)
    monkeypatch.setattr(
        "bt_api_py.bt_api.ExchangeRegistry.create_feed",
        Mock(side_effect=AssertionError("direct feed")),
    )
    config = ForwardingConfig(
        command_endpoint="inproc://c",
        market_endpoint="inproc://m",
        private_endpoint="inproc://p",
        account_id="a",
        strategy_id="s",
    )
    api = BtApi({"MT5___FX": {}}, debug=False, transport_mode="zmq", forwarding_config=config)
    client = SimpleNamespace(
        subscribe=Mock(),
        disconnect=Mock(),
        poll_broker_update=Mock(return_value=None),
        poll_orderbook=Mock(
            return_value={"bids": [[1, 2]], "asks": [[2, 3]], "timestamp": 1700000000}
        ),
    )
    api._backend._client = client
    api.subscribe("MT5___FX___EURUSD", [{"topic": "depth"}])
    assert api.list_exchanges() == ["MT5___FX"] and not api.exchange_feeds
    assert api.poll_event("MT5___FX")["quantity_unit"] == "lots"
    api.close()
    client.disconnect.assert_called_once()


def test_zmq_native_intent_rejected_before_dispatch():
    from bt_api_py.forwarding.btapi_backend import ZmqBtApiBackend

    backend = ZmqBtApiBackend(
        ForwardingConfig(
            command_endpoint="inproc://c",
            market_endpoint="inproc://m",
            private_endpoint="inproc://p",
            account_id="a",
            strategy_id="s",
        )
    )
    backend._client = SimpleNamespace(_send_command_sync=Mock())
    with pytest.raises(CapabilityNotSupportedError):
        backend.make_order(
            "MT5___FX",
            request(quantity_unit="base", position_id="position-10", offset="close"),
        )
    assert not backend._client._send_command_sync.called
    with pytest.raises(CapabilityNotSupportedError):
        backend.query_order(
            "BINANCE___SWAP",
            QueryOrderRequest(symbol="BTCUSDT", account_id="a", client_order_id="client-1"),
        )
    assert not backend._client._send_command_sync.called


@pytest.mark.parametrize(
    "fill_qty,currency,expected",
    [(0.01, "USDT", 0.1), (0.005, "USDT", None), (0.01, None, None)],
)
def test_binance_terminal_fees_need_complete_deduplicated_fills(api, fill_qty, currency, expected):
    api._backend.query_order = Mock(
        return_value={
            "symbol": "BTCUSDT",
            "orderId": 42,
            "status": "FILLED",
            "executedQty": ".01",
            "avgPrice": "50000",
        }
    )
    fill = {
        "symbol": "BTCUSDT",
        "id": 9,
        "orderId": 42,
        "qty": str(fill_qty),
        "price": "50000",
        "commission": ".1",
        "commissionAsset": currency,
    }
    api._backend.get_deals = Mock(return_value=[fill, fill])
    row = api.query_order(
        "BINANCE___SWAP",
        QueryOrderRequest(symbol="BTCUSDT", account_id="demo", order_id="42"),
        normalized=True,
    )
    assert row.get("cumulative_commission") == expected
    assert row["terminal_confirmed"]
    if expected is not None:
        assert row["commission_currency"] == "USDT" and row["commission_source"] == "exchange"


def test_foreign_fee_currency_is_explicit_not_converted():
    row = normalize_result(
        "query_order",
        {
            "ordId": "1",
            "state": "filled",
            "accFillSz": "1",
            "avgPx": "50000",
            "fee": "-.00001",
            "feeCcy": "BTC",
        },
        "OKX___SWAP",
    )
    assert row["cumulative_commission"] == 0.00001 and row["commission_currency"] == "BTC"


def test_forwarding_extra_roundtrip_reaches_existing_gateway_runtime():
    from bt_api_base.gateway.config import GatewayConfig
    from bt_api_base.gateway.runtime import GatewayRuntime

    from bt_api_py.forwarding.btapi_backend import ZmqBtApiBackend
    from bt_api_py.forwarding.schema import OrderCommand

    config = ForwardingConfig(
        command_endpoint="inproc://c",
        market_endpoint="inproc://m",
        private_endpoint="inproc://p",
        account_id="a",
        strategy_id="s",
    )
    runtime = GatewayRuntime(
        GatewayConfig(exchange_type="MT5", asset_type="FX", account_id="a", enable_trading=True)
    )
    runtime.adapter = SimpleNamespace(
        place_order=Mock(
            return_value={
                "order_id": "order-22",
                "position_id": "position-10",
                "trade_id": "deal-23",
                "status": "completed",
                "volume": 0.01,
                "price": 1.12,
            }
        )
    )

    class Client:
        def _send_command_sync(self, command):
            wire = OrderCommand.from_dict(command.to_dict())
            assert (
                wire.extra == command.extra
                and wire.request_fingerprint == command.request_fingerprint
            )
            return runtime._handle_command(wire)

    backend = ZmqBtApiBackend(config)
    backend._client = Client()
    req = OrderRequest(
        symbol="EURUSD",
        side=Side.SELL,
        order_type=OrderType.MARKET,
        quantity=Decimal(".01"),
        quantity_unit="lots",
        account_id="a",
        client_order_id="123456789012",
        position_id="position-10",
        position_side="long",
        offset="close",
        reduce_only=True,
    )
    result = backend.make_order("MT5___FX", req)
    payload = runtime.adapter.place_order.call_args.args[0]
    assert payload["quantity_unit"] == "lots" and payload["size"] == "0.01"
    assert payload["position_id"] == "position-10" and payload["offset"] == "close"
    assert payload["reduce_only"] is True and payload["position_side"] == "long"
    assert result.order_id == "order-22"
    normalized = normalize_result("make_order", result, "MT5___FX", request=req)
    assert normalized["filled"] == 0.01 and normalized["position_id"] == "position-10"


def test_ctp_cancel_references_survive_wire_and_runtime_payload():
    from bt_api_base.gateway.config import GatewayConfig
    from bt_api_base.gateway.runtime import GatewayRuntime

    from bt_api_py.forwarding.btapi_backend import ZmqBtApiBackend
    from bt_api_py.forwarding.schema import OrderCommand

    runtime = GatewayRuntime(
        GatewayConfig(exchange_type="CTP", asset_type="FUTURE", account_id="a", enable_trading=True)
    )
    runtime.adapter = SimpleNamespace(
        cancel_order=Mock(return_value={"order_id": "sys-9", "status": "canceled"})
    )

    class Client:
        def _send_command_sync(self, command):
            return runtime._handle_command(OrderCommand.from_dict(command.to_dict()))

    backend = ZmqBtApiBackend(
        ForwardingConfig(
            command_endpoint="inproc://c",
            market_endpoint="inproc://m",
            private_endpoint="inproc://p",
            account_id="a",
            strategy_id="s",
        )
    )
    backend._client = Client()
    backend.cancel_order(
        "CTP___FUTURE",
        CancelOrderRequest(
            symbol="rb2701",
            account_id="a",
            order_id="sys-9",
            client_order_id="123456789012",
            order_ref="123456789012",
            front_id=3,
            session_id=4,
            exchange_id="SHFE",
        ),
    )
    payload = runtime.adapter.cancel_order.call_args.args[0]
    assert payload["order_id"] == "sys-9" and payload["order_ref"] == "123456789012"
    assert (
        payload["front_id"] == 3 and payload["session_id"] == 4 and payload["exchange_id"] == "SHFE"
    )


@pytest.mark.parametrize(
    "exchange,raw,unit",
    [
        (
            "MT5___FX",
            {
                "instrument": "EURUSD",
                "volume": 0.02,
                "direction": "short",
                "position_id": "pos-9",
                "price": 1.12,
            },
            "lots",
        ),
        (
            "CTP___FUTURE",
            {
                "instrument": "rb2701",
                "volume": 3,
                "direction": "long",
                "today_position": 1,
                "yd_position": 2,
                "price": 3400,
            },
            "contracts",
        ),
    ],
)
def test_native_forwarding_position_snapshot_preserves_volume_and_identity(exchange, raw, unit):
    from datetime import UTC, datetime

    from bt_api_py._contracts.models import Freshness
    from bt_api_py.forwarding.btapi_backend import ZmqBtApiBackend

    backend = ZmqBtApiBackend(
        ForwardingConfig(
            command_endpoint="inproc://c",
            market_endpoint="inproc://m",
            private_endpoint="inproc://p",
            account_id="a",
            strategy_id="s",
        )
    )
    snapshots = backend._positions_from_payloads([raw], Freshness("live", datetime.now(UTC)))
    result = normalize_result("get_position", snapshots, exchange)[0]
    assert result["symbol"] == raw["instrument"] and result["size"] == raw["volume"]
    assert result["quantity_unit"] == unit and result["position_side"] == raw["direction"]
    if exchange.startswith("MT5"):
        assert result["position_id"] == "pos-9"
    else:
        assert result["today"] == 1 and result["yesterday"] == 2


@pytest.mark.parametrize("raw_quantity", [False, True])
def test_normalized_position_rejects_boolean_quantity(raw_quantity):
    with pytest.raises(ValueError, match="boolean_is_not_numeric"):
        normalize_result(
            "get_position",
            [{"instId": "BTC-USDT-SWAP", "pos": raw_quantity, "posSide": "long"}],
            "OKX___SWAP",
        )


@pytest.mark.parametrize("operation", ["query_order", "get_deals"])
def test_native_gateway_unimplemented_reconciliation_is_explicit(operation):
    from bt_api_py.forwarding.btapi_backend import ZmqBtApiBackend

    backend = ZmqBtApiBackend(
        ForwardingConfig(
            command_endpoint="inproc://c",
            market_endpoint="inproc://m",
            private_endpoint="inproc://p",
            account_id="a",
            strategy_id="s",
        )
    )
    assert backend.get_capabilities("MT5___FX")[operation] is False
    args = (
        (QueryOrderRequest(symbol="EURUSD", account_id="a", order_id="9"),)
        if operation == "query_order"
        else ()
    )
    with pytest.raises(CapabilityNotSupportedError):
        getattr(backend, operation)("MT5___FX", *args)


def test_requestdata_default_normalizer_unwraps_exchangeinfo_symbols(api):
    from bt_api_base.containers.requestdatas.request_data import RequestData

    raw = {
        "timezone": "UTC",
        "symbols": [
            {
                "symbol": "ETHUSDT",
                "contractType": "PERPETUAL",
                "baseAsset": "ETH",
                "marginAsset": "USDT",
            },
            {
                "symbol": "BTCUSDT",
                "contractType": "PERPETUAL",
                "baseAsset": "BTC",
                "marginAsset": "USDT",
                "filters": [
                    {"filterType": "LOT_SIZE", "stepSize": ".001", "minQty": ".001"},
                    {"filterType": "PRICE_FILTER", "tickSize": ".1"},
                ],
            },
        ],
    }
    response = RequestData(raw, {})
    attach(api, "BINANCE___SWAP", get_exchange_info=Mock(return_value=response))
    result = api.get_exchange_info("BINANCE___SWAP", "BTCUSDT", normalized=True)
    assert result["base_currency"] == "BTC" and result["settlement_currency"] == "USDT"
    assert result["linear"] is True and result["lot_size"] == 0.001 and result["tick_size"] == 0.1
    assert len(api.get_exchange_info("BINANCE___SWAP", normalized=True)) == 2


def test_partial_okx_account_push_preserves_currencies_and_following_book(api):
    from bt_api_okx.containers.accounts.okx_account import OkxAccountData

    # Private account pushes may omit aggregate imr/totalEq. The legacy total
    # available margin getter then raises TypeError despite valid detail rows.
    push = OkxAccountData(
        {
            "uTime": "1700000000000",
            "details": [
                {"ccy": "USDT", "eq": "1000", "availBal": "900"},
                {"ccy": "BTC", "eq": "1", "availBal": ""},
            ],
        },
        "ANY",
        "SWAP",
        True,
    )
    attach(api, "OKX___SWAP")
    queue = api.data_queues["OKX___SWAP"]
    queue.put(push)
    queue.put(
        {
            "kind": "orderbook",
            "symbol": "BTC-USDT-SWAP",
            "bids": [[100, 1]],
            "asks": [[101, 2]],
        }
    )
    event = api.poll_event("OKX___SWAP")
    assert event["kind"] == "account" and event["partial"] is True
    assert "cash" not in event and "value" not in event
    assert [(item["currency"], item["value"]) for item in event["balances"]] == [
        ("USDT", 1000),
        ("BTC", 1),
    ]
    assert event["balances"][1]["cash"] is None
    assert api.poll_event("OKX___SWAP")["kind"] == "orderbook"


def test_partial_account_event_does_not_relax_rest_account_validation(api):
    from bt_api_okx.containers.accounts.okx_account import OkxAccountData

    push = OkxAccountData(
        {"details": [{"ccy": "USDT", "eq": "1000", "availBal": ""}]},
        "ANY",
        "SWAP",
        True,
    )
    event = normalize_event(push, "OKX___SWAP")
    assert event["currency"] == "USDT" and event["cash"] is None
    attach(api, "OKX___SWAP", get_account=Mock(return_value=push))
    with pytest.raises(NormalizedApiError):
        api.get_account("OKX___SWAP", "USDT", normalized=True)


@pytest.mark.parametrize(
    "permission,expected",
    [
        ("read_only", False),
        ("read_only,trade", True),
        ("read_only,withdraw", False),
        ("", None),
        (None, None),
        ("future_unknown_permission", None),
    ],
)
@pytest.mark.parametrize("operation", ["get_position_mode", "get_account_config"])
def test_okx_config_permission_is_explicit_and_unknown_is_not_pass(
    api, permission, expected, operation
):
    raw = {
        "code": "0",
        "data": [
            {
                "posMode": "long_short_mode",
                "perm": permission,
                "apiKey": "DO_NOT_RETURN",
            }
        ],
    }
    attach(api, "OKX___SWAP", get_config=Mock(return_value=raw))
    result = getattr(api, operation)("OKX___SWAP", normalized=True)
    assert result["can_trade"] is expected and result["position_mode"] == "dual_side"
    if expected is not None:
        assert result["trading_permissions"] == sorted(permission.split(","))
    else:
        assert result["trading_permissions"] is None
    assert "DO_NOT_RETURN" not in str(result) and "apiKey" not in result


def test_account_disabled_overrides_api_trade_permission():
    result = normalize_result(
        "get_position_mode",
        {"position_mode": "net", "canTrade": False, "perm": "read_only,trade"},
        "OKX___SWAP",
    )
    assert result["can_trade"] is False
    unknown = normalize_result("get_position_mode", {"dualSidePosition": False}, "BINANCE___SWAP")
    assert unknown["can_trade"] is None and unknown["trading_permissions"] is None


@pytest.mark.parametrize(
    "side,position_side,expected_side,expected_position",
    [
        ("BUY", "BOTH", "buy", "net"),
        ("SELL", "BOTH", "sell", "net"),
        ("BUY", "LONG", "buy", "long"),
        ("SELL", "SHORT", "sell", "short"),
    ],
)
def test_real_binance_swap_trade_container_uses_native_transaction_side(
    side, position_side, expected_side, expected_position
):
    from bt_api_binance.containers.orders.binance_order import BinanceSwapWssOrderData
    from bt_api_binance.containers.trades.binance_trade import BinanceSwapWssTradeData

    raw = {
        "e": "ORDER_TRADE_UPDATE",
        "E": 1700000000000,
        "T": 1700000000000,
        "o": {
            "s": "BTCUSDT",
            "S": side,
            "ps": position_side,
            "i": 123,
            "c": "client-1",
            "t": 456,
            "l": ".002",
            "z": ".002",
            "L": "65000",
            "ap": "65000",
            "q": ".002",
            "p": "65001",
            "X": "FILLED",
            "x": "TRADE",
            "o": "LIMIT",
            "f": "IOC",
            "m": False,
            "n": ".05",
            "N": "USDT",
            "T": 1700000000000,
        },
    }
    container = BinanceSwapWssTradeData(raw, "BTCUSDT", "SWAP", True)
    container.init_data()
    # Regression: the old container getter confuses ps with transaction side.
    assert container.get_trade_side() == position_side
    trade = normalize_event(container, "BINANCE___SWAP")
    assert trade["kind"] == "trade" and trade["side"] == expected_side
    assert trade["position_side"] == expected_position and trade["size"] == 0.002
    assert trade["price"] == 65000 and trade["fee"] == 0.05 and trade["trade_id"] == "456"
    order = normalize_event(BinanceSwapWssOrderData(raw, "BTCUSDT", "SWAP", True), "BINANCE___SWAP")
    assert order["side"] == expected_side and order["position_side"] == expected_position
    assert order["filled"] == 0.002 and order["avg_price"] == 65000


def test_unknown_transaction_side_is_not_invented_from_position_side():
    trade = normalize_event(
        {
            "kind": "trade",
            "symbol": "BTCUSDT",
            "side": "BOTH",
            "position_side": "BOTH",
            "size": 0.002,
            "price": 65000,
        },
        "BINANCE___SWAP",
    )
    assert trade["side"] is None and trade["position_side"] == "net"
