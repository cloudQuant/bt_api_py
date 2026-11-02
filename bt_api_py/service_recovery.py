"""Public facade for the SDK-owned durable service-recovery journal.

Backtrader and other host frameworks should depend on this module only.  The
SQLite journal, recovery flow, and typed attestation port remain implementation
details of :mod:`bt_api_execution`; this facade creates and owns the journal,
validates durable manifests and prior order state, and issues neutral projection
DTOs through its public flow port.

The host supplies one stateless attestor for its own execution-context facts.
Its two methods return frozen DTO-like values with a ``facts`` mapping; event
attestations also carry ``data_name``, ``instrument``, and
``commission_currency``.  The SDK bridge translates those values to the
execution package's versioned port without importing the host framework.
"""

from __future__ import annotations

import math
from collections.abc import Callable, Iterator, Mapping
from copy import deepcopy
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation
from importlib import import_module
from pathlib import Path
from types import MappingProxyType
from threading import RLock
from typing import Any, Optional, Protocol


class ServiceRecoveryFacadeError(RuntimeError):
    """A finite configuration or lifecycle error from this public facade."""

    def __init__(self, code: str) -> None:
        self.code = code
        super().__init__(code)


class ServiceRecoveryProjectionError(ServiceRecoveryFacadeError):
    """A durable journal value could not be validated for a public projection."""


_PROJECTION_ISSUER = object()


class _RecoveryProjection(Mapping[str, Any]):
    """Immutable SDK-issued mapping projection with copy-on-read values."""

    __slots__ = ("_values",)

    def __init__(self, values: Mapping[str, Any], *, issuer: object = None) -> None:
        if issuer is not _PROJECTION_ISSUER:
            raise TypeError("recovery projections are issued by the SDK facade")
        object.__setattr__(self, "_values", MappingProxyType(deepcopy(dict(values))))

    def __setattr__(self, name: str, value: object) -> None:
        raise AttributeError("recovery projections are immutable")

    def __getitem__(self, key: str) -> Any:
        return deepcopy(self._values[key])

    def __iter__(self) -> Iterator[str]:
        return iter(self._values)

    def __len__(self) -> int:
        return len(self._values)


class ServiceRecoveryManifest(_RecoveryProjection):
    """Validated, neutral projection of one SDK durable submit manifest."""

    __slots__ = ()

    @property
    def intent_id(self) -> str:
        return self["intent_id"]

    @property
    def submit_fingerprint(self) -> str:
        return self["submit_fingerprint"]

    @property
    def submit_payload(self) -> Mapping[str, Any]:
        return self["submit_payload"]

    @property
    def recovery_binding(self) -> Mapping[str, Any]:
        return self["recovery_binding"]


class ServiceRecoveryOrderState(_RecoveryProjection):
    """Validated neutral order facts for restoring a prior durable intent."""

    __slots__ = ()

    @property
    def intent_id(self) -> str:
        return self["intent_id"]

    @property
    def submit_fingerprint(self) -> str:
        return self["submit_fingerprint"]

    @property
    def recovery_binding(self) -> Mapping[str, Any]:
        return self["recovery_binding"]

    @property
    def strategy_position_ref(self) -> Optional["StrategyPositionRefV1"]:
        return _strategy_position_ref_from_binding(self.recovery_binding)


@dataclass(frozen=True)
class StrategyPositionRefV1:
    """Opaque SDK-issued identity for one Strategy-local position."""

    value: str

    def __post_init__(self) -> None:
        if not is_strategy_position_ref(self.value):
            raise ValueError("invalid strategy position reference")


@dataclass(frozen=True)
class ServiceRecoveryPreparationV1:
    """SDK-validated durable submit preparation and any prior order state."""

    recovery_binding: Mapping[str, Any]
    strategy_position_ref: Optional[StrategyPositionRefV1]
    prior_state: Optional[ServiceRecoveryOrderState]

    def __post_init__(self) -> None:
        if not isinstance(self.recovery_binding, Mapping):
            raise ValueError("invalid recovery binding")
        if self.strategy_position_ref is not None and type(
            self.strategy_position_ref
        ) is not StrategyPositionRefV1:
            raise ValueError("invalid strategy position reference")
        if self.prior_state is not None and type(self.prior_state) is not ServiceRecoveryOrderState:
            raise ValueError("invalid prior order state")
        object.__setattr__(
            self, "recovery_binding", MappingProxyType(deepcopy(dict(self.recovery_binding)))
        )


@dataclass(frozen=True)
class ServiceRecoveryOwnerProjectionV1:
    """Opaque host-owned payload associated with one SDK recovery receipt."""

    receipt_ref: str
    payload: Mapping[str, Any]
    strategy_position_key_ref: Optional[str] = None
    strategy_position_owner_payload: Optional[Mapping[str, Any]] = None


@dataclass(frozen=True)
class ServiceRecoveryOwnerProjectionRecordV1:
    """SDK-issued owner projection with its durable commit sequence."""

    receipt_ref: str
    payload: Mapping[str, Any]
    sequence: Optional[int]
    legacy: bool


class ServiceRecoveryFlowPort(Protocol):
    """Canonical neutral service-recovery flow contract owned by this SDK."""

    @property
    def namespace(self) -> Any: ...

    @property
    def consumer_token(self) -> Optional[str]: ...

    @property
    def startup_cursor(self) -> Optional[int]: ...

    @property
    def owner_position_reconciliation(self) -> Optional[Mapping[str, Any]]: ...

    def configure_event_context_resolver(self, resolver: Callable[..., Any]) -> None: ...

    def bind(self, namespace: Any) -> None: ...

    def acquire_consumer_lease(self, owner_token: str) -> None: ...

    def renew_consumer_lease(self, owner_token: str) -> None: ...

    def release_consumer_lease(self, owner_token: str) -> None: ...

    def ensure_strategy_genesis(self, *, trade_ledger_empty: bool) -> Mapping[str, Any]: ...

    def initialize(
        self,
        *,
        owner_book_reader: Optional[Callable[[], Mapping[str, Any]]] = None,
        page_limit: int = 128,
        max_pages: int = 64,
    ) -> Mapping[str, Any]: ...

    def refresh_cut(self) -> Mapping[str, Any]: ...

    def refresh_events(
        self,
        *,
        callback: Optional[Callable[[Any], Any]] = None,
        page_limit: int = 128,
        max_pages: int = 64,
        callback_limit: int = 32,
    ) -> Mapping[str, Any]: ...

    def history(self) -> tuple[Mapping[str, Any], ...]: ...

    def baseline(self) -> Optional[Mapping[str, Any]]: ...

    def manifest(self, intent_id: str) -> Optional[ServiceRecoveryManifest]: ...

    def intent_execution_context(self, intent_id: str) -> Optional[Mapping[str, Any]]: ...

    def claim_callback(self, receipt_id: str) -> Any: ...

    def acknowledge_callback(self, receipt_id: str) -> None: ...

    def fill_projection(self, receipt_id: str) -> Optional[Mapping[str, Any]]: ...

    def persist_owner_projection(
        self, projection: ServiceRecoveryOwnerProjectionV1
    ) -> Mapping[str, Any]: ...

    def owner_projection(
        self, receipt_id: str
    ) -> Optional[ServiceRecoveryOwnerProjectionRecordV1]: ...

    def owner_projections(self) -> tuple[ServiceRecoveryOwnerProjectionRecordV1, ...]: ...

    def prepare_submit(
        self,
        *,
        intent_id: str,
        submit_payload: Mapping[str, Any],
        recovery_binding: Mapping[str, Any],
        strategy_position_owner_payload: Optional[Mapping[str, Any]],
        intent_context: Optional[Mapping[str, Any]],
    ) -> ServiceRecoveryPreparationV1: ...

    def strategy_position_ref_for_key(
        self, local_position_key_ref: str
    ) -> Optional[StrategyPositionRefV1]: ...

    def strategy_position_owner_payload(
        self, strategy_position_ref: StrategyPositionRefV1
    ) -> Optional[Mapping[str, Any]]: ...

    def intent_submit_fingerprint(self, intent_id: str) -> Any: ...

    def prepare_cancel(
        self,
        *,
        cancel_id: str,
        target_intent_id: str,
        provider_order_id: str,
        metadata_version: str,
    ) -> None: ...


