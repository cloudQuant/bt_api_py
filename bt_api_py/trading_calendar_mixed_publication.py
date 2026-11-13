"""Retain a replayable mixed simulation candidate; no account authority."""

from __future__ import annotations

import contextlib
import fcntl
import hashlib
import json
import os
import re
from copy import deepcopy
from pathlib import Path

from bt_api_py import trading_calendar_detail_collection as storage
from bt_api_py import trading_calendar_mixed_simulation_version as mixed
from bt_api_py import trading_calendar_observation_run as runs
from bt_api_py import trading_calendar_review_bundle as review
from bt_api_py.trading_calendar_store import (
    TradingCalendarStore,
    _canonical,
    _decode,
    _scope_key,
    _simulation_provenance,
    calendar_snapshot_digest,
)

_SCHEMA = "bt_api.calendar-mixed-candidate/1"
_MULTI_SCHEMA = "bt_api.calendar-mixed-candidate/2"
_ALIGNMENT = "REPEAT_OVER_MOCK_OWNER_DAY_MAPPING"


class MixedBaselineChangedError(ValueError):
    """The validated candidate's original CURRENT is no longer current."""


class RetainedScopeChangedError(ValueError):
    """The retained scope no longer matches its original evidence."""


def _body(root, candidate_id):
    fds, chain = review._open(root, False)
    try:
        raw = storage._read(fds[-1], chain, f"mock-mixed-{candidate_id}.json", review._OUTPUT_LIMIT)
    finally:
        runs._close(fds)
    if raw is None:
        raise ValueError("mixed evidence unavailable")
    body, _ = storage._unwrap(raw)
    if (
        type(body) is not dict
        or body.get("schema") not in {_SCHEMA, _MULTI_SCHEMA}
        or set(body)
        != {"schema", "environment", "candidate_id", "template", "evidence_sha256", "provenance"}
        | ({"baseline"} if body.get("schema") == _MULTI_SCHEMA else set())
        or body["environment"] != "SIMULATION_ONLY"
        or body["candidate_id"] != candidate_id
    ):
        raise ValueError("invalid mixed evidence")
    return body


def _materialize(root, filesystem, candidate_id, template, evidence_sha256):
    return mixed.materialize_template(
        template,
        root=root,
        candidate_root=root,
        filesystem=filesystem,
        alignment=_ALIGNMENT,
        expected_evidence_sha256=evidence_sha256,
        candidate_id=candidate_id,
    )


def _verify_scope(root, filesystem, scope):
    candidate_id = scope["evidence_locator"]["candidate_id"]
    body = _body(root, candidate_id)
    replayed = _materialize(
        root, filesystem, candidate_id, body["template"], body["evidence_sha256"]
    )
    if replayed["provenance"] != scope:
        raise ValueError("retained scope evidence changed")
    shown = review.show(root=root, candidate_id=candidate_id, filesystem=filesystem)
    if (
        shown.get("status") != "VISIBLE_UNAUTHENTICATED"
        or shown["bundle"]["report"].get("status") != "STRUCTURALLY_READY_UNVERIFIED"
    ):
        raise ValueError("retained candidate unavailable")
    _simulation_provenance(shown["bundle"]["report"]["composite"]["snapshot"], scope)
    parent_id = body["template"]["trace"]["candidate_id"]
    parent = review.show(root=root, candidate_id=parent_id, filesystem=filesystem)
    if parent.get("status") != "VISIBLE_UNAUTHENTICATED":
        raise ValueError("retained parent unavailable")
    return parent["bundle"]["input"]


def _scope_rows(data, target):
    shards = [shard for shard in data["shards"] if shard["venue"] == target["venue"]]
    if len(shards) != 1:
        raise ValueError("retained venue unavailable")
    shard = shards[0]
    rows = [
        row
        for row in shard["layers"]["trading_hours"]
        if row["venue"] == target["venue"]
        and row["kind"] == target["kind"]
        and row["product"] == target["product"]
        and row["contracts"] == [target["mock_contract"]]
        and row["valid_from"] == target["valid_from"]
        and row["valid_to"] == target["valid_to"]
    ]
    if len(rows) != 1:
        raise ValueError("retained scope unavailable")
    return shard, rows[0]


