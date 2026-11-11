"""Experimental, explicitly requested retention of unverified preview candidates.

Candidates are incomplete review material, never authorization or source proof.
Only explicit ``show --against-current`` reads CURRENT; this API never publishes.
Names are no-overwrite by this API;
0600 files can still be altered by their owner. The caller asserts a trusted,
cooperating local POSIX filesystem; hostile writers, Windows, network filesystems
and power-loss durability are not proved. Ordinary preview output contains only
finite metadata. The separate ``show`` command explicitly prints candidate data.
"""

from __future__ import annotations

import argparse
import contextlib
import hashlib
import json
import os
import re
import stat
import sys
import uuid
from copy import deepcopy
from dataclasses import asdict, dataclass
from datetime import date, datetime, time, timedelta, timezone
from zoneinfo import ZoneInfo

from bt_api_py.trading_calendar_preview_reports import (
    TRUSTED_LOCAL_POSIX,
    _directory_matches,
    _identity,
    _open_directory,
    _remove_owned_temp,
    _supported,
)

_ID = re.compile(r"[0-9a-f]{32}\Z", re.ASCII)
_SHA = re.compile(r"[0-9a-f]{64}\Z", re.ASCII)
_SCHEMA = "bt_api.calendar-preview-candidate/1"
_SCHEMA_CATALOG = "bt_api.calendar-preview-candidate/2"
_LIMIT = 2 * 1024 * 1024
_LIMIT_CATALOG = 32 * 1024 * 1024
_CATALOG_BYTES = 24 * 1024 * 1024
_CATALOG_ROWS = 100_000
_CATALOG_NODES = 2_800_000
_CATALOG_FIELDS = (
    "exchange_id",
    "product_id",
    "product_class",
    "instrument_id",
    "exchange_instrument_id",
    "underlying_instrument_id",
    "open_date",
    "expire_date",
    "create_date",
    "start_delivery_date",
    "end_delivery_date",
    "is_trading",
    "life_phase",
    "delivery_year",
    "delivery_month",
    "options_type",
    "strike_price",
    "underlying_multiple",
    "combination_type",
    "kind",
    "identity_status",
    "lifecycle_status",
    "underlying_relation",
    "gaps",
)
_CATALOG_REASONS = frozenset(
    {
        "SCOPE_OR_DAY_INVALID",
        "FILESYSTEM_UNSUPPORTED",
        "CACHE_RESOURCE_LIMIT",
        "CACHE_UNAVAILABLE_OR_CHANGED",
        "CACHE_INVALID",
        "EMPTY_CATALOG",
        "READER_UNAVAILABLE",
        "CACHE_SENSITIVE_VALUE",
    }
)
_COMPARE_OUTPUT_LIMIT = 1024 * 1024
_COMPARE_BASELINE_BYTES = 16 * 1024 * 1024
_COMPARE_DAYS_PER_VENUE = 731
_COMPARE_ROWS_PER_VENUE = 1000
_COMPARE_SOURCES = 1000
_COMPARE_NESTED_ITEMS = 250_000
_COMPARE_SCOPE_DAY_WORK = 150_000
_COMPARE_WINDOW_WORK = 500_000
_COMPARE_CROSS_WINDOW_WORK = 500_000
_VENUES = frozenset({"SHFE", "INE", "CZCE", "DCE", "CFFEX", "GFEX"})
_SHANGHAI = ZoneInfo("Asia/Shanghai")


@dataclass(frozen=True)
class CandidateRetention:
    status: str
    reason: str
    candidate_id: str


def _canonical(value):
    return json.dumps(
        value, ensure_ascii=True, sort_keys=True, separators=(",", ":"), allow_nan=False
    ).encode("ascii")


def _page_to_data(page):
    if page is None:
        return None
    from bt_api_ctp.calendar_shfe_hours_candidates import _recheck_page

    _recheck_page(page)
    return asdict(page)


def _page_from_data(value):
    if value is None:
        return None
    from bt_api_ctp.calendar_shfe_hours_candidates import _recheck_page
    from bt_api_ctp.calendar_shfe_page import PageCandidate, ProductCandidate, StageCandidate

    if (
        type(value) is not dict
        or set(value)
        != {
            "status",
            "source_id",
            "sha256",
            "products",
            "raw_notes",
            "review_reasons",
            "verification",
        }
        or type(value["products"]) is not list
    ):
        raise ValueError("invalid page")
    products = []
    for item in value["products"]:
        if (
            type(item) is not dict
            or set(item) != {"kind", "name", "raw_product_cell", "raw_time_cell", "stages"}
            or type(item["stages"]) is not list
        ):
            raise ValueError("invalid product")
        stages = tuple(StageCandidate(**stage) for stage in item["stages"])
        products.append(
            ProductCandidate(
                item["kind"], item["name"], item["raw_product_cell"], item["raw_time_cell"], stages
            )
        )
    if type(value["raw_notes"]) is not list or type(value["review_reasons"]) is not list:
        raise ValueError("invalid page notes")
    page = PageCandidate(
        value["status"],
        value["source_id"],
        value["sha256"],
        tuple(products),
        tuple(value["raw_notes"]),
        tuple(value["review_reasons"]),
        value["verification"],
    )
    _recheck_page(page)
    return page


