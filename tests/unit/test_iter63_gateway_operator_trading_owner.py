"""Operator composition and cleanup for the direct trading owner."""

from __future__ import annotations

import pytest
import zmq
from bt_api_base.service_protocol import WireScope
from bt_api_gateway.direct_command_journal import GatewayDirectCommandJournal

from bt_api_py.gateway_direct_service import GatewayDirectGrant, GatewayDirectServiceBinding
from bt_api_py.gateway_operator import GatewayOperatorDeployment, GatewayOperatorDeploymentError
from bt_api_py.gateway_operator_application import (
    GatewayOperatorApplication,
    GatewayOperatorApplicationError,
    GatewayOperatorProfile,
    GatewayOperatorProfileRegistry,
)
from bt_api_py.service_components import ComponentResources, ServiceResourceConstructionError
from bt_api_py.service_config import parse_service_config
from bt_api_py.service_host import ServiceHostError, TrustedServiceAuthority
from bt_api_py.service_registry import PrincipalGrant, ResourceGrant
from tests.integration.test_iter63_gateway_direct_service import (
    ACCOUNT,
    CLIENT_A,
    DOMAIN,
    OPS,
    SERVICE,
    _service_config,
)
from tests.integration.test_service_account_instrument_metadata import _free_endpoint


def _authority(server_public, server_secret, client_public):
    grant = ResourceGrant(
        "gateway-a",
        "service.direct_write",
        "scope-a",
        WireScope(DOMAIN, account_ref=ACCOUNT, strategy_ref=CLIENT_A.actor_ref),
        OPS,
    )
    return TrustedServiceAuthority(
        identity=SERVICE,
        scope=WireScope(DOMAIN),
        scope_ref="scope-a",
        scope_version="scope-v1",
        security_ref="security-a",
        security_version="security-v1",
        registration_policy_ref="direct-policy-v1",
        registration_policy_version="direct-policy-v1",
        service_requirements_ref="direct-requirements-v1",
        service_requirements_version="direct-requirements-v1",
        curve_public_key=server_public,
        curve_secret_key=server_secret,
        principals={client_public: CLIENT_A.principal_ref},
        grants={CLIENT_A.principal_ref: (PrincipalGrant(CLIENT_A, (grant,)),)},
    )


def _setup():
    server_public, server_secret = zmq.curve_keypair()
    client_public, client_secret = zmq.curve_keypair()
    endpoint = _free_endpoint()
    return (
        endpoint,
        _authority(server_public, server_secret, client_public),
        (server_public, client_public, client_secret),
    )


def _binding(tmp_path):
    journal = GatewayDirectCommandJournal.create(
        tmp_path / "journal.sqlite", ACCOUNT, generation="g1"
    )
    return GatewayDirectServiceBinding(
        journal, {CLIENT_A.principal_ref: GatewayDirectGrant("strategy-a", frozenset({"rb"}))}
    )


def test_trading_owner_is_a_registered_profile_role():
    profile = GatewayOperatorProfile("trading-profile", "trading_owner", lambda: None)
    assert GatewayOperatorProfileRegistry((profile,)).resolve("trading-profile") is profile


@pytest.mark.parametrize(
    "keyword,code",
    [
        ("session_owner", "UNUSED_ACCOUNT_SESSION_BINDING"),
        ("account_session", "UNUSED_ACCOUNT_SESSION_BINDING"),
        ("market_binding", "UNUSED_MARKET_BINDING"),
    ],
)
def test_inapplicable_injections_fail_before_resource_provider(tmp_path, keyword, code):
    endpoint, authority, _ = _setup()
    calls = []
    with pytest.raises(GatewayOperatorDeploymentError, match=code):
        GatewayOperatorDeployment(
            _service_config(endpoint),
            authority,
            tmp_path / "registry.sqlite",
            resource_provider=lambda *args: calls.append(args),
            **{keyword: object()},
        )
    assert calls == []


@pytest.mark.parametrize("failure", ["false", "exception"])
def test_owned_close_failure_retained_and_retried(tmp_path, failure):
    endpoint, authority, _ = _setup()
    attempts = []
    resources = []

    def owner_close():
        attempts.append(1)
        if len(attempts) == 1:
            if failure == "exception":
                raise RuntimeError("synthetic cleanup error")
            return False
        return True

    def close():
        if owner_close() is not True:
            raise RuntimeError("owner close unconfirmed")

    def provider(*args):
        resource = ComponentResources.owned(
            {"binding": _binding(tmp_path)}, ready=lambda: True, close=close
        )
        resources.append(resource)
        return resource

    deployment = GatewayOperatorDeployment(
        _service_config(endpoint),
        authority,
        tmp_path / "registry.sqlite",
        resource_provider=provider,
    )
    deployment.start()
    with pytest.raises(ServiceHostError):
        deployment.close()
    assert deployment.state == "close_failed"
    deployment.close()
    assert deployment.state == "closed"
    assert len(attempts) == 2
    resources[0].close()
    assert len(attempts) == 2


