"""Synthetic three-port Gateway composition using existing public service contracts."""

from __future__ import annotations

from dataclasses import replace

import pytest
import zmq
from bt_api_base.service_protocol import WireScope
from bt_api_base.market_history_protocol import MarketHistoryRequest
from bt_api_gateway.market_hub import (
    GatewayMarketHub,
    GatewayMarketHubError,
    GatewayMarketService,
    GatewayMarketServiceError,
)

from bt_api_py.service_components import (
    ComponentResources,
    ServiceComponentError,
    build_component,
    component_specs,
    validate_components,
)
from bt_api_py.service_config import ServiceConfigError, parse_service_config
from bt_api_py.service_host import ServiceHost, ServiceHostError, TrustedServiceAuthority
from bt_api_py.service_registry import (
    PrincipalGrant,
    ResourceGrant,
    ServiceOperationDeniedError,
    ServiceCapability,
    ServiceRegistry,
    ServiceResultTooLargeError,
)
from tests.integration.test_iter63_gateway_direct_service import (
    ACCOUNT,
    CLIENT_A,
    DOMAIN,
    SERVICE,
    _service_config,
)
from tests.integration.test_iter63_gateway_dual_ports import _resources
from tests.integration.test_service_account_instrument_metadata import _free_endpoint
from tests.unit.test_iter60_market_runtime_revocation import _Provider
from tests.unit.test_iter63_gateway_direct_service import _context
from tests.unit.test_service_host import _market_key
from tests.unit.test_service_registry import _next_request, _register


def _config(*, account=True, market=True, bounded=False):
    raw = _service_config(_free_endpoint()).to_public_dict()
    settings = raw["components"]["gateway"]["settings"]
    if account:
        settings["bind_ports"].append("service.account_read")
    if market:
        settings["bind_ports"].append("service.market_source")
        settings["options"]["bounded_read_after"] = bounded
        raw["communication"]["profiles"]["server-transport"]["endpoints"][
            "market_bind"
        ] = _free_endpoint()
    return parse_service_config(raw)


def _hub(*, scope=None, principal=CLIENT_A.principal_ref):
    return GatewayMarketHub(
        _Provider(),
        key_universe=(_market_key(),),
        principal_allowed_keys={principal: {_market_key()}},
        service_identity=SERVICE,
        service_scope=scope or WireScope(DOMAIN),
    )


def _prepared(config):
    return validate_components(component_specs(config.components))[0]


def _build(prepared, values, *, ready=lambda: True):
    return build_component(
        prepared,
        scope=WireScope(DOMAIN),
        service_identity=SERVICE,
        resource_provider=lambda *_: ComponentResources.borrowed(values, ready=ready),
    )


def _authority(config):
    server_public, server_secret = zmq.curve_keypair()
    client_public, _ = zmq.curve_keypair()
    scope = WireScope(DOMAIN, account_ref=ACCOUNT, strategy_ref=CLIENT_A.actor_ref)
    resources = tuple(
        ResourceGrant("gateway-a", port, "scope-a", scope, operations)
        for port, operations in (
            ("service.direct_write", frozenset({"direct.command_status"})),
            ("service.account_read", frozenset({"account.snapshot"})),
            ("service.market_source", frozenset({"market.subscribe", "market.unsubscribe"})),
        )
    )
    return TrustedServiceAuthority(
        identity=SERVICE,
        scope=WireScope(DOMAIN),
        scope_ref="scope-a",
        scope_version="scope-v1",
        security_ref="security-a",
        security_version="security-v1",
        registration_policy_ref=config.registration_policy_ref,
        registration_policy_version="policy-v1",
        service_requirements_ref=config.service_requirements_ref,
        service_requirements_version="requirements-v1",
        curve_public_key=server_public,
        curve_secret_key=server_secret,
        principals={client_public: CLIENT_A.principal_ref},
        grants={CLIENT_A.principal_ref: (PrincipalGrant(CLIENT_A, resources),)},
    )


