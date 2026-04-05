"""Tests for bt_api_py/error.py - Unified error framework."""

from __future__ import annotations

import pytest

from bt_api_py.error import (
    ErrorCategory,
    ErrorTranslator,
    ServerError,
    UnifiedAuthError,
    UnifiedError,
    UnifiedErrorCode,
    UnifiedRateLimitError,
    UnifiedRequestFailedError,
)


class TestErrorCategory:
    """Tests for ErrorCategory enum."""

    def test_all_categories_exist(self):
        """Test all expected categories exist."""
        assert ErrorCategory.NETWORK == "network"
        assert ErrorCategory.AUTH == "auth"
        assert ErrorCategory.RATE_LIMIT == "rate_limit"
        assert ErrorCategory.BUSINESS == "business"
        assert ErrorCategory.SYSTEM == "system"
        assert ErrorCategory.CAPABILITY == "capability"
        assert ErrorCategory.VALIDATION == "validation"
        assert ErrorCategory.API == "api"
        assert ErrorCategory.ORDER == "order"
        assert ErrorCategory.TRADE == "trade"
        assert ErrorCategory.ACCOUNT == "account"

    def test_category_is_string_enum(self):
        """Test that ErrorCategory is a StrEnum."""
        assert isinstance(ErrorCategory.NETWORK, str)


class TestUnifiedErrorCode:
    """Tests for UnifiedErrorCode enum."""

    def test_network_error_codes(self):
        """Test network error codes are in 1xxx range."""
        assert UnifiedErrorCode.NETWORK_TIMEOUT.value == 1001
        assert UnifiedErrorCode.NETWORK_DISCONNECTED.value == 1002
        assert UnifiedErrorCode.DNS_ERROR.value == 1003
        assert UnifiedErrorCode.CONNECTION_REFUSED.value == 1004

    def test_auth_error_codes(self):
        """Test auth error codes are in 2xxx range."""
        assert UnifiedErrorCode.INVALID_API_KEY.value == 2001
        assert UnifiedErrorCode.INVALID_SIGNATURE.value == 2002
        assert UnifiedErrorCode.EXPIRED_TIMESTAMP.value == 2003
        assert UnifiedErrorCode.PERMISSION_DENIED.value == 2004
        assert UnifiedErrorCode.SESSION_EXPIRED.value == 2005

    def test_rate_limit_error_codes(self):
        """Test rate limit error codes are in 3xxx range."""
        assert UnifiedErrorCode.RATE_LIMIT_EXCEEDED.value == 3001
        assert UnifiedErrorCode.IP_BANNED.value == 3002
        assert UnifiedErrorCode.TOO_MANY_REQUESTS.value == 3003

    def test_business_error_codes(self):
        """Test business error codes are in 4xxx range."""
        assert UnifiedErrorCode.INVALID_SYMBOL.value == 4001
        assert UnifiedErrorCode.INVALID_PRICE.value == 4002
        assert UnifiedErrorCode.INVALID_VOLUME.value == 4003
        assert UnifiedErrorCode.INSUFFICIENT_BALANCE.value == 4004
        assert UnifiedErrorCode.ORDER_NOT_FOUND.value == 4006

    def test_system_error_codes(self):
        """Test system error codes are in 5xxx range."""
        assert UnifiedErrorCode.EXCHANGE_MAINTENANCE.value == 5001
        assert UnifiedErrorCode.EXCHANGE_OVERLOADED.value == 5002
        assert UnifiedErrorCode.INTERNAL_ERROR.value == 5003
        assert UnifiedErrorCode.UNSUPPORTED_OPERATION.value == 5004

    def test_capability_error_codes(self):
        """Test capability error codes are in 6xxx range."""
        assert UnifiedErrorCode.NOT_SUPPORTED.value == 6001
        assert UnifiedErrorCode.NOT_IMPLEMENTED.value == 6002

    def test_validation_error_codes(self):
        """Test validation error codes are in 7xxx range."""
        assert UnifiedErrorCode.INVALID_PARAMETER.value == 7001
        assert UnifiedErrorCode.MISSING_PARAMETER.value == 7002
        assert UnifiedErrorCode.PARAMETER_OUT_OF_RANGE.value == 7003


