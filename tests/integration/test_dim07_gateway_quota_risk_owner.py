"""DIM07 quota callback through the authenticated Risk account owner.

The ServiceHost, SDK runtime, Risk journal and provider leaf are the accepted
DIM04 local fixture. A trusted resource-provider closure injects the stable
callback from a Gateway account binding into the Risk owner. This proves the
local composition contract only; the provider remains synthetic.
"""

from __future__ import annotations

import json
import multiprocessing
import sqlite3
import time
from pathlib import Path
from typing import Any

import pytest
import zmq
from bt_api_base.account_protocol import AccountKey, AccountResourceKey
from bt_api_gateway.event_journal import GatewayEventJournal
from bt_api_gateway.quota_admission import (
    GatewayQuotaAdmission,
    GatewayQuotaOperation,
    GatewayQuotaSubject,
)
from bt_api_gateway.router import GatewayCommandKind, GatewayPrincipal
from bt_api_gateway.service_component import GatewayServiceAccountScope
from bt_api_gateway.session_guard import LocalAccountSessionGuard

from bt_api_py.gateway_operator import GatewayAccountSessionBinding
from bt_api_py.service_components import ComponentResources, ResourceOwnership
from tests.integration import test_dim01_identity_isolation_service as dim01
from tests.integration import test_dim04_strategy_account_isolation as dim04
from tests.integration.test_dim04_strategy_net_close_owner_path import (
    _CLOSE_INTENT,
    _dim04_net_close_host_process,
    _net_close_runtime_client,
    _runtime_result,
    _seed_synthetic_net_position,
)
from tests.unit._spawned_process_helpers import (
    _assert_no_fallback_cleanup,
    _bounded_reap_spawned_process,
    _close_normally,
    _receive,
    _start_process,
)

_QUOTA_DOMAIN = "synthetic:dim07-risk-dispatch-v1"
_RISK_QUOTA_MAX_WAIT_NS = 5_000_000_000


def _owner(path: str, *, capacity: int, max_queue_wait_seconds: float) -> GatewayQuotaAdmission:
    return GatewayQuotaAdmission(
        path,
        _QUOTA_DOMAIN,
        capacity_per_window=capacity,
        window_seconds=60.0,
        reserved_capacity=0,
        max_queue=8,
        max_queue_per_subject=4,
        max_backoff_seconds=1.0,
        max_queue_wait_seconds=max_queue_wait_seconds,
        poll_interval_seconds=0.005,
    )