def _scope_facts(layers, target, coverage):
    """Extract all declarations that can affect one retained contract scope."""
    start, end = target["valid_from"], target["valid_to"]
    rows = [
        row
        for row in layers["trading_hours"]
        if row["venue"] == target["venue"]
        and row["kind"] == target["kind"]
        and row["product"] == target["product"]
        and row["contracts"] == [target["mock_contract"]]
        and row["valid_from"] == start
        and row["valid_to"] == end
    ]
    if len(rows) != 1:
        raise ValueError("scope row unavailable")
    row = rows[0]
    days = [
        item
        for item in layers["trading_days"]
        if item["venue"] == target["venue"] and start <= item["day"] <= end
    ]
    exceptions = [
        item
        for item in layers["exceptions"]
        if item["venue"] == target["venue"]
        and item["kind"] == target["kind"]
        and item["product"] == target["product"]
        and item["contract"] in (None, target["mock_contract"])
        and start <= item["trading_day"] <= end
    ]
    mappings = [item for item in row["night_mappings"] if start <= item["trading_day"] <= end]
    row_sources = sorted(
        source for source in row["source_ids"] if source != "AKSHARE.simulation_unverified"
    )
    used = set(row_sources)
    for item in days + exceptions + mappings:
        used.update(item["source_ids"])
    sources = [source for source in layers["sources"] if source["id"] in used]
    if len(sources) != len(used):
        raise ValueError("scope source unavailable")
    return {
        "trading_days": sorted(days, key=_canonical),
        "night_mappings": sorted(mappings, key=_canonical),
        "exceptions": sorted(exceptions, key=_canonical),
        "sources": sorted(sources, key=_canonical),
        "row_source_ids": row_sources,
        "coverage": coverage,
    }


def _input_scope_facts(data, target):
    shard, _ = _scope_rows(data, target)
    coverage = {
        "venue": target["venue"],
        "coverage_start": shard["coverage_start"],
        "coverage_end": shard["coverage_end"],
        "semantics": "DECLARED_INCLUSIVE",
    }
    return _scope_facts(shard["layers"], target, coverage)


def _scope_source_text_digests(data, facts):
    """Compare retained-scope materials without treating an ID as authentication."""
    texts = data.get("source_texts")
    if type(texts) is not dict:
        raise ValueError("source materials unavailable")
    result = {}
    for source in facts["sources"]:
        source_id = source["id"]
        value = texts.get(source_id)
        if type(value) is not str or not value:
            raise ValueError("source material unavailable")
        result[source_id] = hashlib.sha256(value.encode("utf-8", "strict")).hexdigest()
    return result


def _current_scope_facts(current, target):
    manifest = json.loads(current.coverage_manifest_bytes)
    coverage = [item for item in manifest["coverage"] if item["venue"] == target["venue"]]
    if len(coverage) != 1:
        raise ValueError("scope coverage unavailable")
    return _scope_facts(current.content, target, coverage[0])


