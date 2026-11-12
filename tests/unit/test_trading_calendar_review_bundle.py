"""Unverified synthetic calendar review bundle, never a publication."""

import hashlib
import json
import os
import subprocess
from concurrent.futures import ThreadPoolExecutor
from copy import deepcopy
from datetime import date, timedelta

import pytest

from bt_api_py import trading_calendar_review_bundle as bundle
from bt_api_py.trading_calendar_preview_reports import TRUSTED_LOCAL_POSIX
from bt_api_py.trading_calendar_store import _canonical
from tests.calendar_test_store import FixtureCalendarStore

VENUES = ("SHFE", "INE", "DCE", "CZCE", "CFFEX", "GFEX")
START = date(2026, 1, 1)
CID = "a" * 32


def sample(days=2):
    end = START + timedelta(days=days - 1)
    shards = []
    for venue in VENUES:
        content = {
            "sources": [{"id": "synthetic", "description": "fictional"}],
            "trading_days": [
                {
                    "venue": venue,
                    "day": (START + timedelta(days=n)).isoformat(),
                    "status": "closed",
                    "source_ids": ["synthetic"],
                }
                for n in range(days)
            ],
            "trading_hours": [],
            "exceptions": [],
        }
        requirements = []
        for kind in ("future", "option"):
            product = "p" + kind
            contract = venue.lower() + kind + "2701"
            content["trading_hours"].append(
                {
                    "venue": venue,
                    "kind": kind,
                    "product": product,
                    "contracts": [contract],
                    "valid_from": START.isoformat(),
                    "valid_to": end.isoformat(),
                    "day_windows": [
                        {
                            "id": "day",
                            "phase": "continuous",
                            "start": "09:00",
                            "end": "10:00",
                            "end_day_offset": 0,
                        }
                    ],
                    "night_windows": [],
                    "night_mappings": [],
                    "windows_complete": True,
                    "exceptions_complete": True,
                    "source_ids": ["synthetic"],
                }
            )
            requirements.append(
                {
                    "kind": kind,
                    "product": product,
                    "contract": contract,
                    "start_day": START.isoformat(),
                    "end_day": end.isoformat(),
                }
            )
        shards.append(
            {
                "venue": venue,
                "coverage_start": START.isoformat(),
                "coverage_end": end.isoformat(),
                "layers": content,
                "requirements": requirements,
                "unresolved": [],
            }
        )
    return {"shards": shards, "source_texts": {"synthetic": "fictional primary source text"}}


def build(root, value, candidate_id=CID):
    return bundle.build_once(
        root=str(root),
        candidate_id=candidate_id,
        input_data=value,
        filesystem=TRUSTED_LOCAL_POSIX,
        emit=lambda _: None,
    )


def show(root, candidate_id=CID):
    return bundle.show(root=str(root), candidate_id=candidate_id, filesystem=TRUSTED_LOCAL_POSIX)


def test_six_venues_futures_options_build_show_without_current(tmp_path):
    value = sample()
    original = deepcopy(value)
    result = build(tmp_path, value)
    assert result["status"] == "COMMITTED"
    assert result["review_status"] == "STRUCTURALLY_READY_UNVERIFIED"
    saved = show(tmp_path)
    assert saved["status"] == "VISIBLE_UNAUTHENTICATED"
    assert saved["bundle"]["input"] == original == value
    report = saved["bundle"]["report"]
    assert report["checker_mode"] == "SIMULATION_ONLY"
    assert report["source_authentication"] == "NOT_PROVEN"
    assert report["coverage_authentication"] == "NOT_PROVEN"
    assert report["publication"] == "NOT_AUTHORIZED"
    assert report["source_bindings"] == [{"source_id": "synthetic", "status": "UNBOUND"}]
    assert (
        saved["payload_sha256"] == hashlib.sha256(bundle.storage._json(saved["bundle"])).hexdigest()
    )
    assert len(report["shards"]) == 6
    assert len(report["composite"]["snapshot"]["trading_days"]) == 12
    assert not (tmp_path / "CURRENT.json").exists()


