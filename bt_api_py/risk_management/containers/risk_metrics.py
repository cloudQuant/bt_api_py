"""风险指标数据容器

包含各种风险评估指标、市场风险指标、信用风险指标等
"""

from __future__ import annotations

import time
from dataclasses import dataclass
from decimal import Decimal
from typing import Any

from bt_api_base.containers.auto_init_mixin import AutoInitMixin


@dataclass
class RiskMetrics(AutoInitMixin):
    """风险指标容器，包含完整的风险评估数据"""

    def __init__(
        self, data: dict[str, Any] | None = None, has_been_json_encoded: bool = False
    ) -> None:
        if data is None:
            data = {}

        self.event = "RiskMetrics"
        self.timestamp = int(time.time())
        self.exchange_name = data.get("exchange_name", "")
        self.user_id = data.get("user_id", "")
        self.account_id = data.get("account_id", "")

        # 市场风险指标
        self.market_risk = MarketRiskMetrics(data.get("market_risk", {}))

        # 信用风险指标
        self.credit_risk = CreditRiskMetrics(data.get("credit_risk", {}))

        # 操作风险指标
        self.operational_risk = OperationalRiskMetrics(data.get("operational_risk", {}))

        # 流动性风险指标
        self.liquidity_risk = LiquidityRiskMetrics(data.get("liquidity_risk", {}))

        # 合规风险指标
        self.compliance_risk = ComplianceRiskMetrics(data.get("compliance_risk", {}))

        # 综合风险评分
        self.overall_risk_score = Decimal(str(data.get("overall_risk_score", 0)))
        self.risk_level = data.get("risk_level", "LOW")
        self.risk_trend = data.get("risk_trend", "STABLE")

        # 风险限制检查
        self.risk_limits = RiskLimitsCheck(data.get("risk_limits", {}))

        # 历史对比
        self.historical_comparison = HistoricalComparison(data.get("historical_comparison", {}))

        # 预测性指标
        self.predictive_indicators = PredictiveIndicators(data.get("predictive_indicators", {}))

        # 建议行动
        self.recommended_actions = data.get("recommended_actions", [])

        self.has_been_json_encoded = has_been_json_encoded


@dataclass
class MarketRiskMetrics:
    """市场风险指标"""

    def __init__(self, data: dict[str, Any]) -> None:
        self.value_at_risk_1d = Decimal(str(data.get("value_at_risk_1d", 0)))  # 1日VaR
        self.value_at_risk_10d = Decimal(str(data.get("value_at_risk_10d", 0)))  # 10日VaR
        self.expected_shortfall = Decimal(str(data.get("expected_shortfall", 0)))  # ES
        self.volatility = Decimal(str(data.get("volatility", 0)))  # 波动率
        self.beta = Decimal(str(data.get("beta", 0)))  # Beta系数
        self.correlation_matrix = data.get("correlation_matrix", {})  # 相关性矩阵
        self.greeks = data.get("greeks", {})  # 期权希腊字母
        self.stress_test_results = data.get("stress_test_results", {})  # 压力测试结果
        self.scenario_analysis = data.get("scenario_analysis", {})  # 情景分析

        # 仓位集中度
        self.position_concentration = PositionConcentration(data.get("position_concentration", {}))

        # 行业暴露
        self.sector_exposure = SectorExposure(data.get("sector_exposure", {}))


@dataclass
class CreditRiskMetrics:
    """信用风险指标"""

    def __init__(self, data: dict[str, Any]) -> None:
        self.credit_score = Decimal(str(data.get("credit_score", 0)))  # 信用评分
        self.probability_of_default = Decimal(
            str(data.get("probability_of_default", 0))
        )  # 违约概率
        self.loss_given_default = Decimal(str(data.get("loss_given_default", 0)))  # 违约损失率
        self.exposure_at_default = Decimal(str(data.get("exposure_at_default", 0)))  # 违约暴露
        self.credit_utilization = Decimal(str(data.get("credit_utilization", 0)))  # 信用使用率
        self.counterparty_risk = data.get("counterparty_risk", {})  # 交易对手风险
        self.settlement_risk = Decimal(str(data.get("settlement_risk", 0)))  # 结算风险
        self.maturity_profile = data.get("maturity_profile", {})  # 期限结构


@dataclass
class OperationalRiskMetrics:
    """操作风险指标"""

    def __init__(self, data: dict[str, Any]) -> None:
        self.system_health_score = Decimal(str(data.get("system_health_score", 0)))  # 系统健康度
        self.latency_metrics = LatencyMetrics(data.get("latency_metrics", {}))  # 延迟指标
        self.error_rate = Decimal(str(data.get("error_rate", 0)))  # 错误率
        self.system_availability = Decimal(str(data.get("system_availability", 0)))  # 系统可用性
        self.data_quality_score = Decimal(str(data.get("data_quality_score", 0)))  # 数据质量
        self.processing_capacity = Decimal(str(data.get("processing_capacity", 0)))  # 处理能力
        self.vulnerability_score = Decimal(str(data.get("vulnerability_score", 0)))  # 漏洞评分
        self.incident_history = data.get("incident_history", [])  # 事件历史


