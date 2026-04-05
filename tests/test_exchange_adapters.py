"""Tests for exchange_adapters module - pure local logic."""

from __future__ import annotations

import pytest

from bt_api_py.exceptions import RateLimitError
from bt_api_py.websocket.exchange_adapters import (
    AuthenticationType,
    BinanceWebSocketAdapter,
    ExchangeCredentials,
    ExchangeType,
    GenericWebSocketAdapter,
    OKXWebSocketAdapter,
    RateLimitConfig,
    WebSocketAdapterFactory,
)


class TestExchangeType:
    """Tests for ExchangeType enum."""

    def test_enum_values(self):
        """Test all enum values exist."""
        assert ExchangeType.SPOT.value == "spot"
        assert ExchangeType.FUTURES.value == "futures"
        assert ExchangeType.SWAP.value == "swap"
        assert ExchangeType.OPTIONS.value == "options"


class TestAuthenticationType:
    """Tests for AuthenticationType enum."""

    def test_enum_values(self):
        """Test all enum values exist."""
        assert AuthenticationType.NONE.value == "none"
        assert AuthenticationType.API_KEY.value == "api_key"
        assert AuthenticationType.API_KEY_SECRET.value == "api_key_secret"
        assert AuthenticationType.JWT.value == "jwt"
        assert AuthenticationType.CUSTOM.value == "custom"


class TestExchangeCredentials:
    """Tests for ExchangeCredentials dataclass."""

    def test_init_defaults(self):
        """Test default initialization."""
        creds = ExchangeCredentials(exchange_name="test", auth_type=AuthenticationType.API_KEY)

        assert creds.exchange_name == "test"
        assert creds.auth_type == AuthenticationType.API_KEY
        assert creds.api_key is None
        assert creds.api_secret is None
        assert creds.passphrase is None
        assert creds.jwt_token is None
        assert creds.custom_params == {}

    def test_init_full(self):
        """Test full initialization."""
        creds = ExchangeCredentials(
            exchange_name="okx",
            auth_type=AuthenticationType.API_KEY_SECRET,
            api_key="my_key",
            api_secret="my_secret",
            passphrase="mypass",
            custom_params={"extra": "value"},
        )

        assert creds.exchange_name == "okx"
        assert creds.api_key == "my_key"
        assert creds.api_secret == "my_secret"
        assert creds.passphrase == "mypass"
        assert creds.custom_params == {"extra": "value"}

    def test_post_init_sets_custom_params(self):
        """Test __post_init__ sets empty dict for custom_params."""
        # Pass None explicitly
        creds = ExchangeCredentials(
            exchange_name="test",
            auth_type=AuthenticationType.NONE,
            custom_params=None,
        )

        assert creds.custom_params == {}


class TestRateLimitConfig:
    """Tests for RateLimitConfig dataclass."""

    def test_init_defaults(self):
        """Test default initialization."""
        config = RateLimitConfig()

        assert config.max_connections_per_ip == 5
        assert config.max_subscriptions_per_connection == 50
        assert config.messages_per_second_limit == 10
        assert config.reconnect_delay_seconds == 1.0
        assert config.requests_per_second == 10
        assert config.requests_per_minute == 600
        assert config.exchange_specific_limits == {}

    def test_init_custom(self):
        """Test custom initialization."""
        config = RateLimitConfig(
            max_connections_per_ip=10,
            max_subscriptions_per_connection=100,
            exchange_specific_limits={"special": 50},
        )

        assert config.max_connections_per_ip == 10
        assert config.max_subscriptions_per_connection == 100
        assert config.exchange_specific_limits == {"special": 50}

    def test_post_init_sets_exchange_specific_limits(self):
        """Test __post_init__ sets empty dict."""
        config = RateLimitConfig(exchange_specific_limits=None)

        assert config.exchange_specific_limits == {}


