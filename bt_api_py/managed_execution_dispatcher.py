"""SDK wrapper for an existing managed execution owner's call mailbox.

This wrapper deliberately creates no thread, event loop, provider, or native
object. Trusted SDK runtime composition supplies the already selected
owner-thread port. Calls are delegated once to that port; this wrapper never
retries callback failures or timeouts and never closes the underlying runtime.
"""

from __future__ import annotations

from typing import Callable, TypeVar

from bt_api_base.managed_execution_runtime_protocol import OwnerThreadCallPort

__all__ = ("OwnerThreadExecutionDispatcher",)

T = TypeVar("T")


class OwnerThreadExecutionDispatcher:
    """Hide the concrete runtime while preserving its narrow owner-call port.

    Construction is an SDK-internal composition step. The runtime-checkable
    protocol check verifies only method shape; it is not proof of authority.
    The caller must obtain ``owner_call_port`` from trusted composition bound
    to the exact selected runtime. No provider or Backtrader consumer should
    construct this wrapper from an arbitrary object.
    """

    __slots__ = ("__owner_call_port",)

    def __init__(self, owner_call_port: OwnerThreadCallPort) -> None:
        if not isinstance(owner_call_port, OwnerThreadCallPort):
            raise TypeError("owner_thread_call_port_required")
        self.__owner_call_port = owner_call_port

    def call_on_owner_thread(
        self,
        callback: Callable[[], T],
        *,
        timeout_seconds: float = 5.0,
    ) -> T:
        """Delegate one synchronous callback exactly once to the owner port."""

        if not callable(callback):
            raise TypeError("callback_must_be_callable")
        return self.__owner_call_port.call_on_owner_thread(
            callback,
            timeout_seconds=timeout_seconds,
        )

    def __repr__(self) -> str:
        return "<SDK owner-thread execution dispatcher>"
