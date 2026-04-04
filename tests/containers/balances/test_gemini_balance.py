"""Tests for Gemini balance containers."""

import pytest

from bt_api_py.containers.balances.gemini_balance import (
    GeminiRequestBalanceData,
    GeminiSpotWssBalanceData,
)


class TestGeminiRequestBalanceData:
    """Tests for GeminiRequestBalanceData."""

    def test_init(self):
        """Test initialization."""
        balance = GeminiRequestBalanceData({})

        assert balance.exchange_name == "GEMINI"
        assert balance.is_rest is True

    def test_parse_rest_data(self):
        """Test parsing REST API data."""
        data = {
            "currency": "BTC",
            "amount": "1.5",
            "available": "1.0",
            "availableForWithdrawal": "1.0",
        }
        balance = GeminiRequestBalanceData(data)

        assert balance.currency == "BTC"
        assert balance.amount == 1.5
        assert balance.available == 1.0

    def test_parse_rest_data_list(self):
        """Test parsing REST API data as list."""
        data = [
            {
                "currency": "BTC",
                "amount": "1.5",
                "available": "1.0",
            }
        ]
        balance = GeminiRequestBalanceData(data)

        assert balance.currency == "BTC"
        assert balance.amount == 1.5

    def test_get_currency(self):
        """Test get_currency."""
        data = {"currency": "BTC", "amount": "1.5"}
        balance = GeminiRequestBalanceData(data)

        assert balance.get_currency() == "BTC"

    def test_to_dict(self):
        """Test to_dict."""
        data = {"currency": "BTC", "amount": "1.5"}
        balance = GeminiRequestBalanceData(data)
        result = balance.to_dict()

        assert result["currency"] == "BTC"
        assert result["amount"] == 1.5

    def test_str_representation(self):
        """Test __str__ method."""
        data = {"currency": "BTC", "amount": "1.5"}
        balance = GeminiRequestBalanceData(data)
        result = str(balance)

        assert "GeminiBalance" in result
        assert "BTC" in result


class TestGeminiSpotWssBalanceData:
    """Tests for GeminiSpotWssBalanceData."""

    def test_init(self):
        """Test initialization."""
        balance = GeminiSpotWssBalanceData({})

        assert balance.exchange_name == "GEMINI"
        assert balance.is_rest is False

    def test_parse_wss_data(self):
        """Test parsing WebSocket data."""
        data = {
            "balances": [
                {
                    "currency": "BTC",
                    "amount": "1.5",
                    "available": "1.0",
                }
            ]
        }
        balance = GeminiSpotWssBalanceData(data)

        assert balance.currency == "BTC"
        assert balance.amount == 1.5
