"""Conservative four-layer supplement merge for calendar review assembly.

This internal helper preserves observed fields and checks declared intervals; it
neither resolves evidence-review items nor authenticates or publishes data.
Inputs must be privately captured, bounded plain objects and not concurrently
mutated. The caller must retain and independently revalidate their provenance.
"""

import json
import re
from collections import Counter
from copy import deepcopy

_LAYERS = ("sources", "trading_days", "trading_hours", "exceptions")
_HOUR_KEY = ("venue", "kind", "product", "valid_from", "valid_to")
_EXCEPTION_KEY = ("venue", "kind", "product", "contract", "trading_day", "action", "window_id")
_SUPPORT_WORK = object()
_SUPPORT_VALIDATIONS = object()
_EXCEPTION_SOURCES = object()


def _refs(left, right):
    return sorted(set(left) | set(right))


def _hour_key(row):
    return tuple(row[key] for key in _HOUR_KEY) + (tuple(sorted(row["contracts"])),)


def merge_supplement_layers(observed, supplement, *, venue, coverage_start, coverage_end):
    """Merge one validated supplement without replacing observed calendar facts.

    An existing time template can only be completed at the exact identity and
    period with the same day windows and all previously observed source refs.
    The result still requires explicit evidence-item decisions and source checks.
    Conflicts and resource/structural errors raise ValueError or a validator error.
    """
    from bt_api_ctp.calendar_auto_candidate import build_auto_candidate
    from bt_api_ctp.calendar_candidate_normalize import _check_and_order
    from bt_api_ctp.calendar_supplement import _validate

    from bt_api_py.trading_calendar_candidate_inbox import _validated_baseline
    from bt_api_py.trading_calendar_review_bundle import _bounded

    _bounded(observed)
    _bounded(supplement)
    original = build_auto_candidate(
        venue=venue, coverage_start=coverage_start, coverage_end=coverage_end, layers=observed
    )
    if original.status != "CANDIDATE" or original.candidate is None:
        raise ValueError("invalid observed shard")
    incoming = _check_and_order(_validate(deepcopy(supplement)).content)
    if any(row["venue"] != venue for layer in _LAYERS[1:] for row in incoming[layer]):
        raise ValueError("supplement venue mismatch")
    result = deepcopy(original.candidate.content)
    sources = {row["id"]: row for row in result["sources"]}
    for row in incoming["sources"]:
        existing = sources.get(row["id"])
        if existing is not None and existing != row:
            raise ValueError("source conflict")
        if existing is None:
            sources[row["id"]] = row
            result["sources"].append(row)
    days = {(row["venue"], row["day"]): row for row in result["trading_days"]}
    for row in incoming["trading_days"]:
        key = (row["venue"], row["day"])
        existing = days.get(key)
        if existing is None:
            result["trading_days"].append(row)
            days[key] = row
        elif existing["status"] != row["status"]:
            raise ValueError("day conflict")
        else:
            existing["source_ids"] = _refs(existing["source_ids"], row["source_ids"])
    hours = {_hour_key(row): row for row in result["trading_hours"]}
    for row in incoming["trading_hours"]:
        key = _hour_key(row)
        old = hours.get(key)
        if old is None:
            result["trading_hours"].append(row)
            hours[key] = row
            continue
        if (
            sorted(old["day_windows"], key=lambda item: item["id"])
            != sorted(row["day_windows"], key=lambda item: item["id"])
            or not set(old["source_ids"]) <= set(row["source_ids"])
            or (
                (old["night_windows"] or old["windows_complete"])
                and sorted(old["night_windows"], key=lambda item: item["id"])
                != sorted(row["night_windows"], key=lambda item: item["id"])
            )
            or (old["windows_complete"] and not row["windows_complete"])
            or (old["exceptions_complete"] and not row["exceptions_complete"])
        ):
            raise ValueError("observed template conflict")
        mappings = {item["trading_day"]: item for item in old["night_mappings"]}
        for item in row["night_mappings"]:
            previous = mappings.get(item["trading_day"])
            if previous is None:
                old["night_mappings"].append(item)
            elif previous["start_date"] != item["start_date"]:
                raise ValueError("night mapping conflict")
            else:
                previous["source_ids"] = _refs(previous["source_ids"], item["source_ids"])
        old["night_windows"] = row["night_windows"]
        old["source_ids"] = _refs(old["source_ids"], row["source_ids"])
        old["windows_complete"] = row["windows_complete"]
        old["exceptions_complete"] = row["exceptions_complete"]
    exceptions = {tuple(row[k] for k in _EXCEPTION_KEY): row for row in result["exceptions"]}
    for row in incoming["exceptions"]:
        key = tuple(row[k] for k in _EXCEPTION_KEY)
        old = exceptions.get(key)
        if old is None:
            result["exceptions"].append(row)
            exceptions[key] = row
        else:
            old["source_ids"] = _refs(old["source_ids"], row["source_ids"])
    # This enforces uniqueness, scope ownership, interval and exception rules.
    return _validated_baseline(result)


def validate_explicit_relations(content):
    """Reject missing or unassessed explicit relationships after budget checks.

    This is a structural check only; it does not establish venue coverage or
    authenticity. Callers must also run per-scope and composite validation.
    """
    from bt_api_py.trading_calendar_candidate_inbox import (
        _check_explicit_intervals,
        _validated_baseline,
    )
    from bt_api_py.trading_calendar_snapshot_budget import check_snapshot_budget

    result = _validated_baseline(content)
    venues = {row["venue"] for row in result["trading_days"]}
    check_snapshot_budget(result, venues)
    assessment, _ = _check_explicit_intervals(result)
    if any(
        assessment[key]
        for key in (
            "missing_mapping_count",
            "missing_date_count",
            "unassessed_exception_count",
            "cancel_window_not_seen_in_declared_windows_scope_day_count",
        )
    ):
        raise ValueError("explicit relationships incomplete")
    return assessment


