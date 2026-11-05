"""Iteration 62 (62.08, I62-10): the managed CTP runtime under SDK write admission.

``bt_api_py.ctp_gateway_execution`` composes the managed CTP execution runtime
for a Gateway execution host with bt_api_py's environment-neutral write
admission instead of the SimNow approval/credential-binding allow-lists:

* admission on: the runtime boots READY with the native gate armed under the
  SDK-admitted profile for its one instrument (after settlement confirmation
  under a one-shot SDK grant), and a typed ``orders.submit`` reaches the native
  ``ReqOrderInsert`` through the managed lease (the per-write gate
  revalidation still runs); a trade callback fills the order;
* admission off: the runtime refuses during preparation, before any
  settlement confirmation, arm or native write.

UNIT/FAKE + synthetic native leaf of ``test_ctp_execution_runtime`` (production
TraderClient/Store/owner/ports over a deterministic CTP API; no network). The
first OrderRef reservation uses the existing *synthetic* cutover seed of
``test_ctp_runtime_order_chain``: production has no ``CtpOrderRefSeedProof``
issuer yet (iteration 62 report, GAP). Run with the CTP test fixtures on
``PYTHONPATH`` like the other ``test_ctp_runtime_*`` modules:
``PYTHONPATH=bt_api/bt_api_ctp/tests:tests/unit``.
"""

from __future__ import annotations

import sys
from contextlib import contextmanager, suppress
from pathlib import Path

# The managed-runtime fixtures live in sibling test modules (this directory and
# bt_api_ctp/tests); make them importable without an external PYTHONPATH.
_HERE = Path(__file__).resolve()
for _fixture_dir in (_HERE.parent, _HERE.parents[2] / "bt_api" / "bt_api_ctp" / "tests"):
    if str(_fixture_dir) not in sys.path:
        sys.path.insert(0, str(_fixture_dir))
from types import SimpleNamespace

import pytest
import test_ctp_callback_evidence as callback_fixtures
import test_ctp_execution_runtime as runtime_fixtures
import test_ctp_runtime_order_chain as chain_fixtures
from bt_api_ctp.ctp.client import SDK_ADMITTED_PROFILE
from bt_api_execution import ExecutionState, OrderIntent
from test_ctp_runtime_zero_fill_risk import _prepare_runtime_order

from bt_api_py.ctp_execution_runtime import (
    DIRECT_MANAGED_OWNER_OPTIONS,
    CtpExecutionResourceProvider,
)
from bt_api_py.ctp_gateway_execution import (
    CtpGatewayExecutionSettings,
    build_ctp_gateway_execution,
)
from bt_api_py.service_components import ServiceResourceConstructionError
from bt_api_py.write_admission import WriteAdmission

_ROUTE = "CTP___FUTURE"


def _settings(tmp_path):
    return CtpGatewayExecutionSettings(
        symbol=runtime_fixtures._INSTRUMENT,
        exchange_id="SHFE",
        strategy_ref="strategy.bt-gateway-sdk-admission",
        trading_day=runtime_fixtures._TRADING_DAY,
        store_directory=tmp_path,
        td_front=runtime_fixtures._SIMNOW_TD_FRONT,
        md_front=runtime_fixtures._SIMNOW_MD_FRONT,
        broker_id=runtime_fixtures._BROKER_ID,
        user_id=runtime_fixtures._USER_ID,
        password="synthetic-only",
        price_tick=__import__("decimal").Decimal("0.01"),
        startup_timeout_s=0.5,
    )


@contextmanager
def _case(tmp_path, monkeypatch, *, admitted: bool):
    factory = runtime_fixtures._native_factory()
    runtime_fixtures._install_native_leaf(monkeypatch, tmp_path, factory)
    composition = build_ctp_gateway_execution(
        _settings(tmp_path), write_admission=WriteAdmission({_ROUTE: admitted})
    )
    case = SimpleNamespace(
        config=composition.config,
        provider=CtpExecutionResourceProvider(composition.config),
        factory=factory,
        execution_scope=composition.execution_scope,
        service_scope=composition.service_scope,
        instance_id=runtime_fixtures._INSTANCE_ID,
        resources=None,
        component=None,
        risk_gate=None,
        journal=None,
        retain_resources_on_cleanup=False,
    )
    try:
        yield case
    finally:
        for api in factory.instances:
            api.join_release.set()
        if case.component is not None:
            with suppress(Exception):
                case.component.close()
        elif case.resources is not None:
            with suppress(Exception):
                case.resources.close()
        composition.close()


