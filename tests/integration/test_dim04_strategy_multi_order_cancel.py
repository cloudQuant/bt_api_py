"""DIM04 exact-run owner cancellation leaves another strategy untouched."""

from __future__ import annotations

import threading
import time
from contextlib import contextmanager
from decimal import Decimal

import pytest
import zmq
from bt_api_base.service_protocol import WireIdentity, WireRole, WireScope
from bt_api_execution import (
    CancelObservation,
    ExecutionScope,
    ProviderObservation,
    SqliteExecutionStore,
)
from bt_api_risk import (
    CancelDispatchResolutionReceiptStatus,
    DispatchResolutionReceiptStatus,
    DurableRiskAdmissionAdapter,
    DurableRiskGate,
    InstrumentRiskAdmissionMapper,
    InstrumentRiskMetadata,
    InstrumentRiskRegistry,
    RiskPolicy,
)

from bt_api_py.bt_api import BtApi
from bt_api_py.service_components import ComponentResources
from bt_api_py.service_config import parse_client_config
from bt_api_py.service_execution_risk_cancel import RiskExecutionCancelAdmissionGate
from bt_api_py.service_host import ServiceHost, TrustedServiceAuthority
from bt_api_py.service_registry import PrincipalGrant, ResourceGrant
from bt_api_py.service_risk_order_journal import RiskOrderJournal
from bt_api_py.service_risk_order_entry import RiskOrderEntryService, RiskOrderTarget
from bt_api_py.service_runtime import (
    ClientCurveKeys,
    ServiceClientRuntime,
    ZmqTransportFactory,
)
from tests.integration import test_service_execution_risk_cancel_owner_control as cancel_case
from tests.unit import test_service_risk_order_exposure_integration as risk_case


_OTHER_STRATEGY = "strategy-dim04-other"
_OTHER_PRINCIPAL = "strategy-principal-dim04-other"
_OTHER_ACTOR = _OTHER_STRATEGY
_OTHER_RUN = "run-dim04-other"
_OWNER_RUN = cancel_case._RUN_ID


@contextmanager
def _synthetic_plugin_discovery():
    """Keep unrelated installed providers out of this synthetic owner fixture."""

    import importlib

    from bt_api_base.plugins.loader import PluginLoader

    bt_api_module = importlib.import_module("bt_api_py.bt_api")
    original_discover = PluginLoader.__dict__["_discover_entry_points"]
    original_plugins_loaded = bt_api_module._plugins_loaded
    discovered_groups: list[str] = []

    def discover(self, group: str):
        discovered_groups.append(group)
        if group == "bt_api.plugins":
            return []
        return original_discover(self, group)

    setattr(PluginLoader, "_discover_entry_points", discover)
    bt_api_module._plugins_loaded = False
    try:
        yield discovered_groups
    finally:
        setattr(PluginLoader, "_discover_entry_points", original_discover)
        bt_api_module._plugins_loaded = original_plugins_loaded


class _RiskBackedDispatch:
    """Synthetic provider leaf behind the real Risk order owner."""

    def __init__(self, risk_service, routes: dict[str, tuple[WireIdentity, WireScope]]) -> None:
        self._risk_service = risk_service
        self._routes = dict(routes)
        self.submitted: dict[str, str] = {}
        self.submit_replies: dict[str, object] = {}
        self.cancelled: list[str] = []

    def submit(self, intent) -> ProviderObservation:
        route = self._routes.get(intent.scope.strategy_id)
        assert route is not None, intent.scope
        identity, scope = route
        reply = self._risk_service.handle(
            "orders.submit",
            principal_ref=identity.principal_ref,
            identity=identity,
            scope=scope,
            arguments={
                "intent_id": intent.intent_id,
                "signal_id": intent.signal_id,
                "instrument": intent.instrument,
                "side": intent.side.value,
                "position_effect": intent.position_effect.value,
                "order_type": intent.order_type.value,
                "quantity": format(intent.quantity, "f"),
                "price": None if intent.price is None else format(intent.price, "f"),
                "time_in_force": intent.time_in_force.value,
                "reduce_only": intent.reduce_only,
                "metadata_version": intent.metadata_version,
                "deadline_ns": intent.deadline_ns,
                "tags": dict(intent.tags),
            },
        )
        self.submit_replies[intent.intent_id] = reply
        assert reply["ok"] is True, reply
        record = reply["result"]["record"]
        assert record["intent_id"] == intent.intent_id
        assert record["state"] == "ACKED_TRACKED", reply
        provider_order_id = record["provider_order_id"]
        self.submitted[intent.intent_id] = provider_order_id
        return ProviderObservation.accepted(intent.intent_id, provider_order_id)

    def cancel(self, intent) -> CancelObservation:
        self.cancelled.append(intent.cancel_id)
        return CancelObservation.cancelled(
            intent.cancel_id, intent.target_intent_id, intent.provider_order_id
        )


