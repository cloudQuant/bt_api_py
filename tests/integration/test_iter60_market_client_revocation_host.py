"""Iteration 60 WP6: runtime revocation end to end through a real ServiceHost process.

A spawned ``ServiceHost`` (CURVE on 127.0.0.1) runs the Gateway market hub with
its enforcing CURVE XPUB publisher (partitioned audience, market wire v2). The
test process runs the SDK public assembly - ``ServiceClientRuntime`` +
``ServiceMarketClient`` + ``ZmqMarketTransportFactory`` with its real
``ZmqMarketSubscriber`` - for one synthetic principal ("target") on one
synthetic market key. A second principal ("holder") keeps the shared upstream
active while the target is revoked (the hub releases an upstream nobody holds,
so no frame could be published otherwise) and is the positive control that
frames really are published during the revocation.

Revocation and re-grant are driven only through the host process's trusted
local control pipe calling ``GatewayMarketHub.revoke_principal_keys`` /
``grant_principal_keys``; no remote control surface is added. Market events
are synthetic; no provider, network, exchange or account is contacted.

Evidence level: INTEGRATION_SIM (local processes, loopback CURVE/ZMQ).
"""

from __future__ import annotations

import multiprocessing
import time
from contextlib import suppress
from datetime import UTC, datetime
from typing import Any
from uuid import uuid4

import pytest

zmq = pytest.importorskip("zmq")

from bt_api_base.market_protocol import (  # noqa: E402
    MarketInstrumentKey,
    MarketSubscriptionKey,
)
from bt_api_base.service_protocol import WireIdentity, WireRole, WireScope  # noqa: E402
from bt_api_gateway.market_hub import GatewayMarketHub  # noqa: E402

from bt_api_py.service_components import ComponentResources  # noqa: E402
from bt_api_py.service_config import parse_client_config, parse_service_config  # noqa: E402
from bt_api_py.service_host import ServiceHost, TrustedServiceAuthority  # noqa: E402
from bt_api_py.service_market import (  # noqa: E402
    ServiceMarketClient,
    ServiceMarketError,
    ZmqMarketTransportFactory,
)
from bt_api_py.service_registry import PrincipalGrant, ResourceGrant  # noqa: E402
from bt_api_py.service_runtime import (  # noqa: E402
    ClientCurveKeys,
    ServiceClientRuntime,
    ZmqTransportFactory,
)
from tests.unit._spawned_process_helpers import (  # noqa: E402
    _assert_no_fallback_cleanup,
    _bounded_reap_spawned_process,
    _close_normally,
    _receive,
    _start_process,
)
from tests.unit.test_service_market_topic_v2_integration import _free_endpoints  # noqa: E402

_SERVICE_ID = "iter60-revocation-market-service"
_SERVICE_INSTANCE = "iter60-revocation-market-hub"
_SERVICE_SCOPE_REF = "iter60-revocation-market-scope-v1"
_DOMAIN_REF = "paper:iter60-market-revocation"
_ACCOUNT_REF = "account:iter60-synthetic-market"
_TRANSPORT_REF = "iter60-revocation-market-v2"
_SECURITY_REF = "iter60-revocation-curve-v1"
_SERVICE_IDENTITY = WireIdentity(WireRole.SERVICE, "iter60-revocation-market", _SERVICE_ID)
_SERVICE_SCOPE = WireScope(_DOMAIN_REF)
_MARKET_OPERATIONS = frozenset(
    {
        "market.subscribe",
        "market.unsubscribe",
        "market.snapshot",
        "market.head",
        "market.read_after",
    }
)
_INSTRUMENT = MarketInstrumentKey(
    provider="synthetic",
    venue="loopback",
    environment="paper",
    product_type="spot",
    instrument_id="iter60-revocation-instrument",
    native_symbol="REVOKE-USD",
)
_KEY = MarketSubscriptionKey(
    source_ref="iter60-revocation-source",
    entitlement_ref="iter60-revocation-entitlement",
    instrument=_INSTRUMENT,
    channel="trades",
)
# Only in the key universe: no principal holds every key, so the hub requires
# the partitioned (enforcing XPUB) publisher that supports runtime revocation.
_UNGRANTED_KEY = MarketSubscriptionKey(
    source_ref="iter60-revocation-ungranted-source",
    entitlement_ref="iter60-revocation-entitlement",
    instrument=_INSTRUMENT,
    channel="trades",
)
_TARGET = ("principal:iter60-market-target", "strategy:iter60-market-target")
_HOLDER = ("principal:iter60-market-holder", "strategy:iter60-market-holder")
# Loaded-host latency budget for test configuration only (product checks are
# unchanged): the shared machine runs unrelated builds at load ~150-200.
_REQUEST_TIMEOUT_MS = 10_000
_HEAD_CHECK_SECONDS = 0.2
_WAIT_SECONDS = 90.0


