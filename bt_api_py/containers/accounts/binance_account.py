import json
import time
from typing import Any

from bt_api_py.containers.accounts.account import AccountData
from bt_api_py.containers.balances.binance_balance import (
    BinanceSpotRequestBalanceData,
    BinanceSpotWssBalanceData,
    BinanceSwapRequestBalanceData,
    BinanceSwapWssBalanceData,
)
from bt_api_py.containers.positions.binance_position import (
    BinanceRequestPositionData,
    BinanceWssPositionData,
)
from bt_api_py.functions.utils import from_dict_get_bool, from_dict_get_float, from_dict_get_string


class BinanceSpotRequestAccountData(AccountData):
    """Binance现货账户数据类。

    用于存储和管理Binance现货账户的账户信息、余额等数据。
    """

    def __init__(
        self,
        account_info: dict[str, Any] | str,
        symbol_name: str,
        asset_type: str,
        has_been_json_encoded: bool = False,
    ) -> None:
        """初始化Binance现货账户数据。

        Args:
            account_info: 账户信息，可以是字典或JSON字符串。
            symbol_name: 交易对名称。
            asset_type: 资产类型。
            has_been_json_encoded: 是否已经JSON编码。
        """
        super().__init__(account_info, has_been_json_encoded)
        self.exchange_name = "BINANCE"
        self.symbol_name = symbol_name
        self.local_update_time = time.time()
        self.asset_type = asset_type
        self.account_data = self.account_info if has_been_json_encoded else None
        self.balances: list[BinanceSpotRequestBalanceData] | None = None
        self.can_withdraw: bool | None = None
        self.can_trade: bool | None = None
        self.can_deposit: bool | None = None
        self.account_type: str | None = None
        self.server_time: float | None = None
        self.is_multi_assets_margin: bool | None = None
        self.all_data: dict[str, Any] | None = None
        self.has_been_init_data = False

    def init_data(self) -> "BinanceSpotRequestAccountData":
        """初始化账户数据。

        Returns:
            初始化后的账户数据对象。
        """
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
        self.balances = [
            BinanceSpotRequestBalanceData(i, i["asset"], self.asset_type, True)
            for i in self.account_data["balances"]
        ]
        self.has_been_init_data = True
        return self

    def get_all_data(self) -> dict[str, Any]:
        """获取所有账户数据。

        Returns:
            包含所有账户信息的字典。
        """
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
                "server_time": self.server_time,
            }
        return self.all_data

    def __str__(self) -> str:
        self.init_data()
        return str(self.get_all_data())

    def __repr__(self) -> str:
        return self.__str__()

    def get_exchange_name(self) -> str:
        """获取交易所名称。

        Returns:
            交易所名称。
        """
        return self.exchange_name

    def get_symbol_name(self) -> str:
        """获取交易对名称。

        Returns:
            交易对名称。
        """
        return self.symbol_name

    def get_asset_type(self) -> str:
        """获取资产类型。

        Returns:
            资产类型。
        """
        return self.asset_type

    def get_server_time(self) -> float | None:
        """获取服务器时间戳。

        Returns:
            服务器时间戳。
        """
        return self.server_time

    def get_local_update_time(self) -> float:
        """获取本地时间戳。

        Returns:
            本地时间戳。
        """
        return self.local_update_time

    def get_account_id(self) -> None:
        """获取账户ID。

        Returns:
            账户ID（现货账户无此信息）。
        """
        return None

    def get_account_type(self) -> str | None:
        """获取账户类型。

        Returns:
            账户类型。
        """
        return self.account_type

    def get_is_multi_assets_margin(self) -> bool | None:
        """获取是否是多账户资产类型。

        Returns:
            是否是多账户资产类型。
        """
        return self.is_multi_assets_margin

    def get_can_deposit(self) -> bool | None:
        """获取是否可以存款。

        Returns:
            是否可以存款。
        """
        return self.can_deposit

    def get_can_trade(self) -> bool | None:
        """获取是否可以交易。

        Returns:
            是否可以交易。
        """
        return self.can_trade

    def get_can_withdraw(self) -> bool | None:
        """获取是否可以取款。

        Returns:
            是否可以取款。
        """
        return self.can_withdraw

    def get_fee_tier(self) -> None:
        """获取资金费率等级。

        Returns:
            资金费率等级（现货账户无此信息）。
        """
        return None

    def get_max_withdraw_amount(self) -> None:
        """获取最大可取资金。

        Returns:
            最大可取资金（现货账户无此信息）。
        """
        return None

    def get_total_margin(self) -> None:
        """获取总的初始化保证金。

        Returns:
            总的初始化保证金（现货账户无此信息）。
        """
        return None

    def get_margin(self) -> int:
        """获取保证金。

        Returns:
            保证金（现货账户为0）。
        """
        return 0

    def get_total_used_margin(self) -> None:
        """获取总的使用的保证金。

        Returns:
            总的使用的保证金（现货账户无此信息）。
        """
        return None

    def get_total_maintain_margin(self) -> None:
        """获取总的维持资金。

        Returns:
            总的维持资金（现货账户无此信息）。
        """
        return None

    def get_available_margin(self) -> int:
        """获取可用保证金。

        Returns:
            可用保证金（现货账户为0）。
        """
        return 0

    def get_total_available_margin(self) -> None:
        """获取总的可用保证金。

        Returns:
            总的可用保证金（现货账户无此信息）。
        """
        return None

    def get_total_open_order_initial_margin(self) -> None:
        """获取总的开仓订单初始保证金。

        Returns:
            总的开仓订单初始保证金（现货账户无此信息）。
        """
        return None

    def get_total_position_initial_margin(self) -> None:
        """获取总的持仓初始化保证金。

        Returns:
            总的持仓初始化保证金（现货账户无此信息）。
        """
        return None

    def get_total_unrealized_profit(self) -> None:
        """获取总的未实现利润。

        Returns:
            总的未实现利润（现货账户无此信息）。
        """
        return None

    def get_unrealized_profit(self) -> int:
        """获取未实现利润。

        Returns:
            未实现利润（现货账户为0）。
        """
        return 0

    def get_total_wallet_balance(self) -> None:
        """获取总的钱包余额。

        Returns:
            总的钱包余额（现货账户无此信息）。
        """
        return None

    def get_balances(self) -> list[BinanceSpotRequestBalanceData] | None:
        """获取现货资产余额列表。

        Returns:
            余额数据列表。
        """
        return self.balances

    def get_positions(self) -> None:
        """获取持仓数据。

        Returns:
            持仓数据（现货账户无此信息）。
        """
        return None

    def get_spot_maker_commission_rate(self) -> None:
        """获取现货maker佣金费率。

        Returns:
            maker佣金费率。
        """
        return None

    def get_spot_taker_commission_rate(self) -> None:
        """获取现货taker佣金费率。

        Returns:
            taker佣金费率。
        """
        return None

    def get_future_maker_commission_rate(self) -> None:
        """获取合约maker佣金费率。

        Returns:
            maker佣金费率。
        """
        return None

    def get_future_taker_commission_rate(self) -> None:
        """获取合约taker佣金费率。

        Returns:
            taker佣金费率。
        """
        return None

    def get_option_maker_commission_rate(self) -> None:
        """获取期权maker佣金费率。

        Returns:
            maker佣金费率。
        """
        return None

    def get_option_taker_commission_rate(self) -> None:
        """获取期权taker佣金费率。

        Returns:
            taker佣金费率。
        """
        return None


