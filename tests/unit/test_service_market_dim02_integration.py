"""DIM02 increment-1: multi-key market subscriptions through the real spawn chain.

Scope (formal plan line 774, sub-items 1 and 4; recon §5.1):

* One contract over several channels/timeframes (``trades`` + ``bar/1m`` +
  ``bar/5m`` of one instrument) plus a sparse three-strike option chain where
  only one strike receives data, crossed with three spawned strategy clients.
* Per-key provider upstream counts: the same key with N strategies stays ONE
  upstream; distinct keys get one acquisition each.
* Dual-level merge counting (line 149-151 semantics, recon §6.2 coordinator
  decision): the hub is NOT changed and still subscribes per key; the merge of
  "fully compatible" subscriptions is modeled as a TEST-DECLARED adapter
  contract — the two bar timeframes of one instrument share ONE underlying
  connection, opened by the first member and closed when the last member is
  released. Both levels are counted (per-key acquisitions AND per-group
  connections).
* Per-key projection independence: stream_id / epoch / head / cursor /
  observed_at never cross keys.
* Cross-key zero cross-stream: an event published for key A never materializes
  on any client of key B.
* Silent keys (the two option strikes that receive no data): head 0, observed
  None, no fabricated events or freshness anywhere on the chain.
* Per-key last-release semantics: each key's upstream starts exactly once and
  stops exactly once, when its own last subscriber exits.
* The sparse-chain join/projection is implemented TEST-SIDE (join across the
  per-key hub heads) because production has no chain aggregation; nothing
  production-side is claimed by this file.

This file is intentionally self-contained (adapted from the GW01 fixture in
``tests/unit/test_service_market_integration.py``, generalized from its
single-key hardcoded shape). The GW01 delivery nodes are NOT re-run here.
"""

from __future__ import annotations

import multiprocessing
import socket
import time
from collections.abc import Mapping
from contextlib import suppress
from datetime import UTC, datetime, timedelta
from typing import Any

import pytest
import zmq
from bt_api_base.market_protocol import MarketInstrumentKey, MarketSubscriptionKey
from bt_api_base.service_protocol import WireIdentity, WireRole, WireScope
from bt_api_gateway.market_hub import GatewayMarketHub

from bt_api_py.service_components import ComponentResources
from bt_api_py.service_config import parse_client_config, parse_service_config
from bt_api_py.service_host import ServiceHost, TrustedServiceAuthority
from bt_api_py.service_market import ServiceMarketClient, ZmqMarketTransportFactory
from bt_api_py.service_registry import PrincipalGrant, ResourceGrant
from bt_api_py.service_runtime import (
    ClientCurveKeys,
    ServiceClientRuntime,
    ZmqTransportFactory,
)

_SERVICE_ID = "synthetic-market-service"
_SERVICE_INSTANCE = "synthetic-market-hub"
_SERVICE_SCOPE_REF = "synthetic-market-public-scope-v1"
_DOMAIN_REF = "paper:market-integration"
_ACCOUNT_REF = "account:synthetic-market-gw01"
_TRANSPORT_REF = "market-listener-v1"
_SECURITY_REF = "market-curve-v1"
_MARKET_OPERATIONS = frozenset(
    {
        "market.subscribe",
        "market.unsubscribe",
        "market.snapshot",
        "market.head",
        "market.read_after",
    }
)
_REQUEST_TIMEOUT_MS = 2_000
_HEARTBEAT_SECONDS = 5
_PROVIDER_REF = "counted-multi-key-provider"
_HEAD_CHECK_SECONDS = 2.0
_COLLECT_SECONDS = 6.0
_SWEEP_SECONDS = 0.6

_SOURCE_REF = "synthetic-market-source"
_ENTITLEMENT_REF = "synthetic-public-entitlement"


def _option_instrument(strike: int) -> MarketInstrumentKey:
    return MarketInstrumentKey(
        provider="synthetic",
        venue="loopback",
        environment="paper",
        product_type="option",
        instrument_id=f"synthetic-option-SYNTH-2612-C{strike}",
        native_symbol=f"SYNTH-2612-C-{strike}",
    )


# Key order is the addressing order used by every command and assertion below.
_MARKET_KEYS: tuple[MarketSubscriptionKey, ...] = (
    MarketSubscriptionKey(
        source_ref=_SOURCE_REF,
        entitlement_ref=_ENTITLEMENT_REF,
        instrument=MarketInstrumentKey(
            provider="synthetic",
            venue="loopback",
            environment="paper",
            product_type="spot",
            instrument_id="synthetic-instrument-1",
            native_symbol="SYNTH-USD",
        ),
        channel="trades",
    ),
    MarketSubscriptionKey(
        source_ref=_SOURCE_REF,
        entitlement_ref=_ENTITLEMENT_REF,
        instrument=MarketInstrumentKey(
            provider="synthetic",
            venue="loopback",
            environment="paper",
            product_type="spot",
            instrument_id="synthetic-instrument-1",
            native_symbol="SYNTH-USD",
        ),
        channel="bar",
        timeframe="1m",
    ),
    MarketSubscriptionKey(
        source_ref=_SOURCE_REF,
        entitlement_ref=_ENTITLEMENT_REF,
        instrument=MarketInstrumentKey(
            provider="synthetic",
            venue="loopback",
            environment="paper",
            product_type="spot",
            instrument_id="synthetic-instrument-1",
            native_symbol="SYNTH-USD",
        ),
        channel="bar",
        timeframe="5m",
    ),
    MarketSubscriptionKey(
        source_ref=_SOURCE_REF,
        entitlement_ref=_ENTITLEMENT_REF,
        instrument=_option_instrument(105),
        channel="quotes",
    ),
    MarketSubscriptionKey(
        source_ref=_SOURCE_REF,
        entitlement_ref=_ENTITLEMENT_REF,
        instrument=_option_instrument(110),
        channel="quotes",
    ),
    MarketSubscriptionKey(
        source_ref=_SOURCE_REF,
        entitlement_ref=_ENTITLEMENT_REF,
        instrument=_option_instrument(115),
        channel="quotes",
    ),
)
_KEY_COUNT = len(_MARKET_KEYS)
assert len({key.stream_id for key in _MARKET_KEYS}) == _KEY_COUNT

