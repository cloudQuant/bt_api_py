"""Public operator composition with one synthetic Gateway trading resource."""

from __future__ import annotations

from dataclasses import replace

import pytest
from bt_api_base.service_protocol import WireIdentity, WireRole, WireScope
from bt_api_gateway.market_hub import GatewayMarketHub, GatewayMarketHubError

from bt_api_py.gateway_operator import (
    GatewayOperatorDeployment,
    GatewayOperatorDeploymentError,
)
from bt_api_py.service_components import (
    ComponentResources,
    ServiceResourceConstructionError,
)
from bt_api_py.service_config import parse_client_config, parse_service_config
from bt_api_py.service_host import ServiceHostError
from bt_api_py.service_registry import (
    PrincipalGrant,
    ResourceGrant,
    ServiceCapability,
    ServiceRegistry,
)
from bt_api_py.service_runtime import ServiceClientRuntime, ServiceRuntimeError
from tests.integration.test_gateway_operator_deployment import (
    _ACCOUNT_REF,
    _DOMAIN_REF,
    _PRINCIPAL_REF,
    _SCOPE_REF,
    _SERVICE_ID,
    _SERVICE_INSTANCE,
    _STRATEGY_REF,
    _market_client_config,
)
from tests.integration.test_iter63_gateway_direct_service import (
    CLIENT_A,
    DOMAIN,
    SERVICE,
)
from tests.integration.test_iter63_gateway_dual_ports import _resources
from tests.unit.test_iter60_market_runtime_revocation import _Provider
from tests.unit.test_iter63_trading_market_component import (
    _authority,
    _build,
    _config,
    _hub,
    _prepared,
)
from tests.unit.test_iter63_gateway_direct_service import _context
from tests.unit.test_service_host import _market_key
from tests.unit.test_service_runtime import PEER, LocalTransport


@pytest.mark.parametrize(
    "account,market", [(False, False), (True, False), (False, True), (True, True)]
)
def test_all_trading_port_combinations_construct_without_resource_calls(
    tmp_path, account, market
):
    config = _config(account=account, market=market)
    calls = []
    deployment = GatewayOperatorDeployment(
        config,
        _authority(config),
        tmp_path / "registry.sqlite",
        resource_provider=lambda *args: calls.append(args),
    )
    assert deployment.state == "new"
    assert calls == []


def test_combined_options_keep_dispatch_and_cancel_checks(tmp_path):
    config = _config()
    raw = config.to_public_dict()
    raw["components"]["gateway"]["settings"]["options"].update(
        dispatch_enabled=True, cancel_enabled=True, bounded_read_after=True
    )
    config = parse_service_config(raw)
    deployment = GatewayOperatorDeployment(
        config,
        _authority(config),
        tmp_path / "registry.sqlite",
        resource_provider=lambda *_: None,
    )
    assert deployment.state == "new"


class AsyncProvider(_Provider):
    def begin_subscribe(self, key, *, generation):
        raise AssertionError("capability discovery must not call provider")

    def subscription_ready(self, handle):
        raise AssertionError("capability discovery must not call provider")

    def probe_source(self, handle, *, key, generation, stream_id, epoch):
        raise AssertionError("capability discovery must not call provider")


@pytest.mark.parametrize("provider_mode", ["sync", "partial", "async"])
def test_market_capabilities_follow_real_provider(tmp_path, provider_mode):
    async_operations = frozenset(
        {"market.bootstrap_begin", "market.bootstrap_status", "market.bootstrap_cancel"}
    )
    class PartialProvider(_Provider):
        def begin_subscribe(self, key, *, generation):
            raise AssertionError("capability discovery must not call provider")

    provider = {"sync": _Provider, "partial": PartialProvider, "async": AsyncProvider}[
        provider_mode
    ]()
    if provider_mode == "partial":
        with pytest.raises(GatewayMarketHubError, match="INVALID_ASYNC_PROVIDER"):
            GatewayMarketHub(
                provider,
                key_universe=(_market_key(),),
                principal_allowed_keys={CLIENT_A.principal_ref: {_market_key()}},
                service_identity=SERVICE,
                service_scope=WireScope(DOMAIN),
            )
        return
    hub = GatewayMarketHub(
        provider,
        key_universe=(_market_key(),),
        principal_allowed_keys={CLIENT_A.principal_ref: {_market_key()}},
        service_identity=SERVICE,
        service_scope=WireScope(DOMAIN),
    )
    hub.set_publisher(lambda _: None)
    binding, _, _ = _resources(tmp_path)
    try:
        component = _build(
            _prepared(_config(account=False)), {"binding": binding, "hub": hub}
        )
        market = next(
            c for c in component.capabilities if c.port == "service.market_source"
        )
        assert market.operations & async_operations == (
            async_operations if provider_mode == "async" else frozenset()
        )
        assert {"market.subscribe", "market.bootstrap_many"} <= market.operations
        assert not market.write_operations
    finally:
        hub.close()


