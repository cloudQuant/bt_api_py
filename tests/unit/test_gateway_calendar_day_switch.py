"""Simulated native callbacks with the real owner queue and local receipt store."""

import json
from datetime import date
from threading import Event, Thread
from types import SimpleNamespace

import pytest
from bt_api_ctp.calendar_snapshot import CalendarSnapshot

from bt_api_py.ctp_gateway_calendar_config import CtpCalendarDaySwitchSettings
from bt_api_py.ctp_gateway_calendar_day_switch import CtpGatewayCalendarDaySwitch
from bt_api_py.gateway_calendar_service import (
    CalendarGrant,
    GatewayCalendarService,
    ReceiptCheckedCalendarStore,
)
from bt_api_py.gateway_direct_owner import GatewayDirectOwnerResources
from bt_api_py.trading_calendar_binding import (
    BindingUnavailableError,
    GateDecision,
    _SnapshotLoadError,
)
from bt_api_py.trading_calendar_store import TradingCalendarStore
from tests.calendar_test_store import FixtureCalendarStore
from tests.test_iter63_gateway_direct_owner import Harness, create, wait_for
from tests.unit.test_trading_calendar_binding import content, publish

pytest_plugins = ("test_iter63_gateway_session",)

FIRST, SECOND, THIRD = (date(2026, 11, i) for i in (6, 7, 8))


def calendar():
    data = content()
    for row, day in zip(data["trading_days"], (FIRST, SECOND, THIRD)):  # noqa: B905
        row["day"] = day.isoformat()
    rule = data["trading_hours"][0]
    rule["valid_from"], rule["valid_to"] = FIRST.isoformat(), THIRD.isoformat()
    for row, day in zip(rule["night_mappings"], (FIRST, SECOND, THIRD)):  # noqa: B905
        row["trading_day"] = day.isoformat()
    return data


def reconnect(feed, native, monkeypatch, day):
    trader = feed.trader_client
    spi = trader._spi
    monkeypatch.setattr(native, "ReqUserLogin", lambda *_: 0, raising=False)
    spi.OnFrontDisconnected(1)
    spi.OnFrontConnected()
    spi.OnRspAuthenticate(
        None, SimpleNamespace(ErrorID=0, ErrorMsg=""), trader._authentication_request_id, True
    )
    spi.OnRspUserLogin(
        SimpleNamespace(
            BrokerID="9999",
            UserID="account",
            TradingDay=day.strftime("%Y%m%d"),
            FrontID=7,
            SessionID=20,
            MaxOrderRef="0",
        ),
        SimpleNamespace(ErrorID=0, ErrorMsg=""),
        trader._login_request_id,
        True,
    )


@pytest.fixture
def system(tmp_path, prepared):
    session_factory, _, _, feeds, natives, *_ = prepared
    h = Harness(tmp_path)
    holder = {}

    def factory():
        resources = h.factory()
        session = session_factory()
        session.connect(generation="calendar-test", timeout=1)
        holder["session"] = session

        def close():
            assert session.close()
            resources.close()

        return GatewayDirectOwnerResources(resources.dispatcher, resources.journal, close)

    owner = create(h)
    owner._factory = factory
    assert owner.start(2)
    root = tmp_path / "calendar"
    store = FixtureCalendarStore(root)
    assert publish(store, "v1", calendar(), start=FIRST, end=THIRD).visibility == "COMMITTED"
    grant = CalendarGrant("p", "d", "a", "s", "r", "route-a", "SIM", "future", "x", "x2601")
    service = GatewayCalendarService(grants=frozenset({grant}))
    settings = CtpCalendarDaySwitchSettings("CTP_AUTHENTICATED_SESSION", 0.02, 0.02, 0.3)
    session = holder["session"]
    checked = ReceiptCheckedCalendarStore(TradingCalendarStore(root))
    switch = CtpGatewayCalendarDaySwitch(
        owner=owner,
        session=session,
        session_is_original=lambda: holder.get("session") is session,
        service=service,
        store=checked,
        snapshot_factory=CalendarSnapshot,
        route="route-a",
        settings=settings,
    )
    try:
        switch.start()
        wait_for(lambda: switch.status()["bound"])
        yield SimpleNamespace(
            owner=owner,
            session=session,
            feed=feeds[0],
            native=natives[0],
            store=store,
            checked=checked,
            service=service,
            switch=switch,
            holder=holder,
        )
    finally:
        assert switch.close()
        service.close()
        assert owner.close(2)


def read(system):
    binding = system.service._binding
    return binding.capture_read_view(mode="SIMULATION_ONLY")


