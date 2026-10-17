"""Pure N5 LOAD01 report aggregation from strategy and owner source facts.

This module deliberately does not import the runner or synthesize observations
from profile targets. It only indexes source facts into the report shape used by
the matrix consumer. Missing facts remain visible as INCOMPLETE.
"""

from __future__ import annotations

from collections.abc import Mapping
from math import isfinite
from typing import Any


_MISSING = object()
_COUNT_FIELDS = (
    "expected_logical_group_count",
    "observed_logical_group_count",
    "expected_child_dispatch_count",
    "observed_child_dispatch_count",
)


def _field(value: Any, name: str, default: Any = _MISSING) -> Any:
    if isinstance(value, Mapping):
        return value.get(name, default)
    return getattr(value, name, default)


def _index_mapping(
    value: Any, label: str, incomplete: list[str]
) -> dict[int, Any]:
    if not isinstance(value, Mapping):
        incomplete.append(f"{label}_not_mapping")
        return {}
    result: dict[int, Any] = {}
    for key, item in value.items():
        if type(key) is not int or key < 0:
            incomplete.append(f"{label}_index_invalid:{key!r}")
            continue
        result[key] = item
    return result


def _nonnegative_int(value: Any) -> bool:
    return type(value) is int and value >= 0


def _valid_duration(value: Any, *, allow_zero: bool) -> bool:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return False
    if not isfinite(value):
        return False
    return value >= 0 if allow_zero else value > 0


def _sum_or_none(values: list[int | None]) -> int | None:
    if not values or any(value is None for value in values):
        return None
    return sum(value for value in values if value is not None)