def _validate_source_checks(checks):
    from bt_api_py.trading_calendar_preview import _PAGE, _TRANSPORT

    if type(checks) is not dict or set(checks) != {
        "hours",
        "holiday",
        "holiday_conversion_status",
        "merge_status",
        "merge_reason",
        "normalization_status",
    }:
        raise ValueError("invalid source checks")
    extra = {"CTP_UNAVAILABLE", "COLLECTOR_ERROR", "COLLECTOR_PROTOCOL_ERROR"}
    allowed = _PAGE | _TRANSPORT | extra
    reasons = {
        "NOT_REQUESTED",
        "NOT_ATTEMPTED",
        "CANDIDATE",
        "SHFE_NOT_VERIFIED",
        "RETRY_LIMIT_REACHED",
        "RETRY_BUDGET_EXHAUSTED",
        "BUDGET_EXHAUSTED",
        "CHILD_CLEANUP_UNCERTAIN",
    }
    for kind in ("hours", "holiday"):
        item = checks[kind]
        if (
            type(item) is not dict
            or set(item) != {"status", "reason", "attempts"}
            or item["status"] not in allowed | {"NOT_REQUESTED", "NOT_ATTEMPTED"}
            or item["reason"] not in reasons
            or type(item["attempts"]) is not list
            or len(item["attempts"]) > 3
        ):
            raise ValueError("invalid source summary")
        attempts = item["attempts"]
        if not attempts and (item["status"], item["reason"]) not in {
            ("NOT_REQUESTED", "NOT_REQUESTED"),
            ("NOT_ATTEMPTED", "NOT_ATTEMPTED"),
        }:
            raise ValueError("missing source attempts")
        if attempts and (
            item["reason"] in {"NOT_REQUESTED", "NOT_ATTEMPTED"}
            or type(attempts[-1]) is not dict
            or item["status"] != attempts[-1].get("source_status")
        ):
            raise ValueError("source status mismatch")
        for number, attempt in enumerate(attempts, 1):
            if (
                type(attempt) is not dict
                or set(attempt)
                != {
                    "number",
                    "transport_status",
                    "source_status",
                    "verification",
                    "product_count",
                    "parse_status",
                }
                or type(attempt["number"]) is not int
                or attempt["number"] != number
                or type(attempt["product_count"]) is not int
                or not 0 <= attempt["product_count"] <= 1024
                or (kind == "holiday" and attempt["product_count"] != 0)
                or attempt["verification"] != "UNVERIFIED"
                or attempt["transport_status"] not in _TRANSPORT | extra
                or attempt["source_status"] not in allowed
                or attempt["parse_status"]
                not in _PAGE | {"NOT_ATTEMPTED", "PARSER_ERROR", "UNKNOWN"}
            ):
                raise ValueError("invalid source attempt")
            if attempt["transport_status"] == "HTTP_OK":
                if (
                    attempt["source_status"] not in _PAGE
                    or attempt["parse_status"] != attempt["source_status"]
                ):
                    raise ValueError("invalid successful transport")
            elif (
                attempt["source_status"] != attempt["transport_status"]
                or attempt["product_count"] != 0
            ):
                raise ValueError("invalid failed transport")
            else:
                transport = attempt["transport_status"]
                if transport == "PARSER_ERROR":
                    expected_parse = "PARSER_ERROR"
                elif (
                    transport
                    in {
                        "CHILD_ERROR",
                        "CHILD_TIMEOUT",
                        "CHILD_CLEANUP_UNCERTAIN",
                        "RESULT_TOO_LARGE",
                    }
                    | extra
                ):
                    expected_parse = "UNKNOWN"
                else:
                    expected_parse = "NOT_ATTEMPTED"
                if attempt["parse_status"] != expected_parse:
                    raise ValueError("failed transport has invalid parse status")
        if attempts:
            last = attempts[-1]
            if last["transport_status"] in {"NETWORK_TIMEOUT", "CHILD_TIMEOUT"}:
                valid_reasons = {"RETRY_LIMIT_REACHED", "RETRY_BUDGET_EXHAUSTED"}
            elif last["transport_status"] == "CHILD_CLEANUP_UNCERTAIN":
                valid_reasons = {"CHILD_CLEANUP_UNCERTAIN"}
            elif last["source_status"] == "CANDIDATE":
                if kind == "holiday" and checks["holiday_conversion_status"] != "CANDIDATE":
                    valid_reasons = {"SHFE_NOT_VERIFIED"}
                else:
                    valid_reasons = {"CANDIDATE"}
            else:
                valid_reasons = {"SHFE_NOT_VERIFIED"}
            if item["reason"] not in valid_reasons:
                raise ValueError("source reason disagrees with final attempt")
    if (
        checks["holiday_conversion_status"]
        not in {
            "NOT_ATTEMPTED",
            "CANDIDATE",
            "INVALID_CANDIDATE",
            "CONVERTER_UNAVAILABLE",
            "CONVERSION_ERROR",
            "CONVERSION_PROTOCOL_ERROR",
        }
        or checks["merge_status"]
        not in {
            "NOT_ATTEMPTED",
            "PARTIAL",
            "MERGE_ERROR",
            "MERGE_UNAVAILABLE",
            "MERGE_PROTOCOL_ERROR",
        }
        or checks["merge_reason"] != "NONE"
        or checks["normalization_status"] not in {"NOT_ATTEMPTED", "NORMALIZED"}
    ):
        raise ValueError("invalid processing status")
    hours = checks["hours"]
    holiday = checks["holiday"]
    conversion = checks["holiday_conversion_status"]
    merge = checks["merge_status"]
    normalized = checks["normalization_status"]
    if (not hours["attempts"] or not holiday["attempts"]) and (
        hours["attempts"]
        or holiday["attempts"]
        or hours["status"] != "NOT_REQUESTED"
        or holiday["status"] != "NOT_REQUESTED"
    ):
        raise ValueError("incomplete source attempt sequence")
    if conversion == "NOT_ATTEMPTED":
        if (
            holiday["status"] == "CANDIDATE"
            or merge != "NOT_ATTEMPTED"
            or normalized != "NOT_ATTEMPTED"
        ):
            raise ValueError("unattempted conversion contradicts source or later steps")
    elif conversion != "CANDIDATE":
        if (
            holiday["status"] != "CANDIDATE"
            or merge != "NOT_ATTEMPTED"
            or normalized != "NOT_ATTEMPTED"
        ):
            raise ValueError("failed conversion contradicts source or later steps")
    else:
        if holiday["status"] != "CANDIDATE":
            raise ValueError("conversion lacks successful source")
        if merge == "PARTIAL":
            if normalized != "NORMALIZED":
                raise ValueError("partial merge lacks completed normalization")
        elif (
            merge not in {"MERGE_ERROR", "MERGE_UNAVAILABLE", "MERGE_PROTOCOL_ERROR"}
            or normalized != "NOT_ATTEMPTED"
        ):
            raise ValueError("conversion contradicts merge or normalization")


def _catalog_day(value):
    if type(value) is not str or re.fullmatch(r"[0-9]{8}", value, re.ASCII) is None:
        return None
    try:
        return value if datetime.strptime(value, "%Y%m%d").strftime("%Y%m%d") == value else None
    except ValueError:
        return None


def unknown_catalog(reason, requested_day=None, observed_count=None):
    """Build a value-free unknown observation; never imply an empty listing."""
    if reason not in _CATALOG_REASONS:
        reason = "CACHE_INVALID"
    if type(observed_count) is not int or not 0 <= observed_count <= _CATALOG_ROWS:
        observed_count = None
    return {
        "status": "UNKNOWN",
        "reason": reason,
        "trading_day": _catalog_day(requested_day),
        "rows": None,
        "columns": list(_CATALOG_FIELDS),
        "conflicts": None,
        "skipped_rows": None,
        "observed_count": observed_count,
        "built_at_utc": None,
        "published_at_utc": None,
        "content_sha256": None,
        "retained_catalog_source_binding": "NOT_PROVEN",
        "completeness": "NOT_PROVEN",
        "source_authentication": "NOT_PROVEN",
        "freshness": "NOT_PROVEN",
        "absence": "NOT_PROVEN",
        "metadata_digest_protection": "NOT_COVERED",
        "writer_quiescence": "NOT_PROVEN",
        "publication": "NOT_AUTHORIZED",
        "scope_binding": "NOT_RETAINED",
    }


def catalog_observation(snapshot, *, requested_day, scope, cache_dir):
    """Exclude direct case/path variants, not arbitrary secrets or encodings.

    The supplied cache digest does not prove a binding to the retained catalog.
    """
    try:
        from bt_api_ctp.calendar_local_catalog import LocalCatalogItem, LocalCatalogSnapshot
        from bt_api_ctp.instrument_catalog_cache import InstrumentCatalogEntry

        if type(snapshot) is not LocalCatalogSnapshot:
            raise ValueError("invalid catalog observation")
        if snapshot.status == "UNKNOWN":
            return unknown_catalog(snapshot.reason, requested_day)
        if (
            snapshot.status not in {"CANDIDATE", "PARTIAL"}
            or snapshot.reason not in {"UNVERIFIED_CACHE", "REFERENCE_GAPS_PRESENT"}
            or snapshot.scope != scope
            or snapshot.trading_day != _catalog_day(requested_day)
            or type(snapshot.entries) is not tuple
            or len(snapshot.entries) > _CATALOG_ROWS
            or type(snapshot.conflicts) is not tuple
            or len(snapshot.conflicts) > _CATALOG_ROWS
            or (
                snapshot.completeness,
                snapshot.source_authentication,
                snapshot.freshness,
                snapshot.absence,
                snapshot.metadata_digest_protection,
                snapshot.writer_quiescence,
                snapshot.publication,
            )
            != (
                "UNVERIFIED_DECLARATION",
                "NOT_PROVEN",
                "NOT_PROVEN",
                "NOT_PROVEN",
                "NOT_COVERED",
                "NOT_PROVEN",
                "NOT_AUTHORIZED",
            )
        ):
            raise ValueError("invalid catalog observation")
        count = len(snapshot.entries)
        if count * (len(_CATALOG_FIELDS) + 1) + len(snapshot.conflicts) > _CATALOG_NODES:
            return unknown_catalog("CACHE_RESOURCE_LIMIT", requested_day, count)
        cache_path = os.fspath(cache_dir)
        paths = (
            (cache_path, os.path.normpath(cache_path))
            if type(cache_path) is str and cache_path
            else ()
        )
        sensitive = tuple(
            folded
            for value in (scope, *paths)
            if type(value) is str and (folded := value.casefold())
        )

        def private(value):
            return type(value) is str and any(item in value.casefold() for item in sensitive)

        rows, size, nodes = [], 0, len(snapshot.conflicts)
        for item in snapshot.entries:
            if type(item) is not LocalCatalogItem or type(item.entry) is not InstrumentCatalogEntry:
                raise ValueError("invalid catalog row")
            row = [getattr(item.entry, key) for key in _CATALOG_FIELDS[:19]] + [
                item.kind,
                item.identity_status,
                item.lifecycle_status,
                item.underlying_relation,
                list(item.gaps),
            ]
            if any(private(value) for value in row if type(value) is str):
                return unknown_catalog("CACHE_SENSITIVE_VALUE", requested_day, count)
            nodes += len(row) + 1 + len(row[-1])
            size += len(_canonical(row)) + 1
            if nodes > _CATALOG_NODES or size > _CATALOG_BYTES:
                return unknown_catalog("CACHE_RESOURCE_LIMIT", requested_day, count)
            rows.append(row)
        if any(
            private(value)
            for value in (
                *snapshot.conflicts,
                snapshot.built_at_utc,
                snapshot.published_at_utc,
                snapshot.content_sha256,
            )
        ):
            return unknown_catalog("CACHE_SENSITIVE_VALUE", requested_day, count)
        data = {
            "status": snapshot.status,
            "reason": snapshot.reason,
            "trading_day": snapshot.trading_day,
            "rows": rows,
            "columns": list(_CATALOG_FIELDS),
            "conflicts": list(snapshot.conflicts),
            "skipped_rows": snapshot.skipped_rows,
            "observed_count": count,
            "built_at_utc": snapshot.built_at_utc,
            "published_at_utc": snapshot.published_at_utc,
            "content_sha256": snapshot.content_sha256,
            "retained_catalog_source_binding": "NOT_PROVEN",
            "completeness": snapshot.completeness,
            "source_authentication": snapshot.source_authentication,
            "freshness": snapshot.freshness,
            "absence": snapshot.absence,
            "metadata_digest_protection": snapshot.metadata_digest_protection,
            "writer_quiescence": snapshot.writer_quiescence,
            "publication": snapshot.publication,
            "scope_binding": "NOT_RETAINED",
        }
        _validate_catalog(data)
        return data
    except ImportError:
        return unknown_catalog("READER_UNAVAILABLE", requested_day)
    except Exception:
        return unknown_catalog("CACHE_INVALID", requested_day)


