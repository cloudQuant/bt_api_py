import queue
import random
import time

import pytest

# from bt_api_py.containers.positions.binance_position import BinanceWssPositionData
from test_live_binance_swap_request_data import init_req_feed

from bt_api_py.containers.accounts.binance_account import BinanceSwapWssAccountData
from bt_api_py.containers.bars.binance_bar import BinanceWssBarData
from bt_api_py.containers.exchanges.binance_exchange_data import BinanceExchangeDataSwap
from bt_api_py.containers.fundingrates.binance_funding_rate import BinanceWssFundingRateData
from bt_api_py.containers.markprices.binance_mark_price import BinanceWssMarkPriceData
from bt_api_py.containers.orderbooks.binance_orderbook import BinanceWssOrderBookData
from bt_api_py.containers.orders.binance_order import BinanceForceOrderData, BinanceSwapWssOrderData
from bt_api_py.containers.tickers.binance_ticker import BinanceWssTickerData
from bt_api_py.containers.trades.binance_trade import BinanceAggTradeData
from bt_api_py.feeds.live_binance_feed import BinanceAccountWssDataSwap, BinanceMarketWssDataSwap
from bt_api_py.functions.utils import read_account_config


def _make_wss_kwargs(topics, wss_name="test_market_data"):
    data = read_account_config()
    return {
        "public_key": data["binance"]["public_key"],
        "private_key": data["binance"]["private_key"],
        "topics": topics,
        "wss_name": wss_name,
        "wss_url": "wss://fstream.binance.com/ws",
        "proxies": data.get("proxies"),
        "async_proxy": data.get("async_proxy"),
    }


def _collect_wss_data(data_queue, wait_seconds=20, max_items=50000):
    time.sleep(wait_seconds)
    items = []
    count = 0
    while count < max_items:
        count += 1
        try:
            items.append(data_queue.get(False))
        except queue.Empty:
            break
    return items


@pytest.mark.timeout(60)
def test_binance_wss_data_feed():
    data_queue = queue.Queue()
    kwargs = _make_wss_kwargs(
        [
            {"topic": "ticker", "symbol": "BTC-USDT"},
            {"topic": "depth", "symbol": "BTC-USDT"},
            {"topic": "funding_rate", "symbol": "BTC-USDT"},
            {"topic": "mark_price", "symbol": "BTC-USDT"},
            {"topic": "kline", "symbol": "BTC-USDT", "period": "1m"},
            {"topic": "kline", "symbol": "ETH-USDT", "period": "1m"},
            {"topic": "agg_trade", "symbol": "BTC-USDT"},
            {"topic": "force_order", "symbol": "BTC-USDT"},
        ]
    )
    BinanceMarketWssDataSwap(data_queue, **kwargs).start()
    items = _collect_wss_data(data_queue, wait_seconds=20)

    receive_binance_bar_data = False
    receive_binance_ticker_data = False
    receive_binance_order_book_data = False
    receive_binance_mark_price_data = False
    receive_binance_funding_rate_data = False
    receive_binance_force_order_data = False
    receive_binance_agg_trade_data = False
    for data in items:
        if isinstance(data, BinanceWssBarData):
            receive_binance_bar_data = True
        if isinstance(data, BinanceWssTickerData):
            receive_binance_ticker_data = True
        if isinstance(data, BinanceWssOrderBookData):
            receive_binance_order_book_data = True
        if isinstance(data, BinanceWssFundingRateData):
            receive_binance_funding_rate_data = True
        if isinstance(data, BinanceWssMarkPriceData):
            receive_binance_mark_price_data = True
        if isinstance(data, BinanceForceOrderData):
            receive_binance_force_order_data = True
            data.init_data()
            print(data.get_all_data())
        if isinstance(data, BinanceAggTradeData):
            receive_binance_agg_trade_data = True
            data.init_data()
            print(data.get_all_data())

    assert receive_binance_bar_data is True
    assert receive_binance_ticker_data is True
    assert receive_binance_order_book_data is True
    assert receive_binance_funding_rate_data is True
    assert receive_binance_mark_price_data is True
    assert receive_binance_agg_trade_data is True
    # assert receive_binance_force_order_data is True


def test_binance_wss_mini_ticker():
    data_queue = queue.Queue()
    kwargs = _make_wss_kwargs(
        [
            {"topic": "mini_ticker", "symbol": "BTC-USDT"},
        ],
        wss_name="test_mini_ticker",
    )
    BinanceMarketWssDataSwap(data_queue, **kwargs).start()
    items = _collect_wss_data(data_queue, wait_seconds=10)
    assert len(items) > 0, "mini_ticker should receive data within 10s"


