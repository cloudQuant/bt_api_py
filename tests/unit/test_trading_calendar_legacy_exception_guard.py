"""Legacy simulation paths must reject the newer CTP interruption schema."""

import hashlib
import json
from contextlib import nullcontext
from copy import deepcopy
from datetime import date
from types import SimpleNamespace

import pytest
from bt_api_ctp.calendar_snapshot import CalendarSnapshot as CtpSnapshot
from bt_api_ctp.calendar_supplement import _validate

from bt_api_py import trading_calendar_candidate_inbox as inbox
from bt_api_py import trading_calendar_mock_publication as publication
from bt_api_py.trading_calendar_composite import _require_legacy_exceptions, compose_simulated
from bt_api_py.trading_calendar_review_supplement import merge_supplement_layers
from bt_api_py.trading_calendar_semantic_diff import compare_calendar_version
from bt_api_py.trading_calendar_store import CalendarSnapshot, PublicationResult
from bt_api_py.trading_calendar_update_simulation import VenueContent
from tests.calendar_historical_interruption import write_historical_interruption

MOCK = "MOCK.simulation_only"


def content():
    return {
        "sources": [publication.mock.SOURCE.copy()],
        "trading_days": [
            {"venue": "SHFE", "day": "2026-06-02", "status": "open", "source_ids": [MOCK]}
        ],
        "trading_hours": [
            {
                "venue": "SHFE",
                "kind": "future",
                "product": "cu",
                "contracts": ["cu1"],
                "valid_from": "2026-06-02",
                "valid_to": "2026-06-02",
                "day_windows": [
                    {
                        "id": "day",
                        "phase": "continuous",
                        "start": "09:00",
                        "end": "11:00",
                        "end_day_offset": 0,
                    }
                ],
                "night_windows": [],
                "night_mappings": [
                    {"trading_day": "2026-06-02", "start_date": None, "source_ids": [MOCK]}
                ],
                "source_ids": [MOCK],
                "windows_complete": True,
                "exceptions_complete": True,
            }
        ],
        "exceptions": [],
    }


def changed(end="2026-06-02T02:30:00Z"):
    data = content()
    data["exceptions"] = [
        {
            "venue": "SHFE",
            "kind": "future",
            "product": "cu",
            "contract": "cu1",
            "trading_day": "2026-06-02",
            "action": "interrupt_window",
            "window_id": "day",
            "exception_schema": "bt_api.calendar-window-interruption/1",
            "start_utc": "2026-06-02T02:00:00Z",
            "end_utc": end,
            "source_ids": [MOCK],
        }
    ]
    return data


def test_new_ctp_accepts_but_legacy_guard_rejects_both_comparison_sides():
    a, b = changed(), changed("2026-06-02T02:45:00Z")
    assert (
        CtpSnapshot(a, calendar_ref="mock")
        .query(
            venue="SHFE", kind="future", product="cu", contract="cu1", trading_day=date(2026, 6, 2)
        )
        .status
        == "COMPLETE"
    )
    assert _validate(a).content == a
    for item in (a, b):
        with pytest.raises(ValueError, match="unsupported legacy exception"):
            inbox._validated_baseline(item)
        with pytest.raises(ValueError, match="unsupported legacy exception"):
            inbox._compare_content(item, content())
        with pytest.raises(ValueError, match="unsupported legacy exception"):
            inbox._compare_content(content(), item)
    with pytest.raises(ValueError, match="unsupported legacy exception"):
        inbox._compare_content(a, b)
    current = CalendarSnapshot("pub", 1, "v1", "a" * 64, a)
    for old, candidate in (
        (current, content()),
        (CalendarSnapshot("p", 1, "v", "a" * 64, content()), b),
    ):
        result = compare_calendar_version(
            old, candidate, scopes=(), start=date(2026, 6, 2), end=date(2026, 6, 2)
        )
        assert result.status == "UNKNOWN" and result.reason == "UNSUPPORTED_EXCEPTION_FORMAT"