class BinanceSwapRequestAccountData(AccountData):
    """Binance永续合约账户数据类。

    用于存储和管理Binance永续合约账户的账户信息、余额、持仓等数据。
    """

    def __init__(
        self,
        account_info: dict[str, Any] | str,
        symbol_name: str,
        asset_type: str,
        has_been_json_encoded: bool = False,
    ) -> None:
        """初始化Binance永续合约账户数据。

        Args:
            account_info: 账户信息，可以是字典或JSON字符串。
            symbol_name: 交易对名称。
            asset_type: 资产类型。
            has_been_json_encoded: 是否已经JSON编码。
        """
        super().__init__(account_info, has_been_json_encoded)
        self.exchange_name = "BINANCE"
        self.symbol_name = symbol_name
        self.local_update_time = time.time()
        self.asset_type = asset_type
        self.account_data = self.account_info if has_been_json_encoded else None
        self.balances: list[BinanceSwapRequestBalanceData] | None = None
        self.total_wallet_balance: float | None = None
        self.total_unrealized_profit: float | None = None
        self.total_open_order_initial_margin: float | None = None
        self.total_maintain_margin: float | None = None
        self.total_margin: float | None = None
        self.server_time: float | None = None
        self.positions: list[BinanceRequestPositionData] | None = None
        self.total_position_initial_margin: float | None = None
        self.total_available_margin: float | None = None
        self.max_withdraw_amount: float | None = None
        self.fee_tier: float | None = None
        self.can_withdraw: bool | None = None
        self.can_trade: bool | None = None
        self.can_deposit: bool | None = None
        self.is_multi_assets_margin: bool | None = None
        self.all_data: dict[str, Any] | None = None
        self.has_been_init_data = False

    def init_data(self) -> "BinanceSwapRequestAccountData":
        """初始化账户数据。

        Returns:
            初始化后的账户数据对象。
        """
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
        self.total_open_order_initial_margin = from_dict_get_float(
            self.account_data, "totalOpenOrderInitialMargin"
        )
        self.total_position_initial_margin = from_dict_get_float(
            self.account_data, "totalPositionInitialMargin"
        )
        self.total_unrealized_profit = from_dict_get_float(self.account_data, "totalCrossUnPnl")
        self.total_wallet_balance = from_dict_get_float(self.account_data, "totalWalletBalance")
        self.balances = [
            BinanceSwapRequestBalanceData(i, self.account_data, self.asset_type, True)
            for i in self.account_data["assets"]
        ]
        self.positions = [
            BinanceRequestPositionData(i, self.symbol_name, self.asset_type, True)
            for i in self.account_data["positions"]
        ]
        self.has_been_init_data = True
        return self

    def get_all_data(self) -> dict[str, Any]:
        """获取所有账户数据。

        Returns:
            包含所有账户信息的字典。
        """
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

    def __str__(self) -> str:
        self.init_data()
        return str(self.get_all_data())

    def __repr__(self) -> str:
        return self.__str__()

    def get_exchange_name(self) -> str:
        """获取交易所名称。

        Returns:
            交易所名称。
        """
        return self.exchange_name

    def get_symbol_name(self) -> str:
        """获取交易对名称。

        Returns:
            交易对名称。
        """
        return self.symbol_name

    def get_asset_type(self) -> str:
        """获取资产类型。

        Returns:
            资产类型。
        """
        return self.asset_type

    def get_server_time(self) -> float | None:
        """获取服务器时间戳。

        Returns:
            服务器时间戳。
        """
        return self.server_time

    def get_local_update_time(self) -> float:
        """获取本地时间戳。

        Returns:
            本地时间戳。
        """
        return self.local_update_time

    def get_account_id(self) -> None:
        """获取账户ID。

        Returns:
            账户ID。
        """
        return None

    def get_account_type(self) -> None:
        """获取账户类型。

        Returns:
            账户类型。
        """
        return None

    def get_is_multi_assets_margin(self) -> bool | None:
        """获取是否是多账户资产类型。

        Returns:
            是否是多账户资产类型。
        """
        return self.is_multi_assets_margin

    def get_can_deposit(self) -> bool | None:
        """获取是否可以存款。

        Returns:
            是否可以存款。
        """
        return self.can_deposit

    def get_can_trade(self) -> bool | None:
        """获取是否可以交易。

        Returns:
            是否可以交易。
        """
        return self.can_trade

    def get_can_withdraw(self) -> bool | None:
        """获取是否可以取款。

        Returns:
            是否可以取款。
        """
        return self.can_withdraw

    def get_fee_tier(self) -> float | None:
        """获取资金费率等级。

        Returns:
            资金费率等级。
        """
        return self.fee_tier

    def get_max_withdraw_amount(self) -> float | None:
        """获取最大可取资金。

        Returns:
            最大可取资金。
        """
        return self.max_withdraw_amount

    def get_total_margin(self) -> float | None:
        """获取总的初始化保证金。

        Returns:
            总的初始化保证金。
        """
        return self.total_margin

    def get_total_used_margin(self) -> float | None:
        """获取总的使用的保证金。

        Returns:
            总的使用的保证金。
        """
        return self.get_total_margin() - self.get_total_available_margin()

    def get_total_maintain_margin(self) -> float | None:
        """获取总的维持资金。

        Returns:
            总的维持资金。
        """
        return self.total_maintain_margin

    def get_total_available_margin(self) -> float | None:
        """获取总的可用保证金。

        Returns:
            总的可用保证金。
        """
        return self.total_available_margin

    def get_total_open_order_initial_margin(self) -> float | None:
        """获取总的开仓订单初始保证金。

        Returns:
            总的开仓订单初始保证金。
        """
        return self.total_open_order_initial_margin

    def get_total_position_initial_margin(self) -> float | None:
        """获取总的持仓初始化保证金。

        Returns:
            总的持仓初始化保证金。
        """
        return self.total_position_initial_margin

    def get_total_unrealized_profit(self) -> float | None:
        """获取总的未实现利润。

        Returns:
            总的未实现利润。
        """
        return self.total_unrealized_profit

    def get_total_wallet_balance(self) -> float | None:
        """获取总的钱包余额。

        Returns:
            总的钱包余额。
        """
        return self.total_wallet_balance

    def get_balances(self) -> list[BinanceSwapRequestBalanceData] | None:
        """获取账户余额列表。

        Returns:
            余额数据列表。
        """
        return self.balances

    def get_positions(self) -> list[BinanceRequestPositionData] | None:
        """获取持仓数据。

        Returns:
            持仓数据列表。
        """
        return self.positions

    def get_spot_maker_commission_rate(self) -> None:
        """获取现货maker佣金费率。

        Returns:
            maker佣金费率。
        """
        return None

    def get_spot_taker_commission_rate(self) -> None:
        """获取现货taker佣金费率。

        Returns:
            taker佣金费率。
        """
        return None

    def get_future_maker_commission_rate(self) -> None:
        """获取合约maker佣金费率。

        Returns:
            maker佣金费率。
        """
        return None

    def get_future_taker_commission_rate(self) -> None:
        """获取合约taker佣金费率。

        Returns:
            taker佣金费率。
        """
        return None

    def get_option_maker_commission_rate(self) -> None:
        """获取期权maker佣金费率。

        Returns:
            maker佣金费率。
        """
        return None

    def get_option_taker_commission_rate(self) -> None:
        """获取期权taker佣金费率。

        Returns:
            taker佣金费率。
        """
        return None


