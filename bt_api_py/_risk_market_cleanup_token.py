"""Unconnected, process-local coordination for one Risk market cleanup batch.

This is neither a global fence nor proof that a resource was cleaned.  External
calls run outside the lock: an UNKNOWN can race the final check and an entered
action cannot be revoked.  Future entry points must check every client's token
identity before using this coordinator.
"""

from dataclasses import dataclass
from threading import Lock


class MarketCleanupUnknown(RuntimeError):
    """Cleanup outcome is unknown; the original batch must be retained."""


class MarketCleanupRejected(RuntimeError):
    """The requested action is not an unused registered batch action."""


@dataclass(frozen=True, eq=False)
class CleanupAction:
    """One identity-bound resource, operation and explicit result validator."""

    resource: object
    operation: object
    validate: object


@dataclass(frozen=True)
class CleanupStatus:
    state: str
    in_flight: bool
    completed: int
    result_retained: bool
    error_retained: bool
    notification_attempted: bool


_MISSING = object()
_MAX_ACTIONS = 256


class RiskMarketCleanupToken:
    """Sticky coordinator. Registration is fixed and each action runs at most once.

    ``guard`` is shared by all actions and must return exactly False before and
    after each verified action. ``on_unknown``, if supplied, receives this token
    once outside the lock; callback failures are retained without being exposed.
    Inputs are trusted in-process objects, not a security boundary.
    """

    def __init__(self, actions, guard, on_unknown=None):
        if type(actions) not in (tuple, list) or len(actions) > _MAX_ACTIONS:
            raise ValueError("INVALID_CLEANUP_CONFIGURATION")
        actions = tuple(actions)
        if not callable(guard):
            raise ValueError("INVALID_CLEANUP_CONFIGURATION")
        if on_unknown is not None and not callable(on_unknown):
            raise ValueError("INVALID_CLEANUP_CONFIGURATION")
        if any(type(a) is not CleanupAction or not callable(a.operation)
               or not callable(a.validate) for a in actions):
            raise ValueError("INVALID_CLEANUP_CONFIGURATION")
        if (len({id(a) for a in actions}) != len(actions)
                or len({(id(a.resource), id(a.operation)) for a in actions}) != len(actions)):
            raise ValueError("DUPLICATE_CLEANUP_ACTION")
        self._actions = actions
        self._guard = guard
        self._on_unknown = on_unknown
        self._lock = Lock()
        self._state = "READY"
        self._active = None
        self._completed = set()
        # One slot per fixed registration retains even an in-flight result that
        # arrives after UNKNOWN. Error slots likewise retain original objects.
        self._results = [_MISSING] * len(actions)
        self._first_error = _MISSING
        self._inflight_error = _MISSING
        self._callback_error = _MISSING
        self._notification_attempted = False

    def same_batch(self, other):
        return self is other

    def status(self):
        with self._lock:
            return CleanupStatus(
                state=self._state,
                in_flight=self._active is not None,
                completed=len(self._completed),
                result_retained=any(result is not _MISSING for result in self._results),
                error_retained=(self._first_error is not _MISSING
                                or self._inflight_error is not _MISSING
                                or self._callback_error is not _MISSING),
                notification_attempted=self._notification_attempted,
            )

    def _unknown_locked(self, error=_MISSING):
        if error is not _MISSING and self._first_error is _MISSING:
            self._first_error = error
        self._state = "UNKNOWN"
        if not self._notification_attempted:
            self._notification_attempted = True
            return self._on_unknown
        return None

    def _notify(self, callback, primary_error=_MISSING):
        if callback is None:
            return
        try:
            callback(self)
        except BaseException as error:
            with self._lock:
                self._callback_error = error
            # A non-Exception interruption already in flight takes precedence.
            # Ordinary primary errors must not hide a callback interruption.
            if (not issubclass(type(error), Exception)
                    and (primary_error is _MISSING
                         or issubclass(type(primary_error), Exception))):
                raise

    def mark_unknown(self, error=_MISSING):
        with self._lock:
            callback = self._unknown_locked(error)
        self._notify(callback)

    def _fail(self, error=_MISSING):
        with self._lock:
            if error is not _MISSING:
                self._inflight_error = error
            callback = self._unknown_locked(error)
            self._active = None
        self._notify(callback, primary_error=error)
        if error is not _MISSING and not issubclass(type(error), Exception):
            return  # The caller re-raises its active exception with bare raise.
        raise MarketCleanupUnknown("MARKET_CLEANUP_UNKNOWN") from None

    def _still_active(self, index):
        with self._lock:
            return self._state == "ACTION_IN_FLIGHT" and self._active == index

    def run(self, action):
        with self._lock:
            if self._state == "UNKNOWN":
                raise MarketCleanupUnknown("MARKET_CLEANUP_UNKNOWN")
            if self._state == "ACTION_IN_FLIGHT":
                callback = self._unknown_locked()
                index = None
            else:
                callback = None
                index = next((i for i, item in enumerate(self._actions)
                              if item is action), None)
                if index is None or index in self._completed:
                    raise MarketCleanupRejected("MARKET_CLEANUP_ACTION_REJECTED")
                self._state = "ACTION_IN_FLIGHT"
                self._active = index
        if index is None:
            self._notify(callback)
            raise MarketCleanupUnknown("MARKET_CLEANUP_UNKNOWN")

        try:
            clear_before = self._guard() is False
        except BaseException as error:
            self._fail(error)
            raise  # Preserve the original interruption and traceback.
        if not clear_before or not self._still_active(index):
            self._fail()
        try:
            result = action.operation()
        except BaseException as error:
            self._fail(error)
            raise  # Preserve the original interruption and traceback.
        with self._lock:
            self._results[index] = result
        if not self._still_active(index):
            self._fail()
        try:
            confirmed = action.validate(result) is True
        except BaseException as error:
            self._fail(error)
            raise  # Preserve the original interruption and traceback.
        if not confirmed or not self._still_active(index):
            self._fail()
        try:
            clear_after = self._guard() is False
        except BaseException as error:
            self._fail(error)
            raise  # Preserve the original interruption and traceback.
        with self._lock:
            if clear_after and self._state == "ACTION_IN_FLIGHT" and self._active == index:
                self._completed.add(index)
                self._active = None
                self._state = "READY"
                return result
        self._fail()


