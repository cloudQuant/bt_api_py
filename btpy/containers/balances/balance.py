
class BalanceData(object):
    """
    用于保存账户的余额
    """
    def __init__(self, balance_info, has_been_json_encoded=False):
        self.event = "BalanceEvent"
        self.balance_info = balance_info
        self.has_been_json_encoded = has_been_json_encoded

    def get_event(self):
        return self.event

    def init_data(self):
        raise NotImplementedError

    def get_all_data(self):
        raise NotImplementedError

    def get_exchange_name(self):
        """# 交易所名称"""
        raise NotImplementedError

    def get_asset_type(self):
        """# 资产类型"""
        raise NotImplementedError

    def get_server_time(self):
        """# 服务器时间戳"""
        raise NotImplementedError

    def get_local_update_time(self):
        """# 本地时间戳"""
        raise NotImplementedError

    def get_account_id(self):
        """# 账户id"""
        raise NotImplementedError

    def get_account_type(self):
        """# 账户类型"""
        raise NotImplementedError

    def get_fee_tier(self):
        """# 资金费率等级"""
        raise NotImplementedError

    def get_max_withdraw_amount(self):
        """# 最大可取资金"""
        raise NotImplementedError

    def get_margin(self):
        """# 总的保证金"""
        raise NotImplementedError

    def get_used_margin(self):
        """# 总的使用的保证金"""
        raise NotImplementedError

    def get_maintain_margin(self):
        """# 总的维持资金"""
        raise NotImplementedError

    def get_available_margin(self):
        """# 总的可用保证金"""
        raise NotImplementedError

    def get_open_order_initial_margin(self):
        """# 总的开仓订单初始保证金"""
        raise NotImplementedError

    def get_open_order_maintenance_margin(self):
        """# 总的持仓初始化保证金"""
        raise NotImplementedError

    def get_unrealized_profit(self):
        """# 总的未实现利润 """
        raise NotImplementedError

    def get_interest(self):
        raise NotImplementedError

    def __str__(self):
        raise NotImplementedError

    def __repr__(self):
        raise NotImplementedError
