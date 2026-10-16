"""DIM02 sub-items (2) 回补切实时 and (3) bar 修正: SDK history->live seam and correction policy.

Correction / late-bar policy (coordinator-decided: FAIL-CLOSED, documented here
once; enforcement points are named explicitly):

* The SDK market surface never interprets bar payloads. ``GatewayMarketHub.publish``
  accepts opaque ``data`` for the exact subscribed key and the SDK stream client
  forwards every event verbatim, in provider order, with the provider-supplied
  ``observed_at_utc`` carried on the wire as the event's source timestamp
  (``WireHeader.sent_at_utc``). The SDK validates stream identity, epoch,
  sequence, and key only.
* No fabrication. A silent key yields no event, no head advance, and no
  freshness claim; a history interval with no provider bars yields an explicit
  empty ``complete`` page (``bars == []``), never synthetic zero bars, and a
  history page's ``observed_at_utc`` is the provider-supplied value.
* History reads are stateless pass-throughs. The hub invokes the configured
  trusted history provider once per exact bounded request and the SDK returns
  the received page (or an explicit finite error). Nothing is cached, so a
  provider re-serving a corrected bar for an already-read interval is returned
  by the next read; history reads never move the live cursor.
* Consequence: because the SDK neither dedups, merges, replaces, nor rejects
  overlapping bar payloads, dedup/conflict enforcement for any *consumer
  projection* is the consumer's fail-closed boundary. This file implements that
  policy in the test-local ``_FailClosedBarProjection`` driven by real SDK
  events: an identical re-delivery for an existing ``open_time_utc`` is
  deduplicated exactly once (no double count); a conflicting OHLCV payload for
  an existing ``open_time_utc`` raises ``_ProjectionConflict`` and leaves the
  already-delivered projection untouched (no partial projection, no silent
  replacement). The accepted Backtrader-side consumer proof of the same policy
  is the round-07B ``ServiceReadProxy`` ``HISTORY_LIVE_CONFLICT`` /
  ``HISTORY_BAR_CONFLICT`` behavior; that suite is NOT re-run here.

All scenarios drive a real spawned ``ServiceHost`` process (real ZMQ CURVE
endpoints + registry) with a synthetic deterministic provider that serves
paginated bounded history AND live publications, plus a real spawned strategy
client using ``ServiceMarketClient``. No network to real venues; deterministic
data only.
"""

from __future__ import annotations

import multiprocessing
import os
import socket
import time
from collections.abc import Mapping
from contextlib import suppress
from datetime import UTC, datetime
from typing import Any

import pytest
import zmq
from bt_api_base.market_history_protocol import (
    MarketBar,
    MarketHistoryPage,
    MarketHistoryRequest,
)
from bt_api_base.market_protocol import MarketInstrumentKey, MarketSubscriptionKey
from bt_api_base.service_protocol import WireIdentity, WireRole, WireScope
from bt_api_gateway.market_hub import GatewayMarketHub

from bt_api_py.service_components import ComponentResources
from bt_api_py.service_config import parse_client_config, parse_service_config
from bt_api_py.service_host import ServiceHost, TrustedServiceAuthority
from bt_api_py.service_market import (
    ServiceMarketClient,
    ServiceMarketError,
    ZmqMarketTransportFactory,
)
from bt_api_py.service_registry import PrincipalGrant, ResourceGrant
from bt_api_py.service_runtime import (
    ClientCurveKeys,
    ServiceClientRuntime,
    ZmqTransportFactory,
)

_SERVICE_ID = "synthetic-market-service"
_SERVICE_PRINCIPAL_REF = "synthetic-market-service"
_SERVICE_INSTANCE = "synthetic-market-hub"
_SERVICE_SCOPE_REF = "synthetic-market-public-scope-v1"
_DOMAIN_REF = "paper:market-dim02-seam"
_ACCOUNT_REF = "account:synthetic-market-dim02-seam"
_TRANSPORT_REF = "market-listener-v1"
_SECURITY_REF = "market-curve-v1"
_BOOTSTRAP_OPERATIONS = frozenset(
    {
        "market.subscribe",
        "market.unsubscribe",
        "market.snapshot",
        "market.head",
        "market.read_after",
    }
)
_HISTORY_OPERATION = "market.history"
_ALL_MARKET_OPERATIONS = _BOOTSTRAP_OPERATIONS | {_HISTORY_OPERATION}
_REQUEST_TIMEOUT_MS = 2_000
_HEARTBEAT_SECONDS = 5
_HISTORY_TIMEOUT_SECONDS = 5.0

