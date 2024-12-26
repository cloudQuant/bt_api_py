import json
import time
from bt_api_py.containers.orders.order import OrderData
from bt_api_py.functions.utils import from_dict_get_float, from_dict_get_string, from_dict_get_bool


class OkxOrderData(OrderData):
    """ 订单类，用于确定订单的属性和方法
    """

    def __init__(self, order_info, symbol_name, asset_type, has_been_json_encoded=False):
        super(OkxOrderData, self).__init__(order_info, has_been_json_encoded)
        self.exchange_name = "OKX"
        self.symbol_name = symbol_name
        self.local_update_time = time.time()  # 本地时间戳
        self.asset_type = asset_type
        self.order_data = self.order_info if has_been_json_encoded else None
        self.server_time = None
        self.trade_id = None
        self.client_order_id = None
        self.executed_qty = None
        self.order_id = None
        self.order_size = None
        self.order_price = None
        self.reduce_only = None
        self.order_side = None
        self.order_status = None
        self.order_symbol_name = None
        self.order_type = None
        self.order_avg_price = None
        self.position_side = None
        self.take_profit_price = None
        self.take_profit_trigger_price = None
        self.take_profit_trigger_price_type = None
        self.stop_loss_price = None
        self.stop_loss_trigger_price = None
        self.stop_loss_trigger_price_type = None
        self.all_data = None
        self.has_been_init_data = False

    def init_data(self):
        if not self.has_been_json_encoded:
            self.order_info = json.loads(self.order_info)
            self.order_data = self.order_info['data']
            self.has_been_json_encoded = True
        if self.has_been_init_data:
            return self
        self.server_time = from_dict_get_float(self.order_data, 'uTime')
        self.trade_id = from_dict_get_float(self.order_data, "tradeId")
        self.client_order_id = from_dict_get_string(self.order_data, 'clOrdId')
        self.executed_qty = from_dict_get_float(self.order_data, 'accFillSz')
        self.order_id = from_dict_get_string(self.order_data, 'ordId')
        self.order_size = from_dict_get_float(self.order_data, 'sz')
        self.order_price = from_dict_get_float(self.order_data, 'px')
        self.reduce_only = from_dict_get_bool(self.order_data, 'reduceOnly')
        self.order_side = from_dict_get_string(self.order_data, 'side')
        self.order_status = from_dict_get_string(self.order_data, 'state')
        self.order_symbol_name = from_dict_get_string(self.order_data, 'instId')
        self.order_type = from_dict_get_string(self.order_data, 'ordType')
        self.order_avg_price = from_dict_get_float(self.order_data, 'avgPx')
        self.position_side = from_dict_get_string(self.order_data, 'posSide')
        self.take_profit_price = from_dict_get_float(self.order_data, 'tpOrdPx')
        self.take_profit_trigger_price = from_dict_get_float(self.order_data, 'tpTriggerPx')
        self.take_profit_trigger_price_type = from_dict_get_string(self.order_data, 'tpTriggerPxType')
        self.stop_loss_price = from_dict_get_float(self.order_data, 'slOrdPx')
        self.stop_loss_trigger_price = from_dict_get_float(self.order_data, 'slTriggerPx')
        self.stop_loss_trigger_price_type = from_dict_get_string(self.order_data, 'slTriggerPxType')
        self.has_been_init_data = True
        return self

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
                "executed_qty": self.executed_qty,
                "order_avg_price": self.order_avg_price,
                "reduce_only": self.reduce_only,
                "take_profit_price": self.take_profit_price,
                "take_profit_trigger_price": self.take_profit_trigger_price,
                "take_profit_trigger_price_type": self.take_profit_trigger_price_type,
                "stop_loss_price": self.stop_loss_price,
                "stop_loss_trigger_price": self.stop_loss_trigger_price,
                "stop_loss_trigger_price_type": self.stop_loss_trigger_price_type,

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
        """# 获取品种名称"""
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
        """# ?"""
        return None

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
        return None

    def get_trailing_stop_trigger_price(self):
        return None

    def get_trailing_stop_trigger_price_type(self):
        return None

    def get_trailing_stop_callback_rate(self):
        """# 跟踪止损回调比例"""
        return None

    def get_order_symbol_name(self):
        """# 品种"""
        return self.order_symbol_name

    def get_order_time_in_force(self):
        """# 订单有效期类型"""
        return self.get_order_type()

    def get_order_type(self):
        """# 订单类型"""
        return self.order_type

    def get_order_avg_price(self):
        """# 平均价格"""
        return self.order_avg_price

    def get_origin_order_type(self):
        """# 原始订单类型"""
        return None

    def get_position_side(self):
        """# 持仓方向"""
        return self.position_side

    def get_close_position(self):
        """# 是否为触发平仓单; 仅在条件订单情况下会推送此字段"""
        return None

    def get_take_profit_price(self):
        """# get_take_profit_price"""
        return self.take_profit_price

    def get_take_profit_trigger_price(self):
        """# get take profit trigger_price"""
        return self.take_profit_trigger_price

    def get_take_profit_trigger_price_type(self):
        """# get take profit trigger_price_type"""
        return self.take_profit_trigger_price_type

    def get_stop_loss_price(self):
        """# 止损价"""
        return self.stop_loss_price

    def get_stop_loss_trigger_price(self):
        """# 止损触发价类型"""
        return self.stop_loss_trigger_price

    def get_stop_loss_trigger_price_type(self):
        """# 止损价触发类型"""
        return self.stop_loss_trigger_price_type
