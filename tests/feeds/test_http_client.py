"""Tests for feeds/http_client.py with mocking."""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest


class TestHttpClient:
    """Tests for HttpClient with mocked httpx."""

    def test_init_with_defaults(self):
        """Test HttpClient initialization with defaults."""
        with patch("bt_api_py.feeds.http_client.httpx") as mock_httpx:
            mock_client = MagicMock()
            mock_httpx.Client.return_value = mock_client
            mock_httpx.Limits.return_value = MagicMock()

            from bt_api_py.feeds.http_client import HttpClient

            client = HttpClient(venue="test")
            assert client._venue == "test"
            assert client.timeout == 10.0

    def test_init_with_custom_params(self):
        """Test HttpClient with custom parameters."""
        with patch("bt_api_py.feeds.http_client.httpx") as mock_httpx:
            mock_client = MagicMock()
            mock_httpx.Client.return_value = mock_client
            mock_httpx.Limits.return_value = MagicMock()

            from bt_api_py.feeds.http_client import HttpClient

            client = HttpClient(venue="binance", timeout=30.0, max_connections=50)
            assert client._venue == "binance"
            assert client.timeout == 30.0

    def test_request_success(self):
        """Test successful HTTP request."""
        with patch("bt_api_py.feeds.http_client.httpx") as mock_httpx:
            mock_response = MagicMock()
            mock_response.status_code = 200
            mock_response.is_success = True
            mock_response.json.return_value = {"result": "success"}
            mock_response.headers = {}

            mock_client = MagicMock()
            mock_client.request.return_value = mock_response
            mock_httpx.Client.return_value = mock_client
            mock_httpx.Limits.return_value = MagicMock()

            from bt_api_py.feeds.http_client import HttpClient

            client = HttpClient(venue="test")
            result = client.request("GET", "https://api.example.com/test")

            # _process_response returns JSON directly for 2xx success
            assert result == {"result": "success"}

    def test_request_with_params(self):
        """Test HTTP request with query params."""
        with patch("bt_api_py.feeds.http_client.httpx") as mock_httpx:
            mock_response = MagicMock()
            mock_response.status_code = 200
            mock_response.is_success = True
            mock_response.json.return_value = {"data": []}
            mock_response.headers = {}

            mock_client = MagicMock()
            mock_client.request.return_value = mock_response
            mock_httpx.Client.return_value = mock_client
            mock_httpx.Limits.return_value = MagicMock()

            from bt_api_py.feeds.http_client import HttpClient

            client = HttpClient(venue="test")
            _ = client.request("GET", "https://api.example.com/test", params={"symbol": "BTC"})

            mock_client.request.assert_called_once()
            call_kwargs = mock_client.request.call_args[1]
            assert call_kwargs["params"] == {"symbol": "BTC"}

    def test_request_with_headers(self):
        """Test HTTP request with custom headers."""
        with patch("bt_api_py.feeds.http_client.httpx") as mock_httpx:
            mock_response = MagicMock()
            mock_response.status_code = 200
            mock_response.is_success = True
            mock_response.json.return_value = {}
            mock_response.headers = {}

            mock_client = MagicMock()
            mock_client.request.return_value = mock_response
            mock_httpx.Client.return_value = mock_client
            mock_httpx.Limits.return_value = MagicMock()

            from bt_api_py.feeds.http_client import HttpClient

            client = HttpClient(venue="test")
            client.request("GET", "https://api.example.com/test", headers={"X-Custom": "value"})

            mock_client.request.assert_called_once()

    def test_request_timeout_error(self):
        """Test request timeout handling."""
        with patch("bt_api_py.feeds.http_client.httpx") as mock_httpx:
            mock_client = MagicMock()
            mock_client.request.side_effect = mock_httpx.TimeoutException("timeout")
            mock_httpx.Client.return_value = mock_client
            mock_httpx.Limits.return_value = MagicMock()

            from bt_api_py.exceptions import RequestFailedError
            from bt_api_py.feeds.http_client import HttpClient

            client = HttpClient(venue="test")
            with pytest.raises(RequestFailedError):
                client.request("GET", "https://api.example.com/test")

    def test_request_connection_error(self):
        """Test connection error handling."""
        with patch("bt_api_py.feeds.http_client.httpx") as mock_httpx:
            mock_client = MagicMock()
            mock_client.request.side_effect = mock_httpx.ConnectError("connection failed")
            mock_httpx.Client.return_value = mock_client
            mock_httpx.Limits.return_value = MagicMock()

            from bt_api_py.exceptions import RequestFailedError
            from bt_api_py.feeds.http_client import HttpClient

            client = HttpClient(venue="test")
            with pytest.raises(RequestFailedError):
                client.request("GET", "https://api.example.com/test")

    def test_proxy_configuration_dict(self):
        """Test proxy configuration with dict."""
        with patch("bt_api_py.feeds.http_client.httpx") as mock_httpx:
            mock_client = MagicMock()
            mock_httpx.Client.return_value = mock_client
            mock_httpx.Limits.return_value = MagicMock()

            from bt_api_py.feeds.http_client import HttpClient

            client = HttpClient(venue="test", proxies={"https": "http://proxy:8080"})
            assert client is not None

    def test_proxy_configuration_string(self):
        """Test proxy configuration with string."""
        with patch("bt_api_py.feeds.http_client.httpx") as mock_httpx:
            mock_client = MagicMock()
            mock_httpx.Client.return_value = mock_client
            mock_httpx.Limits.return_value = MagicMock()

            from bt_api_py.feeds.http_client import HttpClient

            client = HttpClient(venue="test", proxies="http://proxy:8080")
            assert client is not None
