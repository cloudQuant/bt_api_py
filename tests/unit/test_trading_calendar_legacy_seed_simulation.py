"""Explicit old-rule mock expectations; none authenticate venue schedules."""

import hashlib
import json
from datetime import UTC, date, datetime, timedelta

import pytest
from bt_api_base.trading_session_protocol import TradingPhase
from bt_api_ctp.calendar_snapshot import CalendarSnapshot
from bt_api_ctp.simulation_calendar_ports import (
    SimulationCalendarUnavailable,
    SimulationMapping,
    SimulationOwnerClosure,
    create_prepared_simulation_calendar_ports,
)

from bt_api_py import trading_calendar_legacy_seed_simulation as seed
from bt_api_py.trading_calendar_binding import SimulationCalendarBinding
from tests.calendar_test_store import FixtureCalendarStore


def _symlink(link, target, *, target_is_directory=False):
    """Create a symlink, skipping on hosts without the privilege (Windows)."""
    import os as _os

    try:
        link.symlink_to(target, target_is_directory=target_is_directory)
    except OSError as exc:
        if _os.name == "nt" and getattr(exc, "winerror", None) == 1314:
            pytest.skip("symlink creation requires privilege on this host")
        raise


class Gate:
    def verify(self, event, context):
        raise AssertionError("no activation")


def canonical(value):
    return json.dumps(value, sort_keys=True, ensure_ascii=False, separators=(",", ":")).encode()


@pytest.fixture(scope="module")
def bundle():
    return seed.build_seed(mode="SIMULATION_ONLY")


@pytest.fixture(scope="module")
def prepared(tmp_path_factory, bundle):
    root = tmp_path_factory.mktemp("mock-legacy")
    store = FixtureCalendarStore(root)
    value = json.loads(bundle.payload)
    result = store.publish(
        value["snapshot"],
        publication_id="mock-p1",
        version_id="mock-v1",
        coverage_manifest_bytes=canonical(value["manifest"]),
        coverage_manifest_sha256=value["manifest_sha256"],
        binding_bytes=canonical(value["binding"]),
        binding_sha256=value["binding_sha256"],
    )
    assert result.visibility == "COMMITTED"
    bound = SimulationCalendarBinding(
        mode="SIMULATION_ONLY",
        store=store,
        snapshot_factory=CalendarSnapshot,
        gate=Gate(),
        route="mock-route",
        source_generation="mock-gen",
        trading_day=date(2026, 1, 6),
        last_sequence=1,
    )
    view = bound.capture_read_view(mode="SIMULATION_ONLY")
    assert view.status().version_sha256 == value["version_sha256"]
    meta = value["metadata"]
    horizon = meta["comparable_horizon_shanghai"]
    closure = SimulationOwnerClosure(
        value["version_sha256"],
        datetime.fromisoformat(horizon["start"]),
        datetime.fromisoformat(horizon["end"]),
        date.fromisoformat(meta["owner_start"]),
        date.fromisoformat(meta["owner_end"]),
        meta["max_advance_days"],
        meta["max_spill_days"],
    )
    mappings = tuple(
        SimulationMapping(
            row["legacy_instrument_id"],
            row["venue"],
            row["kind"],
            row["product"],
            row["mock_contract"],
        )
        for row in value["mappings"]
    )
    ports = create_prepared_simulation_calendar_ports(
        mode="SIMULATION_ONLY",
        view=view,
        mappings=mappings,
        closure=closure,
        required_instruments=tuple(row.instrument_id for row in mappings),
        required_years=tuple((venue, 2026) for venue in ("SHFE", "INE", "CZCE", "DCE")),
    )
    return value, view, ports


