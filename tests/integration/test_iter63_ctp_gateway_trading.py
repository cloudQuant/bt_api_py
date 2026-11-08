"""Spawned offline profile + real ServiceHost/CURVE; synthetic session/provider."""

import multiprocessing
import os
import queue
import threading
import time
from datetime import UTC
from pathlib import Path

import pytest
import zmq
from bt_api_base.account_protocol import AccountKey
from bt_api_base.direct_write_protocol import SettlementStatus
from bt_api_base.gateway_direct_protocol import GatewayCommandBinding
from bt_api_base.service_protocol import WireScope
from bt_api_gateway.direct_command_journal import GatewayDirectCommandJournal

from bt_api_py._contracts.models import OrderRequest
from bt_api_py.ctp_gateway_trading import CtpGatewayTradingSettings
from bt_api_py.gateway_direct_client import GatewayClientRoute, GatewayDirectClient
from bt_api_py.service_config import parse_client_config, parse_service_config
from bt_api_py.service_host import TrustedServiceAuthority
from bt_api_py.service_registry import PrincipalGrant, ResourceGrant
from bt_api_py.service_runtime import ClientCurveKeys, ServiceClientRuntime, ZmqTransportFactory
from bt_api_py.service_runtime_thread import ThreadedServiceClientRuntime
from tests.integration.test_iter63_gateway_client_processes import _TestSqliteHost
from tests.integration.test_iter63_gateway_direct_service import (
    CLIENT_A,
    DOMAIN,
    SERVICE,
    _client_config,
    _service_config,
)
from tests.integration.test_service_account_instrument_metadata import _free_endpoint
from tests.unit.test_iter63_gateway_direct_service import _order