class TestUnifiedError:
    """Tests for UnifiedError dataclass."""

    def test_init_basic(self):
        """Test basic initialization."""
        error = UnifiedError(
            code=UnifiedErrorCode.INVALID_SYMBOL,
            category=ErrorCategory.BUSINESS,
            venue="binance",
            message="Invalid trading pair",
        )

        assert error.code == UnifiedErrorCode.INVALID_SYMBOL
        assert error.category == ErrorCategory.BUSINESS
        assert error.venue == "binance"
        assert error.message == "Invalid trading pair"
        assert error.original_error is None
        assert error.context == {}

    def test_init_with_all_fields(self):
        """Test initialization with all fields."""
        error = UnifiedError(
            code=UnifiedErrorCode.INSUFFICIENT_BALANCE,
            category=ErrorCategory.BUSINESS,
            venue="okx",
            message="Not enough USDT",
            original_error="-2010: Insufficient balance",
            context={"asset": "USDT", "required": 100.0, "available": 50.0},
        )

        assert error.code == UnifiedErrorCode.INSUFFICIENT_BALANCE
        assert error.original_error == "-2010: Insufficient balance"
        assert error.context["asset"] == "USDT"

    def test_str_representation(self):
        """Test string representation."""
        error = UnifiedError(
            code=UnifiedErrorCode.RATE_LIMIT_EXCEEDED,
            category=ErrorCategory.RATE_LIMIT,
            venue="binance",
            message="Too many requests",
        )

        assert str(error) == "[binance] RATE_LIMIT_EXCEEDED: Too many requests"

    def test_repr_representation(self):
        """Test repr representation."""
        error = UnifiedError(
            code=UnifiedErrorCode.INVALID_API_KEY,
            category=ErrorCategory.AUTH,
            venue="kraken",
            message="Bad key",
        )

        assert "UnifiedError" in repr(error)
        assert "INVALID_API_KEY" in repr(error)
        assert "kraken" in repr(error)

    def test_to_dict(self):
        """Test serialization to dict."""
        error = UnifiedError(
            code=UnifiedErrorCode.ORDER_NOT_FOUND,
            category=ErrorCategory.BUSINESS,
            venue="bybit",
            message="Order does not exist",
            original_error="110001: Order not found",
            context={"order_id": "12345"},
        )

        result = error.to_dict()

        assert result["code"] == 4006
        assert result["code_name"] == "ORDER_NOT_FOUND"
        assert result["category"] == "business"
        assert result["venue"] == "bybit"
        assert result["message"] == "Order does not exist"
        assert result["original_error"] == "110001: Order not found"
        assert result["context"]["order_id"] == "12345"

    def test_inherits_from_btapierror(self):
        """Test that UnifiedError inherits from BtApiError."""
        from bt_api_py.exceptions import BtApiError

        error = UnifiedError(
            code=UnifiedErrorCode.INTERNAL_ERROR,
            category=ErrorCategory.SYSTEM,
            venue="test",
            message="test",
        )

        assert isinstance(error, BtApiError)


class TestUnifiedRateLimitError:
    """Tests for UnifiedRateLimitError."""

    def test_init_basic(self):
        """Test basic initialization."""
        error = UnifiedRateLimitError(venue="binance")

        assert error.code == UnifiedErrorCode.RATE_LIMIT_EXCEEDED
        assert error.category == ErrorCategory.RATE_LIMIT
        assert error.venue == "binance"
        assert error.message == "Rate limit exceeded"
        assert error.exchange_name == "binance"
        assert error.retry_after is None

    def test_init_with_response(self):
        """Test initialization with response."""
        error = UnifiedRateLimitError(
            venue="okx",
            response={"code": "50011", "msg": "Rate limit"},
            message="API rate limit exceeded",
        )

        assert error.message == "API rate limit exceeded"
        assert error.context["raw_response"]["code"] == "50011"

    def test_inherits_from_ratelimiterror(self):
        """Test that it inherits from RateLimitError."""
        from bt_api_py.exceptions import RateLimitError

        error = UnifiedRateLimitError(venue="test")

        assert isinstance(error, RateLimitError)
        assert isinstance(error, UnifiedError)


class TestUnifiedAuthError:
    """Tests for UnifiedAuthError."""

    def test_init_basic(self):
        """Test basic initialization."""
        error = UnifiedAuthError(venue="binance")

        assert error.code == UnifiedErrorCode.INVALID_API_KEY
        assert error.category == ErrorCategory.AUTH
        assert error.venue == "binance"
        assert error.message == "Authentication failed"
        assert error.exchange_name == "binance"

    def test_init_with_response(self):
        """Test initialization with response."""
        error = UnifiedAuthError(
            venue="kraken",
            response={"error": "EAPI:Invalid key"},
            message="Invalid API credentials",
        )

        assert error.message == "Invalid API credentials"
        assert error.context["raw_response"]["error"] == "EAPI:Invalid key"

    def test_inherits_from_authenticationerror(self):
        """Test that it inherits from AuthenticationError."""
        from bt_api_py.exceptions import AuthenticationError

        error = UnifiedAuthError(venue="test")

        assert isinstance(error, AuthenticationError)
        assert isinstance(error, UnifiedError)


