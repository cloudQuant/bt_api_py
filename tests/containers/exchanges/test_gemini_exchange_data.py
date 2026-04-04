"""Tests for GeminiExchangeData container."""

import pytest

from bt_api_py.containers.exchanges.gemini_exchange_data import GeminiExchangeData


class TestGeminiExchangeData:
    """Tests for GeminiExchangeData."""

    def test_init(self):
        """Test initialization."""
        exchange = GeminiExchangeData()

        assert exchange.exchange_name == "GEMINI"
