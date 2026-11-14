"""Fail-closed settlement preparation for the legacy CTP composition.

The trader below is synthetic; these tests do not issue CTP requests or attest
settlement source, account identity, or a durable pre-dispatch marker.
"""

from decimal import Decimal
from types import SimpleNamespace

import pytest

from bt_api_py.ctp_gateway_execution import (
    CtpGatewayExecutionError,
    CtpGatewayExecutionSettings,
    build_ctp_gateway_execution,
)
from bt_api_py.write_admission import WriteAdmission


_ROUTE = "CTP___FUTURE"


def _settings(tmp_path):
    return CtpGatewayExecutionSettings(
        symbol="rb2610",
        exchange_id="SHFE",
        strategy_ref="synthetic-strategy",
        trading_day="20260926",
        store_directory=tmp_path,
        td_front="tcp://synthetic:1",
        md_front="tcp://synthetic:2",
        broker_id="synthetic-broker",
        user_id="fake-user",
        password="synthetic-password",
        max_increase_notional=Decimal("100000"),
        max_increase_count=10,
    )


class _Trader:
    def __init__(self, *, complete=True, ready=True, readback_error=None):
        self.complete, self.is_trading_ready = complete, ready
        self.readback_error = readback_error
        self.calls = []
        self.admitted = False

    def configure_execution_gate(self, capability):
        self.calls.append("configure")

    def record_sdk_write_admission(self):
        self.calls.append("admit")
        self.admitted = True

    def clear_sdk_write_admission(self):
        self.calls.append("clear")
        self.admitted = False

    def get_session_state(self):
        return {"connection_generation": 1}

    def verify_settlement_confirmation(self, *, timeout):
        self.calls.append("readback")
        assert timeout == 15.0
        if self.readback_error is not None:
            raise self.readback_error
        return SimpleNamespace(complete=self.complete)

    def confirm_settlement_for_sdk_admission(self, *_args, **_kwargs):
        self.calls.append("confirm")
        raise AssertionError("legacy composition must not confirm without a marker")

    def arm_execution_for_sdk_admission(self, **_kwargs):
        self.calls.append("arm")


@pytest.fixture
def capability(monkeypatch):
    from bt_api_ctp.ctp import client

    token = object()
    monkeypatch.setattr(client, "_issue_ctp_execution_authority_for_core", lambda: token)
    return token


@pytest.mark.parametrize("complete,ready", [(False, False), (False, True), (True, False)])
def test_unproven_settlement_refuses_without_grant_or_native_confirmation(
    tmp_path, monkeypatch, capability, complete, ready
):
    admission = WriteAdmission({_ROUTE: True})
    monkeypatch.setattr(admission, "issue", lambda *_a, **_kw: pytest.fail("unexpected grant"))
    trader = _Trader(complete=complete, ready=ready)
    composition = build_ctp_gateway_execution(_settings(tmp_path), write_admission=admission)

    with pytest.raises(CtpGatewayExecutionError) as refused:
        composition.config.prepare_trader(trader)

    assert refused.value.code == "GATEWAY_EXECUTION_SETTLEMENT_UNCONFIRMED"
    assert trader.calls == ["configure", "admit", "readback", "clear"]
    assert trader.admitted is False


def test_readback_exception_clears_admission_without_confirmation(tmp_path, capability):
    trader = _Trader(readback_error=RuntimeError("synthetic readback failure"))
    composition = build_ctp_gateway_execution(
        _settings(tmp_path), write_admission=WriteAdmission({_ROUTE: True})
    )

    with pytest.raises(RuntimeError, match="synthetic readback failure"):
        composition.config.prepare_trader(trader)

    assert trader.calls == ["configure", "admit", "readback", "clear"]
    assert trader.admitted is False


def test_confirmed_readback_arms_without_settlement_write(tmp_path, monkeypatch, capability):
    admission = WriteAdmission({_ROUTE: True})
    monkeypatch.setattr(admission, "issue", lambda *_a, **_kw: pytest.fail("unexpected grant"))
    trader = _Trader()
    composition = build_ctp_gateway_execution(_settings(tmp_path), write_admission=admission)

    composition.config.prepare_trader(trader)

    assert trader.calls == ["configure", "admit", "readback", "arm"]
    assert trader.admitted is True


def test_arm_exception_clears_admission_without_touching_other_resources(tmp_path, monkeypatch, capability):
    trader = _Trader()

    def fail_arm(**_kwargs):
        trader.calls.append("arm")
        raise RuntimeError("synthetic arm failure")

    monkeypatch.setattr(trader, "arm_execution_for_sdk_admission", fail_arm)
    composition = build_ctp_gateway_execution(
        _settings(tmp_path), write_admission=WriteAdmission({_ROUTE: True})
    )

    with pytest.raises(RuntimeError, match="synthetic arm failure"):
        composition.config.prepare_trader(trader)

    assert trader.calls == ["configure", "admit", "readback", "arm", "clear"]
    assert trader.admitted is False


def test_disabled_admission_refuses_before_gate_or_readback(tmp_path, capability):
    trader = _Trader()
    composition = build_ctp_gateway_execution(
        _settings(tmp_path), write_admission=WriteAdmission({_ROUTE: False})
    )

    with pytest.raises(CtpGatewayExecutionError) as refused:
        composition.config.prepare_trader(trader)

    assert refused.value.code == "GATEWAY_EXECUTION_WRITE_ADMISSION_DISABLED"
    assert trader.calls == []
