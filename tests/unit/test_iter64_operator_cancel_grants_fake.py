"""Operator cancellation grants apply to actual SDK and Risk calls, without I/O."""

import inspect
import sqlite3
from contextlib import contextmanager
from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace

import pytest
from bt_api_base.service_protocol import WireIdentity, WireRole, WireScope
from bt_api_risk.core.admission import AccountScope
from bt_api_risk.pretrade import CallerIdentity, RiskAdminError, ledger
from bt_api_risk.pretrade.engine import PretradeEngine

from bt_api_py import risk_service_config as config
from bt_api_py.risk_service_component import RiskOrderService
from bt_api_py.risk_service_config import RiskPrincipal
from bt_api_py.risk_service_gateway import GatewayControlAdapter
from bt_api_py.service_registry import ServiceCallContext

OPS = frozenset({"risk.cancel.request", "risk.cancel.status"})
SCOPE = AccountScope("synthetic", "account", "simulation")


def document():
    return {
        "schema": "bt_api_risk.service.v1",
        "service": {"service_id": "risk-service", "principal_ref": "risk-host", "instance_id": "risk-a",
                    "scope_ref": "risk-scope", "rpc_bind": "tcp://example.invalid:1",
                    "public_key_file": "server.pub", "secret_key_file": "server.sec"},
        "account": {"route": "route", "provider": "ctp", "environment": "demo", "account_scope": "account",
                    "execution_domain_ref": "domain", "instruments": {
                        "rb": {"venue": "SHFE", "native_symbol": "rb", "product_type": "future"}}},
        "storage": {"directory": "state"}, "policy": {"file": "policy.yaml"},
        "principals": [
            {"principal_ref": "strategy", "role": "strategy", "actor_ref": "s", "strategy_refs": ["s"],
             "operations": ["risk.status"], "public_key_file": "s.pub"},
            {"principal_ref": "operator", "role": "operator", "actor_ref": "ops", "strategy_refs": [],
             "operations": ["risk.admin.status"], "public_key_file": "op.pub"},
        ],
        "gateway": {"service_id": "gateway", "principal_ref": "gateway-principal", "instance_id": "g",
                    "scope_ref": "scope", "rpc_endpoint": "tcp://example.invalid:2",
                    "server_public_key_file": "gateway.pub",
                    "delegate": {"principal_ref": "delegate", "actor_ref": "delegate-actor",
                                 "public_key_file": "delegate.pub", "secret_key_file": "delegate.sec",
                                 "grant_revisions": {"s": "revision"}},
                    "control": {"principal_ref": "control", "actor_ref": "control-actor",
                                "public_key_file": "control.pub", "secret_key_file": "control.sec"}},
        "deadlines": {"call_timeout_seconds": 5, "reservation_ttl_seconds": "10", "dispatch_recheck_seconds": 5},
        "refresh": {"loop_timeout_ms": 20, "observation_poll_seconds": "0.2", "maintain_seconds": 1,
                    "dispatch_recheck_limit": 8},
    }


class MemoryLedger:
    def __init__(self, txn):
        self.txn = txn

    @contextmanager
    def transaction(self):
        yield self.txn


@contextmanager
def setup_service(principals):
    connection = sqlite3.connect(":memory:")
    try:
        connection.execute("PRAGMA temp_store=MEMORY")
        assert connection.execute("PRAGMA temp_store").fetchone()[0] == 2
        assert connection.execute("PRAGMA journal_mode=MEMORY").fetchone()[0] == "memory"
        assert connection.execute("PRAGMA database_list").fetchone()[2] == ""
        connection.row_factory = sqlite3.Row
        for statement in ledger._split_statements(ledger._SCHEMA):
            connection.execute(statement)
        fence = SimpleNamespace(scope=SCOPE, owner_id="synthetic", fencing_token=1)
        args = (connection, SCOPE.key, fence, None)
        if "schema_version" in inspect.signature(ledger.LedgerTxn).parameters:
            args += (3,)
        txn = ledger.LedgerTxn(*args)
        engine = PretradeEngine.__new__(PretradeEngine)
        engine._ledger = MemoryLedger(txn)
        engine._clock = lambda: 123
        forwarded = []
        engine._apply_cancel = lambda operation_id: forwarded.append(operation_id)
        read_calls = []
        service = RiskOrderService(
            engine, engine._ledger, account_scope="account", route="SYNTHETIC",
            execution_domain_ref="domain", principals=principals,
            instrument_for=lambda *_: None, ready=lambda: True,
            cancel_status=lambda op, **kw: read_calls.append((op, kw)),
        )
        yield service, engine, txn, forwarded, read_calls
    finally:
        connection.rollback()
        connection.close()


