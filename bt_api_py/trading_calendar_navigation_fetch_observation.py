"""Unverified fixed-source network navigation; never a same-buffer product claim."""

import contextlib
import json
from dataclasses import asdict

from bt_api_py.trading_calendar_product_navigation_observation import (
    _MAX_NAV_BYTES,
    _contains_sensitive,
    _validate_nav,
)
from bt_api_py.trading_calendar_product_observation import _canonical

_SOURCE = "https://www.shfe.com.cn/products/futures/metal/nonferrousmetal/cu_f/"
_FAILURES = frozenset(
    {
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
    }
)


def unknown_fetch(reason, transport="NOT_ATTEMPTED", parse="NOT_OBSERVED"):
    return {
        "status": "UNKNOWN",
        "reason": reason,
        "transport_status": transport,
        "parse_status": parse,
        "navigation": None,
        "source_url": _SOURCE,
        "transport_policy": "SUPERVISED_DEFAULT_TLS_POLICY",
        "verification": "UNVERIFIED",
        "source_authentication": "NOT_PROVEN",
        "freshness": "NOT_PROVEN",
        "coverage": "NOT_PROVEN",
        "retained_data_binding": "NOT_PROVEN",
        "permission": "NOT_PROVEN",
        "publication": "NOT_AUTHORIZED",
    }


def fetch_navigation(*, total_timeout, sensitive=()):
    """Fetch only the fixed source; caller controls the absolute shared deadline."""
    if total_timeout <= 0:
        return unknown_fetch("BUDGET_EXHAUSTED")
    try:
        from bt_api_ctp.calendar_shfe_navigation_fetch import (
            NavigationFetchResult,
            NavigationLink,
            NavigationResult,
            _decode,
            _encode,
            collect_navigation,
        )
    except ImportError:
        return unknown_fetch("COLLECTOR_UNAVAILABLE")
    try:
        result = collect_navigation(
            connect_timeout=min(3.0, total_timeout),
            read_timeout=min(3.0, total_timeout),
            total_timeout=total_timeout,
        )
        if type(result) is not NavigationFetchResult or (
            result.transport_assurance,
            result.verification,
            result.source_authentication,
            result.venue,
            result.product_identity,
            result.effective_date,
            result.completeness,
            result.permission,
            result.publication,
        ) != (
            "SUPERVISED_DEFAULT_TLS_POLICY",
            "UNVERIFIED",
            "NOT_PROVEN",
            "UNKNOWN",
            "UNKNOWN",
            "UNKNOWN",
            "NOT_PROVEN",
            "NOT_PROVEN",
            "NOT_AUTHORIZED",
        ):
            raise ValueError("invalid collector result")
        if type(result.transport_status) is not str or len(result.transport_status) > 64:
            raise ValueError("invalid transport")
        page = result.navigation
        if page is not None:
            if (
                type(page) is not NavigationResult
                or page.source_url != _SOURCE
                or type(page.sha256) is not str
                or len(page.sha256) != 64
                or page.status
                not in {"CANDIDATE", "NEEDS_REVIEW", "STRUCTURE_CHANGED", "INVALID_UTF8"}
                or (
                    page.verification,
                    page.source_authentication,
                    page.venue,
                    page.product_identity,
                    page.effective_date,
                    page.completeness,
                    page.permission,
                )
                != (
                    "UNVERIFIED",
                    "NOT_PROVEN",
                    "UNKNOWN",
                    "UNKNOWN",
                    "UNKNOWN",
                    "NOT_PROVEN",
                    "NOT_PROVEN",
                )
                or type(page.links) is not tuple
                or len(page.links) > 512
            ):
                raise ValueError("invalid navigation size")
            for item in page.links:
                if (
                    type(item) is not NavigationLink
                    or type(item.kind) is not str
                    or len(item.kind) > 16
                    or type(item.label) is not str
                    or len(item.label) > 128
                    or type(item.path) is not str
                    or len(item.path) > 512
                ):
                    raise ValueError("invalid navigation link size")
            if (
                type(page.review_reasons) is not tuple
                or len(page.review_reasons) > 1
                or any(
                    type(reason) is not str or len(reason) > 64 for reason in page.review_reasons
                )
            ):
                raise ValueError("invalid navigation reasons")
        transport, navigation = _decode(_encode(result.transport_status, result.navigation))
        if transport != result.transport_status or navigation != result.navigation:
            raise ValueError("invalid protocol")
        if transport != "HTTP_OK":
            if (result.status, result.parse_status, navigation) != (
                "UNKNOWN",
                "NOT_OBSERVED",
                None,
            ):
                raise ValueError("invalid transport result")
            reason = (
                transport
                if transport in {"CHILD_CLEANUP_UNCERTAIN", "SUPERVISOR_UNSUPPORTED"}
                else "FETCH_FAILED"
            )
            value = unknown_fetch(reason, transport)
        elif result.parse_status in {"INVALID_UTF8", "STRUCTURE_CHANGED"}:
            if (
                result.status != "UNKNOWN"
                or navigation is None
                or navigation.status != result.parse_status
            ):
                raise ValueError("invalid parser result")
            value = unknown_fetch("PARSE_FAILED", "HTTP_OK", result.parse_status)
        else:
            if navigation is None or (result.status, result.parse_status) != (
                navigation.status,
                navigation.status,
            ):
                raise ValueError("invalid observation")
            data = asdict(navigation)
            data.update(
                reason="UNVERIFIED_NAVIGATION",
                parser_status=navigation.status,
                freshness="NOT_PROVEN",
                retained_data_binding="NOT_PROVEN",
                publication="NOT_AUTHORIZED",
            )
            data = json.loads(_canonical(data))
            if _contains_sensitive(data, sensitive):
                value = unknown_fetch("SENSITIVE_CONTENT", "HTTP_OK")
            elif len(_canonical(data)) > _MAX_NAV_BYTES:
                value = unknown_fetch("RESULT_RESOURCE_LIMIT", "HTTP_OK")
            else:
                _validate_nav(data)
                value = unknown_fetch("UNVERIFIED_NAVIGATION", "HTTP_OK", navigation.status)
                value.update(status=navigation.status, navigation=data)
        validate_fetch(value)
        return value
    except Exception:
        # The collector or its validation failed after invocation; cleanup is unknown.
        return unknown_fetch("CHILD_CLEANUP_UNCERTAIN", "CHILD_CLEANUP_UNCERTAIN")
    except BaseException as exc:
        with contextlib.suppress(Exception):
            exc.child_cleanup = "CHILD_CLEANUP_UNCERTAIN"
            if hasattr(exc, "add_note"):
                exc.add_note("CHILD_CLEANUP_UNCERTAIN")
        raise