def _server(
    root, endpoint, server_keys, client_public, journal_id, events, controls, read_control=None
):
    import bt_api_py.ctp_gateway_trading as module
    from bt_api_py.gateway_direct_service import GatewayDirectGrant
    from tests.test_iter63_ctp_gateway_trading import FakeProvider, FakeSession

    root = Path(root)
    FakeSession.instances = []
    FakeSession.initial_status = SettlementStatus.CONFIRMED
    module._load_ctp_types = lambda: (FakeSession, FakeProvider)
    if read_control is not None:
        from datetime import datetime

        from bt_api_base.account_protocol import (
            AccountObservation,
            AccountOpenOrder,
            AccountResourceKey,
        )
        from bt_api_base.market_protocol import MarketInstrumentKey

        should_block, blocked, resume, issuer_id = read_control
        resource_key = AccountResourceKey("future", "cross")
        count = [0]

        def read_account(session, *, generation, timeout):
            assert threading.get_ident() == session.owner
            count[0] += 1
            if count[0] == 1:
                events.put(("read", os.getpid(), threading.get_ident()))
            if should_block.is_set():
                blocked.set()
                if not resume.wait(10):
                    raise RuntimeError("synthetic timeout")
                raise RuntimeError("synthetic failed query")
            stamp = datetime.now(UTC).isoformat().replace("+00:00", "Z")
            instrument = session.kwargs["observation_instruments"][0]
            order_id = session.kwargs["observation_issuer"].issue_order_observation_id(
                "ctp", "simnow", session.key.account_key_id, "synthetic-private-order"
            )
            order = AccountOpenOrder(
                resource_key,
                instrument,
                order_id,
                "buy",
                "net",
                "limit",
                "open",
                "2",
                "0",
                "contract",
                stamp,
                stamp,
            )
            return AccountObservation(
                session.key,
                f"obs-{count[0]}",
                (resource_key,),
                (),
                (),
                None,
                stamp,
                stamp,
                "multi_query",
                True,
                True,
                (order,),
                True,
            )

        FakeSession.read_account = read_account
    first_ready = [True]
    original_tick = module.GatewaySettlementController.tick
    original_submit = FakeProvider.submit_order

    def tick(controller):
        result = original_tick(controller)
        if result.observed_ready and first_ready[0]:
            first_ready[0] = False
            events.put(("observed", os.getpid(), threading.get_ident()))
        return result

    def submit(self, reservation, order, grant, verifier):
        result = original_submit(self, reservation, order, grant, verifier)
        events.put(("sent", os.getpid(), self.threads[-1], order.idempotency_key))
        return result

    module.GatewaySettlementController.tick = tick
    FakeProvider.submit_order = submit
    deployment = None
    try:
        key = AccountKey("ctp", "simnow", "simnow", "00001", "09999")
        scope = key.account_key_id
        settings = CtpGatewayTradingSettings(
            key,
            scope,
            root / "guard",
            "owner",
            root / "journal.sqlite",
            journal_id,
            0,
            root / "flow",
            "source",
            "entitlement",
            write_enabled=True,
            maintenance_interval_seconds=0.05,
        )
        cfg = _service_config(endpoint).to_public_dict()
        cfg["components"]["gateway"]["settings"]["options"]["dispatch_enabled"] = True
        if read_control is not None:
            cfg["components"]["gateway"]["settings"]["bind_ports"].append("service.account_read")
        config = parse_service_config(cfg)
        operations = frozenset(
            {"direct.reserve", "direct.dispatch", "direct.release", "direct.command_status"}
        )
        resource = ResourceGrant(
            "gateway-a",
            "service.direct_write",
            "scope-a",
            WireScope(DOMAIN, account_ref=scope, strategy_ref=CLIENT_A.actor_ref),
            operations,
        )
        account_resources = ()
        if read_control is not None:
            account_resources = (
                ResourceGrant(
                    "gateway-a",
                    "service.account_read",
                    "scope-a",
                    WireScope(DOMAIN, account_ref=scope, strategy_ref=CLIENT_A.actor_ref),
                    module._READ_OPERATIONS,
                ),
            )
        authority = TrustedServiceAuthority(
            identity=SERVICE,
            scope=WireScope(DOMAIN),
            scope_ref="scope-a",
            scope_version="scope-v1",
            security_ref="security-a",
            security_version="security-v1",
            registration_policy_ref="direct-policy-v1",
            registration_policy_version="direct-policy-v1",
            service_requirements_ref="direct-requirements-v1",
            service_requirements_version="direct-requirements-v1",
            curve_public_key=server_keys[0],
            curve_secret_key=server_keys[1],
            principals={client_public: CLIENT_A.principal_ref},
            grants={
                CLIENT_A.principal_ref: (PrincipalGrant(CLIENT_A, (resource, *account_resources)),)
            },
        )
        read_settings = (
            None
            if read_control is None
            else module.CtpGatewayAccountReadSettings(
                root / "account.sqlite",
                "account-state",
                (resource_key,),
                root / "identity.sqlite",
                issuer_id,
                (MarketInstrumentKey("ctp", "SHFE", "simnow", "future", "rb2612", "rb2612"),),
                refresh_interval_seconds=0.2,
                max_staleness_seconds=2,
                request_timeout_seconds=2,
            )
        )
        profile = module.build_ctp_gateway_trading_profile(
            config,
            authority,
            root / "registry.sqlite",
            profile_ref="synthetic-ctp",
            settings=settings,
            grants={
                CLIENT_A.principal_ref: GatewayDirectGrant(CLIENT_A.actor_ref, frozenset({"rb"}))
            },
            request_feed_factory=lambda: None,
            market_stream_factory=lambda *_: None,
            assert_admitted=lambda _: True,
            account_read_settings=read_settings,
            account_read_principals=frozenset({CLIENT_A.principal_ref})
            if read_settings
            else frozenset(),
        )
        deployment = profile.deployment_factory()
        deployment.start()
        events.put(("ready", os.getpid(), threading.get_ident()))
        running = True
        while running:
            try:
                if controls.get_nowait() == "stop":
                    running = False
            except queue.Empty:
                pass
            deployment.serve_once(10)
    except BaseException as error:
        events.put(("error", type(error).__name__, str(getattr(error, "code", ""))))
    finally:
        try:
            if deployment is not None and deployment.state in {"started", "close_failed"}:
                deployment.close()
            events.put(("closed", os.getpid()))
        except BaseException as error:
            events.put(("cleanup_error", type(error).__name__))


