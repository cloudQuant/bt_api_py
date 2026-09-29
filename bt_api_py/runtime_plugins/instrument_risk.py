"""Sealed normalized metadata composition for instrument-aware managed admission.

The provider-neutral bt_api_risk package owns deterministic calculations. This
module converts a reviewed, normalized provider snapshot into that calculation
without constructing a provider client or reading user configuration.
"""

from __future__ import annotations

import hashlib
import json
import re
from collections.abc import Callable, Iterable, Mapping
from dataclasses import dataclass, field
from decimal import Decimal, InvalidOperation
from types import MappingProxyType
from typing import Any

from .catalog import LoadedCapabilities, RuntimePluginError
from .contracts import CAPABILITY_EXECUTION, CAPABILITY_RISK

_SNAPSHOT_SCHEMA = "bt_api_py.normalized-instrument-snapshot.v1"
_RECORD_SCHEMA = "bt_api_py.normalized-instrument-record.v1"
_IDENTIFIER = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:-]{0,127}$")
_INSTRUMENT = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:/-]{0,191}$")
_CURRENCY = re.compile(r"^[A-Z0-9][A-Z0-9._-]{1,15}$")
_TRADING_DAY = re.compile(r"^[0-9]{8}$")
_SHA256 = re.compile(r"^[0-9a-f]{64}$")
_QUANTITY_UNIT = re.compile(r"^[a-z][a-z0-9_-]{0,31}$")
_INSTRUMENT_METADATA_DIGEST_TAG = "instrument_metadata_digest"
_QUANTITY_UNIT_TAG = "quantity_unit"


def _canonical_json(value: object) -> str:
    return json.dumps(value, ensure_ascii=True, separators=(",", ":"), sort_keys=True)


