"""操作风险计算（系统健康/延迟/错误率/可用性/数据质量）。"""

from __future__ import annotations

import statistics
from decimal import Decimal
from typing import Any

import numpy as np

from ..containers.risk_metrics import LatencyMetrics, OperationalRiskMetrics


class OperationalRiskMixin:
    """操作风险计算方法（供 RiskCalculator 混入）。"""

    def _calculate_operational_risk(self, account_data: dict[str, Any]) -> OperationalRiskMetrics:
        """"""

        # 
        system_health_score = self._calculate_system_health_score(account_data)

        # 
        latency_metrics = self._calculate_latency_metrics(account_data)

        # 
        error_rate = self._calculate_error_rate(account_data)

        # 
        system_availability = self._calculate_system_availability(account_data)

        # 
        data_quality_score = self._calculate_data_quality_score(account_data)

        # 
        processing_capacity = self._calculate_processing_capacity(account_data)

        # 
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
                "incident_history": [],  # 
            }
        )

    def _calculate_system_health_score(self, account_data: dict[str, Any]) -> Decimal:
        """"""
        # 
        cpu_usage = account_data.get("cpu_usage", 0.5)
        memory_usage = account_data.get("memory_usage", 0.5)
        disk_usage = account_data.get("disk_usage", 0.3)
        error_rate = account_data.get("error_rate", 0.01)

        # 
        health_score = 1.0 - (
            cpu_usage * 0.3 + memory_usage * 0.3 + disk_usage * 0.2 + error_rate * 0.2
        )
        return Decimal(str(max(0, min(health_score, 1.0))))

    def _calculate_latency_metrics(self, account_data: dict[str, Any]) -> LatencyMetrics:
        """"""
        latency_data = account_data.get("latency_history", [100, 150, 120, 80, 200])

        if not latency_data:
            return LatencyMetrics({})

        avg_latency = statistics.mean(latency_data)
        p95_latency = np.percentile(latency_data, 95)
        p99_latency = np.percentile(latency_data, 99)
        max_latency = max(latency_data)

        # SLA (SLA200ms)
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
        """"""
        total_requests = account_data.get("total_requests", 1000)
        error_count = account_data.get("error_count", 10)
        error_rate = error_count / total_requests if total_requests > 0 else 0
        return Decimal(str(error_rate))

    def _calculate_system_availability(self, account_data: dict[str, Any]) -> Decimal:
        """"""
        uptime_seconds = account_data.get("uptime_seconds", 86400)  # 24
        downtime_seconds = account_data.get("downtime_seconds", 3600)  # 1
        total_time = uptime_seconds + downtime_seconds

        availability = uptime_seconds / total_time if total_time > 0 else 0
        return Decimal(str(availability))

    def _calculate_data_quality_score(self, account_data: dict[str, Any]) -> Decimal:
        """"""
        completeness = account_data.get("data_completeness", 0.95)
        accuracy = account_data.get("data_accuracy", 0.98)
        timeliness = account_data.get("data_timeliness", 0.90)
        consistency = account_data.get("data_consistency", 0.92)

        quality_score = (completeness + accuracy + timeliness + consistency) / 4
        return Decimal(str(quality_score))

    def _calculate_processing_capacity(self, account_data: dict[str, Any]) -> Decimal:
        """"""
        current_load = account_data.get("current_load", 0.6)
        max_capacity = 1.0
        capacity_utilization = current_load / max_capacity
        return Decimal(str(capacity_utilization))

    def _calculate_vulnerability_score(self, account_data: dict[str, Any]) -> Decimal:
        """"""
        critical_vulns = account_data.get("critical_vulnerabilities", 0)
        high_vulns = account_data.get("high_vulnerabilities", 1)
        medium_vulns = account_data.get("medium_vulnerabilities", 3)
        low_vulns = account_data.get("low_vulnerabilities", 5)

        # 
        vuln_score = (critical_vulns * 10 + high_vulns * 5 + medium_vulns * 2 + low_vulns * 1) / 100
        return Decimal(str(min(vuln_score, 1.0)))
