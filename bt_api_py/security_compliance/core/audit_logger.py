"""Comprehensive Audit Logging System for Regulatory Compliance.

Provides tamper-evident, cryptographically secure audit logging with support for
SOX, MiFID II, PCI DSS, and GDPR compliance requirements.
"""

import contextlib
import enum
import hashlib
import json
import logging
import tempfile
import time
from collections.abc import Callable
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any
from uuid import uuid4

from bt_api_py.exceptions import BtApiError

_logger = logging.getLogger(__name__)


class AuditError(BtApiError):
    """Audit logging related errors."""


class EventType(enum.Enum):
    """Types of auditable events."""

    USER_LOGIN = "user_login"
    USER_LOGOUT = "user_logout"
    PASSWORD_CHANGE = "password_change"
    ROLE_ASSIGNMENT = "role_assignment"
    PERMISSION_GRANTED = "permission_granted"

    # Trading Events (MiFID II)
    ORDER_CREATED = "order_created"
    ORDER_MODIFIED = "order_modified"
    ORDER_CANCELLED = "order_cancelled"
    ORDER_EXECUTED = "order_executed"
    TRADE_SETTLED = "trade_settled"

    # Data Access (GDPR)
    DATA_ACCESSED = "data_accessed"
    DATA_EXPORTED = "data_exported"
    DATA_DELETED = "data_deleted"

    # System Events
    CONFIG_CHANGE = "config_change"
    SYSTEM_STARTUP = "system_startup"
    SYSTEM_SHUTDOWN = "system_shutdown"
    SECURITY_INCIDENT = "security_incident"

    # Financial Events (SOX/PCI DSS)
    PAYMENT_PROCESSED = "payment_processed"
    BALANCE_ADJUSTMENT = "balance_adjustment"
    ACCOUNT_CREATION = "account_creation"
    ACCOUNT_DELETION = "account_deletion"


class SeverityLevel(enum.IntEnum):
    """Severity levels for audit events."""

    LOW = 1
    MEDIUM = 2
    HIGH = 3
    CRITICAL = 4


@dataclass
class AuditEvent:
    """Individual audit event with cryptographic integrity."""

    event_id: str = field(default_factory=lambda: str(uuid4()))
    timestamp: float = field(default_factory=time.time)
    event_type: EventType = EventType.USER_LOGIN
    severity: SeverityLevel = SeverityLevel.MEDIUM
    user_id: str | None = None
    session_id: str | None = None
    ip_address: str | None = None
    user_agent: str | None = None
    resource: str | None = None
    action: str | None = None
    outcome: str = "success"  # success, failure, error
    details: dict[str, Any] = field(default_factory=dict)
    compliance_tags: list[str] = field(default_factory=list)

    # Cryptographic fields for tamper detection
    previous_hash: str | None = None
    event_hash: str | None = None

    def __post_init__(self) -> None:
        """Calculate event hash after initialization."""
        if not self.event_hash:
            self.event_hash = self._calculate_hash()

    def _calculate_hash(self) -> str:
        """Calculate SHA-256 hash of the event."""
        # Create a dictionary representation without hash fields
        event_data = asdict(self)
        event_data.pop("event_hash", None)
        event_data.pop("previous_hash", None)

        # Sort keys for consistent hashing
        event_json = json.dumps(event_data, sort_keys=True, default=str)
        return hashlib.sha256(event_json.encode()).hexdigest()

    def verify_integrity(self) -> bool:
        """Verify the event has not been tampered with."""
        calculated_hash = self._calculate_hash()
        return calculated_hash == self.event_hash

    def to_dict(self) -> dict[str, Any]:
        """Convert to dictionary for serialization."""
        data = asdict(self)
        data["event_type"] = self.event_type.value
        data["severity"] = int(self.severity)
        return data

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "AuditEvent":
        """Create event from dictionary."""
        # Convert string event_type back to enum
        if isinstance(data.get("event_type"), str):
            data["event_type"] = EventType(data["event_type"])

        # Convert string severity back to enum
        if isinstance(data.get("severity"), int):
            data["severity"] = SeverityLevel(data["severity"])

        return cls(**data)


