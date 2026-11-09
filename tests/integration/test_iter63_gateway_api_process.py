"""Gateway facade over local Registry/CURVE and synthetic service facts.

The spawned provider and BT pending host are local test resources. Their
observations are not venue execution, account truth, or BT Broker wiring.
"""

import multiprocessing
import os

import pytest
import zmq
from bt_api_base.gateway_direct_protocol import GatewayCommandBinding

from bt_api_py.bt_api import BtApi
from bt_api_py.gateway_direct_client import GatewayCancelClientResult, GatewayClientResult
from bt_api_py.service_runtime import ClientCurveKeys
from tests.integration.test_iter63_gateway_cancel_rpc import SyntheticProvider, eventually, order
from tests.integration.test_iter63_gateway_direct_service import ACCOUNT, CLIENT_A, CLIENT_B
from tests.integration.test_iter63_gateway_strategy_client import (
    _bt_host,
    _client_yaml,
    _gateway_process,
    _session,
)
from tests.integration.test_iter63_strategy_read_session import (
    _make_session,
    _settings,
)
from tests.integration.test_service_account_instrument_metadata import _free_endpoint

pytest_plugins = ["tests.integration.test_iter63_strategy_read_session"]


def _tracked_gateway(root, endpoint, server_keys, public_keys, messages, controls, sends):
    # Count calls at the synthetic leaf while retaining its real implementation.
    submit_order = SyntheticProvider.submit_order

    def tracked_submit(self, *args, **kwargs):
        with sends.get_lock():
            sends.value += 1
        return submit_order(self, *args, **kwargs)

    SyntheticProvider.submit_order = tracked_submit
    _gateway_process(root, endpoint, server_keys, public_keys, messages, controls)


@pytest.fixture
def gateway_process(tmp_path):
    context = multiprocessing.get_context("spawn")
    messages, controls = context.Queue(), context.Queue()
    server_keys, a_keys, b_keys = zmq.curve_keypair(), zmq.curve_keypair(), zmq.curve_keypair()
    endpoint = _free_endpoint()
    sends = context.Value("i", 0)
    process = context.Process(
        target=_tracked_gateway,
        args=(
            str(tmp_path),
            endpoint,
            server_keys,
            (a_keys[0], b_keys[0]),
            messages,
            controls,
            sends,
        ),
    )
    sessions = []
    process.start()
    try:
        ready = messages.get(timeout=20)
        assert ready[0] == "ready" and ready[1] == process.pid != os.getpid(), ready

        def make_session(identity, name):
            keys = a_keys if identity == CLIENT_A else b_keys
            binding = GatewayCommandBinding(
                ready[2], ACCOUNT, identity.actor_ref, identity.principal_ref
            )
            _, host = _bt_host(tmp_path, name, binding)
            session = _session(
                _client_yaml(tmp_path, endpoint, identity),
                identity,
                "run-" + name,
                binding,
                host,
                ClientCurveKeys(server_keys[0], *keys),
            )
            sessions.append(session)
            return session

        yield make_session, messages, controls, sends
    finally:
        errors = []
        for session in reversed(sessions):
            try:
                closed = session.close(timeout_seconds=10)
                if closed.state != "closed" or closed.thread_alive:
                    errors.append((closed.state, closed.thread_alive))
            except Exception as exc:
                errors.append(repr(exc))
        controls.put("stop")
        process.join(15)
        if process.is_alive():
            process.terminate()
            process.join(5)
            errors.append("synthetic Gateway required termination")
        assert process.exitcode == 0
        closed = messages.get(timeout=3)
        assert closed == ("closed", process.pid), closed
        controls.close()
        messages.close()
        assert not errors, errors


def test_gateway_facade_process_commands_stream_and_session_ownership(gateway_process, monkeypatch):
    make_session, messages, controls, sends = gateway_process
    session = make_session(CLIENT_A, "borrowed")

    def direct_init_forbidden(*args, **kwargs):
        pytest.fail("Gateway construction must not construct direct BtApi")

    monkeypatch.setattr(BtApi, "__init__", direct_init_forbidden)
    api = BtApi.from_gateway(session, exchange_name="TEST")
    assert session.status().state == "new"
    assert api.start(timeout_seconds=10).healthy
    result = api.reserve_order("TEST", order("facade-order"), timeout_seconds=5)
    assert type(result) is GatewayClientResult
    assert result.outcome == "OBSERVED" and result.record.request_key == "facade-order"
    dispatched = api.dispatch_order("TEST", "facade-order", timeout_seconds=5)
    assert type(dispatched) is GatewayClientResult and dispatched.outcome == "QUEUED"

    def sent():
        result = api.command_status("TEST", "facade-order", timeout_seconds=5)
        return result.record is not None and result.record.state == "SENT"

    eventually(sent)
    observed = api.command_status("TEST", "facade-order", timeout_seconds=5)
    assert type(observed) is GatewayClientResult and observed.record.request_key == "facade-order"
    again = api.dispatch_order("TEST", "facade-order", timeout_seconds=5)
    assert type(again) is GatewayClientResult and again.outcome == "TRANSPORT_NOT_STARTED"
    assert sends.value == 1

    cancelled = api.cancel_order(
        "TEST", "facade-order", operation_id="facade-cancel", timeout_seconds=5
    )
    assert type(cancelled) is GatewayCancelClientResult and cancelled.outcome == "QUEUED"
    eventually(
        lambda: api.cancel_status("TEST", "facade-cancel", timeout_seconds=5).record.state == "SENT"
    )
    cancel = api.cancel_status("TEST", "facade-cancel", timeout_seconds=5)
    assert type(cancel) is GatewayCancelClientResult
    assert (
        cancel.record.operation_id == "facade-cancel"
        and cancel.record.request_key == "facade-order"
    )

    events = api.gateway_execution_stream("TEST")
    assert events is session.execution_stream_client
    assert events.progress().progress.acknowledged_sequence == 0
    page = events.read_after().page
    assert len(page.events) == 1 and page.events[0].observation.kind == "trade"
    assert events.progress().progress.acknowledged_sequence == 0
    assert events.ack(page.events[-1].sequence, page.epoch).outcome == "OBSERVED"
    assert events.progress().progress.acknowledged_sequence == page.events[-1].sequence
    with pytest.raises(ValueError):
        api.command_status("WRONG", "facade-order", timeout_seconds=5)

    api.close(timeout_seconds=10)
    assert session.status().healthy
    assert session.command_client.status("facade-order").record.state == "SENT"
    assert session.close(timeout_seconds=10).state == "closed"

    owned_session = make_session(CLIENT_B, "owned")
    owned = BtApi.from_gateway(owned_session, exchange_name="TEST", owns_session=True)
    assert owned.start(timeout_seconds=10).healthy
    owned.close(timeout_seconds=10)
    closed = owned_session.status()
    assert closed.state == "closed" and not closed.thread_alive


def test_gateway_facade_delegates_configured_account_read(dual_service):
    dual_service[4]()
    session, _ = _make_session(dual_service, _settings())
    api = BtApi.from_gateway(session, exchange_name="TEST", owns_session=True)
    try:
        assert api.start(timeout_seconds=10).healthy
        account = api.account_read("TEST")
        assert account is session.account_client
        view = account.refresh(timeout_seconds=5)
        assert view.observation.account_key.account_id == ACCOUNT
        assert account.snapshot(timeout_seconds=5).snapshot_id == view.snapshot_id
    finally:
        api.close(timeout_seconds=10)
        assert session.status().state == "closed" and not session.status().thread_alive
