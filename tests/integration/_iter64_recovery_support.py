"""Shared loopback composition for the Risk crash matrix and the 64.11 measurements.

INTEGRATION_SIM support only: a spawned loopback Gateway (synthetic native
dispatcher and synthetic venue returns, adapted from the 64.09 process test),
the Risk host started through its CLI in its own process, and strategy-side
clients over real CURVE. Temporary SQLite files; no provider session, no
account, no network beyond loopback. Not collected by pytest (no ``test_``
prefix).

Differences from ``test_iter64_risk_service_process``: any number of
strategies (each delegated to Risk), the Gateway reopens its own journal when
restarted on the same root, it counts native sends per client order id, and
the Risk host can be started with a test-only fault point
(``BT_RISK_FAULT_POINT``).
"""

from __future__ import annotations

import json
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
RISK = WireIdentity(WireRole.SERVICE, "risk-principal", "risk-service-delegate")
CONTROL = WireIdentity(WireRole.APPLICATION_CLIENT, "risk-control", "risk-control-actor")
OPERATOR = WireIdentity(WireRole.APPLICATION_CLIENT, "risk-operator", "risk-operator-actor")
RISK_HOST = WireIdentity(WireRole.SERVICE, "risk-host", "risk-service")
ACCOUNT_ONLY = WireScope(DOMAIN, account_ref=ACCOUNT)
SDK_ROOT = Path(__file__).resolve().parents[2]
SDK_PYTHONPATH = os.pathsep.join(
    str(SDK_ROOT / part)
    for part in (
        ".",
        "bt_api/bt_api_gateway/tests",
        "bt_api/bt_api_ctp/tests",
        "bt_api/bt_api_base/tests",
        "tests/unit",
        "tests",
    )
)
CONTROL_OPS = frozenset(
    {
        "direct.control.apply",
        "direct.control.status",
        "direct.control.list",
        "direct.control.cancel_scope",
        "direct.control.cancel_scope_status",
    }
)
RISK_ORDER_OPS = (
    "risk.order.reserve",
    "risk.order.dispatch",
    "risk.order.release",
    "risk.order.status",
    "risk.status",
)
OPERATOR_OPS = ("risk.admin.status", "risk.unknown.list", "risk.audit.read", "risk.latch.list")


def strategy_identity(index: int) -> WireIdentity:
    suffix = "a" if index == 0 else str(index)
    return WireIdentity(WireRole.STRATEGY_CLIENT, "principal-" + suffix, "strategy-" + suffix)


def instrument():
    from bt_api_base.market_protocol import MarketInstrumentKey

    return MarketInstrumentKey("ctp", "SHFE", "simnow", "future", "rb", "rb")


def synthetic_return_fact(item, instrument_key):
    """Build the legacy ORDER tuple or an explicitly identified synthetic TRADE.

    The caller must first establish that the original command was SENT. The
    trade's venue/binding key is the original command's native strong key;
    its stable trade key is scoped by trading day and supplied trade ID.
    """
    from bt_api_base.direct_write_protocol import (
        DirectExecutionEventKind,
        DirectOrderStatus,
        NativeOrderIdentity,
    )
    from bt_api_base.gateway_execution_protocol import GatewayReturnFact

    if len(item) == 5 and item[0] == "trade":
        _kind, cid, trade_id, quantity, price = item
        native_key = "native-" + cid
        return GatewayReturnFact(
            ACCOUNT, "CTP", instrument_key, TRADING_DAY, DirectExecutionEventKind.TRADE,
            "synthetic-trade-" + trade_id,
            identity=NativeOrderIdentity("CTP", cid, {"trading_day": TRADING_DAY}, 1),
            identity_keys=(native_key,), binding_keys=(native_key,), venue_key=native_key,
            trade_key=(TRADING_DAY, trade_id), side="buy", offset="open",
            trade_quantity=quantity, trade_price=price,
        )
    cid, status, quantity, filled = item
    return GatewayReturnFact(
        ACCOUNT, "CTP", instrument_key, TRADING_DAY, DirectExecutionEventKind.ORDER,
        "synthetic-" + status + "-" + cid,
        identity=NativeOrderIdentity("CTP", cid, {"trading_day": TRADING_DAY}, 1),
        identity_keys=("native-" + cid,), binding_keys=("native-" + cid,),
        status=DirectOrderStatus(status), side="buy", offset="open",
        order_quantity=quantity, filled_quantity=filled, limit_price="10",
    )


