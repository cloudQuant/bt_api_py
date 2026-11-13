"""Offline short-grid and recovery checks; no full annual scan is started."""

import fcntl
import json
import os
from datetime import UTC, datetime, timedelta

import pytest
from bt_api_base.trading_session_protocol import TradingPhase

from bt_api_py import trading_calendar_legacy_scan_simulation as scan


def decoded(result):
    return [code for _, length, code in result["runs"] for _ in range(length)]


def test_annual_grid_manifest_and_worker_limits(tmp_path):
    assert int((scan._END - scan._START) / scan._STEP) == 105120
    assert 41 * scan._POINTS == 4309920
    with pytest.raises(scan.SimulationScanError, match="SCAN_WORKER_LIMIT"):
        scan.run_scan(str(tmp_path / "unused"), workers=5)
    with pytest.raises(scan.SimulationScanError, match="SCAN_WORKER_LIMIT"):
        scan.run_scan(str(tmp_path / "unused"), workers=True)
    assert not (tmp_path / "unused").exists()


def test_real_seed_short_interval_friday_old_two_ports_conflict():
    start = datetime(2026, 6, 5, 12, 55, tzinfo=UTC)
    result = scan.compare_interval("rb2601", start, start + timedelta(minutes=15))
    assert result["status"] == "TEST_ONLY_NOT_ANNUAL"
    assert (
        result["source"] == "MOCK_LEGACY_DERIVED"
        and result["source_authentication"] == "NOT_PROVEN"
    )
    codes = decoded(result)
    assert len(codes) == 3
    # 21:00 Shanghai belongs to Monday June 8, while the old windows port is empty.
    assert codes[1][0:2] == "EE"
    assert codes[1][3] == "U" and codes[1][4] == "C"
    for counts in result["counts"]["dimensions"].values():
        assert sum(counts.values()) == 3


@pytest.mark.parametrize(
    "start,alias",
    [
        (datetime(2026, 2, 13, 12, 55, tzinfo=UTC), "rb2601"),
        (datetime(2026, 2, 24, 0, 55, tzinfo=UTC), "rb2601"),
        (datetime(2026, 6, 5, 16, 55, tzinfo=UTC), "cu2601"),
        (datetime(2026, 6, 5, 18, 25, tzinfo=UTC), "au2601"),
    ],
)
def test_real_seed_holiday_midnight_and_auction_short_grids(start, alias):
    result = scan.compare_interval(alias, start, start + timedelta(minutes=10))
    assert result["total"] == 2 and sum(length for _, length, _ in result["runs"]) == 2
    assert all(sum(row.values()) == 2 for row in result["counts"]["dimensions"].values())


def test_real_seed_start_and_end_outside_horizon_are_unknown():
    for point in (scan._START, scan._END - timedelta(minutes=5)):
        result = scan.compare_interval("rb2601", point, point + timedelta(minutes=5))
        assert decoded(result)[0][:4] == "UUUU"


def test_rle_counts_reject_gaps_mutations_and_empty_unknowns():
    runs = [[0, 2, "EEMU N".replace(" ", "")], [2, 1, "UUUUC"]]
    counts = scan._counts(runs, 3)
    assert counts["dimensions"]["phase"] == {"VALUE_EQUAL": 2, "MISMATCH": 0, "UNKNOWN": 1}
    assert counts["legacy_state_windows"]["CONFLICT"] == 1
    for bad in ([[1, 2, "EEEEN"]], [[0, 2, "EEEEN"], [2, 1, "EEEEN"]], [[0, 3, "EEEEX"]]):
        with pytest.raises(ValueError):
            scan._counts(bad, 3)


def test_fake_real_mismatch_and_two_unknowns_do_not_pass():
    class Schedule:
        def __init__(self, phase):
            self.phase = phase

        def session_state(self, alias, instant):
            from types import SimpleNamespace

            return SimpleNamespace(
                instrument_id=alias,
                at_utc=instant,
                phase=self.phase,
                current_window=None,
                trading_day=None,
                next_continuous=None,
                calendar_ref="ctp.trading-sessions.2026.v1",
            )

        def windows(self, alias, start, end):
            return ()

    class OtherPhase:
        value = "auction"

    # A malformed active state lacks a current window, and is UNKNOWN rather than equal.
    code = scan._compare(
        Schedule(TradingPhase.CLOSED), Schedule(OtherPhase()), "rb2601", scan._START
    )
    assert code == "UUUUU"
    assert (
        scan._compare(
            Schedule(TradingPhase.UNKNOWN), Schedule(TradingPhase.UNKNOWN), "rb2601", scan._START
        )
        == "UUUUU"
    )


