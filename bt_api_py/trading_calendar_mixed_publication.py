"""Retain a replayable mixed simulation candidate; no account authority."""

from __future__ import annotations

import contextlib
import fcntl
import hashlib
import json
import math
import os
import re
from copy import deepcopy
from datetime import date
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
_BATCH_SCHEMA = "bt_api.calendar-mixed-candidate/3"
_ALIGNMENT = "REPEAT_OVER_MOCK_OWNER_DAY_MAPPING"
_DEFINITE_FAILURES = frozenset(
    {
        "EMPTY",
        "MISSING_TRADING_TIME",
        "STRUCTURE_CHANGED",
        "DEPENDENCY_UNAVAILABLE",
        "VERSION_MISMATCH",
        "FETCH_ERROR",
    }
)


def _failure_proof(root, run_id, row):
    """Reread the original request and result; hashes do not authenticate their source."""
    from bt_api_py import trading_calendar_akshare_observation as observation
    from bt_api_py import trading_calendar_mock_akshare as mock_akshare

    oid = row["observation_id"]
    fds, chain = observation._open(root, False)
    try:
        raw = storage._read(fds[-1], chain, f"intent-{oid}.json", observation._LIMIT)
        if raw is None:
            raise ValueError("observation intent unavailable")
        intent, intent_sha = storage._unwrap(raw)
    finally:
        runs._close(fds)
    if (
        not observation._valid_intent(intent, oid, run_id)
        or intent["venue"] != row["target"]["venue"]
        or intent["contract"] != row["contract"]
        or intent["requested_date"] != row["requested_date"]
        or not 0 < intent["timeout"] <= row["timeout"]
    ):
        raise ValueError("observation intent changed")
    fds, chain = runs._open_runs(root, create=False)
    try:
        raw = runs._read_entry(fds[-1], chain, f"mock-akshare-{oid}.json")
        if raw is None:
            raise ValueError("observation descriptor unavailable")
        descriptor, descriptor_sha = storage._unwrap(raw)
    finally:
        runs._close(fds)
    request = {
        "venue": row["target"]["venue"],
        "contract": row["contract"],
        "requested_date": date.fromisoformat(row["requested_date"]),
        "kind": "future",
        "timeout": row["timeout"],
    }
    if descriptor != mock_akshare._body(
        run_id=run_id, observation_id=oid, year=descriptor["mock_year"], request=request
    ):
        raise ValueError("observation descriptor changed")
    mock_akshare._guard(root, run_id, descriptor["mock_year"])
    observed = observation.show(
        root=root, observation_id=oid, run_id=run_id, filesystem=runs.TRUSTED_LOCAL_POSIX
    )
    if (
        observed.get("status") != "VISIBLE_UNAUTHENTICATED"
        or observed.get("observation_status") != row["observation_status"]
        or observed.get("observation_status") not in _DEFINITE_FAILURES
        or observed.get("transport_status") != "CHILD_OK"
        or observed.get("data_state") != "DATA_RETAINED"
        or observed.get("requested_venue") != intent["venue"]
        or observed.get("requested_contract") != intent["contract"]
        or observed.get("requested_date") != intent["requested_date"]
    ):
        raise ValueError("observation failure not established")
    return {
        "intent_sha256": intent_sha,
        "descriptor_sha256": descriptor_sha,
        "result_sha256": observed["payload_sha256"],
        "effective_timeout": intent["timeout"],
        "transport_status": observed["transport_status"],
        "data_state": observed["data_state"],
        "direct_child_cleanup": observed["direct_child_cleanup"],
        "timeout_retry": observed["timeout_retry"],
    }


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
        or body.get("schema") not in {_SCHEMA, _MULTI_SCHEMA, _BATCH_SCHEMA}
        or set(body)
        != (
            {
                "schema",
                "environment",
                "candidate_id",
                "templates",
                "provenance",
                "baseline",
                "batch_id",
                "batch_record_sha256",
            }
            if body.get("schema") == _BATCH_SCHEMA
            else {
                "schema",
                "environment",
                "candidate_id",
                "template",
                "evidence_sha256",
                "provenance",
            }
            | ({"baseline"} if body.get("schema") == _MULTI_SCHEMA else set())
        )
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
    if body["schema"] == _BATCH_SCHEMA:
        _batch_record(root, body["batch_id"], body["batch_record_sha256"])
        found = [
            item
            for item in body["templates"]
            if item["template"]["trace"]["observation_id"]
            == scope["evidence_locator"]["observation_id"]
        ]
        if len(found) != 1:
            raise ValueError("batch scope evidence unavailable")
        entry = found[0]
    else:
        entry = body
    replayed = _materialize(
        root, filesystem, candidate_id, entry["template"], entry["evidence_sha256"]
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
    parent_id = entry["template"]["trace"]["candidate_id"]
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


def _combine(
    root,
    filesystem,
    materialized,
    current,
    *,
    additional_scopes=(),
    batch_record_sha256=None,
    batch_resolutions=None,
):
    prior = current.simulation_provenance
    scopes = (
        prior["scopes"]
        if prior is not None
        and prior["schema"]
        in {
            "bt_api.calendar-mixed-scopes-provenance/1",
            "bt_api.calendar-mixed-scopes-provenance/2",
            "bt_api.calendar-mixed-scopes-provenance/3",
        }
        else [prior]
        if prior is not None
        else []
    )
    new_scope = materialized["provenance"]
    updates = [new_scope, *additional_scopes]
    targets = [item["target"] for item in updates]
    retained = []
    result = deepcopy(materialized)
    for scope in scopes:
        original_parent = _verify_scope(root, filesystem, scope)
        target = scope["target"]
        if target in targets:
            continue
        original_facts = _input_scope_facts(original_parent, target)
        if (
            _current_scope_facts(current, target) != original_facts
            or _input_scope_facts(result["input"], target) != original_facts
            or _scope_source_text_digests(result["input"], original_facts)
            != _scope_source_text_digests(original_parent, original_facts)
        ):
            raise RetainedScopeChangedError("retained scope facts changed")
        a = _scope_key(target)
        for new_target in targets:
            b = _scope_key(new_target)
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
    if not retained and batch_record_sha256 is None:
        return result
    all_scopes = sorted([*retained, *updates], key=lambda item: _scope_key(item["target"]))
    semantic = [
        {"target": item["target"], "semantic_sha256": item["semantic_sha256"]}
        for item in all_scopes
    ]
    result["provenance"] = {
        "schema": "bt_api.calendar-mixed-scopes-provenance/3"
        if batch_resolutions is not None
        else "bt_api.calendar-mixed-scopes-provenance/2"
        if batch_record_sha256 is not None
        else "bt_api.calendar-mixed-scopes-provenance/1",
        "environment": "SIMULATION_ONLY",
        "scopes": all_scopes,
        "semantic_sha256": hashlib.sha256(_canonical(semantic)).hexdigest(),
        "evidence_sha256": hashlib.sha256(_canonical(all_scopes)).hexdigest(),
    }
    if batch_record_sha256 is not None:
        result["provenance"].update(
            update_targets=sorted(targets, key=_scope_key), batch_record_sha256=batch_record_sha256
        )
        if batch_resolutions is not None:
            result["provenance"]["resolutions"] = batch_resolutions
    else:
        result["provenance"]["update_target"] = targets[0]
    return result


def _batch_intent(root, batch_id, expected_sha=None):
    if not runs._id(batch_id):
        raise ValueError("invalid batch ID")
    fds, chain = runs._open_runs(root, create=False)
    try:
        raw = runs._read_entry(fds[-1], chain, f"mock-daily-batch-intent-{batch_id}.json")
    finally:
        runs._close(fds)
    if raw is None:
        raise ValueError("batch intent unavailable")
    intent, digest = storage._unwrap(raw)
    if (
        (expected_sha is not None and digest != expected_sha)
        or type(intent) is not dict
        or set(intent)
        != {
            "schema",
            "environment",
            "source_authentication",
            "batch_id",
            "run_id",
            "original_run_id",
            "candidate_id",
            "derived_id",
            "capture_id",
            "parent_id",
            "review_id",
            "publication_id",
            "version_id",
            "observations",
            "requested_date",
            "date_role",
            "budget_seconds",
            "baseline",
            "policy",
        }
        or intent["schema"] != "bt_api.calendar-daily-batch-intent/1"
        or intent["environment"] != "SIMULATION_ONLY"
        or intent["source_authentication"] != "NOT_PROVEN"
        or intent["batch_id"] != batch_id
        or any(
            not runs._id(intent[key])
            for key in (
                "run_id",
                "original_run_id",
                "candidate_id",
                "derived_id",
                "capture_id",
                "parent_id",
                "review_id",
            )
        )
        or any(
            type(intent[key]) is not str
            or re.fullmatch(r"MOCK_[A-Za-z0-9_-]{1,123}", intent[key]) is None
            for key in ("publication_id", "version_id")
        )
        or type(intent["observations"]) is not list
        or not 2 <= len(intent["observations"]) <= 8
        or intent["date_role"] != "REQUEST_DATE_NOT_EFFECTIVE_DATE"
        or type(intent["budget_seconds"]) not in (int, float)
        or not math.isfinite(intent["budget_seconds"])
        or not 0 < intent["budget_seconds"] <= 600
        or type(intent["baseline"]) is not dict
        or type(intent["policy"]) is not dict
    ):
        raise ValueError("invalid batch intent")
    parsed = date.fromisoformat(intent["requested_date"])
    if parsed.isoformat() != intent["requested_date"]:
        raise ValueError("invalid batch request date")
    seen = set()
    for row in intent["observations"]:
        if (
            type(row) is not dict
            or set(row)
            != {"observation_id", "contract", "target", "assumptions", "alignment", "timeout"}
            or not runs._id(row["observation_id"])
            or row["observation_id"] in seen
            or row["alignment"] != _ALIGNMENT
        ):
            raise ValueError("invalid batch scope")
        seen.add(row["observation_id"])
        mixed._template_input(row["target"], row["assumptions"])
        from bt_api_py.trading_calendar_akshare_observation import _settings

        if not _settings(row["target"]["venue"], row["contract"], parsed, row["timeout"]):
            raise ValueError("invalid batch request")
    return intent, digest


def _batch_record(root, batch_id, expected_sha):
    if not runs._id(batch_id):
        raise ValueError("invalid batch ID")
    fds, chain = runs._open_runs(root, create=False)
    try:
        raw = runs._read_entry(fds[-1], chain, f"mock-daily-batch-{batch_id}.json")
    finally:
        runs._close(fds)
    if raw is None:
        raise ValueError("batch evidence unavailable")
    body, digest = storage._unwrap(raw)
    if (
        digest != expected_sha
        or type(body) is not dict
        or set(body)
        != {
            "schema",
            "environment",
            "source_authentication",
            "batch_id",
            "run_id",
            "candidate_id",
            "derived_id",
            "baseline",
            "policy",
            "intent_sha256",
            "supplement_sha256",
            "observations",
        }
        or body.get("schema")
        not in {"bt_api.calendar-daily-batch-evidence/1", "bt_api.calendar-daily-batch-evidence/2"}
        or body.get("environment") != "SIMULATION_ONLY"
        or body.get("source_authentication") != "NOT_PROVEN"
        or body.get("batch_id") != batch_id
        or any(not runs._id(body.get(key)) for key in ("run_id", "candidate_id", "derived_id"))
        or type(body.get("supplement_sha256")) is not str
        or re.fullmatch(r"[0-9a-f]{64}", body["supplement_sha256"]) is None
        or type(body.get("policy")) is not dict
        or type(body.get("observations")) is not list
    ):
        raise ValueError("batch evidence changed")
    intent, _ = _batch_intent(root, batch_id, body["intent_sha256"])
    if any(
        body[key] != intent[key]
        for key in ("batch_id", "run_id", "candidate_id", "derived_id", "baseline", "policy")
    ) or len(body["observations"]) != len(intent["observations"]):
        raise ValueError("batch intent identity mismatch")
    for actual, expected in zip(body["observations"], intent["observations"], strict=True):
        if (
            type(actual) is not dict
            or any(
                actual.get(key) != expected[key]
                for key in (
                    "observation_id",
                    "contract",
                    "target",
                    "assumptions",
                    "alignment",
                    "timeout",
                )
            )
            or actual.get("requested_date") != intent["requested_date"]
        ):
            raise ValueError("batch intent scope mismatch")
        if body["schema"] == "bt_api.calendar-daily-batch-evidence/2":
            if actual.get("check_status") == "FAILED":
                proof = _failure_proof(root, body["run_id"], actual)
                diagnostics = actual.get("observation_diagnostics")
                if (
                    proof != actual.get("failure_proof")
                    or type(diagnostics) is not dict
                    or any(
                        diagnostics.get(key) != proof[key]
                        for key in (
                            "transport_status",
                            "data_state",
                            "direct_child_cleanup",
                            "timeout_retry",
                        )
                    )
                    or diagnostics.get("automatic_retry") != "NOT_PROVEN_SAFE"
                ):
                    raise ValueError("failed observation changed")
            elif actual.get("failure_proof") is not None:
                raise ValueError("unexpected failure proof")
    return body


def _materialize_batch(root, filesystem, candidate_id, templates, current, batch_sha, record=None):
    partial = record is not None and record["schema"] == "bt_api.calendar-daily-batch-evidence/2"
    if type(templates) is not list or not (1 if partial else 2) <= len(templates) <= 8:
        raise ValueError("invalid batch templates")
    if any(
        type(item) is not dict or set(item) != {"template", "evidence_sha256"} for item in templates
    ):
        raise ValueError("invalid batch template")
    parents = {item["template"]["trace"]["candidate_id"] for item in templates}
    observations = {item["template"]["trace"]["observation_id"] for item in templates}
    if len(parents) != 1 or len(observations) != len(templates):
        raise ValueError("batch parent or observation mismatch")
    entries = [
        _materialize(root, filesystem, candidate_id, item["template"], item["evidence_sha256"])
        for item in templates
    ]
    targets = [item["provenance"]["target"] for item in entries]
    keys = [_scope_key(target) for target in targets]
    if len(set(keys)) != len(keys):
        raise ValueError("duplicate batch target")
    for i, left in enumerate(keys):
        for right in keys[i + 1 :]:
            if (
                left[:2] == right[:2]
                and left[3] == right[3]
                and left[4] <= right[5]
                and right[4] <= left[5]
            ):
                raise ValueError("overlapping batch target")
    result = deepcopy(entries[0])
    for entry in entries[1:]:
        target = entry["provenance"]["target"]
        source_shard, source_row = _scope_rows(entry["input"], target)
        target_shard, target_row = _scope_rows(result["input"], target)
        target_row.clear()
        target_row.update(deepcopy(source_row))
        for source in source_shard["layers"]["sources"]:
            existing = [
                item for item in target_shard["layers"]["sources"] if item["id"] == source["id"]
            ]
            if existing and existing != [source]:
                raise ValueError("batch source conflict")
            if not existing:
                target_shard["layers"]["sources"].append(deepcopy(source))
        for key, value in entry["input"]["source_texts"].items():
            if (
                key in result["input"]["source_texts"]
                and result["input"]["source_texts"][key] != value
            ):
                raise ValueError("batch source text conflict")
            result["input"]["source_texts"][key] = value
    resolutions = None
    if partial:
        prior = current.simulation_provenance
        prior_scopes = prior["scopes"] if prior and "scopes" in prior else [prior] if prior else []
        resolutions = []
        for row in record["observations"]:
            target = row["target"]
            disposition = row["disposition"]
            if disposition == "RETAINED_AKSHARE":
                if len([s for s in prior_scopes if s["target"] == target]) != 1:
                    raise ValueError("retained target unavailable")
            elif disposition == "MOCK_FALLBACK":
                a = _scope_key(target)
                for old in prior_scopes:
                    b = _scope_key(old["target"])
                    if a[:2] == b[:2] and a[3] == b[3] and a[4] <= b[5] and b[4] <= a[5]:
                        raise ValueError("fallback overlaps prior scope")
                shard, mock_row = _scope_rows(result["input"], target)
                facts = _input_scope_facts(result["input"], target)
                if mock_row["source_ids"] != ["MOCK.simulation_only"] or any(
                    s["id"] != "MOCK.simulation_only" for s in facts["sources"]
                ):
                    raise ValueError("fallback is not mock")
                if (
                    not shard["coverage_start"]
                    <= target["valid_from"]
                    <= target["valid_to"]
                    <= shard["coverage_end"]
                ):
                    raise ValueError("fallback outside coverage")
            resolutions.append(
                {
                    key: row[key]
                    for key in (
                        "target",
                        "observation_id",
                        "check_status",
                        "observation_status",
                        "reason",
                        "disposition",
                    )
                }
            )
        resolutions.sort(key=lambda item: _scope_key(item["target"]))
    return _combine(
        root,
        filesystem,
        result,
        current,
        additional_scopes=[entry["provenance"] for entry in entries[1:]],
        batch_record_sha256=batch_sha,
        batch_resolutions=resolutions,
    )


def _verify_batch_record(record, templates, candidate_id, baseline):
    if record.get("candidate_id") != candidate_id or record.get("baseline") != baseline:
        raise ValueError("batch identity mismatch")
    observed = {
        (item["observation_id"], _scope_key(item["target"]))
        for item in record["observations"]
        if item.get("observation_status") == "SUCCESS"
        and item.get("observation_state") == "COMMITTED"
    }
    expected = {
        (
            item["template"]["trace"]["observation_id"],
            _scope_key(item["template"]["trace"]["target"]),
        )
        for item in templates
    }
    partial = record["schema"] == "bt_api.calendar-daily-batch-evidence/2"
    if observed != expected or (not partial and len(observed) != len(record["observations"])):
        raise ValueError("batch observation mismatch")
    if partial:
        for row in record["observations"]:
            if (
                set(row)
                != {
                    "observation_id",
                    "source_type",
                    "source_authentication",
                    "target",
                    "assumptions",
                    "alignment",
                    "timeout",
                    "contract",
                    "requested_date",
                    "date_role",
                    "observation_state",
                    "observation_status",
                    "projection_state",
                    "reason",
                    "check_status",
                    "disposition",
                    "failure_proof",
                    "observation_diagnostics",
                }
                or row["source_type"] != "AKSHARE"
                or row["source_authentication"] != "NOT_PROVEN"
                or row["date_role"] != "REQUEST_DATE_NOT_EFFECTIVE_DATE"
                or type(row["reason"]) is not str
            ):
                raise ValueError("invalid batch observation")
            key = (row["observation_id"], _scope_key(row["target"]))
            if key in expected:
                if (
                    row.get("check_status") != "SUCCESS"
                    or row.get("disposition") != "UPDATED_AKSHARE"
                ):
                    raise ValueError("invalid success resolution")
            elif (
                row.get("check_status") != "FAILED"
                or row.get("disposition") not in {"RETAINED_AKSHARE", "MOCK_FALLBACK"}
                or row.get("observation_state") != "COMMITTED"
                or row.get("observation_status") not in _DEFINITE_FAILURES
            ):
                raise ValueError("invalid failed resolution")
    for entry in templates:
        template = entry["template"]
        trace = template["trace"]
        rows = [
            row
            for row in record["observations"]
            if row["observation_id"] == trace["observation_id"]
        ]
        if len(rows) != 1:
            raise ValueError("batch observation unavailable")
        row = rows[0]
        if (
            set(row)
            != (
                {
                    "observation_id",
                    "source_type",
                    "source_authentication",
                    "target",
                    "assumptions",
                    "alignment",
                    "timeout",
                    "contract",
                    "requested_date",
                    "date_role",
                    "observation_state",
                    "observation_status",
                    "projection_state",
                    "reason",
                }
                | (
                    {"check_status", "disposition", "failure_proof", "observation_diagnostics"}
                    if partial
                    else set()
                )
            )
            or row["source_type"] != "AKSHARE"
            or row["source_authentication"] != "NOT_PROVEN"
            or row["date_role"] != "REQUEST_DATE_NOT_EFFECTIVE_DATE"
            or trace["candidate_id"] != record["derived_id"]
            or trace["run_id"] != record["run_id"]
            or row["assumptions"] != trace["assumptions"]
            or row["requested_date"] != trace["requested_date"]
            or row["contract"] != template["semantic"]["projection"]["request"]["contract"]
            or row["projection_state"] != "VISIBLE_UNAUTHENTICATED"
        ):
            raise ValueError("batch request binding mismatch")


def build_batch_once(
    *, root, filesystem, candidate_id, templates, batch_id, batch_record_sha256, baseline
):
    """Build one review candidate from all captured scope templates."""
    try:
        if not runs._id(candidate_id):
            raise ValueError
        record = _batch_record(root, batch_id, batch_record_sha256)
        _verify_batch_record(record, templates, candidate_id, baseline)
        current = TradingCalendarStore(root).read_current()
        if baseline != {
            "publication_id": current.publication_id,
            "publication_sha256": current.publication_sha256,
            "version_sha256": current.version_sha256,
        }:
            raise MixedBaselineChangedError
        result = _materialize_batch(
            root, filesystem, candidate_id, templates, current, batch_record_sha256, record
        )
    except MixedBaselineChangedError:
        return {"status": "NOT_COMMITTED", "reason": "MIXED_BASELINE_CHANGED"}
    except Exception:
        return {"status": "NOT_COMMITTED", "reason": "INVALID_BATCH_INPUT"}
    candidate = review.build_once(
        root=root,
        filesystem=filesystem,
        candidate_id=candidate_id,
        input_data=result["input"],
        emit=lambda _: None,
        propagate_interrupt=True,
    )
    if candidate.get("status") != "COMMITTED":
        return {
            "status": candidate.get("status", "UNKNOWN"),
            "reason": "BATCH_CANDIDATE_RECONCILE_FIRST",
        }
    if candidate.get("review_status") != "STRUCTURALLY_READY_UNVERIFIED":
        return {
            "status": "NOT_COMMITTED",
            "candidate_state": "COMMITTED",
            "reason": "BATCH_CANDIDATE_NOT_READY",
        }
    body = {
        "schema": _BATCH_SCHEMA,
        "environment": "SIMULATION_ONLY",
        "candidate_id": candidate_id,
        "templates": templates,
        "provenance": result["provenance"],
        "baseline": baseline,
        "batch_id": batch_id,
        "batch_record_sha256": batch_record_sha256,
    }
    fds, chain = review._open(root, False)
    lock = None
    try:
        lock, identity = storage._lock(fds[-1], chain)
        storage._check_lock(fds[-1], chain, lock, identity)
        state = storage._write(
            fds[-1],
            chain,
            f"mock-mixed-{candidate_id}.json",
            storage._envelope(body),
            review._OUTPUT_LIMIT,
        )
        return {"status": state, "candidate_state": "COMMITTED", "candidate_id": candidate_id}
    finally:
        if lock is not None:
            with contextlib.suppress(OSError):
                fcntl.flock(lock, fcntl.LOCK_UN)
            with contextlib.suppress(OSError):
                os.close(lock)
        runs._close(fds)


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
    if body["schema"] == _BATCH_SCHEMA:
        record = _batch_record(root, body["batch_id"], body["batch_record_sha256"])
        _verify_batch_record(record, body["templates"], candidate_id, body["baseline"])
        current = TradingCalendarStore(root).read_current()
        if body["baseline"] != {
            "publication_id": current.publication_id,
            "publication_sha256": current.publication_sha256,
            "version_sha256": current.version_sha256,
        }:
            raise MixedBaselineChangedError("mixed baseline changed")
        materialized = _materialize_batch(
            root,
            filesystem,
            candidate_id,
            body["templates"],
            current,
            body["batch_record_sha256"],
            record,
        )
    else:
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
        if body["schema"] not in {_MULTI_SCHEMA, _BATCH_SCHEMA}:
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
            if body["schema"] == _BATCH_SCHEMA:
                record = _batch_record(root, body["batch_id"], body["batch_record_sha256"])
                _verify_batch_record(record, body["templates"], candidate_id, baseline)
                materialized = _materialize_batch(
                    root,
                    filesystem,
                    candidate_id,
                    body["templates"],
                    previous,
                    body["batch_record_sha256"],
                    record,
                )
            else:
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
