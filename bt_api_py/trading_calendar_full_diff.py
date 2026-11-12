"""Bounded, read-only comparison of complete stored calendar declarations.

This observation is neither source authentication nor a publication receipt.
A later publisher must revalidate under its own writer lock. It assumes trusted,
cooperating local POSIX writers; ABA and power-loss durability are not proven.
Each consistent CURRENT read has its own budget; the two reads, inventory reads
and review bundle/source rereads do not share a single total I/O budget.
"""

from __future__ import annotations

import fcntl
import hashlib
import json
import os
import re
import unicodedata
from pathlib import Path
from urllib.parse import unquote_to_bytes, urlsplit

from bt_api_py import trading_calendar_candidate_inbox as inbox
from bt_api_py import trading_calendar_review_bundle as review
from bt_api_py.trading_calendar_snapshot_budget import check_snapshot_budget as _snapshot_preflight
from bt_api_py.trading_calendar_store import (
    _FULL_DIFFERENCE_BYTES,
    _LEGACY_DIFFERENCE_BYTES,
    _PUBLICATION_EVENT_BYTES,
    CalendarReadBudget,
    TradingCalendarStore,
    _canonical,
    _coverage_bundle,
    _decode,
    calendar_snapshot_digest,
)

_READ_BUDGET = CalendarReadBudget(96 * 1024 * 1024, 512 * 1024 * 1024, 256)
_OUTPUT_LIMIT = 32 * 1024 * 1024
_STORE_DIFFERENCE_BYTES = _FULL_DIFFERENCE_BYTES
_EVENT_LIMIT = _PUBLICATION_EVENT_BYTES
_INVENTORY_TOTAL_BYTES = 256 * 1024 * 1024
_LAYERS = ("sources", "trading_days", "trading_hours", "exceptions", "coverage")
_KEYS = {
    "sources": ("id",),
    "trading_days": ("venue", "day"),
    "trading_hours": ("venue", "kind", "product", "contracts", "valid_from", "valid_to"),
    "exceptions": ("venue", "kind", "product", "contract", "trading_day", "action", "window_id"),
    "coverage": ("venue",),
}
# CLI accepts only conservative prose. It cannot identify arbitrary secret values;
# inputs are required to be public declarations, as for review-bundle retention.
_UNSAFE_TEXT = re.compile(
    r"(?i)(?:[/\\@?=&%#]|password|passwd|secret|token|credential|authorization|api[_ -]?key|[\x00-\x1f\x7f])"
)
# These official hosts are listed in the iteration's local source matrix.
# Matching syntax does not authenticate the URL or digest.
_PUBLIC_SOURCE_DESCRIPTION = re.compile(r"(https://[^\s]+) sha256:[0-9a-f]{64}\Z")
_PUBLIC_SOURCE_HOSTS = frozenset(
    {
        "www.shfe.com.cn",
        "www.shfe.cn",
        "www.ine.cn",
        "www.czce.com.cn",
        "www.dce.com.cn",
        "www.cffex.com.cn",
        "www.e-cffex.com.cn",
        "e-cffex.com.cn",
        "www.gfex.com.cn",
    }
)
_SAFE_ENCODED_SEGMENT = re.compile(r"(?:[A-Za-z0-9_.()\-]|%[0-9A-Fa-f]{2})+\Z")
_ENCODED_DOT = re.compile(r"%2e", re.IGNORECASE)
_SECRET_WORD = re.compile(r"(?i)password|passwd|secret|token|credential|authorization|api[_-]?key")


