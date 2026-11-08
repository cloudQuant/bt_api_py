"""Synthetic cancel capability planning and same-owner CTP composition."""

from __future__ import annotations

import queue
import threading
from dataclasses import replace

import pytest
from bt_api_base.direct_write_protocol import SettlementStatus
from bt_api_base.service_protocol import WireScope

import bt_api_py.ctp_gateway_trading as trading
from bt_api_py.gateway_direct_owner import GatewayCancelCommand
from bt_api_py.gateway_direct_service import (
    CANCEL_OPERATIONS,
    EVENT_OPERATIONS,
    SUPPORTED_OPERATIONS,
    GatewayDirectServiceBinding,
    validate_service_options,
)
from bt_api_py.service_components import (
    ComponentResources,
    ServiceComponentError,
    build_component,
    component_specs,
    validate_components,
)
from bt_api_py.service_config import ServiceConfigError, parse_service_config
from bt_api_py.service_registry import PrincipalGrant
from bt_api_py.service_runtime import PORT_OPERATIONS, ServiceClientRuntime
from tests.integration.test_iter63_gateway_direct_service import CLIENT_A, DOMAIN, _service_config
from tests.test_iter63_ctp_gateway_trading import (
    FakeProvider,
    FakeReturnMaintenance,
    FakeSession,
    make,
)
from tests.test_iter63_gateway_direct_owner import Harness, fence
from tests.unit.test_iter63_gateway_cancel_owner import owner_for, with_cancel


def _config(*, dispatch=True, cancel=False):
    raw = _service_config("tcp://127.0.0.1:12345").to_public_dict()
    raw["components"]["gateway"]["settings"]["options"] = {
        "role": "trading_owner",
        "dispatch_enabled": dispatch,
        "cancel_enabled": cancel,
    }
    return parse_service_config(raw)


def _prepared(config):
    return validate_components(component_specs(config.components))[0]


def _build(prepared, binding):
    return build_component(
        prepared,
        scope=WireScope(DOMAIN),
        resource_provider=lambda *_: ComponentResources.borrowed(
            {"binding": binding}, ready=lambda: True
        ),
    )


def test_options_are_explicit_and_require_dispatch():
    ports = ("service.direct_write",)
    assert validate_service_options({"role": "trading_owner"}, ports) == {
        "role": "trading_owner",
        "dispatch_enabled": False,
        "cancel_enabled": False,
    }
    with pytest.raises(ValueError, match="INVALID_DIRECT_OPTIONS"):
        validate_service_options({"role": "trading_owner", "cancel_enabled": True}, ports)
    for value in (1, "true", None):
        with pytest.raises(ValueError, match="INVALID_DIRECT_OPTIONS"):
            validate_service_options(
                {"role": "trading_owner", "dispatch_enabled": True, "cancel_enabled": value}, ports
            )
    for dispatch, cancel in ((False, True), (True, 1), (True, "yes")):
        with pytest.raises(ServiceConfigError):
            _config(dispatch=dispatch, cancel=cancel)
    raw = _service_config("tcp://127.0.0.1:12345").to_public_dict()
    raw["components"]["gateway"]["settings"]["options"] = {
        "role": "market_hub",
        "cancel_enabled": True,
    }
    with pytest.raises(ServiceConfigError):
        parse_service_config(raw)


