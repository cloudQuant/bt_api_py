import json
from btpy.containers.tickers.binance_ticker import BinanceWssTickerData, BinanceRequestTickerData


def test_binance_request_ticker():
    data = {
        "lastUpdateId": 1027024,
        "symbol": "BTCUSDT",  # 交易对
        "bidPrice": "4.00000000",  # 最优买单价
        "bidQty": "431.00000000",  # 挂单量
        "askPrice": "4.00000200",  # 最优卖单价
        "askQty": "9.00000000",  # 挂单量
        "time": 1589437530011  # 撮合引擎时间
    }
    bt = BinanceRequestTickerData(data, "BTC-USDT", "PERPETUAL", True)
    bt.init_data()
    assert bt.get_server_time() == 1589437530011.0
    assert bt.get_exchange_name() == "BINANCE"
    assert bt.get_symbol_name() == "BTC-USDT"
    assert bt.get_ticker_symbol_name() == "BTCUSDT"
    assert bt.get_bid_price() == 4.00000000
    assert bt.get_bid_volume() == 431.00000000
    assert bt.get_ask_price() == 4.00000200
    assert bt.get_ask_volume() == 9.00000000
    assert bt.get_last_price() is None
    assert bt.get_last_volume() is None


def test_binance_ticker():
    data = {
        "e": "bookTicker",  # 事件类型
        "u": 400900217,  # 更新ID
        "E": 1568014460893,  # 事件推送时间
        "T": 1568014460891,  # 撮合时间
        "s": "BNBUSDT",  # 交易对
        "b": "25.35190000",  # 买单最优挂单价格
        "B": "31.21000000",  # 买单最优挂单数量
        "a": "25.36520000",  # 卖单最优挂单价格
        "A": "40.66000000"  # 卖单最优挂单数量
    }
    bt = BinanceWssTickerData(data, "BNB-USDT", "PERPETUAL", True)
    bt.init_data()
    assert bt.get_server_time() == 1568014460893.0
    assert bt.get_exchange_name() == "BINANCE"
    assert bt.get_symbol_name() == "BNB-USDT"
    assert bt.get_ticker_symbol_name() == "BNBUSDT"
    assert bt.get_bid_price() == 25.35190000
    assert bt.get_bid_volume() == 31.21000000
    assert bt.get_ask_price() == 25.36520000
    assert bt.get_ask_volume() == 40.66000000
    assert bt.get_last_price() is None
    assert bt.get_last_volume() is None


if __name__ == "__main__":
    test_binance_ticker()
