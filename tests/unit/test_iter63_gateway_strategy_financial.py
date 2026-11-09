"""Finite, identity-bound strategy financial observations (synthetic facts)."""
from dataclasses import replace
from decimal import Decimal
from types import MappingProxyType
import threading

import pytest
from bt_api_base.gateway_direct_protocol import GatewayCommandBinding
from bt_api_base.service_protocol import WireIdentity, WireRole, WireScope
from bt_api_py.gateway_direct_client import GatewayClientRoute
from bt_api_py.gateway_strategy_financial import GatewayStrategyFinancialClient
from bt_api_py.gateway_strategy_financial_service import handle_strategy_financial
from bt_api_py.service_runtime_thread import BindingSnapshot, RuntimeSnapshot, ThreadedServiceClientRuntime

OP = "direct.strategy_financial"


def setup(monkeypatch):
    identity = WireIdentity(WireRole.STRATEGY_CLIENT, "principal", "strategy")
    scope = WireScope("domain", account_ref="account", strategy_ref="strategy", run_id="run")
    route = GatewayClientRoute("broker.direct_write", "gateway", "instance", "scope", scope, identity)
    runtime = object.__new__(ThreadedServiceClientRuntime)
    reg = BindingSnapshot("registration", "epoch", True, route.mount, route.service_id,
                          route.instance_id, route.scope_ref, route.scope, frozenset({OP}), True)
    state = [RuntimeSnapshot("running", True, True, None, (reg,), 0, 0, identity, "run")]
    monkeypatch.setattr(runtime, "status", lambda: state[0])
    binding = GatewayCommandBinding("journal", "account", "strategy", "principal")
    return GatewayStrategyFinancialClient(runtime, binding=binding, route=route), runtime, reg, state


def reply(**overrides):
    result = {"status": "OBSERVED", "schema_version": 1, "currency": "CNY",
              "model": "futures_linear_v1", "revision": 2, "cut_ref": "cut",
              "owner_generation": "owner", "snapshot_digest": "a" * 64,
              "value": "-1.25", "cash": "-11.25"}
    result.update(overrides)
    return result


def test_fresh_strategy_value_and_cash_are_bound_and_not_account_cash(monkeypatch):
    client, runtime, _reg, _state = setup(monkeypatch)
    calls = []
    def call(mount, operation, arguments, **kwargs):
        calls.append((mount, operation, arguments, kwargs))
        return MappingProxyType(reply())
    monkeypatch.setattr(runtime, "call", call)
    result = client.snapshot("CNY", timeout_seconds=2)
    assert result.outcome == "OBSERVED"
    assert result.observation.value == Decimal("-1.25")
    assert result.observation.cash == Decimal("-11.25")
    assert calls[0][0:3] == ("broker.direct_write", OP, {"currency": "CNY"})
    assert calls[0][3] == {"account_ref": "account", "scope_ref": "scope", "timeout_seconds": 2}
    assert client.is_current(result.observation, max_age_seconds=10)
    assert not client.is_current(replace(result.observation), max_age_seconds=10)


def test_unavailable_or_revoked_never_reuses_old_value(monkeypatch):
    client, runtime, reg, state = setup(monkeypatch)
    monkeypatch.setattr(runtime, "call", lambda *_a, **_k: reply())
    previous = client.snapshot("CNY").observation
    monkeypatch.setattr(runtime, "call", lambda *_a, **_k:
        {"status":"REQUEST_REJECTED", "code":"STRATEGY_FINANCIAL_UNAVAILABLE"})
    result = client.snapshot("CNY")
    assert result.outcome == "UNAVAILABLE" and result.code == "STRATEGY_FINANCIAL_UNAVAILABLE"
    assert not client.is_current(previous, max_age_seconds=10)
    state[0] = replace(state[0], bindings=(replace(reg, operations=frozenset()),))
    monkeypatch.setattr(runtime, "call", lambda *_a, **_k: pytest.fail("ungranted call"))
    assert client.snapshot("CNY").code == "ROUTE_UNAVAILABLE"


@pytest.mark.parametrize("bad", [
    {"schema_version": True}, {"currency":"USD"}, {"model":"spot"}, {"revision": 0},
    {"snapshot_digest":"x" * 64}, {"owner_generation":""}, {"value":"NaN"},
    {"cash":"0e0"}, {"unexpected": 1},
])
def test_invalid_server_result_is_unavailable(monkeypatch, bad):
    client, runtime, _reg, _state = setup(monkeypatch)
    monkeypatch.setattr(runtime, "call", lambda *_a, **_k: reply(**bad))
    assert client.snapshot("CNY").outcome == "UNAVAILABLE"


