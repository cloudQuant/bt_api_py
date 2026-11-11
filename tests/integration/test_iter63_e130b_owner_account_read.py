"""I63-E130: the account owner serves other work while an account read is outstanding.

The real CTP trading profile, owner thread, read model, return maintenance, CTP
session, account provider and TraderClient, with only the native boundary
substituted. The account query of the read model is answered only when the test
releases it, as a slow SimNow front does. No account or network is contacted.
"""

import importlib
import threading
import time
from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace

import pytest
from bt_api_base.account_protocol import AccountKey, AccountResourceKey
from bt_api_ctp.ctp.client import CtpNativeStopReceipt, TraderClient
from bt_api_ctp.feeds.live_ctp_feed import CtpRequestDataFuture
from bt_api_ctp.gateway_session import CtpGatewaySession

import bt_api_py.ctp_gateway_return_maintenance as return_module
from bt_api_py import ctp_gateway_trading as module
from bt_api_py.ctp_gateway_trading import build_ctp_gateway_trading_profile
from tests.integration.test_iter63_ctp_gateway_return_maintenance import _relogin_same_spi
from tests.test_iter63_ctp_gateway_trading import CLIENT_A, _read_profile, _resources, _wait

_ACCOUNT_ROW = {"AccountID": "account", "BrokerID": "9999", "CurrencyID": "CNY",
                "Balance": 1000, "Available": 800}


