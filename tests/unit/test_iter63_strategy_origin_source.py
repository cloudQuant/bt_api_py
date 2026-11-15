"""Static source validation using synthetic owner and lock objects."""
from dataclasses import replace

import pytest

from bt_api_py.gateway_strategy_origin_source import (
    GatewayStrategyOriginSource,
    GatewayStrategyOriginSourceError,
)
from tests.unit.test_iter64_origin_immutable_owner_fake import _bound, _inputs


def test_source_exact_type_current_state_and_close(monkeypatch):
    context, order, key = _inputs()
    source = GatewayStrategyOriginSource(instruments=(key,))
    _bound(monkeypatch, source)
    assert source.resolve(context, order) == key and source.recheck(key)
    assert not source.recheck(replace(key, environment='different'))
    source.close()
    with pytest.raises(GatewayStrategyOriginSourceError, match='UNAVAILABLE'):
        source.resolve(context, order)


def test_source_rejects_wrong_owner_thread_and_bad_source(monkeypatch):
    context, order, key = _inputs()
    with pytest.raises(GatewayStrategyOriginSourceError):
        GatewayStrategyOriginSource(instruments=({'symbol': 'rb'},))
    source = GatewayStrategyOriginSource(instruments=(key,))
    _bound(monkeypatch, source)
    assert not source.recheck({'symbol': 'rb'})
    import bt_api_py.gateway_strategy_origin_source as module
    monkeypatch.setattr(module.threading, 'current_thread', lambda: object())
    with pytest.raises(GatewayStrategyOriginSourceError, match='UNAVAILABLE'):
        source.resolve(context, order)
