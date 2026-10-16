"""ARB05 account-owner deadline checks use the durable expiry clock."""

from __future__ import annotations

import multiprocessing
import sqlite3
from dataclasses import replace

import pytest

from bt_api_py.service_execution_group_authority import (
    ExecutionGroupAuthority,
    GroupCoordinatorDenied,
)
from tests.unit import test_service_execution_group_authority as authority_fixtures


def _attempt_after_authority_restart(
    database_path,
    context,
    execution_scope,
    intent,
    binding,
    lease,
    wall_now_ns,
    monotonic_now_ns,
    send_connection,
) -> None:
    authority = None
    try:
        authority = ExecutionGroupAuthority(
            database_path,
            wall_clock_ns=lambda: wall_now_ns,
            monotonic_clock_ns=lambda: monotonic_now_ns,
        )
        with authority.guard.bind_service_call(context, execution_scope=execution_scope):
            try:
                with authority_fixtures._authorization(
                    authority,
                    context,
                    intent=intent,
                    binding=binding,
                    lease=lease,
                ):
                    send_connection.send(("unexpected_authorization",))
            except GroupCoordinatorDenied:
                send_connection.send(("denied", authority.pending_holds()))
    except BaseException as error:
        send_connection.send(("unexpected_error", type(error).__name__))
    finally:
        if authority is not None:
            authority.close()
        send_connection.close()


def test_owner_guard_rejects_expired_epoch_deadline_before_creating_a_hold(
    tmp_path,
) -> None:
    wall_now_ns = 1_800_000_000_000_000_000
    monotonic_now_ns = 80_000_000_000
    registry, _contexts = authority_fixtures._capture_registry_contexts(
        tmp_path / "registry.sqlite3"
    )
    authority = ExecutionGroupAuthority(
        tmp_path / "authority.sqlite3",
        wall_clock_ns=lambda: wall_now_ns,
        monotonic_clock_ns=lambda: monotonic_now_ns,
    )
    try:
        context = authority_fixtures._register_and_dispatch(
            registry, session="arb05-expired-epoch-deadline"
        )
        execution_scope = authority_fixtures._execution_scope()
        authority.control.activate(
            context,
            execution_scope=execution_scope,
            risk_account_scope_key=authority_fixtures._RISK_SCOPE_KEY,
        )
        intent, binding = authority_fixtures._intent_and_binding()
        expired_deadline_ns = wall_now_ns - 1
        intent = replace(intent, deadline_ns=expired_deadline_ns)
        binding = replace(
            binding,
            deadline_ns=expired_deadline_ns,
            order_intent_hash=intent.fingerprint,
        )
        lease = authority_fixtures._writer_lease()

        with (
            authority.guard.bind_service_call(context, execution_scope=execution_scope),
            pytest.raises(GroupCoordinatorDenied, match="deadline has expired"),
            authority_fixtures._authorization(
                authority,
                context,
                intent=intent,
                binding=binding,
                lease=lease,
            ),
        ):
            pytest.fail("expired authorization unexpectedly acquired a hold")

        assert authority.pending_holds() == ()
    finally:
        authority.close()
        registry.close()


