from __future__ import annotations

from dataclasses import replace
from uuid import uuid4

import pytest
from bt_api_base.service_protocol import (
    MAX_WIRE_MESSAGE_BYTES,
    WireEnvelope,
    WireHeader,
    WireIdentity,
    WireMessageKind,
    WireRole,
    WireScope,
)
from bt_api_transport_zmq.service_v2 import service_reply_fits

from bt_api_py.service_config import parse_client_config
from bt_api_py.service_registry import (
    PrincipalGrant,
    ResourceGrant,
    ServiceCallContext,
    ServiceCapability,
    ServiceRegistry,
    ServiceResultTooLargeError,
)
from bt_api_py.service_runtime import (
    ServiceCallOutcomeUnknownError,
    ServiceClientRuntime,
    ServiceRuntimeError,
)

_CLIENT = WireIdentity(WireRole.STRATEGY_CLIENT, "budget-client", "strategy-a")
_OTHER = WireIdentity(WireRole.STRATEGY_CLIENT, "other-client", "strategy-a")
_SERVICE = WireIdentity(WireRole.SERVICE, "budget-service", "gateway-a")
_SCOPE_TEMPLATE = WireScope(
    "paper:reply-budget",
    account_ref="account-a",
    strategy_ref="strategy-a",
)
_OPERATIONS = frozenset({"account.snapshot", "account.head", "account.read_after"})
_WRITE_OPERATIONS = frozenset({"orders.submit", "orders.cancel"})
_SERVER_LIMIT = 32 * 1024
_CLIENT_LIMIT = 8 * 1024


def _request(operation, payload, *, identity=_CLIENT, scope=None):
    return WireEnvelope(
        WireHeader(
            protocol_version=2,
            kind=WireMessageKind.REQUEST,
            message_id=uuid4().hex,
            correlation_id=None,
            identity=identity,
            scope=scope or replace(_SCOPE_TEMPLATE, run_id="run-a"),
            operation=operation,
            schema_id="sdk.service.call",
            schema_version=1,
            sent_at_utc="2026-10-14T00:00:00Z",
        ),
        payload,
    )


class _LocalTransport:
    def __init__(self, registry, *, principal=_CLIENT.principal_ref):
        self.registry = registry
        self.principal = principal
        self.requests = []

    def request(self, request, *, timeout):
        del timeout
        self.requests.append(request)
        payload = self.registry.handle(self.principal, request)
        header = replace(
            request.header,
            kind=WireMessageKind.REPLY,
            message_id=uuid4().hex,
            correlation_id=request.header.message_id,
            identity=_SERVICE,
            sent_at_utc="2026-10-14T00:00:01Z",
        )
        return WireEnvelope(header, payload)

    def close(self):
        pass


def _registry(path, *, reply_size_checker=None):
    registry = ServiceRegistry(
        path,
        service_id="service-a",
        capabilities=(ServiceCapability("gateway-a", "service.account_read", _OPERATIONS),),
        grants={
            _CLIENT.principal_ref: (
                PrincipalGrant(
                    _CLIENT,
                    (
                        ResourceGrant(
                            "gateway-a",
                            "service.account_read",
                            "account-grant-a",
                            _SCOPE_TEMPLATE,
                            _OPERATIONS,
                        ),
                    ),
                ),
            )
        },
        policy_version="budget-policy-v1",
        requirements_version="budget-requirements-v1",
        reply_size_checker=reply_size_checker,
    )
    return registry


def _runtime(registry, *, client_limit=_SERVER_LIMIT):
    config = parse_client_config(
        {
            "client_config_schema_version": 1,
            "process_role": "strategy_client",
            "registration": {"strategy_ref": "strategy-a"},
            "clients": {
                "gateway": {
                    "config_schema_version": 1,
                    "settings": {
                        "bindings": [
                            {
                                "mount": "store.account",
                                "service_id": "service-a",
                                "instance_id": "gateway-a",
                                "scope_ref": "account-grant-a",
                                "account_ref": "account-a",
                                "transport_ref": "budget-rpc",
                            }
                        ],
                        "options": {"request_timeout_ms": 1000},
                    },
                }
            },
            "communication": {
                "config_schema_version": 1,
                "profiles": {
                    "budget-rpc": {
                        "backend": "zmq",
                        "config_schema_version": 1,
                        "endpoints": {"rpc_connect": "tcp://127.0.0.1:7601"},
                        "security_profile_ref": "synthetic-budget-keys",
                        "options": {"max_message_bytes": client_limit},
                    }
                },
            },
        }
    )
    transport = _LocalTransport(registry)
    runtime = ServiceClientRuntime(
        config,
        identity=_CLIENT,
        scope_resolver=lambda _scope_ref: _SCOPE_TEMPLATE,
        peer_resolver=lambda _service_id: _SERVICE,
        transport_factory=lambda _profile, _peer: transport,
        run_id="run-a",
        requested_operations={"store.account": _OPERATIONS},
    )
    runtime.start()
    return runtime, transport


