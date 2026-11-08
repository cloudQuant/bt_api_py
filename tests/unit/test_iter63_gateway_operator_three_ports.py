"""Public operator composition with one synthetic Gateway trading resource."""

from __future__ import annotations

import pytest
from bt_api_base.service_protocol import WireScope

from bt_api_py.gateway_operator import (
    GatewayOperatorDeployment,
    GatewayOperatorDeploymentError,
)
from bt_api_py.service_components import (
    ComponentResources,
    ServiceResourceConstructionError,
)
from bt_api_py.service_host import ServiceHostError
from tests.integration.test_iter63_gateway_dual_ports import _resources
from tests.integration.test_iter63_gateway_direct_service import DOMAIN
from tests.unit.test_iter63_trading_market_component import _authority, _config, _hub
from bt_api_py.service_config import parse_service_config


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
