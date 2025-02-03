import time
import queue
import traceback
from bt_api_py.bt_api import BtApi
from bt_api_py.containers.requestdatas.request_data import RequestData
from bt_api_py.containers.tickers.binance_ticker import BinanceRequestTickerData
from bt_api_py.functions.utils import read_yaml_file

def generate_binance_swap_kwargs():
    data = read_yaml_file("account_config.yaml")
    kwargs = {
        "public_key": data['binance']['public_key'],
        "private_key": data['binance']['private_key']
    }

    return {"binance___swap": kwargs}


def generate_binance_spot_kwargs():
    data = read_yaml_file("account_config.yaml")
    kwargs = {
        "public_key": data['binance']['public_key'],
        "private_key": data['binance']['private_key']
    }

    return {"binance___spot": kwargs}


def generate_okx_spot_kwargs():
    data = read_yaml_file("account_config.yaml")
    kwargs = {
        "public_key": data['okx']['public_key'],
        "private_key": data['okx']['private_key'],
        "passphrase": data['okx']["passphrase"]
    }
    return {"okx___spot": kwargs}

def generate_okx_swap_kwargs():
    data = read_yaml_file("account_config.yaml")
    kwargs = {
        "public_key": data['okx']['public_key'],
        "private_key": data['okx']['private_key'],
        "passphrase": data['okx']["passphrase"]
    }
    return {"okx___swap": kwargs}


def generate_bt_api_kwargs():
    data = read_yaml_file("account_config.yaml")
    binance_kwargs = {
        "public_key": data['binance']['public_key'],
        "private_key": data['binance']['private_key']
    }
    okx_kwargs = {
        "public_key": data['okx']['public_key'],
        "private_key": data['okx']['private_key'],
        "passphrase": data['okx']["passphrase"]
    }
    return {"binance___swap": binance_kwargs, "okx___swap": okx_kwargs,
            "binance___spot": binance_kwargs, "okx___spot": okx_kwargs}


def test_binance_swap_bt_api():
    exchange_kwargs = generate_binance_swap_kwargs()
    bt_api = BtApi(exchange_kwargs, debug=True)
    for exchange_name in exchange_kwargs.keys():
        api = bt_api.get_request_api(exchange_name)
        data = api.get_server_time()
        assert isinstance(data, RequestData)
        print(data.get_data())


def test_okx_spot_bt_api():
    exchange_kwargs = generate_okx_spot_kwargs()
    bt_api = BtApi(exchange_kwargs, debug=True)
    for exchange_name in exchange_kwargs.keys():
        api = bt_api.get_request_api(exchange_name)
        data = api.get_tick("BTC-USDT")
        assert isinstance(data, RequestData)
        print(data.get_data())


def test_okx_swap_bt_api():
    exchange_kwargs = generate_okx_swap_kwargs()
    bt_api = BtApi(exchange_kwargs, debug=True)
    for exchange_name in exchange_kwargs.keys():
        api = bt_api.get_request_api(exchange_name)
        data = api.get_tick("BTC-USDT")
        assert isinstance(data, RequestData)
        print(data.get_data())


def test_bt_api():
    exchange_kwargs = generate_bt_api_kwargs()
    bt_api = BtApi(exchange_kwargs, debug=True)
    okx_spot_api = False
    okx_swap_api = False
    binance_spot_api = False
    binance_swap_api = False
    for exchange_name in exchange_kwargs.keys():
        api = bt_api.get_request_api(exchange_name)
        data = api.get_tick("BTC-USDT")
        assert isinstance(data, RequestData)
        tick_list = data.get_data()
        try:
            tick = tick_list[0]
            if tick.get_exchange_name() == "BINANCE" and tick.get_asset_type() == "SPOT":
                binance_spot_api = True
            if tick.get_exchange_name() == "BINANCE" and tick.get_asset_type() == "SWAP":
                binance_swap_api = True
            if tick.get_exchange_name() == "OKX" and tick.get_asset_type() == "SPOT":
                okx_spot_api = True
            if tick.get_exchange_name() == "OKX" and tick.get_asset_type() == "SWAP":
                okx_swap_api = True
        except Exception as e:
            traceback.format_exception(e)
    assert binance_spot_api is True
    assert binance_swap_api is True
    assert okx_spot_api is True
    assert okx_swap_api is True


