"""DIM07 reserved quota remains available to the authenticated cancel owner."""

from __future__ import annotations

import hashlib
import json
import sqlite3
import time
from pathlib import Path

import pytest
import zmq
from bt_api_base.account_protocol import AccountKey, AccountResourceKey
from bt_api_base.service_protocol import WireIdentity, WireRole, WireScope
from bt_api_gateway.event_journal import GatewayEventJournal
from bt_api_gateway.quota_admission import (
    GatewayQuotaAdmission,
    GatewayQuotaAdmissionError,
)
from bt_api_gateway.router import GatewayCommandKind, GatewayPrincipal
from bt_api_gateway.service_component import GatewayServiceAccountScope
from bt_api_gateway.session_guard import LocalAccountSessionGuard

from bt_api_py.gateway_operator import GatewayAccountSessionBinding
from bt_api_py.service_components import ComponentResources
from bt_api_py.service_host import ServiceHost, TrustedServiceAuthority
from bt_api_py.service_registry import PrincipalGrant, ResourceGrant
from bt_api_py.service_runtime import (
    ClientCurveKeys,
    ServiceClientRuntime,
    ServiceRuntimeError,
    ZmqTransportFactory,
)
from tests.integration import test_service_execution_risk_cancel_owner_control as cancel_case


@pytest.mark.integration
def test_owner_cancel_uses_reserved_gateway_quota_after_open_capacity_is_full(
    tmp_path: Path,
) -> None:
    quota = account_journal = account_guard = None
    execution_store = risk_journal = risk_gate = None
    host = host_thread = strategy_runtime = owner_runtime = None
    primary_error: BaseException | None = None
    host_cleanup_facts: str | None = None

    def close_service_host() -> None:
        nonlocal host_cleanup_facts
        if host is None:
            return
        failures = []
        first_close_error = None
        close_error_fact = "none"
        finished_fact = "not_started"
        thread_alive_fact = "not_started"
        thread_error_fact = "unavailable"
        host_state_fact = "unavailable"
        thread_started = (
            host_thread is not None and host_thread.thread.ident is not None
        )
        try:
            if thread_started:
                host_thread.close()
            else:
                host.close()
        except BaseException as error:
            first_close_error = error
            close_error_fact = type(error).__name__
            failures.append(f"close={type(error).__name__}")

        if host_thread is not None:
            try:
                finished = host_thread.finished.is_set()
                finished_fact = str(finished).lower()
                if thread_started and not finished:
                    failures.append("finished=False")
            except BaseException as error:
                finished_fact = f"unavailable:{type(error).__name__}"
                failures.append(f"finished_check={type(error).__name__}")
            try:
                thread_alive = host_thread.thread.is_alive()
                thread_alive_fact = str(thread_alive).lower()
                if thread_alive:
                    failures.append("thread_alive=True")
            except BaseException as error:
                thread_alive_fact = f"unavailable:{type(error).__name__}"
                failures.append(f"thread_alive_check={type(error).__name__}")
            try:
                thread_error = host_thread.error
                thread_error_fact = (
                    "none" if thread_error is None else type(thread_error).__name__
                )
            except BaseException as error:
                thread_error_fact = f"unavailable:{type(error).__name__}"
                failures.append(f"thread_error_check={type(error).__name__}")

        try:
            host_state = host.state
            if type(host_state) is str and host_state in {
                "new",
                "started",
                "failed",
                "close_failed",
                "closed",
            }:
                host_state_fact = host_state
            else:
                host_state_fact = f"unexpected:{type(host_state).__name__}"
            if host_state != "closed":
                failures.append("host_state_not_closed")
        except BaseException as error:
            host_state_fact = f"unavailable:{type(error).__name__}"
            failures.append(f"host_state_check={type(error).__name__}")

        host_cleanup_facts = (
            f"close_error={close_error_fact}; "
            f"thread_error={thread_error_fact}; "
            f"finished={finished_fact}; "
            f"thread_alive={thread_alive_fact}; "
            f"host_state={host_state_fact}"
        )
        if failures:
            failure = AssertionError(
                "ServiceHost cleanup verification failed ("
                + "; ".join(failures)
                + f"; facts: {host_cleanup_facts})"
            )
            if first_close_error is not None:
                raise failure from first_close_error
            raise failure

    try:
        quota_path = tmp_path / "dim07-cancel-quota.sqlite3"
        quota = GatewayQuotaAdmission(
            quota_path,
            "synthetic:dim07-cancel-provider-key-ip",
            capacity_per_window=2,
            window_seconds=60.0,
            reserved_capacity=1,
            max_queue=8,
            max_queue_per_subject=4,
            max_backoff_seconds=1.0,
            max_queue_wait_seconds=6.0,
            poll_interval_seconds=0.005,
        )
        account_key = AccountKey("synthetic", "loopback", "paper", cancel_case._ACCOUNT)
        account_journal = GatewayEventJournal(tmp_path / "gateway-account-journal.sqlite3")
        account_scope = GatewayServiceAccountScope(
            cancel_case._ACCOUNT,
            "provider-domain:dim07-cancel",
            "journal:dim07-cancel",
            strategy_scope_map={cancel_case._STRATEGY: "state:dim07-cancel-strategy"},
            account_read_strategy_scope="state:dim07-cancel-account",
        )
        binding_directory = tmp_path / "gateway-account-binding"
        binding_directory.mkdir()
        account_guard = LocalAccountSessionGuard(
            binding_directory,
            account_key.account_key_id,
            "dim07-cancel-resource-provider",
        )
        principal = GatewayPrincipal(
            "principal:dim07-cancel",
            frozenset({"journal:dim07-cancel"}),
            frozenset({"state:dim07-cancel-account", "state:dim07-cancel-strategy"}),
            frozenset({GatewayCommandKind.READ}),
        )
        gateway_binding = GatewayAccountSessionBinding(
            account_session_ref="session:dim07-cancel",
            journal=account_journal,
            account_key=account_key,
            account_scope=account_scope,
            state_scope="state:dim07-cancel-account",
            guard=account_guard,
            required_coverage=(AccountResourceKey("SWAP", "cross"),),
            principals={principal.principal_id: principal},
            quota_admission=quota,
        )
        cancel_quota_admission = gateway_binding.dispatch_quota_admission
        assert cancel_quota_admission is not None

        endpoint = cancel_case._endpoint()
        config = cancel_case._service_config(endpoint)
        server_public, server_secret = zmq.curve_keypair()
        strategy_public, strategy_secret = zmq.curve_keypair()
        owner_public, owner_secret = zmq.curve_keypair()
        service_identity = WireIdentity(
            WireRole.SERVICE, cancel_case._SERVICE_PRINCIPAL, cancel_case._SERVICE_ID
        )
        strategy_identity = WireIdentity(
            WireRole.STRATEGY_CLIENT,
            cancel_case._STRATEGY_PRINCIPAL,
            cancel_case._STRATEGY_ACTOR,
        )
        owner_identity = WireIdentity(
            WireRole.SERVICE, cancel_case._OWNER_PRINCIPAL, cancel_case._OWNER_ACTOR
        )
        wire_scope = WireScope(
            cancel_case._DOMAIN,
            account_ref=cancel_case._ACCOUNT,
            strategy_ref=cancel_case._STRATEGY,
            run_id=cancel_case._RUN_ID,
        )
        target_scope = WireScope(
            cancel_case._DOMAIN,
            account_ref=cancel_case._ACCOUNT,
            strategy_ref=cancel_case._STRATEGY,
        )
        execution_scope = cancel_case.ExecutionScope(
            "PAPER",
            "simulation",
            cancel_case._ACCOUNT,
            cancel_case._STRATEGY,
            "dim07-quota-cancel-session",
        )
        strategy_grant = ResourceGrant(
            cancel_case._INSTANCE_ID,
            "service.order_entry",
            cancel_case._SCOPE_REF,
            target_scope,
            frozenset({"orders.submit"}),
        )
        owner_grant = ResourceGrant(
            cancel_case._INSTANCE_ID,
            "service.owner_control",
            cancel_case._SCOPE_REF,
            wire_scope,
            frozenset({"owner.cancel"}),
        )
        authority = TrustedServiceAuthority(
            identity=service_identity,
            scope=WireScope(cancel_case._DOMAIN),
            scope_ref=config.scope_ref,
            scope_version="afi08-cancel-service-scope-v1",
            security_ref=cancel_case._SERVER_SECURITY_REF,
            security_version="curve-v1",
            registration_policy_ref=config.registration_policy_ref,
            registration_policy_version="afi08-cancel-registration-v1",
            service_requirements_ref=config.service_requirements_ref,
            service_requirements_version="afi08-cancel-requirements-v1",
            curve_public_key=server_public,
            curve_secret_key=server_secret,
            principals={
                strategy_public: cancel_case._STRATEGY_PRINCIPAL,
                owner_public: cancel_case._OWNER_PRINCIPAL,
            },
            grants={
                cancel_case._STRATEGY_PRINCIPAL: (
                    PrincipalGrant(strategy_identity, (strategy_grant,)),
                ),
                cancel_case._OWNER_PRINCIPAL: (PrincipalGrant(owner_identity, (owner_grant,)),),
            },
        )
        execution_store = cancel_case.SqliteExecutionStore(tmp_path / "execution.sqlite3")
        risk_journal = cancel_case.RiskOrderJournal(
            tmp_path / "risk-order-journal.sqlite3", writer_id="dim07-cancel-journal"
        )
        risk_scope = cancel_case.AccountScope(
            "PAPER", cancel_case._ACCOUNT, "simulation"
        )
        risk_gate = cancel_case.DurableRiskGate(
            tmp_path / "risk.sqlite3",
            cancel_case.RiskPolicy(
                "dim07-cancel-risk-v1",
                cancel_case.Decimal("0"),
                max_increase_count=0,
                permit_ttl_seconds=30,
                notional_unit="USD",
            ),
            execution_journal_authority=risk_journal,
        )
        cancel_gate = cancel_case.RiskExecutionCancelAdmissionGate(
            risk_gate,
            risk_journal,
            execution_store,
            risk_scope=risk_scope,
            execution_scope=execution_scope,
            wire_scope_template=target_scope,
        )
        provider = cancel_case._Provider()

        def resource_provider(component, instance_id, _options, _scope):
            assert component == "execution" and instance_id == cancel_case._INSTANCE_ID
            return ComponentResources.borrowed(
                {
                    "store": execution_store,
                    "targets": {target_scope: execution_scope},
                    "dispatch": provider,
                    "admission": {
                        "cancel_gate": cancel_gate,
                        "cancel_quota_admission": cancel_quota_admission,
                        "allow_unprotected": True,
                    },
                },
                ready=lambda: True,
            )

        host = ServiceHost(
            config,
            authority,
            tmp_path / "registry.sqlite3",
            resource_provider,
        )
        host_thread = cancel_case._HostThread(host)
        host_thread.start()
        strategy_runtime = ServiceClientRuntime(
            cancel_case._client_config(endpoint, strategy=True),
            identity=strategy_identity,
            scope_resolver=lambda _ref: target_scope,
            peer_resolver=lambda _service_id: service_identity,
            transport_factory=ZmqTransportFactory(
                {
                    cancel_case._STRATEGY_SECURITY_REF: ClientCurveKeys(
                        server_public, strategy_public, strategy_secret
                    )
                }
            ),
            run_id=cancel_case._RUN_ID,
            requested_operations={"broker.orders": frozenset({"orders.submit"})},
        )
        strategy_summary = strategy_runtime.start()
        assert strategy_summary.degraded_count == 0

        wrong_run_runtime = ServiceClientRuntime(
            cancel_case._client_config(endpoint, strategy=False),
            identity=owner_identity,
            scope_resolver=lambda _ref: WireScope(
                cancel_case._DOMAIN,
                account_ref=cancel_case._ACCOUNT,
                strategy_ref=cancel_case._STRATEGY,
                run_id="run:wrong-dim07-cancel",
            ),
            peer_resolver=lambda _service_id: service_identity,
            transport_factory=ZmqTransportFactory(
                {
                    cancel_case._OWNER_SECURITY_REF: ClientCurveKeys(
                        server_public, owner_public, owner_secret
                    )
                }
            ),
            requested_operations={"upstream.owner_control": frozenset({"owner.cancel"})},
        )
        wrong_run_primary_error: BaseException | None = None
        try:
            with pytest.raises(ServiceRuntimeError, match="SCOPE_DENIED"):
                wrong_run_runtime.start()
        except BaseException as error:
            wrong_run_primary_error = error
            raise
        finally:
            try:
                wrong_run_runtime.close()
            except BaseException as cleanup_error:
                cleanup_summary = (
                    "resource cleanup failed "
                    f"(wrong_run_runtime: {type(cleanup_error).__name__})"
                )
                if wrong_run_primary_error is not None:
                    wrong_run_primary_error.add_note(cleanup_summary)
                else:
                    raise AssertionError(cleanup_summary) from cleanup_error

        owner_runtime = ServiceClientRuntime(
            cancel_case._client_config(endpoint, strategy=False),
            identity=owner_identity,
            scope_resolver=lambda _ref: wire_scope,
            peer_resolver=lambda _service_id: service_identity,
            transport_factory=ZmqTransportFactory(
                {
                    cancel_case._OWNER_SECURITY_REF: ClientCurveKeys(
                        server_public, owner_public, owner_secret
                    )
                }
            ),
            requested_operations={"upstream.owner_control": frozenset({"owner.cancel"})},
        )
        owner_summary = owner_runtime.start()
        assert owner_summary.degraded_count == 0
        assert owner_summary.bindings[0].operations == frozenset({"owner.cancel"})

        submit_reply = strategy_runtime.call(
            "broker.orders",
            "orders.submit",
            {
                "intent_id": "dim07-cancel-target-order",
                "signal_id": "dim07-cancel-target-signal",
                "instrument": "BTC-USDT",
                "side": "BUY",
                "position_effect": "OPEN",
                "order_type": "LIMIT",
                "quantity": "1",
                "price": "50000",
                "time_in_force": "GTC",
                "reduce_only": False,
                "metadata_version": "dim07-cancel-target-v1",
                "deadline_ns": None,
                "tags": {},
            },
            account_ref=cancel_case._ACCOUNT,
            scope_ref=cancel_case._SCOPE_REF,
        )
        assert submit_reply["ok"] is True, submit_reply
        assert submit_reply["result"]["record"]["state"] == "ACKED"

        # With the target order already accepted, fill ordinary capacity via
        # the same trusted callback used by owner cancellation. A new OPEN is
        # denied while CANCEL can still use the reserved slot.
        cancel_quota_admission(
            cancel_case._ACCOUNT,
            cancel_case._STRATEGY,
            cancel_case._RUN_ID,
            "BTC-USDT",
            "OPEN",
            time.monotonic_ns() + 500_000_000,
        )
        with pytest.raises(GatewayQuotaAdmissionError) as ordinary_denied:
            cancel_quota_admission(
                cancel_case._ACCOUNT,
                cancel_case._STRATEGY,
                cancel_case._RUN_ID,
                "BTC-USDT",
                "OPEN",
                time.monotonic_ns() + 60_000_000,
            )
        assert ordinary_denied.value.code == "DEADLINE_EXCEEDED"
        assert quota.status()["grants_in_window"] == 1
        assert quota.status()["pending_waiters"] == 0

        first = owner_runtime.call(
            "upstream.owner_control",
            "owner.cancel",
            {"command_id": "dim07-cancel-command-1"},
            account_ref=cancel_case._ACCOUNT,
            scope_ref=cancel_case._SCOPE_REF,
        )
        assert first["code"] == "OWNER_CANCEL_UNKNOWN", first
        assert first["result"]["items"][0]["dispatch_attempts"] == 1
        cancel_id = first["result"]["items"][0]["cancel_id"]
        assert provider.cancelled == [cancel_id]

        for command_id in (
            "dim07-cancel-command-1",
            "dim07-cancel-command-2",
        ):
            replay = owner_runtime.call(
                "upstream.owner_control",
                "owner.cancel",
                {"command_id": command_id},
                account_ref=cancel_case._ACCOUNT,
                scope_ref=cancel_case._SCOPE_REF,
            )
            assert replay["code"] == "OWNER_CANCEL_UNKNOWN", replay
            assert replay["result"]["items"][0]["cancel_id"] == cancel_id
            assert replay["result"]["items"][0]["dispatch_attempts"] == 1

        with sqlite3.connect(quota_path) as connection:
            grants = connection.execute(
                "SELECT operation, subject_digest, run_digest FROM grants ORDER BY sequence"
            ).fetchall()
        assert [row[0] for row in grants] == ["open", "cancel"], grants
        expected_subject = hashlib.sha256(
            json.dumps(
                [cancel_case._ACCOUNT, cancel_case._STRATEGY, "BTC-USDT"],
                ensure_ascii=True,
                separators=(",", ":"),
            ).encode("ascii")
        ).hexdigest()
        expected_run = hashlib.sha256(cancel_case._RUN_ID.encode("ascii")).hexdigest()
        assert [row[1] for row in grants] == [expected_subject, expected_subject], grants
        assert [row[2] for row in grants] == [expected_run, expected_run], grants
        assert provider.cancelled == [cancel_id]
    except BaseException as error:
        primary_error = error
        raise
    finally:
        cleanup_actions = []
        if owner_runtime is not None:
            cleanup_actions.append(("owner_runtime", owner_runtime.close))
        if strategy_runtime is not None:
            cleanup_actions.append(("strategy_runtime", strategy_runtime.close))
        if host is not None:
            cleanup_actions.append(("service_host", close_service_host))
        if risk_gate is not None:
            cleanup_actions.append(("risk_gate", risk_gate.close))
        if execution_store is not None:
            cleanup_actions.append(("execution_store", execution_store.close))

        cleanup_failures = []
        for resource_name, close_resource in cleanup_actions:
            try:
                close_resource()
            except BaseException as cleanup_error:
                cleanup_failures.append((resource_name, cleanup_error))

        if cleanup_failures:
            failure_details = "; ".join(
                f"{resource_name}: {type(error).__name__}"
                + (
                    f" [{host_cleanup_facts}]"
                    if resource_name == "service_host" and host_cleanup_facts is not None
                    else ""
                )
                for resource_name, error in cleanup_failures
            )
            cleanup_summary = f"resource cleanup failed ({failure_details})"
            if primary_error is not None:
                if host_cleanup_facts is not None:
                    primary_error.add_note(
                        f"ServiceHost final facts: {host_cleanup_facts}"
                    )
                primary_error.add_note(cleanup_summary)
            else:
                raise AssertionError(cleanup_summary) from cleanup_failures[0][1]
        elif primary_error is not None and host_cleanup_facts is not None:
            primary_error.add_note(f"ServiceHost final facts: {host_cleanup_facts}")