@pytest.fixture
def owner_harness(tmp_path, monkeypatch, request):
    """Start the profile; ``harness["arm"]`` holds the next account query until released."""
    monkeypatch.syspath_prepend(str(Path(__file__).resolve().parents[2] / "bt_api/bt_api_ctp/tests"))
    helper = importlib.import_module("test_iter62_settlement_readback_on_write")
    real_loader = module._load_ctp_types
    real_returns = return_module.CtpGatewayReturnMaintenance
    key = AccountKey("ctp", "simnow", "simnow", "account", "9999")
    _, settings, config, authority, grants, read = _read_profile(tmp_path, monkeypatch, account_key=key)
    settings = replace(settings, write_enabled=True, maintenance_interval_seconds=0.01,
                       settlement_refresh_seconds=0.05)
    read = replace(read, required_coverage=(AccountResourceKey("future", "futures"),),
                   refresh_interval_seconds=0.05, max_staleness_seconds=2,
                   request_timeout_seconds=request.param)
    monkeypatch.setattr(module, "_load_ctp_types", real_loader)
    lock = threading.Lock()
    held, arm, events, natives = [], threading.Event(), [], []
    reading = [False]  # an account read was begun and has not ended

    def outstanding():
        with lock:
            return len(held)

    def record(what):
        with lock:
            events.append((what, len(held), reading[0], time.monotonic()))

    real_begin = CtpGatewaySession.begin_read_account
    real_poll = CtpGatewaySession.poll_read_account
    real_cancel = CtpGatewaySession.cancel_read_account

    def begin_read_account(session, **kwargs):
        request = real_begin(session, **kwargs)
        reading[0] = True
        return request

    def poll_read_account(session, request):
        try:
            observation = real_poll(session, request)
        except BaseException:
            reading[0] = False
            raise
        if observation is not None:
            reading[0] = False
        return observation

    def cancel_read_account(session, request):
        reading[0] = False
        return real_cancel(session, request)

    monkeypatch.setattr(CtpGatewaySession, "begin_read_account", begin_read_account)
    monkeypatch.setattr(CtpGatewaySession, "poll_read_account", poll_read_account)
    monkeypatch.setattr(CtpGatewaySession, "cancel_read_account", cancel_read_account)

    original_tick = module.GatewaySettlementController.tick

    def tick(controller):
        record("settlement")
        return original_tick(controller)

    monkeypatch.setattr(module.GatewaySettlementController, "tick", tick)

    class RecordingReturns(real_returns):
        def start_query(self):
            if self._request is None:
                record("return_query")
            return super().start_query()

    monkeypatch.setattr(return_module, "CtpGatewayReturnMaintenance", RecordingReturns)

    def start(trader, **kwargs):
        native = helper._NativeApi()
        native.client = trader

        def immediate(kind, rows):
            def call(field, request_id):
                for row in rows:
                    trader._handle_query_callback(kind, row, None, request_id, False)
                trader._handle_query_callback(kind, None, None, request_id, True)
                return 0
            return call

        answer_account = immediate("account", [_ACCOUNT_ROW])

        def account(field, request_id):
            record("account_query")
            if not arm.is_set():
                return answer_account(field, request_id)
            arm.clear()
            with lock:
                held.append((trader, request_id))
            return 0  # accepted; the answer comes when the test releases it

        native.ReqQryTradingAccount = account
        native.ReqQryInvestorPosition = immediate("positions", [])
        native.ReqQryOrder = immediate("orders", [])
        native.ReqQryTrade = immediate("trades", [])
        trader._api = native
        helper._login(trader, native, session_id=19)
        # A real spacing between the queries of the session (1.05 s on SimNow):
        # an account read is outstanding between its queries while no native
        # query is, so only the owner's own state keeps others from starting.
        trader._query_interval = 0.15
        natives.append(native)

    monkeypatch.setattr(TraderClient, "start", start)
    monkeypatch.setattr(TraderClient, "stop_and_wait", lambda trader, **kwargs: CtpNativeStopReceipt(
        trader.connection_generation, False, True, True, False, False))

    def release(*, error=False):
        with lock:
            items, held[:] = list(held), []
        for trader, request_id in items:
            if error:  # the venue answers the query with an error
                info = SimpleNamespace(ErrorID=90, ErrorMsg="synthetic")
                trader._handle_query_callback("account", None, info, request_id, True)
                continue
            trader._handle_query_callback("account", _ACCOUNT_ROW, None, request_id, False)
            trader._handle_query_callback("account", None, None, request_id, True)

    def feed():
        return CtpRequestDataFuture(
            None, broker_id="9999", user_id="account", password="synthetic",
            td_front="tcp://offline-neutral-td.invalid:30001",
            md_front="tcp://offline-neutral-md.invalid:30011",
            auto_settlement_confirm=False, ctp_env_profile="config_front_pair",
            ctp_environment="simnow", trusted_flow_root=str(settings.trusted_flow_root),
            flow_account_scope=key.account_key_id)

    profile = build_ctp_gateway_trading_profile(
        config, authority, tmp_path / "e130b.sqlite", profile_ref="e130b", settings=settings,
        grants=grants, request_feed_factory=feed, market_stream_factory=lambda *_: None,
        assert_admitted=lambda _: True, account_read_settings=read,
        account_read_principals=frozenset({CLIENT_A.principal_ref}),
        # Return queries are due again 50 ms after each pair: they would start at
        # once if an outstanding account read did not hold them back.
        return_settings=module.CtpGatewayReturnSettings(
            read.observation_instruments, query_timeout_seconds=30, query_interval_seconds=0.05),
    )
    resources = _resources(profile, authority)
    harness = {"resources": resources, "owner": resources.values["binding"]._owner,
               "model": resources.values["read_service"].state_source, "arm": arm,
               "outstanding": outstanding, "events": events, "lock": lock, "release": release,
               "natives": natives, "helper": helper}
    try:
        _wait(lambda: harness["model"].ready)
        yield harness
    finally:
        release()
        resources.close()


def _serve_while(harness, seconds):
    """Owner service-call latencies over ``seconds``, each bounded by the 1 s RPC timeout."""
    latencies, deadline = [], time.monotonic() + seconds
    while time.monotonic() < deadline:
        started = time.monotonic()
        assert harness["owner"].submit_service_call(lambda: 7).result(timeout_seconds=1.0) == 7
        latencies.append(time.monotonic() - started)
        time.sleep(0.02)
    return latencies


def _events_since(harness, index):
    with harness["lock"]:
        return list(harness["events"][index:])