def test_revision_replay_generation_and_changed_registration_are_refused(monkeypatch):
    client, runtime, reg, state = setup(monkeypatch)
    monkeypatch.setattr(runtime, "call", lambda *_a, **_k: reply())
    old = client.snapshot("CNY").observation
    for altered in (reply(revision=1), reply(cash="-12"), reply(owner_generation="other")):
        monkeypatch.setattr(runtime, "call", lambda *_a, _r=altered, **_k: _r)
        assert client.snapshot("CNY").outcome == "UNAVAILABLE"
    assert not client.is_current(old, max_age_seconds=3)
    def change_during_rpc(*_a, **_k):
        state[0] = replace(state[0], bindings=(replace(reg, service_epoch="new-epoch"),))
        return reply(revision=3)
    monkeypatch.setattr(runtime, "call", change_during_rpc)
    assert client.snapshot("CNY").outcome == "UNAVAILABLE"


def test_wrong_thread_or_input_rejected_without_rpc(monkeypatch):
    client, runtime, _reg, _state = setup(monkeypatch)
    monkeypatch.setattr(runtime, "call", lambda *_a, **_k: pytest.fail("unexpected RPC"))
    for currency in ("", " USD", None, 1):
        with pytest.raises(ValueError, match="GATEWAY_FINANCIAL_INVALID_REQUEST"):
            client.snapshot(currency)
    errors = []
    def run():
        try:
            client.snapshot("CNY")
        except RuntimeError as exc:
            errors.append(str(exc))
    thread = threading.Thread(target=run)
    thread.start()
    thread.join()
    assert errors == ["GATEWAY_POSITION_WRONG_OWNER"]


def test_service_helper_rejects_untrusted_objects_without_reading():
    response = handle_strategy_financial(journal=object(), owner=object(), context=object(),
                                         arguments={"currency":"CNY"}, grant=object())
    assert response == {"status":"REQUEST_REJECTED", "code":"STRATEGY_FINANCIAL_UNAVAILABLE"}


