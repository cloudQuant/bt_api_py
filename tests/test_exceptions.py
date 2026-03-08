"""
Comprehensive tests for bt_api_py exception hierarchy
Target: 50+ tests covering all custom exceptions
"""

import pytest

from bt_api_py.exceptions import (
    AuthenticationError,
    BtApiError,
    ConfigurationError,
    DataParseError,
    ExchangeConnectionAlias,
    ExchangeConnectionError,
    ExchangeNotFoundError,
    InsufficientBalanceError,
    InvalidOrderError,
    InvalidSymbolError,
    NetworkError,
    OrderError,
    OrderNotFoundError,
    RateLimitError,
    RequestError,
    RequestFailedError,
    RequestTimeoutError,
    SubscribeError,
    WebSocketError,
)


class TestExceptionHierarchy:
    """Test custom exception hierarchy and inheritance"""

    def test_btapi_error_is_base_exception(self):
        """All custom exceptions inherit from BtApiError"""
        assert issubclass(ExchangeNotFoundError, BtApiError)
        assert issubclass(ExchangeConnectionError, BtApiError)
        assert issubclass(RequestError, BtApiError)
        assert issubclass(OrderError, BtApiError)
        assert issubclass(SubscribeError, BtApiError)
        assert issubclass(DataParseError, BtApiError)
        assert issubclass(RateLimitError, BtApiError)
        assert issubclass(NetworkError, BtApiError)
        assert issubclass(InvalidSymbolError, BtApiError)
        assert issubclass(ConfigurationError, BtApiError)
        assert issubclass(WebSocketError, BtApiError)
        assert issubclass(RequestFailedError, BtApiError)

    def test_order_error_subclasses(self):
        """OrderError has specific subclasses"""
        assert issubclass(InsufficientBalanceError, OrderError)
        assert issubclass(InvalidOrderError, OrderError)
        assert issubclass(OrderNotFoundError, OrderError)
        assert issubclass(OrderError, BtApiError)

    def test_authentication_error_inherits_from_connection_error(self):
        """AuthenticationError inherits from ExchangeConnectionError"""
        assert issubclass(AuthenticationError, ExchangeConnectionError)
        assert issubclass(AuthenticationError, BtApiError)

    def test_exchange_connection_alias_is_deprecated_but_works(self):
        """ExchangeConnectionAlias should work for backward compatibility"""
        assert ExchangeConnectionAlias is ExchangeConnectionError


class TestExchangeNotFoundError:
    """Test ExchangeNotFoundError exception"""

    def test_basic_exchange_not_found_error(self):
        """ExchangeNotFoundError with minimal parameters"""
        exc = ExchangeNotFoundError("BINANCE___SPOT")
        assert exc.exchange_name == "BINANCE___SPOT"
        assert "BINANCE___SPOT" in str(exc)
        assert "Exchange not found" in str(exc)

    def test_exchange_not_found_with_available_exchanges(self):
        """ExchangeNotFoundError includes available exchanges"""
        exc = ExchangeNotFoundError("BINANCE___SPOT", available=["OKX___SPOT", "BYBIT___SPOT"])
        assert exc.exchange_name == "BINANCE___SPOT"
        assert "OKX___SPOT" in str(exc)
        assert "BYBIT___SPOT" in str(exc)
        assert "Available:" in str(exc)

    def test_exchange_not_found_without_available_exchanges(self):
        """ExchangeNotFoundError without available exchanges"""
        exc = ExchangeNotFoundError("TEST___SPOT", available=None)
        assert exc.exchange_name == "TEST___SPOT"
        assert "Available" not in str(exc)

    def test_exchange_not_found_with_empty_available_list(self):
        """ExchangeNotFoundError with empty available list"""
        exc = ExchangeNotFoundError("TEST___SPOT", available=[])
        assert exc.exchange_name == "TEST___SPOT"


class TestExchangeConnectionError:
    """Test ExchangeConnectionError exception"""

    def test_basic_connection_error(self):
        """ExchangeConnectionError with minimal parameters"""
        exc = ExchangeConnectionError("BINANCE___SPOT")
        assert exc.exchange_name == "BINANCE___SPOT"
        assert "Connection failed" in str(exc)
        assert "BINANCE___SPOT" in str(exc)

    def test_connection_error_with_detail(self):
        """ExchangeConnectionError includes detail message"""
        exc = ExchangeConnectionError("BINANCE___SPOT", detail="Network timeout")
        assert exc.exchange_name == "BINANCE___SPOT"
        assert "Network timeout" in str(exc)
        assert "—" in str(exc)

    def test_connection_error_empty_detail(self):
        """ExchangeConnectionError with empty detail"""
        exc = ExchangeConnectionError("OKX___SPOT", detail="")
        assert exc.exchange_name == "OKX___SPOT"
        assert "—" not in str(exc)


