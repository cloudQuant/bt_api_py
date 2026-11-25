"""I63-E130: a due settlement observation waits for a free query lane, and nothing overtakes it.

The settlement observation still waits for the venue on the owner thread; it is
started only when the session's Trader would send at once, so the owner waits
for its answer only, never for the query spacing or an outstanding query. Until
it ran, no new return query or account read starts. A lane that stays busy
(an unanswered query) no longer defers it after a bound.

Synthetic CTP profile (fake session and provider, real owner thread and
journal); no native account is contacted.
"""

import os
import threading
import time
from dataclasses import replace

import pytest
from bt_api_base.market_protocol import MarketInstrumentKey


@pytest.fixture(autouse=True)
def _protect_win32_tmp_path(tmp_path):
    if os.name == "nt":
        from bt_api_py._win32_security import _set_owner_only_acl
        _set_owner_only_acl(tmp_path)

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


def test_owner_maintain_fast_poll_hint_accelerates_in_flight_queries(tmp_path):
    from bt_api_py.gateway_direct_owner import GatewayDirectOwner, GatewayDirectOwnerResources
    from tests.test_iter63_gateway_direct_owner import Harness

    harness = Harness(tmp_path)
    calls: list[float] = []
    done = threading.Event()

    def maintain():
        calls.append(time.monotonic())
        if len(calls) < 4:
            return 0.02
        done.set()
        return None

    def factory():
        base = harness.factory()
        return replace(base, maintain=maintain, maintenance_interval_seconds=2.0)

    owner = GatewayDirectOwner(
        factory,
        queue_size=4,
        max_queue_wait_seconds=2.0,
        busy_timeout_seconds=2.0,
    )
    assert owner.start(5.0)
    try:
        t0 = time.monotonic()
        assert done.wait(1.5), f"expected 4 fast-polled maintain ticks, got {len(calls)}"
        elapsed = time.monotonic() - t0
        assert elapsed < 1.0, f"fast-poll hint should avoid 2.0s sleeps (elapsed={elapsed:.3f}s)"
        # After returning None, owner falls back to the normal 2.0s maintenance interval.
        count_after_done = len(calls)
        time.sleep(0.25)
        assert len(calls) == count_after_done
    finally:
        assert owner.close(5.0) is True


def test_account_read_waits_for_free_lane_and_polls_fast_while_pending(tmp_path, monkeypatch):
    from datetime import UTC, datetime

    from bt_api_base.account_protocol import AccountObservation
    from tests.integration.test_iter63_gateway_direct_service import CLIENT_A
    from tests.test_iter63_ctp_gateway_trading import _read_profile, _resources

    _, settings, config, authority, grants, read = _read_profile(tmp_path, monkeypatch)
    if os.name == "nt":
        from bt_api_py._win32_security import _set_owner_only_acl
        _set_owner_only_acl(read.observation_identity_path)
    # 1.0s normal maintenance interval (matches SimNow production) while 3-poll read completes fast.
    settings = replace(
        settings,
        write_enabled=True,
        maintenance_interval_seconds=1.0,
        settlement_refresh_seconds=25.0,
    )
    read = replace(
        read,
        refresh_interval_seconds=5.0,
        max_staleness_seconds=20.0,
        request_timeout_seconds=8.0,
    )

    lane = {"ready": False}
    begins: list[float] = []
    polls = {"count": 0}
    completed = threading.Event()

    monkeypatch.setattr(FakeSession, "query_lane_ready", lambda self: lane["ready"])
    monkeypatch.setattr(module.GatewaySettlementController, "tick", lambda self: self.status)

    def read_account(session, *, generation, timeout):
        stamp = datetime.now(UTC).isoformat().replace("+00:00", "Z")
        return AccountObservation(
            session.key,
            f"obs-{polls['count']}",
            read.required_coverage,
            (),
            (),
            None,
            stamp,
            stamp,
            "multi_query",
            True,
            True,
            (),
            True,
        )

    original_begin = FakeSession.begin_read_account
    original_poll = FakeSession.poll_read_account

    def tracked_begin(self, *, generation, timeout):
        begins.append(time.monotonic())
        return original_begin(self, generation=generation, timeout=timeout)

    def three_step_poll(self, request):
        polls["count"] += 1
        if polls["count"] < 3:
            return None
        obs = original_poll(self, request)
        completed.set()
        return obs

    monkeypatch.setattr(FakeSession, "read_account", read_account, raising=False)
    monkeypatch.setattr(FakeSession, "begin_read_account", tracked_begin)
    monkeypatch.setattr(FakeSession, "poll_read_account", three_step_poll)

    profile = build_ctp_gateway_trading_profile(
        config,
        authority,
        tmp_path / "read-lane-registry.sqlite",
        profile_ref="read-lane",
        settings=settings,
        grants=grants,
        request_feed_factory=lambda: None,
        market_stream_factory=lambda *_: None,
        assert_admitted=lambda _: True,
        return_settings=module.CtpGatewayReturnSettings(read.observation_instruments),
        account_read_settings=read,
        account_read_principals=frozenset({CLIENT_A.principal_ref}),
    )
    resources = _resources(profile, authority, cleanup_on_error=True)
    try:
        # While lane is busy, begin_read_account must wait instead of burning its timeout budget.
        time.sleep(0.35)
        assert begins == []

        t_free = time.monotonic()
        lane["ready"] = True
        assert completed.wait(1.5), f"expected 3-step read fast completion, begins={begins} polls={polls}"
        elapsed = time.monotonic() - t_free
        assert len(begins) >= 1
        assert polls["count"] >= 3
        # With 0.1s fast-poll hint, 3 polls finish well under 1.2s (would take >2.0s at 1.0s interval).
        assert elapsed < 1.2, f"expected fast multi-step account read, got {elapsed:.3f}s"
    finally:
        resources.close()