@dataclass(frozen=True)
class DynamicCleanupStatus:
    state: str
    in_flight: bool
    free_resources: int
    reserved_resources: int
    retained_resources: int
    used_slots: int
    notification_attempted: bool


@dataclass(frozen=True, eq=False)
class _DynamicHandle:
    slot: int
    generation: int
    _owner: object


class _DynamicSlot:
    def __init__(self, handle, member, semantic, action, validator, receiver, limit):
        self.handle = handle
        self.member = member
        self.semantic = semantic
        self.action = action
        self.validator = validator
        self.receiver = receiver
        # One reserved cell holds the return value. One emergency cell holds
        # the first publication beyond the declared bound and latches UNKNOWN.
        self.cells = [_MISSING] * (limit + 1)
        self.limit = limit
        self.published = 0
        self.capture_closed = False


_MAX_GENERATION = (1 << 63) - 1


class DynamicRiskMarketCleanupToken(RiskMarketCleanupToken):
    """Unconnected, bounded coordination for trusted dynamic producers.

    Members and semantic keys are sealed by identity at construction. A trusted
    producer must declare a proven upper bound including its return value, publish
    each partial immediately, and stop when publication raises. The extra reserved
    emergency cell retains the first producer contract violation. Arbitrary code
    that hides partials, ignores a publication failure, or lies about its bound is
    outside this contract; no bounded in-process object can retain its unseen or
    unlimited products. A receiver's exact True only confirms this local contract.
    No local check revokes an entered action or establishes remote cleanup.
    """

    def __init__(self, members, semantics, slot_capacity, resource_capacity,
                 guard, on_unknown=None):
        if (type(members) not in (tuple, list) or not members
                or type(semantics) not in (tuple, list) or not semantics
                or type(slot_capacity) is not int or not 1 <= slot_capacity <= _MAX_ACTIONS
                or type(resource_capacity) is not int or not 2 <= resource_capacity <= _MAX_ACTIONS
                or not callable(guard) or (on_unknown is not None and not callable(on_unknown))):
            raise ValueError("INVALID_CLEANUP_CONFIGURATION")
        if (len(members) > _MAX_ACTIONS or len(semantics) > _MAX_ACTIONS
                or len({id(item) for item in members}) != len(members)
                or len({id(item) for item in semantics}) != len(semantics)):
            raise ValueError("INVALID_CLEANUP_CONFIGURATION")
        self._members = tuple(members)
        self._semantics = tuple(semantics)
        self._slots = [None] * slot_capacity
        self._generations = [0] * slot_capacity
        self._handle_owner = object()
        self._late_resource = _MISSING
        self._resource_capacity = resource_capacity
        self._reserved_resources = 0
        self._guard = guard
        self._on_unknown = on_unknown
        self._lock = Lock()
        self._state = "READY"
        self._active = None
        self._first_error = _MISSING
        self._inflight_error = _MISSING
        self._callback_error = _MISSING
        self._notification_attempted = False

    def status(self):
        with self._lock:
            retained = sum(sum(cell is not _MISSING for cell in slot.cells)
                           for slot in self._slots if slot is not None)
            retained += self._late_resource is not _MISSING
            return DynamicCleanupStatus(
                state=self._state,
                in_flight=self._active is not None,
                free_resources=self._resource_capacity - self._reserved_resources,
                reserved_resources=self._reserved_resources,
                retained_resources=retained,
                used_slots=sum(slot is not None for slot in self._slots),
                notification_attempted=self._notification_attempted,
            )

    def run(self, action):
        # The fixed-registration API cannot be mixed with dynamic slots.
        raise MarketCleanupRejected("MARKET_CLEANUP_ACTION_REJECTED")

    def capture(self, handle, resource):
        """Publish an in-flight partial or retain the first late violation."""
        callback = None
        with self._lock:
            if (type(handle) is not _DynamicHandle or handle._owner is not self._handle_owner
                    or type(handle.slot) is not int or not 0 <= handle.slot < len(self._slots)
                    or type(handle.generation) is not int
                    or not 1 <= handle.generation <= self._generations[handle.slot]):
                raise MarketCleanupRejected("MARKET_CLEANUP_HANDLE_REJECTED")
            slot = self._slots[handle.slot]
            current = (slot is not None and slot.handle is handle
                       and self._active == handle.slot
                       and self._generations[handle.slot] == handle.generation)
            if current and not slot.capture_closed and slot.published < slot.limit - 1:
                slot.cells[slot.published] = resource
                slot.published += 1
                return

            # There is a reserved emergency cell for any active/retained slot.
            # A late call after successful release instead consumes one free
            # resource cell. Repeated protocol violations after exhausting all
            # bounded cells are outside the trusted producer contract.
            emergency = next((item for item in self._slots if item is not None
                              and item.cells[item.limit] is _MISSING), None)
            if emergency is not None:
                emergency.cells[emergency.limit] = resource
            elif (self._late_resource is _MISSING
                  and self._reserved_resources < self._resource_capacity):
                self._late_resource = resource
                self._reserved_resources += 1
            callback = self._unknown_locked()
        self._notify(callback)
        raise MarketCleanupUnknown("MARKET_CLEANUP_UNKNOWN")

    def run_dynamic(self, member, semantic, output_limit, action, validator, receiver):
        """Reserve before effects; producer calls capture(handle, resource).

        ``output_limit`` includes its return value. The producer is trusted to
        establish this bound before invocation, and receiver is trusted to accept
        ownership only on an exact True acknowledgement.
        """
        callback = None
        with self._lock:
            if self._state == "UNKNOWN":
                raise MarketCleanupUnknown("MARKET_CLEANUP_UNKNOWN")
            if self._state == "ACTION_IN_FLIGHT":
                callback = self._unknown_locked()
                index = None
            else:
                if (not any(member is item for item in self._members)
                        or not any(semantic is item for item in self._semantics)
                        or type(output_limit) is not int or output_limit < 1
                        or not callable(action) or not callable(validator) or not callable(receiver)
                        or output_limit + 1 > self._resource_capacity - self._reserved_resources):
                    raise MarketCleanupRejected("MARKET_CLEANUP_RESERVATION_REJECTED")
                index = next((i for i, slot in enumerate(self._slots)
                              if slot is None and self._generations[i] < _MAX_GENERATION), None)
                if index is None:
                    raise MarketCleanupRejected("MARKET_CLEANUP_RESERVATION_REJECTED")
                self._generations[index] += 1
                handle = _DynamicHandle(index, self._generations[index], self._handle_owner)
                slot = _DynamicSlot(handle, member, semantic, action, validator, receiver, output_limit)
                self._slots[index] = slot
                self._reserved_resources += output_limit + 1
                self._active = index
                self._state = "ACTION_IN_FLIGHT"
        if index is None:
            self._notify(callback)
            raise MarketCleanupUnknown("MARKET_CLEANUP_UNKNOWN")

        try:
            clear_before = self._guard() is False
        except BaseException as error:
            self._fail(error)
            raise
        if not clear_before or not self._still_active(index):
            self._fail()
        try:
            result = slot.action(handle, self.capture)
        except BaseException as error:
            self._fail(error)
            raise
        with self._lock:
            slot.cells[slot.limit - 1] = result
        if not self._still_active(index):
            self._fail()
        try:
            confirmed = slot.validator(result) is True
        except BaseException as error:
            self._fail(error)
            raise
        if not confirmed or not self._still_active(index):
            self._fail()
        try:
            clear_after = self._guard() is False
        except BaseException as error:
            self._fail(error)
            raise
        if not clear_after or not self._still_active(index):
            self._fail()
        # Seal capture and create the exact handoff snapshot atomically. A late
        # capture during handoff goes to the reserved emergency cell and latches
        # UNKNOWN; it can never be omitted from a successful handoff.
        try:
            with self._lock:
                if self._state == "ACTION_IN_FLIGHT" and self._active == index:
                    slot.capture_closed = True
                    published = tuple(slot.cells[:slot.published]) + (result,)
                else:
                    published = None
        except BaseException as error:
            self._fail(error)
            raise
        if published is None:
            self._fail()
        # All external code, including custody handoff, runs outside the lock.
        try:
            accepted = slot.receiver(published) is True
        except BaseException as error:
            self._fail(error)
            raise
        if not accepted or not self._still_active(index):
            self._fail()
        try:
            clear_handoff = self._guard() is False
        except BaseException as error:
            self._fail(error)
            raise
        with self._lock:
            if clear_handoff and self._state == "ACTION_IN_FLIGHT" and self._active == index:
                self._slots[index] = None
                self._reserved_resources -= slot.limit + 1
                self._active = None
                self._state = "READY"
                return None
        self._fail()
