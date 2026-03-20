from __future__ import annotations

from unittest.mock import MagicMock

from bt_api_py.feeds.live_bydfi.request_base import BYDFiRequestData
from bt_api_py.feeds.live_zaif.request_base import ZaifRequestData
from bt_api_py.feeds.live_zebpay.request_base import ZebpayRequestData


def test_bydfi_disconnect_closes_http_client() -> None:
    request_data = BYDFiRequestData()
    request_data._http_client.close = MagicMock()

    request_data.disconnect()

    request_data._http_client.close.assert_called_once_with()


def test_zaif_disconnect_closes_http_client() -> None:
    request_data = ZaifRequestData()
    request_data._http_client.close = MagicMock()

    request_data.disconnect()

    request_data._http_client.close.assert_called_once_with()


def test_zebpay_disconnect_closes_http_client() -> None:
    request_data = ZebpayRequestData()
    request_data._http_client.close = MagicMock()

    request_data.disconnect()

    request_data._http_client.close.assert_called_once_with()


def test_zaif_falls_back_to_api_credentials_when_aliases_are_empty() -> None:
    request_data = ZaifRequestData(
        public_key="",
        api_key="public-key",
        secret_key="",
        api_secret="secret-key",
    )

    assert request_data.api_key == "public-key"
    assert request_data.api_secret == "secret-key"


def test_zebpay_falls_back_to_api_credentials_when_aliases_are_empty() -> None:
    request_data = ZebpayRequestData(
        public_key="",
        api_key="public-key",
        secret_key="",
        api_secret="secret-key",
    )

    assert request_data.api_key == "public-key"
    assert request_data.api_secret == "secret-key"


def test_bydfi_falls_back_to_api_credentials_when_aliases_are_empty() -> None:
    request_data = BYDFiRequestData(
        public_key="",
        api_key="public-key",
        private_key="",
        api_secret="secret-key",
    )

    headers = request_data._get_headers("GET", "/open/v1/balance")

    assert request_data._params.api_key == "public-key"
    assert request_data._params.api_secret == "secret-key"
    assert headers["X-API-KEY"] == "public-key"
