"""Synthetic market mutation/claim fences; no account or external network."""

import threading
import time
from itertools import count
from datetime import datetime, timezone

import pytest

from bt_api_base.service_protocol import WireIdentity, WireRole, WireScope
from bt_api_gateway.market_hub import (
    GatewayMarketHub,
    GatewayMarketHubError,
    GatewayMarketRegistration,
    GatewayMarketService,
    GatewayMarketServiceError,
)
from bt_api_py.gateway_dispatch_claim import (
    DispatchClaimError,
    DispatchCommandBinding,
    GatewayDispatchClaimCoordinator,
)
from bt_api_py.gateway_market_dispatch_gate import (
    GatewayMarketDispatchError,
    GatewayMarketDispatchGate,
)
from tests.test_iter62_clock_verification import KEY, _unqualified_event


class _Provider:
    def subscribe(self, _key, *, generation):
        return object()

    def unsubscribe(self, _handle):
        return True

    def read_market_login_clock(self, _handle, **_kwargs):
        return {"synthetic": True}


def _coordinator():
    # This test-only constructor is not a production coverage capability.
    return GatewayDispatchClaimCoordinator._for_test("account", "generation")


def _all_ready(coordinator):
    for name in ("registry", "market", "parent_facts", "control", "session_source", "risk"):
        token = coordinator.begin_change(name)
        coordinator.finish_change(token, "READY")


def _binding():
    return DispatchCommandBinding(
        account="account", owner_generation="generation", journal_id="journal",
        strategy="strategy", principal="p", run_id="run", request_key="request",
        reservation_id="reservation", payload_digest="a" * 64,
        native_identity_digest="b" * 64, marker_ref="marker",
    )


def _candidate(coordinator):
    before = coordinator.capture()
    after = coordinator.capture()
    return coordinator.prepare(_binding(), before, after, deadline_ns=time.monotonic_ns() + 1_000_000_000)


def _case(*, publisher=None, partitioned=False, coordinator=None):
    provider = _Provider()
    coordinator = coordinator or _coordinator()
    identities = {"p": (KEY,), "other": (KEY,)} if partitioned else {"p": (KEY,)}
    # A second exported key makes each principal's ACL a partition.
    if partitioned:
        from dataclasses import replace
        extra = replace(KEY, source_ref="other-source")
        keys = (KEY, extra)
        identities = {"p": (KEY,), "other": (KEY, extra)}
    else:
        keys = (KEY,)
    epochs = count(1)
    hub = GatewayMarketHub(
        provider, key_universe=keys, principal_allowed_keys=identities,
        service_identity=WireIdentity(WireRole.SERVICE, "gateway", "gateway"),
        service_scope=WireScope("domain"), epoch_factory=lambda: f"epoch-{next(epochs)}",
        publisher=None if partitioned else (publisher or (lambda _event: None)),
    )
    if partitioned:
        hub.set_partitioned_publisher(
            publisher or (lambda _event: None),
            principal_stream_grants={"p": {KEY.stream_id}, "other": {key.stream_id for key in keys}},
            regrant=lambda _grants: True,
        )
    registration = GatewayMarketRegistration("p", "domain", "strategy", "run", "account")
    service = GatewayMarketService(hub, registrations={"reg": registration})
    gate = GatewayMarketDispatchGate(
        hub, service, account_scope="account", service_generation="generation",
        claim_coordinator=coordinator,
    )
    hub.install_market_dispatch_gate(gate)
    return hub, service, gate, coordinator


def test_market_coordinator_requires_real_matching_owner_and_cannot_open_production():
    with pytest.raises(GatewayMarketDispatchError, match="INVALID_MARKET_CLAIM_COORDINATOR"):
        _case(coordinator=GatewayDispatchClaimCoordinator("other", "generation"))
    production = GatewayDispatchClaimCoordinator("account", "generation")
    hub, _, _, _ = _case(coordinator=production)
    hub.subscribe("p", KEY, "reg")
    attempted = production.begin_change("market")
    with pytest.raises(DispatchClaimError, match="COVERAGE_UNAVAILABLE"):
        production.finish_change(attempted, "READY")
    with pytest.raises(DispatchClaimError, match="COVERAGE_UNAVAILABLE"):
        production.capture()


def test_invalid_rpc_identity_is_pure_preflight_and_does_not_poison():
    _, service, _, coordinator = _case()
    _all_ready(coordinator)
    before = coordinator.capture()
    with pytest.raises(GatewayMarketServiceError, match="UNKNOWN_REGISTRATION"):
        service.handle(
            "market.subscribe", principal_ref="p",
            scope=WireScope("domain", account_ref="account", strategy_ref="strategy", run_id="run"),
            arguments={"key": KEY.to_dict()}, registration_id="not-registered",
        )
    after = coordinator.capture()
    candidate = coordinator.prepare(_binding(), before, after, deadline_ns=time.monotonic_ns() + 1_000_000_000)
    assert coordinator.try_claim(candidate).binding == _binding()._values()


