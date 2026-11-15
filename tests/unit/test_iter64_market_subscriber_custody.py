import gc
import threading
import weakref

import pytest

from bt_api_py._risk_market_cleanup_token import (
    DynamicRiskMarketCleanupToken,
    MarketCleanupRejected,
    MarketCleanupUnknown,
)
from bt_api_py._risk_market_subscriber_custody import subscriber_registry


class Subscriber:
    pass


def setup_custody(operation=None, validate=None, capacity=2):
    member, semantic = object(), object()
    guard = lambda: False
    token = DynamicRiskMarketCleanupToken((member,), (semantic,), 1, 2, guard)
    current = [token]
    current_token = lambda: current[0]
    registry = subscriber_registry(token, guard, current_token, capacity)
    subscriber = Subscriber()
    calls = []
    operation = operation or (lambda: calls.append(1))
    validate = validate or (lambda result: result is None)
    args = (subscriber, object(), object(), operation, object(), member, semantic, validate)
    custody = registry.register(*args)
    return custody, registry, args, calls, token, current, guard, current_token


def test_identity_tombstone_capacity_and_one_close():
    custody, registry, args, calls, token, _, guard, current_token = setup_custody(capacity=1)
    assert subscriber_registry(token, guard, current_token, 1) is registry
    assert registry.register(*args) is custody
    custody.close()
    assert custody.status() == "CLOSED"
    assert registry.register(*args) is custody
    with pytest.raises(MarketCleanupRejected):
        custody.close()
    with pytest.raises(MarketCleanupRejected):
        registry.register(Subscriber(), *args[1:])
    with pytest.raises(MarketCleanupRejected):
        registry.register(args[0], object(), *args[2:])
    assert calls == [1]


def test_changed_token_or_unregistered_member_fails_before_close():
    custody, _, _, calls, _, current, _, _ = setup_custody()
    current[0] = object()
    with pytest.raises(MarketCleanupUnknown):
        custody.close()
    assert custody.status() == "UNKNOWN" and not calls

    custody, registry, args, calls, _, _, _, _ = setup_custody()
    other = list(args)
    other[0] = Subscriber()
    other[5] = object()
    unknown = registry.register(*other)
    with pytest.raises(MarketCleanupRejected):
        unknown.close()
    assert unknown.status() == "UNKNOWN" and not calls


def test_guard_and_validation_failure_retain_unknown():
    custody, _, _, calls, _, _, _, _ = setup_custody(validate=lambda _: False)
    with pytest.raises(MarketCleanupUnknown):
        custody.close()
    assert custody.status() == "UNKNOWN" and calls == [1]
    with pytest.raises(MarketCleanupRejected):
        custody.close()
    assert calls == [1]


def test_interruption_propagates_and_ordinary_exception_becomes_unknown():
    for error in (KeyboardInterrupt(), ValueError("failure")):
        def operation():
            raise error
        custody, _, _, _, _, _, _, _ = setup_custody(operation=operation)
        with pytest.raises(KeyboardInterrupt if isinstance(error, KeyboardInterrupt)
                           else MarketCleanupUnknown) as caught:
            custody.close()
        if isinstance(error, KeyboardInterrupt):
            assert caught.value is error
        assert custody.status() == "UNKNOWN"
        with pytest.raises(MarketCleanupRejected):
            custody.close()


def test_reentrant_callback_latches_unknown():
    holder = []
    def close():
        holder[0].close()
    custody, _, _, _, _, _, _, _ = setup_custody(operation=close)
    holder.append(custody)
    with pytest.raises(MarketCleanupUnknown):
        custody.close()
    assert custody.status() == "UNKNOWN"


