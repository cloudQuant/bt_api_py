"""Source supervisor tests (Task 3.1).

The supervisor owns upstream subscription reference counting so multiple local
consumers share a single upstream stream (U-12).
"""

from __future__ import annotations

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
