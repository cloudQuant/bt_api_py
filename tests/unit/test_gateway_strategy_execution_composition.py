"""Offline composition tests; no services, accounts or venue facts."""
from copy import deepcopy
from dataclasses import replace
from pathlib import Path
import sqlite3
import threading
from decimal import Decimal
from types import SimpleNamespace

import pytest
import yaml

from bt_api_py.execution_parent_grant import OPERATIONS
from bt_api_py.execution_routing import install_execution_routing_tables
from bt_api_py.gateway_strategy_config import GatewayStrategyConfigError, parse_gateway_strategy_config
from bt_api_py.gateway_direct_client import build_gateway_order_intent
from bt_api_py._contracts.models import OrderRequest, OrderType, Side
from bt_api_py.execution_routing import ExecutionRoutingOutcome
from bt_api_py.service_runtime import ClientCurveKeys
from tests.unit.test_iter63_gateway_strategy_client import Host, parts
from tests.unit.test_iter65_sdk_execution_routing import routing_raw


@pytest.fixture
def raw():
    path = Path(__file__).resolve().parents[2] / "examples/services/gateway-strategy-client.example.yaml"
    value = yaml.safe_load(path.read_text(encoding="utf-8"))
    routing = routing_raw(account_ref="account", execution_domain_ref="domain")
    key = value["instruments_by_ref"]["main"]
    routing["instruments"] = [key]
    routing["rules"][0]["match"]["instrument"] = key
    joint = deepcopy(value["client_config"])
    joint["clients"]["execution"] = {"config_schema_version": 1, "settings": {"bindings": [{
        "mount": "broker.execution_parent", "service_id": "exec-service",
        "instance_id": "exec-instance", "scope_ref": "exec-scope",
        "account_ref": "account", "transport_ref": "exec-transport"}]}}
    joint["communication"]["profiles"]["exec-transport"] = {
        "backend": "zmq", "config_schema_version": 1,
        "endpoints": {"rpc_connect": "tcp://127.0.0.1:7001"},
        "security_profile_ref": "exec-security"}
    route = deepcopy(value["route"])
    route.update(mount="broker.execution_parent", service_id="exec-service",
                 instance_id="exec-instance", scope_ref="exec-scope")
    value["execution"] = {"client_config": joint, "route": route,
        "peer": {"role": "service", "principal_ref": "exec-principal",
                 "actor_ref": "exec-service", "delegate_ref": None},
        "requested_operations": list(OPERATIONS), "routing": routing}
    return value


@pytest.fixture
def db():
    connection = sqlite3.connect(":memory:", isolation_level=None)
    connection.execute("BEGIN IMMEDIATE")
    install_execution_routing_tables(connection)
    connection.commit()
    yield connection
    connection.close()


def profiles():
    return {**parts()["security_profiles"],
            "exec-security": ClientCurveKeys(b"a" * 40, b"b" * 40, b"c" * 40)}


def test_selected_composes_inert_and_closes(raw, db):
    config = parse_gateway_strategy_config(raw)
    api = config.create_api(host=Host(), cancel_host=Host(), security_profiles=profiles(), execution_db=db)
    assert api.execution_selected and api.execution_available is False
    assert api.execution_route_status() == {"available": False}
    assert api._execution_router._port is api._execution.client
    assert api._execution_router._risk_selected is False
    assert api._execution.runtime.status().state == "new"
    api.close()
    assert api.cleanup_confirmed


@pytest.mark.parametrize("edit", [
    lambda e: e["route"]["scope"].update(account_ref="other"),
    lambda e: e["route"]["identity"].update(principal_ref="other"),
    lambda e: e["peer"].update(actor_ref="wrong"),
    lambda e: e.update(requested_operations=["execution.parent.query"]),
    lambda e: e["routing"].update(component="wrong"),
    lambda e: e["routing"].update(schema_version=1),
    lambda e: e["client_config"]["registration"].update(strategy_ref="other"),
    lambda e: e["client_config"]["communication"]["profiles"]["exec-transport"].update(security_profile_ref="security"),
    lambda e: e["client_config"]["clients"]["gateway"]["settings"]["bindings"][0].update(scope_ref="other"),
])
def test_inconsistent_config_rejected(raw, edit):
    edit(raw["execution"])
    with pytest.raises(GatewayStrategyConfigError):
        parse_gateway_strategy_config(raw)


