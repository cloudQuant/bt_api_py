"""Source-bound FX valuation at the trusted shared-pool Host boundary.

This extends the accepted DIM03 shared-collateral Host fixture without
modifying its production or test inputs. The provider is a local synthetic
leaf; the normal authenticated ServiceHost, SDK order entry, Risk mapper,
SQLite admission ledger, and RiskOrderJournal remain in the path.
"""

from __future__ import annotations

import hashlib
import json
import sqlite3
import time
from decimal import Decimal
from pathlib import Path
from typing import Any

import pytest
from bt_api_risk import InstrumentRiskAdmissionMapper, InstrumentRiskFxRate, InstrumentRiskMetadata
from bt_api_py.service_risk_order_entry import RiskOrderTarget
from tests.integration import test_dim03_shared_collateral_composition as shared


_FX_SOURCE = "dim03-trusted-eurusd-v1"
_FX_RATE = Decimal("1.5")
_FX_METADATA_VERSION = "dim03-eur-usd-instrument-v1"
_ORDER_SCHEMA = "dim03-fx-order-v2"
_FILL_SCHEMA = "dim03-fx-fill-v2"
_STATUS_MAPPING = (
    ("cancelled", "canceled"),
    ("expired", "expired"),
    ("filled", "completed"),
    ("open", "accepted"),
    ("partial", "partial"),
    ("rejected", "rejected"),
)


class _FxV2SyntheticFeed:
    """Local provider leaf with the same V2 envelope consumed by BtApi."""

    def __init__(self, ledger_path: Path, account_id: str) -> None:
        self._ledger_path = str(ledger_path)
        self.account_id = account_id
        with sqlite3.connect(self._ledger_path) as connection:
            connection.execute(
                "CREATE TABLE provider_writes ("
                "sequence INTEGER PRIMARY KEY AUTOINCREMENT, client_order_id TEXT NOT NULL)"
            )
            connection.execute(
                "CREATE TABLE provider_orders ("
                "client_order_id TEXT PRIMARY KEY, order_json TEXT NOT NULL, fills_json TEXT NOT NULL)"
            )

    @staticmethod
    def _order_envelope(row: dict[str, Any]) -> dict[str, Any]:
        revision = int(row["order_revision"])
        return {
            "account_id": row["account_id"],
            "client_order_id": row["client_order_id"],
            "complete": True,
            "fill_watermark": max(1, revision),
            "schema_version": _ORDER_SCHEMA,
            "watermark": f"order:{row['client_order_id']}:{revision}",
            "data": [dict(row)],
        }

    def make_order(
        self,
        symbol: str,
        volume: float,
        price: float,
        order_type: str,
        *,
        client_order_id: str,
        **_kwargs: Any,
    ) -> dict[str, Any]:
        quantity = format(Decimal(str(volume)), "f")
        order_price = format(Decimal(str(price)), "f")
        row = {
            "account_id": self.account_id,
            "client_order_id": client_order_id,
            "filled": "0",
            "order_id": "provider-" + client_order_id,
            "order_revision": 1,
            "price": order_price,
            "quantity": quantity,
            "side": order_type.split("-", 1)[0].lower(),
            "status": "open",
            "symbol": symbol,
        }
        with sqlite3.connect(self._ledger_path) as connection:
            connection.execute(
                "INSERT INTO provider_writes(client_order_id) VALUES (?)",
                (client_order_id,),
            )
            connection.execute(
                "INSERT INTO provider_orders(client_order_id, order_json, fills_json) "
                "VALUES (?, ?, ?)",
                (
                    client_order_id,
                    json.dumps(row, sort_keys=True, separators=(",", ":")),
                    "[]",
                ),
            )
        return self._order_envelope(row)

    def query_order(
        self, _symbol: str, client_order_id: str, **_kwargs: Any
    ) -> dict[str, Any]:
        with sqlite3.connect(self._ledger_path) as connection:
            found = connection.execute(
                "SELECT order_json FROM provider_orders WHERE client_order_id=?",
                (client_order_id,),
            ).fetchone()
        if found is None:
            return {
                "account_id": self.account_id,
                "client_order_id": client_order_id,
                "complete": True,
                "fill_watermark": 1,
                "schema_version": _ORDER_SCHEMA,
                "watermark": "missing-order",
                "data": [],
            }
        return self._order_envelope(json.loads(found[0]))

    def get_deals(
        self,
        _symbol: str | None,
        *,
        client_order_id: str,
        **_kwargs: Any,
    ) -> dict[str, Any]:
        with sqlite3.connect(self._ledger_path) as connection:
            found = connection.execute(
                "SELECT order_json, fills_json FROM provider_orders WHERE client_order_id=?",
                (client_order_id,),
            ).fetchone()
        order = {} if found is None else json.loads(found[0])
        revision = max(1, int(order.get("order_revision", 1)))
        return {
            "account_id": self.account_id,
            "client_order_id": client_order_id,
            "complete": True,
            "fill_watermark": revision,
            "schema_version": _FILL_SCHEMA,
            "watermark": f"fills:{client_order_id}:{revision}",
            "data": [] if found is None else json.loads(found[1]),
        }

    @property
    def writes(self) -> list[str]:
        with sqlite3.connect(self._ledger_path) as connection:
            return [
                str(row[0])
                for row in connection.execute(
                    "SELECT client_order_id FROM provider_writes ORDER BY sequence"
                )
            ]

    @property
    def orders(self) -> dict[str, dict[str, Any]]:
        with sqlite3.connect(self._ledger_path) as connection:
            rows = connection.execute(
                "SELECT client_order_id, order_json FROM provider_orders"
            ).fetchall()
        return {str(key): json.loads(value) for key, value in rows}


