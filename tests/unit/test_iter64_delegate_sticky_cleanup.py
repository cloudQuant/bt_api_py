"""Pure-fake coverage for delegate cleanup; no service or account is started."""

from types import SimpleNamespace

import pytest

from bt_api_py.risk_service_gateway import DelegateOrderPort, _DelegateSlot


def confirmed():
    return SimpleNamespace(state="closed", thread_alive=False, error_code=None,
                           unregister_uncertainties=0, owner_cleanup_uncertainties=0,
                           unknown_registration_count=0)


class Handle:
    def __init__(self, result=None, *, error=None, during_close=None):
        self.result, self.error, self.during_close = result, error, during_close
        self.close_calls = 0
        self.client = SimpleNamespace(reserve=lambda *a, **kw: None)
        self.host = object()

    def close(self):
        self.close_calls += 1
        if self.during_close:
            self.during_close()
        if self.error is not None:
            raise self.error
        return self.result


def make_port(callback=None, probe=None):
    return DelegateOrderPort(
        account_scope="a", execution_domain_ref="d", identity=None,
        service_id="g", instance_id="i", scope_ref="s",
        grant_revisions={"one": "v", "two": "v"},
        instrument_for=lambda *_: None, connect=lambda _: pytest.fail("unexpected connect"),
        timeout_seconds=1, retry_seconds=1, on_cleanup_uncertain=callback,
        is_cleanup_uncertain=probe,
    )


@pytest.mark.parametrize("result,error,reason", [
    (None, None, "HANDLE_CLOSE_UNCONFIRMED"),
    (None, RuntimeError("private details"), "HANDLE_CLOSE_EXCEPTION"),
])
def test_first_failure_stops_every_slot_and_is_sticky(result, error, reason):
    notices = []
    port = make_port(lambda resource, why: notices.append((resource, why)))
    first, second = Handle(result, error=error), Handle(confirmed())
    first_slot, second_slot = _DelegateSlot("r", first), _DelegateSlot("r", second)
    port._slots.update(one=first_slot, two=second_slot)

    assert port.close() is False
    assert port.cleanup_uncertain and notices == [(first, reason)]
    assert port._slots == {"one": first_slot, "two": second_slot}
    assert first_slot.handle is first and second_slot.handle is second
    assert (first.close_calls, second.close_calls) == (1, 0)
    before = vars(second_slot).copy()
    port.note_run("two", "new")
    port.invalidate_registrations()
    assert port.current_run("two") is None and not port.ready()
    assert port.status(strategy_ref="two", run_id="r", request_key="k").outcome == "UNKNOWN"
    assert port.close() is False
    assert vars(second_slot) == before and (first.close_calls, second.close_calls) == (1, 0)
    assert notices == [(first, reason)]


def test_reentrant_close_and_callback_cannot_touch_other_slots():
    notices, nested = [], []
    port = make_port(lambda resource, reason: (notices.append((resource, reason)), nested.append(port.close())))
    first = Handle(None, during_close=lambda: nested.append(port.close()))
    second = Handle(confirmed())
    port._slots.update(one=_DelegateSlot("r", first), two=_DelegateSlot("r", second))
    assert not port.close()
    assert nested == [False, False]
    assert notices == [(first, "HANDLE_CLOSE_UNCONFIRMED")]
    assert (first.close_calls, second.close_calls) == (1, 0)


def test_inflight_dispatch_stops_after_another_slot_cleanup_fails():
    port = make_port()
    first, second = Handle(confirmed()), Handle(None)
    port._slots.update(one=_DelegateSlot("r", first), two=_DelegateSlot("r", second))

    def dispatch(*args, **kwargs):
        port.note_run("two", "new")
        return SimpleNamespace(outcome="QUEUED")

    first.client.dispatch = dispatch
    reply = port.dispatch(strategy_ref="one", run_id="r", request_key="k", market_ref=None)
    assert reply.outcome == "UNKNOWN" and port.cleanup_uncertain
    assert second.close_calls == 1 and first.close_calls == 0
    assert not port.close() and second.close_calls == 1


