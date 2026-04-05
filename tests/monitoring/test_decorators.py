"""Tests for monitoring/decorators.py."""



class TestDecorators:
    """Tests for monitoring decorators."""

    def test_module_exists(self):
        """Test module can be imported."""
        from bt_api_py.monitoring import decorators

        assert decorators is not None
