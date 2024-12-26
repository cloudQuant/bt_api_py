import json
from bt_api_py.containers.orderbooks.okx_orderbook import OkxOrderBookData


def test_okx_swap_req_orderbook():
    data = {
            "code": "0",
            "msg": "",
            "data": [
                {
                    "asks": [
                        [
                            "41006.8",
                            "0.60038921",
                            "0",
                            "1"
                        ]
                    ],
                    "bids": [
                        [
                            "41006.3",
                            "0.30178218",
                            "0",
                            "2"
                        ]
                    ],
                    "ts": "1629966436396"
                }
            ]
        }
    bo = OkxOrderBookData(data['data'][0], "BTC-USDT", "SWAP", True)
    bo.init_data()
    assert bo.get_server_time() == 1629966436396.0
    assert bo.get_exchange_name() == "OKX"
    assert bo.get_symbol_name() == "BTC-USDT"
    assert bo.get_asset_type() == "SWAP"
    assert bo.get_bid_price_list()[0] == 41006.3
    assert bo.get_bid_volume_list()[-1] == 0.30178218
    assert bo.get_ask_price_list()[-1] == 41006.8
    assert bo.get_ask_volume_list()[-1] == 0.60038921
    assert bo.get_bid_trade_nums()[0] == 2.0
    assert bo.get_ask_trade_nums()[-1] == 1.0


def test_okx_swap_wss_orderbook():
    data = {
                "arg": {
                    "channel": "books",
                    "instId": "BTC-USDT"
                },
                "action": "snapshot",
                "data": [{
                    "asks": [
                        ["8476.98", "415", "0", "13"],
                        ["8477", "7", "0", "2"],
                        ["8477.34", "85", "0", "1"],
                        ["8477.56", "1", "0", "1"],
                        ["8505.84", "8", "0", "1"],
                        ["8506.37", "85", "0", "1"],
                        ["8506.49", "2", "0", "1"],
                        ["8506.96", "100", "0", "2"]
                    ],
                    "bids": [
                        ["8476.97", "256", "0", "12"],
                        ["8475.55", "101", "0", "1"],
                        ["8475.54", "100", "0", "1"],
                        ["8475.3", "1", "0", "1"],
                        ["8447.32", "6", "0", "1"],
                        ["8447.02", "246", "0", "1"],
                        ["8446.83", "24", "0", "1"],
                        ["8446", "95", "0", "3"]
                    ],
                    "ts": "1597026383085",
                    "checksum": -855196043,
                    "prevSeqId": -1,
                    "seqId": 123456
                }]
            }
    bo = OkxOrderBookData(json.dumps(data), "BTC-USDT", "SWAP", False)
    bo.init_data()
    assert bo.get_server_time() == 1597026383085.0
    assert bo.get_exchange_name() == "OKX"
    assert bo.get_symbol_name() == "BTC-USDT"
    assert bo.get_asset_type() == "SWAP"
    assert bo.get_bid_price_list()[0] == 8476.97
    assert bo.get_bid_volume_list()[-1] == 95.0
    assert bo.get_ask_price_list()[-1] == 8506.96
    assert bo.get_ask_volume_list()[-1] == 100.0
    assert bo.get_bid_trade_nums()[0] == 12.0
    assert bo.get_ask_trade_nums()[-1] == 2.0


if __name__ == "__main__":
    test_okx_swap_req_orderbook()
    test_okx_swap_wss_orderbook()
