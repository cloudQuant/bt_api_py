"""Neutral, non-authorizing projection of an unverified SDK execution event."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any, Dict


def project_unverified_execution_event_drop(venue: Any, event: Any) -> Dict[str, Any]:
    """Whitelist safe event identifiers before Store records an unverified drop."""
    if isinstance(event, Mapping):
        kind = event.get("kind")
        client_order_id = event.get("client_order_id")
        event_id = event.get("event_id")
    else:
        kind = None
        client_order_id = None
        event_id = None
    return {
        "kind": kind if type(kind) is str else "unknown",
        "client_order_id": (
            client_order_id
            if type(client_order_id) is str and client_order_id == client_order_id.strip()
            else None
        ),
        "exchange_name": venue if type(venue) is str else None,
        "event_id": event_id if type(event_id) is str else None,
    }


__all__ = ("project_unverified_execution_event_drop",)
