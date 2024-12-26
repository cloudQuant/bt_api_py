from bt_api_py.containers.fundingrates.binance_funding_rate import BinanceRequestFundingRateData, BinanceWssFundingRateData


def test_binance_request_funding_rate():
    data = {
        "symbol": "BTCUSDT",  # 交易对
        "markPrice": "11793.63104562",  # 标记价格
        "indexPrice": "11781.80495970",  # 指数价格
        "estimatedSettlePrice": "11781.16138815",  # 预估结算价,仅在交割开始前最后一小时有意义
        "lastFundingRate": "0.00038246",  # 最近更新的资金费率
        "nextFundingTime": 1597392000000,  # 下次资金费时间
        "interestRate": "0.00010000",  # 标的资产基础利率
        "time": 1597370495002  # 更新时间
    }
    bf = BinanceRequestFundingRateData(data, "BTC-USDT", "SWAP", True)
    bf.init_data()
    assert bf.get_pre_funding_rate() is None
    assert bf.get_next_funding_rate() is None
    assert bf.get_pre_funding_time() is None
    assert bf.get_next_funding_time() == 1597392000000.0
    assert bf.get_current_funding_time() is None
    assert bf.get_current_funding_rate() == 0.00038246
    assert bf.get_server_time() == 1597370495002.0
    assert bf.get_event_type() == "FundingEvent"
    assert bf.get_symbol_name() == "BTC-USDT"
    assert bf.get_funding_rate_symbol_name() == "BTCUSDT"


def test_binance_funding_rate():
    data = {
        "e": "markPriceUpdate",  # 事件类型
        "E": 1562305380000,  # 事件时间
        "s": "BTCUSDT",  # 交易对
        "p": "11794.15000000",  # 标记价格
        "i": "11784.62659091",  # 现货指数价格
        "P": "11784.25641265",  # 预估结算价,仅在结算前最后一小时有参考价值
        "r": "0.00038167",  # 资金费率
        "T": 1562306400000  # 下次资金时间
    }
    bf = BinanceWssFundingRateData(data, "BTC-USDT", "PERPETUAL", True)
    bf.init_data()
    assert bf.get_pre_funding_rate() is None
    assert bf.get_current_funding_rate() == 0.00038167
    assert bf.get_pre_funding_time() is None
    assert bf.get_next_funding_time() == 1562306400000.0
    assert bf.get_server_time() == 1562305380000.0
    assert bf.get_event_type() == "FundingEvent"


if __name__ == "__main__":
    test_binance_request_funding_rate()
