"""Full declaration observations use synthetic local records only."""

import copy
import hashlib
import json
import subprocess
import sys

import pytest

from bt_api_py import trading_calendar_full_diff as full
from bt_api_py import trading_calendar_review_bundle as review
from bt_api_py.trading_calendar_preview_reports import TRUSTED_LOCAL_POSIX
from bt_api_py.trading_calendar_store import _canonical
from tests.unit.test_trading_calendar_review_bundle import (
    CID,
    build,
    published_candidate,
    sample,
    show,
)


def compare(root, candidate_id=CID, **kwargs):
    return review.compare_full(
        root=str(root), candidate_id=candidate_id, filesystem=TRUSTED_LOCAL_POSIX, **kwargs
    )


def second(root, value):
    candidate_id = "b" * 32
    assert build(root, value, candidate_id)["status"] == "COMMITTED"
    return candidate_id


def test_full_diff_binds_current_and_candidate_and_exposes_complete_source_change(tmp_path):
    saved, store = published_candidate(tmp_path, sample())
    value = sample()
    for shard in value["shards"]:
        shard["layers"]["sources"][0]["description"] = "new fictional description"
    candidate_id = second(tmp_path, value)
    result = compare(tmp_path, candidate_id)
    assert result["status"] == "CHANGED", result
    diff = result["difference"]
    assert diff["changes"]["sources"] == [
        {
            "key": ["synthetic"],
            "operation": "modified",
            "before": {"id": "synthetic", "description": "fictional"},
            "after": {"id": "synthetic", "description": "new fictional description"},
        }
    ]
    assert all(
        not diff["changes"][layer]
        for layer in ("trading_days", "trading_hours", "exceptions", "coverage")
    )
    assert result["difference_sha256"] == hashlib.sha256(_canonical(diff)).hexdigest()
    assert diff["before"]["publication_sha256"] == store.read_current().publication_sha256
    assert (
        diff["after"]["snapshot_sha256"]
        != saved["bundle"]["report"]["composite"]["snapshot_sha256"]
    )
    assert result["source_text"] == "SOURCE_TEXT_NOT_STORED"
    assert result["publication"] == "NOT_AUTHORIZED"


def test_partial_composite_and_ordering(tmp_path):
    published_candidate(tmp_path, sample())
    value = sample()
    value["shards"].reverse()
    for shard in value["shards"]:
        shard["layers"]["trading_days"].reverse()
        shard["layers"]["trading_hours"].reverse()
    value["shards"][0]["unresolved"].append("review required")
    candidate_id = second(tmp_path, value)
    result = compare(tmp_path, candidate_id)
    assert result["status"] == "SAME", result
    assert result["candidate_review_status"] == "PARTIAL"


@pytest.mark.parametrize(
    "layer", ["sources", "trading_days", "trading_hours", "exceptions", "coverage"]
)
def test_all_changed_rows_and_stable_keys(layer):
    old = {key: [] for key in full._LAYERS}
    new = copy.deepcopy(old)
    rows = {
        "sources": {"id": "s", "description": "old"},
        "trading_days": {
            "venue": "SHFE",
            "day": "2026-01-01",
            "status": "closed",
            "source_ids": ["s"],
        },
        "trading_hours": {
            "venue": "SHFE",
            "kind": "future",
            "product": "p",
            "contracts": ["c"],
            "valid_from": "2026-01-01",
            "valid_to": "2026-01-02",
            "source_ids": ["s"],
        },
        "exceptions": {
            "venue": "SHFE",
            "kind": "future",
            "product": "p",
            "contract": None,
            "trading_day": "2026-01-01",
            "action": "close_day",
            "window_id": None,
            "source_ids": ["s"],
        },
        "coverage": {
            "venue": "SHFE",
            "coverage_start": "2026-01-01",
            "coverage_end": "2026-01-02",
            "semantics": "DECLARED_INCLUSIVE",
        },
    }
    old[layer] = [rows[layer]]
    new[layer] = [copy.deepcopy(rows[layer])]
    new[layer][0]["marker"] = "changed"
    change = full._changes(old, new)[layer][0]
    assert (
        change["operation"] == "modified"
        and change["before"] == old[layer][0]
        and change["after"] == new[layer][0]
    )
    assert full._changes(old, {**new, layer: []})[layer][0]["operation"] == "removed"
    assert full._changes({**old, layer: []}, new)[layer][0]["operation"] == "added"


