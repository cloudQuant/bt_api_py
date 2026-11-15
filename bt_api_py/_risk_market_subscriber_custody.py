"""Unconnected, bounded custody for explicitly supplied market subscribers.

All inputs are trusted in-process objects. Identity uniqueness is scoped to this
module instance: trusted composition must not reload or alias-load the module or
bypass its private registry. The registry retains tombstones for that instance's
lifetime; it is not a process-wide identity authority or global fence, and cannot
revoke an entered call. Producers that hide objects are outside this contract.
"""

from threading import Lock

from ._risk_market_cleanup_token import (
    DynamicRiskMarketCleanupToken,
    MarketCleanupRejected,
    MarketCleanupUnknown,
)

_MAX_BATCHES = 256
_MAX_SUBSCRIBERS = 256
_MAX_SUBSCRIBERS_TOTAL = 256
_all_custodies = []  # Module-instance identity tombstones across tokens.
_batches = []  # Strong references prevent token/id reuse and tombstone loss.
_batches_lock = Lock()
_MISSING = object()


def subscriber_registry(token, guard, current_token, capacity):
    """Return the sole bounded registry for this token, or reject conflicts.

    ``guard`` must return exactly False, and ``current_token`` must return the
    original token. Both are trusted callbacks; they execute outside locks.
    Registry and tombstone capacity is deliberately never reclaimed within this
    module instance. Trusted composition must use this entry point consistently.
    """
    if (type(token) is not DynamicRiskMarketCleanupToken or not callable(guard)
            or not callable(current_token) or type(capacity) is not int
            or not 1 <= capacity <= _MAX_SUBSCRIBERS):
        raise ValueError("INVALID_SUBSCRIBER_CUSTODY_CONFIGURATION")
    with _batches_lock:
        for batch in _batches:
            if batch._token is token:
                if (batch._guard is not guard or batch._current_token is not current_token
                        or batch._capacity != capacity):
                    raise MarketCleanupRejected("SUBSCRIBER_REGISTRY_CONFLICT")
                return batch
        if len(_batches) >= _MAX_BATCHES:
            raise MarketCleanupRejected("SUBSCRIBER_REGISTRY_CAPACITY")
        batch = _SubscriberRegistry(token, guard, current_token, capacity)
        _batches.append(batch)
        return batch


class _SubscriberRegistry:
    def __init__(self, token, guard, current_token, capacity):
        self._token = token
        self._guard = guard
        self._current_token = current_token
        self._capacity = capacity
        self._entries = []
        self._lock = Lock()

    def register(self, subscriber, client, binding, operation, epoch, member,
                 semantic, validate):
        """Capture original identities, rejecting rebinding or full capacity.

        Member and semantic membership are checked by the token before close.
        The subscriber must already be known to the caller; no factory runs here.
        """
        if subscriber is None or not callable(operation) or not callable(validate):
            raise MarketCleanupRejected("SUBSCRIBER_REGISTRATION_REJECTED")
        with _batches_lock, self._lock:
            for item in self._entries:
                if item._subscriber is subscriber:
                    if all(a is b for a, b in zip(
                        (item._client, item._binding, item._operation, item._epoch,
                         item._member, item._semantic, item._validate),
                        (client, binding, operation, epoch, member, semantic, validate),
                    )):
                        return item
                    raise MarketCleanupRejected("SUBSCRIBER_REGISTRATION_CONFLICT")
            if any(item._subscriber is subscriber for item in _all_custodies):
                raise MarketCleanupRejected("SUBSCRIBER_IDENTITY_CONFLICT")
            if (len(self._entries) >= self._capacity
                    or len(_all_custodies) >= _MAX_SUBSCRIBERS_TOTAL):
                raise MarketCleanupRejected("SUBSCRIBER_REGISTRY_CAPACITY")
            item = _SubscriberCustody(self, subscriber, client, binding, operation,
                                      epoch, member, semantic, validate)
            # Publish the global tombstone first: even an allocation failure
            # in the per-registry list cannot allow later double registration.
            _all_custodies.append(item)
            self._entries.append(item)
            return item


