"""Offline, non-publishable projection of an AKShare observation and mock windows.

The caller supplies a root and the original observation and run IDs. The
projection rereads the sidecar with ``include_content=True``. Sidecar hashes and
IDs do not authenticate who read or wrote them. The result is a detached view,
not a /2 business snapshot, account calendar, approval, publication or CURRENT.

``semantic`` is deterministic and excludes capture IDs, fetch timestamps and
response digests. ``trace`` contains observation IDs and the response digest;
these values do not authenticate source or prove freshness. Mock windows and
AKShare syntax candidates have separate per-window labels. No source establishes
contract identity, effective periods, trading days, coverage or order authority.
"""

from __future__ import annotations

import hashlib
import re
from datetime import date

from bt_api_py import trading_calendar_akshare_observation as observation
from bt_api_py import trading_calendar_detail_collection as storage
from bt_api_py import trading_calendar_observation_run as runs

_VENUES = frozenset({"SHFE", "INE", "CZCE", "DCE", "CFFEX", "GFEX"})
_CONTRACT = re.compile(r"[A-Za-z]{1,8}[0-9]{3,4}\Z", re.ASCII)
_MOCK_ID = re.compile(r"mock_[A-Za-z0-9_.-]{1,118}\Z", re.ASCII)
_WINDOW_ID = re.compile(r"[A-Za-z0-9][A-Za-z0-9_.-]{0,63}\Z", re.ASCII)
_CLOCK = re.compile(r"(?:[01][0-9]|2[0-3]):[0-5][0-9]\Z", re.ASCII)
_HASH = re.compile(r"[0-9a-f]{64}\Z", re.ASCII)


def _mark_overlaps(windows):
    segments = []
    for item in windows:
        start = int(item["start"][:2]) * 60 + int(item["start"][3:])
        end = int(item["end"][:2]) * 60 + int(item["end"][3:])
        wraps = item.get("end_day_offset") == 1 or item.get("day_transition") in {
            "EXPLICIT_NEXT_DAY",
            "INFERRED_NEXT_DAY",
        }
        segments.append([(start, 1440), (0, end)] if wraps else [(start, end)])
    conflicts = []
    for i in range(len(windows)):
        for j in range(i + 1, len(windows)):
            if any(a < d and c < b for a, b in segments[i] for c, d in segments[j]):
                conflicts.append(
                    {"left": i, "right": j, "status": "POTENTIAL_DATE_ALIGNMENT_UNKNOWN"}
                )
                windows[i]["use"] = windows[j]["use"] = "CONFLICT_REVIEW"
    return conflicts


def _scope(value):
    venue, contract, requested = (
        value.get(key) for key in ("requested_venue", "requested_contract", "requested_date")
    )
    if (
        type(venue) is not str
        or venue not in _VENUES
        or type(contract) is not str
        or _CONTRACT.fullmatch(contract) is None
        or type(requested) is not str
        or date.fromisoformat(requested).isoformat() != requested
        or value.get("request_scope") != "DECLARED_NOT_RESPONSE_IDENTITY_OR_TRADING_DAY"
    ):
        raise ValueError("invalid request scope")
    return {
        "venue": venue,
        "contract": contract,
        "requested_date": requested,
        "scope_status": "DECLARED",
    }


