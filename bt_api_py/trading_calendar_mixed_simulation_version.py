"""Versionable AKShare/mock simulation content, with replayable local evidence.

``build`` only reads retained observations through the projection's ID-based
revalidation. ``validate`` replays that read; local hashes do not authenticate
an exchange or a writer. The semantic digest deliberately excludes trace, but
``evidence_digest`` binds *both* sections for a future version/review record.
This is not a published calendar, a /2 business snapshot or account authority.
"""

from __future__ import annotations

import hashlib
import hmac
import json
import re
from copy import deepcopy
from datetime import date
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from bt_api_py import trading_calendar_akshare_mock_projection as projection

SCHEMA = "bt_api.mixed-calendar-simulation/1"
_EVIDENCE_SCHEMA = "bt_api.mixed-calendar-simulation-evidence/1"
_PHASES = frozenset({"continuous", "auction", "break", "UNKNOWN"})
_OWNERSHIP = frozenset({"SAME_TRADING_DAY", "NEXT_TRADING_DAY", "UNKNOWN"})
_ZONE = re.compile(r"[A-Za-z0-9_+.-]+(?:/[A-Za-z0-9_+.-]+)*\Z", re.ASCII)


def _canonical(value):
    return json.dumps(
        value, sort_keys=True, ensure_ascii=True, separators=(",", ":"), allow_nan=False
    ).encode("ascii")


def _assumptions(value):
    if type(value) is not dict or set(value) != {"DAY", "NIGHT"}:
        raise ValueError("explicit DAY and NIGHT assumptions required")
    result = {}
    for scope in ("DAY", "NIGHT"):
        item = value[scope]
        if type(item) is not dict or set(item) != {
            "phase",
            "timezone",
            "local_date",
            "trading_day",
            "night_ownership",
        }:
            raise ValueError("invalid assumptions")
        if type(item["phase"]) is not str or item["phase"] not in _PHASES:
            raise ValueError("invalid phase")
        if type(item["night_ownership"]) is not str or item["night_ownership"] not in _OWNERSHIP:
            raise ValueError("invalid night ownership")
        if scope == "DAY" and item["night_ownership"] != "UNKNOWN":
            raise ValueError("day has no night ownership")
        zone = item["timezone"]
        if type(zone) is not str or len(zone) > 64 or not _ZONE.fullmatch(zone):
            raise ValueError("invalid timezone")
        if zone != "UNKNOWN":
            try:
                ZoneInfo(zone)
            except (ZoneInfoNotFoundError, ValueError):
                raise ValueError("invalid timezone") from None
        for key in ("local_date", "trading_day"):
            val = item[key]
            if type(val) is not str:
                raise ValueError("invalid date")
            if val != "UNKNOWN":
                try:
                    if date.fromisoformat(val).isoformat() != val:
                        raise ValueError
                except ValueError:
                    raise ValueError("invalid date") from None
        local, trading = item["local_date"], item["trading_day"]
        if local != "UNKNOWN" and trading != "UNKNOWN":
            ownership = item["night_ownership"]
            if ((scope == "DAY" or ownership == "SAME_TRADING_DAY") and trading != local) or (
                scope == "NIGHT" and ownership == "NEXT_TRADING_DAY" and trading <= local
            ):
                raise ValueError("contradictory date assumptions")
        result[scope] = dict(item)
    return result


def _assumed(value):
    return {"value": value, "basis": "UNKNOWN" if value == "UNKNOWN" else "MOCK_ASSUMPTION"}


def _window(item, assumptions):
    is_mock = item["source_type"] == "MOCK"
    basis = "MOCK_ASSUMPTION" if is_mock else "AKSHARE_SYNTAX_CANDIDATE"
    sim = assumptions[item["scope"]]
    phase = item["phase"] if is_mock else sim["phase"]
    transition = item["end_day_offset"] if is_mock else item["day_transition"]
    return {
        "source_type": "MOCK" if is_mock else "AKSHARE",
        "fields": {key: {"value": item[key], "basis": basis} for key in ("scope", "start", "end")}
        | {"day_transition": {"value": transition, "basis": basis}},
        "observation": dict.fromkeys(
            ("phase", "identity", "effective_period", "trading_day", "coverage"), "UNKNOWN"
        ),
        "simulation": {
            "phase": _assumed(phase),
            **{
                key: _assumed(sim[key])
                for key in ("timezone", "local_date", "trading_day", "night_ownership")
            },
        },
        "use": item["use"],
    }


