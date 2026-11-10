"""Synthetic owner-local Gateway market dispatch fencing; no network or account."""

from dataclasses import replace
from datetime import datetime, timedelta, timezone
import json
import time

import pytest

from bt_api_base.service_protocol import WireIdentity, WireRole, WireScope
from bt_api_gateway.direct_command_journal import DirectCommandRecord
from bt_api_gateway.market_hub import (
    GatewayMarketHub,
    GatewayMarketRegistration,
    GatewayMarketService,
)
from bt_api_py.gateway_market_dispatch_gate import (
    GatewayMarketDispatchError,
    GatewayMarketDispatchGate,
)
from bt_api_py.service_registry import ServiceCallContext
from tests.test_iter62_clock_verification import KEY, SOURCE, _unqualified_event


class Provider:
    def subscribe(self, key, *, generation):
        return object()

    def unsubscribe(self, handle):
        return True

    def read_market_login_clock(self, handle, **kwargs):
        raise AssertionError("legacy source was used")

    def read_market_tick_clock(self, handle, **kwargs):
        assert kwargs["provider_sequence"] == self.observation["provider_sequence"]
        assert (
            kwargs["provider_subscription_epoch"] == self.observation["provider_subscription_epoch"]
        )
        return self.observation, self.observed_mono


def _case(*, partitioned=False):
    provider = Provider()
    extra = replace(KEY, source_ref="other-source")
    keys = (KEY, extra) if partitioned else (KEY,)
    acl = {"p": (KEY,), "other": (KEY, extra)} if partitioned else {"p": (KEY,)}
    hub = GatewayMarketHub(
        provider,
        key_universe=keys,
        principal_allowed_keys=acl,
        service_identity=WireIdentity(WireRole.SERVICE, "gateway", "gateway"),
        service_scope=WireScope("domain"),
        epoch_factory=lambda: "epoch-1",
        publisher=None if partitioned else (lambda event: None),
    )
    if partitioned:
        hub.set_partitioned_publisher(
            lambda event: None,
            principal_stream_grants={
                "p": {KEY.stream_id},
                "other": {KEY.stream_id, extra.stream_id},
            },
            regrant=lambda grants: True,
        )
    registration = GatewayMarketRegistration("p", "domain", "strategy", "run", "account")
    service = GatewayMarketService(hub, registrations={"reg": registration})
    gate = GatewayMarketDispatchGate(
        hub,
        service,
        account_scope="account",
        service_generation="generation",
        native_source_check=lambda handle, **kwargs: (provider.observation, provider.observed_mono),
    )
    hub.install_market_dispatch_gate(gate)
    hub.subscribe("p", KEY, "reg")
    if partitioned:
        hub.subscribe("other", KEY, "other-reg")
    now = datetime.now(timezone.utc)
    china = now.astimezone(timezone(timedelta(hours=8)))
    event = _unqualified_event(now - timedelta(milliseconds=20), now)
    payload = replace(event.data, trading_date=china.date().isoformat())
    envelope = hub.publish(
        KEY,
        payload.to_dict(),
        now.isoformat().replace("+00:00", "Z"),
        generation=hub._streams[KEY].generation,
    )
    wall = time.time_ns()
    obs = {
        "schema_version": 2,
        "observation_kind": "native_md_tick",
        "provider": "ctp",
        "account_scope": "account",
        "venue": KEY.instrument.venue,
        "service_generation": "generation",
        "td_connection_generation": 1,
        "td_source_ref": "td",
        "trading_day": china.strftime("%Y%m%d"),
        "market_source": SOURCE.to_dict(),
        "provider_sequence": payload.provider_sequence,
        "provider_subscription_epoch": payload.provider_subscription_epoch,
        "venue_observed_at_utc": payload.provider_event_time_utc,
        "source_event_received_wall_ns": wall - 10_000,
        "source_observed_wall_ns": wall,
        "remaining_ns": 60_000_000_000,
        "source_calibration": {
            "schema_version": 1,
            "method": "sntp",
            "offset_ns": 0,
            "error_bound_ns": 2_000_000,
            "remaining_ns": 60_000_000_000,
        },
    }
    provider.observation, provider.observed_mono = obs, time.monotonic_ns()
    ctx = ServiceCallContext(
        WireIdentity(WireRole.STRATEGY_CLIENT, "p", "strategy"),
        WireScope("domain", account_ref="account", strategy_ref="strategy", run_id="run"),
        "reg",
        "p",
    )
    ref = {
        "key": KEY.to_dict(),
        "epoch": envelope.header.stream_epoch,
        "sequence": envelope.header.sequence,
        "message_id": envelope.header.message_id,
    }
    public = service.handle(
        "market.clock_observation",
        principal_ref="p",
        scope=ctx.scope,
        registration_id="reg",
        arguments=ref,
    )
    assert public == obs and "source_observed_monotonic_ns" not in public
    row = DirectCommandRecord(
        "account",
        "strategy",
        "request",
        "reservation",
        "p",
        "a" * 64,
        json.dumps({"symbol": KEY.instrument.native_symbol, "exchange_id": KEY.instrument.venue}),
        "generation",
        time.monotonic() + 10,
        "RESERVED",
        1,
        None,
        (),
        None,
        None,
    )

    def issue():
        return gate.issue(
            ctx,
            ref,
            market_registration_id="reg",
            request_key="request",
            reservation_id="reservation",
            digest="a" * 64,
        )

    return hub, gate, ctx, ref, row, issue