def _fx_inputs(
    *, stale: bool, missing: bool
) -> tuple[InstrumentRiskMetadata, InstrumentRiskFxRate | None]:
    now_ns = time.time_ns()
    metadata = InstrumentRiskMetadata(
        instrument=shared._INSTRUMENT,
        metadata_version=_FX_METADATA_VERSION,
        as_of_ns=now_ns - 1_000_000_000,
        expires_at_ns=now_ns + 3_600_000_000_000,
        tick_size=Decimal("0.01"),
        quantity_step=Decimal("0.001"),
        contract_multiplier=Decimal("1"),
        max_gross_notional=Decimal("1000"),
        quantity_unit="BTC",
        valuation_unit="USD",
        quote_currency="EUR",
        fx_source_ref=_FX_SOURCE,
    )
    if missing:
        return metadata, None

    observed_at_ns = now_ns - 2_000_000_000
    valid_until_ns = now_ns - 1_000_000_000 if stale else now_ns + 3_600_000_000_000
    fact_payload = {
        "instrument": shared._INSTRUMENT,
        "observed_at_ns": observed_at_ns,
        "quote_currency": "EUR",
        "rate": format(_FX_RATE, "f"),
        "source_ref": _FX_SOURCE,
        "source_revision": "dim03-fx-r1",
        "valid_until_ns": valid_until_ns,
        "valuation_unit": "USD",
    }
    source_record_sha256 = hashlib.sha256(
        shared.canonical_json(fact_payload).encode("utf-8")
    ).hexdigest()
    return metadata, InstrumentRiskFxRate(
        instrument=shared._INSTRUMENT,
        quote_currency="EUR",
        valuation_unit="USD",
        rate=_FX_RATE,
        observed_at_ns=observed_at_ns,
        valid_until_ns=valid_until_ns,
        source_ref=_FX_SOURCE,
        source_revision="dim03-fx-r1",
        source_record_sha256=source_record_sha256,
    )


def _install_fx_fixture(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    *,
    stale: bool,
    missing: bool,
) -> tuple[InstrumentRiskMetadata, InstrumentRiskFxRate | None]:
    monkeypatch.setattr(shared, "_INSTRUMENT", "BTC-EUR")
    metadata, fx_rate = _fx_inputs(stale=stale, missing=missing)
    monkeypatch.setattr(shared, "_metadata", lambda: metadata)

    def provider_contract(exchange_name: str):
        return shared.RiskBtApiProviderContract(
            exchange_name=exchange_name,
            profile_id="dim03-fx-provider-v2",
            order_schema=_ORDER_SCHEMA,
            fill_schema=_FILL_SCHEMA,
            client_order_id_query=True,
            client_order_id_query_unique=True,
            complete_order_snapshot=True,
            complete_fill_history=True,
            provider_accepts_client_order_id=True,
            provider_status_mapping=_STATUS_MAPPING,
            supported_order_types=("LIMIT",),
            supported_time_in_force=("GTC",),
            supported_position_effects=("OPEN",),
            normalizer_schema="bt-api-order-source-provenance-v2",
            order_revision_field="order_revision",
            fill_revision_field="fill_revision",
            fill_watermark_field="fill_watermark",
            fee_amount_field="fee_amount",
            fee_currency_field="fee_currency",
            quote_currency_by_instrument=((shared._INSTRUMENT, "EUR"),),
        )

    monkeypatch.setattr(shared, "_provider_contract", provider_contract)

    target_init = RiskOrderTarget.__init__
    fx_rates = () if fx_rate is None else (fx_rate,)

    def target_with_fx(self, *args, **kwargs):
        kwargs["fx_rates_by_instrument"] = fx_rates
        return target_init(self, *args, **kwargs)

    monkeypatch.setattr(RiskOrderTarget, "__init__", target_with_fx)

    mapper_init = InstrumentRiskAdmissionMapper.__init__

    def mapper_with_fx(self, *args, **kwargs):
        kwargs["fx_rates_by_instrument"] = fx_rates
        return mapper_init(self, *args, **kwargs)

    monkeypatch.setattr(InstrumentRiskAdmissionMapper, "__init__", mapper_with_fx)

    def v2_feed(account_id: str) -> _FxV2SyntheticFeed:
        digest = hashlib.sha256(account_id.encode("utf-8")).hexdigest()
        path = tmp_path / ("provider-" + digest + ".sqlite3")
        return _FxV2SyntheticFeed(path, account_id)

    monkeypatch.setattr(shared, "_SyntheticFeed", v2_feed)
    return metadata, fx_rate


