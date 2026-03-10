"""
Exchange-specific WebSocket optimizations and message format handling.
Supports different connection strategies, rate limiting, and data synchronization for each exchange.
"""

import base64
import hashlib
import hmac
import json
import time
from abc import ABC, abstractmethod
from dataclasses import dataclass
from enum import Enum
from typing import Any

from bt_api_py.exceptions import AuthenticationError, RateLimitError
from bt_api_py.logging_factory import get_logger


class ExchangeType(Enum):
    """Exchange type enumeration."""

    SPOT = "spot"
    FUTURES = "futures"
    SWAP = "swap"
    OPTIONS = "options"


class AuthenticationType(Enum):
    """Authentication type enumeration."""

    NONE = "none"
    API_KEY = "api_key"
    API_KEY_SECRET = "api_key_secret"
    JWT = "jwt"
    CUSTOM = "custom"


@dataclass
class ExchangeCredentials:
    """Exchange authentication credentials."""

    exchange_name: str
    auth_type: AuthenticationType
    api_key: str | None = None
    api_secret: str | None = None
    passphrase: str | None = None  # For exchanges like OKX
    jwt_token: str | None = None
    custom_params: dict[str, Any] | None = None

    def __post_init__(self):
        if self.custom_params is None:
            self.custom_params = {}


@dataclass
class RateLimitConfig:
    """Rate limiting configuration for exchange."""

    # WebSocket rate limits
    max_connections_per_ip: int = 5
    max_subscriptions_per_connection: int = 50
    messages_per_second_limit: int = 10
    reconnect_delay_seconds: float = 1.0

    # REST API rate limits (for token refresh)
    requests_per_second: int = 10
    requests_per_minute: int = 600

    # Exchange-specific limits
    exchange_specific_limits: dict[str, int] | None = None

    def __post_init__(self):
        if self.exchange_specific_limits is None:
            self.exchange_specific_limits = {}


class ExchangeWebSocketAdapter(ABC):
    """Abstract base class for exchange-specific WebSocket adapters."""

    def __init__(self, exchange_name: str, credentials: ExchangeCredentials | None = None):
        self.exchange_name = exchange_name
        self.credentials = credentials
        self.logger = get_logger(f"ws_adapter_{exchange_name}")

        # Rate limiting
        self._rate_limiter = None
        self._subscription_counts: dict[str, int] = {}

    @abstractmethod
    async def authenticate(self, websocket: Any) -> None:
        """Perform exchange-specific authentication."""
        pass

    @abstractmethod
    def format_subscription_message(
        self, subscription_id: str, topic: str, symbol: str, params: dict[str, Any]
    ) -> dict[str, Any]:
        """Format subscription message for the exchange."""
        pass

    @abstractmethod
    def format_unsubscription_message(
        self, subscription_id: str, topic: str, symbol: str
    ) -> dict[str, Any]:
        """Format unsubscription message for the exchange."""
        pass

    @abstractmethod
    def extract_topic_symbol(self, message: dict[str, Any]) -> tuple[str | None, str | None]:
        """Extract topic and symbol from incoming message."""
        pass

    @abstractmethod
    def normalize_message(self, message: dict[str, Any]) -> dict[str, Any]:
        """Normalize message format to standard bt_api_py format."""
        pass

    @abstractmethod
    def get_rate_limit_config(self) -> RateLimitConfig:
        """Get rate limiting configuration for the exchange."""
        pass

    def get_endpoints(self, primary_url: str) -> list[str]:
        """Get failover endpoints for the exchange."""
        # Default implementation - override for specific exchanges
        return [primary_url]

    def get_subscription_limits(self) -> dict[str, int]:
        """Get subscription limits per topic."""
        return {"ticker": 100, "depth": 50, "trades": 100, "kline": 200, "orders": 50}

    async def check_rate_limits(self, topic: str) -> None:
        """Check if subscription request complies with rate limits."""
        limits = self.get_subscription_limits()
        current_count = self._subscription_counts.get(topic, 0)

        if current_count >= limits.get(topic, 100):
            raise RateLimitError(
                f"Subscription limit exceeded for {topic}: {current_count}/{limits[topic]}"
            )

    def increment_subscription_count(self, topic: str) -> None:
        """Increment subscription count for topic."""
        self._subscription_counts[topic] = self._subscription_counts.get(topic, 0) + 1

    def decrement_subscription_count(self, topic: str) -> None:
        """Decrement subscription count for topic."""
        self._subscription_counts[topic] = max(0, self._subscription_counts.get(topic, 0) - 1)


