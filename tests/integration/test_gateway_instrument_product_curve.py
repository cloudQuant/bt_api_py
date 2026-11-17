"""Independent ProductID port through trading factory, CURVE and a synthetic Trader."""

import importlib
import queue
import threading
import time
from dataclasses import replace
from datetime import date
from pathlib import Path

import pytest
import zmq
from bt_api_base.account_protocol import AccountKey
from bt_api_base.market_protocol import MarketInstrumentKey
from bt_api_base.service_protocol import WireIdentity, WireRole, WireScope
from bt_api_ctp.ctp.client import CtpNativeStopReceipt, TraderClient
from bt_api_ctp.feeds.live_ctp_feed import CtpRequestDataFuture

import bt_api_py.ctp_gateway_trading as trading
from bt_api_py.ctp_gateway_calendar_config import CtpGatewayCalendarSettings
from bt_api_py.gateway_calendar_service import CalendarMonitorGrant
from bt_api_py.gateway_instrument_product_client import GatewayInstrumentProductClient
from bt_api_py.gateway_instrument_product_codec import OPERATIONS
from bt_api_py.gateway_instrument_product_config import InstrumentProductGrant
from bt_api_py.service_config import parse_service_config
from bt_api_py.service_registry import PrincipalGrant, ResourceGrant
from bt_api_py.service_runtime import ClientCurveKeys, ServiceClientRuntime, ZmqTransportFactory
from bt_api_py.trading_calendar_range_simulation import CalendarRangePolicy
from tests.calendar_test_store import FixtureCalendarStore
from tests.integration.test_iter63_gateway_direct_service import DOMAIN, SERVICE
from tests.integration.test_monitor_service_process import client as telemetry_client
from tests.test_iter63_ctp_gateway_trading import make
from tests.unit.test_trading_calendar_binding import content, publish

pytestmark = pytest.mark.skipif(not zmq.has("curve"), reason="CURVE unavailable")
KEY = MarketInstrumentKey("ctp", "SHFE", "simnow", "future", "rb2701", "rb2701")
IDENTITY = WireIdentity(WireRole.SERVICE, "product-reader", "monitor-service")


