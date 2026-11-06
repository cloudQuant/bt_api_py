"""Trusted composition of the Gateway CTP MarketHub and its strategy client.

Iteration 62 (62.08). This module assembles existing SDK parts; it holds no
credentials and performs no network I/O until the returned operator profile
is started:

* :func:`build_ctp_gateway_market_host` builds the Gateway ``market_hub``
  operator profile (``ctp_account_process.build_ctp_simnow_set2_market_operator_profile``)
  for one SimNow profile and instrument set, with per-run CURVE keys on
  loopback endpoints, and returns the :class:`GatewayClientConnection` a
  trusted strategy process needs to subscribe;
* :func:`create_gateway_market_runtime` turns that connection into a
  ``ServiceClientRuntime`` (started by default) and the
  :class:`GatewayMarketTickRuntimeFactory` that
  ``BtApi(market_runtime_factory=...)`` consumes; the factory keeps the
  runtime's lease fit before each new market client.

The provider reads its market credentials from the hub process's own
environment when the first subscription starts. The connection carries the
client's CURVE secret key: hand it only to the trusted strategy process (for
example through a ``multiprocessing`` pipe), never through argv, logs or files.
"""

from __future__ import annotations

import socket
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Iterable

__all__ = (
    "CTP_GATEWAY_MARKET_MOUNT",
    "GatewayClientConnection",
    "build_ctp_gateway_market_host",
    "create_gateway_market_runtime",
    "ctp_gateway_instrument_key",
    "gateway_market_check_client",
)

CTP_GATEWAY_MARKET_MOUNT = "store.market"
_SERVICE_ID = "bt-ctp-gateway-market"
_INSTANCE_ID = "bt-ctp-gateway-market-hub"
_SCOPE_REF = "bt-ctp-gateway-market-scope-v1"
_SECURITY_REF = "bt-ctp-gateway-market-curve-v1"
_TRANSPORT_REF = "bt-ctp-gateway-market-transport-v1"
_CLIENT_TRANSPORT_REF = "bt-ctp-gateway-market-client-v1"
_DOMAIN_REF = "paper:bt-ctp-gateway-market"
_ACCOUNT_REF = "account:bt-ctp-gateway-market-only"
_STRATEGY_REF = "strategy:bt-ctp-gateway-market"
_PRINCIPAL_REF = "principal:bt-ctp-gateway-market"
_SERVICE_PRINCIPAL = "principal:bt-ctp-gateway-market-service"
_MARKET_OPERATIONS = (
    "market.subscribe",
    "market.unsubscribe",
    "market.snapshot",
    "market.head",
    "market.read_after",
)
# The first subscription waits for the CTP MD login inside the hub (<= 30 s).
_REQUEST_TIMEOUT_MS = 45_000


@dataclass(frozen=True)
class GatewayClientConnection:
    """What a trusted strategy process needs to reach one Gateway MarketHub."""

    rpc_endpoint: str
    market_endpoint: str
    source_ref: str
    entitlement_ref: str
    server_public_key: bytes = field(repr=False)
    client_public_key: bytes = field(repr=False)
    client_secret_key: bytes = field(repr=False)


def ctp_gateway_instrument_key(symbol: str, venue: str) -> Any:
    """SimNow futures instrument key with the SDK route label (``demo``)."""

    from bt_api_base.market_protocol import MarketInstrumentKey

    from bt_api_py.ctp_account_process import CTP_SIMNOW_MARKET_ENVIRONMENT

    return MarketInstrumentKey(
        provider="ctp",
        venue=str(venue),
        environment=CTP_SIMNOW_MARKET_ENVIRONMENT,
        product_type="future",
        instrument_id=str(symbol),
        native_symbol=str(symbol),
    )


def _free_loopback_endpoint() -> str:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as probe:
        probe.bind(("127.0.0.1", 0))
        return f"tcp://127.0.0.1:{probe.getsockname()[1]}"


def _service_config(rpc_bind: str, market_bind: str) -> dict[str, Any]:
    return {
        "service_config_schema_version": 1,
        "process_role": "service",
        "service_id": _SERVICE_ID,
        "scope_ref": _SCOPE_REF,
        "transport_ref": _TRANSPORT_REF,
        "registration_policy_ref": "bt-ctp-gateway-market-policy-v1",
        "service_requirements_ref": "bt-ctp-gateway-market-requirements-v1",
        "components": {
            "gateway": {
                "config_schema_version": 2,
                "settings": {
                    "instance_id": _INSTANCE_ID,
                    "bind_ports": ["service.market_source"],
                    "options": {"role": "market_hub"},
                },
            }
        },
        "communication": {
            "config_schema_version": 1,
            "profiles": {
                _TRANSPORT_REF: {
                    "backend": "zmq",
                    "config_schema_version": 1,
                    "endpoints": {"rpc_bind": rpc_bind, "market_bind": market_bind},
                    "security_profile_ref": _SECURITY_REF,
                    "options": {"high_water_mark": 1_024, "max_message_bytes": 262_144},
                }
            },
        },
    }


