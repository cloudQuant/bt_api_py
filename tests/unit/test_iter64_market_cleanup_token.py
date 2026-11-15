import gc
import threading
import weakref

import pytest

from bt_api_py._risk_market_cleanup_token import (
    CleanupAction,
    MarketCleanupRejected,
    MarketCleanupUnknown,
    RiskMarketCleanupToken,
)


class Resource:
    pass


def test_idle_identity_and_confirmed_actions_are_single_use():
    calls = []
    resource = Resource()
    one = CleanupAction(resource, lambda: calls.append(1) or "one", lambda r: r == "one")
    two = CleanupAction(resource, lambda: calls.append(2) or "two", lambda r: r == "two")
    token = RiskMarketCleanupToken((one, two), lambda: False)
    other = RiskMarketCleanupToken((), lambda: False)
    assert token.same_batch(token) and not token.same_batch(other)
    assert token.status().state == "READY" and not calls
    assert token.run(one) == "one"
    with pytest.raises(MarketCleanupRejected):
        token.run(one)
    assert token.run(two) == "two"
    assert calls == [1, 2]
    assert token.status().completed == 2
    assert token.status().state == "READY"


@pytest.mark.parametrize("guard", [lambda: None, lambda: 0, lambda: True])
def test_guard_must_be_exact_false_and_actions_never_start(guard):
    calls = []
    action = CleanupAction(Resource(), lambda: calls.append(1), lambda _: True)
    token = RiskMarketCleanupToken((action,), guard)
    with pytest.raises(MarketCleanupUnknown):
        token.run(action)
    with pytest.raises(MarketCleanupUnknown):
        token.run(action)
    assert token.status().state == "UNKNOWN" and calls == []


def test_guard_exception_and_callback_exception_are_retained_and_notified_once():
    calls = []
    error = RuntimeError("secret")
    callback_error = RuntimeError("secret")
    def guard():
        raise error
    def callback(token):
        calls.append(token.status().state)  # status works: callback is outside lock
        raise callback_error
    action = CleanupAction(Resource(), lambda: pytest.fail("action ran"), lambda _: True)
    token = RiskMarketCleanupToken((action,), guard, callback)
    with pytest.raises(MarketCleanupUnknown, match="^MARKET_CLEANUP_UNKNOWN$"):
        token.run(action)
    token.mark_unknown(RuntimeError("another secret"))
    assert calls == ["UNKNOWN"]
    assert token._first_error is error and token._callback_error is callback_error
    assert token.status().error_retained
    assert "secret" not in repr(token.status())


@pytest.mark.parametrize("stage", ["action", "validator", "unconfirmed", "after_guard"])
def test_failed_completion_latches_unknown(stage):
    guards = iter((False, None if stage == "after_guard" else False))
    def operation():
        if stage == "action":
            raise RuntimeError("sensitive")
        return object()
    def validate(result):
        if stage == "validator":
            raise RuntimeError("sensitive")
        return stage != "unconfirmed"
    action = CleanupAction(Resource(), operation, validate)
    token = RiskMarketCleanupToken((action,), lambda: next(guards))
    with pytest.raises(MarketCleanupUnknown):
        token.run(action)
    status = token.status()
    assert status.state == "UNKNOWN" and status.completed == 0 and not status.in_flight
    assert status.result_retained is (stage != "action")
    with pytest.raises(MarketCleanupUnknown):
        token.run(action)


def test_reentry_marks_unknown_and_suppresses_next_action():
    events = []
    resource = Resource()
    second = CleanupAction(resource, lambda: events.append("second"), lambda _: True)
    token = None
    def operation():
        with pytest.raises(MarketCleanupUnknown):
            token.run(second)
        return "original"
    first = CleanupAction(resource, operation, lambda _: True)
    token = RiskMarketCleanupToken((first, second), lambda: False, lambda _: events.append("notify"))
    with pytest.raises(MarketCleanupUnknown):
        token.run(first)
    assert token.status().result_retained and token.status().completed == 0
    assert events == ["notify"]


