"""Explicit, bounded observations for paths in one local /5 candidate.

Usage: python -m bt_api_py.trading_calendar_detail_batch
  --calendar-root /absolute/calendar --candidate-id HEX --trusted-local-posix
  --budget-seconds SECONDS [--per-request-seconds SECONDS] --path /products/... [--path ...]

No requests happen on import. A candidate's hash and selected paths are only a
local unauthenticated observation; they establish no source authority, release,
freshness, exchange identity or coverage. Full HTML and local roots are never
emitted. Known full root values and common path syntax are filtered from the
limited text projection; this is not general secret detection. Logical I/O and
record limits do not prove a physical RSS limit. The parent owns a new POSIX
session and supervises the worker group; cleanup is not proven, and operating
system process creation or uninterruptible kernel waits cannot be bounded.
The CLI requires default SIGCHLD and one Python thread; it cannot rule out
SA_NOCLDWAIT or a native reaper in an arbitrary embedded host.
"""

from __future__ import annotations

import argparse
import contextlib
import json
import math
import os
import re
import selectors
import signal
import subprocess
import sys
import threading
import time
from datetime import date

_SCHEMA = "bt_api.calendar-detail-observation/1"
_CANDIDATE_SCHEMA = "bt_api.calendar-preview-candidate/5"
_FETCHED_CANDIDATE_SCHEMA = "bt_api.calendar-preview-candidate/6"
_MAX_PATHS = 10
_MAX_PAGE_OUTPUT = 32 * 1024
_MAX_OUTPUT = 256 * 1024
_MAX_PROTOCOL = 4 * 1024 * 1024
_MAX_STDIN = 16384
_MAX_SOURCE_BYTES = 1024 * 1024
_MAX_RAW_FIELDS = 1024 * 1024
_HEX_ID = re.compile(r"[0-9a-f]{32}\Z", re.ASCII)
_HEX_SHA = re.compile(r"[0-9a-f]{64}\Z", re.ASCII)
_SYMBOL = re.compile(r"[A-Z]{1,5}\Z", re.ASCII)
_DATE = re.compile(r"[0-9]{4}-[0-9]{2}-[0-9]{2}\Z", re.ASCII)
_ITEM_STATES = frozenset(
    {
        "NOT_ATTEMPTED",
        "NOT_ATTEMPTED_BUDGET",
        "NOT_ATTEMPTED_REJECTED",
        "NOT_ATTEMPTED_CLEANUP",
        "NOT_ATTEMPTED_RESULT_LIMIT",
        "IN_PROGRESS",
        "CANDIDATE",
        "NEEDS_REVIEW",
        "UNKNOWN",
    }
)
_REASONS = frozenset(
    {
        "NONE",
        "PENDING",
        "BUDGET_EXHAUSTED",
        "REJECTED",
        "CLEANUP_UNCERTAIN",
        "RESULT_LIMIT",
        "INTERRUPTED",
        "ATTEMPT_UNKNOWN",
        "FETCH_FAILED",
        "PARSE_FAILED",
        "DETAIL_FIELDS_INCOMPLETE",
        "DETAILS_UNSAFE",
        "DETAIL_TOO_LARGE",
        "DETAIL_REVIEW",
        "COLLECTOR_PROTOCOL_ERROR",
        "CHILD_CLEANUP_UNCERTAIN",
        "SUPERVISOR_UNSUPPORTED",
    }
)
_TRANSPORT = frozenset(
    {
        "NOT_ATTEMPTED",
        "IN_PROGRESS",
        "HTTP_OK",
        "HTTP_ERROR",
        "REDIRECT",
        "TLS_ERROR",
        "NETWORK_TIMEOUT",
        "NETWORK_ERROR",
        "RESPONSE_TOO_LARGE",
        "EMPTY_RESPONSE",
        "PARSER_ERROR",
        "RESULT_TOO_LARGE",
        "CHILD_ERROR",
        "CHILD_TIMEOUT",
        "CHILD_CLEANUP_UNCERTAIN",
        "SUPERVISOR_UNSUPPORTED",
        "UNKNOWN",
    }
)
_PARSE = frozenset(
    {"NOT_OBSERVED", "CANDIDATE", "NEEDS_REVIEW", "INVALID_UTF8", "STRUCTURE_CHANGED"}
)
_IDENTITY = frozenset(
    {"NOT_ASSESSED", "CONSISTENT_FIELDS_UNVERIFIED", "CONFLICTING_FIELDS", "INCOMPLETE_FIELDS"}
)
_GAPS = frozenset({"SYMBOL_UNKNOWN", "VENUE_UNKNOWN", "SOURCE_DATE_UNKNOWN"})
_worker_term_received = False


def _json(value):
    return json.dumps(
        value, ensure_ascii=True, sort_keys=True, separators=(",", ":"), allow_nan=False
    ).encode("ascii")


