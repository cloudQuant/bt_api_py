"""N6 synthetic Gateway query admission probe, separate from DIM07 fairness.

The account owner wraps its synthetic provider with the same durable quota
session used by Gateway provider calls. A strategy uses the SDK certification
client over its existing account service. This module records the owner-side
typed quota decision separately from the SDK's bounded service error so a
client failure alone can never be counted as an admission denial.
"""

from __future__ import annotations

import threading
import time
from collections import Counter
from collections.abc import Iterable, Mapping
from os import PathLike
from typing import Any

from bt_api_base.service_protocol import WireEnvelope, WireHeader, WireMessageKind
from bt_api_gateway.quota_admission import (
    GatewayQuotaAdmission,
    GatewayQuotaAdmissionError,
    GatewayQuotaProviderSession,
    GatewayQuotaSubject,
)
from bt_api_py.service_registry import SERVICE_ERROR_CODES


QUOTA_DOMAIN_REF = "synthetic:load01-n6-server-time-query-v1"
CAPACITY_PER_WINDOW = 3
WINDOW_SECONDS = 3_600.0
RESERVED_CAPACITY = 0
MAX_QUEUE = 4
MAX_QUEUE_PER_SUBJECT = 1
MAX_BACKOFF_SECONDS = 0.25
MAX_QUEUE_WAIT_SECONDS = 0.25
POLL_INTERVAL_SECONDS = 0.005
SERVER_TIME_QUERY_COUNT = 2
RUNTIME_RPC_TIMEOUT_MS = 5_000
_MISSING_ATTRIBUTE = object()


def create_server_time_quota_admission(
    database_path: str | PathLike[str],
) -> GatewayQuotaAdmission:
    """Open the dedicated three-slot account-query policy for one owner."""

    return GatewayQuotaAdmission(
        database_path,
        QUOTA_DOMAIN_REF,
        capacity_per_window=CAPACITY_PER_WINDOW,
        window_seconds=WINDOW_SECONDS,
        reserved_capacity=RESERVED_CAPACITY,
        max_queue=MAX_QUEUE,
        max_queue_per_subject=MAX_QUEUE_PER_SUBJECT,
        max_backoff_seconds=MAX_BACKOFF_SECONDS,
        max_queue_wait_seconds=MAX_QUEUE_WAIT_SECONDS,
        poll_interval_seconds=POLL_INTERVAL_SECONDS,
    )


class N6SyntheticServerTimeProvider:
    """Add one deterministic server-time fact to a synthetic account provider."""

    def __init__(self, provider: object) -> None:
        self._provider = provider

    def connect(self, *, account_key: object, generation: str, timeout: float) -> object:
        return self._provider.connect(  # type: ignore[attr-defined]
            account_key=account_key, generation=generation, timeout=timeout
        )

    def read_account(self, *, generation: str, timeout: float) -> object:
        return self._provider.read_account(  # type: ignore[attr-defined]
            generation=generation, timeout=timeout
        )

    def read_exchange_server_time(self, *, generation: str, timeout: float) -> Mapping[str, int]:
        if type(generation) is not str or not generation or timeout <= 0:
            raise ValueError("synthetic server-time request has an invalid session")
        return {"serverTime": 1_800_000_000_000}

    def close(self) -> object:
        return self._provider.close()  # type: ignore[attr-defined]

    def __getattr__(self, name: str) -> object:
        return getattr(self._provider, name)