def test_concurrent_unknown_retains_subscriber_until_action_finishes():
    started, finish = threading.Event(), threading.Event()
    def operation():
        started.set()
        assert finish.wait(5)
    custody, registry, args, _, token, current, guard, current_token = setup_custody(operation=operation)
    ref = weakref.ref(args[0])
    failures = []
    def run():
        try:
            custody.close()
        except BaseException as error:
            failures.append(error)
    thread = threading.Thread(target=run)
    thread.start()
    try:
        assert started.wait(5)
        with pytest.raises(MarketCleanupUnknown):
            custody.close()
        del args
        gc.collect()
        assert ref() is not None and custody.status() == "UNKNOWN"
    finally:
        finish.set()
        thread.join(5)
    assert not thread.is_alive()
    assert failures and isinstance(failures[0], MarketCleanupUnknown)
    assert ref() is not None and token.status().state == "UNKNOWN"


def test_transfer_receives_same_custody_and_failure_is_terminal():
    custody, _, args, calls, _, _, _, _ = setup_custody()
    offered = []
    with pytest.raises(MarketCleanupUnknown):
        custody.transfer(lambda item: offered.append(item) or False)
    assert offered == [custody] and offered[0] is not args[0]
    with pytest.raises(MarketCleanupRejected):
        custody.close()
    assert custody.status() == "UNKNOWN" and not calls


def test_confirmed_transfer_keeps_same_single_use_state_machine():
    custody, _, _, calls, _, _, _, _ = setup_custody()
    received = []
    custody.transfer(lambda item: received.append(item) or True)
    assert received == [custody] and custody.status() == "OPEN"
    received[0].close()
    with pytest.raises(MarketCleanupRejected):
        custody.close()
    assert calls == [1]


def test_guard_changes_during_close_and_token_identity_is_rechecked():
    for change in ("guard", "token"):
        member, semantic, subscriber = object(), object(), Subscriber()
        active = [False]
        guard = lambda: active[0]
        token = DynamicRiskMarketCleanupToken((member,), (semantic,), 1, 2, guard)
        current = [token]
        registry = subscriber_registry(token, guard, lambda: current[0], 1)
        calls = []
        def operation():
            calls.append(1)
            if change == "guard":
                active[0] = True
            else:
                current[0] = object()
        custody = registry.register(subscriber, object(), object(), operation,
                                    object(), member, semantic, lambda _: True)
        with pytest.raises(MarketCleanupUnknown):
            custody.close()
        assert custody.status() == "UNKNOWN" and calls == [1]
        with pytest.raises(MarketCleanupRejected):
            custody.close()
        assert calls == [1]


def test_transfer_exception_and_reentry_are_terminal():
    for reenter in (False, True):
        custody, _, _, calls, _, _, _, _ = setup_custody()
        def receiver(item):
            if reenter:
                try:
                    item.transfer(lambda _: True)
                except MarketCleanupUnknown:
                    pass
                return True
            raise ValueError("transfer failed")
        with pytest.raises(MarketCleanupUnknown):
            custody.transfer(receiver)
        assert custody.status() == "UNKNOWN" and not calls
        with pytest.raises(MarketCleanupRejected):
            custody.close()


def test_bounded_registry_count_rejects_before_operation(monkeypatch):
    import bt_api_py._risk_market_subscriber_custody as module
    monkeypatch.setattr(module, "_MAX_BATCHES", len(module._batches))
    member, semantic = object(), object()
    guard = lambda: False
    token = DynamicRiskMarketCleanupToken((member,), (semantic,), 1, 2, guard)
    with pytest.raises(MarketCleanupRejected):
        subscriber_registry(token, guard, lambda: token, 1)
    assert token.status().state == "READY"


def test_same_subscriber_across_tokens_and_concurrent_registration():
    subscriber = Subscriber()
    entries = []
    for _ in range(2):
        member, semantic = object(), object()
        guard = lambda: False
        token = DynamicRiskMarketCleanupToken((member,), (semantic,), 1, 2, guard)
        registry = subscriber_registry(token, guard, lambda token=token: token, 1)
        args = (subscriber, object(), object(), lambda: None, object(),
                member, semantic, lambda _: True)
        entries.append((registry, args))
    barrier = threading.Barrier(2, timeout=5)
    results = []
    def register(entry):
        try:
            barrier.wait()
            results.append(entry[0].register(*entry[1]))
        except MarketCleanupRejected as error:
            results.append(error)
    threads = [threading.Thread(target=register, args=(entry,)) for entry in entries]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(5)
    assert all(not thread.is_alive() for thread in threads)
    assert len(results) == 2
    winners = [result for result in results if type(result) is not MarketCleanupRejected]
    assert len(winners) == 1
    winners[0].close()
    loser = next(entry for entry in entries if entry[0] is not winners[0]._registry)
    with pytest.raises(MarketCleanupRejected):
        loser[0].register(*loser[1])


