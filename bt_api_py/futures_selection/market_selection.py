"""Selection from an exact, leased neutral market snapshot."""

from __future__ import annotations

from dataclasses import replace
from datetime import date, datetime, timezone
from decimal import Decimal
from typing import Any, Mapping, Optional, Sequence, Tuple
import json
from .models import (
    ContractAssessment,
    EXCLUSION_REASON_NO_LISTED_CONTRACTS,
    ExchangeTradingCalendar,
    FuturesContractRecord,
    MainContractManifest,
    MainContractMarketCoverage,
    MainContractPolicy,
    ProductKey,
    ProductSelection,
    TradingDayReference,
    _decimal_text,
    _key_order,
    _number_as_decimal,
    _utc_text,
)
from .selection import (
    _calendar_evidence,
    _calendar_for,
    _candidate_assessment,
    _exact_equal_weight_score,
    _source_digest,
    _source_text,
    _source_timestamp,
)



def _futures_market_sdk_types() -> Optional[dict[str, Any]]:
    """Load exact SDK contracts only for the provider-neutral selector."""

    try:
        from bt_api_py.futures_contracts import (
            FuturesAssetType,
            FuturesCatalogDisposition,
            FuturesCatalogRecord,
            FuturesContractKind,
            FuturesFamilyCoverage,
            FuturesMarketSnapshotCoverage,
            FuturesMarketSnapshotRequest,
            FuturesProductKey,
            FuturesQuantityUnit,
            FuturesQuoteRecord,
            FuturesSessionRecord,
            FuturesSessionStatus,
            FuturesVolumeSemantics,
            MarketInstrumentKey,
            QuerySnapshotLease,
        )
        from bt_api_py.futures_market_facade import (
            FuturesMarketSnapshot,
            FuturesMarketSnapshotFacade,
        )
    except ImportError:
        return None
    return {
        "FuturesAssetType": FuturesAssetType,
        "FuturesCatalogDisposition": FuturesCatalogDisposition,
        "FuturesCatalogRecord": FuturesCatalogRecord,
        "FuturesContractKind": FuturesContractKind,
        "FuturesFamilyCoverage": FuturesFamilyCoverage,
        "FuturesMarketSnapshotCoverage": FuturesMarketSnapshotCoverage,
        "FuturesMarketSnapshotRequest": FuturesMarketSnapshotRequest,
        "FuturesMarketSnapshot": FuturesMarketSnapshot,
        "FuturesMarketSnapshotFacade": FuturesMarketSnapshotFacade,
        "FuturesProductKey": FuturesProductKey,
        "FuturesQuantityUnit": FuturesQuantityUnit,
        "FuturesQuoteRecord": FuturesQuoteRecord,
        "FuturesSessionRecord": FuturesSessionRecord,
        "FuturesSessionStatus": FuturesSessionStatus,
        "FuturesVolumeSemantics": FuturesVolumeSemantics,
        "MarketInstrumentKey": MarketInstrumentKey,
        "QuerySnapshotLease": QuerySnapshotLease,
    }


def _market_snapshot_json(snapshot: Any, sdk: Mapping[str, Any]) -> Optional[str]:
    """Project finite neutral receipt facts without retaining SDK rows."""

    if type(snapshot) is not sdk["FuturesMarketSnapshot"]:
        return None
    coverage = getattr(snapshot, "coverage", None)
    if type(coverage) is not sdk["FuturesMarketSnapshotCoverage"]:
        return None

    def time_text(value: Any) -> Optional[str]:
        normalized = _source_timestamp(value)
        return _utc_text(normalized) if normalized is not None else None

    def family_projection(value: Any) -> Optional[dict[str, Any]]:
        if type(value) is not sdk["FuturesFamilyCoverage"]:
            return None
        completeness = getattr(value.completeness, "value", None)
        return {
            "family": _source_text(value.family),
            "request_ref": _source_text(value.request_ref),
            "source_ref": _source_text(value.source_ref),
            "generation": _source_text(value.generation),
            "snapshot_ref": _source_text(value.snapshot_ref),
            "completeness": _source_text(completeness),
            "scope_complete": value.scope_complete if type(value.scope_complete) is bool else None,
            "complete": value.complete if type(value.complete) is bool else None,
            "started_at": time_text(value.started_at),
            "completed_at": time_text(value.completed_at),
            "observed_at": time_text(value.observed_at),
            "records_digest": _source_digest(value.records_digest),
            "record_count": value.record_count
            if type(value.record_count) is int and value.record_count >= 0
            else None,
            "blockers": [item for item in value.blockers if type(item) is str],
            "continuation_present": value.continuation_ref is not None,
        }

    sessions = []
    for row in getattr(snapshot, "sessions", ()):
        if type(row) is not sdk["FuturesSessionRecord"]:
            continue
        sessions.append(
            {
                "venue": _source_text(row.venue),
                "environment": _source_text(row.environment),
                "trading_day": row.trading_day.isoformat()
                if type(row.trading_day) is date
                else None,
                "session_status": _source_text(getattr(row.session_status, "value", None)),
            }
        )
    projection = {
        "source_ref": _source_text(snapshot.source_ref),
        "generation": _source_text(snapshot.generation),
        "records_digest": _source_digest(snapshot.records_digest),
        "request_refs": [
            item for item in snapshot.request_refs if type(item) is str and item
        ],
        "requested_at": time_text(snapshot.requested_at),
        "completed_at": time_text(snapshot.completed_at),
        "observed_at": time_text(snapshot.observed_at),
        "complete": snapshot.complete if type(snapshot.complete) is bool else None,
        "freshness_status": _source_text(
            getattr(snapshot.freshness_status, "value", snapshot.freshness_status)
        ),
        "freshness_blockers": [
            item for item in snapshot.freshness_blockers if type(item) is str
        ],
        "coverage": {
            name: family_projection(getattr(coverage, name, None))
            for name in ("session", "catalog", "quotes", "depth")
        },
        "coverage_scope_complete": coverage.scope_complete
        if type(coverage.scope_complete) is bool
        else None,
        "coverage_complete": coverage.complete if type(coverage.complete) is bool else None,
        "expected_instrument_count": len(coverage.expected_instruments),
        "missing_quote_instrument_count": len(coverage.missing_quote_instruments),
        "missing_depth_instrument_count": len(coverage.missing_depth_instruments),
        "missing_quote_field_row_count": len(coverage.missing_quote_fields),
        "unclassified_instrument_count": len(coverage.unclassified_instruments),
        "quote_unit_mismatch_count": len(getattr(coverage, "quote_unit_mismatches", ())),
        "quote_semantic_mismatch_count": len(
            getattr(coverage, "quote_semantic_mismatches", ())
        ),
        "depth_unit_mismatch_count": len(
            getattr(coverage, "depth_unit_mismatches", ())
        ),
        "sessions": sessions,
        "blockers": [item for item in coverage.blockers if type(item) is str],
    }
    return json.dumps(projection, sort_keys=True, separators=(",", ":"))