def test_two_clients_share_token_concurrent_attempt_latches_and_retains_result():
    entered, finish = threading.Event(), threading.Event()
    results, notifications = [], []
    class Result:
        pass
    resource, result = Resource(), Result()
    resource_ref, result_ref = weakref.ref(resource), weakref.ref(result)
    def operation():
        entered.set()
        assert finish.wait(3)
        return result_ref()
    first = CleanupAction(resource, operation, lambda _: True)
    second = CleanupAction(Resource(), lambda: results.append("second"), lambda _: True)
    token = RiskMarketCleanupToken((first, second), lambda: False, lambda _: notifications.append(1))
    def client_one():
        try:
            token.run(first)
        except MarketCleanupUnknown:
            results.append("unknown")
    thread = threading.Thread(target=client_one)
    thread.start()
    try:
        assert entered.wait(3)
        with pytest.raises(MarketCleanupUnknown):
            token.run(second)
        assert token.status().in_flight
        finish.set()
        thread.join(3)
        assert not thread.is_alive()
        del resource, result, first, second
        gc.collect()
        assert resource_ref() is not None and result_ref() is not None
        assert results == ["unknown"] and notifications == [1]
        assert token.status().result_retained and token.status().completed == 0
        assert not token.status().in_flight
    finally:
        finish.set()
        thread.join(3)


def test_external_unknown_while_in_flight_preserves_original_error_and_result():
    entered, finish = threading.Event(), threading.Event()
    error = RuntimeError("private")
    result = object()
    def operation():
        entered.set()
        assert finish.wait(3)
        return result
    action = CleanupAction(Resource(), operation, lambda _: True)
    token = RiskMarketCleanupToken((action,), lambda: False)
    observed = []
    def run():
        try:
            token.run(action)
        except MarketCleanupUnknown:
            observed.append(True)
    thread = threading.Thread(target=run)
    thread.start()
    try:
        assert entered.wait(3)
        token.mark_unknown(error)
        finish.set()
        thread.join(3)
        assert not thread.is_alive() and observed == [True]
        assert token._first_error is error and token._results[0] is result
        assert token.status().state == "UNKNOWN" and token.status().completed == 0
    finally:
        finish.set()
        thread.join(3)


def test_registration_bounds_and_duplicate_resource_operation_identity():
    operation = lambda: None
    resource = Resource()
    first = CleanupAction(resource, operation, lambda _: True)
    duplicate = CleanupAction(resource, operation, lambda _: True)
    with pytest.raises(ValueError):
        RiskMarketCleanupToken((first, duplicate), lambda: False)
    with pytest.raises(ValueError):
        RiskMarketCleanupToken((first,) * 257, lambda: False)
    with pytest.raises(ValueError):
        RiskMarketCleanupToken(iter(()), lambda: False)


def test_guard_reentry_marks_unknown_before_operation():
    calls = []
    action = CleanupAction(Resource(), lambda: calls.append("action"), lambda _: True)
    token = None
    def guard():
        with pytest.raises(MarketCleanupUnknown):
            token.run(action)
        return False
    token = RiskMarketCleanupToken((action,), guard, lambda _: calls.append("notify"))
    with pytest.raises(MarketCleanupUnknown):
        token.run(action)
    assert calls == ["notify"] and token.status().state == "UNKNOWN"


def test_guard_exception_after_external_unknown_keeps_both_errors():
    entered, finish = threading.Event(), threading.Event()
    original = RuntimeError("private original")
    guard_error = RuntimeError("private guard")
    def guard():
        entered.set()
        assert finish.wait(3)
        raise guard_error
    action = CleanupAction(Resource(), lambda: pytest.fail("action ran"), lambda _: True)
    token = RiskMarketCleanupToken((action,), guard)
    observed = []
    def run():
        try:
            token.run(action)
        except MarketCleanupUnknown:
            observed.append(True)
    thread = threading.Thread(target=run)
    thread.start()
    try:
        assert entered.wait(3)
        token.mark_unknown(original)
        finish.set()
        thread.join(3)
        assert not thread.is_alive() and observed == [True]
        assert token._first_error is original and token._inflight_error is guard_error
    finally:
        finish.set()
        thread.join(3)


@pytest.mark.parametrize("stage", ["guard", "action", "validator"])
@pytest.mark.parametrize("error_type", [KeyboardInterrupt, SystemExit, GeneratorExit])
def test_non_exception_propagates_original_after_unknown(stage, error_type):
    error = error_type("private")
    calls = []
    def guard():
        if stage == "guard":
            raise error
        return False
    def operation():
        calls.append("action")
        if stage == "action":
            raise error
        return object()
    def validate(result):
        if stage == "validator":
            raise error
        return True
    action = CleanupAction(Resource(), operation, validate)
    token = RiskMarketCleanupToken((action,), guard, lambda _: calls.append("notify"))
    with pytest.raises(error_type) as raised:
        token.run(action)
    assert raised.value is error
    assert token._first_error is error and token._inflight_error is error
    assert token.status().state == "UNKNOWN" and not token.status().in_flight
    assert token.status().completed == 0
    assert calls.count("notify") == 1
    assert ("action" in calls) is (stage != "guard")
    with pytest.raises(MarketCleanupUnknown):
        token.run(action)


