"""Experimental explicit calendar notifications with a SQLite intent journal.

Nothing runs on import and there is no scheduler or implicit SMTP integration.
The caller injects both a state reader and a sender; ``send_via_base`` is an
optional explicit adapter which lazily loads the Base email transport. State claims must be backed
by the caller's independently authenticated, scope-bound state; this module cannot
prove them. State is checked twice, including immediately before sending, but it
can change after the second check. Production needs shared policy coordination
or locking and revalidation; those integrations, crash durability across power
loss, Windows and network filesystem behavior remain unverified.

A committed IN_FLIGHT intent precedes every sender call. A surviving IN_FLIGHT
is reported as UNKNOWN without resending, including while another process is
still running. A proven pre-call NOT_SENT can be retried through a conditional
transaction; UNKNOWN, IN_FLIGHT and any acceptance/refusal result cannot be reset.
SMTP server acceptance is not mailbox delivery. The journal contains only
validated scope/event IDs, dates, owner tokens and finite statuses, never message
content, recipient addresses, credentials or SMTP replies.
"""

from __future__ import annotations

import math
import re
import sqlite3
import uuid
from collections.abc import Callable, Mapping
from contextlib import suppress
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from email.errors import HeaderParseError
from email.headerregistry import Address
from pathlib import Path
from zoneinfo import ZoneInfo

_ID = re.compile(r"[A-Za-z0-9][A-Za-z0-9_.:-]{0,127}\Z", re.ASCII)
_OUTCOMES = frozenset(
    {"IN_FLIGHT", "NOT_SENT", "ACCEPTED", "PARTIAL_REFUSAL", "REFUSED", "UNKNOWN"}
)


@dataclass(frozen=True)
class EmailConfig:
    """Explicit transport settings; fields are intentionally excluded from repr."""

    enabled: bool = False
    host: str = field(default="", repr=False)
    port: int = field(default=0, repr=False)
    timeout: float = field(default=0, repr=False)
    sender: str = field(default="", repr=False)
    recipients: tuple[str, ...] = field(default=(), repr=False)
    subject: str = field(default="", repr=False)
    body: str = field(default="", repr=False)
    username: str | None = field(default=None, repr=False)
    password: str | None = field(default=None, repr=False)
    security: str = field(default="starttls", repr=False)


@dataclass(frozen=True)
class NotificationState:
    """State port assertion; PROVEN is a caller declaration, not authentication."""

    scope_id: str
    event_id: str
    mode: str
    evidence: str
    candidate_state: str = "UNKNOWN"
    publication_state: str = "UNKNOWN"
    content_change: str = "UNKNOWN"


@dataclass(frozen=True)
class NotificationResult:
    status: str
    reason: str
    journal_status: str | None = None


@dataclass(frozen=True)
class BaseEmailReceipt:
    """Accepted/refused identities for coordinator use, without SMTP replies."""

    accepted_recipients: tuple[str, ...] = field(repr=False)
    refused_recipients: dict[str, None] = field(repr=False)


def _header_valid(value: object) -> bool:
    return (
        type(value) is str
        and bool(value.strip())
        and not any(ord(char) < 32 or ord(char) == 127 for char in value)
    )


def _address_valid(value: object) -> bool:
    if not _header_valid(value):
        return False
    try:
        address = Address(addr_spec=value)
        return bool(address.username and address.domain)
    except (TypeError, ValueError, HeaderParseError):
        return False


def _config_complete(config: EmailConfig) -> bool:
    if type(config) is not EmailConfig or type(config.enabled) is not bool:
        return False
    strings = (config.host, config.sender, config.subject, config.body)
    if any(type(value) is not str or not value.strip() for value in strings):
        return False
    if not _header_valid(config.host) or any(char.isspace() for char in config.host):
        return False
    if not _header_valid(config.subject) or not _address_valid(config.sender):
        return False
    if type(config.port) is not int or not 1 <= config.port <= 65535:
        return False
    if type(config.timeout) not in (float, int) or not math.isfinite(config.timeout):
        return False
    if not 0 < config.timeout <= 86400 or config.security not in ("starttls", "ssl", "plain"):
        return False
    if type(config.recipients) is not tuple or not config.recipients:
        return False
    if any(not _address_valid(value) for value in config.recipients):
        return False
    if len(set(config.recipients)) != len(config.recipients):
        return False
    if (config.username is None) != (config.password is None):
        return False
    return not (
        config.username is not None
        and (
            not _header_valid(config.username)
            or type(config.password) is not str
            or not config.password
        )
    )


