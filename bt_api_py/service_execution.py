"""Managed order submission and cancellation over an authenticated service.

The facade borrows an already-started ``ServiceClientRuntime``; it creates no
thread, never registers or unregisters, never closes the runtime, and never
retries a request whose outcome is unknown.  The execution scope, strategy
attribution and provider session are resolved by the service from the
authenticated ``WireScope``; this client sends order content only.

A returned view always reflects a durable service record.  ``UNKNOWN`` means
the provider may have seen the request: query the record and reconcile it with
provider evidence instead of submitting the same intent again.
"""

from __future__ import annotations

import threading
from collections.abc import Mapping
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation
from types import MappingProxyType
from typing import Any

from bt_api_py.service_deployment import MOUNT_PORTS
from bt_api_py.service_runtime import (
    ServiceCallOutcomeUnknownError,
    ServiceClientRuntime,
    ServiceRuntimeError,
    ServiceRuntimeRequestNotSentError,
)

SUBMIT_OPERATION = "orders.submit"
CANCEL_OPERATION = "orders.cancel"
QUERY_OPERATION = "orders.query"

_ORDER_ENTRY_PORT = "service.order_entry"
_ORDER_QUERY_PORT = "service.order_query"
_ACCOUNT_BOOKS_QUERY_OPERATION = "execution.account_books.query"
_REFERENCE_RE_LENGTH = 128
_RECOVERY_PAGE_MAX = 128

_SIDES = frozenset({"BUY", "SELL"})
_POSITION_EFFECTS = frozenset({"OPEN", "CLOSE", "CLOSE_TODAY", "CLOSE_YESTERDAY"})
_ORDER_TYPES = frozenset({"MARKET", "LIMIT"})
_TIME_IN_FORCE = frozenset({"GTC", "IOC", "FOK", "DAY"})

_SUBMIT_FIELDS = frozenset(
    {
        "intent_id",
        "signal_id",
        "instrument",
        "side",
        "position_effect",
        "order_type",
        "quantity",
        "price",
        "time_in_force",
        "reduce_only",
        "metadata_version",
        "deadline_ns",
        "tags",
    }
)
_CANCEL_FIELDS = frozenset(
    {
        "cancel_id",
        "target_intent_id",
        "provider_order_id",
        "metadata_version",
        "tags",
    }
)
_ORDER_RECORD_FIELDS = frozenset(
    {
        "intent_id",
        "scope_key",
        "payload_sha256",
        "state",
        "provider_order_id",
        "filled_quantity",
        "average_price",
        "cumulative_commission",
        "permit_reference",
        "dispatch_attempts",
        "unknown_reason",
        "review_required",
        "created_at_ns",
        "updated_at_ns",
        "is_terminal",
    }
)
_CANCEL_RECORD_FIELDS = frozenset(
    {
        "cancel_id",
        "scope_key",
        "target_intent_id",
        "provider_order_id",
        "payload_sha256",
        "state",
        "permit_reference",
        "dispatch_attempts",
        "unknown_reason",
        "review_required",
        "created_at_ns",
        "updated_at_ns",
        "is_terminal",
    }
)
_REPLY_FIELDS = frozenset({"ok", "code", "result"})
_RESULT_FIELDS = frozenset({"found", "record"})


class ServiceExecutionError(RuntimeError):
    """Finite local or remote execution diagnostics without remote secrets."""

    def __init__(self, code: str, record: OrderRecordView | CancelRecordView | None = None) -> None:
        self.code = code
        self.record = record
        super().__init__(code)


class ServiceExecutionUnknownOutcomeError(ServiceExecutionError):
    """The request may have reached the provider; reconcile before retrying.

    The intent or cancellation identifier is carried so the caller can query
    the durable record.  Resubmitting the same identifier is safe only after
    the recorded state proves that no dispatch happened.
    """

    def __init__(self, code: str, identifier: str) -> None:
        super().__init__(code)
        self.identifier = identifier