class BinanceSwapWssAccountData(AccountData):
    """Binance永续合约WebSocket账户数据类。

    用于存储和管理通过WebSocket推送的Binance永续合约账户数据。
    """

    def __init__(
        self,
        account_info: dict[str, Any] | str,
        symbol_name: str,
        asset_type: str,
        has_been_json_encoded: bool = False,
    ) -> None:
        """初始化Binance永续合约WebSocket账户数据。

        Args:
            account_info: 账户信息，可以是字典或JSON字符串。
            symbol_name: 交易对名称。
            asset_type: 资产类型。
            has_been_json_encoded: 是否已经JSON编码。
        """
        super().__init__(account_info, has_been_json_encoded)
        self.exchange_name = "BINANCE"
        self.symbol_name = symbol_name
        self.local_update_time = time.time()
        self.asset_type = asset_type
        self.account_data = self.account_info if has_been_json_encoded else None
        self.positions: list[BinanceWssPositionData] | None = None
        self.balances: list[BinanceSwapWssBalanceData] | None = None
        self.server_time: float | None = None
        self.all_data: dict[str, Any] | None = None
        self.has_been_init_data = False

    def init_data(self) -> "BinanceSwapWssAccountData":
        """初始化账户数据。

        Returns:
            初始化后的账户数据对象。
        """
        if not self.has_been_json_encoded:
            self.account_data = json.loads(self.account_info)
            self.has_been_json_encoded = True
        if self.has_been_init_data:
            return self
        self.server_time = from_dict_get_float(self.account_data, "E")
        self.balances = [
            BinanceSwapWssBalanceData(i, self.symbol_name, self.asset_type, True)
            for i in self.account_data["a"]["B"]
        ]
        self.positions = [
            BinanceWssPositionData(i, self.symbol_name, self.asset_type, True)
            for i in self.account_data["a"]["P"]
        ]
        self.has_been_init_data = True
        return self

    def get_all_data(self) -> dict[str, Any] | None:
        """获取所有账户数据。

        Returns:
            包含所有账户信息的字典。
        """
        if self.all_data is not None:
            self.all_data = {
                "exchange_name": self.exchange_name,
                "symbol_name": self.symbol_name,
                "local_update_time": self.local_update_time,
                "asset_type": self.asset_type,
                "positions": self.positions,
                "balances": self.balances,
                "server_time": self.server_time,
            }
        return self.all_data

    def __str__(self) -> str:
        self.init_data()
        return str(self.get_all_data())

    def __repr__(self) -> str:
        return self.__str__()

    def get_exchange_name(self) -> str:
        """获取交易所名称。

        Returns:
            交易所名称。
        """
        return self.exchange_name

    def get_symbol_name(self) -> str:
        """获取交易对名称。

        Returns:
            交易对名称。
        """
        return self.symbol_name

    def get_asset_type(self) -> str:
        """获取资产类型。

        Returns:
            资产类型。
        """
        return self.asset_type

    def get_server_time(self) -> float | None:
        """获取服务器时间戳。

        Returns:
            服务器时间戳。
        """
        return self.server_time

    def get_local_update_time(self) -> float:
        """获取本地时间戳。

        Returns:
            本地时间戳。
        """
        return self.local_update_time

    def get_account_id(self) -> None:
        """获取账户ID。

        Returns:
            账户ID。
        """
        return None

    def get_account_type(self) -> None:
        """获取账户类型。

        Returns:
            账户类型。
        """
        return None

    def get_can_deposit(self) -> None:
        """获取是否可以存款。

        Returns:
            是否可以存款。
        """
        return None

    def get_can_trade(self) -> None:
        """获取是否可以交易。

        Returns:
            是否可以交易。
        """
        return None

    def get_can_withdraw(self) -> None:
        """获取是否可以取款。

        Returns:
            是否可以取款。
        """
        return None

    def get_fee_tier(self) -> None:
        """获取资金费率等级。

        Returns:
            资金费率等级。
        """
        return None

    def get_max_withdraw_amount(self) -> None:
        """获取最大可取资金。

        Returns:
            最大可取资金。
        """
        return None

    def get_total_margin(self) -> None:
        """获取总的初始化保证金。

        Returns:
            总的初始化保证金。
        """
        return None

    def get_total_used_margin(self) -> None:
        """获取总的使用的保证金。

        Returns:
            总的使用的保证金。
        """
        return None

    def get_total_maintain_margin(self) -> None:
        """获取总的维持资金。

        Returns:
            总的维持资金。
        """
        return None

    def get_total_available_margin(self) -> None:
        """获取总的可用保证金。

        Returns:
            总的可用保证金。
        """
        return None

    def get_total_open_order_initial_margin(self) -> None:
        """获取总的开仓订单初始保证金。

        Returns:
            总的开仓订单初始保证金。
        """
        return None

    def get_total_position_initial_margin(self) -> None:
        """获取总的持仓初始化保证金。

        Returns:
            总的持仓初始化保证金。
        """
        return None

    def get_total_unrealized_profit(self) -> None:
        """获取总的未实现利润。

        Returns:
            总的未实现利润。
        """
        return None

    def get_total_wallet_balance(self) -> None:
        """获取总的钱包余额。

        Returns:
            总的钱包余额。
        """
        return None

    def get_balances(self) -> list[BinanceSwapWssBalanceData] | None:
        """获取账户余额列表。

        Returns:
            余额数据列表。
        """
        return self.balances

    def get_positions(self) -> list[BinanceWssPositionData] | None:
        """获取持仓数据。

        Returns:
            持仓数据列表。
        """
        return self.positions

    def get_spot_maker_commission_rate(self) -> None:
        """获取现货maker佣金费率。

        Returns:
            maker佣金费率。
        """
        return None

    def get_spot_taker_commission_rate(self) -> None:
        """获取现货taker佣金费率。

        Returns:
            taker佣金费率。
        """
        return None

    def get_future_maker_commission_rate(self) -> None:
        """获取合约maker佣金费率。

        Returns:
            maker佣金费率。
        """
        return None

    def get_future_taker_commission_rate(self) -> None:
        """获取合约taker佣金费率。

        Returns:
            taker佣金费率。
        """
        return None

    def get_option_maker_commission_rate(self) -> None:
        """获取期权maker佣金费率。

        Returns:
            maker佣金费率。
        """
        return None

    def get_option_taker_commission_rate(self) -> None:
        """获取期权taker佣金费率。

        Returns:
            taker佣金费率。
        """
        return None


