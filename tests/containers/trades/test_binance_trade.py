# import json
from bt_api_py.containers.trades.binance_trade import (BinanceSwapWssTradeData,
                                                       BinanceRequestTradeData,
                                                       BinanceSpotWssTradeData,
                                                       BinanceAggTradeData)


def test_binance_agg_trade():
    data = {
          "e": "aggTrade",
          "E": 123456789,
          "s": "BNBUSDT",
          "a": 5933014,
          "p": "0.001",
          "q": "100",
          "f": 100,
          "l": 105,
          "T": 123456785,
          "m": True
        }
    agg_trade = BinanceAggTradeData(data, "BNB-USDT", "SWAP", True)
    agg_trade.init_data()
    assert agg_trade.get_first_trade_id() == "100"
    assert agg_trade.get_last_trade_id() == "105"
    assert agg_trade.get_server_time() == 123456789
    assert agg_trade.get_trade_id() == '5933014'
    assert agg_trade.get_trade_price() == 0.001
    assert agg_trade.get_trade_volume() == 100
    assert agg_trade.get_trade_time() == 123456785
    assert agg_trade.get_trade_symbol_name() == "BNBUSDT"
    assert agg_trade.get_symbol_name() == "BNB-USDT"


def test_binance_spot_wss_trade():
    data = {'e': 'executionReport', 'E': 1709103527340,
            's': 'OPUSDT', 'c': 'quYaDMgXvQGpI0M2Uztcdl', 'S': 'BUY',
            'o': 'LIMIT', 'f': 'GTC', 'q': '2.00000000', 'p': '3.37900000',
            'P': '0.00000000', 'F': '0.00000000',
            'g': -1, 'C': '784164848349476186', 'x': 'CANCELED',
            'X': 'CANCELED', 'r': 'NONE', 'i': 1110157667,
            'l': '0.00000000', 'z': '0.00000000', 'L': '0.00000000',
            'n': '0', 'N': None, 'T': 1709103527340, 't': -1,
            'I': 2284358278, 'w': False, 'm': False, 'M': False,
            'O': 1709103527220, 'Z': '0.00000000',
            'Y': '0.00000000', 'Q': '0.00000000', 'W': 1709103527220,
            'V': 'EXPIRE_MAKER'}
    spot_wss_trade = BinanceSpotWssTradeData(data, data['s'], "SPOT", True)
    spot_wss_trade.init_data()
    assert spot_wss_trade.get_trade_id() == "-1"
    assert spot_wss_trade.get_trade_price() == 0.0
    assert spot_wss_trade.get_trade_volume() == 0.0
    assert spot_wss_trade.get_trade_accumulate_volume() == 0.0


def test_binance_req_trade():
    data = {
        "buyer": 'false',  # 是否是买方
        "commission": "-0.07819010",  # 手续费
        "commissionAsset": "USDT",  # 手续费计价单位
        "id": 698759,  # 交易ID
        "maker": 'false',  # 是否是挂单方
        "orderId": 25851813,  # 订单编号
        "price": "7819.01",  # 成交价
        "qty": "0.002",  # 成交量
        "quoteQty": "15.63802",  # 成交额
        "realizedPnl": "-0.91539999",  # 实现盈亏
        "side": "SELL",  # 买卖方向
        "positionSide": "SHORT",  # 持仓方向
        "symbol": "BTCUSDT",  # 交易对
        "time": 1569514978020  # 时间
    }
    bo = BinanceRequestTradeData(data, "BTC-USDT", "PERPETUAL", True)
    bo.init_data()
    assert bo.get_server_time() == 1569514978020.0
    assert bo.get_exchange_name() == "BINANCE"
    assert bo.get_asset_type() == "PERPETUAL"
    assert bo.get_trade_id() == '698759'
    assert bo.get_trade_symbol_name() == "BTCUSDT"
    assert bo.get_order_id() == "25851813"
    assert bo.get_client_order_id() is None
    assert bo.get_trade_side() == "SELL"
    assert bo.get_trade_offset() is None
    assert bo.get_trade_price() == 7819.01
    assert bo.get_trade_volume() == 0.002
    assert bo.get_trade_accumulate_volume() is None
    assert bo.get_trade_type() == "taker"
    assert bo.get_trade_time() == 1569514978020.0
    assert bo.get_trade_fee() == -0.07819010
    assert bo.get_trade_fee_symbol() == "USDT"