def test_public_configuration_rejects_missing_direct_publisher_and_bad_options():
    config = _config()
    assert set(config.components["gateway"]["settings"]["bind_ports"]) == {
        "service.direct_write",
        "service.account_read",
        "service.market_source",
    }
    raw = config.to_public_dict()
    raw["components"]["gateway"]["settings"]["bind_ports"].remove("service.direct_write")
    with pytest.raises(ServiceComponentError):
        _prepared(parse_service_config(raw))
    raw = config.to_public_dict()
    del raw["communication"]["profiles"]["server-transport"]["endpoints"]["market_bind"]
    with pytest.raises(ServiceConfigError, match="MISSING_ENDPOINT"):
        parse_service_config(raw)
    raw = _config(market=False).to_public_dict()
    raw["components"]["gateway"]["settings"]["options"]["bounded_read_after"] = True
    with pytest.raises(ServiceConfigError):
        parse_service_config(raw)
    raw = config.to_public_dict()
    raw["components"]["gateway"]["settings"]["options"]["bounded_read_after"] = 1
    with pytest.raises(ServiceConfigError):
        parse_service_config(raw)


def test_build_routes_capabilities_authorization_and_registration_cleanup(tmp_path, monkeypatch):
    binding, read, _ = _resources(tmp_path)
    hub = _hub()
    hub.set_publisher(lambda _: None)
    component = _build(
        _prepared(_config(bounded=True)), {"binding": binding, "read_service": read, "hub": hub}
    )
    caps = {c.port: c for c in component.capabilities}
    assert set(caps) == {"service.direct_write", "service.account_read", "service.market_source"}
    assert "market.read_after_bounded" in caps["service.market_source"].operations
    assert "market.history" not in caps["service.market_source"].operations
    assert not caps["service.market_source"].write_operations
    assert not binding.supported_operations.intersection(caps["service.market_source"].operations)
    context = _context()
    assert (
        component.handlers["direct.command_status"](context, {"request_key": "missing"})["code"]
        == "NOT_FOUND"
    )
    assert component.handlers["account.snapshot"](context, {}) is not None
    result = component.handlers["market.subscribe"](context, {"key": _market_key().to_dict()})
    assert result is not None
    assert hub.readiness["active_registrations"] == 1
    assert component.maintain_registrations is not None
    component.maintain_registrations(())
    assert hub.readiness["active_registrations"] == 0
    with pytest.raises(ServiceOperationDeniedError):
        component.handlers["market.subscribe"](
            context, {"key": replace(_market_key(), source_ref="denied").to_dict()}
        )
    previous = GatewayMarketService.handle

    def too_large(self, operation, **kwargs):
        if operation == "market.read_after_bounded":
            raise GatewayMarketServiceError("REPLAY_PAGE_TOO_LARGE")
        return previous(self, operation, **kwargs)

    monkeypatch.setattr(GatewayMarketService, "handle", too_large)
    with pytest.raises(ServiceResultTooLargeError):
        component.handlers["market.read_after_bounded"](context, {})
    hub.close()


def test_history_only_advertised_with_real_history_provider(tmp_path):
    binding, _, _ = _resources(tmp_path)

    class History:
        def fetch(self, request, timeout_seconds):
            raise AssertionError("advertisement must not fetch")

    hub = GatewayMarketHub(
        _Provider(),
        key_universe=(_market_key(),),
        principal_allowed_keys={CLIENT_A.principal_ref: {_market_key()}},
        service_identity=SERVICE,
        service_scope=WireScope(DOMAIN),
        history_provider=History(),
        history_timeout_seconds=1,
    )
    hub.set_publisher(lambda _: None)
    try:
        component = _build(_prepared(_config(account=False)), {"binding": binding, "hub": hub})
        market = next(cap for cap in component.capabilities if cap.port == "service.market_source")
        assert "market.history" in market.operations
        assert "market.read_after_bounded" not in market.operations
    finally:
        hub.close()