class TestAuthenticationError:
    """Test AuthenticationError exception"""

    def test_auth_error_basic(self):
        """AuthenticationError with minimal parameters"""
        exc = AuthenticationError("BINANCE___SPOT")
        assert exc.exchange_name == "BINANCE___SPOT"
        assert "Connection failed" in str(exc)

    def test_auth_error_with_detail(self):
        """AuthenticationError includes detail"""
        exc = AuthenticationError("BINANCE___SPOT", detail="Invalid API key")
        assert exc.exchange_name == "BINANCE___SPOT"
        assert "Invalid API key" in str(exc)


class TestRequestTimeoutError:
    """Test RequestTimeoutError exception"""

    def test_timeout_error_with_all_params(self):
        """RequestTimeoutError with all parameters"""
        exc = RequestTimeoutError(
            exchange_name="BINANCE___SPOT",
            url="https://api.binance.com/api/v3/ticker/price",
            timeout=30,
        )
        assert exc.exchange_name == "BINANCE___SPOT"
        assert exc.url == "https://api.binance.com/api/v3/ticker/price"
        assert exc.timeout == 30
        assert "30s" in str(exc)
        assert "https://api.binance.com/api/v3/ticker/price" in str(exc)

    def test_timeout_error_without_url(self):
        """RequestTimeoutError without URL"""
        exc = RequestTimeoutError("OKX___SPOT", url="", timeout=60)
        assert exc.exchange_name == "OKX___SPOT"
        assert exc.url == ""
        assert exc.timeout == 60
        assert "60s" in str(exc)

    def test_timeout_error_zero_timeout(self):
        """RequestTimeoutError with zero timeout"""
        exc = RequestTimeoutError("BINANCE___SPOT", url="https://test.com", timeout=0)
        assert exc.timeout == 0


class TestRequestError:
    """Test RequestError exception"""

    def test_request_error_basic(self):
        """RequestError with minimal parameters"""
        exc = RequestError("BINANCE___SPOT")
        assert exc.exchange_name == "BINANCE___SPOT"
        assert "request error" in str(exc)

    def test_request_error_with_url(self):
        """RequestError includes URL"""
        exc = RequestError("BINANCE___SPOT", url="https://api.binance.com/api/v3/order")
        assert exc.exchange_name == "BINANCE___SPOT"
        assert "https://api.binance.com/api/v3/order" in str(exc)

    def test_request_error_with_detail(self):
        """RequestError includes detail message"""
        exc = RequestError("BINANCE___SPOT", detail="HTTP 500")
        assert exc.exchange_name == "BINANCE___SPOT"
        assert "HTTP 500" in str(exc)
        assert "—" in str(exc)

    def test_request_error_with_all_params(self):
        """RequestError with all parameters"""
        exc = RequestError(
            "OKX___SPOT", url="https://www.okx.com/api/v5/trade/order", detail="Invalid signature"
        )
        assert exc.exchange_name == "OKX___SPOT"
        assert "https://www.okx.com/api/v5/trade/order" in str(exc)
        assert "Invalid signature" in str(exc)


class TestOrderError:
    """Test OrderError exception"""

    def test_order_error_basic(self):
        """OrderError with minimal parameters"""
        exc = OrderError("BINANCE___SPOT")
        assert exc.exchange_name == "BINANCE___SPOT"
        assert exc.symbol == ""
        assert "order error" in str(exc)

    def test_order_error_with_symbol(self):
        """OrderError includes symbol"""
        exc = OrderError("BINANCE___SPOT", symbol="BTCUSDT")
        assert exc.exchange_name == "BINANCE___SPOT"
        assert exc.symbol == "BTCUSDT"
        assert "[BTCUSDT]" in str(exc)

    def test_order_error_with_detail(self):
        """OrderError includes detail message"""
        exc = OrderError("BINANCE___SPOT", detail="Insufficient margin")
        assert exc.exchange_name == "BINANCE___SPOT"
        assert "Insufficient margin" in str(exc)

    def test_order_error_with_all_params(self):
        """OrderError with all parameters"""
        exc = OrderError("OKX___SPOT", symbol="BTC-USDT", detail="Order rejected")
        assert exc.exchange_name == "OKX___SPOT"
        assert exc.symbol == "BTC-USDT"
        assert "[BTC-USDT]" in str(exc)
        assert "Order rejected" in str(exc)