# ----------------------------------------------------------------------------- Gateway child


def gateway_main(root, endpoint, server_keys, publics, strategy_count, messages, controls):
    """Loopback Gateway in its own process; reopens its journal when restarted on ``root``."""
    sys.path.insert(0, str(SDK_ROOT / "bt_api/bt_api_gateway/tests"))
    owner = host = None
    try:
        from bt_api_base.direct_cancel_protocol import PreparedCancelRequest
        from bt_api_base.direct_write_protocol import (
            DirectExecutionEventKind,
            NativeOrderIdentity,
            PreDispatchRecord,
            WriteOperation,
        )
        from bt_api_gateway.direct_cancel_dispatch import GatewayDirectCancelDispatcher
        from bt_api_gateway.direct_cancel_journal import GatewayDirectCancelJournal
        from bt_api_gateway.direct_command_journal import GatewayDirectCommandJournal
        from bt_api_gateway.direct_control import GatewayDirectControl
        from bt_api_gateway.direct_delegation import GatewayDelegateGrant
        from bt_api_gateway.direct_dispatch import GatewayDirectDispatcher, NativeDispatchOutcome
        from bt_api_gateway.direct_execution_journal import GatewayDirectExecutionLedger
        from bt_api_gateway.session_guard import LocalAccountSessionGuard
        from iter63_claim_support import SyntheticClaimPort, claiming

        from bt_api_py.gateway_account_comparison import GatewayAccountComparison
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

        key = instrument()
        root = Path(root)
        (root / "guard").mkdir(exist_ok=True)
        strategies = [strategy_identity(i) for i in range(strategy_count)]
        holder: dict = {"places": {}}
        database = root / "journal.sqlite"

        def factory():
            guard = LocalAccountSessionGuard(root / "guard", ACCOUNT, "owner")
            generation = guard.acquire()
            if database.exists():
                with sqlite3.connect(database) as connection:
                    journal_id, revision = connection.execute(
                        "SELECT journal_id, revision FROM direct_journal_meta"
                    ).fetchone()
                journal = GatewayDirectCommandJournal(
                    database, ACCOUNT, generation=generation, expected_journal_id=journal_id,
                    minimum_revision=revision, controls_enabled=True,
                    strategy_ledger_enabled=True, account_extensions_enabled=True,
                )
            else:
                journal = GatewayDirectCommandJournal.create(database, ACCOUNT, generation=generation)
                journal.enable_controls(guard)
                journal.enable_strategy_ledger(guard)
                journal.enable_account_extensions(guard)
            holder["control"] = GatewayDirectControl(journal, guard)
            holder["ledger"] = GatewayDirectExecutionLedger(journal, guard)

            def place(payload, port, account):
                client_order_id = payload["client_order_id"]
                holder["places"][client_order_id] = holder["places"].get(client_order_id, 0) + 1
                identity = NativeOrderIdentity("CTP", client_order_id, {"trading_day": TRADING_DAY}, 1)
                marker = port.commit(
                    PreDispatchRecord(
                        WriteOperation.PLACE, account, "CTP", payload["symbol"], identity, None,
                        payload, TRADING_DAY, 1, "synthetic",
                    )
                )
                return NativeDispatchOutcome(True, identity, marker)

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
            factory, queue_size=64, max_queue_wait_seconds=5, busy_timeout_seconds=5
        )
        assert owner.start(10)
        journal, ledger = holder["journal"], holder["ledger"]
        stream = GatewayExecutionStreamService(
            ledger, journal, {s.principal_ref: s.actor_ref for s in strategies},
            execution_domain_ref=DOMAIN,
        )
        binding = GatewayDirectServiceBinding(
            journal,
            {s.principal_ref: GatewayDirectGrant(s.actor_ref, frozenset({"rb"})) for s in strategies},
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
                tuple(
                    GatewayDelegateGrant(RISK, ACCOUNT, s.actor_ref, DOMAIN, "risk", "risk-v1",
                                         frozenset({key}), logical)
                    for s in strategies
                ),
                lambda value: value == key,
            ),
            owner=owner, journal=journal, instance_id="gateway-a", scope_ref="scope-a",
        )
        actor = GatewayDelegateActorService(delegation)
        observers = frozenset({RISK.principal_ref})
        observer = GatewayAccountObserver(
            owner, journal, ledger, account_scope=ACCOUNT, execution_domain_ref=DOMAIN,
            principals=observers,
        )
        # The account read port is a Host requirement the observer never uses; its fixed
        # synthetic observation cannot be stored twice, so each Gateway start gets its own.
        read_root = root / ("read-" + str(time.time_ns()))
        read_root.mkdir()
        read_service = _read_service(read_root, observers)
        comparison = GatewayAccountComparison(
            owner, journal, account_scope=ACCOUNT, principals=frozenset(),
            instruments=frozenset({key}),
        )
        strategy_ops = frozenset(
            {"direct.reserve", "direct.dispatch", "direct.command_status",
             "direct.delegated_command_status", "direct.delegated_cancel",
             "direct.delegated_cancel_status"}
        ) | EVENT_OPERATIONS
        delegate_ops = frozenset({"direct.delegated_reserve", "direct.delegated_dispatch"}) | ACTOR_OPERATIONS

        def direct(operations, scope):
            return ResourceGrant("gateway-a", "service.direct_write", "scope-a", scope, operations)

        observe = ResourceGrant(
            "gateway-a", "service.account_read", "scope-a", ACCOUNT_ONLY, ACCOUNT_OBSERVER_OPERATIONS
        )
        grants = {}
        for s in strategies:
            template = WireScope(DOMAIN, account_ref=ACCOUNT, strategy_ref=s.actor_ref)
            grants[s.principal_ref] = (PrincipalGrant(s, (direct(strategy_ops, template),)),)
        grants[RISK.principal_ref] = (PrincipalGrant(RISK, tuple(
            direct(delegate_ops, WireScope(DOMAIN, account_ref=ACCOUNT, strategy_ref=s.actor_ref))
            for s in strategies) + (observe,)),)
        grants[CONTROL.principal_ref] = (PrincipalGrant(CONTROL, (direct(CONTROL_OPS, ACCOUNT_ONLY),)),)
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
        messages.put(("ready", os.getpid(), journal.journal_id, journal.generation))
        while True:
            try:
                item = controls.get_nowait()
            except queue.Empty:
                item = None
            if item == "stop":
                break
            if item == "stats":
                messages.put(("stats", dict(holder["places"])))
            elif item is not None:
                # One synthetic venue return for a dispatched order (never a provider call).
                fact = synthetic_return_fact(item, key)
                client_order_id = fact.identity.client_order_id
                result = owner.submit_service_call(
                    lambda fact=fact: ledger.ingest(fact)
                ).result(timeout_seconds=5)
                reply = ("ingested", client_order_id, result.disposition, time.time_ns())
                if fact.kind is DirectExecutionEventKind.TRADE:
                    reply += ("trade", fact.trade_key[-1])
                messages.put(reply)
            host.serve_once(5)
    except BaseException as error:
        import traceback

        messages.put(("error", type(error).__name__, str(error), traceback.format_exc()))
    finally:
        if host is not None and host.state == "started":
            host.close()
        if owner is not None:
            owner.close(5)
        messages.put(("closed", os.getpid()))


