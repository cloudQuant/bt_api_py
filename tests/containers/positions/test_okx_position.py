from bt_api_py.containers.positions.okx_position import OkxPositionData


def test_okx_position():
    data = {
        "code": "0",
        "msg": "",
        "data": [{
            "adl": "1",
            "availPos": "1",
            "avgPx": "2566.31",
            "cTime": "1619507758793",
            "ccy": "ETH",
            "deltaBS": "",
            "deltaPA": "",
            "gammaBS": "",
            "gammaPA": "",
            "imr": "",
            "instId": "ETH-USD-210430",
            "instType": "FUTURES",
            "interest": "0",
            "idxPx": "2566.13",
            "last": "2566.22",
            "usdPx": "",
            "bePx": "2353.949",
            "lever": "10",
            "liab": "",
            "liabCcy": "",
            "liqPx": "2352.8496681818233",
            "markPx": "2353.849",
            "margin": "0.0003896645377994",
            "mgnMode": "isolated",
            "mgnRatio": "11.731726509588816",
            "mmr": "0.0000311811092368",
            "notionalUsd": "2276.2546609009605",
            "optVal": "",
            "pTime": "1619507761462",
            "pos": "1",
            "posCcy": "",
            "posId": "307173036051017730",
            "posSide": "long",
            "spotInUseAmt": "",
            "spotInUseCcy": "",
            "thetaBS": "",
            "thetaPA": "",
            "tradeId": "109844",
            "bizRefId": "",
            "bizRefType": "",
            "quoteBal": "0",
            "baseBal": "0",
            "baseBorrowed": "",
            "baseInterest": "",
            "quoteBorrowed": "",
            "quoteInterest": "",
            "uTime": "1619507761462",
            "upl": "-0.0000009932766034",
            "uplLastPx": "-0.0000009932766034",
            "uplRatio": "-0.0025490556801078",
            "uplRatioLastPx": "-0.0025490556801078",
            "vegaBS": "",
            "vegaPA": "",
            "realizedPnl": "0.001",
            "pnl": "0.0011",
            "fee": "-0.0001",
            "fundingFee": "0",
            "liqPenalty": "0",
            "closeOrderAlgo": [
                {
                    "algoId": "123",
                    "slTriggerPx": "123",
                    "slTriggerPxType": "mark",
                    "tpTriggerPx": "123",
                    "tpTriggerPxType": "mark",
                    "closeFraction": "0.6"
                },
                {
                    "algoId": "123",
                    "slTriggerPx": "123",
                    "slTriggerPxType": "mark",
                    "tpTriggerPx": "123",
                    "tpTriggerPxType": "mark",
                    "closeFraction": "0.4"
                }
            ]
        }]
    }
    bo = OkxPositionData(data['data'][0], "ETH-USD", "PERPETUAL", True)
    bo.init_data()
    assert bo.get_server_time() == 1619507761462.0
    assert bo.get_exchange_name() == "OKX"
    assert bo.get_asset_type() == "PERPETUAL"
    assert bo.get_position_id() is None
    assert bo.get_account_id() is None
    assert bo.get_is_isolated() is True
    assert bo.get_margin_type() == "isolated"
    assert bo.get_is_auto_add_margin() is None
    assert bo.get_leverage() == 10.0
    assert bo.get_max_notional_value() is None
    assert bo.get_position_symbol_name() == "ETH-USD-210430"
    assert bo.get_position_volume() == 1.0
    assert bo.get_position_side() == "long"
    assert bo.get_trade_num() is None
    assert bo.get_avg_price() == 2566.31
    assert bo.get_mark_price() == 2353.849
    assert bo.get_liquidation_price() is None
    assert bo.get_initial_margin() is None
    assert bo.get_maintain_margin() == 0.0000311811092368
    assert bo.open_order_initial_margin() is None
    assert bo.get_position_initial_margin() is None
    assert bo.get_position_fee() == -0.0001
    assert bo.get_position_realized_pnl() == 0.001
    assert bo.get_position_unrealized_pnl() == -0.0000009932766034
    assert bo.get_position_funding_value() == 0.0


if __name__ == "__main__":
    test_okx_position()
