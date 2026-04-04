"""Tests for functions/async_send_message.py.

Note: Requires aiosmtplib dependency.
"""

import pytest

pytest.importorskip("aiosmtplib")

from bt_api_py.functions import async_send_message


class TestAsyncSendMessage:
    """Tests for async_send_message module."""

    def test_module_exists(self):
        """Test module can be imported."""
        assert async_send_message is not None
