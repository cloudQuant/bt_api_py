"""Iteration 62 (D62-11a): SDK accessor for instrument-scoped direct positions.

UNIT/FAKE evidence: a fake direct write provider behind the real coordinator.
"""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from bt_api_base.direct_write_protocol import DirectAccountFacts
from bt_api_py._contracts.errors import NormalizedApiError
from bt_api_py.bt_api import BtApi
from bt_api_py.direct_write import DirectWriteCoordinator

ROUTE = "CTP___FUTURE"


def _instrument_result(symbol="rb2701", **overrides):
    values = dict(scope="instrument", proves_account_flat=False, symbol=symbol, positions=())
    values.update(overrides)
    return SimpleNamespace(**values)


class Provider:
    def __init__(self, result=None):
        self.calls = []
        self.result = result

    def instrument_positions(self, symbol, exchange_id=None, *, timeout=5.0):
        self.calls.append((symbol, exchange_id, timeout))
        return self.result if self.result is not None else _instrument_result(symbol)


def _api(provider):
    api = BtApi()
    api._direct_write_coordinator = DirectWriteCoordinator(
        api.write_admission, lambda name: provider if name == ROUTE else None
    )
    return api


def test_accessor_delegates_to_the_provider_instrument_query():
    provider = Provider()
    result = _api(provider).direct_instrument_positions(
        ROUTE, "rb2701", exchange_id="SHFE", timeout=2.0
    )
    assert provider.calls == [("rb2701", "SHFE", 2.0)]
    assert result.scope == "instrument" and result.proves_account_flat is False


def test_provider_without_the_capability_is_refused():
    api = _api(SimpleNamespace(account_facts=lambda **kw: None))
    with pytest.raises(NormalizedApiError) as info:
        api.direct_instrument_positions(ROUTE, "rb2701")
    assert info.value.code == "capability_not_supported"


@pytest.mark.parametrize(
    "result",
    [
        _instrument_result(scope="account"),
        _instrument_result(proves_account_flat=True),
        _instrument_result(symbol="ag2512"),
        DirectAccountFacts(
            exchange_name=ROUTE,
            trading_day="20261106",
            session_generation=4,
            observed_at_utc="2026-11-06T01:00:00Z",
            positions=(),
            active_orders=None,
            trades=None,
            balance=None,
            complete=True,
        ),
    ],
)
def test_results_without_instrument_scope_are_refused(result):
    with pytest.raises(NormalizedApiError) as info:
        _api(Provider(result)).direct_instrument_positions(ROUTE, "rb2701")
    assert info.value.code == "result_scope_invalid"


@pytest.mark.parametrize("symbol", ["", None, 2701])
def test_invalid_symbol_is_refused_before_the_provider(symbol):
    provider = Provider()
    with pytest.raises(NormalizedApiError) as info:
        _api(provider).direct_instrument_positions(ROUTE, symbol)
    assert info.value.code == "symbol_invalid"
    assert provider.calls == []


def test_ctp_instrument_result_type_passes_the_scope_check():
    facts_module = pytest.importorskip("bt_api_ctp.instrument_position_facts")
    facts = facts_module.InstrumentPositionFacts(
        exchange_name=ROUTE,
        symbol="rb2701",
        venue="SHFE",
        trading_day="20261106",
        session_generation=4,
        observed_at_utc="2026-11-06T01:00:00Z",
        positions=(),
        complete=True,
    )
    result = _api(Provider(facts)).direct_instrument_positions(ROUTE, "rb2701")
    assert result is facts and result.instrument_flat is True
