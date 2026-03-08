from bt_api_py.containers.requestdatas.request_data import RequestData
from bt_api_py.utils.time import convert_utc_timestamp


class GeminiRequestBalanceData(RequestData):
    """Gemini Balance Data Container"""

    def __init__(
        self,
        data,
        extra_data=None,
        status=False,
        normalize_func=None,
        symbol=None,
        asset_type=None,
        is_rest=True,
    ):
        if extra_data is None:
            extra_data = {}
        # Set exchange_name and symbol_name in extra_data for RequestData
        extra_data.setdefault("exchange_name", "GEMINI")
        if asset_type:
            extra_data.setdefault("asset_type", asset_type)

        super().__init__(data, extra_data, status, normalize_func)
        self.symbol = symbol
        self.asset_type = asset_type
        self.is_rest = is_rest

        # Default values
        self.currency = None
        self.amount = None
        self.available = None
        self.available_for_withdrawal = None
        self.timestamp = None
        self.exchange_timestamp = None

        if data:
            self._parse_data(data)

    def _parse_data(self, data):
        """Parse Gemini API response"""
        if self.is_rest:
            self._parse_rest_data(data)
        else:
            self._parse_wss_data(data)

    def _parse_rest_data(self, data):
        """Parse REST API response"""
        if isinstance(data, list) and data:
            # Handle list of balances (from test data)
            self._parse_rest_data(data[0])
        elif isinstance(data, dict):
            self.currency = data.get("currency")
            self.amount = float(data.get("amount", 0))
            self.available = float(data.get("available", 0))
            self.available_for_withdrawal = float(data.get("availableForWithdrawal", 0))
            self.timestamp = data.get("timestampms")
            self.exchange_timestamp = convert_utc_timestamp(self.timestamp)

    def _parse_wss_data(self, data):
        """Parse WebSocket response"""
        # Balance updates typically come as full balance snapshots
        if isinstance(data, dict) and "balances" in data:
            balances = data.get("balances", [])
            if balances:
                # Parse the first balance update
                self._parse_rest_data(balances[0])

    # Getter methods for compatibility with tests
    def get_currency(self):
        return self.currency

    def to_dict(self):
        """Convert to dictionary"""
        return {
            "currency": self.currency,
            "amount": self.amount,
            "available": self.available,
            "available_for_withdrawal": self.available_for_withdrawal,
            "timestamp": self.timestamp,
            "exchange_timestamp": self.exchange_timestamp,
            "symbol": self.symbol,
            "asset_type": self.asset_type,
            "is_rest": self.is_rest,
        }

    def __str__(self):
        """String representation"""
        return (
            f"GeminiBalance(currency={self.currency}, amount={self.amount}, "
            f"available={self.available})"
        )


class GeminiSpotWssBalanceData(GeminiRequestBalanceData):
    """Gemini Spot WebSocket Balance Data"""

    def __init__(self, data, symbol=None, asset_type=None):
        super().__init__(data, symbol, asset_type, is_rest=False)
