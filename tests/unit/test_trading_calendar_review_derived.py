"""Synthetic HTML to immutable derived review; no network or accounts."""

import json
import runpy
from copy import deepcopy
from datetime import date, timedelta
from pathlib import Path

import pytest

from bt_api_py import trading_calendar_holiday_evidence_candidate as holiday
from bt_api_py import trading_calendar_hours_evidence_candidate as hours
from bt_api_py import trading_calendar_review_bundle as review
from bt_api_py import trading_calendar_source_evidence as evidence_api
from bt_api_py.trading_calendar_preview_reports import TRUSTED_LOCAL_POSIX as FS
from tests.unit.test_trading_calendar_candidate_inbox import HOURS, holiday_html
from tests.unit.test_trading_calendar_hours_candidate import mapping
from tests.unit.test_trading_calendar_review_bundle import sample
from tests.unit.test_trading_calendar_review_evidence_bridge import build, show
from tests.unit.test_trading_calendar_source_evidence import fake_runner

DERIVED = "e" * 32
NOTE = "注：合成甲于2026年1月5日09:00至10:15暂停交易。"


def make_candidate(root, venue, note=NOTE):
    he, te, hc, cid = (str(n) * 32 for n in range(1, 5))
    if venue == "INE":
        fixture_path = (
            Path(__file__).resolve().parents[2]
            / "bt_api/bt_api_ctp/tests/test_calendar_ine_holiday_fetch.py"
        )
        holiday_raw = runpy.run_path(str(fixture_path))["_page"]()
    else:
        holiday_raw = holiday_html()
    if note is None:
        hours_raw = HOURS.replace("<tr><td colspan=2>注：合成页面备注</td></tr>".encode(), b"")
    else:
        hours_raw = HOURS.replace("注：合成页面备注".encode(), note.encode())
    if venue == "INE":
        hours_raw = hours_raw.replace(b"21:00-23:00", b"21:00-01:00")
    for kind, eid, raw in (("holiday", he, holiday_raw), ("hours", te, hours_raw)):
        runner = fake_runner(raw, []) if venue == "SHFE" else lambda *_, raw=raw: ("HTTP_OK", raw)
        assert (
            evidence_api.capture(
                root=str(root),
                evidence_id=eid,
                kind=kind,
                exchange=venue,
                filesystem=FS,
                emit=lambda _: None,
                runner=runner,
            )["status"]
            == "COMMITTED"
        )
    assert (
        holiday.build_once(
            root=str(root), evidence_id=he, candidate_id=hc, filesystem=FS, emit=lambda _: None
        )["status"]
        == "COMMITTED"
    )
    selections = mapping()
    for row in selections:
        row["venue"] = venue
    assert (
        hours.build_once(
            root=str(root),
            evidence_id=te,
            holiday_candidate_id=hc,
            candidate_id=cid,
            selections=selections,
            filesystem=FS,
            emit=lambda _: None,
        )["status"]
        == "COMMITTED"
    )
    return cid, he, te, hc


