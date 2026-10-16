"""MIG04 pilot: minimal explicit ServiceHost startup from installed wheels.

Mirrors the cfg02 evidence shape (one explicit gateway market_hub component,
CURVE authority, synthetic provider, explicit communication profile) but is a
standalone script that starts, prints the startup summary, serves briefly,
closes normally, and prints the closed summary. It is NOT the project test
runner; no pytest is involved.

Usage: <service-venv-python> service_host_min.py
Writes: run/service-config-min.json (raw config used), run/registry-min.sqlite3
Exit: 0 only if startup summary shows exactly the one explicit component and
      shutdown reaches state closed with no cleanup failures.
"""

from __future__ import annotations

import base64
import importlib.metadata as md
import json
import socket
import sys
from pathlib import Path
from typing import Any

import zmq
from bt_api_base.market_protocol import MarketInstrumentKey, MarketSubscriptionKey
from bt_api_base.service_protocol import WireIdentity, WireRole, WireScope
from bt_api_gateway.market_hub import GatewayMarketHub

from bt_api_py.service_components import ComponentResources
from bt_api_py.service_config import parse_service_config
from bt_api_py.service_host import ServiceHost, TrustedServiceAuthority
from bt_api_py.service_registry import PrincipalGrant, ResourceGrant

RUN_DIR = Path(__file__).resolve().parent
OUT_DIR = RUN_DIR.parent

SERVICE_ID = "mig04-pilot-market-service"
SERVICE_PRINCIPAL = "mig04-pilot-market-principal"
SERVICE_INSTANCE = "mig04-pilot-market-hub"
SERVICE_SCOPE_REF = "mig04-pilot-market-scope-v1"
STRATEGY_REF = "mig04-pilot-strategy"
STRATEGY_PRINCIPAL = "mig04-pilot-strategy-principal"
ACCOUNT_REF = "mig04-pilot-account"
DOMAIN_REF = "paper:mig04-pilot"
SECURITY_REF = "mig04-pilot-curve-v1"
TRANSPORT_REF = "mig04-pilot-listener-v1"
MARKET_OPERATIONS = frozenset(
    {
        "market.subscribe",
        "market.unsubscribe",
        "market.snapshot",
        "market.head",
        "market.read_after",
    }
)
MARKET_KEY = MarketSubscriptionKey(
    source_ref="mig04-pilot-synthetic-source",
    entitlement_ref="mig04-pilot-synthetic-entitlement",
    instrument=MarketInstrumentKey(
        provider="synthetic",
        venue="loopback",
        environment="paper",
        product_type="spot",
        instrument_id="mig04-pilot-instrument",
        native_symbol="PILOT-USD",
    ),
    channel="trades",
)


def _plain(value: object) -> object:
    if isinstance(value, dict) or hasattr(value, "items"):
        return {str(key): _plain(item) for key, item in value.items()}
    if isinstance(value, (tuple, list)):
        return [_plain(item) for item in value]
    if value is None or type(value) in (str, int, float, bool):
        return value
    raise TypeError(f"unsupported start-summary value: {type(value).__name__}")


def _free_endpoints() -> tuple[str, str]:
    probes: list[socket.socket] = []
    try:
        endpoints: list[str] = []
        for _ in range(2):
            probe = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            probe.bind(("127.0.0.1", 0))
            probes.append(probe)
            endpoints.append(f"tcp://127.0.0.1:{probe.getsockname()[1]}")
        return endpoints[0], endpoints[1]
    finally:
        for probe in probes:
            probe.close()


class _SyntheticProvider:
    def __init__(self) -> None:
        self.hub: Any = None
        self.generation: str | None = None
        self.handle: object | None = None
        self.starts = 0
        self.stops = 0

    def subscribe(self, key: MarketSubscriptionKey, *, generation: str) -> object:
        if key != MARKET_KEY or not generation or self.handle is not None:
            raise AssertionError("unexpected or duplicate synthetic market subscription")
        self.starts += 1
        self.generation = generation
        self.handle = ("pilot-market-handle", self.starts, key.stream_id, generation)
        return self.handle

    def unsubscribe(self, handle: object) -> bool:
        if self.handle is None or handle != self.handle:
            raise AssertionError("gateway attempted to release an unknown synthetic handle")
        self.stops += 1
        self.handle = None
        self.generation = None
        return True


def _distribution_versions() -> dict[str, str | None]:
    names = (
        "bt_api_py",
        "bt_api_base",
        "bt_api_transport_zmq",
        "bt_api_gateway",
        "bt_api_execution",
        "bt_api_risk",
        "bt_api_monitor",
    )
    result: dict[str, str | None] = {}
    for name in names:
        try:
            result[name] = md.version(name)
        except md.PackageNotFoundError:
            result[name] = None
    return result


def _origin(module_name: str) -> str:
    import importlib

    module = importlib.import_module(module_name)
    origin = getattr(module, "__file__", None)
    return str(Path(origin).resolve()) if origin else "<namespace>"


