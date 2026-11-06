"""Iteration 62 continuity-7: the direct market stream after a CTP front disconnect.

SimNow 2026-11-06: after the market-data front disconnected, the direct
channel sat in RECOVERY_REQUIRED with ``PROVIDER_POLL_FAILED`` and the CTP
direct adapter refused every resync, so no tick reached the strategy again
until the process restarted.

The SDK owner now keeps a recovery reason the provider session declares --
only from a finite set, today ``PROVIDER_RESYNC_REQUIRED`` -- so the consumer
can tell a confirmed source restart (data loss acknowledged through
``accept_resync``) from any other failure; and the CTP adapter rebinds the
channel to the new MD session under a new epoch.

Evidence: UNIT (stub session) and INTEGRATION_SIM (the real owner, CTP direct
adapter, provider, CtpMarketStream and MdClient over a fake vendor MD API whose
callbacks run serialised on their own thread).  No CTP library, front, account
or network is involved.
"""

from __future__ import annotations

import queue
import threading
import time
from types import SimpleNamespace

import pytest
from bt_api_base.market_protocol import (
    MarketInstrumentKey,
    MarketSourceDescriptor,
    MarketSubscriptionKey,
)
from bt_api_base.market_stream_protocol import MarketStreamRequest, MarketStreamState
from bt_api_base.market_stream_provider_protocol import (
    MarketStreamChannelCapability,
    MarketStreamProviderBinding,
    MarketStreamProviderChannelBinding,
    MarketStreamProviderContractError,
)

from bt_api_py.service_market_stream_owner import ProviderMarketStreamOwner

EXCHANGE = "CTP___FUTURE"


def _instrument() -> MarketInstrumentKey:
    return MarketInstrumentKey(
        provider="ctp",
        venue="SHFE",
        environment="simnow",
        product_type="future",
        instrument_id="rb2701",
        native_symbol="rb2701",
    )


class _Api:
    """The facade surface the owner reads: one pinned route client."""

    def __init__(self, feed: object) -> None:
        self.exchange_feeds = {EXCHANGE: feed}
        self._selected_route_clients = {EXCHANGE: feed}
        self._market_runtime_factory = None

    def _selected_provider_route(self, exchange_name):
        return (self.exchange_feeds[exchange_name], None)


class _Router:
    def __init__(self, factory) -> None:
        self._factory = factory

    def resolve_request(self, route, request):
        binding = self._factory(route)
        capabilities = binding.adapter.capabilities()
        return binding, next(cap for cap in capabilities if cap.supports(request))


# --------------------------------------------------------------------------
# UNIT: the owner keeps only a finite, declared recovery reason
# --------------------------------------------------------------------------


class _StubSession:
    def __init__(self, declared) -> None:
        self.declared = declared
        self.fail = False

    def subscribe(self, request):
        key = MarketSubscriptionKey(
            source_ref="stub-source",
            entitlement_ref="stub-entitlement",
            instrument=request.instrument,
            channel="tick",
        )
        source = MarketSourceDescriptor(
            key=key,
            stream_id=key.stream_id,
            epoch="stub-epoch-1",
            provider="ctp",
            source="stub",
            connection_generation=1,
            clock_domain_id="stub-domain",
            clock_source_session_id="stub-session",
            clock_source="stub-clock",
        )
        return MarketStreamProviderChannelBinding(
            token=object(),
            request=request,
            key=key,
            source=source,
            initial_cursor=None,
            initial_events=(),
            recovery_required=False,
        )

    def poll(self, *, timeout_ms, max_events):
        if self.fail:
            raise MarketStreamProviderContractError("MARKET_STREAM_PROVIDER_EVENT_INVALID")
        return ()

    def channel_recovery_code(self, binding):
        if self.declared == "raise":
            raise RuntimeError("probe failed")
        return self.declared

    def resync(self, binding, *, acknowledge_data_loss):
        raise MarketStreamProviderContractError("MARKET_STREAM_PROVIDER_SESSION_INVALID")

    def unsubscribe(self, binding):
        return True

    def close(self):
        return True