def test_database_contract_and_optional_selection(raw, db):
    config = parse_gateway_strategy_config(raw)
    with pytest.raises(ValueError, match="EXECUTION_COMPOSITION_FAILED"):
        config.create_api(host=Host(), security_profiles=profiles())
    empty = sqlite3.connect(":memory:", isolation_level=None)
    try:
        with pytest.raises(ValueError, match="EXECUTION_COMPOSITION_FAILED"):
            config.create_api(host=Host(), security_profiles=profiles(), execution_db=empty)
    finally:
        empty.close()
    raw["execution"] = "none"
    unselected = parse_gateway_strategy_config(raw)
    assert unselected.execution_selection is None
    with pytest.raises(ValueError, match="INVALID_EXECUTION_DATABASE"):
        unselected.create_api(host=Host(), security_profiles=parts()["security_profiles"], execution_db=db)


def intent_for(config, *, side=Side.BUY, request_key="request-1"):
    request = request_for(side=side, request_key=request_key)
    return build_gateway_order_intent(config.binding, config.route, request)


def request_for(*, side=Side.BUY, request_key="request-1"):
    return OrderRequest(symbol="IF2612", side=side, order_type=OrderType.LIMIT,
        quantity=Decimal("5"), account_id="account", client_order_id="client",
        idempotency_key=request_key, price=Decimal("10"), quantity_unit="lots",
        exchange_id="CFFEX", offset="open")


def test_unavailable_execution_allows_only_no_match_proof(raw, db):
    config = parse_gateway_strategy_config(raw)
    api = config.create_api(host=Host(), cancel_host=Host(), security_profiles=profiles(), execution_db=db)
    assert api.execution_configuration_id == config.execution_selection.routing.configuration_id
    untouched = intent_for(config, side=Side.SELL)
    result = api.route_execution_order(config.exchange_name, untouched, before_submit=lambda _: True)
    assert result.outcome is ExecutionRoutingOutcome.DIRECT_PATH and result.direct_decision is not None
    called = []
    matched = intent_for(config, request_key="matched")
    result = api.route_execution_order(config.exchange_name, matched, before_submit=lambda _: called.append(True))
    assert result.outcome is ExecutionRoutingOutcome.EXECUTION_UNKNOWN
    assert not called
    api.close()


def test_cleanup_retries_original_session_and_remains_unconfirmed(raw, db, monkeypatch):
    from bt_api_py.execution_parent_strategy_session import ExecutionParentStrategySession
    config = parse_gateway_strategy_config(raw)
    api = config.create_api(host=Host(), cancel_host=Host(), security_profiles=profiles(), execution_db=db)
    original = ExecutionParentStrategySession.close
    calls = []
    def close(session, **kwargs):
        calls.append(session)
        if len(calls) == 1:
            raise RuntimeError("synthetic failure")
        return original(session, **kwargs)
    monkeypatch.setattr(ExecutionParentStrategySession, "close", close)
    with pytest.raises(Exception, match="CLEANUP_FAILED"):
        api.close()
    assert not api.cleanup_confirmed and api.lifecycle_state == "closing"
    api.close()
    assert api.cleanup_confirmed and calls == [api._execution, api._execution]


def test_failed_composition_exposes_cleanup_owner(raw, db, monkeypatch):
    from bt_api_py.gateway_strategy_config import GatewayCompositionError
    import bt_api_py.gateway_api as module
    config = parse_gateway_strategy_config(raw)
    monkeypatch.setattr(module.GatewayBtApi, "__init__", lambda *args, **kwargs: (_ for _ in ()).throw(ValueError()))
    with pytest.raises(GatewayCompositionError) as error:
        config.create_api(host=Host(), cancel_host=Host(), security_profiles=profiles(), execution_db=db)
    assert error.value.cleanup_owner.cleanup_confirmed


