"""Constructor contracts for strategy and exact account-manual Risk targets."""

from __future__ import annotations

from dataclasses import replace
from decimal import Decimal
import os
from pathlib import Path
from types import SimpleNamespace

import pytest
import bt_api_base
import bt_api_risk
from bt_api_base.service_protocol import WireScope, WireValidationError
from bt_api_risk import (
    AccountScope,
    DurableRiskAdmissionAdapter,
    DurableRiskGate,
    InstrumentRiskAdmissionMapper,
    InstrumentRiskMetadata,
    InstrumentRiskRegistry,
    RiskPolicy,
)

from bt_api_py.bt_api import BtApi
from bt_api_py.service_risk_order_entry import (
    RiskBtApiProviderContract,
    RiskOrderEntryError,
    RiskOrderEntryService,
    RiskOrderTarget,
)
from bt_api_py.service_risk_order_journal import RiskOrderJournal
from bt_api_py import service_risk_order_entry as risk_order_entry

_DOMAIN = "paper:manual-target-constructor"
_ACCOUNT_REF = "account-a-v1"
_MANUAL_BOOK = "manual-book-a-v1"
_STRATEGY = "strategy-internal-v1"
_EXCHANGE = "PAPER___SPOT"
_PROVIDER_ACCOUNT = "provider-account-a"
_INSTRUMENT = "BTC-USD"


@pytest.fixture
def owner_inputs(tmp_path):
    account_scope = AccountScope("PAPER", _PROVIDER_ACCOUNT, "simulation")
    registry = InstrumentRiskRegistry(
        (
            InstrumentRiskMetadata(
                instrument=_INSTRUMENT,
                metadata_version="manual-target-metadata-v1",
                as_of_ns=1_700_000_000_000_000_000,
                expires_at_ns=4_000_000_000_000_000_000,
                tick_size=Decimal("0.01"),
                quantity_step=Decimal("0.001"),
                contract_multiplier=Decimal("1"),
                max_gross_notional=Decimal("100000"),
                quantity_unit="BTC",
                valuation_unit="USD",
            ),
        )
    )
    target = RiskOrderTarget(
        account_scope=account_scope,
        strategy_id=_STRATEGY,
        exchange_name=_EXCHANGE,
        provider_account_id=_PROVIDER_ACCOUNT,
        registry=registry,
    )
    journal = RiskOrderJournal(
        str(tmp_path / "manual-target-journal.sqlite3"),
        writer_id="manual-target-constructor",
    )
    gate = DurableRiskGate(
        str(tmp_path / "manual-target-risk.sqlite3"),
        RiskPolicy(
            "manual-target-policy-v1",
            Decimal("100000"),
            max_increase_count=5,
            notional_unit="USD",
        ),
        execution_journal_authority=journal,
    )
    admission = DurableRiskAdmissionAdapter(
        gate,
        InstrumentRiskAdmissionMapper(
            account_scope,
            registry,
            wire_account_ref=_ACCOUNT_REF,
        ),
    )
    contract = RiskBtApiProviderContract(
        exchange_name=_EXCHANGE,
        profile_id="manual-target-provider-v1",
        order_schema="manual-target-order-v1",
        fill_schema="manual-target-fill-v1",
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
            ("partial", "partial"),
            ("rejected", "rejected"),
        ),
        supported_order_types=("LIMIT",),
        supported_time_in_force=("GTC",),
        supported_position_effects=("OPEN",),
    )
    try:
        yield target, admission, journal, gate, contract
    finally:
        gate.close()


def _service(owner_inputs, targets: dict[WireScope, RiskOrderTarget]):
    target, admission, journal, gate, contract = owner_inputs
    scopes = tuple(targets)
    return RiskOrderEntryService(
        object.__new__(BtApi),
        targets,
        {scope: admission for scope in scopes},
        journal,
        gate,
        {scope: contract for scope in scopes},
    )


def _command_for_scope(target: RiskOrderTarget, scope: WireScope) -> SimpleNamespace:
    return SimpleNamespace(
        wire_scope=risk_order_entry._wire_scope_payload(scope),
        scope=target.account_scope,
        exchange_name=target.exchange_name,
        provider_account_id=target.provider_account_id,
    )


def _assert_import_origin(module, expected_origin_env: str) -> None:
    origin = Path(module.__file__).resolve()
    assert origin.is_file()
    expected_origin = os.environ.get(expected_origin_env)
    if expected_origin is not None:
        assert origin == Path(expected_origin).resolve()