class TestBinanceWebSocketAdapter:
    """Tests for BinanceWebSocketAdapter class."""

    def test_init_defaults(self):
        """Test default initialization."""
        adapter = BinanceWebSocketAdapter()

        assert adapter.exchange_name == "BINANCE"
        assert adapter.exchange_type == ExchangeType.SPOT
        assert adapter.credentials is None

    def test_init_with_credentials(self):
        """Test initialization with credentials."""
        creds = ExchangeCredentials(
            exchange_name="BINANCE",
            auth_type=AuthenticationType.API_KEY_SECRET,
            api_key="key",
            api_secret="secret",
        )
        adapter = BinanceWebSocketAdapter(credentials=creds)

        assert adapter.credentials is creds

    def test_init_futures(self):
        """Test initialization for futures."""
        adapter = BinanceWebSocketAdapter(exchange_type=ExchangeType.FUTURES)

        assert adapter.exchange_type == ExchangeType.FUTURES

    def test_get_endpoints_spot(self):
        """Test endpoints for spot."""
        adapter = BinanceWebSocketAdapter(exchange_type=ExchangeType.SPOT)
        endpoints = adapter.get_endpoints("wss://test.com")

        assert "wss://stream.binance.com:9443" in endpoints
        assert len(endpoints) == 4

    def test_get_endpoints_futures(self):
        """Test endpoints for futures."""
        adapter = BinanceWebSocketAdapter(exchange_type=ExchangeType.FUTURES)
        endpoints = adapter.get_endpoints("wss://test.com")

        assert "wss://fstream.binance.com" in endpoints
        assert len(endpoints) == 4

    def test_get_endpoints_swap(self):
        """Test endpoints for swap."""
        adapter = BinanceWebSocketAdapter(exchange_type=ExchangeType.SWAP)
        endpoints = adapter.get_endpoints("wss://test.com")

        assert "wss://dstream.binance.com" in endpoints

    def test_format_subscription_message(self):
        """Test subscription message formatting."""
        adapter = BinanceWebSocketAdapter()

        msg = adapter.format_subscription_message("sub-1", "ticker", "BTCUSDT", {})

        assert msg["method"] == "SUBSCRIBE"
        assert "btcusdt@ticker" in msg["params"]
        assert "id" in msg

    def test_format_subscription_message_depth(self):
        """Test depth subscription message."""
        adapter = BinanceWebSocketAdapter()

        msg = adapter.format_subscription_message("sub-1", "depth", "BTCUSDT", {"level": "5"})

        assert "btcusdt@depth5" in msg["params"]

    def test_format_subscription_message_kline(self):
        """Test kline subscription message."""
        adapter = BinanceWebSocketAdapter()

        msg = adapter.format_subscription_message("sub-1", "kline", "BTCUSDT", {"interval": "5m"})

        assert "btcusdt@kline_5m" in msg["params"]

    def test_format_unsubscription_message(self):
        """Test unsubscription message formatting."""
        adapter = BinanceWebSocketAdapter()

        msg = adapter.format_unsubscription_message("sub-1", "ticker", "BTCUSDT")

        assert msg["method"] == "UNSUBSCRIBE"
        assert "btcusdt@ticker" in msg["params"]

    def test_extract_topic_symbol(self):
        """Test topic/symbol extraction."""
        adapter = BinanceWebSocketAdapter()

        topic, symbol = adapter.extract_topic_symbol({"stream": "btcusdt@ticker"})

        assert topic == "ticker"
        assert symbol == "BTCUSDT"

    def test_extract_topic_symbol_no_stream(self):
        """Test extraction with no stream key."""
        adapter = BinanceWebSocketAdapter()

        topic, symbol = adapter.extract_topic_symbol({"data": {}})

        assert topic is None
        assert symbol is None

    def test_extract_topic_symbol_no_at(self):
        """Test extraction with no @ separator."""
        adapter = BinanceWebSocketAdapter()

        topic, symbol = adapter.extract_topic_symbol({"stream": "invalid"})

        assert topic is None
        assert symbol is None

    def test_normalize_message_ticker(self):
        """Test normalizing ticker message."""
        adapter = BinanceWebSocketAdapter()

        message = {
            "stream": "btcusdt@ticker",
            "data": {
                "E": 1234567890000,
                "c": "50000.00",
                "v": "1000",
                "h": "51000",
                "l": "49000",
                "P": "2.5",
            },
        }

        normalized = adapter.normalize_message(message)

        assert normalized["exchange"] == "BINANCE"
        assert normalized["symbol"] == "BTCUSDT"
        assert normalized["topic"] == "ticker"
        assert normalized["last_price"] == 50000.0
        assert normalized["volume"] == 1000.0

    def test_normalize_message_depth(self):
        """Test normalizing depth message."""
        adapter = BinanceWebSocketAdapter()

        message = {
            "stream": "btcusdt@depth",
            "data": {
                "lastUpdateId": 123,
                "bids": [["50000", "1.5"], ["49999", "2.0"]],
                "asks": [["50001", "1.0"], ["50002", "0.5"]],
            },
        }

        normalized = adapter.normalize_message(message)

        # Depth messages are normalized with topic 'depth'
        assert normalized["topic"] == "depth"
        assert normalized["bids"][0] == [50000.0, 1.5]
        assert normalized["last_update_id"] == 123

    def test_normalize_message_trade(self):
        """Test normalizing trade message."""
        adapter = BinanceWebSocketAdapter()

        message = {
            "stream": "btcusdt@trade",
            "data": {"p": "50000.50", "q": "0.5", "T": 1234567890000, "m": True},
        }

        normalized = adapter.normalize_message(message)

        assert normalized["price"] == 50000.5
        assert normalized["quantity"] == 0.5
        assert normalized["is_buyer_maker"] is True

    def test_normalize_message_kline(self):
        """Test normalizing kline message."""
        adapter = BinanceWebSocketAdapter()

        message = {
            "stream": "btcusdt@kline_1m",
            "data": {
                "k": {
                    "t": 1234567890000,
                    "T": 1234567895999,
                    "o": "50000",
                    "h": "50100",
                    "l": "49900",
                    "c": "50050",
                    "v": "100",
                    "x": False,
                }
            },
        }

        normalized = adapter.normalize_message(message)

        assert normalized["open"] == 50000.0
        assert normalized["high"] == 50100.0
        assert normalized["close"] == 50050.0
        assert normalized["is_closed"] is False

    def test_normalize_message_no_data(self):
        """Test normalizing message without data key returns as-is."""
        adapter = BinanceWebSocketAdapter()

        message = {"error": "something"}

        normalized = adapter.normalize_message(message)

        assert normalized == message

    def test_get_rate_limit_config(self):
        """Test rate limit config."""
        adapter = BinanceWebSocketAdapter()

        config = adapter.get_rate_limit_config()

        assert config.max_connections_per_ip == 5
        assert config.max_subscriptions_per_connection == 1024

    def test_get_subscription_limits(self):
        """Test subscription limits."""
        adapter = BinanceWebSocketAdapter()

        limits = adapter.get_subscription_limits()

        assert limits["ticker"] == 1024
        assert limits["depth"] == 1024

    @pytest.mark.asyncio
    async def test_check_rate_limits_raises(self):
        """Test rate limit check raises when exceeded."""
        adapter = BinanceWebSocketAdapter()

        # Set subscription count to limit
        adapter._subscription_counts["ticker"] = 1024

        with pytest.raises(RateLimitError):
            await adapter.check_rate_limits("ticker")

    @pytest.mark.asyncio
    async def test_check_rate_limits_passes(self):
        """Test rate limit check passes when under limit."""
        adapter = BinanceWebSocketAdapter()

        # Should not raise
        await adapter.check_rate_limits("ticker")

    def test_increment_subscription_count(self):
        """Test incrementing subscription count."""
        adapter = BinanceWebSocketAdapter()

        adapter.increment_subscription_count("ticker")
        adapter.increment_subscription_count("ticker")

        assert adapter._subscription_counts["ticker"] == 2

    def test_decrement_subscription_count(self):
        """Test decrementing subscription count."""
        adapter = BinanceWebSocketAdapter()
        adapter._subscription_counts["ticker"] = 5

        adapter.decrement_subscription_count("ticker")

        assert adapter._subscription_counts["ticker"] == 4

    def test_decrement_subscription_count_floor(self):
        """Test decrementing doesn't go below zero."""
        adapter = BinanceWebSocketAdapter()

        adapter.decrement_subscription_count("ticker")

        assert adapter._subscription_counts["ticker"] == 0


