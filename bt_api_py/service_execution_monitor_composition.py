"""Explicit single-source composition of the Execution -> Monitor relays.

This module wires the two already committed relays into one service lifetime:

* :class:`bt_api_py.service_execution_monitor.ServiceExecutionMonitorRelay`
  (journal streams), and
* :class:`bt_api_py.service_execution_projection_relay.ServiceExecutionProjectionRelay`
  (parent/child projection).

It exists so an owner selects exactly one source for a destination instead of
constructing both.  The selection is an explicit constructor argument, never a
config key: the service config schema has no frozen relay-selection key yet, so
inventing one here would silently become a second contract.  The factory:

* refuses unknown, ambiguous (both sources supplied) and mismatched handles;
* derives the Monitor outbox scope from trusted account/strategy/domain
  references, never from an event payload or a caller-supplied string;
* constructs at most one relay, and imports only the module for the selected
  source (a ``none`` selection imports neither relay);
* performs no network I/O and starts no thread, timer or retry worker.

The returned :class:`ServiceExecutionMonitorComposition` is an owner-thread
lifetime.  ``start``/``stop``/``close`` are explicit and idempotent; ``poll``
runs only when the caller invokes it.  The borrowed runtime, journal reader and
projection are never closed here.  A destination is registered by its host by
adopting ``start``/``maintain``/``poll``/``stop`` on the owner thread; this
module does not register itself in ``ServiceHost``.

Scope contract
--------------

``scope_key`` is a deterministic digest of the selected source plus the trusted
``execution_domain_ref``/``account_ref``/``strategy_ref``.  For the projection
relay it is also the ``outbox_scope`` written into every Monitor event.  The
older journal relay derives its own outbox scope from the trusted journal
generation/scope key; this assembly validates the reader's account/strategy
against the same trusted references and deliberately exposes ``outbox_scope``
as ``None`` for that source instead of pretending a second value is in use.
"""

from __future__ import annotations

import hashlib
import json
import re
import threading
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .service_runtime import ServiceClientRuntime

SOURCE_JOURNAL = "journal"
SOURCE_PROJECTION = "projection"
SOURCE_NONE = "none"

_SOURCES = frozenset({SOURCE_JOURNAL, SOURCE_PROJECTION, SOURCE_NONE})
_ENABLED_SOURCES = frozenset({SOURCE_JOURNAL, SOURCE_PROJECTION})

_SCOPE_SCHEMA = "sdk.execution_monitor_composition.scope.v1"
_SCOPE_PREFIX = "sdk-execution-monitor-composition-v1:"
_CHECKPOINT_PREFIX = "execution-monitor"
_CHECKPOINT_SUFFIX = ".sqlite3"
_STREAMS = ("execution", "cancellation")
_MAX_REF_BYTES = 128
_MAX_POLL_LIMIT = 128
_DEFAULT_JOURNAL_POLL_LIMIT = 32
_MAX_PATH_BYTES = 4096
_UPPER_TOKEN_RE = re.compile(r"[A-Z][A-Z0-9_]{0,63}\Z")

_STATE_NEW = "new"
_STATE_STARTED = "started"
_STATE_CLOSED = "closed"


class ServiceExecutionMonitorCompositionError(RuntimeError):
    """Finite composition diagnostic with a stable, non-sensitive ``code``.

    Relay poll failures are not wrapped: the selected relay's own error object
    (and its last-poll counts) propagates unchanged.
    """

    def __init__(self, code: str) -> None:
        if type(code) is not str or _UPPER_TOKEN_RE.fullmatch(code) is None:
            code = "MONITOR_COMPOSITION_FAILED"
        self.code = code
        super().__init__(code)


@dataclass(frozen=True, slots=True)
class MonitorRelayCheckpoint:
    """One durable local position of the selected relay.

    ``position`` is the journal source sequence for the journal relay and the
    local delivery counter for the projection relay.  ``cursor`` and
    ``fingerprint`` are source-specific and may be ``None``.
    """

    source: str
    stream: str | None
    position: int
    last_event_id: str | None
    cursor: str | None = None
    fingerprint: str | None = None


