"""Build and inspect an immutable, unauthenticated calendar review bundle.

The structural checker is explicitly SIMULATION_ONLY. This module retains its
output for human inspection; it does not authenticate inputs, authorize or
perform publication, or write CURRENT. Caller-declared scopes are not a venue
instrument inventory. A COMMIT_UNKNOWN must be inspected with the same ID.
Filesystem guarantees apply only to cooperating trusted local POSIX writers;
ABA, hostile replacement, and power-loss durability are not proven.
The returned payload_sha256 hashes the envelope body serialized as canonical
ASCII JSON (sorted keys, no insignificant whitespace). Show recomputes using
the current CTP provider and structural checker: missing or incompatible
dependencies, including later upgrades, may yield UNKNOWN.

Supply public source material only: the Python ``show`` API returns the entire
input and is not a general secret filter. CLI ``show`` returns a bounded summary.
CLI ``compare-full`` also defaults to a summary; ``--show-details`` requests
the complete difference after conservative checks, which cannot detect every
secret. The Python ``compare_full`` API returns unredacted public input.
For ``build``, pass JSON on standard input; when using conda run, use
``conda run --no-capture-output -n base python -m ... build``.
The object has ``shards`` (six entries), optional ``source_texts`` and
``metadata``. Each shard declares venue, coverage_start, coverage_end, layers,
requirements, unresolved, and optional provenance. Requirements declare kind,
product, contract, start_day and end_day. Dates use ISO YYYY-MM-DD. Each layer
uses the existing CTP four-layer schema. Missing source text or unresolved items
remain PARTIAL. The declared scopes do not prove full exchange coverage.

Optional repeated ``--hours-candidate-id`` arguments on build explicitly select
one or two already retained SHFE/INE hours-evidence candidates in the same
root. The stdin object remains the ordinary review input. Their evidence and
unresolved details are merged without inferring dates, contracts or validity.
Version 3 merges matching trading-day source references, retains the caller
input and candidate references, and revalidates them on show.
"""

from __future__ import annotations

import argparse
import contextlib
import fcntl
import hashlib
import json
import os
import re
import stat
import sys
from dataclasses import asdict
from datetime import date

from bt_api_py import trading_calendar_detail_batch as detail
from bt_api_py import trading_calendar_detail_collection as storage
from bt_api_py import trading_calendar_observation_run as runs
from bt_api_py.trading_calendar_preview_reports import (
    TRUSTED_LOCAL_POSIX,
    _directory_matches,
    _identity,
    _open_directory,
    _supported,
)

_SCHEMA_V1 = "bt_api.calendar-review-bundle/1"
_SCHEMA = "bt_api.calendar-review-bundle/2"
_SCHEMA_REFERENCES = "bt_api.calendar-review-bundle/3"
_SCHEMA_DERIVED = "bt_api.calendar-review-bundle/4"
_INPUT_LIMIT = 32 * 1024 * 1024
_OUTPUT_LIMIT = 96 * 1024 * 1024
_VENUES = frozenset({"SHFE", "INE", "DCE", "CZCE", "CFFEX", "GFEX"})
_HASH_ONLY = re.compile(
    r"(?i)(?:(?:sha-?(?:1|256|512)|md5)\s*[:=]\s*)?"
    r"(?:[0-9a-f]{32}|[0-9a-f]{40}|[0-9a-f]{64}|[0-9a-f]{128})\Z"
)
_LEGACY_SOURCE = re.compile(r"shfe\.(?:hours\.|holiday\.[0-9]{4}\.)([0-9a-f]{64})\Z")
_KNOWN_SOURCE = re.compile(r"(?:shfe|ine)\.(?:hours\.|holiday\.[0-9]{4}\.)([0-9a-f]{64})\Z")
_SAFE_IDENTIFIER = re.compile(r"[A-Za-z0-9][A-Za-z0-9_.-]{0,127}\Z")
_SAFE_CHANGE_STRING = re.compile(r"[A-Za-z0-9][A-Za-z0-9_.:-]{0,127}\Z")
_SHA256 = re.compile(r"[0-9a-f]{64}\Z")


def _bounded(value):
    """Check caller objects before serializing, including cyclic containers."""
    pending = [(value, 0)]
    nodes = size = 0
    while pending:
        item, depth = pending.pop()
        nodes += 1
        if nodes > 1_000_000 or depth > 32:
            raise ValueError("input budget")
        size += 8
        if type(item) is str:
            size += 6 * len(item)
        elif type(item) is dict:
            if len(item) > 1_000_000 - nodes - len(pending):
                raise ValueError("input nodes")
            for key, child in item.items():
                if type(key) is not str:
                    raise ValueError("input key")
                size += 6 * len(key) + 4
                pending.append((child, depth + 1))
        elif type(item) is list:
            if len(item) > 1_000_000 - nodes - len(pending):
                raise ValueError("input nodes")
            pending.extend((child, depth + 1) for child in item)
        elif item is None or type(item) is bool:
            pass
        elif type(item) is int and item.bit_length() <= 64:
            size += 24
        else:
            raise ValueError("input type")
        if size > _INPUT_LIMIT:
            raise ValueError("input size")


def _date(text):
    if type(text) is not str:
        raise ValueError("invalid date")
    result = date.fromisoformat(text)
    if result.isoformat() != text:
        raise ValueError("invalid date")
    return result


def _jsonable(value):
    if isinstance(value, date):
        return value.isoformat()
    if isinstance(value, dict):
        return {k: _jsonable(v) for k, v in value.items()}
    if isinstance(value, (tuple, list)):
        return [_jsonable(v) for v in value]
    return value


def _date_check(raw, venue, start, end):
    """Count declared venue days; this is not source or instrument evidence."""
    invalid = {
        "date_status": "INVALID_DATE_LAYER",
        "missing_date_count": None,
        "duplicate_date_count": None,
    }
    if start > end or (end - start).days + 1 > 731:
        return invalid
    layers = raw.get("layers")
    if type(layers) is not dict or type(layers.get("trading_days")) is not list:
        return invalid
    rows = layers["trading_days"]
    if len(rows) > 1000:
        return invalid
    counts = {}
    try:
        for row in rows:
            if type(row) is not dict or row.get("venue") != venue:
                return invalid
            day = _date(row.get("day"))
            if start <= day <= end:
                counts[day] = counts.get(day, 0) + 1
    except (TypeError, ValueError, OverflowError):
        return invalid
    missing = (end - start).days + 1 - len(counts)
    duplicates = sum(count > 1 for count in counts.values())
    status = (
        "MISSING_AND_DUPLICATE_DATES"
        if missing and duplicates
        else "MISSING_DATES"
        if missing
        else "DUPLICATE_DATES"
        if duplicates
        else "DECLARED_DATES_COMPLETE_UNVERIFIED"
    )
    return {
        "date_status": status,
        "missing_date_count": missing,
        "duplicate_date_count": duplicates,
    }


