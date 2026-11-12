"""Shared process fixture of the iteration 64 Risk scenario matrix (64.09, INTEGRATION_SIM).

Processes, all over real CURVE on loopback:

* one spawned Gateway (synthetic native dispatcher and cancel dispatcher, synthetic
  venue returns injected on request, journal schema 17 with the strategy ledger,
  strategy origins and contract controls; delegation of strategies A and B to the
  Risk principal; the Risk principal is also the account observer; a ``risk``
  control grant for the Risk control principal and an ``operator`` control grant
  for a human Gateway operator);
* the Risk host, started through its CLI ``python -m bt_api_py.risk_service_host run``
  in a subprocess (restartable on the same storage);
* three spawned strategy processes: A and B select Risk (``GatewayBtApi`` with a
  ``RiskServiceClient``), C selects no Risk and talks to the Gateway directly.

The test process is the operator: it holds a Risk admin client and a Gateway
control client. Every process reports, when it stops, which of the watched
packages its interpreter holds (INV64-02 observation).

No provider session, no account, no venue: temporary SQLite files only. Nothing
here proves a provider, account or production capability.
"""

from __future__ import annotations

import contextlib
import json
import multiprocessing
import os
import queue
import shutil
import subprocess
import sys
import time
import uuid
from decimal import Decimal
from pathlib import Path

from bt_api_base.service_protocol import WireIdentity, WireRole, WireScope

ACCOUNT, DOMAIN = "account-a", "paper:demo"
TRADING_DAY = "20300101"
GATEWAY = WireIdentity(WireRole.SERVICE, "gateway-principal", "gateway-service")
STRATEGIES = {
    "a": WireIdentity(WireRole.STRATEGY_CLIENT, "principal-a", "strategy-a"),
    "b": WireIdentity(WireRole.STRATEGY_CLIENT, "principal-b", "strategy-b"),
    "c": WireIdentity(WireRole.STRATEGY_CLIENT, "principal-c", "strategy-c"),
}
RUNS = {"a": "run-a", "b": "run-b", "c": "run-c"}
RISK_SELECTED = ("a", "b")
RISK = WireIdentity(WireRole.SERVICE, "risk-principal", "risk-service-delegate")
CONTROL = WireIdentity(WireRole.APPLICATION_CLIENT, "risk-control", "risk-control-actor")
GW_OPERATOR = WireIdentity(WireRole.APPLICATION_CLIENT, "gateway-operator", "gateway-operator-actor")
RISK_HOST = WireIdentity(WireRole.SERVICE, "risk-host", "risk-service")
RISK_OPERATOR = WireIdentity(WireRole.APPLICATION_CLIENT, "risk-operator", "risk-operator-actor")
ACCOUNT_ONLY = WireScope(DOMAIN, account_ref=ACCOUNT)
SDK_ROOT = Path(__file__).resolve().parents[2]
WATCHED = ("bt_api_execution", "bt_api_monitor", "bt_api_risk")
RISK_CONTROL_OPS = frozenset(
    {
        "direct.control.apply",
        "direct.control.status",
        "direct.control.list",
        "direct.control.cancel_scope",
        "direct.control.cancel_scope_status",
    }
)
OPERATOR_CONTROL_OPS = frozenset(
    {
        "direct.control.apply",
        "direct.control.status",
        "direct.control.list",
        "direct.control.resume",
        "direct.control.resume_status",
        "direct.control.cancel_scope_status",
    }
)
RISK_ADMIN_OPS = (
    "risk.admin.status",
    "risk.policy.read",
    "risk.policy.publish",
    "risk.latch.list",
    "risk.latch.release",
    "risk.unknown.list",
    "risk.counts.export",
    "risk.audit.read",
    "risk.cancel.request",
    "risk.cancel.status",
)
STRATEGY_RISK_OPS = (
    "risk.order.reserve",
    "risk.order.dispatch",
    "risk.order.release",
    "risk.order.status",
    "risk.status",
)
KEY_NAMES = (
    "gateway_server",
    "risk_delegate",
    "risk_control",
    "risk_server",
    "risk_operator",
    "gateway_operator",
    "strategy_a",
    "strategy_b",
    "strategy_c",
)


def loaded_watched() -> list[str]:
    return sorted({name.split(".")[0] for name in sys.modules} & set(WATCHED))


def instrument(symbol: str):
    from bt_api_base.market_protocol import MarketInstrumentKey

    return MarketInstrumentKey("ctp", "SHFE", "simnow", "future", symbol, symbol)


def _scope(which: str, *, run: bool = True) -> WireScope:
    return WireScope(
        DOMAIN,
        account_ref=ACCOUNT,
        strategy_ref=STRATEGIES[which].actor_ref,
        run_id=RUNS[which] if run else None,
    )


# ============================================================================ Gateway process


