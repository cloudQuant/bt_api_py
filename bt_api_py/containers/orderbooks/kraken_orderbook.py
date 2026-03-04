"""
Kraken Order Book Data Container
Provides standardized order book data structure for Kraken exchange.
"""

import time
from typing import Optional, Dict, Any, List, Tuple

from bt_api_py.containers.orderbooks.orderbook import OrderBookData
from bt_api_py.logging_factory import get_logger


class KrakenRequestOrderBookData(OrderBookData):
    """Kraken Request Order Book Data Container"""

    def __init__(self, data: Dict[str, Any], symbol: str, asset_type: str, has_been_json_encoded=False):
        """Initialize Kraken order book data.

        Args:
            data: Raw order book data from Kraken API
            symbol: Trading pair symbol
            asset_type: Asset type (e.g., 'SPOT')
            has_been_json_encoded: Whether data is already JSON encoded
        """
        super().__init__(data, has_been_json_encoded)
        # Store symbol and asset_type before parsing
        self.symbol = symbol
        self.asset_type = asset_type
        self.logger = get_logger("kraken_orderbook")
        self._parse_data(data)

    def init_data(self):
        """Initialize order book data from parsed data.

        This is a no-op since data is already parsed in __init__ via _parse_data.
        Kept for compatibility with the base class interface.
        """
        # Data is already parsed in __init__, just return self
        return self

    def get_exchange_name(self):
        """Get exchange name."""
        return self.exchange

    def get_local_update_time(self):
        """Get local update time."""
        return self.timestamp

    def get_symbol_name(self):
        """Get symbol name."""
        return self.symbol

    def get_asset_type(self):
        """Get asset type."""
        return self.asset_type

    def get_server_time(self):
        """Get server time."""
        return self.timestamp

    def get_bid_price_list(self):
        """Get bid price list."""
        return [bid['price'] for bid in self.bids]

    def get_ask_price_list(self):
        """Get ask price list."""
        return [ask['price'] for ask in self.asks]

    def get_bid_volume_list(self):
        """Get bid volume list."""
        return [bid['quantity'] for bid in self.bids]

    def get_ask_volume_list(self):
        """Get ask volume list."""
        return [ask['quantity'] for ask in self.asks]

    def get_bid_trade_nums(self):
        """Get bid trade numbers."""
        return None

    def get_ask_trade_nums(self):
        """Get ask trade numbers."""
        return None

    def _parse_data(self, data: Dict[str, Any]):
        """Parse Kraken order book data.

        Kraken order book response format:
        {
            "error": [],
            "result": {
                "XXBTZUSD": {
                    "asks": [
                        ["50000.00000", "1.500", 1688671955],
                        ["50001.00000", "2.300", 1688671956]
                    ],
                    "bids": [
                        ["49999.00000", "2.100", 1688671955],
                        ["49998.00000", "3.200", 1688671954]
                    ]
                },
                "last": 1688671956
            }
        }
        """
        try:
            # Extract order book data
            result = data.get('result', {})
            # Try to get orderbook by symbol key first
            book_data = result.get(self.symbol, {})
            # If not found, try to get the first orderbook in result (for test data)
            if not book_data and result:
                # Get the first available orderbook
                first_key = next(iter(result.keys()))
                book_data = result.get(first_key, {})

            # Basic info
            self.symbol = data.get('symbol', self.symbol)
            self.exchange = data.get('exchange', 'kraken')
            self.timestamp = data.get('timestamp', time.time())
            self.datetime = time.strftime('%Y-%m-%d %H:%M:%S', time.localtime(self.timestamp))

            # Parse asks
            self.asks = []
            for ask in book_data.get('asks', []):
                if len(ask) >= 2:
                    price = float(ask[0])
                    quantity = float(ask[1])
                    timestamp = ask[2] if len(ask) > 2 else self.timestamp
                    self.asks.append({
                        'price': price,
                        'quantity': quantity,
                        'timestamp': timestamp
                    })

            # Parse bids
            self.bids = []
            for bid in book_data.get('bids', []):
                if len(bid) >= 2:
                    price = float(bid[0])
                    quantity = float(bid[1])
                    timestamp = bid[2] if len(bid) > 2 else self.timestamp
                    self.bids.append({
                        'price': price,
                        'quantity': quantity,
                        'timestamp': timestamp
                    })

            # Sort order book levels
            self.asks.sort(key=lambda x: x['price'])
            self.bids.sort(key=lambda x: x['price'], reverse=True)

            # Calculate order book stats
            self._calculate_stats()

        except Exception as e:
            self.logger.error(f"Error parsing Kraken order book data: {e}")
            self.logger.error(f"Raw data: {data}")
            raise

    @property
    def symbol_name(self):
        """Alias for symbol for compatibility with tests."""
        return self.symbol

    def _calculate_stats(self):
        """Calculate order book statistics."""
        # Best bid and ask
        self.best_bid = self.bids[0]['price'] if self.bids else None
        self.best_ask = self.asks[0]['price'] if self.asks else None

        # Mid price
        if self.best_bid and self.best_ask:
            self.mid_price = (self.best_bid + self.best_ask) / 2
        else:
            self.mid_price = None

        # Spread
        if self.best_bid and self.best_ask:
            self.spread = self.best_ask - self.best_bid
            self.spread_percentage = (self.spread / self.best_bid) * 100 if self.best_bid else None
        else:
            self.spread = None
            self.spread_percentage = None

        # Total volume
        self.total_volume_bid = sum(level['quantity'] for level in self.bids)
        self.total_volume_ask = sum(level['quantity'] for level in self.asks)

        # Bid/ask depth at levels
        self.bid_depth_10 = sum(level['quantity'] for level in self.bids[:10])
        self.ask_depth_10 = sum(level['quantity'] for level in self.asks[:10])

        # Price weighted depth
        self.bid_weighted_depth = sum(level['price'] * level['quantity'] for level in self.bids)
        self.ask_weighted_depth = sum(level['price'] * level['quantity'] for level in self.asks)

    def get_levels(self, depth: int = 10, side: str = 'both') -> Dict[str, List[Dict]]:
        """Get order book levels up to specified depth.

        Args:
            depth: Number of levels to return
            side: 'bid', 'ask', or 'both'

        Returns:
            Dictionary with bid and/or ask levels
        """
        levels = {}

        if side in ['bid', 'both']:
            levels['bids'] = self.bids[:depth]

        if side in ['ask', 'both']:
            levels['asks'] = self.asks[:depth]

        return levels

    def get_total_depth(self, side: str, price_range: Optional[Tuple[float, float]] = None) -> float:
        """Get total depth for a side within optional price range.

        Args:
            side: 'bid' or 'ask'
            price_range: Optional (min_price, max_price) range

        Returns:
            Total quantity in the specified range
        """
        levels = self.bids if side == 'bid' else self.asks
        total = 0.0

        for level in levels:
            if price_range:
                min_price, max_price = price_range
                if not (min_price <= level['price'] <= max_price):
                    continue
            total += level['quantity']

        return total

    def get_price_impact(self, side: str, volume: float) -> Dict[str, Any]:
        """Estimate price impact for given volume.

        Args:
            side: 'buy' or 'sell'
            volume: Volume to trade

        Returns:
            Dictionary with estimated price and slippage
        """
        levels = self.bids if side == 'buy' else self.asks
        cumulative_volume = 0.0
        estimated_price = None
        slippage = 0.0

        for level in levels:
            cumulative_volume += level['quantity']
            if cumulative_volume >= volume:
                estimated_price = level['price']
                break

        if estimated_price and side == 'buy' and self.best_ask:
            slippage = estimated_price - self.best_ask
        elif estimated_price and side == 'sell' and self.best_bid:
            slippage = self.best_bid - estimated_price

        return {
            'estimated_price': estimated_price,
            'slippage': slippage,
            'slippage_percentage': (slippage / (estimated_price or 1)) * 100
        }

    def to_dict(self) -> Dict[str, Any]:
        """Convert order book data to dictionary."""
        return {
            'symbol': self.symbol,
            'exchange': self.exchange,
            'timestamp': self.timestamp,
            'datetime': self.datetime,
            'bids': self.bids,
            'asks': self.asks,
            'best_bid': self.best_bid,
            'best_ask': self.best_ask,
            'mid_price': self.mid_price,
            'spread': self.spread,
            'spread_percentage': self.spread_percentage,
            'total_volume_bid': self.total_volume_bid,
            'total_volume_ask': self.total_volume_ask,
            'bid_depth_10': self.bid_depth_10,
            'ask_depth_10': self.ask_depth_10,
            'bid_weighted_depth': self.bid_weighted_depth,
            'ask_weighted_depth': self.ask_weighted_depth,
            'asset_type': self.asset_type
        }

    def validate(self) -> bool:
        """Validate order book data integrity."""
        if not self.symbol:
            return False

        # Check for consistent bid-ask relationship
        if self.bids and self.asks:
            if self.bids[0]['price'] > self.asks[0]['price']:
                return False

        # Check for negative quantities
        for level in self.bids + self.asks:
            if level['quantity'] < 0:
                return False

        return True

    def update_from_delta(self, delta_bids: List[Dict], delta_asks: List[Dict], timestamp: Optional[float] = None):
        """Update order book with delta changes.

        Args:
            delta_bids: List of bid level changes
            delta_asks: List of ask level changes
            timestamp: Optional timestamp for the update
        """
        if timestamp is None:
            timestamp = time.time()

        # Update bids
        for delta in delta_bids:
            price = delta['price']
            quantity = delta['quantity']

            # Find and update existing level or add new one
            found = False
            for i, bid in enumerate(self.bids):
                if bid['price'] == price:
                    if quantity > 0:
                        self.bids[i]['quantity'] = quantity
                        self.bids[i]['timestamp'] = timestamp
                    else:
                        self.bids.pop(i)
                    found = True
                    break

            if quantity > 0 and not found:
                self.bids.append({
                    'price': price,
                    'quantity': quantity,
                    'timestamp': timestamp
                })

        # Update asks
        for delta in delta_asks:
            price = delta['price']
            quantity = delta['quantity']

            # Find and update existing level or add new one
            found = False
            for i, ask in enumerate(self.asks):
                if ask['price'] == price:
                    if quantity > 0:
                        self.asks[i]['quantity'] = quantity
                        self.asks[i]['timestamp'] = timestamp
                    else:
                        self.asks.pop(i)
                    found = True
                    break

            if quantity > 0 and not found:
                self.asks.append({
                    'price': price,
                    'quantity': quantity,
                    'timestamp': timestamp
                })

        # Re-sort and recalculate stats
        self.bids.sort(key=lambda x: x['price'])
        self.asks.sort(key=lambda x: x['price'])
        self._calculate_stats()

    def get_liquidation_price(self, side: str, position_size: float, leverage: float = 1.0) -> Optional[float]:
        """Estimate liquidation price for a position.

        Args:
            side: 'long' or 'short'
            position_size: Size of the position
            leverage: Leverage used

        Returns:
            Estimated liquidation price
        """
        if side == 'long':
            # For long positions, liquidation is when price hits stop-loss or reaches bid-ask boundary
            if not self.bids:
                return None
            # Simple estimation: use best bid as reference
            return self.bids[0]['price'] * (1 - 0.01 * leverage)  # 1% buffer per leverage
        else:
            # For short positions
            if not self.asks:
                return None
            return self.asks[0]['price'] * (1 + 0.01 * leverage)  # 1% buffer per leverage

    def get_vwap(self, side: str, volume: float) -> Optional[float]:
        """Calculate Volume Weighted Average Price for given volume.

        Args:
            side: 'buy' or 'sell'
            volume: Volume to calculate VWAP for

        Returns:
            VWAP price
        """
        levels = self.bids if side == 'buy' else self.asks
        cumulative_volume = 0.0
        cumulative_value = 0.0

        for level in levels:
            level_volume = min(level['quantity'], volume - cumulative_volume)
            cumulative_volume += level_volume
            cumulative_value += level_volume * level['price']

            if cumulative_volume >= volume:
                break

        if cumulative_volume > 0:
            return cumulative_value / cumulative_volume
        return None

    def __str__(self) -> str:
        """String representation of order book."""
        bid_str = f"{self.best_bid}" if self.best_bid else "N/A"
        ask_str = f"{self.best_ask}" if self.best_ask else "N/A"
        return (f"KrakenOrderBook({self.symbol}: BID:{bid_str} ASK:{ask_str} "
                f"Spread:{self.spread:.4f} Levels:{len(self.bids)}b/{len(self.asks)}a)")

    def __repr__(self) -> str:
        """Detailed string representation."""
        return (f"KrakenRequestOrderBookData(symbol='{self.symbol}', "
                f"bid_levels={len(self.bids)}, ask_levels={len(self.asks)}, "
                f"best_bid={self.best_bid}, best_ask={self.best_ask})")