class _HostThread:
    """Keep Host start, serve and close on the same owner thread."""

    def __init__(self, host: ServiceHost) -> None:
        self.host = host
        self.stop_requested = threading.Event()
        self.ready = threading.Event()
        self.finished = threading.Event()
        self.error: BaseException | None = None
        self.cleanup_errors: list[BaseException] = []
        self.startup_wait_timed_out = False
        self._diagnostic_lock = threading.Lock()
        self._phase = "created"
        self._phase_started_ns = time.perf_counter_ns()
        self.phase_times: list[dict[str, object]] = []
        self.closed_observed: bool | None = None
        self.thread = threading.Thread(target=self._run, name="dim04-owner-cancel-host")

    def _set_phase(self, phase: str) -> None:
        now = time.perf_counter_ns()
        with self._diagnostic_lock:
            self.phase_times.append(
                {
                    "phase": self._phase,
                    "duration_ns": max(0, now - self._phase_started_ns),
                }
            )
            self._phase = phase
            self._phase_started_ns = now

    def _capture_error(self, error: BaseException, *, cleanup: bool = False) -> None:
        with self._diagnostic_lock:
            if cleanup:
                self.cleanup_errors.append(error)
            if self.error is None:
                self.error = error

    @staticmethod
    def _error_fact(error: BaseException | None) -> dict[str, str] | None:
        if error is None:
            return None
        code = getattr(error, "code", None)
        return {
            "type": type(error).__qualname__,
            "code": code if type(code) is str else "",
            "message": str(error),
        }

    def diagnostics(self) -> dict[str, object]:
        now = time.perf_counter_ns()
        with self._diagnostic_lock:
            phase = self._phase
            phase_started_ns = self._phase_started_ns
            phases = tuple(dict(item) for item in self.phase_times)
            error = self.error
            cleanup_errors = tuple(self.cleanup_errors)
            timed_out = self.startup_wait_timed_out
        try:
            summary = dict(self.host.summary)
        except BaseException as error:
            summary = {"summary_read_error": self._error_fact(error)}
        state = self.host.state
        closed = state == "closed"
        self.closed_observed = closed
        return {
            "phase": phase,
            "phase_elapsed_ns": max(0, now - phase_started_ns),
            "phase_times": phases,
            "host_state": state,
            "closed": closed,
            "start_failure": summary.get("start_failure"),
            "summary": summary,
            "error": self._error_fact(error),
            "cleanup_errors": tuple(
                self._error_fact(item) for item in cleanup_errors
            ),
            "startup_wait_timed_out": timed_out,
            "ready": self.ready.is_set(),
            "finished": self.finished.is_set(),
        }

    def _diagnostic_message(self, context: str) -> str:
        return f"{context}: {self.diagnostics()!r}"

    def _run(self) -> None:
        try:
            self._set_phase("host_start")
            self.host.start()
            self.ready.set()
            self._set_phase("serving")
            while not self.stop_requested.is_set():
                self.host.serve_once(timeout_ms=15)
        except BaseException as error:
            self._capture_error(error)
        finally:
            try:
                if self.host.state != "closed":
                    self._set_phase("host_close")
                    self.host.close()
            except BaseException as error:
                self._capture_error(error, cleanup=True)
            finally:
                self._set_phase("finished")
                self.ready.set()
                self.finished.set()

    def start(self) -> None:
        self.thread.start()
        if not self.ready.wait(10):
            with self._diagnostic_lock:
                self.startup_wait_timed_out = True
            raise AssertionError(
                self._diagnostic_message("owner ServiceHost startup wait timed out")
            )
        if self.error is not None:
            raise AssertionError(
                self._diagnostic_message("owner ServiceHost startup/serve failed")
            ) from self.error
        if self.host.state != "started":
            raise AssertionError(
                self._diagnostic_message("owner ServiceHost did not reach started")
            )

    def close(self) -> None:
        self.stop_requested.set()
        if not self.finished.wait(10):
            raise AssertionError(
                self._diagnostic_message("owner ServiceHost close wait timed out")
            )
        self.thread.join(1)
        if self.thread.is_alive():
            raise AssertionError(
                self._diagnostic_message("owner ServiceHost thread remained alive")
            )
        diagnostics = self.diagnostics()
        if (
            self.error is not None
            or self.cleanup_errors
            or self.startup_wait_timed_out
            or not diagnostics["closed"]
        ):
            raise AssertionError(
                f"owner ServiceHost failed lifecycle/cleanup: {diagnostics!r}"
            ) from self.error


