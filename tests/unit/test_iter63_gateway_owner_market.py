"""Synthetic native-owner bridge; no real account or transport."""

import os
import threading
import time
from dataclasses import replace

import pytest
from bt_api_base.market_protocol import MarketProviderEvent, MarketSourceDescriptor
from bt_api_base.service_protocol import WireIdentity, WireRole, WireScope
from bt_api_gateway.market_hub import GatewayMarketHub, GatewayMarketHubError

from bt_api_py.gateway_direct_owner import GatewayOwnerServiceCallError
from bt_api_py.gateway_owner_market import GatewayOwnerMarketError, GatewayOwnerMarketProvider
from tests.test_iter63_gateway_direct_owner import Harness
from tests.unit.test_iter63_gateway_owner_service_calls import owner_for
from tests.unit.test_service_market import _KEY, _OTHER_KEY


class Native:
    def __init__(self):
        self.thread = threading.get_ident()
        self.calls = []
        self.gate = None
        self.entered = threading.Event()
        self.reset = False
        self.events = []
        self.fail_close = False
        self.fail_payload = False
        self.fail_after_drain = False
        self.drained = 0
        self.fail_subscribe = False
        self.source = "synthetic"
        self.source_usable = True
        self.active = set()
        self.ack_epochs = []

    def _check(self, op):
        assert self.thread == threading.get_ident()
        self.calls.append(op)
        if self.gate and self.gate[0] == op:
            self.entered.set()
            assert self.gate[1].wait(3)

    def subscribe(self, key, *, generation):
        self._check("subscribe")
        handle = object()
        self.active.add(handle)
        self.generation = generation
        if self.fail_subscribe:
            raise RuntimeError("unknown native outcome")
        return handle

    def unsubscribe(self, handle):
        self._check("unsubscribe")
        if self.fail_close:
            return False
        self.active.discard(handle)
        return True

    def poll(self, handle, *, limit):
        self._check("poll")
        if not self.source_usable:
            return ()
        if self.fail_payload:
            return (
                MarketProviderEvent(self.generation, "2026-01-01T00:00:00Z", {"bad": object()}),
            )
        batch = tuple(self.events[:limit])
        del self.events[:limit]
        self.drained += len(batch)
        if self.fail_after_drain:
            raise RuntimeError("projection failed after dequeue")
        return batch

    def describe_source(self, handle, *, key, generation, stream_id, epoch):
        self._check("describe")
        return MarketSourceDescriptor(
            key,
            stream_id,
            epoch,
            key.instrument.provider,
            self.source,
            1,
            "clock",
            "session",
            "monotonic",
        )

    def source_epoch_reset_required(self, handle):
        self._check("reset")
        return self.reset

    def retained_events_ready(self, handle):
        self._check("ready")
        return self.source_usable

    def acknowledge_source_epoch_reset(self, handle, *, epoch):
        self._check("ack")
        self.ack_epochs.append(epoch)
        self.reset = False
        return True


@pytest.fixture
def system(tmp_path):
    harness = Harness(tmp_path)
    owner = owner_for(harness.factory)
    assert owner.start(2)
    holder = {}

    def getter():
        if "provider" not in holder:
            holder["provider"] = Native()
        return holder["provider"]

    bridge = GatewayOwnerMarketProvider(owner, getter, call_timeout_seconds=0.03)
    try:
        yield bridge, owner, holder
    finally:
        if "provider" in holder and holder["provider"].gate:
            holder["provider"].gate[1].set()
        bridge.close()
        assert owner.close(2)
        assert bridge.close()


def wait_until(predicate):
    deadline = time.monotonic() + 2
    while not predicate():
        if time.monotonic() >= deadline:
            raise AssertionError("condition not reached")
        time.sleep(0.002)


def settle_action(action):
    deadline = time.monotonic() + 2
    while True:
        try:
            return action()
        except GatewayOwnerMarketError as error:
            if error.code != "MARKET_OPERATION_PENDING" or time.monotonic() >= deadline:
                raise
            time.sleep(0.002)


def release_handle(bridge, handle):
    wait_until(lambda: bridge.unsubscribe(handle))
    return True


def poll_after_ready(bridge, handle, *, limit):
    if settle_action(lambda: bridge.source_epoch_reset_required(handle)):
        raise AssertionError("unexpected source reset")
    return bridge.poll(handle, limit=limit)


