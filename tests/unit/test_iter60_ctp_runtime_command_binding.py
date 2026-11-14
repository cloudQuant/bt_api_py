"""CTP command configuration and retired owner resource-provider checks.

The independent command construction test does not exercise a service host,
the direct runtime, network, provider, SimNow or an account.
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
    ServiceCommandWireError,
    build_cancel_command,
    build_submit_command,
    command_arguments,
    derive_command_binding,
)
from bt_api_py.service_components import build_component
from bt_api_py.service_config import ServiceConfigError

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
        (_declaration(), {"role": "account_owner"}, "LEGACY_EXECUTION_OWNER_RETIRED"),
        (None, {"role": "account_owner"}, "LEGACY_EXECUTION_OWNER_RETIRED"),
        (
            _declaration(),
            {"role": "account_owner", "command_bindings": (_declaration(quantity_unit="lots"),)},
            "LEGACY_EXECUTION_OWNER_RETIRED",
        ),
        (
            _declaration(),
            {"role": "account_owner", "command_bindings": (_declaration(), _declaration())},
            "LEGACY_EXECUTION_OWNER_RETIRED",
        ),
    ],
)
def test_retired_host_owner_build_is_refused_before_any_runtime_work(
    tmp_path, command_binding, options, code
):
    provider = CtpExecutionResourceProvider(_config(tmp_path, command_binding=command_binding))
    with pytest.raises(CtpExecutionRuntimeError) as error:
        provider("execution", "execution-owner", MappingProxyType(options), _SERVICE_SCOPE)
    assert error.value.code == code
    assert provider.last_runtime is None and provider._used is False
    assert not (tmp_path / "execution-runtime.sqlite3").exists()


def test_retired_service_host_is_rejected_before_resource_factory(tmp_path, monkeypatch):
    provider = CtpExecutionResourceProvider(_config(tmp_path, command_binding=_declaration()))
    factory_calls = []

    def resource_provider(*args):
        factory_calls.append(args)
        return provider(*args)

    # The configuration is only parsed; no listener needs to bind a port.
    monkeypatch.setitem(
        runtime_fixtures.service_component_fixtures._service_config.__globals__,
        "_endpoint",
        lambda: "tcp://127.0.0.1:7911",
    )
    with pytest.raises(ServiceConfigError) as error:
        build_component(
            runtime_fixtures.service_component_fixtures._prepared({"role": "account_owner"}),
            scope=_SERVICE_SCOPE,
            resource_provider=resource_provider,
            service_identity=WireIdentity(WireRole.SERVICE, "execution-host", "execution-service"),
        )
    assert error.value.code == "LEGACY_EXECUTION_OWNER_RETIRED"
    assert factory_calls == []
    assert provider.last_runtime is None
    assert provider._used is False
    assert not (tmp_path / "execution-runtime.sqlite3").exists()


def test_only_the_direct_bootstrap_sentinel_skips_the_service_binding_rule():
    assert DIRECT_MANAGED_OWNER_OPTIONS == {"role": "account_owner"}
    # An equal plain mapping is not the sentinel (see the parametrized refusal).
    assert MappingProxyType({"role": "account_owner"}) is not DIRECT_MANAGED_OWNER_OPTIONS


# -- neutral command construction without the retired host --------------------


def _generic_submit(intent_id):
    return chain_fixtures._order_arguments(intent_id)


def test_ctp_declaration_builds_neutral_submit_and_cancel_commands(tmp_path):
    declaration = _config(tmp_path, command_binding=_declaration()).command_binding_declaration
    binding = derive_command_binding(
        declaration,
        service_id="execution-service",
        instance_id="execution-owner",
        account_ref=_SCOPE.account_ref,
    )
    scope = WireScope(
        _SERVICE_SCOPE.execution_domain_ref,
        account_ref=_SCOPE.account_ref,
        strategy_ref=_SCOPE.strategy_id,
        run_id="synthetic-run",
    )
    assert binding.route == "execution-service/execution-owner"
    instrument = binding.instruments["rb2610"]
    assert (instrument.venue, instrument.product_type, instrument.native_symbol) == (
        "SHFE", "future", "rb2610"
    )
    assert (binding.quantity_unit, binding.price_currency) == ("contracts", "CNY")
    assert (binding.position_mode, binding.hedge_class) == ("dual_side", "speculation")

    submit = build_submit_command(
        binding, _generic_submit("typed-intent"), scope=scope, request_id="submit-request"
    )
    assert (submit.account_key, submit.instrument, submit.route) == (
        binding.account_key, instrument, binding.route
    )
    assert (submit.strategy_ref, submit.run_id, submit.idempotency_key) == (
        _SCOPE.strategy_id, "synthetic-run", "typed-intent"
    )
    assert set(command_arguments(submit)) == {"command"}

    cancel = build_cancel_command(
        binding,
        {
            "cancel_id": "typed-cancel",
            "target_intent_id": "typed-intent",
            "provider_order_id": "synthetic-provider-order",
            "metadata_version": "ctp-runtime-chain-v1",
            "tags": {},
        },
        instrument=None,
        scope=scope,
        request_id="cancel-request",
    )
    assert (cancel.account_key, cancel.instrument, cancel.route) == (
        binding.account_key, instrument, binding.route
    )
    assert (cancel.strategy_ref, cancel.run_id, cancel.idempotency_key) == (
        _SCOPE.strategy_id, "synthetic-run", "typed-cancel"
    )
    assert set(command_arguments(cancel)) == {"command"}

    with pytest.raises(ServiceCommandWireError, match="COMMAND_BINDING_ACCOUNT_MISMATCH"):
        derive_command_binding(
            declaration, service_id="execution-service", instance_id="execution-owner",
            account_ref="different-account",
        )
    with pytest.raises(ServiceCommandWireError, match="COMMAND_INSTRUMENT_UNBOUND"):
        build_submit_command(
            binding, dict(_generic_submit("typed-other"), instrument="unknown"), scope=scope
        )
