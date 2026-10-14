"""Crash-safe local projection over a tracked account-read service stream.

The caller owns and maintains the registered :class:`ServiceClientRuntime`.
This consumer borrows that runtime and owns only its SQLite projection store.
Reducers are local projection functions; they must be deterministic and have
no external side effects because an unacknowledged committed batch is replayed
and recovered from the durable cursor after a process restart.
"""

from __future__ import annotations

import hashlib
import json
import math
import threading
from collections.abc import Callable, Mapping
from pathlib import Path
from typing import Any

from bt_api_base.service_protocol import MAX_REFERENCE_BYTES, WireIdentity, WireScope

from bt_api_py.service_account_projection import (
    AccountProjectionCheckpoint,
    AccountProjectionStore,
)
from bt_api_py.service_deployment import MOUNT_PORTS
from bt_api_py.service_runtime import (
    ServiceAccountBindingView,
    ServiceClientRuntime,
    ServiceRuntimeError,
)

_ACCOUNT_READ_PORT = "service.account_read"
_REQUIRED_OPERATIONS = frozenset({"account.read_after", "account.ack", "account.progress"})
_MAX_BATCH_SIZE = 1000
_MAX_JSON_DEPTH = 64
_MAX_INITIAL_STATE_BYTES = 1024 * 1024
_MAX_INITIAL_STATE_NODES = 100_000
_MAX_EPOCH_BYTES = 128
_MAX_SQLITE_CURSOR = (1 << 63) - 1


class ServiceAccountConsumerError(RuntimeError):
    """Finite consumer diagnostics that never include remote values."""

    def __init__(self, code: str) -> None:
        self.code = code
        super().__init__(code)