def _decisions_by_key(decisions):
    if type(decisions) is not list or len(decisions) > 10000:
        raise ValueError("invalid decisions")
    result = {}
    for item in decisions:
        if type(item) is not dict or set(item) != {
            "candidate_id",
            "category",
            "index",
            "reason",
            "source_ids",
            "scopes",
        }:
            raise ValueError("invalid decision")
        key = (item["candidate_id"], item["category"], item["index"])
        if (
            key in result
            or type(item["index"]) is not int
            or item["index"] < 0
            or type(item["reason"]) is not str
            or not item["reason"].strip()
            or type(item["source_ids"]) is not list
            or not item["source_ids"]
            or any(type(ref) is not str for ref in item["source_ids"])
            or len(set(item["source_ids"])) != len(item["source_ids"])
            or type(item["scopes"]) is not list
        ):
            raise ValueError("invalid or duplicate decision")
        result[key] = item
    return result


def _identity(row):
    return (row["venue"], row["kind"], row["product"], tuple(sorted(row["contracts"])))


def _considered_sources(category, value, decision, supplemental):
    """Retain source associations used for each derivation decision."""
    if decision is None:
        return []
    sources = set(decision["source_ids"])
    try:
        if category == "hours_unresolved_nights":
            for row in supplemental["trading_hours"]:
                if _identity(row) == _identity(value):
                    sources.update(row["source_ids"])
        elif category == "holiday_unresolved_nights":
            for scope in decision["scopes"]:
                for row in supplemental["trading_hours"]:
                    if (
                        _identity(row) == _identity(scope)
                        and row["valid_from"] == scope["valid_from"]
                        and row["valid_to"] == scope["valid_to"]
                    ):
                        sources.update(row["source_ids"])
                        for mapping in row["night_mappings"]:
                            if mapping["trading_day"] == scope["owner_day"]:
                                sources.update(mapping["source_ids"])
    except (KeyError, TypeError, ValueError):
        pass
    return sorted(sources)


def _canonical_support(value):
    return json.dumps(
        value, sort_keys=True, ensure_ascii=True, separators=(",", ":"), allow_nan=False
    )


def _support_index(texts, source_ids, cache, original_sources=()):
    """Validate every selected support document together, within fixed work limits."""
    from bt_api_ctp.calendar_supplement import _clock, _date, _id, _venue

    from bt_api_py.trading_calendar_observation_run import _unique_pairs

    key = (tuple(sorted(source_ids)), tuple(sorted(original_sources)))
    if key in cache:
        return cache[key]
    if len(cache) >= 32:
        return None
    fields = ("night_ownership", "night_windows", "exceptions")
    result = {
        "night_ownership": set(),
        "night_windows": set(),
        "exceptions": set(),
        "_window_rows": [],
        "_ownership_rows": [],
        "_exception_rows": [],
        "_selected_sources": set(),
        "_provenance": {"night_ownership": {}, "night_windows": {}, "exceptions": {}},
    }
    ownership, owners, windows, exceptions = {}, {}, {}, {}
    associations = comparisons = 0
    scope_keys = {"venue", "kind", "product", "contracts", "valid_from", "valid_to"}
    try:
        for source_id in key[0]:
            if source_id in original_sources:
                continue
            text = texts.get(source_id)
            if type(text) is not str or len(text.encode("utf-8")) > 2_000_000:
                raise ValueError("unsupported source text")
            doc = json.loads(text, object_pairs_hook=_unique_pairs)
            if (
                type(doc) is not dict
                or set(doc)
                not in (
                    {"schema", "night_ownership", "night_windows"},
                    {"schema", "night_ownership", "night_windows", "exceptions"},
                )
                or doc["schema"] != "bt_api.calendar-review-support/1"
                or any(
                    type(doc.get(field, [])) is not list or len(doc.get(field, [])) > 10000
                    for field in fields
                )
            ):
                raise ValueError("unsupported support document")
            result["_selected_sources"].add(source_id)
            for field in fields:
                document_seen = set()
                for item in doc.get(field, []):
                    if field == "exceptions":
                        if type(item) is not dict or set(item) != set(_EXCEPTION_KEY):
                            raise ValueError("invalid support exception")
                        _venue(item["venue"])
                        if item["kind"] not in {"future", "option"}:
                            raise ValueError("invalid exception kind")
                        _id(item["product"])
                        if item["contract"] is not None:
                            _id(item["contract"])
                        _date(item["trading_day"])
                        if item["action"] == "close_day":
                            if item["window_id"] is not None:
                                raise ValueError("invalid close day")
                        elif item["action"] == "cancel_window":
                            _id(item["window_id"])
                        else:
                            raise ValueError("invalid exception action")
                        encoded = _canonical_support(item)
                        if encoded in document_seen:
                            raise ValueError("duplicate support exception")
                        document_seen.add(encoded)
                        if encoded in result[field]:
                            result["_provenance"][field][encoded].add(source_id)
                            continue
                        result[field].add(encoded)
                        result["_provenance"][field][encoded] = {source_id}
                        result["_exception_rows"].append(item)
                        associations += 1
                        if associations > 20000:
                            raise ValueError("support scope budget")
                        group = tuple(item[k] for k in ("venue", "kind", "product", "trading_day"))
                        exceptions.setdefault(group, []).append(item)
                        continue
                    extra = (
                        {"local_date", "owner_day", "start_date"}
                        if field == "night_ownership"
                        else {"phase", "start", "end", "end_day_offset"}
                    )
                    if type(item) is not dict or set(item) != scope_keys | extra:
                        raise ValueError("invalid support fact")
                    _venue(item["venue"])
                    if item["kind"] not in {"future", "option"}:
                        raise ValueError("invalid kind")
                    _id(item["product"])
                    contracts = item["contracts"]
                    if type(contracts) is not list or not contracts:
                        raise ValueError("invalid contracts")
                    if len(contracts) > 20000 - associations:
                        raise ValueError("support scope budget")
                    associations += len(contracts)
                    for contract in contracts:
                        _id(contract)
                    if len(contracts) != len(set(contracts)):
                        raise ValueError("duplicate contracts")
                    start, end = _date(item["valid_from"]), _date(item["valid_to"])
                    if start > end:
                        raise ValueError("invalid validity")
                    normalized = {**item, "contracts": sorted(contracts)}
                    encoded = _canonical_support(normalized)
                    if encoded in document_seen:
                        raise ValueError("duplicate support fact")
                    document_seen.add(encoded)
                    if encoded in result[field]:
                        result["_provenance"][field][encoded].add(source_id)
                        continue
                    result[field].add(encoded)
                    result["_provenance"][field][encoded] = {source_id}
                    identity = (item["kind"], item["product"])
                    if field == "night_ownership":
                        result["_ownership_rows"].append(normalized)
                        natural, owner = _date(item["local_date"]), _date(item["owner_day"])
                        local_start = (
                            None if item["start_date"] is None else _date(item["start_date"])
                        )
                        if not (natural < owner and start <= owner <= end) or (
                            local_start is not None and local_start != natural
                        ):
                            raise ValueError("invalid owner relation")
                        for contract in contracts:
                            ownership.setdefault((item["venue"], contract, natural), []).append(
                                (start, end, identity)
                            )
                            owners.setdefault((item["venue"], contract, owner), []).append(
                                (start, end, identity)
                            )
                    else:
                        result["_window_rows"].append(normalized)
                        begin, finish = _clock(item["start"]), _clock(item["end"])
                        offset = item["end_day_offset"]
                        if (
                            item["phase"] not in {"continuous", "auction"}
                            or type(offset) is not int
                            or offset not in (0, 1)
                            or (offset == 0 and finish <= begin)
                            or (offset == 1 and finish >= begin)
                        ):
                            raise ValueError("invalid support window")
                        start_min = 60 * begin.hour + begin.minute
                        end_min = 60 * finish.hour + finish.minute + 1440 * offset
                        for contract in contracts:
                            windows.setdefault((item["venue"], contract), []).append(
                                (start, end, identity, start_min, end_min)
                            )
        # One natural calendar date for a concrete contract cannot belong to
        # two owner days, even if the declarations use disjoint valid periods.
        if any(len(rows) > 1 for rows in ownership.values()):
            raise ValueError("conflicting natural-date ownership")
        for rows in owners.values():
            rows.sort()
            if any(rows[index][1] >= rows[index + 1][0] for index in range(len(rows) - 1)):
                raise ValueError("conflicting natural-date ownership")
        for rows in windows.values():
            rows.sort()
            for index, row in enumerate(rows):
                for other in rows[index + 1 :]:
                    comparisons += 1
                    if comparisons > 200000:
                        raise ValueError("support comparison budget")
                    if other[0] > row[1]:
                        break
                    if row[2] != other[2] or (row[3] < other[4] and other[3] < row[4]):
                        raise ValueError("conflicting support windows")
        for rows in exceptions.values():
            for index, row in enumerate(rows):
                for other in rows[index + 1 :]:
                    comparisons += 1
                    if comparisons > 200000:
                        raise ValueError("support comparison budget")
                    if (
                        row["contract"] == other["contract"]
                        or row["contract"] is None
                        or other["contract"] is None
                    ) and (
                        row["action"] == "close_day"
                        or other["action"] == "close_day"
                        or row["window_id"] == other["window_id"]
                    ):
                        raise ValueError("conflicting support exceptions")
        cache[key] = result
    except (Exception, RecursionError):
        cache[key] = None
    return cache[key]