@pytest.mark.parametrize("operation", ["dispatch", "release"])
def test_load_observation_reentry_returns_unknown(operation):
    port = make_port()
    first, second = Handle(confirmed()), Handle(None)
    port._slots.update(one=_DelegateSlot("r", first), two=_DelegateSlot("r", second))

    def load(_key):
        port.note_run("two", "new")
        return SimpleNamespace(state="RELEASED", reservation_id="x", version=1,
                               digest="d", owner_generation="g", reservation_generation="g")

    first.host = SimpleNamespace(load_delegate_observation=load)
    if operation == "dispatch":
        first.client.dispatch = lambda *a, **kw: SimpleNamespace(outcome="QUEUED", code=None)
        reply = port.dispatch(strategy_ref="one", run_id="r", request_key="k", market_ref=None)
    else:
        command = SimpleNamespace(state="RESERVED", reservation_id="x", version=1,
                                  digest="d", owner_generation="g", reservation_generation="g")
        first.client.actor_status = lambda *a, **kw: SimpleNamespace(
            outcome="OBSERVED", observation=SimpleNamespace(command=command))
        first.client.release_actor = lambda *a, **kw: SimpleNamespace(outcome="OBSERVED", state="RELEASED")
        reply = port.release(strategy_ref="one", run_id="r", request_key="k", operation_id="o")
    assert reply.outcome == "UNKNOWN" and port.cleanup_uncertain
    assert (first.close_calls, second.close_calls) == (0, 1)
    assert not port.close() and second.close_calls == 1


def test_result_mapping_reentry_is_rechecked_before_return():
    port = make_port()
    first, second = Handle(confirmed()), Handle(None)
    port._slots.update(one=_DelegateSlot("r", first), two=_DelegateSlot("r", second))

    class Result:
        @property
        def outcome(self):
            port.note_run("two", "new")
            return "REQUEST_DENIED"

    first.client.dispatch = lambda *a, **kw: Result()
    reply = port.dispatch(strategy_ref="one", run_id="r", request_key="k", market_ref=None)
    assert reply.outcome == "UNKNOWN" and second.close_calls == 1
    assert first.close_calls == 0 and not port.close()


def test_host_uncertain_during_connect_is_latched_and_original_error_propagates():
    host_state = [False]
    port = make_port(probe=lambda: host_state[0])
    other = Handle(confirmed())
    port._slots["two"] = _DelegateSlot("r", other)
    original = RuntimeError("synthetic connect failure")

    def connect(_route):
        host_state[0] = True
        raise original

    port.route_for = lambda *_: object()
    port._connect = connect
    with pytest.raises(RuntimeError) as error:
        port.status(strategy_ref="one", run_id="r", request_key="k")
    assert error.value is original and port.cleanup_uncertain
    assert not port.ready() and not port.close() and other.close_calls == 0
    assert port.status(strategy_ref="two", run_id="r", request_key="k").outcome == "UNKNOWN"


def test_probe_requires_strict_boolean_and_does_not_swallow_failure():
    port = make_port(probe=lambda: 1)
    with pytest.raises(RuntimeError, match="RISK_DELEGATE_CLEANUP_STATE_INVALID"):
        port.close()
    assert port.cleanup_uncertain and not port.close()


def test_confirmed_close_resets_attempt_before_failed_reconnect():
    notices = []
    port = make_port(lambda resource, why: notices.append((resource, why)))
    handle = Handle(confirmed())
    slot = _DelegateSlot("r", handle, connection_attempted=True, invalid=True)
    port._slots["one"] = slot
    original_close = port._close_slot

    def close_slot(value):
        result = original_close(value)
        assert result and value.handle is None and not value.connection_attempted
        return result

    port._close_slot = close_slot

    def connect(_route):
        assert slot.connection_attempted is True  # set immediately before the new attempt
        raise RuntimeError("connection failed")

    port.route_for = lambda *_: object()
    port._connect = connect
    assert port.status(strategy_ref="one", run_id="r", request_key="k").outcome == "TRANSPORT_NOT_STARTED"
    assert handle.close_calls == 1 and slot.handle is None and slot.connection_attempted
    assert not port.cleanup_uncertain and not notices
    port._close_slot = original_close
    assert not port.close() and port.cleanup_uncertain
    assert notices == [(slot, "SLOT_REGISTRATION_UNCONFIRMED")]


def test_successful_close_preserves_reentrant_registration_invalidation():
    port = make_port()
    handle = Handle(confirmed(), during_close=port.invalidate_registrations)
    slot = _DelegateSlot("r", handle)
    port._slots["one"] = slot
    assert port.close()
    assert not port.cleanup_uncertain and slot.invalid and "one" not in port._slots
    assert slot.invalid_generation == 1 and handle.close_calls == 1


def test_close_preserves_replacement_slot_and_its_new_handle():
    port = make_port()
    new_handle = Handle(confirmed())
    replacement = _DelegateSlot("new", new_handle, connection_attempted=True)
    old = Handle(confirmed(), during_close=lambda: port._slots.__setitem__("one", replacement))
    port._slots["one"] = _DelegateSlot("r", old, connection_attempted=True)
    assert not port.close()
    assert port._slots["one"] is replacement and replacement.handle is new_handle
    assert replacement.connection_attempted and (old.close_calls, new_handle.close_calls) == (1, 0)