def _market_snapshot_source_fields(snapshot: Any, sdk: Mapping[str, Any]) -> dict[str, Any]:
    if type(snapshot) is not sdk["FuturesMarketSnapshot"]:
        return {
            "source_id": None,
            "source_snapshot_id": None,
            "source_sha256": None,
            "source_available_at": None,
            "source_retrieved_at": None,
            "catalog_source_id": None,
            "catalog_snapshot_id": None,
            "catalog_sha256": None,
            "catalog_available_at": None,
            "catalog_retrieved_at": None,
        }
    coverage = snapshot.coverage
    catalog = getattr(coverage, "catalog", None)
    quotes = getattr(coverage, "quotes", None)
    return {
        "source_id": _source_text(snapshot.source_ref),
        "source_snapshot_id": _source_text(getattr(quotes, "snapshot_ref", None)),
        "source_sha256": _source_digest(snapshot.records_digest),
        "source_available_at": _source_timestamp(snapshot.completed_at),
        "source_retrieved_at": _source_timestamp(snapshot.observed_at),
        "catalog_source_id": _source_text(getattr(catalog, "source_ref", None)),
        "catalog_snapshot_id": _source_text(getattr(catalog, "snapshot_ref", None)),
        "catalog_sha256": _source_digest(getattr(catalog, "records_digest", None)),
        "catalog_available_at": _source_timestamp(getattr(catalog, "completed_at", None)),
        "catalog_retrieved_at": _source_timestamp(getattr(catalog, "observed_at", None)),
    }


def _neutral_partial_manifest(
    snapshot: Any,
    expected_products: Sequence[ProductKey],
    *,
    decision_at: datetime,
    policy: MainContractPolicy,
    blockers: Sequence[str],
    sdk: Optional[Mapping[str, Any]],
) -> MainContractManifest:
    targets = tuple(sorted(expected_products, key=_key_order))
    blocker_rows = tuple(dict.fromkeys(blockers or ("MARKET_SNAPSHOT_COVERAGE_UNAVAILABLE",)))
    trading_days = {}
    if sdk is not None and type(snapshot) is sdk["FuturesMarketSnapshot"]:
        raw_days = getattr(snapshot, "trading_days", {})
        if isinstance(raw_days, Mapping):
            trading_days = {
                venue.upper(): day
                for venue, day in raw_days.items()
                if type(venue) is str and type(day) is date
            }
    source_fields = (
        _market_snapshot_source_fields(snapshot, sdk)
        if sdk is not None
        else {
            "source_id": None,
            "source_snapshot_id": None,
            "source_sha256": None,
            "source_available_at": None,
            "source_retrieved_at": None,
            "catalog_source_id": None,
            "catalog_snapshot_id": None,
            "catalog_sha256": None,
            "catalog_available_at": None,
            "catalog_retrieved_at": None,
        }
    )
    return MainContractManifest(
        status="PARTIAL",
        decision_at=decision_at,
        policy=policy,
        target_products=targets,
        **source_fields,
        trading_days=tuple(
            TradingDayReference(venue, day, None) for venue, day in sorted(trading_days.items())
        ),
        calendars=(),
        coverage=(),
        products=tuple(
            ProductSelection(
                product=product,
                in_target_pool=True,
                status="PARTIAL",
                expected_prior_trading_day=None,
                ranking_trading_day=trading_days.get(product.exchange_id),
                selected_instrument_id=None,
                ranked_instrument_ids=(),
                excluded_instrument_ids=(),
                blockers=blocker_rows,
            )
            for product in targets
        ),
        candidates=(),
        blockers=blocker_rows,
        market_snapshot_evidence_json=(
            _market_snapshot_json(snapshot, sdk) if sdk is not None else None
        ),
    )


def _sdk_family_complete(family: Any, sdk: Mapping[str, Any]) -> bool:
    return (
        type(family) is sdk["FuturesFamilyCoverage"]
        and family.complete is True
        and family.scope_complete is True
    )


