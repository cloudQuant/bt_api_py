import json
import time

from bt_api_py.containers.balances.balance import BalanceData
from bt_api_py.functions.utils import from_dict_get_float


class HyperliquidSwapRequestBalanceData(BalanceData):
    """保存Hyperliquid永续合约账户余额数据"""

    def __init__(self, balance_info, symbol_name, asset_type, has_been_json_encoded=False):
        super().__init__(balance_info, has_been_json_encoded)
        self.exchange_name = "HYPERLIQUID"
        self.local_update_time = time.time()
        self.symbol_name = symbol_name
        self.asset_type = asset_type
        self.balance_data = balance_info if has_been_json_encoded else None
        self.coin = None
        self.total = None
        self.available = None
        self.hold = None
        self.initial_margin = None
        self.margin_used = None
        self.unrealized_pnl = None
        self.unrealized_pnl_ratio = None
        self.leverage = None
        self.account_value = None
        self.all_data = None
        self.has_been_init_data = False

    def init_data(self):
        """初始化余额数据"""
        if self.has_been_init_data:
            return self

        try:
            if not self.has_been_json_encoded:
                self.balance_data = json.loads(self.balance_info)

            # Process clearinghouse state for swaps
            if isinstance(self.balance_data, dict):
                if "assetPositions" in self.balance_data:
                    for position in self.balance_data["assetPositions"]:
                        if position.get("coin") == self.symbol_name:
                            pos = position.get("position", {})
                            self.coin = pos.get("coin")
                            self.total = from_dict_get_float(pos, "sz")
                            self.available = from_dict_get_float(pos, "free collateral")
                            self.hold = from_dict_get_float(pos, "margin held")

                            # PnL information
                            self.unrealized_pnl = from_dict_get_float(pos, "unrealizedPnl")

                            # Leverage information
                            if "leverage" in pos and isinstance(pos["leverage"], dict):
                                self.leverage = from_dict_get_float(pos["leverage"], "value")

                # Account summary
                if "marginSummary" in self.balance_data:
                    margin_summary = self.balance_data["marginSummary"]
                    self.account_value = from_dict_get_float(margin_summary, "accountValue")
                    self.margin_used = from_dict_get_float(margin_summary, "totalMarginUsed")
                    self.initial_margin = from_dict_get_float(margin_summary, "initialMargin")

            self.has_been_init_data = True

        except Exception as e:
            print(f"Error initializing Hyperliquid balance data: {e}")
        return self

    def get_all_data(self):
        """获取所有余额数据"""
        if self.all_data is None:
            self.all_data = {
                "exchange_name": self.exchange_name,
                "symbol_name": self.symbol_name,
                "asset_type": self.asset_type,
                "local_update_time": self.local_update_time,
                "coin": self.coin,
                "total": self.total,
                "available": self.available,
                "hold": self.hold,
                "initial_margin": self.initial_margin,
                "margin_used": self.margin_used,
                "unrealized_pnl": self.unrealized_pnl,
                "unrealized_pnl_ratio": self.unrealized_pnl_ratio,
                "leverage": self.leverage,
                "account_value": self.account_value,
            }
        return self.all_data

    def get_exchange_name(self):
        return self.exchange_name

    def get_local_update_time(self):
        return self.local_update_time

    def get_symbol_name(self):
        return self.symbol_name

    def get_asset_type(self):
        return self.asset_type

    def get_coin(self):
        return self.coin

    def get_total(self):
        return self.total

    def get_available(self):
        return self.available

    def get_hold(self):
        return self.hold

    def get_initial_margin(self):
        return self.initial_margin

    def get_margin_used(self):
        return self.margin_used

    def get_unrealized_pnl(self):
        return self.unrealized_pnl

    def get_unrealized_pnl_ratio(self):
        return self.unrealized_pnl_ratio

    def get_leverage(self):
        return self.leverage

    def get_account_value(self):
        return self.account_value

    def __str__(self):
        return f"HyperliquidSwapRequestBalanceData(coin={self.coin}, total={self.total}, available={self.available}, pnl={self.unrealized_pnl})"


class HyperliquidSpotRequestBalanceData(BalanceData):
    """保存Hyperliquid现货账户余额数据"""

    def __init__(self, balance_info, symbol_name, asset_type, has_been_json_encoded=False):
        super().__init__(balance_info, has_been_json_encoded)
        self.exchange_name = "HYPERLIQUID"
        self.local_update_time = time.time()
        self.symbol_name = symbol_name
        self.asset_type = asset_type
        self.balance_data = balance_info if has_been_json_encoded else None
        self.coin = None
        self.total = None
        self.available = None
        self.hold = None
        self.all_data = None
        self.has_been_init_data = False

    def init_data(self):
        """初始化现货余额数据"""
        if self.has_been_init_data:
            return self

        try:
            if not self.has_been_json_encoded:
                self.balance_data = json.loads(self.balance_info)

            # Process spot clearinghouse state
            if isinstance(self.balance_data, dict):
                if "balances" in self.balance_data:
                    for balance in self.balance_data["balances"]:
                        if balance.get("coin") == self.symbol_name:
                            self.coin = balance.get("coin")
                            self.total = from_dict_get_float(balance, "total")
                            self.available = from_dict_get_float(balance, "free")
                            self.hold = from_dict_get_float(balance, "hold")

            self.has_been_init_data = True

        except Exception as e:
            print(f"Error initializing Hyperliquid spot balance data: {e}")
        return self

    def get_all_data(self):
        """获取所有余额数据"""
        if self.all_data is None:
            self.all_data = {
                "exchange_name": self.exchange_name,
                "symbol_name": self.symbol_name,
                "asset_type": self.asset_type,
                "local_update_time": self.local_update_time,
                "coin": self.coin,
                "total": self.total,
                "available": self.available,
                "hold": self.hold,
            }
        return self.all_data

    # Getters (same as swap balance)
    def get_exchange_name(self):
        return self.exchange_name

    def get_local_update_time(self):
        return self.local_update_time

    def get_symbol_name(self):
        return self.symbol_name

    def get_asset_type(self):
        return self.asset_type

    def get_coin(self):
        return self.coin

    def get_total(self):
        return self.total

    def get_available(self):
        return self.available

    def get_hold(self):
        return self.hold

    def __str__(self):
        return f"HyperliquidSpotRequestBalanceData(coin={self.coin}, total={self.total}, available={self.available})"
