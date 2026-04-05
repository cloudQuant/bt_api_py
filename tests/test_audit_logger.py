"""Tests for audit_logger module - pure local logic."""

import time
from unittest.mock import Mock

import pytest

from bt_api_py.security_compliance.core.audit_logger import (
    AuditError,
    AuditEvent,
    AuditLogger,
    EventType,
    SeverityLevel,
    get_audit_logger,
    initialize_audit_logger,
    log_audit_event,
)


class TestEventType:
    """Tests for EventType enum."""

    def test_event_types_exist(self):
        """Test all event types exist."""
        assert EventType.USER_LOGIN.value == "user_login"
        assert EventType.ORDER_CREATED.value == "order_created"
        assert EventType.DATA_ACCESSED.value == "data_accessed"
        assert EventType.PAYMENT_PROCESSED.value == "payment_processed"


class TestSeverityLevel:
    """Tests for SeverityLevel enum."""

    def test_severity_levels(self):
        """Test severity level values."""
        assert SeverityLevel.LOW == 1
        assert SeverityLevel.MEDIUM == 2
        assert SeverityLevel.HIGH == 3
        assert SeverityLevel.CRITICAL == 4


class TestAuditEvent:
    """Tests for AuditEvent dataclass."""

    def test_init_defaults(self):
        """Test default initialization."""
        event = AuditEvent()

        assert event.event_id is not None
        assert event.timestamp > 0
        assert event.event_type == EventType.USER_LOGIN
        assert event.severity == SeverityLevel.MEDIUM
        assert event.outcome == "success"
        assert event.details == {}
        assert event.compliance_tags == []
        assert event.event_hash is not None

    def test_init_custom(self):
        """Test custom initialization."""
        event = AuditEvent(
            event_type=EventType.ORDER_CREATED,
            severity=SeverityLevel.HIGH,
            user_id="user123",
            session_id="sess456",
            ip_address="192.168.1.1",
            resource="order",
            action="create",
            outcome="success",
            details={"symbol": "BTCUSDT"},
        )

        assert event.event_type == EventType.ORDER_CREATED
        assert event.severity == SeverityLevel.HIGH
        assert event.user_id == "user123"
        assert event.details == {"symbol": "BTCUSDT"}

    def test_calculate_hash(self):
        """Test hash calculation."""
        event = AuditEvent(event_type=EventType.USER_LOGIN)

        # Hash should be set after init
        assert event.event_hash is not None
        assert len(event.event_hash) == 64  # SHA-256 hex length

    def test_verify_integrity(self):
        """Test integrity verification."""
        event = AuditEvent(event_type=EventType.USER_LOGIN)

        assert event.verify_integrity() is True

        # Tamper with the event
        event.user_id = "tampered"

        # Hash should no longer match
        assert event.verify_integrity() is False

    def test_to_dict(self):
        """Test dictionary conversion."""
        event = AuditEvent(
            event_type=EventType.ORDER_CREATED,
            severity=SeverityLevel.HIGH,
            user_id="user123",
        )

        data = event.to_dict()

        assert data["event_type"] == "order_created"
        assert data["severity"] == 3
        assert data["user_id"] == "user123"

    def test_from_dict(self):
        """Test creating event from dictionary."""
        data = {
            "event_id": "test-id",
            "timestamp": time.time(),
            "event_type": "order_created",
            "severity": 3,
            "user_id": "user123",
            "outcome": "success",
            "details": {},
            "compliance_tags": [],
            "event_hash": "hash123",
        }

        event = AuditEvent.from_dict(data)

        assert event.event_type == EventType.ORDER_CREATED
        assert event.severity == SeverityLevel.HIGH
        assert event.user_id == "user123"

    def test_from_dict_with_existing_enum(self):
        """Test from_dict with already-converted enum."""
        data = {
            "event_type": EventType.USER_LOGIN,
            "severity": SeverityLevel.LOW,
        }

        event = AuditEvent.from_dict(data)

        assert event.event_type == EventType.USER_LOGIN


