"""A sealed CTP instrument read through the normal Gateway ServiceHost path.

Only the terminal CTP native API leaves are synthetic. The test retains the
real Trader/Md clients, callbacks, metadata reader, Gateway account worker,
quota wrapper, ServiceHost, CURVE/ZMQ runtime and SDK account facade.
"""

from __future__ import annotations

import queue
import socket
import sqlite3
import threading
import time
from contextlib import suppress
from datetime import UTC, datetime
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest
import zmq
from bt_api_base.account_protocol import (
    AccountBalance,
    AccountKey,
    AccountObservation,
    AccountResourceKey,
    AccountValuation,
)
from bt_api_base.service_protocol import WireIdentity, WireRole, WireScope
from bt_api_gateway.event_journal import GatewayEventJournal
from bt_api_gateway.quota_admission import GatewayQuotaAdmission, GatewayQuotaOperation
from bt_api_gateway.router import GatewayCommandKind, GatewayPrincipal
from bt_api_gateway.service_component import GatewayServiceAccountScope
from bt_api_gateway.session_guard import LocalAccountSessionGuard
from bt_api_ctp.ctp.client import MdClient, TraderClient, _TraderSpi
from bt_api_ctp.feeds.live_ctp_feed import CtpMarketStream, CtpRequestDataFuture
from bt_api_gateway.ctp_instrument_metadata import bind_ctp_instrument_metadata_provider

from bt_api_py.gateway_operator import GatewayAccountSessionBinding, GatewayOperatorDeployment
from bt_api_py.service_account import ServiceAccountClient
from bt_api_py.service_config import parse_client_config, parse_service_config
from bt_api_py.service_host import TrustedServiceAuthority
from bt_api_py.service_registry import PrincipalGrant, ResourceGrant
from bt_api_py.service_runtime import (
    ClientCurveKeys,
    ServiceClientRuntime,
    ZmqTransportFactory,
)
from bt_api_gateway.session_owner import GatewaySessionOwner
import bt_api_ctp.ctp.client as ctp_client


_DOMAIN = "paper:ctp-instrument-metadata"
_SERVICE_ID = "ctp-instrument-metadata-service"
_INSTANCE = "ctp-instrument-metadata-gateway"
_SCOPE_REF = "ctp-instrument-metadata-scope-v1"
_SECURITY_REF = "ctp-instrument-metadata-curve-v1"
_SERVER_TRANSPORT = "ctp-instrument-metadata-listener-v1"
_CLIENT_TRANSPORT = "ctp-instrument-metadata-client-v1"
_ACCOUNT_REF = "account:ctp-instrument-metadata"
_SESSION_REF = "session:ctp-instrument-metadata-v1"
_STRATEGY_REF = "strategy:ctp-instrument-metadata"
_PRINCIPAL_REF = "principal:ctp-instrument-metadata"
_SERVICE_PRINCIPAL = "principal:ctp-instrument-metadata-service"
_BROKER = "metadata-host-fixture-broker"
_INVESTOR = "metadata-host-fixture-investor"
_DAY = "20261016"
_INSTRUMENT = "IO2612-C-4000"
_EXCHANGE = "CFFEX"
_OPERATIONS = frozenset(
    {
        "account.ack",
        "account.configuration",
        "account.head",
        "account.instrument_metadata",
        "account.progress",
        "account.read_after",
        "account.server_time_probe",
        "account.snapshot",
    }
)
_RESOURCE = AccountResourceKey("spot", "cash")
_ACCOUNT_KEY = AccountKey("ctp-synthetic", "CFFEX", "paper", "account-metadata-fixture")


class _SyntheticLoginField:
    """Python stand-in for the native fixed-char login struct in this fake-leaf test."""

    BrokerID = ""
    UserID = ""
    Password = ""


def _free_endpoint() -> str:
    probe = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    try:
        probe.bind(("127.0.0.1", 0))
        return f"tcp://127.0.0.1:{probe.getsockname()[1]}"
    finally:
        probe.close()


