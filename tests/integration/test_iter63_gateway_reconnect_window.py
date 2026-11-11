"""Bounded, synthetic same-Trader reconnect gates; no external CTP connection."""
from __future__ import annotations

import importlib
import threading
import time
from dataclasses import replace
from pathlib import Path

import pytest
from bt_api_base.account_protocol import AccountKey, AccountResourceKey
from bt_api_base.service_protocol import WireScope
from bt_api_ctp.ctp.client import CtpNativeStopReceipt, TraderClient
from bt_api_ctp.feeds.live_ctp_feed import CtpRequestDataFuture
from bt_api_gateway.session_guard import LocalAccountSessionGuard, LocalAccountSessionGuardError

import bt_api_py.ctp_gateway_return_maintenance as returns_module
import bt_api_py.ctp_gateway_trading as trading
import bt_api_py.gateway_instrument_service as instrument_module
from bt_api_py.gateway_direct_service import GatewayReservationAdmissionDenied
from bt_api_py.service_registry import ActiveRegistrationContext, ServiceCallContext
from tests.integration.test_iter63_ctp_gateway_return_maintenance import _relogin_same_spi
from tests.integration.test_iter63_gateway_direct_service import CLIENT_A, DOMAIN
from tests.test_iter63_ctp_gateway_trading import _read_profile, _resources, _wait


@pytest.fixture
def harness(tmp_path, monkeypatch, request):
    monkeypatch.syspath_prepend(str(Path(__file__).resolve().parents[2] / "bt_api/bt_api_ctp/tests"))
    native_helper = importlib.import_module("test_iter62_settlement_readback_on_write")
    instrument_helper = importlib.import_module("test_iter63_gateway_instrument_query")
    real_loader = trading._load_ctp_types
    real_returns = returns_module.CtpGatewayReturnMaintenance
    real_instrument = instrument_module.GatewayInstrumentService
    account = AccountKey("ctp", "simnow", "simnow", "account", "9999")
    _, settings, config, authority, grants, read = _read_profile(tmp_path, monkeypatch, account_key=account)
    monkeypatch.setattr(trading, "_load_ctp_types", real_loader)
    monkeypatch.setattr(returns_module, "CtpGatewayReturnMaintenance", real_returns)
    monkeypatch.setattr(instrument_module, "GatewayInstrumentService", real_instrument)
    account_only = getattr(request, "param", None) == "account_only"
    # The fence tests need settlement re-observed every maintenance cycle;
    # production refreshes a live ticket every 10 s (settlement_refresh_seconds).
    settings = replace(settings, write_enabled=not account_only, maintenance_interval_seconds=.01,
                       close_timeout_seconds=2, connect_timeout_seconds=1,
                       settlement_refresh_seconds=0)
    read = replace(read, required_coverage=(AccountResourceKey("future", "futures"),),
                   refresh_interval_seconds=.05, max_staleness_seconds=2, request_timeout_seconds=2)
    native_instances, feeds, return_instances, queries = [], [], [], []
    read_arm, read_entered, read_release = threading.Event(), threading.Event(), threading.Event()
    init = real_returns.__init__
    def capture_returns(self, *args, **kwargs):
        init(self, *args, **kwargs)
        return_instances.append(self)
    monkeypatch.setattr(real_returns, "__init__", capture_returns)

    def start(trader, **_kwargs):
        native = native_helper._NativeApi()
        native.client = trader
        def respond(kind, rows):
            def query(_field, request_id):
                queries.append((kind, trader.connection_generation))
                if kind == "account" and read_arm.is_set():
                    read_arm.clear()
                    read_entered.set()
                    if not read_release.wait(5):
                        raise TimeoutError("synthetic account query barrier")
                    # Disconnect seals this exact in-flight query locally.
                    return 0
                for row in rows:
                    trader._handle_query_callback(kind, row, None, request_id, False)
                trader._handle_query_callback(kind, None, None, request_id, True)
                return 0
            return query
        native.ReqQryTradingAccount = respond("account", [{
            "AccountID": "account", "BrokerID": "9999", "CurrencyID": "CNY",
            "Balance": 1000, "Available": 800,
        }])
        native.ReqQryInvestorPosition = respond("positions", [])
        native.ReqQryOrder = respond("orders", [])
        native.ReqQryTrade = respond("trades", [])
        def instrument(field, request_id):
            row = instrument_helper.row(InstrumentID=field.InstrumentID,
                                        ExchangeID=field.ExchangeID)
            trader._handle_query_callback("instruments", row, None, request_id, False)
            trader._handle_query_callback("instruments", None, None, request_id, True)
            return 0
        native.ReqQryInstrument = instrument
        trader._api = native
        native_helper._login(trader, native, session_id=19)
        native_instances.append(native)
    monkeypatch.setattr(TraderClient, "start", start)
    monkeypatch.setattr(TraderClient, "stop_and_wait", lambda trader, **_: CtpNativeStopReceipt(
        trader.connection_generation, False, True, True, False, False))

    def feed():
        obj = CtpRequestDataFuture(None, broker_id="9999", user_id="account", password="synthetic",
            td_front="tcp://offline-neutral-td.invalid:30001", md_front="tcp://offline-neutral-md.invalid:30011",
            auto_settlement_confirm=False, ctp_env_profile="config_front_pair", ctp_environment="simnow",
            trusted_flow_root=str(settings.trusted_flow_root), flow_account_scope=account.account_key_id)
        obj._connect_timeout = .05
        feeds.append(obj)
        return obj
    profile = trading.build_ctp_gateway_trading_profile(config, authority, tmp_path / "reconnect-registry.sqlite",
        profile_ref="reconnect-window", settings=settings, grants=grants, request_feed_factory=feed,
        market_stream_factory=lambda *_: pytest.fail("market must stay lazy"), assert_admitted=lambda _: True,
        account_read_settings=read, account_read_principals=frozenset({CLIENT_A.principal_ref}),
        return_settings=None if account_only else trading.CtpGatewayReturnSettings(
            read.observation_instruments, query_interval_seconds=.05))
    resources = _resources(profile, authority)
    service = resources.values["instrument_service"]
    owner = resources.values["binding"]._owner
    model = resources.values["read_service"].state_source
    context = ServiceCallContext(CLIENT_A, WireScope(DOMAIN, account_ref=settings.account_scope,
        strategy_ref=CLIENT_A.actor_ref, run_id="reconnect-test"), "registration", CLIENT_A.principal_ref, lambda _: True)
    active = ActiveRegistrationContext(context, "gateway-a", "service.account_read", "scope-a",
        instrument_module.INSTRUMENT_OPERATIONS, frozenset(), "epoch", "policy", "requirements", time.time() + 60)
    _wait(lambda: model.ready and (account_only or (
        return_instances and return_instances[0].status()["reservation_open"])))
    yield {"resources": resources, "settings": settings, "read": read, "service": service,
           "owner": owner, "model": model, "returns": None if account_only else return_instances[0],
           "context": context,
           "active": active, "native": native_instances[0], "natives": native_instances,
           "feeds": feeds, "queries": queries, "helper": native_helper,
           "read_arm": read_arm, "read_entered": read_entered, "read_release": read_release}
    read_release.set()
    resources.close()