def test_wall_clock_rollback_after_owner_restart_does_not_revive_expired_request(
    tmp_path,
) -> None:
    wall_anchor_ns = 1_800_000_000_000_000_000
    monotonic_anchor_ns = 80_000_000_000
    clock = {
        "wall": wall_anchor_ns,
        "monotonic": monotonic_anchor_ns,
    }
    registry, _contexts = authority_fixtures._capture_registry_contexts(
        tmp_path / "registry.sqlite3"
    )
    database_path = tmp_path / "authority.sqlite3"
    authority = ExecutionGroupAuthority(
        database_path,
        wall_clock_ns=lambda: clock["wall"],
        monotonic_clock_ns=lambda: clock["monotonic"],
    )
    try:
        captured = authority_fixtures._register_and_dispatch(
            registry, session="arb05-clock-restart"
        )
        context = authority_fixtures.ServiceCallContext(
            captured.identity,
            captured.scope,
            captured.registration_id,
            captured.principal_ref,
        )
        execution_scope = authority_fixtures._execution_scope()
        authority.control.activate(
            context,
            execution_scope=execution_scope,
            risk_account_scope_key=authority_fixtures._RISK_SCOPE_KEY,
        )
        intent, binding = authority_fixtures._intent_and_binding()
        expired_deadline_ns = wall_anchor_ns + 4_000
        intent = replace(intent, deadline_ns=expired_deadline_ns)
        binding = replace(
            binding,
            deadline_ns=expired_deadline_ns,
            order_intent_hash=intent.fingerprint,
        )
        lease = authority_fixtures._writer_lease()

        # Record a forward wall-clock jump as a durable high-water mark while
        # rejecting the request before any dispatch hold can be created.
        clock["wall"] = wall_anchor_ns + 5_000
        clock["monotonic"] = monotonic_anchor_ns + 50
        with (
            authority.guard.bind_service_call(context, execution_scope=execution_scope),
            pytest.raises(GroupCoordinatorDenied, match="deadline has expired"),
            authority_fixtures._authorization(
                authority,
                context,
                intent=intent,
                binding=binding,
                lease=lease,
            ),
        ):
            pytest.fail("expired request unexpectedly acquired a hold")
        assert authority.pending_holds() == ()
        authority.close()

        # A separate process models authority restart. Its wall clock now
        # makes the identical request look live, so durable rollback detection
        # must reject it before the account owner can create a hold.
        spawn = multiprocessing.get_context("spawn")
        receive_connection, send_connection = spawn.Pipe(duplex=False)
        child = spawn.Process(
            target=_attempt_after_authority_restart,
            args=(
                str(database_path),
                context,
                execution_scope,
                intent,
                binding,
                lease,
                wall_anchor_ns + 1_000,
                monotonic_anchor_ns + 100,
                send_connection,
            ),
        )
        child.start()
        send_connection.close()
        try:
            result = (
                receive_connection.recv()
                if receive_connection.poll(10)
                else ("missing_child_result",)
            )
        finally:
            receive_connection.close()
        child.join(timeout=10)
        if child.is_alive():
            child.terminate()
            child.join(timeout=5)
        assert child.exitcode == 0
        assert result == ("denied", ())
    finally:
        authority.close()
        registry.close()


def test_existing_authority_database_without_clock_state_fails_closed(tmp_path) -> None:
    registry, _contexts = authority_fixtures._capture_registry_contexts(
        tmp_path / "registry.sqlite3"
    )
    database_path = tmp_path / "authority.sqlite3"
    initial_authority = ExecutionGroupAuthority(database_path)
    try:
        context = authority_fixtures._register_and_dispatch(
            registry, session="arb05-legacy-clock-migration"
        )
        execution_scope = authority_fixtures._execution_scope()
        initial_authority.control.activate(
            context,
            execution_scope=execution_scope,
            risk_account_scope_key=authority_fixtures._RISK_SCOPE_KEY,
        )
        initial_authority.close()

        # Model a pre-clock schema-v1 authority DB with coordinator history but
        # no pending hold and no persisted clock anchor.
        with sqlite3.connect(database_path) as connection:
            connection.execute("DROP TABLE execution_group_authority_deadline_clock")

        migrated_authority = ExecutionGroupAuthority(
            database_path,
            wall_clock_ns=lambda: 1_700_000_000_000_000_000,
            monotonic_clock_ns=lambda: 80_000_000_000,
        )
        try:
            intent, binding = authority_fixtures._intent_and_binding()
            lease = authority_fixtures._writer_lease()
            # The absolute deadline is still ahead of this injected wall time,
            # but legacy clock history is unknown so the owner denies it.
            with (
                migrated_authority.guard.bind_service_call(
                    context, execution_scope=execution_scope
                ),
                pytest.raises(GroupCoordinatorDenied, match="deadline has expired"),
                authority_fixtures._authorization(
                    migrated_authority,
                    context,
                    intent=intent,
                    binding=binding,
                    lease=lease,
                ),
            ):
                pytest.fail("legacy authority database unexpectedly authorized a request")
            assert migrated_authority.pending_holds() == ()
        finally:
            migrated_authority.close()
    finally:
        initial_authority.close()
        registry.close()
