"""Tests for gateway/protocol.py."""



class TestProtocol:
    """Tests for gateway protocol."""

    def test_module_exists(self):
        """Test module can be imported."""
        from bt_api_py.gateway import protocol

        assert protocol is not None