def _neutral_snapshot_blockers(
    snapshot: Any,
    *,
    decision_at: datetime,
    policy: MainContractPolicy,
    sdk: Mapping[str, Any],
) -> Tuple[str, ...]:
    coverage = getattr(snapshot, "coverage", None)
    if type(coverage) is not sdk["FuturesMarketSnapshotCoverage"]:
        return ("MARKET_SNAPSHOT_COVERAGE_TYPE_INVALID",)
    blockers = []
    request = getattr(snapshot, "request", None)
    request_is_exact = type(request) is sdk["FuturesMarketSnapshotRequest"]
    if not request_is_exact:
        blockers.append("MARKET_SNAPSHOT_REQUEST_TYPE_INVALID")
    include_depth = getattr(request, "include_depth", False)
    if type(include_depth) is not bool:
        blockers.append("MARKET_SNAPSHOT_DEPTH_REQUIREMENT_INVALID")
        include_depth = True
    elif not include_depth and coverage.depth is not None:
        blockers.append("UNREQUESTED_DEPTH_FAMILY_PRESENT")
    if snapshot.complete is not True or coverage.complete is not True:
        blockers.append("MARKET_SNAPSHOT_COVERAGE_INCOMPLETE")
    if coverage.scope_complete is not True:
        blockers.append("MARKET_SNAPSHOT_SCOPE_INCOMPLETE")
    family_names = ("session", "catalog", "quotes") + (("depth",) if include_depth else ())
    for name in family_names:
        if not _sdk_family_complete(getattr(coverage, name, None), sdk):
            blockers.append("{}_FAMILY_COVERAGE_INCOMPLETE".format(name.upper()))
    blockers.extend(item for item in coverage.blockers if type(item) is str and item)
    if coverage.missing_quote_instruments:
        blockers.append("QUOTE_COVERAGE_MISSING_INSTRUMENTS")
    if coverage.missing_quote_fields:
        blockers.append("QUOTE_COVERAGE_MISSING_REQUIRED_FIELDS")
    if coverage.unclassified_instruments:
        blockers.append("CATALOG_COVERAGE_HAS_UNCLASSIFIED_INSTRUMENTS")
    if getattr(coverage, "quote_unit_mismatches", ()):
        blockers.append("QUOTE_COVERAGE_UNIT_MISMATCH")
    if getattr(coverage, "quote_semantic_mismatches", ()):
        blockers.append("QUOTE_COVERAGE_SEMANTICS_MISMATCH")
    if include_depth and getattr(coverage, "depth_unit_mismatches", ()):
        blockers.append("DEPTH_COVERAGE_UNIT_MISMATCH")

    environment = getattr(request, "environment", None)
    if type(environment) is not str or environment.casefold() == "unknown":
        blockers.append("MARKET_SNAPSHOT_ENVIRONMENT_UNAVAILABLE")
    quantity_unit = sdk["FuturesQuantityUnit"]
    if request is None or (
        getattr(request, "required_open_interest_unit", None) is not quantity_unit.CONTRACTS
        or getattr(request, "required_volume_unit", None) is not quantity_unit.CONTRACTS
    ):
        blockers.append("RANKING_QUANTITY_UNIT_REQUIREMENT_UNAVAILABLE")
    volume_semantics = sdk["FuturesVolumeSemantics"]
    if getattr(request, "required_volume_semantics", None) is not (
        volume_semantics.CUMULATIVE_TRADING_DAY
    ):
        blockers.append("RANKING_VOLUME_SEMANTICS_REQUIREMENT_UNAVAILABLE")
    if include_depth and getattr(request, "required_depth_volume_unit", None) is not (
        quantity_unit.CONTRACTS
    ):
        blockers.append("DEPTH_QUANTITY_UNIT_REQUIREMENT_UNAVAILABLE")

    source_ref = _source_text(getattr(snapshot, "source_ref", None))
    generation = _source_text(getattr(snapshot, "generation", None))
    if source_ref is None or generation is None:
        blockers.append("MARKET_SNAPSHOT_SOURCE_BINDING_UNAVAILABLE")
    for name in family_names:
        family = getattr(coverage, name, None)
        if type(family) is not sdk["FuturesFamilyCoverage"]:
            continue
        if family.source_ref != source_ref or family.generation != generation:
            blockers.append("MARKET_SNAPSHOT_FAMILY_SOURCE_MISMATCH")

    requested_at = _source_timestamp(getattr(snapshot, "requested_at", None))
    completed_at = _source_timestamp(getattr(snapshot, "completed_at", None))
    observed_at = _source_timestamp(getattr(snapshot, "observed_at", None))
    decision_utc = decision_at.astimezone(timezone.utc)
    if requested_at is None or completed_at is None or observed_at is None:
        blockers.append("MARKET_SNAPSHOT_TIME_UNAVAILABLE")
    else:
        requested_utc = requested_at.astimezone(timezone.utc)
        completed_utc = completed_at.astimezone(timezone.utc)
        observed_utc = observed_at.astimezone(timezone.utc)
        if requested_utc > completed_utc or completed_utc > observed_utc:
            blockers.append("MARKET_SNAPSHOT_TIME_ORDER_INVALID")
        if completed_utc > decision_utc or observed_utc > decision_utc:
            blockers.append("MARKET_SNAPSHOT_NOT_AVAILABLE_AT_DECISION")
        if (decision_utc - completed_utc).total_seconds() > policy.max_snapshot_age_seconds:
            blockers.append("MARKET_SNAPSHOT_TOO_OLD")
        if (completed_utc - requested_utc).total_seconds() > policy.max_collection_span_seconds:
            blockers.append("MARKET_SNAPSHOT_COLLECTION_SPAN_EXCEEDED")
    return tuple(dict.fromkeys(blockers))


def _neutral_classification(record: Any, sdk: Mapping[str, Any]) -> Tuple[str, Optional[str]]:
    """Classify through neutral SDK enums without inspecting native provider rows."""

    if type(record) is not sdk["FuturesCatalogRecord"]:
        return "unknown", "CATALOG_ROW_TYPE_INVALID"
    if (
        type(record.contract_kind) is not sdk["FuturesContractKind"]
        or type(record.asset_type) is not sdk["FuturesAssetType"]
        or type(record.disposition) is not sdk["FuturesCatalogDisposition"]
    ):
        return "unknown", "CATALOG_CLASSIFICATION_TYPE_INVALID"
    if record.disposition is sdk["FuturesCatalogDisposition"].EXCLUDED:
        return "non_future", record.disposition_reason or "CATALOG_DISPOSITION_EXCLUDED"
    if (
        record.disposition is sdk["FuturesCatalogDisposition"].UNKNOWN
        or record.contract_kind is sdk["FuturesContractKind"].UNKNOWN
    ):
        return "unknown", "CATALOG_CLASSIFICATION_UNAVAILABLE"
    if record.contract_kind is not sdk["FuturesContractKind"].FUTURE:
        return "non_future", record.disposition_reason or "NOT_ORDINARY_FUTURE"
    if record.asset_type is sdk["FuturesAssetType"].UNKNOWN:
        return "unknown", "CATALOG_CLASSIFICATION_UNAVAILABLE"
    return "future", None


