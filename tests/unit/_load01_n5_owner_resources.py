"""Actual per-owner N5 group proof resources for the LOAD01 runner overlay.

This module creates one account's proof/risk graph around the owner's existing
SQLite execution store.  ServiceHost remains the sole owner of its ordinary
and grouped execution writers and of the group-reservation service adapter.
"""

from __future__ import annotations

from contextlib import ExitStack
from collections import Counter
from dataclasses import dataclass
from decimal import Decimal
import json
from pathlib import Path
import sqlite3
from types import MappingProxyType
import time
from typing import Any

from bt_api_base.service_protocol import WireIdentity, WireRole, WireScope
from bt_api_execution import (
    ExecutionScope,
    OrderType,
    SqliteExecutionStore,
    risk_account_scope_fingerprint,
)
from bt_api_risk import (
    AccountScope,
    DispatchEvidenceClass,
    DurableRiskAdmissionAdapter,
    DurableRiskGate,
    ExposureAccountingMode,
    InstrumentRiskAdmissionMapper,
    InstrumentRiskMetadata,
    InstrumentRiskRegistry,
    RiskPolicy,
)
from bt_api_py.service_execution_group import GroupInstrumentRule
from bt_api_py.service_execution_group_authority import (
    ExecutionGroupAuthority,
    ExecutionStoreGroupCompletionVerifier,
)
from bt_api_py.service_execution_group_client import AcceptedGroupBindingStore
from bt_api_py.service_execution_group_proofs import (
    GroupProofJournal,
    GroupProofRuntimeBundle,
    GroupProofSource,
    GroupProofTarget,
    GroupReservationTarget as ProofReservationTarget,
)
from bt_api_py.service_group_reservations import GroupReservationTarget
from bt_api_py.service_registry import (
    TrustedGroupReservationPolicy,
    TrustedGroupResolutionPolicy,
    TrustedGroupWritePolicy,
)
from bt_api_py.service_risk_order_entry import RiskBtApiProviderContract, RiskOrderTarget
from bt_api_py.service_risk_order_journal import RiskOrderJournal
from tests.unit._load01_group_topology import (
    N5OwnerGroupResources,
    build_n5_owner_group_resources,
)
from tests.unit._load01_group_workload import (
    N5_BASE_UNIT_SCALES,
    N5_GROUP_QUANTITY_UNIT,
    N5_LEG_SPECS,
)
from tests.unit._service_group_provider_fixtures import (
    _V2_FILL_SCHEMA,
    _V2_ORDER_SCHEMA,
)


_GROUP_PORTFOLIO_REF = "load01-n5-portfolio-v1"
_GROUP_DOMAIN_REF = "paper:load01-synthetic"
_GROUP_PROVIDER_EXCHANGE = "FIXTURE___SPOT"
_GROUP_MAPPER_EXCHANGE = "OKX___SWAP"
_FULL_LOAD_SECONDS = 1_800
_METADATA_CLOSE_HEADROOM_SECONDS = 300
_N5_GROUPS_PER_ACCOUNT = 180
_N5_GROUP_CHILD_RESERVATIONS_PER_ACCOUNT = _N5_GROUPS_PER_ACCOUNT * len(N5_LEG_SPECS)
_N5_GROUP_NOTIONAL_PER_CYCLE = Decimal("262.9133")
_N5_ACCOUNT_GROUP_NOTIONAL_BUDGET = (
    _N5_GROUP_NOTIONAL_PER_CYCLE * _N5_GROUPS_PER_ACCOUNT
)
_N5_OWNER_CONTROL_POLL_SECONDS = 0.02


