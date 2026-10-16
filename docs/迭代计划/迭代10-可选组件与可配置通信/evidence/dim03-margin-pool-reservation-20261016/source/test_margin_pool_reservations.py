"""DIM03 focused tests: one shared margin pool, two contracts, atomic admission.

The durable Risk gate accepts an optional Base ``AccountResourceKey``
(``product_type``, ``margin_pool``) on a risk intent and checks it inside the
same ``BEGIN IMMEDIATE`` transaction against a per-pool notional ceiling
installed through ``set_margin_pool_limit``.  Intents without a resource key
keep the established behavior.  Everything here is synthetic local SQLite; no
provider, network, or execution package is involved.
"""

from __future__ import annotations

import hashlib
import json
import subprocess
import sys
import time
from decimal import Decimal
from pathlib import Path

import bt_api_base
import pytest
from bt_api_base.account_protocol import AccountResourceKey

import bt_api_risk
from bt_api_risk import (
    AccountScope,
    DurableRiskGate,
    IntentAction,
    RiskDeniedError,
    RiskGroupLegIntent,
    RiskGroupReservationState,
    RiskIntent,
    RiskPolicy,
)

_POOL_ALPHA = AccountResourceKey(product_type="futures", margin_pool="pool-alpha")
_POOL_BETA = AccountResourceKey(product_type="futures", margin_pool="pool-beta")
_DEADLINE_NS = 1_200_000_000_000


class _Clock:
    def __init__(self, value: float = 100.0) -> None:
        self.value = value

    def __call__(self) -> float:
        return self.value