def make_hub(bridge, keys, published):
    return GatewayMarketHub(
        bridge,
        key_universe=keys,
        principal_allowed_keys={"principal": keys},
        service_identity=WireIdentity(WireRole.SERVICE, "service", "gateway"),
        service_scope=WireScope("paper:market-unit"),
        publisher=published.append,
    )


def close_hub(hub):
    def attempt():
        try:
            hub.close()
            return True
        except GatewayMarketHubError as error:
            if error.code != "PROVIDER_UNSUBSCRIBE_FAILED":
                raise
            return False

    wait_until(attempt)


def test_hub_maintain_eventually_delivers_async_batch(system):
    bridge, _, holder = system
    published = []
    hub = make_hub(bridge, [_KEY], published)
    hub.subscribe("principal", _KEY, "registration")
    native = holder["provider"]
    native.events.append(
        MarketProviderEvent(native.generation, "2026-01-01T00:00:00Z", {"value": 42})
    )
    try:
        deadline = time.monotonic() + 2
        while not published and time.monotonic() < deadline:
            hub.maintain(["registration"])
            time.sleep(0.002)
        assert len(published) == 1
        assert published[0].payload["data"]["value"] == 42
    finally:
        close_hub(hub)


def test_hub_late_observation_preserves_original_source_epoch_and_time(system):
    bridge, _, holder = system
    published = []
    hub = make_hub(bridge, [_KEY], published)
    original_epoch = hub.subscribe("principal", _KEY, "registration")["epoch"]
    native = holder["provider"]
    native.events.append(
        MarketProviderEvent(
            native.generation, "2026-01-01T00:00:00Z", {"value": 42, "source": "old-native"}
        )
    )
    state = next(iter(bridge._states.values()))
    try:
        deadline = time.monotonic() + 2
        while not (state.pending_kind == "poll" and state.pending.done):
            assert time.monotonic() < deadline
            hub.maintain(["registration"])
            time.sleep(0.002)
        hub.maintain(["registration"])
        wait_until(lambda: state.pending_kind == "reset" and state.pending.done)
        native.source_usable = False
        hub.maintain(["registration"])
        assert len(published) == 1
        assert published[0].header.stream_epoch == original_epoch
        assert published[0].header.sent_at_utc == "2026-01-01T00:00:00Z"
        assert published[0].payload["data"]["source"] == "old-native"
    finally:
        close_hub(hub)


def test_hub_rejects_cached_batch_if_owner_observes_disconnect(system):
    bridge, _, holder = system
    published = []
    hub = make_hub(bridge, [_KEY], published)
    hub.subscribe("principal", _KEY, "registration")
    native = holder["provider"]
    native.events.append(
        MarketProviderEvent(native.generation, "2026-01-01T00:00:00Z", {"value": 42})
    )
    state = next(iter(bridge._states.values()))
    release = threading.Event()
    try:
        deadline = time.monotonic() + 2
        while not (state.pending_kind == "poll" and state.pending.done):
            assert time.monotonic() < deadline
            hub.maintain(["registration"])
            time.sleep(0.002)
        native.gate = ("reset", release)
        hub.maintain(["registration"])
        assert native.entered.wait(1)
        native.source_usable = False
        release.set()
        wait_until(lambda: state.pending_kind == "reset" and state.pending.done)
        hub.maintain(["registration"])
        assert not published
    finally:
        release.set()
        close_hub(hub)


def test_many_active_and_expired_hub_streams_do_not_wait_for_blocked_owner(system):
    bridge, owner, _ = system
    keys = [
        replace(
            _KEY,
            instrument=replace(
                _KEY.instrument, instrument_id=f"asset-{index}", native_symbol=f"SYMBOL-{index}"
            ),
        )
        for index in range(8)
    ]
    hub = make_hub(bridge, keys, [])
    for index, key in enumerate(keys):
        hub.subscribe("principal", key, f"registration-{index}")
    entered, release = threading.Event(), threading.Event()

    def block():
        entered.set()
        assert release.wait(2)

    task = owner.submit_service_call(block)
    assert entered.wait(1)
    try:
        start = time.monotonic()
        hub.maintain([f"registration-{index}" for index in range(8)])
        assert time.monotonic() - start < 0.12
        start = time.monotonic()
        result = hub.maintain([])
        assert time.monotonic() - start < 0.12
        assert result["pending_releases"] == 8
    finally:
        release.set()
        task.result(timeout_seconds=1)
        close_hub(hub)


