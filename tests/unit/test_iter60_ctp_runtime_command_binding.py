"""Iteration 60: the CTP execution runtime host takes only typed order commands.

UNIT/FAKE (configuration and resource-provider checks) plus the synthetic
native runtime fixture of ``test_ctp_execution_runtime`` (production
TraderClient/Store/owner code over a deterministic CTP API leaf; no network,
front, SimNow or account). Run with the CTP test fixtures on ``PYTHONPATH``
like the other ``test_ctp_runtime_*`` modules.
"""

from __future__ import annotations

from types import MappingProxyType

import pytest
import test_ctp_execution_runtime as runtime_fixtures
import test_ctp_runtime_order_chain as chain_fixtures
from bt_api_base.service_protocol import WireIdentity, WireRole, WireScope
from bt_api_execution.contracts import ExecutionScope

from bt_api_py.ctp_admission_authority import CtpNativeDispatchBindingV1
from bt_api_py.ctp_execution_runtime import (
    DIRECT_MANAGED_OWNER_OPTIONS,
    CtpExecutionResourceProvider,
    CtpExecutionRuntimeConfig,
    CtpExecutionRuntimeConfigError,
    CtpExecutionRuntimeError,
    CtpTraderConnectionConfig,
)
from bt_api_py.service_command_wire import (
    build_cancel_command,
    build_submit_command,
    command_arguments,
    derive_command_binding,
    verify_command_reply,
)
from bt_api_py.service_components import ServiceComponentError, build_component

_SCOPE = ExecutionScope(
    "ctp", "simulation", "ctp-account-ref.v1:" + "c" * 64, "strategy.ctp-typed", "20260926"
)
_SERVICE_SCOPE = WireScope(
    "ctp-typed-domain", account_ref=_SCOPE.account_ref, strategy_ref=_SCOPE.strategy_id
)


def _declaration(**changes):
    return runtime_fixtures._command_binding_declaration(_SCOPE, **changes)


def _config(tmp_path, *, command_binding, native_instrument="rb2610", exchange_id="SHFE"):
    return CtpExecutionRuntimeConfig(
        store_path=tmp_path / "execution-runtime.sqlite3",
        execution_scope=_SCOPE,
        service_scope=_SERVICE_SCOPE,
        lease_owner_id="ctp-typed-owner",
        trader=CtpTraderConnectionConfig(
            front="tcp://typed-td.invalid:41001",
            md_front="tcp://typed-md.invalid:41002",
            ctp_env_profile="config_front_pair",
            broker_id="9999",
            user_id="investor-1",
            password="synthetic-only",
        ),
        native_binding=CtpNativeDispatchBindingV1(
            account_ref=_SCOPE.account_ref,
            scope_key=_SCOPE.key,
            investor_id="investor-1",
            intent_instrument=native_instrument,
            provider_instrument=native_instrument,
            exchange_id=exchange_id,
        ),
        prepare_trader=lambda _trader: None,
        basic_admission_factory=lambda *_args: None,
        risk_admission_factory=lambda _context: {},
        command_binding=command_binding,
    )


# -- configuration time --------------------------------------------------------


def test_declared_facts_are_checked_at_configuration_time(tmp_path):
    config = _config(tmp_path, command_binding=_declaration())
    declaration = config.command_binding_declaration
    assert declaration == _declaration()  # already canonical
    declaration["quantity_unit"] = "mutated"
    assert config.command_binding_declaration["quantity_unit"] == "contracts"
    # Optional facts may be omitted: the command then states nothing.
    minimal = _declaration()
    for name in ("price_currency", "position_mode", "hedge_class"):
        del minimal[name]
    assert _config(tmp_path, command_binding=minimal).command_binding_declaration == minimal
    # The direct managed bootstrap serves no wire order route.
    assert _config(tmp_path, command_binding=None).command_binding_declaration is None


def _without(name):
    declaration = _declaration()
    del declaration[name]
    return declaration


def _instrument(**changes):
    declaration = _declaration()
    declaration["instruments"] = [dict(declaration["instruments"][0], **changes)]
    return declaration