def _writer_registry(path, *, reply_size_checker):
    registry = ServiceRegistry(
        path,
        service_id="service-a",
        capabilities=(
            ServiceCapability(
                "execution-a",
                "service.order_entry",
                _WRITE_OPERATIONS,
                write_operations=_WRITE_OPERATIONS,
            ),
        ),
        grants={
            _CLIENT.principal_ref: (
                PrincipalGrant(
                    _CLIENT,
                    (
                        ResourceGrant(
                            "execution-a",
                            "service.order_entry",
                            "order-grant-a",
                            _SCOPE_TEMPLATE,
                            _WRITE_OPERATIONS,
                        ),
                    ),
                ),
            )
        },
        policy_version="budget-policy-v1",
        requirements_version="budget-requirements-v1",
        reply_size_checker=reply_size_checker,
    )
    return registry


def _writer_runtime(registry):
    config = parse_client_config(
        {
            "client_config_schema_version": 1,
            "process_role": "strategy_client",
            "registration": {"strategy_ref": "strategy-a"},
            "clients": {
                "execution": {
                    "config_schema_version": 1,
                    "settings": {
                        "bindings": [
                            {
                                "mount": "broker.orders",
                                "service_id": "service-a",
                                "instance_id": "execution-a",
                                "scope_ref": "order-grant-a",
                                "account_ref": "account-a",
                                "transport_ref": "budget-rpc",
                            }
                        ],
                        "options": {"request_timeout_ms": 1000},
                    },
                }
            },
            "communication": {
                "config_schema_version": 1,
                "profiles": {
                    "budget-rpc": {
                        "backend": "zmq",
                        "config_schema_version": 1,
                        "endpoints": {"rpc_connect": "tcp://127.0.0.1:7601"},
                        "security_profile_ref": "synthetic-budget-keys",
                        "options": {"max_message_bytes": _SERVER_LIMIT},
                    }
                },
            },
        }
    )
    transport = _LocalTransport(registry)
    runtime = ServiceClientRuntime(
        config,
        identity=_CLIENT,
        scope_resolver=lambda _scope_ref: _SCOPE_TEMPLATE,
        peer_resolver=lambda _service_id: _SERVICE,
        transport_factory=lambda _profile, _peer: transport,
        run_id="run-a",
    )
    runtime.start()
    return runtime, transport