@dataclass(slots=True)
class N5OwnerGroupRuntime:
    """Per-account backing resources borrowed by this owner's ServiceHost."""

    topology: N5OwnerGroupResources
    route: dict[str, Any]
    risk_scope: AccountScope
    proof_scope: WireScope
    execution_scope: ExecutionScope
    metadata: tuple[InstrumentRiskMetadata, ...]
    provider_contract: RiskBtApiProviderContract
    risk_gate: DurableRiskGate
    proof_source: GroupProofSource
    proof_journal: GroupProofJournal
    _stack: ExitStack
    reservation_policy: TrustedGroupReservationPolicy
    submit_policy: TrustedGroupWritePolicy
    cancel_policy: TrustedGroupWritePolicy
    resolution_policy: TrustedGroupResolutionPolicy
    reservation_instance_id: str
    execution_authority: ExecutionGroupAuthority
    accepted_group_store: AcceptedGroupBindingStore
    provider_ledger_path: Path
    risk_gate_path: Path
    provider_query_counts: dict[str, Counter[str]]
    provider: Any
    bt_api: Any
    group_targets: dict[WireScope, ExecutionScope]
    dispatch: Any
    admission: Any
    _closed: bool = False
    _close_facts: dict[str, Any] | None = None

    @property
    def ready(self) -> bool:
        # The owner resource stack is published only after every typed Risk,
        # proof, provider, journal, and topology resource has been built.
        # DurableRiskGate deliberately has no public ``ready`` attribute;
        # this predicate reports this helper's owned lifetime, while each
        # ServiceHost adapter checks its own component readiness separately.
        return not self._closed

    def facts_snapshot(self) -> dict[str, Any]:
        runtime_facts = self._close_facts
        if runtime_facts is None:
            runtime_facts = self._runtime_facts_snapshot()
        return {**runtime_facts, "state": "closed" if self._closed else "ready"}

    def _runtime_facts_snapshot(self) -> dict[str, Any]:
        risk_reservations = self._risk_reservation_snapshot()
        provider_ledger = self._provider_ledger_snapshot(risk_reservations)
        intent_order_join = provider_ledger.pop("intent_order_join")
        return {
            "account_ref": self.proof_scope.account_ref,
            "strategy_ref": self.proof_scope.strategy_ref,
            "strategy_index": self.route["strategy_index"],
            "proof_target_count": len(self.proof_source.targets),
            "metadata_versions": tuple(item.metadata_version for item in self.metadata),
            "metadata_expiry_ns": tuple(item.expires_at_ns for item in self.metadata),
            "risk_scope_fingerprint": self.route["risk_scope_fingerprint"],
            "provider_ledger": provider_ledger,
            "intent_order_join": intent_order_join,
            "intent_order_join_count": len(intent_order_join),
            "joined_intent_order_count": sum(
                item["join_state"] == "joined" for item in intent_order_join
            ),
            "risk_reservations": risk_reservations,
        }

    def _provider_ledger_snapshot(
        self, risk_facts: dict[str, Any]
    ) -> dict[str, Any]:
        write_ids, query_counts = self.provider.snapshot()
        with sqlite3.connect(self.provider_ledger_path) as connection:
            connection.execute("PRAGMA query_only = ON")
            rows = connection.execute(
                "SELECT client_order_id, order_json FROM provider_orders "
                "ORDER BY client_order_id"
            ).fetchall()
        order_rows = {
            str(client_order_id): json.loads(str(order_json))
            for client_order_id, order_json in rows
        }
        counts = Counter(str(client_order_id) for client_order_id in write_ids)
        writes_by_id = [
            {
                "client_order_id": client_order_id,
                "write_count": count,
                "order_status": order_rows.get(client_order_id, {}).get("status"),
                "filled": order_rows.get(client_order_id, {}).get("filled"),
                "quantity": order_rows.get(client_order_id, {}).get("quantity"),
                "order_query_count": self.provider_query_counts.get(
                    client_order_id, Counter()
                ).get("order_queries", 0),
                "fill_query_count": self.provider_query_counts.get(
                    client_order_id, Counter()
                ).get("fill_queries", 0),
            }
            for client_order_id, count in sorted(counts.items())
        ]
        status_counts = Counter(
            str(order.get("status")) for order in order_rows.values()
        )
        intent_order_join: list[dict[str, Any]] = []
        linked_client_ids: list[str] = []
        for group in risk_facts["groups"]:
            for leg in group["legs"]:
                events = self.proof_journal.events(
                    self.risk_scope.key, leg["intent_id"]
                )
                attempts = [
                    event for event in events if event.event_type == "ATTEMPT_STARTED"
                ]
                attempt = attempts[0] if attempts else None
                payload = {} if attempt is None else dict(attempt.payload)
                request = payload.get("provider_request")
                raw_client_order_id = payload.get("client_order_id")
                client_order_id = (
                    raw_client_order_id
                    if type(raw_client_order_id) is str and raw_client_order_id
                    else None
                )
                attempt_binding_valid = attempt is not None and not (
                    attempt.group_id != group["group_id"]
                    or attempt.leg_id != leg["leg_id"]
                    or attempt.intent_id != leg["intent_id"]
                    or type(request) is not dict
                    or request.get("client_order_id") != raw_client_order_id
                    or client_order_id is None
                )
                if client_order_id is not None:
                    linked_client_ids.append(client_order_id)
                provider_write_count = (
                    0 if client_order_id is None else counts.get(client_order_id, 0)
                )
                order_row_present = (
                    client_order_id in order_rows
                    if client_order_id is not None
                    else False
                )
                join_state = (
                    "attempt_missing"
                    if attempt is None
                    else "duplicate_attempt_started"
                    if len(attempts) != 1
                    else "invalid_attempt_binding"
                    if not attempt_binding_valid
                    else "missing_provider_write"
                    if provider_write_count == 0
                    else "duplicate_provider_write"
                    if provider_write_count != 1
                    else "missing_provider_order_record"
                    if not order_row_present
                    else "joined"
                )
                intent_order_join.append(
                    {
                        "group_id": group["group_id"],
                        "leg_id": leg["leg_id"],
                        "intent_id": leg["intent_id"],
                        "attempt_started_count": len(attempts),
                        "attempt_started_revision": None if attempt is None else attempt.revision,
                        "attempt_started_record_sha256": None
                        if attempt is None
                        else attempt.record_sha256,
                        "attempt_binding_valid": attempt_binding_valid,
                        "client_order_id": client_order_id,
                        "provider_write_count": provider_write_count,
                        "provider_order_row_present": order_row_present,
                        "join_state": join_state,
                        "order_status": order_rows.get(client_order_id, {}).get("status")
                        if client_order_id is not None
                        else None,
                        "order_query_count": 0
                        if client_order_id is None
                        else self.provider_query_counts.get(
                            client_order_id, Counter()
                        ).get("order_queries", 0),
                        "fill_query_count": 0
                        if client_order_id is None
                        else self.provider_query_counts.get(
                            client_order_id, Counter()
                        ).get("fill_queries", 0),
                    }
                )
        duplicate_join_client_order_ids = sorted(
            client_order_id
            for client_order_id, count in Counter(linked_client_ids).items()
            if count > 1
        )
        unjoined_client_order_ids = sorted(set(counts) - set(linked_client_ids))
        return {
            "source": "synthetic-feed-sqlite-provider-ledger",
            "attribution": "n5_group_proof_runtime_only",
            "account_ref": self.proof_scope.account_ref,
            "strategy_ref": self.proof_scope.strategy_ref,
            "strategy_index": self.route["strategy_index"],
            "make_order_write_count": len(write_ids),
            "provider_order_count": len(order_rows),
            "unique_client_order_id_count": len(counts),
            "duplicate_client_order_id_count": sum(count - 1 for count in counts.values()),
            "write_without_order_record_count": sum(
                client_order_id not in order_rows for client_order_id in counts
            ),
            "order_record_without_write_count": sum(
                client_order_id not in counts for client_order_id in order_rows
            ),
            "order_status_counts": dict(sorted(status_counts.items())),
            "order_query_count": query_counts.get("order_queries"),
            "fill_query_count": query_counts.get("fill_queries"),
            "writes_by_client_order_id": writes_by_id,
            "intent_order_join": intent_order_join,
            "unjoined_write_count": len(unjoined_client_order_ids),
            "unjoined_make_order_write_count": sum(
                counts[client_order_id] for client_order_id in unjoined_client_order_ids
            ),
            "unjoined_client_order_ids": unjoined_client_order_ids,
            "duplicate_join_client_order_id_count": len(
                duplicate_join_client_order_ids
            ),
            "duplicate_join_client_order_ids": duplicate_join_client_order_ids,
            "unjoined_query_count": sum(
                count
                for client_order_id, operations in self.provider_query_counts.items()
                if client_order_id not in set(linked_client_ids)
                for count in operations.values()
            ),
        }

    def _risk_reservation_snapshot(self) -> dict[str, Any]:
        with sqlite3.connect(self.risk_gate_path) as connection:
            connection.execute("PRAGMA query_only = ON")
            group_ids = tuple(
                str(row[0])
                for row in connection.execute(
                    "SELECT DISTINCT group_id FROM risk_reservations "
                    "WHERE scope_key=? ORDER BY group_id",
                    (self.risk_scope.key,),
                )
            )
        state_counts: Counter[str] = Counter()
        groups: list[dict[str, Any]] = []
        intent_ids: list[str] = []
        claim_count = 0
        resolved_count = 0
        leg_count = 0
        for group_id in group_ids:
            receipt = self.risk_gate.read_group_vector_snapshot(self.risk_scope, group_id)
            legs = []
            for leg in receipt.legs:
                state = str(leg.state.value)
                state_counts[state] += 1
                claim_count += int(leg.claimed)
                resolved_count += int(leg.resolved)
                leg_count += 1
                intent_ids.append(leg.intent_id)
                legs.append(
                    {
                        "leg_id": leg.leg_id,
                        "permit_id": leg.permit_id,
                        "intent_id": leg.intent_id,
                        "state": state,
                        "claimed": leg.claimed,
                        "resolved": leg.resolved,
                    }
                )
            groups.append({"group_id": receipt.group_id, "legs": legs})
        return {
            "group_count": len(groups),
            "leg_count": leg_count,
            "unique_intent_id_count": len(set(intent_ids)),
            "duplicate_intent_id_count": len(intent_ids) - len(set(intent_ids)),
            "claimed_leg_count": claim_count,
            "resolved_leg_count": resolved_count,
            "leg_state_counts": dict(sorted(state_counts.items())),
            "groups": groups,
        }

    def close(self) -> None:
        if self._closed:
            return
        if self._close_facts is None:
            self._close_facts = self._runtime_facts_snapshot()
        self._stack.close()
        self._closed = True


