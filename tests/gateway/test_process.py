"""Tests for gateway/process.py."""



class TestProcess:
    """Tests for gateway process."""

    def test_module_exists(self):
        """Test module can be imported."""
        from bt_api_py.gateway import process

        assert process is not None