def _select_futures_market_snapshot_rows(
    snapshot: Any,
    expected_products: Sequence[ProductKey],
    calendars: Sequence[ExchangeTradingCalendar],
    *,
    decision_at: datetime,
    policy: MainContractPolicy,
    sdk: Mapping[str, Any],
) -> MainContractManifest:
    targets = tuple(expected_products)
    if any(type(item) is not ProductKey for item in targets):
        raise ValueError("expected_products must contain ProductKey records")
    targets = tuple(sorted(targets, key=_key_order))
    target_by_id = {_key_order(item): item for item in targets}
    if len(target_by_id) != len(targets):
        raise ValueError("expected_products contains duplicate product keys")

    session_blockers = []
    session_by_venue = {}
    route_session = None
    request = getattr(snapshot, "request", None)
    requested_environment = getattr(request, "environment", None)
    require_active_session = getattr(request, "require_active_session", False)
    if type(require_active_session) is not bool:
        session_blockers.append("SESSION_ACTIVE_REQUIREMENT_INVALID")
    for row in tuple(getattr(snapshot, "sessions", ())):
        if type(row) is not sdk["FuturesSessionRecord"]:
            session_blockers.append("SESSION_ROW_TYPE_INVALID")
            continue
        if type(row.session_status) is not sdk["FuturesSessionStatus"]:
            session_blockers.append("SESSION_STATUS_UNKNOWN")
            continue
        if type(requested_environment) is not str or row.environment != requested_environment:
            session_blockers.append("SESSION_ENVIRONMENT_MISMATCH")
        if require_active_session and row.session_status is not sdk["FuturesSessionStatus"].ACTIVE:
            session_blockers.append("SESSION_NOT_ACTIVE")
        if row.venue is None:
            if route_session is not None or session_by_venue:
                session_blockers.append("SESSION_ROUTE_VENUE_SCOPE_MIXED_OR_DUPLICATE")
                continue
            route_session = row
        else:
            venue = row.venue.upper()
            if route_session is not None:
                session_blockers.append("SESSION_ROUTE_VENUE_SCOPE_MIXED_OR_DUPLICATE")
                continue
            if venue in session_by_venue:
                session_blockers.append("SESSION_ROW_DUPLICATE_VENUE")
                continue
            session_by_venue[venue] = row
    raw_days = getattr(snapshot, "trading_days", None)
    day_by_venue = {}
    if isinstance(raw_days, Mapping):
        for venue, trading_day in raw_days.items():
            if type(venue) is not str or type(trading_day) is not date:
                session_blockers.append("SESSION_TRADING_DAY_INVALID")
                continue
            venue_key = venue.upper()
            if venue_key in day_by_venue:
                session_blockers.append("SESSION_TRADING_DAY_DUPLICATE_VENUE")
                continue
            day_by_venue[venue_key] = trading_day
    else:
        session_blockers.append("SESSION_TRADING_DAYS_UNAVAILABLE")
    for venue, row in session_by_venue.items():
        if day_by_venue.get(venue) != row.trading_day:
            session_blockers.append("SESSION_TRADING_DAY_MISMATCH")
    if route_session is not None:
        for venue, trading_day in day_by_venue.items():
            if trading_day != route_session.trading_day:
                session_blockers.append("SESSION_ROUTE_TRADING_DAY_MISMATCH")
    for product in targets:
        has_session = product.exchange_id in session_by_venue or route_session is not None
        if not has_session or product.exchange_id not in day_by_venue:
            session_blockers.append("TARGET_SESSION_COVERAGE_MISSING")
        requested_day = getattr(request, "trading_day", None)
        if requested_day is not None and day_by_venue.get(product.exchange_id) != requested_day:
            session_blockers.append("SESSION_REQUESTED_TRADING_DAY_MISMATCH")
    session_blockers = list(dict.fromkeys(session_blockers))

    instrument_type = sdk["MarketInstrumentKey"]
    product_type = sdk["FuturesProductKey"]
    catalog_by_identity = {}
    catalog_by_product = {}
    classification_by_identity = {}
    row_blockers = []
    for row in tuple(getattr(snapshot, "catalog", ())):
        if type(row) is not sdk["FuturesCatalogRecord"]:
            row_blockers.append("CATALOG_ROW_TYPE_INVALID")
            continue
        if (
            type(row.instrument) is not instrument_type
            or type(row.product) is not product_type
            or row.instrument.venue != row.product.venue
            or type(requested_environment) is not str
            or row.instrument.environment != requested_environment
        ):
            row_blockers.append("CATALOG_ROW_IDENTITY_INVALID")
            continue
        catalog_by_identity.setdefault(row.instrument, []).append(row)
        product = ProductKey(row.product.venue, row.product.product_id)
        catalog_by_product.setdefault(_key_order(product), []).append(row)
        classification_by_identity[row.instrument] = _neutral_classification(row, sdk)
    if any(len(rows) > 1 for rows in catalog_by_identity.values()):
        row_blockers.append("DUPLICATE_CATALOG_INSTRUMENT_IDENTITY")

    quote_by_identity = {}
    quote_rows = tuple(getattr(snapshot, "quotes", ()))
    for quote in quote_rows:
        if type(quote) is not sdk["FuturesQuoteRecord"]:
            row_blockers.append("QUOTE_ROW_TYPE_INVALID")
            continue
        if (
            type(quote.instrument) is not instrument_type
            or type(requested_environment) is not str
            or quote.instrument.environment != requested_environment
        ):
            row_blockers.append("QUOTE_ROW_IDENTITY_INVALID")
            continue
        if quote.instrument not in catalog_by_identity:
            row_blockers.append("QUOTE_IDENTITY_NOT_IN_CATALOG")
            continue
        quote_by_identity.setdefault(quote.instrument, []).append(quote)
        if day_by_venue.get(quote.instrument.venue.upper()) != quote.trading_day:
            row_blockers.append("QUOTE_TRADING_DAY_MISMATCH")
    if any(len(rows) > 1 for rows in quote_by_identity.values()):
        row_blockers.append("DUPLICATE_QUOTE_INSTRUMENT_IDENTITY")
    row_blockers = list(dict.fromkeys(row_blockers))

    source_blockers = list(
        _neutral_snapshot_blockers(
            snapshot,
            decision_at=decision_at,
            policy=policy,
            sdk=sdk,
        )
    )
    source_blockers.extend(session_blockers)
    source_blockers.extend(row_blockers)
    source_blockers = list(dict.fromkeys(source_blockers))

    calendar_rows = tuple(calendars)
    if any(type(item) is not ExchangeTradingCalendar for item in calendar_rows):
        raise ValueError("calendars must contain ExchangeTradingCalendar records")
    calendar_index = {}
    for calendar in calendar_rows:
        if calendar.exchange_id in calendar_index:
            raise ValueError("calendars contains duplicate exchanges")
        calendar_index[calendar.exchange_id] = calendar

    expected_by_product = {}
    for identity in snapshot.coverage.expected_instruments:
        if type(identity) is not instrument_type:
            source_blockers.append("EXPECTED_QUOTE_IDENTITY_INVALID")
            continue
        rows = catalog_by_identity.get(identity, ())
        if len(rows) == 1:
            product = rows[0].product
            product_id = _key_order(ProductKey(product.venue, product.product_id))
            expected_by_product.setdefault(product_id, []).append(identity.instrument_id)
    source_blockers = list(dict.fromkeys(source_blockers))

    request = getattr(snapshot, "request", None)
    requested_products = getattr(request, "products", None)
    requested_venues = getattr(request, "venues", None)
    requested_instruments = getattr(request, "instruments", None)
    requested_contract_kinds = getattr(request, "contract_kinds", ())
    requested_quote_fields = getattr(request, "required_quote_fields", ())
    if requested_instruments is not None:
        source_blockers.append("MARKET_SNAPSHOT_INSTRUMENT_FILTER_INCOMPLETE")
    if sdk["FuturesContractKind"].FUTURE not in requested_contract_kinds:
        source_blockers.append("MARKET_SNAPSHOT_FUTURE_SCOPE_MISSING")
    if not {"open_interest", "volume"}.issubset(set(requested_quote_fields)):
        source_blockers.append("MARKET_SNAPSHOT_RANKING_FIELDS_MISSING")
    source_blockers = list(dict.fromkeys(source_blockers))
    requested_product_ids = None
    if requested_products is not None:
        requested_product_ids = {
            _key_order(ProductKey(row.venue, row.product_id))
            for row in requested_products
            if type(row) is product_type
        }
    requested_venue_ids = (
        {item.upper() for item in requested_venues if type(item) is str}
        if requested_venues is not None
        else None
    )
    no_listed_ids = set()
    product_scope_blockers = {}
    for product_id, product in target_by_id.items():
        in_request_scope = (
            product_id in requested_product_ids
            if requested_product_ids is not None
            else product.exchange_id in requested_venue_ids
            if requested_venue_ids is not None
            else True
        )
        if not in_request_scope:
            product_scope_blockers[product_id] = "TARGET_PRODUCT_OUTSIDE_REQUEST_SCOPE"
        if (
            in_request_scope
            and requested_instruments is None
            and not catalog_by_product.get(product_id)
            and not source_blockers
        ):
            no_listed_ids.add(product_id)

    selections = []
    assessments = []
    market_coverage = []
    day_references = {}
    used_calendars = {}
    for product_id in sorted(set(target_by_id).union(catalog_by_product)):
        product = target_by_id.get(product_id)
        if product is None:
            first = catalog_by_product[product_id][0].product
            product = ProductKey(first.venue, first.product_id)
        records = catalog_by_product.get(product_id, ())
        in_target = product_id in target_by_id
        if not in_target:
            for row in records:
                classification, issue = classification_by_identity.get(
                    row.instrument, ("unknown", "CATALOG_CLASSIFICATION_UNAVAILABLE")
                )
                assessments.append(
                    ContractAssessment(
                        product=product,
                        instrument_id=row.instrument.instrument_id,
                        status="OUT_OF_SCOPE" if classification == "future" else (
                            "PARTIAL" if classification == "unknown" else "EXCLUDED"
                        ),
                        reason=issue,
                        expiry_date=row.expiry_date,
                        trading_days_to_expiry=None,
                        open_interest=None,
                        volume=None,
                        rank=None,
                        asset_type=row.asset_type.value,
                        is_trading=row.is_trading,
                        contract_kind=row.contract_kind.value,
                    )
                )
            continue
        if product_id in no_listed_ids:
            selections.append(
                ProductSelection(
                    product=product,
                    in_target_pool=False,
                    status="EXCLUDED",
                    expected_prior_trading_day=None,
                    ranking_trading_day=day_by_venue.get(product.exchange_id),
                    selected_instrument_id=None,
                    ranked_instrument_ids=(),
                    excluded_instrument_ids=(),
                    blockers=(),
                    exclusion_reason=EXCLUSION_REASON_NO_LISTED_CONTRACTS,
                )
            )
            market_coverage.append(
                MainContractMarketCoverage(
                    product=product,
                    catalog_instrument_ids=(),
                    expected_quote_instrument_ids=(),
                    observed_quote_instrument_ids=(),
                )
            )
            continue

        current_day = day_by_venue.get(product.exchange_id)
        if current_day is not None:
            day_references[product.exchange_id] = TradingDayReference(
                product.exchange_id, current_day, None
            )
        calendar, calendar_blockers = _calendar_for(product, calendar_index, decision_at)
        if calendar is not None and not calendar_blockers:
            used_calendars[product.exchange_id] = _calendar_evidence(calendar)
        product_blockers = list(source_blockers)
        if product_id in product_scope_blockers:
            product_blockers.append(product_scope_blockers[product_id])
        product_blockers.extend(calendar_blockers)
        if current_day is None:
            product_blockers.append("CURRENT_TRADING_DAY_UNAVAILABLE")
        if not records:
            product_blockers.append("PRODUCT_CATALOG_COVERAGE_UNAVAILABLE")

        catalog_ids = tuple(sorted(row.instrument.instrument_id for row in records))
        expected_quote_ids = tuple(sorted(set(expected_by_product.get(product_id, ()))))
        observed_quote_ids = tuple(
            sorted(
                quote.instrument.instrument_id
                for quote in quote_rows
                if type(quote) is sdk["FuturesQuoteRecord"]
                and quote.instrument in catalog_by_identity
                and _key_order(
                    ProductKey(
                        catalog_by_identity[quote.instrument][0].product.venue,
                        catalog_by_identity[quote.instrument][0].product.product_id,
                    )
                )
                == product_id
            )
        )
        market_coverage.append(
            MainContractMarketCoverage(
                product=product,
                catalog_instrument_ids=catalog_ids,
                expected_quote_instrument_ids=expected_quote_ids,
                observed_quote_instrument_ids=observed_quote_ids,
            )
        )

        eligible = []
        group_assessments = []
        for catalog_row in records:
            classification, issue = classification_by_identity.get(
                catalog_row.instrument, ("unknown", "CATALOG_CLASSIFICATION_UNAVAILABLE")
            )
            if classification == "unknown":
                product_blockers.append(issue or "CATALOG_CLASSIFICATION_UNAVAILABLE")
                group_assessments.append(
                    ContractAssessment(
                        product=product,
                        instrument_id=catalog_row.instrument.instrument_id,
                        status="PARTIAL",
                        reason=issue,
                        expiry_date=catalog_row.expiry_date,
                        trading_days_to_expiry=None,
                        open_interest=None,
                        volume=None,
                        rank=None,
                        asset_type=catalog_row.asset_type.value,
                        is_trading=catalog_row.is_trading,
                        contract_kind=catalog_row.contract_kind.value,
                    )
                )
                continue
            if classification != "future":
                group_assessments.append(
                    ContractAssessment(
                        product=product,
                        instrument_id=catalog_row.instrument.instrument_id,
                        status="EXCLUDED",
                        reason=issue or "NOT_ORDINARY_FUTURE",
                        expiry_date=catalog_row.expiry_date,
                        trading_days_to_expiry=None,
                        open_interest=None,
                        volume=None,
                        rank=None,
                        asset_type=catalog_row.asset_type.value,
                        is_trading=catalog_row.is_trading,
                        contract_kind=catalog_row.contract_kind.value,
                    )
                )
                continue
            matching = quote_by_identity.get(catalog_row.instrument, ())
            quote = matching[0] if len(matching) == 1 else None
            if quote is None:
                product_blockers.append("QUOTE_ROW_MISSING_FOR_CANDIDATE")
                group_assessments.append(
                    ContractAssessment(
                        product=product,
                        instrument_id=catalog_row.instrument.instrument_id,
                        status="PARTIAL",
                        reason="QUOTE_ROW_MISSING_FOR_CANDIDATE",
                        expiry_date=catalog_row.expiry_date,
                        trading_days_to_expiry=None,
                        open_interest=None,
                        volume=None,
                        rank=None,
                        asset_type=catalog_row.asset_type.value,
                        is_trading=catalog_row.is_trading,
                        contract_kind=catalog_row.contract_kind.value,
                    )
                )
                continue
            quantity_unit = sdk["FuturesQuantityUnit"]
            if (
                type(quote.open_interest_unit) is not quantity_unit
                or type(quote.volume_unit) is not quantity_unit
                or quote.open_interest_unit is not quantity_unit.CONTRACTS
                or quote.volume_unit is not quantity_unit.CONTRACTS
            ):
                product_blockers.append("RANKING_QUANTITY_UNIT_UNAVAILABLE")
                group_assessments.append(
                    ContractAssessment(
                        product=product,
                        instrument_id=catalog_row.instrument.instrument_id,
                        status="PARTIAL",
                        reason="RANKING_QUANTITY_UNIT_UNAVAILABLE",
                        expiry_date=catalog_row.expiry_date,
                        trading_days_to_expiry=None,
                        open_interest=None,
                        volume=None,
                        rank=None,
                        asset_type=catalog_row.asset_type.value,
                        is_trading=catalog_row.is_trading,
                        contract_kind=catalog_row.contract_kind.value,
                        quote_trading_day=quote.trading_day,
                        provider_event_date=quote.provider_event_date,
                        provider_event_time=quote.provider_event_time,
                        provider_event_millisecond=quote.provider_event_millisecond,
                    )
                )
                continue
            volume_semantics = sdk["FuturesVolumeSemantics"]
            if (
                type(quote.volume_semantics) is not volume_semantics
                or quote.volume_semantics is not volume_semantics.CUMULATIVE_TRADING_DAY
            ):
                product_blockers.append("RANKING_VOLUME_SEMANTICS_UNAVAILABLE")
                group_assessments.append(
                    ContractAssessment(
                        product=product,
                        instrument_id=catalog_row.instrument.instrument_id,
                        status="PARTIAL",
                        reason="RANKING_VOLUME_SEMANTICS_UNAVAILABLE",
                        expiry_date=catalog_row.expiry_date,
                        trading_days_to_expiry=None,
                        open_interest=None,
                        volume=None,
                        rank=None,
                        asset_type=catalog_row.asset_type.value,
                        is_trading=catalog_row.is_trading,
                        contract_kind=catalog_row.contract_kind.value,
                        quote_trading_day=quote.trading_day,
                        provider_event_date=quote.provider_event_date,
                        provider_event_time=quote.provider_event_time,
                        provider_event_millisecond=quote.provider_event_millisecond,
                        volume_semantics=getattr(quote.volume_semantics, "value", None),
                    )
                )
                continue
            ranking_record = FuturesContractRecord(
                product=product,
                instrument_id=catalog_row.instrument.instrument_id,
                asset_type="future",
                is_trading=catalog_row.is_trading,
                expiry_date=catalog_row.expiry_date,
                open_interest=quote.open_interest,
                volume=quote.volume,
            )
            assessment, is_eligible = _candidate_assessment(
                ranking_record,
                current_trading_day=current_day,
                calendar=calendar if not calendar_blockers else None,
                policy=policy,
                suppress_source_values=False,
                exact_metric_limit=True,
                require_month_contract=False,
            )
            assessment = replace(
                assessment,
                asset_type=catalog_row.asset_type.value,
                is_trading=catalog_row.is_trading,
                contract_kind=catalog_row.contract_kind.value,
                quote_trading_day=quote.trading_day,
                provider_event_date=quote.provider_event_date,
                provider_event_time=quote.provider_event_time,
                provider_event_millisecond=quote.provider_event_millisecond,
                volume_semantics=quote.volume_semantics.value,
            )
            score = None
            if is_eligible:
                oi = _number_as_decimal(quote.open_interest)
                volume = _number_as_decimal(quote.volume)
                score = (
                    _exact_equal_weight_score(oi, volume)
                    if oi is not None and volume is not None
                    else None
                )
                if score is None:
                    is_eligible = False
                    assessment = replace(
                        assessment,
                        status="BLOCKED",
                        reason="RANKING_VALUE_RANGE_UNSUPPORTED",
                        open_interest=None,
                        volume=None,
                    )
                else:
                    assessment = replace(assessment, ranking_score=_decimal_text(score))
            if assessment.status == "BLOCKED" and assessment.reason:
                product_blockers.append(assessment.reason)
            if is_eligible:
                eligible.append((catalog_row, assessment, score))
            group_assessments.append(assessment)

        product_blockers = list(dict.fromkeys(product_blockers))
        partial_reasons = {
            "CATALOG_CLASSIFICATION_UNAVAILABLE",
            "CATALOG_CLASSIFICATION_TYPE_INVALID",
            "CATALOG_FUTURES_CLASSIFICATION_CONFLICT",
            "QUOTE_ROW_MISSING_FOR_CANDIDATE",
            "RANKING_QUANTITY_UNIT_UNAVAILABLE",
            "RANKING_VOLUME_SEMANTICS_UNAVAILABLE",
            "CATALOG_TRADING_STATUS_UNAVAILABLE",
            "PRODUCT_CATALOG_COVERAGE_UNAVAILABLE",
            "TARGET_PRODUCT_OUTSIDE_REQUEST_SCOPE",
            "ASSET_TYPE_UNKNOWN",
            "TRADING_STATUS_UNKNOWN",
            "EXPIRY_UNAVAILABLE",
            "EXPIRY_CALENDAR_UNAVAILABLE",
            "EXPIRY_SAFETY_UNPROVEN",
            "RANKING_VALUE_MISSING_OR_INVALID",
            "RANKING_VALUE_RANGE_UNSUPPORTED",
            "EXCHANGE_CALENDAR_UNAVAILABLE",
            "EXCHANGE_CALENDAR_INCOMPLETE",
            "CALENDAR_NOT_AVAILABLE_AT_DECISION",
            "CALENDAR_RETRIEVED_AFTER_DECISION",
        }
        is_partial = bool(source_blockers) or any(
            item in partial_reasons
            or item.startswith(
                (
                    "SESSION_",
                    "MARKET_SNAPSHOT_",
                    "CATALOG_COVERAGE_",
                    "QUOTE_COVERAGE_",
                    "CALENDAR_",
                    "EXCHANGE_CALENDAR_",
                )
            )
            for item in product_blockers
        )
        if is_partial:
            assessments.extend(
                replace(item, status="PARTIAL", rank=None)
                if item.status == "ELIGIBLE"
                else item
                for item in group_assessments
            )
            selections.append(
                ProductSelection(
                    product=product,
                    in_target_pool=True,
                    status="PARTIAL",
                    expected_prior_trading_day=None,
                    ranking_trading_day=current_day,
                    selected_instrument_id=None,
                    ranked_instrument_ids=tuple(
                        row.instrument.instrument_id for row, _assessment, _score in eligible
                    ),
                    excluded_instrument_ids=tuple(
                        item.instrument_id for item in group_assessments if item.status == "EXCLUDED"
                    ),
                    blockers=tuple(product_blockers or ("MARKET_SNAPSHOT_COVERAGE_INCOMPLETE",)),
                )
            )
            continue
        if product_blockers or not eligible:
            if not product_blockers:
                product_blockers.append("NO_ELIGIBLE_FUTURES_CONTRACTS")
            assessments.extend(
                replace(item, status="BLOCKED", reason=product_blockers[0], rank=None)
                if item.status == "ELIGIBLE"
                else item
                for item in group_assessments
            )
            selections.append(
                ProductSelection(
                    product=product,
                    in_target_pool=True,
                    status="BLOCKED",
                    expected_prior_trading_day=None,
                    ranking_trading_day=current_day,
                    selected_instrument_id=None,
                    ranked_instrument_ids=(),
                    excluded_instrument_ids=tuple(
                        item.instrument_id for item in group_assessments if item.status == "EXCLUDED"
                    ),
                    blockers=tuple(product_blockers),
                )
            )
            continue
        ranked = sorted(
            eligible,
            key=lambda item: (
                item[2].copy_negate(),
                Decimal(item[1].open_interest).copy_negate(),
                Decimal(item[1].volume).copy_negate(),
                item[1].expiry_date,
                item[0].instrument.instrument_id,
            ),
        )
        rank_by_id = {
            item[0].instrument.instrument_id: index for index, item in enumerate(ranked, 1)
        }
        score_by_id = {
            item[0].instrument.instrument_id: _decimal_text(item[2]) for item in ranked
        }
        assessments.extend(
            replace(
                assessment,
                status="RANKED",
                reason=None,
                rank=rank_by_id[assessment.instrument_id],
                ranking_score=score_by_id[assessment.instrument_id],
            )
            if assessment.instrument_id in rank_by_id
            else assessment
            for assessment in group_assessments
        )
        selections.append(
            ProductSelection(
                product=product,
                in_target_pool=True,
                status="READY",
                expected_prior_trading_day=None,
                ranking_trading_day=current_day,
                selected_instrument_id=ranked[0][0].instrument.instrument_id,
                ranked_instrument_ids=tuple(
                    item[0].instrument.instrument_id for item in ranked
                ),
                excluded_instrument_ids=tuple(
                    item.instrument_id for item in group_assessments if item.status == "EXCLUDED"
                ),
                blockers=(),
                selected_score=_decimal_text(ranked[0][2]),
            )
        )

    narrowed_targets = tuple(
        item for item in targets if _key_order(item) not in no_listed_ids
    )
    target_selections = [item for item in selections if item.in_target_pool]
    manifest_blockers = list(source_blockers)
    manifest_blockers.extend(reason for item in target_selections for reason in item.blockers)
    if not narrowed_targets:
        manifest_blockers.append("TARGET_PRODUCT_POOL_EMPTY")
    manifest_blockers = list(dict.fromkeys(manifest_blockers))
    has_partial = bool(source_blockers) or any(item.status == "PARTIAL" for item in target_selections)
    status = (
        "PARTIAL"
        if has_partial
        else "READY"
        if narrowed_targets and all(item.status == "READY" for item in target_selections)
        else "BLOCKED"
    )
    return MainContractManifest(
        status=status,
        decision_at=decision_at,
        policy=policy,
        target_products=narrowed_targets,
        **_market_snapshot_source_fields(snapshot, sdk),
        trading_days=tuple(day_references[key] for key in sorted(day_references)),
        calendars=tuple(used_calendars[key] for key in sorted(used_calendars)),
        coverage=(),
        products=tuple(sorted(selections, key=lambda item: _key_order(item.product))),
        candidates=tuple(
            sorted(
                assessments,
                key=lambda item: (
                    _key_order(item.product),
                    item.rank if item.rank is not None else 2**31,
                    item.instrument_id,
                ),
            )
        ),
        blockers=tuple(manifest_blockers),
        market_coverage=tuple(
            sorted(market_coverage, key=lambda item: _key_order(item.product))
        ),
        market_snapshot_evidence_json=_market_snapshot_json(snapshot, sdk),
    )


