"""Contract tests for the SDK owned CTP path-budget reservation."""

from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal

from bt_api_py._ctp_budget import evaluate_ctp_budget


def _incomplete_plan() -> dict:
    return {
        "schema_version": "ctp-execution-budget-v1",
        "source": "synthetic_test",
        "source_version": "fixture-v1",
        "money_unit": "CNY",
        "strategy_id": "budget-strategy",
        "strategy_identity_sha256": "a" * 64,
        "candidate_id": "candidate-1",
        "execution_cycle_id": "cycle-1",
        "account_fingerprint": "account-1",
        "trading_day": "20260911",
        "connection_generation": 1,
        "environment_profile": "simnow_demo",
        "scope_version": "ctp-contract-bundle-v1",
        "authorized_instruments": [
            {"exchange_id": "CZCE", "instrument_id": "SA701"},
            {"exchange_id": "CZCE", "instrument_id": "SR701"},
        ],
        "primary_instrument": {"exchange_id": "CZCE", "instrument_id": "SA701"},
        # A hand-picked final basket must not substitute for all reachable
        # prefixes, partial fills, UNKNOWN, cancellation, late fill, and
        # authorized recovery states.
        "required_state_kinds": [
            "prefix",
            "partial",
            "unknown",
            "cancel",
            "late_fill",
            "recovery",
        ],
        "reachable_states": [
            {
                "state_id": "final-only",
                "state_kind": "final",
                "complete": True,
                "costs": {
                    "future_gross_margin": "3000",
                    "seller_option_gross_margin": "2500",
                    "paid_long_premium": "800",
                    "fees_financing": "100",
                    "stress_cash_loss": "1200",
                    "unresolved_reserve": "0",
                },
            }
        ],
    }


_CONTEXT_FIELDS = (
    "account_fingerprint",
    "trading_day",
    "connection_generation",
    "environment_profile",
    "candidate_id",
    "strategy_id",
    "strategy_identity_sha256",
    "execution_cycle_id",
    "scope_version",
    "authorized_instruments",
    "primary_instrument",
)


def _path_state(
    evidence: dict,
    *,
    state_id: str,
    state_kind: str,
    total_cny: int,
    recovery_increment_cny: int | None = None,
    non_overlapping: bool | None = None,
) -> dict:
    state = {field: evidence[field] for field in _CONTEXT_FIELDS}
    state.update(
        {
            "state_id": state_id,
            "state_kind": state_kind,
            "costs": {
                "future_gross_margin": str(total_cny),
                "seller_option_gross_margin": "0",
                "paid_long_premium": "0",
                "fees_financing": "0",
                "stress_cash_loss": "0",
                "unresolved_reserve": "0",
            },
        }
    )
    if recovery_increment_cny is not None:
        state["recovery_increment_cny"] = str(recovery_increment_cny)
    if non_overlapping is not None:
        state["non_overlapping"] = non_overlapping
    return state


def _complete_plan() -> dict:
    evidence = _incomplete_plan()
    evidence["source"] = "sdk_runtime"
    evidence["required_state_kinds"] = [
        "prefix",
        "partial",
        "unknown",
        "cancel",
        "late_fill",
        "recovery",
    ]
    evidence["historical_cumulative_pnl_cny"] = ["0"]
    evidence["remaining_unabsorbed_new_obligation_cny"] = "100"
    evidence["unallocated_recovery_headroom_cny"] = "200"
    evidence["fresh_available_cny"] = "10000"
    evidence["reachable_states"] = [
        _path_state(evidence, state_id="prefix-1", state_kind="prefix", total_cny=1000),
        _path_state(evidence, state_id="partial-1", state_kind="partial", total_cny=2000),
        _path_state(evidence, state_id="unknown-1", state_kind="unknown", total_cny=3000),
        _path_state(evidence, state_id="cancel-1", state_kind="cancel", total_cny=4000),
        _path_state(evidence, state_id="late-fill-1", state_kind="late_fill", total_cny=5000),
        _path_state(
            evidence,
            state_id="recovery-1",
            state_kind="recovery",
            total_cny=600,
            recovery_increment_cny=500,
            non_overlapping=True,
        ),
    ]
    return evidence


