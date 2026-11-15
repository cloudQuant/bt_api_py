"""Detached, bounded comparison of caller-declared MOCK calendars.

The supplied snapshot factory is trusted to validate venue semantics, exception
application and overlaps. Its results, sources, identities and coverage are not
authenticated. This module neither reads CURRENT nor authorizes publication;
its /3 comparison is a standalone contract, not Store policy evidence.
Each input explicitly declares data_origin=MOCK and simulation_provenance=None;
these caller declarations do not establish origin.
Callers must not mutate inputs during their initial copy; that copy is not an
atomic cross-object snapshot. Factory checks do not defend against a malicious
provider that fabricates validation or conceals its changes.
"""

from __future__ import annotations

from copy import deepcopy
from datetime import date, timedelta
from hashlib import sha256

from bt_api_py.trading_calendar_full_diff import _changes
from bt_api_py.trading_calendar_review_bundle import _bounded
from bt_api_py.trading_calendar_snapshot_budget import check_snapshot_budget
from bt_api_py.trading_calendar_store import _canonical

MOCK = "MOCK.simulation_only"
_LAYERS = ("sources", "trading_days", "trading_hours", "exceptions")
_EXCEPTION = {
    "venue",
    "kind",
    "product",
    "contract",
    "trading_day",
    "action",
    "window_id",
    "source_ids",
}
_EXTRA = {"exception_schema", "start_utc", "end_utc"}
_SCHEMA = "bt_api.calendar-window-interruption/1"
_MAX_DAYS = 10000
_MAX_CELLS = 150000


def _date(value):
    if type(value) is not str:
        raise ValueError("date")
    result = date.fromisoformat(value)
    if result.isoformat() != value:
        raise ValueError("date")
    return result


def _inputs(content, *, allow_conflicting_schema=False):
    _bounded(content)
    if type(content) is not dict or set(content) != set(_LAYERS):
        raise ValueError("layers")
    if any(type(content[key]) is not list for key in _LAYERS):
        raise ValueError("layers")
    if len(content["sources"]) != 1 or type(content["sources"][0]) is not dict:
        raise ValueError("sources")
    source = content["sources"][0]
    if (
        set(source) != {"id", "description"}
        or source["id"] != MOCK
        or type(source["description"]) is not str
        or not source["description"].strip()
    ):
        raise ValueError("sources")

    def refs(value):
        if type(value) is dict:
            for key, item in value.items():
                if key == "source_ids" and item != [MOCK]:
                    raise ValueError("source references")
                refs(item)
        elif type(value) is list:
            for item in value:
                refs(item)

    refs(content)
    for row in content["exceptions"]:
        if type(row) is not dict or type(row.get("action")) is not str:
            raise ValueError("exception")
        action = row["action"]
        if action == "interrupt_window":
            if set(row) != _EXCEPTION | _EXTRA or (
                not allow_conflicting_schema and row["exception_schema"] != _SCHEMA
            ):
                raise ValueError("exception")
        elif action not in {"close_day", "cancel_window"} or set(row) != _EXCEPTION:
            raise ValueError("exception")
    return deepcopy(content)


def _coverage(rows):
    if type(rows) is not list or not rows or len(rows) > 6:
        raise ValueError("coverage")
    result = {}
    for row in rows:
        if (
            type(row) is not dict
            or set(row) != {"venue", "coverage_start", "coverage_end", "semantics"}
            or row["semantics"] != "DECLARED_INCLUSIVE"
        ):
            raise ValueError("coverage")
        venue = row["venue"]
        if type(venue) is not str or not venue or venue in result:
            raise ValueError("coverage")
        start, end = _date(row["coverage_start"]), _date(row["coverage_end"])
        if end < start or (end - start).days + 1 > _MAX_DAYS:
            raise ValueError("coverage")
        result[venue] = (start, end)
    return result


