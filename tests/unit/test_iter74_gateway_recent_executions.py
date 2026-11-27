"""End-to-end unit/loopback tests for Iteration 74.02 Gateway recent executions over real Registry + CURVE + Web."""

from __future__ import annotations

import asyncio
import queue
import socket
import threading

import httpx
import pytest

zmq = pytest.importorskip("zmq")

from bt_api_base.account_protocol import AccountKey
from bt_api_base.direct_write_protocol import (
    DirectExecutionEventKind,
    DirectOrderStatus,
    NativeOrderIdentity,
)
from bt_api_base.gateway_execution_protocol import GatewayReturnFact
from bt_api_base.market_protocol import MarketInstrumentKey
from bt_api_base.service_protocol import WireIdentity, WireRole, WireScope
from bt_api_gateway.account_read_model import GatewayAccountReadModel
from bt_api_gateway.direct_command_journal import GatewayDirectCommandJournal
from bt_api_gateway.direct_dispatch import GatewayDirectDispatcher
from bt_api_gateway.direct_execution_journal import GatewayDirectExecutionLedger
from bt_api_gateway.event_journal import GatewayEventJournal
from bt_api_gateway.router import GatewayCommandKind, GatewayPrincipal
from bt_api_gateway.service_component import GatewayReadService, GatewayServiceAccountScope
from bt_api_gateway.session_guard import LocalAccountSessionGuard
from bt_api_gateway.web.app import AuthenticatedWebSession, WebScope, create_app

from bt_api_py.ctp_gateway_local_profile import _OPERATOR_READ_OPERATIONS
from bt_api_py.gateway_account_recent_executions import (
    ACCOUNT_EXECUTION_HISTORY_OPERATIONS,
    GatewayAccountRecentExecutions,
)
from bt_api_py.gateway_direct_owner import GatewayDirectOwner, GatewayDirectOwnerResources
from bt_api_py.gateway_direct_service import (
    GatewayControlGrant,
    GatewayDirectGrant,
    GatewayDirectServiceBinding,
)
from bt_api_py.gateway_web_local_host import _ACCOUNT_OPERATIONS
from bt_api_py.service_components import ComponentResources
from bt_api_py.service_config import parse_client_config, parse_service_config
from bt_api_py.service_host import ServiceHost, TrustedServiceAuthority
from bt_api_py.service_registry import PrincipalGrant, ResourceGrant
from bt_api_py.service_runtime import ClientCurveKeys, ServiceClientRuntime, ZmqTransportFactory
from bt_api_py.service_runtime_thread import ThreadedServiceClientRuntime

ACCOUNT = "synthetic-acct-74"
DOMAIN = "paper:iter74"
SERVICE = WireIdentity(WireRole.SERVICE, "synthetic-gw", "synthetic-gw-svc")
RB = MarketInstrumentKey("ctp", "SHFE", "simnow", "futures", "rb2701", "rb2701")
CTRL_OPS = frozenset(
    {
        "direct.control.identity",
        "direct.control.list",
        "direct.control.status",
    }
)
ACCT_OPS = frozenset({"account.snapshot.typed", "account.executions.recent"})


def test_operation_registered_in_local_profile_and_web_host():
    assert "account.executions.recent" in ACCOUNT_EXECUTION_HISTORY_OPERATIONS
    assert "account.executions.recent" in _OPERATOR_READ_OPERATIONS
    assert "account.executions.recent" in _ACCOUNT_OPERATIONS