_MAIN_PRINCIPAL_REF = "synthetic-seam-principal"
_MAIN_STRATEGY_REF = "synthetic-seam-strategy"

_BAR_KEY = MarketSubscriptionKey(
    source_ref="synthetic-seam-source",
    entitlement_ref="synthetic-seam-entitlement",
    instrument=MarketInstrumentKey(
        provider="synthetic",
        venue="loopback",
        environment="paper",
        product_type="spot",
        instrument_id="synthetic-seam-instrument",
        native_symbol="SEAM-USD",
    ),
    channel="bar",
    timeframe="1m",
)

_T0 = "2026-10-14T00:00:00Z"
_T1 = "2026-10-14T00:01:00Z"
_T2 = "2026-10-14T00:02:00Z"
_T3 = "2026-10-14T00:03:00Z"
_T4 = "2026-10-14T00:04:00Z"
_PROVIDER_OBSERVED_AT = "2026-10-14T00:04:30Z"
_WARMUP_OBSERVED_AT = "2026-10-14T00:03:30Z"
_DUPLICATE_OBSERVED_AT = "2026-10-14T00:03:40Z"
_REVISION_OBSERVED_AT = "2026-10-14T00:03:50Z"

_B0 = MarketBar(_T0, _T1, "10", "11", "9", "10.5", "2")
_B1 = MarketBar(_T1, _T2, "10.5", "12", "10", "11.5", "3")
_B2 = MarketBar(_T2, _T3, "11.5", "13", "11", "12.5", "4")
_B1_REVISED = MarketBar(_T1, _T2, "10.5", "12.2", "9.8", "11.9", "3.7")
_B3 = MarketBar(_T3, _T4, "12.5", "14", "12", "13.5", "5")
_HISTORY_BARS = (_B0, _B1, _B2)

_REQUEST_FIRST_PAGE = MarketHistoryRequest(_BAR_KEY, _T0, _T4, 2)
_REQUEST_SECOND_PAGE = MarketHistoryRequest(_BAR_KEY, _T2, _T4, 2)
_REQUEST_THROUGH_SEAM = MarketHistoryRequest(_BAR_KEY, _T0, _T3, 10)
_REQUEST_EMPTY_INTERVAL = MarketHistoryRequest(_BAR_KEY, _T0, _T1, 5)


def _expected_page(
    request: MarketHistoryRequest,
    bars: tuple[MarketBar, ...],
    complete: bool,
    next_start_time_utc: str | None,
) -> MarketHistoryPage:
    return MarketHistoryPage(request, bars, complete, next_start_time_utc, _PROVIDER_OBSERVED_AT)


def _overlaps(
    left_open: str, left_close: str, right_open: str, right_close: str
) -> bool:
    return left_open < right_close and right_open < left_close


class _SeamSyntheticProvider:
    """Deterministic source serving bounded/paginated history AND live publications.

    The provider owns the single source of truth for one bar key: a store of
    closed bars (served by ``fetch``) plus live publications pushed through the
    attached hub with an explicit provider-supplied ``observed_at_utc``.
    """

    def __init__(
        self,
        key: MarketSubscriptionKey,
        bars: tuple[MarketBar, ...],
        observed_at_utc: str,
    ) -> None:
        self.key = key
        self._bars: dict[str, MarketBar] = {bar.open_time_utc: bar for bar in bars}
        self.observed_at_utc = observed_at_utc
        self.starts = 0
        self.stops = 0
        self.hub: GatewayMarketHub | None = None
        self.generation: str | None = None
        self.fetch_requests: list[MarketHistoryRequest] = []
        self.fetch_timeouts: list[float] = []

    def subscribe(self, key: MarketSubscriptionKey, *, generation: str) -> object:
        if key != self.key:
            raise AssertionError("only the declared synthetic bar key is supported")
        if not generation:
            raise AssertionError("the hub must provide a nonempty source generation")
        self.starts += 1
        self.generation = generation
        return ("seam-handle", self.starts, key.stream_id, generation)

    def unsubscribe(self, handle: object) -> bool:
        if (
            self.generation is None
            or not isinstance(handle, tuple)
            or handle[0] != "seam-handle"
            or handle[3] != self.generation
        ):
            raise AssertionError("provider received an unknown synthetic handle")
        self.stops += 1
        self.generation = None
        return True

    def apply_correction(self, open_time_utc: str, bar: MarketBar) -> None:
        if bar.open_time_utc != open_time_utc or open_time_utc not in self._bars:
            raise AssertionError("a correction must target one existing open_time_utc")
        self._bars[open_time_utc] = bar

    def fetch(self, request: MarketHistoryRequest, timeout_seconds: float) -> MarketHistoryPage:
        """Serve one bounded page of stored closed bars, or an explicit empty page."""
        if type(request) is not MarketHistoryRequest or request.key != self.key:
            raise AssertionError("history fetch received an unexpected request")
        self.fetch_requests.append(request)
        self.fetch_timeouts.append(timeout_seconds)
        candidates = [
            bar
            for bar in sorted(self._bars.values(), key=lambda item: item.open_time_utc)
            if bar.open_time_utc >= request.start_time_utc
            and bar.close_time_utc <= request.end_time_utc
        ]
        page_bars = tuple(candidates[: request.limit])
        if not page_bars:
            return _expected_page(request, (), True, None)
        if len(candidates) > request.limit and page_bars[-1].close_time_utc < request.end_time_utc:
            return _expected_page(request, page_bars, False, page_bars[-1].close_time_utc)
        return _expected_page(request, page_bars, True, None)

    def publish_bar(self, bar: MarketBar, observed_at_utc: str) -> int:
        return self._publish(bar.to_dict(), observed_at_utc)

    def publish_raw(self, data: Mapping[str, Any], observed_at_utc: str) -> int:
        return self._publish(dict(data), observed_at_utc)

    def _publish(self, data: Mapping[str, Any], observed_at_utc: str) -> int:
        if self.hub is None or self.generation is None:
            raise AssertionError("synthetic hub is not attached")
        event = self.hub.publish(
            self.key, data, observed_at_utc, generation=self.generation
        )
        return event.header.sequence


