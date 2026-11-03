"""Iteration 60, step 60.10: account owner handoff (direct <-> Gateway).

QUIESCE -> FENCE -> RESTORE -> SYNC -> RECONCILE -> READY, persisted.
Covers diagnostic state progression and fail-closed promotion when recovery
and provider-dispatch evidence is not available. State checks do not claim to
atomically prevent provider calls already admitted before QUIESCE.
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


def test_full_handoff_stays_blocked_without_verified_recovery(tmp_path):
    handoff = _open(tmp_path / "owner.sqlite3")
    assert handoff.owner == (OLD, 1)
    assert handoff.may_dispatch(OLD, 1)
    _run(handoff, upto="SYNC")
    with pytest.raises(HandoffError, match="HANDOFF_NO_GO_RECOVERY_UNVERIFIED"):
        handoff.reconcile(to_owner=NEW, reconciliation_state="BALANCED")
    assert handoff.owner == (OLD, 1)
    assert handoff.stage == "SYNC"
    assert not handoff.may_dispatch(NEW, 2)
    assert not handoff.may_dispatch(OLD, 1)
    assert [action for action, _ in handoff.audit()] == [
        *HANDOFF_STAGES[:4], "RECONCILE_UNVERIFIED"
    ]


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


@pytest.mark.parametrize("stage", HANDOFF_STAGES[:4])
def test_nobody_dispatches_mid_handoff(tmp_path, stage):
    handoff = _open(tmp_path / "owner.sqlite3")
    _run(handoff, upto=stage)
    assert not handoff.may_dispatch(OLD, 1)
    assert not handoff.may_dispatch(NEW, 1)
    assert not handoff.may_dispatch(NEW, 2)


@pytest.mark.parametrize("crash_after", HANDOFF_STAGES[:4])
def test_crash_after_any_stage_resumes_but_promotion_remains_blocked(tmp_path, crash_after):
    path = tmp_path / "owner.sqlite3"
    first = _open(path)
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
    actions = [action for action, _ in resumed.audit()]
    assert actions == [*HANDOFF_STAGES[:4], "RECONCILE_UNVERIFIED"]


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


def test_legacy_promoted_owner_is_not_trusted_after_restart(tmp_path):
    handoff = _open(tmp_path / "owner.sqlite3")
    # Simulate a database persisted by the previous implementation, which
    # could mark READY based on caller-supplied strings and a fake verifier.
    with handoff._atomic() as db:
        db.execute(
            "UPDATE account_owner SET owner_id = ?, generation = 2 WHERE account_key = ?",
            (NEW, ACCOUNT),
        )
    assert not handoff.may_dispatch(NEW, 2)
    with pytest.raises(HandoffError, match="HANDOFF_NO_GO_RECOVERY_UNVERIFIED"):
        handoff.ready(to_owner=NEW)
    handoff.begin(from_owner=NEW, to_owner=OLD)
    with pytest.raises(HandoffError):
        handoff.fence(to_owner=OLD, proof=_proof(generation=1))  # the old proof
    handoff.fence(to_owner=OLD, proof=_proof(generation=2))
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


def test_inflight_old_dispatch_does_not_enable_a_new_owner(tmp_path):
    """The local stage change cannot cancel an already admitted provider call."""
    import asyncio

    from bt_api_py.correlated_command_dispatch import dispatch_correlated_command

    handoff = _open(tmp_path / "owner.sqlite3")
    correlation = object()
    sent = []

    class Acknowledgement:
        operation = "submit"
        disposition = "accepted"

        def __init__(self, expected):
            self.correlation = expected

    class Facade:
        def claim_correlated_order(self, *_args, **_kwargs):
            return object()

        async def submit_correlated_order(self, *_args, **_kwargs):
            # The old call passed its check, then handoff begins before the
            # provider side effect. This ordering is explicitly permitted by
            # this test and must not be mistaken for a provider-side fence.
            handoff.begin(from_owner=OLD, to_owner=NEW)
            sent.append("provider invoked")
            return {}

        def verify_order_response(self, expected, _result):
            return Acknowledgement(expected)

    def before_dispatch():
        assert handoff.may_dispatch(OLD, 1)

    asyncio.run(
        dispatch_correlated_command(
            "submit", Facade(), correlation, object(), object(),
            acknowledgement_type=Acknowledgement,
            error_type=RuntimeError,
            before_dispatch=before_dispatch,
            sanitize_exception=lambda _error: None,
        )
    )
    assert sent == ["provider invoked"]
    assert handoff.stage == "QUIESCE"
    assert not handoff.may_dispatch(OLD, 1)
    assert not handoff.may_dispatch(NEW, 2)
    with pytest.raises(HandoffError):
        handoff.ready(to_owner=NEW)


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
