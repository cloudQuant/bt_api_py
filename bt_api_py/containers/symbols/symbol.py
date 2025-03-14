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
        """获取数据的服务器时间"""
        raise NotImplementedError

    def get_local_update_time(self):
        """获取数据的本地时间"""
        raise NotImplementedError

    def get_symbol_name(self):
        """获取比对名称"""
        raise NotImplementedError

    def get_asset_type(self):
        """获取资产类型"""
        raise NotImplementedError

    def get_maintain_margin_percent(self):
        """获取维持保证金率"""
        raise NotImplementedError

    def get_required_margin_percent(self):
        """获取需要的保证金率"""
        raise NotImplementedError

    def get_base_asset(self):
        """获取基础资产"""
        raise NotImplementedError

    def get_quote_asset(self):
        """获取报价资产"""
        raise NotImplementedError

    def get_contract_multiplier(self):
        """获取合约乘数"""
        raise NotImplementedError

    def get_price_unit(self):
        """获取价格单位"""
        raise NotImplementedError

    def get_price_digital(self):
        """获取价格位数"""
        raise NotImplementedError

    def get_max_price(self):
        """获取最大价格"""
        raise NotImplementedError

    def get_min_price(self):
        """获取最小价格"""
        raise NotImplementedError

    def get_qty_unit(self):
        """获取下单量单位"""
        raise NotImplementedError

    def get_qty_digital(self):
        """获取下单量位数"""
        raise NotImplementedError

    def get_min_qty(self):
        """获取最小下单量"""
        raise NotImplementedError

    def get_max_qty(self):
        """获取最大下单量"""
        raise NotImplementedError

    def get_base_asset_digital(self):
        """获取基础资产的位数"""
        raise NotImplementedError

    def get_quote_asset_digital(self):
        """获取报价资产的位数"""
        raise NotImplementedError

    def get_order_types(self):
        """获取symbol支持的订单类型"""
        raise NotImplementedError

    def get_time_in_force(self):
        """获取订单支持的时间类型"""
        raise NotImplementedError

    def get_fee_digital(self):
        """获取订单交易费用的价格位数"""
        raise NotImplementedError

    def get_fee_currency(self):
        """获取交易费用的计价货币"""
        raise NotImplementedError

    def __str__(self):
        raise NotImplementedError

    def __repr__(self):
        raise NotImplementedError
