"""Unverified fixed-source product observations for candidate retention."""

import contextlib
import json
from dataclasses import asdict

from bt_api_py.trading_calendar_product_observation import _canonical, _validate_product

_EXPECTED = {
    "CU_FUTURE": (
        "future",
        "SHFE",
        "CU",
        "https://www.shfe.com.cn/products/futures/metal/nonferrousmetal/cu_f/",
    ),
    "CU_OPTION": (
        "option",
        "SHFE",
        "CU",
        "https://www.shfe.com.cn/products/option/nonferrousmetal/cu_o/",
    ),
    "BC_OPTION": (
        "option",
        "INE",
        "BC",
        "https://www.shfe.com.cn/products/option/nonferrousmetal/bc_o/",
    ),
}
_FAILURES = {
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
}
_LOCAL_FAILURES = {"BUDGET_EXHAUSTED", "COLLECTOR_UNAVAILABLE", "COLLECTOR_ERROR"}


def unknown_fetch(key, reason, transport="NOT_ATTEMPTED"):
    return {
        "source_key": key,
        "status": "UNKNOWN",
        "reason": reason,
        "transport_status": transport,
        "parse_status": "NOT_OBSERVED",
        "identity_status": "NOT_ASSESSED",
        "transport_policy": "SUPERVISED_DEFAULT_TLS_POLICY",
        "page": None,
        "verification": "UNVERIFIED",
        "source_authentication": "NOT_PROVEN",
        "retained_data_binding": "NOT_PROVEN",
        "coverage": "UNKNOWN",
        "current_record": "UNKNOWN",
        "publication": "NOT_AUTHORIZED",
    }


def _identity(key, page):
    expected = _EXPECTED[key][:3]
    values = [(row["kind"], row["venue"], row["symbol"]) for row in page["records"]]
    if any(any(row[i] is not None and row[i] != expected[i] for i in range(3)) for row in values):
        return "SOURCE_FIELDS_MISMATCH"
    if any(any(value is None for value in row) for row in values):
        return "SOURCE_FIELDS_INCOMPLETE"
    return "SOURCE_FIELDS_MATCH_UNVERIFIED"


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


def fetch_observation(key, *, total_timeout, sensitive=()):
    """Collect one named source; no scope, path or authentication is inferred."""
    if key not in _EXPECTED:
        raise ValueError("unknown source")
    if total_timeout <= 0:
        return unknown_fetch(key, "BUDGET_EXHAUSTED")
    try:
        from bt_api_ctp.calendar_shfe_product_fetch import ProductFetchResult, collect_product_page
    except ImportError:
        return unknown_fetch(key, "COLLECTOR_UNAVAILABLE")
    try:
        result = collect_product_page(
            source_key=key,
            connect_timeout=min(3.0, total_timeout),
            read_timeout=min(3.0, total_timeout),
            total_timeout=total_timeout,
        )
        if (
            type(result) is not ProductFetchResult
            or result.transport_assurance != "SUPERVISED_DEFAULT_TLS_POLICY"
        ):
            raise ValueError("invalid collector result")
        if (
            result.source,
            result.verification,
            result.source_authentication,
            result.coverage,
            result.current_record,
            result.publication,
        ) != (
            _EXPECTED[key][3],
            "UNVERIFIED",
            "NOT_PROVEN",
            "UNKNOWN",
            "UNKNOWN",
            "NOT_AUTHORIZED",
        ):
            raise ValueError("invalid collector qualification")
        if result.transport_status != "HTTP_OK":
            if (result.status, result.parse_status, result.identity_status, result.page) != (
                "UNKNOWN",
                "NOT_OBSERVED",
                "NOT_ASSESSED",
                None,
            ):
                raise ValueError("invalid transport result")
            value = unknown_fetch(
                key,
                "CHILD_CLEANUP_UNCERTAIN"
                if result.transport_status == "CHILD_CLEANUP_UNCERTAIN"
                else "SUPERVISOR_UNSUPPORTED"
                if result.transport_status == "SUPERVISOR_UNSUPPORTED"
                else "FETCH_FAILED",
                result.transport_status,
            )
        elif result.parse_status in {"STRUCTURE_CHANGED", "INVALID_UTF8"}:
            if (
                result.status != "UNKNOWN"
                or result.identity_status != "NOT_ASSESSED"
                or result.page is None
                or result.page.status != result.parse_status
            ):
                raise ValueError("invalid parser result")
            value = unknown_fetch(key, "PARSE_FAILED", "HTTP_OK")
            value["parse_status"] = result.parse_status
        else:
            page = asdict(result.page)
            page.update(
                {
                    "reason": "UNVERIFIED_PAGE",
                    "parser_status": page["status"],
                    "retained_data_binding": "NOT_PROVEN",
                    "publication": "NOT_AUTHORIZED",
                }
            )
            page = json.loads(_canonical(page))
            if _contains_sensitive(page, sensitive):
                value = unknown_fetch(key, "SENSITIVE_CONTENT", "HTTP_OK")
            else:
                _validate_product(page)
                identity = _identity(key, page)
                value = unknown_fetch(key, "UNVERIFIED_PAGE", "HTTP_OK")
                value["page"] = page
                value["parse_status"] = page["status"]
                value["identity_status"] = identity
                if identity == "SOURCE_FIELDS_MATCH_UNVERIFIED":
                    value["status"] = page["status"]
                elif identity == "SOURCE_FIELDS_MISMATCH":
                    value["reason"] = "IDENTITY_MISMATCH"
                else:
                    value["reason"] = "IDENTITY_INCOMPLETE"
                if (result.status, result.identity_status, result.parse_status, result.source) != (
                    value["status"],
                    identity,
                    page["status"],
                    _EXPECTED[key][3],
                ):
                    raise ValueError("collector classification mismatch")
        validate_fetch(value)
        return value
    except Exception:
        # A call or validation failure does not prove that its child exited.
        return unknown_fetch(key, "CHILD_CLEANUP_UNCERTAIN", "CHILD_CLEANUP_UNCERTAIN")
    except BaseException as exc:
        with contextlib.suppress(Exception):
            exc.child_cleanup = "CHILD_CLEANUP_UNCERTAIN"
            if hasattr(exc, "add_note"):
                exc.add_note("CHILD_CLEANUP_UNCERTAIN")
        raise