def _validate_catalog(data):
    """Validate structure and all row classifications without authenticating them."""
    fields = {
        "status",
        "reason",
        "trading_day",
        "rows",
        "columns",
        "conflicts",
        "skipped_rows",
        "observed_count",
        "built_at_utc",
        "published_at_utc",
        "content_sha256",
        "retained_catalog_source_binding",
        "completeness",
        "source_authentication",
        "freshness",
        "absence",
        "metadata_digest_protection",
        "writer_quiescence",
        "publication",
        "scope_binding",
    }
    if type(data) is not dict or set(data) != fields or data["columns"] != list(_CATALOG_FIELDS):
        raise ValueError("invalid catalog")
    if any(
        data[key] != "NOT_PROVEN"
        for key in (
            "source_authentication",
            "freshness",
            "absence",
            "writer_quiescence",
            "retained_catalog_source_binding",
        )
    ) or (data["metadata_digest_protection"], data["publication"], data["scope_binding"]) != (
        "NOT_COVERED",
        "NOT_AUTHORIZED",
        "NOT_RETAINED",
    ):
        raise ValueError("invalid catalog qualification")
    count, day = data["observed_count"], data["trading_day"]
    if count is not None and (type(count) is not int or not 0 <= count <= _CATALOG_ROWS):
        raise ValueError("invalid catalog count")
    if day is not None and _catalog_day(day) != day:
        raise ValueError("invalid catalog day")
    if data["status"] == "UNKNOWN":
        if (
            data["reason"] not in _CATALOG_REASONS
            or data["rows"] is not None
            or data["conflicts"] is not None
            or data["skipped_rows"] is not None
            or data["built_at_utc"] is not None
            or data["published_at_utc"] is not None
            or data["content_sha256"] is not None
            or data["completeness"] != "NOT_PROVEN"
        ):
            raise ValueError("invalid unknown catalog")
        return
    from bt_api_ctp.calendar_local_catalog import _item, _valid_utc
    from bt_api_ctp.instrument_catalog_cache import InstrumentCatalogEntry

    rows, conflicts = data["rows"], data["conflicts"]
    if (
        data["status"] not in {"CANDIDATE", "PARTIAL"}
        or day is None
        or type(rows) is not list
        or not 1 <= len(rows) <= _CATALOG_ROWS
        or count != len(rows)
        or type(conflicts) is not list
        or len(conflicts) > _CATALOG_ROWS
        or any(
            type(item) is not str or not item or len(item) > 80 or item != item.strip()
            for item in conflicts
        )
        or len(conflicts) != len(set(conflicts))
        or conflicts != sorted(conflicts)
        or type(data["skipped_rows"]) is not int
        or data["skipped_rows"] < 0
        or not _valid_utc(data["built_at_utc"])
        or not _valid_utc(data["published_at_utc"])
        or type(data["content_sha256"]) is not str
        or _SHA.fullmatch(data["content_sha256"]) is None
        or data["completeness"] != "UNVERIFIED_DECLARATION"
    ):
        raise ValueError("invalid catalog data")
    entries, venues, seen, nodes = [], {}, set(), len(conflicts)
    conflict_set = set(conflicts)
    for row in rows:
        if type(row) is not list or len(row) != len(_CATALOG_FIELDS) or type(row[-1]) is not list:
            raise ValueError("invalid catalog row")
        nodes += 1 + len(row) + len(row[-1])
        if nodes > _CATALOG_NODES or any(type(gap) is not str for gap in row[-1]):
            raise ValueError("catalog node budget")
        entry = InstrumentCatalogEntry(**dict(zip(_CATALOG_FIELDS[:19], row[:19])))  # noqa: B905
        key = (entry.instrument_id, entry.exchange_id)
        if key in seen or entry.instrument_id in conflict_set:
            raise ValueError("duplicate or conflicted row")
        seen.add(key)
        venues.setdefault(entry.instrument_id, set()).add(entry.exchange_id)
        entries.append(entry)
    ambiguous = {name for name, value in venues.items() if len(value) != 1}
    has_gaps = False
    for row, entry in zip(rows, entries):  # noqa: B905 - Python 3.9
        item = _item(entry, ambiguous, day)
        if row[19:] != [
            item.kind,
            item.identity_status,
            item.lifecycle_status,
            item.underlying_relation,
            list(item.gaps),
        ]:
            raise ValueError("catalog classification mismatch")
        has_gaps |= bool(item.gaps)
    partial = bool(conflicts or data["skipped_rows"] or has_gaps)
    if (data["status"], data["reason"]) != (
        ("PARTIAL", "REFERENCE_GAPS_PRESENT") if partial else ("CANDIDATE", "UNVERIFIED_CACHE")
    ):
        raise ValueError("catalog status mismatch")


def _payload(merged, supplement, hours_page, source_checks, catalog=None):
    from bt_api_ctp.calendar_candidate_merge import _REQUIRED_REVIEW, _copy_validated
    from bt_api_ctp.calendar_hours_merge import _partial

    if (merged is None) == (supplement is None):
        raise ValueError("exactly one candidate input is required")
    candidate = _copy_validated(supplement) if merged is None else _partial(merged)
    nights = (
        None
        if merged is None
        else [
            {
                "holiday": n.holiday,
                "local_date": n.local_date.isoformat(),
                "source_id": n.source_id,
                "raw_text": n.raw_text,
                "trading_day": None,
            }
            for n in merged.unresolved_nights
        ]
    )
    try:
        hours = _page_to_data(hours_page)
    except (ValueError, TypeError, AttributeError):
        hours = None
    result = {
        "content": candidate.content,
        "unresolved_nights": nights,
        "holiday_status": "UNKNOWN" if merged is None else "CAPTURED_UNVERIFIED",
        "unresolved_nights_status": "UNKNOWN" if merged is None else "CAPTURED_UNVERIFIED",
        "input_scope": "YAML_ONLY" if merged is None else "YAML_SHFE_PARTIAL",
        "source_checks": source_checks,
        "observed_at_utc": datetime.now(timezone.utc)  # noqa: UP017 - Python 3.9
        .isoformat(timespec="microseconds")
        .replace("+00:00", "Z"),
        "clock_status": "CLOCK_UNVERIFIED",
        "remaining_checks": list(_REQUIRED_REVIEW if merged is None else merged.remaining_checks),
        "hours_page": hours,
        "hours_page_status": "CAPTURED_UNVERIFIED" if hours is not None else "UNKNOWN",
        "identity_mapping": "UNKNOWN",
        "night_mapping": "UNKNOWN",
        "baseline": "NOT_OBSERVED",
        "difference": "NOT_ASSESSED",
        "completeness": "INCOMPLETE",
        "verification": "UNVERIFIED",
        "publication": "NOT_AUTHORIZED",
        "durability": "UNCONFIRMED",
    }
    if catalog is not None:
        result["catalog"] = catalog
    return result


