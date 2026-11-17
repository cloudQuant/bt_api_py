"""Finite startup projection uses exact local owner types, with no raw errors."""

import logging

import pytest

from bt_api_ctp.account_provider import CtpReadOnlyAccountSessionProvider
from bt_api_ctp.gateway_session import CtpGatewaySession
from bt_api_py import ctp_gateway_trading as trading
from bt_api_py.service_components import ServiceComponentError
from tests.test_iter63_ctp_gateway_trading import FakeProvider, FakeSession, make
from tests.unit.test_ctp_gateway_startup_diagnostics import _build


def test_projection_rejects_untrusted_type_and_unbounded_value():
    class Lookalike:
        _state = "failed"
        _reader = object.__new__(CtpReadOnlyAccountSessionProvider)
        _gateway_connect_failure_detail = "NATIVE_START_FAILED"

    assert trading._ctp_connect_failure_detail(Lookalike()) == "UNKNOWN"
    session = object.__new__(CtpGatewaySession)
    session._state = "failed"
    session._reader = object.__new__(CtpReadOnlyAccountSessionProvider)
    session._gateway_connect_failure_detail = "secret /private/path"
    assert trading._ctp_connect_failure_detail(session) == "UNKNOWN"
    session._gateway_connect_failure_detail = "NATIVE_WAIT_FAILED"
    assert trading._ctp_connect_failure_detail(session) == "NATIVE_WAIT_FAILED"
    session._state = "starting"
    assert trading._ctp_connect_failure_detail(session) == "UNKNOWN"


def test_exact_gateway_owner_logs_observed_detail_and_preserves_category(tmp_path, monkeypatch, caplog):
    profile, _, config, authority, _, _ = make(tmp_path, monkeypatch)
    monkeypatch.setattr(trading, "_load_ctp_types", lambda: (CtpGatewaySession, FakeProvider))
    retained = []
    caplog.set_level(logging.WARNING, logger=trading.__name__)
    try:
        with pytest.raises(ServiceComponentError, match="^CTP_SESSION_CONNECT_FAILED$"):
            _build(profile, config, authority, retained)
        assert "CTP_SESSION_CONNECT_FAILED detail=FEED_PREFLIGHT_FAILED" in caplog.text
        assert len(retained) == 1 and retained[0].ready() is False
    finally:
        for resources in retained:
            resources.close()


def test_fake_session_cannot_supply_a_detail_through_exception(tmp_path, monkeypatch, caplog):
    from bt_api_ctp.account_provider import CtpAccountSessionProviderError

    profile, _, config, authority, _, _ = make(tmp_path, monkeypatch)

    class Fake(FakeSession):
        def connect(self, **_kwargs):
            self._gateway_connect_failure_detail = "NATIVE_START_FAILED"
            raise CtpAccountSessionProviderError("SESSION_NOT_CONNECTED")

    monkeypatch.setattr(trading, "_load_ctp_types", lambda: (Fake, FakeProvider))
    retained = []
    caplog.set_level(logging.WARNING, logger=trading.__name__)
    try:
        with pytest.raises(ServiceComponentError, match="^CTP_SESSION_CONNECT_FAILED$"):
            _build(profile, config, authority, retained)
        assert "CTP_SESSION_CONNECT_FAILED detail=UNKNOWN" in caplog.text
    finally:
        for resources in retained:
            resources.close()


def test_logging_failure_does_not_change_failure_or_cleanup(tmp_path, monkeypatch):
    from bt_api_ctp.account_provider import CtpAccountSessionProviderError

    profile, _, config, authority, _, _ = make(tmp_path, monkeypatch)

    class Fake(FakeSession):
        def connect(self, **_kwargs):
            raise CtpAccountSessionProviderError("SESSION_NOT_CONNECTED")

    def broken_log(*_args, **_kwargs):
        raise RuntimeError("secret /private/path")

    monkeypatch.setattr(trading, "_load_ctp_types", lambda: (Fake, FakeProvider))
    monkeypatch.setattr(trading._LOG, "warning", broken_log)
    retained = []
    try:
        with pytest.raises(ServiceComponentError, match="^CTP_SESSION_CONNECT_FAILED$"):
            _build(profile, config, authority, retained)
        assert len(retained) == 1 and retained[0].ready() is False
    finally:
        for resources in retained:
            resources.close()
    assert any(name == "close" for name, _ in Fake.instances[0].events)
