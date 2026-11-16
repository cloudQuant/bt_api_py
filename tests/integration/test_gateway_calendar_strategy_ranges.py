"""Simulation range through actual ServiceHost, CURVE and strategy facade."""
import copy
import json
import queue
import threading
from datetime import date, timedelta

import pytest
import zmq
from bt_api_base.gateway_direct_protocol import GatewayCommandBinding
from bt_api_base.service_protocol import WireScope
from bt_api_ctp.calendar_snapshot import CalendarSnapshot
from bt_api_py.gateway_api import GatewayBtApi
from bt_api_py.gateway_calendar_service import CalendarGrant, GatewayCalendarService, ReceiptCheckedCalendarStore
from bt_api_py.gateway_direct_client import GatewayClientRoute
from bt_api_py.gateway_strategy_calendar import GatewayCalendarClientSettings, GatewayCalendarWindowsResult
from bt_api_py.service_components import ComponentResources
from bt_api_py.service_config import parse_client_config, parse_service_config
from bt_api_py.service_host import ServiceHost, TrustedServiceAuthority
from bt_api_py.service_registry import PrincipalGrant, ResourceGrant
from bt_api_py.service_runtime import ClientCurveKeys
from bt_api_py.gateway_strategy_client import GatewayStrategyClientSession
from bt_api_py.trading_calendar_composite import compose_simulated
from bt_api_py.trading_calendar_range_simulation import CalendarRangePolicy
from bt_api_py.trading_calendar_store import TradingCalendarStore
from bt_api_py.trading_calendar_update_simulation import VenueContent
from tests.calendar_test_store import FixtureCalendarStore
from tests.integration.test_iter63_gateway_direct_service import ACCOUNT, CLIENT_A, DOMAIN, SERVICE, _client_config, _service_config
from tests.integration.test_iter63_gateway_dual_ports import _resources
from tests.integration.test_service_account_instrument_metadata import _free_endpoint
from tests.unit.test_iter63_gateway_strategy_client import Host
from tests.unit.test_trading_calendar_binding import binding, content, decision

pytestmark = pytest.mark.skipif(not zmq.has("curve"), reason="CURVE unavailable")
RANGE_OPS = frozenset({"calendar.query.v1", "calendar.windows.v1"})
DAY = date(2026, 1, 2)
LAST = date(2026, 1, 10)
SCOPES = (
    ("SIM", "future", "x", "x2601"),
    ("SIM", "option", "y", "y2601"),
    ("SIM2", "future", "x", "x2601"),
    ("SIM", "future", "z", "z2601"),  # granted, but no range policy
)


def _publish(root, version, start="09:00"):
    venues = []
    for venue in ("SIM", "SIM2"):
        data = content(start)
        data["trading_days"] = [
            {"venue": venue, "day": (DAY + timedelta(days=i)).isoformat(),
             "status": "open" if i in (0, 3, 4) else "closed", "source_ids": ["synthetic"]}
            for i in range((LAST - DAY).days + 1)
        ]
        row = data["trading_hours"][0]
        row.update(venue=venue, valid_from=DAY.isoformat(), valid_to=LAST.isoformat(),
            night_windows=[{"id": "night", "phase": "continuous", "start": "21:00", "end": "23:00", "end_day_offset": 0}],
            night_mappings=[{"trading_day": d, "start_date": mapping, "source_ids": ["synthetic"]}
                for d, mapping in (("2026-01-02", None), ("2026-01-05", "2026-01-02"), ("2026-01-06", None))])
        if venue == "SIM":
            data["trading_hours"].append(dict(copy.deepcopy(row), kind="option", product="y", contracts=["y2601"]))
        venues.append(VenueContent.from_layers(venue, DAY, LAST, data))
    bundle = compose_simulated(mode="SIMULATION_ONLY", venues=tuple(venues), snapshot_factory=CalendarSnapshot)
    assert bundle.status == "READY_SIMULATED"
    return FixtureCalendarStore(root).publish(json.loads(bundle.snapshot_bytes), publication_id="pub-" + version,
        version_id=version, coverage_manifest_bytes=bundle.manifest_bytes,
        coverage_manifest_sha256=bundle.manifest_sha256, binding_bytes=bundle.binding_bytes,
        binding_sha256=bundle.binding_sha256)


