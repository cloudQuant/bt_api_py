"""Iteration 62 (D62-25, 62.03): SDK accessor for direct trading-permission facts.

UNIT/FAKE evidence: fake direct write providers behind the real coordinator,
plus the CTP provider's neutral result type when that provider is installed.
"""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from bt_api_py._contracts.errors import NormalizedApiError
from bt_api_py.bt_api import BtApi
from bt_api_py.direct_write import DirectWriteCoordinator

ROUTE = "CTP___FUTURE"


def _entry(symbol):
    return SimpleNamespace(symbol=symbol, outcome="unknown", facts=())


def _permission_result(symbols=("rb2701",), **overrides):
    values = dict(
        scope="instrument_permission",
        proves_permission=False,
        complete=True,
        instruments=tuple(_entry(symbol) for symbol in symbols),
    )
    values.update(overrides)
    return SimpleNamespace(**values)


class Provider:
    def __init__(self, result=None):
        self.calls = []
        self.result = result

    def trading_permission_facts(self, symbols, *, exchange_ids=None, timeout=5.0, **kwargs):
        self.calls.append((symbols, exchange_ids, timeout, kwargs))
        return self.result if self.result is not None else _permission_result(symbols)


def _api(provider):
    api = BtApi()
    api._direct_write_coordinator = DirectWriteCoordinator(
        api.write_admission, lambda name: provider if name == ROUTE else None
    )
    return api


def test_accessor_delegates_to_the_provider_permission_read():
    provider = Provider()
    result = _api(provider).direct_trading_permissions(
        ROUTE, ["rb2701", "hc2701"], exchange_ids={"rb2701": "SHFE"}, timeout=2.0
    )
    assert provider.calls == [(("rb2701", "hc2701"), {"rb2701": "SHFE"}, 2.0, {})]
    assert result.scope == "instrument_permission" and result.proves_permission is False


def test_accessor_needs_no_write_admission():
    api = _api(Provider())
    assert api.write_admission.is_enabled(ROUTE) is False
    assert api.direct_trading_permissions(ROUTE, ["rb2701"]).complete is True


def test_providers_without_the_capability_are_refused_unchanged():
    """OKX/Binance-like providers: no permission read, nothing else is called."""
    calls = []
    provider = SimpleNamespace(account_facts=lambda **kw: calls.append(kw))
    with pytest.raises(NormalizedApiError) as info:
        _api(provider).direct_trading_permissions(ROUTE, ["BTC-USDT-SWAP"])
    assert info.value.code == "capability_not_supported"
    assert calls == []


@pytest.mark.parametrize(
    "result",
    [
        _permission_result(scope="instrument"),
        _permission_result(proves_permission=True),
        _permission_result(proves_permission=None),
        _permission_result(complete="yes"),
        _permission_result(symbols=("ag2512",)),
        _permission_result(symbols=("rb2701", "ag2512")),
        _permission_result(symbols=("rb2701", "rb2701")),
        _permission_result(symbols=()),
        SimpleNamespace(scope="instrument_permission", proves_permission=False, complete=True),
        SimpleNamespace(
            scope="instrument",
            proves_account_flat=False,
            symbol="rb2701",
            positions=(),
            complete=True,
        ),
    ],
)
def test_results_of_another_scope_or_instrument_set_are_refused(result):
    with pytest.raises(NormalizedApiError) as info:
        _api(Provider(result)).direct_trading_permissions(ROUTE, ["rb2701"])
    assert info.value.code == "result_scope_invalid"


@pytest.mark.parametrize("symbols", ["rb2701", None, [], ["rb2701", ""], [2701], {"rb2701": 1}])
def test_invalid_symbols_are_refused_before_the_provider(symbols):
    provider = Provider()
    with pytest.raises(NormalizedApiError) as info:
        _api(provider).direct_trading_permissions(ROUTE, symbols)
    assert info.value.code == "symbols_invalid"
    assert provider.calls == []


def test_ctp_permission_result_type_passes_the_scope_check():
    facts_module = pytest.importorskip("bt_api_ctp.trading_permission_facts")
    fact = facts_module.PermissionFact
    entry = facts_module.InstrumentPermissionFacts(
        symbol="rb2701",
        venue="SHFE",
        facts=(
            fact("investor_active", "known_positive", "investor_active", True),
            fact("trading_code", "known_negative", "trading_code_absent", True),
            fact("instrument_trading", "known_positive", "instrument_trading", True),
        ),
    )
    facts = facts_module.TradingPermissionFacts(
        exchange_name=ROUTE,
        trading_day="20261106",
        session_generation=4,
        observed_at_utc="2026-11-06T01:00:00Z",
        instruments=(entry,),
        session_consistent=True,
    )
    result = _api(Provider(facts)).direct_trading_permissions(ROUTE, ["rb2701"])
    assert result is facts
    assert result.complete is True
    assert result.for_symbol("rb2701").outcome == "known_negative"
    assert result.for_symbol("rb2701").denial_reasons == ("trading_code_absent",)
