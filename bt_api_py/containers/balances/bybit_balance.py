import time

from bt_api_py.containers.balances.balance import BalanceData


class BybitBalanceData(BalanceData):
    """保存 Bybit 账户余额信息"""

    def __init__(self, balance_info, asset_type, has_been_json_encoded=False):
        super().__init__(balance_info, has_been_json_encoded)
        self.exchange_name = "BYBIT"
        self.local_update_time = time.time()
        self.asset_type = asset_type
        self.balance_data = balance_info if has_been_json_encoded else None
        self.account_type = None
        self.total_equity = None
        self.total_wallet_balance = None
        self.total_available_balance = None
        self.coins = []
        self.all_data = None
        self.has_been_init_data = False

    def init_data(self):
        """初始化余额数据"""
        if self.has_been_init_data or self.balance_data is None:
            return self

        try:
            result = self.balance_data.get("result", {})
            list_data = result.get("list", [])

            if not list_data:
                return

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
            print(f"Error parsing Bybit balance data: {e}")
            self.has_been_init_data = False
        return self

    def get_all_data(self):
        """获取所有余额数据"""
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

    def get_coin_balance(self, coin):
        """获取指定币种的余额"""
        for coin_info in self.coins:
            if coin_info["coin"] == coin.upper():
                return coin_info
        return None

    def get_available_balance(self, coin):
        """获取指定币种的可用余额"""
        coin_info = self.get_coin_balance(coin)
        if coin_info:
            return coin_info["available_to_withdraw"]
        return 0.0

    def get_locked_balance(self, coin):
        """获取指定币种的锁定余额"""
        coin_info = self.get_coin_balance(coin)
        if coin_info:
            return coin_info["locked"]
        return 0.0

    def get_total_balance(self, coin):
        """获取指定币种的总余额"""
        coin_info = self.get_coin_balance(coin)
        if coin_info:
            return coin_info["wallet_balance"]
        return 0.0

    def get_unrealised_pnl(self, coin):
        """获取指定币种的未实现盈亏"""
        coin_info = self.get_coin_balance(coin)
        if coin_info:
            return coin_info["unrealised_pnl"]
        return 0.0

    def get_all_available_balances(self):
        """获取所有币种的可用余额"""
        return {coin["coin"]: coin["available_to_withdraw"] for coin in self.coins}

    def get_total_equity(self):
        """获取总权益"""
        return float(self.total_equity) if self.total_equity else 0.0

    def get_total_wallet_balance(self):
        """获取总钱包余额"""
        return float(self.total_wallet_balance) if self.total_wallet_balance else 0.0

    def has_balance(self, coin):
        """检查是否有指定币种的余额"""
        return self.get_available_balance(coin) > 0

    def __str__(self):
        """返回余额的字符串表示"""
        self.init_data()
        return (
            f"BybitBalance(account_type={self.account_type}, "
            f"total_equity={self.total_equity}, "
            f"available={self.total_available_balance})"
        )


class BybitSpotBalanceData(BybitBalanceData):
    """Bybit 现货余额数据"""

    def __init__(self, balance_info, has_been_json_encoded=False):
        super().__init__(balance_info, "spot", has_been_json_encoded)

    def init_data(self):
        """初始化现货余额数据"""
        return super().init_data()


class BybitSwapBalanceData(BybitBalanceData):
    """Bybit 期货/swap 余额数据"""

    def __init__(self, balance_info, has_been_json_encoded=False):
        super().__init__(balance_info, "swap", has_been_json_encoded)

    def init_data(self):
        """初始化期货余额数据"""
        return super().init_data()
