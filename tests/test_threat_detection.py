from __future__ import annotations

import datetime as dt
import time

import bt_api_py.security_compliance.core.threat_detection as threat_module
from bt_api_py.security_compliance.core.threat_detection import (
    ThreatDetector,
    ThreatEvent,
    ThreatLevel,
    ThreatType,
)


def test_detect_failed_login_returns_none_below_threshold(monkeypatch) -> None:
    detector = ThreatDetector(
        {
            "thresholds": {
                "failed_login_threshold": 3,
                "suspicious_ip_threshold": 10,
                "data_access_threshold": 1000,
            }
        }
    )
    monkeypatch.setattr(threat_module.time, "time", lambda: 1000.0)

    assert detector.detect_failed_login("user1", "1.1.1.1") is None
    assert detector.detect_failed_login("user1", "1.1.1.1") is None


def test_detect_failed_login_escalates_to_medium_and_high(monkeypatch) -> None:
    detector = ThreatDetector(
        {
            "thresholds": {
                "failed_login_threshold": 1,
                "suspicious_ip_threshold": 10,
                "data_access_threshold": 1000,
            }
        }
    )
    monkeypatch.setattr(threat_module.time, "time", lambda: 2000.0)

    medium_event = detector.detect_failed_login("user1", "2.2.2.2")

    assert medium_event is not None
    assert medium_event.level == ThreatLevel.MEDIUM
    assert medium_event.details["total_ip_failures"] == 1

    for index in range(2, 13):
        detector._failed_logins[f"user{index}:2.2.2.2"].append(2000.0)

    high_event = detector.detect_failed_login("user13", "2.2.2.2")

    assert high_event is not None
    assert high_event.level == ThreatLevel.HIGH
    assert high_event.details["total_ip_failures"] > 10


def test_detect_suspicious_access_pattern_cleans_old_entries_and_triggers(monkeypatch) -> None:
    detector = ThreatDetector(
        {
            "thresholds": {
                "failed_login_threshold": 5,
                "suspicious_ip_threshold": 10,
                "data_access_threshold": 2,
            }
        }
    )
    detector._data_access_patterns["user1"] = [0.0]
    times = iter([4000.0, 4001.0, 4002.0])
    monkeypatch.setattr(threat_module.time, "time", lambda: next(times))

    assert detector.detect_suspicious_access_pattern("user1", "orders", "3.3.3.3") is None
    assert detector.detect_suspicious_access_pattern("user1", "orders", "3.3.3.3") is None
    event = detector.detect_suspicious_access_pattern("user1", "orders", "3.3.3.3")

    assert event is not None
    assert event.threat_type == ThreatType.ANOMALOUS_TRAFFIC
    assert event.details["access_count"] == 3
    assert 0.0 not in detector._data_access_patterns["user1"]


def test_detect_unauthorized_access_attempt_escalates_to_critical(monkeypatch) -> None:
    detector = ThreatDetector(
        {
            "thresholds": {
                "failed_login_threshold": 5,
                "suspicious_ip_threshold": 1,
                "data_access_threshold": 1000,
            }
        }
    )
    monkeypatch.setattr(threat_module.time, "time", lambda: 5000.0)

    high_event = detector.detect_unauthorized_access_attempt("user1", "admin", "4.4.4.4")
    critical_event = detector.detect_unauthorized_access_attempt(
        "user1", "admin", "4.4.4.4", {"reason": "repeat"}
    )

    assert high_event.level == ThreatLevel.HIGH
    assert critical_event.level == ThreatLevel.CRITICAL
    assert critical_event.details["suspicious_ip_count"] == 2
    assert critical_event.details["reason"] == "repeat"


def test_analyze_login_anomaly_and_data_exfiltration(monkeypatch) -> None:
    detector = ThreatDetector()
    suspicious_ts = time.mktime(dt.datetime(2024, 1, 1, 3, 0, 0).timetuple())
    normal_ts = time.mktime(dt.datetime(2024, 1, 1, 12, 0, 0).timetuple())

    monkeypatch.setattr(threat_module.time, "time", lambda: suspicious_ts)
    suspicious_login = detector.analyze_login_anomaly("user1", "5.5.5.5", "agent")

    monkeypatch.setattr(threat_module.time, "time", lambda: normal_ts)
    normal_login = detector.analyze_login_anomaly("user1", "5.5.5.5", "agent")

    exfiltration = detector.detect_data_exfiltration("user1", 101 * 1024 * 1024, "reports")
    no_exfiltration = detector.detect_data_exfiltration("user1", 1024, "reports")

    assert suspicious_login is not None
    assert suspicious_login.details["reason"] == "unusual_login_time"
    assert normal_login is None
    assert exfiltration is not None
    assert exfiltration.threat_type == ThreatType.DATA_EXFILTRATION
    assert no_exfiltration is None


