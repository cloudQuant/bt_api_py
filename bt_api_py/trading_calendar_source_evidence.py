"""Retain one supervised SHFE public-page observation under an immutable ID.

Only public materials belong here. A claim is written before the request; a
missing result or uncertain commit must be inspected with the original ID, not
retried automatically. This is a trusted local POSIX, cooperating-writer
facility; no source, identity, freshness or publication authority is proven.
The Python read API returns raw public bytes. CLI output contains summaries only.
"""

from __future__ import annotations

import argparse
import base64
import contextlib
import fcntl
import json
import math
import os
import re
import stat
import sys

from bt_api_py import trading_calendar_candidate_inbox as inbox
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

_INTENT = "bt_api.calendar-source-evidence-intent/1"
_RESULT = "bt_api.calendar-source-evidence-result/1"
_LIMIT = 4 * 1024 * 1024
_HOLIDAY_ID = re.compile(r"shfe\.holiday\.[0-9]{4}\.([0-9a-f]{64})\Z")


def _open(root, create):
    fds, chain = _open_directory(root)
    parent = fds[-1]
    try:
        if create:
            with contextlib.suppress(FileExistsError):
                os.mkdir("source-evidence", 0o700, dir_fd=parent)
        info = os.stat("source-evidence", dir_fd=parent, follow_symlinks=False)
        if not stat.S_ISDIR(info.st_mode) or info.st_uid != os.geteuid() or info.st_mode & 0o077:
            raise ValueError("invalid evidence directory")
        fd = os.open("source-evidence", os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=parent)
        fds.append(fd)
        if _identity(os.fstat(fd)) != _identity(info):
            raise ValueError("directory changed")
        chain.append((parent, "source-evidence", fd, _identity(info)))
        if not _directory_matches(chain):
            raise ValueError("directory changed")
        return fds, chain
    except BaseException:
        runs._close(fds)
        raise


def _valid(root, evidence_id, filesystem):
    return (
        filesystem == TRUSTED_LOCAL_POSIX
        and _supported()
        and detail._safe_root(root)
        and runs._id(evidence_id)
    )


def _settings(kind, connect_timeout, read_timeout, total_timeout, max_bytes):
    from bt_api_ctp.calendar_shfe_page import MAX_BYTES

    if (
        kind not in {"hours", "holiday"}
        or type(max_bytes) is not int
        or not 0 < max_bytes <= MAX_BYTES
    ):
        raise ValueError("invalid settings")
    if any(
        type(x) not in (int, float) or not math.isfinite(x) or not 0 < x <= 60
        for x in (connect_timeout, read_timeout, total_timeout)
    ):
        raise ValueError("invalid timeout")


