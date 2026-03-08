from bt_api_py.containers.requestdatas.request_data import RequestData
from bt_api_py.utils.time import convert_utc_timestamp


class GeminiRequestTradeData(RequestData):
    """Gemini Trade Data Container"""

    def __init__(self, data, symbol=None, asset_type=None, is_rest=True):
        super().__init__(data)
        self.symbol = symbol
        self.asset_type = asset_type
        self.is_rest = is_rest

        # Default values
        self.trade_id = None
        self.price = None
        self.amount = None
        self.side = None
        self.timestamp = None
        self.exchange_timestamp = None
        self.type = None
        self.fee = None
        self.fee_currency = None

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
        if isinstance(data, dict):
            # Trade from order history
            if "tid" in data:
                self.trade_id = data.get("tid")
                self.price = float(data.get("price", 0))
                self.amount = float(data.get("amount", 0))
                self.side = data.get("type")  # 'buy' or 'sell'
                self.timestamp = data.get("timestampms")
                self.exchange_timestamp = convert_utc_timestamp(self.timestamp)
                self.type = "trade"  # Can be 'taker' or 'maker'
                # Fee might be in separate response

            # Trade from mytrades
            elif "trade_id" in data:
                self.trade_id = data.get("trade_id")
                self.price = float(data.get("price", 0))
                self.amount = float(data.get("amount", 0))
                self.side = data.get("side")
                self.timestamp = data.get("timestampms")
                self.exchange_timestamp = convert_utc_timestamp(self.timestamp)
                self.type = data.get("execution_type")
                self.fee = float(data.get("fee_amount", 0))
                self.fee_currency = data.get("fee_currency")

    def _parse_wss_data(self, data):
        """Parse WebSocket response"""
        if isinstance(data, dict):
            if data.get("type") == "trade":
                self.trade_id = data.get("tid")
                self.price = float(data.get("price", 0))
                self.amount = float(data.get("amount", 0))
                self.side = data.get("makerSide")  # 'buy' or 'sell'
                self.timestamp = data.get("timestamp")
                self.exchange_timestamp = convert_utc_timestamp(self.timestamp)
                self.type = "maker" if data.get("makerSide") else "taker"

    def to_dict(self):
        """Convert to dictionary"""
        return {
            "trade_id": self.trade_id,
            "price": self.price,
            "amount": self.amount,
            "side": self.side,
            "timestamp": self.timestamp,
            "exchange_timestamp": self.exchange_timestamp,
            "type": self.type,
            "fee": self.fee,
            "fee_currency": self.fee_currency,
            "symbol": self.symbol,
            "asset_type": self.asset_type,
            "is_rest": self.is_rest,
        }

    def __str__(self):
        """String representation"""
        return (
            f"GeminiTrade(id={self.trade_id}, symbol={self.symbol}, "
            f"side={self.side}, price={self.price}, amount={self.amount})"
        )


class GeminiSpotWssTradeData(GeminiRequestTradeData):
    """Gemini Spot WebSocket Trade Data"""

    def __init__(self, data, symbol=None, asset_type=None):
        super().__init__(data, symbol, asset_type, is_rest=False)
