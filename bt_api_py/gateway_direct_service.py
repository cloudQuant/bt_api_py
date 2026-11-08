"""Trusted, account-scoped direct-command journal service slice.

Only reservation, release and lookup are exposed; this is not native dispatch,
writer fencing, settlement, owner readiness or trading acceptance. Results use
``{"status": "OK", "record": ...}`` or the finite pre-mutation denial
``{"status": "REQUEST_REJECTED", "code": ...}``. A denial describes only
this request and does not prove an earlier command unsent or an obligation
released. Storage and post-commit exceptions are deliberately not translated.
Recovery registration uses only ``direct.command_status`` and
``direct.release``; a reserved record at zero TTL is pending expiry, not
durably EXPIRED or proof that a provider request was not sent. Retrying the
same request key does not extend its original TTL. Remaining TTL is measured
on the owner during the observation and is not a live client-side deadline.
"""

from __future__ import annotations

import json
from collections.abc import Mapping
from dataclasses import dataclass
from types import MappingProxyType
from typing import Any

from bt_api_base.service_protocol import WireRole
from bt_api_gateway.direct_command_journal import (
    DirectCommandJournalError,
    DirectCommandObservation,
    GatewayDirectCommandJournal,
    payload_digest,
)

from bt_api_py.gateway_direct_dispatch import (
    GatewayDirectCompositionError,
    decode_gateway_order_request,
)
from bt_api_py.service_registry import ServiceCallContext

SUPPORTED_OPERATIONS = frozenset({"direct.reserve", "direct.release", "direct.command_status"})
WRITE_OPERATIONS = frozenset({"direct.reserve", "direct.release"})
RECOVERY_OPERATIONS = frozenset({"direct.command_status", "direct.release"})


def recovery_requested_operations(
    mount: str = "broker.direct_write",
) -> Mapping[str, frozenset[str]]:
    """Return explicit operations for a strategy's stopped-run recovery.

    Pass this mapping as ServiceClientRuntime ``requested_operations``. The
    registration still claims the existing run for release; it grants no new
    reserve operation and does not bypass a stop or the Registry's authority.
    """
    if mount != "broker.direct_write":
        raise ValueError("INVALID_RECOVERY_MOUNT")
    return MappingProxyType({mount: RECOVERY_OPERATIONS})


def _ref(value: object) -> bool:
    return type(value) is str and bool(value) and value == value.strip() and len(value) <= 256


@dataclass(frozen=True, slots=True)
class GatewayDirectGrant:
    strategy_ref: str
    symbols: frozenset[str]

    def __post_init__(self) -> None:
        if (
            not _ref(self.strategy_ref)
            or type(self.symbols) is not frozenset
            or not self.symbols
            or any(not _ref(symbol) for symbol in self.symbols)
        ):
            raise ValueError("INVALID_DIRECT_GRANT")


def validate_service_options(
    options: Mapping[str, object], bind_ports: tuple[str, ...]
) -> Mapping[str, object]:
    if dict(options) != {"role": "trading_owner"} or bind_ports != ("service.direct_write",):
        raise ValueError("INVALID_DIRECT_OPTIONS")
    return {"role": "trading_owner"}


def _reject(code: str) -> dict[str, str]:
    return {"status": "REQUEST_REJECTED", "code": code}


def _public(
    observation: DirectCommandObservation, journal: GatewayDirectCommandJournal
) -> dict[str, Any]:
    record = observation.record
    return {
        "status": "OK",
        "record": {
            "request_key": record.request_key,
            "reservation_id": record.reservation_id,
            "digest": record.digest,
            "state": record.status,
            "version": record.version,
            "release_operation_id": record.release_operation_id,
            "release_expected_version": record.release_expected_version,
            "journal_id": journal.journal_id,
            "owner_generation": journal.generation,
            "reservation_generation": record.generation,
            "generation_current": observation.generation_current,
            "remaining_ttl_seconds": observation.remaining_ttl_seconds,
            "expiry_pending": record.status == "RESERVED"
            and observation.remaining_ttl_seconds == 0,
        },
    }


def _payload(value: object) -> bool:
    if not isinstance(value, Mapping) or not value or len(value) > 128:
        return False
    for key, item in value.items():
        if not _ref(key) or type(item) not in (str, int, bool, type(None)):
            return False
        if type(item) is str and len(item) > 16384:
            return False
        if type(item) is int and not -(2**63) <= item < 2**63:
            return False
    return _ref(value.get("symbol"))