class TestInsufficientBalanceError:
    """Test InsufficientBalanceError exception"""

    def test_insufficient_balance_basic(self):
        """InsufficientBalanceError with minimal parameters"""
        exc = InsufficientBalanceError("BINANCE___SPOT")
        assert exc.exchange_name == "BINANCE___SPOT"
        assert exc.required is None
        assert exc.available is None
        assert "Insufficient balance" in str(exc)

    def test_insufficient_balance_with_amounts(self):
        """InsufficientBalanceError includes required and available"""
        exc = InsufficientBalanceError(
            exchange_name="BINANCE___SPOT", symbol="BTCUSDT", required=1.5, available=0.5
        )
        assert exc.exchange_name == "BINANCE___SPOT"
        assert exc.symbol == "BTCUSDT"
        assert exc.required == 1.5
        assert exc.available == 0.5
        assert "required: 1.5" in str(exc)
        assert "available: 0.5" in str(exc)

    def test_insufficient_balance_zero_available(self):
        """InsufficientBalanceError with zero available balance"""
        exc = InsufficientBalanceError("OKX___SPOT", required=10.0, available=0.0)
        assert exc.required == 10.0
        assert exc.available == 0.0

    def test_insufficient_balance_partial_amounts(self):
        """InsufficientBalanceError with only required or only available"""
        exc1 = InsufficientBalanceError("BINANCE___SPOT", required=5.0)
        assert exc1.required == 5.0
        assert exc1.available is None

        exc2 = InsufficientBalanceError("BINANCE___SPOT", available=2.0)
        assert exc2.required is None
        assert exc2.available == 2.0


class TestOrderNotFoundError:
    """Test OrderNotFoundError exception"""

    def test_order_not_found_basic(self):
        """OrderNotFoundError with minimal parameters"""
        exc = OrderNotFoundError("BINANCE___SPOT", order_id="12345")
        assert exc.exchange_name == "BINANCE___SPOT"
        assert exc.order_id == "12345"
        assert "Order not found" in str(exc)
        assert "12345" in str(exc)

    def test_order_not_found_with_symbol(self):
        """OrderNotFoundError includes symbol"""
        exc = OrderNotFoundError("OKX___SPOT", order_id="67890", symbol="BTC-USDT")
        assert exc.exchange_name == "OKX___SPOT"
        assert exc.order_id == "67890"
        assert exc.symbol == "BTC-USDT"
        assert "[BTC-USDT]" in str(exc)


class TestInvalidOrderError:
    """Test InvalidOrderError exception"""

    def test_invalid_order_error(self):
        """InvalidOrderError basic usage"""
        exc = InvalidOrderError("BINANCE___SPOT", symbol="BTCUSDT", detail="Invalid price")
        assert exc.exchange_name == "BINANCE___SPOT"
        assert exc.symbol == "BTCUSDT"
        assert "Invalid price" in str(exc)


class TestSubscribeError:
    """Test SubscribeError exception"""

    def test_subscribe_error_basic(self):
        """SubscribeError with minimal parameters"""
        exc = SubscribeError("BINANCE___SPOT")
        assert exc.exchange_name == "BINANCE___SPOT"
        assert "subscribe error" in str(exc)

    def test_subscribe_error_with_detail(self):
        """SubscribeError includes detail message"""
        exc = SubscribeError("BINANCE___SPOT", detail="Invalid channel")
        assert exc.exchange_name == "BINANCE___SPOT"
        assert "Invalid channel" in str(exc)


class TestDataParseError:
    """Test DataParseError exception"""

    def test_data_parse_error_basic(self):
        """DataParseError with minimal parameters"""
        exc = DataParseError()
        assert "Data parse error" in str(exc)

    def test_data_parse_error_with_container_class(self):
        """DataParseError includes container class name"""
        exc = DataParseError(container_class="BinanceTickerData")
        assert "BinanceTickerData" in str(exc)

    def test_data_parse_error_with_detail(self):
        """DataParseError includes detail message"""
        exc = DataParseError(container_class="OKXOrderData", detail="Missing required field: ordId")
        assert "OKXOrderData" in str(exc)
        assert "Missing required field: ordId" in str(exc)