def test_runtime_hint_uses_smaller_cap_and_context_checks_complete_framed_reply(tmp_path):
    checks = []
    handler_contexts: list[ServiceCallContext] = []

    def checker(request, payload, hint):
        effective_limit = _SERVER_LIMIT if hint is None else min(_SERVER_LIMIT, hint)
        checks.append((request, dict(payload), effective_limit))
        return service_reply_fits(
            _SERVICE,
            request,
            payload,
            max_message_bytes=effective_limit,
        )

    registry = _registry(tmp_path / "registry.sqlite", reply_size_checker=checker)

    def read_handler(context, arguments):
        handler_contexts.append(context)
        large_fits = context.result_fits({"value": "x" * 10_000})
        assert arguments == {"probe": "large"}
        return {"large_fits": large_fits}

    registry.register_handler("gateway-a", "account.snapshot", read_handler)
    runtime, transport = _runtime(registry)
    try:
        reply = runtime.call(
            "store.account",
            "account.snapshot",
            {"probe": "large"},
            account_ref="account-a",
            max_reply_message_bytes=_CLIENT_LIMIT,
        )
        assert reply == {"large_fits": False}
        assert len(handler_contexts) == 1
        assert handler_contexts[0].result_fits is not None

        call_request = next(
            request
            for request in transport.requests
            if request.header.operation == "account.snapshot"
        )
        assert call_request.payload["max_reply_message_bytes"] == _CLIENT_LIMIT
        assert call_request.payload["arguments"] == {"probe": "large"}

        large_check = next(payload for request, payload, _ in checks if request is call_request)
        assert set(large_check) == {"ok", "code", "result"}
        assert large_check["ok"] is True and large_check["code"] == "OK"
        assert large_check["result"] == {"value": "x" * 10_000}
        assert (
            service_reply_fits(
                _SERVICE,
                call_request,
                large_check,
                max_message_bytes=_SERVER_LIMIT,
            )
            is True
        )
        assert (
            service_reply_fits(
                _SERVICE,
                call_request,
                large_check,
                max_message_bytes=_CLIENT_LIMIT,
            )
            is False
        )
        assert [limit for request, _payload, limit in checks if request is call_request] == [
            _CLIENT_LIMIT,
            _CLIENT_LIMIT,
        ]

        clamped = runtime.call(
            "store.account",
            "account.snapshot",
            {"probe": "large"},
            account_ref="account-a",
            max_reply_message_bytes=MAX_WIRE_MESSAGE_BYTES,
        )
        assert clamped == {"large_fits": True}
        clamped_request = transport.requests[-1]
        assert clamped_request.payload["max_reply_message_bytes"] == _SERVER_LIMIT

        old_call = runtime.call(
            "store.account",
            "account.snapshot",
            {"probe": "large"},
            account_ref="account-a",
        )
        assert old_call == {"large_fits": True}
        old_request = transport.requests[-1]
        assert "max_reply_message_bytes" not in old_request.payload
        assert all(
            limit == _SERVER_LIMIT for request, _payload, limit in checks if request is old_request
        )
    finally:
        runtime.close()
        registry.close()


def test_tracked_read_advertises_bound_profile_cap_by_default(tmp_path):
    checks = []
    registry = _registry(
        tmp_path / "registry.sqlite",
        reply_size_checker=lambda request, payload, limit: (
            checks.append((request, dict(payload), limit)) or True
        ),
    )
    registry.register_handler("gateway-a", "account.read_after", lambda _context, _args: {})
    runtime, transport = _runtime(registry)
    try:
        binding = runtime.account_binding_view(
            "store.account", "account-a", required_operations=frozenset({"account.read_after"})
        )
        assert binding.max_message_bytes == _SERVER_LIMIT
        runtime.call(
            "store.account",
            "account.read_after",
            {"cursor": None, "limit": 1, "track_delivery": True},
            account_ref="account-a",
        )
        request = transport.requests[-1]
        assert request.payload["max_reply_message_bytes"] == _SERVER_LIMIT
        assert request.payload["arguments"]["track_delivery"] is True
        assert checks[-1][0] is request
        assert checks[-1][2] == _SERVER_LIMIT
    finally:
        runtime.close()
        registry.close()


def test_unauthorized_and_invalid_reply_limits_never_reach_handler_or_checker(tmp_path):
    checks = []
    calls = []

    def checker(request, payload, hint):
        checks.append((request, payload, hint))
        return True

    registry = _registry(tmp_path / "registry.sqlite", reply_size_checker=checker)
    registry.register_handler(
        "gateway-a",
        "account.snapshot",
        lambda context, arguments: calls.append((context, arguments)) or {},
    )
    runtime, transport = _runtime(registry)
    try:
        runtime.call(
            "store.account",
            "account.snapshot",
            {},
            account_ref="account-a",
        )
        assert len(calls) == 1
        calls.clear()
        checks.clear()

        registration = runtime.bindings[0]
        unauthorized = _request(
            "account.snapshot",
            {
                "registration_id": registration.registration_id,
                "service_epoch": registration.service_epoch,
                "instance_id": registration.instance_id,
                "arguments": {},
                "max_reply_message_bytes": _CLIENT_LIMIT,
            },
            identity=_OTHER,
            scope=registration.scope,
        )
        denied = registry.handle(_CLIENT.principal_ref, unauthorized)
        assert denied["code"] == "DENIED"
        assert calls == []
        assert checks == []

        request_count = len(transport.requests)
        for invalid in (True, 0, MAX_WIRE_MESSAGE_BYTES + 1, "8192"):
            with pytest.raises(ServiceRuntimeError, match="INVALID_REPLY_MESSAGE_LIMIT"):
                runtime.call(
                    "store.account",
                    "account.snapshot",
                    {},
                    account_ref="account-a",
                    max_reply_message_bytes=invalid,
                )
        assert len(transport.requests) == request_count
        assert calls == []
        assert checks == []
    finally:
        runtime.close()
        registry.close()


