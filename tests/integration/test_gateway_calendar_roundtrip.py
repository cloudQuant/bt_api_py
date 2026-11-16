"""CURVE/Registry loopback with a separately spawned simulated publisher."""
import multiprocessing
import queue
import threading
from concurrent.futures import ThreadPoolExecutor

import pytest
import zmq
from bt_api_base.service_protocol import WireIdentity, WireRole, WireScope
from bt_api_transport_zmq.server import CurveServerCredentials
from bt_api_transport_zmq.service_v2 import ZmqServiceServer
from bt_api_py.gateway_calendar_service import CalendarGrant, GatewayCalendarService, ReceiptCheckedCalendarStore
from bt_api_py.trading_calendar_range_simulation import CalendarRangePolicy
from bt_api_py.service_config import parse_client_config
from bt_api_py.service_registry import PrincipalGrant, ResourceGrant, ServiceCapability, ServiceRegistry
from bt_api_py.service_runtime import ClientCurveKeys, ServiceClientRuntime, ZmqTransportFactory
from bt_api_py.service_runtime_thread import ThreadedServiceClientRuntime
from bt_api_py.service_runtime import ServiceCallOutcomeUnknownError
from bt_api_py.trading_calendar_store import TradingCalendarStore
from tests.calendar_test_store import FixtureCalendarStore
from tests.unit.test_trading_calendar_binding import binding, content, decision, publish
from tests.unit.test_gateway_calendar_service import REQUEST
from tests.integration.test_service_account_instrument_metadata import _free_endpoint

pytestmark = pytest.mark.skipif(not zmq.has("curve"), reason="CURVE unavailable")
CLIENT = WireIdentity(WireRole.STRATEGY_CLIENT, "principal", "strategy")
SERVER = WireIdentity(WireRole.SERVICE, "server", "server-actor")
SCOPE = WireScope("domain", account_ref="account", strategy_ref="strategy")
OP = "calendar.query.v1"
WINDOWS_OP = "calendar.windows.v1"


def _publish(root, output):
    try:
        result = publish(FixtureCalendarStore(root), "v2", content("10:00"))
        output.put(result.visibility)
    except Exception as exc:
        output.put(type(exc).__name__)


def _config(endpoint):
    return parse_client_config({"client_config_schema_version": 1, "process_role": "strategy_client",
        "registration": {"strategy_ref": "strategy"}, "clients": {"gateway": {
            "config_schema_version": 1, "settings": {"bindings": [{"mount": "store.calendar",
                "service_id": "svc", "instance_id": "instance", "scope_ref": "scope-v1",
                "account_ref": "account", "transport_ref": "transport"}],
                "options": {"request_timeout_ms": 3000, "heartbeat_interval_seconds": 5}}}},
        "communication": {"config_schema_version": 1, "profiles": {"transport": {"backend": "zmq",
            "config_schema_version": 1, "endpoints": {"rpc_connect": endpoint},
            "security_profile_ref": "security"}}}})