class BinanceSpotWssAccountData(AccountData):
    """Binance现货WebSocket账户数据类。

    用于存储和管理通过WebSocket推送的Binance现货账户数据。
    """

    def __init__(
        self,
        account_info: dict[str, Any] | str,
        symbol_name: str,
        asset_type: str,
        has_been_json_encoded: bool = False,
    ) -> None:
        """初始化Binance现货WebSocket账户数据。

        Args:
            account_info: 账户信息，可以是字典或JSON字符串。
            symbol_name: 交易对名称。
            asset_type: 资产类型。
            has_been_json_encoded: 是否已经JSON编码。
        """
        super().__init__(account_info, has_been_json_encoded)
        self.exchange_name = "BINANCE"
        self.symbol_name = symbol_name
        self.local_update_time = time.time()
        self.asset_type = asset_type
        self.account_data = self.account_info if has_been_json_encoded else None
        self.server_time: float | None = None
        self.balances: list[BinanceSpotWssBalanceData] | None = None
        self.all_data: dict[str, Any] | None = None
        self.has_been_init_data = False

    def init_data(self) -> "BinanceSpotWssAccountData":
        """初始化账户数据。

        Returns:
            初始化后的账户数据对象。
        """
        if not self.has_been_json_encoded:
            self.account_data = json.loads(self.account_info)
            self.has_been_json_encoded = True
        if self.has_been_init_data:
            return self
        self.server_time = from_dict_get_float(self.account_data, "E")
        self.balances = [
            BinanceSpotWssBalanceData(i, self.symbol_name, self.asset_type, True)
            for i in self.account_data["B"]
        ]
        self.has_been_init_data = True
        return self

    def get_all_data(self) -> dict[str, Any]:
        """获取所有账户数据。

        Returns:
            包含所有账户信息的字典。
        """
        if self.all_data is None:
            self.all_data = {
                "symbol": self.symbol_name,
                "exchange_name": self.exchange_name,
                "local_update_time": self.local_update_time,
                "server_time": self.server_time,
                "balances": self.balances,
            }
        return self.all_data

    def __str__(self) -> str:
        self.init_data()
        return str(self.get_all_data())

    def __repr__(self) -> str:
        return self.__str__()

    def get_exchange_name(self) -> str:
        """获取交易所名称。

        Returns:
            交易所名称。
        """
        return self.exchange_name

    def get_symbol_name(self) -> str:
        """获取交易对名称。

        Returns:
            交易对名称。
        """
        return self.symbol_name

    def get_asset_type(self) -> str:
        """获取资产类型。

        Returns:
            资产类型。
        """
        return self.asset_type

    def get_server_time(self) -> float | None:
        """获取服务器时间戳。

        Returns:
            服务器时间戳。
        """
        return self.server_time

    def get_local_update_time(self) -> float:
        """获取本地时间戳。

        Returns:
            本地时间戳。
        """
        return self.local_update_time

    def get_account_id(self) -> None:
        """获取账户ID。

        Returns:
            账户ID。
        """
        return None

    def get_account_type(self) -> None:
        """获取账户类型。

        Returns:
            账户类型。
        """
        return None

    def get_can_deposit(self) -> None:
        """获取是否可以存款。

        Returns:
            是否可以存款。
        """
        return None

    def get_can_trade(self) -> None:
        """获取是否可以交易。

        Returns:
            是否可以交易。
        """
        return None

    def get_can_withdraw(self) -> None:
        """获取是否可以取款。

        Returns:
            是否可以取款。
        """
        return None

    def get_fee_tier(self) -> None:
        """获取资金费率等级。

        Returns:
            资金费率等级。
        """
        return None

    def get_max_withdraw_amount(self) -> None:
        """获取最大可取资金。

        Returns:
            最大可取资金。
        """
        return None

    def get_total_margin(self) -> None:
        """获取总的初始化保证金。

        Returns:
            总的初始化保证金。
        """
        return None

    def get_total_used_margin(self) -> None:
        """获取总的使用的保证金。

        Returns:
            总的使用的保证金。
        """
        return None

    def get_total_maintain_margin(self) -> None:
        """获取总的维持资金。

        Returns:
            总的维持资金。
        """
        return None

    def get_total_available_margin(self) -> None:
        """获取总的可用保证金。

        Returns:
            总的可用保证金。
        """
        return None

    def get_total_open_order_initial_margin(self) -> None:
        """获取总的开仓订单初始保证金。

        Returns:
            总的开仓订单初始保证金。
        """
        return None

    def get_total_position_initial_margin(self) -> None:
        """获取总的持仓初始化保证金。

        Returns:
            总的持仓初始化保证金。
        """
        return None

    def get_total_unrealized_profit(self) -> None:
        """获取总的未实现利润。

        Returns:
            总的未实现利润。
        """
        return None

    def get_total_wallet_balance(self) -> None:
        """获取总的钱包余额。

        Returns:
            总的钱包余额。
        """
        return None

    def get_balances(self) -> list[BinanceSpotWssBalanceData] | None:
        """获取账户余额列表。

        Returns:
            余额数据列表。
        """
        return self.balances

    def get_positions(self) -> None:
        """获取持仓数据。

        Returns:
            持仓数据。
        """
        return None

    def get_spot_maker_commission_rate(self) -> None:
        """获取现货maker佣金费率。

        Returns:
            maker佣金费率。
        """
        return None

    def get_spot_taker_commission_rate(self) -> None:
        """获取现货taker佣金费率。

        Returns:
            taker佣金费率。
        """
        return None

    def get_future_maker_commission_rate(self) -> None:
        """获取合约maker佣金费率。

        Returns:
            maker佣金费率。
        """
        return None

    def get_future_taker_commission_rate(self) -> None:
        """获取合约taker佣金费率。

        Returns:
            taker佣金费率。
        """
        return None

    def get_option_maker_commission_rate(self) -> None:
        """获取期权maker佣金费率。

        Returns:
            maker佣金费率。
        """
        return None

    def get_option_taker_commission_rate(self) -> None:
        """获取期权taker佣金费率。

        Returns:
            taker佣金费率。
        """
        return None
