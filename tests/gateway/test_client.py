"""Tests for gateway/client.py."""

from __future__ import annotations


class TestClient:
    """Tests for gateway client."""

    def test_module_exists(self):
        """Test module can be imported."""
        from bt_api_py.gateway import client

        assert client is not None