class TestOKXWebSocketAdapter:
    """Tests for OKXWebSocketAdapter class."""

    def test_init_defaults(self):
        """Test default initialization."""
        adapter = OKXWebSocketAdapter()

        assert adapter.exchange_name == "OKX"
        assert adapter.exchange_type == ExchangeType.SPOT

    def test_get_endpoints(self):
        """Test endpoints."""
        adapter = OKXWebSocketAdapter()
        endpoints = adapter.get_endpoints("wss://test.com")

        assert "wss://ws.okx.com:8443/ws/v5/public" in endpoints
        assert len(endpoints) == 4

    def test_format_subscription_message(self):
        """Test subscription message formatting."""
        adapter = OKXWebSocketAdapter()

        msg = adapter.format_subscription_message("sub-1", "ticker", "BTC-USDT", {})

        assert msg["op"] == "subscribe"
        assert msg["args"][0]["channel"] == "tickers"
        assert msg["args"][0]["instId"] == "BTC-USDT"

    def test_format_subscription_message_kline(self):
        """Test kline subscription with interval."""
        adapter = OKXWebSocketAdapter()

        msg = adapter.format_subscription_message("sub-1", "kline", "BTC-USDT", {"interval": "5m"})

        assert msg["args"][0]["channel"] == "candle5m"

    def test_format_unsubscription_message(self):
        """Test unsubscription message formatting."""
        adapter = OKXWebSocketAdapter()

        msg = adapter.format_unsubscription_message("sub-1", "ticker", "BTC-USDT")

        assert msg["op"] == "unsubscribe"

    def test_extract_topic_symbol(self):
        """Test topic/symbol extraction."""
        adapter = OKXWebSocketAdapter()

        topic, symbol = adapter.extract_topic_symbol(
            {"arg": {"channel": "tickers", "instId": "BTC-USDT"}}
        )

        assert topic == "ticker"
        assert symbol == "BTC-USDT"

    def test_extract_topic_symbol_no_arg(self):
        """Test extraction with no arg key."""
        adapter = OKXWebSocketAdapter()

        topic, symbol = adapter.extract_topic_symbol({"data": []})

        assert topic is None
        assert symbol is None

    def test_normalize_message_ticker(self):
        """Test normalizing ticker message."""
        adapter = OKXWebSocketAdapter()

        message = {
            "arg": {"channel": "tickers", "instId": "BTC-USDT"},
            "data": [
                {
                    "ts": 1234567890000,
                    "last": "50000",
                    "vol24h": "1000",
                    "high24h": "51000",
                    "low24h": "49000",
                    "chg": "0.025",
                }
            ],
        }

        normalized = adapter.normalize_message(message)

        assert normalized["exchange"] == "OKX"
        assert normalized["symbol"] == "BTC-USDT"
        assert normalized["topic"] == "ticker"
        assert normalized["last_price"] == 50000.0

    def test_normalize_message_depth(self):
        """Test normalizing depth message."""
        adapter = OKXWebSocketAdapter()

        message = {
            "arg": {"channel": "books", "instId": "BTC-USDT"},
            "data": [
                {
                    "bids": [["50000", "1.5"]],
                    "asks": [["50001", "1.0"]],
                    "checksum": 12345,
                }
            ],
        }

        normalized = adapter.normalize_message(message)

        assert normalized["topic"] == "depth"
        assert normalized["bids"] == [[50000.0, 1.5]]
        assert normalized["checksum"] == 12345

    def test_normalize_message_trade(self):
        """Test normalizing trade message."""
        adapter = OKXWebSocketAdapter()

        message = {
            "arg": {"channel": "trades", "instId": "BTC-USDT"},
            "data": [{"px": "50000", "sz": "0.5", "ts": "1234567890000", "side": "buy"}],
        }

        normalized = adapter.normalize_message(message)

        assert normalized["price"] == 50000.0
        assert normalized["quantity"] == 0.5
        assert normalized["side"] == "buy"

    def test_normalize_message_kline(self):
        """Test normalizing kline message."""
        adapter = OKXWebSocketAdapter()

        message = {
            "arg": {"channel": "candle1m", "instId": "BTC-USDT"},
            "data": [
                {
                    "ts": "1234567890000",
                    "o": "50000",
                    "h": "50100",
                    "l": "49900",
                    "c": "50050",
                    "vol": "100",
                }
            ],
        }

        normalized = adapter.normalize_message(message)

        assert normalized["open"] == 50000.0
        assert normalized["close"] == 50050.0

    def test_normalize_message_no_data(self):
        """Test normalizing message without data key."""
        adapter = OKXWebSocketAdapter()

        message = {"error": "something"}

        normalized = adapter.normalize_message(message)

        assert normalized == message

    def test_get_rate_limit_config(self):
        """Test rate limit config."""
        adapter = OKXWebSocketAdapter()

        config = adapter.get_rate_limit_config()

        assert config.max_connections_per_ip == 4
        assert config.max_subscriptions_per_connection == 240


