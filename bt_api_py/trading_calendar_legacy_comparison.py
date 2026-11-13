"""Bounded, offline comparison of declared simulation windows and a legacy port.

The caller supplies both ports, exact identities, owner day, expected phases and
legacy source declaration. These are unauthenticated declarations, not market
facts, clock evidence or trading authorization. Window absence is UNKNOWN.
"""

from __future__ import annotations

import re
from datetime import date, datetime, timedelta
from zoneinfo import ZoneInfo

from bt_api_base.trading_session_protocol import TradingPhase, TradingWindowKind

_MAX_POINTS = 288
_MAX_WINDOWS = 128
_ID = re.compile(r"[A-Za-z0-9][A-Za-z0-9_.-]{0,127}\Z", re.ASCII)
_SHA = re.compile(r"[0-9a-f]{64}\Z", re.ASCII)
_PRIVATE = re.compile(r"(?i)password|passwd|secret|token|credential|authorization|api[_-]?key")
_PHASES = {"continuous", "auction", "closed", "unknown"}
_LABEL = {
    "environment": "SIMULATION_ONLY",
    "source_authentication": "NOT_PROVEN",
    "identity": "CALLER_DECLARED_NOT_PROVEN",
    "expectation": "CALLER_DECLARED_NOT_PROVEN",
    "legacy_source": "CALLER_DECLARED_NOT_PROVEN",
    "clock": "CALLER_DECLARED_NOT_PROVEN",
    "trade_authorization": "NONE",
    "legacy_41_products": "NOT_RUN",
}


def _identifier(value):
    return (
        type(value) is str and _ID.fullmatch(value) is not None and _PRIVATE.search(value) is None
    )


def _utc(value):
    try:
        return (
            type(value) is datetime
            and value.tzinfo is not None
            and value.utcoffset() == timedelta(0)
        )
    except Exception:
        return False


def _comparison(actual, expected):
    if actual == "unknown" or expected == "unknown":
        return "UNKNOWN"
    return "MATCH" if actual == expected else "MISMATCH"


_BINDING_FIELDS = (
    "mode",
    "route",
    "source_generation",
    "bound_trading_day",
    "last_sequence",
    "calendar_ref",
    "publication_id",
    "content_sha256",
    "coverage_status",
    "coverage_manifest_sha256",
    "binding_sha256",
    "version_sha256",
    "load_error",
    "binding_reason",
    "data_origin",
)


def _binding_identity(status):
    identity = {name: getattr(status, name, None) for name in _BINDING_FIELDS}
    coverage = getattr(status, "declared_coverage", None)
    if type(coverage) is not tuple or not 1 <= len(coverage) <= 64:
        raise ValueError("coverage unavailable")
    identity["declared_coverage"] = tuple(
        (
            getattr(item, "venue", None),
            getattr(item, "start_day", None),
            getattr(item, "end_day", None),
        )
        for item in coverage
    )
    return identity


