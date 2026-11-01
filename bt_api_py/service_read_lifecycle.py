"""Owner-thread lifecycle for caller-supplied read-only service facades.

The caller supplies account validation and market close targets. This object
does not compose a runtime, choose providers, or grant read/write authority.
"""

from __future__ import annotations

import threading
from collections.abc import Callable, Iterable
from typing import Any


class ServiceReadLifecycle:
    """Own connection and one-shot facade close state on the creating thread."""

    def __init__(
        self,
        account_client: Any,
        *,
        validate_observation: Callable[[Any], None],
        market_close_targets: Callable[[], Iterable[tuple[Any, Any]]],
        error_type: type[Exception],
    ) -> None:
        self._account_client = account_client
        self._validate_observation = validate_observation
        self._market_close_targets = market_close_targets
        self._error_type = error_type
        self._owner_thread_id = threading.get_ident()
        self._connected = False
        self._closed = False
        self._closing = False
        self._closed_facade_ids: set[int] = set()

    @property
    def is_connected(self) -> bool:
        return self._connected and not self._closed and not self._closing

    @property
    def closed_or_closing(self) -> bool:
        return self._closed or self._closing

    def assert_owner(self) -> None:
        if threading.get_ident() != self._owner_thread_id:
            raise self._error_type("WRONG_OWNER_THREAD")

    def connect(self) -> None:
        self.assert_owner()
        if self._closed:
            raise self._error_type("PROXY_CLOSED")
        if self._closing:
            raise self._error_type("PROXY_CLOSING")
        if self._connected:
            return
        view = self._account_client.refresh()
        self._validate_observation(getattr(view, "observation", None))
        self._connected = True

    def close(self) -> None:
        self.assert_owner()
        if self._closed:
            return
        self._closing = True
        errors: list[BaseException] = []
        for identity_owner, target in self._market_close_targets():
            facade_id = id(identity_owner)
            if facade_id in self._closed_facade_ids:
                continue
            try:
                target.close()
            except BaseException as exc:
                errors.append(exc)
            else:
                self._closed_facade_ids.add(facade_id)
        account_id = id(self._account_client)
        if account_id not in self._closed_facade_ids:
            try:
                self._account_client.close()
            except BaseException as exc:
                errors.append(exc)
            else:
                self._closed_facade_ids.add(account_id)
        if errors:
            self._connected = True
            raise errors[0]
        self._connected = False
        self._closed = True
        self._closing = False


__all__ = ("ServiceReadLifecycle",)
