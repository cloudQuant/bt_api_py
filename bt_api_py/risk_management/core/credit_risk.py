"""信用风险计算（信用评分/违约概率/违约损失/风险敞口）。"""

from __future__ import annotations

from decimal import Decimal
from typing import Any

from ..containers.risk_metrics import CreditRiskMetrics


class CreditRiskMixin:
    """信用风险计算方法（供 RiskCalculator 混入）。"""

    def _calculate_credit_risk(
        self, account_data: dict[str, Any], position_data: dict[str, Any]
    ) -> CreditRiskMetrics:
        """"""

        #  ()
        credit_score = self._calculate_credit_score(account_data)

        # 
        probability_of_default = self._calculate_probability_of_default(credit_score)

        # 
        loss_given_default = self._calculate_loss_given_default(position_data)

        # 
        exposure_at_default = self._calculate_exposure_at_default(position_data)

        # 
        credit_utilization = self._calculate_credit_utilization(account_data)

        # 
        settlement_risk = self._calculate_settlement_risk(position_data)

        return CreditRiskMetrics(
            {
                "credit_score": credit_score,
                "probability_of_default": probability_of_default,
                "loss_given_default": loss_given_default,
                "exposure_at_default": exposure_at_default,
                "credit_utilization": credit_utilization,
                "counterparty_risk": {},  # 
                "settlement_risk": settlement_risk,
                "maturity_profile": {},  # 
            }
        )

    def _calculate_credit_score(self, account_data: dict[str, Any]) -> Decimal:
        """"""
        # 
        base_score = Decimal("750")  # 
        account_age = account_data.get("account_age_days", 0)
        trading_volume = account_data.get("trading_volume", 0)

        # 
        age_adjustment = Decimal(str(min(account_age / 365 * 10, 50)))  # +50

        # 
        volume_adjustment = Decimal(str(min(trading_volume / 1000000 * 5, 25)))  # +25

        final_score = base_score + age_adjustment + volume_adjustment
        return Decimal(str(min(final_score, 850)))  # 850

    def _calculate_probability_of_default(self, credit_score: Decimal) -> Decimal:
        """"""
        # PD，
        score = float(credit_score)
        if score >= 800:
            return Decimal("0.001")  # 0.1%
        elif score >= 700:
            return Decimal("0.005")  # 0.5%
        elif score >= 600:
            return Decimal("0.02")  # 2%
        else: return Decimal("0.1")  # 10%

    def _calculate_loss_given_default(self, position_data: dict[str, Any]) -> Decimal:
        """"""
        # LGD
        collateral_ratio = position_data.get("collateral_ratio", 0.5)
        lgd = max(0.1, 1 - collateral_ratio)  # 10%，90%
        return Decimal(str(lgd))

    def _calculate_exposure_at_default(self, position_data: dict[str, Any]) -> Decimal:
        """"""
        return Decimal(str(position_data.get("total_exposure", 0)))

    def _calculate_credit_utilization(self, account_data: dict[str, Any]) -> Decimal:
        """"""
        used_credit = account_data.get("used_credit", 0)
        total_credit = account_data.get("total_credit", 1)
        utilization = used_credit / total_credit if total_credit > 0 else 0
        return Decimal(str(min(utilization, 1.0)))

    def _calculate_settlement_risk(self, position_data: dict[str, Any]) -> Decimal:
        """"""
        # 
        portfolio_value = position_data.get("portfolio_value", 0)
        settlement_cycle = position_data.get("settlement_cycle_days", 2)

        # 
        risk_factor = 0.001 * settlement_cycle  # 0.1%
        settlement_risk = portfolio_value * risk_factor

        return Decimal(str(settlement_risk))