class _ProjectionConflict(RuntimeError):
    """The consumer projection's fail-closed error for a conflicting overlap."""


class _FailClosedBarProjection:
    """Test-local consumer projection enforcing the documented fail-closed policy.

    The SDK market stream deliberately forwards every provider event verbatim:
    it never dedups, merges, replaces, or drops bar payloads. A consumer that
    maintains a bar projection must therefore fail closed at this boundary:

    * identical re-delivery for an existing ``open_time_utc``: deduplicated
      once (returns ``False``, no double count);
    * conflicting OHLCV for an existing ``open_time_utc``: raises
      ``_ProjectionConflict`` and leaves the already-delivered projection
      untouched (no partial application, no silent replacement).
    """

    def __init__(self) -> None:
        self.bars: dict[str, dict[str, Any]] = {}

    def apply(self, bar: Mapping[str, Any]) -> bool:
        plain = dict(bar)
        open_time = plain["open_time_utc"]
        existing = self.bars.get(open_time)
        if existing is None:
            self.bars[open_time] = plain
            return True
        if existing == plain:
            return False
        raise _ProjectionConflict(
            f"conflicting bar revision for open_time_utc {open_time!r}"
        )


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
        "registration_policy_ref": "synthetic-seam-registration-v1",
        "service_requirements_ref": "synthetic-seam-requirements-v1",
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


def _service_host_process(
    control, raw_config, registry_path, server_keys, client_audience, history_bars
):
    config = parse_service_config(raw_config)
    service_identity = WireIdentity(WireRole.SERVICE, _SERVICE_PRINCIPAL_REF, _SERVICE_ID)
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
                        _ALL_MARKET_OPERATIONS,
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
        scope_version="synthetic-seam-scope-v1",
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
    provider = _SeamSyntheticProvider(
        _BAR_KEY,
        tuple(MarketBar.from_dict(item) for item in history_bars),
        _PROVIDER_OBSERVED_AT,
    )
    hub = GatewayMarketHub(
        provider,
        key_universe=frozenset({_BAR_KEY}),
        principal_allowed_keys={
            principal_ref: frozenset({_BAR_KEY})
            for _public_key, principal_ref, _strategy_ref in client_audience
        },
        service_identity=service_identity,
        service_scope=service_scope,
        history_provider=provider,
        history_timeout_seconds=_HISTORY_TIMEOUT_SECONDS,
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
                control.send(
                    (
                        "status",
                        dict(hub.readiness),
                        provider.starts,
                        provider.stops,
                        len(provider.fetch_requests),
                        tuple(provider.fetch_timeouts),
                        hub.history_enabled,
                        tuple(sorted(hub.supported_operations)),
                    )
                )
            elif action == "publish-raw":
                sequence = provider.publish_raw(command[1], command[2])
                control.send(("published-raw", sequence))
            elif action == "publish-bar":
                sequence = provider.publish_bar(MarketBar.from_dict(command[1]), command[2])
                control.send(("published-bar", command[1]["open_time_utc"], sequence))
            elif action == "correct-bar":
                provider.apply_correction(command[1], MarketBar.from_dict(command[2]))
                control.send(("corrected-bar", command[1]))
            elif action == "close":
                host.close()
                control.send(("closed", host.state, provider.starts, provider.stops))
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


def _event_tuple(event) -> tuple[Any, ...]:
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
        event.header.sent_at_utc,
        event.header.identity.principal_ref,
        event.header.identity.actor_ref,
        plain(event.payload["key"]),
        plain(event.payload["data"]),
    )


