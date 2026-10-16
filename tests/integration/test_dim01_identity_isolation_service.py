"""DIM01 identity separation through a normal SDK ServiceHost/CURVE path.

The synthetic provider declares a stable server-owned trade-ID domain that is
independent of exchange aliases and versioned provider profiles. Trade IDs
are unique within that domain and canonical instrument; identical bare IDs are
valid across distinct account or product/environment domains. Reuse within a
domain must remain unresolved and cannot credit a second fill. Provider order
IDs are unique within the account/product domain and receive the same
cross-permit collision treatment.

This test uses only the public risk-order owner, normal BtApi normalization,
real ServiceHost/ServiceClientRuntime registration and durable SQLite stores.
It does not assert global uniqueness of a bare provider ID.
"""

from __future__ import annotations

import json
import multiprocessing
import socket
import sqlite3
import time
from contextlib import suppress
from decimal import Decimal
from pathlib import Path
from typing import Any

import pytest
import zmq
from bt_api_base.service_protocol import WireIdentity, WireRole, WireScope
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
from bt_api_py.service_config import parse_client_config, parse_service_config
from bt_api_py.service_execution import ServiceExecutionClient, ServiceExecutionError
from bt_api_py.service_host import ServiceHost, TrustedServiceAuthority
from bt_api_py.service_registry import PrincipalGrant, ResourceGrant
from bt_api_py.service_risk_order_entry import (
    RiskBtApiProviderContract,
    RiskOrderTarget,
)
from bt_api_py.service_risk_order_journal import RiskOrderJournal
from bt_api_py.service_runtime import (
    ClientCurveKeys,
    ServiceClientRuntime,
    ZmqTransportFactory,
)

from tests.unit._spawned_process_helpers import (
    _assert_no_fallback_cleanup,
    _bounded_reap_spawned_process,
    _client_command,
    _close_normally,
    _receive,
    _start_process,
)


_DOMAIN = "paper:dim01-identity-isolation"
_SERVICE_ID = "dim01-risk-owner-service"
_SERVICE_PRINCIPAL = "dim01-risk-owner-principal"
_SERVICE_INSTANCE = "dim01-risk-owner"
_SERVICE_SCOPE_REF = "dim01-service-root-v1"
_SERVER_SECURITY_REF = "dim01-server-curve-v1"
_TRANSPORT_LISTENER = "dim01-listener-v1"
_TRANSPORT_CONNECT = "dim01-client-curve-v1"
_PRINCIPAL_A = "dim01-strategy-principal-a"
_PRINCIPAL_B = "dim01-strategy-principal-b"
_STRATEGY_A = "dim01-strategy-a"
_STRATEGY_B = "dim01-strategy-b"
_REQUEST_TIMEOUT_MS = 2_000
_MAX_MESSAGE_BYTES = 65_536

_EXCHANGE_SPOT_A = "FIXTURE___SPOT_A"
_EXCHANGE_SPOT_A_ALIAS = "FIXTURE___SPOT_A_ALIAS"
_EXCHANGE_SPOT_B = "FIXTURE___SPOT_B"
_EXCHANGE_PERP_A = "FIXTURE___PERP_A"
_PROFILE_SPOT = "dim01-fixture-spot-profile-v1"
_PROFILE_SPOT_ROTATED = "dim01-fixture-spot-profile-v2"
_PROFILE_PERP = "dim01-fixture-perp-profile-v1"
_TRADE_DOMAIN_SPOT_A = "fixture-account-a-spot-trades-v1"
_TRADE_DOMAIN_SPOT_B = "fixture-account-b-spot-trades-v1"
_TRADE_DOMAIN_PERP_A = "fixture-account-a-perpetual-trades-v1"
_TRADE_ID_SCOPE_INSTRUMENT = "INSTRUMENT"
_ORDER_SCHEMA = "dim01-order-v2"
_FILL_SCHEMA = "dim01-fill-v2"
_BTC = "BTC-USD"
_ETH = "ETH-USD"
_VERSION_BTC_SPOT = "dim01-btc-spot-contract-v1"
_VERSION_ETH_SPOT = "dim01-eth-spot-contract-v1"
_VERSION_BTC_PERP = "dim01-btc-perp-contract-v7"

_SCOPE_SPOT_A = "dim01-scope-account-a-spot-strategy-a-v1"
_SCOPE_SPOT_A_STRATEGY_B = "dim01-scope-account-a-spot-strategy-b-v1"
_SCOPE_SPOT_A_ACCOUNT_MODE = "dim01-scope-account-a-spot-strategy-a-account-mode-v1"
_SCOPE_SPOT_B = "dim01-scope-account-b-spot-strategy-a-v1"
_SCOPE_PERP_A = "dim01-scope-account-a-perp-strategy-a-v1"
_ROUTE_SPOT_A = "dim01-route-account-a-spot-v1"
_ROUTE_SPOT_A_STRATEGY_B = "dim01-route-account-a-spot-strategy-b-v1"
_ROUTE_SPOT_A_ACCOUNT_MODE = "dim01-route-account-a-spot-account-mode-v1"
_ROUTE_SPOT_B = "dim01-route-account-b-spot-v1"
_ROUTE_PERP_A = "dim01-route-account-a-perp-v1"

_ACCOUNT_SPOT_A = AccountScope("fixture", "provider-account-a", "simulation")
_ACCOUNT_SPOT_B = AccountScope("fixture", "provider-account-b", "simulation")
_ACCOUNT_PERP_A = AccountScope("fixture", "provider-account-a", "simulation-perpetual")


def _scope(
    account_ref: str,
    strategy_ref: str,
    *,
    portfolio_ref: str,
) -> WireScope:
    return WireScope(
        _DOMAIN,
        account_ref=account_ref,
        strategy_ref=strategy_ref,
        portfolio_ref=portfolio_ref,
    )


_SCOPES = {
    _SCOPE_SPOT_A: _scope("account-a", _STRATEGY_A, portfolio_ref="portfolio-spot"),
    _SCOPE_SPOT_A_STRATEGY_B: _scope(
        "account-a", _STRATEGY_B, portfolio_ref="portfolio-spot"
    ),
    # The wire account_ref is the durable registration namespace for one
    # strategy on one account: the registry admits a single active write
    # registration per (domain, strategy, account_ref). Contracts for further
    # products/environments of the same provider account therefore bind under
    # their own wire account_ref while the trusted target keeps the shared
    # provider-account identity.
    _SCOPE_SPOT_A_ACCOUNT_MODE: _scope(
        "account-a-account-mode", _STRATEGY_A, portfolio_ref="portfolio-spot-account-mode"
    ),
    _SCOPE_SPOT_B: _scope("account-b", _STRATEGY_A, portfolio_ref="portfolio-spot"),
    _SCOPE_PERP_A: _scope(
        "account-a-perpetual", _STRATEGY_A, portfolio_ref="portfolio-perpetual"
    ),
}

