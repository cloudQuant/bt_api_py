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
import logging
import sys
import threading
import time
from collections.abc import Callable, Mapping
from contextlib import contextmanager, nullcontext
from dataclasses import dataclass, replace
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
from bt_api_gateway.control_wire import (
    CONTROL_GRANT_KINDS,
    CONTROL_MODES,
    CONTROL_OPERATION_FIELDS,
    CONTROL_OPERATIONS,
    CONTROL_ORIGIN_PREFIXES,
    CONTROL_RUNTIME_OPERATIONS,
    CONTROL_WIRE_SCHEMA_VERSION,
    CONTROL_WRITE_OPERATIONS,
)
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

_LOG = logging.getLogger(__name__)

STORAGE_OPERATIONS = frozenset({"direct.reserve", "direct.release", "direct.command_status"})
SUPPORTED_OPERATIONS = STORAGE_OPERATIONS | {"direct.dispatch"}
CANCEL_OPERATIONS = frozenset({"direct.cancel", "direct.cancel_status"})
# CONTROL_WRITE_OPERATIONS, CONTROL_OPERATIONS, CONTROL_MODES and
# CONTROL_GRANT_KINDS are the frozen wire vocabulary imported above; the names
# stay importable from this module because existing callers use it.

STRATEGY_POSITION_OPERATIONS = frozenset({"direct.strategy_position", "direct.strategy_close_capacity"})
INSTRUMENT_STATUS_OPERATIONS = frozenset({"direct.instrument_status"})
STRATEGY_FINANCIAL_OPERATIONS = frozenset({'direct.strategy_financial'})
ACCOUNT_COMPARISON_OPERATIONS = frozenset({"account.strategy_net_comparison"})
# Account-wide reads of every strategy's funds and book (G64-06), for an account operator only.
ACCOUNT_STRATEGY_READ_OPERATIONS = frozenset({"account.strategy_financial", "account.strategy_book"})
ACCOUNT_OBSERVER_OPERATIONS = frozenset({"account.events.read_after", "account.events.progress", "account.events.ack"})
ACCOUNT_FACTS_OPERATIONS = frozenset({"account.facts.read_after", "account.facts.read_after.v2", "account.facts.progress", "account.facts.ack", "account.facts.status", "account.facts.snapshot_info", "account.facts.snapshot_read", "account.facts.snapshot_confirm"})
ACCOUNT_ORDER_CUT_OPERATIONS = frozenset({'account.order_cut.current.v1', 'account.order_cut.query_page.v1', 'account.order_cut.receipt_page.v1'})
ACCOUNT_CANCEL_ATTEMPT_OPERATIONS = frozenset({'account.cancel_attempts.read_after', 'account.cancel_attempts.progress', 'account.cancel_attempts.ack'})
DELEGATED_STRATEGY_READ_OPERATIONS = frozenset({"direct.delegated_command_status", "direct.delegated_cancel_status"})
DELEGATED_STRATEGY_CANCEL_OPERATIONS = frozenset({"direct.delegated_cancel"})
DELEGATED_WRITE_OPERATIONS = frozenset({"direct.delegated_reserve", "direct.delegated_dispatch"})
RISK_FIRST_RESERVE_OPERATIONS = frozenset({"direct.risk_first_reserve.v2"})
DELEGATE_ACTOR_OPERATIONS = frozenset({'direct.delegate_actor_status', 'direct.delegate_actor_cancel', 'direct.delegate_actor_cancel_status', 'direct.delegate_actor_release', 'direct.delegate_actor_release_status'})
DELEGATE_ACTOR_V2_OPERATIONS = frozenset({'direct.delegate_actor_status_v2', 'direct.delegate_actor_release_status_v2'})
PARENT_OPERATIONS = frozenset({'direct.parent.register', 'direct.parent.query', 'direct.parent.cancel', 'direct.parent.operation'})
PARENT_SOURCE_OPERATIONS = frozenset({'direct.parent.source'})
PARENT_V2_OPERATIONS = frozenset({'direct.parent.register_v2', 'direct.parent.operation_v2', 'direct.parent.cancel_v2'})
# 65.03a item ①: parent fault cause and its release; source-bound and Execution-only.
PARENT_FAULT_OPERATIONS = frozenset({'direct.parent.fault', 'direct.parent.recover'})
PARENT_ACCEPTANCE_OPERATIONS = frozenset({'direct.parent.acceptance_order'})
PARENT_WRITE_OPERATIONS = frozenset({'direct.parent.register', 'direct.parent.cancel', 'direct.parent.register_v2',
                                     'direct.parent.cancel_v2'}) | PARENT_FAULT_OPERATIONS
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
    | MARKET_OPERATIONS | CONTROL_OPERATIONS | STRATEGY_POSITION_OPERATIONS | INSTRUMENT_STATUS_OPERATIONS | STRATEGY_FINANCIAL_OPERATIONS | ACCOUNT_COMPARISON_OPERATIONS | ACCOUNT_STRATEGY_READ_OPERATIONS | ACCOUNT_OBSERVER_OPERATIONS | ACCOUNT_FACTS_OPERATIONS | ACCOUNT_ORDER_CUT_OPERATIONS | ACCOUNT_CANCEL_ATTEMPT_OPERATIONS | DELEGATED_STRATEGY_READ_OPERATIONS | DELEGATED_STRATEGY_CANCEL_OPERATIONS | DELEGATED_WRITE_OPERATIONS | RISK_FIRST_RESERVE_OPERATIONS | DELEGATE_ACTOR_OPERATIONS | DELEGATE_ACTOR_V2_OPERATIONS | DELEGATE_ACTOR_STREAM_OPERATIONS | PARENT_OPERATIONS | PARENT_SOURCE_OPERATIONS | PARENT_V2_OPERATIONS | PARENT_FAULT_OPERATIONS | PARENT_ACCEPTANCE_OPERATIONS
)
WRITE_OPERATIONS = frozenset(
    {"direct.reserve", "direct.release", "direct.dispatch", "direct.cancel", "direct.delegate_actor_cancel", "direct.delegate_actor_release"} | CONTROL_WRITE_OPERATIONS | DELEGATED_STRATEGY_CANCEL_OPERATIONS | DELEGATED_WRITE_OPERATIONS | RISK_FIRST_RESERVE_OPERATIONS | PARENT_WRITE_OPERATIONS
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
class GatewayRuntimeControlProof:
    """The explicit D66-08 proof installed with a Runtime control grant.

    A Runtime principal is a limited proxy, never an operator. Its grant is
    accepted only with all of the following, installed server-side:

    * ``delegation_id``: a stable identity of this operator-to-Runtime
      delegation. It names the delegation in the audit line and in a replayed
      request, so two delegations are never confused.
    * ``operator_ref``: the trusted operator on whose active request the
      Runtime acts. It is a comparison/audit value, never authorization read
      from a payload; the composition refuses a proof naming an operator that
      is not installed in the same trusted bundle.
    * ``expires_at``: an absolute, finite expiry in the trusted host wall clock
      (the same clock the service uses for ``time.time``; not a journal TTL).
      There is no default; a grant without it or past it is refused.
    * ``revoked``: a revoked proof is refused. Revocation is also implemented
      by replacing or removing the installed grant: the handler re-checks that
      the mapping still holds the exact grant object it authorized.

    Replay is the durable journal's operation id: the same cause and same
    release operation return the recorded result and never a second effect.
    """
    delegation_id: str
    operator_ref: str
    expires_at: float
    revoked: bool = False

    def __post_init__(self) -> None:
        if (not _ref(self.delegation_id) or not _ref(self.operator_ref)
                or type(self.expires_at) not in (int, float)
                or isinstance(self.expires_at, bool) or not math.isfinite(self.expires_at)
                or type(self.revoked) is not bool):
            raise ValueError("INVALID_RUNTIME_CONTROL_PROOF")


@dataclass(frozen=True, slots=True)
class GatewayControlGrant:
    """Installed server-side control authorization, never supplied by a peer.

    ``kind`` names who holds it, and with it the origin of the causes it applies:
    ``operator`` (a person, ``operator:<hash>``), ``risk`` (the Risk service,
    ``risk:<hash>``, G64-03) or ``runtime`` (the Runtime service's limited proxy,
    ``runtime:<hash>``, D66-08). ``modes`` are the modes ``direct.control.apply``
    may use; ``instrument_scope`` lets an account-wide holder name one contract.
    A ``risk`` grant never holds ``direct.control.resume``: Risk never releases a
    cause (D64-21). A ``runtime`` grant is strategy-scoped, holds only
    ``direct.control.apply``, ``direct.control.status`` and
    ``direct.control.resume``, and requires an explicit
    :class:`GatewayRuntimeControlProof`; it never acquires operator authority.
    """
    actor_ref: str
    account_scope_allowed: bool
    strategies: frozenset[str]
    operations: frozenset[str]
    kind: str = "operator"
    modes: frozenset[str] = CONTROL_MODES
    instrument_scope: bool = False
    runtime_proof: GatewayRuntimeControlProof | None = None

    def __post_init__(self):
        if (not _ref(self.actor_ref) or type(self.account_scope_allowed) is not bool
                or type(self.strategies) is not frozenset or any(not _ref(x) for x in self.strategies)
                or type(self.operations) is not frozenset or not self.operations
                or not self.operations <= CONTROL_OPERATIONS
                or (not self.account_scope_allowed and not self.strategies)
                or type(self.kind) is not str or self.kind not in CONTROL_GRANT_KINDS
                or type(self.modes) is not frozenset or not self.modes or not self.modes <= CONTROL_MODES
                or type(self.instrument_scope) is not bool
                or (self.instrument_scope and not self.account_scope_allowed)
                or (self.kind == "risk" and "direct.control.resume" in self.operations)):
            raise ValueError("INVALID_CONTROL_GRANT")
        if self.kind == "runtime":
            # D66-08: a Runtime is a strategy-scoped limited proxy. It never
            # holds an account-wide or contract scope, never acquires the
            # operator's read/list/reconcile/cancel operations, and only runs
            # with an explicit, unexpired proof.
            if (self.runtime_proof is None
                    or type(self.runtime_proof) is not GatewayRuntimeControlProof
                    or not self.operations <= CONTROL_RUNTIME_OPERATIONS
                    or self.account_scope_allowed
                    or not self.strategies
                    or self.instrument_scope):
                raise ValueError("INVALID_CONTROL_GRANT")
        elif self.runtime_proof is not None:
            raise ValueError("INVALID_CONTROL_GRANT")


def validate_service_options(
    options: Mapping[str, object], bind_ports: tuple[str, ...]
) -> Mapping[str, object]:
    if (
        set(options) - {"role", "dispatch_enabled", "cancel_enabled", "bounded_read_after", "control_enabled", "strategy_ledger_enabled", "account_extensions_enabled", "delegation_enabled", "parent_authority_enabled", "parent_acceptance_order_enabled", "parent_original_runs_enabled", "account_facts_financial_enabled", "actor_stream_enabled", "actor_source_v2_enabled", "strategy_origin_enabled", "control_cancel_enabled", "account_fact_recovery_enabled", "account_event_recovery_enabled", "cancel_attempt_stream_enabled"}
        or options.get("role") != "trading_owner"
        or type(options.get("dispatch_enabled", False)) is not bool
        or type(options.get("cancel_enabled", False)) is not bool
        or type(options.get("control_enabled", False)) is not bool
        or type(options.get("strategy_ledger_enabled", False)) is not bool
        or type(options.get("account_extensions_enabled", False)) is not bool
        or type(options.get("delegation_enabled", False)) is not bool
        or type(options.get("parent_authority_enabled", False)) is not bool
        or type(options.get("parent_acceptance_order_enabled", False)) is not bool
        or (options.get("parent_acceptance_order_enabled", False) and not options.get("parent_authority_enabled", False))
        or type(options.get("parent_original_runs_enabled", False)) is not bool
        or (options.get("parent_original_runs_enabled", False) and not options.get("parent_authority_enabled", False))
        or type(options.get("account_facts_financial_enabled", False)) is not bool
        or type(options.get("actor_stream_enabled", False)) is not bool
        or type(options.get("actor_source_v2_enabled", False)) is not bool
        or (options.get("actor_source_v2_enabled", False) and not (options.get("delegation_enabled", False) and options.get("cancel_enabled", False)))
        or type(options.get('strategy_origin_enabled', False)) is not bool
        or type(options.get('control_cancel_enabled', False)) is not bool
        or type(options.get('account_fact_recovery_enabled', False)) is not bool
        or type(options.get('account_event_recovery_enabled', False)) is not bool
        or type(options.get('cancel_attempt_stream_enabled', False)) is not bool
        or (options.get('cancel_attempt_stream_enabled', False) and not options.get('cancel_enabled', False))
        or (options.get('cancel_attempt_stream_enabled', False) and 'service.account_read' not in bind_ports)
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
        or not set(bind_ports) <= {"service.direct_write", "service.account_read", "service.market_source", "service.calendar_read", "service.instrument_product_read"}
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
    if "parent_acceptance_order_enabled" in options:
        result["parent_acceptance_order_enabled"] = options["parent_acceptance_order_enabled"]
    if "parent_original_runs_enabled" in options:
        result["parent_original_runs_enabled"] = options["parent_original_runs_enabled"]
    if "account_facts_financial_enabled" in options:
        result["account_facts_financial_enabled"] = options["account_facts_financial_enabled"]
    if "actor_stream_enabled" in options:
        result["actor_stream_enabled"] = options["actor_stream_enabled"]
    if "actor_source_v2_enabled" in options:
        result["actor_source_v2_enabled"] = options["actor_source_v2_enabled"]
    if 'strategy_origin_enabled' in options:
        result['strategy_origin_enabled'] = options['strategy_origin_enabled']
    if 'control_cancel_enabled' in options:
        result['control_cancel_enabled'] = options['control_cancel_enabled']
    if 'account_fact_recovery_enabled' in options:
        result['account_fact_recovery_enabled'] = options['account_fact_recovery_enabled']
    if 'account_event_recovery_enabled' in options:
        result['account_event_recovery_enabled'] = options['account_event_recovery_enabled']
    if 'cancel_attempt_stream_enabled' in options:
        result['cancel_attempt_stream_enabled'] = options['cancel_attempt_stream_enabled']
    if "service.market_source" in bind_ports:
        result["bounded_read_after"] = options.get("bounded_read_after", False)
    return result


def _reject(code: str) -> dict[str, str]:
    return {"status": "REQUEST_REJECTED", "code": code}


def _instrument_ref(value: object) -> bool:
    """Whether a control request names one contract exactly as its scope value is stored."""
    from bt_api_gateway.direct_control import instrument_scope_value

    if type(value) is not str:
        return False
    venue, separator, symbol = value.partition(":")
    try:
        return bool(separator) and instrument_scope_value(venue, symbol) == value
    except DirectCommandJournalError:
        return False


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
        # The trusted host wall clock used only for a Runtime proof's absolute
        # ``expires_at`` (a configured calendar time, not a journal TTL).
        control_clock: Callable[[], float] | None = None,
        market_gate_required: bool = False,
        strategy_financial_enabled: bool = False,
        strategy_origin_source: object | None = None,
        market_gate_max_latency_seconds: float = 2.0,
        market_gate_simnow_relaxed: bool = False,
        market_gate_allow_stale_quote: bool = False,
        risk_only_submit_strategies: frozenset[str] = frozenset(),
        instrument_status_service=None,
    ):
        if type(journal) is not GatewayDirectCommandJournal:
            raise TypeError("EXACT_DIRECT_JOURNAL_REQUIRED")
        if instrument_status_service is not None:
            from bt_api_py.gateway_instrument_status_service import GatewayInstrumentStatusService
            if (type(instrument_status_service) is not GatewayInstrumentStatusService
                    or instrument_status_service._owner is not owner
                    or instrument_status_service._journal is not journal):
                raise ValueError('INVALID_INSTRUMENT_STATUS_BINDING')
        if not isinstance(grants, Mapping) or not grants:
            raise ValueError("INVALID_DIRECT_GRANTS")
        # D63-37 (optional, empty by default): these strategies' SUBMIT belongs to
        # their Risk delegate only; their own reserve and dispatch are refused.
        if (type(risk_only_submit_strategies) is not frozenset
                or any(not _ref(item) for item in risk_only_submit_strategies)
                or not risk_only_submit_strategies <= {
                    value.strategy_ref for value in grants.values() if type(value) is GatewayDirectGrant}):
            raise ValueError("INVALID_DIRECT_GRANTS")
        self._risk_only_submit = risk_only_submit_strategies
        if type(market_gate_required) is not bool or type(strategy_financial_enabled) is not bool:
            raise ValueError('INVALID_MARKET_GATE_POLICY')
        if (type(market_gate_simnow_relaxed) is not bool
                or type(market_gate_max_latency_seconds) not in (int, float)
                or not math.isfinite(market_gate_max_latency_seconds)
                or not 0 < market_gate_max_latency_seconds <= (5.0 if market_gate_simnow_relaxed else 2.0)):
            raise ValueError('INVALID_MARKET_GATE_POLICY')
        # The stale-quote opt-in needs the SimNow-relaxed mode; the gate
        # constructor re-checks the same pair, so a mismatched binding fails
        # at composition instead of widening the gate silently.
        if market_gate_allow_stale_quote is True and market_gate_simnow_relaxed is not True:
            raise ValueError('INVALID_MARKET_GATE_POLICY')
        self._market_gate_max_latency_seconds = float(market_gate_max_latency_seconds)
        self._market_gate_simnow_relaxed = market_gate_simnow_relaxed
        self._market_gate_allow_stale_quote = market_gate_allow_stale_quote
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
        if control_clock is not None and not callable(control_clock):
            raise TypeError("INVALID_CONTROL_CLOCK")
        # A Runtime proof's expiry is an absolute calendar time from the
        # trusted host. The journal clock is monotonic and cannot compare it.
        self._control_clock = control_clock or time.time
        self._control = control
        # The installed control grants are read on the owner thread while a
        # proof refresh (D66-08 continuation) may replace one runtime grant's
        # proof object.  Both paths go through this lock so the object-identity
        # recheck in ``_handle_control`` never races a replacement.
        self._control_grants_lock = threading.Lock()
        self._control_grants_dict: dict[str, GatewayControlGrant] = dict(control_grants or {})
        self._control_grants = MappingProxyType(self._control_grants_dict)
        self._journal = journal
        self._cancel_journal = GatewayDirectCancelJournal(journal) if cancel_enabled else None
        self._grants = MappingProxyType(copied)
        self._owner = owner
        self._instrument_status_service = self._original_instrument_status_service = instrument_status_service
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
            | (INSTRUMENT_STATUS_OPERATIONS if instrument_status_service is not None else frozenset())
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

    def refresh_runtime_proof(
        self, principal_ref: str, expires_at: object, *, operator_ref: str
    ) -> dict[str, Any]:
        """Refresh a Runtime grant's control proof on an operator's explicit act.

        D66-08 continuation: a Runtime proof carries an absolute, finite
        ``expires_at``.  Once it passes, ``pause``/``resume``/``DRAIN`` are
        refused ``RUNTIME_PROOF_EXPIRED`` (fail-closed).  The only lawful
        renewal is this explicit, operator-authorized replacement of the
        installed grant's proof; nothing renews automatically and an expired
        proof still refuses until a fresh, valid expiry is installed.

        Guards (all fail closed, before any durable effect):
        * ``principal_ref`` must name an installed ``runtime`` grant whose
          current proof is not revoked and whose trusted operator matches
          ``operator_ref`` exactly;
        * ``expires_at`` must be a finite number strictly in the future on the
          trusted host clock (``self._control_clock``), never the past, never a
          non-finite value; a proof cannot be made permanent;
        * the delegation id and operator are preserved from the installed
          proof, so a refresh can only extend the wall-clock window, never
          widen scope or re-target the delegation.

        The replacement is the same object-identity mechanism used for
        revocation: the handler re-checks ``self._control_grants.get(principal)
        is not grant``, so an in-flight call under the old grant fails closed
        once it is swapped out.
        """
        if not _ref(principal_ref):
            return _reject("INVALID_REQUEST")
        with self._control_grants_lock:
            grant = self._control_grants_dict.get(principal_ref)
            if grant is None or grant.kind != "runtime":
                return _reject("SCOPE_DENIED")
            proof = grant.runtime_proof
            if (type(proof) is not GatewayRuntimeControlProof or proof.revoked):
                return _reject("RUNTIME_PROOF_UNAVAILABLE")
            if proof.operator_ref != operator_ref:
                return _reject("SCOPE_DENIED")
            if (
                type(expires_at) not in (int, float)
                or isinstance(expires_at, bool)
                or not math.isfinite(expires_at)
            ):
                return _reject("INVALID_REQUEST")
            try:
                now = self._control_clock()
            except Exception:  # noqa: BLE001 - an unreadable clock proves nothing
                return _reject("RUNTIME_PROOF_UNAVAILABLE")
            if (
                type(now) not in (int, float)
                or isinstance(now, bool)
                or not math.isfinite(now)
            ):
                return _reject("RUNTIME_PROOF_UNAVAILABLE")
            if float(expires_at) <= now:
                return _reject("INVALID_REQUEST")
            new_proof = replace(proof, expires_at=float(expires_at))
            new_grant = replace(grant, runtime_proof=new_proof)
            self._control_grants_dict[principal_ref] = new_grant
            self._control_grants = MappingProxyType(self._control_grants_dict)
        _LOG.warning(
            "Runtime control proof refresh: principal %s delegation %s operator %s "
            "expires_at %.3f",
            principal_ref, proof.delegation_id, operator_ref, float(expires_at),
        )
        return {"status": "OBSERVED", "principal_ref": principal_ref,
                "delegation_id": proof.delegation_id, "expires_at": float(expires_at)}

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
        strategy_argument = (strategy is None and 'strategy' in arguments
                             and operation in {'direct.control.identity', 'direct.control.cancel_scope',
                                               'direct.control.cancel_scope_status'})
        if ((strategy is None and not grant.account_scope_allowed and not strategy_argument)
                or (strategy is not None and strategy not in grant.strategies)):
            return _reject("SCOPE_DENIED")
        if grant.kind == "runtime":
            # D66-08: a Runtime acts only as a limited proxy under an explicit,
            # server-installed proof. Anything unproven is refused before any
            # read or durable effect, with a finite code.
            proof = grant.runtime_proof
            clock = self._control_clock
            if (type(proof) is not GatewayRuntimeControlProof or proof.revoked
                    or not callable(clock)):
                return _reject("RUNTIME_PROOF_UNAVAILABLE")
            try:
                now = clock()
            except Exception:
                return _reject("RUNTIME_PROOF_UNAVAILABLE")
            if (type(now) not in (int, float) or isinstance(now, bool)
                    or not math.isfinite(now)):
                return _reject("RUNTIME_PROOF_UNAVAILABLE")
            if now >= proof.expires_at:
                return _reject("RUNTIME_PROOF_EXPIRED")
        fields = set(CONTROL_OPERATION_FIELDS[operation])
        expected_journal_id = arguments.get('expected_journal_id')
        expected_generation = arguments.get('expected_generation')
        allowed_fields = fields | {'expected_journal_id'} if operation in {'direct.control.apply', 'direct.control.resume', 'direct.control.drain_target', 'direct.control.cancel_scope'} else fields
        # G64-02: one contract of the account, named "<venue>:<native symbol>", instead of the
        # registration's own scope; only for an account-wide registration whose grant allows it.
        instrument = arguments.get('instrument')
        if 'instrument' in arguments and operation in {'direct.control.apply', 'direct.control.cancel_scope'}:
            fields, allowed_fields = fields | {'instrument'}, allowed_fields | {'instrument'}
        # An account-scoped registration can request a particular strategy,
        # but only the independently installed server-side strategy grant
        # authorizes that narrowing. The account grant alone is insufficient.
        if 'strategy' in arguments and operation in {'direct.control.identity', 'direct.control.cancel_scope',
                                                       'direct.control.cancel_scope_status'}:
            fields, allowed_fields = fields | {'strategy'}, allowed_fields | {'strategy'}
            if (scope.strategy_ref is not None or 'instrument' in arguments
                    or not _ref(arguments['strategy'])):
                return _reject('INVALID_REQUEST')
            if arguments['strategy'] not in grant.strategies:
                return _reject('SCOPE_DENIED')
            strategy = arguments['strategy']
            # Explicit account-registration strategy operations use the exact
            # journal and owner generation previously read through identity.
            if operation in {'direct.control.cancel_scope', 'direct.control.cancel_scope_status'}:
                fields = fields | {'expected_journal_id', 'expected_generation'}
                allowed_fields = allowed_fields | {'expected_journal_id', 'expected_generation'}
        # Historical reads are a distinct, explicit contract.  They still
        # require the current account registration and strategy grant, as
        # well as the originally recorded cause generation.  Neither a missing
        # historical operation nor an identity mismatch proves NOT_SENT.
        history_requested = 'history_version' in arguments or 'original_generation' in arguments
        if history_requested:
            if (not strategy_argument or operation != 'direct.control.cancel_scope_status'
                    or type(arguments.get('history_version')) is not int
                    or arguments['history_version'] != 1
                    or not _ref(arguments.get('original_generation'))):
                return _reject('INVALID_REQUEST')
            fields = fields | {'history_version', 'original_generation'}
            allowed_fields = allowed_fields | {'history_version', 'original_generation'}
        if (set(arguments) not in (fields, allowed_fields)
                or ('expected_journal_id' in arguments and not _ref(expected_journal_id))
                or ('expected_generation' in arguments and not _ref(expected_generation))
                or (operation not in {"direct.control.identity", "direct.control.list", "direct.control.resume_status", "direct.control.cancel_scope", "direct.control.cancel_scope_status"} and not _ref(arguments.get("cause_id")))):
            return _reject("INVALID_REQUEST")
        if operation in {"direct.control.resume_status", "direct.control.target_status", "direct.control.cancel_scope", "direct.control.cancel_scope_status"} and not _ref(arguments.get("operation_id")):
            return _reject("INVALID_REQUEST")
        if operation == "direct.control.apply" and arguments.get("mode") not in {"STOP_NEW", "FREEZE", "DRAIN"}:
            return _reject("INVALID_REQUEST")
        # G64-03: the modes this grant may apply, and a contract only where the grant names it.
        if operation == "direct.control.apply" and arguments["mode"] not in grant.modes:
            return _reject("SCOPE_DENIED")
        if 'instrument' in arguments:
            if not grant.instrument_scope or strategy is not None:
                return _reject("SCOPE_DENIED")
            if not _instrument_ref(instrument):
                return _reject("INVALID_REQUEST")
        if operation in {"direct.control.drain_target", "direct.control.cancel_scope"} or (operation == "direct.control.apply" and arguments.get("mode") == "DRAIN"):
            if self._cancel_journal is None or not self._owner.supports_cancel:
                return _reject("CANCEL_UNAVAILABLE")
        if operation in {'direct.control.drain_target', 'direct.control.cancel_scope'} and self._journal._schema_version < 17:
            return _reject('CONTROL_CANCEL_PROOF_REQUIRED')
        if operation == "direct.control.resume" and (type(arguments.get("expected_version")) is not int
                or arguments["expected_version"] < 1 or not _ref(arguments.get("operation_id"))):
            return _reject("INVALID_REQUEST")
        if operation == "direct.control.drain_target" and not _ref(arguments.get("reservation_id")):
            return _reject("INVALID_REQUEST")
        if operation in {"direct.control.targets", "direct.control.list", "direct.control.cancel_scope_status"} and (type(arguments.get("after")) is not str
                or (arguments["after"] and not _ref(arguments["after"]))
                or type(arguments.get("limit")) is not int or not 1 <= arguments["limit"] <= 256):
            return _reject("INVALID_REQUEST")
        if operation == "direct.control.cancel_scope" and (type(arguments.get("limit")) is not int
                                                           or not 1 <= arguments["limit"] <= 256):
            return _reject("INVALID_REQUEST")
        control = self._control
        scoped_identity_binding = strategy_argument and operation in {
            'direct.control.identity', 'direct.control.cancel_scope', 'direct.control.cancel_scope_status'
        }
        if scoped_identity_binding:
            try:
                journal = self._journal
                journal_id = journal.journal_id
                generation = journal.generation
                owner = self._owner
                guard = control._guard
            except Exception:
                return _reject('CONTROL_UNAVAILABLE')
            if not _ref(journal_id) or not _ref(generation) or guard is None:
                return _reject('CONTROL_UNAVAILABLE')
        permit = context.dispatch_admission
        if (type(permit) is not ServiceDispatchAdmission
                or not permit.matches_operation(operation) or not permit.matches_context(context)
                or not permit.matches(arguments)):
            return _reject("ADMISSION_DENIED")
        # Check response budget before any durable effect. UNKNOWN remains a
        # request outcome, never proof of venue acceptance/cancellation.
        _ensure_reply_fits(context, {"status": "UNKNOWN", "cause_id": arguments.get("cause_id", "")})
        # This operator as the journal names it: the origin of a cause it applies and, on a
        # journal with the release audit, the releaser of a cause it releases. One derivation
        # from the authenticated registration for both.
        # A Risk principal's causes are named "risk:" (G64-03) and a Runtime principal's
        # "runtime:" (D66-08), each with the same derivation.
        operator_ref = CONTROL_ORIGIN_PREFIXES[grant.kind] + hashlib.sha256(json.dumps(
            [context.principal_ref, identity.actor_ref], separators=(',', ':')).encode()).hexdigest()
        if 'instrument' in arguments:
            requested_scope = ('instrument', instrument)
        else:
            requested_scope = ('account', '') if strategy is None else ('strategy', strategy)

        def with_releaser(reply, cause):
            # Present only when the journal stores who released the cause.
            if 'releaser' in cause:
                reply['releaser'] = cause['releaser']
            return reply

        def audit(action, cause, operation_id=None):
            # The journal stores a cause's origin when it is applied and, only with the release
            # audit installed, who released it. This line names who did what in every case:
            # authenticated principal and actor, registration, cause and operation.
            # It is a warning so that it appears in the operator's log at the default level.
            # A Runtime action additionally names its stable delegation and the trusted
            # operator on whose request it acts (D66-08: audited as "operator via Runtime").
            proof = grant.runtime_proof if grant.kind == 'runtime' else None
            _LOG.warning(
                "%s control %s: cause %s (%s, scope %s%s) version %s by principal %s actor %s "
                "registration %s%s%s", 'Risk' if grant.kind == 'risk' else
                ('Runtime' if grant.kind == 'runtime' else 'Operator'), action,
                cause['cause_id'], cause['mode'], cause['scope_kind'],
                ' ' + cause['scope_value'] if cause['scope_value'] else '', cause['version'],
                context.principal_ref, identity.actor_ref, context.registration_id,
                '' if operation_id is None else ' operation ' + operation_id,
                '' if proof is None else ' delegation ' + proof.delegation_id
                + ' operator ' + proof.operator_ref)

        def scope_check(cause):
            # A contract cause (D63-59) concerns every strategy of the account: only a
            # caller authorised for the whole account sees or releases it.
            if ((strategy is None and cause['scope_kind'] not in ('account', 'instrument'))
                    or (strategy is not None and (cause['scope_kind'] != 'strategy' or cause['scope_value'] != strategy))):
                raise ValueError("SCOPE_DENIED")

        def checked_strategy_binding(recheck):
            # The expected id is a comparison value, never authorization. Bind
            # the read to the exact control/journal and the current held owner.
            def check_binding():
                if (self._control is not control or self._journal is not journal or self._owner is not owner
                        or getattr(control, '_journal', None) is not journal
                        or getattr(control, '_guard', None) is not guard
                        or getattr(control, '_generation', None) != generation
                        or journal.journal_id != journal_id or journal.generation != generation
                        or journal.account_scope != scope.account_ref
                        or self._control_grants.get(context.principal_ref) is not grant
                        or operation not in grant.operations or strategy not in grant.strategies
                        or not permit.matches_operation(operation) or not permit.matches_context(context)
                        or not permit.matches(arguments)):
                    raise DirectCommandJournalError('CONTROL_SCOPE_DENIED')
                if (operation != 'direct.control.identity' and expected_journal_id != journal_id):
                    raise DirectCommandJournalError('JOURNAL_ID_MISMATCH')
                if operation != 'direct.control.identity' and expected_generation != generation:
                    raise DirectCommandJournalError('JOURNAL_GENERATION_MISMATCH')
            check_binding()
            control._owned()
            recheck()
            check_binding()

        def checked_cause_generation(cause):
            # A matching journal can survive an owner restart. The current
            # owner must not reconcile or claim an operation frozen by an
            # earlier generation, even when the request carries current IDs.
            if (scoped_identity_binding
                    and (type(cause.get('generation')) is not str
                         or cause['generation'] != expected_generation)):
                raise DirectCommandJournalError('CONTROL_CAUSE_GENERATION_MISMATCH')

        def run():
            if operation == 'direct.control.identity':
                with permit.scope() as recheck:
                    if scoped_identity_binding:
                        checked_strategy_binding(recheck)
                    control._owned()
                    with self._journal._transaction() as db:
                        self._journal._assert_anchor(db)
                        if scoped_identity_binding:
                            checked_strategy_binding(recheck)
                        else:
                            recheck()
                        result = {'status': 'OBSERVED',
                                  'schema_version': CONTROL_WIRE_SCHEMA_VERSION,
                                  'account_scope': self._journal.account_scope,
                                  'journal_id': self._journal.journal_id}
                        if scoped_identity_binding:
                            result['journal_generation'] = generation
                    if scoped_identity_binding:
                        checked_strategy_binding(recheck)
                    return result
            if expected_journal_id is not None and expected_journal_id != self._journal.journal_id:
                return _reject('JOURNAL_ID_MISMATCH')
            if scoped_identity_binding and expected_generation != generation:
                return _reject('JOURNAL_GENERATION_MISMATCH')
            def checked_commit(recheck):
                if scoped_identity_binding:
                    checked_strategy_binding(recheck)
                    return
                if expected_journal_id is not None and expected_journal_id != self._journal.journal_id:
                    raise DirectCommandJournalError('JOURNAL_ID_MISMATCH')
                if (self._control_grants.get(context.principal_ref) is not grant
                        or operation not in grant.operations
                        or (strategy is None and not grant.account_scope_allowed)
                        or (strategy is not None and strategy not in grant.strategies)
                        or not permit.matches_context(context) or not permit.matches(arguments)):
                    raise DirectCommandJournalError('CONTROL_SCOPE_DENIED')
                recheck()

            def cancel_scope():
                # G64-04: freeze, once, the scope's orders that may still be working, then cancel
                # at most ``limit`` of the not yet claimed ones, each once by its original identity.
                # Nothing is blocked. The same operation id again continues with what is left.
                with permit.scope() as recheck:
                    if scoped_identity_binding:
                        checked_strategy_binding(recheck)
                    row = control.cancel_scope(arguments['operation_id'], scope_kind=requested_scope[0],
                        scope_value=requested_scope[1], origin=operator_ref,
                        before_commit=lambda: checked_commit(recheck))
                    if scoped_identity_binding:
                        checked_cause_generation(row)
                        checked_strategy_binding(recheck)
                scope_check(row)
                if row['origin'] != operator_ref:
                    raise ValueError('SCOPE_DENIED')
                if strategy is not None:
                    # Validate the complete target set before reconciling or
                    # claiming any item, including targets beyond the page.
                    with permit.scope() as recheck:
                        found = control.cancel_scope_status(arguments['operation_id'], after='', limit=1)
                        checked_commit(recheck)
                    if found is None:
                        raise ValueError('SCOPE_DENIED')
                    stored, _, _, _ = found
                    checked_cause_generation(stored)
                    scope_check(stored)
                    if stored['origin'] != operator_ref or stored['cause_id'] != row['cause_id']:
                        raise ValueError('SCOPE_DENIED')
                with permit.scope() as recheck:
                    # An order already seen ended needs no cancel.
                    control.reconcile_targets(row['cause_id'], before_commit=lambda: checked_commit(recheck))

                @contextmanager
                def target_fence(_row):
                    with permit.scope() as recheck:
                        checked_commit(recheck)
                        yield lambda: checked_commit(recheck)
                issued, after = 0, ''
                while issued < arguments['limit']:
                    page = control.targets(row['cause_id'], after=after, limit=256)
                    if strategy is not None and any(target['strategy'] != strategy for target in page):
                        raise ValueError('SCOPE_DENIED')
                    for target in page:
                        if issued >= arguments['limit']:
                            break
                        if target['state'] != 'PENDING':
                            continue
                        with permit.scope() as recheck:
                            claimed, token = control.claim_target(row['cause_id'], target['reservation_id'],
                                                                  before_commit=lambda: checked_commit(recheck))
                        if token is None:
                            continue
                        if (claimed['cause_id'] != row['cause_id']
                                or claimed['reservation_id'] != target['reservation_id']
                                or (strategy is not None and claimed['strategy'] != strategy)):
                            raise ValueError('SCOPE_DENIED')
                        control.dispatch_claimed_cancel(claimed, self._owner._cancel_dispatcher, token,
                            request_fence=target_fence, requester=context.principal_ref,
                            authority_source={'kind': 'operator', 'actor': identity.actor_ref,
                                              'registration': context.registration_id,
                                              'epoch': permit._binding.service_epoch, 'event_id': None})
                        issued += 1
                    if len(page) < 256:
                        break
                    after = page[-1]['reservation_id']
                audit(f'cancel scope ({issued} cancel(s) issued)', row, arguments['operation_id'])
                with permit.scope() as recheck:
                    _, _, counts, _ = control.cancel_scope_status(arguments['operation_id'], after='', limit=1)
                    checked_commit(recheck)
                return {'status': 'OBSERVED', 'operation_id': arguments['operation_id'],
                        'scope_kind': row['scope_kind'], 'scope_value': row['scope_value'],
                        'issued': issued, 'counts': counts}
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
                    return with_releaser({'status':'OBSERVED','cause_id':cause['cause_id'],'operation_id':arguments['operation_id'],
                                          'version':cause['version'],'active':bool(cause['active'])}, cause)
            if operation == "direct.control.apply":
                with permit.scope() as recheck:
                    cause = control.apply(arguments['cause_id'], arguments['mode'], scope_kind=requested_scope[0],
                        scope_value=requested_scope[1], origin=operator_ref, before_commit=lambda: checked_commit(recheck))
                scope_check(cause)
                audit('apply', cause)
                return {"status": "OBSERVED", "cause_id": cause['cause_id'], "version": cause['version'], "active": bool(cause['active'])}
            if operation == "direct.control.cancel_scope":
                return cancel_scope()
            if operation == "direct.control.cancel_scope_status":
                with permit.scope() as recheck:
                    if scoped_identity_binding:
                        checked_strategy_binding(recheck)
                    found = control.cancel_scope_status(arguments['operation_id'], after=arguments['after'],
                                                        limit=arguments['limit'])
                    checked_commit(recheck)
                    if scoped_identity_binding:
                        checked_strategy_binding(recheck)
                    if history_requested:
                        from bt_api_gateway.direct_control import cancel_scope_cause_id

                        # Check the frozen row before exposing even counts or
                        # NOT_FOUND. The source is derived from the authenticated
                        # principal/actor, never from a caller-provided origin.
                        if found is None:
                            return {'status': 'UNKNOWN', 'cause_id': ''}
                        row, page, counts, remaining_at_cursor = found
                        if (row.get('account') != journal.account_scope
                                or row.get('cause_id') != cancel_scope_cause_id(arguments['operation_id'])
                                or row.get('mode') != 'CANCEL_ONLY'
                                or row.get('scope_kind') != 'strategy'
                                or row.get('scope_value') != strategy
                                or row.get('origin') != operator_ref
                                or row.get('generation') != arguments['original_generation']
                                or any(target.get('strategy') != strategy for target in page)):
                            return {'status': 'UNKNOWN', 'cause_id': ''}
                        # The ordinary status projection can classify a
                        # claimed target with no dispatched attempt as
                        # NOT_SENT because the active owner serializes that
                        # path. A previous owner's crash invalidates that
                        # same-owner reasoning. Preserve the stored rows and
                        # conservatively project those observations UNKNOWN.
                        historical_page = []
                        for target in page:
                            item = dict(target)
                            if item.get('state') == 'NOT_SENT':
                                item['state'] = 'UNKNOWN'
                                item['cancel'] = None
                            historical_page.append(item)
                        historical_counts = dict(counts)
                        historical_counts['UNKNOWN'] += historical_counts['NOT_SENT']
                        historical_counts['NOT_SENT'] = 0
                        checked_strategy_binding(recheck)
                        return {'status': 'OBSERVED', 'operation_id': arguments['operation_id'],
                                'scope_kind': 'strategy', 'scope_value': strategy, 'origin': operator_ref,
                                'targets': historical_page, 'counts': historical_counts, 'after': arguments['after'],
                                'limit': arguments['limit'], 'remaining_at_cursor': remaining_at_cursor,
                                'history_version': 1, 'journal_id': journal_id,
                                'original_generation': row['generation'], 'current_generation': generation,
                                'read_only': True, 'retry_permitted': False}
                if found is None:
                    return {'status': 'NOT_FOUND', 'operation_id': arguments['operation_id']}
                row, page, counts, remaining_at_cursor = found
                checked_cause_generation(row)
                scope_check(row)
                # The origin was captured when the operation was created from
                # an authenticated registration. A different actor with the
                # same operation ID and scope must not read those targets.
                if row['origin'] != operator_ref:
                    return _reject('SCOPE_DENIED')
                if strategy is not None and any(target['strategy'] != strategy for target in page):
                    return _reject('SCOPE_DENIED')
                return {'status': 'OBSERVED', 'operation_id': arguments['operation_id'], 'scope_kind': row['scope_kind'],
                        'scope_value': row['scope_value'], 'origin': row['origin'], 'targets': list(page),
                        'counts': counts, 'after': arguments['after'], 'limit': arguments['limit'],
                        'remaining_at_cursor': remaining_at_cursor}
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
            # D66-08: a Runtime principal releases only the causes it applied
            # itself (its own "runtime:" origin). Operator, Risk, system,
            # Monitor and another Runtime's causes stay untouched; a replay
            # with the same operation id still returns the recorded result.
            if (operation == "direct.control.resume" and grant.kind == 'runtime'
                    and cause['origin'] != operator_ref):
                return _reject('SCOPE_DENIED')
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
                        admission=permit, context=context, arguments=arguments, releaser=operator_ref)
                    if recovered is None:
                        return _reject('RECOVERY_REQUIRED')
                    audit('release', recovered, arguments['operation_id'])
                    return with_releaser({'status':'OBSERVED','cause_id':recovered['cause_id'],
                                          'version':recovered['version'],'active':bool(recovered['active'])}, recovered)
                undecided = None
                from bt_api_py.gateway_instrument_block import ORIGIN_PREFIX
                # Only the unresolved-return watcher's contract cause (D63-59) is vouched for by its
                # release; a contract STOP_NEW, FREEZE or DRAIN of an operator or Risk (G64-01) is not.
                if cause['scope_kind'] == 'instrument' and cause['origin'].startswith(ORIGIN_PREFIX):
                    from bt_api_py.gateway_instrument_block import contract_has_undecided_orders

                    # The operator vouches that the unattributed returns are not a strategy's. The
                    # Gateway refuses that while one of its own orders on the contract may still be
                    # at the venue with an unknown outcome: the returns may be that order's.
                    def undecided(db, scope_value=cause['scope_value']):
                        return not contract_has_undecided_orders(db, self._journal.account_scope, scope_value)
                try:
                    with permit.scope() as recheck:
                        cause = control.resume(arguments['cause_id'], expected_version=arguments['expected_version'], operation_id=arguments['operation_id'], before_commit=lambda: checked_commit(recheck),
                                               before_commit_db=undecided, releaser=operator_ref)
                except DirectCommandJournalError as error:
                    if undecided is None or error.code != 'CONTROL_RECOVERY_UNVERIFIED':
                        raise
                    return _reject('CONTRACT_ORDERS_UNDECIDED')
                audit('release', cause, arguments['operation_id'])
                return with_releaser({"status": "OBSERVED", "cause_id": cause['cause_id'], "version": cause['version'], "active": bool(cause['active'])}, cause)
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
                audit('cancel of target ' + arguments['reservation_id'], cause)
            return {"status": "OBSERVED_TERMINAL" if target['state'] == 'OBSERVED_TERMINAL' else "UNKNOWN", "cause_id": arguments['cause_id'], "reservation_id": arguments['reservation_id']}
        try:
            result = (owner if scoped_identity_binding else self._owner).submit_service_call(run).result(timeout_seconds=1.0)
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
        if operation in ("direct.reserve", "direct.dispatch") and grant.strategy_ref in self._risk_only_submit:
            # D63-37: SUBMIT for this strategy was granted to its Risk delegate only.
            return _reject("SUBMIT_NOT_GRANTED")
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
        if operation in INSTRUMENT_STATUS_OPERATIONS:
            if identity.actor_ref != grant.strategy_ref or identity.delegate_ref is not None:
                return _reject('SCOPE_DENIED')
            if (self._instrument_status_service is None or
                    self._instrument_status_service is not self._original_instrument_status_service):
                return _reject('INSTRUMENT_STATUS_UNAVAILABLE')
            result = self._instrument_status_service.handle(context, arguments, grant)
            _ensure_reply_fits(context, result)
            return result
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
            bound_instrument = getattr(self._journal, '_instrument_binding_enabled', False) is True
            replay = False
            try:
                old = self._journal.observe(grant.strategy_ref, key, context.principal_ref)
            except DirectCommandJournalError as error:
                if error.code == "REQUEST_CONFLICT":
                    return _reject("REQUEST_CONFLICT")
                if error.code != "NOT_FOUND":
                    if bound_instrument:
                        return {'status': 'UNKNOWN'}
                    raise
            else:
                if old.record.digest != digest:
                    return _reject("REQUEST_CONFLICT")
                if not bound_instrument:
                    return _public(old, self._journal)
                # The journal checks the retained complete key and origin under
                # the current owner permit, including an idempotent replay.
                replay = True
            fence = self._reservation_fence
            if self._journal._schema_version >= 16:
                from bt_api_py.gateway_strategy_origin_source import GatewayStrategyOriginSource
                from bt_api_gateway.direct_strategy_origin import authorize_strategy_origin
                if (type(permit) is not ServiceDispatchAdmission or self._owner is None
                        or type(self._strategy_origin_source) is not GatewayStrategyOriginSource
                        or fence is None):
                    return {'status': 'UNKNOWN'} if bound_instrument else _reject('STRATEGY_ORIGIN_UNAVAILABLE')
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
                            # On a bound path a concurrent request may have
                            # inserted the key after the preflight observation.
                            if bound_instrument:
                                return {'status': 'UNKNOWN'}
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
                            if bound_instrument and (replay or error.code == 'STRATEGY_ORIGIN_UNAVAILABLE'):
                                return {'status': 'UNKNOWN'}
                            if error.code not in {'SYSTEM_ORDER_UNKNOWN', 'UNKNOWN_SCOPE_UNAVAILABLE',
                                                  'STRATEGY_LEDGER_UNAVAILABLE', 'STRATEGY_CLOSE_EXCEEDS',
                                                  'STRATEGY_ORIGIN_UNAVAILABLE', 'CONTROL_FREEZE', 'CONTROL_STOP_NEW', 'CONTROL_DRAIN'}:
                                raise
                            recheck()
                            return _reject(error.code)
                        observation = self._journal.observe(grant.strategy_ref, key, context.principal_ref)
                        recheck()
                        if bound_instrument:
                            origin.recheck()
                        return _public(observation, self._journal)
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
            if (record.status == 'RESERVED'
                    and getattr(self._journal, '_instrument_binding_enabled', False) is True):
                # The source callbacks are not yet covered writers of the
                # decision-B claim vector. Preserve the reservation until a
                # source with auditable claim coverage is installed.
                return _reject('STRATEGY_ORIGIN_CLAIM_UNAVAILABLE')
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
                    origin = None
                    if getattr(self._journal, '_instrument_binding_enabled', False) is True:
                        from bt_api_py.gateway_strategy_origin_source import GatewayStrategyOriginSource
                        from bt_api_gateway.direct_strategy_origin import authorize_strategy_origin
                        if type(self._strategy_origin_source) is not GatewayStrategyOriginSource:
                            raise RuntimeError('STRATEGY_ORIGIN_UNAVAILABLE')
                        def registry_recheck(current):
                            if (current is not context or not admission.matches_context(context)
                                    or not admission.matches_operation('direct.dispatch')
                                    or not admission.matches(arguments)):
                                return False
                            recheck()
                            return True
                        instrument = self._strategy_origin_source.resolve(context, current_order)
                        origin = authorize_strategy_origin(context, instrument, current_payload,
                            account=self._journal.account_scope, journal_id=self._journal.journal_id,
                            generation=self._journal.generation, service_epoch=admission._binding.service_epoch,
                            registry_recheck=registry_recheck, instrument_recheck=self._strategy_origin_source.recheck)
                    def recheck_all():
                        recheck()
                        if origin is not None:
                            origin.recheck()
                        if (market_gate is not None and registry.gateway_market_registration_current(
                                context, instance_id=admission._binding.instance_id,
                                registration_id=market_registration) != market_registration):
                            raise RuntimeError('MARKET_REGISTRATION_CHANGED')
                    if origin is not None:
                        recheck_all.strategy_origin_permit = origin
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
