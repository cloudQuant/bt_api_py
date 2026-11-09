"""Explicit Gateway strategy facade over a caller-supplied session.

Returned clients retain their own runtime and ACL checks. Runtime health is not
account readiness or a grant to trade.
"""

from __future__ import annotations

import os
import threading
from typing import TYPE_CHECKING

from bt_api_py.gateway_strategy_client import GatewayStrategyClientSession
from bt_api_py.service_runtime_thread import _timeout

if TYPE_CHECKING:
    from bt_api_py._contracts import OrderRequest

_NO_ROUTE = object()


class GatewayBtApi:
    """Expose selected Gateway operations without constructing a provider."""

    def __init__(self, session, *, exchange_name: str, owns_session: bool = False):
        if type(session) is not GatewayStrategyClientSession:
            raise TypeError("INVALID_GATEWAY_SESSION")
        if (
            type(exchange_name) is not str
            or not exchange_name
            or exchange_name.strip() != exchange_name
        ):
            raise ValueError("INVALID_EXCHANGE_NAME")
        if type(owns_session) is not bool:
            raise TypeError("INVALID_SESSION_OWNERSHIP")
        self._session = session
        self._exchange_name = exchange_name
        self._owns_session = owns_session
        self._pid = os.getpid()
        self._lock = threading.Lock()
        self._state = "open"
        self._binding_object, self._route_object = session._binding, session._route
        self._binding, self._route = session.binding, session.route
        self._runtime = session.runtime
        self._command = session.command_client
        self._stream = session.execution_stream_client
        self._host, self._cancel_host = self._command._host, self._command._cancel_host
        self._account = session.account_client
        self._market = session.market_client
        self._instrument = session.instruments
        self._subscriptions = dict(self._market._clients) if self._market is not None else {}
        self._check_binding()

    def _check_process(self):
        if os.getpid() != self._pid:
            raise RuntimeError("WRONG_PROCESS")

    def _check_binding(self):
        self._check_process()
        session = self._session
        if (
            session._binding is not self._binding_object
            or session._route is not self._route_object
            or session.binding != self._binding
            or session.route != self._route
            or session.runtime is not self._runtime
            or session.command_client is not self._command
            or session.execution_stream_client is not self._stream
            or session.account_client is not self._account
            or session.market_client is not self._market
            or session.instruments is not self._instrument
        ):
            raise RuntimeError("GATEWAY_BINDING_CHANGED")
        for client in (self._command, self._stream):
            if (
                client._runtime is not self._runtime
                or client._binding is not self._binding_object
                or client._route is not self._route_object
                or client._binding != self._binding
                or client._route != self._route
            ):
                raise RuntimeError("GATEWAY_BINDING_CHANGED")
        if (
            self._command._host is not self._host
            or self._command._cancel_host is not self._cancel_host
        ):
            raise RuntimeError("GATEWAY_BINDING_CHANGED")
        for client in (self._account, self._instrument, *self._subscriptions.values()):
            if client is not None and client._runtime is not self._runtime:
                raise RuntimeError("GATEWAY_BINDING_CHANGED")
        if any(client._name != name for name, client in self._subscriptions.items()):
            raise RuntimeError("GATEWAY_BINDING_CHANGED")
        if self._market is not None and (
            self._market._clients.keys() != self._subscriptions.keys()
            or any(
                self._market._clients[name] is not client
                for name, client in self._subscriptions.items()
            )
        ):
            raise RuntimeError("GATEWAY_BINDING_CHANGED")

    def _check(self, exchange_name=_NO_ROUTE, *, allow_closing=False, allow_closed=False):
        self._check_binding()
        if exchange_name is not _NO_ROUTE and (
            type(exchange_name) is not str or exchange_name != self._exchange_name
        ):
            raise ValueError("GATEWAY_ROUTE_MISMATCH")
        with self._lock:
            if self._state == "closed" and not allow_closed:
                raise RuntimeError("GATEWAY_FACADE_CLOSED")
            if self._state == "closing" and not allow_closing:
                raise RuntimeError("GATEWAY_FACADE_CLOSING")

    def list_exchanges(self):
        self._check()
        return [self._exchange_name]

    def start(self, *, timeout_seconds=5.0):
        self._check()
        return self._session.start(timeout_seconds=timeout_seconds)

    def status(self):
        """Return the original runtime snapshot, including cleanup uncertainty."""
        self._check_process()
        return self._runtime.status()

    @property
    def lifecycle_state(self):
        """Report facade state separately from the session runtime snapshot."""
        self._check_process()
        with self._lock:
            return self._state

    def close(self, *, timeout_seconds=5.0):
        """Close this facade; a borrowed session and its host remain open.

        Return the unmodified runtime snapshot. Runtime closure does not imply
        clean unregister, account readiness, or permission to trade.
        """
        self._check_process()
        _timeout(timeout_seconds)
        with self._lock:
            already_closed = self._state == "closed"
            if not already_closed:
                self._state = "closing" if self._owns_session else "closed"
        if already_closed or not self._owns_session:
            return self._runtime.status()
        # No facade lock is held while the original runtime waits for cleanup.
        result = self._runtime.close(timeout_seconds=timeout_seconds)
        if result.state == "closed" and not result.thread_alive:
            with self._lock:
                self._state = "closed"
        return result

    def reserve_order(
        self, exchange_name, request: OrderRequest, *, ttl_seconds=10.0, timeout_seconds=5.0
    ):
        self._check(exchange_name)
        return self._command.reserve(
            request, ttl_seconds=ttl_seconds, timeout_seconds=timeout_seconds
        )

    def dispatch_order(self, exchange_name, request_key, *, timeout_seconds=5.0):
        self._check(exchange_name)
        return self._command.dispatch(request_key, timeout_seconds=timeout_seconds)

    def command_status(self, exchange_name, request_key, *, timeout_seconds=5.0):
        self._check(exchange_name)
        return self._command.status(request_key, timeout_seconds=timeout_seconds)

    def release_order(self, exchange_name, request_key, *, operation_id, timeout_seconds=5.0):
        self._check(exchange_name)
        return self._command.release(
            request_key, operation_id=operation_id, timeout_seconds=timeout_seconds
        )

    def cancel_order(self, exchange_name, request_key, *, operation_id, timeout_seconds=5.0):
        self._check(exchange_name)
        return self._command.cancel(
            request_key, operation_id=operation_id, timeout_seconds=timeout_seconds
        )

    def cancel_status(self, exchange_name, operation_id, *, timeout_seconds=5.0):
        self._check(exchange_name)
        return self._command.cancel_status(operation_id, timeout_seconds=timeout_seconds)

    def gateway_execution_stream(self, exchange_name):
        self._check(exchange_name)
        return self._stream

    def account_read(self, exchange_name):
        self._check(exchange_name)
        if self._account is None:
            raise RuntimeError("ACCOUNT_CLIENT_UNAVAILABLE")
        return self._account

    def market_subscription(self, exchange_name, name):
        """Select a configured name; the route label is not source proof."""
        self._check(exchange_name)
        if type(name) is not str or name not in self._subscriptions:
            raise RuntimeError("MARKET_SUBSCRIPTION_UNAVAILABLE")
        return self._subscriptions[name]

    def get_instrument_spec(self, exchange_name, symbol, *, timeout_seconds=5.0):
        self._check(exchange_name)
        if self._instrument is None:
            raise RuntimeError("INSTRUMENT_CLIENT_UNAVAILABLE")
        return self._session.get_instrument_spec(
            exchange_name, symbol, timeout_seconds=timeout_seconds
        )