def test_seed_exact_41_scopes_dates_and_source_declaration(bundle):
    assert seed.verify_seed(bundle, mode="SIMULATION_ONLY")
    value = json.loads(bundle.payload)
    meta = value["metadata"]
    assert (meta["environment"], meta["data_origin"], meta["source_id"]) == (
        "SIMULATION_ONLY",
        "MOCK_LEGACY_DERIVED",
        "MOCK.simulation_only",
    )
    assert meta["source_authentication"] == meta["identity_authentication"] == "NOT_PROVEN"
    assert (
        meta["independent_venue_evidence"] == "NOT_PROVEN" and meta["trade_authorization"] == "NONE"
    )
    assert (
        meta["store_metadata_binding"]
        == "JOIN_SEED_BY_VERSION_SHA256_METADATA_NOT_IN_STORE_SNAPSHOT"
    )
    expected = {
        "SHFE": {
            "rb",
            "hc",
            "bu",
            "ru",
            "fu",
            "sp",
            "cu",
            "al",
            "zn",
            "pb",
            "ni",
            "sn",
            "ss",
            "au",
            "ag",
            "wr",
        },
        "INE": {"sc"},
        "CZCE": {"SA", "FG", "MA", "TA", "SR", "CF", "RM", "OI", "AP", "CJ"},
        "DCE": {"i", "j", "jm", "m", "y", "p", "a", "c", "l", "v", "pp", "eg", "jd", "lh"},
    }
    assert len(value["mappings"]) == 41
    for venue, products in expected.items():
        assert {item["product"] for item in value["mappings"] if item["venue"] == venue} == products
        rows = [row for row in value["snapshot"]["trading_days"] if row["venue"] == venue]
        assert len(rows) == 365 and len({row["day"] for row in rows}) == 365
        assert sum(row["status"] == "open" for row in rows) == 242
        assert meta["first_open_product_day_unknown"][venue] == "2026-01-05"
    assert len(value["snapshot"]["trading_hours"]) == 41
    assert all(row["valid_from"] == "2026-01-06" for row in value["snapshot"]["trading_hours"])
    assert value["snapshot"]["exceptions"]
    assert all(
        row["action"] == "cancel_window" and row["window_id"] == "day-auction"
        for row in value["snapshot"]["exceptions"]
    )
    assert {row["id"] for row in value["snapshot"]["sources"]} == {"MOCK.simulation_only"}


def test_first_open_is_not_declared_closed_and_outside_horizon_unknown(prepared):
    value, view, ports = prepared
    assert ports.limits.cached_windows > 0 and len(ports.mappings) == 41
    for venue in ("SHFE", "INE", "CZCE", "DCE"):
        day = view.query_venue_day(
            mode="SIMULATION_ONLY", venue=venue, trading_day=date(2026, 1, 5)
        )
        assert day.status == "COMPLETE" and day.day_status == "open"
        assert date(2026, 1, 5) in ports.expected_days.expected_trading_days(venue, 2026)
    row = next(row for row in value["mappings"] if row["product"] == "rb")
    first = view.query(
        venue="SHFE",
        kind="future",
        product="rb",
        contract=row["mock_contract"],
        trading_day=date(2026, 1, 5),
    )
    assert first.result.status == "UNKNOWN" and first.result.reason == "COVERAGE_INSUFFICIENT"
    unknown_contract = view.query(
        venue="SHFE",
        kind="future",
        product="rb",
        contract="mock_typo",
        trading_day=date(2026, 6, 8),
    )
    assert unknown_contract.result.status == "OUT_OF_SCOPE"
    assert (
        ports.schedule.session_state("rb2601", datetime(2026, 1, 1, tzinfo=UTC)).phase
        is TradingPhase.UNKNOWN
    )
    assert (
        ports.schedule.session_state("rb2601", datetime(2026, 12, 31, tzinfo=UTC)).phase
        is TradingPhase.UNKNOWN
    )
    with pytest.raises(SimulationCalendarUnavailable):
        ports.schedule.windows(
            "rb2601", datetime(2026, 12, 31, tzinfo=UTC), datetime(2027, 1, 1, tzinfo=UTC)
        )


def test_hand_declared_weekend_owner_and_auction_boundaries(prepared):
    _, _, ports = prepared
    schedule = ports.schedule
    auction = datetime(2026, 6, 5, 12, 55, tzinfo=UTC)  # Friday 20:55 Shanghai
    night = datetime(2026, 6, 5, 13, 0, tzinfo=UTC)
    end = datetime(2026, 6, 5, 15, 0, tzinfo=UTC)
    assert schedule.session_state("rb2601", auction).phase is TradingPhase.AUCTION
    assert schedule.session_state("rb2601", night).phase is TradingPhase.CONTINUOUS
    assert schedule.session_state("rb2601", night).trading_day == date(2026, 6, 8)
    assert (
        schedule.session_state("rb2601", end - timedelta(microseconds=1)).phase
        is TradingPhase.CONTINUOUS
    )
    assert schedule.session_state("rb2601", end).phase is TradingPhase.CLOSED
    monday_auction = datetime(2026, 6, 8, 0, 55, tzinfo=UTC)
    assert schedule.session_state("rb2601", monday_auction).phase is TradingPhase.CLOSED
    assert (
        schedule.session_state("rb2601", monday_auction + timedelta(minutes=5)).phase
        is TradingPhase.CONTINUOUS
    )


