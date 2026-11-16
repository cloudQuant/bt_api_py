"""The trusted CTP profile exposes only finite configuration errors."""

import pytest

import bt_api_py.ctp_gateway_operator_profile as operator
from bt_api_py.ctp_gateway_trading import CtpGatewayTradingError
from bt_api_py.gateway_operator_application import (
    GatewayOperatorApplication,
    GatewayOperatorApplicationError,
    GatewayOperatorProfile,
)
from tests.test_iter63_ctp_gateway_trading import FakeSession, make


def _application(factory, config):
    profile = GatewayOperatorProfile(
        "ctp-test", "trading_owner", lambda: factory.create_deployment(config)
    )
    return GatewayOperatorApplication(profile)


def test_real_invalid_ctp_configuration_reaches_public_start_without_provider(tmp_path, monkeypatch):
    _, settings, config, _, grants, _ = make(tmp_path, monkeypatch, with_journal=False)
    calls = []
    resources = operator.CtpGatewayTrustedResources(
        object(), tmp_path / "registry.sqlite", settings, grants,
        lambda: calls.append("feed"), lambda *_: calls.append("market"), lambda _: True,
    )
    factory = operator.create_ctp_gateway_profile_factory("ctp-test", resource_loader=lambda _: resources)
    app = _application(factory, config)
    try:
        with pytest.raises(GatewayOperatorApplicationError, match="^INVALID_TRADING_CONFIGURATION$"):
            app.start(timeout_seconds=5)
        assert app.status["error_code"] == "INVALID_TRADING_CONFIGURATION"
        assert app.status["ready"] is False
        assert FakeSession.instances == []
        assert calls == []
        assert not (tmp_path / "registry.sqlite").exists()
    finally:
        app.stop(timeout_seconds=5)

    # Existing direct factory callers can still catch their CTP error type.
    with pytest.raises(CtpGatewayTradingError, match="^INVALID_TRADING_CONFIGURATION$"):
        factory.create_deployment(config)


class _HostileError(Exception):
    code = "INVALID_TRADING_CONFIGURATION"


class _UntrustedCtpError(CtpGatewayTradingError):
    pass


@pytest.mark.parametrize("error", [
    _HostileError("private-token"),
    CtpGatewayTradingError("PRIVATE_TOKEN"),
    _UntrustedCtpError("INVALID_TRADING_CONFIGURATION"),
])
def test_unrecognized_errors_stay_opaque_at_public_start(tmp_path, monkeypatch, error):
    _, _, config, _, _, _ = make(tmp_path, monkeypatch, with_journal=False)

    def loader(_config):
        raise error

    factory = operator.create_ctp_gateway_profile_factory("ctp-test", resource_loader=loader)
    app = _application(factory, config)
    try:
        with pytest.raises(GatewayOperatorApplicationError, match="^APPLICATION_START_FAILED$"):
            app.start(timeout_seconds=5)
        assert app.status["error_code"] == "APPLICATION_START_FAILED"
        assert "private" not in repr(app.status).lower()
        assert FakeSession.instances == []
    finally:
        app.stop(timeout_seconds=5)