def test_acquisition_timeout_and_late_release(system):
    bridge, owner, holder = system
    # Create provider on owner, then block actual subscribe.
    owner.submit_service_call(lambda: bridge._native()).result(timeout_seconds=1)
    native = holder["provider"]
    release = threading.Event()
    native.gate = ("subscribe", release)
    handle = bridge.subscribe(_KEY, generation="gen")
    assert native.entered.is_set()
    assert (
        bridge.describe_source(
            handle, key=_KEY, generation="gen", stream_id=_KEY.stream_id, epoch="epoch"
        )
        is None
    )
    assert bridge.unsubscribe(handle) is False
    assert native.calls.count("subscribe") == 1
    release.set()
    wait_until(lambda: handle in bridge._states and bridge._states[handle].pending.done)
    assert release_handle(bridge, handle) is True
    assert native.calls.count("unsubscribe") == 1


def test_poll_nonblocking_same_batch_and_cloned_source(system):
    bridge, owner, holder = system
    handle = bridge.subscribe(_KEY, generation="gen")
    native = holder["provider"]
    descriptor = bridge.describe_source(
        handle, key=_KEY, generation="gen", stream_id=_KEY.stream_id, epoch="epoch"
    )
    assert descriptor.key == _KEY and descriptor.epoch == "epoch"
    native.events.append(MarketProviderEvent("gen", "2026-01-01T00:00:00Z", {"value": [1, 2]}))
    release = threading.Event()
    native.gate = ("poll", release)
    before = time.monotonic()
    assert poll_after_ready(bridge, handle, limit=1) == ()
    assert time.monotonic() - before < 0.1
    assert native.entered.wait(1)
    with pytest.raises(GatewayOwnerMarketError, match="MARKET_OPERATION_PENDING"):
        bridge.source_epoch_reset_required(handle)
    release.set()
    wait_until(lambda: bridge._states[handle].pending.done)
    batch = poll_after_ready(bridge, handle, limit=1)
    assert len(batch) == 1 and batch[0].data["value"] == (1, 2)
    assert native.calls.count("poll") == 1
    assert release_handle(bridge, handle)


def test_delayed_ack_settles_before_reset_requery(system):
    bridge, owner, holder = system
    handle = bridge.subscribe(_KEY, generation="gen")
    native = holder["provider"]
    native.reset = True
    assert settle_action(lambda: bridge.source_epoch_reset_required(handle))
    release = threading.Event()
    native.gate = ("ack", release)
    with pytest.raises(GatewayOwnerMarketError, match="MARKET_OPERATION_PENDING"):
        bridge.acknowledge_source_epoch_reset(handle, epoch="new-epoch")
    assert native.entered.wait(1)
    with pytest.raises(GatewayOwnerMarketError, match="MARKET_OPERATION_PENDING"):
        bridge.source_epoch_reset_required(handle)
    assert native.calls.count("ack") == 1 and native.calls.count("reset") == 1
    release.set()
    wait_until(lambda: bridge._states[handle].pending.done)
    assert settle_action(lambda: bridge.source_epoch_reset_required(handle)) is False
    assert native.calls.count("ack") == 1 and native.calls.count("reset") == 2
    assert release_handle(bridge, handle)


def test_failed_result_latches_gap_and_release_still_works(system):
    bridge, owner, holder = system
    handle = bridge.subscribe(_KEY, generation="gen")
    other = bridge.subscribe(_OTHER_KEY, generation="gen")
    native = holder["provider"]
    native.fail_payload = True
    assert poll_after_ready(bridge, handle, limit=1) == ()
    wait_until(lambda: bridge._states[handle].pending.done)
    with pytest.raises(GatewayOwnerMarketError, match="LOCAL_RESULT_GAP"):
        poll_after_ready(bridge, handle, limit=1)
    with pytest.raises(GatewayOwnerMarketError, match="LOCAL_RESULT_GAP"):
        bridge.source_epoch_reset_required(handle)
    native.fail_payload = False
    assert settle_action(lambda: bridge.source_epoch_reset_required(other)) is False
    with pytest.raises(GatewayOwnerServiceCallError, match="SERVICE_PORT_ALREADY_CLAIMED"):
        GatewayOwnerMarketProvider(owner, lambda: native)
    assert release_handle(bridge, other)
    assert release_handle(bridge, handle)