@pytest.mark.parametrize(
    "problem",
    [
        "missing_text",
        "hash_only",
        "unresolved",
        "missing_unresolved",
        "unrecognized_unresolved",
        "missing_option",
        "missing_day",
        "missing_scope",
        "conflict",
    ],
)
def test_incomplete_or_conflicting_inputs_are_retained_as_partial(tmp_path, problem):
    value = sample()
    if problem == "missing_text":
        value.pop("source_texts")
    elif problem == "hash_only":
        value["source_texts"]["synthetic"] = "a" * 64
    elif problem == "unresolved":
        value["shards"][0]["unresolved"] = [{"raw": "a night needs review"}]
    elif problem == "missing_unresolved":
        value["shards"][0].pop("unresolved")
    elif problem == "unrecognized_unresolved":
        value["shards"][0]["unresolved_nights"] = [{"raw": "pending"}]
    elif problem == "missing_option":
        value["shards"][0]["requirements"].pop()
    elif problem == "missing_day":
        value["shards"][0]["layers"]["trading_days"].pop()
    elif problem == "missing_scope":
        value["shards"].pop()
    else:
        value["shards"][2]["layers"]["sources"][0]["description"] = "conflicting text"
    assert build(tmp_path, value)["review_status"] == "PARTIAL"
    saved = show(tmp_path)
    assert saved["status"] == "VISIBLE_UNAUTHENTICATED"
    assert saved["bundle"]["input"] == value
    assert saved["bundle"]["report"]["publication"] == "NOT_AUTHORIZED"
    assert not (tmp_path / "CURRENT.json").exists()


def test_duplicate_id_does_not_replace_original(tmp_path):
    assert build(tmp_path, sample())["status"] == "COMMITTED"
    original = show(tmp_path)
    modified = sample()
    modified["shards"][0]["unresolved"].append("pending")
    assert build(tmp_path, modified)["status"] == "NOT_COMMITTED"
    assert show(tmp_path) == original


def test_tampering_invalidates_read_and_recomputed_report(tmp_path):
    assert build(tmp_path, sample())["status"] == "COMMITTED"
    path = tmp_path / "review-bundles" / f"candidate-{CID}.json"
    document = json.loads(path.read_bytes())
    document["body"]["report"]["publication"] = "AUTHORIZED"
    path.write_bytes(bundle.storage._envelope(document["body"]))
    assert show(tmp_path)["status"] == "UNKNOWN"


def test_concurrent_writers_do_not_overwrite(tmp_path):
    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(lambda _: build(tmp_path, sample()), range(2)))
    assert sum(r["status"] == "COMMITTED" for r in results) == 1
    assert all(r["status"] in {"COMMITTED", "NOT_COMMITTED", "COMMIT_UNKNOWN"} for r in results)
    assert show(tmp_path)["status"] == "VISIBLE_UNAUTHENTICATED"


def test_concurrent_processes_do_not_overwrite(tmp_path):
    command = [
        "/Users/yunjinqi/opt/anaconda3/bin/conda",
        "run",
        "--no-capture-output",
        "-n",
        "base",
        "python",
        "-m",
        "bt_api_py.trading_calendar_review_bundle",
        "build",
        "--calendar-root",
        str(tmp_path),
        "--candidate-id",
        CID,
        "--trusted-local-posix",
    ]
    processes = [
        subprocess.Popen(
            command,
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
        )
        for _ in range(2)
    ]
    for process in processes:
        process.stdin.write(json.dumps(sample()))
        process.stdin.close()
        process.stdin = None
    outputs = [p.communicate(timeout=30) for p in processes]
    results = [json.loads(out.splitlines()[-1]) for out, err in outputs]
    assert sum(r["status"] == "COMMITTED" for r in results) == 1
    assert all(r["status"] in {"COMMITTED", "NOT_COMMITTED", "COMMIT_UNKNOWN"} for r in results)
    assert show(tmp_path)["status"] == "VISIBLE_UNAUTHENTICATED"


def test_uncertain_write_inspected_with_original_id(tmp_path, monkeypatch):
    original = bundle.storage._write

    def uncertain(*args):
        assert original(*args) == "COMMITTED"
        return "COMMIT_UNKNOWN"

    monkeypatch.setattr(bundle.storage, "_write", uncertain)
    assert build(tmp_path, sample())["status"] == "COMMIT_UNKNOWN"
    assert show(tmp_path)["status"] == "VISIBLE_UNAUTHENTICATED"
    assert not (tmp_path / "CURRENT.json").exists()


