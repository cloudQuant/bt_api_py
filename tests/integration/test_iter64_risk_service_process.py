"""The Risk service process between a strategy and a spawned Gateway (iteration 64, 64.09).

Three processes over real CURVE: a spawned loopback Gateway (synthetic native
dispatcher, synthetic venue returns, delegation granted to the Risk principal
for strategy A, account observer and a ``risk`` control grant), the Risk host
started through its CLI ``python -m bt_api_py.risk_service_host run`` in a
second process, and the strategy in this test process. INTEGRATION_SIM
evidence only: temporary SQLite files, no provider session, no account.

The Gateway fixture has no market hub and no market dispatch gate
(``market_gate_required`` False); the trading day reaches Risk through the
account observation stream (a strategy's own direct order seen first).
"""

from __future__ import annotations

import json
import multiprocessing
import os
import queue
import sqlite3
import subprocess
import sys
import time
from decimal import Decimal
from pathlib import Path

import zmq
from bt_api_base.service_protocol import WireIdentity, WireRole, WireScope

ACCOUNT, DOMAIN, RUN = "account-a", "paper:demo", "strategy-run"
TRADING_DAY = "20300101"
GATEWAY = WireIdentity(WireRole.SERVICE, "gateway-principal", "gateway-service")
STRATEGY = WireIdentity(WireRole.STRATEGY_CLIENT, "principal-a", "strategy-a")
RISK = WireIdentity(WireRole.SERVICE, "risk-principal", "risk-service-delegate")
CONTROL = WireIdentity(WireRole.APPLICATION_CLIENT, "risk-control", "risk-control-actor")
OPERATOR = WireIdentity(WireRole.APPLICATION_CLIENT, "risk-operator", "risk-operator-actor")
RISK_HOST = WireIdentity(WireRole.SERVICE, "risk-host", "risk-service")
SCOPE = WireScope(DOMAIN, account_ref=ACCOUNT, strategy_ref=STRATEGY.actor_ref, run_id=RUN)
ACCOUNT_ONLY = WireScope(DOMAIN, account_ref=ACCOUNT)
SDK_ROOT = Path(__file__).resolve().parents[2]
CONTROL_OPS = frozenset(
    {
        "direct.control.apply",
        "direct.control.status",
        "direct.control.list",
        "direct.control.cancel_scope",
        "direct.control.cancel_scope_status",
    }
)


def _instrument():
    from bt_api_base.market_protocol import MarketInstrumentKey

    return MarketInstrumentKey("ctp", "SHFE", "simnow", "future", "rb", "rb")


