"""Explicit CURRENT simulation-coverage notifications; not a production alert.

The caller chooses a trusted absolute calendar root, a local journal and a sender.
Path hashing distinguishes lexically normalized roots but does not authenticate physical
filesystem identity. Clocks, source and completeness are NOT_PROVEN. This is a
one-event-per-Shanghai-day simulation convention, not a final scheduling contract.
CURRENT can change after the final read; no lock is held across the sender call.
"""

from __future__ import annotations

import hashlib
import json
import os
from collections.abc import Callable
from dataclasses import replace
from datetime import datetime, timedelta
from pathlib import Path

from bt_api_py.trading_calendar_coverage_cli import report_coverage
from bt_api_py.trading_calendar_coverage_status import VENUES
from bt_api_py.trading_calendar_notifications import (
    EmailConfig,
    SimulationCoverageNotificationState,
    notify_calendar_event,
)


def _sha(value: object) -> str:
    return hashlib.sha256(
        json.dumps(value, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()


def notify_current_coverage(
    root: str,
    *,
    journal_path,
    mode: str,
    clock_error_seconds: float,
    config: EmailConfig,
    sender: Callable,
    threshold_days: int = 30,
    _clock=None,
) -> dict:
    """Notify per CURRENT warning, re-reading before reservation and sending.

    ``_clock`` is a deterministic test seam. A caller-declared clock error is
    used only for simulation arithmetic and is never a verified clock bound.
    """
    labels = {
        "environment": "SIMULATION_ONLY",
        "scope": "CURRENT",
        "real_health": "UNKNOWN",
        "source_authentication": "NOT_PROVEN",
        "completeness": "NOT_PROVEN",
        "clock_authentication": "NOT_PROVEN",
        "root_identity": "CALLER_DECLARED_PATH_SHA256_NOT_AUTHENTICATED",
        "cadence": "SIMULATION_ONCE_PER_SHANGHAI_DAY",
        "trading_authorization": "NONE",
    }
    try:
        if (
            type(root) is not str
            or root.startswith("//")
            or not Path(root).is_absolute()
            or ".." in Path(root).parts
        ):
            raise ValueError("invalid root")
        root = os.path.normpath(root)
        root_digest = _sha(root)

        def read():
            return report_coverage(
                root,
                mode=mode,
                clock_error_seconds=clock_error_seconds,
                threshold_days=threshold_days,
                _clock=_clock,
            )

        baseline = read()
        if (
            baseline.get("environment") != "SIMULATION_ONLY"
            or baseline.get("scope") != "SIMULATION_COVERAGE_DECLARATIONS_ONLY"
        ):
            raise ValueError("invalid report")
        rows = baseline.get("venues")
        if type(rows) is not list or len(rows) != len(VENUES):
            raise ValueError("invalid venue set")
        if any(
            type(row) is not dict or row.get("venue") != venue
            for venue, row in zip(VENUES, rows, strict=True)
        ):
            raise ValueError("invalid venue set")
        results = []
        partial = False
        for row in rows:
            venue = row["venue"]
            try:
                state = row.get("simulation_status")
                if state != "WARNING":
                    results.append(
                        {"venue": venue, "status": "NOT_SENT" if state == "OK" else "UNKNOWN"}
                    )
                    continue
                fields = ("venue", "version", "coverage_start", "coverage_end", "shanghai_date")
                if (
                    any(type(row.get(field)) is not str or not row[field] for field in fields)
                    or row.get("declaration") != "DECLARED"
                    or row.get("source_authentication") != "NOT_PROVEN"
                    or row.get("completeness") != "NOT_PROVEN"
                    or baseline.get("clock_authentication") != "NOT_PROVEN"
                    or baseline.get("threshold_days") != threshold_days
                    or type(row.get("simulation_days_remaining")) is not int
                    or row["simulation_days_remaining"] > threshold_days
                ):
                    results.append({"venue": venue, "status": "UNKNOWN"})
                    continue
                identity = {key: row[key] for key in fields if key != "shanghai_date"}
                identity.update({"root_sha256": root_digest, "threshold_days": threshold_days})
                event_id = "coverage-" + _sha(identity)
                scope_id = "root-" + root_digest
                expected_date = row["shanghai_date"]
                instant = datetime.fromisoformat(baseline["clock_instant_utc"])
                bound = timedelta(seconds=baseline["clock_error_seconds"])

                def reader(
                    kind,
                    scope,
                    event,
                    *,
                    expected=row.copy(),
                    expected_scope=scope_id,
                    expected_event=event_id,
                    expected_venue=venue,
                    expected_fields=fields,
                    date_value=expected_date,
                ):
                    if (
                        kind != "simulation_coverage"
                        or scope != expected_scope
                        or event != expected_event
                    ):
                        raise ValueError("identity mismatch")
                    observed = read()
                    matches = [
                        value
                        for value in observed.get("venues", ())
                        if value.get("venue") == expected_venue
                    ]
                    if (
                        observed.get("threshold_days") != threshold_days
                        or observed.get("clock_authentication") != "NOT_PROVEN"
                        or len(matches) != 1
                        or any(
                            matches[0].get(key) != expected.get(key)
                            for key in (
                                *expected_fields,
                                "declaration",
                                "simulation_status",
                                "source_authentication",
                                "completeness",
                                "simulation_days_remaining",
                            )
                        )
                    ):
                        raise ValueError("CURRENT changed")
                    return SimulationCoverageNotificationState(scope, event, date_value, "WARNING")

                email = replace(
                    config,
                    subject=f"SIMULATION_ONLY CURRENT coverage warning: {venue}",
                    body=(
                        f"SIMULATION_ONLY CURRENT; venue={venue}; version={row['version']}; "
                        f"coverage_end={row['coverage_end']}; remaining_days={row['simulation_days_remaining']}; "
                        f"threshold_days={threshold_days}; Shanghai_date={expected_date}. "
                        "Coverage, source, completeness and clock are NOT_PROVEN; real health is UNKNOWN."
                    ),
                )
                result = notify_calendar_event(
                    journal_path=journal_path,
                    kind="simulation_coverage",
                    scope_id=scope_id,
                    event_id=event_id,
                    config=email,
                    state_reader=reader,
                    sender=sender,
                    clock_instant=instant,
                    clock_error_bound=bound,
                    clock_evidence="CALLER_DECLARED_NOT_PROVEN",
                )
                results.append(
                    {
                        "venue": venue,
                        "status": result.status,
                        "reason": result.reason,
                        "journal_status": result.journal_status,
                    }
                )
            except Exception:
                partial = True
                results.append(
                    {"venue": venue, "status": "UNKNOWN", "reason": "NOTIFICATION_UNAVAILABLE"}
                )
        partial = partial or any(
            row["status"] == "UNKNOWN" or row.get("journal_status") == "UNKNOWN" for row in results
        )
        status = (
            ("PARTIAL" if any(row["status"] != "UNKNOWN" for row in results) else "UNKNOWN")
            if partial
            else "REPORTED"
        )
        return {**labels, "status": status, "venues": results}
    except Exception:
        return {**labels, "status": "UNKNOWN", "venues": []}