def test_hand_declared_spring_festival_night_suppression_and_day_auction(prepared):
    _, _, ports = prepared
    schedule = ports.schedule
    friday_night = datetime(2026, 2, 13, 13, tzinfo=UTC)
    assert schedule.session_state("rb2601", friday_night).phase is TradingPhase.CLOSED
    assert schedule.windows("rb2601", friday_night, friday_night + timedelta(minutes=5)) == ()
    auction = datetime(2026, 2, 24, 0, 55, tzinfo=UTC)
    assert schedule.session_state("rb2601", auction).phase is TradingPhase.AUCTION
    assert (
        schedule.session_state("rb2601", auction + timedelta(minutes=5)).phase
        is TradingPhase.CONTINUOUS
    )


@pytest.mark.parametrize(
    "alias,end",
    [
        ("cu2601", datetime(2026, 6, 5, 17, tzinfo=UTC)),
        ("au2601", datetime(2026, 6, 5, 18, 30, tzinfo=UTC)),
    ],
)
def test_hand_declared_midnight_crossing_is_half_open(prepared, alias, end):
    _, _, ports = prepared
    before = ports.schedule.session_state(alias, end - timedelta(microseconds=1))
    after = ports.schedule.session_state(alias, end)
    assert before.phase is TradingPhase.CONTINUOUS and before.trading_day == date(2026, 6, 8)
    assert after.phase is TradingPhase.CLOSED


def test_hand_declared_day_only_auction_and_break(prepared):
    _, _, ports = prepared
    schedule = ports.schedule
    auction = datetime(2026, 6, 8, 0, 55, tzinfo=UTC)
    assert schedule.session_state("wr2601", auction).phase is TradingPhase.AUCTION
    assert (
        schedule.session_state("wr2601", auction + timedelta(minutes=5)).phase
        is TradingPhase.CONTINUOUS
    )
    break_start = datetime(2026, 6, 8, 2, 15, tzinfo=UTC)
    assert (
        schedule.session_state("wr2601", break_start - timedelta(microseconds=1)).phase
        is TradingPhase.CONTINUOUS
    )
    assert schedule.session_state("wr2601", break_start).phase is TradingPhase.CLOSED
    assert (
        schedule.session_state("wr2601", break_start + timedelta(minutes=15)).phase
        is TradingPhase.CONTINUOUS
    )


@pytest.mark.parametrize("change", ["profile", "source", "scope", "missing"])
def test_rehashed_seed_tampering_fails_rebuild_verification(bundle, change):
    value = json.loads(bundle.payload)
    if change == "profile":
        value["mappings"][0]["product"] = "typo"
    elif change == "source":
        value["metadata"]["legacy_source_sha256"]["trading_session_schedule.py"] = "0" * 64
    elif change == "scope":
        value["snapshot"]["trading_hours"][0]["valid_to"] = "2026-12-01"
    else:
        value["mappings"].pop()
    payload = canonical(value)
    tampered = seed.LegacySeedBundle(payload, hashlib.sha256(payload).hexdigest())
    assert not seed.verify_seed(tampered, mode="SIMULATION_ONLY")


@pytest.mark.parametrize(
    "name", ["trading_session_schedule", "futures_expected_calendar_provider", "calendar_snapshot"]
)
def test_live_source_sha_mismatch_is_rejected(monkeypatch, bundle, name):
    original = seed._module_digest
    monkeypatch.setattr(
        seed,
        "_module_digest",
        lambda module: "0" * 64 if module.__name__.endswith(name) else original(module),
    )
    with pytest.raises(seed.LegacySeedError, match="LEGACY_SOURCE_SHA_MISMATCH"):
        seed.build_seed(mode="SIMULATION_ONLY")
    assert not seed.verify_seed(bundle, mode="SIMULATION_ONLY")


