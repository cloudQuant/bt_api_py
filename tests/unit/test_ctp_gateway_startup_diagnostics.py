"""Finite CTP startup categories with synthetic sessions and real owner cleanup."""
from dataclasses import replace
import threading

import pytest

from bt_api_ctp.account_provider import CtpAccountSessionProviderError
from bt_api_ctp.gateway_session import CtpGatewaySessionError
from bt_api_py import ctp_gateway_trading as trading
from bt_api_py.gateway_operator_application import GatewayOperatorApplication, GatewayOperatorApplicationError
from bt_api_py.service_components import (ServiceComponentError, build_component, component_specs,
                                           validate_components)
from tests.test_iter63_ctp_gateway_trading import FakeProvider, FakeSession, make

_SECRET = 'secret-token /private/path native-payload'


def _build(profile, config, authority, retained):
    deployment = profile.deployment_factory()
    prepared = validate_components(component_specs(config.components))[0]
    return build_component(prepared, scope=authority.scope, resource_provider=deployment._provide_resources,
                           on_resources=retained.append)


@pytest.mark.parametrize('stage,error,expected', [
    ('construct', CtpGatewaySessionError('INVALID_CONFIGURATION'), 'CTP_SESSION_CONSTRUCT_FAILED'),
    ('connect', CtpAccountSessionProviderError('SESSION_NOT_CONNECTED'), 'CTP_SESSION_CONNECT_FAILED'),
    ('connect', TimeoutError(_SECRET), 'CTP_SESSION_CONNECT_TIMEOUT'),
    ('connect', ValueError(_SECRET), 'RESOURCE_RESOLUTION_FAILED'),
    ('connect', CtpGatewaySessionError(_SECRET), 'RESOURCE_RESOLUTION_FAILED'),
    ('construct', ValueError(_SECRET), 'RESOURCE_RESOLUTION_FAILED'),
])
def test_trusted_finite_session_failures_surface_without_values(tmp_path, monkeypatch, stage, error, expected):
    profile, _, config, authority, _, _ = make(tmp_path, monkeypatch)
    class Session(FakeSession):
        def __init__(self, *args, **kwargs):
            if stage == 'construct':
                raise error
            super().__init__(*args, **kwargs)
        def connect(self, **kwargs):
            raise error
    monkeypatch.setattr(trading, '_load_ctp_types', lambda: (Session, FakeProvider))
    retained = []
    with pytest.raises(ServiceComponentError) as caught:
        _build(profile, config, authority, retained)
    try:
        assert caught.value.code == expected
        assert str(caught.value) == expected
        assert _SECRET not in str(caught.value)
        assert len(retained) == 1 and retained[0].ready() is False
    finally:
        for resources in retained:
            resources.close()
    if stage == 'connect':
        assert any(name == 'close' for name, _ in Session.instances[0].events)


def test_application_observes_finite_connect_failure(tmp_path, monkeypatch):
    profile, _, _, _, _, _ = make(tmp_path, monkeypatch)
    class Session(FakeSession):
        def connect(self, **kwargs):
            raise CtpAccountSessionProviderError('SESSION_NOT_CONNECTED')
    monkeypatch.setattr(trading, '_load_ctp_types', lambda: (Session, FakeProvider))
    app = GatewayOperatorApplication(profile)
    try:
        with pytest.raises(GatewayOperatorApplicationError, match='^CTP_SESSION_CONNECT_FAILED$'):
            app.start(timeout_seconds=5)
        assert app.status['error_code'] == 'CTP_SESSION_CONNECT_FAILED'
        assert app.status['ready'] is False
        assert _SECRET not in str(app.status)
    finally:
        app.stop(timeout_seconds=5)


def test_owner_start_timeout_does_not_claim_connect_failure_or_discard_cleanup(tmp_path, monkeypatch):
    _, settings, config, authority, grants, _ = make(tmp_path, monkeypatch)
    settings = replace(settings, startup_timeout_seconds=0.05, close_timeout_seconds=0.1)
    entered, release = threading.Event(), threading.Event()
    class Session(FakeSession):
        def connect(self, **kwargs):
            entered.set()
            assert release.wait(5)
            raise CtpAccountSessionProviderError('SESSION_NOT_CONNECTED')
    monkeypatch.setattr(trading, '_load_ctp_types', lambda: (Session, FakeProvider))
    profile = trading.build_ctp_gateway_trading_profile(config, authority, tmp_path / 'registry.sqlite',
        profile_ref='ctp-test', settings=settings, grants=grants, request_feed_factory=lambda: None,
        market_stream_factory=lambda *_: None, assert_admitted=lambda _: True)
    retained = []
    try:
        with pytest.raises(ServiceComponentError, match='^RESOURCE_RESOLUTION_FAILED$'):
            _build(profile, config, authority, retained)
        assert entered.is_set() and len(retained) == 1 and retained[0].ready() is False
        with pytest.raises(trading.CtpGatewayTradingError, match='TRADING_OWNER_CLOSE_UNCONFIRMED'):
            retained[0].close()
    finally:
        release.set()
        for resources in retained:
            resources.close()
    assert any(name == 'close' for name, _ in Session.instances[0].events)