@pytest.mark.parametrize("dual", [False, True])
def test_product_only_port_uses_original_trading_session(tmp_path, monkeypatch, dual):
    monkeypatch.syspath_prepend(
        str(Path(__file__).resolve().parents[2] / "bt_api/bt_api_ctp/tests")
    )
    helper = importlib.import_module("test_iter62_settlement_readback_on_write")
    native_row = importlib.import_module("test_iter63_gateway_instrument_query").row
    real_loader = trading._load_ctp_types
    account = AccountKey("ctp", "simnow", "simnow", "account", "9999")
    _, settings, config, authority, direct, _ = make(tmp_path, monkeypatch, account_key=account)
    monkeypatch.setattr(trading, "_load_ctp_types", real_loader)
    settings = replace(settings, close_timeout_seconds=1, connect_timeout_seconds=1)
    server_keys, client_keys = zmq.curve_keypair(), zmq.curve_keypair()
    scope = WireScope(DOMAIN, account_ref=settings.account_scope)
    resource = ResourceGrant(
        "gateway-a", "service.instrument_product_read", "scope-a", scope, OPERATIONS
    )
    calendar_settings = None
    resources = [resource]
    if dual:
        calendar_root = tmp_path / "calendar"
        assert (
            publish(FixtureCalendarStore(calendar_root), "v1", content()).visibility == "COMMITTED"
        )
        policy = CalendarRangePolicy("policy", "CTP___FUTURE", "SIM", "future", "x", "x2601", 0, 0)
        calendar_grant = CalendarMonitorGrant(
            IDENTITY.principal_ref,
            IDENTITY.actor_ref,
            DOMAIN,
            settings.account_scope,
            "CTP___FUTURE",
            "SIM",
            "future",
            "x",
            "x2601",
        )
        calendar_settings = CtpGatewayCalendarSettings(
            calendar_root,
            "CTP___FUTURE",
            frozenset(),
            frozenset({policy}),
            date(2026, 1, 1),
            monitor_grants=frozenset({calendar_grant}),
        )
        resources.append(
            ResourceGrant(
                "gateway-a",
                "service.calendar_read",
                "scope-a",
                scope,
                frozenset({"calendar.monitor.windows.v1"}),
            )
        )
    authority = replace(
        authority,
        curve_public_key=server_keys[0],
        curve_secret_key=server_keys[1],
        principals={**authority.principals, client_keys[0]: IDENTITY.principal_ref},
        grants={
            **authority.grants,
            IDENTITY.principal_ref: (PrincipalGrant(IDENTITY, tuple(resources)),),
        },
    )
    raw = config.to_public_dict()
    raw["components"]["gateway"]["settings"]["bind_ports"].append("service.instrument_product_read")
    if dual:
        raw["components"]["gateway"]["settings"]["bind_ports"].append("service.calendar_read")
    config = parse_service_config(raw)
    native_calls, natives, feeds = [], [], []

    def start(trader, **_kwargs):
        native = helper._NativeApi()
        native.client = trader

        def request(field, request_id):
            native_calls.append((field.InstrumentID, field.ExchangeID, trader))
            trader._handle_query_callback("instruments", native_row(), None, request_id, False)
            trader._handle_query_callback("instruments", None, None, request_id, True)
            return 0

        native.ReqQryInstrument = request
        trader._api = native
        helper._login(trader, native, session_id=19)
        natives.append(native)

    monkeypatch.setattr(TraderClient, "start", start)
    monkeypatch.setattr(
        TraderClient,
        "stop_and_wait",
        lambda trader, **_: CtpNativeStopReceipt(
            trader.connection_generation, False, True, True, False, False
        ),
    )

    def feed():
        result = CtpRequestDataFuture(
            None,
            broker_id="9999",
            user_id="account",
            password="synthetic",
            td_front="tcp://offline.invalid:30001",
            md_front="tcp://offline.invalid:30011",
            auto_settlement_confirm=False,
            ctp_env_profile="config_front_pair",
            ctp_environment="simnow",
            trusted_flow_root=str(settings.trusted_flow_root),
            flow_account_scope=settings.account_scope,
        )
        feeds.append(result)
        return result

    grant = InstrumentProductGrant(
        IDENTITY.principal_ref, IDENTITY.actor_ref, DOMAIN, settings.account_scope, frozenset({KEY})
    )
    profile = trading.build_ctp_gateway_trading_profile(
        config,
        authority,
        tmp_path / "registry-product.sqlite",
        profile_ref="product-test",
        settings=settings,
        grants=direct,
        request_feed_factory=feed,
        market_stream_factory=lambda *_: pytest.fail("market session forbidden"),
        assert_admitted=lambda _: False,
        instrument_product_grants=frozenset({grant}),
        calendar_settings=calendar_settings,
    )
    ready, stop = queue.Queue(), threading.Event()

    def server():
        try:
            deployment = profile.deployment_factory()
            deployment.start()
            ready.put(("ready",))
            while not stop.is_set():
                deployment.serve_once(10)
            deployment.close()
        except BaseException as exc:
            ready.put(("error", type(exc).__name__, str(exc)))

    thread = threading.Thread(target=server)
    thread.start()
    client = None
    try:
        result = ready.get(timeout=10)
        assert result == ("ready",), result
        endpoint = config.communication["profiles"]["server-transport"]["endpoints"]["rpc_bind"]
        listener = {
            "principal_ref": SERVICE.principal_ref,
            "service_id": SERVICE.actor_ref,
            "instance_id": "gateway-a",
            "scope_ref": "scope-a",
            "rpc_bind": endpoint,
        }
        template = telemetry_client(
            listener,
            server_keys[0],
            client_keys,
            IDENTITY.principal_ref,
            "events.append",
            actor=IDENTITY.actor_ref,
        )
        client_raw = template._config
        client_raw["clients"] = {"gateway": client_raw["clients"].pop("monitor")}
        binding = client_raw["clients"]["gateway"]["settings"]["bindings"][0]
        binding.update(mount="upstream.instrument_products", account_ref=settings.account_scope)
        if dual:
            client_raw["clients"]["gateway"]["settings"]["bindings"].append(
                dict(binding, mount="upstream.calendar")
            )
        client = ServiceClientRuntime(
            parse_service_config(client_raw),
            identity=IDENTITY,
            scope_resolver=lambda _: scope,
            peer_resolver=lambda _: SERVICE,
            transport_factory=ZmqTransportFactory(
                {"client": ClientCurveKeys(server_keys[0], *client_keys)}
            ),
            requested_operations={
                "upstream.instrument_products": OPERATIONS,
                **(
                    {"upstream.calendar": frozenset({"calendar.monitor.windows.v1"})}
                    if dual
                    else {}
                ),
            },
        )
        client.start()
        if dual:
            calendar = client.call(
                "upstream.calendar",
                "calendar.monitor.windows.v1",
                {
                    "mode": "SIMULATION_ONLY",
                    "route": "CTP___FUTURE",
                    "venue": "SIM",
                    "kind": "future",
                    "product": "x",
                    "contract": "x2601",
                    "start_utc": "2026-01-01T01:30:00Z",
                    "end_utc": "2026-01-01T02:00:00Z",
                },
            )
            assert (
                calendar["status"] == "COMPLETE"
                and calendar["source_authentication"] == "NOT_PROVEN"
            )
        product = GatewayInstrumentProductClient(
            client,
            identity=IDENTITY,
            scope=scope,
            peer=SERVICE,
            service_id=SERVICE.actor_ref,
            instance_id="gateway-a",
            scope_ref="scope-a",
        )
        opid = "a" * 32
        assert product.begin(opid, KEY).status == "pending"
        original_binding = next(
            b for b in client.bindings if b.mount == "upstream.instrument_products"
        )
        client._maintain(force=True)
        client._maintain(force=True)
        assert (
            next(b for b in client.bindings if b.mount == "upstream.instrument_products")
            is not original_binding
        )
        deadline = time.monotonic() + 6
        while time.monotonic() < deadline:
            result = product.status(opid)
            if result.status == "ready":
                break
            assert result.status == "pending", result
            time.sleep(0.01)
        assert result.status == "ready"
        assert result.product_id == "rb" and result.instrument == KEY
        assert time.monotonic() < result.valid_until_monotonic
        product.close()
        assert client.state == "registered"
        assert len(feeds) == len(natives) == len(native_calls) == 1
        assert native_calls[0] == ("rb2701", "SHFE", natives[0].client)
    finally:
        if client is not None:
            client.close()
        stop.set()
        thread.join(10)
        assert not thread.is_alive()
        if not ready.empty():
            assert ready.get_nowait()[0] != "error"


