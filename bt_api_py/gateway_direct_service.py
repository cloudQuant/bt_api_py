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

import math

import hashlib
import json
import sys
import threading
from collections.abc import Callable, Mapping
from contextlib import contextmanager, nullcontext
from dataclasses import dataclass
from types import MappingProxyType
from typing import Any, ContextManager

from bt_api_base.direct_write_protocol import NativeOrderIdentity
from bt_api_base.gateway_cancel_protocol import GatewayCancelRecord
from bt_api_base.gateway_direct_protocol import (
    GatewayCommandBinding,
    GatewayCommandRecord,
    GatewayDirectProtocolError,
    gateway_payload_digest,
    validate_gateway_native_identity,
)
from bt_api_base.service_protocol import WireRole
from bt_api_gateway.direct_cancel_journal import GatewayDirectCancelJournal
from bt_api_gateway.direct_command_journal import (
    DirectCommandJournalError,
    DirectCommandObservation,
    GatewayDirectCommandJournal,
    payload_digest,
)

from bt_api_py.gateway_direct_owner import (
    GatewayCancelCommand,
    GatewayDirectOwner,
    GatewayDispatchCommand,
)
from bt_api_py._contracts.models import OrderRequest
from bt_api_py.gateway_order_contract import (
    GatewayDirectCompositionError,
    decode_gateway_order_request,
)
from bt_api_py.service_registry import (
    ServiceCallContext,
    ServiceDispatchAdmission,
    ServiceResultTooLargeError,
)

STORAGE_OPERATIONS = frozenset({"direct.reserve", "direct.release", "direct.command_status"})
SUPPORTED_OPERATIONS = STORAGE_OPERATIONS | {"direct.dispatch"}
CANCEL_OPERATIONS = frozenset({"direct.cancel", "direct.cancel_status"})
CONTROL_WRITE_OPERATIONS = frozenset({"direct.control.apply", "direct.control.resume", "direct.control.drain_target", "direct.control.reconcile"})
CONTROL_OPERATIONS = CONTROL_WRITE_OPERATIONS | {"direct.control.identity", "direct.control.status", "direct.control.targets", "direct.control.list", "direct.control.resume_status", "direct.control.target_status"}
STRATEGY_POSITION_OPERATIONS = frozenset({"direct.strategy_position", "direct.strategy_close_capacity"})
STRATEGY_FINANCIAL_OPERATIONS = frozenset({'direct.strategy_financial'})
ACCOUNT_COMPARISON_OPERATIONS = frozenset({"account.strategy_net_comparison"})
ACCOUNT_OBSERVER_OPERATIONS = frozenset({"account.events.read_after", "account.events.progress", "account.events.ack"})
ACCOUNT_FACTS_OPERATIONS = frozenset({"account.facts.read_after", "account.facts.progress", "account.facts.ack", "account.facts.status", "account.facts.snapshot_info", "account.facts.snapshot_read", "account.facts.snapshot_confirm"})
DELEGATED_STRATEGY_READ_OPERATIONS = frozenset({"direct.delegated_command_status", "direct.delegated_cancel_status"})
DELEGATED_STRATEGY_CANCEL_OPERATIONS = frozenset({"direct.delegated_cancel"})
DELEGATED_WRITE_OPERATIONS = frozenset({"direct.delegated_reserve", "direct.delegated_dispatch"})
DELEGATE_ACTOR_OPERATIONS = frozenset({'direct.delegate_actor_status', 'direct.delegate_actor_cancel', 'direct.delegate_actor_cancel_status', 'direct.delegate_actor_release', 'direct.delegate_actor_release_status'})
PARENT_OPERATIONS = frozenset({'direct.parent.register', 'direct.parent.query', 'direct.parent.cancel', 'direct.parent.operation'})
PARENT_WRITE_OPERATIONS = frozenset({'direct.parent.register', 'direct.parent.cancel'})
DELEGATE_ACTOR_STREAM_OPERATIONS = frozenset({'direct.delegate_events.read_after', 'direct.delegate_events.progress', 'direct.delegate_events.ack'})
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
INSTRUMENT_OPERATIONS = frozenset({
    "account.instrument_spec.begin", "account.instrument_spec.status",
    "account.instrument_spec.cancel",
})
EVENT_OPERATIONS = frozenset(
    {"direct.events.read_after", "direct.events.progress", "direct.events.ack"}
)
MARKET_OPERATIONS = frozenset({
    "market.bootstrap_many", "market.subscribe", "market.unsubscribe", "market.snapshot",
    "market.head", "market.read_after", "market.read_after_bounded", "market.history",
    "market.clock_observation",
    "market.bootstrap_begin", "market.bootstrap_status", "market.bootstrap_cancel",
})
TRADING_OPERATIONS = (
    SUPPORTED_OPERATIONS | CANCEL_OPERATIONS | ACCOUNT_READ_OPERATIONS | INSTRUMENT_OPERATIONS | EVENT_OPERATIONS
    | MARKET_OPERATIONS | CONTROL_OPERATIONS | STRATEGY_POSITION_OPERATIONS | STRATEGY_FINANCIAL_OPERATIONS | ACCOUNT_COMPARISON_OPERATIONS | ACCOUNT_OBSERVER_OPERATIONS | ACCOUNT_FACTS_OPERATIONS | DELEGATED_STRATEGY_READ_OPERATIONS | DELEGATED_STRATEGY_CANCEL_OPERATIONS | DELEGATED_WRITE_OPERATIONS | DELEGATE_ACTOR_OPERATIONS | DELEGATE_ACTOR_STREAM_OPERATIONS | PARENT_OPERATIONS
)
WRITE_OPERATIONS = frozenset(
    {"direct.reserve", "direct.release", "direct.dispatch", "direct.cancel", "direct.delegate_actor_cancel", "direct.delegate_actor_release"} | CONTROL_WRITE_OPERATIONS | DELEGATED_STRATEGY_CANCEL_OPERATIONS | DELEGATED_WRITE_OPERATIONS | PARENT_WRITE_OPERATIONS
)
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
    *,
    include_execution_events: bool = False,
) -> Mapping[str, frozenset[str]]:
    """Return explicit operations for a strategy's stopped-run recovery.

    Pass this mapping as ServiceClientRuntime ``requested_operations``. The
    registration still claims the existing run for release; it grants no new
    reserve operation and does not bypass a stop or the Registry's authority.
    """
    if mount != "broker.direct_write" or type(include_execution_events) is not bool:
        raise ValueError("INVALID_RECOVERY_MOUNT")
    return MappingProxyType(
        {
            mount: RECOVERY_OPERATIONS
            | (EVENT_OPERATIONS if include_execution_events else frozenset())
        }
    )


def execution_events_requested_operations(
    mount: str = "broker.direct_write",
) -> Mapping[str, frozenset[str]]:
    """Select only event reads and cursor acknowledgments, without release rights."""
    if mount != "broker.direct_write":
        raise ValueError("INVALID_RECOVERY_MOUNT")
    return MappingProxyType({mount: EVENT_OPERATIONS})


def _ref(value: object) -> bool:
    return type(value) is str and bool(value) and value == value.strip() and len(value) <= 256