class ServiceAccountConsumer:
    """Persist a local account projection and ACK only committed cursors.

    ``runtime`` must already be registered with explicit grants for tracked
    account reads, ACK, and progress. The consumer never starts, closes, or
    maintains it; callers retain those lifecycle responsibilities. Construct a
    new consumer with the same SQLite path after restart. Registration and
    service epochs and the scope's ``run_id`` may change across restarts, while
    service, account, strategy, principal, actor, and delegate bindings may
    not.
    """

    def __init__(
        self,
        runtime: ServiceClientRuntime,
        mount: str,
        *,
        account_ref: str,
        path: str | Path,
        projection_version: str,
        initial_state: Mapping[str, Any],
        expected_account_scope: str,
        expected_strategy_scope: str,
        snapshot_reducer: Callable[..., Mapping[str, Any]],
        event_reducer: Callable[..., Mapping[str, Any]],
        limit: int = 100,
    ) -> None:
        if not isinstance(runtime, ServiceClientRuntime):
            raise ServiceAccountConsumerError("INVALID_RUNTIME")
        if (
            type(mount) is not str
            or mount not in MOUNT_PORTS
            or _ACCOUNT_READ_PORT not in MOUNT_PORTS[mount]
        ):
            raise ServiceAccountConsumerError("INVALID_ACCOUNT_MOUNT")
        if not _valid_text(account_ref):
            raise ServiceAccountConsumerError("INVALID_ACCOUNT_REF")
        if not _valid_text(projection_version):
            raise ServiceAccountConsumerError("INVALID_PROJECTION_VERSION")
        if not _valid_text(expected_account_scope):
            raise ServiceAccountConsumerError("INVALID_EXPECTED_ACCOUNT_SCOPE")
        if not _valid_text(expected_strategy_scope):
            raise ServiceAccountConsumerError("INVALID_EXPECTED_STRATEGY_SCOPE")
        if type(limit) is not int or not 1 <= limit <= _MAX_BATCH_SIZE:
            raise ServiceAccountConsumerError("INVALID_BATCH_LIMIT")
        if not callable(snapshot_reducer) or not callable(event_reducer):
            raise ServiceAccountConsumerError("INVALID_REDUCER")

        copied_initial_state = _copy_json_mapping(initial_state)
        initial_state_bytes = _canonical_json(copied_initial_state)
        try:
            binding = runtime.account_binding_view(
                mount,
                account_ref,
                required_operations=_REQUIRED_OPERATIONS,
            )
        except Exception:
            raise ServiceAccountConsumerError("ACCOUNT_BINDING_UNAVAILABLE") from None

        self._runtime = runtime
        self._mount = mount
        self._account_ref = account_ref
        self._scope_ref = binding.scope_ref
        self._limit = limit
        self._snapshot_reducer = snapshot_reducer
        self._event_reducer = event_reducer
        self._owner_thread_id = threading.get_ident()
        self._closed = False
        self._polling = False
        self._stable_binding = _stable_binding(
            binding,
            runtime.identity,
            expected_account_scope=expected_account_scope,
            expected_strategy_scope=expected_strategy_scope,
            projection_version=projection_version,
            initial_state_sha256=hashlib.sha256(initial_state_bytes).hexdigest(),
        )
        try:
            self._store = AccountProjectionStore(
                path,
                binding=self._stable_binding,
                initial_state=copied_initial_state,
            )
        except Exception:
            raise ServiceAccountConsumerError("PROJECTION_STORE_OPEN_FAILED") from None

    def checkpoint(self) -> AccountProjectionCheckpoint:
        """Return the durable cursor and detached projection without RPCs."""

        self._assert_open_owner()
        try:
            return self._store.checkpoint()
        except Exception:
            raise ServiceAccountConsumerError("PROJECTION_STORE_FAILED") from None

    def poll(self) -> AccountProjectionCheckpoint:
        """Apply at most one tracked batch and confirm its durable cursor ACK.

        A restart first confirms any durable pending ACK before reading again.
        If an ACK response is uncertain, the local pending state is preserved
        and the next explicit call repeats that same ACK before another read.
        No remote progress response is allowed to move the local cursor.
        """

        self._assert_open_owner()
        if self._polling:
            raise ServiceAccountConsumerError("POLL_REENTRANT")
        self._polling = True
        try:
            poll_signature = self._current_binding()
            checkpoint = self._read_checkpoint()
            if checkpoint.pending_ack:
                checkpoint = self._acknowledge(checkpoint, poll_signature)

            requested_cursor = checkpoint.cursor if checkpoint.stream_epoch is not None else None
            arguments: dict[str, Any] = {
                "cursor": requested_cursor,
                "limit": self._limit,
                "track_delivery": True,
            }
            if requested_cursor is not None:
                arguments["stream_epoch"] = checkpoint.stream_epoch
            reply = self._call(
                "account.read_after",
                arguments,
                expected_signature=poll_signature,
            )
            if reply.get("requested_cursor", _MISSING) != requested_cursor:
                raise ServiceAccountConsumerError("REQUEST_CURSOR_MISMATCH")
            progress = _read_progress(reply)
            self._validate_progress(
                progress,
                expected_epoch=checkpoint.stream_epoch,
                minimum_cursor=checkpoint.cursor,
            )
            self._current_binding(expected_signature=poll_signature)
            try:
                applied = self._store.apply(
                    reply,
                    requested_cursor=requested_cursor,
                    snapshot_reducer=self._snapshot_reducer,
                    event_reducer=self._event_reducer,
                )
            except Exception:
                raise ServiceAccountConsumerError("PROJECTION_APPLY_FAILED") from None
            if (
                type(applied) is not AccountProjectionCheckpoint
                or applied.stream_epoch != progress["stream_epoch"]
                or type(applied.cursor) is not int
                or applied.cursor < checkpoint.cursor
                or applied.cursor > progress["offered_cursor"]
                or type(applied.pending_ack) is not bool
            ):
                raise ServiceAccountConsumerError("PROJECTION_APPLY_STATE_MISMATCH")
            if not applied.pending_ack:
                if (
                    applied.cursor != checkpoint.cursor
                    or applied.projection != checkpoint.projection
                ):
                    raise ServiceAccountConsumerError("PROJECTION_APPLY_STATE_MISMATCH")
                return applied
            return self._acknowledge(applied, poll_signature)
        finally:
            self._polling = False

    def close(self) -> None:
        """Close the owned projection store, leaving the borrowed runtime open."""

        self._assert_owner()
        if self._closed:
            return
        if self._polling:
            raise ServiceAccountConsumerError("POLL_IN_PROGRESS")
        try:
            self._store.close()
        except Exception:
            raise ServiceAccountConsumerError("PROJECTION_STORE_CLOSE_FAILED") from None
        self._closed = True

    def _acknowledge(
        self,
        checkpoint: AccountProjectionCheckpoint,
        expected_signature: tuple[object, ...],
    ) -> AccountProjectionCheckpoint:
        epoch = checkpoint.stream_epoch
        if type(epoch) is not str or not epoch or not checkpoint.pending_ack:
            raise ServiceAccountConsumerError("INVALID_PENDING_ACK")
        cursor = checkpoint.cursor
        reply = self._call(
            "account.ack",
            {"stream_epoch": epoch, "cursor": cursor},
            expected_signature=expected_signature,
        )
        progress = _read_progress(reply, wrapped_only=True)
        self._validate_progress(
            progress,
            expected_epoch=epoch,
            minimum_cursor=cursor,
        )
        self._current_binding(expected_signature=expected_signature)
        try:
            acknowledged = self._store.mark_acknowledged(epoch, cursor)
        except Exception:
            raise ServiceAccountConsumerError("PROJECTION_ACK_COMMIT_FAILED") from None
        if (
            type(acknowledged) is not AccountProjectionCheckpoint
            or acknowledged.stream_epoch != epoch
            or acknowledged.cursor != cursor
            or acknowledged.pending_ack
            or acknowledged.projection != checkpoint.projection
        ):
            raise ServiceAccountConsumerError("PROJECTION_ACK_STATE_MISMATCH")
        return acknowledged

    def _call(
        self,
        operation: str,
        arguments: Mapping[str, Any],
        *,
        expected_signature: tuple[object, ...],
    ) -> Mapping[str, Any]:
        self._current_binding(expected_signature=expected_signature)
        try:
            reply = self._runtime.call(
                self._mount,
                operation,
                arguments,
                account_ref=self._account_ref,
                scope_ref=self._scope_ref,
                max_reply_message_bytes=expected_signature[-1],
            )
        except ServiceRuntimeError as error:
            if error.code == "REPLY_TOO_LARGE":
                raise ServiceAccountConsumerError("REPLY_TOO_LARGE") from None
            raise ServiceAccountConsumerError("ACCOUNT_SERVICE_CALL_FAILED") from None
        except Exception:
            raise ServiceAccountConsumerError("ACCOUNT_SERVICE_CALL_FAILED") from None
        self._current_binding(expected_signature=expected_signature)
        if not isinstance(reply, Mapping):
            raise ServiceAccountConsumerError("INVALID_ACCOUNT_REPLY")
        return reply

    def _validate_progress(
        self,
        progress: Mapping[str, Any],
        *,
        expected_epoch: str | None,
        minimum_cursor: int,
    ) -> None:
        epoch = progress["stream_epoch"]
        offered = progress["offered_cursor"]
        acknowledged = progress["acknowledged_cursor"]
        if (
            type(epoch) is not str
            or not _valid_text(epoch)
            or len(epoch.encode("utf-8")) > _MAX_EPOCH_BYTES
            or type(offered) is not int
            or not minimum_cursor <= offered <= _MAX_SQLITE_CURSOR
            or type(acknowledged) is not int
            or not minimum_cursor <= acknowledged <= _MAX_SQLITE_CURSOR
            or acknowledged > offered
        ):
            raise ServiceAccountConsumerError("INVALID_ACCOUNT_PROGRESS")
        if expected_epoch is not None and epoch != expected_epoch:
            raise ServiceAccountConsumerError("STREAM_EPOCH_MISMATCH")

    def _read_checkpoint(self) -> AccountProjectionCheckpoint:
        try:
            checkpoint = self._store.checkpoint()
        except Exception:
            raise ServiceAccountConsumerError("PROJECTION_STORE_FAILED") from None
        if type(checkpoint) is not AccountProjectionCheckpoint:
            raise ServiceAccountConsumerError("INVALID_PROJECTION_CHECKPOINT")
        return checkpoint

    def _current_binding(
        self,
        *,
        expected_signature: tuple[object, ...] | None = None,
    ) -> tuple[object, ...]:
        try:
            binding = self._runtime.account_binding_view(
                self._mount,
                self._account_ref,
                required_operations=_REQUIRED_OPERATIONS,
            )
            identity = self._runtime.identity
        except Exception:
            raise ServiceAccountConsumerError("ACCOUNT_BINDING_UNAVAILABLE") from None
        try:
            stable = _stable_binding(
                binding,
                identity,
                expected_account_scope=self._stable_binding["expected_account_scope"],
                expected_strategy_scope=self._stable_binding["expected_strategy_scope"],
                projection_version=self._stable_binding["projection_version"],
                initial_state_sha256=self._stable_binding["initial_state_sha256"],
            )
            signature = _rpc_binding_signature(binding, identity)
        except Exception:
            raise ServiceAccountConsumerError("INVALID_ACCOUNT_BINDING") from None
        if stable != self._stable_binding:
            raise ServiceAccountConsumerError("ACCOUNT_BINDING_CHANGED")
        if expected_signature is not None and signature != expected_signature:
            raise ServiceAccountConsumerError("ACCOUNT_RPC_BINDING_CHANGED")
        return signature

    def _assert_owner(self) -> None:
        if threading.get_ident() != self._owner_thread_id:
            raise ServiceAccountConsumerError("WRONG_OWNER_THREAD")

    def _assert_open_owner(self) -> None:
        self._assert_owner()
        if self._closed:
            raise ServiceAccountConsumerError("CONSUMER_CLOSED")


