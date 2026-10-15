"""Risk-only authenticated order entry through the existing :class:`BtApi`.

This module owns a small idempotent dispatch journal and the last service-side
admission boundary.  It is not an OMS: the configured BtApi provider remains
authoritative for orders and fills, and Risk remains authoritative for permits
and settled reservation occupancy.
"""

from __future__ import annotations

import re
import time
from collections.abc import Mapping
from contextlib import suppress
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation
from enum import StrEnum
from types import MappingProxyType
from typing import Any

from bt_api_base.service_protocol import WireIdentity, WireRole, WireScope
from bt_api_risk import (
    INSTRUMENT_METADATA_DIGEST_TAG,
    AccountScope,
    DispatchClaimBinding,
    DispatchEvidenceClass,
    DispatchResolutionReceipt,
    DispatchResolutionReceiptStatus,
    DispatchTrackedOrderProof,
    DurableRiskAdmissionAdapter,
    DurableRiskGate,
    InstrumentRiskRegistry,
    PermitInvalidError,
    RiskAdmissionDeniedError,
)

from bt_api_py._contracts.models import OrderRequest, OrderType, QueryOrderRequest, Side
from bt_api_py._normalization import (
    NormalizationProvenance,
    NormalizedRecord,
    NormalizedRecords,
)
from bt_api_py.bt_api import BtApi

from .service_risk_order_journal import (
    DispatchProofRecoverySource,
    RiskOrderCommand,
    RiskOrderJournal,
    RiskOrderJournalError,
    RiskOrderWriterFence,
    canonical_json,
    sha256_text,
)

SUBMIT_OPERATION = "orders.submit"
QUERY_OPERATION = "orders.query"
CANCEL_OPERATION = "orders.cancel"
SUPPORTED_OPERATIONS = frozenset({SUBMIT_OPERATION, QUERY_OPERATION})

_ORDER_FIELDS = frozenset(
    {
        "intent_id",
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
        "tags",
    }
)
_REFERENCE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:/-]{0,127}$")
_VERSIONED_POLICY_REFERENCE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:-]*-v[1-9][0-9]*$")
_ORDER_OWNER_PORTS = frozenset({"service.order_entry", "service.order_query"})
_WIRE_SCOPE_FIELDS = (
    "execution_domain_ref",
    "account_ref",
    "account_refs",
    "strategy_ref",
    "manual_book_ref",
    "portfolio_ref",
    "group_id",
    "leg_id",
)
_WIRE_SCOPE_FIELDS_SET = frozenset((*_WIRE_SCOPE_FIELDS, "run_id"))
_ORDER_PROVENANCE_SCHEMA = "bt-api-order-source-provenance-v1"
_ORDER_PROVENANCE_SCHEMA_V2 = "bt-api-order-source-provenance-v2"
_RISK_ORDER_CONTRACT_SCHEMA_V2 = "bt-api-risk-provider-contract-v2"
_SUPPORTED_PROVIDER_TIME_IN_FORCE = frozenset(
    {("GTC",), ("IOC",), ("GTC", "IOC")}
)
_OPEN_ORDER_STATES = frozenset({"accepted"})
_PROVIDER_STATUS_KEYS = frozenset(
    {
        "accepted",
        "submitted",
        "partial",
        "completed",
        "canceled",
        "expired",
        "rejected",
        "unknown",
    }
)
_REQUIRED_PROVIDER_STATUS_KEYS = frozenset(
    {"accepted", "completed", "canceled", "expired", "rejected"}
)
_STATUS_KEY = re.compile(r"^[a-z0-9_]{1,48}$")
_RECOVERY_ORDER_OBSERVATION_FIELDS = frozenset(
    {
        "account_id",
        "acknowledgement",
        "client_order_id",
        "collection_provenance",
        "complete",
        "envelope_fields",
        "envelope_values",
        "filled_quantity",
        "instrument",
        "normalization_provenance",
        "normalizer_schema",
        "operation",
        "provider_contract_sha256",
        "provider_observation_sha256",
        "provider_order_id",
        "quantity",
        "record",
        "schema",
        "side",
        "source_alignment_valid",
        "source_fields",
        "source_row_count",
        "source_sha256",
        "source_values",
        "status",
        "updated_at_ns",
        "watermark",
    }
)
_RECOVERY_FILL_OBSERVATION_FIELDS = frozenset(
    {
        "account_id",
        "client_order_id",
        "complete",
        "envelope_fields",
        "envelope_values",
        "filled_quantity",
        "normalization_provenance",
        "normalizer_schema",
        "operation",
        "provider_contract_sha256",
        "provider_observation_sha256",
        "record_count",
        "records",
        "schema",
        "source_alignment_valid",
        "source_fields",
        "source_row_count",
        "source_sha256",
        "source_values",
        "trade_count",
        "updated_at_ns",
        "watermark",
    }
)
_SOURCE_ALIASES = {
    "provider_order_id": (
        "order_id",
        "external_order_id",
        "ordId",
        "orderId",
        "order_sys_id",
        "OrderSysID",
    ),
    "client_order_id": ("client_order_id", "clOrdId", "clientOrderId", "order_ref", "OrderRef"),
    "status": ("status", "order_status", "state"),
    "filled_quantity": (
        "filled",
        "filled_quantity",
        "executed_qty",
        "accFillSz",
        "executedQty",
        "VolumeTraded",
    ),
    "quantity": ("quantity", "size", "order_size", "sz", "origQty", "VolumeTotalOriginal"),
    "account_id": ("account_id", "AccountID", "InvestorID"),
    "instrument": (
        "symbol",
        "symbol_name",
        "instrument",
        "instrument_id",
        "data_name",
        "instId",
        "InstrumentID",
        "trade_symbol",
    ),
    "side": ("side", "order_side", "trade_side", "S"),
}
_PERMITTED_ROLES = frozenset(
    {WireRole.STRATEGY_CLIENT, WireRole.APPLICATION_CLIENT, WireRole.SERVICE}
)


class RiskOrderEntryError(RuntimeError):
    """Finite, payload-free assembly or entry error."""

    def __init__(self, code: str) -> None:
        if type(code) is not str or not code or len(code) > 96:
            raise ValueError("invalid risk-order entry error code")
        self.code = code
        super().__init__(code)


def validate_service_options(
    options: Mapping[str, object], bind_ports: tuple[str, ...]
) -> Mapping[str, str]:
    """Validate the sole public ``risk/account_owner`` service role."""

    if not isinstance(options, Mapping):
        raise RiskOrderEntryError("INVALID_OPTIONS")
    if type(bind_ports) is not tuple or not bind_ports:
        raise RiskOrderEntryError("INVALID_BIND_PORTS")
    if any(type(port) is not str for port in bind_ports) or len(set(bind_ports)) != len(bind_ports):
        raise RiskOrderEntryError("INVALID_BIND_PORTS")
    if any(port not in _ORDER_OWNER_PORTS for port in bind_ports):
        raise RiskOrderEntryError("UNSUPPORTED_ORDER_OWNER_PORT")
    if any(type(key) is not str for key in options):
        raise RiskOrderEntryError("INVALID_OPTION_KEY")
    if set(options) != {"role", "account_policy_ref"} or options.get("role") != "account_owner":
        raise RiskOrderEntryError("INVALID_ORDER_OWNER_OPTIONS")
    policy_ref = options.get("account_policy_ref")
    if (
        type(policy_ref) is not str
        or _VERSIONED_POLICY_REFERENCE.fullmatch(policy_ref) is None
        or _REFERENCE.fullmatch(policy_ref) is None
    ):
        raise RiskOrderEntryError("ACCOUNT_POLICY_REFERENCE_REQUIRED")
    return MappingProxyType({"role": "account_owner", "account_policy_ref": policy_ref})


@dataclass(frozen=True, slots=True)
class RiskOrderTarget:
    """Trusted server-owned provider and Risk scope for one exact WireScope."""

    account_scope: AccountScope
    strategy_id: str
    exchange_name: str
    provider_account_id: str
    registry: InstrumentRiskRegistry

    def __post_init__(self) -> None:
        if type(self.account_scope) is not AccountScope:
            raise RiskOrderEntryError("TARGET_ACCOUNT_SCOPE_REQUIRED")
        if type(self.strategy_id) is not str or not _REFERENCE.fullmatch(self.strategy_id):
            raise RiskOrderEntryError("TARGET_STRATEGY_REQUIRED")
        if type(self.exchange_name) is not str or not _REFERENCE.fullmatch(self.exchange_name):
            raise RiskOrderEntryError("TARGET_EXCHANGE_REQUIRED")
        if type(self.provider_account_id) is not str or not _REFERENCE.fullmatch(
            self.provider_account_id
        ):
            raise RiskOrderEntryError("TARGET_PROVIDER_ACCOUNT_REQUIRED")
        if type(self.registry) is not InstrumentRiskRegistry:
            raise RiskOrderEntryError("TARGET_METADATA_REGISTRY_REQUIRED")
        if (
            self.account_scope.provider.casefold()
            != self.exchange_name.partition("___")[0].casefold()
        ):
            raise RiskOrderEntryError("TARGET_PROVIDER_SCOPE_MISMATCH")
        if self.account_scope.account_id != self.provider_account_id:
            raise RiskOrderEntryError("TARGET_ACCOUNT_SCOPE_MISMATCH")