def test_component_advertises_cancel_only_with_configuration_and_exact_owner(tmp_path):
    h = Harness(tmp_path)
    owner = owner_for(with_cancel(h))
    try:
        assert not owner.supports_cancel
        assert owner.start(2) and owner.supports_cancel
        grants = {
            CLIENT_A.principal_ref: trading.GatewayDirectGrant(
                CLIENT_A.actor_ref, frozenset({"rb"})
            )
        }
        enabled = GatewayDirectServiceBinding(h.journal, grants, owner=owner, cancel_enabled=True)
        selected = _prepared(_config(cancel=True))
        component = _build(selected, enabled)
        capability = component.capabilities[0]
        assert capability.operations == SUPPORTED_OPERATIONS | CANCEL_OPERATIONS
        assert capability.write_operations == {
            "direct.reserve",
            "direct.release",
            "direct.dispatch",
            "direct.cancel",
        }
        assert set(component.handlers) >= CANCEL_OPERATIONS
        disabled = _build(_prepared(_config(cancel=False)), enabled)
        assert disabled.capabilities[0].operations == SUPPORTED_OPERATIONS
        assert set(disabled.handlers).isdisjoint(CANCEL_OPERATIONS)
        no_cancel = GatewayDirectServiceBinding(h.journal, grants, owner=owner)
        with pytest.raises(ServiceComponentError, match="CANCEL_OWNER_REQUIRED"):
            _build(selected, no_cancel)
    finally:
        assert owner.close(2)
    assert not owner.supports_cancel


def test_requested_operations_must_explicitly_opt_into_cancel():
    class CapturedError(Exception):
        pass

    binding = {
        "instance_id": "gateway-a",
        "port": "service.direct_write",
        "mount": "broker.direct_write",
        "scope_ref": "scope-a",
        "service_id": "svc",
        "transport_ref": "transport",
    }
    capabilities = [
        {
            "instance_id": "gateway-a",
            "port": "service.direct_write",
            "operations": sorted(SUPPORTED_OPERATIONS | CANCEL_OPERATIONS | EVENT_OPERATIONS),
        }
    ]
    assert PORT_OPERATIONS["service.direct_write"] >= CANCEL_OPERATIONS
    for wanted, expected in ((None, SUPPORTED_OPERATIONS), (CANCEL_OPERATIONS, CANCEL_OPERATIONS)):
        runtime = ServiceClientRuntime.__new__(ServiceClientRuntime)
        runtime.client_session_id = "session"
        runtime._requested_operations = {} if wanted is None else {"broker.direct_write": wanted}
        calls = []

        def capture(_service, _transport, _scope, _operation, payload, *, calls=calls, **_kwargs):
            calls.append(payload)
            raise CapturedError

        runtime._rpc = capture
        with pytest.raises(CapturedError):
            runtime._register_port(
                binding,
                WireScope(DOMAIN),
                "service.direct_write",
                capabilities,
                timeout=1,
                required=True,
                heartbeat_interval_seconds=1,
            )
        assert frozenset(calls[0]["operations"]) == expected


def _cancel_profile(tmp_path, monkeypatch, *, enabled=True, admission=True):
    _, settings, config, authority, grants, _ = make(tmp_path, monkeypatch, write_enabled=admission)
    raw = config.to_public_dict()
    raw["components"]["gateway"]["settings"]["options"]["cancel_enabled"] = enabled
    config = parse_service_config(raw)
    principal = authority.grants[CLIENT_A.principal_ref][0]
    resource = principal.resources[0]
    authority = replace(
        authority,
        grants={
            CLIENT_A.principal_ref: (
                PrincipalGrant(
                    CLIENT_A,
                    (replace(resource, operations=resource.operations | CANCEL_OPERATIONS),),
                ),
            )
        },
    )
    return settings, config, authority, grants