def test_publish_same_day_reconnect_advance_and_regression(system, monkeypatch):
    old = read(system)
    assert old.status().calendar_ref == "v1"
    data = calendar()
    data["trading_hours"][0]["day_windows"][0]["start"] = "10:00"
    assert publish(system.store, "v2", data, start=FIRST, end=THIRD).visibility == "COMMITTED"
    system.owner.submit_service_call(
        lambda: reconnect(system.feed, system.native, monkeypatch, FIRST)
    ).result(timeout_seconds=2)
    wait_for(lambda: system.switch.status()["reason"] == "SAME_DAY")
    assert read(system).status().calendar_ref == "v1"
    system.owner.submit_service_call(
        lambda: reconnect(system.feed, system.native, monkeypatch, SECOND)
    ).result(timeout_seconds=2)
    wait_for(lambda: read(system).status().calendar_ref == "v2")
    assert read(system).status().bound_trading_day == SECOND
    assert old.status().calendar_ref == "v1"
    system.owner.submit_service_call(
        lambda: reconnect(system.feed, system.native, monkeypatch, FIRST)
    ).result(timeout_seconds=2)
    wait_for(lambda: system.switch.status()["reason"] == "DAY_REGRESSION")
    assert read(system).status().calendar_ref == "v2"


def test_prepared_identity_staleness_gate_and_failure(system):
    binding = system.service._binding
    first = binding.prepare_update()
    second = binding.prepare_update()
    assert binding.activate_prepared(object(), first).outcome == "STALE"
    assert (
        binding.activate_prepared(
            GateDecision(True, "route-a", binding.status().source_generation, SECOND, 1, "fake"),
            second,
        ).outcome
        == "GATE_REJECTED"
    )
    assert binding.status().calendar_ref == "v1"
    (system.store.root / "CURRENT.json").write_bytes(b"{}")
    with pytest.raises(_SnapshotLoadError):
        binding.prepare_update()
    assert binding.activate_prepared(object(), second).outcome == "STALE"
    assert read(system).status().calendar_ref == "v1"


def test_close_queued_is_confirmed_and_running_timeout_is_retained(system):
    entered, release = Event(), Event()
    handle = system.owner.submit_service_call(lambda: (entered.set(), release.wait(3)))
    assert entered.wait(2)
    wait_for(lambda: system.switch.status()["pending_owner_call"])
    assert system.switch.close()
    release.set()
    handle.result(timeout_seconds=2)


def test_close_does_not_forget_running_handle(system, monkeypatch):
    entered, release = Event(), Event()
    original = system.session.capture_calendar_session

    def blocked():
        entered.set()
        assert release.wait(3)
        return original()

    monkeypatch.setattr(system.session, "capture_calendar_session", blocked)
    assert entered.wait(2)
    try:
        assert not system.switch.close()
        assert system.switch.status()["pending_owner_call"]
    finally:
        release.set()
    assert system.switch.close()


def test_unexpected_wait_error_retains_original_handle(system, monkeypatch):
    released = Event()
    original_submit = system.owner.submit_service_call
    calls = []

    class Handle:
        def __init__(self, inner):
            self.inner = inner

        def cancel(self):
            return False

        def result(self, *, timeout_seconds):
            if not released.is_set():
                raise RuntimeError("synthetic wait failure")
            return self.inner.result(timeout_seconds=timeout_seconds)

    def submit(callback):
        calls.append(True)
        return Handle(original_submit(callback))

    monkeypatch.setattr(system.owner, "submit_service_call", submit)
    wait_for(lambda: system.switch.status()["reason"] == "OWNER_UNCONFIRMED")
    assert len(calls) == 1 and system.switch.status()["pending_owner_call"]
    try:
        assert not system.switch.close()
        assert len(calls) == 1
    finally:
        released.set()
    assert system.switch.close()


def test_shutdown_signal_blocks_install_after_preparation(system, monkeypatch):
    entered, release, closing = Event(), Event(), Event()

    class BlockingStore:
        def read_current(self):
            entered.set()
            assert release.wait(3)
            return system.checked.read_current()

    system.switch._is_closing = closing.is_set
    system.service._binding._store = BlockingStore()
    data = calendar()
    data["trading_hours"][0]["day_windows"][0]["start"] = "10:00"
    assert publish(system.store, "v2", data, start=FIRST, end=THIRD).visibility == "COMMITTED"
    system.owner.submit_service_call(
        lambda: reconnect(system.feed, system.native, monkeypatch, SECOND)
    ).result(timeout_seconds=2)
    assert entered.wait(2)
    closing.set()
    release.set()
    wait_for(lambda: system.switch.status()["reason"] == "SOURCE_CHANGED")
    assert system.switch.close()
    assert read(system).status().calendar_ref == "v1"


