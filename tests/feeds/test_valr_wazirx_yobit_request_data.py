from __future__ import annotations

from unittest.mock import MagicMock

from bt_api_py.feeds.live_valr.request_base import ValrRequestData
from bt_api_py.feeds.live_wazirx.request_base import WazirxRequestData
from bt_api_py.feeds.live_yobit.request_base import YobitRequestData


def test_valr_disconnect_closes_http_client() -> None:
    request_data = ValrRequestData()
    request_data._http_client.close = MagicMock()

    request_data.disconnect()

    request_data._http_client.close.assert_called_once_with()


def test_wazirx_disconnect_closes_http_client() -> None:
    request_data = WazirxRequestData()
    request_data._http_client.close = MagicMock()

    request_data.disconnect()

    request_data._http_client.close.assert_called_once_with()


def test_yobit_disconnect_closes_http_client() -> None:
    request_data = YobitRequestData()
    request_data._http_client.close = MagicMock()

    request_data.disconnect()

    request_data._http_client.close.assert_called_once_with()


def test_valr_falls_back_to_api_credentials_when_aliases_are_empty() -> None:
    request_data = ValrRequestData(
        public_key="",
        api_key="public-key",
        secret_key="",
        api_secret="secret-key",
    )

    assert request_data.api_key == "public-key"
    assert request_data.api_secret == "secret-key"


def test_wazirx_falls_back_to_api_credentials_when_aliases_are_empty() -> None:
    request_data = WazirxRequestData(
        public_key="",
        api_key="public-key",
        secret_key="",
        api_secret="secret-key",
    )

    assert request_data.api_key == "public-key"
    assert request_data.api_secret == "secret-key"


def test_yobit_falls_back_to_api_credentials_when_aliases_are_empty() -> None:
    request_data = YobitRequestData(
        public_key="",
        api_key="public-key",
        secret_key="",
        api_secret="secret-key",
    )

    assert request_data.api_key == "public-key"
    assert request_data.api_secret == "secret-key"
