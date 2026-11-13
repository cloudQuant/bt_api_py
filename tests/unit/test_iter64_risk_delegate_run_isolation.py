"""Local Risk/SDK run fencing with synthetic Gateway and temporary SQLite."""

from dataclasses import replace
from types import SimpleNamespace

import pytest

from tests.unit.test_iter64_risk_service_component import ctx, reserve
from tests.unit.test_iter64_risk_service_component import service as _service_fixture
from tests.unit.test_iter64_risk_service_gateway import Handle, canonical, port


@pytest.fixture
def service(tmp_path):
    yield from _service_fixture.__wrapped__(tmp_path)


def _port_reserve(delegate, run, key="k1"):
    return delegate.reserve(strategy_ref="strategy-a", run_id=run, request_key=key,
                            canonical_payload=canonical(key), parent_ref=None, child_id=None,
                            ttl_seconds=10)


def test_different_run_reserve_preserves_original_handle_and_occupancy(service):
    delegate, handles = port()
    delegate.status(strategy_ref="strategy-a", run_id="old-run", request_key="old")
    old, slot = handles[0], delegate._slots["strategy-a"]
    before = list(old.client.calls)
    service._engine._gateway = delegate
    service._note_run = delegate.note_run
    result = reserve(service, ctx("principal-a", run="new-run"))
    assert result["outcome"] == "UNKNOWN" and result["state"] == "UNKNOWN"
    assert delegate._slots["strategy-a"] is slot and slot.run_id == "old-run"
    assert slot.handle is old and not slot.blocked and not old.closed
    assert len(handles) == 1 and old.client.calls == before
    for _ in range(2):
        service._engine.maintain()
        service._engine.recover()
    status = service.handle("risk.order.status", context=ctx("principal-a", run="new-run"),
                            arguments={"request_key": "k1"})
    assert status["outcome"] == "UNKNOWN" and status["state"] == "UNKNOWN"
    with service._ledger.transaction() as txn:
        row = txn.permit_by_key("strategy-a", "k1")
        assert row is not None and row.state == "UNKNOWN" and row.occupancy > 0
    assert delegate._slots["strategy-a"] is slot and slot.handle is old and not old.closed
    assert old.client.calls == before


def test_policy_denied_new_order_does_not_touch_existing_delegate(service):
    delegate, handles = port()
    delegate.status(strategy_ref="strategy-a", run_id="old-run", request_key="old")
    old, slot = handles[0], delegate._slots["strategy-a"]
    calls = list(old.client.calls)
    service._engine._gateway = delegate
    service._note_run = delegate.note_run
    result = reserve(service, ctx("principal-a", run="new-run"), quantity="3")
    assert result["outcome"] == "DENIED" and result["code"] == "risk_order_quantity_exceeds_cap"
    assert delegate._slots["strategy-a"] is slot and slot.handle is old and not slot.blocked and not old.closed
    assert len(handles) == 1 and old.client.calls == calls


@pytest.mark.parametrize("other_writer", ["foreign", "old_run"])
def test_new_snapshot_race_does_not_retire_old_delegate(service, monkeypatch, other_writer):
    delegate, handles = port()
    delegate.status(strategy_ref="strategy-a", run_id="existing-run", request_key="old")
    old, slot = handles[0], delegate._slots["strategy-a"]
    calls = list(old.client.calls)
    service._engine._gateway = delegate
    service._note_run = delegate.note_run
    original = service._engine.lookup_order

    def race(*args, **kwargs):
        lookup = original(*args, **kwargs)
        assert lookup.kind.value == "NEW"
        caller = args[0]
        writer = (replace(caller, principal_ref="other-principal") if other_writer == "foreign"
                  else replace(caller, run_id="another-run"))
        inserted = service._engine.reserve(writer, kwargs["view"], kwargs["canonical_payload"], writer.run_id)
        assert inserted.outcome == "UNKNOWN"
        return lookup

    monkeypatch.setattr(service._engine, "lookup_order", race)
    result = reserve(service, ctx("principal-a", run="new-run"))
    assert result["outcome"] == "UNKNOWN" and result["permit_id"] is None
    assert delegate._slots["strategy-a"] is slot and slot.handle is old and not slot.blocked and not old.closed
    assert len(handles) == 1 and old.client.calls == calls