class TestWebSocketAdapterFactory:
    """Tests for WebSocketAdapterFactory class."""

    def test_create_binance_adapter(self):
        """Test creating Binance adapter."""
        adapter = WebSocketAdapterFactory.create_adapter("BINANCE")

        assert isinstance(adapter, BinanceWebSocketAdapter)

    def test_create_okx_adapter(self):
        """Test creating OKX adapter."""
        adapter = WebSocketAdapterFactory.create_adapter("OKX")

        assert isinstance(adapter, OKXWebSocketAdapter)

    def test_create_generic_adapter(self):
        """Test creating generic adapter for unknown exchange."""
        adapter = WebSocketAdapterFactory.create_adapter("UNKNOWN_EXCHANGE")

        assert isinstance(adapter, GenericWebSocketAdapter)
        assert adapter.exchange_name == "UNKNOWN_EXCHANGE"

    def test_create_adapter_case_insensitive(self):
        """Test exchange name is case insensitive."""
        adapter = WebSocketAdapterFactory.create_adapter("binance")

        assert isinstance(adapter, BinanceWebSocketAdapter)

    def test_create_adapter_with_credentials(self):
        """Test creating adapter with credentials."""
        creds = ExchangeCredentials(
            exchange_name="BINANCE",
            auth_type=AuthenticationType.API_KEY,
            api_key="test_key",
        )
        adapter = WebSocketAdapterFactory.create_adapter("BINANCE", credentials=creds)

        assert adapter.credentials == creds

    def test_create_adapter_with_exchange_type(self):
        """Test creating adapter with exchange type."""
        adapter = WebSocketAdapterFactory.create_adapter(
            "BINANCE", exchange_type=ExchangeType.FUTURES
        )

        assert adapter.exchange_type == ExchangeType.FUTURES

    def test_register_adapter(self):
        """Test registering custom adapter."""
        # Register a custom adapter
        WebSocketAdapterFactory.register_adapter("CUSTOM", GenericWebSocketAdapter)

        adapter = WebSocketAdapterFactory.create_adapter("CUSTOM")

        assert isinstance(adapter, GenericWebSocketAdapter)


