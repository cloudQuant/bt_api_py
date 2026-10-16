"""Synthetic ServiceHost coverage for the trusted full-position Risk path."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
import hashlib
import json
import socket
import threading
import time
from decimal import Decimal
from pathlib import Path

import pytest
import zmq
from bt_api_base.account_protocol import (
    AccountKey,
    AccountObservation,
    AccountPosition,
    AccountResourceKey,
)
from bt_api_base.market_protocol import MarketInstrumentKey
from bt_api_base.service_protocol import WireIdentity, WireRole, WireScope
from bt_api_gateway import GatewayEventJournal
from bt_api_gateway.account_position_snapshot import (
    GatewayFullPositionSnapshot,
    position_rows_sha256,
    resource_coverage_sha256,
)
from bt_api_gateway.account_session_worker import GatewayAccountSessionWorker
from bt_api_gateway.session_guard import LocalAccountSessionGuard
from bt_api_risk import (
    AccountScope,
    DurableRiskAdmissionAdapter,
    DurableRiskGate,
    ExposureAccountingMode,
    InstrumentRiskAdmissionMapper,
    InstrumentRiskFxRate,
    InstrumentRiskMetadata,
    InstrumentRiskRegistry,
    RiskExposureKind,
    RiskExposureLimit,
    RiskPolicy,
)

from bt_api_py import service_components
from bt_api_py.bt_api import BtApi
from bt_api_py.service_components import ComponentResources
from bt_api_py.service_config import parse_service_config
from bt_api_py.service_host import ServiceHost, TrustedServiceAuthority
from bt_api_py.service_registry import PrincipalGrant, ResourceGrant
from bt_api_py.service_risk_order_entry import (
    RiskBtApiProviderContract,
    RiskOrderEntryService,
    RiskOrderTarget,
)
from bt_api_py.service_risk_order_journal import RiskOrderJournal, canonical_json
from tests.unit import test_service_risk_position_snapshot_baseline as baseline


_SERVICE_ID = "dim03-position-risk-service"
_SERVICE_INSTANCE = "dim03-position-risk-owner"
_GATEWAY_INSTANCE = "dim03-position-gateway-session"
_SERVICE_PRINCIPAL = "dim03-position-risk-principal"
_CLIENT_PRINCIPAL = "dim03-position-strategy-principal"
_DOMAIN = "paper:dim03-position-risk"
_SERVICE_SCOPE_REF = "dim03-position-service-scope-v1"
_SERVER_SECURITY_REF = "dim03-position-server-curve-v1"
_LISTENER_REF = "dim03-position-listener-v1"
_POLICY_ID = "position-snapshot-policy-v1"
_ACCOUNT_SESSION_REF = "dim03-position-session-v1"
_QUERY_SCOPE_REF = "dim03-position-wire-scope-v1"
_WIRE_SCOPE = WireScope(
    _DOMAIN,
    account_ref=baseline._SCOPE.account_id,
    strategy_ref=baseline._STRATEGY,
)
_SCOPE = baseline._SCOPE
_STRATEGY = baseline._STRATEGY
_INSTRUMENT = baseline._INSTRUMENT


def _endpoint() -> str:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as probe:
        probe.bind(("127.0.0.1", 0))
        return f"tcp://127.0.0.1:{probe.getsockname()[1]}"


def _config(endpoint: str):
    return parse_service_config(
        {
            "service_config_schema_version": 1,
            "process_role": "service",
            "service_id": _SERVICE_ID,
            "scope_ref": _SERVICE_SCOPE_REF,
            "transport_ref": _LISTENER_REF,
            "registration_policy_ref": "dim03-position-registration-v1",
            "service_requirements_ref": "dim03-position-requirements-v1",
            "components": {
                "gateway": {
                    "config_schema_version": 2,
                    "settings": {
                        "instance_id": _GATEWAY_INSTANCE,
                        "bind_ports": ["service.provider_session"],
                        "options": {
                            "role": "account_session_worker",
                            "account_session_ref": _ACCOUNT_SESSION_REF,
                        },
                    },
                },
                "risk": {
                    "config_schema_version": 2,
                    "settings": {
                        "instance_id": _SERVICE_INSTANCE,
                        "bind_ports": [
                            "service.order_entry",
                            "service.order_query",
                            "service.provider_session",
                        ],
                        "options": {
                            "role": "account_owner",
                            "account_policy_ref": _POLICY_ID,
                            "provider_session_ref": _GATEWAY_INSTANCE,
                        },
                    },
                },
            },
            "communication": {
                "config_schema_version": 1,
                "profiles": {
                    _LISTENER_REF: {
                        "backend": "zmq",
                        "config_schema_version": 1,
                        "endpoints": {"rpc_bind": endpoint},
                        "security_profile_ref": _SERVER_SECURITY_REF,
                        "options": {
                            "high_water_mark": 16,
                            "max_message_bytes": 65_536,
                        },
                    }
                },
            },
        }
    )


def _utc_text(value: datetime) -> str:
    return value.astimezone(timezone.utc).isoformat(timespec="microseconds").replace(
        "+00:00", "Z"
    )


class _PositionProvider:
    def __init__(self, fact, *, filtered: bool = False) -> None:
        self.fact = fact
        self.filtered = filtered
        self.connected = False
        self.closed = False
        self.reads = 0

    def connect(self, *, account_key, generation, timeout) -> None:
        assert account_key.account_key_id
        assert generation
        assert timeout > 0
        self.connected = True

    def read_account(self, *, generation, timeout):
        assert self.connected and not self.closed
        assert generation and timeout > 0
        self.reads += 1
        now = datetime.now(timezone.utc)
        observed = _utc_text(now)
        account_key = AccountKey(
            provider=_SCOPE.provider,
            venue="fixture-venue",
            environment=_SCOPE.environment,
            account_id=_SCOPE.account_id,
        )
        resource = AccountResourceKey("spot", "cross")
        position = AccountPosition(
            resource=resource,
            instrument=MarketInstrumentKey(
                provider=account_key.provider,
                venue=account_key.venue,
                environment=account_key.environment,
                product_type=resource.product_type,
                instrument_id=_INSTRUMENT,
                native_symbol=_INSTRUMENT,
            ),
            side="long",
            quantity="20",
            qty_unit="CONTRACT",
            average_price="1",
            price_currency="EUR",
            observed_at_utc=observed,
        )
        observation = AccountObservation(
            account_key=account_key,
            observation_id=f"dim03-full-position-{self.reads}",
            coverage=(resource,),
            balances=(),
            positions=(position,),
            valuation=None,
            started_at_utc=observed,
            completed_at_utc=observed,
            consistency="provider_atomic",
            balances_complete=True,
            positions_complete=True,
            open_orders=(),
            open_orders_complete=True,
        )
        snapshot = GatewayFullPositionSnapshot(
            observation=observation,
            account_key_id=account_key.account_key_id,
            session_generation=generation,
            query_scope="ALL_ACCOUNT_POSITIONS",
            filters=(f"instrument={_INSTRUMENT}",) if self.filtered else (),
            permission_scope="ACCOUNT_FULL",
            permission_revision="fixture-account-full-acl-v1",
            pagination_exhausted=True,
            page_count=3,
            account_revision=self.reads,
            source_schema_version="fixture-position-source-v1",
            snapshot_version=f"fixture-position-revision-{self.reads}",
            positions_sha256=position_rows_sha256(observation),
            resource_coverage_sha256=resource_coverage_sha256(observation),
            valid_until_utc=_utc_text(now + timedelta(hours=1)),
        )
        return snapshot

    def close(self) -> None:
        self.closed = True


class _NoDispatchFeed:
    def make_order(self, *_args, **_kwargs):
        raise AssertionError("startup or baseline maintenance must not dispatch")

    def query_order(self, *_args, **_kwargs):
        raise AssertionError("startup or baseline maintenance must not query orders")

    def get_deals(self, *_args, **_kwargs):
        raise AssertionError("startup or baseline maintenance must not query fills")

    def close(self) -> None:
        return None


def _risk_policy():
    limits = (
        RiskExposureLimit(
            RiskExposureKind.UNDERLYING_NOTIONAL,
            "EUR",
            "USD",
            Decimal("1000"),
        ),
    )
    return RiskPolicy(
        _POLICY_ID,
        Decimal("1000"),
        max_increase_count=10,
        permit_ttl_seconds=30.0,
        require_strategy_allocation=True,
        notional_unit="USD",
        exposure_accounting_mode=ExposureAccountingMode.LIVE_GROSS,
        max_gross_occupancy_notional=Decimal("1000"),
        max_gross_occupancy_intent_count=10,
        max_exposure_facts_age_seconds=300,
        exposure_dimension_limits=limits,
    )


def _provider_contract() -> RiskBtApiProviderContract:
    return RiskBtApiProviderContract(
        exchange_name="fixture",
        profile_id="dim03-position-provider-v1",
        order_schema="dim03-position-order-v1",
        fill_schema="dim03-position-fill-v1",
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
        quote_currency_by_instrument=((_INSTRUMENT, "EUR"),),
    )


def _service_host(tmp_path: Path, monkeypatch, *, filtered: bool = False):
    now_ns = time.time_ns()
    fact = baseline._source_facts(now_ns)
    scope = _SCOPE
    resource = AccountResourceKey("spot", "cross")
    account_key = AccountKey(
        provider=scope.provider,
        venue="fixture-venue",
        environment=scope.environment,
        account_id=scope.account_id,
    )
    gateway_journal = GatewayEventJournal(tmp_path / "gateway-events.sqlite3")
    provider = _PositionProvider(fact, filtered=filtered)
    guard_dir = tmp_path / "account-session-guard"
    guard_dir.mkdir()
    worker = GatewayAccountSessionWorker(
        provider,
        gateway_journal,
        account_key=account_key,
        journal_account_scope="dim03-position-account",
        state_scope="dim03-position-state",
        guard=LocalAccountSessionGuard(
            guard_dir, account_key.account_key_id, "dim03-position-owner"
        ),
        required_coverage=(resource,),
        require_open_orders=True,
        max_staleness_seconds=30.0,
    )

    policy = _risk_policy()
    risk_journal = RiskOrderJournal(
        tmp_path / "risk-order-journal.sqlite3", writer_id="dim03-position-risk-owner"
    )
    gate = DurableRiskGate(
        tmp_path / "risk-gate.sqlite3",
        policy,
        execution_journal_authority=risk_journal,
        gross_exposure_authority=risk_journal,
    )
    gate.set_strategy_allocation(
        scope,
        _STRATEGY,
        "allocation-v1",
        max_notional=Decimal("1000"),
        notional_unit="USD",
        max_gross_occupancy_notional=Decimal("1000"),
        max_gross_occupancy_intent_count=10,
    )
    registry = InstrumentRiskRegistry((fact.metadata,))
    fx = fact.fx_rate
    assert fx is not None
    mapper = InstrumentRiskAdmissionMapper(
        scope,
        registry,
        clock_ns=time.time_ns,
        allocation_reader=lambda requested: gate.get_strategy_allocation(
            scope, requested.strategy_id
        ),
        fx_rates_by_instrument=(fx,),
        resource_by_instrument={_INSTRUMENT: resource},
        exposure_dimension_limits=policy.exposure_dimension_limits,
    )
    target = RiskOrderTarget(
        account_scope=scope,
        strategy_id=_STRATEGY,
        exchange_name="fixture",
        provider_account_id=scope.account_id,
        registry=registry,
        fx_rates_by_instrument=(fx,),
        resource_by_instrument={_INSTRUMENT: resource},
    )
    try:
        from bt_api_py import bt_api as bt_api_module

        monkeypatch.setattr(bt_api_module, "_ensure_plugins_loaded", lambda: None)
    except Exception:
        gate.close()
        worker.close()
        raise
    bt_api = BtApi(debug=False)
    feed = _NoDispatchFeed()
    bt_api.exchange_feeds["fixture"] = feed

    wire_target = {
        _WIRE_SCOPE: target,
    }
    admission = {
        _WIRE_SCOPE: DurableRiskAdmissionAdapter(gate, mapper),
    }
    contracts = {_WIRE_SCOPE: _provider_contract()}

    valid_until = now_ns + 3_600_000_000_000

    def resolve_valuation(position, receipt):
        del position
        observed_at_ns = receipt.coverage.observed_at_ns
        fx_payload = {
            "instrument": fact.metadata.instrument,
            "observed_at_ns": observed_at_ns,
            "quote_currency": "EUR",
            "rate": "1.5",
            "source_ref": "eurusd-feed",
            "source_revision": "fx-revision-1",
            "valid_until_ns": min(valid_until, receipt.valid_until_ns),
            "valuation_unit": "USD",
        }
        fx_source = InstrumentRiskFxRate(
            instrument=fact.metadata.instrument,
            quote_currency="EUR",
            valuation_unit="USD",
            rate=Decimal("1.5"),
            observed_at_ns=observed_at_ns,
            valid_until_ns=min(valid_until, receipt.valid_until_ns),
            source_ref="eurusd-feed",
            source_revision="fx-revision-1",
            source_record_sha256=hashlib.sha256(
                canonical_json(fx_payload).encode("utf-8")
            ).hexdigest(),
        )
        return baseline.RiskPositionValuation(
            strategy_id=_STRATEGY,
            metadata=fact.metadata,
            mark_price=Decimal("1"),
            mark_observed_at_ns=observed_at_ns,
            mark_valid_until_ns=min(valid_until, receipt.valid_until_ns),
            mark_source_ref="position-mark-feed",
            mark_source_revision="mark-revision-1",
            mark_source_record_sha256=hashlib.sha256(
                f"mark:{receipt.coverage.account_revision}".encode("ascii")
            ).hexdigest(),
            fx_rate=fx_source,
            greeks=fact.greeks,
        )

    valuation_resolver = baseline.RiskPositionValuationResolver(
        expected_account_key=account_key,
        expected_instruments_by_instrument={
            _INSTRUMENT: MarketInstrumentKey(
                provider=account_key.provider,
                venue=account_key.venue,
                environment=account_key.environment,
                product_type=resource.product_type,
                instrument_id=_INSTRUMENT,
                native_symbol=_INSTRUMENT,
            )
        },
        resolve=resolve_valuation,
    )

    endpoint = _endpoint()
    config = _config(endpoint)
    server_public, server_secret = zmq.curve_keypair()
    client_public, _client_secret = zmq.curve_keypair()
    client_identity = WireIdentity(WireRole.STRATEGY_CLIENT, _CLIENT_PRINCIPAL, _STRATEGY)
    grants = {
        _CLIENT_PRINCIPAL: (
            PrincipalGrant(
                client_identity,
                (
                    ResourceGrant(
                        _SERVICE_INSTANCE,
                        "service.order_entry",
                        _QUERY_SCOPE_REF,
                        _WIRE_SCOPE,
                        frozenset({"orders.submit"}),
                    ),
                    ResourceGrant(
                        _SERVICE_INSTANCE,
                        "service.order_query",
                        _QUERY_SCOPE_REF,
                        _WIRE_SCOPE,
                        frozenset({"orders.query"}),
                    ),
                ),
            ),
        )
    }
    authority = TrustedServiceAuthority(
        identity=WireIdentity(WireRole.SERVICE, _SERVICE_PRINCIPAL, _SERVICE_ID),
        scope=WireScope(_DOMAIN),
        scope_ref=config.scope_ref,
        scope_version="dim03-position-scope-v1",
        security_ref=_SERVER_SECURITY_REF,
        security_version="dim03-position-curve-v1",
        registration_policy_ref=config.registration_policy_ref,
        registration_policy_version="dim03-position-registration-v1",
        service_requirements_ref=config.service_requirements_ref,
        service_requirements_version="dim03-position-requirements-v1",
        curve_public_key=server_public,
        curve_secret_key=server_secret,
        principals={client_public: _CLIENT_PRINCIPAL},
        grants=grants,
    )
    calls: list[tuple[str, str, int]] = []
    real_read_receipt = GatewayAccountSessionWorker.read_full_position_snapshot_receipt

    def recording_receipt(current_worker):
        calls.append(
            (
                current_worker.account_key.account_id,
                current_worker.state,
                threading.get_ident(),
            )
        )
        return real_read_receipt(current_worker)

    monkeypatch.setattr(
        GatewayAccountSessionWorker,
        "read_full_position_snapshot_receipt",
        recording_receipt,
    )

    resource_requests: list[str] = []

    def resource_provider(component, instance_id, _options, _scope):
        resource_requests.append(component + ":" + instance_id)
        if component == "gateway":
            return ComponentResources.owned(
                {},
                ready=lambda: worker.ready is True,
                internal_ports={"service.provider_session": worker},
                close=worker.close,
            )
        if component == "risk":
            if instance_id != _SERVICE_INSTANCE:
                raise AssertionError("unexpected Risk component instance")
            return ComponentResources.owned(
                {
                    "bt_api": bt_api,
                    "targets": wire_target,
                    "admission": admission,
                    "journal": risk_journal,
                    "gate": gate,
                    "provider_contracts": contracts,
                    "position_valuation_resolver": valuation_resolver,
                },
                ready=lambda: True,
                close=lambda: _close_risk_resources(bt_api, gate, risk_journal),
            )
        raise AssertionError("unexpected selected service component")

    host = ServiceHost(
        config,
        authority,
        tmp_path / "service-registry.sqlite3",
        resource_provider,
    )
    return host, worker, provider, gate, risk_journal, resource_requests, calls


def _close_risk_resources(bt_api, gate, journal) -> None:
    del journal
    bt_api.close()
    gate.close()


def test_normal_host_installs_and_maintains_gateway_full_position_baseline(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    host, worker, provider, gate, journal, requests, receipt_calls = _service_host(
        tmp_path, monkeypatch
    )
    try:
        host.start()
        assert host.state == "started"
        assert host.healthy is True
        assert host.endpoint is not None
        assert requests == [
            "gateway:" + _GATEWAY_INSTANCE,
            "risk:" + _SERVICE_INSTANCE,
        ]
        assert worker.ready is True
        assert worker.state == "ready"
        assert provider.reads == 1
        assert gate.gross_exposure_snapshot(_SCOPE)["baseline_revision"] == 1

        with journal._connection() as connection:
            rows = connection.execute(
                "SELECT account_revision, source_snapshot_json "
                "FROM risk_order_gross_baselines ORDER BY account_revision"
            ).fetchall()
        assert len(rows) == 1
        assert rows[0]["account_revision"] == 1
        stored = json.loads(rows[0]["source_snapshot_json"])
        assert stored["complete_positions"] is True
        assert stored["position_coverage"]["query_scope"] == "ALL_ACCOUNT_POSITIONS"
        assert stored["position_coverage"]["filters"] == []
        assert stored["position_coverage"]["pagination_exhausted"] is True
        assert Decimal(stored["positions"][0]["gross_notional"]) == Decimal("30")
        assert stored["valuation_facts"][0]["fx_rate"]["rate"] == "1.5"

        # The regular Host owner-thread maintenance hook verifies the same
        # provider-owned snapshot without duplicating a source revision.
        assert host.serve_once(timeout_ms=0) is False
        assert host.healthy is True
        assert gate.gross_exposure_snapshot(_SCOPE)["baseline_revision"] == 1
        assert len(receipt_calls) == 3
        assert all(call[0] == _SCOPE.account_id and call[1] == "ready" for call in receipt_calls)
        assert receipt_calls[0][2] == receipt_calls[1][2] == threading.get_ident()
        assert provider.reads == 1
        with journal._connection() as connection:
            assert connection.execute(
                "SELECT COUNT(*) FROM risk_order_gross_baselines"
            ).fetchone()[0] == 1
    finally:
        if host.state != "closed":
            host.close()
    assert worker.state == "closed"
    assert provider.closed is True


def test_filtered_gateway_snapshot_fails_before_risk_or_listener_startup(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    host, worker, provider, gate, journal, requests, receipt_calls = _service_host(
        tmp_path, monkeypatch, filtered=True
    )
    try:
        with pytest.raises(Exception):
            host.start()
        assert host.state == "failed"
        assert host.endpoint is None
        assert host._registry is None
        assert not (tmp_path / "service-registry.sqlite3").exists()
        assert requests == ["gateway:" + _GATEWAY_INSTANCE]
        assert provider.reads == 1
        assert receipt_calls == []
        assert gate.gross_exposure_snapshot(_SCOPE)["baseline_revision"] is None
        with journal._connection() as connection:
            assert connection.execute(
                "SELECT COUNT(*) FROM risk_order_gross_baselines"
            ).fetchone()[0] == 0
    finally:
        if host.state != "closed":
            host.close()
    assert worker.state == "closed"
    assert provider.closed is True