def _validate_payload(payload, *, catalog=False):
    from bt_api_ctp.calendar_candidate_merge import PartialMergeResult
    from bt_api_ctp.calendar_candidate_normalize import normalize_calendar_candidate
    from bt_api_ctp.calendar_hours_merge import _partial
    from bt_api_ctp.calendar_shfe_candidates import UnresolvedNight
    from bt_api_ctp.calendar_supplement import _validate

    expected = {
        "content",
        "unresolved_nights",
        "holiday_status",
        "unresolved_nights_status",
        "input_scope",
        "source_checks",
        "observed_at_utc",
        "clock_status",
        "remaining_checks",
        "hours_page",
        "hours_page_status",
        "identity_mapping",
        "night_mapping",
        "baseline",
        "difference",
        "completeness",
        "verification",
        "publication",
        "durability",
    }
    if catalog:
        expected.add("catalog")
    if type(payload) is not dict or set(payload) != expected:
        raise ValueError("invalid payload")
    if catalog:
        _validate_catalog(payload["catalog"])
    if (
        payload["clock_status"] != "CLOCK_UNVERIFIED"
        or type(payload["observed_at_utc"]) is not str
        or not payload["observed_at_utc"].endswith("Z")
    ):
        raise ValueError("invalid observation clock")
    observed = datetime.fromisoformat(payload["observed_at_utc"][:-1] + "+00:00")
    if (
        observed.utcoffset() != timedelta(0)
        or observed.isoformat(timespec="microseconds").replace("+00:00", "Z")
        != payload["observed_at_utc"]
    ):
        raise ValueError("invalid observation time")
    _validate_source_checks(payload["source_checks"])
    if tuple(
        payload[key]
        for key in (
            "identity_mapping",
            "night_mapping",
            "baseline",
            "difference",
            "completeness",
            "verification",
            "publication",
            "durability",
        )
    ) != (
        "UNKNOWN",
        "UNKNOWN",
        "NOT_OBSERVED",
        "NOT_ASSESSED",
        "INCOMPLETE",
        "UNVERIFIED",
        "NOT_AUTHORIZED",
        "UNCONFIRMED",
    ):
        raise ValueError("invalid qualification")
    if type(payload["remaining_checks"]) is not list:
        raise ValueError("invalid reviews")
    if payload["input_scope"] == "YAML_ONLY":
        if (
            payload["holiday_status"],
            payload["unresolved_nights_status"],
            payload["unresolved_nights"],
        ) != ("UNKNOWN", "UNKNOWN", None):
            raise ValueError("unknown holiday review must not be presented as empty")
        raw_nights = []
        if payload["source_checks"]["merge_status"] not in {
            "NOT_ATTEMPTED",
            "MERGE_ERROR",
            "MERGE_UNAVAILABLE",
            "MERGE_PROTOCOL_ERROR",
        }:
            raise ValueError("invalid YAML-only merge state")
    elif payload["input_scope"] == "YAML_SHFE_PARTIAL":
        if (
            (payload["holiday_status"], payload["unresolved_nights_status"])
            != ("CAPTURED_UNVERIFIED", "CAPTURED_UNVERIFIED")
            or type(payload["unresolved_nights"]) is not list
            or not payload["unresolved_nights"]
        ):
            raise ValueError("invalid holiday review")
        raw_nights = payload["unresolved_nights"]
        if (
            payload["source_checks"]["holiday"]["status"],
            payload["source_checks"]["holiday_conversion_status"],
            payload["source_checks"]["merge_status"],
            payload["source_checks"]["normalization_status"],
        ) != ("CANDIDATE", "CANDIDATE", "PARTIAL", "NORMALIZED"):
            raise ValueError("invalid merged source state")
    else:
        raise ValueError("invalid input scope")
    nights = []
    for item in raw_nights:
        if (
            type(item) is not dict
            or set(item) != {"holiday", "local_date", "source_id", "raw_text", "trading_day"}
            or item["trading_day"] is not None
        ):
            raise ValueError("invalid night")
        parsed = date.fromisoformat(item["local_date"])
        if parsed.isoformat() != item["local_date"]:
            raise ValueError("invalid date")
        nights.append(UnresolvedNight(item["holiday"], parsed, item["source_id"], item["raw_text"]))
    candidate = _partial(
        PartialMergeResult(
            "PARTIAL",
            _validate(payload["content"]),
            tuple(nights),
            remaining_checks=tuple(payload["remaining_checks"]),
        )
    )
    if normalize_calendar_candidate(candidate).status != "NORMALIZED":
        raise ValueError("ambiguous candidate")
    page = _page_from_data(payload["hours_page"])
    if payload["hours_page_status"] != ("CAPTURED_UNVERIFIED" if page is not None else "UNKNOWN"):
        raise ValueError("invalid hours status")
    if page is not None:
        hours = payload["source_checks"]["hours"]
        attempts = hours["attempts"]
        if not attempts:
            raise ValueError("captured page lacks source attempt")
        last = attempts[-1]
        if (
            last["transport_status"] != "HTTP_OK"
            or hours["status"] != page.status
            or last["source_status"] != page.status
            or last["parse_status"] != page.status
            or last["product_count"] != len(page.products)
        ):
            raise ValueError("captured page disagrees with source attempt")


def _open_inbox(root, *, create):
    descriptors, chain = _open_directory(root)
    root_fd = descriptors[-1]
    try:
        if create:
            with contextlib.suppress(FileExistsError):
                os.mkdir("inbox", 0o700, dir_fd=root_fd)
        info = os.stat("inbox", dir_fd=root_fd, follow_symlinks=False)
        if not stat.S_ISDIR(info.st_mode):
            raise ValueError("inbox is not a directory")
        fd = os.open("inbox", os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=root_fd)
        descriptors.append(fd)
        identity = _identity(os.fstat(fd))
        if _identity(info) != identity:
            raise ValueError("inbox changed")
        chain.append((root_fd, "inbox", fd, identity))
        return descriptors, chain
    except Exception:
        for fd in reversed(descriptors):
            with contextlib.suppress(OSError):
                os.close(fd)
        raise