class TestServerError:
    """Tests for ServerError."""

    def test_init_basic(self):
        """Test basic initialization."""
        error = ServerError(venue="binance")

        assert error.code == UnifiedErrorCode.INTERNAL_ERROR
        assert error.category == ErrorCategory.SYSTEM
        assert error.venue == "binance"
        assert error.message == "Server error"
        assert error.context["status"] == 500

    def test_init_with_status_and_response(self):
        """Test initialization with status and response."""
        error = ServerError(
            venue="okx",
            status=503,
            response={"code": "50001", "msg": "Maintenance"},
            message="Exchange under maintenance",
        )

        assert error.message == "Exchange under maintenance"
        assert error.context["status"] == 503
        assert error.context["raw_response"]["code"] == "50001"


class TestUnifiedRequestFailedError:
    """Tests for UnifiedRequestFailedError."""

    def test_init_basic(self):
        """Test basic initialization."""
        error = UnifiedRequestFailedError(venue="binance")

        assert error.code == UnifiedErrorCode.INTERNAL_ERROR
        assert error.category == ErrorCategory.SYSTEM
        assert error.venue == "binance"
        assert error.message == "Request failed"
        assert error.exchange_name == "binance"
        assert error.status_code == 0

    def test_init_with_status_and_response(self):
        """Test initialization with status and response."""
        error = UnifiedRequestFailedError(
            venue="kraken",
            status=504,
            response={"error": "ETimedOut"},
            message="Request timed out",
        )

        assert error.message == "Request timed out"
        assert error.status_code == 504
        assert error.context["status"] == 504

    def test_inherits_from_requestfailederror(self):
        """Test that it inherits from RequestFailedError."""
        from bt_api_py.exceptions import RequestFailedError

        error = UnifiedRequestFailedError(venue="test")

        assert isinstance(error, RequestFailedError)
        assert isinstance(error, UnifiedError)


class TestErrorTranslator:
    """Tests for ErrorTranslator base class."""

    def test_http_status_map_exists(self):
        """Test HTTP status map exists with expected values."""
        assert 400 in ErrorTranslator.HTTP_STATUS_MAP
        assert 401 in ErrorTranslator.HTTP_STATUS_MAP
        assert 403 in ErrorTranslator.HTTP_STATUS_MAP
        assert 404 in ErrorTranslator.HTTP_STATUS_MAP
        assert 429 in ErrorTranslator.HTTP_STATUS_MAP
        assert 500 in ErrorTranslator.HTTP_STATUS_MAP
        assert 503 in ErrorTranslator.HTTP_STATUS_MAP
        assert 504 in ErrorTranslator.HTTP_STATUS_MAP

    def test_translate_with_error_map(self):
        """Test translation using ERROR_MAP."""

        class CustomTranslator(ErrorTranslator):
            ERROR_MAP = {
                "INVALID_SYMBOL": (UnifiedErrorCode.INVALID_SYMBOL, "Symbol not found"),
            }

        error = CustomTranslator.translate(
            {"code": "INVALID_SYMBOL", "msg": "BTCUSDT not supported"},
            venue="test",
        )

        assert error is not None
        assert error.code == UnifiedErrorCode.INVALID_SYMBOL
        assert error.venue == "test"
        assert "not supported" in error.message

    def test_translate_with_none_in_error_map(self):
        """Test translation when ERROR_MAP returns None (not an error)."""

        class CustomTranslator(ErrorTranslator):
            ERROR_MAP = {
                0: (None, "Success"),
            }

        error = CustomTranslator.translate(
            {"code": 0, "msg": "Success"},
            venue="test",
        )

        assert error is None

    def test_translate_with_http_status(self):
        """Test translation using HTTP status code."""
        error = ErrorTranslator.translate(
            {"status": 429, "msg": "Too many requests"},
            venue="binance",
        )

        assert error is not None
        assert error.code == UnifiedErrorCode.RATE_LIMIT_EXCEEDED
        assert error.category == ErrorCategory.RATE_LIMIT

    def test_translate_with_unknown_error(self):
        """Test translation for unknown error."""
        error = ErrorTranslator.translate(
            {"code": "UNKNOWN_CODE", "msg": "Something went wrong"},
            venue="test",
        )

        assert error is not None
        assert error.code == UnifiedErrorCode.INTERNAL_ERROR
        assert error.category == ErrorCategory.SYSTEM

    def test_translate_with_empty_error(self):
        """Test translation for empty error dict."""
        error = ErrorTranslator.translate({}, venue="test")

        assert error is not None
        assert error.code == UnifiedErrorCode.INTERNAL_ERROR
        assert error.message == "Unknown error"

    def test_get_category_network(self):
        """Test _get_category for network errors."""
        category = ErrorTranslator._get_category(UnifiedErrorCode.NETWORK_TIMEOUT)
        assert category == ErrorCategory.NETWORK

    def test_get_category_auth(self):
        """Test _get_category for auth errors."""
        category = ErrorTranslator._get_category(UnifiedErrorCode.INVALID_API_KEY)
        assert category == ErrorCategory.AUTH

    def test_get_category_rate_limit(self):
        """Test _get_category for rate limit errors."""
        category = ErrorTranslator._get_category(UnifiedErrorCode.RATE_LIMIT_EXCEEDED)
        assert category == ErrorCategory.RATE_LIMIT

    def test_get_category_business(self):
        """Test _get_category for business errors."""
        category = ErrorTranslator._get_category(UnifiedErrorCode.INVALID_SYMBOL)
        assert category == ErrorCategory.BUSINESS

    def test_get_category_system(self):
        """Test _get_category for system errors."""
        category = ErrorTranslator._get_category(UnifiedErrorCode.INTERNAL_ERROR)
        assert category == ErrorCategory.SYSTEM

    def test_get_category_capability(self):
        """Test _get_category for capability errors."""
        category = ErrorTranslator._get_category(UnifiedErrorCode.NOT_SUPPORTED)
        assert category == ErrorCategory.CAPABILITY

    def test_get_category_validation(self):
        """Test _get_category for validation errors."""
        category = ErrorTranslator._get_category(UnifiedErrorCode.INVALID_PARAMETER)
        assert category == ErrorCategory.VALIDATION