def capture(
    *,
    root,
    evidence_id,
    kind,
    filesystem,
    emit,
    connect_timeout=3.0,
    read_timeout=3.0,
    total_timeout=10.0,
    max_bytes=1024 * 1024,
    runner=None,
):
    """Make at most one claimed attempt; use show with this ID after uncertainty."""
    if not _valid(root, evidence_id, filesystem) or not callable(emit):
        return {"status": "NOT_COMMITTED", "reason": "INVALID_ARGUMENTS", "evidence_id": ""}
    try:
        _settings(kind, connect_timeout, read_timeout, total_timeout, max_bytes)
        # Check all provider dependencies before creating a durable claim.
        from bt_api_ctp import calendar_shfe_evidence_fetch as evidence_fetch
        from bt_api_ctp import calendar_shfe_fetch as shfe

        if runner is not None and not callable(runner):
            raise ValueError
        emit({"event": "SOURCE_EVIDENCE_ID_ALLOCATED", "evidence_id": evidence_id})
        fds, chain = _open(root, True)
    except KeyboardInterrupt:
        return {"status": "NOT_COMMITTED", "reason": "INTERRUPTED", "evidence_id": evidence_id}
    except Exception:
        return {
            "status": "NOT_COMMITTED",
            "reason": "INVALID_OR_UNAVAILABLE",
            "evidence_id": evidence_id,
        }
    lock = None
    attempted = False
    claim_committed = False
    intent = {
        "schema": _INTENT,
        "evidence_id": evidence_id,
        "kind": kind,
        "connect_timeout": connect_timeout,
        "read_timeout": read_timeout,
        "total_timeout": total_timeout,
        "max_bytes": max_bytes,
    }
    try:
        lock, lock_id = storage._lock(fds[-1], chain)
        storage._check_lock(fds[-1], chain, lock, lock_id)
        attempted = True
        claim = storage._write(
            fds[-1], chain, f"intent-{evidence_id}.json", storage._envelope(intent), _LIMIT
        )
        if claim != "COMMITTED":
            return {"status": claim, "reason": "CLAIM_NOT_PROVEN", "evidence_id": evidence_id}
        claim_committed = True
    except BaseException:
        return {
            "status": "COMMIT_UNKNOWN" if attempted else "NOT_COMMITTED",
            "reason": "CLAIM_UNCERTAIN",
            "evidence_id": evidence_id,
        }
    finally:
        try:
            if lock is not None:
                with contextlib.suppress(OSError):
                    fcntl.flock(lock, fcntl.LOCK_UN)
                with contextlib.suppress(OSError):
                    os.close(lock)
            runs._close(fds)
        except BaseException:
            return {
                "status": "ATTEMPT_UNKNOWN"
                if claim_committed
                else "COMMIT_UNKNOWN"
                if attempted
                else "NOT_COMMITTED",
                "reason": "CLAIM_CLEANUP_UNCERTAIN",
                "evidence_id": evidence_id,
            }

    try:
        kwargs = {
            "kind": kind,
            "connect_timeout": connect_timeout,
            "read_timeout": read_timeout,
            "total_timeout": total_timeout,
            "max_bytes": max_bytes,
        }
        if runner is not None:
            kwargs["runner"] = runner
        result = evidence_fetch.collect_shfe_evidence(**kwargs)
        evidence = result.evidence
        transport = result.transport_status
        if evidence is None:
            ipc = {
                "schema": "shfe-evidence-ipc/1",
                "result": {"transport": transport, "page": None},
                "raw_base64": None,
            }
        else:
            encoder = shfe._encode_result if kind == "hours" else shfe._encode_holiday_result
            ipc = {
                "schema": "shfe-evidence-ipc/1",
                "result": json.loads(encoder(transport, evidence.page)),
                "raw_base64": base64.b64encode(evidence.raw).decode("ascii"),
            }
        body = {
            "schema": _RESULT,
            "evidence_id": evidence_id,
            "kind": kind,
            "transport": transport,
            "collected_at": result.collected_at.isoformat(),
            "clock_status": "CLOCK_UNVERIFIED",
            "source": shfe._SOURCE if kind == "hours" else shfe._HOLIDAY_SOURCE,
            "transport_assurance": result.transport_assurance,
            "ipc": ipc,
            "source_authentication": "NOT_PROVEN",
            "publication": "NOT_AUTHORIZED",
        }
    except BaseException:
        # The process/child may still exist or the parser/transport may have run.
        return {
            "status": "ATTEMPT_UNKNOWN",
            "reason": "CHILD_OR_RESULT_UNCERTAIN",
            "evidence_id": evidence_id,
        }
    try:
        fds, chain = _open(root, False)
    except BaseException:
        return {
            "status": "ATTEMPT_UNKNOWN",
            "reason": "RESULT_DIRECTORY_UNAVAILABLE",
            "evidence_id": evidence_id,
        }
    lock = None
    attempted = False
    try:
        lock, lock_id = storage._lock(fds[-1], chain)
        storage._check_lock(fds[-1], chain, lock, lock_id)
        raw = storage._read(fds[-1], chain, f"intent-{evidence_id}.json", _LIMIT)
        if raw is None or storage._unwrap(raw)[0] != intent:
            raise ValueError("claim changed")
        attempted = True
        status = storage._write(
            fds[-1], chain, f"result-{evidence_id}.json", storage._envelope(body), _LIMIT
        )
        if status == "NOT_COMMITTED":
            return {
                "status": "ATTEMPT_UNKNOWN",
                "reason": "RESULT_NOT_COMMITTED",
                "evidence_id": evidence_id,
            }
        return {"status": status, "evidence_id": evidence_id, "transport": transport}
    except BaseException:
        return {
            "status": "COMMIT_UNKNOWN" if attempted else "ATTEMPT_UNKNOWN",
            "reason": "RESULT_UNCERTAIN",
            "evidence_id": evidence_id,
        }
    finally:
        try:
            if lock is not None:
                with contextlib.suppress(OSError):
                    fcntl.flock(lock, fcntl.LOCK_UN)
                with contextlib.suppress(OSError):
                    os.close(lock)
            runs._close(fds)
        except BaseException:
            return {
                "status": "COMMIT_UNKNOWN" if attempted else "ATTEMPT_UNKNOWN",
                "reason": "RESULT_CLEANUP_UNCERTAIN",
                "evidence_id": evidence_id,
            }