def wait_for_owner_control_command(
    control: Any,
    account_host: Any,
    execution_host: Any,
    *,
    timeout_seconds: float = 300.0,
    poll_interval_seconds: float = _N5_OWNER_CONTROL_POLL_SECONDS,
) -> Any:
    """Wait on control while servicing both Hosts on their owning thread.

    ServiceHost.serve_once performs its own due upstream-lease maintenance,
    then polls at most one inbound request. This loop keeps registrations and
    renewals live while the controller waits for coordinator readiness or
    Monitor setup, without creating a second Host thread.
    """

    if (
        type(timeout_seconds) not in (int, float)
        or timeout_seconds <= 0
        or type(poll_interval_seconds) not in (int, float)
        or poll_interval_seconds <= 0
        or not callable(getattr(control, "poll", None))
        or not callable(getattr(control, "recv", None))
        or not callable(getattr(account_host, "serve_once", None))
        or not callable(getattr(execution_host, "serve_once", None))
    ):
        raise ValueError("LOAD01 N5 owner control pump inputs are invalid")
    deadline = time.monotonic() + float(timeout_seconds)
    while True:
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            raise TimeoutError("LOAD01 N5 owner control phase timed out")
        if control.poll(min(float(poll_interval_seconds), remaining)):
            return control.recv()
        account_host.serve_once(timeout_ms=0)
        execution_host.serve_once(timeout_ms=0)


