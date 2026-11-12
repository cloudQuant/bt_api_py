"""Offline checks for conservative review-supplement assembly."""

from copy import deepcopy
from datetime import date

import pytest

from bt_api_py.trading_calendar_review_supplement import (
    merge_supplement_layers,
    validate_explicit_relations,
)
from tests.unit.test_trading_calendar_review_bundle import sample


def inputs():
    observed = sample()["shards"][0]["layers"]
    observed["trading_hours"][0]["windows_complete"] = False
    observed["trading_hours"][0]["exceptions_complete"] = False
    supplement = deepcopy(observed)
    supplement["sources"].append({"id": "manual", "description": "fictional supplement"})
    for row in supplement["trading_days"]:
        row["source_ids"] = ["manual"]
    row = supplement["trading_hours"][0]
    row["source_ids"].append("manual")
    row["windows_complete"] = True
    row["exceptions_complete"] = True
    return observed, supplement


def merge(observed, supplement):
    return merge_supplement_layers(
        observed,
        supplement,
        venue="SHFE",
        coverage_start=date(2026, 1, 1),
        coverage_end=date(2026, 1, 2),
    )


def test_merge_preserves_observations_and_input():
    observed, supplement = inputs()
    before = deepcopy(observed), deepcopy(supplement)
    merged = merge(observed, supplement)
    assert (observed, supplement) == before
    assert merged["trading_days"][0]["source_ids"] == ["manual", "synthetic"]
    assert merged["trading_hours"][0]["windows_complete"]
    assert validate_explicit_relations(merged)["unassessed_exception_count"] == 0


@pytest.mark.parametrize("conflict", ["source", "day", "day_window", "observed_source", "venue"])
def test_conflicting_observations_are_rejected(conflict):
    observed, supplement = inputs()
    if conflict == "source":
        supplement["sources"][0]["description"] = "different"
    elif conflict == "day":
        supplement["trading_days"][0]["status"] = "open"
    elif conflict == "day_window":
        supplement["trading_hours"][0]["day_windows"][0]["end"] = "11:00"
    elif conflict == "observed_source":
        supplement["trading_hours"][0]["source_ids"] = ["manual"]
    else:
        supplement["trading_days"][0]["venue"] = "INE"
    with pytest.raises(ValueError):
        merge(observed, supplement)


def test_missing_open_day_night_mapping_is_not_success():
    observed, supplement = inputs()
    observed["trading_days"][0]["status"] = "open"
    supplement["trading_days"][0]["status"] = "open"
    merged = merge(observed, supplement)
    with pytest.raises(ValueError, match="relationships incomplete"):
        validate_explicit_relations(merged)
    for row in merged["trading_hours"]:
        row["night_mappings"] = [
            {"trading_day": "2026-01-01", "start_date": None, "source_ids": ["manual"]}
        ]
    assert validate_explicit_relations(merged)["missing_mapping_count"] == 0


def test_exception_outside_matching_scope_is_not_success():
    observed, supplement = inputs()
    supplement["exceptions"].append(
        {
            "venue": "SHFE",
            "kind": "future",
            "product": "pfuture",
            "contract": "unknown2701",
            "trading_day": "2026-01-01",
            "action": "close_day",
            "window_id": None,
            "source_ids": ["manual"],
        }
    )
    observed["trading_days"][0]["status"] = "open"
    supplement["trading_days"][0]["status"] = "open"
    for row in supplement["trading_hours"]:
        row["night_mappings"] = [
            {"trading_day": "2026-01-01", "start_date": None, "source_ids": ["manual"]}
        ]
    merged = merge(observed, supplement)
    with pytest.raises(ValueError, match="relationships incomplete"):
        validate_explicit_relations(merged)


def test_cancelled_window_must_exist_in_every_matched_scope():
    observed, supplement = inputs()
    observed["trading_days"][0]["status"] = "open"
    supplement["trading_days"][0]["status"] = "open"
    for row in supplement["trading_hours"]:
        row["night_mappings"] = [
            {"trading_day": "2026-01-01", "start_date": None, "source_ids": ["manual"]}
        ]
    supplement["exceptions"].append(
        {
            "venue": "SHFE",
            "kind": "future",
            "product": "pfuture",
            "contract": "shfefuture2701",
            "trading_day": "2026-01-01",
            "action": "cancel_window",
            "window_id": "not-present",
            "source_ids": ["manual"],
        }
    )
    merged = merge(observed, supplement)
    with pytest.raises(ValueError, match="relationships incomplete"):
        validate_explicit_relations(merged)


def test_existing_night_mapping_cannot_be_replaced():
    observed, supplement = inputs()
    observed["trading_days"][0]["status"] = "open"
    supplement["trading_days"][0]["status"] = "open"
    row = observed["trading_hours"][0]
    row["night_mappings"] = [
        {"trading_day": "2026-01-01", "start_date": None, "source_ids": ["synthetic"]}
    ]
    row = supplement["trading_hours"][0]
    row["night_windows"] = [
        {
            "id": "night",
            "phase": "continuous",
            "start": "21:00",
            "end": "23:00",
            "end_day_offset": 0,
        }
    ]
    row["night_mappings"] = [
        {"trading_day": "2026-01-01", "start_date": "2025-12-31", "source_ids": ["manual"]}
    ]
    with pytest.raises(ValueError, match="night mapping conflict"):
        merge(observed, supplement)


