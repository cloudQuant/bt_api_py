from __future__ import annotations

from unittest.mock import MagicMock

from bt_api_py.feeds.live_exmo.request_base import ExmoRequestData
from bt_api_py.feeds.live_foxbit.request_base import FoxbitRequestData
from bt_api_py.feeds.live_giottus.request_base import GiottusRequestData


def test_exmo_disconnect_closes_http_client() -> None:
    request_data = ExmoRequestData()
    request_data._http_client.close = MagicMock()

    request_data.disconnect()

    request_data._http_client.close.assert_called_once_with()


def test_foxbit_disconnect_closes_http_client() -> None:
    request_data = FoxbitRequestData()
    request_data._http_client.close = MagicMock()

    request_data.disconnect()

    request_data._http_client.close.assert_called_once_with()


def test_giottus_disconnect_closes_http_client() -> None:
    request_data = GiottusRequestData()
    request_data._http_client.close = MagicMock()

    request_data.disconnect()

    request_data._http_client.close.assert_called_once_with()


def test_giottus_accepts_public_private_key_aliases() -> None:
    request_data = GiottusRequestData(public_key="public-key", private_key="secret-key")
    headers = request_data._get_headers()

    assert request_data._params.api_key == "public-key"
    assert request_data._params.api_secret == "secret-key"
    assert headers["X-API-KEY"] == "public-key"
