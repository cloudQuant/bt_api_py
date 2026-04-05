"""Tests for functions/async_base.py."""


class TestAsyncBase:
    """Tests for async_base module."""

    def test_module_exists(self):
        """Test module can be imported."""
        from bt_api_py.functions import async_base

        assert async_base is not None
