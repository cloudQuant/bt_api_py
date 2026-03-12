import time

from bt_api_py.containers.orders.order import OrderData


class LocalBitcoinsRequestOrderData(OrderData):
    """保存 LocalBitcoins 订单信息"""

    def __init__(self, order_info, symbol_name, asset_type, has_been_json_encoded=False):
        super().__init__(order_info, has_been_json_encoded)
        self.symbol_name = symbol_name
        self.asset_type = asset_type
        self.exchange_name = "LOCALBITCOINS"
        self.local_update_time = time.time()
