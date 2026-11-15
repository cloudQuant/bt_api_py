"""Deterministic fictional calendar inputs for isolated simulation only.

These schedules, contracts, holidays and exceptions are invented.  They are
neither exchange data nor a complete instrument inventory.  The returned
review data is unverified and must never be used for account trading.
"""

from datetime import date, timedelta

VENUES = ("SHFE", "INE", "DCE", "CZCE", "CFFEX", "GFEX")
SOURCE = {"id": "MOCK.simulation_only", "description": "MOCK fictional schedules; SIMULATION_ONLY"}
LABEL = {"data_origin": "MOCK", "environment": "SIMULATION_ONLY"}


def build(year):
    """Return a /2 review input and a small matching supplement for two years."""
    if type(year) is not int or not 1901 <= year <= 9998:
        raise ValueError("invalid year")
    first, last = date(year, 1, 1), date(year + 1, 12, 31)
    dates = [first + timedelta(days=n) for n in range((last - first).days + 1)]
    # Invented holiday deliberately has no relationship to exchange holidays.
    holidays = {date(y, 1, 15) for y in (year, year + 1)}
    is_open = lambda day: day.weekday() < 5 and day not in holidays  # noqa: E731
    exception_day = next(day for day in dates if day.month == 3 and is_open(day))
    shards = []
    for venue in VENUES:
        layers = {
            "sources": [SOURCE.copy()],
            "trading_days": [],
            "trading_hours": [],
            "exceptions": [],
        }
        layers["trading_days"] = [
            {
                "venue": venue,
                "day": day.isoformat(),
                "status": "open" if is_open(day) else "closed",
                "source_ids": [SOURCE["id"]],
            }
            for day in dates
        ]
        requirements = []
        for kind in ("future", "option"):
            product = "mock_future" if kind == "future" else "mock_option"
            contract = f"mock_{venue.lower()}_{kind}_contract"
            windows = [
                {
                    "id": "day_am",
                    "phase": "continuous",
                    "start": "09:00",
                    "end": "10:15",
                    "end_day_offset": 0,
                },
                {
                    "id": "day_pm",
                    "phase": "continuous",
                    "start": "10:30",
                    "end": "11:30" if kind == "future" else "11:00",
                    "end_day_offset": 0,
                },
                {
                    "id": "afternoon",
                    "phase": "continuous",
                    "start": "13:30",
                    "end": "15:00" if kind == "future" else "14:45",
                    "end_day_offset": 0,
                },
            ]
            night = [
                {
                    "id": "night",
                    "phase": "continuous",
                    "start": "21:00",
                    "end": "01:00" if kind == "future" else "23:00",
                    "end_day_offset": 1 if kind == "future" else 0,
                }
            ]
            mappings = []
            for day in dates:
                if is_open(day):
                    # Fictional Friday-night ownership is explicit for an ordinary
                    # Monday; a holiday in the gap suppresses the night session.
                    previous_open = day - timedelta(days=3 if day.weekday() == 0 else 1)
                    gap = [
                        previous_open + timedelta(days=n)
                        for n in range(1, (day - previous_open).days)
                    ]
                    local = (
                        previous_open.isoformat()
                        if previous_open >= first
                        and is_open(previous_open)
                        and not any(item in holidays for item in gap)
                        else None
                    )
                    mappings.append(
                        {
                            "trading_day": day.isoformat(),
                            "start_date": local,
                            "source_ids": [SOURCE["id"]],
                        }
                    )
            layers["trading_hours"].append(
                {
                    "venue": venue,
                    "kind": kind,
                    "product": product,
                    "contracts": [contract],
                    "valid_from": first.isoformat(),
                    "valid_to": last.isoformat(),
                    "day_windows": windows,
                    "night_windows": night,
                    "night_mappings": mappings,
                    "windows_complete": True,
                    "exceptions_complete": True,
                    "source_ids": [SOURCE["id"]],
                }
            )
            requirements.append(
                {
                    "kind": kind,
                    "product": product,
                    "contract": contract,
                    "start_day": first.isoformat(),
                    "end_day": last.isoformat(),
                }
            )
            layers["exceptions"].append(
                {
                    "venue": venue,
                    "kind": kind,
                    "product": product,
                    "contract": contract if kind == "future" else None,
                    "trading_day": exception_day.isoformat(),
                    "action": "close_day" if kind == "future" else "cancel_window",
                    "window_id": None if kind == "future" else "day_pm",
                    "source_ids": [SOURCE["id"]],
                }
            )
        shards.append(
            {
                "venue": venue,
                "coverage_start": first.isoformat(),
                "coverage_end": last.isoformat(),
                "layers": layers,
                "requirements": requirements,
                "unresolved": [],
            }
        )
    supplement = {
        "sources": [SOURCE.copy()],
        "trading_days": [shards[0]["layers"]["trading_days"][0].copy()],
        "trading_hours": [],
        "exceptions": [],
    }
    return {
        "shards": shards,
        "source_texts": {SOURCE["id"]: "MOCK fictional data; SIMULATION_ONLY; not venue evidence"},
        "metadata": LABEL.copy(),
    }, supplement