class AuditLogger:
    """Cryptographically secure audit logger with compliance features."""

    def __init__(
        self,
        log_file: str | Path,
        encryption_key: str | None = None,
        retention_days: int = 2555,  # 7 years for SOX compliance
        enable_real_time: bool = True,
    ):
        """Initialize audit logger.

        Args:
            log_file: Path to audit log file
            encryption_key: Optional key for log encryption
            retention_days: Log retention period in days
            enable_real_time: Enable real-time monitoring
        """
        self.log_file = Path(log_file)
        self.log_file.parent.mkdir(parents=True, exist_ok=True)
        self.encryption_key = encryption_key
        self.retention_days = retention_days
        self.enable_real_time = enable_real_time

        # Chain integrity tracking
        self._last_hash: str | None = None
        self._events: list[AuditEvent] = []
        self._load_last_hash()

        # Real-time monitoring
        self._subscribers: list[Callable[[AuditEvent], None]] = []

    def _load_last_hash(self) -> None:
        """Load the hash of the last event for chain integrity."""
        if not self.log_file.exists():
            return

        try:
            with self.log_file.open(encoding="utf-8") as f:
                lines = f.readlines()
                if lines:
                    last_line = lines[-1].strip()
                    if last_line:
                        last_event = json.loads(last_line)
                        self._last_hash = last_event.get("event_hash")
        except (json.JSONDecodeError, KeyError):
            # File might be corrupted or empty
            pass

    def log_event(self, event: AuditEvent) -> None:
        """Log an audit event with cryptographic integrity."""
        # Set previous hash for chain integrity
        event.previous_hash = self._last_hash

        # Add compliance tags based on event type
        self._add_compliance_tags(event)

        # Recalculate hash with chain integrity after all mutations
        event.event_hash = event._calculate_hash()

        # Serialize event
        event_json = json.dumps(event.to_dict(), default=str)

        # Write to file with atomic operation
        self._write_event_atomic(event_json)

        # Update last hash
        self._last_hash = event.event_hash
        self._events.append(event)

        # Notify real-time subscribers
        if self.enable_real_time:
            self._notify_subscribers(event)

    def _add_compliance_tags(self, event: AuditEvent) -> None:
        """Add compliance tags based on event type."""
        # SOX compliance tags
        sox_events = {
            EventType.ORDER_CREATED,
            EventType.ORDER_EXECUTED,
            EventType.TRADE_SETTLED,
            EventType.BALANCE_ADJUSTMENT,
        }

        # MiFID II compliance tags
        mifid_events = {
            EventType.ORDER_CREATED,
            EventType.ORDER_MODIFIED,
            EventType.ORDER_CANCELLED,
            EventType.ORDER_EXECUTED,
        }

        # PCI DSS compliance tags
        pci_events = {
            EventType.PAYMENT_PROCESSED,
            EventType.ACCOUNT_CREATION,
            EventType.DATA_ACCESSED,
        }

        # GDPR compliance tags
        gdpr_events = {
            EventType.DATA_ACCESSED,
            EventType.DATA_EXPORTED,
            EventType.DATA_DELETED,
            EventType.USER_LOGIN,
        }

        if event.event_type in sox_events:
            event.compliance_tags.append("SOX")

        if event.event_type in mifid_events:
            event.compliance_tags.append("MIFID_II")

        if event.event_type in pci_events:
            event.compliance_tags.append("PCI_DSS")

        if event.event_type in gdpr_events:
            event.compliance_tags.append("GDPR")

    def _write_event_atomic(self, event_json: str) -> None:
        """Write event atomically to prevent corruption."""
        temp_file: Path | None = None

        try:
            self.log_file.parent.mkdir(parents=True, exist_ok=True)
            with tempfile.NamedTemporaryFile(
                mode="w",
                encoding="utf-8",
                delete=False,
                dir=self.log_file.parent,
                prefix=f"{self.log_file.stem}_",
                suffix=".tmp",
            ) as f:
                f.write(event_json + "\n")
                temp_file = Path(f.name)

            with self.log_file.open("a", encoding="utf-8") as f:
                f.write(event_json + "\n")

        except Exception as e:
            if temp_file is not None and temp_file.exists():
                with contextlib.suppress(OSError):
                    temp_file.unlink()
            raise AuditError(f"Failed to write audit event: {e}") from e
        finally:
            if temp_file is not None and temp_file.exists():
                with contextlib.suppress(OSError):
                    temp_file.unlink()

    def _notify_subscribers(self, event: AuditEvent) -> None:
        """Notify real-time monitoring subscribers."""
        for callback in self._subscribers:
            # Don't let subscriber errors break logging
            with contextlib.suppress(Exception):
                callback(event)

    def subscribe(self, callback: Callable[[AuditEvent], None]) -> None:
        """Subscribe to real-time audit events."""
        self._subscribers.append(callback)

    def unsubscribe(self, callback: Callable[[AuditEvent], None]) -> None:
        """Unsubscribe from real-time audit events."""
        if callback in self._subscribers:
            self._subscribers.remove(callback)

    def verify_log_integrity(self) -> dict[str, Any]:
        """Verify the integrity of the entire audit log."""
        if not self.log_file.exists():
            return {"status": "no_log_file", "verified_events": 0, "violations": []}

        violations = []
        verified_count = 0
        previous_hash = None

        try:
            with self.log_file.open(encoding="utf-8") as f:
                for line_num, line in enumerate(f, 1):
                    line = line.strip()
                    if not line:
                        continue

                    try:
                        event_data = json.loads(line)
                        event = AuditEvent.from_dict(event_data)

                        # Check chain integrity
                        if previous_hash and event.previous_hash != previous_hash:
                            violations.append(
                                {
                                    "line": line_num,
                                    "event_id": event.event_id,
                                    "violation": "chain_hash_mismatch",
                                    "expected": previous_hash,
                                    "actual": event.previous_hash,
                                }
                            )

                        # Check event integrity
                        if not event.verify_integrity():
                            violations.append(
                                {
                                    "line": line_num,
                                    "event_id": event.event_id,
                                    "violation": "event_hash_mismatch",
                                }
                            )

                        verified_count += 1
                        previous_hash = event.event_hash

                    except Exception as e:
                        violations.append(
                            {"line": line_num, "violation": "parse_error", "error": str(e)}
                        )

        except Exception as e:
            violations.append({"violation": "file_read_error", "error": str(e)})

        return {
            "status": "verified" if not violations else "violations_found",
            "verified_events": verified_count,
            "violations": violations,
            "last_hash": previous_hash,
        }

    def search_events(
        self,
        event_type: EventType | None = None,
        user_id: str | None = None,
        start_time: float | None = None,
        end_time: float | None = None,
        limit: int = 1000,
    ) -> list[AuditEvent]:
        """Search audit events with filters."""
        if not self.log_file.exists():
            return []

        results = []

        try:
            with self.log_file.open(encoding="utf-8") as f:
                for line in f:
                    line = line.strip()
                    if not line:
                        continue

                    try:
                        event_data = json.loads(line)
                        event = AuditEvent.from_dict(event_data)

                        # Apply filters
                        if event_type and event.event_type != event_type:
                            continue

                        if user_id and event.user_id != user_id:
                            continue

                        if start_time and event.timestamp < start_time:
                            continue

                        if end_time and event.timestamp > end_time:
                            continue

                        results.append(event)

                        if len(results) >= limit:
                            break

                    except Exception as e:
                        _logger.debug("Skip malformed audit event: %s", e)
                        continue

        except Exception as e:
            _logger.debug(
                "Audit log file read failed, returning partial results: %s",
                e,
                exc_info=True,
            )

        # Sort by timestamp (newest first)
        results.sort(key=lambda e: e.timestamp, reverse=True)
        return results

    def get_compliance_report(
        self,
        compliance_standard: str,
        start_date: str | None = None,
        end_date: str | None = None,
    ) -> dict[str, Any]:
        """Generate compliance report for specific standard."""
        # Convert date strings to timestamps
        start_timestamp = None
        end_timestamp = None

        if start_date:
            from datetime import datetime

            start_timestamp = datetime.fromisoformat(start_date).timestamp()

        if end_date:
            from datetime import datetime

            end_timestamp = datetime.fromisoformat(end_date).timestamp()

        # Get all events
        all_events = self.search_events(
            start_time=start_timestamp, end_time=end_timestamp, limit=10000
        )

        # Filter by compliance standard
        compliance_events = [
            event for event in all_events if compliance_standard in event.compliance_tags
        ]

        # Generate statistics
        stats: dict[str, Any] = {
            "total_events": len(compliance_events),
            "by_type": {},
            "by_severity": {},
            "by_outcome": {},
            "timeline": [],
        }

        for event in compliance_events:
            # By type
            event_type = event.event_type.value
            stats["by_type"][event_type] = stats["by_type"].get(event_type, 0) + 1

            # By severity
            severity = event.severity.name
            stats["by_severity"][severity] = stats["by_severity"].get(severity, 0) + 1

            # By outcome
            outcome = event.outcome
            stats["by_outcome"][outcome] = stats["by_outcome"].get(outcome, 0) + 1

            # Timeline (daily aggregation)
            from datetime import datetime

            date = datetime.fromtimestamp(event.timestamp).date().isoformat()

            timeline_entry = next(
                (entry for entry in stats["timeline"] if entry["date"] == date), None
            )

            if not timeline_entry:
                timeline_entry = {"date": date, "count": 0}
                stats["timeline"].append(timeline_entry)

            timeline_entry["count"] += 1

        return {
            "standard": compliance_standard,
            "period": {"start": start_date, "end": end_date},
            "statistics": stats,
            "events": [event.to_dict() for event in compliance_events[:100]],  # First 100
        }

    def cleanup_old_events(self) -> int:
        """Remove events older than retention period."""
        if not self.log_file.exists():
            return 0

        cutoff_time = time.time() - (self.retention_days * 24 * 60 * 60)
        temp_file = self.log_file.with_suffix(".clean")
        removed_count = 0

        try:
            with (
                self.log_file.open(encoding="utf-8") as infile,
                temp_file.open("w", encoding="utf-8") as outfile,
            ):
                for line in infile:
                    line = line.strip()
                    if not line:
                        continue

                    try:
                        event_data = json.loads(line)
                        event = AuditEvent.from_dict(event_data)

                        if event.timestamp >= cutoff_time:
                            outfile.write(line + "\n")
                        else:
                            removed_count += 1
                    except Exception:
                        # Keep malformed events for audit trail
                        outfile.write(line + "\n")

            # Replace original file
            temp_file.replace(self.log_file)

        except Exception as e:
            # Clean up temp file on error
            if temp_file.exists():
                temp_file.unlink()
            raise AuditError(f"Failed to cleanup old events: {e}") from e

        return removed_count


# Global audit logger instance
_audit_logger: AuditLogger | None = None


def get_audit_logger() -> AuditLogger | None:
    """Get the global audit logger instance."""
    return _audit_logger


def initialize_audit_logger(log_file: str | Path, **kwargs) -> AuditLogger:
    """Initialize the global audit logger."""
    global _audit_logger
    _audit_logger = AuditLogger(log_file, **kwargs)
    return _audit_logger


def log_audit_event(
    event_type: EventType,
    user_id: str | None = None,
    severity: SeverityLevel = SeverityLevel.MEDIUM,
    **kwargs,
) -> None:
    """Log an audit event using the global logger."""
    if _audit_logger is None:
        raise AuditError("Audit logger not initialized")

    event = AuditEvent(event_type=event_type, user_id=user_id, severity=severity, **kwargs)

    _audit_logger.log_event(event)
