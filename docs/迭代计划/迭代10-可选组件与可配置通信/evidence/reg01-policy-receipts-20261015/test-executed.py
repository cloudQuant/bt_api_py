"""Real ServiceHost registration, policy receipt, and bypass denial matrix."""

from __future__ import annotations

import hashlib
import multiprocessing
import socket
import uuid
from contextlib import suppress
from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path
from typing import Any

import zmq
from bt_api_base.service_protocol import (
    WireEnvelope,
    WireHeader,
    WireIdentity,
    WireMessageKind,
    WireRole,
    WireScope,
    validate_reply,
)
from bt_api_gateway import GatewayCommandKind, GatewayEventJournal, GatewayPrincipal
from bt_api_gateway.account_session_worker import GatewayAccountSessionWorker
from bt_api_gateway.service_component import GatewayServiceAccountScope
from bt_api_gateway.session_guard import LocalAccountSessionGuard
from bt_api_risk import (
    DurableRiskAdmissionAdapter,
    DurableRiskGate,
    InstrumentRiskAdmissionMapper,
    InstrumentRiskMetadata,
    InstrumentRiskRegistry,
    RiskPolicy,
)

from bt_api_py.bt_api import BtApi
from bt_api_py.service_account import ServiceAccountClient
from bt_api_py.service_components import ComponentResources, TrustedRiskPolicyBinding
from bt_api_py.service_config import parse_client_config, parse_service_config
from bt_api_py.service_execution import ServiceExecutionClient
from bt_api_py.service_host import ServiceHost, TrustedServiceAuthority
from bt_api_py.service_registry import PrincipalGrant, ResourceGrant
from bt_api_py.service_risk_order_entry import RiskBtApiProviderContract, RiskOrderTarget
from bt_api_py.service_risk_order_journal import RiskOrderJournal
from bt_api_py.service_runtime import (
    ClientCurveKeys,
    ServiceClientRuntime,
    ZmqTransportFactory,
)
from tests.unit import test_service_account_integration as account_fixture
from tests.unit import test_service_risk_only_order_integration as risk_fixture


_DOMAIN = "paper:service-registration-matrix"
_SERVICE_ID = "registration-matrix-service"
_RISK_INSTANCE = "registration-matrix-risk-owner"
_GATEWAY_INSTANCE = "registration-matrix-gateway-owner"
_SERVICE_PRINCIPAL = "registration-matrix-service-principal"
_SERVER_SECURITY_REF = "registration-matrix-server-curve-v1"
_CLIENT_SECURITY_REF = "registration-matrix-client-curve-v1"
_TRANSPORT_REF = "registration-matrix-transport-v1"
_REGISTRATION_POLICY_REF = "registration-matrix-access-policy"
_REGISTRATION_POLICY_VERSION = "registration-matrix-access-policy-v1"
_REQUIREMENTS_REF = "registration-matrix-service-requirements"
_REQUIREMENTS_VERSION = "registration-matrix-requirements-v1"
_RISK_POLICY_REF = "registration-matrix-account-risk-policy-v1"
_ACTUAL_RISK_POLICY_ID = "registration-matrix-risk-policy-id-v1"
_WRITER_PRINCIPAL = "registration-matrix-writer-principal"
_WRITER_STRATEGY = "registration-matrix-authorized-strategy"
_READER_PRINCIPAL = "registration-matrix-reader-principal"
_READER_STRATEGY = "registration-matrix-readonly-strategy"
_ACCOUNT_REF = "account-a"
_FOREIGN_ACCOUNT_REF = "account-b"
_ORDER_SCOPE_REF = "registration-matrix-order-scope-v1"
_ACCOUNT_SCOPE_REF = "registration-matrix-account-scope-v1"
_REQUEST_TIMEOUT_MS = 2_000
_MAX_MESSAGE_BYTES = 65_536

_WRITER_SCOPE = WireScope(
    _DOMAIN,
    account_ref=_ACCOUNT_REF,
    strategy_ref=_WRITER_STRATEGY,
)
_READER_SCOPE = WireScope(
    _DOMAIN,
    account_ref=_ACCOUNT_REF,
    strategy_ref=_READER_STRATEGY,
)


