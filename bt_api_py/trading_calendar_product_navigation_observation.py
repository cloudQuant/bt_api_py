"""Paired product/navigation observations from one bounded local buffer.

The digest and same-buffer marker are unauthenticated declarations, not proof
of retained-data binding, source authority, freshness, coverage, or publication.
"""

import hashlib
import json
import os
from dataclasses import asdict

from bt_api_py.trading_calendar_preview_reports import TRUSTED_LOCAL_POSIX, _supported
from bt_api_py.trading_calendar_product_observation import (
    _MAX_RETAINED_BYTES,
    _canonical,
    _read_page,
    _validate_product,
    unknown_product,
)

_MAX_NAV_BYTES = 1024 * 1024
_REASONS = frozenset(
    {
        "FILE_UNAVAILABLE_OR_CHANGED",
        "FILE_RESOURCE_LIMIT",
        "PARSER_UNAVAILABLE",
        "PARSER_REJECTED",
        "STRUCTURE_CHANGED",
        "INVALID_SOURCE",
        "INVALID_INPUT",
        "INPUT_TOO_LARGE",
        "INVALID_UTF8",
        "SENSITIVE_CONTENT",
        "RESULT_RESOURCE_LIMIT",
    }
)


def unknown_navigation(reason):
    if reason not in _REASONS:
        reason = "PARSER_REJECTED"
    return {
        "status": "UNKNOWN",
        "reason": reason,
        "parser_status": None,
        "source_url": None,
        "sha256": None,
        "links": None,
        "review_reasons": None,
        "verification": "UNVERIFIED",
        "source_authentication": "NOT_PROVEN",
        "venue": "UNKNOWN",
        "product_identity": "UNKNOWN",
        "effective_date": "UNKNOWN",
        "completeness": "NOT_PROVEN",
        "permission": "NOT_PROVEN",
        "freshness": "NOT_PROVEN",
        "retained_data_binding": "NOT_PROVEN",
        "publication": "NOT_AUTHORIZED",
    }


def _contains_sensitive(value, sensitive):
    needles = tuple(item.casefold() for item in sensitive if type(item) is str and item)
    pending = [value]
    while pending:
        item = pending.pop()
        if type(item) is str and any(needle in item.casefold() for needle in needles):
            return True
        if type(item) is dict:
            pending.extend(item.keys())
            pending.extend(item.values())
        elif type(item) is list:
            pending.extend(item)
    return False


def _product(html, source_url, sensitive):
    try:
        from bt_api_ctp.calendar_shfe_product_page import ProductPage, parse_shfe_product_page

        result = parse_shfe_product_page(source_url=source_url, html=html)
        if type(result) is not ProductPage:
            raise ValueError("invalid parser result")
        if result.status not in {"CANDIDATE", "NEEDS_REVIEW"}:
            return unknown_product("PARSER_REJECTED", result.status)
        if result.source_url != source_url or result.sha256 != hashlib.sha256(html).hexdigest():
            raise ValueError("parser observation mismatch")
        data = asdict(result)
        data.update(
            {
                "reason": "UNVERIFIED_PAGE",
                "parser_status": result.status,
                "retained_data_binding": "NOT_PROVEN",
                "publication": "NOT_AUTHORIZED",
            }
        )
        detached = json.loads(_canonical(data))
        if _contains_sensitive(detached, sensitive):
            return unknown_product("SOURCE_PATH_IN_CONTENT")
        if len(_canonical(detached)) > _MAX_RETAINED_BYTES:
            return unknown_product("FILE_RESOURCE_LIMIT")
        _validate_product(detached)
        return detached
    except ImportError:
        return unknown_product("PARSER_UNAVAILABLE")
    except Exception:
        return unknown_product("PARSER_REJECTED")


