"""I63-E130: a due settlement observation waits for a free query lane, and nothing overtakes it.

The settlement observation still waits for the venue on the owner thread; it is
started only when the session's Trader would send at once, so the owner waits
for its answer only, never for the query spacing or an outstanding query. Until
it ran, no new return query or account read starts. A lane that stays busy
(an unanswered query) no longer defers it after a bound.

Synthetic CTP profile (fake session and provider, real owner thread and
journal); no native account is contacted.
"""

import threading
import time
from dataclasses import replace

from bt_api_base.market_protocol import MarketInstrumentKey

import bt_api_py.ctp_gateway_return_maintenance as return_module
from bt_api_py import ctp_gateway_trading as module
from bt_api_py.ctp_gateway_trading import build_ctp_gateway_trading_profile
from tests.test_iter63_ctp_gateway_trading import FakeReturnMaintenance, FakeSession, _wait, make


def _start(tmp_path, monkeypatch, lane):
    _, settings, config, authority, grants, _ = make(tmp_path, monkeypatch, write_enabled=True)
    # Due on every tick, so it would run at once if nothing made it wait.
    settings = replace(settings, settlement_refresh_seconds=0)
    events, lock = [], threading.Lock()

    def record(what):
        with lock:
            events.append((what, time.monotonic()))

    class EagerReturns(FakeReturnMaintenance):
        """A return query is always due; each one ends on the tick it started."""

        def due(self, now):
            return True

        def start_query(self):
            record("return_query")

    monkeypatch.setattr(return_module, "CtpGatewayReturnMaintenance", EagerReturns)
    monkeypatch.setattr(FakeSession, "query_lane_ready", lambda self: lane["ready"])

    def tick(controller):
        record("settlement")
        return controller.status

    monkeypatch.setattr(module.GatewaySettlementController, "tick", tick)
    profile = build_ctp_gateway_trading_profile(
        config, authority, tmp_path / "lane-registry.sqlite", profile_ref="lane",
        settings=settings, grants=grants, request_feed_factory=lambda: None,
        market_stream_factory=lambda *_: None, assert_admitted=lambda _: True,
        return_settings=module.CtpGatewayReturnSettings(
            (MarketInstrumentKey("ctp", "SHFE", "simnow", "future", "rb2612", "rb2612"),)),
    )
    deployment = profile.deployment_factory()
    resources = deployment._provide_resources(
        "gateway", "gateway-a", {"role": "trading_owner", "dispatch_enabled": True}, authority.scope)

    def seen():
        with lock:
            return list(events)

    return resources, seen


def test_due_settlement_waits_for_a_free_lane_and_no_query_overtakes_it(tmp_path, monkeypatch):
    lane = {"ready": False}
    resources, seen = _start(tmp_path, monkeypatch, lane)
    try:
        time.sleep(0.5)  # about fifty owner ticks with a busy lane
        assert seen() == []
        lane["ready"] = True
        _wait(lambda: {what for what, _ in seen()} == {"settlement", "return_query"})
        first = [what for what, _ in seen()]
        assert first[0] == "settlement"
    finally:
        resources.close()


def test_a_lane_that_stays_busy_no_longer_defers_the_settlement_after_a_bound(
        tmp_path, monkeypatch):
    lane = {"ready": False}
    started = time.monotonic()
    resources, seen = _start(tmp_path, monkeypatch, lane)
    try:
        deadline = time.monotonic() + 15
        while not any(what == "settlement" for what, _ in seen()):
            assert time.monotonic() < deadline
            time.sleep(0.05)
        ran_at = next(at for what, at in seen() if what == "settlement")
        # Not before the 5 s bound; afterwards the held queries resume as well.
        assert ran_at - started >= 4.9
        _wait(lambda: any(what == "return_query" for what, _ in seen()))
    finally:
        resources.close()
