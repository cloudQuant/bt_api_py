"""订单簿类，用于确定订单簿的属性和方法
"""
import json


class OrderBookData(object):
    """保存订单簿相关信息"""

    def __init__(self, order_book_info, has_been_json_encoded=False):
        self.event = "OrderBookEvent"
        self.order_book_info = order_book_info
        self.has_been_json_encoded = has_been_json_encoded

    def init_data(self):
        raise NotImplementedError

    def get_event(self):
        return self.event

    def get_exchange_name(self):
        raise NotImplementedError

    def get_local_update_time(self):
        raise NotImplementedError

    def get_symbol_name(self):
        raise NotImplementedError

    def get_asset_type(self):
        raise NotImplementedError

    def get_server_time(self):
        raise NotImplementedError

    def get_bid_price_list(self):
        raise NotImplementedError

    def get_ask_price_list(self):
        raise NotImplementedError

    def get_bid_volume_list(self):
        raise NotImplementedError

    def get_ask_volume_list(self):
        raise NotImplementedError

    def get_bid_trade_nums(self):
        raise NotImplementedError

    def get_ask_trade_nums(self):
        return NotImplementedError

    def __str__(self):
        raise NotImplementedError

    def __repr__(self):
        raise NotImplementedError