def test_guard_rejects_extra_fields_even_with_old_action():
    value = changed()["exceptions"][0]
    value["action"] = "cancel_window"
    with pytest.raises(ValueError):
        _require_legacy_exceptions({"exceptions": [value]})
    _require_legacy_exceptions(
        {
            "exceptions": [
                {
                    k: v
                    for k, v in value.items()
                    if k not in {"exception_schema", "start_utc", "end_utc"}
                }
            ]
        }
    )


def test_payload_and_composition_reject_even_with_forgiving_ctp_factory():
    candidate = _validate(changed())
    with pytest.raises(ValueError, match="unsupported legacy exception"):
        inbox._payload(None, candidate, None, {})
    fields = {
        "content",
        "unresolved_nights",
        "holiday_status",
        "unresolved_nights_status",
        "input_scope",
        "source_checks",
        "observed_at_utc",
        "clock_status",
        "remaining_checks",
        "hours_page",
        "hours_page_status",
        "identity_mapping",
        "night_mapping",
        "baseline",
        "difference",
        "completeness",
        "verification",
        "publication",
        "durability",
    }
    payload = dict.fromkeys(fields)
    payload["content"] = changed()
    with pytest.raises(ValueError, match="unsupported legacy exception"):
        inbox._validate_payload(payload)
    venue = VenueContent.from_layers("SHFE", date(2026, 6, 2), date(2026, 6, 2), changed())
    result = compose_simulated(
        mode="SIMULATION_ONLY",
        venues=(venue,),
        snapshot_factory=lambda *args, **kwargs: SimpleNamespace(query=lambda **k: None),
    )
    assert result.status == "INVALID_INPUT" and result.snapshot_bytes is None


def test_supplement_merge_rejects_new_rows_on_either_side_before_loss():
    original, incoming = changed(), changed("2026-06-02T02:45:00Z")
    before = deepcopy(original), deepcopy(incoming)
    for left, right in ((original, incoming), (content(), incoming), (original, content())):
        with pytest.raises(ValueError, match="unsupported legacy exception"):
            merge_supplement_layers(
                left,
                right,
                venue="SHFE",
                coverage_start=date(2026, 6, 2),
                coverage_end=date(2026, 6, 2),
            )
    assert (original, incoming) == before


def bare_operator(tmp_path):
    op = object.__new__(publication.MockCalendarPublication)
    op.root = str(tmp_path)
    op.run_id = "a" * 32
    op._declaration = None
    op._guard = lambda: None
    op._check_scope_migration_before_effects = lambda: None
    op._check_scope_migration_locked = lambda: None
    return op


def test_prepare_first_and_existing_current_reject_new_format(tmp_path):
    op = bare_operator(tmp_path)
    op._candidate = lambda _: ({"payload_sha256": "a" * 64}, {"snapshot": changed()})
    assert op.prepare(candidate_id="b" * 32)["status"] == "UNKNOWN"
    venue = VenueContent.from_layers("SHFE", date(2026, 6, 2), date(2026, 6, 2), content())
    combined = compose_simulated(
        mode="SIMULATION_ONLY", venues=(venue,), snapshot_factory=CtpSnapshot
    )
    composite = {
        "snapshot": json.loads(combined.snapshot_bytes),
        "manifest": json.loads(combined.manifest_bytes),
        "manifest_sha256": combined.manifest_sha256,
        "binding": json.loads(combined.binding_bytes),
        "binding_sha256": combined.binding_sha256,
    }
    op._candidate = lambda _: ({"payload_sha256": "a" * 64}, composite)
    op._candidate_provenance = lambda *_: None

    # Construct the unsupported CURRENT as a pre-guard historical fixture.
    from bt_api_py.trading_calendar_store import _canonical
    from tests.calendar_test_store import FixtureCalendarStore

    new_content = changed()
    new_hash = hashlib.sha256(_canonical(new_content)).hexdigest()
    manifest = dict(composite["manifest"], snapshot_sha256=new_hash)
    manifest_bytes = _canonical(manifest)
    manifest_hash = hashlib.sha256(manifest_bytes).hexdigest()
    binding = dict(composite["binding"], snapshot_sha256=new_hash, manifest_sha256=manifest_hash)
    binding_bytes = _canonical(binding)
    store = FixtureCalendarStore(tmp_path)
    written = write_historical_interruption(
        store,
        new_content,
        publication_id="MOCK_pub",
        version_id="MOCK_ver",
        coverage_manifest_bytes=manifest_bytes,
        coverage_manifest_sha256=manifest_hash,
        binding_bytes=binding_bytes,
        binding_sha256=hashlib.sha256(binding_bytes).hexdigest(),
    )
    assert written.visibility == "COMMITTED"
    before = (tmp_path / "CURRENT.json").read_bytes()
    op._store = store
    checked = []
    original = op._check_simulated_current

    def check(current):
        checked.append(1)
        return original(current)

    op._check_simulated_current = check
    result = op.prepare(candidate_id="b" * 32)
    assert result["status"] == "UNKNOWN" and checked == [1]
    assert (tmp_path / "CURRENT.json").read_bytes() == before


