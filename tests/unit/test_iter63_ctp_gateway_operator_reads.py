"""account_observation.operator_reads: an operator's own account read registration (63.14).

Synthetic protected bundle only; no network, venue or account.
"""
from __future__ import annotations

import json

import pytest
from test_iter63_ctp_gateway_local_profile import (
    _composing_config,
    _composing_doc,
    _fixture,
    _observation,
    _observer,
    _operator,
)

from bt_api_py.ctp_gateway_local_profile import CtpLocalProfileError


def _read_resource(operator, **updates):
    resource = dict(operator["resources"][0], port="service.account_read", operations=["account.snapshot.typed"])
    resource["scope"] = dict(resource["scope"], run_id=None)
    return dict(resource, **updates)


def _bundle(tmp_path, monkeypatch):
    doc, file, config = _fixture(tmp_path, monkeypatch)
    _composing_doc(doc)
    operator = doc["controls"]["operators"][0]
    good = dict(_observation(doc, tmp_path, [_observer(doc)]),
                operator_reads=[{"principal_ref": operator["identity"]["principal_ref"],
                                 "resources": [_read_resource(operator)]}])
    return doc, file, config, operator, good


def test_operator_read_adds_one_account_read_resource_to_the_operator(tmp_path, monkeypatch):
    from bt_api_py.ctp_gateway_local_profile import load_simnow_trading_resources

    doc, file, config, operator, good = _bundle(tmp_path, monkeypatch)
    doc["account_observation"] = good
    file.write_text(json.dumps(doc), encoding="utf-8")
    resources = load_simnow_trading_resources(config)
    principal = operator["identity"]["principal_ref"]
    entry, = resources.authority.grants[principal]
    ports = {resource.port: resource.operations for resource in entry.resources}
    assert ports == {"service.direct_write": frozenset(operator["resources"][0]["operations"]),
                     "service.account_read": frozenset({"account.snapshot.typed"})}
    read, = [resource for resource in entry.resources if resource.port == "service.account_read"]
    assert read.scope.strategy_ref is None
    assert principal in resources.account_read_principals
    # It stays an operator: its control grant is unchanged and it trades nothing.
    assert principal in resources.control_grants and principal not in resources.grants
    # Without the entry the operator has no account read.
    doc["account_observation"] = {name: item for name, item in good.items() if name != "operator_reads"}
    file.write_text(json.dumps(doc), encoding="utf-8")
    resources = load_simnow_trading_resources(config)
    entry, = resources.authority.grants[principal]
    assert {resource.port for resource in entry.resources} == {"service.direct_write"}
    assert principal not in resources.account_read_principals


def test_operator_read_is_strict(tmp_path, monkeypatch):
    from bt_api_py.ctp_gateway_local_profile import load_simnow_trading_resources

    doc, file, config, operator, good = _bundle(tmp_path, monkeypatch)
    principal = operator["identity"]["principal_ref"]
    client = doc["clients"][0]["identity"]["principal_ref"]

    def entry(**updates):
        return dict(good, operator_reads=[dict(good["operator_reads"][0], **updates)])

    def resource(**updates):
        return entry(resources=[_read_resource(operator, **updates)])

    for bad in (
            entry(principal_ref="principal-unknown"),
            entry(principal_ref=client),                                          # a strategy client
            entry(principal_ref="principal-observer"),                            # an observer, not an operator
            entry(resources=[]),
            dict(good, operator_reads=good["operator_reads"] * 2),
            dict(good, operator_reads=[dict(good["operator_reads"][0], extra=1)]),
            dict(good, operator_reads={"principal_ref": principal}),
            resource(port="service.direct_write"),
            resource(operations=["account.snapshot.typed", "account.read_after"]),
            resource(operations=["account.events.read_after"]),
            resource(operations=["account.snapshot"]),
            resource(scope=dict(_read_resource(operator)["scope"], strategy_ref="strategy-a")),
    ):
        doc["account_observation"] = bad
        file.write_text(json.dumps(doc), encoding="utf-8")
        with pytest.raises(CtpLocalProfileError, match="^CTP_LOCAL_RESOURCES_INVALID$"):
            load_simnow_trading_resources(config)
    # No controls section: there is no operator to give a read to.
    doc["account_observation"] = good
    del doc["controls"]
    file.write_text(json.dumps(doc), encoding="utf-8")
    with pytest.raises(CtpLocalProfileError, match="^CTP_LOCAL_RESOURCES_INVALID$"):
        load_simnow_trading_resources(config)


def test_operator_read_bundle_composes_with_the_read_port_bound(tmp_path, monkeypatch):
    from bt_api_py.ctp_gateway_local_profile import simnow_trading_profile_factory
    from bt_api_py.service_config import parse_service_config

    doc, file, config, _, good = _bundle(tmp_path, monkeypatch)
    raw, _ = _composing_config(config)
    raw["components"]["gateway"]["settings"]["bind_ports"].append("service.account_read")
    doc["account_observation"] = good
    file.write_text(json.dumps(doc), encoding="utf-8")
    # The trading composition's own checks accept an operator holding both resources.
    assert simnow_trading_profile_factory.create_deployment(parse_service_config(raw)).state == "new"


def test_operator_read_names_only_an_application_client(tmp_path, monkeypatch):
    # The section parser's own contract, independent of how the controls section built the grants.
    from bt_api_base.service_protocol import WireIdentity, WireRole

    from bt_api_py.ctp_gateway_local_profile import (
        _account_observation,
        _identity,
        _return_settings,
        _scope,
    )
    from bt_api_py.service_registry import PrincipalGrant, ResourceGrant

    doc, _, _, operator, good = _bundle(tmp_path, monkeypatch)
    write = operator["resources"][0]
    resource = ResourceGrant(write["instance_id"], write["port"], write["scope_ref"], _scope(write["scope"]),
                             frozenset(write["operations"]))
    principal = operator["identity"]["principal_ref"]
    identity = _identity(operator["identity"])

    def parse(identity):
        grants = {principal: (PrincipalGrant(identity, (resource,)),)}
        return _account_observation(json.loads(json.dumps(good)), {}, grants,
                                    instruments=_return_settings(doc["returns"]).instruments,
                                    account_operators=frozenset({principal}))

    assert principal in parse(identity)[1]
    service = WireIdentity(WireRole.SERVICE, principal, identity.actor_ref)
    with pytest.raises(CtpLocalProfileError, match="^CTP_LOCAL_RESOURCES_INVALID$"):
        parse(service)


def test_operator_for_strategies_only_cannot_be_given_the_account_view(tmp_path, monkeypatch):
    from bt_api_py.ctp_gateway_local_profile import load_simnow_trading_resources

    doc, file, config, operator, good = _bundle(tmp_path, monkeypatch)
    strategy = doc["clients"][0]["identity"]["actor_ref"]
    scoped = _operator(doc)
    scoped["resources"][0]["scope"] = dict(scoped["resources"][0]["scope"], strategy_ref=strategy)
    scoped.update(account_scope_allowed=False, strategies=[strategy])
    doc["controls"]["operators"] = [scoped]
    file.write_text(json.dumps(doc), encoding="utf-8")
    assert operator["identity"]["principal_ref"] in load_simnow_trading_resources(config).control_grants
    # Account totals are never shown to an operator who may act on single strategies only.
    doc["account_observation"] = good
    file.write_text(json.dumps(doc), encoding="utf-8")
    with pytest.raises(CtpLocalProfileError, match="^CTP_LOCAL_RESOURCES_INVALID$"):
        load_simnow_trading_resources(config)
