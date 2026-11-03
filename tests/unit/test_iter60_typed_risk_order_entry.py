"""Iteration 60 R2-B: the typed orders.submit gate on ``RiskOrderEntryService``.

The Risk account owner applies the same ``TypedOrderCommandGate`` as the
Execution account owner: one typed part, decoded once (fingerprint checked by
the Base codec), bound to the authenticated scope and the host's derived
binding, converted to the Risk request form, and answered with a typed result
projected from the durable record. UNIT/FAKE: real Risk parser and journal
objects, the provider write path is replaced by a recording stub.
"""

from __future__ import annotations

import time
from dataclasses import replace
from decimal import Decimal

import pytest
from bt_api_base.service_protocol import WireIdentity, WireRole, WireScope
from bt_api_risk import (
    AccountScope,
    DurableRiskAdmissionAdapter,
    DurableRiskGate,
    InstrumentRiskAdmissionMapper,
    InstrumentRiskMetadata,
    InstrumentRiskRegistry,
    RiskPolicy,
)
from bt_api_risk.risk_order_journal_contracts import RiskOrderJournalError

from bt_api_py.bt_api import BtApi
from bt_api_py.service_command_wire import (
    build_submit_command,
    command_arguments,
    derive_host_command_bindings,
    risk_instrument_facts,
    verify_command_reply,
)
from bt_api_py.service_execution import ServiceExecutionClient
from bt_api_py.service_risk_order_entry import (
    RiskBtApiProviderContract,
    RiskOrderEntryError,
    RiskOrderEntryService,
    RiskOrderTarget,
    _parse_order,
    validate_service_options,
)
from bt_api_py.service_risk_order_journal import RiskOrderJournal
from tests.unit._typed_command_bindings import client_binding, declaration

_ACCOUNT = "risk-account-a"
_STRATEGY = "risk-strategy-a"
_PRINCIPAL = "risk-principal-a"
_SERVICE_ID = "risk-service"
_INSTANCE_ID = "risk-owner"
_TEMPLATE = WireScope("risk-typed-domain-v1", account_ref=_ACCOUNT, strategy_ref=_STRATEGY)
_SCOPE = replace(_TEMPLATE, run_id="risk-run-1")
_IDENTITY = WireIdentity(WireRole.STRATEGY_CLIENT, _PRINCIPAL, _STRATEGY)
_DECLARATION = declaration(
    provider="fixture",
    venue="fixture",
    environment="simulation",
    account_id=_ACCOUNT,
    instruments=("BTC-USD",),
    price_currency="USD",
)


