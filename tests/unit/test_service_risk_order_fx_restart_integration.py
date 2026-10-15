"""Normal ServiceHost evidence for source-bound FX and applied-order restart."""

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

import zmq
from bt_api_base.service_protocol import WireIdentity, WireRole
from bt_api_risk import (
    AccountScope,
    DurableRiskAdmissionAdapter,
    DurableRiskGate,
    ExposureAccountingMode,
    InstrumentRiskAdmissionMapper,
    InstrumentRiskFxRate,
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
    RiskOrderEntryService,
    RiskOrderTarget,
)
from bt_api_py.service_risk_order_journal import (
    RiskOrderJournal,
    _valuation_facts_sha256,
    canonical_json,
    sha256_text,
)
from bt_api_py.service_runtime import ClientCurveKeys, ServiceClientRuntime, ZmqTransportFactory
from tests.unit import test_service_risk_only_order_integration as base
from tests.unit import test_service_risk_order_exposure_integration as v2
from tests.unit._spawned_process_helpers import (
    _assert_no_fallback_cleanup,
    _bounded_reap_spawned_process,
    _client_command,
    _close_normally,
    _receive,
    _start_process,
)

_INSTRUMENT = "ETH-EUR"
_METADATA_VERSION = "risk-order-fx-metadata-v1"
_QUOTE = "EUR"
_VALUATION = "USD"
_MULTIPLIER = Decimal("3")
_FX_SOURCE = "fixture-eurusd-v1"
_ORDER_SCHEMA = v2._V2_ORDER_SCHEMA
_FILL_SCHEMA = v2._V2_FILL_SCHEMA
_STATUS_MAPPING = (
    ("cancelled", "canceled"),
    ("expired", "expired"),
    ("filled", "completed"),
    ("open", "accepted"),
    ("partial", "partial"),
    ("rejected", "rejected"),
)
_ACCOUNT_SCOPE = AccountScope("fixture", base._PROVIDER_ACCOUNT, "simulation")


class _FxSyntheticBtApiFeed(v2._V2SyntheticBtApiFeed):
    """Retain the production BtApi path while labeling synthetic fees in EUR."""

    @staticmethod
    def _fill(**values: Any) -> dict[str, Any]:
        row = v2._V2SyntheticBtApiFeed._fill(**values)
        row["fee_currency"] = _QUOTE
        return row


class _ScaledOrderPriceFxFeed(_FxSyntheticBtApiFeed):
    """Preserve an exact provider price spelling while using normal SDK paths."""

    def __init__(self, ledger_path: str, initial_states: tuple[str, ...], price_text: str):
        super().__init__(ledger_path, initial_states)
        self._source_price_text = price_text

    def make_order(
        self,
        symbol: str,
        volume: float,
        price: float,
        order_type: str,
        *,
        client_order_id: str,
        **kwargs: Any,
    ) -> dict[str, Any]:
        super().make_order(
            symbol,
            volume,
            price,
            order_type,
            client_order_id=client_order_id,
            **kwargs,
        )
        with sqlite3.connect(self._ledger_path) as connection:
            found = connection.execute(
                "SELECT order_json FROM provider_orders WHERE client_order_id=?",
                (client_order_id,),
            ).fetchone()
            assert found is not None
            row = json.loads(found[0])
            row["price"] = self._source_price_text
            connection.execute(
                "UPDATE provider_orders SET order_json=? WHERE client_order_id=?",
                (
                    json.dumps(row, sort_keys=True, separators=(",", ":")),
                    client_order_id,
                ),
            )
        return self._order_envelope(row)


def _fx_fact(rate: str, revision: str, observed_at_ns: int, valid_until_ns: int) -> dict[str, Any]:
    source_payload = {
        "instrument": _INSTRUMENT,
        "observed_at_ns": observed_at_ns,
        "quote_currency": _QUOTE,
        "rate": rate,
        "source_ref": _FX_SOURCE,
        "source_revision": revision,
        "valid_until_ns": valid_until_ns,
        "valuation_unit": _VALUATION,
    }
    return {
        **source_payload,
        "source_record_sha256": sha256_text(canonical_json(source_payload)),
    }


