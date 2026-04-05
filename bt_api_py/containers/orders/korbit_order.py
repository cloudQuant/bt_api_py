from __future__ import annotations

import time

from bt_api_py.containers.orders.order import OrderData


class KorbitRequestOrderData(OrderData):
    """保存 Korbit 订单信息"""

    def __init__(self, order_info, symbol_name, asset_type, has_been_json_encoded=False):
        super().__init__(order_info, has_been_json_encoded)
        self.symbol_name = symbol_name
        self.asset_type = asset_type
        self.exchange_name = "KORBIT"
        self.local_update_time = time.time()