def _client_config(rpc_connect: str, market_connect: str) -> dict[str, Any]:
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
                            "mount": CTP_GATEWAY_MARKET_MOUNT,
                            "service_id": _SERVICE_ID,
                            "instance_id": _INSTANCE_ID,
                            "scope_ref": _SCOPE_REF,
                            "transport_ref": _CLIENT_TRANSPORT_REF,
                        }
                    ],
                    "options": {
                        "request_timeout_ms": _REQUEST_TIMEOUT_MS,
                        "heartbeat_interval_seconds": 5,
                    },
                },
            }
        },
        "communication": {
            "config_schema_version": 1,
            "profiles": {
                _CLIENT_TRANSPORT_REF: {
                    "backend": "zmq",
                    "config_schema_version": 1,
                    "endpoints": {"rpc_connect": rpc_connect, "market_connect": market_connect},
                    "security_profile_ref": _SECURITY_REF,
                    "options": {
                        "request_timeout_ms": _REQUEST_TIMEOUT_MS,
                        "high_water_mark": 1_024,
                        "max_message_bytes": 262_144,
                    },
                }
            },
        },
    }


def _service_identity() -> Any:
    from bt_api_base.service_protocol import WireIdentity, WireRole

    return WireIdentity(WireRole.SERVICE, _SERVICE_PRINCIPAL, _SERVICE_ID)


def build_ctp_gateway_market_host(
    *,
    ctp_profile: str,
    instruments: Iterable[tuple[str, str]],
    registry_path: str | Path,
    rpc_endpoint: str | None = None,
    market_endpoint: str | None = None,
) -> tuple[Any, GatewayClientConnection]:
    """Return the MarketHub operator profile and its client connection.

    ``instruments`` are ``(symbol, venue)`` pairs. Nothing is started; the
    caller passes the profile to ``GatewayOperatorApplication`` (or serves its
    deployment) in the process that owns the CTP market credentials.
    """

    import zmq
    from bt_api_base.service_protocol import WireIdentity, WireRole, WireScope

    from bt_api_py.ctp_account_process import (
        build_ctp_simnow_set2_market_operator_profile,
        ctp_simnow_gateway_refs,
    )
    from bt_api_py.service_config import parse_service_config
    from bt_api_py.service_host import TrustedServiceAuthority
    from bt_api_py.service_registry import PrincipalGrant, ResourceGrant

    refs = ctp_simnow_gateway_refs(ctp_profile)
    pairs = tuple(dict.fromkeys((str(symbol), str(venue)) for symbol, venue in instruments))
    if not pairs:
        raise ValueError("at least one (symbol, venue) instrument is required")
    keys = tuple(
        _market_key(refs, ctp_gateway_instrument_key(symbol, venue)) for symbol, venue in pairs
    )
    rpc = rpc_endpoint or _free_loopback_endpoint()
    market = market_endpoint or _free_loopback_endpoint()
    config = parse_service_config(_service_config(rpc, market))
    server_public, server_secret = zmq.curve_keypair()
    client_public, client_secret = zmq.curve_keypair()
    grant = ResourceGrant(
        _INSTANCE_ID,
        "service.market_source",
        _SCOPE_REF,
        WireScope(_DOMAIN_REF, account_ref=_ACCOUNT_REF, strategy_ref=_STRATEGY_REF),
        frozenset(_MARKET_OPERATIONS),
    )
    authority = TrustedServiceAuthority(
        identity=_service_identity(),
        scope=WireScope(_DOMAIN_REF),
        scope_ref=config.scope_ref,
        scope_version="bt-ctp-gateway-market-scope-v1",
        security_ref=_SECURITY_REF,
        security_version="curve-per-run-v1",
        registration_policy_ref=config.registration_policy_ref,
        registration_policy_version="bt-ctp-gateway-market-policy-v1",
        service_requirements_ref=config.service_requirements_ref,
        service_requirements_version="bt-ctp-gateway-market-requirements-v1",
        curve_public_key=server_public,
        curve_secret_key=server_secret,
        principals={client_public: _PRINCIPAL_REF},
        grants={
            _PRINCIPAL_REF: (
                PrincipalGrant(
                    WireIdentity(WireRole.STRATEGY_CLIENT, _PRINCIPAL_REF, _STRATEGY_REF),
                    (grant,),
                ),
            )
        },
    )
    profile = build_ctp_simnow_set2_market_operator_profile(
        config,
        authority,
        Path(registry_path),
        key_universe=keys,
        principal_allowed_keys={_PRINCIPAL_REF: frozenset(keys)},
        ctp_profile=ctp_profile,
    )
    connection = GatewayClientConnection(
        rpc_endpoint=rpc,
        market_endpoint=market,
        source_ref=refs.market_source_ref,
        entitlement_ref=refs.market_entitlement_ref,
        server_public_key=server_public,
        client_public_key=client_public,
        client_secret_key=client_secret,
    )
    return profile, connection