def _source_checks(texts, source_ids, *, legacy=False):
    """Check known raw-page digests without authenticating their origin.

    A string can reproduce raw bytes only when the caller preserved the exact
    UTF-8 text, including a possible BOM. Failed reconstruction is a mismatch
    or lossy encoding; it is never upgraded to a match.
    """
    checks = []
    valid_mapping = (
        type(texts) is dict and len(texts) == len(source_ids) and set(texts) == source_ids
    )
    for source_id in sorted(source_ids):
        value = texts.get(source_id) if type(texts) is dict else None
        try:
            valid = (
                type(value) is str
                and bool(value.strip())
                and len(value.encode("utf-8", "strict")) <= 2_000_000
                and _HASH_ONLY.fullmatch(value.strip()) is None
            )
            if not valid:
                status = "TEXT_MISSING_OR_INVALID"
            else:
                source = (_LEGACY_SOURCE if legacy else _KNOWN_SOURCE).fullmatch(source_id)
                if source is None:
                    status = "UNBOUND"
                elif hashlib.sha256(value.encode("utf-8", "strict")).hexdigest() == source.group(1):
                    status = "MATCH_UNAUTHENTICATED"
                else:
                    status = "MISMATCH_OR_LOSSY_ENCODING"
        except UnicodeError:
            status = "TEXT_MISSING_OR_INVALID"
        checks.append({"source_id": source_id, "status": status})
    return valid_mapping and all(
        item["status"] != "TEXT_MISSING_OR_INVALID" for item in checks
    ), checks


def _evaluate(input_data, candidate_id, *, schema=_SCHEMA):
    from bt_api_ctp.calendar_auto_candidate import build_auto_candidate
    from bt_api_ctp.calendar_candidate_coverage import CoverageRequirement

    from bt_api_py.trading_calendar_composite import SIMULATION_ONLY
    from bt_api_py.trading_calendar_sharded_validation import (
        VenueShard,
        validate_and_compose_simulated,
    )

    report = {
        "status": "PARTIAL",
        "reason": "INVALID_OR_INCOMPLETE_INPUT",
        "checker_mode": SIMULATION_ONLY,
        "checker_status": "NOT_RUN",
        "checker_reason": "NOT_RUN",
        "shards": [],
        "shard_inputs": [],
        "source_text_status": "MISSING_OR_INCOMPLETE",
        "source_bindings": [],
        "unresolved_status": "MISSING_OR_UNRESOLVED",
        "scope": "CALLER_DECLARED_ONLY",
        "source_authentication": "NOT_PROVEN",
        "coverage_authentication": "NOT_PROVEN",
        "publication": "NOT_AUTHORIZED",
        "composite": None,
    }
    if schema not in {_SCHEMA_V1, _SCHEMA}:
        raise ValueError("unsupported bundle schema")
    body = {"schema": schema, "candidate_id": candidate_id, "input": input_data, "report": report}
    if type(input_data) is not dict or type(input_data.get("shards")) is not list:
        return body
    if set(input_data) - {"shards", "source_texts", "metadata"}:
        return body
    raw_shards = input_data["shards"]
    if (
        len(raw_shards) != 6
        or any(type(s) is not dict or type(s.get("venue")) is not str for s in raw_shards)
        or {s["venue"] for s in raw_shards} != _VENUES
    ):
        return body
    shards = []
    source_ids = set()
    unresolved_ok = True
    all_kinds = True
    invalid = False
    for raw in raw_shards:
        entry = {
            "venue": raw.get("venue") if type(raw) is dict else None,
            "status": "INVALID_OR_INCOMPLETE",
            "date_status": "INVALID_DATE_LAYER",
            "missing_date_count": None,
            "duplicate_date_count": None,
        }
        report["shard_inputs"].append(entry)
        try:
            if type(raw) is not dict or type(raw.get("requirements")) is not list:
                raise ValueError("missing shard")
            if set(raw) - {
                "venue",
                "coverage_start",
                "coverage_end",
                "layers",
                "requirements",
                "unresolved",
                "provenance",
            }:
                raise ValueError("unrecognized shard information")
            venue = raw["venue"]
            start, end = _date(raw["coverage_start"]), _date(raw["coverage_end"])
            entry.update(_date_check(raw, venue, start, end))
            if type(raw.get("unresolved")) is not list or raw["unresolved"]:
                unresolved_ok = False
            reqs = []
            for row in raw["requirements"]:
                if type(row) is not dict or set(row) != {
                    "kind",
                    "product",
                    "contract",
                    "start_day",
                    "end_day",
                }:
                    raise ValueError("requirement")
                reqs.append(
                    CoverageRequirement(
                        venue,
                        row["kind"],
                        row["product"],
                        row["contract"],
                        _date(row["start_day"]),
                        _date(row["end_day"]),
                    )
                )
            all_kinds &= {r.kind for r in reqs} == {"future", "option"}
            built = build_auto_candidate(
                venue=venue, coverage_start=start, coverage_end=end, layers=raw["layers"]
            )
            if built.status != "CANDIDATE" or built.candidate is None:
                raise ValueError("candidate")
            source_ids.update(s["id"] for s in built.candidate.content["sources"])
            shards.append(VenueShard(venue, start, end, built.candidate, tuple(reqs)))
            entry["status"] = "CAPTURED_UNVERIFIED"
        except (KeyError, TypeError, ValueError, OverflowError):
            invalid = True
    if invalid:
        return body
    report["unresolved_status"] = "NONE_DECLARED" if unresolved_ok else "MISSING_OR_UNRESOLVED"
    texts = input_data.get("source_texts")
    texts_ok, source_bindings = _source_checks(texts, source_ids, legacy=schema == _SCHEMA_V1)
    report["source_bindings"] = source_bindings
    report["source_text_status"] = (
        "DECLARED_UNAUTHENTICATED" if texts_ok else "MISSING_OR_INCOMPLETE"
    )
    # Apply the same bound used by full comparison before CTP's per-scope or
    # combined snapshot constructors run. Old retained bundles exceeding this
    # bound no longer revalidate and must remain UNKNOWN on show.
    try:
        from bt_api_py.trading_calendar_snapshot_budget import check_snapshot_budget

        combined = {
            layer: [row for shard in shards for row in shard.candidate.content[layer]]
            for layer in ("trading_days", "trading_hours", "exceptions")
        }
        check_snapshot_budget(combined, {shard.venue for shard in shards})
    except Exception:
        report["reason"] = "SNAPSHOT_BUDGET_UNAVAILABLE"
        return body
    checked = validate_and_compose_simulated(mode=SIMULATION_ONLY, shards=tuple(shards))
    report["checker_status"] = checked.status
    report["checker_reason"] = checked.reason
    report["shards"] = [_jsonable(asdict(item)) for item in checked.shards]
    result = checked.composite
    if result is not None:
        report["composite"] = {
            "snapshot": json.loads(result.snapshot_bytes),
            "snapshot_sha256": result.snapshot_sha256,
            "manifest": json.loads(result.manifest_bytes),
            "manifest_sha256": result.manifest_sha256,
            "binding": json.loads(result.binding_bytes),
            "binding_sha256": result.binding_sha256,
        }
    no_mismatch = all(item["status"] != "MISMATCH_OR_LOSSY_ENCODING" for item in source_bindings)
    if (
        checked.status == "READY_SIMULATED"
        and unresolved_ok
        and texts_ok
        and all_kinds
        and no_mismatch
    ):
        report["status"] = "STRUCTURALLY_READY_UNVERIFIED"
        report["reason"] = "DECLARED_SCOPES_CHECKED_SIMULATED"
    else:
        report["reason"] = "DECLARATIONS_OR_SIMULATED_CHECK_INCOMPLETE"
    return body


