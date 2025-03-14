import time
import json
from bt_api_py.containers.accounts.account import AccountData
from bt_api_py.containers.positions.binance_position import BinanceRequestPositionData, BinanceWssPositionData
from bt_api_py.containers.balances.binance_balance import (BinanceSwapWssBalanceData,
                                                           BinanceSwapRequestBalanceData,
                                                           BinanceSpotRequestBalanceData,
                                                           BinanceSpotWssBalanceData)
from bt_api_py.functions.utils import from_dict_get_string, from_dict_get_float, from_dict_get_bool


class BinanceSpotRequestAccountData(AccountData):
    def __init__(self, account_info, symbol_name, asset_type, has_been_json_encoded=False):
        super(BinanceSpotRequestAccountData, self).__init__(account_info, has_been_json_encoded)
        self.exchange_name = "BINANCE"
        self.symbol_name = symbol_name
        self.local_update_time = time.time()  # 本地时间戳
        self.asset_type = asset_type
        self.account_data = self.account_info if has_been_json_encoded else None
        self.balances = None
        self.can_withdraw = None
        self.can_trade = None
        self.can_deposit = None
        self.account_type = None
        self.server_time = None
        self.is_multi_assets_margin = None
        self.all_data = None
        self.has_been_init_data = False

    def init_data(self):
        if not self.has_been_json_encoded:
            self.account_data = json.loads(self.account_info)
            self.has_been_json_encoded = True
        if self.has_been_init_data:
            return self
        self.server_time = from_dict_get_float(self.account_data, "updateTime")
        self.account_type = from_dict_get_string(self.account_data, "accountType")
        self.can_deposit = from_dict_get_bool(self.account_data, "canDeposit")
        self.can_trade = from_dict_get_bool(self.account_data, "canTrade")
        self.can_withdraw = from_dict_get_bool(self.account_data, "canWithdraw")
        self.balances = [BinanceSpotRequestBalanceData(i, i['asset'], self.asset_type, True)
                         for i in self.account_data["balances"]]
        self.has_been_init_data = True
        return self

    def get_all_data(self):
        if self.all_data is None:
            self.all_data = {
                "exchange_name": self.exchange_name,
                "symbol_name": self.symbol_name,
                "local_update_time": self.local_update_time,
                "asset_type": self.asset_type,
                "balances": self.balances,
                "can_withdraw": self.can_withdraw,
                "can_trade": self.can_trade,
                "can_deposit": self.can_deposit,
                "account_type": self.account_type,
                "server_time": self.server_time}
        return self.all_data

    def __str__(self):
        self.init_data()
        return str(self.get_all_data())

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
        return self.account_type

    def get_is_multi_assets_margin(self):
        """是否是多账户资产类型"""
        return self.is_multi_assets_margin

    def get_can_deposit(self):
        """# 是否可以存钱"""
        return self.can_deposit

    def get_can_trade(self):
        """# 是否可以交易"""
        return self.can_trade

    def get_can_withdraw(self):
        """# 是否可以取钱"""
        return self.can_withdraw

    def get_fee_tier(self):
        """# 资金费率等级"""
        return None

    def get_max_withdraw_amount(self):
        """# 最大可取资金"""
        return None

    def get_total_margin(self):
        """# 总的初始化保证金"""
        return None

    def get_margin(self):
        return 0

    def get_total_used_margin(self):
        """# 总的使用的保证金"""
        return None

    def get_total_maintain_margin(self):
        """# 总的维持资金"""
        return None

    def get_available_margin(self):
        return 0

    def get_total_available_margin(self):
        """# 总的可用保证金"""
        return None

    def get_total_open_order_initial_margin(self):
        """# 总的开仓订单初始保证金"""
        return None

    def get_total_position_initial_margin(self):
        """# 总的持仓初始化保证金"""
        return None

    def get_total_unrealized_profit(self):
        """# 总的未实现利润 """
        return None

    def get_unrealized_profit(self):
        return 0

    def get_total_wallet_balance(self):
        """# 总的钱包余额"""
        return None

    def get_balances(self):
        """# 现货资产,账户余额，可用，冻结"""
        return self.balances

    def get_positions(self):
        """# 获取持仓数据"""
        return None

    def get_spot_maker_commission_rate(self):
        # maker佣金费率
        return None

    def get_spot_taker_commission_rate(self):
        # taker佣金费率
        return None

    def get_future_maker_commission_rate(self):
        # maker佣金费率
        return None

    def get_future_taker_commission_rate(self):
        # taker佣金费率
        return None

    def get_option_maker_commission_rate(self):
        # maker佣金费率
        return None

    def get_option_taker_commission_rate(self):
        # taker佣金费率
        return None