@pytest.mark.parametrize("is_async", [False, True])
def test_host_advertises_only_configured_provider_capabilities(tmp_path, is_async):
    provider = AsyncProvider() if is_async else _Provider()
    config = _config()
    hub = GatewayMarketHub(
        provider,
        key_universe=(_market_key(),),
        principal_allowed_keys={CLIENT_A.principal_ref: {_market_key()}},
        service_identity=SERVICE,
        service_scope=WireScope(DOMAIN),
    )
    binding, read, _ = _resources(tmp_path)
    deployment = GatewayOperatorDeployment(
        config,
        _authority(config),
        tmp_path / "registry.sqlite",
        resource_provider=lambda *_: ComponentResources.owned(
            {"binding": binding, "read_service": read, "hub": hub},
            ready=lambda: True,
            close=hub.close,
        ),
    )
    try:
        deployment.start()
        market = next(
            c
            for c in deployment.service_host._components[0].capabilities
            if c.port == "service.market_source"
        )
        async_ops = frozenset(
            {
                "market.bootstrap_begin",
                "market.bootstrap_status",
                "market.bootstrap_cancel",
            }
        )
        assert market.operations & async_ops == (async_ops if is_async else frozenset())
    finally:
        deployment.close()


def test_unready_market_keeps_existing_bootstrap_job_observable_and_cancellable(tmp_path):
    target = _market_key()
    other = replace(target, source_ref="other-source")

    class PendingProvider(_Provider):
        release = False

        def begin_subscribe(self, key, *, generation):
            return key

        def subscription_ready(self, handle):
            return handle == other

        def probe_source(self, handle, *, key, generation, stream_id, epoch):
            return None

        def unsubscribe(self, handle):
            return self.release

    provider = PendingProvider()
    hub = GatewayMarketHub(
        provider,
        key_universe=(target, other),
        principal_allowed_keys={CLIENT_A.principal_ref: {target, other}},
        service_identity=SERVICE,
        service_scope=WireScope(DOMAIN),
    )
    hub.set_publisher(lambda _: None)
    binding, _, _ = _resources(tmp_path)
    component = _build(_prepared(_config(account=False)), {"binding": binding, "hub": hub})
    context = replace(_context(), result_fits=lambda _result: True)

    def call(name, args):
        return component.handlers["market.bootstrap_" + name](context, args)

    first = {"operation_id": "a" * 32, "subscriptions": [{"key": target.to_dict()}]}
    second = {"operation_id": "b" * 32, "subscriptions": [{"key": other.to_dict()}]}
    try:
        assert call("begin", first)["status"] == "pending"
        assert call("begin", second)["status"] == "pending"
        for _ in range(20):
            state = call("status", {"operation_id": second["operation_id"]})
            if state["status"] == "active":
                break
        assert state["status"] == "active"
        call("cancel", {"operation_id": second["operation_id"]})
        assert hub.ready is False and hub.readiness["pending_releases"] == 1
        status = call("status", {"operation_id": first["operation_id"]})
        repeated = call("begin", first)
        assert status["operation_id"] == repeated["operation_id"] == first["operation_id"]
        assert status["status"] == repeated["status"] == "pending"
        cancelled = call("cancel", {"operation_id": first["operation_id"]})
        assert cancelled["operation_id"] == first["operation_id"]
        assert cancelled["status"] in {"cancelling", "cancelled"}
        assert call("status", {"operation_id": first["operation_id"]})["operation_id"] == first[
            "operation_id"
        ]
        assert component.readiness() is True
    finally:
        provider.release = True
        hub.close()


@pytest.mark.parametrize(
    "explicit,grant_async", [(False, False), (False, True), (True, True), (True, False)]
)
def test_runtime_requires_explicit_opt_in_for_async_operations(
    tmp_path, explicit, grant_async
):
    new_operations = frozenset(
        {"market.bootstrap_begin", "market.bootstrap_status", "market.bootstrap_cancel"}
    )
    operations = frozenset({"market.subscribe"}) | new_operations
    identity = WireIdentity(WireRole.STRATEGY_CLIENT, _PRINCIPAL_REF, _STRATEGY_REF)
    scope = WireScope(_DOMAIN_REF, account_ref=_ACCOUNT_REF, strategy_ref=_STRATEGY_REF)
    resource = ResourceGrant(
        _SERVICE_INSTANCE,
        "service.market_source",
        _SCOPE_REF,
        scope,
        operations if grant_async else frozenset({"market.subscribe"}),
    )
    registry = ServiceRegistry(
        tmp_path / "runtime.sqlite",
        service_id=_SERVICE_ID,
        capabilities=(
            ServiceCapability(
                _SERVICE_INSTANCE,
                "service.market_source",
                operations,
                market_wire_version=1,
            ),
        ),
        grants={_PRINCIPAL_REF: (PrincipalGrant(identity, (resource,)),)},
        policy_version="p1",
        requirements_version="r1",
    )
    transport = LocalTransport(registry, principal_ref=_PRINCIPAL_REF)
    config = parse_client_config(
        _market_client_config("tcp://127.0.0.1:1", "tcp://127.0.0.1:2")
    )
    runtime = ServiceClientRuntime(
        config,
        identity=identity,
        scope_resolver=lambda _: scope,
        peer_resolver=lambda _: PEER,
        transport_factory=lambda *_: transport,
        run_id="run-a",
        requested_operations={"store.market": operations} if explicit else None,
    )
    try:
        if explicit and not grant_async:
            with pytest.raises(ServiceRuntimeError, match="SCOPE_DENIED"):
                runtime.start()
            return
        runtime.start()
        register = next(
            request
            for request in transport.requests
            if request.header.operation == "service.register"
        )
        assert frozenset(register.payload["operations"]) == (
            operations if explicit else frozenset({"market.subscribe"})
        )
    finally:
        runtime.close()
        registry.close()


