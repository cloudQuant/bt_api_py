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
    DispatchExposureResolutionProof,
    DispatchExposureResolutionReceiptStatus,
    DispatchResolutionReceipt,
    DispatchResolutionReceiptStatus,
    DispatchTrackedOrderProof,
    DurableRiskAdmissionAdapter,
    DurableRiskGate,
    InstrumentRiskFxRate,
    InstrumentRiskRegistry,
    OrderExposureObservationProof,
    OrderExposureState,
    OrderFillFact,
    PermitInvalidError,
    RiskAdmissionDeniedError,
    fill_ids_sha256,
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
    RiskOrderJournalEvent,
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
_FILL_SOURCE_RECORD_SCHEMA = "bt-api-provider-source-row-v1"
_SUPPORTED_PROVIDER_TIME_IN_FORCE = frozenset(
    {("GTC",), ("IOC",), ("GTC", "IOC")}
)
_TRADE_ID_SCOPE_MODES = frozenset({"ACCOUNT", "INSTRUMENT"})
# Definitive provider identity conflicts: the exact dispatch latch must stay
# active as durable evidence, but the scope must not treat the accounted
# conflict as a pending unknown that blocks later risk-increasing intents.
_IDENTITY_CONFLICT_DENIAL_CODES = frozenset(
    {
        "ORDER_EXPOSURE_TRADE_IDENTITY_CONFLICT",
        "ORDER_EXPOSURE_TRADE_IDENTITY_MODE_MISMATCH",
        "ORDER_EXPOSURE_TRADE_IDENTITY_DOMAIN_REUSED",
        "ORDER_EXPOSURE_TRADE_IDENTITY_UNMAPPED",
        "ORDER_EXPOSURE_FILL_IDENTITY_CONFLICT",
        "ORDER_EXPOSURE_PROVIDER_ID_REUSED",
    }
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
    "price": ("price", "limit_price", "order_price", "Price"),
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
    fx_rates_by_instrument: tuple[InstrumentRiskFxRate, ...] = ()

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
        if type(self.fx_rates_by_instrument) is not tuple:
            raise RiskOrderEntryError("TARGET_FX_RATES_INVALID")
        seen_fx_instruments: set[str] = set()
        for fact in self.fx_rates_by_instrument:
            if (
                type(fact) is not InstrumentRiskFxRate
                or fact.instrument in seen_fx_instruments
            ):
                raise RiskOrderEntryError("TARGET_FX_RATES_INVALID")
            seen_fx_instruments.add(fact.instrument)
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
    # Optional DIM01 trade-identity declaration supplied only by the
    # server-owned provider profile. Both fields are declared together; None
    # keeps the legacy semantics and the legacy fingerprint byte for byte.
    trade_id_domain_ref: str | None = None
    trade_id_scope: str | None = None

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
        if (self.trade_id_domain_ref is None) != (self.trade_id_scope is None):
            raise RiskOrderEntryError("PROVIDER_TRADE_IDENTITY_CONTRACT_INVALID")
        if self.trade_id_domain_ref is not None:
            if (
                type(self.trade_id_domain_ref) is not str
                or _REFERENCE.fullmatch(self.trade_id_domain_ref) is None
                or self.trade_id_scope not in _TRADE_ID_SCOPE_MODES
                or not self.exposure_ready
            ):
                # A declared identity domain must be enforceable: it needs the
                # complete normalized observation contract, and ACCOUNT vs
                # INSTRUMENT is a fixed fail-closed choice.
                raise RiskOrderEntryError("PROVIDER_TRADE_IDENTITY_CONTRACT_INVALID")

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
        if self.trade_id_domain_ref is not None:
            # Fingerprint unchanged for undeclared profiles; a declared
            # identity domain is part of the trusted contract version.
            payload["trade_id_domain_ref"] = self.trade_id_domain_ref
            payload["trade_id_scope"] = self.trade_id_scope
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
    fx_rate: InstrumentRiskFxRate | None = None

    @property
    def fingerprint(self) -> str:
        payload = {
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
        if self.fx_rate is not None:
            payload["fx_rate"] = self.fx_rate.to_payload()
            payload["fx_rate_sha256"] = self.fx_rate.digest
        return sha256_text(canonical_json(payload))


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
            _validate_target_fx_contract(target, contract)
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
                        "EXPOSURE_APPLIED",
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
        if command.state in {"PROVIDER_QUERY", "EXPOSURE_APPLIED", "UNKNOWN"} and (
            self._has_resolved_ack_history(fence, command)
        ):
            # A durable ACK plus Risk's exact resolved receipt proves this is
            # the previously accepted command. A duplicate submit never
            # dispatches or re-runs immutable proof recovery.
            return command
        if command.state == "EXPOSURE_APPLIED":
            return self._mark_unknown(
                fence,
                command,
                "DISPATCH_PROOF_RECOVERY_UNCERTAIN",
                permit_id=command.permit_id,
                allow_ack_to_unknown=True,
            )
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
            if self._journal.has_resolved_ack_history(
                fence,
                principal_ref=command.principal_ref,
                intent_id=command.intent_id,
                receipt=receipt,
            ):
                return True
        except Exception:
            pass
        try:
            receipt = self._gate.dispatch_exposure_resolution_receipt(command.permit_id)
            return (
                receipt.status is DispatchExposureResolutionReceiptStatus.TERMINAL_EXPOSURE_RESOLVED
                and self._journal.has_resolved_terminal_exposure_history(
                    fence,
                    principal_ref=command.principal_ref,
                    intent_id=command.intent_id,
                    receipt=receipt,
                )
            )
        except Exception:
            return False

    def _resolved_claim_for_query(
        self, fence: RiskOrderWriterFence, command: RiskOrderCommand
    ) -> DispatchClaimBinding:
        """Return the claim only from the exact resolved receipt and ACK history.

        ``dispatch_claim_binding`` is deliberately active-latch-only. Once an
        ACK has resolved the dispatch freeze, later provider observations must
        use the claim carried by Risk's durable receipt and validate that
        receipt against the immutable journal history under the current writer
        fence.
        """

        permit_id = command.permit_id
        if permit_id is None:
            raise RiskOrderEntryError("ACKED_PERMIT_REFERENCE_MISSING")
        receipt: DispatchResolutionReceipt | None = None
        try:
            receipt = self._gate.dispatch_resolution_receipt(permit_id)
        except PermitInvalidError:
            # Positive-fill terminal resolutions use the distinct exposure
            # receipt below; infrastructure errors remain fail-closed.
            pass
        if receipt is not None and (
            receipt.status is DispatchResolutionReceiptStatus.ACKED_TRACKED_RESOLVED
            and receipt.dispatch_latch_active is False
            and receipt.claim.scope == command.scope
            and receipt.claim.permit_id == permit_id
            and receipt.claim.intent_id == command.risk_intent_id
            and self._journal.has_resolved_ack_history(
                fence,
                principal_ref=command.principal_ref,
                intent_id=command.intent_id,
                receipt=receipt,
            )
        ):
            return receipt.claim

        try:
            receipt = self._gate.dispatch_exposure_resolution_receipt(permit_id)
        except PermitInvalidError as error:
            raise RiskOrderEntryError("ACKED_RISK_RECEIPT_INVALID") from error
        if (
            receipt.status is DispatchExposureResolutionReceiptStatus.TERMINAL_EXPOSURE_RESOLVED
            and receipt.dispatch_latch_active is False
            and receipt.claim.scope == command.scope
            and receipt.claim.permit_id == permit_id
            and receipt.claim.intent_id == command.risk_intent_id
            and self._journal.has_resolved_terminal_exposure_history(
                fence,
                principal_ref=command.principal_ref,
                intent_id=command.intent_id,
                receipt=receipt,
            )
        ):
            return receipt.claim
        raise RiskOrderEntryError("ACKED_RISK_RECEIPT_INVALID")

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

        # The deadline is a durable intent field: only canonical_payload
        # persists it, and order_request must stay the exact wire payload
        # that OrderRequest.from_dict rebuilds at dispatch time.
        if not isinstance(command.canonical_payload, Mapping):
            return self._mark_unknown(
                fence, command, "INTENT_DEADLINE_SOURCE_UNCERTAIN", permit_id=permit_id
            )
        deadline_ns = command.canonical_payload.get("deadline_ns")
        if deadline_ns is not None:
            if type(deadline_ns) is not int or deadline_ns <= 0:
                return self._mark_unknown(
                    fence, command, "INTENT_DEADLINE_SOURCE_UNCERTAIN", permit_id=permit_id
                )
            try:
                now_ns = time.time_ns()
            except Exception:
                return self._mark_unknown(
                    fence, command, "INTENT_DEADLINE_CLOCK_UNCERTAIN", permit_id=permit_id
                )
            if type(now_ns) is not int or now_ns <= 0:
                return self._mark_unknown(
                    fence, command, "INTENT_DEADLINE_CLOCK_UNCERTAIN", permit_id=permit_id
                )
            if now_ns >= deadline_ns:
                return self._release_unclaimed_and_record_denial(
                    fence,
                    command,
                    admission=admission,
                    permit_id=permit_id,
                    reason="DEADLINE_EXPIRED",
                )

        if risk_order is None:
            risk_order = self._admission_order(command, target, None)
        try:
            admission.validate(permit_id, risk_order)
            admission.claim_for_dispatch(permit_id, risk_order)
            claim = self._gate.dispatch_claim_binding(permit_id)
        except RiskAdmissionDeniedError as error:
            return self._release_unclaimed_and_record_denial(
                fence,
                command,
                admission=admission,
                permit_id=permit_id,
                reason=error.code,
            )
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

    def _release_unclaimed_and_record_denial(
        self,
        fence: RiskOrderWriterFence,
        command: RiskOrderCommand,
        *,
        admission: DurableRiskAdmissionAdapter,
        permit_id: str,
        reason: str,
    ) -> RiskOrderCommand:
        try:
            admission.release(permit_id, "risk_order_not_dispatched")
        except PermitInvalidError as release_error:
            # Another Risk transaction may expire the pending permit after a
            # denial but before this release call.  DurableRiskGate.release
            # checks for a dispatch claim before reservation status, so this
            # exact result proves the permit is inactive and unclaimed.
            if release_error.code != "PERMIT_NOT_ACTIVE":
                return self._mark_unknown(
                    fence, command, "UNCLAIMED_RELEASE_UNCERTAIN", permit_id=permit_id
                )
        except Exception:
            return self._mark_unknown(
                fence, command, "UNCLAIMED_RELEASE_UNCERTAIN", permit_id=permit_id
            )
        return self._record_denial(fence, command, reason, permit_id=permit_id)

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
                claim=claim,
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
            if (
                allow_resolution
                and contract.exposure_ready
                and order_observation.get("exposure_state")
                in {OrderExposureState.FILLED.value, OrderExposureState.CANCELLED.value}
                and Decimal(str(fill_observation.get("filled_quantity", "0"))) > 0
            ):
                return self._resolve_terminal_exposure(
                    fence,
                    command,
                    target=target,
                    admission=admission,
                    contract=contract,
                    claim=claim,
                    request=request,
                    order=order,
                    fills=fills,
                    order_event=order_event,
                    fill_event=fill_event,
                    reconciliation_sha=reconciliation_sha,
                )
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

    def _resolve_terminal_exposure(
        self,
        fence: RiskOrderWriterFence,
        command: RiskOrderCommand,
        *,
        target: RiskOrderTarget,
        admission: DurableRiskAdmissionAdapter,
        contract: RiskBtApiProviderContract,
        claim: DispatchClaimBinding,
        request: OrderRequest,
        order: object,
        fills: object,
        order_event: RiskOrderJournalEvent,
        fill_event: RiskOrderJournalEvent,
        reconciliation_sha: str,
    ) -> RiskOrderCommand:
        """Resolve a first, complete positive-fill terminal provider observation."""

        try:
            admission.ensure_settled(claim.permit_id)
            exposure_id, exposure_sha = self._journal.store_terminal_exposure_reference(
                fence,
                principal_ref=command.principal_ref,
                intent_id=command.intent_id,
                claim=claim,
                provider_order_id=str(order["order_id"]),
                accepted_request_sha256=command.order_request_sha256,
                order_event=order_event,
                fill_event=fill_event,
                reconciliation_evidence_sha256=reconciliation_sha,
            )
            facts = _v2_order_facts(
                order,
                fills,
                command=command,
                request=request,
                contract=contract,
            )
            observation = _order_exposure_proof(
                command,
                target=target,
                contract=contract,
                claim=claim,
                facts=facts,
                order_payload=order_event.payload,
                fill_payload=fill_event.payload,
                journal_revision=fill_event.revision,
                journal_record_sha256=fill_event.record_sha256,
                exposure_reservation_id=exposure_id,
                exposure_reservation_sha256=exposure_sha,
                writer_owner_id=fence.owner_id,
                writer_fencing_token=fence.fencing_token,
                writer_fence_sha256=fence.fence_sha256,
                dispatch_proof_sha256=None,
            )
            proof = DispatchExposureResolutionProof(
                claim=claim,
                evidence_class=_evidence_class(claim.scope),
                dispatch_attempt_count=1,
                reconciliation_evidence_sha256=reconciliation_sha,
                observation=observation,
            )
            receipt = admission.resolve_dispatch_exposure(proof)
            self._journal.append_terminal_exposure_ack(
                fence,
                principal_ref=command.principal_ref,
                intent_id=command.intent_id,
                receipt=receipt,
                proof=proof,
            )
            return self._journal.get_command(
                target.account_scope, command.principal_ref, command.intent_id
            ) or command
        except RiskAdmissionDeniedError as error:
            if error.code in _IDENTITY_CONFLICT_DENIAL_CODES:
                latch = getattr(self._gate, "latch_dispatch_identity_conflict", None)
                if callable(latch):
                    with suppress(Exception):
                        latch(claim.permit_id)
            return self._mark_unknown(
                fence,
                command,
                "RISK_EXPOSURE_REJECTED_" + error.code,
                permit_id=claim.permit_id,
            )
        except Exception:
            return self._mark_unknown(
                fence,
                command,
                "DISPATCH_EXPOSURE_RESOLUTION_UNCERTAIN",
                permit_id=claim.permit_id,
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
                "EXPOSURE_APPLIED",
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
            if (
                has_resolved_ack
                and command.state == "ACKED_TRACKED"
                and command.latest_event.get("public_state")
                in {OrderExposureState.FILLED.value, OrderExposureState.CANCELLED.value}
            ):
                # A complete first terminal observation is already durable in
                # Risk and the journal; it needs no follow-up provider read.
                return _command_reply(command, target.account_scope)
            if contract is None or not contract.write_ready:
                return _command_reply(command, target.account_scope)
            if has_resolved_ack:
                # The provider remains authoritative for later order/fill facts.
                # Persist the complete normalized observation first; Risk then
                # consumes that exact journal revision while this account fence
                # remains held.
                try:
                    if command.permit_id is None:
                        raise RiskOrderEntryError("ACKED_PERMIT_REFERENCE_MISSING")
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
                    claim = self._resolved_claim_for_query(fence, command)
                    observed = _followup_observation(
                        command,
                        target=target,
                        contract=contract,
                        claim=claim,
                        order=order,
                        fills=fills,
                    )
                    v2_facts = (
                        _v2_order_facts(
                            order,
                            fills,
                            command=command,
                            request=request,
                            contract=contract,
                        )
                        if contract.exposure_ready
                        else None
                    )
                    if v2_facts is None:
                        observed["public_state"] = (
                            "ACKED_TRACKED"
                            if _is_complete_open_zero_fill(order, fills, None, None)
                            else "UNKNOWN"
                        )
                    else:
                        observed["public_state"] = "UNKNOWN"
                    observed["dispatch_attempt_count"] = 1
                    query_event = self._journal.append_event(
                        fence,
                        principal_ref=principal_ref,
                        intent_id=intent_id,
                        event_type="PROVIDER_QUERY",
                        payload=observed,
                    )
                    if v2_facts is not None:
                        receipt = self._gate.dispatch_resolution_receipt(command.permit_id)
                        if (
                            receipt.status is not DispatchResolutionReceiptStatus.ACKED_TRACKED_RESOLVED
                            or receipt.dispatch_latch_active
                            or receipt.proof is None
                            or receipt.claim != claim
                        ):
                            raise RiskOrderEntryError("ACKED_RISK_RECEIPT_INVALID")
                        tracked_order = receipt.proof
                        reference_id = tracked_order.exposure_reservation_id
                        reference_sha = tracked_order.exposure_reservation_sha256
                        proof = _order_exposure_proof(
                            command,
                            target=target,
                            contract=contract,
                            claim=claim,
                            facts=v2_facts,
                            order_payload=query_event.payload,
                            fill_payload=query_event.payload,
                            journal_revision=query_event.revision,
                            journal_record_sha256=query_event.record_sha256,
                            exposure_reservation_id=reference_id,
                            exposure_reservation_sha256=reference_sha,
                            writer_owner_id=fence.owner_id,
                            writer_fencing_token=fence.fencing_token,
                            writer_fence_sha256=fence.fence_sha256,
                            dispatch_proof_sha256=tracked_order.fingerprint,
                        )
                        snapshot = self._gate.apply_order_exposure_observation(proof)
                        applied_payload = {
                            "dispatch_attempt_count": 1,
                            "filled_gross_notional": _decimal_text(snapshot.filled_gross_notional),
                            "filled_quantity": _decimal_text(
                                snapshot.cumulative_filled_quantity
                            ),
                            "journal_record_sha256": query_event.record_sha256,
                            "journal_revision": query_event.revision,
                            "leaves_quantity": _decimal_text(snapshot.leaves_quantity),
                            "notional_unit": snapshot.notional_unit,
                            "open_gross_notional": _decimal_text(snapshot.open_gross_notional),
                            "order_revision": snapshot.order_revision,
                            "permit_id": claim.permit_id,
                            "proof_sha256": snapshot.proof_sha256,
                            "provider_order_id": tracked_order.provider_order_id,
                            "public_state": snapshot.state.value,
                            "risk_intent_id": claim.intent_id,
                            "trade_count": v2_facts["fill_trade_count"]
                            if "fill_trade_count" in v2_facts
                            else len(v2_facts["fill_facts"]),
                        }
                        if snapshot.state is OrderExposureState.UNKNOWN:
                            applied_payload["reason_code"] = "PROVIDER_ORDER_STATE_UNKNOWN"
                        self._journal.append_event(
                            fence,
                            principal_ref=principal_ref,
                            intent_id=intent_id,
                            event_type="EXPOSURE_APPLIED",
                            payload=applied_payload,
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
        contract = self._provider_contract_for_target(target)
        fx_rate = _target_fx_rate(target, contract, request.symbol)
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
            fx_rate=fx_rate,
        )

    def _provider_contract_for_target(
        self, target: RiskOrderTarget
    ) -> RiskBtApiProviderContract:
        for scope, configured in self._targets.items():
            if configured is target:
                contract = self._provider_contracts.get(scope)
                if contract is not None:
                    return contract
        raise RiskOrderEntryError("PROVIDER_CONTRACT_SCOPE_MISMATCH")

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


def _valuation_facts_decimal_text(value: Decimal) -> str:
    """Canonicalize only the decimal fields covered by the valuation digest.

    The journal recomputes this digest with its canonical Decimal formatter.
    Keep ordinary provider/request serialization scale-preserving; this helper
    is only for the shared valuation-facts hash contract.
    """

    if value == 0:
        return "0"
    result = format(value, "f")
    if "." in result:
        result = result.rstrip("0").rstrip(".")
    return result


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
    if command.state in {"PROVIDER_QUERY", "EXPOSURE_APPLIED"}:
        state = command.latest_event.get("public_state", "UNKNOWN")
    elif command.state == "ACKED_TRACKED" and command.latest_event.get("public_state") in {
        OrderExposureState.FILLED.value,
        OrderExposureState.CANCELLED.value,
    }:
        state = command.latest_event["public_state"]
    latest = command.latest_event
    record = {
        "intent_id": command.intent_id,
        "scope_key": scope.key,
        "payload_sha256": command.payload_sha256,
        "state": state,
        "provider_order_id": latest.get("provider_order_id"),
        "filled_quantity": _decimal_text(Decimal(str(latest.get("filled_quantity", 0) or 0))),
        # A durable terminal ACK publishes the executed weighted average price
        # derived from the immutable fill facts; every non-fill event leaves it
        # absent, so the projection stays None until fills are proven.
        "average_price": latest.get("average_price"),
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
        "is_terminal": state in {"REJECTED", OrderExposureState.FILLED.value, OrderExposureState.CANCELLED.value},
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


def _validate_provenance_snapshot(value: object, operation: str) -> dict[str, Any]:
    """Validate the normalizer's finite, source-bound v2 projection."""

    if type(value) is not NormalizationProvenance or value.operation != operation:
        raise RiskOrderEntryError("PROVIDER_SOURCE_PROVENANCE_MISSING")
    projected = value.to_dict()
    if (
        projected.get("schema") != _ORDER_PROVENANCE_SCHEMA_V2
        or type(projected.get("source_fields")) is not list
        or projected["source_fields"] != sorted(set(projected["source_fields"]))
        or type(projected.get("envelope_fields")) is not list
        or projected["envelope_fields"] != sorted(set(projected["envelope_fields"]))
        or type(projected.get("source_values")) is not dict
        or type(projected.get("envelope_values")) is not dict
        or set(projected["source_values"]) - set(projected["source_fields"])
        or set(projected["envelope_values"]) - set(projected["envelope_fields"])
    ):
        raise RiskOrderEntryError("PROVIDER_SOURCE_PROVENANCE_INVALID")
    for values in (projected["source_values"], projected["envelope_values"]):
        if any(type(key) is not str or type(item) not in (str, int, bool) for key, item in values.items()):
            raise RiskOrderEntryError("PROVIDER_SOURCE_PROVENANCE_INVALID")
    digest = projected.get("source_sha256")
    expected = sha256_text(
        canonical_json(
            {
                "envelope_values": projected["envelope_values"],
                "operation": operation,
                "schema": projected["schema"],
                "source_fields": projected["source_fields"],
                "source_values": projected["source_values"],
            }
        )
    )
    if digest != expected:
        raise RiskOrderEntryError("PROVIDER_SOURCE_PROVENANCE_INVALID")
    return projected


def _v2_order_facts(
    order: object,
    fills: object,
    *,
    command: RiskOrderCommand,
    request: OrderRequest,
    contract: RiskBtApiProviderContract,
) -> dict[str, Any]:
    """Validate explicit provider revisions and immutable complete fill facts."""

    if not contract.exposure_ready:
        raise RiskOrderEntryError("PROVIDER_EXPOSURE_CONTRACT_UNAVAILABLE")
    order_prov = _validate_provenance_snapshot(
        getattr(order, "normalization_provenance", None), "query_order"
    )
    fill_prov = _validate_provenance_snapshot(
        getattr(fills, "normalization_provenance", None), "get_deals"
    )
    order_values = order_prov["source_values"]
    fill_envelope = fill_prov["envelope_values"]
    order_revision = order_values.get(contract.order_revision_field)
    fill_watermark = fill_envelope.get(contract.fill_watermark_field)
    if (
        type(order_revision) is not int
        or order_revision <= 0
        or type(fill_watermark) is not int
        or fill_watermark <= 0
        or fill_watermark < order_revision
    ):
        raise RiskOrderEntryError("PROVIDER_ORDER_REVISION_UNVERIFIED")
    status_raw = _field_value(order_prov, "status")
    status_map = dict(contract.provider_status_mapping)
    status = (
        None
        if status_raw is None
        else status_map.get(str(status_raw).lower().replace("-", "_"))
    )
    states = {
        "accepted": OrderExposureState.ACKED,
        "partial": OrderExposureState.PARTIALLY_FILLED,
        "completed": OrderExposureState.FILLED,
        "canceled": OrderExposureState.CANCELLED,
        "unknown": OrderExposureState.UNKNOWN,
    }
    exposure_state = states.get(status or "", OrderExposureState.UNKNOWN)
    if (
        type(order) is not NormalizedRecord
        or type(fills) is not NormalizedRecords
        or order.get("execution_unknown") is not False
        or str(order.get("status")) != status
        or fills.source_alignment_valid is not True
        or len(fills.row_provenance) != len(fills)
        or not contract.complete_order_snapshot
        or not contract.complete_fill_history
    ):
        raise RiskOrderEntryError("PROVIDER_EXPOSURE_OBSERVATION_INCOMPLETE")
    raw_filled = _field_value(order_prov, "filled_quantity")
    raw_quantity = _field_value(order_prov, "quantity")
    try:
        filled = Decimal(str(raw_filled))
        quantity = Decimal(str(raw_quantity))
        normalized_filled = Decimal(str(order.get("filled")))
        source_price = Decimal(str(_field_value(order_prov, "price")))
    except (InvalidOperation, TypeError, ValueError):
        raise RiskOrderEntryError("PROVIDER_FILL_QUANTITY_UNVERIFIED") from None
    if (
        not filled.is_finite()
        or filled < 0
        or quantity != request.quantity
        or request.price is None
        or source_price != request.price
        or filled != normalized_filled
        or order.get("order_id") != _field_value(order_prov, "provider_order_id")
        or order.get("client_order_id") != command.client_order_id
    ):
        raise RiskOrderEntryError("PROVIDER_ORDER_FIELD_MISMATCH")

    row_provenance = tuple(fills.row_provenance)
    if len(fills) != len(row_provenance):
        raise RiskOrderEntryError("PROVIDER_FILL_HISTORY_ALIGNMENT_INVALID")
    fill_facts: list[OrderFillFact] = []
    fill_rows: list[dict[str, Any]] = []
    for index, (normalized, raw_prov_value) in enumerate(
        zip(fills, row_provenance, strict=True)
    ):
        raw_prov = _validate_provenance_snapshot(raw_prov_value, "get_deals")
        values = raw_prov["source_values"]
        trade_id = _first_source_value(values, ("trade_id", "fill_id", "deal_id", "tradeId", "id", "TradeID"))
        provider_order_id = _first_source_value(
            values, ("order_id", "external_order_id", "orderId", "ordId", "OrderSysID")
        )
        client_order_id = _first_source_value(
            values, ("client_order_id", "clOrdId", "OrderRef")
        )
        account_id = _first_source_value(values, ("account_id", "AccountID", "InvestorID"))
        instrument = _first_source_value(
            values,
            ("symbol", "symbol_name", "instrument", "instrument_id", "data_name", "instId", "InstrumentID", "trade_symbol"),
        )
        side_raw = _first_source_value(values, ("side", "order_side", "trade_side", "S"))
        quantity_raw = _first_source_value(
            values, ("quantity", "size", "trade_volume", "qty", "fillSz", "volume", "Volume")
        )
        price_raw = _first_source_value(
            values, ("trade_price", "price", "fillPx", "Price")
        )
        revision = values.get(contract.fill_revision_field)
        fee_raw = values.get(contract.fee_amount_field)
        fee_currency = values.get(contract.fee_currency_field)
        if (
            type(trade_id) is not str
            or not trade_id
            or type(revision) is not int
            or revision <= 0
            or revision > fill_watermark
            or str(provider_order_id) != str(order.get("order_id"))
            or str(client_order_id) != command.client_order_id
            or str(account_id) != command.provider_account_id
            or str(instrument) != request.symbol
            or str(side_raw).upper() != request.side.value.upper()
            or type(fee_currency) is not str
            or not fee_currency
        ):
            raise RiskOrderEntryError("PROVIDER_FILL_SOURCE_UNVERIFIED")
        try:
            fill_quantity = Decimal(str(quantity_raw))
            fill_price = Decimal(str(price_raw))
            fee_amount = Decimal(str(fee_raw))
            normalized_fee = Decimal(str(normalized.get("fee")))
        except (InvalidOperation, TypeError, ValueError):
            raise RiskOrderEntryError("PROVIDER_FILL_SOURCE_INVALID") from None
        if (
            not fill_quantity.is_finite()
            or fill_quantity <= 0
            or not fill_price.is_finite()
            or fill_price <= 0
            or not fee_amount.is_finite()
            or not normalized_fee.is_finite()
            or normalized_fee != fee_amount
            or str(normalized.get("trade_id")) != trade_id
            or str(normalized.get("order_id")) != str(provider_order_id)
            or str(normalized.get("client_order_id")) != command.client_order_id
            or str(normalized.get("account_id")) != command.provider_account_id
            or str(normalized.get("symbol")) != request.symbol
            or str(normalized.get("side")).upper() != request.side.value.upper()
            or str(normalized.get("fee_currency")).upper() != fee_currency.upper()
        ):
            raise RiskOrderEntryError("PROVIDER_FILL_SOURCE_MISMATCH")
        # The row identity must survive a later observation with a newer
        # envelope watermark.  The full provider-observation digest below
        # continues to bind each envelope; this digest binds the immutable
        # explicit source row only, so previously observed fills remain the
        # same facts when another fill is appended.
        digest = _fill_source_record_sha256(raw_prov)
        fill_facts.append(
            OrderFillFact(
                trade_id=trade_id,
                quantity=fill_quantity,
                price=fill_price,
                side=request.side.value.upper(),
                position_effect="OPEN",
                fee_amount=fee_amount,
                fee_currency=fee_currency.upper(),
                source_record_sha256=digest,
                source_revision=revision,
            )
        )
        fill_rows.append(
            {
                "normalization_provenance": raw_prov,
                "record": {
                    **_safe_normalized_fill(normalized),
                    "source_record_sha256": digest,
                    "source_revision": revision,
                },
                "source_revision": revision,
            }
        )
    fill_tuple = tuple(sorted(fill_facts, key=lambda item: item.trade_id))
    fill_qty = sum((item.quantity for item in fill_tuple), Decimal(0))
    if fill_qty != filled or len({item.trade_id for item in fill_tuple}) != len(fill_tuple):
        raise RiskOrderEntryError("PROVIDER_FILL_HISTORY_MISMATCH")
    if exposure_state is OrderExposureState.ACKED and filled != 0:
        raise RiskOrderEntryError("PROVIDER_ORDER_STATE_MISMATCH")
    if exposure_state is OrderExposureState.PARTIALLY_FILLED and not (
        0 < filled < quantity
    ):
        raise RiskOrderEntryError("PROVIDER_ORDER_STATE_MISMATCH")
    if exposure_state is OrderExposureState.FILLED and filled != quantity:
        raise RiskOrderEntryError("PROVIDER_ORDER_STATE_MISMATCH")
    if exposure_state is OrderExposureState.CANCELLED and filled > quantity:
        raise RiskOrderEntryError("PROVIDER_ORDER_STATE_MISMATCH")
    leaves = (
        quantity - filled
        if exposure_state in {OrderExposureState.ACKED, OrderExposureState.PARTIALLY_FILLED}
        else Decimal(0)
    )
    return {
        "fill_facts": fill_tuple,
        "fill_rows": fill_rows,
        "fill_watermark_revision": fill_watermark,
        "filled_quantity": filled,
        "leaves_quantity": leaves,
        "order_revision": order_revision,
        "valuation_price": source_price,
        "state": exposure_state,
    }


def _first_source_value(values: Mapping[str, Any], names: tuple[str, ...]) -> Any:
    for name in names:
        if name in values:
            return values[name]
    return None


def _fill_source_record_sha256(provenance: Mapping[str, Any]) -> str:
    """Hash stable explicit fill-row provenance, excluding envelope watermarks."""

    if (
        provenance.get("operation") != "get_deals"
        or provenance.get("schema") != _ORDER_PROVENANCE_SCHEMA_V2
        or type(provenance.get("source_fields")) is not list
        or type(provenance.get("source_values")) is not dict
    ):
        raise RiskOrderEntryError("PROVIDER_FILL_SOURCE_UNVERIFIED")
    return sha256_text(
        canonical_json(
            {
                "schema": _FILL_SOURCE_RECORD_SCHEMA,
                "operation": "get_deals",
                "source_fields": provenance["source_fields"],
                "source_values": provenance["source_values"],
            }
        )
    )


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
    normalized_status = status_map.get(status_key)
    if normalized_status is None:
        raise RiskOrderEntryError("PROVIDER_ORDER_STATE_UNVERIFIED")
    if str(order.get("status")) != normalized_status:
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
    claim: DispatchClaimBinding,
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
    v2_facts = (
        _v2_order_facts(
            order,
            fills,
            command=command,
            request=request,
            contract=contract,
        )
        if contract.exposure_ready
        else None
    )
    if v2_facts is None and Decimal(str(_field_value(order_provenance, "filled_quantity"))) != 0:
        raise RiskOrderEntryError("PROVIDER_ORDER_NOT_ZERO_FILL")
    metadata_facts = (
        _provider_metadata_facts(target, contract, request.symbol)
        if v2_facts is not None
        else None
    )
    source_alias_map_sha256 = _source_alias_map_sha256() if v2_facts is not None else None

    order_values = {
        "account_id": command.provider_account_id,
        "client_order_id": command.client_order_id,
        "collection_provenance": order.collection_provenance.to_dict(),
        "complete": True,
        "filled_quantity": (
            _decimal_text(v2_facts["filled_quantity"]) if v2_facts is not None else 0
        ),
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
    if v2_facts is not None:
        order_observation["order_revision"] = v2_facts["order_revision"]
        order_observation["exposure_state"] = v2_facts["state"].value
        order_observation["metadata_facts"] = metadata_facts
        order_observation["source_alias_map_sha256"] = source_alias_map_sha256
        order_observation["claim_digest"] = claim.claim_digest
        order_observation["risk_intent_id"] = claim.intent_id
    if len(fills) != 0 and v2_facts is None:
        raise RiskOrderEntryError("PROVIDER_FILL_HISTORY_NONEMPTY")
    fill_observation = {
        "account_id": command.provider_account_id,
        "client_order_id": command.client_order_id,
        "complete": True,
        "normalization_provenance": fill_provenance,
        "operation": "get_deals",
        "record_count": len(v2_facts["fill_rows"]) if v2_facts is not None else 0,
        "records": (
            [item["record"] for item in v2_facts["fill_rows"]]
            if v2_facts is not None
            else []
        ),
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
        "filled_quantity": (
            _decimal_text(v2_facts["filled_quantity"]) if v2_facts is not None else 0
        ),
        "trade_count": len(fills) if v2_facts is not None else 0,
    }
    if v2_facts is not None:
        fill_observation["fill_row_provenance"] = [
            item["normalization_provenance"] for item in v2_facts["fill_rows"]
        ]
        fill_observation["fill_watermark_revision"] = v2_facts[
            "fill_watermark_revision"
        ]
        fill_observation["metadata_facts"] = metadata_facts
        fill_observation["source_alias_map_sha256"] = source_alias_map_sha256
        fill_observation["claim_digest"] = claim.claim_digest
        fill_observation["risk_intent_id"] = claim.intent_id
    order_observation["provider_observation_sha256"] = sha256_text(
        canonical_json(order_observation)
    )
    fill_observation["provider_observation_sha256"] = sha256_text(canonical_json(fill_observation))
    return order_observation, fill_observation


def _followup_observation(
    command: RiskOrderCommand,
    *,
    target: RiskOrderTarget,
    contract: RiskBtApiProviderContract,
    claim: DispatchClaimBinding,
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
    v2_facts = (
        _v2_order_facts(
            order,
            fills,
            command=command,
            request=request,
            contract=contract,
        )
        if contract.exposure_ready
        else None
    )
    metadata_facts = (
        _provider_metadata_facts(target, contract, request.symbol)
        if v2_facts is not None
        else None
    )
    source_alias_map_sha256 = _source_alias_map_sha256() if v2_facts is not None else None
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
        "records": (
            [item["record"] for item in v2_facts["fill_rows"]]
            if v2_facts is not None
            else [_safe_normalized_order(item) for item in fills]
        ),
        "record_count": len(v2_facts["fill_rows"]) if v2_facts is not None else len(fills),
        "source_alignment_valid": order.source_alignment_valid,
        "source_row_count": order.source_row_count,
        "fill_source_alignment_valid": fills.source_alignment_valid,
        "fill_source_row_count": len(fills),
        "public_state": (
            "UNKNOWN" if v2_facts is not None else ("ACKED_TRACKED" if zero_open else "UNKNOWN")
        ),
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
        "quantity": _decimal_text(request.quantity),
        "instrument": str(order.get("symbol") or ""),
        "side": str(order.get("side") or ""),
        "trade_count": len(fills),
    }
    if v2_facts is not None:
        record["fill_row_provenance"] = [
            item["normalization_provenance"] for item in v2_facts["fill_rows"]
        ]
        record["fill_watermark_revision"] = v2_facts["fill_watermark_revision"]
        record["order_revision"] = v2_facts["order_revision"]
        record["exposure_state"] = v2_facts["state"].value
        record["filled_quantity"] = _decimal_text(v2_facts["filled_quantity"])
        record["metadata_facts"] = metadata_facts
        record["source_alias_map_sha256"] = source_alias_map_sha256
        record["dispatch_claim_digest"] = claim.claim_digest
        record["risk_intent_id"] = claim.intent_id
    record["provider_observation_sha256"] = sha256_text(canonical_json(record))
    return record


def _provider_metadata_facts(
    target: RiskOrderTarget,
    contract: RiskBtApiProviderContract,
    instrument: str,
) -> dict[str, Any]:
    metadata = target.registry.get(instrument)
    if metadata is None or metadata.valuation_unit is None:
        raise RiskOrderEntryError("INSTRUMENT_VALUATION_METADATA_UNAVAILABLE")
    fx_rate = _target_fx_rate(target, contract, instrument)
    contract_payload = contract.to_payload()
    contract_payload["fingerprint"] = contract.fingerprint
    contract_payload["quote_currency_by_instrument"] = dict(
        contract.quote_currency_by_instrument
    )
    metadata_payload = metadata.to_payload()
    metadata_payload["metadata_sha256"] = metadata.digest
    facts = {
        "provider_contract_sha256": contract.fingerprint,
        "provider_contract": contract_payload,
        "instrument_metadata": metadata_payload,
    }
    if fx_rate is not None:
        facts["instrument_fx_rate"] = fx_rate.to_payload()
        facts["instrument_fx_rate_sha256"] = fx_rate.digest
    return facts


def _target_fx_rate(
    target: RiskOrderTarget,
    contract: RiskBtApiProviderContract,
    instrument: str,
) -> InstrumentRiskFxRate | None:
    """Resolve the immutable FX fact from trusted target resources only."""

    metadata = target.registry.get(instrument)
    if metadata is None or metadata.valuation_unit is None:
        raise RiskOrderEntryError("INSTRUMENT_VALUATION_METADATA_UNAVAILABLE")
    quote_currency = contract.quote_currency(instrument)
    configured = next(
        (item for item in target.fx_rates_by_instrument if item.instrument == instrument),
        None,
    )
    if quote_currency is None:
        if configured is not None:
            raise RiskOrderEntryError("PROVIDER_FX_CONTRACT_UNAVAILABLE")
        return None
    trusted_quote = metadata.quote_currency or metadata.valuation_unit
    if trusted_quote != quote_currency:
        raise RiskOrderEntryError("PROVIDER_METADATA_CURRENCY_MISMATCH")
    if quote_currency == metadata.valuation_unit:
        if configured is not None or metadata.fx_source_ref is not None:
            raise RiskOrderEntryError("PROVIDER_FX_CONTRACT_INVALID")
        return None
    if (
        not contract.exposure_ready
        or type(configured) is not InstrumentRiskFxRate
        or metadata.quote_currency != quote_currency
        or metadata.fx_source_ref is None
        or configured.instrument != instrument
        or configured.quote_currency != quote_currency
        or configured.valuation_unit != metadata.valuation_unit
        or configured.source_ref != metadata.fx_source_ref
        or configured.fx_pair != quote_currency + "/" + metadata.valuation_unit
    ):
        raise RiskOrderEntryError("PROVIDER_FX_CONTRACT_UNAVAILABLE")
    return configured


def _validate_target_fx_contract(
    target: RiskOrderTarget,
    contract: RiskBtApiProviderContract,
) -> None:
    """Bind every target FX fact to the V2 contract and trusted metadata."""

    if target.fx_rates_by_instrument and not contract.exposure_ready:
        raise RiskOrderEntryError("PROVIDER_FX_CONTRACT_UNAVAILABLE")
    configured_instruments = {item.instrument for item in target.fx_rates_by_instrument}
    contract_instruments = {
        instrument for instrument, _quote in contract.quote_currency_by_instrument
    }
    if not configured_instruments.issubset(contract_instruments):
        raise RiskOrderEntryError("PROVIDER_FX_CONTRACT_UNAVAILABLE")
    for instrument, _quote in contract.quote_currency_by_instrument:
        metadata = target.registry.get(instrument)
        if metadata is not None:
            _target_fx_rate(target, contract, instrument)


def _source_alias_map_sha256() -> str:
    return sha256_text(canonical_json({key: list(value) for key, value in _SOURCE_ALIASES.items()}))


def _order_exposure_proof(
    command: RiskOrderCommand,
    *,
    target: RiskOrderTarget,
    contract: RiskBtApiProviderContract,
    claim: DispatchClaimBinding,
    facts: Mapping[str, Any],
    order_payload: Mapping[str, Any],
    fill_payload: Mapping[str, Any],
    journal_revision: int,
    journal_record_sha256: str,
    exposure_reservation_id: str,
    exposure_reservation_sha256: str,
    writer_owner_id: str,
    writer_fencing_token: int,
    writer_fence_sha256: str,
    dispatch_proof_sha256: str | None,
) -> OrderExposureObservationProof:
    """Build Risk's typed exposure fact from the exact normalized source rows."""

    request = OrderRequest.from_dict(dict(command.order_request))
    metadata = target.registry.get(request.symbol)
    if metadata is None or metadata.valuation_unit is None:
        raise RiskOrderEntryError("INSTRUMENT_VALUATION_METADATA_UNAVAILABLE")
    metadata_facts = _provider_metadata_facts(target, contract, request.symbol)
    if (
        dict(order_payload.get("metadata_facts", {})) != metadata_facts
        or dict(fill_payload.get("metadata_facts", {})) != metadata_facts
        or order_payload.get("source_alias_map_sha256") != _source_alias_map_sha256()
    ):
        raise RiskOrderEntryError("PROVIDER_EXPOSURE_METADATA_BINDING_INVALID")
    quote_currency = contract.quote_currency(request.symbol)
    valuation_unit = metadata.valuation_unit
    fx_rate = _target_fx_rate(target, contract, request.symbol)
    if quote_currency is None:
        raise RiskOrderEntryError("PROVIDER_EXPOSURE_QUOTE_CURRENCY_UNAVAILABLE")
    proof_observed_at_ns = time.time_ns()
    if (
        metadata.expires_at_ns <= proof_observed_at_ns
        or metadata.as_of_ns > proof_observed_at_ns
        or (
            fx_rate is not None
            and not fx_rate.observed_at_ns
            <= proof_observed_at_ns
            < fx_rate.valid_until_ns
        )
        or type(facts.get("order_revision")) is not int
        or type(facts.get("fill_watermark_revision")) is not int
    ):
        raise RiskOrderEntryError("PROVIDER_EXPOSURE_FACTS_STALE")
    multiplier = metadata.contract_multiplier
    filled_quantity = Decimal(facts["filled_quantity"])
    leaves_quantity = Decimal(facts["leaves_quantity"])
    valuation_price = Decimal(facts["valuation_price"])
    conversion_rate = Decimal(1) if fx_rate is None else fx_rate.rate
    filled_gross = sum(
        (item.quantity * item.price * multiplier * conversion_rate for item in facts["fill_facts"]),
        Decimal(0),
    )
    leaves_gross = leaves_quantity * valuation_price * multiplier * conversion_rate
    valuation_observed_at_ns = max(
        metadata.as_of_ns,
        metadata.as_of_ns if fx_rate is None else fx_rate.observed_at_ns,
    )
    valuation_valid_until_ns = min(
        metadata.expires_at_ns,
        metadata.expires_at_ns if fx_rate is None else fx_rate.valid_until_ns,
    )
    observed_at_ns = proof_observed_at_ns
    valid_until_ns = min(valuation_valid_until_ns, observed_at_ns + 60_000_000_000)
    if valid_until_ns <= observed_at_ns:
        raise RiskOrderEntryError("PROVIDER_EXPOSURE_FACTS_STALE")
    valuation_facts = {
        "fill_fingerprints": [item.fingerprint for item in facts["fill_facts"]],
        "instrument_metadata_sha256": metadata.digest,
        "instrument_multiplier": _valuation_facts_decimal_text(multiplier),
        "leaves_valuation_price": _valuation_facts_decimal_text(valuation_price),
        "quote_currency": quote_currency,
        "valuation_unit": valuation_unit,
    }
    if fx_rate is not None:
        valuation_facts["fx_rate"] = fx_rate.to_payload()
        valuation_facts["fx_rate_sha256"] = fx_rate.digest
    valuation_facts_sha256 = sha256_text(canonical_json(valuation_facts))
    return OrderExposureObservationProof(
        scope=claim.scope,
        permit_id=claim.permit_id,
        intent_id=claim.intent_id,
        intent_hash=claim.intent_hash,
        dispatch_claim_digest=claim.claim_digest,
        dispatch_proof_sha256=dispatch_proof_sha256,
        provider_order_id=str(order_payload.get("provider_order_id") or order_payload.get("order_id")),
        accepted_request_sha256=command.order_request_sha256,
        exposure_reservation_id=exposure_reservation_id,
        exposure_reservation_sha256=exposure_reservation_sha256,
        order_revision=facts["order_revision"],
        journal_revision=journal_revision,
        journal_record_sha256=journal_record_sha256,
        observation_sha256=str(order_payload["provider_observation_sha256"]),
        fill_history_sha256=str(fill_payload["provider_observation_sha256"]),
        source_alias_map_sha256=str(order_payload["source_alias_map_sha256"]),
        state=facts["state"],
        requested_quantity=request.quantity,
        cumulative_filled_quantity=filled_quantity,
        leaves_quantity=leaves_quantity,
        filled_gross_notional=filled_gross,
        leaves_gross_notional=leaves_gross,
        leaves_valuation_price=valuation_price if leaves_quantity > 0 else None,
        notional_unit=valuation_unit,
        complete_order_snapshot=True,
        complete_fill_history=True,
        fill_watermark_revision=facts["fill_watermark_revision"],
        source_contract_ref=contract.profile_id,
        source_contract_version=contract.fingerprint,
        provider_profile_version=contract.profile_id,
        normalizer_schema_version=contract.normalizer_schema,
        order_side=request.side.value.upper(),
        position_effect="OPEN",
        fills=tuple(facts["fill_facts"]),
        fill_trade_count=len(facts["fill_facts"]),
        fill_ids_sha256=fill_ids_sha256(tuple(facts["fill_facts"])),
        instrument_id=metadata.instrument,
        instrument_version=metadata.metadata_version,
        instrument_multiplier=multiplier,
        quote_currency=quote_currency,
        valuation_unit=valuation_unit,
        instrument_metadata_sha256=metadata.digest,
        instrument_metadata_observed_at_ns=metadata.as_of_ns,
        instrument_metadata_valid_until_ns=metadata.expires_at_ns,
        valuation_facts_sha256=valuation_facts_sha256,
        valuation_observed_at_ns=valuation_observed_at_ns,
        valuation_valid_until_ns=valuation_valid_until_ns,
        fx_pair=None if fx_rate is None else fx_rate.fx_pair,
        fx_rate=None if fx_rate is None else fx_rate.rate,
        fx_observed_at_ns=None if fx_rate is None else fx_rate.observed_at_ns,
        fx_valid_until_ns=None if fx_rate is None else fx_rate.valid_until_ns,
        observed_at_ns=observed_at_ns,
        valid_until_ns=valid_until_ns,
        writer_owner_id=writer_owner_id,
        writer_fencing_token=writer_fencing_token,
        writer_fence_sha256=writer_fence_sha256,
        trade_id_domain_ref=contract.trade_id_domain_ref,
        trade_id_scope=contract.trade_id_scope,
    )


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


def _safe_normalized_fill(fill: Mapping[str, Any] | None) -> dict[str, Any] | None:
    if fill is None:
        return None
    keys = (
        "account_id",
        "client_order_id",
        "order_id",
        "symbol",
        "side",
        "trade_id",
        "size",
        "price",
        "fee",
        "fee_currency",
    )
    result = {}
    for key in keys:
        value = fill.get(key)
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
