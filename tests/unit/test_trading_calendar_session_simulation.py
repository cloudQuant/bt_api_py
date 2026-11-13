"""Representative simulation-only phase checks against a single pinned snapshot."""

import json
from datetime import date, datetime, timedelta, timezone
from types import SimpleNamespace

import pytest
from bt_api_base.trading_session_protocol import TradingPhase, TradingSessionState
from bt_api_ctp.calendar_snapshot import CalendarSnapshot
from bt_api_ctp.trading_session_schedule import CtpTradingSessionSchedule

from bt_api_py.trading_calendar_composite import compose_simulated
from bt_api_py.trading_calendar_session_simulation import SimulationSessionEvaluator
from bt_api_py.trading_calendar_store import CalendarSnapshot as StoreSnapshot
from bt_api_py.trading_calendar_store import calendar_snapshot_digest
from bt_api_py.trading_calendar_update_simulation import VenueContent

UTC = timezone.utc  # noqa: UP017 - Python 3.9
FIRST = date(2026, 6, 5)  # Friday, the fictional night session's start date
OWNER = date(2026, 6, 8)  # Monday, explicitly declared owner day
LAST = date(2026, 6, 9)


def window(name, phase, start, end, offset=0):
    return {"id": name, "phase": phase, "start": start, "end": end, "end_day_offset": offset}


def dataset():
    return {
        "sources": [{"id": "mock", "description": "MOCK fictional SIMULATION_ONLY"}],
        "trading_days": [
            {"venue": "SHFE", "day": day.isoformat(), "status": "open", "source_ids": ["mock"]}
            for day in (FIRST, OWNER, LAST)
        ],
        "trading_hours": [
            {
                "venue": "SHFE",
                "kind": "future",
                "product": "rb",
                "contracts": ["rb2601", "rb2602"],
                "valid_from": FIRST.isoformat(),
                "valid_to": LAST.isoformat(),
                "day_windows": [
                    window("morning_1", "continuous", "09:00", "10:15"),
                    window("morning_2", "continuous", "10:30", "11:30"),
                    window("afternoon", "continuous", "13:30", "15:00"),
                ],
                "night_windows": [
                    window("night_auction", "auction", "20:55", "21:00"),
                    window("night", "continuous", "21:00", "23:00"),
                ],
                "night_mappings": [
                    {
                        "trading_day": day.isoformat(),
                        "start_date": FIRST.isoformat() if day == OWNER else None,
                        "source_ids": ["mock"],
                    }
                    for day in (FIRST, OWNER, LAST)
                ],
                "windows_complete": True,
                "exceptions_complete": True,
                "source_ids": ["mock"],
            },
            {
                "venue": "SHFE",
                "kind": "option",
                "product": "mock_opt",
                "contracts": ["mock_opt_contract"],
                "valid_from": FIRST.isoformat(),
                "valid_to": LAST.isoformat(),
                "day_windows": [window("option_day", "continuous", "09:30", "10:30")],
                "night_windows": [],
                "night_mappings": [
                    {"trading_day": day.isoformat(), "start_date": None, "source_ids": ["mock"]}
                    for day in (FIRST, OWNER, LAST)
                ],
                "windows_complete": True,
                "exceptions_complete": True,
                "source_ids": ["mock"],
            },
        ],
        "exceptions": [
            {
                "venue": "SHFE",
                "kind": "future",
                "product": "rb",
                "contract": "rb2602",
                "trading_day": OWNER.isoformat(),
                "action": "close_day",
                "window_id": None,
                "source_ids": ["mock"],
            }
        ],
    }


def store_snapshot(data, *, version="v1", coverage_end=LAST, factory=CalendarSnapshot):
    bundle = compose_simulated(
        mode="SIMULATION_ONLY",
        venues=(VenueContent.from_layers("SHFE", FIRST, coverage_end, data),),
        snapshot_factory=factory,
    )
    assert bundle.status == "READY_SIMULATED"
    content = json.loads(bundle.snapshot_bytes)
    version_sha = calendar_snapshot_digest(
        content,
        coverage_manifest_bytes=bundle.manifest_bytes,
        coverage_manifest_sha256=bundle.manifest_sha256,
        binding_bytes=bundle.binding_bytes,
        binding_sha256=bundle.binding_sha256,
    )
    return StoreSnapshot(
        publication_id=f"pub_{version}",
        sequence=1,
        version_id=version,
        content_sha256=bundle.snapshot_sha256,
        content=content,
        coverage_status="DECLARED_UNVERIFIED",
        coverage_manifest_bytes=bundle.manifest_bytes,
        coverage_manifest_sha256=bundle.manifest_sha256,
        binding_bytes=bundle.binding_bytes,
        binding_sha256=bundle.binding_sha256,
        version_sha256=version_sha,
    )


