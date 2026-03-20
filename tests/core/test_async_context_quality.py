from __future__ import annotations

import pytest

from bt_api_py.core.async_context import (
    AsyncContextManager,
    AsyncQueue,
    AsyncRateLimiter,
    AsyncSemaphore,
    AsyncTaskGroup,
    async_circuit_breaker,
    async_retry,
    async_timeout,
)


def test_async_retry_rejects_invalid_configuration() -> None:
    with pytest.raises(ValueError, match="max_attempts must be > 0"):
        async_retry(max_attempts=0)

    with pytest.raises(ValueError, match="delay must be >= 0"):
        async_retry(delay=-1)

    with pytest.raises(ValueError, match="backoff must be > 0"):
        async_retry(backoff=0)

    with pytest.raises(ValueError, match="exceptions must not be empty"):
        async_retry(exceptions=())

    with pytest.raises(TypeError, match="exceptions must contain only BaseException subclasses"):
        async_retry(exceptions=("ValueError",))


def test_async_timeout_rejects_non_positive_timeout() -> None:
    with pytest.raises(ValueError, match="timeout_seconds must be > 0"):
        async_timeout(0)


def test_async_circuit_breaker_rejects_invalid_configuration() -> None:
    with pytest.raises(ValueError, match="failure_threshold must be > 0"):
        async_circuit_breaker(failure_threshold=0)

    with pytest.raises(ValueError, match="recovery_timeout must be >= 0"):
        async_circuit_breaker(recovery_timeout=-1)

    with pytest.raises(TypeError, match="expected_exception must be a BaseException subclass"):
        async_circuit_breaker(expected_exception="RuntimeError")


@pytest.mark.asyncio
async def test_async_context_manager_timeout_rejects_non_positive_timeout() -> None:
    with pytest.raises(ValueError, match="timeout_seconds must be > 0"):
        async with AsyncContextManager.timeout(0):
            pass


def test_async_rate_limiter_rejects_invalid_configuration() -> None:
    with pytest.raises(ValueError, match="max_requests must be > 0"):
        AsyncRateLimiter(max_requests=0, time_window=1)

    with pytest.raises(ValueError, match="time_window must be > 0"):
        AsyncRateLimiter(max_requests=1, time_window=0)


def test_async_semaphore_rejects_invalid_configuration() -> None:
    with pytest.raises(ValueError, match="max_concurrent must be > 0"):
        AsyncSemaphore(max_concurrent=0)


@pytest.mark.asyncio
async def test_async_semaphore_rejects_non_positive_timeout() -> None:
    semaphore = AsyncSemaphore(max_concurrent=1)

    with pytest.raises(ValueError, match="timeout must be > 0"):
        await semaphore.acquire(timeout=0)


def test_async_queue_rejects_invalid_maxsize() -> None:
    with pytest.raises(ValueError, match="maxsize must be >= 0"):
        AsyncQueue(maxsize=-1)


@pytest.mark.asyncio
async def test_async_queue_rejects_non_positive_timeout() -> None:
    queue = AsyncQueue()

    with pytest.raises(ValueError, match="timeout must be > 0"):
        await queue.get(timeout=0)

    with pytest.raises(ValueError, match="timeout must be > 0"):
        await queue.put("item", timeout=0)


@pytest.mark.asyncio
async def test_async_task_group_wait_all_rejects_non_positive_timeout() -> None:
    group = AsyncTaskGroup()

    with pytest.raises(ValueError, match="timeout must be > 0"):
        await group.wait_all(timeout=0)