def _open(root, create):
    fds, chain = _open_directory(root)
    parent = fds[-1]
    try:
        if create:
            with contextlib.suppress(FileExistsError):
                os.mkdir("review-bundles", 0o700, dir_fd=parent)
        info = os.stat("review-bundles", dir_fd=parent, follow_symlinks=False)
        if not stat.S_ISDIR(info.st_mode) or info.st_mode & 0o077 or info.st_uid != os.geteuid():
            raise ValueError("invalid directory")
        fd = os.open("review-bundles", os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=parent)
        fds.append(fd)
        if _identity(os.fstat(fd)) != _identity(info):
            raise ValueError("directory changed")
        chain.append((parent, "review-bundles", fd, _identity(info)))
        if not _directory_matches(chain):
            raise ValueError("directory changed")
        return fds, chain
    except BaseException:
        runs._close(fds)
        raise


def _valid(root, filesystem, candidate_id):
    return (
        filesystem == TRUSTED_LOCAL_POSIX
        and _supported()
        and detail._safe_root(root)
        and runs._id(candidate_id)
    )


def _merge_day_sources(rows, incoming):
    """Union references only for a single exactly compatible day record."""
    fields = {"venue", "day", "status", "source_ids"}
    if type(incoming) is not dict or set(incoming) != fields:
        return False
    matches = [
        row
        for row in rows
        if type(row) is dict
        and row.get("venue") == incoming["venue"]
        and row.get("day") == incoming["day"]
    ]
    if len(matches) != 1 or set(matches[0]) != fields:
        return False
    existing = matches[0]
    if existing["status"] != incoming["status"]:
        return False
    for value in (existing["source_ids"], incoming["source_ids"]):
        if (
            type(value) is not list
            or not value
            or any(type(ref) is not str for ref in value)
            or len(set(value)) != len(value)
        ):
            return False
    existing["source_ids"] = list(dict.fromkeys(existing["source_ids"] + incoming["source_ids"]))
    return True


def _evaluate_references(root, filesystem, candidate_id, caller_input, candidate_ids):
    """Rebuild an ordinary bundle from exact, revalidated local candidates."""
    from bt_api_py import trading_calendar_hours_evidence_candidate as hours

    if (
        type(candidate_ids) is not list
        or not 1 <= len(candidate_ids) <= 2
        or any(not runs._id(item) for item in candidate_ids)
        or len(set(candidate_ids)) != len(candidate_ids)
    ):
        raise ValueError("invalid selection")
    merged = json.loads(storage._json(caller_input), object_pairs_hook=runs._unique_pairs)
    if type(merged) is not dict or type(merged.get("shards")) is not list:
        raise ValueError("missing shards")
    bindings = []
    venues = set()
    for item in candidate_ids:
        result = hours.show(
            root=root, candidate_id=item, filesystem=filesystem, include_source_text=True
        )
        if result.get("status") != "VISIBLE_UNAUTHENTICATED":
            raise ValueError("candidate unavailable")
        source = result["candidate"]
        venue = source.get("exchange", "SHFE")
        if venue not in {"SHFE", "INE"} or venue in venues:
            raise ValueError("duplicate venue")
        venues.add(venue)
        targets = [s for s in merged["shards"] if type(s) is dict and s.get("venue") == venue]
        if len(targets) != 1 or type(targets[0].get("layers")) is not dict:
            raise ValueError("missing selected venue")
        shard = targets[0]
        layers = shard["layers"]
        for name in ("sources", "trading_days", "trading_hours", "exceptions"):
            if type(layers.get(name)) is not list or type(source["content"].get(name)) is not list:
                raise ValueError("missing selected layer")
            for row in source["content"][name]:
                if name == "trading_days" and _merge_day_sources(layers[name], row):
                    continue
                if row not in layers[name]:
                    layers[name].append(row)
        # Retain every unresolved declaration, raw note and source binding.
        # Missing caller declarations stay explicitly unresolved.
        if type(shard.get("unresolved")) is not list:
            shard["unresolved"] = [{"caller_unresolved": "MISSING_OR_INVALID"}]
        shard["unresolved"].append(
            {
                "candidate_id": item,
                "payload_sha256": result["payload_sha256"],
                "holiday_unresolved_nights": source["holiday_unresolved_nights"],
                "hours_unresolved_nights": source["hours_unresolved_nights"],
                "remaining_checks": source["remaining_checks"],
                "page_notes": source["page_notes"],
                "page_review_reasons": source["page_review_reasons"],
            }
        )
        texts = merged.get("source_texts")
        if type(texts) is dict:
            for row in source["content"]["sources"]:
                sid = row["id"]
                match = _KNOWN_SOURCE.fullmatch(sid)
                if not match:
                    continue
                text = (
                    result.get("hours_source_text")
                    if match.group(1) == source["hours_source_sha256"]
                    else result.get("holiday_source_text")
                    if match.group(1) == source["holiday_source_sha256"]
                    else None
                )
                if type(text) is str and sid not in texts:
                    texts[sid] = text
        bindings.append({"candidate_id": item, "payload_sha256": result["payload_sha256"]})
    _bounded(merged)
    body = _evaluate(merged, candidate_id)
    body["schema"] = _SCHEMA_REFERENCES
    body["assembly"] = {"caller_input": caller_input, "hours_candidates": bindings}
    return body


