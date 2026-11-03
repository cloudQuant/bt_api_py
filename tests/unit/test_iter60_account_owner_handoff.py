"""Iteration 60, step 60.10: account owner handoff (direct <-> Gateway).

QUIESCE -> FENCE -> RESTORE -> SYNC -> RECONCILE -> READY, persisted.
Covers diagnostic state progression, provider-session revocation evidence at
FENCE, and fail-closed promotion when recovery is not independently verified.
Dispatch-lease interleavings live in ``test_iter60_account_dispatch_lease.py``.
All accounts, routes and sessions are synthetic; no provider is contacted.
"""

from __future__ import annotations

import time

import pytest

from bt_api_py.account_coordination import AccountCoordinationBinding, HostAccountRegistry
from bt_api_py.account_owner_handoff import (
    HANDOFF_STAGES,
    AccountOwnerHandoff,
    HandoffError,
)
from bt_api_py.provider_session_revocation import (
    ProviderSessionRevocationEvidence,
    RevocationEvidenceError,
)

ACCOUNT = "account:sa2701"
ROUTE = "ctp:fake-front-1"
BINDING = AccountCoordinationBinding(ACCOUNT, ROUTE)
OLD, NEW = "direct-host-1", "gateway-host-1"
SESSION = "fake-session-1"


def _proof(generation=1, account=ACCOUNT, route=ROUTE, session=SESSION, observed=None):
    return ProviderSessionRevocationEvidence(
        account_ref=account,
        provider_route=route,
        revoked_owner_generation=generation,
        revoked_session_ref=session,
        kind="PROVIDER_LOGOUT_RECEIPT",
        observed_at_ns=time.time_ns() if observed is None else observed,
        evidence_ref="logout-receipt-42",
    )


class _ProviderSessions:
    """Trusted source stand-in: which provider sessions are still active."""

    def __init__(self):
        self.active = set()

    def verify_session_revocation(self, evidence):
        return evidence.revoked_session_ref not in self.active


SESSIONS = _ProviderSessions()
_DEFAULT = object()


def _open(path, *, verifier=_DEFAULT):
    verifier = SESSIONS if verifier is _DEFAULT else verifier
    return AccountOwnerHandoff(
        path,
        ACCOUNT,
        initial_owner=OLD,
        binding=BINDING,
        revocation_verifier=verifier,
        host_registry=HostAccountRegistry(path.parent / "host"),
    )


def _owned(path, **kwargs):
    handoff = _open(path, **kwargs)
    handoff.hold_owner_lock(OLD, provider_session_ref=SESSION)
    return handoff


def _run(handoff, *, upto):
    steps = {
        "QUIESCE": lambda: handoff.begin(from_owner=OLD, to_owner=NEW),
        "FENCE": lambda: handoff.fence(to_owner=NEW, evidence=_proof()),
        "RESTORE": lambda: handoff.restore(to_owner=NEW, ledger_cut="journal-g1:120"),
        "SYNC": lambda: handoff.sync(to_owner=NEW, provider_snapshot_ref="snap-7"),
        "RECONCILE": lambda: handoff.reconcile(to_owner=NEW, reconciliation_state="BALANCED"),
        "READY": lambda: handoff.ready(to_owner=NEW),
    }
    for stage in HANDOFF_STAGES[: HANDOFF_STAGES.index(upto) + 1]:
        steps[stage]()


def _actions(handoff):
    return [action for action, _ in handoff.audit() if action != "OWNER_LOCK"]


def test_full_handoff_stays_blocked_without_verified_recovery(tmp_path):
    handoff = _owned(tmp_path / "owner.sqlite3")
    assert handoff.owner == (OLD, 1)
    assert handoff.may_dispatch(OLD, 1)
    _run(handoff, upto="SYNC")
    with pytest.raises(HandoffError, match="HANDOFF_NO_GO_RECOVERY_UNVERIFIED"):
        handoff.reconcile(to_owner=NEW, reconciliation_state="BALANCED")
    assert handoff.owner == (OLD, 1)
    assert handoff.stage == "SYNC"
    assert not handoff.may_dispatch(NEW, 2)
    assert not handoff.may_dispatch(OLD, 1)
    assert _actions(handoff) == [*HANDOFF_STAGES[:4], "RECONCILE_UNVERIFIED"]


