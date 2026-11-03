"""Iteration 60 R2-B: typed order-command bindings derived from deployment config.

One SDK derivation (``service_command_wire``) turns the plain declaration of
the client ``broker.orders`` route and of the host order-entry component into
``ServiceCommandBinding``. Missing or ambiguous facts refuse; nothing falls
back to the generic request map. UNIT/FAKE: parsers, a local SQLite journal
and synthetic providers only.
"""

from __future__ import annotations

import copy
import time
from decimal import Decimal

import pytest
from bt_api_base.service_protocol import WireIdentity, WireRole, WireScope
from bt_api_execution import ExecutionScope, ProviderObservation, SqliteExecutionStore

from bt_api_py.service_command_wire import (
    CommandInstrumentFact,
    ServiceCommandBinding,
    ServiceCommandWireError,
    build_submit_command,
    command_arguments,
    command_route,
    derive_command_binding,
    derive_host_command_bindings,
    parse_command_binding_declaration,
    risk_instrument_facts,
    validate_host_command_bindings,
)
from bt_api_py.service_components import (
    ComponentResources,
    ServiceComponentError,
    build_component,
    component_specs,
    validate_components,
)
from bt_api_py.service_config import ServiceConfigError, parse_client_config
from bt_api_py.service_execution import ServiceExecutionClient
from bt_api_py.service_execution_assembly import (
    ServiceExecutionAssemblyError,
    assemble_service_execution,
)
from tests.unit._typed_command_bindings import client_binding, declaration, host_bindings
from tests.unit.test_service_host import _service_config

_DOMAIN = "paper:typed-binding"
_ACCOUNT = "account-a"
_STRATEGY = "strategy-a"
_SERVICE_ID = "execution-service"
_INSTANCE_ID = "execution-owner"
_TEMPLATE = WireScope(_DOMAIN, account_ref=_ACCOUNT, strategy_ref=_STRATEGY)
_SERVICE_IDENTITY = WireIdentity(WireRole.SERVICE, "service-principal", _SERVICE_ID)


def _declaration(**changes):
    values = dict(
        provider="paper",
        venue="paper",
        environment="simulation",
        account_id=_ACCOUNT,
        instruments=("BTC-USDT", "ETH-USDT"),
        price_currency="USDT",
    )
    values.update(changes)
    return declaration(**values)


def _execution_scope(account=_ACCOUNT, strategy=_STRATEGY):
    return ExecutionScope("PAPER", "simulation", account, strategy, "20261104")


# -- declaration parser ------------------------------------------------------


def test_declaration_round_trips_to_canonical_plain_form():
    parsed = parse_command_binding_declaration(_declaration())
    assert parsed.account_key.account_id == _ACCOUNT
    assert [key.instrument_id for key in parsed.instruments] == ["BTC-USDT", "ETH-USDT"]
    assert parse_command_binding_declaration(parsed.to_plain()) == parsed


@pytest.mark.parametrize(
    ("mutate", "code"),
    [
        (lambda raw: raw.update(extra=1), "COMMAND_BINDING_FIELD_UNKNOWN"),
        (lambda raw: raw.pop("quantity_unit"), "COMMAND_BINDING_FACT_MISSING"),
        (lambda raw: raw.pop("account_key"), "COMMAND_BINDING_FACT_MISSING"),
        (lambda raw: raw.update(binding_schema_version=2), "COMMAND_BINDING_VERSION_UNSUPPORTED"),
        (lambda raw: raw.update(instruments=[]), "COMMAND_BINDING_INSTRUMENTS_MISSING"),
        (
            lambda raw: raw["instruments"].append(dict(raw["instruments"][0], venue="paper")),
            "COMMAND_BINDING_AMBIGUOUS",
        ),
        (
            lambda raw: raw["instruments"][0].update(venue="other-venue"),
            "COMMAND_BINDING_INSTRUMENT_REALM_MISMATCH",
        ),
        (
            lambda raw: raw["instruments"][0].pop("native_symbol"),
            "COMMAND_BINDING_INSTRUMENT_INVALID",
        ),
        (
            lambda raw: raw.update(quantity_unit="lots-of"),
            "COMMAND_BINDING_QUANTITY_UNIT_UNSUPPORTED",
        ),
        (
            lambda raw: raw.update(position_mode="hedge"),
            "COMMAND_BINDING_POSITION_MODE_UNSUPPORTED",
        ),
        (lambda raw: raw.update(hedge_class="spec"), "COMMAND_BINDING_HEDGE_CLASS_UNSUPPORTED"),
        (lambda raw: raw["account_key"].update(secret="x"), "COMMAND_BINDING_ACCOUNT_INVALID"),
    ],
)
def test_incomplete_or_ambiguous_declarations_are_refused(mutate, code):
    raw = copy.deepcopy(_declaration())
    mutate(raw)
    with pytest.raises(ServiceCommandWireError) as error:
        parse_command_binding_declaration(raw)
    assert error.value.code == code


