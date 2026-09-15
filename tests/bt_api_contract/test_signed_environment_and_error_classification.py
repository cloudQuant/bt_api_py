"""Fail-closed contracts for signed order responses without network access."""

from __future__ import annotations

from decimal import Decimal
from types import SimpleNamespace

import pytest

from bt_api_py import BtApi, NormalizedApiError, OrderRequest, OrderType, Side
from bt_api_py._normalization import check_response, normalize_error

OKX = "OKX___SWAP"
SYMBOL = "BTC-USDT-SWAP"


@pytest.mark.parametrize(
    "exchange_name,code",
    [
        (OKX, "50120"),
        (OKX, "59999"),
        ("BINANCE___SWAP", "-4999"),
        ("BINANCE___SWAP", "51008"),
        ("CTP___FUTURE", "51008"),
    ],
)
def test_unknown_numeric_write_response_defaults_to_execution_unknown(exchange_name, code):
    with pytest.raises(NormalizedApiError) as captured:
        check_response(
            {"code": code, "msg": "credential-bearing vendor detail"},
            "make_order",
            exchange_name=exchange_name,
            write=True,
        )

    error = captured.value
    assert error.code == code
    assert error.execution_unknown is True
    assert error.definite_reject is False
    assert "credential-bearing" not in str(error)


@pytest.mark.parametrize(
    "exchange_name,code",
    [
        (OKX, "51000"),
        (OKX, "51008"),
        ("BINANCE___SWAP", "-1102"),
        ("BINANCE___SWAP", "-2019"),
    ],
)
def test_documented_venue_rejection_codes_remain_definite(exchange_name, code):
    with pytest.raises(NormalizedApiError) as captured:
        check_response(
            {"code": code, "msg": "credential-bearing vendor detail"},
            "make_order",
            exchange_name=exchange_name,
            write=True,
        )

    error = captured.value
    assert error.code == code
    assert error.execution_unknown is False
    assert error.definite_reject is True
    assert "credential-bearing" not in str(error)


def test_existing_normalized_numeric_error_cannot_bypass_venue_classification():
    original = NormalizedApiError("make_order", "50120", definite_reject=True)

    error = normalize_error(original, "make_order", exchange_name=OKX, write=True)

    assert error is original
    assert error.execution_unknown is True
    assert error.definite_reject is False


def test_local_prevalidation_error_keeps_explicit_rejection_semantics():
    original = NormalizedApiError("make_order", "invalid_order", definite_reject=True)

    error = normalize_error(original, "make_order", exchange_name=OKX, write=True)

    assert error is original
    assert error.execution_unknown is False
    assert error.definite_reject is True


class ResponseBackend:
    def __init__(self, result):
        self.result = result
        self.calls = []

    def make_order(self, venue, request):
        self.calls.append((venue, request))
        if isinstance(self.result, BaseException):
            raise self.result
        return self.result


def request(client_order_id):
    return OrderRequest(
        symbol=SYMBOL,
        side=Side.BUY,
        order_type=OrderType.LIMIT,
        quantity=Decimal("1"),
        price=Decimal("60000"),
        account_id="demo",
        client_order_id=client_order_id,
        quantity_unit="contracts",
        position_mode="dual_side",
        position_side="long",
        offset="open",
    )


def execution_api(monkeypatch, tmp_path):
    monkeypatch.setattr("bt_api_py.bt_api._ensure_plugins_loaded", lambda: None)
    monkeypatch.setattr(
        "bt_api_py.bt_api.ExchangeRegistry.create_feed",
        lambda exchange_name, data_queue, **kwargs: SimpleNamespace(
            get_environment_info=lambda: {
                "environment": kwargs["environment"],
                "api_region": "global",
                "simulated": True,
                "verified": True,
            },
            disconnect=lambda: None,
        ),
    )
    return BtApi(
        {
            OKX: {
                "environment": "demo",
                "api_key": "fixture-okx-public",
                "api_secret": "fixture-okx-secret",
                "passphrase": "fixture-okx-passphrase",
            }
        },
        debug=False,
        execution_config={
            "order_journal": tmp_path / "orders.jsonl",
            "account_ids": {OKX: "demo"},
            "required_environments": {OKX: "demo"},
        },
    )


def test_unknown_numeric_submit_stays_nonterminal_and_blocks_resubmission(monkeypatch, tmp_path):
    api = execution_api(monkeypatch, tmp_path)
    backend = ResponseBackend(NormalizedApiError("make_order", "50120", definite_reject=True))
    api._backend = backend
    try:
        update = api.make_order(OKX, request("123456789012"), normalized=True)

        assert update["execution_unknown"] is True
        assert update["terminal_confirmed"] is False
        assert update["status"] == "submitted"
        assert backend.calls == [(OKX, request("123456789012"))]
        assert api.get_execution_summary()["unknown_ids"] == ["123456789012"]

        with pytest.raises(NormalizedApiError) as captured:
            api.make_order(OKX, request("123456789013"), normalized=True)
        assert captured.value.code == "unresolved_or_undurable_journal"
        assert len(backend.calls) == 1
    finally:
        api.close()


def test_explicit_submit_rejection_is_terminal(monkeypatch, tmp_path):
    api = execution_api(monkeypatch, tmp_path)
    backend = ResponseBackend({"code": "51008", "msg": "private native message"})
    api._backend = backend
    try:
        update = api.make_order(OKX, request("123456789012"), normalized=True)

        assert update["status"] == "rejected"
        assert update["execution_unknown"] is False
        assert update["terminal_confirmed"] is True
        assert update["definite_reject"] is True
        assert api.get_execution_summary()["unknown_ids"] == []
        assert len(backend.calls) == 1
    finally:
        api.close()
