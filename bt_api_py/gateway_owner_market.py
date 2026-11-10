"""Borrow a market provider exclusively on an existing Gateway native owner.

Logical handles belong to the Hub thread; native handles never leave owner callbacks.
A pending native operation is retained across local wait timeouts.
"""

from __future__ import annotations

import math
import os
import threading
import time
import uuid
import weakref
from collections.abc import Callable
from dataclasses import dataclass
from types import MappingProxyType
from typing import Any

from bt_api_base.market_protocol import (
    MarketProviderEvent,
    MarketSourceDescriptor,
    MarketSubscriptionKey,
)
from bt_api_base.service_protocol import _freeze_json_value

from bt_api_py.gateway_direct_owner import GatewayDirectOwner, GatewayOwnerServiceCallError
from bt_api_py.service_runtime_thread import _copy_owner_json

_TIMEOUTS = frozenset({"SERVICE_CALL_TIMEOUT_QUEUED", "SERVICE_CALL_TIMEOUT_RUNNING"})
_NOT_STARTED = frozenset({"SERVICE_CALL_CANCELLED", "SERVICE_CALL_EXPIRED", "SERVICE_CALL_CLOSED"})
# The native provider itself refused the acquisition and declared that it holds
# no handle for it (see ``begin_subscribe``). Like a call that never started,
# such an acquisition has nothing to release.
_REFUSED = "MARKET_SUBSCRIBE_REFUSED"
_NO_NATIVE_HANDLE = _NOT_STARTED | {_REFUSED}
_MAX_EVENTS = 1000


class GatewayOwnerMarketError(RuntimeError):
    """Finite local proxy diagnostic; never contains provider data."""

    def __init__(self, code: str):
        self.code = code
        super().__init__(code)


class _LogicalHandle:
    __slots__ = ("__weakref__",)


@dataclass
class _State:
    key: MarketSubscriptionKey
    generation: str
    token: str
    pending: Any = None
    pending_kind: str | None = None
    timed_out: bool = False
    acquired: bool = False
    uncertain: bool = False
    not_started: bool = False
    subscribe_error: str | None = None
    failed: bool = False
    result_gap: bool = False
    batch: tuple[MarketProviderEvent, ...] | None = None
    ack_epoch: str | None = None
    ready_permit: bool = False
    ready_until: float = 0.0
    describe_epoch: str | None = None
    describe_probe: bool = False
    describe_slot: tuple[str, Any, float, str | None] | None = None


def _text(value: Any) -> bool:
    if type(value) is not str or not value or len(value) > 256 or value != value.strip():
        return False
    try:
        return len(value.encode("utf-8")) <= 256
    except UnicodeError:
        return False


def _plain(value: Any) -> Any:
    remaining = [100_000]
    active: set[int] = set()

    def copy(item: Any, depth: int) -> Any:
        remaining[0] -= 1
        if remaining[0] < 0 or depth > 32:
            raise ValueError
        kind = type(item)
        if item is None or kind in (bool, int):
            return item
        if kind is float:
            if not math.isfinite(item):
                raise ValueError
            return item
        if kind is str:
            if len(item) > 8 * 1024 * 1024:
                raise ValueError
            return item
        if kind not in (dict, MappingProxyType, list, tuple) or id(item) in active:
            raise ValueError
        active.add(id(item))
        try:
            if len(item) > remaining[0]:
                raise ValueError
            if kind in (list, tuple):
                return [copy(child, depth + 1) for child in item]
            if any(type(key) is not str for key in item):
                raise ValueError
            return {key: copy(child, depth + 1) for key, child in item.items()}
        finally:
            active.remove(id(item))

    return _copy_owner_json(copy(value, 0), error_code="INVALID_OWNER_RESULT")


