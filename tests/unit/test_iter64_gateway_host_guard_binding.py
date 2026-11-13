"""Local Host/owner binding only; no provider account or network listener."""

import threading
from dataclasses import replace
from types import MappingProxyType

import bt_api_transport_zmq.service_v2 as transport
import pytest
from bt_api_gateway.session_guard import LocalAccountSessionGuard, LocalAccountSessionGuardError

import bt_api_py.ctp_gateway_trading as trading
import tests.test_iter63_ctp_gateway_trading as fixtures
from bt_api_py._gateway_owner_guard_binding import _GatewayOwnerGuardBinding
from bt_api_py.gateway_direct_owner import GatewayOwnerServiceCallHandle
from bt_api_py.service_config import parse_service_config
from bt_api_py.service_host import ServiceHostError
from bt_api_py.service_registry import ServiceRegistry


class FakeListener:
    instances = []
    close_allowed = True

    def __init__(self, endpoint, *args, **kwargs):
        self.endpoint = endpoint
        self.instances.append(self)

    def close(self):
        if not self.close_allowed:
            raise RuntimeError("synthetic close failure")


def profile(tmp_path, monkeypatch):
    FakeListener.instances = []
    FakeListener.close_allowed = True
    monkeypatch.setattr(fixtures, "_free_endpoint", lambda: "tcp://127.0.0.1:19999")
    monkeypatch.setattr(transport, "ZmqServiceServer", FakeListener)
    return fixtures.make(tmp_path, monkeypatch)


def test_host_binds_before_listener_and_revokes_before_failed_close(tmp_path, monkeypatch):
    built, settings, _, _, _, _ = profile(tmp_path, monkeypatch)
    deployment = built.deployment_factory()
    host = deployment.service_host
    original_init = FakeListener.__init__

    def listener_init(self, *args, **kwargs):
        assert len(host._gateway_owner_guard_bindings) == 1
        row = host._gateway_owner_guard_bindings["gateway-a"]
        assert row[0] is host._registry and row[1] == "gateway-a"
        assert type(row[4]) is _GatewayOwnerGuardBinding
        original_init(self, *args, **kwargs)

    monkeypatch.setattr(FakeListener, "__init__", listener_init)
    deployment.start()
    FakeListener.close_allowed = False
    with pytest.raises(ServiceHostError, match="CLOSE_FAILED"):
        deployment.close()
    assert not host._gateway_owner_guard_bindings
    contender = LocalAccountSessionGuard(settings.guard_root, settings.account_scope, "other")
    with pytest.raises(LocalAccountSessionGuardError):
        contender.acquire()
    FakeListener.close_allowed = True
    deployment.close()
    contender.acquire()
    contender.release()


def test_absent_holder_remains_compatible(tmp_path, monkeypatch):
    built, _, _, _, _, _ = profile(tmp_path, monkeypatch)
    deployment = built.deployment_factory()
    host = deployment.service_host
    original = host._resource_provider

    def without_holder(*args, **kwargs):
        resource = original(*args, **kwargs)
        return replace(resource, values={k: v for k, v in resource.values.items()
                                         if k != "owner_guard_binding"})

    host._resource_provider = without_holder
    deployment.start()
    try:
        assert host._gateway_owner_guard_bindings == {}
        assert len(FakeListener.instances) == 1
    finally:
        deployment.close()


def test_holder_substitution_fails_before_listener(tmp_path, monkeypatch):
    first = tmp_path / "first"
    second = tmp_path / "second"
    first.mkdir()
    second.mkdir()
    built, _, _, _, _, _ = profile(first, monkeypatch)
    other, _, _, other_authority, _, _ = fixtures.make(second, monkeypatch)
    other_resource = fixtures._resources(other, other_authority)
    deployment = built.deployment_factory()
    host = deployment.service_host
    original = host._resource_provider

    def substitute(*args, **kwargs):
        resource = original(*args, **kwargs)
        return replace(resource, values={**resource.values,
                       "owner_guard_binding": other_resource.values["owner_guard_binding"]})

    host._resource_provider = substitute
    try:
        with pytest.raises(ServiceHostError, match="GATEWAY_OWNER_GUARD_BINDING_INVALID"):
            deployment.start()
        assert host._gateway_owner_guard_bindings == {}
        assert not FakeListener.instances
    finally:
        other_resource.close()