def _strategy_client_process(
    control,
    principal_ref,
    strategy_ref,
    raw_config,
    client_keys,
    service_identity,
):
    config = parse_client_config(raw_config)
    identity = WireIdentity(WireRole.STRATEGY_CLIENT, principal_ref, strategy_ref)

    def scope_resolver(_scope_ref: str) -> WireScope:
        return WireScope(
            _DOMAIN_REF,
            account_ref=_ACCOUNT_REF,
            strategy_ref=strategy_ref,
        )

    security = ClientCurveKeys(client_keys[0], client_keys[1], client_keys[2])
    runtime = ServiceClientRuntime(
        config,
        identity=identity,
        scope_resolver=scope_resolver,
        peer_resolver=lambda _service_id: service_identity,
        transport_factory=ZmqTransportFactory({_SECURITY_REF: security}),
        requested_operations={"store.market": _ALL_MARKET_OPERATIONS},
    )
    market = None
    pending_events: list[tuple[Any, ...]] = []
    try:
        runtime.start()
        market = ServiceMarketClient(
            runtime,
            "store.market",
            _BAR_KEY,
            subscriber_factory=ZmqMarketTransportFactory({_SECURITY_REF: security}),
            stream_scope=WireScope(_DOMAIN_REF),
            head_check_interval_seconds=2.0,
        )
        bootstrap = market.start()
        registration_id = runtime.bindings[0].registration_id
        control.send(
            (
                "ready",
                registration_id,
                bootstrap.snapshot is None,
                bootstrap.snapshot_kind,
                bootstrap.snapshot_complete,
                bootstrap.full_baseline_available,
                bootstrap.snapshot_epoch,
                bootstrap.snapshot_sequence,
                bootstrap.cursor_epoch,
                bootstrap.cursor_sequence,
                market.healthy,
            )
        )

        def service_once(timeout_ms: int) -> None:
            delay = runtime.next_maintenance_delay()
            if delay is not None and delay <= 0:
                runtime.maintain()
            event = market.poll(timeout_ms=timeout_ms)
            if event is not None:
                pending_events.append(_event_tuple(event))

        while True:
            service_once(0)
            if not control.poll(0.005):
                continue
            command = control.recv()
            action = command[0]
            if action == "cursor":
                control.send(
                    (
                        "cursor",
                        market.healthy,
                        market.recovery_required,
                        market._cursor_epoch,
                        market._cursor_sequence,
                        market._queued_epoch,
                        market._queued_sequence,
                        market.queue_count,
                        market.queue_bytes,
                    )
                )
            elif action == "history":
                request = MarketHistoryRequest.from_dict(command[1])
                try:
                    page = market.history(request)
                except ServiceMarketError as exc:
                    control.send(("history-error", exc.code))
                else:
                    control.send(("history-page", page.to_dict()))
            elif action == "wait-events":
                count, deadline = command[1], time.monotonic() + command[2]
                while time.monotonic() < deadline and len(pending_events) < count:
                    service_once(10)
                selected = tuple(pending_events)
                pending_events.clear()
                control.send(("events", selected))
            elif action == "idle-poll":
                event = market.poll(timeout_ms=command[1])
                if event is not None:
                    pending_events.append(_event_tuple(event))
                selected = tuple(pending_events)
                pending_events.clear()
                control.send(("idle-polled", selected))
            elif action == "release":
                market.close()
                runtime.close()
                control.send(("closed", principal_ref, runtime.state, market.healthy))
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


def _host_command(session, *command, timeout: float = 15.0):
    pipe = session["service_pipe"]
    pipe.send(command)
    result = _receive(pipe, timeout)
    assert result[0] != "error", result
    return result


def _client_command(session, *command, timeout: float = 15.0):
    process, pipe = session["client_process"], session["client_pipe"]
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


