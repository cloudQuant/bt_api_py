# """
# K线类
# 用于确定k线数据的属性和方法
# Bar的数据推送和请求频率不是特别高，传入的数据直接使用json格式
# """

class BarData(object):

    def __init__(self, bar_info, has_been_json_encoded=False):
        self.event = "BarEvent"
        self.bar_info = bar_info
        self.has_been_json_encoded = has_been_json_encoded

    def init_data(self):
        raise NotImplementedError

    def get_event(self):
        return self.event

    def get_exchange_name(self):
        raise NotImplementedError

    def get_symbol_name(self):
        raise NotImplementedError

    def get_asset_type(self):
        raise NotImplementedError

    def get_server_time(self):
        raise NotImplementedError

    def get_local_update_time(self):
        raise NotImplementedError

    def get_open_time(self):
        raise NotImplementedError

    def get_open_price(self):
        raise NotImplementedError

    def get_high_price(self):
        raise NotImplementedError

    def get_low_price(self):
        raise NotImplementedError

    def get_close_price(self):
        raise NotImplementedError

    def get_volume(self):
        raise NotImplementedError

    def get_amount(self):
        raise NotImplementedError

    def get_close_time(self):
        raise NotImplementedError

    def get_quote_asset_volume(self):
        raise NotImplementedError

    def get_base_asset_volume(self):
        raise NotImplementedError

    def get_num_trades(self):
        raise NotImplementedError

    def get_taker_buy_base_asset_volume(self):
        raise NotImplementedError

    def get_taker_buy_quote_asset_volume(self):
        raise NotImplementedError

    def get_bar_status(self):
        raise NotImplementedError

    def get_all_data(self):
        raise NotImplementedError

    def __str__(self):
        raise NotImplementedError

    def __repr__(self):
        raise NotImplementedError
