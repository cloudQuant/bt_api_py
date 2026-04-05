from __future__ import annotations

import os.path

import pytest

from bt_api_py.functions.utils import get_package_path, read_account_config, read_yaml_file

pytestmark = [pytest.mark.integration, pytest.mark.network]


def test_get_package_path():
    path = get_package_path("numpy")
    assert path is not None
    path2 = get_package_path("bt_api_py")
    assert "bt_api_py" in path2


@pytest.mark.auth_account
def test_read_yaml_file():
    path = get_package_path("bt_api_py")
    config_path = os.path.join(path, "configs", "account_config.yaml")
    if os.path.exists(config_path):
        content = read_yaml_file("account_config.yaml")
        assert content is not None


@pytest.mark.auth_account
def test_read_account_config():
    config = read_account_config()
    assert config is not None
    assert "okx" in config
    assert "binance" in config
    assert "ib_web" in config
    assert "public_key" in config["okx"]
    assert "private_key" in config["okx"]
    assert "passphrase" in config["okx"]
    assert "public_key" in config["binance"]
    assert "private_key" in config["binance"]
    assert "username" in config["ib_web"]
    assert "password" in config["ib_web"]
    assert "login_mode" in config["ib_web"]
    assert "login_browser" in config["ib_web"]
    assert "cookie_output" in config["ib_web"]
    assert "cookie_path" in config["ib_web"]


if __name__ == "__main__":
    test_get_package_path()
    test_read_yaml_file()
    test_read_account_config()