def test_reply_limit_without_exact_checker_fails_before_handler(tmp_path):
    calls = []
    registry = _registry(tmp_path / "registry.sqlite")
    registry.register_handler(
        "gateway-a",
        "account.snapshot",
        lambda _context, _arguments: calls.append(True) or {},
    )
    runtime, _transport = _runtime(registry)
    try:
        # A standalone registry rejects hints rather than claiming it can
        # prove the transport's sealed-frame budget.
        with pytest.raises(ServiceRuntimeError, match="CAPABILITY_UNAVAILABLE"):
            runtime.call(
                "store.account",
                "account.snapshot",
                {},
                account_ref="account-a",
                max_reply_message_bytes=_CLIENT_LIMIT,
            )
        assert calls == []
    finally:
        runtime.close()
        registry.close()


def test_typed_oversize_maps_finitely_but_unknown_error_codes_stay_generic(tmp_path):
    registry = _registry(tmp_path / "registry.sqlite")

    def typed_oversize(_context, _arguments):
        raise ServiceResultTooLargeError()

    class UntrustedError(RuntimeError):
        code = "REPLY_TOO_LARGE"

    class DerivedOversizeError(ServiceResultTooLargeError):
        pass

    def unknown_error(_context, _arguments):
        raise UntrustedError("ignored external text")

    def derived_oversize(_context, _arguments):
        raise DerivedOversizeError()

    registry.register_handler("gateway-a", "account.snapshot", unknown_error)
    registry.register_handler("gateway-a", "account.head", derived_oversize)
    registry.register_handler("gateway-a", "account.read_after", typed_oversize)
    runtime, _transport = _runtime(registry)
    try:
        registration = runtime.bindings[0]

        def invoke(operation):
            return registry.handle(
                _CLIENT.principal_ref,
                _request(
                    operation,
                    {
                        "registration_id": registration.registration_id,
                        "service_epoch": registration.service_epoch,
                        "instance_id": registration.instance_id,
                        "arguments": {},
                    },
                    scope=registration.scope,
                ),
            )

        assert invoke("account.snapshot")["code"] == "HANDLER_FAILED"
        assert invoke("account.head")["code"] == "HANDLER_FAILED"
        assert invoke("account.read_after")["code"] == "REPLY_TOO_LARGE"
    finally:
        runtime.close()
        registry.close()


def test_write_reply_failures_are_unknown_and_are_never_retried(tmp_path):
    effects = []

    def checker(request, _payload, _hint):
        return request.header.operation != "orders.submit"

    registry = _writer_registry(
        tmp_path / "registry.sqlite",
        reply_size_checker=checker,
    )

    def submit_handler(_context, _arguments):
        effects.append("submit committed")
        return {"receipt": "x" * 10_000}

    def cancel_handler(_context, _arguments):
        effects.append("cancel committed")
        raise ServiceResultTooLargeError()

    registry.register_handler("execution-a", "orders.submit", submit_handler)
    registry.register_handler("execution-a", "orders.cancel", cancel_handler)
    runtime, transport = _writer_runtime(registry)
    try:
        for operation in ("orders.submit", "orders.cancel"):
            before = len(transport.requests)
            with pytest.raises(ServiceCallOutcomeUnknownError) as raised:
                runtime.call(
                    "broker.orders",
                    operation,
                    {"intent_id": f"intent-{operation}"},
                    account_ref="account-a",
                )
            assert raised.value.operation == operation
            assert len(transport.requests) == before + 1
            assert transport.requests[-1].header.operation == operation
        assert effects == ["submit committed", "cancel committed"]
    finally:
        runtime.close()
        registry.close()
