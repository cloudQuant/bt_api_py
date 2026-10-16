"""Contract tests for the LOAD01 synthetic RPC timing hooks."""

from __future__ import annotations

from contextlib import contextmanager
from types import SimpleNamespace
from typing import Any

import pytest
import zmq
from bt_api_transport_zmq import service_v2
from tests.unit import test_load01_hundred_strategy_30min as runner


_HOOK_TARGETS = (
    (zmq.Socket, "send"),
    (zmq.Socket, "send_multipart"),
    (service_v2._BoundedMultipartReceiver, "receive"),
    (service_v2.ZmqServiceClient, "request"),
    (service_v2.ZmqServiceServer, "__init__"),
    (runner.ServiceClientRuntime, "maintain"),
    (runner.ServiceHost, "maintain"),
    (runner.ServiceHost, "_maintain_components"),
)


@contextmanager
def _installed_trace(replacements: dict[tuple[Any, str], Any]):
    original_methods = tuple(
        (owner, name, getattr(owner, name)) for owner, name in _HOOK_TARGETS
    )
    original_trace = runner._LOAD01_RPC_TRACE
    try:
        for owner, name, original in original_methods:
            setattr(owner, name, replacements.get((owner, name), original))
        runner._LOAD01_RPC_TRACE = None
        trace = runner._install_load01_rpc_trace("unit-test")
        assert trace.installation_error is None
        yield trace, original_methods, original_trace
    finally:
        for owner, name, original in reversed(original_methods):
            setattr(owner, name, original)
        runner._LOAD01_RPC_TRACE = original_trace


def _assert_originals_restored(original_methods, original_trace) -> None:
    assert all(getattr(owner, name) is original for owner, name, original in original_methods)
    assert runner._LOAD01_RPC_TRACE is original_trace


def _envelope(message_id: str, cancel_id: str, operation: str = "orders.cancel"):
    return SimpleNamespace(
        header=SimpleNamespace(operation=operation, message_id=message_id),
        payload={"arguments": {"cancel_id": cancel_id}},
    )