def send_via_base(config: EmailConfig) -> BaseEmailReceipt:
    """Explicitly invoke Base email transport after enablement and validation.

    The returned sanitized receipt describes SMTP acceptance/refusal, not
    mailbox delivery. SMTP response contents are discarded and receipt repr
    excludes recipient identities. The caller may pass this as the sender;
    after invocation begins, transport or import errors remain UNKNOWN there.
    Error messages are deliberately fixed and never include transport details.
    """
    if type(config) is not EmailConfig or not _config_complete(config) or not config.enabled:
        raise ValueError("email transport disabled or invalid")
    try:
        from bt_api_base.email_sender import send_email
    except Exception:
        raise RuntimeError("base email transport unavailable") from None
    try:
        result = send_email(
            host=config.host,
            port=config.port,
            timeout=config.timeout,
            sender=config.sender,
            recipients=config.recipients,
            subject=config.subject,
            body=config.body,
            username=config.username,
            password=config.password,
            security=config.security,
        )
        if _delivery_status(result, config) == "UNKNOWN":
            raise ValueError("invalid transport receipt")
        return BaseEmailReceipt(
            tuple(result.accepted_recipients),
            dict.fromkeys(result.refused_recipients),
        )
    except Exception:
        raise RuntimeError("base email transport outcome unknown") from None


def _period(instant: datetime | None, bound: timedelta | None) -> str | None:
    if type(instant) is not datetime or type(bound) is not timedelta or bound < timedelta(0):
        return None
    try:
        if instant.tzinfo is None or instant.utcoffset() is None:
            return None
        moment = instant.astimezone(UTC)
        zone = ZoneInfo("Asia/Shanghai")
        first = (moment - bound).astimezone(zone).date()
        last = (moment + bound).astimezone(zone).date()
        return first.isoformat() if first == last else None
    except Exception:
        return None


def _eligible(state: object, *, kind: str, scope_id: str, event_id: str) -> bool:
    if type(state) is not NotificationState:
        return False
    if state.evidence != "PROVEN" or state.scope_id != scope_id or state.event_id != event_id:
        return False
    if kind == "manual":
        return state.mode == "manual" and state.candidate_state == "PENDING"
    return (
        state.mode == "auto"
        and state.publication_state == "COMMITTED"
        and state.content_change == "CHANGED"
    )


def _delivery_status(result: object, config: EmailConfig) -> str:
    try:
        accepted = result.accepted_recipients
        refused = result.refused_recipients
        if type(accepted) is not tuple or not isinstance(refused, Mapping):
            return "UNKNOWN"
        if any(type(item) is not str for item in accepted) or any(
            type(item) is not str for item in refused
        ):
            return "UNKNOWN"
        expected, accepted_set, refused_set = set(config.recipients), set(accepted), set(refused)
        if (
            len(accepted_set) != len(accepted)
            or accepted_set & refused_set
            or accepted_set | refused_set != expected
        ):
            return "UNKNOWN"
        if accepted_set and refused_set:
            return "PARTIAL_REFUSAL"
        if accepted_set:
            return "ACCEPTED"
        return "REFUSED"
    except Exception:
        return "UNKNOWN"


def _set_outcome(
    connection: sqlite3.Connection, key: tuple[str, str, str, str], owner: str, outcome: str
) -> bool:
    try:
        cursor = connection.execute(
            "UPDATE notifications SET status=? WHERE kind=? AND scope_id=? AND event_id=? "
            "AND period=? AND owner=? AND status='IN_FLIGHT'",
            (outcome, *key, owner),
        )
        connection.commit()
        return cursor.rowcount == 1
    except sqlite3.Error:
        with suppress(sqlite3.Error):
            connection.rollback()
        return False