def derive_monitor_outbox_scope(
    *,
    source: str,
    execution_domain_ref: str,
    account_ref: str,
    strategy_ref: str,
) -> str:
    """Derive the deterministic outbox scope for one enabled source.

    Only trusted references participate.  No event, order or provider payload
    can influence this value.
    """

    if source not in _ENABLED_SOURCES:
        raise ServiceExecutionMonitorCompositionError("UNKNOWN_SOURCE_SELECTION")
    _validate_trusted_refs(execution_domain_ref, account_ref, strategy_ref)
    return _scope_key(source, execution_domain_ref, account_ref, strategy_ref)


def compose_execution_monitor_relay(
    *,
    source: str,
    execution_domain_ref: str,
    account_ref: str,
    strategy_ref: str,
    checkpoint_path: str | Path | None = None,
    checkpoint_dir: str | Path | None = None,
    runtime: ServiceClientRuntime | None = None,
    journal_reader: object | None = None,
    projection: object | None = None,
    scope_ref: str | None = None,
    sink: object | None = None,
    consumer_id: str | None = None,
    page_limit: int | None = None,
    max_events_per_poll: int | None = None,
    byte_budget: int | None = None,
) -> ServiceExecutionMonitorComposition:
    """Build exactly one relay composition for the explicit ``source``.

    ``source`` must be ``"journal"``, ``"projection"`` or ``"none"``.  The
    matching handle (``journal_reader``/``projection``) is required, and a
    handle for any other source is refused.  For ``"projection"`` exactly one
    destination is used: an explicit callable ``sink`` wins, otherwise a
    :class:`~bt_api_py.service_monitor.ServiceMonitorOutboxSink` is built from
    the borrowed registered ``runtime`` and ``scope_ref``.

    ``checkpoint_path`` and ``checkpoint_dir`` are mutually exclusive; when a
    directory is given the file name is derived from the source and trusted
    scope, so different sources cannot accidentally share one cursor.  A
    disabled ``"none"`` composition touches no checkpoint file.
    """

    if type(source) is not str or source not in _SOURCES:
        raise ServiceExecutionMonitorCompositionError("UNKNOWN_SOURCE_SELECTION")
    _validate_trusted_refs(execution_domain_ref, account_ref, strategy_ref)

    has_projection = projection is not None
    has_journal = journal_reader is not None
    if has_projection and has_journal:
        raise ServiceExecutionMonitorCompositionError("AMBIGUOUS_SOURCE_SELECTION")
    if source == SOURCE_NONE:
        if has_projection or has_journal:
            raise ServiceExecutionMonitorCompositionError("UNEXPECTED_SOURCE_HANDLE")
        _reject_disabled_options(
            sink=sink,
            consumer_id=consumer_id,
            page_limit=page_limit,
            max_events_per_poll=max_events_per_poll,
            byte_budget=byte_budget,
        )
    elif source == SOURCE_PROJECTION:
        if has_journal:
            raise ServiceExecutionMonitorCompositionError("UNEXPECTED_SOURCE_HANDLE")
        if not has_projection:
            raise ServiceExecutionMonitorCompositionError("SOURCE_HANDLE_REQUIRED")
    else:
        if has_projection:
            raise ServiceExecutionMonitorCompositionError("UNEXPECTED_SOURCE_HANDLE")
        if not has_journal:
            raise ServiceExecutionMonitorCompositionError("SOURCE_HANDLE_REQUIRED")
        if sink is not None:
            raise ServiceExecutionMonitorCompositionError("UNSUPPORTED_OPTION")
        if any(
            value is not None
            for value in (consumer_id, page_limit, max_events_per_poll, byte_budget)
        ):
            raise ServiceExecutionMonitorCompositionError("UNSUPPORTED_OPTION")

    if runtime is not None and type(runtime) is not ServiceClientRuntime:
        raise ServiceExecutionMonitorCompositionError("INVALID_RUNTIME")

    scope_key = (
        _scope_key(source, execution_domain_ref, account_ref, strategy_ref)
        if source in _ENABLED_SOURCES
        else None
    )
    checkpoint = _resolve_checkpoint_path(
        source,
        scope_key,
        checkpoint_path=checkpoint_path,
        checkpoint_dir=checkpoint_dir,
    )

    relay: object | None = None
    outbox_scope: str | None = None
    if source == SOURCE_PROJECTION:
        if scope_key is None:
            # Only the disabled source has no scope and it never reaches here.
            raise ServiceExecutionMonitorCompositionError("UNKNOWN_SOURCE_SELECTION")
        relay, outbox_scope = _build_projection(
            projection=projection,
            runtime=runtime,
            scope_ref=scope_ref,
            sink=sink,
            consumer_id=consumer_id,
            page_limit=page_limit,
            max_events_per_poll=max_events_per_poll,
            byte_budget=byte_budget,
            outbox_scope=scope_key,
            checkpoint_path=checkpoint,
        )
    elif source == SOURCE_JOURNAL:
        if type(scope_ref) is not str or not _valid_ref(scope_ref):
            raise ServiceExecutionMonitorCompositionError("INVALID_SCOPE_REF")
        _validate_journal_scope(journal_reader, account_ref, strategy_ref)
        relay = _build_journal(
            runtime=runtime,
            journal_reader=journal_reader,
            scope_ref=scope_ref,
            checkpoint_path=checkpoint,
        )

    return ServiceExecutionMonitorComposition(
        source=source,
        relay=relay,
        runtime=runtime,
        scope_ref=scope_ref,
        scope_key=scope_key,
        outbox_scope=outbox_scope,
        checkpoint_path=checkpoint,
    )