def test_same_lock_rechecks_current_before_recording(tmp_path):
    op = bare_operator(tmp_path)
    proposal = {"status": "SAME", "candidate_id": "b" * 32}
    op.prepare = lambda **kwargs: proposal
    op._reserve = lambda *a, **k: pytest.fail("unsupported CURRENT must not reserve")
    op._store = SimpleNamespace(
        _writer_lock=nullcontext,
        _pending_exists=lambda: False,
        _policy_change_pending=lambda: False,
        read_current=lambda: SimpleNamespace(content=changed()),
    )
    result = op.publish(
        proposal=proposal, publication_id="MOCK_pub", version_id="MOCK_ver", emit=lambda _: None
    )
    assert (
        result["status"] == "UNKNOWN" and result["reason"] == "SAME_REVALIDATION_OR_RECORD_UNKNOWN"
    )


@pytest.mark.parametrize("mode", ["auto", "manual"])
@pytest.mark.parametrize("changed_side", ["candidate", "current"])
def test_final_lock_rechecks_candidate_and_current_in_both_modes(tmp_path, mode, changed_side):
    op = bare_operator(tmp_path)
    proposal = {
        "status": "CHANGED",
        "candidate_id": "b" * 32,
        "candidate_payload_sha256": "a" * 64,
        "snapshot_sha256": "b" * 64,
        "difference_sha256": "c" * 64,
        "difference": {},
        "baseline": {"mode": "ABSENT", "publication_id": None, "publication_sha256": None},
        "policy": {"mode": mode, "revision": "rev", "directory_id": "directory"},
        "data_origin": "MOCK",
    }
    op.prepare = lambda **kwargs: proposal
    op._candidate_provenance = lambda *_: None
    op._reserve = lambda *a, **k: ("COMMITTED", "OK")
    count = []

    def candidate(_):
        count.append(1)
        return {"payload_sha256": "a" * 64}, {
            "snapshot": changed() if len(count) > 1 and changed_side == "candidate" else content(),
            "manifest": {},
            "manifest_sha256": "d" * 64,
            "binding": {},
            "binding_sha256": "e" * 64,
        }

    op._candidate = candidate
    called = []

    def publish(*args, locked_policy_guard, **kwargs):
        with pytest.raises(ValueError, match="unsupported legacy exception"):
            locked_policy_guard(None)
        called.append(1)
        return PublicationResult("NOT_COMMITTED", "UNCONFIRMED", "MOCK_pub", "MOCK_ver")

    if changed_side == "current":
        (tmp_path / "CURRENT.json").write_bytes(b"fixture")
    op._store = SimpleNamespace(
        _writer_lock=nullcontext,
        publish=publish,
        read_current=lambda: SimpleNamespace(content=changed()),
    )
    extra = (
        {"reviewer": "MOCK_reviewer", "confirmed_at": "2026-06-02T00:00:00Z"}
        if mode == "manual"
        else {}
    )
    result = op.publish(
        proposal=proposal,
        publication_id="MOCK_pub",
        version_id="MOCK_ver",
        emit=lambda _: None,
        **extra,
    )
    assert result["status"] == "UNKNOWN" and called == [1]
    assert len(count) == (2 if changed_side == "candidate" else 1)


