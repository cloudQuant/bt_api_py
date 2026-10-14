"""Real CURVE integration for independently projected account read views.

The provider, account observations, keys, and SQLite state are local synthetic
fixtures. The gateway publishes trusted per-strategy views; generic SDK
consumers apply and acknowledge those view pages without importing gateway
projection code.
"""

from __future__ import annotations

import json
import multiprocessing
from collections.abc import Mapping
from contextlib import suppress
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any
from uuid import uuid4

import zmq
from bt_api_base.account_protocol import (
    AccountBalance,
    AccountKey,
    AccountObservation,
    AccountPosition,
    AccountResourceKey,
)
from bt_api_base.market_protocol import MarketInstrumentKey
from bt_api_base.service_protocol import WireIdentity, WireRole, WireScope
from bt_api_gateway import GatewayCommandKind, GatewayEventJournal, GatewayPrincipal
from bt_api_gateway.account_session_worker import GatewayAccountSessionWorker
from bt_api_gateway.account_view_projection import GatewayAccountViewPolicy
from bt_api_gateway.service_component import GatewayServiceAccountScope
from bt_api_gateway.session_guard import LocalAccountSessionGuard

from bt_api_py.service_account_consumer import ServiceAccountConsumer
from bt_api_py.service_components import ComponentResources
from bt_api_py.service_config import parse_client_config, parse_service_config
from bt_api_py.service_host import ServiceHost, TrustedServiceAuthority
from bt_api_py.service_registry import PrincipalGrant, ResourceGrant
from bt_api_py.service_runtime import (
    ClientCurveKeys,
    ServiceClientRuntime,
    ZmqTransportFactory,
)
from tests.unit import test_service_account_consumer_integration as consumer_helpers
from tests.unit import test_service_account_integration as account_helpers

_ACCOUNT_REF = "account-a"
_DOMAIN_REF = "paper:account-view-integration"
_JOURNAL_SCOPE = "journal:account-view-integration"
_STATE_SCOPE = "state:account-view-integration"
_VIEW_A = "view:spot-balances"
_VIEW_B = "view:linear-positions"
_STRATEGY_A = "strategy-view-a"
_STRATEGY_B = "strategy-view-b"
_PRINCIPAL_A = "principal-view-a"
_PRINCIPAL_B = "principal-view-b"
_SPOT = AccountResourceKey("spot", "cash")
_LINEAR = AccountResourceKey("linear", "cross")
_COVERAGE = (_SPOT, _LINEAR)
_POLICY_A = GatewayAccountViewPolicy(_VIEW_A, (_SPOT,), frozenset({"balances"}))
_POLICY_B = GatewayAccountViewPolicy(_VIEW_B, (_LINEAR,), frozenset({"positions"}))
_OPERATIONS = frozenset({"account.read_after", "account.ack", "account.progress"})


class _SyntheticViewProvider:
    """One counted read-only source with reproducible numbered observations."""

    def __init__(self, *, first_sequence: int, anchor_utc: str) -> None:
        self.sequence = first_sequence
        self.anchor = datetime.fromisoformat(anchor_utc)
        self.connect_calls = 0
        self.read_calls = 0
        self.close_calls = 0
        self.generation: str | None = None

    def connect(self, *, account_key: AccountKey, generation: str, timeout: float) -> None:
        if (
            account_key != account_helpers._ACCOUNT_KEYS[_ACCOUNT_REF]
            or not generation
            or timeout <= 0
            or self.generation is not None
        ):
            raise AssertionError("synthetic account session received invalid connection metadata")
        self.connect_calls += 1
        self.generation = generation

    def read_account(self, *, generation: str, timeout: float) -> AccountObservation:
        if generation != self.generation or timeout <= 0:
            raise AssertionError("synthetic read did not use the active provider generation")
        sequence = self.sequence
        self.sequence += 1
        self.read_calls += 1
        return _observation(sequence, self.anchor)

    def close(self) -> bool:
        self.close_calls += 1
        self.generation = None
        return True


