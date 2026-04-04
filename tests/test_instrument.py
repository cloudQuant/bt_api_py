"""Tests for instrument module."""

import dataclasses
import pytest
from datetime import datetime, timedelta
from decimal import Decimal

from bt_api_py.containers.instrument import AssetType, Instrument


class TestAssetType:
    """Tests for AssetType enum."""

    def test_spot(self):
        """Test SPOT asset type."""
        assert AssetType.SPOT == "spot"

    def test_swap(self):
        """Test SWAP asset type."""
        assert AssetType.SWAP == "swap"

    def test_future(self):
        """Test FUTURE asset type."""
        assert AssetType.FUTURE == "future"

    def test_option(self):
        """Test OPTION asset type."""
        assert AssetType.OPTION == "option"


class TestInstrument:
    """Tests for Instrument dataclass."""

    def test_init_basic(self):
        """Test basic initialization."""
        instrument = Instrument(
            internal="BTC-USDT",
            venue="BINANCE___SPOT",
            venue_symbol="BTCUSDT",
            asset_type=AssetType.SPOT,
        )

        assert instrument.internal == "BTC-USDT"
        assert instrument.venue == "BINANCE___SPOT"
        assert instrument.venue_symbol == "BTCUSDT"
        assert instrument.asset_type == AssetType.SPOT

    def test_init_with_currencies(self):
        """Test initialization with currencies."""
        instrument = Instrument(
            internal="BTC-USDT",
            venue="BINANCE___SPOT",
            venue_symbol="BTCUSDT",
            asset_type=AssetType.SPOT,
            base_currency="BTC",
            quote_currency="USDT",
        )

        assert instrument.base_currency == "BTC"
        assert instrument.quote_currency == "USDT"

    def test_init_with_trading_params(self):
        """Test initialization with trading parameters."""
        instrument = Instrument(
            internal="BTC-USDT",
            venue="BINANCE___SPOT",
            venue_symbol="BTCUSDT",
            asset_type=AssetType.SPOT,
            tick_size=Decimal("0.01"),
            min_qty=Decimal("0.001"),
            max_qty=Decimal("1000"),
        )

        assert instrument.tick_size == Decimal("0.01")
        assert instrument.min_qty == Decimal("0.001")
        assert instrument.max_qty == Decimal("1000")

    def test_is_expired_no_expiry(self):
        """Test is_expired property with no expiry."""
        instrument = Instrument(
            internal="BTC-USDT",
            venue="BINANCE___SPOT",
            venue_symbol="BTCUSDT",
            asset_type=AssetType.SPOT,
        )

        assert instrument.is_expired is False

    def test_is_expired_future_expiry(self):
        """Test is_expired property with future expiry."""
        future_expiry = datetime.now() + timedelta(days=30)
        instrument = Instrument(
            internal="BTC-USDT",
            venue="BINANCE___FUTURE",
            venue_symbol="BTCUSDT",
            asset_type=AssetType.FUTURE,
            expiry=future_expiry,
        )

        assert instrument.is_expired is False

    def test_is_expired_past_expiry(self):
        """Test is_expired property with past expiry."""
        past_expiry = datetime.now() - timedelta(days=1)
        instrument = Instrument(
            internal="BTC-USDT",
            venue="BINANCE___FUTURE",
            venue_symbol="BTCUSDT",
            asset_type=AssetType.FUTURE,
            expiry=past_expiry,
        )

        assert instrument.is_expired is True

    def test_is_listed_active(self):
        """Test is_listed property with active status."""
        instrument = Instrument(
            internal="BTC-USDT",
            venue="BINANCE___SPOT",
            venue_symbol="BTCUSDT",
            asset_type=AssetType.SPOT,
            status="active",
        )

        assert instrument.is_listed is True

    def test_is_listed_suspended(self):
        """Test is_listed property with suspended status."""
        instrument = Instrument(
            internal="BTC-USDT",
            venue="BINANCE___SPOT",
            venue_symbol="BTCUSDT",
            asset_type=AssetType.SPOT,
            status="suspend",
        )

        assert instrument.is_listed is False

    def test_frozen(self):
        """Test that Instrument is frozen (immutable)."""
        instrument = Instrument(
            internal="BTC-USDT",
            venue="BINANCE___SPOT",
            venue_symbol="BTCUSDT",
            asset_type=AssetType.SPOT,
        )

        with pytest.raises(dataclasses.FrozenInstanceError):
            instrument.internal = "ETH-USDT"


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