@pytest.mark.parametrize("bad_shard,bad_composite", [(True, False), (False, True)])
def test_candidate_loader_checks_original_shards_and_composite(
    tmp_path, monkeypatch, bad_shard, bad_composite
):
    op = bare_operator(tmp_path)
    body = {
        "input": {
            "metadata": publication.mock.LABEL,
            "shards": [{"layers": changed() if bad_shard else content()}],
        },
        "report": {
            "status": "STRUCTURALLY_READY_UNVERIFIED",
            "source_authentication": "NOT_PROVEN",
            "composite": {"snapshot": changed() if bad_composite else content()},
        },
    }
    monkeypatch.setattr(
        publication.review,
        "show",
        lambda **kwargs: {"status": "VISIBLE_UNAUTHENTICATED", "bundle": body},
    )
    with pytest.raises(ValueError, match="unsupported legacy exception"):
        op._candidate("a" * 32)


@pytest.mark.parametrize("mode", ["auto", "manual"])
def test_first_publication_new_candidate_never_becomes_pending_or_published(tmp_path, mode):
    op = bare_operator(tmp_path)
    op._candidate = lambda _: ({"payload_sha256": "a" * 64}, {"snapshot": changed()})
    op._store = SimpleNamespace(publish=lambda *a, **k: pytest.fail("must not publish"))
    proposal = {
        "status": "FIRST_SIMULATION_PUBLICATION",
        "candidate_id": "b" * 32,
        "policy": {"mode": mode},
    }
    extra = (
        {"reviewer": "MOCK_reviewer", "confirmed_at": "2026-06-02T00:00:00Z"}
        if mode == "manual"
        else {}
    )
    result = op.publish(
        proposal=proposal,
        publication_id="MOCK_pub",
        version_id="MOCK_ver",
        emit=lambda _: None,
        **extra,
    )
    assert result["status"] == "NOT_COMMITTED"
    assert result["reason"] == "STALE_OR_UNAVAILABLE_PROPOSAL"
    assert not (tmp_path / "CURRENT.json").exists()


def test_review_bundle_rejects_raw_new_fields_before_ctp_builder(tmp_path, monkeypatch):
    from bt_api_ctp import calendar_auto_candidate

    from bt_api_py import trading_calendar_review_bundle as review
    from tests.unit.test_trading_calendar_review_bundle import sample

    data = sample()
    data["shards"][0]["layers"]["exceptions"].append(changed()["exceptions"][0])
    monkeypatch.setattr(
        calendar_auto_candidate,
        "build_auto_candidate",
        lambda **kw: pytest.fail("normalization must not run"),
    )
    with pytest.raises(ValueError, match="unsupported legacy exception"):
        review._evaluate(data, "a" * 32)
    result = review.build_once(
        root=str(tmp_path),
        candidate_id="a" * 32,
        input_data=data,
        filesystem="TRUSTED_LOCAL_POSIX",
        emit=lambda _: None,
    )
    assert result["status"] == "NOT_COMMITTED"
    assert not (tmp_path / "review-bundles").exists()
    data["shards"] = data["shards"][:1]
    partial = review.build_once(
        root=str(tmp_path),
        candidate_id="b" * 32,
        input_data=data,
        filesystem="TRUSTED_LOCAL_POSIX",
        emit=lambda _: None,
    )
    assert partial["status"] == "NOT_COMMITTED"
    assert not (tmp_path / "review-bundles").exists()


def test_composite_keeps_legacy_source_venue_and_identity_error_priority():
    base = content()
    original = {
        "venue": "SHFE",
        "kind": "future",
        "product": "cu",
        "contract": "cu1",
        "trading_day": "2026-06-02",
        "action": "close_day",
        "window_id": None,
        "source_ids": [MOCK],
    }

    def check(row):
        payload = deepcopy(base)
        payload["exceptions"] = [row]
        venue = VenueContent.from_layers("SHFE", date(2026, 6, 2), date(2026, 6, 2), payload)
        return compose_simulated(
            mode="SIMULATION_ONLY", venues=(venue,), snapshot_factory=CtpSnapshot
        ).status

    missing_source = dict(original)
    del missing_source["source_ids"]
    assert check(missing_source) == "SOURCE_REFERENCE_MISSING"
    assert check(dict(original, venue="DCE", source_ids=[])) == "VENUE_MISMATCH"
    missing_identity = dict(original)
    del missing_identity["window_id"]
    assert check(missing_identity) == "INVALID_INPUT"
    assert check(changed()["exceptions"][0]) == "INVALID_INPUT"


