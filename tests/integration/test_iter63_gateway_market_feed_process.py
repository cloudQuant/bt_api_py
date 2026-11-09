"""Real BT Feed and separate Gateway process; CTP native sessions are synthetic."""
from __future__ import annotations

from types import SimpleNamespace
import time

import pytest

from tests.integration.test_iter63_async_market_bootstrap_service import async_gateway as async_gateway, strategy
from tests.integration.test_iter63_ctp_gateway_joint_service import DOMAIN, SERVICE, _key
from bt_api_py.gateway_api import GatewayBtApi
from bt_api_py.gateway_strategy_market import GatewayMarketClientSettings, GatewayMarketSubscriptionSettings
import backtrader as bt
from backtrader.feeds.btapifeed import BtApiFeed


def test_feed_consumes_real_gateway_market_subscription(async_gateway):
    async_gateway['release'].set()
    session = strategy(async_gateway)
    facade = GatewayBtApi(session, exchange_name='test', owns_session=False)
    key = _key('IF2612')
    config = SimpleNamespace(
        exchange_name='test', peer=SERVICE,
        route=SimpleNamespace(scope=SimpleNamespace(execution_domain_ref=DOMAIN)),
        market_settings=GatewayMarketClientSettings({'ticks': GatewayMarketSubscriptionSettings(
            key, require_source_descriptor=True)}),
        instruments_by_ref={'ticks': key.instrument},
    )
    class Store:
        _gateway_strategy_config = config
        gateway_strategy_config = config
        gateway_api = facade
        def start(self, data=None):
            assert data._gateway_market_enabled_for_store(self)
        def poll_tick(self, *_args):
            raise AssertionError('legacy provider poll')
        def poll_live(self, *_args):
            raise AssertionError('legacy provider poll')
        def poll_orderbook(self, *_args, **_kwargs):
            raise AssertionError('legacy provider poll')
    feed = BtApiFeed(dataname='IF2612', store=Store(), timeframe=bt.TimeFrame.Ticks,
                     backfill_start=False, price_tick=1.0, qcheck=0.0)
    feed._start()
    observed = []
    original_dispatch = feed._dispatch_event
    def capture(**kwargs):
        observed.append(kwargs['event_data'])
        return original_dispatch(**kwargs)
    feed._dispatch_event = capture
    try:
        async_gateway['controls'].put(('tick', 'IF2612'))
        assert async_gateway['events'].get(timeout=5) == ('tick', 'IF2612')
        deadline = time.monotonic() + 10
        while time.monotonic() < deadline:
            feed.load()
            if any(tick.price == 4005.0 for tick in observed):
                break
            time.sleep(0.05)
        else:
            pytest.fail('reason=%s, ticks=%s' % (feed._gateway_market_adapter.recovery_reason(), [(getattr(x, 'quality_flags', None), getattr(x, 'data_quality', None), getattr(x, 'volume_complete', None), getattr(x, 'delta_volume', None), getattr(x, 'continuity_status', None)) for x in observed]))
        # Synthetic native fixture supplies incomplete quotes/volume, so the
        # existing Feed deliberately refuses to publish a line or qualify trade.
        assert any(tick.price == 4005.0 and tick.data_quality == 'INVALID' for tick in observed)
        assert feed.trade_qualification().ok is False
    finally:
        feed.stop()
        facade.close()


def _gateway_with_valid_quotes(*args):
    """Only this synthetic child fills the missing native quote fields."""
    import sys
    from pathlib import Path
    sdk = Path(__file__).resolve().parents[2]
    sys.path.insert(0, str(sdk / 'bt_api/bt_api_ctp/tests'))
    from test_market_provider import _SyntheticMdClient
    from tests.integration.test_iter63_ctp_gateway_joint_service import _gateway
    original = _SyntheticMdClient.emit_tick
    def emit(self, **fields):
        fields.setdefault('BidPrice1', 4004.0)
        fields.setdefault('AskPrice1', 4006.0)
        fields.setdefault('BidVolume1', 3)
        fields.setdefault('AskVolume1', 4)
        fields.setdefault('LowerLimitPrice', 3500.0)
        fields.setdefault('UpperLimitPrice', 4500.0)
        fields.setdefault('OpenInterest', 1000.0)
        fields.setdefault('Turnover', 10000.0)
        return original(self, **fields)
    _SyntheticMdClient.emit_tick = emit
    return _gateway(*args)


@pytest.fixture
def valid_gateway(tmp_path, monkeypatch):
    import tests.integration.test_iter63_async_market_bootstrap_service as fixtures
    monkeypatch.setattr(fixtures, '_gateway', _gateway_with_valid_quotes)
    yield from fixtures.async_gateway.__wrapped__(tmp_path)


def test_usable_synthetic_quote_reaches_real_feed_line(valid_gateway):
    valid_gateway['release'].set()
    session = strategy(valid_gateway)
    facade = GatewayBtApi(session, exchange_name='test', owns_session=False)
    key = _key('IF2612')
    config = SimpleNamespace(exchange_name='test', peer=SERVICE,
        route=SimpleNamespace(scope=SimpleNamespace(execution_domain_ref=DOMAIN)),
        market_settings=GatewayMarketClientSettings({'ticks': GatewayMarketSubscriptionSettings(
            key, require_source_descriptor=True)}), instruments_by_ref={'ticks': key.instrument})
    class Store:
        _gateway_strategy_config = config
        gateway_strategy_config = config
        gateway_api = facade
        def start(self, data=None):
            assert data._gateway_market_enabled_for_store(self)
        def poll_tick(self, *_args):
            raise AssertionError('legacy provider poll')
        def poll_live(self, *_args):
            raise AssertionError('legacy provider poll')
        def poll_orderbook(self, *_args, **_kwargs):
            raise AssertionError('legacy provider poll')
    feed = BtApiFeed(dataname='IF2612', store=Store(), timeframe=bt.TimeFrame.Ticks,
                     backfill_start=False, price_tick=1.0, qcheck=0.0)
    feed._start()
    try:
        valid_gateway['controls'].put(('tick', 'IF2612'))
        assert valid_gateway['events'].get(timeout=5) == ('tick', 'IF2612')
        deadline = time.monotonic() + 8
        while time.monotonic() < deadline:
            if feed.load() and feed.close[0] == 4005.0:
                break
            time.sleep(0.05)
        else:
            pytest.fail('valid Gateway quote did not reach BT feed line')
        receipt = feed._gateway_market_adapter._projector.last_receipt
        assert receipt is not None
        assert receipt.source in {'pub', 'replay'}
        assert receipt.stream_id == key.stream_id
        assert receipt.received_monotonic_ns > 0
        assert feed.trade_qualification().ok is False
    finally:
        feed.stop()
        facade.close()