def _navigation(html, source_url, sensitive):
    try:
        from bt_api_ctp.calendar_shfe_product_navigation import (
            NavigationResult,
            parse_shfe_product_navigation,
        )

        result = parse_shfe_product_navigation(source_url=source_url, html=html)
        if type(result) is not NavigationResult:
            raise ValueError("invalid parser result")
        if result.status not in {"CANDIDATE", "NEEDS_REVIEW"}:
            return unknown_navigation(result.status)
        if result.source_url != source_url or result.sha256 != hashlib.sha256(html).hexdigest():
            raise ValueError("parser observation mismatch")
        data = asdict(result)
        data.update(
            {
                "reason": "UNVERIFIED_NAVIGATION",
                "parser_status": result.status,
                "freshness": "NOT_PROVEN",
                "retained_data_binding": "NOT_PROVEN",
                "publication": "NOT_AUTHORIZED",
            }
        )
        detached = json.loads(_canonical(data))
        if _contains_sensitive(detached, sensitive):
            return unknown_navigation("SENSITIVE_CONTENT")
        if len(_canonical(detached)) > _MAX_NAV_BYTES:
            return unknown_navigation("RESULT_RESOURCE_LIMIT")
        _validate_nav(detached)
        return detached
    except ImportError:
        return unknown_navigation("PARSER_UNAVAILABLE")
    except Exception:
        return unknown_navigation("PARSER_REJECTED")


def _summary(product, nav):
    states = (product["status"], nav["status"])
    if states == ("UNKNOWN", "UNKNOWN"):
        return "UNKNOWN"
    if "UNKNOWN" in states:
        return "PARTIAL"
    if "NEEDS_REVIEW" in states:
        return "NEEDS_REVIEW"
    return "CANDIDATE_UNVERIFIED"


def _wrap(product, nav, digest):
    return {
        "status": _summary(product, nav),
        "same_buffer_observation": "DECLARED_NOT_AUTHENTICATED"
        if digest is not None
        else "NOT_OBSERVED",
        "observed_sha256": digest,
        "navigation": nav,
        "source_authentication": "NOT_PROVEN",
        "freshness": "NOT_PROVEN",
        "coverage": "NOT_PROVEN",
        "retained_data_binding": "NOT_PROVEN",
        "publication": "NOT_AUTHORIZED",
    }


def unknown_pair(reason):
    product_reason = (
        reason
        if reason in {"FILE_RESOURCE_LIMIT", "FILE_UNAVAILABLE_OR_CHANGED"}
        else "PARSER_REJECTED"
    )
    product = unknown_product(product_reason)
    return product, _wrap(product, unknown_navigation(reason), None)


def observe_product_and_navigation(*, source_url, path, filesystem, additional_sensitive=()):
    """Read once and pass the same immutable bytes to both parsers."""
    if filesystem != TRUSTED_LOCAL_POSIX or not _supported():
        return unknown_pair("FILE_UNAVAILABLE_OR_CHANGED")
    try:
        html = _read_page(path)
    except OverflowError:
        return unknown_pair("FILE_RESOURCE_LIMIT")
    except (OSError, ValueError, TypeError):
        return unknown_pair("FILE_UNAVAILABLE_OR_CHANGED")
    sensitive = (path, os.path.normpath(path), *additional_sensitive)
    digest = hashlib.sha256(html).hexdigest()
    product = _product(html, source_url, sensitive)
    nav = _navigation(html, source_url, sensitive)
    wrapper = _wrap(product, nav, digest)
    try:
        validate_navigation_observation(wrapper, product)
    except Exception:
        return unknown_pair("PARSER_REJECTED")
    return product, wrapper


