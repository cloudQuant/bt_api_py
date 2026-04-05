"""Tests for websocket/exchange_adapters.py."""

from __future__ import annotations

from bt_api_py.websocket.exchange_adapters import AuthenticationType, ExchangeType


class TestExchangeType:
    """Tests for ExchangeType enum."""

    def test_types_exist(self):
        """Test all exchange types are defined."""
        assert ExchangeType.SPOT.value == "spot"
        assert ExchangeType.FUTURES.value == "futures"
        assert ExchangeType.SWAP.value == "swap"
        assert ExchangeType.OPTIONS.value == "options"


class TestAuthenticationType:
    """Tests for AuthenticationType enum."""

    def test_types_exist(self):
        """Test all authentication types are defined."""
        assert AuthenticationType.NONE.value == "none"
        assert AuthenticationType.API_KEY.value == "api_key"
        assert AuthenticationType.API_KEY_SECRET.value == "api_key_secret"
        assert AuthenticationType.JWT.value == "jwt"