class _SubscriberCustody:
    def __init__(self, registry, subscriber, client, binding, operation, epoch,
                 member, semantic, validate):
        self._registry = registry
        self._subscriber = subscriber
        self._client = client
        self._binding = binding
        self._operation = operation
        self._epoch = epoch
        self._member = member
        self._semantic = semantic
        self._validate = validate
        self._state = "OPEN"
        self._lock = Lock()
        self._transferring = False
        self._committing = False
        self._result = _MISSING
        self._error = _MISSING

    def status(self):
        # Inspect the shared token outside the custody lock. A later UNKNOWN
        # cannot revoke an earlier return, but an observed one is sticky here.
        token_unknown = self._registry._token.status().state == "UNKNOWN"
        with self._lock:
            if token_unknown:
                self._state = "UNKNOWN"
                self._committing = False
            return "IN_FLIGHT" if self._committing else self._state

    def _unknown(self, error=_MISSING):
        with self._lock:
            if self._state != "CLOSED" or self._committing:
                self._state = "UNKNOWN"
                self._committing = False
            if error is not _MISSING and self._error is _MISSING:
                self._error = error
        # Token notifications can reenter; never call them under the local lock.
        if error is _MISSING:
            self._registry._token.mark_unknown()
        else:
            self._registry._token.mark_unknown(error)

    def _active(self):
        with self._lock:
            return self._state == "IN_FLIGHT"

    def _check(self, token_state):
        registry = self._registry
        return (self._active() and registry._current_token() is registry._token
                and registry._guard() is False
                and registry._token.status().state == token_state and self._active())

    def _check_committing(self):
        def active():
            with self._lock:
                return self._state == "CLOSED" and self._committing
        registry = self._registry
        return (active() and registry._current_token() is registry._token
                and registry._guard() is False
                and registry._token.status().state == "READY" and active())

    def close(self):
        """Execute one explicit close; uncertainty is terminal and retains inputs."""
        self.status()
        with self._lock:
            if self._committing:
                self._state = "UNKNOWN"
                self._committing = False
                reentered = True
            elif self._state in ("CLOSED", "UNKNOWN"):
                raise MarketCleanupRejected("SUBSCRIBER_CUSTODY_TERMINAL")
            elif self._state == "IN_FLIGHT" or self._transferring:
                self._state = "UNKNOWN"
                reentered = True
            else:
                self._state = "IN_FLIGHT"
                reentered = False
        if reentered:
            self._unknown()
            raise MarketCleanupUnknown("SUBSCRIBER_CUSTODY_UNKNOWN")
        token = self._registry._token
        try:
            if not self._check("READY"):
                raise MarketCleanupUnknown("SUBSCRIBER_CUSTODY_UNKNOWN")

            def action(_handle, _capture):
                if not self._check("ACTION_IN_FLIGHT"):
                    raise MarketCleanupUnknown("SUBSCRIBER_CUSTODY_UNKNOWN")
                result = self._operation()
                with self._lock:
                    self._result = result
                if not self._check("ACTION_IN_FLIGHT"):
                    raise MarketCleanupUnknown("SUBSCRIBER_CUSTODY_UNKNOWN")
                return result

            def validate(result):
                return self._check("ACTION_IN_FLIGHT") and self._validate(result) is True

            def receiver(_results):
                return self._check("ACTION_IN_FLIGHT")

            token.run_dynamic(self._member, self._semantic, 1, action, validate, receiver)
            if not self._check("READY"):
                raise MarketCleanupUnknown("SUBSCRIBER_CUSTODY_UNKNOWN")
            with self._lock:
                if self._state == "IN_FLIGHT":
                    self._state = "CLOSED"
                    self._committing = True
                else:
                    raise MarketCleanupUnknown("SUBSCRIBER_CUSTODY_UNKNOWN")
            # Provisional CLOSED is not observable until this second check.
            # Another UNKNOWN can still race after any process-local check.
            if not self._check_committing():
                raise MarketCleanupUnknown("SUBSCRIBER_CUSTODY_UNKNOWN")
            with self._lock:
                if self._state == "CLOSED" and self._committing:
                    self._committing = False
                    return
            raise MarketCleanupUnknown("SUBSCRIBER_CUSTODY_UNKNOWN")
        except BaseException as error:
            # The original object and all identities remain referenced on failure.
            try:
                self._unknown(error)
            except BaseException as notification_error:
                if (issubclass(type(error), Exception)
                        and not issubclass(type(notification_error), Exception)):
                    raise
            if (issubclass(type(error), Exception) and not issubclass(
                    type(error), (MarketCleanupRejected, MarketCleanupUnknown))):
                raise MarketCleanupUnknown("SUBSCRIBER_CUSTODY_UNKNOWN") from None
            raise

    def transfer(self, receiver):
        """Offer this same custody to a trusted receiver, never the subscriber.

        Exact True acknowledges receipt. Both parties retain the same state
        machine; acknowledgement does not create a second close capability.
        """
        if not callable(receiver):
            raise MarketCleanupRejected("SUBSCRIBER_TRANSFER_REJECTED")
        self.status()
        with self._lock:
            if self._committing:
                self._state = "UNKNOWN"
                self._committing = False
                reentered = True
            elif self._state in ("CLOSED", "UNKNOWN"):
                raise MarketCleanupRejected("SUBSCRIBER_CUSTODY_TERMINAL")
            elif self._state == "IN_FLIGHT" or self._transferring:
                self._state = "UNKNOWN"
                reentered = True
            else:
                self._transferring = True
                reentered = False
        if reentered:
            self._unknown()
            raise MarketCleanupUnknown("SUBSCRIBER_CUSTODY_UNKNOWN")

        def clear():
            with self._lock:
                active = self._state == "OPEN" and self._transferring
            registry = self._registry
            if not (active and registry._current_token() is registry._token
                    and registry._guard() is False
                    and registry._token.status().state == "READY"):
                return False
            with self._lock:
                return self._state == "OPEN" and self._transferring

        try:
            if not clear() or receiver(self) is not True or not clear():
                raise MarketCleanupUnknown("SUBSCRIBER_CUSTODY_UNKNOWN")
            with self._lock:
                if self._state == "OPEN" and self._transferring:
                    self._transferring = False
                    return
            raise MarketCleanupUnknown("SUBSCRIBER_CUSTODY_UNKNOWN")
        except BaseException as error:
            try:
                self._unknown(error)
            except BaseException as notification_error:
                if (issubclass(type(error), Exception)
                        and not issubclass(type(notification_error), Exception)):
                    raise
            if (issubclass(type(error), Exception) and not issubclass(
                    type(error), (MarketCleanupRejected, MarketCleanupUnknown))):
                raise MarketCleanupUnknown("SUBSCRIBER_CUSTODY_UNKNOWN") from None
            raise
