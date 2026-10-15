"""Versioned account-books projection of durable managed-paper fill facts."""

from __future__ import annotations

from decimal import Decimal
from time import time_ns
from types import SimpleNamespace

import bt_api_execution.store as store_module
import pytest
from bt_api_base.service_protocol import WireIdentity, WireRole, WireScope
from bt_api_execution import (
    PAPER_ENVIRONMENT,
    PAPER_MODEL_REF,
    PAPER_PROVIDER,
    AllocationOwnerKind,
    ExecutionAdmissionValidatorRegistry,
    ExecutionBasicAdmissionGate,
    ExecutionOwnerScope,
    ExecutionProviderValidatorV1,
    ExecutionScope,
    ExecutionScopeDirectory,
    ExecutionState,
    OrderIntent,
    OrderType,
    PaperInstrumentV1,
    PaperMatchingPort,
    PaperOwnerDomainV1,
    PaperQuoteV1,
    PositionEffect,
    ProviderObservation,
    Side,
    SqliteExecutionStore,
    TimeInForce,
)
from bt_api_execution.paper_execution import (
    PAPER_FEE_MODEL_REF,
    PAPER_FEE_MODEL_VERSION,
    PAPER_MATCH_RECEIPT_SCHEMA_V2,
    PAPER_MATCH_TIME_SOURCE_V2,
    PaperMatchReceiptV1,
    PaperMatchReceiptV2,
)
from bt_api_execution.service_order_entry import SUBMIT_OPERATION, ExecutionOrderEntryService

from bt_api_py.service_execution import (
    ServiceExecutionClient,
    ServiceExecutionError,
)
from bt_api_py.service_execution_account_books import (
    ACCOUNT_BOOKS_QUERY_OPERATION,
    ExecutionAccountBookReadAdapter,
)
from bt_api_py.service_runtime import ServiceClientRuntime

_DOMAIN = "paper:fill-recovery-test"
_ACCOUNT = "fill-recovery-account"
_STRATEGY = "fill-recovery-strategy"
_PRINCIPAL = "fill-recovery-principal"
_WIRE_SCOPE = WireScope(_DOMAIN, account_ref=_ACCOUNT, strategy_ref=_STRATEGY)
_EXECUTION_SCOPE = ExecutionScope(
    PAPER_PROVIDER, PAPER_ENVIRONMENT, _ACCOUNT, _STRATEGY, "20261016"
)
_IDENTITY = WireIdentity(WireRole.STRATEGY_CLIENT, _PRINCIPAL, _STRATEGY)
# Canonical EVENTS_AFTER v2 `details.paper_fill_v2` child schema for the SDK
# and Backtrader consumers. `submit_fingerprint` remains on the enclosing
# event; `event_created_at_ns` is diagnostic receipt data and is never projected.
_V2_FIELD_TYPES = {
    "schema": str,
    "time_source": str,
    "fill_id": str,
    "match_time_ns": int,
    "quantity": str,
    "price": str,
    "commission": str,
    "commission_currency": str,
    "commission_model_id": str,
    "commission_model_version": str,
    "event_id": str,
    "event_sequence": int,
    "journal_generation": str,
    "scope_key": str,
    "intent_id": str,
    "intent_fingerprint": str,
    "account_ref": str,
    "strategy_ref": str,
    "quote_source_ref": str,
    "quote_generation": str,
    "quote_cursor": int,
    "quote_instrument": str,
    "receipt_sha256": str,
}
_V2_FIELDS = frozenset(_V2_FIELD_TYPES)


class _QuoteSource:
    def __init__(self, *, available: bool = True) -> None:
        self.available = available
        self.calls: list[str] = []

    def read_quote(self, instrument: str) -> PaperQuoteV1 | None:
        self.calls.append(instrument)
        if not self.available:
            return None
        return PaperQuoteV1(
            source_ref="synthetic-fill-recovery-quote",
            generation="synthetic-fill-recovery-generation",
            cursor=len(self.calls),
            instrument=instrument,
            metadata_version="instrument-v1",
            quote_currency="USDT",
            bid=Decimal("9.98"),
            ask=Decimal("10.02"),
            bid_quantity=Decimal("5"),
            ask_quantity=Decimal("5"),
            as_of_ns=time_ns() - 1_000_000,
        )


class _IncrementingClock:
    def __init__(self) -> None:
        self.value = time_ns() + 1_000_000_000

    def time_ns(self) -> int:
        self.value += 10_000
        return self.value