def principal(name="p-a", *, account=False, strategies=frozenset({"A"}), instruments=frozenset()):
    return RiskPrincipal(name, "operator", "actor-" + name, frozenset(), OPS, b"synthetic",
                         account, strategies, instruments)


def context(name="p-a"):
    return ServiceCallContext(
        WireIdentity(WireRole.APPLICATION_CLIENT, name, "actor-" + name),
        WireScope("domain", account_ref="account"), "registration", name,
    )


def request(service, operation, kind, value, name="p-a"):
    return service.handle("risk.cancel.request", context=context(name), arguments={
        "operation_id": operation, "scope_kind": kind, "scope_value": value,
    })


def status(service, operation, name="p-a"):
    return service.handle("risk.cancel.status", context=context(name), arguments={"operation_id": operation})


def hidden(operation):
    return {"status": "NOT_FOUND", "operation_id": operation}


def test_handler_and_engine_require_separate_explicit_scope_grants():
    with setup_service({"p-a": principal()}) as (service, engine, txn, forwarded, _):
        assert request(service, "allowed", "strategy", "A")["status"] == "OBSERVED"
        for operation, kind, value in (("b", "strategy", "B"), ("account", "account", ""),
                                       ("instrument", "instrument", "SHFE:rb")):
            assert request(service, operation, kind, value) == {
                "status": "REQUEST_REJECTED", "code": "risk_unauthorized"}
            assert txn.cancel_intent(operation) is None
        assert forwarded == ["allowed"]
        caller, _ = service.caller(context(), "risk.cancel.request")
        with pytest.raises(RiskAdminError, match="risk_unauthorized"):
            engine.request_cancel(caller, "direct", "strategy", "B")
        assert forwarded == ["allowed"]


def test_account_instrument_and_strategy_grants_do_not_imply_each_other():
    grant = principal(account=True, strategies=frozenset(), instruments=frozenset({"SHFE:rb"}))
    with setup_service({"p-a": grant}) as (service, _, _, forwarded, _):
        assert request(service, "account", "account", "")["status"] == "OBSERVED"
        assert request(service, "rb", "instrument", "SHFE:rb")["status"] == "OBSERVED"
        for operation, kind, value in (("s", "strategy", "A"), ("cu", "instrument", "SHFE:cu")):
            assert request(service, operation, kind, value)["code"] == "risk_unauthorized"
        assert forwarded == ["account", "rb"]


def test_status_checks_current_grant_and_original_principal_without_resending():
    with setup_service({"p-a": principal(account=True), "p-b": principal("p-b", account=True)}) as (service, _, txn, sent, reads):
        assert request(service, "op", "account", "")["status"] == "OBSERVED"
        row = txn.cancel_intent("op")
        txn.update_cancel_intent("op", row["row_version"], state="UNKNOWN", updated_ns=124)
        assert status(service, "op")["state"] == "UNKNOWN"
        assert status(service, "op", "p-b") == hidden("op")
        assert sent == ["op"] and reads == []
        # Reinstalling narrower operator grants models a new service configuration.
        denied = RiskOrderService(service._engine, service._ledger, account_scope="account", route="SYNTHETIC",
                                  execution_domain_ref="domain", principals={"p-a": principal(strategies=frozenset())},
                                  instrument_for=lambda *_: None, ready=lambda: True,
                                  cancel_status=lambda *_a, **_kw: pytest.fail("unauthorized read"))
        assert status(denied, "op") == hidden("op")
        assert status(denied, "missing") == hidden("missing")
        assert request(denied, "op", "account", "")["code"] == "risk_unauthorized"
        assert sent == ["op"] and txn.cancel_intent("op")["state"] == "UNKNOWN"