@dataclass(frozen=True, slots=True)
class RiskBtApiProviderContract:
    """Trusted provider semantics required before the service enables writes.

    Capability fields are supplied only by the server-owned provider profile.
    A profile that cannot attest exact, unique client-ID lookup, complete
    snapshots/history, and a complete raw-to-normalized status mapping remains
    query-only.
    """

    exchange_name: str
    profile_id: str
    order_schema: str
    fill_schema: str
    client_order_id_query: bool
    client_order_id_query_unique: bool
    complete_order_snapshot: bool
    complete_fill_history: bool
    provider_accepts_client_order_id: bool
    provider_status_mapping: tuple[tuple[str, str], ...]
    supported_order_types: tuple[str, ...]
    supported_time_in_force: tuple[str, ...]
    supported_position_effects: tuple[str, ...]
    normalizer_schema: str = _ORDER_PROVENANCE_SCHEMA
    order_revision_field: str | None = None
    fill_revision_field: str | None = None
    fill_watermark_field: str | None = None
    fee_amount_field: str | None = None
    fee_currency_field: str | None = None
    quote_currency_by_instrument: tuple[tuple[str, str], ...] = ()

    def __post_init__(self) -> None:
        for name in ("exchange_name", "profile_id", "order_schema", "fill_schema"):
            value = getattr(self, name)
            if type(value) is not str or not _REFERENCE.fullmatch(value):
                raise RiskOrderEntryError("PROVIDER_CONTRACT_INVALID")
        if any(
            type(getattr(self, name)) is not bool
            for name in (
                "client_order_id_query",
                "client_order_id_query_unique",
                "complete_order_snapshot",
                "complete_fill_history",
                "provider_accepts_client_order_id",
            )
        ):
            raise RiskOrderEntryError("PROVIDER_CONTRACT_INVALID")
        for name, expected in (
            ("supported_order_types", ("LIMIT",)),
            ("supported_position_effects", ("OPEN",)),
        ):
            value = getattr(self, name)
            if type(value) is not tuple or value != expected:
                raise RiskOrderEntryError("PROVIDER_ORDER_CAPABILITY_UNSUPPORTED")
        if (
            type(self.supported_time_in_force) is not tuple
            or self.supported_time_in_force not in _SUPPORTED_PROVIDER_TIME_IN_FORCE
        ):
            raise RiskOrderEntryError("PROVIDER_ORDER_CAPABILITY_UNSUPPORTED")
        mapping = self.provider_status_mapping
        if type(mapping) is not tuple or not mapping:
            raise RiskOrderEntryError("PROVIDER_STATUS_MAPPING_REQUIRED")
        normalized: list[tuple[str, str]] = []
        raw_keys: set[str] = set()
        for item in mapping:
            if (
                type(item) is not tuple
                or len(item) != 2
                or type(item[0]) is not str
                or type(item[1]) is not str
            ):
                raise RiskOrderEntryError("PROVIDER_STATUS_MAPPING_INVALID")
            raw_status, normalized_status = item
            if (
                _STATUS_KEY.fullmatch(raw_status) is None
                or normalized_status not in _PROVIDER_STATUS_KEYS
                or raw_status in raw_keys
            ):
                raise RiskOrderEntryError("PROVIDER_STATUS_MAPPING_INVALID")
            raw_keys.add(raw_status)
            normalized.append(item)
        if not {value for _, value in normalized} >= _REQUIRED_PROVIDER_STATUS_KEYS:
            raise RiskOrderEntryError("PROVIDER_TERMINAL_STATES_UNAVAILABLE")
        if not any(value in _OPEN_ORDER_STATES for _, value in normalized):
            raise RiskOrderEntryError("PROVIDER_OPEN_STATE_UNAVAILABLE")
        object.__setattr__(self, "provider_status_mapping", tuple(normalized))
        if self.normalizer_schema not in {
            _ORDER_PROVENANCE_SCHEMA,
            _ORDER_PROVENANCE_SCHEMA_V2,
        }:
            raise RiskOrderEntryError("PROVIDER_NORMALIZER_UNSUPPORTED")
        if (
            "IOC" in self.supported_time_in_force
            and self.normalizer_schema != _ORDER_PROVENANCE_SCHEMA_V2
        ):
            raise RiskOrderEntryError("PROVIDER_V2_SOURCE_CONTRACT_REQUIRED")
        v2_fields = (
            self.order_revision_field,
            self.fill_revision_field,
            self.fill_watermark_field,
            self.fee_amount_field,
            self.fee_currency_field,
        )
        if self.normalizer_schema == _ORDER_PROVENANCE_SCHEMA_V2:
            if v2_fields != (
                "order_revision",
                "fill_revision",
                "fill_watermark",
                "fee_amount",
                "fee_currency",
            ):
                raise RiskOrderEntryError("PROVIDER_V2_SOURCE_CONTRACT_INVALID")
            if "partial" not in {value for _, value in normalized}:
                raise RiskOrderEntryError("PROVIDER_PARTIAL_STATE_UNAVAILABLE")
        elif any(value is not None for value in v2_fields) or self.quote_currency_by_instrument:
            raise RiskOrderEntryError("PROVIDER_V2_SOURCE_CONTRACT_INVALID")
        quote_rows = self.quote_currency_by_instrument
        if type(quote_rows) is not tuple:
            raise RiskOrderEntryError("PROVIDER_QUOTE_CURRENCY_MAPPING_INVALID")
        seen_instruments: set[str] = set()
        checked_quotes: list[tuple[str, str]] = []
        for item in quote_rows:
            if (
                type(item) is not tuple
                or len(item) != 2
                or type(item[0]) is not str
                or type(item[1]) is not str
                or not _REFERENCE.fullmatch(item[0])
                or re.fullmatch(r"[A-Z][A-Z0-9]{1,15}", item[1]) is None
                or item[0] in seen_instruments
            ):
                raise RiskOrderEntryError("PROVIDER_QUOTE_CURRENCY_MAPPING_INVALID")
            seen_instruments.add(item[0])
            checked_quotes.append(item)
        if tuple(checked_quotes) != tuple(sorted(checked_quotes)):
            raise RiskOrderEntryError("PROVIDER_QUOTE_CURRENCY_MAPPING_INVALID")
        object.__setattr__(self, "quote_currency_by_instrument", tuple(checked_quotes))

    @property
    def write_ready(self) -> bool:
        return (
            self.client_order_id_query
            and self.client_order_id_query_unique
            and self.provider_accepts_client_order_id
            and self.complete_order_snapshot
            and self.complete_fill_history
            and {value for _, value in self.provider_status_mapping}
            >= _REQUIRED_PROVIDER_STATUS_KEYS
        )

    @property
    def risk_only_write_ready(self) -> bool:
        """Whether the normal risk-only dispatcher can use its fixed GTC path."""

        return self.write_ready and "GTC" in self.supported_time_in_force

    @property
    def exposure_ready(self) -> bool:
        """Whether the trusted profile supplies the explicit v2 observation contract."""

        return (
            self.write_ready
            and self.normalizer_schema == _ORDER_PROVENANCE_SCHEMA_V2
            and all(
                value is not None
                for value in (
                    self.order_revision_field,
                    self.fill_revision_field,
                    self.fill_watermark_field,
                    self.fee_amount_field,
                    self.fee_currency_field,
                )
            )
            and bool(self.quote_currency_by_instrument)
            and "partial" in {value for _, value in self.provider_status_mapping}
        )

    def quote_currency(self, instrument: str) -> str | None:
        return dict(self.quote_currency_by_instrument).get(instrument)

    def to_payload(self) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "client_order_id_query": self.client_order_id_query,
            "client_order_id_query_unique": self.client_order_id_query_unique,
            "complete_fill_history": self.complete_fill_history,
            "complete_order_snapshot": self.complete_order_snapshot,
            "exchange_name": self.exchange_name,
            "fill_schema": self.fill_schema,
            "normalizer_schema": self.normalizer_schema,
            "order_schema": self.order_schema,
            "profile_id": self.profile_id,
            "provider_accepts_client_order_id": self.provider_accepts_client_order_id,
            "provider_status_mapping": [list(item) for item in self.provider_status_mapping],
            "supported_order_types": list(self.supported_order_types),
            "supported_position_effects": list(self.supported_position_effects),
            "supported_time_in_force": list(self.supported_time_in_force),
        }
        if self.normalizer_schema == _ORDER_PROVENANCE_SCHEMA_V2:
            payload.update(
                {
                    "fee_amount_field": self.fee_amount_field,
                    "fee_currency_field": self.fee_currency_field,
                    "fill_revision_field": self.fill_revision_field,
                    "fill_watermark_field": self.fill_watermark_field,
                    "order_revision_field": self.order_revision_field,
                    "quote_currency_by_instrument": [
                        list(item) for item in self.quote_currency_by_instrument
                    ],
                    "schema": _RISK_ORDER_CONTRACT_SCHEMA_V2,
                }
            )
        else:
            # Keep v1 profile fingerprints byte-for-byte compatible.
            payload["schema"] = "bt-api-risk-provider-contract-v1"
        return payload

    @property
    def fingerprint(self) -> str:
        return sha256_text(canonical_json(self.to_payload()))


class _PositionEffect(StrEnum):
    OPEN = "OPEN"


@dataclass(frozen=True, slots=True)
class _AdmissionScope:
    provider: str
    environment: str
    account_ref: str
    strategy_id: str


@dataclass(frozen=True, slots=True)
class _AdmissionOrder:
    """Execution-shaped neutral DTO consumed by Risk's trusted mapper."""

    intent_id: str
    scope: _AdmissionScope
    instrument: str
    quantity: Decimal
    price: Decimal
    position_effect: _PositionEffect
    metadata_version: str
    tags: Mapping[str, str]
    payload_sha256: str

    @property
    def fingerprint(self) -> str:
        return sha256_text(
            canonical_json(
                {
                    "intent_id": self.intent_id,
                    "instrument": self.instrument,
                    "metadata_version": self.metadata_version,
                    "payload_sha256": self.payload_sha256,
                    "position_effect": self.position_effect.value,
                    "price": format(self.price, "f"),
                    "quantity": format(self.quantity, "f"),
                    "scope": {
                        "account_ref": self.scope.account_ref,
                        "environment": self.scope.environment,
                        "provider": self.scope.provider,
                        "strategy_id": self.scope.strategy_id,
                    },
                    "tags": dict(self.tags),
                }
            )
        )