def retain_candidate(
    *,
    root,
    candidate_id,
    merged,
    hours_page,
    source_checks,
    filesystem,
    supplement=None,
    catalog=None,
):
    if type(candidate_id) is not str or _ID.fullmatch(candidate_id) is None:
        return CandidateRetention("NOT_COMMITTED", "INVALID_ID", "")
    if filesystem != TRUSTED_LOCAL_POSIX or not _supported():
        return CandidateRetention("NOT_COMMITTED", "UNSUPPORTED_FILESYSTEM", candidate_id)
    try:
        payload = _payload(merged, supplement, hours_page, source_checks, catalog)
        # Validate the detached representation actually written, not caller objects.
        payload = json.loads(_canonical(payload))
        _validate_payload(payload, catalog=catalog is not None)
        body = _canonical(payload)
        envelope = _canonical(
            {
                "schema": _SCHEMA_CATALOG if catalog is not None else _SCHEMA,
                "candidate_id": candidate_id,
                "payload": payload,
                "payload_sha256": hashlib.sha256(body).hexdigest(),
            }
        )
        if catalog is not None and len(envelope) > _LIMIT_CATALOG:
            payload["catalog"] = unknown_catalog(
                "CACHE_RESOURCE_LIMIT", catalog.get("trading_day"), catalog.get("observed_count")
            )
            _validate_payload(payload, catalog=True)
            body = _canonical(payload)
            envelope = _canonical(
                {
                    "schema": _SCHEMA_CATALOG,
                    "candidate_id": candidate_id,
                    "payload": payload,
                    "payload_sha256": hashlib.sha256(body).hexdigest(),
                }
            )
        limit = _LIMIT_CATALOG if catalog is not None else _LIMIT
        if len(envelope) > limit:
            raise ValueError("candidate too large")
    except Exception:
        return CandidateRetention("NOT_COMMITTED", "INVALID_OR_TOO_LARGE", candidate_id)
    try:
        descriptors, chain = _open_inbox(root, create=True)
    except Exception:
        return CandidateRetention("NOT_COMMITTED", "INVALID_DIRECTORY", candidate_id)
    directory = descriptors[-1]
    temp = ""
    name = f"candidate-{candidate_id}.json"
    temp_fd, temp_identity, attempted = None, None, False
    try:
        temp = f".candidate-{candidate_id}-{uuid.uuid4().hex}.tmp"
        if not _directory_matches(chain):
            raise ValueError("directory changed")
        temp_fd = os.open(
            temp, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600, dir_fd=directory
        )
        temp_identity = _identity(os.fstat(temp_fd))
        os.fchmod(temp_fd, 0o600)
        offset = 0
        while offset < len(envelope):
            count = os.write(temp_fd, envelope[offset:])
            if count <= 0:
                raise OSError("short write")
            offset += count
        os.fsync(temp_fd)
        if not _directory_matches(chain):
            raise ValueError("directory changed")
        attempted = True
        try:
            os.link(temp, name, src_dir_fd=directory, dst_dir_fd=directory, follow_symlinks=False)
        except FileExistsError:
            existing = os.stat(name, dir_fd=directory, follow_symlinks=False)
            if _identity(existing) == temp_identity or not _directory_matches(chain):
                raise
            attempted = False
            return CandidateRetention("NOT_COMMITTED", "ID_EXISTS", candidate_id)
        fd = os.open(name, os.O_RDONLY | os.O_NOFOLLOW, dir_fd=directory)
        try:
            if _identity(os.fstat(fd)) != temp_identity:
                raise ValueError("candidate changed")
            data = bytearray()
            while len(data) <= limit:
                part = os.read(fd, limit + 1 - len(data))
                if not part:
                    break
                data.extend(part)
            if bytes(data) != envelope or not _directory_matches(chain):
                raise ValueError("candidate readback mismatch")
        finally:
            os.close(fd)
        os.unlink(temp, dir_fd=directory)
        temp_identity = None
        os.fsync(directory)
        if not _directory_matches(chain):
            raise ValueError("directory changed")
        return CandidateRetention("COMMITTED", "NONE", candidate_id)
    except Exception:
        return CandidateRetention(
            "COMMIT_UNKNOWN" if attempted else "NOT_COMMITTED", "IO_ERROR", candidate_id
        )
    finally:
        if temp_fd is not None:
            with contextlib.suppress(OSError):
                os.close(temp_fd)
        if temp_identity is not None:
            _remove_owned_temp(directory, temp, temp_identity)
        for fd in reversed(descriptors):
            with contextlib.suppress(OSError):
                os.close(fd)


def read_candidate(*, root, candidate_id, filesystem):
    """Return candidate data after structural/digest checks; never authenticate it."""
    if (
        filesystem != TRUSTED_LOCAL_POSIX
        or not _supported()
        or type(candidate_id) is not str
        or _ID.fullmatch(candidate_id) is None
    ):
        raise ValueError("candidate unavailable")
    descriptors, chain = _open_inbox(root, create=False)
    try:
        fd = os.open(
            f"candidate-{candidate_id}.json",
            os.O_RDONLY | os.O_NOFOLLOW | getattr(os, "O_NONBLOCK", 0),
            dir_fd=descriptors[-1],
        )
        try:
            before = os.fstat(fd)
            if (
                not stat.S_ISREG(before.st_mode)
                or before.st_size > _LIMIT_CATALOG
                or before.st_mode & 0o077
            ):
                raise ValueError("candidate invalid")
            if before.st_size > _LIMIT:
                # Version 1 keeps its original 2 MiB read limit. Large version
                # 2 envelopes are canonical and put the schema last; inspect
                # only a bounded tail before allowing the larger read.
                os.lseek(fd, max(0, before.st_size - 128), os.SEEK_SET)
                tail = os.read(fd, 128)
                if not tail.endswith(b'"schema":"bt_api.calendar-preview-candidate/2"}'):
                    raise ValueError("candidate invalid")
                os.lseek(fd, 0, os.SEEK_SET)
            data = bytearray()
            while len(data) <= _LIMIT_CATALOG:
                part = os.read(fd, _LIMIT_CATALOG + 1 - len(data))
                if not part:
                    break
                data.extend(part)
            after = os.fstat(fd)
            final_entry = os.stat(
                f"candidate-{candidate_id}.json", dir_fd=descriptors[-1], follow_symlinks=False
            )
            if (
                (
                    before.st_dev,
                    before.st_ino,
                    before.st_size,
                    before.st_mtime_ns,
                    before.st_ctime_ns,
                )
                != (after.st_dev, after.st_ino, after.st_size, after.st_mtime_ns, after.st_ctime_ns)
                or len(data) != before.st_size
                or _identity(final_entry) != _identity(after)
                or not _directory_matches(chain)
            ):
                raise ValueError("candidate changed")
        finally:
            os.close(fd)

        def pairs(rows):
            result = {}
            for key, value in rows:
                if key in result:
                    raise ValueError("duplicate key")
                result[key] = value
            return result

        envelope = json.loads(data, object_pairs_hook=pairs)
        if (
            type(envelope) is not dict
            or set(envelope) != {"schema", "candidate_id", "payload", "payload_sha256"}
            or envelope["schema"] not in {_SCHEMA, _SCHEMA_CATALOG}
            or (envelope["schema"] == _SCHEMA and len(data) > _LIMIT)
            or envelope["candidate_id"] != candidate_id
            or type(envelope["payload_sha256"]) is not str
            or _SHA.fullmatch(envelope["payload_sha256"]) is None
            or hashlib.sha256(_canonical(envelope["payload"])).hexdigest()
            != envelope["payload_sha256"]
        ):
            raise ValueError("candidate digest mismatch")
        _validate_payload(envelope["payload"], catalog=envelope["schema"] == _SCHEMA_CATALOG)
        return envelope
    finally:
        for fd in reversed(descriptors):
            with contextlib.suppress(OSError):
                os.close(fd)


class _ComparisonUnknownError(ValueError):
    """Internal finite comparison rejection; never expose exception text."""


def _validated_baseline(content):
    """Validate every venue shard and global sources without truncation.

    The CTP supplement parser limits each list to 1000 entries. Split a full
    six-venue snapshot into at most six *complete* venue shards, retaining all
    source declarations in every shard. There is no assertion of official truth.
    """
    from bt_api_ctp.calendar_candidate_normalize import _check_and_order
    from bt_api_ctp.calendar_supplement import _validate

    layers = ("trading_days", "trading_hours", "exceptions")
    if (
        type(content) is not dict
        or set(content) != {*layers, "sources"}
        or any(type(content[name]) is not list for name in (*layers, "sources"))
        or not 1 <= len(content["sources"]) <= _COMPARE_SOURCES
        or len(content["trading_days"]) > len(_VENUES) * _COMPARE_DAYS_PER_VENUE
        or any(len(content[name]) > len(_VENUES) * _COMPARE_ROWS_PER_VENUE for name in layers[1:])
        or len(_canonical(content)) > _COMPARE_BASELINE_BYTES
    ):
        raise _ComparisonUnknownError("unsupported baseline shape or budget")
    shards = {venue: {name: [] for name in layers} for venue in _VENUES}
    nested = 0
    for layer in layers:
        for row in content[layer]:
            if (
                type(row) is not dict
                or type(row.get("venue")) is not str
                or row["venue"] not in _VENUES
            ):
                raise _ComparisonUnknownError("unknown venue")
            rows = shards[row["venue"]][layer]
            rows.append(row)
            limit = _COMPARE_DAYS_PER_VENUE if layer == "trading_days" else _COMPARE_ROWS_PER_VENUE
            if len(rows) > limit:
                raise _ComparisonUnknownError("venue row limit")
            if layer == "trading_hours":
                for key in (
                    "contracts",
                    "day_windows",
                    "night_windows",
                    "night_mappings",
                    "source_ids",
                ):
                    if type(row.get(key)) is not list:
                        raise _ComparisonUnknownError("invalid nested field")
                    nested += len(row[key])
                for mapping in row["night_mappings"]:
                    if type(mapping) is not dict or type(mapping.get("source_ids")) is not list:
                        raise _ComparisonUnknownError("invalid mapping")
                    nested += len(mapping["source_ids"])
            else:
                if type(row.get("source_ids")) is not list:
                    raise _ComparisonUnknownError("invalid source refs")
                nested += len(row["source_ids"])
            if nested > _COMPARE_NESTED_ITEMS:
                raise _ComparisonUnknownError("nested item limit")
    result = {"sources": None, **{name: [] for name in layers}}
    for venue in sorted(_VENUES):
        shard = shards[venue]
        if not any(shard.values()):
            continue
        validated = _validate({"sources": content["sources"], **shard})
        # Preserve all the normalizer's global uniqueness, scope, effective-
        # period, window, and exception checks. Its separate 20k item cap is
        # replaced by the explicit whole-baseline bound above; actual per-
        # contract/day UTC work is checked independently below.
        detached = _check_and_order(deepcopy(validated.content))
        if result["sources"] is None:
            result["sources"] = detached["sources"]
        elif result["sources"] != detached["sources"]:
            raise _ComparisonUnknownError("inconsistent sources")
        for layer in layers:
            result[layer].extend(detached[layer])
    if result["sources"] is None:
        raise _ComparisonUnknownError("empty baseline")
    return result