def _assert_persisted_fx_reservation(stack, *, intent_id: str) -> None:
    command = stack.journal.get_command(
        shared._ACCOUNT_SCOPE_A, shared._PRINCIPAL, intent_id
    )
    assert command is not None
    with stack.gate._connection() as connection:
        row = connection.execute(
            "SELECT notional, notional_unit, resource_product_type, resource_margin_pool "
            "FROM risk_reservations WHERE intent_id=?",
            (command.risk_intent_id,),
        ).fetchone()
    assert row is not None
    assert Decimal(row[0]) == Decimal("30")
    assert row[1:] == ("USD", "spot", "unified-usd")


def _assert_persisted_fx_source(
    stack,
    *,
    account_scope,
    intent_id: str,
    metadata: InstrumentRiskMetadata,
    fx_rate: InstrumentRiskFxRate,
) -> None:
    command = stack.journal.get_command(account_scope, shared._PRINCIPAL, intent_id)
    assert command is not None
    order_events = [
        event
        for revision in range(1, command.latest_revision + 1)
        if (
            (event := stack.journal.get_event(
                account_scope, shared._PRINCIPAL, intent_id, revision
            ))
            is not None
            and event.event_type == "ORDER_OBSERVATION"
        )
    ]
    assert len(order_events) == 1
    order_event = order_events[0]
    assert order_event.event_type == "ORDER_OBSERVATION"
    facts = order_event.payload["metadata_facts"]
    assert facts["instrument_metadata"]["metadata_sha256"] == metadata.digest
    assert facts["instrument_fx_rate"] == fx_rate.to_payload()
    assert facts["instrument_fx_rate_sha256"] == fx_rate.digest
    assert facts["provider_contract"]["quote_currency_by_instrument"] == {
        shared._INSTRUMENT: "EUR"
    }


def _submit_fx(client, intent_id: str):
    """Use the exact metadata revision installed by the FX fixture."""

    return client.submit(
        intent_id=intent_id,
        signal_id="signal-" + intent_id,
        instrument=shared._INSTRUMENT,
        side="BUY",
        position_effect="OPEN",
        order_type="LIMIT",
        quantity="1",
        price="20",
        metadata_version=_FX_METADATA_VERSION,
    )


def _serving_host_with_failure_cleanup(
    stack, monkeypatch: pytest.MonkeyPatch
) -> tuple[shared._ServingHost, list[str]]:
    """Close the real Host in its owner thread if serving raises."""

    host = stack.host
    original_serve_once = host.serve_once
    cleanup_failures: list[str] = []

    def serve_once_with_cleanup(timeout_ms: int = 0) -> bool:
        try:
            return original_serve_once(timeout_ms=timeout_ms)
        except BaseException:
            if host.state == "started":
                try:
                    host.close()
                except BaseException as cleanup_error:
                    cleanup_failures.append(type(cleanup_error).__name__)
            raise

    monkeypatch.setattr(host, "serve_once", serve_once_with_cleanup)
    return shared._ServingHost(host), cleanup_failures


def _assert_serving_host_cleanup(stack, cleanup_failures: list[str]) -> None:
    assert cleanup_failures == []
    assert stack.host.state in {"closed", "failed"}
    assert stack.host._server is None
    assert stack.host._registry is None