def _gateway(root, endpoint, server_keys, publics, messages, controls):
    """Loopback Gateway (adapted from the 63.13 delegate fixture) in its own process."""
    owner = host = None
    try:
        from bt_api_base.direct_cancel_protocol import PreparedCancelRequest
        from bt_api_base.direct_write_protocol import (
            DirectExecutionEventKind,
            DirectOrderStatus,
            NativeOrderIdentity,
            PreDispatchRecord,
            WriteOperation,
        )
        from bt_api_base.gateway_execution_protocol import GatewayReturnFact
        from bt_api_gateway.direct_cancel_dispatch import GatewayDirectCancelDispatcher
        from bt_api_gateway.direct_cancel_journal import GatewayDirectCancelJournal
        from bt_api_gateway.direct_command_journal import GatewayDirectCommandJournal
        from bt_api_gateway.direct_control import GatewayDirectControl
        from bt_api_gateway.direct_delegation import GatewayDelegateGrant
        from bt_api_gateway.direct_dispatch import GatewayDirectDispatcher, NativeDispatchOutcome
        from bt_api_gateway.direct_execution_journal import GatewayDirectExecutionLedger
        from bt_api_gateway.session_guard import LocalAccountSessionGuard

        from bt_api_py.gateway_account_observer import (
            ACCOUNT_OBSERVER_OPERATIONS,
            GatewayAccountObserver,
        )
        from bt_api_py.gateway_delegate_actor_service import OPERATIONS as ACTOR_OPERATIONS
        from bt_api_py.gateway_delegate_actor_service import GatewayDelegateActorService
        from bt_api_py.gateway_delegation_service import (
            GatewayDelegationService,
            GatewayDelegationSettings,
        )
        from bt_api_py.gateway_direct_owner import GatewayDirectOwner, GatewayDirectOwnerResources
        from bt_api_py.gateway_direct_service import (
            EVENT_OPERATIONS,
            GatewayControlGrant,
            GatewayDirectGrant,
            GatewayDirectServiceBinding,
        )
        from bt_api_py.gateway_execution_stream_service import GatewayExecutionStreamService
        from bt_api_py.service_components import ComponentResources
        from bt_api_py.service_config import parse_service_config
        from bt_api_py.service_host import ServiceHost, TrustedServiceAuthority
        from bt_api_py.service_registry import PrincipalGrant, ResourceGrant
        from tests.integration.test_iter63_delegate_risk_execution_loopback import _read_service
        from tests.integration.test_iter63_gateway_direct_service import _service_config

        instrument = _instrument()
        root = Path(root)
        (root / "guard").mkdir()
        holder = {}

        def factory():
            guard = LocalAccountSessionGuard(root / "guard", ACCOUNT, "owner")
            journal = GatewayDirectCommandJournal.create(
                root / "journal.sqlite", ACCOUNT, generation=guard.acquire()
            )
            journal.enable_controls(guard)
            journal.enable_strategy_ledger(guard)
            journal.enable_account_extensions(guard)
            holder["control"] = GatewayDirectControl(journal, guard)
            holder["ledger"] = GatewayDirectExecutionLedger(journal, guard)

            def place(payload, port, account):
                identity = NativeOrderIdentity(
                    "CTP", payload["client_order_id"], {"trading_day": TRADING_DAY}, 1
                )
                marker = port.commit(
                    PreDispatchRecord(
                        WriteOperation.PLACE, account, "CTP", payload["symbol"], identity, None,
                        payload, TRADING_DAY, 1, "synthetic",
                    )
                )
                return NativeDispatchOutcome(True, identity, marker)

            from iter63_claim_support import SyntheticClaimPort, claiming

            dispatcher = GatewayDirectDispatcher(
                journal, "CTP", guard, claiming(place),
                lambda identity: ("native-" + identity.client_order_id,), lambda _: True,
                claim_port=SyntheticClaimPort(),
            )

            def cancel(target, symbol, port, account):
                prepared = PreparedCancelRequest(
                    "cancel-" + target.client_order_id, target, symbol, TRADING_DAY, 1,
                    {"request_id": 12}, ("cancel-" + target.client_order_id,),
                )
                marker = port.commit(
                    PreDispatchRecord(
                        WriteOperation.CANCEL, account, "CTP", symbol, None, target, {},
                        prepared.trading_day, prepared.session_generation, "synthetic", prepared,
                    )
                )
                return NativeDispatchOutcome(True, target, marker)

            cancel_dispatcher = GatewayDirectCancelDispatcher(
                GatewayDirectCancelJournal(journal), dispatcher, guard, "CTP", cancel, lambda _: True
            )
            holder["journal"] = journal
            return GatewayDirectOwnerResources(
                dispatcher, journal, guard.release, cancel_dispatcher=cancel_dispatcher
            )

        owner = GatewayDirectOwner(
            factory, queue_size=8, max_queue_wait_seconds=5, busy_timeout_seconds=5
        )
        assert owner.start(10)
        journal, ledger = holder["journal"], holder["ledger"]
        stream = GatewayExecutionStreamService(
            ledger, journal, {STRATEGY.principal_ref: STRATEGY.actor_ref},
            execution_domain_ref=DOMAIN,
        )
        binding = GatewayDirectServiceBinding(
            journal,
            {STRATEGY.principal_ref: GatewayDirectGrant(STRATEGY.actor_ref, frozenset({"rb"}))},
            owner=owner,
            cancel_enabled=True,
            execution_stream=stream,
            execution_domain_ref=DOMAIN,
            control=holder["control"],
            control_grants={
                CONTROL.principal_ref: GatewayControlGrant(
                    CONTROL.actor_ref, True, frozenset(), CONTROL_OPS, kind="risk",
                    modes=frozenset({"STOP_NEW", "FREEZE"}), instrument_scope=True,
                )
            },
        )
        logical = frozenset(
            {"direct.reserve", "direct.dispatch", "direct.command_status", "direct.cancel",
             "direct.cancel_status", "direct.release"}
        )
        delegation = GatewayDelegationService(
            GatewayDelegationSettings(
                (GatewayDelegateGrant(RISK, ACCOUNT, STRATEGY.actor_ref, DOMAIN, "risk",
                                      "risk-v1", frozenset({instrument}), logical),),
                lambda value: value == instrument,
            ),
            owner=owner, journal=journal, instance_id="gateway-a", scope_ref="scope-a",
        )
        actor = GatewayDelegateActorService(delegation)
        observers = frozenset({RISK.principal_ref})
        observer = GatewayAccountObserver(
            owner, journal, ledger, account_scope=ACCOUNT, execution_domain_ref=DOMAIN,
            principals=observers,
        )
        read_service = _read_service(root, observers)
        from bt_api_py.gateway_account_comparison import GatewayAccountComparison

        comparison = GatewayAccountComparison(
            owner, journal, account_scope=ACCOUNT, principals=frozenset(),
            instruments=frozenset({instrument}),
        )
        template = WireScope(DOMAIN, account_ref=ACCOUNT, strategy_ref=STRATEGY.actor_ref)
        strategy_ops = frozenset(
            {"direct.reserve", "direct.dispatch", "direct.command_status",
             "direct.delegated_command_status", "direct.delegated_cancel",
             "direct.delegated_cancel_status"}
        ) | EVENT_OPERATIONS
        delegate_ops = frozenset({"direct.delegated_reserve", "direct.delegated_dispatch"}) | ACTOR_OPERATIONS

        def direct(operations, scope=template):
            return ResourceGrant("gateway-a", "service.direct_write", "scope-a", scope, operations)

        observe = ResourceGrant(
            "gateway-a", "service.account_read", "scope-a", ACCOUNT_ONLY, ACCOUNT_OBSERVER_OPERATIONS
        )
        grants = {
            STRATEGY.principal_ref: (PrincipalGrant(STRATEGY, (direct(strategy_ops),)),),
            RISK.principal_ref: (PrincipalGrant(RISK, (direct(delegate_ops), observe)),),
            CONTROL.principal_ref: (PrincipalGrant(CONTROL, (direct(CONTROL_OPS, ACCOUNT_ONLY),)),),
        }
        authority = TrustedServiceAuthority(
            identity=GATEWAY, scope=WireScope(DOMAIN), scope_ref="scope-a",
            scope_version="scope-v1", security_ref="security-a", security_version="security-v1",
            registration_policy_ref="direct-policy-v1",
            registration_policy_version="direct-policy-v1",
            service_requirements_ref="direct-requirements-v1",
            service_requirements_version="direct-requirements-v1",
            curve_public_key=server_keys[0], curve_secret_key=server_keys[1],
            principals=publics, grants=grants,
        )
        raw = _service_config(endpoint).to_public_dict()
        raw["service_id"] = GATEWAY.actor_ref
        raw["components"]["gateway"]["settings"]["bind_ports"].append("service.account_read")
        raw["components"]["gateway"]["settings"]["options"].update(
            dispatch_enabled=True, cancel_enabled=True, control_enabled=True,
            strategy_ledger_enabled=True, account_extensions_enabled=True, delegation_enabled=True,
        )
        host = ServiceHost(
            parse_service_config(raw), authority, root / "registry.sqlite",
            lambda *_: ComponentResources.borrowed(
                {"binding": binding, "delegation_service": delegation,
                 "delegate_actor_service": actor, "read_service": read_service,
                 "account_observer": observer, "account_comparison": comparison},
                ready=lambda: True,
            ),
        )
        host.start()
        messages.put(("ready", os.getpid(), journal.journal_id))
        while True:
            try:
                item = controls.get_nowait()
            except queue.Empty:
                item = None
            if item == "stop":
                break
            if item is not None:
                # One synthetic venue return for a dispatched order (never a provider call).
                client_order_id, status, quantity, filled = item
                fact = GatewayReturnFact(
                    ACCOUNT, "CTP", instrument, TRADING_DAY, DirectExecutionEventKind.ORDER,
                    "synthetic-" + status + "-" + client_order_id,
                    identity=NativeOrderIdentity(
                        "CTP", client_order_id, {"trading_day": TRADING_DAY}, 1
                    ),
                    identity_keys=("native-" + client_order_id,),
                    binding_keys=("native-" + client_order_id,),
                    status=DirectOrderStatus(status), side="buy", offset="open",
                    order_quantity=quantity, filled_quantity=filled, limit_price="10",
                )
                result = owner.submit_service_call(
                    lambda fact=fact: ledger.ingest(fact)
                ).result(timeout_seconds=5)
                messages.put(("ingested", client_order_id, result.disposition))
            host.serve_once(10)
    except BaseException as error:
        import traceback

        messages.put(("error", type(error).__name__, str(error), traceback.format_exc()))
    finally:
        if host is not None and host.state == "started":
            host.close()
        if owner is not None:
            owner.close(5)
        messages.put(("closed", os.getpid()))


