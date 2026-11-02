"""SDK facade for provider-neutral futures catalog and quote snapshots.

The facade composes one selected :class:`QueryFacade` with the base futures
DTOs. It preserves the exact receipt and family snapshots returned by that
query lifecycle, checks cross-family scope and identity, and delegates lease
issue/revalidation to the same provider. It does not adapt CTP/native rows.

Query completeness and market-data freshness are separate claims. This SDK
facade can establish request/source/generation/snapshot scope and row coverage;
it cannot authenticate an event clock or calibration from provider event
date/time components, so ``freshness_status`` remains unavailable.
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from threading import RLock
from types import MappingProxyType
from typing import Any
from weakref import WeakValueDictionary

from bt_api_base.futures_market_protocol import (
    FUTURES_CATALOG_FAMILY,
    FUTURES_DEPTH_FAMILY,
    FUTURES_PRODUCT_UNIVERSE_FAMILY,
    FUTURES_QUOTES_FAMILY,
    FUTURES_SESSION_FAMILY,
    FuturesCatalogDisposition,
    FuturesCatalogRecord,
    FuturesContractKind,
    FuturesDepthRecord,
    FuturesDepthUnitMismatch,
    FuturesFamilyCoverage,
    FuturesFreshnessStatus,
    FuturesMarketSnapshotCoverage,
    FuturesMarketSnapshotRequest,
    FuturesMissingQuoteFields,
    FuturesProductKey,
    FuturesQuantityUnit,
    FuturesQuoteRecord,
    FuturesQuoteSemanticMismatch,
    FuturesQuoteUnitMismatch,
    FuturesSessionRecord,
    FuturesSessionStatus,
    FuturesVolumeSemantics,
)
from bt_api_base.market_protocol import MarketInstrumentKey
from bt_api_base.query_protocol import (
    QueryBatchReceipt,
    QueryCompleteness,
    QueryRequest,
    QuerySnapshot,
    QuerySnapshotLease,
)

from .query_facade import QueryFacade, QueryFacadeError


_FRESHNESS_BLOCKERS = (
    "MARKET_EVENT_CLOCK_NOT_AUTHENTICATED",
    "CLOCK_CALIBRATION_UNAVAILABLE",
)


class FuturesMarketSnapshotError(RuntimeError):
    """Finite failures at the neutral futures snapshot facade boundary."""

    def __init__(self, code: str) -> None:
        self.code = code
        super().__init__(code)


@dataclass(frozen=True)
class FuturesMarketSnapshot:
    """Immutable composite retaining the exact query receipt and row snapshots.

    Instances returned by ``FuturesMarketSnapshotFacade.refresh`` are bound
    to that facade instance. An application-created DTO is not accepted by its
    lease methods as proof of provider currentness.
    """

    request: FuturesMarketSnapshotRequest
    receipt: QueryBatchReceipt
    session_snapshot: QuerySnapshot | None
    catalog_snapshot: QuerySnapshot | None
    product_universe_snapshot: QuerySnapshot | None
    quote_snapshot: QuerySnapshot | None
    depth_snapshot: QuerySnapshot | None
    sessions: tuple[FuturesSessionRecord, ...]
    catalog: tuple[FuturesCatalogRecord, ...]
    products: tuple[FuturesProductKey, ...]
    quotes: tuple[FuturesQuoteRecord, ...]
    depth: tuple[FuturesDepthRecord, ...]
    trading_days: Mapping[str, date]
    coverage: FuturesMarketSnapshotCoverage

    def __post_init__(self) -> None:
        if type(self.request) is not FuturesMarketSnapshotRequest:
            raise TypeError("request must be a FuturesMarketSnapshotRequest")
        if type(self.receipt) is not QueryBatchReceipt:
            raise TypeError("receipt must be a QueryBatchReceipt")
        for field_name in (
            "session_snapshot",
            "catalog_snapshot",
            "product_universe_snapshot",
            "quote_snapshot",
            "depth_snapshot",
        ):
            snapshot = getattr(self, field_name)
            if snapshot is not None and type(snapshot) is not QuerySnapshot:
                raise TypeError(f"{field_name} must be a QuerySnapshot or None")
        for field_name, item_type in (
            ("sessions", FuturesSessionRecord),
            ("catalog", FuturesCatalogRecord),
            ("products", FuturesProductKey),
            ("quotes", FuturesQuoteRecord),
            ("depth", FuturesDepthRecord),
        ):
            values = getattr(self, field_name)
            if type(values) not in (tuple, list) or any(type(item) is not item_type for item in values):
                raise TypeError(f"{field_name} contains an invalid record type")
            object.__setattr__(self, field_name, tuple(values))
        if type(self.coverage) is not FuturesMarketSnapshotCoverage:
            raise TypeError("coverage must be a FuturesMarketSnapshotCoverage")
        if not isinstance(self.trading_days, Mapping):
            raise TypeError("trading_days must be a mapping")
        expected_requests = tuple(
            QueryRequest(
                request_ref=self.request.family_request_refs[family],
                family=family,
                filters=self.request.filters_for(family),
                required=True,
                require_complete_scope=True,
            )
            for family in self.request.family_request_refs
        )
        if self.receipt.requests != expected_requests:
            raise ValueError("receipt requests do not match the futures snapshot request")
        if not self.request.include_depth and self.depth_snapshot is not None:
            raise ValueError("depth snapshot is present when depth was not requested")
        receipt_by_ref = {
            snapshot.request.request_ref: snapshot for snapshot in self.receipt.snapshots
        }
        expected_family_snapshots = {
            FUTURES_SESSION_FAMILY: self.session_snapshot,
            FUTURES_CATALOG_FAMILY: self.catalog_snapshot,
            FUTURES_QUOTES_FAMILY: self.quote_snapshot,
        }
        if self.request.require_product_universe:
            expected_family_snapshots[FUTURES_PRODUCT_UNIVERSE_FAMILY] = (
                self.product_universe_snapshot
            )
        elif self.product_universe_snapshot is not None or self.products:
            raise ValueError("product universe data is present when it was not requested")
        if self.request.include_depth:
            expected_family_snapshots[FUTURES_DEPTH_FAMILY] = self.depth_snapshot
        for family, snapshot in expected_family_snapshots.items():
            ref = self.request.family_request_refs[family]
            if snapshot is not receipt_by_ref.get(ref):
                raise ValueError("family snapshot is not the exact receipt snapshot")
            if snapshot is not None:
                expected_query = next(item for item in expected_requests if item.family == family)
                if snapshot.request != expected_query:
                    raise ValueError("family snapshot request does not match the futures request")
        if (
            self.coverage.request != self.request
            or self.coverage.batch_ref != self.receipt.batch_ref
            or self.coverage.source_ref != self.receipt.source_ref
            or self.coverage.generation != self.receipt.generation
        ):
            raise ValueError("coverage is not bound to the exact request receipt")
        expected_rows = {
            "sessions": _project_rows(self.session_snapshot, FuturesSessionRecord),
            "catalog": _project_rows(self.catalog_snapshot, FuturesCatalogRecord),
            "products": _project_rows(
                self.product_universe_snapshot,
                FuturesProductKey,
            ),
            "quotes": _project_rows(self.quote_snapshot, FuturesQuoteRecord),
            "depth": _project_rows(self.depth_snapshot, FuturesDepthRecord),
        }
        for field_name, projected in expected_rows.items():
            if getattr(self, field_name) != projected:
                raise ValueError(f"{field_name} do not match the exact receipt records")
        rebuilt_coverage = _build_coverage(
            request=self.request,
            receipt=self.receipt,
            snapshots={
                family: receipt_by_ref.get(ref)
                for family, ref in self.request.family_request_refs.items()
            },
            sessions=self.sessions,
            catalog=self.catalog,
            products=self.products,
            quotes=self.quotes,
            depth=self.depth,
        )
        if self.coverage != rebuilt_coverage:
            raise ValueError("coverage does not match the exact receipt projection")
        days = dict(self.trading_days)
        if any(type(venue) is not str or type(day) is not date for venue, day in days.items()):
            raise TypeError("trading_days must map venue strings to datetime.date values")
        session_days = _trading_days_for(
            self.request,
            self.sessions,
            self.catalog,
            self.quotes,
            self.depth,
        )
        if days != session_days:
            raise ValueError("trading_days must match the projected session rows")
        object.__setattr__(self, "trading_days", MappingProxyType(days))

    @property
    def session(self) -> FuturesSessionRecord | None:
        """Return the sole session row for a one-venue scope, otherwise None."""
        return self.sessions[0] if len(self.sessions) == 1 else None

    @property
    def route_trading_day(self) -> date | None:
        """Return the route-wide session day when the provider supplies one."""
        route_rows = tuple(row for row in self.sessions if row.venue is None)
        return route_rows[0].trading_day if len(route_rows) == 1 else None

    def trading_day_for(self, venue: str) -> date | None:
        """Return a day only when this composite's declared scope covers venue."""
        if type(venue) is not str or not venue:
            raise ValueError("venue must be a non-empty string")
        return self.trading_days.get(venue)

    @property
    def family_snapshots(self) -> Mapping[str, QuerySnapshot | None]:
        values: dict[str, QuerySnapshot | None] = {
            FUTURES_SESSION_FAMILY: self.session_snapshot,
            FUTURES_CATALOG_FAMILY: self.catalog_snapshot,
        }
        if self.request.require_product_universe:
            values[FUTURES_PRODUCT_UNIVERSE_FAMILY] = self.product_universe_snapshot
        values[FUTURES_QUOTES_FAMILY] = self.quote_snapshot
        if self.request.include_depth:
            values[FUTURES_DEPTH_FAMILY] = self.depth_snapshot
        return MappingProxyType(values)

    @property
    def request_refs(self) -> Mapping[str, str]:
        return self.request.family_request_refs

    @property
    def source_ref(self) -> str | None:
        return self.receipt.source_ref

    @property
    def batch_ref(self) -> str:
        return self.receipt.batch_ref

    @property
    def generation(self) -> str | None:
        return self.receipt.generation

    @property
    def product_universe_complete(self) -> bool | None:
        return self.coverage.product_universe_complete

    @property
    def requested_at(self) -> datetime:
        """Provider batch start time, not a market event timestamp."""
        return self.receipt.started_at

    @property
    def started_at(self) -> datetime:
        return self.receipt.started_at

    @property
    def completed_at(self) -> datetime:
        return self.receipt.completed_at

    @property
    def observed_at(self) -> datetime | None:
        snapshots = tuple(item for item in self.family_snapshots.values() if item is not None)
        requested_count = len(self.family_snapshots)
        if len(snapshots) != requested_count or any(item.observed_at is None for item in snapshots):
            return None
        return max(item.observed_at for item in snapshots if item.observed_at is not None)

    @property
    def records_digest(self) -> str:
        """Digest binding the ordered per-family record digests in this request."""
        payload = [
            [family, snapshot.records_digest if snapshot is not None else None]
            for family, snapshot in self.family_snapshots.items()
        ]
        encoded = json.dumps(payload, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
        return hashlib.sha256(encoded).hexdigest()

    @property
    def complete(self) -> bool:
        """Whether provider family scopes and cross-family rows are complete.

        This does not establish market-event freshness, clock calibration, or
        suitability for a freshness-sensitive live feed.
        """
        return self.receipt.complete and self.coverage.complete

    @property
    def completeness(self) -> QueryCompleteness:
        """Aggregate family availability, without promoting missing evidence."""
        if self.complete:
            return QueryCompleteness.COMPLETE
        states = tuple(item.completeness for item in self._family_coverages())
        if QueryCompleteness.UNAVAILABLE in states:
            return QueryCompleteness.UNAVAILABLE
        if QueryCompleteness.UNKNOWN in states:
            return QueryCompleteness.UNKNOWN
        return QueryCompleteness.PARTIAL

    @property
    def blockers(self) -> tuple[str, ...]:
        return self.coverage.blockers

    @property
    def freshness_status(self) -> FuturesFreshnessStatus:
        return FuturesFreshnessStatus.UNAVAILABLE

    @property
    def freshness_blockers(self) -> tuple[str, ...]:
        return _FRESHNESS_BLOCKERS

    def _family_coverages(self) -> tuple[FuturesFamilyCoverage, ...]:
        families = (
            self.coverage.session,
            self.coverage.catalog,
            self.coverage.quotes,
        )
        if self.coverage.product_universe is not None:
            families += (self.coverage.product_universe,)
        if self.coverage.depth is not None:
            families += (self.coverage.depth,)
        return families


class FuturesMarketSnapshotFacade:
    """Query and validate futures family snapshots through one QueryFacade."""

    def __init__(self, query_facade: QueryFacade) -> None:
        if type(query_facade) is not QueryFacade:
            raise FuturesMarketSnapshotError("FUTURES_MARKET_QUERY_FACADE_INVALID")
        self._query_facade = query_facade
        self._issued_snapshots: WeakValueDictionary[int, FuturesMarketSnapshot] = (
            WeakValueDictionary()
        )
        self._issued_lock = RLock()

    def refresh(
        self,
        request: FuturesMarketSnapshotRequest,
        *,
        deadline: datetime,
    ) -> FuturesMarketSnapshot:
        """Run one ordered session/catalog/quote batch and project its facts."""
        if type(request) is not FuturesMarketSnapshotRequest:
            raise FuturesMarketSnapshotError("FUTURES_MARKET_REQUEST_INVALID")
        _validate_utc(deadline, "deadline")
        try:
            query_requests = tuple(
                QueryRequest(
                    request_ref=request.family_request_refs[family],
                    family=family,
                    filters=request.filters_for(family),
                    required=True,
                    require_complete_scope=True,
                )
                for family in request.family_request_refs
            )
        except Exception:
            raise FuturesMarketSnapshotError("FUTURES_MARKET_REQUEST_INVALID") from None
        try:
            receipt = self._query_facade.refresh(query_requests, deadline=deadline)
        except QueryFacadeError:
            raise FuturesMarketSnapshotError("FUTURES_MARKET_QUERY_FAILED") from None
        except Exception:
            raise FuturesMarketSnapshotError("FUTURES_MARKET_QUERY_FAILED") from None
        if type(receipt) is not QueryBatchReceipt or receipt.requests != query_requests:
            raise FuturesMarketSnapshotError("FUTURES_MARKET_RECEIPT_INVALID")

        by_ref = {snapshot.request.request_ref: snapshot for snapshot in receipt.snapshots}
        snapshots = {
            family: by_ref.get(request.family_request_refs[family])
            for family in request.family_request_refs
        }
        try:
            sessions = _project_rows(snapshots[FUTURES_SESSION_FAMILY], FuturesSessionRecord)
            catalog = _project_rows(snapshots[FUTURES_CATALOG_FAMILY], FuturesCatalogRecord)
            products = (
                _project_rows(
                    snapshots[FUTURES_PRODUCT_UNIVERSE_FAMILY],
                    FuturesProductKey,
                )
                if request.require_product_universe
                else ()
            )
            quotes = _project_rows(snapshots[FUTURES_QUOTES_FAMILY], FuturesQuoteRecord)
            depth = (
                _project_rows(snapshots[FUTURES_DEPTH_FAMILY], FuturesDepthRecord)
                if request.include_depth
                else ()
            )
        except Exception:
            raise FuturesMarketSnapshotError("FUTURES_MARKET_RECORD_INVALID") from None

        coverage = _build_coverage(
            request=request,
            receipt=receipt,
            snapshots=snapshots,
            sessions=sessions,
            catalog=catalog,
            products=products,
            quotes=quotes,
            depth=depth,
        )
        trading_days = _trading_days_for(request, sessions, catalog, quotes, depth)
        snapshot = FuturesMarketSnapshot(
            request=request,
            receipt=receipt,
            session_snapshot=snapshots[FUTURES_SESSION_FAMILY],
            catalog_snapshot=snapshots[FUTURES_CATALOG_FAMILY],
            product_universe_snapshot=snapshots.get(FUTURES_PRODUCT_UNIVERSE_FAMILY),
            quote_snapshot=snapshots[FUTURES_QUOTES_FAMILY],
            depth_snapshot=snapshots.get(FUTURES_DEPTH_FAMILY),
            sessions=sessions,
            catalog=catalog,
            products=products,
            quotes=quotes,
            depth=depth,
            trading_days=trading_days,
            coverage=coverage,
        )
        with self._issued_lock:
            self._issued_snapshots[id(snapshot)] = snapshot
        return snapshot

    def acquire_lease(
        self,
        snapshot: FuturesMarketSnapshot,
        *,
        purpose: str,
        expires_at: datetime,
    ) -> QuerySnapshotLease:
        """Issue a provider lease only for this facade's complete snapshot."""
        self._require_issued_snapshot(snapshot)
        if not snapshot.complete:
            raise FuturesMarketSnapshotError("FUTURES_MARKET_LEASE_UNAVAILABLE")
        try:
            return self._query_facade.acquire_snapshot_lease(
                snapshot.receipt,
                purpose=purpose,
                expires_at=expires_at,
            )
        except Exception:
            raise FuturesMarketSnapshotError("FUTURES_MARKET_LEASE_UNAVAILABLE") from None

    def validate_lease(
        self,
        snapshot: FuturesMarketSnapshot,
        lease: QuerySnapshotLease,
        *,
        now: datetime,
    ) -> bool:
        """Revalidate currentness through the exact provider receipt lease."""
        self._require_issued_snapshot(snapshot)
        if type(lease) is not QuerySnapshotLease:
            raise FuturesMarketSnapshotError("FUTURES_MARKET_LEASE_INVALID")
        _validate_utc(now, "now")
        if not snapshot.complete:
            return False
        try:
            return self._query_facade.validate_lease(lease, snapshot.receipt, now=now)
        except Exception:
            return False

    def _require_issued_snapshot(self, snapshot: FuturesMarketSnapshot) -> None:
        if type(snapshot) is not FuturesMarketSnapshot:
            raise FuturesMarketSnapshotError("FUTURES_MARKET_SNAPSHOT_INVALID")
        with self._issued_lock:
            if self._issued_snapshots.get(id(snapshot)) is not snapshot:
                raise FuturesMarketSnapshotError("FUTURES_MARKET_SNAPSHOT_NOT_ISSUED_HERE")


def _project_rows(snapshot: QuerySnapshot | None, row_type: type) -> tuple[Any, ...]:
    if snapshot is None:
        return ()
    if type(snapshot) is not QuerySnapshot:
        raise TypeError("query family result is invalid")
    projector = getattr(row_type, "from_dict", None)
    if not callable(projector):
        raise TypeError("row type has no neutral parser")
    return tuple(projector(row) for row in snapshot.records)


def _build_coverage(
    *,
    request: FuturesMarketSnapshotRequest,
    receipt: QueryBatchReceipt,
    snapshots: Mapping[str, QuerySnapshot | None],
    sessions: tuple[FuturesSessionRecord, ...],
    catalog: tuple[FuturesCatalogRecord, ...],
    products: tuple[FuturesProductKey, ...],
    quotes: tuple[FuturesQuoteRecord, ...],
    depth: tuple[FuturesDepthRecord, ...],
) -> FuturesMarketSnapshotCoverage:
    families = {
        family: _family_coverage(
            family=family,
            request_ref=request.family_request_refs[family],
            batch_ref=receipt.batch_ref,
            filters=request.filters_for(family),
            snapshot=snapshots.get(family),
        )
        for family in request.family_request_refs
    }
    blockers: list[str] = []
    for family, info in families.items():
        if not info.complete:
            blockers.append(f"{family.upper()}_SCOPE_INCOMPLETE")
            blockers.extend(info.blockers)
    if receipt.blockers:
        blockers.extend(receipt.blockers)

    missing_products: list[FuturesProductKey] = []
    unexpected_products: list[FuturesProductKey] = []
    requested_product_identities = (
        {(item.venue, item.product_id) for item in request.products}
        if request.products is not None
        else None
    )
    observed_product_identities = {(item.venue, item.product_id) for item in products}
    if request.require_product_universe and requested_product_identities is not None:
        missing_ids = requested_product_identities - observed_product_identities
        unexpected_ids = observed_product_identities - requested_product_identities
        missing_products.extend(
            item for item in request.products or () if (item.venue, item.product_id) in missing_ids
        )
        unexpected_products.extend(
            item
            for item in products
            if (item.venue, item.product_id) in unexpected_ids
        )
        if missing_products:
            blockers.append("FUTURES_PRODUCT_UNIVERSE_KEYS_MISSING")
        if unexpected_products:
            blockers.append("FUTURES_PRODUCT_UNIVERSE_KEYS_OUTSIDE_FILTER")

    allowed_venues = set(request.venues) if request.venues is not None else None
    sessions_by_venue: dict[str, FuturesSessionRecord] = {}
    route_session: FuturesSessionRecord | None = None
    for row in sessions:
        if row.environment != request.environment:
            blockers.append("SESSION_ENVIRONMENT_MISMATCH")
        if row.venue is not None and allowed_venues is not None and row.venue not in allowed_venues:
            blockers.append("SESSION_VENUE_OUTSIDE_SCOPE")
        if row.venue is None:
            if route_session is not None:
                blockers.append("DUPLICATE_ROUTE_SESSION")
                continue
            route_session = row
        else:
            if row.venue in sessions_by_venue:
                blockers.append("DUPLICATE_SESSION_VENUE")
                continue
            sessions_by_venue[row.venue] = row
        if request.require_active_session and row.session_status is FuturesSessionStatus.UNKNOWN:
            blockers.append("SESSION_STATUS_UNAVAILABLE")
        if request.require_active_session and row.session_status is FuturesSessionStatus.CLOSED:
            blockers.append("SESSION_NOT_ACTIVE")
        if request.trading_day is not None and row.trading_day != request.trading_day:
            blockers.append("SESSION_TRADING_DAY_MISMATCH")
    if route_session is not None and sessions_by_venue:
        blockers.append("SESSION_SCOPE_MIXED_ROUTE_AND_VENUE")
    if request.venues is not None and route_session is None:
        missing_sessions = set(request.venues) - set(sessions_by_venue)
        if missing_sessions:
            blockers.append("SESSION_VENUE_COVERAGE_MISSING")
    elif request.venues is None and route_session is None and not sessions_by_venue:
        blockers.append("SESSION_SCOPE_EMPTY_OR_UNAVAILABLE")

    providers: set[str] = set()
    expected: dict[tuple[str, ...], MarketInstrumentKey] = {}
    catalog_identities: set[tuple[str, ...]] = set()
    requested_product_keys = (
        {(item.venue, item.product_id) for item in request.products}
        if request.products is not None
        else None
    )
    instrument_keys = (
        {_instrument_identity(item) for item in request.instruments}
        if request.instruments is not None
        else None
    )
    requested_kinds = set(request.contract_kinds)
    unclassified: dict[tuple[str, ...], MarketInstrumentKey] = {}
    product_universe_complete = request.require_product_universe and families[
        FUTURES_PRODUCT_UNIVERSE_FAMILY
    ].complete
    for row in catalog:
        instrument = row.instrument
        identity = _instrument_identity(instrument)
        providers.add(instrument.provider)
        if identity in catalog_identities:
            blockers.append("DUPLICATE_CATALOG_INSTRUMENT")
        catalog_identities.add(identity)
        if instrument.environment != request.environment:
            blockers.append("CATALOG_ENVIRONMENT_MISMATCH")
        if allowed_venues is not None and instrument.venue not in allowed_venues:
            blockers.append("CATALOG_VENUE_OUTSIDE_SCOPE")
        if instrument.venue not in sessions_by_venue and route_session is None:
            blockers.append("CATALOG_SESSION_SCOPE_MISSING")
        if row.product.venue != instrument.venue:
            blockers.append("CATALOG_PRODUCT_VENUE_MISMATCH")
        product_identity = (row.product.venue, row.product.product_id)
        if requested_product_keys is not None and product_identity not in requested_product_keys:
            blockers.append("CATALOG_PRODUCT_OUTSIDE_FILTER")
        if product_universe_complete and product_identity not in observed_product_identities:
            blockers.append("CATALOG_PRODUCT_OUTSIDE_PRODUCT_UNIVERSE")
        if instrument_keys is not None and identity not in instrument_keys:
            blockers.append("CATALOG_INSTRUMENT_OUTSIDE_FILTER")
        if row.contract_kind is FuturesContractKind.UNKNOWN:
            unclassified[identity] = instrument
        if row.asset_type.value == "unknown":
            unclassified[identity] = instrument
        if row.disposition is FuturesCatalogDisposition.UNKNOWN:
            unclassified[identity] = instrument
        if row.contract_kind not in requested_kinds:
            if row.disposition is FuturesCatalogDisposition.INCLUDED:
                blockers.append("CATALOG_INCLUDED_KIND_OUTSIDE_FILTER")
            continue
        if row.disposition is not FuturesCatalogDisposition.INCLUDED:
            continue
        if row.is_trading is None or row.expiry_date is None:
            unclassified[identity] = instrument
            continue
        if row.is_trading:
            expected[identity] = instrument

    if len(providers) > 1:
        blockers.append("MULTIPLE_INSTRUMENT_PROVIDERS")
    for row in quotes:
        instrument = row.instrument
        identity = _instrument_identity(instrument)
        providers.add(instrument.provider)
        if instrument.environment != request.environment:
            blockers.append("QUOTE_ENVIRONMENT_MISMATCH")
        if allowed_venues is not None and instrument.venue not in allowed_venues:
            blockers.append("QUOTE_VENUE_OUTSIDE_SCOPE")
        session = sessions_by_venue.get(instrument.venue, route_session)
        if session is None:
            blockers.append("QUOTE_SESSION_SCOPE_MISSING")
        elif row.trading_day != session.trading_day:
            blockers.append("QUOTE_TRADING_DAY_MISMATCH")
        if identity not in expected:
            blockers.append("QUOTE_INSTRUMENT_OUTSIDE_CATALOG_UNIVERSE")
    if len(providers) > 1:
        blockers.append("MULTIPLE_INSTRUMENT_PROVIDERS")

    quote_by_identity: dict[tuple[str, ...], FuturesQuoteRecord] = {}
    for row in quotes:
        identity = _instrument_identity(row.instrument)
        if identity in quote_by_identity:
            blockers.append("DUPLICATE_QUOTE_INSTRUMENT")
            continue
        quote_by_identity[identity] = row
    missing_quotes: list[MarketInstrumentKey] = []
    missing_quote_fields: list[FuturesMissingQuoteFields] = []
    quote_unit_mismatches: list[FuturesQuoteUnitMismatch] = []
    quote_semantic_mismatches: list[FuturesQuoteSemanticMismatch] = []
    for identity, instrument in expected.items():
        quote = quote_by_identity.get(identity)
        if quote is None:
            missing_quotes.append(instrument)
            continue
        absent = tuple(
            field_name
            for field_name in request.required_quote_fields
            if getattr(quote, field_name) is None
        )
        if absent:
            missing_quote_fields.append(FuturesMissingQuoteFields(instrument, absent))
        if (
            "open_interest" in request.required_quote_fields
            and quote.open_interest is not None
            and request.required_open_interest_unit is not None
            and quote.open_interest_unit is not request.required_open_interest_unit
        ):
            quote_unit_mismatches.append(
                FuturesQuoteUnitMismatch(
                    instrument=instrument,
                    field="open_interest",
                    required_unit=request.required_open_interest_unit,
                    actual_unit=quote.open_interest_unit,
                )
            )
        if (
            "volume" in request.required_quote_fields
            and quote.volume is not None
            and request.required_volume_semantics is not None
            and quote.volume_semantics is not request.required_volume_semantics
        ):
            quote_semantic_mismatches.append(
                FuturesQuoteSemanticMismatch(
                    instrument=instrument,
                    field="volume",
                    required_semantics=request.required_volume_semantics,
                    actual_semantics=quote.volume_semantics,
                )
            )
        if (
            set(request.required_quote_fields).intersection({"volume", "bid_volume", "ask_volume"})
            and any(
                getattr(quote, field_name) is not None
                for field_name in ("volume", "bid_volume", "ask_volume")
                if field_name in request.required_quote_fields
            )
            and request.required_volume_unit is not None
            and quote.volume_unit is not request.required_volume_unit
        ):
            quote_unit_mismatches.append(
                FuturesQuoteUnitMismatch(
                    instrument=instrument,
                    field="volume",
                    required_unit=request.required_volume_unit,
                    actual_unit=quote.volume_unit,
                )
            )
    if missing_quotes:
        blockers.append("QUOTE_INSTRUMENT_COVERAGE_MISSING")
    if missing_quote_fields:
        blockers.append("QUOTE_REQUIRED_FIELDS_MISSING")
    if quote_unit_mismatches:
        blockers.append("QUOTE_QUANTITY_UNIT_MISMATCH")
    if quote_semantic_mismatches:
        blockers.append("QUOTE_VOLUME_SEMANTICS_MISMATCH")

    missing_depth: list[MarketInstrumentKey] = []
    depth_unit_mismatches: list[FuturesDepthUnitMismatch] = []
    if request.include_depth:
        depth_by_identity: dict[tuple[str, ...], FuturesDepthRecord] = {}
        for row in depth:
            instrument = row.instrument
            identity = _instrument_identity(instrument)
            providers.add(instrument.provider)
            if instrument.environment != request.environment:
                blockers.append("DEPTH_ENVIRONMENT_MISMATCH")
            session = sessions_by_venue.get(instrument.venue, route_session)
            if session is None:
                blockers.append("DEPTH_SESSION_SCOPE_MISSING")
            elif row.trading_day != session.trading_day:
                blockers.append("DEPTH_TRADING_DAY_MISMATCH")
            if identity not in expected:
                blockers.append("DEPTH_INSTRUMENT_OUTSIDE_CATALOG_UNIVERSE")
            if identity in depth_by_identity:
                blockers.append("DUPLICATE_DEPTH_INSTRUMENT")
                continue
            depth_by_identity[identity] = row
        for identity, instrument in expected.items():
            row = depth_by_identity.get(identity)
            if row is None:
                missing_depth.append(instrument)
                continue
            if not row.complete_state:
                blockers.append("DEPTH_STATE_INCOMPLETE")
            if len(row.bids) < request.depth_levels or len(row.asks) < request.depth_levels:
                blockers.append("DEPTH_LEVEL_COVERAGE_INCOMPLETE")
            if (
                request.required_depth_volume_unit is not None
                and row.volume_unit is not request.required_depth_volume_unit
            ):
                depth_unit_mismatches.append(
                    FuturesDepthUnitMismatch(
                        instrument=instrument,
                        required_unit=request.required_depth_volume_unit,
                        actual_unit=row.volume_unit,
                    )
                )
        if missing_depth:
            blockers.append("DEPTH_INSTRUMENT_COVERAGE_MISSING")
        if depth_unit_mismatches:
            blockers.append("DEPTH_QUANTITY_UNIT_MISMATCH")
    if unclassified:
        blockers.append("CATALOG_CLASSIFICATION_INCOMPLETE")
    if len(providers) > 1:
        blockers.append("MULTIPLE_INSTRUMENT_PROVIDERS")

    # Preserve deterministic order while de-duplicating finite reason codes.
    blockers = list(dict.fromkeys(blockers))
    return FuturesMarketSnapshotCoverage(
        request=request,
        batch_ref=receipt.batch_ref,
        source_ref=receipt.source_ref,
        generation=receipt.generation,
        session=families[FUTURES_SESSION_FAMILY],
        catalog=families[FUTURES_CATALOG_FAMILY],
        quotes=families[FUTURES_QUOTES_FAMILY],
        product_universe=families.get(FUTURES_PRODUCT_UNIVERSE_FAMILY),
        depth=families.get(FUTURES_DEPTH_FAMILY),
        expected_instruments=tuple(expected.values()),
        product_keys=products,
        missing_products=tuple(missing_products),
        unexpected_products=tuple(unexpected_products),
        missing_quote_instruments=tuple(missing_quotes),
        missing_depth_instruments=tuple(missing_depth),
        missing_quote_fields=tuple(missing_quote_fields),
        quote_unit_mismatches=tuple(quote_unit_mismatches),
        quote_semantic_mismatches=tuple(quote_semantic_mismatches),
        depth_unit_mismatches=tuple(depth_unit_mismatches),
        unclassified_instruments=tuple(unclassified.values()),
        blockers=tuple(blockers),
    )


def _family_coverage(
    *,
    family: str,
    request_ref: str,
    batch_ref: str,
    filters: Mapping[str, Any],
    snapshot: QuerySnapshot | None,
) -> FuturesFamilyCoverage:
    if snapshot is None:
        return FuturesFamilyCoverage(
            family=family,
            request_ref=request_ref,
            batch_ref=batch_ref,
            filters=filters,
            completeness=QueryCompleteness.UNKNOWN,
            scope_complete=None,
            source_ref=None,
            generation=None,
            snapshot_ref=None,
            started_at=None,
            completed_at=None,
            observed_at=None,
            records_digest=None,
            record_count=None,
            blockers=("QUERY_FAMILY_RESULT_MISSING",),
        )
    return FuturesFamilyCoverage(
        family=family,
        request_ref=snapshot.request.request_ref,
        batch_ref=batch_ref,
        filters=snapshot.request.filters,
        completeness=snapshot.completeness,
        scope_complete=snapshot.scope_complete,
        source_ref=snapshot.source_ref,
        generation=snapshot.generation,
        snapshot_ref=snapshot.snapshot_ref,
        started_at=snapshot.started_at,
        completed_at=snapshot.completed_at,
        observed_at=snapshot.observed_at,
        records_digest=snapshot.records_digest,
        record_count=snapshot.record_count,
        blockers=snapshot.blockers,
        continuation_ref=snapshot.continuation_ref,
    )


def _trading_days_for(
    request: FuturesMarketSnapshotRequest,
    sessions: tuple[FuturesSessionRecord, ...],
    catalog: tuple[FuturesCatalogRecord, ...],
    quotes: tuple[FuturesQuoteRecord, ...],
    depth: tuple[FuturesDepthRecord, ...],
) -> dict[str, date]:
    route_rows = tuple(row for row in sessions if row.venue is None)
    if len(route_rows) == 1:
        if request.venues is not None:
            venues = set(request.venues)
        else:
            venues = {
                record.instrument.venue
                for record in (*catalog, *quotes, *depth)
            }
        return {venue: route_rows[0].trading_day for venue in venues}
    return {row.venue: row.trading_day for row in sessions if row.venue is not None}


def _instrument_identity(instrument: MarketInstrumentKey) -> tuple[str, ...]:
    return (
        instrument.provider,
        instrument.venue,
        instrument.environment,
        instrument.product_type,
        instrument.instrument_id,
        instrument.native_symbol,
    )


def _validate_utc(value: datetime, field_name: str) -> None:
    if type(value) is not datetime:
        raise FuturesMarketSnapshotError("FUTURES_MARKET_TIME_INVALID")
    if value.tzinfo is None or value.utcoffset() != timedelta(0):
        raise FuturesMarketSnapshotError("FUTURES_MARKET_TIME_INVALID")


__all__ = [
    "FuturesMarketSnapshot",
    "FuturesMarketSnapshotError",
    "FuturesMarketSnapshotFacade",
]
