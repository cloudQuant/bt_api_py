"""Read-only account snapshot facade over an explicitly registered service."""

from __future__ import annotations

import json
import math
import re
import threading
import time
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation
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
_ACCOUNT_INSTRUMENT_METADATA_OPERATION = "account.instrument_metadata"
_ACCOUNT_READ_PORT = "service.account_read"
_INSTRUMENT_METADATA_SCHEMA_ID = "ctp.instrument_metadata"
_INSTRUMENT_METADATA_SCHEMA_VERSION = 1
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
_INSTRUMENT_METADATA_FIELDS = frozenset(
    {
        "schema_id",
        "schema_version",
        "instrument_id",
        "exchange_id",
        "product_class",
        "asset_type",
        "price_tick",
        "volume_multiple",
        "lower_limit_price",
        "upper_limit_price",
        "price_limits_available",
        "price_limits_source",
        "account_session_generation",
        "account_fingerprint",
        "trading_day",
        "source_connection_generation",
        "source_request_id",
        "source_records_sha256",
        "source_revision",
        "source",
        "query_started_at_utc",
        "observed_at_utc",
        "query_clock_domain_id",
        "query_elapsed_seconds",
        "quote_stream_generation",
        "quote_source",
        "clock_domain_id",
        "clock_source_session_id",
        "clock_source",
    }
)
_SHA256_PATTERN = re.compile(r"^[0-9a-f]{64}$")


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