def _real_review_current_with_interruption(tmp_path, kind):
    from bt_api_py.trading_calendar_store import _canonical
    from tests.unit.test_trading_calendar_review_bundle import published_candidate, sample

    value = sample(days=1)
    shfe = next(item for item in value["shards"] if item["venue"] == "SHFE")
    shfe["layers"]["trading_days"][0]["status"] = "open"
    for row in shfe["layers"]["trading_hours"]:
        row["night_mappings"] = [
            {"trading_day": "2026-01-01", "start_date": None, "source_ids": ["synthetic"]}
        ]
    saved, store = published_candidate(tmp_path, value)
    current = store.read_current()
    updated = deepcopy(current.content)
    updated["sources"].append({"id": MOCK, "description": "mock"})
    contract = "shfe" + kind + "2701"
    updated["exceptions"].append(
        {
            "venue": "SHFE",
            "kind": kind,
            "product": "p" + kind,
            "contract": contract,
            "trading_day": "2026-01-01",
            "action": "interrupt_window",
            "window_id": "day",
            "exception_schema": "bt_api.calendar-window-interruption/1",
            "start_utc": "2026-01-01T01:15:00Z",
            "end_utc": "2026-01-01T01:30:00Z",
            "source_ids": [MOCK],
        }
    )
    snapshot = CtpSnapshot(updated, calendar_ref="v2")
    assert (
        snapshot.query(
            venue="SHFE",
            kind=kind,
            product="p" + kind,
            contract=contract,
            trading_day=date(2026, 1, 1),
        ).status
        == "COMPLETE"
    )
    new_hash = hashlib.sha256(_canonical(updated)).hexdigest()
    manifest = dict(json.loads(current.coverage_manifest_bytes), snapshot_sha256=new_hash)
    manifest_bytes = _canonical(manifest)
    manifest_sha = hashlib.sha256(manifest_bytes).hexdigest()
    binding = dict(
        json.loads(current.binding_bytes), snapshot_sha256=new_hash, manifest_sha256=manifest_sha
    )
    binding_bytes = _canonical(binding)
    result = write_historical_interruption(
        store,
        updated,
        publication_id="test-pub-2",
        version_id="test-ver-2",
        coverage_manifest_bytes=manifest_bytes,
        coverage_manifest_sha256=manifest_sha,
        binding_bytes=binding_bytes,
        binding_sha256=hashlib.sha256(binding_bytes).hexdigest(),
    )
    assert result.visibility == "COMMITTED"
    return saved, store


@pytest.mark.parametrize("kind", ["future", "option"])
def test_scope_comparison_rejects_interruption_anywhere_in_real_current(tmp_path, kind):
    from tests.unit.test_trading_calendar_review_bundle import compare, compare_request

    saved, store = _real_review_current_with_interruption(tmp_path, kind)
    before = (tmp_path / "CURRENT.json").read_bytes()
    request = compare_request(saved, store)
    result = compare(tmp_path, request)
    assert result["status"] == "UNKNOWN" and result["reason"] == "UNSUPPORTED_EXCEPTION_FORMAT"
    assert "changes" not in result
    assert (tmp_path / "CURRENT.json").read_bytes() == before


def test_scope_comparison_rejects_full_candidate_snapshot_before_projection(tmp_path, monkeypatch):
    from bt_api_py import trading_calendar_review_bundle as review
    from tests.unit.test_trading_calendar_review_bundle import (
        compare,
        compare_request,
        published_candidate,
        sample,
    )

    saved, store = published_candidate(tmp_path, sample(days=1))
    request = compare_request(saved, store)
    forged = deepcopy(saved)
    forged["bundle"]["report"]["composite"]["snapshot"]["exceptions"].append(
        changed()["exceptions"][0]
    )
    monkeypatch.setattr(review, "show", lambda **kwargs: forged)
    result = compare(tmp_path, request)
    assert result["status"] == "UNKNOWN" and result["reason"] == "UNSUPPORTED_EXCEPTION_FORMAT"
    assert "changes" not in result
