"""Iteration 64 (D64-01, D64-02): explicit Risk selection in the strategy's Gateway tree.

UNIT/FAKE only: no Risk host, Gateway or transport is started.
"""
from __future__ import annotations

import copy
import subprocess
import sys
from pathlib import Path

import pytest
import yaml
from bt_api_base.risk_service_protocol import RISK_ADMIN_OPERATIONS, RISK_ORDER_OPERATIONS

from bt_api_py.gateway_api import GatewayBtApi
from bt_api_py.gateway_strategy_config import (
    GatewayStrategyConfigError,
    parse_gateway_strategy_config,
)
from bt_api_py.service_config import ServiceConfigError, parse_client_config, parse_service_config
from bt_api_py.service_deployment import MOUNT_PORTS
from bt_api_py.service_runtime import ClientCurveKeys, PORT_OPERATIONS
from tests.unit.test_iter63_gateway_strategy_client import Host, parts
from tests.unit.test_iter63_gateway_strategy_delegated import Host as DelegatedCancelHost

EXAMPLE = Path(__file__).resolve().parents[2] / "examples/services/gateway-strategy-client.example.yaml"
RISK_OPS = ["risk.order.reserve", "risk.order.dispatch", "risk.order.release",
            "risk.order.status", "risk.status"]


@pytest.fixture
def raw():
    return yaml.safe_load(EXAMPLE.read_text(encoding="utf-8"))


def risk_tree(raw, **changes):
    tree = {
        "client_config": {
            "client_config_schema_version": 1,
            "process_role": "strategy_client",
            "registration": {"strategy_ref": raw["binding"]["strategy_scope"]},
            "clients": {"risk": {"config_schema_version": 1, "settings": {"bindings": [{
                "mount": "broker.risk_order", "service_id": "risk", "instance_id": "risk-a",
                "scope_ref": "risk-scope", "account_ref": raw["binding"]["account_scope"],
                "transport_ref": "risk-transport"}]}}},
            "communication": {"config_schema_version": 1, "profiles": {"risk-transport": {
                "backend": "zmq", "config_schema_version": 1,
                "endpoints": {"rpc_connect": "tcp://127.0.0.1:12355"},
                "security_profile_ref": "risk-security"}}},
        },
        "route": {"mount": "broker.risk_order", "service_id": "risk", "instance_id": "risk-a",
                  "scope_ref": "risk-scope", "scope": copy.deepcopy(raw["route"]["scope"]),
                  "identity": copy.deepcopy(raw["route"]["identity"])},
        "peer": {"role": "service", "principal_ref": "risk-principal", "actor_ref": "risk",
                 "delegate_ref": None},
        "requested_operations": list(RISK_OPS),
    }
    tree.update(changes)
    return tree


def risk_selected(raw):
    """The public example turned into a Risk-selected strategy (no direct PLACE)."""
    ops = [op for op in raw["requested_operations"] if op not in ("direct.reserve", "direct.dispatch")]
    raw["requested_operations"] = ops + ["direct.delegated_command_status", "direct.delegated_cancel",
                                         "direct.delegated_cancel_status"]
    raw["risk"] = risk_tree(raw)
    return raw


def keys(tag):
    return ClientCurveKeys(tag * 40, b"b" * 40, b"c" * 40)


# --- ports, mounts and client alias -----------------------------------------------------


def test_risk_ports_and_mounts_are_registered_with_frozen_operations():
    assert PORT_OPERATIONS["service.risk_order"] == RISK_ORDER_OPERATIONS
    assert PORT_OPERATIONS["service.risk_admin"] == RISK_ADMIN_OPERATIONS
    assert MOUNT_PORTS["broker.risk_order"] == ("service.risk_order",)
    assert MOUNT_PORTS["upstream.risk_order"] == ("service.risk_order",)
    assert MOUNT_PORTS["application.risk_admin"] == ("service.risk_admin",)


def test_client_alias_risk_maps_the_three_mounts(raw):
    strategy = parse_client_config(risk_tree(raw)["client_config"])
    assert strategy.clients["risk"]["settings"]["bindings"][0]["mount"] == "broker.risk_order"
    for role, mount in (("application_client", "application.risk_admin"),):
        tree = risk_tree(raw)["client_config"]
        tree["process_role"] = role
        tree["registration"] = {"application_ref": "operator-console"}
        tree["clients"]["risk"]["settings"]["bindings"][0]["mount"] = mount
        assert parse_client_config(tree).clients["risk"]["settings"]["bindings"][0]["mount"] == mount
    # The Gateway alias must not reach a Risk mount.
    tree = risk_tree(raw)["client_config"]
    tree["clients"] = {"gateway": tree["clients"]["risk"]}
    with pytest.raises(ServiceConfigError):
        parse_client_config(tree)


