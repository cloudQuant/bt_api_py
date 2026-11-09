"""Synthetic owner-local resolver; it does not provide provider authority."""
import threading
from decimal import Decimal

import pytest
from bt_api_base.market_protocol import MarketInstrumentKey
from bt_api_base.service_protocol import WireIdentity, WireRole, WireScope

from bt_api_py._contracts.models import OrderRequest, OrderType, Side
from bt_api_py.gateway_strategy_origin_source import GatewayStrategyOriginSource, GatewayStrategyOriginSourceError
from bt_api_py.service_registry import ServiceCallContext


def inputs():
    ctx = ServiceCallContext(WireIdentity(WireRole.STRATEGY_CLIENT, "principal", "actor"),
        WireScope("domain", account_ref="acct", strategy_ref="s", run_id="r"), "reg", "principal")
    order = OrderRequest("rb", Side.BUY, OrderType.LIMIT, Decimal("1"), "acct", "client", price=Decimal("10"))
    key = MarketInstrumentKey("ctp", "SHFE", "simnow", "future", "rb", "rb")
    return ctx, order, key


def test_source_exact_type_current_state_and_close():
    ctx, order, key = inputs()
    current = [True]
    source = GatewayStrategyOriginSource(resolve=lambda c, o: key if c is ctx and o is order else None,
                                          recheck=lambda k: current[0] and k == key)
    assert source.resolve(ctx, order) == key and source.recheck(key)
    current[0] = False
    assert not source.recheck(key)
    source.close()
    with pytest.raises(GatewayStrategyOriginSourceError, match="UNAVAILABLE"):
        source.resolve(ctx, order)


def test_source_rejects_wrong_thread_bad_source_and_non_boolean():
    ctx, order, key = inputs()
    source = GatewayStrategyOriginSource(resolve=lambda *_: {"symbol": "rb"}, recheck=lambda _: 1)
    with pytest.raises(GatewayStrategyOriginSourceError, match="UNAVAILABLE"):
        source.resolve(ctx, order)
    assert source.recheck(key) is False
    failures = []
    def other_thread():
        try:
            source.recheck(key)
        except GatewayStrategyOriginSourceError as exc:
            failures.append(str(exc))
    thread = threading.Thread(target=other_thread)
    thread.start()
    thread.join()
    assert failures == ["STRATEGY_ORIGIN_SOURCE_UNAVAILABLE"]
