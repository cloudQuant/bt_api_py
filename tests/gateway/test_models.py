"""Tests for gateway/models.py."""


from bt_api_py.gateway.models import GatewayTick


class TestGatewayTick:
    """Tests for GatewayTick dataclass."""

    def test_init(self):
        """Test initialization."""
        tick = GatewayTick(timestamp=1234567890.0, symbol="BTCUSDT")
        assert tick.timestamp == 1234567890.0
        assert tick.symbol == "BTCUSDT"

    def test_defaults(self):
        """Test default values."""
        tick = GatewayTick(timestamp=1234567890.0, symbol="BTCUSDT")
        assert tick.price == 0.0
        assert tick.volume == 0.0
        assert tick.direction == "buy"

    def test_to_dict(self):
        """Test to_dict method."""
        tick = GatewayTick(timestamp=1234567890.0, symbol="BTCUSDT")
        result = tick.to_dict()
        assert result["timestamp"] == 1234567890.0
        assert result["symbol"] == "BTCUSDT"