def test_fx_shared_pool_uses_source_bound_rate_before_dispatch(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    with monkeypatch.context() as fx_patch:
        metadata, fx_rate = _install_fx_fixture(
            fx_patch, tmp_path, stale=False, missing=False
        )
        assert fx_rate is not None
        stack, keys = shared._stack(tmp_path, fx_patch, shared_domain=True)
        serving, cleanup_failures = _serving_host_with_failure_cleanup(stack, fx_patch)
        runtime = None
        try:
            serving.start()
            runtime = shared._client_runtime(stack.host.endpoint, keys)  # type: ignore[arg-type]
            assert runtime.start().degraded_count == 0
            client_a, client_b = shared._execution_clients(runtime)

            first = _submit_fx(client_a, "dim03-fx-shared-first")
            assert first.state == "ACKED_TRACKED"
            shared._assert_persisted_provider_binding(
                stack,
                account_scope=shared._ACCOUNT_SCOPE_A,
                feed=stack.feeds[shared._WIRE_SCOPE_A],
                intent_id="dim03-fx-shared-first",
                exchange_name="FIXTURE___SPOT",
            )
            _assert_persisted_fx_source(
                stack,
                account_scope=shared._ACCOUNT_SCOPE_A,
                intent_id="dim03-fx-shared-first",
                metadata=metadata,
                fx_rate=fx_rate,
            )
            _assert_persisted_fx_reservation(stack, intent_id="dim03-fx-shared-first")

            with pytest.raises(shared.ServiceExecutionError) as shared_limit:
                _submit_fx(client_b, "dim03-fx-shared-over-cap")
            assert shared_limit.value.code == "ADMISSION_DENIED"
            assert stack.feeds[shared._WIRE_SCOPE_B].writes == []
            assert (
                stack.gate.snapshot(shared._ACCOUNT_SCOPE_A)["increase_notional"]
                == Decimal("30")
            )
            assert stack.gate.snapshot(shared._ACCOUNT_SCOPE_B)["increase_notional"] == Decimal("0")
        finally:
            try:
                if runtime is not None:
                    runtime.close()
            finally:
                try:
                    serving.close()
                finally:
                    try:
                        _assert_serving_host_cleanup(stack, cleanup_failures)
                    finally:
                        stack.close_resources()


def test_stale_fx_denies_without_provider_dispatch(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    with monkeypatch.context() as fx_patch:
        _metadata, _fx_rate = _install_fx_fixture(
            fx_patch, tmp_path, stale=True, missing=False
        )
        stack, keys = shared._stack(tmp_path, fx_patch, shared_domain=True)
        serving, cleanup_failures = _serving_host_with_failure_cleanup(stack, fx_patch)
        runtime = None
        try:
            serving.start()
            runtime = shared._client_runtime(stack.host.endpoint, keys)  # type: ignore[arg-type]
            assert runtime.start().degraded_count == 0
            client_a, _client_b = shared._execution_clients(runtime)

            with pytest.raises(shared.ServiceExecutionError) as stale_denial:
                _submit_fx(client_a, "dim03-fx-stale")
            assert stale_denial.value.code == "ADMISSION_DENIED"
            command = stack.journal.get_command(
                shared._ACCOUNT_SCOPE_A, shared._PRINCIPAL, "dim03-fx-stale"
            )
            assert command is not None
            assert command.state == "ADMISSION_DENIED"
            assert command.latest_event["reason_code"] == "INSTRUMENT_FX_STALE"
            assert all(feed.writes == [] for feed in stack.feeds.values())
            assert stack.gate.snapshot(shared._ACCOUNT_SCOPE_A)["increase_count"] == 0
            assert stack.gate.snapshot(shared._ACCOUNT_SCOPE_A)["increase_notional"] == Decimal("0")
        finally:
            try:
                if runtime is not None:
                    runtime.close()
            finally:
                try:
                    serving.close()
                finally:
                    try:
                        _assert_serving_host_cleanup(stack, cleanup_failures)
                    finally:
                        stack.close_resources()


def test_missing_fx_fails_host_start_before_registry(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    with monkeypatch.context() as fx_patch:
        _metadata, _fx_rate = _install_fx_fixture(
            fx_patch, tmp_path, stale=False, missing=True
        )
        stack, _keys = shared._stack(tmp_path, fx_patch, shared_domain=True)
        try:
            with pytest.raises(shared.ServiceHostError):
                stack.host.start()
            assert stack.host.state == "failed"
            assert stack.host.endpoint is None
            assert stack.host._registry is None
            assert not stack.registry_path.exists()
            assert all(feed.writes == [] for feed in stack.feeds.values())
            assert stack.gate.snapshot(shared._ACCOUNT_SCOPE_A)["increase_count"] == 0
            assert stack.gate.snapshot(shared._ACCOUNT_SCOPE_B)["increase_count"] == 0
        finally:
            stack.close_resources()
