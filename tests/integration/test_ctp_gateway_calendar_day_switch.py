"""CURVE operator day switch with a real CTP session and synthetic native callbacks.

The legacy trading/provider shell is a fixture. Nothing contacts an exchange.
"""

import json
import threading

import pytest
import zmq

from tests.calendar_test_store import FixtureCalendarStore
from tests.integration.test_ctp_gateway_operator_calendar import application, client_for, configured
from tests.test_iter63_ctp_gateway_trading import FakeSession
from tests.test_iter63_gateway_direct_owner import wait_for
from tests.unit.test_gateway_calendar_day_switch import FIRST, SECOND, THIRD, calendar, reconnect
from tests.unit.test_trading_calendar_binding import publish

pytest_plugins = ("test_iter63_gateway_session",)
pytestmark = pytest.mark.skipif(not zmq.has("curve"), reason="CURVE unavailable")


def test_operator_curve_keeps_same_registration_during_native_day_switch(
    tmp_path, monkeypatch, prepared
):
    app_root = tmp_path / "operator"
    app_root.mkdir()
    doc, file, config, public, secret = configured(app_root, monkeypatch, bootstrap=False)
    doc["calendars"]["day_switch"] = {
        "source": "CTP_AUTHENTICATED_SESSION",
        "poll_interval_seconds": 0.02,
        "owner_wait_seconds": 0.1,
        "close_timeout_seconds": 2,
    }
    file.write_text(json.dumps(doc), encoding="utf-8")
    store = FixtureCalendarStore(file.parent / "calendars")
    assert publish(store, "v1", calendar(), start=FIRST, end=THIRD).visibility == "COMMITTED"

    create_real, _, _, feeds, natives, _, _, _ = prepared
    original_connect, original_close = FakeSession.connect, FakeSession.close
    calls = []

    def connect(shell, *, generation, timeout):
        original_connect(shell, generation=generation, timeout=timeout)
        shell.calendar_delegate = create_real()
        shell.calendar_delegate.connect(generation=generation, timeout=timeout)
        calls.append(("connected", threading.get_ident()))

    def close(shell):
        assert shell.calendar_delegate.close() is True
        calls.append(("closed", threading.get_ident()))
        return original_close(shell)

    def capture(shell):
        calls.append(("capture", threading.get_ident()))
        return shell.calendar_delegate.capture_calendar_session()

    def is_current(shell, token):
        return shell.calendar_delegate.calendar_session_is_current(token)

    monkeypatch.setattr(FakeSession, "connect", connect)
    monkeypatch.setattr(FakeSession, "close", close)
    monkeypatch.setattr(FakeSession, "capture_calendar_session", capture, raising=False)
    monkeypatch.setattr(FakeSession, "calendar_session_is_current", is_current, raising=False)

    app = application(config, app_root / "launch.yaml")
    runtime = client_for(doc, config, public, secret)

    def query(day):
        return runtime.call(
            "store.calendar",
            "calendar.query.v1",
            {
                "mode": "SIMULATION_ONLY",
                "route": "CTP___FUTURE",
                "venue": "SIM",
                "kind": "future",
                "product": "x",
                "contract": "x2601",
                "trading_day": day.isoformat(),
            },
            timeout_seconds=5,
        )

    try:
        app.start(timeout_seconds=10)
        runtime.start(timeout_seconds=10)
        wait_for(lambda: query(FIRST)["status"] == "COMPLETE", timeout=8)
        first = query(FIRST)
        assert first["binding"]["calendar_ref"] == "v1"
        registration = first["registration_id"]
        source_generation = first["binding"]["source_generation"]
        assert (first["source_authentication"], first["trade_authorization"]) == (
            "NOT_PROVEN",
            "NONE",
        )

        changed = calendar()
        changed["trading_hours"][0]["day_windows"][0]["start"] = "10:00"
        assert publish(store, "v2", changed, start=FIRST, end=THIRD).visibility == "COMMITTED"
        reconnect(feeds[0], natives[0], monkeypatch, FIRST)
        old_count = sum(event[0] == "capture" for event in calls)
        wait_for(lambda: sum(event[0] == "capture" for event in calls) >= old_count + 2, timeout=8)
        same_day = query(FIRST)
        assert same_day["binding"]["calendar_ref"] == "v1"
        assert same_day["registration_id"] == registration

        reconnect(feeds[0], natives[0], monkeypatch, SECOND)
        wait_for(
            lambda: (query(SECOND).get("binding") or {}).get("calendar_ref") == "v2", timeout=8
        )
        advanced = query(SECOND)
        assert advanced["status"] == "COMPLETE"
        assert advanced["binding"]["bound_trading_day"] == SECOND.isoformat()
        assert advanced["binding"]["source_generation"] == source_generation
        assert advanced["registration_id"] == registration
        assert advanced["windows"][0]["start_utc"] == "2026-11-07T02:00:00Z"
        assert (advanced["source_authentication"], advanced["trade_authorization"]) == (
            "NOT_PROVEN",
            "NONE",
        )
        assert natives[0].calls == []
    finally:
        runtime.close(timeout_seconds=10)
        app.stop(timeout_seconds=10)
    assert app.status["state"] == "closed"
    assert [name for name, _ in calls].count("closed") == 1
    assert len({thread for _, thread in calls}) == 1
    assert not any(
        t.name == "ctp-calendar-day-switch" and t.is_alive() for t in threading.enumerate()
    )
