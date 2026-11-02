"""SDK-owned bridge to the optional CTP execution identity authority.

This module validates the exact execution types and journal mirror inputs. It
does not allocate identities or grant order authority; the SDK session retains
the journal, its lock, and the checks surrounding every use of this bridge.
"""

from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass
from datetime import date
from importlib import metadata as importlib_metadata
from typing import Any, Protocol


class _CtpOrderIdentityReader(Protocol):
    """Read-only portion of the independent I9 CTP identity authority."""

    def read_ctp_order_identity(self, scope: Any, managed_intent_id: str) -> Any: ...


class _CtpDispatchCommandReader(_CtpOrderIdentityReader, Protocol):
    """Read-only portion of the exact shared I9 CTP cancel command ledger."""

    def read_ctp_dispatch_command(self, scope: Any, command_id: str) -> Any: ...

    def read_ctp_dispatch_projection(self, scope: Any, command_id: str) -> Any: ...


_CTP_ORDER_REF_PATTERN = re.compile(r"^[0-9]{12}$", re.ASCII)
_CTP_RUNTIME_ORDER_ID_PATTERN = re.compile(r"^bt-managed-v1:[0-9a-f]{64}$", re.ASCII)
_CTP_MANAGED_INTENT_PATTERN = re.compile(r"^[A-Za-z0-9._:-]{1,128}$", re.ASCII)
_CTP_AUTHORITY_DIGEST_PATTERN = re.compile(r"^(?:account|scope):[0-9a-f]{64}$", re.ASCII)
_CTP_ORDER_IDENTITY_MIRROR_AUTHORITY = "bt_api_execution.CtpOrderIdentityReservation.v1"


@dataclass(frozen=True, slots=True)
class CtpOrderIdentityReservationMirror:
    """Candidate-only SDK-journal echo of an already committed I9 reservation.

    This value is identity evidence only. It does not authorize, stage, queue,
    or dispatch a native CTP request, and it is not sufficient identity proof
    for a later managed OrderRequest or cancel.
    """

    account_key: str
    trading_day: str
    scope_key: str
    managed_intent_id: str
    runtime_order_id: str
    order_ref: str
    created_at_ns: int


def _installed_i9_ctp_order_identity_types():
    """Return the exact supported installed I9 DTO and store classes.

    This is a narrow API-version/type check. It does not authenticate package
    files, the Python process, or code already running in the process.
    """
    if importlib_metadata.version("bt_api_execution") != "0.2.0":
        raise ValueError("unsupported bt_api_execution distribution version")
    from bt_api_execution.contracts import ExecutionScope as I9ExecutionScope
    from bt_api_execution.store import (
        CtpOrderIdentityReservation as I9CtpOrderIdentityReservation,
    )
    from bt_api_execution.store import SqliteExecutionStore as I9SqliteExecutionStore

    if (
        I9ExecutionScope.__module__ != "bt_api_execution.contracts"
        or I9CtpOrderIdentityReservation.__module__ != "bt_api_execution.store"
        or I9SqliteExecutionStore.__module__ != "bt_api_execution.store"
    ):
        raise ValueError("unexpected bt_api_execution CTP identity class origin")
    return I9ExecutionScope, I9SqliteExecutionStore, I9CtpOrderIdentityReservation


def _installed_i9_ctp_dispatch_command_types():
    """Return exact I9 command/readback DTO types supported by this candidate."""
    if importlib_metadata.version("bt_api_execution") != "0.2.0":
        raise ValueError("unsupported bt_api_execution distribution version")
    from bt_api_execution.store import CtpCancelActionProjection as I9CtpCancelActionProjection
    from bt_api_execution.store import CtpDispatchCommand as I9CtpDispatchCommand
    from bt_api_execution.store import CtpDispatchCorrelationKey as I9CtpDispatchCorrelationKey
    from bt_api_execution.store import CtpDispatchProjection as I9CtpDispatchProjection
    from bt_api_execution.store import CtpTargetOrderProjection as I9CtpTargetOrderProjection

    if any(
        value.__module__ != "bt_api_execution.store"
        for value in (
            I9CtpDispatchCommand,
            I9CtpDispatchCorrelationKey,
            I9CtpDispatchProjection,
            I9CtpCancelActionProjection,
            I9CtpTargetOrderProjection,
        )
    ):
        raise ValueError("unexpected bt_api_execution CTP dispatch class origin")
    return (
        I9CtpDispatchCommand,
        I9CtpDispatchCorrelationKey,
        I9CtpDispatchProjection,
        I9CtpCancelActionProjection,
        I9CtpTargetOrderProjection,
    )