def test_two_factories_swapped_as_complete_resources_fail_first_binding(tmp_path, monkeypatch):
    first, second = tmp_path / "first", tmp_path / "second"
    first.mkdir()
    second.mkdir()
    built_a, _, _, authority_a, _, _ = profile(first, monkeypatch)
    _, settings_b, config_b, authority_b, grants_b, _ = fixtures.make(second, monkeypatch)
    raw = config_b.to_public_dict()
    raw["components"]["gateway"]["settings"]["instance_id"] = "gateway-b"
    authority_b = replace(authority_b, grants={
        principal: tuple(replace(grant, resources=tuple(
            replace(resource, instance_id="gateway-b") for resource in grant.resources
        )) for grant in grants)
        for principal, grants in authority_b.grants.items()
    })
    built_b = trading.build_ctp_gateway_trading_profile(
        parse_service_config(raw), authority_b, second / "registry.sqlite",
        profile_ref="other", settings=settings_b, grants=grants_b,
        request_feed_factory=lambda: None, market_stream_factory=lambda *args: None,
        assert_admitted=lambda _: True,
    )
    deployment_a, deployment_b = built_a.deployment_factory(), built_b.deployment_factory()
    resource_a = deployment_a._provide_resources(
        "gateway", "gateway-a", {"role": "trading_owner", "dispatch_enabled": True},
        authority_a.scope,
    )
    resource_b = deployment_b._provide_resources(
        "gateway", "gateway-b", {"role": "trading_owner", "dispatch_enabled": True},
        authority_b.scope,
    )
    deployment_a.service_host._resource_provider = lambda *args, **kwargs: resource_b
    deployment_b.service_host._resource_provider = lambda *args, **kwargs: resource_a
    try:
        for deployment in (deployment_a, deployment_b):
            with pytest.raises(ServiceHostError, match="GATEWAY_OWNER_GUARD_BINDING_INVALID"):
                deployment.start()
            assert not deployment.service_host._gateway_owner_guard_bindings
            assert not FakeListener.instances
    finally:
        resource_a.close()
        resource_b.close()


def test_same_owner_cannot_be_fixed_to_another_instance(tmp_path, monkeypatch):
    built, _, _, _, _, _ = profile(tmp_path, monkeypatch)
    deployment = built.deployment_factory()
    host = deployment.service_host
    original = host._resource_provider
    captured = {}

    def capture(*args, **kwargs):
        captured["resource"] = original(*args, **kwargs)
        return captured["resource"]

    host._resource_provider = capture
    deployment.start()
    try:
        component = host._components[0]
        other_component = replace(
            component, instance_id="other-instance",
            capabilities=tuple(replace(item, instance_id="other-instance")
                               for item in component.capabilities),
        )
        host._components.append(other_component)
        try:
            with pytest.raises(ServiceHostError, match="GATEWAY_OWNER_GUARD_BINDING_INVALID"):
                host._bind_gateway_owner_guard(host._registry, host._prepared[0],
                                              other_component, captured["resource"],
                                              tuple(host._registry._capabilities.values()))
            assert tuple(host._gateway_owner_guard_bindings) == ("gateway-a",)
        finally:
            host._components.pop()
    finally:
        deployment.close()


def test_validation_failure_retains_failed_owner_cleanup(tmp_path, monkeypatch):
    built, settings, _, _, _, _ = profile(tmp_path, monkeypatch)
    deployment = built.deployment_factory()
    host = deployment.service_host
    original = host._resource_provider

    def fail_cleanup(*args, **kwargs):
        resource = original(*args, **kwargs)
        fixtures.FakeSession.instances[-1].close_allowed = False
        return resource

    def fail_check(*args):
        raise RuntimeError("synthetic binding failure")

    host._resource_provider = fail_cleanup
    monkeypatch.setattr(_GatewayOwnerGuardBinding, "assert_bound", fail_check)
    contender = LocalAccountSessionGuard(settings.guard_root, settings.account_scope, "other")
    try:
        with pytest.raises(ServiceHostError, match="START_CLEANUP_FAILED"):
            deployment.start()
        assert not host._gateway_owner_guard_bindings and not FakeListener.instances
        with pytest.raises(LocalAccountSessionGuardError):
            contender.acquire()
    finally:
        fixtures.FakeSession.instances[-1].close_allowed = True
        deployment.close()
    contender.acquire()
    contender.release()


