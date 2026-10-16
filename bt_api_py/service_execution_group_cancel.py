"""Durable cancel-plan identity for service-coordinated execution groups.

The plan rows share the coordinator's existing SQLite connection and lock.
They reserve each child cancel id before any account-owner RPC, so an
ambiguous request can only be queried under that same id and can never be
redispatched by replaying a group cancel.
"""

from __future__ import annotations

import hashlib
import json
import math
import re
import time
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from types import MappingProxyType
from typing import Any

_REF = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:-]{0,127}$")
_PHASES = frozenset({"PLANNED", "DISPATCHING", "UNKNOWN", "OBSERVED"})
_LEG_KEYS = frozenset(
    {"leg_id", "account_ref", "cancel_id", "target_intent_id", "provider_order_id"}
)


class GroupCancelPlanError(RuntimeError):
    """Finite persistence or identity failure for one accepted group cancel."""


@dataclass(frozen=True, slots=True)
class GroupCancelPlanLeg:
    leg_id: str
    account_ref: str
    cancel_id: str
    target_intent_id: str
    provider_order_id: str
    phase: str
    result: Mapping[str, Any] | None
    updated_at_ns: int


@dataclass(frozen=True, slots=True)
class GroupCancelPlan:
    portfolio_ref: str
    group_id: str
    cancel_request_id: str
    origin_run_id: str
    manifest_sha256: str
    created_at_ns: int
    updated_at_ns: int
    legs: tuple[GroupCancelPlanLeg, ...]


def group_cancel_leg_id(
    portfolio_ref: str,
    group_id: str,
    origin_run_id: str,
    leg_id: str,
    account_ref: str,
    execution_scope_key: str,
    target_intent_id: str,
    provider_order_id: str,
) -> str:
    """Derive one child cancellation id for the accepted group leg.

    The caller's request id is deliberately excluded.  Replaying the group
    route with a new request id must not create a second provider cancel for
    the same accepted child. The source run, exact Execution scope, source
    intent generation, and provider order are included so the id cannot be
    reused by a later group generation or a different account/order scope.
    """

    values = (
        portfolio_ref,
        group_id,
        origin_run_id,
        leg_id,
        account_ref,
        execution_scope_key,
        target_intent_id,
        provider_order_id,
    )
    if any(type(value) is not str or _REF.fullmatch(value) is None for value in values):
        raise GroupCancelPlanError("GROUP_CANCEL_IDENTITY_INVALID")
    raw = json.dumps(values, ensure_ascii=True, separators=(",", ":")).encode("ascii")
    return "gcancel-" + hashlib.sha256(raw).hexdigest()


