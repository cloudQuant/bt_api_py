from __future__ import annotations

from typing import TYPE_CHECKING

import requests

if TYPE_CHECKING:
    import pytest

from bt_api_py.functions import utils


class _JsonValueErrorResponse:
    status_code = 200
    text = ""

    def raise_for_status(self) -> None:
        return None

    def json(self) -> dict[str, str]:
        raise ValueError("invalid json")


def test_update_extra_data_returns_copy_for_existing_mapping() -> None:
    original = {"existing": "value"}

    result = utils.update_extra_data(original, added="item")

    assert result == {"existing": "value", "added": "item"}
    assert original == {"existing": "value"}
    assert result is not original


def test_from_dict_get_bool_accepts_common_string_and_int_values() -> None:
    content = {
        "upper_true": "True",
        "upper_false": "FALSE",
        "yes": " yes ",
        "no": "off",
        "one": 1,
        "zero": 0,
    }

    assert utils.from_dict_get_bool(content, "upper_true") is True
    assert utils.from_dict_get_bool(content, "upper_false") is False
    assert utils.from_dict_get_bool(content, "yes") is True
    assert utils.from_dict_get_bool(content, "no") is False
    assert utils.from_dict_get_bool(content, "one") is True
    assert utils.from_dict_get_bool(content, "zero") is False


def test_get_public_ip_returns_none_when_fallback_json_is_invalid(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    responses = [requests.RequestException("primary failed"), _JsonValueErrorResponse()]

    def fake_get(*args: object, **kwargs: object) -> object:
        response = responses.pop(0)
        if isinstance(response, Exception):
            raise response
        return response

    monkeypatch.setattr(utils.requests, "get", fake_get)

    assert utils.get_public_ip() is None


def test_read_account_config_invalid_numeric_and_boolean_envs_fall_back_to_defaults(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(utils, "get_package_path", lambda package_name="bt_api_py": None)
    monkeypatch.setattr(utils, "load_dotenv", lambda *args, **kwargs: False)
    monkeypatch.setattr(utils, "find_dotenv", lambda usecwd=True: "")

    monkeypatch.setenv("IB_PORT", "not-a-number")
    monkeypatch.setenv("IB_CLIENT_ID", "bad-client")
    monkeypatch.setenv("IB_WEB_TIMEOUT", "oops")
    monkeypatch.setenv("IB_WEB_VERIFY_SSL", "definitely-not-bool")

    config = utils.read_account_config()

    assert config["ib"]["port"] == 7497
    assert config["ib"]["client_id"] == 1
    assert config["ib_web"]["timeout"] == 10
    assert config["ib_web"]["verify_ssl"] is False


def test_read_account_config_accepts_truthy_boolean_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(utils, "get_package_path", lambda package_name="bt_api_py": None)
    monkeypatch.setattr(utils, "load_dotenv", lambda *args, **kwargs: False)
    monkeypatch.setattr(utils, "find_dotenv", lambda usecwd=True: "")
    monkeypatch.setenv("IB_WEB_VERIFY_SSL", "YES")

    config = utils.read_account_config()

    assert config["ib_web"]["verify_ssl"] is True