def test_qualified_event_is_bound_and_one_shot():
    _, gate, _, _, row, issue = _case()
    permit = issue()
    called = []
    with gate.dispatch_fence(permit, row):
        gate.recheck(permit, row)
    # Decision B: one-shot use moved to the durable journal (a command leaves
    # RESERVED at its marker) and the per-account claim; the market permit is
    # no longer consumed, so native_recheck is a repeatable read-only check.
    gate.native_recheck(permit, row)
    gate.recheck(permit, row)
    called.append("native")
    gate.native_recheck(permit, row)
    gate.native_recheck(permit, row)
    assert called == ["native"]
    with pytest.raises(GatewayMarketDispatchError, match="MARKET_LEGACY_NATIVE_FENCE_UNAVAILABLE"):
        with gate.native_fence(permit, row):
            called.append("legacy")
    assert called == ["native"]


def test_queue_expiry_rejects_before_native(monkeypatch):
    _, gate, _, _, row, issue = _case()
    permit = issue()
    monkeypatch.setattr(
        "bt_api_py.gateway_market_dispatch_gate.time.monotonic_ns", lambda: permit.expires_ns + 1
    )
    called = []
    with pytest.raises(GatewayMarketDispatchError, match="MARKET_QUOTE_EXPIRED"):
        with gate.dispatch_fence(permit, row):
            called.append("native")
    assert called == []


def test_wrong_scope_request_and_event_reference_fail_closed():
    _, gate, ctx, ref, row, issue = _case()
    permit = issue()
    with pytest.raises(GatewayMarketDispatchError):
        gate.native_recheck(permit, replace(row, strategy_scope="other"))
    with pytest.raises(GatewayMarketDispatchError):
        gate.native_recheck(permit, replace(row, payload_json='{"symbol":"other"}'))
    with pytest.raises(GatewayMarketDispatchError):
        gate.issue(
            ctx,
            {**ref, "message_id": "not-published"},
            market_registration_id="reg",
            request_key="request",
            reservation_id="reservation",
            digest="a" * 64,
        )


def test_authorization_change_invalidates_and_transition_refuses():
    hub, gate, _, _, row, issue = _case()
    permit = issue()
    with gate.market_change():
        with pytest.raises(GatewayMarketDispatchError):
            gate.native_recheck(permit, row)
        hub.unsubscribe("p", KEY, "reg")
    with pytest.raises(GatewayMarketDispatchError):
        with gate.dispatch_fence(permit, row):
            pass


def test_expiry_between_commit_and_native_does_not_invoke(monkeypatch):
    _, gate, _, _, row, issue = _case()
    permit = issue()
    with gate.dispatch_fence(permit, row):
        pass  # synthetic journal marker
    monkeypatch.setattr(
        "bt_api_py.gateway_market_dispatch_gate.time.monotonic_ns", lambda: permit.expires_ns + 1
    )
    called = []
    with pytest.raises(GatewayMarketDispatchError):
        gate.native_recheck(permit, row)
        called.append("native")
    assert not called