class _CountingAccountProvider(account_fixture._SyntheticAccountProvider):
    """The real Gateway read fixture with a visible forbidden write counter."""

    def __init__(self, account_ref: str) -> None:
        super().__init__(account_ref)
        self.write_calls = 0

    def make_order(self, *_args: Any, **_kwargs: Any) -> None:
        self.write_calls += 1
        raise AssertionError("Gateway's account provider exposes no order-write path")


def _endpoint() -> str:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as probe:
        probe.bind(("127.0.0.1", 0))
        return f"tcp://127.0.0.1:{probe.getsockname()[1]}"


def _service_config(endpoint: str) -> dict[str, Any]:
    return {
        "service_config_schema_version": 1,
        "process_role": "service",
        "service_id": _SERVICE_ID,
        "scope_ref": "registration-matrix-service-scope-v1",
        "transport_ref": _TRANSPORT_REF,
        "registration_policy_ref": _REGISTRATION_POLICY_REF,
        "service_requirements_ref": _REQUIREMENTS_REF,
        "components": {
            "gateway": {
                "config_schema_version": 2,
                "settings": {
                    "instance_id": _GATEWAY_INSTANCE,
                    "bind_ports": ["service.account_read", "service.provider_session"],
                    "options": {
                        "role": "account_session_worker",
                        "account_session_ref": "registration-matrix-account-session-v1",
                    },
                },
            },
            "risk": {
                "config_schema_version": 2,
                "settings": {
                    "instance_id": _RISK_INSTANCE,
                    "bind_ports": ["service.order_entry", "service.order_query"],
                    "options": {
                        "role": "account_owner",
                        "account_policy_ref": _RISK_POLICY_REF,
                    },
                },
            },
        },
        "communication": {
            "config_schema_version": 1,
            "profiles": {
                _TRANSPORT_REF: {
                    "backend": "zmq",
                    "config_schema_version": 1,
                    "endpoints": {"rpc_bind": endpoint},
                    "security_profile_ref": _SERVER_SECURITY_REF,
                    "options": {
                        "high_water_mark": 32,
                        "max_message_bytes": _MAX_MESSAGE_BYTES,
                    },
                }
            },
        },
    }


def _client_config(
    *,
    endpoint: str,
    mount: str,
    scope_ref: str,
    account_ref: str,
    strategy_ref: str,
) -> Any:
    return parse_client_config(
        {
            "client_config_schema_version": 1,
            "process_role": "strategy_client",
            "registration": {"strategy_ref": strategy_ref},
            "clients": {
                ("execution" if mount == "broker.orders" else "gateway"): {
                    "config_schema_version": 1,
                    "settings": {
                        "bindings": [
                            {
                                "mount": mount,
                                "service_id": _SERVICE_ID,
                                "instance_id": (
                                    _RISK_INSTANCE
                                    if mount == "broker.orders"
                                    else _GATEWAY_INSTANCE
                                ),
                                "scope_ref": scope_ref,
                                "transport_ref": _TRANSPORT_REF,
                                "account_ref": account_ref,
                            }
                        ],
                        "options": {"request_timeout_ms": _REQUEST_TIMEOUT_MS},
                    },
                }
            },
            "communication": {
                "config_schema_version": 1,
                "profiles": {
                    _TRANSPORT_REF: {
                        "backend": "zmq",
                        "config_schema_version": 1,
                        "endpoints": {"rpc_connect": endpoint},
                        "security_profile_ref": _CLIENT_SECURITY_REF,
                        "options": {
                            "request_timeout_ms": _REQUEST_TIMEOUT_MS,
                            "high_water_mark": 32,
                            "max_message_bytes": _MAX_MESSAGE_BYTES,
                        },
                    }
                },
            },
        }
    )


