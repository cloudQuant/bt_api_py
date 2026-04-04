from __future__ import annotations

import json
import uuid
from dataclasses import asdict, is_dataclass
from datetime import date, datetime
from enum import Enum
from typing import Any

CHANNEL_EVENT = "event"
CHANNEL_MARKET = "market"


def make_request_id() -> str:
    return uuid.uuid4().hex


def dumps_message(payload: dict[str, Any]) -> bytes:
    return json.dumps(_normalize(payload), ensure_ascii=False, separators=(",", ":")).encode(
        "utf-8"
    )


def loads_message(payload: bytes) -> dict[str, Any]:
    return json.loads(payload.decode("utf-8"))


def _normalize(value: Any) -> Any:
    if is_dataclass(value):
        return _normalize(asdict(value))
    if isinstance(value, dict):
        return {str(key): _normalize(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_normalize(item) for item in value]
    if isinstance(value, (datetime, date)):
        return value.isoformat()
    if isinstance(value, Enum):
        return value.value
    return value