class BinanceSwapRequestAccountData(AccountData):
    def __init__(self, account_info, symbol_name, asset_type, has_been_json_encoded=False):
        super(BinanceSwapRequestAccountData, self).__init__(account_info, has_been_json_encoded)
        self.exchange_name = "BINANCE"
        self.symbol_name = symbol_name
        self.local_update_time = time.time()  # 本地时间戳
        self.asset_type = asset_type
        self.account_data = self.account_info if has_been_json_encoded else None
        self.balances = None
        self.total_wallet_balance = None
        self.total_unrealized_profit = None
        self.total_open_order_initial_margin = None
        self.total_maintain_margin = None
        self.total_margin = None
        self.server_time = None
        self.positions = None
        self.total_position_initial_margin = None
        self.total_available_margin = None
        self.max_withdraw_amount = None
        self.fee_tier = None
        self.can_withdraw = None
        self.can_trade = None
        self.can_deposit = None
        self.is_multi_assets_margin = None
        self.all_data = None
        self.has_been_init_data = False

    def init_data(self):
        if not self.has_been_json_encoded:
            self.account_data = json.loads(self.account_info)
            self.has_been_json_encoded = True
        if self.has_been_init_data:
            return self
        self.server_time = from_dict_get_float(self.account_data, "updateTime")
        self.is_multi_assets_margin = from_dict_get_bool(self.account_data, "multiAssetsMargin")
        self.can_deposit = from_dict_get_bool(self.account_data, "canDeposit")
        self.can_trade = from_dict_get_bool(self.account_data, "canTrade")
        self.can_withdraw = from_dict_get_bool(self.account_data, "canWithdraw")
        self.fee_tier = from_dict_get_float(self.account_data, "feeTier")
        self.max_withdraw_amount = from_dict_get_float(self.account_data, "maxWithdrawAmount")
        self.total_margin = from_dict_get_float(self.account_data, "totalMarginBalance")
        self.total_maintain_margin = from_dict_get_float(self.account_data, "totalMaintMargin")
        self.total_available_margin = from_dict_get_float(self.account_data, "availableBalance")
        self.total_open_order_initial_margin = from_dict_get_float(self.account_data, "totalOpenOrderInitialMargin")
        self.total_position_initial_margin = from_dict_get_float(self.account_data, "totalPositionInitialMargin")
        self.total_unrealized_profit = from_dict_get_float(self.account_data, "totalCrossUnPnl")
        self.total_wallet_balance = from_dict_get_float(self.account_data, "totalWalletBalance")
        self.balances = [BinanceSwapRequestBalanceData(i, self.account_data, self.asset_type, True)
                         for i in self.account_data["assets"]]
        self.positions = [BinanceRequestPositionData(i, self.symbol_name, self.asset_type, True)
                          for i in self.account_data["positions"]]
        self.has_been_init_data = True
        return self

    def get_all_data(self):
        if self.all_data is None:
            self.all_data = {
                "exchange_name": self.exchange_name,
                "symbol_name": self.symbol_name,
                "local_update_time": self.local_update_time,
                "asset_type": self.asset_type,
                "balances": self.balances,
                "total_wallet_balance": self.total_wallet_balance,
                "total_unrealized_profit": self.total_unrealized_profit,
                "total_open_order_initial_margin": self.total_open_order_initial_margin,
                "total_maintain_margin": self.total_maintain_margin,
                "total_margin": self.total_margin,
                "server_time": self.server_time,
                "positions": self.positions,
                "total_position_initial_margin": self.total_position_initial_margin,
                "total_available_margin": self.total_available_margin,
                "max_withdraw_amount": self.max_withdraw_amount,
                "fee_tier": self.fee_tier,
                "can_withdraw": self.can_withdraw,
                "can_trade": self.can_trade,
                "can_deposit": self.can_deposit,
                "is_multi_assets_margin": self.is_multi_assets_margin,
            }
        return self.all_data

    def __str__(self):
        self.init_data()
        return str(self.get_all_data())

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

    def get_is_multi_assets_margin(self):
        """是否是多账户资产类型"""
        return self.is_multi_assets_margin

    def get_can_deposit(self):
        """# 是否可以存钱"""
        return self.can_deposit

    def get_can_trade(self):
        """# 是否可以交易"""
        return self.can_trade

    def get_can_withdraw(self):
        """# 是否可以取钱"""
        return self.can_withdraw

    def get_fee_tier(self):
        """# 资金费率等级"""
        return self.fee_tier

    def get_max_withdraw_amount(self):
        """# 最大可取资金"""
        return self.max_withdraw_amount

    def get_total_margin(self):
        """# 总的初始化保证金"""
        return self.total_margin

    def get_total_used_margin(self):
        """# 总的使用的保证金"""
        return self.get_total_margin() - self.get_total_available_margin()

    def get_total_maintain_margin(self):
        """# 总的维持资金"""
        return self.total_maintain_margin

    def get_total_available_margin(self):
        """# 总的可用保证金"""
        return self.total_available_margin

    def get_total_open_order_initial_margin(self):
        """# 总的开仓订单初始保证金"""
        return self.total_open_order_initial_margin

    def get_total_position_initial_margin(self):
        """# 总的持仓初始化保证金"""
        return self.total_position_initial_margin

    def get_total_unrealized_profit(self):
        """# 总的未实现利润 """
        return self.total_unrealized_profit

    def get_total_wallet_balance(self):
        """# 总的钱包余额"""
        return self.total_wallet_balance

    def get_balances(self):
        """# 现货资产,账户余额，可用，冻结"""
        return self.balances

    def get_positions(self):
        """# 获取持仓数据"""
        return self.positions

    def get_spot_maker_commission_rate(self):
        # maker佣金费率
        return None

    def get_spot_taker_commission_rate(self):
        # taker佣金费率
        return None

    def get_future_maker_commission_rate(self):
        # maker佣金费率
        return None

    def get_future_taker_commission_rate(self):
        # taker佣金费率
        return None

    def get_option_maker_commission_rate(self):
        # maker佣金费率
        return None

    def get_option_taker_commission_rate(self):
        # taker佣金费率
        return None