@dataclass(frozen=True)
class OrderRecordView:
    """One detached durable execution record."""

    intent_id: str
    scope_key: str
    state: str
    provider_order_id: str | None
    filled_quantity: Decimal
    average_price: Decimal | None
    cumulative_commission: Decimal | None
    permit_reference: str | None
    dispatch_attempts: int
    unknown_reason: str | None
    review_required: bool
    is_terminal: bool
    created_at_ns: int
    updated_at_ns: int

    @property
    def is_unknown(self) -> bool:
        """Return whether the provider outcome still requires reconciliation."""

        return self.state == "UNKNOWN"


@dataclass(frozen=True)
class CancelRecordView:
    """One detached durable cancellation record."""

    cancel_id: str
    scope_key: str
    target_intent_id: str
    provider_order_id: str
    state: str
    permit_reference: str | None
    dispatch_attempts: int
    unknown_reason: str | None
    review_required: bool
    is_terminal: bool
    created_at_ns: int
    updated_at_ns: int

    @property
    def is_unknown(self) -> bool:
        """Return whether the cancellation outcome still requires reconciliation."""

        return self.state == "UNKNOWN"


class ServiceExecutionClient:
    """Submit, cancel and query managed orders through one runtime binding.

    ``mount`` must be an order-entry mount.  ``query_mount`` and
    ``query_scope_ref`` are optional; without them :meth:`query` raises a
    local configuration error instead of guessing a route.
    """

    def __init__(
        self,
        runtime: ServiceClientRuntime,
        mount: str,
        *,
        scope_ref: str,
        query_mount: str | None = None,
        query_scope_ref: str | None = None,
    ) -> None:
        if not isinstance(runtime, ServiceClientRuntime):
            raise ServiceExecutionError("INVALID_RUNTIME")
        if type(mount) is not str or mount not in MOUNT_PORTS:
            raise ServiceExecutionError("INVALID_EXECUTION_MOUNT")
        if _ORDER_ENTRY_PORT not in MOUNT_PORTS[mount]:
            raise ServiceExecutionError("INVALID_EXECUTION_MOUNT")
        if not _reference(scope_ref):
            raise ServiceExecutionError("INVALID_SCOPE_REF")
        if (query_mount is None) != (query_scope_ref is None):
            raise ServiceExecutionError("INCOMPLETE_QUERY_ROUTE")
        if query_mount is not None:
            if type(query_mount) is not str or query_mount not in MOUNT_PORTS:
                raise ServiceExecutionError("INVALID_QUERY_MOUNT")
            if _ORDER_QUERY_PORT not in MOUNT_PORTS[query_mount]:
                raise ServiceExecutionError("INVALID_QUERY_MOUNT")
            if not _reference(query_scope_ref):
                raise ServiceExecutionError("INVALID_SCOPE_REF")

        self._runtime = runtime
        self._mount = mount
        self._scope_ref = scope_ref
        self._query_mount = query_mount
        self._query_scope_ref = query_scope_ref
        self._owner_thread_id = threading.get_ident()

    @property
    def mount(self) -> str:
        return self._mount

    @property
    def scope_ref(self) -> str:
        return self._scope_ref

    def submit(
        self,
        *,
        intent_id: str,
        signal_id: str,
        instrument: str,
        side: str,
        position_effect: str,
        order_type: str,
        quantity: Decimal | int | str,
        price: Decimal | int | str | None = None,
        time_in_force: str = "GTC",
        reduce_only: bool = False,
        metadata_version: str = "unknown",
        deadline_ns: int | None = None,
        tags: Mapping[str, str] | None = None,
    ) -> OrderRecordView:
        """Submit one intent and return its durable record.

        Repeating an identical intent returns the same record without a second
        dispatch.  A reused identifier with different content raises a
        conflict that carries the existing record.
        """

        arguments = _submit_arguments(
            intent_id=intent_id,
            signal_id=signal_id,
            instrument=instrument,
            side=side,
            position_effect=position_effect,
            order_type=order_type,
            quantity=quantity,
            price=price,
            time_in_force=time_in_force,
            reduce_only=reduce_only,
            metadata_version=metadata_version,
            deadline_ns=deadline_ns,
            tags=tags,
        )
        result = self._call(SUBMIT_OPERATION, arguments, identifier=intent_id)
        return _order_record(result, identifier=intent_id)

    def cancel(
        self,
        *,
        cancel_id: str,
        target_intent_id: str,
        provider_order_id: str,
        metadata_version: str = "unknown",
        tags: Mapping[str, str] | None = None,
    ) -> CancelRecordView:
        """Cancel one order the service journal already evidenced."""

        arguments = _cancel_arguments(
            cancel_id=cancel_id,
            target_intent_id=target_intent_id,
            provider_order_id=provider_order_id,
            metadata_version=metadata_version,
            tags=tags,
        )
        result = self._call(CANCEL_OPERATION, arguments, identifier=cancel_id)
        return _cancel_record(result, identifier=cancel_id)

    def query(self, intent_id: str) -> OrderRecordView | None:
        """Read one durable order record without creating provider activity."""

        self._assert_owner_thread()
        if self._query_mount is None or self._query_scope_ref is None:
            raise ServiceExecutionError("QUERY_NOT_CONFIGURED")
        if not _reference(intent_id):
            raise ServiceExecutionError("INVALID_INTENT_ID")
        payload = self._request(
            self._query_mount,
            QUERY_OPERATION,
            {"intent_id": intent_id},
            self._query_scope_ref,
            identifier=intent_id,
        )
        if payload.get("found") is not True:
            return None
        return _order_record(payload, identifier=intent_id)

    def query_recovery_snapshot(self) -> Mapping[str, Any]:
        """Read a versioned durable snapshot through the configured query route.

        The returned mapping is the authenticated operation's ``result`` value.
        It describes one SQLite execution-journal cut, not provider-live state.
        """

        self._assert_owner_thread()
        query_mount, query_scope_ref = self._require_query_route()
        return self._request(
            query_mount,
            _ACCOUNT_BOOKS_QUERY_OPERATION,
            {"view": "RECOVERY_SNAPSHOT", "version": 1},
            query_scope_ref,
            identifier="execution-recovery-snapshot",
            result_kind="account_books",
        )

    def query_recovery_events_after(
        self,
        *,
        after_sequence: int,
        expected_journal_generation: str,
        expected_directory_revision: str,
        limit: int,
    ) -> Mapping[str, Any]:
        """Read a bounded page pinned to the supplied durable journal cut."""

        self._assert_owner_thread()
        if type(after_sequence) is not int or after_sequence < 0:
            raise ServiceExecutionError("INVALID_RECOVERY_CURSOR")
        if (
            type(expected_journal_generation) is not str
            or len(expected_journal_generation) != 32
            or any(character not in "0123456789abcdef" for character in expected_journal_generation)
        ):
            raise ServiceExecutionError("INVALID_RECOVERY_GENERATION")
        if not _reference(expected_directory_revision):
            raise ServiceExecutionError("INVALID_RECOVERY_DIRECTORY_REVISION")
        if type(limit) is not int or not 1 <= limit <= _RECOVERY_PAGE_MAX:
            raise ServiceExecutionError("INVALID_RECOVERY_LIMIT")
        query_mount, query_scope_ref = self._require_query_route()
        return self._request(
            query_mount,
            _ACCOUNT_BOOKS_QUERY_OPERATION,
            {
                "view": "EVENTS_AFTER",
                "version": 1,
                "after_sequence": after_sequence,
                "expected_journal_generation": expected_journal_generation,
                "expected_directory_revision": expected_directory_revision,
                "limit": limit,
            },
            query_scope_ref,
            identifier="execution-recovery-events",
            result_kind="account_books",
        )

    def _require_query_route(self) -> tuple[str, str]:
        if self._query_mount is None or self._query_scope_ref is None:
            raise ServiceExecutionError("QUERY_NOT_CONFIGURED")
        return self._query_mount, self._query_scope_ref

    def _assert_owner_thread(self) -> None:
        if threading.get_ident() != self._owner_thread_id:
            raise ServiceExecutionError("WRONG_OWNER_THREAD")

    def _call(
        self, operation: str, arguments: Mapping[str, Any], *, identifier: str
    ) -> Mapping[str, Any]:
        self._assert_owner_thread()
        payload = self._request(
            self._mount, operation, arguments, self._scope_ref, identifier=identifier
        )
        if payload.get("found") is not True:
            raise ServiceExecutionError("RECORD_MISSING")
        return payload

    def _request(
        self,
        mount: str,
        operation: str,
        arguments: Mapping[str, Any],
        scope_ref: str,
        *,
        identifier: str,
        result_kind: str = "order",
    ) -> Mapping[str, Any]:
        try:
            reply = self._runtime.call(mount, operation, arguments, scope_ref=scope_ref)
        except ServiceRuntimeRequestNotSentError:
            # Provably never sent; the caller may safely rebuild the request.
            raise ServiceExecutionError("REQUEST_NOT_SENT") from None
        except ServiceCallOutcomeUnknownError:
            raise ServiceExecutionUnknownOutcomeError(
                "BUSINESS_OUTCOME_UNKNOWN", identifier
            ) from None
        except ServiceRuntimeError as error:
            raise ServiceExecutionError(getattr(error, "code", "REMOTE_REQUEST_REJECTED")) from None
        except ServiceExecutionError:
            raise
        except Exception:
            # Any other transport failure may already have reached the service.
            raise ServiceExecutionUnknownOutcomeError(
                "TRANSPORT_OUTCOME_UNKNOWN", identifier
            ) from None
        if result_kind == "account_books":
            return _account_books_reply_payload(reply)
        if result_kind != "order":
            raise ServiceExecutionError("INVALID_REPLY")
        return _reply_payload(reply, identifier=identifier)


