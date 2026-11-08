"""Trusted, account-scoped direct-command journal service slice.

Reservation, release and lookup work without a native owner. Explicit owner
composition adds a volatile dispatch handoff; it is not trading acceptance. Results use
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

import hashlib
import json
import sys
import threading
from collections.abc import Callable, Mapping
from contextlib import contextmanager
from dataclasses import dataclass
from types import MappingProxyType
from typing import Any, ContextManager

from bt_api_base.direct_write_protocol import NativeOrderIdentity
from bt_api_base.gateway_direct_protocol import (
    GatewayCommandBinding,
    GatewayCommandRecord,
    GatewayDirectProtocolError,
    gateway_payload_digest,
    validate_gateway_native_identity,
)
from bt_api_base.service_protocol import WireRole
from bt_api_gateway.direct_command_journal import (
    DirectCommandJournalError,
    DirectCommandObservation,
    GatewayDirectCommandJournal,
    payload_digest,
)

from bt_api_py.gateway_direct_owner import GatewayDirectOwner, GatewayDispatchCommand
from bt_api_py._contracts.models import OrderRequest
from bt_api_py.gateway_order_contract import (
    GatewayDirectCompositionError,
    decode_gateway_order_request,
)
from bt_api_py.service_registry import ServiceCallContext, ServiceDispatchAdmission

STORAGE_OPERATIONS = frozenset({"direct.reserve", "direct.release", "direct.command_status"})
SUPPORTED_OPERATIONS = STORAGE_OPERATIONS | {"direct.dispatch"}
ACCOUNT_READ_OPERATIONS = frozenset(
    {
        "account.snapshot",
        "account.snapshot.typed",
        "account.head",
        "account.read_after",
        "account.ack",
        "account.progress",
    }
)
EVENT_OPERATIONS = frozenset({"direct.events.read_after", "direct.events.progress", "direct.events.ack"})
TRADING_OPERATIONS = SUPPORTED_OPERATIONS | ACCOUNT_READ_OPERATIONS | EVENT_OPERATIONS
WRITE_OPERATIONS = frozenset({"direct.reserve", "direct.release", "direct.dispatch"})
RECOVERY_OPERATIONS = frozenset({"direct.command_status", "direct.release"})


class GatewayReservationAdmissionDenied(Exception):
    """Trusted local admission rejected before reservation mutation."""


class _ReservationAdmissionRejected(Exception):
    pass


@contextmanager
def _reservation_admission(factory, context, order_request):
    # Only a denial from the factory/entry is known to precede mutation.
    # The same exception from exit or commit must propagate as unknown.
    try:
        manager = factory(context, order_request)
        enter, exit_ = type(manager).__enter__, type(manager).__exit__
        enter(manager)
    except GatewayReservationAdmissionDenied as exc:
        if type(exc) is not GatewayReservationAdmissionDenied:
            raise
        raise _ReservationAdmissionRejected from None
    try:
        yield
    except BaseException:
        exit_(manager, *sys.exc_info())
        raise
    else:
        exit_(manager, None, None, None)


def recovery_requested_operations(
    mount: str = "broker.direct_write",
    *, include_execution_events: bool = False,
) -> Mapping[str, frozenset[str]]:
    """Return explicit operations for a strategy's stopped-run recovery.

    Pass this mapping as ServiceClientRuntime ``requested_operations``. The
    registration still claims the existing run for release; it grants no new
    reserve operation and does not bypass a stop or the Registry's authority.
    """
    if mount != "broker.direct_write" or type(include_execution_events) is not bool:
        raise ValueError("INVALID_RECOVERY_MOUNT")
    return MappingProxyType({mount: RECOVERY_OPERATIONS | (EVENT_OPERATIONS if include_execution_events else frozenset())})


def execution_events_requested_operations(mount: str = "broker.direct_write") -> Mapping[str, frozenset[str]]:
    """Select only event reads and cursor acknowledgments, without release rights."""
    if mount != "broker.direct_write":
        raise ValueError("INVALID_RECOVERY_MOUNT")
    return MappingProxyType({mount: EVENT_OPERATIONS})


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
    if (
        set(options) - {"role", "dispatch_enabled"}
        or options.get("role") != "trading_owner"
        or type(options.get("dispatch_enabled", False)) is not bool
        or len(bind_ports) not in (1, 2)
        or frozenset(bind_ports)
        not in (
            frozenset({"service.direct_write"}),
            frozenset({"service.direct_write", "service.account_read"}),
        )
    ):
        raise ValueError("INVALID_DIRECT_OPTIONS")
    return {"role": "trading_owner", "dispatch_enabled": options.get("dispatch_enabled", False)}


def _reject(code: str) -> dict[str, str]:
    return {"status": "REQUEST_REJECTED", "code": code}


def _unique_json_object(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("duplicate JSON field")
        result[key] = value
    return result


def _identity_public(record: object) -> tuple[dict[str, Any] | None, list[str]]:
    """Project bounded durable identity; never return raw provider strong keys."""
    raw = record.native_identity_json
    keys = record.strong_identity_keys
    if raw is None:
        if keys:
            raise RuntimeError("INVALID_STORED_IDENTITY")
        return None, []
    try:
        if type(raw) is not str or len(raw.encode("utf-8")) > 65536:
            raise ValueError
        value = json.loads(raw, object_pairs_hook=_unique_json_object)
        if type(value) is not dict or set(value) != {
            "exchange_name",
            "client_order_id",
            "session_generation",
            "fields_json",
        }:
            raise ValueError
        fields_json = value["fields_json"]
        if type(fields_json) is not str or len(fields_json.encode("utf-8")) > 65536:
            raise ValueError
        fields = json.loads(fields_json, object_pairs_hook=_unique_json_object)
        if type(fields) is not dict:
            raise ValueError
        identity = NativeOrderIdentity(
            value["exchange_name"], value["client_order_id"], fields, value["session_generation"]
        )
        validate_gateway_native_identity(identity)
        public = identity.to_dict()
        if type(keys) is not tuple or not 1 <= len(keys) <= 16 or any(not _ref(k) for k in keys):
            raise ValueError
        digests = [hashlib.sha256(key.encode("utf-8")).hexdigest() for key in keys]
        return public, digests
    except Exception:
        raise RuntimeError("INVALID_STORED_IDENTITY") from None


def _public(
    observation: DirectCommandObservation, journal: GatewayDirectCommandJournal
) -> dict[str, Any]:
    record = observation.record
    native_identity, key_digests = _identity_public(record)
    if record.account_scope != journal.account_scope:
        raise RuntimeError("INVALID_STORED_COMMAND")
    binding = GatewayCommandBinding(
        journal.journal_id, record.account_scope, record.strategy_scope, record.principal
    )
    candidate = {
        "schema_version": 1,
        "journal_id": journal.journal_id,
        "account_scope": record.account_scope,
        "strategy_scope": record.strategy_scope,
        "principal": record.principal,
        "request_key": record.request_key,
        "reservation_id": record.reservation_id,
        "digest": record.digest,
        "state": record.status,
        "version": record.version,
        "native_identity": native_identity,
        "strong_identity_key_digests": key_digests,
        "release_operation_id": record.release_operation_id,
        "release_expected_version": record.release_expected_version,
        "owner_generation": journal.generation,
        "reservation_generation": record.generation,
        "generation_current": observation.generation_current,
        "remaining_ttl_seconds": observation.remaining_ttl_seconds,
        "expiry_pending": record.status == "RESERVED" and observation.remaining_ttl_seconds == 0,
    }
    return {
        "status": "OK",
        "record": GatewayCommandRecord.from_mapping(
            candidate, expected_binding=binding
        ).to_mapping(),
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
        self,
        journal: GatewayDirectCommandJournal,
        grants: Mapping[str, GatewayDirectGrant],
        *,
        owner: GatewayDirectOwner | None = None,
        reservation_fence: Callable[[ServiceCallContext, OrderRequest], ContextManager[None]]
        | None = None,
        execution_stream: object | None = None,
        execution_domain_ref: str | None = None,
    ):
        if type(journal) is not GatewayDirectCommandJournal:
            raise TypeError("EXACT_DIRECT_JOURNAL_REQUIRED")
        if not isinstance(grants, Mapping) or not grants:
            raise ValueError("INVALID_DIRECT_GRANTS")
        if reservation_fence is not None and not callable(reservation_fence):
            raise TypeError("INVALID_RESERVATION_FENCE")
        copied = dict(grants)
        if any(
            not _ref(key) or type(value) is not GatewayDirectGrant for key, value in copied.items()
        ):
            raise ValueError("INVALID_DIRECT_GRANTS")
        if execution_stream is not None:
            from bt_api_py.gateway_execution_stream_service import GatewayExecutionStreamService

            if (type(execution_stream) is not GatewayExecutionStreamService or owner is None
                    or not execution_stream.matches(journal, {key: value.strategy_ref for key, value in copied.items()}, execution_domain_ref)):
                raise ValueError("INVALID_EXECUTION_STREAM")
        if owner is not None:
            if type(owner) is not GatewayDirectOwner:
                raise TypeError("EXACT_DIRECT_OWNER_REQUIRED")
            binding = owner.binding()
            if (
                binding is None
                or not owner.is_bound_to(journal)
                or (binding.account_scope, binding.journal_id, binding.generation)
                != (journal.account_scope, journal.journal_id, journal.generation)
            ):
                raise ValueError("DIRECT_OWNER_JOURNAL_MISMATCH")
        self._journal = journal
        self._grants = MappingProxyType(copied)
        self._owner = owner
        self._reservation_fence = reservation_fence
        self._execution_stream = execution_stream
        self.supported_operations = (
            SUPPORTED_OPERATIONS if owner is not None else STORAGE_OPERATIONS
        ) | (EVENT_OPERATIONS if execution_stream is not None else frozenset())

    @property
    def account_scope(self) -> str:
        """Account scope of the bound direct-command journal."""
        return self._journal.account_scope

    def handle(
        self, operation: str, *, context: ServiceCallContext, arguments: Mapping[str, Any]
    ) -> Mapping[str, Any]:
        if operation in EVENT_OPERATIONS:
            if self._execution_stream is None:
                return _reject("EVENTS_UNAVAILABLE")
            return self._execution_stream.handle(operation, context=context, arguments=arguments)
        if operation != "direct.dispatch":
            return self._handle(operation, context=context, arguments=arguments, permit=None)
        permit = getattr(context, "dispatch_admission", None)
        if type(permit) is not ServiceDispatchAdmission:
            return _reject("ADMISSION_DENIED")
        transferred = [False]
        finalizer_lock = threading.Lock()
        finalized = [False]

        def finalize_once():
            with finalizer_lock:
                if finalized[0]:
                    return
                finalized[0] = True
            permit.close()

        try:
            if not permit.matches_context(context) or not permit.matches(arguments):
                return _reject("ADMISSION_DENIED")
            return self._handle(
                operation,
                context=context,
                arguments=dict(arguments),
                permit=(permit, transferred, finalize_once),
            )
        finally:
            if not transferred[0]:
                finalize_once()

    def _handle(
        self,
        operation: str,
        *,
        context: ServiceCallContext,
        arguments: Mapping[str, Any],
        permit: Any,
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
            "direct.dispatch": {"request_key", "reservation_id", "digest", "expected_version"},
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
                digest = gateway_payload_digest(payload)
                if digest != payload_digest(payload):
                    raise RuntimeError("DIRECT_PAYLOAD_DIGEST_MISMATCH")
            except GatewayDirectProtocolError as error:
                if type(error) is GatewayDirectProtocolError and error.code == "INVALID_PAYLOAD":
                    return _reject("INVALID_REQUEST")
                raise
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
            fence = self._reservation_fence
            try:
                self._journal.reserve(
                    grant.strategy_ref,
                    key,
                    context.principal_ref,
                    payload,
                    ttl_seconds=ttl,
                    reservation_fence=(
                        None
                        if fence is None
                        else lambda: _reservation_admission(fence, context, order_request)
                    ),
                )
            except _ReservationAdmissionRejected:
                return _reject("ADMISSION_DENIED")
            return _public(
                self._journal.observe(grant.strategy_ref, key, context.principal_ref), self._journal
            )
        if operation in {"direct.release", "direct.dispatch"} and (
            not all(_ref(arguments[name]) for name in ("reservation_id", "digest"))
            or (operation == "direct.release" and not _ref(arguments["operation_id"]))
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
        if operation == "direct.dispatch":
            if self._owner is None or permit is None:
                return _reject("DISPATCH_UNAVAILABLE")
            if len(arguments["digest"]) != 64 or any(
                c not in "0123456789abcdef" for c in arguments["digest"]
            ):
                return _reject("INVALID_REQUEST")
            if record.status == "RESERVED" and record.version != arguments["expected_version"]:
                return _reject("VERSION_CONFLICT")
            admission, transferred, finalize_once = permit
            command = GatewayDispatchCommand(
                grant.strategy_ref,
                key,
                context.principal_ref,
                arguments["reservation_id"],
                arguments["digest"],
                arguments["expected_version"],
            )

            public = _public(observation, self._journal)

            @contextmanager
            def request_fence(row):
                with admission.scope() as recheck:
                    if (
                        row.account_scope != self._journal.account_scope
                        or row.strategy_scope != grant.strategy_ref
                        or row.request_key != key
                        or row.principal != context.principal_ref
                        or row.reservation_id != arguments["reservation_id"]
                        or row.digest != arguments["digest"]
                        or row.version != arguments["expected_version"]
                    ):
                        raise RuntimeError("DIRECT_DISPATCH_BINDING_CHANGED")
                    current_payload = json.loads(row.payload_json)
                    if not _payload(current_payload):
                        raise RuntimeError("INVALID_STORED_COMMAND")
                    current_order = decode_gateway_order_request(
                        current_payload, self._journal.account_scope
                    )
                    if (
                        current_order.idempotency_key != key
                        or current_payload["symbol"] not in grant.symbols
                    ):
                        raise RuntimeError("DIRECT_DISPATCH_BINDING_CHANGED")
                    yield recheck

            queue_state = self._owner.enqueue(
                command, request_fence=request_fence, finalize=finalize_once
            )
            transferred[0] = True  # enqueue owns finalization on every returned state
            if queue_state in {"QUEUED", "DUPLICATE"}:
                public["dispatch_queue_state"] = (
                    "QUEUED" if queue_state == "QUEUED" else "ALREADY_QUEUED"
                )
                return public
            if queue_state in {"CONFLICT", "FULL", "CLOSING", "BUSY"}:
                return _reject("DISPATCH_QUEUE_" + queue_state)
            raise RuntimeError("INVALID_DISPATCH_QUEUE_STATE")
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