def test_binance_wss_trade():
    data = {
        "e": "ORDER_TRADE_UPDATE",  # 事件类型
        "E": 1568879465651,  # 事件时间
        "T": 1568879465650,  # 撮合时间
        "o": {
            "s": "BTCUSDT",  # 交易对
            "c": "TEST",  # 客户端自定订单ID
            # 特殊的自定义订单ID:
            # "autoclose-"开头的字符串: 系统强平订单
            # "adl_autoclose": ADL自动减仓订单
            # "settlement_autoclose-": 下架或交割的结算订单
            "S": "SELL",  # 订单方向
            "o": "TRAILING_STOP_MARKET",  # 订单类型
            "f": "GTC",  # 有效方式
            "q": "0.001",  # 订单原始数量
            "p": "0",  # 订单原始价格
            "ap": "0",  # 订单平均价格
            "sp": "7103.04",  # 条件订单触发价格，对追踪止损单无效
            "x": "NEW",  # 本次事件的具体执行类型
            "X": "NEW",  # 订单的当前状态
            "i": 8886774,  # 订单ID
            "l": "0",  # 订单末次成交量
            "z": "0",  # 订单累计已成交量
            "L": "0",  # 订单末次成交价格
            "N": "USDT",  # 手续费资产类型
            "n": "0",  # 手续费数量
            "T": 1568879465650,  # 成交时间
            "t": 0,  # 成交ID
            "b": "0",  # 买单净值
            "a": "9.91",  # 卖单净值
            "m": False,  # 该成交是作为挂单成交吗？
            "R": False,  # 是否是只减仓单
            "wt": "CONTRACT_PRICE",  # 触发价类型
            "ot": "TRAILING_STOP_MARKET",  # 原始订单类型
            "ps": "LONG",  # 持仓方向
            "cp": False,  # 是否为触发平仓单; 仅在条件订单情况下会推送此字段
            "AP": "7476.89",  # 追踪止损激活价格, 仅在追踪止损单时会推送此字段
            "cr": "5.0",  # 追踪止损回调比例, 仅在追踪止损单时会推送此字段
            "pP": False,  # 是否开启条件单触发保护
            "si": 0,  # 忽略
            "ss": 0,  # 忽略
            "rp": "0",  # 该交易实现盈亏
            "V": "EXPIRE_TAKER",  # 自成交防止模式
            "pm": "OPPONENT",  # 价格匹配模式
            "gtd": 0  # TIF为GTD的订单自动取消时间
        }
    }
    bo = BinanceSwapWssTradeData(data, "BTCUSDT", "PERPETUAL", True)
    bo.init_data()
    assert bo.get_server_time() == 1568879465651.0
    assert bo.get_exchange_name() == "BINANCE"
    assert bo.get_asset_type() == "PERPETUAL"
    assert bo.get_trade_id() == '0'
    assert bo.get_trade_symbol_name() == "BTCUSDT"
    assert bo.get_order_id() == "8886774"
    assert bo.get_client_order_id() == "TEST"
    assert bo.get_trade_side() == "LONG"
    assert bo.get_trade_offset() is None
    assert bo.get_trade_price() == 0.0
    assert bo.get_trade_volume() == 0.0
    assert bo.get_trade_accumulate_volume() == 0.0
    assert bo.get_trade_type() == "taker"
    assert bo.get_trade_time() == 1568879465650.0
    assert bo.get_trade_fee() == 0.0
    assert bo.get_trade_fee_symbol() == "USDT"


if __name__ == "__main__":
    test_binance_wss_trade()
    test_binance_req_trade()
