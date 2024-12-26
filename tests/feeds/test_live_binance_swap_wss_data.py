import queue
import time
import random
from btpy.functions.utils import read_yaml_file
from btpy.feeds.live_binance_feed import BinanceMarketWssDataSwap, BinanceAccountWssDataSwap
from btpy.containers.exchanges.binance_exchange_data import BinanceExchangeDataSwap
from btpy.containers.bars.binance_bar import BinanceWssBarData
from btpy.containers.markprices.binance_mark_price import BinanceWssMarkPriceData
from btpy.containers.tickers.binance_ticker import BinanceWssTickerData
from btpy.containers.orderbooks.binance_orderbook import BinanceWssOrderBookData
from btpy.containers.fundingrates.binance_funding_rate import BinanceWssFundingRateData
from btpy.containers.accounts.binance_account import BinanceSwapWssAccountData
from btpy.containers.orders.binance_order import BinanceSwapWssOrderData, BinanceForceOrderData
from btpy.containers.trades.binance_trade import BinanceAggTradeData
# from btpy.containers.positions.binance_position import BinanceWssPositionData
from test_live_binance_swap_request_data import init_req_feed


def test_binance_wss_data_feed():
    data_queue = queue.Queue()
    data = read_yaml_file("account_config.yaml")
    kwargs = {
        "public_key": data['binance']['public_key'],
        "private_key": data['binance']['private_key'],
        'topics': [
            {"topic": "ticker", "symbol": "BTC-USDT"},
            {"topic": "depth", "symbol": "BTC-USDT"},
            {"topic": "funding_rate", "symbol": "BTC-USDT"},
            {"topic": "mark_price", "symbol": "BTC-USDT"},
            {"topic": "kline", "symbol": "BTC-USDT", "period": "1m"},
            {"topic": "kline", "symbol": "ETH-USDT", "period": "1m"},
            {"topic": "agg_trade", "symbol": "BTC-USDT"},
            {"topic": "force_order", "symbol": "BTC-USDT"},
        ],
        "wss_name": "test_market_data",
        "wss_url": 'wss://fstream.binance.com/ws',
        "exchange_data": BinanceExchangeDataSwap()
    }
    BinanceMarketWssDataSwap(data_queue, **kwargs).start()
    time.sleep(20)
    # restart操作
    receive_binance_bar_data = False
    receive_binance_ticker_data = False
    receive_binance_order_book_data = False
    receive_binance_mark_price_data = False
    receive_binance_funding_rate_data = False
    receive_binance_force_order_data = False
    receive_binance_agg_trade_data = False
    count = 1
    while True:
        count += 1
        try:
            data = data_queue.get(False)
        except queue.Empty:
            break
        if count > 10000:
            break
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


def test_get_binance_account_data_feed():
    data_queue = queue.Queue()
    data = read_yaml_file("account_config.yaml")
    kwargs = {
        "public_key": data['binance']['public_key'],
        "private_key": data['binance']['private_key'],
        'topics': [
            {"topic": "account"},
            {"topic": "order"},
            {"topic": "trade"},
        ],
        "wss_name": "test_market_data",
        "wss_url": 'wss://fstream.binance.com/ws',
        "exchange_data": BinanceExchangeDataSwap()
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
    random_number = random.randint(10 ** 17, 10 ** 18 - 1)
    buy_client_order_id = str(random_number - 1)
    sell_client_order_id = str(random_number + 1)
    lots = 0
    while lots * bid_price < 10:
        lots += 1
    buy_data = live_binance_swap_feed.make_order("OP-USDT", lots, bid_price, "buy-limit",
                                                 client_order_id=buy_client_order_id,
                                                 **{"position_side": "LONG"})

    print(buy_data.get_data()[0].init_data())
    sell_data = live_binance_swap_feed.make_order("OP-USDT", lots, ask_price, "sell-limit",
                                                  client_order_id=sell_client_order_id,
                                                  **{"position_side": "SHORT"})

    print(sell_data.get_data()[0].init_data())
    # data = live_binance_swap_feed.query_order("OP-USDT", client_order_id=sell_client_order_id)
    # print("下单数据", data.get_data())
    cancel_buy_data = live_binance_swap_feed.cancel_order("OP-USDT", client_order_id=buy_client_order_id)
    print("撤单数据", cancel_buy_data.get_data()[0].init_data())
    cancel_sell_data = live_binance_swap_feed.cancel_order("OP-USDT", client_order_id=sell_client_order_id)
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


if __name__ == '__main__':
    test_binance_wss_data_feed()
    # test_get_binance_account_data_feed()
