"""Synthetic producer-to-Risk coverage for full position revaluation."""

from __future__ import annotations

from decimal import Decimal
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
from enum import Enum
import hashlib
import json
from pathlib import Path
import time
from types import SimpleNamespace
from typing import Optional

import pytest

from bt_api_risk import (
    AccountScope,
    DurableRiskGate,
    ExposureAccountingMode,
    InstrumentRiskAdmissionMapper,
    InstrumentRiskFxRate,
    InstrumentRiskMetadata,
    InstrumentRiskRegistry,
    RiskDeniedError,
    RiskExposureKind,
    RiskExposureLimit,
    RiskPolicy,
)
from bt_api_base.account_protocol import (
    AccountKey,
    AccountObservation,
    AccountPosition,
    AccountResourceKey,
)
from bt_api_base.market_protocol import MarketInstrumentKey
from bt_api_gateway import GatewayEventJournal
from bt_api_gateway.account_position_snapshot import (
    GatewayFullPositionSnapshot,
    position_rows_sha256,
    resource_coverage_sha256,
)
from bt_api_gateway.account_session_worker import GatewayAccountSessionWorker
from bt_api_gateway.session_guard import LocalAccountSessionGuard
from bt_api_py.service_risk_order_journal import (
    RiskOrderJournal,
    RiskOrderJournalError,
)
from bt_api_py.service_risk_position_snapshot import (
    RiskPositionSnapshotOwner,
    RiskPositionSnapshotProducer,
    RiskPositionSnapshotProducerError,
    RiskPositionValuation,
    RiskPositionValuationResolver,
)


_SCOPE = AccountScope("fixture", "position-account", "simulation")
_INSTRUMENT = "EURUSD-SPOT"
_STRATEGY = "position-strategy"


class _PositionEffect(Enum):
    OPEN = "OPEN"


class _Side(Enum):
    BUY = "BUY"