def test_deliberately_changed_mock_calendar_produces_mismatch():
    from bt_api_base.trading_session_protocol import (
        TradingSessionState,
        TradingWindow,
        TradingWindowKind,
    )

    instant = datetime(2026, 6, 8, 1, tzinfo=UTC)

    class Schedule:
        def __init__(self, kind):
            self.window = TradingWindow(
                instant.date(), kind, instant, instant + timedelta(minutes=5), "day"
            )

        def session_state(self, alias, at):
            return TradingSessionState(
                alias,
                at,
                TradingPhase(self.window.kind.value),
                instant.date(),
                self.window,
                None,
                "ctp.trading-sessions.2026.v1"
                if self.window.kind is TradingWindowKind.CONTINUOUS
                else "mock-legacy-v1",
            )

        def windows(self, alias, start, end):
            return (self.window,)

    old = Schedule(TradingWindowKind.CONTINUOUS)
    changed = Schedule(TradingWindowKind.AUCTION)
    code = scan._compare(old, changed, "rb2601", instant)
    assert code == "MEUMN"


def test_short_run_resume_integrity_and_no_overwrite(tmp_path):
    root = str(tmp_path / "run")
    start = datetime(2026, 6, 5, 12, 55, tzinfo=UTC)
    init = scan._make_run(
        root, test_grid=(start, start + timedelta(minutes=15)), test_aliases=("rb2601", "cu2601")
    )
    assert init["scope"] == "TEST_ONLY_NOT_ANNUAL" and init["expected_points"] == 6
    with pytest.raises(scan.SimulationScanError):
        scan.init_run(root)
    result = scan.run_scan(root, workers=2, _allow_test=True)
    assert result["status"] == "SCANNED_NOT_ACCEPTANCE"
    before = {p.name: p.read_bytes() for p in (tmp_path / "run").iterdir()}
    assert scan.run_scan(root, workers=2, _allow_test=True)["status"] == "SCANNED_NOT_ACCEPTANCE"
    assert {p.name: p.read_bytes() for p in (tmp_path / "run").iterdir()} == before
    summary = scan.aggregate(root, _allow_test=True)
    assert summary["status"] == "SCANNED_NOT_ACCEPTANCE" and summary["total"] == 6
    assert summary["scope"] == "TEST_ONLY_NOT_ANNUAL"
    assert len(summary["dates"]) == 1460
    with pytest.raises(scan.SimulationScanError):
        scan.aggregate(root)


def test_corrupt_or_unresolved_shard_is_not_overwritten_and_other_runs(tmp_path):
    root = str(tmp_path / "run")
    start = datetime(2026, 6, 5, 13, tzinfo=UTC)
    scan._make_run(
        root, test_grid=(start, start + timedelta(minutes=5)), test_aliases=("rb2601", "cu2601")
    )
    dirfd = os.open(root, os.O_RDONLY | os.O_DIRECTORY)
    try:
        manifest = scan._read_object(dirfd, "manifest.json")
        scan._write_new(
            dirfd,
            "claim-rb2601.json",
            scan._envelope(scan._claim_body(scan._sha(scan._json(manifest)), "rb2601", 1)),
        )
    finally:
        os.close(dirfd)
    claim = (tmp_path / "run" / "claim-rb2601.json").read_bytes()
    result = scan.run_scan(root, workers=1, _allow_test=True)
    assert result["shards"] == {"rb2601": "UNKNOWN", "cu2601": "COMPLETE"}
    assert (tmp_path / "run" / "claim-rb2601.json").read_bytes() == claim
    assert not (tmp_path / "run" / "shard-rb2601.json").exists()
    with pytest.raises(scan.SimulationScanError, match="SCAN_AGGREGATE_INCOMPLETE"):
        scan.aggregate(root, _allow_test=True)
    shard = tmp_path / "run" / "shard-cu2601.json"
    shard.write_bytes(shard.read_bytes().replace(b'"total":1', b'"total":2', 1))
    before = shard.read_bytes()
    assert scan.run_scan(root, workers=1, _allow_test=True)["status"] == "INCOMPLETE"
    assert shard.read_bytes() == before