class ServiceExecutionMonitorComposition:
    """Owner-thread lifetime for one explicitly selected Monitor relay.

    ``start`` validates the local checkpoint and activates the composition;
    it performs no network I/O.  ``poll`` delegates to the selected relay and
    returns that relay's native immutable result (``None`` when no source is
    selected).  ``stop``/``close`` close only the relay's logical lifetime;
    they are idempotent and never close the borrowed runtime, reader or
    projection.
    """

    def __init__(
        self,
        *,
        source: str,
        relay: object | None,
        runtime: ServiceClientRuntime | None,
        scope_ref: str | None,
        scope_key: str | None,
        outbox_scope: str | None,
        checkpoint_path: Path | None,
    ) -> None:
        self._source = source
        self._relay = relay
        self._runtime = runtime
        self._scope_ref = scope_ref
        self._scope_key = scope_key
        self._outbox_scope = outbox_scope
        self._checkpoint_path = checkpoint_path
        self._owner_thread_id = threading.get_ident()
        self._state = _STATE_NEW

    @property
    def source(self) -> str:
        """Return the explicitly selected source token."""

        return self._source

    @property
    def enabled(self) -> bool:
        """Return whether a relay was selected and constructed."""

        return self._relay is not None

    @property
    def relay(self) -> object | None:
        """Return the selected relay, or ``None`` for the ``none`` source."""

        return self._relay

    @property
    def runtime(self) -> ServiceClientRuntime | None:
        """Return the borrowed runtime handle, if one was supplied."""

        return self._runtime

    @property
    def scope_ref(self) -> str | None:
        """Return the configured Monitor ingress scope reference, if any."""

        return self._scope_ref

    @property
    def scope_key(self) -> str | None:
        """Return the trusted-reference digest, or ``None`` when disabled."""

        return self._scope_key

    @property
    def outbox_scope(self) -> str | None:
        """Return the outbox scope written by the selected relay.

        The projection relay is configured with the derived value.  The
        journal relay derives its own scope from the trusted journal identity,
        so this property is ``None`` for that source rather than reporting a
        value that is not in use.
        """

        return self._outbox_scope

    @property
    def checkpoint_path(self) -> Path | None:
        """Return the resolved local checkpoint path, if one applies."""

        return self._checkpoint_path

    @property
    def state(self) -> str:
        """Return ``"new"``, ``"started"`` or ``"closed"``."""

        return self._state

    @property
    def started(self) -> bool:
        """Return whether ``start`` succeeded and ``stop`` has not run."""

        return self._state == _STATE_STARTED

    def start(self) -> ServiceExecutionMonitorComposition:
        """Activate the lifetime after validating the local checkpoint.

        A failed start closes the relay's logical lifetime, marks this
        composition closed and raises a finite error.  It does not touch the
        borrowed runtime, reader or projection.
        """

        self._assert_owner_thread()
        if self._state == _STATE_CLOSED:
            raise ServiceExecutionMonitorCompositionError("COMPOSITION_CLOSED")
        if self._state == _STATE_STARTED:
            raise ServiceExecutionMonitorCompositionError("ALREADY_STARTED")
        try:
            self._read_checkpoints()
        except ServiceExecutionMonitorCompositionError as error:
            self._abandon()
            raise ServiceExecutionMonitorCompositionError(error.code) from None
        self._state = _STATE_STARTED
        return self

    def maintain(self) -> None:
        """Maintain the borrowed runtime on the owner thread.

        This is a no-op when no runtime was supplied (for example an explicit
        projection sink).  ``poll`` is never called implicitly.
        """

        self._assert_owner_thread()
        self._assert_active()
        if self._runtime is None:
            return
        try:
            self._runtime.maintain()
        except Exception:
            raise ServiceExecutionMonitorCompositionError("RUNTIME_MAINTAIN_FAILED") from None

    def poll(self, limit: int | None = None) -> object | None:
        """Poll the selected relay once on the owner thread.

        Returns ``None`` when no source was selected.  Projection sink and
        source failures are counted by that relay and do not raise; journal
        relay failures propagate as the relay's own finite error.
        """

        self._assert_owner_thread()
        self._assert_active()
        if self._relay is None:
            return None
        if limit is not None and (type(limit) is not int or not 0 <= limit <= _MAX_POLL_LIMIT):
            raise ServiceExecutionMonitorCompositionError("INVALID_POLL_LIMIT")
        if self._source == SOURCE_JOURNAL:
            effective = _DEFAULT_JOURNAL_POLL_LIMIT if limit is None else limit
            return self._relay.poll(effective)  # type: ignore[attr-defined]
        return self._relay.poll(limit)  # type: ignore[attr-defined]

    def checkpoints(self) -> tuple[MonitorRelayCheckpoint, ...]:
        """Read the selected relay's durable local checkpoint.

        This is a local read-only operation and performs no network I/O.  It
        is allowed before ``start`` so a restarted owner can inspect the
        position, and is refused after ``stop``.
        """

        self._assert_owner_thread()
        if self._state == _STATE_CLOSED:
            raise ServiceExecutionMonitorCompositionError("COMPOSITION_CLOSED")
        return self._read_checkpoints()

    def stop(self) -> None:
        """Close the selected relay's logical lifetime; idempotent."""

        self._assert_owner_thread()
        if self._state == _STATE_CLOSED:
            return
        self._state = _STATE_CLOSED
        relay = self._relay
        if relay is None:
            return
        try:
            relay.close()  # type: ignore[attr-defined]
        except Exception as error:
            raise ServiceExecutionMonitorCompositionError(
                _finite_code(error, "RELAY_CLOSE_FAILED")
            ) from None

    def close(self) -> None:
        """Alias for :meth:`stop`."""

        self.stop()

    def _read_checkpoints(self) -> tuple[MonitorRelayCheckpoint, ...]:
        relay = self._relay
        if relay is None:
            return ()
        try:
            if self._source == SOURCE_PROJECTION:
                checkpoint = relay.checkpoint()  # type: ignore[attr-defined]
                return (
                    MonitorRelayCheckpoint(
                        source=SOURCE_PROJECTION,
                        stream=None,
                        position=checkpoint.delivered,
                        last_event_id=checkpoint.last_event_id or None,
                        cursor=checkpoint.cursor or None,
                    ),
                )
            positions: list[MonitorRelayCheckpoint] = []
            for stream in _STREAMS:
                checkpoint = relay.checkpoint(stream)  # type: ignore[attr-defined]
                positions.append(
                    MonitorRelayCheckpoint(
                        source=SOURCE_JOURNAL,
                        stream=stream,
                        position=checkpoint.sequence,
                        last_event_id=checkpoint.source_event_id,
                        fingerprint=checkpoint.fingerprint,
                    )
                )
            return tuple(positions)
        except ServiceExecutionMonitorCompositionError:
            raise
        except Exception as error:
            raise ServiceExecutionMonitorCompositionError(
                _finite_code(error, "CHECKPOINT_READ_FAILED")
            ) from None

    def _abandon(self) -> None:
        """Close the relay after a failed start without masking the cause."""

        self._state = _STATE_CLOSED
        relay = self._relay
        if relay is None:
            return
        try:
            relay.close()  # type: ignore[attr-defined]
        except Exception:
            # The original finite start error is the reportable outcome; the
            # relay's logical close is best-effort here and the composition is
            # already marked closed.
            return

    def _assert_active(self) -> None:
        if self._state == _STATE_CLOSED:
            raise ServiceExecutionMonitorCompositionError("COMPOSITION_CLOSED")
        if self._state != _STATE_STARTED:
            raise ServiceExecutionMonitorCompositionError("NOT_STARTED")

    def _assert_owner_thread(self) -> None:
        if threading.get_ident() != self._owner_thread_id:
            raise ServiceExecutionMonitorCompositionError("WRONG_OWNER_THREAD")


