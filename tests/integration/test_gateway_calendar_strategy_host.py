"""Synthetic calendar: actual ServiceHost, CURVE and strategy facade."""
import queue
import threading

import pytest
import zmq
from bt_api_base.gateway_direct_protocol import GatewayCommandBinding
from bt_api_base.service_protocol import WireScope
from bt_api_py.gateway_api import GatewayBtApi
from bt_api_py.gateway_calendar_service import CalendarGrant, GatewayCalendarService, ReceiptCheckedCalendarStore
from bt_api_py.gateway_direct_client import GatewayClientRoute
from bt_api_py.gateway_strategy_calendar import GatewayCalendarClientSettings
from bt_api_py.gateway_strategy_client import GatewayStrategyClientSession
from bt_api_py.service_components import ComponentResources
from bt_api_py.service_config import parse_client_config, parse_service_config
from bt_api_py.service_host import ServiceHost, TrustedServiceAuthority
from bt_api_py.service_registry import PrincipalGrant, ResourceGrant
from bt_api_py.service_runtime import ClientCurveKeys
from bt_api_py.trading_calendar_store import TradingCalendarStore
from tests.calendar_test_store import FixtureCalendarStore
from tests.integration.test_iter63_gateway_direct_service import ACCOUNT, CLIENT_A, DOMAIN, SERVICE, _client_config, _service_config
from tests.integration.test_iter63_gateway_dual_ports import _resources
from tests.integration.test_service_account_instrument_metadata import _free_endpoint
from tests.unit.test_gateway_calendar_service import REQUEST
from tests.unit.test_iter63_gateway_strategy_client import Host
from tests.unit.test_trading_calendar_binding import binding, content, decision, publish

pytestmark = pytest.mark.skipif(not zmq.has("curve"), reason="CURVE unavailable")
OP = frozenset({"calendar.query.v1"})


def test_host_curve_strategy_calendar_lifecycle(tmp_path):
    calendar_dir = tmp_path / "calendar"
    calendar_dir.mkdir()
    store = FixtureCalendarStore(calendar_dir)
    assert publish(store, "v1", content()).visibility == "COMMITTED"
    bound = binding(ReceiptCheckedCalendarStore(TradingCalendarStore(calendar_dir)))
    endpoint = _free_endpoint()
    server_keys, client_keys = zmq.curve_keypair(), zmq.curve_keypair()
    raw = _service_config(endpoint).to_public_dict()
    raw["components"]["gateway"]["settings"]["bind_ports"].append("service.calendar_read")
    config = parse_service_config(raw)
    client_raw = _client_config(endpoint, CLIENT_A).to_public_dict()
    bindings = client_raw["clients"]["gateway"]["settings"]["bindings"]
    bindings.append(dict(bindings[0], mount="store.calendar"))
    client_config = parse_client_config(client_raw)
    scope = WireScope(DOMAIN, account_ref=ACCOUNT, strategy_ref=CLIENT_A.actor_ref)
    operations = frozenset({"direct.command_status"})
    principal = PrincipalGrant(CLIENT_A, (
        ResourceGrant("gateway-a", "service.direct_write", "scope-a", scope, operations),
        ResourceGrant("gateway-a", "service.calendar_read", "scope-a", scope, OP),
    ))
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
            calendar = GatewayCalendarService(grants=frozenset({CalendarGrant(CLIENT_A.principal_ref,
                DOMAIN, ACCOUNT, CLIENT_A.actor_ref, "run-a", "route-a", "SIM", "future", "x", "x2601")}))
            calendar.rebind(bound)
            host = ServiceHost(config, authority, tmp_path / "registry.sqlite", lambda *_:
                ComponentResources.borrowed({"binding": direct, "calendar_service": calendar}, ready=lambda: True))
            host.start()
            ready.put(("ready", direct._journal.journal_id, host._registry.service_epoch, calendar))
            while not stop.is_set():
                host.serve_once(10)
        except BaseException as exc:
            ready.put(("error", type(exc).__name__, str(exc)))
        finally:
            if host is not None and host.state == "started":
                host.close()
    thread = threading.Thread(target=serve)
    thread.start()
    sessions = []
    try:
        started = ready.get(timeout=15)
        assert started[0] == "ready", started
        route = GatewayClientRoute("broker.direct_write", SERVICE.actor_ref, "gateway-a", "scope-a",
            WireScope(DOMAIN, account_ref=ACCOUNT, strategy_ref=CLIENT_A.actor_ref, run_id="run-a"), CLIENT_A)
        settings = GatewayCalendarClientSettings("SIMULATION_ONLY", (
            {k: REQUEST[k] for k in ("route", "venue", "kind", "product", "contract")},
            dict(route="route-a", venue="SIM", kind="future", product="x", contract="unauthorized"),))
        session = GatewayStrategyClientSession(client_config, binding=GatewayCommandBinding(started[1], ACCOUNT,
            CLIENT_A.actor_ref, CLIENT_A.principal_ref), route=route, peer=SERVICE,
            security_profiles={"security-a": ClientCurveKeys(server_keys[0], *client_keys)}, host=Host(),
            requested_operations=operations, calendar_settings=settings)
        sessions.append(session)
        api = GatewayBtApi(session, exchange_name="route-a")
        session.start(timeout_seconds=10)
        def query(day="2026-01-01"):
            return api.trading_calendar_query("route-a", **{k: REQUEST[k] for k in ("route", "venue", "kind", "product", "contract")}, trading_day=day)
        first = query()
        assert first.status == "COMPLETE" and first.binding["calendar_ref"] == "v1"
        assert (first.service_epoch, first.source_authentication, first.trade_authorization) == (started[2], "NOT_PROVEN", "NONE")
        assert publish(store, "v2", content("10:00")).visibility == "COMMITTED"
        assert query().binding["calendar_ref"] == "v1"
        assert bound.activate(decision()).outcome == "ACTIVATED"
        second = query("2026-01-02")
        assert second.binding["calendar_ref"] == "v2"
        assert second.windows[0]["start_utc"] == "2026-01-02T02:00:00Z"
        assert second.registration_id == first.registration_id
        with pytest.raises(TypeError):
            second.binding["calendar_ref"] = "fake"
        from bt_api_py.service_runtime_thread import OwnerOperationError
        with pytest.raises(OwnerOperationError, match="CALENDAR_OUTCOME_UNKNOWN"):
            api.trading_calendar_query("route-a", route="route-a", venue="SIM", kind="future", product="x", contract="unauthorized", trading_day="2026-01-01")
        session.close(timeout_seconds=10)
        replacement = GatewayStrategyClientSession(client_config, binding=GatewayCommandBinding(started[1], ACCOUNT,
            CLIENT_A.actor_ref, CLIENT_A.principal_ref), route=route, peer=SERVICE,
            security_profiles={"security-a": ClientCurveKeys(server_keys[0], *client_keys)}, host=Host(),
            requested_operations=operations, calendar_settings=settings)
        sessions.append(replacement)
        replacement.start(timeout_seconds=10)
        new_api = GatewayBtApi(replacement, exchange_name="route-a")
        fresh = new_api.trading_calendar_query("route-a", **{k: REQUEST[k] for k in ("route", "venue", "kind", "product", "contract")}, trading_day="2026-01-02")
        assert fresh.registration_id != first.registration_id
        assert fresh.service_epoch == first.service_epoch and fresh.binding["calendar_ref"] == "v2"
        replacement.close(timeout_seconds=10)
    finally:
        for session in sessions:
            session.close(timeout_seconds=10)
        stop.set()
        thread.join(15)
        assert not thread.is_alive()
    with pytest.raises(ValueError):
        started[3].rebind(bound)