def test_new_order_connect_occurs_only_after_committed_reserving_row(service):
    observed, handles = [], []

    def connect(route):
        with service._ledger.transaction() as txn:
            row = txn.permit_by_key("strategy-a", "k1")
            assert row is not None and row.state == "RESERVING" and row.run_id == "run-1"
            observed.append((row.request_key, row.state))
        handle = Handle(route)
        # Match this command's payload digest in the synthetic Gateway reply.
        from tests.unit.test_iter64_risk_service_gateway import obs, result

        def reserve_client(command, request, *, ttl_seconds, timeout_seconds):
            handle.client.calls.append(("reserve", command, request, ttl_seconds))
            return result("OBSERVED", obs("RESERVED", digest=command.payload_digest))

        handle.client.reserve = reserve_client
        handles.append(handle)
        return handle

    delegate, _ = port(connect=connect)
    service._engine._gateway = delegate
    service._note_run = delegate.note_run
    result = reserve(service, ctx("principal-a"))
    assert result["outcome"] == "RESERVED" and observed == [("k1", "RESERVING")]
    assert len(handles) == 1 and len(handles[0].client.calls) == 1


@pytest.mark.parametrize("failure", ["no_snapshot", "unconfirmed", "exception"])
def test_uncertain_cleanup_blocks_same_run_reconnect_without_changing_old_handle(failure):
    delegate, handles = port()
    delegate.status(strategy_ref="strategy-a", run_id="run-1", request_key="old")
    old, slot = handles[0], delegate._slots["strategy-a"]
    if failure == "no_snapshot":
        old.close = lambda: None
    elif failure == "unconfirmed":
        old.close = lambda: SimpleNamespace(state="failed", thread_alive=False,
                                           unregister_uncertainties=1, owner_cleanup_uncertainties=0)
    elif failure == "exception":
        def fail():
            raise RuntimeError("cleanup uncertain")
        old.close = fail
    delegate.invalidate_registrations()
    assert _port_reserve(delegate, "run-1").outcome == "UNKNOWN"
    assert slot.blocked and slot.handle is old and len(handles) == 1
    assert _port_reserve(delegate, "run-1").outcome == "UNKNOWN"
    assert len(handles) == 1


def test_status_unconfirmed_cleanup_cannot_create_handle_used_by_reserve():
    delegate, handles = port()
    delegate.status(strategy_ref="strategy-a", run_id="run-1", request_key="old")
    old, slot = handles[0], delegate._slots["strategy-a"]
    old.close = lambda: None
    delegate.invalidate_registrations()
    status = delegate.status(strategy_ref="strategy-a", run_id="run-1", request_key="old")
    assert status.outcome == "TRANSPORT_NOT_STARTED"
    assert slot.blocked and slot.handle is old and len(handles) == 1
    assert _port_reserve(delegate, "run-1").outcome == "UNKNOWN"
    delegate.note_run("strategy-a", "run-2")
    assert delegate._slots["strategy-a"] is slot and slot.run_id == "run-1"
    assert delegate.close() is False and len(handles) == 1


def test_status_failed_connect_cannot_be_retried_by_reserve():
    attempts = []

    def connect(route):
        attempts.append(route)
        return None

    delegate, _ = port(connect=connect)
    status = delegate.status(strategy_ref="strategy-a", run_id="run-1", request_key="old")
    assert status.outcome == "TRANSPORT_NOT_STARTED"
    assert delegate._slots["strategy-a"].blocked
    assert _port_reserve(delegate, "run-1").outcome == "UNKNOWN"
    assert len(attempts) == 1


def test_status_connect_reentrant_close_retains_handle_and_blocks_reserve():
    holder, handles, close_results = {}, [], []

    def connect(route):
        close_results.append(holder["delegate"].close())
        handle = Handle(route)
        handles.append(handle)
        return handle

    delegate, _ = port(connect=connect)
    holder["delegate"] = delegate
    status = delegate.status(strategy_ref="strategy-a", run_id="run-1", request_key="old")
    slot = delegate._slots["strategy-a"]
    assert status.outcome == "TRANSPORT_NOT_STARTED" and close_results == [False]
    assert slot.blocked and slot.handle is handles[0] and handles[0].client.calls == []
    assert _port_reserve(delegate, "run-1").outcome == "UNKNOWN" and len(handles) == 1