def test_first_unknown_legacy_orphan_and_pending(tmp_path):
    assert build(tmp_path, sample())["status"] == "COMMITTED"
    result = compare(tmp_path)
    assert (
        result["status"] == "UNKNOWN"
        and result["first_publication"] == "FIRST_PUBLICATION_NOT_PROVEN"
    )
    # Use a separate candidate fixture to publish.
    from tests.calendar_test_store import FixtureCalendarStore

    store = FixtureCalendarStore(tmp_path)
    store.publish(
        show(tmp_path)["bundle"]["report"]["composite"]["snapshot"],
        publication_id="old",
        version_id="old",
    )
    assert compare(tmp_path)["status"] == "UNKNOWN"


def test_orphan_and_output_budget_fail_closed(tmp_path, monkeypatch):
    published_candidate(tmp_path, sample())
    (tmp_path / "versions" / "orphan.json").write_text("{}")
    assert compare(tmp_path)["reason"] == "CURRENT_UNAVAILABLE_OR_UNCERTAIN"
    (tmp_path / "versions" / "orphan.json").unlink()
    monkeypatch.setattr(full, "_OUTPUT_LIMIT", 100)
    assert compare(tmp_path)["reason"] == "OUTPUT_LIMIT"


def test_second_current_or_candidate_change_is_unknown(tmp_path, monkeypatch):
    published_candidate(tmp_path, sample())
    original = full._current
    calls = 0

    def changed(root):
        nonlocal calls
        calls += 1
        if calls == 2:
            raise ValueError("current lost")
        return original(root)

    monkeypatch.setattr(full, "_current", changed)
    assert compare(tmp_path)["reason"] == "CURRENT_OR_CANDIDATE_CHANGED"


def test_sensitive_description_rejected_by_cli_without_leak(tmp_path):
    value = sample()
    for shard in value["shards"]:
        shard["layers"]["sources"][0]["description"] = "https://example.test/page?token=secret"
    published_candidate(tmp_path, value)
    candidate_id = second(tmp_path, sample())
    before = (tmp_path / "CURRENT.json").read_bytes()
    result = subprocess.run(
        [
            sys.executable,
            "-m",
            "bt_api_py.trading_calendar_review_bundle",
            "compare-full",
            "--calendar-root",
            str(tmp_path),
            "--candidate-id",
            candidate_id,
            "--trusted-local-posix",
            "--show-details",
        ],
        capture_output=True,
        text=True,
        check=False,
    )
    assert json.loads(result.stdout)["reason"] == "UNSAFE_TO_DISPLAY"
    assert "secret" not in result.stdout and str(tmp_path) not in result.stdout
    assert (tmp_path / "CURRENT.json").read_bytes() == before


def test_six_venues_two_year_composite(tmp_path):
    published_candidate(tmp_path, sample(731))
    result = compare(tmp_path)
    assert result["status"] == "SAME", result


def test_real_composite_changes_references_hours_exception_and_coverage(tmp_path):
    value = sample()
    for shard in value["shards"]:
        shard["layers"]["sources"].append({"id": "supplement", "description": "second reference"})
    value["source_texts"]["supplement"] = "second fictional primary text"
    shfe = value["shards"][0]["layers"]
    shfe["trading_days"][0]["status"] = "open"
    for hour in shfe["trading_hours"]:
        hour["night_mappings"] = [
            {"trading_day": "2026-01-01", "start_date": None, "source_ids": ["synthetic"]}
        ]
    published_candidate(tmp_path, value)
    changed = copy.deepcopy(value)
    shfe = changed["shards"][0]["layers"]
    shfe["trading_days"][0]["source_ids"].append("supplement")
    shfe["trading_hours"][0]["day_windows"][0]["end"] = "09:45"
    shfe["exceptions"].append(
        {
            "venue": "SHFE",
            "kind": "future",
            "product": "pfuture",
            "contract": "shfefuture2701",
            "trading_day": "2026-01-01",
            "action": "cancel_window",
            "window_id": "day",
            "source_ids": ["synthetic"],
        }
    )
    changed["shards"][0]["coverage_end"] = "2026-01-01"
    changed["shards"][0]["requirements"] = [
        {**r, "end_day": "2026-01-01"} for r in changed["shards"][0]["requirements"]
    ]
    result = compare(tmp_path, second(tmp_path, changed))
    assert result["status"] == "CHANGED", result
    diff = result["difference"]["changes"]
    assert diff["trading_days"][0]["after"]["source_ids"] == ["supplement", "synthetic"]
    assert diff["trading_hours"][0]["after"]["day_windows"][0]["end"] == "09:45"
    assert diff["exceptions"][0]["operation"] == "added"
    assert diff["coverage"][0]["before"]["coverage_end"] == "2026-01-02"
    assert diff["coverage"][0]["after"]["coverage_end"] == "2026-01-01"