def _source_relation(candidate_ids, baseline_ids, candidate_sources, baseline_sources):
    if sorted(candidate_ids) != sorted(baseline_ids):
        return "DIFFERENT_UNVERIFIED"
    if any(candidate_sources.get(key) != baseline_sources.get(key) for key in candidate_ids):
        return "DIFFERENT_UNVERIFIED"
    return "SAME_DECLARATION_UNVERIFIED"


def _hours_key(row):
    return (
        row["venue"],
        row["kind"],
        row["product"],
        tuple(sorted(row["contracts"])),
        row["valid_from"],
        row["valid_to"],
    )


def _hours_identity(row):
    return {
        key: row[key] for key in ("venue", "kind", "product", "contracts", "valid_from", "valid_to")
    }


def _check_cross_hours(candidate, baseline):
    by_contract = {}
    for row in baseline:
        for contract in row["contracts"]:
            by_contract.setdefault((row["venue"], contract), []).append(row)
    for row in candidate:
        key = _hours_key(row)
        for contract in row["contracts"]:
            for other in by_contract.get((row["venue"], contract), ()):
                if (
                    row["valid_from"] <= other["valid_to"]
                    and other["valid_from"] <= row["valid_to"]
                    and key != _hours_key(other)
                ):
                    raise _ComparisonUnknownError("overlapping hour scope")


def _exception_scope(row):
    return row["venue"], row["kind"], row["product"], row["trading_day"]


def _exception_key(row):
    return (*_exception_scope(row), row["contract"], row["action"], row["window_id"])


def _utc_window(local_day, window, session):
    start = datetime.combine(local_day, time.fromisoformat(window["start"]), tzinfo=_SHANGHAI)
    end = datetime.combine(
        local_day + timedelta(days=window["end_day_offset"]),
        time.fromisoformat(window["end"]),
        tzinfo=_SHANGHAI,
    )
    return start.astimezone(timezone.utc), end.astimezone(timezone.utc), window["id"], session  # noqa: UP017 - Python 3.9


def _check_explicit_intervals(content):
    """Check only known windows, bounded per exact scope and explicit day.

    Date rows and night mappings that are absent remain unknown. Missing
    exceptions or incomplete declaration flags never prove coverage. Like the
    CTP snapshot, same-day template overlap is rejected before exceptions;
    cross-day comparisons apply the explicitly supplied cancellations.
    """
    days = {}
    day_status = {}
    for row in content["trading_days"]:
        day = date.fromisoformat(row["day"])
        day_status[(row["venue"], day)] = row["status"]
        if row["status"] == "open":
            days.setdefault(row["venue"], []).append(day)
    for items in days.values():
        items.sort()
    exceptions = {}
    for index, row in enumerate(content["exceptions"]):
        key = (row["venue"], row["kind"], row["product"], row["trading_day"], row["contract"])
        exceptions.setdefault(key, []).append((index, row))
        declared = day_status.get((row["venue"], date.fromisoformat(row["trading_day"])))
        if declared == "closed":
            raise _ComparisonUnknownError("exception contradicts a declared closed date")
    windows_by_scope = {}
    scope_days = 0
    window_work = 0
    missing_mapping = 0
    missing_date = 0
    matched_scope_days = [0] * len(content["exceptions"])
    not_seen_scope_days = [0] * len(content["exceptions"])
    for row in content["trading_hours"]:
        start, end = date.fromisoformat(row["valid_from"]), date.fromisoformat(row["valid_to"])
        mappings = {date.fromisoformat(item["trading_day"]): item for item in row["night_mappings"]}
        for day in mappings:
            declared = day_status.get((row["venue"], day))
            if declared == "closed":
                raise _ComparisonUnknownError("night mapping contradicts a declared closed date")
            if declared is None:
                missing_date += len(row["contracts"])
        active_days = [day for day in days.get(row["venue"], ()) if start <= day <= end]
        cells = len(active_days) * len(row["contracts"])
        scope_days += cells
        window_work += cells * (len(row["day_windows"]) + len(row["night_windows"]))
        if scope_days > _COMPARE_SCOPE_DAY_WORK or window_work > _COMPARE_WINDOW_WORK:
            raise _ComparisonUnknownError("explicit window work exceeds budget")
        for day in active_days:
            mapping = mappings.get(day)
            if mapping is None:
                missing_mapping += len(row["contracts"])
            explicit = [_utc_window(day, item, "DAY") for item in row["day_windows"]]
            if mapping is not None and mapping["start_date"] is not None:
                natural_day = date.fromisoformat(mapping["start_date"])
                explicit.extend(
                    _utc_window(natural_day, item, "NIGHT") for item in row["night_windows"]
                )
            explicit.sort()
            if any(
                explicit[index][1] > explicit[index + 1][0] for index in range(len(explicit) - 1)
            ):
                raise _ComparisonUnknownError("overlapping explicit same-day windows")
            for contract in row["contracts"]:
                prefix = (row["venue"], row["kind"], row["product"], day.isoformat())
                applicable = (
                    *exceptions.get((*prefix, None), ()),
                    *exceptions.get((*prefix, contract), ()),
                )
                chosen = list(explicit)
                for index, exception in applicable:
                    matched_scope_days[index] += 1
                    if exception["action"] == "close_day":
                        chosen = []
                    else:
                        before = len(chosen)
                        chosen = [
                            window for window in chosen if window[2] != exception["window_id"]
                        ]
                        if len(chosen) == before:
                            not_seen_scope_days[index] += 1
                scope = (row["venue"], row["kind"], row["product"], contract)
                windows_by_scope.setdefault(scope, []).extend(
                    (window[0], window[1], day, window[2], window[3]) for window in chosen
                )
    for windows in windows_by_scope.values():
        windows.sort()
        if any(windows[index][1] > windows[index + 1][0] for index in range(len(windows) - 1)):
            raise _ComparisonUnknownError("overlapping explicit trading-day windows")
    assessed = sum(count > 0 for count in matched_scope_days)
    exception_assessments = [
        {
            "identity": {
                key: row[key]
                for key in (
                    "venue",
                    "kind",
                    "product",
                    "contract",
                    "trading_day",
                    "action",
                    "window_id",
                )
            },
            "scope_day_match": "MATCHED_AT_LEAST_ONE"
            if matched_scope_days[index]
            else "NOT_ASSESSED",
            "matched_scope_day_count": matched_scope_days[index],
            "not_seen_in_declared_windows_scope_day_count": not_seen_scope_days[index],
            "scope_coverage": "NOT_PROVEN",
            "window_fact": "NOT_PROVEN",
        }
        for index, row in enumerate(content["exceptions"])
    ]
    return {
        "scope_days_checked": scope_days,
        "window_work": window_work,
        "missing_mapping_count": missing_mapping,
        "missing_date_count": missing_date,
        "declared_exception_count": len(content["exceptions"]),
        "assessed_at_least_one_scope_day_count": assessed,
        "unassessed_exception_count": len(content["exceptions"]) - assessed,
        "cancel_window_not_seen_in_declared_windows_scope_day_count": sum(not_seen_scope_days),
        "exception_assessment_semantics": "AT_LEAST_ONE_SCOPE_DAY_ONLY",
        "exception_scope_coverage": "NOT_PROVEN",
        "exception_assessments": exception_assessments,
    }, windows_by_scope


