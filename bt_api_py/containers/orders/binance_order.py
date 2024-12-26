import json
import time
from bt_api_py.containers.orders.order import OrderData
from bt_api_py.functions.utils import from_dict_get_float, from_dict_get_string, from_dict_get_bool


class BinanceForceOrderData(object):
    def __init__(self, order_info, symbol_name, asset_type, has_been_json_encoded=False):
        self.order_info = order_info
        self.exchange_name = "BINANCE"
        self.local_update_time = time.time()  # 本地时间戳
        self.symbol_name = symbol_name
        self.asset_type = asset_type
        self.has_been_json_encoded = has_been_json_encoded
        self.order_data = self.order_info["o"] if has_been_json_encoded else None
        self.all_data = None
        self.has_been_init_data = False
        self.server_time = None
        self.order_symbol_name = None
        self.order_side = None
        self.order_type = None
        self.order_time_in_force = None
        self.order_price = None
        self.order_qty = None
        self.order_avg_price = None
        self.order_status = None
        self.trade_time = None
        self.last_trade_volume = None
        self.total_trade_volume = None

    def init_data(self):
        if self.has_been_init_data:
            return
        if not self.has_been_json_encoded:
            self.order_info = json.loads(self.order_info)
            self.order_data = self.order_info["o"]
        self.server_time = float(from_dict_get_float(self.order_info, "E"))
        self.order_symbol_name = from_dict_get_string(self.order_data, "s")
        self.order_side = from_dict_get_string(self.order_data, "S")
        self.order_type = from_dict_get_string(self.order_data, "o")
        self.order_time_in_force = from_dict_get_string(self.order_data, "f")
        self.order_price = from_dict_get_float(self.order_data, "p")
        self.order_qty = from_dict_get_float(self.order_data, "q")
        self.order_avg_price = from_dict_get_float(self.order_data, "ap")
        self.order_status = from_dict_get_string(self.order_data, "X")
        self.trade_time = from_dict_get_float(self.order_data, "T")
        self.last_trade_volume = from_dict_get_float(self.order_data, "l")
        self.total_trade_volume = from_dict_get_float(self.order_data, "z")


    def get_all_data(self):
        if self.all_data is None:
            self.all_data = {
                "exchange_name": self.exchange_name,
                "symbol_name": self.symbol_name,
                "server_time": self.server_time,
                "local_update_time": self.local_update_time,
                "asset_type": self.asset_type,
                "order_symbol_name": self.order_symbol_name,
                "order_side": self.order_side,
                "order_type": self.order_type,
                "order_time_in_force": self.order_time_in_force,
                "order_price": self.order_price,
                "order_qty": self.order_qty,
                "order_avg_price": self.order_avg_price,
                "order_status": self.order_status,
                "trade_time": self.trade_time,
                "last_trade_volume": self.last_trade_volume,
                "total_trade_volume": self.total_trade_volume
            }
        return self.all_data

    def __str__(self):
        self.init_data()
        return json.dumps(self.get_all_data())

    def __repr__(self):
        return self.__str__()

    def get_exchange_name(self):
        """# 交易所名称"""
        return self.exchange_name

    def get_symbol_name(self):
        """ # 品种名称"""
        return self.symbol_name

    def get_asset_type(self):
        return self.asset_type

    def get_server_time(self):
        """# 服务器时间戳"""
        return self.server_time

    def get_local_update_time(self):
        """# 本地时间戳"""
        return self.local_update_time

    def get_order_price(self):
        """# 订单价格"""
        return self.order_price

    def get_order_qty(self):
        return self.order_qty

    def get_order_side(self):
        """# 订单方向"""
        return self.order_side

    def get_order_status(self):
        """# 订单状态"""
        return self.order_status

    def get_order_symbol_name(self):
        """# 品种"""
        return self.order_symbol_name

    def get_order_time_in_force(self):
        """# 订单有效期类型"""
        return self.order_time_in_force

    def get_order_type(self):
        """# 订单类型"""
        return self.order_type

    def get_order_avg_price(self):
        """# 平均价格"""
        return self.order_avg_price

    def get_trade_time(self):
        return self.trade_time

    def get_last_trade_volume(self):
        return self.last_trade_volume

    def get_total_trade_volume(self):
        return self.total_trade_volume