class RiskOrderEntryService:
    """Risk-gated service path that makes all writes through typed ``BtApi``."""

    SUPPORTED_OPERATIONS = SUPPORTED_OPERATIONS

    def __init__(
        self,
        bt_api: BtApi,
        targets: Mapping[WireScope, RiskOrderTarget],
        admission: Mapping[WireScope, DurableRiskAdmissionAdapter],
        journal: RiskOrderJournal,
        gate: DurableRiskGate,
        provider_contracts: Mapping[WireScope, RiskBtApiProviderContract],
    ) -> None:
        if type(bt_api) is not BtApi:
            raise RiskOrderEntryError("BT_API_REQUIRED")
        if type(journal) is not RiskOrderJournal:
            raise RiskOrderEntryError("RISK_ORDER_JOURNAL_REQUIRED")
        if type(gate) is not DurableRiskGate:
            raise RiskOrderEntryError("DURABLE_RISK_GATE_REQUIRED")
        if getattr(gate, "_execution_journal_authority", None) is not journal:
            raise RiskOrderEntryError("RISK_JOURNAL_AUTHORITY_MISMATCH")
        if not isinstance(targets, Mapping) or not targets:
            raise RiskOrderEntryError("RISK_ORDER_TARGETS_REQUIRED")
        if not isinstance(admission, Mapping) or not admission:
            raise RiskOrderEntryError("RISK_ORDER_ADMISSION_REQUIRED")
        if not isinstance(provider_contracts, Mapping):
            raise RiskOrderEntryError("PROVIDER_CONTRACTS_INVALID")

        target_copy: dict[WireScope, RiskOrderTarget] = {}
        for scope, target in targets.items():
            if type(scope) is not WireScope or type(target) is not RiskOrderTarget:
                raise RiskOrderEntryError("RISK_ORDER_TARGET_INVALID")
            if scope.strategy_ref != target.strategy_id:
                raise RiskOrderEntryError("TARGET_STRATEGY_SCOPE_MISMATCH")
            target_copy[scope] = target
        admission_copy: dict[WireScope, DurableRiskAdmissionAdapter] = {}
        for scope, adapter in admission.items():
            if type(scope) is not WireScope or type(adapter) is not DurableRiskAdmissionAdapter:
                raise RiskOrderEntryError("RISK_ORDER_ADMISSION_INVALID")
            admission_copy[scope] = adapter
        if set(admission_copy) != set(target_copy):
            raise RiskOrderEntryError("RISK_ORDER_ADMISSION_SCOPE_MISMATCH")

        contract_copy: dict[WireScope, RiskBtApiProviderContract] = {}
        for scope, contract in provider_contracts.items():
            if type(scope) is not WireScope or type(contract) is not RiskBtApiProviderContract:
                raise RiskOrderEntryError("PROVIDER_CONTRACT_INVALID")
            target = target_copy.get(scope)
            if target is None or contract.exchange_name != target.exchange_name:
                raise RiskOrderEntryError("PROVIDER_CONTRACT_SCOPE_MISMATCH")
            if not contract.risk_only_write_ready:
                raise RiskOrderEntryError("PROVIDER_GTC_CAPABILITY_REQUIRED")
            contract_copy[scope] = contract

        self._bt_api = bt_api
        self._targets = MappingProxyType(target_copy)
        self._admission = MappingProxyType(admission_copy)
        self._provider_contracts = MappingProxyType(contract_copy)
        self._journal = journal
        self._gate = gate

    def handle(
        self,
        operation: str,
        *,
        principal_ref: str,
        identity: WireIdentity,
        scope: WireScope,
        arguments: Mapping[str, Any],
    ) -> Mapping[str, Any]:
        """Handle one authenticated service request without trusting payload scope."""

        if type(principal_ref) is not str or not _REFERENCE.fullmatch(principal_ref):
            raise RiskOrderEntryError("TRUSTED_PRINCIPAL_INVALID")
        if operation == CANCEL_OPERATION:
            return _reply(False, "CAPABILITY_UNAVAILABLE", {"found": False, "record": None})
        if type(operation) is not str or operation not in SUPPORTED_OPERATIONS:
            return _reply(False, "UNKNOWN_OPERATION", {"found": False, "record": None})
        if type(identity) is not WireIdentity or identity.principal_ref != principal_ref:
            raise RiskOrderEntryError("TRUSTED_IDENTITY_MISMATCH")
        if identity.role not in _PERMITTED_ROLES:
            return _reply(False, "ROLE_NOT_AUTHORIZED", {"found": False, "record": None})
        if type(scope) is not WireScope:
            return _reply(False, "SCOPE_DENIED", {"found": False, "record": None})
        binding = self._resolve_scope_binding(scope)
        if binding is None:
            return _reply(False, "SCOPE_DENIED", {"found": False, "record": None})
        _template, target, admission, contract = binding
        if identity.role is WireRole.STRATEGY_CLIENT and identity.actor_ref != scope.strategy_ref:
            return _reply(False, "SCOPE_DENIED", {"found": False, "record": None})

        if operation == QUERY_OPERATION:
            return self._query(
                principal_ref=principal_ref,
                scope=scope,
                target=target,
                contract=contract,
                arguments=arguments,
            )
        return self._submit(
            principal_ref=principal_ref,
            identity=identity,
            scope=scope,
            target=target,
            admission=admission,
            contract=contract,
            arguments=arguments,
        )

    def _resolve_scope_binding(
        self, scope: WireScope
    ) -> (
        tuple[
            WireScope,
            RiskOrderTarget,
            DurableRiskAdmissionAdapter,
            RiskBtApiProviderContract | None,
        ]
        | None
    ):
        """Resolve the trusted template while preserving a concrete run scope."""

        if type(scope) is not WireScope:
            return None
        exact = self._targets.get(scope)
        if exact is not None:
            return (
                scope,
                exact,
                self._admission[scope],
                self._provider_contracts.get(scope),
            )

        matches = [template for template in self._targets if _matches_run_wildcard(template, scope)]
        if len(matches) != 1:
            return None
        template = matches[0]
        return (
            template,
            self._targets[template],
            self._admission[template],
            self._provider_contracts.get(template),
        )

    def capability_state(self, scope: WireScope) -> Mapping[str, bool]:
        """Return the explicit service capability projection for one trusted scope."""

        binding = self._resolve_scope_binding(scope)
        if binding is None:
            return MappingProxyType(
                {SUBMIT_OPERATION: False, QUERY_OPERATION: False, CANCEL_OPERATION: False}
            )
        contract = binding[3]
        return MappingProxyType(
            {
                SUBMIT_OPERATION: contract is not None and contract.risk_only_write_ready,
                QUERY_OPERATION: True,
                CANCEL_OPERATION: False,
            }
        )

    def recover_interrupted_dispatches(self) -> tuple[RiskOrderCommand, ...]:
        """Recover permit/claim gaps before the component begins serving writes."""

        recovered: list[RiskOrderCommand] = []
        seen: set[str] = set()
        for target in self._targets.values():
            account_key = target.account_scope.key
            if account_key in seen:
                continue
            seen.add(account_key)
            with self._journal.writer_fence(target.account_scope) as fence:
                for command in self._journal.commands_for_scope(target.account_scope):
                    if command.state in {"ACKED_TRACKED", "ADMISSION_DENIED", "NO_DISPATCH"}:
                        continue
                    if command.state == "PREPARED":
                        # A permit ID is not durable yet; by call ordering no
                        # dispatch claim can exist. A matching retry may finish
                        # the reserve/write-ahead sequence, but recovery never
                        # calls BtApi.
                        continue
                    if command.state in {
                        "PROVIDER_QUERY",
                        "UNKNOWN",
                    } and self._has_resolved_ack_history(fence, command):
                        # An ACK already consumed Risk's dispatch proof. Later
                        # query records are read history, not interrupted dispatch
                        # proof to replay during service startup.
                        continue
                    try:
                        has_proof_ready = self._journal.has_proof_ready_history(
                            fence,
                            principal_ref=command.principal_ref,
                            intent_id=command.intent_id,
                        )
                    except Exception:
                        recovered.append(
                            self._mark_unknown(
                                fence,
                                command,
                                "DISPATCH_PROOF_RECOVERY_UNCERTAIN",
                                permit_id=command.permit_id,
                            )
                        )
                        continue
                    if has_proof_ready:
                        binding = self._binding_for_command(command)
                        if binding is None or binding[1].account_scope != target.account_scope:
                            recovered.append(
                                self._mark_unknown(
                                    fence,
                                    command,
                                    "DISPATCH_PROOF_RECOVERY_UNCERTAIN",
                                    permit_id=command.permit_id,
                                )
                            )
                            continue
                        recovered.append(
                            self._recover_proof_ready(
                                fence,
                                command,
                                target=binding[1],
                                contract=binding[3],
                            )
                        )
                        continue
                    recovered.append(self._recover_claimed(fence, command))
        return tuple(recovered)

    def _submit(
        self,
        *,
        principal_ref: str,
        identity: WireIdentity,
        scope: WireScope,
        target: RiskOrderTarget,
        admission: DurableRiskAdmissionAdapter,
        contract: RiskBtApiProviderContract | None,
        arguments: Mapping[str, Any],
    ) -> Mapping[str, Any]:
        if contract is None or not contract.risk_only_write_ready:
            return _reply(False, "CAPABILITY_UNAVAILABLE", {"found": False, "record": None})
        if not _has_exact_argument_fields(arguments, _ORDER_FIELDS):
            return _reply(False, "INVALID_REQUEST", {"found": False, "record": None})
        try:
            parsed = _parse_order(arguments, target, scope=scope, principal_ref=principal_ref)
        except RiskOrderEntryError as error:
            code = error.code
            if code == "METADATA_VERSION_MISMATCH":
                code = "METADATA_VERSION_MISMATCH"
            return _reply(False, code, {"found": False, "record": None})

        with self._journal.writer_fence(target.account_scope) as fence:
            command, created = self._prepare_command(
                fence,
                principal_ref=principal_ref,
                scope=scope,
                target=target,
                parsed=parsed,
            )
            if not created:
                command = self._resolve_duplicate(
                    fence, command, target=target, admission=admission, contract=contract
                )
                return _command_reply(command, target.account_scope)
            return self._drive_first_attempt(
                fence,
                identity=identity,
                scope=scope,
                target=target,
                admission=admission,
                contract=contract,
                parsed=parsed,
                command=command,
            )

    def _prepare_command(
        self,
        fence: RiskOrderWriterFence,
        *,
        principal_ref: str,
        scope: WireScope,
        target: RiskOrderTarget,
        parsed: Mapping[str, Any],
    ) -> tuple[RiskOrderCommand, bool]:
        risk_intent_id = "risk-order:" + sha256_text(
            canonical_json(
                {
                    "account_scope": target.account_scope.key,
                    "intent_id": parsed["intent_id"],
                    "principal_ref": principal_ref,
                    "strategy_id": target.strategy_id,
                    "wire_scope": _wire_scope_identity_payload(scope),
                }
            )
        )
        metadata = target.registry.get(parsed["instrument"])
        assert metadata is not None
        canonical_payload = {
            **parsed["canonical_payload"],
            "trusted_metadata_digest": metadata.digest,
        }
        payload_sha256 = sha256_text(canonical_json(canonical_payload))
        request = OrderRequest(
            symbol=parsed["instrument"],
            side=Side(parsed["side"].lower()),
            order_type=OrderType.LIMIT,
            quantity=parsed["quantity"],
            account_id=target.provider_account_id,
            client_order_id=parsed["client_order_id"],
            price=parsed["price"],
            time_in_force="GTC",
            reduce_only=False,
            idempotency_key=parsed["client_order_id"],
        )
        order_request = request.to_dict()
        order_request_sha256 = sha256_text(
            canonical_json(
                {
                    "exchange_name": target.exchange_name,
                    "operation": "BtApi.make_order(normalized=True)",
                    "request": order_request,
                }
            )
        )
        command, created = self._journal.prepare_or_get(
            fence,
            principal_ref=principal_ref,
            intent_id=parsed["intent_id"],
            risk_intent_id=risk_intent_id,
            payload_sha256=payload_sha256,
            canonical_payload=canonical_payload,
            wire_scope=_wire_scope_payload(scope),
            exchange_name=target.exchange_name,
            provider_account_id=target.provider_account_id,
            client_order_id=parsed["client_order_id"],
            order_request=order_request,
            order_request_sha256=order_request_sha256,
        )
        return command, created

    def _resolve_duplicate(
        self,
        fence: RiskOrderWriterFence,
        command: RiskOrderCommand,
        *,
        target: RiskOrderTarget,
        admission: DurableRiskAdmissionAdapter,
        contract: RiskBtApiProviderContract,
    ) -> RiskOrderCommand:
        if command.state in {"ADMISSION_DENIED", "NO_DISPATCH"}:
            return command
        if command.state == "ACKED_TRACKED":
            if self._has_resolved_ack_history(fence, command):
                return command
            return self._mark_unknown(
                fence,
                command,
                "DISPATCH_PROOF_RECOVERY_UNCERTAIN",
                permit_id=command.permit_id,
                allow_ack_to_unknown=True,
            )
        if command.state in {"PROVIDER_QUERY", "UNKNOWN"} and self._has_resolved_ack_history(
            fence, command
        ):
            # A durable ACK plus Risk's exact resolved receipt proves this is
            # the previously accepted command. A duplicate submit never
            # dispatches or re-runs immutable proof recovery.
            return command
        try:
            has_proof_ready = self._journal.has_proof_ready_history(
                fence,
                principal_ref=command.principal_ref,
                intent_id=command.intent_id,
            )
        except Exception:
            return self._mark_unknown(
                fence,
                command,
                "DISPATCH_PROOF_RECOVERY_UNCERTAIN",
                permit_id=command.permit_id,
            )
        if has_proof_ready:
            return self._recover_proof_ready(fence, command, target=target, contract=contract)
        if command.state == "PREPARED":
            return self._continue_unclaimed(
                fence, command, target=target, admission=admission, contract=contract
            )
        if command.state == "RESERVED":
            return self._continue_reserved(
                fence, command, target=target, admission=admission, contract=contract
            )
        if command.state in {"CLAIMED", "DISPATCHING", "ORDER_OBSERVATION", "FILL_OBSERVATION"}:
            return self._recover_claimed(fence, command)
        return command

    def _has_resolved_ack_history(
        self, fence: RiskOrderWriterFence, command: RiskOrderCommand
    ) -> bool:
        if command.permit_id is None:
            return False
        try:
            receipt = self._gate.dispatch_resolution_receipt(command.permit_id)
            return self._journal.has_resolved_ack_history(
                fence,
                principal_ref=command.principal_ref,
                intent_id=command.intent_id,
                receipt=receipt,
            )
        except Exception:
            return False

    def _binding_for_command(
        self, command: RiskOrderCommand
    ) -> (
        tuple[
            WireScope,
            RiskOrderTarget,
            DurableRiskAdmissionAdapter,
            RiskBtApiProviderContract | None,
        ]
        | None
    ):
        """Resolve persisted concrete scope through the current trusted template."""

        values = dict(command.wire_scope)
        if set(values) != _WIRE_SCOPE_FIELDS_SET:
            return None
        account_refs = values.get("account_refs")
        if type(account_refs) is not list:
            return None
        try:
            persisted_scope = WireScope(
                execution_domain_ref=values["execution_domain_ref"],
                account_ref=values["account_ref"],
                account_refs=tuple(account_refs),
                strategy_ref=values["strategy_ref"],
                run_id=values["run_id"],
                manual_book_ref=values["manual_book_ref"],
                portfolio_ref=values["portfolio_ref"],
                group_id=values["group_id"],
                leg_id=values["leg_id"],
            )
        except (TypeError, ValueError):
            return None
        binding = self._resolve_scope_binding(persisted_scope)
        if binding is None:
            return None
        _, target, admission, contract = binding
        if (
            target.account_scope != command.scope
            or target.strategy_id != persisted_scope.strategy_ref
            or target.exchange_name != command.exchange_name
            or target.provider_account_id != command.provider_account_id
        ):
            return None
        return binding

    def _recover_proof_ready(
        self,
        fence: RiskOrderWriterFence,
        command: RiskOrderCommand,
        *,
        target: RiskOrderTarget,
        contract: RiskBtApiProviderContract | None,
    ) -> RiskOrderCommand:
        """Resolve a saved proof from Risk's exact receipt without provider I/O."""

        claim: DispatchClaimBinding | None = None
        try:
            if command.permit_id is None:
                raise RiskOrderJournalError("RECOVERY_PERMIT_MISSING")
            receipt = self._gate.dispatch_resolution_receipt(command.permit_id)
            if type(receipt) is not DispatchResolutionReceipt:
                raise RiskOrderJournalError("RECOVERY_RECEIPT_INVALID")
            claim = receipt.claim
            if (
                claim.scope != fence.scope
                or claim.permit_id != command.permit_id
                or claim.intent_id != command.risk_intent_id
                or target.account_scope != fence.scope
            ):
                raise RiskOrderJournalError("RECOVERY_RECEIPT_SCOPE_MISMATCH")
            source = self._journal.load_dispatch_proof_for_recovery(
                fence,
                principal_ref=command.principal_ref,
                intent_id=command.intent_id,
                claim=claim,
            )
            if source is None:
                raise RiskOrderJournalError("RECOVERY_PROOF_SOURCE_MISSING")
            _validate_recovery_observation_source(source, target=target, contract=contract)
            _validate_dispatch_resolution_receipt(receipt, source)

            if receipt.status is DispatchResolutionReceiptStatus.UNRESOLVED_ACTIVE:
                attestation = self._journal.append_proof_recovery_attestation(fence, source)
                # Resolve may have committed before a caller-side failure.
                # The read-only receipt below decides whether ACK is safe.
                with suppress(Exception):
                    self._gate.resolve_dispatch_freeze_recovered(source.proof, attestation)
                receipt = self._gate.dispatch_resolution_receipt(command.permit_id)
                if type(receipt) is not DispatchResolutionReceipt:
                    raise RiskOrderJournalError("RECOVERY_RECEIPT_INVALID")
                if (
                    receipt.status is not DispatchResolutionReceiptStatus.ACKED_TRACKED_RESOLVED
                    or receipt.recovery_attestation != attestation
                ):
                    raise RiskOrderJournalError("RECOVERY_RESOLUTION_UNCONFIRMED")
            elif receipt.status is not DispatchResolutionReceiptStatus.ACKED_TRACKED_RESOLVED:
                raise RiskOrderJournalError("RECOVERY_RECEIPT_STATE_INVALID")

            # Re-read every journal identity after Risk returns. This also
            # covers the crash window where Risk committed but ACKED_TRACKED
            # had not yet been appended.
            current_source = self._journal.load_dispatch_proof_for_recovery(
                fence,
                principal_ref=command.principal_ref,
                intent_id=command.intent_id,
                claim=claim,
            )
            if current_source is None:
                raise RiskOrderJournalError("RECOVERY_PROOF_SOURCE_MISSING")
            _validate_recovery_observation_source(current_source, target=target, contract=contract)
            _validate_dispatch_resolution_receipt(receipt, current_source)
            return self._journal.append_recovered_ack(fence, source=current_source, receipt=receipt)
        except Exception:
            latest = (
                self._journal.get_command(fence.scope, command.principal_ref, command.intent_id)
                or command
            )
            if latest.state in {"ACKED_TRACKED", "ADMISSION_DENIED", "NO_DISPATCH"}:
                return latest
            try:
                return self._mark_unknown(
                    fence,
                    latest,
                    "DISPATCH_PROOF_RECOVERY_UNCERTAIN",
                    permit_id=latest.permit_id,
                    claim=claim,
                )
            except Exception:
                return (
                    self._journal.get_command(fence.scope, command.principal_ref, command.intent_id)
                    or latest
                )

    def _drive_first_attempt(
        self,
        fence: RiskOrderWriterFence,
        *,
        identity: WireIdentity,
        scope: WireScope,
        target: RiskOrderTarget,
        admission: DurableRiskAdmissionAdapter,
        contract: RiskBtApiProviderContract,
        parsed: Mapping[str, Any],
        command: RiskOrderCommand,
    ) -> Mapping[str, Any]:
        del identity, scope  # Authenticated values already bound by target selection.
        risk_order = self._admission_order(command, target, parsed)
        try:
            permit = admission.reserve(risk_order)
        except RiskAdmissionDeniedError as error:
            command = self._record_denial(fence, command, error.code)
            return _command_reply(command, target.account_scope, ok=False, code="ADMISSION_DENIED")
        except Exception:
            command = self._mark_unknown(fence, command, "RISK_RESERVATION_UNCERTAIN")
            return _command_reply(command, target.account_scope)

        try:
            self._journal.append_event(
                fence,
                principal_ref=command.principal_ref,
                intent_id=command.intent_id,
                event_type="RESERVED",
                payload={"permit_id": permit.permit_id, "risk_intent_id": risk_order.intent_id},
            )
        except Exception:
            command = self._mark_unknown(fence, command, "RESERVATION_JOURNAL_UNCERTAIN")
            return _command_reply(command, target.account_scope)
        command = (
            self._journal.get_command(
                target.account_scope, command.principal_ref, command.intent_id
            )
            or command
        )
        return _command_reply(
            self._continue_reserved(
                fence,
                command,
                target=target,
                admission=admission,
                contract=contract,
                risk_order=risk_order,
            ),
            target.account_scope,
        )

    def _continue_unclaimed(
        self,
        fence: RiskOrderWriterFence,
        command: RiskOrderCommand,
        *,
        target: RiskOrderTarget,
        admission: DurableRiskAdmissionAdapter,
        contract: RiskBtApiProviderContract,
    ) -> RiskOrderCommand:
        try:
            risk_order = self._admission_order(command, target, None)
            permit = admission.reserve(risk_order)
        except RiskAdmissionDeniedError as error:
            return self._record_denial(fence, command, error.code)
        except Exception:
            return self._mark_unknown(fence, command, "RISK_RESERVATION_UNCERTAIN")
        try:
            self._journal.append_event(
                fence,
                principal_ref=command.principal_ref,
                intent_id=command.intent_id,
                event_type="RESERVED",
                payload={"permit_id": permit.permit_id, "risk_intent_id": risk_order.intent_id},
            )
        except Exception:
            return self._mark_unknown(fence, command, "RESERVATION_JOURNAL_UNCERTAIN")
        current = self._journal.get_command(
            target.account_scope, command.principal_ref, command.intent_id
        )
        if current is None:
            return command
        return self._continue_reserved(
            fence,
            current,
            target=target,
            admission=admission,
            contract=contract,
            risk_order=risk_order,
        )

    def _continue_reserved(
        self,
        fence: RiskOrderWriterFence,
        command: RiskOrderCommand,
        *,
        target: RiskOrderTarget,
        admission: DurableRiskAdmissionAdapter,
        contract: RiskBtApiProviderContract,
        risk_order: _AdmissionOrder | None = None,
    ) -> RiskOrderCommand:
        permit_id = command.permit_id
        if permit_id is None:
            return self._mark_unknown(fence, command, "RESERVED_PERMIT_MISSING")
        try:
            self._gate.dispatch_claim_binding(permit_id)
        except PermitInvalidError as error:
            if error.code != "PERMIT_NOT_DISPATCH_CLAIMED":
                return self._mark_unknown(
                    fence, command, "RISK_CLAIM_STATE_UNCERTAIN", permit_id=permit_id
                )
        else:
            return self._recover_claimed(fence, command)

        if risk_order is None:
            risk_order = self._admission_order(command, target, None)
        try:
            admission.validate(permit_id, risk_order)
            admission.claim_for_dispatch(permit_id, risk_order)
            claim = self._gate.dispatch_claim_binding(permit_id)
        except RiskAdmissionDeniedError as error:
            try:
                admission.release(permit_id, "risk_order_not_dispatched")
            except Exception:
                return self._mark_unknown(
                    fence, command, "UNCLAIMED_RELEASE_UNCERTAIN", permit_id=permit_id
                )
            return self._record_denial(fence, command, error.code, permit_id=permit_id)
        except Exception:
            try:
                claim = self._gate.dispatch_claim_binding(permit_id)
            except Exception:
                return self._mark_unknown(
                    fence, command, "RISK_CLAIM_UNCERTAIN", permit_id=permit_id
                )
            return self._settle_and_unknown(
                fence,
                command,
                admission=admission,
                claim=claim,
                reason="CLAIM_RESULT_UNCERTAIN",
            )

        try:
            self._journal.append_event(
                fence,
                principal_ref=command.principal_ref,
                intent_id=command.intent_id,
                event_type="CLAIMED",
                payload={
                    "claim": _claim_payload(claim),
                    "permit_id": permit_id,
                    "risk_intent_id": risk_order.intent_id,
                },
            )
            self._journal.append_event(
                fence,
                principal_ref=command.principal_ref,
                intent_id=command.intent_id,
                event_type="DISPATCHING",
                payload={
                    "dispatch_attempt_count": 1,
                    "permit_id": permit_id,
                    "risk_intent_id": risk_order.intent_id,
                },
            )
        except Exception:
            return self._settle_and_unknown(
                fence,
                command,
                admission=admission,
                claim=claim,
                reason="DISPATCH_MARK_UNCERTAIN",
            )

        request = OrderRequest.from_dict(command.order_request)
        try:
            acknowledgement = self._bt_api.make_order(
                command.exchange_name, request, normalized=True
            )
        except Exception:
            return self._settle_and_unknown(
                fence,
                command,
                admission=admission,
                claim=claim,
                reason="PROVIDER_WRITE_OUTCOME_UNKNOWN",
            )
        if not isinstance(acknowledgement, Mapping):
            return self._settle_and_unknown(
                fence,
                command,
                admission=admission,
                claim=claim,
                reason="PROVIDER_ACK_INVALID",
            )

        return self._reconcile_claimed_order(
            fence,
            command,
            target=target,
            admission=admission,
            contract=contract,
            claim=claim,
            acknowledgement=acknowledgement,
            request=request,
            allow_resolution=True,
        )

    def _reconcile_claimed_order(
        self,
        fence: RiskOrderWriterFence,
        command: RiskOrderCommand,
        *,
        target: RiskOrderTarget,
        admission: DurableRiskAdmissionAdapter,
        contract: RiskBtApiProviderContract,
        claim: DispatchClaimBinding,
        acknowledgement: Mapping[str, Any] | None,
        request: OrderRequest,
        allow_resolution: bool,
    ) -> RiskOrderCommand:
        try:
            order = self._bt_api.query_order(
                command.exchange_name,
                QueryOrderRequest(
                    symbol=request.symbol,
                    account_id=command.provider_account_id,
                    client_order_id=command.client_order_id,
                ),
                normalized=True,
            )
            fills = self._bt_api.get_deals(
                command.exchange_name,
                request.symbol,
                normalized=True,
                client_order_id=command.client_order_id,
                account_id=command.provider_account_id,
            )
            order_observation, fill_observation = _observation_payloads(
                command,
                target=target,
                contract=contract,
                request=request,
                acknowledgement=acknowledgement,
                order=order,
                fills=fills,
            )
        except Exception:
            return self._settle_and_unknown(
                fence,
                command,
                admission=admission,
                claim=claim,
                reason="PROVIDER_OBSERVATION_INCOMPLETE",
            )

        try:
            order_event, fill_event, reconciliation_sha = (
                self._journal.append_resolution_observations(
                    fence,
                    principal_ref=command.principal_ref,
                    intent_id=command.intent_id,
                    order_observation=order_observation,
                    fill_observation=fill_observation,
                )
            )
        except Exception:
            return self._settle_and_unknown(
                fence,
                command,
                admission=admission,
                claim=claim,
                reason="OBSERVATION_JOURNAL_UNCERTAIN",
            )

        if not _is_complete_open_zero_fill(order, fills, order_observation, fill_observation):
            return self._settle_and_unknown(
                fence,
                command,
                admission=admission,
                claim=claim,
                reason="ORDER_NOT_TRACKABLE_ZERO_FILL",
            )

        try:
            admission.ensure_settled(claim.permit_id)
            exposure_id, exposure_sha, _reference = self._journal.store_exposure_reference(
                fence,
                principal_ref=command.principal_ref,
                intent_id=command.intent_id,
                claim=claim,
                evidence_class=_evidence_class(claim.scope),
                permit_id=claim.permit_id,
                provider_order_id=str(order["order_id"]),
                accepted_request_sha256=command.order_request_sha256,
                reconciliation_evidence_sha256=reconciliation_sha,
                observation_revision=fill_event.revision,
                observation_record_sha256=fill_event.record_sha256,
            )
            proof_command = self._journal.get_command(
                target.account_scope, command.principal_ref, command.intent_id
            )
            if proof_command is None or proof_command.state != "PROOF_READY":
                raise RiskOrderJournalError("PROOF_READY_EVENT_MISSING")
            proof = DispatchTrackedOrderProof(
                scope=claim.scope,
                permit_id=claim.permit_id,
                intent_id=claim.intent_id,
                intent_hash=claim.intent_hash,
                cause_id=claim.cause_id,
                claim_digest=claim.claim_digest,
                evidence_class=_evidence_class(claim.scope),
                dispatch_attempt_count=1,
                journal_revision=proof_command.latest_revision,
                journal_record_sha256=proof_command.latest_record_sha256,
                reconciliation_evidence_sha256=reconciliation_sha,
                writer_fence_sha256=fence.fence_sha256,
                provider_order_id=str(order["order_id"]),
                accepted_request_sha256=command.order_request_sha256,
                exposure_reservation_id=exposure_id,
                exposure_reservation_sha256=exposure_sha,
                filled_quantity=0,
                trade_count=0,
            )
            if allow_resolution:
                self._gate.resolve_dispatch_freeze(proof)
                self._journal.append_event(
                    fence,
                    principal_ref=command.principal_ref,
                    intent_id=command.intent_id,
                    event_type="ACKED_TRACKED",
                    payload={
                        "dispatch_attempt_count": 1,
                        "exposure_reference_id": exposure_id,
                        "exposure_reference_sha256": exposure_sha,
                        "filled_quantity": 0,
                        "permit_id": claim.permit_id,
                        "provider_order_id": str(order["order_id"]),
                        "public_state": "ACKED_TRACKED",
                        "reconciliation_evidence_sha256": reconciliation_sha,
                        "risk_intent_id": claim.intent_id,
                        "trade_count": 0,
                    },
                )
            current = self._journal.get_command(
                target.account_scope, command.principal_ref, command.intent_id
            )
            return current or proof_command
        except RiskAdmissionDeniedError as error:
            return self._mark_unknown(
                fence, command, "RISK_PROOF_REJECTED_" + error.code, permit_id=claim.permit_id
            )
        except Exception:
            return self._mark_unknown(
                fence, command, "DISPATCH_PROOF_RESOLUTION_UNCERTAIN", permit_id=claim.permit_id
            )

    def _query(
        self,
        *,
        principal_ref: str,
        scope: WireScope,
        target: RiskOrderTarget,
        contract: RiskBtApiProviderContract | None,
        arguments: Mapping[str, Any],
    ) -> Mapping[str, Any]:
        if not _has_exact_argument_fields(arguments, frozenset({"intent_id"})):
            return _reply(False, "INVALID_REQUEST", {"found": False, "record": None})
        intent_id = arguments.get("intent_id")
        if type(intent_id) is not str or not _REFERENCE.fullmatch(intent_id):
            return _reply(False, "INVALID_REQUEST", {"found": False, "record": None})
        command = self._journal.get_command(target.account_scope, principal_ref, intent_id)
        if command is None:
            return _reply(True, "OK", {"found": False, "record": None})
        with self._journal.writer_fence(target.account_scope) as fence:
            command = (
                self._journal.get_command(target.account_scope, principal_ref, intent_id) or command
            )
            if command.state == "PREPARED":
                return _command_reply(command, target.account_scope)
            has_resolved_ack = command.state in {
                "ACKED_TRACKED",
                "PROVIDER_QUERY",
                "UNKNOWN",
            } and self._has_resolved_ack_history(fence, command)
            if command.state == "ACKED_TRACKED" and not has_resolved_ack:
                command = self._mark_unknown(
                    fence,
                    command,
                    "DISPATCH_PROOF_RECOVERY_UNCERTAIN",
                    permit_id=command.permit_id,
                    allow_ack_to_unknown=True,
                )
                return _command_reply(command, target.account_scope)
            if command.state not in {"ACKED_TRACKED", "ADMISSION_DENIED", "NO_DISPATCH"}:
                try:
                    has_proof_ready = self._journal.has_proof_ready_history(
                        fence,
                        principal_ref=principal_ref,
                        intent_id=intent_id,
                    )
                except Exception:
                    latest = (
                        self._journal.get_command(target.account_scope, principal_ref, intent_id)
                        or command
                    )
                    command = self._mark_unknown(
                        fence,
                        latest,
                        "DISPATCH_PROOF_RECOVERY_UNCERTAIN",
                        permit_id=latest.permit_id,
                    )
                    return _command_reply(command, target.account_scope)
                if has_proof_ready and not has_resolved_ack:
                    command = self._recover_proof_ready(
                        fence, command, target=target, contract=contract
                    )
                    # Unresolved proof recovery is intentionally journal/Risk-only.
                    return _command_reply(command, target.account_scope)
            if command.state in {
                "RESERVED",
                "CLAIMED",
                "DISPATCHING",
                "ORDER_OBSERVATION",
                "FILL_OBSERVATION",
            }:
                command = self._recover_claimed(fence, command)
            if command.state in {"REJECTED", "ADMISSION_DENIED", "NO_DISPATCH"}:
                return _command_reply(command, target.account_scope)
            if contract is None or not contract.write_ready:
                return _command_reply(command, target.account_scope)
            if has_resolved_ack:
                # The initial dispatch latch was already resolved. Later fills
                # remain in Risk's original settled reservation until Risk02
                # consumes a separate complete exposure observation.
                try:
                    request = OrderRequest.from_dict(command.order_request)
                    order = self._bt_api.query_order(
                        command.exchange_name,
                        QueryOrderRequest(
                            symbol=request.symbol,
                            account_id=command.provider_account_id,
                            client_order_id=command.client_order_id,
                        ),
                        normalized=True,
                    )
                    fills = self._bt_api.get_deals(
                        command.exchange_name,
                        request.symbol,
                        normalized=True,
                        client_order_id=command.client_order_id,
                        account_id=command.provider_account_id,
                    )
                    observed = _followup_observation(
                        command, contract=contract, order=order, fills=fills
                    )
                    state = (
                        "ACKED_TRACKED"
                        if _is_complete_open_zero_fill(order, fills, None, None)
                        else "UNKNOWN"
                    )
                    observed["public_state"] = state
                    observed["dispatch_attempt_count"] = 1
                    self._journal.append_event(
                        fence,
                        principal_ref=principal_ref,
                        intent_id=intent_id,
                        event_type="PROVIDER_QUERY",
                        payload=observed,
                    )
                except Exception:
                    latest = self._journal.get_command(
                        target.account_scope, principal_ref, intent_id
                    )
                    if latest is not None:
                        command = self._mark_unknown(
                            fence,
                            latest,
                            "PROVIDER_QUERY_UNCERTAIN",
                            permit_id=latest.permit_id,
                            allow_ack_to_unknown=True,
                        )
                        return _command_reply(command, target.account_scope)
                command = (
                    self._journal.get_command(target.account_scope, principal_ref, intent_id)
                    or command
                )
                return _command_reply(command, target.account_scope)

            # UNKNOWN recovery uses only the durable server-generated client ID.
            # It never calls make_order and only resolves Risk when the current
            # dispatch latch and a complete zero-fill snapshot still exist.
            if command.state not in {"UNKNOWN", "PROVIDER_QUERY"}:
                return _command_reply(command, target.account_scope)
            command = self._query_unknown_dispatch(fence, command, target=target, contract=contract)
            return _command_reply(command, target.account_scope)

    def _query_unknown_dispatch(
        self,
        fence: RiskOrderWriterFence,
        command: RiskOrderCommand,
        *,
        target: RiskOrderTarget,
        contract: RiskBtApiProviderContract,
    ) -> RiskOrderCommand:
        if command.permit_id is None:
            return command
        try:
            claim = self._gate.dispatch_claim_binding(command.permit_id)
            admission = self._admission_for_scope(target)
            admission.ensure_settled(command.permit_id)
            request = OrderRequest.from_dict(command.order_request)
            return self._reconcile_claimed_order(
                fence,
                command,
                target=target,
                admission=admission,
                contract=contract,
                claim=claim,
                acknowledgement=None,
                request=request,
                allow_resolution=True,
            )
        except Exception:
            latest = self._journal.get_command(
                target.account_scope, command.principal_ref, command.intent_id
            )
            if latest is None:
                return command
            try:
                return self._mark_unknown(
                    fence, latest, "PROVIDER_QUERY_UNCERTAIN", permit_id=command.permit_id
                )
            except Exception:
                return latest

    def _recover_claimed(
        self, fence: RiskOrderWriterFence, command: RiskOrderCommand
    ) -> RiskOrderCommand:
        if command.permit_id is None:
            return self._mark_unknown(fence, command, "DISPATCH_CLAIM_UNPROVEN")
        try:
            claim = self._gate.dispatch_claim_binding(command.permit_id)
        except PermitInvalidError:
            return self._mark_unknown(
                fence, command, "DISPATCH_CLAIM_UNCERTAIN", permit_id=command.permit_id
            )
        try:
            self._admission_for_scope_by_account(claim.scope).ensure_settled(command.permit_id)
        except Exception:
            return self._mark_unknown(
                fence, command, "RISK_SETTLEMENT_UNCERTAIN", permit_id=command.permit_id
            )
        return self._mark_unknown(
            fence,
            command,
            "INTERRUPTED_AFTER_RISK_CLAIM",
            permit_id=command.permit_id,
            claim=claim,
        )

    def _settle_and_unknown(
        self,
        fence: RiskOrderWriterFence,
        command: RiskOrderCommand,
        *,
        admission: DurableRiskAdmissionAdapter,
        claim: DispatchClaimBinding,
        reason: str,
    ) -> RiskOrderCommand:
        try:
            admission.ensure_settled(claim.permit_id)
        except Exception:
            reason = "RISK_SETTLEMENT_UNCERTAIN"
        latest = (
            self._journal.get_command(fence.scope, command.principal_ref, command.intent_id)
            or command
        )
        return self._mark_unknown(fence, latest, reason, permit_id=claim.permit_id, claim=claim)

    def _record_denial(
        self,
        fence: RiskOrderWriterFence,
        command: RiskOrderCommand,
        reason: str,
        *,
        permit_id: str | None = None,
    ) -> RiskOrderCommand:
        self._journal.append_event(
            fence,
            principal_ref=command.principal_ref,
            intent_id=command.intent_id,
            event_type="ADMISSION_DENIED",
            payload={
                "permit_id": permit_id,
                "public_state": "REJECTED",
                "reason_code": _finite_code(reason),
            },
        )
        return (
            self._journal.get_command(fence.scope, command.principal_ref, command.intent_id)
            or command
        )

    def _mark_unknown(
        self,
        fence: RiskOrderWriterFence,
        command: RiskOrderCommand,
        reason: str,
        *,
        permit_id: str | None = None,
        claim: DispatchClaimBinding | None = None,
        allow_ack_to_unknown: bool = False,
    ) -> RiskOrderCommand:
        current = (
            self._journal.get_command(fence.scope, command.principal_ref, command.intent_id)
            or command
        )
        if current.state in {"ADMISSION_DENIED", "NO_DISPATCH"}:
            return current
        if current.state == "ACKED_TRACKED" and not allow_ack_to_unknown:
            return current
        payload: dict[str, Any] = {
            "dispatch_attempt_count": 1 if current.state not in {"PREPARED", "RESERVED"} else 0,
            "permit_id": permit_id or current.permit_id,
            "public_state": "UNKNOWN",
            "reason_code": _finite_code(reason),
        }
        if claim is not None:
            payload["claim"] = _claim_payload(claim)
            payload["claim_digest"] = claim.claim_digest
        self._journal.append_event(
            fence,
            principal_ref=current.principal_ref,
            intent_id=current.intent_id,
            event_type="UNKNOWN",
            payload=payload,
        )
        return (
            self._journal.get_command(fence.scope, current.principal_ref, current.intent_id)
            or current
        )

    def _admission_order(
        self,
        command: RiskOrderCommand,
        target: RiskOrderTarget,
        parsed: Mapping[str, Any] | None,
    ) -> _AdmissionOrder:
        request = OrderRequest.from_dict(command.order_request)
        metadata = target.registry.get(request.symbol)
        if metadata is None:
            raise RiskOrderEntryError("INSTRUMENT_METADATA_MISSING")
        payload_sha256 = command.payload_sha256
        if parsed is not None:
            payload_sha256 = command.payload_sha256
        return _AdmissionOrder(
            intent_id=command.risk_intent_id,
            scope=_AdmissionScope(
                provider=target.account_scope.provider,
                environment=target.account_scope.environment,
                account_ref=target.account_scope.account_id,
                strategy_id=target.strategy_id,
            ),
            instrument=request.symbol,
            quantity=request.quantity,
            price=request.price or Decimal(0),
            position_effect=_PositionEffect.OPEN,
            metadata_version=str(command.canonical_payload["metadata_version"]),
            tags=MappingProxyType({INSTRUMENT_METADATA_DIGEST_TAG: metadata.digest}),
            payload_sha256=payload_sha256,
        )

    def _admission_for_scope(self, target: RiskOrderTarget) -> DurableRiskAdmissionAdapter:
        for scope, configured in self._targets.items():
            if configured is target:
                return self._admission[scope]
        raise RiskOrderEntryError("RISK_ORDER_TARGET_NOT_CONFIGURED")

    def _admission_for_scope_by_account(
        self, account_scope: AccountScope
    ) -> DurableRiskAdmissionAdapter:
        for scope, target in self._targets.items():
            if target.account_scope == account_scope:
                return self._admission[scope]
        raise RiskOrderEntryError("RISK_ORDER_ACCOUNT_NOT_CONFIGURED")


