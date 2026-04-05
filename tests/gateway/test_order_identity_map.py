"""Tests for gateway/order_identity_map.py."""



class TestOrderIdentityMap:
    """Tests for order identity map."""

    def test_module_exists(self):
        """Test module can be imported."""
        from bt_api_py.gateway import order_identity_map

        assert order_identity_map is not None
