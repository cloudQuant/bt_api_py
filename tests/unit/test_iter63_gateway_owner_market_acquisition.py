"""Nonblocking acquisition retains original native work and source identity."""

import threading
import time
from dataclasses import replace

import pytest
from bt_api_base.market_protocol import MarketSourceDescriptor

from bt_api_py.gateway_owner_market import GatewayOwnerMarketError, GatewayOwnerMarketProvider
from tests.test_iter63_gateway_direct_owner import Harness
from tests.unit.test_iter63_gateway_owner_market import (
    poll_after_ready,
    release_handle,
    settle_action,
    wait_until,
)
from tests.unit.test_iter63_gateway_owner_market import system as system
from tests.unit.test_iter63_gateway_owner_service_calls import owner_for
from tests.unit.test_service_market import _KEY


def wait_ready(bridge, handle):
    wait_until(lambda: bridge.subscription_ready(handle))


def test_begin_retains_running_subscribe_and_releases_exact_late_handle(system):
    bridge, owner, holder = system
    bridge._timeout = 5
    owner.submit_service_call(lambda: bridge._native()).result(timeout_seconds=1)
    native = holder["provider"]
    release = threading.Event()
    native.gate = ("subscribe", release)
    key = replace(_KEY)
    started = time.monotonic()
    handle = bridge.begin_subscribe(key, generation="gen")
    assert time.monotonic() - started < 0.2
    assert native.entered.wait(1)
    assert bridge.subscription_ready(handle) is False
    assert (
        bridge.probe_source(handle, key=key, generation="gen", stream_id=key.stream_id, epoch="one")
        is None
    )
    with pytest.raises(GatewayOwnerMarketError, match="MARKET_KEY_ALREADY_ACTIVE"):
        bridge.begin_subscribe(key, generation="duplicate")
    assert native.calls.count("subscribe") == 1
    assert bridge.unsubscribe(handle) is False
    release.set()
    wait_ready(bridge, handle)
    assert release_handle(bridge, handle)
    assert native.calls.count("unsubscribe") == 1
    assert not native.active
    assert handle not in native.active


def test_probe_retains_original_descriptor_and_requeries_changed_epoch(system):
    bridge, _, holder = system
    bridge._timeout = 5
    handle = bridge.begin_subscribe(_KEY, generation="gen")
    wait_ready(bridge, handle)
    native = holder["provider"]
    release = threading.Event()
    native.gate = ("describe", release)
    seen = []

    def describe(native_handle, *, key, generation, stream_id, epoch):
        native._check("describe")
        seen.append(epoch)
        return MarketSourceDescriptor(
            key,
            stream_id,
            epoch,
            key.instrument.provider,
            "source",
            1,
            "clock",
            "session",
            "monotonic",
        )

    native.describe_source = describe
    args = {"key": _KEY, "generation": "gen", "stream_id": _KEY.stream_id}
    start = time.monotonic()
    assert bridge.probe_source(handle, epoch="old", **args) is None
    assert time.monotonic() - start < 0.2
    assert native.entered.wait(1)
    for _ in range(3):
        assert bridge.probe_source(handle, epoch="old", **args) is None
    assert bridge.probe_source(handle, epoch="new", **args) is None
    release.set()
    wait_until(lambda: bridge._states[handle].pending.done)
    native.gate = None
    result = bridge.probe_source(handle, epoch="new", **args)
    if result is None:
        wait_until(lambda: bridge._states[handle].pending.done)
        result = bridge.probe_source(handle, epoch="new", **args)
    assert result.epoch == "new" and result.key == _KEY
    assert seen == ["old", "new"]
    assert release_handle(bridge, handle)


def test_probe_same_epoch_returns_original_completed_value_once(system):
    bridge, _, holder = system
    handle = bridge.begin_subscribe(_KEY, generation="gen")
    wait_ready(bridge, handle)
    native = holder["provider"]
    release = threading.Event()
    native.gate = ("describe", release)
    args = {"key": _KEY, "generation": "gen", "stream_id": _KEY.stream_id, "epoch": "epoch"}
    assert bridge.probe_source(handle, **args) is None
    assert native.entered.wait(1)
    assert bridge.probe_source(handle, **args) is None
    release.set()
    wait_until(lambda: bridge._states[handle].pending.done)
    descriptor = bridge.probe_source(handle, **args)
    assert descriptor.epoch == "epoch"
    assert native.calls.count("describe") == 1
    assert release_handle(bridge, handle)


