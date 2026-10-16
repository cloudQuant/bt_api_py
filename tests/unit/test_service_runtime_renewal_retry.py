"""Required-lease renewal timeouts on the SDK runtime maintenance pump.

Historical shape (LOAD01 100-strategy/30-min acceptance, r3): during the
concurrent startup of ~100 strategy processes with ~20 market clients each,
one boundary maintenance pass issued a required-lease renewal whose reply
missed the renewal budget entirely (slow hub). The renewal outcome was
indeterminate while the lease still had ~16-21 s left, yet the runtime failed
the whole session on that first timeout. These tests pin the corrected
contract:

* a transient renewal timeout (transport timeout) keeps the session alive
  while the current lease is unexpired and is retried by the next pump;
* an explicit hub rejection still fails closed with the hub's error code;
* a lease that never renews still fails at lease expiry (protection intact);
* the healthy path is unchanged (no extra control RPCs).
"""

from __future__ import annotations

from contextlib import suppress
from dataclasses import replace

import pytest
from bt_api_base.service_protocol import (
    WireEnvelope,
    WireIdentity,
    WireMessageKind,
    WireRole,
    WireScope,
)

from bt_api_py.service_config import parse_client_config
from bt_api_py.service_registry import (
    PrincipalGrant,
    ResourceGrant,
    ServiceCapability,
    ServiceRegistry,
)
from bt_api_py.service_runtime import (
    ServiceClientRuntime,
    ServiceRuntimeError,
)

CLIENT = WireIdentity(WireRole.STRATEGY_CLIENT, "strategy-principal", "strategy-s")
PEER = WireIdentity(WireRole.SERVICE, "service-principal", "account-service")
SCOPE = WireScope("paper:demo", account_ref="account-a", strategy_ref="strategy-s")
CAPABILITY = ServiceCapability("gateway-a", "service.account_read", frozenset({"account.snapshot"}))


class ManualClock:
    def __init__(self, value=0.0):
        self.value = float(value)

    def __call__(self):
        return self.value

    def advance(self, seconds):
        self.value += seconds


def _config(*, timeout_ms=1000):
    raw = {
        "client_config_schema_version": 1,
        "process_role": "strategy_client",
        "registration": {"strategy_ref": "strategy-s"},
        "clients": {
            "gateway": {
                "config_schema_version": 1,
                "settings": {
                    "bindings": [
                        {
                            "mount": "store.account",
                            "service_id": "account-service",
                            "instance_id": "gateway-a",
                            "scope_ref": "account-a",
                            "account_ref": "account-a",
                            "transport_ref": "account-zmq",
                        }
                    ],
                    "options": {"request_timeout_ms": timeout_ms},
                },
            }
        },
        "communication": {
            "config_schema_version": 1,
            "profiles": {
                "account-zmq": {
                    "backend": "zmq",
                    "config_schema_version": 1,
                    "endpoints": {"rpc_connect": "tcp://127.0.0.1:7601"},
                    "security_profile_ref": "client-keys",
                }
            },
        },
    }
    return parse_client_config(raw)


def _registry(path, calls, *, lease_seconds=30, clock=None):
    registry = ServiceRegistry(
        path,
        service_id="account-service",
        capabilities=(CAPABILITY,),
        grants={
            CLIENT.principal_ref: (
                PrincipalGrant(
                    CLIENT,
                    (
                        ResourceGrant(
                            "gateway-a",
                            "service.account_read",
                            "account-a",
                            SCOPE,
                            CAPABILITY.operations,
                        ),
                    ),
                ),
            )
        },
        policy_version="access-v1",
        requirements_version="readiness-v1",
        lease_seconds=lease_seconds,
        **({"clock": clock} if clock is not None else {}),
    )

    def snapshot(context, arguments):
        calls.append((context, arguments))
        return {"account_ref": context.scope.account_ref, "balance": 125.0, "source": "synthetic"}

    registry.register_handler("gateway-a", "account.snapshot", snapshot)
    return registry