def _parse_order(
    arguments: Mapping[str, Any],
    target: RiskOrderTarget,
    *,
    scope: WireScope,
    principal_ref: str,
) -> dict[str, Any]:
    for name in ("intent_id", "signal_id", "instrument", "metadata_version"):
        if type(arguments.get(name)) is not str or not _REFERENCE.fullmatch(arguments[name]):
            raise RiskOrderEntryError("INVALID_REQUEST")
    if type(arguments["position_effect"]) is not str or arguments["position_effect"] != "OPEN":
        raise RiskOrderEntryError("OPEN_ORDERS_ONLY")
    if (
        type(arguments["order_type"]) is not str
        or type(arguments["time_in_force"]) is not str
        or arguments["order_type"] != "LIMIT"
        or arguments["time_in_force"] != "GTC"
    ):
        raise RiskOrderEntryError("LIMIT_GTC_ONLY")
    if type(arguments["side"]) is not str or arguments["side"] not in {"BUY", "SELL"}:
        raise RiskOrderEntryError("INVALID_REQUEST")
    if arguments["reduce_only"] is not False:
        raise RiskOrderEntryError("OPEN_ORDERS_ONLY")
    if not isinstance(arguments["tags"], Mapping) or arguments["tags"]:
        raise RiskOrderEntryError("CLIENT_TAGS_UNSUPPORTED")
    deadline = arguments["deadline_ns"]
    if deadline is not None and (type(deadline) is not int or deadline <= 0):
        raise RiskOrderEntryError("INVALID_REQUEST")
    if deadline is not None and time.time_ns() >= deadline:
        raise RiskOrderEntryError("DEADLINE_EXPIRED")
    quantity = _positive_decimal(arguments["quantity"], "INVALID_REQUEST")
    price = _positive_decimal(arguments["price"], "INVALID_REQUEST")
    metadata = target.registry.get(arguments["instrument"])
    if metadata is None:
        raise RiskOrderEntryError("INSTRUMENT_METADATA_UNREGISTERED")
    if arguments["metadata_version"] != metadata.metadata_version:
        raise RiskOrderEntryError("METADATA_VERSION_MISMATCH")
    client_order_id = (
        "risk-"
        + sha256_text(
            canonical_json(
                {
                    "account_scope": target.account_scope.key,
                    "client_intent_id": arguments["intent_id"],
                    "exchange_name": target.exchange_name,
                    "principal_ref": principal_ref,
                    "provider_account_id": target.provider_account_id,
                    "strategy_id": target.strategy_id,
                    "wire_scope": _wire_scope_identity_payload(scope),
                }
            )
        )[:40]
    )
    canonical_payload = {
        "deadline_ns": deadline,
        "instrument": arguments["instrument"],
        "intent_id": arguments["intent_id"],
        "metadata_version": arguments["metadata_version"],
        "order_type": "LIMIT",
        "position_effect": "OPEN",
        "price": _decimal_text(price),
        "quantity": _decimal_text(quantity),
        "reduce_only": False,
        "side": arguments["side"],
        "signal_id": arguments["signal_id"],
        "tags": {},
        "time_in_force": "GTC",
    }
    return {
        "canonical_payload": canonical_payload,
        "client_order_id": client_order_id,
        "intent_id": arguments["intent_id"],
        "instrument": arguments["instrument"],
        "metadata_version": arguments["metadata_version"],
        "price": price,
        "quantity": quantity,
        "side": arguments["side"],
    }