def test_async_binance_swap_api():
    exchange_kwargs = generate_binance_swap_kwargs()
    bt_api = BtApi(exchange_kwargs, debug=True)
    for exchange_name in exchange_kwargs.keys():
        data_queue = bt_api.get_data_queue(exchange_name)
        api = bt_api.get_request_api(exchange_name)
        api.async_get_tick("BTC-USDT", extra_data={"test_async_tick_data": True})
        time.sleep(3)
        try:
            tick_data = data_queue.get(False)
        except queue.Empty:
            tick_data = None
        # 检测tick数据
        print(tick_data.get_data())
        assert isinstance(tick_data, RequestData)
        assert isinstance(tick_data.get_data(), list)
        assert isinstance(tick_data.get_data()[0], BinanceRequestTickerData)
        async_tick_data = tick_data.get_data()[0].init_data()
        assert async_tick_data.get_exchange_name() == "BINANCE"
        assert async_tick_data.get_symbol_name() == "BTC-USDT"
        assert async_tick_data.get_bid_price() > 0
        assert async_tick_data.get_bid_volume() >= 0
        assert async_tick_data.get_ask_price() > 0
        assert async_tick_data.get_ask_volume() >= 0
        assert async_tick_data.get_last_price() is None
        assert async_tick_data.get_last_volume() is None


def test_async_bt_api():
    exchange_kwargs = generate_bt_api_kwargs()
    bt_api = BtApi(exchange_kwargs, debug=True)
    okx_spot_api = False
    okx_swap_api = False
    binance_spot_api = False
    binance_swap_api = False
    for exchange_name in exchange_kwargs.keys():
        # data_queue = bt_api.get_data_queue(exchange_name)
        api = bt_api.get_request_api(exchange_name)
        api.async_get_tick("BTC-USDT", extra_data={"test_async_tick_data": True})
        time.sleep(1)
        # for exchange_name in exchange_kwargs.keys():
        data_queue = bt_api.get_data_queue(exchange_name)
        try:
            tick_data = data_queue.get(False)
        except queue.Empty:
            tick_data = None
        # 检测tick数据
        print(tick_data.get_data())
        assert isinstance(tick_data, RequestData)
        assert isinstance(tick_data.get_data(), list)
        async_tick_data = tick_data.get_data()[0].init_data()
        assert async_tick_data.get_bid_price() > 0
        assert async_tick_data.get_bid_volume() >= 0
        assert async_tick_data.get_ask_price() > 0
        assert async_tick_data.get_ask_volume() >= 0
        tick_list = tick_data.get_data()
        try:
            tick = tick_list[0]
            if tick.get_exchange_name() == "BINANCE" and tick.get_asset_type() == "SPOT":
                binance_spot_api = True
            if tick.get_exchange_name() == "BINANCE" and tick.get_asset_type() == "SWAP":
                binance_swap_api = True
            if tick.get_exchange_name() == "OKX" and tick.get_asset_type() == "SPOT":
                okx_spot_api = True
            if tick.get_exchange_name() == "OKX" and tick.get_asset_type() == "SWAP":
                okx_swap_api = True
        except Exception as e:
            traceback.format_exception(e)
    assert binance_spot_api is True
    assert binance_swap_api is True
    assert okx_spot_api is True
    assert okx_swap_api is True


def test_binance_swap_wss_data():
    from bt_api_py.containers.bars.binance_bar import BinanceWssBarData
    from bt_api_py.containers.tickers.binance_ticker import BinanceWssTickerData
    from bt_api_py.containers.orderbooks.binance_orderbook import BinanceWssOrderBookData
    from bt_api_py.containers.fundingrates.binance_funding_rate import BinanceWssFundingRateData
    from bt_api_py.containers.markprices.binance_mark_price import BinanceWssMarkPriceData
    from bt_api_py.containers.trades.binance_trade import BinanceAggTradeData
    from bt_api_py.containers.orders.binance_order import BinanceForceOrderData
    exchange_kwargs = generate_binance_swap_kwargs()
    topics = [{"topic": "ticker", "symbol": "BTC-USDT"},
                {"topic": "depth", "symbol": "BTC-USDT"},
                {"topic": "funding_rate", "symbol": "BTC-USDT"},
                {"topic": "mark_price", "symbol": "BTC-USDT"},
                {"topic": "kline", "symbol": "BTC-USDT", "period": "1m"},
                {"topic": "kline", "symbol": "ETH-USDT", "period": "1m"},
                {"topic": "agg_trade", "symbol": "BTC-USDT"},
                {"topic": "force_order", "symbol": "BTC-USDT"}]
    bt_api = BtApi(exchange_kwargs, debug=True)
    bt_api.subscribe(exchange_kwargs, topics)
    data_queue = bt_api.get_data_queue("binance___swap")
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



if __name__ == "__main__":
    test_binance_swap_bt_api()
    test_binance_swap_wss_data()