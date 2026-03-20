"""Tests for async context utilities."""

import asyncio

import pytest

from bt_api_py.core.async_context import (
    AsyncQueue,
    AsyncSemaphore,
    AsyncTaskGroup,
    async_circuit_breaker,
    async_retry,
    async_timeout,
)


@pytest.mark.asyncio
async def test_async_semaphore_timeout_raises_timeout_error() -> None:
    semaphore = AsyncSemaphore(max_concurrent=1)
    await semaphore.acquire()

    with pytest.raises(TimeoutError, match="Failed to acquire semaphore"):
        await semaphore.acquire(timeout=0.01)

    semaphore.release()


@pytest.mark.asyncio
async def test_async_queue_timeout_on_get() -> None:
    queue = AsyncQueue()

    with pytest.raises(TimeoutError, match="Failed to get item"):
        await queue.get(timeout=0.01)


@pytest.mark.asyncio
async def test_async_retry_retries_until_success() -> None:
    attempts = 0

    @async_retry(max_attempts=3, delay=0.0)
    async def flaky() -> str:
        nonlocal attempts
        attempts += 1
        if attempts < 3:
            raise ValueError("retry")
        return "ok"

    assert await flaky() == "ok"
    assert attempts == 3


@pytest.mark.asyncio
async def test_async_timeout_raises_timeout_error() -> None:
    @async_timeout(0.01)
    async def slow() -> None:
        await asyncio.sleep(0.1)

    with pytest.raises(TimeoutError):
        await slow()


@pytest.mark.asyncio
async def test_async_circuit_breaker_opens_after_failures() -> None:
    attempts = 0

    @async_circuit_breaker(failure_threshold=2, recovery_timeout=60.0)
    async def unstable() -> None:
        nonlocal attempts
        attempts += 1
        raise RuntimeError("boom")

    with pytest.raises(RuntimeError, match="boom"):
        await unstable()
    with pytest.raises(RuntimeError, match="boom"):
        await unstable()
    with pytest.raises(RuntimeError, match="Circuit breaker is OPEN"):
        await unstable()

    assert attempts == 2


@pytest.mark.asyncio
async def test_async_task_group_wait_all_clears_done_tasks() -> None:
    group = AsyncTaskGroup()

    async def quick(value: int) -> int:
        await asyncio.sleep(0.01)
        return value

    await group.create_task(quick(1))
    await group.create_task(quick(2))
    await group.wait_all()

    assert group.task_count() == 0
