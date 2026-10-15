"""Shared control-plane contract for negotiated market topic filtering.

These tests exercise parsed configuration, registry discovery, client startup,
and the real service-host startup path. Transport sockets/publishers remain
synthetic leaves in the host integration test.
"""

from __future__ import annotations

import importlib
from dataclasses import replace
from typing import Any

import pytest
import zmq
from bt_api_base.market_protocol import MarketInstrumentKey, MarketSubscriptionKey
from bt_api_base.service_protocol import (
    WIRE_PROTOCOL_VERSION,
    WireEnvelope,
    WireHeader,
    WireIdentity,
    WireMessageKind,
    WireRole,
    WireScope,
)
from bt_api_gateway.market_hub import MARKET_OPERATIONS, GatewayMarketHub

from bt_api_py.service_components import ComponentResources
from bt_api_py.service_config import (
    ServiceConfigError,
    parse_client_config,
    parse_service_config,
)
from bt_api_py.service_host import ServiceHost, TrustedServiceAuthority
from bt_api_py.service_registry import (
    PrincipalGrant,
    ResourceGrant,
    ServiceCapability,
    ServiceRegistry,
)
from bt_api_py.service_runtime import ServiceClientRuntime, ServiceRuntimeError

CLIENT = WireIdentity(WireRole.STRATEGY_CLIENT, "curve-principal", "strategy-client")
PEER = WireIdentity(WireRole.SERVICE, "market-service", "market-service")
MARKET_SCOPE = WireScope("paper:demo", strategy_ref="strategy-client")
SENT_AT = "2026-10-15T00:00:00Z"


def _request(operation: str, payload: dict[str, Any], *, scope: WireScope) -> WireEnvelope:
    return WireEnvelope(
        WireHeader(
            protocol_version=WIRE_PROTOCOL_VERSION,
            kind=WireMessageKind.REQUEST,
            message_id=f"market-topic-{operation}",
            correlation_id=None,
            identity=CLIENT,
            scope=scope,
            operation=operation,
            schema_id="sdk.service.control",
            schema_version=1,
            sent_at_utc=SENT_AT,
        ),
        payload,
    )


def _client_config(*, version: int | None, required: bool = True):
    options = {} if version is None else {"market_wire_version": version}
    raw = {
        "client_config_schema_version": 1,
        "process_role": "strategy_client",
        "registration": {"strategy_ref": "strategy-client"},
        "clients": {
            "gateway": {
                "config_schema_version": 1,
                "settings": {
                    "bindings": [
                        {
                            "mount": "store.market",
                            "service_id": "market-service",
                            "instance_id": "market-source",
                            "scope_ref": "market-grant-v1",
                            "transport_ref": "market-connect-v1",
                            "required": required,
                        }
                    ]
                },
            }
        },
        "communication": {
            "config_schema_version": 1,
            "profiles": {
                "market-connect-v1": {
                    "backend": "zmq",
                    "config_schema_version": 1,
                    "endpoints": {
                        "rpc_connect": "tcp://127.0.0.1:7601",
                        "market_connect": "tcp://127.0.0.1:7602",
                    },
                    "security_profile_ref": "market-client-keys",
                    **({"options": options} if options else {}),
                }
            },
        },
    }
    return parse_client_config(raw)


def _service_config(*, market: bool, version: int | None = None):
    profile_options: dict[str, Any] = {"high_water_mark": 32, "max_message_bytes": 65_536}
    if version is not None:
        profile_options["market_wire_version"] = version
    component = (
        {
            "config_schema_version": 2,
            "settings": {
                "instance_id": "market-source",
                "bind_ports": ["service.market_source"],
                "options": {"role": "market_hub"},
            },
        }
        if market
        else {
            "config_schema_version": 2,
            "settings": {
                "instance_id": "monitor-query",
                "bind_ports": ["service.monitor_query"],
                "options": {},
            },
        }
    )
    return parse_service_config(
        {
            "service_config_schema_version": 1,
            "process_role": "service",
            "service_id": "market-service" if market else "monitor-service",
            "scope_ref": "service-root-v1",
            "transport_ref": "listener-v1",
            "registration_policy_ref": "registration-v1",
            "service_requirements_ref": "requirements-v1",
            "components": {"gateway" if market else "monitor": component},
            "communication": {
                "config_schema_version": 1,
                "profiles": {
                    "listener-v1": {
                        "backend": "zmq",
                        "config_schema_version": 1,
                        "endpoints": {
                            "rpc_bind": _endpoint(),
                            **({"market_bind": _endpoint()} if market else {}),
                        },
                        "security_profile_ref": "service-keys",
                        "options": profile_options,
                    }
                },
            },
        }
    )


