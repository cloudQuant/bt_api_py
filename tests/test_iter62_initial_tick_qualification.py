"""Iteration 62 (D62-38): every tick entering a consumer queue is SDK-qualified.

Polled ticks always went through ``BtApi._qualify_market_tick``; the initial
ticks of a subscribe/resync (direct owner ``initial_events``, Gateway bootstrap
``latest_event``) were queued unqualified, so a fresh first tick could never be
execution eligible.  Both stream implementations now route every tick through
one ``_qualified`` helper.  The Gateway bootstrap path is covered end to end by
``tests/integration/test_iter62_gateway_market_stream.py``.
"""

from __future__ import annotations

import types

import pytest

from bt_api_base.market_tick_protocol import MarketTickEvent

from bt_api_py.service_market_stream import ServiceMarketStream
from bt_api_py.service_market_stream_owner import ProviderMarketStreamOwner


def _bare(cls, api):
    instance = cls.__new__(cls)
    instance._api = api
    return instance


def _tick():
    tick = MarketTickEvent.__new__(MarketTickEvent)
    return tick


class _RecordingApi:
    def __init__(self, result=None, raises=False):
        self.calls = []
        self.result = result
        self.raises = raises

    def _qualify_market_tick(self, exchange_name, event):
        self.calls.append((exchange_name, event))
        if self.raises:
            raise RuntimeError("synthetic qualification failure")
        return self.result if self.result is not None else event


@pytest.mark.parametrize("cls", [ProviderMarketStreamOwner, ServiceMarketStream])
def test_a_tick_is_qualified(cls):
    qualified = object()
    api = _RecordingApi(result=qualified)
    tick = _tick()
    assert _bare(cls, api)._qualified("CTP___FUTURE", tick) is qualified
    assert api.calls == [("CTP___FUTURE", tick)]


@pytest.mark.parametrize("cls", [ProviderMarketStreamOwner, ServiceMarketStream])
def test_a_failing_qualification_keeps_the_unqualified_tick(cls):
    api = _RecordingApi(raises=True)
    tick = _tick()
    assert _bare(cls, api)._qualified("CTP___FUTURE", tick) is tick


@pytest.mark.parametrize("cls", [ProviderMarketStreamOwner, ServiceMarketStream])
def test_without_a_qualification_hook_the_tick_passes_unchanged(cls):
    tick = _tick()
    assert _bare(cls, types.SimpleNamespace())._qualified("CTP___FUTURE", tick) is tick


def test_the_direct_owner_leaves_non_tick_events_unqualified():
    api = _RecordingApi(result=object())
    book = object()
    assert _bare(ProviderMarketStreamOwner, api)._qualified("CTP___FUTURE", book) is book
    assert api.calls == []
