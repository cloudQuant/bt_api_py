"""Read-only preparation of a MOCK interruption proposal.

CURRENT, receipts, policy and review material are each re-observed; they are not
one atomic snapshot. This is OBSERVATION_ONLY, not a confirmation, publication,
source authentication, account authority, or a clock assertion. A future writer
must revalidate all inputs under its writer lock. The injected factory is a
trusted simulation validator; it does not authenticate a venue or source.

Only existing cooperating local POSIX directories, files and lock sidecars are
read. This module creates no directory, lock, review, receipt or version file.
Callers must not mutate inputs while initial copies are taken. No general secret
detector is provided; inputs are public synthetic material.
"""

from __future__ import annotations

from bt_api_py._calendar_compat import fcntl
import hashlib
import json
import os
from bt_api_py import _calendar_fdops as fdops
import stat
from dataclasses import asdict
from pathlib import Path

from bt_api_py import trading_calendar_interruption_review_simulation as review
from bt_api_py.trading_calendar_interruption_compare_simulation import (
    compare_simulated_interruption,
)
from bt_api_py.trading_calendar_migration_barrier import check_migration_barrier
from bt_api_py.trading_calendar_preview_reports import TRUSTED_LOCAL_POSIX
from bt_api_py.trading_calendar_store import (
    CalendarReadBudget,
    TradingCalendarStore,
    _canonical,
    _coverage_bundle,
    _decode,
    _digest,
    _identifier,
)

_BUDGET = CalendarReadBudget(32 * 1024 * 1024, 256 * 1024 * 1024, 256)
_DIFF_LIMIT = 16 * 1024 * 1024
_LABELS = {
    "mode": "SIMULATION_ONLY",
    "observation": "OBSERVATION_ONLY",
    "current_policy_atomicity": "NOT_PROVEN",
    "source_authentication": "NOT_PROVEN",
    "identity_authentication": "NOT_PROVEN",
    "clock_authentication": "NOT_PROVEN",
    "receipt_authentication": "NOT_PROVEN",
    "publication": "NOT_AUTHORIZED",
    "durability": "NOT_PROVEN",
    "format_migration": "NOT_ATTEMPTED",
}


def _sha(value):
    return hashlib.sha256(value).hexdigest()


def _root_lock_identity(root):
    """Capture existing root and lock identities without creating either."""
    entry = os.lstat(root)
    if not stat.S_ISDIR(entry.st_mode):
        raise ValueError("root")
    fd = fdops.open(root, os.O_RDONLY | fdops.O_DIRECTORY | fdops.O_NOFOLLOW)
    try:
        opened = fdops.fstat(fd)
        lock = fdops.stat(".lock", dir_fd=fd, follow_symlinks=False)
        after = os.lstat(root)
        identity = (entry.st_dev, entry.st_ino)
        if (
            (opened.st_dev, opened.st_ino) != identity
            or (after.st_dev, after.st_ino) != identity
            or not stat.S_ISDIR(after.st_mode)
            or not stat.S_ISREG(lock.st_mode)
        ):
            raise ValueError("root or lock changed")
        return identity, (lock.st_dev, lock.st_ino)
    finally:
        fdops.close(fd)


