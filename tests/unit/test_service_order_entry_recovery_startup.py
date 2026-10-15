"""ServiceHost startup recovery and live execution-store ownership."""

from __future__ import annotations

import asyncio
import multiprocessing
import os
import sqlite3
from contextlib import suppress
from decimal import Decimal
from pathlib import Path
from typing import Any

import pytest
import zmq
from bt_api_base.service_protocol import WireIdentity, WireRole, WireScope
from bt_api_execution import (
    CancelObservation,
    ExecutionScope,
    ExecutionState,
    OrderIntent,
    PositionEffect,
    ProviderObservation,
    Side,
    SqliteExecutionStore,
)

from bt_api_py.service_components import ComponentResources
from bt_api_py.service_host import ServiceHost, ServiceHostError, TrustedServiceAuthority
from bt_api_py.service_registry import PrincipalGrant, ResourceGrant, ServiceCallContext
from tests.unit.test_service_host import _service_config

_DOMAIN = "paper:exe02-startup"
_SERVICE_ID = "execution-startup-service"
_SERVICE_PRINCIPAL = "execution-startup-server"
_INSTANCE_ID = "execution-owner"
_ACCOUNT = "account-startup"
_STRATEGY = "strategy-startup"
_CLIENT_PRINCIPAL = "execution-startup-client"
_WIRE_SCOPE = WireScope(_DOMAIN, account_ref=_ACCOUNT, strategy_ref=_STRATEGY)
_EXECUTION_SCOPE = ExecutionScope("PAPER", "simulation", _ACCOUNT, _STRATEGY, "20261015")
_WRITER_ID = "execution-order-entry"
_BUSINESS_ID = "exe02-startup-business-id"
_ORDER_ARGUMENTS: dict[str, Any] = {
    "intent_id": _BUSINESS_ID,
    "signal_id": f"signal-{_BUSINESS_ID}",
    "instrument": "BTC-USDT",
    "side": "BUY",
    "position_effect": "OPEN",
    "order_type": "LIMIT",
    "quantity": "2",
    "price": "50000",
    "time_in_force": "GTC",
    "reduce_only": False,
    "metadata_version": "metadata-v1",
    "deadline_ns": None,
    "tags": {},
}


def _configure_zmq_event_loop_policy() -> None:
    selector_policy = getattr(asyncio, "WindowsSelectorEventLoopPolicy", None)
    if os.name == "nt" and selector_policy is not None:
        asyncio.set_event_loop_policy(selector_policy())


class _Provider:
    def __init__(self) -> None:
        self.submitted: list[str] = []
        self.cancelled: list[str] = []

    def submit(self, intent: OrderIntent) -> ProviderObservation:
        self.submitted.append(intent.intent_id)
        return ProviderObservation.accepted(intent.intent_id, "synthetic-order-1")

    def cancel(self, intent: Any) -> CancelObservation:
        self.cancelled.append(intent.cancel_id)
        return CancelObservation.accepted(
            intent.cancel_id, intent.target_intent_id, intent.provider_order_id
        )


class _DurableJournalProvider(_Provider):
    def __init__(self, journal_path: Path, *, exit_after_submit: bool) -> None:
        super().__init__()
        self._journal_path = journal_path
        self._exit_after_submit = exit_after_submit

    def submit(self, intent: OrderIntent) -> ProviderObservation:
        connection = sqlite3.connect(self._journal_path, isolation_level=None)
        try:
            connection.execute(
                "INSERT INTO provider_calls (intent_id) VALUES (?)", (intent.intent_id,)
            )
        finally:
            connection.close()
        self.submitted.append(intent.intent_id)
        if self._exit_after_submit:
            os._exit(73)
        return ProviderObservation.accepted(intent.intent_id, "synthetic-order-1")