def test_invalid_wire_arguments_are_rejected_without_poisoning_market():
    _, service, _, coordinator = _case()
    _all_ready(coordinator)
    before = coordinator.capture()
    scope = WireScope("domain", account_ref="account", strategy_ref="strategy", run_id="run")
    with pytest.raises(GatewayMarketServiceError, match="INVALID_ARGUMENTS"):
        service.handle("market.subscribe", principal_ref="p", scope=scope,
                       arguments={"wrong": True}, registration_id="reg")
    with pytest.raises(GatewayMarketServiceError, match="MARKET_SUBSCRIPTION_REQUIRED"):
        service.handle("market.head", principal_ref="p", scope=scope,
                       arguments={"key": KEY.to_dict()}, registration_id="reg")
    after = coordinator.capture()
    candidate = coordinator.prepare(_binding(), before, after, deadline_ns=time.monotonic_ns() + 1_000_000_000)
    assert coordinator.try_claim(candidate).binding == _binding()._values()


def test_malformed_key_wire_values_are_pure_rejections_and_valid_subscribe_still_works():
    hub, service, _, coordinator = _case()
    _all_ready(coordinator)
    before = coordinator.capture()
    scope = WireScope("domain", account_ref="account", strategy_ref="strategy", run_id="run")
    invalid_instrument = KEY.to_dict()
    invalid_instrument["instrument"] = None
    missing_field = KEY.to_dict()
    del missing_field["channel"]
    malformed_nested = KEY.to_dict()
    malformed_nested["instrument"] = {"unexpected": [None]}
    for arguments in ({}, {"key": None}, {"key": "{}"}, {"key": []},
                      {"key": invalid_instrument}, {"key": missing_field},
                      {"key": malformed_nested}):
        with pytest.raises(GatewayMarketServiceError):
            service.handle("market.subscribe", principal_ref="p", scope=scope,
                           arguments=arguments, registration_id="reg")
    with pytest.raises(GatewayMarketServiceError, match="UNSUPPORTED_OPERATION"):
        service.handle("market.unknown", principal_ref="p", scope=scope,
                       arguments={}, registration_id="reg")
    after = coordinator.capture()
    coordinator.try_claim(coordinator.prepare(_binding(), before, after,
                                               deadline_ns=time.monotonic_ns() + 1_000_000_000))
    result = service.handle("market.subscribe", principal_ref="p", scope=scope,
                            arguments={"key": KEY.to_dict()}, registration_id="reg")
    assert result["already_subscribed"] is False
    assert hub.registration_active(KEY, "reg")


def test_provider_subscribe_partial_failure_still_poisoned():
    hub, service, _, coordinator = _case()
    _all_ready(coordinator)
    attempts = []
    def fail_after_start(_key, *, generation):
        attempts.append(generation)
        raise RuntimeError("provider outcome unknown")
    hub._provider.subscribe = fail_after_start
    scope = WireScope("domain", account_ref="account", strategy_ref="strategy", run_id="run")
    with pytest.raises(GatewayMarketServiceError, match="PROVIDER_SUBSCRIBE_FAILED"):
        service.handle("market.subscribe", principal_ref="p", scope=scope,
                       arguments={"key": KEY.to_dict()}, registration_id="reg")
    assert len(attempts) == 1
    with pytest.raises(DispatchClaimError, match="DOMAIN_POISONED"):
        coordinator.begin_change("market")


def test_async_bootstrap_pure_schema_errors_do_not_poison():
    hub, service, _, coordinator = _case()
    hub._async_bootstrap = True
    _all_ready(coordinator)
    before = coordinator.capture()
    scope = WireScope("domain", account_ref="account", strategy_ref="strategy", run_id="run")
    malformed = (
        {}, {"operation_id": None, "subscriptions": []},
        {"operation_id": "0" * 32, "subscriptions": None},
        {"operation_id": "0" * 32, "subscriptions": [{"key": None}]},
        {"operation_id": "0" * 32, "subscriptions": [{"key": KEY.to_dict()}], "refresh_existing": None},
    )
    for arguments in malformed:
        with pytest.raises(GatewayMarketServiceError):
            service.handle("market.bootstrap_begin", principal_ref="p", scope=scope,
                           arguments=arguments, registration_id="reg")
    after = coordinator.capture()
    coordinator.try_claim(coordinator.prepare(_binding(), before, after,
                                               deadline_ns=time.monotonic_ns() + 1_000_000_000))


