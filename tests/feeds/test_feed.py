"""Tests for Feed base class."""

from __future__ import annotations

import pytest

from bt_api_py.feeds.feed import Feed


class TestFeed:
    """Tests for Feed class."""

    def test_init_defaults(self):
        """Test default initialization."""
        feed = Feed()

        assert feed.data_queue is None
        assert feed.exchange_name == ""
        assert feed.proxies is None

    def test_init_with_params(self):
        """Test initialization with parameters."""
        feed = Feed(
            data_queue="test_queue",
            exchange_name="BINANCE",
            proxies={"http": "http://proxy:8080"},
            timeout=30.0,
        )

        assert feed.data_queue == "test_queue"
        assert feed.exchange_name == "BINANCE"
        assert feed.proxies == {"http": "http://proxy:8080"}

    def test_is_sensitive_key_apikey(self):
        """Test sensitive key detection for apikey."""
        feed = Feed()

        assert feed._is_sensitive_key("apiKey") is True
        assert feed._is_sensitive_key("API_KEY") is True
        assert feed._is_sensitive_key("api-key") is True

    def test_is_sensitive_key_secret(self):
        """Test sensitive key detection for secret."""
        feed = Feed()

        assert feed._is_sensitive_key("secret") is True
        assert feed._is_sensitive_key("SECRET") is True
        assert feed._is_sensitive_key("secretKey") is True

    def test_is_sensitive_key_token(self):
        """Test sensitive key detection for token."""
        feed = Feed()

        assert feed._is_sensitive_key("token") is True
        assert feed._is_sensitive_key("accessToken") is True
        assert feed._is_sensitive_key("refresh_token") is True

    def test_is_sensitive_key_password(self):
        """Test sensitive key detection for password."""
        feed = Feed()

        assert feed._is_sensitive_key("password") is True
        assert feed._is_sensitive_key("passphrase") is True

    def test_is_sensitive_key_normal(self):
        """Test non-sensitive key detection."""
        feed = Feed()

        assert feed._is_sensitive_key("symbol") is False
        assert feed._is_sensitive_key("quantity") is False
        assert feed._is_sensitive_key("price") is False
        assert feed._is_sensitive_key("order_id") is False

    def test_sanitize_for_log_dict(self):
        """Test sanitizing dict for logging."""
        feed = Feed()

        data = {
            "apiKey": "secret123",
            "symbol": "BTCUSDT",
            "quantity": 1.0,
        }

        result = feed._sanitize_for_log(data)

        assert result["apiKey"] == "***"
        assert result["symbol"] == "BTCUSDT"
        assert result["quantity"] == 1.0

    def test_sanitize_for_log_nested_dict(self):
        """Test sanitizing nested dict for logging."""
        feed = Feed()

        data = {
            "params": {
                "secret": "hidden",
                "value": 100,
            },
            "normal": "visible",
        }

        result = feed._sanitize_for_log(data)

        assert result["params"]["secret"] == "***"
        assert result["params"]["value"] == 100
        assert result["normal"] == "visible"

    def test_sanitize_for_log_list(self):
        """Test sanitizing list for logging."""
        feed = Feed()

        data = [
            {"apiKey": "secret1", "name": "item1"},
            {"apiKey": "secret2", "name": "item2"},
        ]

        result = feed._sanitize_for_log(data)

        assert result[0]["apiKey"] == "***"
        assert result[0]["name"] == "item1"
        assert result[1]["apiKey"] == "***"

    def test_sanitize_for_log_tuple(self):
        """Test sanitizing tuple for logging."""
        feed = Feed()

        data = ({"secret": "hidden"}, {"name": "visible"})

        result = feed._sanitize_for_log(data)

        assert result[0]["secret"] == "***"
        assert result[1]["name"] == "visible"
        assert isinstance(result, tuple)

    def test_sanitize_for_log_scalar(self):
        """Test sanitizing scalar values."""
        feed = Feed()

        assert feed._sanitize_for_log("string") == "string"
        assert feed._sanitize_for_log(123) == 123
        assert feed._sanitize_for_log(1.5) == 1.5
        assert feed._sanitize_for_log(None) is None

    def test_sanitize_url_for_log_no_query(self):
        """Test sanitizing URL without query."""
        feed = Feed()

        url = "https://api.binance.com/api/v3/ticker/price"
        result = feed._sanitize_url_for_log(url)

        assert result == url

    def test_sanitize_url_for_log_with_sensitive_query(self):
        """Test sanitizing URL with sensitive query params."""
        feed = Feed()

        url = (
            "https://api.binance.com/api/v3/order?symbol=BTCUSDT&apiKey=secret123&signature=abc123"
        )
        result = feed._sanitize_url_for_log(url)

        assert "symbol=BTCUSDT" in result
        # *** gets URL encoded to %2A%2A%2A
        assert "apiKey=%2A%2A%2A" in result or "apiKey=***" in result
        assert "signature=%2A%2A%2A" in result or "signature=***" in result
        assert "secret123" not in result

    def test_sanitize_url_for_log_non_string(self):
        """Test sanitizing non-string URL."""
        feed = Feed()

        assert feed._sanitize_url_for_log(None) is None
        assert feed._sanitize_url_for_log(123) == 123

    def test_sanitize_url_preserves_fragment(self):
        """Test that URL fragment is preserved."""
        feed = Feed()

        url = "https://api.example.com/path?token=secret#section"
        result = feed._sanitize_url_for_log(url)

        assert "#section" in result
        # *** gets URL encoded to %2A%2A%2A
        assert "token=%2A%2A%2A" in result or "token=***" in result


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