def _submit_arguments(
    *,
    intent_id: object,
    signal_id: object,
    instrument: object,
    side: object,
    position_effect: object,
    order_type: object,
    quantity: object,
    price: object,
    time_in_force: object,
    reduce_only: object,
    metadata_version: object,
    deadline_ns: object,
    tags: object,
) -> Mapping[str, Any]:
    if not _reference(intent_id):
        raise ServiceExecutionError("INVALID_INTENT_ID")
    if not _reference(signal_id):
        raise ServiceExecutionError("INVALID_SIGNAL_ID")
    if not _instrument(instrument):
        raise ServiceExecutionError("INVALID_INSTRUMENT")
    if side not in _SIDES:
        raise ServiceExecutionError("INVALID_SIDE")
    if position_effect not in _POSITION_EFFECTS:
        raise ServiceExecutionError("INVALID_POSITION_EFFECT")
    if order_type not in _ORDER_TYPES:
        raise ServiceExecutionError("INVALID_ORDER_TYPE")
    if time_in_force not in _TIME_IN_FORCE:
        raise ServiceExecutionError("INVALID_TIME_IN_FORCE")
    if type(reduce_only) is not bool:
        raise ServiceExecutionError("INVALID_REDUCE_ONLY")
    if not _reference(metadata_version):
        raise ServiceExecutionError("INVALID_METADATA_VERSION")
    if deadline_ns is not None and (type(deadline_ns) is not int or deadline_ns <= 0):
        raise ServiceExecutionError("INVALID_DEADLINE")
    normalized_quantity = _decimal_text(quantity, "INVALID_QUANTITY")
    if order_type == "LIMIT":
        normalized_price = _decimal_text(price, "INVALID_PRICE")
    else:
        if price is not None:
            raise ServiceExecutionError("MARKET_INTENT_MUST_NOT_CARRY_PRICE")
        normalized_price = None
    return MappingProxyType(
        {
            "intent_id": intent_id,
            "signal_id": signal_id,
            "instrument": instrument,
            "side": side,
            "position_effect": position_effect,
            "order_type": order_type,
            "quantity": normalized_quantity,
            "price": normalized_price,
            "time_in_force": time_in_force,
            "reduce_only": reduce_only,
            "metadata_version": metadata_version,
            "deadline_ns": deadline_ns,
            "tags": _tags(tags),
        }
    )