def test_binance_wss_all_mini_ticker():
    data_queue = queue.Queue()
    kwargs = _make_wss_kwargs(
        [
            {"topic": "all_mini_ticker"},
        ],
        wss_name="test_all_mini_ticker",
    )
    BinanceMarketWssDataSwap(data_queue, **kwargs).start()
    items = _collect_wss_data(data_queue, wait_seconds=10)
    assert len(items) > 0, "all_mini_ticker should receive data within 10s"


def test_binance_wss_all_book_ticker():
    data_queue = queue.Queue()
    kwargs = _make_wss_kwargs(
        [
            {"topic": "all_book_ticker"},
        ],
        wss_name="test_all_book_ticker",
    )
    BinanceMarketWssDataSwap(data_queue, **kwargs).start()
    items = _collect_wss_data(data_queue, wait_seconds=10)
    received_ticker = any(isinstance(d, BinanceWssTickerData) for d in items)
    assert received_ticker is True, "all_book_ticker should push BinanceWssTickerData"


@pytest.mark.timeout(120)
def test_binance_wss_continuous_kline():
    data_queue = queue.Queue()
    kwargs = _make_wss_kwargs(
        [
            {"topic": "continuous_kline", "pair": "BTC-USDT", "period": "1m"},
        ],
        wss_name="test_continuous_kline",
    )
    BinanceMarketWssDataSwap(data_queue, **kwargs).start()
    items = _collect_wss_data(data_queue, wait_seconds=65)
    received_bar = any(isinstance(d, BinanceWssBarData) for d in items)
    assert received_bar is True, "continuous_kline should push BinanceWssBarData within 65s"


def test_binance_wss_contract_info():
    data_queue = queue.Queue()
    kwargs = _make_wss_kwargs(
        [
            {"topic": "contract_info"},
        ],
        wss_name="test_contract_info",
    )
    BinanceMarketWssDataSwap(data_queue, **kwargs).start()
    items = _collect_wss_data(data_queue, wait_seconds=10)
    # contract_info events are rare (only on symbol updates), so we just verify subscription works
    print(f"contract_info received {len(items)} items (may be 0 if no contract changes)")


def test_binance_wss_liquidation():
    data_queue = queue.Queue()
    kwargs = _make_wss_kwargs(
        [
            {"topic": "liquidation", "symbol": "BTC-USDT"},
        ],
        wss_name="test_liquidation",
    )
    BinanceMarketWssDataSwap(data_queue, **kwargs).start()
    items = _collect_wss_data(data_queue, wait_seconds=10)
    # liquidation events are rare, just verify subscription doesn't error
    received_force_order = any(isinstance(d, BinanceForceOrderData) for d in items)
    print(f"liquidation received {len(items)} items, force_order={received_force_order}")


def test_binance_wss_all_mark_price():
    data_queue = queue.Queue()
    kwargs = _make_wss_kwargs(
        [
            {"topic": "all_mark_price"},
        ],
        wss_name="test_all_mark_price",
    )
    BinanceMarketWssDataSwap(data_queue, **kwargs).start()
    items = _collect_wss_data(data_queue, wait_seconds=5)
    received_mark = any(isinstance(d, BinanceWssMarkPriceData) for d in items)
    assert received_mark is True, "all_mark_price should push BinanceWssMarkPriceData"


def test_binance_wss_all_ticker():
    data_queue = queue.Queue()
    kwargs = _make_wss_kwargs(
        [
            {"topic": "all_ticker"},
        ],
        wss_name="test_all_ticker",
    )
    BinanceMarketWssDataSwap(data_queue, **kwargs).start()
    items = _collect_wss_data(data_queue, wait_seconds=5)
    assert len(items) > 0, "all_ticker should receive data within 5s"


@pytest.mark.timeout(60)
def test_binance_wss_all_force_order():
    data_queue = queue.Queue()
    kwargs = _make_wss_kwargs(
        [
            {"topic": "all_force_order"},
        ],
        wss_name="test_all_force_order",
    )
    BinanceMarketWssDataSwap(data_queue, **kwargs).start()
    items = _collect_wss_data(data_queue, wait_seconds=15)
    # force_order events may be sparse, just verify no crash
    received = any(isinstance(d, BinanceForceOrderData) for d in items)
    print(f"all_force_order received {len(items)} items, has_force_order={received}")


def test_binance_wss_depth_with_symbol_list():
    data_queue = queue.Queue()
    kwargs = _make_wss_kwargs(
        [
            {"topic": "depth", "symbol_list": ["BTC-USDT", "ETH-USDT"]},
        ],
        wss_name="test_depth_symbol_list",
    )
    BinanceMarketWssDataSwap(data_queue, **kwargs).start()
    items = _collect_wss_data(data_queue, wait_seconds=5)
    received_ob = any(isinstance(d, BinanceWssOrderBookData) for d in items)
    assert received_ob is True, "depth with symbol_list should push BinanceWssOrderBookData"