def test_history_acl_rejection_is_a_scope_denial(tmp_path):
    binding, _, _ = _resources(tmp_path)
    history_key = replace(_market_key(), channel="bar", depth=None, timeframe="1m")

    class History:
        def fetch(self, request, timeout_seconds):
            raise AssertionError("denied history must not reach provider")

    hub = GatewayMarketHub(
        _Provider(), key_universe=(_market_key(),),
        principal_allowed_keys={CLIENT_A.principal_ref: {_market_key()}},
        service_identity=SERVICE, service_scope=WireScope(DOMAIN),
        history_provider=History(), history_timeout_seconds=1,
        history_key_universe=(history_key,),
        principal_allowed_history_keys={CLIENT_A.principal_ref: ()},
    )
    hub.set_publisher(lambda _: None)
    component = _build(_prepared(_config(account=False)), {"binding": binding, "hub": hub})
    request = MarketHistoryRequest(
        key=history_key, start_time_utc="2026-01-01T00:00:00Z",
        end_time_utc="2026-01-01T00:02:00Z", limit=100,
    )
    try:
        with pytest.raises(ServiceOperationDeniedError):
            component.handlers["market.history"](_context(), request.to_dict())
        context = _context()
        operations = frozenset({"market.history"})
        registry = ServiceRegistry(
            tmp_path / "history-registry.sqlite", service_id="history-service",
            capabilities=(ServiceCapability("gateway-a", "service.market_source", operations),),
            grants={context.principal_ref: (PrincipalGrant(
                context.identity, (ResourceGrant(
                    "gateway-a", "service.market_source", "scope-a",
                    replace(context.scope, run_id=None), operations,
                ),),
            ),)}, policy_version="policy-v1", requirements_version="requirements-v1",
        )
        try:
            registry.register_handler("gateway-a", "market.history", component.handlers["market.history"])
            registration = _register(
                registry, principal=context.principal_ref, identity=context.identity,
                scope=context.scope, operations=("market.history",), instance_id="gateway-a",
                port="service.market_source", scope_ref="scope-a",
            )["result"]
            response = registry.handle(context.principal_ref, _next_request(
                "market.history", {
                    "registration_id": registration["registration_id"],
                    "service_epoch": registration["service_epoch"],
                    "instance_id": "gateway-a", "arguments": request.to_dict(),
                }, identity=context.identity, scope=context.scope,
            ))
            assert response["ok"] is False and response["code"] == "SCOPE_DENIED"
        finally:
            registry.close()
    finally:
        hub.close()


def test_market_release_failure_does_not_close_trading_or_account(tmp_path):
    binding, read, _ = _resources(tmp_path)

    class FailingRelease(_Provider):
        release = False

        def unsubscribe(self, handle):
            return self.release

    provider = FailingRelease()
    hub = GatewayMarketHub(
        provider, key_universe=(_market_key(),),
        principal_allowed_keys={CLIENT_A.principal_ref: {_market_key()}},
        service_identity=SERVICE, service_scope=WireScope(DOMAIN),
    )
    hub.set_publisher(lambda _: None)
    component = _build(_prepared(_config()), {"binding": binding, "read_service": read, "hub": hub})
    context = _context()
    try:
        component.handlers["market.subscribe"](context, {"key": _market_key().to_dict()})
        component.maintain_registrations(())
        assert hub.readiness["pending_releases"] == 1
        assert component.readiness() is True
        assert component.handlers["direct.command_status"](context, {"request_key": "missing"})[
            "code"
        ] == "NOT_FOUND"
        assert component.handlers["account.snapshot"](context, {}) is not None
        assert component.handlers["market.head"](context, {"key": _market_key().to_dict()}) == {
            "status": "rejected", "code": "MARKET_NOT_READY"
        }
        with pytest.raises(GatewayMarketHubError, match="PROVIDER_UNSUBSCRIBE_FAILED"):
            component.handlers["market.unsubscribe"](context, {"key": _market_key().to_dict()})
    finally:
        provider.release = True
        hub.close()


