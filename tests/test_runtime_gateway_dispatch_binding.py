"""The managed server factory must bind the router's real write gates."""

from __future__ import annotations

import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

from bt_api_py.runtime_plugins import gateway_dispatch
from bt_api_py.runtime_plugins.contracts import (
    CAPABILITY_EXECUTION,
    CAPABILITY_GATEWAY,
    CAPABILITY_MONITOR,
    CAPABILITY_RISK,
    CAPABILITY_TRANSPORT_ZMQ,
    RuntimeCapabilityContract,
)


class _Scope:
    def __init__(
        self, *, provider, environment, account_ref, strategy_id, trading_day=None
    ):
        self.provider = provider
        self.environment = environment
        self.account_ref = account_ref
        self.strategy_id = strategy_id
        self.trading_day = trading_day
        self.key = "scope:" + strategy_id
        self.account_key = "account:" + account_ref


class _Closable:
    def close(self):
        return None


def _contract():
    return RuntimeCapabilityContract(
        strategy_id="strategy.gateway.binding-test",
        mode="live",
        preset="managed_live_gateway",
        environment="production",
        order_route="managed_execution",
        required_capabilities=(
            CAPABILITY_EXECUTION,
            CAPABILITY_RISK,
            CAPABILITY_MONITOR,
            CAPABILITY_GATEWAY,
            CAPABILITY_TRANSPORT_ZMQ,
        ),
        effective_digest="a" * 64,
    )


def _capabilities(monkeypatch, contract):
    import bt_api_gateway

    execution = SimpleNamespace(
        ExecutionScope=_Scope,
        SqliteExecutionStore=lambda _path: _Closable(),
        ManagedExecutionFacade=lambda *_args, **_kwargs: object(),
    )

    class _RiskGate(_Closable):
        def __init__(self, *_args, **_kwargs):
            pass

    risk = SimpleNamespace(
        AccountScope=lambda **values: SimpleNamespace(**values),
        RiskPolicy=lambda **values: SimpleNamespace(**values),
        DurableRiskGate=_RiskGate,
    )
    monitor = SimpleNamespace(
        DurableOutbox=lambda _path: _Closable(),
        OutboxEvent=object,
    )
    transport = SimpleNamespace()
    modules = {
        CAPABILITY_EXECUTION: execution,
        CAPABILITY_RISK: risk,
        CAPABILITY_MONITOR: monitor,
        CAPABILITY_GATEWAY: bt_api_gateway,
        CAPABILITY_TRANSPORT_ZMQ: transport,
    }

    class _Capabilities:
        def __init__(self):
            self.contract = contract

        def require(self, name):
            return modules[name]

    monkeypatch.setattr(
        gateway_dispatch,
        "_require_gateway_snapshot",
        lambda _snapshot: SimpleNamespace(
            require_execution_scope=lambda _scope: None,
            require_live_metadata=lambda: None,
        ),
    )
    monkeypatch.setattr(
        gateway_dispatch,
        "compose_instrument_risk_admission",
        lambda *_args, **_kwargs: SimpleNamespace(admission_gate=lambda *_: True),
    )
    monkeypatch.setattr(
        gateway_dispatch,
        "DurableManagedRecoveryCoordinator",
        lambda _path: _Closable(),
    )
    monkeypatch.setattr(
        gateway_dispatch,
        "ManagedExecutionRuntime",
        lambda **values: SimpleNamespace(**values, close=lambda: None),
    )
    return _Capabilities()


def _compose(monkeypatch, tmp_path, *, server_admission=None, writer_authority=None):
    contract = _contract()
    capabilities = _capabilities(monkeypatch, contract)
    return gateway_dispatch.compose_gateway_execution_authority(
        capabilities,
        state_directory=tmp_path,
        provider="fixture",
        environment="production",
        account_ref="account.gateway",
        strategy_id=contract.strategy_id,
        writer_id="writer.gateway",
        policy_id="policy.gateway",
        max_increase_notional=1_000,
        max_increase_count=10,
        provider_dispatch=lambda _intent: {},
        server_admission=server_admission,
        writer_authority=writer_authority,
    )


def test_gateway_factory_passes_server_gates_to_the_actual_router(
    monkeypatch, tmp_path
):
    def server_admission(_principal, _command):
        return True

    writer_authority = SimpleNamespace(
        database_path=(tmp_path / "gateway_router.sqlite3").resolve()
    )

    authority = _compose(
        monkeypatch,
        tmp_path,
        server_admission=server_admission,
        writer_authority=writer_authority,
    )

    assert authority.router._admission is server_admission
    assert authority.router._writer_authority is writer_authority
    assert "_ctp" not in sys.modules
    authority.close()


def test_gateway_factory_leaves_missing_gates_unavailable(monkeypatch, tmp_path):
    authority = _compose(monkeypatch, tmp_path)

    assert authority.router._admission is None
    assert authority.router._writer_authority is None
    authority.close()


def test_gateway_factory_rejects_a_wrong_admission_gate(monkeypatch, tmp_path):
    with pytest.raises(TypeError, match="admission must be callable"):
        _compose(monkeypatch, tmp_path, server_admission=object())


def test_gateway_factory_rejects_writer_authority_for_another_database(
    monkeypatch, tmp_path
):
    wrong_writer = SimpleNamespace(
        database_path=Path(tmp_path / "other.sqlite3").resolve()
    )
    with pytest.raises(ValueError, match="same SQLite database"):
        _compose(
            monkeypatch,
            tmp_path,
            server_admission=lambda *_: True,
            writer_authority=wrong_writer,
        )
