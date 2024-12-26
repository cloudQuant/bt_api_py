"""品种信息类，用于控制品种的属性和信息, 很长时间才会更新一次，配置型文件,可以采用这种方式进行初始化
"""


class SymbolData(object):

    def __init__(self, symbol_info, has_been_json_encoded):
        self.event = "SymbolEvent"
        self.symbol_info = symbol_info
        self.has_been_json_encoded = has_been_json_encoded

    def get_event(self):
        return self.event

    def get_all_data(self):
        raise NotImplementedError

    def init_data(self):
        raise NotImplementedError

    def get_exchange_name(self):
        """获取交易所名称"""
        raise NotImplementedError

    def get_server_time(self):
        raise NotImplementedError

    def get_local_update_time(self):
        raise NotImplementedError

    def get_symbol_name(self):
        raise NotImplementedError

    def get_asset_type(self):
        raise NotImplementedError

    def get_maintain_margin_percent(self):
        raise NotImplementedError

    def get_required_margin_percent(self):
        raise NotImplementedError

    def get_base_asset(self):
        raise NotImplementedError

    def get_quote_asset(self):
        raise NotImplementedError

    def get_contract_multiplier(self):
        raise NotImplementedError

    def get_price_unit(self):
        raise NotImplementedError

    def get_price_digital(self):
        raise NotImplementedError

    def get_max_price(self):
        raise NotImplementedError

    def get_min_price(self):
        raise NotImplementedError

    def get_qty_unit(self):
        raise NotImplementedError

    def get_qty_digital(self):
        raise NotImplementedError

    def get_min_qty(self):
        raise NotImplementedError

    def get_max_qty(self):
        raise NotImplementedError

    def get_base_asset_digital(self):
        raise NotImplementedError

    def get_quote_asset_digital(self):
        raise NotImplementedError

    def get_order_types(self):
        raise NotImplementedError

    def get_time_in_force(self):
        raise NotImplementedError

    def get_fee_digital(self):
        raise NotImplementedError

    def get_fee_currency(self):
        raise NotImplementedError

    def __str__(self):
        raise NotImplementedError

    def __repr__(self):
        raise NotImplementedError