class _ObservedSyntheticProvider:
    """Count only provider-boundary calls made after quota admission."""

    def __init__(self, provider: object) -> None:
        self._provider = provider
        self.connect_dispatches = 0
        self.account_read_dispatches = 0
        self.server_time_dispatches = 0
        self.close_calls = 0

    def connect(self, *, account_key: object, generation: str, timeout: float) -> object:
        self.connect_dispatches += 1
        return self._provider.connect(  # type: ignore[attr-defined]
            account_key=account_key, generation=generation, timeout=timeout
        )

    def read_account(self, *, generation: str, timeout: float) -> object:
        self.account_read_dispatches += 1
        return self._provider.read_account(  # type: ignore[attr-defined]
            generation=generation, timeout=timeout
        )

    def read_exchange_server_time(self, *, generation: str, timeout: float) -> object:
        self.server_time_dispatches += 1
        method = getattr(self._provider, "read_exchange_server_time", None)
        if not callable(method):
            raise RuntimeError("synthetic provider has no server-time implementation")
        return method(generation=generation, timeout=timeout)

    def close(self) -> object:
        self.close_calls += 1
        return self._provider.close()  # type: ignore[attr-defined]

    def __getattr__(self, name: str) -> object:
        return getattr(self._provider, name)


class ServerTimeQuotaProvider:
    """Owner-thread facade that preserves typed denial facts for reporting."""

    def __init__(
        self,
        provider: object,
        database_path: str | PathLike[str],
        account_ref: str,
    ) -> None:
        if type(account_ref) is not str or not account_ref:
            raise ValueError("account quota subject must be fixed and nonempty")
        self._owner_thread_id = threading.get_ident()
        self._admission = create_server_time_quota_admission(database_path)
        self._provider = _ObservedSyntheticProvider(provider)
        self._subject = GatewayQuotaSubject(account_ref=account_ref)
        session = GatewayQuotaProviderSession(
            self._provider,
            self._admission,
            self._subject,
            assert_session_owned=self._assert_owner,
        )
        self._session = session
        self._server_time_attempts = 0
        self._server_time_returns = 0
        self._typed_denials: Counter[str] = Counter()

    def _assert_owner(self) -> None:
        if threading.get_ident() != self._owner_thread_id:
            raise GatewayQuotaAdmissionError("SESSION_OWNERSHIP_UNAVAILABLE")

    def connect(self, *, account_key: object, generation: str, timeout: float) -> object:
        self._assert_owner()
        return self._session.connect(
            account_key=account_key, generation=generation, timeout=timeout
        )

    def read_account(self, *, generation: str, timeout: float) -> object:
        self._assert_owner()
        return self._session.read_account(generation=generation, timeout=timeout)

    def read_exchange_server_time(self, *, generation: str, timeout: float) -> object:
        self._assert_owner()
        self._server_time_attempts += 1
        try:
            result = self._session.read_exchange_server_time(
                generation=generation, timeout=timeout
            )
        except GatewayQuotaAdmissionError as exc:
            self._typed_denials[exc.code] += 1
            raise
        self._server_time_returns += 1
        return result

    def close(self) -> object:
        self._assert_owner()
        return self._session.close()

    def quota_server_time_facts_snapshot(self) -> dict[str, Any]:
        """Return bounded owner evidence after the worker has stopped."""

        status = self._admission.status()
        return {
            "quota_domain_ref": QUOTA_DOMAIN_REF,
            "subject_scope": "account_only",
            "strategy_ref": self._subject.strategy_ref,
            "capacity_per_window": status["capacity_per_window"],
            "reserved_capacity": status["reserved_capacity"],
            "window_seconds": WINDOW_SECONDS,
            "max_queue_wait_seconds": MAX_QUEUE_WAIT_SECONDS,
            "runtime_rpc_timeout_ms": RUNTIME_RPC_TIMEOUT_MS,
            "server_time_attempts": self._server_time_attempts,
            "server_time_admitted_returns": self._server_time_returns,
            "server_time_typed_denials": dict(sorted(self._typed_denials.items())),
            "provider_connect_dispatches": self._provider.connect_dispatches,
            "provider_account_read_dispatches": self._provider.account_read_dispatches,
            "provider_server_time_dispatches": self._provider.server_time_dispatches,
            "provider_close_calls": self._provider.close_calls,
            "grants_in_window": status["grants_in_window"],
            "pending_waiters": status["pending_waiters"],
        }

    def __getattr__(self, name: str) -> object:
        return getattr(self._provider, name)