def _service_config(rpc_bind: str, market_bind: str) -> dict[str, Any]:
    return {
        "service_config_schema_version": 1,
        "process_role": "service",
        "service_id": _SERVICE_ID,
        "scope_ref": _SERVICE_SCOPE_REF,
        "transport_ref": _TRANSPORT_REF,
        "registration_policy_ref": "iter60-revocation-registration-v1",
        "service_requirements_ref": "iter60-revocation-requirements-v1",
        "components": {
            "gateway": {
                "config_schema_version": 2,
                "settings": {
                    "instance_id": _SERVICE_INSTANCE,
                    "bind_ports": ["service.market_source"],
                    "options": {"role": "market_hub"},
                },
            }
        },
        "communication": {
            "config_schema_version": 1,
            "profiles": {
                _TRANSPORT_REF: {
                    "backend": "zmq",
                    "config_schema_version": 1,
                    "endpoints": {"rpc_bind": rpc_bind, "market_bind": market_bind},
                    "security_profile_ref": _SECURITY_REF,
                    "options": {
                        "high_water_mark": 256,
                        "max_message_bytes": 262_144,
                        "market_wire_version": 2,
                    },
                }
            },
        },
    }


def _client_config(rpc_connect: str, market_connect: str, strategy_ref: str) -> dict[str, Any]:
    return {
        "client_config_schema_version": 1,
        "process_role": "strategy_client",
        "registration": {"strategy_ref": strategy_ref},
        "clients": {
            "gateway": {
                "config_schema_version": 1,
                "settings": {
                    "bindings": [
                        {
                            "mount": "store.market",
                            "service_id": _SERVICE_ID,
                            "instance_id": _SERVICE_INSTANCE,
                            "scope_ref": _SERVICE_SCOPE_REF,
                            "transport_ref": _TRANSPORT_REF,
                        }
                    ],
                    "options": {
                        "request_timeout_ms": _REQUEST_TIMEOUT_MS,
                        "heartbeat_interval_seconds": 5,
                    },
                },
            }
        },
        "communication": {
            "config_schema_version": 1,
            "profiles": {
                _TRANSPORT_REF: {
                    "backend": "zmq",
                    "config_schema_version": 1,
                    "endpoints": {
                        "rpc_connect": rpc_connect,
                        "market_connect": market_connect,
                    },
                    "security_profile_ref": _SECURITY_REF,
                    "options": {
                        "request_timeout_ms": _REQUEST_TIMEOUT_MS,
                        "high_water_mark": 256,
                        "max_message_bytes": 262_144,
                        "market_wire_version": 2,
                    },
                }
            },
        },
    }


class _SyntheticProvider:
    """One synthetic upstream for ``_KEY``; acquire/release are counted."""

    def __init__(self) -> None:
        self.hub: GatewayMarketHub | None = None
        self.generation: str | None = None
        self.starts = 0
        self.stops = 0

    def subscribe(self, key: MarketSubscriptionKey, *, generation: str) -> object:
        if key != _KEY or not generation or self.generation is not None:
            raise AssertionError("the hub requested an undeclared synthetic stream")
        self.generation = generation
        self.starts += 1
        return ("iter60-synthetic", key.stream_id, generation)

    def unsubscribe(self, handle: object) -> bool:
        if not isinstance(handle, tuple) or handle[2] != self.generation:
            raise AssertionError("the hub released an unknown synthetic handle")
        self.generation = None
        self.stops += 1
        return True

    def publish(self, marker: str) -> tuple[str, int]:
        if self.hub is None or self.generation is None:
            raise AssertionError("the synthetic stream is not active")
        sent_at = datetime.now(UTC).isoformat(timespec="microseconds").replace("+00:00", "Z")
        event = self.hub.publish(_KEY, {"marker": marker}, sent_at, generation=self.generation)
        return event.header.stream_epoch, event.header.sequence


