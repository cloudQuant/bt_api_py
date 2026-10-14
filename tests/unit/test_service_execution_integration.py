"""SDK managed-execution facade against a spawned execution service."""

from __future__ import annotations

import multiprocessing
from contextlib import suppress
from decimal import Decimal

import zmq
from bt_api_base.service_protocol import WireIdentity, WireRole

from bt_api_py.service_execution import ServiceExecutionClient
from bt_api_py.service_runtime import ClientCurveKeys, ServiceClientRuntime, ZmqTransportFactory
from tests.unit.test_service_order_entry_integration import (
    _PRINCIPAL,
    _QUERY_SCOPE_REF,
    _SCOPE,
    _SERVICE_ID,
    _SERVICE_PRINCIPAL,
    _STRATEGY,
    _endpoint,
    _host_process,
    _service_config,
    _strategy_config,
)


def _strategy_process(control, endpoint, strategy_public, strategy_secret, server_public):
    runtime = None
    client = None
    try:
        peer = WireIdentity(WireRole.SERVICE, _SERVICE_PRINCIPAL, _SERVICE_ID)
        runtime = ServiceClientRuntime(
            _strategy_config(endpoint, "execution-facade-curve-v1"),
            identity=WireIdentity(WireRole.STRATEGY_CLIENT, _PRINCIPAL, _STRATEGY),
            scope_resolver=lambda _ref: _SCOPE,
            peer_resolver=lambda _service: peer,
            transport_factory=ZmqTransportFactory(
                {
                    "execution-facade-curve-v1": ClientCurveKeys(
                        server_public, strategy_public, strategy_secret
                    )
                }
            ),
        )
        assert runtime.start().degraded_count == 0
        client = ServiceExecutionClient(
            runtime,
            "broker.orders",
            scope_ref="strategy-orders-a-v1",
            query_mount="broker.order_query",
            query_scope_ref=_QUERY_SCOPE_REF,
        )
        control.send(("ready", runtime.run_id))
        while True:
            command = control.recv()
            if command == ("close",):
                runtime.close()
                control.send(("closed", runtime.state))
                return
            if command[0] == "submit":
                view = client.submit(
                    intent_id=command[1],
                    signal_id=f"signal-{command[1]}",
                    instrument="BTC-USDT",
                    side="BUY",
                    position_effect="OPEN",
                    order_type="LIMIT",
                    quantity=Decimal("2"),
                    price=Decimal("50000"),
                )
                control.send(
                    (
                        "submitted",
                        view.intent_id,
                        view.state,
                        view.provider_order_id,
                        view.dispatch_attempts,
                    )
                )
                continue
            if command[0] == "submit_with_provider_failure":
                view = client.submit(
                    intent_id=command[1],
                    signal_id=f"signal-{command[1]}",
                    instrument="BTC-USDT",
                    side="BUY",
                    position_effect="OPEN",
                    order_type="LIMIT",
                    quantity=Decimal("2"),
                    price=Decimal("50000"),
                )
                control.send(("submitted", view.intent_id, view.state, view.dispatch_attempts))
                continue
            if command[0] == "cancel":
                view = client.cancel(
                    cancel_id="cancel-1",
                    target_intent_id="intent-1",
                    provider_order_id="provider-order-1",
                )
                control.send(("cancelled", view.cancel_id, view.state, view.target_intent_id))
                continue
            if command[0] == "query":
                view = client.query(command[1])
                control.send(("queried", None if view is None else view.state))
                continue
            raise AssertionError("unknown local execution facade command")
    except BaseException as exc:
        del client
        if runtime is not None:
            with suppress(BaseException):
                runtime.close()
        with suppress(Exception):
            control.send(("error", getattr(exc, "code", type(exc).__name__)))
        raise
    finally:
        control.close()


def test_spawned_strategy_uses_the_sdk_execution_facade_end_to_end(tmp_path) -> None:
    from tests.unit.test_service_account_integration import (
        _assert_no_fallback_cleanup,
        _bounded_reap_spawned_process,
        _client_command,
        _close_normally,
        _receive,
        _start_process,
    )

    context = multiprocessing.get_context("spawn")
    endpoint = _endpoint()
    server_public, server_secret = zmq.curve_keypair()
    strategy_public, strategy_secret = zmq.curve_keypair()
    keys = {
        "server_public": server_public,
        "server_secret": server_secret,
        "strategy_public": strategy_public,
    }
    records = []
    forced = []
    failures = []
    try:
        host_record = _start_process(
            context,
            _host_process,
            (
                _service_config(endpoint),
                str(tmp_path / "registry.sqlite3"),
                keys,
                str(tmp_path / "execution.sqlite3"),
            ),
            "execution-facade-host-r1",
        )
        records.append(host_record)
        assert _receive(host_record["pipe"])[0] == "ready"

        strategy_record = _start_process(
            context,
            _strategy_process,
            (endpoint, strategy_public, strategy_secret, server_public),
            "execution-facade-strategy-r1",
        )
        records.append(strategy_record)
        assert _receive(strategy_record["pipe"])[0] == "ready"

        submitted = _client_command(strategy_record, "submit", "intent-1")
        assert submitted == ("submitted", "intent-1", "ACKED", "provider-order-1", 1)
        duplicate = _client_command(strategy_record, "submit", "intent-1")
        assert duplicate == submitted
        assert _client_command(host_record, "counts")[1] == ("intent-1",)

        assert _client_command(strategy_record, "query", "intent-1") == ("queried", "ACKED")
        assert _client_command(strategy_record, "query", "intent-missing") == ("queried", None)

        assert _client_command(host_record, "fail_next_submit") == ("armed",)
        unknown = _client_command(strategy_record, "submit_with_provider_failure", "intent-2")
        # A provider failure is a durable UNKNOWN record, not a lost request:
        # the facade returns it and the caller reconciles instead of resending.
        assert unknown == ("submitted", "intent-2", "UNKNOWN", 1)
        # The facade must not resend: the service saw exactly one attempt and
        # the durable record stays UNKNOWN for reconciliation.
        assert _client_command(host_record, "counts")[1] == ("intent-1", "intent-2")
        assert _client_command(strategy_record, "query", "intent-2") == ("queried", "UNKNOWN")

        cancelled = _client_command(strategy_record, "cancel")
        assert cancelled == ("cancelled", "cancel-1", "ACKED", "intent-1")
        counts = _client_command(host_record, "counts")
        assert counts[2] == ("cancel-1",)

        _close_normally(strategy_record, expected_tag="closed")
        _close_normally(host_record, expected_tag="closed")
    finally:
        for record in reversed(records):
            _bounded_reap_spawned_process(record, forced, failures)
        _assert_no_fallback_cleanup(forced, failures)