def _build_projection(
    *,
    projection: object | None,
    runtime: ServiceClientRuntime | None,
    scope_ref: str | None,
    sink: object | None,
    consumer_id: str | None,
    page_limit: int | None,
    max_events_per_poll: int | None,
    byte_budget: int | None,
    outbox_scope: str,
    checkpoint_path: Path | None,
) -> tuple[object, str]:
    """Construct only the projection relay and return it with its scope."""

    sink_object = sink
    if sink_object is None:
        if runtime is None:
            raise ServiceExecutionMonitorCompositionError("SOURCE_HANDLE_REQUIRED")
        if type(scope_ref) is not str or not _valid_ref(scope_ref):
            raise ServiceExecutionMonitorCompositionError("INVALID_SCOPE_REF")
        # The sink module does not import either relay; keep this local so an
        # explicit-sink caller does not depend on the service monitor sink.
        from .service_monitor import (
            ServiceMonitorOutboxSink,
            ServiceMonitorOutboxSinkError,
        )

        try:
            sink_object = ServiceMonitorOutboxSink(
                runtime,
                scope_ref=scope_ref,
                outbox_scope=outbox_scope,
            )
        except ServiceMonitorOutboxSinkError as error:
            raise ServiceExecutionMonitorCompositionError(
                _finite_code(error, "SINK_UNAVAILABLE")
            ) from None
        except Exception:
            raise ServiceExecutionMonitorCompositionError("SINK_UNAVAILABLE") from None
    elif not callable(sink_object):
        raise ServiceExecutionMonitorCompositionError("INVALID_SINK")

    if checkpoint_path is None:
        raise ServiceExecutionMonitorCompositionError("CHECKPOINT_PATH_REQUIRED")
    from .service_execution_projection_relay import (
        ServiceExecutionProjectionRelay,
        ServiceExecutionProjectionRelayError,
    )

    options: dict[str, Any] = {}
    if consumer_id is not None:
        options["consumer_id"] = consumer_id
    if page_limit is not None:
        options["page_limit"] = page_limit
    if max_events_per_poll is not None:
        options["max_events_per_poll"] = max_events_per_poll
    if byte_budget is not None:
        options["byte_budget"] = byte_budget
    try:
        relay = ServiceExecutionProjectionRelay(
            projection,
            sink_object,
            checkpoint_path=checkpoint_path,
            outbox_scope=outbox_scope,
            **options,
        )
    except ServiceExecutionProjectionRelayError as error:
        raise ServiceExecutionMonitorCompositionError(
            _finite_code(error, "RELAY_CONSTRUCTION_FAILED")
        ) from None
    except Exception:
        raise ServiceExecutionMonitorCompositionError("RELAY_CONSTRUCTION_FAILED") from None
    return relay, outbox_scope