def test_same_principal_new_actor_does_not_inherit_original_intent():
    with setup_service({"p-a": principal()}) as (service, _, txn, sent, reads):
        assert request(service, "op", "strategy", "A")["status"] == "OBSERVED"
        other_actor = replace(principal(), actor_ref="new-actor")
        changed = RiskOrderService(service._engine, service._ledger, account_scope="account", route="SYNTHETIC",
                                   execution_domain_ref="domain", principals={"p-a": other_actor},
                                   instrument_for=lambda *_: None, ready=lambda: True,
                                   cancel_status=lambda *_a, **_kw: pytest.fail("identity mismatch read"))
        changed_context = replace(context(), identity=WireIdentity(WireRole.APPLICATION_CLIENT, "p-a", "new-actor"))
        assert changed.handle("risk.cancel.status", context=changed_context,
                              arguments={"operation_id": "op"}) == hidden("op")
        assert changed.handle("risk.cancel.request", context=changed_context, arguments={
            "operation_id": "op", "scope_kind": "strategy", "scope_value": "A"})["code"] == "risk_request_conflict"
        assert txn.cancel_intent("op")["state"] == "PENDING" and sent == ["op"] and not reads


def test_legacy_same_principal_is_unknown_and_never_queries_or_resends():
    with setup_service({"p-a": principal()}) as (service, _, txn, sent, reads):
        txn.ensure_cancel_intent(operation_id="legacy", source="operator:p-a", scope_kind="strategy",
                                 scope_value="A", latch_id=None, wait_cause_id=None, now_ns=1)
        row = txn.cancel_intent("legacy")
        txn.update_cancel_intent("legacy", row["row_version"], state="ACCEPTED", updated_ns=2)
        assert status(service, "legacy") == {"status": "OBSERVED", "operation_id": "legacy",
                                             "state": "UNKNOWN", "gateway": None}
        assert request(service, "legacy", "strategy", "A")["state"] == "UNKNOWN"
        assert txn.cancel_intent("legacy")["state"] == "ACCEPTED"
        assert not sent and not reads


def test_strategy_status_remains_unknown_without_bound_gateway_source():
    with setup_service({"p-a": principal()}) as (service, _, txn, sent, reads):
        assert request(service, "op", "strategy", "A")["status"] == "OBSERVED"
        row = txn.cancel_intent("op")
        txn.update_cancel_intent("op", row["row_version"], state="ACCEPTED", updated_ns=2)
        assert status(service, "op") == {"status": "OBSERVED", "operation_id": "op",
                                        "state": "UNKNOWN", "gateway": None}
        assert txn.cancel_intent("op")["state"] == "ACCEPTED"
        assert sent == ["op"] and not reads


def test_real_strategy_adapter_refuses_without_gateway_runtime_and_status_stays_unknown():
    class ForbiddenRuntime:
        def status(self):
            pytest.fail("unsupported strategy must not use runtime")

        def call(self, *_args, **_kwargs):
            pytest.fail("unsupported strategy must not send")

    with setup_service({"p-a": principal()}) as (service, engine, txn, _, reads):
        del engine._apply_cancel
        engine._control = GatewayControlAdapter(
            ForbiddenRuntime(), mount="synthetic", service_id="gateway", instance_id="instance",
            scope_ref="scope", account_ref="account", timeout_seconds=1,
        )
        result = request(service, "op", "strategy", "A")
        assert result == {"status": "OBSERVED", "operation_id": "op", "state": "REJECTED"}
        row = txn.cancel_intent("op")
        assert row["last_code"] == "RISK_CONTROL_SCOPE_UNSUPPORTED"
        assert status(service, "op") == {"status": "OBSERVED", "operation_id": "op",
                                        "state": "UNKNOWN", "gateway": None}
        assert not reads


def test_historical_or_rule_source_does_not_authorize_status_or_retry():
    with setup_service({"p-a": principal()}) as (service, _, txn, sent, reads):
        for op, source in (("other", "operator:p-b"), ("rule", "rule:hard"), ("old", "")):
            txn.ensure_cancel_intent(operation_id=op, source=source, scope_kind="strategy", scope_value="A",
                                     latch_id=None, wait_cause_id=None, now_ns=1)
            assert status(service, op) == hidden(op)
            assert request(service, op, "strategy", "A")["code"] == "risk_request_conflict"
        assert not sent and not reads


