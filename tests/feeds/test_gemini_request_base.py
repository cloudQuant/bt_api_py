from __future__ import annotations

from unittest.mock import MagicMock

from bt_api_py.feeds.live_gemini.request_base import GeminiRequestData


def test_gemini_defaults_exchange_name_for_http_client() -> None:
    request_data = GeminiRequestData(public_key="public-key", private_key="secret-key")

    assert request_data.exchange_name == "GEMINI___SPOT"
    assert request_data._http_client._venue == "GEMINI___SPOT"


def test_gemini_disconnect_closes_http_client() -> None:
    request_data = GeminiRequestData(public_key="public-key", private_key="secret-key")
    request_data._http_client.close = MagicMock()

    request_data.disconnect()

    request_data._http_client.close.assert_called_once_with()


def test_gemini_accepts_api_key_and_api_secret_aliases() -> None:
    request_data = GeminiRequestData(api_key="public-key", api_secret="secret-key")

    assert request_data.public_key == "public-key"
    assert request_data.private_key == "secret-key"
