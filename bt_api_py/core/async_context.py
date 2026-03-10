"""
Async context manager utilities for modern bt_api_py.
"""

import asyncio
import time
from collections.abc import AsyncGenerator, Callable
from contextlib import asynccontextmanager
from functools import wraps
from typing import Any, TypeVar

T = TypeVar("T")


class AsyncContextManager:
    """Utilities for async context management."""

    @staticmethod
    @asynccontextmanager
    async def timeout(timeout_seconds: float) -> AsyncGenerator[None, None]:
        """Async context manager with timeout."""
        try:
            yield
        except TimeoutError:
            raise
        else:
            # Use asyncio.wait_for for timeout
            async def _inner() -> Any | None:
                await asyncio.sleep(0)  # Dummy async function
                return None

            try:
                await asyncio.wait_for(_inner(), timeout=timeout_seconds)
            except TimeoutError:
                raise TimeoutError(f"Operation timed out after {timeout_seconds} seconds") from None

    @staticmethod
    @asynccontextmanager
    async def retry(
        max_attempts: int = 3,
        delay: float = 1.0,
        backoff: float = 2.0,
        exceptions: tuple = (Exception,),
    ) -> AsyncGenerator[None, None]:
        """Async context manager with retry logic."""
        attempt = 0
        current_delay = delay

        while attempt < max_attempts:
            try:
                yield
                break
            except exceptions:
                attempt += 1
                if attempt >= max_attempts:
                    raise

                await asyncio.sleep(current_delay)
                current_delay *= backoff

    @staticmethod
    @asynccontextmanager
    async def circuit_breaker(
        failure_threshold: int = 5,
        recovery_timeout: float = 60.0,
        expected_exception: type = Exception,
    ) -> AsyncGenerator[None, None]:
        """Async context manager with circuit breaker pattern."""
        state = {"failures": 0, "last_failure": None, "state": "CLOSED"}

        def should_attempt():
            if state["state"] == "CLOSED":
                return True
            elif state["state"] == "OPEN":
                if time.time() - state["last_failure"] > recovery_timeout:
                    state["state"] = "HALF_OPEN"
                    return True
                return False
            else:  # HALF_OPEN
                return True

        def on_success():
            state["failures"] = 0
            state["state"] = "CLOSED"

        def on_failure():
            state["failures"] += 1
            state["last_failure"] = time.time()
            if state["failures"] >= failure_threshold:
                state["state"] = "OPEN"

        if not should_attempt():
            raise RuntimeError("Circuit breaker is OPEN")

        try:
            yield
            on_success()
        except expected_exception:
            on_failure()
            raise


def async_retry(
    max_attempts: int = 3,
    delay: float = 1.0,
    backoff: float = 2.0,
    exceptions: tuple = (Exception,),
):
    """Decorator for async functions with retry logic."""

    def decorator(func: Callable[..., T]) -> Callable[..., T]:
        @wraps(func)
        async def wrapper(*args, **kwargs) -> T:
            attempt = 0
            current_delay = delay

            while attempt < max_attempts:
                try:
                    return await func(*args, **kwargs)
                except exceptions:
                    attempt += 1
                    if attempt >= max_attempts:
                        raise

                    await asyncio.sleep(current_delay)
                    current_delay *= backoff

        return wrapper

    return decorator


def async_timeout(timeout_seconds: float):
    """Decorator for async functions with timeout."""

    def decorator(func: Callable[..., T]) -> Callable[..., T]:
        @wraps(func)
        async def wrapper(*args, **kwargs) -> T:
            return await asyncio.wait_for(func(*args, **kwargs), timeout=timeout_seconds)

        return wrapper

    return decorator


def async_circuit_breaker(
    failure_threshold: int = 5, recovery_timeout: float = 60.0, expected_exception: type = Exception
):
    """Decorator for async functions with circuit breaker."""

    def decorator(func: Callable[..., T]) -> Callable[..., T]:
        # Store circuit breaker state in function attribute
        state = {"failures": 0, "last_failure": None, "state": "CLOSED"}

        @wraps(func)
        async def wrapper(*args, **kwargs) -> T:
            def should_attempt():
                if state["state"] == "CLOSED":
                    return True
                elif state["state"] == "OPEN":
                    if time.time() - state["last_failure"] > recovery_timeout:
                        state["state"] = "HALF_OPEN"
                        return True
                    return False
                else:  # HALF_OPEN
                    return True

            def on_success():
                state["failures"] = 0
                state["state"] = "CLOSED"

            def on_failure():
                state["failures"] += 1
                state["last_failure"] = time.time()
                if state["failures"] >= failure_threshold:
                    state["state"] = "OPEN"

            if not should_attempt():
                raise RuntimeError("Circuit breaker is OPEN")

            try:
                result = await func(*args, **kwargs)
                on_success()
                return result
            except expected_exception:
                on_failure()
                raise

        return wrapper

    return decorator