def _spec_call(h, operation, opid="a" * 32):
    arguments = {"operation_id": opid}
    if operation == "begin":
        arguments["instrument"] = h["read"].observation_instruments[0].to_dict()
    return h["service"].handle("account.instrument_spec." + operation, context=h["context"], arguments=arguments)


def _spec_ready(h, opid="a" * 32):
    h["service"].maintain_registrations((h["active"],))
    result = _spec_call(h, "status", opid)
    assert result["status"] in {"pending", "ready"}, result
    return result if result["status"] == "ready" else None


def _pause_observed(h):
    status = h["returns"].status()
    return status["status"] == "SESSION_CHANGED" and not status["reservation_open"] and not h["model"].ready


def _assert_closed_fence_and_guard(h):
    assert h["owner"].binding() is not None and h["owner"].status().thread_alive
    assert len(h["natives"]) == len(h["feeds"]) == 1
    with pytest.raises(GatewayReservationAdmissionDenied), h["returns"].reservation_fence(None, None):
        pass
    contender = LocalAccountSessionGuard(Path(h["settings"].guard_root), h["settings"].account_scope, "other")
    with pytest.raises(LocalAccountSessionGuardError):
        contender.acquire()


def test_disconnect_preserves_guard_rejects_old_source_and_reconciles(harness):
    h = harness
    assert _spec_call(h, "begin")["status"] == "pending"
    old = _wait_result(lambda: _spec_ready(h))
    native, trader = h["native"], h["native"].client
    binding, spi = h["owner"].binding(), trader._spi
    spi.OnFrontDisconnected(0x1001)
    _wait(lambda: _pause_observed(h) or not h["owner"].status().thread_alive
          or h["owner"].status().failure_category is not None)
    assert _pause_observed(h), (h["owner"].status(), h["returns"].status(), h["model"].read_status())
    _assert_closed_fence_and_guard(h)
    assert h["owner"].binding() == binding and trader._spi is spi
    def old_rejected():
        h["service"].maintain_registrations((h["active"],))
        response = _spec_call(h, "status")
        assert response["status"] != "ready"
        return response["status"] == "failed"
    _wait(old_rejected)
    _relogin_same_spi(native, h["helper"].TODAY, 20)
    _wait(lambda: h["model"].ready and h["returns"].status()["reservation_open"])
    assert h["owner"].binding() == binding and trader._spi is spi
    assert _spec_call(h, "begin", "b" * 32)["status"] == "pending"
    new = _wait_result(lambda: _spec_ready(h, "b" * 32))
    assert new["snapshot"]["connection_generation"] > old["snapshot"]["connection_generation"]


