# import json
from bt_api_py.containers.orderbooks.binance_orderbook import BinanceWssOrderBookData, BinanceRequestOrderBookData


def test_binance_request_orderbook():
    data = {
        "lastUpdateId": 1027024,
        "E": 1589436922972,  # 消息时间
        "T": 1589436922959,  # 撮合引擎时间
        "bids": [  # 买单
            [
                "4.00000000",  # 价格
                "431.00000000"  # 数量
            ]
        ],
        "asks": [  # 卖单
            [
                "4.00000200",  # 价格
                "12.00000000"  # 数量
            ]
        ]
    }
    bo = BinanceRequestOrderBookData(data, "boCUSDT", "PERPETUAL", True)
    bo.init_data()
    assert bo.get_server_time() == 1589436922972.0
    assert bo.get_exchange_name() == "BINANCE"
    assert bo.get_symbol_name() == "boCUSDT"
    assert bo.get_asset_type() == "PERPETUAL"
    assert bo.get_bid_price_list()[0] == 4.0
    assert bo.get_bid_volume_list()[-1] == 431.0
    assert bo.get_ask_price_list()[-1] == 4.00000200
    assert bo.get_ask_volume_list()[-1] == 12.0


def test_binance_orderbook():
    data = {
        "e": "depthUpdate",  # 事件类型
        "E": 1571889248277,  # 事件时间
        "T": 1571889248276,  # 交易时间
        "s": "boCUSDT",
        "U": 390497796,
        "u": 390497878,
        "pu": 390497794,
        "b": [  # 买方
            [
                "7403.89",  # 价格
                "0.002"  # 数量
            ],
            [
                "7403.90",
                "3.906"
            ],
            [
                "7404.00",
                "1.428"
            ],
            [
                "7404.85",
                "5.239"
            ],
            [
                "7405.43",
                "2.562"
            ]
        ],
        "a": [  # 卖方
            [
                "7405.96",  # 价格
                "3.340"  # 数量
            ],
            [
                "7406.63",
                "4.525"
            ],
            [
                "7407.08",
                "2.475"
            ],
            [
                "7407.15",
                "4.800"
            ],
            [
                "7407.20",
                "0.175"
            ]
        ]
    }
    bo = BinanceWssOrderBookData(data, "boCUSDT", "PERPETUAL", True)
    bo.init_data()
    assert bo.get_server_time() == 1571889248277.0
    assert bo.get_exchange_name() == "BINANCE"
    assert bo.get_symbol_name() == "boCUSDT"
    assert bo.get_asset_type() == "PERPETUAL"
    assert bo.get_bid_price_list()[0] == 7403.89
    assert bo.get_bid_volume_list()[-1] == 2.562
    assert bo.get_ask_price_list()[-1] == 7407.20
    assert bo.get_ask_volume_list()[-1] == 0.175


if __name__ == "__main__":
    test_binance_orderbook()
    test_binance_request_orderbook()