class BinanceOrderData(OrderData):
    """ 订单类，用于确定订单的属性和方法
    """

    def __init__(self, order_info, symbol_name, asset_type, has_been_json_encoded=False):
        super(BinanceOrderData, self).__init__(order_info, has_been_json_encoded)
        self.exchange_name = "BINANCE"
        self.local_update_time = time.time()  # 本地时间戳
        self.symbol_name = symbol_name
        self.asset_type = asset_type
        self.order_data = self.order_info if has_been_json_encoded else None
        self.server_time = None
        self.trade_id = None
        self.client_order_id = None
        self.cum_quote = None
        self.executed_qty = None
        self.order_id = None
        self.order_size = None
        self.order_price = None
        self.reduce_only = None
        self.order_side = None
        self.order_status = None
        self.order_symbol_name = None
        self.order_type = None
        self.order_time_in_force = None
        self.order_avg_price = None
        self.origin_order_type = None
        self.position_side = None
        self.close_position = None
        self.trailing_stop_price = None
        self.trailing_stop_trigger_price = None
        self.trailing_stop_trigger_price_type = None
        self.trailing_stop_callback_rate = None
        self.all_data = None
        self.has_been_init_data = False

    def init_data(self):
        raise NotImplementedError

    def get_all_data(self):
        if self.all_data is None:
            self.all_data = {
                "exchange_name": self.exchange_name,
                "symbol_name": self.symbol_name,
                "server_time": self.server_time,
                "local_update_time": self.local_update_time,
                "asset_type": self.asset_type,
                "order_id": self.order_id,
                "client_order_id": self.client_order_id,
                "order_symbol_name": self.order_symbol_name,
                "order_type": self.order_type,
                "order_status": self.order_status,
                "order_size": self.order_size,
                "order_price": self.order_price,
                "trade_id": self.trade_id,
                "position_side": self.position_side,
                "cum_quote": self.cum_quote,
                "executed_qty": self.executed_qty,
                "order_avg_price": self.order_avg_price,
                "reduce_only": self.reduce_only,
                "trailing_stop_callback_rate": self.trailing_stop_callback_rate,
                "trailing_stop_price": self.trailing_stop_price,
                "trailing_stop_trigger_price": self.trailing_stop_trigger_price,
                "trailing_stop_trigger_price_type": self.trailing_stop_trigger_price_type,
                "close_position": self.close_position,
                "origin_order_type": self.origin_order_type,
                "order_time_in_force": self.order_time_in_force,
            }
        return self.all_data

    def __str__(self):
        self.init_data()
        return json.dumps(self.get_all_data())

    def __repr__(self):
        return self.__str__()

    def get_exchange_name(self):
        """# 交易所名称"""
        return self.exchange_name

    def get_symbol_name(self):
        """ # 品种名称"""
        return self.symbol_name

    def get_asset_type(self):
        return self.asset_type

    def get_server_time(self):
        """# 服务器时间戳"""
        return self.server_time

    def get_local_update_time(self):
        """# 本地时间戳"""
        return self.local_update_time

    def get_trade_id(self):
        """# 交易所返回唯一成交id"""
        return self.trade_id

    def get_client_order_id(self):
        """# 客户端自定订单ID"""
        return self.client_order_id

    def get_cum_quote(self):
        """# 成交金额"""
        return self.cum_quote

    def get_executed_qty(self):
        """# 已执行的成交量"""
        return self.executed_qty

    def get_order_id(self):
        """# 订单id"""
        return self.order_id

    def get_order_size(self):
        """# 订单原始数量"""
        return self.order_size

    def get_order_price(self):
        """# 订单价格"""
        return self.order_price

    def get_reduce_only(self):
        """# 是否是只减仓单"""
        return self.reduce_only

    def get_order_side(self):
        """# 订单方向"""
        return self.order_side

    def get_order_status(self):
        """# 订单状态"""
        return self.order_status

    def get_trailing_stop_price(self):
        """# 条件单止损价格"""
        return self.trailing_stop_price

    def get_trailing_stop_trigger_price(self):
        """# 跟踪止损激活价格"""
        return self.trailing_stop_trigger_price

    def get_trailing_stop_callback_rate(self):
        """# 跟踪止损回调比例"""
        return self.trailing_stop_callback_rate

    def get_order_symbol_name(self):
        """# 品种"""
        return self.order_symbol_name

    def get_order_time_in_force(self):
        """# 订单有效期类型"""
        return self.order_time_in_force

    def get_order_type(self):
        """# 订单类型"""
        return self.order_type

    def get_trailing_stop_trigger_price_type(self):
        """# 触发价类型"""
        return self.trailing_stop_trigger_price_type

    def get_order_avg_price(self):
        """# 平均价格"""
        return self.order_avg_price

    def get_origin_order_type(self):
        """# 原始订单类型"""
        return self.origin_order_type

    def get_position_side(self):
        """# 持仓方向"""
        return self.position_side

    def get_close_position(self):
        """# 是否为触发平仓单; 仅在条件订单情况下会推送此字段"""
        return self.close_position

    def get_stop_loss_price(self):
        # stop loss price
        return None

    def get_stop_loss_trigger_price(self):
        # stop_loss_trigger price
        return None

    def get_stop_loss_trigger_price_type(self):
        # stop loss trigger price type
        return None

    def get_take_profit_price(self):
        # get stop profit price
        return None

    def get_take_profit_trigger_price(self):
        # get stop profit trigger price
        return None

    def get_take_profit_trigger_price_type(self):
        # get stop profit trigger price type
        return None