class TestRateLimitError:
    """Test RateLimitError exception"""

    def test_rate_limit_error_basic(self):
        """RateLimitError with minimal parameters"""
        exc = RateLimitError("BINANCE___SPOT")
        assert exc.exchange_name == "BINANCE___SPOT"
        assert exc.retry_after is None
        assert "rate limit exceeded" in str(exc)

    def test_rate_limit_error_with_retry_after(self):
        """RateLimitError includes retry_after"""
        exc = RateLimitError("BINANCE___SPOT", retry_after=60)
        assert exc.exchange_name == "BINANCE___SPOT"
        assert exc.retry_after == 60
        assert "retry after 60s" in str(exc)

    def test_rate_limit_error_with_detail(self):
        """RateLimitError includes detail message"""
        exc = RateLimitError("OKX___SPOT", retry_after=120, detail="IP banned")
        assert exc.exchange_name == "OKX___SPOT"
        assert exc.retry_after == 120
        assert "IP banned" in str(exc)

    def test_rate_limit_error_zero_retry_after(self):
        """RateLimitError with zero retry_after"""
        exc = RateLimitError("BINANCE___SPOT", retry_after=0)
        assert exc.retry_after == 0


class TestNetworkError:
    """Test NetworkError exception"""

    def test_network_error_basic(self):
        """NetworkError with minimal parameters"""
        exc = NetworkError("BINANCE___SPOT")
        assert exc.exchange_name == "BINANCE___SPOT"
        assert "network error" in str(exc)

    def test_network_error_with_detail(self):
        """NetworkError includes detail message"""
        exc = NetworkError("BINANCE___SPOT", detail="DNS resolution failed")
        assert exc.exchange_name == "BINANCE___SPOT"
        assert "DNS resolution failed" in str(exc)


class TestInvalidSymbolError:
    """Test InvalidSymbolError exception"""

    def test_invalid_symbol_error_basic(self):
        """InvalidSymbolError with minimal parameters"""
        exc = InvalidSymbolError("BINANCE___SPOT", "INVALID")
        assert exc.exchange_name == "BINANCE___SPOT"
        assert exc.symbol == "INVALID"
        assert "invalid symbol" in str(exc)
        assert "INVALID" in str(exc)

    def test_invalid_symbol_error_with_detail(self):
        """InvalidSymbolError includes detail message"""
        exc = InvalidSymbolError("OKX___SPOT", "BTC-INVALID", detail="Symbol not found")
        assert exc.exchange_name == "OKX___SPOT"
        assert exc.symbol == "BTC-INVALID"
        assert "Symbol not found" in str(exc)


class TestConfigurationError:
    """Test ConfigurationError exception"""

    def test_configuration_error_basic(self):
        """ConfigurationError with minimal parameters"""
        exc = ConfigurationError()
        assert "Configuration error" in str(exc)

    def test_configuration_error_with_detail(self):
        """ConfigurationError includes detail message"""
        exc = ConfigurationError(detail="Missing API key")
        assert "Missing API key" in str(exc)


class TestWebSocketError:
    """Test WebSocketError exception"""

    def test_websocket_error_basic(self):
        """WebSocketError with minimal parameters"""
        exc = WebSocketError("BINANCE___SPOT")
        assert exc.exchange_name == "BINANCE___SPOT"
        assert "WebSocket error" in str(exc)

    def test_websocket_error_with_detail(self):
        """WebSocketError includes detail message"""
        exc = WebSocketError("BINANCE___SPOT", detail="Connection closed unexpectedly")
        assert exc.exchange_name == "BINANCE___SPOT"
        assert "Connection closed unexpectedly" in str(exc)


class TestRequestFailedError:
    """Test RequestFailedError exception"""

    def test_request_failed_error_basic(self):
        """RequestFailedError with minimal parameters"""
        exc = RequestFailedError()
        assert "Request failed" in str(exc)

    def test_request_failed_error_with_venue(self):
        """RequestFailedError includes venue name"""
        exc = RequestFailedError(venue="BINANCE")
        assert "BINANCE" in str(exc)

    def test_request_failed_error_with_message(self):
        """RequestFailedError includes message"""
        exc = RequestFailedError(message="Service unavailable")
        assert "Service unavailable" in str(exc)

    def test_request_failed_error_with_status_code(self):
        """RequestFailedError includes HTTP status code"""
        exc = RequestFailedError(status_code=500)
        assert "HTTP 500" in str(exc)

    def test_request_failed_error_with_all_params(self):
        """RequestFailedError with all parameters"""
        exc = RequestFailedError(venue="OKX", message="Rate limit exceeded", status_code=429)
        assert exc.venue == "OKX"
        assert exc.status_code == 429
        assert "OKX" in str(exc)
        assert "Rate limit exceeded" in str(exc)
        assert "HTTP 429" in str(exc)


