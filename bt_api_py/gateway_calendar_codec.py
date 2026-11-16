"""Bounded wire contract for simulated, read-only Gateway calendar queries."""
from __future__ import annotations

import json
import re
from copy import deepcopy
from collections.abc import Mapping
from datetime import date, datetime, timedelta, timezone

_ID = re.compile(r"[A-Za-z0-9][A-Za-z0-9_.:-]{0,127}\Z", re.ASCII)
_HASH = re.compile(r"[0-9a-f]{64}\Z", re.ASCII)
_SCOPE = frozenset({"mode", "route", "venue", "kind", "product", "contract", "trading_day"})
_MAX_BYTES = 32768


def _id(value):
    return type(value) is str and _ID.fullmatch(value) is not None


def _day(value):
    if type(value) is not str:
        raise ValueError("INVALID_CALENDAR_VALUE")
    result = date.fromisoformat(value)
    if result.isoformat() != value:
        raise ValueError("INVALID_CALENDAR_VALUE")
    return result


def _utc(value):
    if type(value) is not str or not value.endswith("Z"):
        raise ValueError("INVALID_CALENDAR_VALUE")
    result = datetime.fromisoformat(value[:-1] + "+00:00")
    if result.utcoffset() != timedelta(0) or result.isoformat().replace("+00:00", "Z") != value:
        raise ValueError("INVALID_CALENDAR_VALUE")
    return result


def decode_calendar_query(arguments):
    """Return the plain scope echo and query parameters; reject every extra field."""
    if not isinstance(arguments, Mapping) or set(arguments) != _SCOPE:
        raise ValueError("INVALID_CALENDAR_REQUEST")
    if (arguments["mode"] != "SIMULATION_ONLY" or type(arguments["mode"]) is not str
            or any(not _id(arguments[name]) for name in ("route", "venue", "kind", "product", "contract"))
            or arguments["kind"] not in {"future", "option"}):
        raise ValueError("INVALID_CALENDAR_REQUEST")
    day = _day(arguments["trading_day"])
    return dict(arguments), {name: (day if name == "trading_day" else arguments[name])
                             for name in ("venue", "kind", "product", "contract", "trading_day")}


def validate_calendar_response(value):
    """Validate and return the plain response; this never grants source authority."""
    keys = {"schema", "scope", "registration_id", "service_epoch", "status", "reason", "binding",
            "venue_day", "windows", "source_authentication", "trade_authorization"}
    if type(value) is not dict or set(value) != keys or value["schema"] != "sdk.gateway.calendar.query/1":
        raise ValueError("INVALID_CALENDAR_RESPONSE")
    decode_calendar_query(value["scope"])
    if (not _id(value["registration_id"]) or not _id(value["service_epoch"])
            or value["status"] not in {"COMPLETE", "UNKNOWN", "OUT_OF_SCOPE"}
            or value["reason"] not in {"OK", "SOURCE_UNAVAILABLE", "OWNER_CHANGED", "QUERY_UNAVAILABLE", "OUT_OF_SCOPE", "INVALID_RESULT", "DAY_OUT_OF_RANGE", "COVERAGE_UNAVAILABLE"}
            or value["source_authentication"] != "NOT_PROVEN" or value["trade_authorization"] != "NONE"):
        raise ValueError("INVALID_CALENDAR_RESPONSE")
    reasons = {"COMPLETE": {"OK"}, "OUT_OF_SCOPE": {"OUT_OF_SCOPE", "DAY_OUT_OF_RANGE"},
               "UNKNOWN": {"SOURCE_UNAVAILABLE", "OWNER_CHANGED", "QUERY_UNAVAILABLE",
                           "INVALID_RESULT", "COVERAGE_UNAVAILABLE"}}
    if value["reason"] not in reasons[value["status"]]:
        raise ValueError("INVALID_CALENDAR_RESPONSE")
    binding = value["binding"]
    if binding is not None:
        fields = {"route", "source_generation", "bound_trading_day", "calendar_ref", "publication_id",
                  "content_sha256", "coverage_manifest_sha256", "binding_sha256", "version_sha256",
                  "coverage_status", "coverage_start", "coverage_end", "load_status", "data_origin"}
        if type(binding) is not dict or set(binding) != fields:
            raise ValueError("INVALID_CALENDAR_RESPONSE")
        if (binding["route"] != value["scope"]["route"]
                or any(not _id(binding[name]) for name in ("route", "source_generation", "calendar_ref", "publication_id"))
                or any(type(binding[name]) is not str or _HASH.fullmatch(binding[name]) is None
                       for name in ("content_sha256", "coverage_manifest_sha256", "binding_sha256", "version_sha256"))
                or binding["coverage_status"] != "DECLARED_UNVERIFIED"
                or binding["data_origin"] not in {"UNSPECIFIED", "AKSHARE_MOCK"}
                or binding["load_status"] not in {"CURRENT", "STALE"}):
            raise ValueError("INVALID_CALENDAR_RESPONSE")
        _day(binding["bound_trading_day"])
        if _day(binding["coverage_start"]) > _day(binding["coverage_end"]):
            raise ValueError("INVALID_CALENDAR_RESPONSE")
        requested = _day(value["scope"]["trading_day"])
        bound = _day(binding["bound_trading_day"])
        if value["status"] == "COMPLETE" and not (
            bound <= requested <= bound + timedelta(days=366)
            and _day(binding["coverage_start"]) <= requested <= _day(binding["coverage_end"])
        ):
            raise ValueError("INVALID_CALENDAR_RESPONSE")
    venue = value["venue_day"]
    if (type(venue) is not dict or set(venue) != {"status", "day_status"}
            or venue["status"] not in {"COMPLETE", "UNKNOWN"}
            or (venue["status"] == "COMPLETE" and venue["day_status"] not in {"open", "closed"})
            or (venue["status"] == "UNKNOWN" and venue["day_status"] is not None)):
        raise ValueError("INVALID_CALENDAR_RESPONSE")
    windows = value["windows"]
    if type(windows) is not list or len(windows) > 128 or (value["status"] != "COMPLETE" and windows):
        raise ValueError("INVALID_CALENDAR_RESPONSE")
    if binding is None and (value["status"] != "UNKNOWN" or venue["status"] != "UNKNOWN"):
        raise ValueError("INVALID_CALENDAR_RESPONSE")
    if venue["status"] == "COMPLETE" and not (
        binding is not None
        and _day(binding["bound_trading_day"]) <= _day(value["scope"]["trading_day"])
        <= _day(binding["bound_trading_day"]) + timedelta(days=366)
        and _day(binding["coverage_start"]) <= _day(value["scope"]["trading_day"])
        <= _day(binding["coverage_end"])
    ):
        raise ValueError("INVALID_CALENDAR_RESPONSE")
    if venue["status"] == "COMPLETE" and venue["day_status"] == "closed" and windows:
        raise ValueError("INVALID_CALENDAR_RESPONSE")
    previous = None
    seen = set()
    for window in windows:
        if (type(window) is not dict or set(window) != {"window_id", "phase", "start_utc", "end_utc"}
                or not _id(window["window_id"]) or window["window_id"] in seen
                or window["phase"] not in {"continuous", "auction"}):
            raise ValueError("INVALID_CALENDAR_RESPONSE")
        start, end = _utc(window["start_utc"]), _utc(window["end_utc"])
        if start >= end or end - start > timedelta(days=2) or (previous is not None and start < previous):
            raise ValueError("INVALID_CALENDAR_RESPONSE")
        seen.add(window["window_id"])
        previous = end
    if len(json.dumps(value, ensure_ascii=True, separators=(",", ":")).encode()) > _MAX_BYTES:
        raise ValueError("INVALID_CALENDAR_RESPONSE")
    return deepcopy(value)