class ServiceRecoveryFacadePort(Protocol):
    """Canonical neutral factory contract for SDK-owned recovery journals."""

    def create_flow(
        self,
        *,
        client: object,
        event_version: int,
        require_owner_baseline: bool,
        read_failure_code: Callable[[BaseException], str],
    ) -> ServiceRecoveryFlowPort: ...

    def create_namespace(
        self,
        *,
        account_ref: str,
        strategy_ref: str,
        logical_run_id: str,
    ) -> Any: ...


def recovery_facade_is_usable(value: Any) -> bool:
    """Return whether *value* exposes the canonical SDK facade factories."""

    return all(
        callable(getattr(value, name, None))
        for name in ("create_flow", "create_namespace")
    )


def recovery_flow_is_usable(value: Any) -> bool:
    """Return whether *value* exposes the required canonical recovery flow."""

    required_methods = (
        "configure_event_context_resolver",
        "bind",
        "acquire_consumer_lease",
        "renew_consumer_lease",
        "release_consumer_lease",
        "ensure_strategy_genesis",
        "initialize",
        "refresh_cut",
        "refresh_events",
        "history",
        "baseline",
        "manifest",
        "intent_execution_context",
        "claim_callback",
        "acknowledge_callback",
        "fill_projection",
        "persist_owner_projection",
        "owner_projection",
        "owner_projections",
        "prepare_submit",
        "strategy_position_ref_for_key",
        "strategy_position_owner_payload",
        "intent_submit_fingerprint",
        "prepare_cancel",
    )
    required_attributes = (
        "namespace",
        "consumer_token",
        "startup_cursor",
        "owner_position_reconciliation",
    )
    return all(callable(getattr(value, name, None)) for name in required_methods) and all(
        hasattr(value, name) for name in required_attributes
    )


def _projection_reference(value: object) -> bool:
    return (
        type(value) is str
        and 1 <= len(value) <= 128
        and all(
            character.isascii() and (character.isalnum() or character in "._:-")
            for character in value
        )
    )


def _projection_identifier(value: object) -> bool:
    return (
        type(value) is str
        and 1 <= len(value) <= 192
        and value == value.strip()
        and all(character.isascii() and character.isprintable() for character in value)
    )


def _projection_digest(value: object) -> bool:
    return (
        type(value) is str
        and len(value) == 64
        and all(character in "0123456789abcdef" for character in value)
    )


_STRATEGY_POSITION_BINDING_SCHEMA = "bt_api.strategy-position-binding.v1"
_STRATEGY_POSITION_REF_PREFIX = "btsp1_"
_LOCAL_POSITION_KEY_REF_PREFIX = "btlocal1_"


def is_strategy_position_ref(value: object) -> bool:
    return (
        type(value) is str
        and value.startswith(_STRATEGY_POSITION_REF_PREFIX)
        and len(value) == len(_STRATEGY_POSITION_REF_PREFIX) + 64
        and all(character in "0123456789abcdef" for character in value[len(_STRATEGY_POSITION_REF_PREFIX) :])
    )


def is_local_strategy_position_key_ref(value: object) -> bool:
    return (
        type(value) is str
        and value.startswith(_LOCAL_POSITION_KEY_REF_PREFIX)
        and len(value) == len(_LOCAL_POSITION_KEY_REF_PREFIX) + 64
        and all(character in "0123456789abcdef" for character in value[len(_LOCAL_POSITION_KEY_REF_PREFIX) :])
    )


def _validated_recovery_binding(value: object, *, final: bool) -> dict[str, Any]:
    if not isinstance(value, Mapping):
        raise _projection_error("RECOVERY_ORDER_BINDING_INVALID")
    if frozenset(value) == {"data_name", "tradeid"}:
        if (
            type(value.get("data_name")) is not str
            or not _projection_identifier(value.get("data_name"))
            or type(value.get("tradeid")) is not int
        ):
            raise _projection_error("RECOVERY_ORDER_BINDING_INVALID")
        return {"data_name": value["data_name"], "tradeid": value["tradeid"]}
    required = {"schema", "local_position_key_ref"}
    allowed = required | {"strategy_position_ref"}
    if (
        frozenset(value) not in {frozenset(required), frozenset(allowed)}
        or value.get("schema") != _STRATEGY_POSITION_BINDING_SCHEMA
        or not is_local_strategy_position_key_ref(value.get("local_position_key_ref"))
        or (
            "strategy_position_ref" in value
            and not is_strategy_position_ref(value.get("strategy_position_ref"))
        )
        or (final and "strategy_position_ref" not in value)
    ):
        raise _projection_error("RECOVERY_ORDER_BINDING_INVALID")
    return dict(value)


def _requested_recovery_binding_is_valid(value: Mapping[str, Any], submit_payload: Mapping[str, Any]) -> bool:
    try:
        normalized = _validated_recovery_binding(value, final=False)
    except ServiceRecoveryProjectionError:
        return False
    if normalized.get("schema") == _STRATEGY_POSITION_BINDING_SCHEMA:
        # A host-supplied reference is only a consistency hint. The journal
        # issues or resolves the durable reference inside its transaction.
        return submit_payload.get("position_effect") in {
            "OPEN", "CLOSE", "CLOSE_TODAY", "CLOSE_YESTERDAY"
        }
    return submit_payload.get("position_effect") in {
        "OPEN", "CLOSE", "CLOSE_TODAY", "CLOSE_YESTERDAY"
    }