def _select_futures_market_snapshot(
    snapshot: Any,
    expected_products: Sequence[ProductKey],
    calendars: Sequence[ExchangeTradingCalendar],
    *,
    decision_at: datetime,
    policy: MainContractPolicy,
    snapshot_lease: Any,
    snapshot_facade: Any,
) -> MainContractManifest:
    sdk = _futures_market_sdk_types()
    if sdk is None:
        return _neutral_partial_manifest(
            None,
            expected_products,
            decision_at=decision_at,
            policy=policy,
            blockers=("FUTURES_MARKET_SDK_CONTRACT_UNAVAILABLE",),
            sdk=None,
        )
    if snapshot is None:
        return _neutral_partial_manifest(
            None,
            expected_products,
            decision_at=decision_at,
            policy=policy,
            blockers=("MARKET_SNAPSHOT_UNAVAILABLE",),
            sdk=sdk,
        )
    if type(snapshot) is not sdk["FuturesMarketSnapshot"]:
        return _neutral_partial_manifest(
            None,
            expected_products,
            decision_at=decision_at,
            policy=policy,
            blockers=("MARKET_SNAPSHOT_TYPE_INVALID",),
            sdk=sdk,
        )
    if (
        type(snapshot_lease) is not sdk["QuerySnapshotLease"]
        or type(snapshot_facade) is not sdk["FuturesMarketSnapshotFacade"]
    ):
        return _neutral_partial_manifest(
            snapshot,
            expected_products,
            decision_at=decision_at,
            policy=policy,
            blockers=("MARKET_SNAPSHOT_LEASE_UNAVAILABLE",),
            sdk=sdk,
        )
    try:
        lease_before = snapshot_facade.validate_lease(
            snapshot, snapshot_lease, now=datetime.now(timezone.utc)
        )
    except Exception:
        lease_before = False
    if lease_before is not True:
        return _neutral_partial_manifest(
            snapshot,
            expected_products,
            decision_at=decision_at,
            policy=policy,
            blockers=("MARKET_SNAPSHOT_LEASE_INVALID_BEFORE_SELECTION",),
            sdk=sdk,
        )
    try:
        manifest = _select_futures_market_snapshot_rows(
            snapshot,
            expected_products,
            calendars,
            decision_at=decision_at,
            policy=policy,
            sdk=sdk,
        )
    except Exception:
        manifest = _neutral_partial_manifest(
            snapshot,
            expected_products,
            decision_at=decision_at,
            policy=policy,
            blockers=("MARKET_SNAPSHOT_PROJECTION_INVALID",),
            sdk=sdk,
        )
    try:
        lease_after = snapshot_facade.validate_lease(
            snapshot, snapshot_lease, now=datetime.now(timezone.utc)
        )
    except Exception:
        lease_after = False
    if lease_after is not True:
        return _neutral_partial_manifest(
            snapshot,
            expected_products,
            decision_at=decision_at,
            policy=policy,
            blockers=("MARKET_SNAPSHOT_LEASE_INVALID_AFTER_SELECTION",),
            sdk=sdk,
        )
    return manifest
