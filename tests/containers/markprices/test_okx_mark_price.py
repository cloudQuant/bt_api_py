import json

from btpy.containers.markprices.okx_mark_price import OkxMarkPriceData


def test_okx_req_mark_price():
    data = {
        "code": "0",
        "msg": "",
        "data": [
            {
                "instId": "BTC-USDT",
                "idxPx": "43350",
                "high24h": "43649.7",
                "sodUtc0": "43444.1",
                "open24h": "43640.8",
                "low24h": "43261.9",
                "sodUtc8": "43328.7",
                "ts": "1649419644492"
            }
        ]
    }
    bp = OkxMarkPriceData(data['data'][0], "BTC-USDT", "SPOT", True)
    bp.init_data()
    assert bp.get_server_time() == 1649419644492.0
    assert bp.get_exchange_name() == "OKX"
    assert bp.get_symbol_name() == "BTC-USDT"
    assert bp.get_mark_price() == 43350.0
    assert bp.get_event() == "MarkPriceEvent"
    assert bp.get_asset_type() == "SPOT"


def test_okx_wss_mark_price():
    data = {
        "arg": {
            "channel": "mark-price",
            "instId": "BTC-USDT"
        },
        "data": [
            {
                "instType": "MARGIN",
                "instId": "BTC-USDT",
                "markPx": "42310.6",
                "ts": "1630049139746"
            }
        ]
    }
    bp = OkxMarkPriceData(json.dumps(data), "BTC-USDT", "SPOT", False)
    bp.init_data()
    assert bp.get_server_time() == 1630049139746.0
    assert bp.get_exchange_name() == "OKX"
    assert bp.get_symbol_name() == "BTC-USDT"
    assert bp.get_mark_price() == 42310.6
    assert bp.get_asset_type() == "SPOT"
    assert bp.get_event() == "MarkPriceEvent"


if __name__ == "__main__":
    test_okx_req_mark_price()
    test_okx_wss_mark_price()
