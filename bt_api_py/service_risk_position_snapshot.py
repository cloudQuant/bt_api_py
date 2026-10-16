"""Bridge a Gateway owner's full position snapshot into the Risk journal.

This is an in-process server-side producer. It consumes an owner-issued
Gateway receipt and a trusted valuation/allocation resolver; it does not
accept client snapshots or completeness claims.
"""

from __future__ import annotations

import hashlib
import json
import re
from collections import defaultdict
from dataclasses import dataclass
from decimal import Decimal
from types import MappingProxyType
from typing import Callable, Mapping

from bt_api_base.account_protocol import AccountKey, AccountPosition, AccountResourceKey
from bt_api_base.market_protocol import MarketInstrumentKey
from bt_api_gateway.account_position_snapshot import (
    GatewayFullPositionSnapshotReceipt,
)
from bt_api_gateway.account_session_worker import GatewayAccountSessionWorker
from bt_api_risk import (
    AccountPositionRiskFact,
    AccountScope,
    DurableRiskGate,
    GrossExposureBaselineProof,
    InstrumentRiskFxRate,
    InstrumentRiskGreeks,
    InstrumentRiskMetadata,
    PositionSnapshotCoverage,
)

from .service_risk_order_journal import (
    RiskOrderJournal,
    RiskOrderJournalError,
    RiskOrderWriterFence,
    canonical_json,
    sha256_text,
)


class RiskPositionSnapshotProducerError(RuntimeError):
    """Finite, payload-free trusted snapshot bridge error."""

    def __init__(self, code: str) -> None:
        self.code = code
        super().__init__(code)


@dataclass(frozen=True, slots=True)
class RiskPositionValuation:
    """Trusted server-side attribution and valuation for one nonzero position.

    Position identity, quantity, unit, side and source-row digest are derived
    by the producer from the Gateway observation. The resolver supplies only
    strategy attribution and current typed market facts.
    """

    strategy_id: str
    metadata: InstrumentRiskMetadata
    mark_price: Decimal
    mark_observed_at_ns: int
    mark_valid_until_ns: int
    mark_source_ref: str
    mark_source_revision: str
    mark_source_record_sha256: str
    fx_rate: InstrumentRiskFxRate | None = None
    greeks: InstrumentRiskGreeks | None = None


@dataclass(frozen=True, slots=True)
class RiskPositionValuationResolver:
    """Trusted operator-configured market/account identity plus valuation code.

    The resolver is injected through server-side ``ComponentResources``. Its
    expected identities are separate configuration facts; observed position
    rows are checked against them and never establish the expectation.
    """

    expected_account_key: AccountKey
    expected_instruments_by_instrument: Mapping[str, MarketInstrumentKey]
    resolve: Callable[
        [AccountPosition, GatewayFullPositionSnapshotReceipt], RiskPositionValuation
    ]

    def __post_init__(self) -> None:
        if (
            type(self.expected_account_key) is not AccountKey
            or not isinstance(self.expected_instruments_by_instrument, Mapping)
            or not self.expected_instruments_by_instrument
            or not callable(self.resolve)
        ):
            raise ValueError("invalid trusted position valuation resolver")
        expected: dict[str, MarketInstrumentKey] = {}
        for instrument_id, instrument_key in self.expected_instruments_by_instrument.items():
            if (
                type(instrument_id) is not str
                or type(instrument_key) is not MarketInstrumentKey
                or instrument_key.instrument_id != instrument_id
            ):
                raise ValueError("invalid trusted position instrument identity")
            expected[instrument_id] = instrument_key
        object.__setattr__(
            self,
            "expected_instruments_by_instrument",
            MappingProxyType(expected),
        )

    def __call__(
        self,
        position: AccountPosition,
        receipt: GatewayFullPositionSnapshotReceipt,
    ) -> RiskPositionValuation:
        return self.resolve(position, receipt)

_SOURCE_REFERENCE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:/-]{0,127}$")


def _canonical_hash(value: object) -> str:
    return hashlib.sha256(canonical_json(value).encode("utf-8")).hexdigest()