def _wait_result(fn):
    value = []
    def done():
        result = fn()
        if result:
            value.append(result)
            return True
        return False
    _wait(done)
    return value[0]


def test_disconnect_during_account_read_pauses_and_recovers(harness):
    h = harness
    native = h["native"]
    h["read_arm"].set()
    assert h["read_entered"].wait(5)
    native.client._spi.OnFrontDisconnected(0x1001)
    h["read_release"].set()
    _wait(lambda: _pause_observed(h) or not h["owner"].status().thread_alive)
    assert _pause_observed(h)
    _assert_closed_fence_and_guard(h)
    _relogin_same_spi(native, h["helper"].TODAY, 20)
    _wait(lambda: h["model"].ready and h["returns"].status()["reservation_open"])


def test_fast_relogin_during_account_read_rebuilds_same_owner(harness):
    h = harness
    native = h["native"]
    old = native.client.connection_generation
    h["read_arm"].set()
    assert h["read_entered"].wait(5)
    native.client._spi.OnFrontDisconnected(0x1001)
    _relogin_same_spi(native, h["helper"].TODAY, 20)
    assert native.client.connection_generation > old
    h["read_release"].set()
    _wait(lambda: any(kind == "trades" and generation > old for kind, generation in h["queries"])
          or not h["owner"].status().thread_alive)
    assert h["owner"].status().thread_alive
    _wait(lambda: h["model"].ready and h["returns"].status()["reservation_open"])


@pytest.mark.parametrize("tamper", ["account", "native_api"])
def test_changed_source_is_fatal_not_reconnect(harness, tamper):
    h = harness
    session = h["service"]._session
    trader = h["native"].client
    original_account, original_api = session._account_key, trader._api
    try:
        if tamper == "account":
            session._account_key = replace(original_account, account_id="other")
        else:
            with trader._query_state_lock:
                trader._api = object()
        _wait(lambda: not h["owner"].status().thread_alive or h["owner"].status().failure_category is not None)
        assert not h["returns"].status()["reservation_open"]
        assert not h["model"].ready
    finally:
        session._account_key = original_account
        with trader._query_state_lock:
            trader._api = original_api


def test_first_new_ready_sample_fences_before_blocked_refresh(harness, monkeypatch):
    h = harness
    native = h["native"]
    session = h["service"]._session
    held, release_owner = threading.Event(), threading.Event()
    refresh_entered, release_refresh = threading.Event(), threading.Event()
    original_refresh = session.refresh_session
    def owner_barrier():
        held.set()
        assert release_owner.wait(5)
    def refresh_barrier():
        refresh_entered.set()
        assert release_refresh.wait(5)
        return original_refresh()
    handle = h["owner"].submit_service_call(owner_barrier)
    assert held.wait(5)
    try:
        old = native.client.connection_generation
        native.client._spi.OnFrontDisconnected(0x1001)
        _relogin_same_spi(native, h["helper"].TODAY, 20)
        assert native.client.connection_generation > old
        monkeypatch.setattr(session, "refresh_session", refresh_barrier)
        release_owner.set()
        assert refresh_entered.wait(5)
        assert handle.done
        # The first ready sample was already the new generation; the previous
        # snapshot and reservation fence must be invalid before refresh starts.
        assert _pause_observed(h)
        _assert_closed_fence_and_guard(h)
    finally:
        release_owner.set()
        release_refresh.set()
    _wait(lambda: h["model"].ready and h["returns"].status()["reservation_open"])