def _other_strategy_client_config(endpoint: str):
    return parse_client_config(
        {
            "process_role": "strategy_client",
            "client_config_schema_version": 1,
            "registration": {"strategy_ref": _OTHER_STRATEGY},
            "clients": {
                "execution": {
                    "config_schema_version": 1,
                    "settings": {
                        "bindings": [
                            {
                                "mount": "broker.orders",
                                "service_id": cancel_case._SERVICE_ID,
                                "instance_id": cancel_case._INSTANCE_ID,
                                "scope_ref": cancel_case._SCOPE_REF,
                                "transport_ref": cancel_case._CONNECT_REF,
                                "account_ref": cancel_case._ACCOUNT,
                            }
                        ],
                        "options": {
                            "request_timeout_ms": 3000,
                            "heartbeat_interval_seconds": 5,
                        },
                    },
                }
            },
            "communication": {
                "config_schema_version": 1,
                "profiles": {
                    cancel_case._CONNECT_REF: {
                        "backend": "zmq",
                        "config_schema_version": 1,
                        "endpoints": {"rpc_connect": endpoint},
                        "security_profile_ref": cancel_case._STRATEGY_SECURITY_REF,
                        "options": {
                            "request_timeout_ms": 3000,
                            "high_water_mark": 32,
                            "max_message_bytes": 1_000_000,
                        },
                    }
                },
            },
        }
    )


def _submit(runtime: ServiceClientRuntime, intent_id: str, *, account_ref: str, scope_ref: str):
    reply = runtime.call(
        "broker.orders",
        "orders.submit",
        {
            "intent_id": intent_id,
            "signal_id": intent_id + "-signal",
            "instrument": risk_case._V2_INSTRUMENT,
            "side": "BUY",
            "position_effect": "OPEN",
            "order_type": "LIMIT",
            "quantity": "1",
            "price": "20",
            "time_in_force": "GTC",
            "reduce_only": False,
            "metadata_version": risk_case._V2_METADATA_VERSION,
            "deadline_ns": None,
            "tags": {},
        },
        account_ref=account_ref,
        scope_ref=scope_ref,
    )
    assert reply["ok"] is True, reply
    record = reply["result"]["record"]
    assert record["state"] == "ACKED", record
    return record