def _policy_readonly(store, current):
    """Read policy and fingerprint the validated receipt chain under a shared lock."""
    if not all(hasattr(os, key) for key in ("O_NOFOLLOW", "O_DIRECTORY", "O_NONBLOCK")):
        raise ValueError("unsupported filesystem")
    root_fd = lock_fd = None
    locked = False
    try:
        root_before = os.lstat(store.root)
        if not stat.S_ISDIR(root_before.st_mode):
            raise ValueError("root")
        root_fd = fdops.open(store.root, os.O_RDONLY | fdops.O_DIRECTORY | fdops.O_NOFOLLOW)
        root_identity = (root_before.st_dev, root_before.st_ino)
        if (fdops.fstat(root_fd).st_dev, fdops.fstat(root_fd).st_ino) != root_identity:
            raise ValueError("root changed")
        lock_fd = fdops.open(".lock", os.O_RDONLY | fdops.O_NOFOLLOW | fdops.O_NONBLOCK, dir_fd=root_fd)
        lock_info = fdops.fstat(lock_fd)
        if not stat.S_ISREG(lock_info.st_mode):
            raise ValueError("lock")
        lock_identity = (lock_info.st_dev, lock_info.st_ino)
        fcntl.flock(lock_fd, fcntl.LOCK_SH | fcntl.LOCK_NB)
        locked = True

        def stable():
            root = os.lstat(store.root)
            lock = fdops.stat(".lock", dir_fd=root_fd, follow_symlinks=False)
            return (
                stat.S_ISDIR(root.st_mode)
                and (root.st_dev, root.st_ino) == root_identity
                and stat.S_ISREG(lock.st_mode)
                and (lock.st_dev, lock.st_ino) == lock_identity
                and (fdops.fstat(root_fd).st_dev, fdops.fstat(root_fd).st_ino) == root_identity
                and (fdops.fstat(lock_fd).st_dev, fdops.fstat(lock_fd).st_ino) == lock_identity
            )

        if not stable():
            raise ValueError("store changed")
        check_migration_barrier(store.root, lock_fd)
        store._layout()
        if store._pending_exists() or store._policy_change_pending():
            raise ValueError("pending")
        total = 0

        def bounded(path):
            nonlocal total
            remaining = _BUDGET.max_total_bytes - total
            if remaining <= 0:
                raise ValueError("read budget")
            data = store._read_bounded(path, min(_BUDGET.max_file_bytes, remaining))
            total += len(data)
            return data

        pointer_bytes = bounded(store.root / "CURRENT.json")
        if (
            store._load(pointer_bytes, _reader=bounded, _max_publications=_BUDGET.max_publications)
            != current
        ):
            raise ValueError("current changed")
        store._validate_chain_receipts(
            pointer_bytes, _reader=bounded, _max_publications=_BUDGET.max_publications
        )
        pointer, receipts = _decode(pointer_bytes), []
        while pointer is not None:
            if len(receipts) >= _BUDGET.max_publications:
                raise ValueError("receipt budget")
            publication_id = pointer["publication_id"]
            event_bytes = bounded(store.root / "publications" / f"{publication_id}.json")
            if _sha(event_bytes) != pointer["publication_sha256"]:
                raise ValueError("event changed")
            event = _decode(event_bytes)
            receipt = bounded(store.root / "receipts" / f"{publication_id}.json")
            receipt_record = _decode(receipt)
            if (
                receipt_record["publication_id"] != publication_id
                or receipt_record["version_id"] != event["version_id"]
                or receipt_record["pointer"] != pointer
            ):
                raise ValueError("receipt changed")
            receipts.append([publication_id, _sha(receipt)])
            previous = event["previous"]
            pointer = (
                None
                if previous is None
                else {"schema": "bt_api.calendar-store.current/1", **previous}
            )
        policy = store._review_policy_locked()
        if policy is None or policy != store._review_policy_locked():
            raise ValueError("policy")
        if (
            store._pending_exists()
            or store._policy_change_pending()
            or not stable()
            or bounded(store.root / "CURRENT.json") != pointer_bytes
        ):
            raise ValueError("store changed")
        check_migration_barrier(store.root, lock_fd)
        return asdict(policy), _sha(_canonical(receipts))
    finally:
        if lock_fd is not None:
            try:
                if locked:
                    fcntl.flock(lock_fd, fcntl.LOCK_UN)
            finally:
                fdops.close(lock_fd)
        if root_fd is not None:
            fdops.close(root_fd)


def _target(candidate):
    content_sha = _sha(_canonical(candidate["content"]))
    manifest = {
        "schema": "bt_api.calendar-simulation-coverage/1",
        "snapshot_sha256": content_sha,
        "coverage": candidate["coverage"],
    }
    manifest_bytes = _canonical(manifest)
    manifest_sha = _sha(manifest_bytes)
    binding = {
        "schema": "bt_api.calendar-simulation-binding/1",
        "snapshot_sha256": content_sha,
        "manifest_sha256": manifest_sha,
    }
    binding_bytes = _canonical(binding)
    binding_sha = _sha(binding_bytes)
    _coverage_bundle(
        _canonical(candidate["content"]), manifest_bytes, manifest_sha, binding_bytes, binding_sha
    )
    version = {
        "schema": "bt_api.calendar-store.snapshot/7",
        "content": candidate["content"],
        "manifest": manifest,
        "manifest_sha256": manifest_sha,
        "binding": binding,
        "binding_sha256": binding_sha,
    }
    return {
        "snapshot_schema": version["schema"],
        "content_sha256": content_sha,
        "manifest_sha256": manifest_sha,
        "binding_sha256": binding_sha,
        "version_sha256": _sha(_canonical(version)),
    }


def _baseline_matches(current, body, review_schema, publication_id, publication_sha256):
    baseline = body["input"]["baseline"]
    declaration = baseline["declaration"]
    wanted_schema = (
        "bt_api.calendar-store.snapshot/2"
        if review_schema == review._SCHEMA
        else "bt_api.calendar-store.snapshot/7"
    )
    if (
        current.publication_id != publication_id
        or current.publication_sha256 != publication_sha256
        or current.snapshot_schema != wanted_schema
        or current.coverage_status != "DECLARED_UNVERIFIED"
        or current.simulation_provenance is not None
        or declaration.get("simulation_provenance") is not None
        or current.version_id != baseline["version_id"]
        or current.version_sha256 != baseline["version_sha256"]
        or current.content_sha256 != baseline["content_sha256"]
        or current.coverage_manifest_sha256 != baseline["manifest_sha256"]
        or current.binding_sha256 != baseline["binding_sha256"]
        or current.content != declaration["content"]
        or current.coverage_manifest_bytes != _canonical(baseline["manifest"])
        or current.binding_bytes != _canonical(baseline["binding"])
        or json.loads(current.coverage_manifest_bytes)["coverage"] != declaration["coverage"]
        or (
            review_schema == review._SCHEMA_V6
            and baseline["snapshot_schema"] != current.snapshot_schema
        )
    ):
        raise ValueError("baseline unavailable")