class GatewayOwnerMarketProvider:
    """Hub-facing provider; it never owns or closes the borrowed native provider."""

    def __init__(
        self,
        owner: GatewayDirectOwner,
        provider_getter: Callable[[], Any],
        *,
        clock_source_getter: Callable[[], Any] | None = None,
        call_timeout_seconds: float = 5.0,
        max_handles: int = 1024,
    ):
        if type(owner) is not GatewayDirectOwner or not callable(provider_getter):
            raise ValueError("INVALID_OWNER_MARKET_CONFIGURATION")
        if clock_source_getter is not None and not callable(clock_source_getter):
            raise ValueError("INVALID_OWNER_MARKET_CONFIGURATION")
        if (
            type(call_timeout_seconds) not in (int, float)
            or not math.isfinite(call_timeout_seconds)
            or not 0 < call_timeout_seconds <= 3600
        ):
            raise ValueError("INVALID_OWNER_MARKET_CONFIGURATION")
        if type(max_handles) is not int or not 1 <= max_handles <= 65536:
            raise ValueError("INVALID_OWNER_MARKET_CONFIGURATION")
        self._owner = owner
        self._getter = provider_getter
        self._clock_getter = clock_source_getter
        self._timeout = float(call_timeout_seconds)
        self._max_handles = max_handles
        self._pid = os.getpid()
        self._thread = threading.get_ident()
        self._closed = False
        self._states: dict[_LogicalHandle, _State] = {}
        self._retired: weakref.WeakKeyDictionary[_LogicalHandle, MarketSubscriptionKey] = (
            weakref.WeakKeyDictionary()
        )
        self._gap_keys: set[MarketSubscriptionKey] = set()
        # Only native owner callbacks access these two fields.
        self._native_provider = None
        self._native_handles: dict[str, Any] = {}
        self._owner.claim_service_port("market", self)

    def _check(self, *, closing: bool = False) -> None:
        if os.getpid() != self._pid:
            raise GatewayOwnerMarketError("PROCESS_BOUNDARY")
        if threading.get_ident() != self._thread:
            raise GatewayOwnerMarketError("OWNER_THREAD_REQUIRED")
        if self._closed and not closing:
            raise GatewayOwnerMarketError("PROVIDER_CLOSED")

    def _state(self, handle: object) -> _State:
        if type(handle) is not _LogicalHandle or handle not in self._states:
            raise GatewayOwnerMarketError("INVALID_MARKET_HANDLE")
        return self._states[handle]

    def _retire(self, handle: _LogicalHandle) -> None:
        state = self._states.pop(handle)
        self._retired[handle] = state.key

    def _native(self) -> Any:
        current = self._getter()
        if current is None:
            raise GatewayOwnerServiceCallError("MARKET_PROVIDER_UNAVAILABLE")
        if self._native_provider is None:
            self._native_provider = current
        elif current is not self._native_provider:
            raise GatewayOwnerServiceCallError("MARKET_PROVIDER_CHANGED")
        return current

    def _enqueue(self, state: _State, kind: str, callback: Callable[[], Any]) -> None:
        if state.pending is not None:
            raise GatewayOwnerMarketError("MARKET_OPERATION_PENDING")
        try:
            future = self._owner.submit_service_call(callback)
        except GatewayOwnerServiceCallError as error:
            raise GatewayOwnerMarketError(error.code) from None
        state.pending, state.pending_kind, state.timed_out = future, kind, False

    def _settle(self, state: _State, *, wait: float) -> tuple[str, Any, bool]:
        kind, future, was_timeout = state.pending_kind, state.pending, state.timed_out
        if future is None or kind is None:
            raise GatewayOwnerMarketError("INVALID_MARKET_STATE")
        try:
            result = future.result(timeout_seconds=wait)
        except GatewayOwnerServiceCallError as error:
            if error.code in _TIMEOUTS:
                if wait > 0:
                    state.timed_out = True
                raise GatewayOwnerMarketError("MARKET_OPERATION_PENDING") from None
            state.pending = state.pending_kind = None
            state.timed_out = False
            if kind == "subscribe":
                state.uncertain = error.code not in _NO_NATIVE_HANDLE
                state.not_started = error.code in _NO_NATIVE_HANDLE
                state.subscribe_error = error.code
            elif kind == "describe":
                if state.describe_probe:
                    state.describe_slot = (state.describe_epoch, None, time.monotonic(), error.code)
                    state.describe_epoch = None
                    state.describe_probe = False
                    return kind, (None, time.monotonic(), error.code), was_timeout
                state.describe_epoch = None
                state.describe_probe = False
            elif kind in {"poll", "ack"} and error.code not in _NOT_STARTED:
                state.failed = True
                if kind == "poll" and error.code == "LOCAL_RESULT_GAP":
                    state.result_gap = True
                    self._gap_keys.add(state.key)
            raise GatewayOwnerMarketError(error.code) from None
        state.pending = state.pending_kind = None
        state.timed_out = False
        if kind == "subscribe":
            if result is not True:
                state.uncertain = True
                raise GatewayOwnerMarketError("MARKET_SUBSCRIPTION_UNCERTAIN")
            state.acquired = True
        elif kind == "poll":
            state.batch = result
        elif kind == "describe":
            if state.describe_probe:
                descriptor, completed, error_code = result
                state.describe_slot = (state.describe_epoch, descriptor, completed, error_code)
            state.describe_epoch = None
            state.describe_probe = False
        elif kind == "reset" and result[0] is True:
            state.batch = None
            state.describe_slot = None
        elif kind == "ack":
            if result is not True:
                state.failed = True
                raise GatewayOwnerMarketError("MARKET_ACK_FAILED")
            state.ack_epoch = None
        return kind, result, was_timeout

    def _settle_before(self, state: _State, *, wait: float) -> None:
        if state.pending is not None:
            was_probe = state.pending_kind == "describe" and state.describe_probe
            kind, result, _ = self._settle(state, wait=wait)
            if kind == "describe" and not was_probe and result[2] is not None:
                raise GatewayOwnerMarketError(result[2])
        if state.uncertain:
            raise GatewayOwnerMarketError("MARKET_SUBSCRIPTION_UNCERTAIN")
        if not state.acquired:
            raise GatewayOwnerMarketError("MARKET_SUBSCRIPTION_UNAVAILABLE")
        if state.result_gap:
            raise GatewayOwnerMarketError("LOCAL_RESULT_GAP")
        if state.failed:
            raise GatewayOwnerMarketError("MARKET_HANDLE_FAILED")
        if state.ack_epoch is not None:
            self._enqueue_ack(state, state.ack_epoch)
            self._settle(state, wait=wait)

    def _enqueue_ack(self, state: _State, epoch: str) -> None:
        def acknowledge() -> bool:
            provider = self._native()
            result = provider.acknowledge_source_epoch_reset(
                self._native_handles[state.token], epoch=epoch
            )
            if type(result) is not bool:
                raise GatewayOwnerServiceCallError("INVALID_SOURCE_RESET")
            return result

        self._enqueue(state, "ack", acknowledge)

    def subscribe(self, key: MarketSubscriptionKey, *, generation: str) -> object:
        handle = self.begin_subscribe(key, generation=generation)
        state = self._states[handle]
        try:
            self._settle(state, wait=self._timeout)
        except GatewayOwnerMarketError as error:
            if error.code == "MARKET_OPERATION_PENDING":
                return handle
            if not state.uncertain:
                del self._states[handle]
            raise
        return handle

    def begin_subscribe(self, key: MarketSubscriptionKey, *, generation: str) -> object:
        """Queue exactly one acquisition, retaining its handle without waiting."""
        self._check()
        if type(key) is not MarketSubscriptionKey or not _text(generation):
            raise GatewayOwnerMarketError("INVALID_MARKET_REQUEST")
        key_copy = MarketSubscriptionKey.from_dict(key.to_dict())
        if key_copy in self._gap_keys:
            raise GatewayOwnerMarketError("LOCAL_RESULT_GAP")
        represented = {state.key for state in self._states.values()}
        if key_copy in represented:
            raise GatewayOwnerMarketError("MARKET_KEY_ALREADY_ACTIVE")
        represented.update(self._retired.values())
        used = len(self._states) + len(self._retired) + len(self._gap_keys - represented)
        if used >= self._max_handles:
            raise GatewayOwnerMarketError("MARKET_HANDLE_CAPACITY")
        handle, token = _LogicalHandle(), uuid.uuid4().hex
        state = _State(key_copy, generation, token)

        def acquire() -> bool:
            provider = self._native()
            try:
                native_handle = provider.subscribe(key_copy, generation=generation)
            except Exception as error:
                # Any other failure leaves the native outcome unknown: the
                # handle is then retained until the owner's cleanup, and the
                # Hub stays not ready. A provider that marks its own refusal
                # (``subscription_refused is True``) states that it holds no
                # handle for the key, so only this acquisition ends. Without
                # this, one refused subscribe (a symbol that could not be
                # added again, a subscribe during a source recovery) left the
                # Gateway answering MARKET_NOT_READY to every strategy until
                # it was restarted (SimNow 2026-11-10).
                if getattr(error, "subscription_refused", None) is True:
                    raise GatewayOwnerServiceCallError(_REFUSED) from None
                raise
            if native_handle is None:
                raise GatewayOwnerServiceCallError("MARKET_SUBSCRIPTION_UNCERTAIN")
            self._native_handles[token] = native_handle
            return True

        self._enqueue(state, "subscribe", acquire)
        self._states[handle] = state
        return handle

    def subscription_ready(self, handle: object) -> bool:
        """Nonblocking observation of the original queued acquisition."""
        self._check()
        state = self._state(handle)
        if state.pending is not None and state.pending_kind == "subscribe":
            try:
                self._settle(state, wait=0)
            except GatewayOwnerMarketError as error:
                if error.code == "MARKET_OPERATION_PENDING":
                    return False
                raise
        if state.subscribe_error is not None:
            raise GatewayOwnerMarketError(state.subscribe_error)
        if state.uncertain:
            raise GatewayOwnerMarketError("MARKET_SUBSCRIPTION_UNCERTAIN")
        return state.acquired

    def unsubscribe(self, handle: object) -> bool:
        self._check(closing=True)
        return self._unsubscribe(handle, deadline=None)

    def _unsubscribe(self, handle: object, *, deadline: float | None) -> bool:
        if type(handle) is _LogicalHandle and handle in self._retired:
            return True
        state = self._state(handle)
        state.ready_permit = False
        if self._owner.closed:
            self._retire(handle)
            return True
        if state.not_started:
            self._retire(handle)
            return True
        if (
            state.pending is not None
            and state.pending_kind == "subscribe"
            and state.pending.cancel()
        ):
            state.pending = state.pending_kind = None
            self._retire(handle)
            return True
        if state.pending is not None:
            pending_kind = state.pending_kind
            try:
                wait = 0 if deadline is None else max(0.0, deadline - time.monotonic())
                kind, result, _ = self._settle(state, wait=wait)
            except GatewayOwnerMarketError as error:
                if pending_kind == "subscribe" and error.code in _NO_NATIVE_HANDLE:
                    self._retire(handle)
                    return True
                return False
            if kind == "unsubscribe":
                if result is True:
                    self._retire(handle)
                    return True
                return False
            if pending_kind == "subscribe" and not state.acquired:
                return False
        if state.uncertain or not state.acquired:
            return False
        if deadline is not None and time.monotonic() >= deadline:
            return False
        state.batch = None

        def release() -> bool:
            provider = self._native()
            native_handle = self._native_handles[state.token]
            result = provider.unsubscribe(native_handle)
            if result is None or result is True:
                del self._native_handles[state.token]
                return True
            return False

        try:
            self._enqueue(state, "unsubscribe", release)
            wait = 0 if deadline is None else max(0.0, deadline - time.monotonic())
            _, result, _ = self._settle(state, wait=wait)
        except GatewayOwnerMarketError:
            return False
        if result is True:
            self._retire(handle)
            return True
        return False

    def poll(self, handle: object, *, limit: int) -> tuple[MarketProviderEvent, ...]:
        self._check()
        state = self._state(handle)
        if type(limit) is not int or not 1 <= limit <= _MAX_EVENTS:
            raise GatewayOwnerMarketError("INVALID_POLL_LIMIT")
        if not state.ready_permit or time.monotonic() > state.ready_until:
            state.ready_permit = False
            raise GatewayOwnerMarketError("MARKET_SOURCE_NOT_READY")
        state.ready_permit = False
        self._settle_before(state, wait=0)
        if state.batch is not None:
            batch = state.batch
            result, state.batch = batch[:limit], batch[limit:] or None
            return result

        def drain() -> tuple[MarketProviderEvent, ...]:
            provider = self._native()
            try:
                batch = provider.poll(self._native_handles[state.token], limit=limit)
                if type(batch) is not tuple or len(batch) > limit:
                    raise ValueError
                result = []
                for event in batch:
                    if (
                        type(event) is not MarketProviderEvent
                        or event.generation != state.generation
                    ):
                        raise ValueError
                    data = _freeze_json_value(_plain(event.data), depth=0)
                    result.append(
                        MarketProviderEvent(event.generation, event.observed_at_utc, data)
                    )
                return tuple(result)
            except BaseException:
                raise GatewayOwnerServiceCallError("LOCAL_RESULT_GAP") from None

        self._enqueue(state, "poll", drain)
        return ()

    def describe_source(
        self,
        handle: object,
        *,
        key: MarketSubscriptionKey,
        generation: str,
        stream_id: str,
        epoch: str,
    ) -> MarketSourceDescriptor | None:
        self._check()
        state = self._state(handle)
        state.ready_permit = False
        self._validate_source_request(state, key, generation, stream_id, epoch)
        try:
            self._settle_before(state, wait=self._timeout)
        except GatewayOwnerMarketError as error:
            if error.code == "MARKET_OPERATION_PENDING":
                return None
            raise
        self._enqueue_describe(state, epoch)
        try:
            _, result, _ = self._settle(state, wait=self._timeout)
            if result[2] is not None:
                raise GatewayOwnerMarketError(result[2])
            return result[0]
        except GatewayOwnerMarketError as error:
            if error.code == "MARKET_OPERATION_PENDING":
                return None
            raise

    def read_market_login_clock(
        self, handle: object, *, key: MarketSubscriptionKey, generation: str,
        stream_id: str, epoch: str,
    ) -> dict[str, Any]:
        """Read an installed calibrated clock source on the native owner thread."""
        self._check()
        state = self._state(handle)
        self._validate_source_request(state, key, generation, stream_id, epoch)
        if self._clock_getter is None:
            raise GatewayOwnerMarketError("CLOCK_SOURCE_UNAVAILABLE")
        self._settle_before(state, wait=self._timeout)
        key_copy = MarketSubscriptionKey.from_dict(key.to_dict())

        def observe() -> dict[str, Any]:
            try:
                self._native()
                source = self._clock_getter()
                if source is None or not callable(getattr(source, "read", None)):
                    raise ValueError
                value = source.read(self._native_handles[state.token], key=key_copy,
                                    generation=generation, stream_id=stream_id, epoch=epoch)
                if source is not self._clock_getter():
                    raise ValueError
                result = _plain(value)
                if type(result) is not dict:
                    raise ValueError
                return result
            except Exception:
                raise GatewayOwnerServiceCallError("CLOCK_SOURCE_UNAVAILABLE") from None

        self._enqueue(state, "clock", observe)
        _, result, _ = self._settle(state, wait=self._timeout)
        return result

    def read_market_tick_clock(
        self, handle: object, *, key: MarketSubscriptionKey, generation: str,
        stream_id: str, epoch: str, provider_sequence: int, provider_subscription_epoch: int,
    ) -> tuple[dict[str, Any], int]:
        """Internal owner-to-Hub evidence; the monotonic value must never go on wire."""
        self._check()
        state = self._state(handle)
        self._validate_source_request(state, key, generation, stream_id, epoch)
        if (self._clock_getter is None or type(provider_sequence) is not int or provider_sequence <= 0
                or type(provider_subscription_epoch) is not int or provider_subscription_epoch <= 0):
            raise GatewayOwnerMarketError('CLOCK_SOURCE_UNAVAILABLE')
        self._settle_before(state, wait=self._timeout)
        key_copy = MarketSubscriptionKey.from_dict(key.to_dict())
        def observe():
            try:
                self._native()
                source = self._clock_getter()
                if source is None or not callable(getattr(source, 'read_internal', None)):
                    raise ValueError
                value, observed = source.read_internal(self._native_handles[state.token], key=key_copy,
                    generation=generation, stream_id=stream_id, epoch=epoch,
                    provider_sequence=provider_sequence, provider_subscription_epoch=provider_subscription_epoch)
                if (source is not self._clock_getter() or type(observed) is not int
                        or not 0 < observed <= time.monotonic_ns()):
                    raise ValueError
                result = _plain(value)
                if type(result) is not dict:
                    raise ValueError
                return result, observed
            except Exception:
                raise GatewayOwnerServiceCallError('CLOCK_SOURCE_UNAVAILABLE') from None
        self._enqueue(state, 'clock', observe)
        _, result, _ = self._settle(state, wait=self._timeout)
        return result

    def recheck_market_tick_on_native_owner(
        self, handle: object, *, key: MarketSubscriptionKey, generation: str,
        stream_id: str, epoch: str, provider_sequence: int, provider_subscription_epoch: int,
    ) -> tuple[dict[str, Any], int]:
        """Recheck a gate-pinned handle on the native owner; never enqueue recursively.

        The market gate must hold its mutation fence for the full call, so the
        host cannot concurrently retire or rebind this logical handle.
        """
        if (os.getpid() != self._pid or threading.current_thread() is not self._owner._thread
                or self._closed or self._clock_getter is None):
            raise GatewayOwnerMarketError('CLOCK_SOURCE_UNAVAILABLE')
        try:
            state = self._state(handle)
            self._validate_source_request(state, key, generation, stream_id, epoch)
            if (not state.acquired or state.failed or state.uncertain
                    or type(provider_sequence) is not int or provider_sequence <= 0
                    or type(provider_subscription_epoch) is not int or provider_subscription_epoch <= 0):
                raise ValueError
            self._native()
            source = self._clock_getter()
            if source is None or not callable(getattr(source, 'read_internal', None)):
                raise ValueError
            value, observed = source.read_internal(self._native_handles[state.token], key=key,
                generation=generation, stream_id=stream_id, epoch=epoch,
                provider_sequence=provider_sequence, provider_subscription_epoch=provider_subscription_epoch)
            if (source is not self._clock_getter() or type(observed) is not int
                    or not 0 < observed <= time.monotonic_ns()):
                raise ValueError
            result = _plain(value)
            if type(result) is not dict:
                raise ValueError
            return result, observed
        except Exception:
            raise GatewayOwnerMarketError('CLOCK_SOURCE_UNAVAILABLE') from None

    @staticmethod
    def _validate_source_request(
        state: _State, key: MarketSubscriptionKey, generation: str, stream_id: str, epoch: str
    ) -> None:
        if (
            type(key) is not MarketSubscriptionKey
            or key != state.key
            or generation != state.generation
            or stream_id != state.key.stream_id
            or not _text(epoch)
        ):
            raise GatewayOwnerMarketError("INVALID_MARKET_REQUEST")

    def _enqueue_describe(self, state: _State, epoch: str, *, for_probe: bool = False) -> None:
        key_copy = MarketSubscriptionKey.from_dict(state.key.to_dict())
        generation = state.generation
        stream_id = key_copy.stream_id

        def describe() -> tuple[MarketSourceDescriptor | None, float, str | None]:
            try:
                provider = self._native()
                value = provider.describe_source(
                    self._native_handles[state.token],
                    key=key_copy,
                    generation=generation,
                    stream_id=stream_id,
                    epoch=epoch,
                )
                if value is None:
                    return None, time.monotonic(), None
                if type(value) is not MarketSourceDescriptor:
                    raise GatewayOwnerServiceCallError("INVALID_SOURCE_DESCRIPTOR")
                descriptor = MarketSourceDescriptor.from_dict(_plain(value.to_dict()))
                if (
                    descriptor.key != key_copy
                    or descriptor.stream_id != stream_id
                    or descriptor.epoch != epoch
                    or descriptor.provider != key_copy.instrument.provider
                ):
                    raise GatewayOwnerServiceCallError("INVALID_SOURCE_DESCRIPTOR")
                return descriptor, time.monotonic(), None
            except GatewayOwnerServiceCallError as error:
                return None, time.monotonic(), error.code
            except BaseException:
                return None, time.monotonic(), "SERVICE_CALL_FAILED"

        self._enqueue(state, "describe", describe)
        state.describe_epoch = epoch
        state.describe_probe = for_probe

    def probe_source(
        self,
        handle: object,
        *,
        key: MarketSubscriptionKey,
        generation: str,
        stream_id: str,
        epoch: str,
    ) -> MarketSourceDescriptor | None:
        """Poll one retained descriptor call without blocking or relabelling it."""
        self._check()
        state = self._state(handle)
        state.ready_permit = False
        self._validate_source_request(state, key, generation, stream_id, epoch)
        if not self.subscription_ready(handle):
            return None
        if state.result_gap:
            raise GatewayOwnerMarketError("LOCAL_RESULT_GAP")
        if state.failed:
            raise GatewayOwnerMarketError("MARKET_HANDLE_FAILED")
        if state.ack_epoch is not None:
            return None
        if state.pending is not None:
            if state.pending_kind != "describe":
                return None
            original_epoch = state.describe_epoch
            original_probe = state.describe_probe
            try:
                _, result, was_timeout = self._settle(state, wait=0)
            except GatewayOwnerMarketError as error:
                if error.code == "MARKET_OPERATION_PENDING":
                    return None
                raise
            if not original_probe and not was_timeout:
                descriptor, completed, error_code = result
                state.describe_slot = (original_epoch, descriptor, completed, error_code)
        if state.describe_slot is not None:
            original_epoch, descriptor, completed, error_code = state.describe_slot
            state.describe_slot = None
            if original_epoch == epoch:
                if error_code is not None:
                    raise GatewayOwnerMarketError(error_code)
                if time.monotonic() - completed <= self._timeout:
                    return descriptor
        self._enqueue_describe(state, epoch, for_probe=True)
        try:
            _, result, _ = self._settle(state, wait=0)
            state.describe_slot = None
            if result[2] is not None:
                raise GatewayOwnerMarketError(result[2])
            return result[0]
        except GatewayOwnerMarketError as error:
            if error.code == "MARKET_OPERATION_PENDING":
                return None
            raise

    def source_epoch_reset_required(self, handle: object) -> bool:
        self._check()
        state = self._state(handle)
        state.ready_permit = False
        if state.pending is not None and state.pending_kind == "reset":
            _, observation, _ = self._settle(state, wait=0)
            result, ready, completed = observation
            if time.monotonic() - completed <= self._timeout:
                if not result and not ready:
                    raise GatewayOwnerMarketError("MARKET_SOURCE_UNAVAILABLE")
                state.ready_permit = not result
                state.ready_until = completed + self._timeout
                return result
        self._settle_before(state, wait=0)

        def check() -> tuple[bool, bool, float]:
            provider = self._native()
            native_handle = self._native_handles[state.token]
            result = provider.source_epoch_reset_required(native_handle)
            if type(result) is not bool:
                raise GatewayOwnerServiceCallError("INVALID_SOURCE_RESET")
            if result:
                return True, False, time.monotonic()
            ready_check = getattr(provider, "retained_events_ready", None)
            if not callable(ready_check):
                raise GatewayOwnerServiceCallError("MARKET_SOURCE_UNAVAILABLE")
            ready = ready_check(native_handle)
            if type(ready) is not bool:
                raise GatewayOwnerServiceCallError("MARKET_SOURCE_UNAVAILABLE")
            return False, ready, time.monotonic()

        self._enqueue(state, "reset", check)
        _, observation, _ = self._settle(state, wait=0)
        result, ready, completed = observation
        if not result and not ready:
            raise GatewayOwnerMarketError("MARKET_SOURCE_UNAVAILABLE")
        state.ready_permit = not result
        state.ready_until = completed + self._timeout
        return result

    def acknowledge_source_epoch_reset(self, handle: object, *, epoch: str) -> bool:
        self._check()
        state = self._state(handle)
        state.ready_permit = False
        if not _text(epoch):
            raise GatewayOwnerMarketError("INVALID_SOURCE_EPOCH")
        if state.pending is not None and state.pending_kind == "ack":
            if state.ack_epoch != epoch:
                raise GatewayOwnerMarketError("MARKET_OPERATION_PENDING")
            _, result, _ = self._settle(state, wait=0)
            return result
        if state.ack_epoch is not None:
            if state.ack_epoch != epoch:
                raise GatewayOwnerMarketError("MARKET_OPERATION_PENDING")
            self._settle_before(state, wait=0)
            return True
        self._settle_before(state, wait=0)
        state.ack_epoch = epoch
        self._enqueue_ack(state, epoch)
        _, result, _ = self._settle(state, wait=0)
        return result

    def begin_close(self) -> None:
        self._check(closing=True)
        self._closed = True

    def close(self) -> bool:
        self.begin_close()
        deadline = time.monotonic() + self._timeout
        for handle in tuple(self._states):
            self._unsubscribe(handle, deadline=deadline)
        return not self._states
