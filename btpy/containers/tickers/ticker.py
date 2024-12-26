"""tick类，用于确定ticker的属性和方法
"""


class TickerData:
    """保存ticker信息"""

    def __init__(self, ticker_info, has_been_json_encoded=False):
        self.event = "TickerEvent"
        self.ticker_info = ticker_info
        self.has_been_json_encoded = has_been_json_encoded

    def init_data(self):
        raise NotImplementedError

    def get_all_data(self):
        raise NotImplementedError

    def get_event(self):
        return self.event

    def get_exchange_name(self):
        raise NotImplementedError

    def get_local_update_time(self):
        raise NotImplementedError

    def get_symbol_name(self):
        raise NotImplementedError

    def get_ticker_symbol_name(self):
        raise NotImplementedError

    def get_asset_type(self):
        raise NotImplementedError

    def get_server_time(self):
        raise NotImplementedError

    def get_bid_price(self):
        raise NotImplementedError

    def get_ask_price(self):
        raise NotImplementedError

    def get_bid_volume(self):
        raise NotImplementedError

    def get_ask_volume(self):
        raise NotImplementedError

    def get_last_price(self):
        raise NotImplementedError

    def get_last_volume(self):
        raise NotImplementedError

    def __str__(self):
        raise NotImplementedError

    def __repr__(self):
        raise NotImplementedError