def _start_session(tmp_path, *, history_bars) -> dict[str, Any]:
    """Spawn one real ServiceHost process plus one real strategy-client process."""
    context = multiprocessing.get_context("spawn")
    rpc_endpoint, market_endpoint = _free_endpoints(2)
    server_public, server_secret = zmq.curve_keypair()
    client_public, client_secret = zmq.curve_keypair()
    audience = ((client_public, _MAIN_PRINCIPAL_REF, _MAIN_STRATEGY_REF),)
    raw_service_config = _service_config(rpc_endpoint, market_endpoint)
    raw_client_config = _client_config(rpc_endpoint, market_endpoint, _MAIN_STRATEGY_REF)
    registry_path = str(tmp_path / "market-seam-registry.sqlite3")
    service_identity = WireIdentity(WireRole.SERVICE, _SERVICE_PRINCIPAL_REF, _SERVICE_ID)
    service_process, service_pipe = _start_process(
        context,
        _service_host_process,
        (
            raw_service_config,
            registry_path,
            (server_public, server_secret),
            audience,
            tuple(bar.to_dict() for bar in history_bars),
        ),
    )
    session: dict[str, Any] = {
        "service_process": service_process,
        "service_pipe": service_pipe,
        "client_process": None,
        "client_pipe": None,
        "client_ready": None,
        "principal_ref": _MAIN_PRINCIPAL_REF,
        "host_closed": False,
        "service_closed": False,
        "client_closed": False,
        "client_process_closed": False,
        "forced": [],
        "failures": [],
    }
    try:
        service_ready = _receive(service_pipe)
        assert service_ready[0] == "ready" and service_ready[1] and service_ready[2], service_ready
        client_process, client_pipe = _start_process(
            context,
            _strategy_client_process,
            (
                _MAIN_PRINCIPAL_REF,
                _MAIN_STRATEGY_REF,
                raw_client_config,
                (server_public, client_public, client_secret),
                service_identity,
            ),
        )
        session["client_process"] = client_process
        session["client_pipe"] = client_pipe
        client_ready = _receive(client_pipe)
        assert client_ready[0] == "ready", client_ready
        session["client_ready"] = client_ready
    except BaseException:
        _stop_session(session)
        raise
    return session


def _stop_session(session: dict[str, Any]) -> None:
    client_process = session["client_process"]
    client_pipe = session["client_pipe"]
    if client_process is not None and client_pipe is not None and not session["client_process_closed"]:
        if not session["client_closed"]:
            with suppress(Exception):
                if client_process.is_alive():
                    client_pipe.send(("release",))
                    response = _receive(client_pipe, 4.0)
                    session["client_closed"] = response[0] == "closed"
        _bounded_reap_spawned_process(
            client_process,
            client_pipe,
            f"seam-strategy-client-{client_process.name}",
            session["forced"],
            session["failures"],
        )
        session["client_process_closed"] = True
    service_process = session["service_process"]
    service_pipe = session["service_pipe"]
    if not session["service_closed"]:
        if not session["host_closed"]:
            with suppress(Exception):
                if service_process.is_alive():
                    service_pipe.send(("close",))
                    response = _receive(service_pipe, 4.0)
                    session["host_closed"] = response[0] == "closed"
        _bounded_reap_spawned_process(
            service_process,
            service_pipe,
            "seam-market-service-host",
            session["forced"],
            session["failures"],
        )
        session["service_closed"] = True
    _assert_no_fallback_cleanup(session["forced"], session["failures"])


def _host_status(session) -> dict[str, Any]:
    result = _host_command(session, "status")
    assert result[0] == "status", result
    return {
        "readiness": result[1],
        "starts": result[2],
        "stops": result[3],
        "fetch_count": result[4],
        "fetch_timeouts": result[5],
        "history_enabled": result[6],
        "supported_operations": result[7],
    }


def _cursor(session) -> tuple[Any, ...]:
    result = _client_command(session, "cursor")
    assert result[0] == "cursor", result
    return tuple(result[1:9])


def _release_and_close(session) -> None:
    closed = _client_command(session, "release")
    assert closed == ("closed", session["principal_ref"], "closed", False), closed
    _join_normally(session["client_process"], session["client_pipe"])
    session["client_closed"] = True
    session["client_process_closed"] = True
    status = _host_status(session)
    assert status["starts"] == 1, status
    assert status["stops"] == 1, status
    assert status["readiness"]["active_upstreams"] == 0, status
    closed = _host_command(session, "close")
    assert closed == ("closed", "closed", 1, 1), closed
    session["host_closed"] = True
    _join_normally(session["service_process"], session["service_pipe"])
    session["service_closed"] = True