def _authority(config, server_keys, writer_public: bytes, reader_public: bytes):
    writer_identity = WireIdentity(
        WireRole.STRATEGY_CLIENT,
        _WRITER_PRINCIPAL,
        _WRITER_STRATEGY,
    )
    reader_identity = WireIdentity(
        WireRole.STRATEGY_CLIENT,
        _READER_PRINCIPAL,
        _READER_STRATEGY,
    )
    writer_grant = ResourceGrant(
        _RISK_INSTANCE,
        "service.order_entry",
        _ORDER_SCOPE_REF,
        _WRITER_SCOPE,
        frozenset({"orders.submit"}),
    )
    reader_grant = ResourceGrant(
        _GATEWAY_INSTANCE,
        "service.account_read",
        _ACCOUNT_SCOPE_REF,
        _READER_SCOPE,
        frozenset({"account.snapshot"}),
    )
    return TrustedServiceAuthority(
        identity=WireIdentity(WireRole.SERVICE, _SERVICE_PRINCIPAL, _SERVICE_ID),
        scope=WireScope(_DOMAIN),
        scope_ref=config.scope_ref,
        scope_version="registration-matrix-service-scope-v1",
        security_ref=_SERVER_SECURITY_REF,
        security_version="registration-matrix-server-security-v1",
        registration_policy_ref=config.registration_policy_ref,
        registration_policy_version=_REGISTRATION_POLICY_VERSION,
        service_requirements_ref=config.service_requirements_ref,
        service_requirements_version=_REQUIREMENTS_VERSION,
        curve_public_key=server_keys[0],
        curve_secret_key=server_keys[1],
        principals={
            writer_public: _WRITER_PRINCIPAL,
            reader_public: _READER_PRINCIPAL,
        },
        grants={
            _WRITER_PRINCIPAL: (PrincipalGrant(writer_identity, (writer_grant,)),),
            _READER_PRINCIPAL: (PrincipalGrant(reader_identity, (reader_grant,)),),
        },
    )