_MISSING = object()


def _read_progress(reply: Mapping[str, Any], *, wrapped_only: bool = False) -> Mapping[str, Any]:
    if not isinstance(reply, Mapping):
        raise ServiceAccountConsumerError("INVALID_ACCOUNT_REPLY")
    if wrapped_only or set(reply) == {"consumer_progress"}:
        if set(reply) != {"consumer_progress"}:
            raise ServiceAccountConsumerError("INVALID_ACCOUNT_PROGRESS")
        progress = reply.get("consumer_progress")
    else:
        allowed = {
            "requested_cursor",
            "snapshot",
            "events",
            "next_cursor",
            "latest_cursor",
            "recovered_from_snapshot",
            "consumer_progress",
            "state_status",
        }
        if not {"consumer_progress"} <= set(reply) or not set(reply) <= allowed:
            raise ServiceAccountConsumerError("INVALID_ACCOUNT_REPLY")
        progress = reply.get("consumer_progress")
    if not isinstance(progress, Mapping) or set(progress) != {
        "stream_epoch",
        "offered_cursor",
        "acknowledged_cursor",
    }:
        raise ServiceAccountConsumerError("INVALID_ACCOUNT_PROGRESS")
    return progress


def _stable_binding(
    binding: ServiceAccountBindingView,
    identity: WireIdentity,
    *,
    expected_account_scope: str,
    expected_strategy_scope: str,
    projection_version: str,
    initial_state_sha256: str,
) -> dict[str, Any]:
    if type(binding) is not ServiceAccountBindingView:
        raise TypeError("invalid account binding view")
    if type(identity) is not WireIdentity:
        raise TypeError("invalid client identity")
    scope = binding.rpc_scope
    if type(scope) is not WireScope or scope.account_ref != binding.account_ref:
        raise TypeError("invalid account wire scope")
    return {
        "schema": "sdk.service_account_consumer.binding.v1",
        "service_id": binding.service_id,
        "instance_id": binding.instance_id,
        "mount": binding.mount,
        "scope_ref": binding.scope_ref,
        "account_ref": binding.account_ref,
        "identity": {
            "role": identity.role.value,
            "principal_ref": identity.principal_ref,
            "actor_ref": identity.actor_ref,
            "delegate_ref": identity.delegate_ref,
        },
        "scope": _scope_mapping(scope, include_run_id=False),
        "expected_account_scope": expected_account_scope,
        "expected_strategy_scope": expected_strategy_scope,
        "projection_version": projection_version,
        "initial_state_sha256": initial_state_sha256,
    }


