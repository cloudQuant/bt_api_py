"""

、、
"""

from __future__ import annotations

import time
from dataclasses import dataclass
from decimal import Decimal
from typing import Any

from bt_api_base.containers.auto_init_mixin import AutoInitMixin


@dataclass
class RiskMetrics(AutoInitMixin):
    """，"""

    def __init__(
        self, data: dict[str, Any] | None = None, has_been_json_encoded: bool = False
    ) -> None:
        """__init__ method"""
        if data is None:
            data = {}

        self.event = "RiskMetrics"
        self.timestamp = int(time.time())
        self.exchange_name = data.get("exchange_name", "")
        self.user_id = data.get("user_id", "")
        self.account_id = data.get("account_id", "")

        #
        self.market_risk = MarketRiskMetrics(data.get("market_risk", {}))

        #
        self.credit_risk = CreditRiskMetrics(data.get("credit_risk", {}))

        #
        self.operational_risk = OperationalRiskMetrics(data.get("operational_risk", {}))

        #
        self.liquidity_risk = LiquidityRiskMetrics(data.get("liquidity_risk", {}))

        #
        self.compliance_risk = ComplianceRiskMetrics(data.get("compliance_risk", {}))

        #
        self.overall_risk_score = Decimal(str(data.get("overall_risk_score", 0)))
        self.risk_level = data.get("risk_level", "LOW")
        self.risk_trend = data.get("risk_trend", "STABLE")

        #
        self.risk_limits = RiskLimitsCheck(data.get("risk_limits", {}))

        #
        self.historical_comparison = HistoricalComparison(data.get("historical_comparison", {}))

        #
        self.predictive_indicators = PredictiveIndicators(data.get("predictive_indicators", {}))

        #
        self.recommended_actions = data.get("recommended_actions", [])

        self.has_been_json_encoded = has_been_json_encoded


@dataclass
class MarketRiskMetrics:
    """"""

    def __init__(self, data: dict[str, Any]) -> None:
        """__init__ method"""
        self.value_at_risk_1d = Decimal(str(data.get("value_at_risk_1d", 0)))  # 1VaR
        self.value_at_risk_10d = Decimal(str(data.get("value_at_risk_10d", 0)))  # 10VaR
        self.expected_shortfall = Decimal(str(data.get("expected_shortfall", 0)))  # ES
        self.volatility = Decimal(str(data.get("volatility", 0)))  #
        self.beta = Decimal(str(data.get("beta", 0)))  # Beta
        self.correlation_matrix = data.get("correlation_matrix", {})  #
        self.greeks = data.get("greeks", {})  #
        self.stress_test_results = data.get("stress_test_results", {})  #
        self.scenario_analysis = data.get("scenario_analysis", {})  #

        #
        self.position_concentration = PositionConcentration(data.get("position_concentration", {}))

        #
        self.sector_exposure = SectorExposure(data.get("sector_exposure", {}))


@dataclass
class CreditRiskMetrics:
    """"""

    def __init__(self, data: dict[str, Any]) -> None:
        """__init__ method"""
        self.credit_score = Decimal(str(data.get("credit_score", 0)))  #
        self.probability_of_default = Decimal(str(data.get("probability_of_default", 0)))  #
        self.loss_given_default = Decimal(str(data.get("loss_given_default", 0)))  #
        self.exposure_at_default = Decimal(str(data.get("exposure_at_default", 0)))  #
        self.credit_utilization = Decimal(str(data.get("credit_utilization", 0)))  #
        self.counterparty_risk = data.get("counterparty_risk", {})  #
        self.settlement_risk = Decimal(str(data.get("settlement_risk", 0)))  #
        self.maturity_profile = data.get("maturity_profile", {})  #


@dataclass
class OperationalRiskMetrics:
    """"""

    def __init__(self, data: dict[str, Any]) -> None:
        """__init__ method"""
        self.system_health_score = Decimal(str(data.get("system_health_score", 0)))  #
        self.latency_metrics = LatencyMetrics(data.get("latency_metrics", {}))  #
        self.error_rate = Decimal(str(data.get("error_rate", 0)))  #
        self.system_availability = Decimal(str(data.get("system_availability", 0)))  #
        self.data_quality_score = Decimal(str(data.get("data_quality_score", 0)))  #
        self.processing_capacity = Decimal(str(data.get("processing_capacity", 0)))  #
        self.vulnerability_score = Decimal(str(data.get("vulnerability_score", 0)))  #
        self.incident_history = data.get("incident_history", [])  #


@dataclass
class LiquidityRiskMetrics:
    """"""

    def __init__(self, data: dict[str, Any]) -> None:
        """__init__ method"""
        self.liquidity_score = Decimal(str(data.get("liquidity_score", 0)))  #
        self.bid_ask_spread = Decimal(str(data.get("bid_ask_spread", 0)))  #
        self.market_depth = Decimal(str(data.get("market_depth", 0)))  #
        self.impact_cost = Decimal(str(data.get("impact_cost", 0)))  #
        self.volume_profile = data.get("volume_profile", {})  #
        self.liquidation_value = Decimal(str(data.get("liquidation_value", 0)))  #
        self.funding_constraints = data.get("funding_constraints", {})  #