class RuntimeQueryResponseProbe:
    """Capture finite response-boundary facts without changing runtime behavior."""

    def __init__(self, runtime: object, *, mount: str, account_ref: str) -> None:
        view = runtime.account_binding_view(  # type: ignore[attr-defined]
            mount,
            account_ref,
            required_operations=frozenset({"account.server_time_probe"}),
        )
        transports = getattr(runtime, "_transports", None)
        peers = getattr(runtime, "_peers", None)
        if not isinstance(transports, dict) or not isinstance(peers, Mapping):
            raise AssertionError("N6 runtime response boundary is unavailable")
        key = (view.service_id, view.transport_ref)
        self._transport = transports.get(key)
        self._peer = peers.get(view.service_id)
        if self._transport is None or self._peer is None:
            raise AssertionError("N6 account transport binding is unavailable")
        self._runtime = runtime
        self._call = runtime.call  # type: ignore[attr-defined]
        self._request = self._transport.request
        self._runtime_call_attribute = vars(runtime).get("call", _MISSING_ATTRIBUTE)
        self._transport_request_attribute = vars(self._transport).get(
            "request", _MISSING_ATTRIBUTE
        )
        self._active_call_index: int | None = None
        self._rows: list[dict[str, Any]] = []
        try:
            runtime.call = self.call  # type: ignore[attr-defined]
            self._transport.request = self.request
        except BaseException:
            self._restore_wrapped_callables()
            raise

    def call(self, mount: str, operation: str, arguments: Mapping[str, Any], **kwargs: Any) -> Any:
        if mount != "store.account" or operation != "account.server_time_probe":
            return self._call(mount, operation, arguments, **kwargs)
        if self._active_call_index is not None or len(self._rows) >= SERVER_TIME_QUERY_COUNT:
            raise AssertionError("N6 server-time runtime calls must be serial and bounded")
        call_index = len(self._rows)
        row: dict[str, Any] = {
            "runtime_call_index": call_index,
            "mount": mount,
            "operation": operation,
            "runtime_call_outcome": None,
            "runtime_error_type": None,
            "runtime_error_code": None,
            "response_boundary": None,
        }
        self._rows.append(row)
        self._active_call_index = call_index
        try:
            result = self._call(mount, operation, arguments, **kwargs)
        except BaseException as exc:
            row["runtime_call_outcome"] = "raised"
            row["runtime_error_type"] = type(exc).__name__
            code = getattr(exc, "code", None)
            row["runtime_error_code"] = (
                code if type(code) is str and code.isascii() and len(code) <= 64 else None
            )
            raise
        else:
            row["runtime_call_outcome"] = "returned"
            return result
        finally:
            self._active_call_index = None

    def request(self, request: object, *, timeout: float) -> object:
        active_index = self._active_call_index
        request_header = getattr(request, "header", None)
        if (
            active_index is None
            or type(request) is not WireEnvelope
            or type(request_header) is not WireHeader
            or request_header.operation != "account.server_time_probe"
        ):
            return self._request(request, timeout=timeout)
        row = self._rows[active_index]
        if row["response_boundary"] is not None:
            raise AssertionError("N6 runtime call issued more than one query request")
        try:
            reply = self._request(request, timeout=timeout)
        except BaseException:
            row["response_boundary"] = {
                "request_sequence": active_index,
                "request_operation": request_header.operation,
                "response_received": False,
                "reply_wire_envelope": False,
                "reply_kind_is_reply": False,
                "correlation_matches": False,
                "operation_matches": False,
                "scope_matches": False,
                "owner_epoch_matches": False,
                "peer_matches": False,
                "reply_ok": None,
                "remote_error_code": None,
            }
            raise
        try:
            is_envelope = type(reply) is WireEnvelope
            header = getattr(reply, "header", None) if is_envelope else None
            header_is_wire = type(header) is WireHeader
            payload = getattr(reply, "payload", None) if is_envelope else None
            payload_is_mapping = isinstance(payload, Mapping)
            ok_value = payload.get("ok") if payload_is_mapping else None
            raw_code = payload.get("code") if payload_is_mapping else None
            remote_code = (
                raw_code
                if type(raw_code) is str
                and raw_code.isascii()
                and len(raw_code) <= 64
                and raw_code in SERVICE_ERROR_CODES
                else None
            )
            row["response_boundary"] = {
                "request_sequence": active_index,
                "request_operation": request_header.operation,
                "response_received": True,
                "reply_wire_envelope": is_envelope and header_is_wire,
                "reply_kind_is_reply": bool(
                    is_envelope
                    and header_is_wire
                    and header.kind is WireMessageKind.REPLY
                ),
                "correlation_matches": bool(
                    is_envelope
                    and header_is_wire
                    and header.correlation_id == request_header.message_id
                ),
                "operation_matches": bool(
                    is_envelope
                    and header_is_wire
                    and header.operation == request_header.operation
                ),
                "scope_matches": bool(
                    is_envelope
                    and header_is_wire
                    and header.scope == request_header.scope
                ),
                "owner_epoch_matches": bool(
                    is_envelope
                    and header_is_wire
                    and header.owner_epoch == request_header.owner_epoch
                ),
                "peer_matches": bool(
                    is_envelope and header_is_wire and header.identity == self._peer
                ),
                "reply_ok": ok_value if type(ok_value) is bool else None,
                "remote_error_code": remote_code,
            }
        except Exception:
            # Diagnostics must not alter the SDK call's return or exception.
            row["response_boundary"] = {
                "request_sequence": active_index,
                "request_operation": request_header.operation,
                "response_received": True,
                "reply_wire_envelope": False,
                "reply_kind_is_reply": False,
                "correlation_matches": False,
                "operation_matches": False,
                "scope_matches": False,
                "owner_epoch_matches": False,
                "peer_matches": False,
                "reply_ok": None,
                "remote_error_code": None,
            }
        return reply

    def snapshot(self) -> tuple[dict[str, Any], ...]:
        return tuple(dict(row) for row in self._rows)

    def close(self) -> None:
        """Restore the exact runtime and transport callables that were wrapped."""

        self._restore_wrapped_callables()

    def _restore_wrapped_callables(self) -> None:
        if self._runtime_call_attribute is _MISSING_ATTRIBUTE:
            vars(self._runtime).pop("call", None)
        else:
            self._runtime.call = self._runtime_call_attribute  # type: ignore[attr-defined]
        if self._transport_request_attribute is _MISSING_ATTRIBUTE:
            vars(self._transport).pop("request", None)
        else:
            self._transport.request = self._transport_request_attribute


