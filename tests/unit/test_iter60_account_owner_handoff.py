"""Iteration 60, step 60.10: account owner handoff (direct <-> Gateway).

QUIESCE -> FENCE -> RESTORE -> SYNC -> RECONCILE -> READY, persisted.
Covers: proof required (NO-GO without it), proof bound to account/generation,
no dispatch by anyone mid-handoff, the old generation never dispatches again,
crash and resume after every stage, unbalanced reconciliation blocks, a second
concurrent handoff is refused, and the audit trail is complete.
"""

from __future__ import annotations

import pytest

from bt_api_py.account_owner_handoff import (
    HANDOFF_STAGES,
    AccountOwnerHandoff,
    HandoffError,
    OwnerRevocationProof,
)

ACCOUNT = "account:sa2701"
OLD, NEW = "direct-host-1", "gateway-host-1"


def _proof(generation=1, account=ACCOUNT, kind="PROVIDER_SESSION_REVOKED"):
    return OwnerRevocationProof(account, generation, kind, "logout-receipt-42")


class _ProviderSessions:
    """Trusted source stand-in: which owners still hold a provider session."""

    def __init__(self):
        self.active = set()

    def verify(self, proof, old_owner):
        return old_owner not in self.active


SESSIONS = _ProviderSessions()


def _open(path, *, verifier=SESSIONS.verify):
    return AccountOwnerHandoff(path, ACCOUNT, initial_owner=OLD, proof_verifier=verifier)


def _run(handoff, *, upto):
    steps = {
        "QUIESCE": lambda: handoff.begin(from_owner=OLD, to_owner=NEW),
        "FENCE": lambda: handoff.fence(to_owner=NEW, proof=_proof()),
        "RESTORE": lambda: handoff.restore(to_owner=NEW, ledger_cut="journal-g1:120"),
        "SYNC": lambda: handoff.sync(to_owner=NEW, provider_snapshot_ref="snap-7"),
        "RECONCILE": lambda: handoff.reconcile(to_owner=NEW, reconciliation_state="BALANCED"),
        "READY": lambda: handoff.ready(to_owner=NEW),
    }
    for stage in HANDOFF_STAGES[: HANDOFF_STAGES.index(upto) + 1]:
        steps[stage]()


def test_full_handoff_moves_ownership_to_the_next_generation(tmp_path):
    handoff = _open(tmp_path / "owner.sqlite3")
    assert handoff.owner == (OLD, 1)
    assert handoff.may_dispatch(OLD, 1)
    _run(handoff, upto="READY")
    assert handoff.owner == (NEW, 2)
    assert handoff.stage is None
    assert handoff.may_dispatch(NEW, 2)
    assert not handoff.may_dispatch(OLD, 1)
    assert [action for action, _ in handoff.audit()] == list(HANDOFF_STAGES)


def test_without_revocation_proof_the_handoff_is_no_go(tmp_path):
    handoff = _open(tmp_path / "owner.sqlite3")
    handoff.begin(from_owner=OLD, to_owner=NEW)
    with pytest.raises(HandoffError) as info:
        handoff.fence(to_owner=NEW, proof=None)
    assert info.value.code == "HANDOFF_NO_GO_OLD_OWNER_NOT_PROVEN_REVOKED"
    assert handoff.stage == "QUIESCE"
    assert handoff.owner == (OLD, 1)


@pytest.mark.parametrize(
    "proof",
    [_proof(generation=2), _proof(account="account:other")],
    ids=["stale-generation", "other-account"],
)
def test_proof_must_be_bound_to_this_account_and_generation(tmp_path, proof):
    handoff = _open(tmp_path / "owner.sqlite3")
    handoff.begin(from_owner=OLD, to_owner=NEW)
    with pytest.raises(HandoffError) as info:
        handoff.fence(to_owner=NEW, proof=proof)
    assert info.value.code == "HANDOFF_PROOF_NOT_BOUND"


