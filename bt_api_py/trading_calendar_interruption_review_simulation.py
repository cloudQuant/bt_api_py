"""Retain public MOCK interruption review material, without reading CURRENT.

This is a detached simulation, not publication, source authentication, a
trading authorization, or an email operation. Callers must not mutate inputs
while the initial bounded copy is made. The supplied snapshot factory is a
trusted structural validator, not a source of authentication. Use only public
synthetic input; the existing path check is not a general secret detector.

The review directory is supported only for cooperating local POSIX writers.
A write of unknown outcome must be inspected with its original candidate ID;
no retry or overwrite is performed. Power-loss durability is not established.

For the CLI, provide the build input as JSON on stdin, using
``conda run --no-capture-output -n base python -m
bt_api_py.trading_calendar_interruption_review_simulation build ...``.
Show returns a summary unless ``--show-details`` is explicitly selected.

The input object has exactly ``baseline`` and ``candidate``. A declaration is
``{content, coverage, data_origin: "MOCK", simulation_provenance: null}``.
The baseline has exactly ``declaration``, ``version_id``, ``version_sha256``,
``content_sha256``, ``manifest``, ``manifest_sha256``, ``binding`` and
``binding_sha256``; manifest and binding are decoded JSON objects, with the
declared coverage equal to the manifest coverage. A caller may construct this
from a separately obtained store snapshot, but this module does not read it.
The candidate is a declaration. Both sources inventories must equal the fixed
public mock source. The CLI requires ``--mode SIMULATION_ONLY`` and
``--trusted-local-posix``; Python entry points require the corresponding mode,
filesystem declaration and an explicit factory.

The envelope body SHA uses the existing canonical ASCII JSON encoding;
content, manifest, binding, declaration, difference and input SHA values use
the store's canonical UTF-8 JSON encoding. Version SHA covers the store's
canonical snapshot envelope. These hashes prove only byte consistency.
"""

from __future__ import annotations

import argparse
import contextlib
import fcntl
import hashlib
import json
import os
import sys

from bt_api_py import trading_calendar_detail_batch as detail
from bt_api_py import trading_calendar_detail_collection as storage
from bt_api_py import trading_calendar_mock as mock
from bt_api_py import trading_calendar_observation_run as runs
from bt_api_py import trading_calendar_review_bundle as review
from bt_api_py.trading_calendar_interruption_compare_simulation import (
    compare_simulated_interruption,
)
from bt_api_py.trading_calendar_preview_reports import TRUSTED_LOCAL_POSIX, _directory_matches
from bt_api_py.trading_calendar_store import (
    _canonical,
    _coverage_bundle,
    _digest,
    _identifier,
    calendar_snapshot_digest,
)

_SCHEMA = "bt_api.calendar-review-bundle/5"
_BASE_KEYS = {
    "declaration",
    "version_id",
    "version_sha256",
    "content_sha256",
    "manifest",
    "manifest_sha256",
    "binding",
    "binding_sha256",
}
_LABELS = {
    "mode": "SIMULATION_ONLY",
    "source_authentication": "NOT_PROVEN",
    "identity_authentication": "NOT_PROVEN",
    "coverage": "CALLER_DECLARED",
    "baseline_identity": "CALLER_DECLARED",
    "version_existence": "NOT_PROVEN",
    "candidate_version": "NOT_PUBLISHED",
    "publication": "NOT_AUTHORIZED",
}


def _sha(value):
    return hashlib.sha256(value).hexdigest()