def _service_config(endpoint: str) -> dict[str, Any]:
    return {
        "service_config_schema_version": 1,
        "process_role": "service",
        "service_id": _SERVICE_ID,
        "scope_ref": _SCOPE_REF,
        "transport_ref": _SERVER_TRANSPORT,
        "registration_policy_ref": "ctp-instrument-metadata-policy-v1",
        "service_requirements_ref": "ctp-instrument-metadata-requirements-v1",
        "components": {
            "gateway": {
                "config_schema_version": 2,
                "settings": {
                    "instance_id": _INSTANCE,
                    "bind_ports": ["service.account_read", "service.provider_session"],
                    "options": {
                        "role": "account_session_worker",
                        "account_session_ref": _SESSION_REF,
                    },
                },
            }
        },
        "communication": {
            "config_schema_version": 1,
            "profiles": {
                _SERVER_TRANSPORT: {
                    "backend": "zmq",
                    "config_schema_version": 1,
                    "endpoints": {"rpc_bind": endpoint},
                    "security_profile_ref": _SECURITY_REF,
                    "options": {"high_water_mark": 32, "max_message_bytes": 262_144},
                }
            },
        },
    }


def _client_config(endpoint: str) -> dict[str, Any]:
    return {
        "client_config_schema_version": 1,
        "process_role": "strategy_client",
        "registration": {"strategy_ref": _STRATEGY_REF},
        "clients": {
            "gateway": {
                "config_schema_version": 1,
                "settings": {
                    "bindings": [
                        {
                            "mount": "store.account",
                            "service_id": _SERVICE_ID,
                            "instance_id": _INSTANCE,
                            "scope_ref": _SCOPE_REF,
                            "account_ref": _ACCOUNT_REF,
                            "transport_ref": _CLIENT_TRANSPORT,
                        }
                    ],
                    "options": {"request_timeout_ms": 3_000, "heartbeat_interval_seconds": 5},
                },
            }
        },
        "communication": {
            "config_schema_version": 1,
            "profiles": {
                _CLIENT_TRANSPORT: {
                    "backend": "zmq",
                    "config_schema_version": 1,
                    "endpoints": {"rpc_connect": endpoint},
                    "security_profile_ref": _SECURITY_REF,
                    "options": {
                        "request_timeout_ms": 3_000,
                        "high_water_mark": 32,
                        "max_message_bytes": 262_144,
                    },
                }
            },
        },
    }


class _FakeMdApi:
    def __init__(self) -> None:
        self.login_calls: list[tuple[object, int]] = []
        self.release_calls = 0
        self.detach_calls = 0

    def ReqUserLogin(self, field, request_id: int) -> int:
        self.login_calls.append((field, request_id))
        return 0

    def RegisterSpi(self, spi) -> None:
        if spi is None:
            self.detach_calls += 1

    def Release(self) -> None:
        self.release_calls += 1


class _FakeTraderApi:
    def __init__(self, trader: TraderClient) -> None:
        self.trader = trader
        self.query_fields: list[tuple[str, str, str]] = []

    def ReqQryInstrument(self, field, request_id: int) -> int:
        self.query_fields.append((field.InstrumentID, field.ExchangeID, field.ProductID))
        self.trader._handle_query_callback(
            "instruments",
            SimpleNamespace(
                InstrumentID=field.InstrumentID,
                ExchangeID=field.ExchangeID,
                ProductClass="2",
                PriceTick=0.2,
                VolumeMultiple=100,
            ),
            None,
            request_id,
            True,
        )
        return 0


