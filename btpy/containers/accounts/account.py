
"""
账户类
用于确定账户数据的属性和方法
"""
import json


class AccountData(object):
    def __init__(self, account_info, has_been_json_encoded=False):
        self.event = "AccountEvent"
        self.account_info = account_info
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

    def get_can_deposit(self):
        """# 是否可以存钱"""
        raise NotImplementedError

    def get_can_trade(self):
        """# 是否可以交易"""
        raise NotImplementedError

    def get_can_withdraw(self):
        """# 是否可以取钱"""
        raise NotImplementedError

    def get_fee_tier(self):
        """# 资金费率等级"""
        raise NotImplementedError

    def get_max_withdraw_amount(self):
        """# 最大可取资金"""
        raise NotImplementedError

    def get_total_margin(self):
        """# 总的保证金"""
        raise NotImplementedError

    def get_total_used_margin(self):
        """# 总的使用的保证金"""
        raise NotImplementedError

    def get_total_maintain_margin(self):
        """# 总的维持资金"""
        raise NotImplementedError

    def get_total_available_margin(self):
        """# 总的可用保证金"""
        raise NotImplementedError

    def get_total_open_order_initial_margin(self):
        """# 总的开仓订单初始保证金"""
        raise NotImplementedError

    def get_total_position_initial_margin(self):
        """# 总的持仓初始化保证金"""
        raise NotImplementedError

    def get_total_unrealized_profit(self):
        """# 总的未实现利润 """
        raise NotImplementedError

    def get_total_wallet_balance(self):
        """# 总的钱包余额"""
        raise NotImplementedError

    def get_balances(self):
        """# 现货资产,账户余额，可用，冻结"""
        raise NotImplementedError

    def get_positions(self):
        """# 获取持仓数据"""
        raise NotImplementedError

    def get_spot_maker_commission_rate(self):
        # maker佣金费率
        raise NotImplementedError

    def get_spot_taker_commission_rate(self):
        # taker佣金费率
        raise NotImplementedError

    def get_future_maker_commission_rate(self):
        # maker佣金费率
        raise NotImplementedError

    def get_future_taker_commission_rate(self):
        # taker佣金费率
        raise NotImplementedError

    def get_option_maker_commission_rate(self):
        # maker佣金费率
        raise NotImplementedError

    def get_option_taker_commission_rate(self):
        # taker佣金费率
        raise NotImplementedError

    def __str__(self):
        raise NotImplementedError

    def __repr__(self):
        raise NotImplementedError