def test_changed_runtime_profile_and_open_days_are_rejected(monkeypatch):
    from bt_api_ctp import futures_expected_calendar_provider, trading_session_schedule

    with monkeypatch.context() as patch:
        wrong = dict(trading_session_schedule._PRODUCT_NIGHT_END)
        wrong[("SHFE", "rb")] = None
        patch.setattr(trading_session_schedule, "_PRODUCT_NIGHT_END", wrong)
        with pytest.raises(seed.LegacySeedError, match="LEGACY_PROFILE_MISMATCH"):
            seed.build_seed(mode="SIMULATION_ONLY")
    original = futures_expected_calendar_provider.expected_trading_days
    monkeypatch.setattr(
        futures_expected_calendar_provider,
        "expected_trading_days",
        lambda venue, year: original(venue, year)[1:],
    )
    with pytest.raises(seed.LegacySeedError, match="LEGACY_DAY_SCOPE_UNAVAILABLE"):
        seed.build_seed(mode="SIMULATION_ONLY")


def test_explicit_save_is_exclusive_and_no_parent_is_created(tmp_path, bundle):
    target = tmp_path / "mock-seed.json"
    seed.save_seed(str(target), bundle, mode="SIMULATION_ONLY")
    assert target.read_bytes() == bundle.payload
    assert (
        seed.load_seed(str(target), expected_sha256=bundle.sha256, mode="SIMULATION_ONLY") == bundle
    )
    with pytest.raises(seed.LegacySeedError, match="SIMULATION_SEED_LOAD_UNAVAILABLE"):
        seed.load_seed(str(target), expected_sha256="0" * 64, mode="SIMULATION_ONLY")
    link = tmp_path / "seed-link"
    _symlink(link, target)
    with pytest.raises(seed.LegacySeedError, match="SIMULATION_SEED_LOAD_UNAVAILABLE"):
        seed.load_seed(str(link), expected_sha256=bundle.sha256, mode="SIMULATION_ONLY")
    with pytest.raises(seed.LegacySeedError, match="SIMULATION_SEED_SAVE_UNKNOWN"):
        seed.save_seed(str(target), bundle, mode="SIMULATION_ONLY")
    assert target.read_bytes() == bundle.payload
    assert (
        seed.inspect_saved_seed(
            str(target), expected_sha256=bundle.sha256, mode="SIMULATION_ONLY"
        ).status
        == "HASH_MATCH"
    )
    absent = tmp_path / "absent" / "seed.json"
    with pytest.raises(seed.LegacySeedError):
        seed.save_seed(str(absent), bundle, mode="SIMULATION_ONLY")
    assert not absent.parent.exists()
    assert not seed.verify_seed(bundle, mode="LIVE")
    rejected = tmp_path / "invalid-mode.json"
    with pytest.raises(seed.LegacySeedError, match="SIMULATION_SEED_SAVE_NOT_CREATED"):
        seed.save_seed(str(rejected), bundle, mode="LIVE")
    assert not rejected.exists()
    with pytest.raises(seed.LegacySeedError, match="SIMULATION_MODE_REQUIRED"):
        seed.build_seed(mode="LIVE")


@pytest.mark.parametrize("failure", ["fdopen", "write", "short", "flush", "fsync", "close"])
def test_created_save_failure_is_unknown_and_never_removed(tmp_path, bundle, monkeypatch, failure):
    target = tmp_path / "uncertain-seed.json"
    real_fdopen = seed.os.fdopen

    class FaultyStream:
        def __init__(self, wrapped):
            self.wrapped = wrapped

        def __enter__(self):
            return self

        def __exit__(self, typ, value, trace):
            self.wrapped.close()
            if failure == "close":
                raise OSError("private-close-error")

        def write(self, data):
            if failure == "write":
                self.wrapped.write(data[:64])
                raise OSError("private-write-error")
            if failure == "short":
                return self.wrapped.write(data[:64])
            return self.wrapped.write(data)

        def flush(self):
            if failure == "flush":
                raise OSError("private-flush-error")
            return self.wrapped.flush()

        def fileno(self):
            return self.wrapped.fileno()

    with monkeypatch.context() as patch:
        patch.setattr(seed, "verify_seed", lambda *_args, **_kwargs: True)
        if failure == "fdopen":

            def fail_fdopen(*args, **kwargs):
                raise OSError("private-fdopen-error")

            patch.setattr(seed.os, "fdopen", fail_fdopen)
        else:
            patch.setattr(seed.os, "fdopen", lambda fd, mode: FaultyStream(real_fdopen(fd, mode)))
        if failure == "fsync":

            def fail_fsync(_fd):
                raise OSError("private-fsync-error")

            patch.setattr(seed.os, "fsync", fail_fsync)
        with pytest.raises(seed.LegacySeedError, match="SIMULATION_SEED_SAVE_UNKNOWN") as info:
            seed.save_seed(str(target), bundle, mode="SIMULATION_ONLY")
        assert "private" not in str(info.value) and str(target) not in str(info.value)
    assert target.exists()
    result = seed.inspect_saved_seed(
        str(target), expected_sha256=bundle.sha256, mode="SIMULATION_ONLY"
    )
    expected = "HASH_MISMATCH" if failure in {"fdopen", "write", "short"} else "HASH_MATCH"
    assert result.status == expected
    assert result.seed_rebuild_verification == "NOT_RUN"
    assert result.source_authentication == result.durability == "NOT_PROVEN"
    # A repeated attempt cannot silently replace the retained result.
    retained = target.read_bytes()
    with pytest.raises(seed.LegacySeedError, match="SIMULATION_SEED_SAVE_UNKNOWN"):
        seed.save_seed(str(target), bundle, mode="SIMULATION_ONLY")
    assert target.read_bytes() == retained