@dataclass(frozen=True)
class ServiceAccountInstrumentMetadata:
    """One exact, current CTP instrument metadata row from the service host.

    This DTO is read-only evidence for strategy admission. It is not an
    account snapshot and does not establish price-limit availability when the
    native instrument query did not supply limits.
    """

    instrument_id: str
    exchange_id: str
    product_class: str
    asset_type: str
    price_tick: str
    volume_multiple: int
    lower_limit_price: str | None
    upper_limit_price: str | None
    price_limits_available: bool
    price_limits_source: str
    account_session_generation: str
    account_fingerprint: str
    trading_day: str
    source_connection_generation: int
    source_request_id: int
    source_records_sha256: str
    source_revision: str
    source: str
    query_started_at_utc: str
    observed_at_utc: str
    query_clock_domain_id: str
    query_elapsed_seconds: float
    quote_stream_generation: int
    quote_source: str
    clock_domain_id: str
    clock_source_session_id: str
    clock_source: str


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
        require_open_orders: bool = False,
        expected_scope_ref: str | None = None,
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

        self._runtime = runtime
        self._mount = mount
        self._account_ref = account_ref
        self._expected_account = expected_account
        self._required_coverage = required_coverage
        self._require_open_orders = require_open_orders
        self._expected_scope_ref = expected_scope_ref
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

    def instrument_metadata(
        self,
        instrument_id: str,
        exchange_id: str,
    ) -> ServiceAccountInstrumentMetadata:
        """Read one fresh typed CTP instrument row for this trusted account.

        The caller must first have a healthy account snapshot. The RPC is an
        explicit opt-in capability and is tied to the same mount, account,
        scope, service epoch, and account-session generation as that snapshot.
        """

        self._assert_open_owner()
        _validate_metadata_identifier(instrument_id, "INVALID_INSTRUMENT_ID")
        _validate_metadata_identifier(exchange_id, "INVALID_EXCHANGE_ID")
        view = self.snapshot()
        required = frozenset(
            {_ACCOUNT_SNAPSHOT_OPERATION, _ACCOUNT_INSTRUMENT_METADATA_OPERATION}
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
        return _parse_instrument_metadata_reply(
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
        schema_version = payload.get("schema_version")
        if (
            payload.get("schema_id") != "sdk.account.state"
            or type(schema_version) is not int
            or schema_version not in {1, 2}
        ):
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
        has_open_orders = "open_orders" in observation_raw
        has_open_orders_complete = "open_orders_complete" in observation_raw
        if schema_version == 1 and (has_open_orders or has_open_orders_complete):
            self._reject("ACCOUNT_STATE_SCHEMA_MISMATCH")
        if schema_version == 2 and not (has_open_orders and has_open_orders_complete):
            self._reject("ACCOUNT_STATE_SCHEMA_MISMATCH")
        try:
            observation = AccountObservation.from_dict(observation_raw)
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
        required_operations: frozenset[str] = frozenset({_ACCOUNT_SNAPSHOT_OPERATION}),
    ) -> ServiceAccountBindingView:
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


__all__ = [
    "AccountStateView",
    "ServiceAccountClient",
    "ServiceAccountError",
    "ServiceAccountInstrumentMetadata",
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


def _parse_instrument_metadata_reply(
    reply: object,
    *,
    instrument_id: str,
    exchange_id: str,
    account_session_generation: str,
    max_elapsed_seconds: float,
) -> ServiceAccountInstrumentMetadata:
    if not isinstance(reply, Mapping) or set(reply) != {"metadata"}:
        raise ServiceAccountError("INVALID_INSTRUMENT_METADATA_REPLY")
    value = reply.get("metadata")
    if not isinstance(value, Mapping) or set(value) != _INSTRUMENT_METADATA_FIELDS:
        raise ServiceAccountError("INVALID_INSTRUMENT_METADATA_REPLY")
    if (
        value.get("schema_id") != _INSTRUMENT_METADATA_SCHEMA_ID
        or type(value.get("schema_version")) is not int
        or value.get("schema_version") != _INSTRUMENT_METADATA_SCHEMA_VERSION
    ):
        raise ServiceAccountError("INVALID_INSTRUMENT_METADATA_SCHEMA")

    product_class = value.get("product_class")
    asset_type = value.get("asset_type")
    product_to_asset = {
        "1": "future",
        "2": "option",
        "3": "combination",
        "4": "spot",
        "5": "efp",
        "6": "spot_option",
        "7": "tas",
        "I": "mi",
    }
    if (
        type(value.get("instrument_id")) is not str
        or value["instrument_id"] != instrument_id
        or type(value.get("exchange_id")) is not str
        or value["exchange_id"] != exchange_id
        or type(product_class) is not str
        or product_class not in product_to_asset
        or asset_type != product_to_asset[product_class]
    ):
        raise ServiceAccountError("INSTRUMENT_METADATA_IDENTITY_MISMATCH")

    price_tick = value.get("price_tick")
    _validate_positive_decimal_text(price_tick, "INVALID_INSTRUMENT_METADATA")
    volume_multiple = value.get("volume_multiple")
    if type(volume_multiple) is not int or volume_multiple <= 0:
        raise ServiceAccountError("INVALID_INSTRUMENT_METADATA")

    limits_available = value.get("price_limits_available")
    lower = value.get("lower_limit_price")
    upper = value.get("upper_limit_price")
    limits_source = value.get("price_limits_source")
    if type(limits_available) is not bool or type(limits_source) is not str:
        raise ServiceAccountError("INVALID_INSTRUMENT_METADATA")
    if limits_available:
        if limits_source != "ctp.native.instruments":
            raise ServiceAccountError("INVALID_INSTRUMENT_METADATA")
        _validate_positive_decimal_text(lower, "INVALID_INSTRUMENT_METADATA")
        _validate_positive_decimal_text(upper, "INVALID_INSTRUMENT_METADATA")
        if Decimal(lower) >= Decimal(upper):
            raise ServiceAccountError("INVALID_INSTRUMENT_METADATA")
    elif (
        lower is not None
        or upper is not None
        or limits_source != "unavailable_in_instrument_query"
    ):
        raise ServiceAccountError("INVALID_INSTRUMENT_METADATA")

    generation = value.get("account_session_generation")
    account_fingerprint = value.get("account_fingerprint")
    trading_day = value.get("trading_day")
    if (
        type(trading_day) is str
        and len(trading_day) == 8
        and trading_day.isascii()
        and trading_day.isdigit()
    ):
        try:
            valid_trading_day = (
                datetime.strptime(trading_day, "%Y%m%d").strftime("%Y%m%d")
                == trading_day
            )
        except ValueError:
            valid_trading_day = False
    else:
        valid_trading_day = False
    if (
        type(generation) is not str
        or generation != account_session_generation
        or type(account_fingerprint) is not str
        or not _SHA256_PATTERN.fullmatch(account_fingerprint)
        or not valid_trading_day
    ):
        raise ServiceAccountError("ACCOUNT_GENERATION_MISMATCH")

    source_connection_generation = value.get("source_connection_generation")
    source_request_id = value.get("source_request_id")
    quote_stream_generation = value.get("quote_stream_generation")
    if any(
        type(item) is not int or item <= 0
        for item in (source_connection_generation, source_request_id, quote_stream_generation)
    ):
        raise ServiceAccountError("INVALID_INSTRUMENT_METADATA")
    source_digest = value.get("source_records_sha256")
    if type(source_digest) is not str or not _SHA256_PATTERN.fullmatch(source_digest):
        raise ServiceAccountError("INVALID_INSTRUMENT_METADATA")
    if (
        value.get("source_revision") != "ctp.native.instrument-query.v1"
        or value.get("source") != "ctp.native.instruments"
    ):
        raise ServiceAccountError("INVALID_INSTRUMENT_METADATA")

    query_started = _parse_utc_timestamp(value.get("query_started_at_utc"))
    observed = _parse_utc_timestamp(value.get("observed_at_utc"))
    if observed < query_started:
        raise ServiceAccountError("INVALID_INSTRUMENT_METADATA")
    elapsed = value.get("query_elapsed_seconds")
    if (
        type(elapsed) not in (int, float)
        or isinstance(elapsed, bool)
        or not math.isfinite(elapsed)
        or elapsed < 0
        or elapsed > max_elapsed_seconds
    ):
        raise ServiceAccountError("INVALID_INSTRUMENT_METADATA")
    query_domain = value.get("query_clock_domain_id")
    quote_source = value.get("quote_source")
    clock_domain = value.get("clock_domain_id")
    clock_session = value.get("clock_source_session_id")
    clock_source = value.get("clock_source")
    domain_match = (
        re.fullmatch(r"ctp-md-process:[1-9][0-9]*:monotonic:([0-9a-f]{32})", clock_domain)
        if type(clock_domain) is str
        else None
    )
    if any(
        type(item) is not str or not item or item != item.strip()
        for item in (query_domain, quote_source, clock_domain, clock_session, clock_source)
    ) or (
        len(clock_source) > 128
        or query_domain != "python.time.monotonic"
        or quote_source != "ctp.native.md"
        or domain_match is None
        or domain_match.group(1) != clock_session
    ):
        raise ServiceAccountError("INVALID_INSTRUMENT_METADATA")

    return ServiceAccountInstrumentMetadata(
        instrument_id=instrument_id,
        exchange_id=exchange_id,
        product_class=product_class,
        asset_type=asset_type,
        price_tick=price_tick,
        volume_multiple=volume_multiple,
        lower_limit_price=lower,
        upper_limit_price=upper,
        price_limits_available=limits_available,
        price_limits_source=limits_source,
        account_session_generation=generation,
        account_fingerprint=account_fingerprint,
        trading_day=trading_day,
        source_connection_generation=source_connection_generation,
        source_request_id=source_request_id,
        source_records_sha256=source_digest,
        source_revision="ctp.native.instrument-query.v1",
        source="ctp.native.instruments",
        query_started_at_utc=value["query_started_at_utc"],
        observed_at_utc=value["observed_at_utc"],
        query_clock_domain_id=query_domain,
        query_elapsed_seconds=float(elapsed),
        quote_stream_generation=quote_stream_generation,
        quote_source=quote_source,
        clock_domain_id=clock_domain,
        clock_source_session_id=clock_session,
        clock_source=value["clock_source"],
    )


def _validate_positive_decimal_text(value: object, code: str) -> None:
    if type(value) is not str:
        raise ServiceAccountError(code)
    try:
        decimal_value = Decimal(value)
    except (InvalidOperation, ValueError):
        raise ServiceAccountError(code) from None
    if not decimal_value.is_finite() or decimal_value <= 0:
        raise ServiceAccountError(code)


def _parse_utc_timestamp(value: object) -> datetime:
    if type(value) is not str:
        raise ServiceAccountError("INVALID_INSTRUMENT_METADATA")
    try:
        parsed = datetime.fromisoformat(value)
    except ValueError:
        raise ServiceAccountError("INVALID_INSTRUMENT_METADATA") from None
    if parsed.tzinfo is None or parsed.utcoffset() != timezone.utc.utcoffset(parsed):
        raise ServiceAccountError("INVALID_INSTRUMENT_METADATA")
    return parsed
