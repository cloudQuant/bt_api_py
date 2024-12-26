import time
import json
from bt_api_py.containers.balances.balance import BalanceData
from bt_api_py.functions.utils import from_dict_get_float


class OkxBalanceData(BalanceData):
    def __init__(self, balance_info, symbol_name, asset_type, has_been_json_encoded=False):
        super(OkxBalanceData, self).__init__(balance_info, has_been_json_encoded)
        self.exchange_name = "OKX"
        self.symbol_name = symbol_name
        self.local_update_time = time.time()  # 本地时间戳
        self.asset_type = asset_type
        self.balance_data = balance_info if has_been_json_encoded else None
        self.interest = None
        self.unrealized_profit = None
        self.open_order_initial_margin = None
        self.available_margin = None
        self.used_margin = None
        self.margin = None
        self.server_time = None
        self.all_data = None
        self.has_been_init_data = False

    def init_data(self):
        if not self.has_been_json_encoded:
            self.balance_data = json.loads(self.balance_info)
            self.has_been_json_encoded = True
        if self.has_been_init_data:
            return self
        self.server_time = from_dict_get_float(self.balance_data, "uTime")
        self.margin = from_dict_get_float(self.balance_data, "eq")
        self.used_margin = from_dict_get_float(self.balance_data, "frozenBal")
        self.available_margin = from_dict_get_float(self.balance_data, "availBal")
        self.open_order_initial_margin = from_dict_get_float(self.balance_data, "frozenBal")
        self.unrealized_profit = from_dict_get_float(self.balance_data, "upl")
        self.interest = from_dict_get_float(self.balance_data, "interest")
        self.has_been_init_data = True
        return self

    def get_all_data(self):
        if self.all_data is None:
            self.all_data = {
                "exchange_name": self.exchange_name,
                "symbol_name": self.symbol_name,
                "local_update_time": self.local_update_time,
                "asset_type": self.asset_type,
                "interest": self.interest,
                "unrealized_profit": self.unrealized_profit,
                "open_order_initial_margin": self.open_order_initial_margin,
                "available_margin": self.available_margin,
                "used_margin": self.used_margin,
                "margin": self.margin,
                "server_time": self.server_time
            }
        return self.all_data

    def __str__(self):
        self.init_data()
        return json.dumps(self.get_all_data())

    def __repr__(self):
        return self.__str__()

    def get_exchange_name(self):
        """# 交易所名称"""
        return self.exchange_name

    def get_symbol_name(self):
        """# 货币名称"""
        return self.symbol_name

    def get_asset_type(self):
        """# 资产类型"""
        return self.asset_type

    def get_server_time(self):
        """# 服务器时间戳"""
        return self.server_time

    def get_local_update_time(self):
        """# 本地时间戳"""
        return self.local_update_time

    def get_account_id(self):
        """# 账户id"""
        return None

    def get_account_type(self):
        """# 账户类型"""
        return None

    def get_fee_tier(self):
        """# 资金费率等级"""
        return None

    def get_max_withdraw_amount(self):
        """# 最大可取资金"""
        return None

    def get_margin(self):
        """# 总的保证金"""
        return self.margin

    def get_used_margin(self):
        """# 总的使用的保证金"""
        return self.used_margin

    def get_maintain_margin(self):
        """# 总的维持资金"""
        return None

    def get_available_margin(self):
        """# 总的可用保证金"""
        return self.available_margin

    def get_open_order_initial_margin(self):
        """# 总的开仓订单初始保证金"""
        return self.open_order_initial_margin

    def get_position_initial_margin(self):
        """# 总的持仓初始化保证金"""
        return None

    def get_unrealized_profit(self):
        """# 总的未实现利润 """
        return self.unrealized_profit

    def get_interest(self):
        """# 获取应计利息"""
        return self.interest