def build(*, root, observation_id, run_id, filesystem, assumptions, fallback=None):
    """Build a detached record from retained observation IDs and explicit assumptions.

    Each scope declares phase, IANA timezone, local_date, trading_day and
    night_ownership; each can explicitly be UNKNOWN. DAY night_ownership must
    be UNKNOWN. No dates, phase or ownership are inferred. ``fallback`` follows
    the projection contract, including whole-scope-only MISSING_SCOPE behavior.
    """
    assumptions = _assumptions(assumptions)
    result = projection.project(
        root=root,
        observation_id=observation_id,
        run_id=run_id,
        filesystem=filesystem,
        fallback=fallback,
    )
    sem = result["semantic"]
    # These describe the collection attempt, rather than calendar contents.
    # Diagnostics, windows, conflicts and their unknowns remain semantic.
    trace = {
        **result["trace"],
        "requested_date": sem["request"].pop("requested_date"),
        "source_status": sem.pop("source_status"),
        "observation_state": sem.pop("observation_state"),
        "fallback": deepcopy(fallback),
        "assumptions": assumptions,
    }
    record = {
        "schema": _EVIDENCE_SCHEMA,
        "semantic": {
            "schema": SCHEMA,
            "environment": "SIMULATION_ONLY",
            "source_authentication": "NOT_PROVEN",
            "account_use": "NOT_AUTHORIZED",
            "projection": sem,
            "windows": [_window(item, assumptions) for item in sem["windows"]],
        },
        "trace": trace,
    }
    # Reject non-JSON values even if an upstream validation changes later.
    _canonical(record)
    return record


def validate(record, *, root, filesystem):
    """Check replay consistency against the retained observation and declarations.

    This checks content and local sidecar consistency, not source authenticity,
    chronology, account permission, or protection from a hostile local writer.
    A consistently replaced declaration and its dependent fields may still pass;
    use a previously fixed evidence hash to detect such replacement.
    Unsupported/old schemas are rejected and never upgraded implicitly.
    """
    try:
        if (
            type(record) is not dict
            or set(record) != {"schema", "semantic", "trace"}
            or record["schema"] != _EVIDENCE_SCHEMA
            or type(record["semantic"]) is not dict
            or record["semantic"].get("schema") != SCHEMA
            or type(record["trace"]) is not dict
        ):
            raise ValueError
        replay = build(
            root=root,
            filesystem=filesystem,
            observation_id=record["trace"]["observation_id"],
            run_id=record["trace"]["run_id"],
            assumptions=record["trace"]["assumptions"],
            fallback=record["trace"]["fallback"],
        )
        if _canonical(record) != _canonical(replay):
            raise ValueError
    except Exception:
        raise ValueError("invalid or changed mixed simulation evidence") from None
    return True


def semantic_digest(record, *, root, filesystem):
    """Return SHA-256 of canonical semantic content after replay validation."""
    validate(record, root=root, filesystem=filesystem)
    return hashlib.sha256(_canonical(record["semantic"])).hexdigest()


def evidence_digest(record, *, root, filesystem):
    """Bind semantic content and trace; this hash does not authenticate either."""
    validate(record, root=root, filesystem=filesystem)
    return hashlib.sha256(_canonical(record)).hexdigest()


def verify_evidence_digest(record, *, root, filesystem, expected_evidence_sha256):
    """Replay and compare with a previously fixed evidence SHA-256.

    A matching hash binds the exact supplied evidence but authenticates neither
    its source nor its writer. Later review/version storage must bind the
    previously fixed digest separately.
    """
    if type(expected_evidence_sha256) is not str or not re.fullmatch(
        r"[0-9a-f]{64}", expected_evidence_sha256, re.ASCII
    ):
        raise ValueError("invalid expected evidence digest")
    actual = evidence_digest(record, root=root, filesystem=filesystem)
    if not hmac.compare_digest(actual, expected_evidence_sha256):
        raise ValueError("evidence digest mismatch")
    return True


def diff(before, after, *, before_root, after_root, filesystem):
    """Return deterministic semantic JSON-pointer changes after both replays."""
    validate(before, root=before_root, filesystem=filesystem)
    validate(after, root=after_root, filesystem=filesystem)
    return _semantic_changes(before["semantic"], after["semantic"])