class TestExceptionRaising:
    """Test that exceptions can be raised and caught correctly"""

    def test_raise_and_catch_btapi_error(self):
        """Can raise and catch BtApiError"""
        with pytest.raises(BtApiError) as exc_info:
            raise BtApiError("Test error")
        assert "Test error" in str(exc_info.value)

    def test_raise_and_catch_exchange_not_found(self):
        """Can raise and catch ExchangeNotFoundError"""
        with pytest.raises(ExchangeNotFoundError) as exc_info:
            raise ExchangeNotFoundError("TEST___SPOT")
        assert exc_info.value.exchange_name == "TEST___SPOT"

    def test_catch_order_error_parent(self):
        """Can catch InsufficientBalanceError as OrderError"""
        with pytest.raises(OrderError):
            raise InsufficientBalanceError("BINANCE___SPOT")

    def test_catch_connection_error_parent(self):
        """Can catch AuthenticationError as ExchangeConnectionError"""
        with pytest.raises(ExchangeConnectionError):
            raise AuthenticationError("BINANCE___SPOT")

    def test_catch_specific_and_generic(self):
        """Can catch specific exception as generic BtApiError"""
        with pytest.raises(BtApiError) as exc_info:
            raise RateLimitError("BINANCE___SPOT", retry_after=60)
        assert isinstance(exc_info.value, RateLimitError)


class TestExceptionAttributes:
    """Test exception attributes are properly set"""

    def test_exchange_not_found_attributes(self):
        """ExchangeNotFoundError has correct attributes"""
        exc = ExchangeNotFoundError("TEST", available=["A", "B"])
        assert hasattr(exc, "exchange_name")
        assert exc.exchange_name == "TEST"

    def test_request_timeout_attributes(self):
        """RequestTimeoutError has correct attributes"""
        exc = RequestTimeoutError("TEST", url="http://test.com", timeout=30)
        assert hasattr(exc, "exchange_name")
        assert hasattr(exc, "url")
        assert hasattr(exc, "timeout")
        assert exc.exchange_name == "TEST"
        assert exc.url == "http://test.com"
        assert exc.timeout == 30

    def test_insufficient_balance_attributes(self):
        """InsufficientBalanceError has correct attributes"""
        exc = InsufficientBalanceError("TEST", required=10.0, available=5.0)
        assert hasattr(exc, "required")
        assert hasattr(exc, "available")
        assert exc.required == 10.0
        assert exc.available == 5.0

    def test_order_not_found_attributes(self):
        """OrderNotFoundError has correct attributes"""
        exc = OrderNotFoundError("TEST", order_id="12345")
        assert hasattr(exc, "order_id")
        assert exc.order_id == "12345"

    def test_rate_limit_attributes(self):
        """RateLimitError has correct attributes"""
        exc = RateLimitError("TEST", retry_after=60)
        assert hasattr(exc, "retry_after")
        assert exc.retry_after == 60


class TestEdgeCases:
    """Test edge cases and special scenarios"""

    def test_exception_with_unicode_exchange_name(self):
        """Exception handles unicode exchange names"""
        exc = ExchangeNotFoundError("测试___SPOT")
        assert exc.exchange_name == "测试___SPOT"
        assert "测试" in str(exc)

    def test_exception_with_very_long_message(self):
        """Exception handles long detail messages"""
        long_detail = "A" * 1000
        exc = RequestError("TEST", detail=long_detail)
        assert long_detail in str(exc)

    def test_exception_with_special_characters(self):
        """Exception handles special characters"""
        exc = InvalidSymbolError("TEST", "BTC@USDT#123", detail="Invalid chars: \n\t")
        assert "BTC@USDT#123" in str(exc)

    def test_multiple_exception_inheritance(self):
        """Test exception inheritance chain"""
        assert issubclass(InsufficientBalanceError, OrderError)
        assert issubclass(InsufficientBalanceError, BtApiError)
        assert issubclass(InsufficientBalanceError, Exception)

    def test_exception_str_representation(self):
        """All exceptions have proper string representation"""
        exceptions = [
            BtApiError("test"),
            ExchangeNotFoundError("TEST"),
            ExchangeConnectionError("TEST"),
            AuthenticationError("TEST"),
            RequestTimeoutError("TEST"),
            RequestError("TEST"),
            OrderError("TEST"),
            SubscribeError("TEST"),
            DataParseError(),
            RateLimitError("TEST"),
            NetworkError("TEST"),
            InvalidSymbolError("TEST", "SYM"),
            InsufficientBalanceError("TEST"),
            InvalidOrderError("TEST"),
            OrderNotFoundError("TEST", "123"),
            ConfigurationError(),
            WebSocketError("TEST"),
            RequestFailedError(),
        ]
        for exc in exceptions:
            assert str(exc) != ""
            assert len(str(exc)) > 0