def main() -> int:
    rpc_endpoint, market_endpoint = _free_endpoints()
    config_raw: dict[str, object] = {
        "service_config_schema_version": 1,
        "process_role": "service",
        "service_id": SERVICE_ID,
        "scope_ref": SERVICE_SCOPE_REF,
        "transport_ref": TRANSPORT_REF,
        "registration_policy_ref": "mig04-pilot-registration-v1",
        "service_requirements_ref": "mig04-pilot-requirements-v1",
        "components": {
            "gateway": {
                "config_schema_version": 2,
                "settings": {
                    "instance_id": SERVICE_INSTANCE,
                    "bind_ports": ["service.market_source"],
                    "options": {"role": "market_hub"},
                },
            }
        },
        "communication": {
            "config_schema_version": 1,
            "profiles": {
                TRANSPORT_REF: {
                    "backend": "zmq",
                    "config_schema_version": 1,
                    "endpoints": {
                        "rpc_bind": rpc_endpoint,
                        "market_bind": market_endpoint,
                    },
                    "security_profile_ref": SECURITY_REF,
                    "options": {
                        "high_water_mark": 128,
                        "max_message_bytes": 262144,
                    },
                }
            },
        },
    }
    (RUN_DIR / "service-config-min.json").write_text(
        json.dumps(config_raw, indent=2, sort_keys=True), encoding="utf-8"
    )
    config = parse_service_config(config_raw)

    server_public, server_secret = zmq.curve_keypair()
    strategy_public, _strategy_secret = zmq.curve_keypair()
    service_identity = WireIdentity(WireRole.SERVICE, SERVICE_PRINCIPAL, SERVICE_ID)
    service_scope = WireScope(DOMAIN_REF)
    strategy_identity = WireIdentity(WireRole.STRATEGY_CLIENT, STRATEGY_PRINCIPAL, STRATEGY_REF)
    authority = TrustedServiceAuthority(
        identity=service_identity,
        scope=service_scope,
        scope_ref=SERVICE_SCOPE_REF,
        scope_version="mig04-pilot-service-scope-v1",
        security_ref=SECURITY_REF,
        security_version="curve-v1",
        registration_policy_ref="mig04-pilot-registration-v1",
        registration_policy_version="policy-v1",
        service_requirements_ref="mig04-pilot-requirements-v1",
        service_requirements_version="requirements-v1",
        curve_public_key=server_public,
        curve_secret_key=server_secret,
        principals={strategy_public: STRATEGY_PRINCIPAL},
        grants={
            STRATEGY_PRINCIPAL: (
                PrincipalGrant(
                    strategy_identity,
                    (
                        ResourceGrant(
                            SERVICE_INSTANCE,
                            "service.market_source",
                            SERVICE_SCOPE_REF,
                            WireScope(
                                DOMAIN_REF,
                                account_ref=ACCOUNT_REF,
                                strategy_ref=STRATEGY_REF,
                            ),
                            MARKET_OPERATIONS,
                        ),
                    ),
                ),
            )
        },
    )

    provider = _SyntheticProvider()
    hub = GatewayMarketHub(
        provider,
        key_universe=frozenset({MARKET_KEY}),
        principal_allowed_keys={STRATEGY_PRINCIPAL: frozenset({MARKET_KEY})},
        service_identity=service_identity,
        service_scope=service_scope,
    )
    provider.hub = hub

    def resources(component: str, instance_id: str, options, _scope) -> ComponentResources:
        if (
            component != "gateway"
            or instance_id != SERVICE_INSTANCE
            or options.get("role") != "market_hub"
        ):
            raise AssertionError("the service activated an undeclared component")
        return ComponentResources.owned(
            {"hub": hub},
            ready=lambda: hub.ready,
            close=hub.close,
        )

    registry_path = RUN_DIR / "registry-min.sqlite3"
    host = ServiceHost(config, authority, registry_path, resources)
    result: dict[str, object] = {
        "python": sys.executable,
        "prefix": sys.prefix,
        "distributions": _distribution_versions(),
        "origins": {
            name: _origin(name)
            for name in (
                "bt_api_py.service_host",
                "bt_api_base.service_protocol",
                "bt_api_gateway.market_hub",
                "bt_api_transport_zmq.server",
            )
        },
    }
    host.start()
    started = _plain(host.summary)
    result["started_summary"] = started
    result["endpoint"] = host.endpoint
    result["market_endpoint"] = host.market_endpoint
    print(json.dumps(result, indent=2, sort_keys=True), flush=True)

    components = started["components"]
    exact = (
        len(components) == 1
        and components[0]["component"] == "gateway"
        and components[0]["instance_id"] == SERVICE_INSTANCE
        and list(components[0]["declared_ports"]) == ["service.market_source"]
        and not started["cleanup_failures"]
        and started["start_failure"] is None
    )

    for _ in range(3):
        host.serve_once(10)
    host.close()
    closed = _plain(host.summary)
    result = {
        "closed_summary": closed,
        "provider_starts": provider.starts,
        "provider_stops": provider.stops,
        "state": host.state,
        "exact_single_explicit_component": exact,
        "closed_without_cleanup_failures": not closed["cleanup_failures"],
    }
    print(json.dumps(result, indent=2, sort_keys=True), flush=True)
    ok = exact and host.state == "closed" and not closed["cleanup_failures"]
    return 0 if ok else 1


if __name__ == "__main__":
    if len(sys.argv) != 1:
        print(json.dumps({"error": "no arguments expected"}))
        raise SystemExit(2)
    raise SystemExit(main())