def notify_calendar_event(
    *,
    journal_path: str | Path,
    kind: str,
    scope_id: str,
    event_id: str,
    config: EmailConfig,
    state_reader: Callable[[str, str, str], NotificationState],
    sender: Callable[[EmailConfig], object],
    clock_instant: datetime | None = None,
    clock_error_bound: timedelta | None = None,
    clock_evidence: str = "UNKNOWN",
) -> NotificationResult:
    """Attempt one explicit notification; errors never contain sensitive details.

    Reader receives kind, scope ID and event ID, and is called before intent
    reservation and again immediately before sender invocation. ``clock_evidence``
    is a caller assertion and does not prove clock accuracy. Manual notifications
    require a known Shanghai natural date and explicit finite error bound.
    The sender is injected and must return Base-compatible acceptance fields.
    """
    if kind not in ("manual", "auto") or any(
        type(value) is not str or _ID.fullmatch(value) is None for value in (scope_id, event_id)
    ):
        raise ValueError("invalid notification identity")
    if type(config) is not EmailConfig or type(config.enabled) is not bool:
        return NotificationResult("NOT_SENT", "INVALID_CONFIG")
    if not config.enabled:
        return NotificationResult("NOT_SENT", "DISABLED")
    if not _config_complete(config) or not callable(state_reader) or not callable(sender):
        return NotificationResult("NOT_SENT", "INVALID_CONFIG")
    try:
        path = Path(journal_path)
        if not path.is_absolute() or not path.name or "\x00" in str(path):
            return NotificationResult("NOT_SENT", "INVALID_JOURNAL")
    except (TypeError, ValueError):
        return NotificationResult("NOT_SENT", "INVALID_JOURNAL")
    period = ""
    if kind == "manual":
        if clock_evidence != "PROVEN":
            return NotificationResult("NOT_SENT", "CLOCK_UNKNOWN")
        period = _period(clock_instant, clock_error_bound)
        if period is None:
            return NotificationResult("NOT_SENT", "CLOCK_UNKNOWN")
    try:
        if not _eligible(
            state_reader(kind, scope_id, event_id), kind=kind, scope_id=scope_id, event_id=event_id
        ):
            return NotificationResult("NOT_SENT", "STATE_NOT_ELIGIBLE")
    except Exception:
        return NotificationResult("NOT_SENT", "STATE_UNAVAILABLE")

    key = (kind, scope_id, event_id, period)
    owner = uuid.uuid4().hex
    connection = None
    try:
        connection = sqlite3.connect(str(path), timeout=1.0)
        connection.execute("PRAGMA synchronous=FULL")
        connection.execute(
            "CREATE TABLE IF NOT EXISTS notifications ("
            "kind TEXT NOT NULL, scope_id TEXT NOT NULL, event_id TEXT NOT NULL, "
            "period TEXT NOT NULL, owner TEXT NOT NULL, status TEXT NOT NULL, "
            "PRIMARY KEY(kind, scope_id, event_id, period))"
        )
        schema = connection.execute("PRAGMA table_info(notifications)").fetchall()
        expected = [
            ("kind", 1),
            ("scope_id", 2),
            ("event_id", 3),
            ("period", 4),
            ("owner", 0),
            ("status", 0),
        ]
        if (
            len(schema) != len(expected)
            or [(row[1], row[5]) for row in schema] != expected
            or any(row[2].upper() != "TEXT" or row[3] != 1 for row in schema)
            or connection.execute(
                "SELECT 1 FROM sqlite_master WHERE type='trigger' AND "
                "tbl_name='notifications' LIMIT 1"
            ).fetchone()
        ):
            connection.close()
            return NotificationResult("NOT_SENT", "JOURNAL_INVALID")
        connection.execute("BEGIN IMMEDIATE")
        previous = connection.execute(
            "SELECT status FROM notifications WHERE kind=? AND scope_id=? AND event_id=? AND period=?",
            key,
        ).fetchone()
        if previous is not None:
            previous_status = previous[0]
            if previous_status not in _OUTCOMES:
                connection.rollback()
                connection.close()
                return NotificationResult("NOT_SENT", "JOURNAL_INVALID")
            if previous_status != "NOT_SENT":
                connection.rollback()
                connection.close()
                effective = "UNKNOWN" if previous_status == "IN_FLIGHT" else previous_status
                return NotificationResult("DUPLICATE", "ALREADY_RESERVED", effective)
            connection.execute(
                "UPDATE notifications SET owner=?, status='IN_FLIGHT' "
                "WHERE kind=? AND scope_id=? AND event_id=? AND period=? AND status='NOT_SENT'",
                (owner, *key),
            )
        else:
            connection.execute(
                "INSERT INTO notifications(kind,scope_id,event_id,period,owner,status) "
                "VALUES(?,?,?,?,?,'IN_FLIGHT')",
                (*key, owner),
            )
        connection.commit()
    except (sqlite3.Error, OSError):
        if connection is not None:
            connection.close()
        return NotificationResult("NOT_SENT", "JOURNAL_UNAVAILABLE")

    try:
        try:
            eligible = _eligible(
                state_reader(kind, scope_id, event_id),
                kind=kind,
                scope_id=scope_id,
                event_id=event_id,
            )
            reason = "STATE_NOT_ELIGIBLE"
        except Exception:
            eligible, reason = False, "STATE_UNAVAILABLE"
        if not eligible:
            _set_outcome(connection, key, owner, "NOT_SENT")
            return NotificationResult("NOT_SENT", reason)
        try:
            status = _delivery_status(sender(config), config)
        except Exception:
            status = "UNKNOWN"
        if not _set_outcome(connection, key, owner, status):
            return NotificationResult("UNKNOWN", "JOURNAL_OUTCOME_UNKNOWN", "UNKNOWN")
        return NotificationResult(status, "SMTP_OUTCOME", status)
    finally:
        connection.close()