def capture_two_server_time_queries(
    client: object,
    runtime: object,
    account_ref: str,
    certification_error_type: type[BaseException],
) -> tuple[dict[str, Any], ...]:
    """Make two serial SDK reads and retain runtime reply plus typed outcomes."""

    probe = RuntimeQueryResponseProbe(
        runtime, mount="store.account", account_ref=account_ref
    )
    rows: list[dict[str, Any]] = []
    try:
        for request_index in range(SERVER_TIME_QUERY_COUNT):
            started_ns = time.monotonic_ns()
            try:
                observation = client.read_exchange_server_time()  # type: ignore[attr-defined]
            except certification_error_type as exc:
                code = getattr(exc, "code", None)
                if type(code) is not str or not code.isascii() or len(code) > 64:
                    code = "UNEXPECTED_CLIENT_ERROR"
                rows.append(
                    {
                        "request_index": request_index,
                        "outcome": "sdk_error",
                        "observation_type": None,
                        "error_code": code,
                        "elapsed_ns": max(0, time.monotonic_ns() - started_ns),
                    }
                )
            else:
                rows.append(
                    {
                        "request_index": request_index,
                        "outcome": "returned",
                        "observation_type": type(observation).__name__,
                        "error_code": None,
                        "elapsed_ns": max(0, time.monotonic_ns() - started_ns),
                    }
                )
    finally:
        probe.close()
    boundaries = probe.snapshot()
    if len(boundaries) != len(rows):
        raise AssertionError("N6 runtime and SDK query sequences do not align")
    for outcome, boundary in zip(rows, boundaries):
        outcome["runtime_boundary"] = boundary
    return tuple(rows)