def _fallback(value, scope):
    if value is None:
        return None
    if (
        type(value) is not dict
        or set(value)
        != {
            "data_origin",
            "environment",
            "mode",
            "requested_venue",
            "requested_contract",
            "mock_contract",
            "windows",
        }
        or value["data_origin"] != "MOCK"
        or value["environment"] != "SIMULATION_ONLY"
        or value["requested_venue"] != scope["venue"]
        or value["requested_contract"] != scope["contract"]
        or value["mode"] not in {"ADD_EXPLICIT", "WHEN_NO_AKSHARE", "MISSING_SCOPE"}
        or type(value["mock_contract"]) is not str
        or not _MOCK_ID.fullmatch(value["mock_contract"])
        or type(value["windows"]) is not list
        or not 1 <= len(value["windows"]) <= 128
    ):
        raise ValueError("invalid mock declaration")
    windows = []
    seen = set()
    for item in value["windows"]:
        if (
            type(item) is not dict
            or set(item) != {"id", "scope", "start", "end", "end_day_offset", "phase"}
            or type(item["id"]) is not str
            or not _WINDOW_ID.fullmatch(item["id"])
            or item["id"] in seen
            or item["scope"] not in {"DAY", "NIGHT"}
            or type(item["start"]) is not str
            or not _CLOCK.fullmatch(item["start"])
            or type(item["end"]) is not str
            or not _CLOCK.fullmatch(item["end"])
            or type(item["end_day_offset"]) is not int
            or item["end_day_offset"] not in {0, 1}
            or type(item["phase"]) is not str
            or item["phase"] not in {"continuous", "auction", "break", "UNKNOWN"}
            or (item["end_day_offset"] == 0 and item["end"] <= item["start"])
            or (item["end_day_offset"] == 1 and item["end"] >= item["start"])
        ):
            raise ValueError("invalid mock window")
        seen.add(item["id"])
        windows.append(
            {
                **item,
                "source_type": "MOCK",
                "verification": "SIMULATION_ONLY",
                "source_id": "MOCK.simulation_only",
                "use": "SIMULATION_ONLY",
            }
        )
    windows.sort(key=lambda item: item["id"])
    return {
        "mode": value["mode"],
        "windows": windows,
        "mapping": {
            "requested_venue": scope["venue"],
            "requested_contract": scope["contract"],
            "mock_contract": value["mock_contract"],
            "mapping_status": "SIMULATION_ONLY",
            "identity": "UNKNOWN",
        },
    }


def _revalidate(shown, observation_id, run_id):
    """Check the freshly reread result; the sidecar validates its actual intent."""
    if (
        shown.get("record_state") != "VISIBLE_UNAUTHENTICATED"
        or shown.get("source_type") != "AKSHARE"
        or not runs._id(shown.get("observation_id"))
        or not runs._id(shown.get("run_id"))
        or shown["observation_id"] != observation_id
        or shown["run_id"] != run_id
        or observation_id == run_id
        or type(shown.get("payload_sha256")) is not str
        or not _HASH.fullmatch(shown["payload_sha256"])
        or "content" not in shown
    ):
        raise ValueError("invalid observation")
    names = (
        "observation_id",
        "run_id",
        "transport_status",
        "source_id",
        "capture_id",
        "response_digest",
        "required_version",
        "observed_version",
        "captured_at",
        "content",
        "content_sha256",
        "time_role",
        "data_state",
        "classification_check",
        "automatic_retry",
        "timeout_enforced",
        "direct_child_cleanup",
        "descendant_cleanup",
        "timeout_retry",
        "transport_assurance",
        "source_type",
        "verification",
        "source_authentication",
        "identity",
        "effective_period",
        "trading_day",
        "coverage",
        "publication",
        "association",
        "durability",
    )
    body = {key: shown[key] for key in names}
    if body["data_state"] == "DATA_OMITTED":
        if (
            shown.get("observation_status") != "UNKNOWN"
            or shown.get("capture_state") != "CAPTURE_TOO_LARGE"
        ):
            raise ValueError("invalid omitted observation")
        body["status"] = shown["reported_observation_status"]
        if body["content"] is not None or body["content_sha256"] is not None:
            raise ValueError("invalid omitted content")
    else:
        body["status"] = shown["observation_status"]
        if hashlib.sha256(storage._json(body["content"])).hexdigest() != body["content_sha256"]:
            raise ValueError("content digest changed")
    return body