def _write_key(path: Path, key: bytes, *, secret: bool) -> str:
    path.write_bytes(key + b"\n")
    os.chmod(path, 0o600 if secret else 0o644)
    return str(path)


def _risk_files(root: Path, keys: dict, gateway_endpoint: str, risk_endpoint: str) -> Path:
    key_dir = root / "keys"
    key_dir.mkdir()
    for name, (public, secret) in keys.items():
        _write_key(key_dir / (name + ".public"), public, secret=False)
        _write_key(key_dir / (name + ".secret"), secret, secret=True)
    (root / "risk_policy.yaml").write_text(
        "schema: bt_api_risk.policy.v1\n"
        "policy_id: it-64-09\n"
        "account: {route: CTP___FUTURE, account_scope: " + ACCOUNT + "}\n"
        "per_order:\n  max_quantity: {strategy-a: {rb: \"2\"}}\n",
        encoding="utf-8",
    )
    config = {
        "schema": "bt_api_risk.service.v1",
        "service": {
            "service_id": RISK_HOST.actor_ref, "principal_ref": RISK_HOST.principal_ref,
            "instance_id": "risk-a", "scope_ref": "risk-scope", "rpc_bind": risk_endpoint,
            "public_key_file": "keys/risk_server.public", "secret_key_file": "keys/risk_server.secret",
        },
        "account": {
            "route": "CTP___FUTURE", "provider": "ctp", "environment": "simnow",
            "account_scope": ACCOUNT, "execution_domain_ref": DOMAIN,
            "instruments": {"rb": {"venue": "SHFE", "native_symbol": "rb", "product_type": "future",
                                   "multiplier": "10", "tick_size": "1"}},
        },
        "storage": {"directory": "state"},
        "policy": {"file": "risk_policy.yaml"},
        "principals": [
            {"principal_ref": STRATEGY.principal_ref, "role": "strategy",
             "actor_ref": STRATEGY.actor_ref, "strategy_refs": [STRATEGY.actor_ref],
             "operations": ["risk.order.reserve", "risk.order.dispatch", "risk.order.release",
                            "risk.order.status", "risk.status"],
             "public_key_file": "keys/strategy.public"},
            {"principal_ref": OPERATOR.principal_ref, "role": "operator",
             "actor_ref": OPERATOR.actor_ref, "strategy_refs": [],
             "operations": ["risk.admin.status", "risk.unknown.list", "risk.audit.read",
                            "risk.latch.list"],
             "public_key_file": "keys/operator.public"},
        ],
        "gateway": {
            "service_id": GATEWAY.actor_ref, "principal_ref": GATEWAY.principal_ref,
            "instance_id": "gateway-a", "scope_ref": "scope-a", "rpc_endpoint": gateway_endpoint,
            "server_public_key_file": "keys/gateway_server.public",
            "delegate": {"principal_ref": RISK.principal_ref, "actor_ref": RISK.actor_ref,
                         "public_key_file": "keys/risk_delegate.public",
                         "secret_key_file": "keys/risk_delegate.secret",
                         "grant_revisions": {STRATEGY.actor_ref: "risk-v1"}},
            "control": {"principal_ref": CONTROL.principal_ref, "actor_ref": CONTROL.actor_ref,
                        "public_key_file": "keys/risk_control.public",
                        "secret_key_file": "keys/risk_control.secret"},
        },
        "deadlines": {"call_timeout_seconds": 5, "reservation_ttl_seconds": 20},
        "refresh": {"loop_timeout_ms": 20, "observation_poll_seconds": "0.1",
                    "maintain_seconds": 1},
    }
    path = root / "risk_service.json"
    path.write_text(json.dumps(config), encoding="utf-8")
    return path


