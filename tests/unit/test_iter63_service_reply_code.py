"""The runtime names a rejection only when it is the service's own reply to the request.

``ServiceRuntimeError.service_reply_code`` lets a caller (the Gateway Web adapter) tell a
refusal the service sent for this request from a local, timeout or lost-reply failure.
Synthetic registry and in-process transport; no network or account.
"""

from __future__ import annotations

from dataclasses import replace

import pytest
from bt_api_base.service_protocol import WireEnvelope, WireMessageKind
from test_service_runtime import PEER, LocalTransport, _config, _registry, _runtime

from bt_api_py.service_runtime import ServiceCallOutcomeUnknownError, ServiceRuntimeError


def _started(tmp_path, *, handler=None):
    registry = _registry(tmp_path / "registry.sqlite", [], handler=handler)
    transport = LocalTransport(registry)
    runtime = _runtime(_config(), lambda *_: transport)
    runtime.start()
    return registry, transport, runtime


def test_rejection_reply_of_the_service_carries_its_code(tmp_path):
    registry, _, runtime = _started(tmp_path)
    try:
        assert registry.revoke_principal("strategy-principal") is True
        with pytest.raises(ServiceRuntimeError) as error:
            runtime.call("store.account", "account.snapshot", {})
        assert type(error.value) is ServiceRuntimeError
        assert (error.value.code, error.value.service_reply_code) == ("DENIED", "DENIED")
    finally:
        runtime.close()
        registry.close()


def test_local_failures_carry_no_reply_code(tmp_path):
    registry, transport, runtime = _started(tmp_path)
    try:
        # Refused by the runtime itself before anything is sent.
        with pytest.raises(ServiceRuntimeError) as local:
            runtime.call("store.account", "account.head", {})
        assert local.value.code == "AMBIGUOUS_OR_MISSING_ROUTE"
        assert local.value.service_reply_code is None
        # A constructed error never claims to be a reply.
        assert ServiceRuntimeError("DENIED").service_reply_code is None
        # The transport loses the reply: no error object claims a service answer.
        transport.fail_business = True
        with pytest.raises(TimeoutError) as lost:
            runtime.call("store.account", "account.snapshot", {})
        assert getattr(lost.value, "service_reply_code", None) is None
    finally:
        runtime.close()
        registry.close()


def test_failed_handler_is_an_unknown_outcome_without_reply_code(tmp_path):
    def failing(_context, _arguments):
        raise RuntimeError("synthetic failure after a change")

    registry, _, runtime = _started(tmp_path, handler=failing)
    try:
        with pytest.raises(ServiceCallOutcomeUnknownError) as error:
            runtime.call("store.account", "account.snapshot", {})
        assert error.value.code == "BUSINESS_OUTCOME_UNKNOWN"
        assert error.value.service_reply_code is None
    finally:
        runtime.close()
        registry.close()


def test_control_rejection_with_an_unknown_code_names_no_reply_code(tmp_path):
    # The runtime reports such a reply as REMOTE_REQUEST_REJECTED: the reply code is not its code.
    registry = _registry(tmp_path / "registry.sqlite", [])

    class UnknownCode(LocalTransport):
        def request(self, request, *, timeout):
            if request.header.operation == "service.register":
                self.requests.append(request)
                return WireEnvelope(
                    replace(request.header, kind=WireMessageKind.REPLY, message_id="reply-r",
                            correlation_id=request.header.message_id, identity=PEER),
                    {"ok": False, "code": "SOMETHING_ELSE", "result": {}},
                )
            return super().request(request, timeout=timeout)

    runtime = _runtime(_config(), lambda *_: UnknownCode(registry))
    try:
        with pytest.raises(ServiceRuntimeError) as error:
            runtime.start()
        assert error.value.code == "REMOTE_REQUEST_REJECTED"
        assert error.value.service_reply_code is None
    finally:
        runtime.close()
        registry.close()


class ForeignReply(LocalTransport):
    """Answers a rejection that is correlated to another request."""

    def request(self, request, *, timeout):
        if request.header.operation != "account.snapshot":
            return super().request(request, timeout=timeout)
        self.requests.append(request)
        return WireEnvelope(
            replace(request.header, kind=WireMessageKind.REPLY, message_id="reply-x",
                    correlation_id="another-request", identity=PEER),
            {"ok": False, "code": "DENIED", "result": {}},
        )


def test_rejection_correlated_to_another_request_is_not_a_reply_code(tmp_path):
    registry = _registry(tmp_path / "registry.sqlite", [])
    transport = ForeignReply(registry)
    runtime = _runtime(_config(), lambda *_: transport)
    try:
        runtime.start()
        with pytest.raises(Exception) as error:
            runtime.call("store.account", "account.snapshot", {})
        assert getattr(error.value, "service_reply_code", None) is None
    finally:
        runtime.close()
        registry.close()
