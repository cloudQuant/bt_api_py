"""
Bitget Balance Data Container
"""

import json
import time

from bt_api_py.containers.balances.balance import BalanceData
from bt_api_py.functions.utils import from_dict_get_float, from_dict_get_string


class BitgetBalanceData(BalanceData):
    """保存Bitget账户余额信息"""

    def __init__(self, balance_info, symbol_name, asset_type, has_been_json_encoded=False):
        super().__init__(balance_info, has_been_json_encoded)
        self.exchange_name = "BITGET"
        self.local_update_time = time.time()
        self.symbol_name = symbol_name
        self.asset_type = asset_type
        self.balance_data = balance_info if has_been_json_encoded else None
        self.coin = None
        self.coin_type = None
        self.available = None
        self.frozen = None
        self.stored = None
        self.value_usd = None
        self.equity = None
        self.margin_ratio = None
        self.pnl = None
        self.pnl_ratio = None
        self.has_been_init_data = False

    def init_data(self):
        if not self.has_been_json_encoded:
            self.balance_data = json.loads(self.balance_info)
            self.has_been_json_encoded = True
        if self.has_been_init_data:
            return self

        self.coin = from_dict_get_string(self.balance_data, "coin")
        self.coin_type = from_dict_get_string(self.balance_data, "coinType")
        self.available = from_dict_get_float(self.balance_data, "available")
        self.frozen = from_dict_get_float(self.balance_data, "frozen")
        self.stored = from_dict_get_float(self.balance_data, "stored")
        self.value_usd = from_dict_get_float(self.balance_data, "usdValue")
        self.equity = from_dict_get_float(self.balance_data, "eq")
        self.margin_ratio = from_dict_get_float(self.balance_data, "mr")
        self.pnl = from_dict_get_float(self.balance_data, "pnl")
        self.pnl_ratio = from_dict_get_float(self.balance_data, "pnlRatio")
        self.has_been_init_data = True
        return self

    def get_all_data(self):
        if self.all_data is None:
            self.init_data()
            self.all_data = {
                "exchange_name": self.exchange_name,
                "symbol_name": self.symbol_name,
                "asset_type": self.asset_type,
                "local_update_time": self.local_update_time,
                "coin": self.coin,
                "coin_type": self.coin_type,
                "available": self.available,
                "frozen": self.frozen,
                "stored": self.stored,
                "value_usd": self.value_usd,
                "equity": self.equity,
                "margin_ratio": self.margin_ratio,
                "pnl": self.pnl,
                "pnl_ratio": self.pnl_ratio,
            }
        return self.all_data

    def __str__(self):
        self.init_data()
        return json.dumps(self.get_all_data())

    def __repr__(self):
        return self.__str__()

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

    def get_coin_type(self):
        return self.coin_type

    def get_available(self):
        """Get available balance

        Returns:
            float: Available balance
        """
        self.init_data()
        return self.available

    def get_frozen(self):
        """Get frozen balance

        Returns:
            float: Frozen balance
        """
        self.init_data()
        return self.frozen

    def get_stored(self):
        """Get stored balance

        Returns:
            float: Stored balance
        """
        self.init_data()
        return self.stored

    def get_total(self):
        """Get total balance (available + frozen + stored)

        Returns:
            float: Total balance
        """
        self.init_data()
        return (self.available or 0) + (self.frozen or 0) + (self.stored or 0)

    def get_value_usd(self):
        """Get USD value

        Returns:
            float: USD value
        """
        self.init_data()
        return self.value_usd

    def get_equity(self):
        """Get equity

        Returns:
            float: Equity
        """
        self.init_data()
        return self.equity

    def get_margin_ratio(self):
        """Get margin ratio

        Returns:
            float: Margin ratio
        """
        self.init_data()
        return self.margin_ratio

    def get_pnl(self):
        """Get profit and loss

        Returns:
            float: PNL
        """
        self.init_data()
        return self.pnl

    def get_pnl_ratio(self):
        """Get PNL ratio

        Returns:
            float: PNL ratio
        """
        self.init_data()
        return self.pnl_ratio

    def is_empty(self):
        """Check if balance is empty

        Returns:
            bool: True if all balances are zero
        """
        self.init_data()
        total = self.get_total()
        return total == 0.0


