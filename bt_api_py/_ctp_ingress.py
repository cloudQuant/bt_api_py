"""SDK-owned CTP ingress fences and protected market queue views."""

from __future__ import annotations

import queue
import threading
from typing import Any

from ._contracts.errors import CapabilityNotSupportedError

class _CtpPrivateIngressFence:
    """Linearization point shared by CTP private producers and recovery gates."""

    def __init__(self) -> None:
        self.lock = threading.RLock()
        self.epoch = 0
        self.pending = 0
        self.active_writes = 0
        self.revocation_pending = False
        self.revocation_worker_active = False
        self.revocation_teardown_required = False


class _CtpPrivateIngressQueue:
    """Write-only account-stream proxy which fences every private producer put."""

    def __init__(
        self,
        target: queue.Queue[Any],
        fence: _CtpPrivateIngressFence,
        record_ingress: Any,
        revoke_arm: Any,
    ) -> None:
        self.target = target
        self.fence = fence
        self._record_ingress = record_ingress
        self._revoke_arm = revoke_arm

    def _publish(self, item: Any, publisher: Any) -> None:
        # Publish the epoch before waiting on the session mutex.  An arm which
        # currently owns that mutex can still observe this producer attempt at
        # its final fence and fail closed.
        should_revoke = False
        published = False
        try:
            with self.fence.lock:
                self.fence.epoch += 1
                self.fence.pending += 1
                published = True
                ordered_after_write = self.fence.active_writes > 0
                publisher(item)
            should_revoke = self._record_ingress(ordered_after_write) is True
            if should_revoke:
                self._revoke_arm()
        finally:
            with self.fence.lock:
                if published:
                    self.fence.pending -= 1

    def put(self, item: Any, block: bool = True, timeout: float | None = None) -> None:
        def publish(value: Any) -> None:
            if timeout is None:
                self.target.put(value, block=block)
            else:
                self.target.put(value, block=block, timeout=timeout)

        self._publish(item, publish)

    def append_normalized(self, item: Any, target: Any) -> None:
        self._publish(item, target.append)

    def put_nowait(self, item: Any) -> None:
        self.put(item, block=False)

    def qsize(self) -> int:
        return self.target.qsize()

    def empty(self) -> bool:
        return self.target.empty()

    def full(self) -> bool:
        return self.target.full()


class _CtpMarketIngressEnvelope:
    """One-shot producer proof attached only by the managed market stream."""

    __slots__ = ("_seal", "_item", "_row", "_consumed", "_lock")

    def __init__(self, seal: object, item: Any, row: dict[str, Any] | None) -> None:
        self._seal = seal
        self._item = item
        self._row = row
        self._consumed = False
        self._lock = threading.Lock()

    def consume(self, seal: object) -> tuple[Any, dict[str, Any] | None] | None:
        with self._lock:
            if self._seal is not seal or self._consumed:
                return None
            self._consumed = True
            return self._item, self._row


class _CtpMarketIngressQueue:
    """Private stream queue which seals source-native V2 tick snapshots."""

    def __init__(self, target: queue.Queue[Any], seal_item: Any) -> None:
        self.target = target
        self._seal_item = seal_item

    def put(self, item: Any, block: bool = True, timeout: float | None = None) -> None:
        sealed = self._seal_item(item)
        if timeout is None:
            self.target.put(sealed, block=block)
        else:
            self.target.put(sealed, block=block, timeout=timeout)

    def put_nowait(self, item: Any) -> None:
        self.put(item, block=False)

    def qsize(self) -> int:
        return self.target.qsize()

    def empty(self) -> bool:
        return self.target.empty()

    def full(self) -> bool:
        return self.target.full()


class _CtpMarketConsumerQueue:
    """Public CTP queue view: consumers can read, but cannot forge ingress."""

    def __init__(self, target: queue.Queue[Any], seal: object) -> None:
        self.target = target
        self._seal = seal

    def _consume(self, getter: Any) -> Any:
        while True:
            item = getter()
            if type(item) is not _CtpMarketIngressEnvelope:
                return item
            consumed = item.consume(self._seal)
            if consumed is not None:
                return consumed[0]

    def get(self, block: bool = True, timeout: float | None = None) -> Any:
        if timeout is None:
            return self._consume(lambda: self.target.get(block=block))
        return self._consume(lambda: self.target.get(block=block, timeout=timeout))

    def get_nowait(self) -> Any:
        return self.get(block=False)

    def put(self, _item: Any, *args: Any, **kwargs: Any) -> None:
        del args, kwargs
        raise CapabilityNotSupportedError(
            "put_ticker",
            detail="CTP market ingress is managed by the native market stream",
            definite_reject=True,
        )

    def put_nowait(self, item: Any) -> None:
        self.put(item)

    def qsize(self) -> int:
        return self.target.qsize()

    def empty(self) -> bool:
        return self.target.empty()

    def full(self) -> bool:
        return self.target.full()

    def task_done(self) -> None:
        self.target.task_done()

    def join(self) -> None:
        self.target.join()