def _sha(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def _intent(
    scope: AccountScope,
    intent_id: str,
    quantity: str,
    notional: str,
    *,
    instrument: str = "venue/contract-a",
    profile: str = "a" * 64,
    resource: AccountResourceKey | None = None,
    observed_at_ns: int = 100_000_000_000,
    valid_until_ns: int = 1_000_000_000_000,
) -> RiskIntent:
    return RiskIntent(
        intent_id=intent_id,
        scope=scope,
        action=IntentAction.INCREASE,
        notional=Decimal(notional),
        payload_fingerprint=_sha(f"{intent_id}|{quantity}|{notional}"),
        strategy_id="strategy-1",
        allocation_version="allocation-v1",
        instrument=instrument,
        quantity=Decimal(quantity),
        quantity_unit="contract",
        notional_unit="USD",
        exposure_profile_fingerprint=profile,
        instrument_facts_observed_at_ns=observed_at_ns,
        instrument_facts_valid_until_ns=valid_until_ns,
        resource=resource,
    )


def _gate(
    tmp_path,
    *,
    notional_cap: str = "1000",
    count_cap: int = 8,
    strategy_cap: str = "1000",
    ttl: float = 5.0,
    database: str = "margin-pool.sqlite3",
) -> tuple[DurableRiskGate, AccountScope, _Clock]:
    clock = _Clock()
    scope = AccountScope("fixture", "account-1", "sandbox")
    gate = DurableRiskGate(
        tmp_path / database,
        RiskPolicy(
            "margin-pool-policy",
            Decimal(notional_cap),
            count_cap,
            permit_ttl_seconds=ttl,
            require_strategy_allocation=True,
            notional_unit="USD",
        ),
        clock=clock,
    )
    gate.set_strategy_allocation(
        scope,
        "strategy-1",
        "allocation-v1",
        max_notional=Decimal(strategy_cap),
        notional_unit="USD",
    )
    return gate, scope, clock


def _reserve(gate, scope, group_id, legs, *, deadline_ns=_DEADLINE_NS):
    return gate.reserve_group_vector(
        scope,
        group_id,
        tuple(RiskGroupLegIntent(leg_id=leg_id, intent=intent) for leg_id, intent in legs),
        deadline_ns=deadline_ns,
    )


def _durable_counts(gate) -> tuple[int, int, int, int]:
    """Count reservation/claim/resolution/freeze rows in the one durable ledger."""

    with gate._connection() as connection:
        return (
            connection.execute("SELECT COUNT(*) FROM risk_reservations").fetchone()[0],
            connection.execute("SELECT COUNT(*) FROM risk_dispatch_claims").fetchone()[0],
            connection.execute("SELECT COUNT(*) FROM risk_dispatch_resolutions").fetchone()[0],
            connection.execute("SELECT COUNT(*) FROM risk_freezes").fetchone()[0],
        )


def _pool_usage(gate, resource: AccountResourceKey) -> list[tuple[str, str]]:
    """Return ``(status, notional)`` for every durable row of one exact pool."""

    with gate._connection() as connection:
        rows = connection.execute(
            """SELECT status, notional FROM risk_reservations
               WHERE resource_product_type = ? AND resource_margin_pool = ?
               ORDER BY intent_id""",
            (resource.product_type, resource.margin_pool),
        ).fetchall()
    return [(str(row["status"]), str(row["notional"])) for row in rows]


def test_same_pool_two_contracts_compete_atomically_and_denials_write_nothing(tmp_path):
    gate, scope, _ = _gate(tmp_path)
    gate.set_margin_pool_limit(
        scope, "futures", "pool-alpha", "pool-v1", max_notional=Decimal("300")
    )

    first = _reserve(
        gate,
        scope,
        "group-a",
        (("leg-a", _intent(scope, "intent-a", "2", "200", resource=_POOL_ALPHA)),),
    )
    assert first.legs[0].state is RiskGroupReservationState.ACTIVE

    # A second, different contract in the same pool would exceed the shared cap.
    before = _durable_counts(gate)
    with pytest.raises(RiskDeniedError) as denied:
        _reserve(
            gate,
            scope,
            "group-b",
            (
                (
                    "leg-b",
                    _intent(
                        scope,
                        "intent-b",
                        "1.5",
                        "150",
                        instrument="venue/contract-b",
                        profile="b" * 64,
                        resource=_POOL_ALPHA,
                    ),
                ),
            ),
        )
    assert denied.value.code == "MARGIN_POOL_LIMIT_EXHAUSTED"
    assert _durable_counts(gate) == before
    with pytest.raises(RiskDeniedError) as absent:
        gate.query_group_vector(scope, "group-b")
    assert absent.value.code == "GROUP_NOT_FOUND"

    # An exact-fill reservation is admitted; one cent beyond is not.
    _reserve(
        gate,
        scope,
        "group-c",
        (
            (
                "leg-c",
                _intent(
                    scope,
                    "intent-c",
                    "1",
                    "100",
                    instrument="venue/contract-c",
                    profile="c" * 64,
                    resource=_POOL_ALPHA,
                ),
            ),
        ),
    )
    before = _durable_counts(gate)
    with pytest.raises(RiskDeniedError) as over:
        _reserve(
            gate,
            scope,
            "group-d",
            (
                (
                    "leg-d",
                    _intent(
                        scope,
                        "intent-d",
                        "1",
                        "0.01",
                        instrument="venue/contract-d",
                        profile="d" * 64,
                        resource=_POOL_ALPHA,
                    ),
                ),
            ),
        )
    assert over.value.code == "MARGIN_POOL_LIMIT_EXHAUSTED"
    assert _durable_counts(gate) == before

    # Releasing the first contract's unclaimed vector frees exactly its credit.
    released = gate.release_group_unclaimed(scope, "group-a", "owner-abort")
    assert {leg.state for leg in released.legs} == {RiskGroupReservationState.RELEASED}
    replay = _reserve(
        gate,
        scope,
        "group-b",
        (
            (
                "leg-b",
                _intent(
                    scope,
                    "intent-b",
                    "1.5",
                    "150",
                    instrument="venue/contract-b",
                    profile="b" * 64,
                    resource=_POOL_ALPHA,
                ),
            ),
        ),
    )
    assert replay.legs[0].state is RiskGroupReservationState.ACTIVE
    snapshot = gate.snapshot(scope)
    assert snapshot["increase_count"] == 2
    assert snapshot["increase_notional"] == Decimal("250")
    assert _pool_usage(gate, _POOL_ALPHA) == [
        ("released", "200"),
        ("active", "150"),
        ("active", "100"),
    ]
    gate.close()


def test_one_vector_two_contracts_in_one_pool_is_atomic_and_counts_maxima(tmp_path):
    gate, scope, _ = _gate(tmp_path)
    gate.set_margin_pool_limit(
        scope, "futures", "pool-alpha", "pool-v1", max_notional=Decimal("250")
    )
    legs = (
        ("leg-a", _intent(scope, "intent-a", "1.5", "150", resource=_POOL_ALPHA)),
        (
            "leg-b",
            _intent(
                scope,
                "intent-b",
                "1.5",
                "150",
                instrument="venue/contract-b",
                profile="b" * 64,
                resource=_POOL_ALPHA,
            ),
        ),
    )

    # The two legs together exceed the pool ceiling: the whole vector rolls back.
    before = _durable_counts(gate)
    with pytest.raises(RiskDeniedError) as denied:
        _reserve(gate, scope, "group-mixed", legs)
    assert denied.value.code == "MARGIN_POOL_LIMIT_EXHAUSTED"
    assert _durable_counts(gate) == before
    assert gate.snapshot(scope)["increase_count"] == 0
    with pytest.raises(RiskDeniedError) as absent:
        gate.query_group_vector(scope, "group-mixed")
    assert absent.value.code == "GROUP_NOT_FOUND"

    # A later limit revision gates future reservations; the same vector now fits exactly.
    gate.set_margin_pool_limit(
        scope, "futures", "pool-alpha", "pool-v2", max_notional=Decimal("300")
    )
    receipt = _reserve(gate, scope, "group-mixed", legs)
    assert len(receipt.legs) == 2
    assert _pool_usage(gate, _POOL_ALPHA) == [("active", "150"), ("active", "150")]

    # Reserved maxima occupy the pool; a child claim cannot re-credit capacity here,
    # and a further reservation beyond the ceiling is still denied with zero writes.
    before = _durable_counts(gate)
    with pytest.raises(RiskDeniedError) as refill:
        _reserve(
            gate,
            scope,
            "group-refill",
            (
                (
                    "leg-r",
                    _intent(
                        scope,
                        "intent-r",
                        "1",
                        "0.01",
                        instrument="venue/contract-c",
                        profile="c" * 64,
                        resource=_POOL_ALPHA,
                    ),
                ),
            ),
        )
    assert refill.value.code == "MARGIN_POOL_LIMIT_EXHAUSTED"
    assert _durable_counts(gate) == before

    # An explicit release hands the two legs' credit back exactly once.
    released = gate.release_group_unclaimed(scope, "group-mixed", "coordinator-aborted")
    assert {leg.state for leg in released.legs} == {RiskGroupReservationState.RELEASED}
    refill = _reserve(
        gate,
        scope,
        "group-refill",
        (
            (
                "leg-r",
                _intent(
                    scope,
                    "intent-r",
                    "1",
                    "0.01",
                    instrument="venue/contract-c",
                    profile="c" * 64,
                    resource=_POOL_ALPHA,
                ),
            ),
        ),
    )
    assert refill.legs[0].state is RiskGroupReservationState.ACTIVE
    gate.close()


def test_different_pools_do_not_interfere(tmp_path):
    gate, scope, _ = _gate(tmp_path)
    gate.set_margin_pool_limit(
        scope, "futures", "pool-alpha", "pool-v1", max_notional=Decimal("150")
    )
    gate.set_margin_pool_limit(scope, "futures", "pool-beta", "pool-v1", max_notional=Decimal("40"))

    beta = _reserve(
        gate,
        scope,
        "group-beta",
        (
            (
                "leg-b",
                _intent(
                    scope,
                    "intent-b",
                    "1",
                    "40",
                    instrument="venue/contract-b",
                    profile="b" * 64,
                    resource=_POOL_BETA,
                ),
            ),
        ),
    )
    assert beta.legs[0].state is RiskGroupReservationState.ACTIVE

    before = _durable_counts(gate)
    with pytest.raises(RiskDeniedError) as beta_full:
        _reserve(
            gate,
            scope,
            "group-beta-2",
            (
                (
                    "leg-b2",
                    _intent(
                        scope,
                        "intent-b2",
                        "1",
                        "0.01",
                        instrument="venue/contract-b",
                        profile="b" * 64,
                        resource=_POOL_BETA,
                    ),
                ),
            ),
        )
    assert beta_full.value.code == "MARGIN_POOL_LIMIT_EXHAUSTED"
    assert _durable_counts(gate) == before

    # Pool beta usage never consumes pool alpha capacity.
    alpha = _reserve(
        gate,
        scope,
        "group-alpha",
        (("leg-a", _intent(scope, "intent-a", "1.5", "150", resource=_POOL_ALPHA)),),
    )
    assert alpha.legs[0].state is RiskGroupReservationState.ACTIVE
    assert _pool_usage(gate, _POOL_ALPHA) == [("active", "150")]
    assert _pool_usage(gate, _POOL_BETA) == [("active", "40")]
    gate.close()


def test_declared_pool_without_installed_limit_fails_closed(tmp_path):
    gate, scope, _ = _gate(tmp_path)

    before = _durable_counts(gate)
    with pytest.raises(RiskDeniedError) as group_denied:
        _reserve(
            gate,
            scope,
            "group-unconfigured",
            (("leg-a", _intent(scope, "intent-a-group", "1", "10", resource=_POOL_ALPHA)),),
        )
    assert group_denied.value.code == "MARGIN_POOL_UNCONFIGURED"
    assert _durable_counts(gate) == before
    with pytest.raises(RiskDeniedError) as absent:
        gate.query_group_vector(scope, "group-unconfigured")
    assert absent.value.code == "GROUP_NOT_FOUND"

    with pytest.raises(RiskDeniedError) as singleton_denied:
        gate.reserve(_intent(scope, "intent-a-single", "1", "10", resource=_POOL_ALPHA))
    assert singleton_denied.value.code == "MARGIN_POOL_UNCONFIGURED"
    assert _durable_counts(gate) == before

    # A keyless intent in the same gate is unaffected.
    permit = gate.reserve(_intent(scope, "intent-plain", "1", "10"))
    assert permit.intent_id == "intent-plain"
    gate.close()


def test_singleton_and_group_reservations_share_one_pool_credit(tmp_path):
    gate, scope, _ = _gate(tmp_path)
    gate.set_margin_pool_limit(
        scope, "futures", "pool-alpha", "pool-v1", max_notional=Decimal("100")
    )

    permit = gate.reserve(_intent(scope, "single-a", "1", "60", resource=_POOL_ALPHA))
    assert permit.intent_id == "single-a"

    # The group path sees the singleton's pool occupation; it is not credited twice.
    before = _durable_counts(gate)
    with pytest.raises(RiskDeniedError) as denied:
        _reserve(
            gate,
            scope,
            "group-b",
            (
                (
                    "leg-b",
                    _intent(
                        scope,
                        "intent-b",
                        "1",
                        "60",
                        instrument="venue/contract-b",
                        profile="b" * 64,
                        resource=_POOL_ALPHA,
                    ),
                ),
            ),
        )
    assert denied.value.code == "MARGIN_POOL_LIMIT_EXHAUSTED"
    assert _durable_counts(gate) == before

    second = _reserve(
        gate,
        scope,
        "group-b",
        (
            (
                "leg-b",
                _intent(
                    scope,
                    "intent-b",
                    "1",
                    "40",
                    instrument="venue/contract-b",
                    profile="b" * 64,
                    resource=_POOL_ALPHA,
                ),
            ),
        ),
    )
    assert second.legs[0].state is RiskGroupReservationState.ACTIVE
    assert _pool_usage(gate, _POOL_ALPHA) == [("active", "40"), ("active", "60")]
    gate.close()


def test_keyless_vectors_replay_established_behavior_with_pool_schema_present(tmp_path):
    gate, scope, _ = _gate(tmp_path)
    # A pool limit exists in the same database but keyless intents never use it.
    gate.set_margin_pool_limit(
        scope, "futures", "pool-alpha", "pool-v1", max_notional=Decimal("10")
    )

    singleton = gate.reserve(_intent(scope, "single-plain", "0.5", "25"))
    assert singleton.intent_id == "single-plain"
    validated = gate.validate_permit(
        singleton.permit_id, _intent(scope, "single-plain", "0.5", "25")
    )
    assert validated.permit_id == singleton.permit_id

    legs = (
        ("leg-a", _intent(scope, "intent-a", "2", "200")),
        ("leg-b", _intent(scope, "intent-b", "1", "100")),
    )
    receipt = _reserve(gate, scope, "group-r77", legs)
    assert {leg.expires_at_ns for leg in receipt.legs} == {105_000_000_000}
    replay = _reserve(gate, scope, "group-r77", legs)
    assert replay == receipt

    leg = receipt.legs[0]
    claim = gate.claim_group_leg(
        scope,
        receipt.group_id,
        leg.leg_id,
        leg.permit_id,
        _intent(scope, leg.intent_id, "1", "100"),
        expected_group_fingerprint=receipt.group_fingerprint,
        expected_deadline_ns=receipt.deadline_ns,
    )
    assert claim.permit_reference == leg.permit_id
    assert claim.reserved_intent_hash == leg.reserved_intent_hash
    assert claim.actual_intent_hash != claim.reserved_intent_hash
    gate.settle(leg.permit_id)
    projected = gate.query_group_vector(scope, receipt.group_id)
    assert projected.legs[0].claimed is True
    assert projected.legs[0].state is RiskGroupReservationState.SETTLED
    with pytest.raises(RiskDeniedError) as unresolved:
        gate.release_group_unclaimed(scope, receipt.group_id, "owner-stopped")
    assert unresolved.value.code == "GROUP_CLAIM_UNRESOLVED"

    # Keyless rows never enter pool accounting.
    assert _pool_usage(gate, _POOL_ALPHA) == []
    with gate._connection() as connection:
        keyed = connection.execute(
            "SELECT COUNT(*) FROM risk_reservations WHERE resource_product_type IS NOT NULL"
        ).fetchone()[0]
    assert keyed == 0
    snapshot = gate.snapshot(scope)
    assert snapshot["increase_count"] == 3
    assert snapshot["increase_notional"] == Decimal("325")
    gate.close()


_RACE_WORKER = r"""
import hashlib
import json
import os
import sys
import time

config = json.loads(sys.argv[1])
sys.path[:0] = [config["risk_source"], config["base_source"]]

from decimal import Decimal

from bt_api_base.account_protocol import AccountResourceKey
from bt_api_risk.core.admission import (
    AccountScope,
    DurableRiskGate,
    IntentAction,
    RiskDeniedError,
    RiskGroupLegIntent,
    RiskIntent,
    RiskPolicy,
)

with open(config["ready_file"], "w", encoding="utf-8") as handle:
    handle.write("ready")

deadline = time.time() + 30.0
while not os.path.exists(config["start_file"]) and time.time() <= deadline:
    time.sleep(0.005)

if not os.path.exists(config["start_file"]):
    result = {"granted": None, "code": "START_TIMEOUT"}
else:
    scope = AccountScope("fixture", "account-1", "sandbox")
    gate = DurableRiskGate(
        config["db"],
        RiskPolicy(
            "margin-pool-policy",
            Decimal("1000"),
            8,
            permit_ttl_seconds=5.0,
            require_strategy_allocation=True,
            notional_unit="USD",
        ),
        clock=lambda: 100.0,
    )
    try:
        intent = RiskIntent(
            intent_id=config["intent_id"],
            scope=scope,
            action=IntentAction.INCREASE,
            notional=Decimal(config["notional"]),
            payload_fingerprint=hashlib.sha256(
                config["intent_id"].encode("utf-8")
            ).hexdigest(),
            strategy_id="strategy-1",
            allocation_version="allocation-v1",
            instrument=config["instrument"],
            quantity=Decimal("2"),
            quantity_unit="contract",
            notional_unit="USD",
            exposure_profile_fingerprint=config["profile"],
            instrument_facts_observed_at_ns=100000000000,
            instrument_facts_valid_until_ns=1000000000000,
            resource=AccountResourceKey("futures", "pool-alpha"),
        )
        gate.reserve_group_vector(
            scope,
            config["group_id"],
            (RiskGroupLegIntent("leg-a", intent),),
            deadline_ns=1200000000000,
        )
        result = {"granted": True, "code": None}
    except RiskDeniedError as exc:
        result = {"granted": False, "code": exc.code}
    except BaseException as exc:  # noqa: BLE001 -- surfaced through the result file
        result = {"granted": None, "code": type(exc).__name__ + ": " + str(exc)}
    finally:
        gate.close()

with open(config["result_file"], "w", encoding="utf-8") as handle:
    json.dump(result, handle)
"""


def test_two_processes_racing_one_pool_never_grant_more_than_the_cap(tmp_path):
    gate, scope, _ = _gate(tmp_path, database="race.sqlite3")
    gate.set_margin_pool_limit(
        scope, "futures", "pool-alpha", "pool-v1", max_notional=Decimal("300")
    )
    gate.close()

    risk_source = str(Path(bt_api_risk.__file__).resolve().parent.parent)
    base_source = str(Path(bt_api_base.__file__).resolve().parent.parent)
    start_file = tmp_path / "start.signal"
    workers = []
    ready_files = []
    result_files = []
    for group_id, instrument, profile in (
        ("race-a", "venue/contract-a", "a" * 64),
        ("race-b", "venue/contract-b", "b" * 64),
    ):
        ready_file = tmp_path / ("ready-" + group_id)
        result_file = tmp_path / ("result-" + group_id + ".json")
        ready_files.append(ready_file)
        result_files.append(result_file)
        config = {
            "risk_source": risk_source,
            "base_source": base_source,
            "db": str(tmp_path / "race.sqlite3"),
            "group_id": group_id,
            "intent_id": "intent-" + group_id,
            "instrument": instrument,
            "profile": profile,
            "notional": "200",
            "start_file": str(start_file),
            "ready_file": str(ready_file),
            "result_file": str(result_file),
        }
        workers.append(
            subprocess.Popen(  # noqa: S603 -- fixed interpreter and synthetic local config
                [sys.executable, "-c", _RACE_WORKER, json.dumps(config)],
                cwd=tmp_path,
                text=True,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
            )
        )

    ready_deadline = time.time() + 60.0
    while time.time() < ready_deadline and not all(
        ready_file.exists() for ready_file in ready_files
    ):
        time.sleep(0.01)
    start_file.write_text("go", encoding="utf-8")

    outputs = [worker.communicate(timeout=60) for worker in workers]
    for worker, (stdout_text, stderr_text) in zip(workers, outputs):
        assert worker.returncode == 0, stdout_text + stderr_text

    results = [json.loads(result_file.read_text(encoding="utf-8")) for result_file in result_files]
    granted = [result for result in results if result["granted"] is True]
    denied = [result for result in results if result["granted"] is False]
    assert len(granted) == 1, results
    assert len(denied) == 1, results
    assert denied[0]["code"] == "MARGIN_POOL_LIMIT_EXHAUSTED"

    # The loser wrote nothing: one reservation, no claims, no resolutions, no latches.
    restarted = DurableRiskGate(tmp_path / "race.sqlite3", gate.policy, clock=_Clock())
    try:
        assert _durable_counts(restarted) == (1, 0, 0, 0)
        loser = "race-b" if granted[0] is results[0] else "race-a"
        with pytest.raises(RiskDeniedError) as absent:
            restarted.query_group_vector(scope, loser)
        assert absent.value.code == "GROUP_NOT_FOUND"
        assert _pool_usage(restarted, _POOL_ALPHA) == [("active", "200")]
    finally:
        restarted.close()