def _ctp_order_identity_scope_values(scope, *, expected_type):
    """Validate and return the exact typed I9 scope identity fields."""
    if type(scope) is not expected_type:
        raise ValueError("typed I9 CTP execution scope required")
    provider = getattr(scope, "provider", None)
    environment = getattr(scope, "environment", None)
    account_ref = getattr(scope, "account_ref", None)
    strategy_id = getattr(scope, "strategy_id", None)
    trading_day = getattr(scope, "trading_day", None)
    account_key = getattr(scope, "account_key", None)
    scope_key = getattr(scope, "key", None)
    if (
        type(provider) is not str
        or provider.casefold() != "ctp"
        or type(environment) is not str
        or not environment
        or environment != environment.strip()
        or type(account_ref) is not str
        or not account_ref
        or account_ref != account_ref.strip()
        or type(strategy_id) is not str
        or not strategy_id
        or strategy_id != strategy_id.strip()
        or type(trading_day) is not str
        or not re.fullmatch(r"[0-9]{8}", trading_day, re.ASCII)
        or type(account_key) is not str
        or not _CTP_AUTHORITY_DIGEST_PATTERN.fullmatch(account_key)
        or not account_key.startswith("account:")
        or type(scope_key) is not str
        or not _CTP_AUTHORITY_DIGEST_PATTERN.fullmatch(scope_key)
        or not scope_key.startswith("scope:")
    ):
        raise ValueError("I9 CTP execution scope is incomplete")
    try:
        date(int(trading_day[:4]), int(trading_day[4:6]), int(trading_day[6:8]))
    except ValueError as error:
        raise ValueError("I9 CTP execution scope trading day is invalid") from error
    account_payload = {
        "provider": provider,
        "environment": environment,
        "account_ref": account_ref,
    }
    scope_payload = {
        **account_payload,
        "strategy_id": strategy_id,
        "trading_day": trading_day,
    }
    account_digest = hashlib.sha256(
        json.dumps(
            account_payload,
            sort_keys=True,
            separators=(",", ":"),
            ensure_ascii=True,
            allow_nan=False,
        ).encode("utf-8")
    ).hexdigest()
    scope_digest = hashlib.sha256(
        json.dumps(
            scope_payload,
            sort_keys=True,
            separators=(",", ":"),
            ensure_ascii=True,
            allow_nan=False,
        ).encode("utf-8")
    ).hexdigest()
    if account_key != "account:" + account_digest or scope_key != "scope:" + scope_digest:
        raise ValueError("I9 CTP execution scope digest is inconsistent")
    return {
        "provider": provider.upper(),
        "environment": environment,
        "account_ref": account_ref,
        "strategy_id": strategy_id,
        "trading_day": trading_day,
        "account_key": account_key,
        "scope_key": scope_key,
        "scope_payload": scope_payload,
    }


def _ctp_order_identity_reservation_values(
    value, *, expected_type, scope_values, managed_intent_id, runtime_order_id
):
    """Validate the I9 immutable reservation DTO without allocating a Ref."""
    if type(value) is not expected_type:
        raise ValueError("typed I9 CTP OrderRef reservation required")
    names = (
        "account_key",
        "trading_day",
        "scope_key",
        "managed_intent_id",
        "runtime_order_id",
        "order_ref",
        "created_at_ns",
    )
    values = tuple(getattr(value, name, None) for name in names)
    if (
        any(type(item) is not str or not item for item in values[:6])
        or type(values[6]) is not int
        or values[6] <= 0
        or values[0] != scope_values["account_key"]
        or values[1] != scope_values["trading_day"]
        or values[2] != scope_values["scope_key"]
        or values[3] != managed_intent_id
        or values[4] != runtime_order_id
        or not _CTP_AUTHORITY_DIGEST_PATTERN.fullmatch(values[0])
        or not _CTP_AUTHORITY_DIGEST_PATTERN.fullmatch(values[2])
        or not _CTP_MANAGED_INTENT_PATTERN.fullmatch(values[3])
        or not _CTP_RUNTIME_ORDER_ID_PATTERN.fullmatch(values[4])
        or not _CTP_ORDER_REF_PATTERN.fullmatch(values[5])
    ):
        raise ValueError("I9 CTP OrderRef reservation conflicts with the requested identity")
    try:
        date(int(values[1][:4]), int(values[1][4:6]), int(values[1][6:8]))
    except ValueError as error:
        raise ValueError("I9 CTP OrderRef reservation trading day is invalid") from error
    return CtpOrderIdentityReservationMirror(*values)