def test_failed_publish_is_not_an_authoritative_event():
    hub, gate, ctx, _, _, _ = _case()
    with gate.market_change():
        hub._publisher = lambda event: False
    now = datetime.now(timezone.utc)
    payload = _unqualified_event(now, now).data
    with pytest.raises(Exception):
        hub.publish(
            KEY,
            payload.to_dict(),
            now.isoformat().replace("+00:00", "Z"),
            generation=hub._streams[KEY].generation,
        )
    header = hub._streams[KEY].events[-1].envelope.header
    failed_ref = {
        "key": KEY.to_dict(),
        "epoch": header.stream_epoch,
        "sequence": header.sequence,
        "message_id": header.message_id,
    }
    with pytest.raises(GatewayMarketDispatchError, match="MARKET_EVIDENCE_UNAVAILABLE"):
        gate.issue(
            ctx,
            failed_ref,
            market_registration_id="reg",
            request_key="request",
            reservation_id="reservation",
            digest="a" * 64,
        )


def test_market_transition_does_not_hold_gate_lock_while_owner_checks():
    import threading

    _, gate, _, _, row, issue = _case()
    permit = issue()
    checked = threading.Event()
    results = []

    def attempt():
        try:
            gate.native_recheck(permit, row)
            results.append("native")
        except GatewayMarketDispatchError:
            results.append("denied")
        finally:
            checked.set()

    with gate.market_change():
        worker = threading.Thread(target=attempt)
        worker.start()
        assert checked.wait(1), "market transition held the lock while waiting for owner"
    worker.join(1)
    assert not worker.is_alive() and results == ["denied"]


def test_clock_observation_cannot_be_reused_for_another_native_event():
    _, gate, ctx, ref, _, _ = _case()
    obs, source_mono, clock_wall, clock_mono = gate._clocks[("reg", "p", KEY)]
    obs["provider_sequence"] += 1
    gate._clocks[("reg", "p", KEY)] = obs, source_mono, clock_wall, clock_mono
    with pytest.raises(GatewayMarketDispatchError, match="MARKET_SOURCE_MISMATCH"):
        gate.issue(
            ctx,
            ref,
            market_registration_id="reg",
            request_key="request",
            reservation_id="reservation",
            digest="a" * 64,
        )


def test_delegated_service_uses_own_account_market_registration():
    from bt_api_base.gateway_delegation_protocol import GatewayDelegatedCommand
    from bt_api_base.gateway_direct_protocol import gateway_payload_digest
    from bt_api_gateway.direct_delegation import GatewayDelegateGrant, authorize_delegated_command

    _, gate, _, ref, row, _ = _case()
    with gate.market_change():
        gate._service._registrations["reg"] = GatewayMarketRegistration(
            "p", "domain", account_ref="account"
        )
    identity = WireIdentity(WireRole.SERVICE, "p", "risk")
    ctx = ServiceCallContext(
        identity,
        WireScope("domain", account_ref="account", strategy_ref="strategy", run_id="run"),
        "direct-reg",
        "p",
    )
    payload = {
        "symbol": KEY.instrument.native_symbol,
        "exchange_id": KEY.instrument.venue,
        "idempotency_key": "request",
    }
    digest = gateway_payload_digest(payload)
    command = GatewayDelegatedCommand(
        "account",
        "strategy",
        "domain",
        "p",
        "risk",
        "risk",
        "run",
        "request",
        digest,
        KEY.instrument,
        "revision",
    )
    grant = GatewayDelegateGrant(
        identity,
        "account",
        "strategy",
        "domain",
        "risk",
        "revision",
        frozenset({KEY.instrument}),
        frozenset({"direct.dispatch"}),
    )
    active = [True]
    delegated = authorize_delegated_command(
        ctx,
        grant,
        command,
        "direct.dispatch",
        payload,
        registry_recheck=lambda _: active[0],
        current_grant=lambda: grant,
        instrument_recheck=lambda instrument: instrument == KEY.instrument,
    )
    permit = gate.issue(
        ctx,
        ref,
        market_registration_id="reg",
        request_key="request",
        reservation_id="reservation",
        digest=digest,
        delegation_permit=delegated,
    )
    actual_row = replace(row, digest=digest, payload_json=json.dumps(payload))
    assert gate.matches_delegation(permit, delegated)
    assert not gate.matches_delegation(permit, None)
    active[0] = False
    with pytest.raises(GatewayMarketDispatchError, match="MARKET_DELEGATION_REVOKED"):
        gate.native_recheck(permit, actual_row)
    active[0] = True
    gate.native_recheck(permit, actual_row)
    with pytest.raises(GatewayMarketDispatchError):
        gate.issue(
            ctx,
            ref,
            market_registration_id="reg",
            request_key="request",
            reservation_id="reservation",
            digest=digest,
        )
    with pytest.raises(GatewayMarketDispatchError):
        gate.issue(
            ctx,
            ref,
            market_registration_id="reg",
            request_key="other",
            reservation_id="reservation",
            digest=digest,
            delegation_permit=delegated,
        )


