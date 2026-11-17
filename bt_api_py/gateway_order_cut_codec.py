"""Bounded wire validation for local observed-order cut pages.

A valid projection is not proof of venue completeness or trading permission.
"""

from __future__ import annotations

import hashlib
import json
import re
from datetime import datetime

from bt_api_base.gateway_execution_stream_protocol import GatewayExecutionObservation

CURRENT = "account.order_cut.current.v1"
QUERY_PAGE = "account.order_cut.query_page.v1"
RECEIPT_PAGE = "account.order_cut.receipt_page.v1"
OPERATIONS = frozenset({CURRENT, QUERY_PAGE, RECEIPT_PAGE})
MAX_BYTES = 1024 * 1024
_HASH = re.compile(r"[0-9a-f]{64}\Z")
_HEADER = {
    "journal_id",
    "owner_generation",
    "fact_epoch",
    "fact_watermark",
    "source_binding_ref",
    "current_session_trading_day",
    "base_sequence",
    "begin_sequence",
    "end_sequence",
    "source_prefix",
    "query_digest",
    "query_scope",
    "query_row_count",
    "cut_record_digest",
    "query_fact_digests_digest",
    "source_history_has_gap",
}


def _ref(value):
    return (
        type(value) is str
        and bool(value)
        and value == value.strip()
        and len(value.encode("utf-8")) <= 256
    )


def _hash(value):
    return type(value) is str and _HASH.fullmatch(value) is not None


def _number(value):
    return type(value) is int and 0 <= value < 2**63


def canonical(value):
    raw = json.dumps(
        value, sort_keys=True, separators=(",", ":"), ensure_ascii=True, allow_nan=False
    ).encode("ascii")
    if len(raw) > MAX_BYTES:
        raise ValueError("ORDER_CUT_TOO_LARGE")
    return raw


def decode_query(operation, value):
    if type(value) is not dict:
        raise ValueError("INVALID_ORDER_CUT_REQUEST")
    if operation == CURRENT:
        if value:
            raise ValueError("INVALID_ORDER_CUT_REQUEST")
        return {}
    cursor = "after_ordinal" if operation == QUERY_PAGE else "after_source_sequence"
    if (
        operation not in OPERATIONS
        or set(value) != {"cut_ref", cursor, "limit", "expected_manifest_digest"}
        or not _hash(value["cut_ref"])
        or not _number(value[cursor])
        or type(value["limit"]) is not int
        or not 1 <= value["limit"] <= 256
        or (
            value["expected_manifest_digest"] is not None
            and not _hash(value["expected_manifest_digest"])
        )
    ):
        raise ValueError("INVALID_ORDER_CUT_REQUEST")
    return dict(value)


