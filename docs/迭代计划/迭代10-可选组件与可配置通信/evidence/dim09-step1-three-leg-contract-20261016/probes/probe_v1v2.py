"""Independent v1/v2 byte-compatibility probe (DIM09 review evidence).

Runs the same v2 harness scenarios against whichever bt_api_execution package
is first on PYTHONPATH:
  NEW  -> D:/bt_api_py/bt_api/bt_api_execution/src (dirty worktree under review)
  OLD  -> D:/temp/it10dim09-review-20261016-r1/compat/old_pkg (git HEAD dc8bd7a)
The scenario builder code is identical (imported from the NEW test helper
module in both runs), so any output diff is caused by the package version.
Prints canonical JSON + sha256 for every v1/v2 serialization surface.
"""

from __future__ import annotations

import hashlib
import json
import sys
from pathlib import Path

sys.path.insert(0, "D:/bt_api_py/bt_api/bt_api_execution/tests")

import test_group_coordinator_three_leg as t  # noqa: E402
from bt_api_execution import (  # noqa: E402
    ExecutionState,
    GroupOrderSnapshot,
    GroupOrderState,
    SqliteOrderGroupStore,
)

ROOT = Path("D:/temp/it10dim09-review-20261016-r1/compat/run")


def canon(name: str, payload: object) -> None:
    data = json.dumps(
        payload, sort_keys=True, separators=(",", ":"), ensure_ascii=True
    ).encode("ascii")
    print(f"{name} sha256={hashlib.sha256(data).hexdigest()}")
    print(f"  json={data.decode('ascii')}")


def canon_roundtrip(name: str, payload: dict) -> None:
    canon(name + "_roundtrip", GroupOrderSnapshot.from_payload(payload).to_payload())


def dump_events(store, request):
    return [
        {"event_type": e.event_type, "payload": dict(e.payload)}
        for e in store.events(request.portfolio_ref, request.group_id)
    ]


def scenario_accept() -> None:
    request = t._make_request_v2()
    assert request.version == 1 and request.budget_quote is None
    canon("S1_v1_request", request.to_payload())
    print(f"S1b_v1_unquoted_fingerprint {request.unquoted_fingerprint}")
    run_dir = ROOT / "s1"
    run_dir.mkdir(parents=True, exist_ok=True)
    coordinator, store, _reservations, _orders = t._coordinator(run_dir, request)
    accepted = coordinator.accept(request)
    canon("S2_v2_quote", accepted.request.budget_quote.to_payload())
    canon("S3_v2_request", accepted.request.to_payload())
    canon("S4_v2_accepted_snapshot", accepted.to_payload())
    canon_roundtrip("S4_v2_accepted_snapshot", accepted.to_payload())
    canon("S5_accept_events", dump_events(store, request))
    store.close()


def scenario_paired() -> None:
    request = t._make_request_v2()
    run_dir = ROOT / "s6"
    run_dir.mkdir(parents=True, exist_ok=True)
    coordinator, store, _reservations, orders = t._coordinator(run_dir, request)
    first, hedge = request.ordered_legs
    orders["account-a"].terminal_on_submit[first.maximum_intent.intent_id] = (
        ExecutionState.CANCELLED,
        t.Decimal("1.5"),
        1,
        3,
    )
    orders["account-a"].terminal_on_submit[hedge.maximum_intent.intent_id] = (
        ExecutionState.FILLED,
        t.Decimal("0.75"),
        1,
        5,
    )
    coordinator.accept(request)
    result = coordinator.advance_group(request.portfolio_ref, request.group_id)
    assert result.state is GroupOrderState.PAIRED, result
    canon("S6_v2_paired_snapshot", result.to_payload())
    canon_roundtrip("S6_v2_paired_snapshot", result.to_payload())
    canon("S6b_v2_paired_events", dump_events(store, request))
    store.close()


def scenario_residual() -> None:
    request = t._make_request_v2()
    run_dir = ROOT / "s7"
    run_dir.mkdir(parents=True, exist_ok=True)
    coordinator, store, _reservations, orders = t._coordinator(run_dir, request)
    first, hedge = request.ordered_legs
    orders["account-a"].terminal_on_submit[first.maximum_intent.intent_id] = (
        ExecutionState.CANCELLED,
        t.Decimal("1.5"),
        1,
        3,
    )
    orders["account-a"].terminal_on_submit[hedge.maximum_intent.intent_id] = (
        ExecutionState.FILLED,
        t.Decimal("0.5"),
        1,
        5,
    )
    coordinator.accept(request)
    result = coordinator.advance_group(request.portfolio_ref, request.group_id)
    assert result.state is GroupOrderState.RECOVERY_REQUIRED, result
    assert result.error_code == "hedge_residual_unresolved", result
    canon("S7_v2_hedge_residual_snapshot", result.to_payload())
    canon_roundtrip("S7_v2_hedge_residual_snapshot", result.to_payload())
    canon("S7b_v2_hedge_residual_events", dump_events(store, request))
    store.close()


def scenario_v1_legacy() -> None:
    request = t._make_request_v2()
    run_dir = ROOT / "s8"
    run_dir.mkdir(parents=True, exist_ok=True)
    store = SqliteOrderGroupStore(run_dir / "groups.sqlite")
    snapshot = store.create_or_get(request, budget_policy=None, now_ns=t._NOW + 5)
    canon("S8_v1_legacy_snapshot", snapshot.to_payload())
    canon_roundtrip("S8_v1_legacy_snapshot", snapshot.to_payload())
    store.close()

    run_dir2 = ROOT / "s8b"
    run_dir2.mkdir(parents=True, exist_ok=True)
    coordinator, store2, _reservations, _orders = t._coordinator(run_dir2, request)
    store2.create_or_get(request, budget_policy=None, now_ns=t._NOW + 5)
    result = coordinator.advance_group(request.portfolio_ref, request.group_id)
    assert result.state is GroupOrderState.RECOVERY_REQUIRED, result
    assert result.error_code == "legacy_group_budget_unavailable", result
    canon("S9_v1_legacy_recovery_snapshot", result.to_payload())
    canon("S9b_v1_legacy_recovery_events", dump_events(store2, request))
    store2.close()


scenario_accept()
scenario_paired()
scenario_residual()
scenario_v1_legacy()
print("PROBE_DONE")
