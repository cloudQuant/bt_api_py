"""Known interruption markers cannot be written by the legacy Store path."""

from copy import deepcopy

import pytest

from bt_api_py.trading_calendar_store import TradingCalendarStore
from tests.calendar_historical_interruption import write_historical_interruption
from tests.calendar_test_store import FixtureCalendarStore
from tests.unit.test_trading_calendar_legacy_exception_guard import changed
from tests.unit.test_trading_calendar_store import content


def files(root):
    return {str(p.relative_to(root)): p.read_bytes() for p in root.rglob("*") if p.is_file()}


def lock_identity(root):
    entry = (root / ".lock").stat()
    return entry.st_dev, entry.st_ino, entry.st_mode


@pytest.mark.parametrize(
    "marker",
    [
        {"action": "interrupt_window"},
        {"exception_schema": "bt_api.calendar-window-interruption/1"},
        {"nested_scope": {"DCE": [{"action": "interrupt_window"}]}},
        {
            "venue": "DCE",
            "product": "p",
            "trading_day": "2026-06-02",
            "action": "cancel_window",
            "start_utc": "missing-kind",
        },
        {
            "venue": "DCE",
            "kind": "future",
            "product": "p",
            "trading_day": "2026-06-02",
            "action": "cancel_window",
            "start_utc": "anything",
        },
        {
            "venue": "DCE",
            "kind": "future",
            "product": "p",
            "trading_day": "2026-06-02",
            "action": "cancel_window",
            "end_utc": "anything",
        },
        {
            "venue": "DCE",
            "kind": "option",
            "product": "p",
            "trading_day": "2026-06-02",
            "action": "close_day",
            "exception_schema": "unknown-version",
        },
    ],
)
def test_candidate_marker_rejected_before_root_or_lock_creation(tmp_path, marker):
    root = tmp_path / "absent"
    candidate = content(1)
    candidate["exceptions"] = [{"exception": "unrelated"}, marker]
    result = TradingCalendarStore(root).publish(candidate, publication_id="p1", version_id="v1")
    assert result.visibility == "NOT_COMMITTED"
    assert not root.exists()


def test_candidate_in_another_scope_does_not_change_existing_files_or_lock(tmp_path):
    store = FixtureCalendarStore(tmp_path)
    assert store.publish(content(0), publication_id="p0", version_id="v0").visibility == "COMMITTED"
    before, identity = files(tmp_path), lock_identity(tmp_path)
    candidate = content(1)
    candidate["exceptions"] = [
        {"venue": "SHFE", "action": "cancel_window"},
        {"venue": "DCE", "kind": "future", "action": "interrupt_window"},
    ]
    assert (
        store.publish(candidate, publication_id="p1", version_id="v1").visibility
        == "NOT_COMMITTED"
    )
    assert files(tmp_path) == before
    assert lock_identity(tmp_path) == identity


@pytest.mark.parametrize(
    "exceptions",
    [
        [],
        {},
        [{"exception": 1}],
        [
            {
                "type": "maintenance",
                "start_utc": "ordinary neutral data",
                "metadata": {"end_utc": "also ordinary"},
            }
        ],
        {"SOME_VENUE": {"start_utc": "ordinary neutral mapping"}},
        [
            {
                "venue": "OTHER",
                "kind": "future",
                "product": "p",
                "trading_day": "2026-06-02",
                "action": "cancel_window",
                "start_utc": "ordinary neutral data",
            }
        ],
        [
            {
                "venue": "SHFE",
                "kind": "future",
                "product": "p",
                "contract": "mock_p",
                "trading_day": "2026-06-02",
                "action": "close_day",
                "window_id": None,
                "source_ids": ["MOCK.simulation_only"],
            }
        ],
        [
            {
                "venue": "SHFE",
                "kind": "future",
                "product": "p",
                "contract": "mock_p",
                "trading_day": "2026-06-02",
                "action": "cancel_window",
                "window_id": "day",
                "source_ids": ["MOCK.simulation_only"],
            }
        ],
    ],
)
def test_non_interruption_neutral_and_legacy_data_remain_writable(tmp_path, exceptions):
    store = FixtureCalendarStore(tmp_path)
    candidate = content(0)
    candidate["exceptions"] = exceptions
    assert store.publish(candidate, publication_id="p0", version_id="v0").visibility == "COMMITTED"
    assert store.read_current().content == candidate


