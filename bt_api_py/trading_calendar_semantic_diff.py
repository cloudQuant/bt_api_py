"""Connect an explicitly supplied local version and candidate to CTP comparison.

This does not read CURRENT, authenticate sources, or authorize publication.
A caller must hold the appropriate policy/current-version lock and revalidate
trusted source coverage and freshness before any separate publication.
"""

from __future__ import annotations

from bt_api_py.trading_calendar_store import CalendarSnapshot


def compare_calendar_version(
    current: CalendarSnapshot,
    candidate: dict,
    *,
    scopes,
    start,
    end,
    source_text_before=None,
    source_text_after=None,
):
    """Compare detached contents, without treating store hashes as source facts."""
    from bt_api_ctp.calendar_semantic_diff import CalendarSemanticDiff, compare_calendar_semantics

    if type(current) is not CalendarSnapshot:
        return CalendarSemanticDiff("UNKNOWN", "INVALID_CURRENT_VERSION")
    return compare_calendar_semantics(
        current.content,
        candidate,
        scopes=scopes,
        start=start,
        end=end,
        source_text_before=source_text_before,
        source_text_after=source_text_after,
    )