def test_two_year_six_venue_capacity(tmp_path):
    assert build(tmp_path, sample(730))["review_status"] == "STRUCTURALLY_READY_UNVERIFIED"
    data = show(tmp_path)
    assert data["status"] == "VISIBLE_UNAUTHENTICATED"
    assert len(data["bundle"]["report"]["composite"]["snapshot"]["trading_days"]) == 4380


def test_missing_dates_identify_each_venue_and_keep_original_checker(tmp_path):
    value = sample(days=3)
    value["shards"][0]["layers"]["trading_days"].pop()
    value["shards"][4]["layers"]["trading_days"].pop()
    value["shards"][4]["layers"]["trading_days"].pop()
    assert build(tmp_path, value)["review_status"] == "PARTIAL"
    data = show(tmp_path)
    report = data["bundle"]["report"]
    dates = {item["venue"]: item for item in report["shard_inputs"]}
    assert (dates["SHFE"]["date_status"], dates["SHFE"]["missing_date_count"]) == (
        "MISSING_DATES",
        1,
    )
    assert (dates["CFFEX"]["date_status"], dates["CFFEX"]["missing_date_count"]) == (
        "MISSING_DATES",
        2,
    )
    assert dates["DCE"]["date_status"] == "DECLARED_DATES_COMPLETE_UNVERIFIED"
    assert report["checker_status"] == "CHECK_FAILED"
    assert report["checker_reason"] == "VENUE_DATE_RECORDS_INCOMPLETE"
    assert data["bundle"]["input"] == value


def test_duplicate_dates_are_attributed_even_when_candidate_builder_rejects(tmp_path):
    value = sample()
    rows = value["shards"][2]["layers"]["trading_days"]
    rows.append(deepcopy(rows[0]))
    assert build(tmp_path, value)["review_status"] == "PARTIAL"
    report = show(tmp_path)["bundle"]["report"]
    dates = {item["venue"]: item for item in report["shard_inputs"]}
    assert dates["DCE"]["date_status"] == "DUPLICATE_DATES"
    assert dates["DCE"]["duplicate_date_count"] == 1
    assert dates["DCE"]["missing_date_count"] == 0
    assert dates["DCE"]["status"] == "INVALID_OR_INCOMPLETE"
    assert report["checker_status"] == "NOT_RUN"


def test_date_summary_omits_caller_information(tmp_path):
    value = sample()
    value["shards"][1]["layers"]["trading_days"].pop()
    value["metadata"] = {"note": "/unrelated/private/location", "secret": "sensitive-value"}
    assert build(tmp_path, value)["status"] == "COMMITTED"
    output = bundle._summary(show(tmp_path))
    rows = {item["venue"]: item for item in output["venue_date_checks"]}
    assert rows["INE"]["missing_date_count"] == 1
    encoded = json.dumps(output)
    assert "/unrelated/private/location" not in encoded
    assert "sensitive-value" not in encoded
    assert "fictional primary source text" not in encoded


@pytest.mark.parametrize("prefix", ["shfe.hours.", "shfe.holiday.2026."])
@pytest.mark.parametrize("mismatch", [False, True])
def test_shfe_source_digest_checks_exact_utf8_bytes_including_bom(tmp_path, prefix, mismatch):
    original = "\ufeff<html>source text</html>"
    source_id = prefix + hashlib.sha256(original.encode("utf-8")).hexdigest()
    value = sample()
    value["source_texts"] = {source_id: original.removeprefix("\ufeff") if mismatch else original}
    for shard in value["shards"]:
        layers = shard["layers"]
        layers["sources"][0]["id"] = source_id
        for layer in ("trading_days", "trading_hours"):
            for row in layers[layer]:
                row["source_ids"] = [source_id]
    result = build(tmp_path, value)
    assert result["review_status"] == ("PARTIAL" if mismatch else "STRUCTURALLY_READY_UNVERIFIED")
    body = show(tmp_path)["bundle"]
    expected = "MISMATCH_OR_LOSSY_ENCODING" if mismatch else "MATCH_UNAUTHENTICATED"
    assert body["report"]["source_bindings"] == [{"source_id": source_id, "status": expected}]
    assert body["report"]["source_authentication"] == "NOT_PROVEN"
    summary = bundle._summary(show(tmp_path))
    assert summary["source_binding_counts"][expected] == 1
    assert source_id not in json.dumps(summary)


