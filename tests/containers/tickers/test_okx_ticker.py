import json
from bt_api_py.containers.tickers.okx_ticker import OkxTickerData


def test_okx_ticker():
    data = {
                "instType": "SWAP",
                "instId": "LTC-USD-200327",
                "last": "9999.99",
                "lastSz": "0.1",
                "askPx": "9999.99",
                "askSz": "11",
                "bidPx": "8888.88",
                "bidSz": "5",
                "open24h": "9000",
                "high24h": "10000",
                "low24h": "8888.88",
                "volCcy24h": "2222",
                "vol24h": "2222",
                "sodUtc0": "2222",
                "sodUtc8": "2222",
                "ts": "1597026383085"
            }
    bt = OkxTickerData(data, "LTC-USD-200327", "PERPETUAL", has_been_json_encoded=True)
    bt.init_data()
    assert bt.get_server_time() == 1597026383085.0
    assert bt.get_exchange_name() == "OKX"
    assert bt.get_symbol_name() == "LTC-USD-200327"
    assert bt.get_bid_price() == 8888.88
    assert bt.get_bid_volume() == 5.0
    assert bt.get_ask_price() == 9999.99
    assert bt.get_ask_volume() == 11.0
    assert bt.get_last_price()  == 9999.99
    assert bt.get_last_volume() == 0.1


if __name__ == "__main__":
    test_okx_ticker()