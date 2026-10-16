"""ARB06 node: group residual fact reaches a real Monitor ingress host.

The node reuses the accepted three-host group fixture (two account hosts plus
the coordinator host) that settles at ``RECOVERY_REQUIRED`` with an exact
residual, then adds a real Monitor durable-ingress host and an explicit SDK
group-monitor relay process. Provider leaves are synthetic SQLite-backed
fixtures; the test does not load credentials or contact an exchange.
"""

from __future__ import annotations

import hashlib
import json
import multiprocessing
import sqlite3
from contextlib import suppress
from decimal import Decimal
from pathlib import Path
from typing import Any

import zmq
from bt_api_base.service_protocol import WireIdentity, WireRole, WireScope
from bt_api_execution import GroupOrderState, Side, SqliteOrderGroupStore
from bt_api_monitor.durable import DurableOutbox
from bt_api_monitor.service_ingress import MonitorIngressTarget

from bt_api_py.service_components import ComponentResources
from bt_api_py.service_config import parse_service_config
from bt_api_py.service_group_monitor import (
    ServiceGroupMonitorRelay,
    ServiceGroupMonitorRelayError,
    SqliteGroupResidualReader,
    group_monitor_relay_configured,
)
from bt_api_py.service_host import ServiceHost, TrustedServiceAuthority
from bt_api_py.service_registry import PrincipalGrant, ResourceGrant
from bt_api_py.service_runtime import (
    ClientCurveKeys,
    ServiceClientRuntime,
    ZmqTransportFactory,
)
from tests.integration.test_arb03_dim05_group_partial_fill_service import (
    _ARB03_ACCOUNTS,
    _GROUP_ARB03,
    _PORTFOLIO,
    _STRATEGY,
    _GroupCluster,
    _assert_provider_writes,
    _endpoint,
    _group_request,
)
from tests.unit._service_group_provider_fixtures import _V2_INSTRUMENT
from tests.unit.test_service_account_integration import (
    _assert_no_fallback_cleanup,
    _bounded_reap_spawned_process,
    _client_command,
    _close_normally,
    _receive,
    _start_process,
)

_MONITOR_DOMAIN = "paper:group-monitor-relay"
_MONITOR_ID = "arb06-group-monitor-service"
_MONITOR_PRINCIPAL = "arb06-group-monitor-principal"
_MONITOR_INSTANCE = "arb06-group-monitor-ingress"
_MONITOR_SCOPE_REF = "arb06-group-monitor-ingress-v1"
_MONITOR_SECURITY_REF = "arb06-group-monitor-server-curve-v1"
_RELAY_ID = "arb06-group-monitor-relay"
_RELAY_PRINCIPAL = "arb06-group-monitor-relay-principal"
_RELAY_SCOPE_REF = "arb06-group-monitor-relay-client-v1"
_RELAY_SECURITY_REF = "arb06-group-monitor-relay-client-curve-v1"
_OUTBOX_SCOPE = "outbox:arb06-group-residual"
_MONITOR_EVENT_TYPE = "group.residual"
_PROJECTION_SCHEMA = "sdk.group_monitor_projection.v1"
_EVENT_TYPES = (_MONITOR_EVENT_TYPE,)
_REQUEST_TIMEOUT_MS = 1_000
_HEARTBEAT_SECONDS = 5
_MAX_MESSAGE_BYTES = 65_536
_INGRESS_LIMITS = {"max_events": 64, "max_bytes": 262_144, "max_event_bytes": 16_384}


def _component(instance_id: str, options: dict[str, Any]) -> dict[str, Any]:
    return {
        "config_schema_version": 2,
        "settings": {
            "instance_id": instance_id,
            "bind_ports": ["service.telemetry_ingress"],
            "options": {"role": "durable_ingress", **options},
        },
    }