def _observation(sequence: int, anchor: datetime) -> AccountObservation:
    account_key = account_helpers._ACCOUNT_KEYS[_ACCOUNT_REF]
    observed_at = (anchor + timedelta(seconds=sequence)).isoformat().replace("+00:00", "Z")
    spot_observed_at = (
        (anchor + timedelta(seconds=1 if sequence == 2 else sequence))
        .isoformat()
        .replace("+00:00", "Z")
    )
    spot_total = {1: "100.00", 2: "100.00", 3: "101.25"}.get(sequence, "103.00")
    balances = (
        AccountBalance(
            resource=_SPOT,
            currency="USD",
            total=spot_total,
            available=spot_total,
            locked="0",
            observed_at_utc=spot_observed_at,
        ),
        AccountBalance(
            resource=_LINEAR,
            currency="USD",
            total="900",
            available="850",
            locked="50",
            observed_at_utc=observed_at,
        ),
    )
    positions = ()
    if sequence in {1, 2}:
        instrument = MarketInstrumentKey(
            provider=account_key.provider,
            venue=account_key.venue,
            environment=account_key.environment,
            product_type=_LINEAR.product_type,
            instrument_id="synthetic-perpetual",
            native_symbol="SYNTH-PERP",
        )
        positions = (
            AccountPosition(
                resource=_LINEAR,
                instrument=instrument,
                side="net",
                quantity="2.000" if sequence == 1 else "3.000",
                qty_unit="contract",
                average_price="10.00",
                price_currency="USD",
                observed_at_utc=observed_at,
            ),
        )
    return AccountObservation(
        account_key=account_key,
        observation_id=f"synthetic-view-observation-{sequence}",
        coverage=_COVERAGE,
        balances=balances,
        positions=positions,
        valuation=None,
        started_at_utc=observed_at,
        completed_at_utc=observed_at,
        consistency="multi_query",
        balances_complete=True,
        positions_complete=True,
    )


def _view_resource_provider(
    provider: _SyntheticViewProvider,
    *,
    database_path: str,
    guard_directory: str,
    authorized_principals: tuple[tuple[str, str], ...],
    worker_holder: dict[str, GatewayAccountSessionWorker],
):
    def resources(component, _instance_id, options, _scope):
        if component != "gateway" or options.get("role") != "account_session_worker":
            raise AssertionError("only the trusted account-view gateway is configured")
        journal = GatewayEventJournal(database_path)
        account_scope = GatewayServiceAccountScope(
            account_ref=_ACCOUNT_REF,
            execution_domain_ref=_DOMAIN_REF,
            journal_account_scope=_JOURNAL_SCOPE,
            strategy_scope_map={_STRATEGY_A: _VIEW_A, _STRATEGY_B: _VIEW_B},
        )
        principals = {
            principal_ref: GatewayPrincipal(
                principal_id=principal_ref,
                account_scopes=frozenset({_JOURNAL_SCOPE}),
                strategy_scopes=frozenset({view_scope}),
                allowed_kinds=frozenset({GatewayCommandKind.READ}),
            )
            for principal_ref, view_scope in authorized_principals
        }
        worker = GatewayAccountSessionWorker(
            provider,
            journal,
            account_key=account_helpers._ACCOUNT_KEYS[_ACCOUNT_REF],
            journal_account_scope=_JOURNAL_SCOPE,
            state_scope=_STATE_SCOPE,
            guard=LocalAccountSessionGuard(
                Path(guard_directory),
                account_helpers._ACCOUNT_KEYS[_ACCOUNT_REF].account_key_id,
                owner_ref="synthetic-account-view-host",
            ),
            required_coverage=_COVERAGE,
            view_policies=(_POLICY_A, _POLICY_B),
            refresh_interval_seconds=60,
            max_staleness_seconds=3_600,
            request_timeout_seconds=2,
        )
        worker_holder["worker"] = worker
        return ComponentResources.owned(
            {
                "journal": journal,
                "accounts": {_ACCOUNT_REF: account_scope},
                "principals": principals,
            },
            ready=lambda: True,
            close=worker.close,
            internal_ports={"service.provider_session": worker},
        )

    return resources


