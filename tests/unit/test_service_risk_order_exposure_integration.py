"""Normal ServiceHost evidence for risk order lifecycle and provider contracts."""

from __future__ import annotations

import json
import multiprocessing
import sqlite3
import time
from collections.abc import Mapping
from contextlib import suppress
from dataclasses import asdict
from decimal import Decimal
from types import MappingProxyType
from typing import Any

import pytest
import zmq
from bt_api_base.service_protocol import WireScope
from bt_api_risk import (
    AccountScope,
    DurableRiskAdmissionAdapter,
    DurableRiskGate,
    ExposureAccountingMode,
    InstrumentRiskAdmissionMapper,
    InstrumentRiskMetadata,
    InstrumentRiskRegistry,
    RiskPolicy,
)

from bt_api_py.bt_api import BtApi
from bt_api_py.service_components import ComponentResources
from bt_api_py.service_config import parse_service_config
from bt_api_py.service_execution import ServiceExecutionClient, ServiceExecutionError
from bt_api_py.service_host import ServiceHost
from bt_api_py.service_risk_order_entry import (
    RiskBtApiProviderContract,
    RiskOrderEntryError,
    RiskOrderEntryService,
    RiskOrderTarget,
)
from bt_api_py.service_risk_order_journal import (
    RiskOrderJournal,
    _post_ack_unknown_payload_valid,
    _valid_exposure_applied_payload,
)
from bt_api_py.service_runtime import (
    ClientCurveKeys,
    ServiceClientRuntime,
    ZmqTransportFactory,
)
from tests.unit import test_service_risk_only_order_integration as base

_EXCHANGE = "FIXTURE___SPOT"
_SCOPE = WireScope(
    "paper:risk-contract-test", account_ref="provider-account-a", strategy_ref="strategy-a"
)
_ACCOUNT_SCOPE = AccountScope("fixture", "provider-account-a", "simulation")
_STATUS_MAPPING = (
    ("cancelled", "canceled"),
    ("expired", "expired"),
    ("filled", "completed"),
    ("open", "accepted"),
    ("partial", "partial"),
    ("rejected", "rejected"),
)

_V2_INSTRUMENT = "BTC-USD"
_V2_METADATA_VERSION = "risk-order-exposure-metadata-v2"
_V2_ORDER_SCHEMA = "fixture-order-v2"
_V2_FILL_SCHEMA = "fixture-fill-v2"


