"""Host-lifetime exclusion for the Risk account order writer."""

from __future__ import annotations

import multiprocessing
from typing import Any

import zmq

from tests.unit import test_service_risk_only_order_integration as risk_fixture
from tests.unit._spawned_process_helpers import (
    _assert_no_fallback_cleanup,
    _bounded_reap_spawned_process,
    _close_normally,
    _receive,
    _start_process,
)


def _instrumented_risk_host_process(
    control,
    raw_config: dict[str, Any],
    registry_path: str,
    keys: dict[str, bytes],
    gate_path: str,
    journal_path: str,
    provider_path: str,
) -> None:
    """Run the real fixture Host while reporting lifecycle boundaries."""

    import bt_api_py.service_registry as service_registry_module
    import bt_api_py.service_risk_order_entry as risk_entry_module

    original_recovery = risk_entry_module.RiskOrderEntryService.recover_interrupted_dispatches
    original_registration = service_registry_module.ServiceRegistry.register_handler

    def observe_recovery(service):
        control.send(("recovery",))
        return original_recovery(service)

    def observe_registration(registry, *args, **kwargs):
        control.send(("registration",))
        return original_registration(registry, *args, **kwargs)

    risk_entry_module.RiskOrderEntryService.recover_interrupted_dispatches = observe_recovery
    service_registry_module.ServiceRegistry.register_handler = observe_registration

    feed_type = risk_fixture._SyntheticBtApiFeed
    original_provider_methods = {
        name: getattr(feed_type, name) for name in ("make_order", "query_order", "get_deals")
    }
    for method_name, original_method in original_provider_methods.items():

        def observe_provider(*args, _name=method_name, _method=original_method, **kwargs):
            control.send(("provider", _name))
            return _method(*args, **kwargs)

        setattr(feed_type, method_name, observe_provider)

    try:
        risk_fixture._host_process(
            control,
            raw_config,
            registry_path,
            keys,
            gate_path,
            journal_path,
            provider_path,
        )
    finally:
        risk_entry_module.RiskOrderEntryService.recover_interrupted_dispatches = original_recovery
        service_registry_module.ServiceRegistry.register_handler = original_registration
        for name, method in original_provider_methods.items():
            setattr(feed_type, name, method)


def _startup_events(record: dict[str, Any]) -> tuple[tuple[Any, ...], ...]:
    events: list[tuple[Any, ...]] = []
    while True:
        event = _receive(record["pipe"], timeout=15.0)
        events.append(event)
        if event[0] in {"ready", "error"}:
            return tuple(events)


def _dispose_expected_start_failure(record: dict[str, Any]) -> None:
    process = record["process"]
    process.join(15)
    assert not process.is_alive(), f"{record['label']} did not leave failed startup"
    assert process.exitcode != 0, f"{record['label']} unexpectedly succeeded"
    record["pipe"].close()
    process.close()
    record["closed"] = True


def _cleanup_process(record: dict[str, Any], forced: list[str], failures: list[str]) -> None:
    if record["closed"]:
        return
    process = record["process"]
    process.join(0.2)
    if process.is_alive():
        _bounded_reap_spawned_process(record, forced, failures)
        return
    record["pipe"].close()
    process.close()
    record["closed"] = True


def test_risk_account_owner_has_host_lifetime_writer_exclusion(tmp_path) -> None:
    context = multiprocessing.get_context("spawn")
    endpoint_a = risk_fixture._endpoint()
    endpoint_b = risk_fixture._endpoint()
    assert endpoint_a != endpoint_b
    server_public, server_secret = zmq.curve_keypair()
    strategy_public, _strategy_secret = zmq.curve_keypair()
    keys = {
        "server_public": server_public,
        "server_secret": server_secret,
        "strategy_public": strategy_public,
    }

    shared_journal = str(tmp_path / "risk-owner-shared-journal.sqlite3")
    first_registry = str(tmp_path / "risk-owner-first-registry.sqlite3")
    second_registry = str(tmp_path / "risk-owner-second-registry.sqlite3")
    first_gate = str(tmp_path / "risk-owner-first-gate.sqlite3")
    second_gate = str(tmp_path / "risk-owner-second-gate.sqlite3")
    first_provider = str(tmp_path / "risk-owner-first-provider.sqlite3")
    second_provider = str(tmp_path / "risk-owner-second-provider.sqlite3")

    first_config = risk_fixture._service_config(endpoint_a)
    second_config = risk_fixture._service_config(endpoint_b)
    first_spec = first_config["components"]["risk"]["settings"]
    second_spec = second_config["components"]["risk"]["settings"]
    assert first_spec["instance_id"] == second_spec["instance_id"]
    # Both child builders use this exact configured account scope and receive
    # the same durable Risk journal path, while their RPC endpoints differ.
    assert risk_fixture._ACCOUNT_SCOPE.account_id == risk_fixture._PROVIDER_ACCOUNT

    records: list[dict[str, Any]] = []
    forced: list[str] = []
    failures: list[str] = []
    try:
        first = _start_process(
            context,
            _instrumented_risk_host_process,
            (
                first_config,
                first_registry,
                keys,
                first_gate,
                shared_journal,
                first_provider,
            ),
            "risk-account-owner-first-host",
        )
        records.append(first)
        first_events = _startup_events(first)
        assert first_events[-1] == ("ready", endpoint_a, "started"), first_events
        assert first_events[0] == ("recovery",), first_events
        assert all(event[0] != "provider" for event in first_events), first_events

        competing = _start_process(
            context,
            _instrumented_risk_host_process,
            (
                second_config,
                second_registry,
                keys,
                second_gate,
                shared_journal,
                second_provider,
            ),
            "risk-account-owner-competing-host",
        )
        records.append(competing)
        competing_events = _startup_events(competing)
        assert competing_events == (("error", "ORDER_OWNER_ALREADY_ACTIVE"),), competing_events
        _dispose_expected_start_failure(competing)

        assert _close_normally(first, expected_tag="closed") == ("closed", "closed")

        restarted = _start_process(
            context,
            _instrumented_risk_host_process,
            (
                second_config,
                second_registry,
                keys,
                second_gate,
                shared_journal,
                second_provider,
            ),
            "risk-account-owner-restarted-host",
        )
        records.append(restarted)
        restarted_events = _startup_events(restarted)
        assert restarted_events[-1] == ("ready", endpoint_b, "started"), restarted_events
        assert restarted_events[0] == ("recovery",), restarted_events
        assert all(event[0] != "provider" for event in restarted_events), restarted_events
        assert _close_normally(restarted, expected_tag="closed") == ("closed", "closed")
    finally:
        for record in reversed(records):
            _cleanup_process(record, forced, failures)
        _assert_no_fallback_cleanup(forced, failures)
