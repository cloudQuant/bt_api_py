"""Read-only account snapshot facade over an explicitly registered service."""

from __future__ import annotations

import json
import math
import threading
import time
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from typing import Any

from bt_api_base.account_protocol import (
    AccountKey,
    AccountObservation,
    AccountResourceKey,
)

from bt_api_py.service_deployment import MOUNT_PORTS
from bt_api_py.service_runtime import (
    ServiceAccountBindingView,
    ServiceClientRuntime,
    ServiceRuntimeError,
)

_ACCOUNT_SNAPSHOT_OPERATION = "account.snapshot"
_ACCOUNT_READ_PORT = "service.account_read"
_REPLY_FIELDS = frozenset(
    {
        "requested_cursor",
        "snapshot",
        "events",
        "next_cursor",
        "latest_cursor",
        "recovered_from_snapshot",
        "snapshot_source",
        "state_status",
    }
)
_SNAPSHOT_FIELDS = frozenset(
    {
        "account_scope",
        "strategy_scope",
        "snapshot_id",
        "cursor",
        "payload",
        "captured_at",
    }
)
_STATUS_FIELDS = frozenset({"session_generation", "ready", "fresh_for_seconds"})
_STATE_PAYLOAD_FIELDS = frozenset(
    {"schema_id", "schema_version", "session_generation", "observation"}
)


class ServiceAccountError(RuntimeError):
    """Finite account-read diagnostics without remote values or account data."""

    def __init__(self, code: str) -> None:
        self.code = code
        super().__init__(code)


@dataclass(frozen=True)
class AccountStateView:
    """One immutable provider account observation with a local freshness bound.

    These provider facts do not represent or reconstruct a strategy's virtual
    book, open-order intent, or allocation state.
    """

    observation: AccountObservation
    cursor: int
    snapshot_id: str
    session_generation: str
    received_at_monotonic: float
    fresh_until_monotonic: float