def test_completed_probe_preserved_while_other_consumer_progresses(system):
    bridge, _, holder = system
    bridge._timeout = 5
    handle = bridge.begin_subscribe(_KEY, generation="gen")
    wait_ready(bridge, handle)
    release = threading.Event()
    holder["provider"].gate = ("describe", release)
    args = {"key": _KEY, "generation": "gen", "stream_id": _KEY.stream_id, "epoch": "epoch"}
    assert bridge.probe_source(handle, **args) is None
    assert holder["provider"].entered.wait(1)
    release.set()
    wait_until(lambda: bridge._states[handle].pending.done)
    assert settle_action(lambda: bridge.source_epoch_reset_required(handle)) is False
    assert bridge.probe_source(handle, **args).epoch == "epoch"
    assert holder["provider"].calls.count("describe") == 1
    assert release_handle(bridge, handle)


def test_abandoned_probe_does_not_stall_shared_handle(system):
    bridge, _, holder = system
    bridge._timeout = 5
    handle = bridge.begin_subscribe(_KEY, generation="gen")
    wait_ready(bridge, handle)
    native = holder["provider"]
    release = threading.Event()
    native.gate = ("describe", release)
    args = {"key": _KEY, "generation": "gen", "stream_id": _KEY.stream_id, "epoch": "epoch"}
    assert bridge.probe_source(handle, **args) is None
    assert native.entered.wait(1)
    release.set()
    wait_until(lambda: bridge._states[handle].pending.done)
    native.gate = None
    # The requesting consumer has left; the existing consumer must still poll.
    assert poll_after_ready(bridge, handle, limit=1) == ()
    wait_until(lambda: bridge._states[handle].pending.done)
    assert poll_after_ready(bridge, handle, limit=1) == ()
    assert native.calls.count("describe") == 1
    assert native.calls.count("poll") == 1
    assert release_handle(bridge, handle)


def test_probe_failure_preserved_while_other_consumer_progresses(system):
    bridge, _, holder = system
    handle = bridge.begin_subscribe(_KEY, generation="gen")
    wait_ready(bridge, handle)
    native = holder["provider"]
    release = threading.Event()

    def fail(*args, **kwargs):
        assert release.wait(2)
        raise ValueError("synthetic failure")

    native.describe_source = fail
    args = {"key": _KEY, "generation": "gen", "stream_id": _KEY.stream_id, "epoch": "epoch"}
    assert bridge.probe_source(handle, **args) is None
    release.set()
    wait_until(lambda: bridge._states[handle].pending.done)
    assert settle_action(lambda: bridge.source_epoch_reset_required(handle)) is False
    with pytest.raises(GatewayOwnerMarketError, match="SERVICE_CALL_FAILED"):
        bridge.probe_source(handle, **args)
    assert release_handle(bridge, handle)


def test_timed_out_synchronous_descriptor_is_not_reused_by_probe(system):
    bridge, _, holder = system
    handle = bridge.begin_subscribe(_KEY, generation="gen")
    wait_ready(bridge, handle)
    native = holder["provider"]
    release = threading.Event()
    native.gate = ("describe", release)
    args = {"key": _KEY, "generation": "gen", "stream_id": _KEY.stream_id, "epoch": "epoch"}
    assert bridge.describe_source(handle, **args) is None
    release.set()
    wait_until(lambda: bridge._states[handle].pending.done)
    native.gate = None
    native.source = "fresh-source"
    result = bridge.probe_source(handle, **args)
    if result is None:
        wait_until(lambda: bridge._states[handle].pending.done)
        result = bridge.probe_source(handle, **args)
    assert result.source == "fresh-source"
    assert native.calls.count("describe") == 2
    assert release_handle(bridge, handle)