# Sparse option chain: strike index -> strike value. Only strike 105 (key 3)
# receives data; strikes 110/115 stay subscribed but silent.
_OPTION_STRIKES = {3: 105, 4: 110, 5: 115}

# Three strategies over partly different key subsets. Key -> subscriber count:
# 0:3, 1:3, 2:3, 3:2, 4:1, 5:1 (registration refs total 13).
_STRATEGY_KEY_INDICES: tuple[tuple[int, ...], ...] = (
    (0, 1, 2, 3, 4),
    (0, 1, 2, 3, 5),
    (0, 1, 2),
)

# Test-declared adapter merge contract (hub unchanged, recon §6.2). Fully
# compatible subscriptions — same instrument, same bar channel, different
# timeframes — share ONE underlying connection: it opens with the first member
# acquisition and closes with the last member release. Different option strikes
# have different instruments and are therefore NOT merged.
_MERGE_GROUPS: tuple[tuple[str, tuple[int, ...]], ...] = (
    ("connection:spot-trades", (0,)),
    ("connection:spot-bars", (1, 2)),
    ("connection:option-C105", (3,)),
    ("connection:option-C110", (4,)),
    ("connection:option-C115", (5,)),
)

_OBSERVED_ORIGIN = datetime(2026, 1, 2, 3, 4, 5, tzinfo=UTC)


def _observed_at(key_index: int, key_sequence: int) -> str:
    """Deterministic source timestamp: one distinct hour band per key index.

    The per-key hour offset makes any cross-key bleed of observed_at values
    immediately visible, and the value is invented by the provider only — the
    service and the SDK must pass it through unchanged (no local freshness).
    """
    moment = _OBSERVED_ORIGIN + timedelta(hours=key_index, seconds=key_sequence)
    return moment.isoformat(timespec="microseconds").replace("+00:00", "Z")


def _free_endpoints(count: int) -> tuple[str, ...]:
    sockets: list[socket.socket] = []
    try:
        endpoints = []
        for _ in range(count):
            probe = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            probe.bind(("127.0.0.1", 0))
            sockets.append(probe)
            endpoints.append(f"tcp://127.0.0.1:{probe.getsockname()[1]}")
        return tuple(endpoints)
    finally:
        for probe in sockets:
            probe.close()


def _service_config(rpc_bind: str, market_bind: str) -> dict[str, Any]:
    return {
        "service_config_schema_version": 1,
        "process_role": "service",
        "service_id": _SERVICE_ID,
        "scope_ref": _SERVICE_SCOPE_REF,
        "transport_ref": _TRANSPORT_REF,
        "registration_policy_ref": "synthetic-market-registration-v1",
        "service_requirements_ref": "synthetic-market-requirements-v1",
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
                        "high_water_mark": 128,
                        "max_message_bytes": 262_144,
                    },
                }
            },
        },
    }


def _client_config(
    rpc_connect: str,
    market_connect: str,
    strategy_ref: str,
) -> dict[str, Any]:
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
                            "transport_ref": "market-client-v1",
                        }
                    ],
                    "options": {
                        "request_timeout_ms": _REQUEST_TIMEOUT_MS,
                        "heartbeat_interval_seconds": _HEARTBEAT_SECONDS,
                    },
                },
            }
        },
        "communication": {
            "config_schema_version": 1,
            "profiles": {
                "market-client-v1": {
                    "backend": "zmq",
                    "config_schema_version": 1,
                    "endpoints": {
                        "rpc_connect": rpc_connect,
                        "market_connect": market_connect,
                    },
                    "security_profile_ref": _SECURITY_REF,
                    "options": {
                        "request_timeout_ms": _REQUEST_TIMEOUT_MS,
                        "high_water_mark": 128,
                        "max_message_bytes": 262_144,
                    },
                }
            },
        },
    }


