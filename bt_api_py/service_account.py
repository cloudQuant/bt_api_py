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
_ACCOUNT_TYPED_SNAPSHOT_OPERATION = "account.snapshot.typed"
_ACCOUNT_INSTRUMENT_METADATA_OPERATION = "account.instrument_metadata"
_ACCOUNT_READ_PORT = "service.account_read"
# Retain every accepted generation within an epoch to detect rollback; once
# full, reject new generations rather than forgetting an older binding.
_MAX_ACCOUNT_SCOPE_GENERATIONS = 256
_MAX_ACCOUNT_SCOPE_HISTORY_BYTES = 8 * 1024 * 1024
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
_TYPED_STATE_PAYLOAD_FIELDS = _STATE_PAYLOAD_FIELDS | {"source_schema_version"}


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
    state. The default refresh uses the typed ``account.snapshot.typed`` RPC
    (iteration 60.08, G5); the generic ``account.snapshot`` map is a legacy
    form that must be selected explicitly with ``snapshot_format="generic"``.
    A typed refresh never falls back to the generic operation.

    Both formats pin the authenticated service's declared account scope and
    snapshot identity per session generation. A scope change, cursor rollback,
    or conflicting equal-cursor snapshot is rejected. At most 256 generations
    and 8 MiB of identity text and canonical observations are retained per
    service epoch; exhaustion fails closed. Expired views do not erase history.
    The service's first declared scope does not itself prove provider identity.
    """

    def __init__(
        self,
        runtime: ServiceClientRuntime,
        mount: str,
        *,
        account_ref: str,
        expected_account: AccountKey,
        required_coverage: tuple[AccountResourceKey, ...],
        require_open_orders: bool = False,
        expected_scope_ref: str | None = None,
        instrument_metadata_parser: Callable[..., Any] | None = None,
        snapshot_format: str = "typed",
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
        if type(require_open_orders) is not bool:
            raise ServiceAccountError("INVALID_REQUIRE_OPEN_ORDERS")
        if expected_scope_ref is not None and (
            type(expected_scope_ref) is not str
            or not expected_scope_ref
            or expected_scope_ref != expected_scope_ref.strip()
        ):
            raise ServiceAccountError("INVALID_EXPECTED_SCOPE_REF")
        if not callable(monotonic_clock):
            raise ServiceAccountError("INVALID_MONOTONIC_CLOCK")
        if type(snapshot_format) is not str or snapshot_format not in {"generic", "typed"}:
            raise ServiceAccountError("INVALID_ACCOUNT_SNAPSHOT_FORMAT")

        self._runtime = runtime
        self._snapshot_format = snapshot_format
        self._snapshot_operation = (
            _ACCOUNT_TYPED_SNAPSHOT_OPERATION if snapshot_format == "typed"
            else _ACCOUNT_SNAPSHOT_OPERATION
        )
        self._initial_snapshot_operation = self._snapshot_operation
        self._mount = mount
        self._account_ref = account_ref
        self._expected_account = expected_account
        self._required_coverage = required_coverage
        self._require_open_orders = require_open_orders
        if instrument_metadata_parser is not None and not callable(instrument_metadata_parser):
            raise ServiceAccountError("INVALID_INSTRUMENT_METADATA_PARSER")
        self._instrument_metadata_parser = instrument_metadata_parser
        self._expected_scope_ref = expected_scope_ref
        self._monotonic_clock = monotonic_clock
        self._owner_thread_id = threading.get_ident()
        self._closed = False
        self._current_view: AccountStateView | None = None
        self._current_binding_signature: tuple[object, ...] | None = None
        self._last_view: AccountStateView | None = None
        # These opaque strings are the authenticated service's declarations;
        # pinning them detects changes, but cannot prove their first mapping.
        self._scope_epoch: str | None = None
        self._history_by_generation: dict[str, tuple[str, str, int, str, bytes]] = {}
        self._history_bytes = 0

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
    def instrument_metadata_type(self) -> Any:
        """Return the configured provider parser's metadata type, if declared."""

        return getattr(self._instrument_metadata_parser, "metadata_type", None)

    @property
    def required_coverage(self) -> tuple[AccountResourceKey, ...]:
        """Required complete resource set, fixed for this facade's lifetime."""

        return self._required_coverage

    @property
    def require_open_orders(self) -> bool:
        """Whether this facade requires a complete open-orders baseline."""

        return self._require_open_orders

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
                self._snapshot_operation,
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

        snapshot = reply["snapshot"]
        scope_identity = (
            current_binding.service_epoch,
            generation,
            snapshot["account_scope"],
            snapshot["strategy_scope"],
        )
        prior = (
            self._history_by_generation.get(generation)
            if self._scope_epoch == current_binding.service_epoch else None
        )
        if prior is not None and prior[:2] != scope_identity[2:]:
            self._invalidate_current()
            raise ServiceAccountError("ACCOUNT_SCOPE_CHANGED")

        snapshot_identity = _canonical_observation_bytes(observation)
        if prior is not None:
            if cursor < prior[2]:
                self._invalidate_current()
                raise ServiceAccountError("ACCOUNT_CURSOR_REGRESSION")
            if (
                cursor == prior[2]
                and (prior[3] != snapshot_id or prior[4] != snapshot_identity)
            ):
                self._invalidate_current()
                raise ServiceAccountError("ACCOUNT_CURSOR_CONFLICT")

        if (
            self._scope_epoch == current_binding.service_epoch
            and generation not in self._history_by_generation
            and len(self._history_by_generation) >= _MAX_ACCOUNT_SCOPE_GENERATIONS
        ):
            self._invalidate_current()
            raise ServiceAccountError("ACCOUNT_SCOPE_HISTORY_EXHAUSTED")

        record = (scope_identity[2], scope_identity[3], cursor, snapshot_id, snapshot_identity)
        try:
            record_bytes = _account_history_record_bytes(generation, record)
            previous_bytes = 0 if prior is None else _account_history_record_bytes(generation, prior)
        except UnicodeError:
            self._invalidate_current()
            raise ServiceAccountError("INVALID_ACCOUNT_BASELINE") from None
        history_bytes = (self._history_bytes if self._scope_epoch == current_binding.service_epoch else 0)
        next_history_bytes = history_bytes - previous_bytes + record_bytes
        if next_history_bytes > _MAX_ACCOUNT_SCOPE_HISTORY_BYTES:
            self._invalidate_current()
            raise ServiceAccountError("ACCOUNT_SCOPE_HISTORY_EXHAUSTED")

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
        if self._scope_epoch != current_binding.service_epoch:
            self._scope_epoch = current_binding.service_epoch
            self._history_by_generation.clear()
        self._history_by_generation[generation] = record
        self._history_bytes = next_history_bytes
        return view

    def snapshot(self) -> AccountStateView:
        """Return the cached view only while it and its runtime binding are fresh."""

        self._assert_open_owner()
        if not self.healthy:
            raise ServiceAccountError("STALE_ACCOUNT_STATE")
        assert self._current_view is not None
        return self._current_view

    def instrument_metadata(
        self,
        instrument_id: str,
        exchange_id: str,
    ) -> Any:
        """Read one fresh provider-parsed instrument row for this trusted account.

        The caller must first have a healthy account snapshot. The RPC is an
        explicit opt-in capability and is tied to the same mount, account,
        scope, service epoch, and account-session generation as that snapshot.
        """

        self._assert_open_owner()
        _validate_metadata_identifier(instrument_id, "INVALID_INSTRUMENT_ID")
        _validate_metadata_identifier(exchange_id, "INVALID_EXCHANGE_ID")
        view = self.snapshot()
        parser = self._instrument_metadata_parser
        if parser is None:
            raise ServiceAccountError("INSTRUMENT_METADATA_PARSER_UNAVAILABLE")
        required = frozenset(
            {self._snapshot_operation, _ACCOUNT_INSTRUMENT_METADATA_OPERATION}
        )
        try:
            self._runtime.maintain()
            binding = self._get_binding(required_operations=required)
        except ServiceRuntimeError:
            raise ServiceAccountError("INSTRUMENT_METADATA_UNAVAILABLE") from None
        binding_signature = self._binding_signature(binding)
        if binding_signature != self._current_binding_signature:
            self._invalidate_current()
            raise ServiceAccountError("ACCOUNT_BINDING_CHANGED")

        try:
            reply = self._runtime.call(
                self.mount,
                _ACCOUNT_INSTRUMENT_METADATA_OPERATION,
                {"instrument_id": instrument_id, "exchange_id": exchange_id},
                account_ref=self.account_ref,
                scope_ref=binding.scope_ref,
            )
        except ServiceRuntimeError:
            raise ServiceAccountError("INSTRUMENT_METADATA_READ_FAILED") from None
        except Exception:
            raise ServiceAccountError("INSTRUMENT_METADATA_READ_FAILED") from None

        try:
            current_binding = self._get_binding(required_operations=required)
        except ServiceRuntimeError:
            self._invalidate_current()
            raise ServiceAccountError("ACCOUNT_BINDING_CHANGED") from None
        if self._binding_signature(current_binding) != binding_signature:
            self._invalidate_current()
            raise ServiceAccountError("ACCOUNT_BINDING_CHANGED")
        try:
            current_view = self.snapshot()
        except ServiceAccountError:
            raise ServiceAccountError("STALE_ACCOUNT_STATE") from None
        if current_view is not view:
            raise ServiceAccountError("ACCOUNT_BINDING_CHANGED")
        return parser(
            reply,
            instrument_id=instrument_id,
            exchange_id=exchange_id,
            account_session_generation=view.session_generation,
            max_elapsed_seconds=binding.timeout,
        )

    def close(self) -> None:
        """Clear the facade's local cache without closing the shared runtime."""

        self._assert_owner()
        if self._closed:
            return
        self._invalidate_current()
        self._last_view = None
        self._scope_epoch = None
        self._history_by_generation.clear()
        self._history_bytes = 0
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
        expected_fields = (
            _TYPED_STATE_PAYLOAD_FIELDS if self._snapshot_format == "typed"
            else _STATE_PAYLOAD_FIELDS
        )
        if not isinstance(payload, Mapping) or set(payload) != expected_fields:
            self._reject("INVALID_ACCOUNT_STATE_PAYLOAD")
        schema_version = payload.get("schema_version")
        if self._snapshot_format == "typed":
            if payload.get("schema_id") != "sdk.account.state.typed" or type(schema_version) is not int or schema_version != 1:
                self._reject("INVALID_ACCOUNT_STATE_SCHEMA")
            schema_version = payload.get("source_schema_version")
            if type(schema_version) is not int or schema_version not in {1, 2}:
                self._reject("INVALID_ACCOUNT_STATE_SCHEMA")
        elif (payload.get("schema_id") != "sdk.account.state" or type(schema_version) is not int or schema_version not in {1, 2}):
            self._reject("INVALID_ACCOUNT_STATE_SCHEMA")
        if (
            type(payload.get("session_generation")) is not str
            or not payload.get("session_generation")
            or payload.get("session_generation") != generation
        ):
            self._reject("ACCOUNT_GENERATION_MISMATCH")
        observation_raw = payload.get("observation")
        if not isinstance(observation_raw, Mapping):
            self._reject("INVALID_ACCOUNT_OBSERVATION")
        observation_data = observation_raw.get("data") if self._snapshot_format == "typed" else observation_raw
        if not isinstance(observation_data, Mapping):
            self._reject("INVALID_ACCOUNT_OBSERVATION")
        has_open_orders = "open_orders" in observation_data
        has_open_orders_complete = "open_orders_complete" in observation_data
        if schema_version == 1 and (has_open_orders or has_open_orders_complete):
            self._reject("ACCOUNT_STATE_SCHEMA_MISMATCH")
        if schema_version == 2 and not (has_open_orders and has_open_orders_complete):
            self._reject("ACCOUNT_STATE_SCHEMA_MISMATCH")
        try:
            if self._snapshot_format == "typed":
                from bt_api_base.wire_codec_v1 import (
                    WireCodecError,
                    account_observation_from_typed_map,
                )

                try:
                    observation = account_observation_from_typed_map(observation_raw)
                except WireCodecError as error:
                    if error.code == "WIRE_TYPED_CONTRACT_MISMATCH":
                        # A peer on another typed minor/registry: explicit
                        # refusal, never a downgrade to the generic map.
                        self._reject("TYPED_CONTRACT_MISMATCH")
                    raise
            else:
                observation = AccountObservation.from_dict(observation_raw)
        except ServiceAccountError:
            raise
        except Exception:
            self._reject("INVALID_ACCOUNT_OBSERVATION")
        if schema_version == 2 and observation.open_orders is None:
            self._reject("ACCOUNT_STATE_SCHEMA_MISMATCH")
        if self.require_open_orders and (
            observation.open_orders is None or not observation.open_orders_complete
        ):
            self._reject("ACCOUNT_OPEN_ORDERS_INCOMPLETE")
        if observation.account_key != self.expected_account:
            self._reject("ACCOUNT_IDENTITY_MISMATCH")
        if frozenset(observation.coverage) != frozenset(self.required_coverage):
            self._reject("ACCOUNT_COVERAGE_MISMATCH")
        if not observation.balances_complete or not observation.positions_complete:
            self._reject("ACCOUNT_OBSERVATION_INCOMPLETE")
        return observation, cursor, snapshot_id, generation, fresh_for

    def _get_binding(
        self,
        *,
        required_operations: frozenset[str] | None = None,
    ) -> ServiceAccountBindingView:
        if required_operations is None:
            required_operations = frozenset({self._snapshot_operation})
        binding = self._runtime.account_binding_view(
            self.mount,
            self.account_ref,
            required_operations=required_operations,
        )
        if self._expected_scope_ref is not None and binding.scope_ref != self._expected_scope_ref:
            raise ServiceAccountError("SCOPE_DENIED")
        return binding

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
        expected = (_ACCOUNT_TYPED_SNAPSHOT_OPERATION if self._snapshot_format == "typed" else _ACCOUNT_SNAPSHOT_OPERATION)
        if self._snapshot_operation != self._initial_snapshot_operation or self._snapshot_operation != expected:
            self._invalidate_current()
            raise ServiceAccountError("ACCOUNT_SNAPSHOT_FORMAT_CHANGED")


__all__ = [
    "AccountStateView",
    "ServiceAccountClient",
    "ServiceAccountError",
]


def _canonical_observation_bytes(observation: AccountObservation) -> bytes:
    """Return a stable comparison form for equal-cursor conflict checks."""

    return json.dumps(
        observation.to_dict(),
        ensure_ascii=False,
        allow_nan=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")


def _account_history_record_bytes(
    generation: str, record: tuple[str, str, int, str, bytes]
) -> int:
    """Bound retained payload and identity text, not just generation count."""
    return sum(len(value.encode("utf-8")) for value in (generation, record[0], record[1], record[3])) + len(record[4])


def _validate_metadata_identifier(value: object, code: str) -> None:
    if type(value) is not str or not value or value != value.strip():
        raise ServiceAccountError(code)
    try:
        if len(value.encode("utf-8", errors="strict")) > 128:
            raise ServiceAccountError(code)
    except UnicodeEncodeError:
        raise ServiceAccountError(code) from None
    if any(ord(char) < 32 or ord(char) == 127 for char in value):
        raise ServiceAccountError(code)
