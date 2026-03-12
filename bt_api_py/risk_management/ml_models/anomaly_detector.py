"""异常检测器 - 检测交易异常、市场异常和操作异常

使用多种算法检测异常行为和模式
"""

import time
from typing import Any, cast

import numpy as np
from sklearn.ensemble import IsolationForest
from sklearn.preprocessing import StandardScaler
from sklearn.svm import OneClassSVM

from .ml_base import BaseMLModel


class AnomalyType:
    """异常类型常量"""

    # 交易异常
    UNUSUAL_VOLUME = "unusual_volume"  # 异常成交量
    RAPID_PRICE_CHANGE = "rapid_price_change"  # 价格快速变化
    SUSPICIOUS_ORDER_PATTERN = "suspicious_order_pattern"  # 可疑订单模式
    COORDINATED_TRADING = "coordinated_trading"  # 协调交易
    FRONT_RUNNING = "front_running"  # 抢先交易
    SPOOFING = "spoofing"  # 虚假挂单

    # 市场异常
    LIQUIDITY_CRISIS = "liquidity_crisis"  # 流动性危机
    FLASH_CRASH = "flash_crash"  # 闪崩
    CORRELATION_BREAKDOWN = "correlation_breakdown"  # 相关性失效
    VOLATILITY_SPIKE = "volatility_spike"  # 波动率激增
    MARKET_MANIPULATION = "market_manipulation"  # 市场操纵

    # 操作异常
    SYSTEM_PERFORMANCE_DEGRADATION = "system_performance_degradation"  # 系统性能下降
    UNAUTHORIZED_ACCESS = "unauthorized_access"  # 未授权访问
    DATA_ANOMALY = "data_anomaly"  # 数据异常
    TIMEOUT_ANOMALY = "timeout_anomaly"  # 超时异常
    ERROR_RATE_SPIKE = "error_rate_spike"  # 错误率激增


class AnomalySeverity:
    """异常严重程度"""

    CRITICAL = "CRITICAL"  # 严重 - 需要立即处理
    HIGH = "HIGH"  # 高 - 需要快速响应
    MEDIUM = "MEDIUM"  # 中 - 需要关注
    LOW = "LOW"  # 低 - 仅需监控


class AnomalyDetectionResult:
    """异常检测结果"""

    def __init__(
        self,
        is_anomaly: bool,
        anomaly_score: float,
        anomaly_type: str | None,
        severity: str,
        confidence: float,
        explanation: str,
        timestamp: int,
        features_used: list[str],
    ) -> None:
        self.is_anomaly = is_anomaly
        self.anomaly_score = anomaly_score
        self.anomaly_type = anomaly_type
        self.severity = severity
        self.confidence = confidence
        self.explanation = explanation
        self.timestamp = timestamp
        self.features_used = features_used

    def to_dict(self) -> dict[str, Any]:
        """转换为字典格式"""
        return {
            "is_anomaly": self.is_anomaly,
            "anomaly_score": self.anomaly_score,
            "anomaly_type": self.anomaly_type,
            "severity": self.severity,
            "confidence": self.confidence,
            "explanation": self.explanation,
            "timestamp": self.timestamp,
            "features_used": self.features_used,
        }


