"""Focused contracts for provider-confirmed UNKNOWN order recovery."""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from bt_api_py import reconcile_unknown_order_from_query
from bt_api_py._contracts.errors import NormalizedApiError


class _Session:
    def __init__(self, unknown=("client-1",), market_data_only=False, closed=False):
        self.config = {"market_data_only": market_data_only}
        self.closed = closed
        self._unknown = set(unknown)
        self.events = []
        self.orders = {
            ("OKX___SWAP", "client-1"): {
                "exchange_name": "OKX___SWAP",
                "client_order_id": "client-1",
                "account_id": "acct",
                "side": "buy",
                "position_side": "long",
                "offset": "open",
                "position_mode": "dual_side",
                "quantity_unit": "contracts",
            }
        }

    def _unknown_ids(self):
        return set(self._unknown)

    def event(self, venue, row):
        self.events.append((venue, dict(row)))
        self._unknown.discard(row.get("client_order_id"))
        return dict(row)


class _Api:
    def __init__(self, session, row):
        self._execution_session = session
        self._row = row

    def query_order(self, exchange_name, request, normalized=False):
        assert normalized is True
        return SimpleNamespace(data=[dict(self._row)])


def _request(client_order_id="client-1"):
    return SimpleNamespace(client_order_id=client_order_id, order_id="order-1")


def _row(**overrides):
    row = {
        "kind": "order",
        "client_order_id": "client-1",
        "order_id": "order-1",
        "status": "canceled",
        "terminal_confirmed": True,
        "execution_unknown": False,
        "filled": 0.0,
    }
    row.update(overrides)
    return row


def test_terminal_query_record_updates_the_journal_and_clears_unknown():
    session = _Session()
    api = _Api(session, _row())
    result = reconcile_unknown_order_from_query(
        api, "OKX___SWAP", _request(), expected_client_order_id="client-1"
    )
    assert result["status"] == "canceled"
    assert result["unknown_ids_remaining"] == []
    assert session.events[0][0] == "OKX___SWAP"
    assert session.events[0][1]["terminal_confirmed"] is True


def test_non_terminal_query_does_not_touch_the_journal():
    session = _Session()
    api = _Api(session, _row(status="live", terminal_confirmed=False))
    with pytest.raises(NormalizedApiError) as exc:
        reconcile_unknown_order_from_query(api, "OKX___SWAP", _request())
    assert exc.value.code == "query_result_not_terminal"
    assert session.events == []


def test_wrong_client_identity_is_rejected():
    session = _Session()
    api = _Api(session, _row(client_order_id="other"))
    with pytest.raises(NormalizedApiError) as exc:
        reconcile_unknown_order_from_query(
            api, "OKX___SWAP", _request(), expected_client_order_id="client-1"
        )
    assert exc.value.code == "query_result_identity_mismatch"
    assert session.events == []


def test_order_must_currently_be_unknown():
    session = _Session(unknown=())
    api = _Api(session, _row())
    with pytest.raises(NormalizedApiError) as exc:
        reconcile_unknown_order_from_query(api, "OKX___SWAP", _request())
    assert exc.value.code == "order_not_unknown"


def test_read_only_journal_is_not_written():
    session = _Session(market_data_only=True)
    api = _Api(session, _row())
    with pytest.raises(NormalizedApiError) as exc:
        reconcile_unknown_order_from_query(api, "OKX___SWAP", _request())
    assert exc.value.code == "execution_journal_read_only"