class _ReadSource:
    def __init__(self, scope: ExecutionScope, wire_scope: WireScope) -> None:
        self.directory = ExecutionScopeDirectory(
            account_key=scope.account_key,
            source="synthetic-test-owner-directory",
            source_ref="synthetic-test-owner-resource",
            revision="fill-recovery-directory-v1",
            covers_from_ns=1,
            as_of_ns=2,
            scopes=(
                ExecutionOwnerScope(
                    scope,
                    AllocationOwnerKind.STRATEGY,
                    scope.strategy_id,
                    wire_scope,
                ),
            ),
        )

    def read_scope_directory(self, *, account_key: str) -> ExecutionScopeDirectory:
        assert account_key == self.directory.account_key
        return self.directory

    def authorize_account_view(self, **_kwargs) -> bool:
        return False

    def authorize_manual_book_view(self, **_kwargs) -> bool:
        return False

    def read_inputs(self, **_kwargs):
        raise AssertionError("recovery views must not read account/provider snapshots")


def _paper_domain() -> PaperOwnerDomainV1:
    return PaperOwnerDomainV1(
        execution_domain_ref=_DOMAIN,
        simulation_account_ref=_ACCOUNT,
        experiment_ref="fill-recovery-experiment",
        strategy_ref=_STRATEGY,
        model_ref=PAPER_MODEL_REF,
        model_version="1",
        quote_source_ref="synthetic-fill-recovery-quote",
        cash_currency="USDT",
        initial_cash=Decimal("100"),
        initial_fees=Decimal("0"),
        fee_rate=Decimal("0.001"),
        fee_quantum=Decimal("0.0001"),
        max_quote_age_ns=30_000_000_000,
        instruments=(
            PaperInstrumentV1(
                "BTC-USDT", "instrument-v1", "BTC", "USDT", Decimal("0.25"), Decimal("0.01")
            ),
        ),
        opening_positions=(),
    )


def _build_paper_owner(path, *, quote_source: _QuoteSource | None = None):
    store = SqliteExecutionStore(path)
    try:
        quotes = _QuoteSource() if quote_source is None else quote_source
        domain = _paper_domain()
        port = PaperMatchingPort(domain, quotes)
        validator = ExecutionProviderValidatorV1(
            provider_id=PAPER_PROVIDER,
            validator_id="fill-recovery-market-fok",
            version="1",
            dispatch_port=port,
            instrument_map={"BTC-USDT": "BTC-USDT"},
            quantity_steps={"BTC-USDT": Decimal("0.25")},
            max_quantities={"BTC-USDT": Decimal("10")},
            price_ticks={"BTC-USDT": Decimal("0.01")},
            supported_order_types=(OrderType.MARKET,),
            supported_time_in_force=(TimeInForce.FOK,),
            supported_actions=((Side.BUY, PositionEffect.OPEN),),
            supports_reduce_only=True,
        )
        gate = ExecutionBasicAdmissionGate(
            store,
            ExecutionAdmissionValidatorRegistry((validator,)),
            permit_ttl_ns=60_000_000_000,
        )
        service = ExecutionOrderEntryService(
            store,
            {_WIRE_SCOPE: _EXECUTION_SCOPE},
            port,
            basic_admission_gate=gate,
            paper_owner_domain=domain,
            writer_id="fill-recovery-test-owner",
            lease_ttl_ns=60_000_000_000,
        )
        service.recover_interrupted_dispatches()
        adapter = ExecutionAccountBookReadAdapter(
            store,
            {_WIRE_SCOPE: _EXECUTION_SCOPE},
            _ReadSource(_EXECUTION_SCOPE, _WIRE_SCOPE),
        )
        return store, quotes, service, adapter
    except Exception:
        store.close()
        raise


def _submit(service: ExecutionOrderEntryService, intent_id: str):
    return service.handle(
        SUBMIT_OPERATION,
        principal_ref=_PRINCIPAL,
        identity=_IDENTITY,
        scope=_WIRE_SCOPE,
        arguments={
            "intent_id": intent_id,
            "signal_id": f"signal-{intent_id}",
            "instrument": "BTC-USDT",
            "side": "BUY",
            "position_effect": "OPEN",
            "order_type": "MARKET",
            "quantity": "1.25",
            "price": None,
            "time_in_force": "FOK",
            "reduce_only": False,
            "metadata_version": "instrument-v1",
            "deadline_ns": None,
            "tags": {},
        },
    )


def _events_page(adapter, store, *, version: int):
    generation = store.journal_source_identity()["generation"]
    return adapter.handle(
        ACCOUNT_BOOKS_QUERY_OPERATION,
        principal_ref=_PRINCIPAL,
        identity=_IDENTITY,
        scope=_WIRE_SCOPE,
        arguments={
            "view": "EVENTS_AFTER",
            "version": version,
            "after_sequence": 0,
            "expected_journal_generation": generation,
            "expected_directory_revision": "fill-recovery-directory-v1",
            "limit": 128,
        },
    )