def test_open_failure_is_conservatively_unknown_without_path_leak(tmp_path, bundle, monkeypatch):
    target = tmp_path / "private-parent" / "seed"
    with monkeypatch.context() as patch:
        patch.setattr(seed, "verify_seed", lambda *_args, **_kwargs: True)

        def failed_open(*args, **kwargs):
            raise PermissionError("private-open-error")

        patch.setattr(seed.os, "open", failed_open)
        with pytest.raises(seed.LegacySeedError, match="SIMULATION_SEED_SAVE_UNKNOWN") as info:
            seed.save_seed(str(target), bundle, mode="SIMULATION_ONLY")
        assert "private" not in str(info.value)
    assert not target.parent.exists()
    assert (
        seed.inspect_saved_seed(
            str(target), expected_sha256=bundle.sha256, mode="SIMULATION_ONLY"
        ).status
        == "UNAVAILABLE"
    )


class AbortWithoutNote(BaseException):
    add_note = None


@pytest.mark.parametrize("abort", [KeyboardInterrupt, SystemExit, AbortWithoutNote])
def test_interrupted_save_preserves_exception_and_unknown_note(
    tmp_path, bundle, monkeypatch, abort
):
    target = tmp_path / "interrupted.json"
    real_fdopen = seed.os.fdopen

    class InterruptStream:
        def __init__(self, wrapped):
            self.wrapped = wrapped

        def __enter__(self):
            return self

        def __exit__(self, *_):
            self.wrapped.close()

        def write(self, data):
            self.wrapped.write(data[:64])
            raise abort()

    with monkeypatch.context() as patch:
        patch.setattr(seed, "verify_seed", lambda *_args, **_kwargs: True)
        patch.setattr(seed.os, "fdopen", lambda fd, mode: InterruptStream(real_fdopen(fd, mode)))
        with pytest.raises(abort) as info:
            seed.save_seed(str(target), bundle, mode="SIMULATION_ONLY")
        if callable(getattr(info.value, "add_note", None)):
            assert any("SIMULATION_SEED_SAVE_UNKNOWN" in note for note in info.value.__notes__)
    assert target.exists()
    assert (
        seed.inspect_saved_seed(
            str(target), expected_sha256=bundle.sha256, mode="SIMULATION_ONLY"
        ).status
        == "HASH_MISMATCH"
    )


@pytest.mark.parametrize("unavailable", [0, None])
def test_missing_nofollow_fails_closed_and_does_not_create(
    tmp_path, bundle, monkeypatch, unavailable
):
    target = tmp_path / "missing-capability"
    # Simulate the platform lacking a usable no-follow flag.  On POSIX that is
    # os.O_NOFOLLOW; on Windows the capability comes from fdops, so neutralise
    # both and let _no_follow_available make the cross-platform decision.
    if hasattr(seed.os, "O_NOFOLLOW"):
        if unavailable is None:
            monkeypatch.delattr(seed.os, "O_NOFOLLOW")
        else:
            monkeypatch.setattr(seed.os, "O_NOFOLLOW", unavailable)
    monkeypatch.setattr(seed.fdops, "O_NOFOLLOW", unavailable or 0)
    with pytest.raises(seed.LegacySeedError, match="SIMULATION_SEED_SAVE_NOT_CREATED"):
        seed.save_seed(str(target), bundle, mode="SIMULATION_ONLY")
    assert not target.exists()
    assert (
        seed.inspect_saved_seed(
            str(target), expected_sha256=bundle.sha256, mode="SIMULATION_ONLY"
        ).status
        == "UNAVAILABLE"
    )
    with pytest.raises(seed.LegacySeedError, match="SIMULATION_SEED_LOAD_UNAVAILABLE"):
        seed.load_seed(str(target), expected_sha256=bundle.sha256, mode="SIMULATION_ONLY")