class TestGenericWebSocketAdapter:
    """Tests for GenericWebSocketAdapter class."""

    def test_init(self):
        """Test initialization."""
        adapter = GenericWebSocketAdapter("TEST_EXCHANGE")

        assert adapter.exchange_name == "TEST_EXCHANGE"

    def test_format_subscription_message(self):
        """Test subscription message formatting."""
        adapter = GenericWebSocketAdapter("TEST")

        msg = adapter.format_subscription_message("sub-1", "ticker", "BTC", {"level": 1})

        assert msg["action"] == "subscribe"
        assert msg["topic"] == "ticker"
        assert msg["symbol"] == "BTC"
        assert msg["params"] == {"level": 1}

    def test_format_unsubscription_message(self):
        """Test unsubscription message formatting."""
        adapter = GenericWebSocketAdapter("TEST")

        msg = adapter.format_unsubscription_message("sub-1", "ticker", "BTC")

        assert msg["action"] == "unsubscribe"

    def test_extract_topic_symbol(self):
        """Test topic/symbol extraction."""
        adapter = GenericWebSocketAdapter("TEST")

        topic, symbol = adapter.extract_topic_symbol({"topic": "ticker", "symbol": "BTC"})

        assert topic == "ticker"
        assert symbol == "BTC"

    def test_normalize_message(self):
        """Test message normalization."""
        adapter = GenericWebSocketAdapter("TEST")

        normalized = adapter.normalize_message(
            {"symbol": "BTC", "topic": "ticker", "data": {"price": 100}, "timestamp": 123}
        )

        assert normalized["exchange"] == "TEST"
        assert normalized["symbol"] == "BTC"
        assert normalized["data"] == {"price": 100}

    def test_get_rate_limit_config(self):
        """Test rate limit config."""
        adapter = GenericWebSocketAdapter("TEST")

        config = adapter.get_rate_limit_config()

        assert config.max_connections_per_ip == 5


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