def _semantic_changes(before, after):
    changes = []

    def visit(left, right, path):
        if type(left) is dict and type(right) is dict:
            for key in sorted(left.keys() | right.keys()):
                child = path + "/" + key.replace("~", "~0").replace("/", "~1")
                if key not in left or key not in right:
                    changes.append(
                        {
                            "path": child,
                            "before": left.get(key),
                            "after": right.get(key),
                            "before_present": key in left,
                            "after_present": key in right,
                        }
                    )
                else:
                    visit(left[key], right[key], child)
        elif type(left) is list and type(right) is list and len(left) == len(right):
            for index, (a, b) in enumerate(zip(left, right, strict=False)):
                visit(a, b, path + "/" + str(index))
        elif _canonical(left) != _canonical(right):
            changes.append(
                {
                    "path": path,
                    "before": left,
                    "after": right,
                    "before_present": True,
                    "after_present": True,
                }
            )

    visit(before, after, "")
    return changes


_TEMPLATE = "bt_api.mixed-calendar-template/1"
_TEMPLATE_EVIDENCE = "bt_api.mixed-calendar-template-evidence/1"


def _template_input(target, assumptions):
    from datetime import timedelta

    if type(target) is not dict or set(target) != {
        "venue",
        "kind",
        "product",
        "mock_contract",
        "valid_from",
        "valid_to",
    }:
        raise ValueError("invalid template scope")
    if (
        type(target["venue"]) is not str
        or target["venue"] not in {"SHFE", "INE", "DCE", "CZCE", "CFFEX", "GFEX"}
        or type(target["kind"]) is not str
        or target["kind"] not in {"future", "option"}
        or any(
            type(target[k]) is not str
            or not re.fullmatch(r"[A-Za-z0-9_.-]{1,127}", target[k], re.ASCII)
            for k in ("product", "mock_contract")
        )
        or not target["mock_contract"].startswith("mock_")
    ):
        raise ValueError("invalid template scope")
    if target["kind"] != "future":
        raise ValueError("futures observation cannot support an option template")
    try:
        start, end = (date.fromisoformat(target[k]) for k in ("valid_from", "valid_to"))
        if (
            start.isoformat() != target["valid_from"]
            or end.isoformat() != target["valid_to"]
            or end < start
            or end - start > timedelta(days=731)
        ):
            raise ValueError
    except (ValueError, TypeError):
        raise ValueError("invalid template period") from None
    if type(assumptions) is not dict or set(assumptions) != {"DAY", "NIGHT"}:
        raise ValueError("invalid template assumptions")
    converted = {}
    for scope in ("DAY", "NIGHT"):
        item = assumptions[scope]
        if type(item) is not dict or set(item) != {"phase", "timezone"}:
            raise ValueError("invalid template assumptions")
        converted[scope] = {
            **item,
            "local_date": "UNKNOWN",
            "trading_day": "UNKNOWN",
            "night_ownership": "UNKNOWN",
        }
    return _assumptions(converted)


def _template_candidate(*, candidate_root, candidate_id, filesystem, target):
    from bt_api_py import trading_calendar_review_bundle as review

    shown = review.show(root=candidate_root, candidate_id=candidate_id, filesystem=filesystem)
    if shown.get("status") != "VISIBLE_UNAUTHENTICATED":
        raise ValueError("candidate unavailable")
    bundle = shown["bundle"]
    report = bundle["report"]
    if (
        bundle["schema"]
        not in {"bt_api.calendar-review-bundle/2", "bt_api.calendar-review-bundle/4"}
        or bundle["input"].get("metadata")
        != {"data_origin": "MOCK", "environment": "SIMULATION_ONLY"}
        or report["status"] != "STRUCTURALLY_READY_UNVERIFIED"
        or report["source_authentication"] != "NOT_PROVEN"
    ):
        raise ValueError("candidate not ready mock")
    shards = [s for s in bundle["input"]["shards"] if s["venue"] == target["venue"]]
    if len(shards) != 1:
        raise ValueError("ambiguous venue")
    shard = shards[0]
    start, end = target["valid_from"], target["valid_to"]
    if not (shard["coverage_start"] <= start <= end <= shard["coverage_end"]):
        raise ValueError("outside candidate coverage")
    layers = shard["layers"]
    rows = [
        r
        for r in layers["trading_hours"]
        if r["venue"] == target["venue"]
        and target["mock_contract"] in r["contracts"]
        and r["valid_from"] <= end
        and start <= r["valid_to"]
    ]
    if len(rows) != 1:
        raise ValueError("ambiguous contract scope")
    row = rows[0]
    if (
        row["kind"] != target["kind"]
        or row["product"] != target["product"]
        or not row["valid_from"] <= start <= end <= row["valid_to"]
    ):
        raise ValueError("scope or period unavailable")
    days = sorted(
        (deepcopy(r) for r in layers["trading_days"] if start <= r["day"] <= end),
        key=lambda r: r["day"],
    )
    mappings = sorted(
        (deepcopy(r) for r in row["night_mappings"] if start <= r["trading_day"] <= end),
        key=lambda r: r["trading_day"],
    )
    exceptions = sorted(
        (
            deepcopy(r)
            for r in layers["exceptions"]
            if r["kind"] == target["kind"]
            and r["product"] == target["product"]
            and r["contract"] in (None, target["mock_contract"])
            and start <= r["trading_day"] <= end
        ),
        key=_canonical,
    )
    # Preserve only source declarations actually used by the selected scope.
    used = set(row["source_ids"])
    for item in days + mappings + exceptions:
        used.update(item["source_ids"])
    sources = sorted(
        (deepcopy(s) for s in layers["sources"] if s["id"] in used), key=lambda s: s["id"]
    )
    if len(sources) != len(used) or any(
        not (s["id"].startswith("MOCK.") or s["id"].startswith("MOCK_"))
        or "MOCK" not in s["description"].upper()
        for s in sources
    ):
        raise ValueError("selected sources not explicitly mock")
    return shown, row, days, mappings, exceptions, sources


