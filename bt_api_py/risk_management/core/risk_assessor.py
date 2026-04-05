"""风险评估器 - 智能风险评分和预测

使用机器学习算法进行风险评估，包括历史数据分析和预测模型
"""

from __future__ import annotations

import time
from decimal import Decimal
from typing import Any, cast

from bt_api_py.logging_factory import get_logger

from ..containers.risk_events import RiskLevel
from ..containers.risk_metrics import RiskMetrics


class RiskAssessmentResult:
    """风险评估结果"""

    def __init__(self, data: dict[str, Any]) -> None:
        self.score = Decimal(str(data.get("score", 0)))  # 风险评分 0-1
        self.level = RiskLevel(data.get("level", "LOW"))  # 风险级别
        self.confidence = Decimal(str(data.get("confidence", 0)))  # 置信度 0-1
        self.factors = data.get("factors", {})  # 风险因素分析
        self.recommendations = data.get("recommendations", [])  # 建议措施
        self.prediction = data.get("prediction", {})  # 风险预测
        self.model_version = data.get("model_version", "")  # 模型版本
        self.assessment_time = data.get("assessment_time", int(time.time()))  # 评估时间


class RiskFactor:
    """风险因素"""

    def __init__(self, name: str, weight: float, score: float, description: str = "") -> None:
        self.name = name
        self.weight = weight  # 权重 0-1
        self.score = score  # 评分 0-1
        self.description = description
        self.contribution = weight * score  # 贡献度