def build_once(
    *,
    root,
    candidate_id,
    input_data,
    filesystem,
    emit,
    hours_candidate_ids=None,
    propagate_interrupt=False,
):
    """Retain a candidate; optionally propagate interrupts with the original ID and outcome."""
    if (
        not _valid(root, filesystem, candidate_id)
        or not callable(emit)
        or type(propagate_interrupt) is not bool
    ):
        return {"status": "NOT_COMMITTED", "reason": "INVALID_ARGUMENTS", "candidate_id": ""}
    try:
        emit({"event": "REVIEW_BUNDLE_ID_ALLOCATED", "candidate_id": candidate_id})
        _bounded(input_data)
        detached = json.loads(storage._json(input_data), object_pairs_hook=runs._unique_pairs)
        if hours_candidate_ids is not None:
            if type(hours_candidate_ids) is not list:
                raise ValueError("invalid selection")
            hours_candidate_ids = list(hours_candidate_ids)
        if detail._sensitive(detached, root):
            raise ValueError("sensitive input")
        body = (
            _evaluate(detached, candidate_id)
            if hours_candidate_ids is None
            else _evaluate_references(root, filesystem, candidate_id, detached, hours_candidate_ids)
        )
        if detail._sensitive(body, root):
            raise ValueError("sensitive input")
        encoded = storage._envelope(body)
        if len(encoded) > _OUTPUT_LIMIT:
            raise ValueError("bundle limit")
        fds, chain = _open(root, True)
    except (KeyboardInterrupt, SystemExit) as exc:
        if propagate_interrupt:
            exc.calendar_outcome = {"status": "NOT_COMMITTED", "candidate_id": candidate_id}
            raise
        if isinstance(exc, SystemExit):
            raise
        return {"status": "NOT_COMMITTED", "reason": "INTERRUPTED", "candidate_id": candidate_id}
    except Exception:
        return {
            "status": "NOT_COMMITTED",
            "reason": "INPUT_UNAVAILABLE",
            "candidate_id": candidate_id,
        }
    lock = None
    try:
        lock, lock_id = storage._lock(fds[-1], chain)
        if hours_candidate_ids is not None:
            try:
                unchanged = (
                    _evaluate_references(
                        root, filesystem, candidate_id, detached, hours_candidate_ids
                    )
                    == body
                )
            except Exception:
                unchanged = False
            if not unchanged:
                return {
                    "status": "NOT_COMMITTED",
                    "reason": "SOURCE_CHANGED",
                    "candidate_id": candidate_id,
                }
        storage._check_lock(fds[-1], chain, lock, lock_id)
        status = storage._write(
            fds[-1], chain, f"candidate-{candidate_id}.json", encoded, _OUTPUT_LIMIT
        )
        return {
            "status": status,
            "candidate_id": candidate_id,
            "review_status": body["report"]["status"],
        }
    except (KeyboardInterrupt, SystemExit) as exc:
        if propagate_interrupt:
            exc.calendar_outcome = {"status": "COMMIT_UNKNOWN", "candidate_id": candidate_id}
            raise
        if isinstance(exc, SystemExit):
            raise
        return {
            "status": "COMMIT_UNKNOWN",
            "reason": "INTERRUPTED_OR_IO_UNKNOWN",
            "candidate_id": candidate_id,
        }
    except Exception:
        return {
            "status": "COMMIT_UNKNOWN",
            "reason": "INTERRUPTED_OR_IO_UNKNOWN",
            "candidate_id": candidate_id,
        }
    finally:
        if lock is not None:
            with contextlib.suppress(OSError):
                fcntl.flock(lock, fcntl.LOCK_UN)
            with contextlib.suppress(OSError):
                os.close(lock)
        runs._close(fds)