def _unique_pairs(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("duplicate JSON key")
        result[key] = value
    return result


def _safe_root(root):
    return (
        type(root) is str
        and bool(root)
        and len(root) <= 4096
        and os.path.isabs(root)
        and "\x00" not in root
        and ".." not in root.split(os.sep)
    )


def _sensitive(value, root):
    needles = {root.casefold(), os.path.normpath(root).casefold()}
    pending = [value]
    while pending:
        item = pending.pop()
        if type(item) is str and any(needle and needle in item.casefold() for needle in needles):
            return True
        if type(item) is dict:
            pending.extend(item.keys())
            pending.extend(item.values())
        elif type(item) is list:
            pending.extend(item)
    return False


def _kind(path):
    from bt_api_ctp.calendar_shfe_product_navigation import _path_kind

    return _path_kind(path)


def _arguments_valid(root, candidate_id, paths, budget, per_request):
    if (
        not _safe_root(root)
        or type(candidate_id) is not str
        or _HEX_ID.fullmatch(candidate_id) is None
        or type(paths) is not list
        or not 1 <= len(paths) <= _MAX_PATHS
        or len(paths) != len(set(paths))
        or any(
            type(n) not in (int, float) or not math.isfinite(n) or not 0.1 <= n <= 60
            for n in (budget, per_request)
        )
    ):
        return False
    try:
        return all(_kind(path) in {"future", "option"} for path in paths) and not _sensitive(
            paths, root
        )
    except Exception:
        return False


def _item(path, status="NOT_ATTEMPTED", reason="PENDING"):
    return {
        "path": path,
        "kind": _kind(path),
        "status": status,
        "reason": reason,
        "transport_status": "NOT_ATTEMPTED",
        "parse_status": "NOT_OBSERVED",
        "identity_status": "NOT_ASSESSED",
        "page_sha256": None,
        "records": None,
    }


def _state(candidate_id, paths):
    return {
        "schema": _SCHEMA,
        "status": "IN_PROGRESS",
        "reason": "NONE",
        "candidate_id": candidate_id,
        "candidate_payload_sha256": None,
        "candidate_authentication": "NOT_PROVEN",
        "retained_data_binding": "NOT_PROVEN",
        "source_authentication": "NOT_PROVEN",
        "freshness": "NOT_PROVEN",
        "coverage": "NOT_PROVEN",
        "authorization": "NOT_PROVEN",
        "publication": "NOT_AUTHORIZED",
        "process_cleanup": "NOT_PROVEN",
        "resource_limits": {
            "paths": _MAX_PATHS,
            "candidate_bytes": 32 * 1024 * 1024,
            "response_bytes_per_path": _MAX_SOURCE_BYTES,
            "page_output_bytes": _MAX_PAGE_OUTPUT,
            "total_output_bytes": _MAX_OUTPUT,
            "physical_memory": "NOT_PROVEN",
        },
        "items": [_item(path) for path in paths],
    }


def _safe_time(value, root):
    if (
        type(value) is not str
        or not value
        or len(value) > 2048
        or any(
            ord(c) < 32 or 0x7F <= ord(c) <= 0x9F or 0xD800 <= ord(c) <= 0xDFFF or c in "/\\%<>"
            for c in value
        )
        or _sensitive(value, root)
    ):
        return None
    return value


def _valid_date(value):
    if type(value) is not str or _DATE.fullmatch(value) is None:
        return False
    try:
        return date.fromisoformat(value).isoformat() == value
    except ValueError:
        return False


def _provider_item(path, result, root):
    from bt_api_ctp.calendar_shfe_detail_fetch import DetailFetchResult, _decode, _encode
    from bt_api_ctp.calendar_shfe_product_page import ProductPage, ProductRecord

    expected_kind = _kind(path)
    if (
        type(result) is not DetailFetchResult
        or result.path != path
        or result.kind != expected_kind
        or result.transport_assurance != "SUPERVISED_DEFAULT_TLS_POLICY"
        or (
            result.verification,
            result.source_authentication,
            result.current_record,
            result.coverage,
            result.publication,
        )
        != ("UNVERIFIED", "NOT_PROVEN", "UNKNOWN", "UNKNOWN", "NOT_AUTHORIZED")
    ):
        raise ValueError("invalid result")
    page = result.page
    if page is not None:
        if (
            type(page) is not ProductPage
            or type(page.records) is not tuple
            or len(page.records) > 100
        ):
            raise ValueError("invalid page")
        total = 0
        for row in page.records:
            if (
                type(row) is not ProductRecord
                or type(row.fields) is not tuple
                or len(row.fields) > 100
            ):
                raise ValueError("invalid record")
            for field in row.fields:
                if (
                    type(field) is not tuple
                    or len(field) != 2
                    or type(field[0]) is not str
                    or type(field[1]) is not str
                    or len(field[0]) > 100
                    or len(field[1]) > 8192
                ):
                    raise ValueError("invalid raw field")
                total += len(field[0]) + len(field[1])
                if total > _MAX_RAW_FIELDS:
                    raise ValueError("raw field limit")
    transport, validated = _decode(_encode(result.transport_status, page), path)
    if transport != result.transport_status or validated != page or transport not in _TRANSPORT:
        raise ValueError("invalid provider protocol")
    if result.parse_status != (page.status if page is not None else "NOT_OBSERVED"):
        raise ValueError("invalid parse status")
    identity = "NOT_ASSESSED"
    if page is not None and page.status in {"CANDIDATE", "NEEDS_REVIEW"}:
        if (
            len({r.symbol for r in page.records if r.symbol is not None}) > 1
            or len({r.venue for r in page.records if r.venue is not None}) > 1
        ):
            identity = "CONFLICTING_FIELDS"
        elif any(r.symbol is None or r.venue is None for r in page.records):
            identity = "INCOMPLETE_FIELDS"
        else:
            identity = "CONSISTENT_FIELDS_UNVERIFIED"
    provider_status = (
        result.parse_status
        if identity == "CONSISTENT_FIELDS_UNVERIFIED"
        else "NEEDS_REVIEW"
        if identity in {"CONFLICTING_FIELDS", "INCOMPLETE_FIELDS"}
        else "UNKNOWN"
    )
    if result.identity_status != identity or result.status != provider_status:
        raise ValueError("invalid identity status")
    item = _item(path, "UNKNOWN", "FETCH_FAILED")
    item.update(
        {
            "transport_status": transport,
            "parse_status": result.parse_status,
            "identity_status": identity,
        }
    )
    if transport != "HTTP_OK":
        if transport == "CHILD_CLEANUP_UNCERTAIN":
            item["reason"] = "CHILD_CLEANUP_UNCERTAIN"
        elif transport == "SUPERVISOR_UNSUPPORTED":
            item["reason"] = "SUPERVISOR_UNSUPPORTED"
        return item
    if page is None or page.status not in {"CANDIDATE", "NEEDS_REVIEW"}:
        item["reason"] = "PARSE_FAILED"
        return item
    item["page_sha256"] = page.sha256
    records, incomplete = [], False
    for row in page.records:
        values = dict(row.fields)
        hours = _safe_time(values.get("TradingHours"), root)
        raw_date = row.raw_date if _valid_date(row.raw_date) else None
        if hours is None or raw_date is None:
            incomplete = True
        records.append(
            {
                "kind": row.kind,
                "symbol": row.symbol,
                "venue": row.venue,
                "raw_date": raw_date,
                "trading_hours": hours,
                "unknown_reasons": list(row.unknown_reasons),
                "effective_date": "UNKNOWN",
            }
        )
    item["records"] = records
    item["status"] = "NEEDS_REVIEW" if incomplete or provider_status != "CANDIDATE" else "CANDIDATE"
    item["reason"] = (
        "DETAIL_FIELDS_INCOMPLETE"
        if incomplete
        else "DETAIL_REVIEW"
        if item["status"] == "NEEDS_REVIEW"
        else "NONE"
    )
    if len(_json(item)) > _MAX_PAGE_OUTPUT:
        item.update({"status": "UNKNOWN", "reason": "DETAIL_TOO_LARGE", "records": None})
    return item


def _reject(state, reason):
    state["status"], state["reason"] = "ERROR", reason
    for item in state["items"]:
        item.update({"status": "NOT_ATTEMPTED_REJECTED", "reason": "REJECTED"})
    return state


def _work(root, candidate_id, paths, deadline, per_request, emit):
    state = _state(candidate_id, paths)
    try:
        from bt_api_py.trading_calendar_candidate_inbox import read_candidate

        candidate = read_candidate(
            root=root, candidate_id=candidate_id, filesystem="TRUSTED_LOCAL_POSIX"
        )
        if candidate["schema"] not in {_CANDIDATE_SCHEMA, _FETCHED_CANDIDATE_SCHEMA}:
            return _reject(state, "CANDIDATE_SCHEMA_UNSUPPORTED")
        state["candidate_payload_sha256"] = candidate["payload_sha256"]
        if candidate["schema"] == _CANDIDATE_SCHEMA:
            nav = candidate["payload"]["product_navigation"]["navigation"]
        else:
            fetched = candidate["payload"]["navigation_fetch"]
            if fetched["status"] == "UNKNOWN":
                return _reject(state, "NAVIGATION_UNKNOWN")
            nav = fetched["navigation"]
        if nav["status"] == "UNKNOWN":
            return _reject(state, "NAVIGATION_UNKNOWN")
        if nav["status"] != "CANDIDATE" or nav["review_reasons"]:
            return _reject(state, "NAVIGATION_CONFLICT")
        entries = nav["links"]
        if type(entries) is not list:
            return _reject(state, "NAVIGATION_UNKNOWN")
        seen = {}
        for entry in entries:
            path = entry["path"]
            if path in seen and seen[path] != (entry["kind"], entry["label"]):
                return _reject(state, "NAVIGATION_CONFLICT")
            seen[path] = (entry["kind"], entry["label"])
        if any(path not in seen for path in paths):
            return _reject(state, "SELECTION_NOT_IN_NAVIGATION")
        if any(seen[path][0] != _kind(path) for path in paths):
            return _reject(state, "NAVIGATION_CONFLICT")
        del candidate, entries, nav, seen
    except Exception:
        return _reject(state, "CANDIDATE_UNAVAILABLE")
    emit(state)
    try:
        from bt_api_ctp.calendar_shfe_detail_fetch import collect_detail_page
    except ImportError:
        return _reject(state, "COLLECTOR_UNAVAILABLE")

    for index, path in enumerate(paths):
        remaining = min(per_request, deadline - time.monotonic() - 0.5)
        if remaining <= 0.001:
            for item in state["items"][index:]:
                item.update({"status": "NOT_ATTEMPTED_BUDGET", "reason": "BUDGET_EXHAUSTED"})
            state["status"], state["reason"] = "PARTIAL", "BUDGET_EXHAUSTED"
            return state
        state["items"][index] = _item(path, "IN_PROGRESS", "PENDING")
        state["items"][index]["transport_status"] = "IN_PROGRESS"
        emit(state)
        try:
            result = collect_detail_page(
                path=path,
                connect_timeout=min(3.0, remaining),
                read_timeout=min(3.0, remaining),
                total_timeout=remaining,
                max_bytes=_MAX_SOURCE_BYTES,
            )
            item = _provider_item(path, result, root)
        except Exception:
            item = _item(path, "UNKNOWN", "COLLECTOR_PROTOCOL_ERROR")
            item["transport_status"] = "UNKNOWN"
        state["items"][index] = item
        if item["reason"] in {"CHILD_CLEANUP_UNCERTAIN", "COLLECTOR_PROTOCOL_ERROR"}:
            for pending in state["items"][index + 1 :]:
                pending.update({"status": "NOT_ATTEMPTED_CLEANUP", "reason": "CLEANUP_UNCERTAIN"})
            state["status"] = "CLEANUP_UNCERTAIN"
            state["reason"] = (
                "CHILD_CLEANUP_UNCERTAIN"
                if item["reason"] == "CHILD_CLEANUP_UNCERTAIN"
                else "COLLECTOR_CLEANUP_UNCERTAIN"
            )
            return state
        result_reserve = min(8192, max(1024, _MAX_OUTPUT // 8))
        if len(_json(state)) > _MAX_OUTPUT - result_reserve:
            item.update({"status": "UNKNOWN", "reason": "RESULT_LIMIT", "records": None})
            for pending in state["items"][index + 1 :]:
                pending.update({"status": "NOT_ATTEMPTED_RESULT_LIMIT", "reason": "RESULT_LIMIT"})
            state["status"], state["reason"] = "PARTIAL", "RESULT_LIMIT"
            return state
        emit(state)
    state["status"] = (
        "OBSERVED_UNVERIFIED"
        if all(item["status"] == "CANDIDATE" for item in state["items"])
        else "PARTIAL"
    )
    return state


def _validate_unobserved_item(item):
    status, reason, transport, parse, identity, sha = (
        item[k]
        for k in (
            "status",
            "reason",
            "transport_status",
            "parse_status",
            "identity_status",
            "page_sha256",
        )
    )
    placeholder = {
        "NOT_ATTEMPTED": "PENDING",
        "NOT_ATTEMPTED_BUDGET": "BUDGET_EXHAUSTED",
        "NOT_ATTEMPTED_REJECTED": "REJECTED",
        "NOT_ATTEMPTED_CLEANUP": "CLEANUP_UNCERTAIN",
        "NOT_ATTEMPTED_RESULT_LIMIT": "RESULT_LIMIT",
    }
    if status in placeholder:
        if (reason, transport, parse, identity, sha) != (
            placeholder[status],
            "NOT_ATTEMPTED",
            "NOT_OBSERVED",
            "NOT_ASSESSED",
            None,
        ):
            raise ValueError("invalid not-attempted item")
        return
    if status == "IN_PROGRESS":
        if (reason, transport, parse, identity, sha) != (
            "PENDING",
            "IN_PROGRESS",
            "NOT_OBSERVED",
            "NOT_ASSESSED",
            None,
        ):
            raise ValueError("invalid in-progress item")
        return
    if status != "UNKNOWN":
        raise ValueError("invalid unknown item")
    if reason in {"INTERRUPTED", "ATTEMPT_UNKNOWN", "COLLECTOR_PROTOCOL_ERROR"}:
        if (transport, parse, identity, sha) != ("UNKNOWN", "NOT_OBSERVED", "NOT_ASSESSED", None):
            raise ValueError("invalid interruption")
    elif reason == "CHILD_CLEANUP_UNCERTAIN":
        if (transport, parse, identity, sha) != (
            "CHILD_CLEANUP_UNCERTAIN",
            "NOT_OBSERVED",
            "NOT_ASSESSED",
            None,
        ):
            raise ValueError("invalid cleanup status")
    elif reason == "SUPERVISOR_UNSUPPORTED":
        if (transport, parse, identity, sha) != (
            "SUPERVISOR_UNSUPPORTED",
            "NOT_OBSERVED",
            "NOT_ASSESSED",
            None,
        ):
            raise ValueError("invalid unsupported supervisor status")
    elif reason == "FETCH_FAILED":
        if transport not in _TRANSPORT - {
            "NOT_ATTEMPTED",
            "IN_PROGRESS",
            "HTTP_OK",
            "UNKNOWN",
            "CHILD_CLEANUP_UNCERTAIN",
            "SUPERVISOR_UNSUPPORTED",
        } or (parse, identity, sha) != ("NOT_OBSERVED", "NOT_ASSESSED", None):
            raise ValueError("invalid transport failure")
    elif reason == "PARSE_FAILED":
        if (
            transport != "HTTP_OK"
            or parse not in {"INVALID_UTF8", "STRUCTURE_CHANGED"}
            or identity != "NOT_ASSESSED"
            or sha is not None
        ):
            raise ValueError("invalid parse failure")
    elif reason == "DETAIL_TOO_LARGE":
        if (
            transport != "HTTP_OK"
            or parse not in {"CANDIDATE", "NEEDS_REVIEW"}
            or identity not in _IDENTITY - {"NOT_ASSESSED"}
            or sha is None
        ):
            raise ValueError("invalid resource rejection")
    elif reason == "RESULT_LIMIT":
        if transport in {"NOT_ATTEMPTED", "IN_PROGRESS"}:
            raise ValueError("invalid output limit")
    else:
        raise ValueError("invalid unknown reason")


def _validate_overall_state(data):
    status, reason, items = data["status"], data["reason"], data["items"]
    statuses = [item["status"] for item in items]
    digest = data["candidate_payload_sha256"]
    cleanup_indices = [
        index
        for index, item in enumerate(items)
        if item["reason"] in {"CHILD_CLEANUP_UNCERTAIN", "COLLECTOR_PROTOCOL_ERROR"}
    ]
    if cleanup_indices and (
        status != "CLEANUP_UNCERTAIN"
        or len(cleanup_indices) != 1
        or any(
            item["status"] != "NOT_ATTEMPTED_CLEANUP" for item in items[cleanup_indices[0] + 1 :]
        )
        or any(item["status"].startswith("NOT_ATTEMPTED") for item in items[: cleanup_indices[0]])
    ):
        raise ValueError("invalid cleanup sequence")
    if status == "OBSERVED_UNVERIFIED":
        if reason != "NONE" or digest is None or any(value != "CANDIDATE" for value in statuses):
            raise ValueError("invalid observed report")
    elif status == "IN_PROGRESS":
        if (
            reason != "NONE"
            or digest is None
            or statuses.count("IN_PROGRESS") > 1
            or any(value.startswith("NOT_ATTEMPTED_") for value in statuses)
        ):
            raise ValueError("invalid progress report")
    elif status == "PARTIAL":
        if (
            digest is None
            or reason not in {"NONE", "BUDGET_EXHAUSTED", "RESULT_LIMIT"}
            or all(value == "CANDIDATE" for value in statuses)
            or any(
                value
                in {
                    "NOT_ATTEMPTED",
                    "IN_PROGRESS",
                    "NOT_ATTEMPTED_REJECTED",
                    "NOT_ATTEMPTED_CLEANUP",
                }
                for value in statuses
            )
            or (reason == "BUDGET_EXHAUSTED" and "NOT_ATTEMPTED_BUDGET" not in statuses)
            or (reason == "NONE" and any(value.startswith("NOT_ATTEMPTED") for value in statuses))
            or (
                reason == "RESULT_LIMIT"
                and not any(item["reason"] == "RESULT_LIMIT" for item in items)
            )
        ):
            raise ValueError("invalid partial report")
    elif status == "ERROR":
        if reason not in {
            "CANDIDATE_UNAVAILABLE",
            "SUPERVISOR_UNSUPPORTED",
            "COLLECTOR_UNAVAILABLE",
            "CANDIDATE_SCHEMA_UNSUPPORTED",
            "NAVIGATION_UNKNOWN",
            "NAVIGATION_CONFLICT",
            "SELECTION_NOT_IN_NAVIGATION",
        } or any(value != "NOT_ATTEMPTED_REJECTED" for value in statuses):
            raise ValueError("invalid rejected report")
    elif status == "CLEANUP_UNCERTAIN" and (
        reason
        not in {
            "CHILD_CLEANUP_UNCERTAIN",
            "COLLECTOR_CLEANUP_UNCERTAIN",
            "WORKER_CLEANUP_UNCERTAIN",
            "WORKER_ERROR",
        }
        or any(
            value in {"NOT_ATTEMPTED", "IN_PROGRESS", "NOT_ATTEMPTED_REJECTED"}
            for value in statuses
        )
        or (
            reason == "CHILD_CLEANUP_UNCERTAIN"
            and not any(item["reason"] == "CHILD_CLEANUP_UNCERTAIN" for item in items)
        )
        or (
            reason == "COLLECTOR_CLEANUP_UNCERTAIN"
            and not any(item["reason"] == "COLLECTOR_PROTOCOL_ERROR" for item in items)
        )
    ):
        raise ValueError("invalid cleanup report")


def _validate_state(data, *, candidate_id, paths, root):
    template = _state(candidate_id, paths)
    if type(data) is not dict or set(data) != set(template) or len(_json(data)) > _MAX_OUTPUT:
        raise ValueError("invalid report")
    if (
        data["schema"] != _SCHEMA
        or data["candidate_id"] != candidate_id
        or data["status"]
        not in {"IN_PROGRESS", "OBSERVED_UNVERIFIED", "PARTIAL", "ERROR", "CLEANUP_UNCERTAIN"}
        or data["reason"]
        not in {
            "NONE",
            "CANDIDATE_UNAVAILABLE",
            "SUPERVISOR_UNSUPPORTED",
            "COLLECTOR_UNAVAILABLE",
            "CANDIDATE_SCHEMA_UNSUPPORTED",
            "NAVIGATION_UNKNOWN",
            "NAVIGATION_CONFLICT",
            "SELECTION_NOT_IN_NAVIGATION",
            "BUDGET_EXHAUSTED",
            "RESULT_LIMIT",
            "CHILD_CLEANUP_UNCERTAIN",
            "COLLECTOR_CLEANUP_UNCERTAIN",
            "WORKER_CLEANUP_UNCERTAIN",
            "WORKER_ERROR",
        }
        or tuple(
            data[k]
            for k in (
                "candidate_authentication",
                "retained_data_binding",
                "source_authentication",
                "freshness",
                "coverage",
                "authorization",
                "publication",
                "process_cleanup",
            )
        )
        != (
            "NOT_PROVEN",
            "NOT_PROVEN",
            "NOT_PROVEN",
            "NOT_PROVEN",
            "NOT_PROVEN",
            "NOT_PROVEN",
            "NOT_AUTHORIZED",
            "NOT_PROVEN",
        )
        or data["resource_limits"] != template["resource_limits"]
        or _sensitive(data, root)
    ):
        raise ValueError("invalid report qualification")
    digest = data["candidate_payload_sha256"]
    if digest is not None and (type(digest) is not str or _HEX_SHA.fullmatch(digest) is None):
        raise ValueError("invalid candidate digest")
    if type(data["items"]) is not list or len(data["items"]) != len(paths):
        raise ValueError("invalid item count")
    for expected, item in zip(paths, data["items"]):  # noqa: B905 - Python 3.9
        if (
            type(item) is not dict
            or set(item) != set(_item(expected))
            or item["path"] != expected
            or item["kind"] != _kind(expected)
        ):
            raise ValueError("invalid item")
        if (
            item["status"] not in _ITEM_STATES
            or item["reason"] not in _REASONS
            or item["transport_status"] not in _TRANSPORT
            or item["parse_status"] not in _PARSE
            or item["identity_status"] not in _IDENTITY
        ):
            raise ValueError("invalid item state")
        sha = item["page_sha256"]
        if sha is not None and (type(sha) is not str or _HEX_SHA.fullmatch(sha) is None):
            raise ValueError("invalid page digest")
        records = item["records"]
        if records is not None:
            if (
                type(records) is not list
                or not 1 <= len(records) <= 100
                or len(_json(item)) > _MAX_PAGE_OUTPUT
            ):
                raise ValueError("invalid record count")
            for row in records:
                if type(row) is not dict or set(row) != {
                    "kind",
                    "symbol",
                    "venue",
                    "raw_date",
                    "trading_hours",
                    "unknown_reasons",
                    "effective_date",
                }:
                    raise ValueError("invalid record")
                if (
                    row["kind"] != item["kind"]
                    or (
                        row["symbol"] is not None
                        and (
                            type(row["symbol"]) is not str
                            or _SYMBOL.fullmatch(row["symbol"]) is None
                        )
                    )
                    or row["venue"] not in {"SHFE", "INE", None}
                    or (row["raw_date"] is not None and not _valid_date(row["raw_date"]))
                    or (
                        row["trading_hours"] is not None
                        and _safe_time(row["trading_hours"], root) != row["trading_hours"]
                    )
                    or type(row["unknown_reasons"]) is not list
                    or any(x not in _GAPS for x in row["unknown_reasons"])
                    or row["effective_date"] != "UNKNOWN"
                ):
                    raise ValueError("invalid record content")
                gaps = row["unknown_reasons"]
                if (
                    len(gaps) != len(set(gaps))
                    or ("SYMBOL_UNKNOWN" in gaps) != (row["symbol"] is None)
                    or ("VENUE_UNKNOWN" in gaps) != (row["venue"] is None)
                    or ("SOURCE_DATE_UNKNOWN" in gaps and row["raw_date"] is not None)
                ):
                    raise ValueError("invalid record gaps")
        if item["status"] in {"CANDIDATE", "NEEDS_REVIEW"}:
            if (
                item["transport_status"] != "HTTP_OK"
                or item["parse_status"] not in {"CANDIDATE", "NEEDS_REVIEW"}
                or records is None
                or sha is None
            ):
                raise ValueError("invalid observed item")
            conflicts = (
                len({r["symbol"] for r in records if r["symbol"] is not None}) > 1
                or len({r["venue"] for r in records if r["venue"] is not None}) > 1
            )
            missing_identity = any(r["symbol"] is None or r["venue"] is None for r in records)
            expected_identity = (
                "CONFLICTING_FIELDS"
                if conflicts
                else "INCOMPLETE_FIELDS"
                if missing_identity
                else "CONSISTENT_FIELDS_UNVERIFIED"
            )
            expected_parse = (
                "NEEDS_REVIEW"
                if conflicts or any(r["unknown_reasons"] for r in records)
                else "CANDIDATE"
            )
            incomplete = any(r["raw_date"] is None or r["trading_hours"] is None for r in records)
            expected_status = (
                "NEEDS_REVIEW" if incomplete or expected_parse != "CANDIDATE" else "CANDIDATE"
            )
            expected_reason = (
                "DETAIL_FIELDS_INCOMPLETE"
                if incomplete
                else "DETAIL_REVIEW"
                if expected_status == "NEEDS_REVIEW"
                else "NONE"
            )
            if (item["identity_status"], item["parse_status"], item["status"], item["reason"]) != (
                expected_identity,
                expected_parse,
                expected_status,
                expected_reason,
            ):
                raise ValueError("inconsistent observed item")
        elif records is not None:
            raise ValueError("unexpected records")
        else:
            _validate_unobserved_item(item)
    _validate_overall_state(data)
    return data


def _read_last(output, candidate_id, paths, root):
    if type(output) is not bytes or len(output) > _MAX_PROTOCOL:
        return None
    for line in reversed(output.splitlines()):
        try:
            parsed = _validate_state(
                json.loads(line, object_pairs_hook=_unique_pairs),
                candidate_id=candidate_id,
                paths=paths,
                root=root,
            )
        except Exception:
            parsed = None
        if parsed is not None:
            return parsed
    return None


def _interrupted(latest, candidate_id, paths, *, cleanup):
    if latest is None:
        latest = _state(candidate_id, paths)
        for item in latest["items"]:
            item.update(
                {"status": "UNKNOWN", "reason": "ATTEMPT_UNKNOWN", "transport_status": "UNKNOWN"}
            )
    else:
        for item in latest["items"]:
            if item["status"] == "IN_PROGRESS":
                item.update(
                    {"status": "UNKNOWN", "reason": "INTERRUPTED", "transport_status": "UNKNOWN"}
                )
            elif item["status"] == "NOT_ATTEMPTED":
                item.update({"status": "NOT_ATTEMPTED_BUDGET", "reason": "BUDGET_EXHAUSTED"})
    latest["status"] = "CLEANUP_UNCERTAIN" if cleanup else "PARTIAL"
    latest["reason"] = "WORKER_CLEANUP_UNCERTAIN" if cleanup else "BUDGET_EXHAUSTED"
    return latest


def _supervisor_supported():
    return (
        os.name == "posix"
        and hasattr(os, "killpg")
        and hasattr(signal, "SIGCHLD")
        and signal.getsignal(signal.SIGCHLD) == signal.SIG_DFL
        and threading.current_thread() is threading.main_thread()
        and threading.active_count() == 1
    )


def _signal_owned_group(child, signum=signal.SIGKILL):
    if not _supervisor_supported():
        raise OSError("unsupported process supervision")
    # The child remains unreaped here. This is scoped to the session created
    # by this CLI; it is not proof against arbitrary native reapers or hosts.
    os.killpg(child.pid, signum)


def _worker_term_handler(signum, frame):
    global _worker_term_received
    _worker_term_received = True


def _supervise(args):
    root, candidate_id, paths = args["root"], args["candidate_id"], args["paths"]
    if not _supervisor_supported():
        return _reject(_state(candidate_id, paths), "SUPERVISOR_UNSUPPORTED")
    deadline = time.monotonic() + args["budget"]
    args = dict(args, deadline=deadline)
    try:
        child = subprocess.Popen(
            [sys.executable, "-m", "bt_api_py.trading_calendar_detail_batch", "--_worker"],
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            shell=False,
            start_new_session=True,
        )
    except Exception:
        state = _interrupted(None, candidate_id, paths, cleanup=True)
        state["reason"] = "WORKER_ERROR"
        return state
    latest = None
    payload = _json(args)
    sent = 0
    received = 0
    pending = bytearray()
    eof = False
    final_signal_sent = False
    try:
        os.set_blocking(child.stdin.fileno(), False)
        os.set_blocking(child.stdout.fileno(), False)
        with selectors.DefaultSelector() as selector:
            selector.register(child.stdin, selectors.EVENT_WRITE, "input")
            selector.register(child.stdout, selectors.EVENT_READ, "output")
            while not eof:
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    break
                events = selector.select(remaining)
                if not events:
                    break
                for key, _ in events:
                    if key.data == "input":
                        amount = os.write(child.stdin.fileno(), payload[sent:])
                        if amount <= 0:
                            raise OSError("worker input error")
                        sent += amount
                        if sent == len(payload):
                            selector.unregister(child.stdin)
                            child.stdin.close()
                    else:
                        chunk = os.read(
                            child.stdout.fileno(), min(65536, _MAX_PROTOCOL + 1 - received)
                        )
                        if not chunk:
                            selector.unregister(child.stdout)
                            eof = True
                            break
                        received += len(chunk)
                        if received > _MAX_PROTOCOL:
                            raise ValueError("worker protocol limit")
                        pending.extend(chunk)
                        while b"\n" in pending:
                            line, _, tail = pending.partition(b"\n")
                            pending = bytearray(tail)
                            state = _read_last(bytes(line), candidate_id, paths, root)
                            if state is not None:
                                latest = state
                            if state is not None and state["status"] == "CLEANUP_UNCERTAIN":
                                try:
                                    _signal_owned_group(child)
                                    child.wait(timeout=1.0)
                                except Exception:
                                    return state
                                return state
                            if (
                                state is not None
                                and state["status"] != "IN_PROGRESS"
                                and not final_signal_sent
                            ):
                                try:
                                    _signal_owned_group(child, signal.SIGTERM)
                                except ProcessLookupError:
                                    final_signal_sent = True
                                except Exception:
                                    return _interrupted(state, candidate_id, paths, cleanup=True)
                                final_signal_sent = True
                        if len(pending) > _MAX_OUTPUT:
                            raise ValueError("worker line limit")
        if eof and not pending and not final_signal_sent:
            try:
                # EOF does not prove that a provider descendant exited. Signal
                # while this Popen child is still unreaped, even on success.
                with contextlib.suppress(ProcessLookupError):
                    _signal_owned_group(child, signal.SIGTERM)
            except Exception:
                return _interrupted(latest, candidate_id, paths, cleanup=True)
        if eof and not pending:
            remaining = deadline - time.monotonic()
            if remaining > 0:
                try:
                    child.wait(timeout=remaining)
                except subprocess.TimeoutExpired:
                    pass
                else:
                    if (
                        child.returncode == 0
                        and latest is not None
                        and latest["status"] != "IN_PROGRESS"
                    ):
                        return latest
                    return _interrupted(latest, candidate_id, paths, cleanup=True)
    except Exception:
        eof = False
    finally:
        if child.stdin is not None:
            child.stdin.close()
        if child.stdout is not None:
            child.stdout.close()
    try:
        # The Popen child is not reaped yet, so its process-group ID cannot be recycled.
        _signal_owned_group(child)
        child.wait(timeout=1.0)
    except Exception:
        return _interrupted(latest, candidate_id, paths, cleanup=True)
    if latest is not None and latest["status"] == "CLEANUP_UNCERTAIN":
        return latest
    return _interrupted(latest, candidate_id, paths, cleanup=True)


class _ArgumentError(Exception):
    pass


class _Parser(argparse.ArgumentParser):
    def error(self, message):
        raise _ArgumentError


def _worker():
    try:
        global _worker_term_received
        _worker_term_received = False
        raw = sys.stdin.buffer.read(_MAX_STDIN + 1)
        if len(raw) > _MAX_STDIN:
            return 2
        data = json.loads(raw, object_pairs_hook=_unique_pairs)
        if type(data) is not dict or set(data) != {
            "root",
            "candidate_id",
            "paths",
            "budget",
            "per_request",
            "deadline",
            "filesystem",
        }:
            return 2
        root, candidate_id, paths = data["root"], data["candidate_id"], data["paths"]
        budget, per_request, deadline = data["budget"], data["per_request"], data["deadline"]
        if (
            data["filesystem"] != "TRUSTED_LOCAL_POSIX"
            or not _arguments_valid(root, candidate_id, paths, budget, per_request)
            or type(deadline) not in (int, float)
            or not math.isfinite(deadline)
            or deadline > time.monotonic() + budget + 1
        ):
            return 2
        written = 0

        def emit(state):
            nonlocal written
            _validate_state(state, candidate_id=candidate_id, paths=paths, root=root)
            output = _json(state) + b"\n"
            written += len(output)
            if written > _MAX_PROTOCOL:
                raise ValueError("protocol output limit")
            sys.stdout.buffer.write(output)
            sys.stdout.buffer.flush()

        state = _work(root, candidate_id, paths, deadline, per_request, emit)
        signal.signal(signal.SIGTERM, _worker_term_handler)
        emit(state)
        if state["status"] == "CLEANUP_UNCERTAIN":
            # Do not signal any group here. A parent that created this session
            # may signal it while this worker remains alive and unreaped.
            until = max(time.monotonic() + 1.0, deadline + 1.0)
            while time.monotonic() < until:
                time.sleep(max(0.0, min(0.2, until - time.monotonic())))
        else:
            # Keep the group leader alive and unreaped until the supervising
            # parent has had a chance to signal any remaining descendants.
            until = max(time.monotonic() + 1.0, deadline + 1.0)
            while not _worker_term_received and time.monotonic() < until:
                time.sleep(max(0.0, min(0.2, until - time.monotonic())))
        return 0
    except Exception:
        return 2


def main(argv=None):
    argv = sys.argv[1:] if argv is None else argv
    if argv == ["--_worker"]:
        return _worker()
    parser = _Parser(description=__doc__)
    parser.add_argument("--calendar-root", required=True)
    parser.add_argument("--candidate-id", required=True)
    parser.add_argument("--trusted-local-posix", action="store_true")
    parser.add_argument("--path", action="append", required=True)
    parser.add_argument("--budget-seconds", type=float, required=True)
    parser.add_argument("--per-request-seconds", type=float, default=10.0)
    try:
        args = parser.parse_args(argv)
        if (
            not args.trusted_local_posix
            or os.name != "posix"
            or not hasattr(os, "killpg")
            or not _arguments_valid(
                args.calendar_root,
                args.candidate_id,
                args.path,
                args.budget_seconds,
                args.per_request_seconds,
            )
        ):
            raise _ArgumentError
        state = _supervise(
            {
                "root": args.calendar_root,
                "candidate_id": args.candidate_id,
                "paths": args.path,
                "budget": args.budget_seconds,
                "per_request": args.per_request_seconds,
                "filesystem": "TRUSTED_LOCAL_POSIX",
            }
        )
        _validate_state(
            state, candidate_id=args.candidate_id, paths=args.path, root=args.calendar_root
        )
        sys.stdout.buffer.write(_json(state) + b"\n")
        if state["status"] == "ERROR":
            return 2
        if state["status"] == "CLEANUP_UNCERTAIN":
            return 23
        return 0 if state["status"] == "OBSERVED_UNVERIFIED" else 10
    except Exception:
        sys.stdout.buffer.write(b'{"status":"ERROR","reason":"INVALID_ARGUMENTS"}\n')
        return 2


if __name__ == "__main__":
    sys.exit(main())