def test_result_gap_does_not_allow_reacquisition_of_same_key(system):
    bridge, _, holder = system
    handle = bridge.subscribe(_KEY, generation="gen")
    holder["provider"].fail_payload = True
    assert poll_after_ready(bridge, handle, limit=1) == ()
    wait_until(lambda: bridge._states[handle].pending.done)
    with pytest.raises(GatewayOwnerMarketError, match="LOCAL_RESULT_GAP"):
        poll_after_ready(bridge, handle, limit=1)
    assert release_handle(bridge, handle)
    holder["provider"].fail_payload = False
    with pytest.raises(GatewayOwnerMarketError, match="LOCAL_RESULT_GAP"):
        bridge.subscribe(_KEY, generation="next-gen")


def test_native_poll_failure_after_drain_latches_key_gap(system):
    bridge, _, holder = system
    handle = bridge.subscribe(_KEY, generation="gen")
    native = holder["provider"]
    native.events.append(MarketProviderEvent("gen", "2026-01-01T00:00:00Z", {"value": 1}))
    native.fail_after_drain = True
    assert poll_after_ready(bridge, handle, limit=1) == ()
    wait_until(lambda: bridge._states[handle].pending.done)
    with pytest.raises(GatewayOwnerMarketError, match="LOCAL_RESULT_GAP"):
        bridge.source_epoch_reset_required(handle)
    assert native.drained == 1
    assert release_handle(bridge, handle)
    with pytest.raises(GatewayOwnerMarketError, match="LOCAL_RESULT_GAP"):
        bridge.subscribe(_KEY, generation="retry")


def test_expired_readiness_permit_cannot_consume_or_drain(system):
    bridge, _, holder = system
    handle = bridge.subscribe(_KEY, generation="gen")
    native = holder["provider"]
    assert settle_action(lambda: bridge.source_epoch_reset_required(handle)) is False
    time.sleep(0.04)
    with pytest.raises(GatewayOwnerMarketError, match="MARKET_SOURCE_NOT_READY"):
        bridge.poll(handle, limit=1)
    assert native.calls.count("poll") == 0
    assert poll_after_ready(bridge, handle, limit=1) == ()
    wait_until(lambda: bridge._states[handle].pending.done)
    assert release_handle(bridge, handle)


@pytest.mark.parametrize("invalid", [None, 1, "ready"])
def test_missing_or_nonboolean_readiness_fails_closed(system, invalid):
    bridge, _, holder = system
    handle = bridge.subscribe(_KEY, generation="gen")
    native = holder["provider"]
    native.retained_events_ready = None if invalid is None else lambda _handle: invalid
    with pytest.raises(GatewayOwnerMarketError, match="MARKET_SOURCE_UNAVAILABLE"):
        settle_action(lambda: bridge.source_epoch_reset_required(handle))
    with pytest.raises(GatewayOwnerMarketError, match="MARKET_SOURCE_NOT_READY"):
        bridge.poll(handle, limit=1)
    assert native.calls.count("poll") == 0
    assert release_handle(bridge, handle)


def test_gap_capacity_does_not_double_count_retired_handle(system):
    bridge, _, holder = system
    bridge._max_handles = 2
    handle = bridge.subscribe(_KEY, generation="gen")
    holder["provider"].fail_payload = True
    assert poll_after_ready(bridge, handle, limit=1) == ()
    wait_until(lambda: bridge._states[handle].pending.done)
    with pytest.raises(GatewayOwnerMarketError, match="LOCAL_RESULT_GAP"):
        poll_after_ready(bridge, handle, limit=1)
    assert release_handle(bridge, handle)
    holder["provider"].fail_payload = False
    other = bridge.subscribe(_OTHER_KEY, generation="gen")
    assert release_handle(bridge, other)


def test_close_uses_one_budget_for_all_handles(system):
    bridge, owner, holder = system
    keys = [
        replace(
            _KEY,
            instrument=replace(
                _KEY.instrument, instrument_id=f"asset-{index}", native_symbol=f"SYMBOL-{index}"
            ),
        )
        for index in range(8)
    ]
    handles = [bridge.subscribe(key, generation=f"gen-{index}") for index, key in enumerate(keys)]
    native = holder["provider"]
    release = threading.Event()
    native.gate = ("unsubscribe", release)
    before = list(native.calls)
    bridge.begin_close()
    bridge.begin_close()
    assert native.calls == before
    with pytest.raises(GatewayOwnerMarketError, match="PROVIDER_CLOSED"):
        bridge.subscribe(_KEY, generation="new")
    start = time.monotonic()
    assert bridge.close() is False
    elapsed = time.monotonic() - start
    assert elapsed < 0.15
    assert native.entered.is_set()
    assert owner.status().service_queue_depth == 0
    assert native.calls.count("unsubscribe") == 1
    assert len(bridge._states) == len(handles)
    release.set()
    wait_until(lambda: bridge._states[handles[0]].pending.done)
    native.gate = None
    assert bridge.close() is True