def fixture(root, venue="SHFE", years=1, note=NOTE, conflict_day=None, padding=False):
    from bt_api_py.trading_calendar_review_supplement import _opening_from_original

    cid, he, te, hc = make_candidate(root, venue, note)
    evidence = hours.show(
        root=str(root), candidate_id=cid, filesystem=FS, include_source_text=True
    )["candidate"]
    data = sample()
    data["source_texts"]["synthetic"] = json.dumps(
        {"schema": "bt_api.calendar-review-support/1", "night_ownership": [], "night_windows": []}
    )
    shard = next(s for s in data["shards"] if s["venue"] == venue)
    if padding:
        for name in ("parent1", "parent2"):
            shard["layers"]["sources"].append({"id": name, "description": "synthetic source"})
            data["source_texts"][name] = "x" * 1_500_000
    end = date(2025 + years, 12, 31)
    shard["coverage_end"] = end.isoformat()
    for row in shard["layers"]["trading_hours"]:
        row["valid_to"] = end.isoformat()
    for req in shard["requirements"]:
        req["end_day"] = end.isoformat()
    for row in evidence["content"]["trading_hours"]:
        for contract in row["contracts"]:
            shard["requirements"].append(
                {
                    "kind": row["kind"],
                    "product": row["product"],
                    "contract": contract,
                    "start_day": "2026-01-01",
                    "end_day": end.isoformat(),
                }
            )
    closed = {r["day"]: r["status"] for r in evidence["content"]["trading_days"]}
    owners = []
    for item in evidence["holiday_unresolved_nights"]:
        owner = date.fromisoformat(_opening_from_original(item)["day"])
        assert owner.isoformat() not in closed and owner not in owners
        owners.append(owner)
    shard["layers"]["trading_days"] = [
        {
            "venue": venue,
            "day": (date(2026, 1, 1) + timedelta(days=i)).isoformat(),
            "status": closed.get(
                (date(2026, 1, 1) + timedelta(days=i)).isoformat(),
                "open" if date(2026, 1, 1) + timedelta(days=i) in owners else "closed",
            ),
            "source_ids": ["synthetic"],
        }
        for i in range((end - date(2026, 1, 1)).days + 1)
    ]
    if conflict_day:
        next(row for row in shard["layers"]["trading_days"] if row["day"] == conflict_day)[
            "status"
        ] = "closed"
    assert build(root, data, [cid])["status"] == "COMMITTED"
    parent = show(root)
    assert parent["status"] == "VISIBLE_UNAUTHENTICATED"
    parent_shard = next(s for s in parent["bundle"]["input"]["shards"] if s["venue"] == venue)
    unresolved = parent_shard["unresolved"][-1]
    assert len(owners) == len(unresolved["holiday_unresolved_nights"])
    supplement = deepcopy(parent_shard["layers"])
    supplement["sources"].append({"id": "manual", "description": "synthetic supplement"})
    holiday_source = unresolved["holiday_unresolved_nights"][0]["source_id"]
    hours_source = next(s["id"] for s in supplement["sources"] if ".hours." in s["id"])
    for row in supplement["trading_hours"]:
        row["source_ids"].append("manual")
        row["windows_complete"] = row["exceptions_complete"] = True
        night = next(
            (
                x
                for x in unresolved["hours_unresolved_nights"]
                if x["product"] == row["product"] and x["kind"] == row["kind"]
            ),
            None,
        )
        if night:
            for i, stage in enumerate(night["stages"]):
                row["night_windows"].append(
                    {
                        "id": f"n{i}",
                        "phase": "continuous" if stage["phase"] == "CONTINUOUS" else "auction",
                        "start": stage["start"],
                        "end": stage["end"],
                        "end_day_offset": 0 if stage["day_transition"] == "SAME_CLOCK_DAY" else 1,
                    }
                )
        row["night_mappings"] = [
            {
                "trading_day": day.isoformat(),
                "start_date": None,
                "source_ids": [holiday_source, "manual"],
            }
            for day in owners
        ]
    future = next(row for row in supplement["trading_hours"] if row["product"] == "cu")
    window = next(w for w in future["day_windows"] if w["start"] == "09:00" and w["end"] == "10:15")
    supplement["exceptions"].append(
        {
            "venue": venue,
            "kind": "future",
            "product": "cu",
            "contract": future["contracts"][0],
            "trading_day": "2026-01-05",
            "action": "cancel_window",
            "window_id": window["id"],
            "source_ids": [hours_source, "manual"] if note is not None else ["manual"],
        }
    )
    decisions = []
    for category in (
        "holiday_unresolved_nights",
        "hours_unresolved_nights",
        "page_notes",
        "page_review_reasons",
    ):
        for i, _item in enumerate(unresolved[category]):
            scopes = []
            if category == "holiday_unresolved_nights":
                scopes.extend(
                    [
                        {
                            k: deepcopy(row[k])
                            for k in (
                                "venue",
                                "kind",
                                "product",
                                "contracts",
                                "valid_from",
                                "valid_to",
                            )
                        }
                        | {"owner_day": owners[i].isoformat()}
                        for row in supplement["trading_hours"]
                    ]
                )
            decisions.append(
                {
                    "candidate_id": cid,
                    "category": category,
                    "index": i,
                    "reason": "Synthetic material explicitly reviewed",
                    "source_ids": [holiday_source, "manual"]
                    if category == "holiday_unresolved_nights"
                    else [hours_source, "manual"]
                    if category == "hours_unresolved_nights"
                    else [hours_source, "manual"]
                    if category == "page_notes"
                    else [hours_source, "manual"],
                    "scopes": scopes,
                }
            )
    return parent, supplement, decisions, (cid, he, te, hc)


def support_text(parent, supplement, decisions):
    from bt_api_py.trading_calendar_review_supplement import _window_fact

    doc = {
        "schema": "bt_api.calendar-review-support/1",
        "night_ownership": [],
        "night_windows": [],
        "exceptions": [
            {
                key: row[key]
                for key in (
                    "venue",
                    "kind",
                    "product",
                    "contract",
                    "trading_day",
                    "action",
                    "window_id",
                )
            }
            for row in supplement["exceptions"]
            if "manual" in row["source_ids"]
        ],
    }
    selected = [s for s in parent["bundle"]["input"]["shards"] if s["unresolved"]]
    for shard in selected:
        record = shard["unresolved"][-1]
        for decision in decisions:
            if decision["candidate_id"] != record["candidate_id"]:
                continue
            if decision["category"] == "holiday_unresolved_nights":
                natural = record["holiday_unresolved_nights"][decision["index"]]["local_date"]
                for scope in decision["scopes"]:
                    doc["night_ownership"].append(
                        {**scope, "local_date": natural, "start_date": None}
                    )
            if decision["category"] == "hours_unresolved_nights":
                observed = record["hours_unresolved_nights"][decision["index"]]
                rows = [
                    r
                    for r in supplement["trading_hours"]
                    if r["venue"] == shard["venue"]
                    and r["kind"] == observed["kind"]
                    and r["product"] == observed["product"]
                ]
                if len(rows) != 1:
                    continue
                for stage in observed["stages"]:
                    if stage["day_transition"] != "INFERRED_NEXT_DAY":
                        continue
                    for window in rows[0]["night_windows"]:
                        if window["start"] == stage["start"] and window["end"] == stage["end"]:
                            doc["night_windows"].append(_window_fact(rows[0], window))
    return json.dumps(doc, ensure_ascii=False, sort_keys=True)


def derived(root, parent, supplement, decisions, support=None, extra_texts=None):
    return review.build_derived_once(
        root=str(root),
        candidate_id=DERIVED,
        parent_id=parent["candidate_id"],
        parent_sha256=parent["payload_sha256"],
        supplement=supplement,
        source_texts={
            "manual": support
            if support is not None
            else support_text(parent, supplement, decisions),
            **(extra_texts or {}),
        },
        decisions=decisions,
        filesystem=FS,
        emit=lambda _: None,
    )