def test_operator_loader_shared_identity_through_both_curve_mounts(tmp_path, monkeypatch):
    import json
    from types import SimpleNamespace

    from bt_api_py.ctp_gateway_local_profile import simnow_trading_profile_factory
    from tests.unit.test_gateway_instrument_product_config import configured_shared

    monkeypatch.syspath_prepend(
        str(Path(__file__).resolve().parents[2] / "bt_api/bt_api_ctp/tests")
    )
    helper = importlib.import_module("test_iter62_settlement_readback_on_write")
    native_row = importlib.import_module("test_iter63_gateway_instrument_query").row
    real_loader = trading._load_ctp_types
    doc, file, config = configured_shared(tmp_path, monkeypatch)
    monkeypatch.setattr(trading, "_load_ctp_types", real_loader)
    public, secret = zmq.curve_keypair()
    doc["instrument_product_observers"]["clients"][0]["curve_public_key"] = public.decode()
    doc["calendars"]["monitor_clients"][0]["curve_public_key"] = public.decode()
    doc["calendars"]["simulation_bootstrap"] = {"trading_day": "2026-01-01"}
    assert (
        publish(FixtureCalendarStore(file.parent / "calendars"), "v1", content()).visibility
        == "COMMITTED"
    )
    file.write_text(json.dumps(doc), encoding="utf-8")
    native_calls = []

    def start(trader, **_kwargs):
        native = helper._NativeApi()
        native.client = trader

        def request(field, request_id):
            native_calls.append((field.InstrumentID, field.ExchangeID))
            trader._handle_query_callback("instruments", native_row(), None, request_id, False)
            trader._handle_query_callback("instruments", None, None, request_id, True)
            return 0

        native.ReqQryInstrument = request
        trader._api = native
        trader._on_front_connected()
        with trader._query_state_lock:
            trader._authentication_state = "authenticated"
            trader._login_state = "logging_in"
            request_id = trader._next_request_id()
            trader._login_request_id = request_id
            trader._login_connection_generation = trader._connection_generation
            spi = helper._TraderSpi(trader, native)
            trader._spi = spi
        trader._session_native_api = native
        trader._session_native_front = trader._bound_front
        trader._query_interval = 0
        spi.OnRspUserLogin(
            SimpleNamespace(
                BrokerID="09999",
                UserID="00001",
                TradingDay="20261106",
                FrontID=7,
                SessionID=19,
                MaxOrderRef="0",
            ),
            SimpleNamespace(ErrorID=0, ErrorMsg=""),
            request_id,
            True,
        )

    monkeypatch.setattr(TraderClient, "start", start)
    monkeypatch.setattr(
        TraderClient,
        "stop_and_wait",
        lambda trader, **_: CtpNativeStopReceipt(
            trader.connection_generation, False, True, True, False, False
        ),
    )
    ready, stop = queue.Queue(), threading.Event()

    def server():
        try:
            deployment = simnow_trading_profile_factory.create_deployment(config)
            deployment.start()
            ready.put(("ready",))
            while not stop.is_set():
                deployment.serve_once(10)
            deployment.close()
        except BaseException as error:
            ready.put(("error", type(error).__name__, str(error)))

    thread = threading.Thread(target=server)
    thread.start()
    client = None
    try:
        result = ready.get(timeout=10)
        assert result == ("ready",), result
        ident = WireIdentity(WireRole.SERVICE, "monitor-p", "monitor-a")
        scope_raw = doc["instrument_product_observers"]["clients"][0]["resources"][0]["scope"]
        scope = WireScope(scope_raw["execution_domain_ref"], account_ref=scope_raw["account_ref"])
        server_id = doc["authority"]["identity"]
        peer = WireIdentity(WireRole.SERVICE, server_id["principal_ref"], server_id["actor_ref"])
        endpoint = config.communication["profiles"]["server-transport"]["endpoints"]["rpc_bind"]
        listener = {
            "principal_ref": peer.principal_ref,
            "service_id": peer.actor_ref,
            "instance_id": "gateway-a",
            "scope_ref": "scope-a",
            "rpc_bind": endpoint,
        }
        template = telemetry_client(
            listener,
            doc["authority"]["curve_public_key"].encode(),
            (public, secret),
            ident.principal_ref,
            "events.append",
            actor=ident.actor_ref,
        )
        raw = template._config
        raw["clients"] = {"gateway": raw["clients"].pop("monitor")}
        binding = raw["clients"]["gateway"]["settings"]["bindings"][0]
        binding.update(mount="upstream.instrument_products", account_ref=scope.account_ref)
        raw["clients"]["gateway"]["settings"]["bindings"].append(
            dict(binding, mount="upstream.calendar")
        )
        client = ServiceClientRuntime(
            parse_service_config(raw),
            identity=ident,
            scope_resolver=lambda _: scope,
            peer_resolver=lambda _: peer,
            transport_factory=ZmqTransportFactory(
                {
                    "client": ClientCurveKeys(
                        doc["authority"]["curve_public_key"].encode(), public, secret
                    )
                }
            ),
            requested_operations={
                "upstream.instrument_products": OPERATIONS,
                "upstream.calendar": frozenset({"calendar.monitor.windows.v1"}),
            },
        )
        client.start()
        calendar = client.call(
            "upstream.calendar",
            "calendar.monitor.windows.v1",
            {
                "mode": "SIMULATION_ONLY",
                "route": "CTP___FUTURE",
                "venue": "SIM",
                "kind": "future",
                "product": "x",
                "contract": "x2601",
                "start_utc": "2026-01-01T01:30:00Z",
                "end_utc": "2026-01-01T02:00:00Z",
            },
        )
        assert (
            calendar["status"] == "COMPLETE" and calendar["source_authentication"] == "NOT_PROVEN"
        )
        product = GatewayInstrumentProductClient(
            client,
            identity=ident,
            scope=scope,
            peer=peer,
            service_id=peer.actor_ref,
            instance_id="gateway-a",
            scope_ref="scope-a",
        )
        operation_id = "b" * 32
        assert product.begin(operation_id, KEY).status == "pending"
        deadline = time.monotonic() + 5
        while time.monotonic() < deadline:
            result = product.status(operation_id)
            if result.status != "pending":
                break
            time.sleep(0.01)
        assert result.status == "ready" and result.product_id == "rb"
        assert native_calls == [("rb2701", "SHFE")]
        product.close()
    finally:
        if client is not None:
            client.close()
        stop.set()
        thread.join(10)
        assert not thread.is_alive()
        if not ready.empty():
            assert ready.get_nowait()[0] != "error"
