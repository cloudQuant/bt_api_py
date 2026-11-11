"""Explicit local product-page observation; no fetching or authority is implied."""

import json
import os
import stat
from dataclasses import asdict

from bt_api_py.trading_calendar_preview_reports import (
    TRUSTED_LOCAL_POSIX,
    _directory_matches,
    _open_directory,
    _supported,
)

_MAX_PAGE_BYTES = 1024 * 1024
_MAX_RETAINED_BYTES = 8 * 1024 * 1024
_REASONS = frozenset(
    {
        "FILE_UNAVAILABLE_OR_CHANGED",
        "FILE_RESOURCE_LIMIT",
        "PARSER_UNAVAILABLE",
        "PARSER_REJECTED",
        "SOURCE_PATH_IN_CONTENT",
    }
)
_PARSER_FAILURES = frozenset(
    {"INVALID_SOURCE", "INVALID_INPUT", "INPUT_TOO_LARGE", "INVALID_UTF8", "STRUCTURE_CHANGED"}
)


def _canonical(value):
    return json.dumps(
        value, ensure_ascii=True, sort_keys=True, separators=(",", ":"), allow_nan=False
    ).encode("ascii")


def unknown_product(reason, parser_status=None):
    if reason not in _REASONS:
        reason = "FILE_UNAVAILABLE_OR_CHANGED"
    if parser_status not in _PARSER_FAILURES or reason != "PARSER_REJECTED":
        parser_status = None
    return {
        "status": "UNKNOWN",
        "reason": reason,
        "parser_status": parser_status,
        "source_url": None,
        "sha256": None,
        "records": None,
        "review_reasons": None,
        "verification": "UNVERIFIED",
        "source_authentication": "NOT_PROVEN",
        "retained_data_binding": "NOT_PROVEN",
        "current_record": "UNKNOWN",
        "coverage": "UNKNOWN",
        "publication": "NOT_AUTHORIZED",
    }


def _identity(value):
    return (value.st_dev, value.st_ino, value.st_size, value.st_mtime_ns, value.st_ctime_ns)


def _read_page(path):
    if type(path) is not str or not path or len(path) > 4096 or not os.path.isabs(path):
        raise ValueError("invalid path")
    if ".." in path.split(os.sep) or "\x00" in path:
        raise ValueError("invalid path")
    parent_path, name = os.path.split(path)
    if not name or name == ".":
        raise ValueError("invalid file")
    # Symlink ancestors are rejected, including macOS /tmp. Callers can pass
    # the explicit physical path (e.g. /private/tmp) when appropriate.
    if parent_path == os.sep:
        descriptors = [os.open(os.sep, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)]
        chain = []
    else:
        descriptors, chain = _open_directory(parent_path)
    flags = os.O_RDONLY | os.O_NOFOLLOW | getattr(os, "O_NONBLOCK", 0)
    fd = None
    try:
        if not _directory_matches(chain):
            raise ValueError("directory changed")
        fd = os.open(name, flags, dir_fd=descriptors[-1])
        before = os.fstat(fd)
        if not stat.S_ISREG(before.st_mode):
            raise ValueError("not a regular file")
        if before.st_size > _MAX_PAGE_BYTES:
            raise OverflowError("page too large")
        data = bytearray()
        while len(data) <= _MAX_PAGE_BYTES:
            part = os.read(fd, _MAX_PAGE_BYTES + 1 - len(data))
            if not part:
                break
            data.extend(part)
        if len(data) > _MAX_PAGE_BYTES:
            raise OverflowError("page too large")
        after = os.fstat(fd)
        entry = os.stat(name, dir_fd=descriptors[-1], follow_symlinks=False)
        if (
            _identity(before) != _identity(after)
            or _identity(after) != _identity(entry)
            or len(data) != before.st_size
            or not stat.S_ISREG(entry.st_mode)
            or not _directory_matches(chain)
        ):
            raise ValueError("page changed")
        return bytes(data)
    finally:
        if fd is not None:
            os.close(fd)
        for directory_fd in reversed(descriptors):
            os.close(directory_fd)