def test_native_source_disconnect_or_epoch_change_prevents_dispatch():
    _, gate, _, _, row, issue = _case()
    permit = issue()
    original_check = gate._native_source_check

    def changed(*args, **kwargs):
        obs, mono = original_check(*args, **kwargs)
        return {**obs, "td_connection_generation": obs["td_connection_generation"] + 1}, mono

    gate._native_source_check = changed
    sent = []
    with pytest.raises(GatewayMarketDispatchError, match="MARKET_NATIVE_SOURCE_UNAVAILABLE"):
        gate.native_recheck(permit, row)
        sent.append("native")
    assert not sent


def test_real_journal_marker_rolls_back_if_quote_expires_before_commit(tmp_path, monkeypatch):
    from contextlib import contextmanager
    from bt_api_base.direct_write_protocol import (
        NativeOrderIdentity,
        PreDispatchRecord,
        WriteOperation,
    )
    from bt_api_gateway.direct_command_journal import GatewayDirectCommandJournal
    from bt_api_gateway.direct_dispatch import (
        GatewayDirectDispatcher,
        DirectDispatchError,
        NativeDispatchOutcome,
    )
    from bt_api_gateway.session_guard import LocalAccountSessionGuard
    from iter63_claim_support import SyntheticClaimPort

    _, gate, ctx, ref, _, _ = _case()
    (tmp_path / "guards").mkdir()
    guard = LocalAccountSessionGuard(tmp_path / "guards", "account", "owner")
    generation = guard.acquire()
    gate._generation = generation
    obs, source_mono, clock_wall, clock_mono = gate._clocks[("reg", "p", KEY)]
    obs["service_generation"] = generation
    gate._clocks[("reg", "p", KEY)] = obs, source_mono, clock_wall, clock_mono
    original_source_check = gate._native_source_check

    def source_check(*args, **kwargs):
        # The synthetic native source must follow the re-keyed service generation.
        observed, mono = original_source_check(*args, **kwargs)
        return {**observed, "service_generation": generation}, mono

    gate._native_source_check = source_check
    journal = GatewayDirectCommandJournal.create(
        tmp_path / "journal", "account", generation=generation
    )
    payload = {"symbol": KEY.instrument.native_symbol, "exchange_id": KEY.instrument.venue}
    row = journal.reserve("strategy", "request", "p", payload)
    permit = gate.issue(
        ctx,
        ref,
        market_registration_id="reg",
        request_key="request",
        reservation_id=row.reservation_id,
        digest=row.digest,
    )
    checks = []
    orig_fence = gate.dispatch_fence

    @contextmanager
    def dispatch_fence(capability, record):
        # Decision B: the claim's final validation passed before the marker.
        # The quote then expires between the claim and the marker transaction.
        checks.append(1)
        monkeypatch.setattr(
            "bt_api_py.gateway_market_dispatch_gate.time.monotonic_ns",
            lambda: permit.expires_ns + 1,
        )
        with orig_fence(capability, record):
            yield

    monkeypatch.setattr(gate, "dispatch_fence", dispatch_fence)
    calls = []
    identity = NativeOrderIdentity("CTP", "native-ref", {"trading_day": "20300101"}, 1)

    def native(order, port, scope):
        marker = port.commit(
            PreDispatchRecord(
                WriteOperation.PLACE,
                scope,
                "CTP",
                KEY.instrument.native_symbol,
                identity,
                None,
                order,
                "20300101",
                1,
                "synthetic",
            )
        )
        with port.native_fence() as check:
            check()
            calls.append("native")
        return NativeDispatchOutcome(True, identity, marker)

    @contextmanager
    def registry_fence(record):
        yield lambda: None

    dispatcher = GatewayDirectDispatcher(
        journal,
        "CTP",
        guard,
        native,
        lambda value: ("native-key",),
        lambda record: True,
        claim_port=SyntheticClaimPort(),
    )
    try:
        with pytest.raises(DirectDispatchError, match="PRE_DISPATCH_FAILED"):
            dispatcher.dispatch(
                row.strategy_scope,
                row.request_key,
                row.principal,
                row.reservation_id,
                row.digest,
                row.version,
                request_fence=registry_fence,
                market_gate=gate,
                market_permit=permit,
            )
        assert journal.get("strategy", "request", "p").status == "RESERVED"
        assert not calls and len(checks) == 1  # only the marker transaction fenced
    finally:
        guard.release()