def _service_host_process(
    control,
    mode: str,
    store_path: str,
    registry_path: str,
    journal_path: str,
) -> None:
    _configure_zmq_event_loop_policy()
    provider = _DurableJournalProvider(
        Path(journal_path), exit_after_submit=mode == "crash_during_dispatch"
    )
    config = _host_config(("service.order_entry", "service.order_query"))
    authority, client_identity = _authority(config, ("service.order_entry", "service.order_query"))
    closed: list[str] = []
    stores: list[SqliteExecutionStore] = []
    host = ServiceHost(
        config,
        authority,
        registry_path,
        _resource_provider(Path(store_path), provider, closed, stores),
    )
    try:
        host.start()
        if mode == "crash_during_dispatch":
            control.send(("started", host.state))
            if control.recv() != ("submit",):
                raise AssertionError("unexpected crash-child command")
            _submit_again(host, client_identity)
            control.send(("returned",))
            host.close()
            return

        if mode != "restart":
            raise AssertionError("unexpected host-process mode")
        store = stores[0]
        recovered = store.get(_BUSINESS_ID, scope=_EXECUTION_SCOPE)
        if recovered is None:
            raise AssertionError("restart host lost the durable business ID")
        reply = _submit_again(host, client_identity)
        after_replay = store.get(_BUSINESS_ID, scope=_EXECUTION_SCOPE)
        result = {
            "recovered_state": recovered.state.value,
            "recovered_reason": recovered.unknown_reason,
            "replay_state": reply["result"]["record"]["state"],
            "stored_state": after_replay.state.value if after_replay is not None else None,
            "provider_calls": _provider_journal_count(Path(journal_path)),
            "unknown_events": _dispatch_unknown_count(Path(store_path)),
        }
        host.close()
        result["host_state"] = host.state
        result["resource_closes"] = len(closed)
        control.send(("complete", result))
    except BaseException as error:
        if host.state not in {"closed", "failed"}:
            with suppress(BaseException):
                host.close()
        with suppress(BaseException):
            control.send(("error", getattr(error, "code", type(error).__name__)))
        raise
    finally:
        control.close()


def _start_host_process(
    context, mode: str, store_path: Path, registry_path: Path, journal_path: Path
):
    parent, child = context.Pipe(duplex=True)
    process = context.Process(
        target=_service_host_process,
        args=(child, mode, str(store_path), str(registry_path), str(journal_path)),
        name=f"execution-recovery-{mode}",
    )
    try:
        process.start()
    except BaseException:
        parent.close()
        child.close()
        process.close()
        raise
    child.close()
    return {"process": process, "pipe": parent}


def _finish_host_process(record, *, timeout: float = 20.0) -> int:
    if record.get("closed") is True:
        return record["exitcode"]
    process = record["process"]
    process.join(timeout)
    if process.is_alive():
        process.terminate()
        process.join(5.0)
    assert not process.is_alive(), "test-owned execution host child was not reaped"
    exitcode = process.exitcode
    record["pipe"].close()
    process.close()
    record["exitcode"] = exitcode
    record["closed"] = True
    return exitcode


def _prepare_provider_journal(path: Path) -> None:
    connection = sqlite3.connect(path, isolation_level=None)
    try:
        connection.execute(
            "CREATE TABLE provider_calls (call_id INTEGER PRIMARY KEY, intent_id TEXT NOT NULL)"
        )
    finally:
        connection.close()


def _provider_journal_count(path: Path) -> int:
    connection = sqlite3.connect(path)
    try:
        return int(connection.execute("SELECT COUNT(*) FROM provider_calls").fetchone()[0])
    finally:
        connection.close()