def test_exact_manual_book_is_a_single_target_and_other_books_do_not_resolve(owner_inputs):
    _assert_import_origin(risk_order_entry, "MANUAL_BOOK_EXPECTED_SDK_ORIGIN")
    _assert_import_origin(bt_api_base, "MANUAL_BOOK_EXPECTED_BASE_ORIGIN")
    _assert_import_origin(bt_api_risk, "MANUAL_BOOK_EXPECTED_RISK_ORIGIN")
    assert RiskOrderTarget.__module__ == "bt_api_py.service_risk_order_entry"
    assert WireScope.__module__ == "bt_api_base.service_protocol"
    assert DurableRiskGate.__module__ == "bt_api_risk.core.admission"
    target, admission, _journal, _gate, contract = owner_inputs
    manual_scope = WireScope(
        _DOMAIN,
        account_ref=_ACCOUNT_REF,
        manual_book_ref=_MANUAL_BOOK,
    )

    service = _service(owner_inputs, {manual_scope: target})

    binding = service._resolve_scope_binding(manual_scope)
    assert binding == (manual_scope, target, admission, contract)
    assert service._binding_for_command(_command_for_scope(target, manual_scope)) == binding

    wrong_book = replace(manual_scope, manual_book_ref="manual-book-b-v1")
    assert service._resolve_scope_binding(wrong_book) is None
    assert service._binding_for_command(_command_for_scope(target, wrong_book)) is None

    second_book = replace(manual_scope, manual_book_ref="manual-book-b-v1")
    with pytest.raises(RiskOrderEntryError) as error:
        _service(owner_inputs, {manual_scope: target, second_book: target})
    assert error.value.code == "MANUAL_BOOK_TARGET_MUST_BE_SINGLETON"


def test_strategy_target_still_requires_its_exact_strategy_scope(owner_inputs):
    target, _admission, _journal, _gate, _contract = owner_inputs
    strategy_scope = WireScope(
        _DOMAIN,
        account_ref=_ACCOUNT_REF,
        strategy_ref=_STRATEGY,
    )
    service = _service(owner_inputs, {strategy_scope: target})
    assert service._resolve_scope_binding(strategy_scope) is not None
    assert service._binding_for_command(_command_for_scope(target, strategy_scope)) is not None

    wrong_strategy = replace(strategy_scope, strategy_ref="strategy-forged-v1")
    assert service._binding_for_command(_command_for_scope(target, wrong_strategy)) is None
    with pytest.raises(RiskOrderEntryError) as error:
        _service(owner_inputs, {wrong_strategy: target})
    assert error.value.code == "TARGET_STRATEGY_SCOPE_MISMATCH"


def test_manual_target_rejects_portfolio_group_leg_and_multi_account_scopes(owner_inputs):
    target, _admission, _journal, _gate, _contract = owner_inputs
    unauthorized_scopes = (
        WireScope(
            _DOMAIN,
            account_ref=_ACCOUNT_REF,
            manual_book_ref=_MANUAL_BOOK,
            portfolio_ref="portfolio-a-v1",
        ),
        WireScope(
            _DOMAIN,
            account_refs=(_ACCOUNT_REF, "account-b-v1"),
            manual_book_ref=_MANUAL_BOOK,
            portfolio_ref="portfolio-a-v1",
            group_id="group-a-v1",
        ),
        WireScope(
            _DOMAIN,
            account_ref=_ACCOUNT_REF,
            manual_book_ref=_MANUAL_BOOK,
            portfolio_ref="portfolio-a-v1",
            group_id="group-a-v1",
            leg_id="leg-a-v1",
        ),
        WireScope(
            _DOMAIN,
            account_refs=(_ACCOUNT_REF, "account-b-v1"),
            manual_book_ref=_MANUAL_BOOK,
        ),
    )

    with pytest.raises(WireValidationError):
        WireScope(
            _DOMAIN,
            account_ref=_ACCOUNT_REF,
            strategy_ref=_STRATEGY,
            manual_book_ref=_MANUAL_BOOK,
        )

    for scope in unauthorized_scopes:
        with pytest.raises(RiskOrderEntryError) as error:
            _service(owner_inputs, {scope: target})
        assert error.value.code == "TARGET_STRATEGY_SCOPE_MISMATCH"