@pytest.mark.unit
def test_recovery_v2_projects_only_the_validated_managed_paper_receipt(
    tmp_path, monkeypatch
) -> None:
    store, quotes, service, adapter = _build_paper_owner(tmp_path / "fill-v2.sqlite3")
    try:
        monkeypatch.setattr(
            store_module, "time", SimpleNamespace(time_ns=_IncrementingClock().time_ns)
        )
        result = _submit(service, "fill-v2-order")
        assert result["ok"] is True and result["code"] == "OK", result
        event = next(
            item
            for item in store.read_outbox(scope=_EXECUTION_SCOPE, after_sequence=0, limit=32)
            if item.event_type == "provider_observation"
        )
        receipt = store.get_managed_paper_fill_v2_for_event(
            event.event_id,
            event_sequence=event.sequence,
            journal_generation=event.journal_incarnation_id,
            scope=_EXECUTION_SCOPE,
        )
        assert type(receipt) is PaperMatchReceiptV2
        changes_before_reads = store._connection.total_changes

        page_v1 = _events_page(adapter, store, version=1)
        assert page_v1["ok"] is True and page_v1["code"] == "OK"
        assert page_v1["result"]["version"] == 1
        assert all("paper_fill_v2" not in row["details"] for row in page_v1["result"]["events"])

        page_v2 = _events_page(adapter, store, version=2)
        assert page_v2["ok"] is True and page_v2["code"] == "OK"
        page = page_v2["result"]
        assert page["view"] == "EVENTS_AFTER" and page["version"] == 2
        projected = next(row for row in page["events"] if row["event_id"] == event.event_id)
        fill = projected["details"]["paper_fill_v2"]
        assert set(fill) == _V2_FIELDS
        assert all(type(fill[key]) is field_type for key, field_type in _V2_FIELD_TYPES.items())
        assert "event_created_at_ns" not in fill
        assert fill == {
            key: value for key, value in receipt.to_fill_fact_payload().items() if key in _V2_FIELDS
        }
        assert fill["schema"] == PAPER_MATCH_RECEIPT_SCHEMA_V2
        assert fill["time_source"] == PAPER_MATCH_TIME_SOURCE_V2
        assert fill["fill_id"] == f"paper:{event.journal_incarnation_id}:{event.event_id}"
        assert fill["match_time_ns"] == receipt.match_time_ns
        assert fill["match_time_ns"] != event.created_at_ns
        assert fill["quantity"] == "1.25"
        assert fill["price"] == "10.02"
        assert fill["commission"] == "0.0125"
        assert fill["commission_currency"] == "USDT"
        assert fill["commission_model_id"] == PAPER_FEE_MODEL_REF
        assert fill["commission_model_version"] == PAPER_FEE_MODEL_VERSION
        assert fill["event_sequence"] == projected["sequence"] == event.sequence
        assert fill["journal_generation"] == page["coverage"]["journal_generation"]
        assert fill["scope_key"] == projected["scope_key"] == _EXECUTION_SCOPE.key
        assert fill["intent_id"] == projected["intent_id"]
        assert fill["intent_fingerprint"] == projected["intent_fingerprint"]
        assert fill["account_ref"] == _ACCOUNT and fill["strategy_ref"] == _STRATEGY
        assert store._connection.total_changes == changes_before_reads
        assert quotes.calls == ["BTC-USDT"]
    finally:
        store.close()


@pytest.mark.unit
def test_recovery_v2_never_promotes_a_legacy_v1_paper_receipt(tmp_path) -> None:
    store, quotes, service, adapter = _build_paper_owner(
        tmp_path / "fill-v1.sqlite3",
        quote_source=_QuoteSource(available=False),
    )
    try:
        result = _submit(service, "fill-v1-rejection")
        assert result["ok"] is True and result["code"] == "OK", result
        event = next(
            item
            for item in store.read_outbox(scope=_EXECUTION_SCOPE, after_sequence=0, limit=32)
            if item.event_type == "provider_observation"
        )
        receipt = store.get_paper_match_receipt("fill-v1-rejection", scope=_EXECUTION_SCOPE)
        assert type(receipt) is PaperMatchReceiptV1
        assert receipt.state is ExecutionState.REJECTED
        assert (
            store.get_managed_paper_fill_v2_for_event(
                event.event_id,
                event_sequence=event.sequence,
                journal_generation=event.journal_incarnation_id,
                scope=_EXECUTION_SCOPE,
            )
            is None
        )
        page = _events_page(adapter, store, version=2)
        assert page["ok"] is True and page["code"] == "OK"
        observation = next(
            row for row in page["result"]["events"] if row["event_id"] == event.event_id
        )
        assert observation["details"]["source"] == "provider"
        assert "paper_fill_v2" not in observation["details"]
        assert quotes.calls == ["BTC-USDT"]
    finally:
        store.close()