def test_cli_build_show_with_conda_input(tmp_path):
    command = [
        "/Users/yunjinqi/opt/anaconda3/bin/conda",
        "run",
        "--no-capture-output",
        "-n",
        "base",
        "python",
        "-m",
        "bt_api_py.trading_calendar_review_bundle",
    ]
    common = ["--calendar-root", str(tmp_path), "--candidate-id", CID, "--trusted-local-posix"]
    built = subprocess.run(
        command + ["build"] + common,
        input=json.dumps(sample()),
        text=True,
        capture_output=True,
        env=os.environ.copy(),
    )
    assert built.returncode == 10, built.stderr
    assert json.loads(built.stdout.splitlines()[-1])["status"] == "COMMITTED"
    viewed = subprocess.run(
        command + ["show"] + common, text=True, capture_output=True, env=os.environ.copy()
    )
    assert viewed.returncode == 10, viewed.stderr
    summary = json.loads(viewed.stdout)
    assert summary["status"] == "VISIBLE_UNAUTHENTICATED"
    assert len(summary["declared_ranges"]) == 6
    assert "fictional primary source text" not in viewed.stdout
    assert str(tmp_path) not in viewed.stdout
    assert "bundle" not in summary


def test_interrupt_during_commit_is_unknown(tmp_path, monkeypatch):
    def interrupt(*args):
        raise KeyboardInterrupt

    monkeypatch.setattr(bundle.storage, "_write", interrupt)
    result = build(tmp_path, sample())
    assert result["status"] == "COMMIT_UNKNOWN" and result["candidate_id"] == CID
    assert show(tmp_path)["status"] == "UNKNOWN"


def test_over_budget_and_cyclic_input_are_rejected_before_write(tmp_path):
    value = []
    value.append(value)
    assert build(tmp_path, value)["status"] == "NOT_COMMITTED"
    assert not (tmp_path / "review-bundles").exists()


def published_candidate(root, value):
    assert build(root, value)["status"] == "COMMITTED"
    saved = show(root)
    composite = saved["bundle"]["report"]["composite"]
    assert composite
    store = FixtureCalendarStore(root)
    result = store.publish(
        composite["snapshot"],
        publication_id="test-pub",
        version_id="test-ver",
        coverage_manifest_bytes=_canonical(composite["manifest"]),
        coverage_manifest_sha256=composite["manifest_sha256"],
        binding_bytes=_canonical(composite["binding"]),
        binding_sha256=composite["binding_sha256"],
    )
    assert result.visibility == "COMMITTED", result
    return saved, store


def compare_request(saved, store):
    current = store.read_current_with_consistent_receipts().snapshot
    r = saved["bundle"]["input"]["shards"][0]["requirements"][0]
    return {
        "candidate_payload_sha256": saved["payload_sha256"],
        "current_publication_id": current.publication_id,
        "current_publication_sha256": current.publication_sha256,
        "scope": {"venue": "SHFE", **r},
        "baseline_source_texts": {"synthetic": "fictional primary source text"},
        "candidate_source_texts": {"synthetic": "fictional primary source text"},
    }


def compare(root, request, candidate_id=CID):
    return bundle.compare_current(
        root=str(root), candidate_id=candidate_id, filesystem=TRUSTED_LOCAL_POSIX, request=request
    )


def test_real_six_venue_two_year_store_scope_compare_and_cli(tmp_path):
    saved, store = published_candidate(tmp_path, sample(730))
    request = compare_request(saved, store)
    result = compare(tmp_path, request)
    assert result["status"] == "SAME", result
    assert result["manifest_comparison"] == result["other_scopes"] == "NOT_COMPARED"
    assert result["current_observation"] == "RECHECKED_UNDER_LOCK_NOT_PINNED"
    assert result["publication"] == "NOT_AUTHORIZED"
    command = [
        "/Users/yunjinqi/opt/anaconda3/bin/conda",
        "run",
        "--no-capture-output",
        "-n",
        "base",
        "python",
        "-m",
        "bt_api_py.trading_calendar_review_bundle",
        "compare",
        "--calendar-root",
        str(tmp_path),
        "--candidate-id",
        CID,
        "--trusted-local-posix",
    ]
    proc = subprocess.run(
        command, input=json.dumps(request), text=True, capture_output=True, env=os.environ.copy()
    )
    assert proc.returncode == 10, (proc.stdout, proc.stderr)
    assert json.loads(proc.stdout)["status"] == "SAME"
    assert "fictional primary source text" not in proc.stdout
    assert str(tmp_path) not in proc.stdout