def show(*, root, evidence_id, filesystem, include_public_bytes=False):
    """Read the original ID and reparse evidence; bytes returned only by opt-in API."""
    unknown = {
        "status": "UNKNOWN",
        "reason": "UNAVAILABLE_OR_CHANGED",
        "evidence_id": evidence_id if runs._id(evidence_id) else "",
    }
    if not _valid(root, evidence_id, filesystem) or type(include_public_bytes) is not bool:
        return unknown
    try:
        fds, chain = _open(root, False)
        try:
            intent_raw = storage._read(fds[-1], chain, f"intent-{evidence_id}.json", _LIMIT)
            result_raw = storage._read(fds[-1], chain, f"result-{evidence_id}.json", _LIMIT)
            if intent_raw is None:
                return unknown
            intent, _ = storage._unwrap(intent_raw)
            if (
                set(intent)
                != {
                    "schema",
                    "evidence_id",
                    "kind",
                    "connect_timeout",
                    "read_timeout",
                    "total_timeout",
                    "max_bytes",
                }
                or intent["schema"] != _INTENT
                or intent["evidence_id"] != evidence_id
            ):
                return unknown
            _settings(
                intent["kind"],
                intent["connect_timeout"],
                intent["read_timeout"],
                intent["total_timeout"],
                intent["max_bytes"],
            )
            if result_raw is None:
                return {
                    "status": "ATTEMPT_UNKNOWN",
                    "reason": "RESULT_MISSING",
                    "evidence_id": evidence_id,
                }
            body, digest = storage._unwrap(result_raw)
            if (
                set(body)
                != {
                    "schema",
                    "evidence_id",
                    "kind",
                    "transport",
                    "collected_at",
                    "clock_status",
                    "source",
                    "transport_assurance",
                    "ipc",
                    "source_authentication",
                    "publication",
                }
                or body["schema"] != _RESULT
                or body["evidence_id"] != evidence_id
                or body["kind"] != intent["kind"]
                or body["transport_assurance"]
                not in {"SUPERVISED_DEFAULT_TLS_POLICY", "INJECTED_UNVERIFIED"}
                or body["clock_status"] != "CLOCK_UNVERIFIED"
                or body["source_authentication"] != "NOT_PROVEN"
                or body["publication"] != "NOT_AUTHORIZED"
                or not _directory_matches(chain)
            ):
                return unknown
            from datetime import datetime

            from bt_api_ctp import calendar_shfe_evidence_fetch as ef
            from bt_api_ctp import calendar_shfe_fetch as shfe

            if body["source"] != (
                shfe._SOURCE if intent["kind"] == "hours" else shfe._HOLIDAY_SOURCE
            ):
                return unknown
            collected = datetime.fromisoformat(body["collected_at"])
            if collected.utcoffset() is None or collected.utcoffset().total_seconds() != 0:
                return unknown
            transport = body["transport"]
            if transport not in shfe._TRANSPORT_STATUSES:
                return unknown
            if transport in ef._CHILD_ONLY:
                if body["ipc"] != {
                    "schema": "shfe-evidence-ipc/1",
                    "result": {"transport": transport, "page": None},
                    "raw_base64": None,
                }:
                    return unknown
                evidence = None
            else:
                observed, evidence = ef.decode_evidence(
                    storage._json(body["ipc"]), intent["kind"], intent["max_bytes"]
                )
                if observed != transport:
                    return unknown
            outcome = {
                "status": "VISIBLE_UNAUTHENTICATED",
                "evidence_id": evidence_id,
                "transport": transport,
                "kind": intent["kind"],
                "payload_sha256": digest,
                "source_sha256": evidence.sha256 if evidence else None,
                "page_status": evidence.page.status if evidence else None,
                "collected_at": body["collected_at"],
                "clock_status": "CLOCK_UNVERIFIED",
                "transport_assurance": body["transport_assurance"],
                "source_authentication": "NOT_PROVEN",
                "publication": "NOT_AUTHORIZED",
            }
            if include_public_bytes and evidence is not None:
                outcome["raw_bytes"] = evidence.raw
                outcome["utf8_text"] = ef.evidence_utf8(evidence)
                if intent["kind"] == "hours" and evidence.page.status in {
                    "CANDIDATE",
                    "NEEDS_REVIEW",
                }:
                    outcome["candidate_source_id"] = f"shfe.hours.{evidence.sha256}"
                elif intent["kind"] == "holiday" and evidence.page.status == "CANDIDATE":
                    outcome["candidate_source_id"] = (
                        f"shfe.holiday.{evidence.page.year}.{evidence.sha256}"
                    )
            return outcome
        finally:
            runs._close(fds)
    except Exception:
        return unknown


