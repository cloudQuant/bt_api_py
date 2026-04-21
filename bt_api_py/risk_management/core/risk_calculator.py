"""风险计算器 - 各种风险指标的计算引擎

提供VaR、CVaR、波动率、相关性、压力测试等风险指标计算
"""

from __future__ import annotations

import math
import statistics
from decimal import Decimal
from typing import Any, cast

import numpy as np

from bt_api_base.logging_factory import get_logger

from ..containers.risk_metrics import (
    ComplianceRiskMetrics,
    CreditRiskMetrics,
    HistoricalComparison,
    LatencyMetrics,
    LimitsCheckResult,
    LiquidityRiskMetrics,
    MarketRiskMetrics,
    OperationalRiskMetrics,
    PositionConcentration,
    PredictiveIndicators,
    RiskMetrics,
    SectorExposure,
)


class RiskCalculator:
    """风险计算器

    提供全面的风险指标计算功能:
    1. 市场风险指标 (VaR, CVaR, 波动率, 相关性, Beta等)
    2. 信用风险指标 (违约概率, 违约损失率, 信用评分等)
    3. 操作风险指标 (系统健康度, 错误率, 可用性等)
    4. 流动性风险指标 (流动性评分, 买卖价差, 市场深度等)
    5. 合规风险指标 (合规评分, 监管违规等)
    6. 压力测试和情景分析
    """

    def __init__(self, config: dict[str, Any] | None = None) -> None:
        """初始化风险计算器

        Args:
            config: 计算器配置
        """
        self.logger = get_logger("risk_calculator")
        self.config = config or {}

        # 计算参数
        self.var_confidence_levels = self.config.get("var_confidence_levels", [0.95, 0.99])
        self.var_time_horizons = self.config.get("var_time_horizons", [1, 10])  # 天数
        self.min_data_points = self.config.get("min_data_points", 100)
        self.default_volatility_window = self.config.get("default_volatility_window", 30)

        # 压力测试场景
        self.stress_scenarios = self.config.get(
            "stress_scenarios",
            {
                "market_crash": {"price_change": -0.3, "volatility_spike": 2.0},
                "liquidity_crisis": {"spread_increase": 3.0, "volume_decrease": 0.5},
                "credit_event": {"default_rate": 0.05, "spread_widening": 2.0},
            },
        )

        self.logger.info("RiskCalculator initialized")

    def calculate_risk_metrics(
        self,
        exchange_name: str,
        account_id: str,
        account_data: dict[str, Any],
        position_data: dict[str, Any],
        market_data: dict[str, Any],
    ) -> RiskMetrics:
        """计算综合风险指标

        Args:
            exchange_name: 交易所名称
            account_id: 账户ID
            account_data: 账户数据
            position_data: 仓位数据
            market_data: 市场数据

        Returns:
            RiskMetrics: 风险指标
        """
        try:
            self.logger.debug(f"Calculating risk metrics for {exchange_name}:{account_id}")

            # 计算各类风险指标
            market_risk = self._calculate_market_risk(position_data, market_data)
            credit_risk = self._calculate_credit_risk(account_data, position_data)
            operational_risk = self._calculate_operational_risk(account_data)
            liquidity_risk = self._calculate_liquidity_risk(position_data, market_data)
            compliance_risk = self._calculate_compliance_risk(account_data)

            # 风险限制检查
            risk_limits = self._check_all_risk_limits(
                market_risk, credit_risk, operational_risk, liquidity_risk
            )

            # 历史对比
            historical_comparison = self._calculate_historical_comparison(exchange_name, account_id)

            # 预测性指标
            predictive_indicators = self._calculate_predictive_indicators(
                market_risk, credit_risk, operational_risk, liquidity_risk
            )

            # 创建风险指标对象
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

    def _calculate_market_risk(
        self, position_data: dict[str, Any], market_data: dict[str, Any]
    ) -> MarketRiskMetrics:
        """计算市场风险指标"""

        # 提取价格数据
        price_history = market_data.get("price_history", [])
        returns = self._calculate_returns(price_history)

        # VaR计算
        var_1d = self._calculate_var(returns, confidence=0.95, time_horizon=1)
        var_10d = self._calculate_var(returns, confidence=0.95, time_horizon=10)

        # CVaR (Expected Shortfall)
        expected_shortfall = self._calculate_cvar(returns, confidence=0.95)

        # 波动率
        volatility = self._calculate_volatility(returns)

        # Beta系数 (假设市场基准)
        beta = self._calculate_beta(returns, market_data.get("market_returns", []))

        # 相关性矩阵
        correlation_matrix = self._calculate_correlation_matrix(
            market_data.get("asset_returns", {})
        )

        # 压力测试
        stress_test_results = self._run_stress_tests(position_data, market_data)

        # 情景分析
        scenario_analysis = self._run_scenario_analysis(position_data, market_data)

        # 仓位集中度
        position_concentration = self._calculate_position_concentration(position_data)

        # 行业暴露
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

    def _calculate_credit_risk(
        self, account_data: dict[str, Any], position_data: dict[str, Any]
    ) -> CreditRiskMetrics:
        """计算信用风险指标"""

        # 信用评分 (简化版本)
        credit_score = self._calculate_credit_score(account_data)

        # 违约概率
        probability_of_default = self._calculate_probability_of_default(credit_score)

        # 违约损失率
        loss_given_default = self._calculate_loss_given_default(position_data)

        # 违约暴露
        exposure_at_default = self._calculate_exposure_at_default(position_data)

        # 信用使用率
        credit_utilization = self._calculate_credit_utilization(account_data)

        # 结算风险
        settlement_risk = self._calculate_settlement_risk(position_data)

        return CreditRiskMetrics(
            {
                "credit_score": credit_score,
                "probability_of_default": probability_of_default,
                "loss_given_default": loss_given_default,
                "exposure_at_default": exposure_at_default,
                "credit_utilization": credit_utilization,
                "counterparty_risk": {},  # 简化
                "settlement_risk": settlement_risk,
                "maturity_profile": {},  # 简化
            }
        )

    def _calculate_operational_risk(self, account_data: dict[str, Any]) -> OperationalRiskMetrics:
        """计算操作风险指标"""

        # 系统健康度
        system_health_score = self._calculate_system_health_score(account_data)

        # 延迟指标
        latency_metrics = self._calculate_latency_metrics(account_data)

        # 错误率
        error_rate = self._calculate_error_rate(account_data)

        # 系统可用性
        system_availability = self._calculate_system_availability(account_data)

        # 数据质量
        data_quality_score = self._calculate_data_quality_score(account_data)

        # 处理能力
        processing_capacity = self._calculate_processing_capacity(account_data)

        # 漏洞评分
        vulnerability_score = self._calculate_vulnerability_score(account_data)

        return OperationalRiskMetrics(
            {
                "system_health_score": system_health_score,
                "latency_metrics": self._serialize_metrics(latency_metrics),
                "error_rate": error_rate,
                "system_availability": system_availability,
                "data_quality_score": data_quality_score,
                "processing_capacity": processing_capacity,
                "vulnerability_score": vulnerability_score,
                "incident_history": [],  # 简化
            }
        )

    def _calculate_liquidity_risk(
        self, position_data: dict[str, Any], market_data: dict[str, Any]
    ) -> LiquidityRiskMetrics:
        """计算流动性风险指标"""

        # 流动性评分
        liquidity_score = self._calculate_liquidity_score(position_data, market_data)

        # 买卖价差
        bid_ask_spread = self._calculate_bid_ask_spread(market_data)

        # 市场深度
        market_depth = self._calculate_market_depth(market_data)

        # 冲击成本
        impact_cost = self._calculate_impact_cost(position_data, market_data)

        # 成交量分布
        volume_profile = self._calculate_volume_profile(market_data)

        # 清算价值
        liquidation_value = self._calculate_liquidation_value(position_data, market_data)

        return LiquidityRiskMetrics(
            {
                "liquidity_score": liquidity_score,
                "bid_ask_spread": bid_ask_spread,
                "market_depth": market_depth,
                "impact_cost": impact_cost,
                "volume_profile": volume_profile,
                "liquidation_value": liquidation_value,
                "funding_constraints": {},  # 简化
            }
        )

    def _calculate_compliance_risk(self, account_data: dict[str, Any]) -> ComplianceRiskMetrics:
        """计算合规风险指标"""

        # 合规评分
        compliance_score = self._calculate_compliance_score(account_data)

        # 监管违规
        regulatory_violations = self._get_regulatory_violations(account_data)

        # 报告合规度
        reporting_compliance = self._calculate_reporting_compliance(account_data)

        # 审计发现
        audit_findings = self._get_audit_findings(account_data)

        # 政策遵循度
        policy_adherence = self._calculate_policy_adherence(account_data)

        # KYC状态
        kyc_status = account_data.get("kyc_status", "UNKNOWN")

        # AML标志
        aml_flags = self._get_aml_flags(account_data)

        return ComplianceRiskMetrics(
            {
                "compliance_score": compliance_score,
                "regulatory_violations": regulatory_violations,
                "reporting_compliance": reporting_compliance,
                "audit_findings": audit_findings,
                "policy_adherence": policy_adherence,
                "kyc_status": kyc_status,
                "aml_flags": aml_flags,
            }
        )

    # 具体风险指标计算方法

    def _calculate_returns(self, price_history: list[float]) -> list[float]:
        """计算收益率序列"""
        if len(price_history) < 2:
            return []

        returns = []
        for i in range(1, len(price_history)):
            if price_history[i - 1] != 0:
                ret = (price_history[i] - price_history[i - 1]) / price_history[i - 1]
                returns.append(ret)

        return returns

    def _calculate_var(
        self, returns: list[float], confidence: float = 0.95, time_horizon: int = 1
    ) -> Decimal:
        """计算VaR (Value at Risk)"""
        if not returns or len(returns) < self.min_data_points:
            return Decimal("0")

        # 计算收益率分布的分位数
        var_percentile = (1 - confidence) * 100
        var = np.percentile(returns, var_percentile)

        # 调整时间范围
        var_time_adjusted = var * math.sqrt(time_horizon)

        return Decimal(str(abs(var_time_adjusted)))

    def _calculate_cvar(self, returns: list[float], confidence: float = 0.95) -> Decimal:
        """计算CVaR (Conditional Value at Risk) / Expected Shortfall"""
        if not returns or len(returns) < self.min_data_points:
            return Decimal("0")

        var_percentile = (1 - confidence) * 100
        var_threshold = np.percentile(returns, var_percentile)

        # 计算低于VaR阈值的收益率的平均值
        tail_losses = [r for r in returns if r <= var_threshold]
        if not tail_losses:
            return Decimal("0")

        cvar = statistics.mean(tail_losses)
        return Decimal(str(abs(cvar)))

    def _calculate_volatility(self, returns: list[float], window: int | None = None) -> Decimal:
        """计算波动率"""
        if not returns:
            return Decimal("0")

        window = window or self.default_volatility_window
        if len(returns) < 2:
            return Decimal("0")

        # 使用最近的窗口计算
        recent_returns = returns[-window:] if len(returns) > window else returns

        if len(recent_returns) < 2:
            return Decimal("0")

        volatility = statistics.stdev(recent_returns)
        return Decimal(str(volatility))

    def _calculate_beta(self, asset_returns: list[float], market_returns: list[float]) -> Decimal:
        """计算Beta系数"""
        if len(asset_returns) < 2 or len(market_returns) < 2:
            return Decimal("1.0")  # 默认值

        # 确保长度一致
        min_len = min(len(asset_returns), len(market_returns))
        asset_returns = asset_returns[-min_len:]
        market_returns = market_returns[-min_len:]

        if len(asset_returns) < 2:
            return Decimal("1.0")

        # 计算协方差和方差
        if statistics.stdev(market_returns) == 0:
            return Decimal("1.0")

        covariance = statistics.covariance(asset_returns, market_returns)
        market_variance = statistics.variance(market_returns)

        if market_variance == 0:
            return Decimal("1.0")

        beta = covariance / market_variance
        return Decimal(str(beta))

    def _calculate_correlation_matrix(
        self, asset_returns: dict[str, list[float]]
    ) -> dict[str, dict[str, float]]:
        """计算相关性矩阵"""
        correlation_matrix: dict[str, dict[str, float]] = {}
        assets = list(asset_returns.keys())

        for asset1 in assets:
            correlation_matrix[asset1] = {}
            returns1 = asset_returns[asset1]

            if len(returns1) < 2:
                correlation_matrix[asset1][asset1] = 1.0
                continue

            for asset2 in assets:
                returns2 = asset_returns[asset2]

                if len(returns2) < 2:
                    correlation_matrix[asset1][asset2] = 0.0
                    continue

                if asset1 == asset2:
                    correlation_matrix[asset1][asset2] = 1.0
                else:
                    # 确保长度一致
                    min_len = min(len(returns1), len(returns2))
                    r1 = returns1[-min_len:]
                    r2 = returns2[-min_len:]

                    if len(r1) < 2 or statistics.stdev(r1) == 0 or statistics.stdev(r2) == 0:
                        correlation_matrix[asset1][asset2] = 0.0
                    else:
                        correlation = np.corrcoef(r1, r2)[0, 1]
                        correlation_matrix[asset1][asset2] = (
                            float(correlation) if not math.isnan(correlation) else 0.0
                        )

        return correlation_matrix

    def _run_stress_tests(
        self, position_data: dict[str, Any], market_data: dict[str, Any]
    ) -> dict[str, Any]:
        """运行压力测试"""
        stress_results = {}

        for scenario_name, scenario_params in self.stress_scenarios.items():
            portfolio_value = position_data.get("portfolio_value", 0)

            if scenario_name == "market_crash":
                price_change = scenario_params.get("price_change", -0.3)
                stressed_value = portfolio_value * (1 + price_change)
                loss = portfolio_value - stressed_value

            elif scenario_name == "liquidity_crisis":
                scenario_params.get("spread_increase", 3.0)
                scenario_params.get("volume_decrease", 0.5)
                # 简化的流动性危机影响
                stressed_value = portfolio_value * (1 - 0.1)  # 假设10%损失
                loss = portfolio_value - stressed_value

            elif scenario_name == "credit_event":
                default_rate = scenario_params.get("default_rate", 0.05)
                stressed_value = portfolio_value * (1 - default_rate)
                loss = portfolio_value - stressed_value

            else:
                loss = 0

            stress_results[scenario_name] = {
                "portfolio_loss": loss,
                "loss_percentage": (loss / portfolio_value * 100) if portfolio_value > 0 else 0,
                "scenario_params": scenario_params,
            }

        return stress_results

    def _run_scenario_analysis(
        self, position_data: dict[str, Any], market_data: dict[str, Any]
    ) -> dict[str, Any]:
        """运行情景分析"""
        scenarios = {
            "best_case": {"return": 0.15, "probability": 0.1},
            "moderate_case": {"return": 0.05, "probability": 0.6},
            "worst_case": {"return": -0.20, "probability": 0.3},
        }

        portfolio_value = position_data.get("portfolio_value", 0)
        scenario_results = {}

        for scenario_name, params in scenarios.items():
            expected_return = params["return"]
            probability = params["probability"]
            expected_value = portfolio_value * (1 + expected_return)

            scenario_results[scenario_name] = {
                "expected_value": expected_value,
                "expected_return": expected_return,
                "probability": probability,
                "expected_pnl": expected_value - portfolio_value,
            }

        return scenario_results

    def _calculate_position_concentration(
        self, position_data: dict[str, Any]
    ) -> PositionConcentration:
        """计算仓位集中度"""
        positions = position_data.get("positions", [])
        total_value = sum(pos.get("value", 0) for pos in positions)

        if total_value == 0:
            return PositionConcentration({})

        # 计算赫芬达尔指数
        weights = [pos.get("value", 0) / total_value for pos in positions]
        herfindahl_index = sum(w**2 for w in weights)

        # 前10大持仓占比
        sorted_positions = sorted(positions, key=lambda x: x.get("value", 0), reverse=True)
        top_10_value = sum(pos.get("value", 0) for pos in sorted_positions[:10])
        top_10_ratio = top_10_value / total_value

        # 单一最大持仓
        single_position_max = max(weights) if weights else 0

        return PositionConcentration(
            {
                "herfindahl_index": herfindahl_index,
                "top_10_holdings_ratio": top_10_ratio,
                "single_position_max": single_position_max,
                "sector_concentration": {},  # 简化
                "geographic_concentration": {},  # 简化
            }
        )

    def _calculate_sector_exposure(self, position_data: dict[str, Any]) -> SectorExposure:
        """计算行业暴露"""
        positions = position_data.get("positions", [])
        total_value = sum(pos.get("value", 0) for pos in positions)

        if total_value == 0:
            return SectorExposure({})

        # 简化的行业分类
        sector_exposure: dict[str, float] = {}
        for pos in positions:
            sector = pos.get("sector", "other")
            value = pos.get("value", 0)
            sector_exposure[sector] = sector_exposure.get(sector, 0) + value

        # 转换为比例
        sector_percentages = {}
        for sector, value in sector_exposure.items():
            sector_percentages[sector] = value / total_value

        return SectorExposure(sector_percentages)

    def _calculate_credit_score(self, account_data: dict[str, Any]) -> Decimal:
        """计算信用评分"""
        # 简化的信用评分计算
        base_score = Decimal("750")  # 基础分
        account_age = account_data.get("account_age_days", 0)
        trading_volume = account_data.get("trading_volume", 0)

        # 根据账户年龄调整
        age_adjustment = Decimal(str(min(account_age / 365 * 10, 50)))  # 最多+50分

        # 根据交易量调整
        volume_adjustment = Decimal(str(min(trading_volume / 1000000 * 5, 25)))  # 最多+25分

        final_score = base_score + age_adjustment + volume_adjustment
        return Decimal(str(min(final_score, 850)))  # 最高850分

    def _calculate_probability_of_default(self, credit_score: Decimal) -> Decimal:
        """计算违约概率"""
        # 简化的PD计算，基于信用评分
        score = float(credit_score)
        if score >= 800:
            return Decimal("0.001")  # 0.1%
        elif score >= 700:
            return Decimal("0.005")  # 0.5%
        elif score >= 600:
            return Decimal("0.02")  # 2%
        else:
            return Decimal("0.1")  # 10%

    def _calculate_loss_given_default(self, position_data: dict[str, Any]) -> Decimal:
        """计算违约损失率"""
        # 简化的LGD计算
        collateral_ratio = position_data.get("collateral_ratio", 0.5)
        lgd = max(0.1, 1 - collateral_ratio)  # 最低10%，最高90%
        return Decimal(str(lgd))

    def _calculate_exposure_at_default(self, position_data: dict[str, Any]) -> Decimal:
        """计算违约暴露"""
        return Decimal(str(position_data.get("total_exposure", 0)))

    def _calculate_credit_utilization(self, account_data: dict[str, Any]) -> Decimal:
        """计算信用使用率"""
        used_credit = account_data.get("used_credit", 0)
        total_credit = account_data.get("total_credit", 1)
        utilization = used_credit / total_credit if total_credit > 0 else 0
        return Decimal(str(min(utilization, 1.0)))

    def _calculate_settlement_risk(self, position_data: dict[str, Any]) -> Decimal:
        """计算结算风险"""
        # 基于持仓价值和结算周期
        portfolio_value = position_data.get("portfolio_value", 0)
        settlement_cycle = position_data.get("settlement_cycle_days", 2)

        # 简化的结算风险计算
        risk_factor = 0.001 * settlement_cycle  # 每天0.1%
        settlement_risk = portfolio_value * risk_factor

        return Decimal(str(settlement_risk))

    def _calculate_system_health_score(self, account_data: dict[str, Any]) -> Decimal:
        """计算系统健康度"""
        # 基于各种系统指标
        cpu_usage = account_data.get("cpu_usage", 0.5)
        memory_usage = account_data.get("memory_usage", 0.5)
        disk_usage = account_data.get("disk_usage", 0.3)
        error_rate = account_data.get("error_rate", 0.01)

        # 计算综合健康度
        health_score = 1.0 - (
            cpu_usage * 0.3 + memory_usage * 0.3 + disk_usage * 0.2 + error_rate * 0.2
        )
        return Decimal(str(max(0, min(health_score, 1.0))))

    def _calculate_latency_metrics(self, account_data: dict[str, Any]) -> LatencyMetrics:
        """计算延迟指标"""
        latency_data = account_data.get("latency_history", [100, 150, 120, 80, 200])

        if not latency_data:
            return LatencyMetrics({})

        avg_latency = statistics.mean(latency_data)
        p95_latency = np.percentile(latency_data, 95)
        p99_latency = np.percentile(latency_data, 99)
        max_latency = max(latency_data)

        # SLA合规率 (假设SLA为200ms)
        sla_compliance = sum(1 for lat in latency_data if lat <= 200) / len(latency_data)

        return LatencyMetrics(
            {
                "average_latency_ms": avg_latency,
                "p95_latency_ms": p95_latency,
                "p99_latency_ms": p99_latency,
                "max_latency_ms": max_latency,
                "sla_compliance": sla_compliance,
            }
        )

    def _calculate_error_rate(self, account_data: dict[str, Any]) -> Decimal:
        """计算错误率"""
        total_requests = account_data.get("total_requests", 1000)
        error_count = account_data.get("error_count", 10)
        error_rate = error_count / total_requests if total_requests > 0 else 0
        return Decimal(str(error_rate))

    def _calculate_system_availability(self, account_data: dict[str, Any]) -> Decimal:
        """计算系统可用性"""
        uptime_seconds = account_data.get("uptime_seconds", 86400)  # 24小时
        downtime_seconds = account_data.get("downtime_seconds", 3600)  # 1小时
        total_time = uptime_seconds + downtime_seconds

        availability = uptime_seconds / total_time if total_time > 0 else 0
        return Decimal(str(availability))

    def _calculate_data_quality_score(self, account_data: dict[str, Any]) -> Decimal:
        """计算数据质量评分"""
        completeness = account_data.get("data_completeness", 0.95)
        accuracy = account_data.get("data_accuracy", 0.98)
        timeliness = account_data.get("data_timeliness", 0.90)
        consistency = account_data.get("data_consistency", 0.92)

        quality_score = (completeness + accuracy + timeliness + consistency) / 4
        return Decimal(str(quality_score))

    def _calculate_processing_capacity(self, account_data: dict[str, Any]) -> Decimal:
        """计算处理能力"""
        current_load = account_data.get("current_load", 0.6)
        max_capacity = 1.0
        capacity_utilization = current_load / max_capacity
        return Decimal(str(capacity_utilization))

    def _calculate_vulnerability_score(self, account_data: dict[str, Any]) -> Decimal:
        """计算漏洞评分"""
        critical_vulns = account_data.get("critical_vulnerabilities", 0)
        high_vulns = account_data.get("high_vulnerabilities", 1)
        medium_vulns = account_data.get("medium_vulnerabilities", 3)
        low_vulns = account_data.get("low_vulnerabilities", 5)

        # 加权计算漏洞评分
        vuln_score = (critical_vulns * 10 + high_vulns * 5 + medium_vulns * 2 + low_vulns * 1) / 100
        return Decimal(str(min(vuln_score, 1.0)))

    def _calculate_liquidity_score(
        self, position_data: dict[str, Any], market_data: dict[str, Any]
    ) -> Decimal:
        """计算流动性评分"""
        # 基于多个流动性指标
        bid_ask_spread = market_data.get("bid_ask_spread", 10)  # bps
        market_depth = market_data.get("market_depth", 1000000)  # USD
        volume_24h = market_data.get("volume_24h", 50000000)  # USD

        # 流动性评分 (0-1)
        spread_score = max(0, 1 - bid_ask_spread / 100)  # 假设100bps为最差
        depth_score = min(1, market_depth / 10000000)  # 假设1000万为满分
        volume_score = min(1, volume_24h / 100000000)  # 假设1亿为满分

        liquidity_score = (spread_score + depth_score + volume_score) / 3
        return Decimal(str(liquidity_score))

    def _calculate_bid_ask_spread(self, market_data: dict[str, Any]) -> Decimal:
        """计算买卖价差"""
        bid_price = market_data.get("bid_price", 0)
        ask_price = market_data.get("ask_price", 0)
        mid_price = (bid_price + ask_price) / 2

        if mid_price == 0:
            return Decimal("0")

        spread_bps = ((ask_price - bid_price) / mid_price) * 10000
        return Decimal(str(spread_bps))

    def _calculate_market_depth(self, market_data: dict[str, Any]) -> Decimal:
        """计算市场深度"""
        bid_depth = market_data.get("bid_depth", 0)  # 买单深度
        ask_depth = market_data.get("ask_depth", 0)  # 卖单深度
        total_depth = bid_depth + ask_depth

        return Decimal(str(total_depth))

    def _calculate_impact_cost(
        self, position_data: dict[str, Any], market_data: dict[str, Any]
    ) -> Decimal:
        """计算冲击成本"""
        position_size = position_data.get("position_size", 0)
        market_depth = market_data.get("market_depth", 1000000)
        bid_ask_spread = market_data.get("bid_ask_spread", 10)

        if market_depth == 0:
            return Decimal("0")

        # 冲击成本基于仓位大小与市场深度的比例
        size_ratio = abs(position_size) / market_depth
        spread_cost = bid_ask_spread / 2  # bps
        impact_cost = spread_cost * (1 + size_ratio)

        return Decimal(str(impact_cost))

    def _calculate_volume_profile(self, market_data: dict[str, Any]) -> dict[str, Any]:
        """计算成交量分布"""
        volume_data = market_data.get("volume_by_price", {})

        if not volume_data:
            return {}

        total_volume = sum(volume_data.values())
        volume_profile = {}

        for price, volume in volume_data.items():
            volume_profile[str(price)] = {
                "volume": volume,
                "percentage": volume / total_volume if total_volume > 0 else 0,
            }

        return volume_profile

    def _calculate_liquidation_value(
        self, position_data: dict[str, Any], market_data: dict[str, Any]
    ) -> Decimal:
        """计算清算价值"""
        positions = position_data.get("positions", [])
        liquidation_value = 0

        for pos in positions:
            symbol = pos.get("symbol", "")
            quantity = pos.get("quantity", 0)

            # 获取清算价格 (简化为当前价格的95%)
            current_price = market_data.get("current_prices", {}).get(symbol, 0)
            liquidation_price = current_price * 0.95

            liquidation_value += quantity * liquidation_price

        return Decimal(str(liquidation_value))

    def _calculate_compliance_score(self, account_data: dict[str, Any]) -> Decimal:
        """计算合规评分"""
        kyc_compliance = 1.0 if account_data.get("kyc_status") == "VERIFIED" else 0.0
        aml_compliance = 1.0 if not account_data.get("aml_flags", []) else 0.5
        reporting_compliance = account_data.get("reporting_compliance", 0.9)
        policy_compliance = account_data.get("policy_compliance", 0.85)

        compliance_score = (
            kyc_compliance + aml_compliance + reporting_compliance + policy_compliance
        ) / 4
        return Decimal(str(compliance_score))

    def _get_regulatory_violations(self, account_data: dict[str, Any]) -> list[dict[str, Any]]:
        """获取监管违规记录"""
        return cast("list[dict[str, Any]]", account_data.get("regulatory_violations", []))

    def _calculate_reporting_compliance(self, account_data: dict[str, Any]) -> Decimal:
        """计算报告合规度"""
        required_reports = account_data.get("required_reports", 100)
        submitted_reports = account_data.get("submitted_reports", 95)

        compliance_rate = submitted_reports / required_reports if required_reports > 0 else 0
        return Decimal(str(compliance_rate))

    def _get_audit_findings(self, account_data: dict[str, Any]) -> list[dict[str, Any]]:
        """获取审计发现"""
        return cast("list[dict[str, Any]]", account_data.get("audit_findings", []))

    def _calculate_policy_adherence(self, account_data: dict[str, Any]) -> Decimal:
        """计算政策遵循度"""
        policy_checks = account_data.get("policy_checks", [])
        if not policy_checks:
            return Decimal("1.0")

        passed_checks = sum(1 for check in policy_checks if check.get("passed", False))
        adherence_rate = passed_checks / len(policy_checks)

        return Decimal(str(adherence_rate))

    def _get_aml_flags(self, account_data: dict[str, Any]) -> list[str]:
        """获取AML标志"""
        return cast("list[str]", account_data.get("aml_flags", []))

    def _check_all_risk_limits(
        self,
        market_risk: MarketRiskMetrics,
        credit_risk: CreditRiskMetrics,
        operational_risk: OperationalRiskMetrics,
        liquidity_risk: LiquidityRiskMetrics,
    ) -> LimitsCheckResult:
        """检查所有风险限制"""
        # 简化的限制检查
        return LimitsCheckResult(
            {
                "limit_name": "comprehensive_check",
                "current_value": 0.7,  # 示例值
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
        """计算历史对比"""
        # 简化的历史对比
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
        """计算预测性指标"""
        # 简化的预测指标
        current_risk = float(market_risk.volatility)
        next_period_risk = current_risk * 1.05  # 假设风险略增

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
        """生成风险管理建议行动"""
        actions = []

        # 基于各类风险生成建议
        if float(market_risk.volatility) > 0.3:
            actions.append("考虑增加对冲仓位以降低市场风险")

        if float(credit_risk.probability_of_default) > 0.02:
            actions.append("审查交易对手信用状况，考虑降低暴露")

        if float(operational_risk.system_health_score) < 0.8:
            actions.append("加强系统监控，优化性能")

        if float(liquidity_risk.liquidity_score) < 0.6:
            actions.append("优化流动性管理，考虑持有更多现金")

        if float(compliance_risk.compliance_score) < 0.8:
            actions.append("加强合规审查，确保符合监管要求")

        if not actions:
            actions.append("风险水平正常，继续保持监控")

        return actions

    def _serialize_metrics(self, obj: Any) -> Any:
        """序列化指标对象为字典或可序列化类型"""
        if isinstance(obj, Decimal):
            return str(obj)
        if isinstance(obj, dict):
            return {k: self._serialize_metrics(v) for k, v in obj.items()}
        if isinstance(obj, list):
            return [self._serialize_metrics(item) for item in obj]
        if hasattr(obj, "__dict__"):
            return {k: self._serialize_metrics(v) for k, v in obj.__dict__.items()}
        return obj