def _metadata_is_n5_run_bound(
    metadata: tuple[InstrumentRiskMetadata, ...], *, now_ns: int
) -> bool:
    if (
        type(metadata) is not tuple
        or len(metadata) != len(N5_LEG_SPECS)
        or any(type(item) is not InstrumentRiskMetadata for item in metadata)
    ):
        return False
    expected_expiry_floor = now_ns + (
        _FULL_LOAD_SECONDS + _METADATA_CLOSE_HEADROOM_SECONDS
    ) * 1_000_000_000
    as_of_values = {item.as_of_ns for item in metadata}
    expiry_values = {item.expires_at_ns for item in metadata}
    if len(as_of_values) != 1 or len(expiry_values) != 1:
        return False
    shared_as_of = next(iter(as_of_values))
    if type(shared_as_of) is not int or shared_as_of <= 0 or shared_as_of > now_ns:
        return False
    for spec, item in zip(N5_LEG_SPECS, metadata, strict=True):
        if (
            item.instrument != spec.instrument
            or item.metadata_version != f"load01-n5-{spec.instrument.lower()}-v1"
            or item.tick_size != Decimal("0.01")
            or item.quantity_step != spec.quantity_step
            or item.contract_multiplier != spec.contract_multiplier
            or item.max_gross_notional != Decimal("100000")
            or item.taker_fee_bps != spec.taker_fee_bps
            or item.fixed_fee != spec.fixed_fee
            or item.max_slippage_bps != spec.max_slippage_bps
            or item.quantity_unit != N5_GROUP_QUANTITY_UNIT
            or item.valuation_unit != "USD"
            or item.quote_currency != "USD"
            or item.expires_at_ns < expected_expiry_floor
        ):
            return False
    return True