def _recovery_binding_matches(requested: Mapping[str, Any], issued: Mapping[str, Any]) -> bool:
    try:
        expected = _validated_recovery_binding(requested, final=False)
        actual = _validated_recovery_binding(issued, final=True)
    except ServiceRecoveryProjectionError:
        return False
    if expected.get("schema") != _STRATEGY_POSITION_BINDING_SCHEMA:
        return expected == actual
    return (
        actual.get("schema") == _STRATEGY_POSITION_BINDING_SCHEMA
        and expected.get("local_position_key_ref") == actual.get("local_position_key_ref")
        and (
            expected.get("strategy_position_ref") is None
            or expected.get("strategy_position_ref") == actual.get("strategy_position_ref")
        )
    )


def _strategy_position_ref_from_binding(
    binding: Mapping[str, Any],
) -> Optional[StrategyPositionRefV1]:
    if binding.get("schema") != _STRATEGY_POSITION_BINDING_SCHEMA:
        return None
    value = binding.get("strategy_position_ref")
    if not is_strategy_position_ref(value):
        return None
    return StrategyPositionRefV1(value)


def _projection_error(code: str) -> ServiceRecoveryProjectionError:
    return ServiceRecoveryProjectionError(code)


def _validated_manifest(
    value: object,
    *,
    intent_id: str,
    logical_run_id: str,
) -> ServiceRecoveryManifest:
    if not isinstance(value, Mapping):
        raise _projection_error("RECOVERY_ORDER_MANIFEST_INVALID")
    payload = value.get("submit_payload")
    binding = value.get("recovery_binding")
    fingerprint = value.get("submit_fingerprint")
    intent_fingerprint = value.get("intent_fingerprint")
    scope_key = value.get("scope_key")
    if (
        type(value.get("intent_id")) is not str
        or value.get("intent_id") != intent_id
        or type(value.get("logical_run_id")) is not str
        or value.get("logical_run_id") != logical_run_id
        or not isinstance(payload, Mapping)
        or not isinstance(binding, Mapping)
        or not _projection_reference(fingerprint)
        or (intent_fingerprint is not None and not _projection_reference(intent_fingerprint))
        or (scope_key is not None and not _projection_identifier(scope_key))
    ):
        raise _projection_error("RECOVERY_ORDER_MANIFEST_INVALID")
    normalized_binding = _validated_recovery_binding(binding, final=True)
    normalized = {
        "intent_id": intent_id,
        "logical_run_id": logical_run_id,
        "submit_fingerprint": fingerprint,
        "submit_payload": dict(payload),
        "scope_key": scope_key,
        "intent_fingerprint": intent_fingerprint,
        "recovery_binding": normalized_binding,
    }
    return ServiceRecoveryManifest(normalized, issuer=_PROJECTION_ISSUER)


def _validated_owner_projection_record(
    value: object, *, receipt_id: Optional[str] = None
) -> ServiceRecoveryOwnerProjectionRecordV1:
    if not isinstance(value, Mapping) or frozenset(value) != {
        "receipt_ref",
        "payload",
        "sequence",
        "legacy",
    }:
        raise ServiceRecoveryFacadeError("RECOVERY_OWNER_PROJECTION_INVALID")
    raw_receipt = value.get("receipt_ref")
    payload = value.get("payload")
    sequence = value.get("sequence")
    legacy = value.get("legacy")
    if (
        not _projection_digest(raw_receipt)
        or (receipt_id is not None and raw_receipt != receipt_id)
        or not isinstance(payload, Mapping)
        or type(legacy) is not bool
        or (legacy and sequence is not None)
        or (not legacy and (type(sequence) is not int or sequence <= 0))
    ):
        raise ServiceRecoveryFacadeError("RECOVERY_OWNER_PROJECTION_INVALID")
    try:
        copied_payload = deepcopy(dict(payload))
    except Exception:
        raise ServiceRecoveryFacadeError("RECOVERY_OWNER_PROJECTION_INVALID") from None
    return ServiceRecoveryOwnerProjectionRecordV1(
        receipt_ref=raw_receipt,
        payload=MappingProxyType(copied_payload),
        sequence=sequence,
        legacy=legacy,
    )


def is_service_recovery_owner_record(value: object, *, receipt_id: Optional[str] = None) -> bool:
    """Check neutral owner-record shape without attesting its host payload."""
    return not (
        type(value) is not ServiceRecoveryOwnerProjectionRecordV1
        or (receipt_id is not None and value.receipt_ref != receipt_id)
        or not _projection_digest(value.receipt_ref)
        or type(value.legacy) is not bool
        or (value.legacy and value.sequence is not None)
        or (not value.legacy and (type(value.sequence) is not int or value.sequence <= 0))
        or not isinstance(value.payload, Mapping)
    )


_RECOVERY_ORDER_FIELDS = (
    "scope_key",
    "intent_id",
    "intent_fingerprint",
    "submit_fingerprint",
    "signal_id",
    "instrument",
    "side",
    "position_effect",
    "order_type",
    "quantity",
    "price",
    "time_in_force",
    "reduce_only",
    "metadata_version",
    "deadline_ns",
    "state",
    "provider_order_id",
    "filled_quantity",
    "average_price",
    "cumulative_commission",
    "cumulative_commission_currency",
    "dispatch_attempts",
    "unknown_reason_code",
    "review_required",
    "latest_source_sequence",
    "latest_source_event_id",
    "latest_observation_sequence",
    "latest_observation_event_id",
)