def test_real_owner_journal_and_registry_enforce_financial_scope(tmp_path, monkeypatch):
    """Use one real SQLite owner and Registry with synthetic financial evidence."""
    from contextlib import contextmanager
    from pathlib import Path
    from bt_api_gateway.direct_command_journal import GatewayDirectCommandJournal
    from bt_api_gateway.direct_dispatch import GatewayDirectDispatcher
    from bt_api_gateway.session_guard import LocalAccountSessionGuard
    from bt_api_py.gateway_direct_owner import GatewayDirectOwner, GatewayDirectOwnerResources
    from bt_api_py.gateway_direct_service import GatewayDirectGrant, GatewayDirectServiceBinding
    from bt_api_py.service_registry import PrincipalGrant, ResourceGrant, ServiceCapability, ServiceRegistry
    from tests.unit.test_service_registry import _next_request, _register

    root = Path(__file__).resolve().parents[2]
    monkeypatch.syspath_prepend(str(root / "bt_api/bt_api_gateway/tests"))
    from test_iter63_strategy_financial_ledger import sample

    directory = tmp_path / "guards"
    directory.mkdir()
    holder = {"source_current": True}

    @contextmanager
    def source_fence(account, strategy, currency):
        assert (account, strategy, currency) == ("acct", "s1", "CNY")
        yield lambda _db, snapshot: (
            holder["source_current"] and snapshot.account == account
            and snapshot.strategy == strategy and snapshot.currency == currency
        )

    def factory():
        guard = LocalAccountSessionGuard(directory, "acct", "owner")
        generation = guard.acquire()
        journal = GatewayDirectCommandJournal.create(tmp_path / "db", "acct", generation=generation)
        journal.enable_controls(guard)
        journal.enable_strategy_ledger(guard)
        journal.enable_account_extensions(guard)
        journal.enable_parent_authority(guard)
        journal.enable_account_facts_financial(guard)
        journal.install_strategy_financial_port(guard=guard, source_fence=source_fence)
        synthetic = sample(account="acct", strategy="s1", journal_id=journal.journal_id,
                           owner_generation=generation)
        assert journal.append_strategy_financial(synthetic, guard=guard)
        dispatcher = GatewayDirectDispatcher(journal, "CTP", guard, lambda *_: None,
                                             lambda _: ("anchor",), lambda _: True)
        holder["journal"] = journal
        return GatewayDirectOwnerResources(dispatcher, journal, guard.release)

    owner = GatewayDirectOwner(factory, queue_size=4, max_queue_wait_seconds=1, busy_timeout_seconds=1)
    assert owner.start(2)
    registry = None
    try:
        identity = WireIdentity(WireRole.STRATEGY_CLIENT, "p", "s1")
        scope = WireScope("domain", account_ref="acct", strategy_ref="s1", run_id="run1")
        grant_scope = WireScope("domain", account_ref="acct", strategy_ref="s1")
        ops = frozenset({OP})
        capability = ServiceCapability("gateway", "service.direct_write", ops, frozenset())
        grant = ResourceGrant("gateway", "service.direct_write", "scope", grant_scope, ops)
        registry = ServiceRegistry(":memory:", service_id="gateway-service", capabilities=(capability,),
            grants={"p": (PrincipalGrant(identity, (grant,)),)}, policy_version="p", requirements_version="r")
        binding = GatewayDirectServiceBinding(holder["journal"],
            {"p": GatewayDirectGrant("s1", frozenset({"rb2601"}))}, owner=owner,
            strategy_financial_enabled=True)
        assert OP in binding.supported_operations
        registry.register_handler("gateway", OP, lambda ctx, args: binding.handle(OP, context=ctx, arguments=args))
        registered = _register(registry, principal="p", identity=identity, scope=scope,
            instance_id="gateway", port="service.direct_write", scope_ref="scope", operations=(OP,))
        assert registered["ok"]
        rec = registered["result"]
        route = GatewayClientRoute("broker.direct_write", "gateway-service", "gateway", "scope", scope, identity)
        runtime = object.__new__(ThreadedServiceClientRuntime)
        reg = BindingSnapshot(rec["registration_id"], rec["service_epoch"], True, route.mount,
                              route.service_id, route.instance_id, route.scope_ref, scope, ops, True)
        runtime_snapshot = RuntimeSnapshot("running", True, True, None, (reg,), 0, 0, identity, "run1")
        monkeypatch.setattr(runtime, "status", lambda: runtime_snapshot)
        def call(_mount, op, arguments, **_kwargs):
            reply = registry.handle("p", _next_request(op, {"registration_id": rec["registration_id"],
                "service_epoch": rec["service_epoch"], "instance_id": "gateway", "arguments": arguments},
                identity=identity, scope=scope))
            assert reply["ok"]
            return reply["result"]
        monkeypatch.setattr(runtime, "call", call)
        client = GatewayStrategyFinancialClient(runtime,
            binding=GatewayCommandBinding(holder["journal"].journal_id, "acct", "s1", "p"), route=route)
        observed = client.snapshot("CNY")
        assert observed.outcome == "OBSERVED"
        assert observed.observation.value == Decimal("104900")
        assert observed.observation.cash == Decimal("74900")
        assert client.is_current(observed.observation, max_age_seconds=5)
        holder["source_current"] = False
        assert client.snapshot("CNY").outcome == "UNAVAILABLE"
        assert not client.is_current(observed.observation, max_age_seconds=5)
        # Caller cannot select another strategy in the authenticated handler.
        wrong = call(route.mount, OP, {"currency": "CNY", "strategy": "other"})
        assert wrong == {"status": "REQUEST_REJECTED", "code": "INVALID_REQUEST"}
    finally:
        if registry is not None:
            registry.close()
        assert owner.close(2)


def test_facade_binds_financial_client_and_requires_explicit_operation(monkeypatch):
    from bt_api_py.gateway_api import GatewayBtApi
    from bt_api_py.gateway_strategy_client import GatewayStrategyClientSession
    from tests.unit.test_iter63_gateway_strategy_client import parts

    values = parts()
    values["requested_operations"] = frozenset({OP})
    session = GatewayStrategyClientSession(**values)
    api = GatewayBtApi(session, exchange_name="CTP___FUTURE")
    route = session.route
    reg = BindingSnapshot("registration", "epoch", True, route.mount, route.service_id,
                          route.instance_id, route.scope_ref, route.scope, frozenset({OP}), True)
    snap = RuntimeSnapshot("running", True, True, None, (reg,), 0, 0, route.identity, "run")
    monkeypatch.setattr(session.runtime, "status", lambda: snap)
    monkeypatch.setattr(session.runtime, "call", lambda *_a, **_k: reply())
    result = api.get_strategy_financial("CTP___FUTURE", "CNY")
    assert result.outcome == "OBSERVED" and result.observation.cash == Decimal("-11.25")
    assert api.strategy_financial_is_current("CTP___FUTURE", result.observation, max_age_seconds=5)
    with pytest.raises(ValueError, match="GATEWAY_ROUTE_MISMATCH"):
        api.get_strategy_financial("OTHER", "CNY")
    object.__setattr__(session, "_financial_client", object())
    with pytest.raises(RuntimeError, match="GATEWAY_BINDING_CHANGED"):
        api.get_strategy_financial("CTP___FUTURE", "CNY")