def test_scope_changes_and_boundaries(tmp_path):
    baseline = sample()
    saved, store = published_candidate(tmp_path, baseline)
    changed = sample()
    changed["shards"][0]["layers"]["trading_hours"][0]["day_windows"][0]["end"] = "09:45"
    candidate_id = "b" * 32
    assert build(tmp_path, changed, candidate_id)["status"] == "COMMITTED"
    next_saved = show(tmp_path, candidate_id)
    request = compare_request(next_saved, store)
    result = compare(tmp_path, request, candidate_id)
    assert result["status"] == "CHANGED", result
    assert any(
        c["category"] == "trading_session" and c["operation"] == "modified"
        for c in result["changes"]
    )
    request["scope"]["start_day"] = "2025-12-31"
    assert compare(tmp_path, request, candidate_id)["status"] == "UNKNOWN"
    request["scope"]["start_day"] = "2026-01-02"
    request["scope"]["end_day"] = "2026-01-01"
    assert compare(tmp_path, request, candidate_id)["status"] == "UNKNOWN"


def open_shfe(value):
    shard = value["shards"][0]
    days = shard["layers"]["trading_days"]
    for day in days:
        day["status"] = "open"
    for row in shard["layers"]["trading_hours"]:
        row["night_mappings"] = [
            {"trading_day": day["day"], "start_date": None, "source_ids": ["synthetic"]}
            for day in days
        ]


@pytest.mark.parametrize(
    "change,category,operation",
    [
        ("add", "trading_session", "added"),
        ("remove", "trading_session", "removed"),
        ("night", "night_mapping", "modified"),
        ("exception", "exception", "added"),
    ],
)
def test_actual_scoped_add_remove_night_and_exception(tmp_path, change, category, operation):
    original = sample()
    open_shfe(original)
    if change == "remove":
        original["shards"][0]["layers"]["trading_hours"][0]["day_windows"].append(
            {
                "id": "late",
                "phase": "continuous",
                "start": "10:15",
                "end": "11:00",
                "end_day_offset": 0,
            }
        )
    if change == "night":
        original["shards"][0]["layers"]["trading_hours"][0]["night_windows"] = [
            {
                "id": "night",
                "phase": "continuous",
                "start": "21:00",
                "end": "23:00",
                "end_day_offset": 0,
            }
        ]
    _, store = published_candidate(tmp_path, original)
    updated = deepcopy(original)
    layers = updated["shards"][0]["layers"]
    row = layers["trading_hours"][0]
    if change == "add":
        row["day_windows"].append(
            {
                "id": "late",
                "phase": "continuous",
                "start": "10:15",
                "end": "11:00",
                "end_day_offset": 0,
            }
        )
    elif change == "remove":
        row["day_windows"].pop()
    elif change == "night":
        row["night_mappings"][1]["start_date"] = "2026-01-01"
    else:
        layers["exceptions"].append(
            {
                "venue": "SHFE",
                "kind": row["kind"],
                "product": row["product"],
                "contract": row["contracts"][0],
                "trading_day": "2026-01-01",
                "action": "close_day",
                "window_id": None,
                "source_ids": ["synthetic"],
            }
        )
    candidate_id = "b" * 32
    assert build(tmp_path, updated, candidate_id)["status"] == "COMMITTED"
    saved = show(tmp_path, candidate_id)
    result = compare(tmp_path, compare_request(saved, store), candidate_id)
    assert result["status"] == "CHANGED", result
    assert any(
        item["category"] == category and item["operation"] == operation
        for item in result["changes"]
    )


def test_other_contract_change_does_not_claim_global_equality(tmp_path):
    original = sample()
    _, store = published_candidate(tmp_path, original)
    updated = deepcopy(original)
    updated["shards"][0]["layers"]["trading_hours"][1]["day_windows"][0]["end"] = "09:45"
    candidate_id = "b" * 32
    assert build(tmp_path, updated, candidate_id)["status"] == "COMMITTED"
    result = compare(tmp_path, compare_request(show(tmp_path, candidate_id), store), candidate_id)
    assert result["status"] == "SAME"
    assert result["other_scopes"] == result["manifest_comparison"] == "NOT_COMPARED"