def _monitor_service_config(endpoint: str) -> dict[str, Any]:
    return {
        "service_config_schema_version": 1,
        "process_role": "service",
        "service_id": _MONITOR_ID,
        "scope_ref": "group-monitor-service-root-v1",
        "transport_ref": "group-monitor-listener-v1",
        "registration_policy_ref": "group-monitor-registration-v1",
        "service_requirements_ref": "group-monitor-requirements-v1",
        "components": {"monitor": _component(_MONITOR_INSTANCE, _INGRESS_LIMITS)},
        "communication": {
            "config_schema_version": 1,
            "profiles": {
                "group-monitor-listener-v1": {
                    "backend": "zmq",
                    "config_schema_version": 1,
                    "endpoints": {"rpc_bind": endpoint},
                    "security_profile_ref": _MONITOR_SECURITY_REF,
                    "options": {
                        "high_water_mark": 32,
                        "max_message_bytes": _MAX_MESSAGE_BYTES,
                    },
                }
            },
        },
    }


def _relay_config(endpoint: str):
    """Build an ordinary service-role client config for the explicit relay process."""

    return parse_service_config(
        {
            "service_config_schema_version": 1,
            "process_role": "service",
            "service_id": _RELAY_ID,
            "scope_ref": _RELAY_SCOPE_REF,
            "transport_ref": "group-monitor-relay-listener-v1",
            "registration_policy_ref": "group-monitor-relay-policy-v1",
            "service_requirements_ref": "group-monitor-relay-requirements-v1",
            "components": {
                "monitor": _component("unused-group-monitor-ingress", _INGRESS_LIMITS)
            },
            "clients": {
                "monitor": {
                    "config_schema_version": 1,
                    "settings": {
                        "bindings": [
                            {
                                "mount": "upstream.telemetry",
                                "service_id": _MONITOR_ID,
                                "instance_id": _MONITOR_INSTANCE,
                                "scope_ref": _MONITOR_SCOPE_REF,
                                "transport_ref": "group-monitor-connect-v1",
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
                    "group-monitor-relay-listener-v1": {
                        "backend": "zmq",
                        "config_schema_version": 1,
                        "endpoints": {"rpc_bind": _endpoint()},
                        "security_profile_ref": "group-monitor-relay-unused-server-v1",
                        "options": {
                            "high_water_mark": 32,
                            "max_message_bytes": _MAX_MESSAGE_BYTES,
                        },
                    },
                    "group-monitor-connect-v1": {
                        "backend": "zmq",
                        "config_schema_version": 1,
                        "endpoints": {"rpc_connect": endpoint},
                        "security_profile_ref": _RELAY_SECURITY_REF,
                        "options": {
                            "request_timeout_ms": _REQUEST_TIMEOUT_MS,
                            "high_water_mark": 32,
                            "max_message_bytes": _MAX_MESSAGE_BYTES,
                        },
                    },
                },
            },
        }
    )


def _monitor_host_process(control, raw_config, registry_path, outbox_path, keys, scope):
    config = parse_service_config(raw_config)
    identity = WireIdentity(WireRole.SERVICE, _MONITOR_PRINCIPAL, _MONITOR_ID)
    grant = ResourceGrant(
        _MONITOR_INSTANCE,
        "service.telemetry_ingress",
        _MONITOR_SCOPE_REF,
        scope,
        frozenset({"events.append"}),
    )
    authority = TrustedServiceAuthority(
        identity=identity,
        scope=scope,
        scope_ref=config.scope_ref,
        scope_version="group-monitor-scope-v1",
        security_ref=_MONITOR_SECURITY_REF,
        security_version="curve-key-v1",
        registration_policy_ref=config.registration_policy_ref,
        registration_policy_version="group-monitor-policy-v1",
        service_requirements_ref=config.service_requirements_ref,
        service_requirements_version="group-monitor-requirements-v1",
        curve_public_key=keys["server_public"],
        curve_secret_key=keys["server_secret"],
        principals={keys["relay_public"]: _RELAY_PRINCIPAL},
        grants={
            _RELAY_PRINCIPAL: (
                PrincipalGrant(
                    WireIdentity(WireRole.SERVICE, _RELAY_PRINCIPAL, _RELAY_ID),
                    (grant,),
                ),
            )
        },
    )
    outbox = None
    host = None
    try:
        outbox = DurableOutbox(outbox_path)

        def resource_provider(component, _instance_id, _options, _scope):
            if component != "monitor":
                raise AssertionError("unexpected monitor component")
            return ComponentResources.borrowed(
                {
                    "outbox": outbox,
                    "targets": {scope: MonitorIngressTarget(_OUTBOX_SCOPE, _EVENT_TYPES)},
                },
                ready=lambda: True,
            )

        host = ServiceHost(config, authority, registry_path, resource_provider)
        host.start()
        control.send(("ready", host.endpoint, host.state))
        while True:
            host.serve_once(timeout_ms=25)
            if not control.poll():
                continue
            if control.recv() == ("close",):
                host.close()
                control.send(("closed", host.state))
                return
            raise AssertionError("unknown group monitor host command")
    except BaseException as exc:
        if host is not None and host.state not in {"closed", "failed"}:
            with suppress(BaseException):
                host.close()
        with suppress(Exception):
            control.send(("error", getattr(exc, "code", type(exc).__name__)))
        raise
    finally:
        control.close()


def _relay_process(
    control,
    endpoint,
    group_store_path,
    checkpoint_path,
    keys,
    scope,
):
    reader = None
    relay = None
    runtime = None
    try:
        config = _relay_config(endpoint)
        configured = group_monitor_relay_configured(config, scope_ref=_MONITOR_SCOPE_REF)
        peer = WireIdentity(WireRole.SERVICE, _MONITOR_PRINCIPAL, _MONITOR_ID)
        runtime = ServiceClientRuntime(
            config,
            identity=WireIdentity(WireRole.SERVICE, _RELAY_PRINCIPAL, _RELAY_ID),
            scope_resolver=lambda _ref: scope,
            peer_resolver=lambda _service: peer,
            transport_factory=ZmqTransportFactory(
                {
                    _RELAY_SECURITY_REF: ClientCurveKeys(
                        keys["server_public"], keys["relay_public"], keys["relay_secret"]
                    )
                }
            ),
            requested_operations={"upstream.telemetry": frozenset({"events.append"})},
        )
        try:
            runtime.start()
        except Exception as exc:
            start_code = getattr(exc, "code", type(exc).__name__)
            runtime.close()
            control.send(
                (
                    "start-failed",
                    start_code,
                    runtime.state,
                    runtime.cleanup_failures,
                    runtime.unregister_uncertainties,
                    configured,
                )
            )
            command = control.recv()
            if command != ("close",):
                raise AssertionError("expected relay close after startup failure") from None
            control.send(
                (
                    "closed",
                    runtime.state,
                    runtime.cleanup_failures,
                    runtime.unregister_uncertainties,
                )
            )
            return

        reader = SqliteGroupResidualReader(group_store_path)
        control.send(
            (
                "reader-ready",
                configured,
                reader.generation,
                reader.schema_version,
            )
        )
        relay = ServiceGroupMonitorRelay(
            runtime,
            reader,
            scope_ref=_MONITOR_SCOPE_REF,
            checkpoint_path=checkpoint_path,
        )
        control.send(
            (
                "registered",
                configured,
                runtime.state,
                relay.checkpoint().sequence,
            )
        )

        while True:
            command = control.recv()
            if command == ("close",):
                relay.close()
                relay = None
                reader.close()
                reader = None
                runtime.close()
                control.send(
                    (
                        "closed",
                        runtime.state,
                        runtime.cleanup_failures,
                        runtime.unregister_uncertainties,
                    )
                )
                return
            if command == ("poll",):
                try:
                    result = relay.poll(limit=32)
                except ServiceGroupMonitorRelayError as exc:
                    last_poll = exc.last_poll
                    cursor = relay.checkpoint()
                    control.send(
                        (
                            "poll-failed",
                            exc.code,
                            None if last_poll is None else last_poll.remote_delivered,
                            None if last_poll is None else last_poll.checkpointed,
                            cursor.sequence,
                            cursor.source_event_id,
                        )
                    )
                else:
                    cursor = relay.checkpoint()
                    control.send(
                        (
                            "polled",
                            result.residual_delivered,
                            result.remote_delivered,
                            result.checkpointed,
                            result.total_delivered,
                            cursor.sequence,
                            cursor.source_event_id,
                        )
                    )
                continue
            if command == ("cursors",):
                cursor = relay.checkpoint()
                control.send(
                    (
                        "cursors",
                        cursor.sequence,
                        cursor.source_event_id,
                        cursor.fingerprint,
                        relay.outbox_scope,
                    )
                )
                continue
            raise AssertionError("unknown group monitor relay command")
    except BaseException as exc:
        if relay is not None:
            with suppress(BaseException):
                relay.close()
        if reader is not None:
            with suppress(BaseException):
                reader.close()
        if runtime is not None and runtime.state != "closed":
            with suppress(BaseException):
                runtime.close()
        with suppress(Exception):
            control.send(("error", getattr(exc, "code", type(exc).__name__)))
        raise
    finally:
        control.close()


def _read_monitor_events(path: str):
    outbox = DurableOutbox(path, read_only=True)
    rows, has_more = outbox.read_page_types(_OUTBOX_SCOPE, _EVENT_TYPES, 0, 100)
    assert has_more is False
    return tuple(rows)


def _canonical_json(value: object) -> str:
    return json.dumps(
        value,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=True,
        allow_nan=False,
    )


def _expected_template_outbox_scope(generation: str) -> str:
    digest = hashlib.sha256(
        _canonical_json(
            {
                "schema": "sdk.group_monitor_scope.v1",
                "stream": "group_residual",
                "generation": generation,
            }
        ).encode("utf-8")
    ).hexdigest()
    return "sdk-group-monitor-v1:" + digest


def _expected_projected_event_id(
    generation: str, portfolio_ref: str, group_id: str, source_event_sha256: str
) -> str:
    digest = hashlib.sha256(
        _canonical_json(
            {
                "schema": _PROJECTION_SCHEMA,
                "generation": generation,
                "portfolio_ref": portfolio_ref,
                "group_id": group_id,
                "source_event_sha256": source_event_sha256,
            }
        ).encode("utf-8")
    ).hexdigest()
    return "service-event:" + digest


def test_arb06_group_residual_monitor_event_via_relay(tmp_path: Path) -> None:
    """Partial first leg plus rejected hedge settles and reaches Monitor durably."""

    context = multiprocessing.get_context("spawn")
    monitor_endpoint = _endpoint()
    monitor_server_public, monitor_server_secret = zmq.curve_keypair()
    relay_public, relay_secret = zmq.curve_keypair()
    monitor_keys = {
        "server_public": monitor_server_public,
        "server_secret": monitor_server_secret,
        "relay_public": relay_public,
        "relay_secret": relay_secret,
    }
    monitor_registry_path = str(tmp_path / "group-monitor-registry.sqlite3")
    monitor_outbox_path = str(tmp_path / "group-monitor-outbox.sqlite3")
    checkpoint_path = str(tmp_path / "group-monitor-checkpoint.sqlite3")

    records: list[dict[str, Any]] = []
    forced: list[str] = []
    cleanup_failures: list[str] = []
    monitor_host = relay_process = None
    try:
        with _GroupCluster(
            tmp_path,
            accounts=_ARB03_ACCOUNTS,
            instruments={account: (_V2_INSTRUMENT,) for account in _ARB03_ACCOUNTS},
            initial_states={"account-a": ("cancelled:1.5",), "account-b": ("rejected",)},
            expected_orders={
                "account-a": ((_V2_INSTRUMENT, "2", "100", "buy-limit"),),
                "account-b": ((_V2_INSTRUMENT, "3", "100", "sell-limit"),),
            },
            rule_specs={
                "account-a": {_V2_INSTRUMENT: "2"},
                "account-b": {_V2_INSTRUMENT: "1"},
            },
        ) as cluster:
            snapshot = cluster.submit_group(
                _group_request(
                    _GROUP_ARB03,
                    cluster.metadata_reference_ns,
                    (
                        ("leg-a", "account-a", Side.BUY, Decimal("2"), _V2_INSTRUMENT),
                        ("leg-b", "account-b", Side.SELL, Decimal("4"), _V2_INSTRUMENT),
                    ),
                )
            )
            assert snapshot.state is GroupOrderState.RECOVERY_REQUIRED
            assert snapshot.error_code == "hedge_residual_unresolved"
            assert snapshot.residual_base_units == Decimal("3")
            assert snapshot.request.portfolio_ref == _PORTFOLIO
            assert snapshot.request.strategy_id == _STRATEGY
            assert snapshot.request.account_refs == _ARB03_ACCOUNTS

            group_store_path = cluster.coordinator_paths["groups"]
            persisted = SqliteOrderGroupStore(group_store_path)
            try:
                events = persisted.events(_PORTFOLIO, _GROUP_ARB03)
            finally:
                persisted.close()
            residual_events = tuple(
                event for event in events if event.event_type == "GROUP_RECOVERY_REQUIRED"
            )
            assert len(residual_events) == 1
            residual_event = residual_events[0]
            assert Decimal(residual_event.payload["residual_base_units"]) == Decimal("3")
            assert residual_event.payload["first_leg_id"] == "leg-a"
            assert residual_event.payload["second_leg_id"] == "leg-b"
            assert Decimal(residual_event.payload["first_filled_quantity"]) == Decimal("1.5")
            assert Decimal(residual_event.payload["second_filled_quantity"]) == Decimal("0")

            # Read the raw durable row for the fields the public event DTO
            # does not expose (stored digest and revision).
            connection = sqlite3.connect(group_store_path)
            try:
                row = connection.execute(
                    """
                    SELECT event_sha256, revision FROM order_group_events
                    WHERE portfolio_ref = ? AND group_id = ? AND sequence = ?
                    """,
                    (_PORTFOLIO, _GROUP_ARB03, residual_event.sequence),
                ).fetchone()
            finally:
                connection.close()
            assert row is not None
            source_event_sha256 = str(row[0])
            source_revision = int(row[1])

            # The same read-only reader the relay process uses: pin the
            # generation and the single residual fact independently.
            probe = SqliteGroupResidualReader(group_store_path)
            try:
                generation = probe.generation
                assert probe.schema_version == 2
                page = probe.read_residual_page(after_sequence=0, limit=100)
                assert page.generation == generation
                assert len(page.events) == 1
                probe_event = page.events[0]
                assert probe_event.sequence == residual_event.sequence
                assert probe_event.event_sha256 == source_event_sha256
                assert probe_event.revision == source_revision
                assert probe_event.strategy_id == _STRATEGY
                assert probe_event.account_refs == _ARB03_ACCOUNTS
                assert probe_event.state == "RECOVERY_REQUIRED"
                assert probe_event.reason == "hedge_residual_unresolved"
                assert (
                    probe_event.residual_base_units
                    == residual_event.payload["residual_base_units"]
                )
            finally:
                probe.close()

            monitor_scope = WireScope(
                _MONITOR_DOMAIN,
                account_ref="account-a",
                strategy_ref=snapshot.request.strategy_id,
                portfolio_ref=snapshot.request.portfolio_ref,
            )

            monitor_raw = _monitor_service_config(monitor_endpoint)
            monitor_host = _start_process(
                context,
                _monitor_host_process,
                (monitor_raw, monitor_registry_path, monitor_outbox_path, monitor_keys, monitor_scope),
                "arb06-group-monitor-host",
            )
            records.append(monitor_host)
            monitor_ready = _receive(monitor_host["pipe"])
            assert monitor_ready[0] == "ready", monitor_ready
            assert monitor_ready[1] == monitor_endpoint

            relay_process = _start_process(
                context,
                _relay_process,
                (monitor_endpoint, group_store_path, checkpoint_path, monitor_keys, monitor_scope),
                "arb06-group-monitor-relay",
            )
            records.append(relay_process)
            relay_ready = _receive(relay_process["pipe"])
            assert relay_ready[0] == "reader-ready", relay_ready
            assert relay_ready[1] is True
            assert relay_ready[2] == generation
            assert relay_ready[3] == 2
            registered = _receive(relay_process["pipe"])
            assert registered == ("registered", True, "registered", 0)

            polled = _client_command(relay_process, "poll")
            assert polled == (
                "polled",
                1,
                1,
                1,
                1,
                residual_event.sequence,
                source_event_sha256,
            )

            persisted_rows = _read_monitor_events(monitor_outbox_path)
            assert len(persisted_rows) == 1
            durable = persisted_rows[0]
            assert durable.sequence == 1
            event = durable.event
            assert event.event_type == _MONITOR_EVENT_TYPE
            assert event.scope == _OUTBOX_SCOPE
            assert event.data["producer"]["role"] == "service"
            assert event.data["producer"]["principal_ref"] == _RELAY_PRINCIPAL
            projected_event_id = _expected_projected_event_id(
                generation, _PORTFOLIO, _GROUP_ARB03, source_event_sha256
            )
            assert event.data["source_event_id"] == projected_event_id
            assert event.event_id.startswith("service-event:")
            assert event.occurred_at == residual_event.created_at_ns / 1_000_000_000

            payload = event.data["payload"]
            # The durable outbox freezes nested sequences, so ``account_refs``
            # arrives as a tuple in the mappingproxy view; the persisted JSON
            # form (list) is pinned separately below.
            assert payload == {
                "schema": _PROJECTION_SCHEMA,
                "journal_generation": generation,
                "source_sequence": residual_event.sequence,
                "source_event_sha256": source_event_sha256,
                "source_revision": source_revision,
                "source_created_at_ns": residual_event.created_at_ns,
                "portfolio_ref": _PORTFOLIO,
                "group_id": _GROUP_ARB03,
                "strategy_id": _STRATEGY,
                "account_refs": _ARB03_ACCOUNTS,
                "state": "RECOVERY_REQUIRED",
                "reason": "hedge_residual_unresolved",
                "residual_base_units": residual_event.payload["residual_base_units"],
                "first_leg_id": "leg-a",
                "second_leg_id": "leg-b",
                "first_filled_quantity": residual_event.payload["first_filled_quantity"],
                "second_filled_quantity": residual_event.payload["second_filled_quantity"],
            }
            assert Decimal(payload["residual_base_units"]) == Decimal("3")

            # Raw durable row: the stored canonical JSON keeps the emitted
            # list form for ``account_refs`` and the projected identity.
            raw_connection = sqlite3.connect(
                f"file:{monitor_outbox_path}?mode=ro", uri=True
            )
            try:
                raw_row = raw_connection.execute(
                    "SELECT event_id, scope, event_type, data_json "
                    "FROM monitor_outbox_events WHERE event_id = ?",
                    (event.event_id,),
                ).fetchone()
            finally:
                raw_connection.close()
            assert raw_row is not None
            assert raw_row[0] == event.event_id
            assert raw_row[1] == _OUTBOX_SCOPE
            assert raw_row[2] == _MONITOR_EVENT_TYPE
            raw_payload = json.loads(raw_row[3])["payload"]
            assert raw_payload == dict(payload, account_refs=list(_ARB03_ACCOUNTS))

            # The explicit poll is idempotent: the durable cursor holds and
            # Monitor's outbox gains nothing on a second poll.
            unchanged = _client_command(relay_process, "poll")
            assert unchanged == (
                "polled",
                0,
                0,
                0,
                0,
                residual_event.sequence,
                source_event_sha256,
            )
            assert len(_read_monitor_events(monitor_outbox_path)) == 1

            cursors = _client_command(relay_process, "cursors")
            assert cursors[0] == "cursors"
            assert cursors[1] == residual_event.sequence
            assert cursors[2] == source_event_sha256
            assert type(cursors[3]) is str and len(cursors[3]) == 64
            # The relay-side template scope is bound to the journal generation;
            # the durable Monitor event was re-scoped to the target's outbox
            # scope (``_OUTBOX_SCOPE``) by the ingress.
            assert cursors[4] == _expected_template_outbox_scope(generation)
            assert cursors[4].startswith("sdk-group-monitor-v1:")
            assert cursors[4] != _OUTBOX_SCOPE

            # Monitoring is read-only: the coordinator fact is unchanged and
            # each provider still saw exactly one dispatch.
            still_open = SqliteOrderGroupStore(group_store_path)
            try:
                assert still_open.get(_PORTFOLIO, _GROUP_ARB03) == snapshot
            finally:
                still_open.close()
            for account_ref in _ARB03_ACCOUNTS:
                _assert_provider_writes(
                    Path(cluster.account_paths[account_ref]["provider"]), 1
                )

            relay_closed = _close_normally(relay_process, expected_tag="closed")
            assert relay_closed[1:] == ("closed", 0, 0)
            relay_process = None
            monitor_closed = _close_normally(monitor_host, expected_tag="closed")
            assert monitor_closed == ("closed", "closed")
            monitor_host = None
    finally:
        for record in reversed(records):
            _bounded_reap_spawned_process(record, forced, cleanup_failures)
        _assert_no_fallback_cleanup(forced, cleanup_failures)