class RiskPositionSnapshotProducer:
    """Convert a live Gateway full-position receipt into a fenced Risk baseline."""

    def __init__(
        self,
        worker: GatewayAccountSessionWorker,
        journal: RiskOrderJournal,
        *,
        scope: AccountScope,
        expected_account_key: AccountKey,
        notional_unit: str,
        valuation_resolver: RiskPositionValuationResolver,
        expected_instruments_by_instrument: Mapping[str, MarketInstrumentKey],
        metadata_by_instrument: Mapping[str, InstrumentRiskMetadata],
        resource_by_instrument: Mapping[str, AccountResourceKey],
        strategies_by_instrument: Mapping[str, frozenset[str]],
    ) -> None:
        if type(worker) is not GatewayAccountSessionWorker:
            raise RiskPositionSnapshotProducerError("GATEWAY_OWNER_REQUIRED")
        if type(journal) is not RiskOrderJournal:
            raise RiskPositionSnapshotProducerError("RISK_JOURNAL_REQUIRED")
        if type(scope) is not AccountScope:
            raise RiskPositionSnapshotProducerError("RISK_SCOPE_REQUIRED")
        if (
            type(expected_account_key) is not AccountKey
            or worker.account_key != expected_account_key
            or scope.provider != expected_account_key.provider
            or scope.environment != expected_account_key.environment
            or scope.account_id != expected_account_key.account_id
            or expected_account_key.subaccount_id is not None
        ):
            raise RiskPositionSnapshotProducerError("ACCOUNT_OWNER_SCOPE_MISMATCH")
        if (
            type(notional_unit) is not str
            or not notional_unit
            or not notional_unit.isupper()
        ):
            raise RiskPositionSnapshotProducerError("NOTIONAL_UNIT_INVALID")
        if type(valuation_resolver) is not RiskPositionValuationResolver:
            raise RiskPositionSnapshotProducerError("POSITION_VALUATION_RESOLVER_REQUIRED")
        if not isinstance(expected_instruments_by_instrument, Mapping):
            raise RiskPositionSnapshotProducerError("POSITION_SOURCE_BINDINGS_INVALID")
        if (
            valuation_resolver.expected_account_key != expected_account_key
            or dict(valuation_resolver.expected_instruments_by_instrument)
            != dict(expected_instruments_by_instrument)
        ):
            raise RiskPositionSnapshotProducerError("POSITION_SOURCE_BINDINGS_INVALID")
        if (
            not isinstance(metadata_by_instrument, Mapping)
            or not metadata_by_instrument
            or set(expected_instruments_by_instrument) != set(metadata_by_instrument)
            or not isinstance(resource_by_instrument, Mapping)
            or set(metadata_by_instrument) != set(resource_by_instrument)
            or not isinstance(strategies_by_instrument, Mapping)
            or set(metadata_by_instrument) != set(strategies_by_instrument)
        ):
            raise RiskPositionSnapshotProducerError("POSITION_SOURCE_BINDINGS_INVALID")
        metadata_copy: dict[str, InstrumentRiskMetadata] = {}
        instrument_identity_copy: dict[str, MarketInstrumentKey] = {}
        resource_copy: dict[str, AccountResourceKey] = {}
        strategy_copy: dict[str, frozenset[str]] = {}
        for instrument, metadata in metadata_by_instrument.items():
            resource = resource_by_instrument[instrument]
            instrument_identity = expected_instruments_by_instrument[instrument]
            strategies = strategies_by_instrument[instrument]
            if (
                type(instrument) is not str
                or type(metadata) is not InstrumentRiskMetadata
                or metadata.instrument != instrument
                or type(instrument_identity) is not MarketInstrumentKey
                or instrument_identity.instrument_id != instrument
                or instrument_identity.provider != expected_account_key.provider
                or instrument_identity.venue != expected_account_key.venue
                or instrument_identity.environment != expected_account_key.environment
                or type(resource) is not AccountResourceKey
                or instrument_identity.product_type != resource.product_type
                or type(strategies) is not frozenset
                or not strategies
                or any(
                    type(strategy_id) is not str
                    or _SOURCE_REFERENCE.fullmatch(strategy_id) is None
                    for strategy_id in strategies
                )
            ):
                raise RiskPositionSnapshotProducerError("POSITION_SOURCE_BINDINGS_INVALID")
            metadata_copy[instrument] = metadata
            instrument_identity_copy[instrument] = instrument_identity
            resource_copy[instrument] = resource
            strategy_copy[instrument] = strategies
        self._worker = worker
        self._journal = journal
        self._scope = scope
        self._expected_account_key = expected_account_key
        self._notional_unit = notional_unit
        self._valuation_resolver = valuation_resolver
        self._expected_instruments_by_instrument = MappingProxyType(
            instrument_identity_copy
        )
        self._metadata_by_instrument = MappingProxyType(metadata_copy)
        self._resource_by_instrument = MappingProxyType(resource_copy)
        self._strategies_by_instrument = MappingProxyType(strategy_copy)

    def ingest(self, fence: RiskOrderWriterFence) -> GrossExposureBaselineProof:
        """Read the current owner receipt, derive every row and persist once."""

        if type(fence) is not RiskOrderWriterFence or fence.scope != self._scope:
            raise RiskPositionSnapshotProducerError("RISK_WRITER_FENCE_MISMATCH")
        try:
            receipt = self._worker.read_full_position_snapshot_receipt()
            snapshot = self.build_source_snapshot(receipt)
            return self._journal.ingest_gross_exposure_baseline(
                fence,
                source_snapshot=snapshot,
                position_source_receipt=receipt,
            )
        except RiskOrderJournalError:
            raise
        except Exception as exc:
            code = getattr(exc, "code", None)
            if type(code) is str and code:
                raise RiskPositionSnapshotProducerError(code) from None
            raise RiskPositionSnapshotProducerError("POSITION_BASELINE_PRODUCTION_FAILED") from None

    def build_source_snapshot(
        self, receipt: GatewayFullPositionSnapshotReceipt
    ) -> dict[str, object]:
        """Build an exact source snapshot from the current owner receipt."""

        if type(receipt) is not GatewayFullPositionSnapshotReceipt:
            raise RiskPositionSnapshotProducerError("GATEWAY_POSITION_RECEIPT_REQUIRED")
        try:
            receipt.assert_current()
        except Exception:
            raise RiskPositionSnapshotProducerError("GATEWAY_POSITION_RECEIPT_STALE") from None
        source = receipt.coverage
        observation = receipt.observation
        account_key = receipt.account_key
        if (
            account_key != self._expected_account_key
            or account_key.provider != self._scope.provider
            or account_key.environment != self._scope.environment
            or source.account_key_id != account_key.account_key_id
            or source.session_generation != receipt.session_generation
            or source.positions_sha256 != _canonical_hash(
                [item.to_dict() for item in observation.positions]
            )
        ):
            raise RiskPositionSnapshotProducerError("ACCOUNT_OWNER_SCOPE_MISMATCH")
        if (
            not observation.positions_complete
            or observation.open_orders is None
            or not observation.open_orders_complete
            or observation.open_orders
        ):
            raise RiskPositionSnapshotProducerError("POSITION_SNAPSHOT_INCOMPLETE")

        coverage = PositionSnapshotCoverage(
            scope=self._scope,
            account_revision=source.account_revision,
            source_schema_version=source.source_schema_version,
            query_scope=source.query_scope,
            filters=source.filters,
            permission_scope=source.permission_scope,
            permission_revision=source.permission_revision,
            pagination_exhausted=source.pagination_exhausted,
            page_count=source.page_count,
            snapshot_version=source.snapshot_version,
            observed_at_ns=source.observed_at_ns,
            valid_until_ns=receipt.valid_until_ns,
        )
        row_payloads = [item.to_dict() for item in observation.positions]
        row_by_digest = {
            _canonical_hash(row): row for row in row_payloads
        }
        if len(row_by_digest) != len(row_payloads):
            raise RiskPositionSnapshotProducerError("POSITION_SOURCE_ROW_DUPLICATE")

        facts: list[AccountPositionRiskFact] = []
        facts_by_strategy: dict[str, list[AccountPositionRiskFact]] = defaultdict(list)
        for position in observation.positions:
            raw_quantity = Decimal(position.quantity)
            if raw_quantity == 0:
                continue
            instrument = position.instrument.instrument_id
            expected_instrument = self._expected_instruments_by_instrument.get(instrument)
            expected_metadata = self._metadata_by_instrument.get(instrument)
            expected_resource = self._resource_by_instrument.get(instrument)
            allowed_strategies = self._strategies_by_instrument.get(instrument)
            if (
                expected_metadata is None
                or expected_instrument is None
                or expected_resource is None
                or allowed_strategies is None
                or position.instrument != expected_instrument
                or position.resource != expected_resource
                or position.instrument.provider != account_key.provider
                or position.instrument.venue != account_key.venue
                or position.instrument.environment != account_key.environment
                or position.instrument.product_type != expected_resource.product_type
                or position.price_currency != expected_metadata.quote_currency
            ):
                raise RiskPositionSnapshotProducerError("POSITION_SOURCE_BINDING_MISMATCH")
            valuation = self._valuation_resolver(position, receipt)
            if type(valuation) is not RiskPositionValuation:
                raise RiskPositionSnapshotProducerError("POSITION_VALUATION_INVALID")
            if (
                type(valuation.metadata) is not InstrumentRiskMetadata
                or valuation.metadata != expected_metadata
                or valuation.metadata.instrument != instrument
                or valuation.metadata.valuation_unit != self._notional_unit
                or valuation.metadata.quantity_unit != position.qty_unit
                or valuation.strategy_id not in allowed_strategies
            ):
                raise RiskPositionSnapshotProducerError("POSITION_METADATA_MISMATCH")
            row_digest = _canonical_hash(position.to_dict())
            if position.side == "net":
                position_side = "SHORT" if raw_quantity < 0 else "LONG"
            else:
                position_side = "SHORT" if position.side == "short" else "LONG"
            try:
                fact = AccountPositionRiskFact(
                    position_id=(
                        f"pos-{account_key.account_key_id[-12:]}-"
                        f"{source.account_revision}-{row_digest[:24]}"
                    ),
                    strategy_id=valuation.strategy_id,
                    instrument=position.instrument.instrument_id,
                    position_side=position_side,
                    quantity=abs(raw_quantity),
                    quantity_unit=position.qty_unit,
                    mark_price=valuation.mark_price,
                    mark_observed_at_ns=valuation.mark_observed_at_ns,
                    mark_valid_until_ns=valuation.mark_valid_until_ns,
                    mark_source_ref=valuation.mark_source_ref,
                    mark_source_revision=valuation.mark_source_revision,
                    mark_source_record_sha256=valuation.mark_source_record_sha256,
                    position_source_record_sha256=row_digest,
                    metadata=valuation.metadata,
                    fx_rate=valuation.fx_rate,
                    greeks=valuation.greeks,
                )
            except (TypeError, ValueError):
                raise RiskPositionSnapshotProducerError("POSITION_VALUATION_INVALID") from None
            facts.append(fact)
            facts_by_strategy[fact.strategy_id].append(fact)

        if len({fact.position_source_record_sha256 for fact in facts}) != len(facts):
            raise RiskPositionSnapshotProducerError("POSITION_SOURCE_FACT_DUPLICATE")
        for fact in facts:
            if fact.position_source_record_sha256 not in row_by_digest:
                raise RiskPositionSnapshotProducerError("POSITION_SOURCE_FACT_UNBOUND")

        summary_rows: list[dict[str, object]] = []
        for strategy_id, strategy_facts in sorted(facts_by_strategy.items()):
            gross = Decimal(0)
            source_rows: list[object] = []
            for fact in sorted(strategy_facts, key=lambda item: item.position_id):
                gross_value, _ = fact.exposure_dimensions(
                    (),
                    now_ns=coverage.observed_at_ns,
                    max_age_ns=coverage.observed_at_ns,
                )
                gross += gross_value
                source_rows.append(row_by_digest[fact.position_source_record_sha256])
            ordered_facts = sorted(strategy_facts, key=lambda item: item.position_id)
            summary_rows.append(
                {
                    "active_increase_intent_count": len(strategy_facts),
                    "gross_notional": str(gross),
                    "notional_unit": self._notional_unit,
                    "source_record_sha256": _canonical_hash(source_rows),
                    "strategy_id": strategy_id,
                    "valuation_facts_sha256": _canonical_hash(
                        [fact.to_payload() for fact in ordered_facts]
                    ),
                }
            )

        fact_payloads = [fact.to_payload() for fact in facts]
        return {
            "account_revision": coverage.account_revision,
            "account_scope": {
                "account_id": self._scope.account_id,
                "environment": self._scope.environment,
                "provider": self._scope.provider,
            },
            "complete_open_orders": True,
            "complete_positions": True,
            "notional_unit": self._notional_unit,
            "observed_at_ns": coverage.observed_at_ns,
            "open_orders": [],
            "position_coverage": coverage.to_payload(),
            "position_risk_facts": fact_payloads,
            "position_account_key_id": account_key.account_key_id,
            "position_source_receipt_sha256": receipt.fingerprint,
            "positions": summary_rows,
            "provider_positions": row_payloads,
            "source_schema_version": coverage.source_schema_version,
            "valid_until_ns": coverage.valid_until_ns,
            "valuation_facts": fact_payloads,
        }