def test_client_derivation_pins_route_and_account():
    binding = client_binding(_declaration(), service_id=_SERVICE_ID, instance_id=_INSTANCE_ID)
    assert type(binding) is ServiceCommandBinding
    assert (
        binding.route
        == command_route(_SERVICE_ID, _INSTANCE_ID)
        == ("execution-service/execution-owner")
    )
    assert binding.quantity_unit == "base" and binding.price_currency == "USDT"
    with pytest.raises(ServiceCommandWireError, match="COMMAND_BINDING_ACCOUNT_MISMATCH"):
        derive_command_binding(
            _declaration(),
            service_id=_SERVICE_ID,
            instance_id=_INSTANCE_ID,
            account_ref="account-alias",
        )


# -- host derivation ---------------------------------------------------------


def test_host_derivation_gives_the_client_binding_for_every_target():
    other = WireScope(_DOMAIN, account_ref=_ACCOUNT, strategy_ref="strategy-b")
    targets = {_TEMPLATE: _execution_scope(), other: _execution_scope(strategy="strategy-b")}
    bindings = host_bindings(
        targets, [_declaration()], service_id=_SERVICE_ID, instance_id=_INSTANCE_ID
    )
    expected = client_binding(_declaration(), service_id=_SERVICE_ID, instance_id=_INSTANCE_ID)
    assert set(bindings) == set(targets)
    assert all(binding == expected for binding in bindings.values())


@pytest.mark.parametrize(
    ("declarations", "targets", "code"),
    [
        ([], {_TEMPLATE: _execution_scope()}, "COMMAND_BINDINGS_REQUIRED"),
        (
            [_declaration(account_id="account-z")],
            {_TEMPLATE: _execution_scope()},
            "COMMAND_BINDING_MISSING",
        ),
        (
            [_declaration(), _declaration(instruments=("BTC-USDT",))],
            {_TEMPLATE: _execution_scope()},
            "COMMAND_BINDING_AMBIGUOUS",
        ),
        (
            [_declaration(), _declaration(account_id="account-z")],
            {_TEMPLATE: _execution_scope()},
            "COMMAND_BINDING_UNUSED",
        ),
        (
            [_declaration(provider="okx", venue="okx")],
            {_TEMPLATE: _execution_scope()},
            "COMMAND_BINDING_ACCOUNT_MISMATCH",
        ),
        (
            [_declaration()],
            {
                WireScope(_DOMAIN, account_ref="wire-alias", strategy_ref=_STRATEGY): (
                    _execution_scope()
                )
            },
            "COMMAND_BINDING_ACCOUNT_ALIAS_UNSUPPORTED",
        ),
    ],
)
def test_host_derivation_refuses_missing_ambiguous_or_foreign_facts(declarations, targets, code):
    with pytest.raises(ServiceCommandWireError) as error:
        derive_host_command_bindings(
            declarations, service_id=_SERVICE_ID, instance_id=_INSTANCE_ID, targets=targets
        )
    assert error.value.code == code


def test_host_derivation_checks_loaded_instrument_metadata():
    targets = {_TEMPLATE: _execution_scope()}
    good = {
        "BTC-USDT": CommandInstrumentFact("base", "USDT"),
        "ETH-USDT": CommandInstrumentFact("base", "USDT"),
    }

    def derive(facts):
        return derive_host_command_bindings(
            [_declaration()],
            service_id=_SERVICE_ID,
            instance_id=_INSTANCE_ID,
            targets=targets,
            instrument_facts={_TEMPLATE: facts},
        )

    assert set(derive(good)) == {_TEMPLATE}
    cases = [
        ({"BTC-USDT": good["BTC-USDT"]}, "COMMAND_BINDING_INSTRUMENT_METADATA_MISSING"),
        (
            dict(good, **{"ETH-USDT": CommandInstrumentFact("contracts", "USDT")}),
            "COMMAND_BINDING_METADATA_MISMATCH",
        ),
        (
            dict(good, **{"ETH-USDT": CommandInstrumentFact("base", "BTC")}),
            "COMMAND_BINDING_METADATA_MISMATCH",
        ),
        (
            dict(good, **{"ETH-USDT": CommandInstrumentFact(None, "USDT")}),
            "COMMAND_BINDING_METADATA_FACT_MISSING",
        ),
    ]
    for facts, code in cases:
        with pytest.raises(ServiceCommandWireError) as error:
            derive(facts)
        assert error.value.code == code