class LocalTransport:
    def __init__(self, registry, principal_ref=CLIENT.principal_ref):
        self.registry = registry
        self.principal_ref = principal_ref
        self.requests = []
        self.timeouts = []
        self.closed = False

    def request(self, request, *, timeout):
        self.requests.append(request)
        self.timeouts.append(timeout)
        result = self.registry.handle(self.principal_ref, request)
        return WireEnvelope(
            replace(
                request.header,
                kind=WireMessageKind.REPLY,
                message_id="reply-" + request.header.message_id,
                correlation_id=request.header.message_id,
                identity=PEER,
            ),
            result,
        )

    def close(self):
        self.closed = True


def _runtime(config, transport, *, monotonic_clock):
    return ServiceClientRuntime(
        config,
        identity=CLIENT,
        scope_resolver=lambda _: SCOPE,
        peer_resolver=lambda _: PEER,
        transport_factory=lambda *_: transport,
        run_id="run-one",
        monotonic_clock=monotonic_clock,
    )


def _renew_requests(transport):
    return [request for request in transport.requests if request.header.operation == "service.renew"]


def test_transient_renewal_timeout_is_retried_while_lease_valid(tmp_path):
    calls = []
    registry_clock = ManualClock(500)
    registry = _registry(tmp_path / "retry.sqlite", calls, clock=registry_clock)
    monotonic = ManualClock(1000)

    class FlakyRenewTransport(LocalTransport):
        def __init__(self, registry):
            super().__init__(registry)
            self.renew_attempts = 0
            self.renew_timeouts = []

        def request(self, request, *, timeout):
            if request.header.operation == "service.renew":
                self.renew_attempts += 1
                self.renew_timeouts.append(timeout)
                if self.renew_attempts == 1:
                    # The slow hub's reply misses the renewal budget: the
                    # outcome is indeterminate, not a rejection.
                    self.requests.append(request)
                    self.timeouts.append(timeout)
                    raise TimeoutError("renewal reply lost to a slow hub")
            return super().request(request, timeout=timeout)

    transport = FlakyRenewTransport(registry)
    runtime = _runtime(_config(), transport, monotonic_clock=monotonic)
    try:
        binding = runtime.start().bindings[0]
        original_deadline = binding.lease_deadline
        registry_clock.advance(10)
        monotonic.advance(10.05)

        # First overdue pump: the renewal times out. The lease is still
        # unexpired, so the session must stay registered and healthy with the
        # binding left due for an immediate retry.
        deferred = runtime.maintain()
        assert transport.renew_attempts == 1
        assert deferred.degraded_count == 0
        assert deferred.bindings[0].lease_deadline == original_deadline
        assert deferred.bindings[0].remote_lease_inactive is False
        assert runtime.state == "registered"
        assert runtime.healthy is True
        assert runtime.next_maintenance_delay() == 0.0

        # The next pump retries and the hub answers in time: the lease is
        # extended without restarting the registration session.
        renewed = runtime.maintain().bindings[0]
        assert transport.renew_attempts == 2
        assert renewed.lease_deadline > original_deadline
        assert runtime.state == "registered"
        assert runtime.healthy is True
        assert runtime.next_maintenance_delay() > 0
        assert all(timeout <= 1.0 for timeout in transport.renew_timeouts)

        # Business calls continue to work after the recovery.
        assert (
            runtime.call("store.account", "account.snapshot", {}, account_ref="account-a")[
                "balance"
            ]
            == 125
        )
    finally:
        runtime.close()
        registry.close()
        assert transport.closed