def _evaluate(value, candidate_id, snapshot_factory):
    review._bounded(value)
    if type(value) is not dict or set(value) != {"baseline", "candidate"}:
        raise ValueError("input")
    baseline, candidate = value["baseline"], value["candidate"]
    if type(baseline) is not dict or set(baseline) != _BASE_KEYS:
        raise ValueError("baseline")
    declaration = baseline["declaration"]
    if type(declaration) is not dict or type(candidate) is not dict:
        raise ValueError("declaration")
    for item in (declaration, candidate):
        if type(item.get("content")) is not dict or item["content"].get("sources") != [mock.SOURCE]:
            raise ValueError("mock source")
    if not _identifier(baseline["version_id"]) or any(
        not _digest(baseline[key])
        for key in ("version_sha256", "content_sha256", "manifest_sha256", "binding_sha256")
    ):
        raise ValueError("identity")
    content_bytes = _canonical(declaration["content"])
    manifest_bytes = _canonical(baseline["manifest"])
    binding_bytes = _canonical(baseline["binding"])
    manifest, _ = _coverage_bundle(
        content_bytes,
        manifest_bytes,
        baseline["manifest_sha256"],
        binding_bytes,
        baseline["binding_sha256"],
    )
    if (
        _sha(content_bytes) != baseline["content_sha256"]
        or manifest["coverage"] != declaration.get("coverage")
        or calendar_snapshot_digest(
            declaration["content"],
            coverage_manifest_bytes=manifest_bytes,
            coverage_manifest_sha256=baseline["manifest_sha256"],
            binding_bytes=binding_bytes,
            binding_sha256=baseline["binding_sha256"],
            simulation_provenance=declaration.get("simulation_provenance"),
        )
        != baseline["version_sha256"]
    ):
        raise ValueError("version mismatch")
    difference = compare_simulated_interruption(
        declaration, candidate, mode="SIMULATION_ONLY", snapshot_factory=snapshot_factory
    )
    if difference.get("status") not in {"SAME", "CHANGED"}:
        raise ValueError("comparison unavailable")
    identity = {
        key: baseline[key]
        for key in (
            "version_id",
            "version_sha256",
            "content_sha256",
            "manifest_sha256",
            "binding_sha256",
        )
    }
    return {
        "schema": _SCHEMA,
        "candidate_id": candidate_id,
        "input": value,
        "input_sha256": _sha(_canonical(value)),
        "candidate_declaration_sha256": _sha(_canonical(candidate)),
        "candidate_content_sha256": _sha(_canonical(candidate["content"])),
        "baseline": identity,
        "difference": difference,
        "difference_sha256": _sha(_canonical(difference)),
        "labels": dict(_LABELS),
    }


def _unknown(candidate_id, reason):
    return {
        "status": "UNKNOWN",
        "reason": reason,
        "candidate_id": candidate_id if runs._id(candidate_id) else "",
        **_LABELS,
    }


def build_once(*, root, candidate_id, input_data, filesystem, snapshot_factory, mode):
    """Retain one public synthetic review bundle; never overwrite an ID."""
    if mode != "SIMULATION_ONLY" or not review._valid(root, filesystem, candidate_id):
        return _unknown(candidate_id, "INVALID_ARGUMENTS")
    try:
        review._bounded(input_data)
        value = json.loads(_canonical(input_data), object_pairs_hook=runs._unique_pairs)
        if detail._sensitive(value, root):
            raise ValueError("private input")
        body = _evaluate(value, candidate_id, snapshot_factory)
        if detail._sensitive(body, root):
            raise ValueError("private output")
        encoded = storage._envelope(body)
        if len(encoded) > review._OUTPUT_LIMIT:
            raise ValueError("output budget")
        fds, chain = review._open(root, True)
    except Exception:
        return _unknown(candidate_id, "INPUT_UNAVAILABLE")
    lock = None
    try:
        lock, lock_id = storage._lock(fds[-1], chain)
        storage._check_lock(fds[-1], chain, lock, lock_id)
        outcome = storage._write(
            fds[-1], chain, f"candidate-{candidate_id}.json", encoded, review._OUTPUT_LIMIT
        )
        if outcome != "COMMITTED":
            return _unknown(candidate_id, "WRITE_OUTCOME_OR_ID_UNAVAILABLE")
        return {
            "status": "COMMITTED",
            "candidate_id": candidate_id,
            "payload_sha256": _sha(storage._json(body)),
            "difference_sha256": body["difference_sha256"],
            **_LABELS,
        }
    except (KeyboardInterrupt, SystemExit) as exc:
        with contextlib.suppress(Exception):
            exc.calendar_outcome = _unknown(candidate_id, "WRITE_OUTCOME_OR_ID_UNAVAILABLE")
        raise
    except Exception:
        return _unknown(candidate_id, "WRITE_OUTCOME_OR_ID_UNAVAILABLE")
    finally:
        if lock is not None:
            with contextlib.suppress(OSError):
                fcntl.flock(lock, fcntl.LOCK_UN)
            with contextlib.suppress(OSError):
                os.close(lock)
        runs._close(fds)


