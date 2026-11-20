"""Process-local, single-consumer SDK event batch handoff.

The ingress queue keeps the historical ``queue.Queue`` producer/consumer API
when no event consumer is registered.  An opt-in event consumer owns the one
destructive read lane and receives an immutable batch lease that remains the
current lease until the exact batch object is acknowledged.

This is deliberately an in-process handoff.  It is not a durable journal and
does not provide crash recovery or exactly-once business effects.
"""

from __future__ import annotations

import copy
import queue
import threading
import time
import uuid
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from typing import Any


class EventBatchHandoffError(RuntimeError):
    """A fail-closed event-consumer or batch-acknowledgement error."""

    def __init__(self, code: str) -> None:
        self.code = code
        super().__init__(code)


@dataclass(frozen=True, slots=True)
class EventIngressItem:
    """One raw item as it entered a specific SDK queue epoch."""

    event_epoch: str
    sequence: int
    item: Any


@dataclass(frozen=True, slots=True)
class PendingEventInput:
    """A retained raw/expanded input, including one-shot ingress preparation."""

    item: Any
    ingress: EventIngressItem | None = None
    sealed_row: dict[str, Any] | None = None
    prepared: bool = False


class EventIngressQueue(queue.Queue[Any]):
    """A ``queue.Queue`` that tags producer ingress and can be exclusively leased.

    Ordinary ``put``/``get`` calls keep their payload shape.  After a batch
    consumer claims the queue, ordinary destructive reads fail immediately;
    only the owning ``BtApi`` batch path can drain it through the private
    token-checked consumer method.
    Existing queued items are retained and replayed in FIFO order to the first
    consumer.
    """

    def __init__(self, maxsize: int = 0) -> None:
        super().__init__(maxsize=maxsize)
        self.event_epoch = uuid.uuid4().hex
        self._ingress_sequence = 0
        self._legacy_read_started = False
        self._consumer_token: object | None = None
        self._consumer_retired = False
        self._producer_reservations = 0

    def _put(self, item: Any) -> None:
        if self._consumer_retired:
            raise EventBatchHandoffError("event_ingress_retired")
        self._ingress_sequence += 1
        self.queue.append(EventIngressItem(self.event_epoch, self._ingress_sequence, item))

    def _get(self) -> Any:
        queued = self.queue.popleft()
        return queued.item if type(queued) is EventIngressItem else queued

    @property
    def last_ingress_sequence(self) -> int:
        with self.mutex:
            return self._ingress_sequence

    @property
    def legacy_read_started(self) -> bool:
        with self.mutex:
            return self._legacy_read_started

    @property
    def consumer_claimed(self) -> bool:
        with self.mutex:
            return self._consumer_token is not None or self._consumer_retired

    def _claim_consumer(self) -> object:
        with self.mutex:
            if self._consumer_retired:
                raise EventBatchHandoffError("event_consumer_retired")
            if self._consumer_token is not None:
                raise EventBatchHandoffError("event_consumer_already_claimed")
            if self._legacy_read_started:
                raise EventBatchHandoffError("event_consumer_late_registration")
            token = object()
            self._consumer_token = token
            self.not_empty.notify_all()
            return token

    def get(self, block: bool = True, timeout: float | None = None) -> Any:
        """Preserve Queue semantics, but prohibit reads around an active lease."""

        if not block:
            if timeout is not None:
                raise ValueError("'timeout' must not be specified for non-blocking get")
            with self.not_empty:
                self._legacy_read_started = True
                self._require_legacy_read_allowed()
                if not self._qsize():
                    raise queue.Empty
                item = self._get()
                self.not_full.notify()
                return item

        if timeout is None:
            with self.not_empty:
                self._legacy_read_started = True
                self._require_legacy_read_allowed()
                while not self._qsize():
                    self.not_empty.wait()
                    self._require_legacy_read_allowed()
                item = self._get()
                self.not_full.notify()
                return item

        if timeout < 0:
            raise ValueError("'timeout' must be a non-negative number")
        with self.not_empty:
            self._legacy_read_started = True
            self._require_legacy_read_allowed()
            if not self._qsize():
                if timeout == 0:
                    raise queue.Empty
                deadline = time.monotonic() + timeout
                while not self._qsize():
                    remaining = deadline - time.monotonic()
                    if remaining <= 0:
                        raise queue.Empty
                    self.not_empty.wait(remaining)
                    self._require_legacy_read_allowed()
            item = self._get()
            self.not_full.notify()
            return item

    def _require_legacy_read_allowed(self) -> None:
        if self._consumer_token is not None or self._consumer_retired:
            raise EventBatchHandoffError("event_batch_consumer_owns_queue")

    def _get_for_consumer(self, token: object) -> EventIngressItem:
        """Remove one ingress item for the exact event consumer."""

        with self.not_empty:
            if token is not self._consumer_token or self._consumer_retired:
                raise EventBatchHandoffError("event_consumer_token_invalid")
            if not self._qsize():
                raise queue.Empty
            queued = self.queue.popleft()
            self.not_full.notify()
        if type(queued) is not EventIngressItem:
            # The queue is created empty and wraps every subsequent put.  This
            # branch is defensive for a caller that mutated Queue.queue.
            raise EventBatchHandoffError("event_ingress_item_invalid")
        return queued

    @contextmanager
    def _producer_reservation(self) -> Iterator[None]:
        """Fence side-effecting facade producers against concurrent retirement."""

        with self.mutex:
            if self._consumer_retired:
                raise EventBatchHandoffError("event_ingress_retired")
            self._producer_reservations += 1
        try:
            yield
        finally:
            with self.mutex:
                self._producer_reservations -= 1

    def _retire_consumer(
        self,
        token: object,
        *,
        pending_is_empty: Callable[[], bool],
    ) -> None:
        """Atomically retire only when all producer and consumer lanes are empty."""

        with self.mutex:
            if token is not self._consumer_token or self._consumer_retired:
                raise EventBatchHandoffError("event_consumer_token_invalid")
            if self._qsize() or self._producer_reservations or pending_is_empty() is not True:
                raise EventBatchHandoffError("event_consumer_backlog_pending")
            self._consumer_token = None
            self._consumer_retired = True