_ROUTES = {
    _ROUTE_SPOT_A: {
        "scope_ref": _SCOPE_SPOT_A,
        "account_ref": "account-a",
        "strategy_ref": _STRATEGY_A,
        "provider_account_id": "provider-account-a",
        "environment": "simulation",
        "exchange_name": _EXCHANGE_SPOT_A,
        "profile_id": _PROFILE_SPOT,
        "trade_id_domain_ref": _TRADE_DOMAIN_SPOT_A,
        "trade_id_scope": _TRADE_ID_SCOPE_INSTRUMENT,
    },
    # Separate trusted alias, same underlying venue/account/environment/profile.
    _ROUTE_SPOT_A_STRATEGY_B: {
        "scope_ref": _SCOPE_SPOT_A_STRATEGY_B,
        "account_ref": "account-a",
        "strategy_ref": _STRATEGY_B,
        "provider_account_id": "provider-account-a",
        "environment": "simulation",
        "exchange_name": _EXCHANGE_SPOT_A_ALIAS,
        "profile_id": _PROFILE_SPOT_ROTATED,
        "trade_id_domain_ref": _TRADE_DOMAIN_SPOT_A,
        "trade_id_scope": _TRADE_ID_SCOPE_INSTRUMENT,
    },
    # A second trusted contract for the same provider account/product keeps the
    # same stable domain but declares a conflicting scope mode. Once the domain
    # is fixed to INSTRUMENT on first use, this must fail closed instead of
    # opening a second identity space for the same trades.
    _ROUTE_SPOT_A_ACCOUNT_MODE: {
        "scope_ref": _SCOPE_SPOT_A_ACCOUNT_MODE,
        "account_ref": "account-a-account-mode",
        "strategy_ref": _STRATEGY_A,
        "provider_account_id": "provider-account-a",
        "environment": "simulation",
        "exchange_name": _EXCHANGE_SPOT_A,
        "profile_id": _PROFILE_SPOT,
        "trade_id_domain_ref": _TRADE_DOMAIN_SPOT_A,
        "trade_id_scope": "ACCOUNT",
    },
    _ROUTE_SPOT_B: {
        "scope_ref": _SCOPE_SPOT_B,
        "account_ref": "account-b",
        "strategy_ref": _STRATEGY_A,
        "provider_account_id": "provider-account-b",
        "environment": "simulation",
        "exchange_name": _EXCHANGE_SPOT_B,
        "profile_id": _PROFILE_SPOT,
        "trade_id_domain_ref": _TRADE_DOMAIN_SPOT_B,
        "trade_id_scope": _TRADE_ID_SCOPE_INSTRUMENT,
    },
    _ROUTE_PERP_A: {
        "scope_ref": _SCOPE_PERP_A,
        "account_ref": "account-a-perpetual",
        "strategy_ref": _STRATEGY_A,
        "provider_account_id": "provider-account-a",
        "environment": "simulation-perpetual",
        "exchange_name": _EXCHANGE_PERP_A,
        "profile_id": _PROFILE_PERP,
        "trade_id_domain_ref": _TRADE_DOMAIN_PERP_A,
        "trade_id_scope": _TRADE_ID_SCOPE_INSTRUMENT,
    },
}


class _Dim01SyntheticV2Feed:
    """Durable provider leaf with a stable server-owned identity domain.

    The ledger namespace is the declared ``trade_id_domain_ref``: it survives
    SDK profile rotation and exchange aliases for the same provider
    account/product, exactly like the trusted server contract it models.
    """

    def __init__(
        self,
        ledger_path: str,
        *,
        trade_id_domain_ref: str,
        provider_account_id: str,
        environment: str,
        profile_id: str,
    ) -> None:
        self._ledger_path = ledger_path
        self._trade_id_domain_ref = trade_id_domain_ref
        self._provider_account_id = provider_account_id
        self._environment = environment
        self._profile_id = profile_id
        self._forced_next_order_id: dict[str, str] = {}
        self._forced_next_trade_id: dict[str, str] = {}
        self._ensure_schema()

    def _ensure_schema(self) -> None:
        with sqlite3.connect(self._ledger_path) as connection:
            connection.execute(
                "CREATE TABLE IF NOT EXISTS provider_writes ("
                "sequence INTEGER PRIMARY KEY AUTOINCREMENT, "
                "id_namespace TEXT NOT NULL, client_order_id TEXT NOT NULL)"
            )
            connection.execute(
                "CREATE TABLE IF NOT EXISTS provider_orders ("
                "id_namespace TEXT NOT NULL, client_order_id TEXT NOT NULL, "
                "provider_order_id TEXT NOT NULL, instrument TEXT NOT NULL, "
                "provider_account_id TEXT NOT NULL, environment TEXT NOT NULL, "
                "profile_id TEXT NOT NULL, order_json TEXT NOT NULL, fills_json TEXT NOT NULL, "
                "PRIMARY KEY(id_namespace, client_order_id))"
            )
            connection.execute(
                "CREATE TABLE IF NOT EXISTS provider_sequences ("
                "id_namespace TEXT NOT NULL, instrument TEXT NOT NULL, "
                "kind TEXT NOT NULL, next_value INTEGER NOT NULL, "
                "PRIMARY KEY(id_namespace, instrument, kind))"
            )
            connection.execute(
                "CREATE TABLE IF NOT EXISTS provider_counters ("
                "id_namespace TEXT NOT NULL, name TEXT NOT NULL, count INTEGER NOT NULL, "
                "PRIMARY KEY(id_namespace, name))"
            )
            for name in ("order_queries", "fill_queries"):
                connection.execute(
                    "INSERT OR IGNORE INTO provider_counters(id_namespace,name,count) "
                    "VALUES (?, ?, 0)",
                    (self._id_namespace(), name),
                )

    def _id_namespace(self) -> str:
        return self._trade_id_domain_ref

    def force_next_trade_id(self, instrument: str, trade_id: str) -> None:
        self._forced_next_trade_id[instrument] = trade_id

    def force_next_order_id(self, instrument: str, order_id: str) -> None:
        self._forced_next_order_id[instrument] = order_id

    @staticmethod
    def _next_sequence(
        connection: sqlite3.Connection,
        *,
        namespace: str,
        instrument: str,
        kind: str,
    ) -> int:
        row = connection.execute(
            "SELECT next_value FROM provider_sequences "
            "WHERE id_namespace=? AND instrument=? AND kind=?",
            (namespace, instrument, kind),
        ).fetchone()
        value = 1 if row is None else int(row[0]) + 1
        connection.execute(
            "INSERT INTO provider_sequences(id_namespace,instrument,kind,next_value) "
            "VALUES (?, ?, ?, ?) ON CONFLICT(id_namespace,instrument,kind) "
            "DO UPDATE SET next_value=excluded.next_value",
            (namespace, instrument, kind, value),
        )
        return value

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
    ) -> dict[str, Any]:
        return {
            "account_id": account_id,
            "client_order_id": client_order_id,
            "fee_amount": "-0.05",
            "fee_currency": "USD",
            "fill_revision": 1,
            "order_id": order_id,
            "quantity": quantity,
            "side": side,
            "symbol": symbol,
            "trade_id": trade_id,
            "trade_price": price,
        }

    @staticmethod
    def _envelope(row: dict[str, Any]) -> dict[str, Any]:
        return {
            "account_id": row["account_id"],
            "client_order_id": row["client_order_id"],
            "complete": True,
            "fill_watermark": max(1, int(row["order_revision"])),
            "schema_version": _ORDER_SCHEMA,
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
        quantity_text = format(Decimal(str(volume)), "f")
        price_text = format(Decimal(str(price)), "f")
        side = order_type.split("-", 1)[0].upper()
        namespace = self._id_namespace()
        with sqlite3.connect(self._ledger_path) as connection:
            connection.execute("BEGIN IMMEDIATE")
            order_sequence = self._next_sequence(
                connection,
                namespace=namespace,
                instrument="*",
                kind="order",
            )
            trade_sequence = self._next_sequence(
                connection,
                namespace=namespace,
                instrument=symbol,
                kind="trade",
            )
            order_id = self._forced_next_order_id.pop(
                symbol, f"order-{order_sequence}"
            )
            trade_id = self._forced_next_trade_id.pop(symbol, f"trade-{trade_sequence}")
            row: dict[str, Any] = {
                "account_id": self._provider_account_id,
                "avg_price": price_text,
                "client_order_id": client_order_id,
                "filled": quantity_text,
                "order_id": order_id,
                "order_revision": 1,
                "price": price_text,
                "quantity": quantity_text,
                "side": side.lower(),
                "status": "filled",
                "symbol": symbol,
            }
            fills = [
                self._fill(
                    account_id=self._provider_account_id,
                    client_order_id=client_order_id,
                    order_id=order_id,
                    symbol=symbol,
                    side=side.lower(),
                    trade_id=trade_id,
                    quantity=quantity_text,
                    price=price_text,
                )
            ]
            encoded_order = json.dumps(row, sort_keys=True, separators=(",", ":"))
            encoded_fills = json.dumps(fills, sort_keys=True, separators=(",", ":"))
            connection.execute(
                "INSERT INTO provider_writes(id_namespace,client_order_id) VALUES (?, ?)",
                (namespace, client_order_id),
            )
            connection.execute(
                "INSERT INTO provider_orders(id_namespace,client_order_id,provider_order_id,"
                "instrument,provider_account_id,environment,profile_id,order_json,fills_json) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    namespace,
                    client_order_id,
                    order_id,
                    symbol,
                    self._provider_account_id,
                    self._environment,
                    self._profile_id,
                    encoded_order,
                    encoded_fills,
                ),
            )
        return self._envelope(row)

    def query_order(self, symbol: str, client_order_id: str, **_kwargs: Any) -> dict[str, Any]:
        namespace = self._id_namespace()
        with sqlite3.connect(self._ledger_path) as connection:
            connection.execute(
                "UPDATE provider_counters SET count=count+1 "
                "WHERE id_namespace=? AND name='order_queries'",
                (namespace,),
            )
            found = connection.execute(
                "SELECT order_json FROM provider_orders "
                "WHERE id_namespace=? AND client_order_id=?",
                (namespace, client_order_id),
            ).fetchone()
        if found is None:
            return {
                "account_id": self._provider_account_id,
                "client_order_id": client_order_id,
                "complete": True,
                "fill_watermark": 1,
                "schema_version": _ORDER_SCHEMA,
                "watermark": "missing-order",
                "data": [],
            }
        row = json.loads(found[0])
        if row["symbol"] != symbol:
            return {
                "account_id": self._provider_account_id,
                "client_order_id": client_order_id,
                "complete": True,
                "fill_watermark": 1,
                "schema_version": _ORDER_SCHEMA,
                "watermark": "wrong-instrument",
                "data": [],
            }
        return self._envelope(row)

    def get_deals(
        self,
        symbol: str | None,
        *,
        client_order_id: str,
        **_kwargs: Any,
    ) -> dict[str, Any]:
        namespace = self._id_namespace()
        with sqlite3.connect(self._ledger_path) as connection:
            connection.execute(
                "UPDATE provider_counters SET count=count+1 "
                "WHERE id_namespace=? AND name='fill_queries'",
                (namespace,),
            )
            found = connection.execute(
                "SELECT order_json,fills_json FROM provider_orders "
                "WHERE id_namespace=? AND client_order_id=?",
                (namespace, client_order_id),
            ).fetchone()
        if found is None:
            return {
                "account_id": self._provider_account_id,
                "client_order_id": client_order_id,
                "complete": True,
                "fill_watermark": 1,
                "schema_version": _FILL_SCHEMA,
                "watermark": "missing-fills",
                "data": [],
            }
        row = json.loads(found[0])
        fills = json.loads(found[1])
        if symbol is not None and row["symbol"] != symbol:
            fills = []
        return {
            "account_id": self._provider_account_id,
            "client_order_id": client_order_id,
            "complete": True,
            "fill_watermark": int(row["order_revision"]),
            "schema_version": _FILL_SCHEMA,
            "watermark": f"fills:{client_order_id}:{row['order_revision']}",
            "data": fills,
        }