def _risk_client(risk_endpoint, keys):
    from bt_api_py.risk_service_client import RiskClientRoute, RiskServiceClient
    from bt_api_py.service_config import parse_client_config
    from bt_api_py.service_runtime import ClientCurveKeys

    config = parse_client_config(
        {
            "client_config_schema_version": 1, "process_role": "strategy_client",
            "registration": {"strategy_ref": STRATEGY.actor_ref},
            "clients": {"risk": {"config_schema_version": 1, "settings": {
                "bindings": [{"mount": "broker.risk_order", "service_id": RISK_HOST.actor_ref,
                              "instance_id": "risk-a", "scope_ref": "risk-scope",
                              "account_ref": ACCOUNT, "transport_ref": "risk-transport",
                              "required": True}],
                "options": {"request_timeout_ms": 20000}}}},
            "communication": {"config_schema_version": 1, "profiles": {"risk-transport": {
                "backend": "zmq", "config_schema_version": 1,
                "endpoints": {"rpc_connect": risk_endpoint}, "security_profile_ref": "risk-curve"}}},
        }
    )
    route = RiskClientRoute("broker.risk_order", RISK_HOST.actor_ref, "risk-a", "risk-scope",
                            SCOPE, STRATEGY)
    return RiskServiceClient.compose(
        config, route=route, peer=RISK_HOST,
        security_profiles={"risk-curve": ClientCurveKeys(keys["risk_server"][0], *keys["strategy"])},
        requested_operations=frozenset({"risk.order.reserve", "risk.order.dispatch",
                                        "risk.order.release", "risk.order.status", "risk.status"}),
    )