def test_binance_wss_agg_trade_with_symbol_list():
    data_queue = queue.Queue()
    kwargs = _make_wss_kwargs(
        [
            {"topic": "agg_trade", "symbol_list": ["BTC-USDT", "ETH-USDT"]},
        ],
        wss_name="test_agg_trade_symbol_list",
    )
    BinanceMarketWssDataSwap(data_queue, **kwargs).start()
    items = _collect_wss_data(data_queue, wait_seconds=10)
    received_agg = any(isinstance(d, BinanceAggTradeData) for d in items)
    assert received_agg is True, "agg_trade with symbol_list should push BinanceAggTradeData"


def test_get_binance_account_data_feed():
    data_queue = queue.Queue()
    data = read_account_config()
    kwargs = {
        "public_key": data["binance"]["public_key"],
        "private_key": data["binance"]["private_key"],
        "topics": [
            {"topic": "account"},
            {"topic": "order"},
            {"topic": "trade"},
        ],
        "wss_name": "test_market_data",
        "wss_url": "wss://fstream.binance.com/ws",
        "exchange_data": BinanceExchangeDataSwap(),
        "proxies": data.get("proxies"),
        "async_proxy": data.get("async_proxy"),
    }
    BinanceAccountWssDataSwap(data_queue, **kwargs).start()
    time.sleep(3)
    receive_binance_account_data = False
    # receive_binance_position_data = False
    receive_binance_order_data = False
    # 下单撤单测试订单功能
    live_binance_swap_feed = init_req_feed()
    price_data = live_binance_swap_feed.get_tick("OP-USDT")
    price_data = price_data.get_data()[0].init_data()
    ask_price = round(price_data.get_ask_price() * 1.1, 4)
    bid_price = round(price_data.get_ask_price() * 0.9, 4)
    random_number = random.randint(10**17, 10**18 - 1)
    buy_client_order_id = str(random_number - 1)
    sell_client_order_id = str(random_number + 1)
    lots = 0
    while lots * bid_price < 10:
        lots += 1
    buy_data = live_binance_swap_feed.make_order(
        "OP-USDT",
        lots,
        bid_price,
        "buy-limit",
        client_order_id=buy_client_order_id,
        **{"position_side": "LONG"},
    )

    print(buy_data.get_data()[0].init_data())
    sell_data = live_binance_swap_feed.make_order(
        "OP-USDT",
        lots,
        ask_price,
        "sell-limit",
        client_order_id=sell_client_order_id,
        **{"position_side": "SHORT"},
    )

    print(sell_data.get_data()[0].init_data())
    # data = live_binance_swap_feed.query_order("OP-USDT", client_order_id=sell_client_order_id)
    # print("下单数据", data.get_data())
    cancel_buy_data = live_binance_swap_feed.cancel_order(
        "OP-USDT", client_order_id=buy_client_order_id
    )
    print("撤单数据", cancel_buy_data.get_data()[0].init_data())
    cancel_sell_data = live_binance_swap_feed.cancel_order(
        "OP-USDT", client_order_id=sell_client_order_id
    )
    print("撤单数据", cancel_sell_data.get_data()[0].init_data())
    time.sleep(5)
    count = 1
    while True:
        time.sleep(0.01)
        count += 1
        try:
            data = data_queue.get(False)
        except queue.Empty:
            break
        if count > 10000:
            break
        if isinstance(data, BinanceSwapWssAccountData):
            receive_binance_account_data = True
        # if isinstance(data, BinancePositionData):
        #     receive_binance_position_data = True
        if isinstance(data, BinanceSwapWssOrderData):
            receive_binance_order_data = True

    # assert receive_binance_account_data is True
    # assert receive_binance_position_data is True
    assert receive_binance_order_data is True


if __name__ == "__main__":
    test_binance_wss_data_feed()
    # test_get_binance_account_data_feed()
    # --- New WSS topic tests ---
    # test_binance_wss_mini_ticker()
    # test_binance_wss_all_mini_ticker()
    # test_binance_wss_all_book_ticker()
    # test_binance_wss_continuous_kline()
    # test_binance_wss_contract_info()
    # test_binance_wss_liquidation()
    # test_binance_wss_all_mark_price()
    # test_binance_wss_all_ticker()
    # test_binance_wss_all_force_order()
    # test_binance_wss_depth_with_symbol_list()
    # test_binance_wss_agg_trade_with_symbol_list()
