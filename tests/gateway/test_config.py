"""Tests for gateway/config.py."""

from bt_api_py.gateway.config import GatewayConfig


class TestGatewayConfig:
    """Tests for GatewayConfig dataclass."""

    def test_init(self):
        """Test initialization."""
        config = GatewayConfig(exchange_type="binance", asset_type="spot", account_id="test")
        assert config.exchange_type == "BINANCE"
        assert config.asset_type == "SPOT"
        assert config.account_id == "test"

    def test_defaults(self):
        """Test default values."""
        config = GatewayConfig(exchange_type="binance", asset_type="spot", account_id="test")
        assert config.transport == "tcp"