def test_historical_current_removal_and_historical_chain_cannot_downgrade(tmp_path):
    store = FixtureCalendarStore(tmp_path)
    old = changed()
    write_historical_interruption(store, old, publication_id="old-pub", version_id="old-version")
    assert store.read_current().content == old
    before, identity = files(tmp_path), lock_identity(tmp_path)
    assert (
        store.publish(old, publication_id="old-pub", version_id="old-version").visibility
        == "COMMITTED"
    )
    assert files(tmp_path) == before
    replacement = deepcopy(old)
    replacement["exceptions"] = []
    assert (
        store.publish(replacement, publication_id="new-pub", version_id="new-version").visibility
        == "NOT_COMMITTED"
    )
    assert files(tmp_path) == before
    assert lock_identity(tmp_path) == identity
    # Test-only bytes model a pre-guard writer that already removed the marker.
    write_historical_interruption(
        store, replacement, publication_id="history-pub", version_id="history-version"
    )
    assert store.read_current().content == replacement
    before = files(tmp_path)
    newer = deepcopy(replacement)
    newer["sources"][0]["description"] += " more"
    assert (
        store.publish(newer, publication_id="later-pub", version_id="later-version").visibility
        == "NOT_COMMITTED"
    )
    assert files(tmp_path) == before
    assert store.read_current().content == replacement
    # Original ID replay is a read-only proof of the historic event, not a write.
    assert (
        store.publish(old, publication_id="old-pub", version_id="old-version").visibility
        == "HISTORICAL_COMMITTED"
    )
    assert files(tmp_path) == before


def test_clean_ctp_covered_baseline_remains_writable(tmp_path):
    import hashlib
    from datetime import date

    from bt_api_ctp.calendar_snapshot import CalendarSnapshot

    from bt_api_py.trading_calendar_composite import compose_simulated
    from bt_api_py.trading_calendar_update_simulation import VenueContent
    from tests.unit.test_trading_calendar_legacy_exception_guard import content as ctp_content

    store = FixtureCalendarStore(tmp_path)
    data = ctp_content()
    day = date(2026, 6, 2)
    grouped = VenueContent.from_layers("SHFE", day, day, data)
    composite = compose_simulated(
        mode="SIMULATION_ONLY", venues=(grouped,), snapshot_factory=CalendarSnapshot
    )
    assert composite.status == "READY_SIMULATED"
    result = store.publish(
        data,
        publication_id="clean-pub",
        version_id="clean-version",
        coverage_manifest_bytes=composite.manifest_bytes,
        coverage_manifest_sha256=hashlib.sha256(composite.manifest_bytes).hexdigest(),
        binding_bytes=composite.binding_bytes,
        binding_sha256=hashlib.sha256(composite.binding_bytes).hexdigest(),
    )
    assert result.visibility == "COMMITTED"
    assert store.read_current_with_consistent_receipts().snapshot.content == data


@pytest.mark.parametrize("state", ["pending", "orphan_event"])
def test_marker_preserves_existing_uncertain_operation(tmp_path, state):
    store = FixtureCalendarStore(tmp_path)
    assert store.publish(content(0), publication_id="p0", version_id="v0").visibility == "COMMITTED"
    if state == "pending":
        (tmp_path / "PENDING.json").write_bytes(b"{}")
    else:
        (tmp_path / "publications" / "p1.json").write_bytes(b"{}")
    before = files(tmp_path)
    result = store.publish(changed(), publication_id="p1", version_id="v1")
    assert result.visibility == "COMMIT_UNKNOWN"
    if state == "pending":
        assert result.fence == "PRESENT"
    assert files(tmp_path) == before