def test_risk_order_mount_is_account_scoped(raw):
    tree = risk_tree(raw)["client_config"]
    tree["clients"]["risk"]["settings"]["bindings"][0].pop("account_ref")
    with pytest.raises(ServiceConfigError):
        parse_client_config(tree)


def _risk_service(options, ports):
    return {
        "service_config_schema_version": 1,
        "process_role": "service",
        "service_id": "risk",
        "scope_ref": "risk-root-scope",
        "transport_ref": "listener",
        "registration_policy_ref": "risk-grants",
        "service_requirements_ref": "risk-readiness",
        "components": {"risk": {"config_schema_version": 2, "settings": {
            "instance_id": "risk-a", "bind_ports": ports, "options": options}}},
        "communication": {"config_schema_version": 1, "profiles": {"listener": {
            "backend": "zmq", "config_schema_version": 1,
            "endpoints": {"rpc_bind": "tcp://127.0.0.1:12355"},
            "security_profile_ref": "test-security"}}},
    }


def test_risk_component_order_service_role_binds_both_ports():
    config = parse_service_config(
        _risk_service({"role": "order_service"}, ["service.risk_order", "service.risk_admin"]))
    assert list(config.components["risk"]["settings"]["bind_ports"]) == [
        "service.risk_order", "service.risk_admin"]


def test_risk_order_port_is_refused_under_another_risk_role():
    options = {"role": "account_admission", "account_policy_ref": "account-policy-v1"}
    with pytest.raises(ServiceConfigError) as error:
        parse_service_config(_risk_service(options, ["service.risk_order"]))
    assert error.value.code == "PORT_ROLE_MISMATCH"


# --- the risk key of the gateway_strategy tree --------------------------------------------


def test_missing_risk_selection_refuses_with_its_own_code(raw):
    raw.pop("risk")
    with pytest.raises(GatewayStrategyConfigError, match="^risk_selection_missing$"):
        parse_gateway_strategy_config(raw)


@pytest.mark.parametrize("value", [None, True, 0, "", "None", "NONE", " none", "risk", [], {}])
def test_any_other_risk_value_is_invalid(raw, value):
    raw["risk"] = value
    with pytest.raises(GatewayStrategyConfigError, match="^risk_selection_invalid$"):
        parse_gateway_strategy_config(raw)


def test_explicit_none_keeps_the_direct_gateway_path(raw):
    config = parse_gateway_strategy_config(raw)
    assert config.risk_selection is None
    api = config.create_api(host=Host(), cancel_host=Host(), security_profiles=parts()["security_profiles"])
    assert type(api) is GatewayBtApi and api.risk_selected is False and api.risk_available is None
    api.close()


def test_selected_risk_client_is_parsed_like_the_gateway_client(raw):
    config = parse_gateway_strategy_config(risk_selected(raw))
    selection = config.risk_selection
    assert selection.route.mount == "broker.risk_order"
    assert selection.route.scope == config.route.scope
    assert selection.peer.actor_ref == "risk"
    assert selection.requested_operations == frozenset(RISK_OPS)
    assert selection.security_profile_ref == "risk-security"


def _break(name):
    def edit(tree, raw):
        if name == "route_mount":
            tree["route"]["mount"] = "broker.direct_write"
        elif name == "other_run":
            tree["route"]["scope"]["run_id"] = "other-run"
        elif name == "other_strategy_actor":
            tree["route"]["identity"]["actor_ref"] = "other"
        elif name == "peer_actor":
            tree["peer"]["actor_ref"] = "gateway"
        elif name == "peer_role":
            tree["peer"]["role"] = "strategy_client"
        elif name == "missing_reserve":
            tree["requested_operations"].remove("risk.order.reserve")
        elif name == "missing_status":
            tree["requested_operations"].remove("risk.order.status")
        elif name == "parent_operation":
            tree["requested_operations"].append("risk.parent.reserve")
        elif name == "unknown_operation":
            tree["requested_operations"].append("direct.reserve")
        elif name == "duplicate_operation":
            tree["requested_operations"].append("risk.status")
        elif name == "same_security_ref":
            tree["client_config"]["communication"]["profiles"]["risk-transport"][
                "security_profile_ref"] = "security"
        elif name == "binding_account":
            tree["client_config"]["clients"]["risk"]["settings"]["bindings"][0]["account_ref"] = "x"
        elif name == "binding_instance":
            tree["client_config"]["clients"]["risk"]["settings"]["bindings"][0]["instance_id"] = "x"
        elif name == "registration":
            tree["client_config"]["registration"]["strategy_ref"] = "other"
        elif name == "two_clients":
            tree["client_config"]["clients"]["gateway"] = copy.deepcopy(raw["client_config"]["clients"]["gateway"])
        elif name == "market_endpoint":
            tree["client_config"]["communication"]["profiles"]["risk-transport"]["endpoints"][
                "market_connect"] = "tcp://127.0.0.1:1"
        elif name == "extra_key":
            tree["connection_file"] = "state/risk.json"
        elif name == "missing_peer":
            tree.pop("peer")
    return edit