def _support_has(texts, source_ids, field, expected, cache, original_sources=()):
    """Match a fact only after all selected support sources pass conflict checks."""
    index = _support_index(texts, source_ids, cache, original_sources)
    return index is not None and _canonical_support(expected) in index[field]


def _window_fact(row, window):
    return {
        key: row[key] for key in ("venue", "kind", "product", "contracts", "valid_from", "valid_to")
    } | {key: window[key] for key in ("phase", "start", "end", "end_day_offset")}


def _charge_support(cache, amount):
    work = cache.get(_SUPPORT_WORK, 0) + amount
    if work > 200000:
        return False
    cache[_SUPPORT_WORK] = work
    return True


def _exception_sources(shard, cache):
    memo = cache.setdefault(_EXCEPTION_SOURCES, {})
    key = id(shard)
    if key in memo:
        return memo[key]
    rows = shard["layers"]["exceptions"]
    if not _charge_support(cache, sum(1 + len(row["source_ids"]) for row in rows)):
        return None
    sources = {source for row in rows for source in row["source_ids"]}
    memo[key] = sources
    return sources


def _support_matches_template(support, row, cache):
    """Do not silently omit scoped facts or source associations from a template."""
    contracts = row["contracts"]
    initial_work = len(contracts) * max(1, len(contracts).bit_length()) + len(row["source_ids"])
    initial_work += len(row["night_windows"]) + sum(
        1 + len(m["source_ids"]) for m in row["night_mappings"]
    )
    if not _charge_support(cache, initial_work):
        return False
    contract_set = set(contracts)
    contract_key = tuple(sorted(contracts))
    row_sources = set(row["source_ids"])
    windows = {
        (w["phase"], w["start"], w["end"], w["end_day_offset"]) for w in row["night_windows"]
    }
    mappings = {
        item["trading_day"]: (item, set(item["source_ids"])) for item in row["night_mappings"]
    }
    for field, entries in (
        ("night_windows", support["_window_rows"]),
        ("night_ownership", support["_ownership_rows"]),
    ):
        for item in entries:
            if not _charge_support(cache, 1):
                return False
            if (
                item["venue"] != row["venue"]
                or item["valid_from"] > row["valid_to"]
                or row["valid_from"] > item["valid_to"]
            ):
                continue
            if not _charge_support(cache, len(item["contracts"])):
                return False
            if not any(contract in contract_set for contract in item["contracts"]):
                continue
            if (
                item["kind"],
                item["product"],
                tuple(item["contracts"]),
                item["valid_from"],
                item["valid_to"],
            ) != (row["kind"], row["product"], contract_key, row["valid_from"], row["valid_to"]):
                return False
            provenance = support["_provenance"][field][_canonical_support(item)]
            if not _charge_support(cache, len(provenance)):
                return False
            if field == "night_windows":
                fact = (item["phase"], item["start"], item["end"], item["end_day_offset"])
                if fact not in windows or not provenance <= row_sources:
                    return False
            else:
                match = mappings.get(item["owner_day"])
                if (
                    match is None
                    or match[0]["start_date"] != item["start_date"]
                    or not provenance <= match[1]
                ):
                    return False
    return True