class BinanceWebSocketAdapter(ExchangeWebSocketAdapter):
    """Binance-specific WebSocket adapter."""

    def __init__(
        self,
        exchange_type: ExchangeType = ExchangeType.SPOT,
        credentials: ExchangeCredentials | None = None,
    ):
        super().__init__("BINANCE", credentials)
        self.exchange_type = exchange_type
        self._listen_key: str | None = None

    def get_endpoints(self, primary_url: str) -> list[str]:
        """Get Binance failover endpoints."""
        base_urls = [
            "wss://stream.binance.com:9443",  # Primary
            "wss://stream.binance.com:443",  # Backup
            "wss://stream1.binance.com:9443",  # Alternate
            "wss://stream2.binance.com:9443",  # Alternate
        ]

        if self.exchange_type == ExchangeType.FUTURES:
            base_urls = [
                "wss://fstream.binance.com",
                "wss://fstream.binance.com:443",
                "wss://fstream1.binance.com",
                "wss://fstream2.binance.com",
            ]
        elif self.exchange_type == ExchangeType.SWAP:
            base_urls = [
                "wss://dstream.binance.com",
                "wss://dstream.binance.com:443",
                "wss://dstream1.binance.com",
                "wss://dstream2.binance.com",
            ]

        return base_urls

    async def authenticate(self, websocket: Any) -> None:
        """Authenticate for user data streams."""
        if self.credentials and self.credentials.auth_type == AuthenticationType.API_KEY_SECRET:
            # For user data streams, we need to get a listen key first
            await self._get_listen_key()

            # Send listen key as subscription
            message = {
                "method": "SUBSCRIBE",
                "params": [self._listen_key],
                "id": int(time.time() * 1000),
            }

            await websocket.send(json.dumps(message))
            self.logger.info("Binance authentication completed")

    async def _get_listen_key(self) -> None:
        """Get listen key for user data stream."""
        # This would make REST API call to get listen key
        # For now, just log the operation
        self.logger.info("Getting Binance listen key")
        # In production, this would call Binance REST API
        # self._listen_key = await rest_api.create_listen_key()

    def format_subscription_message(
        self, subscription_id: str, topic: str, symbol: str, params: dict[str, Any]
    ) -> dict[str, Any]:
        """Format Binance subscription message."""
        stream_name = self._get_stream_name(topic, symbol, params)

        return {"method": "SUBSCRIBE", "params": [stream_name], "id": int(time.time() * 1000)}

    def format_unsubscription_message(
        self, subscription_id: str, topic: str, symbol: str
    ) -> dict[str, Any]:
        """Format Binance unsubscription message."""
        stream_name = self._get_stream_name(topic, symbol, {})

        return {"method": "UNSUBSCRIBE", "params": [stream_name], "id": int(time.time() * 1000)}

    def _get_stream_name(self, topic: str, symbol: str, params: dict[str, Any]) -> str:
        """Get Binance stream name."""
        symbol_lower = symbol.lower()

        if topic == "ticker":
            return f"{symbol_lower}@ticker"
        elif topic == "depth":
            level = params.get("level", "20")
            return f"{symbol_lower}@depth{level}"
        elif topic == "trades":
            return f"{symbol_lower}@trade"
        elif topic == "kline":
            interval = params.get("interval", "1m")
            return f"{symbol_lower}@kline_{interval}"
        elif topic == "aggTrades":
            return f"{symbol_lower}@aggTrade"
        elif topic == "markPrice":
            return f"{symbol_lower}@markPrice@1s"
        else:
            return f"{symbol_lower}@{topic}"

    def extract_topic_symbol(self, message: dict[str, Any]) -> tuple[str | None, str | None]:
        """Extract topic and symbol from Binance message."""
        if "stream" in message:
            stream = message["stream"]
            # Format: symbol@topic (e.g., btcusdt@ticker)
            if "@" in stream:
                symbol, topic = stream.split("@", 1)
                return topic, symbol.upper()

        return None, None

    def normalize_message(self, message: dict[str, Any]) -> dict[str, Any]:
        """Normalize Binance message format."""
        if "data" in message and "stream" in message:
            # Stream data message
            data = message["data"]
            message["stream"]
            topic, symbol = self.extract_topic_symbol(message)

            normalized = {
                "exchange": "BINANCE",
                "symbol": symbol,
                "topic": topic,
                "data": data,
                "timestamp": data.get("E", time.time() * 1000),
            }

            # Add topic-specific fields
            if topic == "ticker":
                normalized.update(
                    {
                        "last_price": float(data.get("c", 0)),
                        "volume": float(data.get("v", 0)),
                        "high_24h": float(data.get("h", 0)),
                        "low_24h": float(data.get("l", 0)),
                        "change_24h": float(data.get("P", 0)),
                    }
                )
            elif topic == "depth":
                normalized.update(
                    {
                        "bids": [[float(p), float(q)] for p, q in data.get("bids", [])],
                        "asks": [[float(p), float(q)] for p, q in data.get("asks", [])],
                        "last_update_id": data.get("lastUpdateId"),
                    }
                )
            elif topic in ("trade", "aggTrade"):
                normalized.update(
                    {
                        "price": float(data.get("p", 0)),
                        "quantity": float(data.get("q", 0)),
                        "trade_time": data.get("T"),
                        "is_buyer_maker": data.get("m", False),
                    }
                )
            elif topic and topic.startswith("kline"):
                kline_data = data.get("k", {}) if data else {}
                normalized.update(
                    {
                        "open_time": kline_data.get("t"),
                        "close_time": kline_data.get("T"),
                        "open": float(kline_data.get("o", 0)),
                        "high": float(kline_data.get("h", 0)),
                        "low": float(kline_data.get("l", 0)),
                        "close": float(kline_data.get("c", 0)),
                        "volume": float(kline_data.get("v", 0)),
                        "is_closed": kline_data.get("x", False),
                    }
                )

            return normalized

        return message

    def get_rate_limit_config(self) -> RateLimitConfig:
        """Get Binance rate limiting configuration."""
        return RateLimitConfig(
            max_connections_per_ip=5,
            max_subscriptions_per_connection=1024,
            messages_per_second_limit=5,
            requests_per_second=10,
            requests_per_minute=1200,
        )

    def get_subscription_limits(self) -> dict[str, int]:
        """Get Binance subscription limits."""
        return {
            "ticker": 1024,
            "depth": 1024,
            "trades": 1024,
            "kline": 1024,
            "aggTrades": 1024,
            "markPrice": 1024,
        }