def _positive_decimal(value: object, code: str) -> Decimal:
    if type(value) not in (str, int, Decimal):
        raise RiskOrderEntryError(code)
    try:
        number = Decimal(value)
    except (InvalidOperation, ValueError):
        raise RiskOrderEntryError(code) from None
    if not number.is_finite() or number <= 0:
        raise RiskOrderEntryError(code)
    return number


def _has_exact_argument_fields(arguments: object, expected_fields: frozenset[str]) -> bool:
    """Accept the service registry's immutable Mapping payload projection."""

    return isinstance(arguments, Mapping) and frozenset(arguments) == expected_fields


def _decimal_text(value: Decimal) -> str:
    return format(value, "f")


def _wire_scope_payload(scope: WireScope) -> dict[str, Any]:
    return {
        "account_ref": scope.account_ref,
        "account_refs": list(scope.account_refs),
        "execution_domain_ref": scope.execution_domain_ref,
        "group_id": scope.group_id,
        "leg_id": scope.leg_id,
        "manual_book_ref": scope.manual_book_ref,
        "portfolio_ref": scope.portfolio_ref,
        "run_id": scope.run_id,
        "strategy_ref": scope.strategy_ref,
    }


def _wire_scope_identity_payload(scope: WireScope) -> dict[str, Any]:
    """Bind business identity to the trusted scope while excluding run generation."""

    result = _wire_scope_payload(scope)
    result.pop("run_id")
    return result