@pytest.mark.parametrize("owner_harness", [30.0], indirect=True)
def test_owner_serves_calls_and_starts_no_query_while_an_account_read_waits(owner_harness):
    harness = owner_harness
    harness["arm"].set()
    _wait(lambda: harness["outstanding"]() == 1)
    with harness["lock"]:
        start = len(harness["events"])
    latencies = _serve_while(harness, 1.0)
    # The account query is still waiting for the venue; the owner answered anyway.
    assert harness["outstanding"]() == 1
    assert len(latencies) >= 10 and max(latencies) < 0.3, latencies
    # Neither a return query nor a settlement observation started behind it.
    assert _events_since(harness, start) == []
    harness["release"]()
    # The read completes, and both resume afterwards.
    _wait(lambda: {what for what, _, _, _ in _events_since(harness, start)}
          >= {"settlement", "return_query", "account_query"})
    assert all(count == 0 for _, count, _, _ in _events_since(harness, start))
    _wait(lambda: harness["model"].ready)
    assert harness["owner"].status().thread_alive
    # From start-up on, neither ever started while an account read was
    # outstanding, also between its spaced queries when no native query was.
    with harness["lock"]:
        everything = list(harness["events"])
    assert [entry for entry in everything
            if entry[0] in {"settlement", "return_query"} and entry[2]] == []
    assert sum(entry[0] == "account_query" for entry in everything) >= 3


@pytest.mark.parametrize("owner_harness", [30.0], indirect=True)
def test_account_read_answered_with_an_error_fails_and_the_next_one_serves(owner_harness):
    harness = owner_harness
    model = harness["model"]
    harness["arm"].set()
    _wait(lambda: harness["outstanding"]() == 1)
    with harness["lock"]:
        start = len(harness["events"])
    harness["release"](error=True)
    # The failed read ends (recoverable on a stable session); the owner goes on
    # and its next read completes.
    _wait(lambda: any(entry[0] == "account_query" for entry in _events_since(harness, start)))
    _wait(lambda: model.ready)
    status = harness["owner"].status()
    assert status.thread_alive and status.failure_category is None


@pytest.mark.parametrize("owner_harness", [0.5], indirect=True)
def test_account_read_that_times_out_fails_without_holding_the_owner(owner_harness):
    harness = owner_harness
    harness["arm"].set()
    _wait(lambda: harness["outstanding"]() == 1)
    held_at = time.monotonic()
    model = harness["model"]
    latencies, refused, failed_at = [], 0, None
    while time.monotonic() - held_at < 3 and failed_at is None:
        status = harness["owner"].status()
        if status.failure_category is not None or not status.thread_alive:
            failed_at = time.monotonic()
            break
        started = time.monotonic()
        try:
            harness["owner"].submit_service_call(lambda: 7).result(timeout_seconds=1.0)
        except Exception:
            refused += 1  # the owner is closing after the failed read
            continue
        latencies.append(time.monotonic() - started)
        time.sleep(0.02)
    assert refused <= 1
    # Served promptly while the read was outstanding, for about its 0.5 s budget.
    assert len(latencies) >= 5 and max(latencies) < 0.3, latencies
    # A read model read that overran its budget is fatal on a stable session, as
    # before (test_stable_session_read_and_finish_faults_are_fatal[read_budget]).
    # The budget counts from the read's start, at most one 0.15 s spacing before the send.
    assert failed_at is not None and failed_at - held_at >= 0.3
    assert harness["owner"].status().failure_category == "maintenance_failed"
    assert not model.ready
    harness["release"]()  # the late answer is never used
    time.sleep(0.1)
    assert not model.ready


@pytest.mark.parametrize("owner_harness", [30.0], indirect=True)
def test_account_read_whose_session_changed_is_never_used(owner_harness):
    harness = owner_harness
    model = harness["model"]
    harness["arm"].set()
    _wait(lambda: harness["outstanding"]() == 1)
    native = harness["natives"][0]
    old_generation = native.client.connection_generation
    old_epoch = model.session_generation
    native.client._spi.OnFrontDisconnected(0x1001)
    _relogin_same_spi(native, harness["helper"].TODAY, 20)
    new_generation = native.client.connection_generation
    assert new_generation > old_generation
    # The old session's answer arrives late; it is never used.
    harness["release"]()
    _wait(lambda: model.ready and model.session_generation != old_epoch)
    assert f":native:{new_generation}:" in model.session_generation
    assert harness["owner"].status().thread_alive
    assert harness["owner"].status().failure_category is None