def show(*, root, candidate_id, filesystem, snapshot_factory, mode, show_details=False):
    """Re-read, recompute and return a public summary or complete difference."""
    if (
        mode != "SIMULATION_ONLY"
        or not review._valid(root, filesystem, candidate_id)
        or type(show_details) is not bool
    ):
        return _unknown(candidate_id, "INVALID_ARGUMENTS")
    try:
        fds, chain = review._open(root, False)
    except Exception:
        return _unknown(candidate_id, "UNAVAILABLE_OR_CHANGED")
    try:
        name = f"candidate-{candidate_id}.json"
        raw = storage._read(fds[-1], chain, name, review._OUTPUT_LIMIT)
        if raw is None:
            raise ValueError("missing")
        body, payload_sha256 = storage._unwrap(raw)
        if (
            body.get("schema") != _SCHEMA
            or body.get("candidate_id") != candidate_id
            or storage._envelope(body) != raw
        ):
            raise ValueError("schema")
        value = body["input"]
        review._bounded(value)
        if (
            detail._sensitive(value, root)
            or _evaluate(value, candidate_id, snapshot_factory) != body
        ):
            raise ValueError("changed")
        if storage._read(
            fds[-1], chain, name, review._OUTPUT_LIMIT
        ) != raw or not _directory_matches(chain):
            raise ValueError("changed")
        difference = body["difference"]
        result = {
            "status": "VISIBLE_UNAUTHENTICATED",
            "candidate_id": candidate_id,
            "payload_sha256": payload_sha256,
            "input_sha256": body["input_sha256"],
            "candidate_declaration_sha256": body["candidate_declaration_sha256"],
            "candidate_content_sha256": body["candidate_content_sha256"],
            "difference_sha256": body["difference_sha256"],
            "baseline": body["baseline"],
            "comparison_status": difference["status"],
            "before_sha256": difference["before_sha256"],
            "after_sha256": difference["after_sha256"],
            "declared_coverage": value["candidate"]["coverage"],
            "change_counts": {key: len(rows) for key, rows in difference["changes"].items()},
            **_LABELS,
        }
        if show_details:
            result["difference"] = difference
        return result
    except Exception:
        return _unknown(candidate_id, "UNAVAILABLE_OR_CHANGED")
    finally:
        runs._close(fds)


class _Parser(argparse.ArgumentParser):
    def error(self, message):
        raise ValueError("invalid arguments")


def main(argv=None):
    parser = _Parser(description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)
    for name in ("build", "show"):
        command = sub.add_parser(name)
        command.add_argument("--calendar-root", required=True)
        command.add_argument("--candidate-id", required=True)
        command.add_argument("--trusted-local-posix", action="store_true", required=True)
        command.add_argument("--mode", choices=("SIMULATION_ONLY",), required=True)
        if name == "show":
            command.add_argument("--show-details", action="store_true")
    try:
        args = parser.parse_args(argv)
        from bt_api_ctp.calendar_snapshot import CalendarSnapshot

        common = {
            "root": args.calendar_root,
            "candidate_id": args.candidate_id,
            "filesystem": TRUSTED_LOCAL_POSIX,
            "snapshot_factory": CalendarSnapshot,
            "mode": args.mode,
        }
        if args.command == "build":
            raw = sys.stdin.buffer.read(review._INPUT_LIMIT + 1)
            if len(raw) > review._INPUT_LIMIT:
                raise ValueError("input budget")
            value = json.loads(raw, object_pairs_hook=runs._unique_pairs)
            result = build_once(**common, input_data=value)
        else:
            result = show(**common, show_details=args.show_details)
        print(json.dumps(result, ensure_ascii=True, sort_keys=True))
        return 0 if result["status"] in {"COMMITTED", "VISIBLE_UNAUTHENTICATED"} else 2
    except Exception:
        print('{"status":"UNKNOWN","reason":"UNAVAILABLE"}')
        return 2


if __name__ == "__main__":
    sys.exit(main())