def test_cursor_preflight_and_idempotent_unsubscribe_keep_wire_contract():
    hub, service, _, coordinator = _case()
    hub.subscribe("p", KEY, "reg")
    _all_ready(coordinator)
    before = coordinator.capture()
    scope = WireScope("domain", account_ref="account", strategy_ref="strategy", run_id="run")
    with pytest.raises(GatewayMarketServiceError, match="INVALID_CURSOR"):
        service.handle("market.read_after", principal_ref="p", scope=scope,
                       arguments={"key": KEY.to_dict(), "epoch": "epoch-1", "after": -1, "limit": 1},
                       registration_id="reg")
    after = coordinator.capture()
    coordinator.try_claim(coordinator.prepare(_binding(), before, after, deadline_ns=time.monotonic_ns() + 1_000_000_000))
    service.handle("market.unsubscribe", principal_ref="p", scope=scope,
                   arguments={"key": KEY.to_dict()}, registration_id="reg")
    # The existing API treats a repeated unsubscribe as an idempotent read of
    # the final release state, rather than requiring a live subscription.
    result = service.handle("market.unsubscribe", principal_ref="p", scope=scope,
                            arguments={"key": KEY.to_dict()}, registration_id="reg")
    assert result["upstream_released"] is True


def test_service_nested_subscription_and_unsubscribe_invalidate_market(monkeypatch):
    hub, service, _, coordinator = _case()
    _all_ready(coordinator)
    calls = []
    original = coordinator.begin_change
    def observed(domain):
        calls.append(domain)
        return original(domain)
    monkeypatch.setattr(coordinator, "begin_change", observed)
    service.handle(
        "market.subscribe", principal_ref="p",
        scope=WireScope("domain", account_ref="account", strategy_ref="strategy", run_id="run"),
        arguments={"key": KEY.to_dict()}, registration_id="reg",
    )
    assert calls == ["market"]
    with pytest.raises(DispatchClaimError, match="DOMAIN_UNAVAILABLE"):
        coordinator.capture()
    _all_ready(coordinator)
    hub.unsubscribe("p", KEY, "reg")
    with pytest.raises(DispatchClaimError, match="DOMAIN_UNAVAILABLE"):
        coordinator.capture()


def test_revoke_and_regrant_invalidate_even_with_another_subscriber():
    hub, _, _, coordinator = _case(partitioned=True)
    hub.subscribe("p", KEY, "reg")
    hub.subscribe("other", KEY, "other-reg")
    _all_ready(coordinator)
    hub.revoke_principal_keys("p", [KEY])
    with pytest.raises(DispatchClaimError, match="DOMAIN_UNAVAILABLE"):
        coordinator.capture()
    _all_ready(coordinator)
    hub.grant_principal_keys("p", [KEY])
    with pytest.raises(DispatchClaimError, match="DOMAIN_UNAVAILABLE"):
        coordinator.capture()


@pytest.mark.parametrize("closed", [False, True])
def test_cleanup_releases_provider_after_poisoned_or_closed_coordinator(closed):
    hub, _, _, coordinator = _case()
    released = []
    hub._provider.unsubscribe = lambda handle: released.append(handle) or True
    hub.subscribe("p", KEY, "reg")
    _all_ready(coordinator)
    candidate = _candidate(coordinator)
    if closed:
        coordinator.close()
    else:
        coordinator.poison_domain("market")
    hub.close()
    assert len(released) == 1
    with pytest.raises(DispatchClaimError):
        coordinator.try_claim(candidate)


def test_poisoned_market_allows_revocation_but_never_regrant():
    hub, _, _, coordinator = _case(partitioned=True)
    hub.subscribe("p", KEY, "reg")
    coordinator.poison_domain("market")
    result = hub.revoke_principal_keys("p", [KEY])
    assert result["revoked_stream_ids"] == (KEY.stream_id,)
    with pytest.raises(GatewayMarketDispatchError, match="MARKET_CLAIM_UNAVAILABLE"):
        hub.grant_principal_keys("p", [KEY])


def test_cleanup_poison_first_if_active_coordinator_begin_fails(monkeypatch):
    hub, _, _, coordinator = _case()
    released = []
    hub._provider.unsubscribe = lambda handle: released.append(handle) or True
    hub.subscribe("p", KEY, "reg")
    _all_ready(coordinator)
    candidate = _candidate(coordinator)
    monkeypatch.setattr(coordinator, "begin_change", lambda _domain: (_ for _ in ()).throw(RuntimeError()))
    hub.close()
    assert len(released) == 1
    with pytest.raises(DispatchClaimError, match="DOMAIN_UNAVAILABLE"):
        coordinator.try_claim(candidate)