def _market_registry(path, *, version: int | None):
    market = ServiceCapability(
        "market-source",
        "service.market_source",
        MARKET_OPERATIONS,
        market_wire_version=version,
    )
    grant = ResourceGrant(
        "market-source",
        "service.market_source",
        "market-grant-v1",
        MARKET_SCOPE,
        MARKET_OPERATIONS,
    )
    registry = ServiceRegistry(
        path,
        service_id="market-service",
        capabilities=(market,),
        grants={CLIENT.principal_ref: (PrincipalGrant(CLIENT, (grant,)),)},
        policy_version="policy-v1",
        requirements_version="requirements-v1",
    )
    return registry


class _RegistryTransport:
    def __init__(self, registry: ServiceRegistry, *, discovery_edit=None) -> None:
        self.registry = registry
        self.discovery_edit = discovery_edit
        self.requests: list[WireEnvelope] = []
        self.closed = False

    def request(self, request: WireEnvelope, *, timeout: float) -> WireEnvelope:
        assert timeout > 0
        self.requests.append(request)
        result = self.registry.handle(CLIENT.principal_ref, request)
        if request.header.operation == "service.discover" and self.discovery_edit is not None:
            result = self.discovery_edit(result)
        return WireEnvelope(
            replace(
                request.header,
                kind=WireMessageKind.REPLY,
                message_id="reply-" + request.header.message_id,
                correlation_id=request.header.message_id,
                identity=PEER,
            ),
            result,
        )

    def close(self) -> None:
        self.closed = True


def _runtime(config, registry, *, discovery_edit=None):
    transport = _RegistryTransport(registry, discovery_edit=discovery_edit)
    runtime = ServiceClientRuntime(
        config,
        identity=CLIENT,
        scope_resolver=lambda _scope_ref: MARKET_SCOPE,
        peer_resolver=lambda _service_id: PEER,
        transport_factory=lambda _profile, _peer: transport,
        run_id="topic-test-run",
        requested_operations={
            "store.market": frozenset({"market.subscribe", "market.unsubscribe"})
        },
    )
    return runtime, transport


def test_profile_version_is_opt_in_and_scoped_to_market_endpoints() -> None:
    legacy = _service_config(market=True)
    legacy_options = legacy.to_public_dict()["communication"]["profiles"]["listener-v1"]["options"]
    assert "market_wire_version" not in legacy_options

    explicit_v1 = _service_config(market=True, version=1)
    assert (
        explicit_v1.to_public_dict()["communication"]["profiles"]["listener-v1"]["options"][
            "market_wire_version"
        ]
        == 1
    )
    explicit_v2 = _client_config(version=2)
    assert (
        explicit_v2.to_public_dict()["communication"]["profiles"]["market-connect-v1"]["options"][
            "market_wire_version"
        ]
        == 2
    )

    with pytest.raises(ServiceConfigError) as exc_info:
        _service_config(market=False, version=2)
    assert exc_info.value.code == "MARKET_WIRE_VERSION_REQUIRES_MARKET_ENDPOINT"

    with pytest.raises(ServiceConfigError) as exc_info:
        _service_config(market=True, version=True)
    assert exc_info.value.code == "INVALID_TYPE"

    for invalid in (0, 3):
        with pytest.raises(ServiceConfigError) as exc_info:
            _service_config(market=True, version=invalid)
        assert exc_info.value.code == "INVALID_VALUE"


