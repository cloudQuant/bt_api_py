"""Process-boundary fencing for coordinator restart and generation takeover.

Accepted R190 covers the two-account reservation crash/restart/replay chain;
accepted R229 covers account-owner recovery after strategy exit through a
distinct query registration. This focused test adds the missing cross-process
authority check: a fresh Registry registration cannot take over while an old
generation has an unresolved dispatch hold, then may activate after the hold
is confirmed while the original registration remains fenced.
"""

from __future__ import annotations

import multiprocessing
from contextlib import suppress
from pathlib import Path

import pytest

from bt_api_py.service_execution_group_authority import (
    ExecutionGroupAuthority,
    GroupAuthorityBusy,
    GroupCoordinatorDenied,
)
from bt_api_py.service_registry import ServiceCallContext
from tests.unit import test_service_execution_group_authority as authority_fixtures


def _second_coordinator_process(
    context_facts: tuple,
    authority_path: str,
    execution_scope,
    control,
) -> None:
    authority = None
    try:
        # The parent captured these exact fields from a real Registry call.
        # Its reply-size closure is process-local and deliberately is not
        # serialized; the authority/control path does not consume that field.
        second = ServiceCallContext(*context_facts)
        authority = ExecutionGroupAuthority(
            authority_path,
            completion_verifier=lambda _hold: True,
        )
        try:
            authority.control.activate(
                second,
                execution_scope=execution_scope,
                risk_account_scope_key=authority_fixtures._RISK_SCOPE_KEY,
            )
        except GroupAuthorityBusy:
            control.send(("blocked_by_unresolved_hold", second.registration_id))
        else:
            control.send(("unexpectedly_activated_before_release",))
            return

        if control.recv() != "retry_after_confirmed_release":
            control.send(("unexpected_parent_command",))
            return
        generation = authority.control.activate(
            second,
            execution_scope=execution_scope,
            risk_account_scope_key=authority_fixtures._RISK_SCOPE_KEY,
        )
        control.send(("activated", generation.generation, second.registration_id))

        if control.recv() != "exercise_new_generation":
            control.send(("unexpected_parent_command",))
            return
        intent, binding = authority_fixtures._intent_and_binding(
            coordinator_principal_ref=authority_fixtures._SECOND_PRINCIPAL,
            group_id="arb05-generation-two-group",
        )
        lease = authority_fixtures._writer_lease()
        with (
            authority.guard.bind_service_call(second, execution_scope=execution_scope),
            authority_fixtures._authorization(
                authority,
                second,
                intent=intent,
                binding=binding,
                lease=lease,
            ) as authorization,
        ):
            coordinator_epoch = authorization.coordinator_epoch
        control.send(("authorized", coordinator_epoch))

        if control.recv() != "close":
            control.send(("unexpected_parent_command",))
            return
        control.send(("closed",))
    except BaseException as error:
        with suppress(OSError, ValueError):
            control.send(("unexpected_error", type(error).__name__))
        raise
    finally:
        if authority is not None:
            authority.close()
        control.close()


def _receive_child_result(connection, *, timeout: float = 10) -> tuple:
    assert connection.poll(timeout), "spawned coordinator produced no bounded result"
    result = connection.recv()
    assert result[0] != "unexpected_error", result
    return result


@pytest.mark.integration
def test_second_process_cannot_replace_coordinator_generation_during_dispatch_hold(
    tmp_path: Path,
) -> None:
    registry_path = tmp_path / "registry.sqlite3"
    registry, _contexts = authority_fixtures._capture_registry_contexts(registry_path)
    authority_path = tmp_path / "authority.sqlite3"
    authority = ExecutionGroupAuthority(
        authority_path,
        completion_verifier=lambda _hold: True,
    )
    child = None
    parent_control = None
    child_control = None
    child_started = False
    try:
        first = authority_fixtures._register_and_dispatch(registry, session="arb05-generation-one")
        second = authority_fixtures._register_and_dispatch(
            registry,
            session="arb05-second-process-coordinator",
            principal=authority_fixtures._SECOND_PRINCIPAL,
        )
        second_context_facts = (
            second.identity,
            second.scope,
            second.registration_id,
            second.principal_ref,
        )
        execution_scope = authority_fixtures._execution_scope()
        first_generation = authority.control.activate(
            first,
            execution_scope=execution_scope,
            risk_account_scope_key=authority_fixtures._RISK_SCOPE_KEY,
        )
        assert first_generation.generation == 1

        intent, binding = authority_fixtures._intent_and_binding()
        lease = authority_fixtures._writer_lease()
        spawn = multiprocessing.get_context("spawn")
        parent_control, child_control = spawn.Pipe(duplex=True)
        child = spawn.Process(
            name="arb05-second-process-coordinator",
            target=_second_coordinator_process,
            args=(second_context_facts, str(authority_path), execution_scope, child_control),
        )
        with (
            authority.guard.bind_service_call(first, execution_scope=execution_scope),
            authority_fixtures._authorization(
                authority,
                first,
                intent=intent,
                binding=binding,
                lease=lease,
            ) as authorization,
        ):
            assert authorization.coordinator_epoch == 1
            assert len(authority.pending_holds()) == 1
            child.start()
            child_started = True
            child_control.close()
            blocked = _receive_child_result(parent_control)
            assert blocked[0] == "blocked_by_unresolved_hold", blocked
            second_registration_id = blocked[1]
            assert isinstance(second_registration_id, str) and second_registration_id
            assert second_registration_id != first.registration_id

        assert authority.pending_holds() == ()
        parent_control.send("retry_after_confirmed_release")
        activated = _receive_child_result(parent_control)
        assert activated == ("activated", 2, second_registration_id)

        with (
            authority.guard.bind_service_call(first, execution_scope=execution_scope),
            pytest.raises(GroupCoordinatorDenied),
            authority_fixtures._authorization(
                authority,
                first,
                intent=intent,
                binding=binding,
                lease=lease,
            ),
        ):
            pytest.fail("generation-one registration unexpectedly regained authority")

        parent_control.send("exercise_new_generation")
        assert _receive_child_result(parent_control) == ("authorized", 2)
        assert authority.pending_holds() == ()
        parent_control.send("close")
        assert _receive_child_result(parent_control) == ("closed",)
        child.join(10)
        assert not child.is_alive()
        assert child.exitcode == 0
        assert second_registration_id != first.registration_id
    finally:
        if child_started and child is not None and child.is_alive():
            with suppress(OSError, ValueError):
                if parent_control is not None:
                    parent_control.send("close")
            child.join(5)
            if child.is_alive():
                child.terminate()
                child.join(5)
        if child_started and child is not None and not child.is_alive():
            child.close()
        if parent_control is not None:
            with suppress(OSError, ValueError):
                parent_control.close()
        if child_control is not None:
            with suppress(OSError, ValueError):
                child_control.close()
        authority.close()
        registry.close()
