"""Neutral futures main-contract selection decisions."""

from __future__ import annotations

from datetime import datetime
from decimal import Decimal
from typing import Any, Optional, Sequence

from .market_selection import _select_futures_market_snapshot
from .models import (
    ContractAssessment,
    DEFAULT_MAIN_CONTRACT_POLICY,
    ExchangeTradingCalendar,
    ExchangeTradingDay,
    MainContractManifest,
    MainContractPolicy,
    MainContractRankingSnapshot,
    ProductKey,
    ProductSelection,
    RANKING_MODE_FUTURES_MARKET_SNAPSHOT,
    TradingCalendarContractError,
    TradingDayReference,
    _aware_datetime,
    _canonical_json_sha256,
    _coverage_dict,
    _key_order,
)
from .selection import (
    _blocked_product,
    _calendar_evidence,
    _calendar_for,
    _candidate_assessment,
    _source_blockers,
    _source_digest,
    _source_text,
    _source_timestamp,
)


def select_main_contracts(
    snapshot: Optional[Any],
    expected_products: Sequence[ProductKey],
    current_trading_days: Sequence[ExchangeTradingDay],
    calendars: Sequence[ExchangeTradingCalendar],
    *,
    decision_at: datetime,
    policy: MainContractPolicy = DEFAULT_MAIN_CONTRACT_POLICY,
    snapshot_lease: Any = None,
    snapshot_facade: Any = None,
) -> MainContractManifest:
    """Select one future per target product, fail-closed.

    The input pool is product identities only, never contract IDs.  The
    legacy policy requires finalized latest prior-day rankings.  The explicit
    provider-neutral market snapshot policy consumes only exact SDK catalog,
    session, and quote DTOs. It uses each venue's own session trading day and
    requires the exact snapshot lease to validate before and after selection.
    Quote event timestamps are audit fields; they do not establish freshness.
    """

    _aware_datetime(decision_at, "decision_at")
    if not isinstance(policy, MainContractPolicy):
        raise ValueError("policy must be a MainContractPolicy")
    if policy.ranking_mode == RANKING_MODE_FUTURES_MARKET_SNAPSHOT:
        return _select_futures_market_snapshot(
            snapshot,
            expected_products,
            calendars,
            decision_at=decision_at,
            policy=policy,
            snapshot_lease=snapshot_lease,
            snapshot_facade=snapshot_facade,
        )
    if snapshot is not None and not isinstance(snapshot, MainContractRankingSnapshot):
        raise ValueError("snapshot must be MainContractRankingSnapshot or None")
    targets = tuple(expected_products)
    if any(not isinstance(item, ProductKey) for item in targets):
        raise ValueError("expected_products must contain ProductKey records")
    targets = tuple(sorted(targets, key=_key_order))
    target_ids = [_key_order(item) for item in targets]
    if len(set(target_ids)) != len(target_ids):
        raise ValueError("expected_products contains duplicate product keys")

    day_rows = tuple(current_trading_days)
    if any(not isinstance(item, ExchangeTradingDay) for item in day_rows):
        raise ValueError("current_trading_days must contain ExchangeTradingDay records")
    day_index = {}
    for item in day_rows:
        key = item.exchange_id
        if key in day_index:
            raise ValueError("current_trading_days contains duplicate exchanges")
        day_index[key] = item.trading_day

    calendar_rows = tuple(calendars)
    if any(not isinstance(item, ExchangeTradingCalendar) for item in calendar_rows):
        raise ValueError("calendars must contain ExchangeTradingCalendar records")
    calendar_index = {}
    for item in calendar_rows:
        if item.exchange_id in calendar_index:
            raise ValueError("calendars contains duplicate exchanges")
        calendar_index[item.exchange_id] = item

    source_blockers = _source_blockers(snapshot, decision_at)
    suppress_source_values = bool(source_blockers)
    target_by_id = {_key_order(item): item for item in targets}
    coverage_by_id = {}
    records_by_id = {}
    if snapshot is not None:
        for coverage in snapshot.coverage:
            key = _key_order(coverage.product)
            coverage_by_id.setdefault(key, []).append(coverage)
        for record in snapshot.candidates:
            records_by_id.setdefault(_key_order(record.product), []).append(record)

    all_product_ids = set(target_by_id)
    all_product_ids.update(coverage_by_id)
    all_product_ids.update(records_by_id)
    selections = []
    assessments = []
    day_references = {}
    used_calendars = {}

    for key_id in sorted(all_product_ids):
        key = target_by_id.get(key_id)
        if key is None:
            key = ProductKey(*key_id)
            out_of_scope_records = records_by_id.get(key_id, ())
            for record in out_of_scope_records:
                assessments.append(
                    ContractAssessment(
                        record.product,
                        record.instrument_id,
                        "OUT_OF_SCOPE",
                        "OUTSIDE_TARGET_POOL",
                        None,
                        None,
                        None,
                        None,
                        None,
                    )
                )
            selections.append(
                _blocked_product(
                    key,
                    expected_prior=None,
                    ranking_day=(
                        coverage_by_id[key_id][0].ranking_trading_day
                        if coverage_by_id.get(key_id)
                        else None
                    ),
                    blockers=(),
                    in_target_pool=False,
                )
            )
            continue

        blockers = list(source_blockers)
        calendar, calendar_blockers = _calendar_for(key, calendar_index, decision_at)
        blockers.extend(calendar_blockers)
        current_day = day_index.get(key.exchange_id)
        expected_prior = None
        if current_day is None:
            blockers.append("CURRENT_TRADING_DAY_UNAVAILABLE")
        elif calendar is not None and not calendar_blockers:
            try:
                expected_prior = calendar.previous_complete_trading_day(current_day)
            except TradingCalendarContractError as exc:
                blockers.append(exc.code)
        if current_day is not None:
            day_references[key.exchange_id] = TradingDayReference(
                key.exchange_id, current_day, expected_prior
            )
        if calendar is not None and not calendar_blockers:
            used_calendars[key.exchange_id] = _calendar_evidence(calendar)

        coverage_matches = coverage_by_id.get(key_id, [])
        coverage = coverage_matches[0] if len(coverage_matches) == 1 else None
        if not coverage_matches:
            blockers.append("PRODUCT_COVERAGE_UNAVAILABLE")
        elif len(coverage_matches) > 1:
            blockers.append("DUPLICATE_PRODUCT_COVERAGE")
        records = records_by_id.get(key_id, [])
        if coverage is not None:
            if not coverage.candidate_set_complete:
                blockers.append("CANDIDATE_SET_INCOMPLETE")
            if coverage.candidate_count != len(records):
                blockers.append("CANDIDATE_COUNT_MISMATCH")
            record_ids = [record.instrument_id for record in records]
            expected_ids = list(coverage.expected_instrument_ids)
            expected_id_keys = [instrument_id.casefold() for instrument_id in expected_ids]
            if any(
                not instrument_id or instrument_id != instrument_id.strip()
                for instrument_id in expected_ids
            ):
                blockers.append("EXPECTED_INSTRUMENT_ID_INVALID")
            if len(set(expected_id_keys)) != len(expected_id_keys):
                blockers.append("DUPLICATE_EXPECTED_INSTRUMENT_ID")
            if len(expected_ids) != coverage.candidate_count:
                blockers.append("EXPECTED_INSTRUMENT_ID_COUNT_MISMATCH")
            if set(expected_ids) != set(record_ids):
                blockers.append("CANDIDATE_ID_SET_MISMATCH")
            ranking_ids = list(coverage.ranking_instrument_ids)
            ranking_id_keys = [instrument_id.casefold() for instrument_id in ranking_ids]
            if len(set(ranking_id_keys)) != len(ranking_id_keys):
                blockers.append("DUPLICATE_RANKING_INSTRUMENT_ID")
            if not set(ranking_ids).issubset(set(expected_ids)):
                blockers.append("RANKING_ID_OUTSIDE_CANDIDATE_SET")
            if not coverage.ranking_values_complete:
                blockers.append("RANKING_VALUES_INCOMPLETE")
            if not coverage.finalized:
                blockers.append("RANKING_NOT_FINALIZED")
            if not coverage.latest_available:
                blockers.append("RANKING_SNAPSHOT_NOT_LATEST")
            if coverage.ranking_trading_day is None:
                blockers.append("RANKING_TRADING_DAY_UNAVAILABLE")
            elif expected_prior is not None and coverage.ranking_trading_day != expected_prior:
                code = (
                    "RANKING_STALE"
                    if coverage.ranking_trading_day < expected_prior
                    else "RANKING_DAY_LOOKAHEAD"
                )
                blockers.append(code)
            if not coverage.open_interest_unit or not coverage.volume_unit:
                blockers.append("RANKING_UNIT_UNAVAILABLE")
        canonical_record_ids = [item.instrument_id.casefold() for item in records]
        if len(set(canonical_record_ids)) != len(canonical_record_ids):
            blockers.append("DUPLICATE_CONTRACT_RECORD")

        group_assessments = []
        eligible = []
        for record in records:
            assessment, is_eligible = _candidate_assessment(
                record,
                current_trading_day=current_day,
                calendar=calendar if not calendar_blockers else None,
                policy=policy,
                suppress_source_values=suppress_source_values,
            )
            group_assessments.append(assessment)
            if assessment.status == "BLOCKED" and assessment.reason:
                blockers.append(assessment.reason)
            if is_eligible:
                eligible.append((record, assessment))

        if coverage is not None:
            ranking_ids = set(coverage.ranking_instrument_ids)
            eligible_ids = {record.instrument_id for record, _assessment in eligible}
            if not eligible_ids.issubset(ranking_ids):
                blockers.append("RANKING_ROW_MISSING_FOR_ELIGIBLE_CANDIDATE")

        blockers = list(dict.fromkeys(blockers))
        if not blockers and not eligible:
            blockers.append("NO_ELIGIBLE_MONTH_FUTURES")

        if blockers:
            final_assessments = []
            for assessment in group_assessments:
                if assessment.status == "ELIGIBLE":
                    assessment = ContractAssessment(
                        assessment.product,
                        assessment.instrument_id,
                        "BLOCKED",
                        blockers[0],
                        assessment.expiry_date,
                        assessment.trading_days_to_expiry,
                        assessment.open_interest,
                        assessment.volume,
                        None,
                        assessment.trading_days_to_expiry_lower_bound,
                        assessment.expiry_distance_exact,
                    )
                final_assessments.append(assessment)
            assessments.extend(final_assessments)
            selections.append(
                _blocked_product(
                    key,
                    expected_prior=expected_prior,
                    ranking_day=coverage.ranking_trading_day if coverage is not None else None,
                    blockers=blockers,
                )
            )
            continue

        ranked = sorted(
            eligible,
            key=lambda pair: (
                -Decimal(pair[1].open_interest),
                -Decimal(pair[1].volume),
                pair[1].expiry_date,
                pair[0].instrument_id,
            ),
        )
        rank_by_id = {
            record.instrument_id.casefold(): index for index, (record, _) in enumerate(ranked, 1)
        }
        ranked_assessments = []
        for assessment in group_assessments:
            rank = rank_by_id.get(assessment.instrument_id.casefold())
            if rank is not None:
                assessment = ContractAssessment(
                    assessment.product,
                    assessment.instrument_id,
                    "RANKED",
                    None,
                    assessment.expiry_date,
                    assessment.trading_days_to_expiry,
                    assessment.open_interest,
                    assessment.volume,
                    rank,
                    assessment.trading_days_to_expiry_lower_bound,
                    assessment.expiry_distance_exact,
                )
            ranked_assessments.append(assessment)
        assessments.extend(ranked_assessments)
        selections.append(
            ProductSelection(
                product=key,
                in_target_pool=True,
                status="READY",
                expected_prior_trading_day=expected_prior,
                ranking_trading_day=coverage.ranking_trading_day,
                selected_instrument_id=ranked[0][0].instrument_id,
                ranked_instrument_ids=tuple(record.instrument_id for record, _ in ranked),
                excluded_instrument_ids=tuple(
                    item.instrument_id for item in group_assessments if item.status == "EXCLUDED"
                ),
                blockers=(),
            )
        )

    target_selections = [item for item in selections if item.in_target_pool]
    manifest_blockers = list(source_blockers)
    manifest_blockers.extend(reason for item in target_selections for reason in item.blockers)
    if not targets:
        manifest_blockers.append("TARGET_PRODUCT_POOL_EMPTY")
    manifest_blockers = list(dict.fromkeys(manifest_blockers))
    status = (
        "READY"
        if targets and all(item.status == "READY" for item in target_selections)
        else "BLOCKED"
    )

    source_id = _source_text(snapshot.source_id) if snapshot is not None else None
    snapshot_id = _source_text(snapshot.snapshot_id) if snapshot is not None else None
    source_sha256 = _source_digest(snapshot.source_sha256) if snapshot is not None else None
    source_available_at = _source_timestamp(snapshot.available_at) if snapshot is not None else None
    source_retrieved_at = _source_timestamp(snapshot.retrieved_at) if snapshot is not None else None
    catalog_source_id = _source_text(snapshot.catalog_source_id) if snapshot is not None else None
    catalog_snapshot_id = (
        _source_text(snapshot.catalog_snapshot_id) if snapshot is not None else None
    )
    catalog_sha256 = _source_digest(snapshot.catalog_sha256) if snapshot is not None else None
    catalog_available_at = (
        _source_timestamp(snapshot.catalog_available_at) if snapshot is not None else None
    )
    catalog_retrieved_at = (
        _source_timestamp(snapshot.catalog_retrieved_at) if snapshot is not None else None
    )
    return MainContractManifest(
        status=status,
        decision_at=decision_at,
        policy=policy,
        target_products=targets,
        source_id=source_id,
        source_snapshot_id=snapshot_id,
        source_sha256=source_sha256,
        source_available_at=source_available_at,
        source_retrieved_at=source_retrieved_at,
        catalog_source_id=catalog_source_id,
        catalog_snapshot_id=catalog_snapshot_id,
        catalog_sha256=catalog_sha256,
        catalog_available_at=catalog_available_at,
        catalog_retrieved_at=catalog_retrieved_at,
        trading_days=tuple(day_references[key] for key in sorted(day_references)),
        calendars=tuple(used_calendars[key] for key in sorted(used_calendars)),
        coverage=tuple(
            sorted(
                snapshot.coverage if snapshot is not None else (),
                key=lambda item: (
                    _key_order(item.product),
                    _canonical_json_sha256(_coverage_dict(item)),
                ),
            )
        ),
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
    )


__all__ = ["select_main_contracts"]
