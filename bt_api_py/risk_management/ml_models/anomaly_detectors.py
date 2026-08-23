"""规则型异常检测器集合（成交量/价格/模式/时机/波动/流动性/相关性/性能/错误/访问）。"""

from __future__ import annotations

import time
from typing import Any

import numpy as np  # noqa: TC002 (runtime use in anomaly detectors)

from .anomaly_types import AnomalyDetectionResult, AnomalySeverity, AnomalyType


class AnomalyDetectorsMixin:
    """规则型异常检测方法（供 AnomalyDetector 混入）。"""

    def _detect_volume_anomaly(
        self, trading_data: dict[str, Any], features: np.ndarray
    ) -> AnomalyDetectionResult | None:
        """"""
        volume = trading_data.get("volume", 0)
        avg_volume = trading_data.get("avg_volume_30d", 1)

        if avg_volume > 0:
            volume_ratio = volume / avg_volume
            if volume_ratio > 5.0:  # 5
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
        """"""
        price_change = abs(trading_data.get("price_change", 0))

        if price_change > 0.1:  # 10%
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
        """"""
        cancel_rate = trading_data.get("cancel_rate", 0)

        if cancel_rate > 0.8:  # 80%
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
        """"""
        time_between_orders = trading_data.get("time_between_orders", 0)

        if time_between_orders < 0.001:  # 1
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
        """"""
        volatility = market_data.get("volatility", 0)
        avg_volatility = market_data.get("avg_volatility_30d", 0.02)

        if avg_volatility > 0:
            volatility_ratio = volatility / avg_volatility
            if volatility_ratio > 3.0:  # 3
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
        """"""
        bid_ask_spread = market_data.get("bid_ask_spread", 0)
        market_depth = market_data.get("market_depth", 1000000)

        #
        spread_anomaly = bid_ask_spread > 100  # 100 bps
        depth_anomaly = market_depth < 100000  # 10

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
        """"""
        correlation_breakdown = market_data.get("correlation_breakdown", 0)

        if correlation_breakdown > 0.5:  # 50%
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
        """"""
        response_time = operational_data.get("response_time", 0)

        if response_time > 1000:  # 1
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
        """"""
        error_rate = operational_data.get("error_rate", 0)

        if error_rate > 0.05:  # 5%
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
        """"""
        unauthorized_attempts = operational_data.get("unauthorized_attempts", 0)

        if unauthorized_attempts > 10:  # 10
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