def test_status_hides_foreign_scope_and_actor_like_nonexistent_operation():
    only_a = principal(strategies=frozenset({"A"}))
    only_b = principal("p-b", strategies=frozenset({"B"}))
    same_scope_other_actor = principal("p-c", strategies=frozenset({"A"}))
    with setup_service({"p-a": only_a, "p-b": only_b, "p-c": same_scope_other_actor}) as (service, _, txn, sent, reads):
        assert request(service, "other-strategy", "strategy", "B", "p-b")["status"] == "OBSERVED"
        assert request(service, "other-actor", "strategy", "A", "p-c")["status"] == "OBSERVED"
        previously_b = RiskOrderService(service._engine, service._ledger, account_scope="account", route="SYNTHETIC",
            execution_domain_ref="domain", principals={"p-a": principal(strategies=frozenset({"A", "B"}))},
            instrument_for=lambda *_: None, ready=lambda: True)
        assert request(previously_b, "revoked-scope", "strategy", "B")["status"] == "OBSERVED"
        # Scope B is not authorized to A, and same-scope principal C is not A.
        for op in ("other-strategy", "other-actor", "revoked-scope", "nonexistent"):
            response = status(service, op)
            assert response == hidden(op)
        # Malformed/unattributable historical rows do not reveal existence either.
        txn.ensure_cancel_intent(operation_id="invalid", source="operator:p-a", scope_kind="bogus",
                                 scope_value="A", latch_id=None, wait_cause_id=None, now_ns=1)
        assert status(service, "invalid") == hidden("invalid")
        assert sent == ["other-strategy", "other-actor", "revoked-scope"] and not reads


@pytest.mark.parametrize("bad", [None, {}, {"account": 1, "strategies": [], "instruments": []},
    {"account": False, "strategies": ["A", "A"], "instruments": []},
    {"account": False, "strategies": [], "instruments": ["SHFE:"]},
    {"account": False, "strategies": [[]], "instruments": []},
    {"account": False, "strategies": [], "instruments": [], "extra": True}])
def test_invalid_config_is_refused_before_any_key_read(monkeypatch, bad):
    monkeypatch.setattr(Path, "resolve", lambda self, *a, **kw: self)
    monkeypatch.setattr(config, "read_key_file", lambda *_a, **_kw: pytest.fail("key read"))
    doc = document()
    doc["principals"][1]["cancel_scope_grants"] = bad
    with pytest.raises(config.RiskServiceConfigError):
        config.parse_risk_service_config(doc, path=Path("/synthetic/risk.yaml"))


def test_valid_config_and_absence_of_grants(monkeypatch):
    monkeypatch.setattr(Path, "resolve", lambda self, *a, **kw: self)
    monkeypatch.setattr(config, "read_key_file", lambda path, *, secret: path.name.encode())
    doc = document()
    doc["principals"][1]["operations"] = list(OPS)
    doc["principals"][1]["cancel_scope_grants"] = {
        "account": False, "strategies": ["A"], "instruments": ["SHFE:rb"]}
    parsed = config.parse_risk_service_config(doc, path=Path("/synthetic/risk.yaml"))
    operator = parsed.principals["operator"]
    assert not operator.cancel_account_allowed
    assert operator.cancel_strategy_refs == frozenset({"A"})
    assert operator.cancel_instrument_refs == frozenset({"SHFE:rb"})
    del doc["principals"][1]["cancel_scope_grants"]
    absent = config.parse_risk_service_config(doc, path=Path("/synthetic/risk.yaml")).principals["operator"]
    assert not absent.cancel_account_allowed and not absent.cancel_strategy_refs and not absent.cancel_instrument_refs


def test_non_operator_cannot_carry_cancel_grants():
    with pytest.raises(ValueError, match="RISK_CALLER_INVALID"):
        CallerIdentity("p", "a", "strategy", frozenset({"A"}), "run", OPS,
                       cancel_strategy_refs=frozenset({"A"}))


def test_non_operator_config_grants_are_rejected_before_keys(monkeypatch):
    monkeypatch.setattr(Path, "resolve", lambda self, *a, **kw: self)
    monkeypatch.setattr(config, "read_key_file", lambda *_a, **_kw: pytest.fail("key read"))
    doc = document()
    doc["principals"][0]["cancel_scope_grants"] = {"account": True, "strategies": [], "instruments": []}
    with pytest.raises(config.RiskServiceConfigError, match="RISK_SERVICE_CONFIG_CANCEL_GRANTS_INVALID"):
        config.parse_risk_service_config(doc, path=Path("/synthetic/risk.yaml"))