def _inventory(store, snapshot):
    """Bounded check for entries outside the validated publication chain."""
    seen, versions = set(), set()
    total = 0
    event_id, expected = snapshot.publication_id, snapshot.publication_sha256
    while event_id is not None:
        if event_id in seen or len(seen) >= _READ_BUDGET.max_publications:
            raise ValueError("chain budget")
        seen.add(event_id)
        remaining = _INVENTORY_TOTAL_BYTES - total
        if remaining <= 0:
            raise ValueError("inventory byte budget")
        raw = store._read_bounded(
            store.root / "publications" / f"{event_id}.json", min(_EVENT_LIMIT, remaining)
        )
        total += len(raw)
        if hashlib.sha256(raw).hexdigest() != expected:
            raise ValueError("changed event")
        event = _decode(raw)
        if event.get("publication_id") != event_id:
            raise ValueError("changed event identity")
        versions.add(event["version_id"])
        previous = event["previous"]
        if previous is None:
            event_id = None
        else:
            event_id, expected = previous["publication_id"], previous["publication_sha256"]
    for dirname, ids in (("publications", seen), ("receipts", seen), ("versions", versions)):
        names = set()
        with os.scandir(store.root / dirname) as entries:
            for entry in entries:
                if len(names) >= _READ_BUDGET.max_publications or not entry.is_file(
                    follow_symlinks=False
                ):
                    raise ValueError("invalid inventory")
                names.add(entry.name)
        if names != {f"{item}.json" for item in ids}:
            raise ValueError("orphan inventory")