def test_spawned_profile_host_curve_client_dispatches_one_original_key(tmp_path):
    key = AccountKey("ctp", "simnow", "simnow", "00001", "09999")
    account_scope = key.account_key_id
    (tmp_path / "guard").mkdir()
    (tmp_path / "flow").mkdir(mode=0o700)
    journal = GatewayDirectCommandJournal.create(
        tmp_path / "journal.sqlite", account_scope, generation="provisioned"
    )
    endpoint = _free_endpoint()
    server_keys = zmq.curve_keypair()
    client_keys = zmq.curve_keypair()
    ctx = multiprocessing.get_context("spawn")
    events, controls = ctx.Queue(), ctx.Queue()
    child = ctx.Process(
        target=_server,
        args=(
            str(tmp_path),
            endpoint,
            server_keys,
            client_keys[0],
            journal.journal_id,
            events,
            controls,
        ),
    )
    child.start()
    runtime = None
    try:
        first, second = events.get(timeout=15), events.get(timeout=15)
        seen = {first[0]: first, second[0]: second}
        assert set(seen) == {"ready", "observed"}, (first, second)
        assert seen["ready"][1] == child.pid != os.getpid()
        owner_tid = seen["observed"][2]
        assert owner_tid != seen["ready"][2]
        cfg = _client_config(endpoint, CLIENT_A).to_public_dict()
        cfg["clients"]["gateway"]["settings"]["bindings"][0]["account_ref"] = account_scope
        config = parse_client_config(cfg)
        runtime = ThreadedServiceClientRuntime(
            lambda: ServiceClientRuntime(
                config,
                identity=CLIENT_A,
                scope_resolver=lambda _: WireScope(DOMAIN, account_ref=account_scope),
                peer_resolver=lambda _: SERVICE,
                transport_factory=ZmqTransportFactory(
                    {"security-a": ClientCurveKeys(server_keys[0], *client_keys)}
                ),
                run_id="run-a",
            )
        )
        runtime.start(timeout_seconds=10)
        binding = GatewayCommandBinding(
            journal.journal_id, account_scope, CLIENT_A.actor_ref, CLIENT_A.principal_ref
        )
        route = GatewayClientRoute(
            "broker.direct_write",
            SERVICE.actor_ref,
            "gateway-a",
            "scope-a",
            WireScope(
                DOMAIN, account_ref=account_scope, strategy_ref=CLIENT_A.actor_ref, run_id="run-a"
            ),
            CLIENT_A,
        )
        store = _TestSqliteHost(tmp_path / "client.sqlite", binding)
        client = GatewayDirectClient(runtime, binding=binding, route=route, host=store)
        request = OrderRequest.from_dict(_order(key="same-key", account=account_scope))
        reserved = client.reserve(request, ttl_seconds=30, timeout_seconds=10)
        assert reserved.outcome == "OBSERVED" and reserved.record.state == "RESERVED"
        sent = client.dispatch("same-key", timeout_seconds=10)
        assert sent.outcome == "QUEUED"
        send_event = events.get(timeout=10)
        assert send_event == ("sent", child.pid, owner_tid, "same-key")
        until = time.monotonic() + 10
        while True:
            current = client.status("same-key", timeout_seconds=10)
            if current.outcome == "OBSERVED" and current.record.state == "SENT":
                break
            assert time.monotonic() < until
            time.sleep(0.02)
        assert current.record.reservation_id == reserved.record.reservation_id
        assert client.dispatch("same-key", timeout_seconds=10).outcome != "QUEUED"
        with pytest.raises(queue.Empty):
            events.get(timeout=0.2)
    finally:
        try:
            if runtime is not None:
                runtime.close(timeout_seconds=10)
        finally:
            controls.put("stop")
            child.join(15)
            if child.is_alive():
                child.terminate()
                child.join(5)
                pytest.fail("spawned synthetic Gateway did not stop")
            assert child.exitcode == 0
            assert events.get(timeout=3) == ("closed", child.pid)