def test_candidate_change_at_recheck_and_expected_identity(tmp_path, monkeypatch):
    saved, store = published_candidate(tmp_path, sample())
    assert compare(tmp_path, candidate_payload_sha256="0" * 64)["status"] == "UNKNOWN"
    assert (
        compare(tmp_path, current_publication_id="incorrect", current_publication_sha256="0" * 64)[
            "reason"
        ]
        == "CURRENT_IDENTITY_MISMATCH"
    )
    original = review.show
    calls = 0

    def changing(**kwargs):
        nonlocal calls
        calls += 1
        return original(**kwargs) if calls == 1 else {"status": "UNKNOWN"}

    monkeypatch.setattr(review, "show", changing)
    assert compare(tmp_path)["reason"] == "CURRENT_OR_CANDIDATE_CHANGED"


def test_unknown_duplicate_and_unrecognized_fields_are_rejected():
    value = sample()
    from bt_api_py.trading_calendar_sharded_validation import _VENUES

    content = {
        "sources": [{"id": "synthetic", "description": "fictional"}],
        "trading_days": [],
        "trading_hours": [],
        "exceptions": [],
    }
    for shard in value["shards"]:
        for layer in ("trading_days", "trading_hours", "exceptions"):
            content[layer].extend(copy.deepcopy(shard["layers"][layer]))
    manifest = {"coverage": [{"venue": venue} for venue in sorted(_VENUES)]}
    full._normalized(content, manifest)
    for problem in ("duplicate", "unknown", "missing_reference", "conflict"):
        broken = copy.deepcopy(content)
        if problem == "duplicate":
            broken["trading_days"].append(copy.deepcopy(broken["trading_days"][0]))
        elif problem == "unknown":
            broken["trading_days"][0]["extra"] = "cannot ignore"
        elif problem == "missing_reference":
            broken["trading_days"][0]["source_ids"] = ["unknown"]
        else:
            row = copy.deepcopy(broken["trading_hours"][0])
            row["product"] = "different"
            broken["trading_hours"].append(row)
        with pytest.raises(ValueError):
            full._normalized(broken, manifest)


def test_calendar_read_budget_and_candidate_revalidation(tmp_path, monkeypatch):
    published_candidate(tmp_path, sample())
    monkeypatch.setattr(full, "_READ_BUDGET", full.CalendarReadBudget(128, 256, 1))
    assert compare(tmp_path)["reason"] == "CURRENT_UNAVAILABLE_OR_UNCERTAIN"


def test_many_products_with_two_year_coverage(tmp_path):
    value = sample(731)
    for shard in value["shards"]:
        template = shard["layers"]["trading_hours"][0]
        requirement = shard["requirements"][0]
        for i in range(19):
            row = copy.deepcopy(template)
            row["product"] = f"p{i}"
            row["contracts"] = [f"{shard['venue'].lower()}c{i}"]
            shard["layers"]["trading_hours"].append(row)
            shard["requirements"].append(
                {**requirement, "product": row["product"], "contract": row["contracts"][0]}
            )
    published_candidate(tmp_path, value)
    assert compare(tmp_path)["status"] == "SAME"


def test_current_switch_between_reads_is_unknown(tmp_path, monkeypatch):
    _, store = published_candidate(tmp_path, sample())
    candidate_id = second(tmp_path, sample(3))
    composite = show(tmp_path, candidate_id)["bundle"]["report"]["composite"]
    original = full._current
    count = 0

    def switch(root):
        nonlocal count
        count += 1
        if count == 2:
            result = store.publish(
                composite["snapshot"],
                publication_id="next",
                version_id="next",
                coverage_manifest_bytes=_canonical(composite["manifest"]),
                coverage_manifest_sha256=composite["manifest_sha256"],
                binding_bytes=_canonical(composite["binding"]),
                binding_sha256=composite["binding_sha256"],
            )
            assert result.visibility == "COMMITTED"
        return original(root)

    monkeypatch.setattr(full, "_current", switch)
    assert compare(tmp_path, candidate_id)["reason"] == "CURRENT_OR_CANDIDATE_CHANGED"


def test_cli_does_not_output_candidate_metadata_or_source_text(tmp_path):
    published_candidate(tmp_path, sample())
    value = sample()
    value["metadata"] = {"note": "password client-secret"}
    value["source_texts"]["synthetic"] = "password source-secret"
    candidate_id = second(tmp_path, value)
    result = subprocess.run(
        [
            sys.executable,
            "-m",
            "bt_api_py.trading_calendar_review_bundle",
            "compare-full",
            "--calendar-root",
            str(tmp_path),
            "--candidate-id",
            candidate_id,
            "--trusted-local-posix",
        ],
        capture_output=True,
        text=True,
        check=False,
    )
    assert json.loads(result.stdout)["status"] == "SAME"
    assert "client-secret" not in result.stdout and "source-secret" not in result.stdout