@pytest.mark.parametrize(
    ("declaration", "code"),
    [
        (_without("quantity_unit"), "COMMAND_BINDING_FACT_MISSING"),
        (_without("instruments"), "COMMAND_BINDING_FACT_MISSING"),
        (_declaration(quantity_unit="base"), "COMMAND_BINDING_QUANTITY_UNIT_UNSUPPORTED"),
        (_declaration(quantity_unit="native"), "COMMAND_BINDING_QUANTITY_UNIT_UNSUPPORTED"),
        (_declaration(price_currency="USD"), "COMMAND_BINDING_PRICE_CURRENCY_UNSUPPORTED"),
        (_declaration(position_mode="net"), "COMMAND_BINDING_POSITION_MODE_UNSUPPORTED"),
        (_declaration(hedge_class="hedge"), "COMMAND_BINDING_HEDGE_CLASS_UNSUPPORTED"),
        (_instrument(product_type="option"), "COMMAND_BINDING_INSTRUMENT_MISMATCH"),
        (_instrument(instrument_id="cu2612", native_symbol="cu2612"),
         "COMMAND_BINDING_INSTRUMENT_MISMATCH"),
        (_instrument(native_symbol="RB2610"), "COMMAND_BINDING_INSTRUMENT_MISMATCH"),
        (
            dict(_declaration(), account_key=dict(_declaration()["account_key"], account_id="z")),
            "COMMAND_BINDING_ACCOUNT_MISMATCH",
        ),
        (dict(_declaration(), unknown_fact=1), "COMMAND_BINDING_FIELD_UNKNOWN"),
    ],
)
def test_missing_or_untrue_facts_fail_at_configuration_time(tmp_path, declaration, code):
    with pytest.raises(CtpExecutionRuntimeConfigError) as error:
        _config(tmp_path, command_binding=declaration)
    assert error.value.code == code
    assert isinstance(error.value, ValueError)


def test_declared_venue_must_be_the_native_exchange(tmp_path):
    with pytest.raises(CtpExecutionRuntimeConfigError) as error:
        _config(tmp_path, command_binding=_declaration(), exchange_id="DCE")
    assert error.value.code == "COMMAND_BINDING_INSTRUMENT_MISMATCH"


# -- host start ------------------------------------------------------------------


@pytest.mark.parametrize(
    ("command_binding", "options", "code"),
    [
        (_declaration(), {"role": "account_owner"}, "COMMAND_BINDING_HOST_UNDECLARED"),
        (None, {"role": "account_owner"}, "COMMAND_BINDING_REQUIRED"),
        (
            _declaration(),
            {"role": "account_owner", "command_bindings": (_declaration(quantity_unit="lots"),)},
            "COMMAND_BINDING_CONFIG_MISMATCH",
        ),
        (
            _declaration(),
            {"role": "account_owner", "command_bindings": (_declaration(), _declaration())},
            "COMMAND_BINDING_CONFIG_MISMATCH",
        ),
    ],
)
def test_a_binding_less_host_build_is_refused_before_any_runtime_work(
    tmp_path, command_binding, options, code
):
    provider = CtpExecutionResourceProvider(_config(tmp_path, command_binding=command_binding))
    with pytest.raises(CtpExecutionRuntimeError) as error:
        provider("execution", "execution-owner", MappingProxyType(options), _SERVICE_SCOPE)
    assert error.value.code == code
    assert provider.last_runtime is None and provider._used is False
    assert not (tmp_path / "execution-runtime.sqlite3").exists()


def test_service_host_build_without_bindings_never_starts_the_runtime(tmp_path):
    provider = CtpExecutionResourceProvider(_config(tmp_path, command_binding=_declaration()))
    with pytest.raises(ServiceComponentError) as error:
        build_component(
            runtime_fixtures.service_component_fixtures._prepared({"role": "account_owner"}),
            scope=_SERVICE_SCOPE,
            resource_provider=provider,
            service_identity=WireIdentity(WireRole.SERVICE, "execution-host", "execution-service"),
        )
    assert error.value.code == "RESOURCE_RESOLUTION_FAILED"
    assert provider.last_runtime is None
    assert not (tmp_path / "execution-runtime.sqlite3").exists()


