"""Neutral EOD snapshot binding, lease and manifest projection helpers.

These checks preserve the supplied facade and snapshot identity; they do not
establish provider authority or create an independent ledger.
"""

from __future__ import annotations

import hashlib
import json
from datetime import datetime, timezone
from typing import Any, Tuple


def _instrument_identity(instrument: Any) -> Tuple[str, ...]:
    return (
        instrument.provider,
        instrument.venue,
        instrument.environment,
        instrument.product_type,
        instrument.instrument_id,
        instrument.native_symbol,
    )


def eod_snapshot_manifest_digest(snapshot: Any) -> str:
    payload = {
        "request_ref": snapshot.request.request_ref,
        "trading_day": snapshot.request.trading_day.isoformat(),
        "requested_at": snapshot.requested_at.isoformat(),
        "completed_at": snapshot.completed_at.isoformat(),
        "observed_at": snapshot.observed_at.isoformat(),
        "products": [item.to_dict() for item in snapshot.request.products],
        "catalog": {
            "market_snapshot_ref": snapshot.catalog.market_snapshot_ref,
            "market_source_ref": snapshot.catalog.market_source_ref,
            "market_generation": snapshot.catalog.market_generation,
            "market_records_digest": snapshot.catalog.market_records_digest,
            "environment": snapshot.catalog.environment,
            "catalog_request_ref": snapshot.catalog.catalog_request_ref,
            "catalog_snapshot_ref": snapshot.catalog.catalog_snapshot_ref,
            "catalog_records_digest": snapshot.catalog.catalog_records_digest,
            "catalog_completeness": snapshot.catalog.catalog_completeness.value,
            "catalog_scope_complete": snapshot.catalog.catalog_scope_complete,
            "venues": snapshot.catalog.venues,
            "catalog_products": (
                None
                if snapshot.catalog.catalog_products is None
                else [item.to_dict() for item in snapshot.catalog.catalog_products]
            ),
            "product_universe_request_ref": snapshot.catalog.product_universe_request_ref,
            "product_universe_snapshot_ref": snapshot.catalog.product_universe_snapshot_ref,
            "product_universe_records_digest": (
                snapshot.catalog.product_universe_records_digest
            ),
            "product_universe_complete": snapshot.catalog.product_universe_complete,
            "product_universe": (
                None
                if snapshot.catalog.product_universe is None
                else [item.to_dict() for item in snapshot.catalog.product_universe]
            ),
        },
        "records_digest": snapshot.records_digest,
        "sources": [
            {
                "source_ref": item.source_ref,
                "venues": list(item.venues),
                "snapshot_ref": item.snapshot_ref,
                "source_digest": item.source_digest,
                "observed_at": item.observed_at.isoformat(),
                "trading_day": item.trading_day.isoformat(),
                "reported_update_at": (
                    None if item.reported_update_at is None else item.reported_update_at.isoformat()
                ),
                "retrieved_at": None if item.retrieved_at is None else item.retrieved_at.isoformat(),
                "source_row_count": item.source_row_count,
                "ranking_row_count": item.ranking_row_count,
                "excluded_row_count": item.excluded_row_count,
                "unmatched_row_count": item.unmatched_row_count,
                "ambiguous_row_count": item.ambiguous_row_count,
                "duplicate_row_count": item.duplicate_row_count,
                "completeness": item.completeness.value,
                "report_finalized": item.report_finalized,
                "latest_snapshot": item.latest_snapshot,
                "blockers": list(item.blockers),
            }
            for item in snapshot.sources
        ],
        "product_coverage": [
            {
                "product": item.product.to_dict(),
                "expected_instruments": [
                    _instrument_identity(value)
                    for value in item.expected_instruments
                ],
                "ranking_instruments": [
                    _instrument_identity(value)
                    for value in item.ranking_instruments
                ],
                "missing_instruments": [
                    _instrument_identity(value)
                    for value in item.missing_instruments
                ],
                "candidate_set_complete": item.candidate_set_complete,
                "ranking_values_complete": item.ranking_values_complete,
                "unmatched_source_row_count": item.unmatched_source_row_count,
                "duplicate_source_row_count": item.duplicate_source_row_count,
                "source_refs": list(item.source_refs),
                "blockers": list(item.blockers),
            }
            for item in snapshot.product_coverage
        ],
        "completeness": snapshot.completeness.value,
        "blockers": list(snapshot.blockers),
    }
    encoded = json.dumps(payload, ensure_ascii=True, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(encoded.encode("utf-8")).hexdigest()


def validate_eod_market_binding(market: Any, eod: Any, sdk: Any, error_type: Any) -> None:
    if market.request.instruments is not None:
        raise error_type("MARKET_CATALOG_BINDING_MISMATCH")
    catalog = eod.catalog
    market_venues = (
        None
        if market.request.venues is None
        else tuple(sorted(item.upper() for item in market.request.venues))
    )
    market_products = (
        None
        if market.request.products is None
        else tuple(
            sorted(
                market.request.products,
                key=lambda item: (item.venue, item.product_id),
            )
        )
    )
    if (
        catalog.market_snapshot_ref != market.receipt.batch_ref
        or catalog.market_source_ref != market.source_ref
        or catalog.market_generation != market.generation
        or catalog.market_records_digest != market.records_digest
        or catalog.environment != market.request.environment
        or catalog.venues != market_venues
        or catalog.catalog_records != market.catalog
    ):
        raise error_type("MARKET_CATALOG_BINDING_MISMATCH")
    family = market.coverage.catalog
    if (
        catalog.catalog_request_ref != family.request_ref
        or catalog.catalog_snapshot_ref != family.snapshot_ref
        or catalog.catalog_records_digest != family.records_digest
        or catalog.catalog_completeness is not family.completeness
        or catalog.catalog_scope_complete != family.scope_complete
    ):
        raise error_type("MARKET_CATALOG_BINDING_MISMATCH")
    if catalog.catalog_products != market_products:
        raise error_type("MARKET_CATALOG_BINDING_MISMATCH")
    product_universe = market.coverage.product_universe
    if (
        catalog.product_universe_request_ref
        != (None if product_universe is None else product_universe.request_ref)
        or catalog.product_universe_snapshot_ref
        != (None if product_universe is None else product_universe.snapshot_ref)
        or catalog.product_universe_records_digest
        != (None if product_universe is None else product_universe.records_digest)
        or catalog.product_universe_complete != market.coverage.product_universe_complete
        or catalog.product_universe
        != (
            market.products
            if market.coverage.product_universe_complete is True
            else None
        )
    ):
        raise error_type("MARKET_CATALOG_BINDING_MISMATCH")


def validate_eod_snapshot_lease(facade: Any, market: Any, lease: Any) -> bool:
    if facade is None or lease is None:
        return False
    try:
        return facade.validate_lease(market, lease, now=datetime.now(timezone.utc)) is True
    except Exception:
        return False


__all__ = [
    "eod_snapshot_manifest_digest",
    "validate_eod_market_binding",
    "validate_eod_snapshot_lease",
]