@pytest.mark.parametrize("venue", ["SHFE", "INE"])
def test_full_evidence_and_explicit_supplement(venue, tmp_path):
    import yaml
    from bt_api_ctp.calendar_supplement_reader import read_supplement

    parent, supplement, decisions, ids = fixture(tmp_path, venue)
    path = tmp_path / "public-supplement.yaml"
    path.write_text(yaml.safe_dump(supplement, allow_unicode=True), encoding="utf-8")
    captured = read_supplement(path)
    assert captured.status == "CANDIDATE"
    built = derived(tmp_path, parent, captured.candidate.content, decisions)
    assert built["status"] == "COMMITTED", built
    result = review.show(root=str(tmp_path), candidate_id=DERIVED, filesystem=FS)
    assert result["status"] == "VISIBLE_UNAUTHENTICATED", result
    body = result["bundle"]
    assert body["report"]["status"] == "STRUCTURALLY_READY_UNVERIFIED", body["report"]
    assert body["report"]["composite"]
    assert body["report"]["publication"] == "NOT_AUTHORIZED"
    assert body["report"]["identity_authentication"] == "NOT_PROVEN"
    assert body["report"]["unresolved_status"] == "STRUCTURAL_ITEMS_RESOLVED_EXTERNAL_NOT_PROVEN"
    checks = {item["check"]: item["status"] for item in body["assembly"]["check_decisions"]}
    assert checks["COVERAGE_NOT_PROVEN"] == "NOT_PROVEN"
    assert checks["SOURCE_AUTHORITY_UNVERIFIED"] == "NOT_PROVEN"
    assert checks["OTHER_SESSIONS_NOT_CHECKED"] == "NOT_PROVEN"
    assert checks["NIGHT_TRADING_DAY_UNRESOLVED"] == "CHECKED_UNVERIFIED"
    assert all(
        item["status"] == "RESOLVED_UNVERIFIED" for item in body["assembly"]["item_decisions"]
    )
    assert body["assembly"]["original_unresolved"]
    if venue == "SHFE":
        opening = next(
            row for row in body["assembly"]["original_openings"] if row["day"] == "2026-02-17"
        )
        shard = next(row for row in body["input"]["shards"] if row["venue"] == venue)
        day = next(row for row in shard["layers"]["trading_days"] if row["day"] == opening["day"])
        assert day["status"] == "open" and opening["source_id"] in day["source_ids"]
    assert not (tmp_path / "CURRENT.json").exists()
    (tmp_path / "source-evidence" / f"result-{ids[2]}.json").unlink()
    assert (
        review.show(root=str(tmp_path), candidate_id=DERIVED, filesystem=FS)["status"] == "UNKNOWN"
    )


def test_complete_diff_against_real_local_baseline(tmp_path):
    from tests.unit.test_trading_calendar_review_bundle import published_candidate

    published_candidate(tmp_path, sample())
    current = (tmp_path / "CURRENT.json").read_bytes()
    parent, supplement, decisions, _ = fixture(tmp_path)
    result = derived(tmp_path, parent, supplement, decisions)
    assert result["review_status"] == "STRUCTURALLY_READY_UNVERIFIED"
    diff = review.compare_full(root=str(tmp_path), candidate_id=DERIVED, filesystem=FS)
    assert diff["status"] == "CHANGED", diff
    assert diff["candidate_review_status"] == "STRUCTURALLY_READY_UNVERIFIED"
    assert diff["publication"] == "NOT_AUTHORIZED"
    assert (tmp_path / "CURRENT.json").read_bytes() == current


@pytest.mark.parametrize(
    "case",
    [
        "missing_holiday",
        "missing_scope",
        "missing_stage",
        "missing_note",
        "duplicate",
        "bad_owner",
        "bad_source",
    ],
)
def test_incomplete_decisions_cannot_be_ready(tmp_path, case):
    parent, supplement, decisions, _ = fixture(tmp_path)
    if case == "missing_holiday":
        decisions = [
            d
            for d in decisions
            if not (d["category"] == "holiday_unresolved_nights" and d["index"] == 0)
        ]
    elif case == "missing_scope":
        decisions[0]["scopes"].pop()
    elif case == "missing_stage":
        next(row for row in supplement["trading_hours"] if row["night_windows"])[
            "night_windows"
        ].pop()
    elif case == "missing_note":
        decisions = [d for d in decisions if d["category"] != "page_notes"]
    elif case == "duplicate":
        decisions.append(deepcopy(decisions[0]))
    elif case == "bad_owner":
        decisions[0]["scopes"][0]["owner_day"] = "2026-01-01"
    else:
        decisions[0]["source_ids"] = ["missing"]
    result = derived(tmp_path, parent, supplement, decisions)
    assert result["status"] == "NOT_COMMITTED" or result["review_status"] == "PARTIAL", result
    if result["status"] == "COMMITTED":
        body = review.show(root=str(tmp_path), candidate_id=DERIVED, filesystem=FS)["bundle"]
        assert body["report"]["status"] == "PARTIAL"
        assert body["assembly"]["original_unresolved"]


def test_tampered_sealed_supplement_invalidates_show(tmp_path):
    parent, supplement, decisions, _ = fixture(tmp_path)
    assert derived(tmp_path, parent, supplement, decisions)["status"] == "COMMITTED"
    path = tmp_path / "review-bundles" / f"candidate-{DERIVED}.json"
    document = json.loads(path.read_bytes())
    document["body"]["assembly"]["supplement"]["trading_days"][0]["status"] = "open"
    path.write_bytes(review.storage._envelope(document["body"]))
    assert (
        review.show(root=str(tmp_path), candidate_id=DERIVED, filesystem=FS)["status"] == "UNKNOWN"
    )