def test_expired_completed_descriptor_is_requeried(system):
    bridge, _, holder = system
    handle = bridge.begin_subscribe(_KEY, generation="gen")
    wait_ready(bridge, handle)
    native = holder["provider"]
    release = threading.Event()
    native.gate = ("describe", release)
    args = {"key": _KEY, "generation": "gen", "stream_id": _KEY.stream_id, "epoch": "epoch"}
    assert bridge.probe_source(handle, **args) is None
    assert native.entered.wait(1)
    release.set()
    wait_until(lambda: bridge._states[handle].pending.done)
    time.sleep(0.05)
    native.gate = None
    native.source = "fresh-source"
    result = bridge.probe_source(handle, **args)
    if result is None:
        wait_until(lambda: bridge._states[handle].pending.done)
        result = bridge.probe_source(handle, **args)
    assert result.source == "fresh-source"
    assert native.calls.count("describe") == 2
    assert release_handle(bridge, handle)


def test_probe_cannot_bypass_latched_result_gap(system):
    bridge, _, holder = system
    handle = bridge.begin_subscribe(_KEY, generation="gen")
    wait_ready(bridge, handle)
    holder["provider"].fail_payload = True
    assert poll_after_ready(bridge, handle, limit=1) == ()
    wait_until(lambda: bridge._states[handle].pending.done)
    with pytest.raises(GatewayOwnerMarketError, match="LOCAL_RESULT_GAP"):
        bridge.source_epoch_reset_required(handle)
    with pytest.raises(GatewayOwnerMarketError, match="LOCAL_RESULT_GAP"):
        bridge.probe_source(
            handle, key=_KEY, generation="gen", stream_id=_KEY.stream_id, epoch="epoch"
        )
    assert release_handle(bridge, handle)


def test_probe_does_not_consume_pending_true_reset(system):
    bridge, _, holder = system
    handle = bridge.begin_subscribe(_KEY, generation="gen")
    wait_ready(bridge, handle)
    native = holder["provider"]
    native.reset = True
    release = threading.Event()
    native.gate = ("reset", release)
    with pytest.raises(GatewayOwnerMarketError, match="MARKET_OPERATION_PENDING"):
        bridge.source_epoch_reset_required(handle)
    assert native.entered.wait(1)
    original = bridge._states[handle].pending
    assert (
        bridge.probe_source(
            handle, key=_KEY, generation="gen", stream_id=_KEY.stream_id, epoch="epoch"
        )
        is None
    )
    assert bridge._states[handle].pending is original
    release.set()
    wait_until(lambda: original.done)
    assert bridge.source_epoch_reset_required(handle) is True
    assert native.calls.count("reset") == 1
    assert native.calls.count("describe") == 0
    assert release_handle(bridge, handle)


def test_probe_does_not_consume_pending_epoch_ack(system):
    bridge, _, holder = system
    handle = bridge.begin_subscribe(_KEY, generation="gen")
    wait_ready(bridge, handle)
    native = holder["provider"]
    native.reset = True
    assert settle_action(lambda: bridge.source_epoch_reset_required(handle)) is True
    release = threading.Event()
    native.gate = ("ack", release)
    with pytest.raises(GatewayOwnerMarketError, match="MARKET_OPERATION_PENDING"):
        bridge.acknowledge_source_epoch_reset(handle, epoch="new")
    assert native.entered.wait(1)
    original = bridge._states[handle].pending
    assert (
        bridge.probe_source(
            handle, key=_KEY, generation="gen", stream_id=_KEY.stream_id, epoch="new"
        )
        is None
    )
    assert bridge._states[handle].pending is original
    release.set()
    wait_until(lambda: original.done)
    assert settle_action(lambda: bridge.source_epoch_reset_required(handle)) is False
    assert native.ack_epochs == ["new"]
    assert native.calls.count("describe") == 0
    assert release_handle(bridge, handle)


def test_late_descriptor_result_does_not_lose_release(system):
    bridge, _, holder = system
    handle = bridge.begin_subscribe(_KEY, generation="gen")
    wait_ready(bridge, handle)
    native = holder["provider"]
    release = threading.Event()
    native.gate = ("describe", release)
    assert (
        bridge.probe_source(
            handle, key=_KEY, generation="gen", stream_id=_KEY.stream_id, epoch="epoch"
        )
        is None
    )
    assert native.entered.wait(1)
    assert bridge.unsubscribe(handle) is False
    release.set()
    wait_until(lambda: bridge._states[handle].pending.done)
    assert release_handle(bridge, handle)
    assert native.calls.count("describe") == 1
    assert native.calls.count("unsubscribe") == 1