def _host_status(host: ServiceHost, hub: GatewayMarketHub) -> dict[str, Any]:
    publisher = host._market_publisher  # trusted in-process diagnostics only
    grants = publisher.enforced_stream_grants
    return {
        "head": dict(hub.head(_KEY)),
        "floor": hub.grant_floor(_TARGET[0], _KEY),
        "diagnostics": dict(hub.authorization_diagnostics),
        "delivery_suspended": hub.delivery_suspended,
        "reconfigurations": publisher.reconfigurations,
        "denied_subscriptions": publisher.denied_subscriptions,
        "enforced_grants": (
            None
            if grants is None
            else {principal: tuple(sorted(ids)) for principal, ids in grants.items()}
        ),
    }


def _market_host_process(control, raw_config, registry_path, server_keys, audience) -> None:
    """Spawned ServiceHost; ``audience`` = ((CURVE public key, principal, strategy), ...)."""

    host = None
    provider = _SyntheticProvider()
    try:
        config = parse_service_config(raw_config)
        grants = {
            principal: (
                PrincipalGrant(
                    WireIdentity(WireRole.STRATEGY_CLIENT, principal, strategy),
                    (
                        ResourceGrant(
                            _SERVICE_INSTANCE,
                            "service.market_source",
                            _SERVICE_SCOPE_REF,
                            WireScope(_DOMAIN_REF, account_ref=_ACCOUNT_REF, strategy_ref=strategy),
                            _MARKET_OPERATIONS,
                        ),
                    ),
                ),
            )
            for _public, principal, strategy in audience
        }
        authority = TrustedServiceAuthority(
            identity=_SERVICE_IDENTITY,
            scope=_SERVICE_SCOPE,
            scope_ref=config.scope_ref,
            scope_version="iter60-revocation-scope-v1",
            security_ref=_SECURITY_REF,
            security_version="curve-v1",
            registration_policy_ref=config.registration_policy_ref,
            registration_policy_version="policy-v1",
            service_requirements_ref=config.service_requirements_ref,
            service_requirements_version="requirements-v1",
            curve_public_key=server_keys[0],
            curve_secret_key=server_keys[1],
            principals={public: principal for public, principal, _strategy in audience},
            grants=grants,
        )
        hub = GatewayMarketHub(
            provider,
            key_universe=frozenset({_KEY, _UNGRANTED_KEY}),
            principal_allowed_keys={
                principal: frozenset({_KEY}) for _public, principal, _strategy in audience
            },
            service_identity=_SERVICE_IDENTITY,
            service_scope=_SERVICE_SCOPE,
        )
        provider.hub = hub

        def resources(component, _instance_id, _options, _scope):
            if component != "gateway":
                raise AssertionError("only the trusted market gateway component is expected")
            return ComponentResources.owned({"hub": hub}, ready=lambda: hub.ready, close=hub.close)

        host = ServiceHost(config, authority, registry_path, resources)
        host.start()
        control.send(("ready", host.endpoint, host.market_endpoint, _host_status(host, hub)))
        while True:
            host.serve_once(5)
            if not control.poll():
                continue
            command = control.recv()
            action = command[0]
            if action == "close":
                host.close()
                control.send(("closed", host.state, provider.starts, provider.stops))
                return
            try:
                if action == "publish":
                    epoch, sequence = provider.publish(command[1])
                    reply: tuple[Any, ...] = ("published", command[1], epoch, sequence)
                elif action == "revoke":
                    # Trusted local control: the owner thread calls the hub.
                    result = dict(hub.revoke_principal_keys(command[1]))
                    reply = ("revoked", result, _host_status(host, hub))
                elif action == "grant":
                    result = dict(hub.grant_principal_keys(command[1], {_KEY}))
                    reply = ("granted", result, _host_status(host, hub))
                elif action == "status":
                    reply = ("status", _host_status(host, hub))
                else:
                    raise AssertionError("unknown host command")
            except Exception as exc:
                reply = ("command-failed", action, getattr(exc, "code", type(exc).__name__))
            control.send(reply)
    except BaseException as exc:
        if host is not None and host.state not in {"closed", "failed"}:
            with suppress(BaseException):
                host.close()
        with suppress(Exception):
            control.send(("error", getattr(exc, "code", type(exc).__name__)))
        raise
    finally:
        control.close()