def test_unbound_handoff_is_diagnostic_only(tmp_path):
    handoff = AccountOwnerHandoff(tmp_path / "owner.sqlite3", ACCOUNT, initial_owner=OLD)
    try:
        assert not handoff.may_dispatch(OLD, 1)
        with pytest.raises(HandoffError, match="HANDOFF_DISPATCH_UNBOUND"):
            handoff.hold_owner_lock(OLD, provider_session_ref=SESSION)
        with pytest.raises(HandoffError, match="HANDOFF_DISPATCH_UNBOUND"):
            handoff.acquire_dispatch_lease(OLD, 1, operation="submit", intent_id="i-1")
        handoff.begin(from_owner=OLD, to_owner=NEW)  # removing permission is always safe
        with pytest.raises(HandoffError, match="HANDOFF_COORDINATOR_UNBOUND"):
            handoff.fence(to_owner=NEW, evidence=_proof())
    finally:
        handoff.close()


def test_without_revocation_evidence_the_takeover_is_refused(tmp_path):
    handoff = _owned(tmp_path / "owner.sqlite3")
    handoff.begin(from_owner=OLD, to_owner=NEW)
    with pytest.raises(HandoffError) as info:
        handoff.fence(to_owner=NEW, evidence=None)
    assert info.value.code == "HANDOFF_NO_GO_OLD_SESSION_NOT_REVOKED"
    assert handoff.stage == "QUIESCE"
    assert handoff.owner == (OLD, 1)


def test_unknown_old_session_refuses_takeover(tmp_path):
    handoff = _open(tmp_path / "owner.sqlite3")  # the owner never registered a session
    handoff.begin(from_owner=OLD, to_owner=NEW)
    with pytest.raises(HandoffError, match="HANDOFF_NO_GO_OLD_SESSION_UNKNOWN"):
        handoff.fence(to_owner=NEW, evidence=_proof())
    assert handoff.stage == "QUIESCE"


@pytest.mark.parametrize(
    "kwargs",
    [
        {"generation": 2},
        {"account": "account:other"},
        {"route": "ctp:other-front"},
        {"session": "another-session"},
    ],
    ids=["stale-generation", "other-account", "other-route", "other-session"],
)
def test_evidence_must_be_bound_to_account_route_generation_and_session(tmp_path, kwargs):
    handoff = _owned(tmp_path / "owner.sqlite3")
    handoff.begin(from_owner=OLD, to_owner=NEW)
    with pytest.raises(HandoffError) as info:
        handoff.fence(to_owner=NEW, evidence=_proof(**kwargs))
    assert info.value.code == "HANDOFF_REVOCATION_NOT_BOUND"
    assert handoff.stage == "QUIESCE"


def test_evidence_observed_before_quiesce_is_stale(tmp_path):
    handoff = _owned(tmp_path / "owner.sqlite3")
    early = _proof(observed=time.time_ns() - 1)
    handoff.begin(from_owner=OLD, to_owner=NEW)
    with pytest.raises(HandoffError, match="HANDOFF_REVOCATION_EVIDENCE_STALE"):
        handoff.fence(to_owner=NEW, evidence=early)


def test_storage_lease_is_not_an_accepted_evidence_kind():
    with pytest.raises(RevocationEvidenceError):
        ProviderSessionRevocationEvidence(
            ACCOUNT, ROUTE, 1, SESSION, "STORAGE_LEASE_EXPIRED", time.time_ns(), "lease-9"
        )


@pytest.mark.parametrize("stage", HANDOFF_STAGES[:4])
def test_nobody_dispatches_mid_handoff(tmp_path, stage):
    handoff = _owned(tmp_path / "owner.sqlite3")
    _run(handoff, upto=stage)
    assert not handoff.may_dispatch(OLD, 1)
    assert not handoff.may_dispatch(NEW, 1)
    assert not handoff.may_dispatch(NEW, 2)
    with pytest.raises(HandoffError, match="HANDOFF_DISPATCH_FENCED"):
        handoff.acquire_dispatch_lease(OLD, 1, operation="submit", intent_id="late")