def test_input_changed_before_write_is_not_committed(tmp_path, monkeypatch):
    from bt_api_py import trading_calendar_review_supplement as module

    parent, supplement, decisions, _ = fixture(tmp_path)
    original = module.evaluate_derived
    calls = 0

    def changed(**kwargs):
        nonlocal calls
        calls += 1
        result = original(**kwargs)
        if calls == 1:
            supplement["sources"][0]["description"] = "changed"
        return result

    monkeypatch.setattr(module, "evaluate_derived", changed)
    result = derived(tmp_path, parent, supplement, decisions)
    assert result["status"] == "NOT_COMMITTED" and result["reason"] == "SOURCE_CHANGED"
    assert not (tmp_path / "review-bundles" / f"candidate-{DERIVED}.json").exists()


def test_same_full_diff_with_identical_local_baseline(tmp_path):
    from bt_api_py.trading_calendar_store import _canonical
    from tests.calendar_test_store import FixtureCalendarStore

    parent, supplement, decisions, _ = fixture(tmp_path)
    assert (
        derived(tmp_path, parent, supplement, decisions)["review_status"]
        == "STRUCTURALLY_READY_UNVERIFIED"
    )
    body = review.show(root=str(tmp_path), candidate_id=DERIVED, filesystem=FS)["bundle"]
    composite = body["report"]["composite"]
    store = FixtureCalendarStore(tmp_path)
    published = store.publish(
        composite["snapshot"],
        publication_id="synthetic-pub",
        version_id="synthetic-ver",
        coverage_manifest_bytes=_canonical(composite["manifest"]),
        coverage_manifest_sha256=composite["manifest_sha256"],
        binding_bytes=_canonical(composite["binding"]),
        binding_sha256=composite["binding_sha256"],
    )
    assert published.visibility == "COMMITTED"
    pointer = (tmp_path / "CURRENT.json").read_bytes()
    result = review.compare_full(root=str(tmp_path), candidate_id=DERIVED, filesystem=FS)
    assert result["status"] == "SAME", result
    assert (tmp_path / "CURRENT.json").read_bytes() == pointer


def test_write_unknown_can_be_checked_under_original_id(tmp_path, monkeypatch):
    parent, supplement, decisions, _ = fixture(tmp_path)
    original = review.storage._write

    def unknown(*args):
        assert original(*args) == "COMMITTED"
        raise OSError("simulated post-commit error")

    monkeypatch.setattr(review.storage, "_write", unknown)
    result = derived(tmp_path, parent, supplement, decisions)
    assert result["status"] == "COMMIT_UNKNOWN"
    assert (
        review.show(root=str(tmp_path), candidate_id=DERIVED, filesystem=FS)["status"]
        == "VISIBLE_UNAUTHENTICATED"
    )


def test_budget_rejected_before_commit(tmp_path):
    parent, supplement, decisions, _ = fixture(tmp_path)
    supplement["sources"][0]["description"] = "x" * (review._INPUT_LIMIT + 1)
    result = derived(tmp_path, parent, supplement, decisions)
    assert result["status"] == "NOT_COMMITTED"
    assert not (tmp_path / "review-bundles" / f"candidate-{DERIVED}.json").exists()


def _process_derive(root, parent, supplement, decisions, queue):
    queue.put(derived(root, parent, supplement, decisions)["status"])


def test_concurrent_processes_do_not_replace_same_id(tmp_path):
    import multiprocessing

    parent, supplement, decisions, _ = fixture(tmp_path)
    context = multiprocessing.get_context("fork")
    queue = context.Queue()
    processes = [
        context.Process(
            target=_process_derive, args=(tmp_path, parent, supplement, decisions, queue)
        )
        for _ in range(2)
    ]
    for process in processes:
        process.start()
    for process in processes:
        process.join(30)
        assert process.exitcode == 0
    statuses = [queue.get(timeout=2) for _ in processes]
    assert statuses.count("COMMITTED") == 1
    assert set(statuses) <= {"COMMITTED", "NOT_COMMITTED", "COMMIT_UNKNOWN"}
    assert (
        review.show(root=str(tmp_path), candidate_id=DERIVED, filesystem=FS)["status"]
        == "VISIBLE_UNAUTHENTICATED"
    )


def test_no_network_and_no_current_access(tmp_path, monkeypatch):
    import socket

    parent, supplement, decisions, _ = fixture(tmp_path)
    current = tmp_path / "CURRENT.json"
    current.symlink_to(tmp_path / "unused")
    monkeypatch.setattr(socket.socket, "connect", lambda *a, **kw: pytest.fail("network used"))
    assert derived(tmp_path, parent, supplement, decisions)["status"] == "COMMITTED"
    assert (
        review.show(root=str(tmp_path), candidate_id=DERIVED, filesystem=FS)["status"]
        == "VISIBLE_UNAUTHENTICATED"
    )
    assert current.is_symlink() and not (tmp_path / "unused").exists()


def test_current_and_next_year_declared_coverage(tmp_path):
    parent, supplement, decisions, _ = fixture(tmp_path, years=2)
    result = derived(tmp_path, parent, supplement, decisions)
    assert result["review_status"] == "STRUCTURALLY_READY_UNVERIFIED", result
    saved = review.show(root=str(tmp_path), candidate_id=DERIVED, filesystem=FS)
    shard = next(s for s in saved["bundle"]["input"]["shards"] if s["venue"] == "SHFE")
    assert shard["coverage_end"] == "2027-12-31"
    assert (
        len([row for row in shard["layers"]["trading_days"] if row["day"] <= "2027-12-31"]) == 730
    )


