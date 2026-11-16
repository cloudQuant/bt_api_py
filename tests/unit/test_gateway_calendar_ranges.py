"""Bounded simulated UTC windows through the registration-scoped service."""
from copy import deepcopy
from dataclasses import replace
from threading import Event, Thread

import pytest
from bt_api_py.gateway_calendar_codec import validate_calendar_windows_response
from bt_api_py.gateway_calendar_service import GatewayCalendarService
from bt_api_py.service_registry import ServiceResultTooLargeError
from bt_api_py.trading_calendar_range_simulation import CalendarRangePolicy
from tests.unit.test_gateway_calendar_service import CONTEXT, GRANT, setup_service
from tests.unit.test_trading_calendar_binding import decision

OP = "calendar.windows.v1"
REQUEST = {"mode": "SIMULATION_ONLY", "route": "route-a", "venue": "SIM", "kind": "future",
           "product": "x", "contract": "x2601", "start_utc": "2026-01-01T01:30:00Z",
           "end_utc": "2026-01-01T02:00:00Z"}
POLICY = CalendarRangePolicy("test-policy", "route-a", "SIM", "future", "x", "x2601", 0, 0)


def invoke(service, request=REQUEST, context=CONTEXT):
    return service.handle(OP, context=context, arguments=dict(request))


def test_policy_missing_and_request_acl(setup_service):
    service, _, _ = setup_service
    assert invoke(service)["reason"] == "POLICY_UNAVAILABLE"
    for bad in (dict(REQUEST, policy="injected"), dict(REQUEST, event="injected"),
                dict(REQUEST, start_utc="2026-01-01T01:00:00+00:00"),
                dict(REQUEST, end_utc="2028-01-01T00:00:00Z")):
        with pytest.raises(ValueError): invoke(service, bad)
    with pytest.raises(ValueError, match="SCOPE_DENIED"):
        invoke(service, context=replace(CONTEXT, scope=replace(CONTEXT.scope, run_id="other")))
    with pytest.raises(ValueError): CalendarRangePolicy("bad", "route-a", "SIM", "future", "x", "x2601", True, 0)
    with pytest.raises(ValueError): CalendarRangePolicy("bad", "route-a", "SIM", "future", "x", "x2601", 32, 0)
    with pytest.raises(ValueError): GatewayCalendarService(grants=frozenset({GRANT}), range_policies=frozenset({POLICY, replace(POLICY, policy_id="other")}))


def test_complete_range_is_unclipped_and_budgeted(setup_service):
    service, _, _ = setup_service
    service._range_policies[POLICY.scope_key] = POLICY
    result = invoke(service)
    assert result["status"] == "COMPLETE"
    assert result["policy"]["declaration"] == "DEPLOYMENT_DECLARED_UNVERIFIED"
    assert result["windows"] == [{"window_id": "day", "phase": "continuous", "trading_day": "2026-01-01",
                                  "start_utc": "2026-01-01T01:00:00Z", "end_utc": "2026-01-01T03:00:00Z"}]
    assert result["source_authentication"] == "NOT_PROVEN" and result["trade_authorization"] == "NONE"
    detached = validate_calendar_windows_response(result)
    detached["windows"][0]["window_id"] = "mutated"
    assert result["windows"][0]["window_id"] == "day"
    bad = deepcopy(result); bad["windows"][0]["trading_day"] = "2026-01-02"
    with pytest.raises(ValueError): validate_calendar_windows_response(bad)
    with pytest.raises(ServiceResultTooLargeError):
        invoke(service, context=replace(CONTEXT, result_fits=lambda _: False))


def test_revision_change_discards_range(tmp_path, setup_service, monkeypatch):
    service, _, bound = setup_service
    service._range_policies[POLICY.scope_key] = POLICY
    from bt_api_py.trading_calendar_binding import SimulationCalendarReadView
    original = SimulationCalendarReadView.query
    entered, release = Event(), Event()
    def wait_query(view, **kwargs):
        entered.set()
        assert release.wait(5)
        return original(view, **kwargs)
    monkeypatch.setattr(SimulationCalendarReadView, "query", wait_query)
    outcomes = []
    thread = Thread(target=lambda: outcomes.append(invoke(service)))
    thread.start()
    assert entered.wait(5)
    (tmp_path / "CURRENT.json").write_bytes(b"{}")
    assert bound.activate(decision()).outcome == "LOAD_FAILED"
    release.set(); thread.join(5)
    assert not thread.is_alive()
    assert outcomes[0]["reason"] == "OWNER_CHANGED" and outcomes[0]["binding"] is None and not outcomes[0]["windows"]


