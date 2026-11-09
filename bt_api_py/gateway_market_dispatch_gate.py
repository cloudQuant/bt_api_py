"""Owner-local market proof fence for Gateway order dispatch.

The hosting service must wrap *all* market Hub/registration mutations in
``market_change`` and call the two private recording hooks only after a real
publisher succeeded or an owner clock observation was returned.  This module
is not a transport API: client data is solely an index into those host facts.
Until the host installs and verifies all hooks, this component must not be
used to authorize a production dispatch.
"""

from __future__ import annotations

import copy
import json
import logging
import math
import os
import threading
import time
from collections import OrderedDict
from contextlib import contextmanager
from dataclasses import dataclass, field
from datetime import datetime, timezone

from bt_api_base.market_protocol import MarketSourceDescriptor, MarketSubscriptionKey
from bt_api_base.market_tick_protocol import MarketTickPayload
from bt_api_base.service_protocol import WireRole
from bt_api_gateway.direct_command_journal import DirectCommandRecord
from bt_api_gateway.market_hub import GatewayMarketHub, GatewayMarketService
from bt_api_py.service_registry import ServiceCallContext

_LOG = logging.getLogger(__name__)


class GatewayMarketDispatchError(RuntimeError):
    def __init__(self, code):
        self.code = code
        super().__init__(code)


def _text(value):
    return type(value) is str and bool(value) and value == value.strip() and len(value) <= 256


def _utc_ns(text):
    if type(text) is not str or not text.endswith("Z"):
        raise GatewayMarketDispatchError("MARKET_TIME_INVALID")
    try:
        value = datetime.fromisoformat(text[:-1] + "+00:00")
        if value.tzinfo is None:
            raise ValueError
        delta = value.astimezone(timezone.utc) - datetime(1970, 1, 1, tzinfo=timezone.utc)
        return (delta.days * 86400 + delta.seconds) * 1_000_000_000 + delta.microseconds * 1000
    except (ValueError, OverflowError, OSError):
        raise GatewayMarketDispatchError("MARKET_TIME_INVALID") from None


@dataclass(frozen=True, slots=True, eq=False)
class _Permit:
    account: str
    strategy: str
    principal: str
    registration: str
    request_key: str
    reservation_id: str
    digest: str
    key: MarketSubscriptionKey
    epoch: str
    sequence: int
    message_id: str
    revision: int
    expires_ns: int
    anchor_mono_ns: int
    anchor_wall_ns: int
    handle: object = field(repr=False)
    source_generation: str
    provider_sequence: int
    provider_subscription_epoch: int
    source_facts_json: str = field(repr=False)
    delegated: object = field(repr=False)
    seal: object = field(repr=False)