@dataclass(frozen=True, slots=True)
class EventBatch:
    """One exact, process-local SDK→consumer handoff batch."""

    exchange_name: str
    event_epoch: str
    batch_sequence: int
    batch_id: str
    _events: tuple[dict[str, Any], ...]
    blocked_error_code: str | None = None

    @property
    def events(self) -> tuple[dict[str, Any], ...]:
        """Return detached event dictionaries; callers cannot mutate the lease."""

        return tuple(copy.deepcopy(event) for event in self._events)


class EventBatchHandoff:
    """Own one exchange's active token and exact outstanding batch."""

    def __init__(self, exchange_name: str, ingress: EventIngressQueue) -> None:
        self.exchange_name = exchange_name
        self.ingress = ingress
        self.token = ingress._claim_consumer()
        self.event_epoch = ingress.event_epoch
        self.next_batch_sequence = 1
        self.outstanding: EventBatch | None = None
        self.retired = False
        self._lock = threading.RLock()

    @contextmanager
    def polling(self, token: object) -> Iterator[EventBatch | None]:
        """Serialize consumers and acknowledgement around one poll operation."""

        with self._lock:
            if self.retired:
                raise EventBatchHandoffError("event_consumer_retired")
            if token is not self.token:
                raise EventBatchHandoffError("event_consumer_token_invalid")
            yield self.outstanding

    def lease(
        self,
        events: list[dict[str, Any]],
        *,
        blocked_error_code: str | None = None,
    ) -> EventBatch:
        with self._lock:
            if self.retired:
                raise EventBatchHandoffError("event_consumer_retired")
            if self.outstanding is not None:
                return self.outstanding
            batch = EventBatch(
                exchange_name=self.exchange_name,
                event_epoch=self.event_epoch,
                batch_sequence=self.next_batch_sequence,
                batch_id=uuid.uuid4().hex,
                # These normalized dictionaries have not escaped yet. Retain
                # their references so an unusual uncopyable provider value
                # cannot strand an already-consumed ingress item at lease time.
                _events=tuple(events),
                blocked_error_code=blocked_error_code,
            )
            self.next_batch_sequence += 1
            self.outstanding = batch
            return batch

    def acknowledge(self, token: object, batch: EventBatch) -> None:
        with self._lock:
            if self.retired:
                raise EventBatchHandoffError("event_consumer_retired")
            if token is not self.token:
                raise EventBatchHandoffError("event_consumer_token_invalid")
            if type(batch) is not EventBatch or batch is not self.outstanding:
                raise EventBatchHandoffError("event_batch_ack_not_current")
            if (
                batch.exchange_name != self.exchange_name
                or batch.event_epoch != self.event_epoch
                or batch.batch_sequence != self.next_batch_sequence - 1
            ):
                raise EventBatchHandoffError("event_batch_ack_binding_mismatch")
            self.outstanding = None

    def _append_pending(self, pending: Any, item: Any) -> None:
        """Serialize normalized-input producers with polling and retirement."""

        with self._lock:
            if self.retired:
                raise EventBatchHandoffError("event_ingress_retired")
            pending.append(item)

    def retire(
        self,
        token: object,
        *,
        pending_is_empty: Callable[[], bool],
    ) -> None:
        with self._lock:
            if self.retired:
                raise EventBatchHandoffError("event_consumer_retired")
            if token is not self.token:
                raise EventBatchHandoffError("event_consumer_token_invalid")
            if self.outstanding is not None:
                raise EventBatchHandoffError("event_batch_ack_pending")
            self.ingress._retire_consumer(token, pending_is_empty=pending_is_empty)
            self.retired = True