class _SyntheticCtpProvider:
    def __init__(self) -> None:
        self.trader = TraderClient(
            "tcp://offline.invalid:0",
            _BROKER,
            _INVESTOR,
            "",
            auto_settlement_confirm=False,
        )
        self.trader._connected = True
        self.trader._authentication_state = "authenticated"
        self.trader._login_state = "logging_in"
        self.trader._connection_generation = 7
        self.trader._req_id = 30
        self.trader._login_request_id = 30
        self.trader._login_connection_generation = 7
        self.trader._query_interval = 0.0
        _TraderSpi(self.trader).OnRspUserLogin(
            SimpleNamespace(
                BrokerID=self.trader._bound_broker_id,
                InvestorID=self.trader._bound_user_id,
                UserID=self.trader._bound_user_id,
                TradingDay=_DAY,
                FrontID=0,
                SessionID=0,
                MaxOrderRef="",
            ),
            SimpleNamespace(ErrorID=0, ErrorMsg=""),
            30,
            True,
        )
        self.trader_api = _FakeTraderApi(self.trader)
        self.trader._api = self.trader_api
        self.ctp_request_feed = CtpRequestDataFuture()
        self.ctp_request_feed._trader = self.trader
        self.ctp_market_stream = CtpMarketStream(
            md_front="tcp://offline.invalid:0",
            broker_id=_BROKER,
            user_id=_INVESTOR,
            password="",
            topics=(),
        )
        self.ctp_trader_client = self.trader
        self.ctp_account_key: AccountKey | None = None
        self.connect_calls = 0
        self.account_reads = 0
        self.close_calls = 0
        self.md_apis: list[_FakeMdApi] = []

    def connect(self, *, account_key: AccountKey, generation: str, timeout: float) -> None:
        assert type(generation) is str and generation
        assert timeout > 0
        self.connect_calls += 1
        self.ctp_account_key = account_key
        self.ctp_market_stream.connect()

    def read_account(self, *, generation: str, timeout: float) -> AccountObservation:
        assert generation
        assert timeout > 0
        self.account_reads += 1
        observed_at = datetime.now(UTC).isoformat(timespec="microseconds").replace("+00:00", "Z")
        balance = AccountBalance(
            resource=_RESOURCE,
            currency="CNY",
            total="100000",
            available="90000",
            locked="10000",
            observed_at_utc=observed_at,
        )
        return AccountObservation(
            account_key=_ACCOUNT_KEY,
            observation_id=f"ctp-metadata-account-{self.account_reads}",
            coverage=(_RESOURCE,),
            balances=(balance,),
            positions=(),
            valuation=AccountValuation(
                currency="CNY",
                available_cash="90000",
                equity="100000",
                observed_at_utc=observed_at,
            ),
            started_at_utc=observed_at,
            completed_at_utc=observed_at,
            consistency="provider_atomic",
            balances_complete=True,
            positions_complete=True,
        )

    def close(self) -> bool:
        self.close_calls += 1
        self.ctp_market_stream.disconnect()
        return True