def test_risk_and_execution_use_separate_profiles_and_preserve_risk_flag(raw, db):
    from tests.unit.test_iter64_strategy_risk_selection import risk_selected, keys
    from tests.unit.test_iter63_gateway_strategy_delegated import Host as DelegatedHost
    risk_selected(raw)
    config = parse_gateway_strategy_config(raw)
    security = {**profiles(), "risk-security": keys(b"d")}
    api = config.create_api(host=Host(), cancel_host=Host(), delegated_cancel_host=DelegatedHost(),
                            security_profiles=security, execution_db=db)
    assert api.risk_selected and api.execution_selected
    assert api._execution_router._risk_selected is True
    api.close()
    assert api.cleanup_confirmed


def test_execution_start_failure_does_not_disable_gateway_reads(raw, db, monkeypatch):
    from bt_api_py.gateway_strategy_client import GatewayStrategyClientSession
    from bt_api_py.execution_parent_strategy_session import ExecutionParentStrategySession
    config = parse_gateway_strategy_config(raw)
    api = config.create_api(host=Host(), cancel_host=Host(), security_profiles=profiles(), execution_db=db)
    monkeypatch.setattr(GatewayStrategyClientSession, "start", lambda *args, **kwargs: "gateway-started")
    monkeypatch.setattr(ExecutionParentStrategySession, "start", lambda *args, **kwargs: (_ for _ in ()).throw(RuntimeError("private details")))
    assert api.start() == "gateway-started"
    assert api.list_exchanges() == [config.exchange_name]
    assert api.execution_selected and api.execution_route_status() == {"available": False}
    result = api.route_execution_order(config.exchange_name, intent_for(config), before_submit=lambda _: True)
    assert result.outcome is ExecutionRoutingOutcome.EXECUTION_UNKNOWN
    api.close()


def test_failed_composition_retains_retryable_owner(raw, db, monkeypatch):
    from bt_api_py.gateway_strategy_client import GatewayStrategyClientSession
    from bt_api_py.gateway_strategy_config import GatewayCompositionError
    import bt_api_py.gateway_api as module
    config = parse_gateway_strategy_config(raw)
    original = GatewayStrategyClientSession.close
    calls = []
    def close(session, **kwargs):
        calls.append(session)
        if len(calls) == 1:
            raise RuntimeError("private detail")
        return original(session, **kwargs)
    monkeypatch.setattr(GatewayStrategyClientSession, "close", close)
    monkeypatch.setattr(module.GatewayBtApi, "__init__", lambda *args, **kwargs: (_ for _ in ()).throw(ValueError()))
    with pytest.raises(GatewayCompositionError) as error:
        config.create_api(host=Host(), cancel_host=Host(), security_profiles=profiles(), execution_db=db)
    owner = error.value.cleanup_owner
    assert not owner.cleanup_confirmed
    owner.close()
    assert owner.cleanup_confirmed and calls[0] is calls[1]


@pytest.mark.parametrize("barrier", [None, False, "not callable"])
def test_missing_persistence_barrier_rejected_before_database_access(raw, db, barrier):
    config = parse_gateway_strategy_config(raw)
    api = config.create_api(host=Host(), cancel_host=Host(), security_profiles=profiles(), execution_db=db)
    calls = []
    db.set_trace_callback(calls.append)
    with pytest.raises(ValueError, match="INVALID_EXECUTION_PERSISTENCE_BARRIER"):
        api.route_execution_order(config.exchange_name, intent_for(config), before_submit=barrier)
    assert calls == []
    db.set_trace_callback(None)
    api.close()