def test_discovery_advertises_version_only_for_market_capabilities(tmp_path) -> None:
    market_v2 = ServiceCapability(
        "market-source",
        "service.market_source",
        MARKET_OPERATIONS,
        market_wire_version=2,
    )
    monitor_query = ServiceCapability(
        "monitor-query", "service.monitor_query", frozenset({"monitor.query"})
    )
    grant = ResourceGrant(
        "market-source",
        "service.market_source",
        "market-grant-v1",
        MARKET_SCOPE,
        MARKET_OPERATIONS,
    )
    registry = ServiceRegistry(
        tmp_path / "capability-discovery.sqlite",
        service_id="market-service",
        capabilities=(market_v2, monitor_query),
        grants={CLIENT.principal_ref: (PrincipalGrant(CLIENT, (grant,)),)},
        policy_version="policy-v1",
        requirements_version="requirements-v1",
    )
    try:
        scope = MARKET_SCOPE
        result = registry.handle(
            CLIENT.principal_ref,
            _request("service.discover", {}, scope=scope),
        )
        assert result["ok"] is True
        capabilities = result["result"]["capabilities"]
        assert capabilities == [
            {
                "instance_id": "market-source",
                "port": "service.market_source",
                "operations": sorted(MARKET_OPERATIONS),
                "market_wire_version": 2,
            },
            {
                "instance_id": "monitor-query",
                "port": "service.monitor_query",
                "operations": ["monitor.query"],
            },
        ]
    finally:
        registry.close()


def test_runtime_treats_old_missing_version_as_v1(tmp_path) -> None:
    registry = _market_registry(tmp_path / "legacy-market.sqlite", version=None)
    runtime, transport = _runtime(_client_config(version=None), registry)
    try:
        summary = runtime.start()
        assert len(summary.bindings) == 1
        assert any(r.header.operation == "service.register" for r in transport.requests)
    finally:
        runtime.close()
        registry.close()


@pytest.mark.parametrize(
    ("client_version", "remote_version", "edit", "expected_code"),
    [
        (2, None, None, "MARKET_WIRE_VERSION_MISMATCH"),
        (
            2,
            2,
            lambda reply: _edit_market_capability(reply, {"unrecognized": True}),
            "INVALID_MARKET_CAPABILITY",
        ),
        (
            2,
            2,
            lambda reply: _edit_market_capability(reply, {"market_wire_version": True}),
            "INVALID_MARKET_CAPABILITY",
        ),
    ],
)
def test_optional_market_binding_cannot_swallow_negotiation_defects(
    tmp_path, client_version, remote_version, edit, expected_code
) -> None:
    registry = _market_registry(tmp_path / f"market-{expected_code}.sqlite", version=remote_version)
    runtime, transport = _runtime(
        _client_config(version=client_version, required=False),
        registry,
        discovery_edit=edit,
    )
    try:
        with pytest.raises(ServiceRuntimeError) as exc_info:
            runtime.start()
        assert exc_info.value.code == expected_code
        assert not any(r.header.operation == "service.register" for r in transport.requests)
    finally:
        runtime.close()
        registry.close()


def _edit_market_capability(reply, fields):
    edited = dict(reply)
    result = dict(reply["result"])
    capabilities = [dict(capability) for capability in result["capabilities"]]
    capabilities[0].update(fields)
    result["capabilities"] = capabilities
    edited["result"] = result
    return edited


def _endpoint() -> str:
    import socket

    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as listener:
        listener.bind(("127.0.0.1", 0))
        port = listener.getsockname()[1]
    return f"tcp://127.0.0.1:{port}"


def _host_authority(config):
    client_public, _client_secret = zmq.curve_keypair()
    server_public, server_secret = zmq.curve_keypair()
    service_scope = WireScope("paper:demo")
    grant = ResourceGrant(
        "market-source",
        "service.market_source",
        "market-grant-v1",
        MARKET_SCOPE,
        MARKET_OPERATIONS,
    )
    authority = TrustedServiceAuthority(
        identity=WireIdentity(WireRole.SERVICE, "market-service", config.service_id),
        scope=service_scope,
        scope_ref=config.scope_ref,
        scope_version="scope-v1",
        security_ref="service-keys",
        security_version="security-v1",
        registration_policy_ref=config.registration_policy_ref,
        registration_policy_version="policy-v1",
        service_requirements_ref=config.service_requirements_ref,
        service_requirements_version="requirements-v1",
        curve_public_key=server_public,
        curve_secret_key=server_secret,
        principals={client_public: CLIENT.principal_ref},
        grants={CLIENT.principal_ref: (PrincipalGrant(CLIENT, (grant,)),)},
    )
    return authority