def test_history_to_live_seam_is_gap_free_and_passes_through_source_times(tmp_path):
    """Subscribe first, read bounded/paginated history to the seam, then consume live.

    Proven here: paginated history pages exhaust the provider store at the seam
    boundary (last history bar close == first live bar open, no gap, no
    unintended overlap for the same key); the live event's ``sent_at_utc`` is
    the exact provider-supplied source time (not local wall-clock); key,
    stream_id, epoch, and the live cursor are consistent across the seam; and
    history reads never advance the live cursor.
    """
    session = _start_session(tmp_path, history_bars=_HISTORY_BARS)
    try:
        ready = session["client_ready"]
        assert isinstance(ready[1], str) and ready[1]
        # Silent subscription first: no fabricated snapshot or freshness.
        assert ready[2] is True, ready
        assert ready[3] == "latest_event", ready
        assert ready[4] is False and ready[5] is False, ready
        epoch = ready[6]
        assert isinstance(epoch, str) and epoch and ready[8] == epoch, ready
        assert ready[7] == 0 and ready[9] == 0, ready
        assert ready[10] is True, ready
        assert _cursor(session) == (True, False, epoch, 0, epoch, 0, 0, 0)

        # Bounded warm-up publishes so every local CURVE/SUB join completes;
        # discard them before the measured seam observations (GW01 pattern).
        time.sleep(0.4)
        first_warmup = _host_command(session, "publish-raw", {"warmup": "warmup-0"}, _WARMUP_OBSERVED_AT)
        second_warmup = _host_command(session, "publish-raw", {"warmup": "warmup-1"}, _WARMUP_OBSERVED_AT)
        assert first_warmup == ("published-raw", 1), first_warmup
        assert second_warmup == ("published-raw", 2), second_warmup
        warmups = _client_command(session, "wait-events", 2, 15.0)
        assert warmups[0] == "events" and len(warmups[1]) == 2, warmups
        assert [event[2] for event in warmups[1]] == [1, 2], warmups
        assert all(event[1] == epoch and event[3] == _WARMUP_OBSERVED_AT for event in warmups[1])
        warm_cursor = (True, False, epoch, 2, epoch, 2, 0, 0)
        assert _cursor(session) == warm_cursor

        # Bounded paginated history: page 1 stops at its limit with an explicit
        # continuation; page 2 exhausts the provider store at the seam.
        first_page = _client_command(session, "history", _REQUEST_FIRST_PAGE.to_dict())
        page_one = _expected_page(_REQUEST_FIRST_PAGE, (_B0, _B1), False, _T2)
        assert first_page == ("history-page", page_one.to_dict()), first_page
        assert _cursor(session) == warm_cursor  # history reads never move the live cursor

        second_page = _client_command(session, "history", _REQUEST_SECOND_PAGE.to_dict())
        page_two = _expected_page(_REQUEST_SECOND_PAGE, (_B2,), True, None)
        assert second_page == ("history-page", page_two.to_dict()), second_page
        assert second_page[1]["next_start_time_utc"] is None
        assert _cursor(session) == warm_cursor

        # Live seam: the first live bar opens exactly at the history cursor.
        published = _host_command(session, "publish-bar", _B3.to_dict(), _T4)
        assert published == ("published-bar", _T3, 3), published
        result = _client_command(session, "wait-events", 1, 15.0)
        assert result[0] == "events" and len(result[1]) == 1, result
        event = result[1][0]
        stream_id, event_epoch, sequence, sent_at, principal, actor, key_dict, data = event
        assert key_dict == _BAR_KEY.to_dict()
        assert data == _B3.to_dict()
        assert stream_id == _BAR_KEY.stream_id
        assert event_epoch == epoch and sequence == 3
        assert principal == _SERVICE_PRINCIPAL_REF and actor == _SERVICE_ID
        # Source-time passthrough: the provider-supplied observed_at_utc travels
        # unchanged on the wire, it is not the consuming host's wall clock.
        assert sent_at == _T4, event
        assert sent_at != datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ"), event

        # Seam continuity for the same key: no gap and no unintended overlap.
        history_bars = first_page[1]["bars"] + second_page[1]["bars"]
        assert [bar["open_time_utc"] for bar in history_bars] == [_T0, _T1, _T2]
        last_history = history_bars[-1]
        live_open, live_close = data["open_time_utc"], data["close_time_utc"]
        assert last_history["close_time_utc"] == live_open == _T3
        assert all(bar["close_time_utc"] <= live_open for bar in history_bars)
        assert not any(
            _overlaps(bar["open_time_utc"], bar["close_time_utc"], live_open, live_close)
            for bar in history_bars
        )

        # Cursor/identity consistency across the seam.
        assert _cursor(session) == (True, False, epoch, 3, epoch, 3, 0, 0)

        status = _host_status(session)
        assert status["readiness"]["active_upstreams"] == 1, status
        assert status["readiness"]["active_registrations"] == 1, status
        assert status["starts"] == 1 and status["stops"] == 0, status
        assert status["fetch_count"] == 2, status
        assert status["fetch_timeouts"] == (_HISTORY_TIMEOUT_SECONDS, _HISTORY_TIMEOUT_SECONDS)
        assert status["history_enabled"] is True
        assert status["supported_operations"] == tuple(sorted(_ALL_MARKET_OPERATIONS))

        _release_and_close(session)
    finally:
        _stop_session(session)


