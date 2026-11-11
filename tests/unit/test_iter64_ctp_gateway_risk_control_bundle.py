"""controls.operators[] entries for the Risk service's control principal (G64-03, G64-06 a).

Synthetic protected bundle only; no network, venue or account.
"""
from __future__ import annotations

import json

import pytest
from test_iter63_ctp_gateway_local_profile import _composing_config, _fixture, _operator, _returns
from test_iter63_ctp_gateway_operator_reads import _bundle

from bt_api_py.ctp_gateway_local_profile import CtpLocalProfileError

RISK_OPERATIONS = ["direct.control.apply", "direct.control.status", "direct.control.cancel_scope",
                   "direct.control.cancel_scope_status"]


def _risk(doc, **updates):
    operator = _operator(doc)
    operator["identity"] = dict(operator["identity"], principal_ref="principal-risk", actor_ref="risk-service")
    operator["resources"] = [dict(operator["resources"][0], operations=list(RISK_OPERATIONS))]
    operator.update(kind="risk", modes=["STOP_NEW", "FREEZE"], instrument_scope=True)
    operator.update(updates)
    return operator


def _load(tmp_path, monkeypatch, operator):
    from bt_api_py.ctp_gateway_local_profile import load_simnow_trading_resources

    doc, file, config = _fixture(tmp_path, monkeypatch)
    doc["settings"]["write_enabled"] = True
    doc["returns"] = _returns()
    doc["controls"] = {"operators": [operator(doc)], "upgrade_controls_from_v9": True}
    file.write_text(json.dumps(doc), encoding="utf-8")
    return load_simnow_trading_resources(config)


def test_risk_control_principal_is_installed_with_its_kind_modes_and_contract_scope(tmp_path, monkeypatch):
    resources = _load(tmp_path, monkeypatch, _risk)
    grant = resources.control_grants["principal-risk"]
    assert (grant.kind, grant.modes, grant.instrument_scope, grant.account_scope_allowed) == (
        "risk", frozenset({"STOP_NEW", "FREEZE"}), True, True)
    assert grant.operations == frozenset(RISK_OPERATIONS)
    assert "principal-risk" not in resources.grants                         # no trading grant


def test_operator_entries_without_the_new_keys_are_read_as_before(tmp_path, monkeypatch):
    grant = _load(tmp_path, monkeypatch, _operator).control_grants["principal-ops"]
    assert (grant.kind, grant.modes, grant.instrument_scope) == (
        "operator", frozenset({"STOP_NEW", "FREEZE", "DRAIN"}), False)
    # An operator may also be limited to some modes or given the contract argument.
    (tmp_path / "b").mkdir()
    grant = _load(tmp_path / "b", monkeypatch, lambda doc: dict(
        _operator(doc), modes=["FREEZE"], instrument_scope=True)).control_grants["principal-ops"]
    assert (grant.kind, grant.modes, grant.instrument_scope) == ("operator", frozenset({"FREEZE"}), True)


@pytest.mark.parametrize("updates", [
    {"resources": "resume"},                                    # Risk never releases a cause (D64-21)
    {"modes": None},                                            # modes are required for kind risk
    {"modes": []},
    {"modes": ["STOP_NEW", "STOP_NEW"]},
    {"modes": ["STOP_NEW", "RESUME"]},
    {"modes": "STOP_NEW"},
    {"kind": "auditor"},
    {"instrument_scope": "yes"},
    {"account_scope": False},                                   # a contract needs an account-wide principal
])
def test_risk_control_entries_that_overreach_are_refused(tmp_path, monkeypatch, updates):
    def operator(doc):
        value = _risk(doc)
        if updates.get("resources") == "resume":
            value["resources"][0]["operations"].append("direct.control.resume")
        elif updates.get("account_scope") is False:
            strategy = doc["clients"][0]["identity"]["actor_ref"]
            value["resources"][0]["scope"] = dict(value["resources"][0]["scope"], strategy_ref=strategy)
            value.update(account_scope_allowed=False, strategies=[strategy])
        elif "modes" in updates and updates["modes"] is None:
            del value["modes"]
        else:
            value.update(updates)
        return value
    with pytest.raises(CtpLocalProfileError, match="^CTP_LOCAL_RESOURCES_INVALID$"):
        _load(tmp_path, monkeypatch, operator)


def test_risk_control_principal_may_hold_the_account_read(tmp_path, monkeypatch):
    """G64-06 a: the Risk principal reads account positions and funds as an account-wide operator does."""
    from bt_api_py.ctp_gateway_local_profile import (
        load_simnow_trading_resources,
        simnow_trading_profile_factory,
    )
    from bt_api_py.service_config import parse_service_config

    doc, file, config, operator, good = _bundle(tmp_path, monkeypatch)
    operator.update(kind="risk", modes=["STOP_NEW", "FREEZE"], instrument_scope=True)
    operator["resources"][0]["operations"] = [
        op for op in operator["resources"][0]["operations"] if op != "direct.control.resume"]
    doc["account_observation"] = good
    file.write_text(json.dumps(doc), encoding="utf-8")
    resources = load_simnow_trading_resources(config)
    principal = operator["identity"]["principal_ref"]
    assert resources.control_grants[principal].kind == "risk"
    entry, = resources.authority.grants[principal]
    assert {resource.port: resource.operations for resource in entry.resources}["service.account_read"] == \
        frozenset({"account.snapshot.typed"})
    assert principal in resources.account_read_principals
    # The trading composition's own grant checks accept it.
    raw, _ = _composing_config(config)
    raw["components"]["gateway"]["settings"]["bind_ports"].append("service.account_read")
    assert simnow_trading_profile_factory.create_deployment(parse_service_config(raw)).state == "new"


def test_operator_reads_may_hold_the_strategy_funds_and_book_reads(tmp_path, monkeypatch):
    """G64-06 b: an account-wide operator's own read resource may name the two account reads."""
    from test_iter63_ctp_gateway_operator_reads import _read_resource

    from bt_api_py.ctp_gateway_local_profile import (
        load_simnow_trading_resources,
        simnow_trading_profile_factory,
    )
    from bt_api_py.service_config import parse_service_config

    doc, file, config, operator, good = _bundle(tmp_path, monkeypatch)
    operations = ["account.snapshot.typed", "account.strategy_financial", "account.strategy_book"]
    good["operator_reads"][0]["resources"] = [_read_resource(operator, operations=operations)]
    doc["account_observation"] = good
    file.write_text(json.dumps(doc), encoding="utf-8")
    resources = load_simnow_trading_resources(config)
    entry, = resources.authority.grants[operator["identity"]["principal_ref"]]
    assert {resource.port: resource.operations for resource in entry.resources}["service.account_read"] == \
        frozenset(operations)
    raw, _ = _composing_config(config)
    raw["components"]["gateway"]["settings"]["bind_ports"].append("service.account_read")
    assert simnow_trading_profile_factory.create_deployment(parse_service_config(raw)).state == "new"
    # Never for an operator who may act on single strategies only.
    strategy = doc["clients"][0]["identity"]["actor_ref"]
    scoped = dict(operator, account_scope_allowed=False, strategies=[strategy])
    scoped["resources"] = [dict(operator["resources"][0],
                                scope=dict(operator["resources"][0]["scope"], strategy_ref=strategy))]
    doc["controls"]["operators"] = [scoped]
    file.write_text(json.dumps(doc), encoding="utf-8")
    with pytest.raises(CtpLocalProfileError, match="^CTP_LOCAL_RESOURCES_INVALID$"):
        load_simnow_trading_resources(config)