def test_risk_registry_facts_bind_the_base_transport_unit_and_quote_currency():
    from bt_api_risk import AccountScope, InstrumentRiskMetadata, InstrumentRiskRegistry

    from bt_api_py.service_risk_order_entry import RiskOrderTarget

    now_ns = time.time_ns()
    metadata = InstrumentRiskMetadata(
        instrument="BTC-USDT",
        metadata_version="typed-risk-metadata-v1",
        as_of_ns=now_ns - 1_000_000,
        expires_at_ns=now_ns + 60_000_000_000,
        tick_size=Decimal("0.01"),
        quantity_step=Decimal("0.001"),
        contract_multiplier=Decimal("1"),
        max_gross_notional=Decimal("100000"),
        quantity_unit="BTC",
        valuation_unit="USDT",
    )
    target = RiskOrderTarget(
        account_scope=AccountScope("paper", _ACCOUNT, "simulation"),
        strategy_id=_STRATEGY,
        exchange_name="PAPER___SPOT",
        provider_account_id=_ACCOUNT,
        registry=InstrumentRiskRegistry((metadata,)),
    )
    facts = risk_instrument_facts(target)
    assert facts == {"BTC-USDT": CommandInstrumentFact("base", "USDT")}
    targets = {_TEMPLATE: target}
    bindings = derive_host_command_bindings(
        [_declaration(instruments=("BTC-USDT",))],
        service_id=_SERVICE_ID,
        instance_id=_INSTANCE_ID,
        targets=targets,
        instrument_facts={_TEMPLATE: facts},
    )
    assert bindings[_TEMPLATE].instruments.keys() == {"BTC-USDT"}
    # A declared instrument the Risk host has no metadata for refuses.
    with pytest.raises(ServiceCommandWireError, match="INSTRUMENT_METADATA_MISSING"):
        derive_host_command_bindings(
            [_declaration()],
            service_id=_SERVICE_ID,
            instance_id=_INSTANCE_ID,
            targets=targets,
            instrument_facts={_TEMPLATE: facts},
        )
    # Risk dispatches the base transport unit: a "contracts" declaration refuses.
    with pytest.raises(ServiceCommandWireError, match="COMMAND_BINDING_METADATA_MISMATCH"):
        derive_host_command_bindings(
            [_declaration(instruments=("BTC-USDT",), quantity_unit="contracts")],
            service_id=_SERVICE_ID,
            instance_id=_INSTANCE_ID,
            targets=targets,
            instrument_facts={_TEMPLATE: facts},
        )


def test_resource_bindings_are_checked_with_the_same_rules():
    targets = {_TEMPLATE: _execution_scope()}
    good = host_bindings(
        targets, [_declaration()], service_id=_SERVICE_ID, instance_id=_INSTANCE_ID
    )
    assert (
        validate_host_command_bindings(
            good, service_id=_SERVICE_ID, instance_id=_INSTANCE_ID, targets=targets
        )
        == good
    )
    with pytest.raises(ServiceCommandWireError, match="COMMAND_BINDING_ROUTE_MISMATCH"):
        validate_host_command_bindings(
            good, service_id=_SERVICE_ID, instance_id="other-owner", targets=targets
        )
    other = WireScope(_DOMAIN, account_ref=_ACCOUNT, strategy_ref="strategy-b")
    with pytest.raises(ServiceCommandWireError, match="COMMAND_BINDING_MISSING"):
        validate_host_command_bindings(
            good,
            service_id=_SERVICE_ID,
            instance_id=_INSTANCE_ID,
            targets={**targets, other: _execution_scope(strategy="strategy-b")},
        )


# -- configuration -----------------------------------------------------------


def _client_config(command_binding=None, *, query_binding=None):
    writer = {
        "mount": "broker.orders",
        "service_id": _SERVICE_ID,
        "instance_id": _INSTANCE_ID,
        "scope_ref": "orders-v1",
        "transport_ref": "rpc-v1",
        "account_ref": _ACCOUNT,
    }
    if command_binding is not None:
        writer["command_binding"] = command_binding
    query = {
        "mount": "broker.order_query",
        "service_id": _SERVICE_ID,
        "instance_id": _INSTANCE_ID,
        "scope_ref": "order-query-v1",
        "transport_ref": "rpc-v1",
        "account_ref": _ACCOUNT,
    }
    if query_binding is not None:
        query["command_binding"] = query_binding
    return {
        "client_config_schema_version": 1,
        "process_role": "strategy_client",
        "registration": {"strategy_ref": _STRATEGY},
        "clients": {
            "execution": {
                "config_schema_version": 1,
                "settings": {"bindings": [writer, query]},
            }
        },
        "communication": {
            "config_schema_version": 1,
            "profiles": {
                "rpc-v1": {
                    "backend": "zmq",
                    "config_schema_version": 1,
                    "endpoints": {"rpc_connect": "tcp://127.0.0.1:1"},
                    "security_profile_ref": "client-curve-v1",
                }
            },
        },
    }