def test_real_curve_gateway_and_web_executions_cursor_free_paginated(tmp_path):
    with socket.socket() as probe:
        probe.bind(("127.0.0.1", 0))
        endpoint = f"tcp://127.0.0.1:{probe.getsockname()[1]}"

    server_keys = zmq.curve_keypair()
    op_keys = zmq.curve_keypair()
    op_identity = WireIdentity(WireRole.APPLICATION_CLIENT, "web-op", "web-op-app")
    op_scope = WireScope(DOMAIN, account_ref=ACCOUNT, strategy_ref=None)

    ready: queue.Queue = queue.Queue()
    stop = threading.Event()
    holder: dict = {}

    def serve():
        owner = host = None
        try:
            config = parse_service_config(
                {
                    "service_config_schema_version": 1,
                    "process_role": "service",
                    "service_id": SERVICE.actor_ref,
                    "scope_ref": "scope-a",
                    "transport_ref": "server",
                    "registration_policy_ref": "policy",
                    "service_requirements_ref": "requirements",
                    "components": {
                        "gateway": {
                            "config_schema_version": 2,
                            "settings": {
                                "instance_id": "gateway-a",
                                "bind_ports": ["service.direct_write", "service.account_read"],
                                "options": {
                                    "role": "trading_owner",
                                    "dispatch_enabled": True,
                                    "control_enabled": True,
                                },
                            },
                        }
                    },
                    "communication": {
                        "config_schema_version": 1,
                        "profiles": {
                            "server": {
                                "backend": "zmq",
                                "config_schema_version": 1,
                                "endpoints": {"rpc_bind": endpoint},
                                "security_profile_ref": "key-a",
                            }
                        },
                    },
                }
            )
            resources = (
                ResourceGrant("gateway-a", "service.direct_write", "scope-a", op_scope, CTRL_OPS),
                ResourceGrant("gateway-a", "service.account_read", "scope-a", op_scope, ACCT_OPS),
            )
            authority = TrustedServiceAuthority(
                identity=SERVICE,
                scope=WireScope(DOMAIN),
                scope_ref="scope-a",
                scope_version="v1",
                security_ref="key-a",
                security_version="v1",
                registration_policy_ref="policy",
                registration_policy_version="v1",
                service_requirements_ref="requirements",
                service_requirements_version="v1",
                curve_public_key=server_keys[0],
                curve_secret_key=server_keys[1],
                principals={op_keys[0]: "web-op"},
                grants={"web-op": (PrincipalGrant(op_identity, resources),)},
            )
            (tmp_path / "guard").mkdir()
            owned = []

            def factory():
                guard = LocalAccountSessionGuard(tmp_path / "guard", ACCOUNT, "owner-74")
                journal = GatewayDirectCommandJournal.create(
                    tmp_path / "journal.sqlite", ACCOUNT, generation=guard.acquire()
                )
                dispatcher = GatewayDirectDispatcher(
                    journal, "CTP", guard, lambda *_: None, lambda _: (), lambda _: True
                )
                ledger = GatewayDirectExecutionLedger(journal, guard)
                # Seed 1 order update + 2 trade fills on owner thread
                row = journal.reserve(
                    "strat-alpha",
                    "req-1",
                    "strat-p",
                    {"symbol": "rb2701", "side": "buy", "offset": "open", "quantity": "2"},
                )
                native = {
                    "exchange_name": "CTP",
                    "client_order_id": "req-1",
                    "session_generation": 1,
                    "fields_json": "{}",
                }
                journal.begin_dispatch(
                    "strat-alpha",
                    "req-1",
                    row.reservation_id,
                    row.digest,
                    "strat-p",
                    1,
                    native,
                    ("anchor-74",),
                )
                identity = NativeOrderIdentity("CTP", "ord-74", {"trading_day": "20261127"}, 1)
                assert (
                    ledger.ingest(
                        GatewayReturnFact(
                            ACCOUNT,
                            "CTP",
                            RB,
                            "20261127",
                            DirectExecutionEventKind.ORDER,
                            "src-1",
                            identity=identity,
                            identity_keys=tuple(sorted(("anchor-74", "SHFE:ord-74"))),
                            binding_keys=("anchor-74",),
                            venue_key="SHFE:ord-74",
                            status=DirectOrderStatus.ACCEPTED,
                            side="buy",
                            offset="open",
                            order_quantity="2",
                            filled_quantity="0",
                            remaining_quantity="2",
                            limit_price="3500",
                        )
                    ).disposition
                    == "ATTRIBUTED"
                )
                for idx, (price, tid, ttime) in enumerate(
                    [("3501", "trd-1", "09:01:00"), ("3502", "trd-2", "09:02:00")],
                    start=2,
                ):
                    assert (
                        ledger.ingest(
                            GatewayReturnFact(
                                ACCOUNT,
                                "CTP",
                                RB,
                                "20261127",
                                DirectExecutionEventKind.TRADE,
                                f"src-{idx}",
                                identity=identity,
                                identity_keys=("SHFE:ord-74",),
                                binding_keys=(),
                                venue_key="SHFE:ord-74",
                                trade_key=("SHFE", tid),
                                side="buy",
                                offset="open",
                                trade_quantity="1",
                                trade_price=price,
                                trade_time=ttime,
                            )
                        ).disposition
                        == "ATTRIBUTED"
                    )
                owned.append((journal, guard, ledger))
                return GatewayDirectOwnerResources(dispatcher, journal, guard.release)

            owner = GatewayDirectOwner(
                factory, queue_size=8, max_queue_wait_seconds=10, busy_timeout_seconds=10
            )
            assert owner.start(10)
            journal, guard, ledger = owned[0]
            control = owner.submit_service_call(lambda: journal.enable_controls(guard)).result(
                timeout_seconds=5
            )
            binding = GatewayDirectServiceBinding(
                journal,
                {"strat-p": GatewayDirectGrant("strat-alpha", frozenset({"rb2701"}))},
                owner=owner,
                control=control,
                control_grants={
                    "web-op": GatewayControlGrant("web-op-app", True, frozenset(), CTRL_OPS)
                },
            )
            read_journal = GatewayEventJournal(tmp_path / "account-read.sqlite")
            from bt_api_base.account_protocol import AccountResourceKey

            main = AccountResourceKey("future", "main")
            read_model = GatewayAccountReadModel(
                read_journal,
                account_key=AccountKey("synthetic", "SHFE", "simnow", ACCOUNT),
                journal_account_scope=ACCOUNT,
                state_scope="state-a",
                required_coverage=(main,),
                assert_current_generation=lambda _: None,
                max_staleness_seconds=30,
            )
            read_model.start_generation(journal.generation)
            account_scope_cfg = GatewayServiceAccountScope(
                account_ref=ACCOUNT,
                execution_domain_ref=DOMAIN,
                journal_account_scope=ACCOUNT,
                account_read_strategy_scope="state-a",
                shared_account_facts_scope=None,
                shared_fact_strategy_refs=frozenset(),
            )
            principals_map = {
                "web-op": GatewayPrincipal(
                    "web-op",
                    frozenset({ACCOUNT}),
                    frozenset({"state-a"}),
                    frozenset({GatewayCommandKind.READ}),
                )
            }
            read_service = GatewayReadService(
                read_journal,
                accounts={ACCOUNT: account_scope_cfg},
                principals=principals_map,
                state_worker=read_model,
            )
            recent_exec = GatewayAccountRecentExecutions(
                owner,
                journal,
                ledger,
                account_scope=ACCOUNT,
                principals=frozenset({"web-op"}),
            )
            values = {
                "binding": binding,
                "read_service": read_service,
                "account_recent_executions": recent_exec,
            }
            host = ServiceHost(
                config,
                authority,
                tmp_path / "registry.sqlite",
                lambda *_: ComponentResources.borrowed(values, ready=lambda: True),
            )
            host.start()
            holder["owner"] = owner
            holder["ledger"] = ledger
            ready.put(("ready",))
            while not stop.is_set():
                host.serve_once(10)
        except BaseException as exc:
            ready.put(("error", type(exc).__name__, str(exc)))
        finally:
            if host is not None and host.state == "started":
                host.close()
            if owner is not None:
                owner.close(10)

    thread = threading.Thread(target=serve, daemon=True)
    thread.start()
    started = ready.get(timeout=20)
    assert started == ("ready",), started

    client_config = parse_client_config(
        {
            "client_config_schema_version": 1,
            "process_role": "application_client",
            "registration": {"application_ref": op_identity.actor_ref},
            "clients": {
                "gateway": {
                    "config_schema_version": 1,
                    "settings": {
                        "bindings": [
                            {
                                "mount": "application.direct_write",
                                "service_id": SERVICE.actor_ref,
                                "instance_id": "gateway-a",
                                "scope_ref": "scope-a",
                                "account_ref": ACCOUNT,
                                "transport_ref": "client",
                            },
                            {
                                "mount": "application.account",
                                "service_id": SERVICE.actor_ref,
                                "instance_id": "gateway-a",
                                "scope_ref": "scope-a",
                                "account_ref": ACCOUNT,
                                "transport_ref": "client",
                            },
                        ],
                        "options": {"request_timeout_ms": 2000},
                    },
                }
            },
            "communication": {
                "config_schema_version": 1,
                "profiles": {
                    "client": {
                        "backend": "zmq",
                        "config_schema_version": 1,
                        "endpoints": {"rpc_connect": endpoint},
                        "security_profile_ref": "key-a",
                    }
                },
            },
        }
    )
    keys = ClientCurveKeys(server_keys[0], op_keys[0], op_keys[1])
    runtime = ThreadedServiceClientRuntime(
        lambda: ServiceClientRuntime(
            client_config,
            identity=op_identity,
            scope_resolver=lambda _: op_scope,
            peer_resolver=lambda _: SERVICE,
            transport_factory=ZmqTransportFactory({"key-a": keys}),
            requested_operations={
                "application.direct_write": CTRL_OPS,
                "application.account": ACCT_OPS,
            },
        )
    )
    try:
        assert runtime.start(timeout_seconds=10).healthy
        direct_reply = runtime.call(
            "application.account",
            "account.executions.recent",
            {"limit": 2},
            account_ref=ACCOUNT,
            scope_ref="scope-a",
            timeout_seconds=5,
        )
        assert direct_reply["status"] == "OBSERVED", direct_reply
        origin = "http://127.0.0.1:18080"
        session = AuthenticatedWebSession(
            "web-op",
            "web-op-app",
            "c" * 40,
            (WebScope("desk", ACCOUNT, "scope-a", None),),
        )
        app = create_app(
            origin=origin,
            authenticate=lambda _req: session,
            resolve_runtime=lambda _sess, _scope: runtime,
            receipt_key=b"k" * 32,
            receipt_key_id="test-v1",
        )

        async def _verify():
            transport = httpx.ASGITransport(app=app, client=("127.0.0.1", 1234))
            async with httpx.AsyncClient(transport=transport, base_url=origin) as client:
                r1 = await client.get("/api/v1/scopes/desk/executions?limit=2")
                assert r1.status_code == 200, r1.text
                body1 = r1.json()
                assert body1["status"] == "OBSERVED", body1
                assert body1["view"] == "executions"
                assert body1["has_more"] is True
                assert body1["next_before_sequence"] == 2
                assert [row["sequence"] for row in body1["executions"]] == [3, 2]
                assert body1["executions"][0]["kind"] == "trade"
                assert body1["executions"][0]["price"] == "3502"
                assert body1["executions"][0]["strategy"] == "strat-alpha"
                assert body1["executions"][0]["trade_ref"] == "SHFE:trd-2"

                r2 = await client.get(
                    f"/api/v1/scopes/desk/executions?limit=2&before_sequence={body1['next_before_sequence']}"
                )
                assert r2.status_code == 200, r2.text
                body2 = r2.json()
                assert body2["status"] == "OBSERVED", body2
                assert body2["has_more"] is False
                assert body2["next_before_sequence"] is None
                assert len(body2["executions"]) == 1
                assert body2["executions"][0]["sequence"] == 1
                assert body2["executions"][0]["kind"] == "order"
                assert body2["executions"][0]["status"] == "accepted"

        asyncio.run(_verify())

        # Confirm zero consumer rows touched on the owner thread!
        def _count_consumers():
            with holder["ledger"]._read() as db:
                return db.execute("SELECT COUNT(*) FROM direct_execution_consumers").fetchone()[0]

        assert holder["owner"].submit_service_call(_count_consumers).result(timeout_seconds=5) == 0
    finally:
        runtime.close(timeout_seconds=10)
        stop.set()
        thread.join(15)
        assert not thread.is_alive()
