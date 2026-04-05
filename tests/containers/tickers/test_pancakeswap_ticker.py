"""Tests for PancakeSwapRequestTickerData container."""

from __future__ import annotations

from bt_api_py.containers.tickers.pancakeswap_ticker import PancakeSwapRequestTickerData


class TestPancakeSwapRequestTickerData:
    """Tests for PancakeSwapRequestTickerData."""

    def test_init(self):
        """Test initialization."""
        ticker = PancakeSwapRequestTickerData(
            symbol="CAKE-USDC",
            price=10.5,
            timestamp=1678901234,
            volume=1000.0,
            quote_volume=10500.0,
        )

        assert ticker.symbol == "CAKE-USDC"
        assert ticker.price == 10.5
        assert ticker.timestamp == 1678901234
        assert ticker.volume == 1000.0
        assert ticker.quote_volume == 10500.0

    def test_init_with_optional_fields(self):
        """Test initialization with optional fields."""
        ticker = PancakeSwapRequestTickerData(
            symbol="CAKE-USDC",
            price=10.5,
            timestamp=1678901234,
            volume=1000.0,
            quote_volume=10500.0,
            high=11.0,
            low=10.0,
            bid=10.4,
            ask=10.6,
            count=100,
        )

        assert ticker.high == 11.0
        assert ticker.low == 10.0
        assert ticker.bid == 10.4
        assert ticker.ask == 10.6
        assert ticker.count == 100

    def test_negative_price_normalized(self):
        """Test that negative price is normalized to 0."""
        ticker = PancakeSwapRequestTickerData(
            symbol="CAKE-USDC",
            price=-10.5,
            timestamp=1678901234,
            volume=1000.0,
            quote_volume=10500.0,
        )

        assert ticker.price == 0.0

    def test_str_representation(self):
        """Test __str__ method."""
        ticker = PancakeSwapRequestTickerData(
            symbol="CAKE-USDC",
            price=10.5,
            timestamp=1678901234,
            volume=1000.0,
            quote_volume=10500.0,
        )
        result = str(ticker)

        assert "CAKE-USDC" in result or "PancakeSwapRequestTickerData" in result