def test_client_config_carries_the_writer_command_binding():
    parsed = parse_client_config(_client_config(_declaration()))
    writer = parsed.clients["execution"]["settings"]["bindings"][0]
    assert parse_command_binding_declaration(writer["command_binding"]) == (
        parse_command_binding_declaration(_declaration())
    )
    with pytest.raises(ServiceConfigError) as error:
        parse_client_config(_client_config(query_binding=_declaration()))
    assert error.value.code == "FIELD_NOT_ALLOWED"
    bad = _declaration()
    bad.pop("quantity_unit")
    with pytest.raises(ServiceConfigError) as error:
        parse_client_config(_client_config(bad))
    assert error.value.code == "COMMAND_BINDING_FACT_MISSING"


def test_assembly_refuses_a_writer_route_without_typed_binding_before_runtime_work():
    calls = []

    def factory(_parsed):
        calls.append(1)
        raise AssertionError("must not be reached")

    with pytest.raises(ServiceExecutionAssemblyError) as error:
        assemble_service_execution(_client_config(), runtime_factory=factory)
    assert error.value.code == "COMMAND_BINDING_REQUIRED"
    with pytest.raises(ServiceExecutionAssemblyError) as error:
        assemble_service_execution(
            _client_config(_declaration(account_id="account-other")), runtime_factory=factory
        )
    assert error.value.code == "COMMAND_BINDING_ACCOUNT_MISMATCH"
    assert calls == []


# -- host component wiring ---------------------------------------------------


class _Provider:
    def __init__(self) -> None:
        self.submitted: list[str] = []

    def submit(self, intent):
        self.submitted.append(intent.intent_id)
        return ProviderObservation.accepted(intent.intent_id, "provider-" + intent.intent_id)

    def cancel(self, intent):  # pragma: no cover - not exercised
        raise AssertionError("not used")


def _prepared(options):
    config = _service_config(
        {
            "execution": {
                "config_schema_version": 2,
                "settings": {
                    "instance_id": _INSTANCE_ID,
                    "bind_ports": ["service.order_entry", "service.order_query"],
                    "options": options,
                },
            }
        },
        service_id=_SERVICE_ID,
    )
    return validate_components(component_specs(config.components))[0]


def _build(tmp_path, options, *, extra=None, identity=_SERVICE_IDENTITY):
    store = SqliteExecutionStore(tmp_path / f"execution-{len(list(tmp_path.iterdir()))}.sqlite3")
    provider = _Provider()
    values = {
        "store": store,
        "targets": {_TEMPLATE: _execution_scope()},
        "dispatch": provider,
        "admission": {"allow_unprotected": True},
    }
    values.update(extra or {})
    component = build_component(
        _prepared(options),
        scope=WireScope(_DOMAIN),
        resource_provider=lambda *_: ComponentResources.borrowed(values, ready=lambda: True),
        service_identity=identity,
    )
    return component, provider, store


def test_configured_host_component_requires_typed_commands(tmp_path):
    from bt_api_py.service_registry import ServiceCallContext

    component, provider, store = _build(
        tmp_path, {"role": "account_owner", "command_bindings": [_declaration()]}
    )
    try:
        scope = WireScope(_DOMAIN, account_ref=_ACCOUNT, strategy_ref=_STRATEGY, run_id="run-1")
        identity = WireIdentity(WireRole.STRATEGY_CLIENT, "strategy-principal", _STRATEGY)
        context = ServiceCallContext(identity, scope, "registration-a", "strategy-principal")
        generic = ServiceExecutionClient.build_submit_payload(
            intent_id="intent-1",
            signal_id="signal-1",
            instrument="BTC-USDT",
            side="BUY",
            position_effect="OPEN",
            order_type="LIMIT",
            quantity="1",
            price="100",
            metadata_version="metadata-v1",
        )
        refused = component.handlers["orders.submit"](context, generic)
        assert (refused["ok"], refused["code"]) == (False, "COMMAND_TYPED_REQUIRED")
        binding = client_binding(_declaration(), service_id=_SERVICE_ID, instance_id=_INSTANCE_ID)
        command = build_submit_command(binding, generic, scope=scope)
        accepted = component.handlers["orders.submit"](context, command_arguments(command))
        assert accepted["ok"] is True, accepted
        assert "command_result" in accepted["result"]
        assert provider.submitted == ["intent-1"]
    finally:
        component.close()
        store.close()