def validate_fetch(value):
    template = unknown_fetch("BUDGET_EXHAUSTED")
    if type(value) is not dict or set(value) != set(template):
        raise ValueError("invalid navigation fetch")
    for key in (
        "source_url",
        "transport_policy",
        "verification",
        "source_authentication",
        "freshness",
        "coverage",
        "retained_data_binding",
        "permission",
        "publication",
    ):
        if value[key] != template[key]:
            raise ValueError("invalid qualification")
    status, reason, transport, parse, nav = (
        value[k] for k in ("status", "reason", "transport_status", "parse_status", "navigation")
    )
    if reason in {"BUDGET_EXHAUSTED", "COLLECTOR_UNAVAILABLE"}:
        expected = ("UNKNOWN", "NOT_ATTEMPTED", "NOT_OBSERVED", None)
    elif reason in {"CHILD_CLEANUP_UNCERTAIN", "SUPERVISOR_UNSUPPORTED"}:
        expected = ("UNKNOWN", reason, "NOT_OBSERVED", None)
    elif reason == "FETCH_FAILED":
        if transport not in _FAILURES:
            raise ValueError("invalid transport")
        expected = ("UNKNOWN", transport, "NOT_OBSERVED", None)
    elif reason == "PARSE_FAILED":
        if parse not in {"INVALID_UTF8", "STRUCTURE_CHANGED"}:
            raise ValueError("invalid parse")
        expected = ("UNKNOWN", "HTTP_OK", parse, None)
    elif reason in {"SENSITIVE_CONTENT", "RESULT_RESOURCE_LIMIT"}:
        expected = ("UNKNOWN", "HTTP_OK", "NOT_OBSERVED", None)
    elif reason == "UNVERIFIED_NAVIGATION":
        _validate_nav(nav)
        if nav["source_url"] != _SOURCE or nav["status"] not in {"CANDIDATE", "NEEDS_REVIEW"}:
            raise ValueError("invalid source")
        expected = (nav["status"], "HTTP_OK", nav["status"], nav)
    else:
        raise ValueError("invalid reason")
    if (status, transport, parse, nav) != expected:
        raise ValueError("inconsistent navigation fetch")