def _validated(value, factory):
    _bounded(value)
    if (
        type(value) is not dict
        or set(value) != {"content", "coverage", "data_origin", "simulation_provenance"}
        or value["data_origin"] != "MOCK"
        or value["simulation_provenance"] is not None
    ):
        raise ValueError("declaration")
    content = _inputs(value["content"])
    coverage = deepcopy(value["coverage"])
    spans = _coverage(coverage)
    if {row["venue"] for row in content["trading_days"]} != set(spans):
        raise ValueError("venues")
    days = {}
    for row in content["trading_days"]:
        key = (row["venue"], _date(row["day"]))
        if key in days or key[0] not in spans or not spans[key[0]][0] <= key[1] <= spans[key[0]][1]:
            raise ValueError("day")
        days[key] = row["status"]
    if sum((b - a).days + 1 for a, b in spans.values()) != len(days):
        raise ValueError("missing dates")
    if any(day not in {"open", "closed"} for day in days.values()):
        raise ValueError("day")
    scopes = set()
    cells = 0
    for row in content["trading_hours"]:
        venue = row["venue"]
        if (
            venue not in spans
            or row["windows_complete"] is not True
            or row["exceptions_complete"] is not True
        ):
            raise ValueError("scope")
        first, last = _date(row["valid_from"]), _date(row["valid_to"])
        if first < spans[venue][0] or last > spans[venue][1] or last < first:
            raise ValueError("scope")
        for mapping in row["night_mappings"]:
            owner = _date(mapping["trading_day"])
            natural = mapping["start_date"]
            if not first <= owner <= last or (
                natural is not None and not spans[venue][0] <= _date(natural) < owner
            ):
                raise ValueError("owner outside declared coverage")
        if type(row["contracts"]) is not list or not row["contracts"]:
            raise ValueError("scope")
        for contract in row["contracts"]:
            if type(contract) is not str or not contract.startswith("mock_"):
                raise ValueError("scope")
            for offset in range((last - first).days + 1):
                key = (venue, row["kind"], row["product"], contract, first + timedelta(days=offset))
                if key in scopes:
                    raise ValueError("duplicate scope")
                scopes.add(key)
                cells += 1
                if cells > _MAX_CELLS:
                    raise ValueError("scope budget")
    if not scopes or {scope[0] for scope in scopes} != set(spans):
        raise ValueError("empty scope")
    scope_days = {}
    for venue, kind, product, contract, day in scopes:
        scope_days.setdefault((venue, kind, product, day), set()).add(contract)
    for row in content["exceptions"]:
        owner = _date(row["trading_day"])
        venue = row["venue"]
        key = (venue, row["kind"], row["product"], owner)
        contracts = scope_days.get(key, set())
        if (
            venue not in spans
            or days.get((venue, owner)) != "open"
            or not contracts
            or (row["contract"] is not None and row["contract"] not in contracts)
        ):
            raise ValueError("unmatched exception scope or owner")
    check_snapshot_budget(content, set(spans))
    if not callable(factory):
        raise ValueError("factory")
    for venue in spans:
        shard = {"sources": deepcopy(content["sources"])}
        for layer in _LAYERS[1:]:
            shard[layer] = [deepcopy(row) for row in content[layer] if row["venue"] == venue]
        shard_bytes = _canonical(shard)
        snapshot = factory(shard, calendar_ref="simulation-interruption-check")
        _bounded(shard)
        if _canonical(shard) != shard_bytes:
            raise ValueError("factory changed shard")
        for (v, day), day_status in days.items():
            if v != venue:
                continue
            result = snapshot.query_venue_day(venue=venue, trading_day=day)
            if (
                result.status,
                result.calendar_ref,
                result.venue,
                result.trading_day,
                result.day_status,
                result.source_ids,
                result.sources_verified,
            ) != (
                "COMPLETE",
                "simulation-interruption-check",
                venue,
                day,
                day_status,
                (MOCK,),
                False,
            ):
                raise ValueError("day query")
        for v, kind, product, contract, day in scopes:
            if v != venue:
                continue
            result = snapshot.query(
                venue=v, kind=kind, product=product, contract=contract, trading_day=day
            )
            if (
                result.status,
                result.calendar_ref,
                result.venue,
                result.kind,
                result.product,
                result.contract,
                result.trading_day,
                result.source_ids,
                result.sources_verified,
            ) != (
                "COMPLETE",
                "simulation-interruption-check",
                v,
                kind,
                product,
                contract,
                day,
                (MOCK,),
                False,
            ):
                raise ValueError("scope query")
        _bounded(shard)
        if _canonical(shard) != shard_bytes:
            raise ValueError("provider changed shard")
    return {**content, "coverage": coverage}, frozenset(scopes), spans