def _operator_runtime(risk_endpoint, keys):
    from bt_api_py.service_config import parse_client_config
    from bt_api_py.service_runtime import ClientCurveKeys, ServiceClientRuntime, ZmqTransportFactory
    from bt_api_py.service_runtime_thread import ThreadedServiceClientRuntime

    config = parse_client_config(
        {
            "client_config_schema_version": 1, "process_role": "application_client",
            "registration": {"application_ref": OPERATOR.actor_ref},
            "clients": {"risk": {"config_schema_version": 1, "settings": {"bindings": [{
                "mount": "application.risk_admin", "service_id": RISK_HOST.actor_ref,
                "instance_id": "risk-a", "scope_ref": "risk-scope", "account_ref": ACCOUNT,
                "transport_ref": "risk-transport"}]}}},
            "communication": {"config_schema_version": 1, "profiles": {"risk-transport": {
                "backend": "zmq", "config_schema_version": 1,
                "endpoints": {"rpc_connect": risk_endpoint}, "security_profile_ref": "risk-curve"}}},
        }
    )
    factory = ZmqTransportFactory(
        {"risk-curve": ClientCurveKeys(keys["risk_server"][0], *keys["operator"])}
    )
    return ThreadedServiceClientRuntime(lambda: ServiceClientRuntime(
        config, identity=OPERATOR, scope_resolver=lambda _: ACCOUNT_ONLY,
        peer_resolver=lambda _: RISK_HOST, transport_factory=factory,
        requested_operations={"application.risk_admin": frozenset(
            {"risk.admin.status", "risk.unknown.list", "risk.audit.read", "risk.latch.list"})}))