def _quota_risk_host_process(
    control,
    raw_config: dict[str, Any],
    registry_path: str,
    key_material: dict[str, bytes],
    risk_path: str,
    journal_path: str,
    provider_path: str,
    quota_path: str,
    binding_dir: str,
    capacity: int,
    max_queue_wait_seconds: float,
    expected_provider_close_calls: int,
) -> None:
    """Inject a Gateway binding's exact callback through trusted Host resources."""

    quota = _owner(quota_path, capacity=capacity, max_queue_wait_seconds=max_queue_wait_seconds)
    account_key = AccountKey("synthetic", "loopback", "paper", "dim07-quota-account")
    binding_journal = GatewayEventJournal(Path(binding_dir) / "binding-journal.sqlite3")
    trusted_account_ref = dim01._ROUTES[dim01._ROUTE_SPOT_A_STRATEGY_B]["account_ref"]
    trusted_strategy_ref = dim01._STRATEGY_B
    account_scope = GatewayServiceAccountScope(
        trusted_account_ref,
        "provider-domain:dim07-quota",
        "journal:dim07-quota",
        strategy_scope_map={trusted_strategy_ref: "state:dim07-quota-strategy"},
        account_read_strategy_scope="state:dim07-quota-account",
    )
    binding_dir_path = Path(binding_dir)
    binding_dir_path.mkdir(parents=True, exist_ok=True)
    guard = LocalAccountSessionGuard(
        binding_dir_path,
        account_key.account_key_id,
        "dim07-risk-quota-resource-provider",
    )
    principal_ref = "principal:dim07-quota"
    principal = GatewayPrincipal(
        principal_ref,
        frozenset({"journal:dim07-quota"}),
        frozenset({"state:dim07-quota-account", "state:dim07-quota-strategy"}),
        frozenset({GatewayCommandKind.READ}),
    )
    gateway_binding = GatewayAccountSessionBinding(
        account_session_ref="session:dim07-quota",
        journal=binding_journal,
        account_key=account_key,
        account_scope=account_scope,
        state_scope="state:dim07-quota-account",
        guard=guard,
        required_coverage=(AccountResourceKey("SWAP", "cross"),),
        principals={principal_ref: principal},
        quota_admission=quota,
    )
    callback = gateway_binding.dispatch_quota_admission
    if callback is None:
        raise AssertionError("trusted Gateway binding omitted its quota callback")

    with sqlite3.connect(quota_path + ".callback-audit.sqlite3") as connection:
        connection.execute(
            "CREATE TABLE IF NOT EXISTS callback_calls ("
            "sequence INTEGER PRIMARY KEY AUTOINCREMENT, account_ref TEXT NOT NULL, "
            "strategy_ref TEXT, run_ref TEXT, instrument_ref TEXT, operation TEXT NOT NULL, "
            "deadline_monotonic_ns INTEGER NOT NULL)"
        )

    def _audited_callback(*arguments):
        if len(arguments) != 6:
            raise AssertionError("Risk callback did not use the fixed six-argument contract")
        with sqlite3.connect(quota_path + ".callback-audit.sqlite3") as connection:
            connection.execute(
                "INSERT INTO callback_calls(account_ref,strategy_ref,run_ref,instrument_ref,"
                "operation,deadline_monotonic_ns) VALUES (?,?,?,?,?,?)",
                arguments,
            )
        return callback(*arguments)

    original_service_host = dim01.ServiceHost

    def _service_host_with_quota(config, authority, selected_registry, resource_provider, *args, **kwargs):
        def _resource_provider(component, instance_id, options, scope):
            resources = resource_provider(component, instance_id, options, scope)
            if component != "risk" or options.get("role") != "account_owner":
                return resources
            if type(resources) is not ComponentResources:
                raise AssertionError("Risk resource provider returned an invalid resource bundle")
            values = dict(resources.values)
            values["dispatch_quota_admission"] = _audited_callback
            if resources.ownership is ResourceOwnership.BORROWED:
                return ComponentResources.borrowed(
                    values,
                    ready=resources.ready,
                    internal_ports=resources.internal_ports,
                    risk_policy_bindings=resources.risk_policy_bindings,
                    shared_collateral_domains=resources.shared_collateral_domains,
                )
            return ComponentResources.owned(
                values,
                ready=resources.ready,
                close=resources.close,
                internal_ports=resources.internal_ports,
                risk_policy_bindings=resources.risk_policy_bindings,
                shared_collateral_domains=resources.shared_collateral_domains,
            )

        return original_service_host(
            config, authority, selected_registry, _resource_provider, *args, **kwargs
        )

    dim01.ServiceHost = _service_host_with_quota
    try:
        _dim04_net_close_host_process(
            control,
            raw_config,
            registry_path,
            key_material,
            risk_path,
            journal_path,
            provider_path,
            expected_provider_close_calls=expected_provider_close_calls,
        )
    finally:
        dim01.ServiceHost = original_service_host


def _quota_runtime_client(control, *arguments) -> None:
    """Give the client RPC longer than Risk's bounded quota wait."""

    original_timeout_ms = dim01._REQUEST_TIMEOUT_MS
    dim01._REQUEST_TIMEOUT_MS = 10_000
    try:
        _net_close_runtime_client(control, *arguments)
    finally:
        dim01._REQUEST_TIMEOUT_MS = original_timeout_ms