def test_status_close_reentrant_invalidation_cannot_be_cleared_or_reused():
    delegate, handles = port()
    delegate.status(strategy_ref="strategy-a", run_id="run-1", request_key="old")
    old, slot = handles[0], delegate._slots["strategy-a"]
    delegate.invalidate_registrations()
    generation = slot.invalid_generation

    def close():
        delegate.invalidate_registrations()
        return SimpleNamespace(state="closed", thread_alive=False, error_code=None,
                               unregister_uncertainties=0, owner_cleanup_uncertainties=0,
                               unknown_registration_count=0)

    old.close = close
    status = delegate.status(strategy_ref="strategy-a", run_id="run-1", request_key="old")
    assert status.outcome == "TRANSPORT_NOT_STARTED" and slot.blocked
    assert slot.invalid and slot.invalid_generation == generation + 1
    assert _port_reserve(delegate, "run-1").outcome == "UNKNOWN" and len(handles) == 1


@pytest.mark.parametrize("strategy,run", [("strategy-a", None), ("unknown", "run-1"), ("", "run-1")])
def test_unavailable_route_returns_unknown_without_connect(strategy, run):
    delegate, handles = port()
    reply = delegate.reserve(strategy_ref=strategy, run_id=run, request_key="k1",
                             canonical_payload=canonical(), parent_ref=None, child_id=None,
                             ttl_seconds=10)
    assert reply.outcome == "UNKNOWN" and handles == []


def test_connect_exception_fences_retries_without_cleanup_proof():
    attempts = []

    def connect(route):
        attempts.append(route)
        raise RuntimeError("indeterminate registration")

    delegate, _ = port(connect=connect)
    assert _port_reserve(delegate, "run-1").outcome == "UNKNOWN"
    assert _port_reserve(delegate, "run-1").outcome == "UNKNOWN"
    assert len(attempts) == 1 and delegate._slots["strategy-a"].blocked
    slot = delegate._slots["strategy-a"]
    assert slot.connection_attempted and slot.handle is None
    delegate.note_run("strategy-a", "run-2")
    assert delegate._slots["strategy-a"] is slot and slot.run_id == "run-1"
    assert delegate.status(strategy_ref="strategy-a", run_id="run-2",
                           request_key="k1").outcome == "TRANSPORT_NOT_STARTED"
    assert delegate.close() is False and len(attempts) == 1


def test_never_connected_empty_slot_can_switch_runs_without_cleanup():
    delegate, handles = port()
    delegate.note_run("strategy-a", "run-1")
    old = delegate._slots["strategy-a"]
    assert old.handle is None and not old.connection_attempted and not old.blocked
    delegate.note_run("strategy-a", "run-2")
    current = delegate._slots["strategy-a"]
    assert current is not old and current.run_id == "run-2" and not current.connection_attempted
    assert handles == []
    assert _port_reserve(delegate, "run-2").outcome == "OBSERVED"
    assert current.connection_attempted and len(handles) == 1


def test_invalidated_never_connected_slot_remains_blocked_on_run_change():
    delegate, handles = port()
    delegate.note_run("strategy-a", "run-1")
    old = delegate._slots["strategy-a"]
    delegate.invalidate_registrations()
    delegate.note_run("strategy-a", "run-2")
    assert delegate._slots["strategy-a"] is old and old.invalid and old.blocked
    assert not old.connection_attempted and old.handle is None and handles == []


def test_interrupted_connect_preserves_unknown_attempt_and_reraises():
    attempts = []

    def connect(route):
        attempts.append(route)
        raise KeyboardInterrupt

    delegate, _ = port(connect=connect)
    with pytest.raises(KeyboardInterrupt):
        _port_reserve(delegate, "run-1")
    slot = delegate._slots["strategy-a"]
    assert slot.connection_attempted and slot.blocked and slot.handle is None
    delegate.note_run("strategy-a", "run-2")
    assert delegate._slots["strategy-a"] is slot and len(attempts) == 1


