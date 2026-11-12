"""Offline, unauthenticated product/contract association review candidates.

Only retained, locally revalidated inputs are read. The caller asserts a trusted
cooperating POSIX filesystem; hashes do not authenticate sources or writers.
No effective dates, publication, network access or completeness are inferred.
"""

from __future__ import annotations

import argparse
import contextlib
import fcntl
import hashlib
import os
import re
import stat
import sys
from collections import Counter, defaultdict

from bt_api_py import trading_calendar_candidate_inbox as inbox
from bt_api_py import trading_calendar_detail_batch as detail
from bt_api_py import trading_calendar_detail_collection as collection
from bt_api_py import trading_calendar_observation_run as runs
from bt_api_py.trading_calendar_preview_reports import (
    TRUSTED_LOCAL_POSIX,
    _directory_matches,
    _identity,
    _open_directory,
    _supported,
)

_SCHEMA = "bt_api.calendar-association-review-candidate/1"
_LIMIT = 4 * 1024 * 1024
_CONTRACT_LIMIT = 512
_TOTAL_CONTRACT_LIMIT = 20_000
_ASCII_ID = re.compile(r"[A-Za-z0-9][A-Za-z0-9_.-]{0,127}\Z", re.ASCII)


def _ascii(value):
    return value.upper() if type(value) is str and _ASCII_ID.fullmatch(value) else None


def _open(root, create):
    fds, chain = _open_directory(root)
    parent = fds[-1]
    try:
        if create:
            with contextlib.suppress(FileExistsError):
                os.mkdir("association-review-candidates", 0o700, dir_fd=parent)
        info = os.stat("association-review-candidates", dir_fd=parent, follow_symlinks=False)
        if not stat.S_ISDIR(info.st_mode) or info.st_mode & 0o077 or info.st_uid != os.geteuid():
            raise ValueError("invalid directory")
        fd = os.open(
            "association-review-candidates",
            os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW,
            dir_fd=parent,
        )
        fds.append(fd)
        if _identity(os.fstat(fd)) != _identity(info):
            raise ValueError("directory changed")
        chain.append((parent, "association-review-candidates", fd, _identity(info)))
        if not _directory_matches(chain):
            raise ValueError("directory changed")
        return fds, chain
    except BaseException:
        runs._close(fds)
        raise


def _inputs(root, navigation_id, hours_id, collection_id, filesystem):
    nav = inbox.read_candidate(root=root, candidate_id=navigation_id, filesystem=filesystem)
    hours = inbox.read_candidate(root=root, candidate_id=hours_id, filesystem=filesystem)
    paths = collection._paths(nav, root)
    summary, batches = collection._show_all(
        root=root, collection_id=collection_id, filesystem=filesystem
    )
    if (
        summary.get("status") not in {"PARTIAL", "OBSERVED_UNVERIFIED"}
        or summary.get("candidate_id") != navigation_id
        or summary.get("candidate_payload_sha256") != nav["payload_sha256"]
        or [row["path"] for row in summary["paths"]] != paths
        or batches is None
    ):
        raise ValueError("collection unavailable or mismatched")
    page = inbox._page_from_data(hours["payload"].get("hours_page"))
    if page is None or hours["payload"].get("hours_page_status") != "CAPTURED_UNVERIFIED":
        raise ValueError("hours unavailable")
    return nav, hours, summary, batches