@pytest.mark.unit
def test_recovery_v2_leaves_nonpaper_provider_observations_time_unavailable(tmp_path) -> None:
    scope = ExecutionScope("binance", "synthetic", "account-a", "strategy-a", "20261016")
    wire_scope = WireScope(
        "binance:fill-recovery-test", account_ref="account-a", strategy_ref="strategy-a"
    )
    identity = WireIdentity(WireRole.STRATEGY_CLIENT, "fill-recovery-principal", "strategy-a")
    store = SqliteExecutionStore(tmp_path / "fill-live.sqlite3")
    try:
        lease = store.acquire_or_renew_lease(scope, "fill-recovery-live-test")
        intent = OrderIntent(
            intent_id="live-order",
            scope=scope,
            signal_id="live-signal",
            instrument="BTC-USDT",
            side=Side.BUY,
            position_effect=PositionEffect.OPEN,
            order_type=OrderType.LIMIT,
            quantity=Decimal("1"),
            price=Decimal("10"),
        )
        store.admit_intent(intent, writer_lease=lease)
        store.activate_intent(intent.intent_id, scope, writer_lease=lease)
        claimed, did_claim = store.claim_for_dispatch(intent.intent_id, scope, writer_lease=lease)
        assert did_claim is True and claimed.state is ExecutionState.DISPATCHING
        store.record_observation(
            ProviderObservation(
                intent_id=intent.intent_id,
                state=ExecutionState.FILLED,
                provider_order_id="synthetic-live-order",
                filled_quantity=Decimal("1"),
                average_price=Decimal("10"),
                cumulative_commission=Decimal("0.1"),
                cumulative_commission_currency="USDT",
            ),
            scope=scope,
            writer_lease=lease,
        )
        adapter = ExecutionAccountBookReadAdapter(
            store,
            {wire_scope: scope},
            _ReadSource(scope, wire_scope),
        )
        generation = store.journal_source_identity()["generation"]
        page = adapter.handle(
            ACCOUNT_BOOKS_QUERY_OPERATION,
            principal_ref=identity.principal_ref,
            identity=identity,
            scope=wire_scope,
            arguments={
                "view": "EVENTS_AFTER",
                "version": 2,
                "after_sequence": 0,
                "expected_journal_generation": generation,
                "expected_directory_revision": "fill-recovery-directory-v1",
                "limit": 32,
            },
        )
        assert page["ok"] is True and page["code"] == "OK"
        event = next(
            row for row in page["result"]["events"] if row["event_type"] == "provider_observation"
        )
        assert event["details"]["source"] == "provider"
        assert "paper_fill_v2" not in event["details"]
    finally:
        store.close()


class _StubRuntime(ServiceClientRuntime):
    def __init__(self) -> None:
        self.calls = []

    def call(self, mount, operation, arguments, *, scope_ref):
        self.calls.append((mount, operation, dict(arguments), scope_ref))
        return {
            "ok": True,
            "code": "OK",
            "result": {"view": "EVENTS_AFTER", "version": arguments["version"], "events": []},
        }


@pytest.mark.unit
def test_execution_client_requires_explicit_exact_version_for_paper_fill_pages() -> None:
    runtime = _StubRuntime()
    client = ServiceExecutionClient(
        runtime,
        "broker.orders",
        scope_ref="strategy-order-entry",
        query_mount="broker.order_query",
        query_scope_ref="strategy-order-query",
    )
    arguments = {
        "after_sequence": 0,
        "expected_journal_generation": "a" * 32,
        "expected_directory_revision": "directory-v1",
        "limit": 32,
    }
    assert client.query_recovery_events_after(**arguments)["version"] == 1
    assert client.query_recovery_events_after(**arguments, version=2)["version"] == 2
    assert [call[2]["version"] for call in runtime.calls] == [1, 2]
    for version in (True, 0, 3):
        with pytest.raises(ServiceExecutionError, match="INVALID_RECOVERY_VERSION"):
            client.query_recovery_events_after(**arguments, version=version)
    assert [call[2]["version"] for call in runtime.calls] == [1, 2]
