import json
from bt_api_py.containers.orders.binance_order import (BinanceSwapWssOrderData,
                                                       BinanceRequestOrderData,
                                                       BinanceSpotWssOrderData,
                                                       BinanceForceOrderData)
from bt_api_py.containers.orders.order import OrderStatus

def test_binance_force_order_data():
    data = {
            "e":"forceOrder",
            "E":1568014460893,
            "o":{
                "s":"BTCUSDT",
                "S":"SELL",
                "o":"LIMIT",
                "f":"IOC",
                "q":"0.014",
                "p":"9910",
                "ap":"9910",
                "X":"FILLED",
                "l":"0.014",
                "z":"0.014",
                "T":1568014460893,
                }
            }
    fo = BinanceForceOrderData(data, "BTC-USDT", "SWAP", True)
    fo.init_data()
    assert fo.get_trade_time() == 1568014460893
    assert fo.get_order_time_in_force() == "IOC"
    assert fo.get_asset_type() == "SWAP"
    assert fo.get_last_trade_volume() == 0.014
    assert fo.get_total_trade_volume() == 0.014
    assert fo.get_symbol_name() == "BTC-USDT"
    assert fo.get_order_side() == "SELL"
    assert fo.get_order_type() == "LIMIT"
    assert fo.get_order_price() == 9910
    assert fo.get_order_qty() == 0.014
    assert fo.get_order_avg_price() == 9910
    assert fo.get_order_status() == OrderStatus.COMPLETED



def test_binance_spot_wss_order():
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
    spot_wss_data = BinanceSpotWssOrderData(data, data['s'], "SPOT", True)
    spot_wss_data.init_data()
    assert spot_wss_data is not None
    assert spot_wss_data.get_order_id() == "1110157667"
    assert spot_wss_data.get_server_time() == 1709103527340.0
    assert spot_wss_data.get_trade_id() == -1.0
    assert spot_wss_data.get_client_order_id() == 'quYaDMgXvQGpI0M2Uztcdl'
    assert spot_wss_data.get_executed_qty() == 0.0
    assert spot_wss_data.get_order_size() == 2.0
    assert spot_wss_data.get_asset_type() == "SPOT"
    assert spot_wss_data.get_order_price() == 3.379
    assert spot_wss_data.get_reduce_only() is None
    assert spot_wss_data.get_order_side() == "BUY"
    assert spot_wss_data.get_order_status() == OrderStatus.CANCELED
    assert spot_wss_data.get_order_symbol_name() == 'OPUSDT'
    assert spot_wss_data.get_order_time_in_force() == "GTC"
    assert spot_wss_data.get_order_type() == "LIMIT"


def test_binance_wss_order():
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
    bo = BinanceSwapWssOrderData(data, "BTC-USDT", "PERPETUAL", True)
    bo.init_data()
    assert bo.get_server_time() == 1568879465651.0
    assert bo.get_exchange_name() == "BINANCE"
    assert bo.get_trade_id() == 0
    assert bo.get_client_order_id() == "TEST"
    assert bo.get_cum_quote() is None
    assert bo.get_executed_qty() == 0.0
    assert bo.get_order_id() == "8886774"
    assert bo.get_order_size() == 0.001
    assert bo.get_order_price() == 0.0
    assert bo.get_reduce_only() is False
    assert bo.get_order_status() == OrderStatus.ACCEPTED
    assert bo.get_trailing_stop_price() == 7103.04
    assert bo.get_trailing_stop_trigger_price() == 7476.89
    assert bo.get_trailing_stop_callback_rate() == 5.0
    assert bo.get_order_symbol_name() == "BTCUSDT"
    assert bo.get_order_time_in_force() == "GTC"
    assert bo.get_order_type() == "TRAILING_STOP_MARKET"
    assert bo.get_trailing_stop_trigger_price_type() == "CONTRACT_PRICE"
    assert bo.get_order_avg_price() == 0.0
    assert bo.get_origin_order_type() == "TRAILING_STOP_MARKET"
    assert bo.get_position_side() == "LONG"
    assert bo.get_close_position() is False


def test_binance_req_order():
    data = {
        "clientOrderId": "testOrder",  # 用户自定义的订单号
        "cumQty": "0",
        "cumQuote": "0",  # 成交金额
        "executedQty": "0",  # 成交量
        "orderId": 22542179,  # 系统订单号
        "avgPrice": "0.00000",  # 平均成交价
        "origQty": "10",  # 原始委托数量
        "price": "0",  # 委托价格
        "reduceOnly": 'false',  # 仅减仓
        "side": "SELL",  # 买卖方向
        "positionSide": "SHORT",  # 持仓方向
        "status": "NEW",  # 订单状态
        "stopPrice": "0",  # 触发价，对`TRAILING_STOP_MARKET`无效
        "closePosition": 'false',  # 是否条件全平仓
        "symbol": "BTCUSDT",  # 交易对
        "timeInForce": "GTD",  # 有效方法
        "type": "TRAILING_STOP_MARKET",  # 订单类型
        "origType": "TRAILING_STOP_MARKET",  # 触发前订单类型
        "activatePrice": "9020",  # 跟踪止损激活价格, 仅`TRAILING_STOP_MARKET` 订单返回此字段
        "priceRate": "0.3",  # 跟踪止损回调比例, 仅`TRAILING_STOP_MARKET` 订单返回此字段
        "updateTime": 1566818724722,  # 更新时间
        "workingType": "CONTRACT_PRICE",  # 条件价格触发类型
        "priceProtect": 'false',  # 是否开启条件单触发保护
        "priceMatch": "NONE",  # 盘口价格下单模式
        "selfTradePreventionMode": "NONE",  # 订单自成交保护模式
        "goodTillDate": 1693207680000  # 订单TIF为GTD时的自动取消时间
    }
    bo = BinanceRequestOrderData(data, "BTC-USDT", "PERPETUAL", True)
    bo.init_data()
    assert bo.get_server_time() == 1566818724722.0
    assert bo.get_exchange_name() == "BINANCE"
    assert bo.get_trade_id() is None
    assert bo.get_client_order_id() == "testOrder"
    assert bo.get_cum_quote() == 0.0
    assert bo.get_executed_qty() == 0.0
    assert bo.get_order_id() == "22542179"
    assert bo.get_order_size() == 10.0
    assert bo.get_order_price() == 0.0
    assert bo.get_reduce_only() is False
    assert bo.get_order_status() == OrderStatus.ACCEPTED
    assert bo.get_trailing_stop_price() == 0.0
    assert bo.get_trailing_stop_trigger_price() == 9020
    assert bo.get_trailing_stop_callback_rate() == 0.3
    assert bo.get_order_symbol_name() == "BTCUSDT"
    assert bo.get_order_time_in_force() == "GTD"
    assert bo.get_order_type() == "TRAILING_STOP_MARKET"
    assert bo.get_trailing_stop_trigger_price_type() == "CONTRACT_PRICE"
    assert bo.get_order_avg_price() == 0.0
    assert bo.get_origin_order_type() == "TRAILING_STOP_MARKET"
    assert bo.get_position_side() == "SHORT"
    assert bo.get_close_position() is False


if __name__ == "__main__":
    test_binance_wss_order()
    test_binance_req_order()