def test_hook_correlates_cancel_boundaries_and_preserves_rpc_semantics() -> None:
    client_socket, server_socket = object(), object()
    client_send_result, reply_send_result = object(), object()
    handler_result, request_result = {"handled": True}, object()
    mode: dict[str, Any] = {
        "send_error": None,
        "reply_error": None,
        "receive_error": None,
        "request_error": None,
    }
    calls: list[tuple[Any, ...]] = []
    seen_handlers: list[tuple[Any, Any]] = []
    server = SimpleNamespace()
    receiver = SimpleNamespace(_socket=server_socket)
    request_frames = [b"request-frame"]

    def socket_send(sock, *args, **kwargs):
        calls.append(("send", sock, args, dict(kwargs)))
        if mode["send_error"] is not None:
            raise mode["send_error"]
        return client_send_result

    def socket_send_multipart(sock, *args, **kwargs):
        calls.append(("send_multipart", sock, args, dict(kwargs)))
        if mode["reply_error"] is not None:
            raise mode["reply_error"]
        return reply_send_result

    def receive(receiver_arg, max_frames):
        calls.append(("receive", receiver_arg, max_frames))
        if mode["receive_error"] is not None:
            raise mode["receive_error"]
        return request_frames

    def server_init(server_arg, endpoint, identity, principals, handler, *, credentials):
        calls.append(("server_init", endpoint, identity, principals, credentials))
        server_arg._identity = identity
        server_arg._socket = server_socket
        server_arg._handler = handler

    def request(client, envelope_arg, *, timeout):
        calls.append(("request", envelope_arg, timeout))
        if envelope_arg.header.operation != "orders.cancel":
            zmq.Socket.send(client._socket, b"query-frame", flags=zmq.NOBLOCK, copy=False)
            raise mode["request_error"]
        sent = zmq.Socket.send(client._socket, b"request-frame", flags=zmq.NOBLOCK, copy=False)
        assert sent is client_send_result
        if mode["request_error"] is not None:
            raise mode["request_error"]
        frames = service_v2._BoundedMultipartReceiver.receive(receiver, 1)
        assert frames is request_frames
        handled = server._handler("trusted-principal", envelope_arg)
        assert handled is handler_result
        sent_reply = zmq.Socket.send_multipart(
            server_socket, [b"reply-frame"], flags=zmq.NOBLOCK, copy=False
        )
        assert sent_reply is reply_send_result
        return request_result

    runtime_result, host_result = object(), object()
    maintain_error = RuntimeError("maintenance sentinel")
    maintain_calls: list[tuple[Any, ...]] = []

    def runtime_maintain(owner, *args, **kwargs):
        maintain_calls.append(("runtime", owner, args, kwargs))
        return runtime_result

    def host_maintain(owner, *args, **kwargs):
        maintain_calls.append(("host", owner, args, kwargs))
        return host_result

    def component_maintain(owner, *args, **kwargs):
        maintain_calls.append(("components", owner, args, kwargs))
        raise maintain_error

    replacements = {
        (zmq.Socket, "send"): socket_send,
        (zmq.Socket, "send_multipart"): socket_send_multipart,
        (service_v2._BoundedMultipartReceiver, "receive"): receive,
        (service_v2.ZmqServiceClient, "request"): request,
        (service_v2.ZmqServiceServer, "__init__"): server_init,
        (runner.ServiceClientRuntime, "maintain"): runtime_maintain,
        (runner.ServiceHost, "maintain"): host_maintain,
        (runner.ServiceHost, "_maintain_components"): component_maintain,
    }
    with _installed_trace(replacements) as (trace, originals, original_trace):
        def original_handler(principal, envelope_arg):
            seen_handlers.append((principal, envelope_arg))
            return handler_result

        identity = SimpleNamespace(actor_ref="load01-owner")
        service_v2.ZmqServiceServer.__init__(
            server,
            "inproc://load01-test",
            identity,
            {},
            original_handler,
            credentials=object(),
        )
        client = SimpleNamespace(_socket=client_socket)

        first = _envelope("cancel-message-1", "cancel-order-1")
        assert service_v2.ZmqServiceClient.request(client, first, timeout=5.0) is request_result
        second = _envelope("cancel-message-2", "cancel-order-2")
        assert service_v2.ZmqServiceClient.request(client, second, timeout=5.0) is request_result

        send_again = zmq.Again()
        mode["send_error"] = send_again
        send_failure = _envelope("cancel-send-again", "cancel-send-again")
        with pytest.raises(zmq.Again) as caught_send:
            service_v2.ZmqServiceClient.request(client, send_failure, timeout=5.0)
        assert caught_send.value is send_again
        mode["send_error"] = None

        timeout_error = service_v2.RequestOutcomeUnknownError(
            "cancel-timeout", "response_timeout"
        )
        mode["request_error"] = timeout_error
        timeout_request = _envelope("cancel-timeout", "cancel-timeout")
        with pytest.raises(service_v2.RequestOutcomeUnknownError) as caught_timeout:
            service_v2.ZmqServiceClient.request(client, timeout_request, timeout=5.0)
        assert caught_timeout.value is timeout_error
        mode["request_error"] = None

        receive_again = zmq.Again()
        mode["receive_error"] = receive_again
        receive_failure = _envelope("cancel-receive-again", "cancel-receive-again")
        with pytest.raises(zmq.Again) as caught_receive:
            service_v2.ZmqServiceClient.request(client, receive_failure, timeout=5.0)
        assert caught_receive.value is receive_again
        mode["receive_error"] = None

        reply_again = zmq.Again()
        mode["reply_error"] = reply_again
        reply_failure = _envelope("cancel-reply-again", "cancel-reply-again")
        with pytest.raises(zmq.Again) as caught_reply:
            service_v2.ZmqServiceClient.request(client, reply_failure, timeout=5.0)
        assert caught_reply.value is reply_again
        mode["reply_error"] = None

        non_cancel_error = service_v2.RequestOutcomeUnknownError(
            "query-timeout", "response_timeout"
        )
        mode["request_error"] = non_cancel_error
        event_count = trace.total
        non_cancel = _envelope("query-timeout", "unused", operation="orders.query")
        with pytest.raises(service_v2.RequestOutcomeUnknownError) as caught_non_cancel:
            service_v2.ZmqServiceClient.request(client, non_cancel, timeout=5.0)
        assert caught_non_cancel.value is non_cancel_error
        assert trace.total == event_count
        mode["request_error"] = None

        request_calls = [call for call in calls if call[0] == "request"]
        assert len(request_calls) == 7
        assert all(call[2] == 5.0 for call in request_calls)
        assert calls.count(("send", client_socket, (b"request-frame",), {
            "flags": zmq.NOBLOCK, "copy": False
        })) == 6
        assert calls.count(("send", client_socket, (b"query-frame",), {
            "flags": zmq.NOBLOCK, "copy": False
        })) == 1
        assert calls.count(("send_multipart", server_socket, ([b"reply-frame"],), {
            "flags": zmq.NOBLOCK, "copy": False
        })) == 3
        assert seen_handlers == [
            ("trusted-principal", first),
            ("trusted-principal", second),
            ("trusted-principal", reply_failure),
        ]

        snapshot = trace.snapshot()
        assert snapshot["ring_capacity"] == 256
        assert len(trace.events) <= 256
        grouped: dict[tuple[str, str], list[dict[str, Any]]] = {}
        for event in snapshot["events"]:
            grouped.setdefault((event["message_id"], event["cancel_id"]), []).append(event)
        assert set(grouped) == {
            ("cancel-message-1", "cancel-order-1"),
            ("cancel-message-2", "cancel-order-2"),
            ("cancel-send-again", "cancel-send-again"),
            ("cancel-timeout", "cancel-timeout"),
            ("cancel-receive-again", "cancel-receive-again"),
            ("cancel-reply-again", "cancel-reply-again"),
        }
        assert all(
            (event["message_id"], event["cancel_id"]) == key
            for key, events in grouped.items()
            for event in events
        )
        complete_flow = {
            "client_send", "server_dequeue", "handler_enter", "handler_return",
            "reply_send", "client_outcome",
        }
        for key in (
            ("cancel-message-1", "cancel-order-1"),
            ("cancel-message-2", "cancel-order-2"),
        ):
            assert {event["event"] for event in grouped[key]} == complete_flow
        assert {
            event["event"]: event.get("outcome")
            for event in grouped[("cancel-send-again", "cancel-send-again")]
        } == {"client_send": "again", "client_outcome": "exception"}
        assert {
            event["event"]: event.get("outcome")
            for event in grouped[("cancel-timeout", "cancel-timeout")]
        } == {"client_send": "sent", "client_outcome": "exception"}
        assert {
            event["event"]: event.get("outcome")
            for event in grouped[("cancel-receive-again", "cancel-receive-again")]
        } == {"client_send": "sent", "client_outcome": "exception"}
        assert {
            event["event"]: event.get("outcome")
            for event in grouped[("cancel-reply-again", "cancel-reply-again")]
        }["reply_send"] == "again"

        report = runner._load01_rpc_timing_report({"unit-test": snapshot})
        assert report["ring_capacity_per_process"] == 256
        assert report["event_filter"] == "orders.cancel only"
        reported = {
            (item["message_id"], item["cancel_id"]): item
            for item in report["correlated_requests"]
        }
        assert set(reported) == set(grouped)
        first_durations = reported[("cancel-message-1", "cancel-order-1")]["durations_ms"]
        assert set(first_durations) == {
            "client_send_to_server_dequeue",
            "dequeue_to_handler_including_auth_decode_validation",
            "handler",
            "handler_return_to_reply_send",
            "reply_send_to_client_outcome",
            "client_send_to_client_outcome",
        }
        assert all(value is not None for value in first_durations.values())

        runtime_owner = SimpleNamespace(identity=SimpleNamespace(actor_ref="runtime"))
        host_owner = SimpleNamespace(identity=SimpleNamespace(actor_ref="host"))
        assert runner.ServiceClientRuntime.maintain(runtime_owner, "tick", marker=1) is runtime_result
        assert runner.ServiceHost.maintain(host_owner, "tick", marker=2) is host_result
        with pytest.raises(RuntimeError) as caught_maintain:
            runner.ServiceHost._maintain_components(host_owner, "tick", marker=3)
        assert caught_maintain.value is maintain_error
        assert maintain_calls == [
            ("runtime", runtime_owner, ("tick",), {"marker": 1}),
            ("host", host_owner, ("tick",), {"marker": 2}),
            ("components", host_owner, ("tick",), {"marker": 3}),
        ]
        maintenance = trace.snapshot()["maintenance_durations"]
        assert maintenance["service_runtime.maintain:runtime"]["count"] == 1
        assert maintenance["service_host.maintain:host"]["count"] == 1
        assert maintenance["service_host._maintain_components:host"]["count"] == 1

    _assert_originals_restored(originals, original_trace)