class BinanceRequestOrderData(BinanceOrderData):
    """ 订单类，用于确定订单的属性和方法
    """

    def init_data(self):
        if not self.has_been_json_encoded:
            self.order_info = json.loads(self.order_info)
            self.order_data = self.order_info['data']
            self.has_been_json_encoded = True
        if self.has_been_init_data:
            return self
        self.server_time = from_dict_get_float(self.order_data, "updateTime")
        self.trade_id = from_dict_get_float(self.order_data, "tradeId")
        self.client_order_id = from_dict_get_string(self.order_data, "clientOrderId")
        self.cum_quote = from_dict_get_float(self.order_data, "cumQuote")
        self.executed_qty = from_dict_get_float(self.order_data, "cumQty")
        self.order_id = from_dict_get_string(self.order_data, "orderId")
        self.order_size = from_dict_get_float(self.order_data, "origQty")
        self.order_price = from_dict_get_float(self.order_data, "price")
        self.reduce_only = from_dict_get_bool(self.order_data, 'reduceOnly')
        self.order_side = from_dict_get_string(self.order_data, "side")
        self.order_status = from_dict_get_string(self.order_data, "status")
        self.order_symbol_name = from_dict_get_string(self.order_data, "symbol")
        self.order_type = from_dict_get_string(self.order_data, 'type')
        self.order_time_in_force = from_dict_get_string(self.order_data, "timeInForce")
        self.order_avg_price = from_dict_get_float(self.order_data, "avgPrice")
        self.origin_order_type = from_dict_get_string(self.order_data, "origType")
        self.position_side = from_dict_get_string(self.order_data, "positionSide")
        self.close_position = from_dict_get_bool(self.order_data, "closePosition")
        self.trailing_stop_price = from_dict_get_float(self.order_data, "stopPrice")
        self.trailing_stop_trigger_price = from_dict_get_float(self.order_data, "activatePrice")
        self.trailing_stop_trigger_price_type = from_dict_get_string(self.order_data, "workingType")
        self.trailing_stop_callback_rate = from_dict_get_float(self.order_data, "priceRate")
        self.has_been_init_data = True
        return self