@dataclass
class ComplianceRiskMetrics:
    """"""

    def __init__(self, data: dict[str, Any]) -> None:
        """__init__ method"""
        self.compliance_score = Decimal(str(data.get("compliance_score", 0)))  #
        self.regulatory_violations = data.get("regulatory_violations", [])  #
        self.reporting_compliance = Decimal(str(data.get("reporting_compliance", 0)))  #
        self.audit_findings = data.get("audit_findings", [])  #
        self.policy_adherence = Decimal(str(data.get("policy_adherence", 0)))  #
        self.kyc_status = data.get("kyc_status", "UNKNOWN")  # KYC
        self.aml_flags = data.get("aml_flags", [])  # AML


@dataclass
class RiskLimitsCheck:
    """"""

    def __init__(self, data: dict[str, Any]) -> None:
        """__init__ method"""
        self.position_limits = LimitsCheckResult(data.get("position_limits", {}))
        self.concentration_limits = LimitsCheckResult(data.get("concentration_limits", {}))
        self.leverage_limits = LimitsCheckResult(data.get("leverage_limits", {}))
        self.notional_limits = LimitsCheckResult(data.get("notional_limits", {}))
        self.var_limits = LimitsCheckResult(data.get("var_limits", {}))
        self.custom_limits = [LimitsCheckResult(limit) for limit in data.get("custom_limits", [])]


@dataclass
class LimitsCheckResult:
    """"""

    def __init__(self, data: dict[str, Any]) -> None:
        """__init__ method"""
        self.limit_name = data.get("limit_name", "")
        self.current_value = Decimal(str(data.get("current_value", 0)))
        self.limit_value = Decimal(str(data.get("limit_value", 0)))
        self.utilization_ratio = Decimal(str(data.get("utilization_ratio", 0)))  #
        self.status = data.get("status", "WITHIN_LIMIT")  # WITHIN_LIMIT, WARNING, BREACHED
        self.breached_amount = Decimal(str(data.get("breached_amount", 0)))
        self.time_to_breach = data.get("time_to_breach")  #


@dataclass
class HistoricalComparison:
    """"""

    def __init__(self, data: dict[str, Any]) -> None:
        """__init__ method"""
        self.day_over_day_change = Decimal(str(data.get("day_over_day_change", 0)))
        self.week_over_week_change = Decimal(str(data.get("week_over_week_change", 0)))
        self.month_over_month_change = Decimal(str(data.get("month_over_month_change", 0)))
        self.year_over_year_change = Decimal(str(data.get("year_over_year_change", 0)))
        self.percentile_ranking = Decimal(str(data.get("percentile_ranking", 0)))  #
        self.z_score = Decimal(str(data.get("z_score", 0)))  # Z


@dataclass
class PredictiveIndicators:
    """"""

    def __init__(self, data: dict[str, Any]) -> None:
        """__init__ method"""
        self.next_period_risk = Decimal(str(data.get("next_period_risk", 0)))  #
        self.risk_trajectory = data.get(
            "risk_trajectory", "STABLE"
        )  # INCREASING, DECREASING, STABLE
        self.early_warning_signals = data.get("early_warning_signals", [])  #
        self.model_confidence = Decimal(str(data.get("model_confidence", 0)))  #
        self.stress_test_prediction = data.get("stress_test_prediction", {})  #


@dataclass
class PositionConcentration:
    """"""

    def __init__(self, data: dict[str, Any]) -> None:
        """__init__ method"""
        self.herfindahl_index = Decimal(str(data.get("herfindahl_index", 0)))  #
        self.top_10_holdings_ratio = Decimal(str(data.get("top_10_holdings_ratio", 0)))  # 10
        self.single_position_max = Decimal(str(data.get("single_position_max", 0)))  #
        self.sector_concentration = data.get("sector_concentration", {})  #
        self.geographic_concentration = data.get("geographic_concentration", {})  #


@dataclass
class SectorExposure:
    """"""

    def __init__(self, data: dict[str, Any]) -> None:
        """__init__ method"""
        self.technology = Decimal(str(data.get("technology", 0)))
        self.finance = Decimal(str(data.get("finance", 0)))
        self.healthcare = Decimal(str(data.get("healthcare", 0)))
        self.energy = Decimal(str(data.get("energy", 0)))
        self.materials = Decimal(str(data.get("materials", 0)))
        self.consumer_discretionary = Decimal(str(data.get("consumer_discretionary", 0)))
        self.consumer_staples = Decimal(str(data.get("consumer_staples", 0)))
        self.utilities = Decimal(str(data.get("utilities", 0)))
        self.real_estate = Decimal(str(data.get("real_estate", 0)))
        self.communication = Decimal(str(data.get("communication", 0)))
        self.industrials = Decimal(str(data.get("industrials", 0)))
        self.other = Decimal(str(data.get("other", 0)))


@dataclass
class LatencyMetrics:
    """"""

    def __init__(self, data: dict[str, Any]) -> None:
        """__init__ method"""
        self.average_latency_ms = Decimal(str(data.get("average_latency_ms", 0)))
        self.p95_latency_ms = Decimal(str(data.get("p95_latency_ms", 0)))
        self.p99_latency_ms = Decimal(str(data.get("p99_latency_ms", 0)))
        self.max_latency_ms = Decimal(str(data.get("max_latency_ms", 0)))
        self.sla_compliance = Decimal(str(data.get("sla_compliance", 0)))  # SLA