def test_many_products_with_open_day_mappings(tmp_path):
    from datetime import date

    value = sample(731)
    for shard in value["shards"]:
        rows = shard["layers"]["trading_days"]
        for row in rows:
            if date.fromisoformat(row["day"]).weekday() < 5:
                row["status"] = "open"
        mappings = [
            {"trading_day": row["day"], "start_date": None, "source_ids": ["synthetic"]}
            for row in rows
            if row["status"] == "open"
        ]
        template = shard["layers"]["trading_hours"][0]
        requirement = shard["requirements"][0]
        for row in shard["layers"]["trading_hours"]:
            row["night_mappings"] = copy.deepcopy(mappings)
        for i in range(19):
            row = copy.deepcopy(template)
            row["product"] = f"p{i}"
            row["contracts"] = [f"{shard['venue'].lower()}c{i}"]
            shard["layers"]["trading_hours"].append(row)
            shard["requirements"].append(
                {**requirement, "product": row["product"], "contract": row["contracts"][0]}
            )
    assert build(tmp_path, value)["status"] == "COMMITTED"
    observed = show(tmp_path)
    report = observed["bundle"]["report"]
    assert report["composite"] is not None, (
        report["reason"],
        report["checker_status"],
        report["checker_reason"],
    )
    from tests.calendar_test_store import FixtureCalendarStore

    composite = report["composite"]
    store = FixtureCalendarStore(tmp_path)
    result = store.publish(
        composite["snapshot"],
        publication_id="large",
        version_id="large",
        coverage_manifest_bytes=_canonical(composite["manifest"]),
        coverage_manifest_sha256=composite["manifest_sha256"],
        binding_bytes=_canonical(composite["binding"]),
        binding_sha256=composite["binding_sha256"],
    )
    assert result.visibility == "COMMITTED"
    assert compare(tmp_path)["status"] == "SAME"


def test_large_scope_fallback_rejects_global_conflicts_and_child_mutation(monkeypatch):
    from datetime import date

    from bt_api_ctp import calendar_scope_batches
    from bt_api_ctp.calendar_auto_candidate import build_auto_candidate
    from bt_api_ctp.calendar_candidate_coverage import CoverageRequirement

    from bt_api_py.trading_calendar_sharded_validation import _large_scope_batches

    shard = sample()["shards"][0]
    captured = build_auto_candidate(
        venue="SHFE",
        coverage_start=date(2026, 1, 1),
        coverage_end=date(2026, 1, 2),
        layers=shard["layers"],
    )
    requirements = tuple(
        CoverageRequirement(
            "SHFE",
            r["kind"],
            r["product"],
            r["contract"],
            date.fromisoformat(r["start_day"]),
            date.fromisoformat(r["end_day"]),
        )
        for r in shard["requirements"]
    )
    assert _large_scope_batches(captured, requirements).status == "SCOPES_CHECKED_UNVERIFIED"
    broken = copy.deepcopy(captured)
    broken.candidate.content["trading_hours"].append(
        copy.deepcopy(broken.candidate.content["trading_hours"][0])
    )
    with pytest.raises(ValueError):
        _large_scope_batches(broken, requirements)
    original = calendar_scope_batches.check_scope_batches

    def mutating(candidate, **kwargs):
        result = original(candidate, **kwargs)
        candidate.candidate.content["sources"][0]["description"] = "changed"
        return result

    monkeypatch.setattr(calendar_scope_batches, "check_scope_batches", mutating)
    with pytest.raises(ValueError):
        _large_scope_batches(captured, requirements)


def _cli_compare(root, candidate_id):
    return subprocess.run(
        [
            sys.executable,
            "-m",
            "bt_api_py.trading_calendar_review_bundle",
            "compare-full",
            "--calendar-root",
            str(root),
            "--candidate-id",
            candidate_id,
            "--trusted-local-posix",
            "--show-details",
        ],
        capture_output=True,
        text=True,
        check=False,
    )


