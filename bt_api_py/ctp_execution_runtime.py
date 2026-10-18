"""Thread-confined composition for one managed CTP execution owner.

This module is deliberately opt-in.  Constructing the provider creates no
Store, lease, account-family claim, TraderClient, or native object.  Those
resources are acquired only when ServiceHost invokes the provider on its
owner thread.
"""

from __future__ import annotations

import math
import threading
import time
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from pathlib import Path
from time import monotonic_ns
from types import MappingProxyType
from typing import Any


class CtpExecutionRuntimeError(RuntimeError):
    """Finite, non-sensitive status for a failed CTP runtime operation."""

    def __init__(self, code: str) -> None:
        self.code = code
        super().__init__(code)


@dataclass(frozen=True, slots=True)
class CtpTraderConnectionConfig:
    """The explicit TD/MD front pair and credentials for one TraderClient."""

    front: str
    md_front: str
    broker_id: str
    user_id: str
    password: str = field(repr=False)
    ctp_env_profile: str = "config_front_pair"
    app_id: str = field(default="simnow_client_test", repr=False)
    auth_code: str = field(default="0000000000000000", repr=False)

    def __post_init__(self) -> None:
        for name in ("front", "md_front", "broker_id", "user_id", "password"):
            value = getattr(self, name)
            if type(value) is not str or not value or value != value.strip():
                raise ValueError(f"invalid CTP connection field: {name}")
        if (
            type(self.ctp_env_profile) is not str
            or not self.ctp_env_profile
            or self.ctp_env_profile != self.ctp_env_profile.strip()
        ):
            raise ValueError("invalid CTP environment profile")
        for name in ("app_id", "auth_code"):
            value = getattr(self, name)
            if type(value) is not str or not value:
                raise ValueError(f"invalid CTP connection field: {name}")


@dataclass(frozen=True, slots=True)
class CtpRuntimeStatus:
    phase: str
    code: str | None
    ready: bool
    closed: bool


@dataclass(frozen=True, slots=True)
class CtpRuntimeAdmissionContext:
    """Exact owner-thread objects passed to trusted Risk composition code."""

    store: Any
    execution_scope: Any
    service_scope: Any
    writer_lease: Any
    trader: Any
    family_owner: Any
    callback_owner: Any
    session_adapter: Any
    session_generation_id: str
    reconciliation_port: Any
    submit_port: Any
    execution_owner: Any
    native_binding: Any


@dataclass(frozen=True, slots=True)
class CtpExecutionRuntimeConfig:
    """Trusted inputs for one non-restartable CTP account-owner generation.

    ``prepare_trader`` is an application-owned composition hook.  It must use
    the TraderClient's real settlement confirmation/readback and established
    owner-sealed SimNow write-binding APIs.  This runtime never creates an
    approval, credential binding, Risk gate, or cutover proof.

    The basic gate factory is called after the real submit port exists and must
    register an ``ExecutionProviderValidatorV1`` bound to that exact port.  The
    Risk factory is called after the exact execution owner exists and returns
    the service admission resources described by this module's contract.
    """

    store_path: str | Path
    execution_scope: Any
    service_scope: Any
    lease_owner_id: str
    trader: CtpTraderConnectionConfig
    native_binding: Any
    prepare_trader: Callable[[Any], None]
    basic_admission_factory: Callable[[Any, Any, Any], Any]
    risk_admission_factory: Callable[[CtpRuntimeAdmissionContext], Mapping[str, Any]]
    startup_timeout_s: float = 15.0
    stop_timeout_s: float = 2.0
    lease_ttl_ns: int = 30_000_000_000
    callback_pump_events: int = 32
    drain_limit: int = 4096

    def __post_init__(self) -> None:
        if (
            type(self.store_path) is not str
            and not isinstance(self.store_path, Path)
        ) or not str(self.store_path).strip():
            raise ValueError("invalid CTP execution Store path")
        if type(self.lease_owner_id) is not str or not self.lease_owner_id.strip():
            raise ValueError("invalid CTP execution writer owner")
        if type(self.trader) is not CtpTraderConnectionConfig:
            raise ValueError("typed CTP TraderClient configuration is required")
        if self.trader.ctp_env_profile != "config_front_pair":
            raise ValueError("managed CTP runtime requires config_front_pair")
        for field_name in (
            "prepare_trader",
            "basic_admission_factory",
            "risk_admission_factory",
        ):
            if not callable(getattr(self, field_name)):
                raise ValueError(f"trusted CTP runtime hook is required: {field_name}")
        for field_name in ("startup_timeout_s", "stop_timeout_s"):
            value = getattr(self, field_name)
            if (
                type(value) not in (int, float)
                or isinstance(value, bool)
                or not math.isfinite(value)
                or value <= 0
                or value > 60
            ):
                raise ValueError(f"invalid CTP runtime timeout: {field_name}")
        if type(self.lease_ttl_ns) is not int or not 1_000_000 <= self.lease_ttl_ns <= 300_000_000_000:
            raise ValueError("invalid CTP execution lease duration")
        if type(self.callback_pump_events) is not int or not 1 <= self.callback_pump_events <= 256:
            raise ValueError("invalid CTP callback pump bound")
        if type(self.drain_limit) is not int or not 1 <= self.drain_limit <= 100_000:
            raise ValueError("invalid CTP callback drain bound")