@dataclass
class LiquidityRiskMetrics:
    """流动性风险指标"""

    def __init__(self, data: dict[str, Any]) -> None:
        self.liquidity_score = Decimal(str(data.get("liquidity_score", 0)))  # 流动性评分
        self.bid_ask_spread = Decimal(str(data.get("bid_ask_spread", 0)))  # 买卖价差
        self.market_depth = Decimal(str(data.get("market_depth", 0)))  # 市场深度
        self.impact_cost = Decimal(str(data.get("impact_cost", 0)))  # 冲击成本
        self.volume_profile = data.get("volume_profile", {})  # 成交量分布
        self.liquidation_value = Decimal(str(data.get("liquidation_value", 0)))  # 清算价值
        self.funding_constraints = data.get("funding_constraints", {})  # 资金约束


@dataclass
class ComplianceRiskMetrics:
    """合规风险指标"""

    def __init__(self, data: dict[str, Any]) -> None:
        self.compliance_score = Decimal(str(data.get("compliance_score", 0)))  # 合规评分
        self.regulatory_violations = data.get("regulatory_violations", [])  # 监管违规
        self.reporting_compliance = Decimal(str(data.get("reporting_compliance", 0)))  # 报告合规度
        self.audit_findings = data.get("audit_findings", [])  # 审计发现
        self.policy_adherence = Decimal(str(data.get("policy_adherence", 0)))  # 政策遵循度
        self.kyc_status = data.get("kyc_status", "UNKNOWN")  # KYC状态
        self.aml_flags = data.get("aml_flags", [])  # AML标志


@dataclass
class RiskLimitsCheck:
    """风险限制检查结果"""

    def __init__(self, data: dict[str, Any]) -> None:
        self.position_limits = LimitsCheckResult(data.get("position_limits", {}))
        self.concentration_limits = LimitsCheckResult(data.get("concentration_limits", {}))
        self.leverage_limits = LimitsCheckResult(data.get("leverage_limits", {}))
        self.notional_limits = LimitsCheckResult(data.get("notional_limits", {}))
        self.var_limits = LimitsCheckResult(data.get("var_limits", {}))
        self.custom_limits = [LimitsCheckResult(limit) for limit in data.get("custom_limits", [])]


@dataclass
class LimitsCheckResult:
    """限制检查结果"""

    def __init__(self, data: dict[str, Any]) -> None:
        self.limit_name = data.get("limit_name", "")
        self.current_value = Decimal(str(data.get("current_value", 0)))
        self.limit_value = Decimal(str(data.get("limit_value", 0)))
        self.utilization_ratio = Decimal(str(data.get("utilization_ratio", 0)))  # 使用率
        self.status = data.get("status", "WITHIN_LIMIT")  # WITHIN_LIMIT, WARNING, BREACHED
        self.breached_amount = Decimal(str(data.get("breached_amount", 0)))
        self.time_to_breach = data.get("time_to_breach")  # 预计突破时间


@dataclass
class HistoricalComparison:
    """历史对比数据"""

    def __init__(self, data: dict[str, Any]) -> None:
        self.day_over_day_change = Decimal(str(data.get("day_over_day_change", 0)))
        self.week_over_week_change = Decimal(str(data.get("week_over_week_change", 0)))
        self.month_over_month_change = Decimal(str(data.get("month_over_month_change", 0)))
        self.year_over_year_change = Decimal(str(data.get("year_over_year_change", 0)))
        self.percentile_ranking = Decimal(str(data.get("percentile_ranking", 0)))  # 历史百分位
        self.z_score = Decimal(str(data.get("z_score", 0)))  # Z分数


@dataclass
class PredictiveIndicators:
    """预测性指标"""

    def __init__(self, data: dict[str, Any]) -> None:
        self.next_period_risk = Decimal(str(data.get("next_period_risk", 0)))  # 下一期风险
        self.risk_trajectory = data.get(
            "risk_trajectory", "STABLE"
        )  # INCREASING, DECREASING, STABLE
        self.early_warning_signals = data.get("early_warning_signals", [])  # 早期预警信号
        self.model_confidence = Decimal(str(data.get("model_confidence", 0)))  # 模型置信度
        self.stress_test_prediction = data.get("stress_test_prediction", {})  # 压力测试预测


@dataclass
class PositionConcentration:
    """仓位集中度"""

    def __init__(self, data: dict[str, Any]) -> None:
        self.herfindahl_index = Decimal(str(data.get("herfindahl_index", 0)))  # 赫芬达尔指数
        self.top_10_holdings_ratio = Decimal(
            str(data.get("top_10_holdings_ratio", 0))
        )  # 前10大持仓占比
        self.single_position_max = Decimal(str(data.get("single_position_max", 0)))  # 单一最大持仓
        self.sector_concentration = data.get("sector_concentration", {})  # 行业集中度
        self.geographic_concentration = data.get("geographic_concentration", {})  # 地域集中度


@dataclass
class SectorExposure:
    """行业暴露"""

    def __init__(self, data: dict[str, Any]) -> None:
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
    """延迟指标"""

    def __init__(self, data: dict[str, Any]) -> None:
        self.average_latency_ms = Decimal(str(data.get("average_latency_ms", 0)))
        self.p95_latency_ms = Decimal(str(data.get("p95_latency_ms", 0)))
        self.p99_latency_ms = Decimal(str(data.get("p99_latency_ms", 0)))
        self.max_latency_ms = Decimal(str(data.get("max_latency_ms", 0)))
        self.sla_compliance = Decimal(str(data.get("sla_compliance", 0)))  # SLA合规率