def test_source_changes_during_load_rejects_candidate(system, monkeypatch):
    entered, release, second = Event(), Event(), Event()

    class BlockingStore:
        count = 0

        def read_current(self):
            self.count += 1
            if self.count == 1:
                entered.set()
                assert release.wait(3)
            else:
                second.set()
                assert release_second.wait(3)
            return system.checked.read_current()

    release_second = Event()
    system.service._binding._store = BlockingStore()
    data = calendar()
    data["trading_hours"][0]["day_windows"][0]["start"] = "10:00"
    assert publish(system.store, "v2", data, start=FIRST, end=THIRD).visibility == "COMMITTED"
    system.owner.submit_service_call(
        lambda: reconnect(system.feed, system.native, monkeypatch, SECOND)
    ).result(timeout_seconds=2)
    assert entered.wait(2)
    try:
        system.owner.submit_service_call(
            lambda: reconnect(system.feed, system.native, monkeypatch, THIRD)
        ).result(timeout_seconds=2)
        release.set()
        assert second.wait(2)
        assert read(system).status().calendar_ref == "v1"
    finally:
        release.set()
        release_second.set()
    wait_for(lambda: read(system).status().bound_trading_day == THIRD)


def test_highest_seen_day_fences_regression_after_load_failure(system, monkeypatch):
    (system.store.root / "CURRENT.json").write_bytes(b"{}")
    system.owner.submit_service_call(
        lambda: reconnect(system.feed, system.native, monkeypatch, SECOND)
    ).result(timeout_seconds=2)
    wait_for(lambda: system.switch.status()["reason"] == "SNAPSHOT_UNAVAILABLE")
    system.owner.submit_service_call(
        lambda: reconnect(system.feed, system.native, monkeypatch, FIRST)
    ).result(timeout_seconds=2)
    wait_for(lambda: system.switch.status()["reason"] == "DAY_REGRESSION")
    assert read(system).status().calendar_ref == "v1"
    assert (
        read(system)
        .query(
            venue="SIM", kind="future", product="x", contract="x2601", trading_day=date(2026, 11, 9)
        )
        .result.status
        == "UNKNOWN"
    )


def test_owner_session_object_replacement_reports_unavailable(system):
    system.holder["session"] = object()
    wait_for(lambda: system.switch.status()["reason"] == "SOURCE_UNAVAILABLE")
    assert system.switch.status()["state"] == "DEGRADED"
    assert read(system).status().calendar_ref == "v1"


def test_thread_start_failure_has_confirmed_cleanup(system, monkeypatch):
    from bt_api_py.gateway_direct_owner import GatewayDirectOwner

    unused = GatewayDirectOwner(
        lambda: None, queue_size=1, max_queue_wait_seconds=1, busy_timeout_seconds=1
    )
    extra = CtpGatewayCalendarDaySwitch(
        owner=unused,
        session=system.session,
        session_is_original=lambda: True,
        service=system.service,
        store=system.checked,
        snapshot_factory=CalendarSnapshot,
        route="route-a",
        settings=CtpCalendarDaySwitchSettings("CTP_AUTHENTICATED_SESSION", 1, 1, 1),
    )

    def fail():
        raise RuntimeError("synthetic")

    monkeypatch.setattr(extra._thread, "start", fail)
    with pytest.raises(RuntimeError, match="synthetic"):
        extra.start()
    assert extra.close()
    assert extra.status()["reason"] == "START_FAILED"


def test_concurrent_prepare_latest_call_wins(system):
    entered, release = Event(), Event()

    class DelayedStore:
        count = 0

        def read_current(self):
            self.count += 1
            if self.count == 1:
                entered.set()
                assert release.wait(3)
            return system.checked.read_current()

    binding = system.service._binding
    binding._store = DelayedStore()
    outcome = []

    def first():
        try:
            binding.prepare_update()
        except BindingUnavailableError:
            outcome.append("STALE")

    thread = Thread(target=first)
    thread.start()
    assert entered.wait(2)
    try:
        token = binding.prepare_update()
    finally:
        release.set()
        thread.join(3)
    assert outcome == ["STALE"]
    assert binding.activate_prepared(object(), token).outcome == "GATE_REJECTED"


def test_prepared_tokens_are_instance_bound_and_one_shot(system):
    from tests.unit.test_trading_calendar_binding import binding as new_binding

    original = system.service._binding
    other = new_binding(system.checked)
    token = original.prepare_update()
    assert other.activate_prepared(object(), token).outcome == "STALE"
    event = system.switch._gate.issue(original.status(), SECOND)
    assert original.activate_prepared(event, token).outcome == "ACTIVATED"
    assert original.activate_prepared(event, token).outcome == "STALE"