def test_handpicked_final_basket_cannot_pass_path_budget() -> None:
    result = evaluate_ctp_budget(_incomplete_plan(), mode="ordinary")

    assert result.accepted is False
    assert result.ordinary_peak_cny == Decimal("7600")
    assert "budget_path_incomplete" in result.reasons


def test_complete_path_budget_accepts_ordinary_and_recovery_modes() -> None:
    evidence = _complete_plan()

    ordinary = evaluate_ctp_budget(evidence, mode="ordinary", now=datetime(2026, 9, 11, tzinfo=UTC))
    recovery = evaluate_ctp_budget(evidence, mode="recovery", now=datetime(2026, 9, 11, tzinfo=UTC))

    expected_state_costs = (
        ("prefix-1", Decimal("1000")),
        ("partial-1", Decimal("2000")),
        ("unknown-1", Decimal("3000")),
        ("cancel-1", Decimal("4000")),
        ("late-fill-1", Decimal("5000")),
        ("recovery-1", Decimal("600")),
    )
    for result in (ordinary, recovery):
        assert result.accepted is True
        assert result.write_eligible is True
        assert result.coverage_complete is True
        assert result.reasons == ()
        assert result.candidate_budget_cny == Decimal("10000")
        assert result.ordinary_cap_cny == Decimal("8000")
        assert result.ordinary_peak_cny == Decimal("5000")
        assert result.recovery_increment_cny == Decimal("500")
        assert result.full_state_peak_cny == Decimal("5500")
        assert result.available_required_cny == Decimal("300")
        assert result.fresh_available_cny == Decimal("10000")
        assert result.state_costs_cny == expected_state_costs


def test_state_scan_reasons_keep_first_occurrence_order() -> None:
    evidence = _complete_plan()
    mismatched_context = _path_state(
        evidence, state_id="partial-1", state_kind="partial", total_cny=2000
    )
    mismatched_context["candidate_id"] = "other-candidate"
    evidence["reachable_states"] = [
        _path_state(evidence, state_id="prefix-1", state_kind="prefix", total_cny=1000),
        _path_state(evidence, state_id="prefix-1", state_kind="partial", total_cny=1100),
        _path_state(evidence, state_id="prefix-1", state_kind="unknown", total_cny=1200),
        mismatched_context,
        _path_state(
            evidence,
            state_id="recovery-1",
            state_kind="recovery",
            total_cny=3000,
            recovery_increment_cny=500,
            non_overlapping=False,
        ),
    ]

    result = evaluate_ctp_budget(evidence)

    assert result.accepted is False
    assert result.coverage_complete is False
    assert result.reasons == (
        "budget_duplicate_state",
        "budget_state_context_mismatch",
        "budget_recovery_nonoverlap_unproven",
        "budget_path_incomplete",
    )
    assert result.state_costs_cny == (
        ("prefix-1", Decimal("1000")),
        ("partial-1", Decimal("2000")),
        ("recovery-1", Decimal("3000")),
    )


def test_late_fail_closed_reasons_keep_first_occurrence_order() -> None:
    evidence = _complete_plan()
    evidence["historical_cumulative_pnl_cny"] = ["0", None]
    evidence["fresh_available_cny"] = "50"
    evidence["expires_at"] = "2026-09-10T00:00:00Z"

    result = evaluate_ctp_budget(evidence, now=datetime(2026, 9, 11, tzinfo=UTC))

    assert result.accepted is False
    assert result.write_eligible is False
    assert result.available_required_cny == Decimal("300")
    assert result.fresh_available_cny == Decimal("50")
    assert result.reasons == (
        "budget_valuation_unknown",
        "budget_available_insufficient",
        "budget_expired",
    )