def validate_fetch(value):
    required = {
        "source_key",
        "status",
        "reason",
        "transport_status",
        "parse_status",
        "identity_status",
        "transport_policy",
        "page",
        "verification",
        "source_authentication",
        "retained_data_binding",
        "coverage",
        "current_record",
        "publication",
    }
    if type(value) is not dict or set(value) != required or value["source_key"] not in _EXPECTED:
        raise ValueError("invalid fetch")
    if tuple(
        value[k]
        for k in (
            "transport_policy",
            "verification",
            "source_authentication",
            "retained_data_binding",
            "coverage",
            "current_record",
            "publication",
        )
    ) != (
        "SUPERVISED_DEFAULT_TLS_POLICY",
        "UNVERIFIED",
        "NOT_PROVEN",
        "NOT_PROVEN",
        "UNKNOWN",
        "UNKNOWN",
        "NOT_AUTHORIZED",
    ):
        raise ValueError("invalid qualification")
    reason, transport, parse, identity, page = (
        value[k] for k in ("reason", "transport_status", "parse_status", "identity_status", "page")
    )
    if reason in _LOCAL_FAILURES:
        expected = ("UNKNOWN", "NOT_ATTEMPTED", "NOT_OBSERVED", "NOT_ASSESSED", None)
    elif reason == "SUPERVISOR_UNSUPPORTED":
        expected = ("UNKNOWN", "SUPERVISOR_UNSUPPORTED", "NOT_OBSERVED", "NOT_ASSESSED", None)
    elif reason in {"FETCH_FAILED", "CHILD_CLEANUP_UNCERTAIN"}:
        if transport not in _FAILURES or (reason == "CHILD_CLEANUP_UNCERTAIN") != (
            transport == "CHILD_CLEANUP_UNCERTAIN"
        ):
            raise ValueError("invalid transport")
        expected = ("UNKNOWN", transport, "NOT_OBSERVED", "NOT_ASSESSED", None)
    elif reason == "PARSE_FAILED":
        if parse not in {"STRUCTURE_CHANGED", "INVALID_UTF8"}:
            raise ValueError("invalid parse")
        expected = ("UNKNOWN", "HTTP_OK", parse, "NOT_ASSESSED", None)
    elif reason in {"SENSITIVE_CONTENT", "RESULT_RESOURCE_LIMIT"}:
        expected = ("UNKNOWN", "HTTP_OK", "NOT_OBSERVED", "NOT_ASSESSED", None)
    elif reason in {"UNVERIFIED_PAGE", "IDENTITY_MISMATCH", "IDENTITY_INCOMPLETE"}:
        _validate_product(page)
        if page["source_url"] != _EXPECTED[value["source_key"]][3]:
            raise ValueError("source mismatch")
        actual_identity = _identity(value["source_key"], page)
        expected_reason = {
            "SOURCE_FIELDS_MATCH_UNVERIFIED": "UNVERIFIED_PAGE",
            "SOURCE_FIELDS_MISMATCH": "IDENTITY_MISMATCH",
            "SOURCE_FIELDS_INCOMPLETE": "IDENTITY_INCOMPLETE",
        }[actual_identity]
        if reason != expected_reason:
            raise ValueError("identity mismatch")
        expected = (
            page["status"] if reason == "UNVERIFIED_PAGE" else "UNKNOWN",
            "HTTP_OK",
            page["status"],
            actual_identity,
            page,
        )
    else:
        raise ValueError("invalid reason")
    if (value["status"], transport, parse, identity, page) != expected:
        raise ValueError("inconsistent fetch")
