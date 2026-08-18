"""Capability report contract (Task 1.3)."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

SUPPORT_STATUSES = ("installed", "loadable", "certified", "experimental", "retired")


@dataclass(frozen=True)
class CapabilityReport:
    """Read-only capability and support report for one exchange.

    ``operations`` maps an operation name to whether the venue supports it.
    ``status`` is one of the FR-08 support tiers.
    """

    exchange_name: str
    status: str
    operations: dict[str, bool] = field(default_factory=dict)
    raw: dict[str, Any] = field(default_factory=dict)

    def supports(self, operation: str) -> bool:
        return bool(self.operations.get(operation, False))