class GroupCancelPlanStore:
    """Sidecar journal using one ``GroupOriginRunBindingStore`` transaction."""

    def __init__(self, run_bindings: object) -> None:
        if (
            not callable(getattr(run_bindings, "_transaction", None))
            or getattr(run_bindings, "_connection", None) is None
            or not hasattr(run_bindings, "_lock")
        ):
            raise GroupCancelPlanError("GROUP_CANCEL_STORE_INVALID")
        self._owner = run_bindings
        try:
            with run_bindings._transaction():
                run_bindings._connection.execute(
                    "CREATE TABLE IF NOT EXISTS coordinator_group_cancel_plans ("
                    "portfolio_ref TEXT NOT NULL, group_id TEXT NOT NULL, "
                    "cancel_request_id TEXT NOT NULL, origin_run_id TEXT NOT NULL, "
                    "manifest_json TEXT NOT NULL, manifest_sha256 TEXT NOT NULL, "
                    "created_at_ns INTEGER NOT NULL, updated_at_ns INTEGER NOT NULL, "
                    "PRIMARY KEY(portfolio_ref, group_id, cancel_request_id))"
                )
                run_bindings._connection.execute(
                    "CREATE TABLE IF NOT EXISTS coordinator_group_cancel_plan_legs ("
                    "portfolio_ref TEXT NOT NULL, group_id TEXT NOT NULL, "
                    "cancel_request_id TEXT NOT NULL, leg_id TEXT NOT NULL, "
                    "account_ref TEXT NOT NULL, cancel_id TEXT NOT NULL, "
                    "target_intent_id TEXT NOT NULL, provider_order_id TEXT NOT NULL, "
                    "phase TEXT NOT NULL CHECK(phase IN "
                    "('PLANNED','DISPATCHING','UNKNOWN','OBSERVED')), "
                    "result_json TEXT, updated_at_ns INTEGER NOT NULL, "
                    "PRIMARY KEY(portfolio_ref, group_id, cancel_request_id, leg_id), "
                    "FOREIGN KEY(portfolio_ref, group_id, cancel_request_id) REFERENCES "
                    "coordinator_group_cancel_plans(portfolio_ref, group_id, cancel_request_id))"
                )
                run_bindings._connection.execute(
                    "CREATE TABLE IF NOT EXISTS coordinator_group_cancel_child_claims ("
                    "cancel_id TEXT PRIMARY KEY, portfolio_ref TEXT NOT NULL, "
                    "group_id TEXT NOT NULL, origin_run_id TEXT NOT NULL, leg_id TEXT NOT NULL, "
                    "account_ref TEXT NOT NULL, target_intent_id TEXT NOT NULL, "
                    "provider_order_id TEXT NOT NULL, owner_cancel_request_id TEXT NOT NULL, "
                    "phase TEXT NOT NULL CHECK(phase IN "
                    "('PLANNED','DISPATCHING','UNKNOWN','OBSERVED')), "
                    "updated_at_ns INTEGER NOT NULL)"
                )
        except Exception:
            raise GroupCancelPlanError("GROUP_CANCEL_STORE_UNAVAILABLE") from None

    def create_or_get(
        self,
        *,
        portfolio_ref: str,
        group_id: str,
        cancel_request_id: str,
        origin_run_id: str,
        legs: Sequence[Mapping[str, object]],
    ) -> GroupCancelPlan:
        self._validate_refs(portfolio_ref, group_id, cancel_request_id, origin_run_id)
        manifest = self._validate_legs(legs)
        manifest_json = _canonical(manifest)
        manifest_sha256 = hashlib.sha256(manifest_json.encode("utf-8")).hexdigest()
        now_ns = time.time_ns()
        owner = self._owner
        try:
            with owner._transaction():
                row = owner._connection.execute(
                    "SELECT * FROM coordinator_group_cancel_plans "
                    "WHERE portfolio_ref=? AND group_id=? AND cancel_request_id=?",
                    (portfolio_ref, group_id, cancel_request_id),
                ).fetchone()
                if row is None:
                    owner._connection.execute(
                        "INSERT INTO coordinator_group_cancel_plans VALUES(?, ?, ?, ?, ?, ?, ?, ?)",
                        (
                            portfolio_ref,
                            group_id,
                            cancel_request_id,
                            origin_run_id,
                            manifest_json,
                            manifest_sha256,
                            now_ns,
                            now_ns,
                        ),
                    )
                    for leg in manifest:
                        owner._connection.execute(
                            "INSERT INTO coordinator_group_cancel_plan_legs "
                            "(portfolio_ref, group_id, cancel_request_id, leg_id, account_ref, cancel_id, "
                            "target_intent_id, provider_order_id, phase, result_json, updated_at_ns) "
                            "VALUES(?, ?, ?, ?, ?, ?, ?, ?, 'PLANNED', NULL, ?)",
                            (
                                portfolio_ref,
                                group_id,
                                cancel_request_id,
                                leg["leg_id"],
                                leg["account_ref"],
                                leg["cancel_id"],
                                leg["target_intent_id"],
                                leg["provider_order_id"],
                                now_ns,
                            ),
                        )
                        self._reserve_child_claim_locked(
                            portfolio_ref=portfolio_ref,
                            group_id=group_id,
                            origin_run_id=origin_run_id,
                            cancel_request_id=cancel_request_id,
                            leg=leg,
                            now_ns=now_ns,
                        )
                elif (
                    row["origin_run_id"] != origin_run_id
                    or row["manifest_json"] != manifest_json
                    or row["manifest_sha256"] != manifest_sha256
                ):
                    raise GroupCancelPlanError("GROUP_CANCEL_REQUEST_CONFLICT")
                else:
                    for leg in manifest:
                        self._validate_child_claim_locked(
                            portfolio_ref=portfolio_ref,
                            group_id=group_id,
                            origin_run_id=origin_run_id,
                            cancel_request_id=cancel_request_id,
                            leg=leg,
                        )
                return self._read_plan_locked(portfolio_ref, group_id, cancel_request_id)
        except GroupCancelPlanError:
            raise
        except Exception:
            raise GroupCancelPlanError("GROUP_CANCEL_STORE_UNAVAILABLE") from None

    def get(
        self, portfolio_ref: str, group_id: str, cancel_request_id: str
    ) -> GroupCancelPlan | None:
        self._validate_refs(portfolio_ref, group_id, cancel_request_id)
        owner = self._owner
        with owner._lock:
            if getattr(owner, "_closed", True):
                raise GroupCancelPlanError("GROUP_CANCEL_STORE_CLOSED")
            row = owner._connection.execute(
                "SELECT 1 FROM coordinator_group_cancel_plans "
                "WHERE portfolio_ref=? AND group_id=? AND cancel_request_id=?",
                (portfolio_ref, group_id, cancel_request_id),
            ).fetchone()
            return (
                None
                if row is None
                else self._read_plan_locked(portfolio_ref, group_id, cancel_request_id)
            )

    def claim_dispatch(
        self, portfolio_ref: str, group_id: str, cancel_request_id: str, leg_id: str
    ) -> bool:
        self._validate_refs(portfolio_ref, group_id, cancel_request_id, leg_id)
        owner = self._owner
        now_ns = time.time_ns()
        try:
            with owner._transaction():
                local = owner._connection.execute(
                    "SELECT cancel_id, phase FROM coordinator_group_cancel_plan_legs "
                    "WHERE portfolio_ref=? AND group_id=? AND cancel_request_id=? AND leg_id=?",
                    (portfolio_ref, group_id, cancel_request_id, leg_id),
                ).fetchone()
                if local is None:
                    raise GroupCancelPlanError("GROUP_CANCEL_PLAN_LEG_MISSING")
                if local["phase"] != "PLANNED":
                    return False
                claim = owner._connection.execute(
                    "SELECT owner_cancel_request_id, leg_id, phase "
                    "FROM coordinator_group_cancel_child_claims WHERE cancel_id=?",
                    (local["cancel_id"],),
                ).fetchone()
                if claim is None:
                    raise GroupCancelPlanError("GROUP_CANCEL_CHILD_CLAIM_MISSING")
                if (
                    claim["owner_cancel_request_id"] != cancel_request_id
                    or claim["leg_id"] != leg_id
                    or claim["phase"] != "PLANNED"
                ):
                    owner._connection.execute(
                        "UPDATE coordinator_group_cancel_plan_legs "
                        "SET phase='UNKNOWN', updated_at_ns=? "
                        "WHERE portfolio_ref=? AND group_id=? AND cancel_request_id=? "
                        "AND leg_id=? AND phase='PLANNED'",
                        (now_ns, portfolio_ref, group_id, cancel_request_id, leg_id),
                    )
                    return False
                claim_cursor = owner._connection.execute(
                    "UPDATE coordinator_group_cancel_child_claims "
                    "SET phase='DISPATCHING', updated_at_ns=? "
                    "WHERE cancel_id=? AND owner_cancel_request_id=? AND leg_id=? "
                    "AND phase='PLANNED'",
                    (now_ns, local["cancel_id"], cancel_request_id, leg_id),
                )
                if claim_cursor.rowcount != 1:
                    return False
                local_cursor = owner._connection.execute(
                    "UPDATE coordinator_group_cancel_plan_legs "
                    "SET phase='DISPATCHING', updated_at_ns=? "
                    "WHERE portfolio_ref=? AND group_id=? AND cancel_request_id=? "
                    "AND leg_id=? AND phase='PLANNED'",
                    (now_ns, portfolio_ref, group_id, cancel_request_id, leg_id),
                )
                if local_cursor.rowcount != 1:
                    raise GroupCancelPlanError("GROUP_CANCEL_PLAN_STATE_INVALID")
                return True
        except GroupCancelPlanError:
            raise
        except Exception:
            raise GroupCancelPlanError("GROUP_CANCEL_STORE_UNAVAILABLE") from None

    def mark_unknown(
        self,
        portfolio_ref: str,
        group_id: str,
        cancel_request_id: str,
        leg_id: str,
        result: Mapping[str, object] | None = None,
    ) -> GroupCancelPlanLeg:
        self._validate_refs(portfolio_ref, group_id, cancel_request_id, leg_id)
        result_json = None if result is None else _canonical(_json_value(result))
        owner = self._owner
        now_ns = time.time_ns()
        try:
            with owner._transaction():
                owner._connection.execute(
                    "UPDATE coordinator_group_cancel_plan_legs "
                    "SET phase='UNKNOWN', result_json=COALESCE(?, result_json), updated_at_ns=? "
                    "WHERE portfolio_ref=? AND group_id=? AND cancel_request_id=? AND leg_id=? "
                    "AND phase IN ('DISPATCHING','UNKNOWN')",
                    (result_json, now_ns, portfolio_ref, group_id, cancel_request_id, leg_id),
                )
                owner._connection.execute(
                    "UPDATE coordinator_group_cancel_child_claims "
                    "SET phase='UNKNOWN', updated_at_ns=? WHERE cancel_id=("
                    "SELECT cancel_id FROM coordinator_group_cancel_plan_legs "
                    "WHERE portfolio_ref=? AND group_id=? AND cancel_request_id=? AND leg_id=?"
                    ") AND phase IN ('DISPATCHING','UNKNOWN')",
                    (now_ns, portfolio_ref, group_id, cancel_request_id, leg_id),
                )
                owner._connection.execute(
                    "UPDATE coordinator_group_cancel_plans SET updated_at_ns=? "
                    "WHERE portfolio_ref=? AND group_id=? AND cancel_request_id=?",
                    (now_ns, portfolio_ref, group_id, cancel_request_id),
                )
                return self._read_leg_locked(
                    portfolio_ref, group_id, cancel_request_id, leg_id
                )
        except GroupCancelPlanError:
            raise
        except Exception:
            raise GroupCancelPlanError("GROUP_CANCEL_STORE_UNAVAILABLE") from None

    def record_result(
        self,
        portfolio_ref: str,
        group_id: str,
        cancel_request_id: str,
        leg_id: str,
        result: Mapping[str, object],
    ) -> GroupCancelPlanLeg:
        self._validate_refs(portfolio_ref, group_id, cancel_request_id, leg_id)
        normalized = _json_value(result)
        phase = _result_phase(normalized)
        result_json = _canonical(normalized)
        owner = self._owner
        now_ns = time.time_ns()
        try:
            with owner._transaction():
                current = self._read_leg_locked(
                    portfolio_ref, group_id, cancel_request_id, leg_id
                )
                if not _result_matches_leg(normalized, current):
                    raise GroupCancelPlanError("GROUP_CANCEL_RESULT_MISMATCH")
                old_state = _record_state(current.result)
                new_state = _record_state(normalized)
                if _record_terminal(current.result):
                    # A terminal result is immutable. In particular a stale
                    # UNKNOWN reply cannot erase cancellation evidence.
                    return current
                if _state_rank(new_state) < _state_rank(old_state):
                    return current
                owner._connection.execute(
                    "UPDATE coordinator_group_cancel_plan_legs "
                    "SET phase=?, result_json=?, updated_at_ns=? "
                    "WHERE portfolio_ref=? AND group_id=? AND cancel_request_id=? AND leg_id=?",
                    (phase, result_json, now_ns, portfolio_ref, group_id, cancel_request_id, leg_id),
                )
                claim_phase = "OBSERVED" if phase == "OBSERVED" else "UNKNOWN"
                owner._connection.execute(
                    "UPDATE coordinator_group_cancel_child_claims "
                    "SET phase=?, updated_at_ns=? WHERE cancel_id=("
                    "SELECT cancel_id FROM coordinator_group_cancel_plan_legs "
                    "WHERE portfolio_ref=? AND group_id=? AND cancel_request_id=? AND leg_id=?"
                    ") AND phase!='OBSERVED' AND (phase IN ('DISPATCHING','UNKNOWN') "
                    "OR (?='OBSERVED' AND phase='PLANNED'))",
                    (
                        claim_phase,
                        now_ns,
                        portfolio_ref,
                        group_id,
                        cancel_request_id,
                        leg_id,
                        phase,
                    ),
                )
                owner._connection.execute(
                    "UPDATE coordinator_group_cancel_plans SET updated_at_ns=? "
                    "WHERE portfolio_ref=? AND group_id=? AND cancel_request_id=?",
                    (now_ns, portfolio_ref, group_id, cancel_request_id),
                )
                return self._read_leg_locked(
                    portfolio_ref, group_id, cancel_request_id, leg_id
                )
        except GroupCancelPlanError:
            raise
        except Exception:
            raise GroupCancelPlanError("GROUP_CANCEL_STORE_UNAVAILABLE") from None

    def _reserve_child_claim_locked(
        self,
        *,
        portfolio_ref: str,
        group_id: str,
        origin_run_id: str,
        cancel_request_id: str,
        leg: Mapping[str, str],
        now_ns: int,
    ) -> None:
        owner = self._owner
        owner._connection.execute(
            "INSERT OR IGNORE INTO coordinator_group_cancel_child_claims "
            "(cancel_id, portfolio_ref, group_id, origin_run_id, leg_id, account_ref, "
            "target_intent_id, provider_order_id, owner_cancel_request_id, phase, updated_at_ns) "
            "VALUES(?, ?, ?, ?, ?, ?, ?, ?, ?, 'PLANNED', ?)",
            (
                leg["cancel_id"],
                portfolio_ref,
                group_id,
                origin_run_id,
                leg["leg_id"],
                leg["account_ref"],
                leg["target_intent_id"],
                leg["provider_order_id"],
                cancel_request_id,
                now_ns,
            ),
        )
        self._validate_child_claim_locked(
            portfolio_ref=portfolio_ref,
            group_id=group_id,
            origin_run_id=origin_run_id,
            cancel_request_id=cancel_request_id,
            leg=leg,
        )

    def _validate_child_claim_locked(
        self,
        *,
        portfolio_ref: str,
        group_id: str,
        origin_run_id: str,
        cancel_request_id: str,
        leg: Mapping[str, str],
    ) -> None:
        row = self._owner._connection.execute(
            "SELECT portfolio_ref, group_id, origin_run_id, leg_id, account_ref, "
            "target_intent_id, provider_order_id FROM coordinator_group_cancel_child_claims "
            "WHERE cancel_id=?",
            (leg["cancel_id"],),
        ).fetchone()
        if row is None or tuple(row) != (
            portfolio_ref,
            group_id,
            origin_run_id,
            leg["leg_id"],
            leg["account_ref"],
            leg["target_intent_id"],
            leg["provider_order_id"],
        ):
            raise GroupCancelPlanError("GROUP_CANCEL_CHILD_CLAIM_CONFLICT")

    def _read_plan_locked(
        self, portfolio_ref: str, group_id: str, cancel_request_id: str
    ) -> GroupCancelPlan:
        owner = self._owner
        row = owner._connection.execute(
            "SELECT * FROM coordinator_group_cancel_plans "
            "WHERE portfolio_ref=? AND group_id=? AND cancel_request_id=?",
            (portfolio_ref, group_id, cancel_request_id),
        ).fetchone()
        if row is None:
            raise GroupCancelPlanError("GROUP_CANCEL_PLAN_MISSING")
        try:
            manifest = json.loads(row["manifest_json"])
            if (
                type(manifest) is not list
                or _canonical(manifest) != row["manifest_json"]
                or hashlib.sha256(row["manifest_json"].encode("utf-8")).hexdigest()
                != row["manifest_sha256"]
            ):
                raise ValueError
            legs = tuple(
                self._read_leg_locked(
                    portfolio_ref, group_id, cancel_request_id, item["leg_id"]
                )
                for item in manifest
            )
            if any(
                any(getattr(leg, name) != item[name] for name in _LEG_KEYS)
                for leg, item in zip(legs, manifest, strict=True)
            ):
                raise ValueError
            return GroupCancelPlan(
                portfolio_ref=portfolio_ref,
                group_id=group_id,
                cancel_request_id=row["cancel_request_id"],
                origin_run_id=row["origin_run_id"],
                manifest_sha256=row["manifest_sha256"],
                created_at_ns=row["created_at_ns"],
                updated_at_ns=row["updated_at_ns"],
                legs=legs,
            )
        except Exception:
            raise GroupCancelPlanError("GROUP_CANCEL_PLAN_CORRUPT") from None

    def _read_leg_locked(
        self, portfolio_ref: str, group_id: str, cancel_request_id: str, leg_id: str
    ) -> GroupCancelPlanLeg:
        row = self._owner._connection.execute(
            "SELECT * FROM coordinator_group_cancel_plan_legs "
            "WHERE portfolio_ref=? AND group_id=? AND cancel_request_id=? AND leg_id=?",
            (portfolio_ref, group_id, cancel_request_id, leg_id),
        ).fetchone()
        if row is None:
            raise GroupCancelPlanError("GROUP_CANCEL_PLAN_LEG_MISSING")
        try:
            result = None if row["result_json"] is None else json.loads(row["result_json"])
            if result is not None and _canonical(result) != row["result_json"]:
                raise ValueError
            if row["phase"] not in _PHASES:
                raise ValueError
            return GroupCancelPlanLeg(
                leg_id=row["leg_id"],
                account_ref=row["account_ref"],
                cancel_id=row["cancel_id"],
                target_intent_id=row["target_intent_id"],
                provider_order_id=row["provider_order_id"],
                phase=row["phase"],
                result=None if result is None else MappingProxyType(result),
                updated_at_ns=row["updated_at_ns"],
            )
        except Exception:
            raise GroupCancelPlanError("GROUP_CANCEL_PLAN_CORRUPT") from None

    @staticmethod
    def _validate_refs(*values: str) -> None:
        if any(type(value) is not str or _REF.fullmatch(value) is None for value in values):
            raise GroupCancelPlanError("GROUP_CANCEL_IDENTITY_INVALID")

    @classmethod
    def _validate_legs(cls, legs: Sequence[Mapping[str, object]]) -> list[dict[str, str]]:
        if (
            isinstance(legs, (str, bytes))
            or not isinstance(legs, Sequence)
            or not 1 <= len(legs) <= 3
        ):
            raise GroupCancelPlanError("GROUP_CANCEL_LEGS_INVALID")
        normalized: list[dict[str, str]] = []
        for item in legs:
            if not isinstance(item, Mapping) or set(item) != _LEG_KEYS:
                raise GroupCancelPlanError("GROUP_CANCEL_LEGS_INVALID")
            values = {name: item[name] for name in _LEG_KEYS}
            if any(type(value) is not str or _REF.fullmatch(value) is None for value in values.values()):
                raise GroupCancelPlanError("GROUP_CANCEL_LEGS_INVALID")
            normalized.append({name: values[name] for name in sorted(_LEG_KEYS)})
        if len({item["leg_id"] for item in normalized}) != len(normalized):
            raise GroupCancelPlanError("GROUP_CANCEL_LEGS_INVALID")
        normalized.sort(key=lambda item: item["leg_id"])
        return normalized


