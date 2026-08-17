"""Structured audit events for CTP certification evidence."""

from __future__ import annotations

import json
import uuid
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from enum import Enum
from pathlib import Path
from typing import Any

_SENSITIVE_KEYS = {
    "auth_code",
    "password",
    "passwd",
    "secret",
    "secret_key",
    "token",
    "access_token",
    "api_key",
}


class CertificationAuditStatus(str, Enum):
    """Certification scenario/event result states."""

    PASS = "PASS"
    FAIL = "FAIL"
    WARN = "WARN"
    SKIPPED = "SKIPPED"
    BLOCKED = "BLOCKED"
    INFO = "INFO"


def mask_sensitive(value: Any) -> Any:
    """Recursively mask sensitive audit evidence fields."""

    if isinstance(value, dict):
        masked: dict[str, Any] = {}
        for key, item in value.items():
            if str(key).lower() in _SENSITIVE_KEYS:
                text = str(item or "")
                masked[key] = "****" + text[-4:] if len(text) > 4 else "****"
            else:
                masked[key] = mask_sensitive(item)
        return masked
    if isinstance(value, list):
        return [mask_sensitive(item) for item in value]
    return value


@dataclass(frozen=True)
class CertificationAuditEvent:
    """Machine-readable audit event for certification logs and reports."""

    scenario_id: str
    scenario_name: str
    category: str
    status: CertificationAuditStatus | str
    message: str
    evidence: dict[str, Any] = field(default_factory=dict)
    raw_fields: dict[str, Any] = field(default_factory=dict)
    event_id: str = field(default_factory=lambda: str(uuid.uuid4()))
    trace_id: str = ""
    severity: str = "INFO"
    timestamp: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat())
    gateway_key: str = ""
    exchange_type: str = "CTP"
    account_id_masked: str = ""
    strategy_id: str = ""
    instrument: str = ""
    order_ref: str = ""
    external_order_id: str = ""
    trade_id: str = ""

    def to_dict(self) -> dict[str, Any]:
        payload = asdict(self)
        payload["status"] = str(self.status.value if isinstance(self.status, Enum) else self.status)
        payload["evidence"] = mask_sensitive(payload.get("evidence") or {})
        payload["raw_fields"] = mask_sensitive(payload.get("raw_fields") or {})
        return payload


class InMemoryCertificationAuditSink:
    """In-memory audit sink for tests and lightweight web previews."""

    def __init__(self) -> None:
        self.events: list[dict[str, Any]] = []

    def emit(self, event: CertificationAuditEvent) -> dict[str, Any]:
        payload = event.to_dict()
        self.events.append(payload)
        return payload

    def query(
        self,
        *,
        scenario_id: str = "",
        status: str = "",
        account_id_masked: str = "",
        strategy_id: str = "",
    ) -> list[dict[str, Any]]:
        rows = list(self.events)
        if scenario_id:
            rows = [item for item in rows if item.get("scenario_id") == scenario_id]
        if status:
            rows = [item for item in rows if item.get("status") == status]
        if account_id_masked:
            rows = [item for item in rows if item.get("account_id_masked") == account_id_masked]
        if strategy_id:
            rows = [item for item in rows if item.get("strategy_id") == strategy_id]
        return rows


class JsonlCertificationAuditSink:
    """Append-only JSONL audit sink."""

    def __init__(self, path: str | Path) -> None:
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)

    def emit(self, event: CertificationAuditEvent) -> dict[str, Any]:
        payload = event.to_dict()
        with self.path.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(payload, ensure_ascii=False, sort_keys=True) + "\n")
        return payload