def _modern_snapshot(binding, scope):
    before = _binding_identity(binding.status())
    if (
        before["mode"] != "SIMULATION_ONLY"
        or before["coverage_status"] != "DECLARED_UNVERIFIED"
        or before["load_error"] is not None
        or type(before["bound_trading_day"]) is not date
        or not _identifier(before["binding_reason"])
        or not _identifier(before["data_origin"])
        or not _identifier(before["route"])
        or not _identifier(before["source_generation"])
        or type(before["last_sequence"]) is not int
        or before["last_sequence"] < 0
        or not _identifier(before["calendar_ref"])
        or not _identifier(before["publication_id"])
        or any(
            type(before[name]) is not str or _SHA.fullmatch(before[name]) is None
            for name in (
                "version_sha256",
                "content_sha256",
                "coverage_manifest_sha256",
                "binding_sha256",
            )
        )
    ):
        raise ValueError("binding unavailable")
    coverage = before["declared_coverage"]
    if (
        any(
            not _identifier(venue)
            or type(start) is not date
            or type(end) is not date
            or end < start
            for venue, start, end in coverage
        )
        or len({item[0] for item in coverage}) != len(coverage)
        or not any(
            venue == scope["venue"] and start <= scope["trading_day"] <= end
            for venue, start, end in coverage
        )
    ):
        raise ValueError("owner day not covered")
    query = binding.query(**scope)
    result = getattr(query, "result", None)
    if (
        _binding_identity(getattr(query, "binding", None)) != before
        or getattr(result, "status", None) != "COMPLETE"
        or getattr(result, "sources_verified", None) is not False
        or getattr(result, "calendar_ref", None) != before["calendar_ref"]
        or any(getattr(result, key, None) != value for key, value in scope.items())
        or type(getattr(result, "source_ids", None)) is not tuple
        or not result.source_ids
        or any(not _identifier(value) for value in result.source_ids)
        or type(getattr(result, "windows", None)) is not tuple
        or len(result.windows) > _MAX_WINDOWS
    ):
        raise ValueError("unavailable scope evidence")
    attribution = getattr(query, "simulation_provenance", None)
    if attribution is None:
        attribution = getattr(query, "declared_mock_provenance", None)
    if (
        type(attribution) is not dict
        or attribution.get("environment") != "SIMULATION_ONLY"
        or type(attribution.get("windows")) is not list
    ):
        raise ValueError("window source unavailable")
    attributed = attribution["windows"]
    schema = attribution.get("schema")
    mixed_locator = None
    if schema == "bt_api.calendar-declared-mock-windows/1":
        if any(
            item.get("source_type") != "MOCK"
            or item.get("source_id") != "MOCK.simulation_only"
            or item.get("source_authentication") != "NOT_PROVEN"
            or item.get("source_id") not in result.source_ids
            for item in attributed
            if type(item) is dict
        ):
            raise ValueError("mock source unavailable")
    elif schema == "bt_api.calendar-mixed-window-provenance/2":
        target = attribution.get("target")
        if (
            type(target) is not dict
            or (
                target.get("venue"),
                target.get("kind"),
                target.get("product"),
                target.get("mock_contract"),
            )
            != (scope["venue"], scope["kind"], scope["product"], scope["contract"])
            or type(target.get("valid_from")) is not str
            or type(target.get("valid_to")) is not str
            or not target["valid_from"] <= scope["trading_day"].isoformat() <= target["valid_to"]
        ):
            raise ValueError("mixed scope unavailable")
        locator = attribution.get("evidence_locator")
        if (
            type(locator) is not dict
            or any(
                not _identifier(locator.get(key))
                for key in (
                    "candidate_id",
                    "parent_candidate_id",
                    "observation_id",
                    "observation_run_id",
                    "capture_id",
                )
            )
            or type(locator.get("response_digest")) is not str
            or _SHA.fullmatch(locator["response_digest"]) is None
        ):
            raise ValueError("mixed source unavailable")
        mixed_locator = {
            key: locator[key]
            for key in (
                "candidate_id",
                "parent_candidate_id",
                "observation_id",
                "observation_run_id",
                "capture_id",
                "response_digest",
            )
        }
        for item in attributed:
            if type(item) is dict:
                expected = (
                    "AKSHARE.simulation_unverified"
                    if item.get("source_type") == "AKSHARE"
                    else "MOCK.simulation_only"
                )
                if expected not in result.source_ids:
                    raise ValueError("mixed source unavailable")
    else:
        raise ValueError("unknown attribution schema")
    if (
        len(attributed) != len(result.windows)
        or any(
            type(item) is not dict
            or not _identifier(item.get("window_id"))
            or item.get("source_type") not in {"MOCK", "AKSHARE"}
            for item in attributed
        )
        or len({item["window_id"] for item in attributed}) != len(attributed)
        or len({getattr(window, "window_id", None) for window in result.windows})
        != len(result.windows)
        or {item["window_id"] for item in attributed}
        != {getattr(window, "window_id", None) for window in result.windows}
    ):
        raise ValueError("window attribution incomplete")
    attribution_by_id = {item["window_id"]: item for item in attributed}
    source_by_id = {
        item["window_id"]: (
            item["source_type"],
            "AKSHARE.simulation_unverified"
            if item["source_type"] == "AKSHARE"
            else "MOCK.simulation_only",
        )
        for item in attributed
    }
    windows = []
    for window in result.windows:
        start, end = getattr(window, "start_utc", None), getattr(window, "end_utc", None)
        phase = getattr(window, "phase", None)
        if (
            not _identifier(getattr(window, "window_id", None))
            or not _utc(start)
            or not _utc(end)
            or end <= start
            or phase not in {"continuous", "auction"}
            or getattr(window, "trading_day", None) != scope["trading_day"]
        ):
            raise ValueError("window unavailable")
        source_type, source_id = source_by_id[window.window_id]
        if schema == "bt_api.calendar-mixed-window-provenance/2":
            item = attribution_by_id[window.window_id]
            fields, simulation = item.get("fields"), item.get("simulation")
            local_start = start.astimezone(ZoneInfo("Asia/Shanghai"))
            local_end = end.astimezone(ZoneInfo("Asia/Shanghai"))
            offset = (local_end.date() - local_start.date()).days
            basis = "AKSHARE_SYNTAX_CANDIDATE" if source_type == "AKSHARE" else "MOCK_ASSUMPTION"
            transition = (
                ("EXPLICIT_NEXT_DAY" if offset == 1 else "SAME_CLOCK_DAY")
                if source_type == "AKSHARE"
                else offset
            )
            if (
                item.get("scope") not in {"DAY", "NIGHT"}
                or offset not in {0, 1}
                or type(fields) is not dict
                or fields.get("scope") != {"value": item["scope"], "basis": basis}
                or fields.get("start") != {"value": local_start.strftime("%H:%M"), "basis": basis}
                or fields.get("end") != {"value": local_end.strftime("%H:%M"), "basis": basis}
                or fields.get("day_transition") != {"value": transition, "basis": basis}
                or type(simulation) is not dict
                or simulation.get("phase") != {"value": phase, "basis": "MOCK_ASSUMPTION"}
            ):
                raise ValueError("mixed window source mismatch")
        phase_basis = (
            "MOCK_ASSUMPTION"
            if schema == "bt_api.calendar-mixed-window-provenance/2"
            else "MOCK_SIMULATION_DECLARATION"
        )
        windows.append((start, end, phase, window.window_id, source_type, source_id, phase_basis))
    windows.sort(key=lambda item: item[0])
    if any(left[1] > right[0] for left, right in zip(windows, windows[1:], strict=False)):
        raise ValueError("overlapping windows")
    return before, windows, mixed_locator