def _validated_prior_state(
    value: object,
    *,
    intent_id: str,
    logical_run_id: str,
    submit_payload: Mapping[str, Any],
    recovery_binding: Mapping[str, Any],
) -> ServiceRecoveryOrderState:
    if not isinstance(value, Mapping) or frozenset(value) != {
        "manifest",
        "snapshot_order",
        "event_projection",
    }:
        raise _projection_error("RECOVERY_ORDER_PROJECTION_INVALID")
    manifest = _validated_manifest(
        value.get("manifest"), intent_id=intent_id, logical_run_id=logical_run_id
    )
    if dict(manifest.submit_payload) != dict(submit_payload) or not _recovery_binding_matches(
        recovery_binding, manifest.recovery_binding
    ):
        raise _projection_error("RECOVERY_ORDER_MANIFEST_MISMATCH")

    raw_order = value.get("snapshot_order")
    raw_projection = value.get("event_projection")
    if raw_order is not None and not isinstance(raw_order, Mapping):
        raise _projection_error("RECOVERY_ORDER_PROJECTION_INVALID")
    if raw_projection is not None and not isinstance(raw_projection, Mapping):
        raise _projection_error("RECOVERY_ORDER_PROJECTION_INVALID")
    event = raw_projection.get("event") if isinstance(raw_projection, Mapping) else None
    if event is not None and not isinstance(event, Mapping):
        raise _projection_error("RECOVERY_ORDER_PROJECTION_INVALID")
    if raw_order is None and event is None:
        raise _projection_error("RECOVERY_ORDER_PROJECTION_MISSING")

    expected_intent_fingerprint = manifest.get("intent_fingerprint")
    if raw_order is not None:
        order = dict(raw_order)
        if (
            any(name not in order for name in _RECOVERY_ORDER_FIELDS)
            or order.get("intent_id") != intent_id
            or order.get("scope_key") != manifest.get("scope_key")
            or order.get("submit_fingerprint") != manifest.submit_fingerprint
        ):
            raise _projection_error("RECOVERY_ORDER_PROJECTION_INVALID")
        for name in ("instrument", "side", "position_effect", "quantity"):
            if order.get(name) != submit_payload.get(name):
                raise _projection_error("RECOVERY_ORDER_CONTENT_MISMATCH")
        if expected_intent_fingerprint not in (None, order.get("intent_fingerprint")):
            raise _projection_error("RECOVERY_ORDER_FINGERPRINT_MISMATCH")
        result = {name: order[name] for name in _RECOVERY_ORDER_FIELDS}
    else:
        event_fields = (
            "scope_key",
            "intent_id",
            "intent_fingerprint",
            "submit_fingerprint",
            "event_id",
            "event_type",
            "state",
            "sequence",
        )
        if (
            any(name not in event for name in event_fields)
            or event.get("intent_id") != intent_id
            or event.get("scope_key") != manifest.get("scope_key")
            or event.get("submit_fingerprint") != manifest.submit_fingerprint
            or (
                expected_intent_fingerprint is not None
                and event.get("intent_fingerprint") != expected_intent_fingerprint
            )
            or type(event.get("sequence")) is not int
            or event["sequence"] < 0
            or not _projection_reference(event.get("event_id"))
            or type(event.get("state")) is not str
            or type(event.get("event_type")) is not str
        ):
            raise _projection_error("RECOVERY_ORDER_FINGERPRINT_MISMATCH")
        payload = manifest.submit_payload
        result = {
            "scope_key": event["scope_key"],
            "intent_id": intent_id,
            "intent_fingerprint": event["intent_fingerprint"],
            "submit_fingerprint": manifest.submit_fingerprint,
            "signal_id": payload.get("signal_id"),
            "instrument": payload.get("instrument"),
            "side": payload.get("side"),
            "position_effect": payload.get("position_effect"),
            "order_type": payload.get("order_type"),
            "quantity": payload.get("quantity"),
            "price": payload.get("price"),
            "time_in_force": payload.get("time_in_force"),
            "reduce_only": payload.get("reduce_only"),
            "metadata_version": payload.get("metadata_version"),
            "deadline_ns": payload.get("deadline_ns"),
            "state": event["state"],
            "provider_order_id": None,
            "filled_quantity": None,
            "average_price": None,
            "cumulative_commission": None,
            "cumulative_commission_currency": None,
            "dispatch_attempts": None,
            "unknown_reason_code": None,
            "review_required": None,
            "latest_source_sequence": event["sequence"],
            "latest_source_event_id": event["event_id"],
            "latest_observation_sequence": None,
            "latest_observation_event_id": None,
        }

    if event is not None:
        sequence = event.get("sequence")
        projection_sequence = (
            raw_projection.get("sequence") if isinstance(raw_projection, Mapping) else None
        )
        latest_sequence = result.get("latest_source_sequence")
        if (
            type(sequence) is not int
            or sequence < 0
            or type(projection_sequence) is not int
            or projection_sequence != sequence
            or type(latest_sequence) is not int
        ):
            raise _projection_error("RECOVERY_ORDER_PROJECTION_INVALID")
        if raw_order is None or sequence > latest_sequence:
            result["state"] = event["state"]
            result["latest_source_sequence"] = sequence
            result["latest_source_event_id"] = event["event_id"]
            details = event.get("details")
            if details is not None and not isinstance(details, Mapping):
                raise _projection_error("RECOVERY_ORDER_PROJECTION_INVALID")
            details = details or {}
            if event["event_type"] in {
                "provider_observation",
                "reconciled_observation",
                "provider_commission_evidence",
                "cancelled_by_cancel_intent",
            }:
                for name in (
                    "provider_order_id",
                    "filled_quantity",
                    "average_price",
                    "cumulative_commission",
                    "cumulative_commission_currency",
                ):
                    if name in details:
                        result[name] = details[name]
                result["latest_observation_sequence"] = sequence
                result["latest_observation_event_id"] = event["event_id"]
            if event["event_type"] in {
                "dispatch_unknown",
                "intent_blocked",
                "dispatch_blocked",
            }:
                result["unknown_reason_code"] = details.get("reason_code")
            if event["event_type"] == "manual_review_required":
                result["review_required"] = True

    result["recovery_binding"] = dict(manifest.recovery_binding)
    if result.get("intent_id") != intent_id:
        raise _projection_error("RECOVERY_ORDER_PROJECTION_INVALID")
    return ServiceRecoveryOrderState(result, issuer=_PROJECTION_ISSUER)


@dataclass(frozen=True)
class _RecoveryBackend:
    journal_type: type
    namespace_type: type
    recovery_error_type: type
    flow_type: type
    attested_execution_type: type
    attested_event_type: type
    attestation_port_type: type
    port_schema_version: int
    configure_ports: Callable[..., Any]


_BACKEND_LOCK = RLock()
_BACKEND: _RecoveryBackend | None = None
_ATTESTOR_LOCK = RLock()
_CONFIGURED_ATTESTOR: object | None = None
_CONFIGURED_ATTESTATION_PORT: object | None = None
_CONFIGURED_BACKEND: _RecoveryBackend | None = None


