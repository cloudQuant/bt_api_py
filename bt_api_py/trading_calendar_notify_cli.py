"""Explicit, local SIMULATION_ONLY calendar notifications; no scheduler.

Proof means consistency of unauthenticated, cooperating local simulation records,
not source, identity, clock, delivery, durability, or production authorization.
Manual observations share the confirm lock, but release it before SMTP. State can
still change after the final observation and before sender invocation.

The CLI requires a private owner-only SMTP JSON file and an explicit journal path
to send. Manual reminders additionally require a private simulation clock JSON
with ``environment=SIMULATION_ONLY``, ``evidence=CALLER_DECLARED``, and a numeric
``error_bound_seconds``. This is an operator assertion, not a clock calibration
or authenticated clock proof; without it the clock remains UNKNOWN. SMTP server
acceptance is never represented as mailbox delivery.

An explicitly supplied schedule operation ID can select its original automatic
publication. ``--scan-missed`` is the explicit idempotent alternative: it scans
the schedule-owned pending review and effective terminal automatic publications,
dedupes by original review/publication ID and Beijing natural day through the
journal, and reports send failures and unknown results separately. It never
marks an unknown outcome as sent and does not resend one.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import math
import os
import re
import stat
import sys
from contextlib import contextmanager
from dataclasses import asdict, replace
from datetime import UTC, datetime, timedelta
from pathlib import Path

from bt_api_py.trading_calendar_notifications import (
    EmailConfig,
    NotificationResult,
    NotificationState,
    _config_complete,
    _period,
    notify_calendar_event,
    read_notification_outcome,
    send_via_base,
)

_SUPPORTED_PLATFORM = False
if os.name == "posix":
    try:
        import fcntl
    except ModuleNotFoundError as exc:
        if exc.name != "fcntl":
            raise
    else:
        _SUPPORTED_PLATFORM = True

if _SUPPORTED_PLATFORM:
    from bt_api_py import trading_calendar_detail_collection as storage
    from bt_api_py import trading_calendar_mock_cli as mock_cli
    from bt_api_py import trading_calendar_observation_run as runs
    from bt_api_py import trading_calendar_review_bundle as review
    from bt_api_py.trading_calendar_mock_publication import MockCalendarPublication
    from bt_api_py.trading_calendar_mock_publication import _id as _publication_id
    from bt_api_py.trading_calendar_store import (
        CalendarReadBudget,
        _decode,
        _sha,
        calendar_snapshot_digest,
    )

    _BUDGET = CalendarReadBudget(16 * 1024 * 1024, 64 * 1024 * 1024, 256)
_SHA = re.compile(r"[0-9a-f]{64}\Z", re.ASCII)
_VENUES = {"SHFE", "INE", "CZCE", "DCE", "CFFEX", "GFEX"}


def _digest(value):
    return hashlib.sha256(mock_cli._canonical(value)).hexdigest()


def _no_review_markers(operator, review_id):
    fds, chain = review._open(operator.root, False)
    try:
        for prefix in ("mock-review-stale-", "mock-confirm-intent-"):
            if (
                storage._read(fds[-1], chain, prefix + review_id + ".json", review._OUTPUT_LIMIT)
                is not None
            ):
                return False
        return True
    finally:
        runs._close(fds)


@contextmanager
def _review_observation_lock(operator, review_id):
    """Coordinate one read with the existing mock-confirm writer lock."""
    if not runs._id(review_id):
        raise ValueError("invalid review")
    fds, chain = review._open(operator.root, False)
    lock = None
    locked = False
    try:
        name = f".mock-confirm-{review_id}.lock"
        lock = os.open(name, os.O_RDWR | os.O_CREAT | os.O_NOFOLLOW, 0o600, dir_fd=fds[-1])
        info = os.fstat(lock)
        if (
            not stat.S_ISREG(info.st_mode)
            or info.st_uid != os.geteuid()
            or info.st_mode & 0o077
            or info.st_nlink != 1
        ):
            raise ValueError("invalid review lock")
        fcntl.flock(lock, fcntl.LOCK_SH | fcntl.LOCK_NB)
        locked = True

        def verify():
            entry = os.stat(name, dir_fd=fds[-1], follow_symlinks=False)
            if (
                (entry.st_dev, entry.st_ino) != (info.st_dev, info.st_ino)
                or not stat.S_ISREG(entry.st_mode)
                or entry.st_uid != os.geteuid()
                or entry.st_mode & 0o077
                or entry.st_nlink != 1
                or not runs._directory_matches(chain)
            ):
                raise ValueError("review lock changed")

        verify()
        yield
        verify()
    finally:
        try:
            if lock is not None:
                try:
                    if locked:
                        fcntl.flock(lock, fcntl.LOCK_UN)
                finally:
                    os.close(lock)
        finally:
            runs._close(fds)


@contextmanager
def _protected_journal(path):
    """Pin a private local journal; SQLite still reopens its pathname."""
    path = Path(path)
    if not path.is_absolute() or str(path) != os.path.realpath(path) or not path.name:
        raise ValueError("invalid journal")
    parent = path.parent
    dfd = os.open(parent, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
    fd = None
    try:
        directory = os.fstat(dfd)
        if (
            not stat.S_ISDIR(directory.st_mode)
            or directory.st_uid != os.geteuid()
            or stat.S_IMODE(directory.st_mode) != 0o700
        ):
            raise ValueError("invalid journal directory")
        flags = os.O_RDWR | os.O_NOFOLLOW | os.O_NONBLOCK
        try:
            fd = os.open(path.name, flags | os.O_CREAT | os.O_EXCL, 0o600, dir_fd=dfd)
        except FileExistsError:
            fd = os.open(path.name, flags, dir_fd=dfd)
        original = os.fstat(fd)

        def valid_file(info):
            return (
                stat.S_ISREG(info.st_mode)
                and info.st_uid == os.geteuid()
                and stat.S_IMODE(info.st_mode) == 0o600
                and info.st_nlink == 1
            )

        def verify():
            current_dir = os.stat(parent, follow_symlinks=False)
            current_file = os.stat(path.name, dir_fd=dfd, follow_symlinks=False)
            if (
                (current_dir.st_dev, current_dir.st_ino) != (directory.st_dev, directory.st_ino)
                or not stat.S_ISDIR(current_dir.st_mode)
                or current_dir.st_uid != os.geteuid()
                or stat.S_IMODE(current_dir.st_mode) != 0o700
                or (current_file.st_dev, current_file.st_ino) != (original.st_dev, original.st_ino)
                or not valid_file(current_file)
                or not valid_file(os.fstat(fd))
            ):
                raise ValueError("journal identity changed")
            for suffix in ("-journal", "-wal", "-shm"):
                try:
                    auxiliary = os.stat(path.name + suffix, dir_fd=dfd, follow_symlinks=False)
                except FileNotFoundError:
                    continue
                if not valid_file(auxiliary):
                    raise ValueError("invalid journal auxiliary")

        verify()
        yield verify
    finally:
        try:
            if fd is not None:
                os.close(fd)
        finally:
            os.close(dfd)


def _candidate_binding(operator, candidate_id, publication_id=None):
    provenance = None
    observed = review.show(
        root=operator.root, candidate_id=candidate_id, filesystem=runs.TRUSTED_LOCAL_POSIX
    )
    if observed.get("status") != "VISIBLE_UNAUTHENTICATED":
        raise ValueError("candidate unavailable")
    is_mixed = observed["bundle"]["input"].get("metadata") == {
        "data_origin": "AKSHARE_MOCK",
        "environment": "SIMULATION_ONLY",
    }
    if publication_id is not None and is_mixed:
        from bt_api_py import trading_calendar_mixed_publication as mixed

        history = mixed.read_history(
            root=operator.root,
            filesystem=runs.TRUSTED_LOCAL_POSIX,
            candidate_id=candidate_id,
            publication_id=publication_id,
        )
        if history.get("status") == "VISIBLE_UNAUTHENTICATED":
            if history.get("publication_consistency") != "RECEIPT_CHAIN_BOUND_UNAUTHENTICATED":
                raise ValueError("historical publication unverified")
            candidate = review.show(
                root=operator.root, candidate_id=candidate_id, filesystem=runs.TRUSTED_LOCAL_POSIX
            )
            if (
                candidate.get("status") != "VISIBLE_UNAUTHENTICATED"
                or candidate.get("payload_sha256") != history.get("candidate_payload_sha256")
                or candidate["bundle"]["report"].get("status") != "STRUCTURALLY_READY_UNVERIFIED"
                or candidate["bundle"]["report"].get("source_authentication") != "NOT_PROVEN"
                or candidate["bundle"]["input"].get("metadata")
                != {"data_origin": "AKSHARE_MOCK", "environment": "SIMULATION_ONLY"}
            ):
                raise ValueError("historical candidate unavailable")
            composite = candidate["bundle"]["report"]["composite"]
            provenance = history["simulation_provenance"]
        elif history.get("reason") != "NOT_MULTI_SCOPE_CANDIDATE":
            raise ValueError("historical candidate unavailable")
    if provenance is None:
        candidate, composite = operator._candidate(candidate_id)
        provenance = operator._candidate_provenance(candidate_id, candidate)
    if provenance is None:
        if candidate_id != operator._association.get("derived_id"):
            raise ValueError("original candidate association unavailable")
    else:
        schema = provenance.get("schema")
        if schema in {
            "bt_api.calendar-mixed-scopes-provenance/1",
            "bt_api.calendar-mixed-scopes-provenance/2",
            "bt_api.calendar-mixed-scopes-provenance/3",
        }:
            targets = (
                provenance["update_targets"]
                if schema
                in {
                    "bt_api.calendar-mixed-scopes-provenance/2",
                    "bt_api.calendar-mixed-scopes-provenance/3",
                }
                else [provenance["update_target"]]
            )
            scopes = [item for item in provenance["scopes"] if item["target"] in targets]
            if not targets or len(scopes) != len(targets):
                raise ValueError("mixed update scope unavailable")
            locators = [item.get("evidence_locator", {}) for item in scopes]
        else:
            locators = [provenance.get("evidence_locator", {})]
        if any(
            locator.get("candidate_id") != candidate_id
            or locator.get("parent_candidate_id") != operator._association.get("derived_id")
            or (
                schema
                in {
                    "bt_api.calendar-mixed-scopes-provenance/2",
                    "bt_api.calendar-mixed-scopes-provenance/3",
                }
                and locator.get("observation_run_id") != operator.run_id
            )
            for locator in locators
        ):
            raise ValueError("mixed candidate association unavailable")
        if schema in {
            "bt_api.calendar-mixed-scopes-provenance/2",
            "bt_api.calendar-mixed-scopes-provenance/3",
        } and len({locator.get("observation_id") for locator in locators}) != len(locators):
            raise ValueError("batch observations not distinct")
    return candidate, composite, provenance


def _summary(
    difference,
    candidate_id,
    candidate,
    publication_id=None,
    version_id=None,
    review_id=None,
    provenance=None,
):
    changes = difference.get("changes")
    if (
        type(changes) is not dict
        or set(changes) != {"sources", "trading_days", "trading_hours", "exceptions", "coverage"}
        or any(type(value) is not list for value in changes.values())
    ):
        raise ValueError("invalid difference")
    venues = set()

    def visit(value):
        if type(value) is dict:
            if value.get("venue") in _VENUES:
                venues.add(value["venue"])
            for child in value.values():
                visit(child)
        elif type(value) is list:
            for child in value:
                visit(child)

    visit(changes)
    counts = ", ".join(f"{key}={len(changes[key])}" for key in sorted(changes))
    checks = {}
    for item in candidate["bundle"]["report"]["shards"]:
        venue = item.get("venue")
        if venue not in _VENUES or venue in checks:
            raise ValueError("invalid venue check")
        checks[venue] = (
            "STRUCTURAL_COMPLETE_UNVERIFIED"
            if item.get("status") == "STRUCTURAL_COMPLETE_UNVERIFIED"
            else "UNKNOWN"
        )
    origin = "MOCK" if provenance is None else "AKSHARE_MOCK"
    window_summary = ""
    if provenance is not None:

        def scopes(value):
            if value is None:
                return []
            return (
                value["scopes"]
                if value.get("schema")
                in {
                    "bt_api.calendar-mixed-scopes-provenance/1",
                    "bt_api.calendar-mixed-scopes-provenance/2",
                    "bt_api.calendar-mixed-scopes-provenance/3",
                }
                else [value]
            )

        def scope_key(target):
            venue, kind, product, contract, start, end = (
                target[key]
                for key in ("venue", "kind", "product", "mock_contract", "valid_from", "valid_to")
            )
            if (
                venue not in _VENUES
                or kind not in {"future", "option"}
                or any(
                    type(item) is not str or re.fullmatch(r"[A-Za-z0-9_.:-]{1,128}", item) is None
                    for item in (product, contract)
                )
                or any(
                    type(item) is not str
                    or re.fullmatch(r"[0-9]{4}-[0-9]{2}-[0-9]{2}", item) is None
                    for item in (start, end)
                )
            ):
                raise ValueError("invalid scope summary")
            return venue, kind, product, contract, start, end

        after_scopes = scopes(provenance)
        resolution_map = {}
        if provenance.get("schema") == "bt_api.calendar-mixed-scopes-provenance/3":
            window_summary += "Batch result: PARTIAL; some scope checks failed.\n"
            for resolution in provenance["resolutions"]:
                key = scope_key(resolution["target"])
                disposition, check, observation = (
                    resolution["disposition"],
                    resolution["check_status"],
                    resolution["observation_status"],
                )
                if (
                    key in resolution_map
                    or (
                        disposition == "UPDATED_AKSHARE"
                        and (check, observation) != ("SUCCESS", "SUCCESS")
                    )
                    or (
                        disposition in {"RETAINED_AKSHARE", "MOCK_FALLBACK"}
                        and (
                            check != "FAILED"
                            or observation
                            not in {
                                "EMPTY",
                                "MISSING_TRADING_TIME",
                                "STRUCTURE_CHANGED",
                                "DEPENDENCY_UNAVAILABLE",
                                "VERSION_MISMATCH",
                                "FETCH_ERROR",
                            }
                        )
                    )
                    or disposition not in {"UPDATED_AKSHARE", "RETAINED_AKSHARE", "MOCK_FALLBACK"}
                ):
                    raise ValueError("unknown partial resolution")
                resolution_map[key] = resolution
        before_map = {}
        previous_known = False
        if difference.get("schema") == "bt_api.calendar-full-declaration-difference/2":
            before, after = difference["simulation"]["before"], difference["simulation"]["after"]
            if after != provenance:
                raise ValueError("scope provenance changed")
            before_map = {scope_key(item["target"]): item for item in scopes(before)}
            previous_known = True
            if (
                before is None
                or after is None
                or before["semantic_sha256"] != after["semantic_sha256"]
            ):
                window_summary += "Simulation semantics: CHANGED.\n"
        after_keys = set()
        for item in after_scopes:
            key = scope_key(item["target"])
            if key in after_keys:
                raise ValueError("duplicate scope")
            after_keys.add(key)
            old = before_map.get(key)
            change = (
                "INITIAL"
                if not previous_known
                else "ADDED"
                if old is None
                else "UNCHANGED"
                if old["semantic_sha256"] == item["semantic_sha256"]
                else "CHANGED"
            )
            if change != "UNCHANGED":
                venues.add(key[0])
            windows = []
            for window in item["windows"]:
                name, scope, source = window["window_id"], window["scope"], window["source_type"]
                if (
                    type(name) is not str
                    or re.fullmatch(r"[A-Za-z0-9_.:-]{1,128}", name) is None
                    or scope not in {"DAY", "NIGHT"}
                    or source not in {"AKSHARE", "MOCK"}
                ):
                    raise ValueError("invalid window summary")
                windows.append(f"{scope}/{name}={source}")
            resolution = resolution_map.get(key)
            if resolution is not None and resolution["disposition"] == "MOCK_FALLBACK":
                raise ValueError("fallback unexpectedly has mixed scope")
            label = (
                f"; check={resolution['check_status']}; observation={resolution['observation_status']}; "
                f"disposition={resolution['disposition']}"
                if resolution
                else ""
            )
            window_summary += (
                f"Scope {'/'.join(key)}: {change}{label}; window sources: {', '.join(windows)}.\n"
            )
        for key, resolution in sorted(resolution_map.items()):
            if key in after_keys:
                continue
            if resolution["disposition"] != "MOCK_FALLBACK":
                raise ValueError("resolved scope unavailable")
            venues.add(key[0])
            window_summary += (
                f"Scope {'/'.join(key)}: check=FAILED; observation={resolution['observation_status']}; "
                "disposition=MOCK_FALLBACK; source=MOCK.\n"
            )
        for key in sorted(before_map.keys() - after_keys):
            venues.add(key[0])
            window_summary += f"Scope {'/'.join(key)}: REMOVED.\n"
        window_summary += "Date alignment UNKNOWN; simulation assumptions apply.\n"
    return (
        f"SIMULATION_ONLY / {origin}; source authentication NOT_PROVEN.\n"
        f"Candidate: {candidate_id}\n"
        + (f"Publication: {publication_id}; version: {version_id}\n" if publication_id else "")
        + (f"Review ID: {review_id}\n" if review_id else "")
        + f"Affected venues: {', '.join(sorted(venues)) or 'UNKNOWN'}\nChanges: {counts}\n"
        + "Exchange checks: "
        + (", ".join(f"{k}={v}" for k, v in sorted(checks.items())) or "UNKNOWN")
        + ".\n"
        + window_summary
        + (
            "Published does not imply running processes switched; same-day restart may rebind."
            if publication_id
            else "Review the original candidate using the mock-confirm command and its original review digest."
        )
    )


def _manual_conditions(operator, review_id, review_sha256, body):
    pending = mock_cli._read_pending(operator, review_id)
    if (
        pending.get("status") != "VISIBLE_UNAUTHENTICATED"
        or pending.get("payload_sha256") != review_sha256
        or pending.get("body") != body
    ):
        raise ValueError("review unavailable")
    _, _, provenance = _candidate_binding(operator, body["candidate_id"])
    if provenance is not None and provenance.get("schema") in {
        "bt_api.calendar-mixed-scopes-provenance/2",
        "bt_api.calendar-mixed-scopes-provenance/3",
    }:
        if not operator._is_batch_proposal(body["proposal"]):
            raise ValueError("batch proposal unavailable")
        operator._check_batch_identity(
            body["proposal"],
            publication_id=body["publication_id"],
            version_id=body["version_id"],
            review_id=review_id,
        )
    if not _no_review_markers(operator, review_id):
        raise ValueError("review intent or stale marker")
    if (
        body.get("schema") != mock_cli._SUPPLEMENT_SCHEMA
        or mock_cli._verify_supplement(operator, body["input_binding"]) != "MATCH"
    ):
        raise ValueError("supplement unavailable")
    if any(
        result.get("status") != "NOT_OBSERVED"
        for result in (
            operator.inspect_publication_claim(publication_id=body["publication_id"]),
            operator.inspect_version_claim(version_id=body["version_id"]),
        )
    ):
        raise ValueError("publication intent exists")
    with operator._store._readonly_lock():
        store = operator._store
        if store._pending_exists() or store._policy_change_pending():
            raise ValueError("pending operation")
        policy = store._review_policy_locked()
        if (
            policy is None
            or policy.mode != "manual"
            or asdict(policy) != body["proposal"]["policy"]
        ):
            raise ValueError("policy changed")
        if operator._declaration is not None:
            operator._check_policy_locked(policy)
        if any(
            os.path.lexists(Path(operator.root) / folder / (identifier + ".json"))
            for folder, identifier in (
                ("publications", body["publication_id"]),
                ("receipts", body["publication_id"]),
                ("versions", body["version_id"]),
            )
        ):
            raise ValueError("original publication ID requires reconciliation")
        baseline = body["proposal"]["baseline"]
        if baseline["mode"] == "ABSENT":
            if os.path.lexists(Path(operator.root) / "CURRENT.json") or any(
                any((Path(operator.root) / folder).iterdir())
                for folder in ("versions", "publications", "receipts")
            ):
                raise ValueError("baseline changed")
        else:
            pointer = store._read(Path(operator.root) / "CURRENT.json")
            current = store._load(pointer)
            store._validate_chain_receipts(pointer, _max_publications=256)
            if (baseline["mode"], baseline["publication_id"], baseline["publication_sha256"]) != (
                "MATCH",
                current.publication_id,
                current.publication_sha256,
            ):
                raise ValueError("baseline changed")
    operator._guard()
    return policy


def _manual(operator, review_id, review_sha256):
    with _review_observation_lock(operator, review_id):
        return _manual_locked(operator, review_id, review_sha256)


def _manual_locked(operator, review_id, review_sha256):
    if type(review_sha256) is not str or not _SHA.fullmatch(review_sha256):
        raise ValueError("original review digest required")
    pending = mock_cli._read_pending(operator, review_id)
    if (
        pending.get("status") != "VISIBLE_UNAUTHENTICATED"
        or pending.get("payload_sha256") != review_sha256
    ):
        raise ValueError("review unavailable")
    body = pending["body"]
    _manual_conditions(operator, review_id, review_sha256, body)
    fresh = operator.prepare(candidate_id=body["candidate_id"])
    if fresh != body["proposal"] or fresh.get("status") not in {
        "CHANGED",
        "FIRST_SIMULATION_PUBLICATION",
    }:
        raise ValueError("candidate changed")
    candidate, _, provenance = _candidate_binding(operator, body["candidate_id"])
    if candidate["payload_sha256"] != fresh["candidate_payload_sha256"]:
        raise ValueError("candidate changed")
    policy = _manual_conditions(operator, review_id, review_sha256, body)
    operator._guard()
    return (
        policy.directory_id,
        _summary(
            fresh["difference"],
            body["candidate_id"],
            candidate,
            review_id=review_id,
            provenance=provenance,
        ),
        _digest(body),
    )


def _auto(operator, publication_id):
    claim_observed = operator.inspect_publication_claim(publication_id=publication_id)
    if claim_observed.get("status") != "VISIBLE_UNAUTHENTICATED":
        raise ValueError("claim unavailable")
    claim = claim_observed["claim"]
    if claim["state"] != "INTENT_ONLY":
        raise ValueError("no content change")
    store = operator._store
    total = 0

    def read(path):
        nonlocal total
        remaining = _BUDGET.max_total_bytes - total
        if remaining <= 0:
            raise ValueError("read budget exceeded")
        value = store._read_bounded(path, min(_BUDGET.max_file_bytes, remaining))
        total += len(value)
        return value

    with store._readonly_lock():
        if store._pending_exists() or store._policy_change_pending():
            raise ValueError("pending operation")
        policy = store._review_policy_locked()
        if policy is None:
            raise ValueError("policy unavailable")
        if operator._declaration is not None:
            operator._check_policy_locked(policy)
        pointer = read(Path(operator.root) / "CURRENT.json")
        receipt = store._read_receipt(publication_id, _reader=read)
        if receipt["version_id"] != claim["version_id"]:
            raise ValueError("publication version mismatch")
        store._load(
            pointer,
            target=receipt["pointer"],
            target_version=receipt["version_id"],
            _reader=read,
            _max_publications=_BUDGET.max_publications,
        )
        store._validate_chain_receipts(
            pointer, _reader=read, _max_publications=_BUDGET.max_publications
        )
        event_raw = read(Path(operator.root) / "publications" / f"{publication_id}.json")
        if _sha(event_raw) != receipt["pointer"]["publication_sha256"]:
            raise ValueError("publication changed")
        event = _decode(event_raw)
        evidence = event.get("review_policy")
        if (
            read(Path(operator.root) / "CURRENT.json") != pointer
            or store._pending_exists()
            or store._policy_change_pending()
        ):
            raise ValueError("store changed")
    if (
        not evidence
        or evidence["mode"] != "auto"
        or evidence["directory_id"] != policy.directory_id
    ):
        raise ValueError("publication policy mismatch")
    diff = evidence["difference"]
    if diff.get("schema") == "bt_api.calendar-mock-first-difference-reference/1":
        retained = operator.inspect_first_difference(publication_id=publication_id)
        if retained.get("status") != "VISIBLE_UNAUTHENTICATED" or retained.get(
            "difference_sha256"
        ) != diff.get("first_difference_sha256"):
            raise ValueError("first difference unavailable")
        difference = retained["difference"]
    elif diff.get("schema") in {
        "bt_api.calendar-full-declaration-difference/1",
        "bt_api.calendar-full-declaration-difference/2",
    }:
        difference = diff
    else:
        raise ValueError("difference unavailable")
    candidate, composite, provenance = _candidate_binding(
        operator, claim["candidate_id"], publication_id
    )
    if provenance is not None and provenance.get("schema") in {
        "bt_api.calendar-mixed-scopes-provenance/2",
        "bt_api.calendar-mixed-scopes-provenance/3",
    }:
        identity = operator._batch_identity(claim["candidate_id"])
        if (
            identity["publication_id"] != publication_id
            or identity["version_id"] != receipt["version_id"]
        ):
            raise ValueError("batch publication identity mismatch")
    snapshot_hash = calendar_snapshot_digest(
        composite["snapshot"],
        coverage_manifest_bytes=mock_cli._canonical(composite["manifest"]),
        coverage_manifest_sha256=composite["manifest_sha256"],
        binding_bytes=mock_cli._canonical(composite["binding"]),
        binding_sha256=composite["binding_sha256"],
        simulation_provenance=provenance,
    )
    simulation_changed = False
    if difference.get("schema") == "bt_api.calendar-full-declaration-difference/2":
        sim = difference["simulation"]
        before, after = sim["before"], sim["after"]
        if after != provenance:
            raise ValueError("candidate provenance changed")
        simulation_changed = (
            before is None or after is None or before["semantic_sha256"] != after["semantic_sha256"]
        )
    if (
        diff.get("candidate_id") != claim["candidate_id"]
        or diff.get("candidate_payload_sha256") != claim["candidate_payload_sha256"]
        or candidate["payload_sha256"] != claim["candidate_payload_sha256"]
        or evidence["snapshot_sha256"] != claim["snapshot_sha256"]
        or evidence["snapshot_sha256"] != snapshot_hash
        or _digest(difference) != claim["difference_sha256"]
        or not (any(difference.get("changes", {}).values()) or simulation_changed)
    ):
        raise ValueError("candidate association unavailable")
    operator._guard()
    return (
        policy.directory_id,
        _summary(
            difference,
            claim["candidate_id"],
            candidate,
            publication_id,
            claim["version_id"],
            provenance=provenance,
        ),
        _digest({"claim": claim, "evidence": evidence, "receipt": receipt}),
    )


def _read_protected_json(path):
    if not os.path.isabs(path) or os.path.realpath(path) != path:
        raise ValueError("noncanonical config")
    fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    try:
        info = os.fstat(fd)
        if (
            not stat.S_ISREG(info.st_mode)
            or info.st_uid != os.geteuid()
            or info.st_mode & 0o077
            or info.st_nlink != 1
            or info.st_size > 16384
        ):
            raise ValueError("unprotected config")
        raw = os.read(fd, 16385)
        if len(raw) != info.st_size:
            raise ValueError("config changed")

        def version(item):
            return (
                item.st_dev,
                item.st_ino,
                item.st_size,
                item.st_mtime_ns,
                item.st_ctime_ns,
                item.st_mode,
                item.st_uid,
                item.st_nlink,
            )

        if version(info) != version(os.fstat(fd)) or version(info) != version(
            os.stat(path, follow_symlinks=False)
        ):
            raise ValueError("config changed")

        def unique(pairs):
            result = {}
            for key, value in pairs:
                if key in result:
                    raise ValueError("duplicate field")
                result[key] = value
            return result

        return json.loads(raw, object_pairs_hook=unique), version(info)
    finally:
        os.close(fd)


def _read_config(path):
    data, version = _read_protected_json(path)
    if type(data) is not dict or set(data) - {
        "enabled",
        "host",
        "port",
        "timeout",
        "sender",
        "recipients",
        "username",
        "password",
        "security",
    }:
        raise ValueError("invalid config")
    if type(data.get("recipients", [])) is not list:
        raise ValueError("invalid recipients")
    data["recipients"] = tuple(data.get("recipients", []))
    return EmailConfig(**data), version


def _read_clock_declaration(path):
    data, version = _read_protected_json(path)
    if (
        type(data) is not dict
        or set(data) != {"environment", "evidence", "error_bound_seconds"}
        or data["environment"] != "SIMULATION_ONLY"
        or data["evidence"] != "CALLER_DECLARED"
        or type(data["error_bound_seconds"]) not in (int, float)
        or not math.isfinite(data["error_bound_seconds"])
        or not 0 <= data["error_bound_seconds"] <= 86400
    ):
        raise ValueError("invalid simulation clock declaration")
    return timedelta(seconds=data["error_bound_seconds"]), version


def run(
    *,
    root,
    run_id,
    kind,
    event_id,
    review_sha256=None,
    config=None,
    journal_path=None,
    dry_run=False,
    sender=None,
    clock_instant=None,
    clock_error_bound=None,
    clock_evidence="UNKNOWN",
    clock_source=None,
    config_guard=None,
):
    """Perform one explicit event check; clock assertions are simulation-only injections."""
    if not _SUPPORTED_PLATFORM:
        return NotificationResult("NOT_SENT", "UNSUPPORTED_FILESYSTEM")
    send_started = False
    try:
        if kind not in ("manual", "auto") or not (
            runs._id(event_id) if kind == "manual" else _publication_id(event_id)
        ):
            raise ValueError("invalid event")
        operator = MockCalendarPublication(root=root, run_id=run_id)
        if operator._declaration is None:
            raise ValueError("persistent simulation declaration required")
        observe = (
            (lambda: _manual(operator, event_id, review_sha256))
            if kind == "manual"
            else (lambda: _auto(operator, event_id))
        )
        scope, body, fingerprint = observe()
        if dry_run:
            return NotificationResult("NOT_SENT", "DRY_RUN_ELIGIBLE")
        if config is None:
            return NotificationResult("NOT_SENT", "DISABLED")
        if type(config) is not EmailConfig or not config.enabled:
            return NotificationResult("NOT_SENT", "DISABLED")
        config = replace(config, subject="SIMULATION_ONLY calendar notification", body=body)
        if not _config_complete(config):
            return NotificationResult("NOT_SENT", "INVALID_CONFIG")
        if clock_source is not None:
            clock_instant = clock_source()
        expected_period = _period(clock_instant, clock_error_bound) if kind == "manual" else None
        if kind == "manual" and (clock_evidence != "PROVEN" or expected_period is None):
            return NotificationResult("NOT_SENT", "CLOCK_UNKNOWN")
        with _protected_journal(journal_path) as verify_journal:

            def tracked_sender(send_config):
                nonlocal send_started
                send_started = True
                return (sender or send_via_base)(send_config)

            def reader(request_kind, request_scope, request_event):
                new_scope, new_body, new_fingerprint = observe()
                good = (
                    request_kind,
                    request_scope,
                    request_event,
                    new_scope,
                    new_body,
                    new_fingerprint,
                ) == (kind, scope, event_id, scope, body, fingerprint)
                verify_journal()
                if config_guard is not None:
                    good = good and config_guard()
                if kind == "manual" and clock_source is not None:
                    good = (
                        good
                        and expected_period is not None
                        and _period(clock_source(), clock_error_bound) == expected_period
                    )
                return NotificationState(
                    scope,
                    event_id,
                    kind,
                    "PROVEN" if good else "UNKNOWN",
                    candidate_state="PENDING" if good and kind == "manual" else "UNKNOWN",
                    publication_state="COMMITTED" if good and kind == "auto" else "UNKNOWN",
                    content_change="CHANGED" if good and kind == "auto" else "UNKNOWN",
                )

            return notify_calendar_event(
                journal_path=journal_path,
                kind=kind,
                scope_id=scope,
                event_id=event_id,
                config=config,
                state_reader=reader,
                sender=tracked_sender,
                clock_instant=clock_instant,
                clock_error_bound=clock_error_bound,
                clock_evidence=clock_evidence,
            )
    except Exception:
        if send_started:
            return NotificationResult("UNKNOWN", "POST_SEND_OUTCOME_UNKNOWN", "UNKNOWN")
        return NotificationResult("NOT_SENT", "STATE_OR_CONFIG_UNAVAILABLE")


def run_scheduled(
    *,
    root,
    config=None,
    journal_path=None,
    dry_run=False,
    sender=None,
    clock_instant=None,
    clock_error_bound=None,
    clock_evidence="UNKNOWN",
    clock_source=None,
    config_guard=None,
):
    """Notify only the original schedule-owned review after repeated local checks.

    Discovery and clock evidence are unauthenticated simulation declarations.
    State may change between the final check and the sender invocation.
    """
    if not _SUPPORTED_PLATFORM:
        return NotificationResult("NOT_SENT", "UNSUPPORTED_FILESYSTEM")
    try:
        from bt_api_py import trading_calendar_schedule_once as schedule

        def discover():
            value = schedule.inspect_active(root=root, filesystem=runs.TRUSTED_LOCAL_POSIX)
            if (
                type(value) is not dict
                or value.get("status") != "PENDING_REVIEW"
                or value.get("environment") != "SIMULATION_ONLY"
                or value.get("source_authentication") != "NOT_PROVEN"
                or any(
                    not runs._id(value.get(key)) for key in ("operation_id", "run_id", "review_id")
                )
                or type(value.get("review_payload_sha256")) is not str
                or _SHA.fullmatch(value["review_payload_sha256"]) is None
            ):
                raise ValueError("scheduled review unavailable")
            return tuple(
                value[key]
                for key in ("operation_id", "run_id", "review_id", "review_payload_sha256")
            )

        identity = discover()

        def guard():
            return (config_guard is None or config_guard()) and discover() == identity

        result = run(
            root=root,
            run_id=identity[1],
            kind="manual",
            event_id=identity[2],
            review_sha256=identity[3],
            config=config,
            journal_path=journal_path,
            dry_run=dry_run,
            sender=sender,
            clock_instant=clock_instant,
            clock_error_bound=clock_error_bound,
            clock_evidence=clock_evidence,
            clock_source=clock_source,
            config_guard=guard,
        )
        if dry_run and result.reason == "DRY_RUN_ELIGIBLE" and not guard():
            return NotificationResult("NOT_SENT", "SCHEDULE_REVIEW_UNAVAILABLE")
        return result
    except Exception:
        return NotificationResult("NOT_SENT", "SCHEDULE_REVIEW_UNAVAILABLE")


def _revalidate_schedule_publication(root, operation_id, expected_terminal):
    """Replay the original terminal proof using existing read-only store/ledger locks."""
    from bt_api_py import trading_calendar_schedule_once as schedule

    def read_terminal():
        with schedule._store_lock(root, readonly=True) as (_, fd, chain, _):  # noqa: SIM117
            with schedule._ledger(root, fd, chain, readonly=True) as db:
                active, _ = schedule._ledger_state(db, root)
                if active is not None or schedule._pending_locked(fd, chain, root) is not None:
                    raise ValueError("another schedule operation pending")
                row, completed = schedule._verify_terminal_files(db, fd, chain, root, operation_id)
                if completed is None:
                    raise ValueError("non-effective schedule terminal")
                returned = schedule._returned(fd, chain, row[0], row[1])
                if returned is None or returned[1] != row[3] or returned[0]["status"] != "COMMITTED":
                    raise ValueError("automatic terminal unavailable")
                if completed[0] != expected_terminal or completed[0]["outcome"] != "COMMITTED":
                    raise ValueError("terminal changed")
                return row, completed, returned

    row, completed, returned = read_terminal()
    pending = row[0]
    proof = schedule._batch_proof(pending, returned[0], allow_review=False)
    previous = completed[0]["proof"]
    if proof["kind"] != "COMMITTED" or any(
        proof.get(key) != value for key, value in previous.items() if key != "pointer_sha256"
    ):
        raise ValueError("terminal proof changed")
    with schedule._store_lock(root, readonly=True) as (store, fd, chain, _):  # noqa: SIM117
        with schedule._ledger(root, fd, chain, readonly=True) as db:
            active, _ = schedule._ledger_state(db, root)
            if active is not None or schedule._pending_locked(fd, chain, root) is not None:
                raise ValueError("another schedule operation pending")
            if schedule._verify_terminal_files(db, fd, chain, root, operation_id) != (row, completed):
                raise ValueError("terminal changed")
            if schedule._returned(fd, chain, pending, row[1]) != returned:
                raise ValueError("returned evidence changed")
            shown = schedule._batch_show_readonly(pending, locked=(fd, chain))
            if shown["evidence_sha256"] != returned[0]["batch_evidence_sha256"]:
                raise ValueError("batch evidence changed")
            schedule._verify_locked(store, pending, returned[0], proof)
    return _digest({"terminal": completed[0], "proof": proof})


def run_scheduled_publication(
    *, root, operation_id, config=None, journal_path=None, dry_run=False, sender=None,
    clock_instant=None, clock_error_bound=None, clock_evidence="UNKNOWN", clock_source=None,
    config_guard=None,
):
    """Notify one explicitly identified schedule publication; never scan history."""
    if not _SUPPORTED_PLATFORM:
        return NotificationResult("NOT_SENT", "UNSUPPORTED_FILESYSTEM")
    try:
        from bt_api_py import trading_calendar_schedule_once as schedule

        def discover():
            value = schedule.inspect(
                root=root, operation_id=operation_id, filesystem=runs.TRUSTED_LOCAL_POSIX
            )
            if (
                type(value) is not dict
                or value.get("status") != "LOCAL_HISTORICAL_RECORD_ONLY"
                or value.get("environment") != "SIMULATION_ONLY"
                or value.get("source_authentication") != "NOT_PROVEN"
                or value.get("operation_id") != operation_id
                or not runs._id(operation_id)
                or type(value.get("identity_bundle")) is not dict
                or type(value.get("terminal")) is not dict
            ):
                raise ValueError("scheduled publication unavailable")
            ids, terminal = value["identity_bundle"], value["terminal"]
            if (
                terminal.get("operation_id") != operation_id
                or terminal.get("identity_bundle") != ids
                or terminal.get("outcome") != "COMMITTED"
                or not runs._id(ids.get("run_id"))
                or not _publication_id(ids.get("publication_id"))
            ):
                raise ValueError("scheduled publication identity unavailable")
            return (
                ids["run_id"],
                ids["publication_id"],
                _revalidate_schedule_publication(root, operation_id, terminal),
            )

        identity = discover()

        def guard():
            return (config_guard is None or config_guard()) and discover() == identity

        result = run(
            root=root,
            run_id=identity[0],
            kind="auto",
            event_id=identity[1],
            config=config,
            journal_path=journal_path,
            dry_run=dry_run,
            sender=sender,
            clock_instant=clock_instant,
            clock_error_bound=clock_error_bound,
            clock_evidence=clock_evidence,
            clock_source=clock_source,
            config_guard=guard,
        )
        if dry_run and result.reason == "DRY_RUN_ELIGIBLE" and not guard():
            return NotificationResult("NOT_SENT", "SCHEDULE_PUBLICATION_UNAVAILABLE")
        return result
    except Exception:
        return NotificationResult("NOT_SENT", "SCHEDULE_PUBLICATION_UNAVAILABLE")


class _Parser(argparse.ArgumentParser):
    def error(self, message):
        raise ValueError("invalid arguments")


_SENT_OUTCOMES = frozenset({"ACCEPTED"})
_FAILED_OUTCOMES = frozenset({"PARTIAL_REFUSAL", "REFUSED"})


def _scan_classify(probe):
    """Map one read-only journal probe to a scan decision."""
    if probe["status"] == "ABSENT":
        return "DUE", None
    if probe["status"] != "AVAILABLE":
        return "RESULT_UNKNOWN", probe["status"]
    outcome = probe["outcome"]
    if outcome in _SENT_OUTCOMES:
        return "ALREADY_SENT", outcome
    if outcome in _FAILED_OUTCOMES:
        return "SEND_FAILED", outcome
    if outcome == "UNKNOWN":
        return "RESULT_UNKNOWN", outcome
    if outcome == "NOT_SENT":
        return "DUE", outcome
    return "RESULT_UNKNOWN", probe["status"]


def _scan_send_classify(result):
    """Map one explicit send result without ever treating UNKNOWN as sent."""
    if result.status == "ACCEPTED" or (
        result.status == "DUPLICATE" and result.journal_status == "ACCEPTED"
    ):
        return "SENT", result.reason
    if result.status in _FAILED_OUTCOMES:
        return "SEND_FAILED", result.status
    if result.status in ("UNKNOWN", "DUPLICATE"):
        return "RESULT_UNKNOWN", result.journal_status or result.status
    if result.status == "NOT_SENT":
        return "SKIPPED", result.reason
    if result.status == "DRY_RUN_ELIGIBLE":
        return "DUE", result.reason
    return "RESULT_UNKNOWN", result.status


def scan_missed(
    *,
    root,
    config=None,
    journal_path=None,
    dry_run=False,
    sender=None,
    clock_instant=None,
    clock_error_bound=None,
    clock_evidence="UNKNOWN",
    clock_source=None,
    config_guard=None,
    limit=64,
):
    """Scan durable state and explicitly resend unconfirmed notifications.

    The scan is read-only apart from the explicit send it performs. It enumerates
    the schedule-owned pending review (a reminder keyed by review ID and Beijing
    day) and effective terminal automatic publications (a summary keyed by
    publication ID). Completion, candidate invalidation or a switch away from the
    manual policy suppresses a pending reminder. Send failures and unknown results
    are reported in separate buckets; an unknown journal outcome is never resent
    and never reported as sent. All evidence is unauthenticated local state.
    """
    report = {
        "environment": "SIMULATION_ONLY",
        "source_authentication": "NOT_PROVEN",
        "delivery": "NOT_PROVEN",
        "clock_evidence": clock_evidence,
        "dedupe": "ORIGINAL_ID_AND_BEIJING_DAY",
        "pending_review": None,
        "publications": [],
        "publication_scan": {"status": "NOT_RUN", "reason": "NOT_RUN"},
        "send_failures": [],
        "result_unknown": [],
        "counts": {
            "due": 0,
            "sent": 0,
            "already_sent": 0,
            "skipped": 0,
            "send_failed": 0,
            "result_unknown": 0,
        },
        "status": "UNKNOWN",
        "reason": "SCAN_UNAVAILABLE",
    }
    if not _SUPPORTED_PLATFORM:
        return {
            **report,
            "status": "UNKNOWN",
            "reason": "UNSUPPORTED_FILESYSTEM",
        }
    if type(limit) is not int or not 1 <= limit <= 256:
        return {**report, "status": "UNKNOWN", "reason": "INVALID_INPUT"}
    from bt_api_py import trading_calendar_schedule_once as schedule

    if clock_source is not None:
        clock_instant = clock_source()
    period = _period(clock_instant, clock_error_bound)
    manual_ready = clock_evidence == "PROVEN" and period is not None
    constants = {
        "root": root,
        "config": config,
        "journal_path": journal_path,
        "sender": sender,
        "clock_instant": clock_instant,
        "clock_error_bound": clock_error_bound,
        "clock_evidence": clock_evidence,
        "clock_source": clock_source,
        "config_guard": config_guard,
    }

    def journal(kind, event_id, row_period):
        if journal_path is None:
            absent = {"status": "ABSENT", "journal_status": None, "period": None}
            return absent if dry_run else {**absent, "status": "JOURNAL_UNAVAILABLE"}
        try:
            return read_notification_outcome(
                journal_path=journal_path, kind=kind, event_id=event_id, period=row_period
            )
        except Exception:
            return {"status": "JOURNAL_UNAVAILABLE", "journal_status": None, "period": None}

    def account(
        kind, operation_id, event_id, dedupe_key, status, reason, outcome, candidate_id=None
    ):
        counts = report["counts"]
        if status == "DUE":
            counts["due"] += 1
        elif status == "SENT":
            counts["sent"] += 1
        elif status == "ALREADY_SENT":
            counts["already_sent"] += 1
        elif status == "SEND_FAILED":
            counts["send_failed"] += 1
        elif status == "RESULT_UNKNOWN":
            counts["result_unknown"] += 1
        else:
            counts["skipped"] += 1
        entry = {
            "kind": kind,
            "operation_id": operation_id,
            "candidate_id": candidate_id,
            "event_id": event_id,
            "dedupe_key": dedupe_key,
            "journal_outcome": outcome,
            "status": status,
            "reason": reason,
        }
        if status == "SEND_FAILED":
            report["send_failures"].append(entry)
        elif status == "RESULT_UNKNOWN":
            report["result_unknown"].append(entry)
        return entry

    def send_manual():
        if dry_run:
            return "DUE", "DRY_RUN_ELIGIBLE"
        if config is None or not config.enabled:
            return "SKIPPED", "DISABLED"
        if not manual_ready:
            return "SKIPPED", "CLOCK_UNKNOWN"
        result = run_scheduled(dry_run=False, **constants)
        return _scan_send_classify(result)

    def send_publication(operation_id):
        if dry_run:
            return "DUE", "DRY_RUN_ELIGIBLE"
        if config is None or not config.enabled:
            return "SKIPPED", "DISABLED"
        result = run_scheduled_publication(dry_run=False, operation_id=operation_id, **constants)
        return _scan_send_classify(result)

    try:
        active = schedule.inspect_active(root=root, filesystem=runs.TRUSTED_LOCAL_POSIX)
    except Exception:
        active = {"status": "UNKNOWN", "reason": "ACTIVE_OPERATION_UNAVAILABLE"}
    if active.get("status") == "PENDING_REVIEW":
        ids = (
            active.get("operation_id"),
            active.get("run_id"),
            active.get("review_id"),
            active.get("review_payload_sha256"),
        )
        if (
            not all(runs._id(value) for value in ids[:3])
            or type(ids[3]) is not str
            or _SHA.fullmatch(ids[3]) is None
        ):
            report["pending_review"] = {
                "status": "REVIEW_STATE_UNKNOWN",
                "reason": "REVIEW_IDENTITY_INVALID",
            }
        elif not manual_ready:
            report["pending_review"] = account(
                "manual",
                ids[0],
                ids[2],
                f"{ids[2]}:CLOCK_UNKNOWN",
                "SKIPPED",
                "CLOCK_UNKNOWN",
                None,
                candidate_id=(active.get("identity_bundle") or {}).get("candidate_id"),
            )
        else:
            decision, outcome = _scan_classify(journal("manual", ids[2], period))
            if decision == "DUE":
                decision, reason = send_manual()
            else:
                reason = outcome
            report["pending_review"] = account(
                "manual",
                ids[0],
                ids[2],
                f"{ids[2]}:{period}",
                decision,
                reason,
                outcome,
                candidate_id=(active.get("identity_bundle") or {}).get("candidate_id"),
            )
    elif active.get("status") == "NONE":
        report["pending_review"] = {"status": "NO_PENDING_REVIEW", "reason": "NO_ACTIVE_OPERATION"}
    elif active.get("status") == "UNKNOWN" and active.get("reason") in {
        "POLICY_CHANGED_REQUIRES_FRESH_UPDATE",
        "ORIGINAL_REVIEW_STARTED_OR_COMPLETED",
    }:
        report["pending_review"] = {
            "status": "SUPPRESSED",
            "reason": active["reason"],
        }
    else:
        report["pending_review"] = {
            "status": "REVIEW_STATE_UNKNOWN",
            "reason": active.get("reason", "ACTIVE_OPERATION_UNAVAILABLE"),
        }

    try:
        scanned = schedule.scan_publications(
            root=root, filesystem=runs.TRUSTED_LOCAL_POSIX, limit=limit
        )
    except Exception:
        scanned = {"status": "UNKNOWN", "reason": "PUBLICATION_SCAN_UNAVAILABLE"}
    report["publication_scan"] = {
        "status": scanned.get("status", "UNKNOWN"),
        "reason": scanned.get("reason", "PUBLICATION_SCAN_UNAVAILABLE"),
        "unverified": scanned.get("unverified"),
        "scan_limit": scanned.get("scan_limit"),
    }
    for item in scanned.get("operations", []) if type(scanned.get("operations")) is list else []:
        operation_id = item.get("operation_id") if type(item) is dict else None
        event_id = item.get("publication_id") if type(item) is dict else None
        if not runs._id(operation_id) or not _publication_id(event_id):
            report["publications"].append(
                {
                    "operation_id": operation_id,
                    "event_id": event_id,
                    "status": "RESULT_UNKNOWN",
                    "reason": "PUBLICATION_IDENTITY_INVALID",
                }
            )
            report["counts"]["result_unknown"] += 1
            report["result_unknown"].append(
                {
                    "kind": "auto",
                    "operation_id": operation_id,
                    "event_id": event_id,
                    "dedupe_key": str(event_id),
                    "journal_outcome": None,
                    "status": "RESULT_UNKNOWN",
                    "reason": "PUBLICATION_IDENTITY_INVALID",
                }
            )
            continue
        decision, outcome = _scan_classify(journal("auto", event_id, ""))
        if decision == "DUE":
            decision, reason = send_publication(operation_id)
        else:
            reason = outcome
        report["publications"].append(
            account(
                "auto",
                operation_id,
                event_id,
                event_id,
                decision,
                reason,
                outcome,
                candidate_id=item.get("candidate_id"),
            )
        )

    skipped = [
        entry
        for entry in [report["pending_review"], *report["publications"]]
        if type(entry) is dict and entry.get("status") == "SKIPPED"
    ]
    scan_status = report["publication_scan"]["status"]
    if (
        report["result_unknown"]
        or scan_status == "UNKNOWN"
        or scan_status == "INCOMPLETE"
    ):
        report["status"] = "UNKNOWN"
        report["reason"] = (
            "RESULT_UNKNOWN"
            if report["result_unknown"]
            else "PUBLICATION_SCAN_UNAVAILABLE"
            if scan_status == "UNKNOWN"
            else "PUBLICATION_SCAN_INCOMPLETE"
        )
    elif report["send_failures"] or report["pending_review"].get("status") in {
        "REVIEW_STATE_UNKNOWN",
        "REVIEW_IDENTITY_INVALID",
    }:
        report["status"] = "INCOMPLETE"
        report["reason"] = (
            "SEND_FAILED" if report["send_failures"] else "REVIEW_STATE_UNKNOWN"
        )
    elif report["counts"]["due"]:
        report["status"] = "DUE"
        report["reason"] = "DRY_RUN_ELIGIBLE"
    elif skipped:
        report["status"] = "INCOMPLETE"
        report["reason"] = "SEND_SKIPPED"
    else:
        report["status"] = "COMPLETE"
        report["reason"] = "SCAN_COMPLETE"
    return report


def main(argv=None):
    clock_status = "UNKNOWN"
    if not _SUPPORTED_PLATFORM:
        return _output_result(NotificationResult("NOT_SENT", "UNSUPPORTED_FILESYSTEM"), clock_status)
    arguments = sys.argv[1:] if argv is None else argv
    scan_requested = "--scan-missed" in arguments
    report = None
    result = None
    try:
        parser = _Parser(add_help=False)
        parser.add_argument("--calendar-root", required=True)
        parser.add_argument("--run-id")
        ids = parser.add_mutually_exclusive_group(required=True)
        ids.add_argument("--review-id")
        ids.add_argument("--publication-id")
        ids.add_argument("--schedule-active", action="store_true")
        ids.add_argument("--schedule-operation-id")
        ids.add_argument("--scan-missed", action="store_true")
        parser.add_argument("--review-sha256")
        parser.add_argument("--smtp-config")
        parser.add_argument("--journal-path")
        parser.add_argument("--simulation-clock-config")
        parser.add_argument("--dry-run", action="store_true")
        parser.add_argument("--trusted-local-posix", action="store_true", required=True)
        args = parser.parse_args(argv)
        if (
            (args.publication_id and args.review_sha256)
            or (args.schedule_active and (args.run_id or args.review_sha256))
            or (args.schedule_operation_id and (args.run_id or args.review_sha256))
            or (args.scan_missed and (args.run_id or args.review_sha256))
            or (
                not args.schedule_active
                and not args.schedule_operation_id
                and not args.scan_missed
                and not args.run_id
            )
        ):
            raise ValueError("invalid arguments")
        config = None
        config_version = None
        if not args.dry_run and args.smtp_config:
            config, config_version = _read_config(args.smtp_config)
        clock_bound = None
        clock_version = None
        if not args.dry_run and args.simulation_clock_config:
            if not (args.review_id or args.schedule_active or args.scan_missed):
                raise ValueError("clock declaration is for manual reminders")
            clock_bound, clock_version = _read_clock_declaration(args.simulation_clock_config)
            clock_status = "CALLER_DECLARED_NOT_AUTHENTICATED"

        def config_guard():
            if config is not None and _read_config(args.smtp_config) != (config, config_version):
                return False
            return clock_bound is None or _read_clock_declaration(args.simulation_clock_config) == (
                clock_bound,
                clock_version,
            )

        common = {
            "root": args.calendar_root,
            "config": config,
            "journal_path": args.journal_path,
            "dry_run": args.dry_run,
            "clock_evidence": "PROVEN" if clock_bound is not None else "UNKNOWN",
            "clock_error_bound": clock_bound,
            "clock_source": (lambda: datetime.now(UTC)) if clock_bound is not None else None,
            "config_guard": config_guard,
        }
        if args.scan_missed:
            report = scan_missed(**common)
        elif args.schedule_active:
            result = run_scheduled(**common)
        elif args.schedule_operation_id:
            result = run_scheduled_publication(**common, operation_id=args.schedule_operation_id)
        else:
            result = run(
                **common,
                run_id=args.run_id,
                kind="manual" if args.review_id else "auto",
                event_id=args.review_id or args.publication_id,
                review_sha256=args.review_sha256,
            )
    except Exception:
        if scan_requested:
            report = {
                "environment": "SIMULATION_ONLY",
                "source_authentication": "NOT_PROVEN",
                "delivery": "NOT_PROVEN",
                "clock_evidence": clock_status,
                "status": "UNKNOWN",
                "reason": "ARGUMENT_OR_CONFIG_UNAVAILABLE",
            }
        else:
            result = NotificationResult("NOT_SENT", "ARGUMENT_OR_CONFIG_UNAVAILABLE")
    if report is not None:
        return _output_scan(report)
    return _output_result(result, clock_status)


def _output_scan(report):
    print(json.dumps(report, sort_keys=True))
    return 0 if report.get("status") in {"COMPLETE", "DUE"} else 1


def _output_result(result, clock_status):
    print(
        json.dumps(
            {
                "status": result.status,
                "reason": result.reason,
                "journal_status": result.journal_status,
                "environment": "SIMULATION_ONLY",
                "clock_evidence": clock_status,
                "source_authentication": "NOT_PROVEN",
                "delivery": "NOT_PROVEN",
            },
            sort_keys=True,
        )
    )
    return (
        0
        if (
            result.status == "ACCEPTED"
            or (result.status == "DUPLICATE" and result.journal_status == "ACCEPTED")
            or result.reason == "DRY_RUN_ELIGIBLE"
        )
        else 1
    )


if __name__ == "__main__":
    raise SystemExit(main())
