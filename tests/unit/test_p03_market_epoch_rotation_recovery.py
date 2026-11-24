"""P0-3 regression tests: ServiceMarketClient epoch rotation with require_source_descriptor=True
(SimNow MD disconnect reason=8193 -> Gateway rebuilds MD session and rotates stream epoch)."""
from __future__ import annotations

from dataclasses import replace

import pytest

from bt_api_py.service_market import ServiceMarketError
from tests.unit.test_service_market import (
    _KEY,
    _close_all,
    _make_client,
    _test_source_descriptor,
)


def test_pub_frame_from_new_epoch_with_required_source_descriptor_triggers_clean_recovery(tmp_path):
    """Receiving a live PUB frame from a rotated epoch must transition cleanly into
    recovery_required=True / healthy=False so recover() -> accept_resync() refreshes
    the MarketSourceDescriptor and resumes live tick delivery."""
    desc_v1 = _test_source_descriptor(epoch="market-epoch-one")
    client, runtime, registry, market, _transport, subscribers = _make_client(
        tmp_path, require_source_descriptor=True
    )
    market.source_descriptor = desc_v1
    try:
        bootstrap = client.start()
        assert bootstrap.source_descriptor == desc_v1
        assert client.healthy is True
        assert client.source_descriptor == desc_v1

        # Deliver one normal epoch-1 tick
        ev1 = market.publish(_KEY, "tick-v1-1")
        subscribers.created[-1].events.append(ev1)
        got1 = client.poll(timeout_ms=0)
        assert got1 is not None and got1.header.stream_epoch == "market-epoch-one"

        # Simulate SimNow MD reconnect (reason=8193) -> Gateway rebuilds MD session
        # (stream_generation=2) and rotates MarketHub stream epoch to market-epoch-two.
        desc_v2 = replace(
            desc_v1,
            epoch="market-epoch-two",
            connection_generation=2,
            clock_source_session_id="synthetic-clock-session-gen2",
        )
        market.epochs[_KEY.stream_id] = "market-epoch-two"
        market.events[_KEY.stream_id] = []
        market.source_descriptor = desc_v2
        ev2 = market.publish(_KEY, "tick-v2-1")
        subscribers.created[-1].events.append(ev2)

        # poll() must fail closed with RECOVERY_REQUIRED (never escape SOURCE_DESCRIPTOR_EPOCH_MISMATCH
        # while leaving recovery_required=False and healthy=True!)
        with pytest.raises(ServiceMarketError) as err:
            client.poll(timeout_ms=0)
        assert err.value.code == "RECOVERY_REQUIRED"
        assert client.recovery_required is True
        assert client.resync_required is False
        assert client.healthy is False
        assert client.source_descriptor is None

        # Step 1 of adapter recovery: recover() re-subscribes and validates desc_v2,
        # returning resync_required=True so the consumer acknowledges epoch discontinuity.
        rec_bootstrap = client.recover()
        assert rec_bootstrap.resync_required is True
        assert rec_bootstrap.snapshot_epoch == "market-epoch-two"
        assert rec_bootstrap.source_descriptor == desc_v2
        assert client.resync_required is True

        # Step 2 of adapter recovery: accept_resync(acknowledge_data_loss=True) adopts epoch-2 + desc_v2.
        resync_bootstrap = client.accept_resync(acknowledge_data_loss=True)
        assert resync_bootstrap.resync_required is False
        assert resync_bootstrap.snapshot_epoch == "market-epoch-two"
        assert resync_bootstrap.source_descriptor == desc_v2
        assert client.healthy is True
        assert client.recovery_required is False
        assert client.resync_required is False
        assert client.source_descriptor == desc_v2

        # Live epoch-2 ticks resume cleanly!
        ev3 = market.publish(_KEY, "tick-v2-2")
        subscribers.created[-1].events.append(ev3)
        got3 = client.poll(timeout_ms=0)
        assert got3 is not None
        assert got3.header.stream_epoch == "market-epoch-two"
        assert got3.header.sequence == ev3.header.sequence
    finally:
        _close_all(client, runtime, registry)


def test_double_epoch_rotation_during_accept_resync_clears_resync_and_requires_recover(tmp_path):
    """If the Gateway rotates epoch a second time between recover() and accept_resync(),
    accept_resync() must clear resync_required, set recovery_required=True, and mark
    healthy=False so the consumer runs recover() again to fetch the newest descriptor."""
    desc_v1 = _test_source_descriptor(epoch="market-epoch-one")
    client, runtime, registry, market, _transport, subscribers = _make_client(
        tmp_path, require_source_descriptor=True
    )
    market.source_descriptor = desc_v1
    try:
        client.start()

        # First rotation: epoch-1 -> epoch-2
        desc_v2 = replace(
            desc_v1,
            epoch="market-epoch-two",
            connection_generation=2,
            clock_source_session_id="synthetic-clock-session-gen2",
        )
        market.epochs[_KEY.stream_id] = "market-epoch-two"
        market.events[_KEY.stream_id] = []
        market.source_descriptor = desc_v2
        subscribers.created[-1].events.append(market.publish(_KEY, "tick-v2-1"))

        with pytest.raises(ServiceMarketError) as err:
            client.poll(timeout_ms=0)
        assert err.value.code == "RECOVERY_REQUIRED"

        rec_v2 = client.recover()
        assert rec_v2.resync_required is True
        assert rec_v2.source_descriptor == desc_v2

        # Second rotation BEFORE accept_resync(): epoch-2 -> epoch-3
        desc_v3 = replace(
            desc_v1,
            epoch="market-epoch-three",
            connection_generation=3,
            clock_source_session_id="synthetic-clock-session-gen3",
        )
        market.epochs[_KEY.stream_id] = "market-epoch-three"
        market.events[_KEY.stream_id] = []
        market.source_descriptor = desc_v3
        market.publish(_KEY, "tick-v3-1")

        with pytest.raises(ServiceMarketError) as err2:
            client.accept_resync(acknowledge_data_loss=True)
        assert err2.value.code == "SOURCE_DESCRIPTOR_EPOCH_MISMATCH"
        assert client.recovery_required is True
        assert client.resync_required is False
        assert client.healthy is False
        assert client.source_descriptor is None

        # Recover again -> accept_resync -> healthy on epoch-3!
        rec_v3 = client.recover()
        assert rec_v3.resync_required is True
        assert rec_v3.source_descriptor == desc_v3
        accepted = client.accept_resync(acknowledge_data_loss=True)
        assert accepted.resync_required is False
        assert accepted.source_descriptor == desc_v3
        assert client.healthy is True
        assert client.source_descriptor == desc_v3
    finally:
        _close_all(client, runtime, registry)
