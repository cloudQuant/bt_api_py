"""Simulated fixed-view Gateway reads; none of these establish venue facts."""
from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
from copy import deepcopy
from threading import Event, Thread

import pytest
from bt_api_base.service_protocol import WireIdentity, WireRole, WireScope
from bt_api_py.gateway_calendar_service import CalendarGrant, GatewayCalendarService, ReceiptCheckedCalendarStore
from bt_api_py.gateway_calendar_codec import validate_calendar_response
from bt_api_py.trading_calendar_binding import SimulationCalendarBinding
from bt_api_ctp.calendar_snapshot import CalendarSnapshot
from bt_api_py.service_registry import ServiceCallContext, ServiceResultTooLargeError
from bt_api_py.trading_calendar_store import TradingCalendarStore
from tests.calendar_test_store import FixtureCalendarStore
from tests.unit.test_trading_calendar_binding import binding, content, decision, publish, DAY1, DAY2, FakeGate
from tests.unit.test_service_registry import _make_registry

REQUEST = dict(mode="SIMULATION_ONLY", route="route-a", venue="SIM", kind="future", product="x", contract="x2601", trading_day="2026-01-01")
GRANT = CalendarGrant("principal", "domain", "account", "strategy", "run", "route-a", "SIM", "future", "x", "x2601")
CONTEXT = ServiceCallContext(WireIdentity(WireRole.STRATEGY_CLIENT, "principal", "strategy"), WireScope("domain", account_ref="account", strategy_ref="strategy", run_id="run"), "registration", "principal", lambda _: True)


@pytest.fixture
def setup_service(tmp_path):
    store = FixtureCalendarStore(tmp_path)
    assert publish(store, "v1", content()).visibility == "COMMITTED"
    bound = binding(ReceiptCheckedCalendarStore(TradingCalendarStore(tmp_path)))
    registry = _make_registry(tmp_path / "registry.sqlite")
    service = GatewayCalendarService(grants=frozenset({GRANT}))
    service.bind_registry(registry)
    service.rebind(bound)
    yield service, store, bound
    registry.close()


def call(service, request=REQUEST, context=CONTEXT):
    return service.handle("calendar.query.v1", context=context, arguments=dict(request))


def test_fixed_view_and_reload_failure(tmp_path, setup_service):
    service, store, bound = setup_service
    first = call(service)
    assert first["status"] == "COMPLETE" and first["binding"]["calendar_ref"] == "v1"
    assert first["windows"][0]["start_utc"] == "2026-01-01T01:00:00Z"
    assert first["venue_day"] == {"status": "COMPLETE", "day_status": "open"}
    assert first["source_authentication"] == "NOT_PROVEN" and first["trade_authorization"] == "NONE"
    assert publish(store, "v2", content("10:00")).visibility == "COMMITTED"
    assert call(service)["binding"]["calendar_ref"] == "v1"
    assert bound.activate(decision(route="wrong")).outcome == "GATE_REJECTED"
    assert bound.activate(decision(generation="old")).outcome == "GATE_REJECTED"
    assert bound.activate(decision()).outcome == "ACTIVATED"
    response = call(service, dict(REQUEST, trading_day=DAY2.isoformat()))
    assert response["binding"]["calendar_ref"] == "v2" and response["windows"][0]["start_utc"] == "2026-01-02T02:00:00Z"
    assert bound.activate(decision()).outcome == "DUPLICATE"
    (tmp_path / "CURRENT.json").write_bytes(b"{}")
    assert bound.activate(decision(day=DAY2.replace(day=3), sequence=3, event_id="event3")).outcome == "LOAD_FAILED"
    stale = call(service, dict(REQUEST, trading_day=DAY2.isoformat()))
    assert stale["binding"]["calendar_ref"] == "v2" and stale["binding"]["load_status"] == "STALE"