@pytest.mark.parametrize("first_close_fails", [False, True])
def test_partial_construction_resources_are_tracked(tmp_path, first_close_fails):
    endpoint, authority, _ = _setup()
    attempts = []

    def close():
        attempts.append(1)
        if first_close_fails and len(attempts) == 1:
            raise RuntimeError("owner close unconfirmed")

    resource = ComponentResources.owned({}, ready=lambda: False, close=close)

    def provider(*args):
        raise ServiceResourceConstructionError(resource)

    deployment = GatewayOperatorDeployment(
        _service_config(endpoint),
        authority,
        tmp_path / "registry.sqlite",
        resource_provider=provider,
    )
    with pytest.raises(ServiceHostError):
        deployment.start()
    assert deployment.state == ("close_failed" if first_close_fails else "failed")
    if first_close_fails:
        deployment.close()
        assert len(attempts) == 2
    else:
        assert len(attempts) == 1
    resource.close()
    assert len(attempts) == (2 if first_close_fails else 1)


def test_partial_construction_exception_keeps_exact_resource(tmp_path):
    endpoint, authority, _ = _setup()
    resource = ComponentResources.owned({}, ready=lambda: False, close=lambda: None)

    def provider(*args):
        raise ServiceResourceConstructionError(resource)

    deployment = GatewayOperatorDeployment(
        _service_config(endpoint),
        authority,
        tmp_path / "registry.sqlite",
        resource_provider=provider,
    )
    with pytest.raises(ServiceResourceConstructionError) as error:
        deployment._provide_resources(
            "gateway", "gateway-a", {"role": "trading_owner"}, WireScope(DOMAIN)
        )
    assert error.value.resources is resource


@pytest.mark.parametrize(
    "profile_role,candidate_role",
    [("trading_owner", "market_hub"), ("market_hub", "trading_owner")],
)
def test_profile_role_mismatch_rejected_before_start(profile_role, candidate_role):
    class WrongRole(GatewayOperatorDeployment):
        def __init__(self):
            self._gateway_role = candidate_role
            self.started = False

        @property
        def state(self):
            return "new"

        def start(self):
            self.started = True

    candidate = WrongRole()
    app = GatewayOperatorApplication(GatewayOperatorProfile("p", profile_role, lambda: candidate))
    with pytest.raises(GatewayOperatorApplicationError, match="APPLICATION_PROFILE_ROLE_MISMATCH"):
        app.start(timeout_seconds=5)
    assert candidate.started is False


@pytest.mark.parametrize("health", [None, "error"])
def test_unavailable_health_is_never_ready(health):
    class UnavailableHealth(GatewayOperatorDeployment):
        @property
        def state(self):
            return "started"

        @property
        def healthy(self):
            if health == "error":
                raise RuntimeError("synthetic health error")
            return health

        @property
        def cleanup_status(self):
            return {"unclaimed_provider_count": 0, "last_cleanup_code": None}

    app = GatewayOperatorApplication(GatewayOperatorProfile("p", "trading_owner", lambda: None))
    app._deployment = object.__new__(UnavailableHealth)
    app._refresh_deployment_status(state="running")
    assert app.status["ready"] is False
    assert app.status["error_code"] == "DEPLOYMENT_STATUS_UNAVAILABLE"


@pytest.mark.parametrize("dispatch_enabled", [False, True])
def test_invalid_binding_or_missing_dispatch_owner_fails_and_closes(tmp_path, dispatch_enabled):
    endpoint, authority, _ = _setup()
    raw = _service_config(endpoint).to_public_dict()
    raw["components"]["gateway"]["settings"]["options"]["dispatch_enabled"] = dispatch_enabled
    config = parse_service_config(raw)
    closed = []
    binding = _binding(tmp_path) if dispatch_enabled else object()

    def provider(*args):
        return ComponentResources.owned(
            {"binding": binding}, ready=lambda: True, close=lambda: closed.append(True)
        )

    deployment = GatewayOperatorDeployment(
        config, authority, tmp_path / "registry.sqlite", resource_provider=provider
    )
    with pytest.raises(ServiceHostError):
        deployment.start()
    assert deployment.state == "failed"
    assert closed == [True]
