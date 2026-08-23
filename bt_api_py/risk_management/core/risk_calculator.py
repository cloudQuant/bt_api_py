"""风险计算门面 -

VaR、CVaR、、、
按风险类别拆分为子模块（market_risk/position_risk/credit_risk/operational_risk/
liquidity_risk/compliance_risk），本模块保留编排逻辑并通过 mixin 继承。
"""

from __future__ import annotations

from decimal import Decimal
from typing import Any

from bt_api_base.logging_factory import get_logger

from ..containers.risk_metrics import (
    ComplianceRiskMetrics,
    CreditRiskMetrics,
    HistoricalComparison,
    LimitsCheckResult,
    LiquidityRiskMetrics,
    MarketRiskMetrics,
    OperationalRiskMetrics,
    PredictiveIndicators,
    RiskMetrics,
)
from .compliance_risk import ComplianceRiskMixin
from .credit_risk import CreditRiskMixin
from .liquidity_risk import LiquidityRiskMixin
from .market_risk import MarketRiskMixin
from .operational_risk import OperationalRiskMixin
from .position_risk import PositionRiskMixin

__all__ = ["RiskCalculator"]


class RiskCalculator(
    MarketRiskMixin,
    PositionRiskMixin,
    CreditRiskMixin,
    OperationalRiskMixin,
    LiquidityRiskMixin,
    ComplianceRiskMixin,
):
    """
    风险计算门面，聚合各风险类别的计算方法。

    类别:
    1. 市场风险 (VaR, CVaR, 波动率, Beta) -> MarketRiskMixin
    2. 持仓风险 (集中度, 行业敞口) -> PositionRiskMixin
    3. 信用风险 (信用评分, 违约概率) -> CreditRiskMixin
    4. 操作风险 (系统健康, 延迟) -> OperationalRiskMixin
    5. 流动性风险 (价差, 深度) -> LiquidityRiskMixin
    6. 合规风险 (合规评分, 监管违规) -> ComplianceRiskMixin
    """

    def __init__(self, config: dict[str, Any] | None = None) -> None:
        """
        初始化。

        Args: config: 配置字典
        """
        self.logger = get_logger("risk_calculator")
        self.config = config or {}

        #
        self.var_confidence_levels = self.config.get("var_confidence_levels", [0.95, 0.99])
        self.var_time_horizons = self.config.get("var_time_horizons", [1, 10])  #
        self.min_data_points = self.config.get("min_data_points", 100)
        self.default_volatility_window = self.config.get("default_volatility_window", 30)

        #
        self.stress_scenarios = self.config.get(
            "stress_scenarios",
            {
                "market_crash": {"price_change": -0.3, "volatility_spike": 2.0},
                "liquidity_crisis": {"spread_increase": 3.0, "volume_decrease": 0.5},
                "credit_event": {"default_rate": 0.05, "spread_widening": 2.0},
            },
        )

        self.logger.info("RiskCalculator initialized")

    def _calculate_market_risk(
        self, position_data: dict[str, Any], market_data: dict[str, Any]
    ) -> MarketRiskMetrics:
        """聚合市场风险指标（编排 Market/Position 两个 mixin 的计算）。"""
        price_history = market_data.get("price_history", [])
        returns = self._calculate_returns(price_history)

        var_1d = self._calculate_var(returns, confidence=0.95, time_horizon=1)
        var_10d = self._calculate_var(returns, confidence=0.95, time_horizon=10)
        expected_shortfall = self._calculate_cvar(returns, confidence=0.95)
        volatility = self._calculate_volatility(returns)
        beta = self._calculate_beta(returns, market_data.get("market_returns", []))
        correlation_matrix = self._calculate_correlation_matrix(
            market_data.get("asset_returns", {})
        )
        stress_test_results = self._run_stress_tests(position_data, market_data)
        scenario_analysis = self._run_scenario_analysis(position_data, market_data)
        position_concentration = self._calculate_position_concentration(position_data)
        sector_exposure = self._calculate_sector_exposure(position_data)

        return MarketRiskMetrics(
            {
                "value_at_risk_1d": var_1d,
                "value_at_risk_10d": var_10d,
                "expected_shortfall": expected_shortfall,
                "volatility": volatility,
                "beta": beta,
                "correlation_matrix": correlation_matrix,
                "stress_test_results": stress_test_results,
                "scenario_analysis": scenario_analysis,
                "position_concentration": self._serialize_metrics(position_concentration),
                "sector_exposure": self._serialize_metrics(sector_exposure),
            }
        )

    def calculate_risk_metrics(
        self,
        exchange_name: str,
        account_id: str,
        account_data: dict[str, Any],
        position_data: dict[str, Any],
        market_data: dict[str, Any],
    ) -> RiskMetrics:
        """
        计算综合风险指标。

        Args: exchange_name: 交易所标识
            account_id: 账户 ID
            account_data: 账户数据
            position_data: 持仓数据
            market_data: 行情数据

        Returns: RiskMetrics: 综合风险指标
        """
        try:
            self.logger.debug(f"Calculating risk metrics for {exchange_name}:{account_id}")

            #
            market_risk = self._calculate_market_risk(position_data, market_data)
            credit_risk = self._calculate_credit_risk(account_data, position_data)
            operational_risk = self._calculate_operational_risk(account_data)
            liquidity_risk = self._calculate_liquidity_risk(position_data, market_data)
            compliance_risk = self._calculate_compliance_risk(account_data)

            #
            risk_limits = self._check_all_risk_limits(
                market_risk, credit_risk, operational_risk, liquidity_risk
            )

            #
            historical_comparison = self._calculate_historical_comparison(exchange_name, account_id)

            #
            predictive_indicators = self._calculate_predictive_indicators(
                market_risk, credit_risk, operational_risk, liquidity_risk
            )

            #
            risk_metrics = RiskMetrics(
                {
                    "exchange_name": exchange_name,
                    "account_id": account_id,
                    "market_risk": self._serialize_metrics(market_risk),
                    "credit_risk": self._serialize_metrics(credit_risk),
                    "operational_risk": self._serialize_metrics(operational_risk),
                    "liquidity_risk": self._serialize_metrics(liquidity_risk),
                    "compliance_risk": self._serialize_metrics(compliance_risk),
                    "risk_limits": self._serialize_metrics(risk_limits),
                    "historical_comparison": self._serialize_metrics(historical_comparison),
                    "predictive_indicators": self._serialize_metrics(predictive_indicators),
                    "recommended_actions": self._generate_risk_actions(
                        market_risk, credit_risk, operational_risk, liquidity_risk, compliance_risk
                    ),
                }
            )

            return risk_metrics

        except Exception as e:
            self.logger.error(f"Error calculating risk metrics: {e}")
            raise

    def _check_all_risk_limits(
        self,
        market_risk: MarketRiskMetrics,
        credit_risk: CreditRiskMetrics,
        operational_risk: OperationalRiskMetrics,
        liquidity_risk: LiquidityRiskMetrics,
    ) -> LimitsCheckResult:
        """"""
        #
        return LimitsCheckResult(
            {
                "limit_name": "comprehensive_check",
                "current_value": 0.7,  #
                "limit_value": 0.8,
                "utilization_ratio": 0.875,
                "status": "WITHIN_LIMIT",
                "breached_amount": 0,
                "time_to_breach": None,
            }
        )

    def _calculate_historical_comparison(
        self, exchange_name: str, account_id: str
    ) -> HistoricalComparison:
        """"""
        #
        return HistoricalComparison(
            {
                "day_over_day_change": 0.05,
                "week_over_week_change": 0.12,
                "month_over_month_change": 0.25,
                "year_over_year_change": 0.18,
                "percentile_ranking": 0.65,
                "z_score": 0.8,
            }
        )

    def _calculate_predictive_indicators(
        self,
        market_risk: MarketRiskMetrics,
        credit_risk: CreditRiskMetrics,
        operational_risk: OperationalRiskMetrics,
        liquidity_risk: LiquidityRiskMetrics,
    ) -> PredictiveIndicators:
        """"""
        #
        current_risk = float(market_risk.volatility)
        next_period_risk = current_risk * 1.05  #

        return PredictiveIndicators(
            {
                "next_period_risk": next_period_risk,
                "risk_trajectory": "INCREASING",
                "early_warning_signals": ["volatility_rising", "liquidity_decreasing"],
                "model_confidence": 0.75,
                "stress_test_prediction": {"scenario": "moderate_stress", "probability": 0.15},
            }
        )

    def _generate_risk_actions(
        self,
        market_risk: MarketRiskMetrics,
        credit_risk: CreditRiskMetrics,
        operational_risk: OperationalRiskMetrics,
        liquidity_risk: LiquidityRiskMetrics,
        compliance_risk: ComplianceRiskMetrics,
    ) -> list[str]:
        """"""
        actions = []

        #
        if float(market_risk.volatility) > 0.3:
            actions.append("")

        if float(credit_risk.probability_of_default) > 0.02:
            actions.append("，")

        if float(operational_risk.system_health_score) < 0.8:
            actions.append("，")

        if float(liquidity_risk.liquidity_score) < 0.6:
            actions.append("，")

        if float(compliance_risk.compliance_score) < 0.8:
            actions.append("，")

        if not actions:
            actions.append("，")

        return actions

    def _serialize_metrics(self, obj: Any) -> Any:
        """"""
        if isinstance(obj, Decimal):
            return str(obj)
        if isinstance(obj, dict):
            return {k: self._serialize_metrics(v) for k, v in obj.items()}
        if isinstance(obj, list):
            return [self._serialize_metrics(item) for item in obj]
        if hasattr(obj, "__dict__"):
            return {k: self._serialize_metrics(v) for k, v in obj.__dict__.items()}
        return obj