def _strategy_host():
    """Durable-enough strategy storage for one test: intents and records in memory."""
    from tests.unit.test_iter63_gateway_direct_client import MemoryHost

    class StrategyMemoryHost(MemoryHost):
        def save_cancel_plan(self, plan):
            return plan

        def load_cancel_plan(self, *_args):
            return None

        def save_cancel_record(self, *_args):
            return None

        def load_cancel_record(self, *_args):
            return None

    return StrategyMemoryHost()


def _until(read, done, seconds=15.0):
    deadline = time.monotonic() + seconds
    while True:
        value = read()
        if done(value) or time.monotonic() >= deadline:
            return value
        time.sleep(0.05)


def _request(key, client_order_id, quantity="1"):
    from bt_api_py._contracts.models import OrderRequest, OrderType, Side

    return OrderRequest(symbol="rb", side=Side.BUY, order_type=OrderType.LIMIT,
                        quantity=Decimal(quantity), price=Decimal("10"), account_id=ACCOUNT,
                        client_order_id=client_order_id, idempotency_key=key, offset="open",
                        quantity_unit="lots", exchange_id="SHFE")


def test_strategy_order_through_the_risk_process_reaches_the_gateway_and_settles(tmp_path):
    from bt_api_base.gateway_direct_protocol import GatewayCommandBinding

    from bt_api_py.gateway_api import GatewayBtApi
    from bt_api_py.gateway_direct_client import GatewayClientRoute
    from bt_api_py.gateway_strategy_client import GatewayStrategyClientSession
    from bt_api_py.service_runtime import ClientCurveKeys
    from tests.integration.test_iter63_gateway_direct_service import _client_config
    from tests.integration.test_service_account_instrument_metadata import _free_endpoint
    from tests.unit.test_iter63_gateway_strategy_delegated import Host as CancelHost

    keys = {name: zmq.curve_keypair() for name in
            ("gateway_server", "strategy", "risk_delegate", "risk_control", "risk_server", "operator")}
    publics = {keys["strategy"][0]: STRATEGY.principal_ref,
               keys["risk_delegate"][0]: RISK.principal_ref,
               keys["risk_control"][0]: CONTROL.principal_ref}
    gateway_root = tmp_path / "gateway"
    gateway_root.mkdir()
    risk_root = tmp_path / "risk"
    risk_root.mkdir()
    gateway_endpoint, risk_endpoint = _free_endpoint(), _free_endpoint()
    context = multiprocessing.get_context("spawn")
    messages, controls = context.Queue(), context.Queue()
    gateway = context.Process(target=_gateway, args=(str(gateway_root), gateway_endpoint,
                                                     keys["gateway_server"], publics, messages, controls))
    gateway.start()
    risk = strategy = risk_client = operator = None
    try:
        ready = messages.get(timeout=40)
        assert ready[0] == "ready", "\n".join(map(str, ready))
        strategy = GatewayStrategyClientSession(
            _client_config(gateway_endpoint, STRATEGY),
            binding=GatewayCommandBinding(ready[2], ACCOUNT, STRATEGY.actor_ref, STRATEGY.principal_ref),
            route=GatewayClientRoute("broker.direct_write", GATEWAY.actor_ref, "gateway-a", "scope-a",
                                     SCOPE, STRATEGY),
            peer=GATEWAY,
            security_profiles={"security-a": ClientCurveKeys(keys["gateway_server"][0], *keys["strategy"])},
            host=_strategy_host(), delegated_cancel_host=CancelHost(),
            requested_operations=frozenset({"direct.reserve", "direct.dispatch", "direct.command_status",
                                            "direct.delegated_command_status"}),
        )
        assert strategy.start(timeout_seconds=15).healthy

        config_path = _risk_files(risk_root, keys, gateway_endpoint, risk_endpoint)
        ready_file = risk_root / "ready.json"
        env = dict(os.environ)
        env["PYTHONDONTWRITEBYTECODE"] = "1"
        risk = subprocess.Popen(
            [sys.executable, "-m", "bt_api_py.risk_service_host", "run", "--config",
             str(config_path), "--ready-file", str(ready_file)],
            cwd=str(SDK_ROOT), env=env, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
        )
        deadline = time.monotonic() + 60
        while not ready_file.exists():
            assert risk.poll() is None, risk.communicate(timeout=10)
            assert time.monotonic() < deadline, "Risk host did not become ready"
            time.sleep(0.1)
        assert json.loads(ready_file.read_text())["pid"] == risk.pid != os.getpid()

        # A strategy's own direct order (not through Risk): its return gives Risk the
        # trading day and is counted as a bypass order.
        direct = strategy.command_client.reserve(_request("direct-1", "client-direct-1"),
                                                 timeout_seconds=10)
        assert direct.record is not None, direct
        assert strategy.command_client.dispatch("direct-1", timeout_seconds=10).outcome in {
            "QUEUED", "OBSERVED"}
        sent = _until(lambda: strategy.command_client.status("direct-1", timeout_seconds=5),
                      lambda r: r.record is not None and r.record.state == "SENT")
        assert sent.record.state == "SENT", sent
        controls.put(("client-direct-1", "accepted", "1", "0"))
        assert messages.get(timeout=10) == ("ingested", "client-direct-1", "ATTRIBUTED")

        operator = _operator_runtime(risk_endpoint, keys)
        assert operator.start(timeout_seconds=15).healthy

        def admin(operation, arguments=None):
            from bt_api_py.gateway_execution_stream_client import _detach_wire

            return _detach_wire(operator.call(
                "application.risk_admin", operation, arguments or {},
                account_ref=ACCOUNT, scope_ref="risk-scope", timeout_seconds=10))

        summary = _until(lambda: admin("risk.admin.status"),
                         lambda r: r["summary"]["bypass_orders"] >= 1)
        assert summary["status"] == "OBSERVED" and summary["summary"]["bypass_orders"] == 1, summary
        assert summary["summary"]["observation"]["continuity"] == "VERIFIED", summary
        assert summary["summary"]["policy_version"] == 1

        risk_client = _risk_client(risk_endpoint, keys)
        assert risk_client.start(timeout_seconds=15).healthy
        status = risk_client.service_status(timeout_seconds=10)
        assert status.outcome == "ANSWERED" and status.status.available is True, status

        # Accepted path: reserve then dispatch through Risk; the Gateway attributes it.
        reserved = risk_client.reserve(_request("risk-1", "risk-client-1"), request_key="risk-1",
                                       timeout_seconds=20)
        assert reserved.outcome == "ANSWERED" and reserved.answer.outcome == "RESERVED", reserved
        answer = reserved.answer
        dispatched = risk_client.dispatch("risk-1", permit_id=answer.permit_id,
                                          reservation_id=answer.reservation_id,
                                          view_digest=answer.view_digest, timeout_seconds=20)
        assert dispatched.outcome == "ANSWERED" and dispatched.answer.outcome == "DISPATCHED", dispatched
        facade = GatewayBtApi(strategy, exchange_name="CTP")
        observed = _until(lambda: facade.delegated_command_status("CTP", "risk-1"),
                          lambda r: r.observation is not None and r.observation.state == "SENT")
        assert observed.observation.state == "SENT", observed
        assert observed.observation.strategy_scope == STRATEGY.actor_ref
        assert observed.observation.actor_principal == RISK.principal_ref

        # A synthetic terminal return settles the permit through the observation stream.
        controls.put(("risk-client-1", "accepted", "1", "0"))
        assert messages.get(timeout=10) == ("ingested", "risk-client-1", "ATTRIBUTED")
        controls.put(("risk-client-1", "filled", "1", "1"))
        assert messages.get(timeout=10) == ("ingested", "risk-client-1", "ATTRIBUTED")
        terminal = _until(lambda: risk_client.status("risk-1", timeout_seconds=10),
                          lambda r: r.answer is not None and r.answer.outcome == "TERMINAL")
        assert terminal.answer.outcome == "TERMINAL" and terminal.answer.state == "TERMINAL", terminal

        # R-01 refusal: never reaches the Gateway.
        refused = risk_client.reserve(_request("risk-2", "risk-client-2", quantity="5"),
                                      request_key="risk-2", timeout_seconds=20)
        assert refused.outcome == "ANSWERED" and refused.answer.outcome == "DENIED", refused
        assert refused.answer.code == "risk_order_quantity_exceeds_cap"
        missing = facade.delegated_command_status("CTP", "risk-2")
        assert missing.observation is None, missing
        counts = risk_client.service_status(timeout_seconds=10).status.refusal_counts
        assert counts == {"risk_order_quantity_exceeds_cap": 1}
        assert admin("risk.unknown.list") == {"status": "OBSERVED", "permits": []}
        assert admin("risk.latch.list", {"active_only": False}) == {"status": "OBSERVED",
                                                                    "latches": []}
        assert admin("risk.latch.list", {"active_only": 1})["code"] == "risk_request_invalid"
        audit = admin("risk.audit.read", {"after": 0, "limit": 500})
        kinds = [record["kind"] for record in audit["records"]]
        assert "policy_publish" in kinds and "dispatched" in kinds and "reserve_refused" in kinds
    finally:
        for client in (risk_client, operator):
            if client is not None:
                client.close(timeout_seconds=5)
        risk_exit = None
        if risk is not None:
            risk.terminate()  # SIGTERM: clean shutdown path
            try:
                risk_exit = risk.wait(timeout=30)
            except subprocess.TimeoutExpired:
                risk.kill()
                risk.wait(timeout=10)
        if strategy is not None:
            strategy.close(timeout_seconds=5)
        controls.put("stop")
        gateway.join(20)
    assert risk_exit == 0, risk.communicate(timeout=10) if risk is not None else None
    assert not gateway.is_alive() and gateway.exitcode == 0

    # The Risk ledger names the delegated order with the Risk origin (read after exit).
    database = risk_root / "state" / "risk.sqlite"
    with sqlite3.connect("file:" + str(database) + "?mode=ro", uri=True) as connection:
        rows = dict(connection.execute(
            "SELECT request_key, origin FROM risk_pretrade_orders").fetchall())
        permits = dict(connection.execute(
            "SELECT request_key, state FROM risk_pretrade_permits").fetchall())
    assert rows == {"direct-1": "self", "risk-1": "risk:" + RISK.principal_ref}, rows
    assert permits == {"risk-1": "TERMINAL", "risk-2": "DENIED"}, permits
    assert oct(database.stat().st_mode & 0o777) == oct(0o600)
