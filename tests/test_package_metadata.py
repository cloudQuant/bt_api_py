"""Tests for package metadata exposed to users."""

import bt_api_py
from bt_api_py._version import __version__


def test_package_exports_version() -> None:
    assert bt_api_py.__version__ == __version__
    assert bt_api_py.__version__
