# import json
from btpy.containers.trades.okx_trade import *


def test_okx_req_order():
    data = {
        "code": "0",
        "msg": "",
        "data": [
            {
                "instType": "FUTURES",
                "instId": "BTC-USD-200329",
                "tradeId": "123",
                "ordId": "312269865356374016",
                "clOrdId": "b16",
                "billId": "1111",
                "tag": "",
                "fillPx": "999",
                "fillSz": "3",
                "fillIdxPx": "998",
                "fillPnl": "0.01",
                "fillPxVol": "",
                "fillPxUsd": "",
                "fillMarkVol": "",
                "fillFwdPx": "",
                "fillMarkPx": "",
                "side": "buy",
                "posSide": "long",
                "execType": "M",
                "feeCcy": "",
                "fee": "",
                "ts": "1597026383085",
                "fillTime": "1597026383084"
            },
            {
                "instType": "FUTURES",
                "instId": "BTC-USD-200329",
                "tradeId": "123",
                "ordId": "312269865356374016",
                "clOrdId": "b16",
                "billId": "1111",
                "tag": "",
                "fillPx": "999",
                "fillSz": "3",
                "fillIdxPx": "998",
                "fillPnl": "0.02",
                "fillPxVol": "",
                "fillPxUsd": "",
                "fillMarkVol": "",
                "fillFwdPx": "",
                "fillMarkPx": "",
                "side": "buy",
                "posSide": "long",
                "execType": "M",
                "feeCcy": "",
                "fee": "",
                "ts": "1597026383085",
                "fillTime": "1597026383084"
            }
        ]
    }

    bo = OkxRequestTradeData(data["data"][-1], "BTC-USD-200329", "SWAP", True)
    bo.init_data()
    assert bo.get_server_time() == 1597026383085.0
    assert bo.get_exchange_name() == "OKX"
    assert bo.get_asset_type() == "SWAP"
    assert bo.get_trade_id() == 123.0
    assert bo.get_trade_symbol_name() == "BTC-USD-200329"
    assert bo.get_order_id() == "312269865356374016"
    assert bo.get_client_order_id() == "b16"
    assert bo.get_trade_side() == "buy"
    assert bo.get_trade_offset() is None
    assert bo.get_trade_price() == 999.0
    assert bo.get_trade_volume() == 3.0
    assert bo.get_trade_accumulate_volume() is None
    assert bo.get_trade_type() == "maker"
    assert bo.get_trade_time() == 1597026383084.0
    assert bo.get_trade_fee() is None
    assert bo.get_trade_fee_symbol() == ""


def test_okx_wss_trade():
    data = {
        "arg": {
            "channel": "orders",
            "instType": "SPOT",
            "instId": "BTC-USDT",
            "uid": "614488474791936"
        },
        "data": [
            {
                "accFillSz": "0.001",
                "amendResult": "",
                "avgPx": "31527.1",
                "cTime": "1654084334977",
                "category": "normal",
                "ccy": "",
                "clOrdId": "",
                "code": "0",
                "execType": "M",
                "fee": "-0.02522168",
                "feeCcy": "USDT",
                "fillFee": "-0.02522168",
                "fillFeeCcy": "USDT",
                "fillNotionalUsd": "31.50818374",
                "fillPx": "31527.1",
                "fillSz": "0.001",
                "fillPnl": "0.01",
                "fillTime": "1654084353263",
                "fillPxVol": "",
                "fillPxUsd": "",
                "fillMarkVol": "",
                "fillFwdPx": "",
                "fillMarkPx": "",
                "instId": "BTC-USDT",
                "instType": "SPOT",
                "lever": "0",
                "msg": "",
                "notionalUsd": "31.50818374",
                "ordId": "452197707845865472",
                "ordType": "limit",
                "pnl": "0",
                "posSide": "",
                "px": "31527.1",
                "pxUsd": "",
                "pxVol": "",
                "pxType": "",
                "rebate": "0",
                "rebateCcy": "BTC",
                "reduceOnly": "false",
                "reqId": "",
                "side": "sell",
                "attachAlgoClOrdId": "",
                "slOrdPx": "",
                "slTriggerPx": "",
                "slTriggerPxType": "last",
                "source": "",
                "state": "filled",
                "stpId": "",
                "stpMode": "",
                "sz": "0.001",
                "tag": "",
                "tdMode": "cash",
                "tgtCcy": "",
                "tpOrdPx": "",
                "tpTriggerPx": "",
                "tpTriggerPxType": "last",
                "tradeId": "242589207",
                "lastPx": "38892.2",
                "quickMgnType": "",
                "algoClOrdId": "",
                "attachAlgoOrds": [],
                "algoId": "",
                "amendSource": "",
                "cancelSource": "",
                "uTime": "1654084353264"
            }
        ]
    }
    bo = OkxWssTradeData(data["data"][-1], "BTC-USDT", "SWAP", True)
    bo.init_data()
    assert bo.get_server_time() == 1654084353264.0
    assert bo.get_exchange_name() == "OKX"
    assert bo.get_asset_type() == "SWAP"
    assert bo.get_trade_id() == 242589207.0
    assert bo.get_trade_symbol_name() == "BTC-USDT"
    assert bo.get_order_id() == "452197707845865472"
    assert bo.get_client_order_id() == ""
    assert bo.get_trade_side() == "sell"
    assert bo.get_trade_offset() is None
    assert bo.get_trade_price() == 31527.1
    assert bo.get_trade_volume() == 0.001
    assert bo.get_trade_accumulate_volume() is None
    assert bo.get_trade_type() == "maker"
    assert bo.get_trade_time() == 1654084353263.0
    assert bo.get_trade_fee() == -0.02522168
    assert bo.get_trade_fee_symbol() == "USDT"


if __name__ == "__main__":
    test_okx_req_order()