def test_duplicate_key_rejected_while_poll_pending(system):
    bridge, _, holder = system
    handle = bridge.subscribe(_KEY, generation="gen")
    native = holder["provider"]
    release = threading.Event()
    native.gate = ("poll", release)
    assert poll_after_ready(bridge, handle, limit=1) == ()
    assert native.entered.wait(1)
    with pytest.raises(GatewayOwnerMarketError, match="MARKET_KEY_ALREADY_ACTIVE"):
        bridge.subscribe(_KEY, generation="other")
    assert native.calls.count("subscribe") == 1
    release.set()
    wait_until(lambda: bridge._states[handle].pending.done)
    native.gate = None
    assert release_handle(bridge, handle)
    second = bridge.subscribe(_KEY, generation="other")
    assert native.calls.count("subscribe") == 2
    assert release_handle(bridge, second)


def test_close_retries_and_waits_for_owner_cleanup(system):
    bridge, owner, holder = system
    handle = bridge.subscribe(_KEY, generation="gen")
    native = holder["provider"]
    native.fail_close = True
    assert not bridge.close()
    assert handle in bridge._states
    assert owner.close(2)
    assert bridge.close()


def test_release_is_idempotent_after_close_and_retired_is_bounded(system):
    bridge, _, holder = system
    handle = bridge.subscribe(_KEY, generation="gen")
    assert bridge.close()
    assert release_handle(bridge, handle) is True
    assert holder["provider"].calls.count("unsubscribe") == 1


def test_reset_discards_old_completed_poll_before_publish(system):
    bridge, _, holder = system
    handle = bridge.subscribe(_KEY, generation="gen")
    native = holder["provider"]
    native.events.append(MarketProviderEvent("gen", "2026-01-01T00:00:00Z", {"old": True}))
    assert poll_after_ready(bridge, handle, limit=1) == ()
    wait_until(lambda: bridge._states[handle].pending.done)
    native.reset = True
    assert settle_action(lambda: bridge.source_epoch_reset_required(handle)) is True
    assert settle_action(lambda: bridge.acknowledge_source_epoch_reset(handle, epoch="new"))
    native.events.append(MarketProviderEvent("gen", "2026-01-01T00:00:01Z", {"new": True}))
    assert poll_after_ready(bridge, handle, limit=1) == ()
    wait_until(lambda: bridge._states[handle].pending.done)
    new_batch = poll_after_ready(bridge, handle, limit=1)
    assert len(new_batch) == 1 and new_batch[0].data == {"new": True}
    assert release_handle(bridge, handle)


def test_subscription_queued_cancel_is_safe(system):
    bridge, owner, holder = system
    entered, release = threading.Event(), threading.Event()

    def block():
        entered.set()
        assert release.wait(2)

    task = owner.submit_service_call(block)
    assert entered.wait(1)
    try:
        handle = bridge.subscribe(_KEY, generation="gen")
        assert release_handle(bridge, handle)
        assert release_handle(bridge, handle)
        assert "provider" not in holder
    finally:
        release.set()
        task.result(timeout_seconds=1)


def test_timed_out_descriptor_and_reset_are_requeried(system):
    bridge, _, holder = system
    handle = bridge.subscribe(_KEY, generation="gen")
    native = holder["provider"]
    release = threading.Event()
    native.gate = ("describe", release)
    assert (
        bridge.describe_source(
            handle, key=_KEY, generation="gen", stream_id=_KEY.stream_id, epoch="epoch"
        )
        is None
    )
    release.set()
    wait_until(lambda: bridge._states[handle].pending.done)
    native.gate = None
    native.source = "new-source"
    descriptor = bridge.describe_source(
        handle, key=_KEY, generation="gen", stream_id=_KEY.stream_id, epoch="epoch"
    )
    assert descriptor.source == "new-source" and native.calls.count("describe") == 2
    release.clear()
    native.gate = ("reset", release)
    with pytest.raises(GatewayOwnerMarketError, match="MARKET_OPERATION_PENDING"):
        bridge.source_epoch_reset_required(handle)
    release.set()
    wait_until(lambda: bridge._states[handle].pending.done)
    native.gate = None
    time.sleep(0.04)
    native.reset = True
    assert settle_action(lambda: bridge.source_epoch_reset_required(handle)) is True
    assert native.calls.count("reset") == 2
    assert release_handle(bridge, handle)