def test_service_host_process_crash_restart_recovers_before_same_id_replay(tmp_path):
    store_path = tmp_path / "execution-process-crash.sqlite3"
    provider_journal_path = tmp_path / "execution-process-provider.sqlite3"
    _prepare_provider_journal(provider_journal_path)
    context = multiprocessing.get_context("spawn")
    crashed = _start_host_process(
        context,
        "crash_during_dispatch",
        store_path,
        tmp_path / "registry-before-crash.sqlite3",
        provider_journal_path,
    )

    try:
        assert crashed["pipe"].poll(20.0), "initial ServiceHost missed its startup bound"
        assert crashed["pipe"].recv() == ("started", "started")
        crashed["pipe"].send(("submit",))
        assert _finish_host_process(crashed) == 73
    finally:
        _finish_host_process(crashed)

    after_crash = SqliteExecutionStore(store_path)
    try:
        interrupted = after_crash.get(_BUSINESS_ID, scope=_EXECUTION_SCOPE)
        assert interrupted is not None
        assert interrupted.state is ExecutionState.DISPATCHING
        assert _dispatch_unknown_count(store_path) == 0
        assert _provider_journal_count(provider_journal_path) == 1
    finally:
        after_crash.close()

    restarted = _start_host_process(
        context,
        "restart",
        store_path,
        tmp_path / "registry-after-crash.sqlite3",
        provider_journal_path,
    )
    try:
        assert restarted["pipe"].poll(20.0), "restart ServiceHost missed its result bound"
        result_message = restarted["pipe"].recv()
        assert result_message[0] == "complete", result_message
        assert _finish_host_process(restarted) == 0
    finally:
        _finish_host_process(restarted)

    assert result_message[1] == {
        "recovered_state": ExecutionState.UNKNOWN.value,
        "recovered_reason": "interrupted_order_dispatch",
        "replay_state": ExecutionState.UNKNOWN.value,
        "stored_state": ExecutionState.UNKNOWN.value,
        "provider_calls": 1,
        "unknown_events": 1,
        "host_state": "closed",
        "resource_closes": 1,
    }


def _host_config(ports: tuple[str, ...]):
    return _service_config(
        {
            "execution": {
                "config_schema_version": 2,
                "settings": {
                    "instance_id": _INSTANCE_ID,
                    "bind_ports": list(ports),
                    "options": {"role": "account_owner"},
                },
            }
        },
        service_id=_SERVICE_ID,
        scope_ref="execution-startup-scope-v1",
        security_ref="execution-startup-security-v1",
    )


def _authority(config, ports: tuple[str, ...]) -> tuple[TrustedServiceAuthority, WireIdentity]:
    client_public, _client_secret = zmq.curve_keypair()
    server_public, server_secret = zmq.curve_keypair()
    client_identity = WireIdentity(WireRole.STRATEGY_CLIENT, _CLIENT_PRINCIPAL, _STRATEGY)
    grants: list[ResourceGrant] = []
    if "service.order_entry" in ports:
        grants.append(
            ResourceGrant(
                _INSTANCE_ID,
                "service.order_entry",
                "execution-entry-scope-v1",
                _WIRE_SCOPE,
                frozenset({"orders.submit", "orders.cancel"}),
            )
        )
    if "service.order_query" in ports:
        grants.append(
            ResourceGrant(
                _INSTANCE_ID,
                "service.order_query",
                "execution-query-scope-v1",
                _WIRE_SCOPE,
                frozenset({"orders.query"}),
            )
        )
    authority = TrustedServiceAuthority(
        identity=WireIdentity(WireRole.SERVICE, _SERVICE_PRINCIPAL, _SERVICE_ID),
        scope=WireScope(_DOMAIN),
        scope_ref=config.scope_ref,
        scope_version="execution-startup-scope-v1",
        security_ref="execution-startup-security-v1",
        security_version="execution-startup-security-v1",
        registration_policy_ref=config.registration_policy_ref,
        registration_policy_version="execution-startup-policy-v1",
        service_requirements_ref=config.service_requirements_ref,
        service_requirements_version="execution-startup-requirements-v1",
        curve_public_key=server_public,
        curve_secret_key=server_secret,
        principals={client_public: _CLIENT_PRINCIPAL},
        grants={_CLIENT_PRINCIPAL: (PrincipalGrant(client_identity, tuple(grants)),)},
    )
    return authority, client_identity