def test_stable_session_read_past_its_budget_fails_the_read_not_the_owner(harness, monkeypatch, caplog):
    """I63-E146: since the read is request and response, its budget also covers the owner's own
    delays; running past it fails this read and leaves the session and the owner as they are."""
    import logging

    from bt_api_ctp.account_provider import CtpAccountSessionProviderError
    caplog.set_level(logging.INFO, logger="bt_api_py.ctp_gateway_trading")
    h = harness
    session, model = h["service"]._session, h["model"]
    original = session.poll_read_account
    failing, failures = threading.Event(), []
    failing.set()

    def poll(request):
        if not failing.is_set():
            return original(request)
        session.cancel_read_account(request)       # as the provider does when a read ends failed
        failures.append(time.monotonic())
        raise CtpAccountSessionProviderError("QUERY_BUDGET_EXCEEDED")
    monkeypatch.setattr(session, "poll_read_account", poll)
    _wait(lambda: len(failures) >= 3)              # several reads in a row
    status = h["owner"].status()
    assert status.thread_alive and status.failure_category is None
    assert not model.ready                         # nothing stale is offered meanwhile
    assert h["returns"].status()["reservation_open"]   # the return stream and writes are untouched
    failing.clear()
    _wait(lambda: model.ready)
    assert h["owner"].status().failure_category is None
    # One warning for the whole run of failed reads, one line when a read completes again.
    lines = [record.getMessage() for record in caplog.records if record.getMessage().startswith("Account read ")]
    assert len(failures) >= 3 and lines == [
        "Account read failed (QUERY_BUDGET_EXCEEDED); the account read model is not ready until a later "
        "read completes",
        "Account read completed; the account read model is ready again"]


@pytest.mark.parametrize("fault", ["read_program", "finish_program", "finish_journal"])
def test_stable_session_read_and_finish_faults_are_fatal(harness, monkeypatch, fault):
    from bt_api_gateway.account_read_model import GatewayAccountSessionWorkerError
    h = harness
    session, model = h["service"]._session, h["model"]
    entered = threading.Event()
    def fail(*_args, **_kwargs):
        entered.set()
        if fault == "finish_journal":
            raise GatewayAccountSessionWorkerError("STATE_WRITE_FAILED")
        raise ValueError("synthetic programming fault")
    target = session if fault.startswith("read_") else model
    # Since I63-E130 the owner's account read ends (and fails) in poll_read_account.
    method = "poll_read_account" if fault.startswith("read_") else "finish_refresh"
    monkeypatch.setattr(target, method, fail)
    assert entered.wait(5)
    _wait(lambda: not h["owner"].status().thread_alive or h["owner"].status().failure_category is not None)
    assert not h["returns"].status()["reservation_open"] and not h["model"].ready


def test_query_lock_busy_pauses_then_recovers(harness):
    h = harness
    locked, release = threading.Event(), threading.Event()
    owner_held, owner_release = threading.Event(), threading.Event()
    def hold_owner():
        owner_held.set()
        assert owner_release.wait(5)
    h["owner"].submit_service_call(hold_owner)
    assert owner_held.wait(5)
    def hold():
        with h["native"].client._query_state_lock:
            locked.set()
            release.wait(5)
    thread = threading.Thread(target=hold)
    thread.start()
    assert locked.wait(2)
    try:
        # The owner begins its next maintenance only after the test has the
        # source lock; otherwise it could already be inside a synchronous read.
        owner_release.set()
        _wait(lambda: _pause_observed(h))
        _assert_closed_fence_and_guard(h)
    finally:
        owner_release.set()
        release.set()
        thread.join(2)
    assert not thread.is_alive()
    _wait(lambda: h["model"].ready and h["returns"].status()["reservation_open"])


