"""订单类，用于确定订单的属性和方法
"""
import json


class OrderData(object):
    """保存订单相关信息"""

    def __init__(self, order_info, has_been_json_encoded=False):
        self.event = "OrderEvent"
        self.order_info = order_info
        self.has_been_json_encoded = has_been_json_encoded

    def get_event(self):
        """# 事件类型"""
        return self.event

    def init_data(self):
        raise NotImplementedError

    def get_exchange_name(self):
        """# 交易所名称"""
        raise NotImplementedError

    def get_asset_type(self):
        """# 资产类型"""
        raise NotImplementedError

    def get_server_time(self):
        """# 服务器时间戳"""
        raise NotImplementedError

    def get_local_update_time(self):
        """# 本地时间戳"""
        raise NotImplementedError

    def get_trade_id(self):
        """# 交易所返回唯一成交id"""
        raise NotImplementedError

    def get_client_order_id(self):
        """# 客户端自定订单ID"""
        raise NotImplementedError

    def get_cum_quote(self):
        """# ?"""
        raise NotImplementedError

    def get_executed_qty(self):
        """# 已执行的成交量"""
        raise NotImplementedError

    def get_order_id(self):
        """# 订单id"""
        raise NotImplementedError

    def get_order_size(self):
        """# 订单原始数量"""
        raise NotImplementedError

    def get_order_price(self):
        """# 订单价格"""
        raise NotImplementedError

    def get_reduce_only(self):
        """# 是否是只减仓单"""
        raise NotImplementedError

    def get_order_side(self):
        """# 订单方向"""
        raise NotImplementedError

    def get_order_status(self):
        """# 订单状态"""
        raise NotImplementedError

    def get_order_symbol_name(self):
        """# 品种"""
        raise NotImplementedError

    def get_order_time_in_force(self):
        """# 订单有效期类型"""
        raise NotImplementedError

    def get_order_type(self):
        """# 订单类型"""
        raise NotImplementedError

    def get_order_avg_price(self):
        """# 平均价格"""
        raise NotImplementedError

    def get_origin_order_type(self):
        """# 原始类型"""
        raise NotImplementedError

    def get_position_side(self):
        """# 持仓方向"""
        raise NotImplementedError

    def get_trailing_stop_price(self):
        """# 止损价"""
        raise NotImplementedError

    def get_trailing_stop_trigger_price(self):
        """# 激活价格"""
        raise NotImplementedError

    def get_trailing_stop_callback_rate(self):
        """# price?"""
        raise NotImplementedError

    def get_trailing_stop_trigger_price_type(self):
        """# 触发价类型"""
        raise NotImplementedError

    def get_stop_loss_price(self):
        # stop loss price
        raise NotImplementedError

    def get_stop_loss_trigger_price(self):
        # stop_loss_trigger price
        raise NotImplementedError

    def get_stop_loss_trigger_price_type(self):
        # stop loss trigger price type
        raise NotImplementedError

    def get_take_profit_price(self):
        # get stop profit price
        raise NotImplementedError

    def get_take_profit_trigger_price(self):
        # get stop profit trigger price
        raise NotImplementedError

    def get_take_profit_trigger_price_type(self):
        # get stop profit trigger price type
        raise NotImplementedError

    def get_close_position(self):
        """# 是否为触发平仓单; 仅在条件订单情况下会推送此字段"""
        raise NotImplementedError

    def __str__(self):
        raise NotImplementedError

    def __repr__(self):
        raise NotImplementedError