def _host(record, *command, timeout: float = 60.0) -> tuple[Any, ...]:
    assert record["process"].is_alive(), "the market host process exited"
    record["pipe"].send(command)
    reply = _receive(record["pipe"], timeout=timeout)
    assert reply[0] not in {"error", "command-failed"}, reply
    return reply


class _StrategyClient:
    """The SDK public market assembly for one principal, owned by this thread."""

    def __init__(self, principal_strategy, keys, server_public, endpoints) -> None:
        principal, strategy = principal_strategy
        public, secret = keys
        security = ClientCurveKeys(server_public, public, secret)
        self.runtime = ServiceClientRuntime(
            parse_client_config(_client_config(endpoints[0], endpoints[1], strategy)),
            identity=WireIdentity(WireRole.STRATEGY_CLIENT, principal, strategy),
            scope_resolver=lambda _scope_ref: WireScope(
                _DOMAIN_REF, account_ref=_ACCOUNT_REF, strategy_ref=strategy
            ),
            peer_resolver=lambda _service_id: _SERVICE_IDENTITY,
            transport_factory=ZmqTransportFactory({_SECURITY_REF: security}),
            run_id=uuid4().hex,
            requested_operations={"store.market": _MARKET_OPERATIONS},
        )
        self.market: ServiceMarketClient | None = None
        self.runtime.start()
        self.market = ServiceMarketClient(
            self.runtime,
            "store.market",
            _KEY,
            subscriber_factory=ZmqMarketTransportFactory({_SECURITY_REF: security}),
            stream_scope=_SERVICE_SCOPE,
            head_check_interval_seconds=_HEAD_CHECK_SECONDS,
        )
        # (epoch, sequence, marker) of every event ``poll`` delivered.
        self.delivered: list[tuple[str, int, str]] = []

    @property
    def markers(self) -> list[str]:
        return [marker for _epoch, _sequence, marker in self.delivered]

    def poll(self, timeout_ms: int = 10) -> None:
        event = self.market.poll(timeout_ms=timeout_ms)
        if event is not None:
            self.delivered.append(
                (event.header.stream_epoch, event.header.sequence, event.payload["data"]["marker"])
            )

    def keep_alive(self) -> None:
        delay = self.runtime.next_maintenance_delay()
        if delay is not None and delay <= 0:
            self.runtime.maintain()

    def close(self) -> None:
        try:
            if self.market is not None:
                self.market.close()
        finally:
            if self.runtime.state != "closed":
                self.runtime.close()


def _pump_until(predicate, *, poll=(), idle=(), timeout: float = _WAIT_SECONDS) -> None:
    deadline = time.monotonic() + timeout
    while not predicate():
        assert time.monotonic() < deadline, "bounded wait expired"
        for client in poll:
            client.poll()
        for client in idle:
            client.keep_alive()


def _publish(host_record, marker: str) -> tuple[str, int]:
    reply = _host(host_record, "publish", marker)
    assert reply[1] == marker, reply
    return reply[2], reply[3]


def _assert_interrupted(target: _StrategyClient) -> None:
    market = target.market
    assert market.authorization_interrupted == "MARKET_KEY_DENIED"
    assert market.healthy is False and market.recovery_required is True
    assert market.source_descriptor is None and market.queue_count == 0
    # The real ZMQ receiver was told: it drops every frame until the re-grant.
    assert market._subscriber.revoked == "MARKET_KEY_DENIED"
    for call in (
        lambda: market.poll(timeout_ms=0),
        lambda: market.drain(4),
        market.recover,
        lambda: market.accept_resync(acknowledge_data_loss=True),
    ):
        with pytest.raises(ServiceMarketError) as error:
            call()
        assert error.value.code == "MARKET_AUTHORIZATION_INTERRUPTED"
        assert error.value.cause_code == "MARKET_KEY_DENIED"