def test_legacy_current_does_not_assert_coverage(tmp_path):
    assert build(tmp_path, sample())["status"] == "COMMITTED"
    saved = show(tmp_path)
    store = FixtureCalendarStore(tmp_path)
    assert (
        store.publish(
            saved["bundle"]["report"]["composite"]["snapshot"],
            publication_id="legacy",
            version_id="legacy-version",
        ).visibility
        == "COMMITTED"
    )
    assert (
        compare(tmp_path, compare_request(saved, store))["reason"]
        == "CURRENT_COVERAGE_NOT_DECLARED"
    )


def test_exact_scoped_sources_exclude_other_products(tmp_path):
    value = sample()
    for shard in value["shards"]:
        shard["layers"]["sources"].append({"id": "option_source", "description": "other"})
        shard["layers"]["trading_hours"][1]["source_ids"] = ["option_source"]
    value["source_texts"]["option_source"] = "option source text"
    saved, store = published_candidate(tmp_path, value)
    result = compare(tmp_path, compare_request(saved, store))
    assert result["status"] == "SAME", result
    assert result["source_binding_counts"]["UNBOUND"] == 2


def test_shfe_baseline_source_digest_mismatch_returns_unknown(tmp_path):
    value = sample()
    text = "\ufeff<html>old official bytes</html>"
    source_id = "shfe.hours." + hashlib.sha256(text.encode("utf-8")).hexdigest()
    value["source_texts"] = {source_id: text}
    for shard in value["shards"]:
        shard["layers"]["sources"][0]["id"] = source_id
        for layer in ("trading_days", "trading_hours"):
            for row in shard["layers"][layer]:
                row["source_ids"] = [source_id]
    saved, store = published_candidate(tmp_path, value)
    request = compare_request(saved, store)
    request["baseline_source_texts"] = {source_id: text}
    request["candidate_source_texts"] = {source_id: text}
    assert compare(tmp_path, request)["status"] == "SAME"
    request["baseline_source_texts"][source_id] = text.removeprefix("\ufeff")
    assert compare(tmp_path, request)["reason"] == "SOURCE_TEXT_MISSING_OR_MISMATCH"


def set_source(value, source_id):
    value["source_texts"] = {source_id: "fictional source body"}
    for shard in value["shards"]:
        shard["layers"]["sources"][0]["id"] = source_id
        for layer in ("trading_days", "trading_hours"):
            for row in shard["layers"][layer]:
                row["source_ids"] = [source_id]
                for mapping in row.get("night_mappings", []):
                    mapping["source_ids"] = [source_id]


@pytest.mark.parametrize("source_changed", [True, False])
def test_compare_cli_never_prints_source_ids_on_unknown_or_changed(tmp_path, source_changed):
    marker = "SecretSourceMarker123"
    initial = sample()
    if not source_changed:
        set_source(initial, marker)
    _, store = published_candidate(tmp_path, initial)
    updated = deepcopy(initial)
    if source_changed:
        set_source(updated, marker)
    else:
        updated["shards"][0]["layers"]["trading_days"][0]["status"] = "open"
        for row in updated["shards"][0]["layers"]["trading_hours"]:
            row["night_mappings"] = [
                {"trading_day": "2026-01-01", "start_date": None, "source_ids": [marker]}
            ]
    candidate_id = "b" * 32
    assert build(tmp_path, updated, candidate_id)["status"] == "COMMITTED"
    saved = show(tmp_path, candidate_id)
    request = compare_request(saved, store)
    if not source_changed:
        request["baseline_source_texts"] = {marker: "fictional source body"}
    request["candidate_source_texts"] = {marker: "fictional source body"}
    command = [
        "/Users/yunjinqi/opt/anaconda3/bin/conda",
        "run",
        "--no-capture-output",
        "-n",
        "base",
        "python",
        "-m",
        "bt_api_py.trading_calendar_review_bundle",
        "compare",
        "--calendar-root",
        str(tmp_path),
        "--candidate-id",
        candidate_id,
        "--trusted-local-posix",
    ]
    result = subprocess.run(
        command, input=json.dumps(request), text=True, capture_output=True, env=os.environ.copy()
    )
    payload = json.loads(result.stdout)
    assert payload["status"] == ("UNKNOWN" if source_changed else "CHANGED")
    assert payload["source_identifiers"] == "REDACTED"
    assert marker not in result.stdout
    assert "fictional source body" not in result.stdout
    assert marker not in json.dumps(compare(tmp_path, request, candidate_id))
    if source_changed:
        assert any(
            c["category"] == "source_basis" and c["key"] == "SOURCE_ID_REDACTED"
            for c in payload["changes"]
        )
    else:
        assert any(
            c["category"] == "trading_day" and c["after"]["source_reference_count"] == 1
            for c in payload["changes"]
        )