class TestErrorTranslatorImports:
    """Tests for lazy translator imports."""

    def test_import_binance_translator(self):
        """Test importing BinanceErrorTranslator."""
        from bt_api_py.error import BinanceErrorTranslator

        assert BinanceErrorTranslator is not None

    def test_import_okx_translator(self):
        """Test importing OKXErrorTranslator."""
        from bt_api_py.error import OKXErrorTranslator

        assert OKXErrorTranslator is not None

    def test_import_bybit_translator(self):
        """Test importing BybitErrorTranslator."""
        from bt_api_py.error import BybitErrorTranslator

        assert BybitErrorTranslator is not None

    def test_import_bitget_translator(self):
        """Test importing BitgetErrorTranslator."""
        from bt_api_py.error import BitgetErrorTranslator

        assert BitgetErrorTranslator is not None

    def test_import_kucoin_translator(self):
        """Test importing KuCoinErrorTranslator."""
        from bt_api_py.error import KuCoinErrorTranslator

        assert KuCoinErrorTranslator is not None

    def test_import_ctp_translator(self):
        """Test importing CTPErrorTranslator."""
        from bt_api_py.error import CTPErrorTranslator

        assert CTPErrorTranslator is not None

    def test_import_gemini_translator(self):
        """Test importing GeminiErrorTranslator."""
        from bt_api_py.error import GeminiErrorTranslator

        assert GeminiErrorTranslator is not None

    def test_import_kraken_translator(self):
        """Test importing KrakenErrorTranslator."""
        from bt_api_py.error import KrakenErrorTranslator

        assert KrakenErrorTranslator is not None

    def test_import_upbit_translator(self):
        """Test importing UpbitErrorTranslator."""
        from bt_api_py.error import UpbitErrorTranslator

        assert UpbitErrorTranslator is not None

    def test_import_ib_web_translator(self):
        """Test importing IBWebErrorTranslator."""
        from bt_api_py.error import IBWebErrorTranslator

        assert IBWebErrorTranslator is not None

    def test_import_bitfinex_translator(self):
        """Test importing BitfinexErrorTranslator."""
        from bt_api_py.error import BitfinexErrorTranslator

        assert BitfinexErrorTranslator is not None

    def test_invalid_import_raises_attributeerror(self):
        """Test that invalid import raises AttributeError."""
        import bt_api_py.error as error_module

        with pytest.raises(AttributeError, match="has no attribute"):
            _ = error_module.NonExistentTranslator