def gateway_process(root, endpoint, server_keys, publics, messages, controls, pause_release):
    """Loopback Gateway in its own process; answers control-queue requests on ``messages``."""
    sys.path.insert(0, str(SDK_ROOT / "bt_api/bt_api_gateway/tests"))
    owner = host = None
    try:
        from contextlib import contextmanager, nullcontext

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
        from bt_api_gateway.strategy_position_ledger import (
            StrategyBaselineEvidence,
            StrategyInstrumentBinding,
        )
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
        from bt_api_py.gateway_strategy_origin_source import GatewayStrategyOriginSource
        from bt_api_py.service_components import ComponentResources
        from bt_api_py.service_config import parse_service_config
        from bt_api_py.service_host import ServiceHost, TrustedServiceAuthority
        from bt_api_py.service_registry import PrincipalGrant, ResourceGrant
        from tests.integration.test_iter63_delegate_risk_execution_loopback import _read_service
        from tests.integration.test_iter63_gateway_direct_service import _service_config

        instruments = {"rb": instrument("rb"), "hc": instrument("hc")}
        root = Path(root)
        (root / "guard").mkdir()
        holder = {}

        def factory():
            guard = LocalAccountSessionGuard(root / "guard", ACCOUNT, "owner")
            generation = guard.acquire()
            journal = GatewayDirectCommandJournal.create(
                root / "journal.sqlite", ACCOUNT, generation=generation
            )
            journal.enable_controls(guard)
            journal.enable_strategy_ledger(guard)
            evidence = StrategyBaselineEvidence(
                ACCOUNT, journal.journal_id, TRADING_DAY, "CTP", generation, 1, "source",
                "positions", "orders", "cut", ("resource",), 100, 200, True, True, True, True,
            )

            @contextmanager
            def baseline_fence():
                yield

            journal.initialize_empty_strategy_ledger(
                evidence, guard=guard, baseline_fence=baseline_fence,
                verify_current_empty=lambda value: value == evidence,
            )
            journal.install_strategy_ledger_ports(
                guard=guard,
                resolve_binding=lambda payload: StrategyInstrumentBinding(
                    instruments[payload["symbol"]], "CTP", "lots", False
                ),
                current_trading_day=lambda: TRADING_DAY,
                verify_day_transition=lambda *_: False,
                verify_current_continuity=lambda *_: True,
            )
            for step in (
                "enable_account_extensions", "enable_parent_authority",
                "enable_account_facts_financial", "enable_actor_stream",
                "enable_strategy_origin", "enable_control_cancel",
            ):
                getattr(journal, step)(guard)
            holder["control"] = GatewayDirectControl(journal, guard)
            holder["ledger"] = GatewayDirectExecutionLedger(journal, guard)
            holder["source"] = GatewayStrategyOriginSource(
                resolve=lambda _context, order: instruments[order.symbol],
                recheck=lambda value: value in instruments.values(),
            )

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

            dispatcher = GatewayDirectDispatcher(
                journal, "CTP", guard, claiming(place),
                lambda identity: ("native-" + identity.client_order_id,), lambda _: True,
                claim_port=SyntheticClaimPort(),
            )

            def cancel(target, symbol, port, account):
                holder.setdefault("cancels", []).append(target.client_order_id)
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

            def close():
                holder["source"].close()
                guard.release()

            return GatewayDirectOwnerResources(
                dispatcher, journal, close, cancel_dispatcher=cancel_dispatcher
            )

        owner = GatewayDirectOwner(
            factory, queue_size=16, max_queue_wait_seconds=5, busy_timeout_seconds=5
        )
        assert owner.start(10)
        journal, ledger = holder["journal"], holder["ledger"]
        principals = {identity.principal_ref: identity.actor_ref for identity in STRATEGIES.values()}
        stream = GatewayExecutionStreamService(
            ledger, journal, principals, execution_domain_ref=DOMAIN
        )
        binding = GatewayDirectServiceBinding(
            journal,
            {
                identity.principal_ref: GatewayDirectGrant(identity.actor_ref, frozenset(instruments))
                for identity in STRATEGIES.values()
            },
            owner=owner,
            reservation_fence=lambda _context, _order: nullcontext(),
            strategy_origin_source=holder["source"],
            cancel_enabled=True,
            execution_stream=stream,
            execution_domain_ref=DOMAIN,
            control=holder["control"],
            control_grants={
                CONTROL.principal_ref: GatewayControlGrant(
                    CONTROL.actor_ref, True, frozenset(), RISK_CONTROL_OPS, kind="risk",
                    modes=frozenset({"STOP_NEW", "FREEZE"}), instrument_scope=True,
                ),
                GW_OPERATOR.principal_ref: GatewayControlGrant(
                    GW_OPERATOR.actor_ref, True, frozenset(), OPERATOR_CONTROL_OPS,
                    kind="operator", instrument_scope=True,
                ),
            },
        )
        logical = frozenset(
            {"direct.reserve", "direct.dispatch", "direct.command_status", "direct.cancel",
             "direct.cancel_status", "direct.release"}
        )
        delegation = GatewayDelegationService(
            GatewayDelegationSettings(
                tuple(
                    GatewayDelegateGrant(RISK, ACCOUNT, STRATEGIES[which].actor_ref, DOMAIN, "risk",
                                         "risk-v1", frozenset(instruments.values()), logical)
                    for which in RISK_SELECTED
                ),
                lambda value: value in instruments.values(),
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
        comparison = GatewayAccountComparison(
            owner, journal, account_scope=ACCOUNT, principals=frozenset(),
            instruments=frozenset(instruments.values()),
        )
        risk_ops = frozenset(
            {"direct.reserve", "direct.dispatch", "direct.command_status",
             "direct.delegated_command_status", "direct.delegated_cancel",
             "direct.delegated_cancel_status"}
        ) | EVENT_OPERATIONS
        direct_ops = frozenset(
            {"direct.reserve", "direct.dispatch", "direct.command_status", "direct.cancel",
             "direct.cancel_status"}
        ) | EVENT_OPERATIONS
        delegate_ops = frozenset(
            {"direct.delegated_reserve", "direct.delegated_dispatch"}
        ) | ACTOR_OPERATIONS

        def direct(operations, scope):
            return ResourceGrant("gateway-a", "service.direct_write", "scope-a", scope, operations)

        observe = ResourceGrant(
            "gateway-a", "service.account_read", "scope-a", ACCOUNT_ONLY, ACCOUNT_OBSERVER_OPERATIONS
        )
        grants = {
            STRATEGIES[which].principal_ref: (
                PrincipalGrant(
                    STRATEGIES[which],
                    (direct(risk_ops if which in RISK_SELECTED else direct_ops,
                            _scope(which, run=False)),),
                ),
            )
            for which in STRATEGIES
        }
        grants[RISK.principal_ref] = (
            PrincipalGrant(
                RISK,
                tuple(direct(delegate_ops, _scope(which, run=False)) for which in RISK_SELECTED)
                + (observe,),
            ),
        )
        grants[CONTROL.principal_ref] = (
            PrincipalGrant(CONTROL, (direct(RISK_CONTROL_OPS, ACCOUNT_ONLY),)),
        )
        grants[GW_OPERATOR.principal_ref] = (
            PrincipalGrant(GW_OPERATOR, (direct(OPERATOR_CONTROL_OPS, ACCOUNT_ONLY),)),
        )
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
            strategy_origin_enabled=True, control_cancel_enabled=True,
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

        def ingest(item):
            cid = item["cid"]
            kind = item.get("kind", "order")
            native = NativeOrderIdentity("CTP", cid, {"trading_day": TRADING_DAY}, 1)
            keys, venue = ("native-" + cid,), "venue-" + cid
            # Shape of a venue return (as in test_iter63_gateway_return_ledger): an order
            # return binds the venue key; a trade is attributed by that venue key.
            common = {
                "identity": native, "identity_keys": tuple(sorted((*keys, venue))),
                "binding_keys": keys, "venue_key": venue, "side": item.get("side", "buy"),
                "offset": item.get("offset", "open"),
            }
            if kind == "trade":
                fact = GatewayReturnFact(
                    ACCOUNT, "CTP", instruments[item.get("symbol", "rb")], TRADING_DAY,
                    DirectExecutionEventKind.TRADE, "synthetic-trade-" + item["trade_id"],
                    identity=native, identity_keys=(venue,), venue_key=venue,
                    side=item.get("side", "buy"), offset=item.get("offset", "open"),
                    trade_key=(TRADING_DAY, item["trade_id"]),
                    trade_quantity=item["quantity"], trade_price=item.get("price", "10"),
                )
            else:
                status = item["status"]
                fact = GatewayReturnFact(
                    ACCOUNT, "CTP", instruments[item.get("symbol", "rb")], TRADING_DAY,
                    DirectExecutionEventKind.ORDER,
                    "synthetic-" + status + "-" + cid + "-" + item.get("filled", "0"),
                    status=DirectOrderStatus(status), order_quantity=item["quantity"],
                    filled_quantity=item.get("filled", "0"), limit_price=item.get("price", "10"),
                    **common,
                )
            result = owner.submit_service_call(lambda fact=fact: ledger.ingest(fact)).result(
                timeout_seconds=5
            )
            return result.disposition

        def commands():
            def read():
                with journal._transaction() as db:
                    rows = db.execute(
                        "SELECT strategy, request_key, principal, status FROM direct_journal_commands"
                    ).fetchall()
                return [list(row) for row in rows]

            return owner.submit_service_call(read).result(timeout_seconds=5)

        while True:
            try:
                item = controls.get_nowait()
            except queue.Empty:
                item = None
            if item == "stop":
                break
            if item is not None:
                tag, op = item["tag"], item["op"]
                try:
                    if op == "ingest":
                        reply = ingest(item)
                    elif op == "commands":
                        reply = commands()
                    elif op == "cancels":
                        reply = list(holder.get("cancels", []))
                    elif op == "pause":
                        # No serving at all for this long: every peer's call waits or times out.
                        messages.put((tag, "paused", None))
                        if item.get("hold"):
                            pause_release.wait(item["seconds"])
                        else:
                            time.sleep(item["seconds"])
                        reply = "resumed"
                    else:
                        raise ValueError("unknown fixture operation")
                    messages.put((tag, "ok", reply))
                except Exception as error:  # report, keep serving
                    messages.put((tag, "error", type(error).__name__ + ": " + str(error)))
            host.serve_once(10)
    except BaseException as error:
        import traceback

        messages.put(("error", type(error).__name__, str(error), traceback.format_exc()))
    finally:
        if host is not None and host.state == "started":
            host.close()
        if owner is not None:
            owner.close(5)
        messages.put(("modules", "gateway", os.getpid(), loaded_watched()))


# ============================================================================ strategy process


def _summary(result) -> dict:
    """Plain picklable view of a facade/client result (no objects cross processes)."""
    if result is None:
        return {"outcome": None}
    out = {"outcome": getattr(result, "outcome", None), "code": getattr(result, "code", None)}
    for name in ("risk_code", "risk_denied"):
        if hasattr(result, name):
            out[name] = getattr(result, name)
    record = getattr(result, "record", None)
    if record is not None:
        out["state"] = getattr(record, "state", None)
        for name in ("permit_id", "reservation_id", "view_digest", "request_key", "operation_id"):
            if hasattr(record, name):
                out[name] = getattr(record, name)
    observation = getattr(result, "observation", None)
    if observation is not None:
        out["state"] = getattr(observation, "state", None)
        out["actor_principal"] = getattr(observation, "actor_principal", None)
    answer = getattr(result, "risk_answer", None) or getattr(result, "answer", None)
    if answer is not None:
        out["answer"] = {
            name: getattr(answer, name, None)
            for name in ("outcome", "state", "code", "gateway_code", "permit_id",
                         "reservation_id", "view_digest", "policy_version")
        }
    return out


def order_request(key, cid, *, side="buy", offset="open", quantity="1", price="10", symbol="rb"):
    from bt_api_py._contracts.models import OrderRequest, OrderType, Side

    return OrderRequest(
        symbol=symbol, side=Side.BUY if side == "buy" else Side.SELL,
        order_type=OrderType.LIMIT, quantity=Decimal(quantity), price=Decimal(price),
        account_id=ACCOUNT, client_order_id=cid, idempotency_key=key, offset=offset,
        quantity_unit="lots", exchange_id="SHFE",
    )


def strategy_process(which, gateway_endpoint, risk_endpoint, keys, journal_id, commands, results):
    """One strategy process; executes test commands through its own SDK facade."""
    facade = None
    try:
        from bt_api_base.gateway_direct_protocol import GatewayCommandBinding

        from bt_api_py.gateway_api import GatewayBtApi
        from bt_api_py.gateway_direct_client import GatewayClientRoute
        from bt_api_py.gateway_strategy_client import GatewayStrategyClientSession
        from bt_api_py.service_runtime import ClientCurveKeys
        from tests.integration.test_iter63_gateway_direct_service import _client_config
        from tests.integration.test_iter64_risk_service_process import _strategy_host
        from tests.unit.test_iter63_gateway_strategy_delegated import Host as CancelHost

        identity = STRATEGIES[which]
        scope = _scope(which)
        selected = which in RISK_SELECTED
        operations = (
            {"direct.reserve", "direct.dispatch", "direct.command_status",
             "direct.delegated_command_status", "direct.delegated_cancel",
             "direct.delegated_cancel_status"}
            if selected
            else {"direct.reserve", "direct.dispatch", "direct.command_status"}
        )
        session = GatewayStrategyClientSession(
            _client_config(gateway_endpoint, identity),
            binding=GatewayCommandBinding(journal_id, ACCOUNT, identity.actor_ref,
                                          identity.principal_ref),
            route=GatewayClientRoute("broker.direct_write", GATEWAY.actor_ref, "gateway-a",
                                     "scope-a", scope, identity),
            peer=GATEWAY,
            security_profiles={"security-a": ClientCurveKeys(keys["gateway_server"][0],
                                                             *keys["strategy_" + which])},
            host=_strategy_host(),
            delegated_cancel_host=CancelHost() if selected else None,
            requested_operations=frozenset(operations),
        )
        risk_client = risk_client_for(which, risk_endpoint, keys) if selected else None
        facade = GatewayBtApi(session, exchange_name="CTP", owns_session=True,
                              risk_client=risk_client)
        snapshot = facade.start(timeout_seconds=15)
        results.put(("ready", which, os.getpid(), bool(snapshot.healthy),
                     None if risk_client is None else facade.risk_available))
        while True:
            tag, op, args = commands.get()
            if op == "exit":
                break
            try:
                reply = _strategy_op(facade, risk_client, op, args)
                results.put((tag, "ok", reply))
            except Exception as error:
                results.put((tag, "error", type(error).__name__ + ": " + str(error)))
    except BaseException as error:
        import traceback

        results.put(("error", which, type(error).__name__, str(error), traceback.format_exc()))
    finally:
        if facade is not None:
            with contextlib.suppress(Exception):  # the report below is what the test reads
                facade.close(timeout_seconds=5)
        results.put(("modules", which, os.getpid(), loaded_watched()))


def _strategy_op(facade, risk_client, op, args):
    if op == "place":
        request = order_request(args["key"], args["cid"], side=args.get("side", "buy"),
                                offset=args.get("offset", "open"),
                                quantity=args.get("quantity", "1"),
                                price=args.get("price", "10"), symbol=args.get("symbol", "rb"))
        reserved = facade.reserve_order("CTP", request, ttl_seconds=20, timeout_seconds=15)
        out = {"reserve": _summary(reserved)}
        if reserved.outcome == "OBSERVED" and getattr(reserved.record, "state", None) == "RESERVED":
            out["dispatch"] = _summary(
                facade.dispatch_order("CTP", args["key"], timeout_seconds=15)
            )
        return out
    if op == "reserve":
        request = order_request(args["key"], args["cid"], side=args.get("side", "buy"),
                                offset=args.get("offset", "open"),
                                quantity=args.get("quantity", "1"),
                                price=args.get("price", "10"), symbol=args.get("symbol", "rb"))
        return _summary(facade.reserve_order("CTP", request, ttl_seconds=20, timeout_seconds=15))
    if op == "dispatch":
        return _summary(facade.dispatch_order("CTP", args["key"], timeout_seconds=15))
    if op == "command_status":
        return _summary(facade.command_status("CTP", args["key"], timeout_seconds=10))
    if op == "risk_status":
        return _summary(facade.risk_order_status("CTP", args["key"], timeout_seconds=10))
    if op == "cancel":
        return _summary(facade.cancel_order("CTP", args["key"], operation_id=args["operation_id"],
                                            timeout_seconds=10))
    if op == "cancel_status":
        return _summary(facade.cancel_status("CTP", args["operation_id"], timeout_seconds=10))
    if op == "risk_available":
        return facade.risk_available
    if op == "risk_reserve_raw":
        request = order_request(args["key"], args["cid"], side=args.get("side", "buy"),
                                offset=args.get("offset", "open"),
                                quantity=args.get("quantity", "1"),
                                price=args.get("price", "10"), symbol=args.get("symbol", "rb"))
        return _summary(risk_client.reserve(request, request_key=args["request_key"],
                                            timeout_seconds=15))
    if op == "risk_dispatch_raw":
        return _summary(risk_client.dispatch(args["key"], permit_id=args["permit_id"],
                                             reservation_id=args["reservation_id"],
                                             view_digest=args["view_digest"], timeout_seconds=15))
    if op == "risk_service_status":
        status = risk_client.service_status(timeout_seconds=10)
        return {"outcome": status.outcome, "code": status.code,
                "available": None if status.status is None else status.status.available,
                "refusal_counts": None if status.status is None
                else dict(status.status.refusal_counts)}
    raise ValueError("unknown strategy operation " + op)


def risk_client_for(which, risk_endpoint, keys, *, timeout_ms=15000):
    from bt_api_py.risk_service_client import RiskClientRoute, RiskServiceClient
    from bt_api_py.service_config import parse_client_config
    from bt_api_py.service_runtime import ClientCurveKeys

    identity = STRATEGIES[which]
    config = parse_client_config(
        {
            "client_config_schema_version": 1, "process_role": "strategy_client",
            "registration": {"strategy_ref": identity.actor_ref},
            "clients": {"risk": {"config_schema_version": 1, "settings": {
                "bindings": [{"mount": "broker.risk_order", "service_id": RISK_HOST.actor_ref,
                              "instance_id": "risk-a", "scope_ref": "risk-scope",
                              "account_ref": ACCOUNT, "transport_ref": "risk-transport",
                              "required": True}],
                "options": {"request_timeout_ms": timeout_ms}}}},
            "communication": {"config_schema_version": 1, "profiles": {"risk-transport": {
                "backend": "zmq", "config_schema_version": 1,
                "endpoints": {"rpc_connect": risk_endpoint}, "security_profile_ref": "risk-curve"}}},
        }
    )
    route = RiskClientRoute("broker.risk_order", RISK_HOST.actor_ref, "risk-a", "risk-scope",
                            _scope(which), identity)
    return RiskServiceClient.compose(
        config, route=route, peer=RISK_HOST,
        security_profiles={"risk-curve": ClientCurveKeys(keys["risk_server"][0],
                                                         *keys["strategy_" + which])},
        requested_operations=frozenset(STRATEGY_RISK_OPS),
    )


# ============================================================================ test-process side


def free_endpoint() -> str:
    from tests.integration.test_service_account_instrument_metadata import _free_endpoint

    return _free_endpoint()


def until(read, done, seconds=15.0, step=0.05):
    deadline = time.monotonic() + seconds
    while True:
        value = read()
        if done(value) or time.monotonic() >= deadline:
            return value
        time.sleep(step)


BASE_POLICY = (
    "schema: bt_api_risk.policy.v1\n"
    "policy_id: it-64-09-scenarios\n"
    "account: {route: CTP___FUTURE, account_scope: " + ACCOUNT + "}\n"
    "per_order:\n"
    "  max_quantity: {strategy-a: {rb: \"1\", hc: \"1\"}, strategy-b: {rb: \"2\", hc: \"2\"}}\n"
    "closable_check: {enabled: false}\n"
    # These process scenarios exercise other rules with R-05 explicitly disabled.
    # The Gateway currently has no complete active-orders coverage proof.
    "self_trade: {enabled: false, on_unverifiable: deny}\n"
)


class RiskScenarioEnv:
    """Owns every process of one scenario and the operator clients of the test process."""

    def __init__(self, tmp_path: Path, *, policy: str = BASE_POLICY, trusted_rules=None,
                 bootstrap: bool = True) -> None:
        import zmq

        self.root = Path(tmp_path)
        self.keys = {name: zmq.curve_keypair() for name in KEY_NAMES}
        self.publics = {self.keys["strategy_" + w][0]: STRATEGIES[w].principal_ref for w in STRATEGIES}
        self.publics[self.keys["risk_delegate"][0]] = RISK.principal_ref
        self.publics[self.keys["risk_control"][0]] = CONTROL.principal_ref
        self.publics[self.keys["gateway_operator"][0]] = GW_OPERATOR.principal_ref
        self.gateway_endpoint, self.risk_endpoint = free_endpoint(), free_endpoint()
        self.policy, self.trusted_rules = policy, dict(trusted_rules or {})
        self.context = multiprocessing.get_context("spawn")
        self.messages, self.controls = self.context.Queue(), self.context.Queue()
        self.pause_release = self.context.Event()
        self.results = self.context.Queue()
        self.gateway = None
        self.risk = None
        self.risk_exits: list[int] = []
        self.strategies: dict[str, tuple] = {}
        self.modules: dict[str, list] = {}
        self.journal_id = None
        self._admin = self._control = None
        self._pending: dict[str, tuple] = {}
        self.bootstrap = bootstrap

    # ------------------------------------------------------------------ lifecycle

    def start(self, *, strategies=("a", "b", "c")) -> RiskScenarioEnv:
        gateway_root = self.root / "gateway"
        gateway_root.mkdir()
        (self.root / "risk").mkdir()
        self.gateway = self.context.Process(
            target=gateway_process,
            args=(str(gateway_root), self.gateway_endpoint, self.keys["gateway_server"],
                  self.publics, self.messages, self.controls, self.pause_release),
        )
        self.gateway.start()
        ready = self.messages.get(timeout=60)
        assert ready[0] == "ready", "\n".join(map(str, ready))
        self.journal_id = ready[2]
        if "c" in strategies:
            self.start_strategy("c")
            if self.bootstrap:
                # C's own working order on hc gives Risk the trading day through the
                # observation stream (no market hub in this fixture) and is a bypass order.
                placed = self.call("c", "place", key="c-boot", cid="c-boot", symbol="hc",
                                   price="1")
                assert placed["dispatch"]["outcome"] in ("QUEUED", "OBSERVED"), placed
                self.wait_gateway_state("c-boot", "SENT")
                assert self.ingest("c-boot", "accepted", symbol="hc", price="1") == "ATTRIBUTED"
        self.write_risk_files()
        self.start_risk()
        for which in strategies:
            if which != "c":
                self.start_strategy(which)
        return self

    def close(self) -> None:
        if getattr(self, "_closed", False):
            return
        self._closed = True
        self.pause_release.set()  # release a test-owned pause if an assertion failed
        for client in (self._admin, self._control):
            if client is not None:
                with contextlib.suppress(Exception):  # test-process client cleanup only
                    client.close(timeout_seconds=5)
        self._admin = self._control = None
        for which in list(self.strategies):
            self.stop_strategy(which)
        if self.risk is not None:
            self.stop_risk()
        if self.gateway is not None:
            self.controls.put("stop")
            self.gateway.join(30)
            while True:
                try:
                    item = self.messages.get(timeout=0.5)
                except queue.Empty:
                    break
                if item[0] == "modules":
                    self.modules["gateway"] = item[3]
            if self.gateway.is_alive():
                self.gateway.kill()
                self.gateway.join(10)

    # ------------------------------------------------------------------ Gateway fixture

    def _gateway_request(self, op, timeout=30, **fields):
        tag = uuid.uuid4().hex
        self.controls.put({"tag": tag, "op": op, **fields})
        deadline = time.monotonic() + timeout
        while True:
            item = self.messages.get(timeout=max(0.1, deadline - time.monotonic()))
            if item[0] == tag:
                assert item[1] == "ok", item
                return item[2]
            if item[0] == "error":
                raise AssertionError("\n".join(map(str, item)))

    def ingest(self, cid, status, *, quantity="1", filled="0", side="buy", offset="open",
               price="10", symbol="rb"):
        return self._gateway_request("ingest", cid=cid, status=status, quantity=quantity,
                                     filled=filled, side=side, offset=offset, price=price,
                                     symbol=symbol)

    def ingest_trade(self, cid, trade_id, *, quantity="1", side="buy", offset="open",
                     price="10", symbol="rb"):
        return self._gateway_request("ingest", kind="trade", cid=cid, trade_id=trade_id,
                                     quantity=quantity, side=side, offset=offset, price=price,
                                     symbol=symbol)

    def gateway_commands(self) -> dict:
        """``{request_key: (strategy, principal, status)}`` from the Gateway journal."""
        rows = self._gateway_request("commands")
        return {key: (strategy, principal, status) for strategy, key, principal, status in rows}

    def gateway_cancels(self) -> list:
        return self._gateway_request("cancels")

    def pause_gateway(self, seconds, *, hold=False) -> None:
        """Wait until Gateway is paused; optionally hold until explicitly resumed."""
        tag = uuid.uuid4().hex
        self.pause_release.clear()
        self.controls.put({"tag": tag, "op": "pause", "seconds": seconds, "hold": hold})
        self._pending["pause"] = (tag, time.monotonic() + seconds)
        while True:
            item = self.messages.get(timeout=30)
            if item[0] == tag and item[1] == "paused":
                return
            if item[0] == "error":
                raise AssertionError("\n".join(map(str, item)))

    def wait_gateway_resumed(self) -> None:
        self.pause_release.set()
        tag, _ = self._pending.pop("pause")
        while True:
            item = self.messages.get(timeout=60)
            if item[0] == tag:
                return

    def wait_gateway_state(self, key, state, seconds=15.0):
        value = until(lambda: self.gateway_commands().get(key), lambda r: r is not None and r[2] == state,
                      seconds)
        assert value is not None and value[2] == state, (key, value)
        return value

    # ------------------------------------------------------------------ strategies

    def start_strategy(self, which) -> None:
        commands, results = self.context.Queue(), self.context.Queue()
        process = self.context.Process(
            target=strategy_process,
            args=(which, self.gateway_endpoint, self.risk_endpoint, self.keys, self.journal_id,
                  commands, results),
        )
        process.start()
        ready = results.get(timeout=60)
        assert ready[0] == "ready" and ready[3] is True, "\n".join(map(str, ready))
        self.strategies[which] = (process, commands, results)

    def call(self, which, op, timeout=60, **args):
        _, commands, results = self.strategies[which]
        tag = uuid.uuid4().hex
        commands.put((tag, op, args))
        while True:
            item = results.get(timeout=timeout)
            if item[0] == tag:
                assert item[1] == "ok", item
                return item[2]
            if item[0] == "error":
                raise AssertionError("\n".join(map(str, item)))

    def wait_risk_route(self, which, *, available=True, seconds=60.0):
        """Wait until the strategy's Risk route is (un)available, using reads only.

        ``risk_status`` of a probe key goes through the facade, which re-registers a
        lost Risk route (bounded backoff) before reading; it never places anything.
        """
        def read():
            if not available:
                return self.call(which, "risk_available")
            return self.call(which, "risk_status", key="route-probe")

        def done(value):
            if not available:
                return value is False
            return value["outcome"] == "ANSWERED" and value["answer"]["outcome"] == "NOT_FOUND"

        value = until(read, done, seconds, step=0.2)
        assert done(value), (which, value)
        return value

    def stop_strategy(self, which) -> None:
        process, commands, results = self.strategies.pop(which)
        commands.put((None, "exit", {}))
        process.join(30)
        while True:
            try:
                item = results.get(timeout=0.5)
            except queue.Empty:
                break
            if item[0] == "modules":
                self.modules["strategy_" + which] = item[3]
        if process.is_alive():
            process.kill()
            process.join(10)

    # ------------------------------------------------------------------ Risk host

    def write_risk_files(self) -> Path:
        root = self.root / "risk"
        key_dir = root / "keys"
        key_dir.mkdir(exist_ok=True)
        names = {
            "risk_server": ("risk_server", True), "risk_delegate": ("risk_delegate", True),
            "risk_control": ("risk_control", True), "gateway_server": ("gateway_server", False),
            "risk_operator": ("operator", False), "strategy_a": ("strategy_a", False),
            "strategy_b": ("strategy_b", False),
        }
        for name, (file_name, with_secret) in names.items():
            public, secret = self.keys[name]
            (key_dir / (file_name + ".public")).write_bytes(public + b"\n")
            os.chmod(key_dir / (file_name + ".public"), 0o644)
            if with_secret:
                (key_dir / (file_name + ".secret")).write_bytes(secret + b"\n")
                os.chmod(key_dir / (file_name + ".secret"), 0o600)
        (root / "risk_policy.yaml").write_text(self.policy, encoding="utf-8")
        principals = [
            {"principal_ref": STRATEGIES[w].principal_ref, "role": "strategy",
             "actor_ref": STRATEGIES[w].actor_ref, "strategy_refs": [STRATEGIES[w].actor_ref],
             "operations": list(STRATEGY_RISK_OPS),
             "public_key_file": "keys/strategy_" + w + ".public"}
            for w in RISK_SELECTED
        ]
        principals.append(
            {"principal_ref": RISK_OPERATOR.principal_ref, "role": "operator",
             "actor_ref": RISK_OPERATOR.actor_ref, "strategy_refs": [],
             "operations": list(RISK_ADMIN_OPS), "public_key_file": "keys/operator.public"}
        )
        config = {
            "schema": "bt_api_risk.service.v1",
            "service": {
                "service_id": RISK_HOST.actor_ref, "principal_ref": RISK_HOST.principal_ref,
                "instance_id": "risk-a", "scope_ref": "risk-scope", "rpc_bind": self.risk_endpoint,
                "public_key_file": "keys/risk_server.public",
                "secret_key_file": "keys/risk_server.secret",
            },
            "account": {
                "route": "CTP___FUTURE", "provider": "ctp", "environment": "simnow",
                "account_scope": ACCOUNT, "execution_domain_ref": DOMAIN,
                "instruments": {
                    symbol: {"venue": "SHFE", "native_symbol": symbol, "product_type": "future",
                             "multiplier": "10", "tick_size": "1"}
                    for symbol in ("rb", "hc")
                },
            },
            "storage": {"directory": "state"},
            "policy": {"file": "risk_policy.yaml"},
            "principals": principals,
            "gateway": {
                "service_id": GATEWAY.actor_ref, "principal_ref": GATEWAY.principal_ref,
                "instance_id": "gateway-a", "scope_ref": "scope-a",
                "rpc_endpoint": self.gateway_endpoint,
                "server_public_key_file": "keys/gateway_server.public",
                "delegate": {"principal_ref": RISK.principal_ref, "actor_ref": RISK.actor_ref,
                             "public_key_file": "keys/risk_delegate.public",
                             "secret_key_file": "keys/risk_delegate.secret",
                             "grant_revisions": {STRATEGIES[w].actor_ref: "risk-v1"
                                                 for w in RISK_SELECTED}},
                "control": {"principal_ref": CONTROL.principal_ref, "actor_ref": CONTROL.actor_ref,
                            "public_key_file": "keys/risk_control.public",
                            "secret_key_file": "keys/risk_control.secret"},
            },
            "deadlines": {"call_timeout_seconds": 3, "reservation_ttl_seconds": 20,
                          "dispatch_recheck_seconds": 5},
            "refresh": {"loop_timeout_ms": 20, "observation_poll_seconds": "0.1",
                        "maintain_seconds": 1, "dispatch_recheck_limit": 8},
        }
        if self.trusted_rules:
            config["rules"] = {"trusted_implementations": self.trusted_rules}
        path = root / "risk_service.json"
        path.write_text(json.dumps(config), encoding="utf-8")
        self.risk_config = path
        return path

    def start_risk(self, *, environ=None, expect_ready=True):
        root = self.root / "risk"
        ready_file = root / "ready.json"
        if ready_file.exists():
            ready_file.unlink()
        env = dict(os.environ)
        env["PYTHONDONTWRITEBYTECODE"] = "1"
        env.update(environ or {})
        self.risk = subprocess.Popen(
            [sys.executable, "-m", "bt_api_py.risk_service_host", "run", "--config",
             str(self.risk_config), "--ready-file", str(ready_file)],
            cwd=str(SDK_ROOT), env=env, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
        )
        if not expect_ready:
            return self.risk
        deadline = time.monotonic() + 60
        while not ready_file.exists():
            assert self.risk.poll() is None, self.risk.communicate(timeout=10)
            assert time.monotonic() < deadline, "Risk host did not become ready"
            time.sleep(0.05)
        return self.risk

    def stop_risk(self) -> int:
        if self._admin is not None:
            with contextlib.suppress(Exception):  # the Risk host goes away anyway
                self._admin.close(timeout_seconds=5)
            self._admin = None
        process, self.risk = self.risk, None
        process.terminate()
        try:
            code = process.wait(timeout=30)
        except subprocess.TimeoutExpired:
            process.kill()
            code = process.wait(timeout=10)
        self.risk_exits.append(code)
        self.risk_stderr = process.stderr.read().decode("utf-8", "replace")
        return code

    def restart_risk(self) -> None:
        assert self.stop_risk() == 0, self.risk_stderr
        self.start_risk()

    # ------------------------------------------------------------------ operator clients

    def _application_runtime(self, identity, keys, server_key, endpoint, service, mount,
                             operations, scope_ref, instance_id, alias):
        from bt_api_py.service_config import parse_client_config
        from bt_api_py.service_runtime import (
            ClientCurveKeys,
            ServiceClientRuntime,
            ZmqTransportFactory,
        )
        from bt_api_py.service_runtime_thread import ThreadedServiceClientRuntime

        config = parse_client_config(
            {
                "client_config_schema_version": 1, "process_role": "application_client",
                "registration": {"application_ref": identity.actor_ref},
                "clients": {alias: {"config_schema_version": 1, "settings": {"bindings": [{
                    "mount": mount, "service_id": service.actor_ref,
                    "instance_id": instance_id, "scope_ref": scope_ref, "account_ref": ACCOUNT,
                    "transport_ref": "transport"}], "options": {"request_timeout_ms": 30000}}}},
                "communication": {"config_schema_version": 1, "profiles": {"transport": {
                    "backend": "zmq", "config_schema_version": 1,
                    "endpoints": {"rpc_connect": endpoint}, "security_profile_ref": "curve"}}},
            }
        )
        factory = ZmqTransportFactory({"curve": ClientCurveKeys(server_key, *keys)})
        runtime = ThreadedServiceClientRuntime(lambda: ServiceClientRuntime(
            config, identity=identity, scope_resolver=lambda _: ACCOUNT_ONLY,
            peer_resolver=lambda _: service, transport_factory=factory,
            requested_operations={mount: frozenset(operations)}))
        assert runtime.start(timeout_seconds=15).healthy
        return runtime

    def admin(self, operation, arguments=None, timeout=15):
        """Risk operator call (the test process acts as the Risk operator)."""
        from bt_api_py.gateway_execution_stream_client import _detach_wire

        if self._admin is None:
            self._admin = self._application_runtime(
                RISK_OPERATOR, self.keys["risk_operator"], self.keys["risk_server"][0],
                self.risk_endpoint, RISK_HOST, "application.risk_admin", RISK_ADMIN_OPS,
                "risk-scope", "risk-a", "risk",
            )
        return _detach_wire(self._admin.call(
            "application.risk_admin", operation, arguments or {}, account_ref=ACCOUNT,
            scope_ref="risk-scope", timeout_seconds=timeout))

    def gateway_control(self, operation, arguments, timeout=15):
        """Gateway operator call (direct.control.*) with the operator's own control grant."""
        from bt_api_py.gateway_execution_stream_client import _detach_wire

        if self._control is None:
            self._control = self._application_runtime(
                GW_OPERATOR, self.keys["gateway_operator"], self.keys["gateway_server"][0],
                self.gateway_endpoint, GATEWAY, "application.direct_write",
                OPERATOR_CONTROL_OPS, "scope-a", "gateway-a", "gateway",
            )
        return _detach_wire(self._control.call(
            "application.direct_write", operation, arguments, account_ref=ACCOUNT,
            scope_ref="scope-a", timeout_seconds=timeout))

    def risk_summary(self) -> dict:
        reply = self.admin("risk.admin.status")
        assert reply["status"] == "OBSERVED", reply
        return reply["summary"]

    def wait_risk_verified(self, *, bypass_at_least=0, seconds=20.0) -> dict:
        summary = until(
            self.risk_summary,
            lambda s: s["observation"]["continuity"] == "VERIFIED"
            and s["bypass_orders"] >= bypass_at_least,
            seconds,
        )
        assert summary["observation"]["continuity"] == "VERIFIED", summary
        assert summary["bypass_orders"] >= bypass_at_least, summary
        return summary

    def audit(self) -> list[dict]:
        records, after = [], 0
        while True:
            reply = self.admin("risk.audit.read", {"after": after, "limit": 500})
            assert reply["status"] == "OBSERVED", reply
            records.extend(reply["records"])
            if len(reply["records"]) < 500:
                return records
            after = reply["next_after"]

    def risk_database(self) -> Path:
        return self.root / "risk" / "state" / "risk.sqlite"

    def copy_risk_database(self, name: str) -> Path:
        """A consistent copy of the Risk SQLite file (read without touching the live one)."""
        import sqlite3

        target = self.root / (name + ".sqlite")
        source = sqlite3.connect("file:" + str(self.risk_database()) + "?mode=ro", uri=True)
        try:
            with sqlite3.connect(target) as destination:
                source.backup(destination)
        finally:
            source.close()
        return target


def import_control_process(reports) -> None:
    """INV64-02 control: a process that imports the watched packages reports them."""
    import importlib
    import importlib.util

    for name in WATCHED:
        if importlib.util.find_spec(name) is not None:
            importlib.import_module(name)
    reports.put(("modules", "control", os.getpid(), loaded_watched()))


def remove_tree(path: Path) -> None:
    shutil.rmtree(path, ignore_errors=True)


__all__ = [
    "ACCOUNT",
    "BASE_POLICY",
    "CONTROL",
    "GATEWAY",
    "GW_OPERATOR",
    "RISK",
    "RISK_HOST",
    "RISK_OPERATOR",
    "RiskScenarioEnv",
    "STRATEGIES",
    "TRADING_DAY",
    "WATCHED",
    "gateway_process",
    "instrument",
    "loaded_watched",
    "order_request",
    "risk_client_for",
    "strategy_process",
    "until",
]
