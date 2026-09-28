"""Local-only acceptance for the Iteration 41 gateway managed dispatch adapter."""

from __future__ import annotations

import importlib
import threading
import uuid
from decimal import Decimal
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest
import zmq

from bt_api_py.runtime_plugins import (
    CAPABILITY_EXECUTION,
    CAPABILITY_GATEWAY,
    CAPABILITY_MONITOR,
    CAPABILITY_RISK,
    CAPABILITY_TRANSPORT_ZMQ,
    CapabilityCatalog,
    CapabilityPin,
    RuntimeCapabilityContract,
    RuntimePluginError,
    SealedNormalizedInstrumentMetadataSnapshot,
    compose_gateway_execution_authority,
    compose_gateway_managed_client,
)

_REPO_ROOT = Path(__file__).resolve().parents[2]
_CAPABILITY_SOURCES = (
    _REPO_ROOT / "bt_api" / "bt_api_base" / "src",
    _REPO_ROOT / "bt_api" / "bt_api_execution" / "src",
    _REPO_ROOT / "bt_api" / "bt_api_risk" / "src",
    _REPO_ROOT / "bt_api" / "bt_api_monitor" / "src",
    _REPO_ROOT / "bt_api" / "bt_api_gateway" / "src",
    _REPO_ROOT / "bt_api" / "bt_api_transport_zmq" / "src",
)