def _current(root):
    store = TradingCalendarStore(root)
    # Hold the store lock over both the receipt-consistent read and inventory.
    fd = os.open(Path(root) / ".lock", os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    try:
        fcntl.flock(fd, fcntl.LOCK_SH | fcntl.LOCK_NB)
        observation = store.read_current_with_consistent_receipts(budget=_READ_BUDGET)
        _inventory(store, observation.snapshot)
        return observation.snapshot
    finally:
        os.close(fd)


def _artifacts(content, manifest_bytes, manifest_sha, binding_bytes, binding_sha):
    review._bounded(content)
    content_bytes = _canonical(content)
    manifest, _ = _coverage_bundle(
        content_bytes, manifest_bytes, manifest_sha, binding_bytes, binding_sha
    )
    return hashlib.sha256(content_bytes).hexdigest(), manifest


def _normalized(content, manifest):
    # This validates exact fields, references, duplicate keys and per-venue
    # conflicts without applying CTP's 1000-row cap to all six venues at once.
    result = inbox._validated_baseline(content)
    venues = {row["venue"] for row in manifest["coverage"]}
    if venues != {row["venue"] for row in result["trading_days"]} or any(
        row["venue"] not in venues
        for layer in ("trading_hours", "exceptions")
        for row in result[layer]
    ):
        raise ValueError("manifest venue mismatch")
    _snapshot_preflight(result, venues)
    inbox._check_explicit_intervals(result)
    from bt_api_ctp.calendar_snapshot import CalendarSnapshot

    for venue in venues:
        shard = {"sources": result["sources"]}
        for layer in ("trading_days", "trading_hours", "exceptions"):
            shard[layer] = [row for row in result[layer] if row["venue"] == venue]
        CalendarSnapshot(shard, calendar_ref="full-diff-validation")
    return {**result, "coverage": manifest["coverage"]}


def _key(layer, row):
    return tuple(tuple(row[k]) if k == "contracts" else row[k] for k in _KEYS[layer])


def _changes(before, after):
    output = {}
    for layer in _LAYERS:
        old = {_key(layer, row): row for row in before[layer]}
        new = {_key(layer, row): row for row in after[layer]}
        if len(old) != len(before[layer]) or len(new) != len(after[layer]):
            raise ValueError("duplicate stable key")
        # JSON bytes order heterogeneous None/string keys deterministically.
        changes = []
        for key in sorted(
            old.keys() | new.keys(), key=lambda item: json.dumps(item, ensure_ascii=True)
        ):
            prior, next_ = old.get(key), new.get(key)
            if prior != next_:
                changes.append(
                    {
                        "key": [list(item) if type(item) is tuple else item for item in key],
                        "operation": "added"
                        if prior is None
                        else "removed"
                        if next_ is None
                        else "modified",
                        "before": prior,
                        "after": next_,
                    }
                )
        output[layer] = changes
    return output


def _public_source_description(value):
    match = _PUBLIC_SOURCE_DESCRIPTION.fullmatch(value)
    if match is None:
        return False
    url = match.group(1)
    if len(url) > 2048 or not url.isascii() or any(c in url for c in ("\\", "?", "#", "@")):
        return False
    try:
        parsed = urlsplit(url)
    except ValueError:
        return False
    if (
        parsed.scheme != "https"
        or parsed.netloc not in _PUBLIC_SOURCE_HOSTS
        or parsed.username is not None
        or parsed.password is not None
        or parsed.query
        or parsed.fragment
        or not parsed.path.startswith("/")
    ):
        return False
    path = parsed.path[1:]
    if path.endswith("/"):
        path = path[:-1]
    if not path:
        return True
    for part in path.split("/"):
        if _SAFE_ENCODED_SEGMENT.fullmatch(part) is None or _ENCODED_DOT.search(part):
            return False
        try:
            decoded = unquote_to_bytes(part).decode("utf-8", "strict")
        except UnicodeDecodeError:
            return False
        # Reject alternate Unicode forms, encoded separators/control/query
        # characters and nested percent escapes. Decoding is only for checking;
        # the original URL is always retained in the returned difference.
        if (
            not decoded
            or not decoded[0].isalnum()
            or unicodedata.normalize("NFKC", decoded) != decoded
            or _SECRET_WORD.search(decoded)
            or any(not (c.isalnum() or c in "_.()-") for c in decoded)
        ):
            return False
    return True


def _safe_output(value, root, *, source_changes=False):
    if review.detail._sensitive(value, str(root)):
        return False
    pending = [(value, ())]
    while pending:
        item, path = pending.pop()
        if type(item) is str and _UNSAFE_TEXT.search(item):
            source_description = (
                source_changes
                and len(path) == 4
                and path[0] == "sources"
                and type(path[1]) is int
                and path[2] in {"before", "after"}
                and path[3] == "description"
            )
            if not source_description or not _public_source_description(item):
                return False
        if type(item) is dict:
            pending.extend((child, (*path, key)) for key, child in item.items())
        elif type(item) in (list, tuple):
            pending.extend((child, (*path, index)) for index, child in enumerate(item))
    return True


def compare_full(
    *,
    root,
    candidate_id,
    filesystem,
    candidate_payload_sha256=None,
    current_publication_id=None,
    current_publication_sha256=None,
):
    """Compare all four stored layers and manifest coverage, without publishing.

    Optional expected identities only restrict the observation. Source text is
    not stored in the published snapshot and is never compared or printed.
    Results contain full changed rows and are not publication credentials.
    The Python API returns unredacted caller-declared data and is intended only
    for public input; it cannot detect arbitrary secrets or private paths.
    ``after.snapshot_sha256`` hashes the four layers, while
    ``after.store_snapshot_sha256`` hashes the covered Store envelope, in the
    same manner as ``before.version_sha256``.
    """
    unknown = {
        "status": "UNKNOWN",
        "reason": "INVALID_OR_UNAVAILABLE",
        "publication": "NOT_AUTHORIZED",
        "first_publication": "FIRST_PUBLICATION_NOT_PROVEN",
    }
    try:
        if not review._valid(root, filesystem, candidate_id):
            return unknown
        if candidate_payload_sha256 is not None and (
            type(candidate_payload_sha256) is not str
            or not review._SHA256.fullmatch(candidate_payload_sha256)
        ):
            return unknown
        if (current_publication_id is None) != (current_publication_sha256 is None):
            return unknown
        if current_publication_id is not None and (
            type(current_publication_id) is not str
            or not review._SAFE_IDENTIFIER.fullmatch(current_publication_id)
            or type(current_publication_sha256) is not str
            or not review._SHA256.fullmatch(current_publication_sha256)
        ):
            return unknown
        first = review.show(root=root, candidate_id=candidate_id, filesystem=filesystem)
        if first.get("status") != "VISIBLE_UNAUTHENTICATED" or (
            candidate_payload_sha256 is not None
            and first["payload_sha256"] != candidate_payload_sha256
        ):
            return {**unknown, "reason": "CANDIDATE_UNAVAILABLE_OR_CHANGED"}
        body = first["bundle"]
        if body["schema"] not in {review._SCHEMA, review._SCHEMA_REFERENCES}:
            return {**unknown, "reason": "OLD_CANDIDATE_SCHEMA"}
        report = body["report"]
        composite = report.get("composite")
        if type(composite) is not dict or set(composite) != {
            "snapshot",
            "snapshot_sha256",
            "manifest",
            "manifest_sha256",
            "binding",
            "binding_sha256",
        }:
            return {**unknown, "reason": "COMPOSITE_UNAVAILABLE"}
        after_sha, after_manifest = _artifacts(
            composite["snapshot"],
            _canonical(composite["manifest"]),
            composite["manifest_sha256"],
            _canonical(composite["binding"]),
            composite["binding_sha256"],
        )
        if after_sha != composite["snapshot_sha256"]:
            raise ValueError("snapshot digest")
        after_store_snapshot_sha = calendar_snapshot_digest(
            composite["snapshot"],
            coverage_manifest_bytes=_canonical(composite["manifest"]),
            coverage_manifest_sha256=composite["manifest_sha256"],
            binding_bytes=_canonical(composite["binding"]),
            binding_sha256=composite["binding_sha256"],
        )
        try:
            current = _current(root)
        except Exception:
            return {**unknown, "reason": "CURRENT_UNAVAILABLE_OR_UNCERTAIN"}
        if current_publication_id is not None and (
            current.publication_id,
            current.publication_sha256,
        ) != (current_publication_id, current_publication_sha256):
            return {**unknown, "reason": "CURRENT_IDENTITY_MISMATCH"}
        if current.coverage_status != "DECLARED_UNVERIFIED" or current.version_sha256 is None:
            return {**unknown, "reason": "CURRENT_COVERAGE_NOT_DECLARED"}
        before_sha, before_manifest = _artifacts(
            current.content,
            current.coverage_manifest_bytes,
            current.coverage_manifest_sha256,
            current.binding_bytes,
            current.binding_sha256,
        )
        if before_sha != current.content_sha256:
            raise ValueError("current digest")
        before, after = (
            _normalized(current.content, before_manifest),
            _normalized(composite["snapshot"], after_manifest),
        )
        changes = _changes(before, after)
        identity = {
            "candidate_id": candidate_id,
            "candidate_payload_sha256": first["payload_sha256"],
            "before": {
                "publication_id": current.publication_id,
                "publication_sha256": current.publication_sha256,
                "version_id": current.version_id,
                "version_sha256": current.version_sha256,
                "snapshot_sha256": before_sha,
                "manifest_sha256": current.coverage_manifest_sha256,
                "binding_sha256": current.binding_sha256,
            },
            "after": {
                "snapshot_sha256": after_sha,
                "store_snapshot_sha256": after_store_snapshot_sha,
                "manifest_sha256": composite["manifest_sha256"],
                "binding_sha256": composite["binding_sha256"],
            },
        }
        diff = {
            "schema": "bt_api.calendar-full-declaration-difference/1",
            **identity,
            "changes": changes,
        }
        # Size preflight before building the final encoded output; no truncation.
        review._bounded(diff)
        difference_bytes = _canonical(diff)
        digest = hashlib.sha256(difference_bytes).hexdigest()
        result = {
            "status": "CHANGED" if any(changes.values()) else "SAME",
            "candidate_review_status": report["status"],
            "difference": diff,
            "difference_sha256": digest,
            "difference_size_bytes": len(difference_bytes),
            "store_difference_size_limit_bytes": _STORE_DIFFERENCE_BYTES,
            "store_difference_size_status": "COMPATIBLE"
            if len(difference_bytes) <= _STORE_DIFFERENCE_BYTES
            else "EXCEEDS",
            "legacy_store_difference_size_limit_bytes": _LEGACY_DIFFERENCE_BYTES,
            "legacy_store_difference_size_status": "COMPATIBLE"
            if len(difference_bytes) <= _LEGACY_DIFFERENCE_BYTES
            else "EXCEEDS",
            "store_difference_size_check": "SIZE_ONLY_NOT_PUBLICATION_VALIDATION",
            "comparison_scope": "COMPLETE_STORED_DECLARATIONS_ONLY",
            "source_text": "SOURCE_TEXT_NOT_STORED",
            "source_authentication": "NOT_PROVEN",
            "coverage_authentication": "NOT_PROVEN",
            "identity_authentication": "NOT_PROVEN",
            "candidate_and_difference_status": "CALLER_DECLARED_UNVERIFIED",
            "publication": "NOT_AUTHORIZED",
            "current_observation": "RECHECKED_NOT_PINNED",
            "publication_revalidation": "REQUIRED_UNDER_WRITER_LOCK",
        }
        if (
            len(json.dumps(result, ensure_ascii=True, sort_keys=True).encode("ascii"))
            > _OUTPUT_LIMIT
        ):
            return {**unknown, "reason": "OUTPUT_LIMIT"}
        second = review.show(root=root, candidate_id=candidate_id, filesystem=filesystem)
        try:
            latest = _current(root)
        except Exception:
            return {**unknown, "reason": "CURRENT_OR_CANDIDATE_CHANGED"}
        if second != first or latest != current:
            return {**unknown, "reason": "CURRENT_OR_CANDIDATE_CHANGED"}
        return result
    except Exception:
        return {**unknown, "reason": "INVALID_OR_UNAVAILABLE"}


def cli_view(result, root, *, show_details):
    """Display hashes/counts by default; details require conservative checks.

    These checks cannot identify an arbitrary secret disguised as public data.
    A digest always covers the complete unredacted difference, never this view.
    """
    if result.get("status") not in {"SAME", "CHANGED"}:
        return result
    difference = result["difference"]
    if show_details:
        identity = {
            key: value for key, value in difference.items() if key not in {"schema", "changes"}
        }
        if not _safe_output(difference["changes"], root, source_changes=True) or not _safe_output(
            identity, root
        ):
            return {
                "status": "UNKNOWN",
                "reason": "UNSAFE_TO_DISPLAY",
                "publication": "NOT_AUTHORIZED",
            }
        detailed = {**result, "details": "DISPLAYED_COMPLETE"}
        if (
            len(json.dumps(detailed, ensure_ascii=True, sort_keys=True).encode("ascii"))
            > _OUTPUT_LIMIT
        ):
            return {"status": "UNKNOWN", "reason": "OUTPUT_LIMIT", "publication": "NOT_AUTHORIZED"}
        return detailed
    return {
        "status": result["status"],
        "candidate_review_status": result["candidate_review_status"],
        "candidate_payload_sha256": difference["candidate_payload_sha256"],
        "before": {
            key: value for key, value in difference["before"].items() if key.endswith("_sha256")
        },
        "after": difference["after"],
        "change_counts": {layer: len(difference["changes"][layer]) for layer in _LAYERS},
        "difference_sha256": result["difference_sha256"],
        "difference_size_bytes": result["difference_size_bytes"],
        "store_difference_size_limit_bytes": result["store_difference_size_limit_bytes"],
        "store_difference_size_status": result["store_difference_size_status"],
        "legacy_store_difference_size_limit_bytes": result[
            "legacy_store_difference_size_limit_bytes"
        ],
        "legacy_store_difference_size_status": result["legacy_store_difference_size_status"],
        "store_difference_size_check": result["store_difference_size_check"],
        "details": "NOT_DISPLAYED",
        "comparison_scope": result["comparison_scope"],
        "source_text": result["source_text"],
        "source_authentication": result["source_authentication"],
        "coverage_authentication": result["coverage_authentication"],
        "identity_authentication": result["identity_authentication"],
        "candidate_and_difference_status": result["candidate_and_difference_status"],
        "publication": result["publication"],
        "current_observation": result["current_observation"],
        "publication_revalidation": result["publication_revalidation"],
    }