def _native_calls(case):
    return [call[0] for api in case.factory.instances for call in api.calls]


@pytest.mark.unit
def test_admitted_runtime_boots_with_the_gate_armed_under_sdk_admission(
    tmp_path, monkeypatch
):
    with _case(tmp_path, monkeypatch, admitted=True) as case:
        case.resources = case.provider(
            "execution", case.instance_id, DIRECT_MANAGED_OWNER_OPTIONS, case.service_scope
        )
        runtime = case.provider.last_runtime
        assert runtime.status.ready is True, runtime.status
        gate = runtime._trader.get_execution_gate_state()
        assert gate["managed"] is True
        assert gate["sdk_write_admission"] is True
        assert gate["armed"] is True
        assert gate["environment_profile"] == SDK_ADMITTED_PROFILE
        assert gate["runtime_simnow_credential_binding_configured"] is False
        calls = _native_calls(case)
        # D62-20: the day's settlement was read back (the synthetic server
        # reports it confirmed), so no confirmation write was needed.
        assert "ReqQrySettlementInfoConfirm" in calls
        assert "ReqOrderInsert" not in calls and "ReqOrderAction" not in calls


@pytest.mark.unit
def test_admission_off_refuses_before_settlement_arm_or_any_write(tmp_path, monkeypatch):
    with _case(tmp_path, monkeypatch, admitted=False) as case:
        with pytest.raises(ServiceResourceConstructionError) as caught:
            case.provider(
                "execution", case.instance_id, DIRECT_MANAGED_OWNER_OPTIONS, case.service_scope
            )
        case.resources = caught.value.resources
        runtime = case.provider.last_runtime
        assert runtime.status.ready is False
        assert runtime.status.code == "TRADER_PREPARATION_FAILED"
        gate = runtime._trader.get_execution_gate_state()
        assert gate["armed"] is False and gate["sdk_write_admission"] is False
        calls = _native_calls(case)
        assert "ReqSettlementInfoConfirm" not in calls
        assert "ReqOrderInsert" not in calls and "ReqOrderAction" not in calls


@pytest.mark.unit
def test_admitted_typed_submit_reaches_the_native_insert_and_a_trade_fills_it(
    tmp_path, monkeypatch
):
    intent_id = "bt-gateway-sdk-admitted-submit"
    with _case(tmp_path, monkeypatch, admitted=True) as case:
        component, api, callback_case, _binding, reserve_errors, _queries = (
            _prepare_runtime_order(case, monkeypatch, intent_id)
        )
        context = chain_fixtures._service_context(case)
        submitted = runtime_fixtures._typed_submit(case, component)(
            context, chain_fixtures._order_arguments(intent_id)
        )
        assert reserve_errors == [], reserve_errors
        assert submitted["ok"] is True, submitted.get("code")
        assert submitted["result"]["record"]["state"] == ExecutionState.ACKED.value
        inserts = [call for call in api.calls if call[0] == "ReqOrderInsert"]
        assert len(inserts) == 1
        # The per-write native gate stayed armed under SDK admission only.
        gate = case.provider.last_runtime._trader.get_execution_gate_state()
        assert gate["sdk_write_admission"] is True and gate["armed"] is True

        store = case.resources.values["store"]
        intent = store.get_intent(intent_id, scope=case.execution_scope)
        reservation = store.read_ctp_order_identity(case.execution_scope, intent_id)
        assert type(intent) is OrderIntent and reservation is not None
        callback_fixtures._emit_trade(
            callback_case,
            intent,
            reservation,
            trade_id="SDK-ADMITTED-TRADE-001",
            sequence=1,
            volume=2,
        )
        callback_fixtures._emit_order_status(
            callback_case,
            intent,
            reservation,
            inserts[0][2],
            status="0",
            submit_status="3",
            traded=2,
            sequence=2,
        )
        runtime = case.provider.last_runtime
        applied = runtime._execution_owner.pump_callbacks(32)
        assert len(applied) == 2
        assert applied[0].trade_quantity == 2 and applied[0].duplicate is False
        assert applied[1].projection_state == "FILLED"
        command_id = chain_fixtures._submit_binding(case, intent_id)[1]
        snapshot = store.read_ctp_owner_callback_observation_snapshot(
            case.execution_scope, command_id
        )
        assert snapshot.provider_state == "FILLED"
        assert snapshot.filled_quantity == 2
        # Fees are not invented (CTP trade rows carry none).
        assert snapshot.cumulative_commission is None
