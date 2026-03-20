from __future__ import annotations

from unittest.mock import MagicMock

from bt_api_py.feeds.live_coindcx.request_base import CoinDCXRequestData
from bt_api_py.feeds.live_cow_swap.request_base import CowSwapRequestData
from bt_api_py.feeds.live_curve.request_base import CurveRequestData


def test_coindcx_disconnect_closes_http_client() -> None:
    request_data = CoinDCXRequestData()
    request_data._http_client.close = MagicMock()

    request_data.disconnect()

    request_data._http_client.close.assert_called_once_with()


def test_cow_swap_disconnect_closes_http_client() -> None:
    request_data = CowSwapRequestData()
    request_data._http_client.close = MagicMock()

    request_data.disconnect()

    request_data._http_client.close.assert_called_once_with()


def test_curve_disconnect_closes_http_client() -> None:
    request_data = CurveRequestData()
    request_data._http_client.close = MagicMock()

    request_data.disconnect()

    request_data._http_client.close.assert_called_once_with()


def test_coindcx_falls_back_to_api_credentials_when_aliases_are_empty() -> None:
    request_data = CoinDCXRequestData(
        public_key="",
        api_key="public-key",
        private_key="",
        api_secret="secret-key",
    )

    headers = request_data._get_headers("{}")

    assert request_data._params.api_key == "public-key"
    assert request_data._params.api_secret == "secret-key"
    assert headers["X-AUTH-APIKEY"] == "public-key"
    assert headers["X-AUTH-SIGNATURE"]
