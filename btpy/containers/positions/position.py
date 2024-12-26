"""持仓类，用于保存各个品种的持仓信息，分析当前策略持有某个品种多少的仓位，平均成本是多少，和总体账户有一定的区别"""


class PositionData(object):
    """保存持仓信息"""

    def __init__(self, position_info, has_been_json_encoded=False):
        self.event = "PositionEvent"
        self.position_info = position_info
        self.has_been_json_encoded = has_been_json_encoded

    def get_event(self):
        """# 事件类型"""
        return self.event

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

    def get_position_id(self):
        """# 持仓id"""
        raise NotImplementedError

    def get_is_isolated(self):
        """# 是否是逐仓模式"""
        raise NotImplementedError

    def get_margin_type(self):
        """# 保证金类型"""
        raise NotImplementedError

    def get_is_auto_add_margin(self):
        """# 是否可以自动增加保证金"""
        raise NotImplementedError

    def get_leverage(self):
        """# 杠杆倍率"""
        raise NotImplementedError

    def get_max_notional_value(self):
        """# 当前杠杆下用户可用的最大名义价值"""
        raise NotImplementedError

    def get_position_symbol_name(self):
        """# 仓位的品种名称"""
        raise NotImplementedError

    def get_position_volume(self):
        """# 持仓数量"""
        raise NotImplementedError

    def get_position_side(self):
        """# 持仓方向"""
        raise NotImplementedError

    def get_trade_num(self):
        """# trade的个数"""
        raise NotImplementedError

    def get_avg_price(self):
        """# 持仓成本价"""
        raise NotImplementedError

    def get_mark_price(self):
        """# 标记价格"""
        raise NotImplementedError

    def get_liquidation_price(self):
        """# 清算价格"""
        raise NotImplementedError

    def get_initial_margin(self):
        """# 当前所需起始保证金(基于最新标记价格)"""
        raise NotImplementedError

    def get_maintain_margin(self):
        """# 维持保证金"""
        raise NotImplementedError

    def open_order_initial_margin(self):
        """# 当前挂单所需起始保证金(基于最新标记价格)"""
        raise NotImplementedError

    def get_position_initial_margin(self):
        """# 持仓所需起始保证金(基于最新标记价格)"""
        raise NotImplementedError

    def get_position_fee(self):
        """# 这个position交易所耗费的手续费"""
        raise NotImplementedError

    def get_position_realized_pnl(self):
        """# 已经实现的利润"""
        raise NotImplementedError

    def get_position_unrealized_pnl(self):
        """# 持仓未实现盈亏"""
        raise NotImplementedError

    def get_position_funding_value(self):
        """# 总的资金费率"""
        raise NotImplementedError

    def __str__(self):
        raise NotImplementedError

    def __repr__(self):
        raise NotImplementedError