_WINDOWS_SCOPE = frozenset({"mode", "route", "venue", "kind", "product", "contract", "start_utc", "end_utc"})


def decode_calendar_windows_query(arguments):
    """Return exact scope echo and parsed UTC half-open endpoints."""
    if not isinstance(arguments, Mapping) or set(arguments) != _WINDOWS_SCOPE:
        raise ValueError("INVALID_CALENDAR_REQUEST")
    if (arguments["mode"] != "SIMULATION_ONLY" or type(arguments["mode"]) is not str
            or any(not _id(arguments[name]) for name in ("route", "venue", "kind", "product", "contract"))
            or arguments["kind"] not in {"future", "option"}):
        raise ValueError("INVALID_CALENDAR_REQUEST")
    start, end = _utc(arguments["start_utc"]), _utc(arguments["end_utc"])
    if not start < end or end - start > timedelta(days=366):
        raise ValueError("INVALID_CALENDAR_REQUEST")
    return dict(arguments), start, end


def validate_calendar_windows_response(value):
    """Check and detach a simulated response; gaps list only observed failures."""
    from zoneinfo import ZoneInfo
    keys = {"schema", "scope", "registration_id", "service_epoch", "status", "reason", "binding",
            "policy", "windows", "gaps", "source_authentication", "trade_authorization"}
    if (type(value) is not dict or set(value) != keys
            or value["schema"] != "sdk.gateway.calendar.windows/1"):
        raise ValueError("INVALID_CALENDAR_RESPONSE")
    scope, start, end = decode_calendar_windows_query(value["scope"])
    if (not _id(value["registration_id"]) or not _id(value["service_epoch"])
            or value["source_authentication"] != "NOT_PROVEN" or value["trade_authorization"] != "NONE"):
        raise ValueError("INVALID_CALENDAR_RESPONSE")
    reasons = {"COMPLETE": {"OK"}, "UNKNOWN": {"SOURCE_UNAVAILABLE", "OWNER_CHANGED", "POLICY_UNAVAILABLE",
        "DAY_OUT_OF_RANGE", "COVERAGE_UNAVAILABLE", "OWNER_DATA_UNAVAILABLE", "CONTRADICTORY_DATA",
        "POLICY_CONTRADICTED", "PAST_OWNER_OVERLAP", "CLOSURE_UNAVAILABLE", "INVALID_RESULT"}}
    if value["status"] not in reasons or value["reason"] not in reasons[value["status"]]:
        raise ValueError("INVALID_CALENDAR_RESPONSE")
    binding, policy, windows = value["binding"], value["policy"], value["windows"]
    if binding is not None:
        synthetic = {"schema": "sdk.gateway.calendar.query/1", "scope": {
            **{k: scope[k] for k in ("mode", "route", "venue", "kind", "product", "contract")},
            "trading_day": binding.get("bound_trading_day") if type(binding) is dict else None},
            "registration_id": value["registration_id"], "service_epoch": value["service_epoch"],
            "status": "UNKNOWN", "reason": "QUERY_UNAVAILABLE", "binding": binding,
            "venue_day": {"status": "UNKNOWN", "day_status": None}, "windows": [],
            "source_authentication": "NOT_PROVEN", "trade_authorization": "NONE"}
        validate_calendar_response(synthetic)
    if policy is not None:
        fields = {"policy_id", "declaration", "max_advance_days", "max_spill_days", "first_owner_day", "last_owner_day"}
        if (type(policy) is not dict or set(policy) != fields or not _id(policy["policy_id"])
                or policy["declaration"] != "DEPLOYMENT_DECLARED_UNVERIFIED"
                or type(policy["max_advance_days"]) is not int or not 0 <= policy["max_advance_days"] <= 31
                or type(policy["max_spill_days"]) is not int or not 0 <= policy["max_spill_days"] <= 7
                or (policy["first_owner_day"] is None) != (policy["last_owner_day"] is None)):
            raise ValueError("INVALID_CALENDAR_RESPONSE")
        if policy["first_owner_day"] is not None:
            first, last = _day(policy["first_owner_day"]), _day(policy["last_owner_day"])
            zone = ZoneInfo("Asia/Shanghai")
            if (first != start.astimezone(zone).date() - timedelta(days=policy["max_spill_days"])
                    or last != (end - timedelta(microseconds=1)).astimezone(zone).date() + timedelta(days=policy["max_advance_days"])
                    or (last - first).days + 1 > 405):
                raise ValueError("INVALID_CALENDAR_RESPONSE")
    if type(windows) is not list or len(windows) > 128 or (value["status"] != "COMPLETE" and windows):
        raise ValueError("INVALID_CALENDAR_RESPONSE")
    gaps = value["gaps"]
    if type(gaps) is not list or len(gaps) > 405 or (value["status"] == "COMPLETE" and gaps):
        raise ValueError("INVALID_CALENDAR_RESPONSE")
    previous_gap = None
    for gap in gaps:
        if (type(gap) is not dict or set(gap) != {"trading_day", "reason"}
                or gap["reason"] not in {"OWNER_DATA_UNAVAILABLE", "CONTRADICTORY_DATA", "POLICY_CONTRADICTED", "PAST_OWNER_OVERLAP"}
                or policy is None or policy["first_owner_day"] is None):
            raise ValueError("INVALID_CALENDAR_RESPONSE")
        day = _day(gap["trading_day"])
        if not first <= day <= last or (previous_gap is not None and day <= previous_gap):
            raise ValueError("INVALID_CALENDAR_RESPONSE")
        previous_gap = day
    if value["status"] == "COMPLETE":
        if (binding is None or policy is None or policy["first_owner_day"] is None
                or first < _day(binding["coverage_start"]) or last > _day(binding["coverage_end"])
                or start.astimezone(ZoneInfo("Asia/Shanghai")).date()
                   < _day(binding["bound_trading_day"]) - timedelta(days=policy["max_advance_days"])
                or (end - timedelta(microseconds=1)).astimezone(ZoneInfo("Asia/Shanghai")).date()
                   > _day(binding["bound_trading_day"]) + timedelta(days=366)):
            raise ValueError("INVALID_CALENDAR_RESPONSE")
    previous, seen = None, set()
    for window in windows:
        if (type(window) is not dict or set(window) != {"window_id", "phase", "trading_day", "start_utc", "end_utc"}
                or not _id(window["window_id"]) or window["phase"] not in {"continuous", "auction"}):
            raise ValueError("INVALID_CALENDAR_RESPONSE")
        owner, wstart, wend = _day(window["trading_day"]), _utc(window["start_utc"]), _utc(window["end_utc"])
        key = owner, window["window_id"]
        if (key in seen or owner < _day(binding["bound_trading_day"]) or not first <= owner <= last
                or not wstart < wend or wend - wstart > timedelta(days=2)
                or wstart >= end or wend <= start or (previous is not None and wstart < previous)
                or wstart.astimezone(zone).date() < owner - timedelta(days=policy["max_advance_days"])
                or (wend - timedelta(microseconds=1)).astimezone(zone).date() > owner + timedelta(days=policy["max_spill_days"])):
            raise ValueError("INVALID_CALENDAR_RESPONSE")
        seen.add(key)
        previous = wend
    if len(json.dumps(value, ensure_ascii=True, separators=(",", ":")).encode()) > _MAX_BYTES:
        raise ValueError("INVALID_CALENDAR_RESPONSE")
    return deepcopy(value)