def _matches_run_wildcard(template: WireScope, requested: WireScope) -> bool:
    """Match only a trusted strategy template whose sole variable is run_id."""

    if type(template) is not WireScope or type(requested) is not WireScope:
        return False
    if template.run_id is not None or requested.run_id is None:
        return False
    if template.strategy_ref is None or requested.strategy_ref != template.strategy_ref:
        return False
    return all(
        getattr(template, field, object()) == getattr(requested, field, None)
        for field in _WIRE_SCOPE_FIELDS
    )


def _reply(ok: bool, code: str, result: Mapping[str, Any]) -> Mapping[str, Any]:
    return {"ok": ok, "code": code, "result": dict(result)}


def _command_reply(
    command: RiskOrderCommand,
    scope: AccountScope,
    *,
    ok: bool | None = None,
    code: str | None = None,
) -> Mapping[str, Any]:
    state = {
        "PREPARED": "UNKNOWN",
        "RESERVED": "UNKNOWN",
        "CLAIMED": "UNKNOWN",
        "DISPATCHING": "UNKNOWN",
        "ORDER_OBSERVATION": "UNKNOWN",
        "FILL_OBSERVATION": "UNKNOWN",
        "PROOF_READY": "UNKNOWN",
        "PROOF_RECOVERY_ATTESTED": "UNKNOWN",
        "ADMISSION_DENIED": "REJECTED",
        "NO_DISPATCH": "REJECTED",
    }.get(command.state, command.state)
    if command.state == "PROVIDER_QUERY":
        state = command.latest_event.get("public_state", "UNKNOWN")
    latest = command.latest_event
    record = {
        "intent_id": command.intent_id,
        "scope_key": scope.key,
        "payload_sha256": command.payload_sha256,
        "state": state,
        "provider_order_id": latest.get("provider_order_id"),
        "filled_quantity": _decimal_text(Decimal(str(latest.get("filled_quantity", 0) or 0))),
        "average_price": None,
        "cumulative_commission": None,
        "permit_reference": latest.get("permit_id") or command.permit_id,
        "dispatch_attempts": int(
            latest.get(
                "dispatch_attempt_count",
                1 if state in {"ACKED_TRACKED", "UNKNOWN"} and command.permit_id else 0,
            )
        ),
        "unknown_reason": latest.get("reason_code") if state == "UNKNOWN" else None,
        "review_required": state == "UNKNOWN",
        "created_at_ns": command.created_at_ns,
        "updated_at_ns": command.updated_at_ns,
        "is_terminal": state == "REJECTED",
    }
    if ok is None:
        ok = state not in {"REJECTED"}
    if code is None:
        code = "OK" if ok else "ADMISSION_DENIED"
    return _reply(ok, code, {"found": True, "record": record})


def _evidence_class(scope: AccountScope) -> DispatchEvidenceClass:
    return (
        DispatchEvidenceClass.SIMULATION_JOURNAL
        if scope.provider.casefold() in {"fake", "fixture"}
        else DispatchEvidenceClass.NATIVE_PROVIDER_JOURNAL
    )


def _claim_payload(claim: DispatchClaimBinding) -> dict[str, Any]:
    return {
        "cause_id": claim.cause_id,
        "claim_digest": claim.claim_digest,
        "claimed_at": claim.claimed_at,
        "intent_hash": claim.intent_hash,
        "intent_id": claim.intent_id,
        "permit_id": claim.permit_id,
        "scope_key": claim.scope.key,
    }


def _finite_code(value: str) -> str:
    if type(value) is str and re.fullmatch(r"[A-Z][A-Z0-9_]{0,95}", value):
        return value
    return "UNKNOWN_FAILURE"


def _provenance(value: object, operation: str) -> dict[str, Any]:
    if type(value) is not NormalizationProvenance or value.operation != operation:
        raise RiskOrderEntryError("PROVIDER_SOURCE_PROVENANCE_MISSING")
    return value.to_dict()


def _provenance_map(value: Mapping[str, Any]) -> dict[str, Any]:
    result = dict(value)
    for key in ("source_fields", "envelope_fields"):
        fields = result.get(key)
        if type(fields) is not list or any(type(field) is not str for field in fields):
            raise RiskOrderEntryError("PROVIDER_SOURCE_PROVENANCE_INVALID")
    for key in ("source_values", "envelope_values"):
        values = result.get(key)
        if type(values) is not dict:
            raise RiskOrderEntryError("PROVIDER_SOURCE_PROVENANCE_INVALID")
    return result


def _restore_provenance(value: object, *, operation: str) -> NormalizationProvenance:
    if not isinstance(value, Mapping) or set(value) != {
        "schema",
        "operation",
        "source_fields",
        "source_values",
        "envelope_fields",
        "envelope_values",
        "source_sha256",
    }:
        raise RiskOrderEntryError("RECOVERY_SOURCE_PROVENANCE_INVALID")
    source_fields = value.get("source_fields")
    envelope_fields = value.get("envelope_fields")
    source_values = value.get("source_values")
    envelope_values = value.get("envelope_values")
    if (
        type(value.get("schema")) is not str
        or value.get("schema") != _ORDER_PROVENANCE_SCHEMA
        or value.get("operation") != operation
        or type(source_fields) is not list
        or any(type(item) is not str for item in source_fields)
        or source_fields != sorted(set(source_fields))
        or type(envelope_fields) is not list
        or any(type(item) is not str for item in envelope_fields)
        or envelope_fields != sorted(set(envelope_fields))
        or not isinstance(source_values, Mapping)
        or not isinstance(envelope_values, Mapping)
        or set(source_values) - set(source_fields)
        or set(envelope_values) - set(envelope_fields)
    ):
        raise RiskOrderEntryError("RECOVERY_SOURCE_PROVENANCE_INVALID")
    for values in (source_values, envelope_values):
        if any(
            type(key) is not str or type(item) not in (str, int, bool)
            for key, item in values.items()
        ):
            raise RiskOrderEntryError("RECOVERY_SOURCE_PROVENANCE_INVALID")
    source_value_map = dict(source_values)
    envelope_value_map = dict(envelope_values)
    digest = value.get("source_sha256")
    if type(digest) is not str or re.fullmatch(r"[0-9a-f]{64}", digest) is None:
        raise RiskOrderEntryError("RECOVERY_SOURCE_PROVENANCE_INVALID")
    projection = {
        "envelope_values": envelope_value_map,
        "operation": operation,
        "schema": value["schema"],
        "source_fields": source_fields,
        "source_values": source_value_map,
    }
    if sha256_text(canonical_json(projection)) != digest:
        raise RiskOrderEntryError("RECOVERY_SOURCE_PROVENANCE_INVALID")
    return NormalizationProvenance(
        schema=value["schema"],
        operation=operation,
        source_fields=tuple(source_fields),
        source_values=tuple(sorted(source_value_map.items())),
        envelope_fields=tuple(envelope_fields),
        envelope_values=tuple(sorted(envelope_value_map.items())),
        source_sha256=digest,
    )


def _verify_observation_digest(value: Mapping[str, Any]) -> None:
    digest = value.get("provider_observation_sha256")
    if type(digest) is not str or re.fullmatch(r"[0-9a-f]{64}", digest) is None:
        raise RiskOrderEntryError("RECOVERY_PROVIDER_OBSERVATION_INVALID")
    payload = dict(value)
    payload.pop("provider_observation_sha256")
    # The provider digest is created before the journal appends its server-side
    # event timestamp. The immutable journal record hash binds updated_at_ns.
    payload.pop("updated_at_ns", None)
    if sha256_text(canonical_json(payload)) != digest:
        raise RiskOrderEntryError("RECOVERY_PROVIDER_OBSERVATION_INVALID")