def summarize_server_time_probe(
    outcomes: Iterable[Mapping[str, Any]],
    owner_facts: Mapping[str, Any],
) -> dict[str, Any]:
    """Require one actual provider dispatch and one owner-confirmed denial."""

    rows = tuple(outcomes)
    if len(rows) != SERVER_TIME_QUERY_COUNT:
        raise AssertionError("N6 server-time probe must contain exactly two SDK outcomes")
    expected_outcome_keys = {
        "request_index",
        "outcome",
        "observation_type",
        "error_code",
        "elapsed_ns",
        "runtime_boundary",
    }
    for index, row in enumerate(rows):
        if (
            type(row) is not dict
            or set(row) != expected_outcome_keys
            or type(row.get("request_index")) is not int
            or row.get("request_index") != index
        ):
            raise AssertionError("N6 server-time SDK outcomes are not ordered and typed")
        if type(row.get("elapsed_ns")) is not int or row["elapsed_ns"] < 0:
            raise AssertionError("N6 server-time SDK outcome duration is invalid")
    first, second = rows
    if (
        first.get("outcome") != "returned"
        or first.get("observation_type") != "ExchangeServerTimeObservation"
        or first.get("error_code") is not None
    ):
        raise AssertionError("N6 first SDK server-time query did not return its typed fact")
    if (
        second.get("outcome") != "sdk_error"
        or second.get("error_code") != "ACCOUNT_FACT_READ_FAILED"
        or second.get("observation_type") is not None
        or second["elapsed_ns"] >= RUNTIME_RPC_TIMEOUT_MS * 1_000_000
    ):
        raise AssertionError("N6 second SDK query did not return the bounded owner denial")
    for index, (outcome, expected_ok, expected_remote_code, expected_runtime_outcome, expected_runtime_code) in enumerate(
        (
            (first, True, "OK", "returned", None),
            (second, False, "HANDLER_FAILED", "raised", "BUSINESS_OUTCOME_UNKNOWN"),
        )
    ):
        boundary = outcome.get("runtime_boundary")
        if (
            type(boundary) is not dict
            or set(boundary)
            != {
                "runtime_call_index",
                "mount",
                "operation",
                "runtime_call_outcome",
                "runtime_error_type",
                "runtime_error_code",
                "response_boundary",
            }
            or boundary.get("runtime_call_index") != index
            or boundary.get("mount") != "store.account"
            or boundary.get("operation") != "account.server_time_probe"
            or boundary.get("runtime_call_outcome") != expected_runtime_outcome
            or boundary.get("runtime_error_code") != expected_runtime_code
            or (
                index == 0
                and boundary.get("runtime_error_type") is not None
            )
        ):
            raise AssertionError("N6 runtime.call response boundary is incomplete or misordered")
        if (
            index == 1 and boundary.get("runtime_error_type") != "ServiceCallOutcomeUnknownError"
        ):
            raise AssertionError("N6 remote handler rejection was not mapped by the SDK runtime")
        response = boundary.get("response_boundary")
        if (
            type(response) is not dict
            or set(response)
            != {
                "request_sequence",
                "request_operation",
                "response_received",
                "reply_wire_envelope",
                "reply_kind_is_reply",
                "correlation_matches",
                "operation_matches",
                "scope_matches",
                "owner_epoch_matches",
                "peer_matches",
                "reply_ok",
                "remote_error_code",
            }
            or response.get("request_sequence") != index
            or response.get("request_operation") != "account.server_time_probe"
            or response.get("response_received") is not True
            or response.get("reply_wire_envelope") is not True
            or response.get("reply_kind_is_reply") is not True
            or response.get("correlation_matches") is not True
            or response.get("operation_matches") is not True
            or response.get("scope_matches") is not True
            or response.get("owner_epoch_matches") is not True
            or response.get("peer_matches") is not True
            or response.get("reply_ok") is not expected_ok
            or response.get("remote_error_code") != expected_remote_code
        ):
            raise AssertionError("N6 SDK query did not receive the expected correlated service reply")
    expected = {
        "quota_domain_ref": QUOTA_DOMAIN_REF,
        "subject_scope": "account_only",
        "strategy_ref": None,
        "capacity_per_window": CAPACITY_PER_WINDOW,
        "reserved_capacity": RESERVED_CAPACITY,
        "window_seconds": WINDOW_SECONDS,
        "max_queue_wait_seconds": MAX_QUEUE_WAIT_SECONDS,
        "runtime_rpc_timeout_ms": RUNTIME_RPC_TIMEOUT_MS,
        "server_time_attempts": 2,
        "server_time_admitted_returns": 1,
        "server_time_typed_denials": {"DEADLINE_EXCEEDED": 1},
        "provider_connect_dispatches": 1,
        "provider_account_read_dispatches": 1,
        "provider_server_time_dispatches": 1,
        "provider_close_calls": 1,
        "grants_in_window": CAPACITY_PER_WINDOW,
        "pending_waiters": 0,
    }
    if type(owner_facts) is not dict or set(owner_facts) != set(expected):
        raise AssertionError("N6 owner quota facts are missing or contain unbounded fields")
    if any(owner_facts.get(key) != value for key, value in expected.items()):
        raise AssertionError("N6 owner quota facts do not match the three-slot query probe")
    second_runtime_sequence = second["runtime_boundary"]["runtime_call_index"]
    second_owner_sequence = second_runtime_sequence + 1
    if (
        second_runtime_sequence != 1
        or second_owner_sequence != owner_facts["server_time_attempts"]
        or second_owner_sequence != 2
    ):
        raise AssertionError("N6 second runtime request did not map to owner attempt two")
    return {
        "status": "RUN_PENDING",
        "scenario": "n6_sdk_server_time_quota_probe",
        "quota_domain_ref": QUOTA_DOMAIN_REF,
        "quota_capacity": CAPACITY_PER_WINDOW,
        "reserved_capacity": RESERVED_CAPACITY,
        "quota_window_seconds": WINDOW_SECONDS,
        "quota_max_queue_wait_seconds": MAX_QUEUE_WAIT_SECONDS,
        "runtime_rpc_timeout_ms": RUNTIME_RPC_TIMEOUT_MS,
        "startup_slots_measured": {
            "login_dispatches": owner_facts["provider_connect_dispatches"],
            "initial_account_read_dispatches": owner_facts["provider_account_read_dispatches"],
            "consumed_startup_slots": (
                owner_facts["provider_connect_dispatches"]
                + owner_facts["provider_account_read_dispatches"]
            ),
            "grants_at_owner_ready": (
                owner_facts["provider_connect_dispatches"]
                + owner_facts["provider_account_read_dispatches"]
            ),
            "reserved_capacity": owner_facts["reserved_capacity"],
        },
        "strategy_queries": rows,
        "owner_typed_quota_denials": owner_facts["server_time_typed_denials"],
        "correlated_remote_error_reply": {
            "server_reply_code": "HANDLER_FAILED",
            "runtime_error_code": second["runtime_boundary"]["runtime_error_code"],
            "sdk_error_code": second["error_code"],
            "request_sequence": second_runtime_sequence,
            "owner_attempt_sequence": second_owner_sequence,
            "correlation_matches": True,
            "owner_attempt_sequence_matches": True,
            "provider_dispatch_count_remained_one": (
                owner_facts["provider_server_time_dispatches"] == 1
            ),
        },
        "provider_server_time_dispatches": owner_facts["provider_server_time_dispatches"],
        "durable_grants_in_window": owner_facts["grants_in_window"],
        "pending_waiters": owner_facts["pending_waiters"],
        "subject_scope": "account_only; no strategy-fairness claim",
        "provider_boundary": "synthetic local provider; no exchange request was sent",
        "evidence_scope": "query-path source integration only",
    }