def _legacy_phase(schedule, instrument_id, owner_day, instant, legacy_ref):
    try:
        state = schedule.session_state(instrument_id, instant)
        if (
            getattr(state, "instrument_id", None) != instrument_id
            or getattr(state, "at_utc", None) != instant
            or getattr(state, "calendar_ref", None) != legacy_ref
            or getattr(state, "trading_day", None) != owner_day
        ):
            return "unknown"
        phase = getattr(state, "phase", None)
        if phase in {TradingPhase.CONTINUOUS, TradingPhase.AUCTION}:
            window = getattr(state, "current_window", None)
            start, end = getattr(window, "start_utc", None), getattr(window, "end_utc", None)
            expected_kind = (
                TradingWindowKind.CONTINUOUS
                if phase is TradingPhase.CONTINUOUS
                else TradingWindowKind.AUCTION
            )
            if (
                getattr(window, "trading_day", None) != owner_day
                or getattr(window, "kind", None) is not expected_kind
                or not _utc(start)
                or not _utc(end)
                or not start <= instant < end
            ):
                return "unknown"
            return phase.value
        if phase is TradingPhase.CLOSED and getattr(state, "current_window", None) is None:
            following = getattr(state, "next_continuous", None)
            start, end = getattr(following, "start_utc", None), getattr(following, "end_utc", None)
            if (
                getattr(following, "trading_day", None) == owner_day
                and getattr(following, "kind", None) is TradingWindowKind.CONTINUOUS
                and _utc(start)
                and _utc(end)
                and instant < start < end
            ):
                return "closed"
        return "unknown"
    except Exception:
        return "unknown"


