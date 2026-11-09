"""Gateway native dispatch stays closed until its shared claim is complete.

These tests use the real journal, dispatcher, and SDK composition with only a
synthetic provider.  A reservation or a queued acknowledgement is not a
provider send or venue acceptance.
"""

import pytest

from bt_api_gateway.direct_command_journal import GatewayDirectCommandJournal
from bt_api_gateway.direct_dispatch import DirectDispatchError, GatewayDirectDispatcher
from bt_api_gateway.session_guard import LocalAccountSessionGuard

from bt_api_py.gateway_direct_dispatch import compose_gateway_direct_dispatcher
from tests.unit.test_iter63_gateway_prepared_place import (
    PreparedProvider,
    ROUTE,
    make_request,
)
from bt_api_py.direct_write import DirectWriteCoordinator
from bt_api_py.write_admission import WriteAdmission


@pytest.mark.parametrize("prepared_place", [False, True])
def test_real_gateway_composition_never_dispatches_without_claim(tmp_path, prepared_place):
    guard_dir = tmp_path / "guard"
    guard_dir.mkdir()
    guard = LocalAccountSessionGuard(guard_dir, "account-a", "owner")
    journal = GatewayDirectCommandJournal.create(
        tmp_path / "journal.sqlite", "account-a", generation=guard.acquire()
    )
    provider = PreparedProvider()
    coordinator = DirectWriteCoordinator(WriteAdmission({ROUTE: True}), lambda _: provider)
    try:
        request = make_request()
        row = journal.reserve("strategy-a", "key-a", "principal-a", request.to_dict())
        dispatcher = compose_gateway_direct_dispatcher(
            coordinator,
            ROUTE,
            journal,
            guard,
            lambda _: True,
            expected_local_provider=provider,
            prepared_place=prepared_place,
        )
        with pytest.raises(DirectDispatchError, match="DISPATCH_CLAIM_UNAVAILABLE"):
            dispatcher.dispatch(
                row.strategy_scope, row.request_key, row.principal,
                row.reservation_id, row.digest, row.version,
            )
        assert journal.get("strategy-a", "key-a", "principal-a") == row
        assert provider.prepared == []
        assert provider.sends == []

        # Independently protect the SDK composition: its former checked port
        # silently omitted the Gateway native fence.  Even if an internal
        # caller bypasses dispatcher.dispatch, it must not reach the provider.
        with pytest.raises(ValueError, match="gateway_dispatch_claim_unavailable"):
            dispatcher._native_place(request.to_dict(), object(), "account-a")
        assert provider.prepared == []
        assert provider.sends == []
    finally:
        guard.release()


def test_direct_gateway_dispatcher_does_not_invoke_injected_native(tmp_path):
    guard_dir = tmp_path / "guard"
    guard_dir.mkdir()
    guard = LocalAccountSessionGuard(guard_dir, "account-a", "owner")
    journal = GatewayDirectCommandJournal.create(
        tmp_path / "journal.sqlite", "account-a", generation=guard.acquire()
    )
    called = []
    try:
        request = make_request()
        row = journal.reserve("strategy-a", "key-a", "principal-a", request.to_dict())
        dispatcher = GatewayDirectDispatcher(
            journal, ROUTE, guard, lambda *args: called.append(args),
            lambda _identity: ("synthetic-key",), lambda _record: True,
        )
        with pytest.raises(DirectDispatchError, match="DISPATCH_CLAIM_UNAVAILABLE"):
            dispatcher.dispatch(
                row.strategy_scope, row.request_key, row.principal,
                row.reservation_id, row.digest, row.version,
            )
        assert called == []
        assert journal.get("strategy-a", "key-a", "principal-a") == row
    finally:
        guard.release()


def test_expired_reservation_stays_observable_without_native_dispatch(tmp_path):
    guard_dir = tmp_path / "guard"
    guard_dir.mkdir()
    guard = LocalAccountSessionGuard(guard_dir, "account-a", "owner")
    now = [100.0]
    journal = GatewayDirectCommandJournal.create(
        tmp_path / "journal.sqlite", "account-a", generation=guard.acquire(),
        clock=lambda: now[0],
    )
    called = []
    try:
        row = journal.reserve("strategy-a", "key-a", "principal-a", make_request().to_dict(),
                              ttl_seconds=1.0)
        dispatcher = GatewayDirectDispatcher(
            journal, ROUTE, guard, lambda *args: called.append(args),
            lambda _identity: ("synthetic-key",), lambda _record: True,
        )
        now[0] += 2.0
        expired = dispatcher.dispatch(
            row.strategy_scope, row.request_key, row.principal,
            row.reservation_id, row.digest, row.version,
        )
        assert expired.status == "EXPIRED"
        assert called == []
        assert journal.get("strategy-a", "key-a", "principal-a") == expired
    finally:
        guard.release()