def _sha(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def _source_facts(
    now_ns: int,
    *,
    quantity: str = "20",
    metadata_as_of_ns: Optional[int] = None,
    mark_observed_at_ns: Optional[int] = None,
    fx_observed_at_ns: Optional[int] = None,
):
    observed = now_ns - 1_000
    metadata = InstrumentRiskMetadata(
        instrument=_INSTRUMENT,
        metadata_version="position-metadata-v1",
        as_of_ns=observed if metadata_as_of_ns is None else metadata_as_of_ns,
        expires_at_ns=now_ns + 3_600_000_000_000,
        tick_size=Decimal("0.0001"),
        quantity_step=Decimal("1"),
        contract_multiplier=Decimal("1"),
        max_gross_notional=Decimal("1000"),
        quantity_unit="CONTRACT",
        valuation_unit="USD",
        quote_currency="EUR",
        fx_source_ref="eurusd-feed",
        underlying_ref="EUR",
    )
    fx = InstrumentRiskFxRate(
        instrument=_INSTRUMENT,
        quote_currency="EUR",
        valuation_unit="USD",
        rate=Decimal("1.5"),
        observed_at_ns=observed if fx_observed_at_ns is None else fx_observed_at_ns,
        valid_until_ns=now_ns + 3_600_000_000_000,
        source_ref="eurusd-feed",
        source_revision="fx-revision-1",
        source_record_sha256=_sha("eurusd-source-record"),
    )
    from bt_api_risk import AccountPositionRiskFact

    fact = AccountPositionRiskFact(
        position_id="position-1",
        strategy_id=_STRATEGY,
        instrument=_INSTRUMENT,
        position_side="LONG",
        quantity=Decimal(quantity),
        quantity_unit="CONTRACT",
        mark_price=Decimal("1"),
        mark_observed_at_ns=(
            observed if mark_observed_at_ns is None else mark_observed_at_ns
        ),
        mark_valid_until_ns=now_ns + 3_600_000_000_000,
        mark_source_ref="position-mark-feed",
        mark_source_revision="mark-revision-1",
        mark_source_record_sha256=_sha("position-mark-record"),
        position_source_record_sha256=_sha("position-record"),
        metadata=metadata,
        fx_rate=fx,
    )
    return fact


def _coverage(now_ns: int, *, filters: Optional[list[str]] = None) -> dict[str, object]:
    return {
        "account_revision": 1,
        "account_scope": {
            "account_id": _SCOPE.account_id,
            "environment": _SCOPE.environment,
            "provider": _SCOPE.provider,
        },
        "filters": [] if filters is None else filters,
        "observed_at_ns": now_ns,
        "page_count": 3,
        "pagination_exhausted": True,
        "permission_revision": "account-full-permission-r3",
        "permission_scope": "ACCOUNT_FULL",
        "query_scope": "ALL_ACCOUNT_POSITIONS",
        "schema": "bt_api_risk.position-snapshot-coverage.v1",
        "snapshot_version": "provider-position-snapshot-r7",
        "source_schema_version": "position-source-v1",
        "valid_until_ns": now_ns + 3_600_000_000_000,
    }


def _snapshot(
    now_ns: int,
    *,
    fact=None,
    filters: Optional[list[str]] = None,
    declared_gross: str = "30",
    full_details: bool = True,
) -> dict[str, object]:
    position_facts = [] if fact is None else [fact.to_payload()]
    valuation_facts = position_facts if full_details else []
    positions = []
    if fact is not None:
        positions.append(
            {
                "active_increase_intent_count": 1,
                "gross_notional": declared_gross,
                "notional_unit": "USD",
                "source_record_sha256": _sha("position-summary-source"),
                "strategy_id": _STRATEGY,
                "valuation_facts_sha256": _sha("position-summary-valuation"),
            }
        )
    snapshot: dict[str, object] = {
        "account_revision": 1,
        "account_scope": {
            "account_id": _SCOPE.account_id,
            "environment": _SCOPE.environment,
            "provider": _SCOPE.provider,
        },
        "complete_open_orders": True,
        "complete_positions": True,
        "notional_unit": "USD",
        "observed_at_ns": now_ns,
        "open_orders": [],
        "positions": positions,
        "source_schema_version": "position-source-v1",
        "valid_until_ns": now_ns + 3_600_000_000_000,
        "valuation_facts": valuation_facts,
    }
    if full_details:
        snapshot["position_coverage"] = _coverage(now_ns, filters=filters)
        snapshot["position_risk_facts"] = position_facts
    return snapshot


def _policy(limits: tuple[RiskExposureLimit, ...], *, max_age_seconds: int = 300):
    return RiskPolicy(
        "position-snapshot-policy-v1",
        Decimal("1000"),
        max_increase_count=10,
        permit_ttl_seconds=30.0,
        require_strategy_allocation=True,
        notional_unit="USD",
        exposure_accounting_mode=ExposureAccountingMode.LIVE_GROSS,
        max_gross_occupancy_notional=Decimal("1000"),
        max_gross_occupancy_intent_count=10,
        max_exposure_facts_age_seconds=max_age_seconds,
        exposure_dimension_limits=limits,
    )


def _stack(tmp_path, limits, *, now_ns: int, max_age_seconds: int = 300):
    journal = RiskOrderJournal(
        tmp_path / "risk-order-journal.sqlite3", writer_id="position-snapshot-test"
    )
    policy = _policy(limits, max_age_seconds=max_age_seconds)
    gate = DurableRiskGate(
        tmp_path / "risk-gate.sqlite3",
        policy,
        execution_journal_authority=journal,
        gross_exposure_authority=journal,
        clock_ns=lambda: now_ns,
    )
    return journal, gate, policy


def _utc_text(timestamp_ns: int) -> str:
    value = datetime(1970, 1, 1, tzinfo=timezone.utc) + timedelta(
        microseconds=timestamp_ns // 1_000
    )
    return value.isoformat(timespec="microseconds").replace("+00:00", "Z")


class _FullPositionProvider:
    def __init__(
        self,
        observation: AccountObservation,
        *,
        valid_until_utc: str,
        account_revision: int = 1,
        snapshot_version: Optional[str] = None,
    ) -> None:
        self.observation = observation
        self.valid_until_utc = valid_until_utc
        self.account_revision = account_revision
        self.snapshot_version = snapshot_version

    def connect(self, *, account_key, generation, timeout) -> None:
        return None

    def read_account(self, *, generation, timeout):
        return GatewayFullPositionSnapshot(
            observation=self.observation,
            account_key_id=self.observation.account_key.account_key_id,
            session_generation=generation,
            query_scope="ALL_ACCOUNT_POSITIONS",
            filters=(),
            permission_scope="ACCOUNT_FULL",
            permission_revision="fixture-account-full-acl-v1",
            pagination_exhausted=True,
            page_count=3,
            account_revision=self.account_revision,
            source_schema_version="fixture-position-source-v1",
            snapshot_version=(
                self.observation.observation_id
                if self.snapshot_version is None
                else self.snapshot_version
            ),
            positions_sha256=position_rows_sha256(self.observation),
            resource_coverage_sha256=resource_coverage_sha256(self.observation),
            valid_until_utc=self.valid_until_utc,
        )

    def close(self):
        return None


@contextmanager
def _owner_position_producer(
    tmp_path,
    journal,
    fact,
    now_ns,
    *,
    venue: str = "fixture-venue",
    expected_venue: Optional[str] = None,
    revision: int = 1,
    gateway_root: Optional[Path] = None,
    guard_root: Optional[Path] = None,
    observation_id: Optional[str] = None,
    snapshot_version: Optional[str] = None,
):
    account_key = AccountKey(
        provider=_SCOPE.provider,
        venue=venue,
        environment=_SCOPE.environment,
        account_id=_SCOPE.account_id,
    )
    resource = AccountResourceKey(product_type="spot", margin_pool="cross")
    position_time = _utc_text(now_ns // 1_000 * 1_000 - 1_000)
    completed_time = _utc_text(now_ns // 1_000 * 1_000)
    raw_side = "short" if fact.position_side == "SHORT" else "long"
    raw_position = AccountPosition(
        resource=resource,
        instrument=MarketInstrumentKey(
            provider=account_key.provider,
            venue=account_key.venue,
            environment=account_key.environment,
            product_type=resource.product_type,
            instrument_id=fact.instrument,
            native_symbol=fact.instrument,
        ),
        side=raw_side,
        quantity=str(fact.quantity),
        qty_unit=fact.quantity_unit,
        average_price=str(fact.mark_price),
        price_currency=fact.metadata.quote_currency,
        observed_at_utc=position_time,
    )
    observation = AccountObservation(
        account_key=account_key,
        observation_id=(
            f"full-positions-{now_ns}" if observation_id is None else observation_id
        ),
        coverage=(resource,),
        balances=(),
        positions=(raw_position,),
        valuation=None,
        started_at_utc=completed_time,
        completed_at_utc=completed_time,
        consistency="provider_atomic",
        balances_complete=True,
        positions_complete=True,
        open_orders=(),
        open_orders_complete=True,
    )
    provider = _FullPositionProvider(
        observation,
        valid_until_utc=_utc_text(now_ns // 1_000 * 1_000 + 3_600_000_000_000),
        account_revision=revision,
        snapshot_version=snapshot_version,
    )
    gateway_root = tmp_path if gateway_root is None else gateway_root
    gateway_root.mkdir(parents=True, exist_ok=True)
    gateway_journal = GatewayEventJournal(
        gateway_root / "gateway-account-snapshots.sqlite3",
        clock=lambda: now_ns / 1_000_000_000,
    )
    guard_dir = tmp_path / "gateway-session-guards" if guard_root is None else guard_root
    guard_dir.mkdir(parents=True, exist_ok=True)
    worker = GatewayAccountSessionWorker(
        provider,
        gateway_journal,
        account_key=account_key,
        journal_account_scope="risk-position-test-account",
        state_scope="risk-position-test-state",
        guard=LocalAccountSessionGuard(
            guard_dir, account_key.account_key_id, "risk-position-test-owner"
        ),
        required_coverage=(resource,),
        require_open_orders=True,
        max_staleness_seconds=5.0,
    )
    resolved_venue = venue if expected_venue is None else expected_venue
    expected_instrument = MarketInstrumentKey(
        provider=account_key.provider,
        venue=resolved_venue,
        environment=account_key.environment,
        product_type=resource.product_type,
        instrument_id=fact.instrument,
        native_symbol=fact.instrument,
    )

    def resolve(position, receipt):
        del position, receipt
        return RiskPositionValuation(
            strategy_id=fact.strategy_id,
            metadata=fact.metadata,
            mark_price=fact.mark_price,
            mark_observed_at_ns=fact.mark_observed_at_ns,
            mark_valid_until_ns=fact.mark_valid_until_ns,
            mark_source_ref=fact.mark_source_ref,
            mark_source_revision=fact.mark_source_revision,
            mark_source_record_sha256=fact.mark_source_record_sha256,
            fx_rate=fact.fx_rate,
            greeks=fact.greeks,
        )

    resolver = RiskPositionValuationResolver(
        expected_account_key=account_key,
        expected_instruments_by_instrument={fact.instrument: expected_instrument},
        resolve=resolve,
    )
    producer = RiskPositionSnapshotProducer(
        worker,
        journal,
        scope=_SCOPE,
        expected_account_key=account_key,
        notional_unit="USD",
        valuation_resolver=resolver,
        expected_instruments_by_instrument={fact.instrument: expected_instrument},
        metadata_by_instrument={fact.metadata.instrument: fact.metadata},
        resource_by_instrument={fact.metadata.instrument: resource},
        strategies_by_instrument={fact.metadata.instrument: frozenset({_STRATEGY})},
    )
    worker.start()
    try:
        yield worker, producer
    finally:
        worker.close()


def _install_snapshot(tmp_path, journal, gate, *, fact, now_ns):
    with _owner_position_producer(tmp_path=tmp_path, journal=journal, fact=fact, now_ns=now_ns) as (
        _worker,
        producer,
    ):
        with journal.writer_fence(_SCOPE) as fence:
            proof = producer.ingest(fence)
            gate.install_gross_exposure_baseline(proof)
    return proof


def _execution(
    metadata: InstrumentRiskMetadata,
    intent_id: str,
    quantity: str,
    *,
    fx_rate: InstrumentRiskFxRate,
):
    return SimpleNamespace(
        intent_id=intent_id,
        fingerprint=_sha("execution/" + intent_id),
        instrument=metadata.instrument,
        quantity=Decimal(quantity),
        price=Decimal("1"),
        metadata_version=metadata.metadata_version,
        tags={"instrument_metadata_digest": metadata.digest},
        fx_rate=fx_rate,
        position_effect=_PositionEffect.OPEN,
        side=_Side.BUY,
        scope=SimpleNamespace(
            provider=_SCOPE.provider,
            account_ref=_SCOPE.account_id,
            environment=_SCOPE.environment,
            strategy_id=_STRATEGY,
            trading_day="2026-10-16",
        ),
    )


def _mapper(gate, metadata, fx, limits):
    return InstrumentRiskAdmissionMapper(
        _SCOPE,
        InstrumentRiskRegistry((metadata,)),
        clock_ns=lambda: metadata.as_of_ns + 1_000,
        allocation_reader=lambda requested: gate.get_strategy_allocation(
            _SCOPE, requested.strategy_id
        ),
        fx_rates_by_instrument=(fx,),
        exposure_dimension_limits=limits,
    )


def _underlying_limit(maximum: str = "50"):
    return (
        RiskExposureLimit(
            RiskExposureKind.UNDERLYING_NOTIONAL,
            "EUR",
            "USD",
            Decimal(maximum),
        ),
    )


def test_full_snapshot_revalues_fx_and_binds_persisted_dimension_admission(tmp_path) -> None:
    now_ns = time.time_ns()
    limits = _underlying_limit()
    fact = _source_facts(now_ns)
    journal, gate, policy = _stack(tmp_path, limits, now_ns=now_ns)
    reopened = None
    try:
        gate.set_strategy_allocation(
            _SCOPE,
            _STRATEGY,
            "allocation-v1",
            max_notional=Decimal("1000"),
            notional_unit="USD",
            max_gross_occupancy_notional=Decimal("1000"),
            max_gross_occupancy_intent_count=10,
        )
        with _owner_position_producer(tmp_path, journal, fact, now_ns) as (
            _worker,
            producer,
        ):
            with journal.writer_fence(_SCOPE) as fence:
                proof = producer.ingest(fence)
                gate.install_gross_exposure_baseline(proof)
                gate.install_gross_exposure_baseline(proof)
        assert proof.account_gross_notional == Decimal("30")
        assert proof.position_coverage is not None
        assert proof.position_coverage.page_count == 3
        assert len(proof.position_risk_facts) == 1

        with gate._connection() as connection:
            baseline_row = connection.execute(
                """SELECT dimension_kind, dimension_key, unit, exposure, source_sha256
                   FROM risk_gross_baseline_dimensions"""
            ).fetchone()
        assert baseline_row is not None
        assert tuple(baseline_row[:4]) == (
            "underlying_notional",
            "EUR",
            "USD",
            "30",
        )
        assert baseline_row["source_sha256"]

        metadata = fact.metadata
        mapper = _mapper(gate, metadata, fact.fx_rate, limits)
        first = mapper(
            _execution(metadata, "fx-dimension-first", "7", fx_rate=fact.fx_rate)
        )
        first_permit = gate.reserve(first)
        second = mapper(
            _execution(metadata, "fx-dimension-second", "7", fx_rate=fact.fx_rate)
        )
        with pytest.raises(RiskDeniedError) as denied:
            gate.reserve(second)
        assert denied.value.code == "RISK_EXPOSURE_DIMENSION_LIMIT_EXHAUSTED"

        with gate._connection() as connection:
            assert connection.execute(
                "SELECT COUNT(*) FROM risk_reservations"
            ).fetchone()[0] == 1
            assert connection.execute(
                "SELECT COUNT(*) FROM risk_reservation_dimensions"
            ).fetchone()[0] == 1
            assert connection.execute(
                "SELECT permit_id FROM risk_reservations"
            ).fetchone()[0] == first_permit.permit_id

        gate.close()
        reopened = DurableRiskGate(
            tmp_path / "risk-gate.sqlite3",
            policy,
            execution_journal_authority=journal,
            gross_exposure_authority=journal,
            clock_ns=lambda: now_ns,
        )
        after_restart = _mapper(reopened, metadata, fact.fx_rate, limits)(
            _execution(
                metadata,
                "fx-dimension-after-restart",
                "7",
                fx_rate=fact.fx_rate,
            )
        )
        with pytest.raises(RiskDeniedError) as restarted_denial:
            reopened.reserve(after_restart)
        assert restarted_denial.value.code == "RISK_EXPOSURE_DIMENSION_LIMIT_EXHAUSTED"
    finally:
        if reopened is not None:
            reopened.close()
        else:
            gate.close()


def test_dimension_cap_rejects_boolean_only_baseline_before_risk_writes(tmp_path) -> None:
    now_ns = time.time_ns()
    limits = _underlying_limit()
    journal, gate, _ = _stack(tmp_path, limits, now_ns=now_ns)
    try:
        with journal.writer_fence(_SCOPE) as fence:
            proof = journal.ingest_gross_exposure_baseline(
                fence,
                source_snapshot=_snapshot(
                    now_ns, fact=None, full_details=False, declared_gross="0"
                ),
            )
            assert proof.position_coverage is None
            with pytest.raises(RiskDeniedError) as denied:
                gate.install_gross_exposure_baseline(proof)
            assert denied.value.code == "GROSS_EXPOSURE_POSITION_COVERAGE_REQUIRED"
        with gate._connection() as connection:
            assert connection.execute(
                "SELECT COUNT(*) FROM risk_policy_bindings"
            ).fetchone()[0] == 0
            assert connection.execute(
                "SELECT COUNT(*) FROM risk_gross_exposure_baselines"
            ).fetchone()[0] == 0
            assert connection.execute(
                "SELECT COUNT(*) FROM risk_gross_baseline_dimensions"
            ).fetchone()[0] == 0
            assert connection.execute(
                "SELECT COUNT(*) FROM risk_reservations"
            ).fetchone()[0] == 0
    finally:
        gate.close()


def test_filtered_snapshot_cannot_claim_complete_position_coverage(tmp_path) -> None:
    now_ns = time.time_ns()
    journal, gate, _ = _stack(tmp_path, _underlying_limit(), now_ns=now_ns)
    snapshot = _snapshot(now_ns, fact=None)
    snapshot["position_coverage"] = _coverage(
        now_ns, filters=["instrument=EURUSD-SPOT"]
    )
    try:
        with journal.writer_fence(_SCOPE) as fence:
            with pytest.raises(RiskOrderJournalError) as denied:
                journal.ingest_gross_exposure_baseline(
                    fence, source_snapshot=snapshot
                )
            assert denied.value.code == "GROSS_BASELINE_POSITION_COVERAGE_INVALID"
        with journal._connection() as connection:
            assert connection.execute(
                "SELECT COUNT(*) FROM risk_order_gross_baselines"
            ).fetchone()[0] == 0
    finally:
        gate.close()


@pytest.mark.parametrize(
    "failure", ("stale_fx", "stale_mark", "stale_metadata", "missing_greeks")
)
def test_unfresh_or_missing_position_valuation_facts_fail_before_risk_writes(
    tmp_path, failure
) -> None:
    now_ns = time.time_ns()
    limits = _underlying_limit()
    if failure == "missing_greeks":
        limits += (
            RiskExposureLimit(
                RiskExposureKind.DELTA,
                "EUR",
                "EUR",
                Decimal("100"),
            ),
        )
        fact = _source_facts(now_ns)
    elif failure == "stale_fx":
        fact = _source_facts(
            now_ns, fx_observed_at_ns=now_ns - 301_000_000_000
        )
    elif failure == "stale_mark":
        fact = _source_facts(
            now_ns, mark_observed_at_ns=now_ns - 301_000_000_000
        )
    else:
        fact = _source_facts(
            now_ns, metadata_as_of_ns=now_ns - 301_000_000_000
        )
    journal, gate, _ = _stack(
        tmp_path, limits, now_ns=now_ns, max_age_seconds=300
    )
    try:
        with pytest.raises(RiskDeniedError) as denied:
            _install_snapshot(tmp_path, journal, gate, fact=fact, now_ns=now_ns)
        assert denied.value.code == "GROSS_EXPOSURE_POSITION_FACT_INVALID"
        with gate._connection() as connection:
            for table in (
                "risk_policy_bindings",
                "risk_gross_exposure_baselines",
                "risk_gross_baseline_dimensions",
                "risk_reservations",
            ):
                assert connection.execute(
                    "SELECT COUNT(*) FROM " + table
                ).fetchone()[0] == 0
    finally:
        gate.close()


def test_summary_gross_must_match_revalued_position_facts(tmp_path) -> None:
    now_ns = time.time_ns()
    fact = _source_facts(now_ns)
    journal, gate, _ = _stack(tmp_path, _underlying_limit(), now_ns=now_ns)
    try:
        with journal.writer_fence(_SCOPE) as fence:
            with pytest.raises(RiskOrderJournalError) as mismatch:
                journal.ingest_gross_exposure_baseline(
                    fence,
                    source_snapshot=_snapshot(
                        now_ns, fact=fact, declared_gross="29.99"
                    ),
                )
            assert mismatch.value.code == "GROSS_BASELINE_POSITION_REVALUATION_MISMATCH"
        with journal._connection() as connection:
            assert connection.execute(
                "SELECT COUNT(*) FROM risk_order_gross_baselines"
            ).fetchone()[0] == 0
    finally:
        gate.close()


def test_same_instrument_id_on_another_venue_fails_trusted_target_binding(tmp_path) -> None:
    now_ns = time.time_ns()
    fact = _source_facts(now_ns)
    journal = RiskOrderJournal(
        tmp_path / "risk-order-journal.sqlite3", writer_id="position-venue-binding"
    )
    with pytest.raises(RiskPositionSnapshotProducerError) as denied:
        with _owner_position_producer(
            tmp_path,
            journal,
            fact,
            now_ns,
            venue="SHFE",
            expected_venue="DCE",
        ):
            pytest.fail("venue-mismatched trusted target must not compose")
    assert denied.value.code == "POSITION_SOURCE_BINDINGS_INVALID"
    connection = journal._connection()
    try:
        assert connection.execute(
            "SELECT COUNT(*) FROM risk_order_gross_baselines"
        ).fetchone()[0] == 0
    finally:
        connection.close()


def test_scope_journal_rejects_a_second_full_account_key_on_new_revision(tmp_path) -> None:
    now_ns = time.time_ns()
    fact = _source_facts(now_ns)
    journal_path = tmp_path / "risk-order-journal.sqlite3"
    journal = RiskOrderJournal(
        journal_path, writer_id="position-account-key-binding"
    )
    with _owner_position_producer(
        tmp_path, journal, fact, now_ns, venue="SHFE", revision=1
    ) as (_worker, producer):
        with journal.writer_fence(_SCOPE) as fence:
            first = producer.ingest(fence)
    assert first.account_revision == 1

    reopened_journal = RiskOrderJournal(
        journal_path, writer_id="position-account-key-binding"
    )
    with reopened_journal.writer_fence(_SCOPE) as fence:
        with pytest.raises(RiskOrderJournalError) as downgrade:
            reopened_journal.ingest_gross_exposure_baseline(
                fence,
                source_snapshot=_snapshot(
                    now_ns + 1_000_000,
                    fact=None,
                    full_details=False,
                    declared_gross="0",
                ),
            )
    assert downgrade.value.code == "GROSS_BASELINE_POSITION_SOURCE_REQUIRED"

    second_gateway_root = tmp_path / "second-venue-gateway"
    second_gateway_root.mkdir()
    with _owner_position_producer(
        second_gateway_root,
        reopened_journal,
        fact,
        now_ns + 1_000_000,
        venue="DCE",
        revision=2,
    ) as (_worker, producer):
        with reopened_journal.writer_fence(_SCOPE) as fence:
            with pytest.raises(RiskOrderJournalError) as denied:
                producer.ingest(fence)
    assert denied.value.code == "GROSS_BASELINE_POSITION_ACCOUNT_IDENTITY_MISMATCH"
    connection = reopened_journal._connection()
    try:
        rows = connection.execute(
            """SELECT account_revision, source_snapshot_json
               FROM risk_order_gross_baselines WHERE scope_key=?""",
            (_SCOPE.key,),
        ).fetchall()
    finally:
        connection.close()
    assert len(rows) == 1
    assert rows[0]["account_revision"] == 1
    assert json.loads(rows[0]["source_snapshot_json"])["position_account_key_id"] == (
        AccountKey(
            provider=_SCOPE.provider,
            venue="SHFE",
            environment=_SCOPE.environment,
            account_id=_SCOPE.account_id,
        ).account_key_id
    )


def test_owner_prime_installs_replays_same_revision_after_restart_and_rejects_conflict(
    tmp_path,
) -> None:
    now_ns = time.time_ns()
    limits = _underlying_limit()
    fact = _source_facts(now_ns)
    observation_id = f"same-provider-observation-{now_ns}"
    snapshot_version = "provider-position-snapshot-r7"
    risk_path = tmp_path / "risk-order-journal.sqlite3"
    gate_path = tmp_path / "risk-gate.sqlite3"
    guard_root = tmp_path / "shared-gateway-session-guards"
    journal = RiskOrderJournal(risk_path, writer_id="position-owner-restart-test")
    policy = _policy(limits)

    def make_gate(current_journal):
        return DurableRiskGate(
            gate_path,
            policy,
            execution_journal_authority=current_journal,
            gross_exposure_authority=current_journal,
            clock_ns=lambda: now_ns,
        )

    def journal_baseline_identity(current_journal):
        connection = current_journal._connection()
        try:
            row = connection.execute(
                """SELECT account_revision, source_snapshot_sha256, proof_sha256
                   FROM risk_order_gross_baselines WHERE scope_key=?""",
                (_SCOPE.key,),
            ).fetchone()
            assert row is not None
            return tuple(row)
        finally:
            connection.close()

    def risk_baseline_projection(current_gate):
        snapshot = current_gate.gross_exposure_snapshot(_SCOPE)
        with current_gate._connection() as connection:
            baseline_count = connection.execute(
                "SELECT COUNT(*) FROM risk_gross_exposure_baselines"
            ).fetchone()[0]
            dimensions = tuple(
                tuple(row)
                for row in connection.execute(
                    """SELECT dimension_kind, dimension_key, unit, exposure, source_sha256
                       FROM risk_gross_baseline_dimensions
                       WHERE scope_key=? AND account_revision=1
                       ORDER BY dimension_kind, dimension_key, unit""",
                    (_SCOPE.key,),
                ).fetchall()
            )
        return (
            snapshot["baseline_revision"],
            snapshot["account_baseline_notional"],
            snapshot["notional_unit"],
            snapshot["strategy_baselines"],
            baseline_count,
            dimensions,
        )

    gate = make_gate(journal)
    try:
        gate.set_strategy_allocation(
            _SCOPE,
            _STRATEGY,
            "allocation-v1",
            max_notional=Decimal("1000"),
            notional_unit="USD",
            max_gross_occupancy_notional=Decimal("1000"),
            max_gross_occupancy_intent_count=10,
        )
        with _owner_position_producer(
            tmp_path,
            journal,
            fact,
            now_ns,
            gateway_root=tmp_path / "gateway-generation-1",
            guard_root=guard_root,
            observation_id=observation_id,
            snapshot_version=snapshot_version,
        ) as (worker, producer):
            generation_1 = worker.session_generation
            owner = RiskPositionSnapshotOwner(worker, journal, gate, producer)
            owner.prime()
            assert owner.ready is True
            assert worker.session_generation == generation_1

        first_journal_identity = journal_baseline_identity(journal)
        first_risk_projection = risk_baseline_projection(gate)
        assert first_journal_identity[0] == 1
        assert first_risk_projection[0:3] == (1, Decimal("30"), "USD")
        assert first_risk_projection[4] == 1
        assert len(first_risk_projection[5]) == 1

        gate.close()
        journal = RiskOrderJournal(risk_path, writer_id="position-owner-restart-test")
        gate = make_gate(journal)
        with _owner_position_producer(
            tmp_path,
            journal,
            fact,
            now_ns,
            gateway_root=tmp_path / "gateway-generation-2",
            guard_root=guard_root,
            observation_id=observation_id,
            snapshot_version=snapshot_version,
        ) as (worker, producer):
            generation_2 = worker.session_generation
            assert generation_2 != generation_1
            owner = RiskPositionSnapshotOwner(worker, journal, gate, producer)
            owner.prime()
            assert owner.ready is True

        assert journal_baseline_identity(journal) == first_journal_identity
        assert risk_baseline_projection(gate) == first_risk_projection

        gate.close()
        journal = RiskOrderJournal(risk_path, writer_id="position-owner-restart-test")
        gate = make_gate(journal)
        conflicting_fact = _source_facts(now_ns, quantity="21")
        with _owner_position_producer(
            tmp_path,
            journal,
            conflicting_fact,
            now_ns,
            gateway_root=tmp_path / "gateway-generation-3",
            guard_root=guard_root,
            observation_id=observation_id,
            snapshot_version=snapshot_version,
        ) as (worker, producer):
            generation_3 = worker.session_generation
            assert generation_3 != generation_2
            owner = RiskPositionSnapshotOwner(worker, journal, gate, producer)
            with pytest.raises(RiskPositionSnapshotProducerError) as denied:
                owner.prime()
            assert denied.value.code == "GROSS_BASELINE_REVISION_CONFLICT"
            assert owner.ready is False

        assert journal_baseline_identity(journal) == first_journal_identity
        assert risk_baseline_projection(gate) == first_risk_projection
    finally:
        gate.close()
