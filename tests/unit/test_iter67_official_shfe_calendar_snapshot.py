"""Official SHFE calendar snapshot shape: official closure days + rb2701 windows."""
from datetime import date

from bt_api_ctp.calendar_snapshot import CalendarSnapshot

OFFICIAL = "SHFE.OFFICIAL.HOLIDAY.2026"


def content():
    return {
        "sources": [
            {"id": OFFICIAL, "description": "SHFE official 2026 holiday page (candidate; UNVERIFIED)"},
            {"id": "SYNTHETIC_HOURS.shfe.rb2701", "description": "Synthetic rb2701 hours; not official"},
        ],
        "trading_days": [
            {"venue": "SHFE", "day": "2026-01-01", "status": "closed", "source_ids": [OFFICIAL]},
            {"venue": "SHFE", "day": "2026-01-05", "status": "open", "source_ids": [OFFICIAL]},
        ],
        "trading_hours": [
            {
                "venue": "SHFE", "kind": "future", "product": "rb", "contracts": ["rb2701"],
                "valid_from": "2026-01-01", "valid_to": "2026-12-31",
                "day_windows": [
                    {"id": "day_am", "start": "09:00", "end": "10:15", "end_day_offset": 0, "phase": "continuous"},
                    {"id": "day_pm", "start": "13:30", "end": "15:00", "end_day_offset": 0, "phase": "continuous"},
                ],
                "night_windows": [
                    {"id": "night", "start": "21:00", "end": "01:00", "end_day_offset": 1, "phase": "continuous"},
                ],
                "night_mappings": [
                    {"trading_day": "2026-01-05", "start_date": None,
                     "source_ids": ["SYNTHETIC_HOURS.shfe.rb2701"]},
                ],
                "source_ids": ["SYNTHETIC_HOURS.shfe.rb2701"],
                "windows_complete": True, "exceptions_complete": True,
            }
        ],
        "exceptions": [],
    }


def test_official_shfe_days_and_rb2701_windows_are_explicit():
    snap = CalendarSnapshot(content(), calendar_ref="official-shfe-2026-v1")
    closed = snap.query_venue_day(venue="SHFE", trading_day=date(2026, 1, 1))
    assert (closed.status, closed.day_status) == ("COMPLETE", "closed")
    opened = snap.query_venue_day(venue="SHFE", trading_day=date(2026, 1, 5))
    assert (opened.status, opened.day_status) == ("COMPLETE", "open")
    result = snap.query(venue="SHFE", kind="future", product="rb", contract="rb2701",
                        trading_day=date(2026, 1, 5))
    assert result.status == "COMPLETE" and result.reason == "EXPLICIT_DATA"
    assert [w.window_id for w in result.windows] == ["day_am", "day_pm"]
