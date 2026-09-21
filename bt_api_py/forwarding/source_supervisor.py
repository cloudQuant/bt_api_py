"""Source supervisor: upstream subscription reference counting (Task 3.1)."""

from __future__ import annotations

import asyncio
from typing import Any, Protocol
from weakref import WeakValueDictionary

from bt_api_py._contracts.models import SubscribeRequest


class UpstreamSource(Protocol):
    """An upstream exchange stream that the supervisor starts and stops."""

    async def start(self, request: SubscribeRequest) -> None: ...
    async def stop(self, key: tuple) -> None: ...


class _SupervisedSubscription:
    """Handle for one consumer's subscription; closing releases one refcount."""

    def __init__(self, supervisor: SourceSupervisor, key: tuple) -> None:
        self._supervisor = supervisor
        self._key = key
        self._closed = False

    async def close(self) -> None:
        if self._closed:
            return
        self._closed = True
        await self._supervisor._release(self._key)


class SourceSupervisor:
    """Refcounts upstream stream subscriptions across local consumers.

    The first consumer starts the upstream stream; the last consumer stops it.
    This replaces ``MarketDataHub`` subscribing to its own in-memory bus as a
    fake upstream-management layer (U-12).
    """

    def __init__(self, upstream: Any) -> None:
        self._upstream = upstream
        self._refcounts: dict[tuple, int] = {}
        self._key_locks: WeakValueDictionary[tuple, asyncio.Lock] = WeakValueDictionary()
        self.upstream_start_count = 0
        self.upstream_stop_count = 0

    @staticmethod
    def _key(request: SubscribeRequest) -> tuple:
        return (
            request.exchange_name,
            tuple(request.symbols),
            tuple(request.topics),
            request.account_id,
        )

    def _lock_for(self, key: tuple) -> asyncio.Lock:
        lock = self._key_locks.get(key)
        if lock is None:
            lock = asyncio.Lock()
            self._key_locks[key] = lock
        return lock

    async def subscribe(self, request: SubscribeRequest) -> _SupervisedSubscription:
        key = self._key(request)
        async with self._lock_for(key):
            count = self._refcounts.get(key, 0)
            if count == 0:
                await self._upstream.start(request)
                self.upstream_start_count += 1
            self._refcounts[key] = count + 1
        return _SupervisedSubscription(self, key)

    async def _release(self, key: tuple) -> None:
        async with self._lock_for(key):
            count = self._refcounts.get(key, 0)
            if count <= 1:
                self._refcounts.pop(key, None)
                await self._upstream.stop(key)
                self.upstream_stop_count += 1
            else:
                self._refcounts[key] = count - 1