def _rpc_binding_signature(
    binding: ServiceAccountBindingView,
    identity: WireIdentity,
) -> tuple[object, ...]:
    if type(binding) is not ServiceAccountBindingView or type(identity) is not WireIdentity:
        raise TypeError("invalid account binding")
    scope = binding.rpc_scope
    if type(scope) is not WireScope:
        raise TypeError("invalid wire scope")
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
        identity.role,
        identity.principal_ref,
        identity.actor_ref,
        identity.delegate_ref,
        binding.max_message_bytes,
    )


def _scope_mapping(scope: WireScope, *, include_run_id: bool) -> dict[str, Any]:
    result = {
        "execution_domain_ref": scope.execution_domain_ref,
        "account_ref": scope.account_ref,
        "account_refs": list(scope.account_refs),
        "strategy_ref": scope.strategy_ref,
        "manual_book_ref": scope.manual_book_ref,
        "portfolio_ref": scope.portfolio_ref,
        "group_id": scope.group_id,
        "leg_id": scope.leg_id,
    }
    if include_run_id:
        result["run_id"] = scope.run_id
    return result


def _valid_text(value: object) -> bool:
    if type(value) is not str or not value or value != value.strip():
        return False
    try:
        encoded = value.encode("utf-8", errors="strict")
    except UnicodeEncodeError:
        return False
    return len(encoded) <= MAX_REFERENCE_BYTES and not any(
        ord(character) < 32 or 0x7F <= ord(character) <= 0x9F for character in value
    )