def test_interrupted_cleanup_preserves_handle_and_reraises():
    delegate, handles = port()
    delegate.status(strategy_ref="strategy-a", run_id="run-1", request_key="old")
    old, slot = handles[0], delegate._slots["strategy-a"]

    def interrupt():
        raise KeyboardInterrupt

    old.close = interrupt
    delegate.invalidate_registrations()
    with pytest.raises(KeyboardInterrupt):
        _port_reserve(delegate, "run-1")
    assert slot.blocked and slot.handle is old and slot.connection_attempted
    assert _port_reserve(delegate, "run-1").outcome == "UNKNOWN" and len(handles) == 1


def test_failed_clock_after_connect_exception_cannot_reopen_slot():
    attempts = []

    def connect(route):
        attempts.append(route)
        raise RuntimeError("connect failed")

    def clock():
        raise RuntimeError("clock failed")

    delegate, _ = port(connect=connect, clock=clock)
    assert _port_reserve(delegate, "run-1").outcome == "UNKNOWN"
    assert _port_reserve(delegate, "run-1").outcome == "UNKNOWN"
    assert delegate._slots["strategy-a"].blocked and len(attempts) == 1


@pytest.mark.parametrize("bad_handle", [None, object(), SimpleNamespace(client=object(), host=object())])
def test_invalid_connect_result_blocks_all_reconnects(bad_handle):
    attempts = []

    def connect(route):
        attempts.append(route)
        return bad_handle

    delegate, _ = port(connect=connect)
    assert _port_reserve(delegate, "run-1").outcome == "UNKNOWN"
    slot = delegate._slots["strategy-a"]
    assert slot.blocked
    delegate.note_run("strategy-a", "run-2")
    assert delegate._slots["strategy-a"] is slot and slot.run_id == "run-1"
    assert _port_reserve(delegate, "run-1").outcome == "UNKNOWN"
    assert delegate.close() is False and len(attempts) == 1


def test_unreadable_runtime_status_blocks_existing_handle_without_reconnect():
    handles = []

    class UnreadableRuntime(Handle):
        @property
        def runtime(self):
            raise RuntimeError("runtime unavailable")

    def connect(route):
        handle = UnreadableRuntime(route)
        handles.append(handle)
        return handle

    delegate, _ = port(connect=connect)
    assert _port_reserve(delegate, "run-1").outcome == "OBSERVED"
    assert _port_reserve(delegate, "run-1", "next").outcome == "UNKNOWN"
    slot = delegate._slots["strategy-a"]
    assert slot.blocked and slot.handle is handles[0] and len(handles) == 1
    delegate.note_run("strategy-a", "run-2")
    assert delegate._slots["strategy-a"] is slot and slot.run_id == "run-1"


def test_reentrant_close_during_connect_retains_handle_and_prevents_send():
    holder, handles, close_results = {}, [], []

    def connect(route):
        close_results.append(holder["delegate"].close())
        handle = Handle(route)
        handle.close = lambda: None
        handles.append(handle)
        return handle

    delegate, _ = port(connect=connect)
    holder["delegate"] = delegate
    assert _port_reserve(delegate, "run-1").outcome == "UNKNOWN"
    slot = delegate._slots["strategy-a"]
    assert close_results == [False] and slot.blocked and slot.handle is handles[0]
    assert handles[0].client.calls == []
    assert _port_reserve(delegate, "run-1").outcome == "UNKNOWN"
    assert delegate.close() is False and len(handles) == 1


def test_reentrant_invalidation_during_close_cannot_be_cleared():
    delegate, handles = port()
    delegate.status(strategy_ref="strategy-a", run_id="run-1", request_key="old")
    old, slot = handles[0], delegate._slots["strategy-a"]
    delegate.invalidate_registrations()
    original_generation = slot.invalid_generation

    def close():
        delegate.invalidate_registrations()
        return SimpleNamespace(state="closed", thread_alive=False, error_code=None,
                               unregister_uncertainties=0, owner_cleanup_uncertainties=0,
                               unknown_registration_count=0)

    old.close = close
    calls = list(old.client.calls)
    assert _port_reserve(delegate, "run-1").outcome == "UNKNOWN"
    assert slot.invalid and slot.invalid_generation == original_generation + 1 and slot.blocked
    assert old.client.calls == calls and len(handles) == 1
    assert _port_reserve(delegate, "run-1").outcome == "UNKNOWN"
    assert len(handles) == 1


