"""Single-owner bounded close lifecycle for an SDK API instance."""

from __future__ import annotations

import threading
from typing import Any, Callable, Optional


class BoundedApiCloser:
    """Retain one close outcome and prevent another API taking its place.

    The caller supplies framework-specific failure and health projections;
    the closer owns its lock, close worker, generation and retained outcome.
    """

    def __init__(
        self,
        *,
        error_type: type[Exception],
        on_pending: Callable[[], None],
        on_failure: Callable[[BaseException, bool, bool], None],
        on_timeout: Callable[[], None],
        on_success: Callable[[], None],
        on_worker_error: Callable[[], None],
        thread_name: str,
    ) -> None:
        self._error_type = error_type
        self._on_pending = on_pending
        self._on_failure = on_failure
        self._on_timeout = on_timeout
        self._on_success = on_success
        self._on_worker_error = on_worker_error
        self._thread_name = thread_name
        self._lock = threading.RLock()
        self.thread: Optional[threading.Thread] = None
        self.generation = 0
        self.state: Optional[dict[str, Any]] = None
        self.last_successful_api: Any = None

    def close(
        self,
        api: Any,
        timeout: float,
        *,
        select_close: Callable[[], Any],
        generation: Callable[[], int],
    ) -> tuple[bool, Optional[BaseException]]:
        """Start at most one close for an exact API and observe its result."""
        wait_timeout = max(float(timeout), 0.0)
        with self._lock:
            if self.last_successful_api is api:
                return True, None

            state = self.state
            if state is not None:
                if state.get("api") is not api:
                    self._on_pending()
                    return False, self._error_type(
                        "A different SDK API already owns the retained close outcome"
                    )
                close_thread = state.get("thread")
            else:
                try:
                    close = select_close()
                except BaseException as error:
                    self.state = {"api": api, "thread": None, "error": [error]}
                    self._on_failure(error, True, False)
                    return True, error
                if not callable(close):
                    error = self._error_type("The SDK client does not expose close()")
                    self.state = {"api": api, "thread": None, "error": [error]}
                    self._on_failure(error, False, True)
                    return True, error

                outcome: list[Optional[BaseException]] = [None]

                def invoke_close() -> None:
                    try:
                        close()
                    except BaseException as error:
                        self._on_worker_error()
                        outcome[0] = error

                close_thread = threading.Thread(
                    target=invoke_close, name=self._thread_name, daemon=True
                )
                state = {"api": api, "thread": close_thread, "error": outcome}
                self.state = state
                self.thread = close_thread
                self.generation = generation()
                self._on_pending()
                try:
                    close_thread.start()
                except BaseException as error:
                    outcome[0] = error
                    self._on_failure(error, True, False)
                    return True, error

        if close_thread is not None and close_thread.is_alive():
            close_thread.join(wait_timeout)

        with self._lock:
            if self.last_successful_api is api:
                return True, None
            state = self.state
            if state is None or state.get("api") is not api:
                self._on_pending()
                return False, self._error_type("SDK close outcome ownership changed")
            close_thread = state.get("thread")
            if close_thread is not None and close_thread.is_alive():
                self._on_timeout()
                return False, None

            close_error = state.get("error", [None])[0]
            if close_error is not None:
                self._on_failure(close_error, True, False)
                return True, close_error

            self.state = None
            self.thread = None
            self._on_success()
            self.last_successful_api = api
            return True, None


__all__ = ("BoundedApiCloser",)