@pytest.mark.parametrize(
    "venue,kind,url",
    [
        ("shfe", "hours", "https://www.shfe.com.cn/services/calenderandholidays/tradinghours/"),
        ("shfe", "holiday.2026", "https://www.shfe.com.cn/services/calenderandholidays/holiday/"),
        ("ine", "hours", "https://www.ine.cn/services/calenderandholidays/tradinghours/"),
        ("ine", "holiday.2026", "https://www.ine.cn/services/calenderandholidays/holiday/"),
        ("gfex", "hours", "https://www.gfex.com.cn/gfex/jyrl/list.shtml"),
        ("cffex", "hours", "https://www.cffex.com.cn/cn/jystz/20251217/47006.html"),
        ("cffex", "hours", "https://www.e-cffex.com.cn/u/cms/www/202007/231658479i3m.pdf"),
        ("cffex", "hours", "https://e-cffex.com.cn/u/cms/www/202007/08152235b2ys.pdf"),
        ("czce", "hours", "https://www.czce.com.cn/cn/uploadfile/2023/04/27/sample.pdf"),
        ("dce", "hours", "https://www.dce.com.cn/dce/content/2025/sample.html"),
        ("shfe", "hours", "https://www.shfe.cn/publicnotice/notice/202604/t20260413_831097.html"),
        (
            "dce",
            "hours",
            "https://www.dce.com.cn/dalianshangpin/resource/cms/2017/04/"
            "%E6%9C%9F%E8%B4%A7%E5%85%AC%E5%8F%B8%E8%B1%86%E7%B2%95%E6%9C%9F"
            "%E6%9D%83%E4%B8%9A%E5%8A%A1%E6%8C%87%E5%8D%97%281%29.pdf",
        ),
    ],
)
def test_fixed_public_source_descriptions_are_full_cli_changes(tmp_path, venue, kind, url):
    text = "fictional public page content not for CLI output"
    digest = hashlib.sha256(text.encode()).hexdigest()
    source_id = f"{venue}.{kind}.{digest}"
    baseline = sample()
    baseline["source_texts"] = {source_id: text}
    for shard in baseline["shards"]:
        layers = shard["layers"]
        layers["sources"] = [{"id": source_id, "description": "previous public reference"}]
        for layer in ("trading_days", "trading_hours", "exceptions"):
            for row in layers[layer]:
                row["source_ids"] = [source_id]
    published_candidate(tmp_path, baseline)
    candidate = copy.deepcopy(baseline)
    description = f"{url} sha256:{digest}"
    for shard in candidate["shards"]:
        shard["layers"]["sources"][0]["description"] = description
    candidate_id = second(tmp_path, candidate)
    current = (tmp_path / "CURRENT.json").read_bytes()
    result = _cli_compare(tmp_path, candidate_id)
    output = json.loads(result.stdout)
    assert output["status"] == "CHANGED", output
    change = output["difference"]["changes"]["sources"][0]
    assert change["before"] == {"id": source_id, "description": "previous public reference"}
    assert change["after"] == {"id": source_id, "description": description}
    assert (
        output["difference_sha256"] == hashlib.sha256(_canonical(output["difference"])).hexdigest()
    )
    assert text not in result.stdout and str(tmp_path) not in result.stdout
    assert (tmp_path / "CURRENT.json").read_bytes() == current


@pytest.mark.parametrize(
    "description",
    [
        "https://user:pass@www.shfe.com.cn/services/calenderandholidays/holiday/ sha256:"
        + "a" * 64,
        "https://www.shfe.com.cn/services/calenderandholidays/holiday/?x=1 sha256:" + "a" * 64,
        "https://www.shfe.com.cn/services/calenderandholidays/holiday/#fragment sha256:" + "a" * 64,
        "https://www.shfe.com.cn/services/calenderandholidays/../holiday/ sha256:" + "a" * 64,
        "https://www.shfe.com.cn/services/calenderandholidays/holiday/ sha256:"
        + "a" * 64
        + " secret=hidden",
        "https://www.shfe.com.cn/services/calenderandholidays/holiday/ sha256:" + "a" * 63,
        "https://www.shfe.com.cn/services/calenderandholidays/holiday/ sha256:" + "a" * 64 + "\n",
        "https://evil.test/services/calenderandholidays/holiday/ sha256:" + "a" * 64,
        "https://www.gfex.com.cn.evil.test/gfex/jyrl/list.shtml sha256:" + "a" * 64,
        "https://www.gfex.com.cn/%2e%2e/gfex/jyrl/list.shtml sha256:" + "a" * 64,
        "https://www.cffex.com.cn/cn/%2fprivate sha256:" + "a" * 64,
        "https://www.dce.com.cn/a%5cb sha256:" + "a" * 64,
        "https://www.dce.com.cn/a/%2e%2e/b sha256:" + "a" * 64,
        "https://www.dce.com.cn/a/%252e%252e/b sha256:" + "a" * 64,
        "https://www.dce.com.cn/a%252fb sha256:" + "a" * 64,
        "https://www.dce.com.cn/%C0%AFbad sha256:" + "a" * 64,
        "https://www.dce.com.cn/%E6%9C sha256:" + "a" * 64,
        "https://www.dce.com.cn/a%0ab sha256:" + "a" * 64,
        "https://www.dce.com.cn/a%C2%85b sha256:" + "a" * 64,
        "https://www.dce.com.cn/%EF%BC%8Fetc sha256:" + "a" * 64,
        "https://www.dce.com.cn/a%3fb sha256:" + "a" * 64,
        "https://www.dce.com.cn/a%23b sha256:" + "a" * 64,
        "https://www.dce.com.cn/a%40b sha256:" + "a" * 64,
        "https://www.dce.com.cn/a%ZZb sha256:" + "a" * 64,
        "https://www.dce.com.cn/to%6ben sha256:" + "a" * 64,
        "https://[invalid/gfex sha256:" + "a" * 64,
        "/Users/example/private/file",
    ],
)
def test_nonpublic_or_malformed_description_is_not_printed(tmp_path, description):
    published_candidate(tmp_path, sample())
    value = sample()
    for shard in value["shards"]:
        shard["layers"]["sources"][0]["description"] = description
    candidate_id = second(tmp_path, value)
    result = _cli_compare(tmp_path, candidate_id)
    assert json.loads(result.stdout)["reason"] == "UNSAFE_TO_DISPLAY"
    assert description not in result.stdout
    assert str(tmp_path) not in result.stdout