def test_close_preserves_new_handle_added_to_same_slot():
    port = make_port()
    new_handle = Handle(confirmed())
    slot = _DelegateSlot("r")
    old = Handle(confirmed(), during_close=lambda: setattr(slot, "handle", new_handle))
    slot.handle = old
    port._slots["one"] = slot
    assert not port.close()
    assert port._slots["one"] is slot and slot.handle is new_handle and slot.blocked
    assert (old.close_calls, new_handle.close_calls) == (1, 0)


@pytest.mark.parametrize("initial_attempted", [False, True])
def test_close_preserves_new_unconfirmed_attempt_in_same_slot(initial_attempted):
    port = make_port()
    slot = _DelegateSlot("r", connection_attempted=initial_attempted)
    old = Handle(confirmed(), during_close=lambda: setattr(slot, "connection_attempted", not initial_attempted))
    slot.handle = old
    port._slots["one"] = slot
    assert not port.close()
    assert port._slots["one"] is slot and slot.handle is None
    assert slot.connection_attempted and slot.blocked and not port.cleanup_uncertain
    assert old.close_calls == 1


def test_close_does_not_touch_a_replaced_later_slot():
    port = make_port()
    second, replacement_handle = Handle(confirmed()), Handle(confirmed())
    replacement = _DelegateSlot("new", replacement_handle)
    first = Handle(confirmed(), during_close=lambda: port._slots.__setitem__("two", replacement))
    port._slots.update(one=_DelegateSlot("r", first), two=_DelegateSlot("r", second))
    assert not port.close()
    assert port._slots["two"] is replacement and replacement.handle is replacement_handle
    assert (first.close_calls, second.close_calls, replacement_handle.close_calls) == (1, 0, 0)


@pytest.mark.parametrize("operation", ["dispatch", "status", "release", "cancel"])
@pytest.mark.parametrize("change", ["replace", "invalidate"])
def test_reentrant_client_method_lookup_never_calls_stale_client(operation, change):
    port = make_port()
    old = Handle(confirmed())
    port._slots["one"] = _DelegateSlot("r", old)
    calls = []
    target = {"dispatch": "dispatch", "status": "actor_status",
              "release": "release_actor", "cancel": "cancel_actor"}[operation]
    command = SimpleNamespace(state="RESERVED" if operation == "release" else "SENT",
                              reservation_id="x", version=1, digest="d",
                              owner_generation="g", reservation_generation="g")
    actor_result = SimpleNamespace(outcome="OBSERVED", observation=SimpleNamespace(command=command))

    def invoked(*_args, **_kwargs):
        calls.append(target)
        return SimpleNamespace(outcome="REQUEST_DENIED", code=None)

    class Client:
        def reserve(self, *_args, **_kwargs):
            pass

        def __getattr__(self, name):
            if name == target:
                if change == "replace":
                    port.note_run("one", "new")
                else:
                    port.invalidate_registrations()
                return invoked
            if name == "actor_status":
                return lambda *_a, **_kw: actor_result
            raise AttributeError(name)

    old.client = Client()
    arguments = {"strategy_ref": "one", "run_id": "r", "request_key": "k"}
    if operation == "dispatch":
        arguments["market_ref"] = None
    if operation in ("release", "cancel"):
        arguments["operation_id"] = "o"
    reply = getattr(port, operation)(**arguments)
    assert reply.outcome == "UNKNOWN" and not calls
    assert old.close_calls == (1 if change == "replace" else 0)
    if change == "replace":
        assert port._slots["one"].run_id == "new" and port._slots["one"].handle is None
    else:
        assert port._slots["one"].handle is old and port._slots["one"].invalid_generation == 1
    assert not port.cleanup_uncertain