def _contract() -> RuntimeCapabilityContract:
    return RuntimeCapabilityContract(
        strategy_id="example.014_1.ctp_options_lowfreq",
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


def _catalog(monkeypatch: pytest.MonkeyPatch) -> CapabilityCatalog:
    for source in _CAPABILITY_SOURCES:
        monkeypatch.syspath_prepend(str(source))
    return CapabilityCatalog(
        (
            CapabilityPin(CAPABILITY_EXECUTION, "bt_api_execution", "bt_api_execution", "0.1.0"),
            CapabilityPin(CAPABILITY_RISK, "bt_api_risk", "bt_api_risk", "0.1.0"),
            CapabilityPin(CAPABILITY_MONITOR, "bt_api_monitor", "bt_api_monitor", "0.1.0"),
            CapabilityPin(CAPABILITY_GATEWAY, "bt_api_gateway", "bt_api_gateway", "0.1.0"),
            CapabilityPin(
                CAPABILITY_TRANSPORT_ZMQ,
                "bt_api_transport_zmq",
                "bt_api_transport_zmq",
                "0.1.0",
            ),
        ),
        importer=importlib.import_module,
        version_getter=lambda distribution: "0.1.0",
    )


def _snapshot() -> SealedNormalizedInstrumentMetadataSnapshot:
    return SealedNormalizedInstrumentMetadataSnapshot.from_normalized_payload(
        {
            "provider": "fixture_provider",
            "environment": "production",
            "account_ref": "fixture_account",
            "trading_day": "20260922",
            "metadata_version": "fixture.gateway.metadata.v1",
            "as_of_ns": 1_000,
            "expires_at_ns": 2_000,
            "account_currency": "USD",
            "instruments": [
                {
                    "instrument": "fixture/contract",
                    "tick_size": "0.1",
                    "lot_size": "1",
                    "contract_multiplier": "1",
                    "max_gross_notional_account": "1000",
                    "quote_currency": "USD",
                    "fee_currency": "USD",
                    "quote_to_account_fx": "1",
                    "fee_to_account_fx": "1",
                    "taker_fee_bps": "0",
                    "fixed_fee": "0",
                    "max_slippage_bps": "0",
                    "quantity_unit": "contracts",
                }
            ],
        }
    )


def _metadata_kwargs() -> dict[str, Any]:
    return {
        "trading_day": "20260922",
        "instrument_metadata_snapshot": _snapshot(),
        "instrument_clock_ns": lambda: 1_500,
    }


def _intent(runtime: Any, intent_id: str = "intent.gateway.1") -> Any:
    snapshot = runtime.instrument_metadata_snapshot
    return runtime.execution.OrderIntent.limit(
        intent_id=intent_id,
        scope=runtime.scope,
        signal_id="signal.gateway.1",
        instrument="fixture/contract",
        side=runtime.execution.Side.BUY,
        quantity=Decimal("1"),
        price=Decimal("10"),
        metadata_version=snapshot.metadata_version,
        tags={
            "instrument_metadata_digest": snapshot.instrument_digest("fixture/contract"),
            "quantity_unit": snapshot.instrument_metadata("fixture/contract").quantity_unit,
        },
    )


def _serve_one(server: Any) -> threading.Thread:
    worker = threading.Thread(target=lambda: server.serve_once(timeout_ms=2_000))
    worker.start()
    return worker


def _join(worker: threading.Thread) -> None:
    worker.join(timeout=5)
    assert not worker.is_alive()


def test_gateway_client_dispatches_only_through_authenticated_server_authority(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """The local client journal has no provider/risk route and never calls Store direct."""

    loaded = _catalog(monkeypatch).load(_contract())
    execution = loaded.require(CAPABILITY_EXECUTION)
    gateway = loaded.require(CAPABILITY_GATEWAY)
    transport = loaded.require(CAPABILITY_TRANSPORT_ZMQ)
    provider_calls: list[str] = []

    def provider_dispatch(intent: Any) -> Any:
        provider_calls.append(intent.intent_id)
        return execution.ProviderObservation.accepted(intent.intent_id, "provider.gateway.1")

    server_state = tmp_path / "server"
    server_state.mkdir(parents=True)
    writer_authority = gateway.GatewayAccountWriterAuthority(
        server_state / "gateway_router.sqlite3"
    )
    authority = compose_gateway_execution_authority(
        loaded,
        state_directory=server_state,
        provider="fixture_provider",
        environment="production",
        account_ref="fixture_account",
        strategy_id="example.014_1.ctp_options_lowfreq",
        writer_id="gateway_server_writer",
        policy_id="gateway_policy",
        max_increase_notional=Decimal("100"),
        max_increase_count=1,
        provider_dispatch=provider_dispatch,
        server_admission=lambda _principal, _command: True,
        writer_authority=writer_authority,
        **_metadata_kwargs(),
    )
    writer_authority.acquire_writer(
        authority.scope.account_key, "gateway_server_writer", lease_seconds=60
    )
    assert authority.server_runtime.recovery_coordinator is not None
    principal = gateway.GatewayPrincipal(
        principal_id="server-derived-fixture-principal",
        account_scopes=frozenset({authority.scope.account_key}),
        strategy_scopes=frozenset({authority.scope.key}),
        allowed_kinds=frozenset({gateway.GatewayCommandKind.SUBMIT}),
    )
    context = zmq.Context()
    endpoint = "inproc://iteration41-gateway-" + uuid.uuid4().hex
    server = authority.create_zmq_server(
        endpoint,
        lambda peer_identity, message: principal,
        context=context,
    )
    client = transport.ZmqCommandClient(endpoint, context=context)
    runtime = compose_gateway_managed_client(
        loaded,
        state_directory=tmp_path / "client",
        provider="fixture_provider",
        environment="production",
        account_ref="fixture_account",
        strategy_id="example.014_1.ctp_options_lowfreq",
        writer_id="gateway_client_writer",
        client=client,
        **_metadata_kwargs(),
    )
    direct_calls: list[Any] = []
    intent = _intent(runtime)
    command = runtime.dispatcher.command_for_intent(intent)
    assert not hasattr(runtime, "risk_gate")
    assert not hasattr(runtime, "provider_dispatch")
    worker = _serve_one(server)
    try:
        record = runtime.submit(
            intent,
            lambda order: (
                direct_calls.append(order)
                or (_ for _ in ()).throw(AssertionError("Store direct dispatch must never run"))
            ),
        )
        _join(worker)

        assert record.state is execution.ExecutionState.UNKNOWN
        assert record.review_required is True
        assert provider_calls == [intent.intent_id]
        assert direct_calls == []
        gateway_result = authority.router.get(command.command_id)
        assert gateway_result.status is gateway.GatewayCommandStatus.RETURNED_UNVERIFIED
        # The fixture provider says ACKED, but the gateway has no independent
        # terminal-query evidence and must keep the command unverified.
        assert gateway_result.outcome["state"] == "ACKED"
        assert runtime.dispatcher.command_for_intent(intent).fingerprint == command.fingerprint

        # A replay stays UNKNOWN and cannot automatically repeat the provider
        # side effect while terminal-query evidence is absent.
        repeated = runtime.submit(intent, lambda order: direct_calls.append(order))
        assert repeated.state is execution.ExecutionState.UNKNOWN
        assert repeated.review_required is True
        assert provider_calls == [intent.intent_id]
        assert direct_calls == []
    finally:
        runtime.close()
        client.close()
        server.close()
        authority.close()
        context.term()


def test_server_provider_timeout_latches_server_risk_and_client_unknown(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """A server-side uncertain provider effect cannot free account capacity."""

    loaded = _catalog(monkeypatch).load(_contract())
    gateway = loaded.require(CAPABILITY_GATEWAY)
    transport = loaded.require(CAPABILITY_TRANSPORT_ZMQ)
    provider_calls: list[str] = []

    def uncertain_provider(intent: Any) -> Any:
        provider_calls.append(intent.intent_id)
        raise TimeoutError("fixture provider outcome is unknown")

    server_state = tmp_path / "server"
    server_state.mkdir(parents=True)
    writer_authority = gateway.GatewayAccountWriterAuthority(
        server_state / "gateway_router.sqlite3"
    )
    authority = compose_gateway_execution_authority(
        loaded,
        state_directory=server_state,
        provider="fixture_provider",
        environment="production",
        account_ref="fixture_account",
        strategy_id="example.014_1.ctp_options_lowfreq",
        writer_id="gateway_server_writer",
        policy_id="gateway_policy",
        max_increase_notional=Decimal("100"),
        max_increase_count=1,
        provider_dispatch=uncertain_provider,
        server_admission=lambda _principal, _command: True,
        writer_authority=writer_authority,
        **_metadata_kwargs(),
    )
    writer_authority.acquire_writer(
        authority.scope.account_key, "gateway_server_writer", lease_seconds=60
    )
    principal = gateway.GatewayPrincipal(
        principal_id="server-derived-fixture-principal",
        account_scopes=frozenset({authority.scope.account_key}),
        strategy_scopes=frozenset({authority.scope.key}),
        allowed_kinds=frozenset({gateway.GatewayCommandKind.SUBMIT}),
    )
    context = zmq.Context()
    endpoint = "inproc://iteration41-server-timeout-" + uuid.uuid4().hex
    server = authority.create_zmq_server(
        endpoint,
        lambda peer_identity, message: principal,
        context=context,
    )
    client = transport.ZmqCommandClient(endpoint, context=context)
    runtime = compose_gateway_managed_client(
        loaded,
        state_directory=tmp_path / "client",
        provider="fixture_provider",
        environment="production",
        account_ref="fixture_account",
        strategy_id="example.014_1.ctp_options_lowfreq",
        writer_id="gateway_client_writer",
        client=client,
        **_metadata_kwargs(),
    )
    direct_calls: list[Any] = []
    intent = _intent(runtime, "intent.gateway.server-timeout")
    worker = _serve_one(server)
    try:
        record = runtime.submit(intent, lambda order: direct_calls.append(order))
        _join(worker)

        assert record.state is runtime.execution.ExecutionState.UNKNOWN
        assert record.review_required is True
        assert provider_calls == [intent.intent_id]
        assert direct_calls == []
        assert authority.server_runtime.risk_gate.active_freeze_reasons(
            authority.server_runtime.risk_scope
        ) == ["dispatch-inflight:" + intent.intent_id]
    finally:
        runtime.close()
        client.close()
        server.close()
        authority.close()
        context.term()


def test_missing_server_admission_rejects_write_before_provider_dispatch(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """A server authority without its own write admission stays fail-closed."""

    loaded = _catalog(monkeypatch).load(_contract())
    execution = loaded.require(CAPABILITY_EXECUTION)
    gateway = loaded.require(CAPABILITY_GATEWAY)
    transport = loaded.require(CAPABILITY_TRANSPORT_ZMQ)
    provider_calls: list[str] = []
    authority = compose_gateway_execution_authority(
        loaded,
        state_directory=tmp_path / "server",
        provider="fixture_provider",
        environment="production",
        account_ref="fixture_account",
        strategy_id="example.014_1.ctp_options_lowfreq",
        writer_id="gateway_server_writer",
        policy_id="gateway_policy",
        max_increase_notional=Decimal("100"),
        max_increase_count=1,
        provider_dispatch=lambda intent: (
            provider_calls.append(intent.intent_id)
            or execution.ProviderObservation.accepted(intent.intent_id, "provider.gateway.denied")
        ),
        **_metadata_kwargs(),
    )
    principal = gateway.GatewayPrincipal(
        principal_id="server-derived-fixture-principal",
        account_scopes=frozenset({authority.scope.account_key}),
        strategy_scopes=frozenset({authority.scope.key}),
        allowed_kinds=frozenset({gateway.GatewayCommandKind.SUBMIT}),
    )
    context = zmq.Context()
    endpoint = "inproc://iteration41-no-server-admission-" + uuid.uuid4().hex
    server = authority.create_zmq_server(
        endpoint,
        lambda _peer_identity, _message: principal,
        context=context,
    )
    client = transport.ZmqCommandClient(endpoint, context=context)
    runtime = compose_gateway_managed_client(
        loaded,
        state_directory=tmp_path / "client",
        provider="fixture_provider",
        environment="production",
        account_ref="fixture_account",
        strategy_id="example.014_1.ctp_options_lowfreq",
        writer_id="gateway_client_writer",
        client=client,
        **_metadata_kwargs(),
    )
    intent = _intent(runtime, "intent.gateway.no-server-admission")
    command = runtime.dispatcher.command_for_intent(intent)
    worker = _serve_one(server)
    try:
        record = runtime.submit(
            intent, lambda _order: pytest.fail("legacy dispatch must stay closed")
        )
        _join(worker)

        assert record.state is execution.ExecutionState.UNKNOWN
        assert provider_calls == []
        rejected = authority.router.get(command.command_id)
        assert rejected is not None
        assert rejected.status is gateway.GatewayCommandStatus.REJECTED
    finally:
        runtime.close()
        client.close()
        server.close()
        authority.close()
        context.term()


def test_gateway_rejects_client_claimed_principal_before_provider_execution(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """Authorization comes only from the server authenticator, never wire JSON."""

    loaded = _catalog(monkeypatch).load(_contract())
    execution = loaded.require(CAPABILITY_EXECUTION)
    gateway = loaded.require(CAPABILITY_GATEWAY)
    transport = loaded.require(CAPABILITY_TRANSPORT_ZMQ)
    provider_calls: list[str] = []
    authority = compose_gateway_execution_authority(
        loaded,
        state_directory=tmp_path / "server",
        provider="fixture_provider",
        environment="production",
        account_ref="fixture_account",
        strategy_id="example.014_1.ctp_options_lowfreq",
        writer_id="gateway_server_writer",
        policy_id="gateway_policy",
        max_increase_notional=Decimal("100"),
        max_increase_count=1,
        provider_dispatch=lambda intent: (
            provider_calls.append(intent.intent_id)
            or execution.ProviderObservation.accepted(intent.intent_id, "provider.gateway.1")
        ),
        **_metadata_kwargs(),
    )
    principal = gateway.GatewayPrincipal(
        principal_id="trusted-server-principal",
        account_scopes=frozenset({authority.scope.account_key}),
        strategy_scopes=frozenset({authority.scope.key}),
        allowed_kinds=frozenset({gateway.GatewayCommandKind.SUBMIT}),
    )
    context = zmq.Context()
    endpoint = "inproc://iteration41-principal-" + uuid.uuid4().hex
    server = authority.create_zmq_server(
        endpoint,
        lambda peer_identity, message: principal,
        context=context,
    )
    client = transport.ZmqCommandClient(endpoint, context=context)
    runtime = compose_gateway_managed_client(
        loaded,
        state_directory=tmp_path / "client",
        provider="fixture_provider",
        environment="production",
        account_ref="fixture_account",
        strategy_id="example.014_1.ctp_options_lowfreq",
        writer_id="gateway_client_writer",
        client=client,
        **_metadata_kwargs(),
    )
    intent = _intent(runtime)
    command = runtime.dispatcher.command_for_intent(intent)
    payload = gateway.gateway_command_to_wire_payload(command)
    payload["principal"] = "client-supplied-admin"
    message = transport.WireMessage(
        message_id=command.command_id,
        channel=transport.WireChannel.COMMAND,
        scope=command.strategy_scope,
        sequence=int(command.fingerprint[:16], 16),
        sent_at=1.0,
        payload=payload,
    )
    worker = _serve_one(server)
    try:
        response = client.request(message, timeout_ms=2_000)
        _join(worker)
        assert response.payload["accepted"] is False
        assert provider_calls == []
        assert authority.router.get(command.command_id) is None
    finally:
        runtime.close()
        client.close()
        server.close()
        authority.close()
        context.term()


def test_client_timeout_is_durable_unknown_and_never_uses_legacy_dispatch(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """A lost ZMQ outcome has one local UNKNOWN record and no resend/fallback."""

    loaded = _catalog(monkeypatch).load(_contract())
    transport = loaded.require(CAPABILITY_TRANSPORT_ZMQ)
    attempted_messages: list[Any] = []

    class TimeoutClient:
        def request(self, message: Any, timeout_ms: int) -> Any:
            assert timeout_ms == 5_000
            attempted_messages.append(message)
            raise transport.CommandOutcomeUnknown(message.message_id)

    runtime = compose_gateway_managed_client(
        loaded,
        state_directory=tmp_path / "client",
        provider="fixture_provider",
        environment="production",
        account_ref="fixture_account",
        strategy_id="example.014_1.ctp_options_lowfreq",
        writer_id="gateway_client_writer",
        client=TimeoutClient(),
        clock=lambda: 1_700_000_000.0,
        **_metadata_kwargs(),
    )
    direct_calls: list[Any] = []
    intent = _intent(runtime, "intent.gateway.timeout")
    command = runtime.dispatcher.command_for_intent(intent)
    try:
        first = runtime.submit(
            intent,
            lambda order: (
                direct_calls.append(order)
                or (_ for _ in ()).throw(AssertionError("legacy direct dispatch must not run"))
            ),
        )
        repeated = runtime.submit(intent, lambda order: direct_calls.append(order))

        assert first.state is runtime.execution.ExecutionState.UNKNOWN
        assert repeated.state is runtime.execution.ExecutionState.UNKNOWN
        assert first.review_required is True
        assert len(attempted_messages) == 1
        assert direct_calls == []
    finally:
        runtime.close()

    # The persisted command has the same command ID, fingerprint, and original
    # deadline after a client process restart.  It is not regenerated as a new
    # dispatch opportunity.
    reopened = compose_gateway_managed_client(
        loaded,
        state_directory=tmp_path / "client",
        provider="fixture_provider",
        environment="production",
        account_ref="fixture_account",
        strategy_id="example.014_1.ctp_options_lowfreq",
        writer_id="gateway_client_writer_reopened",
        client=TimeoutClient(),
        clock=lambda: 1_700_000_100.0,
        **_metadata_kwargs(),
    )
    try:
        restored = reopened.dispatcher.command_for_intent(intent)
        restored_record = reopened.execution_store.get(intent.intent_id, scope=reopened.scope)
        assert restored.command_id == command.command_id
        assert restored.fingerprint == command.fingerprint
        assert restored.issued_at == command.issued_at
        assert restored.expires_at == command.expires_at
        assert restored_record is not None
        assert restored_record.state is reopened.execution.ExecutionState.UNKNOWN
    finally:
        reopened.close()


def test_gateway_live_compositions_require_snapshot_before_loading_capabilities(
    tmp_path: Path,
) -> None:
    """Neither client nor authority can construct a live gateway path without metadata."""

    required: list[str] = []
    loaded = SimpleNamespace(
        contract=_contract(),
        require=lambda capability: required.append(capability) or object(),
    )
    common = {
        "state_directory": tmp_path,
        "provider": "fixture_provider",
        "environment": "production",
        "account_ref": "fixture_account",
        "strategy_id": "example.014_1.ctp_options_lowfreq",
        "writer_id": "gateway_writer",
    }

    with pytest.raises(RuntimePluginError) as client_error:
        compose_gateway_managed_client(loaded, client=object(), **common)
    assert client_error.value.code == "INSTRUMENT_SNAPSHOT_REQUIRED"
    assert required == []

    with pytest.raises(RuntimePluginError) as authority_error:
        compose_gateway_execution_authority(
            loaded,
            policy_id="gateway_policy",
            max_increase_notional=Decimal("100"),
            max_increase_count=1,
            provider_dispatch=lambda _intent: None,
            **common,
        )
    assert authority_error.value.code == "INSTRUMENT_SNAPSHOT_REQUIRED"
    assert required == []


def test_gateway_snapshot_day_and_intent_digest_mismatch_fail_before_transport_or_provider(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """Gateway client and server independently enforce the sealed snapshot binding."""

    loaded = _catalog(monkeypatch).load(_contract())
    no_transport_calls: list[Any] = []

    class NeverTransport:
        def request(self, message: Any, timeout_ms: int) -> Any:
            no_transport_calls.append((message, timeout_ms))
            raise AssertionError("metadata failure must precede transport")

    snapshot = _snapshot()
    with pytest.raises(RuntimePluginError) as day_error:
        compose_gateway_managed_client(
            loaded,
            state_directory=tmp_path / "wrong-day",
            provider="fixture_provider",
            environment="production",
            account_ref="fixture_account",
            strategy_id="example.014_1.ctp_options_lowfreq",
            writer_id="gateway_client_writer",
            client=NeverTransport(),
            trading_day="20260923",
            instrument_metadata_snapshot=snapshot,
            instrument_clock_ns=lambda: 1_500,
        )
    assert day_error.value.code == "INSTRUMENT_SNAPSHOT_TRADING_DAY_MISMATCH"
    assert no_transport_calls == []

    runtime = compose_gateway_managed_client(
        loaded,
        state_directory=tmp_path / "client",
        provider="fixture_provider",
        environment="production",
        account_ref="fixture_account",
        strategy_id="example.014_1.ctp_options_lowfreq",
        writer_id="gateway_client_writer",
        client=NeverTransport(),
        **_metadata_kwargs(),
    )
    try:
        bad = runtime.execution.OrderIntent.limit(
            intent_id="intent.gateway.bad-digest",
            scope=runtime.scope,
            signal_id="signal.gateway.bad-digest",
            instrument="fixture/contract",
            side=runtime.execution.Side.BUY,
            quantity=Decimal("1"),
            price=Decimal("10"),
            metadata_version=runtime.instrument_metadata_snapshot.metadata_version,
            tags={"instrument_metadata_digest": "0" * 64, "quantity_unit": "contracts"},
        )
        with pytest.raises(RuntimePluginError) as digest_error:
            runtime.dispatcher.command_for_intent(bad)
        assert digest_error.value.code == "INSTRUMENT_SNAPSHOT_METADATA_DIGEST_MISMATCH"
        assert no_transport_calls == []

        bad_unit = runtime.execution.OrderIntent.limit(
            intent_id="intent.gateway.bad-unit",
            scope=runtime.scope,
            signal_id="signal.gateway.bad-unit",
            instrument="fixture/contract",
            side=runtime.execution.Side.BUY,
            quantity=Decimal("1"),
            price=Decimal("10"),
            metadata_version=runtime.instrument_metadata_snapshot.metadata_version,
            tags={
                "instrument_metadata_digest": runtime.instrument_metadata_snapshot.instrument_digest(
                    "fixture/contract"
                ),
                "quantity_unit": "base",
            },
        )
        with pytest.raises(RuntimePluginError) as unit_error:
            runtime.dispatcher.command_for_intent(bad_unit)
        assert unit_error.value.code == "INSTRUMENT_SNAPSHOT_QUANTITY_UNIT_MISMATCH"
        assert no_transport_calls == []
    finally:
        runtime.close()

    provider_calls: list[str] = []
    authority = compose_gateway_execution_authority(
        loaded,
        state_directory=tmp_path / "server",
        provider="fixture_provider",
        environment="production",
        account_ref="fixture_account",
        strategy_id="example.014_1.ctp_options_lowfreq",
        writer_id="gateway_server_writer",
        policy_id="gateway_policy",
        max_increase_notional=Decimal("100"),
        max_increase_count=1,
        provider_dispatch=lambda intent: provider_calls.append(intent.intent_id),
        **_metadata_kwargs(),
    )
    try:
        server_bad = authority.execution.OrderIntent.limit(
            intent_id="intent.gateway.server-bad-digest",
            scope=authority.scope,
            signal_id="signal.gateway.server-bad-digest",
            instrument="fixture/contract",
            side=authority.execution.Side.BUY,
            quantity=Decimal("1"),
            price=Decimal("10"),
            metadata_version=authority.server_runtime.instrument_metadata_snapshot.metadata_version,
            tags={"instrument_metadata_digest": "0" * 64, "quantity_unit": "contracts"},
        )
        record = authority.server_runtime.submit(server_bad, authority._provider_dispatch)
        assert record.state is authority.execution.ExecutionState.BLOCKED
        assert provider_calls == []
    finally:
        authority.close()


def test_gateway_client_pre_authority_crash_recovers_dispatching_as_unknown_without_transport_io(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """A crash before command creation has no resend path after client restart recovery."""

    loaded = _catalog(monkeypatch).load(_contract())
    transport_calls: list[Any] = []

    class NeverTransport:
        def request(self, message: Any, timeout_ms: int) -> Any:
            transport_calls.append((message, timeout_ms))
            raise AssertionError("client recovery must not call the gateway")

    runtime = compose_gateway_managed_client(
        loaded,
        state_directory=tmp_path / "client",
        provider="fixture_provider",
        environment="production",
        account_ref="fixture_account",
        strategy_id="example.014_1.ctp_options_lowfreq",
        writer_id="gateway_client_writer",
        client=NeverTransport(),
        **_metadata_kwargs(),
    )
    intent = _intent(runtime, "intent.gateway.pre-authority-gap")

    def crash_before_authority(_intent: Any) -> Any:
        raise SystemExit("simulated gateway client crash before authority")

    monkeypatch.setattr(runtime.dispatcher, "submit", crash_before_authority)
    try:
        with pytest.raises(SystemExit, match="before authority"):
            runtime._facade.submit(intent, runtime.dispatcher)
        assert runtime.execution_store.get(intent.intent_id, scope=runtime.scope).state is (
            runtime.execution.ExecutionState.DISPATCHING
        )

        assert runtime.recover() == (intent.intent_id,)
        recovered = runtime.execution_store.get(intent.intent_id, scope=runtime.scope)
        assert recovered.state is runtime.execution.ExecutionState.UNKNOWN
        assert recovered.review_required is True
        assert transport_calls == []
        assert (
            runtime.submit(intent, lambda _intent: None).state
            is runtime.execution.ExecutionState.UNKNOWN
        )
        assert transport_calls == []
    finally:
        runtime.close()