def _build_journal(
    *,
    runtime: ServiceClientRuntime | None,
    journal_reader: object | None,
    scope_ref: str,
    checkpoint_path: Path | None,
) -> object:
    """Construct only the journal-stream relay."""

    if runtime is None:
        raise ServiceExecutionMonitorCompositionError("SOURCE_HANDLE_REQUIRED")
    if checkpoint_path is None:
        raise ServiceExecutionMonitorCompositionError("CHECKPOINT_PATH_REQUIRED")
    from .service_execution_monitor import (
        ServiceExecutionMonitorRelay,
        ServiceExecutionMonitorRelayError,
    )

    try:
        return ServiceExecutionMonitorRelay(
            runtime,
            journal_reader,
            scope_ref=scope_ref,
            checkpoint_path=checkpoint_path,
        )
    except ServiceExecutionMonitorRelayError as error:
        raise ServiceExecutionMonitorCompositionError(
            _finite_code(error, "RELAY_CONSTRUCTION_FAILED")
        ) from None
    except Exception:
        raise ServiceExecutionMonitorCompositionError("RELAY_CONSTRUCTION_FAILED") from None


def _validate_journal_scope(
    journal_reader: object | None,
    account_ref: str,
    strategy_ref: str,
) -> None:
    """Bind the trusted journal reader to the configured account/strategy."""

    scope = getattr(journal_reader, "scope", None)
    reader_account = getattr(scope, "account_ref", None)
    reader_strategy = getattr(scope, "strategy_id", None)
    if type(reader_account) is not str or type(reader_strategy) is not str:
        raise ServiceExecutionMonitorCompositionError("INVALID_SOURCE_SCOPE")
    if reader_account != account_ref or reader_strategy != strategy_ref:
        raise ServiceExecutionMonitorCompositionError("SCOPE_MISMATCH")