def test_silent_key_and_beyond_head_read_do_not_fabricate_bars_or_freshness(tmp_path):
    """A silent key and a no-data history read produce explicit emptiness only.

    The subscription is authoritative and active, yet no event, no head
    advance, no synthetic zero bar, and no fabricated freshness appears; the
    no-data history read returns an explicit empty ``complete`` page whose
    ``observed_at_utc`` is the provider-supplied value.
    """
    session = _start_session(tmp_path, history_bars=())
    try:
        ready = session["client_ready"]
        assert ready[2] is True, ready
        assert ready[3] == "latest_event", ready
        assert ready[4] is False and ready[5] is False, ready
        epoch = ready[6]
        assert isinstance(epoch, str) and epoch and ready[8] == epoch, ready
        assert ready[7] == 0 and ready[9] == 0, ready
        assert ready[10] is True, ready
        assert _cursor(session) == (True, False, epoch, 0, epoch, 0, 0, 0)

        idle = _client_command(session, "idle-poll", 800)
        assert idle == ("idle-polled", ()), idle
        assert _cursor(session) == (True, False, epoch, 0, epoch, 0, 0, 0)

        status = _host_status(session)
        assert status["readiness"]["active_upstreams"] == 1, status
        assert status["readiness"]["active_registrations"] == 1, status
        assert status["starts"] == 1 and status["stops"] == 0, status
        assert status["fetch_count"] == 0, status
        assert status["history_enabled"] is True

        # Beyond-head / no-data read: explicit empty complete page, zero bars,
        # provider-supplied observation time (never a synthesized zero bar).
        page = _client_command(session, "history", _REQUEST_EMPTY_INTERVAL.to_dict())
        expected = _expected_page(_REQUEST_EMPTY_INTERVAL, (), True, None)
        assert page == ("history-page", expected.to_dict()), page
        assert page[1]["bars"] == []
        assert page[1]["complete"] is True and page[1]["next_start_time_utc"] is None
        assert page[1]["observed_at_utc"] == _PROVIDER_OBSERVED_AT

        status = _host_status(session)
        assert status["fetch_count"] == 1, status
        assert status["fetch_timeouts"] == (_HISTORY_TIMEOUT_SECONDS,), status
        assert _cursor(session) == (True, False, epoch, 0, epoch, 0, 0, 0)

        _release_and_close(session)
    finally:
        _stop_session(session)


def test_corrected_history_reread_returns_provider_supplied_values(tmp_path):
    """The SDK caches nothing: a corrected re-read returns the provider's new value.

    The same bounded request read twice around a provider-side correction of an
    already-read bar (same open_time, different OHLCV) returns page 1 then the
    corrected page 2; both equals the provider-supplied page, page 1 is not
    mutated, and the live cursor never changes across history reads.
    """
    session = _start_session(tmp_path, history_bars=_HISTORY_BARS)
    try:
        ready = session["client_ready"]
        epoch = ready[6]
        baseline_cursor = (True, False, epoch, 0, epoch, 0, 0, 0)
        assert _cursor(session) == baseline_cursor

        first = _client_command(session, "history", _REQUEST_THROUGH_SEAM.to_dict())
        page_one = _expected_page(_REQUEST_THROUGH_SEAM, (_B0, _B1, _B2), True, None)
        assert first == ("history-page", page_one.to_dict()), first
        assert _cursor(session) == baseline_cursor

        corrected = _host_command(session, "correct-bar", _T1, _B1_REVISED.to_dict())
        assert corrected == ("corrected-bar", _T1), corrected

        second = _client_command(session, "history", _REQUEST_THROUGH_SEAM.to_dict())
        page_two = _expected_page(_REQUEST_THROUGH_SEAM, (_B0, _B1_REVISED, _B2), True, None)
        assert second == ("history-page", page_two.to_dict()), second
        assert second[1]["bars"][1] != first[1]["bars"][1]
        assert (
            second[1]["bars"][1]["open_time_utc"] == first[1]["bars"][1]["open_time_utc"] == _T1
        )
        assert first[1]["bars"][1] == _B1.to_dict()
        assert second[1]["bars"][1] == _B1_REVISED.to_dict()
        assert _cursor(session) == baseline_cursor

        status = _host_status(session)
        assert status["fetch_count"] == 2, status
        assert status["fetch_timeouts"] == (_HISTORY_TIMEOUT_SECONDS, _HISTORY_TIMEOUT_SECONDS)

        _release_and_close(session)
    finally:
        _stop_session(session)