# ----------------------------------------------------------------------------- files and clients


def keypairs(strategy_count: int) -> dict:
    names = ["gateway_server", "risk_delegate", "risk_control", "risk_server", "operator"]
    names += ["strategy-" + str(i) for i in range(strategy_count)]
    return {name: zmq.curve_keypair() for name in names}


def gateway_publics(keys: dict, strategy_count: int) -> dict:
    publics = {keys["strategy-" + str(i)][0]: strategy_identity(i).principal_ref
               for i in range(strategy_count)}
    publics[keys["risk_delegate"][0]] = RISK.principal_ref
    publics[keys["risk_control"][0]] = CONTROL.principal_ref
    return publics


def _write_key(path: Path, key: bytes, *, secret: bool) -> None:
    path.write_bytes(key + b"\n")
    os.chmod(path, 0o600 if secret else 0o644)


def risk_files(root: Path, keys: dict, strategy_count: int, gateway_endpoint: str,
               risk_endpoint: str, *, ttl_seconds: int = 20, max_quantity: str = "2",
               observation_poll: str = "0.1", maintain_seconds: str = "1",
               dispatch_recheck_seconds: int = 2) -> Path:
    key_dir = root / "keys"
    key_dir.mkdir(exist_ok=True)
    for name, (public, secret) in keys.items():
        _write_key(key_dir / (name + ".public"), public, secret=False)
        _write_key(key_dir / (name + ".secret"), secret, secret=True)
    strategies = [strategy_identity(i) for i in range(strategy_count)]
    caps = ", ".join(s.actor_ref + ": {rb: \"" + max_quantity + "\"}" for s in strategies)
    (root / "risk_policy.yaml").write_text(
        "schema: bt_api_risk.policy.v1\n"
        "policy_id: it-64-recovery\n"
        "account: {route: CTP___FUTURE, account_scope: " + ACCOUNT + "}\n"
        "per_order:\n  max_quantity: {" + caps + "}\n"
        "self_trade: {enabled: false, on_unverifiable: deny}\n",
        encoding="utf-8",
    )
    principals = [
        {"principal_ref": s.principal_ref, "role": "strategy", "actor_ref": s.actor_ref,
         "strategy_refs": [s.actor_ref], "operations": list(RISK_ORDER_OPS),
         "public_key_file": "keys/strategy-" + str(i) + ".public"}
        for i, s in enumerate(strategies)
    ]
    principals.append(
        {"principal_ref": OPERATOR.principal_ref, "role": "operator",
         "actor_ref": OPERATOR.actor_ref, "strategy_refs": [], "operations": list(OPERATOR_OPS),
         "public_key_file": "keys/operator.public"}
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
        "principals": principals,
        "gateway": {
            "service_id": GATEWAY.actor_ref, "principal_ref": GATEWAY.principal_ref,
            "instance_id": "gateway-a", "scope_ref": "scope-a", "rpc_endpoint": gateway_endpoint,
            "server_public_key_file": "keys/gateway_server.public",
            "delegate": {"principal_ref": RISK.principal_ref, "actor_ref": RISK.actor_ref,
                         "public_key_file": "keys/risk_delegate.public",
                         "secret_key_file": "keys/risk_delegate.secret",
                         "grant_revisions": {s.actor_ref: "risk-v1" for s in strategies}},
            "control": {"principal_ref": CONTROL.principal_ref, "actor_ref": CONTROL.actor_ref,
                        "public_key_file": "keys/risk_control.public",
                        "secret_key_file": "keys/risk_control.secret"},
        },
        "deadlines": {"call_timeout_seconds": 5, "reservation_ttl_seconds": ttl_seconds,
                      "dispatch_recheck_seconds": dispatch_recheck_seconds},
        "refresh": {"loop_timeout_ms": 5, "observation_poll_seconds": observation_poll,
                    "maintain_seconds": maintain_seconds, "dispatch_recheck_limit": 64},
    }
    path = root / "risk_service.json"
    path.write_text(json.dumps(config), encoding="utf-8")
    return path


