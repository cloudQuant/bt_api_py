"""Tests for websocket/advanced_connection_manager.py."""

from __future__ import annotations

from bt_api_py.websocket.advanced_connection_manager import ConnectionState, ErrorCategory


class TestConnectionState:
    """Tests for ConnectionState enum."""

    def test_states_exist(self):
        """Test all connection states are defined."""
        assert ConnectionState.DISCONNECTED.value == "disconnected"
        assert ConnectionState.CONNECTING.value == "connecting"
        assert ConnectionState.CONNECTED.value == "connected"
        assert ConnectionState.AUTHENTICATED.value == "authenticated"
        assert ConnectionState.ERROR.value == "error"


class TestErrorCategory:
    """Tests for ErrorCategory enum."""

    def test_categories_exist(self):
        """Test all error categories are defined."""
        assert ErrorCategory.NETWORK.value == "network"
        assert ErrorCategory.AUTHENTICATION.value == "authentication"