def _validate_recovery_observation_fields(
    order: Mapping[str, Any], fills: Mapping[str, Any]
) -> None:
    if (
        set(order) != _RECOVERY_ORDER_OBSERVATION_FIELDS
        or set(fills) != _RECOVERY_FILL_OBSERVATION_FIELDS
        or type(order.get("updated_at_ns")) is not int
        or order.get("updated_at_ns") <= 0
        or type(fills.get("updated_at_ns")) is not int
        or fills.get("updated_at_ns") <= 0
    ):
        raise RiskOrderEntryError("RECOVERY_PROVIDER_OBSERVATION_INVALID")


def _validate_dispatch_resolution_receipt(
    receipt: DispatchResolutionReceipt,
    source: DispatchProofRecoverySource,
) -> None:
    if type(receipt) is not DispatchResolutionReceipt or receipt.claim != source.claim:
        raise RiskOrderEntryError("RECOVERY_RECEIPT_MISMATCH")
    if receipt.status is DispatchResolutionReceiptStatus.UNRESOLVED_ACTIVE:
        if (
            receipt.proof is not None
            or receipt.proof_sha256 is not None
            or receipt.recovery_attestation is not None
            or receipt.dispatch_latch_active is not True
        ):
            raise RiskOrderEntryError("RECOVERY_RECEIPT_MISMATCH")
        return
    if (
        receipt.status is not DispatchResolutionReceiptStatus.ACKED_TRACKED_RESOLVED
        or receipt.proof != source.proof
        or receipt.proof_sha256 != source.proof.fingerprint
        or receipt.dispatch_latch_active is not False
        or (
            receipt.recovery_attestation is not None
            and receipt.recovery_attestation not in source.recovery_attestations
        )
    ):
        raise RiskOrderEntryError("RECOVERY_RECEIPT_MISMATCH")


def _validate_recovery_observation_source(
    source: DispatchProofRecoverySource,
    *,
    target: RiskOrderTarget,
    contract: RiskBtApiProviderContract | None,
) -> None:
    command = source.command
    if (
        type(source) is not DispatchProofRecoverySource
        or type(target) is not RiskOrderTarget
        or type(contract) is not RiskBtApiProviderContract
        or not contract.write_ready
        or target.account_scope != command.scope
        or target.exchange_name != command.exchange_name
        or target.provider_account_id != command.provider_account_id
    ):
        raise RiskOrderEntryError("RECOVERY_PROVIDER_CONTRACT_UNAVAILABLE")
    metadata = target.registry.get(str(command.canonical_payload.get("instrument", "")))
    if (
        metadata is None
        or command.canonical_payload.get("trusted_metadata_digest") != metadata.digest
        or command.canonical_payload.get("metadata_version") != metadata.metadata_version
    ):
        raise RiskOrderEntryError("RECOVERY_METADATA_BINDING_INVALID")
    try:
        request = OrderRequest.from_dict(dict(command.order_request))
        quantity = Decimal(str(command.canonical_payload["quantity"]))
        price = Decimal(str(command.canonical_payload["price"]))
    except (KeyError, TypeError, ValueError, InvalidOperation):
        raise RiskOrderEntryError("RECOVERY_REQUEST_INVALID") from None
    if (
        request.symbol != metadata.instrument
        or request.account_id != target.provider_account_id
        or request.client_order_id != command.client_order_id
        or request.idempotency_key != command.client_order_id
        or request.side.value.upper() != command.canonical_payload.get("side")
        or request.order_type.value != "limit"
        or request.time_in_force != "GTC"
        or request.reduce_only is not False
        or request.quantity != quantity
        or request.price != price
        or command.canonical_payload.get("position_effect") != "OPEN"
        or command.canonical_payload.get("order_type") != "LIMIT"
        or command.canonical_payload.get("time_in_force") != "GTC"
        or command.canonical_payload.get("reduce_only") is not False
        or command.canonical_payload.get("tags") != {}
    ):
        raise RiskOrderEntryError("RECOVERY_REQUEST_INVALID")
    if sha256_text(canonical_json(dict(command.canonical_payload))) != command.payload_sha256:
        raise RiskOrderEntryError("RECOVERY_REQUEST_INVALID")
    expected_risk_intent = "risk-order:" + sha256_text(
        canonical_json(
            {
                "account_scope": target.account_scope.key,
                "intent_id": command.intent_id,
                "principal_ref": command.principal_ref,
                "strategy_id": target.strategy_id,
                "wire_scope": _wire_scope_identity_from_payload(command.wire_scope),
            }
        )
    )
    expected_client_order_id = (
        "risk-"
        + sha256_text(
            canonical_json(
                {
                    "account_scope": target.account_scope.key,
                    "client_intent_id": command.intent_id,
                    "exchange_name": target.exchange_name,
                    "principal_ref": command.principal_ref,
                    "provider_account_id": target.provider_account_id,
                    "strategy_id": target.strategy_id,
                    "wire_scope": _wire_scope_identity_from_payload(command.wire_scope),
                }
            )
        )[:40]
    )
    if (
        command.risk_intent_id != expected_risk_intent
        or command.client_order_id != expected_client_order_id
    ):
        raise RiskOrderEntryError("RECOVERY_REQUEST_SCOPE_INVALID")

    order = dict(source.order_observation_event.payload)
    fills = dict(source.fill_observation_event.payload)
    _validate_recovery_observation_fields(order, fills)
    _verify_observation_digest(order)
    _verify_observation_digest(fills)
    if (
        order.get("complete") is not True
        or order.get("source_alignment_valid") is not True
        or type(order.get("source_row_count")) is not int
        or order.get("source_row_count") != 1
        or fills.get("complete") is not True
        or fills.get("source_alignment_valid") is not True
        or type(fills.get("source_row_count")) is not int
        or fills.get("source_row_count") != 0
        or fills.get("record_count") != 0
        or fills.get("records") != []
        or fills.get("trade_count") != 0
        or fills.get("filled_quantity") != 0
    ):
        raise RiskOrderEntryError("RECOVERY_PROVIDER_OBSERVATION_INCOMPLETE")
    order_provenance = _restore_provenance(
        order.get("normalization_provenance"), operation="query_order"
    )
    collection_provenance = _restore_provenance(
        order.get("collection_provenance"), operation="query_order"
    )
    fill_provenance = _restore_provenance(
        fills.get("normalization_provenance"), operation="get_deals"
    )
    for payload, provenance in ((order, order_provenance), (fills, fill_provenance)):
        serialized = provenance.to_dict()
        if (
            payload.get("source_fields") != serialized["source_fields"]
            or payload.get("source_values") != serialized["source_values"]
            or payload.get("envelope_fields") != serialized["envelope_fields"]
            or payload.get("envelope_values") != serialized["envelope_values"]
            or payload.get("source_sha256") != serialized["source_sha256"]
            or payload.get("normalizer_schema") != contract.normalizer_schema
        ):
            raise RiskOrderEntryError("RECOVERY_PROVIDER_PROVENANCE_MISMATCH")
    if order.get("normalization_provenance") != order_provenance.to_dict():
        raise RiskOrderEntryError("RECOVERY_PROVIDER_PROVENANCE_MISMATCH")
    if fills.get("normalization_provenance") != fill_provenance.to_dict():
        raise RiskOrderEntryError("RECOVERY_PROVIDER_PROVENANCE_MISMATCH")
    if (
        order.get("provider_contract_sha256") != contract.fingerprint
        or fills.get("provider_contract_sha256") != contract.fingerprint
        or order.get("operation") != "query_order"
        or fills.get("operation") != "get_deals"
        or order.get("account_id") != command.provider_account_id
        or fills.get("account_id") != command.provider_account_id
        or order.get("client_order_id") != command.client_order_id
        or fills.get("client_order_id") != command.client_order_id
        or order.get("provider_order_id") != source.proof.provider_order_id
        or order.get("instrument") != request.symbol
        or order.get("side") != request.side.value
        or order.get("quantity") != _decimal_text(request.quantity)
        or order.get("filled_quantity") != 0
        or order.get("schema") != contract.order_schema
        or fills.get("schema") != contract.fill_schema
    ):
        raise RiskOrderEntryError("RECOVERY_PROVIDER_OBSERVATION_MISMATCH")
    record = order.get("record")
    if type(record) is not dict:
        raise RiskOrderEntryError("RECOVERY_PROVIDER_OBSERVATION_INVALID")
    restored_order = NormalizedRecord(
        record,
        provenance=order_provenance,
        source_row_count=order["source_row_count"],
        source_alignment_valid=order["source_alignment_valid"],
        collection_provenance=collection_provenance,
    )
    restored_fills = NormalizedRecords(
        (),
        provenance=fill_provenance,
        row_provenance=(),
        source_alignment_valid=fills["source_alignment_valid"],
    )
    _verify_order_source_fields(restored_order, command, request, contract)
    if not _is_complete_open_zero_fill(restored_order, restored_fills, order, fills):
        raise RiskOrderEntryError("RECOVERY_PROVIDER_OBSERVATION_INCOMPLETE")


def _wire_scope_identity_from_payload(value: Mapping[str, Any]) -> dict[str, Any]:
    if not isinstance(value, Mapping) or set(value) != _WIRE_SCOPE_FIELDS_SET:
        raise RiskOrderEntryError("RECOVERY_SCOPE_INVALID")
    return {key: value[key] for key in _WIRE_SCOPE_FIELDS}


def _contract_envelope(
    provenance: Mapping[str, Any],
    *,
    contract: RiskBtApiProviderContract,
    operation: str,
    client_order_id: str,
    account_id: str,
) -> tuple[str, str]:
    if (
        provenance.get("schema") != contract.normalizer_schema
        or provenance.get("operation") != operation
    ):
        raise RiskOrderEntryError("PROVIDER_NORMALIZER_MISMATCH")
    envelope = provenance.get("envelope_values")
    source = provenance.get("source_values")
    if type(envelope) is not dict or type(source) is not dict:
        raise RiskOrderEntryError("PROVIDER_SOURCE_PROVENANCE_INVALID")
    schema = envelope.get("schema_version") or envelope.get("response_schema")
    watermark = envelope.get("watermark") or envelope.get("observation_watermark")
    complete = envelope.get("complete")
    if complete is None:
        complete = (
            envelope.get("snapshot_complete")
            if operation == "query_order"
            else envelope.get("history_complete")
        )
    if schema != (contract.order_schema if operation == "query_order" else contract.fill_schema):
        raise RiskOrderEntryError("PROVIDER_SCHEMA_UNVERIFIED")
    if complete is not True:
        raise RiskOrderEntryError("PROVIDER_RESPONSE_INCOMPLETE")
    envelope_client = (
        envelope.get("client_order_id")
        or envelope.get("query_client_order_id")
        or envelope.get("queried_client_order_id")
        or envelope.get("response_client_order_id")
    )
    envelope_account = envelope.get("account_id") or envelope.get("response_account_id")
    if envelope_client != client_order_id or envelope_account != account_id:
        raise RiskOrderEntryError("PROVIDER_RESPONSE_SCOPE_MISMATCH")
    if type(watermark) not in (str, int) or str(watermark).strip() == "":
        raise RiskOrderEntryError("PROVIDER_WATERMARK_MISSING")
    return str(schema), str(watermark)


def _field_value(provenance: Mapping[str, Any], field: str) -> Any:
    values = provenance.get("source_values")
    fields = provenance.get("source_fields")
    if type(values) is not dict or type(fields) is not list:
        return None
    for alias in _SOURCE_ALIASES[field]:
        if alias in fields and alias in values:
            return values[alias]
    return None


