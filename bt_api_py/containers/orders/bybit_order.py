import time

from bt_api_py.containers.orders.order import OrderData


class BybitOrderData(OrderData):
    """保存 Bybit 订单信息"""

    def __init__(self, order_info, symbol_name, asset_type, has_been_json_encoded=False):
        super().__init__(order_info, has_been_json_encoded)
        self.exchange_name = "BYBIT"
        self.local_update_time = time.time()
        self.symbol_name = symbol_name
        self.asset_type = asset_type
        self.order_data = order_info if has_been_json_encoded else None
        self.order_id = None
        self.order_link_id = None
        self.status = None
        self.side = None
        self.order_type = None
        self.price = None
        self.qty = None
        self.filled_qty = None
        self.remaining_qty = None
        self.cum_exec_value = None
        self.avg_price = None
        self.create_time = None
        self.update_time = None
        self.time_in_force = None
        self.leaves_qty = None
        self.canceled_time = None
        self.all_data = None
        self.has_been_init_data = False

    def init_data(self):
        """初始化订单数据"""
        if self.has_been_init_data or self.order_data is None:
            return self

        try:
            result = self.order_data.get("result", {})
            list_data = result.get("list", [])

            if not list_data:
                return

            order = list_data[0]  # Bybit 返回列表，取第一个

            # 订单基本信息
            self.order_id = order.get("orderId")
            self.order_link_id = order.get("orderLinkId")
            self.status = order.get("orderStatus")
            self.side = order.get("side")
            self.order_type = order.get("orderType")

            # 价格和数量
            self.price = order.get("price")
            self.qty = order.get("qty")
            self.filled_qty = order.get("cumExecQty")
            self.remaining_qty = order.get("leavesQty")

            # 成交信息
            self.cum_exec_value = order.get("cumExecValue")
            self.avg_price = order.get("avgPrice")

            # 时间信息
            self.create_time = order.get("createdTime")
            self.update_time = order.get("updatedTime")
            self.canceled_time = order.get("canceledTime")

            # 其他信息
            self.time_in_force = order.get("timeInForce")
            self.leaves_qty = order.get("leavesQty")

            self.has_been_init_data = True

        except Exception as e:
            print(f"Error parsing Bybit order data: {e}")
            self.has_been_init_data = False
        return self

    def get_all_data(self):
        """获取所有订单数据"""
        if self.all_data is None:
            self.init_data()
            self.all_data = {
                "exchange_name": self.exchange_name,
                "symbol_name": self.symbol_name,
                "asset_type": self.asset_type,
                "local_update_time": self.local_update_time,
                "order_id": self.order_id,
                "order_link_id": self.order_link_id,
                "status": self.status,
                "side": self.side,
                "order_type": self.order_type,
                "price": self.price,
                "qty": self.qty,
                "filled_qty": self.filled_qty,
                "remaining_qty": self.remaining_qty,
                "cum_exec_value": self.cum_exec_value,
                "avg_price": self.avg_price,
                "create_time": self.create_time,
                "update_time": self.update_time,
                "canceled_time": self.canceled_time,
                "time_in_force": self.time_in_force,
                "leaves_qty": self.leaves_qty,
            }
        return self.all_data

    def is_active(self):
        """检查订单是否活跃"""
        return self.status in ["New", "PartiallyFilled"]

    def is_filled(self):
        """检查订单是否已成交"""
        return self.status == "Filled"

    def is_canceled(self):
        """检查订单是否已取消"""
        return self.status in ["Canceled", "Rejected", "Expired"]

    def get_fill_ratio(self):
        """获取成交比例"""
        if not self.qty or float(self.qty) == 0:
            return 0.0
        return float(self.filled_qty) / float(self.qty) if self.filled_qty else 0.0

    def __str__(self):
        """返回订单的字符串表示"""
        self.init_data()
        return (f"BybitOrder(id={self.order_id}, "
                f"symbol={self.symbol_name}, "
                f"side={self.side}, "
                f"type={self.order_type}, "
                f"status={self.status}, "
                f"qty={self.qty}, "
                f"filled={self.filled_qty})")


class BybitSpotOrderData(BybitOrderData):
    """Bybit 现货订单数据"""

    def __init__(self, order_info, symbol_name, has_been_json_encoded=False):
        super().__init__(order_info, symbol_name, "spot", has_been_json_encoded)


class BybitSwapOrderData(BybitOrderData):
    """Bybit 期货/swap 订单数据"""

    def __init__(self, order_info, symbol_name, has_been_json_encoded=False):
        super().__init__(order_info, symbol_name, "swap", has_been_json_encoded)
