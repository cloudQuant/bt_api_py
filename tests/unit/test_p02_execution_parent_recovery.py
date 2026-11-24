"""P0-2 regression tests for ExecutionRiskServiceClient, RiskParentPort,
ExecutionParentDispatcher reconciliation, ExecutionParentClient and ExecutionOrderRouter."""
from __future__ import annotations

import hashlib
import json
import os
import sqlite3
import uuid
from decimal import Decimal
from pathlib import Path
from types import SimpleNamespace

import pytest
import zmq
from bt_api_base.gateway_direct_protocol import GatewayCommandBinding
from bt_api_base.market_protocol import MarketInstrumentKey
from bt_api_base.risk_service_protocol import RiskOrderResult
from bt_api_base.service_protocol import WireIdentity, WireRole, WireScope, encode_wire_value
from bt_api_execution.account_journal_host import AccountJournalHost
from bt_api_execution.contracts import ExecutionScope, canonical_json
from bt_api_execution.store import SqliteExecutionStore

from bt_api_py.execution_parent_client import ExecutionParentClient, ExecutionParentObservation
from bt_api_py.execution_parent_dispatcher import (
    ExecutionParentDispatcher,
    ParentDispatchConfig,
    _UNKNOWN_NOT_FOUND_GRACE_NS,
)
from bt_api_py.execution_parent_grant import QUERY, SUBMIT
from bt_api_py.execution_risk_client import ExecutionRiskClientConfig, ExecutionRiskServiceClient
from bt_api_py.execution_routing import (
    ExecutionOrderRouter,
    ExecutionRoutingOutcome,
    install_execution_routing_tables,
    parse_execution_routing,
)
from bt_api_py.gateway_direct_client import GatewayClientRoute, build_gateway_order_intent
from bt_api_py.risk_parent_port import RiskParentPort
from bt_api_py.risk_service_client import RiskParentCallResult
from bt_api_py.service_runtime import (
    ClientCurveKeys,
    ServiceRuntimeError,
    ServiceRuntimeRequestNotSentError,
)
from bt_api_py.service_runtime_thread import ThreadedRuntimeError
from bt_api_py._contracts.models import OrderRequest, OrderType, Side
from tests.unit.test_iter65_execution_parent_client import request as sample_parent_request
from tests.unit.test_iter65_execution_parent_client import setup_synthetic


INSTRUMENT = MarketInstrumentKey("ctp", "SHFE", "simnow", "future", "rb", "rb2701")


def _owner_only(path: Path) -> None:
    if os.name == "nt":
        import win32con
        import win32security

        sd = win32security.GetFileSecurity(str(path), win32security.OWNER_SECURITY_INFORMATION)
        owner = sd.GetSecurityDescriptorOwner()
        dacl = win32security.ACL()
        dacl.AddAccessAllowedAce(win32security.ACL_REVISION, win32con.GENERIC_ALL, owner)
        sd.SetSecurityDescriptorDacl(1, dacl, 0)
        win32security.SetFileSecurity(str(path), win32security.DACL_SECURITY_INFORMATION, sd)
    else:
        os.chmod(path, 0o700)


def _make_risk_client() -> ExecutionRiskServiceClient:
    srv_pub, _ = zmq.curve_keypair()
    cli_pub, cli_sec = zmq.curve_keypair()
    cfg = ExecutionRiskClientConfig(
        service_id="exec-svc",
        identity=WireIdentity(WireRole.SERVICE, "exec-principal", "exec-svc"),
        scope=WireScope("paper:simnow", account_ref="acct-1", strategy_ref="strat-1", run_id="run-1"),
        risk_service_id="risk-svc",
        risk_instance_id="risk-inst",
        risk_scope_ref="risk-scope",
        risk_endpoint="tcp://127.0.0.1:59999",
        risk_peer=WireIdentity(WireRole.SERVICE, "risk-principal", "risk-svc"),
        curve_keys=ClientCurveKeys(srv_pub, cli_pub, cli_sec),
    )
    return ExecutionRiskServiceClient(cfg)


