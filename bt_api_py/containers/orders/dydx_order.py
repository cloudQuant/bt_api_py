from __future__ import annotations

import json
import time

from bt_api_py.containers.orders.order import OrderData, OrderStatus
from bt_api_py.functions.utils import from_dict_get_float, from_dict_get_string


class DydxOrderData(OrderData):
    """dYdX 订单类，用于确定订单的属性和方法"""

    def __init__(self, order_info, symbol_name, asset_type, has_been_json_encoded=False):
        super().__init__(order_info, has_been_json_encoded)
        self.exchange_name = "DYDX"
        self.symbol_name = symbol_name
        self.local_update_time = time.time()  # 本地时间戳
        self.asset_type = asset_type
        self.order_data = self.order_info if has_been_json_encoded else None
        self.server_time = None
        self.order_id = None
        self.client_order_id = None
        self.subaccount_number = None
        self.market = None
        self.side = None
        self.order_type = None
        self.size = None
        self.price = None
        self.reduce_only = None
        self.status = None
        self.created_at = None
        self.updated_at = None
        self.filled_size = None
        self.remaining_size = None
        self.avg_fill_price = None
        self.fees = None
        self.trigger_price = None
        self.all_data = None
        self.has_been_init_data = False

    def init_data(self):
        if not self.has_been_json_encoded:
            self.order_data = json.loads(self.order_info)
            self.has_been_json_encoded = True
        if self.has_been_init_data:
            return self

        # 处理订单列表响应
        if isinstance(self.order_data, list) and self.order_data:
            self.order_data = self.order_data[0]

        # 转换订单状态
        if "status" in self.order_data:
            self.status = self.order_data["status"]
            # 将 dYdX 状态转换为标准状态
            if self.status == "OPEN":
                self.order_status = OrderStatus.ACCEPTED
            elif self.status == "FILLED":
                self.order_status = OrderStatus.COMPLETED
            elif self.status == "CANCELED":
                self.order_status = OrderStatus.CANCELED
            elif self.status == "EXPIRED":
                self.order_status = OrderStatus.EXPIRED
            else:
                self.order_status = OrderStatus.SUBMITTED

        self.server_time = from_dict_get_string(self.order_data, "createdAt")
        self.order_id = from_dict_get_string(self.order_data, "id")
        self.client_order_id = from_dict_get_string(self.order_data, "clientId")
        self.subaccount_number = from_dict_get_string(self.order_data, "subaccountNumber")
        self.market = from_dict_get_string(self.order_data, "market")
        self.side = from_dict_get_string(self.order_data, "side")
        self.order_type = from_dict_get_string(self.order_data, "type")
        self.size = from_dict_get_float(self.order_data, "size")
        self.price = from_dict_get_float(self.order_data, "price")
        self.reduce_only = from_dict_get_string(self.order_data, "reduceOnly") == "true"
        self.created_at = from_dict_get_string(self.order_data, "createdAt")
        self.updated_at = from_dict_get_string(self.order_data, "updatedAt")
        self.filled_size = from_dict_get_float(self.order_data, "filledSize")
        self.remaining_size = from_dict_get_float(self.order_data, "remainingSize")
        self.avg_fill_price = from_dict_get_float(self.order_data, "avgFillPrice")
        self.fees = from_dict_get_float(self.order_data, "fees")
        self.trigger_price = from_dict_get_float(self.order_data, "triggerPrice")

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
                "subaccount_number": self.subaccount_number,
                "market": self.market,
                "side": self.side,
                "order_type": self.order_type,
                "size": self.size,
                "price": self.price,
                "reduce_only": self.reduce_only,
                "status": self.status,
                "order_status": self.order_status.value if self.order_status else None,
                "created_at": self.created_at,
                "updated_at": self.updated_at,
                "filled_size": self.filled_size,
                "remaining_size": self.remaining_size,
                "avg_fill_price": self.avg_fill_price,
                "fees": self.fees,
                "trigger_price": self.trigger_price,
            }
        return self.all_data

    def __str__(self):
        self.init_data()
        return json.dumps(self.get_all_data())

    def __repr__(self):
        return self.__str__()

    def get_exchange_name(self):
        """交易所名称"""
        return self.exchange_name

    def get_symbol_name(self):
        """获取品种名称"""
        return self.symbol_name

    def get_asset_type(self):
        return self.asset_type

    def get_server_time(self):
        """服务器时间戳"""
        return self.server_time

    def get_local_update_time(self):
        """本地时间戳"""
        return self.local_update_time

    def get_trade_id(self):
        """交易所返回唯一成交id (dYdX 使用 order_id)"""
        return self.order_id

    def get_client_order_id(self):
        """客户端自定订单ID"""
        return self.client_order_id

    def get_cum_quote(self):
        """累计报价金额"""
        return  # dYdX 可能没有这个字段

    def get_executed_qty(self):
        """已执行的成交量"""
        return self.filled_size

    def get_order_id(self):
        """订单id"""
        return self.order_id

    def get_order_size(self):
        """订单原始数量"""
        return self.size

    def get_order_price(self):
        """订单价格"""
        return self.price

    def get_reduce_only(self):
        """是否是只减仓单"""
        return self.reduce_only

    def get_order_side(self):
        """订单方向"""
        return self.side

    def get_order_status(self):
        """订单状态"""
        return self.order_status

    def get_trailing_stop_price(self):
        """跟踪止损价"""
        return  # dYdX 可能不支持跟踪止损

    def get_trailing_stop_trigger_price(self):
        """跟踪止损触发价"""
        return

    def get_trailing_stop_trigger_price_type(self):
        """跟踪止损触发价类型"""
        return

    def get_trailing_stop_callback_rate(self):
        """跟踪止损回调比例"""
        return

    def get_order_symbol_name(self):
        """品种"""
        return self.market

    def get_order_time_in_force(self):
        """订单有效期类型"""
        # dYdX 使用 GTT (Good Til Time) 或 IOC (Immediate Or Cancel)
        return self.order_type

    def get_order_type(self):
        """订单类型"""
        return self.order_type

    def get_order_avg_price(self):
        """平均价格"""
        return self.avg_fill_price

    def get_origin_order_type(self):
        """原始订单类型"""
        return self.order_type

    def get_position_side(self):
        """持仓方向 (dYdX 是单边持仓)"""
        return "BOTH" if not self.reduce_only else "REDUCE_ONLY"

    def get_close_position(self):
        """是否为触发平仓单; 仅在条件订单情况下会推送此字段"""
        return

    def get_take_profit_price(self):
        """止盈价"""
        return  # dYdX 的止盈通过 stopLossTriggerPrice 实现

    def get_take_profit_trigger_price(self):
        """止盈触发价"""
        return

    def get_take_profit_trigger_price_type(self):
        """止盈触发价类型"""
        return

    def get_stop_loss_price(self):
        """止损价"""
        return self.trigger_price

    def get_stop_loss_trigger_price(self):
        """止损触发价"""
        return self.trigger_price

    def get_stop_loss_trigger_price_type(self):
        """止损价触发类型"""
        return

    def get_subaccount_number(self):
        """子账户编号"""
        return self.subaccount_number