class OKXWebSocketAdapter(ExchangeWebSocketAdapter):
    """OKX-specific WebSocket adapter."""

    def __init__(
        self,
        exchange_type: ExchangeType = ExchangeType.SPOT,
        credentials: ExchangeCredentials | None = None,
    ):
        super().__init__("OKX", credentials)
        self.exchange_type = exchange_type

    def get_endpoints(self, primary_url: str) -> list[str]:
        """Get OKX failover endpoints."""
        return [
            "wss://ws.okx.com:8443/ws/v5/public",
            "wss://ws.okx.com:8443/ws/v5/private",
            "wss://wsa.okx.com:8443/ws/v5/public",
            "wss://wsa.okx.com:8443/ws/v5/private",
        ]

    async def authenticate(self, websocket: Any) -> None:
        """Authenticate with OKX API."""
        if self.credentials and self.credentials.auth_type == AuthenticationType.API_KEY_SECRET:
            timestamp = str(int(time.time()))
            sign = self._generate_signature(timestamp, "GET", "/users/self/verify")

            message = {
                "op": "login",
                "args": [
                    {
                        "apiKey": self.credentials.api_key,
                        "passphrase": self.credentials.passphrase,
                        "timestamp": timestamp,
                        "sign": sign,
                    }
                ],
            }

            await websocket.send(json.dumps(message))
            self.logger.info("OKX authentication sent")

    def _generate_signature(self, timestamp: str, method: str, path: str) -> str:
        """Generate OKX API signature."""
        if not self.credentials or not self.credentials.api_secret:
            raise AuthenticationError("Missing API credentials")

        message = timestamp + method + path
        signature = hmac.new(
            self.credentials.api_secret.encode(), message.encode(), hashlib.sha256
        ).digest()

        return base64.b64encode(signature).decode()

    def format_subscription_message(
        self, subscription_id: str, topic: str, symbol: str, params: dict[str, Any]
    ) -> dict[str, Any]:
        """Format OKX subscription message."""
        channel = self._get_okx_channel(topic, params)
        inst_id = symbol  # OKX uses instrument ID directly

        return {"op": "subscribe", "args": [{"channel": channel, "instId": inst_id}]}

    def format_unsubscription_message(
        self, subscription_id: str, topic: str, symbol: str
    ) -> dict[str, Any]:
        """Format OKX unsubscription message."""
        return {"op": "unsubscribe", "args": [{"channel": topic, "instId": symbol}]}

    def _get_okx_channel(self, topic: str, params: dict[str, Any]) -> str:
        """Convert generic topic to OKX channel."""
        mapping = {
            "ticker": "tickers",
            "depth": "books",
            "trades": "trades",
            "kline": "candle" + params.get("interval", "1m"),
            "orders": "orders",
            "positions": "positions",
            "account": "account",
        }

        return mapping.get(topic, topic)

    def extract_topic_symbol(self, message: dict[str, Any]) -> tuple[str | None, str | None]:
        """Extract topic and symbol from OKX message."""
        if "arg" in message:
            arg = message["arg"]
            channel = arg.get("channel")
            inst_id = arg.get("instId")

            # Convert OKX channel back to generic topic
            topic = self._convert_okx_channel_to_generic(channel)
            symbol = inst_id

            return topic, symbol

        return None, None

    def _convert_okx_channel_to_generic(self, channel: str) -> str:
        """Convert OKX channel to generic topic."""
        if channel == "tickers":
            return "ticker"
        elif channel == "books":
            return "depth"
        elif channel == "trades":
            return "trades"
        elif channel.startswith("candle"):
            return "kline"
        elif channel == "orders":
            return "orders"
        elif channel == "positions":
            return "positions"
        elif channel == "account":
            return "account"

        return channel

    def normalize_message(self, message: dict[str, Any]) -> dict[str, Any]:
        """Normalize OKX message format."""
        if "data" in message and "arg" in message:
            message["arg"]
            data = message["data"]

            # Handle array data (most OKX messages)
            if isinstance(data, list) and data:
                data = data[0]

            topic, symbol = self.extract_topic_symbol(message)

            normalized = {
                "exchange": "OKX",
                "symbol": symbol,
                "topic": topic,
                "data": data,
                "timestamp": int(
                    float(data.get("ts", time.time() * 1000))
                    if isinstance(data, dict)
                    else int(time.time() * 1000)
                ),
            }

            # Add topic-specific fields
            if topic == "ticker" and isinstance(data, dict):
                normalized.update(
                    {
                        "last_price": float(data.get("last", 0)),
                        "volume": float(data.get("vol24h", 0)),
                        "high_24h": float(data.get("high24h", 0)),
                        "low_24h": float(data.get("low24h", 0)),
                        "change_24h": float(data.get("chg", 0)),
                    }
                )
            elif topic == "depth" and isinstance(data, dict):
                normalized.update(
                    {
                        "bids": [[float(p), float(s)] for p, s in data.get("bids", [])],
                        "asks": [[float(p), float(s)] for p, s in data.get("asks", [])],
                        "checksum": data.get("checksum"),
                    }
                )
            elif topic == "trades" and isinstance(data, dict):
                normalized.update(
                    {
                        "price": float(data.get("px", 0)),
                        "quantity": float(data.get("sz", 0)),
                        "trade_time": int(data.get("ts", 0)),
                        "side": data.get("side"),
                    }
                )
            elif topic == "kline" and isinstance(data, dict):
                normalized.update(
                    {
                        "open_time": int(data.get("ts", 0)),
                        "open": float(data.get("o", 0)),
                        "high": float(data.get("h", 0)),
                        "low": float(data.get("l", 0)),
                        "close": float(data.get("c", 0)),
                        "volume": float(data.get("vol", 0)),
                    }
                )

            return normalized

        return message

    def get_rate_limit_config(self) -> RateLimitConfig:
        """Get OKX rate limiting configuration."""
        return RateLimitConfig(
            max_connections_per_ip=4,
            max_subscriptions_per_connection=240,
            messages_per_second_limit=20,
            requests_per_second=20,
            requests_per_minute=600,
        )