class Store:
    def __init__(self, value):
        self.value = value
        self.reads = 0

    def read_current(self):
        self.reads += 1
        return self.value


def point(day, hour, minute=0):
    return datetime(day.year, day.month, day.day, hour, minute, tzinfo=UTC)


def evaluate(
    evaluator, at, *, owner=OWNER, contract="rb2601", kind="future", product="rb", venue="SHFE"
):
    return evaluator.evaluate(
        venue=venue, kind=kind, product=product, contract=contract, owner_day=owner, at_utc=at
    )


def test_day_night_and_auction_match_old_phase_with_unclipped_boundaries():
    snapshot = store_snapshot(dataset())
    evaluator = SimulationSessionEvaluator(mode="SIMULATION_ONLY", store=Store(snapshot))
    legacy = CtpTradingSessionSchedule()
    cases = [
        (point(OWNER, 1), TradingPhase.CONTINUOUS, "DAY", point(OWNER, 1), point(OWNER, 2, 15)),
        (
            point(FIRST, 12, 55),
            TradingPhase.AUCTION,
            "NIGHT",
            point(FIRST, 12, 55),
            point(FIRST, 13),
        ),
        (point(FIRST, 13), TradingPhase.CONTINUOUS, "NIGHT", point(FIRST, 13), point(FIRST, 15)),
    ]
    for at, phase, scope, start, end in cases:
        result = evaluate(evaluator, at)
        old = legacy.session_state("rb2601", at)
        assert result.phase is old.phase is phase and result.trading_day == old.trading_day == OWNER
        assert result.current_window.session_scope == scope
        assert (result.current_window.start_utc, result.current_window.end_utc) == (start, end)
        assert (old.current_window.start_utc, old.current_window.end_utc) == (start, end)
        assert result.mode == "SIMULATION_ONLY" and result.source_authentication == "NOT_PROVEN"
        assert not isinstance(result, TradingSessionState)
        assert not hasattr(result.current_window, "session_id")
    assert not hasattr(evaluator, "windows")


def test_half_open_end_and_unmapped_owner_are_unknown():
    evaluator = SimulationSessionEvaluator(
        mode="SIMULATION_ONLY", store=Store(store_snapshot(dataset()))
    )
    assert (
        evaluate(evaluator, point(OWNER, 1) - timedelta(microseconds=1)).phase
        is TradingPhase.UNKNOWN
    )
    assert evaluate(evaluator, point(OWNER, 2, 15)).phase is TradingPhase.UNKNOWN
    assert evaluate(evaluator, point(FIRST, 15)).phase is TradingPhase.UNKNOWN
    assert evaluate(evaluator, point(FIRST, 13), owner=FIRST).phase is TradingPhase.UNKNOWN
    assert evaluate(evaluator, point(OWNER, 1), contract="rb9999").phase is TradingPhase.UNKNOWN
    assert (
        evaluate(evaluator, point(OWNER, 1), owner=LAST + timedelta(days=1)).phase
        is TradingPhase.UNKNOWN
    )
    assert evaluate(evaluator, point(OWNER, 1), contract="rb2602").phase is TradingPhase.UNKNOWN
    assert evaluate(evaluator, point(OWNER, 1), venue="DCE").phase is TradingPhase.UNKNOWN
    assert evaluate(evaluator, datetime(2026, 6, 2, 1)).phase is TradingPhase.UNKNOWN


def test_friday_night_is_owned_by_monday_not_friday():
    assert FIRST.weekday() == 4 and OWNER.weekday() == 0
    evaluator = SimulationSessionEvaluator(
        mode="SIMULATION_ONLY", store=Store(store_snapshot(dataset()))
    )
    friday_night = point(FIRST, 13)
    result = evaluate(evaluator, friday_night, owner=OWNER)
    assert result.phase is TradingPhase.CONTINUOUS and result.trading_day == OWNER
    assert result.current_window.session_scope == "NIGHT"
    assert (result.current_window.start_utc, result.current_window.end_utc) == (
        friday_night,
        point(FIRST, 15),
    )
    wrong_owner = evaluate(evaluator, friday_night, owner=FIRST)
    assert wrong_owner.phase is TradingPhase.UNKNOWN and wrong_owner.current_window is None


