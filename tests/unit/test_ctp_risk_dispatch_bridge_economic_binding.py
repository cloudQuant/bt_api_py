"""Focused pre-reservation checks for the CTP/Risk economic binding."""

from dataclasses import replace
from decimal import Decimal

import pytest

import test_ctp_execution_runtime as runtime_fixtures
import test_ctp_runtime_order_chain as order_chain_fixtures


@pytest.mark.parametrize(
    ("field", "mismatch"),
    (
        ("instrument", "cu2612"),
        ("quantity", Decimal("3")),
    ),
)
def test_mapped_risk_economic_mismatch_fails_before_prepare_or_reserve(
    tmp_path, monkeypatch, field, mismatch
):
    with runtime_fixtures._runtime_case(tmp_path, monkeypatch) as case:
        component = runtime_fixtures._build_execution_component(case, monkeypatch)
        order_chain_fixtures._seed_synthetic_order_ref_cutover(case)
        reserve_errors = order_chain_fixtures._observe_authenticated_reserve(
            case, monkeypatch
        )
        bridge = case.resources.values["admission"]["order_gate"]
        adapter = bridge._adapter
        original_mapper = adapter._map_intent

        class MismatchingMapper:
            wire_account_ref = original_mapper.wire_account_ref

            def __call__(self, intent):
                mapped = original_mapper(intent)
                return replace(mapped, **{field: mismatch})

        monkeypatch.setattr(adapter, "_map_intent", MismatchingMapper())
        risk_reserve_calls = []
        original_reserve = adapter.reserve

        def observe_reserve(intent):
            risk_reserve_calls.append(intent)
            return original_reserve(intent)

        monkeypatch.setattr(adapter, "reserve", observe_reserve)

        intent_id = f"ctp-economic-mismatch-{field}"
        reply = component.handlers["orders.submit"](
            order_chain_fixtures._service_context(case),
            order_chain_fixtures._order_arguments(intent_id),
        )

        assert reply["ok"] is True
        assert reply["code"] == "OK"
        record = reply["result"]["record"]
        assert record["state"] == "BLOCKED"
        assert record["unknown_reason"] == "admission_gate_error"
        assert record["dispatch_attempts"] == 0
        assert record["provider_order_id"] is None
        assert reserve_errors and reserve_errors[-1]["error_code"] == (
            "RISK_INTENT_ECONOMIC_MISMATCH"
        )
        assert case.journal.get_command(
            bridge.account_scope,
            order_chain_fixtures._PRINCIPAL,
            intent_id,
        ) is None
        assert risk_reserve_calls == []
        assert order_chain_fixtures._counts(case.factory.instances[0]) == (0, 0, 0)


@pytest.mark.unit
@pytest.mark.parametrize("quantity_unit", ("contracts", "lots"))
def test_supported_units_preserve_exact_quantity_in_prepared_risk_request(
    tmp_path, monkeypatch, quantity_unit
):
    with runtime_fixtures._runtime_case(tmp_path, monkeypatch) as case:
        component = runtime_fixtures._build_execution_component(case, monkeypatch)
        order_chain_fixtures._seed_synthetic_order_ref_cutover(case)
        reserve_errors = order_chain_fixtures._observe_authenticated_reserve(
            case, monkeypatch
        )
        bridge = case.resources.values["admission"]["order_gate"]
        adapter = bridge._adapter
        original_mapper = adapter._map_intent

        class LotsMapper:
            wire_account_ref = original_mapper.wire_account_ref

            def __call__(self, intent):
                return replace(original_mapper(intent), quantity_unit=quantity_unit)

        monkeypatch.setattr(adapter, "_map_intent", LotsMapper())
        intent_id = f"ctp-economic-unit-{quantity_unit}"
        component.handlers["orders.submit"](
            order_chain_fixtures._service_context(case),
            order_chain_fixtures._order_arguments(intent_id),
        )

        assert reserve_errors == [], reserve_errors
        command = case.journal.get_command(
            bridge.account_scope,
            order_chain_fixtures._PRINCIPAL,
            intent_id,
        )
        assert command is not None
        assert command.order_request["quantity_unit"] == quantity_unit
        assert command.order_request["quantity"] == "2"
        assert command.order_request["price"] == "100"