def _load_backend() -> _RecoveryBackend:
    """Load the optional execution implementation without a fallback path."""

    global _BACKEND
    with _BACKEND_LOCK:
        if _BACKEND is not None:
            return _BACKEND
        try:
            recovery = import_module("bt_api_execution.service_recovery")
            flow = import_module("bt_api_execution.service_recovery_flow")
            ports = import_module("bt_api_execution.service_recovery_ports")
        except ModuleNotFoundError as error:
            missing = error.name or ""
            code = (
                "SERVICE_RECOVERY_BACKEND_UNAVAILABLE"
                if missing == "bt_api_execution" or missing.startswith("bt_api_execution.")
                else "SERVICE_RECOVERY_DEPENDENCY_UNAVAILABLE"
            )
            raise ServiceRecoveryFacadeError(code) from None
        except ImportError:
            raise ServiceRecoveryFacadeError("SERVICE_RECOVERY_BACKEND_INCOMPATIBLE") from None
        except Exception:
            raise ServiceRecoveryFacadeError(
                "SERVICE_RECOVERY_BACKEND_INITIALIZATION_FAILED"
            ) from None

        try:
            backend = _RecoveryBackend(
                journal_type=recovery.ServiceExecutionRecoveryJournal,
                namespace_type=recovery.ServiceExecutionRecoveryNamespace,
                recovery_error_type=recovery.ServiceExecutionRecoveryError,
                flow_type=flow.ServiceRecoveryFlow,
                attested_execution_type=ports.AttestedRecoveryExecutionContext,
                attested_event_type=ports.AttestedRecoveryEventContext,
                attestation_port_type=ports.ServiceRecoveryExecutionContextAttestationPort,
                port_schema_version=ports.SERVICE_RECOVERY_PORT_SCHEMA_VERSION,
                configure_ports=ports.configure_service_recovery_ports,
            )
        except AttributeError:
            raise ServiceRecoveryFacadeError("SERVICE_RECOVERY_BACKEND_INCOMPATIBLE") from None
        if (
            type(backend.port_schema_version) is not int
            or backend.port_schema_version != 1
            or not isinstance(backend.journal_type, type)
            or not isinstance(backend.namespace_type, type)
            or not isinstance(backend.recovery_error_type, type)
            or not isinstance(backend.flow_type, type)
            or not isinstance(backend.attested_execution_type, type)
            or not isinstance(backend.attested_event_type, type)
            or not isinstance(backend.attestation_port_type, type)
            or not callable(backend.configure_ports)
        ):
            raise ServiceRecoveryFacadeError("SERVICE_RECOVERY_BACKEND_INCOMPATIBLE")
        _BACKEND = backend
        return backend


def _valid_code(value: object) -> bool:
    return (
        type(value) is str
        and 1 <= len(value) <= 128
        and all(
            character.isascii()
            and (character.isalnum() or character in "_.:-")
            for character in value
        )
    )


def _public_backend_error(
    error: BaseException, fallback_code: str
) -> ServiceRecoveryFacadeError:
    """Expose a bounded backend code without leaking exception text."""

    code = getattr(error, "code", None)
    return ServiceRecoveryFacadeError(code if _valid_code(code) else fallback_code)


def _require_attestor(attestor: object) -> None:
    if not callable(getattr(attestor, "attest_intent_context", None)):
        raise ServiceRecoveryFacadeError("RECOVERY_ATTESTOR_METHOD_UNAVAILABLE")
    if not callable(getattr(attestor, "attest_event_context", None)):
        raise ServiceRecoveryFacadeError("RECOVERY_ATTESTOR_METHOD_UNAVAILABLE")


def _make_attestation_port(backend: _RecoveryBackend, attestor: object) -> object:
    recovery_error_type = backend.recovery_error_type
    execution_attestation_type = backend.attested_execution_type
    event_attestation_type = backend.attested_event_type
    attest_intent_context = getattr(attestor, "attest_intent_context")
    attest_event_context = getattr(attestor, "attest_event_context")

    class _ExecutionContextAttestationPort(backend.attestation_port_type):
        port_version = backend.port_schema_version

        def attest_intent_context(
            self,
            raw: Mapping[str, Any],
            submit_payload: Mapping[str, Any],
        ) -> Any:
            try:
                result = attest_intent_context(raw, submit_payload)
            except Exception as error:
                code = getattr(error, "code", None)
                if _valid_code(code):
                    raise recovery_error_type(code) from None
                raise recovery_error_type("RECOVERY_ATTESTATION_FAILED") from None
            facts = getattr(result, "facts", None)
            if not isinstance(facts, Mapping):
                raise recovery_error_type("RECOVERY_ATTESTATION_RESULT_INVALID")
            return execution_attestation_type(facts=facts)

        def attest_event_context(
            self,
            raw: Mapping[str, Any],
            manifest: Mapping[str, Any],
            execution_context: Mapping[str, Any],
            paper_fill: Mapping[str, Any],
        ) -> Any:
            try:
                result = attest_event_context(
                    raw,
                    manifest,
                    execution_context,
                    paper_fill,
                )
            except Exception as error:
                code = getattr(error, "code", None)
                if _valid_code(code):
                    raise recovery_error_type(code) from None
                raise recovery_error_type("RECOVERY_ATTESTATION_FAILED") from None
            facts = getattr(result, "facts", None)
            data_name = getattr(result, "data_name", None)
            instrument = getattr(result, "instrument", None)
            commission_currency = getattr(result, "commission_currency", None)
            if (
                not isinstance(facts, Mapping)
                or type(data_name) is not str
                or type(instrument) is not str
                or type(commission_currency) is not str
            ):
                raise recovery_error_type("RECOVERY_ATTESTATION_RESULT_INVALID")
            return event_attestation_type(
                facts=facts,
                data_name=data_name,
                instrument=instrument,
                commission_currency=commission_currency,
            )

    return _ExecutionContextAttestationPort()


def _configure_attestor(backend: _RecoveryBackend, attestor: object) -> object:
    global _CONFIGURED_ATTESTOR, _CONFIGURED_ATTESTATION_PORT, _CONFIGURED_BACKEND
    _require_attestor(attestor)
    with _ATTESTOR_LOCK:
        if _CONFIGURED_ATTESTOR is not None:
            if attestor is not _CONFIGURED_ATTESTOR:
                raise ServiceRecoveryFacadeError("RECOVERY_ATTESTOR_ALREADY_CONFIGURED")
            if backend is not _CONFIGURED_BACKEND:
                raise ServiceRecoveryFacadeError("SERVICE_RECOVERY_BACKEND_CHANGED")
            port = _CONFIGURED_ATTESTATION_PORT
        else:
            try:
                port = _make_attestation_port(backend, attestor)
            except Exception:
                raise ServiceRecoveryFacadeError(
                    "SERVICE_RECOVERY_BACKEND_INCOMPATIBLE"
                ) from None
        try:
            backend.configure_ports(execution_context_attestation=port, replace=False)
        except Exception as error:
            raise _public_backend_error(error, "RECOVERY_PORT_CONFIGURATION_FAILED") from None
        if _CONFIGURED_ATTESTOR is None:
            _CONFIGURED_ATTESTOR = attestor
            _CONFIGURED_ATTESTATION_PORT = port
            _CONFIGURED_BACKEND = backend
        return port