def test_reconnect_during_generation_start_retries_with_new_epoch(harness, monkeypatch):
    h = harness
    model, native = h["model"], h["native"]
    original_assert = model._assert_generation
    switched = threading.Event()
    def switch_during_first_rebind(generation):
        if ":attempt:1" in generation and not switched.is_set():
            switched.set()
            native.client._spi.OnFrontDisconnected(0x1001)
            _relogin_same_spi(native, h["helper"].TODAY, 21)
        return original_assert(generation)
    monkeypatch.setattr(model, "_assert_generation", switch_during_first_rebind)
    native.client._spi.OnFrontDisconnected(0x1001)
    _relogin_same_spi(native, h["helper"].TODAY, 20)
    assert switched.wait(5)
    _wait(lambda: (model.ready and ":attempt:2" in model.read_status()["session_generation"])
          or not h["owner"].status().thread_alive)
    assert h["owner"].status().thread_alive and model.ready
    assert ":attempt:2" in model.read_status()["session_generation"]
    _wait(lambda: h["returns"].status()["reservation_open"])


def test_program_fault_during_disconnect_is_not_masked(harness, monkeypatch):
    h = harness
    session, native = h["service"]._session, h["native"]
    entered, release = threading.Event(), threading.Event()
    def fail_during_reconnect():
        entered.set()
        assert release.wait(5)
        raise ValueError("synthetic programming fault")
    monkeypatch.setattr(session, "refresh_session", fail_during_reconnect)
    try:
        assert entered.wait(5)
        native.client._spi.OnFrontDisconnected(0x1001)
        release.set()
        _wait(lambda: not h["owner"].status().thread_alive or h["owner"].status().failure_category is not None)
        assert not h["returns"].status()["reservation_open"] and not h["model"].ready
    finally:
        release.set()


def test_probe_integrity_failure_is_fatal_even_if_next_probe_would_be_ready(harness, monkeypatch):
    from bt_api_ctp.gateway_session import CtpGatewaySessionError
    h = harness
    session = h["service"]._session
    original = session.connection_status
    first = threading.Event()
    def once_invalid():
        if not first.is_set():
            first.set()
            raise CtpGatewaySessionError("SESSION_CHANGED")
        return original()
    monkeypatch.setattr(session, "connection_status", once_invalid)
    assert first.wait(5)
    _wait(lambda: not h["owner"].status().thread_alive or h["owner"].status().failure_category is not None)
    assert not h["returns"].status()["reservation_open"] and not h["model"].ready


def test_disconnect_between_ready_sample_and_refresh_is_recoverable(harness, monkeypatch):
    h = harness
    session, native = h["service"]._session, h["native"]
    original = session.refresh_session
    entered, release = threading.Event(), threading.Event()
    def blocked_refresh():
        entered.set()
        assert release.wait(5)
        return original()
    monkeypatch.setattr(session, "refresh_session", blocked_refresh)
    try:
        assert entered.wait(5)
        native.client._spi.OnFrontDisconnected(0x1001)
        release.set()
        _wait(lambda: _pause_observed(h) or not h["owner"].status().thread_alive)
        assert _pause_observed(h)
        _assert_closed_fence_and_guard(h)
    finally:
        release.set()
    _relogin_same_spi(native, h["helper"].TODAY, 20)
    _wait(lambda: h["model"].ready and h["returns"].status()["reservation_open"])


def test_new_instrument_source_does_not_wait_for_account_reader_rebind(harness):
    h = harness
    session, native = h["service"]._session, h["native"]
    entered, release = threading.Event(), threading.Event()
    def query_on_owner_before_account_rebind():
        old_account_generation = session._reader._session_scope.connection_generation
        entered.set()
        assert release.wait(5)
        assert session._reader._session_scope.connection_generation == old_account_generation
        assert native.client.connection_generation > old_account_generation
        reader = session.create_instrument_query_reader()
        handle = reader.begin(h["read"].observation_instruments[0], timeout_seconds=2)
        deadline = time.monotonic() + 2
        while time.monotonic() < deadline:
            observation = reader.poll(handle)
            if observation is not None:
                reader.assert_current(observation)
                assert reader.release(handle)
                return observation.connection_generation
            time.sleep(.002)
        raise AssertionError("synthetic instrument query did not complete")
    future = h["owner"].submit_service_call(query_on_owner_before_account_rebind)
    assert entered.wait(5)
    try:
        old = native.client.connection_generation
        native.client._spi.OnFrontDisconnected(0x1001)
        _relogin_same_spi(native, h["helper"].TODAY, 20)
        release.set()
        _wait(lambda: future.done)
        assert future.result(timeout_seconds=0) > old
    finally:
        release.set()