def _derive(nav, hours, summary, batches, candidate_id, collection_id):
    links = nav["payload"]["navigation_fetch"]["navigation"]["links"]
    page = inbox._page_from_data(hours["payload"]["hours_page"])
    catalog = nav["payload"]["catalog"]
    if catalog is None:
        catalog = inbox.unknown_catalog("CACHE_INVALID")
    inbox._validate_catalog(catalog)
    names = Counter((row["kind"], row["label"]) for row in links)
    page_names = Counter((row.kind, row.name) for row in page.products)
    pages = {(row.kind, row.name): row for row in page.products}
    states = {row["path"]: row["status"] for row in summary["paths"]}
    items = {}
    for batch in batches:
        if batch["result"] is None:
            continue
        for item in batch["result"]["items"]:
            items[item["path"]] = item
        # Retry reports are already validated by the collection. Select only the
        # last recorded attempt; an unknown final state never reuses an old one.
        for retry in batch.get("retry_attempts", []):
            report = retry["result"]
            if report is None:
                items.pop(summary["paths"][retry["path_index"]]["path"], None)
            else:
                item = report["items"][0]
                items[item["path"]] = item
    claims = defaultdict(set)
    path_claims = defaultdict(set)
    for link in links:
        path = link["path"]
        item = items.get(path)
        if (
            item is None
            or states[path] not in {"CANDIDATE", "NEEDS_REVIEW"}
            or item["status"] not in {"CANDIDATE", "NEEDS_REVIEW"}
        ):
            continue
        for record in item["records"] or []:
            identity = (_ascii(record["venue"]), record["kind"], _ascii(record["symbol"]))
            if None not in identity and identity[1] == link["kind"]:
                claims[identity].add((link["kind"], link["label"], path))
                path_claims[path].add(identity)
    index = defaultdict(list)
    ambiguous = set()
    if catalog["status"] != "UNKNOWN":
        for row in catalog["rows"]:
            venue, product, instrument, kind = row[0], row[1], row[3], row[19]
            key = (_ascii(venue), kind, _ascii(product))
            if None not in key:
                if not _ascii(instrument) or "INSTRUMENT_ID_AMBIGUOUS" in row[-1]:
                    ambiguous.add(key)
                else:
                    index[key].append((venue, product, instrument))
    results = []
    total = 0
    for link in links:
        kind, label, path = link["kind"], link["label"], link["path"]
        key = (kind, label)
        out = {
            "kind": kind,
            "page_product_name": label,
            "path": path,
            "status": "UNKNOWN",
            "reason": "DETAIL_UNAVAILABLE",
            "venue": None,
            "product": None,
            "contracts": None,
            "hours_stages": None,
            "effective_date": "UNKNOWN",
        }
        item = items.get(path)
        if any(len(claims[identity]) > 1 for identity in path_claims[path]):
            out["reason"] = "DETAIL_IDENTITY_MULTIPLE_PAGES"
        elif names[key] != 1:
            out["reason"] = "NAME_MULTIPLE_PATHS"
        elif page_names[key] != 1:
            out["reason"] = "HOURS_NAME_MISSING" if not page_names[key] else "HOURS_NAME_CONFLICT"
        elif item is None or states[path] not in {"CANDIDATE", "NEEDS_REVIEW"}:
            out["reason"] = "DETAIL_" + states[path]
        elif item["identity_status"] == "CONFLICTING_FIELDS":
            out["reason"] = "DETAIL_HISTORY_CONFLICT"
        elif item["identity_status"] != "CONSISTENT_FIELDS_UNVERIFIED" or not item["records"]:
            out["reason"] = "DETAIL_IDENTITY_UNKNOWN"
        elif item["status"] not in {"CANDIDATE", "NEEDS_REVIEW"}:
            out["reason"] = "DETAIL_UNAVAILABLE"
        else:
            records = item["records"]
            identities = {(_ascii(r["venue"]), r["kind"], _ascii(r["symbol"])) for r in records}
            if len(identities) != 1 or any(None in identity for identity in identities):
                out["reason"] = "DETAIL_HISTORY_CONFLICT"
            elif next(iter(identities))[1] != kind:
                out["reason"] = "DETAIL_KIND_CONFLICT"
            elif catalog["status"] == "UNKNOWN":
                out["reason"] = "CATALOG_UNAVAILABLE"
            elif catalog["conflicts"] or catalog["skipped_rows"]:
                out["reason"] = "CATALOG_CONFLICT_OR_SKIPPED"
            else:
                identity = next(iter(identities))
                matches = index.get(identity, [])
                # Raw case variants may normalize together but are not silently
                # treated as one product declaration.
                raw_pairs = {(v, p) for v, p, _ in matches}
                if identity in ambiguous:
                    out["reason"] = "CATALOG_MATCH_CONFLICT"
                elif not matches:
                    out["reason"] = "CATALOG_MATCH_MISSING"
                elif len(raw_pairs) != 1 or len({i.upper() for _, _, i in matches}) != len(matches):
                    out["reason"] = "CATALOG_MATCH_CONFLICT"
                elif len(matches) > _CONTRACT_LIMIT or total + len(matches) > _TOTAL_CONTRACT_LIMIT:
                    out["reason"] = "CONTRACT_LIMIT"
                else:
                    venue, product = next(iter(raw_pairs))
                    total += len(matches)
                    out.update(
                        status="CANDIDATE",
                        reason="UNVERIFIED_MATCH",
                        venue=venue,
                        product=product,
                        contracts=sorted(i for _, _, i in matches),
                        detail_venue=records[0]["venue"],
                        detail_symbol=records[0]["symbol"],
                        hours_stages=[vars(stage) for stage in pages[key].stages],
                    )
        results.append(out)
    # A matching name on different paths is already unknown. Separately report
    # unmatched hours names so partial navigation cannot look exhaustive.
    unmatched = [
        {"kind": row.kind, "page_product_name": row.name, "reason": "NAVIGATION_MISSING"}
        for row in page.products
        if (row.kind, row.name) not in names
    ]
    return {
        "schema": _SCHEMA,
        "candidate_id": candidate_id,
        "navigation_candidate_id": nav["candidate_id"],
        "navigation_payload_sha256": nav["payload_sha256"],
        "hours_candidate_id": hours["candidate_id"],
        "hours_payload_sha256": hours["payload_sha256"],
        "collection_id": collection_id,
        "collection_observation_sha256": hashlib.sha256(
            collection._json([summary, batches])
        ).hexdigest(),
        "results": results,
        "unmatched_hours": unmatched,
        "effective_date": "UNKNOWN",
        "verification": "UNVERIFIED",
        "source_authentication": "NOT_PROVEN",
        "association": "NOT_PROVEN",
        "binding": "NOT_PROVEN",
        "coverage": "NOT_PROVEN",
        "durability": "NOT_PROVEN",
        "publication": "NOT_AUTHORIZED",
        "last_success": "NOT_UPDATED",
    }