def _cancel_arguments(
    *,
    cancel_id: object,
    target_intent_id: object,
    provider_order_id: object,
    metadata_version: object,
    tags: object,
) -> Mapping[str, Any]:
    for value, code in (
        (cancel_id, "INVALID_CANCEL_ID"),
        (target_intent_id, "INVALID_INTENT_ID"),
        (provider_order_id, "INVALID_PROVIDER_ORDER_ID"),
        (metadata_version, "INVALID_METADATA_VERSION"),
    ):
        if not _reference(value):
            raise ServiceExecutionError(code)
    return MappingProxyType(
        {
            "cancel_id": cancel_id,
            "target_intent_id": target_intent_id,
            "provider_order_id": provider_order_id,
            "metadata_version": metadata_version,
            "tags": _tags(tags),
        }
    )


def _tags(value: object) -> dict[str, str]:
    if value is None:
        return {}
    if not isinstance(value, Mapping):
        raise ServiceExecutionError("INVALID_TAGS")
    normalized: dict[str, str] = {}
    for key, item in value.items():
        if not _reference(key) or type(item) is not str:
            raise ServiceExecutionError("INVALID_TAGS")
        if len(item) > 256 or item != item.strip():
            raise ServiceExecutionError("INVALID_TAGS")
        normalized[key] = item
    return normalized