class _CountedMultiKeyProvider:
    """Deterministic multi-key source counting per-key and per-connection use.

    Per-key counters are what the hub itself drives (one ``subscribe`` per key,
    shared by every registration ref on that key). Group counters model the
    test-declared adapter contract above: compatible members share one
    underlying connection. A duplicate acquisition of a still-held key, an
    unknown handle, or a connection lifecycle mismatch fails loud because the
    real chain must never duplicate an upstream.
    """

    def __init__(
        self,
        keys: tuple[MarketSubscriptionKey, ...],
        merge_groups: tuple[tuple[str, tuple[int, ...]], ...],
    ) -> None:
        self.keys = tuple(keys)
        self._index_by_key = {key: index for index, key in enumerate(self.keys)}
        self._index_by_stream_id = {key.stream_id: index for index, key in enumerate(self.keys)}
        self._group_of: dict[int, str] = {}
        self._group_members: dict[str, tuple[int, ...]] = {}
        for group_id, member_indices in merge_groups:
            self._group_members[group_id] = tuple(member_indices)
            for index in member_indices:
                self._group_of[index] = group_id
        if sorted(self._group_of) != list(range(len(self.keys))):
            raise AssertionError("each key needs exactly one declared merge group")
        self.key_starts = {key.stream_id: 0 for key in self.keys}
        self.key_stops = {key.stream_id: 0 for key in self.keys}
        self.connection_starts = {group_id: 0 for group_id, _members in merge_groups}
        self.connection_stops = {group_id: 0 for group_id, _members in merge_groups}
        self.open_connections: set[str] = set()
        self.generations: dict[str, str | None] = {key.stream_id: None for key in self.keys}
        self.published = {key.stream_id: 0 for key in self.keys}
        self.hub: GatewayMarketHub | None = None

    def subscribe(self, key: MarketSubscriptionKey, *, generation: str) -> object:
        if not generation:
            raise AssertionError("the hub must provide a nonempty source generation")
        index = self._index_by_key.get(key)
        if index is None:
            raise AssertionError("provider received an unknown synthetic key")
        stream_id = key.stream_id
        if self.generations[stream_id] is not None:
            raise AssertionError("the hub started a duplicate upstream for one key")
        self.generations[stream_id] = generation
        self.key_starts[stream_id] += 1
        group_id = self._group_of[index]
        if group_id not in self.open_connections:
            self.open_connections.add(group_id)
            self.connection_starts[group_id] += 1
        return (stream_id, generation)

    def unsubscribe(self, handle: object) -> bool:
        if not isinstance(handle, tuple) or len(handle) != 2:
            raise AssertionError("provider received an unknown synthetic handle")
        stream_id, generation = handle
        if self.generations.get(stream_id) != generation:
            raise AssertionError("provider received an unknown synthetic handle")
        self.generations[stream_id] = None
        self.key_stops[stream_id] += 1
        group_id = self._group_of[self._index_by_stream_id[stream_id]]
        if any(
            self.generations[self.keys[member].stream_id] is not None
            for member in self._group_members[group_id]
        ):
            if group_id not in self.open_connections:
                raise AssertionError("compatible connection missing while a member is held")
        else:
            if group_id not in self.open_connections:
                raise AssertionError("compatible connection closed twice")
            self.open_connections.discard(group_id)
            self.connection_stops[group_id] += 1
        return True

    def publish(
        self, index: int, marker: str, payload_extra: Mapping[str, Any]
    ) -> tuple[int, str, int, str, str, str]:
        key = self.keys[index]
        stream_id = key.stream_id
        generation = self.generations[stream_id]
        if self.hub is None or generation is None:
            raise AssertionError("synthetic hub is not attached to an active key")
        if not isinstance(payload_extra, Mapping):
            raise AssertionError("publish payload extra must be a mapping")
        sequence = self.published[stream_id] + 1
        observed_at = _observed_at(index, sequence)
        data = {"marker": marker, "source": _PROVIDER_REF}
        data.update(payload_extra)
        event = self.hub.publish(key, data, observed_at, generation=generation)
        self.published[stream_id] = sequence
        return (
            index,
            marker,
            event.header.sequence,
            stream_id,
            event.header.stream_epoch,
            observed_at,
        )

    def counts(self) -> dict[str, Any]:
        return {
            "key_starts": dict(self.key_starts),
            "key_stops": dict(self.key_stops),
            "connection_starts": dict(self.connection_starts),
            "connection_stops": dict(self.connection_stops),
            "open_connections": tuple(sorted(self.open_connections)),
        }


def _service_host_process(
    control,
    raw_config,
    registry_path,
    server_keys,
    client_audience,
    key_dicts,
):
    keys = tuple(MarketSubscriptionKey.from_dict(item) for item in key_dicts)
    config = parse_service_config(raw_config)
    service_identity = WireIdentity(WireRole.SERVICE, "synthetic-market-service", _SERVICE_ID)
    service_scope = WireScope(_DOMAIN_REF)
    principals = {public_key: principal_ref for public_key, principal_ref, _ in client_audience}
    grants = {
        principal_ref: (
            PrincipalGrant(
                WireIdentity(WireRole.STRATEGY_CLIENT, principal_ref, strategy_ref),
                (
                    ResourceGrant(
                        _SERVICE_INSTANCE,
                        "service.market_source",
                        _SERVICE_SCOPE_REF,
                        WireScope(
                            _DOMAIN_REF,
                            account_ref=_ACCOUNT_REF,
                            strategy_ref=strategy_ref,
                        ),
                        _MARKET_OPERATIONS,
                    ),
                ),
            ),
        )
        for _public_key, principal_ref, strategy_ref in client_audience
    }
    authority = TrustedServiceAuthority(
        identity=service_identity,
        scope=service_scope,
        scope_ref=config.scope_ref,
        scope_version="synthetic-market-scope-v1",
        security_ref=_SECURITY_REF,
        security_version="curve-v1",
        registration_policy_ref=config.registration_policy_ref,
        registration_policy_version="policy-v1",
        service_requirements_ref=config.service_requirements_ref,
        service_requirements_version="requirements-v1",
        curve_public_key=server_keys[0],
        curve_secret_key=server_keys[1],
        principals=principals,
        grants=grants,
    )
    provider = _CountedMultiKeyProvider(keys, _MERGE_GROUPS)
    hub = GatewayMarketHub(
        provider,
        key_universe=frozenset(keys),
        # The PUB endpoint exposes one shared audience, so every authorized
        # principal must be allowed the entire exported key universe. A valid
        # single endpoint cannot model per-principal stream isolation here;
        # per-key isolation is asserted per key at the SDK projection instead.
        principal_allowed_keys={
            principal_ref: frozenset(keys)
            for _public_key, principal_ref, _strategy_ref in client_audience
        },
        service_identity=service_identity,
        service_scope=service_scope,
    )
    provider.hub = hub

    def resources(component, _instance_id, _options, _scope):
        if component != "gateway":
            raise AssertionError("only the trusted market gateway component is expected")
        return ComponentResources.owned(
            {"hub": hub},
            ready=lambda: hub.ready,
            close=hub.close,
        )

    host = ServiceHost(config, authority, registry_path, resources)
    try:
        host.start()
        control.send(("ready", host.endpoint, host.market_endpoint))
        while True:
            host.serve_once(10)
            if not control.poll():
                continue
            command = control.recv()
            action = command[0]
            if action == "status":
                heads = []
                for index, key in enumerate(keys):
                    state = hub.head(key)
                    heads.append(
                        (
                            index,
                            state["stream_id"],
                            state["epoch"],
                            state["head"],
                            state["latest_observed_at_utc"],
                        )
                    )
                control.send(("status", dict(hub.readiness), provider.counts(), tuple(heads)))
            elif action == "publish":
                result = provider.publish(command[1], command[2], command[3])
                control.send(("published", *result))
            elif action == "close":
                host.close()
                control.send(("closed", host.state, provider.counts()))
                return
            else:
                raise AssertionError("unknown host command")
    except BaseException as exc:
        if host.state not in {"closed", "failed"}:
            with suppress(BaseException):
                host.close()
        with suppress(Exception):
            control.send(("error", getattr(exc, "code", type(exc).__name__)))
        raise
    finally:
        control.close()