def match_candidate(*, root, evidence_id, candidate_id, filesystem):
    """Compare with the candidate's declared digest, not unavailable old bytes.

    Matching does not prove that the older candidate's original page existed,
    had these bytes, or came from the asserted source. Ordering is not proven.
    """
    unknown = {
        "status": "UNKNOWN",
        "reason": "UNAVAILABLE_OR_MISMATCH",
        "evidence_id": evidence_id if runs._id(evidence_id) else "",
    }
    try:
        evidence = show(root=root, evidence_id=evidence_id, filesystem=filesystem)
        candidate = inbox.read_candidate(
            root=root, candidate_id=candidate_id, filesystem=filesystem
        )
        payload = candidate["payload"]
        if evidence["status"] != "VISIBLE_UNAUTHENTICATED" or evidence["transport"] != "HTTP_OK":
            return unknown
        if evidence["kind"] == "hours":
            page = inbox._page_from_data(payload.get("hours_page"))
            digests = (
                {page.sha256}
                if page is not None and payload.get("hours_page_status") == "CAPTURED_UNVERIFIED"
                else set()
            )
        else:
            if payload.get("holiday_status") != "CAPTURED_UNVERIFIED":
                return unknown
            digests = {
                match.group(1)
                for row in payload["content"]["sources"]
                if (match := _HOLIDAY_ID.fullmatch(row["id"])) is not None
            }
        if digests != {evidence["source_sha256"]}:
            return unknown
        return {
            "status": "CANDIDATE_DECLARED_DIGEST_MATCH",
            "evidence_id": evidence_id,
            "candidate_id": candidate_id,
            "candidate_payload_sha256": candidate["payload_sha256"],
            "source_sha256": evidence["source_sha256"],
            "observation_order": "NOT_PROVEN",
            "old_source_bytes": "NOT_AVAILABLE",
            "source_authentication": "NOT_PROVEN",
            "publication": "NOT_AUTHORIZED",
        }
    except Exception:
        return unknown


class _Parser(argparse.ArgumentParser):
    def error(self, message):
        raise ValueError("invalid arguments")


def main(argv=None):
    parser = _Parser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True, parser_class=_Parser)
    for command in ("capture", "show", "match"):
        entry = commands.add_parser(command)
        entry.add_argument("--calendar-root", required=True)
        entry.add_argument("--evidence-id", required=True)
        entry.add_argument("--trusted-local-posix", action="store_true")
        if command == "capture":
            entry.add_argument("--kind", choices=("hours", "holiday"), required=True)
            entry.add_argument("--connect-timeout", type=float, default=3.0)
            entry.add_argument("--read-timeout", type=float, default=3.0)
            entry.add_argument("--total-timeout", type=float, default=10.0)
            entry.add_argument("--max-bytes", type=int, default=1024 * 1024)
        if command == "match":
            entry.add_argument("--candidate-id", required=True)
    try:
        args = parser.parse_args(argv)
        if not args.trusted_local_posix:
            raise ValueError
        common = {
            "root": args.calendar_root,
            "evidence_id": args.evidence_id,
            "filesystem": TRUSTED_LOCAL_POSIX,
        }
        if args.command == "capture":
            result = capture(
                **common,
                kind=args.kind,
                connect_timeout=args.connect_timeout,
                read_timeout=args.read_timeout,
                total_timeout=args.total_timeout,
                max_bytes=args.max_bytes,
                emit=lambda x: print(json.dumps(x), flush=True),
            )
        elif args.command == "match":
            result = match_candidate(**common, candidate_id=args.candidate_id)
        else:
            result = show(**common)
        print(json.dumps(result, ensure_ascii=True, sort_keys=True))
        return (
            10
            if result["status"]
            in {"COMMITTED", "VISIBLE_UNAUTHENTICATED", "CANDIDATE_DECLARED_DIGEST_MATCH"}
            else 25
        )
    except KeyboardInterrupt:
        print('{"status":"ATTEMPT_UNKNOWN","reason":"INTERRUPTED"}')
        return 130
    except Exception:
        print('{"status":"NOT_COMMITTED","reason":"INVALID_ARGUMENTS"}')
        return 2


if __name__ == "__main__":
    sys.exit(main())