class BinanceSwapWssOrderData(BinanceOrderData):
    """ 订单类，用于确定订单的属性和方法
    """
    def init_data(self):
        if not self.has_been_json_encoded:
            self.order_data = json.loads(self.order_info)
            self.has_been_json_encoded = True
        if self.has_been_init_data:
            return self
        self.server_time = from_dict_get_float(self.order_data, "E")
        order_dict = self.order_data['o']
        self.trade_id = from_dict_get_float(order_dict, "t")
        self.client_order_id = from_dict_get_string(order_dict, "c")
        self.executed_qty = from_dict_get_float(order_dict, "z")
        self.order_id = from_dict_get_string(order_dict, "i")
        self.order_size = from_dict_get_float(order_dict, "q")
        self.order_price = from_dict_get_float(order_dict, "p")
        self.reduce_only = from_dict_get_bool(order_dict, "R")
        self.order_side = from_dict_get_string(order_dict, "S")
        self.order_status = from_dict_get_string(order_dict, "X")
        self.trailing_stop_price = from_dict_get_float(order_dict, "sp")
        self.trailing_stop_trigger_price = from_dict_get_float(order_dict, "AP")
        self.trailing_stop_callback_rate = from_dict_get_float(order_dict, "cr")
        self.order_symbol_name = from_dict_get_string(order_dict, "s")
        self.order_time_in_force = from_dict_get_string(order_dict, "f")
        self.order_type = from_dict_get_string(order_dict, "o")
        self.trailing_stop_trigger_price_type = from_dict_get_string(order_dict, "wt")
        self.order_avg_price = from_dict_get_float(order_dict, "ap")
        self.origin_order_type = from_dict_get_string(order_dict, "ot")
        self.position_side = from_dict_get_string(order_dict, "ps")
        self.close_position = from_dict_get_bool(order_dict, "cp")
        self.has_been_init_data = True
        return self


class BinanceSpotWssOrderData(BinanceOrderData):
    """ spot订单类，用于确定订单的属性和方法
    """
    def init_data(self):
        if not self.has_been_json_encoded:
            self.order_data = json.loads(self.order_info)
            self.has_been_json_encoded = True
        if self.has_been_init_data:
            return self
        self.server_time = from_dict_get_float(self.order_data, "E")
        self.trade_id = from_dict_get_float(self.order_data, "t")
        self.client_order_id = from_dict_get_string(self.order_data, "c")
        self.cum_quote = from_dict_get_float(self.order_data, "cum_quote")
        self.executed_qty = from_dict_get_float(self.order_data, "z")
        self.order_id = from_dict_get_string(self.order_data, "i")
        self.order_size = from_dict_get_float(self.order_data, "q")
        self.order_price = from_dict_get_float(self.order_data, "p")
        # self.reduce_only = from_dict_get_bool(self.order_data, "R")
        self.order_side = from_dict_get_string(self.order_data, "S")
        self.order_status = from_dict_get_string(self.order_data, "X")
        # self.trailing_stop_price = from_dict_get_float(self.order_data, "sp")
        # self.trailing_stop_trigger_price = from_dict_get_float(self.order_data, "AP")
        # self.trailing_stop_callback_rate = from_dict_get_float(self.order_data, "cr")
        self.order_symbol_name = from_dict_get_string(self.order_data, "s")
        self.order_time_in_force = from_dict_get_string(self.order_data, "f")
        self.order_type = from_dict_get_string(self.order_data, "o")
        # self.trailing_stop_trigger_price_type = from_dict_get_float(self.order_data, "wt")
        # self.order_avg_price = from_dict_get_float(self.order_data, "ap")
        # self.origin_order_type = from_dict_get_string(self.order_data, "ot")
        # self.position_side = from_dict_get_string(self.order_data, "ps")
        # self.close_position = from_dict_get_float(self.order_data, "cp")
        self.has_been_init_data = True
        return self