@pytest.mark.parametrize("harness", ["account_only"], indirect=True)
def test_account_only_reconnect_rebuilds_reads_without_write_authority(harness):
    from contextlib import nullcontext

    from bt_api_py.gateway_direct_owner import GatewayDispatchCommand
    from tests.unit.test_iter63_gateway_direct_service import _order

    h = harness
    native, trader = h["native"], h["native"].client
    binding, owner, model = h["resources"].values["binding"], h["owner"], h["model"]
    assert h["settings"].write_enabled is False and h["returns"] is None
    assert binding._execution_stream is None and binding._reservation_fence is None
    assert _spec_call(h, "begin")["status"] == "pending"
    old_spec = _wait_result(lambda: _spec_ready(h))
    old_epoch = model.read_status()["session_generation"]
    old_binding, old_spi = owner.binding(), trader._spi
    old_generation = trader.connection_generation
    old_operations = binding.supported_operations
    trader._spi.OnFrontDisconnected(0x1001)
    _wait(lambda: not model.ready or not owner.status().thread_alive)
    assert not model.ready and owner.status().thread_alive and owner.binding() == old_binding
    contender = LocalAccountSessionGuard(Path(h["settings"].guard_root), h["settings"].account_scope, "other")
    with pytest.raises(LocalAccountSessionGuardError):
        contender.acquire()
    def old_rejected():
        h["service"].maintain_registrations((h["active"],))
        result = _spec_call(h, "status")
        assert result["status"] != "ready"
        return result["status"] == "failed"
    _wait(old_rejected)
    _relogin_same_spi(native, h["helper"].TODAY, 20)
    _wait(lambda: (model.ready and model.read_status()["session_generation"] != old_epoch)
          or not owner.status().thread_alive)
    assert model.ready and owner.status().thread_alive and owner.binding() == old_binding
    assert trader._spi is old_spi and len(h["feeds"]) == len(h["natives"]) == 1
    assert any(kind == "account" and generation > old_generation for kind, generation in h["queries"])
    assert binding.supported_operations == old_operations
    assert _spec_call(h, "status")["status"] == "failed"
    assert _spec_call(h, "begin", "b" * 32)["status"] == "pending"
    new_spec = _wait_result(lambda: _spec_ready(h, "b" * 32))
    assert new_spec["snapshot"]["connection_generation"] > old_spec["snapshot"]["connection_generation"]
    # A pre-existing local reservation is not write authority. Dispatch remains
    # closed by the original write-disabled configuration after read recovery.
    payload = _order(key="before-close", symbol="rb2612", account=h["settings"].account_scope)
    payload.update(exchange_id="SHFE", offset="open", time_in_force="GFD", quantity_unit="contracts")
    journal = binding._journal
    row = journal.reserve(CLIENT_A.actor_ref, "before-close", CLIENT_A.principal_ref, payload)
    done = threading.Event()
    command = GatewayDispatchCommand(row.strategy_scope, row.request_key, row.principal,
        row.reservation_id, row.digest, row.version)
    assert owner.enqueue(command, request_fence=lambda _: nullcontext(), finalize=done.set) == "QUEUED"
    assert done.wait(5)
    assert journal.get(CLIENT_A.actor_ref, "before-close", CLIENT_A.principal_ref).status == "RESERVED"
    assert "insert" not in native.calls and "confirm_settlement" not in native.calls
    assert not any(kind == "trades" for kind, _ in h["queries"])


@pytest.mark.parametrize("harness", ["account_only"], indirect=True)
def test_account_only_rejects_account_replacement(harness):
    h = harness
    session = h["service"]._session
    original = session._account_key
    try:
        session._account_key = replace(original, account_id="other")
        _wait(lambda: not h["owner"].status().thread_alive or h["owner"].status().failure_category is not None)
        assert not h["model"].ready
    finally:
        session._account_key = original


@pytest.mark.parametrize("harness", ["account_only"], indirect=True)
def test_account_only_fast_relogin_during_read_rebuilds(harness):
    h = harness
    native, model = h["native"], h["model"]
    epoch, generation = model.read_status()["session_generation"], native.client.connection_generation
    binding = h["owner"].binding()
    h["read_arm"].set()
    assert h["read_entered"].wait(5)
    try:
        native.client._spi.OnFrontDisconnected(0x1001)
        _relogin_same_spi(native, h["helper"].TODAY, 20)
    finally:
        h["read_release"].set()
    _wait(lambda: (model.ready and model.read_status()["session_generation"] != epoch)
          or not h["owner"].status().thread_alive)
    assert h["owner"].status().thread_alive and h["owner"].binding() == binding
    assert model.ready and native.client.connection_generation > generation
    assert not any(kind == "trades" for kind, _ in h["queries"])
    assert "insert" not in native.calls and "confirm_settlement" not in native.calls