def _result_phase(value: object) -> str:
    if not isinstance(value, Mapping) or set(value) != {"found", "record"}:
        return "UNKNOWN"
    record = value["record"]
    if value["found"] is not True or not isinstance(record, Mapping):
        return "UNKNOWN"
    state = record.get("state")
    if state in {"ACKED", "CANCELLED", "REJECTED", "BLOCKED"}:
        return "OBSERVED"
    return "UNKNOWN"


def _result_matches_leg(value: object, leg: GroupCancelPlanLeg) -> bool:
    if not isinstance(value, Mapping) or set(value) != {"found", "record"}:
        return False
    if value["found"] is False:
        return value["record"] is None
    record = value["record"]
    if value["found"] is not True or not isinstance(record, Mapping):
        return False
    return (
        record.get("cancel_id") == leg.cancel_id
        and record.get("target_intent_id") == leg.target_intent_id
        and record.get("provider_order_id") == leg.provider_order_id
        and type(record.get("state")) is str
        and type(record.get("dispatch_attempts")) is int
        and type(record.get("review_required")) is bool
    )


def _record_state(value: Mapping[str, Any] | None) -> str | None:
    if value is None or not isinstance(value.get("record"), Mapping):
        return None
    state = value["record"].get("state")
    return state if type(state) is str else None