def _host_process(
    control,
    raw_config,
    registry_path,
    gate_path,
    risk_journal_path,
    risk_provider_path,
    account_journal_path,
    guard_directory,
    server_keys,
    writer_public,
    reader_public,
) -> None:
    """Spawn target that owns one actual combined Risk and Gateway ServiceHost."""

    host = None
    gate = None
    bt_api = None
    risk_provider = risk_fixture._SyntheticBtApiFeed(risk_provider_path)
    account_provider = _CountingAccountProvider(_ACCOUNT_REF)
    gateway_worker = None
    try:
        config = parse_service_config(raw_config)
        account_key = account_fixture._ACCOUNT_KEYS[_ACCOUNT_REF]
        account_journal_scope = "registration-matrix-account-journal"
        account_state_scope = "registration-matrix-account-state"
        account_scope = GatewayServiceAccountScope(
            account_ref=_ACCOUNT_REF,
            execution_domain_ref=_DOMAIN,
            journal_account_scope=account_journal_scope,
            account_read_strategy_scope=account_state_scope,
            shared_account_facts_scope=account_state_scope,
            shared_fact_strategy_refs=frozenset({_READER_STRATEGY}),
        )
        gateway_principals = {
            _READER_PRINCIPAL: GatewayPrincipal(
                principal_id=_READER_PRINCIPAL,
                account_scopes=frozenset({account_journal_scope}),
                strategy_scopes=frozenset({account_state_scope}),
                allowed_kinds=frozenset({GatewayCommandKind.READ}),
            )
        }
        bt_api = BtApi(debug=False)
        exchange_name = risk_fixture._EXCHANGE
        bt_api.exchange_feeds[exchange_name] = risk_provider
        risk_journal = RiskOrderJournal(
            risk_journal_path,
            writer_id="registration-matrix-risk-owner",
        )
        policy = RiskPolicy(
            _ACTUAL_RISK_POLICY_ID,
            Decimal("450"),
            max_increase_count=10,
            notional_unit="USD",
        )
        gate = DurableRiskGate(
            gate_path,
            policy,
            execution_journal_authority=risk_journal,
        )
        metadata = InstrumentRiskMetadata(
            instrument=risk_fixture._INSTRUMENT,
            metadata_version=risk_fixture._METADATA_VERSION,
            as_of_ns=risk_fixture._METADATA_AS_OF_NS,
            expires_at_ns=risk_fixture._METADATA_EXPIRES_AT_NS,
            tick_size=Decimal("1"),
            quantity_step=Decimal("1"),
            contract_multiplier=Decimal("1"),
            max_gross_notional=Decimal("1000"),
            taker_fee_bps=Decimal("0"),
            fixed_fee=Decimal("0"),
            max_slippage_bps=Decimal("0"),
            quantity_unit="BTC",
            valuation_unit="USD",
        )
        risk_scope = risk_fixture._ACCOUNT_SCOPE
        target = RiskOrderTarget(
            account_scope=risk_scope,
            strategy_id=_WRITER_STRATEGY,
            exchange_name=exchange_name,
            provider_account_id=risk_fixture._PROVIDER_ACCOUNT,
            registry=InstrumentRiskRegistry((metadata,)),
        )
        mapper = InstrumentRiskAdmissionMapper(
            risk_scope,
            target.registry,
        )
        admission = DurableRiskAdmissionAdapter(gate, mapper)
        provider_contract = RiskBtApiProviderContract(
            exchange_name=exchange_name,
            profile_id="registration-matrix-provider-contract-v1",
            order_schema="fixture.order.v1",
            fill_schema="fixture.fills.v1",
            client_order_id_query=True,
            complete_order_snapshot=True,
            complete_fill_history=True,
            provider_accepts_client_order_id=True,
            client_order_id_query_unique=True,
            provider_status_mapping=(
                ("open", "accepted"),
                ("filled", "completed"),
                ("cancelled", "canceled"),
                ("expired", "expired"),
                ("rejected", "rejected"),
            ),
            supported_order_types=("LIMIT",),
            supported_time_in_force=("GTC",),
            supported_position_effects=("OPEN",),
        )
        Path(guard_directory).mkdir(parents=True, exist_ok=True)
        account_session_guard = LocalAccountSessionGuard(
            Path(guard_directory),
            account_key.account_key_id,
            owner_ref="registration-matrix-gateway-owner",
        )
        gateway_journal = GatewayEventJournal(account_journal_path)
        gateway_worker = GatewayAccountSessionWorker(
            account_provider,
            gateway_journal,
            account_key=account_key,
            journal_account_scope=account_journal_scope,
            state_scope=account_state_scope,
            guard=account_session_guard,
            required_coverage=account_fixture._REQUIRED_COVERAGE,
            # This bounded authorization matrix observes provider-call deltas;
            # scheduled refresh belongs to the separate worker lifecycle tests.
            refresh_interval_seconds=3_600,
            max_staleness_seconds=7_200,
            request_timeout_seconds=2,
        )

        def resources(component, _instance_id, options, _scope):
            if component == "risk":
                if options.get("role") != "account_owner":
                    raise AssertionError("the risk route must be an account owner")
                return ComponentResources.borrowed(
                    {
                        "bt_api": bt_api,
                        "targets": {_WRITER_SCOPE: target},
                        "admission": {_WRITER_SCOPE: admission},
                        "journal": risk_journal,
                        "gate": gate,
                        "provider_contracts": {_WRITER_SCOPE: provider_contract},
                    },
                    ready=lambda: True,
                    risk_policy_bindings=(
                        TrustedRiskPolicyBinding(
                            reference=_RISK_POLICY_REF,
                            scope=_WRITER_SCOPE,
                            policy=gate.policy,
                        ),
                    ),
                )
            if component == "gateway":
                if options.get("role") != "account_session_worker":
                    raise AssertionError("the Gateway route must own its account session")
                return ComponentResources.owned(
                    {
                        "journal": gateway_journal,
                        "accounts": {_ACCOUNT_REF: account_scope},
                        "principals": gateway_principals,
                    },
                    ready=lambda: True,
                    close=gateway_worker.close,
                    internal_ports={"service.provider_session": gateway_worker},
                )
            raise AssertionError("unexpected selected service component")

        host = ServiceHost(
            config,
            _authority(config, server_keys, writer_public, reader_public),
            registry_path,
            resources,
        )
        host.start()
        control.send(("ready", host.endpoint, gate.policy.fingerprint, host.state))
        while True:
            host.serve_once(timeout_ms=25)
            if not control.poll():
                continue
            command = control.recv()
            if command == ("counts",):
                control.send(
                    (
                        "counts",
                        tuple(risk_provider.writes),
                        account_provider.write_calls,
                        account_provider.read_calls,
                        account_provider.connect_calls,
                        tuple(risk_provider.order_queries),
                        tuple(risk_provider.fill_queries),
                        account_provider.close_calls,
                    )
                )
                continue
            if command == ("close",):
                host.close()
                gate.close()
                bt_api.close()
                control.send(("closed", host.state))
                return
            raise AssertionError("unknown registration-matrix host command")
    except BaseException as exc:
        if host is not None and host.state not in {"closed", "failed", "close_failed"}:
            with suppress(BaseException):
                host.close()
        if gateway_worker is not None:
            with suppress(BaseException):
                gateway_worker.close()
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