def _view_service_process(
    control,
    endpoint: str,
    raw_config: dict[str, Any],
    registry_path: str,
    journal_path: str,
    guard_directory: str,
    server_keys: tuple[bytes, bytes],
    client_audience: tuple[tuple[bytes, str, str], ...],
    first_sequence: int,
    anchor_utc: str,
) -> None:
    host = None
    worker = None
    provider = _SyntheticViewProvider(first_sequence=first_sequence, anchor_utc=anchor_utc)
    try:
        config = parse_service_config(raw_config)
        service = account_helpers._account_service_info(
            _ACCOUNT_REF, endpoint, "account-view-curve-v1"
        )
        worker_holder: dict[str, GatewayAccountSessionWorker] = {}
        strategy_to_view = {_STRATEGY_A: _VIEW_A, _STRATEGY_B: _VIEW_B}
        authorized = tuple(
            (principal, strategy_to_view[strategy]) for _, principal, strategy in client_audience
        )
        provider_factory = _view_resource_provider(
            provider,
            database_path=journal_path,
            guard_directory=guard_directory,
            authorized_principals=authorized,
            worker_holder=worker_holder,
        )
        identities = {
            principal: WireIdentity(WireRole.STRATEGY_CLIENT, principal, strategy)
            for _, principal, strategy in client_audience
        }
        grants = {}
        for _public_key, principal_ref, strategy_ref in client_audience:
            resource = ResourceGrant(
                service["instance_id"],
                "service.account_read",
                service["scope_ref"],
                WireScope(_DOMAIN_REF, account_ref=_ACCOUNT_REF, strategy_ref=strategy_ref),
                _OPERATIONS,
            )
            grants[principal_ref] = (PrincipalGrant(identities[principal_ref], (resource,)),)
        host = ServiceHost(
            config,
            TrustedServiceAuthority(
                identity=WireIdentity(
                    WireRole.SERVICE, service["principal_ref"], service["service_id"]
                ),
                scope=WireScope(_DOMAIN_REF),
                scope_ref=config.scope_ref,
                scope_version="account-view-scope-v1",
                security_ref=service["security_ref"],
                security_version="account-view-curve-v1",
                registration_policy_ref=config.registration_policy_ref,
                registration_policy_version="account-view-policy-v1",
                service_requirements_ref=config.service_requirements_ref,
                service_requirements_version="account-view-requirements-v1",
                curve_public_key=server_keys[0],
                curve_secret_key=server_keys[1],
                principals={public_key: principal for public_key, principal, _ in client_audience},
                grants=grants,
            ),
            registry_path,
            provider_factory,
        )
        host.start()
        worker = worker_holder["worker"]
        control.send(("ready", host.endpoint, provider.connect_calls, provider.read_calls))
        while True:
            host.serve_once(10)
            if not control.poll():
                continue
            command = control.recv()
            if command[0] == "refresh":
                snapshot = worker.refresh()
                control.send(("refresh-requested", snapshot.cursor, provider.read_calls))
            elif command[0] == "status":
                control.send(
                    ("status", provider.connect_calls, provider.read_calls, provider.close_calls)
                )
            elif command[0] == "close":
                host.close()
                control.send(
                    (
                        "closed",
                        host.state,
                        provider.connect_calls,
                        provider.read_calls,
                        provider.close_calls,
                    )
                )
                return
            else:
                raise AssertionError("unknown account-view host command")
    except BaseException as exc:
        if host is not None and host.state not in {"closed", "failed"}:
            with suppress(BaseException):
                host.close()
        with suppress(Exception):
            control.send(("error", getattr(exc, "code", type(exc).__name__)))
        raise
    finally:
        control.close()