class WebSocketAdapterFactory:
    """Factory for creating exchange-specific WebSocket adapters."""

    _adapters = {
        "BINANCE": BinanceWebSocketAdapter,
        "OKX": OKXWebSocketAdapter,
        # Add more exchanges as needed
    }

    @classmethod
    def create_adapter(
        cls,
        exchange_name: str,
        exchange_type: ExchangeType = ExchangeType.SPOT,
        credentials: ExchangeCredentials | None = None,
    ) -> ExchangeWebSocketAdapter:
        """Create exchange-specific adapter."""
        adapter_class = cls._adapters.get(exchange_name.upper())

        if not adapter_class:
            # Use generic adapter if exchange not supported
            cls._adapters[exchange_name.upper()] = adapter_class
            return GenericWebSocketAdapter(exchange_name, credentials)

        return adapter_class(exchange_type, credentials)

    @classmethod
    def register_adapter(cls, exchange_name: str, adapter_class: type) -> None:
        """Register a new adapter class."""
        cls._adapters[exchange_name.upper()] = adapter_class


class GenericWebSocketAdapter(ExchangeWebSocketAdapter):
    """Generic WebSocket adapter for unsupported exchanges."""

    async def authenticate(self, websocket: Any) -> None:
        """No authentication for generic adapter."""
        pass

    def format_subscription_message(
        self, subscription_id: str, topic: str, symbol: str, params: dict[str, Any]
    ) -> dict[str, Any]:
        """Generic subscription message format."""
        return {
            "action": "subscribe",
            "topic": topic,
            "symbol": symbol,
            "params": params,
            "id": subscription_id,
        }

    def format_unsubscription_message(
        self, subscription_id: str, topic: str, symbol: str
    ) -> dict[str, Any]:
        """Generic unsubscription message format."""
        return {"action": "unsubscribe", "topic": topic, "symbol": symbol, "id": subscription_id}

    def extract_topic_symbol(self, message: dict[str, Any]) -> tuple[str | None, str | None]:
        """Generic topic/symbol extraction."""
        return message.get("topic"), message.get("symbol")

    def normalize_message(self, message: dict[str, Any]) -> dict[str, Any]:
        """Generic message normalization."""
        return {
            "exchange": self.exchange_name,
            "symbol": message.get("symbol"),
            "topic": message.get("topic"),
            "data": message.get("data"),
            "timestamp": message.get("timestamp", time.time() * 1000),
        }

    def get_rate_limit_config(self) -> RateLimitConfig:
        """Generic rate limiting configuration."""
        return RateLimitConfig()
