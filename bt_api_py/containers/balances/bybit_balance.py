import time
from typing import Any

from bt_api_py.containers.balances.balance import BalanceData
from bt_api_py.logging_factory import get_logger

logger = get_logger("container")


class BybitBalanceData(BalanceData):
    """保存 Bybit 账户余额信息"""

    def __init__(
        self,
        balance_info: dict[str, Any],
        asset_type: str,
        has_been_json_encoded: bool = False,
    ) -> None:
        """初始化Bybit余额数据。

        Args:
            balance_info: 余额信息字典。
            asset_type: 资产类型（如 spot、swap 等）。
            has_been_json_encoded: 是否已经 JSON 编码，默认为 False。
        """
        super().__init__(balance_info, has_been_json_encoded)
        self.exchange_name = "BYBIT"
        self.local_update_time = time.time()
        self.asset_type = asset_type
        self.balance_data = balance_info if has_been_json_encoded else None
        self.account_type: str | None = None
        self.total_equity: float | None = None
        self.total_wallet_balance: float | None = None
        self.total_available_balance: float | None = None
        self.coins: list[dict[str, Any]] = []
        self.all_data: dict[str, Any] | None = None
        self.has_been_init_data = False

    def init_data(self) -> "BybitBalanceData":
        """初始化余额数据。

        Returns:
            初始化后的 BybitBalanceData 实例。
        """
        if self.has_been_init_data or self.balance_data is None:
            return self

        try:
            result = self.balance_data.get("result", {})
            list_data = result.get("list", [])

            if not list_data:
                return self

            account = list_data[0]  # Bybit 返回列表，取第一个

            # 账户基本信息
            self.account_type = account.get("accountType")
            self.total_equity = account.get("totalEquity")
            self.total_wallet_balance = account.get("totalWalletBalance")
            self.total_available_balance = account.get("totalAvailableBalance")

            # 币种信息
            coins_data = account.get("coin", [])
            self.coins = []

            for coin_info in coins_data:
                coin = {
                    "coin": coin_info.get("coin"),
                    "wallet_balance": float(coin_info.get("walletBalance", 0)),
                    "available_to_withdraw": float(coin_info.get("availableToWithdraw", 0)),
                    "locked": float(coin_info.get("locked", 0)),
                    "equity": float(coin_info.get("equity", 0)),
                    "unrealised_pnl": float(coin_info.get("unrealisedPnl", 0)),
                }
                self.coins.append(coin)

            self.has_been_init_data = True

        except Exception as e:
            logger.error(f"Error parsing Bybit balance data: {e}", exc_info=True)

            self.has_been_init_data = False
        return self

    def get_all_data(self) -> dict[str, Any]:
        """获取所有余额数据。

        Returns:
            包含所有余额信息的字典。
        """
        if self.all_data is None:
            self.init_data()
            self.all_data = {
                "exchange_name": self.exchange_name,
                "asset_type": self.asset_type,
                "local_update_time": self.local_update_time,
                "account_type": self.account_type,
                "total_equity": self.total_equity,
                "total_wallet_balance": self.total_wallet_balance,
                "total_available_balance": self.total_available_balance,
                "coins": self.coins,
            }
        return self.all_data

    def get_coin_balance(self, coin: str) -> dict[str, Any] | None:
        """获取指定币种的余额。

        Args:
            coin: 币种名称。

        Returns:
            指定币种的余额信息字典，如果未找到则返回 None。
        """
        for coin_info in self.coins:
            if coin_info["coin"] == coin.upper():
                return coin_info
        return None

    def get_available_balance(self, coin: str) -> float:
        """获取指定币种的可用余额。

        Args:
            coin: 币种名称。

        Returns:
            指定币种的可用余额。
        """
        coin_info = self.get_coin_balance(coin)
        if coin_info:
            return coin_info["available_to_withdraw"]
        return 0.0

    def get_locked_balance(self, coin: str) -> float:
        """获取指定币种的锁定余额。

        Args:
            coin: 币种名称。

        Returns:
            指定币种的锁定余额。
        """
        coin_info = self.get_coin_balance(coin)
        if coin_info:
            return coin_info["locked"]
        return 0.0

    def get_total_balance(self, coin: str) -> float:
        """获取指定币种的总余额。

        Args:
            coin: 币种名称。

        Returns:
            指定币种的总余额。
        """
        coin_info = self.get_coin_balance(coin)
        if coin_info:
            return coin_info["wallet_balance"]
        return 0.0

    def get_unrealised_pnl(self, coin: str) -> float:
        """获取指定币种的未实现盈亏。

        Args:
            coin: 币种名称。

        Returns:
            指定币种的未实现盈亏。
        """
        coin_info = self.get_coin_balance(coin)
        if coin_info:
            return coin_info["unrealised_pnl"]
        return 0.0

    def get_all_available_balances(self) -> dict[str, float]:
        """获取所有币种的可用余额。

        Returns:
            字典，键为币种名称，值为可用余额。
        """
        return {coin["coin"]: coin["available_to_withdraw"] for coin in self.coins}

    def get_total_equity(self) -> float:
        """获取总权益。

        Returns:
            总权益。
        """
        return float(self.total_equity) if self.total_equity else 0.0

    def get_total_wallet_balance(self) -> float:
        """获取总钱包余额。

        Returns:
            总钱包余额。
        """
        return float(self.total_wallet_balance) if self.total_wallet_balance else 0.0

    def has_balance(self, coin: str) -> bool:
        """检查是否有指定币种的余额。

        Args:
            coin: 币种名称。

        Returns:
            如果有可用余额则返回 True，否则返回 False。
        """
        return self.get_available_balance(coin) > 0

    def __str__(self) -> str:
        """返回余额的字符串表示。

        Returns:
            余额信息的字符串表示。
        """
        self.init_data()
        return (
            f"BybitBalance(account_type={self.account_type}, "
            f"total_equity={self.total_equity}, "
            f"available={self.total_available_balance})"
        )

    def get_exchange_name(self) -> str:
        """获取交易所名称。

        Returns:
            交易所名称 "BYBIT"。
        """
        return self.exchange_name

    def get_asset_type(self) -> str:
        """获取资产类型。

        Returns:
            资产类型。
        """
        return self.asset_type

    def get_server_time(self) -> float:
        """获取服务器时间戳。

        Returns:
            本地更新时间戳作为服务器时间。
        """
        return self.local_update_time

    def get_local_update_time(self) -> float:
        """获取本地更新时间戳。

        Returns:
            本地更新时间戳。
        """
        return self.local_update_time

    def get_account_id(self) -> str | None:
        """获取账户ID。

        Returns:
            账户ID。
        """
        return self.account_type

    def get_account_type(self) -> str | None:
        """获取账户类型。

        Returns:
            账户类型。
        """
        return self.account_type

    def get_fee_tier(self) -> str | None:
        """获取手续费等级。

        Returns:
            手续费等级（暂未实现）。
        """
        return None

    def get_max_withdraw_amount(self) -> float | None:
        """获取最大提现金额。

        Returns:
            最大提现金额（暂未实现）。
        """
        return None

    def get_margin(self) -> float | None:
        """获取保证金。

        Returns:
            保证金（暂未实现）。
        """
        return None

    def get_used_margin(self) -> float | None:
        """获取已用保证金。

        Returns:
            已用保证金（暂未实现）。
        """
        return None

    def get_maintain_margin(self) -> float | None:
        """获取维持保证金。

        Returns:
            维持保证金（暂未实现）。
        """
        return None

    def get_available_margin(self) -> float | None:
        """获取可用保证金。

        Returns:
            可用保证金（暂未实现）。
        """
        return None

    def get_open_order_initial_margin(self) -> float | None:
        """获取挂单初始保证金。

        Returns:
            挂单初始保证金（暂未实现）。
        """
        return None

    def get_position_initial_margin(self) -> float | None:
        """获取持仓初始保证金。

        Returns:
            持仓初始保证金（暂未实现）。
        """
        return None

    def get_unrealized_profit(self) -> float | None:
        """获取未实现盈亏。

        Returns:
            未实现盈亏（暂未实现）。
        """
        return None

    def get_interest(self) -> float | None:
        """获取利息。

        Returns:
            利息（暂未实现）。
        """
        return None