@pytest.mark.parametrize("error_type", [KeyboardInterrupt, SystemExit, GeneratorExit])
def test_callback_interruption_is_retained_and_propagates_once(error_type):
    error = error_type("private")
    calls = []
    def callback(token):
        calls.append(token.status().state)
        raise error
    token = RiskMarketCleanupToken((), lambda: False, callback)
    with pytest.raises(error_type) as raised:
        token.mark_unknown()
    assert raised.value is error
    assert token._callback_error is error
    assert token.status().state == "UNKNOWN"
    token.mark_unknown()
    assert calls == ["UNKNOWN"]


@pytest.mark.parametrize("stage", ["guard_before", "action", "validator", "guard_after"])
def test_primary_interruption_precedes_callback_interruption_and_keeps_traceback(stage):
    primary = KeyboardInterrupt("private primary")
    callback_error = SystemExit("private callback")
    original_tracebacks = []
    guard_calls = []
    def interrupt():
        try:
            raise primary
        except BaseException as error:
            original_tracebacks.append(error.__traceback__)
            raise
    def guard():
        guard_calls.append(True)
        if stage == "guard_before" or (stage == "guard_after" and len(guard_calls) == 2):
            interrupt()
        return False
    def operation():
        if stage == "action":
            interrupt()
        return object()
    def validate(result):
        if stage == "validator":
            interrupt()
        return True
    def callback(token):
        assert token.status().state == "UNKNOWN"
        raise callback_error
    action = CleanupAction(Resource(), operation, validate)
    token = RiskMarketCleanupToken((action,), guard, callback)
    with pytest.raises(KeyboardInterrupt) as raised:
        token.run(action)
    assert raised.value is primary
    assert token._first_error is primary and token._inflight_error is primary
    assert token._callback_error is callback_error
    traceback = primary.__traceback__
    while traceback is not None and traceback is not original_tracebacks[0]:
        traceback = traceback.tb_next
    assert traceback is original_tracebacks[0]
    assert token.status().state == "UNKNOWN" and not token.status().in_flight


def test_callback_interruption_precedes_ordinary_primary_error():
    primary = RuntimeError("private primary")
    callback_error = SystemExit("private callback")
    def operation():
        raise primary
    def callback(token):
        raise callback_error
    action = CleanupAction(Resource(), operation, lambda _: True)
    token = RiskMarketCleanupToken((action,), lambda: False, callback)
    with pytest.raises(SystemExit) as raised:
        token.run(action)
    assert raised.value is callback_error
    assert token._first_error is primary and token._inflight_error is primary
    assert token._callback_error is callback_error
    assert token.status().state == "UNKNOWN"


@pytest.mark.parametrize("stage", ["guard", "action", "validator", "callback"])
@pytest.mark.parametrize("disguise", ["raise", "ordinary"])
def test_exception_instance_class_override_cannot_hide_interruption(stage, disguise):
    class DisguisedInterrupt(BaseException):
        def __getattribute__(self, name):
            if name == "__class__":
                if disguise == "raise":
                    raise RuntimeError("private attribute")
                return Exception
            return super().__getattribute__(name)
    error = DisguisedInterrupt("private interruption")
    def guard():
        if stage == "guard":
            raise error
        return False
    def operation():
        if stage == "action":
            raise error
        return object()
    def validate(result):
        if stage == "validator":
            raise error
        return True
    def callback(token):
        if stage == "callback":
            raise error
    action = CleanupAction(Resource(), operation, validate)
    token = RiskMarketCleanupToken((action,), guard, callback)
    try:
        if stage == "callback":
            token.mark_unknown()
        else:
            token.run(action)
    except BaseException as caught:
        assert caught is error
    else:
        pytest.fail("interruption was swallowed")
    if stage == "callback":
        assert token._callback_error is error
    else:
        assert token._first_error is error and token._inflight_error is error
    assert token.status().state == "UNKNOWN"


from bt_api_py._risk_market_cleanup_token import DynamicRiskMarketCleanupToken