def _valid(root, filesystem, *ids):
    return (
        filesystem == TRUSTED_LOCAL_POSIX
        and _supported()
        and detail._safe_root(root)
        and all(runs._id(value) for value in ids)
    )


def build_once(
    *,
    root,
    navigation_candidate_id,
    hours_candidate_id,
    collection_id,
    candidate_id,
    filesystem,
    emit,
):
    """Allocate once and retain only a bounded, rechecked offline candidate."""
    ids = (navigation_candidate_id, hours_candidate_id, collection_id, candidate_id)
    if not _valid(root, filesystem, *ids):
        return {"status": "NOT_COMMITTED", "reason": "INVALID_ARGUMENTS", "candidate_id": ""}
    emit({"event": "ASSOCIATION_CANDIDATE_ID_ALLOCATED", "candidate_id": candidate_id})
    result = {
        "status": "NOT_COMMITTED",
        "reason": "INPUT_UNAVAILABLE",
        "candidate_id": candidate_id,
    }
    try:
        inputs = _inputs(root, *ids[:3], filesystem)
        body = _derive(*inputs, candidate_id, collection_id)
        encoded = collection._envelope(body)
        if len(encoded) > _LIMIT or detail._sensitive(body, root):
            return {**result, "reason": "OUTPUT_LIMIT_OR_SENSITIVE"}
        fds, chain = _open(root, True)
    except (KeyboardInterrupt, SystemExit) as exc:
        exc.candidate_id, exc.commit_status = candidate_id, "COMMIT_UNKNOWN"
        raise
    except Exception:
        return result
    lock = None
    try:
        lock, lock_id = collection._lock(fds[-1], chain)
        if _inputs(root, *ids[:3], filesystem) != inputs:
            return result
        collection._check_lock(fds[-1], chain, lock, lock_id)
        status = collection._write(
            fds[-1], chain, f"candidate-{candidate_id}.json", encoded, _LIMIT
        )
        return {
            "status": status,
            "reason": "NONE"
            if status == "COMMITTED"
            else "WRITE_UNCERTAIN"
            if status == "COMMIT_UNKNOWN"
            else "WRITE_REJECTED",
            "candidate_id": candidate_id,
        }
    except (KeyboardInterrupt, SystemExit) as exc:
        exc.candidate_id, exc.commit_status = candidate_id, "COMMIT_UNKNOWN"
        raise
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