class GatewayMarketDispatchGate:
    """Bounded in-process proof store; not independently deployable.

    The market Hub owner is the only writer of published and source facts.
    ``market_change`` does not hold a lock while the Hub may wait on the
    native owner.  A concurrent transaction or native fence fails closed while
    a Hub change is in progress; on return a changed scope invalidates permits.
    Caller lock order is registry admission -> market fence -> journal.
    """

    def __init__(
        self,
        hub,
        market_service,
        *,
        account_scope,
        service_generation,
        max_latency_seconds=2.0,
        max_clock_error_ms=500.0,
        max_entries=2048,
        native_source_check=None,
        claim_coordinator=None,
    ):
        if claim_coordinator is not None:
            from bt_api_py.gateway_dispatch_claim import GatewayDispatchClaimCoordinator

            if (type(claim_coordinator) is not GatewayDispatchClaimCoordinator
                    or claim_coordinator.matches_owner(account_scope, service_generation) is not True):
                raise GatewayMarketDispatchError("INVALID_MARKET_CLAIM_COORDINATOR")
        if (
            type(hub) is not GatewayMarketHub
            or type(market_service) is not GatewayMarketService
            or market_service._hub is not hub
            or not _text(account_scope)
            or not _text(service_generation)
            or (native_source_check is not None and not callable(native_source_check))
            or type(max_entries) is not int
            or not 1 <= max_entries <= 10000
            or any(
                type(v) not in (int, float) or not math.isfinite(v) or v <= 0
                for v in (max_latency_seconds, max_clock_error_ms)
            )
            or max_latency_seconds > 2.0
            or max_clock_error_ms > 500.0
        ):
            raise GatewayMarketDispatchError("INVALID_GATE")
        hub._check_owner()
        self._hub, self._service = hub, market_service
        self._account, self._generation = account_scope, service_generation
        self._native_source_check = native_source_check
        # This only joins the Hub's mutations to the shared invalidation
        # barrier.  Native MD callbacks and the background clock source have
        # independent writers, so this hook alone cannot certify coverage.
        self._claim_coordinator = claim_coordinator
        self._original_claim_coordinator = claim_coordinator
        self._latency_ns = int(max_latency_seconds * 1_000_000_000)
        self._max_error_ns = int(max_clock_error_ms * 1_000_000)
        self._limit = max_entries
        self._pid, self._host_thread = os.getpid(), threading.get_ident()
        self._lock, self._seal = threading.RLock(), object()
        self._changing = 0
        self._change_failed = False
        self._poisoned = False
        self._revision = 0
        self._published = OrderedDict()
        self._clocks = OrderedDict()
        self._permits = OrderedDict()

    def _host(self):
        if os.getpid() != self._pid or threading.get_ident() != self._host_thread:
            raise GatewayMarketDispatchError("MARKET_HOST_CHANGED")
        self._hub._check_owner()

    def _fingerprint(self):
        hub = self._hub
        streams = tuple(
            sorted(
                (
                    key.stream_id,
                    stream.epoch,
                    stream.generation,
                    id(stream.handle),
                    tuple(sorted(stream.registrations)),
                    tuple(sorted(stream.pending_refs.items())),
                    tuple(sorted(stream.ref_owners.items())),
                    stream.starting,
                    stream.start_uncertain,
                    stream.releasing,
                    stream.pending_release,
                    stream.needs_new_epoch,
                )
                for key, stream in hub._streams.items()
            )
        )
        registrations = tuple(
            sorted((key, value) for key, value in self._service._registrations.items())
        )
        owners = tuple(
            sorted(
                (key[0].stream_id, key[1], value)
                for key, value in hub._registration_principals.items()
            )
        )
        acl = tuple(
            sorted(
                (principal, tuple(sorted(key.stream_id for key in keys)))
                for principal, keys in hub._principal_allowed_keys.items()
            )
        )
        floors = tuple(
            sorted(
                (principal, key.stream_id, epoch, sequence)
                for (principal, key), (epoch, sequence) in hub._grant_floors.items()
            )
        )
        return (
            hub._authorization_generation,
            hub._delivery_suspended,
            hub._closing,
            id(hub._publisher),
            id(hub._provider),
            acl,
            floors,
            streams,
            registrations,
            owners,
        )

    @contextmanager
    def market_change(self, *, cleanup=False):
        """Invalidate the common market domain before any Hub mutation.

        Hub operations can nest and may wait for the native owner.  Only the
        outer operation enters the coordinator; neither its mutex nor this
        gate's mutex is held while the mutation executes.  A Hub hook cannot
        prove that independent MD/clock writers were attached, so the market
        domain always ends UNAVAILABLE until a separate trusted composition
        establishes complete coverage.
        """
        self._host()
        token = None
        with self._lock:
            outer = self._changing == 0
            coordinator = self._claim_coordinator
            if coordinator is not self._original_claim_coordinator:
                self._poisoned = True
                raise GatewayMarketDispatchError("MARKET_CLAIM_COORDINATOR_CHANGED")
            if outer and coordinator is not None:
                try:
                    token = coordinator.begin_change("market")
                except Exception:
                    self._poisoned = True
                    if not cleanup or not self._poison_for_cleanup(coordinator):
                        raise GatewayMarketDispatchError("MARKET_CLAIM_UNAVAILABLE") from None
            try:
                before = self._fingerprint() if outer else None
            except Exception:
                self._poisoned = True
                if token is not None:
                    try:
                        coordinator.poison(token)
                    except Exception:
                        if not cleanup or not self._poison_for_cleanup(coordinator):
                            raise GatewayMarketDispatchError("MARKET_CLAIM_UNAVAILABLE") from None
                    token = None
                if not cleanup:
                    raise GatewayMarketDispatchError("MARKET_STATE_UNAVAILABLE") from None
                before = None
            if outer:
                self._change_failed = False
            self._changing += 1
        failed = True
        try:
            yield
            failed = False
        finally:
            poison = False
            failed_change = False
            with self._lock:
                self._changing -= 1
                if failed:
                    self._change_failed = True
                if outer:
                    try:
                        changed = self._fingerprint() != before
                    except Exception:
                        self._poisoned = True
                        changed = True
                    # Only an integrity fault (state unreadable, coordinator
                    # replaced) closes the domain for good. A failed Hub
                    # operation (e.g. a timed-out owner call) still bumped the
                    # epoch, so prior candidates are stale; it ends UNAVAILABLE
                    # and the next successful market change publishes again.
                    poison = self._poisoned
                    failed_change = self._change_failed
                    if self._change_failed or changed:
                        self._revision += 1
            if token is not None:
                try:
                    if poison:
                        coordinator.poison(token)
                    elif failed_change:
                        coordinator.finish_change(token, "UNAVAILABLE")
                    else:
                        coordinator.finish_change_current(token)
                except Exception:
                    with self._lock:
                        self._poisoned = True
                    if not cleanup or not self._poison_for_cleanup(coordinator):
                        raise GatewayMarketDispatchError("MARKET_CLAIM_UNAVAILABLE") from None

    @staticmethod
    def _poison_for_cleanup(coordinator):
        """Close claims before allowing only physical shutdown or revocation."""
        from bt_api_py.gateway_dispatch_claim import DispatchClaimError

        try:
            coordinator.poison_domain("market")
            return True
        except DispatchClaimError as exc:
            # A closed coordinator cannot issue any more claims either.
            return exc.code == "COORDINATOR_CLOSED"
        except Exception:
            return False

    def _published_after_success(self, key, envelope):
        """Host hook invoked only after this exact PUB publisher returned success."""
        self._host()
        with self._lock:
            if not self._changing:
                raise GatewayMarketDispatchError("MARKET_HOOK_OUTSIDE_CHANGE")
            stream = self._hub._stream(key)
            header = envelope.header
            if (
                not stream.events
                or stream.events[-1].envelope is not envelope
                or header.stream_id != key.stream_id
                or header.stream_epoch != stream.epoch
                or header.sequence != stream.sequence
                or not _text(header.message_id)
            ):
                raise GatewayMarketDispatchError("MARKET_PUBLICATION_INVALID")
            token = (key, header.stream_epoch, header.sequence, header.message_id)
            self._published[token] = envelope
            self._published.move_to_end(token)
            while len(self._published) > self._limit:
                self._published.popitem(last=False)

    def _clock_after_source(
        self, registration_id, principal, key, observation, *, observed_monotonic_ns
    ):
        """Host hook invoked only with the exact return from its owner clock source."""
        self._host()
        if (
            not _text(registration_id)
            or not _text(principal)
            or type(key) is not MarketSubscriptionKey
        ):
            raise GatewayMarketDispatchError("MARKET_CLOCK_INVALID")
        if (
            type(observation) is not dict
            or type(observed_monotonic_ns) is not int
            or not 0 < observed_monotonic_ns <= time.monotonic_ns()
        ):
            raise GatewayMarketDispatchError("MARKET_CLOCK_INVALID")
        try:
            saved = copy.deepcopy(observation)
        except Exception:
            raise GatewayMarketDispatchError("MARKET_CLOCK_INVALID") from None
        with self._lock:
            if not self._changing:
                raise GatewayMarketDispatchError("MARKET_HOOK_OUTSIDE_CHANGE")
            token = (registration_id, principal, key)
            self._clocks[token] = (
                saved,
                observed_monotonic_ns,
                time.time_ns(),
                time.monotonic_ns(),
            )
            self._clocks.move_to_end(token)
            while len(self._clocks) > self._limit:
                self._clocks.popitem(last=False)

    def _check_grant_floor(self, principal, key, epoch, sequence):
        floor = self._hub._grant_floors.get((principal, key))
        if floor is not None and floor[0] == epoch and sequence <= floor[1]:
            raise GatewayMarketDispatchError("MARKET_AUTHORIZATION_FLOOR")

    def _published_tick(self, principal, key, registration_id, epoch, sequence, message_id):
        """Find the exact previously pushed frame on the Hub owner thread."""
        self._host()
        if (
            type(key) is not MarketSubscriptionKey
            or not _text(principal)
            or not _text(registration_id)
            or not _text(epoch)
            or type(sequence) is not int
            or sequence <= 0
            or not _text(message_id)
        ):
            raise GatewayMarketDispatchError("MARKET_REQUEST_INVALID")
        with self._lock:
            if self._poisoned:
                raise GatewayMarketDispatchError("MARKET_STATE_UNAVAILABLE")
            try:
                stream = self._hub._authorize_key(principal, key)
                if (
                    self._hub._closing
                    or self._hub._delivery_suspended
                    or registration_id not in stream.registrations
                    or self._hub._registration_principals.get((key, registration_id)) != principal
                    or stream.epoch != epoch
                    or stream.handle is None
                    or stream.generation is None
                    or stream.pending_release
                    or stream.starting
                    or stream.releasing
                    or stream.start_uncertain
                    or stream.needs_new_epoch
                ):
                    raise ValueError
                self._check_grant_floor(principal, key, epoch, sequence)
                envelope = self._published[(key, epoch, sequence, message_id)]
                if not any(entry.envelope is envelope for entry in stream.events):
                    raise ValueError
                payload = MarketTickPayload.from_dict(envelope.payload["data"])
                if (
                    envelope.payload.get("key") != key.to_dict()
                    or payload.instrument != key.instrument
                    or type(payload.provider_sequence) is not int
                    or payload.provider_sequence <= 0
                    or type(payload.provider_subscription_epoch) is not int
                    or payload.provider_subscription_epoch <= 0
                ):
                    raise ValueError
                return payload.provider_sequence, payload.provider_subscription_epoch
            except Exception:
                raise GatewayMarketDispatchError("MARKET_EVIDENCE_UNAVAILABLE") from None

    def issue(
        self,
        context,
        market_ref,
        *,
        market_registration_id,
        request_key,
        reservation_id,
        digest,
        delegation_permit=None,
    ):
        """Resolve a request's index to host-only facts and return a local permit."""
        self._host()
        if (
            type(context) is not ServiceCallContext
            or type(market_ref) is not dict
            or set(market_ref) != {"key", "epoch", "sequence", "message_id"}
            or not all(
                _text(v) for v in (market_registration_id, request_key, reservation_id, digest)
            )
            or len(digest) != 64
            or any(ch not in "0123456789abcdef" for ch in digest)
        ):
            raise GatewayMarketDispatchError("MARKET_REQUEST_INVALID")
        try:
            key = MarketSubscriptionKey.from_dict(market_ref["key"])
            epoch, sequence, message_id = (
                market_ref["epoch"],
                market_ref["sequence"],
                market_ref["message_id"],
            )
            if (
                not _text(epoch)
                or type(sequence) is not int
                or sequence <= 0
                or not _text(message_id)
            ):
                raise ValueError
        except Exception:
            raise GatewayMarketDispatchError("MARKET_REQUEST_INVALID") from None
        identity, scope = context.identity, context.scope
        if (
            identity.principal_ref != context.principal_ref
            or scope.account_ref != self._account
            or not _text(scope.strategy_ref)
            or not _text(scope.run_id)
            or scope.account_refs
            or scope.manual_book_ref is not None
            or scope.portfolio_ref is not None
            or scope.group_id is not None
            or scope.leg_id is not None
            or not _text(context.registration_id)
        ):
            raise GatewayMarketDispatchError("MARKET_SCOPE_DENIED")
        if identity.role is WireRole.STRATEGY_CLIENT:
            if (
                identity.actor_ref != scope.strategy_ref
                or identity.delegate_ref is not None
                or delegation_permit is not None
            ):
                raise GatewayMarketDispatchError("MARKET_SCOPE_DENIED")
        else:
            from bt_api_gateway.direct_delegation import GatewayDelegationPermit

            try:
                command = delegation_permit.command
                if (
                    type(delegation_permit) is not GatewayDelegationPermit
                    or identity.role not in (WireRole.SERVICE, WireRole.APPLICATION_CLIENT)
                    or delegation_permit.operation != "direct.dispatch"
                    or delegation_permit._context is not context
                    or command.actor_principal != context.principal_ref
                    or command.actor_ref != identity.actor_ref
                    or command.account_scope != self._account
                    or command.strategy_scope != scope.strategy_ref
                    or command.run_id != scope.run_id
                    or command.request_key != request_key
                    or command.payload_digest != digest
                    or command.instrument != key.instrument
                ):
                    raise ValueError
                # Parent authority and same-journal envelope are rechecked by
                # the owner inside the existing Registry -> journal fence.
                delegation_permit._check(None, verify_parent=False)
            except Exception:
                raise GatewayMarketDispatchError("MARKET_SCOPE_DENIED") from None
        with self._lock:
            if self._changing or self._poisoned:
                raise GatewayMarketDispatchError("MARKET_TRANSITION")
            self._current_stream(context, key, epoch, market_registration_id)
            self._check_grant_floor(context.principal_ref, key, epoch, sequence)
            event = self._published.get((key, epoch, sequence, message_id))
            clock_entry = self._clocks.get((market_registration_id, context.principal_ref, key))
            if event is None or clock_entry is None:
                raise GatewayMarketDispatchError("MARKET_EVIDENCE_UNAVAILABLE")
            obs, source_mono, clock_wall, clock_mono = clock_entry
            stream = self._hub._stream(key)
            if not any(entry.envelope is event for entry in stream.events):
                raise GatewayMarketDispatchError("MARKET_EVIDENCE_UNAVAILABLE")
            payload = MarketTickPayload.from_dict(event.payload["data"])
            descriptor = MarketSourceDescriptor.from_dict(obs.get("market_source"))
            if (
                event.payload.get("key") != key.to_dict()
                or key.instrument.provider != "ctp"
                or key.channel != "tick"
                or payload.instrument != key.instrument
                or descriptor.key != key
                or descriptor.epoch != epoch
                or payload.provider_source != descriptor.source
                or payload.provider_connection_generation != descriptor.connection_generation
                or payload.clock_domain_id != descriptor.clock_domain_id
                or payload.clock_source_session_id != descriptor.clock_source_session_id
                or payload.clock_source != descriptor.clock_source
                or payload.event_time_provenance != "provider_reported"
                or payload.continuity_status != "continuous"
                or obs.get("schema_version") != 2
                or obs.get("observation_kind") != "native_md_tick"
                or obs.get("provider") != key.instrument.provider
                or obs.get("account_scope") != self._account
                or obs.get("venue") != key.instrument.venue
                or obs.get("service_generation") != self._generation
                or obs.get("trading_day") != (payload.trading_date or "").replace("-", "")
                or type(obs.get("provider_sequence")) is not int
                or type(obs.get("provider_subscription_epoch")) is not int
                or obs.get("provider_sequence") != payload.provider_sequence
                or obs.get("provider_subscription_epoch") != payload.provider_subscription_epoch
                or obs.get("venue_observed_at_utc") != payload.provider_event_time_utc
                or type(obs.get("td_connection_generation")) is not int
                or obs["td_connection_generation"] <= 0
                or not _text(obs.get("td_source_ref"))
            ):
                raise GatewayMarketDispatchError("MARKET_SOURCE_MISMATCH")
            now_mono, now_wall = time.monotonic_ns(), time.time_ns()
            calibration = obs.get("source_calibration")
            if (
                now_mono < clock_mono
                or abs((now_wall - clock_wall) - (now_mono - clock_mono)) > 250_000_000
            ):
                raise GatewayMarketDispatchError("MARKET_CLOCK_INVALID")
            if (
                type(calibration) is not dict
                or calibration.get("schema_version") != 1
                or calibration.get("method") != "sntp"
                or any(
                    type(calibration.get(n)) is not int
                    for n in ("offset_ns", "error_bound_ns", "remaining_ns")
                )
                or not 0 <= calibration["error_bound_ns"] <= self._max_error_ns
                or type(obs.get("source_observed_wall_ns")) is not int
                or type(obs.get("source_event_received_wall_ns")) is not int
                or type(obs.get("remaining_ns")) is not int
            ):
                raise GatewayMarketDispatchError("MARKET_CLOCK_INVALID")
            elapsed = now_mono - source_mono
            if (
                elapsed < 0
                or abs((now_wall - obs["source_observed_wall_ns"]) - elapsed) > 250_000_000
            ):
                raise GatewayMarketDispatchError("MARKET_CLOCK_INVALID")
            quote_ns = _utc_ns(payload.provider_event_time_utc)
            # The native source sampled these wall and monotonic anchors at
            # the same instant. Advance that wall anchor with the same-process
            # monotonic clock; a sub-threshold wall step must never extend TTL.
            corrected_now = obs["source_observed_wall_ns"] + elapsed + calibration["offset_ns"]
            worst_age = corrected_now - quote_ns + calibration["error_bound_ns"]
            if (
                elapsed < 0
                or obs["source_event_received_wall_ns"] > obs["source_observed_wall_ns"]
                or worst_age < -(1_000_000_000 + calibration["error_bound_ns"])
            ):
                raise GatewayMarketDispatchError("MARKET_CLOCK_INVALID")
            remaining = min(
                self._latency_ns - worst_age,
                obs["remaining_ns"] - elapsed,
                calibration["remaining_ns"] - elapsed,
            )
            if remaining <= 0:
                # Timing diagnostics only (milliseconds; no account data).
                _LOG.debug(
                    "market gate quote expired: age_budget_ms=%d observation_ms=%d "
                    "calibration_ms=%d",
                    (self._latency_ns - worst_age) // 1_000_000,
                    (obs["remaining_ns"] - elapsed) // 1_000_000,
                    (calibration["remaining_ns"] - elapsed) // 1_000_000,
                )
                raise GatewayMarketDispatchError("MARKET_QUOTE_EXPIRED")
            facts = {
                name: obs[name]
                for name in (
                    "schema_version",
                    "observation_kind",
                    "provider",
                    "account_scope",
                    "venue",
                    "service_generation",
                    "td_connection_generation",
                    "td_source_ref",
                    "trading_day",
                    "market_source",
                    "provider_sequence",
                    "provider_subscription_epoch",
                    "venue_observed_at_utc",
                    "source_event_received_wall_ns",
                )
            }
            permit = _Permit(
                self._account,
                scope.strategy_ref,
                context.principal_ref,
                market_registration_id,
                request_key,
                reservation_id,
                digest,
                key,
                epoch,
                sequence,
                message_id,
                self._revision,
                now_mono + remaining,
                now_mono,
                now_wall,
                stream.handle,
                stream.generation,
                payload.provider_sequence,
                payload.provider_subscription_epoch,
                json.dumps(facts, sort_keys=True, separators=(",", ":")),
                delegation_permit,
                self._seal,
            )
            self._permits[id(permit)] = permit
            while len(self._permits) > self._limit:
                self._permits.popitem(last=False)
            return permit

    def _current_stream(self, context, key, epoch, market_registration_id):
        try:
            hub, service = self._hub, self._service
            stream = hub._authorize_key(context.principal_ref, key)
            registration = service._registrations[market_registration_id]
            scope = context.scope
            if (
                hub._closing
                or hub._delivery_suspended
                or stream.epoch != epoch
                or stream.handle is None
                or stream.generation is None
                or stream.pending_release
                or stream.starting
                or stream.releasing
                or stream.start_uncertain
                or stream.needs_new_epoch
                or market_registration_id not in stream.registrations
                or hub._registration_principals.get((key, market_registration_id))
                != context.principal_ref
                or registration.principal_ref != context.principal_ref
                or registration.account_ref != scope.account_ref
                or (
                    context.identity.role is WireRole.STRATEGY_CLIENT
                    and (
                        registration.strategy_ref != scope.strategy_ref
                        or registration.run_id != scope.run_id
                    )
                )
                or (
                    context.identity.role is not WireRole.STRATEGY_CLIENT
                    and (registration.strategy_ref is not None or registration.run_id is not None)
                )
                or registration.execution_domain_ref != scope.execution_domain_ref
            ):
                raise ValueError
        except Exception:
            raise GatewayMarketDispatchError("MARKET_SCOPE_DENIED") from None

    def _check_permit(self, permit, row, horizon_ns=0):
        if (
            os.getpid() != self._pid
            or type(permit) is not _Permit
            or permit.seal is not self._seal
            or self._permits.get(id(permit)) is not permit
            or type(row) is not DirectCommandRecord
            or self._changing
            or self._poisoned
            or permit.revision != self._revision
            or (
                row.account_scope,
                row.strategy_scope,
                row.principal,
                row.request_key,
                row.reservation_id,
                row.digest,
                row.generation,
            )
            != (
                permit.account,
                permit.strategy,
                permit.principal,
                permit.request_key,
                permit.reservation_id,
                permit.digest,
                self._generation,
            )
        ):
            raise GatewayMarketDispatchError("MARKET_PERMIT_INVALID")
        try:
            stream = self._hub._stream(permit.key)
            self._check_grant_floor(permit.principal, permit.key, permit.epoch, permit.sequence)
            event = self._published[(permit.key, permit.epoch, permit.sequence, permit.message_id)]
            if not any(entry.envelope is event for entry in stream.events):
                raise ValueError
        except Exception:
            raise GatewayMarketDispatchError("MARKET_EVIDENCE_UNAVAILABLE") from None
        try:
            payload = json.loads(row.payload_json)
            if (
                payload["symbol"] != permit.key.instrument.native_symbol
                or payload.get("exchange_id") != permit.key.instrument.venue
            ):
                raise ValueError
        except Exception:
            raise GatewayMarketDispatchError("MARKET_INSTRUMENT_MISMATCH") from None
        now_mono, now_wall = time.monotonic_ns(), time.time_ns()
        if (
            now_mono + horizon_ns >= permit.expires_ns
            or now_mono < permit.anchor_mono_ns
            or abs((now_wall - permit.anchor_wall_ns) - (now_mono - permit.anchor_mono_ns))
            > 250_000_000
        ):
            raise GatewayMarketDispatchError("MARKET_QUOTE_EXPIRED")

    def recheck(self, permit, row):
        """Recheck immediately before the journal marker or native call."""
        with self._lock:
            self._check_permit(permit, row)

    @contextmanager
    def dispatch_fence(self, permit, row):
        with self._lock:
            self._check_permit(permit, row)
            yield

    def _recheck_native_source(self, permit, horizon_ns=0):
        callback = self._native_source_check
        if callback is None:
            raise GatewayMarketDispatchError("MARKET_NATIVE_SOURCE_UNAVAILABLE")
        try:
            result = callback(
                permit.handle,
                key=permit.key,
                generation=permit.source_generation,
                stream_id=permit.key.stream_id,
                epoch=permit.epoch,
                provider_sequence=permit.provider_sequence,
                provider_subscription_epoch=permit.provider_subscription_epoch,
            )
            if (
                type(result) is not tuple
                or len(result) != 2
                or type(result[0]) is not dict
                or type(result[1]) is not int
            ):
                raise ValueError
            obs, source_mono = result
            original = json.loads(permit.source_facts_json)
            if any(
                type(obs.get(key)) is not type(value) or obs.get(key) != value
                for key, value in original.items()
            ):
                raise ValueError
            calibration = obs["source_calibration"]
            now_mono, now_wall = time.monotonic_ns(), time.time_ns()
            if (
                type(calibration) is not dict
                or calibration.get("schema_version") != 1
                or calibration.get("method") != "sntp"
                or any(
                    type(calibration.get(name)) is not int
                    for name in ("offset_ns", "error_bound_ns", "remaining_ns")
                )
                or not 0 <= calibration["error_bound_ns"] <= self._max_error_ns
                or type(obs.get("remaining_ns")) is not int
                or type(obs.get("source_observed_wall_ns")) is not int
                or not 0 < source_mono <= now_mono
            ):
                raise ValueError
            elapsed = now_mono - source_mono
            if (
                abs((now_wall - obs["source_observed_wall_ns"]) - elapsed) > 250_000_000
                or obs["remaining_ns"] <= elapsed + horizon_ns
                or calibration["remaining_ns"] <= elapsed + horizon_ns
            ):
                raise ValueError
            age = (
                obs["source_observed_wall_ns"]
                + elapsed
                + calibration["offset_ns"]
                - _utc_ns(obs["venue_observed_at_utc"])
            )
            if (
                age < -(1_000_000_000 + calibration["error_bound_ns"])
                or age + calibration["error_bound_ns"] + horizon_ns >= self._latency_ns
            ):
                raise ValueError
        except Exception:
            raise GatewayMarketDispatchError("MARKET_NATIVE_SOURCE_UNAVAILABLE") from None

    def matches_delegation(self, permit, delegation_permit):
        """Only the exact delegation object used to issue this permit may dispatch."""
        with self._lock:
            return (
                type(permit) is _Permit
                and permit.seal is self._seal
                and self._permits.get(id(permit)) is permit
                and permit.delegated is delegation_permit
            )

    def native_recheck(self, permit, row, *, horizon_ns=0):
        """Revalidate the exact live TD/MD source before the native claim.

        ``horizon_ns`` requires every time-limited input to remain valid for
        that many nanoseconds, covering the decision-B claim window.
        """
        if type(horizon_ns) is not int or not 0 <= horizon_ns <= 2_000_000_000:
            raise GatewayMarketDispatchError("MARKET_PERMIT_INVALID")
        with self._lock:
            self._check_permit(permit, row, horizon_ns)
            if permit.delegated is not None:
                try:
                    permit.delegated._check(None, verify_parent=False)
                except Exception:
                    raise GatewayMarketDispatchError("MARKET_DELEGATION_REVOKED") from None
            self._recheck_native_source(permit, horizon_ns)
            self._check_permit(permit, row, horizon_ns)

    def native_fence(self, permit, row):
        """Reject the former lock-across-native protocol under decision B.

        A caller can hold the context manager across an unbounded provider
        call.  That prevents market-state changes from completing and is not
        the shared, one-shot claim required by the current contract.  Market
        reading remains available; a complete replacement must additionally
        prove that every relevant writer participates in invalidation.
        """
        raise GatewayMarketDispatchError("MARKET_LEGACY_NATIVE_FENCE_UNAVAILABLE")