def _combine(root, filesystem, materialized, current):
    prior = current.simulation_provenance
    if prior is None:
        return materialized
    scopes = (
        prior["scopes"]
        if prior["schema"] == "bt_api.calendar-mixed-scopes-provenance/1"
        else [prior]
    )
    new_scope = materialized["provenance"]
    new_target = new_scope["target"]
    retained = []
    result = deepcopy(materialized)
    for scope in scopes:
        original_parent = _verify_scope(root, filesystem, scope)
        target = scope["target"]
        if target == new_target:
            continue
        original_facts = _input_scope_facts(original_parent, target)
        if (
            _current_scope_facts(current, target) != original_facts
            or _input_scope_facts(result["input"], target) != original_facts
            or _scope_source_text_digests(result["input"], original_facts)
            != _scope_source_text_digests(original_parent, original_facts)
        ):
            raise RetainedScopeChangedError("retained scope facts changed")
        a, b = _scope_key(target), _scope_key(new_target)
        if a[:2] == b[:2] and a[3] == b[3] and a[4] <= b[5] and b[4] <= a[5]:
            raise ValueError("overlapping mixed targets")
        shard, row = _scope_rows(result["input"], target)
        prior_rows = [
            item
            for item in current.content["trading_hours"]
            if item["venue"] == target["venue"]
            and item["kind"] == target["kind"]
            and item["product"] == target["product"]
            and item["contracts"] == [target["mock_contract"]]
            and item["valid_from"] == target["valid_from"]
            and item["valid_to"] == target["valid_to"]
        ]
        if len(prior_rows) != 1:
            raise ValueError("retained scope row unavailable")
        old = prior_rows[0]
        ignored = {"day_windows", "night_windows", "source_ids"}
        if {k: v for k, v in row.items() if k not in ignored} != {
            k: v for k, v in old.items() if k not in ignored
        }:
            raise ValueError("retained scope mapping or coverage changed")
        row["day_windows"] = deepcopy(old["day_windows"])
        row["night_windows"] = deepcopy(old["night_windows"])
        row["source_ids"] = deepcopy(old["source_ids"])
        source_id = "AKSHARE.simulation_unverified"
        prior_source = next((s for s in current.content["sources"] if s["id"] == source_id), None)
        if prior_source != {
            "id": source_id,
            "description": "AKSHARE unverified observation SIMULATION_ONLY",
        }:
            raise ValueError("retained source unavailable")
        existing = [s for s in shard["layers"]["sources"] if s["id"] == source_id]
        if existing and existing != [prior_source]:
            raise ValueError("retained source conflict")
        if not existing:
            shard["layers"]["sources"].append(deepcopy(prior_source))
        result["input"]["source_texts"][source_id] = (
            "AKSHARE unverified observation SIMULATION_ONLY"
        )
        retained.append(deepcopy(scope))
    if not retained:
        return result
    all_scopes = sorted([*retained, new_scope], key=lambda item: _scope_key(item["target"]))
    semantic = [
        {"target": item["target"], "semantic_sha256": item["semantic_sha256"]}
        for item in all_scopes
    ]
    result["provenance"] = {
        "schema": "bt_api.calendar-mixed-scopes-provenance/1",
        "environment": "SIMULATION_ONLY",
        "scopes": all_scopes,
        "update_target": new_target,
        "semantic_sha256": hashlib.sha256(_canonical(semantic)).hexdigest(),
        "evidence_sha256": hashlib.sha256(_canonical(all_scopes)).hexdigest(),
    }
    return result


def build_once(*, root, filesystem, candidate_id, template, evidence_sha256):
    """Write once under a caller-preallocated ID; inspect the ID on uncertainty."""
    if not runs._id(candidate_id):
        return {"status": "NOT_COMMITTED", "reason": "INVALID_ID"}
    try:
        result = _materialize(root, filesystem, candidate_id, template, evidence_sha256)
        baseline = None
        if os.path.lexists(os.path.join(root, "CURRENT.json")):
            current = TradingCalendarStore(root).read_current()
            result = _combine(root, filesystem, result, current)
            if result["provenance"]["schema"] == "bt_api.calendar-mixed-scopes-provenance/1":
                baseline = {
                    "publication_id": current.publication_id,
                    "publication_sha256": current.publication_sha256,
                    "version_sha256": current.version_sha256,
                }
    except RetainedScopeChangedError:
        return {"status": "NOT_COMMITTED", "reason": "RETAINED_SCOPE_CHANGED"}
    except Exception:
        return {"status": "NOT_COMMITTED", "reason": "INVALID_MIXED_INPUT"}
    candidate = review.build_once(
        root=root,
        filesystem=filesystem,
        candidate_id=candidate_id,
        input_data=result["input"],
        emit=lambda _: None,
        propagate_interrupt=True,
    )
    if candidate.get("status") != "COMMITTED":
        return {"status": candidate.get("status", "UNKNOWN"), "reason": "CANDIDATE_RECONCILE_FIRST"}
    body = {
        "schema": _MULTI_SCHEMA if baseline is not None else _SCHEMA,
        "environment": "SIMULATION_ONLY",
        "candidate_id": candidate_id,
        "template": template,
        "evidence_sha256": evidence_sha256,
        "provenance": result["provenance"],
    }
    if baseline is not None:
        body["baseline"] = baseline
    attempted = False
    fds = []
    lock = None
    try:
        fds, chain = review._open(root, False)
        lock, identity = storage._lock(fds[-1], chain)
        storage._check_lock(fds[-1], chain, lock, identity)
        attempted = True
        state = storage._write(
            fds[-1],
            chain,
            f"mock-mixed-{candidate_id}.json",
            storage._envelope(body),
            review._OUTPUT_LIMIT,
        )
        return {"status": state, "candidate_state": "COMMITTED", "candidate_id": candidate_id}
    except (KeyboardInterrupt, SystemExit):
        raise
    except Exception:
        return {
            "status": "UNKNOWN" if attempted else "NOT_COMMITTED",
            "candidate_state": "COMMITTED",
            "reason": "MIXED_EVIDENCE_RECONCILE_FIRST",
        }
    finally:
        if lock is not None:
            with contextlib.suppress(OSError):
                fcntl.flock(lock, fcntl.LOCK_UN)
            with contextlib.suppress(OSError):
                os.close(lock)
        runs._close(fds)


