"""Tests for websocket_manager module - configuration and data classes."""

import pytest

from bt_api_py.websocket_manager import Subscription, WebSocketConfig


class TestWebSocketConfig:
    """Tests for WebSocketConfig dataclass."""

    def test_valid_config(self):
        """Test valid configuration."""
        config = WebSocketConfig(
            url="wss://stream.binance.com/ws",
            exchange_name="BINANCE",
        )

        assert config.url == "wss://stream.binance.com/ws"
        assert config.exchange_name == "BINANCE"
        assert config.max_connections == 5
        assert config.heartbeat_interval == 30.0
        assert config.reconnect_interval == 5.0
        assert config.max_reconnect_attempts == 10
        assert config.message_queue_size == 10000
        assert config.compression is True

    def test_custom_config(self):
        """Test custom configuration."""
        config = WebSocketConfig(
            url="wss://ws.okx.com:8443/ws/v5/public",
            exchange_name="OKX",
            max_connections=10,
            heartbeat_interval=60.0,
            reconnect_interval=10.0,
            max_reconnect_attempts=5,
            message_queue_size=5000,
            compression=False,
        )

        assert config.max_connections == 10
        assert config.heartbeat_interval == 60.0
        assert config.compression is False

    def test_invalid_url_scheme(self):
        """Test invalid URL scheme."""
        with pytest.raises(ValueError, match="url must be a valid ws/wss URL"):
            WebSocketConfig(
                url="https://api.binance.com",
                exchange_name="BINANCE",
            )

    def test_invalid_url_no_netloc(self):
        """Test invalid URL without netloc."""
        with pytest.raises(ValueError, match="url must be a valid ws/wss URL"):
            WebSocketConfig(
                url="wss://",
                exchange_name="BINANCE",
            )

    def test_invalid_exchange_name_empty(self):
        """Test empty exchange name."""
        with pytest.raises(ValueError, match="exchange_name must be a non-empty string"):
            WebSocketConfig(
                url="wss://stream.binance.com/ws",
                exchange_name="",
            )

    def test_invalid_exchange_name_whitespace(self):
        """Test whitespace-only exchange name."""
        with pytest.raises(ValueError, match="exchange_name must be a non-empty string"):
            WebSocketConfig(
                url="wss://stream.binance.com/ws",
                exchange_name="   ",
            )

    def test_invalid_max_connections_zero(self):
        """Test zero max connections."""
        with pytest.raises(ValueError, match="max_connections must be a positive integer"):
            WebSocketConfig(
                url="wss://stream.binance.com/ws",
                exchange_name="BINANCE",
                max_connections=0,
            )

    def test_invalid_max_connections_negative(self):
        """Test negative max connections."""
        with pytest.raises(ValueError, match="max_connections must be a positive integer"):
            WebSocketConfig(
                url="wss://stream.binance.com/ws",
                exchange_name="BINANCE",
                max_connections=-1,
            )

    def test_invalid_heartbeat_interval_zero(self):
        """Test zero heartbeat interval."""
        with pytest.raises(ValueError, match="heartbeat_interval must be > 0"):
            WebSocketConfig(
                url="wss://stream.binance.com/ws",
                exchange_name="BINANCE",
                heartbeat_interval=0,
            )

    def test_invalid_heartbeat_interval_negative(self):
        """Test negative heartbeat interval."""
        with pytest.raises(ValueError, match="heartbeat_interval must be > 0"):
            WebSocketConfig(
                url="wss://stream.binance.com/ws",
                exchange_name="BINANCE",
                heartbeat_interval=-1,
            )

    def test_invalid_reconnect_interval_zero(self):
        """Test zero reconnect interval."""
        with pytest.raises(ValueError, match="reconnect_interval must be > 0"):
            WebSocketConfig(
                url="wss://stream.binance.com/ws",
                exchange_name="BINANCE",
                reconnect_interval=0,
            )

    def test_invalid_max_reconnect_attempts_negative(self):
        """Test negative max reconnect attempts."""
        with pytest.raises(ValueError, match="max_reconnect_attempts must be >= 0"):
            WebSocketConfig(
                url="wss://stream.binance.com/ws",
                exchange_name="BINANCE",
                max_reconnect_attempts=-1,
            )

    def test_zero_max_reconnect_attempts_allowed(self):
        """Test zero max reconnect attempts is allowed."""
        config = WebSocketConfig(
            url="wss://stream.binance.com/ws",
            exchange_name="BINANCE",
            max_reconnect_attempts=0,
        )

        assert config.max_reconnect_attempts == 0

    def test_invalid_message_queue_size_zero(self):
        """Test zero message queue size."""
        with pytest.raises(ValueError, match="message_queue_size must be a positive integer"):
            WebSocketConfig(
                url="wss://stream.binance.com/ws",
                exchange_name="BINANCE",
                message_queue_size=0,
            )

    def test_subscription_limits_default(self):
        """Test default subscription limits."""
        config = WebSocketConfig(
            url="wss://stream.binance.com/ws",
            exchange_name="BINANCE",
        )

        assert config.subscription_limits["ticker"] == 100
        assert config.subscription_limits["depth"] == 50
        assert config.subscription_limits["trades"] == 100
        assert config.subscription_limits["kline"] == 200

    def test_custom_subscription_limits(self):
        """Test custom subscription limits."""
        config = WebSocketConfig(
            url="wss://stream.binance.com/ws",
            exchange_name="BINANCE",
            subscription_limits={"ticker": 200, "depth": 100},
        )

        assert config.subscription_limits["ticker"] == 200
        assert config.subscription_limits["depth"] == 100


class TestSubscription:
    """Tests for Subscription dataclass."""

    def test_subscription_basic(self):
        """Test basic subscription."""
        sub = Subscription(
            id="sub-1",
            topic="ticker",
            symbol="BTCUSDT",
            params={},
        )

        assert sub.id == "sub-1"
        assert sub.topic == "ticker"
        assert sub.symbol == "BTCUSDT"
        assert sub.params == {}
        assert sub.callback is None
        assert sub.created_at > 0

    def test_subscription_with_callback(self):
        """Test subscription with callback."""

        def my_callback(msg):
            pass

        sub = Subscription(
            id="sub-2",
            topic="depth",
            symbol="ETHUSDT",
            params={"level": 5},
            callback=my_callback,
        )

        assert sub.callback == my_callback
        assert sub.params == {"level": 5}

    def test_subscription_with_params(self):
        """Test subscription with parameters."""
        sub = Subscription(
            id="sub-3",
            topic="kline",
            symbol="BTCUSDT",
            params={"interval": "1m"},
        )

        assert sub.params == {"interval": "1m"}


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
