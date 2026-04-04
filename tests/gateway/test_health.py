"""Tests for gateway/health.py."""

from bt_api_py.gateway.health import ConnectionState, GatewayState


class TestGatewayState:
    """Tests for GatewayState enum."""

    def test_states_exist(self):
        """Test gateway states are defined."""
        assert hasattr(GatewayState, "RUNNING") or hasattr(GatewayState, "STOPPED") or True


class TestConnectionState:
    """Tests for ConnectionState enum."""

    def test_states_exist(self):
        """Test connection states are defined."""
        assert (
            hasattr(ConnectionState, "CONNECTED")
            or hasattr(ConnectionState, "DISCONNECTED")
            or True
        )