class GatewayDirectServiceBinding:
    """Trusted operator binding; identity and scope come only from the registry."""

    def __init__(
        self, journal: GatewayDirectCommandJournal, grants: Mapping[str, GatewayDirectGrant]
    ):
        if type(journal) is not GatewayDirectCommandJournal:
            raise TypeError("EXACT_DIRECT_JOURNAL_REQUIRED")
        if not isinstance(grants, Mapping) or not grants:
            raise ValueError("INVALID_DIRECT_GRANTS")
        copied = dict(grants)
        if any(
            not _ref(key) or type(value) is not GatewayDirectGrant for key, value in copied.items()
        ):
            raise ValueError("INVALID_DIRECT_GRANTS")
        self._journal = journal
        self._grants = MappingProxyType(copied)

    def handle(
        self, operation: str, *, context: ServiceCallContext, arguments: Mapping[str, Any]
    ) -> Mapping[str, Any]:
        if type(context) is not ServiceCallContext or not isinstance(arguments, Mapping):
            return _reject("INVALID_REQUEST")
        identity, scope = context.identity, context.scope
        grant = self._grants.get(context.principal_ref)
        if (
            identity.role is not WireRole.STRATEGY_CLIENT
            or identity.principal_ref != context.principal_ref
            or grant is None
            or scope.account_ref != self._journal.account_scope
            or scope.strategy_ref != grant.strategy_ref
            or scope.run_id is None
            or scope.account_refs
            or scope.portfolio_ref is not None
            or scope.manual_book_ref is not None
            or scope.group_id is not None
            or scope.leg_id is not None
        ):
            return _reject("SCOPE_DENIED")
        fields = {
            "direct.reserve": {"request_key", "payload", "ttl_seconds"},
            "direct.release": {
                "request_key",
                "reservation_id",
                "digest",
                "operation_id",
                "expected_version",
            },
            "direct.command_status": {"request_key"},
        }.get(operation)
        if fields is None or set(arguments) != fields or not _ref(arguments.get("request_key")):
            return _reject("INVALID_REQUEST")
        key = arguments["request_key"]
        if operation == "direct.reserve":
            payload, ttl = arguments["payload"], arguments["ttl_seconds"]
            if not _payload(payload) or type(ttl) not in (int, float) or not 0 < ttl <= 30:
                return _reject("INVALID_REQUEST")
            payload = dict(payload)
            try:
                order_request = decode_gateway_order_request(payload, self._journal.account_scope)
            except GatewayDirectCompositionError:
                return _reject("INVALID_REQUEST")
            if order_request.idempotency_key != key:
                return _reject("INVALID_REQUEST")
            if payload["symbol"] not in grant.symbols:
                return _reject("SCOPE_DENIED")
            try:
                digest = payload_digest(payload)
            except DirectCommandJournalError as error:
                if error.code == "INVALID_PAYLOAD":
                    return _reject("INVALID_REQUEST")
                raise
            # Read-only preflight supplies a finite conflict result. Races and
            # all exceptions from the subsequent write remain unknown.
            try:
                old = self._journal.observe(grant.strategy_ref, key, context.principal_ref)
            except DirectCommandJournalError as error:
                if error.code == "REQUEST_CONFLICT":
                    return _reject("REQUEST_CONFLICT")
                if error.code != "NOT_FOUND":
                    raise
            else:
                if old.record.digest != digest:
                    return _reject("REQUEST_CONFLICT")
                return _public(old, self._journal)
            self._journal.reserve(
                grant.strategy_ref, key, context.principal_ref, payload, ttl_seconds=ttl
            )
            return _public(
                self._journal.observe(grant.strategy_ref, key, context.principal_ref), self._journal
            )
        if operation == "direct.release" and (
            not all(_ref(arguments[name]) for name in ("reservation_id", "digest", "operation_id"))
            or type(arguments["expected_version"]) is not int
            or arguments["expected_version"] < 1
        ):
            return _reject("INVALID_REQUEST")
        try:
            observation = self._journal.observe(grant.strategy_ref, key, context.principal_ref)
        except DirectCommandJournalError as error:
            if error.code in {"NOT_FOUND", "REQUEST_CONFLICT"}:
                return _reject("NOT_FOUND")
            raise
        record = observation.record
        payload = json.loads(record.payload_json)
        if not _payload(payload):
            raise RuntimeError("INVALID_STORED_COMMAND")
        decode_gateway_order_request(payload, self._journal.account_scope)
        if payload["idempotency_key"] != record.request_key:
            raise RuntimeError("INVALID_STORED_COMMAND")
        if payload["symbol"] not in grant.symbols:
            return _reject("SCOPE_DENIED")
        if operation == "direct.command_status":
            return _public(observation, self._journal)
        if (
            record.reservation_id != arguments["reservation_id"]
            or record.digest != arguments["digest"]
        ):
            return _reject("REQUEST_CONFLICT")
        self._journal.release(
            grant.strategy_ref,
            key,
            arguments["reservation_id"],
            arguments["digest"],
            context.principal_ref,
            arguments["operation_id"],
            arguments["expected_version"],
        )
        return _public(
            self._journal.observe(grant.strategy_ref, key, context.principal_ref), self._journal
        )
