"""Local CTP sealed-query to Gateway execution-ledger composition.

This is a bounded single-query observation, not account bootstrap, complete
venue history, authorization or a network API. Rows are projected before any
write. Ledger writes remain individually atomic; failure can leave a committed
prefix, and a failed in-flight write may also have committed. Recovery is an
explicit fresh query and idempotent replay, never an automatic retry.
"""

from __future__ import annotations

import logging
import math
import os
import threading
from dataclasses import dataclass, replace
from datetime import datetime
from types import MappingProxyType

from bt_api_base.account_protocol import AccountKey, account_venue_contains_instrument_venue
from bt_api_base.market_protocol import MarketInstrumentKey
from bt_api_ctp.gateway_return_projection import project_gateway_return
from bt_api_ctp.gateway_return_query import CtpGatewayReturnQueryBatch, CtpGatewayReturnQueryReader
from bt_api_gateway.direct_execution_journal import GatewayDirectExecutionLedger

from bt_api_py.direct_write import project_direct_strong_identity_keys

_LOG = logging.getLogger(__name__)
# Contracts whose skipped returns are counted; later ones are still skipped.
_MAX_UNKEYED_CONTRACTS = 1024


class CtpGatewayReturnIngestionError(RuntimeError):
    """Finite failure; completed_count is only the confirmed earlier prefix."""

    _CODES = frozenset(
        {
            "INVALID_CONFIGURATION",
            "OWNER_REQUIRED",
            "INVALID_REQUEST",
            "QUERY_FAILED",
            "PROJECTION_FAILED",
            "SOURCE_STALE",
            "INGEST_FAILED",
            "UNSUPPORTED_RETURN",
        }
    )

    def __init__(
        self, code: str, *, completed_count: int = 0, in_flight_may_have_committed: bool = False
    ) -> None:
        self.code = code if type(code) is str and code in self._CODES else "INGEST_FAILED"
        self.completed_count = completed_count
        self.in_flight_may_have_committed = in_flight_may_have_committed
        super().__init__(self.code)


@dataclass(frozen=True, slots=True)
class CtpGatewayReturnIngestionSummary:
    """One handled query; counts are returned ledger calls, including duplicates.

    This does not imply complete attribution, venue history or account readiness.
    """

    kind: str
    source_ref: str
    request_id: int
    generation: str
    connection_generation: int
    trading_day: str
    input_count: int
    ingested_count: int
    attributed_count: int
    unattributed_count: int
    conflict_count: int


def _ref(value: object) -> bool:
    if (
        type(value) is not str
        or not value
        or len(value) > 256
        or value != value.strip()
        or not value.isprintable()
    ):
        return False
    try:
        return len(value.encode("utf-8", errors="strict")) <= 256
    except UnicodeError:
        return False


def _row_key(row: object) -> tuple[str, str]:
    try:
        symbol, venue = row["InstrumentID"], row["ExchangeID"]
        if type(symbol) is not str or type(venue) is not str:
            raise ValueError
        symbol, venue = symbol.rstrip("\x00").strip(), venue.rstrip("\x00").strip()
        if not _ref(symbol) or not _ref(venue):
            raise ValueError
        return venue, symbol
    except Exception:
        raise CtpGatewayReturnIngestionError("PROJECTION_FAILED") from None


class _CallbackSourceStaleError(Exception):
    """The source failed inside the ledger's pre-commit recheck."""


@dataclass(frozen=True, slots=True)
class TrustedExternalClassification:
    """Locally issued evidence and held source fence; never a wire DTO."""

    evidence: object
    source_fence: object
    verify_source_and_exclusion: object