def test_change_sanitizer_redacts_nested_source_ids():
    from bt_api_ctp.calendar_semantic_diff import CalendarChange

    value = json.dumps({"contracts": [{"source_ids": ["SecretSourceMarker123"]}]})
    result = bundle._safe_change(
        CalendarChange("applicability", '["SHFE"]', "modified", value, value)
    )
    assert "SecretSourceMarker123" not in json.dumps(result)
    assert result["before"]["contracts"][0] == {"source_reference_count": 1}


def test_partial_candidate_cannot_compare(tmp_path):
    _, store = published_candidate(tmp_path, sample())
    value = sample()
    value["shards"][0]["unresolved"].append("unknown night")
    candidate_id = "b" * 32
    assert build(tmp_path, value, candidate_id)["review_status"] == "PARTIAL"
    saved = show(tmp_path, candidate_id)
    result = compare(tmp_path, compare_request(saved, store), candidate_id)
    assert (result["status"], result["reason"]) == ("UNKNOWN", "CANDIDATE_PARTIAL")


def test_current_switch_during_comparison_is_unknown(tmp_path, monkeypatch):
    saved, store = published_candidate(tmp_path, sample())
    request = compare_request(saved, store)
    updated = sample()
    updated["shards"][0]["layers"]["trading_hours"][0]["day_windows"][0]["end"] = "09:45"
    candidate_id = "b" * 32
    assert build(tmp_path, updated, candidate_id)["status"] == "COMMITTED"
    composite = show(tmp_path, candidate_id)["bundle"]["report"]["composite"]
    from bt_api_py.trading_calendar_store import TradingCalendarStore

    original = TradingCalendarStore.read_current_with_consistent_receipts
    calls = 0

    def switched(self):
        nonlocal calls
        calls += 1
        if calls == 2:
            result = store.publish(
                composite["snapshot"],
                publication_id="next-pub",
                version_id="next-ver",
                coverage_manifest_bytes=_canonical(composite["manifest"]),
                coverage_manifest_sha256=composite["manifest_sha256"],
                binding_bytes=_canonical(composite["binding"]),
                binding_sha256=composite["binding_sha256"],
            )
            assert result.visibility == "COMMITTED"
        return original(self)

    monkeypatch.setattr(TradingCalendarStore, "read_current_with_consistent_receipts", switched)
    result = compare(tmp_path, request)
    assert (result["status"], result["reason"]) == ("UNKNOWN", "CURRENT_OR_CANDIDATE_CHANGED")


@pytest.mark.parametrize(
    "bad",
    [
        "candidate_hash",
        "current_id",
        "current_hash",
        "source_old",
        "source_new",
        "scope",
        "missing_current",
        "pending",
        "lock",
        "corrupt",
    ],
)
def test_current_comparison_rejects_uncertain_evidence(tmp_path, bad):
    saved, store = published_candidate(tmp_path, sample())
    request = compare_request(saved, store)
    if bad == "candidate_hash":
        request["candidate_payload_sha256"] = "0" * 64
    elif bad == "current_id":
        request["current_publication_id"] = "wrong"
    elif bad == "current_hash":
        request["current_publication_sha256"] = "0" * 64
    elif bad == "source_old":
        request["baseline_source_texts"] = {}
    elif bad == "source_new":
        request["candidate_source_texts"]["synthetic"] = "another source text"
    elif bad == "scope":
        request["scope"]["contract"] = "missing"
    elif bad == "missing_current":
        (tmp_path / "CURRENT.json").unlink()
    elif bad == "pending":
        (tmp_path / "PENDING.json").write_text("{}")
    elif bad == "lock":
        (tmp_path / ".lock").unlink()
    elif bad == "corrupt":
        (tmp_path / "CURRENT.json").write_text("{}")
    assert compare(tmp_path, request)["status"] == "UNKNOWN"