def test_dual_port_read_uses_owner_and_slow_query_does_not_block_status(tmp_path):
    from bt_api_base.account_protocol import AccountResourceKey

    from bt_api_py.gateway_direct_service import ACCOUNT_READ_OPERATIONS
    from bt_api_py.order_correlation_issuer import DurableOrderCorrelationIssuer
    from bt_api_py.service_account import ServiceAccountClient, ServiceAccountError
    from bt_api_py.service_runtime import ServiceCallOutcomeUnknownError

    key = AccountKey("ctp", "simnow", "simnow", "00001", "09999")
    scope = key.account_key_id
    (tmp_path / "guard").mkdir()
    (tmp_path / "flow").mkdir(mode=0o700)
    journal = GatewayDirectCommandJournal.create(
        tmp_path / "journal.sqlite", scope, generation="old"
    )
    issuer = DurableOrderCorrelationIssuer(tmp_path / "identity.sqlite")
    issuer_id = issuer.issuer_id
    issuer.close()
    endpoint = _free_endpoint()
    server_keys, client_keys = zmq.curve_keypair(), zmq.curve_keypair()
    context = multiprocessing.get_context("spawn")
    events, controls = context.Queue(), context.Queue()
    should_block, blocked, resume = context.Event(), context.Event(), context.Event()
    child = context.Process(
        target=_server,
        args=(
            str(tmp_path),
            endpoint,
            server_keys,
            client_keys[0],
            journal.journal_id,
            events,
            controls,
            (should_block, blocked, resume, issuer_id),
        ),
    )
    child.start()
    runtime = read_runtime = account_client = None
    try:
        observed = {}
        for _ in range(3):
            event = events.get(timeout=15)
            assert event[0] not in {"error", "cleanup_error", "closed"}, event
            observed[event[0]] = event
        assert set(observed) == {"ready", "observed", "read"}
        assert observed["read"][2] == observed["observed"][2]
        assert observed["read"][2] != observed["ready"][2]
        cfg = _client_config(endpoint, CLIENT_A).to_public_dict()
        binding = cfg["clients"]["gateway"]["settings"]["bindings"][0]
        binding["account_ref"] = scope
        account_binding = dict(binding)
        account_binding["mount"] = "store.account"
        cfg["clients"]["gateway"]["settings"]["bindings"].append(account_binding)
        config = parse_client_config(cfg)
        runtime = ThreadedServiceClientRuntime(
            lambda: ServiceClientRuntime(
                config,
                identity=CLIENT_A,
                scope_resolver=lambda _: WireScope(DOMAIN, account_ref=scope),
                peer_resolver=lambda _: SERVICE,
                transport_factory=ZmqTransportFactory(
                    {"security-a": ClientCurveKeys(server_keys[0], *client_keys)}
                ),
                run_id="run-a",
                requested_operations={
                    "broker.direct_write": frozenset(
                        {
                            "direct.reserve",
                            "direct.dispatch",
                            "direct.release",
                            "direct.command_status",
                        }
                    ),
                    "store.account": ACCOUNT_READ_OPERATIONS,
                },
            )
        )
        runtime.start(timeout_seconds=10)
        deadline = time.monotonic() + 10
        while True:
            try:
                typed = runtime.call(
                    "store.account", "account.snapshot.typed", {}, timeout_seconds=3
                )
                break
            except ServiceCallOutcomeUnknownError:
                assert time.monotonic() < deadline
                time.sleep(0.02)
        assert typed["snapshot"]["payload"]["schema_id"] == "sdk.account.state.typed"
        read_cfg = _client_config(endpoint, CLIENT_A).to_public_dict()
        read_binding = read_cfg["clients"]["gateway"]["settings"]["bindings"][0]
        read_binding["account_ref"], read_binding["mount"] = scope, "store.account"
        read_runtime = ServiceClientRuntime(
            parse_client_config(read_cfg),
            identity=CLIENT_A,
            scope_resolver=lambda _: WireScope(DOMAIN, account_ref=scope),
            peer_resolver=lambda _: SERVICE,
            transport_factory=ZmqTransportFactory(
                {"security-a": ClientCurveKeys(server_keys[0], *client_keys)}
            ),
            run_id="run-a",
            requested_operations={"store.account": ACCOUNT_READ_OPERATIONS},
        )
        read_runtime.start()
        account_client = ServiceAccountClient(
            read_runtime,
            "store.account",
            account_ref=scope,
            expected_account=key,
            required_coverage=(AccountResourceKey("future", "cross"),),
            expected_scope_ref="scope-a",
            require_open_orders=True,
        )
        view = account_client.refresh()
        assert view.observation.open_orders_complete and len(view.observation.open_orders) == 1
        assert view.observation.open_orders[0].order_id.startswith("sdk-order-observation-v1-")
        assert view.observation.open_orders[0].client_order_id is None
        reserve = runtime.call(
            "broker.direct_write",
            "direct.reserve",
            {
                "request_key": "key",
                "payload": _order(key="key", account=scope),
                "ttl_seconds": 30,
            },
            timeout_seconds=3,
        )
        assert reserve["record"]["state"] == "RESERVED"
        command_binding = GatewayCommandBinding(
            journal.journal_id, scope, CLIENT_A.actor_ref, CLIENT_A.principal_ref
        )
        route = GatewayClientRoute(
            "broker.direct_write",
            SERVICE.actor_ref,
            "gateway-a",
            "scope-a",
            WireScope(DOMAIN, account_ref=scope, strategy_ref=CLIENT_A.actor_ref, run_id="run-a"),
            CLIENT_A,
        )
        client = GatewayDirectClient(
            runtime,
            binding=command_binding,
            route=route,
            host=_TestSqliteHost(tmp_path / "dual-client.sqlite", command_binding),
        )
        assert (
            client.reserve(OrderRequest.from_dict(_order(key="send", account=scope))).outcome
            == "OBSERVED"
        )
        assert client.dispatch("send").outcome == "QUEUED"
        sent = events.get(timeout=5)
        assert sent[0] == "sent" and sent[1] == child.pid and sent[3] == "send"
        assert sent[2] == observed["read"][2]
        should_block.set()
        assert blocked.wait(5)
        with pytest.raises(ServiceCallOutcomeUnknownError):
            runtime.call("store.account", "account.snapshot.typed", {}, timeout_seconds=3)
        with pytest.raises(ServiceAccountError):
            account_client.refresh()
        result = runtime.call(
            "broker.direct_write",
            "direct.command_status",
            {"request_key": "key"},
            timeout_seconds=3,
        )
        assert result["record"]["state"] == "RESERVED"
    finally:
        resume.set()
        try:
            if account_client is not None:
                account_client.close()
            if read_runtime is not None:
                read_runtime.close()
            if runtime is not None:
                runtime.close(timeout_seconds=10)
        finally:
            controls.put("stop")
            child.join(15)
            if child.is_alive():
                child.terminate()
                child.join(5)
                pytest.fail("spawned synthetic Gateway did not stop")
            assert child.exitcode == 0
            assert events.get(timeout=3) == ("closed", child.pid)