def read(*, root, filesystem, candidate_id):
    """Read and fully replay retained inputs; raise on any unavailable proof."""
    if not runs._id(candidate_id):
        raise ValueError("invalid candidate")
    body = _body(root, candidate_id)
    materialized = _materialize(
        root, filesystem, candidate_id, body["template"], body["evidence_sha256"]
    )
    if body["schema"] == _MULTI_SCHEMA:
        current = TradingCalendarStore(root).read_current()
        if body["baseline"] != {
            "publication_id": current.publication_id,
            "publication_sha256": current.publication_sha256,
            "version_sha256": current.version_sha256,
        }:
            raise MixedBaselineChangedError("mixed baseline changed")
        materialized = _combine(root, filesystem, materialized, current)
    shown = review.show(root=root, candidate_id=candidate_id, filesystem=filesystem)
    if (
        shown.get("status") != "VISIBLE_UNAUTHENTICATED"
        or shown["bundle"]["input"] != materialized["input"]
        or body["provenance"] != materialized["provenance"]
    ):
        raise ValueError("mixed candidate or provenance changed")
    return shown, body["provenance"]


def read_history(*, root, filesystem, candidate_id, publication_id=None):
    """Replay a retained multi-scope candidate against its receipt-bound baseline.

    An optional publication ID additionally proves local receipt/chain binding;
    neither this evidence nor the reported sources are authenticated.
    """
    unknown = {
        "status": "UNKNOWN",
        "reason": "HISTORICAL_EVIDENCE_UNAVAILABLE",
        "candidate_id": candidate_id,
    }
    if not review._valid(root, filesystem, candidate_id) or (
        publication_id is not None
        and (
            type(publication_id) is not str
            or re.fullmatch(r"MOCK_[A-Za-z0-9_-]{1,123}", publication_id) is None
        )
    ):
        return unknown
    try:
        body = _body(root, candidate_id)
        if body["schema"] != _MULTI_SCHEMA:
            return {**unknown, "reason": "NOT_MULTI_SCOPE_CANDIDATE"}
        baseline = body["baseline"]
        if (
            type(baseline) is not dict
            or set(baseline) != {"publication_id", "publication_sha256", "version_sha256"}
            or type(baseline["publication_id"]) is not str
            or re.fullmatch(r"MOCK_[A-Za-z0-9_-]{1,123}", baseline["publication_id"]) is None
            or any(
                type(baseline[key]) is not str
                or re.fullmatch(r"[0-9a-f]{64}", baseline[key]) is None
                for key in ("publication_sha256", "version_sha256")
            )
        ):
            raise ValueError("invalid original baseline")
        store = TradingCalendarStore(root)
        total = 0

        def bounded(path):
            nonlocal total
            remaining = 128 * 1024 * 1024 - total
            if remaining <= 0:
                raise ValueError("history read budget")
            data = store._read_bounded(path, min(32 * 1024 * 1024, remaining))
            total += len(data)
            return data

        with store._readonly_lock():
            if store._pending_exists() or store._policy_change_pending():
                raise ValueError("store operation pending")
            pointer_bytes = bounded(Path(root) / "CURRENT.json")
            store._validate_chain_receipts(pointer_bytes, _reader=bounded, _max_publications=256)
            base_receipt = store._read_receipt(baseline["publication_id"], _reader=bounded)
            base_pointer = base_receipt["pointer"]
            if base_pointer["publication_sha256"] != baseline["publication_sha256"]:
                raise ValueError("baseline receipt changed")
            store._load(
                pointer_bytes,
                target=base_pointer,
                target_version=base_receipt["version_id"],
                _reader=bounded,
                _max_publications=256,
            )
            previous = store._load(_canonical(base_pointer), _reader=bounded, _max_publications=256)
            if previous.version_sha256 != baseline["version_sha256"]:
                raise ValueError("baseline version changed")
            materialized = _materialize(
                root, filesystem, candidate_id, body["template"], body["evidence_sha256"]
            )
            materialized = _combine(root, filesystem, materialized, previous)
            shown = review.show(root=root, candidate_id=candidate_id, filesystem=filesystem)
            if (
                shown.get("status") != "VISIBLE_UNAUTHENTICATED"
                or shown["bundle"]["input"] != materialized["input"]
                or body["provenance"] != materialized["provenance"]
            ):
                raise ValueError("historical candidate changed")
            publication_state = "NOT_CHECKED"
            if publication_id is not None:
                receipt = store._read_receipt(publication_id, _reader=bounded)
                pub_pointer = receipt["pointer"]
                store._load(
                    pointer_bytes,
                    target=pub_pointer,
                    target_version=receipt["version_id"],
                    _reader=bounded,
                    _max_publications=256,
                )
                published = store._load(
                    _canonical(pub_pointer),
                    target=base_pointer,
                    target_version=base_receipt["version_id"],
                    _reader=bounded,
                    _max_publications=256,
                )
                event = _decode(bounded(Path(root) / "publications" / f"{publication_id}.json"))
                composite = shown["bundle"]["report"]["composite"]
                snapshot_hash = calendar_snapshot_digest(
                    composite["snapshot"],
                    coverage_manifest_bytes=_canonical(composite["manifest"]),
                    coverage_manifest_sha256=composite["manifest_sha256"],
                    binding_bytes=_canonical(composite["binding"]),
                    binding_sha256=composite["binding_sha256"],
                    simulation_provenance=body["provenance"],
                )
                policy = event.get("review_policy") or {}
                difference = policy.get("difference") or {}
                before = {
                    "publication_id": previous.publication_id,
                    "publication_sha256": previous.publication_sha256,
                    "version_id": previous.version_id,
                    "version_sha256": previous.version_sha256,
                    "snapshot_sha256": previous.content_sha256,
                    "manifest_sha256": previous.coverage_manifest_sha256,
                    "binding_sha256": previous.binding_sha256,
                }
                if (
                    event.get("previous")
                    != {
                        "publication_id": baseline["publication_id"],
                        "publication_sha256": baseline["publication_sha256"],
                    }
                    or difference.get("schema") != "bt_api.calendar-full-declaration-difference/2"
                    or difference.get("before") != before
                    or difference.get("candidate_id") != candidate_id
                    or difference.get("candidate_payload_sha256") != shown["payload_sha256"]
                    or policy.get("snapshot_sha256") != snapshot_hash
                    or published.version_sha256 != snapshot_hash
                    or published.simulation_provenance != body["provenance"]
                    or published.content != composite["snapshot"]
                ):
                    raise ValueError("publication candidate mismatch")
                publication_state = "RECEIPT_CHAIN_BOUND_UNAUTHENTICATED"
            if (
                _body(root, candidate_id) != body
                or review.show(root=root, candidate_id=candidate_id, filesystem=filesystem) != shown
                or bounded(Path(root) / "CURRENT.json") != pointer_bytes
            ):
                raise ValueError("historical evidence changed")
            return {
                "status": "VISIBLE_UNAUTHENTICATED",
                "candidate_id": candidate_id,
                "candidate_payload_sha256": shown["payload_sha256"],
                "baseline": baseline,
                "simulation_provenance": body["provenance"],
                "publication_id": publication_id,
                "publication_consistency": publication_state,
                "source_authentication": "NOT_PROVEN",
                "environment": "SIMULATION_ONLY",
            }
    except Exception:
        return unknown