def _check_cross_explicit_intervals(candidate, baseline):
    """Reject overlaps across declared windows except their exact counterparts."""
    work = 0
    for scope in candidate.keys() & baseline.keys():
        candidate_windows, baseline_windows = candidate[scope], baseline[scope]
        index = 0
        for window in candidate_windows:
            while index < len(baseline_windows) and baseline_windows[index][1] <= window[0]:
                index += 1
            other = index
            while other < len(baseline_windows) and baseline_windows[other][0] < window[1]:
                work += 1
                if work > _COMPARE_CROSS_WINDOW_WORK:
                    raise _ComparisonUnknownError("cross-window work exceeds budget")
                match = baseline_windows[other]
                if match[1] > window[0] and window[2:] != match[2:]:
                    raise _ComparisonUnknownError("cross-input explicit windows overlap")
                other += 1
    return work


def _check_cross_exceptions(candidate, baseline):
    by_scope = {}
    keys = {_exception_key(row) for row in baseline}
    for row in baseline:
        by_scope.setdefault(_exception_scope(row), []).append(row)
    for row in candidate:
        if _exception_key(row) in keys:
            continue
        for other in by_scope.get(_exception_scope(row), ()):
            overlaps = (
                row["contract"] == other["contract"]
                or row["contract"] is None
                or other["contract"] is None
            )
            conflicts = (
                row["action"] == "close_day"
                or other["action"] == "close_day"
                or row["window_id"] == other["window_id"]
            )
            if overlaps and conflicts and _exception_key(row) != _exception_key(other):
                raise _ComparisonUnknownError("ambiguous exception scope")


def _window_range(row):
    a, b = row["start"].split(":"), row["end"].split(":")
    return int(a[0]) * 60 + int(a[1]), int(b[0]) * 60 + int(b[1]) + 1440 * row["end_day_offset"]


def _window_observations(candidate, baseline):
    by_id = {} if baseline is None else {row["id"]: row for row in baseline}
    result = []
    for row in candidate:
        other = by_id.get(row["id"])
        if baseline is not None:
            begin, end = _window_range(row)
            if any(
                item["id"] != row["id"]
                and begin < _window_range(item)[1]
                and _window_range(item)[0] < end
                for item in baseline
            ):
                raise _ComparisonUnknownError("ambiguous overlapping windows")
        result.append(
            {
                "id": row["id"],
                "relation": "CANDIDATE_OBSERVATION"
                if other is None
                else "SAME_DECLARATION"
                if row == other
                else "DIFFERENT_DECLARATION",
                "candidate": row,
                "baseline": other,
            }
        )
    return result


def _mapping_observations(candidate, baseline, candidate_sources, baseline_sources):
    by_day = {} if baseline is None else {row["trading_day"]: row for row in baseline}
    result = []
    for row in candidate:
        other = by_day.get(row["trading_day"])
        result.append(
            {
                "trading_day": row["trading_day"],
                "relation": "CANDIDATE_OBSERVATION"
                if other is None
                else "SAME_DECLARATION"
                if row["start_date"] == other["start_date"]
                else "DIFFERENT_DECLARATION",
                "candidate_start_date": row["start_date"],
                "baseline_start_date": None if other is None else other["start_date"],
                "source_relation": "NOT_ASSESSED"
                if other is None
                else _source_relation(
                    row["source_ids"], other["source_ids"], candidate_sources, baseline_sources
                ),
                "interpretation": "DECLARATION_ONLY_NOT_VERIFIED",
            }
        )
    return result


def _compare_content(candidate, baseline):
    candidate_sources = {row["id"]: row["description"] for row in candidate["sources"]}
    baseline_sources = {row["id"]: row["description"] for row in baseline["sources"]}
    day_index = {(row["venue"], row["day"]): row for row in baseline["trading_days"]}
    hours_index = {_hours_key(row): row for row in baseline["trading_hours"]}
    exception_index = {_exception_key(row): row for row in baseline["exceptions"]}
    _check_cross_hours(candidate["trading_hours"], baseline["trading_hours"])
    _check_cross_exceptions(candidate["exceptions"], baseline["exceptions"])
    days = []
    for row in candidate["trading_days"]:
        other = day_index.get((row["venue"], row["day"]))
        days.append(
            {
                "venue": row["venue"],
                "day": row["day"],
                "relation": "CANDIDATE_OBSERVATION"
                if other is None
                else "SAME_DECLARATION"
                if row["status"] == other["status"]
                else "DIFFERENT_DECLARATION",
                "candidate_status": row["status"],
                "baseline_status": None if other is None else other["status"],
                "source_relation": "NOT_ASSESSED"
                if other is None
                else _source_relation(
                    row["source_ids"], other["source_ids"], candidate_sources, baseline_sources
                ),
            }
        )
    hours = []
    for row in candidate["trading_hours"]:
        other = hours_index.get(_hours_key(row))
        hours.append(
            {
                "identity": _hours_identity(row),
                "relation": "CANDIDATE_OBSERVATION" if other is None else "MATCHED_SCOPE",
                "source_relation": "NOT_ASSESSED"
                if other is None
                else _source_relation(
                    row["source_ids"], other["source_ids"], candidate_sources, baseline_sources
                ),
                "candidate_completeness_flags": {
                    "windows_complete": row["windows_complete"],
                    "exceptions_complete": row["exceptions_complete"],
                },
                "baseline_completeness_flags": None
                if other is None
                else {
                    "windows_complete": other["windows_complete"],
                    "exceptions_complete": other["exceptions_complete"],
                },
                "completeness_interpretation": "UNVERIFIED_DECLARATION_ONLY",
                "omitted_windows_and_mappings": "NOT_ASSESSED",
                "day_windows": _window_observations(
                    row["day_windows"], None if other is None else other["day_windows"]
                ),
                "night_windows": _window_observations(
                    row["night_windows"], None if other is None else other["night_windows"]
                ),
                "night_mappings": _mapping_observations(
                    row["night_mappings"],
                    None if other is None else other["night_mappings"],
                    candidate_sources,
                    baseline_sources,
                ),
            }
        )
    exceptions = []
    for row in candidate["exceptions"]:
        other = exception_index.get(_exception_key(row))
        exceptions.append(
            {
                "identity": {
                    key: row[key]
                    for key in (
                        "venue",
                        "kind",
                        "product",
                        "contract",
                        "trading_day",
                        "action",
                        "window_id",
                    )
                },
                "relation": "CANDIDATE_OBSERVATION" if other is None else "SAME_DECLARATION",
                "source_relation": "NOT_ASSESSED"
                if other is None
                else _source_relation(
                    row["source_ids"], other["source_ids"], candidate_sources, baseline_sources
                ),
            }
        )
    sources = []
    for row in candidate["sources"]:
        other = baseline_sources.get(row["id"])
        sources.append(
            {
                "source_id": row["id"],
                "relation": "CANDIDATE_SOURCE_OBSERVATION_UNVERIFIED"
                if other is None
                else "SAME_DECLARATION_UNVERIFIED"
                if other == row["description"]
                else "DIFFERENT_UNVERIFIED",
            }
        )
    return {
        "trading_days": days,
        "trading_hours": hours,
        "exceptions": exceptions,
        "source_clues": sources,
    }


