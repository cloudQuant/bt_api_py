"""Tests for feeds/base_stream.py."""

from bt_api_py.feeds.base_stream import BaseDataStream, ConnectionState


class TestBaseDataStream:
    """Tests for BaseDataStream."""

    def test_base_data_stream_exists(self):
        """Test BaseDataStream is defined."""
        assert BaseDataStream is not None


class TestConnectionState:
    """Tests for ConnectionState enum."""

    def test_states_exist(self):
        """Test connection states are defined."""
        assert ConnectionState is not None