def _real_ctp_read_server(
    root, endpoint, server_keys, client_public, journal_id, issuer_id, events, controls
):
    import importlib
    import sys

    from bt_api_base.account_protocol import AccountResourceKey
    from bt_api_base.market_protocol import MarketInstrumentKey
    from bt_api_ctp.ctp.client import CtpNativeStopReceipt, TraderClient
    from bt_api_ctp.feeds.live_ctp_feed import CtpRequestDataFuture

    import bt_api_py.ctp_gateway_trading as module
    from bt_api_py.gateway_direct_service import GatewayDirectGrant

    root = Path(root)
    ctp_tests = Path(__file__).resolve().parents[2] / "bt_api" / "bt_api_ctp" / "tests"
    sys.path.insert(0, str(ctp_tests))
    helper = importlib.import_module("test_iter62_settlement_readback_on_write")
    key = AccountKey("ctp", "simnow", "simnow", "account", "9999")
    scope = key.account_key_id
    coverage = (AccountResourceKey("future", "futures"),)
    instrument = MarketInstrumentKey("ctp", "CFFEX", "simnow", "future", "IF2612", "IF2612")
    starts, queries = [], []

    def start(trader, **_kwargs):
        starts.append(trader)
        native = helper._NativeApi()
        native.client = trader
        trader._api = native
        helper._login(trader, native, session_id=19)
        account = {
            "AccountID": "account",
            "BrokerID": "9999",
            "CurrencyID": "CNY",
            "Balance": 1000,
            "Available": 800,
        }
        order = {
            "BrokerID": "9999",
            "InvestorID": "account",
            "InstrumentID": "IF2612",
            "ExchangeID": "CFFEX",
            "TradingDay": helper.TODAY,
            "OrderSysID": "SYS1",
            "OrderStatus": "1",
            "Direction": "0",
            "CombOffsetFlag": "0",
            "VolumeTotalOriginal": 3,
            "VolumeTraded": 1,
            "OrderPriceType": "2",
            "ContingentCondition": "1",
            "LimitPrice": 4001,
            "InsertDate": helper.TODAY,
            "InsertTime": "00:00:00",
        }

        def responder(kind, rows):
            def submit(_field, request_id):
                queries.append((kind, threading.get_ident()))
                for row in rows:
                    trader._handle_query_callback(kind, row, None, request_id, False)
                trader._handle_query_callback(kind, None, None, request_id, True)
                return 0

            return submit

        native.ReqQryTradingAccount = responder("account", [account])
        native.ReqQryInvestorPosition = responder("positions", [])
        native.ReqQryOrder = responder("orders", [order])

    def stop(trader, *, timeout):
        return CtpNativeStopReceipt(trader.connection_generation, False, True, True, False, False)

    TraderClient.start = start
    TraderClient.stop_and_wait = stop

    def feed():
        return CtpRequestDataFuture(
            None,
            broker_id="9999",
            user_id="account",
            password="synthetic",
            td_front="tcp://offline-neutral-td.invalid:30001",
            md_front="tcp://offline-neutral-md.invalid:30011",
            auto_settlement_confirm=False,
            ctp_env_profile="config_front_pair",
            ctp_environment="simnow",
            trusted_flow_root=str(root / "flow"),
            flow_account_scope=scope,
        )

    cfg = _service_config(endpoint).to_public_dict()
    cfg["components"]["gateway"]["settings"]["options"]["dispatch_enabled"] = True
    cfg["components"]["gateway"]["settings"]["bind_ports"].append("service.account_read")
    config = parse_service_config(cfg)
    wire_scope = WireScope(DOMAIN, account_ref=scope, strategy_ref=CLIENT_A.actor_ref)
    direct_ops = frozenset(
        {"direct.reserve", "direct.dispatch", "direct.release", "direct.command_status"}
    )
    authority = TrustedServiceAuthority(
        identity=SERVICE,
        scope=WireScope(DOMAIN),
        scope_ref="scope-a",
        scope_version="scope-v1",
        security_ref="security-a",
        security_version="security-v1",
        registration_policy_ref="direct-policy-v1",
        registration_policy_version="direct-policy-v1",
        service_requirements_ref="direct-requirements-v1",
        service_requirements_version="direct-requirements-v1",
        curve_public_key=server_keys[0],
        curve_secret_key=server_keys[1],
        principals={client_public: CLIENT_A.principal_ref},
        grants={
            CLIENT_A.principal_ref: (
                PrincipalGrant(
                    CLIENT_A,
                    (
                        ResourceGrant(
                            "gateway-a", "service.direct_write", "scope-a", wire_scope, direct_ops
                        ),
                        ResourceGrant(
                            "gateway-a",
                            "service.account_read",
                            "scope-a",
                            wire_scope,
                            module._READ_OPERATIONS,
                        ),
                    ),
                ),
            )
        },
    )
    settings = CtpGatewayTradingSettings(
        key,
        scope,
        root / "guard",
        "owner",
        root / "journal.sqlite",
        journal_id,
        0,
        root / "flow",
        "source",
        "entitlement",
        maintenance_interval_seconds=0.05,
    )
    read = module.CtpGatewayAccountReadSettings(
        root / "account.sqlite",
        "account-state",
        coverage,
        root / "identity.sqlite",
        issuer_id,
        (instrument,),
        refresh_interval_seconds=0.2,
        max_staleness_seconds=2,
        request_timeout_seconds=2,
    )
    deployment = None
    try:
        profile = module.build_ctp_gateway_trading_profile(
            config,
            authority,
            root / "registry.sqlite",
            profile_ref="synthetic-native",
            settings=settings,
            grants={
                CLIENT_A.principal_ref: GatewayDirectGrant(
                    CLIENT_A.actor_ref, frozenset({"IF2612"})
                )
            },
            request_feed_factory=feed,
            market_stream_factory=lambda *_: None,
            assert_admitted=lambda _: True,
            account_read_settings=read,
            account_read_principals=frozenset({CLIENT_A.principal_ref}),
        )
        deployment = profile.deployment_factory()
        deployment.start()
        events.put(("ready", os.getpid(), threading.get_ident()))
        while True:
            try:
                if controls.get_nowait() == "stop":
                    break
            except queue.Empty:
                pass
            deployment.serve_once(10)
    except BaseException as error:
        events.put(("error", type(error).__name__, str(getattr(error, "code", ""))))
    finally:
        try:
            if deployment is not None and deployment.state in {"started", "close_failed"}:
                deployment.close()
            events.put(("closed", os.getpid(), len(starts), tuple(queries)))
        except BaseException as error:
            events.put(("cleanup_error", type(error).__name__))