@pytest.mark.integration
def test_instrument_metadata_uses_trusted_ctp_owner_through_curve_service_host(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(ctp_client, "CThostFtdcReqUserLoginField", _SyntheticLoginField)
    holder: dict[str, _SyntheticCtpProvider] = {}
    deployment_holder: dict[str, GatewayOperatorDeployment] = {}
    def start_fake_md_native_leaf(client: MdClient, *, block: bool = True) -> None:
        assert block is False
        api = _FakeMdApi()
        client._metadata_test_api = api
        trader_api_holder = holder.get("provider")
        if trader_api_holder is not None:
            trader_api_holder.md_apis.append(api)
        spi = client._create_md_spi(api)
        client._api = api
        client._spi = spi
        spi.OnFrontConnected()
        request_id = api.login_calls[-1][1]
        spi.OnRspUserLogin(
            SimpleNamespace(
                BrokerID=client._bound_broker_id,
                UserID=client._bound_user_id,
                TradingDay=_DAY,
            ),
            SimpleNamespace(ErrorID=0, ErrorMsg=""),
            request_id,
            True,
        )

    monkeypatch.setattr(MdClient, "start", start_fake_md_native_leaf)
    endpoint = _free_endpoint()
    service_config = parse_service_config(_service_config(endpoint))
    server_public, server_secret = zmq.curve_keypair()
    client_public, client_secret = zmq.curve_keypair()
    domain_scope = WireScope(_DOMAIN)
    # The facade reads canonical account state through the configured shared
    # account-facts scope. The service grant itself remains strategy scoped;
    # ServiceClientRuntime enriches this account binding with that trusted
    # strategy identity before registration and each RPC.
    resource_scope = WireScope(_DOMAIN, account_ref=_ACCOUNT_REF)
    service_identity = WireIdentity(WireRole.SERVICE, _SERVICE_PRINCIPAL, _SERVICE_ID)
    strategy_identity = WireIdentity(WireRole.STRATEGY_CLIENT, _PRINCIPAL_REF, _STRATEGY_REF)
    grant = ResourceGrant(
        _INSTANCE,
        "service.account_read",
        _SCOPE_REF,
        WireScope(_DOMAIN, account_ref=_ACCOUNT_REF, strategy_ref=_STRATEGY_REF),
        _OPERATIONS,
    )
    authority = TrustedServiceAuthority(
        identity=service_identity,
        scope=domain_scope,
        scope_ref=_SCOPE_REF,
        scope_version="ctp-instrument-metadata-scope-v1",
        security_ref=_SECURITY_REF,
        security_version="ctp-instrument-metadata-security-v1",
        registration_policy_ref=service_config.registration_policy_ref,
        registration_policy_version="ctp-instrument-metadata-policy-v1",
        service_requirements_ref=service_config.service_requirements_ref,
        service_requirements_version="ctp-instrument-metadata-requirements-v1",
        curve_public_key=server_public,
        curve_secret_key=server_secret,
        principals={client_public: _PRINCIPAL_REF},
        grants={_PRINCIPAL_REF: (PrincipalGrant(strategy_identity, (grant,)),)},
    )
    journal = GatewayEventJournal(tmp_path / "ctp-instrument-metadata-journal.sqlite3")
    account_scope = GatewayServiceAccountScope(
        _ACCOUNT_REF,
        _DOMAIN,
        "journal:ctp-instrument-metadata-account",
        account_read_strategy_scope="state:ctp-instrument-metadata-account",
        shared_account_facts_scope="state:ctp-instrument-metadata-account",
        shared_fact_strategy_refs=frozenset({_STRATEGY_REF}),
    )
    principal = GatewayPrincipal(
        _PRINCIPAL_REF,
        frozenset({"journal:ctp-instrument-metadata-account"}),
        frozenset({"state:ctp-instrument-metadata-account"}),
        frozenset({GatewayCommandKind.READ}),
    )
    quota = GatewayQuotaAdmission(
        tmp_path / "ctp-instrument-metadata-quota.sqlite3",
        "synthetic:ctp-instrument-metadata-quota",
        capacity_per_window=16,
        window_seconds=60.0,
        reserved_capacity=0,
        max_queue=16,
        max_queue_per_subject=8,
        max_backoff_seconds=1.0,
        max_queue_wait_seconds=5.0,
        poll_interval_seconds=0.005,
    )

    def provider_factory(_session_ref: str, _credential: object):
        provider = _SyntheticCtpProvider()
        holder["provider"] = provider
        return bind_ctp_instrument_metadata_provider(
            provider,
            request_feed=provider.ctp_request_feed,
            market_stream=provider.ctp_market_stream,
        )

    startup: queue.Queue[tuple[str, object]] = queue.Queue()
    stop = threading.Event()

    def serve_host() -> None:
        deployment: GatewayOperatorDeployment | None = None
        try:
            guard_dir = tmp_path / "ctp-instrument-metadata-guard"
            guard_dir.mkdir()
            binding = GatewayAccountSessionBinding(
                account_session_ref=_SESSION_REF,
                journal=journal,
                account_key=_ACCOUNT_KEY,
                account_scope=account_scope,
                state_scope="state:ctp-instrument-metadata-account",
                guard=LocalAccountSessionGuard(
                    guard_dir,
                    _ACCOUNT_KEY.account_key_id,
                    "ctp-instrument-metadata-owner",
                ),
                required_coverage=(_RESOURCE,),
                principals={_PRINCIPAL_REF: principal},
                refresh_interval_seconds=60.0,
                max_staleness_seconds=30.0,
                request_timeout_seconds=2.0,
                quota_admission=quota,
            )
            owner = GatewaySessionOwner(
                {_SESSION_REF: "credential:synthetic"},
                resolve_credential=lambda _ref: object(),
                provider_factory=provider_factory,
            )
            deployment = GatewayOperatorDeployment(
                service_config,
                authority,
                tmp_path / "ctp-instrument-metadata-registry.sqlite3",
                resource_provider=lambda *_args: pytest.fail(
                    "configured Gateway account role must use its trusted session owner"
                ),
                session_owner=owner,
                account_session=binding,
            )
            deployment_holder["deployment"] = deployment
            deployment.start()
            startup.put(("started", deployment.endpoint))
            while not stop.is_set():
                deployment.serve_once(10)
        except BaseException as exc:
            startup.put(("error", getattr(exc, "code", type(exc).__name__)))
        finally:
            if deployment is not None and deployment.state not in {"closed", "failed"}:
                with suppress(BaseException):
                    deployment.close()

    host_thread = threading.Thread(target=serve_host, name="ctp-metadata-service-owner", daemon=True)
    host_thread.start()
    runtime: ServiceClientRuntime | None = None
    account_client: ServiceAccountClient | None = None
    try:
        status, value = startup.get(timeout=10.0)
        assert status == "started", value
        assert value == endpoint
        client_config = parse_client_config(_client_config(endpoint))
        runtime = ServiceClientRuntime(
            client_config,
            identity=strategy_identity,
            scope_resolver=lambda _ref: resource_scope,
            peer_resolver=lambda _service_id: service_identity,
            transport_factory=ZmqTransportFactory(
                {_SECURITY_REF: ClientCurveKeys(server_public, client_public, client_secret)}
            ),
            requested_operations={"store.account": _OPERATIONS},
        )
        registration = runtime.start()
        assert registration.degraded_count == 0
        account_client = ServiceAccountClient(
            runtime,
            "store.account",
            account_ref=_ACCOUNT_REF,
            expected_account=_ACCOUNT_KEY,
            required_coverage=(_RESOURCE,),
            expected_scope_ref=_SCOPE_REF,
        )
        snapshot = account_client.refresh()
        assert snapshot.observation.balances[0].total == "100000"
        metadata = account_client.instrument_metadata(_INSTRUMENT, _EXCHANGE)
        assert metadata.instrument_id == _INSTRUMENT
        assert metadata.exchange_id == _EXCHANGE
        assert metadata.asset_type == "option"
        assert metadata.price_tick == "0.2"
        assert metadata.volume_multiple == 100
        assert metadata.price_limits_available is False
        assert metadata.lower_limit_price is None and metadata.upper_limit_price is None
        assert metadata.price_limits_source == "unavailable_in_instrument_query"
        assert metadata.account_session_generation == snapshot.session_generation
        assert metadata.quote_source == "ctp.native.md"
        assert metadata.clock_domain_id.startswith("ctp-md-process:")
        assert metadata.query_clock_domain_id == "python.time.monotonic"
        assert len(holder["provider"].trader_api.query_fields) == 1
        assert holder["provider"].trader_api.query_fields == [(_INSTRUMENT, _EXCHANGE, "")]
        assert quota.status()["grants_in_window"] >= 3
        with sqlite3.connect(tmp_path / "ctp-instrument-metadata-quota.sqlite3") as connection:
            quota_operations = tuple(
                row[0] for row in connection.execute("SELECT operation FROM grants ORDER BY rowid")
            )
        assert "ctp_query" in quota_operations
        assert quota_operations.count("ctp_query") == 1
    finally:
        if account_client is not None:
            account_client.close()
        if runtime is not None:
            with suppress(BaseException):
                runtime.close()
        stop.set()
        host_thread.join(10.0)
        assert not host_thread.is_alive(), "Gateway CTP metadata owner thread did not close"

    deployment = deployment_holder["deployment"]
    assert deployment.state == "closed"
    provider = holder["provider"]
    assert provider.connect_calls == 1
    assert provider.account_reads >= 1
    assert provider.close_calls == 1
    assert len(provider.md_apis) == 1
    assert provider.md_apis[0].release_calls == 1