def test_dynamic_reservation_conserves_capacity_and_reuses_slot_with_new_generation():
    member, semantic = object(), object()
    received, handles = [], []
    token = DynamicRiskMarketCleanupToken((member,), (semantic,), 1, 4, lambda: False)
    def action(handle, capture):
        handles.append(handle)
        status = token.status()
        assert status.free_resources == 1 and status.reserved_resources == 3
        capture(handle, "partial")
        assert token.status().retained_resources == 1
        return "result"
    def receiver(items):
        received.append(items)
        status = token.status()
        assert status.retained_resources == 2
        assert status.free_resources + status.reserved_resources == 4
        return True
    token.run_dynamic(member, semantic, 2, action, lambda _: True, receiver)
    status = token.status()
    assert status.state == "READY" and status.free_resources == 4
    assert status.reserved_resources == status.retained_resources == status.used_slots == 0
    token.run_dynamic(member, semantic, 2, action, lambda _: True, receiver)
    assert handles[1].slot == handles[0].slot
    assert handles[1].generation == handles[0].generation + 1
    assert received == [("partial", "result"), ("partial", "result")]


def test_dynamic_unknown_bound_capacity_and_identity_refuse_before_side_effects():
    member, semantic = object(), object()
    token = DynamicRiskMarketCleanupToken((member,), (semantic,), 1, 3, lambda: False)
    calls = []
    action = lambda handle, capture: calls.append("effect")
    for bad_member, bad_semantic, limit in [
        (object(), semantic, 1), (member, object(), 1),
        (member, semantic, None), (member, semantic, 3),
        (member, semantic, True),
    ]:
        with pytest.raises(MarketCleanupRejected):
            token.run_dynamic(bad_member, bad_semantic, limit, action, lambda _: True, lambda _: True)
    assert calls == []
    status = token.status()
    assert status.state == "READY" and status.free_resources == 3
    assert status.reserved_resources == 0


def test_dynamic_concurrent_unknown_preserves_partial_and_later_result():
    member, semantic = Resource(), object()
    entered, finish = threading.Event(), threading.Event()
    class Product:
        pass
    partial, result = Product(), Product()
    partial_ref, result_ref = weakref.ref(partial), weakref.ref(result)
    token = DynamicRiskMarketCleanupToken((member,), (semantic,), 1, 3, lambda: False)
    notifications, observed = [], []
    def action(handle, capture):
        entered.set()
        assert finish.wait(3)
        capture(handle, partial_ref())
        return result_ref()
    def run():
        try:
            token.run_dynamic(member, semantic, 2, action, lambda _: True,
                              lambda _: notifications.append("receiver") or True)
        except MarketCleanupUnknown:
            observed.append("unknown")
    thread = threading.Thread(target=run)
    thread.start()
    try:
        assert entered.wait(3)
        token.mark_unknown()
        finish.set()
        thread.join(3)
        assert not thread.is_alive()
        del partial, result
        gc.collect()
        status = token.status()
        assert partial_ref() is not None and result_ref() is not None
        assert status.state == "UNKNOWN" and not status.in_flight
        assert status.retained_resources == 2 and status.reserved_resources == 3
        assert status.free_resources + status.reserved_resources == 3
        assert observed == ["unknown"] and notifications == []
    finally:
        finish.set()
        thread.join(3)


def test_dynamic_receiver_failure_or_unknown_never_discards_products():
    member, semantic = object(), object()
    token = DynamicRiskMarketCleanupToken((member,), (semantic,), 1, 2, lambda: False)
    product = Resource()
    reference = weakref.ref(product)
    with pytest.raises(MarketCleanupUnknown):
        token.run_dynamic(member, semantic, 1, lambda *_: product,
                          lambda _: True, lambda _: None)
    del product
    gc.collect()
    assert reference() is not None
    assert token.status().retained_resources == 1
    assert token.status().reserved_resources == 2
    with pytest.raises(MarketCleanupUnknown):
        token.run_dynamic(member, semantic, 1, lambda *_: None, lambda _: True, lambda _: True)


def test_dynamic_overflow_retains_first_unexpected_resource_and_latches():
    member, semantic = object(), object()
    token = DynamicRiskMarketCleanupToken((member,), (semantic,), 1, 2, lambda: False)
    resource = Resource()
    reference = weakref.ref(resource)
    def action(handle, capture):
        capture(handle, resource)  # bound of one reserves only the eventual return
    with pytest.raises(MarketCleanupUnknown):
        token.run_dynamic(member, semantic, 1, action, lambda _: True, lambda _: True)
    del resource
    gc.collect()
    assert reference() is not None
    assert token.status().state == "UNKNOWN" and token.status().retained_resources == 1


