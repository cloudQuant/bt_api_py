"""SDK composition of Gateway position receipts with Risk position facts.

This bridge does not issue receipts, own the journal or create account authority.
"""
from __future__ import annotations

from collections.abc import Mapping
from decimal import Decimal
from typing import Any

from bt_api_risk import AccountPositionRiskFact, AccountScope, PositionSnapshotCoverage


def verify_gateway_position_source_receipt(
    receipt: object,
    *,
    scope: AccountScope,
    snapshot: Mapping[str, Any],
    coverage: PositionSnapshotCoverage,
    facts: tuple[AccountPositionRiskFact, ...],
    summary_counts: Mapping[str, int],
    journal_error_type: type[Exception],
    sha256_text,
    canonical_json,
) -> None:
    """Revalidate the exact live owner snapshot before accepting detail rows."""

    try:
        from bt_api_base.account_protocol import AccountObservation
        from bt_api_gateway.account_position_snapshot import (
            GatewayFullPositionSnapshot,
            GatewayFullPositionSnapshotReceipt,
        )
    except ImportError:
        raise journal_error_type("GROSS_BASELINE_POSITION_SOURCE_UNAVAILABLE") from None
    if type(receipt) is not GatewayFullPositionSnapshotReceipt:
        raise journal_error_type("GROSS_BASELINE_POSITION_SOURCE_REQUIRED")
    try:
        receipt.assert_current()
    except Exception:
        raise journal_error_type("GROSS_BASELINE_POSITION_SOURCE_STALE") from None
    source = receipt.coverage
    observation = receipt.observation
    if (
        type(source) is not GatewayFullPositionSnapshot
        or type(observation) is not AccountObservation
        or source.observation is not observation
        or source.account_key_id != receipt.account_key.account_key_id
        or snapshot.get("position_account_key_id")
        != receipt.account_key.account_key_id
        or source.session_generation != receipt.session_generation
        or receipt.account_key.provider != scope.provider
        or receipt.account_key.environment != scope.environment
        or observation.account_key != receipt.account_key
        or not observation.positions_complete
        or observation.open_orders is None
        or not observation.open_orders_complete
        or observation.open_orders
    ):
        raise journal_error_type("GROSS_BASELINE_POSITION_SOURCE_MISMATCH")
    expected_coverage = PositionSnapshotCoverage(
        scope=scope,
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
    if coverage != expected_coverage:
        raise journal_error_type("GROSS_BASELINE_POSITION_SOURCE_MISMATCH")
    expected_rows = [item.to_dict() for item in observation.positions]
    if (
        snapshot.get("provider_positions") != expected_rows
        or snapshot.get("position_source_receipt_sha256") != receipt.fingerprint
    ):
        raise journal_error_type("GROSS_BASELINE_POSITION_SOURCE_MISMATCH")

    rows_by_digest: dict[str, Mapping[str, Any]] = {}
    for position in observation.positions:
        instrument = position.instrument
        if (
            instrument.provider != receipt.account_key.provider
            or instrument.venue != receipt.account_key.venue
            or instrument.environment != receipt.account_key.environment
            or instrument.product_type != position.resource.product_type
        ):
            raise journal_error_type("GROSS_BASELINE_POSITION_ACCOUNT_IDENTITY_MISMATCH")
        row = position.to_dict()
        digest = sha256_text(canonical_json(row))
        if digest in rows_by_digest:
            raise journal_error_type("GROSS_BASELINE_POSITION_SOURCE_DUPLICATE")
        rows_by_digest[digest] = row
    nonzero_rows = {
        digest: row
        for digest, row in rows_by_digest.items()
        if Decimal(str(row["quantity"])) != 0
    }
    facts_by_source: dict[str, AccountPositionRiskFact] = {}
    for fact in facts:
        if fact.position_source_record_sha256 in facts_by_source:
            raise journal_error_type("GROSS_BASELINE_POSITION_FACT_DUPLICATE")
        facts_by_source[fact.position_source_record_sha256] = fact
    if set(facts_by_source) != set(nonzero_rows):
        raise journal_error_type("GROSS_BASELINE_POSITION_FACTS_INCOMPLETE")

    facts_by_strategy: dict[str, list[AccountPositionRiskFact]] = {}
    for row_digest, row in nonzero_rows.items():
        fact = facts_by_source[row_digest]
        raw_quantity = Decimal(str(row["quantity"]))
        raw_side = str(row["side"])
        expected_side = (
            ("SHORT" if raw_quantity < 0 else "LONG")
            if raw_side == "net"
            else ("SHORT" if raw_side == "short" else "LONG")
        )
        instrument = row["instrument"]
        if not isinstance(instrument, Mapping) or (
            fact.instrument != instrument.get("instrument_id")
            or fact.quantity != abs(raw_quantity)
            or fact.quantity_unit != row.get("qty_unit")
            or fact.position_side != expected_side
        ):
            raise journal_error_type("GROSS_BASELINE_POSITION_FACT_SOURCE_MISMATCH")
        facts_by_strategy.setdefault(fact.strategy_id, []).append(fact)

    if summary_counts != {
        strategy_id: len(strategy_facts)
        for strategy_id, strategy_facts in facts_by_strategy.items()
    }:
        raise journal_error_type("GROSS_BASELINE_POSITION_SUMMARY_MISMATCH")
    summary_by_strategy = {
        row["strategy_id"]: row for row in snapshot["positions"]
    }
    if set(summary_by_strategy) != set(facts_by_strategy):
        raise journal_error_type("GROSS_BASELINE_POSITION_SUMMARY_MISMATCH")
    for strategy_id, strategy_facts in facts_by_strategy.items():
        ordered_facts = sorted(strategy_facts, key=lambda item: item.position_id)
        source_rows = [
            rows_by_digest[fact.position_source_record_sha256]
            for fact in ordered_facts
        ]
        summary = summary_by_strategy[strategy_id]
        if (
            summary["source_record_sha256"] != sha256_text(canonical_json(source_rows))
            or summary["valuation_facts_sha256"]
            != sha256_text(canonical_json([fact.to_payload() for fact in ordered_facts]))
        ):
            raise journal_error_type("GROSS_BASELINE_POSITION_SUMMARY_MISMATCH")