class RiskPositionSnapshotOwner:
    """Host-thread owner that primes and advances one dimension baseline."""

    def __init__(
        self,
        worker: GatewayAccountSessionWorker,
        journal: RiskOrderJournal,
        gate: DurableRiskGate,
        producer: RiskPositionSnapshotProducer,
    ) -> None:
        if (
            type(worker) is not GatewayAccountSessionWorker
            or type(journal) is not RiskOrderJournal
            or type(gate) is not DurableRiskGate
            or type(producer) is not RiskPositionSnapshotProducer
            or getattr(gate, "_execution_journal_authority", None) is not journal
            or getattr(gate, "_gross_exposure_authority", None) is not journal
        ):
            raise RiskPositionSnapshotProducerError("RISK_POSITION_OWNER_COMPOSITION_INVALID")
        self._worker = worker
        self._journal = journal
        self._gate = gate
        self._producer = producer
        self._scope = producer._scope
        self._ready = False

    @property
    def ready(self) -> bool:
        return self._ready

    def prime(self) -> None:
        """Install or verify a fresh trusted baseline before listener startup."""

        self._synchronize()

    def maintain(self, active_registration_ids: frozenset[str]) -> None:
        """Refresh from the already-owned Gateway snapshot on the Host thread."""

        if type(active_registration_ids) is not frozenset or any(
            type(item) is not str for item in active_registration_ids
        ):
            self._ready = False
            raise RiskPositionSnapshotProducerError("RISK_POSITION_OWNER_INPUT_INVALID")
        self._synchronize()

    def _synchronize(self) -> None:
        self._ready = False
        try:
            receipt = self._worker.read_full_position_snapshot_receipt()
            snapshot = self._producer.build_source_snapshot(receipt)
            revision = snapshot["account_revision"]
            current = self._gate.gross_exposure_snapshot(self._scope)
            installed_revision = current.get("baseline_revision")
            if installed_revision is not None and (
                type(installed_revision) is not int or installed_revision > revision
            ):
                raise RiskPositionSnapshotProducerError("POSITION_BASELINE_REVISION_REGRESSION")

            with self._journal.writer_fence(self._scope) as fence:
                if installed_revision == revision:
                    if not self._journal.has_matching_gateway_position_baseline(
                        fence, source_snapshot=snapshot
                    ):
                        raise RiskPositionSnapshotProducerError(
                            "POSITION_BASELINE_PRIOR_SOURCE_MISMATCH"
                        )
                    self._ready = True
                    return

                if self._journal.has_matching_gateway_position_baseline(
                    fence, source_snapshot=snapshot
                ):
                    # The two durable stores do not share a transaction. If a
                    # prior process committed the journal row but not the Risk
                    # baseline, do not manufacture a new provider revision to
                    # make that partial commit appear recoverable.
                    raise RiskPositionSnapshotProducerError(
                        "POSITION_BASELINE_GATE_RECONCILIATION_REQUIRED"
                    )
                proof = self._journal.ingest_gross_exposure_baseline(
                    fence,
                    source_snapshot=snapshot,
                    position_source_receipt=receipt,
                )
                self._gate.install_gross_exposure_baseline(proof)
            self._ready = True
        except RiskPositionSnapshotProducerError:
            self._ready = False
            raise
        except Exception as exc:
            self._ready = False
            code = getattr(exc, "code", None)
            if type(code) is str and _SOURCE_REFERENCE.fullmatch(code):
                raise RiskPositionSnapshotProducerError(code) from None
            raise RiskPositionSnapshotProducerError(
                "RISK_POSITION_BASELINE_SYNC_FAILED"
            ) from None