@pytest.mark.parametrize("problem", ["missing", "extra", "wrong", "domain"])
def test_invalid_composite_resources_fail_and_close(tmp_path, problem):
    binding, read, _ = _resources(tmp_path)
    hub = _hub(scope=WireScope("other") if problem == "domain" else None)
    closed = []
    values = {"binding": binding, "read_service": read, "hub": hub}
    if problem == "missing":
        del values["hub"]
    elif problem == "extra":
        values["extra"] = object()
    elif problem == "wrong":
        values["hub"] = object()
    with pytest.raises(ServiceComponentError):
        build_component(
            _prepared(_config()),
            scope=WireScope(DOMAIN),
            service_identity=SERVICE,
            resource_provider=lambda *_: ComponentResources.owned(
                values, ready=lambda: True, close=lambda: (closed.append(True), hub.close())
            ),
        )
    assert closed == [True]


def test_actual_host_publisher_binding_and_resource_cleanup(tmp_path):
    config = _config()
    binding, read, _ = _resources(tmp_path)
    hub = _hub()
    closed, resource_maintenance = [], []
    resources = ComponentResources.owned(
        {"binding": binding, "read_service": read, "hub": hub},
        ready=lambda: True,
        close=lambda: (closed.append(True), hub.close()),
        maintain=lambda active: resource_maintenance.append(active),
    )
    host = ServiceHost(
        config, _authority(config), tmp_path / "registry.sqlite", lambda *_: resources
    )
    try:
        host.start()
        assert host.market_endpoint is not None and hub.ready
        component = host._components[0]
        assert {c.port for c in component.capabilities} == {
            "service.direct_write",
            "service.account_read",
            "service.market_source",
        }
        hub.subscribe(CLIENT_A.principal_ref, _market_key(), "expired-registration")
        assert hub.readiness["active_registrations"] == 1
        host._maintain_components(registrations_only=True)
        assert hub.readiness["active_registrations"] == 0
        assert resource_maintenance
        host.serve_once(0)
    finally:
        host.close()
    assert closed == [True]


@pytest.mark.parametrize("problem", ["wrong", "domain", "extra", "audience"])
def test_actual_host_start_failure_retains_and_closes_resources(tmp_path, problem):
    config = _config()
    binding, read, _ = _resources(tmp_path)
    hub = _hub(
        scope=WireScope("other") if problem == "domain" else None,
        principal="unknown" if problem == "audience" else CLIENT_A.principal_ref,
    )
    values = {"binding": binding, "read_service": read, "hub": hub}
    if problem == "wrong":
        values["hub"] = object()
    elif problem == "extra":
        values["extra"] = object()
    closed = []
    resources = ComponentResources.owned(
        values, ready=lambda: True, close=lambda: (closed.append(True), hub.close())
    )
    host = ServiceHost(
        config, _authority(config), tmp_path / "registry.sqlite", lambda *_: resources
    )
    with pytest.raises(ServiceHostError):
        host.start()
    assert closed == [True]