def test_parent_digest_mismatch_is_not_committed(tmp_path):
    parent, supplement, decisions, _ = fixture(tmp_path)
    parent["payload_sha256"] = "0" * 64
    assert derived(tmp_path, parent, supplement, decisions)["status"] == "NOT_COMMITTED"


def test_parent_changed_immediately_before_write_is_not_committed(tmp_path, monkeypatch):
    from bt_api_py import trading_calendar_review_supplement as module

    parent, supplement, decisions, ids = fixture(tmp_path)
    original = module.evaluate_derived
    calls = 0

    def changed(**kwargs):
        nonlocal calls
        calls += 1
        result = original(**kwargs)
        if calls == 1:
            (tmp_path / "source-evidence" / f"result-{ids[2]}.json").unlink()
        return result

    monkeypatch.setattr(module, "evaluate_derived", changed)
    result = derived(tmp_path, parent, supplement, decisions)
    assert result["status"] == "NOT_COMMITTED" and result["reason"] == "SOURCE_CHANGED"
    assert not (tmp_path / "review-bundles" / f"candidate-{DERIVED}.json").exists()


def assert_partial_item(root, result, category):
    assert result["status"] == "COMMITTED" and result["review_status"] == "PARTIAL", result
    shown = review.show(root=str(root), candidate_id=DERIVED, filesystem=FS)
    assert shown["status"] == "VISIBLE_UNAUTHENTICATED"
    items = shown["bundle"]["assembly"]["item_decisions"]
    assert any(item["category"] == category and item["status"] == "UNRESOLVED" for item in items)
    return shown["bundle"]


def test_free_text_note_is_not_accepted_as_understood(tmp_path):
    parent, supplement, decisions, _ = fixture(tmp_path, note="注：香蕉。")
    for item in decisions:
        if item["category"] == "page_notes":
            item["reason"] = "香蕉"
    body = assert_partial_item(
        tmp_path, derived(tmp_path, parent, supplement, decisions), "page_notes"
    )
    assert any(
        item["category"] == "page_review_reasons" and item["status"] == "UNRESOLVED"
        for item in body["assembly"]["item_decisions"]
    )


def test_recognized_note_requires_its_exact_scoped_exception(tmp_path):
    parent, supplement, decisions, _ = fixture(tmp_path)
    supplement["exceptions"] = []
    assert_partial_item(tmp_path, derived(tmp_path, parent, supplement, decisions), "page_notes")


def test_announcement_reopening_cannot_be_overridden_closed(tmp_path):
    parent, supplement, decisions, _ = fixture(tmp_path, conflict_day="2026-02-17")
    result = derived(tmp_path, parent, supplement, decisions)
    assert result["status"] == "COMMITTED" and result["review_status"] == "PARTIAL"
    body = review.show(root=str(tmp_path), candidate_id=DERIVED, filesystem=FS)["bundle"]
    assert any(item["day"] == "2026-02-17" for item in body["assembly"]["original_openings"])
    shard = next(s for s in body["input"]["shards"] if s["venue"] == "SHFE")
    assert any("announcement_opening_conflict" in item for item in shard["unresolved"])


def test_declared_owner_requires_explicit_scoped_support_material(tmp_path):
    parent, supplement, decisions, _ = fixture(tmp_path)
    text = json.loads(support_text(parent, supplement, decisions))
    text["night_ownership"][0]["local_date"] = "2025-12-30"
    body = assert_partial_item(
        tmp_path,
        derived(tmp_path, parent, supplement, decisions, support=json.dumps(text)),
        "holiday_unresolved_nights",
    )
    assert body["report"]["source_authentication"] == "NOT_PROVEN"


def test_inferred_ine_next_day_requires_explicit_support(tmp_path):
    parent, supplement, decisions, _ = fixture(tmp_path, "INE")
    text = json.loads(support_text(parent, supplement, decisions))
    assert text["night_windows"]
    text["night_windows"] = []
    body = assert_partial_item(
        tmp_path,
        derived(tmp_path, parent, supplement, decisions, support=json.dumps(text)),
        "hours_unresolved_nights",
    )
    assert any(
        item["original"] == "CROSS_MIDNIGHT_UNVERIFIED" and item["status"] == "UNRESOLVED"
        for item in body["assembly"]["item_decisions"]
    )


def test_extra_night_stage_needs_specific_support(tmp_path):
    parent, supplement, decisions, _ = fixture(tmp_path)
    row = next(row for row in supplement["trading_hours"] if row["product"] == "cu")
    row["night_windows"].append(
        {
            "id": "extra",
            "phase": "continuous",
            "start": "23:10",
            "end": "23:30",
            "end_day_offset": 0,
        }
    )
    assert_partial_item(
        tmp_path, derived(tmp_path, parent, supplement, decisions), "hours_unresolved_nights"
    )


def test_extra_night_stage_with_precise_scoped_support(tmp_path):
    from bt_api_py.trading_calendar_review_supplement import _window_fact

    parent, supplement, decisions, _ = fixture(tmp_path)
    row = next(row for row in supplement["trading_hours"] if row["product"] == "cu")
    extra = {
        "id": "extra",
        "phase": "continuous",
        "start": "23:10",
        "end": "23:30",
        "end_day_offset": 0,
    }
    row["night_windows"].append(extra)
    text = json.loads(support_text(parent, supplement, decisions))
    text["night_windows"].append(_window_fact(row, extra))
    result = derived(tmp_path, parent, supplement, decisions, support=json.dumps(text))
    assert result["review_status"] == "STRUCTURALLY_READY_UNVERIFIED", result