def test_trusted_parent_symlink_is_followed_but_final_symlink_is_not(tmp_path, bundle):
    parent = tmp_path / "trusted"
    parent.mkdir()
    alias = tmp_path / "parent-alias"
    _symlink(alias, parent, target_is_directory=True)
    via_parent = alias / "mock.json"
    seed.save_seed(str(via_parent), bundle, mode="SIMULATION_ONLY")
    report = seed.inspect_saved_seed(
        str(via_parent), expected_sha256=bundle.sha256, mode="SIMULATION_ONLY"
    )
    assert report.status == "HASH_MATCH"
    assert report.parent_path == "CALLER_TRUSTED_PARENT_SYMLINKS_MAY_BE_FOLLOWED"
    assert (parent / "mock.json").read_bytes() == bundle.payload
    final_link = tmp_path / "final-link"
    _symlink(final_link, parent / "mock.json")
    assert (
        seed.inspect_saved_seed(
            str(final_link), expected_sha256=bundle.sha256, mode="SIMULATION_ONLY"
        ).status
        == "UNAVAILABLE"
    )
    with pytest.raises(seed.LegacySeedError, match="SIMULATION_SEED_SAVE_UNKNOWN"):
        seed.save_seed(str(final_link), bundle, mode="SIMULATION_ONLY")


def test_interruption_immediately_after_open_is_unknown_and_retained(tmp_path, bundle, monkeypatch):
    import inspect
    import sys

    target = tmp_path / "interrupted-after-open"
    monkeypatch.setattr(seed, "verify_seed", lambda *_args, **_kwargs: True)
    lines, start_line = inspect.getsourcelines(seed.save_seed)
    marker = next(
        i for i, line in enumerate(lines) if line.strip() == 'stream = os.fdopen(int(owned_fd), "wb")'
    )

    def trace(frame, event, arg):
        if (
            frame.f_code is seed.save_seed.__code__
            and event == "line"
            and frame.f_lineno == start_line + marker
        ):
            raise KeyboardInterrupt()
        return trace

    sys.settrace(trace)
    try:
        with pytest.raises(KeyboardInterrupt) as info:
            seed.save_seed(str(target), bundle, mode="SIMULATION_ONLY")
    finally:
        sys.settrace(None)
    if callable(getattr(info.value, "add_note", None)):
        assert any("SIMULATION_SEED_SAVE_UNKNOWN" in note for note in info.value.__notes__)
    assert target.exists()
    assert (
        seed.inspect_saved_seed(
            str(target), expected_sha256=bundle.sha256, mode="SIMULATION_ONLY"
        ).status
        == "HASH_MISMATCH"
    )


def test_file_exists_error_after_successful_open_is_unknown(tmp_path, bundle, monkeypatch):
    import inspect
    import sys

    target = tmp_path / "created-before-exception"
    monkeypatch.setattr(seed, "verify_seed", lambda *_args, **_kwargs: True)
    lines, start_line = inspect.getsourcelines(seed.save_seed)
    marker = next(
        i for i, line in enumerate(lines) if line.strip() == 'stream = os.fdopen(int(owned_fd), "wb")'
    )

    def trace(frame, event, arg):
        if (
            frame.f_code is seed.save_seed.__code__
            and event == "line"
            and frame.f_lineno == start_line + marker
        ):
            raise FileExistsError("private-ambiguous-path")
        return trace

    sys.settrace(trace)
    try:
        with pytest.raises(seed.LegacySeedError, match="SIMULATION_SEED_SAVE_UNKNOWN") as info:
            seed.save_seed(str(target), bundle, mode="SIMULATION_ONLY")
    finally:
        sys.settrace(None)
    assert "private" not in str(info.value)
    assert target.exists() and target.read_bytes() == b""
    assert (
        seed.inspect_saved_seed(
            str(target), expected_sha256=bundle.sha256, mode="SIMULATION_ONLY"
        ).status
        == "HASH_MISMATCH"
    )
