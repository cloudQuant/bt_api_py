"""Account-wide exclusivity: provable on one host, still open across hosts.

Each process opens its own coordination database for one synthetic account.
On one host (one shared host registry) the second database for the same
account binding is refused and only one process can hold the host owner
lock, so at most one process may dispatch. Two hosts are simulated with two
independent registry roots: nothing local can coordinate them, so that case
stays a strict expected failure (R10-1 cross-host OPEN). No provider or real
account is contacted.
"""

import multiprocessing

import pytest


def _local_owner(path, registry_root, owner, ready, start, results):
    from bt_api_py.account_coordination import AccountCoordinationBinding, HostAccountRegistry
    from bt_api_py.account_owner_handoff import AccountOwnerHandoff, HandoffError

    binding = AccountCoordinationBinding("synthetic-account", "fake:route-1")
    handoff = None
    try:
        ready.put(owner)
        if not start.wait(15):
            results.put((owner, "start-timeout"))
            return
        try:
            handoff = AccountOwnerHandoff(
                path,
                "synthetic-account",
                initial_owner=owner,
                binding=binding,
                host_registry=HostAccountRegistry(registry_root),
            )
            handoff.hold_owner_lock(owner, provider_session_ref=f"session-{owner}")
        except HandoffError as error:
            results.put((owner, error.code))
            return
        results.put((owner, handoff.may_dispatch(owner, 1)))
    finally:
        if handoff is not None:
            start.wait(15)
            handoff.close()


def _run_two_owners(tmp_path, registry_roots):
    ctx = multiprocessing.get_context("spawn")
    ready, results, start = ctx.Queue(), ctx.Queue(), ctx.Event()
    processes = [
        ctx.Process(
            target=_local_owner,
            args=(str(tmp_path / f"{i}.sqlite"), str(root), name, ready, start, results),
        )
        for i, (name, root) in enumerate(zip(("owner-a", "owner-b"), registry_roots))
    ]
    for process in processes:
        process.start()
    try:
        owners = {ready.get(timeout=30), ready.get(timeout=30)}
        if owners != {"owner-a", "owner-b"}:
            raise RuntimeError("worker startup was not complete")
        start.set()
        outcomes = dict((results.get(timeout=30), results.get(timeout=30)))
    finally:
        start.set()
        for process in processes:
            process.join(timeout=30)
    if any(process.exitcode != 0 for process in processes):
        raise RuntimeError("worker did not exit successfully")
    if set(outcomes) != owners:
        raise RuntimeError("worker did not return valid local admission outcomes")
    return outcomes


def test_same_host_second_database_for_the_account_cannot_dispatch(tmp_path):
    host = tmp_path / "host"
    outcomes = _run_two_owners(tmp_path, (host, host))
    assert sorted(outcomes.values(), key=str) == sorted(
        [True, "HANDOFF_COORDINATION_STORE_CONFLICT"], key=str
    )


@pytest.mark.xfail(
    strict=True,
    raises=AssertionError,
    reason="R10-1 cross-host OPEN: independent hosts need provider-side session proof",
)
def test_cross_host_databases_do_not_prove_account_exclusivity(tmp_path):
    outcomes = _run_two_owners(tmp_path, (tmp_path / "host-a", tmp_path / "host-b"))
    assert sum(value is True for value in outcomes.values()) <= 1