def _support_doc(ownership=(), windows=()):
    import json

    return json.dumps(
        {
            "schema": "bt_api.calendar-review-support/1",
            "night_ownership": list(ownership),
            "night_windows": list(windows),
        }
    )


def _support_scope():
    return {
        "venue": "SHFE",
        "kind": "future",
        "product": "cu",
        "contracts": ["cu2701"],
        "valid_from": "2026-01-01",
        "valid_to": "2026-12-31",
    }


def _support_match(texts, field, expected):
    from bt_api_py.trading_calendar_review_supplement import _support_has

    return _support_has(texts, set(texts), field, expected, {})


@pytest.mark.parametrize(
    "variant", ["duplicate", "owner", "start_date", "overlapping_scope", "disjoint_validity"]
)
def test_support_same_natural_date_conflicts_never_match(variant):
    scope = _support_scope()
    original = {**scope, "local_date": "2026-02-14", "owner_day": "2026-02-17", "start_date": None}
    other = deepcopy(original)
    if variant == "owner":
        other["owner_day"] = "2026-02-18"
    elif variant == "start_date":
        other["start_date"] = "2026-02-14"
    elif variant == "overlapping_scope":
        other["contracts"] = ["cu2701", "cu2702"]
        other["owner_day"] = "2026-02-18"
    elif variant == "disjoint_validity":
        other["valid_from"] = "2027-01-01"
        other["valid_to"] = "2027-12-31"
        other["owner_day"] = "2027-01-05"
    assert not _support_match(
        {"support": _support_doc([original, other])}, "night_ownership", original
    )


@pytest.mark.parametrize("variant", ["contradictory", "unparseable"])
def test_cross_document_ownership_never_ignores_duplicates_or_conflicts(variant):
    scope = _support_scope()
    fact = {**scope, "local_date": "2026-02-14", "owner_day": "2026-02-17", "start_date": None}
    other = deepcopy(fact)
    if variant == "contradictory":
        other["owner_day"] = "2026-02-18"
    text = "unparseable" if variant == "unparseable" else _support_doc([other])
    assert not _support_match(
        {"first": _support_doc([fact]), "second": text}, "night_ownership", fact
    )


def test_same_fact_from_distinct_sources_is_merged_with_provenance():
    from bt_api_py.trading_calendar_review_supplement import _canonical_support, _support_index

    fact = {
        **_support_scope(),
        "local_date": "2026-02-14",
        "owner_day": "2026-02-17",
        "start_date": None,
    }
    texts = {"first": _support_doc([fact]), "second": _support_doc([fact])}
    index = _support_index(texts, set(texts), {})
    assert index is not None
    assert index["_provenance"]["night_ownership"][_canonical_support(fact)] == {"first", "second"}


def test_non_conflicting_multiple_support_documents_are_allowed():
    scope = _support_scope()
    first = {**scope, "local_date": "2026-02-14", "owner_day": "2026-02-17", "start_date": None}
    second = {**scope, "local_date": "2026-04-14", "owner_day": "2026-04-17", "start_date": None}
    texts = {"first": _support_doc([first]), "second": _support_doc([second])}
    assert _support_match(texts, "night_ownership", first)
    assert _support_match(texts, "night_ownership", second)


@pytest.mark.parametrize("cross_document", [False, True])
def test_overlapping_support_windows_are_conflicts(cross_document):
    scope = _support_scope()
    first = {**scope, "phase": "continuous", "start": "21:00", "end": "23:00", "end_day_offset": 0}
    other = {**scope, "phase": "auction", "start": "22:50", "end": "23:10", "end_day_offset": 0}
    texts = (
        {"first": _support_doc(windows=[first]), "second": _support_doc(windows=[other])}
        if cross_document
        else {"first": _support_doc(windows=[first, other])}
    )
    assert not _support_match(texts, "night_windows", first)


def test_disjoint_support_windows_and_validity_are_allowed():
    scope = _support_scope()
    first = {**scope, "phase": "continuous", "start": "21:00", "end": "23:00", "end_day_offset": 0}
    adjacent = {
        **scope,
        "phase": "continuous",
        "start": "23:00",
        "end": "23:30",
        "end_day_offset": 0,
    }
    other_period = {**first, "valid_from": "2027-01-01", "valid_to": "2027-12-31"}
    assert _support_match(
        {"one": _support_doc(windows=[first, adjacent, other_period])}, "night_windows", first
    )


def test_hash_named_additional_source_is_not_mistaken_for_bound_original():
    import hashlib

    from bt_api_py.trading_calendar_review_supplement import _support_has

    scope = _support_scope()
    fact = {**scope, "local_date": "2026-02-14", "owner_day": "2026-02-17", "start_date": None}
    other = {**fact, "owner_day": "2026-02-18"}
    second_text = _support_doc([other])
    hash_named = "ine.holiday.2026." + hashlib.sha256(second_text.encode()).hexdigest()
    texts = {"manual": _support_doc([fact]), hash_named: second_text}
    assert not _support_has(texts, set(texts), "night_ownership", fact, {})
    original = "shfe.hours." + "b" * 64
    texts = {"manual": _support_doc([fact]), original: "original HTML evidence"}
    assert _support_has(texts, set(texts), "night_ownership", fact, {}, {original})