def _run_strategy_close_with_quota(
    tmp_path: Path,
    *,
    deny_quota: bool,
) -> tuple[dict[str, Any], tuple[dict[str, Any], ...], tuple[dict[str, Any], ...]]:
    context = multiprocessing.get_context("spawn")
    endpoint = dim01._endpoint()
    server_public, server_secret = zmq.curve_keypair()
    strategy_a_public, _strategy_a_secret = zmq.curve_keypair()
    strategy_public, strategy_secret = zmq.curve_keypair()
    key_material = {
        "server_public": server_public,
        "server_secret": server_secret,
        "strategy_a_public": strategy_a_public,
        "strategy_b_public": strategy_public,
    }
    paths = {
        "registry": str(tmp_path / "dim07-quota-registry.sqlite3"),
        "risk": str(tmp_path / "dim07-quota-risk.sqlite3"),
        "journal": str(tmp_path / "dim07-quota-journal.sqlite3"),
        "provider": str(tmp_path / "dim07-quota-provider.sqlite3"),
        "quota": str(tmp_path / "dim07-quota.sqlite3"),
        "binding": str(tmp_path / "dim07-quota-binding"),
    }
    _seed_synthetic_net_position(paths["provider"])
    dim04._seed_shared_account_close(paths["risk"], paths["journal"])
    capacity = 1 if deny_quota else 4
    # Risk uses a fixed five-second monotonic deadline. The shared quota
    # owner's configured maximum must exceed that bound so it can report a
    # genuine capacity wait instead of rejecting the deadline as malformed.
    max_wait = 6.0
    if deny_quota:
        _owner(paths["quota"], capacity=capacity, max_queue_wait_seconds=max_wait).admit(
            GatewayQuotaSubject("account:seed", strategy_ref="strategy:seed"),
            GatewayQuotaOperation.OPEN,
            time.monotonic_ns() + _RISK_QUOTA_MAX_WAIT_NS,
        )

    host = None
    client = None
    forced: list[str] = []
    failures: list[str] = []
    try:
        host = _start_process(
            context,
            _quota_risk_host_process,
            (
                dim01._service_config(endpoint),
                paths["registry"],
                key_material,
                paths["risk"],
                paths["journal"],
                paths["provider"],
                paths["quota"],
                paths["binding"],
                capacity,
                max_wait,
                0 if deny_quota else 1,
            ),
            "dim07-quota-risk-owner-host",
        )
        assert _receive(host["pipe"]) == (
            "synthetic_close_contract",
            dim01._EXCHANGE_SPOT_A_ALIAS,
            dim01._PROFILE_SPOT_ROTATED,
            ("OPEN", "CLOSE"),
        )
        assert _receive(host["pipe"]) == ("synthetic_provider_fixture", "bt_api.plugins", "empty")
        host_ready = _receive(host["pipe"])
        if host_ready[0] == "error":
            pytest.fail(f"quota Risk ServiceHost startup returned {host_ready!r}")
        assert host_ready == ("ready", endpoint, "started"), host_ready

        client = _start_process(
            context,
            _quota_runtime_client,
            (
                endpoint,
                strategy_public,
                strategy_secret,
                server_public,
                dim01._STRATEGY_B,
                dim01._PRINCIPAL_B,
                dim01._ROUTE_SPOT_A_STRATEGY_B,
            ),
            "dim07-quota-strategy-runtime",
        )
        ready = _receive(client["pipe"])
        assert ready[0] == "ready" and ready[1], ready
        run_id = ready[1]

        result = _runtime_result(
            client,
            "submit",
            _CLOSE_INTENT,
            "SELL",
            "CLOSE",
            True,
        )
        if deny_quota:
            assert result["ok"] is False and result["code"] == "ADMISSION_DENIED", result
            record = result["result"]["record"]
            assert record["state"] == "REJECTED" and record["dispatch_attempts"] == 0, record
            duplicate = _runtime_result(
                client,
                "submit",
                _CLOSE_INTENT,
                "SELL",
                "CLOSE",
                True,
            )
            assert duplicate == result, duplicate
            assert dim01._client_command(host, "provider_write_count") == ("provider_write_count", 0)
        else:
            assert result["ok"] is True and result["code"] == "OK", result
            record = result["result"]["record"]
            assert record["state"] == "FILLED" and record["dispatch_attempts"] == 1, record
            assert _receive(host["pipe"])[0] == "synthetic_close_written"
            duplicate = _runtime_result(
                client,
                "submit",
                _CLOSE_INTENT,
                "SELL",
                "CLOSE",
                True,
            )
            assert duplicate == result, duplicate
            assert dim01._client_command(host, "provider_write_count") == ("provider_write_count", 1)

        query = _runtime_result(client, "query", _CLOSE_INTENT)
        assert query["result"]["record"] == record, query
        assert _close_normally(client, expected_tag="closed") == ("closed", "closed")
        client = None
        assert _close_normally(host, expected_tag="closed") == ("closed", "closed")
        host = None

        with sqlite3.connect(paths["quota"] + ".callback-audit.sqlite3") as connection:
            connection.row_factory = sqlite3.Row
            callback_rows = tuple(dict(row) for row in connection.execute("SELECT * FROM callback_calls"))
        assert len(callback_rows) == 1, callback_rows
        call = callback_rows[0]
        assert call["account_ref"] == dim01._SCOPES[dim01._SCOPE_SPOT_A_STRATEGY_B].account_ref
        assert call["strategy_ref"] == dim01._STRATEGY_B
        assert call["run_ref"] == run_id
        assert call["instrument_ref"] == dim01._BTC
        assert call["operation"] == "CLOSE"
        assert call["deadline_monotonic_ns"] > 0

        with sqlite3.connect(paths["journal"]) as connection:
            event_rows = connection.execute(
                "SELECT event_type,payload_json FROM risk_order_events WHERE scope_key=? "
                "AND principal_ref=? AND intent_id=? ORDER BY revision",
                (dim01._ACCOUNT_SPOT_A.key, dim01._PRINCIPAL_B, _CLOSE_INTENT),
            ).fetchall()
        events = tuple({"event_type": row[0], "payload_json": row[1]} for row in event_rows)
        if deny_quota:
            denials = [row for row in events if row["event_type"] == "ADMISSION_DENIED"]
            assert len(denials) == 1, events
            denial_payload = json.loads(denials[0]["payload_json"])
            assert denial_payload["reason_code"] == "GATEWAY_QUOTA_DENIED", denial_payload
            assert "CLAIMED" not in [row["event_type"] for row in events], events
            assert "DISPATCHING" not in [row["event_type"] for row in events], events

        with sqlite3.connect(paths["quota"]) as connection:
            connection.row_factory = sqlite3.Row
            grants = tuple(dict(row) for row in connection.execute("SELECT * FROM grants ORDER BY sequence"))
        if deny_quota:
            assert [row["operation"] for row in grants] == ["open"], grants
        else:
            assert [row["operation"] for row in grants] == ["close"], grants
        return {"result": result, "run_id": run_id}, callback_rows, events
    finally:
        if client is not None:
            _bounded_reap_spawned_process(client, forced, failures)
        if host is not None:
            _bounded_reap_spawned_process(host, forced, failures)
        _assert_no_fallback_cleanup(forced, failures)


@pytest.mark.integration
def test_strategy_close_uses_gateway_quota_binding_and_authenticated_run(tmp_path: Path) -> None:
    evidence, callback_rows, _events = _run_strategy_close_with_quota(
        tmp_path, deny_quota=False
    )
    assert evidence["result"]["result"]["record"]["state"] == "FILLED"
    assert callback_rows[0]["strategy_ref"] == dim01._STRATEGY_B
    assert callback_rows[0]["run_ref"] == evidence["run_id"]


@pytest.mark.integration
def test_quota_denial_is_durable_before_claim_and_provider_dispatch(tmp_path: Path) -> None:
    evidence, callback_rows, events = _run_strategy_close_with_quota(
        tmp_path, deny_quota=True
    )
    assert evidence["result"]["code"] == "ADMISSION_DENIED"
    assert len(callback_rows) == 1
    assert "CLAIMED" not in [row["event_type"] for row in events]
    assert "DISPATCHING" not in [row["event_type"] for row in events]
