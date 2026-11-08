"""Durable SDK-owned provider-neutral order correlation reservations.

This module persists only the neutral exchange, symbol, client order ID, and
reservation reference.  It never imports or stores provider-native identity.
An issuer record proves only that the SDK issued that neutral mapping; it is
not provider reservation proof, authorization, a dispatch claim, or an order
state observation.

Provider binding is a separate durable saga step.  A selected
``TypedOrderProvider`` must durably and idempotently bind the exact correlation
and issuer handle to its own native reservation before it returns its opaque
provider reservation.  Its one-shot claim and dispatch journal must then fence
replays and preserve any claimed/ambiguous attempt as UNKNOWN.  This module
does not make the issuer database and a provider database one transaction and
does not enable dispatch when those provider contracts are absent.

The SQLite file is file-backed by design: ``:memory:`` cannot provide
cross-process recovery.  Callers must choose a durable local path and protect
its filesystem access.  Reservations are append-only and permanently consume
capacity; there is no release, reset, or ID-reuse operation.
"""

from __future__ import annotations

import hashlib
import hmac
import os
import re
import secrets
import sqlite3
import threading
import time
from contextlib import contextmanager
from dataclasses import dataclass, field
from pathlib import Path
from typing import Iterator

from bt_api_base.execution_correlation_protocol import (
    ManagedOrderObservationIssuer,
    OrderCorrelation,
    OrderCorrelationIssuer,
)

__all__ = (
    "DEFAULT_MAX_ORDER_CORRELATION_RESERVATIONS",
    "DurableOrderCorrelationIssuer",
    "OrderCorrelationIssuer",
    "OrderCorrelationIssuerError",
    "OrderCorrelationIssuerCleanupError",
    "ManagedOrderObservationIssuer",
)


DEFAULT_MAX_ORDER_CORRELATION_RESERVATIONS = 1_000_000
_SCHEMA_VERSION = 1
# Client order ids go to venues verbatim (OKX ``clOrdId``: up to 32
# alphanumerics; Binance ``newClientOrderId``: up to 36 of [.A-Z:/a-z0-9_-]).
# v2 (iteration 60): 32 lowercase alphanumerics - ``c`` + 31 random hex digits,
# or, attributed to a strategy, the Base ``strategy_client_order_id`` (``s`` +
# 9-digit strategy prefix + 22 random hex digits). The v1 form
# ``sdk-order-correlation-v1-`` + 64 hex (89 characters) was rejected by both.
_IDENTIFIER_PREFIX = "c"
_IDENTIFIER_RANDOM_HEX = 31
_ORDER_OBSERVATION_PREFIX = "sdk-order-observation-v1-"
_TRADE_OBSERVATION_PREFIX = "sdk-trade-observation-v1-"
_TOKEN_HEX_RE = re.compile(r"^[0-9a-f]{64}$", re.ASCII)
_OBSERVATION_ID_RE = re.compile(
    r"^sdk-(?:order|trade)-observation-v1-[0-9a-f]{64}$", re.ASCII
)


class OrderCorrelationIssuerError(RuntimeError):
    """Stable, provider-neutral failure from the durable correlation issuer."""

    def __init__(self, code: str) -> None:
        self.code = code
        super().__init__(code)


class OrderCorrelationIssuerCleanupError(OrderCorrelationIssuerError):
    """Retain an issuer whose initialization cleanup requires a later retry."""

    def __init__(self, issuer: DurableOrderCorrelationIssuer) -> None:
        super().__init__("order_correlation_initialization_cleanup_failed")
        self._issuer = issuer

    def close(self) -> None:
        """Retry closing the exact retained issuer connection."""
        if os.getpid() != self._issuer._creation_pid:
            raise OrderCorrelationIssuerError("order_correlation_issuer_wrong_process")
        self._issuer.close()

    def __repr__(self) -> str:
        return "OrderCorrelationIssuerCleanupError('order_correlation_initialization_cleanup_failed')"


@dataclass(frozen=True, slots=True, repr=False)
class _IssuerReservationHandle:
    """Opaque SDK-only reference to one immutable durable issuer row."""

    issuer_id: str
    reservation_ref: str
    handle_mac: str = field(repr=False)

    def __repr__(self) -> str:
        return "_IssuerReservationHandle(<opaque>)"