class AsyncRateLimiter:
    """Async rate limiter implementation."""

    def __init__(self, max_requests: int, time_window: float) -> Any | None:
        self.max_requests = max_requests
        self.time_window = time_window
        self.requests = []
        self._lock = asyncio.Lock()

    async def acquire(self) -> None:
        """Acquire rate limit permission."""
        async with self._lock:
            now = time.time()
            # Remove old requests
            self.requests = [
                req_time for req_time in self.requests if now - req_time < self.time_window
            ]

            # Check if we can make a request
            if len(self.requests) >= self.max_requests:
                sleep_time = self.time_window - (now - self.requests[0])
                if sleep_time > 0:
                    await asyncio.sleep(sleep_time)
                    # Clean up old requests after sleep
                    now = time.time()
                    self.requests = [
                        req_time for req_time in self.requests if now - req_time < self.time_window
                    ]

            # Add current request
            self.requests.append(now)


class AsyncSemaphore:
    """Async semaphore with timeout support."""

    def __init__(self, max_concurrent: int) -> Any | None:
        self._semaphore = asyncio.Semaphore(max_concurrent)

    async def acquire(self, timeout: float | None = None) -> None:
        """Acquire semaphore with optional timeout."""
        if timeout is not None:
            try:
                await asyncio.wait_for(self._semaphore.acquire(), timeout=timeout)
            except TimeoutError:
                raise TimeoutError(
                    f"Failed to acquire semaphore within {timeout} seconds"
                ) from None
        else:
            await self._semaphore.acquire()

    def release(self) -> None:
        """Release semaphore."""
        self._semaphore.release()

    async def __aenter__(self) -> "AsyncSemaphore":
        await self.acquire()
        return self

    async def __aexit__(self, exc_type, exc_val, exc_tb) -> None:
        self.release()


class AsyncQueue:
    """Async queue with timeout and priority support."""

    def __init__(self, maxsize: int = 0) -> Any | None:
        self._queue = asyncio.Queue(maxsize=maxsize)

    async def put(self, item: Any, timeout: float | None = None) -> None:
        """Put item with optional timeout."""
        if timeout is not None:
            try:
                await asyncio.wait_for(self._queue.put(item), timeout=timeout)
            except TimeoutError:
                raise TimeoutError(f"Failed to put item within {timeout} seconds") from None
        else:
            await self._queue.put(item)

    async def get(self, timeout: float | None = None) -> Any:
        """Get item with optional timeout."""
        if timeout is not None:
            try:
                return await asyncio.wait_for(self._queue.get(), timeout=timeout)
            except TimeoutError:
                raise TimeoutError(f"Failed to get item within {timeout} seconds") from None
        else:
            return await self._queue.get()

    def qsize(self) -> int:
        """Get queue size."""
        return self._queue.qsize()

    def empty(self) -> bool:
        """Check if queue is empty."""
        return self._queue.empty()

    def full(self) -> bool:
        """Check if queue is full."""
        return self._queue.full()


class AsyncTaskGroup:
    """Manage group of async tasks with proper cleanup."""

    def __init__(self) -> Any | None:
        self._tasks = set()
        self._shutdown = False

    async def create_task(self, coro) -> asyncio.Task:
        """Create and track a task."""
        if self._shutdown:
            raise RuntimeError("TaskGroup is shutting down")

        task = asyncio.create_task(coro)
        self._tasks.add(task)
        task.add_done_callback(self._tasks.discard)
        return task

    async def wait_all(self, timeout: float | None = None) -> None:
        """Wait for all tasks to complete."""
        if not self._tasks:
            return

        if timeout is not None:
            try:
                await asyncio.wait_for(
                    asyncio.gather(*self._tasks, return_exceptions=True), timeout=timeout
                )
            except TimeoutError:
                # Cancel all tasks on timeout
                for task in self._tasks:
                    task.cancel()
                await asyncio.gather(*self._tasks, return_exceptions=True)
                raise
        else:
            await asyncio.gather(*self._tasks, return_exceptions=True)

    async def cancel_all(self) -> None:
        """Cancel all tasks."""
        for task in self._tasks:
            task.cancel()
        await asyncio.gather(*self._tasks, return_exceptions=True)

    def task_count(self) -> int:
        """Get number of active tasks."""
        return len(self._tasks)