def test_cleanup_reports_actual_provider_failure_after_claim_poison():
    hub, _, _, coordinator = _case()
    hub.subscribe("p", KEY, "reg")
    coordinator.poison_domain("market")
    hub._provider.unsubscribe = lambda _handle: False
    with pytest.raises(GatewayMarketHubError, match="PROVIDER_UNSUBSCRIBE_FAILED"):
        hub.close()


def test_registration_clock_observation_and_epoch_reset_are_invalidating():
    hub, service, _, coordinator = _case()
    _all_ready(coordinator)
    registration = GatewayMarketRegistration("p", "domain", "strategy", "run", "account")
    service.register_registration("reg2", registration)
    with pytest.raises(DispatchClaimError, match="DOMAIN_UNAVAILABLE"):
        coordinator.capture()
    hub.subscribe("p", KEY, "reg")
    _all_ready(coordinator)
    assert hub.clock_observation("p", KEY, "reg") == {"synthetic": True}
    with pytest.raises(DispatchClaimError, match="DOMAIN_UNAVAILABLE"):
        coordinator.capture()
    _all_ready(coordinator)
    assert hub.reset_stream_epoch(KEY) == "epoch-2"
    with pytest.raises(DispatchClaimError, match="DOMAIN_UNAVAILABLE"):
        coordinator.capture()
    _all_ready(coordinator)
    hub.close()
    with pytest.raises(DispatchClaimError, match="DOMAIN_UNAVAILABLE"):
        coordinator.capture()


def test_partial_publish_failure_and_caught_nested_error_poison_domain():
    def failing_publisher(_event):
        raise RuntimeError("synthetic publish failure")
    hub, _, _, coordinator = _case(publisher=failing_publisher)
    hub.subscribe("p", KEY, "reg")
    _all_ready(coordinator)
    now = datetime.now(timezone.utc)
    event = _unqualified_event(now, now)
    with pytest.raises(GatewayMarketHubError, match="PUBLISHER_FAILED"):
        hub.publish(KEY, event.data.to_dict(), now.isoformat().replace("+00:00", "Z"),
                    generation=hub._streams[KEY].generation)
    with pytest.raises(DispatchClaimError, match="DOMAIN_POISONED"):
        coordinator.begin_change("market")

    _, _, gate, nested = _case()
    _all_ready(nested)
    with gate.market_change():
        try:
            with gate.market_change():
                raise ValueError("synthetic partially failed nested operation")
        except ValueError:
            pass
    with pytest.raises(DispatchClaimError, match="DOMAIN_POISONED"):
        nested.begin_change("market")


def test_in_progress_publisher_blocks_claim_without_holding_coordinator_lock():
    started, finished = threading.Event(), threading.Event()
    result = []
    def publisher(_event):
        started.set()
        assert finished.wait(2), "claim thread could not inspect an in-progress mutation"
    hub, _, _, coordinator = _case(publisher=publisher)
    hub.subscribe("p", KEY, "reg")
    _all_ready(coordinator)
    candidate = _candidate(coordinator)
    def attempt_claim():
        assert started.wait(2)
        try:
            coordinator.try_claim(candidate)
        except DispatchClaimError as exc:
            result.append(exc.code)
        finally:
            finished.set()
    thread = threading.Thread(target=attempt_claim)
    thread.start()
    now = datetime.now(timezone.utc)
    event = _unqualified_event(now, now)
    hub.publish(KEY, event.data.to_dict(), now.isoformat().replace("+00:00", "Z"),
                generation=hub._streams[KEY].generation)
    thread.join(2)
    assert not thread.is_alive()
    assert result == ["DOMAIN_UNAVAILABLE"]


def test_claimed_order_does_not_block_later_revoke_and_legacy_fence_is_closed():
    hub, _, gate, coordinator = _case(partitioned=True)
    hub.subscribe("p", KEY, "reg")
    _all_ready(coordinator)
    candidate = _candidate(coordinator)
    coordinator.try_claim(candidate)
    native_started, native_release = threading.Event(), threading.Event()
    def synthetic_native():
        native_started.set()
        assert native_release.wait(2)
    thread = threading.Thread(target=synthetic_native)
    thread.start()
    try:
        assert native_started.wait(2)
        hub.revoke_principal_keys("p", [KEY])
        assert thread.is_alive(), "test must exercise an in-flight native call"
        with pytest.raises(DispatchClaimError):
            coordinator.try_claim(candidate)
        with pytest.raises(GatewayMarketDispatchError, match="MARKET_LEGACY_NATIVE_FENCE_UNAVAILABLE"):
            gate.native_fence(None, None)
    finally:
        native_release.set()
        thread.join(2)
    assert not thread.is_alive()