def test_curve_facade_range_is_pinned_scoped_and_unverified(tmp_path, monkeypatch):
    cal = tmp_path / "calendar"; cal.mkdir()
    assert _publish(cal, "v1").visibility == "COMMITTED"
    bound = binding(ReceiptCheckedCalendarStore(TradingCalendarStore(cal)), day=DAY)
    endpoint = _free_endpoint()
    server_keys, client_keys = zmq.curve_keypair(), zmq.curve_keypair()
    raw = _service_config(endpoint).to_public_dict()
    raw["components"]["gateway"]["settings"]["bind_ports"].append("service.calendar_read")
    client_raw = _client_config(endpoint, CLIENT_A).to_public_dict()
    binds = client_raw["clients"]["gateway"]["settings"]["bindings"]
    binds.append(dict(binds[0], mount="store.calendar"))
    scope = WireScope(DOMAIN, account_ref=ACCOUNT, strategy_ref=CLIENT_A.actor_ref)
    direct_ops = frozenset({"direct.command_status"})
    principal = PrincipalGrant(CLIENT_A, (
        ResourceGrant("gateway-a", "service.direct_write", "scope-a", scope, direct_ops),
        ResourceGrant("gateway-a", "service.calendar_read", "scope-a", scope, RANGE_OPS)))
    authority = TrustedServiceAuthority(identity=SERVICE, scope=WireScope(DOMAIN), scope_ref="scope-a",
        scope_version="v1", security_ref="security-a", security_version="v1",
        registration_policy_ref="direct-policy-v1", registration_policy_version="v1",
        service_requirements_ref="direct-requirements-v1", service_requirements_version="v1",
        curve_public_key=server_keys[0], curve_secret_key=server_keys[1],
        principals={client_keys[0]: CLIENT_A.principal_ref}, grants={CLIENT_A.principal_ref: (principal,)})
    ready, stop = queue.Queue(), threading.Event()
    def serve():
        host = None
        try:
            direct, _, _ = _resources(tmp_path)
            grants = frozenset(CalendarGrant(CLIENT_A.principal_ref, DOMAIN, ACCOUNT, CLIENT_A.actor_ref,
                "run-a", "route-a", *item) for item in SCOPES)
            policies = frozenset(CalendarRangePolicy("policy-" + str(i), "route-a", *item, 3, 0)
                for i, item in enumerate(SCOPES[:3]))
            service = GatewayCalendarService(grants=grants, range_policies=policies)
            service.rebind(bound)
            host = ServiceHost(parse_service_config(raw), authority, tmp_path / "registry.sqlite", lambda *_:
                ComponentResources.borrowed({"binding": direct, "calendar_service": service}, ready=lambda: True))
            host.start(); ready.put(("ready", direct._journal.journal_id))
            while not stop.is_set(): host.serve_once(10)
        except BaseException as exc:
            ready.put(("error", type(exc).__name__, str(exc)))
        finally:
            if host is not None and host.state == "started": host.close()
    thread = threading.Thread(target=serve); thread.start()
    session = None
    try:
        started = ready.get(timeout=15)
        assert started[0] == "ready", started
        route = GatewayClientRoute("broker.direct_write", SERVICE.actor_ref, "gateway-a", "scope-a",
            WireScope(DOMAIN, account_ref=ACCOUNT, strategy_ref=CLIENT_A.actor_ref, run_id="run-a"), CLIENT_A)
        settings = GatewayCalendarClientSettings("SIMULATION_ONLY", tuple(dict(route="route-a", venue=v, kind=k, product=p, contract=c)
            for v, k, p, c in SCOPES), True)
        session = GatewayStrategyClientSession(parse_client_config(client_raw),
            binding=GatewayCommandBinding(started[1], ACCOUNT, CLIENT_A.actor_ref, CLIENT_A.principal_ref),
            route=route, peer=SERVICE, security_profiles={"security-a": ClientCurveKeys(server_keys[0], *client_keys)},
            host=Host(), requested_operations=direct_ops, calendar_settings=settings)
        session.start(timeout_seconds=10)
        api = GatewayBtApi(session, exchange_name="route-a")
        def query(item, start="2026-01-02T13:00:00Z", end="2026-01-06T03:00:00Z"):
            v, k, p, c = item
            return api.trading_calendar_windows("route-a", route="route-a", venue=v, kind=k, product=p,
                contract=c, start_utc=start, end_utc=end, timeout_seconds=5)
        first = query(SCOPES[0])
        assert type(first) is GatewayCalendarWindowsResult and first.status == "COMPLETE"
        assert first.policy["declaration"] == "DEPLOYMENT_DECLARED_UNVERIFIED" and first.gaps == ()
        assert (first.source_authentication, first.trade_authorization) == ("NOT_PROVEN", "NONE")
        assert [(w["window_id"], w["trading_day"]) for w in first.windows] == [
            ("night", "2026-01-05"), ("day", "2026-01-05"), ("day", "2026-01-06")]
        for item in SCOPES[1:3]:
            other = query(item)
            assert other.status == "COMPLETE" and other.binding == first.binding
            assert [(w["window_id"], w["trading_day"]) for w in other.windows] == [(w["window_id"], w["trading_day"]) for w in first.windows]
        missing = query(SCOPES[3])
        assert missing.status == "UNKNOWN" and missing.reason == "POLICY_UNAVAILABLE" and not missing.windows
        with pytest.raises(TypeError): first.windows[0]["window_id"] = "fake"
        assert _publish(cal, "v2", "10:00").visibility == "COMMITTED"
        assert query(SCOPES[0]).binding["calendar_ref"] == "v1"
        assert bound.activate(decision(day=date(2026, 1, 5))).outcome == "ACTIVATED"
        after = query(SCOPES[0], start="2026-01-05T02:00:00Z", end="2026-01-06T03:00:00Z")
        assert after.status == "COMPLETE" and after.binding["calendar_ref"] == "v2"
        assert all(w["start_utc"] >= "2026-01-05T02:00:00Z" for w in after.windows)
        # Force a switch after this RPC captures its fixed view; no mixed
        # windows may escape from the old and new revisions.
        from bt_api_py.trading_calendar_binding import SimulationCalendarReadView
        original = SimulationCalendarReadView.query
        entered, release, outcomes = threading.Event(), threading.Event(), []
        def blocked(view, **kwargs):
            entered.set()
            assert release.wait(5)
            return original(view, **kwargs)
        monkeypatch.setattr(SimulationCalendarReadView, "query", blocked)
        def concurrent_query():
            try:
                outcomes.append(query(SCOPES[0], start="2026-01-05T02:00:00Z", end="2026-01-06T03:00:00Z"))
            except BaseException as exc:
                outcomes.append(exc)
        call = threading.Thread(target=concurrent_query); call.start()
        try:
            assert entered.wait(5)
            assert bound.activate(decision(day=date(2026, 1, 6), sequence=3, event_id="event3")).outcome == "ACTIVATED"
        finally:
            release.set(); call.join(10)
        assert not call.is_alive() and len(outcomes) == 1
        assert outcomes[0].status == "UNKNOWN" and outcomes[0].reason == "OWNER_CHANGED" and not outcomes[0].windows
    finally:
        if session is not None: session.close(timeout_seconds=10)
        stop.set(); thread.join(15)
        assert not thread.is_alive()
