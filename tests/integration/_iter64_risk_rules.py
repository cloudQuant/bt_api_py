"""Test-only trusted Risk rule for the 64.09 scenario matrix (imported by the Risk host only).

Kept apart from ``_iter64_risk_fixture`` so that the Gateway and strategy processes,
which import the fixture, never import ``bt_api_risk`` (INV64-02).
"""

from __future__ import annotations

from decimal import Decimal

from bt_api_risk.pretrade.rules import BreachRequest, RiskRule, RuleDecision


class PriceTripRule(RiskRule):
    """An order at or above ``params.trip_price`` on ``params.instrument`` breaches at claim.

    The breach asks for an instrument-scope STOP_NEW (the policy's breach action) so that
    the policy's cancel rule for this rule id applies (D64-11). Test vocabulary only.
    """

    version = 1

    def evaluate(self, ctx):
        view = ctx.view
        if ctx.phase != "claim" or view is None or view.limit_price is None:
            return RuleDecision("PASS")
        instrument_id = view.instrument.instrument_id
        if instrument_id != ctx.params["instrument"]:
            return RuleDecision("PASS")
        if view.limit_price >= Decimal(ctx.params["trip_price"]):
            return RuleDecision(
                "DENY",
                "risk_price_band_exceeded",
                BreachRequest("STOP_NEW", "instrument", instrument_id),
            )
        return RuleDecision("PASS")
