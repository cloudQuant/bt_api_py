"""异常检测类型与结果容器定义。"""

from __future__ import annotations

from typing import Any


class AnomalyType:
    """"""

    # 
    UNUSUAL_VOLUME = "unusual_volume"  # 
    RAPID_PRICE_CHANGE = "rapid_price_change"  # 
    SUSPICIOUS_ORDER_PATTERN = "suspicious_order_pattern"  # 
    COORDINATED_TRADING = "coordinated_trading"  # 
    FRONT_RUNNING = "front_running"  # 
    SPOOFING = "spoofing"  # 

    # 
    LIQUIDITY_CRISIS = "liquidity_crisis"  # 
    FLASH_CRASH = "flash_crash"  # 
    CORRELATION_BREAKDOWN = "correlation_breakdown"  # 
    VOLATILITY_SPIKE = "volatility_spike"  # 
    MARKET_MANIPULATION = "market_manipulation"  # 

    # 
    SYSTEM_PERFORMANCE_DEGRADATION = "system_performance_degradation"  # 
    UNAUTHORIZED_ACCESS = "unauthorized_access"  # 
    DATA_ANOMALY = "data_anomaly"  # 
    TIMEOUT_ANOMALY = "timeout_anomaly"  # 
    ERROR_RATE_SPIKE = "error_rate_spike"  # 


class AnomalySeverity:
    """"""

    CRITICAL = "CRITICAL"  #  - 
    HIGH = "HIGH"  #  - 
    MEDIUM = "MEDIUM"  #  - 
    LOW = "LOW"  #  - 


class AnomalyDetectionResult:
    """"""

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
        """__init__ method"""
        self.is_anomaly = is_anomaly
        self.anomaly_score = anomaly_score
        self.anomaly_type = anomaly_type
        self.severity = severity
        self.confidence = confidence
        self.explanation = explanation
        self.timestamp = timestamp
        self.features_used = features_used

    def to_dict(self) -> dict[str, Any]:
        """"""
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