def test_authorization_strict_request_budget_and_generation(tmp_path, setup_service):
    service, _, bound = setup_service
    for bad in (dict(REQUEST, event="inject"), dict(REQUEST, route="../secret"), dict(REQUEST, trading_day="20260101")):
        with pytest.raises(ValueError): call(service, bad)
    with pytest.raises(ValueError, match="SCOPE_DENIED"):
        call(service, context=replace(CONTEXT, scope=replace(CONTEXT.scope, run_id="other")))
    for field, value in (("route", "other"), ("venue", "OTHER"), ("kind", "option"),
                         ("product", "other"), ("contract", "other")):
        with pytest.raises(ValueError, match="SCOPE_DENIED"):
            call(service, dict(REQUEST, **{field: value}))
    with pytest.raises(ServiceResultTooLargeError):
        call(service, context=replace(CONTEXT, result_fits=lambda _: False))
    with pytest.raises(ValueError, match="GENERATION_REUSED"):
        service.rebind(bound)
    outside = call(service, dict(REQUEST, trading_day="2027-01-03"))
    assert outside["status"] == "OUT_OF_SCOPE" and outside["reason"] == "DAY_OUT_OF_RANGE"
    service.rebind(None)
    assert call(service)["binding"] is None
    service.close()
    with pytest.raises(ValueError, match="SERVICE_CLOSED"):
        service.rebind(bound)


def test_unverified_receipts_never_bind(tmp_path):
    store = FixtureCalendarStore(tmp_path)
    assert publish(store, "v1", content()).visibility == "COMMITTED"
    receipt = tmp_path / "receipts" / "pub-v1.json"
    receipt.write_bytes(b"{}")
    raw = TradingCalendarStore(tmp_path)
    assert raw.read_current().version_id == "v1"
    bound = binding(ReceiptCheckedCalendarStore(raw))
    assert bound.status().calendar_ref is None


def test_generation_history_is_bounded_and_codec_is_detached(tmp_path, setup_service):
    service, _, first = setup_service
    source = ReceiptCheckedCalendarStore(TradingCalendarStore(tmp_path))
    def fresh(generation):
        return SimulationCalendarBinding(mode="SIMULATION_ONLY", store=source,
            snapshot_factory=CalendarSnapshot, gate=FakeGate(), route="route-a",
            source_generation=generation, trading_day=DAY1, last_sequence=1)
    service.rebind(fresh("gen-2"))
    with pytest.raises(ValueError, match="GENERATION_REUSED"):
        service.rebind(first)
    service._generations.update(f"filled-{i}" for i in range(254))
    with pytest.raises(ValueError, match="GENERATION_CAPACITY"):
        service.rebind(fresh("gen-3"))
    response = call(service)
    detached = validate_calendar_response(response)
    detached["scope"]["route"] = "modified"
    assert response["scope"]["route"] == "route-a"
    invalid = []
    bad = deepcopy(response); bad["scope"]["trading_day"] = "2026-01-04"; invalid.append(bad)
    bad = deepcopy(response); bad["reason"] = "QUERY_UNAVAILABLE"; invalid.append(bad)
    bad = deepcopy(response); bad["venue_day"]["day_status"] = "closed"; invalid.append(bad)
    bad = deepcopy(response); bad["windows"][0]["start_utc"] = "2026-01-01T01:00:00+08:00"; invalid.append(bad)
    for bad in invalid:
        with pytest.raises(ValueError): validate_calendar_response(bad)


def test_concurrent_captured_view_and_revision_fence(tmp_path, monkeypatch, setup_service):
    service, store, bound = setup_service
    with ThreadPoolExecutor(max_workers=2) as pool:
        a, b = list(pool.map(lambda _: call(service), range(2)))
    assert a == b
    # Force a binding revision between capture and response, even when reload fails.
    from bt_api_py.trading_calendar_binding import SimulationCalendarReadView
    original = SimulationCalendarReadView.query
    entered, release = Event(), Event()
    def wait_query(view, **kw):
        entered.set()
        assert release.wait(5)
        return original(view, **kw)
    monkeypatch.setattr(SimulationCalendarReadView, "query", wait_query)
    results = []
    thread = Thread(target=lambda: results.append(call(service)))
    thread.start()
    assert entered.wait(5)
    (tmp_path / "CURRENT.json").write_bytes(b"{}")
    assert bound.activate(decision()).outcome == "LOAD_FAILED"
    release.set()
    thread.join(5)
    assert not thread.is_alive()
    assert results[0]["reason"] == "OWNER_CHANGED" and results[0]["binding"] is None