def _cancel_ref(value: object) -> bool:
    if not _ref(value):
        return False
    try:
        return len(value.encode("utf-8", errors="strict")) <= 256
    except UnicodeError:
        return False


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


@dataclass(frozen=True, slots=True)
class GatewayControlGrant:
    """Installed server-side operator authorization, never supplied by a peer."""
    actor_ref: str
    account_scope_allowed: bool
    strategies: frozenset[str]
    operations: frozenset[str]

    def __post_init__(self):
        if (not _ref(self.actor_ref) or type(self.account_scope_allowed) is not bool
                or type(self.strategies) is not frozenset or any(not _ref(x) for x in self.strategies)
                or type(self.operations) is not frozenset or not self.operations
                or not self.operations <= CONTROL_OPERATIONS
                or (not self.account_scope_allowed and not self.strategies)):
            raise ValueError("INVALID_CONTROL_GRANT")


def validate_service_options(
    options: Mapping[str, object], bind_ports: tuple[str, ...]
) -> Mapping[str, object]:
    if (
        set(options) - {"role", "dispatch_enabled", "cancel_enabled", "bounded_read_after", "control_enabled", "strategy_ledger_enabled", "account_extensions_enabled", "delegation_enabled", "parent_authority_enabled", "account_facts_financial_enabled", "actor_stream_enabled", "strategy_origin_enabled", "control_cancel_enabled", "account_fact_recovery_enabled", "account_event_recovery_enabled"}
        or options.get("role") != "trading_owner"
        or type(options.get("dispatch_enabled", False)) is not bool
        or type(options.get("cancel_enabled", False)) is not bool
        or type(options.get("control_enabled", False)) is not bool
        or type(options.get("strategy_ledger_enabled", False)) is not bool
        or type(options.get("account_extensions_enabled", False)) is not bool
        or type(options.get("delegation_enabled", False)) is not bool
        or type(options.get("parent_authority_enabled", False)) is not bool
        or type(options.get("account_facts_financial_enabled", False)) is not bool
        or type(options.get("actor_stream_enabled", False)) is not bool
        or type(options.get('strategy_origin_enabled', False)) is not bool
        or type(options.get('control_cancel_enabled', False)) is not bool
        or type(options.get('account_fact_recovery_enabled', False)) is not bool
        or type(options.get('account_event_recovery_enabled', False)) is not bool
        or (options.get('account_event_recovery_enabled', False) and not options.get('account_fact_recovery_enabled', False))
        or (options.get('account_fact_recovery_enabled', False) and not (options.get('control_cancel_enabled', False) and options.get('account_facts_financial_enabled', False)))
        or (options.get('control_cancel_enabled', False) and (not options.get('control_enabled', False) or not options.get('cancel_enabled', False) or not options.get('strategy_origin_enabled', False)))
        or (options.get('strategy_origin_enabled', False) and not options.get('account_extensions_enabled', False))
        or (options.get("actor_stream_enabled", False) and not options.get("account_extensions_enabled", False))
        or (options.get("actor_stream_enabled", False) and not options.get("delegation_enabled", False))
        or (options.get("account_facts_financial_enabled", False) and not options.get("account_extensions_enabled", False))
        or (options.get("parent_authority_enabled", False) and not options.get("account_extensions_enabled", False))
        or (options.get("delegation_enabled", False) and not options.get("account_extensions_enabled", False))
        or (options.get("account_extensions_enabled", False) and not options.get("strategy_ledger_enabled", False))
        or (options.get("strategy_ledger_enabled", False) and not options.get("control_enabled", False))
        or (options.get("control_enabled", False) and not options.get("dispatch_enabled", False))
        or (options.get("cancel_enabled", False) and not options.get("dispatch_enabled", False))
        or type(options.get("bounded_read_after", False)) is not bool
        or ("bounded_read_after" in options and "service.market_source" not in bind_ports)
        or "service.direct_write" not in bind_ports
        or len(bind_ports) != len(set(bind_ports))
        or not set(bind_ports) <= {"service.direct_write", "service.account_read", "service.market_source"}
    ):
        raise ValueError("INVALID_DIRECT_OPTIONS")
    result = {
        "role": "trading_owner",
        "dispatch_enabled": options.get("dispatch_enabled", False),
        "cancel_enabled": options.get("cancel_enabled", False),
    }
    if "control_enabled" in options:
        result["control_enabled"] = options["control_enabled"]
    if "strategy_ledger_enabled" in options:
        result["strategy_ledger_enabled"] = options["strategy_ledger_enabled"]
    if "account_extensions_enabled" in options:
        result["account_extensions_enabled"] = options["account_extensions_enabled"]
    if "delegation_enabled" in options:
        result["delegation_enabled"] = options["delegation_enabled"]
    if "parent_authority_enabled" in options:
        result["parent_authority_enabled"] = options["parent_authority_enabled"]
    if "account_facts_financial_enabled" in options:
        result["account_facts_financial_enabled"] = options["account_facts_financial_enabled"]
    if "actor_stream_enabled" in options:
        result["actor_stream_enabled"] = options["actor_stream_enabled"]
    if 'strategy_origin_enabled' in options:
        result['strategy_origin_enabled'] = options['strategy_origin_enabled']
    if 'control_cancel_enabled' in options:
        result['control_cancel_enabled'] = options['control_cancel_enabled']
    if 'account_fact_recovery_enabled' in options:
        result['account_fact_recovery_enabled'] = options['account_fact_recovery_enabled']
    if 'account_event_recovery_enabled' in options:
        result['account_event_recovery_enabled'] = options['account_event_recovery_enabled']
    if "service.market_source" in bind_ports:
        result["bounded_read_after"] = options.get("bounded_read_after", False)
    return result


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


def _public_cancel(record, journal: GatewayDirectCommandJournal) -> dict[str, Any]:
    if record.account_scope != journal.account_scope or record.principal != record.target_principal:
        raise RuntimeError("INVALID_STORED_CANCEL")
    binding = GatewayCommandBinding(
        journal.journal_id, record.account_scope, record.strategy_scope, record.principal
    )
    error = None
    if record.error_code is not None and record.status == "NOT_SENT":
        error = "CANCEL_NOT_SENT"
    elif record.error_code is not None and record.status == "UNKNOWN":
        error = "CANCEL_OUTCOME_UNKNOWN"
    value = GatewayCancelRecord(
        1,
        binding,
        record.operation_id,
        record.attempt_id,
        record.request_key,
        record.target_reservation_id,
        record.target_digest,
        record.status,
        record.version,
        journal.generation,
        record.generation,
        record.generation == journal.generation,
        error,
    )
    return {"status": "OK", "record": value.to_mapping()}