def _sha256(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def _decimal_text(value: Decimal) -> str:
    return format(value, "f")


def _text(value: object, name: str, *, pattern: re.Pattern[str] = _IDENTIFIER) -> str:
    if (
        not isinstance(value, str)
        or value != value.strip()
        or not pattern.fullmatch(value)
    ):
        raise ValueError("invalid " + name)
    return value


def _currency(value: object, name: str) -> str:
    return _text(value, name, pattern=_CURRENCY)


def _quantity_unit(value: object, name: str) -> str:
    return _text(value, name, pattern=_QUANTITY_UNIT)


def _decimal(
    value: object,
    name: str,
    *,
    positive: bool = False,
    non_negative: bool = False,
) -> Decimal:
    if isinstance(value, (bool, float)):
        raise ValueError("invalid " + name)
    try:
        result = value if isinstance(value, Decimal) else Decimal(str(value))
    except (InvalidOperation, TypeError, ValueError) as error:
        raise ValueError("invalid " + name) from error
    if (
        not result.is_finite()
        or (positive and result <= 0)
        or (non_negative and result < 0)
    ):
        raise ValueError("invalid " + name)
    return result


def _timestamp_ns(value: object, name: str) -> int:
    if type(value) is not int or value <= 0:
        raise ValueError("invalid " + name)
    return value


def _optional_decimal(value: object, name: str) -> Decimal | None:
    return None if value is None else _decimal(value, name, positive=True)


def _require_exact_keys(
    value: Mapping[str, object],
    *,
    required: frozenset[str],
    optional: frozenset[str] = frozenset(),
    name: str,
) -> None:
    keys = set(value)
    if required - keys or keys - required - optional:
        raise ValueError("invalid " + name + " fields")


@dataclass(frozen=True)
class NormalizedInstrumentMetadata:
    """One provider-normalized, account-currency instrument risk record."""

    instrument: str
    tick_size: Decimal
    lot_size: Decimal
    contract_multiplier: Decimal
    max_gross_notional_account: Decimal
    quote_currency: str
    fee_currency: str
    account_currency: str
    quote_to_account_fx: Decimal
    fee_to_account_fx: Decimal
    taker_fee_bps: Decimal
    fixed_fee: Decimal
    max_slippage_bps: Decimal
    min_quantity: Decimal | None = None
    max_quantity: Decimal | None = None
    quantity_unit: str | None = None

    def __post_init__(self) -> None:
        object.__setattr__(
            self,
            "instrument",
            _text(self.instrument, "instrument", pattern=_INSTRUMENT),
        )
        for name in ("quote_currency", "fee_currency", "account_currency"):
            object.__setattr__(self, name, _currency(getattr(self, name), name))
        for name in (
            "tick_size",
            "lot_size",
            "contract_multiplier",
            "max_gross_notional_account",
            "quote_to_account_fx",
            "fee_to_account_fx",
        ):
            object.__setattr__(
                self, name, _decimal(getattr(self, name), name, positive=True)
            )
        for name in ("taker_fee_bps", "fixed_fee", "max_slippage_bps"):
            object.__setattr__(
                self, name, _decimal(getattr(self, name), name, non_negative=True)
            )
        for name in ("min_quantity", "max_quantity"):
            object.__setattr__(self, name, _optional_decimal(getattr(self, name), name))
        if self.quantity_unit is not None:
            object.__setattr__(
                self,
                "quantity_unit",
                _quantity_unit(self.quantity_unit, "quantity_unit"),
            )
        if (
            self.min_quantity is not None
            and self.max_quantity is not None
            and self.min_quantity > self.max_quantity
        ):
            raise ValueError("min_quantity cannot exceed max_quantity")
        if (
            self.quote_currency == self.account_currency
            and self.quote_to_account_fx != Decimal("1")
        ):
            raise ValueError("quote_to_account_fx must be one for account currency")
        if (
            self.fee_currency == self.account_currency
            and self.fee_to_account_fx != Decimal("1")
        ):
            raise ValueError("fee_to_account_fx must be one for account currency")

    @classmethod
    def from_normalized_payload(
        cls, value: Mapping[str, object], *, account_currency: str
    ) -> NormalizedInstrumentMetadata:
        """Parse one exact normalized-provider record without provider I/O."""

        if not isinstance(value, Mapping):
            raise ValueError("normalized instrument metadata must be a mapping")
        required = frozenset(
            {
                "instrument",
                "tick_size",
                "lot_size",
                "contract_multiplier",
                "max_gross_notional_account",
                "quote_currency",
                "fee_currency",
                "quote_to_account_fx",
                "fee_to_account_fx",
                "taker_fee_bps",
                "fixed_fee",
                "max_slippage_bps",
            }
        )
        _require_exact_keys(
            value,
            required=required,
            # Raw normalized provider facts deliberately do not need the
            # serialization-only fields.  A sealed snapshot's ``to_payload``
            # does include them, however, so accept and verify those fields
            # rather than making a serialized snapshot impossible to reload.
            optional=frozenset(
                {
                    "min_quantity",
                    "max_quantity",
                    "quantity_unit",
                    "schema",
                    "account_currency",
                }
            ),
            name="normalized metadata",
        )
        schema = value.get("schema")
        if schema is not None and schema != _RECORD_SCHEMA:
            raise ValueError("invalid normalized metadata schema")
        serialized_currency = value.get("account_currency")
        if serialized_currency is not None and _currency(
            serialized_currency, "account_currency"
        ) != account_currency:
            raise ValueError("record account currency does not match snapshot")
        return cls(
            instrument=value["instrument"],
            tick_size=value["tick_size"],
            lot_size=value["lot_size"],
            contract_multiplier=value["contract_multiplier"],
            max_gross_notional_account=value["max_gross_notional_account"],
            quote_currency=value["quote_currency"],
            fee_currency=value["fee_currency"],
            account_currency=account_currency,
            quote_to_account_fx=value["quote_to_account_fx"],
            fee_to_account_fx=value["fee_to_account_fx"],
            taker_fee_bps=value["taker_fee_bps"],
            fixed_fee=value["fixed_fee"],
            max_slippage_bps=value["max_slippage_bps"],
            min_quantity=value.get("min_quantity"),
            max_quantity=value.get("max_quantity"),
            quantity_unit=value.get("quantity_unit"),
        )

    def to_payload(self) -> dict[str, object]:
        payload: dict[str, object] = {
            "account_currency": self.account_currency,
            "contract_multiplier": _decimal_text(self.contract_multiplier),
            "fee_currency": self.fee_currency,
            "fee_to_account_fx": _decimal_text(self.fee_to_account_fx),
            "fixed_fee": _decimal_text(self.fixed_fee),
            "instrument": self.instrument,
            "lot_size": _decimal_text(self.lot_size),
            "max_gross_notional_account": _decimal_text(
                self.max_gross_notional_account
            ),
            "max_quantity": None
            if self.max_quantity is None
            else _decimal_text(self.max_quantity),
            "max_slippage_bps": _decimal_text(self.max_slippage_bps),
            "min_quantity": None
            if self.min_quantity is None
            else _decimal_text(self.min_quantity),
            "quote_currency": self.quote_currency,
            "quote_to_account_fx": _decimal_text(self.quote_to_account_fx),
            "schema": _RECORD_SCHEMA,
            "taker_fee_bps": _decimal_text(self.taker_fee_bps),
            "tick_size": _decimal_text(self.tick_size),
        }
        if self.quantity_unit is not None:
            payload["quantity_unit"] = self.quantity_unit
        return payload


@dataclass(frozen=True)
class SealedNormalizedInstrumentMetadataSnapshot:
    """Scope-bound, time-bounded normalized provider metadata."""

    provider: str
    environment: str
    account_ref: str
    trading_day: str
    metadata_version: str
    as_of_ns: int
    expires_at_ns: int
    account_currency: str
    instruments: tuple[NormalizedInstrumentMetadata, ...]

    def __post_init__(self) -> None:
        for name in ("provider", "environment", "account_ref", "metadata_version"):
            object.__setattr__(self, name, _text(getattr(self, name), name))
        object.__setattr__(
            self,
            "trading_day",
            _text(self.trading_day, "trading_day", pattern=_TRADING_DAY),
        )
        object.__setattr__(
            self,
            "account_currency",
            _currency(self.account_currency, "account_currency"),
        )
        object.__setattr__(self, "as_of_ns", _timestamp_ns(self.as_of_ns, "as_of_ns"))
        object.__setattr__(
            self, "expires_at_ns", _timestamp_ns(self.expires_at_ns, "expires_at_ns")
        )
        if self.expires_at_ns <= self.as_of_ns:
            raise ValueError("expires_at_ns must be after as_of_ns")
        records = tuple(self.instruments)
        if not records:
            raise ValueError("normalized metadata snapshot needs instruments")
        by_instrument: dict[str, NormalizedInstrumentMetadata] = {}
        for record in records:
            if not isinstance(record, NormalizedInstrumentMetadata):
                raise ValueError("normalized metadata records are required")
            if record.account_currency != self.account_currency:
                raise ValueError("record account currency does not match snapshot")
            if record.instrument in by_instrument:
                raise ValueError("duplicate normalized instrument metadata")
            by_instrument[record.instrument] = record
        object.__setattr__(
            self,
            "instruments",
            tuple(by_instrument[key] for key in sorted(by_instrument)),
        )

    @classmethod
    def from_normalized_payload(
        cls, value: Mapping[str, object]
    ) -> SealedNormalizedInstrumentMetadataSnapshot:
        """Seal an exact normalized SDK/fake-provider payload without network I/O."""

        if not isinstance(value, Mapping):
            raise ValueError("normalized metadata snapshot must be a mapping")
        required = frozenset(
            {
                "provider",
                "environment",
                "account_ref",
                "trading_day",
                "metadata_version",
                "as_of_ns",
                "expires_at_ns",
                "account_currency",
                "instruments",
            }
        )
        _require_exact_keys(
            value,
            required=required,
            optional=frozenset({"schema"}),
            name="normalized metadata snapshot",
        )
        schema = value.get("schema")
        if schema is not None and schema != _SNAPSHOT_SCHEMA:
            raise ValueError("invalid normalized metadata snapshot schema")
        records = value["instruments"]
        if isinstance(records, (str, bytes)):
            raise ValueError("normalized metadata instruments must be iterable")
        try:
            payloads = tuple(records)
        except TypeError as error:
            raise ValueError(
                "normalized metadata instruments must be iterable"
            ) from error
        account_currency = _currency(value["account_currency"], "account_currency")
        return cls(
            provider=value["provider"],
            environment=value["environment"],
            account_ref=value["account_ref"],
            trading_day=value["trading_day"],
            metadata_version=value["metadata_version"],
            as_of_ns=value["as_of_ns"],
            expires_at_ns=value["expires_at_ns"],
            account_currency=account_currency,
            instruments=tuple(
                NormalizedInstrumentMetadata.from_normalized_payload(
                    item, account_currency=account_currency
                )
                for item in payloads
            ),
        )

    def to_payload(self) -> dict[str, object]:
        return {
            "account_currency": self.account_currency,
            "account_ref": self.account_ref,
            "as_of_ns": self.as_of_ns,
            "environment": self.environment,
            "expires_at_ns": self.expires_at_ns,
            "instruments": [item.to_payload() for item in self.instruments],
            "metadata_version": self.metadata_version,
            "provider": self.provider,
            "schema": _SNAPSHOT_SCHEMA,
            "trading_day": self.trading_day,
        }

    @property
    def digest(self) -> str:
        """Bind scope, freshness, fees, FX, lot, multiplier, and trading day."""

        return _sha256(_canonical_json(self.to_payload()))

    def instrument_digest(self, instrument: str) -> str:
        record = self._record(instrument)
        return _sha256(
            _canonical_json(
                {
                    "instrument": record.instrument,
                    "schema": _RECORD_SCHEMA,
                    "snapshot_digest": self.digest,
                }
            )
        )

    def instrument_metadata(self, instrument: str) -> NormalizedInstrumentMetadata:
        """Return the immutable normalized record for one registered instrument."""

        return self._record(instrument)

    def require_execution_scope(self, scope: Any) -> None:
        """Reject a snapshot whose provider/account/day is not the execution scope."""

        expected = {
            "provider": self.provider,
            "environment": self.environment,
            "account_ref": self.account_ref,
            "trading_day": self.trading_day,
        }
        for name, value in expected.items():
            if getattr(scope, name, None) != value:
                code = (
                    "INSTRUMENT_SNAPSHOT_TRADING_DAY_MISMATCH"
                    if name == "trading_day"
                    else "INSTRUMENT_SNAPSHOT_SCOPE_MISMATCH"
                )
                raise RuntimePluginError(
                    code, "instrument metadata snapshot does not match execution scope"
                )

    def require_live_metadata(self) -> None:
        """Require provider quantity semantics before a live route can exist."""

        if any(record.quantity_unit is None for record in self.instruments):
            raise RuntimePluginError(
                "INSTRUMENT_SNAPSHOT_QUANTITY_UNIT_REQUIRED",
                "managed live metadata must seal each instrument quantity unit",
            )

    def require_bound_intent(
        self, intent: Any, *, now_ns: int, require_fresh: bool
    ) -> NormalizedInstrumentMetadata:
        """Validate the exact snapshot digest before risk admission."""

        self.require_execution_scope(getattr(intent, "scope", None))
        record = self._record(getattr(intent, "instrument", None))
        if getattr(intent, "metadata_version", None) != self.metadata_version:
            raise RuntimePluginError(
                "INSTRUMENT_SNAPSHOT_METADATA_VERSION_MISMATCH",
                "intent metadata version differs from the sealed snapshot",
            )
        tags = getattr(intent, "tags", None)
        if not isinstance(tags, Mapping):
            raise RuntimePluginError(
                "INSTRUMENT_SNAPSHOT_METADATA_DIGEST_REQUIRED",
                "intent lacks the sealed instrument metadata digest",
            )
        observed = tags.get(_INSTRUMENT_METADATA_DIGEST_TAG)
        if not isinstance(observed, str) or not _SHA256.fullmatch(observed):
            raise RuntimePluginError(
                "INSTRUMENT_SNAPSHOT_METADATA_DIGEST_REQUIRED",
                "intent lacks the sealed instrument metadata digest",
            )
        if observed != self.instrument_digest(record.instrument):
            raise RuntimePluginError(
                "INSTRUMENT_SNAPSHOT_METADATA_DIGEST_MISMATCH",
                "intent metadata digest differs from the sealed instrument snapshot",
            )
        if record.quantity_unit is not None:
            observed_unit = tags.get(_QUANTITY_UNIT_TAG)
            if observed_unit is None:
                raise RuntimePluginError(
                    "INSTRUMENT_SNAPSHOT_QUANTITY_UNIT_REQUIRED",
                    "intent lacks the sealed instrument quantity unit",
                )
            if observed_unit != record.quantity_unit:
                raise RuntimePluginError(
                    "INSTRUMENT_SNAPSHOT_QUANTITY_UNIT_MISMATCH",
                    "intent quantity unit differs from the sealed instrument snapshot",
                )
        if require_fresh:
            now_ns = _timestamp_ns(now_ns, "metadata clock")
            if now_ns < self.as_of_ns:
                raise RuntimePluginError(
                    "INSTRUMENT_SNAPSHOT_METADATA_NOT_ACTIVE",
                    "instrument metadata snapshot is not active yet",
                )
            if now_ns >= self.expires_at_ns:
                raise RuntimePluginError(
                    "INSTRUMENT_SNAPSHOT_METADATA_STALE",
                    "instrument metadata snapshot is stale",
                )
        return record

    def to_risk_metadata(self, risk: Any) -> tuple[Any, ...]:
        """Convert normalized facts to provider-neutral account-risk records."""

        factory = getattr(risk, "InstrumentRiskMetadata", None)
        if not callable(factory):
            raise RuntimePluginError(
                "INSTRUMENT_RISK_METADATA_FACTORY_MISSING",
                "loaded risk capability lacks InstrumentRiskMetadata",
            )
        try:
            records = [
                factory(
                    instrument=item.instrument,
                    metadata_version=self.metadata_version,
                    as_of_ns=self.as_of_ns,
                    expires_at_ns=self.expires_at_ns,
                    tick_size=item.tick_size,
                    quantity_step=item.lot_size,
                    contract_multiplier=item.contract_multiplier
                    * item.quote_to_account_fx,
                    max_gross_notional=item.max_gross_notional_account,
                    min_quantity=item.min_quantity,
                    max_quantity=item.max_quantity,
                    taker_fee_bps=item.taker_fee_bps,
                    fixed_fee=item.fixed_fee * item.fee_to_account_fx,
                    max_slippage_bps=item.max_slippage_bps,
                )
                for item in self.instruments
            ]
        except (TypeError, ValueError) as error:
            raise RuntimePluginError(
                "INSTRUMENT_SNAPSHOT_RISK_CONVERSION_INVALID",
                "sealed normalized metadata cannot form account-risk facts",
            ) from error
        return tuple(records)

    def _record(self, instrument: object) -> NormalizedInstrumentMetadata:
        if not isinstance(instrument, str):
            raise RuntimePluginError(
                "INSTRUMENT_SNAPSHOT_INSTRUMENT_UNREGISTERED",
                "intent instrument is absent from the sealed snapshot",
            )
        for record in self.instruments:
            if record.instrument == instrument:
                return record
        raise RuntimePluginError(
            "INSTRUMENT_SNAPSHOT_INSTRUMENT_UNREGISTERED",
            "intent instrument is absent from the sealed snapshot",
        )


class _IntentWithRiskMetadataDigest:
    """Delegate an intent while exposing only the internal risk digest."""

    def __init__(self, intent: Any, risk_digest: str) -> None:
        self._intent = intent
        tags = dict(getattr(intent, "tags", {}))
        tags[_INSTRUMENT_METADATA_DIGEST_TAG] = risk_digest
        self.tags = MappingProxyType(tags)

    def __getattr__(self, name: str) -> Any:
        return getattr(self._intent, name)


class _SnapshotBoundInstrumentRiskMapper:
    """Bind a sealed snapshot before delegating math to bt_api_risk."""

    def __init__(
        self,
        *,
        risk: Any,
        snapshot: SealedNormalizedInstrumentMetadataSnapshot,
        mapper: Any,
        risk_metadata: Mapping[str, Any],
        clock_ns: Callable[[], int],
    ) -> None:
        self._risk = risk
        self._snapshot = snapshot
        self._mapper = mapper
        self._risk_metadata = MappingProxyType(dict(risk_metadata))
        self._clock_ns = clock_ns

    def __call__(self, intent: Any) -> Any:
        effect = getattr(getattr(intent, "position_effect", None), "value", None)
        record = self._snapshot.require_bound_intent(
            intent,
            now_ns=self._clock_ns(),
            # Existing safe-reduction semantics permit an expired quote/FX
            # snapshot for a close, while still requiring scope/day/digest/lattice.
            require_fresh=effect == "OPEN",
        )
        metadata = self._risk_metadata[record.instrument]
        mapped = self._mapper(_IntentWithRiskMetadataDigest(intent, metadata.digest))
        return self._risk.RiskIntent(
            intent_id=mapped.intent_id,
            scope=mapped.scope,
            action=mapped.action,
            notional=mapped.notional,
            payload_fingerprint=_sha256(
                _canonical_json(
                    {
                        "instrument_digest": self._snapshot.instrument_digest(
                            record.instrument
                        ),
                        "risk_payload_fingerprint": mapped.payload_fingerprint,
                        "schema": _SNAPSHOT_SCHEMA,
                        "snapshot_digest": self._snapshot.digest,
                        "trading_day": self._snapshot.trading_day,
                    }
                )
            ),
        )


@dataclass(frozen=True)
class InstrumentRiskAdmission:
    """Immutable objects installed on one managed execution facade."""

    registry: Any
    mapper: Any
    admission_gate: Any
    snapshot: SealedNormalizedInstrumentMetadataSnapshot | None = None
    metadata_digests: Mapping[str, str] = field(default_factory=dict)

    def __post_init__(self) -> None:
        object.__setattr__(
            self, "metadata_digests", MappingProxyType(dict(self.metadata_digests))
        )

    def map_execution_intent(self, intent: Any) -> Any:
        return self.mapper(intent)

    def metadata_digest_for(self, instrument: str) -> str:
        try:
            return self.metadata_digests[instrument]
        except KeyError as error:
            raise RuntimePluginError(
                "INSTRUMENT_SNAPSHOT_INSTRUMENT_UNREGISTERED",
                "instrument is absent from the sealed metadata admission",
            ) from error


def compose_instrument_risk_admission(
    capabilities: LoadedCapabilities,
    *,
    risk_gate: Any,
    risk_scope: Any,
    metadata: Iterable[Any] | None = None,
    normalized_snapshot: SealedNormalizedInstrumentMetadataSnapshot | None = None,
    execution_scope: Any | None = None,
    clock_ns: Callable[[], int] | None = None,
) -> InstrumentRiskAdmission:
    """Build a fail-closed instrument-aware shared admission gate.

    Existing callers may pass provider-neutral metadata directly. New managed
    composition passes normalized_snapshot plus the exact execution_scope.
    """

    if not isinstance(capabilities, LoadedCapabilities):
        raise TypeError("LoadedCapabilities is required")
    if not capabilities.contract.is_managed_execution:
        raise RuntimePluginError(
            "MANAGED_CONTRACT_REQUIRED",
            "instrument risk admission requires a sealed managed execution contract",
        )
    execution = capabilities.require(CAPABILITY_EXECUTION)
    risk = capabilities.require(CAPABILITY_RISK)
    if not isinstance(risk_gate, risk.DurableRiskGate):
        raise RuntimePluginError(
            "INSTRUMENT_RISK_GATE_INVALID",
            "instrument admission requires the loaded durable risk gate",
        )
    if not isinstance(risk_scope, risk.AccountScope):
        raise RuntimePluginError(
            "INSTRUMENT_RISK_SCOPE_INVALID",
            "instrument admission requires the loaded account risk scope",
        )
    if metadata is not None and normalized_snapshot is not None:
        raise RuntimePluginError(
            "INSTRUMENT_RISK_METADATA_SOURCE_AMBIGUOUS",
            "instrument admission accepts direct metadata or one normalized snapshot",
        )
    if metadata is None and normalized_snapshot is None:
        raise RuntimePluginError(
            "INSTRUMENT_RISK_METADATA_SOURCE_REQUIRED",
            "instrument admission requires sealed metadata",
        )
    if clock_ns is not None and not callable(clock_ns):
        raise RuntimePluginError(
            "INSTRUMENT_RISK_CLOCK_INVALID", "clock_ns must be callable"
        )
    admission_clock = clock_ns or _wall_clock_ns
    snapshot: SealedNormalizedInstrumentMetadataSnapshot | None = None
    metadata_digests: Mapping[str, str] = {}
    try:
        if normalized_snapshot is not None:
            if not isinstance(
                normalized_snapshot, SealedNormalizedInstrumentMetadataSnapshot
            ):
                raise RuntimePluginError(
                    "INSTRUMENT_SNAPSHOT_INVALID",
                    "normalized_snapshot must be a sealed normalized metadata snapshot",
                )
            if execution_scope is None:
                raise RuntimePluginError(
                    "INSTRUMENT_SNAPSHOT_SCOPE_REQUIRED",
                    "sealed normalized metadata needs the exact execution scope",
                )
            normalized_snapshot.require_execution_scope(execution_scope)
            source_metadata = normalized_snapshot.to_risk_metadata(risk)
            registry = risk.InstrumentRiskRegistry(source_metadata)
            base_mapper = risk.InstrumentRiskAdmissionMapper(
                risk_scope, registry, clock_ns=admission_clock
            )
            by_instrument = {item.instrument: item for item in source_metadata}
            mapper = _SnapshotBoundInstrumentRiskMapper(
                risk=risk,
                snapshot=normalized_snapshot,
                mapper=base_mapper,
                risk_metadata=by_instrument,
                clock_ns=admission_clock,
            )
            snapshot = normalized_snapshot
            metadata_digests = {
                item.instrument: normalized_snapshot.instrument_digest(item.instrument)
                for item in normalized_snapshot.instruments
            }
        else:
            registry = risk.InstrumentRiskRegistry(metadata)
            mapper = risk.InstrumentRiskAdmissionMapper(
                risk_scope, registry, clock_ns=admission_clock
            )
    except RuntimePluginError:
        raise
    except (TypeError, ValueError) as error:
        raise RuntimePluginError(
            "INSTRUMENT_RISK_METADATA_INVALID",
            "instrument risk metadata cannot form a sealed admission policy",
        ) from error
    admission_gate = execution.SharedRiskAdmissionAdapter(risk_gate, mapper)
    return InstrumentRiskAdmission(
        registry=registry,
        mapper=mapper,
        admission_gate=admission_gate,
        snapshot=snapshot,
        metadata_digests=metadata_digests,
    )


def _wall_clock_ns() -> int:
    import time

    return time.time_ns()


__all__ = [
    "InstrumentRiskAdmission",
    "NormalizedInstrumentMetadata",
    "SealedNormalizedInstrumentMetadataSnapshot",
    "compose_instrument_risk_admission",
]