def test_owner_financial_source_lifecycle_rejects_expired_and_failed_collection(tmp_path, monkeypatch):
    from contextlib import contextmanager
    from pathlib import Path
    from bt_api_gateway.direct_command_journal import GatewayDirectCommandJournal, DirectCommandJournalError
    from bt_api_gateway.session_guard import LocalAccountSessionGuard
    from bt_api_py.gateway_strategy_financial_source import GatewayStrategyFinancialSource

    monkeypatch.syspath_prepend(str(Path(__file__).resolve().parents[2] / "bt_api/bt_api_gateway/tests"))
    from test_iter63_strategy_financial_ledger import sample

    (tmp_path / "guards").mkdir()
    guard = LocalAccountSessionGuard(tmp_path / "guards", "acct", "owner")
    generation = guard.acquire()
    journal = GatewayDirectCommandJournal.create(tmp_path / "db", "acct", generation=generation)
    journal.enable_controls(guard)
    journal.enable_strategy_ledger(guard)
    journal.enable_account_extensions(guard)
    journal.enable_parent_authority(guard)
    journal.enable_account_facts_financial(guard)
    snapshot = sample(account="acct", strategy="s1", journal_id=journal.journal_id,
                      owner_generation=generation)
    state = {"current": True, "fail": False, "closed": False, "close_fail": True}

    @contextmanager
    def source_fence(account, strategy, currency):
        yield lambda _db, actual: (state["current"] and actual == snapshot
            and (account, strategy, currency) == ("acct", "s1", "CNY"))

    @contextmanager
    def admission():
        yield lambda: None

    def collect():
        if state["fail"]:
            raise RuntimeError("synthetic source failure")
        return (snapshot,)

    def close():
        if state["close_fail"]:
            raise RuntimeError("synthetic cleanup failure")
        state["closed"] = True

    source = GatewayStrategyFinancialSource(journal=journal, guard=guard, source_fence=source_fence,
        collect=collect, close=close)
    try:
        assert source.bound_to(journal, guard) and source.start()
        assert journal.read_strategy_financial("s1", "CNY", admission_fence=admission) == snapshot
        state["current"] = False
        with pytest.raises(DirectCommandJournalError, match="STRATEGY_FINANCIAL_UNAVAILABLE"):
            journal.read_strategy_financial("s1", "CNY", admission_fence=admission)
        state.update(current=True, fail=True)
        assert source.maintain() is False
        with pytest.raises(Exception, match="STRATEGY_FINANCIAL_SOURCE_UNAVAILABLE"):
            journal.read_strategy_financial("s1", "CNY", admission_fence=admission)
        state["fail"] = False
        assert source.maintain()
        assert journal.read_strategy_financial("s1", "CNY", admission_fence=admission) == snapshot
        source.invalidate()
        with pytest.raises(Exception, match="STRATEGY_FINANCIAL_SOURCE_UNAVAILABLE"):
            journal.read_strategy_financial("s1", "CNY", admission_fence=admission)
        assert source.maintain()
        with pytest.raises(RuntimeError, match="synthetic cleanup failure"):
            source.close()
        with pytest.raises(RuntimeError, match="STRATEGY_FINANCIAL_SOURCE_UNAVAILABLE"):
            source.maintain()
        with pytest.raises(Exception, match="STRATEGY_FINANCIAL_SOURCE_UNAVAILABLE"):
            journal.read_strategy_financial("s1", "CNY", admission_fence=admission)
        state["close_fail"] = False
        source.close()
        assert state["closed"]
        with pytest.raises(Exception, match="STRATEGY_FINANCIAL_SOURCE_UNAVAILABLE"):
            journal.read_strategy_financial("s1", "CNY", admission_fence=admission)
    finally:
        guard.release()