def build_n5_group_report(
    profile: Any,
    strategy_results: Mapping[int, Any],
    owner_totals: Mapping[int, Any],
    duration_seconds: int | float,
    actual_duration: int | float,
) -> dict[str, Any]:
    """Aggregate N5 strategy rows and raw owner facts into one report section.

    The returned object is assigned directly to
    accounts_and_execution["n5_group_metrics"]. Strategy metrics and owner
    evidence are retained by reference; only the index-to-report-key envelope
    and count summaries are constructed here.
    """

    incomplete: list[str] = []
    profile_id = _field(profile, "profile_id")
    if profile_id != "N5":
        incomplete.append("profile_is_not_n5")
    if not _valid_duration(duration_seconds, allow_zero=False):
        incomplete.append("planned_duration_invalid")
    if not _valid_duration(actual_duration, allow_zero=False):
        incomplete.append("actual_duration_invalid")

    raw_account_strategies = _field(profile, "strategy_indices_by_account")
    account_strategies: list[tuple[int, ...]] = []
    account_by_strategy: dict[int, int] = {}
    if not isinstance(raw_account_strategies, (tuple, list)) or not raw_account_strategies:
        incomplete.append("profile_strategy_account_topology_missing")
    else:
        for account_index, raw_indices in enumerate(raw_account_strategies):
            if not isinstance(raw_indices, (tuple, list)):
                incomplete.append(f"profile_account_strategy_indices_invalid:{account_index}")
                account_strategies.append(())
                continue
            indices: list[int] = []
            for index in raw_indices:
                if type(index) is not int or index < 0:
                    incomplete.append(f"profile_strategy_index_invalid:{account_index}")
                    continue
                if index in account_by_strategy:
                    incomplete.append(f"profile_strategy_account_ambiguous:{index}")
                    continue
                account_by_strategy[index] = account_index
                indices.append(index)
            account_strategies.append(tuple(indices))

    expected_indices = sorted(account_by_strategy)
    raw_group_capable = _field(profile, "expected_group_capable_strategies")
    group_capable: set[int] = set()
    if not isinstance(raw_group_capable, (tuple, list, set, frozenset)):
        incomplete.append("profile_group_capable_strategy_indices_missing")
    else:
        for index in raw_group_capable:
            if type(index) is not int or index not in account_by_strategy:
                incomplete.append(f"profile_group_capable_strategy_index_invalid:{index!r}")
            else:
                group_capable.add(index)
    group_owner_indices = sorted({account_by_strategy[index] for index in group_capable})
    if len(group_owner_indices) != len(account_strategies):
        incomplete.append("profile_group_capable_account_coverage_incomplete")

    strategies = _index_mapping(strategy_results, "strategy_results", incomplete)
    owners = _index_mapping(owner_totals, "owner_totals", incomplete)
    by_strategy: dict[str, Any] = {}
    metric_by_index: dict[int, Mapping[str, Any]] = {}
    expected_index_set = set(expected_indices)
    for index in expected_indices:
        stats = strategies.get(index, _MISSING)
        if stats is _MISSING:
            incomplete.append(f"strategy_result_missing:{index}")
            continue
        if not isinstance(stats, Mapping):
            incomplete.append(f"strategy_result_invalid:{index}")
            continue
        metrics = stats.get("n5_group_metrics", _MISSING)
        if not isinstance(metrics, Mapping):
            incomplete.append(f"strategy_n5_group_metrics_missing:{index}")
            continue
        by_strategy[f"strategy-{index:03d}"] = metrics
        metric_by_index[index] = metrics

    # Retain unexpected indexed source rows for diagnosis, while making the
    # profile mismatch explicit and excluding those rows from registered totals.
    for index in sorted(set(strategies) - expected_index_set):
        incomplete.append(f"strategy_result_outside_profile:{index}")
        stats = strategies[index]
        if isinstance(stats, Mapping):
            metrics = stats.get("n5_group_metrics")
            if isinstance(metrics, Mapping):
                by_strategy[f"strategy-{index:03d}"] = metrics

    def row_count(index: int, field: str) -> int | None:
        metrics = metric_by_index.get(index)
        if metrics is None:
            return None
        value = metrics.get(field, _MISSING)
        if not _nonnegative_int(value):
            incomplete.append(f"strategy_group_count_missing_or_invalid:{index}:{field}")
            return None
        return value

    def observed_detail_count(index: int, field: str) -> int | None:
        metrics = metric_by_index.get(index)
        observed = _field(metrics, "observed") if metrics is not None else _MISSING
        if not isinstance(observed, Mapping):
            incomplete.append(f"strategy_group_observed_detail_missing:{index}")
            return None
        value = observed.get(field, _MISSING)
        if not _nonnegative_int(value):
            incomplete.append(f"strategy_group_observed_detail_invalid:{index}:{field}")
            return None
        return value

    summary_fields = (*_COUNT_FIELDS, "observed_group_failure_count", "observed_group_unresolved_count")
    values_by_index: dict[int, dict[str, int | None]] = {}
    for index in expected_indices:
        values_by_index[index] = {
            "expected_logical_group_count": row_count(index, "expected_logical_group_count"),
            "observed_logical_group_count": row_count(index, "observed_logical_group_count"),
            "expected_child_dispatch_count": row_count(index, "expected_child_dispatch_count"),
            "observed_child_dispatch_count": row_count(index, "observed_child_dispatch_count"),
            "observed_group_failure_count": observed_detail_count(index, "group_failure_count"),
            "observed_group_unresolved_count": observed_detail_count(index, "group_unresolved_count"),
        }

    totals: dict[str, int | None] = {
        field: _sum_or_none([values_by_index[i][field] for i in expected_indices])
        for field in summary_fields
    }
    by_account: dict[str, Any] = {}
    for account_index, indices in enumerate(account_strategies):
        account_ref = f"account-{account_index:02d}"
        account_values = {
            field: _sum_or_none([values_by_index[index][field] for index in indices])
            for field in summary_fields
        }
        by_account[account_ref] = {
            "strategy_count": len(indices),
            "group_capable_strategy_indices": [index for index in indices if index in group_capable],
            **account_values,
        }

    provider_ledger_by_account: dict[str, Any] = {}
    intent_order_join_by_account: dict[str, Any] = {}
    owner_join_counts_by_account: dict[str, Any] = {}
    risk_reservations_by_account: dict[str, Any] = {}
    for account_index in group_owner_indices:
        # The report uses stable local aliases, while owner facts retain the
        # actual identities from the LOAD01 runner and gateway fixture.
        account_ref = f"account-{account_index:02d}"
        source_account_ref = f"gw02-account-{account_index:02d}"
        group_strategy_indices = [
            index
            for index in account_strategies[account_index]
            if index in group_capable
        ]
        if len(group_strategy_indices) != 1:
            incomplete.append(f"profile_group_strategy_identity_ambiguous:{account_index}")
            continue
        source_strategy_ref = f"load01-strategy-{group_strategy_indices[0]:03d}"
        owner = owners.get(account_index, _MISSING)
        if owner is _MISSING:
            incomplete.append(f"owner_total_missing:{account_index}")
            continue
        if not isinstance(owner, Mapping):
            incomplete.append(f"owner_total_invalid:{account_index}")
            continue
        if owner.get("account_ref") != source_account_ref:
            incomplete.append(f"owner_account_ref_mismatch:{account_index}")
            continue
        runtime = owner.get("n5_group_runtime", _MISSING)
        if not isinstance(runtime, Mapping):
            incomplete.append(f"owner_n5_group_runtime_missing:{account_index}")
            continue
        if runtime.get("account_ref") != source_account_ref:
            incomplete.append(f"owner_runtime_account_ref_mismatch:{account_index}")
        if runtime.get("strategy_ref") != source_strategy_ref:
            incomplete.append(f"owner_runtime_strategy_ref_mismatch:{account_index}")

        for field, destination in (
            ("provider_ledger", provider_ledger_by_account),
            ("intent_order_join", intent_order_join_by_account),
            ("risk_reservations", risk_reservations_by_account),
        ):
            raw_fact = runtime.get(field, _MISSING)
            if raw_fact is _MISSING:
                incomplete.append(f"owner_n5_group_fact_missing:{account_index}:{field}")
            else:
                if field == "provider_ledger":
                    if not isinstance(raw_fact, Mapping):
                        incomplete.append(f"owner_provider_ledger_invalid:{account_index}")
                    else:
                        if raw_fact.get("account_ref") != source_account_ref:
                            incomplete.append(f"owner_provider_ledger_account_ref_mismatch:{account_index}")
                        if raw_fact.get("strategy_ref") != source_strategy_ref:
                            incomplete.append(f"owner_provider_ledger_strategy_ref_mismatch:{account_index}")
                # Keep the source fact unchanged; shape/content validation is
                # the matrix consumer's responsibility.
                destination[account_ref] = raw_fact

        raw_counts: dict[str, Any] = {}
        for field in ("intent_order_join_count", "joined_intent_order_count"):
            raw_count = runtime.get(field, _MISSING)
            if raw_count is _MISSING:
                incomplete.append(f"owner_n5_group_fact_missing:{account_index}:{field}")
            else:
                raw_counts[field] = raw_count
        if raw_counts:
            owner_join_counts_by_account[account_ref] = raw_counts

    if not expected_indices:
        incomplete.append("profile_has_no_strategy_indices")
    # Deduplicate diagnostics without changing first-seen order.
    incomplete = list(dict.fromkeys(incomplete))
    complete = not incomplete
    return {
        "schema_version": 1,
        "evidence_level": "N5_STRATEGY_AND_OWNER_FACT_AGGREGATION_ONLY",
        "formal_acceptance_status": "NOT_CLAIMED_BY_REPORT_AGGREGATOR",
        "profile_id": profile_id if profile_id is not _MISSING else None,
        "planned_duration_seconds": duration_seconds,
        "actual_duration_seconds": actual_duration,
        "status": "SOURCE_FACTS_COMPLETE" if complete else "INCOMPLETE",
        "incomplete_reasons": incomplete,
        "summary": {
            "strategy_count": len(expected_indices),
            "group_capable_strategy_count": len(group_capable),
            "ordinary_strategy_count": max(0, len(expected_indices) - len(group_capable)),
            "counts_source": "per_strategy_n5_group_metrics_rows_only",
            "totals": totals,
            "by_account": by_account,
        },
        "by_strategy": by_strategy,
        "provider_ledger_by_account": provider_ledger_by_account,
        "intent_order_join_by_account": intent_order_join_by_account,
        "owner_join_counts_by_account": owner_join_counts_by_account,
        "risk_reservations_by_account": risk_reservations_by_account,
    }