class BitgetWssBalanceData(BitgetBalanceData):
    """Bitget WebSocket Balance Data"""

    def init_data(self):
        if not self.has_been_json_encoded:
            self.balance_data = json.loads(self.balance_info)
            self.has_been_json_encoded = True
        if self.has_been_init_data:
            return self

        self.coin = from_dict_get_string(self.balance_data, "a")  # asset
        self.coin_type = from_dict_get_string(self.balance_data, "t")  # type
        self.available = from_dict_get_float(self.balance_data, "b")  # balance
        self.frozen = from_dict_get_float(self.balance_data, "f")  # frozen
        self.stored = from_dict_get_float(self.balance_data, "s")  # stored
        self.value_usd = from_dict_get_float(self.balance_data, "v")  # value
        self.equity = from_dict_get_float(self.balance_data, "e")  # equity
        self.margin_ratio = from_dict_get_float(self.balance_data, "mr")  # margin ratio
        self.pnl = from_dict_get_float(self.balance_data, "pnl")  # pnl
        self.pnl_ratio = from_dict_get_float(self.balance_data, "pnlRatio")  # pnl ratio
        self.has_been_init_data = True
        return self


class BitgetRequestBalanceData(BitgetBalanceData):
    """Bitget REST API Balance Data"""

    def init_data(self):
        if not self.has_been_json_encoded:
            self.balance_data = json.loads(self.balance_info)
            self.has_been_json_encoded = True
        if self.has_been_init_data:
            return self

        self.coin = from_dict_get_string(self.balance_data, "coin")
        self.coin_type = from_dict_get_string(self.balance_data, "coinType")
        self.available = from_dict_get_float(self.balance_data, "available")
        self.frozen = from_dict_get_float(self.balance_data, "frozen")
        self.stored = from_dict_get_float(self.balance_data, "stored")
        self.value_usd = from_dict_get_float(self.balance_data, "usdValue")
        self.equity = from_dict_get_float(self.balance_data, "equity")
        self.margin_ratio = from_dict_get_float(self.balance_data, "marginRatio")
        self.pnl = from_dict_get_float(self.balance_data, "pnl")
        self.pnl_ratio = from_dict_get_float(self.balance_data, "pnlRatio")
        self.has_been_init_data = True
        return self


class BitgetSpotWssAccountData:
    """Bitget Spot WebSocket Account Data"""

    def __init__(self, account_info, symbol_name, asset_type, has_been_json_encoded=False):
        self.exchange_name = "BITGET"
        self.local_update_time = time.time()
        self.symbol_name = symbol_name
        self.asset_type = asset_type
        self.account_info = account_info if has_been_json_encoded else None
        self.balances = []
        self.has_been_init_data = False

    def init_data(self):
        if not self.has_been_json_encoded:
            self.account_info = json.loads(self.account_info)
            self.has_been_json_encoded = True
        if self.has_been_init_data:
            return self

        # Process balance data
        if "data" in self.account_info and "balances" in self.account_info["data"]:
            for balance in self.account_info["data"]["balances"]:
                balance_data = BitgetWssBalanceData(
                    balance, self.symbol_name, self.asset_type, True
                )
                self.balances.append(balance_data)

        self.has_been_init_data = True
        return self

    def get_balances(self):
        """Get all balances

        Returns:
            list: List of BitgetBalanceData objects
        """
        self.init_data()
        return self.balances

    def get_balance(self, coin):
        """Get balance for specific coin

        Args:
            coin: Coin symbol (e.g., "USDT", "BTC")

        Returns:
            BitgetBalanceData: Balance data or None
        """
        self.init_data()
        for balance in self.balances:
            if balance.get_coin() == coin:
                return balance
        return None

    def get_total_equity(self):
        """Get total equity

        Returns:
            float: Total equity
        """
        self.init_data()
        total = 0.0
        for balance in self.balances:
            if balance.get_equity():
                total += balance.get_equity()
        return total