@pytest.fixture
def risk_service(tmp_path):
    now_ns = time.time_ns()
    metadata = InstrumentRiskMetadata(
        instrument="BTC-USD",
        metadata_version="risk-typed-metadata-v1",
        as_of_ns=now_ns - 1_000_000,
        expires_at_ns=now_ns + 600_000_000_000,
        tick_size=Decimal("0.01"),
        quantity_step=Decimal("0.001"),
        contract_multiplier=Decimal("1"),
        max_gross_notional=Decimal("100000"),
        quantity_unit="BTC",
        valuation_unit="USD",
    )
    registry = InstrumentRiskRegistry((metadata,))
    account_scope = AccountScope("fixture", _ACCOUNT, "simulation")
    target = RiskOrderTarget(
        account_scope=account_scope,
        strategy_id=_STRATEGY,
        exchange_name="FIXTURE___SPOT",
        provider_account_id=_ACCOUNT,
        registry=registry,
    )
    mapper = InstrumentRiskAdmissionMapper(
        account_scope, registry, clock_ns=time.time_ns, wire_account_ref=_ACCOUNT
    )
    journal = RiskOrderJournal(tmp_path / "risk-orders.sqlite3", writer_id="risk-typed-test")
    gate = DurableRiskGate(
        tmp_path / "risk-gate.sqlite3",
        RiskPolicy("risk-typed-policy-v1", Decimal("100000"), 10),
        execution_journal_authority=journal,
    )
    contract = RiskBtApiProviderContract(
        exchange_name="FIXTURE___SPOT",
        profile_id="risk-typed-provider-v1",
        order_schema="risk-typed-order-v1",
        fill_schema="risk-typed-fill-v1",
        client_order_id_query=True,
        client_order_id_query_unique=True,
        complete_order_snapshot=True,
        complete_fill_history=True,
        provider_accepts_client_order_id=True,
        provider_status_mapping=(
            ("cancelled", "canceled"),
            ("expired", "expired"),
            ("filled", "completed"),
            ("open", "accepted"),
            ("partially_filled", "partial"),
            ("rejected", "rejected"),
        ),
        supported_order_types=("LIMIT",),
        supported_time_in_force=("GTC",),
        supported_position_effects=("OPEN",),
        order_revision_field="order_revision",
        fill_revision_field="fill_revision",
        fill_watermark_field="fill_watermark",
        fee_amount_field="fee_amount",
        fee_currency_field="fee_currency",
        quote_currency_by_instrument=(("BTC-USD", "USD"),),
    )
    targets = {_TEMPLATE: target}
    bindings = derive_host_command_bindings(
        [_DECLARATION],
        service_id=_SERVICE_ID,
        instance_id=_INSTANCE_ID,
        targets=targets,
        instrument_facts={_TEMPLATE: risk_instrument_facts(target)},
    )
    bt_api = BtApi()
    service = RiskOrderEntryService(
        bt_api,
        targets,
        {_TEMPLATE: DurableRiskAdmissionAdapter(gate, mapper)},
        journal,
        gate,
        {_TEMPLATE: contract},
        command_bindings=bindings,
    )
    try:
        yield service, target, contract, metadata
    finally:
        bt_api.close()


def _payload(intent_id="risk-intent-1", **changes):
    values = dict(
        intent_id=intent_id,
        signal_id="signal-" + intent_id,
        instrument="BTC-USD",
        side="BUY",
        position_effect="OPEN",
        order_type="LIMIT",
        quantity="0.1",
        price="100",
        time_in_force="GTC",
        metadata_version="risk-typed-metadata-v1",
    )
    values.update(changes)
    return ServiceExecutionClient.build_submit_payload(**values)


def _binding():
    return client_binding(_DECLARATION, service_id=_SERVICE_ID, instance_id=_INSTANCE_ID)


def _handle(service, arguments):
    return service.handle(
        "orders.submit",
        principal_ref=_PRINCIPAL,
        identity=_IDENTITY,
        scope=_SCOPE,
        arguments=arguments,
    )


def _record(intent_id, state="ACKED_TRACKED"):
    return {
        "intent_id": intent_id,
        "scope_key": "risk-scope",
        "payload_sha256": "0" * 64,
        "state": state,
        "provider_order_id": "provider-" + intent_id if state != "UNKNOWN" else None,
        "filled_quantity": "0",
        "average_price": None,
        "cumulative_commission": None,
        "permit_reference": "permit-1",
        "dispatch_attempts": 1,
        "unknown_reason": "DISPATCH_OUTCOME_UNKNOWN" if state == "UNKNOWN" else None,
        "review_required": state == "UNKNOWN",
        "created_at_ns": 1,
        "updated_at_ns": 2,
        "is_terminal": False,
    }


def test_risk_owner_refuses_generic_and_tampered_submits_before_any_work(risk_service, monkeypatch):
    service, *_ = risk_service
    calls = []
    monkeypatch.setattr(service, "_submit", lambda **kwargs: calls.append(kwargs))
    assert service.typed_commands is True
    generic = _handle(service, dict(_payload()))
    assert (generic["ok"], generic["code"]) == (False, "COMMAND_TYPED_REQUIRED")
    command = build_submit_command(_binding(), _payload(), scope=_SCOPE)
    part = dict(command_arguments(command)["command"])
    part["data"] = dict(part["data"], quantity="5")
    tampered = _handle(service, {"command": part})
    assert tampered["code"] == "WIRE_COMMAND_FINGERPRINT_MISMATCH"
    for change in (
        {"quantity_unit": "contracts"},
        {"route": "other-service/other-owner"},
        {"price_currency": "USDT"},
    ):
        wrong = replace(command, **change)
        reply = _handle(service, command_arguments(wrong))
        assert (reply["ok"], reply["code"]) == (False, "COMMAND_BINDING_MISMATCH")
    other_run = build_submit_command(
        _binding(), _payload(), scope=replace(_SCOPE, run_id="risk-run-other")
    )
    assert _handle(service, command_arguments(other_run))["code"] == "COMMAND_SCOPE_MISMATCH"
    assert calls == []