def test_only_the_direct_bootstrap_sentinel_skips_the_service_binding_rule():
    assert DIRECT_MANAGED_OWNER_OPTIONS == {"role": "account_owner"}
    # An equal plain mapping is not the sentinel (see the parametrized refusal).
    assert MappingProxyType({"role": "account_owner"}) is not DIRECT_MANAGED_OWNER_OPTIONS


# -- typed host on the synthetic native runtime ---------------------------------


def _generic_submit(intent_id):
    return chain_fixtures._order_arguments(intent_id)


def test_ctp_runtime_host_accepts_only_typed_commands_with_the_clients_binding(
    tmp_path, monkeypatch
):
    with runtime_fixtures._runtime_case(tmp_path, monkeypatch) as case:
        component = runtime_fixtures._build_execution_component(case, monkeypatch)
        context = chain_fixtures._service_context(case)
        adapter = component.handlers["orders.submit"].__kwdefaults__["_adapter"]
        client = runtime_fixtures._client_command_binding(case)
        # One declaration, one derivation: host and client hold the same binding.
        assert adapter._command_gate._bindings == {case.service_scope: client}
        assert client.route == "execution-service/" + case.instance_id
        instrument = client.instruments["rb2610"]
        assert (instrument.venue, instrument.product_type, instrument.native_symbol) == (
            "SHFE",
            "future",
            "rb2610",
        )
        assert (client.quantity_unit, client.price_currency) == ("contracts", "CNY")
        assert (client.position_mode, client.hedge_class) == ("dual_side", "speculation")

        store = case.resources.values["store"]
        generic = component.handlers["orders.submit"](context, _generic_submit("typed-generic"))
        assert (generic["ok"], generic["code"]) == (False, "COMMAND_TYPED_REQUIRED")
        assert store.get("typed-generic", scope=case.execution_scope) is None

        # Another (still CTP-valid) declaration: the host's binding is refused.
        other = derive_command_binding(
            runtime_fixtures._command_binding_declaration(
                case.execution_scope, quantity_unit="lots"
            ),
            service_id="execution-service",
            instance_id=case.instance_id,
            account_ref=case.execution_scope.account_ref,
        )
        foreign = build_submit_command(other, _generic_submit("typed-foreign"), scope=context.scope)
        mismatched = component.handlers["orders.submit"](context, command_arguments(foreign))
        assert (mismatched["ok"], mismatched["code"]) == (False, "COMMAND_BINDING_MISMATCH")
        assert store.get("typed-foreign", scope=case.execution_scope) is None

        command = build_submit_command(client, _generic_submit("typed-intent"), scope=context.scope)
        accepted = component.handlers["orders.submit"](context, command_arguments(command))
        assert accepted["ok"] is True, accepted.get("code")
        typed = verify_command_reply(accepted["result"], command)
        assert typed.idempotency_key == "typed-intent"
        assert store.get("typed-intent", scope=case.execution_scope) is not None

        cancel_payload = {
            "cancel_id": "typed-cancel",
            "target_intent_id": "typed-intent",
            "provider_order_id": "synthetic-provider-order",
            "metadata_version": "ctp-runtime-chain-v1",
            "tags": {},
        }
        generic_cancel = component.handlers["orders.cancel"](context, cancel_payload)
        assert (generic_cancel["ok"], generic_cancel["code"]) == (False, "COMMAND_TYPED_REQUIRED")
        # A cancel pins account, route and instrument (not the quantity unit).
        foreign_route = derive_command_binding(
            case.provider.config.command_binding_declaration,
            service_id="execution-service",
            instance_id="another-owner",
            account_ref=case.execution_scope.account_ref,
        )
        wrong_cancel = build_cancel_command(
            foreign_route, cancel_payload, instrument=None, scope=context.scope
        )
        refused = component.handlers["orders.cancel"](context, command_arguments(wrong_cancel))
        assert (refused["ok"], refused["code"]) == (False, "COMMAND_BINDING_MISMATCH")
        cancel = build_cancel_command(client, cancel_payload, instrument=None, scope=context.scope)
        typed_cancel = component.handlers["orders.cancel"](context, command_arguments(cancel))
        # Past the typed gate: the host's own cancel rules decide the outcome.
        assert not str(typed_cancel.get("code")).startswith("COMMAND_"), typed_cancel.get("code")