def build_risk_position_snapshot_owner(
    *,
    worker: GatewayAccountSessionWorker,
    journal: RiskOrderJournal,
    gate: DurableRiskGate,
    targets: Mapping[object, object],
    notional_unit: str,
    valuation_resolver: object,
) -> RiskPositionSnapshotOwner:
    """Compose from trusted target metadata; clients cannot supply these maps."""

    try:
        from .service_risk_order_entry import RiskOrderTarget

        from bt_api_risk import InstrumentRiskRegistry
    except Exception:
        raise RiskPositionSnapshotProducerError("RISK_POSITION_OWNER_DEPENDENCY_UNAVAILABLE") from None
    if not isinstance(targets, Mapping) or not targets:
        raise RiskPositionSnapshotProducerError("RISK_POSITION_TARGETS_INVALID")
    if type(valuation_resolver) is not RiskPositionValuationResolver:
        raise RiskPositionSnapshotProducerError("POSITION_VALUATION_RESOLVER_REQUIRED")
    account_scopes: set[AccountScope] = set()
    metadata_by_instrument: dict[str, InstrumentRiskMetadata] = {}
    resource_by_instrument: dict[str, AccountResourceKey] = {}
    strategies_by_instrument: dict[str, set[str]] = defaultdict(set)
    for wire_scope, target in targets.items():
        if type(target) is not RiskOrderTarget:
            raise RiskPositionSnapshotProducerError("RISK_POSITION_TARGETS_INVALID")
        account_scope = target.account_scope
        registry = target.registry
        if (
            type(account_scope) is not AccountScope
            or type(registry) is not InstrumentRiskRegistry
            or type(target.strategy_id) is not str
            or not target.strategy_id
            or type(registry.instruments) is not frozenset
            or not callable(getattr(registry, "get", None))
            or not isinstance(target.resource_by_instrument, Mapping)
        ):
            raise RiskPositionSnapshotProducerError("RISK_POSITION_TARGETS_INVALID")
        account_scopes.add(account_scope)
        for instrument in registry.instruments:
            metadata = registry.get(instrument)
            if type(instrument) is not str or type(metadata) is not InstrumentRiskMetadata:
                raise RiskPositionSnapshotProducerError("RISK_POSITION_TARGETS_INVALID")
            prior = metadata_by_instrument.get(instrument)
            if prior is not None and prior != metadata:
                raise RiskPositionSnapshotProducerError("RISK_POSITION_METADATA_CONFLICT")
            metadata_by_instrument[instrument] = metadata
        for instrument, resource in target.resource_by_instrument.items():
            if instrument not in registry.instruments or type(resource) is not AccountResourceKey:
                raise RiskPositionSnapshotProducerError("RISK_POSITION_RESOURCE_MAP_INVALID")
            prior_resource = resource_by_instrument.get(instrument)
            if prior_resource is not None and prior_resource != resource:
                raise RiskPositionSnapshotProducerError("RISK_POSITION_RESOURCE_CONFLICT")
            resource_by_instrument[instrument] = resource
            strategies_by_instrument[instrument].add(target.strategy_id)
    if len(account_scopes) != 1:
        raise RiskPositionSnapshotProducerError("RISK_POSITION_ACCOUNT_SCOPE_AMBIGUOUS")
    account_scope = next(iter(account_scopes))
    if (
        set(metadata_by_instrument) != set(resource_by_instrument)
        or set(metadata_by_instrument) != set(strategies_by_instrument)
        or set(metadata_by_instrument)
        != set(valuation_resolver.expected_instruments_by_instrument)
    ):
        raise RiskPositionSnapshotProducerError("RISK_POSITION_RESOURCE_MAP_INCOMPLETE")
    account_key = worker.account_key
    expected_account_key = valuation_resolver.expected_account_key
    if (
        type(account_key) is not AccountKey
        or account_key != expected_account_key
        or expected_account_key.provider != account_scope.provider
        or expected_account_key.environment != account_scope.environment
        or expected_account_key.account_id != account_scope.account_id
        or expected_account_key.subaccount_id is not None
        or any(
            target.provider_account_id != account_key.account_id
            for target in targets.values()
        )
    ):
        raise RiskPositionSnapshotProducerError("ACCOUNT_OWNER_SCOPE_MISMATCH")
    for instrument, instrument_key in (
        valuation_resolver.expected_instruments_by_instrument.items()
    ):
        resource = resource_by_instrument[instrument]
        if (
            type(instrument_key) is not MarketInstrumentKey
            or instrument_key.instrument_id != instrument
            or instrument_key.provider != expected_account_key.provider
            or instrument_key.venue != expected_account_key.venue
            or instrument_key.environment != expected_account_key.environment
            or instrument_key.product_type != resource.product_type
        ):
            raise RiskPositionSnapshotProducerError("POSITION_SOURCE_BINDINGS_INVALID")
    producer = RiskPositionSnapshotProducer(
        worker,
        journal,
        scope=account_scope,
        expected_account_key=expected_account_key,
        notional_unit=notional_unit,
        valuation_resolver=valuation_resolver,
        expected_instruments_by_instrument=(
            valuation_resolver.expected_instruments_by_instrument
        ),
        metadata_by_instrument=metadata_by_instrument,
        resource_by_instrument=resource_by_instrument,
        strategies_by_instrument={
            instrument: frozenset(strategies)
            for instrument, strategies in strategies_by_instrument.items()
        },
    )
    return RiskPositionSnapshotOwner(worker, journal, gate, producer)


def validate_service_options(options: Mapping[str, object], bind_ports: tuple[str, ...]):
    """Validate the Risk owner options plus its non-wire Gateway dependency."""

    from .service_risk_order_entry import validate_service_options as validate_risk_options

    if not isinstance(options, Mapping) or type(bind_ports) is not tuple:
        raise ValueError("invalid Risk owner options")
    has_internal_port = "service.provider_session" in bind_ports
    provider_ref = options.get("provider_session_ref")
    if has_internal_port != (provider_ref is not None):
        raise ValueError("Gateway dependency and internal binding must be declared together")
    public_bind_ports = tuple(
        port for port in bind_ports if port != "service.provider_session"
    )
    base_options = dict(options)
    base_options.pop("provider_session_ref", None)
    normalized = validate_risk_options(base_options, public_bind_ports)
    if provider_ref is None:
        return normalized
    if type(provider_ref) is not str or _SOURCE_REFERENCE.fullmatch(provider_ref) is None:
        raise ValueError("invalid Gateway dependency reference")
    result = dict(normalized)
    result["provider_session_ref"] = provider_ref
    return MappingProxyType(result)