def build_derived_once(
    *,
    root,
    candidate_id,
    parent_id,
    parent_sha256,
    supplement,
    source_texts,
    decisions,
    filesystem,
    emit,
    classify_conflicts=False,
):
    """Retain a derived review from a captured, CTP-validated supplement.

    The caller owns YAML file reading; this function stores a normalized copy,
    rechecks the /2 or /3 parent and revalidates inputs before the no-overwrite write.
    It never authenticates a source or authorizes publication.

    ``supplement`` is the four-layer content returned by the CTP supplement
    reader. ``source_texts`` maps new source IDs to public text, as in build.
    Each entry in ``decisions`` has exactly ``candidate_id``, ``category``,
    ``index``, ``reason``, ``source_ids``, and ``scopes``. Candidate ID and
    index refer to the original /3 evidence item. Categories are
    ``holiday_unresolved_nights``, ``hours_unresolved_nights``, ``page_notes``,
    and ``page_review_reasons``. For a holiday item each scope specifies
    ``venue``, ``kind``, ``product``, ``contracts``, ``valid_from``,
    ``valid_to``, and an explicit ``owner_day``. Other categories use an empty
    scopes list. Decision text and source references do not prove source truth.
    Missing, incomplete or unassessed decisions remain unresolved. An uncertain
    write must be inspected under this same candidate ID.
    Callers must not mutate the objects during capture; the repeated pre-write
    observation detects changes but cannot exclude change-and-revert races.

    Additional support is accepted only as a JSON source text with keys
    ``schema``, ``night_ownership``, ``night_windows``, and optional
    ``exceptions``; the schema value is ``bt_api.calendar-review-support/1``
    and the remaining values are lists.
    An ownership record contains the scope fields, ``owner_day``, ``local_date``
    and ``start_date: null``. A window record contains the scope fields plus
    ``phase``, ``start``, ``end`` and ``end_day_offset``. The relevant source
    ID must also occur on the actual supplement mapping or time template.
    An exception record has ``venue``, ``kind``, ``product``, ``contract``,
    ``trading_day``, ``action`` and ``window_id``; each supplemental exception
    source must explicitly contain the exact applicable exception record.
    A product-wide exception (``contract: null``) cannot currently be justified
    by the original hours-page note alone and remains unresolved; the note is
    not extended to undeclared contracts.
    All sources referenced by the relevant template, mapping or candidate
    exceptions are checked together, including those omitted from the decision.
    Every support fact must match a sourced candidate fact within declared
    coverage; unknown scope and missing source associations remain unresolved.
    Duplicate facts within one document, conflicting ownership, overlapping
    windows and omitted source facts remain unresolved. Identical facts across
    different source documents are merged with their source associations.
    Except for the exact source already bound to the original page, every
    selected support source must be parseable under this schema; a source ID
    that merely resembles an original digest is not treated as that page.
    This exact parsed representation is a source declaration, not proof of its
    origin or truth. Only explicitly recognized notes can be reconciled to
    data; all other notes and unrecognized review reasons remain unresolved.
    """
    from bt_api_py.trading_calendar_review_supplement import (
        SupplementConflictError,
        evaluate_derived,
    )

    if (
        not _valid(root, filesystem, candidate_id)
        or not _valid(root, filesystem, parent_id)
        or not callable(emit)
    ):
        return {"status": "NOT_COMMITTED", "reason": "INVALID_ARGUMENTS", "candidate_id": ""}
    try:
        emit({"event": "REVIEW_BUNDLE_ID_ALLOCATED", "candidate_id": candidate_id})
        for value in (supplement, source_texts, decisions):
            _bounded(value)
        detached = json.loads(
            storage._json([supplement, source_texts, decisions]),
            object_pairs_hook=runs._unique_pairs,
        )
        if detail._sensitive(detached, root):
            raise ValueError("sensitive input")
        body = evaluate_derived(
            root=root,
            filesystem=filesystem,
            candidate_id=candidate_id,
            parent_id=parent_id,
            parent_sha256=parent_sha256,
            supplement=detached[0],
            source_texts=detached[1],
            decisions=detached[2],
        )
        # Apply the same combined-input limit as show before publishing a file.
        _bounded(body.get("input"))
        if detail._sensitive(body, root):
            raise ValueError("sensitive input")
        encoded = storage._envelope(body)
        if len(encoded) > _OUTPUT_LIMIT:
            raise ValueError("bundle limit")
        fds, chain = _open(root, True)
    except SupplementConflictError as exc:
        if classify_conflicts is not True:
            return {
                "status": "NOT_COMMITTED",
                "reason": "INPUT_UNAVAILABLE",
                "candidate_id": candidate_id,
            }
        return {
            "status": "NOT_COMMITTED",
            "reason": "SUPPLEMENT_CONFLICT",
            "conflict_kind": exc.code,
            "candidate_id": candidate_id,
        }
    except (Exception, KeyboardInterrupt):
        return {
            "status": "NOT_COMMITTED",
            "reason": "INPUT_UNAVAILABLE",
            "candidate_id": candidate_id,
        }
    lock = None
    try:
        lock, lock_id = storage._lock(fds[-1], chain)
        try:
            for value in (supplement, source_texts, decisions):
                _bounded(value)
            unchanged = (
                json.loads(
                    storage._json([supplement, source_texts, decisions]),
                    object_pairs_hook=runs._unique_pairs,
                )
                == detached
            )
            if unchanged:
                rechecked = evaluate_derived(
                    root=root,
                    filesystem=filesystem,
                    candidate_id=candidate_id,
                    parent_id=parent_id,
                    parent_sha256=parent_sha256,
                    supplement=detached[0],
                    source_texts=detached[1],
                    decisions=detached[2],
                )
                _bounded(rechecked.get("input"))
                unchanged = rechecked == body
        except Exception:
            unchanged = False
        if not unchanged:
            return {
                "status": "NOT_COMMITTED",
                "reason": "SOURCE_CHANGED",
                "candidate_id": candidate_id,
            }
        storage._check_lock(fds[-1], chain, lock, lock_id)
        status = storage._write(
            fds[-1], chain, f"candidate-{candidate_id}.json", encoded, _OUTPUT_LIMIT
        )
        return {
            "status": status,
            "candidate_id": candidate_id,
            "review_status": body["report"]["status"],
        }
    except (Exception, KeyboardInterrupt):
        return {
            "status": "COMMIT_UNKNOWN",
            "reason": "INTERRUPTED_OR_IO_UNKNOWN",
            "candidate_id": candidate_id,
        }
    finally:
        if lock is not None:
            with contextlib.suppress(OSError):
                fcntl.flock(lock, fcntl.LOCK_UN)
            with contextlib.suppress(OSError):
                os.close(lock)
        runs._close(fds)


def show(*, root, candidate_id, filesystem):
    """Recompute the retained bundle and return its full public-source input.

    Callers must treat its content as untrusted; root filtering is not a
    general detector for secrets or local paths.
    """
    unknown = {
        "status": "UNKNOWN",
        "reason": "UNAVAILABLE_OR_CHANGED",
        "candidate_id": candidate_id if runs._id(candidate_id) else "",
    }
    if not _valid(root, filesystem, candidate_id):
        return unknown
    try:
        fds, chain = _open(root, False)
    except Exception:
        return unknown
    try:
        raw = storage._read(fds[-1], chain, f"candidate-{candidate_id}.json", _OUTPUT_LIMIT)
        if raw is None:
            return unknown
        body, digest = storage._unwrap(raw)
        if (
            body.get("schema") not in {_SCHEMA_V1, _SCHEMA, _SCHEMA_REFERENCES, _SCHEMA_DERIVED}
            or body.get("candidate_id") != candidate_id
        ):
            return unknown
        _bounded(body.get("input"))
        if body["schema"] == _SCHEMA_DERIVED:
            from bt_api_py.trading_calendar_review_supplement import evaluate_derived

            assembly = body["assembly"]
            parent = assembly["parent"]
            expected = evaluate_derived(
                root=root,
                filesystem=filesystem,
                candidate_id=candidate_id,
                parent_id=parent["candidate_id"],
                parent_sha256=parent["payload_sha256"],
                supplement=assembly["supplement"],
                source_texts=assembly["supplement_source_texts"],
                decisions=assembly["decisions"],
            )
            if (
                expected != body
                or evaluate_derived(
                    root=root,
                    filesystem=filesystem,
                    candidate_id=candidate_id,
                    parent_id=parent["candidate_id"],
                    parent_sha256=parent["payload_sha256"],
                    supplement=assembly["supplement"],
                    source_texts=assembly["supplement_source_texts"],
                    decisions=assembly["decisions"],
                )
                != body
            ):
                return unknown
        elif body["schema"] == _SCHEMA_REFERENCES:
            assembly = body["assembly"]
            if type(assembly) is not dict or set(assembly) != {"caller_input", "hours_candidates"}:
                return unknown
            refs = assembly["hours_candidates"]
            if type(refs) is not list or any(
                type(ref) is not dict or set(ref) != {"candidate_id", "payload_sha256"}
                for ref in refs
            ):
                return unknown
            ids = [ref["candidate_id"] for ref in refs]
            expected = _evaluate_references(
                root, filesystem, candidate_id, assembly["caller_input"], ids
            )
            if (
                expected != body
                or _evaluate_references(
                    root,
                    filesystem,
                    candidate_id,
                    assembly["caller_input"],
                    ids,
                )
                != body
            ):
                return unknown
        else:
            expected = _evaluate(body["input"], candidate_id, schema=body["schema"])
        if detail._sensitive(body, root) or expected != body or not _directory_matches(chain):
            return unknown
        return {
            "status": "VISIBLE_UNAUTHENTICATED",
            "candidate_id": candidate_id,
            "payload_sha256": digest,
            "bundle": body,
        }
    except Exception:
        return unknown
    finally:
        runs._close(fds)