class TestAuditLogger:
    """Tests for AuditLogger class."""

    def test_init(self, tmp_path):
        """Test initialization."""
        log_file = tmp_path / "audit.log"
        logger = AuditLogger(log_file)

        assert logger.log_file == log_file
        assert logger.retention_days == 2555
        assert logger.enable_real_time is True

    def test_log_event(self, tmp_path):
        """Test logging an event."""
        log_file = tmp_path / "audit.log"
        logger = AuditLogger(log_file)

        event = AuditEvent(event_type=EventType.USER_LOGIN, user_id="user123")
        logger.log_event(event)

        assert log_file.exists()
        assert len(logger._events) == 1
        assert logger._last_hash is not None

    def test_log_event_chain_integrity(self, tmp_path):
        """Test chain integrity in logging."""
        log_file = tmp_path / "audit.log"
        logger = AuditLogger(log_file)

        event1 = AuditEvent(event_type=EventType.USER_LOGIN)
        logger.log_event(event1)

        event2 = AuditEvent(event_type=EventType.USER_LOGOUT)
        logger.log_event(event2)

        # Second event should reference first event's hash
        assert event2.previous_hash == event1.event_hash

    def test_compliance_tags_sox(self, tmp_path):
        """Test SOX compliance tags."""
        log_file = tmp_path / "audit.log"
        logger = AuditLogger(log_file)

        event = AuditEvent(event_type=EventType.ORDER_CREATED)
        logger.log_event(event)

        assert "SOX" in event.compliance_tags

    def test_compliance_tags_mifid(self, tmp_path):
        """Test MiFID II compliance tags."""
        log_file = tmp_path / "audit.log"
        logger = AuditLogger(log_file)

        event = AuditEvent(event_type=EventType.ORDER_MODIFIED)
        logger.log_event(event)

        assert "MIFID_II" in event.compliance_tags

    def test_compliance_tags_pci(self, tmp_path):
        """Test PCI DSS compliance tags."""
        log_file = tmp_path / "audit.log"
        logger = AuditLogger(log_file)

        event = AuditEvent(event_type=EventType.PAYMENT_PROCESSED)
        logger.log_event(event)

        assert "PCI_DSS" in event.compliance_tags

    def test_compliance_tags_gdpr(self, tmp_path):
        """Test GDPR compliance tags."""
        log_file = tmp_path / "audit.log"
        logger = AuditLogger(log_file)

        event = AuditEvent(event_type=EventType.DATA_EXPORTED)
        logger.log_event(event)

        assert "GDPR" in event.compliance_tags

    def test_subscribe(self, tmp_path):
        """Test subscribing to events."""
        log_file = tmp_path / "audit.log"
        logger = AuditLogger(log_file)

        callback = Mock()
        logger.subscribe(callback)

        event = AuditEvent(event_type=EventType.USER_LOGIN)
        logger.log_event(event)

        callback.assert_called_once_with(event)

    def test_unsubscribe(self, tmp_path):
        """Test unsubscribing from events."""
        log_file = tmp_path / "audit.log"
        logger = AuditLogger(log_file)

        callback = Mock()
        logger.subscribe(callback)
        logger.unsubscribe(callback)

        event = AuditEvent(event_type=EventType.USER_LOGIN)
        logger.log_event(event)

        callback.assert_not_called()

    def test_verify_log_integrity_no_file(self, tmp_path):
        """Test integrity verification with no file."""
        log_file = tmp_path / "audit.log"
        logger = AuditLogger(log_file)

        result = logger.verify_log_integrity()

        assert result["status"] == "no_log_file"
        assert result["verified_events"] == 0

    def test_verify_log_integrity_valid(self, tmp_path):
        """Test integrity verification with valid log."""
        log_file = tmp_path / "audit.log"
        logger = AuditLogger(log_file)

        event = AuditEvent(event_type=EventType.USER_LOGIN)
        logger.log_event(event)

        result = logger.verify_log_integrity()

        assert result["status"] == "verified"
        assert result["verified_events"] == 1

    def test_search_events_no_file(self, tmp_path):
        """Test search with no file."""
        log_file = tmp_path / "audit.log"
        logger = AuditLogger(log_file)

        results = logger.search_events()

        assert results == []

    def test_search_events_with_filters(self, tmp_path):
        """Test search with filters."""
        log_file = tmp_path / "audit.log"
        logger = AuditLogger(log_file)

        # Log multiple events
        logger.log_event(AuditEvent(event_type=EventType.USER_LOGIN, user_id="user1"))
        logger.log_event(AuditEvent(event_type=EventType.USER_LOGOUT, user_id="user1"))
        logger.log_event(AuditEvent(event_type=EventType.USER_LOGIN, user_id="user2"))

        # Filter by event type
        results = logger.search_events(event_type=EventType.USER_LOGIN)
        assert len(results) == 2

        # Filter by user_id
        results = logger.search_events(user_id="user1")
        assert len(results) == 2

    def test_search_events_time_range(self, tmp_path):
        """Test search with time range."""
        log_file = tmp_path / "audit.log"
        logger = AuditLogger(log_file)

        now = time.time()
        logger.log_event(AuditEvent(event_type=EventType.USER_LOGIN))

        # Search in time range
        results = logger.search_events(start_time=now - 100, end_time=now + 100)
        assert len(results) == 1

    def test_get_compliance_report(self, tmp_path):
        """Test compliance report generation."""
        log_file = tmp_path / "audit.log"
        logger = AuditLogger(log_file)

        logger.log_event(AuditEvent(event_type=EventType.ORDER_CREATED))
        logger.log_event(AuditEvent(event_type=EventType.ORDER_EXECUTED))

        report = logger.get_compliance_report("SOX")

        assert report["standard"] == "SOX"
        assert report["statistics"]["total_events"] == 2

    def test_cleanup_old_events(self, tmp_path):
        """Test cleanup of old events."""
        log_file = tmp_path / "audit.log"
        logger = AuditLogger(log_file, retention_days=0)  # 0 days retention

        logger.log_event(AuditEvent(event_type=EventType.USER_LOGIN))

        removed = logger.cleanup_old_events()

        assert removed == 1

    def test_cleanup_no_file(self, tmp_path):
        """Test cleanup with no file."""
        log_file = tmp_path / "audit.log"
        logger = AuditLogger(log_file)

        removed = logger.cleanup_old_events()

        assert removed == 0

    def test_load_last_hash_existing_file(self, tmp_path):
        """Test loading last hash from existing file."""
        log_file = tmp_path / "audit.log"

        # Create file with one event
        logger1 = AuditLogger(log_file)
        logger1.log_event(AuditEvent(event_type=EventType.USER_LOGIN))

        # Create new logger - should load last hash
        logger2 = AuditLogger(log_file)

        assert logger2._last_hash is not None


