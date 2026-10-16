"""The legacy Paper import API crosses the normal authenticated ServiceHost."""

from __future__ import annotations

import queue
import socket
import threading
from decimal import Decimal
from typing import Any

import zmq

from bt_api_base.service_protocol import WireIdentity, WireRole, WireScope
from bt_api_execution import (
    PAPER_ENVIRONMENT,
    PAPER_MODEL_REF,
    PAPER_PROVIDER,
    PaperInstrumentV1,
    PaperOwnerDomainV1,
    PaperOpeningPositionV1,
    SqliteExecutionStore,
)
from bt_api_execution.contracts import ExecutionScope
from bt_api_execution.paper_legacy_import import (
    LegacyPaperSymbolBindingV1,
    PaperLegacyImportV1,
)

from bt_api_py.service_components import ComponentResources
from bt_api_py.service_config import parse_client_config, parse_service_config
from bt_api_py.service_host import ServiceHost, TrustedServiceAuthority
from bt_api_py.service_paper_legacy_import_client import PaperLegacyImportClient
from bt_api_py.service_registry import PrincipalGrant, ResourceGrant
from bt_api_py.service_runtime import (
    ClientCurveKeys,
    ServiceClientRuntime,
    ZmqTransportFactory,
)

_SERVICE_ID = "legacy-import-host"
_SERVICE_PRINCIPAL = "legacy-import-service-principal"
_SERVICE_INSTANCE = "legacy-import-owner"
_SERVICE_SCOPE_REF = "legacy-import-service-scope-v1"
_SERVER_SECURITY_REF = "legacy-import-server-curve-v1"
_CLIENT_SECURITY_REF = "legacy-import-client-curve-v1"
_APPLICATION_ID = "legacy-import-application"
_SOURCE_OWNER_ID = "legacy-owner-01"
_TARGET_ACCOUNT_REF = "managed-paper-account-01"
_STRATEGY_REF = "managed-paper-strategy-01"
_SCOPE_REF = "managed-paper-import-scope-v1"
_DOMAIN_REF = "managed-paper-domain-v1"
_MOUNT = "application.paper_legacy_import"
_PORT = "service.paper_legacy_import"
_OPERATIONS = frozenset(
    {
        "paper.import.status",
        "paper.import.inspect_source",
        "paper.import.prepare",
        "paper.import.activate",
        "paper.import.abort",
    }
)
_MAX_MESSAGE_BYTES = 65_536


def _endpoint() -> str:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as probe:
        probe.bind(("127.0.0.1", 0))
        return f"tcp://127.0.0.1:{probe.getsockname()[1]}"


