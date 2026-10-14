"""Configured history capabilities through the actual host and registry."""

from dataclasses import replace

import pytest
from bt_api_base.market_history_protocol import MarketBar, MarketHistoryPage, MarketHistoryRequest
from bt_api_gateway.market_hub import ALL_MARKET_OPERATIONS, MARKET_OPERATIONS, GatewayMarketHub

from bt_api_py.service_components import ComponentResources
from bt_api_py.service_host import ServiceHost, ServiceHostError
from tests.unit.test_service_host import (
    _CLIENT_IDENTITY,
    _endpoint,
    _market_authority,
    _market_hub_component,
    _market_key,
    _request,
    _service_config,
)


class Provider:
    def __init__(self):
        self.requests = []

    def subscribe(self, key, *, generation):
        return key

    def unsubscribe(self, handle):
        return None

    def fetch(self, request, timeout_seconds):
        self.requests.append(request)
        assert timeout_seconds == 2
        return MarketHistoryPage(
            request,
            (MarketBar(request.start_time_utc, request.end_time_utc, "10", "12", "9", "11", "2"),),
            True,
            None,
            request.end_time_utc,
        )


def _host(tmp_path, *, enabled, grant_history):
    config = _service_config(
        {"gateway": _market_hub_component()},
        service_id="market-service",
        market_endpoint=_endpoint(),
    )
    authority, scope = _market_authority(config)
    if grant_history:
        principal = authority.grants["curve-principal"][0]
        resource = replace(principal.resources[0], operations=ALL_MARKET_OPERATIONS)
        authority = replace(
            authority, grants={"curve-principal": (replace(principal, resources=(resource,)),)}
        )
    key = replace(_market_key(), channel="bar", timeframe="1m")
    provider = Provider()
    hub = GatewayMarketHub(
        provider,
        key_universe={key},
        principal_allowed_keys={"curve-principal": {key}},
        service_identity=authority.identity,
        service_scope=authority.scope,
        history_provider=provider if enabled else None,
        history_timeout_seconds=2 if enabled else None,
    )
    closes = []

    def close():
        hub.close()
        closes.append(True)

    resources = ComponentResources.owned({"hub": hub}, close=close, ready=lambda: True)
    host = ServiceHost(config, authority, tmp_path / "registry.sqlite3", lambda *_: resources)
    return host, provider, key, replace(scope, run_id="history-host-run"), closes


@pytest.mark.parametrize("enabled", [False, True])
def test_host_advertises_only_configured_history_and_dispatches(tmp_path, enabled):
    host, provider, key, scope, closes = _host(tmp_path, enabled=enabled, grant_history=enabled)
    host.start()
    try:
        registry = host._registry
        operations = ALL_MARKET_OPERATIONS if enabled else MARKET_OPERATIONS
        discovery = registry.handle(
            "curve-principal",
            _request("service.discover", {}, schema_id="sdk.service.control", scope=scope),
        )
        assert discovery["code"] == "OK"
        assert set(discovery["result"]["capabilities"][0]["operations"]) == operations
        assert set(host._components[0].handlers) == operations
        registered = registry.handle(
            "curve-principal",
            _request(
                "service.register",
                {
                    "registration_key": "history-registration",
                    "client_session_id": "history-session",
                    "instance_id": "market-source",
                    "port": "service.market_source",
                    "scope_ref": "market-grant-v1",
                    "operations": sorted(operations),
                },
                schema_id="sdk.service.control",
                identity=_CLIENT_IDENTITY,
                scope=scope,
            ),
        )
        assert registered["code"] == "OK"
        registration = registered["result"]
        query = MarketHistoryRequest(key, "2026-10-14T00:00:00Z", "2026-10-14T00:01:00Z", 1)
        payload = {
            "registration_id": registration["registration_id"],
            "service_epoch": registration["service_epoch"],
            "instance_id": "market-source",
            "arguments": query.to_dict(),
        }
        reply = registry.handle(
            "curve-principal",
            _request("market.history", payload, schema_id="sdk.service.call", scope=scope),
        )
        if enabled:
            assert reply["code"] == "OK"
            assert MarketHistoryPage.from_dict(reply["result"]).request == query
            assert provider.requests == [query]
            wrong_scope = replace(scope, account_ref="ungranted-account")
            denied = registry.handle(
                "curve-principal",
                _request(
                    "market.history", payload, schema_id="sdk.service.call", scope=wrong_scope
                ),
            )
            assert denied["code"] == "SCOPE_DENIED"
            assert provider.requests == [query]
        else:
            assert reply["code"] == "CAPABILITY_UNAVAILABLE"
            assert provider.requests == []
    finally:
        host.close()
    assert host.state == "closed"
    assert closes == [True]


def test_history_grant_without_provider_fails_start_and_closes_resources(tmp_path):
    host, provider, _key, _scope, closes = _host(tmp_path, enabled=False, grant_history=True)
    with pytest.raises(ServiceHostError):
        host.start()
    assert provider.requests == []
    assert closes == [True]
    host.close()
    assert closes == [True]