def validate_response(operation, value, *, arguments, account_ref, registration_id, service_epoch):
    """Return canonical detached bytes after strict validation of the entire response."""
    args = decode_query(operation, arguments)
    if type(value) is not dict or value.get("status") != "OBSERVED":
        raise ValueError("INVALID_ORDER_CUT_RESPONSE")
    schema = {
        CURRENT: "sdk.gateway.order_cut.current/1",
        QUERY_PAGE: "sdk.gateway.order_cut.query_page/1",
        RECEIPT_PAGE: "sdk.gateway.order_cut.receipt_page/1",
    }[operation]
    keys = {
        "status",
        "schema",
        "registration_id",
        "service_epoch",
        "cut_ref",
        "header",
        "query_manifest_digest",
    }
    if operation != CURRENT:
        keys |= {"manifest_digest", "items", "has_more"}
        keys |= (
            {"after_ordinal", "next_ordinal"}
            if operation == QUERY_PAGE
            else {"after_source_sequence", "next_source_sequence"}
        )
    if (
        set(value) != keys
        or value["schema"] != schema
        or value["registration_id"] != registration_id
        or value["service_epoch"] != service_epoch
        or not _hash(value["cut_ref"])
        or not _hash(value["query_manifest_digest"])
        or (operation != CURRENT and value["cut_ref"] != args["cut_ref"])
    ):
        raise ValueError("INVALID_ORDER_CUT_RESPONSE")
    h = value["header"]
    if (
        type(h) is not dict
        or set(h) != _HEADER
        or not _ref(h["journal_id"])
        or not _ref(h["owner_generation"])
        or any(
            not _hash(h[k])
            for k in (
                "fact_epoch",
                "source_binding_ref",
                "query_digest",
                "cut_record_digest",
                "query_fact_digests_digest",
            )
        )
        or type(h["current_session_trading_day"]) is not str
        or re.fullmatch(r"\d{8}", h["current_session_trading_day"]) is None
        or h["query_scope"] != "ctp.futures.account_orders.v1"
        or any(
            not _number(h[k])
            for k in (
                "fact_watermark",
                "base_sequence",
                "begin_sequence",
                "end_sequence",
                "source_prefix",
                "query_row_count",
            )
        )
        or h["base_sequence"] > h["begin_sequence"]
        or h["begin_sequence"] > h["end_sequence"]
        or h["source_prefix"] < h["end_sequence"]
        or h["query_row_count"] > 10000
        or type(h["source_history_has_gap"]) is not bool
    ):
        raise ValueError("INVALID_ORDER_CUT_RESPONSE")
    try:
        datetime.strptime(h["current_session_trading_day"], "%Y%m%d")
    except ValueError:
        raise ValueError("INVALID_ORDER_CUT_RESPONSE") from None
    expected_ref = hashlib.sha256(
        json.dumps(
            ["sdk.gateway.order_cut.ref/1", h, value["query_manifest_digest"]],
            sort_keys=True,
            separators=(",", ":"),
            ensure_ascii=True,
        ).encode("ascii")
    ).hexdigest()
    if value["cut_ref"] != expected_ref:
        raise ValueError("INVALID_ORDER_CUT_RESPONSE")
    if operation != CURRENT:
        if (
            not _hash(value["manifest_digest"])
            or type(value["items"]) is not list
            or len(value["items"]) > args["limit"]
            or type(value["has_more"]) is not bool
            or (
                args["expected_manifest_digest"] is not None
                and value["manifest_digest"] != args["expected_manifest_digest"]
            )
        ):
            raise ValueError("INVALID_ORDER_CUT_RESPONSE")
        if operation == QUERY_PAGE:
            if (
                not _number(value["after_ordinal"])
                or not _number(value["next_ordinal"])
                or value["manifest_digest"] != value["query_manifest_digest"]
                or value["after_ordinal"] != args["after_ordinal"]
                or value["next_ordinal"] != args["after_ordinal"] + len(value["items"])
                or value["next_ordinal"] > h["query_row_count"]
                or value["has_more"] != (value["next_ordinal"] < h["query_row_count"])
            ):
                raise ValueError("INVALID_ORDER_CUT_RESPONSE")
        else:
            if (
                not _number(value["after_source_sequence"])
                or not _number(value["next_source_sequence"])
                or value["after_source_sequence"] != args["after_source_sequence"]
                or value["next_source_sequence"]
                != args["after_source_sequence"] + len(value["items"])
                or not h["begin_sequence"]
                <= value["after_source_sequence"]
                <= value["next_source_sequence"]
                <= h["end_sequence"]
                or value["has_more"] != (value["next_source_sequence"] < h["end_sequence"])
            ):
                raise ValueError("INVALID_ORDER_CUT_RESPONSE")
        if not value["items"] and value["has_more"]:
            raise ValueError("INVALID_ORDER_CUT_RESPONSE")
        for i, item in enumerate(value["items"], 1):
            expected = (
                {"ordinal", "fact_digest", "fact"}
                if operation == QUERY_PAGE
                else {
                    "source_sequence",
                    "source_record_digest",
                    "fact_digest",
                    "fact_sequence",
                    "disposition",
                    "fact",
                }
            )
            if type(item) is not dict or set(item) != expected or not _hash(item["fact_digest"]):
                raise ValueError("INVALID_ORDER_CUT_RESPONSE")
            if operation == QUERY_PAGE:
                if not _number(item["ordinal"]) or item["ordinal"] != args["after_ordinal"] + i:
                    raise ValueError("INVALID_ORDER_CUT_RESPONSE")
            elif (
                not _number(item["source_sequence"])
                or item["source_sequence"] != args["after_source_sequence"] + i
                or not _hash(item["source_record_digest"])
                or not _number(item["fact_sequence"])
                or not 0 < item["fact_sequence"] <= h["fact_watermark"]
                or item["disposition"] not in {"ATTRIBUTED", "UNATTRIBUTED", "VERIFIED_EXTERNAL"}
            ):
                raise ValueError("INVALID_ORDER_CUT_RESPONSE")
            fact = GatewayExecutionObservation.from_mapping(item["fact"])
            if fact.account_scope != account_ref:
                raise ValueError("INVALID_ORDER_CUT_RESPONSE")
    return canonical(value)