class BybitSpotBalanceData(BybitBalanceData):
    """Bybit 现货余额数据"""

    def __init__(self, balance_info: dict[str, Any], has_been_json_encoded: bool = False) -> None:
        """初始化Bybit现货余额数据。

        Args:
            balance_info: 余额信息字典。
            has_been_json_encoded: 是否已经 JSON 编码，默认为 False。
        """
        super().__init__(balance_info, "spot", has_been_json_encoded)

    def init_data(self) -> "BybitSpotBalanceData":
        """初始化现货余额数据。

        Returns:
            初始化后的 BybitSpotBalanceData 实例。
        """
        return super().init_data()  # type: ignore


class BybitSwapBalanceData(BybitBalanceData):
    """Bybit 期货/swap 余额数据"""

    def __init__(self, balance_info: dict[str, Any], has_been_json_encoded: bool = False) -> None:
        """初始化Bybit期货余额数据。

        Args:
            balance_info: 余额信息字典。
            has_been_json_encoded: 是否已经 JSON 编码，默认为 False。
        """
        super().__init__(balance_info, "swap", has_been_json_encoded)

    def init_data(self) -> "BybitSwapBalanceData":
        """初始化期货余额数据。

        Returns:
            初始化后的 BybitSwapBalanceData 实例。
        """
        return super().init_data()  # type: ignore
