"""Tests for gateway/subscription_manager.py."""



class TestSubscriptionManager:
    """Tests for subscription manager."""

    def test_module_exists(self):
        """Test module can be imported."""
        from bt_api_py.gateway import subscription_manager

        assert subscription_manager is not None