def test_combined_input_budget_prevents_commit_before_unreadable_bundle(tmp_path):
    parent, supplement, decisions, _ = fixture(tmp_path, padding=True)
    extras = {"new1": "x" * 1_500_000, "new2": "y" * 1_500_000}
    for name in extras:
        supplement["sources"].append({"id": name, "description": "synthetic source"})
    result = derived(tmp_path, parent, supplement, decisions, extra_texts=extras)
    assert result["status"] == "NOT_COMMITTED", result
    assert not (tmp_path / "review-bundles" / f"candidate-{DERIVED}.json").exists()


def test_inferred_and_explicit_next_day_are_distinct():
    from bt_api_py.trading_calendar_review_supplement import _has_original_stages, _window_fact

    row = {
        "venue": "INE",
        "kind": "future",
        "product": "cu",
        "contracts": ["cu2701"],
        "valid_from": "2026-01-01",
        "valid_to": "2027-12-31",
        "source_ids": ["manual"],
        "night_mappings": [],
        "night_windows": [
            {
                "id": "n",
                "phase": "continuous",
                "start": "21:00",
                "end": "01:00",
                "end_day_offset": 1,
            }
        ],
    }
    stage = {
        "phase": "CONTINUOUS",
        "start": "21:00",
        "end": "01:00",
        "day_transition": "INFERRED_NEXT_DAY",
    }
    source = {
        "schema": "bt_api.calendar-review-support/1",
        "night_ownership": [],
        "night_windows": [_window_fact(row, row["night_windows"][0])],
    }
    decision = {"source_ids": ["manual"]}
    assert not _has_original_stages({"stages": [stage]}, row, {"manual": "unrelated"}, decision)
    assert _has_original_stages({"stages": [stage]}, row, {"manual": json.dumps(source)}, decision)
    source["night_windows"][0]["end_day_offset"] = True
    assert not _has_original_stages(
        {"stages": [stage]}, row, {"manual": json.dumps(source)}, decision
    )
    stage["day_transition"] = "EXPLICIT_NEXT_DAY"
    original_source = "ine.hours." + "a" * 64
    row["source_ids"] = [original_source]
    assert _has_original_stages(
        {"stages": [stage]},
        row,
        {},
        {"source_ids": [original_source]},
        original_sources={original_source},
    )


@pytest.mark.parametrize("variant", ["duplicate", "owner", "start_date", "disjoint_validity"])
def test_retained_support_conflict_blocks_owner_decision(tmp_path, variant):
    parent, supplement, decisions, _ = fixture(tmp_path)
    document = json.loads(support_text(parent, supplement, decisions))
    conflicting = deepcopy(document["night_ownership"][0])
    if variant == "owner":
        conflicting["owner_day"] = "2026-01-06"
    elif variant == "start_date":
        conflicting["start_date"] = conflicting["local_date"]
    elif variant == "disjoint_validity":
        conflicting["valid_from"] = "2028-01-01"
        conflicting["valid_to"] = "2028-12-31"
        conflicting["owner_day"] = "2028-01-05"
    document["night_ownership"].append(conflicting)
    assert_partial_item(
        tmp_path,
        derived(tmp_path, parent, supplement, decisions, support=json.dumps(document)),
        "holiday_unresolved_nights",
    )


@pytest.mark.parametrize("include_in_decision", [True, False])
def test_conflicting_owner_in_second_selected_source_blocks_decision(tmp_path, include_in_decision):
    parent, supplement, decisions, _ = fixture(tmp_path)
    first = json.loads(support_text(parent, supplement, decisions))
    conflict = deepcopy(first["night_ownership"][0])
    conflict["owner_day"] = "2026-01-06"
    second = {"schema": first["schema"], "night_ownership": [conflict], "night_windows": []}
    supplement["sources"].append({"id": "manual2", "description": "second synthetic source"})
    selected = next(
        item
        for item in decisions
        if item["category"] == "holiday_unresolved_nights" and item["index"] == 0
    )
    if include_in_decision:
        selected["source_ids"].append("manual2")
    for row in supplement["trading_hours"]:
        next(m for m in row["night_mappings"] if m["trading_day"] == "2026-01-05")[
            "source_ids"
        ].append("manual2")
    result = derived(
        tmp_path,
        parent,
        supplement,
        decisions,
        support=json.dumps(first),
        extra_texts={"manual2": json.dumps(second)},
    )
    body = assert_partial_item(tmp_path, result, "holiday_unresolved_nights")
    item = next(
        item
        for item in body["assembly"]["item_decisions"]
        if item["category"] == "holiday_unresolved_nights" and item["index"] == 0
    )
    assert "manual2" in item["source_ids_considered"]


def test_conflicting_support_windows_block_even_when_original_stages_match(tmp_path):
    from bt_api_py.trading_calendar_review_supplement import _window_fact

    parent, supplement, decisions, _ = fixture(tmp_path)
    document = json.loads(support_text(parent, supplement, decisions))
    row = next(item for item in supplement["trading_hours"] if item["product"] == "cu")
    first = _window_fact(
        row, {"phase": "continuous", "start": "21:00", "end": "23:00", "end_day_offset": 0}
    )
    second = {**first, "start": "22:55", "end": "23:05"}
    document["night_windows"] = [first, second]
    assert_partial_item(
        tmp_path,
        derived(tmp_path, parent, supplement, decisions, support=json.dumps(document)),
        "hours_unresolved_nights",
    )