def observe_product_page(*, source_url, path, filesystem, additional_sensitive=()):
    """Read one bounded local file. No other file or network source is consulted."""
    if filesystem != TRUSTED_LOCAL_POSIX or not _supported():
        return unknown_product("FILE_UNAVAILABLE_OR_CHANGED")
    try:
        html = _read_page(path)
    except OverflowError:
        return unknown_product("FILE_RESOURCE_LIMIT")
    except (OSError, ValueError, TypeError):
        return unknown_product("FILE_UNAVAILABLE_OR_CHANGED")
    try:
        from bt_api_ctp.calendar_shfe_product_page import ProductPage, parse_shfe_product_page

        result = parse_shfe_product_page(source_url=source_url, html=html)
        if type(result) is not ProductPage:
            raise ValueError("invalid parser result")
        if result.status not in {"CANDIDATE", "NEEDS_REVIEW"}:
            return unknown_product("PARSER_REJECTED", result.status)
        data = asdict(result)
        data.update(
            {
                "reason": "UNVERIFIED_PAGE",
                "parser_status": result.status,
                "retained_data_binding": "NOT_PROVEN",
                "publication": "NOT_AUTHORIZED",
            }
        )
        # Reject the complete supplied local path and its lexical/case variant
        # anywhere in retained data. This is not general secret redaction.
        sensitive = (path, os.path.normpath(path), *additional_sensitive)
        variants = tuple(value.casefold() for value in sensitive if type(value) is str and value)
        raw = _canonical(data)
        text = json.dumps(data, ensure_ascii=False).casefold()
        if any(value in text for value in variants):
            return unknown_product("SOURCE_PATH_IN_CONTENT")
        if len(raw) > _MAX_RETAINED_BYTES:
            return unknown_product("FILE_RESOURCE_LIMIT")
        detached = json.loads(raw)
        _validate_product(detached)
        return detached
    except ImportError:
        return unknown_product("PARSER_UNAVAILABLE")
    except Exception:
        return unknown_product("PARSER_REJECTED")


def _validate_product(data):
    fields = {
        "status",
        "reason",
        "parser_status",
        "source_url",
        "sha256",
        "records",
        "review_reasons",
        "verification",
        "source_authentication",
        "retained_data_binding",
        "current_record",
        "coverage",
        "publication",
    }
    if type(data) is not dict or set(data) != fields:
        raise ValueError("invalid product observation")
    if tuple(
        data[k]
        for k in (
            "verification",
            "source_authentication",
            "retained_data_binding",
            "current_record",
            "coverage",
            "publication",
        )
    ) != ("UNVERIFIED", "NOT_PROVEN", "NOT_PROVEN", "UNKNOWN", "UNKNOWN", "NOT_AUTHORIZED"):
        raise ValueError("invalid product qualification")
    if data["status"] == "UNKNOWN":
        if (
            data["reason"] not in _REASONS
            or data["parser_status"] not in _PARSER_FAILURES | {None}
            or (data["parser_status"] is not None and data["reason"] != "PARSER_REJECTED")
            or any(
                data[k] is not None for k in ("source_url", "sha256", "records", "review_reasons")
            )
        ):
            raise ValueError("invalid unknown product")
        return
    from bt_api_ctp.calendar_shfe_product_page import _record, _url_kind

    kind = _url_kind(data["source_url"])
    if (
        kind is None
        or any(ord(c) < 32 or 0x7F <= ord(c) <= 0x9F for c in data["source_url"])
        or data["status"] not in {"CANDIDATE", "NEEDS_REVIEW"}
        or data["parser_status"] != data["status"]
        or data["reason"] != "UNVERIFIED_PAGE"
        or type(data["sha256"]) is not str
        or len(data["sha256"]) != 64
        or any(c not in "0123456789abcdef" for c in data["sha256"])
        or type(data["records"]) is not list
        or not 0 < len(data["records"]) <= 100
        or len(_canonical(data)) > _MAX_RETAINED_BYTES
    ):
        raise ValueError("invalid product page")
    checked = []
    for row in data["records"]:
        if (
            type(row) is not dict
            or set(row)
            != {
                "kind",
                "symbol",
                "venue",
                "raw_date",
                "fields",
                "unknown_reasons",
                "effective_date",
            }
            or type(row["fields"]) is not list
            or not 0 < len(row["fields"]) <= 100
        ):
            raise ValueError("invalid product row")
        values = {}
        for pair in row["fields"]:
            if (
                type(pair) is not list
                or len(pair) != 2
                or type(pair[0]) is not str
                or type(pair[1]) is not str
                or len(pair[0]) > 100
                or len(pair[1]) > 8192
                or pair[0] in values
                or any(
                    ord(c) < 32 or 0x7F <= ord(c) <= 0x9F or 0xD800 <= ord(c) <= 0xDFFF
                    for c in pair[0] + pair[1]
                )
            ):
                raise ValueError("invalid product fields")
            values[pair[0]] = pair[1]
        expected = json.loads(_canonical(asdict(_record(values, kind))))
        if row != expected:
            raise ValueError("product classification mismatch")
        checked.append(row)
    reasons = []
    if any(row["unknown_reasons"] for row in checked):
        reasons.append("RECORD_UNRESOLVED")
    if len({row["symbol"] for row in checked if row["symbol"] is not None}) > 1:
        reasons.append("SYMBOL_CONFLICT")
    if len({row["venue"] for row in checked if row["venue"] is not None}) > 1:
        reasons.append("VENUE_CONFLICT")
    if data["review_reasons"] != reasons or data["status"] != (
        "NEEDS_REVIEW" if reasons else "CANDIDATE"
    ):
        raise ValueError("product status mismatch")