def _decimal_text(value: object, code: str) -> str:
    if isinstance(value, bool) or not isinstance(value, (int, str, Decimal)):
        raise ServiceExecutionError(code)
    try:
        number = Decimal(value)
    except (InvalidOperation, ValueError):
        raise ServiceExecutionError(code) from None
    if not number.is_finite() or number <= 0:
        raise ServiceExecutionError(code)
    return format(number, "f")


def _reply_payload(reply: object, *, identifier: str) -> Mapping[str, Any]:
    if not isinstance(reply, Mapping) or set(reply) != _REPLY_FIELDS:
        raise ServiceExecutionError("INVALID_REPLY")
    code = reply.get("code")
    if type(code) is not str or not code or len(code) > 64:
        raise ServiceExecutionError("INVALID_REPLY")
    result = reply.get("result")
    if not isinstance(result, Mapping) or set(result) != _RESULT_FIELDS:
        raise ServiceExecutionError("INVALID_REPLY")
    if reply.get("ok") is True:
        if code != "OK":
            raise ServiceExecutionError("INVALID_REPLY")
        return result
    if reply.get("ok") is not False:
        raise ServiceExecutionError("INVALID_REPLY")
    record = result.get("record")
    if code == "UNKNOWN_OPERATION":
        raise ServiceExecutionError(code)
    if result.get("found") is True and isinstance(record, Mapping):
        raise ServiceExecutionError(code, _any_record(record))
    raise ServiceExecutionError(code)


def _account_books_reply_payload(reply: object) -> Mapping[str, Any]:
    """Unwrap one successful account-books operation without projecting its view."""

    if not isinstance(reply, Mapping) or set(reply) != _REPLY_FIELDS:
        raise ServiceExecutionError("INVALID_REPLY")
    code = reply.get("code")
    result = reply.get("result")
    if type(code) is not str or not code or len(code) > 64 or not isinstance(result, Mapping):
        raise ServiceExecutionError("INVALID_REPLY")
    if reply.get("ok") is True:
        if code != "OK":
            raise ServiceExecutionError("INVALID_REPLY")
        return result
    if reply.get("ok") is not False or code == "OK":
        raise ServiceExecutionError("INVALID_REPLY")
    raise ServiceExecutionError(code)


def _order_record(payload: Mapping[str, Any], *, identifier: str) -> OrderRecordView:
    record = payload.get("record")
    if not isinstance(record, Mapping) or set(record) != _ORDER_RECORD_FIELDS:
        raise ServiceExecutionError("INVALID_REPLY")
    view = _order_view(record)
    if view.intent_id != identifier:
        raise ServiceExecutionError("INVALID_REPLY")
    return view


def _cancel_record(payload: Mapping[str, Any], *, identifier: str) -> CancelRecordView:
    record = payload.get("record")
    if not isinstance(record, Mapping) or set(record) != _CANCEL_RECORD_FIELDS:
        raise ServiceExecutionError("INVALID_REPLY")
    view = _cancel_view(record)
    if view.cancel_id != identifier:
        raise ServiceExecutionError("INVALID_REPLY")
    return view


