"""Local CTP sealed-query to Gateway execution-ledger composition.

This is a bounded single-query observation, not account bootstrap, complete
venue history, authorization or a network API. Rows are projected before any
write. Ledger writes remain individually atomic; failure can leave a committed
prefix, and a failed in-flight write may also have committed. Recovery is an
explicit fresh query and idempotent replay, never an automatic retry.
"""

from __future__ import annotations

import math
import os
import threading
from dataclasses import dataclass, replace
from types import MappingProxyType

from bt_api_base.account_protocol import AccountKey, account_venue_contains_instrument_venue
from bt_api_base.market_protocol import MarketInstrumentKey
from bt_api_ctp.gateway_return_projection import project_gateway_return
from bt_api_ctp.gateway_return_query import CtpGatewayReturnQueryBatch, CtpGatewayReturnQueryReader
from bt_api_gateway.direct_execution_journal import GatewayDirectExecutionLedger

from bt_api_py.direct_write import project_direct_strong_identity_keys


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


class CtpGatewayReturnIngestor:
    """Compose one sealed reader, trusted instrument catalog and guarded ledger."""

    def __init__(
        self,
        reader: CtpGatewayReturnQueryReader,
        ledger: GatewayDirectExecutionLedger,
        *,
        account_key: AccountKey,
        instruments: tuple[MarketInstrumentKey, ...],
    ) -> None:
        if (
            type(reader) is not CtpGatewayReturnQueryReader
            or type(ledger) is not GatewayDirectExecutionLedger
            or type(account_key) is not AccountKey
            or type(instruments) is not tuple
            or not instruments
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

    def _check_owner(self) -> None:
        if os.getpid() != self._pid or threading.current_thread() is not self._thread:
            raise CtpGatewayReturnIngestionError("OWNER_REQUIRED")

    def _assert_current(self, batch: CtpGatewayReturnQueryBatch, completed: int = 0) -> None:
        try:
            self._reader.assert_current(batch)
        except Exception:
            raise CtpGatewayReturnIngestionError(
                "SOURCE_STALE", completed_count=completed
            ) from None

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
        facts = []
        try:
            for row in batch.rows:
                instrument = self._catalog[_row_key(row)]
                projection = project_gateway_return(
                    "order" if kind == "orders" else "trade",
                    row,
                    account_scope=self._account_scope,
                    source_ref=batch.source_ref,
                    expected_trading_day=batch.trading_day,
                    expected_broker=self._broker_id,
                    expected_investor=self._investor_id,
                    instrument=instrument,
                    source_generation=batch.connection_generation,
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
                    else project_direct_strong_identity_keys(
                        projection.venue_identity, day_scoped_ids=True
                    )
                )
                if projection.venue_identity is not None and len(venue_keys) != 1:
                    raise ValueError
                facts.append(
                    replace(
                        projection.fact,
                        binding_keys=tuple(sorted(binding_keys)),
                        identity_keys=tuple(sorted(set(binding_keys) | set(venue_keys))),
                        venue_key=None if not venue_keys else venue_keys[0],
                    )
                )
        except Exception:
            raise CtpGatewayReturnIngestionError("PROJECTION_FAILED") from None
        self._assert_current(batch)
        completed = 0
        counts = {"ATTRIBUTED": 0, "UNATTRIBUTED": 0, "CONFLICT": 0}
        for fact in facts:
            self._assert_current(batch, completed)
            try:
                result = self._ledger.ingest(fact)
                if result.disposition not in counts:
                    raise ValueError
                counts[result.disposition] += 1
                completed += 1
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