def test_real_journal_native_fence_denial_records_not_sent(tmp_path, monkeypatch):
    from contextlib import contextmanager
    from bt_api_base.direct_write_protocol import (
        NativeOrderIdentity,
        PreDispatchRecord,
        WriteOperation,
    )
    from bt_api_gateway.direct_command_journal import GatewayDirectCommandJournal
    from bt_api_gateway.direct_dispatch import (
        DirectDispatchError,
        GatewayDirectDispatcher,
        NativeDispatchOutcome,
    )
    from bt_api_gateway.session_guard import LocalAccountSessionGuard
    from iter63_claim_support import SyntheticClaimPort

    _, gate, ctx, ref, _, _ = _case()
    (tmp_path / "guards").mkdir()
    guard = LocalAccountSessionGuard(tmp_path / "guards", "account", "owner")
    generation = guard.acquire()
    gate._generation = generation
    obs, source_mono, clock_wall, clock_mono = gate._clocks[("reg", "p", KEY)]
    obs["service_generation"] = generation
    gate._clocks[("reg", "p", KEY)] = obs, source_mono, clock_wall, clock_mono
    original_source_check = gate._native_source_check

    def source_check(*args, **kwargs):
        # The synthetic native source must follow the re-keyed service generation.
        observed, mono = original_source_check(*args, **kwargs)
        return {**observed, "service_generation": generation}, mono

    gate._native_source_check = source_check
    journal = GatewayDirectCommandJournal.create(
        tmp_path / "journal", "account", generation=generation
    )
    payload = {"symbol": KEY.instrument.native_symbol, "exchange_id": KEY.instrument.venue}
    row = journal.reserve("strategy", "request", "p", payload)
    permit = gate.issue(
        ctx,
        ref,
        market_registration_id="reg",
        request_key="request",
        reservation_id=row.reservation_id,
        digest=row.digest,
    )
    calls = []
    identity = NativeOrderIdentity("CTP", "native-ref", {"trading_day": "20300101"}, 1)

    def native(order, port, scope):
        marker = port.commit(
            PreDispatchRecord(
                WriteOperation.PLACE,
                scope,
                "CTP",
                KEY.instrument.native_symbol,
                identity,
                None,
                order,
                "20300101",
                1,
                "synthetic",
            )
        )
        with port.native_fence() as check:
            check()
            calls.append("native")
        return NativeDispatchOutcome(True, identity, marker)

    @contextmanager
    def registry_fence(record):
        yield lambda: None

    claim_port = SyntheticClaimPort()
    dispatcher = GatewayDirectDispatcher(
        journal,
        "CTP",
        guard,
        native,
        lambda value: ("native-key",),
        lambda record: True,
        claim_port=claim_port,
    )
    # Decision B: the claim (with the market native_recheck) precedes the
    # marker, so an expired quote refuses before any marker or native call.
    monkeypatch.setattr(
        "bt_api_py.gateway_market_dispatch_gate.time.monotonic_ns",
        lambda: permit.expires_ns + 1,
    )
    try:
        with pytest.raises(DirectDispatchError, match="DISPATCH_CLAIM_UNAVAILABLE"):
            dispatcher.dispatch(
                row.strategy_scope,
                row.request_key,
                row.principal,
                row.reservation_id,
                row.digest,
                row.version,
                request_fence=registry_fence,
                market_gate=gate,
                market_permit=permit,
            )
        assert not calls and claim_port.claims == 0  # no claim, no marker, no native
        stored = journal.get("strategy", "request", "p")
        assert stored.status == "RESERVED" and stored.native_identity_json is None
    finally:
        guard.release()


def test_evicted_event_is_never_dispatchable():
    hub, gate, _, _, row, issue = _case()
    permit = issue()
    with gate.market_change():
        hub._streams[KEY].events.clear()
    with pytest.raises(GatewayMarketDispatchError, match="MARKET_EVIDENCE_UNAVAILABLE"):
        gate.native_recheck(permit, row)


def test_caught_nested_market_mutation_failure_invalidates_existing_permit():
    _, gate, _, _, row, issue = _case()
    permit = issue()
    with gate.market_change():
        try:
            with gate.market_change():
                raise ValueError("nested failure")
        except ValueError:
            pass
    with pytest.raises(GatewayMarketDispatchError, match="MARKET_PERMIT_INVALID"):
        gate.native_recheck(permit, row)