def _service_config(endpoint: str):
    return parse_service_config(
        {
            "service_config_schema_version": 1,
            "process_role": "service",
            "service_id": _SERVICE_ID,
            "scope_ref": _SERVICE_SCOPE_REF,
            "transport_ref": "legacy-import-listener-v1",
            "registration_policy_ref": "legacy-import-registration-v1",
            "service_requirements_ref": "legacy-import-requirements-v1",
            "components": {
                "execution": {
                    "config_schema_version": 2,
                    "settings": {
                        "instance_id": _SERVICE_INSTANCE,
                        "bind_ports": [_PORT],
                        "options": {"role": "legacy_import_owner"},
                    },
                }
            },
            "communication": {
                "config_schema_version": 1,
                "profiles": {
                    "legacy-import-listener-v1": {
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
    )


def _application_config(endpoint: str):
    return parse_client_config(
        {
            "client_config_schema_version": 1,
            "process_role": "application_client",
            "registration": {"application_ref": _APPLICATION_ID},
            "clients": {
                "execution": {
                    "config_schema_version": 1,
                    "settings": {
                        "bindings": [
                            {
                                "mount": _MOUNT,
                                "service_id": _SERVICE_ID,
                                "instance_id": _SERVICE_INSTANCE,
                                "scope_ref": _SCOPE_REF,
                                "transport_ref": "legacy-import-connect-v1",
                                "account_ref": _TARGET_ACCOUNT_REF,
                            }
                        ],
                        "options": {"request_timeout_ms": 2_000},
                    },
                }
            },
            "communication": {
                "config_schema_version": 1,
                "profiles": {
                    "legacy-import-connect-v1": {
                        "backend": "zmq",
                        "config_schema_version": 1,
                        "endpoints": {"rpc_connect": endpoint},
                        "security_profile_ref": _CLIENT_SECURITY_REF,
                        "options": {
                            "request_timeout_ms": 2_000,
                            "high_water_mark": 32,
                            "max_message_bytes": _MAX_MESSAGE_BYTES,
                        },
                    }
                },
            },
        }
    )


def _scopes() -> tuple[WireScope, ExecutionScope]:
    return (
        WireScope(
            _DOMAIN_REF,
            account_ref=_TARGET_ACCOUNT_REF,
            strategy_ref=_STRATEGY_REF,
        ),
        ExecutionScope(
            PAPER_PROVIDER,
            PAPER_ENVIRONMENT,
            _TARGET_ACCOUNT_REF,
            _STRATEGY_REF,
            "20261017",
        ),
    )


def _domain(scope: ExecutionScope) -> PaperOwnerDomainV1:
    return PaperOwnerDomainV1(
        execution_domain_ref=_DOMAIN_REF,
        simulation_account_ref=scope.account_ref,
        experiment_ref="legacy-import-experiment-v1",
        strategy_ref=scope.strategy_id,
        model_ref=PAPER_MODEL_REF,
        model_version="1",
        quote_source_ref="legacy-import-synthetic-quote-v1",
        cash_currency="USDT",
        initial_cash=Decimal("974.975"),
        initial_fees=Decimal("0.025"),
        fee_rate=Decimal("0.001"),
        fee_quantum=Decimal("0.0001"),
        max_quote_age_ns=30_000_000_000,
        instruments=(
            PaperInstrumentV1(
                "BTC-USDT",
                "legacy-import-metadata-v1",
                "BTC",
                "USDT",
                Decimal("0.25"),
                Decimal("0.01"),
            ),
        ),
        opening_positions=(
            PaperOpeningPositionV1("BTC-USDT", Decimal("2.5"), Decimal("10")),
        ),
    )


def _snapshot(owner_id: str) -> PaperLegacyImportV1:
    source_account_id = "legacy-account-01"
    document = {
        "schema": "afi.legacy-paper-inventory.v1",
        "account": {
            "id": source_account_id,
            "user_id": owner_id,
            "name": "Synthetic migrated paper account",
            "initial_cash": 1000.0,
            "current_cash": 974.975,
            "total_equity": 1024.975,
            "profit_loss": 24.975,
            "profit_loss_pct": 2.4975,
            "commission_rate": 0.001,
            "slippage_rate": 0.0,
            "is_active": True,
            "created_at": "2026-10-01T00:00:00+00:00",
            "updated_at": "2026-10-17T00:00:00+00:00",
        },
        "orders": [
            {
                "id": "legacy-order-filled",
                "account_id": "legacy-account-01",
                "symbol": "BTC/USDT",
                "order_type": "market",
                "side": "buy",
                "size": 2.5,
                "filled_size": 2.5,
                "avg_fill_price": 10.0,
                "status": "filled",
                "commission": 0.025,
            }
        ],
        "positions": [
            {
                "id": "legacy-position-btc",
                "account_id": "legacy-account-01",
                "symbol": "BTC/USDT",
                "size": 2.5,
                "avg_price": 10.0,
                "market_value": 50.0,
                "margin_value": 0.0,
                "multiplier": 1.0,
                "margin_rate": 1.0,
            }
        ],
        "trades": [
            {
                "id": "legacy-trade-btc",
                "account_id": "legacy-account-01",
                "order_id": "legacy-order-filled",
                "symbol": "BTC/USDT",
                "side": "buy",
                "size": 2.5,
                "price": 10.0,
                "commission": 0.025,
                "created_at": "2026-10-02T00:00:01+00:00",
            }
        ],
    }
    return PaperLegacyImportV1.from_source_document(
        source_account_id=source_account_id,
        source_owner_id=owner_id,
        source_document=document,
        cash_currency="USDT",
        symbol_bindings=(
            LegacyPaperSymbolBindingV1("BTC/USDT", "BTC-USDT"),
        ),
    )


def _authority(config, server_public: bytes, server_secret: bytes, client_public: bytes):
    wire_scope, _execution_scope = _scopes()
    identity = WireIdentity(WireRole.APPLICATION_CLIENT, _SOURCE_OWNER_ID, _APPLICATION_ID)
    grant = ResourceGrant(
        _SERVICE_INSTANCE,
        _PORT,
        _SCOPE_REF,
        wire_scope,
        _OPERATIONS,
    )
    return TrustedServiceAuthority(
        identity=WireIdentity(WireRole.SERVICE, _SERVICE_PRINCIPAL, _SERVICE_ID),
        scope=WireScope(_DOMAIN_REF),
        scope_ref=config.scope_ref,
        scope_version="legacy-import-host-scope-v1",
        security_ref=_SERVER_SECURITY_REF,
        security_version="curve-key-v1",
        registration_policy_ref=config.registration_policy_ref,
        registration_policy_version="legacy-import-policy-v1",
        service_requirements_ref=config.service_requirements_ref,
        service_requirements_version="legacy-import-requirements-v1",
        curve_public_key=server_public,
        curve_secret_key=server_secret,
        principals={client_public: _SOURCE_OWNER_ID},
        grants={_SOURCE_OWNER_ID: (PrincipalGrant(identity, (grant,)),)},
    )


def test_authenticated_host_import_path_prepares_and_activates_one_fixed_paper_target(
    tmp_path,
):
    endpoint = _endpoint()
    wire_scope, execution_scope = _scopes()
    domain = _domain(execution_scope)
    server_public, server_secret = zmq.curve_keypair()
    client_public, client_secret = zmq.curve_keypair()
    host_config = _service_config(endpoint)
    authority = _authority(host_config, server_public, server_secret, client_public)
    store_path = tmp_path / "legacy-import-store.sqlite3"
    registry_path = tmp_path / "legacy-import-registry.sqlite3"
    ready: queue.Queue[tuple[str, Any]] = queue.Queue(maxsize=1)
    stop = threading.Event()
    host_observation: dict[str, Any] = {}
    stores: list[SqliteExecutionStore] = []

    def serve_host() -> None:
        store = SqliteExecutionStore(store_path)
        stores.append(store)
        host: ServiceHost | None = None
        try:
            def resource_provider(component, instance_id, _options, _service_scope):
                assert component == "execution"
                assert instance_id == _SERVICE_INSTANCE
                return ComponentResources.borrowed(
                    {
                        "store": store,
                        "targets": {wire_scope: execution_scope},
                        "legacy_import_domain": domain,
                    },
                    ready=lambda: True,
                )

            host = ServiceHost(host_config, authority, registry_path, resource_provider)
            host.start()
            ready.put(("started", host.endpoint))
            while not stop.is_set():
                host.serve_once(timeout_ms=20)
        except BaseException as error:
            host_observation["error_type"] = type(error).__name__
            if ready.empty():
                ready.put(("failed", type(error).__name__))
        finally:
            if host is not None and host.state == "started":
                try:
                    host.close()
                except BaseException as error:
                    host_observation["close_error_type"] = type(error).__name__
            store.close()
            host_observation["state"] = None if host is None else host.state

    server_thread = threading.Thread(target=serve_host, name="legacy-import-host", daemon=True)
    server_thread.start()
    runtime: ServiceClientRuntime | None = None
    try:
        status, actual_endpoint = ready.get(timeout=5)
        assert status == "started"
        client_identity = WireIdentity(
            WireRole.APPLICATION_CLIENT, _SOURCE_OWNER_ID, _APPLICATION_ID
        )
        client_config = _application_config(actual_endpoint)
        runtime = ServiceClientRuntime(
            client_config,
            identity=client_identity,
            scope_resolver=lambda _ref: wire_scope,
            peer_resolver=lambda _service_id: authority.identity,
            transport_factory=ZmqTransportFactory(
                {
                    _CLIENT_SECURITY_REF: ClientCurveKeys(
                        server_public,
                        client_public,
                        client_secret,
                    )
                }
            ),
            requested_operations={_MOUNT: _OPERATIONS},
        )
        runtime.start()
        client = PaperLegacyImportClient(
            runtime,
            account_ref=_TARGET_ACCOUNT_REF,
            scope_ref=_SCOPE_REF,
        )

        before = client.status()
        assert before.ok is True and before.code == "OK"
        assert before.result == {"found": False, "receipt": None}
        inspected_before = client.inspect_source("legacy-account-01")
        assert inspected_before.ok is True and inspected_before.code == "OK"
        assert inspected_before.result["source_receipt"] is None
        assert inspected_before.result["owner_domain_exists"] is False

        forged_owner = client.prepare(_snapshot("different-owner"))
        assert forged_owner.ok is False
        assert forged_owner.code == "SOURCE_OWNER_MISMATCH"
        assert forged_owner.result == {}
        assert client.status().result == {"found": False, "receipt": None}

        imported = _snapshot(_SOURCE_OWNER_ID)
        prepared = client.prepare(imported)
        assert prepared.ok is True and prepared.code == "OK"
        assert prepared.result["state"] == "PREPARED"
        assert prepared.result["origin"] == "imported_legacy"
        assert prepared.result["source_owner_id"] == _SOURCE_OWNER_ID
        assert prepared.result["source_sha256"] == imported.source_sha256
        assert prepared.result["import_sha256"] == imported.fingerprint

        prepared_status = client.status()
        assert prepared_status.ok is True
        assert prepared_status.result["receipt"]["state"] == "PREPARED"
        assert prepared_status.result["receipt"]["source_owner_id"] == _SOURCE_OWNER_ID

        activated = client.activate()
        assert activated.ok is True and activated.code == "OK"
        assert activated.result["state"] == "ACTIVE"
        final_status = client.status()
        assert final_status.result["receipt"]["state"] == "ACTIVE"
        inspected_after = client.inspect_source("legacy-account-01")
        assert inspected_after.ok is True
        assert inspected_after.result["source_receipt"]["source_owner_id"] == _SOURCE_OWNER_ID
        assert inspected_after.result["active_account_import_state"] == "ACTIVE"
        assert inspected_after.result["owner_domain_exists"] is True
        book = stores[0].read_paper_book(execution_scope)
        assert book.cash == Decimal("974.975")
        assert book.cumulative_fees == Decimal("0.025")
        assert [(row.instrument, row.quantity, row.average_cost) for row in book.positions] == [
            ("BTC-USDT", Decimal("2.5"), Decimal("10"))
        ]
        assert book.receipt_count == 0
        assert book.execution_cursor == 0
    finally:
        try:
            if runtime is not None and runtime.state not in {"closed", "failed"}:
                runtime.close()
        finally:
            stop.set()
            server_thread.join(timeout=5)
            assert not server_thread.is_alive()

    assert host_observation.get("error_type") is None
    assert host_observation.get("close_error_type") is None
    assert host_observation.get("state") == "closed"