def compare_candidate_with_current(*, root, candidate_id, filesystem):
    """Read-only, bounded-output comparison of explicit unverified declarations.

    This observes one CURRENT with consistent, unauthenticated local receipts,
    not a runtime binding or proof of source authority, durability, completeness or future
    currentness. The store reads its whole event chain before this function can check
    content budgets; total filesystem/time bounds are not established here.
    """
    if type(candidate_id) is not str or _ID.fullmatch(candidate_id) is None:
        return {"status": "UNKNOWN", "reason": "INVALID_CANDIDATE_ID", "candidate_id": ""}
    try:
        candidate = read_candidate(root=root, candidate_id=candidate_id, filesystem=filesystem)
    except Exception:
        return {
            "status": "UNKNOWN",
            "reason": "CANDIDATE_UNAVAILABLE",
            "candidate_id": candidate_id,
        }
    base = {"candidate_id": candidate_id, "candidate_payload_sha256": candidate["payload_sha256"]}
    descriptors = []
    try:
        if filesystem != TRUSTED_LOCAL_POSIX or not _supported():
            raise _ComparisonUnknownError("unsupported filesystem")
        descriptors, chain = _open_directory(root)
        if not _directory_matches(chain):
            raise _ComparisonUnknownError("directory changed")
        from bt_api_py.trading_calendar_store import ReceiptConsistentCurrent, TradingCalendarStore

        observed = TradingCalendarStore(root).read_current_with_consistent_receipts()
        if type(observed) is not ReceiptConsistentCurrent or (
            observed.publication_proof,
            observed.receipt_consistency,
            observed.receipt_authentication,
            observed.durability,
            observed.source_authentication,
            observed.identity_authentication,
            observed.runtime_binding,
        ) != (
            "NOT_PROVEN",
            "LOCAL_RECEIPTS_CONSISTENT_UNAUTHENTICATED",
            "NOT_PROVEN",
            "UNCONFIRMED",
            "NOT_PROVEN",
            "NOT_PROVEN",
            "NOT_PROVEN",
        ):
            raise _ComparisonUnknownError("invalid local observation")
        snapshot = observed.snapshot
        if not _directory_matches(chain):
            raise _ComparisonUnknownError("directory changed")
    except Exception:
        return {"status": "UNKNOWN", "reason": "BASELINE_UNAVAILABLE", **base}
    finally:
        for fd in reversed(descriptors):
            with contextlib.suppress(OSError):
                os.close(fd)
    baseline = {
        "publication_id": snapshot.publication_id,
        "publication_sha256": snapshot.publication_sha256,
        "version_id": snapshot.version_id,
        "content_sha256": snapshot.content_sha256,
        "coverage_status": snapshot.coverage_status,
        "reference_status": "CURRENT_WITH_CONSISTENT_UNAUTHENTICATED_RECEIPTS",
        "currentness": "OBSERVED_UNDER_LOCK_NOT_REVALIDATED",
        "publication_proof": observed.publication_proof,
        "receipt_consistency": observed.receipt_consistency,
        "receipt_authentication": observed.receipt_authentication,
        "durability": observed.durability,
        "source_authentication": observed.source_authentication,
        "identity_authentication": observed.identity_authentication,
        "runtime_binding": observed.runtime_binding,
    }
    if not all(
        type(baseline[key]) is str and _SHA.fullmatch(baseline[key])
        for key in ("publication_sha256", "content_sha256")
    ):
        return {"status": "UNKNOWN", "reason": "BASELINE_UNAVAILABLE", "baseline": baseline, **base}
    try:
        baseline_content = _validated_baseline(snapshot.content)
    except Exception:
        return {
            "status": "UNKNOWN",
            "reason": "BASELINE_UNSUPPORTED_OR_TOO_LARGE",
            "baseline": baseline,
            **base,
        }
    try:
        # Candidate validation already normalizes/validates the full partial
        # structure. Normalizing again fixes all set-like collection ordering.
        from bt_api_ctp.calendar_candidate_normalize import normalize_calendar_candidate
        from bt_api_ctp.calendar_supplement import _validate

        normalized = normalize_calendar_candidate(_validate(candidate["payload"]["content"]))
        if normalized.status != "NORMALIZED" or normalized.json_bytes is None:
            raise _ComparisonUnknownError("candidate invalid")
        candidate_content = json.loads(normalized.json_bytes)
        candidate_windows, candidate_scopes = _check_explicit_intervals(candidate_content)
        baseline_windows, baseline_scopes = _check_explicit_intervals(baseline_content)
        cross_window_work = _check_cross_explicit_intervals(candidate_scopes, baseline_scopes)
        comparison = _compare_content(candidate_content, baseline_content)
        nights = candidate["payload"]["unresolved_nights"]
        result = {
            "status": "PARTIAL",
            "reason": "EXPLICIT_CANDIDATE_DECLARATIONS_ONLY",
            **base,
            "baseline": baseline,
            "baseline_row_counts": {
                key: len(baseline_content[key])
                for key in ("sources", "trading_days", "trading_hours", "exceptions")
            },
            "candidate_row_counts": {
                key: len(candidate_content[key])
                for key in ("sources", "trading_days", "trading_hours", "exceptions")
            },
            "current_only": "NOT_ASSESSED",
            "overall_change": "NOT_ASSESSED",
            "source_authentication": "NOT_PROVEN",
            "candidate_coverage": "INCOMPLETE",
            "publication": "NOT_AUTHORIZED",
            "approval": "NOT_PERFORMED",
            "unresolved_nights": {
                "status": "UNKNOWN" if nights is None else "UNVERIFIED_REVIEW_REQUIRED",
                "items": nights,
            },
            "hours_page_status": candidate["payload"]["hours_page_status"],
            "explicit_window_check": {
                "status": "KNOWN_WINDOWS_ONLY",
                "coverage": "NOT_ASSESSED",
                "missing_mapping": "NOT_ASSESSED",
                "uncovered_dates": "NOT_ASSESSED",
                "source_authentication": "NOT_PROVEN",
                "candidate": candidate_windows,
                "baseline": baseline_windows,
                "scope_day_budget": _COMPARE_SCOPE_DAY_WORK,
                "window_work_budget": _COMPARE_WINDOW_WORK,
                "cross_window_work": cross_window_work,
                "cross_window_work_budget": _COMPARE_CROSS_WINDOW_WORK,
            },
            "comparison": comparison,
        }
        if "catalog" in candidate["payload"]:
            catalog = candidate["payload"]["catalog"]
            result["catalog"] = {
                key: catalog[key]
                for key in (
                    "status",
                    "reason",
                    "trading_day",
                    "observed_count",
                    "skipped_rows",
                    "source_authentication",
                    "retained_catalog_source_binding",
                    "freshness",
                    "absence",
                    "publication",
                )
            }
            result["catalog"]["rows"] = "NOT_INCLUDED_USE_SHOW"
        if len(_canonical(result)) > _COMPARE_OUTPUT_LIMIT:
            return {"status": "UNKNOWN", "reason": "OUTPUT_LIMIT", "baseline": baseline, **base}
        return result
    except Exception:
        return {
            "status": "UNKNOWN",
            "reason": "UNSUPPORTED_OR_AMBIGUOUS",
            "baseline": baseline,
            **base,
        }


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("show", choices=["show"])
    parser.add_argument("--calendar-root", required=True)
    parser.add_argument("--candidate-id", required=True)
    parser.add_argument("--trusted-local-posix", action="store_true", required=True)
    parser.add_argument("--against-current", action="store_true")
    args = parser.parse_args(argv)
    if args.against_current:
        result = compare_candidate_with_current(
            root=args.calendar_root, candidate_id=args.candidate_id, filesystem=TRUSTED_LOCAL_POSIX
        )
        print(json.dumps(result, ensure_ascii=False, sort_keys=True))
        return 0 if result["status"] == "PARTIAL" else 20
    try:
        item = read_candidate(
            root=args.calendar_root, candidate_id=args.candidate_id, filesystem=TRUSTED_LOCAL_POSIX
        )
    except Exception:
        print(
            json.dumps(
                {
                    "status": "UNAVAILABLE",
                    "candidate_id": args.candidate_id if _ID.fullmatch(args.candidate_id) else "",
                }
            )
        )
        return 20
    print(json.dumps(item, ensure_ascii=False, sort_keys=True))
    return 0


if __name__ == "__main__":
    sys.exit(main())
