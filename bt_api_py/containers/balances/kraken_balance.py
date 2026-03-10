"""
Kraken Balance Data Container
Provides standardized balance data structure for Kraken exchange.
"""

import time
from typing import Any

from bt_api_py.containers.balances.balance import BalanceData
from bt_api_py.logging_factory import get_logger


class KrakenRequestBalanceData(BalanceData):
    """Kraken Request Balance Data Container"""

    def __init__(self, data: dict[str, Any], asset_type: str, has_been_json_encoded=False):
        """Initialize Kraken balance data.

        Args:
            data: Raw balance data from Kraken API
            asset_type: Asset type (e.g., 'SPOT')
            has_been_json_encoded: Whether data is already JSON encoded
        """
        super().__init__(data, has_been_json_encoded)
        # Store asset_type before parsing
        self.asset_type = asset_type
        self.logger = get_logger("kraken_balance")
        self._parse_data(data)

    def _parse_data(self, data: dict[str, Any]):
        """Parse Kraken balance data.

        Kraken balance response format:
        {
            "error": [],
            "result": {
                "XXBT": "1.0000000000",
                "ZUSD": "10000.0000",
                "XETH": "10.5000000000",
                "DASH": "2.5000000000",
                "ETH": "1.0000000000",
                "ETC": "10.0000000000",
                "XRP": "1000.0000000000",
                "REP": "5.0000000000",
                "XLM": "100.0000000000",
                "NMC": "1.0000000000",
                "LSK": "5.0000000000",
                "FCN": "10000.0000000000",
                "FCT": "1.0000000000",
                "ZEC": "1.0000000000",
                "XMR": "1.0000000000",
                "XDG": "10000.0000000000",
                "VTC": "1.0000000000",
                "DGB": "10000.0000000000",
                "SC": "10000.0000000000",
                "XBC": "0.0000000000",
                "BTG": "0.0000000000",
                "BCH": "0.0000000000",
                "BSV": "0.0000000000",
                "EUR": "0.0000000000",
                "CAD": "0.0000000000",
                "GBP": "0.0000000000",
                "JPY": "0.0000000000"
            }
        }
        """
        try:
            self.timestamp = time.time()
            self.datetime = time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(self.timestamp))
            self.exchange = "kraken"

            # Parse balance data
            result = data.get("result", {})
            if not result:
                raise ValueError("No balance data found in response")

            self.balances = {}
            total_value_usd = 0.0
            self.total_crypto_balance = 0.0

            for currency, balance_str in result.items():
                try:
                    balance = float(balance_str)
                    if balance > 0:  # Only include non-zero balances
                        # Determine balance type
                        balance_info = self._get_balance_info(currency, balance)

                        # Store balance
                        self.balances[currency] = balance_info

                        # Update totals
                        if balance_info["usd_value"] is not None:
                            total_value_usd += balance_info["usd_value"]
                        if balance_info["crypto_amount"] is not None:
                            self.total_crypto_balance += balance_info["crypto_amount"]

                except ValueError as e:
                    self.logger.warn(f"Error parsing balance for {currency}: {e}")

            self.total_value_usd = total_value_usd
            self.currency_balances = self._group_by_currency()

        except Exception as e:
            self.logger.error(f"Error parsing Kraken balance data: {e}")
            self.logger.error(f"Raw data: {data}")
            raise

    def _get_balance_info(self, currency: str, balance: float) -> dict[str, Any]:
        """Get detailed balance information for a currency."""
        # Get currency info
        currency_info = self._get_currency_info(currency)

        balance_info = {
            "currency": currency,
            "free": balance,
            "used": 0.0,  # Kraken doesn't provide separate used/free in basic balance
            "total": balance,
            "crypto_amount": balance,
            "usd_value": None,
            "eur_value": None,
            "btc_value": None,
            "is_stakable": currency_info["is_stakable"],
            "is_fiat": currency_info["is_fiat"],
            "decimal_places": currency_info["decimal_places"],
            "display_name": currency_info["display_name"],
        }

        # Calculate USD value if price is available
        if currency_info["usd_price"] and balance > 0:
            usd_value = balance * currency_info["usd_price"]
            balance_info["usd_value"] = usd_value

        # Calculate EUR value
        if currency_info["eur_price"] and balance > 0:
            eur_value = balance * currency_info["eur_price"]
            balance_info["eur_value"] = eur_value

        # Calculate BTC value
        if currency_info["btc_price"] and balance > 0:
            btc_value = balance * currency_info["btc_price"]
            balance_info["btc_value"] = btc_value

        return balance_info

    def _get_currency_info(self, currency: str) -> dict[str, Any]:
        """Get currency information and price data."""
        # Currency information (could be loaded from config or API)
        currency_data = {
            # Major cryptocurrencies
            "XXBT": {
                "display_name": "Bitcoin",
                "decimal_places": 8,
                "is_stakable": True,
                "is_fiat": False,
            },
            "XBT": {
                "display_name": "Bitcoin",
                "decimal_places": 8,
                "is_stakable": True,
                "is_fiat": False,
            },
            "ETH": {
                "display_name": "Ethereum",
                "decimal_places": 8,
                "is_stakable": True,
                "is_fiat": False,
            },
            "XRP": {
                "display_name": "Ripple",
                "decimal_places": 6,
                "is_stakable": False,
                "is_fiat": False,
            },
            "LTC": {
                "display_name": "Litecoin",
                "decimal_places": 8,
                "is_stakable": True,
                "is_fiat": False,
            },
            "DASH": {
                "display_name": "Dash",
                "decimal_places": 8,
                "is_stakable": True,
                "is_fiat": False,
            },
            "ETC": {
                "display_name": "Ethereum Classic",
                "decimal_places": 8,
                "is_stakable": True,
                "is_fiat": False,
            },
            "XMR": {
                "display_name": "Monero",
                "decimal_places": 12,
                "is_stakable": True,
                "is_fiat": False,
            },
            "ZEC": {
                "display_name": "Zcash",
                "decimal_places": 8,
                "is_stakable": True,
                "is_fiat": False,
            },
            # Stablecoins
            "USDT": {
                "display_name": "Tether USD",
                "decimal_places": 6,
                "is_stakable": False,
                "is_fiat": True,
            },
            "USDC": {
                "display_name": "USD Coin",
                "decimal_places": 6,
                "is_stakable": True,
                "is_fiat": True,
            },
            "DAI": {
                "display_name": "Dai",
                "decimal_places": 18,
                "is_stakable": False,
                "is_fiat": True,
            },
            # Fiat currencies
            "ZUSD": {
                "display_name": "US Dollar",
                "decimal_places": 4,
                "is_stakable": False,
                "is_fiat": True,
            },
            "ZEUR": {
                "display_name": "Euro",
                "decimal_places": 4,
                "is_stakable": False,
                "is_fiat": True,
            },
            "ZJPY": {
                "display_name": "Japanese Yen",
                "decimal_places": 2,
                "is_stakable": False,
                "is_fiat": True,
            },
            "ZCAD": {
                "display_name": "Canadian Dollar",
                "decimal_places": 4,
                "is_stakable": False,
                "is_fiat": True,
            },
            "ZGBP": {
                "display_name": "British Pound",
                "decimal_places": 4,
                "is_stakable": False,
                "is_fiat": True,
            },
            # Other currencies
            "REP": {
                "display_name": "Augur",
                "decimal_places": 8,
                "is_stakable": False,
                "is_fiat": False,
            },
            "NMC": {
                "display_name": "Namecoin",
                "decimal_places": 8,
                "is_stakable": False,
                "is_fiat": False,
            },
            "XLM": {
                "display_name": "Stellar",
                "decimal_places": 7,
                "is_stakable": True,
                "is_fiat": False,
            },
            "LSK": {
                "display_name": "Lisk",
                "decimal_places": 8,
                "is_stakable": False,
                "is_fiat": False,
            },
            "FCN": {
                "display_name": "FairCoin",
                "decimal_places": 8,
                "is_stakable": False,
                "is_fiat": False,
            },
            "FCT": {
                "display_name": "Factom",
                "decimal_places": 6,
                "is_stakable": False,
                "is_fiat": False,
            },
            "VTC": {
                "display_name": "Vertcoin",
                "decimal_places": 8,
                "is_stakable": False,
                "is_fiat": False,
            },
            "DGB": {
                "display_name": "Dogecoin",
                "decimal_places": 8,
                "is_stakable": False,
                "is_fiat": False,
            },
            "SC": {
                "display_name": "Siacoin",
                "decimal_places": 12,
                "is_stakable": False,
                "is_fiat": False,
            },
            "XBC": {
                "display_name": "BlackCoin",
                "decimal_places": 8,
                "is_stakable": False,
                "is_fiat": False,
            },
            "BTG": {
                "display_name": "Bitcoin Gold",
                "decimal_places": 8,
                "is_stakable": False,
                "is_fiat": False,
            },
            "BCH": {
                "display_name": "Bitcoin Cash",
                "decimal_places": 8,
                "is_stakable": True,
                "is_fiat": False,
            },
            "BSV": {
                "display_name": "Bitcoin SV",
                "decimal_places": 8,
                "is_stakable": False,
                "is_fiat": False,
            },
            "XDG": {
                "display_name": "Dogecoin",
                "decimal_places": 8,
                "is_stakable": False,
                "is_fiat": False,
            },
        }

        # Get base currency info
        info = currency_data.get(
            currency,
            {"display_name": currency, "decimal_places": 8, "is_stakable": False, "is_fiat": False},
        )

        # Get prices (these should be fetched from market data)
        # For now, using approximate values
        prices = self._get_prices()
        usd_price = prices.get(currency)
        eur_price = prices.get(f"{currency}EUR") if currency != "EUR" else None
        btc_price = prices.get(f"{currency}XBT") if currency != "XBT" else None

        return {**info, "usd_price": usd_price, "eur_price": eur_price, "btc_price": btc_price}

    def _get_prices(self) -> dict[str, float]:
        """Get currency prices (simplified - should fetch from market data)."""
        # This is a simplified version - in practice, you'd fetch from market data
        return {
            "XXBT": 45000.0,
            "XBT": 45000.0,
            "ETH": 3000.0,
            "XRP": 0.5,
            "LTC": 100.0,
            "DASH": 150.0,
            "ETC": 20.0,
            "XMR": 200.0,
            "ZEC": 50.0,
            "USDT": 1.0,
            "USDC": 1.0,
            "DAI": 1.0,
            "ZUSD": 1.0,
            "ZEUR": 1.0,
            "ZJPY": 0.0067,  # JPY to USD
            "ZCAD": 0.75,
            "ZGBP": 0.8,
            "REP": 10.0,
            "NMC": 5.0,
            "XLM": 0.1,
            "LSK": 2.0,
            "FCN": 0.01,
            "FCT": 15.0,
            "VTC": 0.1,
            "DGB": 0.005,
            "SC": 0.001,
            "XBC": 1.0,
            "BTG": 50.0,
            "BCH": 400.0,
            "BSV": 100.0,
            "XDG": 0.0001,
        }

    def _group_by_currency(self) -> dict[str, float]:
        """Group balances by currency type."""
        grouped = {}
        for currency, info in self.balances.items():
            if info["total"] > 0:
                grouped[currency] = info["total"]
        return grouped

    def to_dict(self) -> dict[str, Any]:
        """Convert balance data to dictionary."""
        return {
            "timestamp": self.timestamp,
            "datetime": self.datetime,
            "exchange": self.exchange,
            "asset_type": self.asset_type,
            "balances": self.balances,
            "currency_balances": self.currency_balances,
            "total_value_usd": self.total_value_usd,
            "total_crypto_balance": self.total_crypto_balance,
        }

    def validate(self) -> bool:
        """Validate balance data integrity."""
        if not self.balances:
            return False

        # Check for negative balances
        for _currency, info in self.balances.items():
            if info["total"] < 0:
                return False

        # Check if total value calculation is reasonable
        return not self.total_value_usd < 0

    def get_currency_balance(self, currency: str) -> float | None:
        """Get balance for a specific currency."""
        if currency in self.balances:
            return self.balances[currency]["total"]
        return None

    def get_fiat_balance(self, currency: str = "USD") -> float:
        """Get total fiat balance in specified currency."""
        total = 0.0
        for info in self.balances.values():
            if info["is_fiat"]:
                if currency == "USD" and info["usd_value"]:
                    total += info["usd_value"]
                elif currency == "EUR" and info["eur_value"]:
                    total += info["eur_value"]
            else:
                # Convert crypto to fiat
                if currency == "USD" and info["usd_value"]:
                    total += info["usd_value"]
                elif currency == "EUR" and info["eur_value"]:
                    total += info["eur_value"]
        return total

    def get_crypto_balance(self) -> float:
        """Get total cryptocurrency balance."""
        return self.total_crypto_balance

    def get_stakable_balance(self) -> dict[str, float]:
        """Get stakable cryptocurrency balances."""
        stakable = {}
        for currency, info in self.balances.items():
            if info["is_stakable"] and info["total"] > 0:
                stakable[currency] = info["total"]
        return stakable

    def get_biggest_holding(self) -> tuple | None:
        """Get currency with the largest USD value."""
        if not self.balances:
            return None

        biggest = None
        max_value = 0.0

        for currency, info in self.balances.items():
            if info["usd_value"] and info["usd_value"] > max_value:
                max_value = info["usd_value"]
                biggest = (currency, info["total"], max_value)

        return biggest

    def update_balance(self, currency: str, delta: float):
        """Update balance for a currency by delta amount."""
        if currency in self.balances:
            old_balance = self.balances[currency]["total"]
            new_balance = old_balance + delta

            if new_balance >= 0:
                self.balances[currency]["total"] = new_balance
                self.balances[currency]["free"] = new_balance
                self.balances[currency]["crypto_amount"] = new_balance

                # Recalculate USD value
                currency_info = self._get_currency_info(currency)
                if currency_info["usd_price"]:
                    self.balances[currency]["usd_value"] = new_balance * currency_info["usd_price"]

                # Update totals
                self._update_totals()

                # Update currency balances
                self.currency_balances = self._group_by_currency()
            else:
                self.logger.warn(f"Cannot update balance for {currency}: would be negative")

    def _update_totals(self):
        """Update total value calculations."""
        self.total_value_usd = sum(info["usd_value"] or 0 for info in self.balances.values())
        self.total_crypto_balance = sum(
            info["crypto_amount"] or 0 for info in self.balances.values()
        )

    def __str__(self) -> str:
        """String representation of balance."""
        return f"KrakenBalance({self.total_value_usd:.2f} USD, {len(self.balances)} currencies)"

    def __repr__(self) -> str:
        """Detailed string representation."""
        return (
            f"KrakenRequestBalanceData(timestamp={self.timestamp}, "
            f"total_value_usd={self.total_value_usd}, "
            f"currency_count={len(self.balances)})"
        )