def build_template(
    *, root, candidate_root, observation_id, run_id, candidate_id, filesystem, target, assumptions
):
    """Build a two-year-at-most simulation template from two retained inputs.

    Candidate and observation are independently reread. A missing or ambiguous
    candidate scope is rejected. Missing/contradictory mapping or unmatched
    exceptions remain NEEDS_REVIEW. This futures-observation entry rejects option
    targets. Product/contract mapping is a simulation assumption; date alignment
    remains UNKNOWN. This grants no account authority.
    """
    from datetime import timedelta

    normalized = _template_input(target, assumptions)
    shown, row, days, mappings, exceptions, sources = _template_candidate(
        candidate_root=candidate_root,
        candidate_id=candidate_id,
        filesystem=filesystem,
        target=target,
    )
    initial = projection.project(
        root=root, observation_id=observation_id, run_id=run_id, filesystem=filesystem
    )
    request = initial["semantic"]["request"]
    if request["venue"] != target["venue"]:
        raise ValueError("observation venue mismatch")
    windows = [
        {**deepcopy(w), "scope": scope}
        for scope, key in (("DAY", "day_windows"), ("NIGHT", "night_windows"))
        for w in row[key]
    ]
    fallback = (
        {
            "data_origin": "MOCK",
            "environment": "SIMULATION_ONLY",
            "mode": "MISSING_SCOPE",
            "requested_venue": target["venue"],
            "requested_contract": request["contract"],
            "mock_contract": target["mock_contract"],
            "windows": windows,
        }
        if windows
        else None
    )
    projected = projection.project(
        root=root,
        observation_id=observation_id,
        run_id=run_id,
        filesystem=filesystem,
        fallback=fallback,
    )
    sem = projected["semantic"]
    trace = {
        **projected["trace"],
        "requested_date": sem["request"].pop("requested_date"),
        "source_status": sem.pop("source_status"),
        "observation_state": sem.pop("observation_state"),
        "candidate_id": candidate_id,
        "candidate_sha256": shown["payload_sha256"],
        "target": deepcopy(target),
        "assumptions": deepcopy(assumptions),
    }
    start, end = date.fromisoformat(target["valid_from"]), date.fromisoformat(target["valid_to"])
    expected_days = {(start + timedelta(days=i)).isoformat() for i in range((end - start).days + 1)}
    open_days = {r["day"] for r in days if r["status"] == "open"}
    mapped = [r["trading_day"] for r in mappings]
    issues = []
    if len(days) != len(expected_days) or {r["day"] for r in days} != expected_days:
        issues.append("MISSING_OR_DUPLICATE_DAYS")
    if len(mapped) != len(set(mapped)) or set(mapped) != open_days:
        issues.append("MISSING_OR_INVALID_NIGHT_MAPPINGS")
    if not row["windows_complete"] or not row["exceptions_complete"]:
        issues.append("INCOMPLETE_MOCK_DECLARATION")
    used_mock_ids = {w["id"] for w in sem["windows"] if w["source_type"] == "MOCK"}
    if any(
        e["action"] == "cancel_window" and e["window_id"] not in used_mock_ids for e in exceptions
    ):
        issues.append("EXCEPTION_WINDOW_UNMATCHED")
    if sem["conflicts"] or sem["diagnostic"] is not None:
        issues.append("OBSERVATION_REVIEW_REQUIRED")
    if sem["date_alignment"] == "UNKNOWN":
        issues.append("DATE_ALIGNMENT_UNKNOWN")
    if any(
        field["basis"] == "UNKNOWN"
        for item in (_window(window, normalized) for window in sem["windows"])
        for name, field in item["simulation"].items()
        if name in {"phase", "timezone"}
    ):
        issues.append("SIMULATION_ASSUMPTION_UNKNOWN")
    return {
        "schema": _TEMPLATE_EVIDENCE,
        "semantic": {
            "schema": _TEMPLATE,
            "environment": "SIMULATION_ONLY",
            "source_authentication": "NOT_PROVEN",
            "account_use": "NOT_AUTHORIZED",
            "scope": deepcopy(target),
            "identity_binding": {
                "observation_contract_identity": "UNKNOWN",
                "observation_product_identity": "UNKNOWN",
                "target_product_to_observation_contract": "MOCK_ASSUMPTION",
            },
            "mock_calendar_basis": "MOCK_ASSUMPTION",
            "projection": sem,
            "windows": [_window(w, normalized) for w in sem["windows"]],
            "trading_days": days,
            "night_mappings": mappings,
            "exceptions": exceptions,
            "mock_sources": sources,
            "mock_window_source_ids": sorted(row["source_ids"]) if used_mock_ids else [],
            "mock_completeness": {
                "windows": row["windows_complete"],
                "exceptions": row["exceptions_complete"],
            },
            "issues": issues,
            "status": "NEEDS_REVIEW" if issues else "SIMULATION_TEMPLATE_ONLY",
        },
        "trace": trace,
    }