class _ValidatedServiceRecoveryFlow:
    """SDK owner that validates journal manifests and prior order state."""

    def __init__(self, flow: object) -> None:
        self._flow = flow
        namespace = getattr(flow, "namespace", None)
        logical_run_id = getattr(namespace, "logical_run_id", None)
        if type(logical_run_id) is not str or not logical_run_id:
            raise ServiceRecoveryFacadeError("RECOVERY_NAMESPACE_INVALID")
        self._logical_run_id = logical_run_id

    def __getattr__(self, name: str) -> Any:
        return getattr(self._flow, name)

    def fill_projection(self, receipt_id: str) -> Optional[Mapping[str, Any]]:
        if not _projection_digest(receipt_id):
            raise ServiceRecoveryFacadeError("INVALID_CALLBACK_RECEIPT")
        value = self._flow.fill_projection(receipt_id)
        if value is None:
            return None
        if not isinstance(value, Mapping):
            raise ServiceRecoveryFacadeError("RECOVERY_FILL_PROJECTION_INVALID")
        # Historical framework-owned fields are returned through the separate
        # opaque owner-projection record below.
        result = deepcopy(dict(value))
        result.pop("trade_state", None)
        result.pop("trade_notification", None)
        return result

    def manifest(self, intent_id: str) -> Optional[ServiceRecoveryManifest]:
        if not _projection_reference(intent_id):
            raise ServiceRecoveryFacadeError("INVALID_INTENT_ID")
        raw = self._flow.manifest(intent_id)
        if raw is None:
            return None
        return _validated_manifest(
            raw,
            intent_id=intent_id,
            logical_run_id=self._logical_run_id,
        )

    def prepare_submit(
        self,
        *,
        intent_id: str,
        submit_payload: Mapping[str, Any],
        recovery_binding: Mapping[str, Any],
        strategy_position_owner_payload: Optional[Mapping[str, Any]],
        intent_context: Optional[Mapping[str, Any]],
    ) -> ServiceRecoveryPreparationV1:
        if (
            not _projection_reference(intent_id)
            or not isinstance(submit_payload, Mapping)
            or not isinstance(recovery_binding, Mapping)
            or not _requested_recovery_binding_is_valid(recovery_binding, submit_payload)
            or (
                strategy_position_owner_payload is not None
                and not isinstance(strategy_position_owner_payload, Mapping)
            )
            or (intent_context is not None and not isinstance(intent_context, Mapping))
        ):
            raise ServiceRecoveryFacadeError("INVALID_RECOVERY_SUBMIT_REQUEST")
        is_position_ref_binding = (
            recovery_binding.get("schema") == "bt_api.strategy-position-binding.v1"
        )
        if is_position_ref_binding != (strategy_position_owner_payload is not None):
            raise ServiceRecoveryFacadeError("INVALID_RECOVERY_SUBMIT_REQUEST")
        prepared = self._flow.prepare_submit(
            intent_id=intent_id,
            submit_payload=submit_payload,
            recovery_binding=recovery_binding,
            strategy_position_owner_payload=strategy_position_owner_payload,
            intent_context=intent_context,
        )
        if not isinstance(prepared, Mapping) or frozenset(prepared) != {
            "recovery_binding",
            "prior_state",
        }:
            raise ServiceRecoveryFacadeError("RECOVERY_SUBMIT_PREPARATION_INVALID")
        binding = _validated_recovery_binding(prepared.get("recovery_binding"), final=True)
        if not _recovery_binding_matches(recovery_binding, binding):
            raise ServiceRecoveryFacadeError("RECOVERY_SUBMIT_BINDING_MISMATCH")
        raw_prior = prepared.get("prior_state")
        if raw_prior is None:
            prior_state = None
        else:
            prior_state = _validated_prior_state(
                raw_prior,
                intent_id=intent_id,
                logical_run_id=self._logical_run_id,
                submit_payload=submit_payload,
                recovery_binding=binding,
            )
        position_ref = _strategy_position_ref_from_binding(binding)
        if is_position_ref_binding and position_ref is None:
            raise ServiceRecoveryFacadeError("RECOVERY_STRATEGY_POSITION_REF_UNAVAILABLE")
        if prior_state is not None and prior_state.recovery_binding != binding:
            raise ServiceRecoveryFacadeError("RECOVERY_SUBMIT_BINDING_MISMATCH")
        return ServiceRecoveryPreparationV1(
            recovery_binding=binding,
            strategy_position_ref=position_ref,
            prior_state=prior_state,
        )

    def strategy_position_ref_for_key(
        self, local_position_key_ref: str
    ) -> Optional[StrategyPositionRefV1]:
        if not is_local_strategy_position_key_ref(local_position_key_ref):
            raise ServiceRecoveryFacadeError("INVALID_STRATEGY_POSITION_KEY_REF")
        value = self._flow.strategy_position_ref_for_key(local_position_key_ref)
        if value is None:
            return None
        if not is_strategy_position_ref(value):
            raise ServiceRecoveryFacadeError("RECOVERY_STRATEGY_POSITION_REF_INVALID")
        return StrategyPositionRefV1(value)

    def strategy_position_owner_payload(
        self, strategy_position_ref: StrategyPositionRefV1
    ) -> Optional[Mapping[str, Any]]:
        if type(strategy_position_ref) is not StrategyPositionRefV1:
            raise ServiceRecoveryFacadeError("INVALID_STRATEGY_POSITION_REF")
        value = self._flow.strategy_position_owner_payload(strategy_position_ref.value)
        if value is None:
            return None
        if not isinstance(value, Mapping):
            raise ServiceRecoveryFacadeError("RECOVERY_STRATEGY_POSITION_OWNER_PAYLOAD_INVALID")
        try:
            return MappingProxyType(deepcopy(dict(value)))
        except Exception:
            raise ServiceRecoveryFacadeError(
                "RECOVERY_STRATEGY_POSITION_OWNER_PAYLOAD_INVALID"
            ) from None

    def persist_owner_projection(
        self, projection: ServiceRecoveryOwnerProjectionV1
    ) -> Mapping[str, Any]:
        has_position_key = projection.strategy_position_key_ref is not None
        has_position_payload = projection.strategy_position_owner_payload is not None
        if (
            type(projection) is not ServiceRecoveryOwnerProjectionV1
            or not _projection_digest(projection.receipt_ref)
            or not isinstance(projection.payload, Mapping)
            or has_position_key != has_position_payload
            or (
                has_position_key
                and not is_local_strategy_position_key_ref(projection.strategy_position_key_ref)
            )
            or (
                has_position_payload
                and not isinstance(projection.strategy_position_owner_payload, Mapping)
            )
        ):
            raise ServiceRecoveryFacadeError("INVALID_RECOVERY_OWNER_PROJECTION")
        try:
            payload = deepcopy(dict(projection.payload))
        except Exception:
            raise ServiceRecoveryFacadeError("INVALID_RECOVERY_OWNER_PROJECTION") from None
        result = self._flow.persist_owner_projection(
            projection.receipt_ref,
            payload=payload,
            strategy_position_key_ref=projection.strategy_position_key_ref,
            strategy_position_owner_payload=(
                deepcopy(dict(projection.strategy_position_owner_payload))
                if has_position_payload
                else None
            ),
        )
        if not isinstance(result, Mapping):
            raise ServiceRecoveryFacadeError("RECOVERY_OWNER_PROJECTION_INVALID")
        if has_position_key and not is_strategy_position_ref(
            result.get("strategy_position_ref")
        ):
            raise ServiceRecoveryFacadeError("RECOVERY_STRATEGY_POSITION_REF_UNAVAILABLE")
        return deepcopy(dict(result))

    def owner_projection(
        self, receipt_id: str
    ) -> Optional[ServiceRecoveryOwnerProjectionRecordV1]:
        if not _projection_digest(receipt_id):
            raise ServiceRecoveryFacadeError("INVALID_CALLBACK_RECEIPT")
        value = self._flow.owner_projection(receipt_id)
        if value is None:
            return None
        return _validated_owner_projection_record(value, receipt_id=receipt_id)

    def owner_projections(self) -> tuple[ServiceRecoveryOwnerProjectionRecordV1, ...]:
        values = self._flow.owner_projections()
        if type(values) is not tuple:
            raise ServiceRecoveryFacadeError("RECOVERY_OWNER_PROJECTIONS_INVALID")
        result = []
        last_sequence = 0
        for value in values:
            record = _validated_owner_projection_record(value)
            if not record.legacy:
                if record.sequence <= last_sequence:
                    raise ServiceRecoveryFacadeError("RECOVERY_OWNER_PROJECTIONS_INVALID")
                last_sequence = record.sequence
            result.append(record)
        return tuple(result)

    def persist_trade_projection(self, *args: Any, **kwargs: Any) -> Any:
        """Retire the BT-shaped public writer without hiding old stored rows."""

        raise ServiceRecoveryFacadeError("RECOVERY_TRADE_PROJECTION_LEGACY_UNSUPPORTED")

    def trade_checkpoints(self, *args: Any, **kwargs: Any) -> Any:
        """Retire the BT-keyed public read in favor of owner projections."""

        raise ServiceRecoveryFacadeError("RECOVERY_TRADE_CHECKPOINTS_LEGACY_UNSUPPORTED")