def _original_note_supports_exception(exception, shard, context, cache):
    """Only the recognized original page note can support its own exception."""
    from datetime import date

    source, notes, reviews = context
    if source not in exception["source_ids"]:
        return True
    if exception["action"] != "cancel_window" or exception["contract"] is None:
        return False
    rows = shard["layers"]["trading_hours"]
    if not _charge_support(cache, len(notes)):
        return False
    for note in notes:
        if type(note) is str and not _charge_support(cache, len(note)):
            return False
        match = _NOTE.fullmatch(note) if type(note) is str else None
        if match is None:
            continue
        try:
            day = date(int(match["year"]), int(match["month"]), int(match["day"])).isoformat()
        except ValueError:
            continue
        if day != exception["trading_day"]:
            continue
        if not _charge_support(cache, len(reviews)):
            return False
        for review in reviews:
            if review["page_product_name"] != match["name"] or (
                review["venue"],
                review["kind"],
                review["product"],
            ) != (exception["venue"], exception["kind"], exception["product"]):
                continue
            if not _charge_support(cache, len(review["contracts"])):
                return False
            contracts = set(review["contracts"])
            if exception["contract"] not in contracts:
                continue
            if not _charge_support(cache, len(rows)):
                return False
            for row in rows:
                if (row["venue"], row["kind"], row["product"]) != (
                    review["venue"],
                    review["kind"],
                    review["product"],
                ) or not row["valid_from"] <= day <= row["valid_to"]:
                    continue
                if not _charge_support(cache, len(row["contracts"])):
                    return False
                if set(row["contracts"]) != contracts:
                    continue
                if not _charge_support(cache, len(row["day_windows"])):
                    return False
                if any(
                    w["id"] == exception["window_id"]
                    and w["start"] == match["start"]
                    and w["end"] == match["end"]
                    and w["end_day_offset"] == 0
                    for w in row["day_windows"]
                ):
                    return True
    return False


def _support_matches_candidate(support, shard, cache, original_context):
    """Account for every fact in the selected sources over the declared shard."""
    memo = cache.setdefault(_SUPPORT_VALIDATIONS, {})
    key = (id(support), id(shard), original_context[0], id(original_context[1]))
    if key in memo:
        return memo[key]
    if len(memo) >= 64:
        return False
    memo[key] = False
    if (
        not any(support[name] for name in ("_window_rows", "_ownership_rows", "_exception_rows"))
        and not shard["layers"]["exceptions"]
    ):
        memo[key] = True
        return True
    try:
        layers = shard["layers"]
        coverage_start, coverage_end = shard["coverage_start"], shard["coverage_end"]
        hours, by_contract, by_product = {}, {}, {}
        for row in layers["trading_hours"]:
            size = len(row["contracts"])
            amount = (
                size * max(1, size.bit_length())
                + len(row["source_ids"])
                + len(row["night_windows"])
            )
            amount += sum(1 + len(mapping["source_ids"]) for mapping in row["night_mappings"])
            if not _charge_support(cache, amount):
                return False
            identity = _hour_key(row)
            if identity in hours:
                return False
            hours[identity] = (
                row,
                set(row["source_ids"]),
                {
                    (w["phase"], w["start"], w["end"], w["end_day_offset"])
                    for w in row["night_windows"]
                },
                {m["trading_day"]: (m, set(m["source_ids"])) for m in row["night_mappings"]},
            )
            product = (row["venue"], row["kind"], row["product"])
            by_product.setdefault(product, []).append(row)
            for contract in row["contracts"]:
                by_contract.setdefault((*product, contract), []).append(row)
        if not _charge_support(cache, len(layers["trading_days"]) + len(layers["exceptions"])):
            return False
        days = {(row["venue"], row["day"]): row["status"] for row in layers["trading_days"]}
        exceptions = {}
        for row in layers["exceptions"]:
            if not _charge_support(cache, len(row["source_ids"])):
                return False
            encoded = _canonical_support({name: row[name] for name in _EXCEPTION_KEY})
            references = set(row["source_ids"])
            selected = references & support["_selected_sources"]
            if not selected <= support["_provenance"]["exceptions"].get(encoded, set()):
                return False
            if not _original_note_supports_exception(row, shard, original_context, cache):
                return False
            exceptions[encoded] = references
        for field, entries in (
            ("night_windows", support["_window_rows"]),
            ("night_ownership", support["_ownership_rows"]),
        ):
            for item in entries:
                size = len(item["contracts"])
                provenance = support["_provenance"][field][_canonical_support(item)]
                if not _charge_support(
                    cache, 1 + size * max(1, size.bit_length()) + len(provenance)
                ):
                    return False
                if (
                    item["venue"] != shard["venue"]
                    or item["valid_to"] < coverage_start
                    or item["valid_from"] > coverage_end
                ):
                    return False
                actual = hours.get(_hour_key(item))
                if actual is None:
                    return False
                if field == "night_windows":
                    value = (item["phase"], item["start"], item["end"], item["end_day_offset"])
                    if value not in actual[2] or not provenance <= actual[1]:
                        return False
                else:
                    if not coverage_start <= item["owner_day"] <= coverage_end:
                        return False
                    mapping = actual[3].get(item["owner_day"])
                    if (
                        mapping is None
                        or mapping[0]["start_date"] != item["start_date"]
                        or not provenance <= mapping[1]
                    ):
                        return False
        for item in support["_exception_rows"]:
            provenance = support["_provenance"]["exceptions"][_canonical_support(item)]
            if not _charge_support(cache, 1 + len(provenance)):
                return False
            day = item["trading_day"]
            product = (item["venue"], item["kind"], item["product"])
            candidates = (
                by_product.get(product, [])
                if item["contract"] is None
                else by_contract.get((*product, item["contract"]), [])
            )
            if not _charge_support(cache, len(candidates)):
                return False
            if (
                item["venue"] != shard["venue"]
                or not coverage_start <= day <= coverage_end
                or days.get((item["venue"], day)) != "open"
                or not any(row["valid_from"] <= day <= row["valid_to"] for row in candidates)
            ):
                return False
            sources = exceptions.get(_canonical_support(item))
            if sources is None or not provenance <= sources:
                return False
        memo[key] = True
        return True
    except (KeyError, TypeError, ValueError):
        return False


