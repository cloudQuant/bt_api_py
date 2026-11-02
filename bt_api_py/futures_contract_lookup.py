"""Explicit, bounded futures ranking from one SDK query snapshot.

The query lease bounds query completion, not the age of the underlying market
event. This workflow currently supports reference quotes only and does not
establish real-time market freshness or permission to trade.
"""

from dataclasses import dataclass
from datetime import date, datetime, timezone
from uuid import uuid4

from .futures_contracts import FuturesMarketSnapshotRequest, FuturesProductKey
from .futures_expected_calendar_contracts import (
    FuturesExpectedCalendarRequest,
    FuturesExpectedCalendarSnapshot,
    FuturesExpectedTradingDay,
)
from .futures_market_facade import FuturesMarketSnapshotFacade
from .futures_selection import (
    ExchangeTradingCalendar,
    ExchangeTradingDay,
    FUTURES_MARKET_SNAPSHOT_EQUAL_WEIGHT_POLICY,
    ProductKey,
    get_ranked_contract,
    select_main_contracts,
)
from .futures_snapshot_workflow import collect_futures_snapshot


class FuturesContractLookupUnavailable(RuntimeError):
    """The requested source or required selection evidence is unavailable."""


@dataclass(frozen=True)
class FuturesContractLookup:
    """Both ranks and their shared source evidence; no freshness is implied."""

    route: str
    exchange_id: str
    product_id: str
    main: str
    secondary: str | None
    snapshot: object
    lease: object
    calendar_snapshot: object
    manifest: object

    @property
    def freshness_status(self):
        return self.snapshot.freshness_status

    @property
    def freshness_blockers(self):
        return self.snapshot.freshness_blockers


def lookup_futures_contracts(
    query_facade,
    calendar_port,
    *,
    route: str,
    exchange_id: str,
    product_id: str,
    environment: str,
    allow_reference: bool = False,
    max_collection_seconds: float = 30.0,
    max_snapshot_age_seconds: float = 30.0,
    lease_seconds: float = 15.0,
) -> FuturesContractLookup:
    """Query once for one venue/product; each call refreshes, without caching.

    ``route`` identifies the configured SDK route; ``exchange_id`` is the
    venue. Venue and product IDs must match the provider's identifiers.
    Because the current neutral facade cannot attest market-event freshness,
    callers must explicitly opt into reference-only results. The default
    fails closed and reference results do not establish real-time freshness.
    Time bounds constrain the SDK query and lease, subject to the provider
    honoring its query deadline. Do not call repeatedly on every bar.
    """
    if type(allow_reference) is not bool:
        raise TypeError("allow_reference must be bool")
    if not allow_reference:
        raise FuturesContractLookupUnavailable("FUTURES_MARKET_FRESHNESS_UNAVAILABLE")
    for name, value in (("route", route), ("exchange_id", exchange_id),
                        ("product_id", product_id), ("environment", environment)):
        if type(value) is not str or not value or value != value.strip():
            raise ValueError(f"{name} must be non-empty trimmed text")

    product = FuturesProductKey(exchange_id, product_id)
    request = FuturesMarketSnapshotRequest(
        request_ref="futures-lookup-" + uuid4().hex,
        environment=environment,
        venues=(exchange_id,),
        products=(product,),
        require_product_universe=True,
    )
    facade = FuturesMarketSnapshotFacade(query_facade)
    leased = collect_futures_snapshot(
        facade, request,
        max_collection_seconds=max_collection_seconds,
        max_snapshot_age_seconds=max_snapshot_age_seconds,
        lease_seconds=lease_seconds,
    )
    snapshot = leased.snapshot
    if snapshot.products != (product,) or not snapshot.product_universe_complete:
        raise FuturesContractLookupUnavailable("FUTURES_PRODUCT_UNIVERSE_UNAVAILABLE")
    day = snapshot.trading_days.get(exchange_id)
    if type(day) is not date:
        raise FuturesContractLookupUnavailable("FUTURES_TRADING_DAY_UNAVAILABLE")
    decision_at = datetime.now(timezone.utc)
    calendar_request = FuturesExpectedCalendarRequest(
        products=(product,),
        current_trading_days=(FuturesExpectedTradingDay(exchange_id, day),),
        as_of_utc=decision_at,
    )
    calendar = calendar_port.build(calendar_request)
    if (type(calendar) is not FuturesExpectedCalendarSnapshot
            or calendar.request is not calendar_request
            or calendar_port.validate(calendar) is not True
            or calendar.complete is not True):
        raise FuturesContractLookupUnavailable("FUTURES_EXPECTED_CALENDAR_UNAVAILABLE")
    sources = {item.venue: item for item in calendar.sources}
    calendars = []
    for row in calendar.venue_calendars:
        source = sources.get(row.venue)
        if source is None or source.source_id != row.source_id:
            raise FuturesContractLookupUnavailable("FUTURES_EXPECTED_CALENDAR_BINDING_INVALID")
        calendars.append(ExchangeTradingCalendar(
            exchange_id=row.venue, source_id=source.source_id,
            version=source.version, available_at=source.available_at_utc,
            retrieved_at=source.retrieved_at_utc,
            coverage_start=row.coverage_start, coverage_through=row.coverage_through,
            trading_days=row.trading_days, complete=row.complete,
            evidence_mode="expected_rule_derived",
            source_manifest_sha256=calendar.rules_manifest_sha256,
        ))
    manifest = select_main_contracts(
        snapshot, (ProductKey(exchange_id, product_id),),
        (ExchangeTradingDay(exchange_id, day),), tuple(calendars),
        decision_at=decision_at, policy=FUTURES_MARKET_SNAPSHOT_EQUAL_WEIGHT_POLICY,
        snapshot_lease=leased.lease, snapshot_facade=facade,
    )
    main = get_ranked_contract(manifest, exchange_id, product_id, 1)
    secondary = get_ranked_contract(manifest, exchange_id, product_id, 2)
    if calendar_port.validate(calendar) is not True:
        raise FuturesContractLookupUnavailable("FUTURES_EXPECTED_CALENDAR_UNAVAILABLE")
    return FuturesContractLookup(
        route, exchange_id, product_id, main, secondary,
        snapshot, leased.lease, calendar, manifest,
    )