def _verify_order_source_fields(
    order: object,
    command: RiskOrderCommand,
    request: OrderRequest,
    contract: RiskBtApiProviderContract,
) -> tuple[dict[str, Any], str, str]:
    if type(order) is not NormalizedRecord:
        raise RiskOrderEntryError("NORMALIZED_ORDER_REQUIRED")
    if (
        order.source_alignment_valid is not True
        or type(order.source_row_count) is not int
        or order.source_row_count != 1
        or type(order.collection_provenance) is not NormalizationProvenance
    ):
        raise RiskOrderEntryError("PROVIDER_ORDER_CARDINALITY_UNPROVEN")
    collection_provenance = _provenance_map(_provenance(order.collection_provenance, "query_order"))
    provenance = _provenance_map(_provenance(order.normalization_provenance, "query_order"))
    collection_schema, collection_watermark = _contract_envelope(
        collection_provenance,
        contract=contract,
        operation="query_order",
        client_order_id=command.client_order_id,
        account_id=command.provider_account_id,
    )
    schema, watermark = _contract_envelope(
        provenance,
        contract=contract,
        operation="query_order",
        client_order_id=command.client_order_id,
        account_id=command.provider_account_id,
    )
    if (
        collection_schema != schema
        or collection_watermark != watermark
        or collection_provenance["envelope_values"] != provenance["envelope_values"]
    ):
        raise RiskOrderEntryError("PROVIDER_COLLECTION_PROVENANCE_MISMATCH")
    expected_values = {
        "provider_order_id": order.get("order_id"),
        "client_order_id": command.client_order_id,
        "quantity": _decimal_text(request.quantity),
        "account_id": command.provider_account_id,
        "instrument": request.symbol,
        "side": request.side.value,
    }
    for field, expected in expected_values.items():
        value = _field_value(provenance, field)
        if value is None or expected in (None, ""):
            raise RiskOrderEntryError("PROVIDER_ORDER_FIELD_UNPROVEN")
        if field == "quantity":
            try:
                matches = Decimal(str(value)) == Decimal(expected)
            except (InvalidOperation, ValueError):
                raise RiskOrderEntryError("PROVIDER_ORDER_FIELD_INVALID") from None
        elif field == "side":
            matches = str(value).lower() == expected
        else:
            matches = str(value) == str(expected)
        if not matches:
            raise RiskOrderEntryError("PROVIDER_ORDER_FIELD_MISMATCH")

    source_status = _field_value(provenance, "status")
    if source_status is None:
        raise RiskOrderEntryError("PROVIDER_ORDER_STATE_UNVERIFIED")
    status_key = str(source_status).lower().replace("-", "_")
    status_map = dict(contract.provider_status_mapping)
    if status_map.get(status_key) != "accepted":
        raise RiskOrderEntryError("PROVIDER_ORDER_STATE_UNVERIFIED")
    if str(order.get("status")) != "accepted":
        raise RiskOrderEntryError("PROVIDER_ORDER_STATE_UNVERIFIED")
    source_filled = _field_value(provenance, "filled_quantity")
    try:
        filled = Decimal(str(source_filled))
        normalized_filled = Decimal(str(order.get("filled")))
    except (InvalidOperation, ValueError):
        raise RiskOrderEntryError("PROVIDER_FILL_QUANTITY_UNVERIFIED") from None
    if not filled.is_finite() or filled < 0 or filled != normalized_filled:
        raise RiskOrderEntryError("PROVIDER_FILL_QUANTITY_UNVERIFIED")
    if not order.get("order_id") or order.get("client_order_id") != command.client_order_id:
        raise RiskOrderEntryError("PROVIDER_ORDER_IDENTITY_MISMATCH")
    return provenance, schema, watermark


def _observation_payloads(
    command: RiskOrderCommand,
    *,
    target: RiskOrderTarget,
    contract: RiskBtApiProviderContract,
    request: OrderRequest,
    acknowledgement: Mapping[str, Any] | None,
    order: object,
    fills: object,
) -> tuple[dict[str, Any], dict[str, Any]]:
    if type(fills) is not NormalizedRecords:
        raise RiskOrderEntryError("NORMALIZED_FILL_HISTORY_REQUIRED")
    if (
        fills.source_alignment_valid is not True
        or len(fills.row_provenance) != len(fills)
        or any(type(item) is not NormalizationProvenance for item in fills.row_provenance)
    ):
        raise RiskOrderEntryError("PROVIDER_FILL_HISTORY_ALIGNMENT_INVALID")
    order_provenance, order_schema, order_watermark = _verify_order_source_fields(
        order, command, request, contract
    )
    fill_provenance = _provenance_map(_provenance(fills.normalization_provenance, "get_deals"))
    fill_schema, fill_watermark = _contract_envelope(
        fill_provenance,
        contract=contract,
        operation="get_deals",
        client_order_id=command.client_order_id,
        account_id=command.provider_account_id,
    )
    if Decimal(str(_field_value(order_provenance, "filled_quantity"))) != 0:
        raise RiskOrderEntryError("PROVIDER_ORDER_NOT_ZERO_FILL")

    order_values = {
        "account_id": command.provider_account_id,
        "client_order_id": command.client_order_id,
        "collection_provenance": order.collection_provenance.to_dict(),
        "complete": True,
        "filled_quantity": 0,
        "instrument": request.symbol,
        "provider_contract_sha256": contract.fingerprint,
        "provider_order_id": str(order["order_id"]),
        "quantity": _decimal_text(request.quantity),
        "record": _safe_normalized_order(order),
        "source_alignment_valid": order.source_alignment_valid,
        "source_row_count": order.source_row_count,
        "schema": order_schema,
        "side": request.side.value,
        "status": str(order["status"]),
        "watermark": order_watermark,
    }
    order_observation = {
        **order_values,
        "normalization_provenance": order_provenance,
        "operation": "query_order",
        "source_fields": order_provenance["source_fields"],
        "source_values": order_provenance["source_values"],
        "envelope_fields": order_provenance["envelope_fields"],
        "envelope_values": order_provenance["envelope_values"],
        "source_sha256": order_provenance["source_sha256"],
        "normalizer_schema": order_provenance["schema"],
        "acknowledgement": _safe_acknowledgement(acknowledgement),
    }
    if len(fills) != 0:
        raise RiskOrderEntryError("PROVIDER_FILL_HISTORY_NONEMPTY")
    fill_observation = {
        "account_id": command.provider_account_id,
        "client_order_id": command.client_order_id,
        "complete": True,
        "normalization_provenance": fill_provenance,
        "operation": "get_deals",
        "record_count": 0,
        "records": [],
        "provider_contract_sha256": contract.fingerprint,
        "schema": fill_schema,
        "source_alignment_valid": fills.source_alignment_valid,
        "source_row_count": len(fills),
        "watermark": fill_watermark,
        "source_fields": fill_provenance["source_fields"],
        "source_values": fill_provenance["source_values"],
        "envelope_fields": fill_provenance["envelope_fields"],
        "envelope_values": fill_provenance["envelope_values"],
        "source_sha256": fill_provenance["source_sha256"],
        "normalizer_schema": fill_provenance["schema"],
        "filled_quantity": 0,
        "trade_count": 0,
    }
    order_observation["provider_observation_sha256"] = sha256_text(
        canonical_json(order_observation)
    )
    fill_observation["provider_observation_sha256"] = sha256_text(canonical_json(fill_observation))
    return order_observation, fill_observation


def _followup_observation(
    command: RiskOrderCommand,
    *,
    contract: RiskBtApiProviderContract,
    order: object,
    fills: object,
) -> dict[str, Any]:
    if type(order) is not NormalizedRecord or type(fills) is not NormalizedRecords:
        raise RiskOrderEntryError("NORMALIZED_PROVIDER_OBSERVATION_REQUIRED")
    request = OrderRequest.from_dict(command.order_request)
    order_prov, order_schema, order_watermark = _verify_order_source_fields(
        order, command, request, contract
    )
    fill_prov = _provenance_map(_provenance(fills.normalization_provenance, "get_deals"))
    if (
        fills.source_alignment_valid is not True
        or len(fills.row_provenance) != len(fills)
        or any(type(item) is not NormalizationProvenance for item in fills.row_provenance)
    ):
        raise RiskOrderEntryError("PROVIDER_FILL_HISTORY_ALIGNMENT_INVALID")
    fill_schema, fill_watermark = _contract_envelope(
        fill_prov,
        contract=contract,
        operation="get_deals",
        client_order_id=command.client_order_id,
        account_id=command.provider_account_id,
    )
    zero_open = _is_complete_open_zero_fill(order, fills, None, None)
    record = {
        "account_id": command.provider_account_id,
        "client_order_id": command.client_order_id,
        "collection_provenance": order.collection_provenance.to_dict(),
        "complete": True,
        "filled_quantity": str(order.get("filled", 0)),
        "normalization_provenance": {"order": order_prov, "fills": fill_prov},
        "operation": "orders.query",
        "provider_contract_sha256": contract.fingerprint,
        "provider_order_id": str(order.get("order_id") or ""),
        "record": _safe_normalized_order(order),
        "records": [_safe_normalized_order(item) for item in fills],
        "record_count": len(fills),
        "source_alignment_valid": order.source_alignment_valid,
        "source_row_count": order.source_row_count,
        "fill_source_alignment_valid": fills.source_alignment_valid,
        "fill_source_row_count": len(fills),
        "public_state": "ACKED_TRACKED" if zero_open else "UNKNOWN",
        "schema": {"order": order_schema, "fills": fill_schema},
        "source_fields": {
            "order": order_prov["source_fields"],
            "fills": fill_prov["source_fields"],
        },
        "source_values": {
            "order": order_prov["source_values"],
            "fills": fill_prov["source_values"],
        },
        "envelope_fields": {
            "order": order_prov["envelope_fields"],
            "fills": fill_prov["envelope_fields"],
        },
        "envelope_values": {
            "order": order_prov["envelope_values"],
            "fills": fill_prov["envelope_values"],
        },
        "source_sha256": {
            "order": order_prov["source_sha256"],
            "fills": fill_prov["source_sha256"],
        },
        "normalizer_schema": contract.normalizer_schema,
        "status": str(order.get("status") or "unknown"),
        "watermark": {"order": order_watermark, "fills": fill_watermark},
        "quantity": str(order.get("size") or "0"),
        "instrument": str(order.get("symbol") or ""),
        "side": str(order.get("side") or ""),
        "trade_count": len(fills),
    }
    record["provider_observation_sha256"] = sha256_text(canonical_json(record))
    return record


def _safe_normalized_order(order: Mapping[str, Any] | None) -> dict[str, Any] | None:
    if order is None:
        return None
    keys = (
        "account_id",
        "client_order_id",
        "order_id",
        "symbol",
        "side",
        "status",
        "filled",
        "size",
        "execution_unknown",
        "terminal_confirmed",
        "definite_reject",
    )
    result = {}
    for key in keys:
        value = order.get(key)
        if value is None or type(value) in (str, int, bool):
            result[key] = value
        elif isinstance(value, (float, Decimal)):
            result[key] = str(value)
    return result


def _safe_acknowledgement(acknowledgement: Mapping[str, Any] | None) -> dict[str, Any] | None:
    if acknowledgement is None:
        return None
    provenance = getattr(acknowledgement, "normalization_provenance", None)
    projected_provenance = (
        provenance.to_dict() if type(provenance) is NormalizationProvenance else None
    )
    return {
        "normalization_provenance": projected_provenance,
        "record": _safe_normalized_order(acknowledgement),
    }


def _is_complete_open_zero_fill(
    order: object,
    fills: object,
    order_observation: Mapping[str, Any] | None,
    fill_observation: Mapping[str, Any] | None,
) -> bool:
    if type(order) is not NormalizedRecord or type(fills) is not NormalizedRecords:
        return False
    if str(order.get("status")) not in _OPEN_ORDER_STATES:
        return False
    try:
        if Decimal(str(order.get("filled"))) != 0:
            return False
    except (InvalidOperation, ValueError):
        return False
    if (
        order.get("execution_unknown") is not False
        or len(fills) != 0
        or fills.source_alignment_valid is not True
        or len(fills.row_provenance) != 0
    ):
        return False
    if order_observation is not None and order_observation.get("complete") is not True:
        return False
    if fill_observation is not None and fill_observation.get("complete") is not True:
        return False
    order_prov = getattr(order, "normalization_provenance", None)
    fill_prov = getattr(fills, "normalization_provenance", None)
    return (
        type(order_prov) is NormalizationProvenance
        and order_prov.operation == "query_order"
        and type(fill_prov) is NormalizationProvenance
        and fill_prov.operation == "get_deals"
    )


__all__ = [
    "CANCEL_OPERATION",
    "QUERY_OPERATION",
    "RiskBtApiProviderContract",
    "RiskOrderEntryError",
    "RiskOrderEntryService",
    "RiskOrderTarget",
    "SUBMIT_OPERATION",
    "SUPPORTED_OPERATIONS",
    "validate_service_options",
]