def test_queue_full_does_not_acquire_or_keep_phantom_handle(tmp_path):
    harness = Harness(tmp_path)
    owner = owner_for(harness.factory, service_queue_size=1)
    assert owner.start(2)
    bridge = GatewayOwnerMarketProvider(owner, lambda: None, call_timeout_seconds=0.02)
    entered, release = threading.Event(), threading.Event()

    def block():
        entered.set()
        assert release.wait(2)

    active = owner.submit_service_call(block)
    assert entered.wait(1)
    queued = owner.submit_service_call(lambda: None)
    try:
        with pytest.raises(GatewayOwnerMarketError, match="SERVICE_CALL_QUEUE_FULL"):
            bridge.subscribe(_KEY, generation="gen")
        assert not bridge._states
    finally:
        queued.cancel()
        release.set()
        active.result(timeout_seconds=1)
        assert bridge.close()
        assert owner.close(2)


def test_expired_subscription_settled_by_descriptor_can_still_release(tmp_path):
    harness = Harness(tmp_path)
    owner = owner_for(harness.factory, max_queue_wait_seconds=0.03)
    assert owner.start(2)
    calls = []
    bridge = GatewayOwnerMarketProvider(
        owner, lambda: calls.append(True), call_timeout_seconds=0.01
    )
    entered, release = threading.Event(), threading.Event()

    def block():
        entered.set()
        assert release.wait(2)

    active = owner.submit_service_call(block)
    assert entered.wait(1)
    try:
        handle = bridge.subscribe(_KEY, generation="gen")
        time.sleep(0.05)
        release.set()
        active.result(timeout_seconds=1)
        wait_until(lambda: bridge._states[handle].pending.done)
        with pytest.raises(GatewayOwnerMarketError, match="SERVICE_CALL_EXPIRED"):
            bridge.describe_source(
                handle, key=_KEY, generation="gen", stream_id=_KEY.stream_id, epoch="epoch"
            )
        assert release_handle(bridge, handle)
        assert not calls
    finally:
        release.set()
        assert bridge.close()
        assert owner.close(2)


@pytest.mark.parametrize("kind", ["poll", "ack"])
def test_expired_queued_poll_and_ack_resume_safely(tmp_path, kind):
    harness = Harness(tmp_path)
    owner = owner_for(harness.factory, max_queue_wait_seconds=0.04)
    assert owner.start(2)
    holder = {}

    def getter():
        if "native" not in holder:
            holder["native"] = Native()
        return holder["native"]

    bridge = GatewayOwnerMarketProvider(owner, getter, call_timeout_seconds=0.01)
    handle = bridge.subscribe(_KEY, generation="gen")
    native = holder["native"]
    native.reset = kind == "ack"
    if kind == "ack":
        assert settle_action(lambda: bridge.source_epoch_reset_required(handle))
    else:
        assert settle_action(lambda: bridge.source_epoch_reset_required(handle)) is False
    entered, release = threading.Event(), threading.Event()

    def block():
        entered.set()
        assert release.wait(2)

    active = owner.submit_service_call(block)
    assert entered.wait(1)
    try:
        if kind == "poll":
            assert bridge.poll(handle, limit=1) == ()
        else:
            with pytest.raises(GatewayOwnerMarketError, match="MARKET_OPERATION_PENDING"):
                bridge.acknowledge_source_epoch_reset(handle, epoch="epoch-kept")
        time.sleep(0.06)
        release.set()
        active.result(timeout_seconds=1)
        wait_until(lambda: bridge._states[handle].pending.done)
        with pytest.raises(GatewayOwnerMarketError, match="SERVICE_CALL_EXPIRED"):
            bridge.source_epoch_reset_required(handle)
        assert settle_action(lambda: bridge.source_epoch_reset_required(handle)) is False
        if kind == "ack":
            assert native.ack_epochs == ["epoch-kept"]
        else:
            assert native.calls.count("poll") == 0
        assert release_handle(bridge, handle)
    finally:
        release.set()
        assert bridge.close()
        assert owner.close(2)