class TestGlobalFunctions:
    """Tests for global functions."""

    def test_get_audit_logger_none(self):
        """Test getting uninitialized logger."""
        # Reset global logger
        import bt_api_py.security_compliance.core.audit_logger as module

        module._audit_logger = None

        assert get_audit_logger() is None

    def test_initialize_audit_logger(self, tmp_path):
        """Test initializing global logger."""
        import bt_api_py.security_compliance.core.audit_logger as module

        module._audit_logger = None

        log_file = tmp_path / "audit.log"
        logger = initialize_audit_logger(log_file)

        assert logger is not None
        assert get_audit_logger() == logger

    def test_log_audit_event(self, tmp_path):
        """Test logging via global function."""
        import bt_api_py.security_compliance.core.audit_logger as module

        module._audit_logger = None

        log_file = tmp_path / "audit.log"
        initialize_audit_logger(log_file)

        log_audit_event(EventType.USER_LOGIN, user_id="test_user")

        assert log_file.exists()

    def test_log_audit_event_not_initialized(self):
        """Test logging without initialization."""
        import bt_api_py.security_compliance.core.audit_logger as module

        module._audit_logger = None

        with pytest.raises(AuditError, match="Audit logger not initialized"):
            log_audit_event(EventType.USER_LOGIN)


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