def _market_key(refs: Any, instrument: Any) -> Any:
    from bt_api_base.market_protocol import MarketSubscriptionKey

    from bt_api_py.ctp_account_process import CTP_MARKET_TICK_CHANNEL

    return MarketSubscriptionKey(
        source_ref=refs.market_source_ref,
        entitlement_ref=refs.market_entitlement_ref,
        instrument=instrument,
        channel=CTP_MARKET_TICK_CHANNEL,
    )


def create_gateway_market_runtime(
    connection: GatewayClientConnection,
    *,
    head_check_interval_seconds: float = 1.0,
    start: bool = True,
) -> tuple[Any, Any]:
    """Create the strategy-side market runtime and return it with its factory.

    Call on the thread that will run Cerebro (the factory's owner thread).
    By default the runtime is started (registered with the Gateway host) here,
    so an operator check (:func:`gateway_market_check_client`) can use it at
    once; ``start=False`` leaves it unstarted and the first subscription
    starts it. Before every new market client the factory renews a due lease
    and, only before its first client, replaces a registration whose required
    lease expired while the host was still starting (store and broker start
    can outlast the lease) with a fresh registration session. A lease that
    expires after the first client exists stays a reported failure.
    The caller closes the runtime after the ``BtApi`` that uses the factory.
    """

    from bt_api_base.service_protocol import WireIdentity, WireRole, WireScope

    from bt_api_py.gateway_market_runtime import GatewayMarketTickRuntimeFactory
    from bt_api_py.service_config import parse_client_config
    from bt_api_py.service_market import ZmqMarketTransportFactory
    from bt_api_py.service_runtime import (
        ClientCurveKeys,
        ServiceClientRuntime,
        ZmqTransportFactory,
    )

    if type(connection) is not GatewayClientConnection:
        raise TypeError("an exact GatewayClientConnection is required")
    if type(start) is not bool:
        raise TypeError("start must be a bool")
    keys = ClientCurveKeys(
        connection.server_public_key,
        connection.client_public_key,
        connection.client_secret_key,
    )
    service_identity = _service_identity()
    runtime = ServiceClientRuntime(
        parse_client_config(_client_config(connection.rpc_endpoint, connection.market_endpoint)),
        identity=WireIdentity(WireRole.STRATEGY_CLIENT, _PRINCIPAL_REF, _STRATEGY_REF),
        scope_resolver=lambda _ref: WireScope(
            _DOMAIN_REF, account_ref=_ACCOUNT_REF, strategy_ref=_STRATEGY_REF
        ),
        peer_resolver=lambda _service_id: service_identity,
        transport_factory=ZmqTransportFactory({_SECURITY_REF: keys}),
        run_id="bt-ctp-gateway-market-" + uuid.uuid4().hex[:12],
        requested_operations={CTP_GATEWAY_MARKET_MOUNT: frozenset(_MARKET_OPERATIONS)},
    )
    if start:
        runtime.start()
    factory = GatewayMarketTickRuntimeFactory(
        runtime,
        mount=CTP_GATEWAY_MARKET_MOUNT,
        source_ref=connection.source_ref,
        entitlement_ref=connection.entitlement_ref,
        subscriber_factory=ZmqMarketTransportFactory({_SECURITY_REF: keys}),
        stream_scope=WireScope(_DOMAIN_REF),
        head_check_interval_seconds=head_check_interval_seconds,
    )
    return runtime, factory


def gateway_market_check_client(
    runtime: Any, connection: GatewayClientConnection, symbol: str, venue: str
) -> Any:
    """One exact ``ServiceMarketClient`` for an operator market check.

    Uses the same mount, key, scope and subscriber as the ``BtApi`` path; the
    caller starts, polls and closes it on the runtime's owner thread.
    """

    from bt_api_base.service_protocol import WireScope

    from bt_api_base.market_protocol import MarketSubscriptionKey

    from bt_api_py.ctp_account_process import CTP_MARKET_TICK_CHANNEL
    from bt_api_py.service_market import ServiceMarketClient, ZmqMarketTransportFactory
    from bt_api_py.service_runtime import ClientCurveKeys

    keys = ClientCurveKeys(
        connection.server_public_key,
        connection.client_public_key,
        connection.client_secret_key,
    )
    key = MarketSubscriptionKey(
        source_ref=connection.source_ref,
        entitlement_ref=connection.entitlement_ref,
        instrument=ctp_gateway_instrument_key(symbol, venue),
        channel=CTP_MARKET_TICK_CHANNEL,
    )
    return ServiceMarketClient(
        runtime,
        CTP_GATEWAY_MARKET_MOUNT,
        key,
        subscriber_factory=ZmqMarketTransportFactory({_SECURITY_REF: keys}),
        stream_scope=WireScope(_DOMAIN_REF),
        head_check_interval_seconds=1.0,
        require_source_descriptor=True,
    )