def test_ack_queue_full_preserves_epoch_for_later_reset_check(tmp_path):
    harness = Harness(tmp_path)
    owner = owner_for(harness.factory, service_queue_size=1)
    assert owner.start(2)
    holder = {}

    def getter():
        if "native" not in holder:
            holder["native"] = Native()
        return holder["native"]

    bridge = GatewayOwnerMarketProvider(owner, getter, call_timeout_seconds=0.02)
    handle = bridge.subscribe(_KEY, generation="gen")
    native = holder["native"]
    native.reset = True
    assert settle_action(lambda: bridge.source_epoch_reset_required(handle))
    entered, release = threading.Event(), threading.Event()

    def block():
        entered.set()
        assert release.wait(2)

    active = owner.submit_service_call(block)
    assert entered.wait(1)
    queued = owner.submit_service_call(lambda: None)
    try:
        with pytest.raises(GatewayOwnerMarketError, match="SERVICE_CALL_QUEUE_FULL"):
            bridge.acknowledge_source_epoch_reset(handle, epoch="epoch-kept")
        assert queued.cancel()
        release.set()
        active.result(timeout_seconds=1)
        assert settle_action(lambda: bridge.source_epoch_reset_required(handle)) is False
        assert native.ack_epochs == ["epoch-kept"]
        assert release_handle(bridge, handle)
    finally:
        release.set()
        assert bridge.close()
        assert owner.close(2)


def test_uncertain_acquisition_retained_until_confirmed_owner_cleanup(tmp_path):
    harness = Harness(tmp_path)
    harness.fail_cleanup = 1
    owner = owner_for(harness.factory)
    assert owner.start(2)
    holder = {}

    def getter():
        if "native" not in holder:
            holder["native"] = Native()
            holder["native"].fail_subscribe = True
        return holder["native"]

    bridge = GatewayOwnerMarketProvider(owner, getter, call_timeout_seconds=0.1)
    try:
        with pytest.raises(GatewayOwnerMarketError, match="SERVICE_CALL_FAILED"):
            bridge.subscribe(_KEY, generation="gen")
        assert not bridge.close()
        assert not owner.close(0.1)
        assert not owner.closed and not bridge.close()
        assert owner.close(2) and owner.closed
        assert bridge.close()
    finally:
        owner.close(2)


def test_wrong_handle_thread_and_pid(system, monkeypatch):
    bridge, _, _ = system
    with pytest.raises(GatewayOwnerMarketError, match="INVALID_MARKET_HANDLE"):
        bridge.poll(object(), limit=1)
    errors = []

    def other_thread():
        try:
            bridge.close()
        except GatewayOwnerMarketError as error:
            errors.append(error.code)

    thread = threading.Thread(target=other_thread)
    thread.start()
    thread.join()
    assert errors == ["OWNER_THREAD_REQUIRED"]
    with monkeypatch.context() as patch:
        patch.setattr(os, "getpid", lambda: bridge._pid + 1)
        with pytest.raises(GatewayOwnerMarketError, match="PROCESS_BOUNDARY"):
            bridge.close()


def test_native_owner_reentry_rejected(system):
    bridge, owner, _ = system

    def reenter():
        try:
            bridge.close()
        except GatewayOwnerMarketError as error:
            return error.code

    assert owner.submit_service_call(reenter).result(timeout_seconds=1) == "OWNER_THREAD_REQUIRED"


def test_fork_cannot_use_inherited_proxy(system):
    import select
    import signal

    if not hasattr(os, "fork"):
        pytest.skip("fork unavailable")
    bridge, _, _ = system
    read_fd, write_fd = os.pipe()
    child = os.fork()
    if child == 0:
        os.close(read_fd)
        try:
            bridge.close()
        except GatewayOwnerMarketError as error:
            os.write(write_fd, error.code.encode("ascii"))
        finally:
            os._exit(0)
    os.close(write_fd)
    try:
        ready, _, _ = select.select([read_fd], [], [], 2)
        if not ready:
            os.kill(child, signal.SIGKILL)
            pytest.fail("inherited proxy blocked")
        assert os.read(read_fd, 128) == b"PROCESS_BOUNDARY"
    finally:
        os.close(read_fd)
        os.waitpid(child, 0)