def test_concurrent_close_during_risk_start_fences_execution_start(raw, db, monkeypatch):
    from bt_api_py.gateway_strategy_client import GatewayStrategyClientSession
    from bt_api_py.execution_parent_strategy_session import ExecutionParentStrategySession
    from bt_api_py.risk_service_client import RiskServiceClient
    from tests.unit.test_iter64_strategy_risk_selection import risk_selected, keys
    from tests.unit.test_iter63_gateway_strategy_delegated import Host as DelegatedHost
    risk_selected(raw)
    config = parse_gateway_strategy_config(raw)
    api = config.create_api(host=Host(), cancel_host=Host(), delegated_cancel_host=DelegatedHost(),
                            security_profiles={**profiles(), "risk-security": keys(b"d")}, execution_db=db)
    entered, release = threading.Event(), threading.Event()
    execution_starts, failures = [], []
    def risk_start(*args, **kwargs):
        entered.set()
        assert release.wait(5)
    monkeypatch.setattr(GatewayStrategyClientSession, "start", lambda *args, **kwargs: "started")
    monkeypatch.setattr(RiskServiceClient, "start", risk_start)
    monkeypatch.setattr(ExecutionParentStrategySession, "start", lambda *args, **kwargs: execution_starts.append(True))
    def start():
        try:
            api.start()
        except Exception as error:
            failures.append(str(error))
    thread = threading.Thread(target=start)
    thread.start()
    assert entered.wait(5)
    try:
        with pytest.raises(Exception, match="CLEANUP_FAILED"):
            api.close()
    finally:
        release.set()
        thread.join(5)
    assert not thread.is_alive() and not execution_starts
    assert failures == ["GATEWAY_FACADE_CLOSING"]
    api.close()
    assert api.cleanup_confirmed


def test_cleanup_original_execution_despite_router_drift(raw, db, monkeypatch):
    from bt_api_py.execution_parent_strategy_session import ExecutionParentStrategySession
    config = parse_gateway_strategy_config(raw)
    api = config.create_api(host=Host(), cancel_host=Host(), security_profiles=profiles(), execution_db=db)
    original_session = api._execution
    original_close = ExecutionParentStrategySession.close
    calls = []
    def close(session, **kwargs):
        calls.append(session)
        return original_close(session, **kwargs)
    monkeypatch.setattr(ExecutionParentStrategySession, "close", close)
    api._execution_router._config = object()
    api._execution_router._port = object()
    api.close()
    assert calls == [original_session]
    assert original_session.cleanup_confirmed and api.cleanup_confirmed


def test_execution_runtime_replacement_is_not_cleaned_or_confirmed(raw, db):
    config = parse_gateway_strategy_config(raw)
    api = config.create_api(host=Host(), cancel_host=Host(), security_profiles=profiles(), execution_db=db)
    session = api._execution
    original_runtime = session._runtime
    replacement = object()
    session._runtime = replacement
    with pytest.raises(Exception, match="CLEANUP_FAILED"):
        api.close()
    assert not api.cleanup_confirmed and session._runtime is replacement
    session._runtime = original_runtime
    api.close()
    assert api.cleanup_confirmed


def direct_host(intent, config, *, kind="GATEWAY"):
    host = Host()
    host.load_intent = lambda key: intent if key == intent.request_key else None
    host.load_execution_route = lambda key: SimpleNamespace(
        kind=kind, account=intent.binding.account_scope, strategy=intent.binding.strategy_scope,
        request_key=key, intent_digest=intent.digest,
        configuration_id=config.execution_selection.routing.configuration_id,
        execution_domain_ref=intent.route.scope.execution_domain_ref,
        request_id=None, request_sha256=None, parent_ref=None)
    return host