def test_public_url_exception_is_confined_to_source_change_description():
    description = "https://www.ine.cn/services/calenderandholidays/holiday/ sha256:" + "b" * 64
    allowed = {"sources": [{"before": {"id": "source", "description": description}}]}
    assert full._safe_output(allowed, "/private/example", source_changes=True)
    assert not full._safe_output(allowed, "/private/example")
    assert not full._safe_output(
        {"trading_days": [{"before": {"description": description}}]},
        "/private/example",
        source_changes=True,
    )
    assert not full._safe_output(
        {"sources": [{"before": {"id": description, "description": "public"}}]},
        "/private/example",
        source_changes=True,
    )
    assert not full._safe_output(
        {"sources": [{"key": [description]}]}, "/private/example", source_changes=True
    )


def test_closed_day_cross_product_rejected_before_provider_construction(monkeypatch):
    from bt_api_ctp import calendar_snapshot

    shard = sample(731)["shards"][0]
    content = copy.deepcopy(shard["layers"])
    content["trading_hours"] = [content["trading_hours"][0]]
    content["trading_hours"][0]["contracts"] = [f"c{i}" for i in range(1000)]
    assert all(row["status"] == "closed" for row in content["trading_days"])
    constructed = []

    def forbidden(*args, **kwargs):
        constructed.append(True)
        raise AssertionError("provider must not be constructed")

    monkeypatch.setattr(calendar_snapshot, "CalendarSnapshot", forbidden)
    with pytest.raises(ValueError, match="snapshot work budget"):
        full._normalized(content, {"coverage": [{"venue": "SHFE"}]})
    assert not constructed


def test_cli_summary_hides_source_ids_details_preserve_digest(tmp_path):
    published_candidate(tmp_path, sample())
    value = sample()
    source_id = "token_secret_sensitive"
    value["source_texts"] = {source_id: "fictional source text"}
    for shard in value["shards"]:
        layers = shard["layers"]
        layers["sources"] = [{"id": source_id, "description": "fictional new source"}]
        for layer in ("trading_days", "trading_hours", "exceptions"):
            for row in layers[layer]:
                row["source_ids"] = [source_id]
    candidate_id = second(tmp_path, value)
    command = [
        sys.executable,
        "-m",
        "bt_api_py.trading_calendar_review_bundle",
        "compare-full",
        "--calendar-root",
        str(tmp_path),
        "--candidate-id",
        candidate_id,
        "--trusted-local-posix",
    ]
    summary = json.loads(
        subprocess.run(command, capture_output=True, text=True, check=False).stdout
    )
    assert summary["status"] == "CHANGED" and summary["details"] == "NOT_DISPLAYED"
    assert source_id not in json.dumps(summary) and "difference" not in summary
    assert summary["difference_sha256"] == compare(tmp_path, candidate_id)["difference_sha256"]
    explicit = subprocess.run(
        [*command, "--show-details"], capture_output=True, text=True, check=False
    )
    assert json.loads(explicit.stdout)["reason"] == "UNSAFE_TO_DISPLAY"
    assert source_id not in explicit.stdout