def test_dynamic_generation_never_wraps_and_retired_slot_refuses_before_action():
    from bt_api_py._risk_market_cleanup_token import _MAX_GENERATION
    member, semantic = object(), object()
    token = DynamicRiskMarketCleanupToken((member,), (semantic,), 1, 2, lambda: False)
    token._generations[0] = _MAX_GENERATION - 1
    token.run_dynamic(member, semantic, 1, lambda *_: None, lambda _: True, lambda _: True)
    called = []
    with pytest.raises(MarketCleanupRejected):
        token.run_dynamic(member, semantic, 1, lambda *_: called.append(1), lambda _: True, lambda _: True)
    assert called == [] and token._generations[0] == _MAX_GENERATION


def test_dynamic_primary_interrupt_precedes_callback_interrupt():
    member, semantic = object(), object()
    primary, callback_error = KeyboardInterrupt("private"), SystemExit("private")
    token = DynamicRiskMarketCleanupToken((member,), (semantic,), 1, 2,
        lambda: False, lambda _: (_ for _ in ()).throw(callback_error))
    def action(*_):
        raise primary
    with pytest.raises(KeyboardInterrupt) as raised:
        token.run_dynamic(member, semantic, 1, action, lambda _: True, lambda _: True)
    assert raised.value is primary
    assert token._inflight_error is primary and token._callback_error is callback_error
    assert token.status().state == "UNKNOWN"


def test_dynamic_partial_is_retained_when_producer_raises():
    member, semantic = object(), object()
    token = DynamicRiskMarketCleanupToken((member,), (semantic,), 1, 3, lambda: False)
    partial = Resource()
    ref = weakref.ref(partial)
    def action(handle, capture):
        capture(handle, partial)
        raise RuntimeError("private")
    with pytest.raises(MarketCleanupUnknown):
        token.run_dynamic(member, semantic, 2, action, lambda _: True, lambda _: True)
    del partial
    gc.collect()
    assert ref() is not None and token.status().retained_resources == 1
    assert token.status().state == "UNKNOWN"


def test_dynamic_unknown_during_handoff_retains_resources_despite_ack():
    member, semantic = object(), object()
    entered, finish = threading.Event(), threading.Event()
    token = DynamicRiskMarketCleanupToken((member,), (semantic,), 1, 2, lambda: False)
    product = Resource()
    ref = weakref.ref(product)
    observed = []
    def receiver(_):
        entered.set()
        assert finish.wait(3)
        return True
    def run():
        try:
            token.run_dynamic(member, semantic, 1, lambda *_: product, lambda _: True, receiver)
        except MarketCleanupUnknown:
            observed.append("unknown")
    thread = threading.Thread(target=run)
    thread.start()
    try:
        assert entered.wait(3)
        token.mark_unknown()
        finish.set()
        thread.join(3)
        assert not thread.is_alive() and observed == ["unknown"]
        del product
        gc.collect()
        assert ref() is not None and token.status().retained_resources == 1
        assert token.status().state == "UNKNOWN"
    finally:
        finish.set()
        thread.join(3)


def test_dynamic_disallows_fixed_action_api():
    member, semantic = object(), object()
    token = DynamicRiskMarketCleanupToken((member,), (semantic,), 1, 2, lambda: False)
    with pytest.raises(MarketCleanupRejected):
        token.run(CleanupAction(member, lambda: None, lambda _: True))
    assert token.status().state == "READY"


def test_dynamic_stale_handle_during_reuse_latches_and_retains_resource():
    member, semantic = object(), object()
    token = DynamicRiskMarketCleanupToken((member,), (semantic,), 1, 3, lambda: False)
    handles = []
    token.run_dynamic(member, semantic, 1,
                      lambda handle, _: handles.append(handle), lambda _: True, lambda _: True)
    late = Resource()
    ref = weakref.ref(late)
    def next_action(handle, capture):
        with pytest.raises(MarketCleanupUnknown):
            capture(handles[0], late)
        return "result"
    with pytest.raises(MarketCleanupUnknown):
        token.run_dynamic(member, semantic, 2, next_action, lambda _: True, lambda _: True)
    del late
    gc.collect()
    assert ref() is not None and token.status().retained_resources == 2
    assert token.status().state == "UNKNOWN"


