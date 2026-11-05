"""Iteration 62 (62.08): Gateway CTP market through the SDK operator profile.

The SDK composition ``build_ctp_simnow_set2_market_operator_profile`` used to
require subscription keys on channel ``ctp.quote.v2`` while the CTP provider
(``CtpQuoteV2MarketProvider``) accepts only its neutral ``"tick"`` channel, so
no Gateway CTP key could ever be subscribed; the profile was also fixed to the
SimNow ``set2_7x24_4000x`` fronts. This test builds the profile for the SimNow
standard-environment profile ``set1_group1_vpn``, serves it on a real
``ServiceHost`` (CURVE ZMQ on 127.0.0.1), subscribes with ``ServiceMarketClient``
and receives one tick. The native MD client is a synthetic stand-in that
records the front it was given.

INTEGRATION_SIM: local host thread, temp SQLite, loopback CURVE; no real CTP
front, account or network.
"""

from __future__ import annotations

import queue
import threading
import time
from contextlib import suppress
from pathlib import Path
from types import SimpleNamespace

import pytest

zmq = pytest.importorskip("zmq")

from bt_api_base.market_protocol import (  # noqa: E402
    MarketInstrumentKey,
    MarketSubscriptionKey,
)
from bt_api_base.service_protocol import WireIdentity, WireRole, WireScope  # noqa: E402
from bt_api_ctp.ctp_env_selector import official_simnow_fronts  # noqa: E402
from bt_api_ctp.feeds import live_ctp_feed  # noqa: E402

from bt_api_py.ctp_account_process import (  # noqa: E402
    build_ctp_simnow_set2_market_operator_profile,
    ctp_simnow_gateway_refs,
)
from bt_api_py.service_config import parse_client_config, parse_service_config  # noqa: E402
from bt_api_py.service_market import (  # noqa: E402
    ServiceMarketClient,
    ZmqMarketTransportFactory,
)
from bt_api_py.service_runtime import (  # noqa: E402
    ClientCurveKeys,
    ServiceClientRuntime,
    ZmqTransportFactory,
)
from tests.integration.test_gateway_operator_ctp_market import (  # noqa: E402
    _SyntheticMdClient,
)
from tests.integration.test_gateway_operator_deployment import (  # noqa: E402
    _ACCOUNT_REF,
    _DOMAIN_REF,
    _MARKET_OPERATIONS,
    _PRINCIPAL_REF,
    _SECURITY_REF,
    _STRATEGY_REF,
    _free_endpoint,
    _market_authority,
    _market_client_config,
    _market_service_config,
)

_PROFILE = "set1_group1_vpn"


class _RecordingMdClient(_SyntheticMdClient):
    fronts: list[str] = []

    def __init__(self, md_front: str, *args: object, **kwargs: object) -> None:
        self.__class__.fronts.append(md_front)
        super().__init__(md_front, *args)

    def emit_tick(self, symbol: str = "rb2701") -> None:
        # SimNow MD rows leave ExchangeID empty; the key supplies the venue.
        assert callable(self.on_tick)
        self.on_tick(
            SimpleNamespace(
                InstrumentID=symbol,
                ExchangeID="",
                LastPrice=3_050.0,
                PreSettlementPrice=3_040.0,
                OpenPrice=3_045.0,
                HighestPrice=3_060.0,
                LowestPrice=3_030.0,
                BidPrice1=3_049.0,
                BidVolume1=5,
                AskPrice1=3_050.0,
                AskVolume1=7,
                OpenInterest=10_000.0,
                Volume=321,
                Turnover=9_790_000.0,
                UpperLimitPrice=3_250.0,
                LowerLimitPrice=2_850.0,
                TradingDay="20261106",
                ActionDay="20261105",
                UpdateTime="21:30:01",
                UpdateMillisec=500,
            )
        )


def _key(refs, symbol: str) -> MarketSubscriptionKey:
    return MarketSubscriptionKey(
        source_ref=refs.market_source_ref,
        entitlement_ref=refs.market_entitlement_ref,
        instrument=MarketInstrumentKey(
            provider="ctp",
            venue="SHFE",
            environment="demo",
            product_type="future",
            instrument_id=symbol,
            native_symbol=symbol,
        ),
        channel="tick",
    )