class BinanceSwapWssAccountData(AccountData):
    def __init__(self, account_info, symbol_name, asset_type, has_been_json_encoded=False):
        super(BinanceSwapWssAccountData, self).__init__(account_info, has_been_json_encoded)
        self.exchange_name = "BINANCE"
        self.symbol_name = symbol_name
        self.local_update_time = time.time()  # 本地时间戳
        self.asset_type = asset_type
        self.account_data = self.account_info if has_been_json_encoded else None
        self.positions = None
        self.balances = None
        self.server_time = None
        self.all_data = None
        self.has_been_init_data = False

    def init_data(self):
        if not self.has_been_json_encoded:
            self.account_data = json.loads(self.account_info)
            self.has_been_json_encoded = True
        if self.has_been_init_data:
            return self
        self.server_time = from_dict_get_float(self.account_data, 'E')
        self.balances = [BinanceSwapWssBalanceData(i, self.symbol_name, self.asset_type, True)
                         for i in self.account_data['a']['B']]
        self.positions = [BinanceWssPositionData(i, self.symbol_name, self.asset_type, True) for i in
                          self.account_data['a']['P']]
        self.has_been_init_data = True
        return self

    def get_all_data(self):
        if self.all_data is not None:
            self.all_data = {
                "exchange_name": self.exchange_name,
                "symbol_name": self.symbol_name,
                "local_update_time": self.local_update_time,
                "asset_type": self.asset_type,
                "positions": self.positions,
                "balances": self.balances,
                "server_time": self.server_time
            }
        return self.all_data

    def __str__(self):
        self.init_data()
        return str(self.get_all_data())

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

    def get_can_deposit(self):
        """# 是否可以存钱"""
        return None

    def get_can_trade(self):
        """# 是否可以交易"""
        return None

    def get_can_withdraw(self):
        """# 是否可以取钱"""
        return None

    def get_fee_tier(self):
        """# 资金费率等级"""
        return None

    def get_max_withdraw_amount(self):
        """# 最大可取资金"""
        return None

    def get_total_margin(self):
        """# 总的初始化保证金"""
        return None

    def get_total_used_margin(self):
        """# 总的使用的保证金"""
        return None

    def get_total_maintain_margin(self):
        """# 总的维持资金"""
        return None

    def get_total_available_margin(self):
        """# 总的可用保证金"""
        return None

    def get_total_open_order_initial_margin(self):
        """# 总的开仓订单初始保证金"""
        return None

    def get_total_position_initial_margin(self):
        """# 总的持仓初始化保证金"""
        return None

    def get_total_unrealized_profit(self):
        """# 总的未实现利润 """
        return None

    def get_total_wallet_balance(self):
        """# 总的钱包余额"""
        return None

    def get_balances(self):
        """# 现货资产,账户余额，可用，冻结"""
        return self.balances

    def get_positions(self):
        """# 获取持仓数据"""
        return self.positions

    def get_spot_maker_commission_rate(self):
        # maker佣金费率
        return None

    def get_spot_taker_commission_rate(self):
        # taker佣金费率
        return None

    def get_future_maker_commission_rate(self):
        # maker佣金费率
        return None

    def get_future_taker_commission_rate(self):
        # taker佣金费率
        return None

    def get_option_maker_commission_rate(self):
        # maker佣金费率
        return None

    def get_option_taker_commission_rate(self):
        # taker佣金费率
        return None