def test_cli_summary_and_public_details_have_identical_digest(tmp_path):
    published_candidate(tmp_path, sample())
    value = sample()
    for shard in value["shards"]:
        shard["layers"]["sources"][0]["description"] = "updated fictional source"
    candidate_id = second(tmp_path, value)
    command = [
        sys.executable,
        "-m",
        "bt_api_py.trading_calendar_review_bundle",
        "compare-full",
        "--calendar-root",
        str(tmp_path),
        "--candidate-id",
        candidate_id,
        "--trusted-local-posix",
    ]
    summary = json.loads(
        subprocess.run(command, capture_output=True, text=True, check=False).stdout
    )
    detailed = json.loads(
        subprocess.run(
            [*command, "--show-details"], capture_output=True, text=True, check=False
        ).stdout
    )
    assert summary["details"] == "NOT_DISPLAYED" and detailed["details"] == "DISPLAYED_COMPLETE"
    assert summary["difference_sha256"] == detailed["difference_sha256"]
    assert summary["store_difference_size_status"] == "COMPATIBLE"
    assert summary["store_difference_size_check"] == "SIZE_ONLY_NOT_PUBLICATION_VALIDATION"
    assert (
        detailed["difference_sha256"]
        == hashlib.sha256(_canonical(detailed["difference"])).hexdigest()
    )
    assert (
        detailed["difference"]["changes"]["sources"][0]["after"]["description"]
        == "updated fictional source"
    )


def _republish_with_difference(store, content, composite, publication_id, difference):
    from bt_api_py.trading_calendar_store import (
        CurrentPrecondition,
        PolicyPublication,
        TradingCalendarStore,
        calendar_snapshot_digest,
    )

    current = store.read_current_with_consistent_receipts().snapshot
    policy = store.read_review_policy()
    artifacts = {
        "coverage_manifest_bytes": _canonical(composite["manifest"]),
        "coverage_manifest_sha256": composite["manifest_sha256"],
        "binding_bytes": _canonical(composite["binding"]),
        "binding_sha256": composite["binding_sha256"],
    }
    claim = PolicyPublication(
        policy.directory_id,
        policy.revision,
        policy.mode,
        calendar_snapshot_digest(content, **artifacts),
        difference,
        hashlib.sha256(_canonical(difference)).hexdigest(),
    )
    return TradingCalendarStore.publish(
        store,
        content,
        publication_id=publication_id,
        version_id=current.version_id,
        republish=True,
        expected_current=CurrentPrecondition.matching(
            current.publication_id, current.publication_sha256
        ),
        policy_publication=claim,
        **artifacts,
    )


def test_large_current_and_historical_events_and_total_inventory_budget(tmp_path, monkeypatch):
    saved, store = published_candidate(tmp_path, sample())
    composite = saved["bundle"]["report"]["composite"]
    content = composite["snapshot"]
    assert (
        _republish_with_difference(
            store, content, composite, "large-current", {"evidence": "x" * 70000}
        ).visibility
        == "COMMITTED"
    )
    assert (tmp_path / "publications" / "large-current.json").stat().st_size > 65536
    assert compare(tmp_path)["status"] == "SAME"
    assert (
        _republish_with_difference(
            store, content, composite, "later-current", {"evidence": "y" * 70000}
        ).visibility
        == "COMMITTED"
    )
    assert compare(tmp_path)["status"] == "SAME"
    assert (
        _republish_with_difference(
            store, content, composite, "near-limit", {"evidence": "z" * 1_048_400}
        ).visibility
        == "COMMITTED"
    )
    assert (tmp_path / "publications" / "near-limit.json").stat().st_size > 1024 * 1024
    assert compare(tmp_path)["status"] == "SAME"
    monkeypatch.setattr(full, "_INVENTORY_TOTAL_BYTES", 120000)
    assert compare(tmp_path)["reason"] == "CURRENT_UNAVAILABLE_OR_UNCERTAIN"


def test_full_difference_exceeds_store_limit_without_truncation(tmp_path):
    baseline = sample(731)
    old_id, new_id = "a" * 120, "b" * 120

    def rename_source(value, source_id):
        value["source_texts"] = {source_id: "fictional public source"}
        for shard in value["shards"]:
            layers = shard["layers"]
            layers["sources"][0]["id"] = source_id
            for layer in ("trading_days", "trading_hours", "exceptions"):
                for row in layers[layer]:
                    row["source_ids"] = [source_id]

    rename_source(baseline, old_id)
    published_candidate(tmp_path, baseline)
    candidate = copy.deepcopy(baseline)
    rename_source(candidate, new_id)
    result = compare(tmp_path, second(tmp_path, candidate))
    assert result["status"] == "CHANGED", result
    assert result["difference_size_bytes"] == len(_canonical(result["difference"]))
    assert result["difference_size_bytes"] > 1024 * 1024
    assert result["store_difference_size_status"] == "EXCEEDS"
    assert len(result["difference"]["changes"]["trading_days"]) == 6 * 731
    assert (
        result["difference_sha256"] == hashlib.sha256(_canonical(result["difference"])).hexdigest()
    )
    assert result["publication"] == "NOT_AUTHORIZED"