def _ensure_reply_fits(context: ServiceCallContext, response: Mapping[str, Any]) -> None:
    fits = context.result_fits
    if fits is not None:
        if not callable(fits):
            raise RuntimeError("INVALID_RESULT_BUDGET")
        result = fits(response)
        if type(result) is not bool:
            raise RuntimeError("INVALID_RESULT_BUDGET")
        if not result:
            raise ServiceResultTooLargeError()


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
        reservation_owner_serialized: bool = False,
        execution_stream: object | None = None,
        execution_domain_ref: str | None = None,
        cancel_enabled: bool = False,
        control: object | None = None,
        control_grants: Mapping[str, GatewayControlGrant] | None = None,
        market_gate_required: bool = False,
        strategy_financial_enabled: bool = False,
        strategy_origin_source: object | None = None,
        market_gate_max_latency_seconds: float = 2.0,
        market_gate_simnow_relaxed: bool = False,
    ):
        if type(journal) is not GatewayDirectCommandJournal:
            raise TypeError("EXACT_DIRECT_JOURNAL_REQUIRED")
        if not isinstance(grants, Mapping) or not grants:
            raise ValueError("INVALID_DIRECT_GRANTS")
        if type(market_gate_required) is not bool or type(strategy_financial_enabled) is not bool:
            raise ValueError('INVALID_MARKET_GATE_POLICY')
        if (type(market_gate_simnow_relaxed) is not bool
                or type(market_gate_max_latency_seconds) not in (int, float)
                or not math.isfinite(market_gate_max_latency_seconds)
                or not 0 < market_gate_max_latency_seconds <= (5.0 if market_gate_simnow_relaxed else 2.0)):
            raise ValueError('INVALID_MARKET_GATE_POLICY')
        self._market_gate_max_latency_seconds = float(market_gate_max_latency_seconds)
        self._market_gate_simnow_relaxed = market_gate_simnow_relaxed
        if (type(reservation_owner_serialized) is not bool
                or (reservation_owner_serialized and (owner is None or reservation_fence is None))):
            raise ValueError("INVALID_RESERVATION_OWNER_POLICY")
        if reservation_fence is not None and not callable(reservation_fence):
            raise TypeError("INVALID_RESERVATION_FENCE")
        copied = dict(grants)
        if any(
            not _ref(key) or type(value) is not GatewayDirectGrant for key, value in copied.items()
        ):
            raise ValueError("INVALID_DIRECT_GRANTS")
        if execution_stream is not None:
            from bt_api_py.gateway_execution_stream_service import GatewayExecutionStreamService

            if (
                type(execution_stream) is not GatewayExecutionStreamService
                or owner is None
                or not execution_stream.matches(
                    journal,
                    {key: value.strategy_ref for key, value in copied.items()},
                    execution_domain_ref,
                )
            ):
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
        if type(cancel_enabled) is not bool:
            raise ValueError("INVALID_CANCEL_CONFIGURATION")
        if cancel_enabled and (owner is None or owner.supports_cancel is not True):
            raise ValueError("CANCEL_OWNER_UNAVAILABLE")
        if control is not None:
            from bt_api_gateway.direct_control import GatewayDirectControl
            if (type(control) is not GatewayDirectControl or control._journal is not journal
                    or owner is None or journal._schema_version < 10
                    or not isinstance(control_grants, Mapping) or not control_grants):
                raise ValueError("INVALID_CONTROL_BINDING")
            control_grants = dict(control_grants)
            if any(not _ref(key) or type(value) is not GatewayControlGrant for key, value in control_grants.items()):
                raise ValueError("INVALID_CONTROL_GRANTS")
        elif control_grants is not None:
            raise ValueError("INVALID_CONTROL_BINDING")
        self._control = control
        self._control_grants = MappingProxyType(control_grants or {})
        self._journal = journal
        self._cancel_journal = GatewayDirectCancelJournal(journal) if cancel_enabled else None
        self._grants = MappingProxyType(copied)
        self._owner = owner
        self._reservation_fence = reservation_fence
        self._reservation_owner_serialized = reservation_owner_serialized
        self._execution_stream = execution_stream
        self._market_gate_required = market_gate_required
        self._market_gate = None
        self._strategy_origin_source = strategy_origin_source
        self._lease_recovery = None
        self.supported_operations = (
            (SUPPORTED_OPERATIONS if owner is not None else STORAGE_OPERATIONS)
            | (EVENT_OPERATIONS if execution_stream is not None else frozenset())
            | (CANCEL_OPERATIONS if cancel_enabled else frozenset())
            | (CONTROL_OPERATIONS if control is not None else frozenset())
            | (STRATEGY_POSITION_OPERATIONS if owner is not None and journal._schema_version >= 11 else frozenset())
            | (STRATEGY_FINANCIAL_OPERATIONS if owner is not None and journal._schema_version >= 14 and strategy_financial_enabled else frozenset())
            | ((DELEGATED_STRATEGY_READ_OPERATIONS - {'direct.delegated_cancel_status'}) if owner is not None and journal._schema_version >= 12 else frozenset())
            | (frozenset({'direct.delegated_cancel_status'}) if cancel_enabled and journal._schema_version >= 12 else frozenset())
            | (DELEGATED_STRATEGY_CANCEL_OPERATIONS if cancel_enabled and journal._schema_version >= 12 else frozenset())
        )

    @property
    def account_scope(self) -> str:
        """Account scope of the bound direct-command journal."""
        return self._journal.account_scope

    def install_market_gate(self, gate):
        from bt_api_py.gateway_market_dispatch_gate import GatewayMarketDispatchGate
        from bt_api_py.gateway_owner_market import GatewayOwnerMarketProvider
        provider = getattr(getattr(gate, '_hub', None), '_provider', None)
        if (not self._market_gate_required or self._market_gate is not None
                or type(gate) is not GatewayMarketDispatchGate
                or gate._account != self._journal.account_scope or gate._generation != self._journal.generation
                or type(provider) is not GatewayOwnerMarketProvider or provider._owner is not self._owner
                or getattr(gate._native_source_check, '__self__', None) is not provider
                or getattr(gate._native_source_check, '__func__', None) is not GatewayOwnerMarketProvider.recheck_market_tick_on_native_owner):
            raise ValueError('INVALID_MARKET_GATE_BINDING')
        self._market_gate = gate

    def handle(
        self, operation: str, *, context: ServiceCallContext, arguments: Mapping[str, Any]
    ) -> Mapping[str, Any]:
        if operation == 'direct.reserve':
            admission = getattr(context, 'dispatch_admission', None)
            if self._journal._schema_version >= 16 and (
                    type(admission) is not ServiceDispatchAdmission or not admission.matches_context(context)
                    or not admission.matches_operation(operation) or not admission.matches(arguments)):
                return _reject('ADMISSION_DENIED')
            try:
                return self._handle(operation, context=context, arguments=arguments, permit=admission)
            finally:
                if type(admission) is ServiceDispatchAdmission:
                    admission.close()
        if operation in CONTROL_OPERATIONS:
            return self._handle_control(operation, context=context, arguments=arguments)
        if operation in EVENT_OPERATIONS:
            if self._execution_stream is None:
                return _reject("EVENTS_UNAVAILABLE")
            return self._execution_stream.handle(operation, context=context, arguments=arguments)
        if operation in (DELEGATED_STRATEGY_READ_OPERATIONS | DELEGATED_STRATEGY_CANCEL_OPERATIONS | {'direct.strategy_financial'}):
            admission = getattr(context, 'dispatch_admission', None)
            if (type(admission) is not ServiceDispatchAdmission or not admission.matches_context(context)
                    or not admission.matches_operation(operation) or not admission.matches(arguments)):
                return _reject('ADMISSION_DENIED')
            if operation == 'direct.delegated_cancel':
                return self._handle_delegated_cancel(context, arguments, admission)
            try:
                return self._handle(operation, context=context, arguments=arguments, permit=admission)
            finally:
                admission.close()
        if operation not in {"direct.dispatch", "direct.cancel"}:
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
            if (
                not permit.matches_operation(operation)
                or not permit.matches_context(context)
                or not permit.matches(arguments)
            ):
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

    def _handle_control(self, operation, *, context, arguments):
        from bt_api_py.gateway_direct_owner import GatewayOwnerServiceCallError
        from bt_api_py.service_registry import ServiceDispatchAdmission
        if self._control is None or type(context) is not ServiceCallContext or not isinstance(arguments, Mapping):
            return _reject("CONTROL_UNAVAILABLE")
        identity, scope = context.identity, context.scope
        grant = self._control_grants.get(context.principal_ref)
        if (identity.role is not WireRole.APPLICATION_CLIENT or identity.principal_ref != context.principal_ref
                or grant is None or identity.actor_ref != grant.actor_ref or identity.delegate_ref is not None
                or scope.account_ref != self._journal.account_scope or scope.account_refs
                or scope.manual_book_ref is not None or scope.portfolio_ref is not None
                or scope.group_id is not None or scope.leg_id is not None or operation not in grant.operations):
            return _reject("SCOPE_DENIED")
        strategy = scope.strategy_ref
        if ((strategy is None and not grant.account_scope_allowed)
                or (strategy is not None and strategy not in grant.strategies)):
            return _reject("SCOPE_DENIED")
        fields = {
            "direct.control.identity": set(),
            "direct.control.apply": {"cause_id", "mode"},
            "direct.control.resume": {"cause_id", "expected_version", "operation_id"},
            "direct.control.reconcile": {"cause_id"},
            "direct.control.status": {"cause_id"},
            "direct.control.targets": {"cause_id", "after", "limit"},
            "direct.control.list": {"after", "limit"},
            "direct.control.resume_status": {"operation_id"},
            "direct.control.target_status": {"cause_id", "operation_id"},
            "direct.control.drain_target": {"cause_id", "reservation_id"},
        }[operation]
        expected_journal_id = arguments.get('expected_journal_id')
        allowed_fields = fields | {'expected_journal_id'} if operation in {'direct.control.apply', 'direct.control.resume', 'direct.control.drain_target'} else fields
        if (set(arguments) not in (fields, allowed_fields)
                or ('expected_journal_id' in arguments and not _ref(expected_journal_id))
                or (operation not in {"direct.control.identity", "direct.control.list", "direct.control.resume_status"} and not _ref(arguments.get("cause_id")))):
            return _reject("INVALID_REQUEST")
        if operation in {"direct.control.resume_status", "direct.control.target_status"} and not _ref(arguments.get("operation_id")):
            return _reject("INVALID_REQUEST")
        if operation == "direct.control.apply" and arguments.get("mode") not in {"STOP_NEW", "FREEZE", "DRAIN"}:
            return _reject("INVALID_REQUEST")
        if operation in {"direct.control.drain_target"} or (operation == "direct.control.apply" and arguments.get("mode") == "DRAIN"):
            if self._cancel_journal is None or not self._owner.supports_cancel:
                return _reject("CANCEL_UNAVAILABLE")
        if operation == 'direct.control.drain_target' and self._journal._schema_version < 17:
            return _reject('CONTROL_CANCEL_PROOF_REQUIRED')
        if operation == "direct.control.resume" and (type(arguments.get("expected_version")) is not int
                or arguments["expected_version"] < 1 or not _ref(arguments.get("operation_id"))):
            return _reject("INVALID_REQUEST")
        if operation == "direct.control.drain_target" and not _ref(arguments.get("reservation_id")):
            return _reject("INVALID_REQUEST")
        if operation in {"direct.control.targets", "direct.control.list"} and (type(arguments.get("after")) is not str
                or (arguments["after"] and not _ref(arguments["after"]))
                or type(arguments.get("limit")) is not int or not 1 <= arguments["limit"] <= 256):
            return _reject("INVALID_REQUEST")
        permit = context.dispatch_admission
        if (type(permit) is not ServiceDispatchAdmission
                or not permit.matches_operation(operation) or not permit.matches_context(context)
                or not permit.matches(arguments)):
            return _reject("ADMISSION_DENIED")
        # Check response budget before any durable effect. UNKNOWN remains a
        # request outcome, never proof of venue acceptance/cancellation.
        _ensure_reply_fits(context, {"status": "UNKNOWN", "cause_id": arguments.get("cause_id", "")})
        control = self._control
        def scope_check(cause):
            # A contract cause (D63-59) concerns every strategy of the account: only a
            # caller authorised for the whole account sees or releases it.
            if ((strategy is None and cause['scope_kind'] not in ('account', 'instrument'))
                    or (strategy is not None and (cause['scope_kind'] != 'strategy' or cause['scope_value'] != strategy))):
                raise ValueError("SCOPE_DENIED")
        def run():
            if operation == 'direct.control.identity':
                with permit.scope() as recheck:
                    control._owned()
                    with self._journal._transaction() as db:
                        self._journal._assert_anchor(db)
                        recheck()
                        return {'status': 'OBSERVED', 'schema_version': 1,
                                'account_scope': self._journal.account_scope,
                                'journal_id': self._journal.journal_id}
            if expected_journal_id is not None and expected_journal_id != self._journal.journal_id:
                return _reject('JOURNAL_ID_MISMATCH')
            def checked_commit(recheck):
                if expected_journal_id is not None and expected_journal_id != self._journal.journal_id:
                    raise DirectCommandJournalError('JOURNAL_ID_MISMATCH')
                recheck()
            if operation == "direct.control.list":
                with permit.scope() as recheck:
                    causes = control.list_causes(scope_kind="account" if strategy is None else "strategy",
                        scope_value="" if strategy is None else strategy, after=arguments['after'], limit=arguments['limit'])
                    if strategy is None:
                        # One page over the account's own causes and its contract causes, by cause id.
                        causes = tuple(sorted(
                            causes + control.list_causes(scope_kind="instrument", after=arguments['after'],
                                                         limit=arguments['limit']),
                            key=lambda row: row['cause_id']))[:arguments['limit']]
                    recheck()
                    return {"status": "OBSERVED", "causes": list(causes)}
            if operation == "direct.control.resume_status":
                with permit.scope() as recheck:
                    cause = control.resume_status(arguments['operation_id'], scope_kind='account' if strategy is None else 'strategy',
                                                   scope_value='' if strategy is None else strategy)
                    if cause is None and strategy is None:
                        cause = control.resume_status(arguments['operation_id'], scope_kind='instrument')
                    recheck()
                    if cause is None:
                        return {'status':'NOT_FOUND','operation_id':arguments['operation_id']}
                    return {'status':'OBSERVED','cause_id':cause['cause_id'],'operation_id':arguments['operation_id'],
                            'version':cause['version'],'active':bool(cause['active'])}
            if operation == "direct.control.apply":
                with permit.scope() as recheck:
                    cause = control.apply(arguments['cause_id'], arguments['mode'], scope_kind='account' if strategy is None else 'strategy',
                        scope_value='' if strategy is None else strategy, origin='operator:' + hashlib.sha256(json.dumps([context.principal_ref, identity.actor_ref], separators=(',', ':')).encode()).hexdigest(), before_commit=lambda: checked_commit(recheck))
                scope_check(cause)
                return {"status": "OBSERVED", "cause_id": cause['cause_id'], "version": cause['version'], "active": bool(cause['active'])}
            if operation in {"direct.control.status", "direct.control.targets", "direct.control.target_status"}:
                with permit.scope() as recheck:
                    try:
                        cause = control.get(arguments['cause_id'])
                    except DirectCommandJournalError as error:
                        if error.code != 'CONTROL_NOT_FOUND':
                            raise
                        recheck()
                        result = {'status':'NOT_FOUND','cause_id':arguments['cause_id']}
                        if operation == 'direct.control.target_status':
                            result['operation_id'] = arguments['operation_id']
                        return result
                    scope_check(cause)
                    if operation == "direct.control.status":
                        result = {"status": "OBSERVED", "cause": cause}
                    elif operation == "direct.control.targets":
                        result = {"status": "OBSERVED", "targets": list(control.targets(arguments['cause_id'], after=arguments['after'], limit=arguments['limit']))}
                    else:
                        target = control.target_status(arguments['cause_id'], arguments['operation_id'])
                        result = ({'status':'NOT_FOUND','cause_id':arguments['cause_id'],'operation_id':arguments['operation_id']}
                                  if target is None else {'status':'OBSERVED','target':target})
                    recheck()
                    return result
            cause = control.get(arguments['cause_id'])
            scope_check(cause)
            if operation == "direct.control.reconcile":
                with permit.scope() as recheck:
                    changed = control.reconcile_targets(arguments['cause_id'], before_commit=recheck)
                return {"status": "OBSERVED", "cause_id": arguments['cause_id'], "changed": changed}
            if operation == "direct.control.resume":
                if cause['origin'].startswith('system:lease:'):
                    if self._lease_recovery is None:
                        return _reject("RECOVERY_REQUIRED")
                    recovered = self._lease_recovery.recover_manual_owned(arguments['cause_id'],
                        arguments['expected_version'], arguments['operation_id'],
                        admission=permit, context=context, arguments=arguments)
                    if recovered is None:
                        return _reject('RECOVERY_REQUIRED')
                    return {'status':'OBSERVED','cause_id':recovered['cause_id'],
                            'version':recovered['version'],'active':bool(recovered['active'])}
                undecided = None
                if cause['scope_kind'] == 'instrument':
                    from bt_api_py.gateway_instrument_block import contract_has_undecided_orders

                    # The operator vouches that the unattributed returns are not a strategy's. The
                    # Gateway refuses that while one of its own orders on the contract may still be
                    # at the venue with an unknown outcome: the returns may be that order's.
                    def undecided(db, scope_value=cause['scope_value']):
                        return not contract_has_undecided_orders(db, self._journal.account_scope, scope_value)
                try:
                    with permit.scope() as recheck:
                        cause = control.resume(arguments['cause_id'], expected_version=arguments['expected_version'], operation_id=arguments['operation_id'], before_commit=lambda: checked_commit(recheck),
                                               before_commit_db=undecided)
                except DirectCommandJournalError as error:
                    if undecided is None or error.code != 'CONTROL_RECOVERY_UNVERIFIED':
                        raise
                    return _reject('CONTRACT_ORDERS_UNDECIDED')
                return {"status": "OBSERVED", "cause_id": cause['cause_id'], "version": cause['version'], "active": bool(cause['active'])}
            with permit.scope() as recheck:
                # Do not send a cancel for an already observed terminal order.
                # The same native owner serializes reconciliation and claim.
                control.reconcile_targets(arguments['cause_id'], before_commit=lambda: checked_commit(recheck))
                target, token = control.claim_target(arguments['cause_id'], arguments['reservation_id'], before_commit=lambda: checked_commit(recheck))
            if token is not None:
                @contextmanager
                def target_fence(_row):
                    with permit.scope() as recheck:
                        checked_commit(recheck)
                        yield lambda: checked_commit(recheck)
                control.dispatch_claimed_cancel(target, self._owner._cancel_dispatcher, token,
                    request_fence=target_fence, requester=context.principal_ref,
                    authority_source={'kind':'operator', 'actor':context.identity.actor_ref,
                                      'registration':context.registration_id,
                                      'epoch':permit._binding.service_epoch, 'event_id':None})
            return {"status": "OBSERVED_TERMINAL" if target['state'] == 'OBSERVED_TERMINAL' else "UNKNOWN", "cause_id": arguments['cause_id'], "reservation_id": arguments['reservation_id']}
        try:
            result = self._owner.submit_service_call(run).result(timeout_seconds=1.0)
            _ensure_reply_fits(context, result)
            return result
        except GatewayOwnerServiceCallError:
            return {"status": "UNKNOWN", "cause_id": arguments.get('cause_id', '')}
        finally:
            permit.close()

    def _handle_strategy_position(self, operation, context, arguments, grant):
        from bt_api_base.market_protocol import MarketInstrumentKey
        from bt_api_py.gateway_direct_owner import GatewayOwnerServiceCallError

        if (self._owner is None or self._journal._schema_version < 11
                or context.identity.actor_ref != grant.strategy_ref
                or context.identity.delegate_ref is not None
                or set(arguments) != {"instrument", "payload"}
                or not isinstance(arguments.get("payload"), Mapping)):
            return _reject("STRATEGY_LEDGER_UNAVAILABLE")
        payload = dict(arguments["payload"])
        required = {"symbol", "exchange_id", "quantity_unit"}
        if operation == "direct.strategy_close_capacity":
            required |= {"side", "offset", "quantity"}
        allowed = required | ({"position_side"} if operation == "direct.strategy_close_capacity" else set())
        if set(payload) not in (required, allowed):
            return _reject("INVALID_REQUEST")
        try:
            instrument = MarketInstrumentKey.from_dict(arguments["instrument"])
        except Exception:
            return _reject("INVALID_REQUEST")
        if (instrument.native_symbol not in grant.symbols or payload["symbol"] != instrument.native_symbol
                or payload["exchange_id"] != instrument.venue):
            return _reject("SCOPE_DENIED")

        def read():
            if operation == "direct.strategy_position":
                result = self._journal.read_strategy_position(grant.strategy_ref, payload,
                                                               expected_instrument=instrument)
                position, capacity = result, None
            else:
                capacity = self._journal.read_strategy_close_capacity(grant.strategy_ref, payload,
                                                                       expected_instrument=instrument)
                position = capacity.position
            values = {name: format(getattr(position, name), "f") for name in (
                "long_today", "long_yesterday", "short_today", "short_yesterday")}
            values.update({"trading_day": position.trading_day,
                           "projected_buckets": position.projected_buckets})
            if capacity is not None:
                values.update({"strategy_close_available": format(capacity.available, "f"),
                               "strategy_close_reserved": format(capacity.reserved, "f")})
            return {"status": "OBSERVED", "schema_version": 1, "instrument": instrument.to_dict(),
                    "owner_generation": self._journal.generation, "strategy_position": values,
                    "provider_close_capacity": "UNKNOWN"}
        try:
            result = self._owner.submit_service_call(read).result(timeout_seconds=1.0)
            _ensure_reply_fits(context, result)
            return result
        except GatewayOwnerServiceCallError:
            return _reject("STRATEGY_LEDGER_UNAVAILABLE")

    def _delegated_cancel_projection(self, row):
        return {'account_scope':row.account_scope,'strategy_scope':row.strategy_scope,
            'requester_principal':row.principal,'target_actor_principal':row.target_principal,
            'request_key':row.request_key,'operation_id':row.operation_id,'attempt_id':row.attempt_id,
            'target_reservation_id':row.target_reservation_id,'target_digest':row.target_digest,
            'state':row.status,'version':row.version,'owner_generation':self._journal.generation,
            'attempt_generation':row.generation}

    def _delegated_cancel_grant(self, context):
        grant = self._grants.get(context.principal_ref)
        scope, identity = context.scope, context.identity
        if (self._owner is None or self._cancel_journal is None or self._journal._schema_version < 12
                or identity.role is not WireRole.STRATEGY_CLIENT or identity.principal_ref != context.principal_ref
                or identity.delegate_ref is not None or grant is None or identity.actor_ref != grant.strategy_ref
                or scope.account_ref != self._journal.account_scope or scope.strategy_ref != grant.strategy_ref
                or scope.run_id is None or scope.account_refs or scope.portfolio_ref is not None
                or scope.manual_book_ref is not None or scope.group_id is not None or scope.leg_id is not None):
            return None
        return grant

    def _handle_delegated_cancel_status(self, context, arguments, admission):
        from bt_api_py.gateway_direct_owner import GatewayOwnerServiceCallError
        grant = self._delegated_cancel_grant(context)
        if grant is None:
            return _reject('SCOPE_DENIED')
        if set(arguments) != {'operation_id'} or not _cancel_ref(arguments.get('operation_id')):
            return _reject('INVALID_REQUEST')
        def read():
            with admission.scope() as recheck:
                row = self._cancel_journal.get(grant.strategy_ref,arguments['operation_id'],context.principal_ref)
                if row.principal == row.target_principal or row.symbol not in grant.symbols:
                    raise ValueError('INVALID_DELEGATED_CANCEL')
                recheck()
                return {'status':'OK','record':self._delegated_cancel_projection(row)}
        try:
            return self._owner.submit_service_call(read).result(timeout_seconds=1.0)
        except GatewayOwnerServiceCallError:
            return _reject('CANCEL_UNAVAILABLE')

    def _handle_delegated_cancel(self, context, arguments, admission):
        from bt_api_py.gateway_direct_owner import GatewayOwnerServiceCallError
        grant = self._delegated_cancel_grant(context)
        transferred = False
        try:
            if grant is None:
                return _reject('SCOPE_DENIED')
            fields = {'request_key','reservation_id','digest','operation_id'}
            if (not isinstance(arguments, Mapping) or set(arguments) != fields
                    or not all(_cancel_ref(arguments.get(field)) for field in fields)
                    or len(arguments['digest']) != 64
                    or any(char not in '0123456789abcdef' for char in arguments['digest'])):
                return _reject('INVALID_REQUEST')
            def create():
                target = self._journal.read_delegated_strategy_command(grant.strategy_ref,arguments['request_key'],
                                                                        admission_fence=admission.scope)
                payload = json.loads(target.payload_json)
                native, _ = _identity_public(target)
                if (not _payload(payload) or payload['symbol'] not in grant.symbols or native is None
                        or target.reservation_id != arguments['reservation_id'] or target.digest != arguments['digest']
                        or target.status not in {'DISPATCHING','SENT','UNKNOWN'}):
                    raise ValueError('INVALID_DELEGATED_TARGET')
                identity = NativeOrderIdentity.from_dict(native)
                attempt = self._cancel_journal.request(grant.strategy_ref,arguments['operation_id'],context.principal_ref,
                    target.request_key,target.reservation_id,target.digest,target_identity=identity,
                    symbol=payload['symbol'],target_principal=target.principal,strategy_admission_fence=admission.scope)
                return target, attempt
            target, attempt = self._owner.submit_service_call(create).result(timeout_seconds=1.0)
            response = {'status':'OK','record':self._delegated_cancel_projection(attempt)}
            if attempt.status != 'REQUESTED':
                return {**response,'cancel_queue_state':'OBSERVED'}
            for state in ('QUEUED','ALREADY_QUEUED'):
                _ensure_reply_fits(context,{**response,'cancel_queue_state':state})
            @contextmanager
            def fence(row):
                with admission.scope() as recheck:
                    if (row.account_scope != self._journal.account_scope or row.strategy_scope != grant.strategy_ref
                            or row.principal != context.principal_ref or row.target_principal != target.principal
                            or row.operation_id != attempt.operation_id or row.request_key != target.request_key
                            or row.target_reservation_id != target.reservation_id or row.target_digest != target.digest
                            or row.version != attempt.version or row.attempt_id != attempt.attempt_id):
                        raise ValueError('DELEGATED_CANCEL_BINDING_CHANGED')
                    yield recheck
            queued = self._owner.enqueue_cancel(GatewayCancelCommand(grant.strategy_ref,attempt.operation_id,
                context.principal_ref,attempt.version),request_fence=fence,finalize=admission.close)
            transferred = True
            if queued in {'QUEUED','DUPLICATE'}:
                return {**response,'cancel_queue_state':'QUEUED' if queued == 'QUEUED' else 'ALREADY_QUEUED'}
            return _reject('CANCEL_UNAVAILABLE')
        except GatewayOwnerServiceCallError:
            return {'status':'UNKNOWN'}
        except Exception:
            return {'status':'UNKNOWN'}
        finally:
            if not transferred:
                admission.close()

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
        if operation in CANCEL_OPERATIONS:
            if identity.actor_ref != grant.strategy_ref or identity.delegate_ref is not None:
                return _reject("SCOPE_DENIED")
            return self._handle_cancel(operation, context, arguments, grant, permit)
        if operation == 'direct.strategy_financial':
            if (identity.actor_ref != grant.strategy_ref or identity.delegate_ref is not None
                    or type(permit) is not ServiceDispatchAdmission):
                return _reject('SCOPE_DENIED')
            from bt_api_py.gateway_strategy_financial_service import handle_strategy_financial
            return handle_strategy_financial(journal=self._journal, owner=self._owner, context=context,
                arguments=arguments, grant=grant)
        if operation in STRATEGY_POSITION_OPERATIONS:
            return self._handle_strategy_position(operation, context, arguments, grant)
        if operation == 'direct.delegated_cancel_status':
            return self._handle_delegated_cancel_status(context, arguments, permit)
        if operation in DELEGATED_STRATEGY_READ_OPERATIONS:
            from bt_api_py.gateway_direct_owner import GatewayOwnerServiceCallError
            if (self._owner is None or self._journal._schema_version < 12
                    or identity.actor_ref != grant.strategy_ref or identity.delegate_ref is not None
                    or set(arguments) != {'request_key'} or not _ref(arguments.get('request_key'))):
                return _reject('SCOPE_DENIED')
            key = arguments['request_key']
            try:
                record = self._owner.submit_service_call(lambda: self._journal.read_delegated_strategy_command(
                    grant.strategy_ref, key, admission_fence=permit.scope)).result(timeout_seconds=1.0)
            except GatewayOwnerServiceCallError:
                return _reject('DELEGATED_COMMAND_UNAVAILABLE')
            payload = json.loads(record.payload_json)
            if (record.account_scope != self._journal.account_scope or record.strategy_scope != grant.strategy_ref
                    or not _payload(payload) or payload.get('symbol') not in grant.symbols):
                return _reject('SCOPE_DENIED')
            return {'status':'OBSERVED', 'schema_version':1, 'account_scope':record.account_scope,
                    'strategy_scope':record.strategy_scope, 'actor_principal':record.principal,
                    'request_key':record.request_key, 'reservation_id':record.reservation_id,
                    'digest':record.digest, 'state':record.status, 'version':record.version,
                    'owner_generation':self._journal.generation, 'reservation_generation':record.generation}
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
        allowed_fields = (fields | {'market_ref'}) if operation == 'direct.dispatch' else fields
        if fields is None or set(arguments) not in (fields, allowed_fields) or not _ref(arguments.get("request_key")):
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
            if self._journal._schema_version >= 16:
                from bt_api_py.gateway_strategy_origin_source import GatewayStrategyOriginSource
                from bt_api_gateway.direct_strategy_origin import authorize_strategy_origin
                if (type(permit) is not ServiceDispatchAdmission or self._owner is None
                        or type(self._strategy_origin_source) is not GatewayStrategyOriginSource
                        or fence is None):
                    return _reject('STRATEGY_ORIGIN_UNAVAILABLE')
                def reserve_with_origin():
                    with permit.scope() as recheck:
                        def registry_recheck(current):
                            if (current is not context or not permit.matches_context(context)
                                    or not permit.matches_operation('direct.reserve') or not permit.matches(arguments)):
                                return False
                            recheck()
                            return True
                        try:
                            instrument = self._strategy_origin_source.resolve(context, order_request)
                            origin = authorize_strategy_origin(context, instrument, payload,
                                account=self._journal.account_scope, journal_id=self._journal.journal_id,
                                generation=self._journal.generation, service_epoch=permit._binding.service_epoch,
                                registry_recheck=registry_recheck, instrument_recheck=self._strategy_origin_source.recheck)
                        except Exception:
                            recheck()
                            return _reject('STRATEGY_ORIGIN_UNAVAILABLE')
                        try:
                            self._journal.reserve(grant.strategy_ref, key, context.principal_ref, payload,
                                ttl_seconds=ttl, reservation_fence=nullcontext, strategy_origin_permit=origin,
                                origin_trading_fence=lambda: _reservation_admission(fence, context, order_request))
                        except _ReservationAdmissionRejected:
                            # The trading fence refused at its entry (for example the return stream
                            # is not healthy right now): the journal rolled the reservation back,
                            # so this is a plain refusal, not an unknown outcome (I63-E141).
                            recheck()
                            return _reject('ADMISSION_DENIED')
                        except DirectCommandJournalError as error:
                            if error.code not in {'SYSTEM_ORDER_UNKNOWN', 'UNKNOWN_SCOPE_UNAVAILABLE',
                                                  'STRATEGY_LEDGER_UNAVAILABLE', 'STRATEGY_CLOSE_EXCEEDS',
                                                  'STRATEGY_ORIGIN_UNAVAILABLE', 'CONTROL_FREEZE', 'CONTROL_STOP_NEW', 'CONTROL_DRAIN'}:
                                raise
                            recheck()
                            return _reject(error.code)
                        recheck()
                        return _public(self._journal.observe(grant.strategy_ref, key, context.principal_ref), self._journal)
                try:
                    return self._owner.submit_service_call(reserve_with_origin).result(timeout_seconds=1.0)
                except Exception:
                    return {'status': 'UNKNOWN'}
            if self._reservation_owner_serialized:
                if (type(permit) is not ServiceDispatchAdmission or not permit.matches_context(context)
                        or not permit.matches_operation('direct.reserve') or not permit.matches(arguments)):
                    return _reject('ADMISSION_DENIED')
                def reserve_on_owner():
                    with permit.scope() as recheck:
                        try:
                            self._journal.reserve(
                                grant.strategy_ref, key, context.principal_ref, payload,
                                ttl_seconds=ttl,
                                reservation_fence=lambda: _reservation_admission(fence, context, order_request),
                                before_reservation_commit=recheck,
                            )
                        except _ReservationAdmissionRejected:
                            recheck()
                            return _reject('ADMISSION_DENIED')
                        except DirectCommandJournalError as error:
                            if error.code not in {'CONTROL_FREEZE', 'CONTROL_STOP_NEW', 'CONTROL_DRAIN',
                                                  'STRATEGY_LEDGER_UNAVAILABLE', 'STRATEGY_CLOSE_EXCEEDS',
                                                  'SYSTEM_ORDER_UNKNOWN', 'UNKNOWN_SCOPE_UNAVAILABLE'}:
                                raise
                            recheck()
                            return _reject(error.code)
                        recheck()
                        return _public(self._journal.observe(grant.strategy_ref, key, context.principal_ref), self._journal)
                try:
                    return self._owner.submit_service_call(reserve_on_owner).result(timeout_seconds=1.0)
                except Exception:
                    # A timed-out running transaction may have crossed its
                    # final authorized check; never infer it did not commit.
                    return {'status': 'UNKNOWN'}
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
            except DirectCommandJournalError as error:
                # These exact control checks happen before insertion inside
                # the journal transaction; other failures remain uncertain.
                if error.code in {"CONTROL_FREEZE", "CONTROL_STOP_NEW", "CONTROL_DRAIN",
                                  "STRATEGY_LEDGER_UNAVAILABLE", "STRATEGY_CLOSE_EXCEEDS",
                                  "SYSTEM_ORDER_UNKNOWN", "UNKNOWN_SCOPE_UNAVAILABLE"}:
                    return _reject(error.code)
                raise
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
            if record.status == 'RESERVED':
                # Without the owner's sealed decision-B claim port (or while it
                # closes), refuse before the owner queue or any dispatch marker;
                # the existing reservation and its original identity remain.
                refusal_of = getattr(self._owner, 'dispatch_refusal', None)
                refusal = (refusal_of() if callable(refusal_of) else
                           None if self._owner.supports_dispatch_claims()
                           else 'DISPATCH_CLAIM_UNAVAILABLE')
                if refusal is not None:
                    with admission.scope() as recheck:
                        recheck()
                        return _reject(refusal)
            if record.status != 'RESERVED':
                with admission.scope() as recheck:
                    current = self._journal.observe(
                        grant.strategy_ref, key, context.principal_ref)
                    if (current.record.reservation_id != arguments['reservation_id']
                            or current.record.digest != arguments['digest']):
                        return _reject('REQUEST_CONFLICT')
                    recheck()
                    return {**_public(current, self._journal),
                            'dispatch_queue_state': 'OBSERVED'}
            market_gate = market_permit = market_registration = None
            if record.status == 'RESERVED' and self._market_gate_required:
                if self._market_gate is None or 'market_ref' not in arguments:
                    return _reject('MARKET_GATE_UNAVAILABLE')
                try:
                    from bt_api_py.gateway_execution_stream_client import _detach_wire
                    market_gate = self._market_gate
                    market_ref = _detach_wire(arguments['market_ref'])
                    registry = admission._registry
                    with registry._read_snapshot():
                        code = registry._direct_dispatch_admission_error(context.registration_id,
                            admission._binding.service_epoch, admission._binding.instance_id,
                            context.principal_ref, context.identity, context.scope, 'direct.dispatch')
                        market_registration = registry.gateway_market_registration_current(
                            context, instance_id=admission._binding.instance_id)
                        if code is not None or market_registration is None:
                            return _reject('MARKET_GATE_UNAVAILABLE')
                        market_permit = market_gate.issue(context, market_ref,
                            market_registration_id=market_registration, request_key=key,
                            reservation_id=arguments['reservation_id'], digest=arguments['digest'])
                except Exception:
                    return _reject('MARKET_GATE_UNAVAILABLE')
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
                    def recheck_all():
                        recheck()
                        if (market_gate is not None and registry.gateway_market_registration_current(
                                context, instance_id=admission._binding.instance_id,
                                registration_id=market_registration) != market_registration):
                            raise RuntimeError('MARKET_REGISTRATION_CHANGED')
                    recheck_all()
                    yield recheck_all

            queue_state = self._owner.enqueue(
                command, request_fence=request_fence, finalize=finalize_once,
                **({'market_gate': market_gate, 'market_permit': market_permit} if market_gate is not None else {})
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

    def _handle_cancel(self, operation, context, arguments, grant, permit):
        if self._cancel_journal is None or self._owner is None:
            return _reject("CANCEL_UNAVAILABLE")
        fields = (
            {"request_key", "reservation_id", "digest", "operation_id"}
            if operation == "direct.cancel"
            else {"operation_id"}
        )
        if set(arguments) != fields or not all(
            _cancel_ref(arguments.get(field)) for field in fields
        ):
            return _reject("INVALID_REQUEST")
        if operation == "direct.cancel" and (
            len(arguments["digest"]) != 64
            or any(char not in "0123456789abcdef" for char in arguments["digest"])
        ):
            return _reject("INVALID_REQUEST")
        operation_id = arguments["operation_id"]
        try:
            existing = self._cancel_journal.get(
                grant.strategy_ref, operation_id, context.principal_ref
            )
        except DirectCommandJournalError as error:
            if error.code == "NOT_FOUND":
                existing = None
            elif error.code == "REQUEST_CONFLICT":
                return _reject("NOT_FOUND")
            else:
                raise
        if operation == "direct.cancel_status" and existing is None:
            return _reject("NOT_FOUND")
        if (
            operation == "direct.cancel"
            and existing is not None
            and (
                existing.request_key != arguments["request_key"]
                or existing.target_reservation_id != arguments["reservation_id"]
                or existing.target_digest != arguments["digest"]
            )
        ):
            return _reject("REQUEST_CONFLICT")
        request_key = (
            arguments["request_key"] if operation == "direct.cancel" else existing.request_key
        )
        try:
            target = self._journal.observe(
                grant.strategy_ref, request_key, context.principal_ref
            ).record
        except DirectCommandJournalError as error:
            if error.code in {"NOT_FOUND", "REQUEST_CONFLICT"}:
                return _reject("NOT_FOUND")
            raise
        if (
            operation == "direct.cancel"
            and existing is None
            and target.status not in {"DISPATCHING", "SENT", "UNKNOWN"}
        ):
            return _reject("INVALID_TARGET")
        try:
            payload = json.loads(target.payload_json)
            if not _payload(payload):
                raise ValueError
            order = decode_gateway_order_request(payload, self._journal.account_scope)
            if order.idempotency_key != target.request_key:
                raise ValueError
            native, _ = _identity_public(target)
            if native is None:
                raise ValueError
            identity = NativeOrderIdentity.from_dict(native)
        except Exception:
            raise RuntimeError("INVALID_STORED_COMMAND") from None
        if payload["symbol"] not in grant.symbols:
            return _reject("SCOPE_DENIED")
        if existing is not None and (
            existing.request_key != target.request_key
            or existing.target_reservation_id != target.reservation_id
            or existing.target_digest != target.digest
            or existing.target_principal != context.principal_ref
            or existing.symbol != payload["symbol"]
            or existing.target_identity.to_dict() != identity.to_dict()
        ):
            if operation == "direct.cancel":
                return _reject("REQUEST_CONFLICT")
            raise RuntimeError("INVALID_STORED_CANCEL")
        if operation == "direct.cancel_status":
            response = _public_cancel(existing, self._journal)
            _ensure_reply_fits(context, response)
            return response
        if (
            target.reservation_id != arguments["reservation_id"]
            or target.digest != arguments["digest"]
        ):
            return _reject("REQUEST_CONFLICT")
        if existing is None and target.status not in {"DISPATCHING", "SENT", "UNKNOWN"}:
            return _reject("INVALID_TARGET")
        if permit is None:
            return _reject("ADMISSION_DENIED")
        admission, transferred, finalize_once = permit
        with admission.creation_scope() as recheck:
            attempt = self._cancel_journal.request(
                grant.strategy_ref,
                operation_id,
                context.principal_ref,
                request_key,
                target.reservation_id,
                target.digest,
                target_identity=identity,
                symbol=payload["symbol"],
                before_commit=recheck,
            )
        response = _public_cancel(attempt, self._journal)
        if attempt.status != "REQUESTED":
            response["cancel_queue_state"] = "OBSERVED"
            _ensure_reply_fits(context, response)
            return response
        for state in ("QUEUED", "ALREADY_QUEUED"):
            _ensure_reply_fits(context, {**response, "cancel_queue_state": state})
        command = GatewayCancelCommand(
            grant.strategy_ref, operation_id, context.principal_ref, attempt.version
        )

        @contextmanager
        def request_fence(row):
            with admission.scope() as recheck:
                if (
                    row.account_scope != self._journal.account_scope
                    or row.strategy_scope != grant.strategy_ref
                    or row.operation_id != operation_id
                    or row.principal != context.principal_ref
                    or row.target_principal != context.principal_ref
                    or row.request_key != request_key
                    or row.target_reservation_id != target.reservation_id
                    or row.target_digest != target.digest
                    or row.target_identity.to_dict() != identity.to_dict()
                    or row.symbol != payload["symbol"]
                    or row.symbol not in grant.symbols
                    or row.version != attempt.version
                    or row.attempt_id != attempt.attempt_id
                    or row.generation != attempt.generation
                ):
                    raise RuntimeError("DIRECT_CANCEL_BINDING_CHANGED")
                yield recheck

        queue_state = self._owner.enqueue_cancel(
            command, request_fence=request_fence, finalize=finalize_once
        )
        transferred[0] = True
        if queue_state in {"QUEUED", "DUPLICATE"}:
            response["cancel_queue_state"] = (
                "QUEUED" if queue_state == "QUEUED" else "ALREADY_QUEUED"
            )
            return response
        if queue_state == "UNSUPPORTED":
            return _reject("CANCEL_UNAVAILABLE")
        if queue_state in {"CONFLICT", "FULL", "CLOSING", "BUSY"}:
            return _reject("CANCEL_QUEUE_" + queue_state)
        raise RuntimeError("INVALID_CANCEL_QUEUE_STATE")