def test_global_subscriber_capacity_rejects_before_action(monkeypatch):
    import bt_api_py._risk_market_subscriber_custody as module
    custody, _, _, calls, _, _, _, _ = setup_custody()
    monkeypatch.setattr(module, "_MAX_SUBSCRIBERS_TOTAL", len(module._all_custodies))
    member, semantic = object(), object()
    guard = lambda: False
    token = DynamicRiskMarketCleanupToken((member,), (semantic,), 1, 2, guard)
    registry = subscriber_registry(token, guard, lambda: token, 1)
    with pytest.raises(MarketCleanupRejected):
        registry.register(Subscriber(), object(), object(), lambda: calls.append(2),
                          object(), member, semantic, lambda _: True)
    assert calls == [] and custody.status() == "OPEN"


def test_unknown_after_final_check_is_detected_before_confirming_closed():
    custody, _, _, calls, token, _, _, _ = setup_custody()
    original_check = custody._check
    ready_checks = []
    def check(state):
        result = original_check(state)
        if state == "READY":
            ready_checks.append(1)
            if len(ready_checks) == 2:
                token.mark_unknown()
        return result
    custody._check = check
    with pytest.raises(MarketCleanupUnknown):
        custody.close()
    assert calls == [1] and custody.status() == "UNKNOWN"
    with pytest.raises(MarketCleanupRejected):
        custody.close()


class MaskedInterrupt(BaseException):
    @property
    def __class__(self):
        raise AssertionError("exception instance class was inspected")


class MaskedError(Exception):
    @property
    def __class__(self):
        raise AssertionError("exception instance class was inspected")


def test_exception_identity_and_notification_interruption_priority():
    for primary, secondary in ((MaskedInterrupt(), KeyboardInterrupt()),
                               (MaskedError(), KeyboardInterrupt()),
                               (MaskedError(), ValueError())):
        member, semantic = object(), object()
        def guard():
            raise primary
        def notify(_):
            raise secondary
        token = DynamicRiskMarketCleanupToken((member,), (semantic,), 1, 2,
                                               lambda: False, notify)
        registry = subscriber_registry(token, guard, lambda: token, 1)
        custody = registry.register(Subscriber(), object(), object(), lambda: None,
                                    object(), member, semantic, lambda _: True)
        caught = None
        try:
            custody.close()
        except BaseException as error:
            caught = error
        if type(primary) is MaskedInterrupt:
            assert caught is primary
            trace = caught.__traceback__
            names = []
            while trace is not None:
                names.append(trace.tb_frame.f_code.co_name)
                trace = trace.tb_next
            assert "guard" in names
        elif type(secondary) is KeyboardInterrupt:
            assert caught is secondary
        else:
            assert type(caught) is MarketCleanupUnknown
        assert custody.status() == "UNKNOWN"


def test_late_token_unknown_is_observed_after_success_and_on_later_entry():
    for observe_status in (True, False):
        custody, _, args, calls, token, _, _, _ = setup_custody()
        ref = weakref.ref(args[0])
        custody.close()
        assert custody.status() == "CLOSED" and calls == [1]
        token.mark_unknown()
        del args
        gc.collect()
        if observe_status:
            assert custody.status() == "UNKNOWN"
        with pytest.raises(MarketCleanupRejected):
            custody.close()
        assert custody.status() == "UNKNOWN" and calls == [1]
        assert ref() is not None


def test_open_custody_projects_shared_token_unknown():
    custody, _, _, calls, token, _, _, _ = setup_custody()
    token.mark_unknown()
    assert custody.status() == "UNKNOWN"
    with pytest.raises(MarketCleanupRejected):
        custody.transfer(lambda _: True)
    assert calls == []