def _has_original_stages(
    review, row, texts, decision, cache=None, original_sources=(), shard=None, original_context=None
):
    cache = {} if cache is None else cache
    all_sources = set(decision["source_ids"]) | set(row.get("source_ids", ()))
    if shard is not None:
        extra = _exception_sources(shard, cache)
        if extra is None or original_context is None:
            return False
        all_sources.update(extra)
    support = _support_index(texts, all_sources, cache, original_sources)
    if support is None:
        return False
    work = (
        cache.get(_SUPPORT_WORK, 0) + len(support["_window_rows"]) + len(support["_ownership_rows"])
    )
    if work > 200000:
        return False
    cache[_SUPPORT_WORK] = work
    phases = {"AUCTION_ORDER": "auction", "AUCTION_MATCH": "auction", "CONTINUOUS": "continuous"}
    observed = []
    inferred = []
    for stage in review["stages"]:
        phase = phases.get(stage["phase"])
        transition = stage["day_transition"]
        if phase is None or transition not in {
            "SAME_CLOCK_DAY",
            "INFERRED_NEXT_DAY",
            "EXPLICIT_NEXT_DAY",
        }:
            return False
        value = (phase, stage["start"], stage["end"], int(transition != "SAME_CLOCK_DAY"))
        observed.append(value)
        if transition == "INFERRED_NEXT_DAY":
            inferred.append(value)
    supplied = [
        (w["phase"], w["start"], w["end"], w["end_day_offset"]) for w in row["night_windows"]
    ]
    if not _support_matches_template(support, row, cache):
        return False
    if shard is not None and not _support_matches_candidate(
        support, shard, cache, original_context
    ):
        return False
    if not _charge_support(cache, len(supplied) + len(observed)):
        return False
    supplied_counts, observed_counts = Counter(supplied), Counter(observed)
    if any(supplied_counts[stage] < count for stage, count in observed_counts.items()):
        return False
    extras = list((supplied_counts - observed_counts).elements())
    for stage in extras + inferred:
        phase, start, end, offset = stage
        window = {"phase": phase, "start": start, "end": end, "end_day_offset": offset}
        if not _support_has(
            texts,
            all_sources,
            "night_windows",
            _window_fact(row, window),
            cache,
            original_sources,
        ):
            return False
    return True


_NOTE = re.compile(
    r"注：(?P<name>[^\s，。]{1,128})于(?P<year>[0-9]{4})年(?P<month>[0-9]{1,2})月"
    r"(?P<day>[0-9]{1,2})日(?P<start>[0-9]{2}:[0-9]{2})至(?P<end>[0-9]{2}:[0-9]{2})暂停交易。\Z"
)


def _known_note(
    value,
    decision,
    *,
    shard,
    supplemental,
    hours_source,
    reviews,
    texts,
    support_cache,
    considered_sources,
    page_notes,
):
    """Recognize only a dated product-specific cancellation in the original page."""
    from datetime import date

    if decision["scopes"] or hours_source not in decision["source_ids"] or type(value) is not str:
        return False
    match = _NOTE.fullmatch(value)
    if match is None:
        return False
    try:
        day = date(int(match["year"]), int(match["month"]), int(match["day"])).isoformat()
        selected = [item for item in reviews if item["page_product_name"] == match["name"]]
        if len(selected) != 1:
            return False
        targets = [
            row for row in supplemental["trading_hours"] if _identity(row) == _identity(selected[0])
        ]
        if len(targets) != 1:
            return False
        row = targets[0]
        if not row["valid_from"] <= day <= row["valid_to"]:
            return False
        windows = [
            w
            for w in row["day_windows"]
            if w["start"] == match["start"]
            and w["end"] == match["end"]
            and w["end_day_offset"] == 0
        ]
        if len(windows) != 1:
            return False
        amount = (
            len(decision["source_ids"])
            + len(row["source_ids"])
            + len(shard["layers"]["trading_days"])
        )
        amount += sum(1 + len(e["source_ids"]) for e in supplemental["exceptions"])
        if not _charge_support(support_cache, amount):
            return False
        decision_sources, row_sources = set(decision["source_ids"]), set(row["source_ids"])
        candidates = []
        for exception in supplemental["exceptions"]:
            if (
                exception["venue"] == row["venue"]
                and exception["kind"] == row["kind"]
                and exception["product"] == row["product"]
                and exception["trading_day"] == day
                and exception["action"] == "cancel_window"
                and exception["window_id"] == windows[0]["id"]
            ):
                references = set(exception["source_ids"])
                if decision_sources <= references:
                    candidates.append((exception, references))
        if not _charge_support(support_cache, len(row["contracts"]) * len(candidates)):
            return False
        if not any(
            d["venue"] == row["venue"] and d["day"] == day and d["status"] == "open"
            for d in shard["layers"]["trading_days"]
        ):
            return False
        for contract in row["contracts"]:
            matches = [
                (exception, references)
                for exception, references in candidates
                if exception["contract"] in (None, contract)
            ]
            if len(matches) != 1:
                return False
            exception, references = matches[0]
            exception_sources = _exception_sources(shard, support_cache)
            if exception_sources is None:
                return False
            actual_sources = references | row_sources | decision_sources | exception_sources
            considered_sources.update(actual_sources)
            support = _support_index(texts, actual_sources, support_cache, {hours_source})
            if support is None:
                return False
            work = (
                support_cache.get(_SUPPORT_WORK, 0)
                + len(support["_exception_rows"])
                + len(support["_window_rows"])
                + len(support["_ownership_rows"])
                + len(supplemental["exceptions"])
            )
            if work > 200000:
                return False
            support_cache[_SUPPORT_WORK] = work
            if not _support_matches_template(support, row, support_cache):
                return False
            if not _support_matches_candidate(
                support, shard, support_cache, (hours_source, page_notes, reviews)
            ):
                return False
            fact = {key: exception[key] for key in _EXCEPTION_KEY}
            supplied_by = support["_provenance"]["exceptions"].get(_canonical_support(fact), set())
            if not references - {hours_source} <= supplied_by:
                return False
        return True
    except (ValueError, TypeError, KeyError):
        return False