def risk_client(risk_endpoint: str, keys: dict, index: int = 0, *, run_id: str = RUN):
    from bt_api_py.risk_service_client import RiskClientRoute, RiskServiceClient
    from bt_api_py.service_config import parse_client_config
    from bt_api_py.service_runtime import ClientCurveKeys

    identity = strategy_identity(index)
    config = parse_client_config(
        {
            "client_config_schema_version": 1, "process_role": "strategy_client",
            "registration": {"strategy_ref": identity.actor_ref},
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
    scope = WireScope(DOMAIN, account_ref=ACCOUNT, strategy_ref=identity.actor_ref, run_id=run_id)
    route = RiskClientRoute("broker.risk_order", RISK_HOST.actor_ref, "risk-a", "risk-scope",
                            scope, identity)
    return RiskServiceClient.compose(
        config, route=route, peer=RISK_HOST,
        security_profiles={"risk-curve": ClientCurveKeys(
            keys["risk_server"][0], *keys["strategy-" + str(index)])},
        requested_operations=frozenset(RISK_ORDER_OPS),
    )


def operator_runtime(risk_endpoint: str, keys: dict, *, account: str = ACCOUNT):
    from bt_api_py.service_config import parse_client_config
    from bt_api_py.service_runtime import ClientCurveKeys, ServiceClientRuntime, ZmqTransportFactory
    from bt_api_py.service_runtime_thread import ThreadedServiceClientRuntime

    config = parse_client_config(
        {
            "client_config_schema_version": 1, "process_role": "application_client",
            "registration": {"application_ref": OPERATOR.actor_ref},
            "clients": {"risk": {"config_schema_version": 1, "settings": {"bindings": [{
                "mount": "application.risk_admin", "service_id": RISK_HOST.actor_ref,
                "instance_id": "risk-a", "scope_ref": "risk-scope", "account_ref": account,
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
        config, identity=OPERATOR, scope_resolver=lambda _: WireScope(DOMAIN, account_ref=account),
        peer_resolver=lambda _: RISK_HOST, transport_factory=factory,
        requested_operations={"application.risk_admin": frozenset(OPERATOR_OPS)}))


def admin(operator, operation, arguments=None, timeout_seconds=10):
    from bt_api_py.gateway_execution_stream_client import _detach_wire

    return _detach_wire(operator.call(
        "application.risk_admin", operation, arguments or {},
        account_ref=ACCOUNT, scope_ref="risk-scope", timeout_seconds=timeout_seconds))


def strategy_session(gateway_endpoint: str, keys: dict, journal_id: str, index: int = 0,
                     *, run_id: str = RUN, account: str = ACCOUNT, peer: WireIdentity = GATEWAY,
                     requested_operations: frozenset | None = None):
    from bt_api_base.gateway_direct_protocol import GatewayCommandBinding

    from bt_api_py.gateway_direct_client import GatewayClientRoute
    from bt_api_py.gateway_strategy_client import GatewayStrategyClientSession
    from bt_api_py.service_config import parse_client_config
    from bt_api_py.service_runtime import ClientCurveKeys
    from tests.integration.test_iter63_gateway_direct_service import _client_config
    from tests.unit.test_iter63_gateway_direct_client import MemoryHost
    from tests.unit.test_iter63_gateway_strategy_delegated import Host as CancelHost

    class StrategyMemoryHost(MemoryHost):
        def save_cancel_plan(self, plan):
            return plan

        def load_cancel_plan(self, *_args):
            return None

        def save_cancel_record(self, *_args):
            return None

        def load_cancel_record(self, *_args):
            return None

    identity = strategy_identity(index)
    scope = WireScope(DOMAIN, account_ref=account, strategy_ref=identity.actor_ref, run_id=run_id)
    raw = _client_config(gateway_endpoint, identity).to_public_dict()
    raw["clients"]["gateway"]["settings"]["bindings"][0]["account_ref"] = account
    raw["clients"]["gateway"]["settings"]["bindings"][0]["service_id"] = peer.actor_ref
    return GatewayStrategyClientSession(
        parse_client_config(raw),
        binding=GatewayCommandBinding(journal_id, account, identity.actor_ref, identity.principal_ref),
        route=GatewayClientRoute("broker.direct_write", peer.actor_ref, "gateway-a", "scope-a",
                                 scope, identity),
        peer=peer,
        security_profiles={"security-a": ClientCurveKeys(
            keys["gateway_server"][0], *keys["strategy-" + str(index)])},
        host=StrategyMemoryHost(), delegated_cancel_host=CancelHost(),
        requested_operations=requested_operations or frozenset(
            {"direct.reserve", "direct.dispatch", "direct.command_status",
             "direct.delegated_command_status"}),
    )


def order_request(key: str, client_order_id: str, quantity: str = "1"):
    from bt_api_py._contracts.models import OrderRequest, OrderType, Side

    return OrderRequest(symbol="rb", side=Side.BUY, order_type=OrderType.LIMIT,
                        quantity=Decimal(quantity), price=Decimal("10"), account_id=ACCOUNT,
                        client_order_id=client_order_id, idempotency_key=key, offset="open",
                        quantity_unit="lots", exchange_id="SHFE")


def until(read, done, seconds=15.0, interval=0.05):
    deadline = time.monotonic() + seconds
    while True:
        value = read()
        if done(value) or time.monotonic() >= deadline:
            return value
        time.sleep(interval)


def start_risk(config_path: Path, ready_file: Path, *, fault_point: str | None = None,
               extra_env: dict | None = None, launcher: list[str] | None = None,
               timeout: float = 90.0) -> subprocess.Popen:
    """Start the Risk host CLI in its own process and wait for its ready file."""
    if ready_file.exists():
        ready_file.unlink()
    env = dict(os.environ)
    env["PYTHONDONTWRITEBYTECODE"] = "1"
    env["PYTHONPATH"] = SDK_PYTHONPATH + (
        os.pathsep + env["PYTHONPATH"] if env.get("PYTHONPATH") else "")
    env.pop("BT_RISK_FAULT_POINT", None)
    if fault_point is not None:
        env["BT_RISK_FAULT_POINT"] = fault_point
    env.update(extra_env or {})
    command = launcher or [sys.executable, "-m", "bt_api_py.risk_service_host"]
    process = subprocess.Popen(
        command + ["run", "--config", str(config_path), "--ready-file", str(ready_file)],
        cwd=str(SDK_ROOT), env=env, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
    )
    deadline = time.monotonic() + timeout
    while not ready_file.exists():
        if process.poll() is not None:
            raise AssertionError(("risk host exited", process.returncode,
                                  process.communicate(timeout=10)))
        if time.monotonic() > deadline:
            process.kill()
            raise AssertionError(("risk host not ready", process.communicate(timeout=10)))
        time.sleep(0.02)
    return process


def stop_risk(process: subprocess.Popen | None, timeout: float = 30.0) -> int | None:
    if process is None:
        return None
    if process.poll() is None:
        process.terminate()
        try:
            return process.wait(timeout=timeout)
        except subprocess.TimeoutExpired:
            process.kill()
            process.wait(timeout=10)
            return None
    return process.returncode


def next_message(messages, kind: str, timeout: float = 10.0):
    """The next Gateway message of ``kind``; fails on a Gateway error report."""
    deadline = time.monotonic() + timeout
    while True:
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            raise AssertionError("no Gateway message " + kind)
        item = messages.get(timeout=remaining)
        if item[0] == "error":
            raise AssertionError("\n".join(map(str, item)))
        if item[0] == kind:
            return item


def gateway_command(gateway_root: Path, strategy: str, request_key: str):
    """Read-only view of the Gateway journal row of one command (status, version)."""
    database = Path(gateway_root) / "journal.sqlite"
    with sqlite3.connect("file:" + str(database) + "?mode=ro", uri=True, timeout=5) as connection:
        return connection.execute(
            "SELECT status, version, reservation_id FROM direct_journal_commands "
            "WHERE account=? AND strategy=? AND request_key=?",
            (ACCOUNT, strategy, request_key),
        ).fetchall()


def risk_permit(risk_root: Path, request_key: str):
    database = Path(risk_root) / "state" / "risk.sqlite"
    with sqlite3.connect("file:" + str(database) + "?mode=ro", uri=True, timeout=5) as connection:
        connection.row_factory = sqlite3.Row
        rows = connection.execute(
            "SELECT state, gateway_state, reservation_id, code FROM risk_pretrade_permits "
            "WHERE request_key=?", (request_key,)
        ).fetchall()
    return [dict(row) for row in rows]