def test_failed_snapshot_is_not_sent_and_invalid_diagnostics_do_not_mask_messages() -> None:
    class BrokenTrace:
        def snapshot(self, limit: int = 32):
            raise RuntimeError("snapshot sentinel")

    class Pipe:
        def __init__(self, messages):
            self.messages = list(messages)
            self.closed = False

        def poll(self, timeout=0):
            return bool(self.messages)

        def recv(self):
            return self.messages.pop(0)

        def send(self, message):
            self.messages.append(message)

        def close(self):
            self.closed = True

    class Process:
        def __init__(self, alive=True):
            self.alive = alive
            self.exitcode = 0 if not alive else None
            self.closed = False

        def is_alive(self):
            return self.alive

        def join(self, timeout=0):
            return None

        def close(self):
            self.closed = True

    invalid = ("rpc-diagnostic", "owner-00", 73, None, "failure")
    failed = ("error", "owner-00", 73, "CHILD_FAILED", {"rpc_timing_at_failure": None})
    closed = ("closed",)
    diagnostic_control = Pipe([])
    snapshot = runner._load01_trace_snapshot_or_none(BrokenTrace())
    assert snapshot is None
    runner._send_load01_rpc_diagnostic(
        diagnostic_control, "owner-00", 73, snapshot, "failure"
    )
    assert diagnostic_control.messages == []
    valid_snapshot = {"pid": 73, "events": []}
    runner._send_load01_rpc_diagnostic(
        diagnostic_control, "owner-00", 73, valid_snapshot, "failure"
    )
    assert diagnostic_control.messages == [
        ("rpc-diagnostic", "owner-00", 73, valid_snapshot, "failure")
    ]

    class BrokenControl:
        def send(self, message):
            raise BrokenPipeError("closed control pipe")

    runner._send_load01_rpc_diagnostic(
        BrokenControl(), "owner-00", 73, valid_snapshot, "emergency"
    )

    record = {
        "pid": 73,
        "name": "owner-00",
        "pipe": Pipe([invalid, closed]),
        "process": Process(alive=True),
    }
    received = runner._collect_expected(
        {73: record}, "closed", 0.1, "unit", {}
    )
    assert received[73] == closed
    assert "rpc_timing_snapshots" not in record

    for drain in ("collect", "progress"):
        record = {
            "pid": 73,
            "name": "owner-00",
            "pipe": Pipe([invalid, failed]),
            "process": Process(alive=True),
        }
        with pytest.raises(runner._Load01ChildFailureError) as caught:
            if drain == "collect":
                runner._collect_expected({73: record}, "closed", 0.1, "unit", {})
            else:
                runner._drain_progress({"owner-00": record}, {})
        assert caught.value.code == "CHILD_FAILED"
        assert "rpc_timing_snapshots" not in record

    for tail in (closed, failed):
        pipe = Pipe([invalid, tail])
        process = Process(alive=False)
        record = {
            "pid": 73,
            "name": "owner-00",
            "kind": "owner",
            "pipe": pipe,
            "process": process,
            "closed": False,
        }
        class Flag:
            is_set = False

            def set(self):
                self.is_set = True

        abort_event, stop_event = Flag(), Flag()
        cleanup_errors: list[str] = []
        runner._emergency_cleanup(
            {"owner-00": record},
            abort_event=abort_event,
            stop_event=stop_event,
            terminated=[],
            cleanup_errors=cleanup_errors,
        )
        assert abort_event.is_set and stop_event.is_set
        assert record["closed"] and process.closed and pipe.closed
        assert cleanup_errors == []
        assert "rpc_timing_snapshots" not in record