def _any_record(record: Mapping[str, Any]) -> OrderRecordView | CancelRecordView:
    keys = set(record)
    if keys == _ORDER_RECORD_FIELDS:
        return _order_view(record)
    if keys == _CANCEL_RECORD_FIELDS:
        return _cancel_view(record)
    raise ServiceExecutionError("INVALID_REPLY")


def _order_view(record: Mapping[str, Any]) -> OrderRecordView:
    return OrderRecordView(
        intent_id=_text(record["intent_id"]),
        scope_key=_text(record["scope_key"]),
        state=_state(record["state"]),
        provider_order_id=_optional_text(record["provider_order_id"]),
        filled_quantity=_decimal(record["filled_quantity"]),
        average_price=_optional_decimal(record["average_price"]),
        cumulative_commission=_optional_decimal(record["cumulative_commission"]),
        permit_reference=_optional_text(record["permit_reference"]),
        dispatch_attempts=_counter(record["dispatch_attempts"]),
        unknown_reason=_optional_text(record["unknown_reason"]),
        review_required=_flag(record["review_required"]),
        is_terminal=_flag(record["is_terminal"]),
        created_at_ns=_counter(record["created_at_ns"]),
        updated_at_ns=_counter(record["updated_at_ns"]),
    )


def _cancel_view(record: Mapping[str, Any]) -> CancelRecordView:
    return CancelRecordView(
        cancel_id=_text(record["cancel_id"]),
        scope_key=_text(record["scope_key"]),
        target_intent_id=_text(record["target_intent_id"]),
        provider_order_id=_text(record["provider_order_id"]),
        state=_state(record["state"]),
        permit_reference=_optional_text(record["permit_reference"]),
        dispatch_attempts=_counter(record["dispatch_attempts"]),
        unknown_reason=_optional_text(record["unknown_reason"]),
        review_required=_flag(record["review_required"]),
        is_terminal=_flag(record["is_terminal"]),
        created_at_ns=_counter(record["created_at_ns"]),
        updated_at_ns=_counter(record["updated_at_ns"]),
    )


def _text(value: object) -> str:
    if type(value) is not str or not value or len(value) > 256:
        raise ServiceExecutionError("INVALID_REPLY")
    return value


def _optional_text(value: object) -> str | None:
    return None if value is None else _text(value)


def _state(value: object) -> str:
    state = _text(value)
    if state != state.upper():
        raise ServiceExecutionError("INVALID_REPLY")
    return state


def _decimal(value: object) -> Decimal:
    text = _text(value)
    try:
        number = Decimal(text)
    except InvalidOperation:
        raise ServiceExecutionError("INVALID_REPLY") from None
    if not number.is_finite():
        raise ServiceExecutionError("INVALID_REPLY")
    return number


def _optional_decimal(value: object) -> Decimal | None:
    return None if value is None else _decimal(value)


def _counter(value: object) -> int:
    if type(value) is not int or value < 0:
        raise ServiceExecutionError("INVALID_REPLY")
    return value


def _flag(value: object) -> bool:
    if type(value) is not bool:
        raise ServiceExecutionError("INVALID_REPLY")
    return value


def _instrument(value: object) -> bool:
    if type(value) is not str or not 1 <= len(value) <= 192:
        return False
    if not value[0].isalnum() or not value[0].isascii():
        return False
    return all(
        character.isascii() and (character.isalnum() or character in "._:/-") for character in value
    )


def _reference(value: object) -> bool:
    if type(value) is not str or not value or len(value) > _REFERENCE_RE_LENGTH:
        return False
    if value != value.strip():
        return False
    return all(character.isalnum() or character in "._:-" for character in value)


__all__ = [
    "CANCEL_OPERATION",
    "CancelRecordView",
    "OrderRecordView",
    "QUERY_OPERATION",
    "SUBMIT_OPERATION",
    "ServiceExecutionClient",
    "ServiceExecutionError",
    "ServiceExecutionUnknownOutcomeError",
]
