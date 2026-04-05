"""Real-time security monitoring and SIEM integration.

Provides comprehensive security monitoring, alerting, and integration
with SIEM systems for financial industry compliance.
"""

from __future__ import annotations

import contextlib
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from enum import Enum
from typing import Any


class AlertSeverity(Enum):
    """Security alert severity levels."""

    INFO = "info"
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"
    CRITICAL = "critical"


@dataclass
class SecurityAlert:
    """Security alert event."""

    alert_id: str
    title: str
    description: str
    severity: AlertSeverity
    source: str
    timestamp: float = field(default_factory=time.time)
    details: dict[str, Any] = field(default_factory=dict)
    acknowledged: bool = False
    resolved: bool = False


class SecurityMonitoring:
    """Security monitoring and alerting system."""

    def __init__(self, audit_logger, config: dict[str, Any] | None = None):
        """Initialize security monitoring."""
        self.audit_logger = audit_logger
        self.config = config or {}

        self._alerts: list[SecurityAlert] = []
        self._alert_handlers: list[Callable] = []
        self._thresholds = self.config.get("alert_thresholds", {})

    def create_alert(
        self, title: str, description: str, severity: AlertSeverity, source: str, **details
    ) -> SecurityAlert:
        """Create a security alert."""
        alert = SecurityAlert(
            alert_id=f"sec_{int(time.time())}_{len(self._alerts)}",
            title=title,
            description=description,
            severity=severity,
            source=source,
            details=details,
        )

        self._alerts.append(alert)

        # Log to audit system
        if self.audit_logger:
            from .audit_logger import EventType, SeverityLevel

            self.audit_logger.log_event(
                EventType.SECURITY_INCIDENT,
                user_id="system",
                severity=SeverityLevel.HIGH,
                resource="security_monitoring",
                action="alert_created",
                details=alert.to_dict()
                if hasattr(alert, "to_dict")
                else {
                    "alert_id": alert.alert_id,
                    "title": alert.title,
                    "severity": alert.severity.value,
                },
            )

        # Notify handlers
        for handler in self._alert_handlers:
            # Don't let handler errors break monitoring
            with contextlib.suppress(Exception):
                handler(alert)

        return alert

    def get_alerts(
        self,
        severity: AlertSeverity | None = None,
        acknowledged: bool | None = None,
        limit: int = 100,
    ) -> list[SecurityAlert]:
        """Get filtered alerts."""
        filtered = self._alerts

        if severity:
            filtered = [a for a in filtered if a.severity == severity]

        if acknowledged is not None:
            filtered = [a for a in filtered if a.acknowledged == acknowledged]

        # Sort by timestamp (newest first)
        filtered.sort(key=lambda a: a.timestamp, reverse=True)
        return filtered[:limit]

    def acknowledge_alert(self, alert_id: str) -> bool:
        """Acknowledge a security alert."""
        for alert in self._alerts:
            if alert.alert_id == alert_id:
                alert.acknowledged = True
                return True
        return False

    def resolve_alert(self, alert_id: str) -> bool:
        """Resolve a security alert."""
        for alert in self._alerts:
            if alert.alert_id == alert_id:
                alert.resolved = True
                return True
        return False

    def add_alert_handler(self, handler: Callable) -> None:
        """Add custom alert handler."""
        self._alert_handlers.append(handler)

    def get_monitoring_summary(self) -> dict[str, Any]:
        """Get monitoring summary statistics."""
        total = len(self._alerts)
        unacknowledged = len([a for a in self._alerts if not a.acknowledged])
        critical = len([a for a in self._alerts if a.severity == AlertSeverity.CRITICAL])
        high = len([a for a in self._alerts if a.severity == AlertSeverity.HIGH])

        return {
            "total_alerts": total,
            "unacknowledged": unacknowledged,
            "critical": critical,
            "high": high,
            "recent_24h": len([a for a in self._alerts if time.time() - a.timestamp < 86400]),
        }
