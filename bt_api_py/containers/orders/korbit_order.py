import json
import time

from bt_api_py.containers.orders.order import OrderData


class KorbitRequestOrderData(OrderData):
    """保存 Korbit 订单信息"""

    def __init__(self, order_info, symbol_name, asset_type, has_been_json_encoded=False):
        super().__init__(order_info, symbol_name, asset_type, has_been_json_encoded)
        self.exchange_name = "KORBIT"
        self.local_update_time = time.time()
