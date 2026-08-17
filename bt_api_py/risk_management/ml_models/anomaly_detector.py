"""异常检测门面 - 、

规则型检测器拆到 anomaly_detectors.py，类型定义拆到 anomaly_types.py，
本模块保留模型训练/检测编排逻辑。
"""

from __future__ import annotations

import time
from typing import Any, cast

import numpy as np

from .anomaly_detectors import AnomalyDetectorsMixin
from .anomaly_types import AnomalyDetectionResult, AnomalySeverity, AnomalyType
from .ml_base import BaseMLModel

__all__ = [
    "AnomalyDetectionResult",
    "AnomalyDetector",
    "AnomalySeverity",
    "AnomalyType",
]


class AnomalyDetector(BaseMLModel, AnomalyDetectorsMixin):
    """
    异常检测门面。

    方法:
    1. Isolation Forest - 隔离森林
    2. One-Class SVM - 单类 SVM
    3. 统计方法 - Z-score, IQR
    4. 规则型检测器 - AnomalyDetectorsMixin
    """

    def __init__(self, config: dict[str, Any] | None = None) -> None:
        """
        初始化。

        Args: config: 配置字典
        """
        super().__init__("AnomalyDetector", config)

        # 
        self.contamination = self.config.get("contamination", 0.1)  # 
        self.anomaly_threshold = self.config.get("anomaly_threshold", 0.5)
        self.use_ensemble = self.config.get("use_ensemble", True)

        # 
        from sklearn.ensemble import IsolationForest
        from sklearn.preprocessing import StandardScaler
        from sklearn.svm import OneClassSVM

        self.isolation_forest = IsolationForest(
            contamination=self.contamination, random_state=42, n_estimators=100
        )
        self.one_class_svm = OneClassSVM(kernel="rbf", gamma="scale", nu=self.contamination)
        self.scaler = StandardScaler()

        # 
        self.z_threshold = self.config.get("z_threshold", 3.0)
        self.iqr_factor = self.config.get("iqr_factor", 1.5)

        # 
        self.window_size = self.config.get("window_size", 50)
        self.trend_threshold = self.config.get("trend_threshold", 2.0)

        # 
        self.detection_history: list[AnomalyDetectionResult] = []
        self.feature_stats: dict[str, dict[str, float]] = {}

        # 
        self.anomaly_patterns = self._load_anomaly_patterns()

        self.logger.info("AnomalyDetector initialized")

    def train(
        self,
        X: np.ndarray,
        y: np.ndarray | None = None,
        validation_data: tuple[np.ndarray, np.ndarray] | None = None,
    ) -> dict[str, Any]:
        """
        训练模型。

        Args: X: 特征矩阵（无标签）
            y: 标签（可选）
            validation_data: 验证数据

        Returns: Dict[str, Any]: 训练结果
        """
        start_time = time.time()

        if not self._validate_input(X):
            return {"error": "Invalid input data"}

        try:
            # 
            X_processed = self._preprocess_features(X)

            # Isolation Forest
            self.isolation_forest.fit(X_processed)

            # One-Class SVM ()
            if len(X_processed) < 10000:  # 
                self.one_class_svm.fit(X_processed)

            # 
            self._compute_feature_statistics(X_processed)

            # 
            self.is_trained = True
            self.training_time = time.time() - start_time
            self.last_training_time = int(time.time())
            self.metrics["training_samples"] = len(X_processed)

            # 
            self._record_training_step(
                {
                    "action": "train",
                    "samples": len(X_processed),
                    "features": X_processed.shape[1],
                    "contamination": self.contamination,
                    "training_time": self.training_time,
                }
            )

            result = {
                "success": True,
                "training_time": self.training_time,
                "samples_trained": len(X_processed),
                "features_used": X_processed.shape[1],
                "model_info": self.get_model_info(),
            }

            self.logger.info(f"AnomalyDetector trained successfully in {self.training_time:.2f}s")
            return result

        except Exception as e:
            self.logger.error(f"Error training anomaly detector: {e}")
            return {"error": str(e)}

    def detect_anomaly(
        self, X: np.ndarray | dict[str, Any], method: str = "ensemble"
    ) -> AnomalyDetectionResult:
        """
        检测异常。

        Args: X: 特征向量（或字典）
            method: 检测方法 ("isolation_forest", "one_class_svm", "statistical", "ensemble")

        Returns: AnomalyDetectionResult: 检测结果
        """
        try:
            # 
            if isinstance(X, dict):
                X_vector = self._dict_to_features(X)
                feature_names = list(X.keys())
            else:
                X_vector = X.reshape(1, -1) if X.ndim == 1 else X
                feature_names = self.feature_names

            if not self.is_trained:
                return AnomalyDetectionResult(
                    is_anomaly=False,
                    anomaly_score=0.0,
                    anomaly_type=None,
                    severity=AnomalySeverity.LOW,
                    confidence=0.0,
                    explanation="Model not trained",
                    timestamp=int(time.time()),
                    features_used=feature_names,
                )

            # 
            X_processed = self._preprocess_features(X_vector)

            # 
            if method == "isolation_forest":
                result = self._detect_with_isolation_forest(X_processed, feature_names)
            elif method == "one_class_svm":
                result = self._detect_with_one_class_svm(X_processed, feature_names)
            elif method == "statistical":
                result = self._detect_statistical(X_processed, feature_names)
            elif method == "ensemble":
                result = self._detect_ensemble(X_processed, feature_names)
            else:
                raise ValueError(f"Unknown detection method: {method}")

            # 
            self.detection_history.append(result)
            if len(self.detection_history) > 10000:
                self.detection_history = self.detection_history[-5000:]

            return result

        except Exception as e:
            self.logger.error(f"Error detecting anomaly: {e}")
            return AnomalyDetectionResult(
                is_anomaly=False,
                anomaly_score=0.0,
                anomaly_type="detection_error",
                severity=AnomalySeverity.LOW,
                confidence=0.0,
                explanation=f"Detection error: {e}",
                timestamp=int(time.time()),
                features_used=[],
            )

    def predict(self, X: np.ndarray) -> np.ndarray:
        """
        预测异常。

        Args: X: 特征矩阵

        Returns: np.ndarray: 预测标签 (1, -1)
        """
        if not self.is_trained:
            raise ValueError("Model not trained")

        X_processed = self._preprocess_features(X)

        if self.use_ensemble:
            # 
            if_pred = self.isolation_forest.predict(X_processed)
            svm_pred = (
                self.one_class_svm.predict(X_processed) if len(X_processed) < 10000 else if_pred
            )

            # 
            predictions = []
            for i in range(len(X_processed)):
                votes = [if_pred[i], svm_pred[i]]
                prediction = 1 if sum(votes) > 0 else -1
                predictions.append(prediction)

            return np.array(predictions)
        else:
            return self.isolation_forest.predict(X_processed)  # type: ignore[no-any-return]

    def predict_proba(self, X: np.ndarray) -> np.ndarray:
        """
        预测异常概率。

        Args: X: 特征矩阵

        Returns: np.ndarray: 异常概率
        """
        if not self.is_trained:
            raise ValueError("Model not trained")

        X_processed = self._preprocess_features(X)

        # decision_function
        if self.use_ensemble:
            if_scores = self.isolation_forest.decision_function(X_processed)
            svm_scores = (
                self.one_class_svm.decision_function(X_processed)
                if len(X_processed) < 10000
                else if_scores
            )

            # 
            ensemble_scores = (if_scores + svm_scores) / 2
        else:
                ensemble_scores = cast(
                "np.ndarray", self.isolation_forest.decision_function(X_processed)
            )

        #  (sigmoid)
        probabilities = 1 / (1 + np.exp(-ensemble_scores))

        #  (1 - )
        return 1 - probabilities.reshape(-1, 1)  # type: ignore[no-any-return]

    def detect_trading_anomalies(
        self, trading_data: dict[str, Any]
    ) -> list[AnomalyDetectionResult]:
        """
        检测交易异常。

        Args: trading_data: 交易数据

        Returns: List[AnomalyDetectionResult]: 异常列表
        """
        anomalies = []

        # 
        features = self._extract_trading_features(trading_data)

        # 
        volume_anomaly = self._detect_volume_anomaly(trading_data, features)
        if volume_anomaly:
            anomalies.append(volume_anomaly)

        price_anomaly = self._detect_price_anomaly(trading_data, features)
        if price_anomaly:
            anomalies.append(price_anomaly)

        pattern_anomaly = self._detect_pattern_anomaly(trading_data, features)
        if pattern_anomaly:
            anomalies.append(pattern_anomaly)

        timing_anomaly = self._detect_timing_anomaly(trading_data, features)
        if timing_anomaly:
            anomalies.append(timing_anomaly)

        return anomalies

    def detect_market_anomalies(self, market_data: dict[str, Any]) -> list[AnomalyDetectionResult]:
        """
        检测市场异常。

        Args: market_data: 市场数据

        Returns: List[AnomalyDetectionResult]: 异常列表
        """
        anomalies = []

        # 
        features = self._extract_market_features(market_data)

        # 
        volatility_anomaly = self._detect_volatility_anomaly(market_data, features)
        if volatility_anomaly:
            anomalies.append(volatility_anomaly)

        liquidity_anomaly = self._detect_liquidity_anomaly(market_data, features)
        if liquidity_anomaly:
            anomalies.append(liquidity_anomaly)

        correlation_anomaly = self._detect_correlation_anomaly(market_data, features)
        if correlation_anomaly:
            anomalies.append(correlation_anomaly)

        return anomalies

    def detect_operational_anomalies(
        self, operational_data: dict[str, Any]
    ) -> list[AnomalyDetectionResult]:
        """
        检测操作异常。

        Args: operational_data: 操作数据

        Returns: List[AnomalyDetectionResult]: 异常列表
        """
        anomalies = []

        # 
        features = self._extract_operational_features(operational_data)

        # 
        performance_anomaly = self._detect_performance_anomaly(operational_data, features)
        if performance_anomaly:
            anomalies.append(performance_anomaly)

        error_anomaly = self._detect_error_anomaly(operational_data, features)
        if error_anomaly:
            anomalies.append(error_anomaly)

        access_anomaly = self._detect_access_anomaly(operational_data, features)
        if access_anomaly:
            anomalies.append(access_anomaly)

        return anomalies

    # 

    def _detect_with_isolation_forest(
        self, X: np.ndarray, feature_names: list[str]
    ) -> AnomalyDetectionResult:
        """Isolation Forest"""
        prediction = self.isolation_forest.predict(X)[0]
        score = self.isolation_forest.decision_function(X)[0]

        is_anomaly = prediction == -1
        anomaly_score = abs(score)

        # 
        anomaly_type, severity = self._classify_anomaly(X[0], is_anomaly, anomaly_score)

        return AnomalyDetectionResult(
            is_anomaly=is_anomaly,
            anomaly_score=anomaly_score,
            anomaly_type=anomaly_type,
            severity=severity,
            confidence=min(anomaly_score, 1.0),
            explanation=self._generate_explanation(X[0], feature_names, is_anomaly, score),
            timestamp=int(time.time()),
            features_used=feature_names,
        )

    def _detect_with_one_class_svm(
        self, X: np.ndarray, feature_names: list[str]
    ) -> AnomalyDetectionResult:
        """One-Class SVM"""
        prediction = self.one_class_svm.predict(X)[0]
        score = self.one_class_svm.decision_function(X)[0]

        is_anomaly = prediction == -1
        anomaly_score = abs(score)

        anomaly_type, severity = self._classify_anomaly(X[0], is_anomaly, anomaly_score)

        return AnomalyDetectionResult(
            is_anomaly=is_anomaly,
            anomaly_score=anomaly_score,
            anomaly_type=anomaly_type,
            severity=severity,
            confidence=min(anomaly_score, 1.0),
            explanation=self._generate_explanation(X[0], feature_names, is_anomaly, score),
            timestamp=int(time.time()),
            features_used=feature_names,
        )

    def _detect_statistical(
        self, X: np.ndarray, feature_names: list[str]
    ) -> AnomalyDetectionResult:
        """"""
        anomaly_scores = []
        explanations = []

        for i, feature_value in enumerate(X[0]):
            if i < len(feature_names):
                feature_name = feature_names[i]
                if feature_name in self.feature_stats:
                    stats = self.feature_stats[feature_name]
                    z_score = abs((feature_value - stats["mean"]) / stats["std"])
                    anomaly_scores.append(z_score)

                    if z_score > self.z_threshold:
                        explanations.append(f"{feature_name} z-score: {z_score:.2f}")

        max_anomaly_score = max(anomaly_scores) if anomaly_scores else 0
        is_anomaly = max_anomaly_score > self.z_threshold

        anomaly_type, severity = self._classify_anomaly(X[0], is_anomaly, max_anomaly_score)

        return AnomalyDetectionResult(
            is_anomaly=is_anomaly,
            anomaly_score=max_anomaly_score,
            anomaly_type=anomaly_type,
            severity=severity,
            confidence=min(max_anomaly_score / self.z_threshold, 1.0),
            explanation="; ".join(explanations),
            timestamp=int(time.time()),
            features_used=feature_names,
        )

    def _detect_ensemble(self, X: np.ndarray, feature_names: list[str]) -> AnomalyDetectionResult:
        """"""
        # 
        if_result = self._detect_with_isolation_forest(X, feature_names)
        svm_result = self._detect_with_one_class_svm(X, feature_names)
        stat_result = self._detect_statistical(X, feature_names)

        # 
        votes = [if_result.is_anomaly, svm_result.is_anomaly, stat_result.is_anomaly]
        vote_count = sum(votes)

        is_anomaly = vote_count >= 2  # 2

        # 
        ensemble_score = (
            if_result.anomaly_score * 0.4
            + svm_result.anomaly_score * 0.3
            + stat_result.anomaly_score * 0.3
        )

        # 
        explanations = []
        if if_result.is_anomaly:
            explanations.append(f"IsolationForest: {if_result.explanation}")
        if svm_result.is_anomaly:
            explanations.append(f"OneClassSVM: {svm_result.explanation}")
        if stat_result.is_anomaly:
            explanations.append(f"Statistical: {stat_result.explanation}")

        ensemble_explanation = (
            "; ".join(explanations) if explanations else "No significant anomalies detected"
        )

        anomaly_type, severity = self._classify_anomaly(X[0], is_anomaly, ensemble_score)

        return AnomalyDetectionResult(
            is_anomaly=is_anomaly,
            anomaly_score=ensemble_score,
            anomaly_type=anomaly_type,
            severity=severity,
            confidence=min(ensemble_score, 1.0),
            explanation=ensemble_explanation,
            timestamp=int(time.time()),
            features_used=feature_names,
        )

    def _classify_anomaly(
        self, features: np.ndarray, is_anomaly: bool, score: float
    ) -> tuple[str | None, str]:
        """"""
        if not is_anomaly:
            return None, AnomalySeverity.LOW

        # 
        if score > 0.8:
            severity = AnomalySeverity.CRITICAL
        elif score > 0.6:
            severity = AnomalySeverity.HIGH
        elif score > 0.4:
            severity = AnomalySeverity.MEDIUM
        else:
            severity = AnomalySeverity.LOW

        # 
        anomaly_type = "general_anomaly"

        return anomaly_type, severity

    def _generate_explanation(
        self, features: np.ndarray, feature_names: list[str], is_anomaly: bool, score: float
    ) -> str:
        """"""
        if not is_anomaly:
            return "No anomaly detected"

        # 
        if len(feature_names) == len(features):
            feature_contributions = [
                (name, abs(value)) for name, value in zip(feature_names, features)
            ]
            feature_contributions.sort(key=lambda x: x[1], reverse=True)

            top_features = feature_contributions[:3]
            feature_explanation = ", ".join(
                [f"{name}: {value:.3f}" for name, value in top_features]
            )

            return f"Anomaly detected. Score: {score:.3f}. Key features: {feature_explanation}"

        return f"Anomaly detected with score: {score:.3f}"

    def _extract_trading_features(self, trading_data: dict[str, Any]) -> np.ndarray:
        """"""
        features = [
            trading_data.get("volume", 0),
            trading_data.get("price_change", 0),
            trading_data.get("order_count", 0),
            trading_data.get("cancel_rate", 0),
            trading_data.get("time_between_orders", 0),
            trading_data.get("order_size_variance", 0),
        ]
        return np.array(features)

    def _extract_market_features(self, market_data: dict[str, Any]) -> np.ndarray:
        """"""
        features = [
            market_data.get("volatility", 0),
            market_data.get("bid_ask_spread", 0),
            market_data.get("volume_24h", 0),
            market_data.get("price_change_24h", 0),
            market_data.get("market_depth", 0),
            market_data.get("correlation_breakdown", 0),
        ]
        return np.array(features)

    def _extract_operational_features(self, operational_data: dict[str, Any]) -> np.ndarray:
        """"""
        features = [
            operational_data.get("response_time", 0),
            operational_data.get("error_rate", 0),
            operational_data.get("cpu_usage", 0),
            operational_data.get("memory_usage", 0),
            operational_data.get("request_rate", 0),
            operational_data.get("timeout_rate", 0),
        ]
        return np.array(features)

    def _compute_feature_statistics(self, X: np.ndarray) -> None:
        """"""
        self.feature_stats = {}

        for i in range(X.shape[1]):
            feature_values = X[:, i]
            self.feature_stats[f"feature_{i}"] = {
                "mean": float(np.mean(feature_values)),
                "std": float(np.std(feature_values)),
                "min": float(np.min(feature_values)),
                "max": float(np.max(feature_values)),
                "median": float(np.median(feature_values)),
                "q25": float(np.percentile(feature_values, 25)),
                "q75": float(np.percentile(feature_values, 75)),
            }

    def _dict_to_features(self, data: dict[str, Any]) -> np.ndarray:
        """"""
        if not self.feature_names:
            self.feature_names = list(data.keys())

        features = [float(data.get(name, 0)) for name in self.feature_names]
        return np.array(features).reshape(1, -1)

    def _load_anomaly_patterns(self) -> dict[str, Any]:
        """"""
        # 
        return {
            "volume_spike": {"threshold": 5.0, "description": "Unusual trading volume"},
            "price_crash": {"threshold": 0.1, "description": "Rapid price decline"},
            "flash_crash": {
                "threshold": 0.2,
                "description": "Extreme price movement in short time",
            },
            "liquidity_crisis": {"threshold": 100, "description": "Widening bid-ask spread"},
            "system_overload": {"threshold": 1000, "description": "High system response time"},
        }

    def get_anomaly_statistics(self) -> dict[str, Any]:
        """"""
        if not self.detection_history:
            return {}

        total_detections = len(self.detection_history)
        anomaly_count = sum(1 for d in self.detection_history if d.is_anomaly)

        severity_counts: dict[str, int] = {}
        type_counts: dict[str, int] = {}

        for detection in self.detection_history:
            if detection.is_anomaly:
                severity_counts[detection.severity] = severity_counts.get(detection.severity, 0) + 1
                if detection.anomaly_type:
                    type_counts[detection.anomaly_type] = (
                        type_counts.get(detection.anomaly_type, 0) + 1
                    )

        return {
            "total_detections": total_detections,
            "anomaly_count": anomaly_count,
            "anomaly_rate": anomaly_count / total_detections if total_detections > 0 else 0,
            "severity_distribution": severity_counts,
            "type_distribution": type_counts,
            "recent_anomalies": [d.to_dict() for d in self.detection_history[-10:]],
        }