@pytest.mark.parametrize("crash_after", HANDOFF_STAGES[:4])
def test_crash_after_any_stage_resumes_but_promotion_remains_blocked(tmp_path, crash_after):
    path = tmp_path / "owner.sqlite3"
    first = _owned(path)
    _run(first, upto=crash_after)
    first.close()  # process dies here

    resumed = _open(path)
    assert resumed.stage == crash_after
    assert resumed.owner == (OLD, 1)
    # Re-running the diagnostic stages is idempotent after a restart; the
    # unverified ledger/snapshot strings cannot release the new owner.
    _run(resumed, upto="SYNC")
    with pytest.raises(HandoffError, match="HANDOFF_NO_GO_RECOVERY_UNVERIFIED"):
        resumed.reconcile(to_owner=NEW, reconciliation_state="BALANCED")
    assert resumed.owner == (OLD, 1)
    assert not resumed.may_dispatch(NEW, 2)
    assert _actions(resumed) == [*HANDOFF_STAGES[:4], "RECONCILE_UNVERIFIED"]


def test_unbalanced_reconciliation_blocks_for_manual_review(tmp_path):
    handoff = _owned(tmp_path / "owner.sqlite3")
    _run(handoff, upto="SYNC")
    with pytest.raises(HandoffError) as info:
        handoff.reconcile(to_owner=NEW, reconciliation_state="RESIDUAL")
    assert info.value.code == "HANDOFF_RECONCILIATION_NOT_BALANCED"
    assert handoff.stage == "SYNC"
    with pytest.raises(HandoffError):
        handoff.ready(to_owner=NEW)
    assert handoff.audit()[-1][0] == "RECONCILE_BLOCKED"


def test_a_second_handoff_cannot_start_while_one_is_in_progress(tmp_path):
    handoff = _open(tmp_path / "owner.sqlite3")
    handoff.begin(from_owner=OLD, to_owner=NEW)
    with pytest.raises(HandoffError) as info:
        handoff.begin(from_owner=OLD, to_owner="another-host")
    assert info.value.code == "HANDOFF_ALREADY_IN_PROGRESS"


def test_stages_cannot_be_skipped(tmp_path):
    handoff = _open(tmp_path / "owner.sqlite3")
    handoff.begin(from_owner=OLD, to_owner=NEW)
    with pytest.raises(HandoffError) as info:
        handoff.sync(to_owner=NEW, provider_snapshot_ref="snap")
    assert info.value.code == "HANDOFF_STAGE_OUT_OF_ORDER"


def test_legacy_promoted_owner_is_not_trusted_after_restart(tmp_path):
    handoff = _open(tmp_path / "owner.sqlite3")
    # Simulate a database persisted by the previous implementation, which
    # could mark READY based on caller-supplied strings and a fake verifier.
    with handoff._atomic() as db:
        db.execute(
            "UPDATE account_owner SET owner_id = ?, generation = 2 WHERE account_key = ?",
            (NEW, ACCOUNT),
        )
    handoff.hold_owner_lock(NEW, provider_session_ref="session-g2")
    assert not handoff.may_dispatch(NEW, 2)
    with pytest.raises(HandoffError, match="HANDOFF_NO_GO_RECOVERY_UNVERIFIED"):
        handoff.ready(to_owner=NEW)
    handoff.begin(from_owner=NEW, to_owner=OLD)
    with pytest.raises(HandoffError):
        handoff.fence(to_owner=OLD, evidence=_proof(generation=1))  # the old proof
    handoff.fence(to_owner=OLD, evidence=_proof(generation=2, session="session-g2"))
    handoff.restore(to_owner=OLD, ledger_cut="journal-g1:300")
    handoff.sync(to_owner=OLD, provider_snapshot_ref="snap-9")
    with pytest.raises(HandoffError, match="HANDOFF_NO_GO_RECOVERY_UNVERIFIED"):
        handoff.reconcile(to_owner=OLD, reconciliation_state="BALANCED")
    assert not handoff.may_dispatch(OLD, 3)


def test_legacy_reconcile_stage_does_not_enable_promotion(tmp_path):
    handoff = _open(tmp_path / "owner.sqlite3")
    # An old database may already contain the once-accepted stage.
    with handoff._atomic() as db:
        db.execute(
            "UPDATE account_owner SET stage = ?, target_owner = ? WHERE account_key = ?",
            ("RECONCILE", NEW, ACCOUNT),
        )
    with pytest.raises(HandoffError, match="HANDOFF_NO_GO_RECOVERY_UNVERIFIED"):
        handoff.reconcile(to_owner=NEW, reconciliation_state="BALANCED")
    with pytest.raises(HandoffError, match="HANDOFF_NO_GO_RECOVERY_UNVERIFIED"):
        handoff.ready(to_owner=NEW)
    assert not handoff.may_dispatch(NEW, 2)