def _runtime(
    *,
    endpoint: str,
    identity: WireIdentity,
    scope: WireScope,
    client_keys: tuple[bytes, bytes],
    server_public: bytes,
    mount: str,
) -> ServiceClientRuntime:
    config = _client_config(
        endpoint=endpoint,
        mount=mount,
        scope_ref=_ORDER_SCOPE_REF if mount == "broker.orders" else _ACCOUNT_SCOPE_REF,
        account_ref=_ACCOUNT_REF,
        strategy_ref=scope.strategy_ref,
    )
    runtime = ServiceClientRuntime(
        config,
        identity=identity,
        scope_resolver=lambda _scope_ref: scope,
        peer_resolver=lambda _service_id: WireIdentity(
            WireRole.SERVICE,
            _SERVICE_PRINCIPAL,
            _SERVICE_ID,
        ),
        transport_factory=ZmqTransportFactory(
            {
                _CLIENT_SECURITY_REF: ClientCurveKeys(
                    server_public,
                    client_keys[0],
                    client_keys[1],
                )
            }
        ),
        requested_operations={
            mount: frozenset(
                {"orders.submit"} if mount == "broker.orders" else {"account.snapshot"}
            )
        },
    )
    runtime.start()
    return runtime


def _raw_register(
    runtime: ServiceClientRuntime,
    *,
    identity: WireIdentity,
    scope: WireScope,
    port: str,
    scope_ref: str,
) -> dict[str, Any]:
    payload = {
        "registration_key": hashlib.sha256(
            f"{runtime.client_session_id}:{port}:{uuid.uuid4().hex}".encode()
        ).hexdigest(),
        "client_session_id": runtime.client_session_id,
        "instance_id": (
            _GATEWAY_INSTANCE if port == "service.provider_session" else _RISK_INSTANCE
        ),
        "port": port,
        "scope_ref": scope_ref,
        "operations": ["orders.submit"],
    }
    request = WireEnvelope(
        WireHeader(
            protocol_version=2,
            kind=WireMessageKind.REQUEST,
            message_id=uuid.uuid4().hex,
            correlation_id=None,
            identity=identity,
            scope=scope,
            operation="service.register",
            schema_id="sdk.service.control",
            schema_version=1,
            sent_at_utc=datetime.now(UTC).isoformat().replace("+00:00", "Z"),
        ),
        payload,
    )
    transport = runtime._transports[(_SERVICE_ID, _TRANSPORT_REF)]
    reply = transport.request(request, timeout=_REQUEST_TIMEOUT_MS / 1000)
    validate_reply(reply, request, expected_peer=runtime._peers[_SERVICE_ID])
    return dict(reply.payload)


def _assert_receipt(binding, *, risk_status: str, risk_fingerprint: str | None) -> None:
    assert binding.access_policy_ref == _REGISTRATION_POLICY_REF
    assert binding.access_policy_version == _REGISTRATION_POLICY_VERSION
    assert binding.policy_version == _REGISTRATION_POLICY_VERSION
    assert binding.requirements_ref == _REQUIREMENTS_REF
    assert binding.requirements_version == _REQUIREMENTS_VERSION
    assert binding.risk_policy_status == risk_status
    if risk_status == "APPLIED":
        assert binding.risk_policy_ref == _RISK_POLICY_REF
        assert binding.risk_policy_version == risk_fingerprint
        assert binding.risk_policy_version != binding.access_policy_version
    else:
        assert binding.risk_policy_status == "NOT_APPLICABLE"
        assert binding.risk_policy_ref is None
        assert binding.risk_policy_version is None


