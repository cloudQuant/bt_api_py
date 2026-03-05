"""
Kraken Ticker Data Container
Provides standardized ticker data structure for Kraken exchange.
"""

import time
from typing import Optional, Dict, Any

from bt_api_py.containers.tickers.ticker import TickerData
from bt_api_py.logging_factory import get_logger


class KrakenRequestTickerData(TickerData):
    """Kraken Request Ticker Data Container"""

    def __init__(self, data: Dict[str, Any], symbol: str, asset_type: str, has_been_json_encoded=False):
        """Initialize Kraken ticker data.

        Args:
            data: Raw ticker data from Kraken API
            symbol: Trading pair symbol
            asset_type: Asset type (e.g., 'SPOT')
            has_been_json_encoded: Whether data is already JSON encoded
        """
        super().__init__(data, has_been_json_encoded)
        # Store symbol and asset_type before parsing
        self.symbol = symbol
        self.asset_type = asset_type
        self.logger = get_logger("kraken_ticker")
        self._parse_data(data)

    def _parse_data(self, data: Dict[str, Any]):
        """Parse Kraken ticker data.

        Kraken ticker response format:
        {
            "result": {
                "XBTUSD": {
                    "a": ["50000.00000", "1", "1.000"],  # ask [price, whole lot volume, lot volume]
                    "b": ["49999.00000", "2", "2.000"],  # bid [price, whole lot volume, lot volume]
                    "c": ["50000.00000", "0.00100000"],  # last trade [price, lot volume]
                    "v": ["1234.56789012", "5678.90123456"],  # volume [today, last 24 hours]
                    "p": ["49500.00000", "49800.00000"],  # volume weighted average price [today, last 24 hours]
                    "t": [1000, 5000],  # number of trades [today, last 24 hours]
                    "l": ["49000.00000", "48500.00000"],  # low [today, last 24 hours]
                    "h": ["51000.00000", "51500.00000"],  # high [today, last 24 hours]
                    "o": "49500.00000"  # today's opening price
                }
            },
            "error": []
        }
        """
        try:
            # Extract ticker data
            result = data.get('result', {})
            # Try to get ticker by symbol key first
            ticker = result.get(self.symbol, {})
            # If not found, try to get the first ticker in result (for test data)
            if not ticker and result:
                # Get the first available ticker
                first_key = next(iter(result.keys()))
                ticker = result.get(first_key, {})

            # Basic price information
            self.symbol = data.get('symbol', self.symbol)
            self.exchange = data.get('exchange', 'kraken')
            self.timestamp = data.get('timestamp', time.time())
            self.datetime = time.strftime('%Y-%m-%d %H:%M:%S', time.localtime(self.timestamp))

            # Price data
            ask_data = ticker.get('a', [None, None, None])
            bid_data = ticker.get('b', [None, None, None])
            last_data = ticker.get('c', [None, None])

            self.ask_price = float(ask_data[0]) if ask_data[0] else None
            self.ask_quantity = float(ask_data[1]) if len(ask_data) > 1 and ask_data[1] else None
            self.bid_price = float(bid_data[0]) if bid_data[0] else None
            self.bid_quantity = float(bid_data[1]) if len(bid_data) > 1 and bid_data[1] else None
            self.last_price = float(last_data[0]) if last_data[0] else None
            self.last_quantity = float(last_data[1]) if len(last_data) > 1 and last_data[1] else None

            # Volume data
            volume_data = ticker.get('v', [None, None])
            self.volume_1d = float(volume_data[0]) if volume_data[0] else None
            self.volume_24h = float(volume_data[1]) if volume_data[1] else None

            # VWAP data
            vwap_data = ticker.get('p', [None, None])
            self.vwap_1d = float(vwap_data[0]) if vwap_data[0] else None
            self.vwap_24h = float(vwap_data[1]) if vwap_data[1] else None

            # Trade count
            trade_data = ticker.get('t', [None, None])
            self.trades_1d = int(trade_data[0]) if trade_data[0] else None
            self.trades_24h = int(trade_data[1]) if trade_data[1] else None

            # High/Low prices
            high_data = ticker.get('h', [None, None])
            self.high_1d = float(high_data[0]) if high_data[0] else None
            self.high_24h = float(high_data[1]) if high_data[1] else None

            low_data = ticker.get('l', [None, None])
            self.low_1d = float(low_data[0]) if low_data[0] else None
            self.low_24h = float(low_data[1]) if low_data[1] else None

            # Opening price
            self.open_price = float(ticker.get('o')) if ticker.get('o') else None

            # Calculate spread
            if self.ask_price and self.bid_price:
                self.spread = self.ask_price - self.bid_price
                self.spread_percentage = (self.spread / self.bid_price) * 100 if self.bid_price else None
            else:
                self.spread = None
                self.spread_percentage = None

            # Calculate price change
            if self.last_price and self.open_price:
                self.price_change = self.last_price - self.open_price
                self.price_change_percentage = ((self.last_price - self.open_price) / self.open_price) * 100
            else:
                self.price_change = None
                self.price_change_percentage = None

            # Additional Kraken-specific fields
            self.wholesale_market_data = data.get('wholesale_market_data', {})
            self.error = data.get('error', [])

        except Exception as e:
            self.logger.error(f"Error parsing Kraken ticker data: {e}")
            self.logger.error(f"Raw data: {data}")
            raise

    def to_dict(self) -> Dict[str, Any]:
        """Convert ticker data to dictionary."""
        return {
            'symbol': self.symbol,
            'exchange': self.exchange,
            'timestamp': self.timestamp,
            'datetime': self.datetime,
            'ask_price': self.ask_price,
            'ask_quantity': self.ask_quantity,
            'bid_price': self.bid_price,
            'bid_quantity': self.bid_quantity,
            'last_price': self.last_price,
            'last_quantity': self.last_quantity,
            'volume_1d': self.volume_1d,
            'volume_24h': self.volume_24h,
            'vwap_1d': self.vwap_1d,
            'vwap_24h': self.vwap_24h,
            'trades_1d': self.trades_1d,
            'trades_24h': self.trades_24h,
            'high_1d': self.high_1d,
            'high_24h': self.high_24h,
            'low_1d': self.low_1d,
            'low_24h': self.low_24h,
            'open_price': self.open_price,
            'spread': self.spread,
            'spread_percentage': self.spread_percentage,
            'price_change': self.price_change,
            'price_change_percentage': self.price_change_percentage,
            'wholesale_market_data': self.wholesale_market_data,
            'error': self.error,
            'asset_type': self.asset_type
        }

    # Base class interface methods
    def init_data(self):
        """Initialize ticker data from parsed data.

        This is a no-op since data is already parsed in __init__ via _parse_data.
        Kept for compatibility with the base class interface.
        """
        # Data is already parsed in __init__, just return self
        return self

    def get_all_data(self):
        """Get all ticker data."""
        return self.to_dict()

    def get_exchange_name(self):
        """Get exchange name."""
        return self.exchange

    def get_local_update_time(self):
        """Get local update time."""
        return self.timestamp

    def get_symbol_name(self):
        """Get symbol name."""
        return self.symbol

    def get_ticker_symbol_name(self):
        """Get ticker symbol name."""
        return self.symbol

    def get_asset_type(self):
        """Get asset type."""
        return self.asset_type

    def get_server_time(self):
        """Get server time."""
        return self.timestamp

    def get_bid_price(self):
        """Get bid price."""
        return self.bid_price

    def get_ask_price(self):
        """Get ask price."""
        return self.ask_price

    def get_bid_volume(self):
        """Get bid volume."""
        return self.bid_quantity

    def get_ask_volume(self):
        """Get ask volume."""
        return self.ask_quantity

    def get_last_price(self):
        """Get last price."""
        return self.last_price

    def get_last_volume(self):
        """Get last volume."""
        return self.last_quantity

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
                self.logger.warn(f"Unusual price change: {self.price_change_percentage}%")

        return True

    def get_mid_price(self) -> Optional[float]:
        """Calculate mid price from bid and ask."""
        if self.bid_price and self.ask_price:
            return (self.bid_price + self.ask_price) / 2
        return None

    def get_price_impact(self, volume: float) -> Dict[str, Optional[float]]:
        """Estimate price impact for given volume.

        Args:
            volume: Volume to trade

        Returns:
            Dict with estimated bid and ask prices after impact
        """
        if not self.bid_price or not self.ask_price or not self.volume_24h:
            return {'estimated_bid': None, 'estimated_ask': None}

        # Simple price impact estimation (can be improved)
        volume_ratio = volume / self.volume_24h if self.volume_24h else 0

        # Conservative price impact estimation
        impact_factor = min(volume_ratio * 0.01, 0.1)  # Max 10% impact

        estimated_bid = self.bid_price * (1 - impact_factor)
        estimated_ask = self.ask_price * (1 + impact_factor)

        return {
            'estimated_bid': estimated_bid,
            'estimated_ask': estimated_ask,
            'impact_factor': impact_factor
        }

    def __str__(self) -> str:
        """String representation of ticker."""
        return (f"KrakenTicker({self.symbol}: {self.last_price} "
                f"Bid:{self.bid_price} Ask:{self.ask_price} "
                f"Vol24h:{self.volume_24h} Chg:{self.price_change_percentage:.2f}%)")

    def __repr__(self) -> str:
        """Detailed string representation."""
        return (f"KrakenRequestTickerData(symbol='{self.symbol}', "
                f"last_price={self.last_price}, "
                f"bid_ask=({self.bid_price}, {self.ask_price}), "
                f"volume_24h={self.volume_24h}, "
                f"timestamp={self.timestamp})")