def project(*, root, observation_id, run_id, filesystem, fallback=None):
    """Return detached semantic and trace sections; raises ValueError on conflict.

    ``fallback`` explicitly declares MOCK/SIMULATION_ONLY, a fictional contract,
    its windows, and ADD_EXPLICIT, WHEN_NO_AKSHARE or MISSING_SCOPE. The last
    adds explicit mock windows only for a DAY/NIGHT scope absent from AKShare;
    absence is not evidence of closure or complete real-world coverage. Times
    use half-open clock intervals; date alignment is always unknown. Clock
    overlaps across any scope are potentially conflicting and need review.
    The declared request scope does not imply the response matches that request.
    """
    try:
        if not runs._id(observation_id) or not runs._id(run_id) or observation_id == run_id:
            raise ValueError
        shown = observation.show(
            root=root,
            observation_id=observation_id,
            run_id=run_id,
            filesystem=filesystem,
            include_content=True,
        )
        if (
            type(shown) is not dict
            or shown.get("observation_id") != observation_id
            or shown.get("run_id") != run_id
        ):
            raise ValueError
        scope = _scope(shown)
        mock = _fallback(fallback, scope)
        state = shown.get("status")
        if state == "ATTEMPT_UNKNOWN":
            if not runs._id(shown.get("observation_id")) or not runs._id(shown.get("run_id")):
                raise ValueError
            body = None
            source_status = "UNKNOWN"
        elif state == "VISIBLE_UNAUTHENTICATED":
            body = _revalidate(shown, observation_id, run_id)
            source_status = "UNKNOWN" if body["data_state"] == "DATA_OMITTED" else body["status"]
        else:
            raise ValueError

        windows = []
        diagnostic = None
        if body is not None and body["content"] is not None:
            time = body["content"]["time"]
            if time is not None:
                if (
                    body["status"] == "SUCCESS"
                    and body["time_role"] == "CANDIDATE"
                    and time["status"] == "SYNTAX_CANDIDATE"
                ):
                    windows.extend(
                        {
                            "scope": item["scope"],
                            "start": item["start"],
                            "end": item["end"],
                            "day_transition": item["day_transition"],
                            "phase": "UNKNOWN",
                            "source_type": "AKSHARE_SYNTAX_CANDIDATE",
                            "verification": "UNVERIFIED",
                            "source_id": body["source_id"],
                            "use": "REVIEW_ONLY",
                        }
                        for item in time["ranges"]
                    )
                else:
                    diagnostic = {
                        "upstream_status": body["status"],
                        "parse_status": time["status"],
                        "review_reasons": list(time["review_reasons"]),
                        "uncertain_count": time["uncertain_count"],
                        "duplicate_count": time["duplicate_count"],
                        "ranges": [{**item, "use": "DIAGNOSTIC_ONLY"} for item in time["ranges"]],
                    }
        has_akshare = bool(windows)
        mapping = None
        if mock is not None:
            akshare_scopes = {item["scope"] for item in windows}
            selected = [
                item
                for item in mock["windows"]
                if mock["mode"] == "ADD_EXPLICIT"
                or (mock["mode"] == "WHEN_NO_AKSHARE" and not has_akshare)
                or (mock["mode"] == "MISSING_SCOPE" and item["scope"] not in akshare_scopes)
            ]
            if selected:
                windows.extend(selected)
                mapping = mock["mapping"]
        conflicts = _mark_overlaps(windows)
        semantic = {
            "schema": "bt_api.akshare-mock-projection/1",
            "request": scope,
            "source_status": source_status,
            "windows": windows,
            "diagnostic": diagnostic,
            "observation_state": "ATTEMPT_UNKNOWN"
            if body is None
            else "CAPTURE_TOO_LARGE"
            if body["data_state"] == "DATA_OMITTED"
            else "VISIBLE_UNAUTHENTICATED",
            "simulation_mapping": mapping,
            "conflicts": conflicts,
            "interval_convention": "HALF_OPEN",
            "date_alignment": "UNKNOWN",
            "simulation_status": "NEEDS_REVIEW" if conflicts else "DATE_ALIGNMENT_UNRESOLVED",
            "identity": "UNKNOWN",
            "effective_period": "UNKNOWN",
            "trading_day": "UNKNOWN",
            "coverage": "UNKNOWN",
            "source_authentication": "NOT_PROVEN",
            "publication": "NOT_AUTHORIZED",
            "account_use": "NOT_AUTHORIZED",
            "business_snapshot": "NOT_A_BUSINESS_SNAPSHOT",
        }
        trace = {
            "observation_id": shown["observation_id"],
            "run_id": shown["run_id"],
            "response_digest": body["response_digest"] if body is not None else None,
            "capture_id": body["capture_id"] if body is not None else None,
            "unverified_reported_status": shown.get("reported_observation_status")
            if body is not None and body["data_state"] == "DATA_OMITTED"
            else None,
        }
        return {"semantic": semantic, "trace": trace}
    except Exception:
        # The caller's data may contain paths, credentials or arbitrary objects.
        raise ValueError("invalid observation or mock declaration") from None