def test_execution_risk_client_distinguishes_not_sent_vs_unknown_and_order_status(monkeypatch):
    client = _make_risk_client()
    # 1. Not started -> ROUTE_UNAVAILABLE -> NOT_SENT
    res = client.parent_status("p-1")
    assert res == RiskParentCallResult("NOT_SENT", code="ROUTE_UNAVAILABLE")

    # Stub healthy registration
    monkeypatch.setattr(client, "_registration", lambda op: ("reg-1", "epoch-1"))

    # 2. ServiceRuntimeRequestNotSentError -> NOT_SENT
    monkeypatch.setattr(
        client._runtime,
        "call",
        lambda *a, **kw: (_ for _ in ()).throw(ServiceRuntimeRequestNotSentError()),
    )
    assert client.parent_status("p-1").outcome == "NOT_SENT"

    # 3. Pre-send ThreadedRuntimeError -> NOT_SENT; post-send/timeout -> UNKNOWN
    monkeypatch.setattr(
        client._runtime,
        "call",
        lambda *a, **kw: (_ for _ in ()).throw(ThreadedRuntimeError("NOT_READY")),
    )
    assert client.parent_status("p-1").outcome == "NOT_SENT"

    monkeypatch.setattr(
        client._runtime,
        "call",
        lambda *a, **kw: (_ for _ in ()).throw(ThreadedRuntimeError("TIMEOUT")),
    )
    assert client.parent_status("p-1").outcome == "UNKNOWN"

    # 4. Registry pre-handler refusal on unchanged registration -> NOT_SENT
    refusal = ServiceRuntimeError("STALE_EPOCH")
    refusal.service_reply_code = "STALE_EPOCH"
    monkeypatch.setattr(
        client._runtime,
        "call",
        lambda *a, **kw: (_ for _ in ()).throw(refusal),
    )
    assert client.parent_status("p-1") == RiskParentCallResult("NOT_SENT", code="STALE_EPOCH")

    # 5. order_status parses matching key and fails closed on mismatched key
    monkeypatch.setattr(
        client._runtime,
        "call",
        lambda *a, **kw: RiskOrderResult("exec-child-1", "NOT_FOUND").to_mapping(),
    )
    assert client.order_status("exec-child-1").outcome == "NOT_FOUND"
    assert client.order_status("exec-child-other").outcome == "UNKNOWN"


def test_risk_parent_port_maps_not_sent_to_rejected_and_preserves_unknown():
    class _StubClient:
        def __init__(self, result):
            self.result = result
            self._route = SimpleNamespace(scope=SimpleNamespace(run_id="run-1"))

        def available(self):
            return True

        def parent_reserve(self, _view):
            return self.result

    payload = {
        "spec": {
            "parent_ref": "p-1",
            "account_scope": "acct",
            "strategy_scope": "s",
            "execution_domain_ref": "paper:simnow",
            "instrument": INSTRUMENT.to_dict(),
            "quantity": "1",
            "quantity_unit": "lots",
            "side": "buy",
            "offset": "open",
            "limit_price": "3500",
            "creation_trading_day": "20261124",
            "allowed_trading_days": ["20261124"],
        },
        "strategy": {"strategy_ref": "s", "run_id": "run-1"},
    }

    not_sent_port = RiskParentPort(_StubClient(RiskParentCallResult("NOT_SENT", code="ROUTE_UNAVAILABLE")), route="CTP___FUTURE")
    assert not_sent_port.reserve_parent(payload) == {"status": "REJECTED", "code": "ROUTE_UNAVAILABLE"}
    assert not_sent_port._parent_reserved("p-1") is False

    unknown_port = RiskParentPort(_StubClient(RiskParentCallResult("UNKNOWN", code="TIMEOUT")), route="CTP___FUTURE")
    assert unknown_port.reserve_parent(payload) == {"status": "UNKNOWN", "code": "TIMEOUT"}

    unknown_port.record_reserved_parent("p-rec")
    assert unknown_port._parent_reserved("p-rec") is True