def _ctp_order_identity_mirror_payload(row):
    """Validate the JSON-safe reservation echo restored from the SDK journal."""
    value = row.get("ctp_order_identity_reservation")
    expected_fields = {
        "account_key",
        "trading_day",
        "scope_key",
        "managed_intent_id",
        "runtime_order_id",
        "order_ref",
        "created_at_ns",
    }
    scope = row.get("ctp_order_identity_scope")
    scope_fields = {"provider", "environment", "account_ref", "strategy_id", "trading_day"}
    if (
        row.get("ctp_order_identity_authority") != _CTP_ORDER_IDENTITY_MIRROR_AUTHORITY
        or not isinstance(value, dict)
        or set(value) != expected_fields
        or not isinstance(scope, dict)
        or set(scope) != scope_fields
        or any(type(scope.get(name)) is not str or not scope[name] for name in scope_fields)
        or type(value.get("created_at_ns")) is not int
        or value["created_at_ns"] <= 0
        or any(type(value.get(name)) is not str for name in expected_fields - {"created_at_ns"})
        or not _CTP_AUTHORITY_DIGEST_PATTERN.fullmatch(value["account_key"])
        or not _CTP_AUTHORITY_DIGEST_PATTERN.fullmatch(value["scope_key"])
        or not _CTP_MANAGED_INTENT_PATTERN.fullmatch(value["managed_intent_id"])
        or not _CTP_RUNTIME_ORDER_ID_PATTERN.fullmatch(value["runtime_order_id"])
        or not _CTP_ORDER_REF_PATTERN.fullmatch(value["order_ref"])
        or row.get("client_order_id") != value["order_ref"]
        or row.get("runtime_order_id") != value["runtime_order_id"]
        or row.get("managed_intent_id") != value["managed_intent_id"]
        or row.get("trading_day") != value["trading_day"]
        or scope["provider"].upper() != "CTP"
        or scope["trading_day"] != value["trading_day"]
        or scope["strategy_id"] != row.get("strategy_id")
        or scope["account_ref"] != row.get("account_id")
        or len(value["trading_day"]) != 8
        or not value["trading_day"].isascii()
        or not value["trading_day"].isdigit()
    ):
        raise ValueError("invalid CTP OrderRef mirror journal row")
    try:
        date(
            int(value["trading_day"][:4]),
            int(value["trading_day"][4:6]),
            int(value["trading_day"][6:8]),
        )
    except ValueError as error:
        raise ValueError("invalid CTP OrderRef mirror journal trading day") from error
    scope_account_key = (
        "account:"
        + hashlib.sha256(
            json.dumps(
                {
                    "provider": scope["provider"],
                    "environment": scope["environment"],
                    "account_ref": scope["account_ref"],
                },
                sort_keys=True,
                separators=(",", ":"),
                ensure_ascii=True,
                allow_nan=False,
            ).encode("utf-8")
        ).hexdigest()
    )
    scope_key = (
        "scope:"
        + hashlib.sha256(
            json.dumps(
                scope, sort_keys=True, separators=(",", ":"), ensure_ascii=True, allow_nan=False
            ).encode("utf-8")
        ).hexdigest()
    )
    if scope_account_key != value["account_key"] or scope_key != value["scope_key"]:
        raise ValueError("CTP OrderRef mirror scope digest mismatch")
    return CtpOrderIdentityReservationMirror(
        value["account_key"],
        value["trading_day"],
        value["scope_key"],
        value["managed_intent_id"],
        value["runtime_order_id"],
        value["order_ref"],
        value["created_at_ns"],
    )