def test_explicit_renewal_rejection_still_fails_closed(tmp_path):
    calls = []
    registry = _registry(tmp_path / "reject.sqlite", calls)
    monotonic = ManualClock(2000)

    class RejectRenewTransport(LocalTransport):
        def request(self, request, *, timeout):
            reply = super().request(request, timeout=timeout)
            if request.header.operation == "service.renew":
                return WireEnvelope(reply.header, {"ok": False, "code": "STALE_EPOCH"})
            return reply

    transport = RejectRenewTransport(registry)
    runtime = _runtime(_config(), transport, monotonic_clock=monotonic)
    try:
        runtime.start()
        monotonic.advance(10.05)
        # A definitive hub rejection is not an indeterminate timeout: the
        # session must still fail closed with the hub's own error code.
        with pytest.raises(ServiceRuntimeError, match="STALE_EPOCH"):
            runtime.maintain()
        assert runtime.state == "failed"
        assert runtime.healthy is False
        assert runtime.bindings[0].remote_lease_inactive is True
        with pytest.raises(ServiceRuntimeError, match="SESSION_NOT_REGISTERED"):
            runtime.call("store.account", "account.snapshot", {}, account_ref="account-a")
    finally:
        with suppress(ServiceRuntimeError):
            runtime.close()
        registry.close()


def test_lease_that_never_renews_still_fails_at_expiry(tmp_path):
    calls = []
    registry = _registry(tmp_path / "expiry.sqlite", calls)
    monotonic = ManualClock(3000)

    class DeadRenewTransport(LocalTransport):
        def request(self, request, *, timeout):
            if request.header.operation == "service.renew":
                self.requests.append(request)
                self.timeouts.append(timeout)
                raise TimeoutError("renewal reply lost")
            return super().request(request, timeout=timeout)

    transport = DeadRenewTransport(registry)
    runtime = _runtime(_config(), transport, monotonic_clock=monotonic)
    try:
        runtime.start()
        # Overdue pumps defer while the lease remains valid...
        monotonic.advance(10.05)
        runtime.maintain()
        assert runtime.state == "registered"
        assert runtime.healthy is True
        monotonic.advance(19.0)
        runtime.maintain()
        assert runtime.state == "registered"
        assert runtime.healthy is True
        assert len(_renew_requests(transport)) == 2
        # ...but at the lease deadline the hard guard still fires: no
        # successful renewal ever means REQUIRED_LEASE_EXPIRED, exactly as
        # before the retry tolerance was added.
        monotonic.advance(1.0)
        with pytest.raises(ServiceRuntimeError, match="REQUIRED_LEASE_EXPIRED"):
            runtime.maintain()
        assert runtime.state == "failed"
        assert runtime.healthy is False
        with pytest.raises(ServiceRuntimeError, match="SESSION_NOT_REGISTERED"):
            runtime.call("store.account", "account.snapshot", {}, account_ref="account-a")
        assert len(_renew_requests(transport)) == 2
    finally:
        with suppress(ServiceRuntimeError):
            runtime.close()
        registry.close()


def test_healthy_renewal_path_issues_no_extra_requests(tmp_path):
    calls = []
    registry = _registry(tmp_path / "steady.sqlite", calls)
    monotonic = ManualClock(4000)

    transport = LocalTransport(registry)
    runtime = _runtime(_config(), transport, monotonic_clock=monotonic)
    try:
        runtime.start()
        assert len(_renew_requests(transport)) == 0

        # Not yet due: maintenance stays silent.
        monotonic.advance(9.9)
        runtime.maintain()
        assert len(_renew_requests(transport)) == 0

        # Due: exactly one renewal extends the lease.
        monotonic.advance(0.2)
        renewed = runtime.maintain().bindings[0]
        assert len(_renew_requests(transport)) == 1
        assert renewed.lease_deadline > 4030.0

        # Immediately after a success nothing is due again: still one renewal.
        runtime.maintain()
        assert len(_renew_requests(transport)) == 1

        # The next due cycle needs exactly one more renewal.
        monotonic.advance(10.0)
        runtime.maintain()
        assert len(_renew_requests(transport)) == 2

        operations = [request.header.operation for request in transport.requests]
        assert operations.count("service.renew") == 2
        assert operations.count("service.register") == 1
        assert calls == []
    finally:
        runtime.close()
        registry.close()