def _covers(manifest, venue, start, end):
    if type(manifest) is not dict or type(manifest.get("coverage")) is not list:
        return False
    rows = [row for row in manifest["coverage"] if type(row) is dict and row.get("venue") == venue]
    return len(rows) == 1 and _date(rows[0]["coverage_start"]) <= start <= end <= _date(
        rows[0]["coverage_end"]
    )


def _safe_change(change):
    """Only print schema-validated identifiers and fixed scalar fields."""
    if change.category not in {
        "trading_day",
        "applicability",
        "trading_session",
        "night_mapping",
        "exception",
        "source_basis",
    } or change.operation not in {"added", "removed", "modified"}:
        raise ValueError("invalid change")
    fields = {
        "status",
        "day",
        "venue",
        "kind",
        "product",
        "contracts",
        "valid_from",
        "valid_to",
        "windows_complete",
        "exceptions_complete",
        "id",
        "phase",
        "start",
        "end",
        "end_day_offset",
        "trading_day",
        "start_date",
        "action",
        "window_id",
        "contract",
        "source_ids",
        "source_reference_count",
    }

    def safe(value, depth=0):
        if depth > 6:
            raise ValueError("change depth")
        if value is None or type(value) is bool:
            return value
        if type(value) is int and -1 <= value <= 4096:
            return value
        if type(value) is str and _SAFE_CHANGE_STRING.fullmatch(value):
            return value
        if type(value) is list and len(value) <= 1024:
            return [safe(item, depth + 1) for item in value]
        if type(value) is dict and set(value) <= fields:
            output = {}
            for key, item in value.items():
                if key == "source_ids":
                    if type(item) is not list or len(item) > 4096:
                        raise ValueError("invalid source reference count")
                    output["source_reference_count"] = len(item)
                else:
                    output[key] = safe(item, depth + 1)
            return output
        raise ValueError("unsafe change value")

    def decoded(raw):
        if raw is None:
            return None
        if change.category == "source_basis":
            return {"source": "REDACTED"}
        value = json.loads(raw, object_pairs_hook=runs._unique_pairs)
        if type(value) is dict:
            value = {k: v for k, v in value.items() if k in fields}
        return safe(value)

    return {
        "category": change.category,
        "operation": change.operation,
        "key": "SOURCE_ID_REDACTED"
        if change.category == "source_basis"
        else safe(json.loads(change.key)),
        "before": decoded(change.before),
        "after": decoded(change.after),
    }