def _resolve_source_item(
    category,
    value,
    decision,
    *,
    shard,
    observed_shard,
    supplemental,
    hours_source,
    texts,
    reviews,
    notes_resolved,
    support_cache,
    considered_sources,
    page_notes,
):
    sources = {row["id"] for row in shard["layers"]["sources"]}
    if not set(decision["source_ids"]) <= sources or hours_source not in sources:
        return False
    if category == "page_notes":
        return _known_note(
            value,
            decision,
            shard=shard,
            supplemental=supplemental,
            hours_source=hours_source,
            reviews=reviews,
            texts=texts,
            support_cache=support_cache,
            considered_sources=considered_sources,
            page_notes=page_notes,
        )
    if category == "page_review_reasons":
        if decision["scopes"] or hours_source not in decision["source_ids"]:
            return False
        if value == "PAGE_NOTE_UNVERIFIED":
            return notes_resolved
        if value == "CROSS_MIDNIGHT_UNVERIFIED":
            inferred = [
                item
                for item in reviews
                if any(s["day_transition"] == "INFERRED_NEXT_DAY" for s in item["stages"])
            ]
            if not inferred:
                return False
            for item in inferred:
                rows = [
                    row
                    for row in supplemental["trading_hours"]
                    if _identity(row) == _identity(item)
                ]
                if (
                    len(rows) != 1
                    or not set(decision["source_ids"]) <= set(rows[0]["source_ids"])
                    or not _has_original_stages(
                        item,
                        rows[0],
                        texts,
                        decision,
                        support_cache,
                        {hours_source},
                        shard,
                        (hours_source, page_notes, reviews),
                    )
                ):
                    return False
            return True
        return False
    if category == "hours_unresolved_nights":
        if decision["scopes"] or hours_source not in decision["source_ids"]:
            return False
        targets = [
            row for row in supplemental["trading_hours"] if _identity(row) == _identity(value)
        ]
        observed = [
            row
            for row in observed_shard["layers"]["trading_hours"]
            if _identity(row) == _identity(value) and hours_source in row["source_ids"]
        ]
        if (
            len(targets) != 1
            or len(observed) != 1
            or (targets[0]["valid_from"], targets[0]["valid_to"])
            != (observed[0]["valid_from"], observed[0]["valid_to"])
        ):
            return False
        target = targets[0]
        return (
            hours_source in target["source_ids"]
            and set(decision["source_ids"]) <= set(target["source_ids"])
            and _has_original_stages(
                value,
                target,
                texts,
                decision,
                support_cache,
                {hours_source},
                shard,
                (hours_source, page_notes, reviews),
            )
            and target["windows_complete"] is True
            and target["exceptions_complete"] is True
        )
    if category != "holiday_unresolved_nights" or not decision["scopes"]:
        return False
    holiday_source = value["source_id"]
    if holiday_source not in decision["source_ids"]:
        return False
    # Every declared template for this venue needs an explicit owner mapping;
    # neither the preceding natural day nor a weekend is inferred.
    all_hours = shard["layers"]["trading_hours"]
    required = {_identity(row) + (row["valid_from"], row["valid_to"]) for row in all_hours}
    resolved = set()
    for scope in decision["scopes"]:
        if type(scope) is not dict or set(scope) != {
            "venue",
            "kind",
            "product",
            "contracts",
            "valid_from",
            "valid_to",
            "owner_day",
        }:
            return False
        try:
            ident = _identity(scope) + (scope["valid_from"], scope["valid_to"])
            owner = scope["owner_day"]
            if not (
                scope["valid_from"] <= owner <= scope["valid_to"]
                and shard["coverage_start"] <= owner <= shard["coverage_end"]
                and value["local_date"] < owner
            ):
                return False
            from bt_api_py.trading_calendar_review_bundle import _date

            _date(owner)
            targets = [
                row
                for row in supplemental["trading_hours"]
                if _identity(row) + (row["valid_from"], row["valid_to"]) == ident
            ]
            if len(targets) != 1 or ident in resolved:
                return False
            mappings = [m for m in targets[0]["night_mappings"] if m["trading_day"] == owner]
            if (
                len(mappings) != 1
                or mappings[0]["start_date"] is not None
                or not set(decision["source_ids"]) <= set(mappings[0]["source_ids"])
                or len(set(mappings[0]["source_ids"]) - {holiday_source}) == 0
            ):
                return False
            fact = {
                key: scope[key]
                for key in (
                    "venue",
                    "kind",
                    "product",
                    "contracts",
                    "valid_from",
                    "valid_to",
                    "owner_day",
                )
            }
            fact.update({"local_date": value["local_date"], "start_date": None})
            exception_sources = _exception_sources(shard, support_cache)
            if exception_sources is None or holiday_source in exception_sources:
                return False
            support_sources = (
                set(decision["source_ids"])
                | set(mappings[0]["source_ids"])
                | set(targets[0]["source_ids"])
                | exception_sources
            ) - {holiday_source}
            if not _support_has(
                texts, support_sources, "night_ownership", fact, support_cache, {hours_source}
            ):
                return False
            indexed = _support_index(texts, support_sources, support_cache, {hours_source})
            if indexed is None or not _support_matches_candidate(
                indexed, shard, support_cache, (hours_source, page_notes, reviews)
            ):
                return False
            days = [
                d
                for d in shard["layers"]["trading_days"]
                if d["venue"] == scope["venue"] and d["day"] == owner
            ]
            if len(days) != 1 or days[0]["status"] != "open":
                return False
            resolved.add(ident)
        except (KeyError, TypeError, ValueError):
            return False
    return required == resolved


