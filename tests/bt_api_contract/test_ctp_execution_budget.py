"""Contract tests for the SDK owned CTP path-budget reservation."""

from __future__ import annotations

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


def test_handpicked_final_basket_cannot_pass_path_budget() -> None:
    result = evaluate_ctp_budget(_incomplete_plan(), mode="ordinary")

    assert result.accepted is False
    assert result.ordinary_peak_cny == Decimal("7600")
    assert "budget_path_incomplete" in result.reasons