def test_nonblocking_lock_and_manifest_seed_tampering(tmp_path):
    root = str(tmp_path / "run")
    start = datetime(2026, 6, 5, 13, tzinfo=UTC)
    scan._make_run(root, test_grid=(start, start + timedelta(minutes=5)), test_aliases=("rb2601",))
    with open(tmp_path / "run" / ".lock", "rb") as handle:
        fcntl.flock(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
        try:
            with pytest.raises(scan.SimulationScanError, match="SCAN_BUSY"):
                scan.inspect_run(root, _allow_test=True)
        finally:
            fcntl.flock(handle, fcntl.LOCK_UN)
    manifest = tmp_path / "run" / "manifest.json"
    original = manifest.read_bytes()
    manifest.write_bytes(original.replace(b'"step_minutes":5', b'"step_minutes":6'))
    with pytest.raises(scan.SimulationScanError, match="SCAN_IDENTITY_UNAVAILABLE"):
        scan.inspect_run(root, _allow_test=True)
    manifest.write_bytes(original)
    data = tmp_path / "run" / "seed.json"
    data.write_bytes(data.read_bytes() + b" ")
    with pytest.raises(scan.SimulationScanError, match="SCAN_IDENTITY_UNAVAILABLE"):
        scan.inspect_run(root, _allow_test=True)


@pytest.fixture(scope="module")
def real_short_ports():
    bundle = scan.seed.build_seed(mode="SIMULATION_ONLY")
    start = datetime(2026, 6, 5, 12, 55, tzinfo=UTC)
    manifest = scan._manifest(
        bundle, start=start, end=start + timedelta(minutes=5), aliases=("rb2601",), test=True
    )
    with scan._temporary_store(bundle) as root:
        view = scan._view(root, manifest, bundle)
        yield bundle, manifest, view


def test_hand_declared_legacy_and_seeded_session_boundaries(real_short_ports):
    from bt_api_ctp.trading_session_schedule import CtpTradingSessionSchedule

    bundle, manifest, view = real_short_ports
    data = json.loads(bundle.payload)
    legacy = CtpTradingSessionSchedule()

    def new_schedule(alias):
        mapping = next(m for m in data["mappings"] if m["legacy_instrument_id"] == alias)
        return scan._prepared(view, mapping, manifest).schedule

    rb = new_schedule("rb2601")
    friday = datetime(2026, 6, 5, 13, tzinfo=UTC)
    assert legacy.session_state("rb2601", friday).phase is TradingPhase.CONTINUOUS
    assert legacy.session_state("rb2601", friday).trading_day.isoformat() == "2026-06-08"
    assert legacy.windows("rb2601", friday, friday + timedelta(minutes=5)) == ()
    assert rb.session_state("rb2601", friday).phase is TradingPhase.CONTINUOUS
    assert rb.session_state("rb2601", friday).trading_day.isoformat() == "2026-06-08"
    holiday = datetime(2026, 2, 13, 13, tzinfo=UTC)
    assert legacy.session_state("rb2601", holiday).phase is TradingPhase.CLOSED
    assert rb.session_state("rb2601", holiday).phase is TradingPhase.CLOSED
    auction = datetime(2026, 2, 24, 0, 55, tzinfo=UTC)
    assert legacy.session_state("rb2601", auction).phase is TradingPhase.AUCTION
    assert rb.session_state("rb2601", auction).phase is TradingPhase.AUCTION
    copper = new_schedule("cu2601")
    copper_end = datetime(2026, 6, 5, 17, tzinfo=UTC)
    assert (
        legacy.session_state("cu2601", copper_end - timedelta(minutes=5)).phase
        is TradingPhase.CONTINUOUS
    )
    assert (
        copper.session_state("cu2601", copper_end - timedelta(minutes=5)).phase
        is TradingPhase.CONTINUOUS
    )
    assert legacy.session_state("cu2601", copper_end).phase is TradingPhase.CLOSED
    assert copper.session_state("cu2601", copper_end).phase is TradingPhase.CLOSED
    gold = new_schedule("au2601")
    gold_end = datetime(2026, 6, 5, 18, 30, tzinfo=UTC)
    assert (
        legacy.session_state("au2601", gold_end - timedelta(minutes=5)).phase
        is TradingPhase.CONTINUOUS
    )
    assert (
        gold.session_state("au2601", gold_end - timedelta(minutes=5)).phase
        is TradingPhase.CONTINUOUS
    )
    assert legacy.session_state("au2601", gold_end).phase is TradingPhase.CLOSED
    assert gold.session_state("au2601", gold_end).phase is TradingPhase.CLOSED


def test_rle_tamper_with_recomputed_envelope_still_rejects_wrong_counts():
    runs = [[0, 2, "EEUUN"], [2, 1, "UUUUC"]]
    body = {
        "schema": "bt_api.legacy-scan-shard/1",
        "manifest_sha256": "a" * 64,
        "alias": "rb2601",
        "total": 3,
        "runs": runs,
        "counts": scan._counts(runs, 3),
        "samples": scan._samples(runs),
    }
    body["runs"][0][2] = "MEUUN"
    forged = json.loads(scan._envelope(body))
    with pytest.raises(ValueError):
        scan._verify_shard(forged, "a" * 64, "rb2601", 3)


def test_fsync_unknown_retains_claim_and_exclusive_no_overwrite(tmp_path, monkeypatch):
    directory = os.open(tmp_path, os.O_RDONLY | os.O_DIRECTORY)
    try:
        with monkeypatch.context() as patch:

            def fail(_fd):
                raise OSError("private-error")

            patch.setattr(scan.os, "fsync", fail)
            with pytest.raises(scan.SimulationScanError, match="SCAN_WRITE_UNKNOWN") as info:
                scan._write_new(directory, "claim.json", b"partial-or-complete")
            assert "private" not in str(info.value)
        original = (tmp_path / "claim.json").read_bytes()
        assert original == b"partial-or-complete"
        with pytest.raises(scan.SimulationScanError, match="SCAN_WRITE_UNKNOWN"):
            scan._write_new(directory, "claim.json", b"overwrite")
        assert (tmp_path / "claim.json").read_bytes() == original
    finally:
        os.close(directory)


def test_claim_create_error_does_not_dispatch_any_worker(tmp_path, monkeypatch):
    root = str(tmp_path / "run")
    start = datetime(2026, 6, 5, 13, tzinfo=UTC)
    scan._make_run(
        root, test_grid=(start, start + timedelta(minutes=5)), test_aliases=("rb2601", "cu2601")
    )
    original = scan._write_new
    calls = []

    def blocked(directory, name, payload):
        if name.startswith("claim-"):
            calls.append(name)
            raise scan.SimulationScanError("SCAN_WRITE_UNKNOWN")
        return original(directory, name, payload)

    monkeypatch.setattr(scan, "_write_new", blocked)
    result = scan.run_scan(root, workers=1, _allow_test=True)
    assert result["status"] == "INCOMPLETE" and len(calls) == 1
    assert result["shards"] == {"rb2601": "UNKNOWN", "cu2601": "MISSING"}
    assert not list((tmp_path / "run").glob("shard-*.json"))


def test_annual_init_is_inert_and_preserves_full_grid(tmp_path):
    root = str(tmp_path / "annual")
    result = scan.init_run(root)
    assert result["expected_points"] == 4309920 and result["scope"] == "ANNUAL_2026"
    files = {entry.name for entry in (tmp_path / "annual").iterdir()}
    assert files == {".lock", "seed.json", "manifest.json"}
    observed = scan.inspect_run(root)
    assert observed["expected_points"] == 4309920
    assert len(observed["shards"]) == 41 and set(observed["shards"].values()) == {"MISSING"}
    assert observed["dates"] == "MISSING"
    with pytest.raises(scan.SimulationScanError, match="SCAN_AGGREGATE_INCOMPLETE"):
        scan.aggregate(root)


def test_unknown_scratch_store_creation_is_not_deleted(tmp_path, monkeypatch):
    bundle = scan.seed.build_seed(mode="SIMULATION_ONLY")
    scratch = tmp_path / "scratch"

    def make_scratch(**kwargs):
        scratch.mkdir()
        return str(scratch)

    def fail_policy(self, **kwargs):
        (scratch / "partial").write_text("owned scratch")
        raise OSError("private-error")

    monkeypatch.setattr(scan.tempfile, "mkdtemp", make_scratch)
    monkeypatch.setattr(scan.TradingCalendarStore, "initialize_review_policy", fail_policy)
    with (
        pytest.raises(scan.SimulationScanError, match="^SCAN_STORE_UNAVAILABLE$"),
        scan._temporary_store(bundle),
    ):
        pass
    assert (scratch / "partial").read_text() == "owned scratch"


def test_closed_requires_consistent_future_continuous_owner():
    from datetime import date
    from types import SimpleNamespace

    from bt_api_base.trading_session_protocol import TradingWindow, TradingWindowKind

    instant = datetime(2026, 6, 8, 1, tzinfo=UTC)
    owner = date(2026, 6, 8)
    window = TradingWindow(
        owner,
        TradingWindowKind.CONTINUOUS,
        instant + timedelta(minutes=5),
        instant + timedelta(minutes=10),
        "day",
    )

    class Schedule:
        def __init__(self, legacy, **changes):
            self.ref = "ctp.trading-sessions.2026.v1" if legacy else "mock-legacy-v1"
            self.changes = changes

        def session_state(self, alias, at):
            row = {
                "instrument_id": alias,
                "at_utc": at,
                "phase": TradingPhase.CLOSED,
                "trading_day": owner,
                "current_window": None,
                "next_continuous": window,
                "calendar_ref": self.ref,
            }
            row.update(self.changes)
            return SimpleNamespace(**row)

        def windows(self, alias, start, end):
            return ()

    valid_old, valid_new = Schedule(True), Schedule(False)
    assert scan._compare(valid_old, valid_new, "rb2601", instant)[:3] == "EEE"
    invalid = (
        {"next_continuous": None},
        {"trading_day": None},
        {"trading_day": "2026-06-08"},
        {"trading_day": date(2026, 6, 9)},
        {
            "next_continuous": TradingWindow(
                owner,
                TradingWindowKind.AUCTION,
                instant + timedelta(minutes=5),
                instant + timedelta(minutes=10),
                "day",
            )
        },
        {
            "next_continuous": TradingWindow(
                owner, TradingWindowKind.CONTINUOUS, instant, instant + timedelta(minutes=5), "day"
            )
        },
        {"next_continuous": object()},
        {"current_window": window},
    )
    for changes in invalid:
        assert scan._compare(Schedule(True, **changes), valid_new, "rb2601", instant)[:3] == "UUU"
        assert scan._compare(valid_old, Schedule(False, **changes), "rb2601", instant)[:3] == "UUU"
    assert (
        scan._compare(
            Schedule(True, next_continuous=None, trading_day=None),
            Schedule(False, next_continuous=None, trading_day=None),
            "rb2601",
            instant,
        )
        == "UUUUU"
    )


def test_modern_wrong_calendar_ref_makes_state_and_windows_unknown():
    from datetime import date

    from bt_api_base.trading_session_protocol import (
        TradingSessionState,
        TradingWindow,
        TradingWindowKind,
    )

    instant = datetime(2026, 6, 8, 1, tzinfo=UTC)
    window = TradingWindow(
        date(2026, 6, 8),
        TradingWindowKind.CONTINUOUS,
        instant,
        instant + timedelta(minutes=5),
        "day",
    )

    class Schedule:
        def __init__(self, ref):
            self.ref = ref

        def session_state(self, alias, at):
            return TradingSessionState(
                alias, at, TradingPhase.CONTINUOUS, window.trading_day, window, None, self.ref
            )

        def windows(self, alias, start, end):
            return (window,)

    old = Schedule("ctp.trading-sessions.2026.v1")
    assert scan._compare(old, Schedule("mock-legacy-v1"), "rb2601", instant) == "EEUEN"
    assert scan._compare(old, Schedule("other-version"), "rb2601", instant) == "UUUUN"


def test_legacy_unknown_or_wrong_calendar_ref_suppresses_windows():
    from datetime import date

    from bt_api_base.trading_session_protocol import (
        TradingSessionState,
        TradingWindow,
        TradingWindowKind,
    )

    instant = datetime(2026, 6, 8, 1, tzinfo=UTC)
    window = TradingWindow(
        date(2026, 6, 8),
        TradingWindowKind.CONTINUOUS,
        instant,
        instant + timedelta(minutes=5),
        "day",
    )

    class Schedule:
        def __init__(self, ref, phase=TradingPhase.CONTINUOUS):
            self.ref, self.phase = ref, phase

        def session_state(self, alias, at):
            if self.phase is TradingPhase.UNKNOWN:
                return TradingSessionState(
                    alias, at, self.phase, None, None, None, self.ref, reason="UNAVAILABLE"
                )
            return TradingSessionState(
                alias, at, self.phase, window.trading_day, window, None, self.ref
            )

        def windows(self, alias, start, end):
            return (window,)

    modern = Schedule("mock-legacy-v1")
    assert (
        scan._compare(Schedule("ctp.trading-sessions.2026.v1"), modern, "rb2601", instant)
        == "EEUEN"
    )
    assert (
        scan._compare(
            Schedule("ctp.trading-sessions.2026.v1", TradingPhase.UNKNOWN),
            modern,
            "rb2601",
            instant,
        )
        == "UUUUU"
    )
    assert scan._compare(Schedule("other-version"), modern, "rb2601", instant) == "UUUUU"


def test_dependency_timezone_and_schema_changes_fail_closed(tmp_path, monkeypatch):
    root = str(tmp_path / "run")
    start = datetime(2026, 6, 5, 13, tzinfo=UTC)
    scan._make_run(root, test_grid=(start, start + timedelta(minutes=5)), test_aliases=("rb2601",))
    manifest_path = tmp_path / "run" / "manifest.json"
    original = manifest_path.read_bytes()
    manifest = json.loads(original)
    assert manifest["schema"] == "bt_api.legacy-scan-run/2"
    names = {
        "bt_api_py.trading_calendar_store",
        "bt_api_py.trading_calendar_composite",
        "bt_api_py.trading_calendar_update_simulation",
        "bt_api_py.trading_calendar_migration_barrier",
        "bt_api_base.trading_session_protocol",
        "bt_api_base.futures_expected_calendar_protocol",
        "bt_api_base.futures_market_protocol",
        "bt_api_base.market_protocol",
        "bt_api_base.query_protocol",
        "bt_api_base.service_protocol",
        "bt_api_base.account_protocol",
        "bt_api_base.execution_query_protocol",
        "bt_api_ctp.calendar_snapshot",
        "bt_api_ctp.futures_expected_calendar_provider",
        "bt_api_ctp.simulation_calendar_ports",
        "bt_api_ctp.trading_session_schedule",
    }
    assert names <= manifest["code_sha256"].keys()
    initial = scan._fingerprints()
    for name in names:
        changed = initial | {name: "0" * 64}
        with monkeypatch.context() as patch:
            patch.setattr(scan, "_fingerprints", lambda changed=changed: changed)
            with pytest.raises(scan.SimulationScanError, match="SCAN_IDENTITY_UNAVAILABLE"):
                scan._assert_runtime(manifest)
    with monkeypatch.context() as patch:
        patch.setattr(scan, "_timezone_grid_digest", lambda: "0" * 64)
        with pytest.raises(scan.SimulationScanError, match="SCAN_IDENTITY_UNAVAILABLE"):
            scan._assert_runtime(manifest)
    manifest["schema"] = "bt_api.legacy-scan-run/1"
    manifest_path.write_bytes(scan._json(manifest))
    with pytest.raises(scan.SimulationScanError, match="SCAN_IDENTITY_UNAVAILABLE"):
        scan.inspect_run(root, _allow_test=True)
    manifest_path.write_bytes(original)
    marker = tmp_path / "run" / "claim-rb2601.json"
    marker.write_bytes(b"existing claim remains")
    with monkeypatch.context() as patch:
        patch.setattr(
            scan, "_fingerprints", lambda: initial | {"bt_api_py.trading_calendar_store": "0" * 64}
        )
        for action in (scan.inspect_run, scan.run_scan, scan.aggregate):
            with pytest.raises(scan.SimulationScanError, match="SCAN_IDENTITY_UNAVAILABLE"):
                action(root, _allow_test=True)
    assert marker.read_bytes() == b"existing claim remains"
    assert manifest_path.read_bytes() == original


def test_worker_rejects_changed_runtime_before_opening_view(monkeypatch):
    monkeypatch.setattr(scan, "_view", lambda *args: pytest.fail("must not open view"))
    with pytest.raises(scan.SimulationScanError, match="SCAN_IDENTITY_UNAVAILABLE"):
        scan._worker("unused", {"schema": "bt_api.legacy-scan-run/1"}, None, {})


def test_changed_runtime_before_shard_commit_retains_claim(tmp_path, monkeypatch):
    root = str(tmp_path / "run")
    start = datetime(2026, 6, 5, 13, tzinfo=UTC)
    scan._make_run(root, test_grid=(start, start + timedelta(minutes=5)), test_aliases=("rb2601",))
    original_write = scan._write_new
    original_assert = scan._assert_runtime
    claimed = False

    def claim_then_change(directory, name, payload):
        nonlocal claimed
        original_write(directory, name, payload)
        if name == "claim-rb2601.json":
            claimed = True

    def check_runtime(manifest):
        if claimed:
            raise scan.SimulationScanError("SCAN_IDENTITY_UNAVAILABLE")
        original_assert(manifest)

    monkeypatch.setattr(scan, "_write_new", claim_then_change)
    monkeypatch.setattr(scan, "_assert_runtime", check_runtime)
    result = scan.run_scan(root, workers=1, _allow_test=True)
    assert result["shards"]["rb2601"] == "UNKNOWN"
    assert (tmp_path / "run" / "claim-rb2601.json").exists()
    assert not (tmp_path / "run" / "shard-rb2601.json").exists()


def test_timezone_digest_covers_annual_grid(monkeypatch):
    from datetime import timezone

    original = scan._timezone_grid_digest()
    assert len(original) == 64
    monkeypatch.setattr(scan, "_ZONE", timezone(timedelta(hours=7)))
    assert scan._timezone_grid_digest() != original


def test_date_rows_with_wrong_calendar_ref_are_unknown(monkeypatch):
    from types import SimpleNamespace

    from bt_api_ctp.futures_expected_calendar_provider import expected_trading_days

    bundle = scan.seed.build_seed(mode="SIMULATION_ONLY")
    start = datetime(2026, 6, 5, 13, tzinfo=UTC)
    manifest = scan._manifest(
        bundle, start=start, end=start + timedelta(minutes=5), aliases=("rb2601",), test=True
    )
    monkeypatch.setattr(
        scan,
        "_prepared",
        lambda *args: SimpleNamespace(
            expected_days=SimpleNamespace(expected_trading_days=expected_trading_days)
        ),
    )

    class View:
        def query_venue_day(self, *, mode, venue, trading_day):
            return SimpleNamespace(
                status="COMPLETE",
                binding=SimpleNamespace(
                    version_sha256=manifest["version_sha256"],
                    content_sha256=manifest["content_sha256"],
                    calendar_ref="other-version",
                ),
                venue=venue,
                trading_day=trading_day,
                source_ids=("MOCK.simulation_only",),
                source_authentication="NOT_PROVEN",
                day_status="open"
                if trading_day in expected_trading_days(venue, 2026)
                else "closed",
            )

    result = scan._dates(View(), manifest, bundle)
    assert len(result["values"]) == 1460
    assert all(row[2] == "UNKNOWN" for row in result["values"])


def test_changed_dependency_source_file_rejects_resume_without_overwrite(tmp_path, monkeypatch):
    from pathlib import Path

    from bt_api_py import trading_calendar_store

    root = str(tmp_path / "run")
    start = datetime(2026, 6, 5, 13, tzinfo=UTC)
    scan._make_run(root, test_grid=(start, start + timedelta(minutes=5)), test_aliases=("rb2601",))
    marker = tmp_path / "run" / "claim-rb2601.json"
    marker.write_bytes(b"retain claim")
    copy = tmp_path / "store_copy.py"
    copy.write_bytes(Path(trading_calendar_store.__file__).read_bytes())
    monkeypatch.setattr(trading_calendar_store, "__file__", str(copy))
    assert scan.inspect_run(root, _allow_test=True)["shards"]["rb2601"] == "UNKNOWN"
    copy.write_bytes(copy.read_bytes() + b"\n# changed\n")
    for action in (scan.inspect_run, scan.run_scan, scan.aggregate):
        with pytest.raises(scan.SimulationScanError, match="SCAN_IDENTITY_UNAVAILABLE"):
            action(root, _allow_test=True)
    assert marker.read_bytes() == b"retain claim"