def test_unsupported_omission_of_source_window_is_unresolved(tmp_path):
    from bt_api_py.trading_calendar_review_supplement import _window_fact

    parent, supplement, decisions, _ = fixture(tmp_path)
    document = json.loads(support_text(parent, supplement, decisions))
    row = next(item for item in supplement["trading_hours"] if item["product"] == "cu")
    document["night_windows"] = [
        _window_fact(
            row, {"phase": "continuous", "start": "23:10", "end": "23:30", "end_day_offset": 0}
        )
    ]
    assert_partial_item(
        tmp_path,
        derived(tmp_path, parent, supplement, decisions, support=json.dumps(document)),
        "hours_unresolved_nights",
    )


@pytest.mark.parametrize("conflict", [False, True])
@pytest.mark.parametrize("include_in_decision", [True, False])
def test_two_support_sources_window_relations(tmp_path, conflict, include_in_decision):
    from bt_api_py.trading_calendar_review_supplement import _window_fact

    parent, supplement, decisions, _ = fixture(tmp_path)
    first = json.loads(support_text(parent, supplement, decisions))
    row = next(item for item in supplement["trading_hours"] if item["product"] == "cu")
    early = next(item for item in row["night_windows"] if item["start"] == "20:55")
    continuous = next(item for item in row["night_windows"] if item["start"] == "21:00")
    first["night_windows"] = [_window_fact(row, early)]
    second_window = (
        _window_fact(
            row, {"phase": "continuous", "start": "20:58", "end": "21:01", "end_day_offset": 0}
        )
        if conflict
        else _window_fact(row, continuous)
    )
    second = {"schema": first["schema"], "night_ownership": [], "night_windows": [second_window]}
    supplement["sources"].append({"id": "manual2", "description": "second synthetic source"})
    row["source_ids"].append("manual2")
    selected = next(
        item
        for item in decisions
        if item["category"] == "hours_unresolved_nights" and item["index"] == 0
    )
    if include_in_decision:
        selected["source_ids"].append("manual2")
    result = derived(
        tmp_path,
        parent,
        supplement,
        decisions,
        support=json.dumps(first),
        extra_texts={"manual2": json.dumps(second)},
    )
    if conflict:
        body = assert_partial_item(tmp_path, result, "hours_unresolved_nights")
    else:
        assert result["review_status"] == "STRUCTURALLY_READY_UNVERIFIED", result
        body = review.show(root=str(tmp_path), candidate_id=DERIVED, filesystem=FS)["bundle"]
    item = next(
        item
        for item in body["assembly"]["item_decisions"]
        if item["category"] == "hours_unresolved_nights" and item["index"] == 0
    )
    assert "manual2" in item["source_ids_considered"]


@pytest.mark.parametrize("field", ["night_ownership", "night_windows"])
def test_identical_fact_from_distinct_referenced_sources_is_merged(tmp_path, field):
    from bt_api_py.trading_calendar_review_supplement import _window_fact

    parent, supplement, decisions, _ = fixture(tmp_path)
    first = json.loads(support_text(parent, supplement, decisions))
    supplement["sources"].append({"id": "manual2", "description": "second synthetic source"})
    if field == "night_ownership":
        fact = deepcopy(first[field][0])
        for row in supplement["trading_hours"]:
            next(m for m in row["night_mappings"] if m["trading_day"] == "2026-01-05")[
                "source_ids"
            ].append("manual2")
        category = "holiday_unresolved_nights"
    else:
        row = next(item for item in supplement["trading_hours"] if item["product"] == "cu")
        early = next(item for item in row["night_windows"] if item["start"] == "20:55")
        fact = _window_fact(row, early)
        first[field].append(fact)
        row["source_ids"].append("manual2")
        category = "hours_unresolved_nights"
    second = {"schema": first["schema"], "night_ownership": [], "night_windows": []}
    second[field] = [deepcopy(fact)]
    result = derived(
        tmp_path,
        parent,
        supplement,
        decisions,
        support=json.dumps(first),
        extra_texts={"manual2": json.dumps(second)},
    )
    assert result["review_status"] == "STRUCTURALLY_READY_UNVERIFIED", result
    body = review.show(root=str(tmp_path), candidate_id=DERIVED, filesystem=FS)["bundle"]
    item = next(
        item
        for item in body["assembly"]["item_decisions"]
        if item["category"] == category and item["index"] == 0
    )
    assert {"manual", "manual2"} <= set(item["source_ids_considered"])
    assert body["input"]["source_texts"]["manual2"] == json.dumps(second)