@pytest.mark.parametrize("name", [
    "route_mount", "other_run", "other_strategy_actor", "peer_actor", "peer_role",
    "missing_reserve", "missing_status", "parent_operation", "unknown_operation",
    "duplicate_operation", "same_security_ref", "binding_account", "binding_instance",
    "registration", "two_clients", "market_endpoint", "extra_key", "missing_peer",
])
def test_inconsistent_risk_client_is_invalid(raw, name):
    risk_selected(raw)
    _break(name)(raw["risk"], raw)
    with pytest.raises(GatewayStrategyConfigError, match="^risk_selection_invalid$"):
        parse_gateway_strategy_config(raw)


@pytest.mark.parametrize("change", ["direct_reserve", "direct_dispatch", "no_delegated_status",
                                    "no_delegated_cancel", "no_delegated_cancel_status"])
def test_selected_risk_requires_no_direct_place_and_delegated_observation(raw, change):
    risk_selected(raw)
    ops = raw["requested_operations"]
    if change == "direct_reserve":
        ops.append("direct.reserve")
    elif change == "direct_dispatch":
        ops.append("direct.dispatch")
    elif change == "no_delegated_status":
        ops.remove("direct.delegated_command_status")
    elif change == "no_delegated_cancel":
        ops.remove("direct.delegated_cancel")
    else:
        ops.remove("direct.delegated_cancel_status")
    with pytest.raises(GatewayStrategyConfigError, match="^risk_selection_invalid$"):
        parse_gateway_strategy_config(raw)


def test_other_defects_keep_the_generic_code_when_risk_is_present(raw):
    risk_selected(raw)
    raw["binding"]["account_scope"] = "wrong"
    with pytest.raises(GatewayStrategyConfigError, match="^INVALID_GATEWAY_STRATEGY_CONFIG$"):
        parse_gateway_strategy_config(raw)


def test_selected_risk_creates_an_inert_facade_with_both_profiles(raw, monkeypatch):
    import bt_api_py.gateway_strategy_client as gateway_client
    import bt_api_py.risk_service_client as risk_client

    def forbidden(*_a, **_kw):
        pytest.fail("runtime or transport constructed before start")

    for module in (gateway_client, risk_client):
        monkeypatch.setattr(module, "ServiceClientRuntime", forbidden)
        monkeypatch.setattr(module, "ZmqTransportFactory", forbidden)
    config = parse_gateway_strategy_config(risk_selected(raw))
    profiles = {"security": keys(b"a"), "risk-security": keys(b"r")}
    api = config.create_api(host=Host(), cancel_host=Host(), security_profiles=profiles,
                            delegated_cancel_host=DelegatedCancelHost())
    assert type(api) is GatewayBtApi and api.risk_selected is True
    assert api._risk.runtime_status().state == "new" and api.risk_available is False
    assert api._session.status().state == "new"
    api.close()
    assert api._risk.runtime_status().state == "closed"
    # Only the Gateway profile reaches the Gateway session.
    session = config.create_session(host=Host(), cancel_host=Host(), security_profiles=profiles,
                                    delegated_cancel_host=DelegatedCancelHost())
    assert session.delegated_client._cancel_host is not None
    session.close()
    # Cancels of Risk-placed orders need a durable delegated cancel host.
    with pytest.raises(ValueError, match="INVALID_REQUESTED_OPERATIONS"):
        config.create_session(host=Host(), cancel_host=Host(), security_profiles=profiles)


@pytest.mark.parametrize("profiles", [
    {"security": keys(b"a")},
    {"risk-security": keys(b"r")},
    {"security": keys(b"a"), "risk-security": keys(b"r"), "other": keys(b"o")},
])
def test_selected_risk_requires_exactly_both_security_profiles(raw, profiles):
    config = parse_gateway_strategy_config(risk_selected(raw))
    with pytest.raises(ValueError):
        config.create_api(host=Host(), cancel_host=Host(), security_profiles=profiles,
                          delegated_cancel_host=DelegatedCancelHost())


def test_strategy_side_never_imports_bt_api_risk():
    """INV64-02: the strategy process composes Risk routing without bt_api_risk."""
    code = (
        "import sys, yaml\n"
        "from bt_api_py.gateway_strategy_config import parse_gateway_strategy_config\n"
        "import bt_api_py.risk_service_client, bt_api_py.gateway_api\n"
        "bad = [m for m in sys.modules if m == 'bt_api_risk' or m.startswith('bt_api_risk.')]\n"
        "assert not bad, bad\n"
    )
    result = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True, timeout=120)
    assert result.returncode == 0, result.stderr[-2000:]