def compare_current(*, root, candidate_id, filesystem, request):
    """Compare one declared contract scope against a receipt-consistent CURRENT.

    Request contains candidate_payload_sha256, current_publication_id,
    current_publication_sha256, scope (venue/kind/product/contract/start_day/
    end_day), and separate exact baseline_source_texts and candidate_source_texts.
    Caller source data is unverified. The scope comparison is not a full-version
    diff, publication approval, or proof CURRENT will remain unchanged.
    """
    unknown = {
        "status": "UNKNOWN",
        "reason": "INVALID_OR_UNAVAILABLE",
        "candidate_id": candidate_id if runs._id(candidate_id) else "",
        "publication": "NOT_AUTHORIZED",
    }
    try:
        from bt_api_ctp.calendar_scope_projection import prepare_venue_scope
        from bt_api_ctp.calendar_semantic_diff import CalendarScope, compare_calendar_semantics

        from bt_api_py.trading_calendar_store import TradingCalendarStore

        if not _valid(root, filesystem, candidate_id):
            return unknown
        _bounded(request)
        if type(request) is not dict or set(request) != {
            "candidate_payload_sha256",
            "current_publication_id",
            "current_publication_sha256",
            "scope",
            "baseline_source_texts",
            "candidate_source_texts",
        }:
            return unknown
        candidate_sha = request["candidate_payload_sha256"]
        current_id, current_sha = (
            request["current_publication_id"],
            request["current_publication_sha256"],
        )
        if (
            type(candidate_sha) is not str
            or not _SHA256.fullmatch(candidate_sha)
            or type(current_sha) is not str
            or not _SHA256.fullmatch(current_sha)
            or type(current_id) is not str
            or not _SAFE_IDENTIFIER.fullmatch(current_id)
        ):
            return unknown
        raw_scope = request["scope"]
        if type(raw_scope) is not dict or set(raw_scope) != {
            "venue",
            "kind",
            "product",
            "contract",
            "start_day",
            "end_day",
        }:
            return unknown
        venue, kind, product, contract = (
            raw_scope[k] for k in ("venue", "kind", "product", "contract")
        )
        if (
            venue not in _VENUES
            or kind not in {"future", "option"}
            or any(
                type(v) is not str or not _SAFE_IDENTIFIER.fullmatch(v) for v in (product, contract)
            )
        ):
            return unknown
        start, end = _date(raw_scope["start_day"]), _date(raw_scope["end_day"])
        if start > end or (end - start).days + 1 > 731:
            return unknown
        first = show(root=root, candidate_id=candidate_id, filesystem=filesystem)
        if (
            first.get("status") != "VISIBLE_UNAUTHENTICATED"
            or first.get("payload_sha256") != candidate_sha
        ):
            return {**unknown, "reason": "CANDIDATE_UNAVAILABLE_OR_CHANGED"}
        body = first["bundle"]
        if body["report"]["status"] != "STRUCTURALLY_READY_UNVERIFIED":
            return {**unknown, "reason": "CANDIDATE_PARTIAL"}
        # Old /1 records are displayed using their historical validation rules.
        # A new comparison must apply today's source checks to the entire input,
        # including INE sources outside the selected scope.
        if body["schema"] == _SCHEMA_V1 and (
            _evaluate(body["input"], candidate_id, schema=_SCHEMA)["report"]["status"]
            != "STRUCTURALLY_READY_UNVERIFIED"
        ):
            return {**unknown, "reason": "CANDIDATE_STRICT_RECHECK_FAILED"}
        composite = body["report"]["composite"]
        if not _covers(composite["manifest"], venue, start, end):
            return {**unknown, "reason": "CANDIDATE_SCOPE_NOT_DECLARED"}
        shard = next(s for s in body["input"]["shards"] if s["venue"] == venue)
        if not any(
            r["kind"] == kind
            and r["product"] == product
            and r["contract"] == contract
            and _date(r["start_day"]) <= start <= end <= _date(r["end_day"])
            for r in shard["requirements"]
        ):
            return {**unknown, "reason": "CANDIDATE_SCOPE_NOT_DECLARED"}
        store = TradingCalendarStore(root)
        first_current = store.read_current_with_consistent_receipts().snapshot
        if (first_current.publication_id, first_current.publication_sha256) != (
            current_id,
            current_sha,
        ):
            return {**unknown, "reason": "CURRENT_IDENTITY_MISMATCH"}
        if (
            first_current.coverage_status != "DECLARED_UNVERIFIED"
            or first_current.coverage_manifest_bytes is None
        ):
            return {**unknown, "reason": "CURRENT_COVERAGE_NOT_DECLARED"}
        current_manifest = json.loads(
            first_current.coverage_manifest_bytes, object_pairs_hook=runs._unique_pairs
        )
        if not _covers(current_manifest, venue, start, end):
            return {**unknown, "reason": "CURRENT_COVERAGE_NOT_DECLARED"}
        scope = CalendarScope(venue, kind, product, contract)
        before = prepare_venue_scope(first_current.content, scope=scope, start=start, end=end)
        after = prepare_venue_scope(composite["snapshot"], scope=scope, start=start, end=end)
        if before.status != "READY_UNVERIFIED" or after.status != "READY_UNVERIFIED":
            return {**unknown, "reason": "SCOPE_INCOMPLETE_OR_OVER_BUDGET"}
        old_texts, new_texts = request["baseline_source_texts"], request["candidate_source_texts"]
        old_ok, old_bindings = _source_checks(old_texts, set(before.required_source_ids))
        new_ok, new_bindings = _source_checks(new_texts, set(after.required_source_ids))
        if (
            not old_ok
            or not new_ok
            or any(
                item["status"] == "MISMATCH_OR_LOSSY_ENCODING"
                for item in old_bindings + new_bindings
            )
        ):
            return {**unknown, "reason": "SOURCE_TEXT_MISSING_OR_MISMATCH"}
        if any(
            body["input"]["source_texts"].get(key) != new_texts[key]
            for key in after.required_source_ids
        ):
            return {**unknown, "reason": "CANDIDATE_SOURCE_TEXT_MISMATCH"}
        comparison = compare_calendar_semantics(
            before.content,
            after.content,
            scopes=(scope,),
            start=start,
            end=end,
            source_text_before=old_texts,
            source_text_after=new_texts,
        )
        changes = [_safe_change(item) for item in comparison.changes]
        if len(changes) > 10000 or len(storage._json(changes)) > 1_000_000:
            return {**unknown, "reason": "DIFFERENCE_OVER_BUDGET"}
        # Re-observe under each component's own lock; neither remains locked
        # after return, so this cannot be used as publication authorization.
        second_current = store.read_current_with_consistent_receipts().snapshot
        second = show(root=root, candidate_id=candidate_id, filesystem=filesystem)
        if (
            second_current != first_current
            or second.get("payload_sha256") != candidate_sha
            or second.get("status") != "VISIBLE_UNAUTHENTICATED"
        ):
            return {**unknown, "reason": "CURRENT_OR_CANDIDATE_CHANGED"}
        return {
            "status": comparison.status,
            "reason": comparison.reason,
            "candidate_id": candidate_id,
            "candidate_payload_sha256": candidate_sha,
            "current_publication_id": current_id,
            "current_publication_sha256": current_sha,
            "scope": raw_scope,
            "range_basis": "OWNER_TRADING_DAY",
            "difference_sha256": comparison.digest,
            "changes": changes,
            "baseline_manifest_sha256": first_current.coverage_manifest_sha256,
            "candidate_manifest_sha256": composite["manifest_sha256"],
            "manifest_comparison": "NOT_COMPARED",
            "other_scopes": "NOT_COMPARED",
            "cross_venue_relations": "NOT_CHECKED",
            "current_observation": "RECHECKED_UNDER_LOCK_NOT_PINNED",
            "source_binding_counts": {
                status: sum(item["status"] == status for item in old_bindings + new_bindings)
                for status in ("MATCH_UNAUTHENTICATED", "UNBOUND")
            },
            "source_authentication": "NOT_PROVEN",
            "source_identifiers": "REDACTED",
            "coverage_authentication": "NOT_PROVEN",
            "publication": "NOT_AUTHORIZED",
        }
    except (Exception, KeyboardInterrupt):
        return unknown


def _summary(result):
    """Avoid printing caller input, source text, paths, or individual contracts."""
    if result.get("status") != "VISIBLE_UNAUTHENTICATED":
        return result
    body = result["bundle"]
    report = body["report"]
    ranges = []
    input_data = body["input"]
    candidates = input_data.get("shards", []) if type(input_data) is dict else []
    if type(candidates) is not list:
        candidates = []
    for shard in candidates:
        try:
            venue = shard["venue"]
            start, end = _date(shard["coverage_start"]), _date(shard["coverage_end"])
            reqs = shard.get("requirements")
            if venue not in _VENUES or type(reqs) is not list:
                continue
            ranges.append(
                {
                    "venue": venue,
                    "start": start.isoformat(),
                    "end": end.isoformat(),
                    "declared_scope_count": len(reqs),
                }
            )
        except (KeyError, TypeError, ValueError, OverflowError):
            continue
    composite = report["composite"]
    return {
        "status": result["status"],
        "candidate_id": result["candidate_id"],
        "payload_sha256": result["payload_sha256"],
        "review_status": report["status"],
        "review_reason": report["reason"],
        "checker_mode": report["checker_mode"],
        "checker_status": report["checker_status"],
        "checker_reason": report["checker_reason"],
        "declared_ranges": ranges,
        "venue_date_checks": report["shard_inputs"],
        "source_text_status": report["source_text_status"],
        "source_binding_counts": {
            status: sum(item["status"] == status for item in report["source_bindings"])
            for status in (
                "MATCH_UNAUTHENTICATED",
                "MISMATCH_OR_LOSSY_ENCODING",
                "UNBOUND",
                "TEXT_MISSING_OR_INVALID",
            )
        },
        "unresolved_status": report["unresolved_status"],
        "snapshot_sha256": composite["snapshot_sha256"] if composite else None,
        "manifest_sha256": composite["manifest_sha256"] if composite else None,
        "binding_sha256": composite["binding_sha256"] if composite else None,
        "source_authentication": report["source_authentication"],
        "coverage_authentication": report["coverage_authentication"],
        "publication": report["publication"],
    }