def test_dispatcher_reconciles_unknown_parents_and_reserve_child_only(tmp_path):
    root = tmp_path / "journal"
    root.mkdir()
    _owner_only(root)
    keys_dir = tmp_path / "keys"
    keys_dir.mkdir()
    _owner_only(keys_dir)
    srv_pub_b, _ = zmq.curve_keypair()
    cli_pub_b, cli_sec_b = zmq.curve_keypair()
    srv_pub = keys_dir / "srv.pub"
    cli_pub = keys_dir / "cli.pub"
    cli_sec = keys_dir / "cli.sec"
    for p, b in ((srv_pub, srv_pub_b), (cli_pub, cli_pub_b), (cli_sec, cli_sec_b)):
        p.write_bytes(b)
        _owner_only(p)

    wire_scope = WireScope("paper:simnow", account_ref="acct-d", strategy_ref="strat-d", run_id="run-d")
    local_scope = ExecutionScope("ctp", "simnow", "acct-d", "strat-d")
    parent_scope_key = ExecutionScope("ctp", "simnow", "acct-d", "strat-d").key
    journal = AccountJournalHost(root=str(root), scope=local_scope, storage_id=str(uuid.uuid4()))
    journal.acquire_intake_lease(local_scope, "owner-d", ttl_ns=60_000_000_000)

    try:
        cfg = ParentDispatchConfig(
            route="CTP___FUTURE",
            risk_endpoint="tcp://127.0.0.1:59998",
            risk_service_id="risk-svc",
            risk_instance_id="risk-inst",
            risk_scope_ref="risk-scope",
            risk_peer=WireIdentity(WireRole.SERVICE, "risk-principal", "risk-svc"),
            risk_server_public=srv_pub,
            client_public=cli_pub,
            client_secret=cli_sec,
            poll_interval_seconds=0.01,
        )
        dispatcher = ExecutionParentDispatcher(
            dispatch_config=cfg,
            service_id="exec-svc",
            identity=WireIdentity(WireRole.SERVICE, "exec-principal", "exec-svc"),
            wire_scope=wire_scope,
            local_scope=local_scope,
            journal=journal,
        )

        now_ns = journal.intake_now_ns()
        old_ns = now_ns - _UNKNOWN_NOT_FOUND_GRACE_NS - 5_000_000_000
        fresh_ns = now_ns - 1_000_000_000

        def _insert_parent(parent_id, risk_state, gateway_state, *, gw_receipt=None, updated_at_ns=old_ns, child_state=None):
            spec = {
                "parent_ref": parent_id,
                "account_scope": "acct-d",
                "strategy_scope": "strat-d",
                "execution_domain_ref": "paper:simnow",
                "instrument": INSTRUMENT.to_dict(),
                "side": "buy",
                "offset": "open",
                "quantity": "1",
                "quantity_unit": "lots",
                "limit_price": "3500",
                "time_in_force": "DAY",
                "creation_trading_day": "20261124",
                "allowed_trading_days": ["20261124"],
            }
            req_json = canonical_json(spec)
            with journal.intake_scope(nonblocking=False) as store:
                with store._lock:
                    with SqliteExecutionStore._transaction(store) as cur:
                        cur.execute(
                            "INSERT INTO execution_parent_records "
                            "(scope_key, parent_id, account_key, request_json, request_sha256, "
                            "risk_operation_id, gateway_operation_id, risk_state, gateway_state, "
                            "cancel_state, risk_receipt_json, gateway_receipt_json, created_at_ns, updated_at_ns) "
                            "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                            (
                                parent_scope_key,
                                parent_id,
                                local_scope.account_key,
                                req_json,
                                hashlib.sha256(req_json.encode()).hexdigest(),
                                f"risk-{parent_id}",
                                f"gw-{parent_id}",
                                risk_state,
                                gateway_state,
                                "NONE",
                                None,
                                None if gw_receipt is None else canonical_json(gw_receipt),
                                updated_at_ns,
                                updated_at_ns,
                            ),
                        )
                        if child_state is not None:
                            cur.execute(
                                "INSERT INTO execution_parent_children "
                                "(scope_key, parent_id, child_id, request_json, request_sha256, "
                                "quantity, filled_quantity, state, updated_at_ns) "
                                "VALUES (?,?,?,?,?,?,?,?,?)",
                                (
                                    parent_scope_key,
                                    parent_id,
                                    f"{parent_id}:c0",
                                    canonical_json({"child_id": f"{parent_id}:c0"}),
                                    "0" * 64,
                                    "1",
                                    "0",
                                    child_state,
                                    updated_at_ns,
                                ),
                            )

        def _child_key(parent_id: str) -> str:
            digest = hashlib.sha256(f"{parent_id}:{parent_id}:c0".encode("utf-8")).hexdigest()[:24]
            return f"exec-child-{digest}"

        _insert_parent("p-denied", "UNKNOWN", "PENDING", updated_at_ns=fresh_ns)
        _insert_parent("p-reserved", "UNKNOWN", "PENDING", updated_at_ns=fresh_ns)
        _insert_parent("p-nf-fresh", "UNKNOWN", "PENDING", updated_at_ns=fresh_ns)
        _insert_parent("p-nf-old", "UNKNOWN", "PENDING", updated_at_ns=old_ns)
        _insert_parent("p-child-denied", "ACCEPTED", "UNKNOWN", gw_receipt={"stage": "reserve_child"}, updated_at_ns=fresh_ns, child_state="UNKNOWN")
        _insert_parent("p-child-nf-old", "ACCEPTED", "UNKNOWN", gw_receipt={"stage": "reserve_child"}, updated_at_ns=old_ns, child_state="UNKNOWN")
        _insert_parent("p-child-reserved", "ACCEPTED", "UNKNOWN", gw_receipt={"stage": "reserve_child"}, updated_at_ns=old_ns, child_state="UNKNOWN")
        _insert_parent("p-dispatch-unk", "ACCEPTED", "UNKNOWN", gw_receipt={"stage": "dispatch_child"}, updated_at_ns=old_ns, child_state="UNKNOWN")

        parent_answers = {
            "p-denied": RiskParentCallResult("ANSWERED", parent_ref="p-denied", parent_outcome="DENIED", code="risk_order_limit"),
            "p-reserved": RiskParentCallResult("ANSWERED", parent_ref="p-reserved", parent_outcome="RESERVED"),
            "p-nf-fresh": RiskParentCallResult("ANSWERED", parent_ref="p-nf-fresh", parent_outcome="NOT_FOUND"),
            "p-nf-old": RiskParentCallResult("ANSWERED", parent_ref="p-nf-old", parent_outcome="NOT_FOUND"),
        }
        order_answers = {
            _child_key("p-child-denied"): RiskOrderResult(_child_key("p-child-denied"), "DENIED", code="risk_facts_unverified"),
            _child_key("p-child-nf-old"): RiskOrderResult(_child_key("p-child-nf-old"), "NOT_FOUND"),
            _child_key("p-child-reserved"): RiskOrderResult(_child_key("p-child-reserved"), "RESERVED", permit_id="perm-1", reservation_id="res-1", view_digest="a" * 64),
            _child_key("p-dispatch-unk"): RiskOrderResult(_child_key("p-dispatch-unk"), "NOT_FOUND"),
        }

        dispatcher._client.parent_status = lambda pid: parent_answers[pid]
        dispatcher._client.order_status = lambda rk: order_answers[rk]

        count = dispatcher._reconcile_unknown_parents()
        assert count == 5

        def _row(pid):
            with journal.intake_scope(nonblocking=False) as store:
                with store._lock:
                    with SqliteExecutionStore._read_transaction(store) as cur:
                        p = cur.execute(
                            "SELECT risk_state, gateway_state FROM execution_parent_records WHERE scope_key=? AND parent_id=?",
                            (parent_scope_key, pid),
                        ).fetchone()
                        c = cur.execute(
                            "SELECT state FROM execution_parent_children WHERE scope_key=? AND parent_id=?",
                            (parent_scope_key, pid),
                        ).fetchone()
                        return p["risk_state"], p["gateway_state"], (None if c is None else c["state"])

        assert _row("p-denied") == ("REJECTED", "PENDING", None)
        assert _row("p-reserved") == ("ACCEPTED", "PENDING", None)
        assert dispatcher._risk_port._parent_reserved("p-reserved") is True
        assert _row("p-nf-fresh") == ("UNKNOWN", "PENDING", None)
        assert _row("p-nf-old") == ("REJECTED", "PENDING", None)
        assert _row("p-child-denied") == ("ACCEPTED", "REJECTED", "TERMINAL")
        assert _row("p-child-nf-old") == ("ACCEPTED", "REJECTED", "TERMINAL")
        # Reserved child and dispatch_child UNKNOWN MUST remain fail-closed UNKNOWN
        assert _row("p-child-reserved") == ("ACCEPTED", "UNKNOWN", "UNKNOWN")
        assert _row("p-dispatch-unk") == ("ACCEPTED", "UNKNOWN", "UNKNOWN")
    finally:
        journal.close()