def test_reentrant_connect_cannot_switch_run_or_reserve_twice():
    holder, nested, handles = {}, [], []

    def connect(route):
        delegate = holder["delegate"]
        delegate.note_run("strategy-a", "run-2")
        nested.append(_port_reserve(delegate, "run-1", "nested").outcome)
        handle = Handle(route)
        handles.append(handle)
        return handle

    delegate, _ = port(connect=connect)
    holder["delegate"] = delegate
    reply = _port_reserve(delegate, "run-1")
    assert nested == ["UNKNOWN"] and reply.outcome == "OBSERVED"
    assert delegate.current_run("strategy-a") == "run-1"
    assert len(handles) == 1 and len(handles[0].client.calls) == 1


def test_invalidation_during_instrument_mapping_prevents_reserve():
    delegate, handles = port()
    original = delegate._instrument_for

    def invalidate(venue, native):
        delegate.invalidate_registrations()
        return original(venue, native)

    delegate._instrument_for = invalidate
    assert _port_reserve(delegate, "run-1").outcome == "UNKNOWN"
    assert len(handles) == 1 and handles[0].client.calls == []
    assert delegate._slots["strategy-a"].blocked
    assert delegate.status(strategy_ref="strategy-a", run_id="run-1",
                           request_key="k1").outcome == "TRANSPORT_NOT_STARTED"
    assert len(handles) == 1


@pytest.mark.parametrize("action", ["invalidate", "close"])
def test_invalidation_or_close_during_gateway_call_keeps_unknown_occupancy(service, action):
    from tests.unit.test_iter64_risk_service_gateway import obs
    from tests.unit.test_iter64_risk_service_gateway import result as make_result

    holder, handles, close_results = {}, [], []

    def connect(route):
        handle = Handle(route)

        def reserve_client(command, request, *, ttl_seconds, timeout_seconds):
            handle.client.calls.append(("reserve", command, request, ttl_seconds))
            if action == "invalidate":
                holder["delegate"].invalidate_registrations()
            else:
                close_results.append(holder["delegate"].close())
            return make_result("OBSERVED", obs("RESERVED", digest=command.payload_digest))

        handle.client.reserve = reserve_client
        handles.append(handle)
        return handle

    delegate, _ = port(connect=connect)
    holder["delegate"] = delegate
    service._engine._gateway = delegate
    result = reserve(service, ctx("principal-a"))
    assert result["outcome"] == "UNKNOWN" and result["state"] == "UNKNOWN"
    assert len(handles) == 1 and len(handles[0].client.calls) == 1
    assert delegate._slots["strategy-a"].blocked
    if action == "close":
        assert close_results == [False]
    for _ in range(2):
        service._engine.maintain()
        service._engine.recover()
    with service._ledger.transaction() as txn:
        row = txn.permit_by_key("strategy-a", "k1")
        assert row is not None and row.state == "UNKNOWN" and row.occupancy > 0
    assert len(handles) == 1 and len(handles[0].client.calls) == 1


def test_invalidation_during_reply_mapping_keeps_unknown_occupancy(service):
    from tests.unit.test_iter64_risk_service_gateway import obs

    holder, handles = {}, []

    def connect(route):
        handle = Handle(route)

        def reserve_client(command, request, *, ttl_seconds, timeout_seconds):
            handle.client.calls.append(("reserve", command, request, ttl_seconds))

            class InvalidatingResult:
                code = None
                observation = obs("RESERVED", digest=command.payload_digest)

                @property
                def outcome(self):
                    holder["delegate"].invalidate_registrations()
                    return "OBSERVED"

            return InvalidatingResult()

        handle.client.reserve = reserve_client
        handles.append(handle)
        return handle

    delegate, _ = port(connect=connect)
    holder["delegate"] = delegate
    service._engine._gateway = delegate
    response = reserve(service, ctx("principal-a"))
    assert response["outcome"] == "UNKNOWN" and response["state"] == "UNKNOWN"
    assert len(handles) == 1 and len(handles[0].client.calls) == 1
    assert delegate._slots["strategy-a"].blocked
    service._engine.maintain()
    service._engine.recover()
    with service._ledger.transaction() as txn:
        row = txn.permit_by_key("strategy-a", "k1")
        assert row is not None and row.state == "UNKNOWN" and row.occupancy > 0
    assert len(handles) == 1 and len(handles[0].client.calls) == 1
