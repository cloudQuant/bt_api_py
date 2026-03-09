"""Threat detection and intrusion prevention system.

Real-time security monitoring with anomaly detection, behavioral analysis,
and automated threat response for financial systems.
"""

import time
from collections import defaultdict, deque
from dataclasses import dataclass, field
from enum import Enum
from typing import Any


class ThreatLevel(Enum):
    """Threat severity levels."""

    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"
    CRITICAL = "critical"


class ThreatType(Enum):
    """Types of security threats."""

    BRUTE_FORCE = "brute_force"
    SUSPICIOUS_LOGIN = "suspicious_login"
    ANOMALOUS_TRAFFIC = "anomalous_traffic"
    DATA_EXFILTRATION = "data_exfiltration"
    UNAUTHORIZED_ACCESS = "unauthorized_access"
    MALICIOUS_REQUEST = "malicious_request"
    SQL_INJECTION = "sql_injection"
    XSS_ATTACK = "xss_attack"


@dataclass
class ThreatEvent:
    """Security threat event."""

    event_id: str
    threat_type: ThreatType
    level: ThreatLevel
    user_id: str | None
    ip_address: str | None
    details: dict[str, Any]
    timestamp: float = field(default_factory=time.time)
    resolved: bool = False


class ThreatDetector:
    """Advanced threat detection system."""

    def __init__(self, config: dict[str, Any] | None = None) -> None:
        """Initialize threat detector."""
        self.config = config or {}
        self.thresholds = self.config.get(
            "thresholds",
            {
                "failed_login_threshold": 5,
                "suspicious_ip_threshold": 10,
                "data_access_threshold": 1000,
            },
        )

        # Event tracking
        self._failed_logins: dict[str, deque] = defaultdict(lambda: deque(maxlen=100))
        self._suspicious_ips: dict[str, int] = defaultdict(int)
        self._data_access_patterns: dict[str, list[float]] = defaultdict(list)
        self._threat_events: list[ThreatEvent] = []

        # Anomaly detection
        self._baseline_patterns: dict[str, Any] = {}

    def detect_failed_login(self, user_id: str, ip_address: str) -> ThreatEvent | None:
        """Detect brute force or suspicious login attempts."""
        current_time = time.time()
        key = f"{user_id}:{ip_address}"

        # Track failed attempts
        self._failed_logins[key].append(current_time)

        # Count recent failures (last 5 minutes)
        recent_failures = sum(
            1
            for t in self._failed_logins[key]
            if current_time - t < 300  # 5 minutes
        )

        if recent_failures >= self.thresholds["failed_login_threshold"]:
            # Check for pattern across different users/IPs
            ip_failures = sum(
                1
                for other_key in self._failed_logins
                if other_key.endswith(f":{ip_address}")
                and any(current_time - t < 300 for t in self._failed_logins[other_key])
            )

            threat_level = ThreatLevel.HIGH if ip_failures > 10 else ThreatLevel.MEDIUM

            threat_event = ThreatEvent(
                event_id=f"brute_force_{int(current_time)}",
                threat_type=ThreatType.BRUTE_FORCE,
                level=threat_level,
                user_id=user_id,
                ip_address=ip_address,
                details={
                    "failed_attempts": recent_failures,
                    "total_ip_failures": ip_failures,
                    "time_window": "5_minutes",
                },
            )

            self._threat_events.append(threat_event)
            return threat_event

        return None

    def detect_suspicious_access_pattern(
        self, user_id: str, resource: str, ip_address: str
    ) -> ThreatEvent | None:
        """Detect anomalous access patterns."""
        current_time = time.time()

        # Track data access
        self._data_access_patterns[user_id].append(current_time)

        # Clean old entries (older than 1 hour)
        self._data_access_patterns[user_id] = [
            t for t in self._data_access_patterns[user_id] if current_time - t < 3600
        ]

        # Check for rapid access
        recent_access = len(self._data_access_patterns[user_id])

        if recent_access > self.thresholds["data_access_threshold"]:
            threat_event = ThreatEvent(
                event_id=f"anomalous_access_{int(current_time)}",
                threat_type=ThreatType.ANOMALOUS_TRAFFIC,
                level=ThreatLevel.MEDIUM,
                user_id=user_id,
                ip_address=ip_address,
                details={
                    "access_count": recent_access,
                    "resource": resource,
                    "time_window": "1_hour",
                },
            )

            self._threat_events.append(threat_event)
            return threat_event

        return None

    def detect_unauthorized_access_attempt(
        self, user_id: str, resource: str, ip_address: str, details: dict[str, Any] | None = None
    ) -> ThreatEvent:
        """Detect unauthorized access attempts."""
        current_time = time.time()

        # Track suspicious IPs
        self._suspicious_ips[ip_address] += 1

        # Determine threat level
        threat_level = ThreatLevel.HIGH
        if self._suspicious_ips[ip_address] > self.thresholds["suspicious_ip_threshold"]:
            threat_level = ThreatLevel.CRITICAL

        threat_event = ThreatEvent(
            event_id=f"unauthorized_{int(current_time)}",
            threat_type=ThreatType.UNAUTHORIZED_ACCESS,
            level=threat_level,
            user_id=user_id,
            ip_address=ip_address,
            details={
                "resource": resource,
                "suspicious_ip_count": self._suspicious_ips[ip_address],
                **(details or {}),
            },
        )

        self._threat_events.append(threat_event)
        return threat_event

    def analyze_login_anomaly(
        self, user_id: str, ip_address: str, user_agent: str
    ) -> ThreatEvent | None:
        """Analyze login patterns for anomalies."""
        # In a real implementation, this would use ML models
        # For now, basic heuristic checks

        current_time = time.time()

        # Check for unusual login time (e.g., 3 AM)
        from datetime import datetime

        hour = datetime.fromtimestamp(current_time).hour

        if hour < 6 or hour > 22:  # Unusual hours
            threat_event = ThreatEvent(
                event_id=f"suspicious_login_{int(current_time)}",
                threat_type=ThreatType.SUSPICIOUS_LOGIN,
                level=ThreatLevel.MEDIUM,
                user_id=user_id,
                ip_address=ip_address,
                details={
                    "login_hour": hour,
                    "user_agent": user_agent,
                    "reason": "unusual_login_time",
                },
            )

            self._threat_events.append(threat_event)
            return threat_event

        return None

    def detect_data_exfiltration(
        self, user_id: str, data_size: int, resource: str
    ) -> ThreatEvent | None:
        """Detect potential data exfiltration."""
        # Threshold for large data transfers
        large_data_threshold = 100 * 1024 * 1024  # 100MB

        if data_size > large_data_threshold:
            threat_event = ThreatEvent(
                event_id=f"data_exfil_{int(time.time())}",
                threat_type=ThreatType.DATA_EXFILTRATION,
                level=ThreatLevel.HIGH,
                user_id=user_id,
                ip_address=None,
                details={
                    "data_size": data_size,
                    "resource": resource,
                    "threshold": large_data_threshold,
                },
            )

            self._threat_events.append(threat_event)
            return threat_event

        return None

    def get_threat_summary(self, time_window: int = 3600) -> dict[str, Any]:
        """Get threat summary for specified time window."""
        current_time = time.time()
        cutoff_time = current_time - time_window

        recent_threats = [
            threat for threat in self._threat_events if threat.timestamp >= cutoff_time
        ]

        # Group by type and level
        threat_counts = defaultdict(int)
        level_counts = defaultdict(int)

        for threat in recent_threats:
            threat_counts[threat.threat_type.value] += 1
            level_counts[threat.level.value] += 1

        return {
            "total_threats": len(recent_threats),
            "time_window_seconds": time_window,
            "by_type": dict(threat_counts),
            "by_level": dict(level_counts),
            "unresolved": len([t for t in recent_threats if not t.resolved]),
            "recent_events": [
                {
                    "event_id": threat.event_id,
                    "type": threat.threat_type.value,
                    "level": threat.level.value,
                    "user_id": threat.user_id,
                    "timestamp": threat.timestamp,
                }
                for threat in recent_threats[-10:]  # Last 10 events
            ],
        }

    def resolve_threat(self, event_id: str, resolution_note: str) -> bool:
        """Mark a threat as resolved."""
        for threat in self._threat_events:
            if threat.event_id == event_id:
                threat.resolved = True
                threat.details["resolution_note"] = resolution_note
                threat.details["resolved_at"] = time.time()
                return True
        return False

    def auto_respond_to_threat(self, threat_event: ThreatEvent) -> dict[str, Any]:
        """Automated threat response actions."""
        response_actions = []

        # High/Critical threats get immediate response
        if threat_event.level in [ThreatLevel.HIGH, ThreatLevel.CRITICAL]:
            if threat_event.ip_address:
                # Block IP address (would integrate with firewall)
                response_actions.append(
                    {
                        "action": "block_ip",
                        "ip_address": threat_event.ip_address,
                        "duration": "24_hours",
                    }
                )

            if threat_event.user_id:
                # Lock user account
                response_actions.append(
                    {
                        "action": "lock_user",
                        "user_id": threat_event.user_id,
                        "reason": f"Threat detected: {threat_event.threat_type.value}",
                    }
                )

                # Send security alert
                response_actions.append(
                    {
                        "action": "send_alert",
                        "type": "security_team",
                        "message": f"Critical security threat detected: {threat_event.threat_type.value}",
                    }
                )

        elif threat_event.level == ThreatLevel.MEDIUM:
            # Medium threats get monitoring
            response_actions.append(
                {
                    "action": "enhanced_monitoring",
                    "user_id": threat_event.user_id,
                    "ip_address": threat_event.ip_address,
                }
            )

        return {
            "threat_id": threat_event.event_id,
            "response_actions": response_actions,
            "timestamp": time.time(),
        }

    def establish_baseline(self, user_id: str, patterns: dict[str, Any]) -> None:
        """Establish baseline behavioral patterns."""
        self._baseline_patterns[user_id] = {
            "normal_login_hours": patterns.get("login_hours", []),
            "usual_ip_addresses": patterns.get("ip_addresses", []),
            "average_data_access": patterns.get("data_access_rate", 0),
            "created_at": time.time(),
        }

    def is_baseline_anomaly(self, user_id: str, current_behavior: dict[str, Any]) -> bool:
        """Check if current behavior deviates from baseline."""
        baseline = self._baseline_patterns.get(user_id)
        if not baseline:
            return False

        # Check login time anomaly
        current_hour = current_behavior.get("hour")
        normal_hours = baseline.get("normal_login_hours", [])

        if current_hour and normal_hours and current_hour not in normal_hours:
            return True

        # Check IP address anomaly
        current_ip = current_behavior.get("ip_address")
        usual_ips = baseline.get("usual_ip_addresses", [])

        if current_ip and usual_ips and current_ip not in usual_ips:
            return True

        return False
