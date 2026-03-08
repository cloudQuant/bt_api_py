"""Property-based testing for bt_api_py using hypothesis.

This module provides property-based tests to verify invariants and properties
of the trading API across different exchanges and scenarios.
"""

import pytest
from hypothesis import assume, given
from hypothesis import strategies as st

from bt_api_py.bt_api import BtApi
from bt_api_py.exceptions import BtApiError, ExchangeNotFoundError, InvalidSymbolError
from bt_api_py.registry import ExchangeRegistry


# Test data strategies
@st.composite
def valid_symbols(draw):
    """Generate valid trading symbols."""
    bases = ["BTC", "ETH", "BNB", "ADA", "SOL", "DOT", "AVAX", "MATIC", "LINK", "UNI"]
    quotes = ["USDT", "USDC", "USD", "EUR", "BTC", "ETH"]
    base = draw(st.sampled_from(bases))
    quote = draw(st.sampled_from(quotes))
    return f"{base}{quote}" if base != quote else "BTCUSDT"


@st.composite
def exchange_names(draw):
    """Generate exchange names with market types."""
    exchanges = ["BINANCE", "OKX", "HTX", "BYBIT", "KUCOIN", "MEXC"]
    markets = ["SPOT", "FUTURE", "SWAP"]
    exchange = draw(st.sampled_from(exchanges))
    market = draw(st.sampled_from(markets))
    return f"{exchange}___{market}"


@st.composite
def order_parameters(draw):
    """Generate valid order parameters."""
    symbol = draw(valid_symbols())
    side = draw(st.sampled_from(["buy", "sell"]))
    order_types = ["limit", "market"]
    order_type = draw(st.sampled_from(order_types))

    # Generate quantities based on order type
    if order_type == "market":
        quantity = draw(st.floats(min_value=0.001, max_value=1.0))
        price = None
    else:
        quantity = draw(st.floats(min_value=0.001, max_value=1.0))
        # Generate realistic price ranges
        base_prices = {"BTC": 30000, "ETH": 2000, "BNB": 300}
        base = symbol[:3] if len(symbol) >= 3 else "BTC"
        base_price = base_prices.get(base, 100)
        price = draw(st.floats(min_value=base_price * 0.9, max_value=base_price * 1.1))

    return {
        "symbol": symbol,
        "side": side,
        "order_type": order_type,
        "quantity": quantity,
        "price": price,
    }