def test_direct_write_requires_persisted_no_match_and_host_selection(raw, db, monkeypatch):
    from bt_api_py.gateway_direct_client import GatewayDirectClient
    config = parse_gateway_strategy_config(raw)
    intent = intent_for(config, side=Side.SELL)
    host = direct_host(intent, config)
    api = config.create_api(host=host, cancel_host=Host(), security_profiles=profiles(), execution_db=db)
    calls = []
    monkeypatch.setattr(GatewayDirectClient, "reserve", lambda *args, **kwargs: calls.append("reserve"))
    monkeypatch.setattr(GatewayDirectClient, "dispatch", lambda *args, **kwargs: calls.append("dispatch"))
    request = request_for(side=Side.SELL)
    with pytest.raises(RuntimeError, match="EXECUTION_DIRECT_ROUTE_REQUIRED"):
        api.reserve_order(config.exchange_name, request)
    with pytest.raises(RuntimeError, match="EXECUTION_DIRECT_ROUTE_REQUIRED"):
        api.dispatch_order(config.exchange_name, intent.request_key)
    assert not calls
    result = api.route_execution_order(config.exchange_name, intent, before_submit=lambda _: True)
    assert result.outcome is ExecutionRoutingOutcome.DIRECT_PATH
    api.reserve_order(config.exchange_name, request)
    api.dispatch_order(config.exchange_name, intent.request_key)
    assert calls == ["reserve", "dispatch"]
    for kind in ("PENDING", "EXECUTION_PARENT", "RISK"):
        host.load_execution_route = direct_host(intent, config, kind=kind).load_execution_route
        with pytest.raises(RuntimeError, match="EXECUTION_DIRECT_ROUTE_REQUIRED"):
            api.reserve_order(config.exchange_name, request)
        with pytest.raises(RuntimeError, match="EXECUTION_DIRECT_ROUTE_REQUIRED"):
            api.dispatch_order(config.exchange_name, intent.request_key)
    assert calls == ["reserve", "dispatch"]
    host.load_execution_route = direct_host(intent, config).load_execution_route
    db.execute("DROP TABLE execution_route_candidates_v1")
    with pytest.raises(RuntimeError, match="EXECUTION_DIRECT_ROUTE_REQUIRED"):
        api.reserve_order(config.exchange_name, request)
    assert calls == ["reserve", "dispatch"]
    api.close()


def test_nested_routing_config_drift_refused_but_original_cleanup_runs(raw, db):
    raw["execution"]["routing"]["rules"][0]["algorithm"]["params"] = {"nested": {"value": 1}}
    config = parse_gateway_strategy_config(raw)
    api = config.create_api(host=Host(), cancel_host=Host(), security_profiles=profiles(), execution_db=db)
    api._execution_config.policies[0].algorithm_params["nested"]["value"] = 2
    with pytest.raises(RuntimeError, match="EXECUTION_BINDING_CHANGED"):
        _ = api.execution_configuration_id
    api.close()
    assert api.cleanup_confirmed


def test_ticket_reserve_is_gated_before_risk_host_consumption(raw, db):
    from tests.unit.test_iter64_strategy_risk_selection import risk_selected, keys
    from tests.unit.test_iter64_risk_service_client import Host as RiskHost
    from tests.unit.test_iter63_gateway_strategy_delegated import Host as DelegatedHost
    risk_selected(raw)
    config = parse_gateway_strategy_config(raw)
    intent = intent_for(config, side=Side.SELL)
    host = RiskHost()
    ticket = host.create_risk_reserve_intent(intent)
    host.load_execution_route = direct_host(intent, config, kind="RISK").load_execution_route
    api = config.create_api(host=host, cancel_host=Host(), delegated_cancel_host=DelegatedHost(),
                            security_profiles={**profiles(), "risk-security": keys(b"d")}, execution_db=db)
    contract = api.risk_first_send_reserve_contract()
    with pytest.raises(RuntimeError, match="EXECUTION_DIRECT_ROUTE_REQUIRED"):
        contract[1](config.exchange_name, request_for(side=Side.SELL), first_send_ticket=ticket)
    assert intent.request_key in host._risk_tickets
    result = api.route_execution_order(config.exchange_name, intent, before_submit=lambda _: True)
    assert result.outcome is ExecutionRoutingOutcome.DIRECT_PATH
    result = contract[1](config.exchange_name, request_for(side=Side.SELL), first_send_ticket=ticket)
    assert result.outcome == "UNKNOWN"
    assert intent.request_key not in host._risk_tickets
    api.close()