class KrakenSpotWssBalanceData(BalanceData):
    """Kraken Spot WebSocket Balance Data Container"""

    def __init__(self, data: dict[str, Any], asset_type: str, has_been_json_encoded=False):
        """Initialize Kraken WebSocket balance data.

        Args:
            data: Raw balance data from Kraken API
            asset_type: Asset type (e.g., 'SPOT')
            has_been_json_encoded: Whether data is already JSON encoded
        """
        super().__init__(data, has_been_json_encoded)
        # Store asset_type before parsing
        self.asset_type = asset_type
        self.logger = get_logger("kraken_wss_balance")
        self._parse_wss_data(data)

    def _parse_wss_data(self, data: dict[str, Any]):
        """Parse WebSocket balance data.

        For individual balance entries from a dict like {"XXBT": "0.5", "XETH": "5.0"},
        this stores the first currency found. For structured data with explicit currency,
        it uses that.
        """
        # Handle both dict format (multiple currencies) and single currency format
        if isinstance(data, dict) and "currency" in data:
            # Structured format with explicit currency
            self.currency = data.get("currency")
            self.free = float(data.get("free", 0))
            self.used = float(data.get("used", 0))
            self.total = self.free + self.used
            self.timestamp = data.get("time", time.time())
        elif isinstance(data, dict):
            # Dict format like {"XXBT": "0.5", "XETH": "5.0"}
            # Use the first non-zero balance currency
            self.currency = None
            self.free = 0.0
            self.used = 0.0
            self.total = 0.0
            for curr, amount in data.items():
                try:
                    amt = float(amount)
                    if amt > 0 and self.currency is None:
                        self.currency = curr
                        self.free = amt
                        self.total = amt
                        break
                except (ValueError, TypeError):
                    continue
            # If no currency found, use the first key
            if self.currency is None and data:
                self.currency = list(data.keys())[0]
            self.timestamp = time.time()
        else:
            # Fallback
            self.currency = "UNKNOWN"
            self.free = 0.0
            self.used = 0.0
            self.total = 0.0
            self.timestamp = time.time()

        self.datetime = time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(self.timestamp))
        self.exchange = "kraken"

    def to_dict(self) -> dict[str, Any]:
        """Convert WebSocket balance data to dictionary."""
        return {
            "currency": self.currency,
            "free": self.free,
            "used": self.used,
            "total": self.total,
            "timestamp": self.timestamp,
            "datetime": self.datetime,
            "exchange": self.exchange,
            "asset_type": self.asset_type,
        }