def _record_terminal(value: Mapping[str, Any] | None) -> bool:
    if value is None or not isinstance(value.get("record"), Mapping):
        return False
    record = value["record"]
    return record.get("state") in {"CANCELLED", "REJECTED", "BLOCKED"}


def _state_rank(state: str | None) -> int:
    return {
        None: 0,
        "PENDING_ADMISSION": 1,
        "PENDING_DISPATCH": 2,
        "DISPATCHING": 3,
        "UNKNOWN": 4,
        "ACKED": 5,
        "CANCELLED": 6,
        "REJECTED": 6,
        "BLOCKED": 6,
    }.get(state, 0)


def _json_value(value: object) -> object:
    if value is None or type(value) in {str, int, bool}:
        return value
    if type(value) is float:
        if not math.isfinite(value):
            raise GroupCancelPlanError("GROUP_CANCEL_RESULT_INVALID")
        return value
    if isinstance(value, Mapping):
        if any(type(key) is not str for key in value):
            raise GroupCancelPlanError("GROUP_CANCEL_RESULT_INVALID")
        return {key: _json_value(item) for key, item in value.items()}
    if type(value) in {tuple, list}:
        return [_json_value(item) for item in value]
    raise GroupCancelPlanError("GROUP_CANCEL_RESULT_INVALID")


def _canonical(value: object) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=True)


__all__ = [
    "GroupCancelPlan",
    "GroupCancelPlanError",
    "GroupCancelPlanLeg",
    "GroupCancelPlanStore",
    "group_cancel_leg_id",
]
