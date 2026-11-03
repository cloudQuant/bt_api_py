"""The SQLite owner record alone does not prove account-wide exclusivity.

The expected failure records an open R10-1 safety gap, not an acceptance of
multiple writers. Each process uses a private temporary database and a
synthetic account; no provider or real account is contacted.
"""

import multiprocessing

import pytest


def _local_owner(path, owner, ready, start, results):
    from bt_api_py.account_owner_handoff import AccountOwnerHandoff

    handoff = AccountOwnerHandoff(path, "synthetic-account", initial_owner=owner)
    try:
        ready.put(owner)
        if not start.wait(15):
            results.put((owner, "start-timeout"))
            return
        results.put((owner, handoff.may_dispatch(owner, 1)))
    finally:
        handoff.close()


@pytest.mark.xfail(
    strict=True,
    raises=AssertionError,
    reason="R10-1 OPEN: independent local databases do not enforce a single account writer",
)
def test_distinct_owner_databases_do_not_prove_account_exclusivity(tmp_path):
    ctx = multiprocessing.get_context("spawn")
    ready, results, start = ctx.Queue(), ctx.Queue(), ctx.Event()
    processes = [
        ctx.Process(
            target=_local_owner, args=(str(tmp_path / f"{i}.sqlite"), name, ready, start, results)
        )
        for i, name in enumerate(("owner-a", "owner-b"))
    ]
    for process in processes:
        process.start()
    try:
        owners = {ready.get(timeout=15), ready.get(timeout=15)}
        if owners != {"owner-a", "owner-b"}:
            raise RuntimeError("worker startup was not complete")
        start.set()
        outcomes = dict((results.get(timeout=15), results.get(timeout=15)))
    finally:
        start.set()
        for process in processes:
            process.join(timeout=15)
    if any(process.exitcode != 0 for process in processes):
        raise RuntimeError("worker did not exit successfully")
    if set(outcomes) != owners or any(type(value) is not bool for value in outcomes.values()):
        raise RuntimeError("worker did not return valid local admission outcomes")
    # The desired account-wide invariant is currently unmet when both are True.
    assert sum(outcomes.values()) <= 1