def test_restart_creates_new_generation_and_loads_latest(system, prepared, tmp_path):
    from bt_api_py.gateway_calendar_service import CalendarGrant

    data = calendar()
    data["trading_hours"][0]["day_windows"][0]["start"] = "10:00"
    assert publish(system.store, "v2", data, start=FIRST, end=THIRD).visibility == "COMMITTED"
    generation = read(system).status().source_generation
    assert system.switch.close()
    system.service.close()
    assert system.owner.close(2)
    root = tmp_path / "second-owner"
    root.mkdir()
    harness = Harness(root)
    session_factory = prepared[0]
    session_holder = {}

    def factory():
        resources = harness.factory()
        session = session_factory()
        session.connect(generation="calendar-test-next", timeout=1)
        session_holder["session"] = session

        def close():
            assert session.close()
            resources.close()

        return GatewayDirectOwnerResources(resources.dispatcher, resources.journal, close)

    owner = create(harness)
    owner._factory = factory
    assert owner.start(2)
    session = session_holder["session"]
    grant = CalendarGrant("p", "d", "a", "s", "r", "route-a", "SIM", "future", "x", "x2601")
    service = GatewayCalendarService(grants=frozenset({grant}))
    switch = CtpGatewayCalendarDaySwitch(
        owner=owner,
        session=session,
        session_is_original=lambda: True,
        service=service,
        store=system.checked,
        snapshot_factory=CalendarSnapshot,
        route="route-a",
        settings=CtpCalendarDaySwitchSettings("CTP_AUTHENTICATED_SESSION", 0.02, 0.02, 1),
    )
    try:
        switch.start()
        wait_for(lambda: switch.status()["bound"])
        status = service._binding.status()
        assert status.calendar_ref == "v2" and status.bound_trading_day == FIRST
        assert status.source_generation != generation
    finally:
        assert switch.close()
        service.close()
        assert owner.close(2)


def test_factory_opt_in_and_invalid_options(tmp_path, monkeypatch):
    from bt_api_py.ctp_gateway_calendar_config import create_calendar_service
    from bt_api_py.ctp_gateway_local_profile import CtpLocalProfileError, load_local_ctp_resources
    from tests.integration.test_ctp_gateway_operator_calendar import configured

    doc, file, config, _, _ = configured(tmp_path, monkeypatch, bootstrap=False)
    option = {
        "source": "CTP_AUTHENTICATED_SESSION",
        "poll_interval_seconds": 1,
        "owner_wait_seconds": 1,
        "close_timeout_seconds": 1,
    }
    doc["calendars"]["day_switch"] = option
    file.write_text(json.dumps(doc), encoding="utf-8")
    settings = load_local_ctp_resources(config).calendar_settings
    assert settings.day_switch.source == "CTP_AUTHENTICATED_SESSION"
    service = create_calendar_service(settings)
    assert service._binding is None
    service.close()
    for change in (
        {"source": "OTHER"},
        {"poll_interval_seconds": True},
        {"owner_wait_seconds": 0},
        {"close_timeout_seconds": 31},
    ):
        doc["calendars"]["day_switch"] = {**option, **change}
        file.write_text(json.dumps(doc), encoding="utf-8")
        with pytest.raises(CtpLocalProfileError):
            load_local_ctp_resources(config)
    doc["calendars"]["day_switch"] = option
    doc["calendars"]["simulation_bootstrap"] = {"trading_day": "2026-11-06"}
    file.write_text(json.dumps(doc), encoding="utf-8")
    with pytest.raises(CtpLocalProfileError):
        load_local_ctp_resources(config)


def test_opt_in_factory_attaches_and_closes_coordinator(tmp_path, monkeypatch):
    import bt_api_py.ctp_gateway_calendar_day_switch as coordinator
    from tests.integration.test_ctp_gateway_operator_calendar import application, configured

    doc, file, config, _, _ = configured(tmp_path, monkeypatch, bootstrap=False)
    doc["calendars"]["day_switch"] = {
        "source": "CTP_AUTHENTICATED_SESSION",
        "poll_interval_seconds": 1,
        "owner_wait_seconds": 1,
        "close_timeout_seconds": 1,
    }
    file.write_text(json.dumps(doc), encoding="utf-8")
    calls = []

    class Spy:
        def __init__(self, **kwargs):
            calls.append(("create", kwargs))

        def start(self):
            calls.append(("start", None))

        def close(self):
            calls.append(("close", None))
            return True

        def status(self):
            return {"state": "STARTING"}

    monkeypatch.setattr(coordinator, "CtpGatewayCalendarDaySwitch", Spy)
    app = application(config, tmp_path / "launch.yaml")
    try:
        app.start(timeout_seconds=10)
        assert [entry[0] for entry in calls] == ["create", "start"]
        values = calls[0][1]
        assert values["session_is_original"]() is True
        assert values["service"]._binding is None
        assert values["settings"].source == "CTP_AUTHENTICATED_SESSION"
    finally:
        app.stop(timeout_seconds=10)
    assert [entry[0] for entry in calls] == ["create", "start", "close"]