def test_real_service_registration_returns_scoped_policy_receipts_and_denies_bypasses(
    tmp_path,
) -> None:
    from tests.unit._spawned_process_helpers import (
        _assert_no_fallback_cleanup,
        _bounded_reap_spawned_process,
        _client_command,
        _close_normally,
        _receive,
        _start_process,
    )

    context = multiprocessing.get_context("spawn")
    endpoint = _endpoint()
    server_keys = zmq.curve_keypair()
    writer_keys = zmq.curve_keypair()
    reader_keys = zmq.curve_keypair()
    raw_config = _service_config(endpoint)
    process_paths = (
        str(tmp_path / "registry.sqlite3"),
        str(tmp_path / "risk-gate.sqlite3"),
        str(tmp_path / "risk-journal.sqlite3"),
        str(tmp_path / "risk-provider.sqlite3"),
        str(tmp_path / "account-journal.sqlite3"),
        str(tmp_path / "account-session-guard"),
    )
    host_record = None
    writer_runtime = None
    reader_runtime = None
    account_client = None
    records = []
    forced = []
    failures = []
    try:
        host_record = _start_process(
            context,
            _host_process,
            (
                raw_config,
                *process_paths,
                server_keys,
                writer_keys[0],
                reader_keys[0],
            ),
            "registration-matrix-service-host",
        )
        records.append(host_record)
        ready = _receive(host_record["pipe"])
        assert ready[0] == "ready", ready
        assert ready[1] == endpoint
        assert ready[3] == "started"
        risk_fingerprint = ready[2]
        assert type(risk_fingerprint) is str and len(risk_fingerprint) == 64

        writer_identity = WireIdentity(
            WireRole.STRATEGY_CLIENT,
            _WRITER_PRINCIPAL,
            _WRITER_STRATEGY,
        )
        writer_scope = WireScope(
            _DOMAIN,
            account_ref=_ACCOUNT_REF,
            strategy_ref=_WRITER_STRATEGY,
        )
        writer_runtime = _runtime(
            endpoint=endpoint,
            identity=writer_identity,
            scope=writer_scope,
            client_keys=writer_keys,
            server_public=server_keys[0],
            mount="broker.orders",
        )
        assert len(writer_runtime.bindings) == 1
        writer_binding = writer_runtime.bindings[0]
        assert writer_binding.mount == "broker.orders"
        assert writer_binding.operations == frozenset({"orders.submit"})
        _assert_receipt(
            writer_binding,
            risk_status="APPLIED",
            risk_fingerprint=risk_fingerprint,
        )

        reader_identity = WireIdentity(
            WireRole.STRATEGY_CLIENT,
            _READER_PRINCIPAL,
            _READER_STRATEGY,
        )
        reader_scope = WireScope(
            _DOMAIN,
            account_ref=_ACCOUNT_REF,
            strategy_ref=_READER_STRATEGY,
        )
        reader_runtime = _runtime(
            endpoint=endpoint,
            identity=reader_identity,
            scope=reader_scope,
            client_keys=reader_keys,
            server_public=server_keys[0],
            mount="store.account",
        )
        assert len(reader_runtime.bindings) == 1
        reader_binding = reader_runtime.bindings[0]
        assert reader_binding.mount == "store.account"
        assert reader_binding.operations == frozenset({"account.snapshot"})
        _assert_receipt(
            reader_binding,
            risk_status="NOT_APPLICABLE",
            risk_fingerprint=None,
        )
        account_client = ServiceAccountClient(
            reader_runtime,
            "store.account",
            account_ref=_ACCOUNT_REF,
            expected_account=account_fixture._ACCOUNT_KEYS[_ACCOUNT_REF],
            required_coverage=account_fixture._REQUIRED_COVERAGE,
            expected_scope_ref=_ACCOUNT_SCOPE_REF,
        )
        account_view = account_client.refresh()
        assert account_view.observation.account_key == account_fixture._ACCOUNT_KEYS[_ACCOUNT_REF]
        assert "broker.orders" not in {binding.mount for binding in reader_runtime.bindings}

        baseline = _client_command(host_record, "counts")
        assert baseline[1] == ()
        assert baseline[2] == 0
        assert baseline[3] >= 1
        assert baseline[4] == 1

        def assert_no_provider_write() -> None:
            current = _client_command(host_record, "counts")
            # Registration denials must not enter any provider operation,
            # including reads, order/fill queries, and session lifecycle calls.
            assert current == baseline
            assert current[1] == baseline[1] == ()
            assert current[2] == baseline[2] == 0

        forged_identity = WireIdentity(
            WireRole.STRATEGY_CLIENT,
            _WRITER_PRINCIPAL,
            "registration-matrix-forged-strategy",
        )
        forged_scope = WireScope(
            _DOMAIN,
            account_ref=_ACCOUNT_REF,
            strategy_ref=forged_identity.actor_ref,
            run_id=writer_runtime.run_id,
        )
        forged = _raw_register(
            writer_runtime,
            identity=forged_identity,
            scope=forged_scope,
            port="service.order_entry",
            scope_ref=_ORDER_SCOPE_REF,
        )
        assert forged == {"ok": False, "code": "DENIED", "result": {}}
        assert_no_provider_write()

        wrong_account_scope = WireScope(
            _DOMAIN,
            account_ref=_FOREIGN_ACCOUNT_REF,
            strategy_ref=_WRITER_STRATEGY,
            run_id=writer_runtime.run_id,
        )
        wrong_account = _raw_register(
            writer_runtime,
            identity=writer_identity,
            scope=wrong_account_scope,
            port="service.order_entry",
            scope_ref=_ORDER_SCOPE_REF,
        )
        assert wrong_account == {"ok": False, "code": "SCOPE_DENIED", "result": {}}
        assert_no_provider_write()

        missing_order_grant = _raw_register(
            reader_runtime,
            identity=reader_identity,
            scope=WireScope(
                _DOMAIN,
                account_ref=_ACCOUNT_REF,
                strategy_ref=_READER_STRATEGY,
                run_id=reader_runtime.run_id,
            ),
            port="service.order_entry",
            scope_ref=_ORDER_SCOPE_REF,
        )
        assert missing_order_grant == {
            "ok": False,
            "code": "SCOPE_DENIED",
            "result": {},
        }
        assert_no_provider_write()

        internal_provider_port = _raw_register(
            reader_runtime,
            identity=reader_identity,
            scope=WireScope(
                _DOMAIN,
                account_ref=_ACCOUNT_REF,
                strategy_ref=_READER_STRATEGY,
                run_id=reader_runtime.run_id,
            ),
            port="service.provider_session",
            scope_ref=_ACCOUNT_SCOPE_REF,
        )
        assert internal_provider_port == {
            "ok": False,
            "code": "CAPABILITY_UNAVAILABLE",
            "result": {},
        }
        assert_no_provider_write()

        execution = ServiceExecutionClient(
            writer_runtime,
            "broker.orders",
            scope_ref=_ORDER_SCOPE_REF,
        )
        accepted_order = execution.submit(
            **risk_fixture._order_arguments("registration-matrix-authorized-order")
        )
        assert accepted_order.state == "ACKED_TRACKED"
        counts_after_write = _client_command(host_record, "counts")
        assert counts_after_write[0] == "counts"
        assert len(counts_after_write[1]) == 1
        assert counts_after_write[2] == 0

        account_client.close()
        account_client = None
        reader_runtime.close()
        reader_runtime = None
        writer_runtime.close()
        writer_runtime = None
        closed = _close_normally(host_record, expected_tag="closed")
        assert closed == ("closed", "closed")
    finally:
        for client in (account_client,):
            if client is not None:
                with suppress(BaseException):
                    client.close()
        for runtime in (reader_runtime, writer_runtime):
            if runtime is not None:
                with suppress(BaseException):
                    runtime.close()
        for record in records:
            _bounded_reap_spawned_process(record, forced, failures)
        _assert_no_fallback_cleanup(forced, failures)