def test_risk_owner_converts_typed_submit_to_the_risk_request_and_answers_typed(
    risk_service, monkeypatch
):
    service, target, contract, _metadata = risk_service
    seen = []

    def fake_submit(**kwargs):
        seen.append(kwargs["arguments"])
        return {
            "ok": True,
            "code": "OK",
            "result": {"found": True, "record": _record(kwargs["arguments"]["intent_id"])},
        }

    monkeypatch.setattr(service, "_submit", fake_submit)
    command = build_submit_command(_binding(), _payload(), scope=_SCOPE)
    reply = _handle(service, command_arguments(command))
    assert reply["ok"] is True
    typed = verify_command_reply(reply["result"], command)
    assert typed.outcome == "accepted" and typed.provider_order_ref == "provider-risk-intent-1"
    # The converted request is exactly the Risk request form and parses.
    (request,) = seen
    parsed = _parse_order(
        request, target, contract=contract, scope=_SCOPE, principal_ref=_PRINCIPAL
    )
    assert parsed["quantity"] == Decimal("0.1") and parsed["price"] == Decimal("100")
    assert parsed["quantity_unit"] == "base"


def test_risk_owner_maps_unknown_and_intent_conflict(risk_service, monkeypatch):
    service, *_ = risk_service
    monkeypatch.setattr(
        service,
        "_submit",
        lambda **kwargs: {
            "ok": True,
            "code": "OK",
            "result": {
                "found": True,
                "record": _record(kwargs["arguments"]["intent_id"], "UNKNOWN"),
            },
        },
    )
    command = build_submit_command(_binding(), _payload("risk-intent-u"), scope=_SCOPE)
    typed = verify_command_reply(_handle(service, command_arguments(command))["result"], command)
    assert typed.outcome == "unknown"

    def conflict(**_kwargs):
        raise RiskOrderJournalError("INTENT_CONFLICT")

    monkeypatch.setattr(service, "_submit", conflict)
    changed = build_submit_command(
        _binding(), _payload("risk-intent-u", quantity="0.2"), scope=_SCOPE
    )
    reply = _handle(service, command_arguments(changed))
    assert (reply["ok"], reply["code"]) == (False, "INTENT_CONFLICT")
    assert "command_result" not in reply["result"]


def test_risk_typed_bindings_must_cover_the_targets(risk_service):
    service, target, contract, _metadata = risk_service
    with pytest.raises(RiskOrderEntryError, match="COMMAND_BINDINGS_TARGET_MISMATCH"):
        RiskOrderEntryService(
            service._bt_api,
            {_TEMPLATE: target},
            dict(service._admission),
            service._journal,
            service._gate,
            {_TEMPLATE: contract},
            command_bindings={},
        )


def test_risk_options_accept_parsed_command_binding_declarations():
    options = validate_service_options(
        {
            "role": "account_owner",
            "account_policy_ref": "risk-policy-v1",
            "command_bindings": [_DECLARATION],
        },
        ("service.order_entry", "service.order_query"),
    )
    assert options["command_bindings"][0]["account_key"]["account_id"] == _ACCOUNT
    with pytest.raises(RiskOrderEntryError, match="COMMAND_BINDING_FACT_MISSING"):
        bad = dict(_DECLARATION)
        bad.pop("quantity_unit")
        validate_service_options(
            {
                "role": "account_owner",
                "account_policy_ref": "risk-policy-v1",
                "command_bindings": [bad],
            },
            ("service.order_entry",),
        )