def test_conflicting_live_overlap_fails_closed_at_the_consumer_projection(tmp_path):
    """The SDK forwards overlaps verbatim; the consumer projection fails closed.

    Live re-delivery of an already-read bar (identical) and a conflicting
    revision of another read bar (same open_time, different OHLCV) are both
    forwarded by the SDK stream in provider order, unchanged: the SDK performs
    no dedup, merge, replacement, or drop. The fail-closed boundary is the
    consumer projection driven here: identical overlap deduplicates exactly
    once (no double count); conflicting overlap raises and leaves the
    already-delivered projection untouched (no partial projection, no silent
    replacement).
    """
    session = _start_session(tmp_path, history_bars=_HISTORY_BARS)
    try:
        ready = session["client_ready"]
        epoch = ready[6]

        history = _client_command(session, "history", _REQUEST_THROUGH_SEAM.to_dict())
        page = _expected_page(_REQUEST_THROUGH_SEAM, (_B0, _B1, _B2), True, None)
        assert history == ("history-page", page.to_dict()), history

        time.sleep(0.4)
        assert _host_command(
            session, "publish-raw", {"warmup": "warmup-0"}, _WARMUP_OBSERVED_AT
        ) == ("published-raw", 1)
        assert _host_command(
            session, "publish-raw", {"warmup": "warmup-1"}, _WARMUP_OBSERVED_AT
        ) == ("published-raw", 2)
        warmups = _client_command(session, "wait-events", 2, 15.0)
        assert warmups[0] == "events" and len(warmups[1]) == 2, warmups

        duplicate = _host_command(session, "publish-bar", _B2.to_dict(), _DUPLICATE_OBSERVED_AT)
        conflict = _host_command(session, "publish-bar", _B1_REVISED.to_dict(), _REVISION_OBSERVED_AT)
        assert duplicate == ("published-bar", _T2, 3), duplicate
        assert conflict == ("published-bar", _T1, 4), conflict

        result = _client_command(session, "wait-events", 2, 15.0)
        assert result[0] == "events" and len(result[1]) == 2, result
        duplicate_event, conflict_event = result[1]
        assert [duplicate_event[2], conflict_event[2]] == [3, 4], result
        assert duplicate_event[1] == conflict_event[1] == epoch
        assert duplicate_event[3] == _DUPLICATE_OBSERVED_AT
        assert conflict_event[3] == _REVISION_OBSERVED_AT
        assert duplicate_event[6] == conflict_event[6] == _BAR_KEY.to_dict()

        history_bars = history[1]["bars"]
        # Observed SDK behavior: both overlaps are forwarded verbatim, ordered,
        # with no dedup/replacement/drop on the stream itself.
        assert duplicate_event[7] == _B2.to_dict() == history_bars[2]
        assert conflict_event[7] == _B1_REVISED.to_dict()
        assert conflict_event[7] != history_bars[1]
        assert conflict_event[7]["open_time_utc"] == history_bars[1]["open_time_utc"] == _T1

        # Fail-closed consumer projection boundary (documented policy).
        projection = _FailClosedBarProjection()
        for bar in history_bars:
            assert projection.apply(bar) is True
        assert len(projection.bars) == 3
        assert projection.apply(duplicate_event[7]) is False  # dedup once, no double count
        assert len(projection.bars) == 3
        with pytest.raises(_ProjectionConflict) as error:
            projection.apply(conflict_event[7])
        assert _T1 in str(error.value)
        assert len(projection.bars) == 3  # no partial application
        assert projection.bars[_T1] == _B1.to_dict()  # no silent replacement

        _release_and_close(session)
    finally:
        _stop_session(session)