def test_multiple_days_empty_and_cross_day_reused_window_ids(setup_service):
    service, _, _ = setup_service
    service._range_policies[POLICY.scope_key] = POLICY
    request = dict(REQUEST, start_utc="2026-01-01T01:00:00Z", end_utc="2026-01-03T03:00:00Z")
    result = invoke(service, request)
    assert result["status"] == "COMPLETE"
    assert [window["trading_day"] for window in result["windows"]] == ["2026-01-01", "2026-01-02", "2026-01-03"]
    assert {window["window_id"] for window in result["windows"]} == {"day"}
    empty = invoke(service, dict(REQUEST, start_utc="2026-01-01T04:00:00Z", end_utc="2026-01-01T05:00:00Z"))
    assert empty["status"] == "COMPLETE" and empty["windows"] == []


def test_too_many_windows_refused_without_truncation(setup_service, monkeypatch):
    from datetime import datetime, timedelta, timezone
    from bt_api_ctp.calendar_snapshot import CalendarWindow
    from bt_api_py.trading_calendar_binding import SimulationCalendarReadView
    service, _, _ = setup_service
    service._range_policies[POLICY.scope_key] = POLICY
    original = SimulationCalendarReadView.query
    def many(self, **kwargs):
        result = original(self, **kwargs)
        base = datetime(2026, 1, 1, 1, tzinfo=timezone.utc)
        windows = tuple(CalendarWindow(f"w{i}", "continuous", base.date(), base + timedelta(seconds=30*i),
            base + timedelta(seconds=30*(i+1)), base + timedelta(seconds=30*i),
            base + timedelta(seconds=30*(i+1))) for i in range(129))
        return replace(result, result=replace(result.result, windows=windows))
    monkeypatch.setattr(SimulationCalendarReadView, "query", many)
    with pytest.raises(ServiceResultTooLargeError): invoke(service)


def test_wire_byte_limit_is_refused(setup_service, monkeypatch):
    from datetime import datetime, timedelta, timezone
    from bt_api_ctp.calendar_snapshot import CalendarWindow
    from bt_api_py.trading_calendar_binding import SimulationCalendarReadView
    service, _, _ = setup_service
    service._range_policies[POLICY.scope_key] = POLICY
    original = SimulationCalendarReadView.query
    def large(self, **kwargs):
        result = original(self, **kwargs)
        base = datetime(2026, 1, 1, 1, 30, tzinfo=timezone.utc)
        windows = tuple(CalendarWindow(f"{i:03d}" + "x"*125, "continuous", base.date(),
            base + timedelta(seconds=10*i), base + timedelta(seconds=10*(i+1)),
            base + timedelta(seconds=10*i), base + timedelta(seconds=10*(i+1))) for i in range(128))
        return replace(result, result=replace(result.result, windows=windows))
    monkeypatch.setattr(SimulationCalendarReadView, "query", large)
    with pytest.raises(ServiceResultTooLargeError): invoke(service)


def test_owner_gap_is_reported_and_cannot_be_forged(setup_service, monkeypatch):
    from bt_api_py.trading_calendar_binding import SimulationCalendarReadView
    service, _, _ = setup_service
    service._range_policies[POLICY.scope_key] = POLICY
    original = SimulationCalendarReadView.query_venue_day
    def unknown(self, **kwargs):
        return replace(original(self, **kwargs), status="UNKNOWN", day_status=None, source_ids=())
    monkeypatch.setattr(SimulationCalendarReadView, "query_venue_day", unknown)
    result = invoke(service)
    assert result["status"] == "UNKNOWN" and result["windows"] == []
    assert result["gaps"] == [{"trading_day": "2026-01-01", "reason": "OWNER_DATA_UNAVAILABLE"}]
    bad = deepcopy(result); bad["gaps"].append(dict(bad["gaps"][0]))
    with pytest.raises(ValueError): validate_calendar_windows_response(bad)
