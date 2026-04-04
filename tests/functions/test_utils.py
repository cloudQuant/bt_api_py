"""Tests for functions/utils.py."""

import pytest


class TestUtils:
    """Tests for functions/utils.py."""

    def test_module_exists(self):
        """Test module can be imported."""
        from bt_api_py.functions import utils
        assert utils is not None