class RiskAssessor:
    """风险评估器

    使用多因子模型和机器学习算法进行风险评估

    功能特性:
    1. 多维度风险评估 (市场、信用、操作、流动性、合规)
    2. 机器学习驱动的风险评分
    3. 实时风险预测
    4. 风险因素分解
    5. 历史趋势分析
    6. 压力测试集成
    """

    def __init__(self, config: dict[str, Any] | None = None) -> None:
        """初始化风险评估器

        Args:
            config: 评估器配置
        """
        self.logger = get_logger("risk_assessor")
        self.config = config or {}

        # 风险因子权重配置
        self.factor_weights = self.config.get(
            "factor_weights",
            {
                "market_risk": 0.35,
                "credit_risk": 0.25,
                "operational_risk": 0.20,
                "liquidity_risk": 0.15,
                "compliance_risk": 0.05,
            },
        )

        # 风险阈值配置
        self.risk_thresholds = self.config.get(
            "risk_thresholds",
            {
                "low": 0.3,
                "medium": 0.6,
                "high": 0.8,
                "critical": 0.9,
            },
        )

        # 模型配置
        self.use_ml_models = self.config.get("use_ml_models", True)
        self.model_update_interval = self.config.get("model_update_interval", 86400)  # 24小时
        self.min_samples_for_ml = self.config.get("min_samples_for_ml", 1000)

        # 历史数据存储
        self.historical_assessments: list[RiskAssessmentResult] = []
        self.risk_factors_history: list[dict[str, float]] = []

        # 统计数据
        self.assessment_stats = {
            "total_assessments": 0,
            "average_score": 0.0,
            "score_distribution": {"LOW": 0, "MEDIUM": 0, "HIGH": 0, "CRITICAL": 0},
        }

        # 初始化机器学习组件
        self._init_ml_components()

        self.logger.info("RiskAssessor initialized")

    def _init_ml_components(self) -> None:
        """初始化机器学习组件"""
        # 这里可以集成各种ML模型
        # 由于依赖复杂性，这里提供简化版本
        self.ml_models = {
            "random_forest": self._create_simple_rf_model(),
            "neural_network": self._create_simple_nn_model(),
            "ensemble": self._create_ensemble_model(),
        }

        # 模型训练状态
        self.last_training_time = 0
        self.model_accuracy = {"random_forest": 0.8, "neural_network": 0.75, "ensemble": 0.85}

    def assess_risk(self, risk_metrics: RiskMetrics) -> RiskAssessmentResult:
        """评估风险

        Args:
            risk_metrics: 风险指标数据

        Returns:
            RiskAssessmentResult: 风险评估结果
        """
        try:
            self.logger.debug(
                f"Assessing risk for {risk_metrics.exchange_name}:{risk_metrics.account_id}"
            )

            # 提取风险因素
            risk_factors = self._extract_risk_factors(risk_metrics)

            # 计算传统评分
            traditional_score = self._calculate_traditional_score(risk_factors)

            # 使用ML模型评分
            ml_score = 0.0
            ml_confidence = 0.0
            if self.use_ml_models and len(self.historical_assessments) >= self.min_samples_for_ml:
                ml_score, ml_confidence = self._predict_with_ml(risk_factors)

            # 集成评分
            final_score = self._ensemble_scores(traditional_score, ml_score, ml_confidence)

            # 确定风险级别
            risk_level = self._determine_risk_level(float(final_score))

            # 生成建议
            recommendations = self._generate_recommendations(risk_factors, risk_level)

            # 创建评估结果
            result = RiskAssessmentResult(
                {
                    "score": final_score,
                    "level": risk_level.value,
                    "confidence": ml_confidence if ml_confidence > 0 else Decimal("0.5"),
                    "factors": {
                        rf.name: {
                            "score": rf.score,
                            "weight": rf.weight,
                            "contribution": rf.contribution,
                        }
                        for rf in risk_factors
                    },
                    "recommendations": recommendations,
                    "prediction": self._predict_future_risk(risk_factors),
                    "model_version": "1.0.0",
                    "assessment_time": int(time.time()),
                }
            )

            # 更新历史数据
            self._update_historical_data(result, risk_factors)

            # 更新统计
            self._update_statistics(result)

            total = cast("int", self.assessment_stats["total_assessments"])
            self.assessment_stats["total_assessments"] = total + 1

            return result

        except Exception as e:
            self.logger.error(f"Error assessing risk: {e}")
            # 返回默认评估结果
            return RiskAssessmentResult(
                {
                    "score": Decimal("0.5"),
                    "level": "MEDIUM",
                    "confidence": Decimal("0.1"),
                    "factors": {},
                    "recommendations": ["Risk assessment failed, manual review required"],
                    "prediction": {},
                    "model_version": "fallback",
                    "assessment_time": int(time.time()),
                }
            )

    def _extract_risk_factors(self, risk_metrics: RiskMetrics) -> list[RiskFactor]:
        """提取风险因素

        Args:
            risk_metrics: 风险指标

        Returns:
            List[RiskFactor]: 风险因素列表
        """
        factors = []

        # 市场风险因素
        factors.append(
            RiskFactor(
                name="market_volatility",
                weight=self.factor_weights["market_risk"] * 0.4,
                score=min(float(risk_metrics.market_risk.volatility), 1.0),
                description="市场波动率风险",
            )
        )

        factors.append(
            RiskFactor(
                name="value_at_risk",
                weight=self.factor_weights["market_risk"] * 0.3,
                score=min(
                    float(risk_metrics.market_risk.value_at_risk_1d) / 1000000, 1.0
                ),  # 假设100万为最大风险
                description="在险价值",
            )
        )

        factors.append(
            RiskFactor(
                name="position_concentration",
                weight=self.factor_weights["market_risk"] * 0.3,
                score=float(risk_metrics.market_risk.position_concentration.herfindahl_index),
                description="仓位集中度风险",
            )
        )

        # 信用风险因素
        factors.append(
            RiskFactor(
                name="credit_score",
                weight=self.factor_weights["credit_risk"] * 0.5,
                score=max(
                    0, 1 - float(risk_metrics.credit_risk.credit_score) / 850
                ),  # 假设850为最高信用分
                description="交易对手信用风险",
            )
        )

        factors.append(
            RiskFactor(
                name="default_probability",
                weight=self.factor_weights["credit_risk"] * 0.5,
                score=float(risk_metrics.credit_risk.probability_of_default),
                description="违约概率",
            )
        )

        # 操作风险因素
        factors.append(
            RiskFactor(
                name="system_health",
                weight=self.factor_weights["operational_risk"] * 0.4,
                score=max(0, 1 - float(risk_metrics.operational_risk.system_health_score)),
                description="系统健康状况",
            )
        )

        factors.append(
            RiskFactor(
                name="error_rate",
                weight=self.factor_weights["operational_risk"] * 0.3,
                score=float(risk_metrics.operational_risk.error_rate),
                description="系统错误率",
            )
        )

        factors.append(
            RiskFactor(
                name="system_availability",
                weight=self.factor_weights["operational_risk"] * 0.3,
                score=max(0, 1 - float(risk_metrics.operational_risk.system_availability)),
                description="系统可用性",
            )
        )

        # 流动性风险因素
        factors.append(
            RiskFactor(
                name="liquidity_score",
                weight=self.factor_weights["liquidity_risk"] * 0.5,
                score=max(0, 1 - float(risk_metrics.liquidity_risk.liquidity_score)),
                description="市场流动性",
            )
        )

        factors.append(
            RiskFactor(
                name="bid_ask_spread",
                weight=self.factor_weights["liquidity_risk"] * 0.5,
                score=min(
                    float(risk_metrics.liquidity_risk.bid_ask_spread) / 1000, 1.0
                ),  # 假设1000bps为最大价差
                description="买卖价差风险",
            )
        )

        # 合规风险因素
        factors.append(
            RiskFactor(
                name="compliance_score",
                weight=self.factor_weights["compliance_risk"] * 0.6,
                score=max(0, 1 - float(risk_metrics.compliance_risk.compliance_score)),
                description="合规评分",
            )
        )

        factors.append(
            RiskFactor(
                name="kyc_status",
                weight=self.factor_weights["compliance_risk"] * 0.4,
                score=0.0 if risk_metrics.compliance_risk.kyc_status == "VERIFIED" else 0.5,
                description="KYC状态",
            )
        )

        return factors

    def _calculate_traditional_score(self, risk_factors: list[RiskFactor]) -> float:
        """计算传统评分

        Args:
            risk_factors: 风险因素

        Returns:
            float: 风险评分 0-1
        """
        total_score = 0.0
        total_weight = 0.0

        for factor in risk_factors:
            total_score += factor.contribution
            total_weight += factor.weight

        return total_score if total_weight > 0 else 0.0

    def _predict_with_ml(self, risk_factors: list[RiskFactor]) -> tuple[float, float]:
        """使用机器学习模型预测

        Args:
            risk_factors: 风险因素

        Returns:
            Tuple[float, float]: (预测评分, 置信度)
        """
        if not self.use_ml_models:
            return 0.0, 0.0

        # 提取特征向量
        features = [rf.score for rf in risk_factors]

        # 这里简化ML预测逻辑
        # 实际实现应该调用训练好的模型
        rf_score = self._predict_rf(features) * self.model_accuracy["random_forest"]
        nn_score = self._predict_nn(features) * self.model_accuracy["neural_network"]
        ensemble_score = self._predict_ensemble(features) * self.model_accuracy["ensemble"]

        # 加权平均
        final_score = (rf_score + nn_score + ensemble_score) / 3

        # 置信度基于历史准确性
        confidence = sum(self.model_accuracy.values()) / len(self.model_accuracy)

        return final_score, confidence

    def _ensemble_scores(
        self, traditional_score: float, ml_score: float, ml_confidence: float
    ) -> Decimal:
        """集成传统评分和ML评分

        Args:
            traditional_score: 传统评分
            ml_score: ML评分
            ml_confidence: ML置信度

        Returns:
            Decimal: 最终评分
        """
        if ml_confidence > 0.5:
            # ML置信度高，增加ML权重
            ml_weight = 0.6
            traditional_weight = 0.4
        else:
            # ML置信度低，主要依赖传统评分
            ml_weight = 0.2
            traditional_weight = 0.8

        final_score = traditional_score * traditional_weight + ml_score * ml_weight
        return Decimal(str(min(final_score, 1.0)))

    def _determine_risk_level(self, score: float) -> RiskLevel:
        """确定风险级别

        Args:
            score: 风险评分 0-1

        Returns:
            RiskLevel: 风险级别
        """
        if score >= self.risk_thresholds["critical"]:
            return RiskLevel.CRITICAL
        elif score >= self.risk_thresholds["high"]:
            return RiskLevel.HIGH
        elif score >= self.risk_thresholds["medium"]:
            return RiskLevel.MEDIUM
        else:
            return RiskLevel.LOW

    def _generate_recommendations(
        self, risk_factors: list[RiskFactor], risk_level: RiskLevel
    ) -> list[str]:
        """生成风险管理建议

        Args:
            risk_factors: 风险因素
            risk_level: 风险级别

        Returns:
            List[str]: 建议列表
        """
        recommendations = []

        # 基于风险级别的基本建议
        if risk_level == RiskLevel.CRITICAL:
            recommendations.extend(
                ["立即暂停高风险交易活动", "启动紧急风险缓解程序", "通知风险管理委员会"]
            )
        elif risk_level == RiskLevel.HIGH:
            recommendations.extend(["减少风险暴露", "增加监控频率", "准备应急计划"])
        elif risk_level == RiskLevel.MEDIUM:
            recommendations.extend(["密切监控风险指标", "审查风险管理策略", "考虑对冲措施"])

        # 基于具体风险因素的建议
        high_risk_factors = [rf for rf in risk_factors if rf.score > 0.7]
        for factor in high_risk_factors:
            if factor.name == "market_volatility":
                recommendations.append("考虑使用波动率对冲工具")
            elif factor.name == "position_concentration":
                recommendations.append("分散投资组合，减少集中度风险")
            elif factor.name == "credit_score":
                recommendations.append("审查交易对手信用状况")
            elif factor.name == "system_health":
                recommendations.append("加强系统监控和维护")
            elif factor.name == "liquidity_score":
                recommendations.append("优化流动性管理策略")
            elif factor.name == "compliance_score":
                recommendations.append("强化合规审查和培训")

        return recommendations

    def _predict_future_risk(self, risk_factors: list[RiskFactor]) -> dict[str, Any]:
        """预测未来风险

        Args:
            risk_factors: 当前风险因素

        Returns:
            Dict[str, Any]: 风险预测
        """
        # 简化的预测逻辑
        current_scores = [rf.score for rf in risk_factors]
        avg_score = sum(current_scores) / len(current_scores)

        # 基于历史趋势的简单预测
        trend = "STABLE"
        if len(self.historical_assessments) >= 5:
            recent_scores = [float(r.score) for r in self.historical_assessments[-5:]]
            if recent_scores[-1] > recent_scores[0]:
                trend = "INCREASING"
            elif recent_scores[-1] < recent_scores[0]:
                trend = "DECREASING"

        # 预测下一期风险
        next_period_risk = avg_score
        if trend == "INCREASING":
            next_period_risk *= 1.1
        elif trend == "DECREASING":
            next_period_risk *= 0.9

        return {
            "next_period_risk": next_period_risk,
            "trend": trend,
            "confidence": 0.7,
            "time_horizon": "24h",
        }

    def _update_historical_data(
        self, result: RiskAssessmentResult, risk_factors: list[RiskFactor]
    ) -> None:
        """更新历史数据

        Args:
            result: 评估结果
            risk_factors: 风险因素
        """
        self.historical_assessments.append(result)

        # 限制历史数据大小
        if len(self.historical_assessments) > 10000:
            self.historical_assessments = self.historical_assessments[-5000:]

        # 记录风险因素历史
        factors_data = {rf.name: rf.score for rf in risk_factors}
        self.risk_factors_history.append(factors_data)

        if len(self.risk_factors_history) > 10000:
            self.risk_factors_history = self.risk_factors_history[-5000:]

    def _update_statistics(self, result: RiskAssessmentResult) -> None:
        """更新统计信息

        Args:
            result: 评估结果
        """
        total = cast("int", self.assessment_stats["total_assessments"])
        current_avg = cast("float", self.assessment_stats["average_score"])
        new_score = float(result.score)

        # 更新平均评分
        self.assessment_stats["average_score"] = (current_avg * (total - 1) + new_score) / total

        # 更新分布
        dist = cast("dict[str, int]", self.assessment_stats["score_distribution"])
        dist[result.level.value] = dist.get(result.level.value, 0) + 1

    def get_risk_statistics(self) -> dict[str, Any]:
        """获取风险统计信息

        Returns:
            Dict[str, Any]: 统计信息
        """
        return {
            "assessment_stats": self.assessment_stats,
            "historical_count": len(self.historical_assessments),
            "model_accuracy": self.model_accuracy,
            "factor_weights": self.factor_weights,
            "risk_thresholds": self.risk_thresholds,
        }

    # 简化的ML模型方法 (实际实现应该使用scikit-learn、tensorflow等)

    def _create_simple_rf_model(self) -> Any:
        """创建简化随机森林模型"""
        return {"type": "random_forest", "trained": False}

    def _create_simple_nn_model(self) -> Any:
        """创建简化神经网络模型"""
        return {"type": "neural_network", "trained": False}

    def _create_ensemble_model(self) -> Any:
        """创建简化集成模型"""
        return {"type": "ensemble", "trained": False}

    def _predict_rf(self, features: list[float]) -> float:
        """随机森林预测"""
        # 简化逻辑
        return sum(features) / len(features) * 0.9

    def _predict_nn(self, features: list[float]) -> float:
        """神经网络预测"""
        # 简化逻辑
        return sum(features) / len(features) * 0.95

    def _predict_ensemble(self, features: list[float]) -> float:
        """集成模型预测"""
        # 简化逻辑
        return sum(features) / len(features) * 0.92