def _resource_provider(
    store_path: Path,
    provider: _Provider,
    closed: list[str],
    stores: list[SqliteExecutionStore],
):
    def provide(component, _instance_id, _options, _scope):
        assert component == "execution"
        store = SqliteExecutionStore(store_path)
        stores.append(store)

        def close_store() -> None:
            closed.append("closed")
            store.close()

        return ComponentResources.owned(
            {
                "store": store,
                "targets": {_WIRE_SCOPE: _EXECUTION_SCOPE},
                "dispatch": provider,
                "admission": {"allow_unprotected": True},
            },
            ready=lambda: True,
            close=close_store,
        )

    return provide


def _make_host(
    tmp_path: Path,
    *,
    store_path: Path,
    registry_name: str,
    ports: tuple[str, ...] = ("service.order_entry", "service.order_query"),
    provider: _Provider | None = None,
):
    _configure_zmq_event_loop_policy()
    selected_provider = _Provider() if provider is None else provider
    closed: list[str] = []
    stores: list[SqliteExecutionStore] = []
    config = _host_config(ports)
    authority, client_identity = _authority(config, ports)
    host = ServiceHost(
        config,
        authority,
        tmp_path / registry_name,
        _resource_provider(store_path, selected_provider, closed, stores),
    )
    return host, selected_provider, closed, stores, client_identity


def _seed_dispatching(path: Path, *, writer_id: str = _WRITER_ID) -> None:
    store = SqliteExecutionStore(path)
    try:
        lease = store.acquire_or_renew_lease(_EXECUTION_SCOPE, writer_id, ttl_ns=60_000_000_000)
        intent = OrderIntent.limit(
            intent_id=_BUSINESS_ID,
            scope=_EXECUTION_SCOPE,
            signal_id=f"signal-{_BUSINESS_ID}",
            instrument="BTC-USDT",
            side=Side.BUY,
            position_effect=PositionEffect.OPEN,
            quantity=Decimal("2"),
            price=Decimal("50000"),
            metadata_version="metadata-v1",
            tags={},
        )
        store.admit_intent(intent, writer_lease=lease)
        store.activate_intent(
            intent.intent_id,
            _EXECUTION_SCOPE,
            "synthetic-permit",
            writer_lease=lease,
        )
        claimed, did_claim = store.claim_for_dispatch(
            intent.intent_id, _EXECUTION_SCOPE, writer_lease=lease
        )
        assert did_claim is True
        assert claimed.state is ExecutionState.DISPATCHING
    finally:
        store.close()


def _dispatch_unknown_count(path: Path) -> int:
    store = SqliteExecutionStore(path)
    try:
        events = store.read_outbox(scope=_EXECUTION_SCOPE, after_sequence=0, limit=1000)
        return sum(event.event_type == "dispatch_unknown" for event in events)
    finally:
        store.close()


def _write_lease_rows(path: Path) -> tuple[tuple[str, int], ...]:
    connection = sqlite3.connect(path)
    try:
        return tuple(
            (str(owner), int(token))
            for owner, token in connection.execute(
                "SELECT owner_id, fencing_token FROM execution_writer_leases"
            )
        )
    finally:
        connection.close()


def _submit_again(host: ServiceHost, client_identity: WireIdentity):
    component = host._components[0]
    context = ServiceCallContext(
        client_identity,
        _WIRE_SCOPE,
        "execution-startup-registration",
        _CLIENT_PRINCIPAL,
    )
    return component.handlers["orders.submit"](context, _ORDER_ARGUMENTS)