@pytest.mark.integration
def test_strategy_owner_cancel_cancels_all_own_run_orders_only(tmp_path, monkeypatch) -> None:
    # The V2 synthetic provider normalizer binds to its provider account ID.
    # Use that same identity through the trusted service fixture.
    monkeypatch.setattr(cancel_case, "_ACCOUNT", risk_case.base._PROVIDER_ACCOUNT)
    endpoint = cancel_case._endpoint()
    config = cancel_case._service_config(endpoint)
    server_public, server_secret = zmq.curve_keypair()
    owner_public, owner_secret = zmq.curve_keypair()
    first_public, first_secret = zmq.curve_keypair()
    other_public, other_secret = zmq.curve_keypair()

    service_identity = WireIdentity(
        WireRole.SERVICE, cancel_case._SERVICE_PRINCIPAL, cancel_case._SERVICE_ID
    )
    first_identity = WireIdentity(
        WireRole.STRATEGY_CLIENT,
        cancel_case._STRATEGY_PRINCIPAL,
        cancel_case._STRATEGY_ACTOR,
    )
    other_identity = WireIdentity(
        WireRole.STRATEGY_CLIENT, _OTHER_PRINCIPAL, _OTHER_ACTOR
    )
    owner_identity = WireIdentity(
        WireRole.SERVICE, cancel_case._OWNER_PRINCIPAL, cancel_case._OWNER_ACTOR
    )

    first_template = WireScope(
        cancel_case._DOMAIN,
        account_ref=cancel_case._ACCOUNT,
        strategy_ref=cancel_case._STRATEGY,
    )
    other_template = WireScope(
        cancel_case._DOMAIN,
        account_ref=cancel_case._ACCOUNT,
        strategy_ref=_OTHER_STRATEGY,
    )
    first_dispatch_scope = WireScope(
        cancel_case._DOMAIN,
        account_ref=cancel_case._ACCOUNT,
        strategy_ref=cancel_case._STRATEGY,
        run_id=_OWNER_RUN,
    )
    other_dispatch_scope = WireScope(
        cancel_case._DOMAIN,
        account_ref=cancel_case._ACCOUNT,
        strategy_ref=_OTHER_STRATEGY,
        run_id=_OTHER_RUN,
    )
    owner_scope = WireScope(
        cancel_case._DOMAIN,
        account_ref=cancel_case._ACCOUNT,
        strategy_ref=cancel_case._STRATEGY,
        run_id=_OWNER_RUN,
    )
    first_execution_scope = ExecutionScope(
        risk_case._ACCOUNT_SCOPE.provider, "simulation", cancel_case._ACCOUNT, cancel_case._STRATEGY,
        "dim04-owner-cancel-first-strategy",
    )
    other_execution_scope = ExecutionScope(
        risk_case._ACCOUNT_SCOPE.provider, "simulation", cancel_case._ACCOUNT, _OTHER_STRATEGY,
        "dim04-owner-cancel-other-strategy",
    )

    first_entry_grant = ResourceGrant(
        cancel_case._INSTANCE_ID,
        "service.order_entry",
        cancel_case._SCOPE_REF,
        first_template,
        frozenset({"orders.submit"}),
    )
    other_entry_grant = ResourceGrant(
        cancel_case._INSTANCE_ID,
        "service.order_entry",
        cancel_case._SCOPE_REF,
        other_template,
        frozenset({"orders.submit"}),
    )
    owner_cancel_grant = ResourceGrant(
        cancel_case._INSTANCE_ID,
        "service.owner_control",
        cancel_case._SCOPE_REF,
        owner_scope,
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
            first_public: cancel_case._STRATEGY_PRINCIPAL,
            other_public: _OTHER_PRINCIPAL,
            owner_public: cancel_case._OWNER_PRINCIPAL,
        },
        grants={
            cancel_case._STRATEGY_PRINCIPAL: (
                PrincipalGrant(first_identity, (first_entry_grant,)),
            ),
            _OTHER_PRINCIPAL: (PrincipalGrant(other_identity, (other_entry_grant,)),),
            cancel_case._OWNER_PRINCIPAL: (
                PrincipalGrant(owner_identity, (owner_cancel_grant,)),
            ),
        },
    )

    execution_store = SqliteExecutionStore(tmp_path / "execution.sqlite3")
    risk_journal = RiskOrderJournal(
        tmp_path / "risk-order-journal.sqlite3", writer_id="dim04-owner-cancel-journal"
    )
    risk_scope = risk_case._ACCOUNT_SCOPE
    risk_gate = DurableRiskGate(
        tmp_path / "risk.sqlite3",
        RiskPolicy(
            "dim04-owner-cancel-risk-v1",
            Decimal("1000"),
            max_increase_count=5,
            permit_ttl_seconds=30,
            notional_unit="USD",
        ),
        execution_journal_authority=risk_journal,
    )
    cancel_gate = RiskExecutionCancelAdmissionGate(
        risk_gate,
        risk_journal,
        execution_store,
        risk_scope=risk_scope,
        execution_scope=first_execution_scope,
        wire_scope_template=first_template,
    )
    quota_calls: list[tuple[object, ...]] = []

    def quota_admission(*args) -> None:
        quota_calls.append(args)
        return None

    resource_state: dict[str, object] = {}

    def resource_provider(component, instance_id, _options, _scope):
        assert component == "execution" and instance_id == cancel_case._INSTANCE_ID
        now_ns = time.time_ns()
        metadata = InstrumentRiskMetadata(
            instrument=risk_case._V2_INSTRUMENT,
            metadata_version=risk_case._V2_METADATA_VERSION,
            as_of_ns=now_ns - 1_000_000_000,
            expires_at_ns=now_ns + 3_600_000_000_000,
            tick_size=Decimal("0.01"),
            quantity_step=Decimal("0.001"),
            contract_multiplier=Decimal("1"),
            max_gross_notional=Decimal("1000"),
            taker_fee_bps=Decimal("0"),
            fixed_fee=Decimal("0"),
            max_slippage_bps=Decimal("0"),
            quantity_unit="BTC",
            valuation_unit="USD",
        )
        registry = InstrumentRiskRegistry((metadata,))
        first_target = RiskOrderTarget(
            account_scope=risk_scope,
            strategy_id=cancel_case._STRATEGY,
            exchange_name=risk_case._EXCHANGE,
            provider_account_id=risk_case.base._PROVIDER_ACCOUNT,
            registry=registry,
        )
        other_target = RiskOrderTarget(
            account_scope=risk_scope,
            strategy_id=_OTHER_STRATEGY,
            exchange_name=risk_case._EXCHANGE,
            provider_account_id=risk_case.base._PROVIDER_ACCOUNT,
            registry=registry,
        )
        with _synthetic_plugin_discovery() as discovered_groups:
            bt_api = BtApi(debug=False)
        assert discovered_groups == ["bt_api.plugins"]
        provider_feed = risk_case._V2SyntheticBtApiFeed(
            str(tmp_path / "provider.sqlite3"), ("open", "open", "open")
        )
        bt_api.exchange_feeds[risk_case._EXCHANGE] = provider_feed
        risk_target_scopes = {first_template: first_target, other_template: other_target}
        risk_admissions = {
            target_scope: DurableRiskAdmissionAdapter(
                risk_gate, InstrumentRiskAdmissionMapper(risk_scope, registry)
            )
            for target_scope in risk_target_scopes
        }
        risk_service = RiskOrderEntryService(
            bt_api,
            risk_target_scopes,
            risk_admissions,
            risk_journal,
            risk_gate,
            {
                target_scope: risk_case._contract(("GTC",))
                for target_scope in risk_target_scopes
            },
        )
        first_risk_identity = WireIdentity(
            WireRole.STRATEGY_CLIENT,
            cancel_case._STRATEGY_PRINCIPAL,
            cancel_case._STRATEGY_ACTOR,
        )
        other_risk_identity = WireIdentity(
            WireRole.STRATEGY_CLIENT, _OTHER_PRINCIPAL, _OTHER_ACTOR
        )
        provider = _RiskBackedDispatch(
            risk_service,
            {
                cancel_case._STRATEGY: (first_risk_identity, first_dispatch_scope),
                _OTHER_STRATEGY: (other_risk_identity, other_dispatch_scope),
            },
        )
        resource_state["bt_api"] = bt_api
        resource_state["provider_feed"] = provider_feed
        resource_state["risk_service"] = risk_service
        resource_state["dispatch"] = provider
        return ComponentResources.borrowed(
            {
                "store": execution_store,
                "targets": {
                    first_template: first_execution_scope,
                    other_template: other_execution_scope,
                },
                "dispatch": provider,
                "admission": {
                    "cancel_gate": cancel_gate,
                    "cancel_quota_admission": quota_admission,
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
    host_thread = _HostThread(host)
    first_runtime = other_runtime = owner_runtime = None
    first_started = other_started = owner_started = False
    try:
        host_thread.start()
        first_runtime = ServiceClientRuntime(
            cancel_case._client_config(endpoint, strategy=True),
            identity=first_identity,
            scope_resolver=lambda _ref: first_template,
            peer_resolver=lambda _service_id: service_identity,
            transport_factory=ZmqTransportFactory(
                {
                    cancel_case._STRATEGY_SECURITY_REF: ClientCurveKeys(
                        server_public, first_public, first_secret
                    )
                }
            ),
            run_id=_OWNER_RUN,
            requested_operations={"broker.orders": frozenset({"orders.submit"})},
        )
        first_summary = first_runtime.start()
        first_started = True
        assert first_summary.degraded_count == 0, first_summary
        assert len(first_summary.bindings) == 1
        assert first_summary.bindings[0].scope.run_id == _OWNER_RUN

        other_runtime = ServiceClientRuntime(
            _other_strategy_client_config(endpoint),
            identity=other_identity,
            scope_resolver=lambda _ref: other_template,
            peer_resolver=lambda _service_id: service_identity,
            transport_factory=ZmqTransportFactory(
                {
                    cancel_case._STRATEGY_SECURITY_REF: ClientCurveKeys(
                        server_public, other_public, other_secret
                    )
                }
            ),
            run_id=_OTHER_RUN,
            requested_operations={"broker.orders": frozenset({"orders.submit"})},
        )
        other_summary = other_runtime.start()
        other_started = True
        assert other_summary.degraded_count == 0, other_summary
        assert len(other_summary.bindings) == 1
        assert other_summary.bindings[0].scope.run_id == _OTHER_RUN

        owner_runtime = ServiceClientRuntime(
            cancel_case._client_config(endpoint, strategy=False),
            identity=owner_identity,
            scope_resolver=lambda _ref: owner_scope,
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
        owner_started = True
        assert owner_summary.degraded_count == 0, owner_summary
        assert len(owner_summary.bindings) == 1
        assert owner_summary.bindings[0].scope == owner_scope
        assert owner_summary.bindings[0].operations == frozenset({"owner.cancel"})

        own_ids = ("dim04-owner-a-order-1", "dim04-owner-a-order-2")
        for intent_id in own_ids:
            _submit(
                first_runtime,
                intent_id,
                account_ref=cancel_case._ACCOUNT,
                scope_ref=cancel_case._SCOPE_REF,
            )
        other_id = "dim04-owner-b-order-1"
        _submit(
            other_runtime,
            other_id,
            account_ref=cancel_case._ACCOUNT,
            scope_ref=cancel_case._SCOPE_REF,
        )
        provider = resource_state.get("dispatch")
        assert isinstance(provider, _RiskBackedDispatch)
        assert set(provider.submitted) == {*own_ids, other_id}
        assert all(
            reply["ok"] is True
            and reply["result"]["record"]["state"] == "ACKED_TRACKED"
            for reply in provider.submit_replies.values()
        )
        assert len(resource_state["provider_feed"].snapshot()[0]) == 3
        risk_source_by_order = (
            (own_ids[0], cancel_case._STRATEGY_PRINCIPAL, cancel_case._STRATEGY, _OWNER_RUN),
            (own_ids[1], cancel_case._STRATEGY_PRINCIPAL, cancel_case._STRATEGY, _OWNER_RUN),
            (other_id, _OTHER_PRINCIPAL, _OTHER_STRATEGY, _OTHER_RUN),
        )
        for intent_id, principal_ref, strategy_ref, run_id in risk_source_by_order:
            command = risk_journal.get_command(risk_scope, principal_ref, intent_id)
            assert command is not None and command.permit_id is not None
            assert command.scope == risk_scope
            assert command.principal_ref == principal_ref
            assert command.wire_scope["account_ref"] == cancel_case._ACCOUNT
            assert command.wire_scope["strategy_ref"] == strategy_ref
            assert command.wire_scope["run_id"] == run_id
            assert provider.submitted[intent_id] == "provider-" + command.client_order_id
            assert command.state == "ACKED_TRACKED"
            source_receipt = risk_gate.dispatch_resolution_receipt(command.permit_id)
            assert (
                source_receipt.status
                is DispatchResolutionReceiptStatus.ACKED_TRACKED_RESOLVED
            )
            assert source_receipt.dispatch_latch_active is False
            assert source_receipt.claim.intent_id == command.risk_intent_id
            assert source_receipt.proof.provider_order_id == provider.submitted[intent_id]

        reply = owner_runtime.call(
            "upstream.owner_control",
            "owner.cancel",
            {"command_id": "dim04-owner-cancel-run-a"},
            account_ref=cancel_case._ACCOUNT,
            scope_ref=cancel_case._SCOPE_REF,
        )
        assert reply["ok"] is True, reply
        assert reply["code"] == "OWNER_CANCEL_APPLIED", reply
        result = reply["result"]
        assert (result["strategy_ref"], result["run_id"]) == (
            cancel_case._STRATEGY,
            _OWNER_RUN,
        )
        assert result["outcome"] == "APPLIED"
        assert result["total_count"] == result["selected_count"] == 2
        assert result["omitted_count"] == 0
        assert result["state_counts"] == {"CANCELLED": 2}
        assert {item["intent_id"] for item in result["items"]} == set(own_ids)
        assert all(
            item["decision"] == "READY"
            and item["cancel_state"] == "CANCELLED"
            and item["dispatch_attempts"] == 1
            and item["review_required"] is False
            for item in result["items"]
        ), result["items"]
        assert len(provider.cancelled) == 2
        assert len(set(provider.cancelled)) == 2
        assert len(quota_calls) == 2
        assert all(
            call[:5]
            == (
                cancel_case._ACCOUNT,
                cancel_case._STRATEGY,
                _OWNER_RUN,
                risk_case._V2_INSTRUMENT,
                "CANCEL",
            )
            and type(call[5]) is int
            and call[5] > 0
            for call in quota_calls
        )

        for item in result["items"]:
            cancel_record = execution_store.get_cancel(
                item["cancel_id"], scope=first_execution_scope
            )
            assert cancel_record is not None
            assert cancel_record.state.value == "CANCELLED"
            assert cancel_record.dispatch_attempts == 1
            assert cancel_record.permit_reference is not None
            cancel_intent = execution_store.get_cancel_intent(
                item["cancel_id"], scope=first_execution_scope
            )
            assert cancel_intent is not None
            assert cancel_intent.target_intent_id == item["intent_id"]
            assert cancel_intent.provider_order_id == provider.submitted[item["intent_id"]]
            receipt = risk_gate.cancel_dispatch_resolution_receipt(
                cancel_record.permit_reference
            )
            assert (
                receipt.status is CancelDispatchResolutionReceiptStatus.TERMINAL_RESOLVED
            )
            assert receipt.dispatch_latch_active is False
            assert receipt.claim.permit_id == cancel_record.permit_reference
            assert receipt.claim.intent_id == (
                "cancel-admission:"
                + first_execution_scope.key
                + ":"
                + cancel_record.cancel_id
            )
            assert receipt.proof is not None
            assert receipt.proof_sha256 == receipt.proof.fingerprint
            assert receipt.proof.claim_digest == receipt.claim.claim_digest
            assert receipt.proof.cancel_intent_fingerprint == cancel_intent.fingerprint
            assert receipt.proof.execution_scope_key == first_execution_scope.key
            assert receipt.proof.cancel_id == cancel_record.cancel_id
            assert receipt.proof.target_intent_id == cancel_record.target_intent_id
            assert receipt.proof.provider_order_id == cancel_record.provider_order_id
            assert receipt.proof.terminal_state.value == "CANCELLED"
            assert receipt.proof.target_postcondition.value == "TARGET_TERMINAL"
            assert receipt.proof.target_state == "CANCELLED"
            assert receipt.proof.target_event_type == "cancelled_by_cancel_intent"
            assert (
                receipt.proof.dispatch_attempt_count == cancel_record.dispatch_attempts == 1
            )

        other_record = execution_store.get(other_id, scope=other_execution_scope)
        assert other_record is not None and other_record.state.value == "ACKED"
        assert other_record.provider_order_id == provider.submitted[other_id]
        assert execution_store.list_cancellations_for_target(
            other_id, scope=other_execution_scope
        ) == ()
        assert all(
            cancel_record.target_intent_id != other_id
            for cancel_id in provider.cancelled
            if (cancel_record := execution_store.get_cancel(
                cancel_id, scope=first_execution_scope
            )) is not None
        )

        replay = owner_runtime.call(
            "upstream.owner_control",
            "owner.cancel",
            {"command_id": "dim04-owner-cancel-run-a"},
            account_ref=cancel_case._ACCOUNT,
            scope_ref=cancel_case._SCOPE_REF,
        )
        assert replay["ok"] is True and replay["code"] == "OWNER_CANCEL_APPLIED", replay
        assert replay["result"]["batch_sha256"] == result["batch_sha256"]
        assert provider.cancelled == [item["cancel_id"] for item in result["items"]]
        assert len(quota_calls) == 2
        assert execution_store.get(other_id, scope=other_execution_scope).state.value == "ACKED"
    finally:
        close_error: BaseException | None = None
        for runtime, started in (
            (owner_runtime, owner_started),
            (other_runtime, other_started),
            (first_runtime, first_started),
        ):
            if runtime is not None and started:
                try:
                    runtime.close()
                except BaseException as error:
                    if close_error is None:
                        close_error = error
        try:
            host_thread.close()
        except BaseException as error:
            if close_error is None:
                close_error = error
        try:
            host_cleanup = host_thread.diagnostics()
            if (
                host_cleanup["finished"] is not True
                or host_cleanup["closed"] is not True
                or host_cleanup["host_state"] != "closed"
            ) and close_error is None:
                close_error = AssertionError(
                    f"DIM04 owner-cancel Host cleanup not verified: {host_cleanup!r}"
                )
            elif close_error is None:
                print(f"DIM04 owner-cancel Host cleanup verified: {host_cleanup!r}")
        except BaseException as error:
            if close_error is None:
                close_error = error
        bt_api = resource_state.get("bt_api")
        if isinstance(bt_api, BtApi):
            try:
                bt_api.close()
            except BaseException as error:
                if close_error is None:
                    close_error = error
        for closeable in (risk_gate, execution_store):
            try:
                closeable.close()
            except BaseException as error:
                if close_error is None:
                    close_error = error
        if close_error is not None:
            raise AssertionError("DIM04 owner-cancel resource cleanup failed") from close_error
