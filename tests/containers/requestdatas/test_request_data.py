from bt_api_py.containers.requestdatas.request_data import RequestData
from bt_api_py.containers.orders.binance_order import BinanceRequestOrderData


def test_request_data():
    datas = {
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

    def _get_open_orders_normalize_function(input_data, extra_data_):
        status = True if input_data is not None else False
        symbol_name = extra_data_["symbol_name"]
        asset_type = extra_data_["asset_type"]
        if isinstance(input_data, list):
            data = [BinanceRequestOrderData(i, symbol_name, asset_type, True)
                    for i in input_data]
        elif isinstance(input_data, dict):
            data = [BinanceRequestOrderData(input_data, symbol_name, asset_type, True)]
        else:
            data = []
        return data, status

    extra_data = {
        "request_type": "get_open_orders",
        "symbol_name": "BTCUSDT",
        "asset_type": "SWAP",
        "exchange_name": "BINANCE",
        "normalize_function": _get_open_orders_normalize_function,
    }
    request_data = RequestData(datas, extra_data=extra_data, status=False)

    request_data.init_data()
    assert request_data.get_event() == "RequestEvent"
    assert request_data.get_status() is True
    assert request_data.get_symbol_name() == "BTCUSDT"
    assert request_data.get_asset_type() == "SWAP"
    assert request_data.get_exchange_name() == "BINANCE"
    assert len(request_data.get_data()) > 0
    assert isinstance(request_data.get_extra_data(), dict)
