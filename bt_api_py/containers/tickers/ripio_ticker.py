"""
Ripio Ticker Data Container
Provides standardized ticker data structure for Ripio exchange.
"""

import time
from typing import Optional, Dict, Any

from bt_api_py.containers.tickers.ticker import TickerData
from bt_api_py.logging_factory import get_logger


class RipioRequestTickerData(TickerData):
    """Ripio Request Ticker Data Container"""

    def __init__(self, data: Dict[str, Any], symbol: str, asset_type: str, has_been_json_encoded=False):
        """Initialize Ripio ticker data.

        Args:
            data: Raw ticker data from Ripio API
            symbol: Trading pair symbol
            asset_type: Asset type (e.g., 'SPOT')
            has_been_json_encoded: Whether data is already JSON encoded
        """
        super().__init__(data, has_been_json_encoded)
        self.symbol_name = symbol
        self.asset_type = asset_type
        self.logger = get_logger("ripio_ticker")
        self._parse_data(data)

    def _parse_data(self, data: Dict[str, Any]):
        """Parse Ripio ticker data.

        Ripio ticker response format (typical):
        {
            "success": true,
            "data": {
                "symbol": "BTC_USDT",
                "last": "50000.00",
                "high": "51000.00",
                "low": "49000.00",
                "bid": "49999.00",
                "ask": "50001.00",
                "volume": "1000.00",
                "quote_volume": "50000000.00",
                "change": "500.00",
                "percent_change": "1.01",
                "timestamp": 1234567890
            }
        }
        """
        try:
            # Ripio wraps response in {'success': true, 'data': {...}}
            result = data.get('data', {})

            # Basic information
            self.symbol = result.get('symbol', self.symbol_name)
            self.exchange = 'ripio'
            self.timestamp = result.get('timestamp', time.time())
            self.datetime = time.strftime('%Y-%m-%d %H:%M:%S', time.localtime(self.timestamp))

            # Price data (Ripio returns strings)
            self.last_price = self._parse_float(result.get('last'))
            self.high_price = self._parse_float(result.get('high'))
            self.low_price = self._parse_float(result.get('low'))
            self.open_price = self._parse_float(result.get('open'))

            # Bid/Ask
            self.bid_price = self._parse_float(result.get('bid'))
            self.ask_price = self._parse_float(result.get('ask'))

            # Volume data
            self.volume_24h = self._parse_float(result.get('volume'))
            self.quote_volume_24h = self._parse_float(result.get('quote_volume'))

            # Price change
            self.price_change = self._parse_float(result.get('change'))
            self.price_change_percentage = self._parse_float(result.get('percent_change'))

            # Calculate spread
            if self.ask_price and self.bid_price:
                self.spread = self.ask_price - self.bid_price
                self.spread_percentage = (self.spread / self.bid_price) * 100 if self.bid_price else None
            else:
                self.spread = None
                self.spread_percentage = None

        except Exception as e:
            self.logger.error(f"Error parsing Ripio ticker data: {e}")
            self.logger.error(f"Raw data: {data}")
            raise

    def _parse_float(self, value: Any) -> Optional[float]:
        """Parse a value to float.

        Args:
            value: Value to parse (string, float, int, or None)

        Returns:
            Float value or None
        """
        if value is None:
            return None
        try:
            return float(value)
        except (ValueError, TypeError):
            return None

    def to_dict(self) -> Dict[str, Any]:
        """Convert ticker data to dictionary."""
        return {
            'symbol': self.symbol,
            'exchange': self.exchange,
            'timestamp': self.timestamp,
            'datetime': self.datetime,
            'last_price': self.last_price,
            'high_price': self.high_price,
            'low_price': self.low_price,
            'open_price': self.open_price,
            'bid_price': self.bid_price,
            'ask_price': self.ask_price,
            'spread': self.spread,
            'spread_percentage': self.spread_percentage,
            'volume_24h': self.volume_24h,
            'quote_volume_24h': self.quote_volume_24h,
            'price_change': self.price_change,
            'price_change_percentage': self.price_change_percentage,
            'asset_type': self.asset_type
        }

    def validate(self) -> bool:
        """Validate ticker data integrity."""
        if not self.symbol:
            return False

        # Check required price fields
        if not self.last_price or self.last_price <= 0:
            return False

        # Validate bid-ask spread
        if self.ask_price and self.bid_price:
            if self.ask_price < self.bid_price:
                return False

        # Validate price change
        if self.price_change is not None and self.open_price:
            if abs(self.price_change_percentage) > 100:  # Unusual percentage change
                self.logger.warning(f"Unusual price change: {self.price_change_percentage}%")

        return True

    def get_mid_price(self) -> Optional[float]:
        """Calculate mid price from bid and ask."""
        if self.bid_price and self.ask_price:
            return (self.bid_price + self.ask_price) / 2
        return None

    def __str__(self) -> str:
        """String representation of ticker."""
        return (f"RipioTicker({self.symbol}: {self.last_price} "
                f"Bid:{self.bid_price} Ask:{self.ask_price} "
                f"Vol24h:{self.volume_24h} Chg:{self.price_change_percentage:.2f}%)")

    def __repr__(self) -> str:
        """Detailed string representation."""
        return (f"RipioRequestTickerData(symbol='{self.symbol}', "
                f"last_price={self.last_price}, "
                f"bid_ask=({self.bid_price}, {self.ask_price}), "
                f"volume_24h={self.volume_24h}, "
                f"timestamp={self.timestamp})")