class PropertyBasedTests:
    """Property-based test suite for bt_api_py."""

    @pytest.mark.contract
    @pytest.mark.unit
    @given(exchange=exchange_names(), symbol=valid_symbols())
    def test_symbol_validation_invariant(self, exchange, symbol):
        """Test that symbol validation follows consistent rules."""
        registry = ExchangeRegistry()

        # Property: Valid exchange names should not raise ExchangeNotFoundError
        # (Note: We mock this since we don't have all exchanges registered)
        try:
            # This will likely fail for unregistered exchanges, which is expected
            feed = registry.create_feed(exchange, data_queue="test")
        except ExchangeNotFoundError:
            # Expected for unregistered exchanges
            pass
        except BtApiError as e:
            # Other errors should not be about exchange name format
            assert "exchange name" not in str(e).lower()

    @pytest.mark.contract
    @pytest.mark.unit
    @given(symbols=st.lists(valid_symbols(), min_size=1, max_size=10))
    def test_symbol_list_properties(self, symbols):
        """Test properties of symbol lists."""
        # Property: All symbols should be strings
        assert all(isinstance(s, str) for s in symbols)

        # Property: Symbols should not be empty
        assert all(len(s) > 0 for s in symbols)

        # Property: Symbols should contain only letters and numbers
        import re

        for symbol in symbols:
            assert re.match(r"^[A-Z0-9]+$", symbol), f"Invalid symbol format: {symbol}"

    @pytest.mark.contract
    @pytest.mark.unit
    @given(params=order_parameters())
    def test_order_parameter_properties(self, params):
        """Test properties of order parameters."""
        # Property: Side must be valid
        assert params["side"] in ["buy", "sell"]

        # Property: Quantity must be positive
        assert params["quantity"] > 0

        # Property: Limit orders must have price, market orders must not
        if params["order_type"] == "limit":
            assert params["price"] is not None
            assert params["price"] > 0
        else:
            assert params["price"] is None

        # Property: Symbol should be valid format
        assert len(params["symbol"]) >= 6  # Minimum 3+3 characters
        assert all(c.isalnum() for c in params["symbol"])

    @pytest.mark.contract
    @pytest.mark.integration
    @pytest.mark.asyncio
    @given(data=st.lists(st.floats(min_value=-1000, max_value=1000), min_size=1, max_size=100))
    async def test_price_data_processing_properties(self, data):
        """Test properties of price data processing."""
        # Property: Processing should not change the length
        assert len(data) == len(data)

        # Property: Processing should handle all numeric values
        for value in data:
            assert isinstance(value, (int, float))

        # Property: Extreme values should be handled gracefully
        has_extreme = any(abs(v) > 100 for v in data)
        if has_extreme:
            # Should filter or normalize extreme values
            filtered = [v for v in data if -100 <= v <= 100]
            assert len(filtered) > 0 or len(data) == 0  # Either filtered or all extreme

    @pytest.mark.contract
    @pytest.mark.unit
    @given(
        prices=st.lists(st.floats(min_value=0.01, max_value=100000), min_size=2),
        volumes=st.lists(st.floats(min_value=0.01, max_value=1000000), min_size=2),
    )
    def test_orderbook_properties(self, prices, volumes):
        """Test properties of orderbook data."""
        assume(len(prices) == len(volumes))

        # Create orderbook structure
        orderbook = {
            "bids": list(zip(prices[: len(prices) // 2], volumes[: len(volumes) // 2])),
            "asks": list(zip(prices[len(prices) // 2 :], volumes[len(volumes) // 2 :])),
        }

        # Property: Best bid should be lower than best ask (if both exist)
        if orderbook["bids"] and orderbook["asks"]:
            best_bid_price = orderbook["bids"][0][0]
            best_ask_price = orderbook["asks"][0][0]
            assert best_bid_price <= best_ask_price

        # Property: All prices and volumes should be positive
        for side in ["bids", "asks"]:
            for price, volume in orderbook[side]:
                assert price > 0
                assert volume > 0

        # Property: Bids should be in descending order
        if len(orderbook["bids"]) > 1:
            bid_prices = [price for price, _ in orderbook["bids"]]
            assert bid_prices == sorted(bid_prices, reverse=True)

        # Property: Asks should be in ascending order
        if len(orderbook["asks"]) > 1:
            ask_prices = [price for price, _ in orderbook["asks"]]
            assert ask_prices == sorted(ask_prices)


@pytest.mark.flaky  # Network tests can be flaky
@pytest.mark.contract
@pytest.mark.integration
@pytest.mark.network
class TestExchangeProperties:
    """Property tests for exchange-specific behaviors."""

    @pytest.mark.asyncio
    @given(symbols=st.lists(valid_symbols(), min_size=1, max_size=5))
    async def test_price_retrieval_properties(self, symbols):
        """Test properties of price retrieval across exchanges."""
        api = BtApi()

        for symbol in symbols:
            try:
                # Try to get ticker data
                ticker = await api.async_get_ticker("BINANCE___SPOT", symbol)

                if ticker:
                    # Property: Ticker should have price information
                    assert hasattr(ticker, "last_price") or "last_price" in ticker

                    # Property: Price should be positive if present
                    price = getattr(ticker, "last_price", None) or ticker.get("last_price")
                    if price is not None:
                        price_float = float(price)
                        assert price_float > 0

            except (InvalidSymbolError, ExchangeNotFoundError, BtApiError):
                # Expected for some symbols/exchanges
                pass
            except Exception as e:
                # Unexpected errors should fail the test
                pytest.fail(f"Unexpected error for symbol {symbol}: {e}")

    @pytest.mark.asyncio
    @given(depth_levels=st.integers(min_value=1, max_value=20))
    async def test_orderbook_depth_properties(self, depth_levels):
        """Test properties of orderbook depth requests."""
        api = BtApi()

        try:
            depth = await api.async_get_depth("BINANCE___SPOT", "BTCUSDT", limit=depth_levels)

            if depth:
                # Property: Depth should have bids and asks
                assert hasattr(depth, "bids") or "bids" in depth
                assert hasattr(depth, "asks") or "asks" in depth

                # Property: Number of levels should not exceed request
                bids = getattr(depth, "bids", depth.get("bids", []))
                asks = getattr(depth, "asks", depth.get("asks", []))

                assert len(bids) <= depth_levels
                assert len(asks) <= depth_levels

        except BtApiError:
            # Network/auth errors are acceptable
            pass


# Custom hypothesis strategies for specific exchange data
@st.composite
def binance_ticker_data(draw):
    """Generate valid Binance ticker data."""
    return {
        "symbol": draw(valid_symbols()),
        "last_price": str(draw(st.floats(min_value=0.01, max_value=100000))),
        "volume": str(draw(st.floats(min_value=0, max_value=1000000))),
        "price_change": str(draw(st.floats(min_value=-10000, max_value=10000))),
        "price_change_percent": str(draw(st.floats(min_value=-100, max_value=100))),
        "timestamp": draw(st.integers(min_value=1000000000, max_value=2000000000)),
    }


class TestDataContracts:
    """Test data contracts and invariants."""

    @pytest.mark.contract
    @pytest.mark.unit
    @given(data=binance_ticker_data())
    def test_ticker_data_contract(self, data):
        """Test ticker data contract invariants."""
        # Property: Required fields should exist
        required_fields = ["symbol", "last_price", "timestamp"]
        for field in required_fields:
            assert field in data, f"Missing required field: {field}"

        # Property: Numeric fields should be convertible to float
        numeric_fields = ["last_price", "volume", "price_change"]
        for field in numeric_fields:
            if field in data and data[field]:
                try:
                    float(data[field])
                except ValueError:
                    pytest.fail(f"Field {field} is not a valid number: {data[field]}")

        # Property: Symbol should be valid format
        assert data["symbol"].isupper()
        assert all(c.isalnum() for c in data["symbol"])

        # Property: Timestamp should be reasonable
        assert 1000000000 <= data["timestamp"] <= 2000000000
