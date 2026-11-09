"""Synthetic CTP service wires owner coordinator before native MD start."""
import sys
from pathlib import Path

import pytest

from tests.integration import test_iter63_async_market_bootstrap_service as fixtures
from tests.integration import test_iter63_ctp_gateway_joint_service as joint


def _gateway_with_observers(*args):
    ctp_tests = Path(joint.__file__).resolve().parents[2] / "bt_api" / "bt_api_ctp" / "tests"
    sys.path.insert(0, str(ctp_tests))
    from test_market_provider import _SyntheticMdClient
    from bt_api_ctp.gateway_session import CtpGatewaySession
    from bt_api_ctp.feeds.live_ctp_feed import CtpMarketStream
    from bt_api_py.gateway_dispatch_claim import DispatchClaimError

    seen = []
    streams = []
    sessions = []
    original_session = CtpGatewaySession._bind_gateway_md_stream_dispatch_coordinator
    original_stream = CtpMarketStream._bind_gateway_md_stream_dispatch_coordinator

    def session_bind(self, coordinator, *, account_scope, service_generation):
        assert coordinator.matches_owner(account_scope, service_generation)
        try:
            original_session(self, coordinator, account_scope=account_scope, service_generation=service_generation)
        except Exception as error:
            args[7].put(("session-hook-error", type(error).__name__, getattr(error, "code", None)))
            raise
        seen.append((coordinator, account_scope, service_generation))
        sessions.append(self)
        try:
            coordinator.capture()
        except DispatchClaimError as error:
            assert error.code == "COVERAGE_UNAVAILABLE"
        else:
            raise AssertionError("partial binding must not enable claims")

    def stream_bind(self, coordinator, *, account_scope, service_generation):
        assert len(seen) == 1
        assert (coordinator, account_scope, service_generation) == seen[0]
        assert coordinator is seen[0][0]
        assert all(value is not self for value in streams)
        original_stream(self, coordinator, account_scope=account_scope, service_generation=service_generation)
        streams.append(self)
        args[7].put(("stream-bound", len(streams)))

    def fake_native_bind(self, coordinator, *, account_scope, service_generation):
        assert len(seen) == 1 and coordinator is seen[0][0]
        assert (account_scope, service_generation) == seen[0][1:]
        assert sessions[0]._trader._gateway_td_dispatch_coordinator is coordinator
        args[7].put(("native-bound", True))

    CtpGatewaySession._bind_gateway_md_stream_dispatch_coordinator = session_bind
    CtpMarketStream._bind_gateway_md_stream_dispatch_coordinator = stream_bind
    _SyntheticMdClient._bind_gateway_md_dispatch_coordinator = fake_native_bind
    return joint._gateway(*args)


@pytest.fixture
def gateway(tmp_path, monkeypatch):
    monkeypatch.setattr(fixtures, "_gateway", _gateway_with_observers)
    yield from fixtures.async_gateway.__wrapped__(tmp_path)


def test_sdk_profile_binds_real_checked_stream_before_synthetic_native_start(gateway):
    gateway["release"].set()
    session = fixtures.strategy(gateway)
    boot = session.market_client.subscription("ticks").start(timeout_seconds=6)
    assert boot.source_descriptor.key == joint._key("IF2612")
    assert gateway["events"].get(timeout=5) == ("stream-bound", 1)
    assert gateway["events"].get(timeout=5) == ("native-bound", True)
    assert gateway["child"].is_alive()