@pytest.mark.parametrize("stage", ["before_commit", "after_commit"])
def test_settlement_stale_fence_pauses_and_requires_new_readback(harness, monkeypatch, stage):
    h = harness
    native, trader = h["native"], h["native"].client
    journal = h["resources"].values["binding"]._journal
    old_generation = trader.connection_generation
    before_marker = journal.settlement_get(trading.ROUTE, h["helper"].TODAY)
    original_confirm = journal.settlement_confirm
    original_readback = native.ReqQrySettlementInfoConfirm
    entered, release, armed = threading.Event(), threading.Event(), threading.Event()
    readback_generations = []
    confirm_count = native.calls.count("confirm_settlement")
    def readback(field, request_id):
        readback_generations.append(trader.connection_generation)
        return original_readback(field, request_id)
    def gated_confirm(observation, *, before_begin=None, before_commit=None):
        if not armed.is_set():
            return original_confirm(observation, before_begin=before_begin, before_commit=before_commit)
        armed.clear()
        if stage == "before_commit":
            def gated_fence():
                entered.set()
                assert release.wait(5)
                return before_commit()
            return original_confirm(observation, before_begin=before_begin, before_commit=gated_fence)
        result = original_confirm(observation, before_begin=before_begin, before_commit=before_commit)
        entered.set()
        assert release.wait(5)
        return result
    monkeypatch.setattr(native, "ReqQrySettlementInfoConfirm", readback)
    monkeypatch.setattr(journal, "settlement_confirm", gated_confirm)
    armed.set()
    try:
        assert entered.wait(5)
        trader._spi.OnFrontDisconnected(0x1001)
        release.set()
        _wait(lambda: _pause_observed(h) or not h["owner"].status().thread_alive)
        assert _pause_observed(h)
        _assert_closed_fence_and_guard(h)
        current_marker = journal.settlement_get(trading.ROUTE, h["helper"].TODAY)
        if before_marker is not None:
            assert current_marker is not None
            assert current_marker.marker_id == before_marker.marker_id
        assert native.calls.count("confirm_settlement") == confirm_count
        _relogin_same_spi(native, h["helper"].TODAY, 20)
        _wait(lambda: (h["model"].ready and h["returns"].status()["reservation_open"]
                       and any(generation > old_generation for generation in readback_generations))
              or not h["owner"].status().thread_alive)
        assert h["owner"].status().thread_alive
        assert h["model"].ready and h["returns"].status()["reservation_open"]
        assert any(generation > old_generation for generation in readback_generations)
        assert native.calls.count("confirm_settlement") == confirm_count
    finally:
        release.set()


def test_settlement_journal_fault_remains_fatal(harness, monkeypatch):
    import sqlite3
    h = harness
    journal = h["resources"].values["binding"]._journal
    entered = threading.Event()
    def fail_journal(*_args, **_kwargs):
        entered.set()
        raise sqlite3.OperationalError("synthetic journal error")
    monkeypatch.setattr(journal, "settlement_confirm", fail_journal)
    assert entered.wait(5)
    _wait(lambda: not h["owner"].status().thread_alive or h["owner"].status().failure_category is not None)
    assert not h["returns"].status()["reservation_open"] and not h["model"].ready


@pytest.mark.parametrize("fault", ["journal", "stale_lookalike"])
def test_reconnect_does_not_mask_unrelated_settlement_errors(harness, monkeypatch, fault):
    import sqlite3
    h = harness
    journal = h["resources"].values["binding"]._journal
    entered, release = threading.Event(), threading.Event()
    def fail_journal(*_args, **_kwargs):
        entered.set()
        assert release.wait(5)
        if fault == "journal":
            raise sqlite3.OperationalError("synthetic journal error")
        raise ValueError("SETTLEMENT_OBSERVATION_STALE")
    monkeypatch.setattr(journal, "settlement_confirm", fail_journal)
    try:
        assert entered.wait(5)
        h["native"].client._spi.OnFrontDisconnected(0x1001)
        release.set()
        _wait(lambda: not h["owner"].status().thread_alive or h["owner"].status().failure_category is not None)
        assert not h["returns"].status()["reservation_open"] and not h["model"].ready
    finally:
        release.set()