def _side_order_type(side: object) -> str:
    return "buy-limit" if str(getattr(side, "value", side)).lower() == "buy" else "sell-limit"


def _database_path(execution_store_path: str | Path, suffix: str) -> Path:
    source = Path(execution_store_path)
    return source.with_name(f"{source.stem}.n5-{suffix}.sqlite3")


def build_n5_owner_group_runtime(
    *,
    account_index: int,
    account_ref: str,
    strategy_index: int,
    strategy_ref: str,
    origin_strategy_run_id: str,
    execution_scope: ExecutionScope,
    ordinary_targets: dict[WireScope, ExecutionScope],
    portfolio_ref: str,
    reservation_scope_ref: str,
    order_scope_ref: str,
    query_scope_ref: str,
    coordinator_identity: tuple[str, str],
    reservation_instance_id: str,
    execution_instance_id: str,
    metadata: tuple[InstrumentRiskMetadata, ...],
    execution_store: SqliteExecutionStore,
    execution_store_path: str | Path,
    dispatch: object,
    admission: object,
) -> N5OwnerGroupRuntime:
    """Build one account's typed N5 group resources without starting a Host.

    Provider proof continues through the committed OKX mapper, BtApi, the
    DIM09 three-leg synthetic feed, typed Risk admission and SQLite journals.
    Each owner gets its own risk/proof SQLite files and one group proof scope.
    """

    if (
        type(account_index) is not int
        or not 0 <= account_index < 10
        or type(account_ref) is not str
        or not account_ref
        or type(strategy_index) is not int
        or strategy_index != account_index * 10
        or type(strategy_ref) is not str
        or not strategy_ref
        or type(execution_scope) is not ExecutionScope
        or execution_scope.provider != "fixture"
        or execution_scope.environment != "simulation"
        or execution_scope.account_ref != account_ref
        or execution_scope.strategy_id != strategy_ref
        or not isinstance(ordinary_targets, dict)
        or not ordinary_targets
        or len(ordinary_targets) != 10
        or any(scope.portfolio_ref is not None for scope in ordinary_targets)
        or ordinary_targets.get(
            WireScope(
                _GROUP_DOMAIN_REF,
                account_ref=account_ref,
                strategy_ref=strategy_ref,
            )
        )
        != execution_scope
        or portfolio_ref != _GROUP_PORTFOLIO_REF
        or reservation_scope_ref != f"load01-n5-reservation-scope-a{account_index:02d}-v1"
        or order_scope_ref != f"load01-execution-orders-{account_index:02d}-v1"
        or query_scope_ref != f"load01-execution-query-{account_index:02d}-v1"
        or type(coordinator_identity) is not tuple
        or len(coordinator_identity) != 2
        or any(type(value) is not str or not value for value in coordinator_identity)
        or type(origin_strategy_run_id) is not str
        or not origin_strategy_run_id
        or type(execution_instance_id) is not str
        or not execution_instance_id
        or type(reservation_instance_id) is not str
        or not reservation_instance_id
        or type(execution_store) is not SqliteExecutionStore
        or dispatch is None
        or not isinstance(admission, dict)
        or set(admission) != {"order_gate", "cancel_gate"}
        or admission["order_gate"] is None
        or admission["cancel_gate"] is None
    ):
        raise ValueError("LOAD01 N5 owner resource input contract is invalid")
    now_ns = time.time_ns()
    if not _metadata_is_n5_run_bound(metadata, now_ns=now_ns):
        raise ValueError("LOAD01 N5 risk metadata does not cover the full run window")

    group_scope = WireScope(
        _GROUP_DOMAIN_REF,
        account_ref=account_ref,
        strategy_ref=strategy_ref,
        portfolio_ref=portfolio_ref,
    )
    group_targets = dict(ordinary_targets)
    group_targets[group_scope] = execution_scope
    risk_scope = AccountScope(
        execution_scope.provider,
        account_ref,
        execution_scope.environment,
    )
    provider_contract = RiskBtApiProviderContract(
        exchange_name=_GROUP_PROVIDER_EXCHANGE,
        profile_id="service-group-provider-v2",
        order_schema=_V2_ORDER_SCHEMA,
        fill_schema=_V2_FILL_SCHEMA,
        client_order_id_query=True,
        client_order_id_query_unique=True,
        complete_order_snapshot=True,
        complete_fill_history=True,
        provider_accepts_client_order_id=True,
        provider_status_mapping=(
            ("cancelled", "canceled"),
            ("expired", "expired"),
            ("filled", "completed"),
            ("open", "accepted"),
            ("partial", "partial"),
            ("rejected", "rejected"),
        ),
        supported_order_types=("LIMIT",),
        supported_time_in_force=("IOC",),
        supported_position_effects=("OPEN",),
        normalizer_schema="bt-api-order-source-provenance-v2",
        order_revision_field="order_revision",
        fill_revision_field="fill_revision",
        fill_watermark_field="fill_watermark",
        fee_amount_field="fee_amount",
        fee_currency_field="fee_currency",
        quote_currency_by_instrument=tuple(
            sorted((item.instrument, item.quote_currency or "USD") for item in metadata)
        ),
    )

    stack = ExitStack()
    try:
        from bt_api_py._venue_mappers import MAPPERS
        from tests.integration import _dim09_three_leg_fixtures as dim09

        had_fixture_mapper = _GROUP_PROVIDER_EXCHANGE in MAPPERS
        previous_fixture_mapper = MAPPERS.get(_GROUP_PROVIDER_EXCHANGE)
        okx_mapper = MAPPERS.get(_GROUP_MAPPER_EXCHANGE)
        if not callable(okx_mapper):
            raise RuntimeError("LOAD01 N5 committed OKX mapper is unavailable")
        MAPPERS[_GROUP_PROVIDER_EXCHANGE] = okx_mapper

        def restore_fixture_mapper() -> None:
            if had_fixture_mapper:
                MAPPERS[_GROUP_PROVIDER_EXCHANGE] = previous_fixture_mapper
            else:
                MAPPERS.pop(_GROUP_PROVIDER_EXCHANGE, None)

        stack.callback(restore_fixture_mapper)

        expected_orders = tuple(
            (spec.instrument, spec.quantity, spec.price, _side_order_type(spec.side))
            for _cycle in range(_N5_GROUPS_PER_ACCOUNT)
            for spec in N5_LEG_SPECS
        )
        # DIM09's committed constructor validates a single three-leg cycle.
        # Keep that typed validation, then extend only its ordered expected
        # terms for N5's finite 180-group schedule.  The feed's actual
        # make_order path still checks every mapped provider call in sequence.
        provider_ledger_path = _database_path(
            execution_store_path, f"provider-a{account_index:02d}"
        )
        feed = dim09._ThreeLegMappedFeed(
            str(provider_ledger_path),
            ("filled",) * _N5_GROUP_CHILD_RESERVATIONS_PER_ACCOUNT,
            account_ref,
            expected_provider_orders=expected_orders[: len(N5_LEG_SPECS)],
            expected_size_in_contracts=True,
            expected_position_mode=None,
        )
        feed._expected_provider_orders = expected_orders
        provider_query_counts: dict[str, Counter[str]] = {}
        original_query_order = feed.query_order
        original_get_deals = feed.get_deals

        def record_provider_query(operation: str, client_order_id: object) -> None:
            key = client_order_id if type(client_order_id) is str else "<missing-client-order-id>"
            provider_query_counts.setdefault(key, Counter())[operation] += 1

        def observed_query_order(*args: Any, **kwargs: Any) -> dict[str, Any]:
            client_order_id = kwargs.get("client_order_id", kwargs.get("order_id"))
            if client_order_id is None and len(args) > 1:
                client_order_id = args[1]
            record_provider_query("order_queries", client_order_id)
            return original_query_order(*args, **kwargs)

        def observed_get_deals(*args: Any, **kwargs: Any) -> dict[str, Any]:
            record_provider_query("fill_queries", kwargs.get("client_order_id"))
            return original_get_deals(*args, **kwargs)

        feed.query_order = observed_query_order
        feed.get_deals = observed_get_deals
        with dim09._synthetic_plugin_discovery():
            bt_api = dim09.BtApi(debug=False)
        stack.callback(bt_api.close)
        bt_api.exchange_feeds[_GROUP_PROVIDER_EXCHANGE] = feed

        registry = InstrumentRiskRegistry(metadata)
        risk_target = RiskOrderTarget(
            account_scope=risk_scope,
            strategy_id=strategy_ref,
            exchange_name=_GROUP_PROVIDER_EXCHANGE,
            provider_account_id=account_ref,
            registry=registry,
        )
        risk_order_journal = RiskOrderJournal(
            _database_path(execution_store_path, f"risk-order-a{account_index:02d}"),
            writer_id=f"load01-n5-risk-order-a{account_index:02d}-v1",
        )
        proof_journal = GroupProofJournal(
            _database_path(execution_store_path, f"proof-a{account_index:02d}"),
            writer_id=f"load01-n5-proof-a{account_index:02d}-v1",
        )
        stack.callback(proof_journal.close)
        proof_target = GroupProofTarget(
            execution_scope=execution_scope,
            risk_target=risk_target,
            provider_contract=provider_contract,
            bt_api=bt_api,
            execution_store=execution_store,
            risk_order_journal=risk_order_journal,
            evidence_class=DispatchEvidenceClass.SIMULATION_JOURNAL,
        )
        proof_source = GroupProofSource(proof_journal, {group_scope: proof_target})
        risk_gate_path = _database_path(
            execution_store_path, f"risk-gate-a{account_index:02d}"
        )
        risk_gate = DurableRiskGate(
            risk_gate_path,
            RiskPolicy(
                f"load01-n5-risk-policy-a{account_index:02d}-v1",
                _N5_ACCOUNT_GROUP_NOTIONAL_BUDGET,
                max_increase_count=_N5_GROUP_CHILD_RESERVATIONS_PER_ACCOUNT,
                permit_ttl_seconds=240,
                require_strategy_allocation=False,
                notional_unit="USD",
                exposure_accounting_mode=ExposureAccountingMode.RESERVATIONS_ONLY,
            ),
            execution_journal_authority=proof_source,
        )
        stack.callback(risk_gate.close)
        risk_gate.set_strategy_allocation(
            risk_scope,
            strategy_ref,
            f"load01-n5-allocation-a{account_index:02d}-v1",
            max_notional=_N5_ACCOUNT_GROUP_NOTIONAL_BUDGET,
            notional_unit="USD",
        )
        risk_admission = DurableRiskAdmissionAdapter(
            risk_gate,
            InstrumentRiskAdmissionMapper(
                risk_scope,
                registry,
                clock_ns=time.time_ns,
                allocation_reader=lambda routed_scope: risk_gate.get_strategy_allocation(
                    risk_scope, routed_scope.strategy_id
                ),
                wire_account_ref=account_ref,
            ),
        )
        account_admission = dim09.AccountScopedGroupAdmissionAdapter(
            risk_admission, account_scope=risk_scope
        )
        reservation_target = GroupReservationTarget(risk_scope, risk_admission)
        proof_reservation_target = ProofReservationTarget(risk_scope, risk_admission)
        bundle = GroupProofRuntimeBundle(
            risk_admission,
            account_admission,
            MappingProxyType({group_scope: proof_reservation_target}),
            MappingProxyType({group_scope: proof_target}),
            proof_source,
        )
        bundle.require_runtime_bundle(
            account_scoped_admission=account_admission,
            risk_admission=risk_admission,
        )
        accepted_group_store = AcceptedGroupBindingStore(
            _database_path(execution_store_path, f"accepted-group-a{account_index:02d}")
        )
        stack.callback(accepted_group_store.close)
        execution_authority = ExecutionGroupAuthority(
            _database_path(execution_store_path, f"group-authority-a{account_index:02d}"),
            completion_verifier=ExecutionStoreGroupCompletionVerifier(
                execution_store, execution_scopes=(execution_scope,)
            ),
        )
        stack.callback(execution_authority.close)

        coordinator_principal, coordinator_service_id = coordinator_identity
        coordinator_identity_value = WireIdentity(
            WireRole.SERVICE,
            coordinator_principal,
            coordinator_service_id,
        )
        reservation_policy = TrustedGroupReservationPolicy(
            reservation_instance_id,
            coordinator_principal,
            coordinator_identity_value,
            reservation_scope_ref,
            group_scope,
            origin_strategy_run_id,
            account_ref,
        )
        submit_policy = TrustedGroupWritePolicy(
            execution_instance_id,
            coordinator_principal,
            coordinator_identity_value,
            order_scope_ref,
            group_scope,
            origin_strategy_run_id,
        )
        cancel_policy = TrustedGroupWritePolicy(
            execution_instance_id,
            coordinator_principal,
            coordinator_identity_value,
            order_scope_ref,
            group_scope,
            origin_strategy_run_id,
            operation="orders.cancel_group_leg",
        )
        resolution_policy = TrustedGroupResolutionPolicy(
            execution_instance_id,
            coordinator_principal,
            coordinator_identity_value,
            order_scope_ref,
            group_scope,
            origin_strategy_run_id,
            account_ref,
        )
        topology = build_n5_owner_group_resources(
            store=execution_store,
            targets=group_targets,
            dispatch=dispatch,
            admission=admission,
            accepted_group_store=accepted_group_store,
            group_authority=execution_authority.guard,
            group_runtime_bundle=bundle,
        )
        base_scales = dict(N5_BASE_UNIT_SCALES)
        instruments = {
            item.instrument: GroupInstrumentRule(
                metadata=item,
                base_units_per_order_unit=base_scales[item.instrument],
                allowed_order_types=frozenset({OrderType.LIMIT}),
            )
            for item in metadata
        }
        route = {
            "account_ref": account_ref,
            "portfolio_ref": portfolio_ref,
            "strategy_ref": strategy_ref,
            "strategy_index": strategy_index,
            "scope_ref": f"load01-n5-group-scope-a{account_index:02d}-v1",
            "execution_scope": execution_scope,
            "owner_scope": group_scope,
            "risk_scope_fingerprint": risk_account_scope_fingerprint(risk_scope.key),
            "order_scope_ref": order_scope_ref,
            "query_scope_ref": query_scope_ref,
            "reservation_scope_ref": reservation_scope_ref,
            "origin_strategy_run_id": origin_strategy_run_id,
            "reservation_instance_id": reservation_instance_id,
            "instruments": instruments,
        }
        runtime = N5OwnerGroupRuntime(
            topology=topology,
            route=route,
            risk_scope=risk_scope,
            proof_scope=group_scope,
            execution_scope=execution_scope,
            metadata=metadata,
            provider_contract=provider_contract,
            risk_gate=risk_gate,
            proof_source=proof_source,
            proof_journal=proof_journal,
            _stack=stack,
            reservation_policy=reservation_policy,
            submit_policy=submit_policy,
            cancel_policy=cancel_policy,
            resolution_policy=resolution_policy,
            reservation_instance_id=reservation_instance_id,
            execution_authority=execution_authority,
            accepted_group_store=accepted_group_store,
            provider_ledger_path=provider_ledger_path,
            risk_gate_path=risk_gate_path,
            provider_query_counts=provider_query_counts,
            provider=feed,
            bt_api=bt_api,
            group_targets=group_targets,
            dispatch=dispatch,
            admission=admission,
        )
        return runtime
    except BaseException:
        stack.close()
        raise


__all__ = [
    "N5OwnerGroupRuntime",
    "build_n5_owner_group_runtime",
    "wait_for_owner_control_command",
    "_N5_GROUP_CHILD_RESERVATIONS_PER_ACCOUNT",
    "_N5_ACCOUNT_GROUP_NOTIONAL_BUDGET",
]