def _resync(target: _StrategyClient, host_record, *, generation: int, floor) -> Any:
    head_before = _host(host_record, "status")[1]["head"]
    bootstrap = target.market.resync_after_regrant()
    head_after = _host(host_record, "status")[1]["head"]
    assert bootstrap.resync_required is True
    assert bootstrap.cursor_epoch == floor[0] == head_after["epoch"]
    # Resumed from the hub cut at resync time, which is at/after the floor.
    assert head_before["head"] <= bootstrap.cursor_sequence <= head_after["head"]
    assert bootstrap.cursor_sequence >= floor[1]
    if bootstrap.snapshot is None:
        assert bootstrap.cursor_sequence == floor[1]  # the hub hid an older event
    else:
        assert bootstrap.snapshot.header.sequence == bootstrap.cursor_sequence > floor[1]
    market = target.market
    assert market.authorization_interrupted is None and market.healthy is True
    assert market.authorization_generation == generation
    receiver = market._subscriber
    assert receiver.revoked is None
    assert receiver.authorization_generation >= 1
    assert receiver._authorization_cut == (bootstrap.cursor_epoch, bootstrap.cursor_sequence)
    return bootstrap


@pytest.mark.integration
def test_revoked_principal_stops_delivery_and_resyncs_from_the_hub_cut_after_regrant(tmp_path):
    context = multiprocessing.get_context("spawn")
    rpc_bind, market_bind = _free_endpoints(2)
    server_public, server_secret = zmq.curve_keypair()
    target_keys = zmq.curve_keypair()
    holder_keys = zmq.curve_keypair()
    audience = (
        (target_keys[0], *_TARGET),
        (holder_keys[0], *_HOLDER),
    )
    forced: list[str] = []
    failures: list[str] = []
    host_record = _start_process(
        context,
        _market_host_process,
        (
            _service_config(rpc_bind, market_bind),
            str(tmp_path / "registry.sqlite3"),
            (server_public, server_secret),
            audience,
        ),
        "iter60-market-revocation-host",
    )
    holder: _StrategyClient | None = None
    target: _StrategyClient | None = None
    try:
        ready = _receive(host_record["pipe"], timeout=120.0)
        assert ready[0] == "ready", ready
        endpoints = (ready[1], ready[2])
        # The hub really is bound to the enforcing (partitioned) XPUB publisher.
        assert ready[3]["enforced_grants"] == {
            _TARGET[0]: (_KEY.stream_id,),
            _HOLDER[0]: (_KEY.stream_id,),
        }
        holder = _StrategyClient(_HOLDER, holder_keys, server_public, endpoints)
        holder.market.start()
        target = _StrategyClient(_TARGET, target_keys, server_public, endpoints)
        target.market.start()

        # -- 1. authorized: events flow to both principals ------------------
        published: dict[str, tuple[str, int]] = {}
        for index in range(1, 4):
            published[f"authorized-{index}"] = _publish(host_record, f"authorized-{index}")
        _pump_until(
            lambda: len(target.delivered) >= 3 and len(holder.delivered) >= 3,
            poll=(holder, target),
        )
        assert target.markers == ["authorized-1", "authorized-2", "authorized-3"]
        assert target.market.healthy is True
        assert target.market.authorization_interrupted is None
        epoch = published["authorized-1"][0]

        # Frames published while still authorized, left unread in the SUB socket.
        for index in range(1, 4):
            published[f"pre-revocation-{index}"] = _publish(host_record, f"pre-revocation-{index}")
        _pump_until(lambda: len(holder.delivered) >= 6, poll=(holder,), idle=(target,))

        # -- 2. the host revokes the target while it is subscribed ------------
        revoked = _host(host_record, "revoke", _TARGET[0])
        assert revoked[1]["transport_enforced"] is True
        assert revoked[1]["dropped_registrations"] == 1
        assert revoked[1]["revoked_stream_ids"] == (_KEY.stream_id,)
        status = revoked[2]
        assert status["enforced_grants"] == {_TARGET[0]: (), _HOLDER[0]: (_KEY.stream_id,)}
        assert status["delivery_suspended"] is False
        assert status["reconfigurations"] == 1
        revocation_head = status["head"]["head"]
        assert revocation_head == published["pre-revocation-3"][1]

        # Frames published under the revocation; the holder keeps receiving.
        revoked_markers: list[str] = []

        def publish_revoked() -> str:
            marker = f"revoked-{len(revoked_markers) + 1}"
            published[marker] = _publish(host_record, marker)
            revoked_markers.append(marker)
            return marker

        for _ in range(3):
            marker = publish_revoked()
            _pump_until(lambda m=marker: holder.markers[-1:] == [m], poll=(holder,), idle=(target,))

        # The target observes the revocation itself (denied head/replay RPC),
        # possibly after delivering the frames queued before the revocation.
        observed: list[ServiceMarketError] = []

        def observe() -> bool:
            if observed:
                return True
            try:
                target.poll()
            except ServiceMarketError as error:
                observed.append(error)
            return bool(observed)

        _pump_until(observe, poll=(holder,))
        assert observed[0].code == "MARKET_AUTHORIZATION_INTERRUPTED", observed
        assert observed[0].cause_code == "MARKET_KEY_DENIED"
        # Before observing it, the target delivered only frames of the old grant.
        assert all(sequence <= revocation_head for _e, sequence, _m in target.delivered)
        assert set(target.markers) <= {f"authorized-{i}" for i in range(1, 4)} | {
            f"pre-revocation-{i}" for i in range(1, 4)
        }
        delivered_before_observation = len(target.delivered)
        _assert_interrupted(target)

        # Transport enforcement on the target's real SUB socket: touching it
        # lets ZMQ attach the reconnected pipe and resend the subscription,
        # which the XPUB refuses; no frame published under the revocation ever
        # reaches the socket (the revoked receiver would count it as dropped).
        receiver = target.market._subscriber
        assert receiver.dropped_revoked == 0
        deadline = time.monotonic() + _WAIT_SECONDS
        refused_at = None
        while refused_at is None or len(revoked_markers) < refused_at + 3:
            assert time.monotonic() < deadline, "the XPUB never refused the revoked SUB"
            for _ in range(5):
                assert receiver.receive(timeout=0.02) is None
                holder.poll()
            target.keep_alive()
            marker = publish_revoked()
            _pump_until(lambda m=marker: holder.markers[-1:] == [m], poll=(holder,), idle=(target,))
            if refused_at is None and _host(host_record, "status")[1]["denied_subscriptions"] >= 1:
                refused_at = len(revoked_markers)
        for _ in range(10):
            assert receiver.receive(timeout=0.02) is None
        assert receiver.dropped_revoked == 0, "a frame published under revocation reached the SUB"

        # -- 3. resync while still revoked keeps the interruption --------------
        with pytest.raises(ServiceMarketError) as still_denied:
            target.market.resync_after_regrant()
        assert still_denied.value.code == "MARKET_AUTHORIZATION_INTERRUPTED"
        assert still_denied.value.cause_code == "MARKET_KEY_DENIED"
        _assert_interrupted(target)

        # -- 4. the host re-grants; frames before the cut never reach poll ----
        assert target.market._subscriber is receiver
        dropped_before_grant = receiver.dropped_revoked
        granted = _host(host_record, "grant", _TARGET[0])
        assert granted[1]["transport_enforced"] is True
        assert granted[1]["granted_stream_ids"] == (_KEY.stream_id,)
        floor = granted[2]["floor"]
        assert floor == (epoch, published[revoked_markers[-1]][1])
        assert granted[2]["enforced_grants"][_TARGET[0]] == (_KEY.stream_id,)
        # The re-granted XPUB delivers to the target's socket again, but its
        # still-revoked receiver drops those frames (probed on the real socket).
        between_markers: list[str] = []
        deadline = time.monotonic() + _WAIT_SECONDS
        while receiver.dropped_revoked <= dropped_before_grant:
            assert time.monotonic() < deadline, "no frame reached the re-granted SUB"
            marker = f"between-{len(between_markers) + 1}"
            published[marker] = _publish(host_record, marker)
            between_markers.append(marker)
            for _ in range(5):
                assert receiver.receive(timeout=0.02) is None
                holder.poll()
            target.keep_alive()
        # One more frame stays queued in the SUB socket across the resync.
        published["between-queued"] = _publish(host_record, "between-queued")
        between_markers.append("between-queued")
        _pump_until(lambda: holder.markers[-1:] == ["between-queued"], poll=(holder,))

        bootstrap = _resync(target, host_record, generation=1, floor=floor)
        cut = bootstrap.cursor_sequence
        assert cut == published["between-queued"][1]
        assert bootstrap.snapshot.payload["data"]["marker"] == "between-queued"

        # -- 5. new events flow again, strictly after the cut -----------------
        for index in range(1, 4):
            published[f"after-regrant-{index}"] = _publish(host_record, f"after-regrant-{index}")
        _pump_until(
            lambda: len(target.delivered) >= delivered_before_observation + 3,
            poll=(holder, target),
        )
        resumed = target.delivered[delivered_before_observation:]
        assert [marker for _e, _s, marker in resumed] == [
            "after-regrant-1",
            "after-regrant-2",
            "after-regrant-3",
        ]
        assert [sequence for _e, sequence, _m in resumed] == [cut + 1, cut + 2, cut + 3]
        assert all(epoch_ == epoch for epoch_, _s, _m in resumed)
        # The queued pre-cut frame is read and dropped as stale by the receiver
        # (SUB duplicates of replayed frames are dropped by the client cursor).
        _pump_until(lambda: receiver.dropped_stale_authorization >= 1, poll=(holder, target))
        assert target.delivered[delivered_before_observation:] == resumed
        assert target.market.healthy is True

        # -- 6. revoked again while healthy: ``recover`` itself is denied ------
        again = _host(host_record, "revoke", _TARGET[0])
        assert again[1]["transport_enforced"] is True
        assert again[1]["dropped_registrations"] == 1
        assert target.market.drain(16) == ()
        old_receiver = target.market._subscriber
        with pytest.raises(ServiceMarketError) as denied_recover:
            target.market.recover()
        assert denied_recover.value.code == "MARKET_AUTHORIZATION_INTERRUPTED"
        assert denied_recover.value.cause_code == "MARKET_KEY_DENIED"
        assert target.market._subscriber is not old_receiver  # recreated by recover
        _assert_interrupted(target)
        published["revoked-again-1"] = _publish(host_record, "revoked-again-1")
        _pump_until(lambda: holder.markers[-1:] == ["revoked-again-1"], poll=(holder,))
        with pytest.raises(ServiceMarketError, match="MARKET_AUTHORIZATION_INTERRUPTED"):
            target.market.resync_after_regrant()
        regranted = _host(host_record, "grant", _TARGET[0])
        assert regranted[1]["transport_enforced"] is True
        second_floor = regranted[2]["floor"]
        assert second_floor == (epoch, published["revoked-again-1"][1])
        second = _resync(target, host_record, generation=2, floor=second_floor)
        published["final-1"] = _publish(host_record, "final-1")
        _pump_until(lambda: target.markers[-1:] == ["final-1"], poll=(holder, target))
        assert target.delivered[-1][1] == second.cursor_sequence + 1

        # -- never delivered: anything published from revocation to each cut --
        never = set(revoked_markers) | set(between_markers) | {"revoked-again-1"}
        assert never.isdisjoint(target.markers), target.markers
        assert all(
            sequence <= revocation_head or sequence > cut for _e, sequence, _m in target.delivered
        )
        # Positive control: the holder received every frame exactly once, in order.
        _pump_until(lambda: holder.markers[-1:] == ["final-1"], poll=(holder,), idle=(target,))
        head = _host(host_record, "status")[1]
        assert [sequence for _e, sequence, _m in holder.delivered] == list(
            range(1, head["head"]["head"] + 1)
        )
        diagnostics = head["diagnostics"]
        assert diagnostics["authorization_revocations"] == 2
        assert diagnostics["authorization_grants"] == 2
        assert diagnostics["revoked_registrations"] == 2
        assert diagnostics["publisher_regrant_failures"] == 0
        assert head["reconfigurations"] == 4
        assert head["delivery_suspended"] is False

        target.close()
        target = None
        holder.close()
        holder = None
        closed = _close_normally(host_record, expected_tag="closed")
        assert closed[1] == "closed"
        assert closed[2] == closed[3] == 1  # one shared upstream, released once
    finally:
        for client in (target, holder):
            if client is not None:
                with suppress(Exception):
                    client.close()
        _bounded_reap_spawned_process(host_record, forced, failures)
        _assert_no_fallback_cleanup(forced, failures)