class _StubAdapter:
    def __init__(self, session) -> None:
        self.session = session

    def capabilities(self):
        return (MarketStreamChannelCapability(channel="tick", max_depth=None),)

    def open_session(self, *, wake_callback):
        return self.session


@pytest.mark.parametrize(
    ("declared", "expected"),
    [
        ("PROVIDER_RESYNC_REQUIRED", "PROVIDER_RESYNC_REQUIRED"),
        ("PROVIDER_SOURCE_RESTARTED", "PROVIDER_POLL_FAILED"),  # not a neutral code
        (None, "PROVIDER_POLL_FAILED"),
        ("ANY_OTHER_TEXT", "PROVIDER_POLL_FAILED"),
        (object(), "PROVIDER_POLL_FAILED"),
        ("raise", "PROVIDER_POLL_FAILED"),
    ],
)
def test_owner_keeps_only_a_declared_finite_recovery_code(declared, expected):
    session = _StubSession(declared)
    feed = object()

    def factory(route):
        return MarketStreamProviderBinding(
            instrument=route.instrument, adapter=_StubAdapter(session)
        )

    owner = ProviderMarketStreamOwner(_Api(feed), router=_Router(factory))
    subscription = owner.subscribe(
        MarketStreamRequest(exchange_name=EXCHANGE, instrument=_instrument())
    )
    assert owner.poll(subscription).state is MarketStreamState.ACTIVE
    session.fail = True
    result = owner.poll(subscription)
    assert result.state is MarketStreamState.RECOVERY_REQUIRED
    assert result.recovery_code == expected


# --------------------------------------------------------------------------
# INTEGRATION_SIM: the real CTP direct adapter behind the owner
# --------------------------------------------------------------------------


class _Front:
    def __init__(self) -> None:
        self.trading_day = "20261106"
        self.apis: list[_FakeMdApi] = []


class _FakeMdApi:
    """Fake vendor MD API: callbacks serialised on one thread, like CTP."""

    def __init__(self, front: _Front) -> None:
        front.apis.append(self)
        self.front = front
        self.spi = None
        self.released = False
        self._events: queue.Queue = queue.Queue()
        self._thread: threading.Thread | None = None
        self._volume = 100
        self._second = 0

    def RegisterSpi(self, spi):
        self.spi = spi

    def RegisterFront(self, _front):
        return None

    def Init(self):
        self._thread = threading.Thread(target=self._run, daemon=True)
        self._thread.start()
        self._events.put(lambda: self.spi.OnFrontConnected())

    def Release(self):
        self.released = True
        self._events.put(None)
        if self._thread is not None and self._thread is not threading.current_thread():
            self._thread.join(2.0)

    def ReqUserLogin(self, _field, _request_id):
        day = self.front.trading_day
        self._events.put(
            lambda: self.spi.OnRspUserLogin(
                SimpleNamespace(BrokerID="", UserID="", TradingDay=day),
                SimpleNamespace(ErrorID=0),
                0,
                True,
            )
        )
        return 0

    def SubscribeMarketData(self, instruments):
        symbols = list(instruments)

        def answer():
            for index, symbol in enumerate(symbols):
                self.spi.OnRspSubMarketData(
                    SimpleNamespace(InstrumentID=symbol),
                    SimpleNamespace(ErrorID=0),
                    0,
                    index == len(symbols) - 1,
                )
            for symbol in symbols:
                self._tick(symbol)

        self._events.put(answer)
        return 0

    def UnSubscribeMarketData(self, _instruments):
        return 0

    def _run(self):
        while True:
            function = self._events.get()
            if function is None:
                return
            if self.spi is not None:
                function()

    def _tick(self, symbol):
        self._volume += 2
        self._second = (self._second + 1) % 60
        day = self.front.trading_day
        self.spi.OnRtnDepthMarketData(
            SimpleNamespace(
                InstrumentID=symbol,
                ExchangeID="SHFE",
                LastPrice=3_100.0,
                PreSettlementPrice=3_090.0,
                OpenPrice=3_095.0,
                HighestPrice=3_110.0,
                LowestPrice=3_080.0,
                BidPrice1=3_099.0,
                BidVolume1=5,
                AskPrice1=3_101.0,
                AskVolume1=6,
                OpenInterest=1_000.0,
                Volume=self._volume,
                Turnover=1.0,
                UpperLimitPrice=3_300.0,
                LowerLimitPrice=2_900.0,
                TradingDay=day,
                ActionDay=day,
                UpdateTime=f"21:00:{self._second:02d}",
                UpdateMillisec=0,
            )
        )

    def drop(self):
        self._events.put(lambda: self.spi.OnFrontDisconnected(4097))

    def tick(self, symbol):
        self._events.put(lambda: self._tick(symbol))