def validate_template(record, *, root, candidate_root, filesystem):
    """Replay both retained IDs and compare the complete simulation evidence."""
    try:
        if (
            type(record) is not dict
            or set(record) != {"schema", "semantic", "trace"}
            or record["schema"] != _TEMPLATE_EVIDENCE
            or record["semantic"]["schema"] != _TEMPLATE
        ):
            raise ValueError
        trace = record["trace"]
        replay = build_template(
            root=root,
            candidate_root=candidate_root,
            filesystem=filesystem,
            observation_id=trace["observation_id"],
            run_id=trace["run_id"],
            candidate_id=trace["candidate_id"],
            target=trace["target"],
            assumptions=trace["assumptions"],
        )
        if _canonical(record) != _canonical(replay):
            raise ValueError
    except Exception:
        raise ValueError("invalid or changed template evidence") from None
    return True


def template_digest(record, *, root, candidate_root, filesystem, include_trace=False):
    """Digest replayed semantic content or, when requested, full evidence."""
    if type(include_trace) is not bool:
        raise ValueError("invalid digest mode")
    validate_template(record, root=root, candidate_root=candidate_root, filesystem=filesystem)
    return hashlib.sha256(_canonical(record if include_trace else record["semantic"])).hexdigest()


def compare_templates(before, after, *, root, candidate_root, filesystem):
    """Return semantic equality after both original inputs are revalidated."""
    before_digest = template_digest(
        before, root=root, candidate_root=candidate_root, filesystem=filesystem
    )
    after_digest = template_digest(
        after, root=root, candidate_root=candidate_root, filesystem=filesystem
    )
    return {
        "status": "SAME" if before_digest == after_digest else "CHANGED",
        "before_sha256": before_digest,
        "after_sha256": after_digest,
    }


def diff_templates(before, after, *, root, candidate_root, filesystem):
    """Return detailed semantic changes after revalidating both inputs."""
    validate_template(before, root=root, candidate_root=candidate_root, filesystem=filesystem)
    validate_template(after, root=root, candidate_root=candidate_root, filesystem=filesystem)
    return _semantic_changes(before["semantic"], after["semantic"])


def verify_template_evidence_digest(
    record, *, root, candidate_root, filesystem, expected_evidence_sha256
):
    """Compare replayed full evidence to a previously fixed, unauthenticated hash."""
    if type(expected_evidence_sha256) is not str or not re.fullmatch(
        r"[0-9a-f]{64}", expected_evidence_sha256, re.ASCII
    ):
        raise ValueError("invalid expected evidence digest")
    actual = template_digest(
        record, root=root, candidate_root=candidate_root, filesystem=filesystem, include_trace=True
    )
    if not hmac.compare_digest(actual, expected_evidence_sha256):
        raise ValueError("evidence digest mismatch")
    return True