def test_host_recovers_before_replay_and_excludes_a_second_live_owner(tmp_path, monkeypatch):
    import bt_api_execution.service_order_entry as order_entry_module

    store_path = tmp_path / "execution.sqlite3"
    _seed_dispatching(store_path)
    recovery_calls: list[int] = []
    original_recovery = order_entry_module.ExecutionOrderEntryService.recover_interrupted_dispatches

    def observe_recovery(service):
        recovery_calls.append(1)
        return original_recovery(service)

    monkeypatch.setattr(
        order_entry_module.ExecutionOrderEntryService,
        "recover_interrupted_dispatches",
        observe_recovery,
    )

    provider = _Provider()
    first, _, first_closed, first_stores, client_identity = _make_host(
        tmp_path,
        store_path=store_path,
        registry_name="execution-registry-first.sqlite3",
        provider=provider,
    )
    second, _, second_closed, _, _ = _make_host(
        tmp_path,
        store_path=store_path,
        registry_name="execution-registry-second.sqlite3",
        provider=provider,
    )
    restarted, _, restarted_closed, _, restarted_identity = _make_host(
        tmp_path,
        store_path=store_path,
        registry_name="execution-registry-restarted.sqlite3",
        provider=provider,
    )

    try:
        first.start()
        assert recovery_calls == [1]
        recovered = first_stores[0].get(_BUSINESS_ID, scope=_EXECUTION_SCOPE)
        assert recovered is not None
        assert recovered.state is ExecutionState.UNKNOWN
        assert recovered.unknown_reason == "interrupted_order_dispatch"
        assert _dispatch_unknown_count(store_path) == 1
        assert provider.submitted == []

        with pytest.raises(ServiceHostError) as error:
            second.start()
        assert error.value.code == "ORDER_OWNER_ALREADY_ACTIVE"
        assert recovery_calls == [1]
        assert second.state == "failed"
        assert second_closed == ["closed"]
        assert provider.submitted == []

        replay = _submit_again(first, client_identity)
        assert replay["result"]["record"]["state"] == ExecutionState.UNKNOWN.value
        assert provider.submitted == []
        assert _dispatch_unknown_count(store_path) == 1

        first.close()
        assert first_closed == ["closed"]

        restarted.start()
        assert recovery_calls == [1, 1]
        assert _dispatch_unknown_count(store_path) == 1
        restarted_replay = _submit_again(restarted, restarted_identity)
        assert restarted_replay["result"]["record"]["state"] == ExecutionState.UNKNOWN.value
        assert provider.submitted == []
    finally:
        if first.state not in {"closed", "failed"}:
            first.close()
        if second.state not in {"closed", "failed"}:
            second.close()
        if restarted.state not in {"closed", "failed"}:
            restarted.close()
    assert restarted_closed == ["closed"]


def test_unavailable_writer_lease_fails_startup_and_closes_owned_resources(tmp_path):
    store_path = tmp_path / "execution-conflicting-lease.sqlite3"
    store = SqliteExecutionStore(store_path)
    try:
        store.acquire_or_renew_lease(_EXECUTION_SCOPE, "other-live-writer", ttl_ns=60_000_000_000)
    finally:
        store.close()

    host, provider, closed, _stores, _client_identity = _make_host(
        tmp_path,
        store_path=store_path,
        registry_name="execution-registry-conflicting.sqlite3",
    )
    with pytest.raises(ServiceHostError) as error:
        host.start()

    assert error.value.code == "ORDER_RECOVERY_FAILED"
    assert host.state == "failed"
    assert host._server is None
    assert closed == ["closed"]
    assert provider.submitted == []
    assert _write_lease_rows(store_path) == (("other-live-writer", 1),)


def test_query_only_account_owner_skips_recovery_and_owner_lock(tmp_path, monkeypatch):
    import bt_api_execution.service_order_entry as order_entry_module

    store_path = tmp_path / "execution-query-only.sqlite3"
    recovery_calls: list[int] = []
    original_recovery = order_entry_module.ExecutionOrderEntryService.recover_interrupted_dispatches

    def observe_recovery(service):
        recovery_calls.append(1)
        return original_recovery(service)

    monkeypatch.setattr(
        order_entry_module.ExecutionOrderEntryService,
        "recover_interrupted_dispatches",
        observe_recovery,
    )
    query_host, _, query_closed, _, _ = _make_host(
        tmp_path,
        store_path=store_path,
        registry_name="execution-query-only-registry.sqlite3",
        ports=("service.order_query",),
    )
    write_host, _, write_closed, _, _ = _make_host(
        tmp_path,
        store_path=store_path,
        registry_name="execution-write-registry.sqlite3",
        ports=("service.order_entry",),
    )

    try:
        query_host.start()
        assert _write_lease_rows(store_path) == ()
        assert recovery_calls == []

        # The read-only composition did not retain execution-owner exclusion.
        write_host.start()
        assert recovery_calls == [1]
        assert _write_lease_rows(store_path) == ((_WRITER_ID, 1),)
    finally:
        if write_host.state not in {"closed", "failed"}:
            write_host.close()
        if query_host.state not in {"closed", "failed"}:
            query_host.close()
    assert write_closed == ["closed"]
    assert query_closed == ["closed"]