class CtpGatewayReturnIngestor:
    """Compose one sealed reader, trusted instrument catalog and guarded ledger."""

    def __init__(
        self,
        reader: CtpGatewayReturnQueryReader,
        ledger: GatewayDirectExecutionLedger,
        *,
        account_key: AccountKey,
        instruments: tuple[MarketInstrumentKey, ...],
        external_classifier=None,
    ) -> None:
        if (
            type(reader) is not CtpGatewayReturnQueryReader
            or type(ledger) is not GatewayDirectExecutionLedger
            or type(account_key) is not AccountKey
            or type(instruments) is not tuple
            or not instruments
            or (external_classifier is not None and not callable(external_classifier))
        ):
            raise CtpGatewayReturnIngestionError("INVALID_CONFIGURATION")
        try:
            account_key = AccountKey.from_dict(account_key.to_dict())
            environment = reader.environment
            if (
                (account_key.provider, account_key.venue, account_key.environment)
                != ("ctp", "simnow", "simnow")
                or not _ref(account_key.account_id)
                or not _ref(account_key.subaccount_id)
                or reader.broker_id != account_key.subaccount_id
                or reader.investor_id != account_key.account_id
                or environment != account_key.environment
                or ledger.account_scope != account_key.account_key_id
            ):
                raise ValueError
            catalog = {}
            for instrument in instruments:
                if type(instrument) is not MarketInstrumentKey:
                    raise ValueError
                item = MarketInstrumentKey.from_dict(instrument.to_dict())
                if (
                    item.provider != "ctp"
                    or item.product_type != "future"
                    or item.environment != environment
                    or item.instrument_id != item.native_symbol
                    or not account_venue_contains_instrument_venue(account_key, item.venue)
                ):
                    raise ValueError
                key = (item.venue, item.native_symbol)
                if key in catalog:
                    raise ValueError
                catalog[key] = item
        except Exception:
            raise CtpGatewayReturnIngestionError("INVALID_CONFIGURATION") from None
        self._pid, self._thread = os.getpid(), threading.current_thread()
        self._reader, self._ledger = reader, ledger
        self._account_scope, self._broker_id, self._investor_id = (
            account_key.account_key_id,
            account_key.subaccount_id,
            account_key.account_id,
        )
        self._catalog = MappingProxyType(catalog)
        self._external_classifier = external_classifier
        self._account_key, self._environment = account_key, environment
        self._unkeyed: dict[tuple[str, str], int] = {}

    def _instrument(self, row: object) -> MarketInstrumentKey | None:
        """The row's instrument: its configured key, else one built by the same rules.

        A return on a contract outside the configured instruments (a manual
        order, or a contract removed from the configuration during the day)
        must not stop the stream for every contract (D63-09, D63-59). Its key
        is built only from the row's own exchange and instrument id, under
        exactly the rules every configured key passed (CTP future of this
        account's exchanges, instrument id equal to the native symbol), so a
        return of the Gateway's own order on a contract configured earlier gets
        the key it was traded under; any other one ends unattributed and blocks
        only its contract. None when no such key exists.
        """
        key = _row_key(row)
        configured = self._catalog.get(key)
        if configured is not None:
            return configured
        venue, symbol = key
        if not account_venue_contains_instrument_venue(self._account_key, venue):
            return None
        # _row_key already applied the key's own text rules to both values.
        return MarketInstrumentKey("ctp", venue, self._environment, "future", symbol, symbol)

    def _note_unkeyed(self, row: object) -> None:
        """Count a skipped return; warn on the 1st, 2nd, 4th, ... per contract."""
        venue, symbol = _row_key(row)
        contract = (venue, symbol)
        if contract not in self._unkeyed and len(self._unkeyed) >= _MAX_UNKEYED_CONTRACTS:
            return
        count = self._unkeyed.get(contract, 0) + 1
        self._unkeyed[contract] = count
        if count & (count - 1) == 0:
            _LOG.warning(
                "CTP return on contract %s %s skipped (%d so far): it is not configured and no "
                "instrument key can be built for it; configured contracts are unaffected",
                venue[:64], symbol[:64], count)

    def _check_owner(self) -> None:
        if os.getpid() != self._pid or threading.current_thread() is not self._thread:
            raise CtpGatewayReturnIngestionError("OWNER_REQUIRED")

    def assert_session_current(self):
        self._check_owner()
        try:
            self._reader.assert_session_current()
        except Exception:
            raise CtpGatewayReturnIngestionError("SOURCE_STALE") from None

    def rebind_same_native(self):
        self._check_owner()
        return self._reader.rebind_same_native()

    def _assert_current(self, batch: CtpGatewayReturnQueryBatch, completed: int = 0) -> None:
        try:
            self._reader.assert_current(batch)
        except Exception:
            raise CtpGatewayReturnIngestionError(
                "SOURCE_STALE", completed_count=completed
            ) from None

    def _project(self, kind, row, *, source_ref, trading_day, connection_generation):
        """One row as a fact, or None for a row without any instrument key."""
        instrument = self._instrument(row)
        if instrument is None:
            self._note_unkeyed(row)
            return None
        projection = project_gateway_return(
            kind,
            row,
            account_scope=self._account_scope,
            source_ref=source_ref,
            expected_trading_day=trading_day,
            expected_broker=self._broker_id,
            expected_investor=self._investor_id,
            instrument=instrument,
            source_generation=connection_generation,
        )
        binding_keys = (
            ()
            if projection.binding_identity is None
            else project_direct_strong_identity_keys(
                projection.binding_identity, day_scoped_ids=True
            )
        )
        venue_keys = (
            ()
            if projection.venue_identity is None
            else project_direct_strong_identity_keys(projection.venue_identity, day_scoped_ids=True)
        )
        if projection.venue_identity is not None and len(venue_keys) != 1:
            raise ValueError
        return replace(
            projection.fact,
            binding_keys=tuple(sorted(binding_keys)),
            identity_keys=tuple(sorted(set(binding_keys) | set(venue_keys))),
            venue_key=None if not venue_keys else venue_keys[0],
        )

    def _maybe_record_external(self, fact, result, source, *, assert_source_current):
        """Classify only after normal strong-identity handling left it unresolved.

        The optional classifier belongs to trusted owner composition, not RPC or
        configuration. A missing/incomplete proof leaves the original fact
        unresolved. The sealed native source and classifier fence are both
        rechecked while the journal transaction is held.
        """
        classifier = self._external_classifier
        if classifier is None or result.disposition != "UNATTRIBUTED":
            return
        from bt_api_gateway.external_position_ledger import ExternalFactEvidence

        try:
            candidate = classifier(source, fact)
        except Exception:
            return
        if type(candidate) is not TrustedExternalClassification:
            return
        evidence = candidate.evidence
        if (type(evidence) is not ExternalFactEvidence
                or evidence.account_scope != self._account_scope
                or evidence.route != fact.route or evidence.trading_day != fact.trading_day
                or evidence.fact_digest != fact.fact_digest() or evidence.source_ref != fact.source_ref
                or evidence.session_generation != source.connection_generation
                or not callable(candidate.source_fence)
                or not callable(candidate.verify_source_and_exclusion)):
            return

        def verify(db, actual_fact, actual_evidence):
            try:
                assert_source_current()
            except Exception:
                raise _CallbackSourceStaleError from None
            if actual_fact != fact or actual_evidence != evidence:
                return False
            return candidate.verify_source_and_exclusion(db, actual_fact, actual_evidence) is True

        try:
            self._ledger.record_verified_external_fact(
                fact.fact_digest(), evidence, source_fence=candidate.source_fence,
                verify_source_and_exclusion=verify,
            )
        except _CallbackSourceStaleError:
            raise
        except Exception:
            # Recording is optional and must never turn absence of evidence into
            # a trading control action or a synthetic external entry.
            return

    def ingest_callbacks(self, batch, *, assert_current):
        """Project the complete offered prefix before writing; caller owns ACK."""
        self._check_owner()
        try:
            records = batch.records
            facts = []
            for record in records:
                try:
                    assert_current(record)
                except Exception:
                    raise CtpGatewayReturnIngestionError("SOURCE_STALE") from None
                if record.kind in {"insert_response", "insert_error"}:
                    if type(record.error_code) is not int or record.error_code <= 0:
                        raise CtpGatewayReturnIngestionError("UNSUPPORTED_RETURN")
                    from bt_api_ctp.gateway_place_return_projection import (
                        gateway_place_error_key,
                        project_gateway_place_error,
                    )

                    place_key = gateway_place_error_key(
                        record, expected_broker=self._broker_id, expected_investor=self._investor_id
                    )
                    journal = self._ledger.journal
                    command = journal.find_place_by_request_key(place_key)
                    if command is None:
                        raise ValueError
                    prepared = journal.get_prepared_place(command.reservation_id)
                    if prepared is None:
                        raise CtpGatewayReturnIngestionError("UNSUPPORTED_RETURN")
                    # The Gateway's own order; never skipped without a key.
                    instrument = self._instrument(record.row)
                    if instrument is None:
                        raise ValueError
                    projection = project_gateway_place_error(
                        record,
                        prepared,
                        account_scope=self._account_scope,
                        expected_broker=self._broker_id,
                        expected_investor=self._investor_id,
                        instrument=instrument,
                    )
                    if projection.place_key != place_key:
                        raise ValueError
                    binding_keys = project_direct_strong_identity_keys(
                        prepared.identity, day_scoped_ids=True
                    )
                    fact = replace(
                        projection.fact,
                        binding_keys=tuple(sorted(binding_keys)),
                        identity_keys=tuple(sorted(binding_keys)),
                    )
                    facts.append((fact, projection))
                    continue
                if record.kind in {"action_response", "action_error"}:
                    if type(record.error_code) is not int or record.error_code <= 0:
                        raise CtpGatewayReturnIngestionError("UNSUPPORTED_RETURN")
                    from bt_api_ctp.gateway_cancel_return_projection import (
                        gateway_cancel_error_key,
                        project_gateway_cancel_error,
                    )
                    from bt_api_gateway.direct_cancel_journal import GatewayDirectCancelJournal

                    cancel_key = gateway_cancel_error_key(
                        record, expected_broker=self._broker_id, expected_investor=self._investor_id
                    )
                    attempt = GatewayDirectCancelJournal(self._ledger.journal).find_by_request_key(
                        cancel_key
                    )
                    if attempt is None or attempt.prepared_cancel is None:
                        raise ValueError
                    if attempt.prepared_cancel.source_binding is None:
                        raise CtpGatewayReturnIngestionError("UNSUPPORTED_RETURN")
                    # The Gateway's own cancel; never skipped without a key.
                    instrument = self._instrument(record.row)
                    if instrument is None:
                        raise ValueError
                    projection = project_gateway_cancel_error(
                        record,
                        attempt.prepared_cancel,
                        account_scope=self._account_scope,
                        expected_broker=self._broker_id,
                        expected_investor=self._investor_id,
                        instrument=instrument,
                    )
                    if projection.cancel_key != cancel_key:
                        raise ValueError
                    binding_keys = project_direct_strong_identity_keys(
                        attempt.prepared_cancel.target_identity, day_scoped_ids=True
                    )
                    fact = replace(
                        projection.fact,
                        binding_keys=tuple(sorted(binding_keys)),
                        identity_keys=tuple(sorted(binding_keys)),
                    )
                    facts.append((fact, projection))
                    continue
                if record.kind not in {"order", "trade"}:
                    raise CtpGatewayReturnIngestionError("UNSUPPORTED_RETURN")
                day = record.row["TradingDay"]
                if (
                    type(day) is not str
                    or len(day) != 8
                    or not day.isascii()
                    or not day.isdigit()
                    or datetime.strptime(day, "%Y%m%d").strftime("%Y%m%d") != day
                    or day > record.reception_trading_day
                ):
                    raise ValueError
                facts.append(
                    (
                        self._project(
                            record.kind,
                            record.row,
                            source_ref=record.source_ref,
                            trading_day=day,
                            connection_generation=record.connection_generation,
                        ),
                        None,
                    )
                )
        except CtpGatewayReturnIngestionError:
            raise
        except Exception:
            raise CtpGatewayReturnIngestionError("PROJECTION_FAILED") from None
        completed = 0
        for record, (fact, error_projection) in zip(records, facts, strict=True):
            try:
                assert_current(record)
            except Exception:
                raise CtpGatewayReturnIngestionError(
                    "SOURCE_STALE", completed_count=completed
                ) from None
            if fact is None:
                # No instrument key exists for its contract; counted in _project.
                completed += 1
                continue
            ledger_returned = False
            try:
                if error_projection is None:
                    result = self._ledger.ingest(fact)
                    ledger_returned = True
                    self._maybe_record_external(
                        fact, result, record, assert_source_current=lambda: assert_current(record)
                    )
                else:

                    def before_commit(record=record):
                        try:
                            assert_current(record)
                        except Exception:
                            raise _CallbackSourceStaleError from None

                    if record.kind in {"insert_response", "insert_error"}:
                        self._ledger.ingest_place_error(
                            fact,
                            place_key=error_projection.place_key,
                            prepared_digest=error_projection.prepared_digest,
                            source_binding=error_projection.source_binding,
                            before_commit=before_commit,
                        )
                    else:
                        self._ledger.ingest_cancel_error(
                            fact,
                            cancel_key=error_projection.cancel_key,
                            prepared_digest=error_projection.prepared_digest,
                            source_binding=error_projection.source_binding,
                            before_commit=before_commit,
                        )
                completed += 1
            except _CallbackSourceStaleError:
                # Raised inside the ledger's pre-commit recheck the transaction
                # rolled back; raised by the optional external recorder after
                # the ledger returned, this fact is already committed.
                raise CtpGatewayReturnIngestionError(
                    "SOURCE_STALE", completed_count=completed,
                    in_flight_may_have_committed=ledger_returned,
                ) from None
            except Exception:
                raise CtpGatewayReturnIngestionError(
                    "INGEST_FAILED", completed_count=completed, in_flight_may_have_committed=True
                ) from None
        for record in records:
            try:
                assert_current(record)
            except Exception:
                raise CtpGatewayReturnIngestionError(
                    "SOURCE_STALE", completed_count=completed
                ) from None
        return completed

    def read_and_ingest(self, kind: str, *, timeout: float) -> CtpGatewayReturnIngestionSummary:
        """Project every row, then ingest individually while revalidating the source."""
        self._check_owner()
        if (
            type(kind) is not str
            or kind not in {"orders", "trades"}
            or type(timeout) not in (int, float)
            or not 0 < timeout <= 300
            or not math.isfinite(timeout)
        ):
            raise CtpGatewayReturnIngestionError("INVALID_REQUEST")
        try:
            batch = self._reader.read(kind=kind, timeout=timeout)
            if type(batch) is not CtpGatewayReturnQueryBatch or batch.kind != kind:
                raise ValueError
        except Exception:
            raise CtpGatewayReturnIngestionError("QUERY_FAILED") from None
        try:
            projected = [
                self._project(
                    "order" if kind == "orders" else "trade",
                    row,
                    source_ref=batch.source_ref,
                    trading_day=batch.trading_day,
                    connection_generation=batch.connection_generation,
                )
                for row in batch.rows
            ]
        except Exception:
            raise CtpGatewayReturnIngestionError("PROJECTION_FAILED") from None
        facts = [fact for fact in projected if fact is not None]
        self._assert_current(batch)
        completed = 0
        counts = {"ATTRIBUTED": 0, "UNATTRIBUTED": 0, "CONFLICT": 0}
        for fact in facts:
            self._assert_current(batch, completed)
            try:
                result = self._ledger.ingest(fact)
                self._maybe_record_external(
                    fact, result, batch, assert_source_current=lambda: self._reader.assert_current(batch)
                )
                if result.disposition not in counts:
                    raise ValueError
                counts[result.disposition] += 1
                completed += 1
            except _CallbackSourceStaleError:
                raise CtpGatewayReturnIngestionError(
                    "SOURCE_STALE", completed_count=completed, in_flight_may_have_committed=True
                ) from None
            except Exception:
                raise CtpGatewayReturnIngestionError(
                    "INGEST_FAILED", completed_count=completed, in_flight_may_have_committed=True
                ) from None
        self._assert_current(batch, completed)
        return CtpGatewayReturnIngestionSummary(
            kind,
            batch.source_ref,
            batch.request_id,
            batch.generation,
            batch.connection_generation,
            batch.trading_day,
            len(facts),
            completed,
            counts["ATTRIBUTED"],
            counts["UNATTRIBUTED"],
            counts["CONFLICT"],
        )