def test_host_failed_registry_binding_closes_service(tmp_path, monkeypatch):
    from bt_api_py.service_host import ServiceHostError
    endpoint = _free_endpoint()
    server_keys, client_keys = zmq.curve_keypair(), zmq.curve_keypair()
    raw = _service_config(endpoint).to_public_dict()
    raw["components"]["gateway"]["settings"]["bind_ports"].append("service.calendar_read")
    scope = WireScope(DOMAIN, account_ref=ACCOUNT, strategy_ref=CLIENT_A.actor_ref)
    principal = PrincipalGrant(CLIENT_A, (
        ResourceGrant("gateway-a", "service.direct_write", "scope-a", scope, frozenset({"direct.command_status"})),
        ResourceGrant("gateway-a", "service.calendar_read", "scope-a", scope, OP),
    ))
    authority = TrustedServiceAuthority(identity=SERVICE, scope=WireScope(DOMAIN), scope_ref="scope-a",
        scope_version="v1", security_ref="security-a", security_version="v1",
        registration_policy_ref="direct-policy-v1", registration_policy_version="v1",
        service_requirements_ref="direct-requirements-v1", service_requirements_version="v1",
        curve_public_key=server_keys[0], curve_secret_key=server_keys[1],
        principals={client_keys[0]: CLIENT_A.principal_ref}, grants={CLIENT_A.principal_ref: (principal,)})
    direct, _, _ = _resources(tmp_path)
    service = GatewayCalendarService(grants=frozenset({CalendarGrant(CLIENT_A.principal_ref,
        DOMAIN, ACCOUNT, CLIENT_A.actor_ref, "run-a", "route-a", "SIM", "future", "x", "x2601")}))
    def fail_binding(self, registry):
        raise ValueError("synthetic failure")
    monkeypatch.setattr(GatewayCalendarService, "bind_registry", fail_binding)
    host = ServiceHost(parse_service_config(raw), authority, tmp_path / "registry.sqlite", lambda *_:
        ComponentResources.borrowed({"binding": direct, "calendar_service": service}, ready=lambda: True))
    with pytest.raises(ServiceHostError, match="GATEWAY_CALENDAR_INVALID"):
        host.start()
    with pytest.raises(ValueError):
        service.rebind(None)