def test_real_ctp_active_order_reaches_service_account_client_over_curve(tmp_path):
    from bt_api_base.account_protocol import AccountResourceKey

    from bt_api_py.gateway_direct_service import ACCOUNT_READ_OPERATIONS
    from bt_api_py.order_correlation_issuer import DurableOrderCorrelationIssuer
    from bt_api_py.service_account import ServiceAccountClient, ServiceAccountError
    from bt_api_py.service_runtime import ServiceCallOutcomeUnknownError, ServiceRuntimeError

    key = AccountKey("ctp", "simnow", "simnow", "account", "9999")
    scope = key.account_key_id
    (tmp_path / "guard").mkdir()
    (tmp_path / "flow").mkdir(mode=0o700)
    journal = GatewayDirectCommandJournal.create(
        tmp_path / "journal.sqlite", scope, generation="old"
    )
    issuer = DurableOrderCorrelationIssuer(tmp_path / "identity.sqlite")
    issuer_id = issuer.issuer_id
    issuer.close()
    server_keys, client_keys = zmq.curve_keypair(), zmq.curve_keypair()
    endpoint = _free_endpoint()
    context = multiprocessing.get_context("spawn")
    events, controls = context.Queue(), context.Queue()
    child = context.Process(
        target=_real_ctp_read_server,
        args=(
            str(tmp_path),
            endpoint,
            server_keys,
            client_keys[0],
            journal.journal_id,
            issuer_id,
            events,
            controls,
        ),
    )
    child.start()
    runtime = account_client = None
    try:
        ready = events.get(timeout=15)
        assert ready[0] == "ready", ready
        config = _client_config(endpoint, CLIENT_A).to_public_dict()
        binding = config["clients"]["gateway"]["settings"]["bindings"][0]
        binding["account_ref"], binding["mount"] = scope, "store.account"
        runtime = ServiceClientRuntime(
            parse_client_config(config),
            identity=CLIENT_A,
            scope_resolver=lambda _: WireScope(DOMAIN, account_ref=scope),
            peer_resolver=lambda _: SERVICE,
            transport_factory=ZmqTransportFactory(
                {"security-a": ClientCurveKeys(server_keys[0], *client_keys)}
            ),
            run_id="run-a",
            requested_operations={"store.account": ACCOUNT_READ_OPERATIONS},
        )
        runtime.start()
        account_client = ServiceAccountClient(
            runtime,
            "store.account",
            account_ref=scope,
            expected_account=key,
            required_coverage=(AccountResourceKey("future", "futures"),),
            require_open_orders=True,
            expected_scope_ref="scope-a",
        )
        deadline = time.monotonic() + 10
        while True:
            try:
                view = account_client.refresh()
                break
            except ServiceAccountError:
                assert time.monotonic() < deadline
                time.sleep(0.02)
        assert view.observation.open_orders_complete and len(view.observation.open_orders) == 1
        order = view.observation.open_orders[0]
        assert (
            order.order_id.startswith("sdk-order-observation-v1-") and order.client_order_id is None
        )
        assert order.instrument.instrument_id == "IF2612" and order.quantity == "3"
        assert order.filled_quantity == "1" and "SYS1" not in repr(view.observation.to_dict())
        with pytest.raises(ServiceCallOutcomeUnknownError):
            runtime.call("store.account", "account.head", {"stream": "execution"})
        with pytest.raises(ServiceRuntimeError):
            runtime.call("store.account", "account.snapshot.typed", {}, account_ref="other")
        with pytest.raises(ServiceRuntimeError):
            runtime.call("broker.direct_write", "direct.reserve", {})
    finally:
        try:
            if account_client is not None:
                account_client.close()
            if runtime is not None:
                runtime.close()
        finally:
            controls.put("stop")
            child.join(15)
            if child.is_alive():
                child.terminate()
                child.join(5)
                pytest.fail("spawned synthetic CTP Gateway did not stop")
            assert child.exitcode == 0
            closed = events.get(timeout=3)
            assert closed[:3] == ("closed", child.pid, 1)
            queries = closed[3]
            assert {kind for kind, _ in queries} == {"account", "positions", "orders"}
            assert len({owner for _, owner in queries}) == 1
            assert queries[0][1] != ready[2]