class DurableOrderCorrelationIssuer(ManagedOrderObservationIssuer):
    """SQLite-backed provider-neutral correlation issuer.

    Each successful ``issue_order_correlation`` call appends one immutable
    row under ``BEGIN IMMEDIATE``.  The reservation reference and client order
    ID are random identifiers, but their uniqueness and recovery semantics come
    from the durable SQLite transaction and constraints, not from UUID
    generation or an in-memory registry.  ``resolve_order_correlation`` finds
    the existing row by its exact reservation reference and reconstructs the
    same SDK-private handle after a process restart; it never mints a
    replacement.

    ``validate_order_correlation`` checks only this immutable issuer mapping.
    Provider binding, one-shot consumption, authorization, dispatch, and
    UNKNOWN resolution remain provider-owned contracts.
    """

    _META_TABLE = "sdk_order_correlation_issuer_meta"
    _RESERVATION_TABLE = "sdk_order_correlation_reservations"
    _OBSERVATION_TABLE = "sdk_order_observation_bindings"
    _PROVIDER_REFERENCE_TABLE = "sdk_order_provider_references"
    _UPDATE_TRIGGER = "sdk_order_correlation_reservations_no_update"
    _DELETE_TRIGGER = "sdk_order_correlation_reservations_no_delete"

    def __init__(
        self,
        database_path: str | os.PathLike[str],
        *,
        max_reservations: int = DEFAULT_MAX_ORDER_CORRELATION_RESERVATIONS,
    ) -> None:
        self._open_database(database_path, max_reservations=max_reservations, expected_issuer_id=None)

    @classmethod
    def open_existing(
        cls,
        database_path: str | os.PathLike[str],
        *,
        expected_issuer_id: str,
        max_reservations: int = DEFAULT_MAX_ORDER_CORRELATION_RESERVATIONS,
    ) -> DurableOrderCorrelationIssuer:
        """Open an existing exact issuer without creating or repairing its store."""
        if type(expected_issuer_id) is not str or not _TOKEN_HEX_RE.fullmatch(expected_issuer_id):
            raise OrderCorrelationIssuerError("order_correlation_issuer_id_invalid")
        issuer = cls.__new__(cls)
        issuer._open_database(
            database_path, max_reservations=max_reservations, expected_issuer_id=expected_issuer_id
        )
        return issuer

    def _open_database(
        self,
        database_path: str | os.PathLike[str],
        *,
        max_reservations: int,
        expected_issuer_id: str | None,
    ) -> None:
        if (
            type(max_reservations) is not int
            or not 1 <= max_reservations <= 9_223_372_036_854_775_807
        ):
            raise OrderCorrelationIssuerError("order_correlation_capacity_invalid")
        if isinstance(database_path, bytes) or not isinstance(database_path, (str, os.PathLike)):
            raise OrderCorrelationIssuerError("order_correlation_database_path_invalid")
        raw_path = os.fspath(database_path)
        if (
            type(raw_path) is not str
            or not raw_path
            or raw_path.strip() != raw_path
            or raw_path == ":memory:"
            or raw_path.lower().startswith("file:")
        ):
            raise OrderCorrelationIssuerError("order_correlation_database_path_invalid")
        try:
            path = Path(raw_path).expanduser().resolve(strict=False)
        except (OSError, RuntimeError, ValueError) as error:
            raise OrderCorrelationIssuerError("order_correlation_database_path_invalid") from error
        if not path.parent.is_dir() or (path.exists() and path.is_dir()):
            raise OrderCorrelationIssuerError("order_correlation_database_path_invalid")

        self.database_path = path
        self.max_reservations = max_reservations
        self._lock = threading.RLock()
        self._connection: sqlite3.Connection | None = None
        self._issuer_id: str | None = None
        self._issuer_secret: bytes | None = None
        self._closed = False
        self._failed_closed = False
        self._creation_pid = os.getpid()

        try:
            connection = sqlite3.connect(
                path.as_uri() + "?mode=rw" if expected_issuer_id is not None else str(path),
                timeout=5.0,
                isolation_level=None,
                check_same_thread=False,
                uri=expected_issuer_id is not None,
            )
            self._connection = connection
            connection.row_factory = sqlite3.Row
            connection.execute("PRAGMA busy_timeout = 5000")
            connection.execute("PRAGMA foreign_keys = ON")
            if expected_issuer_id is not None:
                self._initialize_schema(expected_issuer_id=expected_issuer_id)
            journal_mode = str(connection.execute("PRAGMA journal_mode = WAL").fetchone()[0])
            if journal_mode.lower() != "wal":
                raise sqlite3.OperationalError("required WAL mode unavailable")
            connection.execute("PRAGMA synchronous = FULL")
            if int(connection.execute("PRAGMA synchronous").fetchone()[0]) != 2:
                raise sqlite3.OperationalError("required FULL synchronous mode unavailable")
            if expected_issuer_id is None:
                self._initialize_schema()
        except OrderCorrelationIssuerError:
            self._close_after_initialization_failure()
            raise
        except (OSError, sqlite3.Error) as error:
            self._close_after_initialization_failure()
            raise OrderCorrelationIssuerError("order_correlation_store_unavailable") from error
        except BaseException:
            self._close_after_initialization_failure()
            raise

    @property
    def issuer_id(self) -> str:
        """Return the non-secret store identity while this issuer is open in its process."""
        if os.getpid() != self._creation_pid:
            raise OrderCorrelationIssuerError("order_correlation_issuer_wrong_process")
        with self._lock:
            self._ensure_open_locked()
            if self._issuer_id is None:
                raise OrderCorrelationIssuerError("order_correlation_schema_corrupt")
            return self._issuer_id

    def __enter__(self) -> "DurableOrderCorrelationIssuer":
        self._ensure_open()
        return self

    def __exit__(self, exc_type: object, exc_value: object, traceback: object) -> None:
        self.close()

    def issue_order_correlation(
        self, exchange_name: str, symbol: str, *, strategy_ref: str | None = None
    ) -> tuple[OrderCorrelation, object]:
        """Durably reserve one neutral correlation and return its private handle.

        The client order id is venue-safe (32 lowercase alphanumerics). With
        ``strategy_ref`` it carries that strategy's Base client-order prefix so
        Gateway execution events route to the strategy; the prefix is an
        attribution label, not authentication.

        A failed/ambiguous database commit never returns a correlation.  The
        attempted row may have been safely burned if SQLite reported an
        ambiguous commit outcome; callers must not infer that no row exists or
        dispatch from an exception.
        """

        _validate_neutral_field(exchange_name, "exchange_name")
        _validate_neutral_field(symbol, "symbol")
        if strategy_ref is not None:
            _validate_neutral_field(strategy_ref, "strategy_ref")
            from bt_api_base.execution_row_observation_protocol import strategy_client_order_id
        self._ensure_open()
        for _ in range(8):
            token = secrets.token_hex(32)
            client_order_id = (
                _IDENTIFIER_PREFIX + token[:_IDENTIFIER_RANDOM_HEX]
                if strategy_ref is None
                else strategy_client_order_id(strategy_ref, token)
            )
            reservation_ref = "sdk-reservation-v1-" + secrets.token_hex(32)
            try:
                correlation = OrderCorrelation(
                    exchange_name=exchange_name,
                    symbol=symbol,
                    client_order_id=client_order_id,
                    reservation_ref=reservation_ref,
                )
            except (TypeError, ValueError) as error:
                raise OrderCorrelationIssuerError("order_correlation_fields_invalid") from error

            try:
                with self._write_transaction() as cursor:
                    count = int(
                        cursor.execute(
                            "SELECT COUNT(*) FROM " + self._RESERVATION_TABLE
                        ).fetchone()[0]
                    )
                    if count >= self.max_reservations:
                        raise OrderCorrelationIssuerError(
                            "order_correlation_capacity_exhausted"
                        )
                    created_at_ns = time.time_ns()
                    cursor.execute(
                        """INSERT INTO sdk_order_correlation_reservations(
                               reservation_ref, exchange_name, symbol,
                               client_order_id, created_at_ns, record_mac
                           ) VALUES (?, ?, ?, ?, ?, ?)""",
                        (
                            reservation_ref,
                            exchange_name,
                            symbol,
                            client_order_id,
                            created_at_ns,
                            self._row_mac(
                                reservation_ref,
                                exchange_name,
                                symbol,
                                client_order_id,
                                created_at_ns,
                            ),
                        ),
                    )
            except sqlite3.IntegrityError:
                # A random collision is retried only before any correlation is
                # returned.  Unique constraints remain the cross-process
                # authority for the allocation.
                continue
            except OrderCorrelationIssuerError:
                raise
            except (OSError, sqlite3.Error) as error:
                self._latch_failed_closed()
                raise OrderCorrelationIssuerError(
                    "order_correlation_store_unavailable"
                ) from error

            return correlation, self._reservation_handle(reservation_ref)
        self._latch_failed_closed()
        raise OrderCorrelationIssuerError("order_correlation_collision_exhausted")

    def resolve_order_correlation(
        self, correlation: OrderCorrelation
    ) -> object | None:
        """Resolve the exact persisted correlation to its original issuer handle.

        ``None`` means the exact reservation reference is absent or its neutral
        fields differ.  Storage failures raise a typed unavailable error so a
        caller cannot mistake I/O failure for permission to issue a replacement.
        """

        if type(correlation) is not OrderCorrelation:
            raise OrderCorrelationIssuerError("order_correlation_invalid")
        with self._lock:
            self._ensure_open_locked()
            try:
                row = self._require_connection().execute(
                    """SELECT reservation_ref, exchange_name, symbol, client_order_id,
                              created_at_ns, record_mac
                       FROM sdk_order_correlation_reservations
                       WHERE reservation_ref=?""",
                    (correlation.reservation_ref,),
                ).fetchone()
            except (UnicodeError, sqlite3.Error) as error:
                self._latch_failed_closed_locked()
                raise OrderCorrelationIssuerError(
                    "order_correlation_store_unavailable"
                ) from error
            if row is None or not self._row_matches_correlation(row, correlation):
                return None
            return self._reservation_handle(str(row["reservation_ref"]))

    def validate_order_correlation(
        self, correlation: OrderCorrelation, *, issuer_reservation: object
    ) -> bool:
        """Return exact ``bool`` for the durable issuer mapping and opaque handle.

        This method deliberately does not claim that provider-native binding,
        authorization, claim, or dispatch has occurred.  Storage errors fail
        closed as ``False``; a caller must not dispatch without a separate
        provider-owned durable binding and one-shot claim proof.
        """

        if (
            type(correlation) is not OrderCorrelation
            or type(issuer_reservation) is not _IssuerReservationHandle
        ):
            return False
        with self._lock:
            try:
                self._ensure_open_locked()
                if (
                    type(issuer_reservation.issuer_id) is not str
                    or type(issuer_reservation.reservation_ref) is not str
                    or issuer_reservation.issuer_id != self._issuer_id
                    or issuer_reservation.reservation_ref != correlation.reservation_ref
                    or type(issuer_reservation.handle_mac) is not str
                    or not _TOKEN_HEX_RE.fullmatch(issuer_reservation.handle_mac)
                ):
                    return False
                row = self._require_connection().execute(
                    """SELECT reservation_ref, exchange_name, symbol, client_order_id,
                              created_at_ns, record_mac
                       FROM sdk_order_correlation_reservations
                       WHERE reservation_ref=?""",
                    (correlation.reservation_ref,),
                ).fetchone()
                if row is None or not self._row_matches_correlation(row, correlation):
                    return False
                expected_mac = self._handle_mac(correlation.reservation_ref)
                return hmac.compare_digest(issuer_reservation.handle_mac, expected_mac)
            except (OrderCorrelationIssuerError, OSError, UnicodeError, sqlite3.Error):
                self._latch_failed_closed_locked()
                return False

    def issue_order_observation_id(
        self,
        provider_name: str,
        environment: str,
        account_ref: str,
        provider_identity: str,
    ) -> str:
        """Issue a stable opaque ID for one private provider order identity.

        The provider identity is transient HMAC input only. It is never written
        to SQLite, included in the returned value, or interpolated into errors.
        This method does not bind the order to a managed correlation.
        """

        self._validate_observation_scope(provider_name, environment, account_ref)
        _validate_private_identity(provider_identity)
        with self._lock:
            self._ensure_open_locked()
            digest = self._observation_digest(
                b"order",
                provider_name,
                environment,
                account_ref,
                provider_identity,
            )
        return _ORDER_OBSERVATION_PREFIX + digest

    def issue_trade_observation_id(
        self,
        provider_name: str,
        environment: str,
        account_ref: str,
        provider_identity: str,
    ) -> str:
        """Issue a stable opaque trade ID, domain-separated from order IDs."""

        self._validate_observation_scope(provider_name, environment, account_ref)
        _validate_private_identity(provider_identity)
        with self._lock:
            self._ensure_open_locked()
            digest = self._observation_digest(
                b"trade",
                provider_name,
                environment,
                account_ref,
                provider_identity,
            )
        return _TRADE_OBSERVATION_PREFIX + digest

    def bind_order_observation(
        self,
        order_id: str,
        provider_name: str,
        environment: str,
        account_ref: str,
        scope_ref: str,
        provider_identity: str,
        correlation: OrderCorrelation,
        *,
        issuer_reservation: object,
    ) -> bool:
        """Durably bind one observed order token to its exact neutral issuer row.

        A successful binding makes the observation resolvable to a correlation;
        it grants no cancel permission. Native identity is used only to
        recompute the scoped HMAC token and is not persisted.
        """

        self._validate_observation_scope(provider_name, environment, account_ref)
        _validate_private_identity(scope_ref)
        _validate_private_identity(provider_identity)
        if type(correlation) is not OrderCorrelation:
            raise OrderCorrelationIssuerError("order_observation_correlation_invalid")
        expected_id = self.issue_order_observation_id(
            provider_name,
            environment,
            account_ref,
            provider_identity,
        )
        if (
            type(order_id) is not str
            or not _OBSERVATION_ID_RE.fullmatch(order_id)
            or not hmac.compare_digest(order_id, expected_id)
        ):
            raise OrderCorrelationIssuerError("order_observation_id_mismatch")
        if not self.validate_order_correlation(
            correlation,
            issuer_reservation=issuer_reservation,
        ):
            raise OrderCorrelationIssuerError("order_observation_reservation_invalid")
        scope_digest = self._scope_digest(provider_name, environment, account_ref)
        ledger_scope_digest = self._ledger_scope_digest(scope_digest, scope_ref)
        created_at_ns = time.time_ns()
        try:
            with self._write_transaction() as cursor:
                self._require_correlation_row(cursor, correlation)
                existing = cursor.execute(
                    """SELECT * FROM sdk_order_observation_bindings
                       WHERE observation_id=?""",
                    (order_id,),
                ).fetchone()
                if existing is not None:
                    if self._observation_binding_matches(
                        existing,
                        order_id,
                        scope_digest,
                        ledger_scope_digest,
                        correlation,
                    ):
                        return True
                    raise OrderCorrelationIssuerError(
                        "order_observation_binding_conflict"
                    )
                correlation_binding = cursor.execute(
                    """SELECT * FROM sdk_order_observation_bindings
                       WHERE reservation_ref=?""",
                    (correlation.reservation_ref,),
                ).fetchone()
                if correlation_binding is not None:
                    raise OrderCorrelationIssuerError(
                        "order_observation_binding_conflict"
                    )
                count = int(
                    cursor.execute(
                        "SELECT COUNT(*) FROM sdk_order_observation_bindings"
                    ).fetchone()[0]
                )
                if count >= self.max_reservations:
                    raise OrderCorrelationIssuerError(
                        "order_observation_capacity_exhausted"
                    )
                cursor.execute(
                    """INSERT INTO sdk_order_observation_bindings(
                           observation_id, scope_hmac, ledger_scope_hmac,
                           reservation_ref, exchange_name, symbol, client_order_id,
                           created_at_ns, record_mac
                       ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                    (
                        order_id,
                        scope_digest,
                        ledger_scope_digest,
                        correlation.reservation_ref,
                        correlation.exchange_name,
                        correlation.symbol,
                        correlation.client_order_id,
                        created_at_ns,
                        self._observation_binding_mac(
                            order_id,
                            scope_digest,
                            ledger_scope_digest,
                            correlation,
                            created_at_ns,
                        ),
                    ),
                )
        except OrderCorrelationIssuerError:
            raise
        except (OSError, sqlite3.Error) as error:
            self._latch_failed_closed()
            raise OrderCorrelationIssuerError(
                "order_observation_store_unavailable"
            ) from error
        return True

    def resolve_order_observation(
        self,
        order_id: str,
        *,
        provider_name: str,
        environment: str,
        account_ref: str,
        scope_ref: str,
    ) -> tuple[OrderCorrelation, object] | None:
        """Resolve a bound token to neutral identity; never authorize cancel."""

        self._validate_observation_scope(provider_name, environment, account_ref)
        _validate_private_identity(scope_ref)
        if type(order_id) is not str or not _OBSERVATION_ID_RE.fullmatch(order_id):
            raise OrderCorrelationIssuerError("order_observation_id_invalid")
        if not order_id.startswith(_ORDER_OBSERVATION_PREFIX):
            return None
        scope_digest = self._scope_digest(provider_name, environment, account_ref)
        ledger_scope_digest = self._ledger_scope_digest(scope_digest, scope_ref)
        with self._lock:
            self._ensure_open_locked()
            try:
                row = self._require_connection().execute(
                    """SELECT * FROM sdk_order_observation_bindings
                       WHERE observation_id=?""",
                    (order_id,),
                ).fetchone()
                if row is None:
                    return None
                if (
                    not hmac.compare_digest(str(row["scope_hmac"]), scope_digest)
                    or not hmac.compare_digest(
                        str(row["ledger_scope_hmac"]), ledger_scope_digest
                    )
                ):
                    return None
                correlation = self._correlation_from_observation_row(row)
                if not self._observation_binding_matches(
                    row,
                    order_id,
                    scope_digest,
                    ledger_scope_digest,
                    correlation,
                ):
                    raise OrderCorrelationIssuerError(
                        "order_observation_binding_corrupt"
                    )
                cursor = self._require_connection().cursor()
                try:
                    self._require_correlation_row(cursor, correlation)
                finally:
                    cursor.close()
                return correlation, self._reservation_handle(correlation.reservation_ref)
            except OrderCorrelationIssuerError:
                raise
            except (OSError, UnicodeError, sqlite3.Error) as error:
                self._latch_failed_closed_locked()
                raise OrderCorrelationIssuerError(
                    "order_observation_store_unavailable"
                ) from error

    def bind_provider_order_reference(
        self,
        provider_reference: str,
        correlation: OrderCorrelation,
        *,
        issuer_reservation: object,
        provider_name: str,
        environment: str,
        account_ref: str,
        scope_ref: str,
    ) -> bool:
        """Durably join a provider-private managed reference to neutral intent.

        Only a domain-separated HMAC of the reference is persisted. This
        supports restart recovery without copying a native order identifier
        into the neutral correlation database.
        """

        self._validate_observation_scope(provider_name, environment, account_ref)
        _validate_private_identity(scope_ref)
        _validate_private_identity(provider_reference)
        if type(correlation) is not OrderCorrelation:
            raise OrderCorrelationIssuerError("provider_order_reference_correlation_invalid")
        if not self.validate_order_correlation(
            correlation,
            issuer_reservation=issuer_reservation,
        ):
            raise OrderCorrelationIssuerError(
                "provider_order_reference_reservation_invalid"
            )
        scope_digest = self._scope_digest(provider_name, environment, account_ref)
        ledger_scope_digest = self._ledger_scope_digest(scope_digest, scope_ref)
        reference_digest = self._provider_reference_digest(
            scope_digest,
            ledger_scope_digest,
            provider_reference,
        )
        created_at_ns = time.time_ns()
        try:
            with self._write_transaction() as cursor:
                self._require_correlation_row(cursor, correlation)
                existing = cursor.execute(
                    """SELECT * FROM sdk_order_provider_references
                       WHERE scope_hmac=? AND ledger_scope_hmac=?
                         AND provider_reference_hmac=?""",
                    (scope_digest, ledger_scope_digest, reference_digest),
                ).fetchone()
                if existing is not None:
                    if self._provider_reference_matches(
                        existing,
                        scope_digest,
                        ledger_scope_digest,
                        reference_digest,
                        correlation,
                    ):
                        return True
                    raise OrderCorrelationIssuerError(
                        "provider_order_reference_conflict"
                    )
                correlation_binding = cursor.execute(
                    """SELECT * FROM sdk_order_provider_references
                       WHERE scope_hmac=? AND ledger_scope_hmac=? AND reservation_ref=?""",
                    (scope_digest, ledger_scope_digest, correlation.reservation_ref),
                ).fetchone()
                if correlation_binding is not None:
                    raise OrderCorrelationIssuerError(
                        "provider_order_reference_conflict"
                    )
                count = int(
                    cursor.execute(
                        "SELECT COUNT(*) FROM sdk_order_provider_references"
                    ).fetchone()[0]
                )
                if count >= self.max_reservations:
                    raise OrderCorrelationIssuerError(
                        "provider_order_reference_capacity_exhausted"
                    )
                cursor.execute(
                    """INSERT INTO sdk_order_provider_references(
                           scope_hmac, ledger_scope_hmac, provider_reference_hmac,
                           reservation_ref,
                           exchange_name, symbol, client_order_id, created_at_ns,
                           record_mac
                       ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                    (
                        scope_digest,
                        ledger_scope_digest,
                        reference_digest,
                        correlation.reservation_ref,
                        correlation.exchange_name,
                        correlation.symbol,
                        correlation.client_order_id,
                        created_at_ns,
                        self._provider_reference_mac(
                            scope_digest,
                            ledger_scope_digest,
                            reference_digest,
                            correlation,
                            created_at_ns,
                        ),
                    ),
                )
        except OrderCorrelationIssuerError:
            raise
        except sqlite3.IntegrityError as error:
            self._latch_failed_closed()
            raise OrderCorrelationIssuerError(
                "provider_order_reference_conflict"
            ) from error
        except (OSError, sqlite3.Error) as error:
            self._latch_failed_closed()
            raise OrderCorrelationIssuerError(
                "provider_order_reference_store_unavailable"
            ) from error
        return True

    def resolve_provider_order_reference(
        self,
        provider_reference: str,
        *,
        provider_name: str,
        environment: str,
        account_ref: str,
        scope_ref: str,
    ) -> tuple[OrderCorrelation, object] | None:
        """Restore a previously bound provider reference and issuer handle."""

        self._validate_observation_scope(provider_name, environment, account_ref)
        _validate_private_identity(scope_ref)
        _validate_private_identity(provider_reference)
        scope_digest = self._scope_digest(provider_name, environment, account_ref)
        ledger_scope_digest = self._ledger_scope_digest(scope_digest, scope_ref)
        reference_digest = self._provider_reference_digest(
            scope_digest,
            ledger_scope_digest,
            provider_reference,
        )
        with self._lock:
            self._ensure_open_locked()
            try:
                row = self._require_connection().execute(
                    """SELECT * FROM sdk_order_provider_references
                       WHERE scope_hmac=? AND ledger_scope_hmac=?
                         AND provider_reference_hmac=?""",
                    (scope_digest, ledger_scope_digest, reference_digest),
                ).fetchone()
                if row is None:
                    return None
                correlation = self._correlation_from_provider_reference_row(row)
                if not self._provider_reference_matches(
                    row,
                    scope_digest,
                    ledger_scope_digest,
                    reference_digest,
                    correlation,
                ):
                    raise OrderCorrelationIssuerError(
                        "provider_order_reference_corrupt"
                    )
                cursor = self._require_connection().cursor()
                try:
                    self._require_correlation_row(cursor, correlation)
                finally:
                    cursor.close()
                return correlation, self._reservation_handle(correlation.reservation_ref)
            except OrderCorrelationIssuerError:
                raise
            except (OSError, UnicodeError, sqlite3.Error) as error:
                self._latch_failed_closed_locked()
                raise OrderCorrelationIssuerError(
                    "provider_order_reference_store_unavailable"
                ) from error

    def close(self) -> None:
        """Close this connection.  Reopen from the same file to resolve rows."""

        with self._lock:
            if self._closed:
                return
            connection = self._connection
            if connection is not None:
                try:
                    connection.close()
                except BaseException as error:
                    self._failed_closed = True
                    if isinstance(error, Exception):
                        raise OrderCorrelationIssuerError(
                            "order_correlation_store_close_failed"
                        ) from error
                    raise
            self._connection = None
            self._closed = True

    def _initialize_schema(self, *, expected_issuer_id: str | None = None) -> None:
        with self._write_transaction() as cursor:
            if expected_issuer_id is None:
                cursor.execute(
                    """CREATE TABLE IF NOT EXISTS sdk_order_correlation_issuer_meta (
                           singleton INTEGER PRIMARY KEY NOT NULL CHECK(singleton=1),
                           schema_version INTEGER NOT NULL,
                           issuer_id TEXT NOT NULL,
                           issuer_secret_hex TEXT NOT NULL,
                           max_reservations INTEGER NOT NULL
                       )"""
                )
                cursor.execute(
                    """CREATE TABLE IF NOT EXISTS sdk_order_correlation_reservations (
                           reservation_ref TEXT PRIMARY KEY NOT NULL,
                           exchange_name TEXT NOT NULL,
                           symbol TEXT NOT NULL,
                           client_order_id TEXT NOT NULL UNIQUE,
                           created_at_ns INTEGER NOT NULL CHECK(created_at_ns > 0),
                           record_mac TEXT NOT NULL
                       )"""
                )
                cursor.execute(
                    """CREATE TABLE IF NOT EXISTS sdk_order_observation_bindings (
                           observation_id TEXT PRIMARY KEY NOT NULL,
                           scope_hmac TEXT NOT NULL CHECK(length(scope_hmac)=64),
                           ledger_scope_hmac TEXT NOT NULL CHECK(length(ledger_scope_hmac)=64),
                           reservation_ref TEXT NOT NULL,
                           exchange_name TEXT NOT NULL,
                           symbol TEXT NOT NULL,
                           client_order_id TEXT NOT NULL,
                           created_at_ns INTEGER NOT NULL CHECK(created_at_ns > 0),
                           record_mac TEXT NOT NULL CHECK(length(record_mac)=64),
                           UNIQUE(reservation_ref),
                           FOREIGN KEY(reservation_ref)
                               REFERENCES sdk_order_correlation_reservations(reservation_ref)
                       )"""
                )
                cursor.execute(
                    """CREATE TABLE IF NOT EXISTS sdk_order_provider_references (
                           scope_hmac TEXT NOT NULL CHECK(length(scope_hmac)=64),
                           ledger_scope_hmac TEXT NOT NULL CHECK(length(ledger_scope_hmac)=64),
                           provider_reference_hmac TEXT NOT NULL
                               CHECK(length(provider_reference_hmac)=64),
                           reservation_ref TEXT NOT NULL,
                           exchange_name TEXT NOT NULL,
                           symbol TEXT NOT NULL,
                           client_order_id TEXT NOT NULL,
                           created_at_ns INTEGER NOT NULL CHECK(created_at_ns > 0),
                           record_mac TEXT NOT NULL CHECK(length(record_mac)=64),
                           PRIMARY KEY(scope_hmac, ledger_scope_hmac, provider_reference_hmac),
                           UNIQUE(scope_hmac, ledger_scope_hmac, reservation_ref),
                           FOREIGN KEY(reservation_ref)
                               REFERENCES sdk_order_correlation_reservations(reservation_ref)
                       )"""
                )
                cursor.execute(
                    """CREATE TRIGGER IF NOT EXISTS
                           sdk_order_correlation_reservations_no_update
                       BEFORE UPDATE ON sdk_order_correlation_reservations
                       BEGIN SELECT RAISE(ABORT, 'order correlation rows are immutable'); END"""
                )
                cursor.execute(
                    """CREATE TRIGGER IF NOT EXISTS
                           sdk_order_correlation_reservations_no_delete
                       BEFORE DELETE ON sdk_order_correlation_reservations
                       BEGIN SELECT RAISE(ABORT, 'order correlation rows are append-only'); END"""
                )
                cursor.execute(
                    """CREATE TRIGGER IF NOT EXISTS
                           sdk_order_observation_bindings_no_update
                       BEFORE UPDATE ON sdk_order_observation_bindings
                       BEGIN SELECT RAISE(ABORT, 'order observation rows are immutable'); END"""
                )
                cursor.execute(
                    """CREATE TRIGGER IF NOT EXISTS
                           sdk_order_observation_bindings_no_delete
                       BEFORE DELETE ON sdk_order_observation_bindings
                       BEGIN SELECT RAISE(ABORT, 'order observation rows are append-only'); END"""
                )
                cursor.execute(
                    """CREATE TRIGGER IF NOT EXISTS
                           sdk_order_provider_references_no_update
                       BEFORE UPDATE ON sdk_order_provider_references
                       BEGIN SELECT RAISE(ABORT, 'provider reference rows are immutable'); END"""
                )
                cursor.execute(
                    """CREATE TRIGGER IF NOT EXISTS
                           sdk_order_provider_references_no_delete
                       BEFORE DELETE ON sdk_order_provider_references
                       BEGIN SELECT RAISE(ABORT, 'provider reference rows are append-only'); END"""
                )
                cursor.execute(
                    """CREATE TRIGGER IF NOT EXISTS
                           sdk_order_correlation_issuer_meta_no_update
                       BEFORE UPDATE ON sdk_order_correlation_issuer_meta
                       BEGIN SELECT RAISE(ABORT, 'order correlation issuer metadata is immutable'); END"""
                )
                cursor.execute(
                    """CREATE TRIGGER IF NOT EXISTS
                           sdk_order_correlation_issuer_meta_no_delete
                       BEFORE DELETE ON sdk_order_correlation_issuer_meta
                       BEGIN SELECT RAISE(ABORT, 'order correlation issuer metadata is immutable'); END"""
                )
            self._validate_schema(cursor)
            meta_count = int(
                cursor.execute(
                    "SELECT COUNT(*) FROM sdk_order_correlation_issuer_meta"
                ).fetchone()[0]
            )
            meta = cursor.execute(
                """SELECT schema_version, issuer_id, issuer_secret_hex, max_reservations
                   FROM sdk_order_correlation_issuer_meta WHERE singleton=1"""
            ).fetchone()
            if meta is None:
                if expected_issuer_id is not None:
                    raise OrderCorrelationIssuerError("order_correlation_schema_corrupt")
                reservation_count = int(
                    cursor.execute(
                        "SELECT COUNT(*) FROM sdk_order_correlation_reservations"
                    ).fetchone()[0]
                )
                extension_count = int(
                    cursor.execute(
                        "SELECT (SELECT COUNT(*) FROM sdk_order_observation_bindings) + "
                        "(SELECT COUNT(*) FROM sdk_order_provider_references)"
                    ).fetchone()[0]
                )
                if meta_count != 0 or reservation_count != 0 or extension_count != 0:
                    raise OrderCorrelationIssuerError("order_correlation_schema_corrupt")
                issuer_id = secrets.token_hex(32)
                issuer_secret = secrets.token_bytes(32)
                cursor.execute(
                    """INSERT INTO sdk_order_correlation_issuer_meta(
                           singleton, schema_version, issuer_id, issuer_secret_hex,
                           max_reservations
                       ) VALUES (1, ?, ?, ?, ?)""",
                    (
                        _SCHEMA_VERSION,
                        issuer_id,
                        issuer_secret.hex(),
                        self.max_reservations,
                    ),
                )
                self._issuer_id = issuer_id
                self._issuer_secret = issuer_secret
            else:
                try:
                    version = int(meta["schema_version"])
                    issuer_id = str(meta["issuer_id"])
                    secret_hex = str(meta["issuer_secret_hex"])
                    stored_capacity = int(meta["max_reservations"])
                    if not _TOKEN_HEX_RE.fullmatch(secret_hex):
                        raise ValueError("invalid issuer secret encoding")
                    issuer_secret = bytes.fromhex(secret_hex)
                except (TypeError, ValueError, OverflowError) as error:
                    raise OrderCorrelationIssuerError(
                        "order_correlation_schema_corrupt"
                    ) from error
                if (
                    version != _SCHEMA_VERSION
                    or not _TOKEN_HEX_RE.fullmatch(issuer_id)
                    or len(issuer_secret) != 32
                    or meta_count != 1
                    or stored_capacity != self.max_reservations
                ):
                    raise OrderCorrelationIssuerError(
                        "order_correlation_schema_or_capacity_mismatch"
                    )
                if expected_issuer_id is not None and not hmac.compare_digest(
                    issuer_id, expected_issuer_id
                ):
                    raise OrderCorrelationIssuerError("order_correlation_issuer_id_mismatch")
                self._issuer_id = issuer_id
                self._issuer_secret = issuer_secret
            row_count = int(
                cursor.execute(
                    "SELECT COUNT(*) FROM sdk_order_correlation_reservations"
                ).fetchone()[0]
            )
            if row_count > self.max_reservations:
                raise OrderCorrelationIssuerError("order_correlation_capacity_corrupt")
            observation_count = int(
                cursor.execute(
                    "SELECT COUNT(*) FROM sdk_order_observation_bindings"
                ).fetchone()[0]
            )
            provider_reference_count = int(
                cursor.execute(
                    "SELECT COUNT(*) FROM sdk_order_provider_references"
                ).fetchone()[0]
            )
            if (
                observation_count > self.max_reservations
                or provider_reference_count > self.max_reservations
            ):
                raise OrderCorrelationIssuerError(
                    "order_correlation_extension_capacity_corrupt"
                )

    @classmethod
    def _validate_schema(cls, cursor: sqlite3.Cursor) -> None:
        expected_columns = {
            cls._META_TABLE: (
                ("singleton", "INTEGER", 1, 1),
                ("schema_version", "INTEGER", 1, 0),
                ("issuer_id", "TEXT", 1, 0),
                ("issuer_secret_hex", "TEXT", 1, 0),
                ("max_reservations", "INTEGER", 1, 0),
            ),
            cls._RESERVATION_TABLE: (
                ("reservation_ref", "TEXT", 1, 1),
                ("exchange_name", "TEXT", 1, 0),
                ("symbol", "TEXT", 1, 0),
                ("client_order_id", "TEXT", 1, 0),
                ("created_at_ns", "INTEGER", 1, 0),
                ("record_mac", "TEXT", 1, 0),
            ),
            cls._OBSERVATION_TABLE: (
                ("observation_id", "TEXT", 1, 1),
                ("scope_hmac", "TEXT", 1, 0),
                ("ledger_scope_hmac", "TEXT", 1, 0),
                ("reservation_ref", "TEXT", 1, 0),
                ("exchange_name", "TEXT", 1, 0),
                ("symbol", "TEXT", 1, 0),
                ("client_order_id", "TEXT", 1, 0),
                ("created_at_ns", "INTEGER", 1, 0),
                ("record_mac", "TEXT", 1, 0),
            ),
            cls._PROVIDER_REFERENCE_TABLE: (
                ("scope_hmac", "TEXT", 1, 1),
                ("ledger_scope_hmac", "TEXT", 1, 2),
                ("provider_reference_hmac", "TEXT", 1, 3),
                ("reservation_ref", "TEXT", 1, 0),
                ("exchange_name", "TEXT", 1, 0),
                ("symbol", "TEXT", 1, 0),
                ("client_order_id", "TEXT", 1, 0),
                ("created_at_ns", "INTEGER", 1, 0),
                ("record_mac", "TEXT", 1, 0),
            ),
        }
        for table, expected in expected_columns.items():
            actual = tuple(
                (str(row["name"]), str(row["type"]).upper(), int(row["notnull"]), int(row["pk"]))
                for row in cursor.execute("PRAGMA table_info(" + table + ")").fetchall()
            )
            if actual != expected:
                raise OrderCorrelationIssuerError("order_correlation_schema_mismatch")

        unique_indexes = cursor.execute(
            "PRAGMA index_list(" + cls._RESERVATION_TABLE + ")"
        ).fetchall()
        unique_columns = set()
        for index in unique_indexes:
            if int(index["unique"]):
                if int(index["partial"]):
                    continue
                index_name = str(index["name"])
                columns = tuple(
                    str(row["name"])
                    for row in cursor.execute(
                        "PRAGMA index_info('" + index_name.replace("'", "''") + "')"
                    ).fetchall()
                )
                unique_columns.add(columns)
        if (
            ("reservation_ref",) not in unique_columns
            or ("client_order_id",) not in unique_columns
        ):
            raise OrderCorrelationIssuerError("order_correlation_schema_mismatch")

        required_unique_indexes = {
            cls._OBSERVATION_TABLE: {
                ("observation_id",),
                ("reservation_ref",),
            },
            cls._PROVIDER_REFERENCE_TABLE: {
                ("scope_hmac", "ledger_scope_hmac", "provider_reference_hmac"),
                ("scope_hmac", "ledger_scope_hmac", "reservation_ref"),
            },
        }
        for table, required in required_unique_indexes.items():
            indexes = cursor.execute("PRAGMA index_list(" + table + ")").fetchall()
            found = set()
            for index in indexes:
                if not int(index["unique"]) or int(index["partial"]):
                    continue
                index_name = str(index["name"])
                columns = tuple(
                    str(row["name"])
                    for row in cursor.execute(
                        "PRAGMA index_info('" + index_name.replace("'", "''") + "')"
                    ).fetchall()
                )
                found.add(columns)
            if not required.issubset(found):
                raise OrderCorrelationIssuerError("order_correlation_schema_mismatch")

        expected_triggers = (
            (
                cls._UPDATE_TRIGGER,
                "CREATE TRIGGER {} BEFORE UPDATE ON {} BEGIN SELECT RAISE(ABORT, "
                "'ORDER CORRELATION ROWS ARE IMMUTABLE'); END".format(
                    cls._UPDATE_TRIGGER.upper(), cls._RESERVATION_TABLE.upper()
                ),
            ),
            (
                cls._DELETE_TRIGGER,
                "CREATE TRIGGER {} BEFORE DELETE ON {} BEGIN SELECT RAISE(ABORT, "
                "'ORDER CORRELATION ROWS ARE APPEND-ONLY'); END".format(
                    cls._DELETE_TRIGGER.upper(), cls._RESERVATION_TABLE.upper()
                ),
            ),
            (
                "sdk_order_observation_bindings_no_update",
                "CREATE TRIGGER SDK_ORDER_OBSERVATION_BINDINGS_NO_UPDATE BEFORE UPDATE ON "
                "SDK_ORDER_OBSERVATION_BINDINGS BEGIN SELECT RAISE(ABORT, "
                "'ORDER OBSERVATION ROWS ARE IMMUTABLE'); END",
            ),
            (
                "sdk_order_observation_bindings_no_delete",
                "CREATE TRIGGER SDK_ORDER_OBSERVATION_BINDINGS_NO_DELETE BEFORE DELETE ON "
                "SDK_ORDER_OBSERVATION_BINDINGS BEGIN SELECT RAISE(ABORT, "
                "'ORDER OBSERVATION ROWS ARE APPEND-ONLY'); END",
            ),
            (
                "sdk_order_provider_references_no_update",
                "CREATE TRIGGER SDK_ORDER_PROVIDER_REFERENCES_NO_UPDATE BEFORE UPDATE ON "
                "SDK_ORDER_PROVIDER_REFERENCES BEGIN SELECT RAISE(ABORT, "
                "'PROVIDER REFERENCE ROWS ARE IMMUTABLE'); END",
            ),
            (
                "sdk_order_provider_references_no_delete",
                "CREATE TRIGGER SDK_ORDER_PROVIDER_REFERENCES_NO_DELETE BEFORE DELETE ON "
                "SDK_ORDER_PROVIDER_REFERENCES BEGIN SELECT RAISE(ABORT, "
                "'PROVIDER REFERENCE ROWS ARE APPEND-ONLY'); END",
            ),
            (
                "sdk_order_correlation_issuer_meta_no_update",
                "CREATE TRIGGER SDK_ORDER_CORRELATION_ISSUER_META_NO_UPDATE BEFORE UPDATE ON "
                "SDK_ORDER_CORRELATION_ISSUER_META BEGIN SELECT RAISE(ABORT, "
                "'ORDER CORRELATION ISSUER METADATA IS IMMUTABLE'); END",
            ),
            (
                "sdk_order_correlation_issuer_meta_no_delete",
                "CREATE TRIGGER SDK_ORDER_CORRELATION_ISSUER_META_NO_DELETE BEFORE DELETE ON "
                "SDK_ORDER_CORRELATION_ISSUER_META BEGIN SELECT RAISE(ABORT, "
                "'ORDER CORRELATION ISSUER METADATA IS IMMUTABLE'); END",
            ),
        )
        for trigger_name, expected_sql in expected_triggers:
            trigger = cursor.execute(
                "SELECT sql FROM sqlite_master WHERE type='trigger' AND name=?",
                (trigger_name,),
            ).fetchone()
            if trigger is None or " ".join(str(trigger["sql"]).upper().split()) != " ".join(
                expected_sql.split()
            ):
                raise OrderCorrelationIssuerError("order_correlation_schema_mismatch")

    @contextmanager
    def _write_transaction(self) -> Iterator[sqlite3.Cursor]:
        with self._lock:
            self._ensure_open_locked()
            connection = self._require_connection()
            cursor = connection.cursor()
            began = False
            try:
                cursor.execute("BEGIN IMMEDIATE")
                began = True
                yield cursor
                cursor.execute("COMMIT")
                began = False
            except BaseException:
                if began:
                    try:
                        cursor.execute("ROLLBACK")
                    except BaseException:
                        self._latch_failed_closed_locked()
                raise
            finally:
                cursor.close()

    def _reservation_handle(self, reservation_ref: str) -> _IssuerReservationHandle:
        issuer_id = self._issuer_id
        if issuer_id is None:
            raise OrderCorrelationIssuerError("order_correlation_schema_corrupt")
        return _IssuerReservationHandle(
            issuer_id=issuer_id,
            reservation_ref=reservation_ref,
            handle_mac=self._handle_mac(reservation_ref),
        )

    def _handle_mac(self, reservation_ref: str) -> str:
        secret = self._issuer_secret
        if secret is None:
            raise OrderCorrelationIssuerError("order_correlation_schema_corrupt")
        message = b"bt_api_py.order-correlation-issuer.v1:" + reservation_ref.encode("ascii")
        return hmac.new(secret, message, hashlib.sha256).hexdigest()

    def _row_matches_correlation(self, row: sqlite3.Row, correlation: OrderCorrelation) -> bool:
        try:
            created_at_ns = int(row["created_at_ns"])
            record_mac = str(row["record_mac"])
            if not _TOKEN_HEX_RE.fullmatch(record_mac):
                return False
            expected_mac = self._row_mac(
                str(row["reservation_ref"]),
                str(row["exchange_name"]),
                str(row["symbol"]),
                str(row["client_order_id"]),
                created_at_ns,
            )
        except (KeyError, TypeError, ValueError, UnicodeError, OrderCorrelationIssuerError):
            return False
        return (
            hmac.compare_digest(record_mac, expected_mac)
            and created_at_ns > 0
            and str(row["reservation_ref"]) == correlation.reservation_ref
            and str(row["exchange_name"]) == correlation.exchange_name
            and str(row["symbol"]) == correlation.symbol
            and str(row["client_order_id"]) == correlation.client_order_id
        )

    def _row_mac(
        self,
        reservation_ref: str,
        exchange_name: str,
        symbol: str,
        client_order_id: str,
        created_at_ns: int,
    ) -> str:
        issuer_id = self._issuer_id
        secret = self._issuer_secret
        if issuer_id is None or secret is None:
            raise OrderCorrelationIssuerError("order_correlation_schema_corrupt")
        values = (
            b"bt_api_py.order-correlation-row.v1",
            issuer_id.encode("ascii"),
            reservation_ref.encode("utf-8", errors="strict"),
            exchange_name.encode("utf-8", errors="strict"),
            symbol.encode("utf-8", errors="strict"),
            client_order_id.encode("utf-8", errors="strict"),
            str(created_at_ns).encode("ascii"),
        )
        material = b"".join(len(value).to_bytes(4, "big") + value for value in values)
        return hmac.new(secret, material, hashlib.sha256).hexdigest()

    def _validate_observation_scope(
        self,
        provider_name: str,
        environment: str,
        account_ref: str,
    ) -> None:
        _validate_neutral_field(provider_name, "provider_name")
        _validate_neutral_field(environment, "environment")
        _validate_neutral_field(account_ref, "account_ref")

    def _scope_digest(
        self,
        provider_name: str,
        environment: str,
        account_ref: str,
    ) -> str:
        return self._private_digest(
            b"order-observation-scope.v1",
            provider_name,
            environment,
            account_ref,
        )

    def _ledger_scope_digest(self, scope_digest: str, scope_ref: str) -> str:
        return self._private_digest(
            b"order-observation-ledger-scope.v1",
            scope_digest,
            scope_ref,
        )

    def _observation_digest(
        self,
        kind: bytes,
        provider_name: str,
        environment: str,
        account_ref: str,
        provider_identity: str,
    ) -> str:
        if kind == b"order":
            domain = b"order-observation-id.v1"
        elif kind == b"trade":
            domain = b"trade-observation-id.v1"
        else:
            raise OrderCorrelationIssuerError("order_observation_kind_invalid")
        return self._private_digest(
            domain,
            provider_name,
            environment,
            account_ref,
            provider_identity,
        )

    def _provider_reference_digest(
        self,
        scope_digest: str,
        ledger_scope_digest: str,
        provider_reference: str,
    ) -> str:
        return self._private_digest(
            b"provider-order-reference.v1",
            scope_digest,
            ledger_scope_digest,
            provider_reference,
        )

    def _private_digest(self, domain: bytes, *parts: str) -> str:
        with self._lock:
            self._ensure_open_locked()
            secret = self._issuer_secret
            issuer_id = self._issuer_id
            if secret is None or issuer_id is None:
                raise OrderCorrelationIssuerError("order_correlation_schema_corrupt")
            values = (b"bt_api_py.order-correlation-private-index.v1", issuer_id.encode("ascii"), domain)
            values += tuple(part.encode("utf-8", errors="strict") for part in parts)
            material = b"".join(len(value).to_bytes(4, "big") + value for value in values)
            return hmac.new(secret, material, hashlib.sha256).hexdigest()

    def _require_correlation_row(
        self,
        cursor: sqlite3.Cursor,
        correlation: OrderCorrelation,
    ) -> None:
        row = cursor.execute(
            """SELECT reservation_ref, exchange_name, symbol, client_order_id,
                      created_at_ns, record_mac
               FROM sdk_order_correlation_reservations WHERE reservation_ref=?""",
            (correlation.reservation_ref,),
        ).fetchone()
        if row is None or not self._row_matches_correlation(row, correlation):
            raise OrderCorrelationIssuerError("order_correlation_schema_corrupt")

    @staticmethod
    def _correlation_from_observation_row(row: sqlite3.Row) -> OrderCorrelation:
        try:
            return OrderCorrelation(
                exchange_name=str(row["exchange_name"]),
                symbol=str(row["symbol"]),
                client_order_id=str(row["client_order_id"]),
                reservation_ref=str(row["reservation_ref"]),
            )
        except (KeyError, TypeError, ValueError) as error:
            raise OrderCorrelationIssuerError(
                "order_observation_binding_corrupt"
            ) from error

    @staticmethod
    def _correlation_from_provider_reference_row(row: sqlite3.Row) -> OrderCorrelation:
        try:
            return OrderCorrelation(
                exchange_name=str(row["exchange_name"]),
                symbol=str(row["symbol"]),
                client_order_id=str(row["client_order_id"]),
                reservation_ref=str(row["reservation_ref"]),
            )
        except (KeyError, TypeError, ValueError) as error:
            raise OrderCorrelationIssuerError(
                "provider_order_reference_corrupt"
            ) from error

    def _observation_binding_matches(
        self,
        row: sqlite3.Row,
        order_id: str,
        scope_digest: str,
        ledger_scope_digest: str,
        correlation: OrderCorrelation,
    ) -> bool:
        try:
            created_at_ns = int(row["created_at_ns"])
            record_mac = str(row["record_mac"])
            stored_scope = str(row["scope_hmac"])
            stored_id = str(row["observation_id"])
            expected_mac = self._observation_binding_mac(
                order_id,
                scope_digest,
                ledger_scope_digest,
                correlation,
                created_at_ns,
            )
        except (KeyError, TypeError, ValueError, UnicodeError, OrderCorrelationIssuerError):
            return False
        return (
            created_at_ns > 0
            and hmac.compare_digest(record_mac, expected_mac)
            and hmac.compare_digest(stored_scope, scope_digest)
            and hmac.compare_digest(
                str(row["ledger_scope_hmac"]), ledger_scope_digest
            )
            and hmac.compare_digest(stored_id, order_id)
            and str(row["reservation_ref"]) == correlation.reservation_ref
            and str(row["exchange_name"]) == correlation.exchange_name
            and str(row["symbol"]) == correlation.symbol
            and str(row["client_order_id"]) == correlation.client_order_id
        )

    def _provider_reference_matches(
        self,
        row: sqlite3.Row,
        scope_digest: str,
        ledger_scope_digest: str,
        reference_digest: str,
        correlation: OrderCorrelation,
    ) -> bool:
        try:
            created_at_ns = int(row["created_at_ns"])
            record_mac = str(row["record_mac"])
            stored_scope = str(row["scope_hmac"])
            stored_reference = str(row["provider_reference_hmac"])
            expected_mac = self._provider_reference_mac(
                scope_digest,
                ledger_scope_digest,
                reference_digest,
                correlation,
                created_at_ns,
            )
        except (KeyError, TypeError, ValueError, UnicodeError, OrderCorrelationIssuerError):
            return False
        return (
            created_at_ns > 0
            and hmac.compare_digest(record_mac, expected_mac)
            and hmac.compare_digest(stored_scope, scope_digest)
            and hmac.compare_digest(
                str(row["ledger_scope_hmac"]), ledger_scope_digest
            )
            and hmac.compare_digest(stored_reference, reference_digest)
            and str(row["reservation_ref"]) == correlation.reservation_ref
            and str(row["exchange_name"]) == correlation.exchange_name
            and str(row["symbol"]) == correlation.symbol
            and str(row["client_order_id"]) == correlation.client_order_id
        )

    def _observation_binding_mac(
        self,
        order_id: str,
        scope_digest: str,
        ledger_scope_digest: str,
        correlation: OrderCorrelation,
        created_at_ns: int,
    ) -> str:
        return self._private_record_mac(
            b"order-observation-binding-row.v1",
            order_id,
            scope_digest,
            ledger_scope_digest,
            correlation.reservation_ref,
            correlation.exchange_name,
            correlation.symbol,
            correlation.client_order_id,
            str(created_at_ns),
        )

    def _provider_reference_mac(
        self,
        scope_digest: str,
        ledger_scope_digest: str,
        reference_digest: str,
        correlation: OrderCorrelation,
        created_at_ns: int,
    ) -> str:
        return self._private_record_mac(
            b"provider-order-reference-row.v1",
            scope_digest,
            ledger_scope_digest,
            reference_digest,
            correlation.reservation_ref,
            correlation.exchange_name,
            correlation.symbol,
            correlation.client_order_id,
            str(created_at_ns),
        )

    def _private_record_mac(self, domain: bytes, *parts: str) -> str:
        with self._lock:
            self._ensure_open_locked()
            secret = self._issuer_secret
            issuer_id = self._issuer_id
            if secret is None or issuer_id is None:
                raise OrderCorrelationIssuerError("order_correlation_schema_corrupt")
            values = (b"bt_api_py.order-correlation-private-row.v1", issuer_id.encode("ascii"), domain)
            values += tuple(part.encode("utf-8", errors="strict") for part in parts)
            material = b"".join(len(value).to_bytes(4, "big") + value for value in values)
            return hmac.new(secret, material, hashlib.sha256).hexdigest()

    def _require_connection(self) -> sqlite3.Connection:
        connection = self._connection
        if connection is None:
            raise OrderCorrelationIssuerError("order_correlation_issuer_closed")
        return connection

    def _ensure_open(self) -> None:
        with self._lock:
            self._ensure_open_locked()

    def _ensure_open_locked(self) -> None:
        if self._closed or self._failed_closed:
            raise OrderCorrelationIssuerError("order_correlation_issuer_unavailable")
        if self._connection is None:
            raise OrderCorrelationIssuerError("order_correlation_issuer_unavailable")

    def _latch_failed_closed(self) -> None:
        with self._lock:
            self._latch_failed_closed_locked()

    def _latch_failed_closed_locked(self) -> None:
        self._failed_closed = True

    def _close_after_initialization_failure(self) -> None:
        self._failed_closed = True
        try:
            self.close()
        except BaseException:
            raise OrderCorrelationIssuerCleanupError(self) from None


def _validate_neutral_field(value: str, field_name: str) -> None:
    if type(value) is not str or not value or value != value.strip():
        raise OrderCorrelationIssuerError("order_correlation_{}_invalid".format(field_name))
    try:
        encoded = value.encode("utf-8", errors="strict")
    except UnicodeEncodeError as error:
        raise OrderCorrelationIssuerError(
            "order_correlation_{}_invalid".format(field_name)
        ) from error
    if len(encoded) > 256 or not value.isprintable():
        raise OrderCorrelationIssuerError("order_correlation_{}_invalid".format(field_name))


def _validate_private_identity(value: str) -> None:
    """Validate transient provider identity input without exposing its value."""

    if type(value) is not str or not value or value != value.strip() or not value.isprintable():
        raise OrderCorrelationIssuerError("provider_private_identity_invalid")
    try:
        encoded = value.encode("utf-8", errors="strict")
    except UnicodeEncodeError as error:
        raise OrderCorrelationIssuerError("provider_private_identity_invalid") from error
    if len(encoded) > 2048:
        raise OrderCorrelationIssuerError("provider_private_identity_invalid")