def test_resource_provider_result_is_not_replaced(tmp_path):
    config = _config()
    resource = ComponentResources.borrowed({}, ready=lambda: True)
    seen = []

    def provide(*args):
        seen.append(args)
        return resource

    deployment = GatewayOperatorDeployment(
        config,
        _authority(config),
        tmp_path / "registry.sqlite",
        resource_provider=provide,
    )
    options = {"role": "trading_owner", "bounded_read_after": False}
    assert (
        deployment._provide_resources(
            "gateway", "gateway-a", options, WireScope(DOMAIN)
        )
        is resource
    )
    assert seen == [("gateway", "gateway-a", options, WireScope(DOMAIN))]


@pytest.mark.parametrize(
    "ports,options",
    [
        (["service.market_source"], {"role": "trading_owner"}),
        (["service.direct_write", "service.direct_write"], {"role": "trading_owner"}),
        (["service.direct_write", "service.unknown"], {"role": "trading_owner"}),
        (
            ["service.direct_write"],
            {"role": "trading_owner", "bounded_read_after": False},
        ),
        (
            ["service.direct_write", "service.market_source"],
            {"role": "trading_owner", "bounded_read_after": 1},
        ),
        (["service.direct_write"], {"role": "trading_owner", "cancel_enabled": True}),
        (["service.direct_write"], {"role": "trading_owner", "dispatch_enabled": 1}),
        (["service.direct_write"], {"role": "trading_owner", "extra": False}),
    ],
)
def test_operator_defensively_rejects_invalid_ports_and_options(
    tmp_path, ports, options
):
    config = _config()
    component = dict(config.components["gateway"])
    settings = dict(component["settings"])
    settings.update(bind_ports=ports, options=options)
    component["settings"] = settings
    object.__setattr__(config, "components", {"gateway": component})
    calls = []
    with pytest.raises(
        GatewayOperatorDeploymentError, match="INVALID_DEPLOYMENT_CONFIG"
    ):
        GatewayOperatorDeployment(
            config,
            _authority(config),
            tmp_path / "registry.sqlite",
            resource_provider=lambda *args: calls.append(args),
        )
    assert calls == []


@pytest.mark.parametrize(
    "keyword", ["market_binding", "account_session", "session_owner"]
)
def test_trading_owner_rejects_standalone_bindings(tmp_path, keyword):
    config = _config()
    calls = []
    with pytest.raises(GatewayOperatorDeploymentError, match="UNUSED_"):
        GatewayOperatorDeployment(
            config,
            _authority(config),
            tmp_path / "registry.sqlite",
            resource_provider=lambda *args: calls.append(args),
            **{keyword: object()},
        )
    assert calls == []


def test_three_ports_pass_through_and_start_public_publisher(tmp_path):
    config = _config(bounded=True)
    binding, read_service, _ = _resources(tmp_path)
    hub = _hub()
    calls, closed = [], []
    values = {"binding": binding, "read_service": read_service, "hub": hub}

    def provide(*args):
        calls.append(args)
        return ComponentResources.owned(
            values, ready=lambda: True, close=lambda: (closed.append(True), hub.close())
        )

    deployment = GatewayOperatorDeployment(
        config,
        _authority(config),
        tmp_path / "registry.sqlite",
        resource_provider=provide,
    )
    assert calls == []
    try:
        deployment.start()
        assert deployment.endpoint and deployment.market_endpoint and hub.ready
        assert len(calls) == 1
        assert calls[0][2]["bounded_read_after"] is True
        assert {
            cap.port for cap in deployment.service_host._components[0].capabilities
        } == {"service.direct_write", "service.account_read", "service.market_source"}
        deployment.serve_once(0)
    finally:
        deployment.close()
    assert closed == [True]


@pytest.mark.parametrize("partial_exception", [False, True])
def test_three_port_start_failure_closes_partial_resources(tmp_path, partial_exception):
    config = _config()
    hub = _hub()
    closed = []
    resource = ComponentResources.owned(
        {"hub": hub},
        ready=lambda: False,
        close=lambda: (closed.append(True), hub.close()),
    )

    def provide(*_):
        if partial_exception:
            raise ServiceResourceConstructionError(resource)
        return resource

    deployment = GatewayOperatorDeployment(
        config,
        _authority(config),
        tmp_path / "registry.sqlite",
        resource_provider=provide,
    )
    with pytest.raises(ServiceHostError):
        deployment.start()
    assert deployment.state == "failed" and closed == [True]
