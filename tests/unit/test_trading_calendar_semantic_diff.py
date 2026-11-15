"""Local synthetic version assembly, without publishing a calendar."""

from copy import deepcopy
from datetime import date

from bt_api_ctp.calendar_semantic_diff import CalendarScope

from bt_api_py.trading_calendar_semantic_diff import compare_calendar_version
from bt_api_py.trading_calendar_store import CalendarSnapshot


def test_explicit_current_content_is_compared_without_using_its_hash():
    content = {
        "sources": [{"id": "a", "description": "synthetic"}],
        "trading_days": [
            {"venue": "X", "day": "2026-01-02", "status": "open", "source_ids": ["a"]}
        ],
        "trading_hours": [
            {
                "venue": "X",
                "kind": "future",
                "product": "cu",
                "contracts": ["c1"],
                "valid_from": "2026-01-02",
                "valid_to": "2026-01-02",
                "day_windows": [
                    {
                        "id": "d",
                        "phase": "continuous",
                        "start": "09:00",
                        "end": "11:00",
                        "end_day_offset": 0,
                    }
                ],
                "night_windows": [],
                "night_mappings": [
                    {"trading_day": "2026-01-02", "start_date": None, "source_ids": ["a"]}
                ],
                "source_ids": ["a"],
                "windows_complete": True,
                "exceptions_complete": True,
            }
        ],
        "exceptions": [],
    }
    original = deepcopy(content)
    current = CalendarSnapshot("synthetic", 1, "v1", "not-a-source-proof", content)
    kwargs = {
        "scopes": (CalendarScope("X", "future", "cu", "c1"),),
        "start": date(2026, 1, 2),
        "end": date(2026, 1, 2),
    }
    assert compare_calendar_version(current, deepcopy(content), **kwargs).reason == (
        "SOURCE_EVIDENCE_MISSING"
    )
    kwargs.update(
        source_text_before={"a": "synthetic source text"},
        source_text_after={"a": "synthetic source text"},
    )
    assert compare_calendar_version(current, deepcopy(content), **kwargs).status == "SAME"
    changed = deepcopy(content)
    changed["trading_hours"][0]["day_windows"][0]["end"] = "10:00"
    result = compare_calendar_version(current, changed, **kwargs)
    assert result.status == "CHANGED"
    assert result.publication == "NOT_AUTHORIZED"
    assert content == original
    assert compare_calendar_version(None, changed, **kwargs).status == "UNKNOWN"
