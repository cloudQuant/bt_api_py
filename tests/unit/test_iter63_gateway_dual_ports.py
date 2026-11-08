"""Exact resources and per-port capability planning for Gateway trading owner."""

from __future__ import annotations

from types import SimpleNamespace

import pytest
from bt_api_base.service_protocol import WireScope
from bt_api_gateway.direct_command_journal import GatewayDirectCommandJournal
from bt_api_gateway.event_journal import GatewayEventJournal, GatewayEventJournalError
from bt_api_gateway.router import GatewayCommandKind, GatewayPrincipal
from bt_api_gateway.service_component import GatewayReadService, GatewayServiceAccountScope

from bt_api_py.gateway_direct_service import (
    ACCOUNT_READ_OPERATIONS,
    INSTRUMENT_OPERATIONS,
    EVENT_OPERATIONS,
    STORAGE_OPERATIONS,
    SUPPORTED_OPERATIONS,
    GatewayDirectGrant,
    GatewayDirectServiceBinding,
)
from bt_api_py.service_components import (
    ComponentResources,
    ServiceComponentError,
    build_component,
    component_specs,
    validate_components,
)
from bt_api_py.service_config import parse_service_config
from bt_api_py.service_registry import ServiceResultTooLargeError
from tests.integration.test_iter63_gateway_direct_service import (
    ACCOUNT,
    CLIENT_A,
    DOMAIN,
    _service_config,
)
from tests.integration.test_iter63_gateway_dual_ports import _dual_config, _resources


def _prepare(config):
    return validate_components(component_specs(config.components))[0]


def _build(prepared, values):
    return build_component(
        prepared,
        scope=WireScope(DOMAIN),
        resource_provider=lambda *_: ComponentResources.borrowed(values, ready=lambda: True),
    )


def test_both_capabilities_exact_and_no_read_write_grant(tmp_path):
    prepared = _prepare(_dual_config("tcp://127.0.0.1:12345"))
    binding, read, _model = _resources(tmp_path)
    component = _build(prepared, {"binding": binding, "read_service": read})
    caps = {cap.port: cap for cap in component.capabilities}
    assert caps["service.direct_write"].operations == STORAGE_OPERATIONS
    assert caps["service.direct_write"].write_operations == {"direct.reserve", "direct.release"}
    assert caps["service.account_read"].operations == ACCOUNT_READ_OPERATIONS
    assert caps["service.account_read"].write_operations == frozenset()
    assert set(component.handlers) == STORAGE_OPERATIONS | ACCOUNT_READ_OPERATIONS


@pytest.mark.parametrize("problem", ["missing", "extra", "wrong", "no_model"])
def test_resource_composition_rejects_missing_or_wrong_read_binding(tmp_path, problem):
    binding, read, model = _resources(tmp_path)
    if problem == "extra":
        config = _service_config("tcp://127.0.0.1:12345")
        values = {"binding": binding, "read_service": read}
    else:
        config = _dual_config("tcp://127.0.0.1:12345")
        values = {"binding": binding}
        if problem == "wrong":
            values["read_service"] = object()
        elif problem == "no_model":
            principal = GatewayPrincipal(
                CLIENT_A.principal_ref,
                frozenset({ACCOUNT}),
                frozenset({"state-a"}),
                frozenset({GatewayCommandKind.READ}),
            )
            account = GatewayServiceAccountScope(
                ACCOUNT, DOMAIN, ACCOUNT, account_read_strategy_scope="state-a"
            )
            values["read_service"] = GatewayReadService(
                model.journal,
                accounts={ACCOUNT: account},
                principals={CLIENT_A.principal_ref: principal},
            )
    with pytest.raises(ServiceComponentError):
        _build(_prepare(config), values)


def test_read_service_rejects_model_from_another_journal(tmp_path):
    _, _read, model = _resources(tmp_path)
    account = GatewayServiceAccountScope(
        ACCOUNT, DOMAIN, ACCOUNT, account_read_strategy_scope="state-a"
    )
    principal = GatewayPrincipal(
        CLIENT_A.principal_ref,
        frozenset({ACCOUNT}),
        frozenset({"state-a"}),
        frozenset({GatewayCommandKind.READ}),
    )
    with pytest.raises(Exception, match="STATE_WORKER_JOURNAL_MISMATCH"):
        GatewayReadService(
            GatewayEventJournal(tmp_path / "other.sqlite"),
            accounts={ACCOUNT: account},
            principals={CLIENT_A.principal_ref: principal},
            state_worker=model,
        )