class _Parser(argparse.ArgumentParser):
    def error(self, message):
        raise ValueError("invalid arguments")


def compare_full(
    *,
    root,
    candidate_id,
    filesystem,
    candidate_payload_sha256=None,
    current_publication_id=None,
    current_publication_sha256=None,
):
    """Return unredacted public declarations; not a publication authorization."""
    from bt_api_py.trading_calendar_full_diff import compare_full as compare

    return compare(
        root=root,
        candidate_id=candidate_id,
        filesystem=filesystem,
        candidate_payload_sha256=candidate_payload_sha256,
        current_publication_id=current_publication_id,
        current_publication_sha256=current_publication_sha256,
    )


def main(argv=None):
    parser = _Parser(description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True, parser_class=_Parser)
    for name in ("build", "show", "compare", "compare-full", "publication-show"):
        command = sub.add_parser(name)
        command.add_argument("--calendar-root", required=True)
        command.add_argument(
            "--publication-id" if name == "publication-show" else "--candidate-id", required=True
        )
        command.add_argument("--trusted-local-posix", action="store_true")
        if name == "build":
            command.add_argument("--hours-candidate-id", action="append")
        if name == "compare-full":
            command.add_argument("--candidate-payload-sha256")
            command.add_argument("--current-publication-id")
            command.add_argument("--current-publication-sha256")
            command.add_argument("--show-details", action="store_true")
    try:
        args = parser.parse_args(argv)
        identifier = (
            args.publication_id if args.command == "publication-show" else args.candidate_id
        )
        if args.command == "publication-show":
            from bt_api_py.trading_calendar_store import _identifier

            valid = (
                _supported() and detail._safe_root(args.calendar_root) and _identifier(identifier)
            )
        else:
            valid = _valid(args.calendar_root, TRUSTED_LOCAL_POSIX, identifier)
        if not args.trusted_local_posix or not valid:
            raise ValueError("invalid arguments")
        if args.command == "publication-show":
            from bt_api_py.trading_calendar_store import (
                CalendarReadBudget,
                TradingCalendarStore,
                _canonical,
            )

            try:
                evidence = TradingCalendarStore(
                    args.calendar_root
                ).read_publication_policy_readonly(
                    identifier, budget=CalendarReadBudget(96 * 1024 * 1024, 512 * 1024 * 1024, 256)
                )
                if evidence is None:
                    result = {"status": "UNKNOWN", "reason": "POLICY_NOT_PRESENT"}
                else:
                    difference = evidence["difference"]
                    result = {
                        "status": "VISIBLE_UNAUTHENTICATED",
                        "difference_sha256": evidence["difference_sha256"],
                        "difference_size_bytes": len(_canonical(difference)),
                        "snapshot_sha256": evidence["snapshot_sha256"],
                        "identity_authentication": "NOT_PROVEN",
                        "source_authentication": "NOT_PROVEN",
                        "candidate_and_difference_status": "CALLER_DECLARED_UNVERIFIED",
                        "manual_review_and_locked_recalculation": "NOT_IMPLEMENTED",
                    }
            except Exception:
                result = {"status": "UNKNOWN", "reason": "PUBLICATION_UNAVAILABLE"}
        elif args.command == "show":
            result = _summary(
                show(
                    root=args.calendar_root,
                    candidate_id=args.candidate_id,
                    filesystem=TRUSTED_LOCAL_POSIX,
                )
            )
        elif args.command == "build":
            raw = sys.stdin.buffer.read(_INPUT_LIMIT + 1)
            if len(raw) > _INPUT_LIMIT:
                raise ValueError("input limit")
            value = json.loads(raw, object_pairs_hook=runs._unique_pairs)
            result = build_once(
                root=args.calendar_root,
                candidate_id=args.candidate_id,
                input_data=value,
                filesystem=TRUSTED_LOCAL_POSIX,
                emit=lambda x: print(json.dumps(x, sort_keys=True), flush=True),
                hours_candidate_ids=args.hours_candidate_id,
            )
        elif args.command == "compare-full":
            from bt_api_py.trading_calendar_full_diff import cli_view

            result = compare_full(
                root=args.calendar_root,
                candidate_id=args.candidate_id,
                filesystem=TRUSTED_LOCAL_POSIX,
                candidate_payload_sha256=args.candidate_payload_sha256,
                current_publication_id=args.current_publication_id,
                current_publication_sha256=args.current_publication_sha256,
            )
            result = cli_view(result, args.calendar_root, show_details=args.show_details)
        else:
            raw = sys.stdin.buffer.read(_INPUT_LIMIT + 1)
            if len(raw) > _INPUT_LIMIT:
                raise ValueError("input limit")
            request = json.loads(raw, object_pairs_hook=runs._unique_pairs)
            result = compare_current(
                root=args.calendar_root,
                candidate_id=args.candidate_id,
                filesystem=TRUSTED_LOCAL_POSIX,
                request=request,
            )
        print(json.dumps(result, ensure_ascii=True, sort_keys=True))
        return (
            10
            if result["status"] in {"COMMITTED", "VISIBLE_UNAUTHENTICATED", "SAME", "CHANGED"}
            else 25
            if result["status"] in {"UNKNOWN", "COMMIT_UNKNOWN"}
            else 2
        )
    except KeyboardInterrupt:
        print('{"status":"NOT_COMMITTED","reason":"INTERRUPTED"}')
        return 130
    except Exception:
        print('{"status":"NOT_COMMITTED","reason":"INVALID_ARGUMENTS"}')
        return 2


if __name__ == "__main__":
    sys.exit(main())