def test_parent_recovery_verification_reads_only_original_persisted_record(raw, db):
    config = parse_gateway_strategy_config(raw)
    intent = intent_for(config)
    api = config.create_api(host=Host(), cancel_host=Host(), security_profiles=profiles(), execution_db=db)
    captured = []
    api._execution_router.route(intent, before_submit=lambda prepared: captured.append(prepared) or False)
    assert len(captured) == 1 and api.verify_execution_parent_route(intent, captured[0])
    assert not api.verify_execution_parent_route(intent_for(config, request_key="other"), captured[0])
    assert not api.verify_execution_parent_route(intent, object())
    api.close()


def test_old_run_parent_is_read_only_and_requires_original_identity(raw, db, monkeypatch):
    from bt_api_py.execution_parent_client import ExecutionParentClient, ExecutionParentObservation
    old_config = parse_gateway_strategy_config(raw)
    old_intent = intent_for(old_config)
    old_api = old_config.create_api(host=Host(), cancel_host=Host(), security_profiles=profiles(), execution_db=db)
    prepared = []
    old_api._execution_router.route(old_intent, before_submit=lambda item: prepared.append(item) or False)
    old_api.close()
    original = prepared[0]
    raw["route"]["scope"]["run_id"] = "new-run"
    raw["execution"]["route"]["scope"]["run_id"] = "new-run"
    config = parse_gateway_strategy_config(raw)
    host = Host()
    host.load_intent = lambda key: old_intent if key == old_intent.request_key else None
    host.load_execution_route = lambda key: SimpleNamespace(kind="EXECUTION_PARENT", **vars(original))
    api = config.create_api(host=host, cancel_host=Host(), security_profiles=profiles(), execution_db=db)
    calls = []
    def query(client, request):
        calls.append(request)
        return ExecutionParentObservation("LOCAL_RECEIVED", original.request_id,
                                          original.request_sha256, original.parent_ref)
    monkeypatch.setattr(ExecutionParentClient, "query", query)
    assert api.verify_execution_parent_route(old_intent, original)
    status = api.execution_order_status(config.exchange_name, old_intent)
    assert (status.request_id, status.request_sha256, status.parent_ref) == (
        original.request_id, original.request_sha256, original.parent_ref)
    assert len(calls) == 1 and calls[0]["request_id"] == original.request_id
    with pytest.raises(ValueError, match="EXECUTION_INTENT_MISMATCH"):
        api.route_execution_order(config.exchange_name, old_intent, before_submit=lambda _: True)
    with pytest.raises(ValueError, match="EXECUTION_INTENT_MISMATCH"):
        api.cancel_execution_order(config.exchange_name, old_intent)
    forged_route = replace(old_intent.route, scope=replace(old_intent.route.scope, run_id="other-run"))
    forged = replace(old_intent, route=forged_route)
    assert not api.verify_execution_parent_route(forged, original)
    with pytest.raises(ValueError, match="EXECUTION_PARENT_ROUTE_UNAVAILABLE"):
        api.execution_order_status(config.exchange_name, forged)
    wrong_route = replace(old_intent.route, service_id="other-service")
    assert not api.verify_execution_parent_route(replace(old_intent, route=wrong_route), original)
    with pytest.raises(ValueError, match="EXECUTION_INTENT_MISMATCH"):
        api.execution_order_status(config.exchange_name, replace(old_intent, route=wrong_route))
    for field, value in (("account_ref", "other-account"), ("strategy_ref", "other-strategy"),
                         ("execution_domain_ref", "other-domain")):
        altered_scope = replace(old_intent.route.scope, **{field: value})
        altered = replace(old_intent, route=replace(old_intent.route, scope=altered_scope))
        assert not api.verify_execution_parent_route(altered, original)
        with pytest.raises(ValueError, match="EXECUTION_INTENT_MISMATCH"):
            api.execution_order_status(config.exchange_name, altered)
    altered_identity = replace(old_intent.route.identity, principal_ref="other-principal")
    altered = replace(old_intent, route=replace(old_intent.route, identity=altered_identity))
    assert not api.verify_execution_parent_route(altered, original)
    with pytest.raises(ValueError, match="EXECUTION_INTENT_MISMATCH"):
        api.execution_order_status(config.exchange_name, altered)
    assert len(calls) == 1
    api.close()