def _event_tuple(event) -> tuple[str, str, int, dict[str, Any], dict[str, Any]]:
    def plain(value):
        if isinstance(value, Mapping):
            return {key: plain(item) for key, item in value.items()}
        if isinstance(value, (tuple, list)):
            return tuple(plain(item) for item in value)
        return value

    return (
        event.header.stream_id,
        event.header.stream_epoch,
        event.header.sequence,
        plain(event.payload["key"]),
        plain(event.payload["data"]),
    )


def _strategy_client_process(
    control,
    principal_ref,
    strategy_ref,
    raw_config,
    curve_keys,
    service_identity,
    key_dicts,
    subscribed_indices,
):
    keys = tuple(MarketSubscriptionKey.from_dict(item) for item in key_dicts)
    config = parse_client_config(raw_config)
    identity = WireIdentity(WireRole.STRATEGY_CLIENT, principal_ref, strategy_ref)

    def scope_resolver(_scope_ref: str) -> WireScope:
        return WireScope(
            _DOMAIN_REF,
            account_ref=_ACCOUNT_REF,
            strategy_ref=strategy_ref,
        )

    security = ClientCurveKeys(curve_keys[0], curve_keys[1], curve_keys[2])
    runtime = ServiceClientRuntime(
        config,
        identity=identity,
        scope_resolver=scope_resolver,
        peer_resolver=lambda _service_id: service_identity,
        transport_factory=ZmqTransportFactory({_SECURITY_REF: security}),
        requested_operations={"store.market": _MARKET_OPERATIONS},
    )
    market_clients: dict[int, ServiceMarketClient] = {}
    pending_events: dict[int, list[tuple[str, str, int, dict[str, Any], dict[str, Any]]]] = {}
    try:
        runtime.start()
        bootstraps = []
        for key_index in subscribed_indices:
            market = ServiceMarketClient(
                runtime,
                "store.market",
                keys[key_index],
                subscriber_factory=ZmqMarketTransportFactory({_SECURITY_REF: security}),
                stream_scope=WireScope(_DOMAIN_REF),
                head_check_interval_seconds=_HEAD_CHECK_SECONDS,
            )
            bootstrap = market.start()
            market_clients[key_index] = market
            pending_events[key_index] = []
            bootstraps.append(
                (
                    key_index,
                    bootstrap.stream_id,
                    bootstrap.snapshot_kind,
                    bootstrap.snapshot_complete,
                    bootstrap.full_baseline_available,
                    bootstrap.snapshot_epoch,
                    bootstrap.snapshot_sequence,
                    bootstrap.cursor_epoch,
                    bootstrap.cursor_sequence,
                )
            )
        registration_id = runtime.bindings[0].registration_id
        control.send(
            (
                "ready",
                registration_id,
                runtime.bindings[0].scope.account_ref,
                tuple(bootstraps),
            )
        )

        def service_once(timeout_ms: int) -> None:
            delay = runtime.next_maintenance_delay()
            if delay is not None and delay <= 0:
                runtime.maintain()
            share = max(0, timeout_ms // len(market_clients))
            for key_index, market in market_clients.items():
                event = market.poll(timeout_ms=share)
                if event is not None:
                    pending_events[key_index].append(_event_tuple(event))

        while True:
            service_once(0)
            if not control.poll(0.005):
                continue
            command = control.recv()
            action = command[0]
            if action == "collect":
                key_index, marker, seconds = command[1], command[2], command[3]
                deadline = time.monotonic() + seconds
                while time.monotonic() < deadline and not any(
                    item[4].get("marker") == marker
                    for item in pending_events.get(key_index, ())
                ):
                    service_once(10)
                selected = [
                    item
                    for item in pending_events.get(key_index, ())
                    if item[4].get("marker") == marker
                ]
                pending_events[key_index] = [
                    item
                    for item in pending_events.get(key_index, ())
                    if item[4].get("marker") != marker
                ]
                control.send(("events", key_index, marker, tuple(selected)))
            elif action == "sweep":
                # Bounded pump proving a marker does NOT materialize elsewhere:
                # returns every event across all subscribed keys whose data
                # carries the marker, plus the remaining queue depth.
                marker, seconds = command[1], command[2]
                deadline = time.monotonic() + seconds
                while time.monotonic() < deadline:
                    service_once(10)
                matches = []
                for key_index in tuple(pending_events):
                    kept = []
                    for item in pending_events[key_index]:
                        if item[4].get("marker") == marker:
                            matches.append((key_index, item))
                        else:
                            kept.append(item)
                    pending_events[key_index] = kept
                remaining = sum(len(items) for items in pending_events.values())
                control.send(("swept", marker, tuple(matches), remaining))
            elif action == "drain":
                for key_index in pending_events:
                    pending_events[key_index] = []
                control.send(("drained", principal_ref))
            elif action == "release":
                healthy = []
                for key_index, market in market_clients.items():
                    market.close()
                    healthy.append((key_index, market.healthy))
                runtime.close()
                control.send(("closed", principal_ref, runtime.state, tuple(healthy)))
                return
            else:
                raise AssertionError("unknown client command")
    except BaseException as exc:
        with suppress(Exception):
            control.send(("error", principal_ref, getattr(exc, "code", type(exc).__name__)))
        raise
    finally:
        control.close()


def _start_process(context, target, args):
    parent, child = context.Pipe(duplex=True)
    process = context.Process(target=target, args=(child, *args))
    process.start()
    child.close()
    return process, parent


def _receive(pipe, timeout: float = 15.0):
    assert pipe.poll(timeout), "spawned market process did not answer within its bound"
    return pipe.recv()


def _host_command(pipe, *command):
    pipe.send(command)
    result = _receive(pipe)
    assert result[0] != "error", result
    return result


def _client_command(record, *command, timeout: float = 10.0):
    process, pipe = record["process"], record["pipe"]
    assert process.is_alive()
    pipe.send(command)
    result = _receive(pipe, timeout)
    assert result[0] != "error", result
    return result


def _join_normally(process, pipe, *, expected_exitcode: int = 0) -> None:
    process.join(15)
    assert not process.is_alive(), "spawned market process did not exit within its bound"
    assert process.exitcode == expected_exitcode
    pipe.close()
    process.close()


def _bounded_reap_spawned_process(process, pipe, label: str, forced, failures) -> None:
    """Reap only a process created by this test, recording forced fallback."""

    def alive() -> bool:
        try:
            return process.is_alive()
        except Exception as exc:
            failures.append(f"{label}:state:{type(exc).__name__}")
            return False

    try:
        process.join(5)
    except Exception as exc:
        failures.append(f"{label}:normal_join:{type(exc).__name__}")
    if alive():
        forced.append(label)
        try:
            process.terminate()
        except Exception as exc:
            failures.append(f"{label}:terminate:{type(exc).__name__}")
        try:
            process.join(5)
        except Exception as exc:
            failures.append(f"{label}:terminate_join:{type(exc).__name__}")
    if alive():
        try:
            process.kill()
        except Exception as exc:
            failures.append(f"{label}:kill:{type(exc).__name__}")
        try:
            process.join(5)
        except Exception as exc:
            failures.append(f"{label}:kill_join:{type(exc).__name__}")

    is_alive = alive()
    try:
        pipe.close()
    except Exception as exc:
        failures.append(f"{label}:pipe_close:{type(exc).__name__}")
    if is_alive:
        failures.append(f"{label}:still_alive_after_kill")
        return
    try:
        process.close()
    except Exception as exc:
        failures.append(f"{label}:process_close:{type(exc).__name__}")


def _assert_no_fallback_cleanup(forced, failures) -> None:
    if forced or failures:
        raise AssertionError(
            f"spawned integration cleanup required fallback: forced={tuple(forced)}, "
            f"failures={tuple(failures)}"
        )


def _host_status(pipe) -> tuple[Mapping[str, Any], Mapping[str, Any], tuple]:
    result = _host_command(pipe, "status")
    assert result[0] == "status", result
    return result[1], result[2], result[3]


def _live_key_indices(live_strategy_indices) -> frozenset[int]:
    return frozenset(
        key_index
        for strategy_index in live_strategy_indices
        for key_index in _STRATEGY_KEY_INDICES[strategy_index]
    )


def _subscription_refs(live_strategy_indices) -> int:
    return sum(
        1
        for strategy_index in live_strategy_indices
        for _key_index in _STRATEGY_KEY_INDICES[strategy_index]
    )


def _assert_hub_readiness(readiness, live_strategy_indices, live_keys) -> None:
    assert readiness["ready"] is True, readiness
    assert readiness["exported_keys"] == _KEY_COUNT, readiness
    assert readiness["active_upstreams"] == len(live_keys), (
        "the hub counts exactly one shared upstream per live key",
        readiness,
    )
    assert readiness["active_registrations"] == _subscription_refs(live_strategy_indices), (
        readiness,
    )
    assert readiness["pending_releases"] == 0, readiness


def _assert_provider_counts(counts, live_keys) -> None:
    expected_key_starts = {key.stream_id: 1 for key in _MARKET_KEYS}
    expected_key_stops = {
        key.stream_id: (0 if index in live_keys else 1)
        for index, key in enumerate(_MARKET_KEYS)
    }
    assert counts["key_starts"] == expected_key_starts, counts
    assert counts["key_stops"] == expected_key_stops, counts

    expected_connection_starts = {group_id: 1 for group_id, _members in _MERGE_GROUPS}
    expected_connection_stops: dict[str, int] = {}
    expected_open: list[str] = []
    for group_id, members in _MERGE_GROUPS:
        still_open = any(member in live_keys for member in members)
        expected_connection_stops[group_id] = 0 if still_open else 1
        if still_open:
            expected_open.append(group_id)
    assert counts["connection_starts"] == expected_connection_starts, counts
    assert counts["connection_stops"] == expected_connection_stops, counts
    assert counts["open_connections"] == tuple(sorted(expected_open)), counts


def _assert_heads(heads, key_epochs, expected) -> None:
    by_index = {entry[0]: entry for entry in heads}
    assert set(by_index) == set(range(_KEY_COUNT)), heads
    assert set(expected) == set(range(_KEY_COUNT)), expected
    for index, (expected_head, expected_observed) in expected.items():
        _index, stream_id, epoch, head, observed = by_index[index]
        assert stream_id == _MARKET_KEYS[index].stream_id, by_index[index]
        assert epoch == key_epochs[index], by_index[index]
        assert head == expected_head, by_index[index]
        assert observed == expected_observed, by_index[index]


def _assert_status(readiness, counts, heads, live_strategy_indices, key_epochs, expected_heads):
    live_keys = _live_key_indices(live_strategy_indices)
    _assert_hub_readiness(readiness, live_strategy_indices, live_keys)
    _assert_provider_counts(counts, live_keys)
    _assert_heads(heads, key_epochs, expected_heads)


def _publish_round(
    service_pipe,
    clients,
    strategy_indices,
    key_index,
    marker,
    payload_extra,
    expected_sequence,
    key_epochs,
):
    published = _host_command(service_pipe, "publish", key_index, marker, payload_extra)
    key = _MARKET_KEYS[key_index]
    assert published[0] == "published", published
    assert published[1:7] == (
        key_index,
        marker,
        expected_sequence,
        key.stream_id,
        key_epochs[key_index],
        _observed_at(key_index, expected_sequence),
    ), published
    events = {}
    for strategy_index in strategy_indices:
        result = _client_command(
            clients[strategy_index], "collect", key_index, marker, _COLLECT_SECONDS
        )
        assert result[0] == "events" and result[1] == key_index and result[2] == marker, (
            strategy_index,
            result,
        )
        matching = tuple(event for event in result[3] if event[4].get("marker") == marker)
        assert len(matching) == 1, (strategy_index, key_index, marker, matching)
        event = matching[0]
        assert event[0] == key.stream_id, event
        assert event[1] == key_epochs[key_index], event
        assert event[2] == expected_sequence, event
        assert event[3] == key.to_dict(), event
        events[strategy_index] = event
    return published, events


def _sweep_absent(clients, strategy_indices, marker, seconds=_SWEEP_SECONDS) -> None:
    """No event carrying ``marker`` may materialize on any other key or client."""

    for strategy_index in strategy_indices:
        result = _client_command(clients[strategy_index], "sweep", marker, seconds)
        assert result[0] == "swept" and result[1] == marker, result
        assert result[2] == (), (strategy_index, marker, result[2])
        assert result[3] == 0, (strategy_index, marker, result[3])


def _project_option_chain(entries) -> dict[int, dict[str, Any]]:
    """Test-side sparse-chain join across per-key streams, by strike.

    Production has no chain aggregation and none is claimed; this join runs in
    the test process over the per-key authoritative hub heads. Contract: a
    strike is ``live`` only when its own stream head advanced AND its source
    observed_at exists; otherwise it is explicit ``no_data`` — never a zero
    quote/bar and never a locally observed timestamp. Inconsistent input
    (head without a source time, or a source time without any event) raises
    instead of fabricating freshness.
    """

    projection: dict[int, dict[str, Any]] = {}
    for entry in entries:
        strike = entry["strike"]
        head = entry["head"]
        observed = entry["latest_observed_at_utc"]
        if type(head) is not int or head < 0:
            raise AssertionError(f"invalid option chain head for strike {strike}: {head!r}")
        if head > 0 and type(observed) is not str:
            raise AssertionError(f"live strike {strike} is missing its source observed_at")
        if head == 0 and observed is not None:
            raise AssertionError(f"silent strike {strike} carries fabricated freshness")
        projection[strike] = {
            "strike": strike,
            "state": "live" if head > 0 else "no_data",
            "head": head,
            "latest_observed_at_utc": observed,
        }
    return projection


def test_multi_key_spawned_strategies_share_one_upstream_per_key_and_isolated_projections(
    tmp_path,
):
    context = multiprocessing.get_context("spawn")
    rpc_endpoint, market_endpoint = _free_endpoints(2)
    server_public, server_secret = zmq.curve_keypair()
    audience = []
    client_material = {}
    client_raw_configs = {}
    for index in range(len(_STRATEGY_KEY_INDICES)):
        principal_ref = f"synthetic-dim02-principal-{index}"
        strategy_ref = f"synthetic-dim02-strategy-{index}"
        public_key, secret_key = zmq.curve_keypair()
        audience.append((public_key, principal_ref, strategy_ref))
        client_material[index] = (server_public, public_key, secret_key)
        client_raw_configs[index] = _client_config(rpc_endpoint, market_endpoint, strategy_ref)
    assert len({entry[1] for entry in audience}) == len(audience)
    assert len({entry[2] for entry in audience}) == len(audience)

    raw_service_config = _service_config(rpc_endpoint, market_endpoint)
    registry_path = str(tmp_path / "dim02-market-registry.sqlite3")
    service_identity = WireIdentity(WireRole.SERVICE, "synthetic-market-service", _SERVICE_ID)
    key_dicts = tuple(key.to_dict() for key in _MARKET_KEYS)
    service_process, service_pipe = _start_process(
        context,
        _service_host_process,
        (
            raw_service_config,
            registry_path,
            (server_public, server_secret),
            tuple(audience),
            key_dicts,
        ),
    )
    clients: dict[int, dict[str, Any]] = {}
    host_closed = False
    service_process_closed = False
    forced_cleanup = []
    cleanup_failures = []
    try:
        service_ready = _receive(service_pipe)
        assert service_ready[0] == "ready" and service_ready[1] and service_ready[2], service_ready

        # ------------------------------------------------------------------
        # Start: every strategy starts one SDK facade per subscribed key on a
        # shared runtime; the hub must share one provider acquisition per key.
        # ------------------------------------------------------------------
        client_ready = {}
        key_epochs: dict[int, str] = {}
        for index, (_public, principal_ref, strategy_ref) in enumerate(audience):
            process, pipe = _start_process(
                context,
                _strategy_client_process,
                (
                    principal_ref,
                    strategy_ref,
                    client_raw_configs[index],
                    client_material[index],
                    service_identity,
                    key_dicts,
                    _STRATEGY_KEY_INDICES[index],
                ),
            )
            clients[index] = {
                "process": process,
                "pipe": pipe,
                "closed": False,
                "process_closed": False,
            }
            ready = _receive(pipe, 30.0)
            assert ready[0] == "ready" and ready[1], ready
            assert ready[2] == _ACCOUNT_REF, ready
            client_ready[index] = ready[1]
            per_key_bootstraps = ready[3]
            assert tuple(entry[0] for entry in per_key_bootstraps) == _STRATEGY_KEY_INDICES[index]
            for entry in per_key_bootstraps:
                (
                    key_index,
                    stream_id,
                    snapshot_kind,
                    snapshot_complete,
                    full_baseline_available,
                    snapshot_epoch,
                    snapshot_sequence,
                    cursor_epoch,
                    cursor_sequence,
                ) = entry
                assert stream_id == _MARKET_KEYS[key_index].stream_id, entry
                assert (snapshot_kind, snapshot_complete, full_baseline_available) == (
                    "latest_event",
                    False,
                    False,
                ), entry
                # Silent keys never fabricate a baseline: empty snapshot, zero head.
                assert (snapshot_sequence, cursor_sequence) == (0, 0), entry
                assert cursor_epoch == snapshot_epoch, entry
                if key_index in key_epochs:
                    # One shared upstream per key: every subscriber of the same
                    # key observes the same epoch; epochs never cross keys.
                    assert key_epochs[key_index] == snapshot_epoch, entry
                else:
                    key_epochs[key_index] = snapshot_epoch
        assert len(set(client_ready.values())) == len(audience)
        assert set(key_epochs) == set(range(_KEY_COUNT))
        assert len(set(key_epochs.values())) == _KEY_COUNT

        all_strategies = tuple(range(len(audience)))
        readiness, counts, heads = _host_status(service_pipe)
        assert readiness["active_registrations"] == _subscription_refs(all_strategies)
        _assert_status(
            readiness,
            counts,
            heads,
            all_strategies,
            key_epochs,
            {index: (0, None) for index in range(_KEY_COUNT)},
        )
        # Hub-level logical upstreams (6 keys) vs adapter-declared physical
        # connections (5 groups): both levels counted simultaneously.
        assert readiness["active_upstreams"] == _KEY_COUNT
        assert len(counts["open_connections"]) == len(_MERGE_GROUPS) == _KEY_COUNT - 1

        # ------------------------------------------------------------------
        # Warm-up absorbs the CURVE slow join; every strategy collects the same
        # warm-up event on the fully shared key 0 and drains all keys.
        # ------------------------------------------------------------------
        time.sleep(0.4)
        _publish_round(
            service_pipe, clients, all_strategies, 0, "warmup-key0", {}, 1, key_epochs
        )
        for strategy_index in all_strategies:
            assert _client_command(clients[strategy_index], "drain")[0] == "drained"

        # ------------------------------------------------------------------
        # Cross-key isolation: an event for key 0 reaches every subscriber of
        # key 0 and materializes nowhere else (marker sweep across all keys).
        # ------------------------------------------------------------------
        _publish_round(
            service_pipe, clients, all_strategies, 0, "spot-trade-1", {}, 2, key_epochs
        )
        _sweep_absent(clients, all_strategies, "spot-trade-1")

        readiness, counts, heads = _host_status(service_pipe)
        _assert_status(
            readiness,
            counts,
            heads,
            all_strategies,
            key_epochs,
            {
                0: (2, _observed_at(0, 2)),
                **{index: (0, None) for index in range(1, _KEY_COUNT)},
            },
        )

        # ------------------------------------------------------------------
        # Sparse option chain: publish one quote on strike 105 only. The two
        # silent strikes stay at head 0 / observed None with zero events.
        # ------------------------------------------------------------------
        payload_extra = {"strike": 105, "bid": 1.05, "ask": 1.1}
        published, events = _publish_round(
            service_pipe, clients, (0, 1), 3, "opt-105-quote-1", payload_extra, 1, key_epochs
        )
        # Key 3's own stream sequence restarted at 1 while key 0 is at 2: cursors
        # are per key, and the epochs must never cross keys.
        assert events[0][2] == 1 and events[1][2] == 1
        assert events[0][1] == key_epochs[3] != key_epochs[0]
        expected_data = {"marker": "opt-105-quote-1", "source": _PROVIDER_REF}
        expected_data.update(payload_extra)
        assert events[0][4] == expected_data

        # Strategy 2 does not subscribe key 3: the quote never materializes on
        # any of its keys (its SUB sockets physically receive the frame and the
        # SDK must filter it by key, not queue it).
        _sweep_absent(clients, (2,), "opt-105-quote-1")
        # Bounded pumps on the subscribed-but-silent option keys (S0 key 4 and
        # S1 key 5): zero events and zero freshness for contracts without data,
        # even after other keys of the same chain were published. Both sweeps
        # scan every subscribed key, so a cross-key copy would also appear.
        _sweep_absent(clients, (0,), "opt-105-quote-1", 0.8)
        _sweep_absent(clients, (1,), "opt-105-quote-1", 0.8)

        readiness, counts, heads = _host_status(service_pipe)
        expected_heads = {
            0: (2, _observed_at(0, 2)),
            3: (1, _observed_at(3, 1)),
            **{index: (0, None) for index in (1, 2, 4, 5)},
        }
        _assert_status(readiness, counts, heads, all_strategies, key_epochs, expected_heads)

        # Test-side chain join over the per-key heads (see _project_option_chain):
        # exactly one of three strikes is live; the rest are explicit no_data.
        heads_by_index = {entry[0]: entry for entry in heads}
        chain = tuple(
            {
                "strike": strike,
                "head": heads_by_index[key_index][3],
                "latest_observed_at_utc": heads_by_index[key_index][4],
            }
            for key_index, strike in _OPTION_STRIKES.items()
        )
        projection = _project_option_chain(chain)
        assert sum(1 for entry in projection.values() if entry["state"] == "live") == 1
        assert projection[105] == {
            "strike": 105,
            "state": "live",
            "head": 1,
            "latest_observed_at_utc": _observed_at(3, 1),
        }
        for strike in (110, 115):
            assert projection[strike] == {
                "strike": strike,
                "state": "no_data",
                "head": 0,
                "latest_observed_at_utc": None,
            }

        # ------------------------------------------------------------------
        # Per-key last-release semantics: releasing strategy 0 closes exactly
        # its exclusive upstreams (key 4 and the C110 connection) and nothing
        # else; the shared keys keep serving the remaining strategies.
        # ------------------------------------------------------------------
        closed = _client_command(clients[0], "release")
        assert closed[0] == "closed" and closed[1] == audience[0][1], closed
        assert closed[2] == "closed", closed
        assert tuple(index for index, _healthy in closed[3]) == _STRATEGY_KEY_INDICES[0]
        assert all(healthy is False for _index, healthy in closed[3]), closed
        clients[0]["closed"] = True
        _join_normally(clients[0]["process"], clients[0]["pipe"])
        clients[0]["process_closed"] = True

        live_strategies = (1, 2)
        readiness, counts, heads = _host_status(service_pipe)
        _assert_status(readiness, counts, heads, live_strategies, key_epochs, expected_heads)
        assert readiness["active_upstreams"] == 5  # key 4 released with its last ref

        # The shared keys skip no event and start no duplicate upstream: key 0
        # advances to sequence 3 for the two surviving strategies.
        expected_heads_after_trade2 = {
            0: (3, _observed_at(0, 3)),
            3: (1, _observed_at(3, 1)),
            **{index: (0, None) for index in (1, 2, 4, 5)},
        }
        _publish_round(service_pipe, clients, live_strategies, 0, "spot-trade-2", {}, 3, key_epochs)
        _sweep_absent(clients, tuple(live_strategies), "spot-trade-2")
        readiness, counts, heads = _host_status(service_pipe)
        assert readiness["active_upstreams"] == 5, readiness
        _assert_status(
            readiness,
            counts,
            heads,
            live_strategies,
            key_epochs,
            expected_heads_after_trade2,
        )

        # ------------------------------------------------------------------
        # Strategy 1 exits: its exclusive option keys (3 and 5) release now,
        # while the three keys shared with strategy 2 stay live.
        # ------------------------------------------------------------------
        closed = _client_command(clients[1], "release")
        assert closed[0] == "closed" and closed[1] == audience[1][1], closed
        assert all(healthy is False for _index, healthy in closed[3]), closed
        clients[1]["closed"] = True
        _join_normally(clients[1]["process"], clients[1]["pipe"])
        clients[1]["process_closed"] = True

        live_strategies = (2,)
        readiness, counts, heads = _host_status(service_pipe)
        _assert_status(
            readiness,
            counts,
            heads,
            live_strategies,
            key_epochs,
            expected_heads_after_trade2,
        )
        assert readiness["active_upstreams"] == 3
        assert readiness["active_registrations"] == 3

        # ------------------------------------------------------------------
        # The last release stops every remaining key exactly once: one start
        # and one stop per key, one connection per declared group at both level
        # counters (6 logical keys, 5 physical connections).
        # ------------------------------------------------------------------
        closed = _client_command(clients[2], "release")
        assert closed[0] == "closed" and closed[1] == audience[2][1], closed
        assert all(healthy is False for _index, healthy in closed[3]), closed
        clients[2]["closed"] = True
        _join_normally(clients[2]["process"], clients[2]["pipe"])
        clients[2]["process_closed"] = True

        readiness, counts, heads = _host_status(service_pipe)
        _assert_status(
            readiness,
            counts,
            heads,
            (),
            key_epochs,
            expected_heads_after_trade2,
        )
        assert readiness["active_upstreams"] == 0
        assert readiness["active_registrations"] == 0

        closed = _host_command(service_pipe, "close")
        assert closed[0] == "closed" and closed[1] == "closed", closed
        assert closed[2]["key_starts"] == {key.stream_id: 1 for key in _MARKET_KEYS}, closed
        assert closed[2]["key_stops"] == {key.stream_id: 1 for key in _MARKET_KEYS}, closed
        assert closed[2]["connection_starts"] == {
            group_id: 1 for group_id, _members in _MERGE_GROUPS
        }, closed
        assert closed[2]["connection_stops"] == {
            group_id: 1 for group_id, _members in _MERGE_GROUPS
        }, closed
        assert closed[2]["open_connections"] == (), closed
        host_closed = True
        _join_normally(service_process, service_pipe)
        service_process_closed = True
    finally:
        for record in clients.values():
            process, pipe = record["process"], record["pipe"]
            if record["process_closed"]:
                continue
            if not record["closed"]:
                with suppress(Exception):
                    if process.is_alive():
                        pipe.send(("release",))
                        response = _receive(pipe, 4.0)
                        if response[0] == "closed":
                            record["closed"] = True
            _bounded_reap_spawned_process(
                process,
                pipe,
                f"dim02-strategy-client-{process.name}",
                forced_cleanup,
                cleanup_failures,
            )
        if not service_process_closed:
            if not host_closed:
                with suppress(Exception):
                    if service_process.is_alive():
                        service_pipe.send(("close",))
                        response = _receive(service_pipe, 4.0)
                        host_closed = response[0] == "closed"
            _bounded_reap_spawned_process(
                service_process,
                service_pipe,
                "dim02-market-service-host",
                forced_cleanup,
                cleanup_failures,
            )
        _assert_no_fallback_cleanup(forced_cleanup, cleanup_failures)


def test_sparse_option_chain_join_marks_silent_strikes_as_no_data():
    """Deterministic check of the test-side chain join contract (no processes).

    The integration node above feeds this join with real hub heads; this node
    fixes its edge semantics: silent strikes are explicit ``no_data`` with no
    price fields and no substituted timestamp, and inconsistent input is
    rejected instead of fabricating freshness.
    """

    live = {"strike": 105, "head": 1, "latest_observed_at_utc": _observed_at(3, 1)}
    silent = (
        {"strike": 115, "head": 0, "latest_observed_at_utc": None},
        {"strike": 110, "head": 0, "latest_observed_at_utc": None},
    )
    projection = _project_option_chain((live, *silent))
    assert set(projection) == {105, 110, 115}
    assert projection[105] == {
        "strike": 105,
        "state": "live",
        "head": 1,
        "latest_observed_at_utc": _observed_at(3, 1),
    }
    for strike in (110, 115):
        entry = projection[strike]
        # Missing data is never rendered as a zero bar: no quote fields exist,
        # and the absent source time is reported as None, not a local clock.
        assert entry == {
            "strike": strike,
            "state": "no_data",
            "head": 0,
            "latest_observed_at_utc": None,
        }
        assert set(entry) == {"strike", "state", "head", "latest_observed_at_utc"}
    assert sum(1 for entry in projection.values() if entry["state"] == "no_data") == 2
    assert _project_option_chain(()) == {}
    with pytest.raises(AssertionError):
        # A live head without a source timestamp must never be papered over.
        _project_option_chain(
            ({"strike": 110, "head": 3, "latest_observed_at_utc": None},)
        )
    with pytest.raises(AssertionError):
        # A timestamp without any event is fabricated freshness.
        _project_option_chain(
            ({"strike": 115, "head": 0, "latest_observed_at_utc": _observed_at(5, 1)},)
        )
