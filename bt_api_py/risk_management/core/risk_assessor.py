"""-

，
"""

from __future__ import annotations

import time
from decimal import Decimal
from typing import Any, cast

from bt_api_base.logging_factory import get_logger

from ..containers.risk_events import RiskLevel
from ..containers.risk_metrics import RiskMetrics


class RiskAssessmentResult:
    """"""

    def __init__(self, data: dict[str, Any]) -> None:
        """__init__ method"""
        self.score = Decimal(str(data.get("score", 0)))  #  0-1
        self.level = RiskLevel(data.get("level", "LOW"))  #
        self.confidence = Decimal(str(data.get("confidence", 0)))  #  0-1
        self.factors = data.get("factors", {})  #
        self.recommendations = data.get("recommendations", [])  #
        self.prediction = data.get("prediction", {})  #
        self.model_version = data.get("model_version", "")  #
        self.assessment_time = data.get("assessment_time", int(time.time()))  #


class RiskFactor:
    """"""

    def __init__(self, name: str, weight: float, score: float, description: str = "") -> None:
        """__init__ method"""
        self.name = name
        self.weight = weight  #  0-1
        self.score = score  #  0-1
        self.description = description
        self.contribution = weight * score  #


class RiskAssessor:
    """



    :
    1.  (、、、、)
    2.
    3.
    4.
    5.
    6.
    """

    def __init__(self, config: dict[str, Any] | None = None) -> None:
        """

        Args: config:
        """
        self.logger = get_logger("risk_assessor")
        self.config = config or {}

        #
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

        #
        self.risk_thresholds = self.config.get(
            "risk_thresholds",
            {
                "low": 0.3,
                "medium": 0.6,
                "high": 0.8,
                "critical": 0.9,
            },
        )

        #
        self.use_ml_models = self.config.get("use_ml_models", True)
        self.model_update_interval = self.config.get("model_update_interval", 86400)  # 24
        self.min_samples_for_ml = self.config.get("min_samples_for_ml", 1000)

        #
        self.historical_assessments: list[RiskAssessmentResult] = []
        self.risk_factors_history: list[dict[str, float]] = []

        #
        self.assessment_stats = {
            "total_assessments": 0,
            "average_score": 0.0,
            "score_distribution": {"LOW": 0, "MEDIUM": 0, "HIGH": 0, "CRITICAL": 0},
        }

        #
        self._init_ml_components()

        self.logger.info("RiskAssessor initialized")

    def _init_ml_components(self) -> None:
        """"""
        # ML
        # ，
        self.ml_models = {
            "random_forest": self._create_simple_rf_model(),
            "neural_network": self._create_simple_nn_model(),
            "ensemble": self._create_ensemble_model(),
        }

        #
        self.last_training_time = 0
        self.model_accuracy = {"random_forest": 0.8, "neural_network": 0.75, "ensemble": 0.85}

    def assess_risk(self, risk_metrics: RiskMetrics) -> RiskAssessmentResult:
        """

        Args: risk_metrics:

        Returns: RiskAssessmentResult:
        """
        try:
            self.logger.debug(
                f"Assessing risk for {risk_metrics.exchange_name}:{risk_metrics.account_id}"
            )

            #
            risk_factors = self._extract_risk_factors(risk_metrics)

            #
            traditional_score = self._calculate_traditional_score(risk_factors)

            # ML
            ml_score = 0.0
            ml_confidence = 0.0
            if self.use_ml_models and len(self.historical_assessments) >= self.min_samples_for_ml:
                ml_score, ml_confidence = self._predict_with_ml(risk_factors)

            #
            final_score = self._ensemble_scores(traditional_score, ml_score, ml_confidence)

            #
            risk_level = self._determine_risk_level(float(final_score))

            #
            recommendations = self._generate_recommendations(risk_factors, risk_level)

            #
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

            #
            self._update_historical_data(result, risk_factors)

            #
            self._update_statistics(result)

            total = cast("int", self.assessment_stats["total_assessments"])
            self.assessment_stats["total_assessments"] = total + 1

            return result

        except Exception as e:
            self.logger.error(f"Error assessing risk: {e}")
            #
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
        """

        Args: risk_metrics:

        Returns: List[RiskFactor]:
        """
        factors = []

        #
        factors.append(
            RiskFactor(
                name="market_volatility",
                weight=self.factor_weights["market_risk"] * 0.4,
                score=min(float(risk_metrics.market_risk.volatility), 1.0),
                description="",
            )
        )

        factors.append(
            RiskFactor(
                name="value_at_risk",
                weight=self.factor_weights["market_risk"] * 0.3,
                score=min(float(risk_metrics.market_risk.value_at_risk_1d) / 1000000, 1.0),  # 100
                description="",
            )
        )

        factors.append(
            RiskFactor(
                name="position_concentration",
                weight=self.factor_weights["market_risk"] * 0.3,
                score=float(risk_metrics.market_risk.position_concentration.herfindahl_index),
                description="",
            )
        )

        #
        factors.append(
            RiskFactor(
                name="credit_score",
                weight=self.factor_weights["credit_risk"] * 0.5,
                score=max(0, 1 - float(risk_metrics.credit_risk.credit_score) / 850),  # 850
                description="",
            )
        )

        factors.append(
            RiskFactor(
                name="default_probability",
                weight=self.factor_weights["credit_risk"] * 0.5,
                score=float(risk_metrics.credit_risk.probability_of_default),
                description="",
            )
        )

        #
        factors.append(
            RiskFactor(
                name="system_health",
                weight=self.factor_weights["operational_risk"] * 0.4,
                score=max(0, 1 - float(risk_metrics.operational_risk.system_health_score)),
                description="",
            )
        )

        factors.append(
            RiskFactor(
                name="error_rate",
                weight=self.factor_weights["operational_risk"] * 0.3,
                score=float(risk_metrics.operational_risk.error_rate),
                description="",
            )
        )

        factors.append(
            RiskFactor(
                name="system_availability",
                weight=self.factor_weights["operational_risk"] * 0.3,
                score=max(0, 1 - float(risk_metrics.operational_risk.system_availability)),
                description="",
            )
        )

        #
        factors.append(
            RiskFactor(
                name="liquidity_score",
                weight=self.factor_weights["liquidity_risk"] * 0.5,
                score=max(0, 1 - float(risk_metrics.liquidity_risk.liquidity_score)),
                description="",
            )
        )

        factors.append(
            RiskFactor(
                name="bid_ask_spread",
                weight=self.factor_weights["liquidity_risk"] * 0.5,
                score=min(float(risk_metrics.liquidity_risk.bid_ask_spread) / 1000, 1.0),  # 1000bps
                description="",
            )
        )

        #
        factors.append(
            RiskFactor(
                name="compliance_score",
                weight=self.factor_weights["compliance_risk"] * 0.6,
                score=max(0, 1 - float(risk_metrics.compliance_risk.compliance_score)),
                description="",
            )
        )

        factors.append(
            RiskFactor(
                name="kyc_status",
                weight=self.factor_weights["compliance_risk"] * 0.4,
                score=0.0 if risk_metrics.compliance_risk.kyc_status == "VERIFIED" else 0.5,
                description="KYC",
            )
        )

        return factors

    def _calculate_traditional_score(self, risk_factors: list[RiskFactor]) -> float:
        """

        Args: risk_factors:

        Returns: float:  0-1
        """
        total_score = 0.0
        total_weight = 0.0

        for factor in risk_factors:
            total_score += factor.contribution
            total_weight += factor.weight

        return total_score if total_weight > 0 else 0.0

    def _predict_with_ml(self, risk_factors: list[RiskFactor]) -> tuple[float, float]:
        """

        Args: risk_factors:

        Returns: Tuple[float, float]: (, )
        """
        if not self.use_ml_models:
            return 0.0, 0.0

        #
        features = [rf.score for rf in risk_factors]

        # ML
        #
        rf_score = self._predict_rf(features) * self.model_accuracy["random_forest"]
        nn_score = self._predict_nn(features) * self.model_accuracy["neural_network"]
        ensemble_score = self._predict_ensemble(features) * self.model_accuracy["ensemble"]

        #
        final_score = (rf_score + nn_score + ensemble_score) / 3

        #
        confidence = sum(self.model_accuracy.values()) / len(self.model_accuracy)

        return final_score, confidence

    def _ensemble_scores(
        self, traditional_score: float, ml_score: float, ml_confidence: float
    ) -> Decimal:
        """ML

        Args: traditional_score:
            ml_score: ML
            ml_confidence: ML

        Returns: Decimal:
        """
        if ml_confidence > 0.5:
            # ML，ML
            ml_weight = 0.6
            traditional_weight = 0.4
        else:
            # ML，
            ml_weight = 0.2
            traditional_weight = 0.8

        final_score = traditional_score * traditional_weight + ml_score * ml_weight
        return Decimal(str(min(final_score, 1.0)))

    def _determine_risk_level(self, score: float) -> RiskLevel:
        """

        Args: score:  0-1

        Returns: RiskLevel:
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
        """

        Args: risk_factors:
            risk_level:

        Returns: List[str]:
        """
        recommendations = []

        #
        if (
            risk_level == RiskLevel.CRITICAL
            or risk_level == RiskLevel.HIGH
            or risk_level == RiskLevel.MEDIUM
        ):
            recommendations.extend(["", "", ""])

        #
        high_risk_factors = [rf for rf in risk_factors if rf.score > 0.7]
        for factor in high_risk_factors:
            if factor.name == "market_volatility":
                recommendations.append("")
            elif factor.name == "position_concentration":
                recommendations.append("，")
            elif (
                factor.name == "credit_score"
                or factor.name == "system_health"
                or factor.name == "liquidity_score"
                or factor.name == "compliance_score"
            ):
                recommendations.append("")

        return recommendations

    def _predict_future_risk(self, risk_factors: list[RiskFactor]) -> dict[str, Any]:
        """

        Args: risk_factors:

        Returns: Dict[str, Any]:
        """
        #
        current_scores = [rf.score for rf in risk_factors]
        avg_score = sum(current_scores) / len(current_scores)

        #
        trend = "STABLE"
        if len(self.historical_assessments) >= 5:
            recent_scores = [float(r.score) for r in self.historical_assessments[-5:]]
            if recent_scores[-1] > recent_scores[0]:
                trend = "INCREASING"
            elif recent_scores[-1] < recent_scores[0]:
                trend = "DECREASING"

        #
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
        """

        Args: result:
            risk_factors:
        """
        self.historical_assessments.append(result)

        #
        if len(self.historical_assessments) > 10000:
            self.historical_assessments = self.historical_assessments[-5000:]

        #
        factors_data = {rf.name: rf.score for rf in risk_factors}
        self.risk_factors_history.append(factors_data)

        if len(self.risk_factors_history) > 10000:
            self.risk_factors_history = self.risk_factors_history[-5000:]

    def _update_statistics(self, result: RiskAssessmentResult) -> None:
        """

        Args: result:
        """
        total = cast("int", self.assessment_stats["total_assessments"])
        current_avg = cast("float", self.assessment_stats["average_score"])
        new_score = float(result.score)

        #
        self.assessment_stats["average_score"] = (current_avg * (total - 1) + new_score) / total

        #
        dist = cast("dict[str, int]", self.assessment_stats["score_distribution"])
        dist[result.level.value] = dist.get(result.level.value, 0) + 1

    def get_risk_statistics(self) -> dict[str, Any]:
        """

        Returns: Dict[str, Any]:
        """
        return {
            "assessment_stats": self.assessment_stats,
            "historical_count": len(self.historical_assessments),
            "model_accuracy": self.model_accuracy,
            "factor_weights": self.factor_weights,
            "risk_thresholds": self.risk_thresholds,
        }

    # ML (scikit-learn、tensorflow)

    def _create_simple_rf_model(self) -> Any:
        """"""
        return {"type": "random_forest", "trained": False}

    def _create_simple_nn_model(self) -> Any:
        """"""
        return {"type": "neural_network", "trained": False}

    def _create_ensemble_model(self) -> Any:
        """"""
        return {"type": "ensemble", "trained": False}

    def _predict_rf(self, features: list[float]) -> float:
        """"""
        #
        return sum(features) / len(features) * 0.9

    def _predict_nn(self, features: list[float]) -> float:
        """"""
        #
        return sum(features) / len(features) * 0.95

    def _predict_ensemble(self, features: list[float]) -> float:
        """"""
        #
        return sum(features) / len(features) * 0.92
