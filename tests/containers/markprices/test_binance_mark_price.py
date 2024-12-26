# import json
from bt_api_py.containers.markprices.binance_mark_price import BinanceWssMarkPriceData, BinanceRequestMarkPriceData


def test_binance_request_mark_price():
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
    bp = BinanceRequestMarkPriceData(data, "BTC-USDT", "SWAP", True)
    bp.init_data()
    assert bp.get_server_time() == 1597370495002.0
    assert bp.get_exchange_name() == "BINANCE"
    assert bp.get_symbol_name() == "BTC-USDT"
    assert bp.get_mark_price() == 11793.63104562
    assert bp.get_index_price() == 11781.80495970
    assert bp.get_settlement_price() == 11781.16138815
    assert bp.get_event() == "MarkPriceEvent"


def test_binance_mark_price():
    data = {
            "e": "markPriceUpdate",     # 事件类型
            "E": 1562305380000,         # 事件时间
            "s": "BTCUSDT",             # 交易对
            "p": "11794.15000000",      # 标记价格
            "i": "11784.62659091",      # 现货指数价格
            "P": "11784.25641265",      # 预估结算价,仅在结算前最后一小时有参考价值
            "r": "0.00038167",          # 资金费率
            "T": 1562306400000          # 下次资金时间
          }
    bp = BinanceWssMarkPriceData(data, "BTC-USDT", "PERPETUAL", True)
    bp.init_data()
    assert bp.get_server_time() == 1562305380000.0
    assert bp.get_exchange_name() == "BINANCE"
    assert bp.get_symbol_name() == "BTC-USDT"
    assert bp.get_mark_price_symbol_name() == "BTCUSDT"
    assert bp.get_mark_price() == 11794.15000000
    assert bp.get_index_price() == 11784.62659091
    assert bp.get_settlement_price() == 11784.25641265
    assert bp.get_event() == "MarkPriceEvent"


if __name__ == "__main__":
    test_binance_mark_price()