def test_dynamic_stale_handle_after_success_latches_and_retains_resource():
    member, semantic = object(), object()
    token = DynamicRiskMarketCleanupToken((member,), (semantic,), 1, 2, lambda: False)
    handles = []
    token.run_dynamic(member, semantic, 1,
                      lambda handle, _: handles.append(handle), lambda _: True, lambda _: True)
    late = Resource()
    ref = weakref.ref(late)
    with pytest.raises(MarketCleanupUnknown):
        token.capture(handles[0], late)
    del late
    gc.collect()
    status = token.status()
    assert ref() is not None and status.retained_resources == 1
    assert status.reserved_resources == 1 and status.free_resources == 1
    assert status.state == "UNKNOWN"


def test_dynamic_success_drops_token_resource_reference_after_ack():
    member, semantic = object(), object()
    token = DynamicRiskMarketCleanupToken((member,), (semantic,), 1, 2, lambda: False)
    held, refs = [], []
    def action(*_):
        product = Resource()
        refs.append(weakref.ref(product))
        return product
    def receiver(items):
        held.extend(items)
        return True
    token.run_dynamic(member, semantic, 1, action, lambda _: True, receiver)
    gc.collect()
    assert refs[0]() is held[0]
    held.clear()
    gc.collect()
    assert refs[0]() is None and token.status().free_resources == 2


def test_dynamic_receiver_reentrant_late_capture_latches_and_retains_resource():
    member, semantic = object(), object()
    notified, handles = [], []
    token = DynamicRiskMarketCleanupToken((member,), (semantic,), 1, 3,
        lambda: False, lambda t: notified.append(t.status().state))
    late = Resource()
    late_ref = weakref.ref(late)
    def action(handle, capture):
        handles.append(handle)
        capture(handle, "partial")
        return "result"
    def receiver(items):
        assert items == ("partial", "result")
        with pytest.raises(MarketCleanupUnknown):
            token.capture(handles[0], late)
        return True
    with pytest.raises(MarketCleanupUnknown):
        token.run_dynamic(member, semantic, 2, action, lambda _: True, receiver)
    del late
    gc.collect()
    status = token.status()
    assert late_ref() is not None and status.retained_resources == 3
    assert status.state == "UNKNOWN" and status.reserved_resources == 3
    assert notified == ["UNKNOWN"] and not status.in_flight


def test_dynamic_thread_late_capture_during_receiver_is_retained():
    member, semantic = object(), object()
    receiver_entered, receiver_finish = threading.Event(), threading.Event()
    token = DynamicRiskMarketCleanupToken((member,), (semantic,), 1, 2, lambda: False)
    handles, observed, notifications = [], [], []
    late = Resource()
    ref = weakref.ref(late)
    def action(handle, capture):
        handles.append(handle)
        return "result"
    def receiver(items):
        assert items == ("result",)
        receiver_entered.set()
        assert receiver_finish.wait(3)
        return True
    def run():
        try:
            token.run_dynamic(member, semantic, 1, action, lambda _: True, receiver)
        except MarketCleanupUnknown:
            observed.append("unknown")
    thread = threading.Thread(target=run)
    thread.start()
    try:
        assert receiver_entered.wait(3)
        with pytest.raises(MarketCleanupUnknown):
            token.capture(handles[0], late)
        receiver_finish.set()
        thread.join(3)
        assert not thread.is_alive()
        del late
        gc.collect()
        status = token.status()
        assert ref() is not None and status.retained_resources == 2
        assert status.state == "UNKNOWN" and status.reserved_resources == 2
        assert observed == ["unknown"] and status.notification_attempted
    finally:
        receiver_finish.set()
        thread.join(3)


def test_dynamic_late_capture_retains_resource_before_callback_interrupts():
    member, semantic = object(), object()
    callback_error = KeyboardInterrupt("private")
    notifications, handles = [], []
    def notify(token):
        notifications.append(token.status().state)
        raise callback_error
    token = DynamicRiskMarketCleanupToken((member,), (semantic,), 1, 2, lambda: False, notify)
    token.run_dynamic(member, semantic, 1,
                      lambda handle, _: handles.append(handle), lambda _: True, lambda _: True)
    late = Resource()
    ref = weakref.ref(late)
    with pytest.raises(KeyboardInterrupt) as raised:
        token.capture(handles[0], late)
    assert raised.value is callback_error and token._callback_error is callback_error
    del late
    gc.collect()
    assert ref() is not None and token.status().retained_resources == 1
    assert token.status().state == "UNKNOWN" and notifications == ["UNKNOWN"]