def _plain(value: Any) -> Any:
    if isinstance(value, Mapping):
        return {key: _plain(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_plain(item) for item in value]
    return value


def _snapshot_reducer(
    _initial: Mapping[str, Any], snapshot: Mapping[str, Any]
) -> Mapping[str, Any]:
    # AccountProjectionStore passes the validated snapshot envelope here;
    # the account-view schema is the envelope's payload.
    payload = snapshot.get("payload")
    if (
        not isinstance(payload, Mapping)
        or payload.get("schema_id") != "sdk.account.view"
        or payload.get("schema_version") != 1
    ):
        raise AssertionError("unexpected published account-view snapshot schema")
    return _plain(payload)


def _row_identity(kind: str, row: Mapping[str, Any]) -> str:
    plain_row = _plain(row)
    if kind == "balances":
        identity = {"resource": plain_row["resource"], "currency": plain_row["currency"]}
    elif kind == "positions":
        identity = {
            "resource": plain_row["resource"],
            "instrument": plain_row["instrument"],
            "side": plain_row["side"],
        }
    else:
        identity = {
            "resource": plain_row["resource"],
            "instrument": plain_row["instrument"],
            "order_id": plain_row["order_id"],
        }
    return json.dumps(identity, sort_keys=True, separators=(",", ":"))


def _event_reducer(current: Mapping[str, Any], event: Mapping[str, Any]) -> Mapping[str, Any]:
    if event.get("kind") != "account.view_delta":
        raise AssertionError("unexpected account-view event kind")
    delta = event["payload"]
    if delta.get("schema_id") != "sdk.account.view.delta" or delta.get("schema_version") != 1:
        raise AssertionError("unexpected published account-view delta schema")
    if delta.get("view_scope") != current.get("view_scope"):
        raise AssertionError("view delta crossed its trusted scope")
    result = _plain(current)
    for kind, changes in delta["changes"].items():
        rows = {_row_identity(kind, row): _plain(row) for row in result.get(kind, ())}
        for identity in changes["delete"]:
            rows.pop(_row_identity(kind, identity), None)
        for row in changes["upsert"]:
            rows[_row_identity(kind, row)] = _plain(row)
        result[kind] = [rows[key] for key in sorted(rows)]
    if "valuation" in delta:
        result["valuation"] = _plain(delta["valuation"]["set"])
    return result


def _view_client_process(
    control,
    principal_ref: str,
    strategy_ref: str,
    view_scope: str,
    other_strategy_ref: str,
    raw_config: dict[str, Any],
    service: dict[str, Any],
    client_keys: tuple[bytes, bytes, bytes],
    projection_path: str,
) -> None:
    runtime = None
    consumer = None
    try:
        config = parse_client_config(raw_config)
        identity = WireIdentity(WireRole.STRATEGY_CLIENT, principal_ref, strategy_ref)
        runtime = ServiceClientRuntime(
            config,
            identity=identity,
            scope_resolver=lambda _scope_ref: WireScope(
                _DOMAIN_REF,
                account_ref=_ACCOUNT_REF,
                strategy_ref=strategy_ref,
            ),
            peer_resolver=lambda _service_id: service["peer"],
            transport_factory=ZmqTransportFactory(
                {service["security_ref"]: ClientCurveKeys(*client_keys)}
            ),
            run_id=uuid4().hex,
            requested_operations={"store.account": _OPERATIONS},
        )
        runtime.start()
        consumer = ServiceAccountConsumer(
            runtime,
            "store.account",
            account_ref=_ACCOUNT_REF,
            path=projection_path,
            projection_version="sdk.account.view.v1/local-test-projection-v1",
            initial_state={"view_scope": view_scope, "rows": {}},
            expected_account_scope=_JOURNAL_SCOPE,
            expected_strategy_scope=view_scope,
            snapshot_reducer=_snapshot_reducer,
            event_reducer=_event_reducer,
        )
        binding = runtime.bindings[0]
        control.send(
            (
                "ready",
                runtime.run_id,
                binding.scope.run_id,
                binding.service_epoch,
                binding.registration_id,
                runtime.healthy,
            )
        )
        while True:
            delay = runtime.next_maintenance_delay()
            if delay is not None and delay <= 0:
                runtime.maintain()
            if not control.poll(0.05):
                continue
            command = control.recv()
            if command[0] == "poll":
                checkpoint = consumer.poll()
                progress = runtime.call("store.account", "account.progress", {})[
                    "consumer_progress"
                ]
                control.send(
                    (
                        "checkpoint",
                        checkpoint.stream_epoch,
                        checkpoint.cursor,
                        _plain(checkpoint.projection),
                        checkpoint.pending_ack,
                        runtime.run_id,
                        _plain(progress),
                    )
                )
            elif command[0] == "forge":
                binding_view = runtime.bindings[0]
                profile = config.communication["profiles"][service["client_transport_ref"]]
                transport = ZmqTransportFactory(
                    {service["security_ref"]: ClientCurveKeys(*client_keys)}
                )(profile, service["peer"])
                try:
                    denied = consumer_helpers._direct_call(
                        transport,
                        identity,
                        replace(binding_view.scope, strategy_ref=other_strategy_ref),
                        binding_view,
                        "account.read_after",
                        {"cursor": 0, "limit": 1, "track_delivery": True},
                    )
                finally:
                    transport.close()
                control.send(("forged-result", _plain(denied.payload)))
            elif command[0] == "close":
                consumer.close()
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
            else:
                raise AssertionError("unknown account-view client command")
    except BaseException as exc:
        if consumer is not None:
            with suppress(BaseException):
                consumer.close()
        if runtime is not None:
            with suppress(BaseException):
                runtime.close()
        with suppress(Exception):
            control.send(("error", getattr(exc, "code", type(exc).__name__)))
        raise
    finally:
        control.close()


def _start_child(context, target, args, label: str) -> dict[str, Any]:
    return account_helpers._start_process(context, target, args, label)


def _checkpoint(
    record: dict[str, Any],
) -> tuple[str, int, dict[str, Any], bool, str, dict[str, Any]]:
    message = account_helpers._client_command(record, "poll")
    assert message[0] == "checkpoint", message
    return message[1], message[2], message[3], message[4], message[5], message[6]


def _start_view_client(
    context,
    records: list[dict[str, Any]],
    *,
    principal_ref: str,
    strategy_ref: str,
    view_scope: str,
    other_strategy_ref: str,
    raw_config: dict[str, Any],
    service: dict[str, Any],
    client_keys: tuple[bytes, bytes, bytes],
    projection_path: Path,
    label: str,
) -> tuple[dict[str, Any], tuple[Any, ...]]:
    record = _start_child(
        context,
        _view_client_process,
        (
            principal_ref,
            strategy_ref,
            view_scope,
            other_strategy_ref,
            raw_config,
            service,
            client_keys,
            str(projection_path),
        ),
        label,
    )
    records.append(record)
    ready = account_helpers._receive(record["pipe"], timeout=15)
    assert ready[0] == "ready" and ready[5] is True, ready
    return record, ready[1:]


def test_spawned_curve_consumers_receive_only_their_durable_account_views(tmp_path):
    context = multiprocessing.get_context("spawn")
    endpoint = account_helpers._free_endpoints(1)[0]
    server_keys = zmq.curve_keypair()
    service = account_helpers._account_service_info(_ACCOUNT_REF, endpoint, "account-view-curve-v1")
    audience = []
    client_material = {}
    for principal_ref, strategy_ref in ((_PRINCIPAL_A, _STRATEGY_A), (_PRINCIPAL_B, _STRATEGY_B)):
        public_key, secret_key = zmq.curve_keypair()
        client_material[strategy_ref] = (server_keys[0], public_key, secret_key)
        audience.append((public_key, principal_ref, strategy_ref))
    raw_service_config = account_helpers._service_config(service)
    client_configs = {
        _STRATEGY_A: account_helpers._client_config(
            _STRATEGY_A, {_ACCOUNT_REF: service}, (_ACCOUNT_REF,)
        ),
        _STRATEGY_B: account_helpers._client_config(
            _STRATEGY_B, {_ACCOUNT_REF: service}, (_ACCOUNT_REF,)
        ),
    }
    registry_path = str(tmp_path / "view-service-registry.sqlite3")
    journal_path = str(tmp_path / "view-account-journal.sqlite3")
    guard_directory = str(tmp_path / "view-session-guards")
    Path(guard_directory).mkdir()
    anchor = (datetime.now(UTC) - timedelta(minutes=1)).isoformat()
    records: list[dict[str, Any]] = []
    forced_cleanup: list[str] = []
    cleanup_failures: list[str] = []
    host_record = None
    clients: dict[str, dict[str, Any]] = {}
    client_ready: dict[str, tuple[Any, ...]] = {}

    def start_host(first_sequence: int, label: str):
        record = _start_child(
            context,
            _view_service_process,
            (
                endpoint,
                raw_service_config,
                registry_path,
                journal_path,
                guard_directory,
                server_keys,
                tuple(audience),
                first_sequence,
                anchor,
            ),
            label,
        )
        records.append(record)
        ready = account_helpers._receive(record["pipe"], timeout=20)
        assert ready[0] == "ready" and ready[1] == endpoint, ready
        return record, ready

    def start_clients(suffix: str) -> None:
        for principal, strategy, view_scope, other in (
            (_PRINCIPAL_A, _STRATEGY_A, _VIEW_A, _STRATEGY_B),
            (_PRINCIPAL_B, _STRATEGY_B, _VIEW_B, _STRATEGY_A),
        ):
            key_info = client_material[strategy]
            client_keys = key_info
            record, ready = _start_view_client(
                context,
                records,
                principal_ref=principal,
                strategy_ref=strategy,
                view_scope=view_scope,
                other_strategy_ref=other,
                raw_config=client_configs[strategy],
                service=service,
                client_keys=client_keys,
                projection_path=tmp_path / f"{strategy}-projection.sqlite3",
                label=f"{strategy}-{suffix}",
            )
            clients[strategy] = record
            client_ready[strategy] = ready

    try:
        host_record, host_ready = start_host(1, "account-view-host-first")
        assert host_ready[2:] == (1, 1), host_ready
        start_clients("first")
        assert client_ready[_STRATEGY_A][0] != client_ready[_STRATEGY_B][0]
        assert client_ready[_STRATEGY_A][1] != client_ready[_STRATEGY_B][1]
        assert client_ready[_STRATEGY_A][0] == client_ready[_STRATEGY_A][1]
        assert client_ready[_STRATEGY_B][0] == client_ready[_STRATEGY_B][1]

        forged = account_helpers._client_command(clients[_STRATEGY_A], "forge")
        assert forged[0] == "forged-result", forged
        assert forged[1]["ok"] is False and forged[1]["code"] == "SCOPE_DENIED", forged

        epoch_a, cursor_a, view_a, pending_a, run_a, progress_a = _checkpoint(clients[_STRATEGY_A])
        epoch_b, cursor_b, view_b, pending_b, run_b, progress_b = _checkpoint(clients[_STRATEGY_B])
        assert epoch_a and epoch_b and epoch_a != epoch_b
        assert cursor_a == cursor_b == 1 and pending_a is pending_b is False
        assert progress_a == {
            "stream_epoch": epoch_a,
            "offered_cursor": 1,
            "acknowledged_cursor": 1,
        }
        assert progress_b == {
            "stream_epoch": epoch_b,
            "offered_cursor": 1,
            "acknowledged_cursor": 1,
        }
        assert run_a == client_ready[_STRATEGY_A][0]
        assert run_b == client_ready[_STRATEGY_B][0]
        assert set(view_a) == {
            "schema_id",
            "schema_version",
            "view_scope",
            "consistency",
            "coverage",
            "fact_kinds",
            "balances",
        }
        assert set(view_b) == {
            "schema_id",
            "schema_version",
            "view_scope",
            "consistency",
            "coverage",
            "fact_kinds",
            "positions",
        }
        assert view_a["schema_id"] == view_b["schema_id"] == "sdk.account.view"
        assert view_a["view_scope"] == _VIEW_A and view_b["view_scope"] == _VIEW_B
        assert view_a["fact_kinds"] == ["balances"]
        assert view_b["fact_kinds"] == ["positions"]
        assert view_a["balances"][0]["resource"] == _SPOT.to_dict()
        assert view_a["balances"][0]["total"] == "100.00"
        assert view_b["positions"][0]["resource"] == _LINEAR.to_dict()
        assert view_b["positions"][0]["quantity"] == "2.000"
        assert all(
            field not in view
            for view in (view_a, view_b)
            for field in ("policy_digest", "account_key", "observation_id", "session_generation")
        )

        status = account_helpers._host_command(host_record, "status")
        assert status[0] == "status" and status[1:] == (1, 1, 0), status
        hidden_refresh = account_helpers._host_command(host_record, "refresh")
        assert hidden_refresh == ("refresh-requested", 2, 2), hidden_refresh
        hidden_a = _checkpoint(clients[_STRATEGY_A])
        hidden_b = _checkpoint(clients[_STRATEGY_B])
        assert hidden_a[1] == 1 and hidden_a[2] == view_a and hidden_a[4] == run_a
        assert hidden_a[5]["acknowledged_cursor"] == 1
        assert hidden_b[1] == 2 and hidden_b[2]["positions"][0]["quantity"] == "3.000"
        assert hidden_b[5]["acknowledged_cursor"] == 2
        assert "balances" not in hidden_b[2]
        assert hidden_a[3] is hidden_b[3] is False
        status = account_helpers._host_command(host_record, "status")
        assert status[1:] == (1, 2, 0), status

        visible_refresh = account_helpers._host_command(host_record, "refresh")
        assert visible_refresh == ("refresh-requested", 3, 3), visible_refresh
        changed_a = _checkpoint(clients[_STRATEGY_A])
        changed_b = _checkpoint(clients[_STRATEGY_B])
        assert changed_a[1] == 2 and changed_a[2]["balances"][0]["total"] == "101.25"
        assert changed_a[5]["acknowledged_cursor"] == 2
        assert changed_b[1] == 3 and changed_b[2]["positions"] == []
        assert changed_b[5]["acknowledged_cursor"] == 3
        assert changed_a[2]["view_scope"] == _VIEW_A
        assert changed_b[2]["view_scope"] == _VIEW_B
        status = account_helpers._host_command(host_record, "status")
        assert status[1:] == (1, 3, 0), status

        for strategy in (_STRATEGY_A, _STRATEGY_B):
            closed = account_helpers._close_normally(clients[strategy], expected_tag="closed")
            assert closed == ("closed", "closed", 0, 0), closed
        first_service_epoch = client_ready[_STRATEGY_A][2]
        closed_host = account_helpers._close_normally(host_record, expected_tag="closed")
        assert closed_host == ("closed", "closed", 1, 3, 1), closed_host

        clients.clear()
        client_ready.clear()
        host_record, restarted_host_ready = start_host(3, "account-view-host-restarted")
        assert restarted_host_ready[2:] == (1, 1), restarted_host_ready
        start_clients("restarted")
        assert client_ready[_STRATEGY_A][0] != run_a
        assert client_ready[_STRATEGY_B][0] != run_b
        assert client_ready[_STRATEGY_A][2] == client_ready[_STRATEGY_B][2]
        assert client_ready[_STRATEGY_A][2] != first_service_epoch

        resumed_a = _checkpoint(clients[_STRATEGY_A])
        resumed_b = _checkpoint(clients[_STRATEGY_B])
        assert resumed_a[0] == epoch_a and resumed_a[1] == 2
        assert resumed_a[5]["acknowledged_cursor"] == 2
        assert resumed_a[2]["balances"][0]["total"] == "101.25"
        assert resumed_b[0] == epoch_b and resumed_b[1] == 3
        assert resumed_b[5]["acknowledged_cursor"] == 3
        assert resumed_b[2]["positions"] == []
        status = account_helpers._host_command(host_record, "status")
        assert status[1:] == (1, 1, 0), status

        continued = account_helpers._host_command(host_record, "refresh")
        # Restart accepted a fresh canonical observation at source cursor 4
        # without changing either visible view; this refresh advances it to 5.
        assert continued == ("refresh-requested", 5, 2), continued
        continued_a = _checkpoint(clients[_STRATEGY_A])
        continued_b = _checkpoint(clients[_STRATEGY_B])
        assert continued_a[0] == epoch_a and continued_a[1] == 3
        assert continued_a[5]["acknowledged_cursor"] == 3
        assert continued_a[2]["balances"][0]["total"] == "103.00"
        assert continued_b[0] == epoch_b and continued_b[1] == 3
        assert continued_b[5]["acknowledged_cursor"] == 3
        assert continued_b[2]["positions"] == []
        status = account_helpers._host_command(host_record, "status")
        assert status[1:] == (1, 2, 0), status

        for strategy in (_STRATEGY_A, _STRATEGY_B):
            closed = account_helpers._close_normally(clients[strategy], expected_tag="closed")
            assert closed == ("closed", "closed", 0, 0), closed
        closed_host = account_helpers._close_normally(host_record, expected_tag="closed")
        assert closed_host == ("closed", "closed", 1, 2, 1), closed_host
    finally:
        for record in reversed(records):
            account_helpers._bounded_reap_spawned_process(record, forced_cleanup, cleanup_failures)
        account_helpers._assert_no_fallback_cleanup(forced_cleanup, cleanup_failures)