@pytest.fixture
def ctp_front(monkeypatch):
    client_module = pytest.importorskip("bt_api_ctp.ctp.client")
    pytest.importorskip("bt_api_ctp.market_stream_provider")
    front = _Front()
    monkeypatch.setattr(client_module, "_check_native_module", lambda: None)
    monkeypatch.setattr(client_module, "_flow_dir", lambda _name: "offline-flow")
    monkeypatch.setattr(
        client_module.CThostFtdcMdApi,
        "CreateFtdcMdApi",
        staticmethod(lambda _flow: _FakeMdApi(front)),
    )
    yield front
    for api in front.apis:
        if not api.released:
            api.Release()


def _poll_until(owner, subscription, predicate, timeout: float = 5.0):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        result = owner.poll(subscription, timeout_ms=20, max_events=16)
        if predicate(result):
            return result
    raise AssertionError("owner never reached the expected state")


def test_direct_ctp_channel_resumes_on_the_new_session_after_an_acknowledged_resync(ctp_front):
    from bt_api_ctp.feeds.live_ctp_feed import CtpRequestDataFuture
    from bt_api_ctp.market_stream_provider import _create_ctp_market_stream_binding

    feed = CtpRequestDataFuture(
        broker_id="9999",
        user_id="investor",
        password="secret",
        td_front="tcp://127.0.0.1:19801",
        md_front="tcp://127.0.0.1:19802",
        auto_settlement_confirm=False,
        ctp_env_profile="config_front_pair",
        ctp_environment="simnow",
    )
    owner = ProviderMarketStreamOwner(_Api(feed), router=_Router(_create_ctp_market_stream_binding))
    subscription = owner.subscribe(
        MarketStreamRequest(exchange_name=EXCHANGE, instrument=_instrument())
    )
    first = _poll_until(owner, subscription, lambda result: result.events)
    assert first.events[0].cursor.sequence == 1
    old_source = first.source

    # The front drops; the provider stops the lost MD session, starts a new
    # one, which logs in (a new trading day) and subscribes again.
    ctp_front.trading_day = "20261109"
    ctp_front.apis[0].drop()
    lost = _poll_until(
        owner, subscription, lambda result: result.state is MarketStreamState.RECOVERY_REQUIRED
    )
    assert lost.recovery_code == "PROVIDER_RESYNC_REQUIRED"
    assert ctp_front.apis[0].released is True

    resumed = owner.accept_resync(subscription, acknowledge_data_loss=True)
    assert resumed.state is MarketStreamState.ACTIVE
    assert resumed.source.epoch != old_source.epoch
    assert resumed.source.stream_id == old_source.stream_id
    assert resumed.source.connection_generation > old_source.connection_generation

    ctp_front.apis[-1].tick("rb2701")
    events = []
    deadline = time.monotonic() + 5.0
    while time.monotonic() < deadline and len(events) < 2:
        events.extend(owner.poll(subscription, timeout_ms=20, max_events=16).events)
    assert [event.cursor.sequence for event in events][:2] == [1, 2]
    assert {event.source for event in events} == {resumed.source}
    assert events[0].data.trading_date in ("20261109", "2026-11-09")
    assert events[0].data.continuity_status == "continuous"
    owner.close()