def test_source_clock_wall_jump_fails_closed(monkeypatch):
    _, gate, ctx, ref, _, _ = _case()
    real_time_ns = time.time_ns
    monkeypatch.setattr(
        "bt_api_py.gateway_market_dispatch_gate.time.time_ns",
        lambda: real_time_ns() + 1_000_000_000,
    )
    with pytest.raises(GatewayMarketDispatchError, match="MARKET_CLOCK_INVALID"):
        gate.issue(
            ctx,
            ref,
            market_registration_id="reg",
            request_key="request",
            reservation_id="reservation",
            digest="a" * 64,
        )


@pytest.mark.parametrize("step_ns", [-200_000_000, 200_000_000])
def test_subthreshold_wall_steps_cannot_extend_or_reduce_tick_deadline(monkeypatch, step_ns):
    _, gate, ctx, ref, row, issue = _case()
    baseline = issue()
    real_time_ns = time.time_ns
    monkeypatch.setattr(
        "bt_api_py.gateway_market_dispatch_gate.time.time_ns", lambda: real_time_ns() + step_ns
    )
    later = gate.issue(
        ctx,
        ref,
        market_registration_id="reg",
        request_key="request",
        reservation_id="reservation",
        digest="a" * 64,
    )
    assert abs(later.expires_ns - baseline.expires_ns) < 1000
    gate.native_recheck(later, row)


def test_regrant_floor_excludes_old_frames_from_clock_and_dispatch():
    from bt_api_gateway.market_hub import GatewayMarketServiceError

    hub, gate, ctx, old_ref, _, issue = _case(partitioned=True)
    old_permit = issue()
    hub.revoke_principal_keys("p", (KEY,))
    now = datetime.now(timezone.utc)
    from bt_api_base.market_tick_protocol import MarketTickPayload

    original = MarketTickPayload.from_dict(hub._streams[KEY].events[-1].envelope.payload["data"])
    frame = replace(original, provider_sequence=original.provider_sequence + 1)
    during = hub.publish(
        KEY,
        frame.to_dict(),
        now.isoformat().replace("+00:00", "Z"),
        generation=hub._streams[KEY].generation,
    )
    hub.grant_principal_keys("p", (KEY,))
    hub.subscribe("p", KEY, "reg")
    during_ref = {
        "key": KEY.to_dict(),
        "epoch": during.header.stream_epoch,
        "sequence": during.header.sequence,
        "message_id": during.header.message_id,
    }
    for ref in (old_ref, during_ref):
        with pytest.raises(GatewayMarketServiceError, match="MARKET_EVIDENCE_UNAVAILABLE"):
            gate._service.handle(
                "market.clock_observation",
                principal_ref="p",
                scope=ctx.scope,
                registration_id="reg",
                arguments=ref,
            )
        with pytest.raises(GatewayMarketDispatchError, match="MARKET_AUTHORIZATION_FLOOR"):
            gate.issue(
                ctx,
                ref,
                market_registration_id="reg",
                request_key="request",
                reservation_id="reservation",
                digest="a" * 64,
            )
    with pytest.raises(GatewayMarketDispatchError):
        gate.recheck(old_permit, replace(_case()[4], generation="generation"))
    frame = replace(frame, provider_sequence=frame.provider_sequence + 1)
    hub._provider.observation["provider_sequence"] = frame.provider_sequence
    new_frame = hub.publish(
        KEY,
        frame.to_dict(),
        now.isoformat().replace("+00:00", "Z"),
        generation=hub._streams[KEY].generation,
    )
    new_ref = {
        "key": KEY.to_dict(),
        "epoch": new_frame.header.stream_epoch,
        "sequence": new_frame.header.sequence,
        "message_id": new_frame.header.message_id,
    }
    gate._service.handle(
        "market.clock_observation",
        principal_ref="p",
        scope=ctx.scope,
        registration_id="reg",
        arguments=new_ref,
    )
    assert gate.issue(
        ctx,
        new_ref,
        market_registration_id="reg",
        request_key="request",
        reservation_id="reservation",
        digest="a" * 64,
    )