@pytest.mark.parametrize("admission", [True, False])
def test_ctp_cancel_gate_independent_of_place_and_fails_closed(tmp_path, monkeypatch, admission):
    from bt_api_base.market_protocol import MarketInstrumentKey

    settings, config, authority, grants = _cancel_profile(
        tmp_path, monkeypatch, admission=admission
    )
    FakeSession.initial_status = SettlementStatus.UNKNOWN
    monkeypatch.setattr(FakeReturnMaintenance, "dispatch_ready", lambda _: False)
    seen = queue.Queue()
    source_ready = threading.Event()
    source_ready.set()
    pause = threading.Event()
    entered = threading.Event()
    resume = threading.Event()

    def ready(provider, *, expected_account_binding):
        assert threading.get_ident() == FakeSession.instances[0].owner
        assert expected_account_binding == "native-binding"
        if pause.is_set():
            entered.set()
            assert resume.wait(3)
        if not source_ready.is_set():
            raise ValueError("synthetic untrusted source")

    monkeypatch.setattr(FakeProvider, "assert_cancel_ready", ready, raising=False)
    original_compose = trading.compose_gateway_direct_cancel_dispatcher

    def capture(*args, **kwargs):
        dispatcher = original_compose(*args, **kwargs)
        gate = dispatcher._admission

        def dispatch(*_args, request_fence, **_kwargs):
            with request_fence(None):
                place_allowed = dispatcher._owner._admission(None)
                try:
                    seen.put((gate(None), place_allowed, threading.get_ident()))
                except Exception:
                    seen.put((False, place_allowed, threading.get_ident()))

        dispatcher.dispatch = dispatch
        return dispatcher

    monkeypatch.setattr(trading, "compose_gateway_direct_cancel_dispatcher", capture)
    profile = trading.build_ctp_gateway_trading_profile(
        config,
        authority,
        tmp_path / "registry.sqlite",
        profile_ref="cancel-test",
        settings=settings,
        grants=grants,
        request_feed_factory=lambda: None,
        market_stream_factory=lambda *_: None,
        assert_admitted=lambda _: False,
        return_settings=trading.CtpGatewayReturnSettings(
            (MarketInstrumentKey("ctp", "SHFE", "simnow", "future", "rb2612", "rb2612"),)
        ),
    )
    resources = profile.deployment_factory()._provide_resources(
        "gateway",
        "gateway-a",
        {"role": "trading_owner", "dispatch_enabled": True, "cancel_enabled": True},
        authority.scope,
    )
    owner = resources.values["binding"]._owner
    done = queue.Queue()
    try:
        assert owner.supports_cancel
        for index in range(2):
            if index:
                source_ready.clear()
            assert (
                owner.enqueue_cancel(
                    GatewayCancelCommand(
                        CLIENT_A.actor_ref, f"op-{index}", CLIENT_A.principal_ref, 1
                    ),
                    request_fence=fence,
                    finalize=lambda: done.put(True),
                )
                == "QUEUED"
            )
            result, place_allowed, thread = seen.get(timeout=3)
            assert result is (index == 0 and admission)
            assert place_allowed is False
            assert thread == FakeSession.instances[0].owner
            assert done.get(timeout=3)
        if admission:
            source_ready.set()
            pause.set()
            assert (
                owner.enqueue_cancel(
                    GatewayCancelCommand(CLIENT_A.actor_ref, "op-close", CLIENT_A.principal_ref, 1),
                    request_fence=fence,
                    finalize=lambda: done.put(True),
                )
                == "QUEUED"
            )
            assert entered.wait(2)
            with pytest.raises(
                trading.CtpGatewayTradingError, match="TRADING_OWNER_CLOSE_UNCONFIRMED"
            ):
                resources.close()
            resume.set()
            result, place_allowed, _thread = seen.get(timeout=3)
            assert result is False and place_allowed is False
            assert done.get(timeout=3)
    finally:
        resume.set()
        resources.close()
    assert not owner.supports_cancel


def test_ctp_disabled_config_rejects_cancel_static_grant(tmp_path, monkeypatch):
    settings, config, authority, grants = _cancel_profile(tmp_path, monkeypatch, enabled=False)
    settings = replace(settings, write_enabled=False)
    with pytest.raises(trading.CtpGatewayTradingError, match="INVALID_TRADING_CONFIGURATION"):
        trading.build_ctp_gateway_trading_profile(
            config,
            authority,
            tmp_path / "registry.sqlite",
            profile_ref="cancel-test",
            settings=settings,
            grants=grants,
            request_feed_factory=lambda: None,
            market_stream_factory=lambda *_: None,
            assert_admitted=lambda _: True,
        )
