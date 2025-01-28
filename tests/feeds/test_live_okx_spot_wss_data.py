import queue
import time
import random
from bt_api_py.functions.utils import read_yaml_file
from bt_api_py.containers.exchanges.okx_exchange_data import OkxExchangeDataSwap
from bt_api_py.feeds.live_okx_feed import OkxRequestDataSpot, OkxMarketWssDataSpot, OkxKlineWssDataSpot, \
    OkxAccountWssDataSpot
from bt_api_py.containers.exchanges.okx_exchange_data import OkxExchangeDataSpot
from bt_api_py.containers.bars.okx_bar import OkxBarData
from bt_api_py.containers.markprices.okx_mark_price import OkxMarkPriceData
from bt_api_py.containers.tickers.okx_ticker import OkxTickerData
from bt_api_py.containers.orderbooks.okx_orderbook import OkxOrderBookData
# from bt_api_py.containers.fundingrates.okx_funding_rate import OkxFundingRateData
from bt_api_py.containers.accounts.okx_account import OkxAccountData
from bt_api_py.containers.orders.okx_order import OkxOrderData


# from bt_api_py.containers.trades.okx_trade import OkxWssTradeData
# from bt_api_py.containers.positions.okx_position import OkxPositionData


def generate_kwargs(exchange=OkxExchangeDataSwap):
    data = read_yaml_file("account_config.yaml")
    kwargs = {
        "public_key": data['okx']['public_key'],
        "private_key": data['okx']['private_key'],
        "passphrase": data['okx']["passphrase"],
        "topics": {"tick": {"symbol": "BTC-USDT"}}
    }
    return kwargs


def init_req_feed():
    data_queue = queue.Queue()
    kwargs = generate_kwargs(exchange=OkxExchangeDataSpot)
    live_okx_spot_feed = OkxRequestDataSpot(data_queue, **kwargs)
    return live_okx_spot_feed


def test_okx_wss_data_feed():
    data_queue = queue.Queue()
    data = read_yaml_file("account_config.yaml")
    kwargs = {
        "public_key": data['okx']['public_key'],
        "private_key": data['okx']['private_key'],
        "passphrase": data['okx']["passphrase"],
        'topics': [
            {"topic": "ticker", "symbol": "BTC-USDT"},
            {"topic": "depth", "symbol": "BTC-USDT"},
            {"topic": "mark_price", "symbol": "BTC-USDT"},
        ],
        "wss_name": "test_market_data",
        "wss_url": 'wss://ws.okx.com:8443/ws/v5/public',
        "exchange_data": OkxExchangeDataSpot()
    }
    OkxMarketWssDataSpot(data_queue, **kwargs).start()
    kline_kwargs = {
        "public_key": data['okx']['public_key'],
        "private_key": data['okx']['private_key'],
        "passphrase": data['okx']["passphrase"],
        'topics': [
            {"topic": "kline", "symbol": "BTC-USDT", "period": "1m"},
            {"topic": "kline", "symbol": "ETH-USDT", "period": "1m"},
            # {"topic": "kline", "symbol": "ETH-USDT", "period": "15m"}
        ],
        "wss_name": "test_kline_data",
        "wss_url": 'wss://ws.okx.com:8443/ws/v5/business',
        "exchange_data": OkxExchangeDataSpot()
    }
    OkxKlineWssDataSpot(data_queue, **kline_kwargs).start()
    time.sleep(3)
    receive_okx_bar_data = False
    receive_okx_ticker_data = False
    receive_okx_order_book_data = False
    receive_okx_mark_price_data = False
    count = 1
    while True:
        count += 1
        try:
            data = data_queue.get(False)
        except queue.Empty:
            break
        if count > 10000:
            break
        if isinstance(data, OkxBarData):
            receive_okx_bar_data = True
        if isinstance(data, OkxTickerData):
            receive_okx_ticker_data = True
        if isinstance(data, OkxOrderBookData):
            receive_okx_order_book_data = True
        if isinstance(data, OkxMarkPriceData):
            receive_okx_mark_price_data = True

    assert receive_okx_bar_data is True
    assert receive_okx_ticker_data is True
    assert receive_okx_order_book_data is True
    assert receive_okx_mark_price_data is True


def test_get_okx_account_data_feed():
    data_queue = queue.Queue()
    data = read_yaml_file("account_config.yaml")
    kwargs = {
        "public_key": data['okx']['public_key'],
        "private_key": data['okx']['private_key'],
        "passphrase": data['okx']["passphrase"],
        'topics': [
            {"topic": "account", "symbol": "OP-USDT", "currency": "USDT"},
            {"topic": "orders", "symbol": "OP-USDT"},
            {"topic": "positions", "symbol": "OP-USDT"},
        ],
        "wss_name": "test_market_data",
        # "wss_url": 'wss://ws.okx.com:8443/ws/v5/public',
        "exchange_data": OkxExchangeDataSpot()
    }
    OkxAccountWssDataSpot(data_queue, **kwargs).start()
    time.sleep(3)
    receive_okx_account_data = False
    # receive_okx_position_data = False
    receive_okx_order_data = False
    # 下单撤单测试订单功能
    live_okx_spot_feed = init_req_feed()
    price_data = live_okx_spot_feed.get_tick("OP-USDT")
    price_data = price_data.get_data()[0].init_data()
    ask_price = round(price_data.get_ask_price() * 1.1, 2)
    random_number = random.randint(10 ** 17, 10 ** 18 - 1)
    sell_client_order_id = str(random_number + 1)
    lots = 0
    while lots * ask_price < 10:
        lots += 1
    sell_data = live_okx_spot_feed.make_order("OP-USDT", lots, ask_price,
                                              "sell-limit",
                                              client_order_id=sell_client_order_id,
                                              )
    print(sell_data)
    data = live_okx_spot_feed.query_order("OP-USDT", client_order_id=sell_client_order_id)
    print("下单数据", data.get_data())
    data = live_okx_spot_feed.cancel_order("OP-USDT", client_order_id=sell_client_order_id)
    print("撤单数据", data.get_data())
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
        if isinstance(data, OkxAccountData):
            receive_okx_account_data = True
        # if isinstance(data, OkxPositionData):
        #     receive_okx_position_data = True
        if isinstance(data, OkxOrderData):
            receive_okx_order_data = True

    assert receive_okx_account_data is True
    # assert receive_okx_position_data is True
    assert receive_okx_order_data is True


if __name__ == '__main__':
    # test_okx_wss_data_feed()
    test_get_okx_account_data_feed()