@pytest.mark.parametrize(
    "problem", ["instance", "scope_ref", "port", "operation", "domain", "extra_acl"]
)
def test_trading_market_publisher_rejects_ungranted_audience(tmp_path, problem):
    config = _config()
    authority = _authority(config)
    principal = CLIENT_A.principal_ref
    grant = authority.grants[principal][0]
    market = grant.resources[2]
    changes = {
        "instance": {"instance_id": "other-instance"},
        "scope_ref": {"scope_ref": "other-scope"},
        "port": {"port": "service.account_read"},
        "operation": {"operations": frozenset({"market.head"})},
        "domain": {"scope": replace(market.scope, execution_domain_ref="other")},
    }
    if problem in changes:
        market = replace(market, **changes[problem])
        grant = replace(grant, resources=grant.resources[:2] + (market,))
        authority = replace(authority, grants={principal: (grant,)})
    binding, read, _ = _resources(tmp_path)
    if problem == "extra_acl":
        extra_public, _ = zmq.curve_keypair()
        extra = "direct-only"
        authority = replace(
            authority,
            principals={**authority.principals, extra_public: extra},
            grants={**authority.grants, extra: (replace(grant, resources=grant.resources[:1]),)},
        )
        hub = GatewayMarketHub(
            _Provider(), key_universe=(_market_key(),),
            principal_allowed_keys={principal: {_market_key()}, extra: {_market_key()}},
            service_identity=SERVICE, service_scope=authority.scope,
        )
    else:
        hub = _hub(scope=authority.scope)
    closed = []
    resources = ComponentResources.owned(
        {"binding": binding, "read_service": read, "hub": hub},
        ready=lambda: True, close=lambda: (closed.append(True), hub.close()),
    )
    host = ServiceHost(config, authority, tmp_path / "registry.sqlite", lambda *_: resources)
    if problem != "extra_acl":
        assert host._trading_market_principals("gateway-a", ACCOUNT) == {}
    try:
        with pytest.raises(ServiceHostError) as rejected:
            host.start()
    finally:
        host.close()
    assert rejected.value.code in {"MARKET_HUB_AUDIENCE_INVALID", "AUTHORITY_GRANTS_INVALID"}
    assert closed == ([] if rejected.value.code == "AUTHORITY_GRANTS_INVALID" else [True])
    if not closed:
        hub.close()


def test_trading_market_audience_respects_restricted_account_scope(tmp_path):
    config = _config()
    authority = _authority(config)
    principal = CLIENT_A.principal_ref
    grant = authority.grants[principal][0]
    authority = replace(authority, scope=WireScope(DOMAIN, account_ref=ACCOUNT))
    host = ServiceHost(config, authority, tmp_path / "registry.sqlite", lambda *_: None)
    assert set(host._trading_market_principals("gateway-a", ACCOUNT).values()) == {principal}
    wrong_scope = replace(grant.resources[2].scope, account_ref="other-account")
    wrong_market = replace(grant.resources[2], scope=wrong_scope)
    host._authority = replace(
        authority, grants={principal: (replace(grant, resources=grant.resources[:2] + (wrong_market,)),)}
    )
    assert host._trading_market_principals("gateway-a", ACCOUNT) == {}
    host._authority = authority
    assert host._trading_market_principals("gateway-a", "account-other") == {}


def test_domain_only_host_excludes_other_account_from_publisher(tmp_path, monkeypatch):
    from bt_api_transport_zmq import market_v2

    config = _config()
    authority = _authority(config)
    principal = CLIENT_A.principal_ref
    grant = authority.grants[principal][0]
    other_identity = replace(CLIENT_A, principal_ref="principal-other", actor_ref="strategy-other")
    other_public, _ = zmq.curve_keypair()
    other_market = replace(grant.resources[2], scope=replace(
        grant.resources[2].scope, account_ref="account-other", strategy_ref="strategy-other"
    ))
    authority = replace(
        authority,
        principals={**authority.principals, other_public: other_identity.principal_ref},
        grants={**authority.grants, other_identity.principal_ref: (
            PrincipalGrant(other_identity, (other_market,)),
        )},
    )
    binding, read, _ = _resources(tmp_path)
    hub = _hub()
    captured = []
    original = market_v2.ZmqMarketPublisher

    def publisher(endpoint, identity, scope, principals, **options):
        captured.append(dict(principals))
        return original(endpoint, identity, scope, principals, **options)

    monkeypatch.setattr(market_v2, "ZmqMarketPublisher", publisher)
    resources = ComponentResources.owned(
        {"binding": binding, "read_service": read, "hub": hub},
        ready=lambda: True, close=hub.close,
    )
    host = ServiceHost(config, authority, tmp_path / "registry.sqlite", lambda *_: resources)
    try:
        host.start()
        assert set(captured[0].values()) == {principal}
        assert other_public not in captured[0]
    finally:
        host.close()
