from __future__ import annotations

from unittest.mock import MagicMock

from bt_api_py.feeds.live_btc_markets.request_base import BtcMarketsRequestData
from bt_api_py.feeds.live_buda.request_base import BudaRequestData
from bt_api_py.feeds.live_swyftx.request_base import SwyftxRequestData


def test_btc_markets_disconnect_closes_http_client() -> None:
    request_data = BtcMarketsRequestData()
    request_data._http_client.close = MagicMock()

    request_data.disconnect()

    request_data._http_client.close.assert_called_once_with()


def test_buda_disconnect_closes_http_client() -> None:
    request_data = BudaRequestData()
    request_data._http_client.close = MagicMock()

    request_data.disconnect()

    request_data._http_client.close.assert_called_once_with()


def test_buda_accepts_public_private_key_aliases() -> None:
    request_data = BudaRequestData(public_key="public-key", private_key="secret-key")
    headers = request_data._get_headers("GET", "/api/v2/balances")

    assert request_data._params.api_key == "public-key"
    assert request_data._params.api_secret == "secret-key"
    assert headers["X-SBTC-APIKEY"] == "public-key"


def test_swyftx_disconnect_closes_http_client() -> None:
    request_data = SwyftxRequestData(None)
    request_data._http_client.close = MagicMock()

    request_data.disconnect()

    request_data._http_client.close.assert_called_once_with()


def test_swyftx_falls_back_to_api_key_when_public_key_is_empty() -> None:
    request_data = SwyftxRequestData(None, public_key="", api_key="public-key")

    assert request_data.api_key == "public-key"