def _validate_nav(nav):
    fields = {
        "status",
        "reason",
        "parser_status",
        "source_url",
        "sha256",
        "links",
        "review_reasons",
        "verification",
        "source_authentication",
        "venue",
        "product_identity",
        "effective_date",
        "completeness",
        "permission",
        "freshness",
        "retained_data_binding",
        "publication",
    }
    if type(nav) is not dict or set(nav) != fields:
        raise ValueError("invalid navigation")
    if tuple(
        nav[k]
        for k in (
            "verification",
            "source_authentication",
            "venue",
            "product_identity",
            "effective_date",
            "completeness",
            "permission",
            "freshness",
            "retained_data_binding",
            "publication",
        )
    ) != (
        "UNVERIFIED",
        "NOT_PROVEN",
        "UNKNOWN",
        "UNKNOWN",
        "UNKNOWN",
        "NOT_PROVEN",
        "NOT_PROVEN",
        "NOT_PROVEN",
        "NOT_PROVEN",
        "NOT_AUTHORIZED",
    ):
        raise ValueError("invalid navigation qualification")
    if nav["status"] == "UNKNOWN":
        if nav["reason"] not in _REASONS or any(
            nav[k] is not None
            for k in ("parser_status", "source_url", "sha256", "links", "review_reasons")
        ):
            raise ValueError("invalid unknown navigation")
        return
    from bt_api_ctp.calendar_shfe_product_navigation import _SOURCES, _path_kind

    if (
        nav["status"] not in {"CANDIDATE", "NEEDS_REVIEW"}
        or nav["reason"] != "UNVERIFIED_NAVIGATION"
        or nav["parser_status"] != nav["status"]
        or nav["source_url"] not in _SOURCES
        or type(nav["sha256"]) is not str
        or len(nav["sha256"]) != 64
        or any(c not in "0123456789abcdef" for c in nav["sha256"])
        or type(nav["links"]) is not list
        or not 1 <= len(nav["links"]) <= 512
        or len(_canonical(nav)) > _MAX_NAV_BYTES
    ):
        raise ValueError("invalid navigation data")
    seen, paths, kinds = set(), {}, set()
    conflict = False
    for row in nav["links"]:
        if type(row) is not dict or set(row) != {"kind", "label", "path"}:
            raise ValueError("invalid link")
        label, path, kind = row["label"], row["path"], row["kind"]
        if (
            type(label) is not str
            or not label
            or not label.strip()
            or len(label) > 128
            or any(
                ord(c) < 32 or 0x7F <= ord(c) <= 0x9F or 0xD800 <= ord(c) <= 0xDFFF for c in label
            )
            or kind not in {"future", "option"}
            or _path_kind(path) != kind
        ):
            raise ValueError("invalid link content")
        key = (kind, label, path)
        if key in seen:
            raise ValueError("duplicate link")
        seen.add(key)
        kinds.add(kind)
        if path in paths and paths[path] != (kind, label):
            conflict = True
        paths[path] = (kind, label)
    if kinds != {"future", "option"} or (nav["status"], nav["review_reasons"]) != (
        ("NEEDS_REVIEW", ["DUPLICATE_PATH_CONFLICT"]) if conflict else ("CANDIDATE", [])
    ):
        raise ValueError("navigation classification mismatch")


def validate_navigation_observation(wrapper, product):
    fields = {
        "status",
        "same_buffer_observation",
        "observed_sha256",
        "navigation",
        "source_authentication",
        "freshness",
        "coverage",
        "retained_data_binding",
        "publication",
    }
    if type(wrapper) is not dict or set(wrapper) != fields:
        raise ValueError("invalid observation")
    if tuple(
        wrapper[k]
        for k in (
            "source_authentication",
            "freshness",
            "coverage",
            "retained_data_binding",
            "publication",
        )
    ) != ("NOT_PROVEN", "NOT_PROVEN", "NOT_PROVEN", "NOT_PROVEN", "NOT_AUTHORIZED"):
        raise ValueError("invalid observation qualification")
    _validate_product(product)
    nav = wrapper["navigation"]
    _validate_nav(nav)
    if wrapper["status"] != _summary(product, nav):
        raise ValueError("invalid observation status")
    digest = wrapper["observed_sha256"]
    if digest is None:
        if (
            wrapper["same_buffer_observation"] != "NOT_OBSERVED"
            or product["status"] != "UNKNOWN"
            or nav["status"] != "UNKNOWN"
        ):
            raise ValueError("invalid missing observation")
    elif (
        type(digest) is not str
        or len(digest) != 64
        or any(c not in "0123456789abcdef" for c in digest)
        or wrapper["same_buffer_observation"] != "DECLARED_NOT_AUTHENTICATED"
    ):
        raise ValueError("invalid digest")
    if product["status"] != "UNKNOWN" and product["sha256"] != digest:
        raise ValueError("product digest mismatch")
    if nav["status"] != "UNKNOWN" and nav["sha256"] != digest:
        raise ValueError("navigation digest mismatch")
    if (
        product["status"] != "UNKNOWN"
        and nav["status"] != "UNKNOWN"
        and product["source_url"] != nav["source_url"]
    ):
        raise ValueError("source mismatch")