def test_get_threat_summary_resolve_and_recent_events_limit(monkeypatch) -> None:
    detector = ThreatDetector()
    detector._threat_events = [
        ThreatEvent(
            event_id=f"event-{index}",
            threat_type=ThreatType.BRUTE_FORCE
            if index % 2 == 0
            else ThreatType.UNAUTHORIZED_ACCESS,
            level=ThreatLevel.HIGH if index % 3 == 0 else ThreatLevel.MEDIUM,
            user_id=f"user-{index}",
            ip_address=f"10.0.0.{index}",
            details={},
            timestamp=1000.0 + index,
        )
        for index in range(12)
    ]
    detector._threat_events[0].resolved = True
    monkeypatch.setattr(threat_module.time, "time", lambda: 1012.0)

    summary = detector.get_threat_summary(time_window=20)

    assert summary["total_threats"] == 12
    assert summary["unresolved"] == 11
    assert summary["by_type"][ThreatType.BRUTE_FORCE.value] == 6
    assert summary["by_level"][ThreatLevel.MEDIUM.value] == 8
    assert len(summary["recent_events"]) == 10
    assert summary["recent_events"][0]["event_id"] == "event-2"

    assert detector.resolve_threat("event-1", "handled") is True
    assert detector._threat_events[1].resolved is True
    assert detector._threat_events[1].details["resolution_note"] == "handled"
    assert detector.resolve_threat("missing", "ignored") is False


def test_auto_respond_to_threat_returns_expected_actions(monkeypatch) -> None:
    detector = ThreatDetector()
    monkeypatch.setattr(threat_module.time, "time", lambda: 6000.0)
    high_event = ThreatEvent(
        event_id="high-1",
        threat_type=ThreatType.UNAUTHORIZED_ACCESS,
        level=ThreatLevel.HIGH,
        user_id="user1",
        ip_address="6.6.6.6",
        details={},
    )
    medium_event = ThreatEvent(
        event_id="medium-1",
        threat_type=ThreatType.SUSPICIOUS_LOGIN,
        level=ThreatLevel.MEDIUM,
        user_id="user2",
        ip_address="7.7.7.7",
        details={},
    )
    low_event = ThreatEvent(
        event_id="low-1",
        threat_type=ThreatType.SUSPICIOUS_LOGIN,
        level=ThreatLevel.LOW,
        user_id=None,
        ip_address=None,
        details={},
    )

    high_response = detector.auto_respond_to_threat(high_event)
    medium_response = detector.auto_respond_to_threat(medium_event)
    low_response = detector.auto_respond_to_threat(low_event)

    assert [action["action"] for action in high_response["response_actions"]] == [
        "block_ip",
        "lock_user",
        "send_alert",
    ]
    assert medium_response["response_actions"] == [
        {"action": "enhanced_monitoring", "user_id": "user2", "ip_address": "7.7.7.7"}
    ]
    assert low_response["response_actions"] == []


def test_establish_baseline_and_is_baseline_anomaly(monkeypatch) -> None:
    detector = ThreatDetector()
    monkeypatch.setattr(threat_module.time, "time", lambda: 7000.0)

    assert detector.is_baseline_anomaly("missing", {"hour": 9, "ip_address": "8.8.8.8"}) is False

    detector.establish_baseline(
        "user1",
        {"login_hours": [9, 10], "ip_addresses": ["1.1.1.1"], "data_access_rate": 5},
    )

    assert detector._baseline_patterns["user1"]["average_data_access"] == 5
    assert detector.is_baseline_anomaly("user1", {"hour": 11, "ip_address": "1.1.1.1"}) is True
    assert detector.is_baseline_anomaly("user1", {"hour": 9, "ip_address": "9.9.9.9"}) is True
    assert detector.is_baseline_anomaly("user1", {"hour": 9, "ip_address": "1.1.1.1"}) is False