# -- acceptance report P0-1 / re-check G1 ----------------------------------------

import multiprocessing  # noqa: E402
import threading  # noqa: E402


def test_without_a_revocation_verifier_the_takeover_is_refused(tmp_path):
    handoff = _owned(tmp_path / "owner.sqlite3", verifier=None)
    handoff.begin(from_owner=OLD, to_owner=NEW)
    with pytest.raises(HandoffError) as info:
        handoff.fence(to_owner=NEW, evidence=_proof())
    assert info.value.code == "HANDOFF_NO_GO_REVOCATION_UNVERIFIABLE"
    assert handoff.stage == "QUIESCE"


def test_verifier_without_the_protocol_method_is_rejected(tmp_path):
    with pytest.raises(HandoffError, match="HANDOFF_REVOCATION_VERIFIER_INVALID"):
        _open(tmp_path / "owner.sqlite3", verifier=lambda evidence: True)


def test_old_session_still_active_at_provider_blocks_the_new_owner(tmp_path):
    sessions = _ProviderSessions()
    sessions.active.add(SESSION)
    handoff = _owned(tmp_path / "owner.sqlite3", verifier=sessions)
    handoff.begin(from_owner=OLD, to_owner=NEW)
    with pytest.raises(HandoffError) as info:
        handoff.fence(to_owner=NEW, evidence=_proof())
    assert info.value.code == "HANDOFF_NO_GO_REVOCATION_NOT_CONFIRMED"
    assert not handoff.may_dispatch(NEW, 2) and not handoff.may_dispatch(OLD, 1)
    sessions.active.clear()  # the provider now confirms the old session is gone
    handoff.fence(to_owner=NEW, evidence=_proof())
    assert handoff.stage == "FENCE"


def test_raising_verifier_is_not_a_confirmation(tmp_path):
    class _Broken:
        def verify_session_revocation(self, evidence):
            raise OSError("provider unreachable")

    handoff = _owned(tmp_path / "owner.sqlite3", verifier=_Broken())
    handoff.begin(from_owner=OLD, to_owner=NEW)
    with pytest.raises(HandoffError, match="HANDOFF_NO_GO_REVOCATION_NOT_CONFIRMED"):
        handoff.fence(to_owner=NEW, evidence=_proof())


def test_concurrent_begins_across_instances_admit_exactly_one(tmp_path):
    path = tmp_path / "owner.sqlite3"
    instances = [_open(path) for _ in range(6)]
    barrier = threading.Barrier(len(instances))
    results = []

    def attempt(index, handoff):
        barrier.wait()
        try:
            handoff.begin(from_owner=OLD, to_owner=f"new-{index}")
            results.append("ok")
        except HandoffError as error:
            results.append(error.code)

    threads = [threading.Thread(target=attempt, args=item) for item in enumerate(instances)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()
    assert results.count("ok") == 1
    assert results.count("HANDOFF_ALREADY_IN_PROGRESS") == len(instances) - 1
    assert _actions(instances[0]) == ["QUIESCE"]


def _begin_in_process(path, index, start, queue):
    handoff = AccountOwnerHandoff(path, ACCOUNT, initial_owner=OLD)
    start.wait()
    try:
        handoff.begin(from_owner=OLD, to_owner=f"proc-{index}")
        queue.put("ok")
    except HandoffError as error:
        queue.put(error.code)
    finally:
        handoff.close()


def test_concurrent_begins_across_processes_admit_exactly_one(tmp_path):
    path = str(tmp_path / "owner.sqlite3")
    AccountOwnerHandoff(path, ACCOUNT, initial_owner=OLD).close()
    context = multiprocessing.get_context("spawn")
    start, queue = context.Event(), context.Queue()
    processes = [
        context.Process(target=_begin_in_process, args=(path, index, start, queue))
        for index in range(4)
    ]
    for process in processes:
        process.start()
    start.set()
    results = [queue.get(timeout=60) for _ in processes]
    for process in processes:
        process.join(timeout=60)
    assert results.count("ok") == 1
    handoff = AccountOwnerHandoff(path, ACCOUNT, initial_owner=OLD)
    try:
        assert [action for action, _ in handoff.audit()] == ["QUIESCE"]
    finally:
        handoff.close()