def _delegated_real_journal_case(tmp_path, *, parent):
    from contextlib import contextmanager

    from bt_api_base.gateway_delegation_protocol import GatewayDelegatedCommand
    from bt_api_base.gateway_direct_protocol import gateway_payload_digest
    from bt_api_gateway.direct_command_journal import GatewayDirectCommandJournal
    from bt_api_gateway.direct_delegation import GatewayDelegateGrant, authorize_delegated_command
    from bt_api_gateway.session_guard import LocalAccountSessionGuard
    from iter63_claim_support import SyntheticClaimPort

    _, gate, _, ref, _, _ = _case()
    with gate.market_change():
        gate._service._registrations["reg"] = GatewayMarketRegistration(
            "p", "domain", account_ref="account"
        )
    (tmp_path / "guards").mkdir()
    guard = LocalAccountSessionGuard(tmp_path / "guards", "account", "owner")
    generation = guard.acquire()
    gate._generation = generation
    obs, source_mono, clock_wall, clock_mono = gate._clocks[("reg", "p", KEY)]
    obs["service_generation"] = generation
    gate._hub._provider.observation["service_generation"] = generation
    gate._clocks[("reg", "p", KEY)] = obs, source_mono, clock_wall, clock_mono
    original_source_check = gate._native_source_check

    def source_check(*args, **kwargs):
        # The synthetic native source must follow the re-keyed service generation.
        observed, mono = original_source_check(*args, **kwargs)
        return {**observed, "service_generation": generation}, mono

    gate._native_source_check = source_check
    journal = GatewayDirectCommandJournal.create(
        tmp_path / "journal", "account", generation=generation
    )
    journal.enable_controls(guard)
    journal.enable_strategy_ledger(guard)
    journal.enable_account_extensions(guard)
    identity = WireIdentity(WireRole.SERVICE, "p", "risk")
    ctx = ServiceCallContext(
        identity,
        WireScope("domain", account_ref="account", strategy_ref="strategy", run_id="run"),
        "direct-reg",
        "p",
    )
    payload = {
        "symbol": KEY.instrument.native_symbol,
        "exchange_id": KEY.instrument.venue,
        "idempotency_key": "request",
        "offset": "open",
    }
    digest = gateway_payload_digest(payload)
    command = GatewayDelegatedCommand(
        "account",
        "strategy",
        "domain",
        "p",
        "risk",
        "risk",
        "run",
        "request",
        digest,
        KEY.instrument,
        "revision",
        parent_ref="parent" if parent else None,
        child_id="child" if parent else None,
    )
    grant = GatewayDelegateGrant(
        identity,
        "account",
        "strategy",
        "domain",
        "risk",
        "revision",
        frozenset({KEY.instrument}),
        frozenset({"direct.reserve", "direct.dispatch"}),
    )

    def permit_for(operation):
        return authorize_delegated_command(
            ctx,
            grant,
            command,
            operation,
            payload,
            registry_recheck=lambda _: True,
            current_grant=lambda: grant,
            instrument_recheck=lambda value: value == KEY.instrument,
            parent_recheck=lambda db, cmd, order: db.in_transaction,
        )

    @contextmanager
    def reserve_fence():
        yield

    row = journal.reserve(
        "strategy",
        "request",
        "p",
        payload,
        reservation_fence=reserve_fence,
        delegation_permit=permit_for("direct.reserve"),
    )
    delegated = permit_for("direct.dispatch")
    market = gate.issue(
        ctx,
        ref,
        market_registration_id="reg",
        request_key="request",
        reservation_id=row.reservation_id,
        digest=row.digest,
        delegation_permit=delegated,
    )
    return guard, journal, gate, row, delegated, market


def test_market_parent_dispatch_rejected_before_marker_and_native(tmp_path):
    from contextlib import contextmanager
    from bt_api_gateway.direct_dispatch import DirectDispatchError, GatewayDirectDispatcher

    guard, journal, gate, row, delegated, market = _delegated_real_journal_case(
        tmp_path, parent=True
    )
    calls = []

    @contextmanager
    def fence(record):
        yield lambda: None

    dispatcher = GatewayDirectDispatcher(
        journal,
        "CTP",
        guard,
        lambda *args: calls.append("native"),
        lambda value: ("native-key",),
        lambda record: True,
    )
    try:
        with pytest.raises(DirectDispatchError, match="PARENT_NATIVE_FENCE_UNAVAILABLE"):
            dispatcher.dispatch(
                row.strategy_scope,
                row.request_key,
                row.principal,
                row.reservation_id,
                row.digest,
                row.version,
                request_fence=fence,
                delegation_permit=delegated,
                market_gate=gate,
                market_permit=market,
            )
        actual = journal.get("strategy", "request", "p")
        assert actual.status == "RESERVED" and actual.version == row.version
        assert actual.native_identity_json is None and not calls
    finally:
        guard.release()