def prepare(
    *,
    root,
    candidate_id,
    review_schema,
    expected_candidate_payload_sha256,
    expected_current_publication_id,
    expected_current_publication_sha256,
    filesystem,
    mode,
    snapshot_factory,
):
    """Build a detached proposal from two matching observations; never publish."""
    unknown = {"status": "UNKNOWN", "reason": "OBSERVATION_UNAVAILABLE", **_LABELS}
    try:
        if (
            mode != "SIMULATION_ONLY"
            or filesystem != TRUSTED_LOCAL_POSIX
            or type(root) is not str
            or not Path(root).is_absolute()
            or review_schema not in {review._SCHEMA, review._SCHEMA_V6}
            or not _digest(expected_candidate_payload_sha256)
            or not _identifier(expected_current_publication_id)
            or not _digest(expected_current_publication_sha256)
            or not callable(snapshot_factory)
        ):
            raise ValueError("arguments")
        store = TradingCalendarStore(root)
        root_identity = _root_lock_identity(root)
        args = {
            "root": root,
            "candidate_id": candidate_id,
            "filesystem": filesystem,
            "snapshot_factory": snapshot_factory,
            "mode": mode,
            "schema": review_schema,
        }
        body, payload = review._load_verified_material(**args)
        if payload != expected_candidate_payload_sha256:
            raise ValueError("candidate identity")
        current = store.read_current_with_consistent_receipts(budget=_BUDGET).snapshot
        policy, receipt_identity = _policy_readonly(store, current)
        _baseline_matches(
            current,
            body,
            review_schema,
            expected_current_publication_id,
            expected_current_publication_sha256,
        )
        baseline_declaration = {
            "content": current.content,
            "coverage": json.loads(current.coverage_manifest_bytes)["coverage"],
            "data_origin": "MOCK",
            "simulation_provenance": None,
        }
        candidate = body["input"]["candidate"]
        difference = compare_simulated_interruption(
            baseline_declaration, candidate, mode=mode, snapshot_factory=snapshot_factory
        )
        if (
            difference.get("status") not in {"SAME", "CHANGED"}
            or difference != body["difference"]
            or _sha(_canonical(difference)) != body["difference_sha256"]
        ):
            raise ValueError("difference")
        target = _target(candidate)
        before = {
            "publication_id": current.publication_id,
            "publication_sha256": current.publication_sha256,
            "version_id": current.version_id,
            "version_sha256": current.version_sha256,
            "snapshot_sha256": current.content_sha256,
            "manifest_sha256": current.coverage_manifest_sha256,
            "binding_sha256": current.binding_sha256,
        }
        after = {
            "snapshot_sha256": target["content_sha256"],
            "store_snapshot_sha256": target["version_sha256"],
            "manifest_sha256": target["manifest_sha256"],
            "binding_sha256": target["binding_sha256"],
        }
        publication_difference = {
            "schema": "bt_api.calendar-interruption-publication-difference/1",
            "candidate_id": candidate_id,
            "candidate_payload_sha256": payload,
            "before": before,
            "after": after,
            "declaration_difference": difference,
        }
        encoded_difference = _canonical(publication_difference)
        if len(encoded_difference) > _DIFF_LIMIT:
            raise ValueError("difference budget")
        body_again, payload_again = review._load_verified_material(**args)
        current_again = store.read_current_with_consistent_receipts(budget=_BUDGET).snapshot
        policy_again, receipt_identity_again = _policy_readonly(store, current_again)
        if (
            body_again != body
            or payload_again != payload
            or current_again != current
            or policy_again != policy
            or receipt_identity_again != receipt_identity
            or _root_lock_identity(root) != root_identity
        ):
            raise ValueError("observation changed")
        status = (
            "SAME_DECLARATION"
            if difference["status"] == "SAME"
            else "NEEDS_CONFIRMATION"
            if policy["mode"] == "manual"
            else "AUTO_DECLARED"
        )
        proposal = {
            "schema": "bt_api.calendar-interruption-prepare/1",
            "status": status,
            "review_schema": review_schema,
            "candidate_id": candidate_id,
            "candidate_payload_sha256": payload,
            "review_input_sha256": body["input_sha256"],
            "candidate_declaration_sha256": body["candidate_declaration_sha256"],
            "receipt_chain_sha256": receipt_identity,
            "baseline": {
                **before,
                "snapshot_schema": current.snapshot_schema,
                "sequence": current.sequence,
            },
            "policy": policy,
            "target": target,
            "declaration_difference_sha256": body["difference_sha256"],
            "publication_difference_sha256": _sha(encoded_difference),
            "publication_difference": publication_difference,
            **_LABELS,
        }
        proposal["proposal_sha256"] = _sha(_canonical(proposal))
        return proposal
    except Exception:
        return unknown