def _exception_doc(rows):
    import json

    return json.dumps(
        {
            "schema": "bt_api.calendar-review-support/1",
            "night_ownership": [],
            "night_windows": [],
            "exceptions": rows,
        }
    )


def test_exception_evidence_merges_exact_cross_source_and_retains_provenance():
    from bt_api_py.trading_calendar_review_supplement import _canonical_support, _support_index

    fact = {
        "venue": "SHFE",
        "kind": "future",
        "product": "cu",
        "contract": "cu2701",
        "trading_day": "2026-01-05",
        "action": "cancel_window",
        "window_id": "day1",
    }
    texts = {"one": _exception_doc([fact]), "two": _exception_doc([fact])}
    index = _support_index(texts, set(texts), {})
    assert index is not None
    assert index["_provenance"]["exceptions"][_canonical_support(fact)] == {"one", "two"}
    assert _support_index({"one": _exception_doc([fact, fact])}, {"one"}, {}) is None


def test_exception_evidence_rejects_cross_source_wildcard_conflict():
    from bt_api_py.trading_calendar_review_supplement import _support_index

    fact = {
        "venue": "SHFE",
        "kind": "future",
        "product": "cu",
        "contract": "cu2701",
        "trading_day": "2026-01-05",
        "action": "cancel_window",
        "window_id": "day1",
    }
    conflicting = {**fact, "contract": None, "action": "close_day", "window_id": None}
    texts = {"one": _exception_doc([fact]), "two": _exception_doc([conflicting])}
    assert _support_index(texts, set(texts), {}) is None


def test_note_exception_match_work_budget_fails_closed():
    from bt_api_py.trading_calendar_review_supplement import _known_note

    source = "shfe.hours." + "a" * 64
    contracts = [f"cu{n}" for n in range(501)]
    row = {
        "venue": "SHFE",
        "kind": "future",
        "product": "cu",
        "contracts": contracts,
        "valid_from": "2026-01-01",
        "valid_to": "2026-12-31",
        "source_ids": [source],
        "day_windows": [{"id": "d", "start": "09:00", "end": "10:15", "end_day_offset": 0}],
    }
    exception = {
        "venue": "SHFE",
        "kind": "future",
        "product": "cu",
        "contract": None,
        "trading_day": "2026-01-05",
        "action": "cancel_window",
        "window_id": "d",
        "source_ids": [source],
    }
    result = _known_note(
        "注：合成甲于2026年1月5日09:00至10:15暂停交易。",
        {"scopes": [], "source_ids": [source]},
        shard={
            "layers": {"trading_days": [{"venue": "SHFE", "day": "2026-01-05", "status": "open"}]}
        },
        supplemental={"trading_hours": [row], "exceptions": [exception] * 400},
        hours_source=source,
        reviews=[
            {
                "venue": "SHFE",
                "kind": "future",
                "product": "cu",
                "contracts": contracts,
                "page_product_name": "合成甲",
            }
        ],
        texts={},
        support_cache={},
        considered_sources=set(),
        page_notes=[],
    )
    assert result is False


def test_template_contract_intersection_is_precomputed_and_bounded():
    from bt_api_py.trading_calendar_review_supplement import _support_matches_template

    class CountingContracts(list):
        iterations = 0

        def __iter__(self):
            self.iterations += 1
            return super().__iter__()

    row_contracts = CountingContracts(f"cu{n}" for n in range(500))
    row = {
        **_support_scope(),
        "contracts": row_contracts,
        "source_ids": ["source"],
        "night_windows": [],
        "night_mappings": [],
    }
    unrelated = {
        **_support_scope(),
        "contracts": ["other"],
        "phase": "continuous",
        "start": "21:00",
        "end": "23:00",
        "end_day_offset": 0,
    }
    support = {
        "_window_rows": [unrelated] * 10000,
        "_ownership_rows": [],
        "_provenance": {"night_windows": {}, "night_ownership": {}},
    }
    assert _support_matches_template(support, row, {})
    assert row_contracts.iterations <= 2
    oversized = {**unrelated, "contracts": [f"other{n}" for n in range(1000)]}
    support["_window_rows"] = [oversized] * 201
    assert not _support_matches_template(support, row, {})


def test_original_note_matching_is_budgeted_by_text_size():
    from bt_api_py.trading_calendar_review_supplement import (
        _SUPPORT_WORK,
        _original_note_supports_exception,
    )

    source = "shfe.hours." + "a" * 64
    cache = {}
    result = _original_note_supports_exception(
        {"source_ids": [source], "action": "cancel_window", "contract": "cu2701"},
        {"layers": {"trading_hours": []}},
        (source, ["x" * 8192] * 30, []),
        cache,
    )
    assert result is False
    assert 190000 <= cache[_SUPPORT_WORK] <= 200000