class ServiceAccountClient:
    """Synchronously read one trusted, complete account baseline from a mount.

    The runtime must already be started. This facade creates no thread, does
    not close the runtime, and never turns provider balances into strategy-book
    state. Every refresh is a read-only ``account.snapshot`` RPC.
    """

    def __init__(
        self,
        runtime: ServiceClientRuntime,
        mount: str,
        *,
        account_ref: str,
        expected_account: AccountKey,
        required_coverage: tuple[AccountResourceKey, ...],
        monotonic_clock: Callable[[], float] = time.monotonic,
    ) -> None:
        if not isinstance(runtime, ServiceClientRuntime):
            raise ServiceAccountError("INVALID_RUNTIME")
        if (
            type(mount) is not str
            or mount not in MOUNT_PORTS
            or _ACCOUNT_READ_PORT not in MOUNT_PORTS[mount]
        ):
            raise ServiceAccountError("INVALID_ACCOUNT_MOUNT")
        if type(account_ref) is not str or not account_ref or account_ref != account_ref.strip():
            raise ServiceAccountError("INVALID_ACCOUNT_REF")
        if type(expected_account) is not AccountKey:
            raise ServiceAccountError("INVALID_EXPECTED_ACCOUNT")
        if (
            type(required_coverage) is not tuple
            or not required_coverage
            or any(type(item) is not AccountResourceKey for item in required_coverage)
            or len(set(required_coverage)) != len(required_coverage)
        ):
            raise ServiceAccountError("INVALID_REQUIRED_COVERAGE")
        if not callable(monotonic_clock):
            raise ServiceAccountError("INVALID_MONOTONIC_CLOCK")

        self._runtime = runtime
        self._mount = mount
        self._account_ref = account_ref
        self._expected_account = expected_account
        self._required_coverage = required_coverage
        self._monotonic_clock = monotonic_clock
        self._owner_thread_id = threading.get_ident()
        self._closed = False
        self._current_view: AccountStateView | None = None
        self._current_binding_signature: tuple[object, ...] | None = None
        self._last_view: AccountStateView | None = None
        self._last_cursor_identity: tuple[str, str, int] | None = None
        self._last_snapshot_identity: tuple[str, str, int, str, bytes] | None = None

    @property
    def mount(self) -> str:
        """Configured runtime mount, fixed for the lifetime of this facade."""

        return self._mount

    @property
    def account_ref(self) -> str:
        """Trusted account reference selected when the facade was created."""

        return self._account_ref

    @property
    def expected_account(self) -> AccountKey:
        """Expected provider account identity used to validate every baseline."""

        return self._expected_account

    @property
    def required_coverage(self) -> tuple[AccountResourceKey, ...]:
        """Required complete resource set, fixed for this facade's lifetime."""

        return self._required_coverage

    @property
    def healthy(self) -> bool:
        """Whether the current accepted view is fresh under the current binding."""

        if self._closed or self._current_view is None:
            return False
        if threading.get_ident() != self._owner_thread_id:
            return False
        try:
            now = self._now()
            if now >= self._current_view.fresh_until_monotonic:
                self._invalidate_current()
                return False
            binding = self._get_binding()
        except (ServiceAccountError, ServiceRuntimeError):
            self._invalidate_current()
            return False
        if self._binding_signature(binding) != self._current_binding_signature:
            self._invalidate_current()
            return False
        return True

    @property
    def last_view(self) -> AccountStateView | None:
        """Most recently accepted view, retained for diagnostics after invalidation."""

        return self._last_view

    def refresh(self) -> AccountStateView:
        """Refresh from the registered account service with one bounded read RPC."""

        self._assert_open_owner()
        # A failed explicit refresh must not leave an older observation looking
        # current. Keep it available through last_view for diagnostics only.
        self._invalidate_current()
        try:
            self._runtime.maintain()
            binding = self._get_binding()
        except ServiceRuntimeError:
            self._invalidate_current()
            raise ServiceAccountError("ACCOUNT_BINDING_UNAVAILABLE") from None

        binding_signature = self._binding_signature(binding)
        if self._current_view is not None and binding_signature != self._current_binding_signature:
            self._invalidate_current()

        request_started = self._now()
        try:
            reply = self._runtime.call(
                self.mount,
                _ACCOUNT_SNAPSHOT_OPERATION,
                {"limit": 1},
                account_ref=self.account_ref,
                scope_ref=binding.scope_ref,
            )
        except ServiceRuntimeError:
            self._invalidate_current()
            raise ServiceAccountError("ACCOUNT_READ_FAILED") from None
        except Exception:
            self._invalidate_current()
            raise ServiceAccountError("ACCOUNT_READ_FAILED") from None
        received_at = self._now()

        try:
            current_binding = self._get_binding()
        except ServiceRuntimeError:
            self._invalidate_current()
            raise ServiceAccountError("ACCOUNT_BINDING_CHANGED") from None
        if self._binding_signature(current_binding) != binding_signature:
            self._invalidate_current()
            raise ServiceAccountError("ACCOUNT_BINDING_CHANGED")

        observation, cursor, snapshot_id, generation, fresh_for = self._parse_reply(reply)
        freshness_deadline = request_started + fresh_for
        if not math.isfinite(freshness_deadline):
            self._invalidate_current()
            raise ServiceAccountError("INVALID_ACCOUNT_STATE_STATUS")
        if self._now() >= freshness_deadline:
            self._invalidate_current()
            raise ServiceAccountError("STALE_ACCOUNT_STATE")

        previous = self._last_cursor_identity
        snapshot_identity = _canonical_observation_bytes(observation)
        prior_snapshot = self._last_snapshot_identity
        if (
            previous is not None
            and previous[0] == current_binding.service_epoch
            and previous[1] == generation
        ):
            if cursor < previous[2]:
                self._invalidate_current()
                raise ServiceAccountError("ACCOUNT_CURSOR_REGRESSION")
            if (
                cursor == previous[2]
                and prior_snapshot is not None
                and (prior_snapshot[3] != snapshot_id or prior_snapshot[4] != snapshot_identity)
            ):
                self._invalidate_current()
                raise ServiceAccountError("ACCOUNT_CURSOR_CONFLICT")

        view = AccountStateView(
            observation=observation,
            cursor=cursor,
            snapshot_id=snapshot_id,
            session_generation=generation,
            received_at_monotonic=received_at,
            fresh_until_monotonic=freshness_deadline,
        )
        self._current_view = view
        self._current_binding_signature = self._binding_signature(current_binding)
        self._last_view = view
        self._last_cursor_identity = (current_binding.service_epoch, generation, cursor)
        self._last_snapshot_identity = (
            current_binding.service_epoch,
            generation,
            cursor,
            snapshot_id,
            snapshot_identity,
        )
        return view

    def snapshot(self) -> AccountStateView:
        """Return the cached view only while it and its runtime binding are fresh."""

        self._assert_open_owner()
        if not self.healthy:
            raise ServiceAccountError("STALE_ACCOUNT_STATE")
        assert self._current_view is not None
        return self._current_view

    def close(self) -> None:
        """Clear the facade's local cache without closing the shared runtime."""

        self._assert_owner()
        if self._closed:
            return
        self._invalidate_current()
        self._last_view = None
        self._last_cursor_identity = None
        self._last_snapshot_identity = None
        self._closed = True

    def _parse_reply(
        self, reply: Mapping[str, Any]
    ) -> tuple[AccountObservation, int, str, str, float]:
        if not isinstance(reply, Mapping) or set(reply) != _REPLY_FIELDS:
            self._reject("INVALID_ACCOUNT_REPLY")

        status = reply.get("state_status")
        if not isinstance(status, Mapping) or set(status) != _STATUS_FIELDS:
            self._reject("ACCOUNT_STATE_STATUS_UNAVAILABLE")
        ready = status.get("ready")
        fresh_for_raw = status.get("fresh_for_seconds")
        if type(ready) is not bool:
            self._reject("INVALID_ACCOUNT_STATE_STATUS")
        if (
            type(fresh_for_raw) not in (int, float)
            or not math.isfinite(fresh_for_raw)
            or fresh_for_raw < 0
        ):
            self._reject("INVALID_ACCOUNT_STATE_STATUS")
        if not ready:
            self._reject("ACCOUNT_SESSION_NOT_READY")
        fresh_for = float(fresh_for_raw)
        if fresh_for <= 0:
            self._reject("STALE_ACCOUNT_STATE")

        generation = status.get("session_generation")
        if type(generation) is not str or not generation:
            self._reject("INVALID_ACCOUNT_STATE_STATUS")

        if reply.get("requested_cursor") is not None:
            self._reject("INVALID_ACCOUNT_BASELINE")
        if reply.get("recovered_from_snapshot") is not False:
            self._reject("INVALID_ACCOUNT_BASELINE")
        if reply.get("snapshot_source") != "persisted_journal_baseline":
            self._reject("INVALID_ACCOUNT_BASELINE")
        events = reply.get("events")
        if type(events) not in (list, tuple) or events:
            self._reject("INVALID_ACCOUNT_BASELINE")

        snapshot = reply.get("snapshot")
        if not isinstance(snapshot, Mapping) or set(snapshot) != _SNAPSHOT_FIELDS:
            self._reject("ACCOUNT_BASELINE_UNAVAILABLE")
        account_scope = snapshot.get("account_scope")
        strategy_scope = snapshot.get("strategy_scope")
        snapshot_id = snapshot.get("snapshot_id")
        cursor = snapshot.get("cursor")
        captured_at = snapshot.get("captured_at")
        if (
            type(account_scope) is not str
            or not account_scope
            or type(strategy_scope) is not str
            or not strategy_scope
            or type(snapshot_id) is not str
            or not snapshot_id
            or type(cursor) is not int
            or cursor < 0
            or type(captured_at) not in (int, float)
            or not math.isfinite(captured_at)
            or captured_at < 0
        ):
            self._reject("INVALID_ACCOUNT_BASELINE")
        if (
            type(reply.get("next_cursor")) is not int
            or type(reply.get("latest_cursor")) is not int
            or reply["next_cursor"] != cursor
            or reply["latest_cursor"] != cursor
        ):
            self._reject("INVALID_ACCOUNT_BASELINE")

        payload = snapshot.get("payload")
        if not isinstance(payload, Mapping) or set(payload) != _STATE_PAYLOAD_FIELDS:
            self._reject("INVALID_ACCOUNT_STATE_PAYLOAD")
        if (
            payload.get("schema_id") != "sdk.account.state"
            or type(payload.get("schema_version")) is not int
            or payload.get("schema_version") != 1
            or type(payload.get("session_generation")) is not str
            or not payload.get("session_generation")
            or payload.get("session_generation") != generation
        ):
            self._reject("ACCOUNT_GENERATION_MISMATCH")
        observation_raw = payload.get("observation")
        try:
            observation = AccountObservation.from_dict(observation_raw)
        except Exception:
            self._reject("INVALID_ACCOUNT_OBSERVATION")
        if observation.account_key != self.expected_account:
            self._reject("ACCOUNT_IDENTITY_MISMATCH")
        if frozenset(observation.coverage) != frozenset(self.required_coverage):
            self._reject("ACCOUNT_COVERAGE_MISMATCH")
        if not observation.balances_complete or not observation.positions_complete:
            self._reject("ACCOUNT_OBSERVATION_INCOMPLETE")
        return observation, cursor, snapshot_id, generation, fresh_for

    def _get_binding(self) -> ServiceAccountBindingView:
        return self._runtime.account_binding_view(
            self.mount,
            self.account_ref,
            required_operations=frozenset({_ACCOUNT_SNAPSHOT_OPERATION}),
        )

    @staticmethod
    def _binding_signature(binding: ServiceAccountBindingView) -> tuple[object, ...]:
        return (
            binding.mount,
            binding.service_id,
            binding.instance_id,
            binding.scope_ref,
            binding.account_ref,
            binding.registration_id,
            binding.service_epoch,
            binding.rpc_scope,
            binding.transport_ref,
            binding.operations,
        )

    def _now(self) -> float:
        value = self._monotonic_clock()
        if type(value) not in (int, float) or not math.isfinite(value):
            raise ServiceAccountError("INVALID_MONOTONIC_CLOCK")
        return float(value)

    def _reject(self, code: str) -> None:
        self._invalidate_current()
        raise ServiceAccountError(code)

    def _invalidate_current(self) -> None:
        self._current_view = None
        self._current_binding_signature = None

    def _assert_owner(self) -> None:
        if threading.get_ident() != self._owner_thread_id:
            raise ServiceAccountError("WRONG_OWNER_THREAD")

    def _assert_open_owner(self) -> None:
        self._assert_owner()
        if self._closed:
            raise ServiceAccountError("CLIENT_CLOSED")


__all__ = ["AccountStateView", "ServiceAccountClient", "ServiceAccountError"]


def _canonical_observation_bytes(observation: AccountObservation) -> bytes:
    """Return a stable comparison form for equal-cursor conflict checks."""

    return json.dumps(
        observation.to_dict(),
        ensure_ascii=False,
        allow_nan=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