def test_review_bundle_budget_precedes_any_candidate_snapshot(tmp_path, monkeypatch):
    from bt_api_ctp import calendar_snapshot

    published_candidate(tmp_path, sample())
    value = sample(731)
    value["shards"][0]["layers"]["trading_hours"][0]["contracts"] = [f"c{i}" for i in range(1000)]
    constructed = []

    def forbidden(*args, **kwargs):
        constructed.append(True)
        raise AssertionError("candidate provider must not be constructed")

    monkeypatch.setattr(calendar_snapshot, "CalendarSnapshot", forbidden)
    candidate_id = second(tmp_path, value)
    shown = show(tmp_path, candidate_id)
    assert shown["bundle"]["report"]["reason"] == "SNAPSHOT_BUDGET_UNAVAILABLE"
    assert shown["bundle"]["report"]["composite"] is None
    assert compare(tmp_path, candidate_id)["reason"] == "COMPOSITE_UNAVAILABLE"
    assert not constructed


def test_direct_sharded_validation_rejects_snapshot_work_before_any_ctp_snapshot(monkeypatch):
    from dataclasses import replace

    from bt_api_ctp import calendar_candidate_coverage, calendar_scope_batches, calendar_snapshot
    from bt_api_ctp.calendar_supplement import parse_supplement

    from tests.unit.test_trading_calendar_sharded_validation import execute, make_shards

    shards = list(make_shards(days=731))
    content = copy.deepcopy(shards[0].candidate.content)
    content["trading_hours"][0]["contracts"] = [f"c{i}" for i in range(600)]
    shards[0] = replace(shards[0], candidate=parse_supplement(json.dumps(content).encode()))
    calls = []

    def forbidden(*args, **kwargs):
        calls.append(True)
        raise AssertionError("work preflight must precede snapshot construction")

    monkeypatch.setattr(calendar_snapshot, "CalendarSnapshot", forbidden)
    monkeypatch.setattr(calendar_candidate_coverage, "CalendarSnapshot", forbidden)
    monkeypatch.setattr(calendar_scope_batches, "check_scope_batches", forbidden)
    result = execute(tuple(shards))
    assert (result.status, result.reason) == ("INVALID_INPUT", "SNAPSHOT_BUDGET_UNAVAILABLE")
    assert result.composite is None and not calls


def test_candidate_store_snapshot_digest_binds_coverage_not_only_content(tmp_path):
    from bt_api_py.trading_calendar_store import calendar_snapshot_digest

    _, store = published_candidate(tmp_path, sample())
    current = store.read_current_with_consistent_receipts().snapshot
    changed = sample()
    changed["shards"][0]["coverage_end"] = "2026-01-01"
    changed["shards"][0]["requirements"] = [
        {**item, "end_day": "2026-01-01"} for item in changed["shards"][0]["requirements"]
    ]
    candidate_id = second(tmp_path, changed)
    composite = show(tmp_path, candidate_id)["bundle"]["report"]["composite"]
    result = compare(tmp_path, candidate_id)
    assert result["status"] == "CHANGED", result
    before, after = result["difference"]["before"], result["difference"]["after"]
    assert before["snapshot_sha256"] == after["snapshot_sha256"] == current.content_sha256
    assert before["version_sha256"] == calendar_snapshot_digest(
        current.content,
        coverage_manifest_bytes=current.coverage_manifest_bytes,
        coverage_manifest_sha256=current.coverage_manifest_sha256,
        binding_bytes=current.binding_bytes,
        binding_sha256=current.binding_sha256,
    )
    assert after["store_snapshot_sha256"] == calendar_snapshot_digest(
        composite["snapshot"],
        coverage_manifest_bytes=_canonical(composite["manifest"]),
        coverage_manifest_sha256=composite["manifest_sha256"],
        binding_bytes=_canonical(composite["binding"]),
        binding_sha256=composite["binding_sha256"],
    )
    assert before["version_sha256"] != after["store_snapshot_sha256"]
    assert (
        result["difference_sha256"] == hashlib.sha256(_canonical(result["difference"])).hexdigest()
    )
    assert result["publication"] == "NOT_AUTHORIZED"
