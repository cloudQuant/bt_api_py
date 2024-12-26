import json
from bt_api_py.containers.orders.okx_order import OkxOrderData


def test_receive_okx_order():
    data = {
        "code": "0",
        "msg": "",
        "data": [
            {
                "instType": "FUTURES",
                "instId": "BTC-USD-200329",
                "ccy": "",
                "ordId": "123445",
                "clOrdId": "b1",
                "tag": "",
                "px": "999",
                "pxUsd": "",
                "pxVol": "",
                "pxType": "",
                "sz": "3",
                "pnl": "5",
                "ordType": "limit",
                "side": "buy",
                "posSide": "long",
                "tdMode": "isolated",
                "accFillSz": "0",
                "fillPx": "0",
                "tradeId": "0",
                "fillSz": "0",
                "fillTime": "0",
                "source": "",
                "state": "live",
                "avgPx": "0",
                "lever": "20",
                "attachAlgoClOrdId": "",
                "tpTriggerPx": "",
                "tpTriggerPxType": "last",
                "tpOrdPx": "",
                "slTriggerPx": "",
                "slTriggerPxType": "last",
                "slOrdPx": "",
                "attachAlgoOrds": [],
                "stpId": "",
                "stpMode": "",
                "feeCcy": "",
                "fee": "",
                "rebateCcy": "",
                "rebate": "",
                "tgtCcy": "",
                "category": "",
                "reduceOnly": "false",
                "cancelSource": "20",
                "cancelSourceReason": "Cancel all after triggered",
                "quickMgnType": "",
                "algoClOrdId": "",
                "algoId": "",
                "uTime": "1597026383085",
                "cTime": "1597026383085"
            }
        ]
    }

    bo = OkxOrderData(data['data'][0], "BTC-USDT", "FUTURE", True)
    bo.init_data()
    assert bo.get_asset_type() == "FUTURE"
    assert bo.get_event() == "OrderEvent"
    assert bo.get_client_order_id() == "b1"
    assert bo.get_close_position() is None
    assert bo.get_exchange_name() == "OKX"
    assert bo.get_cum_quote() is None
    assert bo.get_executed_qty() == 0.0
    assert bo.get_local_update_time() > 0
    assert bo.get_order_avg_price() == 0.0
    assert bo.get_order_id() == "123445"
    assert bo.get_order_price() == 999.0
    assert bo.get_order_size() == 3.0
    assert bo.get_order_side() == "buy"
    assert bo.get_order_status() == "live"
    assert bo.get_order_symbol_name() == "BTC-USD-200329"
    assert bo.get_order_time_in_force() == "limit"
    assert bo.get_order_type() == "limit"
    assert bo.get_origin_order_type() is None
    assert bo.get_position_side() == "long"
    assert bo.get_reduce_only() is False
    assert bo.get_server_time() == 1597026383085.0
    assert bo.get_stop_loss_price() is None
    assert bo.get_stop_loss_trigger_price() is None
    assert bo.get_stop_loss_trigger_price_type() == "last"
    assert bo.get_symbol_name() == "BTC-USDT"
    assert bo.get_take_profit_price() is None
    assert bo.get_take_profit_trigger_price() is None
    assert bo.get_take_profit_trigger_price_type() == "last"
    assert bo.get_trade_id() == 0.0
    assert bo.get_trailing_stop_price() is None
    assert bo.get_trailing_stop_trigger_price() is None
    assert bo.get_trailing_stop_trigger_price_type() is None
    assert bo.get_trailing_stop_callback_rate() is None


if __name__ == "__main__":
    test_receive_okx_order()