class CtpExecutionRuntime:
    """One exact TraderClient, SQLite owner, and submit/cancel port generation."""

    def __init__(self, config: CtpExecutionRuntimeConfig, instance_id: str) -> None:
        self.config = config
        self.instance_id = instance_id
        self.owner_thread_id: int | None = None
        self._phase = "CREATED"
        self._code: str | None = None
        self._ready = False
        self._closed = False
        self._lease_healthy = False
        self._lease_expires_at_ns: int | None = None
        self._next_lease_renewal_ns: int | None = None
        self._lease_renewal_interval_ns = max(1, config.lease_ttl_ns // 3)
        self._store_closed = False
        self._native_start_attempted = False
        self._native_stop_attempted = False
        self._native_stop_complete = False
        self._last_stop_receipt: Any | None = None
        self._store: Any | None = None
        self._family_owner: Any | None = None
        self._writer_lease: Any | None = None
        self._callback_owner: Any | None = None
        self._session_adapter: Any | None = None
        self._trader: Any | None = None
        self._callback_verifier: Any | None = None
        self._reconciliation_port: Any | None = None
        self._submit_port: Any | None = None
        self._basic_gate: Any | None = None
        self._execution_owner: Any | None = None
        self._cancel_gate: Any | None = None
        self._cancel_port: Any | None = None
        self._order_gate: Any | None = None
        self._cancel_quota_admission: Any | None = None
        self._resources: Any | None = None

    @property
    def status(self) -> CtpRuntimeStatus:
        return CtpRuntimeStatus(
            phase=self._phase,
            code=self._code,
            ready=self.is_ready(),
            closed=self._closed,
        )

    @property
    def last_stop_receipt(self) -> Any | None:
        return self._last_stop_receipt

    def _assert_owner_thread(self) -> None:
        if self.owner_thread_id is None or threading.get_ident() != self.owner_thread_id:
            raise CtpExecutionRuntimeError("OWNER_THREAD_REQUIRED")

    def is_ready(self) -> bool:
        if (
            not self._ready
            or not self._lease_healthy
            or self._closed
            or self._trader is None
            or self._lease_expires_at_ns is None
            or time.time_ns() >= self._lease_expires_at_ns
        ):
            return False
        return self._trader_write_ready()

    def _trader_write_ready(self) -> bool:
        """Recheck the full managed-write gate, not only login readiness."""

        if self._trader is None:
            return False
        try:
            gate_state = self._trader.get_execution_gate_state()
            return (
                self._trader.is_trading_ready is True
                and isinstance(gate_state, Mapping)
                and gate_state.get("armed") is False
                and gate_state.get("managed") is True
                and gate_state.get("runtime_simnow_credential_binding_configured")
                is True
                and gate_state.get("runtime_simnow_write_verifier_configured")
                is True
            )
        except Exception:
            return False

    def _lease_failure(self) -> CtpExecutionRuntimeError:
        self._lease_healthy = False
        self._ready = False
        self._set_phase("LEASE_FAILED", "WRITER_LEASE_RENEWAL_FAILED")
        return CtpExecutionRuntimeError("WRITER_LEASE_RENEWAL_FAILED")

    def _renew_writer_lease_if_due(self, *, force: bool = False) -> bool:
        """Renew the existing fence without replacing its shared handle."""

        self._assert_owner_thread()
        lease = self._writer_lease
        store = self._store
        if (
            not self._lease_healthy
            or lease is None
            or store is None
            or self._family_owner is None
            or self._lease_expires_at_ns is None
            or self._next_lease_renewal_ns is None
        ):
            raise self._lease_failure()
        current_mono_ns = monotonic_ns()
        if time.time_ns() >= self._lease_expires_at_ns:
            raise self._lease_failure()
        if not force and current_mono_ns < self._next_lease_renewal_ns:
            return False

        try:
            from bt_api_execution import WriterLease

            renewed = store.acquire_or_renew_lease(
                self.config.execution_scope,
                self.config.lease_owner_id,
                ttl_ns=self.config.lease_ttl_ns,
                ctp_account_family_owner=self._family_owner,
            )
            if (
                type(renewed) is not WriterLease
                or renewed.scope_key != lease.scope_key
                or renewed.owner_id != lease.owner_id
                or renewed.fencing_token != lease.fencing_token
                or renewed.family_key != lease.family_key
                or renewed.family_owner_intent_id != lease.family_owner_intent_id
                or type(renewed.expires_at_ns) is not int
                or renewed.expires_at_ns <= time.time_ns()
            ):
                raise RuntimeError("writer lease identity changed")
            store.assert_writer_lease(self.config.execution_scope, lease)
        except Exception:
            raise self._lease_failure() from None

        # All ports and the callback owner retain `lease`; never substitute the
        # newly returned immutable snapshot. The store validates its owner and
        # fencing token against the renewed durable expiry.
        self._lease_expires_at_ns = renewed.expires_at_ns
        self._next_lease_renewal_ns = (
            monotonic_ns() + self._lease_renewal_interval_ns
        )
        return True

    def _startup_should_stop(self) -> bool:
        self._assert_owner_thread()
        if self._phase == "CLOSING":
            return True
        self._renew_writer_lease_if_due()
        return not self._lease_healthy

    def maintain(self, active_ids: frozenset[str]) -> None:
        """Renew the exact writer lease on the ServiceHost owner thread."""

        self._assert_owner_thread()
        if type(active_ids) is not frozenset or any(
            type(item) is not str or not item for item in active_ids
        ):
            raise CtpExecutionRuntimeError("MAINTENANCE_INPUT_INVALID")
        if self._closed:
            return
        if not self._lease_healthy:
            raise CtpExecutionRuntimeError("WRITER_LEASE_RENEWAL_FAILED")
        self._renew_writer_lease_if_due()

    def next_maintenance_delay(self) -> float | None:
        """Seconds until the owner-thread lease renewal callback is due."""

        self._assert_owner_thread()
        if (
            self._closed
            or not self._lease_healthy
            or self._next_lease_renewal_ns is None
        ):
            return None
        if (
            self._lease_expires_at_ns is not None
            and time.time_ns() >= self._lease_expires_at_ns
        ):
            return 0.0
        return max(0, self._next_lease_renewal_ns - monotonic_ns()) / 1_000_000_000

    def _validate_scope(self) -> None:
        from bt_api_base.service_protocol import WireScope
        from bt_api_execution import ExecutionScope

        from bt_api_py.ctp_admission_authority import CtpNativeDispatchBindingV1

        scope = self.config.execution_scope
        wire = self.config.service_scope
        binding = self.config.native_binding
        if (
            type(scope) is not ExecutionScope
            or scope.provider != "ctp"
            or scope.environment != "simulation"
            or not scope.trading_day
            or type(wire) is not WireScope
            or wire.run_id is not None
            or wire.account_refs
            or wire.manual_book_ref is not None
            or wire.group_id is not None
            or wire.leg_id is not None
            or wire.account_ref != scope.account_ref
            or wire.strategy_ref != scope.strategy_id
            or type(binding) is not CtpNativeDispatchBindingV1
            or binding.account_ref != scope.account_ref
            or binding.scope_key != scope.key
        ):
            raise CtpExecutionRuntimeError("TRUSTED_SCOPE_INVALID")

    def _set_phase(self, phase: str, code: str | None = None) -> None:
        self._phase = phase
        self._code = code

    def bootstrap(self) -> Any:
        """Create and return the owned component resources on the owner thread."""

        if self.owner_thread_id is not None:
            raise CtpExecutionRuntimeError("RUNTIME_ALREADY_STARTED")
        self.owner_thread_id = threading.get_ident()
        try:
            self._validate_scope()
            self._bootstrap_owned()
            self._renew_writer_lease_if_due(force=True)
            self._resources = self._make_resources(complete=True)
            self._ready = True
            self._set_phase("READY")
            return self._resources
        except Exception as exc:
            # Keep only a finite code; never place a native exception, credential,
            # callback field, or operator-provided string in the public error.
            if self._code is None:
                self._code = (
                    exc.code
                    if type(exc) is CtpExecutionRuntimeError
                    else "BOOTSTRAP_FAILED"
                )
            self._ready = False
            self._set_phase("BOOTSTRAP_FAILED", self._code)
            if self._has_owned_state():
                resources = self._make_resources(complete=False)
                self._resources = resources
                from bt_api_py.service_components import ServiceResourceConstructionError

                raise ServiceResourceConstructionError(resources) from None
            if isinstance(exc, CtpExecutionRuntimeError):
                raise exc
            raise CtpExecutionRuntimeError(self._code) from None

    def _bootstrap_owned(self) -> None:
        from bt_api_ctp.ctp.callback_ingress import (
            CtpTraderCallbackIngressAckV2,
            CtpTraderCallbackIngressRecordV2,
        )
        from bt_api_ctp.ctp.client import TraderClient
        from bt_api_execution import SqliteExecutionStore
        from bt_api_execution.ctp_callback_session import CtpCallbackSessionStoreAdapter

        from bt_api_py.ctp_callback_evidence import CtpSessionIngressEvidenceVerifierV1
        from bt_api_py.ctp_managed_cancel_port import CtpManagedCancelPort
        from bt_api_py.ctp_managed_submit_port import CtpManagedSubmitPort
        from bt_api_py.ctp_service_execution import CtpOwnerCallbackReconciliationPort

        self._set_phase("STORE_OPEN")
        self._store = SqliteExecutionStore(self.config.store_path)
        self._set_phase("ACCOUNT_FAMILY_OWNER")
        self._family_owner = self._store.acquire_ctp_account_family_owner(
            self.config.execution_scope
        )
        self._set_phase("WRITER_LEASE")
        self._writer_lease = self._store.acquire_or_renew_lease(
            self.config.execution_scope,
            self.config.lease_owner_id,
            ttl_ns=self.config.lease_ttl_ns,
            ctp_account_family_owner=self._family_owner,
        )
        self._lease_expires_at_ns = self._writer_lease.expires_at_ns
        self._next_lease_renewal_ns = (
            monotonic_ns() + self._lease_renewal_interval_ns
        )
        self._lease_healthy = True
        self._set_phase("CALLBACK_OWNER")
        self._callback_owner = self._store.create_ctp_callback_session_owner(
            self.config.execution_scope,
            writer_lease=self._writer_lease,
            authorized_scopes=(self.config.execution_scope,),
        )
        self._session_adapter = CtpCallbackSessionStoreAdapter(
            self._store,
            self.config.execution_scope,
            self._callback_owner,
            self._writer_lease,
            CtpTraderCallbackIngressAckV2,
            CtpTraderCallbackIngressRecordV2,
        )

        self._set_phase("TRADER_CREATE")
        trader_config = self.config.trader
        self._trader = TraderClient(
            trader_config.front,
            trader_config.broker_id,
            trader_config.user_id,
            trader_config.password,
            app_id=trader_config.app_id,
            auth_code=trader_config.auth_code,
            auto_settlement_confirm=False,
            md_front=trader_config.md_front,
            ctp_env_profile=trader_config.ctp_env_profile,
        )
        self._trader.install_callback_ingress_sink(
            self._callback_owner,
            self._session_adapter.sink,
            self._session_adapter.bind_session,
            self._session_adapter.verify_native_call,
        )
        self._set_phase("TRADER_START")
        self._native_start_attempted = True
        self._trader.start(block=False)
        self._set_phase("LOGIN_WAIT")
        if not self._trader.wait_ready(
            self.config.startup_timeout_s,
            should_stop=self._startup_should_stop,
        ):
            raise self._fail("TRADER_LOGIN_TIMEOUT")

        facts = self._store.read_ctp_callback_session_context_facts(
            self._callback_owner
        )
        if type(facts) is not tuple or len(facts) != 3:
            raise self._fail("CALLBACK_SESSION_READBACK_FAILED")
        binding, broker_id, user_id = facts
        from bt_api_execution import CtpCallbackSessionBindingV1

        scope = self.config.execution_scope
        if (
            type(binding) is not CtpCallbackSessionBindingV1
            or binding.owner_intent_id != self._callback_owner.owner_intent_id
            or binding.account_key != scope.account_key
            or binding.scope_key != scope.key
            or binding.trading_day != scope.trading_day
            or not binding.session_generation_id
            or broker_id != trader_config.broker_id
            or user_id != trader_config.user_id
            or self.config.native_binding.investor_id != user_id
        ):
            raise self._fail("CALLBACK_SESSION_BINDING_MISMATCH")
        session_generation_id = binding.session_generation_id

        self._callback_verifier = CtpSessionIngressEvidenceVerifierV1(
            self._store,
            scope,
            self._writer_lease,
            self._session_adapter,
            self._trader,
            session_generation_id=session_generation_id,
        )
        self._reconciliation_port = CtpOwnerCallbackReconciliationPort(
            self._store,
            scope,
            self._writer_lease,
            self._session_adapter,
            session_generation_id=session_generation_id,
            callback_verifier=self._callback_verifier,
            trade_fact_verifier=self._callback_verifier,
        )

        self._set_phase("TRADER_PREPARE")
        try:
            self._renew_writer_lease_if_due(force=True)
            self.config.prepare_trader(self._trader)
            self._renew_writer_lease_if_due(force=True)
        except Exception:
            if not self._lease_healthy:
                raise self._lease_failure() from None
            raise self._fail("TRADER_PREPARATION_FAILED") from None
        if not self._trader_write_ready():
            raise self._fail("TRADER_WRITE_READINESS_UNPROVEN")

        self._set_phase("SUBMIT_PORT")
        self._submit_port = CtpManagedSubmitPort(
            self._store,
            scope,
            self._writer_lease,
            self._trader,
            self._session_adapter,
            self._reconciliation_port,
            self.config.native_binding,
            session_generation_id=session_generation_id,
            callback_pump_events=self.config.callback_pump_events,
        )
        self._set_phase("BASIC_ADMISSION")
        self._basic_gate = self.config.basic_admission_factory(
            self._store, scope, self._submit_port
        )
        from bt_api_execution import ExecutionBasicAdmissionGate

        if type(self._basic_gate) is not ExecutionBasicAdmissionGate:
            raise self._fail("BASIC_ADMISSION_GATE_INVALID")
        self._submit_port.bind_basic_admission(self._basic_gate)
        self._execution_owner = self._submit_port.execution_owner

        context = CtpRuntimeAdmissionContext(
            store=self._store,
            execution_scope=scope,
            service_scope=self.config.service_scope,
            writer_lease=self._writer_lease,
            trader=self._trader,
            family_owner=self._family_owner,
            callback_owner=self._callback_owner,
            session_adapter=self._session_adapter,
            session_generation_id=session_generation_id,
            reconciliation_port=self._reconciliation_port,
            submit_port=self._submit_port,
            execution_owner=self._execution_owner,
            native_binding=self.config.native_binding,
        )
        self._set_phase("RISK_ADMISSION")
        try:
            admission = self.config.risk_admission_factory(context)
        except Exception:
            raise self._fail("RISK_ADMISSION_FACTORY_FAILED") from None
        if not isinstance(admission, Mapping) or set(admission) - {
            "order_gate",
            "cancel_gate",
            "cancel_quota_admission",
        }:
            raise self._fail("RISK_ADMISSION_RESOURCES_INVALID")
        self._order_gate = admission.get("order_gate")
        self._cancel_gate = admission.get("cancel_gate")
        self._cancel_quota_admission = admission.get("cancel_quota_admission")
        if self._order_gate is None or self._cancel_gate is None:
            raise self._fail("RISK_ADMISSION_RESOURCES_INCOMPLETE")
        if self._cancel_quota_admission is not None and not callable(
            self._cancel_quota_admission
        ):
            raise self._fail("CANCEL_QUOTA_ADMISSION_INVALID")

        from bt_api_execution import SharedRiskAdmissionAdapter
        from bt_api_risk import DurableRiskAdmissionAdapter

        from bt_api_py.service_execution_risk_cancel import RiskExecutionCancelAdmissionGate

        if (
            type(self._cancel_gate) is not RiskExecutionCancelAdmissionGate
            or self._cancel_gate._store is not self._store
            or self._cancel_gate._execution_scope != scope
        ):
            raise self._fail("RISK_CANCEL_GATE_BINDING_INVALID")
        cancel_risk_gate = self._cancel_gate._gate
        if type(self._order_gate) is DurableRiskAdmissionAdapter:
            same_risk_authority = (
                self._order_gate.is_bound_to(cancel_risk_gate) is True
            )
        elif type(self._order_gate) is SharedRiskAdmissionAdapter:
            same_risk_authority = self._order_gate._risk_gate is cancel_risk_gate
        else:
            same_risk_authority = False
        if not same_risk_authority:
            raise self._fail("RISK_ORDER_CANCEL_AUTHORITY_MISMATCH")
        self._cancel_port = CtpManagedCancelPort(
            self._execution_owner,
            self._cancel_gate,
            callback_pump_events=self.config.callback_pump_events,
        )

    def _fail(self, code: str) -> CtpExecutionRuntimeError:
        self._code = code
        return CtpExecutionRuntimeError(code)

    def _has_owned_state(self) -> bool:
        return any(
            value is not None
            for value in (
                self._store,
                self._trader,
                self._family_owner,
                self._writer_lease,
                self._callback_owner,
            )
        )

    def _callback_ingress_is_drained(self) -> bool:
        store = self._store
        owner = self._callback_owner
        lease = self._writer_lease
        if store is None or owner is None or lease is None:
            self._set_phase("CLOSE_FAILED", "CALLBACK_DRAIN_STATUS_UNAVAILABLE")
            raise CtpExecutionRuntimeError("CALLBACK_DRAIN_STATUS_UNAVAILABLE")
        try:
            from bt_api_execution import CtpCallbackIngressDrainStatus

            status = store.read_ctp_callback_ingress_drain_status(
                owner,
                writer_lease=lease,
            )
        except Exception:
            self._set_phase("CLOSE_FAILED", "CALLBACK_DRAIN_STATUS_UNAVAILABLE")
            raise CtpExecutionRuntimeError(
                "CALLBACK_DRAIN_STATUS_UNAVAILABLE"
            ) from None
        if type(status) is not CtpCallbackIngressDrainStatus:
            self._set_phase("CLOSE_FAILED", "CALLBACK_DRAIN_STATUS_INVALID")
            raise CtpExecutionRuntimeError("CALLBACK_DRAIN_STATUS_INVALID")
        live_owner = status.owner_state in {"PREPARED", "ACTIVE"} and status.poison_code is None
        normal_stop_owner = (
            self._native_stop_complete is True
            and status.owner_state == "POISONED"
            and status.poison_code == "owner_stop"
        )
        if (
            status.owner_handle is not owner
            or not (live_owner or normal_stop_owner)
            or type(status.last_source_sequence) is not int
            or type(status.applied_source_sequence) is not int
            or type(status.pending_count) is not int
            or type(status.drained) is not bool
        ):
            self._set_phase("CLOSE_FAILED", "CALLBACK_DRAIN_STATUS_INVALID")
            raise CtpExecutionRuntimeError("CALLBACK_DRAIN_STATUS_INVALID")
        return status.drained

    def _make_resources(self, *, complete: bool) -> Any:
        from bt_api_py.service_components import ComponentResources

        values: dict[str, object] = {}
        if complete:
            if any(
                value is None
                for value in (
                    self._store,
                    self._submit_port,
                    self._basic_gate,
                    self._order_gate,
                    self._cancel_gate,
                    self._cancel_port,
                )
            ):
                raise CtpExecutionRuntimeError("RUNTIME_RESOURCES_INCOMPLETE")
            admission: dict[str, object] = {
                "order_gate": self._order_gate,
                "cancel_gate": self._cancel_gate,
                "basic_admission_gate": self._basic_gate,
            }
            if self._cancel_quota_admission is not None:
                admission["cancel_quota_admission"] = self._cancel_quota_admission
            values = {
                "store": self._store,
                "borrowed_writer_lease": self._writer_lease,
                "targets": MappingProxyType(
                    {self.config.service_scope: self.config.execution_scope}
                ),
                "dispatch": self._submit_port,
                "admission": MappingProxyType(admission),
                "cancel_dispatch": self._cancel_port,
                "cancel_before_dispatch": self._cancel_port.prepare_target,
            }
        return ComponentResources.owned(
            values,
            ready=self.is_ready,
            maintain=self.maintain,
            next_maintenance_delay=self.next_maintenance_delay,
            close=self.close,
            internal_ports={},
        )

    def close(self) -> None:
        """Pump available callbacks, confirm native stop, then close the Store."""

        self._assert_owner_thread()
        if self._closed:
            return
        self._ready = False
        self._set_phase("CLOSING")
        callback_drain_failed = False
        if (
            self._store is not None
            and self._callback_owner is not None
            and self._execution_owner is not None
            and not self._native_stop_attempted
        ):
            drained = 0
            try:
                while drained < self.config.drain_limit:
                    remaining = self.config.drain_limit - drained
                    results = self._execution_owner.pump_callbacks(
                        min(self.config.callback_pump_events, remaining)
                    )
                    if not results:
                        break
                    drained += len(results)
            except Exception:
                callback_drain_failed = True
        if self._native_start_attempted and not self._native_stop_complete:
            if self._trader is None:
                self._set_phase("CLOSE_FAILED", "TRADER_REFERENCE_MISSING")
                raise CtpExecutionRuntimeError("TRADER_REFERENCE_MISSING")
            self._native_stop_attempted = True
            try:
                receipt = self._trader.stop_and_wait(self.config.stop_timeout_s)
            except Exception:
                self._set_phase("CLOSE_FAILED", "NATIVE_STOP_UNCERTAIN")
                raise CtpExecutionRuntimeError("NATIVE_STOP_UNCERTAIN") from None
            self._last_stop_receipt = receipt
            from bt_api_ctp.ctp.client import CtpNativeStopReceipt

            if (
                type(receipt) is not CtpNativeStopReceipt
                or type(receipt.connection_generation) is not int
                or receipt.connection_generation < 0
                or type(receipt.join_required) is not bool
                or type(receipt.join_completed) is not bool
                or type(receipt.native_released) is not bool
                or (receipt.thread_alive is not None and type(receipt.thread_alive) is not bool)
                or type(receipt.timed_out) is not bool
                or receipt.complete is not True
            ):
                self._set_phase("CLOSE_FAILED", "NATIVE_STOP_UNCERTAIN")
                raise CtpExecutionRuntimeError("NATIVE_STOP_UNCERTAIN")
            self._native_stop_complete = True

        if self._store is not None and not self._store_closed:
            if self._callback_owner is not None:
                if callback_drain_failed:
                    self._set_phase("CLOSE_FAILED", "CALLBACK_DRAIN_FAILED")
                    raise CtpExecutionRuntimeError("CALLBACK_DRAIN_FAILED")
                if not self._callback_ingress_is_drained():
                    self._set_phase("CLOSE_FAILED", "CALLBACK_DRAIN_INCOMPLETE")
                    raise CtpExecutionRuntimeError("CALLBACK_DRAIN_INCOMPLETE")
            try:
                self._store.close()
            except Exception:
                self._set_phase("CLOSE_FAILED", "STORE_CLOSE_UNCERTAIN")
                raise CtpExecutionRuntimeError("STORE_CLOSE_UNCERTAIN") from None
            self._store_closed = True
        self._closed = True
        self._set_phase("CLOSED")


class CtpExecutionResourceProvider:
    """Build a single ``execution/account_owner`` resource set on Host start."""

    def __init__(self, config: CtpExecutionRuntimeConfig) -> None:
        if type(config) is not CtpExecutionRuntimeConfig:
            raise TypeError("exact CTP execution runtime config is required")
        self.config = config
        self.last_runtime: CtpExecutionRuntime | None = None
        self._used = False
        self._lock = threading.Lock()

    def __call__(
        self,
        component: str,
        instance_id: str,
        options: Mapping[str, object],
        scope: Any,
    ) -> Any:
        from bt_api_py.service_components import ServiceResourceConstructionError

        if (
            component != "execution"
            or not isinstance(options, Mapping)
            or options.get("role") != "account_owner"
            or type(instance_id) is not str
            or not instance_id
            or scope != self.config.service_scope
        ):
            raise CtpExecutionRuntimeError("RUNTIME_COMPONENT_BINDING_INVALID")
        with self._lock:
            if self._used:
                raise CtpExecutionRuntimeError("RUNTIME_PROVIDER_ONE_SHOT")
            self._used = True
        runtime = CtpExecutionRuntime(self.config, instance_id)
        self.last_runtime = runtime
        try:
            return runtime.bootstrap()
        except ServiceResourceConstructionError:
            raise
        except CtpExecutionRuntimeError:
            raise
        except Exception:
            if runtime._has_owned_state():
                resources = runtime._make_resources(complete=False)
                runtime._resources = resources
                raise ServiceResourceConstructionError(resources) from None
            raise CtpExecutionRuntimeError("RUNTIME_BOOTSTRAP_FAILED") from None