class ServiceRecoveryFacade:
    """Own one SDK recovery journal and create opaque recovery flows for it.

    Construct instances with :func:`create_service_recovery_facade`.  The
    creating SDK owner is responsible for closing this facade; a framework
    session may borrow it but must not close it.
    """

    def __init__(
        self,
        journal: object,
        *,
        backend: _RecoveryBackend,
        attestor: object,
    ) -> None:
        self._journal = journal
        self._backend = backend
        self._attestor = attestor
        self._closed = False
        self._state_lock = RLock()

    def create_flow(
        self,
        *,
        client: object,
        event_version: int,
        require_owner_baseline: bool,
        read_failure_code: Callable[[BaseException], str],
    ) -> ServiceRecoveryFlowPort:
        """Create one recovery flow bound to the configured SDK client and journal."""

        self._require_open()
        if type(event_version) is not int or event_version not in {1, 2}:
            raise ServiceRecoveryFacadeError("RECOVERY_EVENT_VERSION_UNSUPPORTED")
        if type(require_owner_baseline) is not bool:
            raise ServiceRecoveryFacadeError("INVALID_RECOVERY_BASELINE_REQUIREMENT")
        if not callable(read_failure_code):
            raise ServiceRecoveryFacadeError("RECOVERY_READ_FAILURE_MAPPER_UNAVAILABLE")
        if not callable(getattr(client, "query_recovery_snapshot", None)):
            raise ServiceRecoveryFacadeError("RECOVERY_SNAPSHOT_API_UNAVAILABLE")
        if not callable(getattr(client, "query_recovery_events_after", None)):
            raise ServiceRecoveryFacadeError("RECOVERY_EVENTS_API_UNAVAILABLE")

        def _checked_read_failure_code(error: BaseException) -> str:
            code = read_failure_code(error)
            if not _valid_code(code):
                raise ServiceRecoveryFacadeError("INVALID_RECOVERY_READ_FAILURE_CODE")
            return code

        try:
            flow = self._backend.flow_type(
                journal=self._journal,
                client=client,
                event_version=event_version,
                require_owner_baseline=require_owner_baseline,
                read_failure_code=_checked_read_failure_code,
            )
            return _ValidatedServiceRecoveryFlow(flow)
        except ServiceRecoveryFacadeError:
            raise
        except Exception as error:
            raise _public_backend_error(
                error,
                "SERVICE_RECOVERY_FLOW_INITIALIZATION_FAILED",
            ) from None

    def create_namespace(
        self,
        *,
        account_ref: str,
        strategy_ref: str,
        logical_run_id: str,
    ) -> object:
        """Create the SDK's opaque stable logical-run namespace."""

        self._require_open()
        try:
            return self._backend.namespace_type(
                account_ref=account_ref,
                strategy_ref=strategy_ref,
                logical_run_id=logical_run_id,
            )
        except Exception as error:
            raise _public_backend_error(error, "INVALID_RECOVERY_NAMESPACE") from None

    def close(self) -> None:
        """Close the owned journal once; a borrowed framework session must not call this."""

        with self._state_lock:
            if self._closed:
                return
            self._closed = True
        try:
            close = getattr(self._journal, "close", None)
            if not callable(close):
                raise ServiceRecoveryFacadeError("RECOVERY_JOURNAL_CLOSE_UNAVAILABLE")
            close()
        except ServiceRecoveryFacadeError:
            raise
        except Exception as error:
            raise _public_backend_error(error, "SERVICE_RECOVERY_CLOSE_FAILED") from None

    def _require_open(self) -> None:
        with self._state_lock:
            if self._closed:
                raise ServiceRecoveryFacadeError("SERVICE_RECOVERY_FACADE_CLOSED")


def create_service_recovery_facade(
    database_path: str | Path,
    *,
    attestor: object,
) -> ServiceRecoveryFacade:
    """Create the SDK-owned journal and bind the host's stateless attestor.

    This operation has no fallback.  Missing or incompatible optional execution
    components fail with :class:`ServiceRecoveryFacadeError`; journal setup
    errors preserve only a finite backend ``code`` where one is available.
    The ``database_path`` belongs to the SDK owner.  Framework sessions should
    receive the returned opaque facade instead of handling the path or journal.
    """

    if not isinstance(database_path, (str, Path)):
        raise ServiceRecoveryFacadeError("INVALID_RECOVERY_DATABASE_PATH")
    backend = _load_backend()
    _configure_attestor(backend, attestor)
    try:
        journal = backend.journal_type(database_path)
    except Exception as error:
        raise _public_backend_error(
            error,
            "SERVICE_RECOVERY_JOURNAL_INITIALIZATION_FAILED",
        ) from None
    return ServiceRecoveryFacade(journal, backend=backend, attestor=attestor)