def _endpoint() -> str:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as probe:
        probe.bind(("127.0.0.1", 0))
        return f"tcp://127.0.0.1:{probe.getsockname()[1]}"


def _service_config(endpoint: str) -> dict[str, Any]:
    return {
        "service_config_schema_version": 1,
        "process_role": "service",
        "service_id": _SERVICE_ID,
        "scope_ref": _SERVICE_SCOPE_REF,
        "transport_ref": _TRANSPORT_LISTENER,
        "registration_policy_ref": "dim01-registration-policy-v1",
        "service_requirements_ref": "dim01-service-requirements-v1",
        "components": {
            "risk": {
                "config_schema_version": 2,
                "settings": {
                    "instance_id": _SERVICE_INSTANCE,
                    "bind_ports": ["service.order_entry", "service.order_query"],
                    "options": {
                        "role": "account_owner",
                        "account_policy_ref": "dim01-risk-policy-v1",
                    },
                },
            }
        },
        "communication": {
            "config_schema_version": 1,
            "profiles": {
                _TRANSPORT_LISTENER: {
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


def _client_config(endpoint: str, security_ref: str, strategy_ref: str, scopes: tuple[str, ...]):
    bindings = []
    for scope_ref in scopes:
        route = _ROUTES[scope_ref]
        for mount in ("broker.orders", "broker.order_query"):
            bindings.append(
                {
                    "mount": mount,
                    "service_id": _SERVICE_ID,
                    "instance_id": _SERVICE_INSTANCE,
                    "scope_ref": route["scope_ref"],
                    "transport_ref": _TRANSPORT_CONNECT,
                    "account_ref": route["account_ref"],
                }
            )
    return parse_client_config(
        {
            "client_config_schema_version": 1,
            "process_role": "strategy_client",
            "registration": {"strategy_ref": strategy_ref},
            "clients": {
                "execution": {
                    "config_schema_version": 1,
                    "settings": {
                        "bindings": bindings,
                        "options": {"request_timeout_ms": _REQUEST_TIMEOUT_MS},
                    },
                }
            },
            "communication": {
                "config_schema_version": 1,
                "profiles": {
                    _TRANSPORT_CONNECT: {
                        "backend": "zmq",
                        "config_schema_version": 1,
                        "endpoints": {"rpc_connect": endpoint},
                        "security_profile_ref": security_ref,
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


def _authority(config, keys: dict[str, bytes]) -> TrustedServiceAuthority:
    grants_by_principal: dict[str, PrincipalGrant] = {}
    for strategy_ref, principal_ref, route_refs in (
        (
            _STRATEGY_A,
            _PRINCIPAL_A,
            (_ROUTE_SPOT_A, _ROUTE_SPOT_A_ACCOUNT_MODE, _ROUTE_SPOT_B, _ROUTE_PERP_A),
        ),
        (_STRATEGY_B, _PRINCIPAL_B, (_ROUTE_SPOT_A_STRATEGY_B,)),
    ):
        grants = []
        for route_ref in route_refs:
            route = _ROUTES[route_ref]
            for port, operation in (
                ("service.order_entry", "orders.submit"),
                ("service.order_query", "orders.query"),
            ):
                grants.append(
                    ResourceGrant(
                        _SERVICE_INSTANCE,
                        port,
                        route["scope_ref"],
                        _SCOPES[route["scope_ref"]],
                        frozenset({operation}),
                    )
                )
        grants_by_principal[principal_ref] = (
            PrincipalGrant(
                WireIdentity(WireRole.STRATEGY_CLIENT, principal_ref, strategy_ref),
                tuple(grants),
            ),
        )
    identity = WireIdentity(WireRole.SERVICE, _SERVICE_PRINCIPAL, _SERVICE_ID)
    return TrustedServiceAuthority(
        identity=identity,
        scope=WireScope(_DOMAIN),
        scope_ref=config.scope_ref,
        scope_version="dim01-scope-v1",
        security_ref=_SERVER_SECURITY_REF,
        security_version="curve-key-v1",
        registration_policy_ref=config.registration_policy_ref,
        registration_policy_version="dim01-registration-policy-v1",
        service_requirements_ref=config.service_requirements_ref,
        service_requirements_version="dim01-service-requirements-v1",
        curve_public_key=keys["server_public"],
        curve_secret_key=keys["server_secret"],
        principals={
            keys["strategy_a_public"]: _PRINCIPAL_A,
            keys["strategy_b_public"]: _PRINCIPAL_B,
        },
        grants=grants_by_principal,
    )


def _metadata(instrument: str, version: str, *, multiplier: str) -> InstrumentRiskMetadata:
    now_ns = time.time_ns()
    unit = "BTC" if instrument == _BTC else "ETH"
    return InstrumentRiskMetadata(
        instrument=instrument,
        metadata_version=version,
        as_of_ns=now_ns - 1_000_000_000,
        expires_at_ns=now_ns + 3_600_000_000_000,
        tick_size=Decimal("0.01"),
        quantity_step=Decimal("0.001"),
        contract_multiplier=Decimal(multiplier),
        max_gross_notional=Decimal("100000"),
        taker_fee_bps=Decimal("0"),
        fixed_fee=Decimal("0"),
        max_slippage_bps=Decimal("0"),
        quantity_unit=unit,
        valuation_unit="USD",
    )


def _provider_contract(
    exchange_name: str,
    profile_id: str,
    *,
    trade_id_domain_ref: str,
    trade_id_scope: str,
) -> RiskBtApiProviderContract:
    return RiskBtApiProviderContract(
        exchange_name=exchange_name,
        profile_id=profile_id,
        order_schema=_ORDER_SCHEMA,
        fill_schema=_FILL_SCHEMA,
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
        quote_currency_by_instrument=((_BTC, "USD"), (_ETH, "USD")),
        trade_id_domain_ref=trade_id_domain_ref,
        trade_id_scope=trade_id_scope,
    )


def _install_baseline_and_allocations(
    gate: DurableRiskGate,
    journal: RiskOrderJournal,
) -> None:
    strategy_by_account = (
        (_ACCOUNT_SPOT_A, (_STRATEGY_A, _STRATEGY_B)),
        (_ACCOUNT_SPOT_B, (_STRATEGY_A,)),
        (_ACCOUNT_PERP_A, (_STRATEGY_A,)),
    )
    for account_scope, strategies in strategy_by_account:
        # The baseline install requires an explicit gross allocation for every
        # strategy it declares, so allocations are pinned first.
        for strategy_ref in strategies:
            gate.set_strategy_allocation(
                account_scope,
                strategy_ref,
                f"dim01-allocation-{account_scope.account_id}-{account_scope.environment}-{strategy_ref}",
                max_notional=Decimal("100000"),
                notional_unit="USD",
                max_gross_occupancy_notional=Decimal("100000"),
                max_gross_occupancy_intent_count=100,
            )
        observed_at = time.time_ns()
        snapshot = {
            "account_revision": 1,
            "account_scope": {
                "account_id": account_scope.account_id,
                "environment": account_scope.environment,
                "provider": account_scope.provider,
            },
            "complete_open_orders": True,
            "complete_positions": True,
            "notional_unit": "USD",
            "observed_at_ns": observed_at,
            "open_orders": [],
            "positions": [
                {
                    "active_increase_intent_count": 0,
                    "gross_notional": "0",
                    "notional_unit": "USD",
                    "source_record_sha256": "1" * 64,
                    "strategy_id": strategy_ref,
                    "valuation_facts_sha256": "2" * 64,
                }
                for strategy_ref in strategies
            ],
            "source_schema_version": "dim01-synthetic-account-baseline-v1",
            "valid_until_ns": observed_at + 3_600_000_000_000,
            "valuation_facts": [],
        }
        with journal.writer_fence(account_scope) as fence:
            baseline = journal.ingest_gross_exposure_baseline(
                fence,
                source_snapshot=snapshot,
            )
            gate.install_gross_exposure_baseline(baseline)


def _host_process(
    control,
    raw_config: dict[str, Any],
    registry_path: str,
    key_material: dict[str, bytes],
    risk_path: str,
    journal_path: str,
    provider_path: str,
) -> None:
    config = parse_service_config(raw_config)
    bt_api = None
    journal = None
    gate = None
    host = None
    feeds: dict[str, _Dim01SyntheticV2Feed] = {}
    try:
        feed_specs = (
            (
                _EXCHANGE_SPOT_A,
                _TRADE_DOMAIN_SPOT_A,
                "provider-account-a",
                "simulation",
                _PROFILE_SPOT,
            ),
            (
                _EXCHANGE_SPOT_A_ALIAS,
                # The alias and the rotated profile keep the same stable
                # server-owned domain: profile versions must not open a new
                # identity space for the same provider account/product.
                _TRADE_DOMAIN_SPOT_A,
                "provider-account-a",
                "simulation",
                _PROFILE_SPOT_ROTATED,
            ),
            (
                _EXCHANGE_SPOT_B,
                _TRADE_DOMAIN_SPOT_B,
                "provider-account-b",
                "simulation",
                _PROFILE_SPOT,
            ),
            (
                _EXCHANGE_PERP_A,
                _TRADE_DOMAIN_PERP_A,
                "provider-account-a",
                "simulation-perpetual",
                _PROFILE_PERP,
            ),
        )
        for exchange_name, domain_ref, provider_account_id, environment, profile_id in feed_specs:
            feeds[exchange_name] = _Dim01SyntheticV2Feed(
                provider_path,
                trade_id_domain_ref=domain_ref,
                provider_account_id=provider_account_id,
                environment=environment,
                profile_id=profile_id,
            )

        journal = RiskOrderJournal(journal_path, writer_id="dim01-risk-journal-owner")
        gate = DurableRiskGate(
            risk_path,
            RiskPolicy(
                "dim01-risk-policy-v1",
                Decimal("100000"),
                max_increase_count=100,
                require_strategy_allocation=True,
                notional_unit="USD",
                exposure_accounting_mode=ExposureAccountingMode.LIVE_GROSS,
                max_gross_occupancy_notional=Decimal("100000"),
                max_gross_occupancy_intent_count=100,
                max_exposure_facts_age_seconds=3600,
            ),
            execution_journal_authority=journal,
            gross_exposure_authority=journal,
        )
        _install_baseline_and_allocations(gate, journal)

        registry_spot_a = InstrumentRiskRegistry(
            (
                _metadata(_BTC, _VERSION_BTC_SPOT, multiplier="1"),
                _metadata(_ETH, _VERSION_ETH_SPOT, multiplier="2"),
            )
        )
        registry_spot_b = InstrumentRiskRegistry(
            (_metadata(_BTC, _VERSION_BTC_SPOT, multiplier="1"),)
        )
        registry_perp_a = InstrumentRiskRegistry(
            (_metadata(_BTC, _VERSION_BTC_PERP, multiplier="10"),)
        )
        account_scope_by_route = {
            _ROUTE_SPOT_A: _ACCOUNT_SPOT_A,
            _ROUTE_SPOT_A_STRATEGY_B: _ACCOUNT_SPOT_A,
            _ROUTE_SPOT_A_ACCOUNT_MODE: _ACCOUNT_SPOT_A,
            _ROUTE_SPOT_B: _ACCOUNT_SPOT_B,
            _ROUTE_PERP_A: _ACCOUNT_PERP_A,
        }
        registry_by_route = {
            _ROUTE_SPOT_A: registry_spot_a,
            _ROUTE_SPOT_A_STRATEGY_B: registry_spot_a,
            _ROUTE_SPOT_A_ACCOUNT_MODE: registry_spot_a,
            _ROUTE_SPOT_B: registry_spot_b,
            _ROUTE_PERP_A: registry_perp_a,
        }
        target_by_route: dict[str, RiskOrderTarget] = {}
        admission_by_route: dict[str, DurableRiskAdmissionAdapter] = {}
        contract_by_route: dict[str, RiskBtApiProviderContract] = {}
        for route_ref, route in _ROUTES.items():
            account_scope = account_scope_by_route[route_ref]
            registry = registry_by_route[route_ref]
            target_by_route[route_ref] = RiskOrderTarget(
                account_scope=account_scope,
                strategy_id=route["strategy_ref"],
                exchange_name=route["exchange_name"],
                provider_account_id=route["provider_account_id"],
                registry=registry,
            )
            mapper = InstrumentRiskAdmissionMapper(
                account_scope,
                registry,
                clock_ns=time.time_ns,
                allocation_reader=lambda execution_scope, selected_scope=account_scope: (
                    gate.get_strategy_allocation(selected_scope, execution_scope.strategy_id)
                ),
            )
            admission_by_route[route_ref] = DurableRiskAdmissionAdapter(gate, mapper)
            contract_by_route[route_ref] = _provider_contract(
                route["exchange_name"],
                route["profile_id"],
                trade_id_domain_ref=route["trade_id_domain_ref"],
                trade_id_scope=route["trade_id_scope"],
            )

        bt_api = BtApi(debug=False)
        for exchange_name, feed in feeds.items():
            bt_api.exchange_feeds[exchange_name] = feed

        targets = {
            _SCOPES[route["scope_ref"]]: target_by_route[route_ref]
            for route_ref, route in _ROUTES.items()
        }
        admissions = {
            _SCOPES[route["scope_ref"]]: admission_by_route[route_ref]
            for route_ref, route in _ROUTES.items()
        }
        contracts = {
            _SCOPES[route["scope_ref"]]: contract_by_route[route_ref]
            for route_ref, route in _ROUTES.items()
        }

        def resource_provider(component, _instance_id, _options, _scope):
            if component != "risk":
                raise AssertionError("unexpected component selected by DIM01 service")
            return ComponentResources.borrowed(
                {
                    "bt_api": bt_api,
                    "targets": targets,
                    "admission": admissions,
                    "journal": journal,
                    "gate": gate,
                    "provider_contracts": contracts,
                },
                ready=lambda: True,
            )

        host = ServiceHost(
            config,
            _authority(config, key_material),
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
            if command == ("provider_write_count",):
                with sqlite3.connect(provider_path) as connection:
                    count = int(
                        connection.execute("SELECT count(*) FROM provider_writes").fetchone()[0]
                    )
                control.send(("provider_write_count", count))
                continue
            if command[0] == "force_trade_id":
                exchange_name, instrument, trade_id = command[1:]
                feeds[exchange_name].force_next_trade_id(instrument, trade_id)
                control.send(("forced_trade_id", exchange_name, instrument, trade_id))
                continue
            if command[0] == "force_order_id":
                exchange_name, instrument, order_id = command[1:]
                feeds[exchange_name].force_next_order_id(instrument, order_id)
                control.send(("forced_order_id", exchange_name, instrument, order_id))
                continue
            if command == ("close",):
                host.close()
                gate.close()
                bt_api.close()
                control.send(("closed", host.state))
                return
            raise AssertionError("unknown DIM01 owner control")
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


def _client_process(
    control,
    endpoint: str,
    strategy_public: bytes,
    strategy_secret: bytes,
    server_public: bytes,
    strategy_ref: str,
    principal_ref: str,
    route_refs: tuple[str, ...],
) -> None:
    runtime = None
    try:
        # The service runtime resolves scopes by the binding's scope_ref, so the
        # map is keyed by scope reference, not by route reference.
        scope_map = {
            _ROUTES[ref]["scope_ref"]: _SCOPES[_ROUTES[ref]["scope_ref"]]
            for ref in route_refs
        }
        config = _client_config(endpoint, _TRANSPORT_CONNECT, strategy_ref, route_refs)
        identity = WireIdentity(WireRole.STRATEGY_CLIENT, principal_ref, strategy_ref)
        peer = WireIdentity(WireRole.SERVICE, _SERVICE_PRINCIPAL, _SERVICE_ID)
        runtime = ServiceClientRuntime(
            config,
            identity=identity,
            scope_resolver=lambda scope_ref: scope_map[scope_ref],
            peer_resolver=lambda _service_id: peer,
            transport_factory=ZmqTransportFactory(
                {
                    _TRANSPORT_CONNECT: ClientCurveKeys(
                        server_public,
                        strategy_public,
                        strategy_secret,
                    )
                }
            ),
        )
        startup = runtime.start()
        assert startup.degraded_count == 0
        clients = {
            route_ref: ServiceExecutionClient(
                runtime,
                "broker.orders",
                scope_ref=_ROUTES[route_ref]["scope_ref"],
                query_mount="broker.order_query",
                query_scope_ref=_ROUTES[route_ref]["scope_ref"],
            )
            for route_ref in route_refs
        }
        control.send(("ready", runtime.run_id))
        while True:
            command = control.recv()
            if command == ("close",):
                runtime.close()
                control.send(("closed", runtime.state))
                return
            if command[0] == "submit":
                _tag, route_ref, intent_id, instrument, metadata_version, quantity, price = command
                try:
                    view = clients[route_ref].submit(
                        intent_id=intent_id,
                        signal_id="signal-" + intent_id,
                        instrument=instrument,
                        side="BUY",
                        position_effect="OPEN",
                        order_type="LIMIT",
                        quantity=quantity,
                        price=price,
                        time_in_force="GTC",
                        reduce_only=False,
                        metadata_version=metadata_version,
                        deadline_ns=None,
                        tags={},
                    )
                except ServiceExecutionError as error:
                    record = error.record
                    control.send(
                        (
                            "submit_error",
                            error.code,
                            None
                            if record is None
                            else {
                                "state": record.state,
                                "provider_order_id": record.provider_order_id,
                                "filled_quantity": str(record.filled_quantity),
                                "average_price": (
                                    None
                                    if record.average_price is None
                                    else str(record.average_price)
                                ),
                                "dispatch_attempts": record.dispatch_attempts,
                            },
                        )
                    )
                else:
                    control.send(
                        (
                            "submitted",
                            {
                                "intent_id": view.intent_id,
                                "state": view.state,
                                "provider_order_id": view.provider_order_id,
                                "filled_quantity": str(view.filled_quantity),
                                "average_price": (
                                    None
                                    if view.average_price is None
                                    else str(view.average_price)
                                ),
                                "permit_reference": view.permit_reference,
                                "dispatch_attempts": view.dispatch_attempts,
                            },
                        )
                    )
                continue
            if command[0] == "query":
                _tag, route_ref, intent_id = command
                try:
                    view = clients[route_ref].query(intent_id)
                except ServiceExecutionError as error:
                    control.send(("query_error", error.code))
                else:
                    control.send(
                        (
                            "queried",
                            None
                            if view is None
                            else {
                                "intent_id": view.intent_id,
                                "state": view.state,
                                "provider_order_id": view.provider_order_id,
                                "filled_quantity": str(view.filled_quantity),
                                "average_price": (
                                    None
                                    if view.average_price is None
                                    else str(view.average_price)
                                ),
                                "dispatch_attempts": view.dispatch_attempts,
                            },
                        )
                    )
                continue
            raise AssertionError("unknown DIM01 strategy control")
    except BaseException as exc:
        if runtime is not None:
            with suppress(BaseException):
                runtime.close()
        with suppress(Exception):
            control.send(("error", getattr(exc, "code", type(exc).__name__)))
        raise
    finally:
        control.close()


def _plain_snapshot(database_path: str) -> tuple[dict[str, Any], ...]:
    uri = Path(database_path).resolve().as_uri() + "?mode=ro"
    with sqlite3.connect(uri, uri=True) as connection:
        provider_rows = connection.execute(
            "SELECT id_namespace,client_order_id,provider_order_id,instrument,"
            "provider_account_id,environment,profile_id,order_json,fills_json "
            "FROM provider_orders ORDER BY id_namespace,client_order_id"
        ).fetchall()
    result = []
    for row in provider_rows:
        order = json.loads(row[7])
        fills = json.loads(row[8])
        result.append(
            {
                "id_namespace": str(row[0]),
                "client_order_id": str(row[1]),
                "provider_order_id": str(row[2]),
                "instrument": str(row[3]),
                "provider_account_id": str(row[4]),
                "environment": str(row[5]),
                "profile_id": str(row[6]),
                "trade_ids": tuple(str(item["trade_id"]) for item in fills),
                "order": order,
            }
        )
    return tuple(result)


def _risk_fill_rows(database_path: str) -> tuple[dict[str, Any], ...]:
    uri = Path(database_path).resolve().as_uri() + "?mode=ro"
    with sqlite3.connect(uri, uri=True) as connection:
        connection.row_factory = sqlite3.Row
        rows = connection.execute(
            "SELECT order_row.scope_key,order_row.strategy_id,order_row.intent_id,"
            "order_row.provider_order_id,order_row.instrument_id,fill.trade_id,"
            "fill.fact_sha256,fill.quantity,fill.price,fill.fee_amount,fill.fee_currency "
            "FROM risk_order_exposure_fills AS fill "
            "JOIN risk_order_exposures AS order_row USING (permit_id) "
            "ORDER BY order_row.scope_key,order_row.instrument_id,fill.trade_id,"
            "order_row.intent_id"
        ).fetchall()
    return tuple(dict(row) for row in rows)


def _risk_dispatch_latch(database_path: str, intent_id: str) -> dict[str, Any]:
    """Read the exact unresolved claim/freeze for an uncertain provider result."""

    uri = Path(database_path).resolve().as_uri() + "?mode=ro"
    with sqlite3.connect(uri, uri=True) as connection:
        connection.row_factory = sqlite3.Row
        row = connection.execute(
            "SELECT reservation.permit_id,reservation.status,claim.cause_id,"
            "resolution.permit_id AS resolved_permit_id,freeze.active AS freeze_active "
            "FROM risk_reservations AS reservation "
            "LEFT JOIN risk_dispatch_claims AS claim USING (permit_id) "
            "LEFT JOIN risk_dispatch_resolutions AS resolution USING (permit_id) "
            "LEFT JOIN risk_freezes AS freeze ON freeze.scope_key=reservation.scope_key "
            "AND freeze.cause_id=claim.cause_id "
            "WHERE reservation.intent_id=?",
            (intent_id,),
        ).fetchall()
    assert len(rows := tuple(dict(item) for item in row)) == 1, (intent_id, rows)
    return rows[0]


def _risk_intent_id(
    database_path: str,
    *,
    account_scope: AccountScope,
    principal_ref: str,
    intent_id: str,
) -> str:
    """Map the public business ID to the journal's durable namespaced ID."""

    uri = Path(database_path).resolve().as_uri() + "?mode=ro"
    with sqlite3.connect(uri, uri=True) as connection:
        rows = connection.execute(
            "SELECT risk_intent_id FROM risk_order_commands "
            "WHERE scope_key=? AND principal_ref=? AND intent_id=?",
            (account_scope.key, principal_ref, intent_id),
        ).fetchall()
    assert len(rows) == 1, (account_scope.key, principal_ref, intent_id, rows)
    return str(rows[0][0])


def _metadata_version_for(route_ref: str, instrument: str) -> str:
    if route_ref == _ROUTE_PERP_A:
        return _VERSION_BTC_PERP
    if instrument == _BTC:
        return _VERSION_BTC_SPOT
    return _VERSION_ETH_SPOT


def test_dim01_identity_namespaces_do_not_cross_route_or_double_credit(tmp_path) -> None:
    """Exercise all DIM01 axes through trusted targets and persisted Risk facts."""

    context = multiprocessing.get_context("spawn")
    endpoint = _endpoint()
    server_public, server_secret = zmq.curve_keypair()
    strategy_a_public, strategy_a_secret = zmq.curve_keypair()
    strategy_b_public, strategy_b_secret = zmq.curve_keypair()
    key_material = {
        "server_public": server_public,
        "server_secret": server_secret,
        "strategy_a_public": strategy_a_public,
        "strategy_b_public": strategy_b_public,
    }
    paths = {
        "registry": str(tmp_path / "dim01-registry.sqlite3"),
        "risk": str(tmp_path / "dim01-risk.sqlite3"),
        "journal": str(tmp_path / "dim01-journal.sqlite3"),
        "provider": str(tmp_path / "dim01-provider.sqlite3"),
    }
    host = None
    clients: list[dict[str, Any]] = []
    forced: list[str] = []
    failures: list[str] = []
    try:
        host = _start_process(
            context,
            _host_process,
            (
                _service_config(endpoint),
                paths["registry"],
                key_material,
                paths["risk"],
                paths["journal"],
                paths["provider"],
            ),
            "dim01-risk-owner-host",
        )
        host_ready = _receive(host["pipe"])
        if host_ready[0] == "error":
            process = host["process"]
            process.join(15)
            assert not process.is_alive(), "failed DIM01 ServiceHost did not exit"
            exit_code = process.exitcode
            host["pipe"].close()
            process.close()
            host["closed"] = True
            pytest.fail(f"DIM01 ServiceHost startup returned {host_ready!r} (exit={exit_code})")
        assert host_ready == ("ready", endpoint, "started"), host_ready

        strategy_a = _start_process(
            context,
            _client_process,
            (
                endpoint,
                strategy_a_public,
                strategy_a_secret,
                server_public,
                _STRATEGY_A,
                _PRINCIPAL_A,
                (
                    _ROUTE_SPOT_A,
                    _ROUTE_SPOT_A_ACCOUNT_MODE,
                    _ROUTE_SPOT_B,
                    _ROUTE_PERP_A,
                ),
            ),
            "dim01-strategy-a-client",
        )
        clients.append(strategy_a)
        strategy_a_ready = _receive(strategy_a["pipe"])
        assert strategy_a_ready[0] == "ready", strategy_a_ready
        assert strategy_a_ready[1]

        strategy_b = _start_process(
            context,
            _client_process,
            (
                endpoint,
                strategy_b_public,
                strategy_b_secret,
                server_public,
                _STRATEGY_B,
                _PRINCIPAL_B,
                (_ROUTE_SPOT_A_STRATEGY_B,),
            ),
            "dim01-strategy-b-client",
        )
        clients.append(strategy_b)
        strategy_b_ready = _receive(strategy_b["pipe"])
        assert strategy_b_ready[0] == "ready", strategy_b_ready
        assert strategy_b_ready[1]

        intent_id = "dim01-same-business-id"
        spot_btc = _client_command(
            strategy_a,
            "submit",
            _ROUTE_SPOT_A,
            intent_id,
            _BTC,
            _VERSION_BTC_SPOT,
            "1",
            "20",
        )
        assert spot_btc[0] == "submitted" and spot_btc[1]["state"] == "FILLED", spot_btc
        assert spot_btc[1]["provider_order_id"] == "order-1"
        assert spot_btc[1]["filled_quantity"] == "1"

        spot_eth = _client_command(
            strategy_a,
            "submit",
            _ROUTE_SPOT_A,
            "dim01-same-strategy-eth",
            _ETH,
            _VERSION_ETH_SPOT,
            "2",
            "10",
        )
        assert spot_eth[0] == "submitted" and spot_eth[1]["state"] == "FILLED", spot_eth
        assert spot_eth[1]["provider_order_id"] == "order-2"

        # A second strategy on the same provider account has its own trusted
        # allocation while sharing the provider account/product namespace.
        strategy_b_btc = _client_command(
            strategy_b,
            "submit",
            _ROUTE_SPOT_A_STRATEGY_B,
            intent_id,
            _BTC,
            _VERSION_BTC_SPOT,
            "0.25",
            "40",
        )
        assert strategy_b_btc[0] == "submitted", strategy_b_btc
        assert strategy_b_btc[1]["state"] == "FILLED", strategy_b_btc
        assert strategy_b_btc[1]["provider_order_id"] == "order-3"

        # A third permit in this exact provider/account/environment/profile/
        # instrument namespace receives a deliberately reused raw trade ID
        # with different order/client/quantity/price facts. It must not receive
        # a second credit for strategy A's existing trade-1.
        collision_intent_id = "dim01-strategy-b-trade-id-collision"
        forced_trade = _client_command(
            host, "force_trade_id", _EXCHANGE_SPOT_A_ALIAS, _BTC, "trade-1"
        )
        assert forced_trade == (
            "forced_trade_id",
            _EXCHANGE_SPOT_A_ALIAS,
            _BTC,
            "trade-1",
        ), forced_trade
        same_namespace_collision = _client_command(
            strategy_b,
            "submit",
            _ROUTE_SPOT_A_STRATEGY_B,
            collision_intent_id,
            _BTC,
            _VERSION_BTC_SPOT,
            "0.5",
            "21",
        )
        assert same_namespace_collision[0] in {"submitted", "submit_error"}, (
            same_namespace_collision
        )
        collision_view = (
            same_namespace_collision[1]
            if same_namespace_collision[0] == "submitted"
            else same_namespace_collision[2]
        )
        assert collision_view is not None, same_namespace_collision
        assert collision_view["state"] == "UNKNOWN", same_namespace_collision
        assert collision_view["dispatch_attempts"] == 1, same_namespace_collision
        assert _client_command(host, "provider_write_count")[1] == 4

        # Reuse a provider order ID already accepted in this exact account /
        # product identity domain. The provider still writes a FILLED order,
        # but Risk must preserve UNKNOWN and the dispatch latch instead of
        # converting an identity conflict into a clean rejection.
        order_collision_intent_id = "dim01-strategy-b-order-id-collision"
        forced_order = _client_command(
            host,
            "force_order_id",
            _EXCHANGE_SPOT_A_ALIAS,
            _BTC,
            "order-1",
        )
        assert forced_order == (
            "forced_order_id",
            _EXCHANGE_SPOT_A_ALIAS,
            _BTC,
            "order-1",
        ), forced_order
        same_order_namespace_collision = _client_command(
            strategy_b,
            "submit",
            _ROUTE_SPOT_A_STRATEGY_B,
            order_collision_intent_id,
            _BTC,
            _VERSION_BTC_SPOT,
            "0.75",
            "22",
        )
        assert same_order_namespace_collision[0] in {"submitted", "submit_error"}, (
            same_order_namespace_collision
        )
        order_collision_view = (
            same_order_namespace_collision[1]
            if same_order_namespace_collision[0] == "submitted"
            else same_order_namespace_collision[2]
        )
        assert order_collision_view is not None, same_order_namespace_collision
        assert order_collision_view["state"] == "UNKNOWN", same_order_namespace_collision
        assert order_collision_view["dispatch_attempts"] == 1, (
            same_order_namespace_collision
        )
        assert _client_command(host, "provider_write_count")[1] == 5

        # The stable domain was fixed to INSTRUMENT scope by its first accepted
        # trade. A second trusted contract for the same provider account/product
        # re-declaring the same domain as ACCOUNT scope is a contradiction, not
        # a new identity space: the provider write still happens, but Risk must
        # fail closed, keep UNKNOWN and credit nothing.
        mode_change_intent_id = "dim01-strategy-a-domain-mode-change"
        mode_change = _client_command(
            strategy_a,
            "submit",
            _ROUTE_SPOT_A_ACCOUNT_MODE,
            mode_change_intent_id,
            _BTC,
            _VERSION_BTC_SPOT,
            "0.5",
            "18",
        )
        assert mode_change[0] in {"submitted", "submit_error"}, mode_change
        mode_change_view = (
            mode_change[1] if mode_change[0] == "submitted" else mode_change[2]
        )
        assert mode_change_view is not None, mode_change
        assert mode_change_view["state"] == "UNKNOWN", mode_change
        assert mode_change_view["dispatch_attempts"] == 1, mode_change
        assert _client_command(host, "provider_write_count")[1] == 6

        # Same venue and same bare IDs are valid for another provider account.
        spot_other_account = _client_command(
            strategy_a,
            "submit",
            _ROUTE_SPOT_B,
            intent_id,
            _BTC,
            _VERSION_BTC_SPOT,
            "2",
            "31",
        )
        assert (
            spot_other_account[0] == "submitted"
            and spot_other_account[1]["state"] == "FILLED"
            and spot_other_account[1]["provider_order_id"] == "order-1"
        ), spot_other_account

        # Same display symbol and provider/account, but the trusted target
        # selects a different environment/product contract and metadata.
        perp_same_display_name = _client_command(
            strategy_a,
            "submit",
            _ROUTE_PERP_A,
            intent_id,
            _BTC,
            _VERSION_BTC_PERP,
            "3",
            "17",
        )
        assert (
            perp_same_display_name[0] == "submitted"
            and perp_same_display_name[1]["state"] == "FILLED"
            and perp_same_display_name[1]["provider_order_id"] == "order-1"
        ), perp_same_display_name

        # Same intent ID remains scoped by trusted strategy/account/product.
        for process, route_ref, scoped_intent, expected in (
            (
                strategy_a,
                _ROUTE_SPOT_A,
                intent_id,
                ("order-1", "1", "20"),
            ),
            (
                strategy_b,
                _ROUTE_SPOT_A_STRATEGY_B,
                intent_id,
                ("order-3", "0.25", "40"),
            ),
            (
                strategy_a,
                _ROUTE_SPOT_B,
                intent_id,
                ("order-1", "2", "31"),
            ),
            (
                strategy_a,
                _ROUTE_PERP_A,
                intent_id,
                ("order-1", "3", "17"),
            ),
        ):
            queried = _client_command(process, "query", route_ref, scoped_intent)
            assert queried[0] == "queried", queried
            assert queried[1]["state"] == "FILLED", queried
            assert queried[1]["provider_order_id"] == expected[0], queried
            assert queried[1]["filled_quantity"] == expected[1], queried
            assert queried[1]["average_price"] == expected[2], queried
            assert queried[1]["dispatch_attempts"] == 1, queried

        collision_query = _client_command(
            strategy_b, "query", _ROUTE_SPOT_A_STRATEGY_B, collision_intent_id
        )
        assert collision_query[0] == "queried", collision_query
        assert collision_query[1] is not None, collision_query
        assert collision_query[1]["state"] == "UNKNOWN", collision_query
        assert collision_query[1]["dispatch_attempts"] == 1, collision_query
        order_collision_query = _client_command(
            strategy_b, "query", _ROUTE_SPOT_A_STRATEGY_B, order_collision_intent_id
        )
        assert order_collision_query[0] == "queried", order_collision_query
        assert order_collision_query[1] is not None, order_collision_query
        assert order_collision_query[1]["state"] == "UNKNOWN", order_collision_query
        assert order_collision_query[1]["dispatch_attempts"] == 1, order_collision_query
        mode_change_query = _client_command(
            strategy_a, "query", _ROUTE_SPOT_A_ACCOUNT_MODE, mode_change_intent_id
        )
        assert mode_change_query[0] == "queried", mode_change_query
        assert mode_change_query[1] is not None, mode_change_query
        assert mode_change_query[1]["state"] == "UNKNOWN", mode_change_query
        assert mode_change_query[1]["dispatch_attempts"] == 1, mode_change_query

        # Each provider response was FILLED, but the duplicate identifiers and
        # the contradicting domain mode leave all Risk claims unresolved and
        # keep their exact occupancy freezes active. No fill from the collisions
        # or the mode change may be credited.
        trade_collision_risk_id = _risk_intent_id(
            paths["journal"],
            account_scope=_ACCOUNT_SPOT_A,
            principal_ref=_PRINCIPAL_B,
            intent_id=collision_intent_id,
        )
        order_collision_risk_id = _risk_intent_id(
            paths["journal"],
            account_scope=_ACCOUNT_SPOT_A,
            principal_ref=_PRINCIPAL_B,
            intent_id=order_collision_intent_id,
        )
        mode_change_risk_id = _risk_intent_id(
            paths["journal"],
            account_scope=_ACCOUNT_SPOT_A,
            principal_ref=_PRINCIPAL_A,
            intent_id=mode_change_intent_id,
        )
        for risk_intent_id in (
            trade_collision_risk_id,
            order_collision_risk_id,
            mode_change_risk_id,
        ):
            dispatch_latch = _risk_dispatch_latch(paths["risk"], risk_intent_id)
            assert dispatch_latch["cause_id"] == "dispatch-inflight:" + risk_intent_id
            assert dispatch_latch["resolved_permit_id"] is None
            assert dispatch_latch["freeze_active"] == 1

        # Replaying a known terminal command and querying its durable record
        # cannot dispatch or credit the same synthetic event again.
        duplicate_submit = _client_command(
            strategy_a,
            "submit",
            _ROUTE_SPOT_A,
            intent_id,
            _BTC,
            _VERSION_BTC_SPOT,
            "1",
            "20",
        )
        assert duplicate_submit[0] == "submitted", duplicate_submit
        assert duplicate_submit[1]["state"] == "FILLED"
        assert duplicate_submit[1]["provider_order_id"] == spot_btc[1]["provider_order_id"]
        assert duplicate_submit[1]["dispatch_attempts"] == spot_btc[1]["dispatch_attempts"] == 1
        duplicate_query = _client_command(strategy_a, "query", _ROUTE_SPOT_A, intent_id)
        assert duplicate_query[0] == "queried" and duplicate_query[1]["state"] == "FILLED"
        assert duplicate_query[1]["dispatch_attempts"] == 1
        assert _client_command(host, "provider_write_count")[1] == 8

        _close_normally(strategy_b, expected_tag="closed")
        _close_normally(strategy_a, expected_tag="closed")
        _close_normally(host, expected_tag="closed")

        provider_rows = _plain_snapshot(paths["provider"])
        assert len(provider_rows) == 8, provider_rows
        # The provider row's natural key is the declared identity namespace
        # plus the client order ID, not either column alone.
        rows_by_client = {
            (row["id_namespace"], row["client_order_id"]): row for row in provider_rows
        }
        assert len(rows_by_client) == len(provider_rows), provider_rows
        spot_a_namespace = _TRADE_DOMAIN_SPOT_A
        spot_b_namespace = _TRADE_DOMAIN_SPOT_B
        perp_a_namespace = _TRADE_DOMAIN_PERP_A
        spot_btc_provider_rows = [
            row
            for row in provider_rows
            if row["id_namespace"] == spot_a_namespace
            and row["instrument"] == _BTC
            and row["provider_order_id"] == "order-1"
            and row["trade_ids"] == ("trade-1",)
        ]
        assert len(spot_btc_provider_rows) == 1, provider_rows
        order_one_rows = [
            row
            for row in provider_rows
            if row["id_namespace"] == spot_a_namespace
            and row["provider_order_id"] == "order-1"
        ]
        assert len(order_one_rows) == 2, order_one_rows
        assert all(row["order"]["status"] == "filled" for row in order_one_rows)
        assert len({row["client_order_id"] for row in order_one_rows}) == 2
        assert any(
            row["id_namespace"] == spot_b_namespace
            and row["provider_order_id"] == "order-1"
            and row["trade_ids"] == ("trade-1",)
            for row in provider_rows
        ), provider_rows
        assert any(
            row["id_namespace"] == perp_a_namespace
            and row["provider_order_id"] == "order-1"
            and row["trade_ids"] == ("trade-1",)
            for row in provider_rows
        ), provider_rows
        assert any(
            row["id_namespace"] == spot_a_namespace
            and row["instrument"] == _ETH
            and row["trade_ids"] == ("trade-1",)
            for row in provider_rows
        ), provider_rows
        trade_collision_provider_rows = [
            row
            for row in provider_rows
            if row["id_namespace"] == spot_a_namespace
            and row["instrument"] == _BTC
            and row["trade_ids"] == ("trade-1",)
            and row["provider_order_id"] != "order-1"
        ]
        assert len(trade_collision_provider_rows) == 1, provider_rows
        assert trade_collision_provider_rows[0]["trade_ids"] == ("trade-1",)
        # The rotated provider profile and the exchange alias share the one
        # declared domain namespace: version rotation never opens a second
        # identity space.
        spot_a_namespace_profiles = {
            row["profile_id"]
            for row in provider_rows
            if row["id_namespace"] == spot_a_namespace
        }
        assert spot_a_namespace_profiles == {_PROFILE_SPOT, _PROFILE_SPOT_ROTATED}, (
            spot_a_namespace_profiles
        )
        # The contradicting ACCOUNT-scope contract still produced a provider
        # write, but the raw trade of that write was never occupied by Risk.
        mode_change_provider_rows = [
            row
            for row in provider_rows
            if row["id_namespace"] == spot_a_namespace
            and row["instrument"] == _BTC
            and row["provider_order_id"] == "order-6"
        ]
        assert len(mode_change_provider_rows) == 1, provider_rows
        assert mode_change_provider_rows[0]["trade_ids"] == ("trade-5",)
        assert mode_change_provider_rows[0]["profile_id"] == _PROFILE_SPOT

        fill_rows = _risk_fill_rows(paths["risk"])
        spot_a_key = _ACCOUNT_SPOT_A.key
        spot_b_key = _ACCOUNT_SPOT_B.key
        perp_a_key = _ACCOUNT_PERP_A.key
        # Persisted Risk rows carry the journal's durable namespaced intent ID,
        # never the public business ID, so map each leg explicitly.
        spot_a_risk_intent = _risk_intent_id(
            paths["journal"],
            account_scope=_ACCOUNT_SPOT_A,
            principal_ref=_PRINCIPAL_A,
            intent_id=intent_id,
        )
        spot_a_strategy_b_risk_intent = _risk_intent_id(
            paths["journal"],
            account_scope=_ACCOUNT_SPOT_A,
            principal_ref=_PRINCIPAL_B,
            intent_id=intent_id,
        )
        spot_b_risk_intent = _risk_intent_id(
            paths["journal"],
            account_scope=_ACCOUNT_SPOT_B,
            principal_ref=_PRINCIPAL_A,
            intent_id=intent_id,
        )
        perp_a_risk_intent = _risk_intent_id(
            paths["journal"],
            account_scope=_ACCOUNT_PERP_A,
            principal_ref=_PRINCIPAL_A,
            intent_id=intent_id,
        )
        btc_spot_a = [
            row
            for row in fill_rows
            if row["scope_key"] == spot_a_key and row["instrument_id"] == _BTC
        ]
        assert len(btc_spot_a) == 2, btc_spot_a
        accepted_btc_by_strategy = {row["strategy_id"]: row for row in btc_spot_a}
        assert accepted_btc_by_strategy[_STRATEGY_A]["intent_id"] == spot_a_risk_intent
        assert accepted_btc_by_strategy[_STRATEGY_A]["trade_id"] == "trade-1"
        assert (
            accepted_btc_by_strategy[_STRATEGY_B]["intent_id"]
            == spot_a_strategy_b_risk_intent
        )
        assert accepted_btc_by_strategy[_STRATEGY_B]["trade_id"] == "trade-2"
        assert any(
            row["scope_key"] == spot_a_key
            and row["strategy_id"] == _STRATEGY_A
            and row["instrument_id"] == _ETH
            and row["trade_id"] == "trade-1"
            for row in fill_rows
        ), fill_rows
        assert any(
            row["scope_key"] == spot_b_key
            and row["strategy_id"] == _STRATEGY_A
            and row["trade_id"] == "trade-1"
            for row in fill_rows
        ), fill_rows
        for collision_risk_intent in (
            trade_collision_risk_id,
            order_collision_risk_id,
            mode_change_risk_id,
        ):
            assert any(
                row["scope_key"] == spot_a_key
                and row["intent_id"] == collision_risk_intent
                for row in fill_rows
            ) is False, fill_rows
        assert any(
            row["scope_key"] == perp_a_key
            and row["strategy_id"] == _STRATEGY_A
            and row["instrument_id"] == _BTC
            and row["trade_id"] == "trade-1"
            for row in fill_rows
        ), fill_rows

        spot_btc_notional = next(
            row["filled_gross_notional"]
            for row in _read_exposure_rows(paths["risk"])
            if row["scope_key"] == spot_a_key
            and row["strategy_id"] == _STRATEGY_A
            and row["intent_id"] == spot_a_risk_intent
            and row["instrument_id"] == _BTC
        )
        spot_b_notional = next(
            row["filled_gross_notional"]
            for row in _read_exposure_rows(paths["risk"])
            if row["scope_key"] == spot_b_key
            and row["intent_id"] == spot_b_risk_intent
            and row["instrument_id"] == _BTC
        )
        perp_btc_notional = next(
            row["filled_gross_notional"]
            for row in _read_exposure_rows(paths["risk"])
            if row["scope_key"] == perp_a_key
            and row["intent_id"] == perp_a_risk_intent
            and row["instrument_id"] == _BTC
        )
        assert spot_btc_notional == "20"
        assert spot_b_notional == "62"
        # The perpetual contract multiplies the conservative floor by its
        # declared instrument multiplier of 10: 3 x 17 x 10 = 510.
        assert perp_btc_notional == "510"
    finally:
        for record in reversed(clients):
            _bounded_reap_spawned_process(record, forced, failures)
        if host is not None:
            _bounded_reap_spawned_process(host, forced, failures)
        _assert_no_fallback_cleanup(forced, failures)


def _read_exposure_rows(database_path: str) -> tuple[dict[str, Any], ...]:
    uri = Path(database_path).resolve().as_uri() + "?mode=ro"
    with sqlite3.connect(uri, uri=True) as connection:
        connection.row_factory = sqlite3.Row
        rows = connection.execute(
            "SELECT scope_key,strategy_id,intent_id,provider_order_id,instrument_id,"
            "state,filled_gross_notional,cumulative_filled_quantity "
            "FROM risk_order_exposures ORDER BY scope_key,strategy_id,intent_id"
        ).fetchall()
    return tuple(dict(row) for row in rows)