def _opening_from_original(value):
    """Recover the explicitly parsed reopening date, never infer an owner day."""
    from bt_api_ctp.calendar_shfe_holiday import _row

    source_id = value["source_id"]
    parts = source_id.split(".")
    if len(parts) != 4 or parts[0] not in {"shfe", "ine"} or parts[1] != "holiday":
        raise ValueError("invalid holiday source")
    _, row, opened = _row(value["raw_text"], int(parts[2]))
    if row.name != value["holiday"] or row.no_night.isoformat() != value["local_date"]:
        raise ValueError("holiday text mismatch")
    return {"day": opened.isoformat(), "source_id": source_id}


def evaluate_derived(
    *, root, filesystem, candidate_id, parent_id, parent_sha256, supplement, source_texts, decisions
):
    """Recompute a derived body from its original /3 and captured supplement."""
    import hashlib
    import json

    from bt_api_ctp.calendar_candidate_normalize import _check_and_order
    from bt_api_ctp.calendar_supplement import _validate

    from bt_api_py import trading_calendar_detail_collection as storage
    from bt_api_py import trading_calendar_review_bundle as review

    if parent_id == candidate_id or not review._SHA256.fullmatch(parent_sha256):
        raise ValueError("invalid parent")
    parent = review.show(root=root, candidate_id=parent_id, filesystem=filesystem)
    if (
        parent.get("status") != "VISIBLE_UNAUTHENTICATED"
        or parent.get("payload_sha256") != parent_sha256
        or parent["bundle"]["schema"] != review._SCHEMA_REFERENCES
    ):
        raise ValueError("parent unavailable")
    review._bounded(supplement)
    review._bounded(source_texts)
    review._bounded(decisions)
    normalized = _check_and_order(_validate(deepcopy(supplement)).content)
    digest = hashlib.sha256(storage._json(normalized)).hexdigest()
    selected = _decisions_by_key(decisions)
    data = deepcopy(parent["bundle"]["input"])
    if type(source_texts) is not dict or type(data.get("source_texts")) is not dict:
        raise ValueError("source texts missing")
    source_ids = {row["id"] for row in normalized["sources"]}
    if not set(source_texts) <= source_ids:
        raise ValueError("unexpected source text")
    for key, text in source_texts.items():
        if key in data["source_texts"] and data["source_texts"][key] != text:
            raise ValueError("source text conflict")
        data["source_texts"][key] = text
    for shard in data["shards"]:
        venue = shard["venue"]
        addition = {"sources": normalized["sources"]}
        for layer in _LAYERS[1:]:
            addition[layer] = [row for row in normalized[layer] if row["venue"] == venue]
        if not any(addition[layer] for layer in _LAYERS[1:]):
            continue
        shard["layers"] = merge_supplement_layers(
            shard["layers"],
            addition,
            venue=venue,
            coverage_start=review._date(shard["coverage_start"]),
            coverage_end=review._date(shard["coverage_end"]),
        )
    unresolved_records = [
        {"venue": shard["venue"], "items": deepcopy(shard.get("unresolved"))}
        for shard in parent["bundle"]["input"]["shards"]
    ]
    decisions_result = []
    categories = (
        "holiday_unresolved_nights",
        "hours_unresolved_nights",
        "page_notes",
        "page_review_reasons",
    )
    structural_checks = {
        "CROSS_LAYER_CONFLICTS_NOT_CHECKED",
        "EXCEPTIONS_CONFLICTS_NOT_CHECKED",
        "HOURS_NIGHT_MAPPING_UNRESOLVED",
        "NIGHT_TRADING_DAY_UNRESOLVED",
        "TRADING_HOURS_CONFLICTS_NOT_CHECKED",
    }
    retained = {"COVERAGE_NOT_PROVEN", "SOURCE_AUTHORITY_UNVERIFIED", "OTHER_SESSIONS_NOT_CHECKED"}
    used = set()
    holiday_mappings = set()
    original_openings = []
    support_cache = {}
    for shard in data["shards"]:
        observed_shard = next(
            item for item in parent["bundle"]["input"]["shards"] if item["venue"] == shard["venue"]
        )
        pending = []
        for record in shard["unresolved"]:
            if type(record) is not dict or set(record) != {
                "candidate_id",
                "payload_sha256",
                "holiday_unresolved_nights",
                "hours_unresolved_nights",
                "remaining_checks",
                "page_notes",
                "page_review_reasons",
            }:
                pending.append(record)
                continue
            source_candidate = record["candidate_id"]
            # A later record may add or union an observed reopening date.
            # Never reuse a whole-shard check across that mutation.
            support_cache.pop(_SUPPORT_VALIDATIONS, None)
            for holiday in record["holiday_unresolved_nights"]:
                try:
                    opening = _opening_from_original(holiday)
                    opening["venue"] = shard["venue"]
                    opening["candidate_id"] = source_candidate
                    original_openings.append(opening)
                    matches = [
                        d
                        for d in shard["layers"]["trading_days"]
                        if d["venue"] == shard["venue"] and d["day"] == opening["day"]
                    ]
                    if len(matches) > 1 or (matches and matches[0]["status"] != "open"):
                        pending.append({"announcement_opening_conflict": opening})
                    elif matches:
                        matches[0]["source_ids"] = _refs(
                            matches[0]["source_ids"], [opening["source_id"]]
                        )
                    else:
                        shard["layers"]["trading_days"].append(
                            {
                                "venue": shard["venue"],
                                "day": opening["day"],
                                "status": "open",
                                "source_ids": [opening["source_id"]],
                            }
                        )
                except Exception:
                    pending.append({"announcement_opening": "UNRESOLVED", "original": holiday})
            hour_sources = {
                ref
                for row in shard["layers"]["trading_hours"]
                for ref in row["source_ids"]
                if review._KNOWN_SOURCE.fullmatch(ref) and ".hours." in ref
            }
            if len(hour_sources) != 1:
                pending.append(record)
                continue
            hours_source = next(iter(hour_sources))
            addition = {
                "sources": normalized["sources"],
                **{
                    layer: [row for row in normalized[layer] if row["venue"] == shard["venue"]]
                    for layer in _LAYERS[1:]
                },
            }
            note_results = []
            for category in categories:
                for index, value in enumerate(record[category]):
                    key = (source_candidate, category, index)
                    decision = selected.get(key)
                    if decision is not None:
                        used.add(key)
                    considered_sources = set(
                        _considered_sources(category, value, decision, addition)
                    )
                    if decision is not None and category in {
                        "hours_unresolved_nights",
                        "holiday_unresolved_nights",
                    }:
                        extra_sources = _exception_sources(shard, support_cache)
                        if extra_sources is not None:
                            considered_sources.update(extra_sources)
                    ok = decision is not None and _resolve_source_item(
                        category,
                        value,
                        decision,
                        shard=shard,
                        observed_shard=observed_shard,
                        supplemental=addition,
                        hours_source=hours_source,
                        texts=data["source_texts"],
                        reviews=record["hours_unresolved_nights"],
                        notes_resolved=bool(note_results) and all(note_results),
                        support_cache=support_cache,
                        considered_sources=considered_sources,
                        page_notes=record["page_notes"],
                    )
                    if category == "page_notes":
                        note_results.append(ok)
                    if ok and category == "holiday_unresolved_nights":
                        mapping_keys = {
                            _identity(scope)
                            + (scope["valid_from"], scope["valid_to"], scope["owner_day"])
                            for scope in decision["scopes"]
                        }
                        if holiday_mappings.intersection(mapping_keys):
                            ok = False
                        else:
                            holiday_mappings.update(mapping_keys)
                    item = {
                        "candidate_id": source_candidate,
                        "category": category,
                        "index": index,
                        "original": value,
                        "decision": decision,
                        "source_ids_considered": sorted(considered_sources),
                        "status": "RESOLVED_UNVERIFIED" if ok else "UNRESOLVED",
                    }
                    decisions_result.append(item)
                    if not ok:
                        pending.append(item)
            unknown = set(record["remaining_checks"]) - structural_checks - retained
            if unknown:
                pending.append(
                    {"candidate_id": source_candidate, "unknown_checks": sorted(unknown)}
                )
        shard["unresolved"] = pending
    if used != set(selected):
        raise ValueError("decision does not reference original item")
    # Exact requirements must cover every explicit template intersection; checking
    # only a subset cannot prove the supplied template was structurally assessed.
    for shard in data["shards"]:
        for row in shard["layers"]["trading_hours"]:
            start, end = (
                max(row["valid_from"], shard["coverage_start"]),
                min(row["valid_to"], shard["coverage_end"]),
            )
            if start > end:
                continue
            for contract in row["contracts"]:
                if not any(
                    req["kind"] == row["kind"]
                    and req["product"] == row["product"]
                    and req["contract"] == contract
                    and req["start_day"] <= start
                    and req["end_day"] >= end
                    for req in shard["requirements"]
                ):
                    shard["unresolved"].append(
                        {"unassessed_template": _identity(row), "contract": contract}
                    )
        try:
            validate_explicit_relations(shard["layers"])
        except Exception:
            shard["unresolved"].append({"explicit_relations": "INCOMPLETE_OR_CONFLICTING"})
    # Convert tuples used for local keys before bounding and canonical serialization.
    data = review._jsonable(data)
    review._bounded(data)
    body = review._evaluate(data, candidate_id)
    body["schema"] = review._SCHEMA_DERIVED
    body["report"]["identity_authentication"] = "NOT_PROVEN"
    if body["report"]["unresolved_status"] == "NONE_DECLARED":
        body["report"]["unresolved_status"] = "STRUCTURAL_ITEMS_RESOLVED_EXTERNAL_NOT_PROVEN"
    check_decisions = []
    for original in unresolved_records:
        current = next(shard for shard in data["shards"] if shard["venue"] == original["venue"])
        for record in original["items"] or []:
            if (
                type(record) is not dict
                or type(record.get("remaining_checks")) is not list
                or type(record.get("candidate_id")) is not str
            ):
                continue
            for check in record["remaining_checks"]:
                if check in retained:
                    status, reason = (
                        "NOT_PROVEN",
                        "Source authority and coverage, including unobserved sessions, remain external checks",
                    )
                elif (
                    check in structural_checks
                    and not current["unresolved"]
                    and body["report"]["checker_status"] == "READY_SIMULATED"
                ):
                    status, reason = (
                        "CHECKED_UNVERIFIED",
                        "Explicit supplement and simulated structural checks",
                    )
                else:
                    status, reason = (
                        "UNRESOLVED",
                        "Required explicit data or structural checks are incomplete",
                    )
                check_decisions.append(
                    {
                        "candidate_id": record["candidate_id"],
                        "check": check,
                        "status": status,
                        "reason": reason,
                    }
                )
    body["assembly"] = {
        "parent": {"candidate_id": parent_id, "payload_sha256": parent_sha256},
        "supplement": normalized,
        "supplement_normalized_sha256": digest,
        "supplement_source_texts": source_texts,
        "decisions": decisions,
        "original_unresolved": unresolved_records,
        "original_openings": original_openings,
        "item_decisions": decisions_result,
        "check_decisions": check_decisions,
        "retained_checks": sorted(retained),
    }
    # Decoded form matches persisted canonical JSON even with arbitrary caller objects.
    return json.loads(storage._json(body))