@pytest.mark.parametrize("operation", ["dispatch", "status", "release", "cancel"])
def test_reentrant_change_during_remote_call_makes_reply_unknown(operation):
    port = make_port()
    old = Handle(confirmed())
    port._slots["one"] = _DelegateSlot("r", old)
    calls = []
    command = SimpleNamespace(state="RESERVED" if operation == "release" else "SENT",
                              reservation_id="x", version=1, digest="d",
                              owner_generation="g", reservation_generation="g")
    actor_result = SimpleNamespace(outcome="OBSERVED", observation=SimpleNamespace(command=command))
    target = {"dispatch": "dispatch", "status": "actor_status",
              "release": "release_actor", "cancel": "cancel_actor"}[operation]

    def invoked(*_args, **_kwargs):
        calls.append(target)
        port.note_run("one", "new")
        return SimpleNamespace(outcome="REQUEST_DENIED", code=None)

    if operation in ("release", "cancel"):
        old.client.actor_status = lambda *_a, **_kw: actor_result
    setattr(old.client, target, invoked)
    arguments = {"strategy_ref": "one", "run_id": "r", "request_key": "k"}
    if operation == "dispatch":
        arguments["market_ref"] = None
    if operation in ("release", "cancel"):
        arguments["operation_id"] = "o"
    reply = getattr(port, operation)(**arguments)
    assert reply.outcome == "UNKNOWN" and calls == [target]
    assert old.close_calls == 1 and port._slots["one"].run_id == "new"


def test_cancel_with_unchanged_binding_preserves_success():
    port = make_port()
    handle = Handle(confirmed())
    port._slots["one"] = _DelegateSlot("r", handle)
    command = SimpleNamespace(state="SENT", reservation_id="x", version=1, digest="d",
                              owner_generation="g", reservation_generation="g")
    observation = SimpleNamespace(command=command)
    handle.client.actor_status = lambda *_a, **_kw: SimpleNamespace(
        outcome="OBSERVED", observation=observation)
    calls = []

    def cancel_actor(value, **_kwargs):
        calls.append(value)
        return SimpleNamespace(outcome="QUEUED", code=None)

    handle.client.cancel_actor = cancel_actor
    reply = port.cancel(strategy_ref="one", run_id="r", request_key="k", operation_id="o")
    assert reply.outcome == "QUEUED" and calls == [observation]
    assert handle.close_calls == 0


def test_uncertain_registration_retains_exact_slot_and_stops_other_cleanup():
    notices = []
    port = make_port(lambda resource, reason: notices.append((resource, reason)))
    slot, second = _DelegateSlot("r", blocked=True, connection_attempted=True), Handle(confirmed())
    port._slots.update(one=slot, two=_DelegateSlot("r", second))
    assert not port.close()
    assert port.cleanup_uncertain and notices == [(slot, "SLOT_REGISTRATION_UNCONFIRMED")]
    assert port._slots["one"] is slot and second.close_calls == 0


def test_success_local_slot_and_business_blocked_remain_distinct():
    notices = []
    port = make_port(lambda *args: notices.append(args))
    handle = Handle(confirmed())
    port._slots.update(one=_DelegateSlot("r", handle), two=_DelegateSlot("r"))
    assert port.close() and handle.close_calls == 1
    assert not port.cleanup_uncertain and port.ready() and not notices
    blocked = _DelegateSlot("r", blocked=True, invalid=True)
    port._slots["one"] = blocked
    assert port.close() and not port.cleanup_uncertain and not notices
    assert "one" not in port._slots


def test_base_exception_preserves_state_then_propagates():
    notices = []
    port = make_port(lambda resource, reason: notices.append((resource, reason)))
    first, second = Handle(error=KeyboardInterrupt()), Handle(confirmed())
    port._slots.update(one=_DelegateSlot("r", first), two=_DelegateSlot("r", second))
    with pytest.raises(KeyboardInterrupt):
        port.close()
    assert port.cleanup_uncertain and notices == [(first, "HANDLE_CLOSE_EXCEPTION")]
    assert port._slots["one"].handle is first and second.close_calls == 0
    assert not port.close() and first.close_calls == 1


def test_callback_exception_does_not_clear_uncertainty():
    def callback(*_args):
        raise RuntimeError("private callback details")

    port = make_port(callback)
    handle = Handle(None)
    port._slots["one"] = _DelegateSlot("r", handle)
    assert not port.close() and port.cleanup_uncertain
    assert not port.close() and handle.close_calls == 1


@pytest.mark.parametrize("foreign", ["pid", "thread"])
def test_cleanup_still_requires_owner(monkeypatch, foreign):
    port = make_port()
    if foreign == "pid":
        monkeypatch.setattr("bt_api_py.risk_service_gateway.os.getpid", lambda: port._pid + 1)
    else:
        monkeypatch.setattr("bt_api_py.risk_service_gateway.threading.current_thread", lambda: object())
    with pytest.raises(RuntimeError, match="RISK_DELEGATE_WRONG_OWNER"):
        port.close()
    with pytest.raises(RuntimeError, match="RISK_DELEGATE_WRONG_OWNER"):
        _ = port.cleanup_uncertain