def test_execution_parent_client_and_router_status_forward_rejected_only_on_query(monkeypatch, tmp_path):
    submitted = sample_parent_request()
    expected_digest = hashlib.sha256(encode_wire_value(submitted)).hexdigest()

    mode = {"query_rejected": True, "submit_extra": False}

    def handler(envelope):
        result = {
            "local_only": True,
            "status": "RECEIVED",
            "request_id": "original-id",
            "parent_ref": "original-parent",
            "request_sha256": expected_digest,
        }
        if envelope.header.operation == QUERY:
            result["found"] = True
            if mode["query_rejected"]:
                result["execution_state"] = "REJECTED"
        elif mode["submit_extra"]:
            result["execution_state"] = "REJECTED"
        return result

    wrapper, _, target, _ = setup_synthetic(monkeypatch, handler)
    try:
        client = ExecutionParentClient(wrapper, target=target)
        # Normal SUBMIT succeeds with execution_state=None
        obs_sub = client.submit(submitted)
        assert obs_sub == ExecutionParentObservation("LOCAL_RECEIVED", "original-id", expected_digest, "original-parent")

        # QUERY with execution_state='REJECTED' is accepted and surfaced
        obs_q = client.query(submitted)
        assert obs_q == ExecutionParentObservation(
            "LOCAL_RECEIVED", "original-id", expected_digest, "original-parent", execution_state="REJECTED"
        )

        # SUBMIT with extra execution_state='REJECTED' MUST be refused as UNKNOWN
        mode["submit_extra"] = True
        assert client.submit(submitted).outcome == "UNKNOWN"
    finally:
        wrapper.close()

    # Verify ExecutionOrderRouter.status() forwards execution_state='REJECTED'
    db = sqlite3.connect(":memory:", isolation_level=None)
    db.execute("BEGIN IMMEDIATE")
    install_execution_routing_tables(db)
    db.execute("COMMIT")
    key = MarketInstrumentKey("ctp", "SHFE", "simnow", "future", "rb", "rb2701")
    cfg = parse_execution_routing(
        {
            "schema_version": 2,
            "algorithm_build_id": "build-p02",
            "component": "exec-a",
            "account_ref": "synthetic-account",
            "strategy_ref": "synthetic-strategy",
            "execution_domain_ref": "synthetic-domain",
            "grant_revision": "grant-v1",
            "delegation": {"delegation_id": "del-1", "principal_ref": "strategy-principal"},
            "instruments": [key.to_dict()],
            "rules": [
                {
                    "id": "r-open",
                    "match": {
                        "instrument": key.to_dict(),
                        "side": "buy",
                        "offset": "open",
                        "order_types": ["limit"],
                        "minimum_quantity": "1",
                        "quantity_unit": "lots",
                    },
                    "schedule_binding": {"quantity_step": "1", "price_step": "1", "retry_limit": 0},
                    "scheme": {"name": "pass-v1", "version": 1},
                    "algorithm": {"class": "PASS", "version": 1, "params": {}},
                    "deadline": {"kind": "absolute", "at": "2099-12-31T08:00:00+08:00"},
                    "time_in_force": "DAY",
                    "trading_day": "20991231",
                    "allowed_trading_days": ["20991231"],
                }
            ],
        }
    )

    recorded = {}

    def router_handler(envelope):
        args = envelope.payload["arguments"]
        if envelope.header.operation == SUBMIT:
            recorded["request_id"] = args["request_id"]
            recorded["parent_ref"] = args["spec"]["parent_ref"]
            recorded["digest"] = hashlib.sha256(encode_wire_value(args)).hexdigest()
            return {
                "local_only": True,
                "status": "RECEIVED",
                "request_id": recorded["request_id"],
                "parent_ref": recorded["parent_ref"],
                "request_sha256": recorded["digest"],
            }
        return {
            "local_only": True,
            "found": True,
            "status": "RECEIVED",
            "request_id": recorded["request_id"],
            "parent_ref": recorded["parent_ref"],
            "request_sha256": recorded["digest"],
            "execution_state": "REJECTED",
        }

    wrapper2, _, target2, _ = setup_synthetic(monkeypatch, router_handler)
    try:
        port = ExecutionParentClient(wrapper2, target=target2)
        router = ExecutionOrderRouter(db=db, config=cfg, execution_port=port, risk_selected=True)
        binding = GatewayCommandBinding("journal-1", "synthetic-account", "synthetic-strategy", "strategy-principal")
        route = GatewayClientRoute(
            "broker.direct_write",
            "gateway",
            "gw-1",
            "scope-1",
            WireScope("synthetic-domain", account_ref="synthetic-account", strategy_ref="synthetic-strategy", run_id="run-1"),
            WireIdentity(WireRole.STRATEGY_CLIENT, "strategy-principal", "synthetic-strategy"),
        )
        req = OrderRequest(
            symbol="rb2701",
            side=Side.BUY,
            order_type=OrderType.LIMIT,
            quantity=Decimal("1"),
            price=Decimal("3500"),
            account_id="synthetic-account",
            client_order_id="bt-p02-1",
            idempotency_key="bt-p02-1",
            quantity_unit="lots",
            time_in_force="DAY",
            offset="open",
            exchange_id="SHFE",
        )
        intent = build_gateway_order_intent(binding, route, req)
        submitted_res = router.route(intent)
        assert submitted_res.outcome is ExecutionRoutingOutcome.EXECUTION_LOCAL_RECEIPT
        status_res = router.status(intent)
        assert status_res.routed is True
        assert status_res.outcome is ExecutionRoutingOutcome.EXECUTION_LOCAL_RECEIPT
        assert status_res.execution_state == "REJECTED"
    finally:
        wrapper2.close()
        db.close()
