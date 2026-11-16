"""Round-trip of the frozen monitor.heartbeat request/ACK across both packages.

The Gateway side builds the request and validates the ACK with
``bt_api_gateway.monitor_heartbeat_wire``; the passive handler is the real
``bt_api_monitor`` service. No transport, network, Gateway or account is used.
"""
from __future__ import annotations

import pytest
from bt_api_base.service_protocol import WireIdentity, WireRole, WireScope
from bt_api_gateway.monitor_heartbeat_wire import (
    MONITOR_HEARTBEAT_ACK_FIELDS,
    MONITOR_HEARTBEAT_ERROR_CODES,
    build_monitor_heartbeat_request,
    monitor_heartbeat_ack_matches,
)
from bt_api_monitor.service_gateway_heartbeat import (
    GatewayHeartbeatError,
    GatewayHeartbeatService,
)

MONITOR = WireIdentity(WireRole.SERVICE, "monitor-p", "monitor-a")
OTHER_MONITOR = WireIdentity(WireRole.SERVICE, "other-p", "other-a")
SCOPE = WireScope("domain", account_ref="account")
EPOCH = "registration-1"


def request():
    return build_monitor_heartbeat_request(
        relay_epoch=EPOCH, heartbeat_id="a" * 32, sent_at_ns=123456789)


def service(current=True, *, validator=True):
    calls = []

    def validate(identity, scope, epoch):
        calls.append((identity, scope, epoch))
        return current

    value = GatewayHeartbeatService(
        monitor_principal="monitor-p",
        targets=frozenset({(MONITOR, SCOPE)}),
        validate_current_epoch=validate if validator else None,
    )
    return value, calls


def call(component, *, identity=MONITOR, arguments=None, principal_ref="monitor-p", epoch=EPOCH):
    return component.handle(
        "monitor.heartbeat", principal_ref=principal_ref, identity=identity, scope=SCOPE,
        registration_id=epoch, arguments=request() if arguments is None else arguments)


def test_the_real_handler_answers_exactly_the_frozen_ack():
    component, calls = service()
    sent = request()
    ack = call(component)
    assert set(ack) == MONITOR_HEARTBEAT_ACK_FIELDS
    assert ack == {**sent, "monitor_principal": "monitor-p"}
    assert monitor_heartbeat_ack_matches(sent, ack, "monitor-p") is True
    assert calls == [(MONITOR, SCOPE, EPOCH)]


@pytest.mark.parametrize("arguments", [
    {**request(), "extra": 1},
    {key: value for key, value in request().items() if key != "heartbeat_id"},
    {**request(), "schema_version": 2},
    {**request(), "sent_at_ns": 0},
])
def test_the_real_handler_refuses_a_non_exact_request_with_a_finite_code(arguments):
    component, _ = service()
    with pytest.raises(GatewayHeartbeatError) as refused:
        call(component, arguments=arguments)
    assert refused.value.code == "INVALID_HEARTBEAT"
    assert refused.value.code in MONITOR_HEARTBEAT_ERROR_CODES


def test_an_untrusted_relay_wrong_epoch_or_stale_epoch_never_acks():
    component, _ = service()
    with pytest.raises(GatewayHeartbeatError) as denied:
        call(component, identity=OTHER_MONITOR, principal_ref="other-p")
    assert denied.value.code == "UNAUTHORIZED_RELAY"

    with pytest.raises(GatewayHeartbeatError) as wrong_epoch:
        call(component, epoch="registration-2")
    assert wrong_epoch.value.code == "REGISTRATION_EPOCH_MISMATCH"

    stale, _ = service(current=False)
    with pytest.raises(GatewayHeartbeatError) as unverified:
        call(stale)
    assert unverified.value.code == "STALE_OR_UNVERIFIED_EPOCH"

    unbound, _ = service(validator=False)
    with pytest.raises(GatewayHeartbeatError) as no_validator:
        call(unbound)
    assert no_validator.value.code == "EPOCH_VALIDATOR_UNBOUND"

    for error in (denied, wrong_epoch, unverified, no_validator):
        assert error.value.code in MONITOR_HEARTBEAT_ERROR_CODES