def _host_resources(authority):
    key = MarketSubscriptionKey(
        source_ref="source-v1",
        entitlement_ref="entitlement-v1",
        instrument=MarketInstrumentKey(
            provider="synthetic-provider",
            venue="synthetic-venue",
            environment="paper",
            product_type="future",
            instrument_id="instrument-1",
            native_symbol="SYNTH1",
        ),
        channel="trades",
    )

    class Provider:
        def subscribe(self, _key, *, generation):
            return generation

        def unsubscribe(self, _handle):
            return None

    hub = GatewayMarketHub(
        Provider(),
        key_universe={key},
        principal_allowed_keys={CLIENT.principal_ref: {key}},
        service_identity=authority.identity,
        service_scope=authority.scope,
    )
    return ComponentResources.owned({"hub": hub}, ready=lambda: True, close=hub.close)


@pytest.mark.parametrize(("version", "pass_version_keyword"), [(1, False), (2, True)])
def test_host_binds_market_capability_and_publisher_to_profile_version(
    tmp_path, monkeypatch, version, pass_version_keyword
) -> None:
    endpoint = _endpoint()
    config = _service_config(market=True, version=None if version == 1 else version)
    raw = config.to_public_dict()
    raw["communication"]["profiles"]["listener-v1"]["endpoints"]["market_bind"] = endpoint
    config = parse_service_config(raw)
    authority = _host_authority(config)
    resources = _host_resources(authority)
    market_transport = importlib.import_module("bt_api_transport_zmq.market_v2")
    service_transport = importlib.import_module("bt_api_transport_zmq.service_v2")
    validation_kwargs: list[dict[str, Any]] = []
    publisher_kwargs: list[dict[str, Any]] = []
    original_validate = market_transport.validate_publisher_configuration

    def validate(*args, **kwargs):
        validation_kwargs.append(dict(kwargs))
        return original_validate(*args, **kwargs)

    class Publisher:
        def __init__(self, publisher_endpoint, *_args, **kwargs):
            self.endpoint = publisher_endpoint
            self.options = dict(kwargs)
            publisher_kwargs.append(dict(kwargs))

        def publish(self, _event):
            return None

        def close(self):
            return None

    monkeypatch.setattr(market_transport, "validate_publisher_configuration", validate)
    monkeypatch.setattr(market_transport, "ZmqMarketPublisher", Publisher)
    monkeypatch.setattr(service_transport.ZmqServiceServer, "serve_once", lambda _s, _t: False)
    host = ServiceHost(
        config,
        authority,
        tmp_path / f"host-market-{version}.sqlite",
        lambda *_args: resources,
    ).start()
    try:
        assert host._registry is not None
        discovery = host._registry.handle(
            CLIENT.principal_ref,
            _request("service.discover", {}, scope=MARKET_SCOPE),
        )
        assert discovery["ok"] is True
        assert discovery["result"]["capabilities"] == [
            {
                "instance_id": "market-source",
                "port": "service.market_source",
                "operations": sorted(MARKET_OPERATIONS),
                "market_wire_version": version,
            }
        ]
        assert ("wire_version" in validation_kwargs[0]) is pass_version_keyword
        assert ("wire_version" in publisher_kwargs[0]) is pass_version_keyword
        if pass_version_keyword:
            assert validation_kwargs[0]["wire_version"] == 2
            assert publisher_kwargs[0]["wire_version"] == 2
        else:
            assert "wire_version" not in validation_kwargs[0]
            assert "wire_version" not in publisher_kwargs[0]
    finally:
        host.close()