def compare(
    *,
    binding,
    legacy_schedule,
    mode,
    venue,
    kind,
    product,
    contract,
    instrument_id,
    owner_day,
    start_utc,
    end_utc,
    expected_phases,
    expected_source_id,
    legacy_source_id,
    legacy_calendar_ref,
    simulation_mapping=None,
):
    """Compare independently declared phases on a bounded half-open 5-minute grid.

    The legacy source, instrument pairing, expected samples and owner day are
    caller declarations. A matching result is not a source/identity/clock proof.
    """
    unavailable = {
        **_LABEL,
        "status": "UNKNOWN",
        "reason": "INPUT_OR_EVIDENCE_UNAVAILABLE",
        "samples": [],
    }
    try:
        if type(expected_phases) not in (list, tuple):
            return unavailable
        # Keep the caller's samples fixed before invoking any supplied port.
        expected_phases = tuple(expected_phases)
        if simulation_mapping is not None:
            if type(simulation_mapping) is not dict or any(
                type(key) is not str or type(value) is not str
                for key, value in simulation_mapping.items()
            ):
                return unavailable
            # Retain the declaration as it was received, even if an injected
            # binding or legacy callback later mutates the caller's dictionary.
            simulation_mapping = simulation_mapping.copy()
        if (
            mode != "SIMULATION_ONLY"
            or type(mode) is not str
            or any(
                not _identifier(value)
                for value in (
                    venue,
                    kind,
                    product,
                    contract,
                    instrument_id,
                    expected_source_id,
                    legacy_source_id,
                    legacy_calendar_ref,
                )
            )
            or kind != "future"
            or type(owner_day) is not date
            or not _utc(start_utc)
            or not _utc(end_utc)
            or start_utc.second
            or start_utc.microsecond
            or start_utc.minute % 5
            or end_utc <= start_utc
            or (end_utc - start_utc) % timedelta(minutes=5)
        ):
            return unavailable
        from bt_api_ctp.trading_session_schedule import product_session_profile

        profile = product_session_profile(instrument_id)
        if profile is None or profile[0] != venue:
            return unavailable
        if simulation_mapping is None:
            if contract != instrument_id or (profile[0], profile[1]) != (venue, product):
                return unavailable
        else:
            # The caller explicitly asserts a simulation-only crosswalk; none of
            # these values establish an exchange or instrument identity.
            expected_mapping = {
                "mode": "SIMULATION_ONLY",
                "modern_venue": venue,
                "modern_kind": kind,
                "modern_product": product,
                "modern_contract": contract,
                "legacy_instrument_id": instrument_id,
                "legacy_profile_venue": profile[0],
                "legacy_profile_product": profile[1],
                "owner_day": owner_day.isoformat(),
                "legacy_calendar_ref": legacy_calendar_ref,
            }
            if (
                set(simulation_mapping)
                != set(expected_mapping) | {"calendar_ref", "version_sha256"}
                or any(
                    simulation_mapping.get(key) != value for key, value in expected_mapping.items()
                )
                or not contract.startswith("mock_")
                or not _identifier(simulation_mapping.get("calendar_ref"))
                or type(simulation_mapping.get("version_sha256")) is not str
                or _SHA.fullmatch(simulation_mapping["version_sha256"]) is None
            ):
                return unavailable
        count = (end_utc - start_utc) // timedelta(minutes=5)
        if (
            count < 1
            or count > _MAX_POINTS
            or len(expected_phases) != count
            or any(type(item) is not str or item not in _PHASES for item in expected_phases)
        ):
            return unavailable
        scope = {
            "venue": venue,
            "kind": kind,
            "product": product,
            "contract": contract,
            "trading_day": owner_day,
        }
        before, windows, mixed_locator = _modern_snapshot(binding, scope)
        if simulation_mapping is not None and (
            simulation_mapping["calendar_ref"] != before["calendar_ref"]
            or simulation_mapping["version_sha256"] != before["version_sha256"]
        ):
            return unavailable
        samples = []
        for index, expected in enumerate(expected_phases):
            instant = start_utc + timedelta(minutes=5 * index)
            active = [window for window in windows if window[0] <= instant < window[1]]
            modern = active[0][2] if len(active) == 1 else "unknown"
            legacy = _legacy_phase(
                legacy_schedule, instrument_id, owner_day, instant, legacy_calendar_ref
            )
            samples.append(
                {
                    "offset_minutes": index * 5,
                    "expected_phase": expected,
                    "modern_phase": modern,
                    "legacy_phase": legacy,
                    "modern_window_id": active[0][3] if len(active) == 1 else None,
                    "modern_source_type": active[0][4] if len(active) == 1 else None,
                    "modern_source_id": active[0][5] if len(active) == 1 else None,
                    "modern_window_time_source_type": active[0][4] if len(active) == 1 else None,
                    "modern_window_time_source_id": active[0][5] if len(active) == 1 else None,
                    "modern_source_scope": "WINDOW_TIME_FIELDS_ONLY"
                    if len(active) == 1
                    else "UNKNOWN",
                    "modern_phase_basis": active[0][6] if len(active) == 1 else "UNKNOWN",
                    "modern_vs_expected": _comparison(modern, expected),
                    "legacy_vs_expected": _comparison(legacy, expected),
                    "modern_vs_legacy": _comparison(modern, legacy),
                }
            )
        if _binding_identity(binding.status()) != before:
            return {**unavailable, "reason": "BINDING_CHANGED"}
        comparisons = [
            sample[key]
            for sample in samples
            for key in ("modern_vs_expected", "legacy_vs_expected", "modern_vs_legacy")
        ]
        status = (
            "MISMATCH"
            if "MISMATCH" in comparisons
            else "UNKNOWN"
            if "UNKNOWN" in comparisons
            else "MATCH"
        )
        return {
            **_LABEL,
            "status": status,
            "reason": "DECLARED_SAMPLE_COMPARISON",
            "calendar_ref": before["calendar_ref"],
            "version_sha256": before["version_sha256"],
            "content_sha256": before["content_sha256"],
            "legacy_calendar_ref": legacy_calendar_ref,
            "scope": {
                "venue": venue,
                "kind": kind,
                "product": product,
                "contract": contract,
                "instrument_id": instrument_id,
                "owner_day": owner_day.isoformat(),
            },
            "sample_range": {
                "start_utc": start_utc.isoformat(),
                "end_utc": end_utc.isoformat(),
                "expected_source_id": expected_source_id,
                "legacy_source_id": legacy_source_id,
            },
            "binding": {
                **before,
                "bound_trading_day": before["bound_trading_day"].isoformat(),
                "declared_coverage": [
                    {"venue": v, "start_day": start.isoformat(), "end_day": end.isoformat()}
                    for v, start, end in before["declared_coverage"]
                ],
            },
            "legacy_scope_check": "STATIC_CTP_PROFILE_NOT_AUTHENTICATED",
            "simulation_mapping": dict(simulation_mapping)
            if simulation_mapping is not None
            else None,
            "mapping_identity_authentication": "NOT_PROVEN",
            "mapping_declaration_time": "CALL_TIME_NOT_AUTHENTICATED",
            "match_meaning": "DECLARED_SAMPLE_PHASES_ONLY_NO_IDENTITY_OR_TRADE_AUTHORIZATION",
            "source_id_basis": "WINDOW_TIME_FIELDS_ONLY_NOT_PHASE_NOT_AUTHENTICATED",
            "mixed_evidence_locator": mixed_locator,
            "mixed_evidence_authentication": "NOT_PROVEN",
            "coverage": "EXPLICIT_SAMPLES_ONLY",
            "sample_step_minutes": 5,
            "samples": samples,
        }
    except Exception:
        return unavailable