def _risk_order_fx_host(
    control,
    raw_config,
    registry_path: str,
    keys: dict[str, bytes],
    gate_path: str,
    journal_path: str,
    provider_path: str,
    metadata_args: dict[str, Any],
    fx_args: dict[str, Any],
    initial_states: tuple[str, ...],
    install_baseline: bool,
) -> None:
    config = parse_service_config(raw_config)
    provider = _FxSyntheticBtApiFeed(provider_path, initial_states)
    journal = None
    gate = None
    bt_api = None
    host = None
    try:
        metadata = InstrumentRiskMetadata(**metadata_args)
        fx_rate = InstrumentRiskFxRate(**fx_args)
        registry = InstrumentRiskRegistry((metadata,))
        target = RiskOrderTarget(
            account_scope=_ACCOUNT_SCOPE,
            strategy_id=base._STRATEGY,
            exchange_name=base._EXCHANGE,
            provider_account_id=base._PROVIDER_ACCOUNT,
            registry=registry,
            fx_rates_by_instrument=(fx_rate,),
        )
        bt_api = BtApi(debug=False)
        bt_api.exchange_feeds[base._EXCHANGE] = provider
        journal = RiskOrderJournal(journal_path, writer_id="risk-order-fx-owner")
        gate = DurableRiskGate(
            gate_path,
            RiskPolicy(
                "service-risk-policy-v1",
                Decimal("1000"),
                max_increase_count=10,
                require_strategy_allocation=True,
                notional_unit=_VALUATION,
                exposure_accounting_mode=ExposureAccountingMode.LIVE_GROSS,
                max_gross_occupancy_notional=Decimal("170"),
                max_gross_occupancy_intent_count=10,
                max_exposure_facts_age_seconds=3600,
            ),
            execution_journal_authority=journal,
            gross_exposure_authority=journal,
        )
        gate.set_strategy_allocation(
            _ACCOUNT_SCOPE,
            base._STRATEGY,
            "fixture-risk-fx-allocation-v1",
            max_notional=Decimal("1000"),
            notional_unit=_VALUATION,
            max_gross_occupancy_notional=Decimal("170"),
            max_gross_occupancy_intent_count=10,
        )
        if install_baseline:
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
                        "notional_unit": _VALUATION,
                        "observed_at_ns": baseline_time,
                        "open_orders": [],
                        "positions": [
                            {
                                "active_increase_intent_count": 0,
                                "gross_notional": "0",
                                "notional_unit": _VALUATION,
                                "source_record_sha256": "1" * 64,
                                "strategy_id": base._STRATEGY,
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
            fx_rates_by_instrument=(fx_rate,),
        )
        admission = DurableRiskAdmissionAdapter(gate, mapper)
        contract = RiskBtApiProviderContract(
            exchange_name=base._EXCHANGE,
            profile_id="fixture-risk-fx-profile-v2",
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
            quote_currency_by_instrument=((_INSTRUMENT, _QUOTE),),
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

        host = ServiceHost(
            config,
            base._authority(config, keys),
            registry_path,
            resource_provider,
        )
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
                risk = gate.gross_exposure_snapshot(_ACCOUNT_SCOPE)
                orders = tuple(
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
                        "notional_unit": item.notional_unit,
                        "proof_sha256": item.proof_sha256,
                    }
                    for item in risk["orders"]
                )
                control.send(
                    (
                        "snapshot",
                        provider.snapshot()[0],
                        provider.snapshot()[1],
                        risk["baseline_revision"],
                        orders,
                        tuple(risk["active_freeze_reasons"]),
                    )
                )
                continue
            if command[0] == "command":
                item = journal.get_command(_ACCOUNT_SCOPE, base._PRINCIPAL, command[1])
                control.send(
                    (
                        "command",
                        None if item is None else item.state,
                        None if item is None else item.permit_id,
                        None if item is None else item.client_order_id,
                        None if item is None else item.risk_intent_id,
                        None if item is None else dict(item.wire_scope),
                        None if item is None else item.latest_revision,
                    )
                )
                continue
            if command[0] == "events":
                item = journal.get_command(_ACCOUNT_SCOPE, base._PRINCIPAL, command[1])
                events = []
                if item is not None:
                    for revision in range(item.created_revision, item.latest_revision + 1):
                        event = journal.get_event(
                            _ACCOUNT_SCOPE,
                            base._PRINCIPAL,
                            command[1],
                            revision,
                        )
                        if event is not None:
                            events.append(
                                (
                                    event.event_type,
                                    event.revision,
                                    v2._plain(event.payload),
                                )
                            )
                control.send(("events", tuple(events)))
                continue
            raise AssertionError("unknown FX host control")
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


def _risk_order_fx_strategy(
    control,
    endpoint: str,
    strategy_public: bytes,
    strategy_secret: bytes,
    server_public: bytes,
    logical_run_id: str | None = None,
) -> None:
    runtime = None
    try:
        config = base._client_config(endpoint, "risk-order-strategy-curve-v1")
        identity = WireIdentity(WireRole.STRATEGY_CLIENT, base._PRINCIPAL, base._STRATEGY)
        peer = WireIdentity(WireRole.SERVICE, base._SERVICE_PRINCIPAL, base._SERVICE_ID)
        runtime = ServiceClientRuntime(
            config,
            identity=identity,
            scope_resolver=lambda _ref: base._SCOPE,
            peer_resolver=lambda _service: peer,
            run_id=logical_run_id,
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
        control.send(("ready", runtime.run_id, runtime.client_session_id))
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
                        instrument=_INSTRUMENT,
                        side="BUY",
                        position_effect="OPEN",
                        order_type="LIMIT",
                        quantity=command[2],
                        price=command[3],
                        time_in_force="GTC",
                        reduce_only=False,
                        metadata_version=_METADATA_VERSION,
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
                    control.send(("submitted", v2._plain(asdict(view))))
                continue
            if command[0] == "query":
                try:
                    view = client.query(command[1])
                except ServiceExecutionError as error:
                    control.send(("query_error", error.code))
                else:
                    control.send(("queried", None if view is None else v2._plain(asdict(view))))
                continue
            raise AssertionError("unknown FX strategy control")
    except BaseException as exc:
        if runtime is not None:
            with suppress(BaseException):
                runtime.close()
        with suppress(Exception):
            control.send(("error", getattr(exc, "code", type(exc).__name__)))
        raise
    finally:
        control.close()


def _risk_rows(snapshot: tuple[Any, ...]) -> dict[str, dict[str, Any]]:
    assert snapshot[0] == "snapshot", snapshot
    return {row["intent_id"]: row for row in snapshot[4]}


def _risk_total(snapshot: tuple[Any, ...]) -> Decimal:
    return sum(
        (
            Decimal(row["filled_gross_notional"]) + Decimal(row["open_gross_notional"])
            for row in _risk_rows(snapshot).values()
        ),
        Decimal(0),
    )


def test_spawned_fx_risk_order_survives_restart_expiry_and_revaluation(tmp_path) -> None:
    """Exercise typed FX admission and observation across real service restarts."""

    context = multiprocessing.get_context("spawn")
    endpoint = base._endpoint()
    server_public, server_secret = zmq.curve_keypair()
    strategy_public, strategy_secret = zmq.curve_keypair()
    keys = {
        "server_public": server_public,
        "server_secret": server_secret,
        "strategy_public": strategy_public,
    }
    raw_config = base._service_config(endpoint)
    paths = {
        "registry": str(tmp_path / "fx-registry.sqlite3"),
        "gate": str(tmp_path / "fx-risk.sqlite3"),
        "journal": str(tmp_path / "fx-journal.sqlite3"),
        "provider": str(tmp_path / "fx-provider.sqlite3"),
    }
    now_ns = time.time_ns()
    metadata_args = {
        "instrument": _INSTRUMENT,
        "metadata_version": _METADATA_VERSION,
        "as_of_ns": now_ns - 1_000_000_000,
        "expires_at_ns": now_ns + 86_400_000_000_000,
        "tick_size": Decimal("0.01"),
        "quantity_step": Decimal("0.001"),
        "contract_multiplier": _MULTIPLIER,
        "max_gross_notional": Decimal("1000"),
        "taker_fee_bps": Decimal("0"),
        "fixed_fee": Decimal("0"),
        "max_slippage_bps": Decimal("0"),
        "quantity_unit": "ETH",
        "valuation_unit": _VALUATION,
        "quote_currency": _QUOTE,
        "fx_source_ref": _FX_SOURCE,
    }
    fx1 = _fx_fact(
        "1.25",
        "eurusd-r1",
        now_ns - 100_000_000,
        now_ns + 86_400_000_000_000,
    )
    stale_now = time.time_ns()
    fx_stale = _fx_fact(
        "1.25",
        "eurusd-expired",
        stale_now - 7_200_000_000_000,
        stale_now - 3_600_000_000_000,
    )
    fresh_now = time.time_ns()
    fx2 = _fx_fact(
        "1.30",
        "eurusd-r2",
        fresh_now - 100_000_000,
        fresh_now + 86_400_000_000_000,
    )
    host_records: list[dict[str, Any]] = []
    strategy_records: list[dict[str, Any]] = []
    forced: list[str] = []
    failures: list[str] = []

    def start_host(
        label: str, fx_args: dict[str, Any], initial_states: tuple[str, ...], install: bool
    ):
        record = _start_process(
            context,
            _risk_order_fx_host,
            (
                raw_config,
                paths["registry"],
                keys,
                paths["gate"],
                paths["journal"],
                paths["provider"],
                metadata_args,
                fx_args,
                initial_states,
                install,
            ),
            label,
        )
        host_records.append(record)
        ready = _receive(record["pipe"])
        assert ready == ("ready", endpoint, "started"), ready
        return record

    def start_strategy(
        host_record: dict[str, Any], label: str, *, logical_run_id: str | None = None
    ):
        record = _start_process(
            context,
            _risk_order_fx_strategy,
            (endpoint, strategy_public, strategy_secret, server_public, logical_run_id),
            label,
        )
        strategy_records.append(record)
        ready = _receive(record["pipe"])
        assert (
            type(ready) is tuple
            and len(ready) == 3
            and ready[0] == "ready"
            and type(ready[1]) is str
            and ready[1]
            and type(ready[2]) is str
            and ready[2]
        ), ready
        return record, ready[1], ready[2]

    try:
        host1 = start_host("risk-fx-host-1", fx1, ("open",), True)
        strategy1, run1, session1 = start_strategy(host1, "risk-fx-strategy-1")
        intent_id = "eurusd-restart-order"
        first_submit = _client_command(strategy1, "submit", intent_id, "2", "20")
        assert first_submit[0] == "submitted", first_submit
        assert first_submit[1]["state"] == "ACKED_TRACKED", first_submit
        command1 = _client_command(host1, "command", intent_id)
        assert command1[1] == "ACKED_TRACKED", command1
        risk_intent_id, client_order_id = command1[4], command1[3]
        assert risk_intent_id.startswith("risk-order:")
        assert client_order_id.startswith("risk-")
        assert command1[5]["run_id"] == run1

        first_fill = _FxSyntheticBtApiFeed._fill(
            account_id=base._PROVIDER_ACCOUNT,
            client_order_id=client_order_id,
            order_id="provider-" + client_order_id,
            symbol=_INSTRUMENT,
            side="buy",
            trade_id="fill-" + client_order_id + "-1",
            quantity="0.5",
            price="20",
            revision=1,
            fee="0",
        )
        v2._store_provider_observation(
            paths["provider"],
            client_order_id,
            status="partial",
            filled="0.5",
            order_revision=2,
            fills=[first_fill],
        )
        partial = _client_command(strategy1, "query", intent_id)
        assert partial[0] == "queried" and partial[1]["state"] == "PARTIALLY_FILLED", partial
        snapshot1 = _client_command(host1, "snapshot")
        partial_row = _risk_rows(snapshot1)[risk_intent_id]
        assert Decimal(partial_row["requested_quantity"]) == Decimal("2")
        assert Decimal(partial_row["cumulative_filled_quantity"]) == Decimal("0.5")
        assert Decimal(partial_row["leaves_quantity"]) == Decimal("1.5")
        assert Decimal(partial_row["filled_gross_notional"]) == Decimal("37.5")
        assert Decimal(partial_row["open_gross_notional"]) == Decimal("112.5")
        assert partial_row["notional_unit"] == _VALUATION
        assert _risk_total(snapshot1) == Decimal("150")
        assert snapshot1[1] == (client_order_id,)

        applied_query = next(
            payload
            for event_type, _revision, payload in _client_command(host1, "events", intent_id)[1]
            if event_type == "PROVIDER_QUERY"
            and payload.get("exposure_state") == "PARTIALLY_FILLED"
        )
        assert applied_query["source_values"]["order"]["price"] == "20.0"
        assert applied_query["metadata_facts"]["instrument_fx_rate"]["rate"] == "1.25"
        assert applied_query["metadata_facts"]["instrument_fx_rate_sha256"] == sha256_text(
            canonical_json(applied_query["metadata_facts"]["instrument_fx_rate"])
        )

        over_cap = _client_command(strategy1, "submit", "eurusd-over-cap", "1", "20")
        assert over_cap[:3] == ("submit_error", "ADMISSION_DENIED", "REJECTED"), over_cap
        after_over_cap = _client_command(host1, "snapshot")
        assert after_over_cap[1] == (client_order_id,)
        assert len(_risk_rows(after_over_cap)) == 1
        assert _risk_total(after_over_cap) == Decimal("150")
        pre_restart_counters = after_over_cap[2]
        _close_normally(strategy1, expected_tag="closed")
        _close_normally(host1, expected_tag="closed")

        host2 = start_host("risk-fx-host-2-expired", fx_stale, (), False)
        after_restart = _client_command(host2, "snapshot")
        assert after_restart[1] == (client_order_id,)
        assert after_restart[2] == pre_restart_counters
        assert _risk_rows(after_restart)[risk_intent_id] == partial_row
        assert _risk_total(after_restart) == Decimal("150")
        strategy2, run2, session2 = start_strategy(
            host2, "risk-fx-strategy-2", logical_run_id=run1
        )
        assert run2 == run1
        assert session2 != session1
        duplicate_after_restart = _client_command(strategy2, "submit", intent_id, "2", "20")
        assert duplicate_after_restart[0] == "submitted", duplicate_after_restart
        assert duplicate_after_restart[1]["state"] == "PARTIALLY_FILLED"
        command2 = _client_command(host2, "command", intent_id)
        assert command2[3] == client_order_id and command2[4] == risk_intent_id
        assert command2[5]["run_id"] == run1
        duplicate_snapshot = _client_command(host2, "snapshot")
        assert duplicate_snapshot[1] == (client_order_id,)
        assert duplicate_snapshot[2] == pre_restart_counters
        assert len(_risk_rows(duplicate_snapshot)) == 1
        assert _risk_total(duplicate_snapshot) == Decimal("150")

        stale_submit = _client_command(strategy2, "submit", "eurusd-expired-new-order", "1", "20")
        assert stale_submit[:3] == ("submit_error", "ADMISSION_DENIED", "REJECTED"), stale_submit
        stale_denial_snapshot = _client_command(host2, "snapshot")
        assert stale_denial_snapshot[1] == (client_order_id,)
        assert len(_risk_rows(stale_denial_snapshot)) == 1
        assert _risk_total(stale_denial_snapshot) == Decimal("150")

        stale_query = _client_command(strategy2, "query", intent_id)
        assert stale_query[0] == "queried" and stale_query[1]["state"] == "UNKNOWN", stale_query
        stale_snapshot = _client_command(host2, "snapshot")
        assert stale_snapshot[1] == (client_order_id,)
        assert stale_snapshot[2]["order_queries"] == stale_denial_snapshot[2]["order_queries"] + 1
        assert stale_snapshot[2]["fill_queries"] == stale_denial_snapshot[2]["fill_queries"] + 1
        assert _risk_rows(stale_snapshot)[risk_intent_id]["state"] == "PARTIALLY_FILLED"
        assert _risk_total(stale_snapshot) == Decimal("150")
        stale_events = _client_command(host2, "events", intent_id)[1]
        assert stale_events[-1][0] == "UNKNOWN"
        assert stale_events[-1][2]["public_state"] == "UNKNOWN"
        assert sum(kind == "EXPOSURE_APPLIED" for kind, _rev, _payload in stale_events) == 1
        stale_counters = stale_snapshot[2]
        _close_normally(strategy2, expected_tag="closed")
        _close_normally(host2, expected_tag="closed")

        final_fills = [first_fill]
        v2._store_provider_observation(
            paths["provider"],
            client_order_id,
            status="cancelled",
            filled="0.5",
            order_revision=3,
            fills=final_fills,
        )
        host3 = start_host("risk-fx-host-3-fresh", fx2, (), False)
        before_fresh_query = _client_command(host3, "snapshot")
        assert before_fresh_query[1] == (client_order_id,)
        assert before_fresh_query[2] == stale_counters
        assert _risk_rows(before_fresh_query)[risk_intent_id]["state"] == "PARTIALLY_FILLED"
        assert _risk_total(before_fresh_query) == Decimal("150")
        strategy3, run3, session3 = start_strategy(
            host3, "risk-fx-strategy-3", logical_run_id=run1
        )
        assert run3 == run1
        assert len({session1, session2, session3}) == 3
        duplicate_unknown = _client_command(strategy3, "submit", intent_id, "2", "20")
        assert duplicate_unknown[0] == "submitted", duplicate_unknown
        assert duplicate_unknown[1]["state"] == "UNKNOWN"
        after_duplicate_unknown = _client_command(host3, "snapshot")
        assert after_duplicate_unknown[2] == stale_counters
        assert len(_risk_rows(after_duplicate_unknown)) == 1
        assert _risk_total(after_duplicate_unknown) == Decimal("150")

        cancelled = _client_command(strategy3, "query", intent_id)
        assert cancelled[0] == "queried" and cancelled[1]["state"] == "CANCELLED", cancelled
        cancelled_snapshot = _client_command(host3, "snapshot")
        cancelled_row = _risk_rows(cancelled_snapshot)[risk_intent_id]
        assert Decimal(cancelled_row["cumulative_filled_quantity"]) == Decimal("0.5")
        assert Decimal(cancelled_row["leaves_quantity"]) == Decimal("0")
        assert Decimal(cancelled_row["filled_gross_notional"]) == Decimal("39")
        assert Decimal(cancelled_row["open_gross_notional"]) == Decimal("0")
        assert cancelled_row["state"] == "CANCELLED"
        assert _risk_total(cancelled_snapshot) == Decimal("39")
        assert cancelled_snapshot[1] == (client_order_id,)
        assert cancelled_snapshot[2]["order_queries"] == stale_counters["order_queries"] + 1
        assert cancelled_snapshot[2]["fill_queries"] == stale_counters["fill_queries"] + 1
        latest_events = _client_command(host3, "events", intent_id)[1]
        latest_query = next(
            payload
            for event_type, _revision, payload in reversed(latest_events)
            if event_type == "PROVIDER_QUERY"
        )
        assert latest_query["source_values"]["order"]["price"] == "20.0"
        assert latest_query["metadata_facts"]["instrument_fx_rate"]["rate"] == "1.30"
        repeated_cancel = _client_command(strategy3, "query", intent_id)
        assert repeated_cancel[0] == "queried" and repeated_cancel[1]["state"] == "CANCELLED"
        after_repeated_cancel = _client_command(host3, "snapshot")
        assert _risk_total(after_repeated_cancel) == Decimal("39")
        assert len(_risk_rows(after_repeated_cancel)) == 1
        assert (
            after_repeated_cancel[2]["order_queries"] == cancelled_snapshot[2]["order_queries"] + 1
        )
        assert after_repeated_cancel[2]["fill_queries"] == cancelled_snapshot[2]["fill_queries"] + 1
        final_command = _client_command(host3, "command", intent_id)
        assert final_command[1] == "EXPOSURE_APPLIED"
        assert final_command[3] == client_order_id
        assert final_command[4] == risk_intent_id
        assert final_command[5]["run_id"] == run1
        _close_normally(strategy3, expected_tag="closed")
        _close_normally(host3, expected_tag="closed")
    finally:
        for record in reversed(strategy_records):
            _bounded_reap_spawned_process(record, forced, failures)
        for record in reversed(host_records):
            _bounded_reap_spawned_process(record, forced, failures)
        _assert_no_fallback_cleanup(forced, failures)


def test_valuation_digest_canonicalizes_equivalent_provider_price_scales(tmp_path) -> None:
    """Exercise terminal and partial builders through the real Risk authority."""

    metadata_now = time.time_ns()
    metadata = InstrumentRiskMetadata(
        instrument=_INSTRUMENT,
        metadata_version=_METADATA_VERSION,
        as_of_ns=metadata_now - 1_000_000_000,
        expires_at_ns=metadata_now + 86_400_000_000_000,
        tick_size=Decimal("0.01"),
        quantity_step=Decimal("0.001"),
        contract_multiplier=_MULTIPLIER,
        max_gross_notional=Decimal("1000"),
        taker_fee_bps=Decimal("0"),
        fixed_fee=Decimal("0"),
        max_slippage_bps=Decimal("0"),
        quantity_unit="ETH",
        valuation_unit=_VALUATION,
        quote_currency=_QUOTE,
        fx_source_ref=_FX_SOURCE,
    )
    fx_rate = InstrumentRiskFxRate(
        **_fx_fact(
            "1.25",
            "eurusd-scale-equivalence-v1",
            metadata_now - 100_000_000,
            metadata_now + 86_400_000_000_000,
        )
    )
    registry = InstrumentRiskRegistry((metadata,))
    observed_digests: dict[str, dict[str, str]] = {"terminal": {}, "partial": {}}

    for index, source_price in enumerate(("20", "20.0", "20.00")):
        case_dir = tmp_path / f"scale-{index}"
        case_dir.mkdir()
        provider_path = str(case_dir / "provider.sqlite3")
        provider = _ScaledOrderPriceFxFeed(provider_path, ("filled", "open"), source_price)
        bt_api = BtApi(debug=False)
        bt_api.exchange_feeds[base._EXCHANGE] = provider
        journal = RiskOrderJournal(
            case_dir / "journal.sqlite3", writer_id=f"risk-order-fx-scale-{index}"
        )
        gate = DurableRiskGate(
            case_dir / "risk.sqlite3",
            RiskPolicy(
                "risk-order-fx-scale-policy-v1",
                Decimal("1000"),
                max_increase_count=10,
                require_strategy_allocation=True,
                notional_unit=_VALUATION,
                exposure_accounting_mode=ExposureAccountingMode.LIVE_GROSS,
                max_gross_occupancy_notional=Decimal("500"),
                max_gross_occupancy_intent_count=10,
                max_exposure_facts_age_seconds=3600,
            ),
            execution_journal_authority=journal,
            gross_exposure_authority=journal,
        )
        try:
            gate.set_strategy_allocation(
                _ACCOUNT_SCOPE,
                base._STRATEGY,
                "risk-order-fx-scale-allocation-v1",
                max_notional=Decimal("1000"),
                notional_unit=_VALUATION,
                max_gross_occupancy_notional=Decimal("500"),
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
                        "notional_unit": _VALUATION,
                        "observed_at_ns": baseline_time,
                        "open_orders": [],
                        "positions": [
                            {
                                "active_increase_intent_count": 0,
                                "gross_notional": "0",
                                "notional_unit": _VALUATION,
                                "source_record_sha256": "1" * 64,
                                "strategy_id": base._STRATEGY,
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
                allocation_reader=lambda execution_scope, risk_gate=gate: (
                    risk_gate.get_strategy_allocation(_ACCOUNT_SCOPE, execution_scope.strategy_id)
                ),
                fx_rates_by_instrument=(fx_rate,),
            )
            admission = DurableRiskAdmissionAdapter(gate, mapper)
            contract = RiskBtApiProviderContract(
                exchange_name=base._EXCHANGE,
                profile_id="risk-order-fx-scale-profile-v2",
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
                quote_currency_by_instrument=((_INSTRUMENT, _QUOTE),),
            )
            service = RiskOrderEntryService(
                bt_api,
                {
                    base._SCOPE: RiskOrderTarget(
                        account_scope=_ACCOUNT_SCOPE,
                        strategy_id=base._STRATEGY,
                        exchange_name=base._EXCHANGE,
                        provider_account_id=base._PROVIDER_ACCOUNT,
                        registry=registry,
                        fx_rates_by_instrument=(fx_rate,),
                    )
                },
                {base._SCOPE: admission},
                journal,
                gate,
                {base._SCOPE: contract},
            )

            terminal_proofs = []
            partial_proofs = []
            resolve_terminal = gate.resolve_dispatch_exposure
            apply_observation = gate.apply_order_exposure_observation

            def capture_terminal(proof, proofs=terminal_proofs, resolver=resolve_terminal):
                proofs.append(proof.observation)
                return resolver(proof)

            def capture_observation(proof, proofs=partial_proofs, apply=apply_observation):
                proofs.append(proof)
                return apply(proof)

            gate.resolve_dispatch_exposure = capture_terminal
            gate.apply_order_exposure_observation = capture_observation
            identity = WireIdentity(WireRole.STRATEGY_CLIENT, base._PRINCIPAL, base._STRATEGY)

            def call(
                operation: str,
                intent_id: str,
                arguments: Mapping[str, Any],
                *,
                order_service=service,
                wire_identity=identity,
            ):
                frozen = dict(arguments)
                if "tags" in frozen:
                    frozen["tags"] = MappingProxyType(dict(frozen["tags"]))
                return order_service.handle(
                    operation,
                    principal_ref=base._PRINCIPAL,
                    identity=wire_identity,
                    scope=base._SCOPE,
                    arguments=MappingProxyType(frozen),
                )

            def submit(intent_id: str):
                return call(
                    "orders.submit",
                    intent_id,
                    base._submit_arguments(
                        intent_id=intent_id,
                        signal_id="signal-" + intent_id,
                        instrument=_INSTRUMENT,
                        side="BUY",
                        position_effect="OPEN",
                        order_type="LIMIT",
                        quantity="2",
                        price="20",
                        time_in_force="GTC",
                        reduce_only=False,
                        metadata_version=_METADATA_VERSION,
                        deadline_ns=None,
                        tags={},
                    ),
                )

            def record_state(reply: Mapping[str, Any]) -> str:
                assert reply["ok"] is True, reply
                return reply["result"]["record"]["state"]

            terminal_id = "fx-scale-terminal"
            terminal = submit(terminal_id)
            assert record_state(terminal) == "FILLED", terminal

            partial_id = "fx-scale-partial"
            opened = submit(partial_id)
            assert record_state(opened) == "ACKED_TRACKED", opened
            command = journal.get_command(_ACCOUNT_SCOPE, base._PRINCIPAL, partial_id)
            assert command is not None and command.client_order_id is not None
            first_fill = _FxSyntheticBtApiFeed._fill(
                account_id=base._PROVIDER_ACCOUNT,
                client_order_id=command.client_order_id,
                order_id="provider-" + command.client_order_id,
                symbol=_INSTRUMENT,
                side="buy",
                trade_id="fill-" + command.client_order_id + "-1",
                quantity="0.5",
                price="20",
                revision=1,
                fee="0",
            )
            v2._store_provider_observation(
                provider_path,
                command.client_order_id,
                status="partial",
                filled="0.5",
                order_revision=2,
                fills=[first_fill],
            )
            partial = call("orders.query", partial_id, {"intent_id": partial_id})
            assert record_state(partial) == "PARTIALLY_FILLED", partial
            assert len(terminal_proofs) == len(partial_proofs) == 1

            events: dict[str, dict[str, Any]] = {}
            observation_specs = (
                (terminal_id, "ORDER_OBSERVATION", "FILLED", "query_order", _ORDER_SCHEMA),
                (
                    partial_id,
                    "PROVIDER_QUERY",
                    "PARTIALLY_FILLED",
                    "orders.query",
                    {"order": _ORDER_SCHEMA, "fills": _FILL_SCHEMA},
                ),
            )
            for intent_id, event_type, exposure_state, operation, schema in observation_specs:
                current = journal.get_command(_ACCOUNT_SCOPE, base._PRINCIPAL, intent_id)
                assert current is not None
                observation_events = [
                    journal.get_event(_ACCOUNT_SCOPE, base._PRINCIPAL, intent_id, revision)
                    for revision in range(current.created_revision, current.latest_revision + 1)
                ]
                observation_event = next(
                    event
                    for event in observation_events
                    if event is not None
                    and event.event_type == event_type
                    and event.payload.get("exposure_state") == exposure_state
                )
                payload = dict(observation_event.payload)
                assert payload["operation"] == operation
                assert payload["schema"] == schema
                source_values = payload["source_values"]
                order_source_values = (
                    source_values["order"] if event_type == "PROVIDER_QUERY" else source_values
                )
                assert order_source_values["price"] == source_price
                events[intent_id] = payload

            snapshots = gate.gross_exposure_snapshot(_ACCOUNT_SCOPE)
            rows = {item.intent_id: item for item in snapshots["orders"]}
            terminal_command = journal.get_command(_ACCOUNT_SCOPE, base._PRINCIPAL, terminal_id)
            assert terminal_command is not None
            terminal_row = rows[terminal_command.risk_intent_id]
            partial_row = rows[command.risk_intent_id]
            assert terminal_row.filled_gross_notional == Decimal("150")
            assert terminal_row.open_gross_notional == 0
            assert partial_row.cumulative_filled_quantity == Decimal("0.5")
            assert partial_row.leaves_quantity == Decimal("1.5")
            assert partial_row.filled_gross_notional == Decimal("37.5")
            assert partial_row.open_gross_notional == Decimal("112.5")
            assert sum(
                (item.filled_gross_notional + item.open_gross_notional for item in rows.values()),
                Decimal(0),
            ) == Decimal("300")
            assert provider.snapshot()[1] == {
                "fill_queries": 3,
                "order_queries": 3,
            }
            assert len(provider.snapshot()[0]) == 2

            valuation_metadata = events[partial_id]["metadata_facts"]["instrument_metadata"]
            expected_partial = _valuation_facts_sha256(
                metadata=valuation_metadata,
                quote_currency=_QUOTE,
                valuation_unit=_VALUATION,
                fill_facts=partial_proofs[0].fills,
                leaves_valuation_price=Decimal(source_price),
                fx_rate=fx_rate,
            )
            assert partial_proofs[0].valuation_facts_sha256 == expected_partial
            assert terminal_proofs[0].valuation_facts_sha256 == _valuation_facts_sha256(
                metadata=events[terminal_id]["metadata_facts"]["instrument_metadata"],
                quote_currency=_QUOTE,
                valuation_unit=_VALUATION,
                fill_facts=terminal_proofs[0].fills,
                leaves_valuation_price=Decimal(source_price),
                fx_rate=fx_rate,
            )
            observed_digests["terminal"][source_price] = terminal_proofs[0].valuation_facts_sha256
            observed_digests["partial"][source_price] = partial_proofs[0].valuation_facts_sha256
        finally:
            gate.close()
            bt_api.close()

    assert set(observed_digests["terminal"]) == {"20", "20.0", "20.00"}
    assert set(observed_digests["partial"]) == {"20", "20.0", "20.00"}
    assert len(set(observed_digests["terminal"].values())) == 1
    assert len(set(observed_digests["partial"].values())) == 1