@pytest.mark.parametrize(
    "variant",
    [
        "unparseable",
        "missing",
        "wrong_day",
        "conflict",
        "extra",
        "extra_unattributed",
        "extra_attributed",
        "valid",
        "window_unattributed",
        "window_attributed",
        "ownership_unattributed",
        "ownership_attributed",
        "wildcard",
    ],
)
def test_note_checks_every_actual_exception_source(tmp_path, variant):
    parent, supplement, decisions, _ = fixture(tmp_path)
    exception = supplement["exceptions"][0]
    if variant == "wildcard":
        exception["contract"] = None
    exception["source_ids"].append("manual2")
    supplement["sources"].append({"id": "manual2", "description": "second synthetic source"})
    keys = ("venue", "kind", "product", "contract", "trading_day", "action", "window_id")
    fact = {key: exception[key] for key in keys}
    doc = {
        "schema": "bt_api.calendar-review-support/1",
        "night_ownership": [],
        "night_windows": [],
        "exceptions": [fact],
    }
    if variant == "missing":
        doc["exceptions"] = []
    elif variant == "wrong_day":
        doc["exceptions"][0] = {**fact, "trading_day": "2026-01-06"}
    elif variant == "conflict":
        doc["exceptions"].append({**fact, "action": "close_day", "window_id": None})
    elif variant == "extra":
        doc["exceptions"].append({**fact, "window_id": "day.continuous.1030.1130"})
    elif variant in {"extra_unattributed", "extra_attributed"}:
        extra = {**fact, "window_id": "day.continuous.1030.1130"}
        doc["exceptions"].append(extra)
        supplement["exceptions"].append(
            {
                **extra,
                "source_ids": ["manual", "manual2"]
                if variant == "extra_attributed"
                else ["manual"],
            }
        )
    elif variant.startswith("window_"):
        from bt_api_py.trading_calendar_review_supplement import _window_fact

        row = next(item for item in supplement["trading_hours"] if item["product"] == "cu")
        window = next(item for item in row["night_windows"] if item["start"] == "21:00")
        doc["night_windows"].append(_window_fact(row, window))
        if variant == "window_attributed":
            row["source_ids"].append("manual2")
    elif variant.startswith("ownership_"):
        row = next(item for item in supplement["trading_hours"] if item["product"] == "cu")
        manual = json.loads(support_text(parent, supplement, decisions))
        ownership = next(item for item in manual["night_ownership"] if item["product"] == "cu")
        doc["night_ownership"].append(ownership)
        if variant == "ownership_attributed":
            next(
                item
                for item in row["night_mappings"]
                if item["trading_day"] == ownership["owner_day"]
            )["source_ids"].append("manual2")
    text = "unparseable source" if variant == "unparseable" else json.dumps(doc)
    result = derived(tmp_path, parent, supplement, decisions, extra_texts={"manual2": text})
    if variant in {"valid", "extra_attributed", "window_attributed", "ownership_attributed"}:
        assert result["review_status"] == "STRUCTURALLY_READY_UNVERIFIED", result
        body = review.show(root=str(tmp_path), candidate_id=DERIVED, filesystem=FS)["bundle"]
    else:
        body = assert_partial_item(tmp_path, result, "page_notes")
    note = next(
        item for item in body["assembly"]["item_decisions"] if item["category"] == "page_notes"
    )
    assert {"manual", "manual2"} <= set(note["source_ids_considered"])
    assert "manual2" not in note["decision"]["source_ids"]
    if variant in {"valid", "extra_attributed", "window_attributed", "ownership_attributed"}:
        assert note["status"] == "RESOLVED_UNVERIFIED"
        assert body["input"]["source_texts"]["manual2"] == text


@pytest.mark.parametrize("note", [NOTE, None], ids=["with-note", "without-note"])
@pytest.mark.parametrize("candidate_includes_fact", [False, True])
def test_source_exception_on_other_open_date_is_accounted_for(
    tmp_path, note, candidate_includes_fact
):
    parent, supplement, decisions, _ = fixture(tmp_path, note=note)
    if note is None:
        assert not any(d["category"] == "page_notes" for d in decisions)
    document = json.loads(support_text(parent, supplement, decisions))
    fact = {**document["exceptions"][0], "trading_day": "2026-02-17"}
    document["exceptions"].append(fact)
    if candidate_includes_fact:
        supplement["exceptions"].append({**fact, "source_ids": ["manual"]})
    result = derived(tmp_path, parent, supplement, decisions, support=json.dumps(document))
    if candidate_includes_fact:
        assert result["review_status"] == "STRUCTURALLY_READY_UNVERIFIED", result
    else:
        assert_partial_item(tmp_path, result, "hours_unresolved_nights")


@pytest.mark.parametrize("case", ["missing", "unparseable", "original_not_asserted", "valid"])
def test_without_notes_candidate_exception_sources_must_support_fact(tmp_path, case):
    parent, supplement, decisions, _ = fixture(tmp_path, note=None)
    assert not any(d["category"] == "page_notes" for d in decisions)
    exception = supplement["exceptions"][0]
    texts = {}
    if case in {"unparseable", "valid"}:
        supplement["sources"].append({"id": "manual2", "description": "second synthetic source"})
        exception["source_ids"].append("manual2")
        fact = {
            key: exception[key]
            for key in (
                "venue",
                "kind",
                "product",
                "contract",
                "trading_day",
                "action",
                "window_id",
            )
        }
        document = {
            "schema": "bt_api.calendar-review-support/1",
            "night_ownership": [],
            "night_windows": [],
            "exceptions": [fact],
        }
        texts["manual2"] = "unparseable" if case == "unparseable" else json.dumps(document)
    elif case == "original_not_asserted":
        source = next(row["id"] for row in supplement["sources"] if ".hours." in row["id"])
        exception["source_ids"].append(source)
    manual = json.loads(support_text(parent, supplement, decisions))
    if case == "missing":
        manual["exceptions"] = []
    result = derived(
        tmp_path, parent, supplement, decisions, support=json.dumps(manual), extra_texts=texts
    )
    if case == "valid":
        assert result["review_status"] == "STRUCTURALLY_READY_UNVERIFIED", result
        body = review.show(root=str(tmp_path), candidate_id=DERIVED, filesystem=FS)["bundle"]
    else:
        body = assert_partial_item(tmp_path, result, "hours_unresolved_nights")
    if case in {"unparseable", "valid"}:
        item = next(
            item
            for item in body["assembly"]["item_decisions"]
            if item["category"] == "hours_unresolved_nights" and item["index"] == 0
        )
        assert "manual2" in item["source_ids_considered"]