def _labels(status):
    return {
        "status": status,
        "mode": "SIMULATION_ONLY",
        "source_authentication": "NOT_PROVEN",
        "identity_authentication": "NOT_PROVEN",
        "coverage": "CALLER_DECLARED",
        "publication": "NOT_AUTHORIZED",
    }


def compare_simulated_interruption(before, after, *, mode, snapshot_factory):
    """Compare declarations; None before is a caller-declared empty baseline only."""
    try:
        if mode != "SIMULATION_ONLY":
            raise ValueError("mode")
        _bounded(before)
        _bounded(after)
        before, after = deepcopy(before), deepcopy(after)
        right, right_scopes, right_spans = _validated(after, snapshot_factory)
        if before is None:
            left = {key: [] for key in (*_LAYERS, "coverage")}
        else:
            left, left_scopes, left_spans = _validated(before, snapshot_factory)
            if left_scopes != right_scopes or left_spans != right_spans:
                raise ValueError("different scope or coverage")
        changes = _changes(left, right)
        result = _labels("CHANGED" if any(changes.values()) else "SAME")
        result.update(
            {
                "schema": "bt_api.calendar-full-declaration-difference/3",
                "baseline": "CALLER_DECLARED_ABSENT" if before is None else "CALLER_DECLARED",
                "before_sha256": None if before is None else sha256(_canonical(left)).hexdigest(),
                "after_sha256": sha256(_canonical(right)).hexdigest(),
                "changes": changes,
            }
        )
        if len(_canonical(result)) > 16 * 1024 * 1024:
            raise ValueError("output budget")
        return result
    except Exception:
        return {**_labels("UNKNOWN"), "reason": "DECLARATION_UNAVAILABLE"}


def merge_simulated_interruption(
    observed, supplement, *, coverage, mode, snapshot_factory, data_origin, simulation_provenance
):
    """Merge complete rows by identity; conflicting declarations never coalesce."""
    try:
        if mode != "SIMULATION_ONLY" or data_origin != "MOCK" or simulation_provenance is not None:
            raise ValueError("mode")
        _bounded(observed)
        _bounded(supplement)
        _bounded(coverage)
        observed, supplement, coverage = deepcopy((observed, supplement, coverage))
        left = _inputs(observed, allow_conflicting_schema=True)
        right = _inputs(supplement, allow_conflicting_schema=True)
        keys = {
            "sources": ("id",),
            "trading_days": ("venue", "day"),
            "trading_hours": ("venue", "kind", "product", "contracts", "valid_from", "valid_to"),
            "exceptions": (
                "venue",
                "kind",
                "product",
                "contract",
                "trading_day",
                "action",
                "window_id",
            ),
        }
        for layer in _LAYERS:
            index = {}
            for row in left[layer]:
                key = tuple(tuple(row[k]) if k == "contracts" else row[k] for k in keys[layer])
                if key in index:
                    raise ValueError("duplicate")
                index[key] = row
            incoming = set()
            for row in right[layer]:
                key = tuple(tuple(row[k]) if k == "contracts" else row[k] for k in keys[layer])
                if key in incoming:
                    raise ValueError("duplicate")
                incoming.add(key)
                if key in index and index[key] != row:
                    return {**_labels("CONFLICT"), "reason": "DECLARATION_CONFLICT"}
                if key not in index:
                    left[layer].append(row)
                    index[key] = row
        normalized, _, _ = _validated(
            {
                "content": left,
                "coverage": coverage,
                "data_origin": data_origin,
                "simulation_provenance": simulation_provenance,
            },
            snapshot_factory,
        )
        return {
            **_labels("READY"),
            "content": {key: normalized[key] for key in _LAYERS},
            "coverage_declaration": normalized["coverage"],
        }
    except Exception:
        return {**_labels("UNKNOWN"), "reason": "DECLARATION_UNAVAILABLE"}