@pytest.mark.parametrize(
    ("options", "extra", "identity", "code"),
    [
        (
            {"role": "account_owner", "command_bindings": [_declaration(account_id="z")]},
            None,
            _SERVICE_IDENTITY,
            "COMMAND_BINDING_MISSING",
        ),
        (
            {"role": "account_owner", "command_bindings": [_declaration()]},
            None,
            None,
            "COMMAND_BINDING_SERVICE_IDENTITY_REQUIRED",
        ),
        (
            {"role": "account_owner", "command_bindings": [_declaration()]},
            "resource",
            _SERVICE_IDENTITY,
            "COMMAND_BINDINGS_CONFLICT",
        ),
        (
            {"role": "account_owner"},
            "foreign-route",
            _SERVICE_IDENTITY,
            "COMMAND_BINDING_ROUTE_MISMATCH",
        ),
    ],
)
def test_host_component_refuses_to_start_on_inconsistent_binding_facts(
    tmp_path, options, extra, identity, code
):
    resource = None
    if extra is not None:
        resource = host_bindings(
            {_TEMPLATE: _execution_scope()},
            [_declaration()],
            service_id=_SERVICE_ID,
            instance_id=_INSTANCE_ID if extra == "resource" else "foreign-owner",
        )
    with pytest.raises(ServiceComponentError) as error:
        _build(
            tmp_path,
            options,
            extra=None if resource is None else {"command_bindings": resource},
            identity=identity,
        )
    assert error.value.code == code


def test_service_config_rejects_invalid_component_declarations():
    with pytest.raises(ServiceConfigError) as error:
        _prepared({"role": "account_owner", "command_bindings": []})
    assert error.value.code == "INVALID_VALUE"
    bad = _declaration()
    bad["instruments"] = []
    with pytest.raises(ServiceConfigError) as error:
        _prepared({"role": "account_owner", "command_bindings": [bad]})
    assert error.value.code == "COMMAND_BINDING_INSTRUMENTS_MISSING"


# -- typed cancel instrument -------------------------------------------------


def test_workflow_cancel_names_the_typed_instrument_of_a_multi_instrument_binding(tmp_path):
    from bt_api_py.service_execution_workflow import (
        ServiceExecutionCancelRequestV1,
        ServiceExecutionWorkflow,
    )
    from tests.unit.test_iter60_typed_order_entry import (
        _Dispatch,
        _FakeRuntime,
        _client,
        _service,
        _submit_kwargs,
    )

    service = _service(tmp_path, _Dispatch())
    submitter = _client(_FakeRuntime(service))
    submitter.submit(**_submit_kwargs("intent-eth", instrument="ETH-USDT"))
    submitter.submit(**_submit_kwargs("intent-eth2", instrument="ETH-USDT"))
    # The submitting client remembers the typed instrument of its intent.
    runtime = _FakeRuntime(service)
    assert (
        submitter.cancel(
            cancel_id="cancel-eth-0",
            target_intent_id="intent-eth",
            provider_order_id="provider-intent-eth",
            metadata_version="typed-v1",
        ).cancel_id
        == "cancel-eth-0"
    )

    # A fresh client (e.g. after restart) has no memory: without the
    # instrument the typed cancel refuses before sending; with it, it works.
    workflow = ServiceExecutionWorkflow(_client(runtime))
    base = dict(
        target_intent_id="intent-eth2",
        provider_order_id="provider-intent-eth2",
        metadata_version="typed-v1",
    )
    refused = workflow.cancel(ServiceExecutionCancelRequestV1(cancel_id="cancel-eth-1", **base))
    # The workflow keeps its conservative projection (only REQUEST_NOT_SENT is
    # a definitive rejection), but nothing reached the service.
    assert refused.code == "COMMAND_INSTRUMENT_REQUIRED"
    assert refused.disposition in {"REJECTED", "UNKNOWN"}
    assert runtime.calls == []
    accepted = workflow.cancel(
        ServiceExecutionCancelRequestV1(cancel_id="cancel-eth-2", instrument="ETH-USDT", **base)
    )
    assert accepted.disposition == "RECORD", accepted
    operation, arguments = runtime.calls[-1]
    assert operation == "orders.cancel"
    assert arguments["command"]["data"]["instrument"]["instrument_id"] == "ETH-USDT"