def test_option_scope_is_explicit_and_has_own_original_window():
    evaluator = SimulationSessionEvaluator(
        mode="SIMULATION_ONLY", store=Store(store_snapshot(dataset()))
    )
    result = evaluate(
        evaluator,
        point(OWNER, 1, 30),
        kind="option",
        product="mock_opt",
        contract="mock_opt_contract",
    )
    assert result.phase is TradingPhase.CONTINUOUS
    assert result.current_window.session_scope == "DAY"
    assert (result.current_window.start_utc, result.current_window.end_utc) == (
        point(OWNER, 1, 30),
        point(OWNER, 2, 30),
    )
    assert (
        evaluate(
            evaluator,
            point(OWNER, 1, 30),
            kind="future",
            product="mock_opt",
            contract="mock_opt_contract",
        ).phase
        is TradingPhase.UNKNOWN
    )


def test_missing_mapping_fails_closed_and_missing_day_is_unknown():
    data = dataset()
    data["trading_hours"][0]["night_mappings"] = [
        m
        for m in data["trading_hours"][0]["night_mappings"]
        if m["trading_day"] != OWNER.isoformat()
    ]
    # Valid artifact hashes do not turn a missing night mapping into a valid snapshot.
    invalid = store_snapshot(
        data, factory=lambda *_args, **_kwargs: SimpleNamespace(query=lambda **_: None)
    )
    evaluator = SimulationSessionEvaluator(mode="SIMULATION_ONLY", store=Store(invalid))
    result = evaluate(evaluator, point(OWNER, 1))
    assert result.phase is TradingPhase.UNKNOWN and result.reason == "SNAPSHOT_UNAVAILABLE"
    missing = dataset()
    missing["trading_days"] = [
        row for row in missing["trading_days"] if row["day"] != LAST.isoformat()
    ]
    for row in missing["trading_hours"]:
        row["night_mappings"] = [
            m for m in row["night_mappings"] if m["trading_day"] != LAST.isoformat()
        ]
    evaluator = SimulationSessionEvaluator(
        mode="SIMULATION_ONLY", store=Store(store_snapshot(missing))
    )
    assert evaluate(evaluator, point(LAST, 1), owner=LAST).phase is TradingPhase.UNKNOWN


def test_explicit_closed_day_and_manifest_exclusion_remain_unknown():
    closed = dataset()
    closed["trading_days"][-1]["status"] = "closed"
    for row in closed["trading_hours"]:
        row["night_mappings"] = [
            m for m in row["night_mappings"] if m["trading_day"] != LAST.isoformat()
        ]
    evaluator = SimulationSessionEvaluator(
        mode="SIMULATION_ONLY", store=Store(store_snapshot(closed))
    )
    assert evaluate(evaluator, point(LAST, 1), owner=LAST).phase is TradingPhase.UNKNOWN
    clipped = SimulationSessionEvaluator(
        mode="SIMULATION_ONLY", store=Store(store_snapshot(dataset(), coverage_end=OWNER))
    )
    result = evaluate(clipped, point(LAST, 1), owner=LAST)
    assert result.phase is TradingPhase.UNKNOWN and result.reason == "COVERAGE_UNAVAILABLE"


def test_snapshot_is_detached_and_never_reloads_after_current_changes():
    first = store_snapshot(dataset(), version="v1")
    second_data = dataset()
    second_data["trading_hours"][0]["day_windows"][0]["end"] = "10:00"
    second = store_snapshot(second_data, version="v2")
    store = Store(first)
    evaluator = SimulationSessionEvaluator(mode="SIMULATION_ONLY", store=store)
    store.value = second
    first.content["trading_hours"][0]["day_windows"][0]["end"] = "09:30"
    result = evaluate(evaluator, point(OWNER, 1, 45))
    assert result.phase is TradingPhase.CONTINUOUS and result.calendar_ref == "v1"
    assert result.current_window.end_utc == point(OWNER, 2, 15)
    assert result.version_sha256 != second.version_sha256 and store.reads == 1


def test_explicit_simulation_mode_is_required():
    with pytest.raises(ValueError):
        SimulationSessionEvaluator(mode="LIVE", store=Store(store_snapshot(dataset())))