def test_begin_detaches_key_and_descriptor_mismatch_is_rejected(system):
    bridge, _, holder = system
    key = replace(_KEY, instrument=replace(_KEY.instrument))
    original_id = key.instrument.instrument_id
    handle = bridge.begin_subscribe(key, generation="gen")
    object.__setattr__(key.instrument, "instrument_id", "mutated")
    wait_ready(bridge, handle)
    state = bridge._states[handle]
    assert state.key.instrument.instrument_id == original_id
    native = holder["provider"]
    release = threading.Event()

    def bad_descriptor(_handle, *, key, generation, stream_id, epoch):
        assert release.wait(2)
        return MarketSourceDescriptor(
            key,
            stream_id,
            "wrong-epoch",
            key.instrument.provider,
            "source",
            1,
            "clock",
            "session",
            "monotonic",
        )

    native.describe_source = bad_descriptor
    args = {
        "key": state.key,
        "generation": "gen",
        "stream_id": state.key.stream_id,
        "epoch": "expected",
    }
    result = bridge.probe_source(handle, **args)
    assert result is None
    release.set()
    wait_until(lambda: bridge._states[handle].pending.done)
    with pytest.raises(GatewayOwnerMarketError, match="INVALID_SOURCE_DESCRIPTOR"):
        bridge.probe_source(handle, **args)
    assert release_handle(bridge, handle)


def test_subscription_error_keeps_handle_until_owner_cleanup(system):
    bridge, owner, holder = system
    owner.submit_service_call(lambda: bridge._native()).result(timeout_seconds=1)
    native = holder["provider"]
    native.fail_subscribe = True
    handle = bridge.begin_subscribe(_KEY, generation="gen")
    wait_until(lambda: bridge._states[handle].pending.done)
    for _ in range(2):
        with pytest.raises(GatewayOwnerMarketError, match="SERVICE_CALL_FAILED"):
            bridge.subscription_ready(handle)
    assert handle in bridge._states
    assert bridge.unsubscribe(handle) is False
    assert not bridge.close()
    assert owner.close(2)
    assert bridge.close()


def test_queued_acquisition_cancel_never_invokes_provider(system):
    bridge, owner, holder = system
    entered, release = threading.Event(), threading.Event()

    def block():
        entered.set()
        assert release.wait(2)

    active = owner.submit_service_call(block)
    assert entered.wait(1)
    try:
        handle = bridge.begin_subscribe(_KEY, generation="gen")
        assert bridge.subscription_ready(handle) is False
        assert bridge.unsubscribe(handle)
        assert "provider" not in holder
    finally:
        release.set()
        active.result(timeout_seconds=1)


def test_expired_queued_acquisition_retains_diagnostic_and_handle(tmp_path):
    harness = Harness(tmp_path)
    owner = owner_for(harness.factory, max_queue_wait_seconds=0.03)
    assert owner.start(2)
    called = []
    bridge = GatewayOwnerMarketProvider(
        owner, lambda: called.append(True), call_timeout_seconds=0.01
    )
    entered, release = threading.Event(), threading.Event()

    def block():
        entered.set()
        assert release.wait(2)

    active = owner.submit_service_call(block)
    assert entered.wait(1)
    try:
        handle = bridge.begin_subscribe(_KEY, generation="gen")
        time.sleep(0.05)
        release.set()
        active.result(timeout_seconds=1)
        wait_until(lambda: bridge._states[handle].pending.done)
        for _ in range(2):
            with pytest.raises(GatewayOwnerMarketError, match="SERVICE_CALL_EXPIRED"):
                bridge.subscription_ready(handle)
        assert handle in bridge._states
        assert bridge.unsubscribe(handle)
        assert not called
    finally:
        release.set()
        assert bridge.close()
        assert owner.close(2)