def test_nonparent_final_recheck_does_not_open_journal(tmp_path, monkeypatch):
    from contextlib import contextmanager
    import threading
    from bt_api_base.direct_write_protocol import (
        NativeOrderIdentity,
        PreDispatchRecord,
        WriteOperation,
    )
    from iter63_claim_support import SyntheticClaimPort
    from bt_api_gateway.direct_dispatch import GatewayDirectDispatcher, NativeDispatchOutcome

    guard, journal, gate, row, delegated, market = _delegated_real_journal_case(
        tmp_path, parent=False
    )
    identity = NativeOrderIdentity("CTP", "native-ref", {"trading_day": "20300101"}, 1)
    source_lock = threading.RLock()
    sends = []
    write_attempts = []
    lock_free = []
    before, after = {}, {}

    def forbidden_write(*args, **kwargs):
        write_attempts.append(True)
        raise AssertionError("journal write during final recheck")

    def gate_lock_is_free():
        got = []

        def probe():
            acquired = gate._lock.acquire(timeout=1)
            got.append(acquired)
            if acquired:
                gate._lock.release()

        worker = threading.Thread(target=probe)
        worker.start()
        worker.join(2)
        return got == [True]

    def native(order, port, scope):
        marker = port.commit(
            PreDispatchRecord(
                WriteOperation.PLACE,
                scope,
                "CTP",
                KEY.instrument.native_symbol,
                identity,
                None,
                order,
                "20300101",
                1,
                "synthetic",
            )
        )
        before["row"] = journal.get(row.strategy_scope, row.request_key, row.principal)
        with source_lock:
            # Decision B: the final validation includes a read-only journal
            # control recheck (reads allowed), but must never WRITE.
            for name in ("begin_dispatch", "finish", "reserve"):
                monkeypatch.setattr(journal, name, forbidden_write)
            try:
                with port.native_fence() as final_recheck:
                    final_recheck()
                    # The native call runs after every lock was released.
                    lock_free.append(gate_lock_is_free())
                    sends.append("native")
            finally:
                monkeypatch.undo()
                after["row"] = journal.get(row.strategy_scope, row.request_key, row.principal)
        return NativeDispatchOutcome(True, identity, marker)

    @contextmanager
    def fence(record):
        yield lambda: None

    dispatcher = GatewayDirectDispatcher(
        journal,
        "CTP",
        guard,
        native,
        lambda value: ("native-key",),
        lambda record: True,
        claim_port=SyntheticClaimPort(),
    )
    try:
        result = dispatcher.dispatch(
            row.strategy_scope,
            row.request_key,
            row.principal,
            row.reservation_id,
            row.digest,
            row.version,
            request_fence=fence,
            delegation_permit=delegated,
            market_gate=gate,
            market_permit=market,
        )
        assert result.status == "SENT" and sends == ["native"] and not write_attempts
        assert lock_free == [True]
        assert after["row"] == before["row"]  # final recheck left the row untouched
    finally:
        guard.release()


def test_gate_latency_above_two_seconds_requires_the_simnow_relaxation():
    # User decision 2026-11-10: up to 5 s only for a SimNow-only Gateway.
    hub = GatewayMarketHub(
        Provider(), key_universe=(KEY,), principal_allowed_keys={"p": (KEY,)},
        service_identity=WireIdentity(WireRole.SERVICE, "gateway", "gateway"),
        service_scope=WireScope("domain"), epoch_factory=lambda: "epoch-1",
        publisher=lambda event: None,
    )
    service = GatewayMarketService(hub, registrations={
        "reg": GatewayMarketRegistration("p", "domain", "strategy", "run", "account")})

    def gate(**kwargs):
        return GatewayMarketDispatchGate(hub, service, account_scope="account",
                                         service_generation="generation", **kwargs)

    assert gate()._latency_ns == 2_000_000_000
    assert gate(max_latency_seconds=5.0, simnow_latency_relaxed=True)._latency_ns == 5_000_000_000
    for kwargs in ({"max_latency_seconds": 2.5},
                   {"max_latency_seconds": 5.0, "simnow_latency_relaxed": "yes"},
                   {"max_latency_seconds": 5.5, "simnow_latency_relaxed": True}):
        with pytest.raises(GatewayMarketDispatchError, match="INVALID_GATE"):
            gate(**kwargs)

