import queue
import time
import random
from btpy.functions.utils import read_yaml_file
from btpy.feeds.live_binance_feed import BinanceMarketWssDataSpot, BinanceAccountWssDataSpot
from btpy.containers.exchanges.binance_exchange_data import BinanceExchangeDataSpot
from btpy.containers.bars.binance_bar import BinanceWssBarData
from btpy.containers.tickers.binance_ticker import BinanceWssTickerData
from btpy.containers.orderbooks.binance_orderbook import BinanceWssOrderBookData
from btpy.containers.accounts.binance_account import BinanceSpotWssAccountData
from btpy.containers.orders.binance_order import BinanceSpotWssOrderData
# from btpy.containers.trades.binance_trade import BinanceWssTradeData
# from btpy.containers.positions.binance_position import BinanceWssPositionData
from btpy.feeds.live_binance_feed import BinanceRequestDataSpot


def generate_kwargs(exchange=BinanceExchangeDataSpot):
    data = read_yaml_file("account_config.yaml")
    kwargs = {
        "public_key": data['binance']['public_key'],
        "private_key": data['binance']['private_key'],
        "exchange_data": exchange(),
        "topics": {"tick": {"symbol": "BTC-USDT"}}
    }
    return kwargs


def init_req_feed():
    data_queue = queue.Queue()
    kwargs = generate_kwargs()
    live_binance_spot_feed = BinanceRequestDataSpot(data_queue, **kwargs)
    return live_binance_spot_feed


def test_binance_wss_data_feed():
    data_queue = queue.Queue()
    data = read_yaml_file("account_config.yaml")
    kwargs = {
        "public_key": data['binance']['public_key'],
        "private_key": data['binance']['private_key'],
        'topics': [
            {"topic": "ticker", "symbol": "BTC-USDT"},
            {"topic": "depth", "symbol": "BTC-USDT"},
            {"topic": "kline", "symbol": "BTC-USDT", "period": "1m"},
            {"topic": "kline", "symbol": "ETH-USDT", "period": "1m"},
        ],
        "wss_name": "test_market_data",
        "wss_url": 'wss://fstream.binance.com/ws',
        "exchange_data": BinanceExchangeDataSpot()
    }
    BinanceMarketWssDataSpot(data_queue, **kwargs).start()
    time.sleep(20)
    receive_binance_bar_data = False
    receive_binance_ticker_data = False
    receive_binance_order_book_data = False
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

    assert receive_binance_bar_data is True
    assert receive_binance_ticker_data is True
    assert receive_binance_order_book_data is True


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
        "exchange_data": BinanceExchangeDataSpot()
    }
    BinanceAccountWssDataSpot(data_queue, **kwargs).start()
    time.sleep(3)
    receive_binance_account_data = False
    # receive_binance_position_data = False
    receive_binance_order_data = False
    # 下单撤单测试订单功能
    live_binance_spot_feed = init_req_feed()
    price_data = live_binance_spot_feed.get_tick("OP-USDT")
    price_data = price_data.get_data()[0].init_data()
    ask_price = round(price_data.get_ask_price() * 1.1, 3)
    bid_price = round(price_data.get_ask_price() * 0.9, 3)
    random_number = random.randint(10 ** 17, 10 ** 18 - 1)
    buy_client_order_id = str(random_number - 1)
    lots = 0
    while lots * ask_price < 10:
        lots += 1
    # https://api.binance.com/api/v3/order?recvWindow=3000&timestamp=1709102826825&symbol=OPUSDT&side=BUY&quantity=2&price=3.4&type=LIMIT&timeInForce=GTC&newClientOrderId=143907825008256004&signature=0e5bc5205e91b0356611952e8177ba0580662f290b25e575c4e3579c1d107edb
    buy_data = live_binance_spot_feed.make_order("OP-USDT", lots, bid_price, "buy-limit",
                                                 client_order_id=buy_client_order_id,
                                                 )

    print(buy_data.get_data()[0].init_data())
    # data = live_binance_spot_feed.query_order("OP-USDT", client_order_id=sell_client_order_id)
    # print("下单数据", data.get_data())
    cancel_buy_data = live_binance_spot_feed.cancel_order("OP-USDT", client_order_id=buy_client_order_id)
    print("撤单数据", cancel_buy_data.get_data()[0].init_data())

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
        if isinstance(data, BinanceSpotWssAccountData):
            receive_binance_account_data = True
        # if isinstance(data, BinancePositionData):
        #     receive_binance_position_data = True
        if isinstance(data, BinanceSpotWssOrderData):
            receive_binance_order_data = True

    # assert receive_binance_account_data is True
    # assert receive_binance_position_data is True
    assert receive_binance_order_data is True


if __name__ == '__main__':
    # test_binance_wss_data_feed()
    test_get_binance_account_data_feed()