class _V2SyntheticBtApiFeed:
    """Synthetic leaves behind the real DirectBackend and BtApi normalizer."""

    def __init__(self, ledger_path: str, initial_states: tuple[str, ...]) -> None:
        self._ledger_path = ledger_path
        self._initial_states = initial_states
        with sqlite3.connect(self._ledger_path) as connection:
            connection.execute(
                "CREATE TABLE IF NOT EXISTS provider_writes ("
                "sequence INTEGER PRIMARY KEY AUTOINCREMENT, client_order_id TEXT NOT NULL)"
            )
            connection.execute(
                "CREATE TABLE IF NOT EXISTS provider_orders ("
                "client_order_id TEXT PRIMARY KEY, order_json TEXT NOT NULL, fills_json TEXT NOT NULL)"
            )
            connection.execute(
                "CREATE TABLE IF NOT EXISTS provider_counters ("
                "name TEXT PRIMARY KEY, count INTEGER NOT NULL)"
            )
            connection.execute("CREATE TABLE IF NOT EXISTS provider_faults (name TEXT PRIMARY KEY)")
            for name in ("order_queries", "fill_queries"):
                connection.execute(
                    "INSERT OR IGNORE INTO provider_counters(name, count) VALUES (?, 0)",
                    (name,),
                )

    @staticmethod
    def _fill(
        *,
        account_id: str,
        client_order_id: str,
        order_id: str,
        symbol: str,
        side: str,
        trade_id: str,
        quantity: str,
        price: str,
        revision: int,
        fee: str,
    ) -> dict[str, Any]:
        return {
            "account_id": account_id,
            "client_order_id": client_order_id,
            "fee_amount": fee,
            "fee_currency": "USD",
            "fill_revision": revision,
            "order_id": order_id,
            "quantity": quantity,
            "side": side,
            "symbol": symbol,
            "trade_id": trade_id,
            "trade_price": price,
        }

    @staticmethod
    def _order_envelope(row: dict[str, Any], *, complete: bool = True) -> dict[str, Any]:
        return {
            "account_id": row["account_id"],
            "client_order_id": row["client_order_id"],
            "complete": complete,
            "fill_watermark": max(1, int(row["order_revision"])),
            "schema_version": _V2_ORDER_SCHEMA,
            "watermark": f"order:{row['client_order_id']}:{row['order_revision']}",
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
        side = order_type.split("-", 1)[0].lower()
        order_id = "provider-" + client_order_id
        with sqlite3.connect(self._ledger_path) as connection:
            sequence = int(connection.execute("SELECT COUNT(*) FROM provider_writes").fetchone()[0])
            state = (
                self._initial_states[sequence] if sequence < len(self._initial_states) else "open"
            )
            filled = quantity if state == "filled" else "0"
            row: dict[str, Any] = {
                "account_id": base._PROVIDER_ACCOUNT,
                "client_order_id": client_order_id,
                "filled": filled,
                "order_id": order_id,
                "order_revision": 1,
                "price": order_price,
                "quantity": quantity,
                "side": side,
                "status": state,
                "symbol": symbol,
            }
            fills = []
            if state == "filled":
                row["avg_price"] = order_price
                fills.append(
                    self._fill(
                        account_id=base._PROVIDER_ACCOUNT,
                        client_order_id=client_order_id,
                        order_id=order_id,
                        symbol=symbol,
                        side=side,
                        trade_id="fill-" + client_order_id + "-1",
                        quantity=quantity,
                        price=order_price,
                        revision=1,
                        fee="-0.05",
                    )
                )
            connection.execute(
                "INSERT INTO provider_writes(client_order_id) VALUES (?)", (client_order_id,)
            )
            connection.execute(
                "INSERT INTO provider_orders(client_order_id, order_json, fills_json) "
                "VALUES (?, ?, ?)",
                (
                    client_order_id,
                    json.dumps(row, sort_keys=True, separators=(",", ":")),
                    json.dumps(fills, sort_keys=True, separators=(",", ":")),
                ),
            )
        return self._order_envelope(row)

    def query_order(self, _symbol: str, client_order_id: str, **_kwargs: Any) -> dict[str, Any]:
        with sqlite3.connect(self._ledger_path) as connection:
            connection.execute(
                "UPDATE provider_counters SET count=count+1 WHERE name='order_queries'"
            )
            found = connection.execute(
                "SELECT order_json FROM provider_orders WHERE client_order_id=?",
                (client_order_id,),
            ).fetchone()
        if found is None:
            return {
                "account_id": base._PROVIDER_ACCOUNT,
                "client_order_id": client_order_id,
                "complete": True,
                "fill_watermark": 1,
                "schema_version": _V2_ORDER_SCHEMA,
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
            connection.execute(
                "UPDATE provider_counters SET count=count+1 WHERE name='fill_queries'"
            )
            found = connection.execute(
                "SELECT order_json, fills_json FROM provider_orders WHERE client_order_id=?",
                (client_order_id,),
            ).fetchone()
            fault = connection.execute(
                "SELECT name FROM provider_faults WHERE name='incomplete_next_fills'"
            ).fetchone()
            if fault is not None:
                connection.execute("DELETE FROM provider_faults WHERE name='incomplete_next_fills'")
        row = (
            json.loads(found[0])
            if found is not None
            else {"account_id": base._PROVIDER_ACCOUNT, "order_revision": 1}
        )
        fills = [] if found is None else json.loads(found[1])
        watermark = max(1, int(row["order_revision"]))
        return {
            "account_id": base._PROVIDER_ACCOUNT,
            "client_order_id": client_order_id,
            "complete": fault is None,
            "fill_watermark": watermark,
            "schema_version": _V2_FILL_SCHEMA,
            "watermark": f"fills:{client_order_id}:{watermark}",
            "data": fills,
        }

    def snapshot(self) -> tuple[tuple[str, ...], dict[str, int]]:
        with sqlite3.connect(self._ledger_path) as connection:
            writes = tuple(
                str(row[0])
                for row in connection.execute(
                    "SELECT client_order_id FROM provider_writes ORDER BY sequence"
                )
            )
            counters = {
                str(row[0]): int(row[1])
                for row in connection.execute(
                    "SELECT name, count FROM provider_counters ORDER BY name"
                )
            }
        return writes, counters


def _store_provider_observation(
    ledger_path: str,
    client_order_id: str,
    *,
    status: str,
    filled: str,
    order_revision: int,
    fills: list[dict[str, Any]],
) -> None:
    with sqlite3.connect(ledger_path) as connection:
        found = connection.execute(
            "SELECT order_json FROM provider_orders WHERE client_order_id=?",
            (client_order_id,),
        ).fetchone()
        assert found is not None
        row = json.loads(found[0])
        row.update(
            {
                "avg_price": "20" if Decimal(filled) > 0 else None,
                "filled": filled,
                "order_revision": order_revision,
                "status": status,
            }
        )
        row = {key: value for key, value in row.items() if value is not None}
        connection.execute(
            "UPDATE provider_orders SET order_json=?, fills_json=? WHERE client_order_id=?",
            (
                json.dumps(row, sort_keys=True, separators=(",", ":")),
                json.dumps(fills, sort_keys=True, separators=(",", ":")),
                client_order_id,
            ),
        )


def _arm_incomplete_fills(ledger_path: str) -> None:
    with sqlite3.connect(ledger_path) as connection:
        connection.execute(
            "INSERT OR IGNORE INTO provider_faults(name) VALUES ('incomplete_next_fills')"
        )


def _host_process(
    control,
    raw_config,
    registry_path: str,
    keys: dict[str, bytes],
    gate_path: str,
    journal_path: str,
    provider_path: str,
    initial_states: tuple[str, ...],
) -> None:
    config = parse_service_config(raw_config)
    provider = _V2SyntheticBtApiFeed(provider_path, initial_states)
    bt_api = None
    gate = None
    host = None
    try:
        now_ns = time.time_ns()
        metadata = InstrumentRiskMetadata(
            instrument=_V2_INSTRUMENT,
            metadata_version=_V2_METADATA_VERSION,
            as_of_ns=now_ns - 1_000_000_000,
            expires_at_ns=now_ns + 3_600_000_000_000,
            tick_size=Decimal("0.01"),
            quantity_step=Decimal("0.001"),
            contract_multiplier=Decimal("1"),
            max_gross_notional=Decimal("1000"),
            taker_fee_bps=Decimal("0"),
            fixed_fee=Decimal("0"),
            max_slippage_bps=Decimal("0"),
            quantity_unit="BTC",
            valuation_unit="USD",
        )
        registry = InstrumentRiskRegistry((metadata,))
        target = RiskOrderTarget(
            account_scope=base._ACCOUNT_SCOPE,
            strategy_id=base._STRATEGY,
            exchange_name=base._EXCHANGE,
            provider_account_id=base._PROVIDER_ACCOUNT,
            registry=registry,
        )
        bt_api = BtApi(debug=False)
        bt_api.exchange_feeds[base._EXCHANGE] = provider
        journal = RiskOrderJournal(journal_path, writer_id="risk-order-exposure-owner")
        gate = DurableRiskGate(
            gate_path,
            RiskPolicy(
                "service-risk-policy-v1",
                Decimal("1000"),
                max_increase_count=10,
                require_strategy_allocation=True,
                notional_unit="USD",
                exposure_accounting_mode=ExposureAccountingMode.LIVE_GROSS,
                max_gross_occupancy_notional=Decimal("45"),
                max_gross_occupancy_intent_count=10,
                max_exposure_facts_age_seconds=3600,
            ),
            execution_journal_authority=journal,
            gross_exposure_authority=journal,
        )
        gate.set_strategy_allocation(
            base._ACCOUNT_SCOPE,
            base._STRATEGY,
            "fixture-live-gross-allocation-v1",
            max_notional=Decimal("1000"),
            notional_unit="USD",
            max_gross_occupancy_notional=Decimal("45"),
            max_gross_occupancy_intent_count=10,
        )
        baseline_time = time.time_ns()
        baseline_snapshot = {
            "account_revision": 1,
            "account_scope": {
                "account_id": base._ACCOUNT_SCOPE.account_id,
                "environment": base._ACCOUNT_SCOPE.environment,
                "provider": base._ACCOUNT_SCOPE.provider,
            },
            "complete_open_orders": True,
            "complete_positions": True,
            "notional_unit": "USD",
            "observed_at_ns": baseline_time,
            "open_orders": [],
            "positions": [
                {
                    "active_increase_intent_count": 0,
                    "gross_notional": "0",
                    "notional_unit": "USD",
                    "source_record_sha256": "1" * 64,
                    "strategy_id": base._STRATEGY,
                    "valuation_facts_sha256": "2" * 64,
                }
            ],
            "source_schema_version": "fixture-account-snapshot-v1",
            "valid_until_ns": baseline_time + 3_600_000_000_000,
            "valuation_facts": [],
        }
        with journal.writer_fence(base._ACCOUNT_SCOPE) as fence:
            baseline = journal.ingest_gross_exposure_baseline(
                fence,
                source_snapshot=baseline_snapshot,
            )
            gate.install_gross_exposure_baseline(baseline)
        mapper = InstrumentRiskAdmissionMapper(
            base._ACCOUNT_SCOPE,
            registry,
            clock_ns=time.time_ns,
            allocation_reader=lambda execution_scope: gate.get_strategy_allocation(base._ACCOUNT_SCOPE, execution_scope.strategy_id),
        )
        admission = DurableRiskAdmissionAdapter(gate, mapper)
        contract = RiskBtApiProviderContract(
            exchange_name=base._EXCHANGE,
            profile_id="fixture-risk-order-v2-profile",
            order_schema=_V2_ORDER_SCHEMA,
            fill_schema=_V2_FILL_SCHEMA,
            client_order_id_query=True,
            client_order_id_query_unique=True,
            complete_order_snapshot=True,
            complete_fill_history=True,
            provider_accepts_client_order_id=True,
            provider_status_mapping=(
                ("cancelled", "canceled"),
                ("expired", "expired"),
                ("filled", "completed"),
                ("open", "accepted"),
                ("partial", "partial"),
                ("rejected", "rejected"),
            ),
            supported_order_types=("LIMIT",),
            supported_time_in_force=("GTC",),
            supported_position_effects=("OPEN",),
            normalizer_schema="bt-api-order-source-provenance-v2",
            order_revision_field="order_revision",
            fill_revision_field="fill_revision",
            fill_watermark_field="fill_watermark",
            fee_amount_field="fee_amount",
            fee_currency_field="fee_currency",
            quote_currency_by_instrument=((_V2_INSTRUMENT, "USD"),),
        )

        def resource_provider(component, _instance_id, _options, _scope):
            if component != "risk":
                raise AssertionError("unexpected component")
            return ComponentResources.borrowed(
                {
                    "bt_api": bt_api,
                    "targets": {base._SCOPE: target},
                    "admission": {base._SCOPE: admission},
                    "journal": journal,
                    "gate": gate,
                    "provider_contracts": {base._SCOPE: contract},
                },
                ready=lambda: True,
            )

        host = ServiceHost(config, base._authority(config, keys), registry_path, resource_provider)
        host.start()
        control.send(("ready", host.endpoint, host.state))
        while True:
            host.serve_once(timeout_ms=25)
            if not control.poll():
                continue
            command = control.recv()
            if command == ("close",):
                host.close()
                gate.close()
                bt_api.close()
                control.send(("closed", host.state))
                return
            if command == ("snapshot",):
                writes, counters = provider.snapshot()
                risk = gate.gross_exposure_snapshot(base._ACCOUNT_SCOPE)
                order_rows = tuple(
                    {
                        "intent_id": item.intent_id,
                        "state": item.state.value,
                        "order_revision": item.order_revision,
                        "journal_revision": item.journal_revision,
                        "requested_quantity": str(item.requested_quantity),
                        "cumulative_filled_quantity": str(item.cumulative_filled_quantity),
                        "leaves_quantity": str(item.leaves_quantity),
                        "filled_gross_notional": str(item.filled_gross_notional),
                        "open_gross_notional": str(item.open_gross_notional),
                        "proof_sha256": item.proof_sha256,
                    }
                    for item in risk["orders"]
                )
                control.send(
                    (
                        "snapshot",
                        writes,
                        counters,
                        str(risk["account_baseline_notional"]),
                        risk["baseline_revision"],
                        order_rows,
                        tuple(risk["active_freeze_reasons"]),
                    )
                )
                continue
            if command[0] == "command":
                item = journal.get_command(base._ACCOUNT_SCOPE, base._PRINCIPAL, command[1])
                control.send(
                    (
                        "command",
                        None if item is None else item.state,
                        None if item is None else item.permit_id,
                        None if item is None else item.client_order_id,
                        None if item is None else item.latest_revision,
                        None if item is None else item.risk_intent_id,
                    )
                )
                continue
            if command[0] == "exposure_receipt":
                item = journal.get_command(base._ACCOUNT_SCOPE, base._PRINCIPAL, command[1])
                if item is None or item.permit_id is None:
                    control.send(("exposure_receipt", None))
                    continue
                receipt = gate.dispatch_exposure_resolution_receipt(item.permit_id)
                control.send(
                    (
                        "exposure_receipt",
                        type(receipt).__name__,
                        receipt.status.value,
                        hasattr(receipt, "proof"),
                        receipt.claim.intent_id,
                        receipt.claim.permit_id,
                        receipt.proof_sha256,
                        receipt.exposure_proof_sha256,
                        receipt.final_state.value,
                        str(receipt.requested_quantity),
                        str(receipt.filled_quantity),
                        receipt.trade_count,
                        receipt.order_revision,
                        receipt.journal_revision,
                        receipt.exposure_reservation_id,
                        receipt.exposure_reservation_sha256,
                        receipt.dispatch_latch_active,
                    )
                )
                continue
            if command[0] == "events":
                item = journal.get_command(base._ACCOUNT_SCOPE, base._PRINCIPAL, command[1])
                events = []
                if item is not None:
                    for revision in range(item.created_revision, item.latest_revision + 1):
                        event = journal.get_event(
                            base._ACCOUNT_SCOPE,
                            base._PRINCIPAL,
                            command[1],
                            revision,
                        )
                        if event is not None:
                            events.append(
                                (
                                    event.event_type,
                                    event.revision,
                                    _plain(event.payload),
                                )
                            )
                control.send(("events", tuple(events)))
                continue
            raise AssertionError("unknown exposure host control")
    except BaseException as exc:
        if host is not None and host.state not in {"closed", "failed", "close_failed"}:
            with suppress(BaseException):
                host.close()
        if gate is not None:
            with suppress(BaseException):
                gate.close()
        if bt_api is not None:
            with suppress(BaseException):
                bt_api.close()
        with suppress(Exception):
            control.send(("error", getattr(exc, "code", type(exc).__name__)))
        raise
    finally:
        control.close()


def _exposure_strategy_process(
    control,
    endpoint: str,
    strategy_public: bytes,
    strategy_secret: bytes,
    server_public: bytes,
) -> None:
    runtime = None
    try:
        config = base._client_config(endpoint, "risk-order-strategy-curve-v1")
        from bt_api_base.service_protocol import WireIdentity, WireRole

        identity = WireIdentity(WireRole.STRATEGY_CLIENT, base._PRINCIPAL, base._STRATEGY)
        peer = WireIdentity(WireRole.SERVICE, base._SERVICE_PRINCIPAL, base._SERVICE_ID)
        runtime = ServiceClientRuntime(
            config,
            identity=identity,
            scope_resolver=lambda _ref: base._SCOPE,
            peer_resolver=lambda _service: peer,
            transport_factory=ZmqTransportFactory(
                {
                    "risk-order-strategy-curve-v1": ClientCurveKeys(
                        server_public, strategy_public, strategy_secret
                    )
                }
            ),
        )
        startup = runtime.start()
        assert startup.degraded_count == 0
        client = ServiceExecutionClient(
            runtime,
            "broker.orders",
            scope_ref=base._ORDER_SCOPE_REF,
            query_mount="broker.order_query",
            query_scope_ref=base._QUERY_SCOPE_REF,
        )
        control.send(("ready", runtime.run_id))
        while True:
            command = control.recv()
            if command == ("close",):
                runtime.close()
                control.send(("closed",))
                return
            if command[0] == "submit":
                try:
                    view = client.submit(
                        intent_id=command[1],
                        signal_id="signal-" + command[1],
                        instrument=_V2_INSTRUMENT,
                        side="BUY",
                        position_effect="OPEN",
                        order_type="LIMIT",
                        quantity=command[2],
                        price=command[3],
                        time_in_force="GTC",
                        reduce_only=False,
                        metadata_version=_V2_METADATA_VERSION,
                        deadline_ns=None,
                        tags={},
                    )
                except ServiceExecutionError as error:
                    record = error.record
                    control.send(
                        (
                            "submit_error",
                            error.code,
                            getattr(record, "state", None),
                            getattr(record, "permit_reference", None),
                        )
                    )
                else:
                    control.send(("submitted", _plain(asdict(view))))
                continue
            if command[0] == "query":
                try:
                    view = client.query(command[1])
                except ServiceExecutionError as error:
                    control.send(("query_error", error.code))
                else:
                    control.send(("queried", None if view is None else _plain(asdict(view))))
                continue
            raise AssertionError("unknown exposure client control")
    except BaseException as exc:
        if runtime is not None:
            with suppress(BaseException):
                runtime.close()
        with suppress(Exception):
            control.send(("error", getattr(exc, "code", type(exc).__name__)))
        raise
    finally:
        control.close()


def _plain(value: Any) -> Any:
    if isinstance(value, dict):
        return {str(key): _plain(item) for key, item in value.items()}
    if hasattr(value, "items") and callable(value.items):
        return {str(key): _plain(item) for key, item in value.items()}
    if isinstance(value, (tuple, list)):
        return [_plain(item) for item in value]
    if isinstance(value, Decimal):
        return str(value)
    if hasattr(value, "value") and type(value.value) is str:
        return value.value
    if value is None or type(value) in (str, bool, int, float):
        return value
    return str(value)


def _risk_order_rows(snapshot: tuple[Any, ...]) -> dict[str, dict[str, Any]]:
    assert snapshot[0] == "snapshot", snapshot
    return {row["intent_id"]: row for row in snapshot[5]}


def test_exposure_applied_timestamp_is_journal_owned() -> None:
    payload = {
        "dispatch_attempt_count": 1,
        "filled_gross_notional": "0",
        "filled_quantity": "0",
        "journal_record_sha256": "a" * 64,
        "journal_revision": 1,
        "leaves_quantity": "1",
        "notional_unit": "USD",
        "open_gross_notional": "20",
        "order_revision": 1,
        "permit_id": "permit-1",
        "proof_sha256": "b" * 64,
        "provider_order_id": "provider-order-1",
        "public_state": "ACKED",
        "risk_intent_id": "risk-order-1",
        "trade_count": 0,
    }

    assert _valid_exposure_applied_payload(payload)
    persisted = {**payload, "updated_at_ns": 1}
    assert not _valid_exposure_applied_payload(persisted)
    assert _valid_exposure_applied_payload(persisted, persisted=True)
    assert not _valid_exposure_applied_payload(payload, persisted=True)

    for timestamp in (True, 0, -1, "1", 1.0, None):
        assert not _valid_exposure_applied_payload(
            {**payload, "updated_at_ns": timestamp}, persisted=True
        )
    assert not _valid_exposure_applied_payload({**persisted, "unexpected": "field"}, persisted=True)


def test_in_process_live_gross_ack_partial_unknown_cancel_lifecycle(tmp_path) -> None:
    """Run the normal order-entry handler with real SQLite Risk and normalized provider leaves."""

    from types import MappingProxyType

    from bt_api_base.service_protocol import WireIdentity, WireRole

    now_ns = time.time_ns()
    metadata = InstrumentRiskMetadata(
        instrument=_V2_INSTRUMENT,
        metadata_version=_V2_METADATA_VERSION,
        as_of_ns=now_ns - 1_000_000_000,
        expires_at_ns=now_ns + 3_600_000_000_000,
        tick_size=Decimal("0.01"),
        quantity_step=Decimal("0.001"),
        contract_multiplier=Decimal("1"),
        max_gross_notional=Decimal("1000"),
        taker_fee_bps=Decimal("0"),
        fixed_fee=Decimal("0"),
        max_slippage_bps=Decimal("0"),
        quantity_unit="BTC",
        valuation_unit="USD",
    )
    registry = InstrumentRiskRegistry((metadata,))
    target = RiskOrderTarget(
        account_scope=_ACCOUNT_SCOPE,
        strategy_id=_SCOPE.strategy_ref,
        exchange_name=_EXCHANGE,
        provider_account_id=_ACCOUNT_SCOPE.account_id,
        registry=registry,
    )
    provider_path = str(tmp_path / "provider.sqlite3")
    provider = _V2SyntheticBtApiFeed(provider_path, ("filled", "open", "open"))
    bt_api = BtApi(debug=False)
    bt_api.exchange_feeds[_EXCHANGE] = provider
    journal = RiskOrderJournal(
        tmp_path / "journal.sqlite3", writer_id="risk-order-in-process-owner"
    )
    gate = DurableRiskGate(
        tmp_path / "risk.sqlite3",
        RiskPolicy(
            "in-process-live-gross-policy-v1",
            Decimal("1000"),
            max_increase_count=10,
            require_strategy_allocation=True,
            notional_unit="USD",
            exposure_accounting_mode=ExposureAccountingMode.LIVE_GROSS,
            max_gross_occupancy_notional=Decimal("45"),
            max_gross_occupancy_intent_count=10,
            max_exposure_facts_age_seconds=3600,
        ),
        execution_journal_authority=journal,
        gross_exposure_authority=journal,
    )
    try:
        gate.set_strategy_allocation(
            _ACCOUNT_SCOPE,
            _SCOPE.strategy_ref,
            "in-process-live-gross-allocation-v1",
            max_notional=Decimal("1000"),
            notional_unit="USD",
            max_gross_occupancy_notional=Decimal("45"),
            max_gross_occupancy_intent_count=10,
        )
        baseline_time = time.time_ns()
        with journal.writer_fence(_ACCOUNT_SCOPE) as fence:
            baseline = journal.ingest_gross_exposure_baseline(
                fence,
                source_snapshot={
                    "account_revision": 1,
                    "account_scope": {
                        "account_id": _ACCOUNT_SCOPE.account_id,
                        "environment": _ACCOUNT_SCOPE.environment,
                        "provider": _ACCOUNT_SCOPE.provider,
                    },
                    "complete_open_orders": True,
                    "complete_positions": True,
                    "notional_unit": "USD",
                    "observed_at_ns": baseline_time,
                    "open_orders": [],
                    "positions": [
                        {
                            "active_increase_intent_count": 0,
                            "gross_notional": "0",
                            "notional_unit": "USD",
                            "source_record_sha256": "1" * 64,
                            "strategy_id": _SCOPE.strategy_ref,
                            "valuation_facts_sha256": "2" * 64,
                        }
                    ],
                    "source_schema_version": "fixture-account-snapshot-v1",
                    "valid_until_ns": baseline_time + 3_600_000_000_000,
                    "valuation_facts": [],
                },
            )
            gate.install_gross_exposure_baseline(baseline)
        mapper = InstrumentRiskAdmissionMapper(
            _ACCOUNT_SCOPE,
            registry,
            clock_ns=time.time_ns,
            allocation_reader=lambda execution_scope: gate.get_strategy_allocation(
                _ACCOUNT_SCOPE, execution_scope.strategy_id
            ),
        )
        admission = DurableRiskAdmissionAdapter(gate, mapper)
        contract = _contract(("GTC",))
        service = RiskOrderEntryService(
            bt_api,
            {_SCOPE: target},
            {_SCOPE: admission},
            journal,
            gate,
            {_SCOPE: contract},
        )
        applied_proofs = []
        apply_order_exposure_observation = gate.apply_order_exposure_observation

        def capture_exposure_proof(proof):
            applied_proofs.append(proof)
            return apply_order_exposure_observation(proof)

        gate.apply_order_exposure_observation = capture_exposure_proof
        identity = WireIdentity(WireRole.STRATEGY_CLIENT, base._PRINCIPAL, _SCOPE.strategy_ref)

        def call(operation: str, intent_id: str, arguments: Mapping[str, Any]):
            frozen = dict(arguments)
            if "tags" in frozen:
                frozen["tags"] = MappingProxyType(dict(frozen["tags"]))
            return service.handle(
                operation,
                principal_ref=base._PRINCIPAL,
                identity=identity,
                scope=_SCOPE,
                arguments=MappingProxyType(frozen),
            )

        def submit(intent_id: str, quantity: str, price: str):
            arguments = base._submit_arguments(
                intent_id=intent_id,
                signal_id="signal-" + intent_id,
                instrument=_V2_INSTRUMENT,
                side="BUY",
                position_effect="OPEN",
                order_type="LIMIT",
                quantity=quantity,
                price=price,
                time_in_force="GTC",
                reduce_only=False,
                metadata_version=_V2_METADATA_VERSION,
                deadline_ns=None,
                tags={},
            )
            return call("orders.submit", intent_id, arguments)

        def query(intent_id: str):
            return call("orders.query", intent_id, {"intent_id": intent_id})

        def state(reply: Mapping[str, Any]) -> str:
            assert reply["result"]["found"] is True, reply
            return reply["result"]["record"]["state"]

        def exposure_rows() -> dict[str, Any]:
            risk = gate.gross_exposure_snapshot(_ACCOUNT_SCOPE)
            return {item.intent_id: item for item in risk["orders"]}

        def gross_total() -> Decimal:
            return sum(
                (
                    item.filled_gross_notional + item.open_gross_notional
                    for item in exposure_rows().values()
                ),
                Decimal(0),
            )

        terminal = submit("terminal-filled", "1", "20")
        assert terminal["ok"] is True and state(terminal) == "FILLED", terminal
        terminal_command = journal.get_command(_ACCOUNT_SCOPE, base._PRINCIPAL, "terminal-filled")
        assert terminal_command is not None
        assert provider.snapshot()[1] == {"fill_queries": 1, "order_queries": 1}

        opened = submit("post-ack-lifecycle", "1", "20")
        assert opened["ok"] is True and state(opened) == "ACKED_TRACKED", opened
        open_command = journal.get_command(_ACCOUNT_SCOPE, base._PRINCIPAL, "post-ack-lifecycle")
        assert open_command is not None
        pre_observation_rows = exposure_rows()
        assert terminal_command.risk_intent_id in pre_observation_rows
        assert open_command.risk_intent_id not in pre_observation_rows
        assert gross_total() == Decimal("20")

        denied = submit("before-ack-observation", "0.5", "20")
        assert denied["ok"] is False and denied["code"] == "ADMISSION_DENIED", denied
        assert state(denied) == "REJECTED"
        assert len(provider.snapshot()[0]) == 2

        acknowledged = query("post-ack-lifecycle")
        assert acknowledged["ok"] is True and state(acknowledged) == "ACKED", acknowledged
        assert provider.snapshot()[1] == {"fill_queries": 3, "order_queries": 3}
        assert gross_total() == Decimal("40")

        over_gross = submit("over-gross-limit", "0.5", "20")
        assert over_gross["ok"] is False and over_gross["code"] == "ADMISSION_DENIED", over_gross
        assert len(provider.snapshot()[0]) == 2

        first_fill = _V2SyntheticBtApiFeed._fill(
            account_id=base._PROVIDER_ACCOUNT,
            client_order_id=open_command.client_order_id,
            order_id="provider-" + open_command.client_order_id,
            symbol=_V2_INSTRUMENT,
            side="buy",
            trade_id="fill-" + open_command.client_order_id + "-1",
            quantity="0.5",
            price="20",
            revision=1,
            fee="-0.01",
        )
        _store_provider_observation(
            provider_path,
            open_command.client_order_id,
            status="partial",
            filled="0.5",
            order_revision=2,
            fills=[first_fill],
        )
        partial = query("post-ack-lifecycle")
        assert partial["ok"] is True and state(partial) == "PARTIALLY_FILLED", partial
        partial_exposure = exposure_rows()[open_command.risk_intent_id]
        assert partial_exposure.state.value == "PARTIALLY_FILLED"
        assert partial_exposure.cumulative_filled_quantity == Decimal("0.5")
        assert partial_exposure.leaves_quantity == Decimal("0.5")
        assert partial_exposure.filled_gross_notional == Decimal("10")
        assert partial_exposure.open_gross_notional == Decimal("10")
        assert applied_proofs[-1].leaves_quantity == Decimal("0.5")
        assert applied_proofs[-1].leaves_valuation_price == Decimal("20")
        assert gross_total() == Decimal("40")

        duplicate_partial = query("post-ack-lifecycle")
        assert duplicate_partial["ok"] is True and state(duplicate_partial) == "PARTIALLY_FILLED"
        assert gross_total() == Decimal("40")
        assert exposure_rows()[open_command.risk_intent_id].cumulative_filled_quantity == Decimal(
            "0.5"
        )

        _arm_incomplete_fills(provider_path)
        incomplete = query("post-ack-lifecycle")
        assert incomplete["ok"] is True and state(incomplete) == "UNKNOWN", incomplete
        assert exposure_rows()[open_command.risk_intent_id].state.value == "PARTIALLY_FILLED"
        assert gross_total() == Decimal("40")

        final_fills = [
            first_fill,
            _V2SyntheticBtApiFeed._fill(
                account_id=base._PROVIDER_ACCOUNT,
                client_order_id=open_command.client_order_id,
                order_id="provider-" + open_command.client_order_id,
                symbol=_V2_INSTRUMENT,
                side="buy",
                trade_id="fill-" + open_command.client_order_id + "-2",
                quantity="0.25",
                price="20",
                revision=3,
                fee="-0.005",
            ),
        ]
        _store_provider_observation(
            provider_path,
            open_command.client_order_id,
            status="cancelled",
            filled="0.75",
            order_revision=3,
            fills=final_fills,
        )
        cancelled = query("post-ack-lifecycle")
        assert cancelled["ok"] is True and state(cancelled) == "CANCELLED", cancelled
        cancelled_exposure = exposure_rows()[open_command.risk_intent_id]
        assert cancelled_exposure.state.value == "CANCELLED"
        assert cancelled_exposure.cumulative_filled_quantity == Decimal("0.75")
        assert cancelled_exposure.leaves_quantity == Decimal("0")
        assert cancelled_exposure.filled_gross_notional == Decimal("15")
        assert cancelled_exposure.open_gross_notional == 0
        assert applied_proofs[-1].state.value == "CANCELLED"
        assert applied_proofs[-1].leaves_quantity == Decimal("0")
        assert applied_proofs[-1].leaves_valuation_price is None
        assert gross_total() == Decimal("35")

        duplicate_cancel = query("post-ack-lifecycle")
        assert duplicate_cancel["ok"] is True and state(duplicate_cancel) == "CANCELLED"
        assert gross_total() == Decimal("35")
        assert exposure_rows()[open_command.risk_intent_id].cumulative_filled_quantity == Decimal(
            "0.75"
        )

        after_cancel = submit("after-cancel-capacity", "0.5", "20")
        assert after_cancel["ok"] is True and state(after_cancel) == "ACKED_TRACKED", after_cancel
        capacity_query = query("after-cancel-capacity")
        assert capacity_query["ok"] is True and state(capacity_query) == "ACKED", capacity_query
        assert gross_total() == Decimal("45")
        assert provider.snapshot()[1] == {"fill_queries": 10, "order_queries": 10}
        assert len(provider.snapshot()[0]) == 3

        current_command = journal.get_command(_ACCOUNT_SCOPE, base._PRINCIPAL, "post-ack-lifecycle")
        assert current_command is not None
        final_events = tuple(
            event
            for revision in range(
                current_command.created_revision, current_command.latest_revision + 1
            )
            if (
                event := journal.get_event(
                    _ACCOUNT_SCOPE, base._PRINCIPAL, "post-ack-lifecycle", revision
                )
            )
            is not None
        )
        assert sum(event.event_type == "UNKNOWN" for event in final_events) == 1
        assert sum(event.event_type == "EXPOSURE_APPLIED" for event in final_events) == 5
        assert all(
            _valid_exposure_applied_payload(event.payload, persisted=True)
            for event in final_events
            if event.event_type == "EXPOSURE_APPLIED"
        )
    finally:
        gate.close()
        bt_api.close()


def test_post_ack_unknown_requires_exact_persisted_event_shape() -> None:
    """Reject malformed journal-owned UNKNOWN events without loosening ACK history."""

    payload = MappingProxyType(
        {
            "dispatch_attempt_count": 1,
            "permit_id": "permit-1",
            "public_state": "UNKNOWN",
            "reason_code": "PROVIDER_QUERY_UNCERTAIN",
            "updated_at_ns": 1,
        }
    )
    assert _post_ack_unknown_payload_valid(
        payload,
        revision=9,
        ack_revision=8,
        permit_id="permit-1",
    )

    malformed: list[tuple[object, object, object]] = []
    invalid_attempt = dict(payload)
    invalid_attempt["dispatch_attempt_count"] = True
    malformed.append((invalid_attempt, 9, 8))
    missing_timestamp = dict(payload)
    del missing_timestamp["updated_at_ns"]
    malformed.append((missing_timestamp, 9, 8))
    invalid_timestamp = dict(payload)
    invalid_timestamp["updated_at_ns"] = True
    malformed.append((invalid_timestamp, 9, 8))
    zero_timestamp = dict(payload)
    zero_timestamp["updated_at_ns"] = 0
    malformed.append((zero_timestamp, 9, 8))
    malformed.append((dict(payload), 7, 8))
    invalid_reason = dict(payload)
    invalid_reason["reason_code"] = "provider query uncertain"
    malformed.append((invalid_reason, 9, 8))
    extra_claim = dict(payload)
    extra_claim["claim"] = {}
    malformed.append((extra_claim, 9, 8))
    malformed.append((dict(payload), 8, 8))
    malformed.append((dict(payload), True, 8))

    for candidate, revision, ack_revision in malformed:
        assert not _post_ack_unknown_payload_valid(
            candidate,
            revision=revision,
            ack_revision=ack_revision,
            permit_id="permit-1",
        )
    assert not _post_ack_unknown_payload_valid(
        dict(payload), revision=9, ack_revision=8, permit_id="permit-2"
    )
    assert not _post_ack_unknown_payload_valid(
        [], revision=9, ack_revision=8, permit_id="permit-1"
    )


def test_spawned_live_gross_terminal_and_post_ack_order_exposure_lifecycle(tmp_path) -> None:
    """Exercise V2 fills through ServiceHost/CURVE, BtApi normalization and SQLite Risk."""

    from tests.unit._spawned_process_helpers import (
        _assert_no_fallback_cleanup,
        _bounded_reap_spawned_process,
        _client_command,
        _close_normally,
        _receive,
        _start_process,
    )

    context = multiprocessing.get_context("spawn")
    endpoint = base._endpoint()
    server_public, server_secret = zmq.curve_keypair()
    strategy_public, strategy_secret = zmq.curve_keypair()
    keys = {
        "server_public": server_public,
        "server_secret": server_secret,
        "strategy_public": strategy_public,
    }
    paths = {
        "registry": str(tmp_path / "exposure-registry.sqlite3"),
        "gate": str(tmp_path / "exposure-risk.sqlite3"),
        "journal": str(tmp_path / "exposure-journal.sqlite3"),
        "provider": str(tmp_path / "exposure-provider.sqlite3"),
    }
    records = []
    forced = []
    failures = []
    try:
        host = _start_process(
            context,
            _host_process,
            (
                base._service_config(endpoint),
                paths["registry"],
                keys,
                paths["gate"],
                paths["journal"],
                paths["provider"],
                ("filled", "open", "open"),
            ),
            "risk-order-exposure-owner",
        )
        records.append(host)
        host_ready = _receive(host["pipe"])
        if host_ready[0] == "error":
            # A failed-start worker sends its finite error projection just
            # before exiting. Join and close that dead process directly so a
            # racing close-send cannot mask the actual startup diagnostic.
            host_process = host["process"]
            host_process.join(15)
            assert not host_process.is_alive(), "failed-start ServiceHost did not exit"
            startup_exit = host_process.exitcode
            host["pipe"].close()
            host_process.close()
            host["closed"] = True
            pytest.fail(f"ServiceHost startup returned {host_ready!r} (exit={startup_exit})")
        assert host_ready == ("ready", endpoint, "started"), host_ready

        strategy = _start_process(
            context,
            _exposure_strategy_process,
            (endpoint, strategy_public, strategy_secret, server_public),
            "risk-order-exposure-strategy",
        )
        records.append(strategy)
        strategy_ready = _receive(strategy["pipe"])
        assert strategy_ready[0] == "ready", strategy_ready
        run_id = strategy_ready[1]
        assert type(run_id) is str and run_id

        terminal_submit = _client_command(strategy, "submit", "terminal-filled", "1", "20")
        assert terminal_submit[0] == "submitted", terminal_submit
        terminal_view = terminal_submit[1]
        assert terminal_view["state"] == "FILLED", terminal_view
        assert terminal_view["dispatch_attempts"] == 1
        terminal_command = _client_command(host, "command", "terminal-filled")
        assert terminal_command[1] == "ACKED_TRACKED", terminal_command
        assert terminal_command[3].startswith("risk-"), terminal_command
        assert terminal_command[5].startswith("risk-order:"), terminal_command

        after_terminal = _client_command(host, "snapshot")
        assert after_terminal[1] == (terminal_command[3],)
        assert after_terminal[2] == {"fill_queries": 1, "order_queries": 1}
        terminal_rows = _risk_order_rows(after_terminal)
        terminal_exposure = terminal_rows[terminal_command[5]]
        assert terminal_exposure["state"] == "FILLED"
        assert Decimal(terminal_exposure["cumulative_filled_quantity"]) == Decimal("1")
        assert Decimal(terminal_exposure["filled_gross_notional"]) == Decimal("20")
        assert Decimal(terminal_exposure["open_gross_notional"]) == 0

        terminal_events = _client_command(host, "events", "terminal-filled")[1]
        terminal_ack_event = next(
            payload
            for event_type, _revision, payload in terminal_events
            if event_type == "ACKED_TRACKED"
        )
        terminal_fill_event = next(
            payload
            for event_type, _revision, payload in terminal_events
            if event_type == "FILL_OBSERVATION"
        )
        terminal_order_event = next(
            payload
            for event_type, _revision, payload in terminal_events
            if event_type == "ORDER_OBSERVATION"
        )
        terminal_fill = terminal_fill_event["records"][0]
        assert terminal_fill["fee"] == "-0.05"
        assert terminal_fill["fee_currency"] == "USD"
        assert terminal_fill["source_revision"] == 1
        terminal_source = terminal_fill_event["fill_row_provenance"][0]["source_values"]
        assert terminal_source["fee_amount"] == "-0.05"
        assert terminal_source["fee_currency"] == "USD"
        assert terminal_source["fill_revision"] == 1

        terminal_receipt = _client_command(host, "exposure_receipt", "terminal-filled")
        assert terminal_receipt[:4] == (
            "exposure_receipt",
            "DispatchExposureResolutionReceipt",
            "TERMINAL_EXPOSURE_RESOLVED",
            False,
        ), terminal_receipt
        assert terminal_receipt[4:6] == (terminal_command[5], terminal_command[2])
        assert len(terminal_receipt[6]) == 64
        assert len(terminal_receipt[7]) == 64
        assert terminal_receipt[6:8] == (
            terminal_ack_event["proof_sha256"],
            terminal_ack_event["exposure_proof_sha256"],
        )
        assert terminal_receipt[8] == "FILLED"
        assert type(terminal_receipt[9]) is str
        assert type(terminal_receipt[10]) is str
        terminal_requested_quantity = Decimal(terminal_receipt[9])
        terminal_filled_quantity = Decimal(terminal_receipt[10])
        assert terminal_requested_quantity.is_finite()
        assert terminal_filled_quantity.is_finite()
        assert terminal_requested_quantity == Decimal("1")
        assert terminal_filled_quantity == Decimal("1")
        assert type(terminal_receipt[11]) is int
        assert terminal_receipt[11] == 1
        assert terminal_receipt[12] == terminal_order_event["order_revision"]
        assert terminal_receipt[13] == next(
            revision
            for event_type, revision, _payload in terminal_events
            if event_type == "FILL_OBSERVATION"
        )
        assert terminal_receipt[14] == terminal_ack_event["exposure_reference_id"]
        assert terminal_receipt[15] == terminal_ack_event["exposure_reference_sha256"]
        assert len(terminal_receipt[15]) == 64
        assert terminal_receipt[16] is False

        terminal_duplicate = _client_command(strategy, "submit", "terminal-filled", "1", "20")
        assert terminal_duplicate[0] == "submitted", terminal_duplicate
        assert terminal_duplicate[1]["state"] == "FILLED"
        assert terminal_duplicate[1]["provider_order_id"] == terminal_view["provider_order_id"]
        after_terminal_duplicate = _client_command(host, "snapshot")
        assert after_terminal_duplicate[1:3] == after_terminal[1:3]
        assert len(_risk_order_rows(after_terminal_duplicate)) == 1

        open_submit = _client_command(strategy, "submit", "post-ack-lifecycle", "1", "20")
        assert open_submit[0] == "submitted", open_submit
        assert open_submit[1]["state"] == "ACKED_TRACKED"
        open_command = _client_command(host, "command", "post-ack-lifecycle")
        assert open_command[1] == "ACKED_TRACKED", open_command
        assert open_command[3].startswith("risk-"), open_command
        assert open_command[5].startswith("risk-order:"), open_command
        assert open_command[4] > terminal_command[4]
        open_events = _client_command(host, "events", "post-ack-lifecycle")[1]
        open_proof_event = next(
            payload
            for event_type, _revision, payload in open_events
            if event_type == "PROOF_READY"
        )
        open_ack_event = next(
            payload
            for event_type, _revision, payload in open_events
            if event_type == "ACKED_TRACKED"
        )
        # Ordinary tracked-order ACKs identify the immutable PROOF_READY row
        # they resolve, using the same proof anchors as recovery ACKs.
        assert "proof_sha256" not in open_proof_event
        assert type(open_ack_event.get("proof_sha256")) is str
        assert len(open_ack_event["proof_sha256"]) == 64
        proof_ready_revision = next(
            revision
            for event_type, revision, _payload in open_events
            if event_type == "PROOF_READY"
        )
        assert open_ack_event["proof_ready_revision"] == proof_ready_revision
        assert open_ack_event["claim_digest"] == open_proof_event["claim_digest"]
        assert open_ack_event["recovery_attestation_sha256"] is None
        assert type(open_proof_event.get("exposure_reference_id")) is str
        assert type(open_proof_event.get("exposure_reference_sha256")) is str
        assert type(open_ack_event.get("exposure_reference_id")) is str
        assert type(open_ack_event.get("exposure_reference_sha256")) is str

        pending_denial = _client_command(strategy, "submit", "before-ack-observation", "0.5", "20")
        assert pending_denial == (
            "submit_error",
            "ADMISSION_DENIED",
            "REJECTED",
            None,
        ), pending_denial
        before_ack_observation = _client_command(host, "snapshot")
        assert len(before_ack_observation[1]) == 2

        acknowledged = _client_command(strategy, "query", "post-ack-lifecycle")
        assert acknowledged[0] == "queried", acknowledged
        assert acknowledged[1]["state"] == "ACKED"
        after_ack_observation = _client_command(host, "snapshot")
        assert after_ack_observation[2] == {
            name: before_ack_observation[2][name] + 1
            for name in ("fill_queries", "order_queries")
        }
        acked_rows = _risk_order_rows(after_ack_observation)
        acked_exposure = acked_rows[open_command[5]]
        assert acked_exposure["state"] == "ACKED"
        assert Decimal(acked_exposure["leaves_quantity"]) == Decimal("1")
        assert Decimal(acked_exposure["open_gross_notional"]) == Decimal("20")
        assert sum(
            Decimal(row["filled_gross_notional"]) + Decimal(row["open_gross_notional"])
            for row in acked_rows.values()
        ) == Decimal("40")

        gross_denial = _client_command(strategy, "submit", "over-gross-limit", "0.5", "20")
        assert gross_denial == (
            "submit_error",
            "ADMISSION_DENIED",
            "REJECTED",
            None,
        ), gross_denial
        after_gross_denial = _client_command(host, "snapshot")
        assert after_gross_denial[1] == after_ack_observation[1]
        assert len(_risk_order_rows(after_gross_denial)) == 2

        open_fill = _V2SyntheticBtApiFeed._fill(
            account_id=base._PROVIDER_ACCOUNT,
            client_order_id=open_command[3],
            order_id="provider-" + open_command[3],
            symbol=_V2_INSTRUMENT,
            side="buy",
            trade_id="fill-" + open_command[3] + "-1",
            quantity="0.5",
            price="20",
            revision=1,
            fee="-0.01",
        )
        _store_provider_observation(
            paths["provider"],
            open_command[3],
            status="partial",
            filled="0.5",
            order_revision=2,
            fills=[open_fill],
        )
        partial_query = _client_command(strategy, "query", "post-ack-lifecycle")
        assert partial_query[0] == "queried", partial_query
        assert partial_query[1]["state"] == "PARTIALLY_FILLED"
        after_partial = _client_command(host, "snapshot")
        partial_rows = _risk_order_rows(after_partial)
        partial_exposure = partial_rows[open_command[5]]
        assert partial_exposure["state"] == "PARTIALLY_FILLED"
        assert Decimal(partial_exposure["cumulative_filled_quantity"]) == Decimal("0.5")
        assert Decimal(partial_exposure["leaves_quantity"]) == Decimal("0.5")
        assert Decimal(partial_exposure["filled_gross_notional"]) == Decimal("10")
        assert Decimal(partial_exposure["open_gross_notional"]) == Decimal("10")
        partial_total = sum(
            Decimal(row["filled_gross_notional"]) + Decimal(row["open_gross_notional"])
            for row in partial_rows.values()
        )
        assert partial_total == Decimal("40")

        duplicate_partial = _client_command(strategy, "query", "post-ack-lifecycle")
        assert duplicate_partial[0] == "queried", duplicate_partial
        assert duplicate_partial[1]["state"] == "PARTIALLY_FILLED"
        after_duplicate_partial = _client_command(host, "snapshot")
        duplicate_partial_rows = _risk_order_rows(after_duplicate_partial)
        assert (
            sum(
                Decimal(row["filled_gross_notional"]) + Decimal(row["open_gross_notional"])
                for row in duplicate_partial_rows.values()
            )
            == partial_total
        )
        assert (
            duplicate_partial_rows[open_command[5]]["cumulative_filled_quantity"]
            == (partial_exposure["cumulative_filled_quantity"])
        )

        _arm_incomplete_fills(paths["provider"])
        incomplete = _client_command(strategy, "query", "post-ack-lifecycle")
        assert incomplete[0] == "queried", incomplete
        assert incomplete[1]["state"] == "UNKNOWN"
        after_incomplete = _client_command(host, "snapshot")
        incomplete_rows = _risk_order_rows(after_incomplete)
        assert incomplete_rows[open_command[5]]["state"] == "PARTIALLY_FILLED"
        assert (
            sum(
                Decimal(row["filled_gross_notional"]) + Decimal(row["open_gross_notional"])
                for row in incomplete_rows.values()
            )
            == partial_total
        )

        final_fills = [
            open_fill,
            _V2SyntheticBtApiFeed._fill(
                account_id=base._PROVIDER_ACCOUNT,
                client_order_id=open_command[3],
                order_id="provider-" + open_command[3],
                symbol=_V2_INSTRUMENT,
                side="buy",
                trade_id="fill-" + open_command[3] + "-2",
                quantity="0.25",
                price="20",
                revision=3,
                fee="-0.005",
            ),
        ]
        _store_provider_observation(
            paths["provider"],
            open_command[3],
            status="cancelled",
            filled="0.75",
            order_revision=3,
            fills=final_fills,
        )
        cancelled = _client_command(strategy, "query", "post-ack-lifecycle")
        assert cancelled[0] == "queried", cancelled
        assert cancelled[1]["state"] == "CANCELLED"
        after_cancelled = _client_command(host, "snapshot")
        cancelled_rows = _risk_order_rows(after_cancelled)
        cancelled_exposure = cancelled_rows[open_command[5]]
        assert cancelled_exposure["state"] == "CANCELLED"
        assert Decimal(cancelled_exposure["cumulative_filled_quantity"]) == Decimal("0.75")
        assert Decimal(cancelled_exposure["leaves_quantity"]) == 0
        assert Decimal(cancelled_exposure["filled_gross_notional"]) == Decimal("15")
        assert Decimal(cancelled_exposure["open_gross_notional"]) == 0
        cancelled_total = sum(
            Decimal(row["filled_gross_notional"]) + Decimal(row["open_gross_notional"])
            for row in cancelled_rows.values()
        )
        assert cancelled_total == Decimal("35")

        duplicate_cancelled = _client_command(strategy, "query", "post-ack-lifecycle")
        assert duplicate_cancelled[0] == "queried", duplicate_cancelled
        assert duplicate_cancelled[1]["state"] == "CANCELLED"
        after_duplicate_cancelled = _client_command(host, "snapshot")
        duplicate_cancelled_rows = _risk_order_rows(after_duplicate_cancelled)
        assert (
            sum(
                Decimal(row["filled_gross_notional"]) + Decimal(row["open_gross_notional"])
                for row in duplicate_cancelled_rows.values()
            )
            == cancelled_total
        )
        assert len(after_duplicate_cancelled[1]) == 2

        released_capacity = _client_command(
            strategy, "submit", "after-cancel-capacity", "0.5", "20"
        )
        assert released_capacity[0] == "submitted", released_capacity
        assert released_capacity[1]["state"] == "ACKED_TRACKED"
        before_third_query = _client_command(host, "snapshot")
        assert len(before_third_query[1]) == 3
        third_query = _client_command(strategy, "query", "after-cancel-capacity")
        assert third_query[0] == "queried", third_query
        assert third_query[1]["state"] == "ACKED"
        capacity_command = _client_command(host, "command", "after-cancel-capacity")
        assert capacity_command[1] == "EXPOSURE_APPLIED", capacity_command
        assert capacity_command[5].startswith("risk-order:"), capacity_command
        final_snapshot = _client_command(host, "snapshot")
        final_rows = _risk_order_rows(final_snapshot)
        assert len(final_rows) == 3
        assert final_rows[capacity_command[5]]["state"] == "ACKED"
        assert Decimal(final_rows[capacity_command[5]]["open_gross_notional"]) == Decimal("10")
        assert sum(
            Decimal(row["filled_gross_notional"]) + Decimal(row["open_gross_notional"])
            for row in final_rows.values()
        ) == Decimal("45")

        final_events = _client_command(host, "events", "post-ack-lifecycle")[1]
        provider_queries = [
            payload
            for event_type, _revision, payload in final_events
            if event_type == "PROVIDER_QUERY"
        ]
        assert provider_queries
        complete_cancel_observation = provider_queries[-1]
        assert complete_cancel_observation["exposure_state"] == "CANCELLED"
        assert complete_cancel_observation["order_revision"] == 3
        assert complete_cancel_observation["fill_watermark_revision"] == 3
        query_fills = complete_cancel_observation["records"]
        assert len(query_fills) == 2
        assert [item["source_revision"] for item in query_fills] == [1, 3]
        assert [item["fee"] for item in query_fills] == ["-0.01", "-0.005"]
        assert [item["fee_currency"] for item in query_fills] == ["USD", "USD"]

        _close_normally(strategy, expected_tag="closed")
        _close_normally(host, expected_tag="closed")
    finally:
        for record in reversed(records):
            _bounded_reap_spawned_process(record, forced, failures)
        _assert_no_fallback_cleanup(forced, failures)


def _contract(time_in_force: tuple[str, ...], *, v2: bool = True) -> RiskBtApiProviderContract:
    fields = (
        {
            "normalizer_schema": "bt-api-order-source-provenance-v2",
            "order_revision_field": "order_revision",
            "fill_revision_field": "fill_revision",
            "fill_watermark_field": "fill_watermark",
            "fee_amount_field": "fee_amount",
            "fee_currency_field": "fee_currency",
            "quote_currency_by_instrument": (("BTC-USD", "USD"),),
        }
        if v2
        else {}
    )
    return RiskBtApiProviderContract(
        exchange_name=_EXCHANGE,
        profile_id="risk-order-contract-profile-v2" if v2 else "risk-order-contract-profile-v1",
        order_schema="fixture-order-v2" if v2 else "fixture-order-v1",
        fill_schema="fixture-fill-v2" if v2 else "fixture-fill-v1",
        client_order_id_query=True,
        client_order_id_query_unique=True,
        complete_order_snapshot=True,
        complete_fill_history=True,
        provider_accepts_client_order_id=True,
        provider_status_mapping=_STATUS_MAPPING,
        supported_order_types=("LIMIT",),
        supported_time_in_force=time_in_force,
        supported_position_effects=("OPEN",),
        **fields,
    )


def test_v2_ioc_profile_is_bound_in_payload_and_fingerprint() -> None:
    ioc = _contract(("IOC",))
    gtc = _contract(("GTC",))

    assert ioc.exposure_ready is True
    assert ioc.risk_only_write_ready is False
    assert ioc.to_payload()["schema"] == "bt-api-risk-provider-contract-v2"
    assert ioc.to_payload()["supported_time_in_force"] == ["IOC"]
    assert ioc.fingerprint != gtc.fingerprint


def test_ioc_profile_requires_v2_evidence_and_finite_tif_contract() -> None:
    with pytest.raises(RiskOrderEntryError) as legacy_error:
        _contract(("IOC",), v2=False)
    assert legacy_error.value.code == "PROVIDER_V2_SOURCE_CONTRACT_REQUIRED"

    day = _contract(("DAY",))
    assert day.risk_only_order_write_ready is True
    assert day.risk_only_write_ready is False
    assert day.to_payload()["supported_time_in_force"] == ["DAY"]

    gtc_and_day = _contract(("GTC", "DAY"))
    assert gtc_and_day.risk_only_order_write_ready is True
    assert gtc_and_day.risk_only_write_ready is True
    assert gtc_and_day.to_payload()["supported_time_in_force"] == ["GTC", "DAY"]


def test_ioc_only_profile_cannot_assemble_normal_risk_order_entry(tmp_path) -> None:
    now_ns = 1_800_000_000_000_000_000
    metadata = InstrumentRiskMetadata(
        instrument="BTC-USD",
        metadata_version="risk-order-contract-metadata-v1",
        as_of_ns=now_ns - 1_000,
        expires_at_ns=now_ns + 60_000_000_000,
        tick_size=Decimal("0.01"),
        quantity_step=Decimal("0.001"),
        contract_multiplier=Decimal("1"),
        max_gross_notional=Decimal("100000"),
        valuation_unit="USD",
    )
    registry = InstrumentRiskRegistry((metadata,))
    target = RiskOrderTarget(
        account_scope=_ACCOUNT_SCOPE,
        strategy_id="strategy-a",
        exchange_name=_EXCHANGE,
        provider_account_id=_ACCOUNT_SCOPE.account_id,
        registry=registry,
    )
    journal = RiskOrderJournal(tmp_path / "journal.sqlite", writer_id="ioc-contract-test")
    gate = DurableRiskGate(
        tmp_path / "risk.sqlite",
        RiskPolicy(
            "ioc-contract-policy-v1",
            Decimal("100000"),
            max_increase_count=5,
            notional_unit="USD",
        ),
        execution_journal_authority=journal,
    )
    mapper = InstrumentRiskAdmissionMapper(_ACCOUNT_SCOPE, registry)
    adapter = DurableRiskAdmissionAdapter(gate, mapper)
    bt_api = BtApi()
    try:
        with pytest.raises(RiskOrderEntryError) as error:
            RiskOrderEntryService(
                bt_api,
                {_SCOPE: target},
                {_SCOPE: adapter},
                journal,
                gate,
                {_SCOPE: _contract(("IOC",))},
            )
        assert error.value.code == "PROVIDER_GTC_CAPABILITY_REQUIRED"
    finally:
        bt_api.close()
        gate.close()