def test_marker_with_orphan_and_missing_lock_does_not_create_sidecars(tmp_path):
    (tmp_path / "publications").mkdir()
    (tmp_path / "publications" / "p1.json").write_bytes(b"{}")
    before = files(tmp_path)
    result = TradingCalendarStore(tmp_path).publish(changed(), publication_id="p1", version_id="v1")
    assert result.visibility == "COMMIT_UNKNOWN"
    assert not (tmp_path / ".lock").exists()
    assert files(tmp_path) == before


def test_existing_empty_root_marker_is_unknown_without_creating_lock(tmp_path):
    before = files(tmp_path)
    result = TradingCalendarStore(tmp_path).publish(changed(), publication_id="p1", version_id="v1")
    assert (result.visibility, result.fence) == ("COMMIT_UNKNOWN", "UNKNOWN")
    assert not (tmp_path / ".lock").exists()
    assert files(tmp_path) == before


def test_marker_with_busy_existing_lock_is_unknown_without_writes(tmp_path):
    import fcntl

    store = FixtureCalendarStore(tmp_path)
    assert store.publish(content(0), publication_id="p0", version_id="v0").visibility == "COMMITTED"
    before, identity = files(tmp_path), lock_identity(tmp_path)
    with (tmp_path / ".lock").open("rb") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        result = store.publish(changed(), publication_id="p1", version_id="v1")
    assert (result.visibility, result.fence) == ("COMMIT_UNKNOWN", "UNKNOWN")
    assert files(tmp_path) == before
    assert lock_identity(tmp_path) == identity


def test_marker_with_occupied_version_is_unknown(tmp_path):
    store = FixtureCalendarStore(tmp_path)
    assert store.publish(content(0), publication_id="p0", version_id="v0").visibility == "COMMITTED"
    before = files(tmp_path)
    result = store.publish(changed(), publication_id="p1", version_id="v0")
    assert (result.visibility, result.fence) == ("COMMIT_UNKNOWN", "UNKNOWN")
    assert files(tmp_path) == before


def test_cooperating_writer_can_consume_id_during_unlocked_marker_probe(tmp_path):
    from concurrent.futures import ThreadPoolExecutor
    from threading import Event

    writer = FixtureCalendarStore(tmp_path)
    assert (
        writer.publish(content(0), publication_id="p0", version_id="v0").visibility == "COMMITTED"
    )
    observed, proceed = Event(), Event()

    class PausingStore(TradingCalendarStore):
        def _entry_exists(self, path):
            result = super()._entry_exists(path)
            if path.name == "POLICY_CHANGE_PENDING.json":
                observed.set()
                if not proceed.wait(timeout=10):
                    raise TimeoutError("test writer did not proceed")
            return result

    candidate = PausingStore(tmp_path)
    with ThreadPoolExecutor(max_workers=1) as pool:
        pending = pool.submit(candidate.publish, changed(), publication_id="p1", version_id="v1")
        try:
            assert observed.wait(timeout=10)
            assert (
                writer.publish(content(1), publication_id="p1", version_id="v1").visibility
                == "COMMITTED"
            )
            after_writer = files(tmp_path)
        finally:
            proceed.set()
        result = pending.result(timeout=10)
    assert (result.visibility, result.fence) == ("COMMIT_UNKNOWN", "UNKNOWN")
    assert result.publication_id == "p1" and result.version_id == "v1"
    assert files(tmp_path) == after_writer
    assert writer.verify_publication("p1") == "VISIBLE"
    assert writer.read_current_with_consistent_receipts().snapshot.version_id == "v1"
    assert files(tmp_path) == after_writer