def recovery_context_scalar(value):
    """Project one scalar into the persisted recovery-context representation."""
    if value is None or type(value) in (str, bool, int):
        return value
    if isinstance(value, Decimal):
        if not value.is_finite():
            raise ValueError("recovery context decimal is not finite")
        return format(value, "f")
    if isinstance(value, float):
        if not math.isfinite(value):
            raise ValueError("recovery context float is not finite")
        return value.hex()
    raise ValueError("recovery context value is not a supported scalar")


def recovery_exact_decimal(value, *, positive=False, nonnegative=False):
    """Read an exact decimal source with the recovery contract's bounds."""
    if type(value) not in (str, Decimal, int):
        raise ValueError("recovery decimal source is not exact text")
    try:
        number = value if isinstance(value, Decimal) else Decimal(str(value))
    except (InvalidOperation, TypeError, ValueError) as error:
        raise ValueError("recovery decimal source is invalid") from error
    if (
        not number.is_finite()
        or (positive and number <= 0)
        or (nonnegative and number < 0)
    ):
        raise ValueError("recovery decimal source is outside its bounds")
    return number


_PAPER_RECOVERY_FILL_FIELDS = frozenset({
    "schema", "time_source", "fill_id", "match_time_ns", "quantity", "price",
    "commission", "commission_currency", "commission_model_id",
    "commission_model_version", "event_id", "event_sequence", "journal_generation",
    "scope_key", "intent_id", "intent_fingerprint", "account_ref", "strategy_ref",
    "quote_source_ref", "quote_generation", "quote_cursor", "quote_instrument",
    "receipt_sha256",
})


def paper_recovery_fill_shape_matches(fill):
    """Compare a persisted fill with the neutral V2 field set."""
    return frozenset(fill) == _PAPER_RECOVERY_FILL_FIELDS


def paper_recovery_fill_identity_matches(fill, event, intent_id):
    """Check neutral fill identity and scalar types against its source event."""
    return not (
        fill.get("schema") != "managed_paper_fill.v2"
        or fill.get("time_source") != "MANAGED_PAPER_MATCH"
        or fill.get("commission_model_id") != "quote_notional_rate"
        or fill.get("commission_model_version") != "1"
        or fill.get("event_id") != event.get("event_id")
        or fill.get("event_sequence") != event.get("sequence")
        or fill.get("journal_generation") != event.get("journal_generation")
        or fill.get("scope_key") != event.get("scope_key")
        or fill.get("intent_id") != intent_id
        or fill.get("intent_fingerprint") != event.get("intent_fingerprint")
        or fill.get("fill_id") != f"paper:{fill.get('journal_generation')}:{fill.get('event_id')}"
        or type(fill.get("match_time_ns")) is not int
        or fill.get("match_time_ns") <= 0
        or type(fill.get("quote_cursor")) is not int
        or fill.get("quote_cursor") < 0
        or type(fill.get("event_sequence")) is not int
    )


def validate_paper_recovery_fill_binding(namespace, event, submit, details, fill, instrument):
    """Check neutral paper-fill identifiers against the persisted event and intent.

    This does not attest provider facts or a host framework's feed and bar.
    """
    if (
        event.get("event_type") != "provider_observation"
        or event.get("state") != "FILLED"
        or details.get("source") != "provider"
        or submit.get("position_effect") != "OPEN"
        or submit.get("reduce_only") is not False
        or submit.get("order_type") != "MARKET"
        or submit.get("time_in_force") != "FOK"
        or submit.get("price") is not None
        or fill.get("commission_model_id") != "quote_notional_rate"
        or fill.get("commission_model_version") != "1"
        or fill.get("instrument") not in (None, instrument)
        or fill.get("scope_key") != event.get("scope_key")
        or fill.get("event_id") != event.get("event_id")
        or fill.get("event_sequence") != event.get("sequence")
        or fill.get("journal_generation") != event.get("journal_generation")
        or fill.get("intent_id") != event.get("intent_id")
        or fill.get("intent_fingerprint") != event.get("intent_fingerprint")
        or fill.get("account_ref") != getattr(namespace, "account_ref", None)
        or fill.get("strategy_ref") != getattr(namespace, "strategy_ref", None)
        or fill.get("quote_instrument") != instrument
    ):
        raise ValueError("managed paper V2 fill binding is inconsistent")


def validate_paper_recovery_fill_economics(fill, submit, details, *, decimal):
    """Compare the persisted fill's numeric values with its enclosing event."""
    quantity = decimal(fill.get("quantity"), positive=True)
    price = decimal(fill.get("price"), positive=True)
    commission = decimal(fill.get("commission"))
    if (
        decimal(submit.get("quantity"), positive=True) != quantity
        or decimal(details.get("filled_quantity"), nonnegative=True) != quantity
        or decimal(details.get("average_price"), positive=True) != price
        or decimal(details.get("cumulative_commission")) != commission
    ):
        raise ValueError("managed paper V2 economics do not match the order event")


def project_service_owner_book_payload(snapshot: Any, *, error_type: type[Exception]) -> dict[str, Any]:
    """Project a service-owned book through the optional execution backend."""
    from bt_api_execution.service_owner_book_projection import owner_book_payload

    return owner_book_payload(snapshot, error_type=error_type)


__all__ = [
    "ServiceRecoveryPreparationV1",
    "ServiceRecoveryFacade",
    "ServiceRecoveryFacadeError",
    "ServiceRecoveryFacadePort",
    "ServiceRecoveryFlowPort",
    "ServiceRecoveryManifest",
    "ServiceRecoveryOrderState",
    "StrategyPositionRefV1",
    "ServiceRecoveryOwnerProjectionRecordV1",
    "ServiceRecoveryOwnerProjectionV1",
    "ServiceRecoveryProjectionError",
    "create_service_recovery_facade",
    "is_service_recovery_owner_record",
    "is_strategy_position_ref",
    "is_local_strategy_position_key_ref",
    "project_service_owner_book_payload",
    "paper_recovery_fill_identity_matches",
    "paper_recovery_fill_shape_matches",
    "recovery_context_scalar",
    "recovery_exact_decimal",
    "recovery_facade_is_usable",
    "recovery_flow_is_usable",
    "validate_paper_recovery_fill_binding",
    "validate_paper_recovery_fill_economics",
]