def show(*, root, candidate_id, filesystem):
    """Recheck original records and recompute; no authentication is implied."""
    result = {
        "status": "UNKNOWN",
        "reason": "UNAVAILABLE",
        "candidate_id": candidate_id if runs._id(candidate_id) else "",
    }
    if not _valid(root, filesystem, candidate_id):
        return result
    try:
        fds, chain = _open(root, False)
    except Exception:
        return result
    try:
        raw = collection._read(fds[-1], chain, f"candidate-{candidate_id}.json", _LIMIT)
        if raw is None:
            return result
        body, _ = collection._unwrap(raw)
        if body.get("schema") != _SCHEMA or body.get("candidate_id") != candidate_id:
            return result
        inputs = _inputs(
            root,
            body["navigation_candidate_id"],
            body["hours_candidate_id"],
            body["collection_id"],
            filesystem,
        )
        if (
            _derive(*inputs, candidate_id, body["collection_id"]) != body
            or detail._sensitive(body, root)
            or not _directory_matches(chain)
        ):
            return result
        return {
            "status": "VISIBLE_UNAUTHENTICATED",
            "reason": "UNVERIFIED_REVIEW",
            "candidate_id": candidate_id,
            "candidate": body,
        }
    except Exception:
        return result
    finally:
        runs._close(fds)


class _Parser(argparse.ArgumentParser):
    def error(self, message):
        raise ValueError("invalid arguments")


def main(argv=None):
    """Build or inspect one local review candidate; never retry an unknown write."""
    started = False
    try:
        parser = _Parser(description=__doc__)
        sub = parser.add_subparsers(dest="command", required=True, parser_class=_Parser)
        for name in ("build", "show"):
            command = sub.add_parser(name)
            command.add_argument("--calendar-root", required=True)
            command.add_argument("--candidate-id", required=True)
            command.add_argument("--trusted-local-posix", action="store_true")
            if name == "build":
                command.add_argument("--navigation-candidate-id", required=True)
                command.add_argument("--hours-candidate-id", required=True)
                command.add_argument("--collection-id", required=True)
        args = parser.parse_args(argv)
        ids = [args.candidate_id]
        if args.command == "build":
            ids.extend((args.navigation_candidate_id, args.hours_candidate_id, args.collection_id))
        if not args.trusted_local_posix or not _valid(
            args.calendar_root, TRUSTED_LOCAL_POSIX, *ids
        ):
            raise ValueError("invalid arguments")

        def emit(value):
            sys.stdout.buffer.write(collection._json(value) + b"\n")
            sys.stdout.buffer.flush()

        started = True
        if args.command == "build":
            result = build_once(
                root=args.calendar_root,
                navigation_candidate_id=args.navigation_candidate_id,
                hours_candidate_id=args.hours_candidate_id,
                collection_id=args.collection_id,
                candidate_id=args.candidate_id,
                filesystem=TRUSTED_LOCAL_POSIX,
                emit=emit,
            )
        else:
            result = show(
                root=args.calendar_root,
                candidate_id=args.candidate_id,
                filesystem=TRUSTED_LOCAL_POSIX,
            )
        output = collection._json(result)
        if len(output) > _LIMIT + 4096:
            result = {
                "status": "UNKNOWN",
                "reason": "OUTPUT_LIMIT",
                "candidate_id": args.candidate_id,
            }
            output = collection._json(result)
        sys.stdout.buffer.write(output + b"\n")
        return (
            10
            if result["status"] in {"COMMITTED", "VISIBLE_UNAUTHENTICATED"}
            else 25
            if result["status"] in {"UNKNOWN", "COMMIT_UNKNOWN"}
            else 2
        )
    except (KeyboardInterrupt, SystemExit) as exc:
        if not started:
            if isinstance(exc, SystemExit):
                raise
            sys.stdout.buffer.write(b'{"status":"NOT_COMMITTED","reason":"INTERRUPTED"}\n')
            return 130
        status = "COMMIT_UNKNOWN" if args.command == "build" else "UNKNOWN"
        sys.stdout.buffer.write(
            collection._json(
                {"status": status, "reason": "INTERRUPTED", "candidate_id": args.candidate_id}
            )
            + b"\n"
        )
        return 25
    except Exception:
        if not started:
            sys.stdout.buffer.write(b'{"status":"NOT_COMMITTED","reason":"INVALID_ARGUMENTS"}\n')
            return 2
        status = "COMMIT_UNKNOWN" if args.command == "build" else "UNKNOWN"
        sys.stdout.buffer.write(
            collection._json(
                {
                    "status": status,
                    "reason": "INTERRUPTED_OR_IO_UNKNOWN",
                    "candidate_id": args.candidate_id,
                }
            )
            + b"\n"
        )
        return 25


if __name__ == "__main__":
    sys.exit(main())