def test_read_model_and_direct_binding_must_share_account_scope(tmp_path):
    _, read, _model = _resources(tmp_path)
    journal = GatewayDirectCommandJournal.create(
        tmp_path / "other-direct.sqlite", "other-account", generation="g1"
    )
    binding = GatewayDirectServiceBinding(
        journal, {CLIENT_A.principal_ref: GatewayDirectGrant("strategy-a", frozenset({"rb"}))}
    )
    with pytest.raises(ServiceComponentError, match="INVALID_ACCOUNT_READ_BINDING"):
        _build(
            _prepare(_dual_config("tcp://127.0.0.1:12345")),
            {"binding": binding, "read_service": read},
        )


def test_read_account_alias_cannot_redirect_another_account(tmp_path):
    binding, _read, model = _resources(tmp_path)
    account = GatewayServiceAccountScope(
        "other-account",
        DOMAIN,
        ACCOUNT,
        account_read_strategy_scope="state-a",
        shared_account_facts_scope="state-a",
        shared_fact_strategy_refs=frozenset({"strategy-a"}),
    )
    principal = GatewayPrincipal(
        CLIENT_A.principal_ref,
        frozenset({ACCOUNT}),
        frozenset({"state-a"}),
        frozenset({GatewayCommandKind.READ}),
    )
    read = GatewayReadService(
        model.journal,
        accounts={"other-account": account},
        principals={CLIENT_A.principal_ref: principal},
        state_worker=model,
    )
    with pytest.raises(ServiceComponentError, match="INVALID_ACCOUNT_READ_BINDING"):
        _build(
            _prepare(_dual_config("tcp://127.0.0.1:12345")),
            {"binding": binding, "read_service": read},
        )


def test_read_only_port_cannot_construct_trading_owner():
    raw = _service_config("tcp://127.0.0.1:12345").to_public_dict()
    raw["components"]["gateway"]["settings"]["bind_ports"] = ["service.account_read"]
    parsed = parse_service_config(raw)
    with pytest.raises(ServiceComponentError, match="COMPONENT_OPTIONS_INVALID"):
        _prepare(parsed)


def test_dispatch_enabled_does_not_bypass_exact_owner_requirement(tmp_path, monkeypatch):
    raw = _dual_config("tcp://127.0.0.1:12345").to_public_dict()
    raw["components"]["gateway"]["settings"]["options"]["dispatch_enabled"] = True
    prepared = _prepare(parse_service_config(raw))
    assert prepared.operations_by_port["service.direct_write"] == SUPPORTED_OPERATIONS | EVENT_OPERATIONS
    assert prepared.operations_by_port["service.account_read"] == ACCOUNT_READ_OPERATIONS | INSTRUMENT_OPERATIONS
    binding, read, _model = _resources(tmp_path)
    with pytest.raises(ServiceComponentError, match="DIRECT_OWNER_REQUIRED"):
        _build(prepared, {"binding": binding, "read_service": read})

    # The plan contains optional events, but a real owner with no stream must
    # advertise only the direct operations actually supplied by its binding.
    from tests.test_iter63_ctp_gateway_trading import _resources as ctp_resources, make

    profile, _, single_config, authority, _, _ = make(tmp_path, monkeypatch)
    resources = ctp_resources(profile, authority)
    try:
        actual = _build(_prepare(single_config), {"binding": resources.values["binding"]})
        capability = actual.capabilities[0]
        assert capability.operations == SUPPORTED_OPERATIONS
        assert capability.operations.isdisjoint(EVENT_OPERATIONS)
        assert set(actual.handlers).isdisjoint(EVENT_OPERATIONS)
    finally:
        resources.close()


def test_read_after_preserves_result_fitness_and_size_error(tmp_path, monkeypatch):
    binding, read, _model = _resources(tmp_path)
    component = _build(
        _prepare(_dual_config("tcp://127.0.0.1:12345")), {"binding": binding, "read_service": read}
    )
    captured = []

    def fits(_):
        return False

    identity = object()

    def handle(self, operation, *, principal_ref, identity, scope, arguments, result_fits):
        captured.append((self, operation, principal_ref, identity, scope, result_fits))
        raise GatewayEventJournalError("CONSUMER_PAGE_TOO_LARGE", "synthetic")

    monkeypatch.setattr(GatewayReadService, "handle", handle)
    context = SimpleNamespace(
        principal_ref="principal-a", identity=identity, scope=WireScope(DOMAIN), result_fits=fits
    )
    with pytest.raises(ServiceResultTooLargeError):
        component.handlers["account.read_after"](context, {})
    assert captured == [(read, "account.read_after", "principal-a", identity, context.scope, fits)]