def test_curve_registry_fixed_binding_with_spawned_publisher(tmp_path):
    store = FixtureCalendarStore(tmp_path)
    assert publish(store, "v1", content()).visibility == "COMMITTED"
    bound = binding(ReceiptCheckedCalendarStore(TradingCalendarStore(tmp_path)))
    endpoint = _free_endpoint()
    server_public, server_secret = zmq.curve_keypair()
    client_public, client_secret = zmq.curve_keypair()
    cap = ServiceCapability("instance", "service.calendar_read", frozenset({OP, WINDOWS_OP}), frozenset())
    resource = ResourceGrant("instance", "service.calendar_read", "scope-v1", SCOPE, frozenset({OP, WINDOWS_OP}))
    registry = ServiceRegistry(tmp_path / "registry.sqlite", service_id="svc", capabilities=(cap,),
        grants={"principal": (PrincipalGrant(CLIENT, (resource,)),)}, policy_version="policy-v1", requirements_version="requirements-v1")
    grant = CalendarGrant("principal", "domain", "account", "strategy", "run", "route-a", "SIM", "future", "x", "x2601")
    policy = CalendarRangePolicy("range-policy", "route-a", "SIM", "future", "x", "x2601", 0, 0)
    service = GatewayCalendarService(grants=frozenset({grant}), range_policies=frozenset({policy}))
    service.bind_registry(registry)
    service.rebind(bound)
    registry.register_handler("instance", OP, lambda ctx, args: service.handle(OP, context=ctx, arguments=args))
    registry.register_handler("instance", WINDOWS_OP, lambda ctx, args: service.handle(WINDOWS_OP, context=ctx, arguments=args))
    ready, stop = queue.Queue(), threading.Event()
    def serve():
        server = None
        try:
            server = ZmqServiceServer(endpoint, SERVER, {client_public: "principal"}, registry.handle,
                                      credentials=CurveServerCredentials(server_public, server_secret))
            ready.put(None)
            while not stop.is_set(): server.serve_once(20)
        except BaseException as exc:
            ready.put(exc)
        finally:
            if server is not None: server.close()
    thread = threading.Thread(target=serve)
    thread.start()
    assert ready.get(timeout=5) is None
    runtime = ThreadedServiceClientRuntime(lambda: ServiceClientRuntime(_config(endpoint), identity=CLIENT,
        scope_resolver=lambda _: SCOPE, peer_resolver=lambda _: SERVER,
        transport_factory=ZmqTransportFactory({"security": ClientCurveKeys(server_public, client_public, client_secret)}),
        run_id="run", requested_operations={"store.calendar": frozenset({OP, WINDOWS_OP})}))
    try:
        runtime.start(timeout_seconds=8)
        def query(day="2026-01-01"):
            return runtime.call("store.calendar", OP, dict(REQUEST, trading_day=day), timeout_seconds=5)
        one = query()
        assert one["binding"]["calendar_ref"] == "v1"
        def windows(day):
            args = {k: REQUEST[k] for k in ("mode", "route", "venue", "kind", "product", "contract")}
            args.update(start_utc=f"{day}T01:30:00Z", end_utc=f"{day}T02:00:00Z")
            return runtime.call("store.calendar", WINDOWS_OP, args, timeout_seconds=5)
        assert windows("2026-01-01")["windows"][0]["start_utc"] == "2026-01-01T01:00:00Z"
        with ThreadPoolExecutor(max_workers=2) as pool:
            same = list(pool.map(lambda _: query(), range(2)))
        assert same[0] == same[1] == one
        for extra in ({"event": "fake"}, {"contract": "forbidden"}):
            with pytest.raises(ServiceCallOutcomeUnknownError):
                runtime.call("store.calendar", OP, dict(REQUEST, **extra), timeout_seconds=5)
        ctx = multiprocessing.get_context("spawn")
        output = ctx.Queue()
        process = ctx.Process(target=_publish, args=(str(tmp_path), output))
        process.start()
        assert output.get(timeout=15) == "COMMITTED"
        process.join(10)
        assert process.exitcode == 0
        assert query()["binding"]["calendar_ref"] == "v1"
        assert windows("2026-01-01")["binding"]["calendar_ref"] == "v1"
        assert bound.activate(decision(generation="old")).outcome == "GATE_REJECTED"
        assert query()["binding"]["calendar_ref"] == "v1"
        assert bound.activate(decision()).outcome == "ACTIVATED"
        assert bound.activate(decision()).outcome == "DUPLICATE"
        two = query("2026-01-02")
        assert windows("2026-01-02")["binding"]["calendar_ref"] == "v2"
        assert two["binding"]["calendar_ref"] == "v2" and two["windows"][0]["start_utc"] == "2026-01-02T02:00:00Z"
        assert two["registration_id"] == one["registration_id"] and two["service_epoch"] == registry.service_epoch
        assert "account" not in str(two["windows"])
        (tmp_path / "CURRENT.json").write_bytes(b"{}")
        from datetime import date
        assert bound.activate(decision(day=date(2026, 1, 3), sequence=3, event_id="event3")).outcome == "LOAD_FAILED"
        stale = query("2026-01-02")
        assert stale["binding"]["calendar_ref"] == "v2" and stale["binding"]["load_status"] == "STALE"
    finally:
        runtime.close(timeout_seconds=5)
        stop.set()
        thread.join(5)
        registry.close()
        assert not thread.is_alive()
