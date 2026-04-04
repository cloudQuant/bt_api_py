"""Tests for gateway/client.py."""

import pytest


class TestClient:
    """Tests for gateway client."""

    def test_module_exists(self):
        """Test module can be imported."""
        from bt_api_py.gateway import client
        assert client is not None