def _resolve_checkpoint_path(
    source: str,
    scope_key: str | None,
    *,
    checkpoint_path: str | Path | None,
    checkpoint_dir: str | Path | None,
) -> Path | None:
    """Resolve exactly one explicit or directory-derived checkpoint path."""

    if checkpoint_path is not None and checkpoint_dir is not None:
        raise ServiceExecutionMonitorCompositionError("CHECKPOINT_PATH_AMBIGUOUS")
    if checkpoint_path is not None:
        return _resolve_path(checkpoint_path)
    if checkpoint_dir is not None:
        directory = _resolve_path(checkpoint_dir)
        if source not in _ENABLED_SOURCES or scope_key is None:
            return None
        digest = _sha256(scope_key)[:32]
        return directory / f"{_CHECKPOINT_PREFIX}-{source}-{digest}{_CHECKPOINT_SUFFIX}"
    if source in _ENABLED_SOURCES:
        raise ServiceExecutionMonitorCompositionError("CHECKPOINT_PATH_REQUIRED")
    return None


def _resolve_path(value: str | Path) -> Path:
    if not isinstance(value, (str, Path)) or not str(value):
        raise ServiceExecutionMonitorCompositionError("INVALID_CHECKPOINT_PATH")
    try:
        text = str(value)
        if len(text.encode("utf-8", errors="strict")) > _MAX_PATH_BYTES:
            raise ValueError
        return Path(value).expanduser().resolve(strict=False)
    except (OSError, RuntimeError, UnicodeEncodeError, ValueError):
        raise ServiceExecutionMonitorCompositionError("INVALID_CHECKPOINT_PATH") from None


def _validate_trusted_refs(
    execution_domain_ref: object,
    account_ref: object,
    strategy_ref: object,
) -> None:
    if not all(_valid_ref(value) for value in (execution_domain_ref, account_ref, strategy_ref)):
        raise ServiceExecutionMonitorCompositionError("INVALID_TRUSTED_SCOPE")


def _valid_ref(value: object) -> bool:
    if type(value) is not str or not value or value != value.strip():
        return False
    try:
        encoded = value.encode("utf-8", errors="strict")
    except UnicodeEncodeError:
        return False
    return len(encoded) <= _MAX_REF_BYTES and all(
        ord(character) >= 32 and ord(character) != 127 for character in value
    )


def _reject_disabled_options(
    *,
    sink: object | None,
    consumer_id: str | None,
    page_limit: int | None,
    max_events_per_poll: int | None,
    byte_budget: int | None,
) -> None:
    if any(
        value is not None
        for value in (sink, consumer_id, page_limit, max_events_per_poll, byte_budget)
    ):
        raise ServiceExecutionMonitorCompositionError("UNSUPPORTED_OPTION")


def _scope_key(
    source: str,
    execution_domain_ref: str,
    account_ref: str,
    strategy_ref: str,
) -> str:
    digest = _sha256(
        _canonical_json(
            {
                "schema": _SCOPE_SCHEMA,
                "source": source,
                "execution_domain_ref": execution_domain_ref,
                "account_ref": account_ref,
                "strategy_ref": strategy_ref,
            }
        )
    )
    return _SCOPE_PREFIX + digest


def _finite_code(error: BaseException, fallback: str) -> str:
    code = getattr(error, "code", None)
    if type(code) is str and _UPPER_TOKEN_RE.fullmatch(code) is not None:
        return code
    return fallback


def _canonical_json(value: object) -> str:
    return json.dumps(
        value,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=True,
        allow_nan=False,
    )


def _sha256(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8", errors="strict")).hexdigest()


__all__ = (
    "MonitorRelayCheckpoint",
    "SOURCE_JOURNAL",
    "SOURCE_NONE",
    "SOURCE_PROJECTION",
    "ServiceExecutionMonitorComposition",
    "ServiceExecutionMonitorCompositionError",
    "compose_execution_monitor_relay",
    "derive_monitor_outbox_scope",
)