class BinanceSpotWssAccountData(AccountData):
    def __init__(self, account_info, symbol_name, asset_type, has_been_json_encoded=False):
        super(BinanceSpotWssAccountData, self).__init__(account_info, has_been_json_encoded)
        self.exchange_name = "BINANCE"
        self.symbol_name = symbol_name
        self.local_update_time = time.time()  # 本地时间戳
        self.asset_type = asset_type
        self.account_data = self.account_info if has_been_json_encoded else None
        self.server_time = None
        self.balances = None
        self.all_data = None
        self.has_been_init_data = False

    def init_data(self):
        if not self.has_been_json_encoded:
            self.account_data = json.loads(self.account_info)
            self.has_been_json_encoded = True
        if self.has_been_init_data:
            return self
        self.server_time = from_dict_get_float(self.account_data, "E")
        self.balances = [BinanceSpotWssBalanceData(i, self.symbol_name, self.asset_type, True)
                         for i in self.account_data['B']]
        self.has_been_init_data = True
        return self

    def get_all_data(self):
        if self.all_data is None:
            self.all_data = {
                "symbol": self.symbol_name,
                "exchange_name": self.exchange_name,
                "local_update_time": self.local_update_time,
                "server_time": self.server_time,
                "balances": self.balances,
            }
        return self.all_data

    def __str__(self):
        self.init_data()
        return str(self.get_all_data())

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

    def get_can_deposit(self):
        """# 是否可以存钱"""
        return None

    def get_can_trade(self):
        """# 是否可以交易"""
        return None

    def get_can_withdraw(self):
        """# 是否可以取钱"""
        return None

    def get_fee_tier(self):
        """# 资金费率等级"""
        return None

    def get_max_withdraw_amount(self):
        """# 最大可取资金"""
        return None

    def get_total_margin(self):
        """# 总的初始化保证金"""
        return None

    def get_total_used_margin(self):
        """# 总的使用的保证金"""
        return None

    def get_total_maintain_margin(self):
        """# 总的维持资金"""
        return None

    def get_total_available_margin(self):
        """# 总的可用保证金"""
        return None

    def get_total_open_order_initial_margin(self):
        """# 总的开仓订单初始保证金"""
        return None

    def get_total_position_initial_margin(self):
        """# 总的持仓初始化保证金"""
        return None

    def get_total_unrealized_profit(self):
        """# 总的未实现利润 """
        return None

    def get_total_wallet_balance(self):
        """# 总的钱包余额"""
        return None

    def get_balances(self):
        """# 现货资产,账户余额，可用，冻结"""
        return self.balances

    def get_positions(self):
        """# 获取持仓数据"""
        return None

    def get_spot_maker_commission_rate(self):
        # maker佣金费率
        return None

    def get_spot_taker_commission_rate(self):
        # taker佣金费率
        return None

    def get_future_maker_commission_rate(self):
        # maker佣金费率
        return None

    def get_future_taker_commission_rate(self):
        # taker佣金费率
        return None

    def get_option_maker_commission_rate(self):
        # maker佣金费率
        return None

    def get_option_taker_commission_rate(self):
        # taker佣金费率
        return None
