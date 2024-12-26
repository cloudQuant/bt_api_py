import json

from btpy.containers.fundingrates.okx_funding_rate import OkxFundingRateData

data = {
    "code": "0",
    "data": [
        {
            "fundingRate": "0.0000792386885340",
            "fundingTime": "1703088000000",
            "instId": "BTC-USDT-SWAP",
            "instType": "SWAP",
            "method": "next_period",
            "maxFundingRate": "0.00375",
            "minFundingRate": "-0.00375",
            "nextFundingRate": "0.0002061194322149",
            "nextFundingTime": "1703116800000",
            "settFundingRate": "0.0001418433662153",
            "settState": "settled",
            "ts": "1703070685309"
        }
    ],
    "msg": ""
}


def assert_value(bf):
    assert bf.get_pre_funding_rate() is None
    assert bf.get_pre_funding_time() is None
    assert bf.get_next_funding_rate() == 0.0002061194322149
    assert bf.get_next_funding_time() == 1703116800000.0
    assert bf.get_server_time() == 1703070685309.0
    assert bf.get_event_type() == "FundingEvent"
    assert bf.get_current_funding_rate() == 0.0000792386885340
    assert bf.get_current_funding_time() == 1703088000000.0
    assert bf.get_max_funding_rate() == 0.00375
    assert bf.get_min_funding_rate() == -0.00375
    assert bf.get_settlement_funding_rate() == 0.0001418433662153
    assert bf.get_settlement_status() == "settled"
    assert bf.get_method() == "next_period"


def test_okx_req_funding_rate():
    bf = OkxFundingRateData(data['data'][0], "BTC-USDT", "SWAP", True)
    bf.init_data()
    assert_value(bf)


def test_okx_wss_funding_rate():
    bf = OkxFundingRateData(json.dumps(data), "BTC-USDT", "SWAP", False)
    bf.init_data()
    assert_value(bf)


if __name__ == "__main__":
    test_okx_req_funding_rate()
