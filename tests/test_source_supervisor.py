"""Source supervisor tests (Task 3.1).

The supervisor owns upstream subscription reference counting so multiple local
consumers share a single upstream stream (U-12).
"""

from __future__ import annotations

import asyncio

import pytest

from bt_api_py._contracts.models import SubscribeRequest
from bt_api_py.forwarding.source_supervisor import SourceSupervisor


class _FakeUpstream:
    def __init__(self) -> None:
        self.started: list[SubscribeRequest] = []
        self.stopped: list[tuple] = []

    async def start(self, request: SubscribeRequest) -> None:
        self.started.append(request)

    async def stop(self, key: tuple) -> None:
        self.stopped.append(key)


@pytest.fixture
def supervisor() -> tuple[SourceSupervisor, _FakeUpstream]:
    upstream = _FakeUpstream()
    return SourceSupervisor(upstream), upstream


def _request() -> SubscribeRequest:
    return SubscribeRequest(exchange_name="SIM___SPOT", symbols=["RB2510"], topics=["ticker"])


@pytest.mark.asyncio
async def test_two_consumers_share_one_upstream_subscription(
    supervisor: tuple[SourceSupervisor, _FakeUpstream],
) -> None:
    sup, upstream = supervisor
    first = await sup.subscribe(_request())
    second = await sup.subscribe(_request())
    assert sup.upstream_start_count == 1
    assert len(upstream.started) == 1

    await first.close()
    assert sup.upstream_stop_count == 0, "one remaining consumer must keep the stream alive"
    await second.close()
    assert sup.upstream_stop_count == 1
    assert len(upstream.stopped) == 1


@pytest.mark.asyncio
async def test_distinct_keys_start_distinct_upstream_streams(
    supervisor: tuple[SourceSupervisor, _FakeUpstream],
) -> None:
    sup, upstream = supervisor
    first = await sup.subscribe(_request())
    other = await sup.subscribe(
        SubscribeRequest(exchange_name="SIM___SPOT", symbols=["RB2601"], topics=["ticker"])
    )
    assert sup.upstream_start_count == 2

    await first.close()
    await other.close()
    assert sup.upstream_stop_count == 2


@pytest.mark.asyncio
async def test_double_close_is_idempotent(
    supervisor: tuple[SourceSupervisor, _FakeUpstream],
) -> None:
    sup, _ = supervisor
    handle = await sup.subscribe(_request())
    await handle.close()
    await handle.close()
    assert sup.upstream_stop_count == 1


@pytest.mark.asyncio
async def test_concurrent_first_subscriptions_share_one_upstream_start() -> None:
    class _BlockingUpstream(_FakeUpstream):
        def __init__(self) -> None:
            super().__init__()
            self.start_entered = asyncio.Event()
            self.release_start = asyncio.Event()

        async def start(self, request: SubscribeRequest) -> None:
            self.started.append(request)
            self.start_entered.set()
            await self.release_start.wait()

    upstream = _BlockingUpstream()
    sup = SourceSupervisor(upstream)
    request = _request()

    first_task = asyncio.create_task(sup.subscribe(request))
    await upstream.start_entered.wait()

    second_entered = asyncio.Event()

    async def subscribe_second():
        second_entered.set()
        return await sup.subscribe(request)

    second_task = asyncio.create_task(subscribe_second())
    await second_entered.wait()
    upstream.release_start.set()
    first, second = await asyncio.gather(first_task, second_task)

    await first.close()
    stops_after_first_close = sup.upstream_stop_count

    await second.close()
    assert (
        len(upstream.started),
        sup.upstream_start_count,
        stops_after_first_close,
        len(upstream.stopped),
        sup.upstream_stop_count,
    ) == (1, 1, 0, 1, 1)


@pytest.mark.asyncio
async def test_new_subscription_waits_for_last_stop_to_finish() -> None:
    class _SlowStopUpstream(_FakeUpstream):
        def __init__(self) -> None:
            super().__init__()
            self.first_stop_entered = asyncio.Event()
            self.release_first_stop = asyncio.Event()
            self.second_start_entered = asyncio.Event()
            self.lifecycle: list[str] = []

        async def start(self, request: SubscribeRequest) -> None:
            self.started.append(request)
            start_number = len(self.started)
            self.lifecycle.append(f"start-{start_number}")
            if start_number == 2:
                self.second_start_entered.set()

        async def stop(self, key: tuple) -> None:
            self.stopped.append(key)
            stop_number = len(self.stopped)
            self.lifecycle.append(f"stop-{stop_number}-entered")
            if stop_number == 1:
                self.first_stop_entered.set()
                await self.release_first_stop.wait()
            self.lifecycle.append(f"stop-{stop_number}-completed")

    upstream = _SlowStopUpstream()
    sup = SourceSupervisor(upstream)
    request = _request()
    first = await sup.subscribe(request)

    first_close_task = asyncio.create_task(first.close())
    await upstream.first_stop_entered.wait()

    second_attempted = asyncio.Event()

    async def subscribe_again():
        second_attempted.set()
        return await sup.subscribe(request)

    second_subscribe_task = asyncio.create_task(subscribe_again())
    try:
        await second_attempted.wait()
        assert not second_subscribe_task.done()
        assert not upstream.second_start_entered.is_set()
        assert len(upstream.started) == 1
        assert sup.upstream_start_count == 1
    finally:
        upstream.release_first_stop.set()
        await first_close_task
        second = await second_subscribe_task
        await second.close()

    assert upstream.lifecycle == [
        "start-1",
        "stop-1-entered",
        "stop-1-completed",
        "start-2",
        "stop-2-entered",
        "stop-2-completed",
    ]
    assert (len(upstream.started), sup.upstream_start_count) == (2, 2)
    assert (len(upstream.stopped), sup.upstream_stop_count) == (2, 2)