@pytest.mark.integration
def test_set1_market_profile_serves_a_tick_on_the_provider_channel(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    _SyntheticMdClient.instances.clear()
    _RecordingMdClient.fronts = []
    monkeypatch.setattr(live_ctp_feed.ctp_client, "MdClient", _RecordingMdClient)
    # The profile's provider factory reads the trusted local environment only
    # when the hub first subscribes; these are synthetic placeholders.
    monkeypatch.setenv("CTP_USER_ID", "synthetic-user")
    monkeypatch.setenv("CTP_BROKER_ID", "synthetic-broker")
    monkeypatch.setenv("CTP_PASSWORD", "synthetic-password")

    refs = ctp_simnow_gateway_refs(_PROFILE)
    key = _key(refs, "rb2701")
    config = parse_service_config(_market_service_config(_free_endpoint(), _free_endpoint()))
    server_keys = zmq.curve_keypair()
    client_public, client_secret = zmq.curve_keypair()
    authority, service_identity = _market_authority(config, server_keys, client_public)
    profile = build_ctp_simnow_set2_market_operator_profile(
        config,
        authority,
        tmp_path / "ctp-market-registry.sqlite3",
        key_universe=(key,),
        principal_allowed_keys={_PRINCIPAL_REF: frozenset({key})},
        ctp_profile=_PROFILE,
    )
    assert profile.profile_ref == refs.market_operator_profile_ref
    # Serve the profile's own deployment on a daemon owner thread (as the
    # existing operator CTP market test does): a failing assertion must not
    # leave a non-daemon operator owner alive at interpreter exit.
    deployment = profile.deployment_factory()
    startup: queue.Queue[tuple[str, object, object]] = queue.Queue()
    stop = threading.Event()

    def serve_host() -> None:
        try:
            deployment.start()
            startup.put(("started", deployment.endpoint, deployment.market_endpoint))
            while not stop.is_set():
                deployment.serve_once(10)
        except BaseException as exc:
            startup.put(("error", getattr(exc, "code", "HOST_START_FAILED"), None))
        finally:
            if deployment.state not in {"closed", "failed"}:
                with suppress(BaseException):
                    deployment.close()

    owner = threading.Thread(target=serve_host, name="iter62-ctp-market-profile", daemon=True)
    owner.start()
    runtime: ServiceClientRuntime | None = None
    market: ServiceMarketClient | None = None
    try:
        started = startup.get(timeout=15)
        assert started[0] == "started", started
        rpc_endpoint, market_endpoint = started[1], started[2]
        assert isinstance(rpc_endpoint, str) and isinstance(market_endpoint, str)

        client_config = parse_client_config(_market_client_config(rpc_endpoint, market_endpoint))
        client_security = ClientCurveKeys(server_keys[0], client_public, client_secret)
        runtime = ServiceClientRuntime(
            client_config,
            identity=WireIdentity(WireRole.STRATEGY_CLIENT, _PRINCIPAL_REF, _STRATEGY_REF),
            scope_resolver=lambda _ref: WireScope(
                _DOMAIN_REF, account_ref=_ACCOUNT_REF, strategy_ref=_STRATEGY_REF
            ),
            peer_resolver=lambda _service_id: service_identity,
            transport_factory=ZmqTransportFactory({_SECURITY_REF: client_security}),
            requested_operations={"store.market": _MARKET_OPERATIONS},
        )
        runtime.start()
        market = ServiceMarketClient(
            runtime,
            "store.market",
            key,
            subscriber_factory=ZmqMarketTransportFactory({_SECURITY_REF: client_security}),
            stream_scope=WireScope(_DOMAIN_REF),
            head_check_interval_seconds=1.0,
            require_source_descriptor=True,
        )
        bootstrap = market.start()
        assert bootstrap.source_descriptor is not None
        assert bootstrap.source_descriptor.key == key

        # The selected SimNow profile's MD front reached the native client.
        assert _RecordingMdClient.fronts == [official_simnow_fronts(_PROFILE)[1]]
        (client,) = _SyntheticMdClient.instances
        assert client.subscribed == ["rb2701"]
        client.emit_tick("rb2701")
        deadline = time.monotonic() + 5.0
        event = None
        while event is None and time.monotonic() < deadline:
            event = market.poll(timeout_ms=25)
        assert event is not None
        assert event.payload["key"] == key.to_dict()
        assert event.payload["key"]["channel"] == "tick"
        assert event.payload["data"]["schema"] == "sdk.market.tick"
        assert event.payload["data"]["instrument"]["native_symbol"] == "rb2701"
        assert event.payload["data"]["provider_source"] == "ctp.native.md"
        assert event.payload["data"]["instrument"]["venue"] == "SHFE"
        assert event.payload["data"]["last_price"] == 3_050.0
        assert event.payload["data"]["execution_eligible"] is False

        market.close()
        market = None
        runtime.close()
        runtime = None
    finally:
        if market is not None:
            with suppress(Exception):
                market.close()
        if runtime is not None:
            with suppress(Exception):
                runtime.close()
        stop.set()
        owner.join(10)
    assert not owner.is_alive(), "operator Host owner thread did not close"
    assert deployment.state == "closed"
    assert _SyntheticMdClient.instances[-1].stop_wait_calls == 1