def test_storage_lease_is_not_an_accepted_proof_kind():
    with pytest.raises(HandoffError):
        OwnerRevocationProof(ACCOUNT, 1, "STORAGE_LEASE_EXPIRED", "lease-9")


@pytest.mark.parametrize("stage", HANDOFF_STAGES[:-1])
def test_nobody_dispatches_mid_handoff(tmp_path, stage):
    handoff = _open(tmp_path / "owner.sqlite3")
    _run(handoff, upto=stage)
    assert not handoff.may_dispatch(OLD, 1)
    assert not handoff.may_dispatch(NEW, 1)
    assert not handoff.may_dispatch(NEW, 2)


@pytest.mark.parametrize("crash_after", HANDOFF_STAGES[:-1])
def test_crash_after_any_stage_resumes_without_two_owners(tmp_path, crash_after):
    path = tmp_path / "owner.sqlite3"
    first = _open(path)
    _run(first, upto=crash_after)
    first.close()  # process dies here

    resumed = _open(path)
    assert resumed.stage == crash_after
    assert resumed.owner == (OLD, 1)
    # Re-running every stage from the start is idempotent up to the crash
    # point and then continues.
    _run(resumed, upto="READY")
    assert resumed.owner == (NEW, 2)
    actions = [action for action, _ in resumed.audit()]
    assert actions == list(HANDOFF_STAGES)  # no stage recorded twice


def test_unbalanced_reconciliation_blocks_for_manual_review(tmp_path):
    handoff = _open(tmp_path / "owner.sqlite3")
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


def test_switch_back_requires_its_own_proof_for_the_new_generation(tmp_path):
    handoff = _open(tmp_path / "owner.sqlite3")
    _run(handoff, upto="READY")
    handoff.begin(from_owner=NEW, to_owner=OLD)
    with pytest.raises(HandoffError):
        handoff.fence(to_owner=OLD, proof=_proof(generation=1))  # the old proof
    handoff.fence(to_owner=OLD, proof=_proof(generation=2))
    handoff.restore(to_owner=OLD, ledger_cut="journal-g1:300")
    handoff.sync(to_owner=OLD, provider_snapshot_ref="snap-9")
    handoff.reconcile(to_owner=OLD, reconciliation_state="BALANCED")
    assert handoff.ready(to_owner=OLD) == (OLD, 3)


# -- acceptance report P0-1 -----------------------------------------------------

import multiprocessing  # noqa: E402
import threading  # noqa: E402


def test_without_a_proof_verifier_the_handoff_is_no_go(tmp_path):
    handoff = _open(tmp_path / "owner.sqlite3", verifier=None)
    handoff.begin(from_owner=OLD, to_owner=NEW)
    with pytest.raises(HandoffError) as info:
        handoff.fence(to_owner=NEW, proof=_proof())
    assert info.value.code == "HANDOFF_NO_GO_PROOF_UNVERIFIABLE"
    assert handoff.stage == "QUIESCE"


def test_old_owner_still_holding_a_session_blocks_the_new_owner(tmp_path):
    sessions = _ProviderSessions()
    sessions.active.add(OLD)
    handoff = _open(tmp_path / "owner.sqlite3", verifier=sessions.verify)
    handoff.begin(from_owner=OLD, to_owner=NEW)
    with pytest.raises(HandoffError) as info:
        handoff.fence(to_owner=NEW, proof=_proof())
    assert info.value.code == "HANDOFF_NO_GO_PROOF_NOT_CONFIRMED"
    assert not handoff.may_dispatch(NEW, 2) and not handoff.may_dispatch(OLD, 1)
    sessions.active.clear()  # the old session is really gone now
    handoff.fence(to_owner=NEW, proof=_proof())
    assert handoff.stage == "FENCE"


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
    assert [action for action, _ in instances[0].audit()] == ["QUIESCE"]


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
