"""Clock source runs on the borrowed native owner, never on the RPC thread."""

import threading
import time

import pytest

from bt_api_py.gateway_owner_market import GatewayOwnerMarketError, GatewayOwnerMarketProvider
from tests.test_iter63_gateway_direct_owner import Harness
from tests.unit.test_iter63_gateway_owner_market import Native
from tests.unit.test_iter63_gateway_owner_service_calls import owner_for
from tests.unit.test_service_market import _KEY


def test_clock_uses_native_handle_and_missing_source_fails_closed(tmp_path):
    harness = Harness(tmp_path)
    owner = owner_for(harness.factory)
    assert owner.start(2)
    holder = {}

    def native():
        if "native" not in holder:
            holder["native"] = Native()
        return holder["native"]

    class Clock:
        def read(self, handle, **kwargs):
            assert threading.get_ident() == holder["native"].thread
            assert handle in holder["native"].active
            return {"schema_version": 1, "epoch": kwargs["epoch"]}

        def read_internal(self, handle, **kwargs):
            assert threading.get_ident() == holder['native'].thread
            assert handle in holder['native'].active
            assert kwargs['provider_sequence'] == 7 and kwargs['provider_subscription_epoch'] == 2
            return {'schema_version': 2, 'epoch': kwargs['epoch']}, time.monotonic_ns()

    bridge = GatewayOwnerMarketProvider(owner, native, clock_source_getter=lambda: holder.get("clock"))
    try:
        handle = bridge.subscribe(_KEY, generation="gen-1")
        args = {"key": _KEY, "generation": "gen-1", "stream_id": _KEY.stream_id, "epoch": "ep-1"}
        with pytest.raises(GatewayOwnerMarketError, match="CLOCK_SOURCE_UNAVAILABLE"):
            bridge.read_market_login_clock(handle, **args)
        holder["clock"] = Clock()
        assert bridge.read_market_login_clock(handle, **args) == {"schema_version": 1, "epoch": "ep-1"}
        observation, source_mono = bridge.read_market_tick_clock(handle, **args,
            provider_sequence=7, provider_subscription_epoch=2)
        assert observation == {'schema_version': 2, 'epoch': 'ep-1'} and source_mono > 0
        with pytest.raises(GatewayOwnerMarketError, match='CLOCK_SOURCE_UNAVAILABLE'):
            bridge.recheck_market_tick_on_native_owner(handle, **args,
                provider_sequence=7, provider_subscription_epoch=2)
        checked = owner.submit_service_call(lambda: bridge.recheck_market_tick_on_native_owner(handle, **args,
            provider_sequence=7, provider_subscription_epoch=2)).result(timeout_seconds=1)
        assert checked[0] == observation and checked[1] > 0
        with pytest.raises(GatewayOwnerMarketError, match='CLOCK_SOURCE_UNAVAILABLE'):
            bridge.read_market_tick_clock(handle, **args, provider_sequence=True, provider_subscription_epoch=2)
        with pytest.raises(GatewayOwnerMarketError, match="INVALID_MARKET_REQUEST"):
            bridge.read_market_login_clock(handle, **{**args, "generation": "forged"})
    finally:
        bridge.close()
        assert owner.close(2)
        assert bridge.close()


def test_profile_calibration_precedes_td_login(tmp_path, monkeypatch):
    import bt_api_py.ctp_gateway_clock_source as clock_module
    from tests.test_iter63_ctp_gateway_trading import FakeSession
    from tests.unit.test_iter63_ctp_gateway_market_profile import setup

    events = []
    original_connect = FakeSession.connect

    def connect(self, *args, **kwargs):
        events.append("connect")
        return original_connect(self, *args, **kwargs)

    def capture(verifier, *, allowed_servers):
        assert verifier is sentinel
        assert allowed_servers == frozenset({'clock.example'})
        events.append("capture")
        return anchor

    class Source:
        def __init__(self, session, calibration, *, max_age_seconds):
            assert calibration is anchor and max_age_seconds == 300
            events.append("source")

    sentinel, anchor = object(), object()
    monkeypatch.setattr(FakeSession, "connect", connect)
    monkeypatch.setattr(clock_module, "capture_gateway_server_clock_calibration", capture)
    monkeypatch.setattr(clock_module, "CtpGatewayCalibratedClockSource", Source)
    build, _, _, authority, _ = setup(tmp_path, monkeypatch)
    profile = build(clock_verifier_factory=lambda: sentinel, clock_allowed_servers=frozenset({'clock.example'}))
    deployment = profile.deployment_factory()
    resources = deployment._provide_resources(
        "gateway", "gateway-a", {"role": "trading_owner", "dispatch_enabled": True}, authority.scope
    )
    try:
        assert events == ["capture", "connect", "source"]
    finally:
        resources.close()