def test_in_memory_store_identity_excludes_a_second_live_owner(tmp_path, monkeypatch):
    import bt_api_execution.service_order_entry as order_entry_module

    _configure_zmq_event_loop_policy()
    shared_store = SqliteExecutionStore(":memory:")
    provider = _Provider()
    borrowed = ComponentResources.borrowed(
        {
            "store": shared_store,
            "targets": {_WIRE_SCOPE: _EXECUTION_SCOPE},
            "dispatch": provider,
            "admission": {"allow_unprotected": True},
        },
        ready=lambda: True,
    )
    recovery_calls: list[int] = []
    original_recovery = order_entry_module.ExecutionOrderEntryService.recover_interrupted_dispatches

    def observe_recovery(service):
        recovery_calls.append(1)
        return original_recovery(service)

    monkeypatch.setattr(
        order_entry_module.ExecutionOrderEntryService,
        "recover_interrupted_dispatches",
        observe_recovery,
    )
    config = _host_config(("service.order_entry",))
    authority, _client_identity = _authority(config, ("service.order_entry",))

    def resource_provider(*_args):
        return borrowed

    first = ServiceHost(
        config,
        authority,
        tmp_path / "execution-memory-registry-first.sqlite3",
        resource_provider,
    )
    second = ServiceHost(
        config,
        authority,
        tmp_path / "execution-memory-registry-second.sqlite3",
        resource_provider,
    )

    try:
        first.start()
        assert recovery_calls == [1]
        with pytest.raises(ServiceHostError) as error:
            second.start()
        assert error.value.code == "ORDER_OWNER_ALREADY_ACTIVE"
        assert recovery_calls == [1]
        assert second.state == "failed"
        assert provider.submitted == []
    finally:
        if first.state not in {"closed", "failed"}:
            first.close()
        if second.state not in {"closed", "failed"}:
            second.close()
        shared_store.close()


def test_owner_file_close_failure_keeps_os_and_local_lock_until_retry():
    import bt_api_py.service_components as components_module

    key = "execution-owner-close-retry-test"

    class _File:
        def __init__(self):
            self.closed = False
            self.lock_held = True
            self.fail_close_once = True

        def close(self):
            if self.fail_close_once:
                self.fail_close_once = False
                raise OSError("descriptor still open")
            self.closed = True
            self.lock_held = False

    owner_file = _File()
    with components_module._EXECUTION_OWNER_LOCK_GUARD:
        components_module._EXECUTION_OWNER_LOCK_PATHS.add(key)
        components_module._EXECUTION_OWNER_LOCK_FILES[key] = owner_file
    lock = components_module._ExecutionOwnerFileLock(owner_file, key)

    with pytest.raises(OSError, match="descriptor still open"):
        lock.close()
    assert lock._owner_file is owner_file
    assert key in components_module._EXECUTION_OWNER_LOCK_PATHS
    assert components_module._EXECUTION_OWNER_LOCK_FILES[key] is owner_file
    assert owner_file.lock_held is True

    lock.close()
    assert owner_file.closed is True
    assert owner_file.lock_held is False
    assert lock._owner_file is None
    assert key not in components_module._EXECUTION_OWNER_LOCK_PATHS
    assert key not in components_module._EXECUTION_OWNER_LOCK_FILES
