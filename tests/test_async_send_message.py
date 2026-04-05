"""Tests for async_send_message module."""

import pytest

pytest.importorskip("aiosmtplib")

from bt_api_py.functions.async_send_message import FeishuManagerAsync


class TestFeishuManagerAsync:
    """Tests for FeishuManagerAsync class."""

    def test_init(self):
        """Test initialization."""
        manager = FeishuManagerAsync()

        assert manager.host == "https://open.larksuite.com/open-apis/bot/v2/hook/"

        # Cleanup
        manager.release()

    def test_gen_sign(self):
        """Test gen_sign method."""
        manager = FeishuManagerAsync()

        timestamp = 1705315800
        secret = "test_secret"
        sign = manager.gen_sign(timestamp, secret)

        assert isinstance(sign, str)
        assert len(sign) > 0

        # Cleanup
        manager.release()

    def test_gen_sign_deterministic(self):
        """Test that gen_sign produces same result for same inputs."""
        manager = FeishuManagerAsync()

        timestamp = 1705315800
        secret = "test_secret"
        sign1 = manager.gen_sign(timestamp, secret)
        sign2 = manager.gen_sign(timestamp, secret)

        assert sign1 == sign2

        # Cleanup
        manager.release()


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