def _copy_json_mapping(value: Mapping[str, Any]) -> dict[str, Any]:
    if not isinstance(value, Mapping):
        raise ServiceAccountConsumerError("INVALID_INITIAL_STATE")
    budget = {"nodes": 0, "bytes": 0, "active": set()}
    copied = _copy_json_value(value, depth=0, budget=budget)
    if type(copied) is not dict:
        raise ServiceAccountConsumerError("INVALID_INITIAL_STATE")
    return copied


def _copy_json_value(value: Any, *, depth: int, budget: dict[str, Any]) -> Any:
    budget["nodes"] += 1
    if depth > _MAX_JSON_DEPTH or budget["nodes"] > _MAX_INITIAL_STATE_NODES:
        raise ServiceAccountConsumerError("INVALID_INITIAL_STATE")
    _add_initial_state_bytes(budget, 2)
    if value is None or type(value) is bool:
        return value
    if type(value) is int:
        _add_initial_state_bytes(budget, len(str(value)))
        return value
    if type(value) is str:
        try:
            encoded = value.encode("utf-8", errors="strict")
        except UnicodeEncodeError:
            raise ServiceAccountConsumerError("INVALID_INITIAL_STATE") from None
        _add_initial_state_bytes(budget, len(encoded))
        return value
    if type(value) is float:
        if not math.isfinite(value):
            raise ServiceAccountConsumerError("INVALID_INITIAL_STATE")
        _add_initial_state_bytes(budget, len(repr(value)))
        return value
    if isinstance(value, Mapping):
        marker = id(value)
        if marker in budget["active"]:
            raise ServiceAccountConsumerError("INVALID_INITIAL_STATE")
        budget["active"].add(marker)
        copied: dict[str, Any] = {}
        try:
            for key, item in value.items():
                if type(key) is not str:
                    raise ServiceAccountConsumerError("INVALID_INITIAL_STATE")
                try:
                    key_bytes = key.encode("utf-8", errors="strict")
                except UnicodeEncodeError:
                    raise ServiceAccountConsumerError("INVALID_INITIAL_STATE") from None
                _add_initial_state_bytes(budget, len(key_bytes))
                copied[key] = _copy_json_value(item, depth=depth + 1, budget=budget)
        finally:
            budget["active"].remove(marker)
        return copied
    if type(value) in (list, tuple):
        marker = id(value)
        if marker in budget["active"]:
            raise ServiceAccountConsumerError("INVALID_INITIAL_STATE")
        budget["active"].add(marker)
        try:
            return [_copy_json_value(item, depth=depth + 1, budget=budget) for item in value]
        finally:
            budget["active"].remove(marker)
    raise ServiceAccountConsumerError("INVALID_INITIAL_STATE")


def _add_initial_state_bytes(budget: dict[str, Any], amount: int) -> None:
    budget["bytes"] += amount
    if budget["bytes"] > _MAX_INITIAL_STATE_BYTES:
        raise ServiceAccountConsumerError("INVALID_INITIAL_STATE")


def _canonical_json(value: Mapping[str, Any]) -> bytes:
    try:
        return json.dumps(
            value,
            ensure_ascii=False,
            allow_nan=False,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8", errors="strict")
    except (TypeError, ValueError, UnicodeEncodeError):
        raise ServiceAccountConsumerError("INVALID_INITIAL_STATE") from None