class AnomalyDetector(BaseMLModel):
    """异常检测器

    使用多种算法检测异常:
    1. Isolation Forest - 隔离森林
    2. One-Class SVM - 单类支持向量机
    3. 统计方法 - Z-score, IQR等
    4. 时间序列异常检测
    5. 模式识别异常检测
    """

    def __init__(self, config: dict[str, Any] | None = None) -> None:
        """初始化异常检测器

        Args:
            config: 检测器配置
        """
        super().__init__("AnomalyDetector", config)

        # 检测器参数
        self.contamination = self.config.get("contamination", 0.1)  # 异常比例
        self.anomaly_threshold = self.config.get("anomaly_threshold", 0.5)
        self.use_ensemble = self.config.get("use_ensemble", True)

        # 检测器组件
        self.isolation_forest = IsolationForest(
            contamination=self.contamination, random_state=42, n_estimators=100
        )
        self.one_class_svm = OneClassSVM(kernel="rbf", gamma="scale", nu=self.contamination)
        self.scaler = StandardScaler()

        # 统计检测参数
        self.z_threshold = self.config.get("z_threshold", 3.0)
        self.iqr_factor = self.config.get("iqr_factor", 1.5)

        # 时间序列检测参数
        self.window_size = self.config.get("window_size", 50)
        self.trend_threshold = self.config.get("trend_threshold", 2.0)

        # 检测历史
        self.detection_history: list[AnomalyDetectionResult] = []
        self.feature_stats: dict[str, dict[str, float]] = {}

        # 异常模式库
        self.anomaly_patterns = self._load_anomaly_patterns()

        self.logger.info("AnomalyDetector initialized")

    def train(
        self,
        X: np.ndarray,
        y: np.ndarray | None = None,
        validation_data: tuple[np.ndarray, np.ndarray] | None = None,
    ) -> dict[str, Any]:
        """训练异常检测模型

        Args:
            X: 特征矩阵 (异常检测通常无监督，y可选)
            y: 目标变量 (可选)
            validation_data: 验证数据

        Returns:
            Dict[str, Any]: 训练结果
        """
        start_time = time.time()

        if not self._validate_input(X):
            return {"error": "Invalid input data"}

        try:
            # 预处理数据
            X_processed = self._preprocess_features(X)

            # 训练Isolation Forest
            self.isolation_forest.fit(X_processed)

            # 训练One-Class SVM (大数据集时可能较慢)
            if len(X_processed) < 10000:  # 限制数据量以提高性能
                self.one_class_svm.fit(X_processed)

            # 计算特征统计
            self._compute_feature_statistics(X_processed)

            # 更新模型状态
            self.is_trained = True
            self.training_time = time.time() - start_time
            self.last_training_time = int(time.time())
            self.metrics["training_samples"] = len(X_processed)

            # 记录训练历史
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
        """检测异常

        Args:
            X: 输入特征 (数组或字典)
            method: 检测方法 ("isolation_forest", "one_class_svm", "statistical", "ensemble")

        Returns:
            AnomalyDetectionResult: 检测结果
        """
        try:
            # 转换输入为特征向量
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

            # 预处理
            X_processed = self._preprocess_features(X_vector)

            # 根据方法检测异常
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

            # 记录检测历史
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
        """预测异常标签

        Args:
            X: 特征矩阵

        Returns:
            np.ndarray: 异常标签 (1为正常, -1为异常)
        """
        if not self.is_trained:
            raise ValueError("Model not trained")

        X_processed = self._preprocess_features(X)

        if self.use_ensemble:
            # 使用集成预测
            if_pred = self.isolation_forest.predict(X_processed)
            svm_pred = (
                self.one_class_svm.predict(X_processed) if len(X_processed) < 10000 else if_pred
            )

            # 投票机制
            predictions = []
            for i in range(len(X_processed)):
                votes = [if_pred[i], svm_pred[i]]
                prediction = 1 if sum(votes) > 0 else -1
                predictions.append(prediction)

            return np.array(predictions)
        else:
            return self.isolation_forest.predict(X_processed)  # type: ignore[no-any-return]

    def predict_proba(self, X: np.ndarray) -> np.ndarray:
        """预测异常概率

        Args:
            X: 特征矩阵

        Returns:
            np.ndarray: 异常概率
        """
        if not self.is_trained:
            raise ValueError("Model not trained")

        X_processed = self._preprocess_features(X)

        # 使用decision_function作为异常分数
        if self.use_ensemble:
            if_scores = self.isolation_forest.decision_function(X_processed)
            svm_scores = (
                self.one_class_svm.decision_function(X_processed)
                if len(X_processed) < 10000
                else if_scores
            )

            # 平均分数
            ensemble_scores = (if_scores + svm_scores) / 2
        else:
            ensemble_scores = cast(
                "np.ndarray", self.isolation_forest.decision_function(X_processed)
            )

        # 转换为概率 (使用sigmoid函数)
        probabilities = 1 / (1 + np.exp(-ensemble_scores))

        # 返回异常概率 (1 - 正常概率)
        return 1 - probabilities.reshape(-1, 1)  # type: ignore[no-any-return]

    def detect_trading_anomalies(
        self, trading_data: dict[str, Any]
    ) -> list[AnomalyDetectionResult]:
        """检测交易异常

        Args:
            trading_data: 交易数据

        Returns:
            List[AnomalyDetectionResult]: 检测到的异常列表
        """
        anomalies = []

        # 提取交易特征
        features = self._extract_trading_features(trading_data)

        # 检测不同类型的异常
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
        """检测市场异常

        Args:
            market_data: 市场数据

        Returns:
            List[AnomalyDetectionResult]: 检测到的异常列表
        """
        anomalies = []

        # 提取市场特征
        features = self._extract_market_features(market_data)

        # 检测市场异常
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
        """检测操作异常

        Args:
            operational_data: 操作数据

        Returns:
            List[AnomalyDetectionResult]: 检测到的异常列表
        """
        anomalies = []

        # 提取操作特征
        features = self._extract_operational_features(operational_data)

        # 检测操作异常
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

    # 私有方法

    def _detect_with_isolation_forest(
        self, X: np.ndarray, feature_names: list[str]
    ) -> AnomalyDetectionResult:
        """使用Isolation Forest检测异常"""
        prediction = self.isolation_forest.predict(X)[0]
        score = self.isolation_forest.decision_function(X)[0]

        is_anomaly = prediction == -1
        anomaly_score = abs(score)

        # 确定异常类型和严重程度
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
        """使用One-Class SVM检测异常"""
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
        """使用统计方法检测异常"""
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
        """使用集成方法检测异常"""
        # 获取各个检测器的结果
        if_result = self._detect_with_isolation_forest(X, feature_names)
        svm_result = self._detect_with_one_class_svm(X, feature_names)
        stat_result = self._detect_statistical(X, feature_names)

        # 投票机制
        votes = [if_result.is_anomaly, svm_result.is_anomaly, stat_result.is_anomaly]
        vote_count = sum(votes)

        is_anomaly = vote_count >= 2  # 至少2个检测器认为是异常

        # 集成分数
        ensemble_score = (
            if_result.anomaly_score * 0.4
            + svm_result.anomaly_score * 0.3
            + stat_result.anomaly_score * 0.3
        )

        # 综合解释
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
        """分类异常类型和严重程度"""
        if not is_anomaly:
            return None, AnomalySeverity.LOW

        # 根据特征和分数分类
        if score > 0.8:
            severity = AnomalySeverity.CRITICAL
        elif score > 0.6:
            severity = AnomalySeverity.HIGH
        elif score > 0.4:
            severity = AnomalySeverity.MEDIUM
        else:
            severity = AnomalySeverity.LOW

        # 简化的异常类型判断
        anomaly_type = "general_anomaly"

        return anomaly_type, severity

    def _generate_explanation(
        self, features: np.ndarray, feature_names: list[str], is_anomaly: bool, score: float
    ) -> str:
        """生成异常解释"""
        if not is_anomaly:
            return "No anomaly detected"

        # 找出贡献最大的特征
        if len(feature_names) == len(features):
            feature_contributions = [
                (name, abs(value)) for name, value in zip(feature_names, features, strict=False)
            ]
            feature_contributions.sort(key=lambda x: x[1], reverse=True)

            top_features = feature_contributions[:3]
            feature_explanation = ", ".join(
                [f"{name}: {value:.3f}" for name, value in top_features]
            )

            return f"Anomaly detected. Score: {score:.3f}. Key features: {feature_explanation}"

        return f"Anomaly detected with score: {score:.3f}"

    def _extract_trading_features(self, trading_data: dict[str, Any]) -> np.ndarray:
        """提取交易特征"""
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
        """提取市场特征"""
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
        """提取操作特征"""
        features = [
            operational_data.get("response_time", 0),
            operational_data.get("error_rate", 0),
            operational_data.get("cpu_usage", 0),
            operational_data.get("memory_usage", 0),
            operational_data.get("request_rate", 0),
            operational_data.get("timeout_rate", 0),
        ]
        return np.array(features)

    def _detect_volume_anomaly(
        self, trading_data: dict[str, Any], features: np.ndarray
    ) -> AnomalyDetectionResult | None:
        """检测成交量异常"""
        volume = trading_data.get("volume", 0)
        avg_volume = trading_data.get("avg_volume_30d", 1)

        if avg_volume > 0:
            volume_ratio = volume / avg_volume
            if volume_ratio > 5.0:  # 成交量超过平均值5倍
                return AnomalyDetectionResult(
                    is_anomaly=True,
                    anomaly_score=min(volume_ratio / 10, 1.0),
                    anomaly_type=AnomalyType.UNUSUAL_VOLUME,
                    severity=AnomalySeverity.HIGH if volume_ratio > 10 else AnomalySeverity.MEDIUM,
                    confidence=min(volume_ratio / 5, 1.0),
                    explanation=f"Unusual trading volume: {volume:.2f} ({volume_ratio:.1f}x average)",
                    timestamp=int(time.time()),
                    features_used=["volume", "avg_volume_30d"],
                )
        return None

    def _detect_price_anomaly(
        self, trading_data: dict[str, Any], features: np.ndarray
    ) -> AnomalyDetectionResult | None:
        """检测价格异常"""
        price_change = abs(trading_data.get("price_change", 0))

        if price_change > 0.1:  # 价格变化超过10%
            return AnomalyDetectionResult(
                is_anomaly=True,
                anomaly_score=min(price_change * 2, 1.0),
                anomaly_type=AnomalyType.RAPID_PRICE_CHANGE,
                severity=AnomalySeverity.CRITICAL if price_change > 0.2 else AnomalySeverity.HIGH,
                confidence=min(price_change * 5, 1.0),
                explanation=f"Rapid price change detected: {price_change:.2%}",
                timestamp=int(time.time()),
                features_used=["price_change"],
            )
        return None

    def _detect_pattern_anomaly(
        self, trading_data: dict[str, Any], features: np.ndarray
    ) -> AnomalyDetectionResult | None:
        """检测模式异常"""
        cancel_rate = trading_data.get("cancel_rate", 0)

        if cancel_rate > 0.8:  # 撤单率超过80%
            return AnomalyDetectionResult(
                is_anomaly=True,
                anomaly_score=cancel_rate,
                anomaly_type=AnomalyType.SUSPICIOUS_ORDER_PATTERN,
                severity=AnomalySeverity.HIGH,
                confidence=cancel_rate,
                explanation=f"Suspicious order pattern: high cancel rate {cancel_rate:.1%}",
                timestamp=int(time.time()),
                features_used=["cancel_rate"],
            )
        return None

    def _detect_timing_anomaly(
        self, trading_data: dict[str, Any], features: np.ndarray
    ) -> AnomalyDetectionResult | None:
        """检测时间异常"""
        time_between_orders = trading_data.get("time_between_orders", 0)

        if time_between_orders < 0.001:  # 订单间隔小于1毫秒
            return AnomalyDetectionResult(
                is_anomaly=True,
                anomaly_score=1.0 - time_between_orders * 1000,
                anomaly_type=AnomalyType.COORDINATED_TRADING,
                severity=AnomalySeverity.HIGH,
                confidence=0.8,
                explanation=f"Suspicious timing: orders placed {time_between_orders * 1000:.1f}ms apart",
                timestamp=int(time.time()),
                features_used=["time_between_orders"],
            )
        return None

    def _detect_volatility_anomaly(
        self, market_data: dict[str, Any], features: np.ndarray
    ) -> AnomalyDetectionResult | None:
        """检测波动率异常"""
        volatility = market_data.get("volatility", 0)
        avg_volatility = market_data.get("avg_volatility_30d", 0.02)

        if avg_volatility > 0:
            volatility_ratio = volatility / avg_volatility
            if volatility_ratio > 3.0:  # 波动率超过平均值3倍
                return AnomalyDetectionResult(
                    is_anomaly=True,
                    anomaly_score=min(volatility_ratio / 5, 1.0),
                    anomaly_type=AnomalyType.VOLATILITY_SPIKE,
                    severity=AnomalySeverity.CRITICAL
                    if volatility_ratio > 5
                    else AnomalySeverity.HIGH,
                    confidence=min(volatility_ratio / 3, 1.0),
                    explanation=f"Volatility spike: {volatility:.3f} ({volatility_ratio:.1f}x average)",
                    timestamp=int(time.time()),
                    features_used=["volatility", "avg_volatility_30d"],
                )
        return None

    def _detect_liquidity_anomaly(
        self, market_data: dict[str, Any], features: np.ndarray
    ) -> AnomalyDetectionResult | None:
        """检测流动性异常"""
        bid_ask_spread = market_data.get("bid_ask_spread", 0)
        market_depth = market_data.get("market_depth", 1000000)

        # 价差过大或深度过小
        spread_anomaly = bid_ask_spread > 100  # 超过100 bps
        depth_anomaly = market_depth < 100000  # 深度小于10万

        if spread_anomaly or depth_anomaly:
            return AnomalyDetectionResult(
                is_anomaly=True,
                anomaly_score=0.8,
                anomaly_type=AnomalyType.LIQUIDITY_CRISIS,
                severity=AnomalySeverity.HIGH,
                confidence=0.7,
                explanation=f"Liquidity issues detected - spread: {bid_ask_spread:.1f} bps, depth: {market_depth:,.0f}",
                timestamp=int(time.time()),
                features_used=["bid_ask_spread", "market_depth"],
            )
        return None

    def _detect_correlation_anomaly(
        self, market_data: dict[str, Any], features: np.ndarray
    ) -> AnomalyDetectionResult | None:
        """检测相关性异常"""
        correlation_breakdown = market_data.get("correlation_breakdown", 0)

        if correlation_breakdown > 0.5:  # 相关性失效超过50%
            return AnomalyDetectionResult(
                is_anomaly=True,
                anomaly_score=correlation_breakdown,
                anomaly_type=AnomalyType.CORRELATION_BREAKDOWN,
                severity=AnomalySeverity.HIGH,
                confidence=correlation_breakdown,
                explanation=f"Correlation breakdown detected: {correlation_breakdown:.1%}",
                timestamp=int(time.time()),
                features_used=["correlation_breakdown"],
            )
        return None

    def _detect_performance_anomaly(
        self, operational_data: dict[str, Any], features: np.ndarray
    ) -> AnomalyDetectionResult | None:
        """检测性能异常"""
        response_time = operational_data.get("response_time", 0)

        if response_time > 1000:  # 响应时间超过1秒
            return AnomalyDetectionResult(
                is_anomaly=True,
                anomaly_score=min(response_time / 5000, 1.0),
                anomaly_type=AnomalyType.SYSTEM_PERFORMANCE_DEGRADATION,
                severity=AnomalySeverity.HIGH if response_time > 5000 else AnomalySeverity.MEDIUM,
                confidence=min(response_time / 2000, 1.0),
                explanation=f"Slow response time: {response_time:.0f}ms",
                timestamp=int(time.time()),
                features_used=["response_time"],
            )
        return None

    def _detect_error_anomaly(
        self, operational_data: dict[str, Any], features: np.ndarray
    ) -> AnomalyDetectionResult | None:
        """检测错误异常"""
        error_rate = operational_data.get("error_rate", 0)

        if error_rate > 0.05:  # 错误率超过5%
            return AnomalyDetectionResult(
                is_anomaly=True,
                anomaly_score=min(error_rate * 10, 1.0),
                anomaly_type=AnomalyType.ERROR_RATE_SPIKE,
                severity=AnomalySeverity.CRITICAL if error_rate > 0.1 else AnomalySeverity.HIGH,
                confidence=min(error_rate * 15, 1.0),
                explanation=f"High error rate: {error_rate:.1%}",
                timestamp=int(time.time()),
                features_used=["error_rate"],
            )
        return None

    def _detect_access_anomaly(
        self, operational_data: dict[str, Any], features: np.ndarray
    ) -> AnomalyDetectionResult | None:
        """检测访问异常"""
        unauthorized_attempts = operational_data.get("unauthorized_attempts", 0)

        if unauthorized_attempts > 10:  # 未授权访问尝试超过10次
            return AnomalyDetectionResult(
                is_anomaly=True,
                anomaly_score=min(unauthorized_attempts / 50, 1.0),
                anomaly_type=AnomalyType.UNAUTHORIZED_ACCESS,
                severity=AnomalySeverity.CRITICAL,
                confidence=min(unauthorized_attempts / 20, 1.0),
                explanation=f"Multiple unauthorized access attempts: {unauthorized_attempts}",
                timestamp=int(time.time()),
                features_used=["unauthorized_attempts"],
            )
        return None

    def _compute_feature_statistics(self, X: np.ndarray) -> None:
        """计算特征统计"""
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
        """将字典转换为特征向量"""
        if not self.feature_names:
            self.feature_names = list(data.keys())

        features = [float(data.get(name, 0)) for name in self.feature_names]
        return np.array(features).reshape(1, -1)

    def _load_anomaly_patterns(self) -> dict[str, Any]:
        """加载异常模式库"""
        # 简化的异常模式库
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
        """获取异常统计信息"""
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