def test_closing_after_owner_check_prevents_publication(tmp_path, monkeypatch):
    built, _, _, _, _, _ = profile(tmp_path, monkeypatch)
    original = _GatewayOwnerGuardBinding.assert_bound

    def close_after_check(self, owner, journal):
        original(self, owner, journal)
        self._GatewayOwnerGuardBinding__closing.set()

    monkeypatch.setattr(_GatewayOwnerGuardBinding, "assert_bound", close_after_check)
    deployment = built.deployment_factory()
    with pytest.raises(ServiceHostError, match="GATEWAY_OWNER_GUARD_BINDING_INVALID"):
        deployment.start()
    assert not deployment.service_host._gateway_owner_guard_bindings
    assert not FakeListener.instances


def test_direct_owner_close_between_check_and_publication_is_rejected(tmp_path, monkeypatch):
    built, _, _, _, _, _ = profile(tmp_path, monkeypatch)
    original = _GatewayOwnerGuardBinding.assert_bound
    close_results = []

    def close_owner_after_check(self, owner, journal):
        original(self, owner, journal)
        closer = threading.Thread(target=lambda: close_results.append(owner.close(0.01)))
        closer.start()
        closer.join(timeout=1)
        assert not closer.is_alive() and close_results == [False]
        assert owner._closing and not self._GatewayOwnerGuardBinding__closing.is_set()

    monkeypatch.setattr(_GatewayOwnerGuardBinding, "assert_bound", close_owner_after_check)
    deployment = built.deployment_factory()
    with pytest.raises(ServiceHostError, match="GATEWAY_OWNER_GUARD_BINDING_INVALID"):
        deployment.start()
    assert not deployment.service_host._gateway_owner_guard_bindings
    assert not FakeListener.instances


def test_registry_capability_substitution_prevents_publication(tmp_path, monkeypatch):
    built, _, _, _, _, _ = profile(tmp_path, monkeypatch)
    original_init = ServiceRegistry.__init__

    def altered_capability(self, *args, **kwargs):
        original_init(self, *args, **kwargs)
        key = ("gateway-a", "service.direct_write")
        cap = self._capabilities[key]
        capabilities = dict(self._capabilities)
        capabilities[key] = replace(
            cap, operations=cap.operations - {"direct.release"},
            write_operations=cap.write_operations - {"direct.release"},
        )
        self._capabilities = MappingProxyType(capabilities)

    monkeypatch.setattr(ServiceRegistry, "__init__", altered_capability)
    deployment = built.deployment_factory()
    with pytest.raises(ServiceHostError, match="GATEWAY_OWNER_GUARD_BINDING_INVALID"):
        deployment.start()
    assert not deployment.service_host._gateway_owner_guard_bindings
    assert not FakeListener.instances


def test_running_timeout_retains_guard_until_owner_cleanup(tmp_path, monkeypatch):
    built, settings, _, _, _, _ = profile(tmp_path, monkeypatch)
    entered, release = threading.Event(), threading.Event()
    original_check = _GatewayOwnerGuardBinding.assert_bound
    original_result = GatewayOwnerServiceCallHandle.result

    def blocked_check(self, owner, journal):
        entered.set()
        assert release.wait(3)
        return original_check(self, owner, journal)

    def short_result(self, *, timeout_seconds=5.0):
        if getattr(self._task.callback, "__name__", "") == "bind_on_owner":
            return original_result(self, timeout_seconds=0.05)
        return original_result(self, timeout_seconds=timeout_seconds)

    monkeypatch.setattr(_GatewayOwnerGuardBinding, "assert_bound", blocked_check)
    monkeypatch.setattr(GatewayOwnerServiceCallHandle, "result", short_result)
    deployment = built.deployment_factory()
    host = deployment.service_host
    contender = LocalAccountSessionGuard(settings.guard_root, settings.account_scope, "other")
    try:
        with pytest.raises(ServiceHostError, match="START_CLEANUP_FAILED"):
            deployment.start()
        assert entered.is_set() and not host._gateway_owner_guard_bindings
        assert not FakeListener.instances
        with pytest.raises(LocalAccountSessionGuardError):
            contender.acquire()
    finally:
        release.set()
        deployment.close()
    assert not host._gateway_owner_guard_bindings
    contender.acquire()
    contender.release()
