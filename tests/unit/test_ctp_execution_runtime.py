"""Synthetic-native integration checks for the owned CTP execution runtime.

The TraderClient, callback SPI/ingress, SQLite store, owner and managed ports
remain production implementations. Only the CTP API leaf is replaced with a
deterministic callback source; no network or native extension is used. The
approval, verifier, and settlement/readback evidence are synthetic fixtures
and do not establish credential or real-account acceptance.
"""

from __future__ import annotations

import hashlib
import threading
import time
from contextlib import contextmanager
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from types import MappingProxyType, SimpleNamespace

import bt_api_execution.store as execution_store_module
import pytest
import test_ctp_callback_ingress_client as ctp_client_fixtures
import test_ctp_execution_owner as ctp_owner_fixtures
import test_service_order_entry_components as service_component_fixtures
from bt_api_base.service_protocol import WireScope
from bt_api_ctp.ctp.client import (
    CtpRuntimeSimNowCredentialBinding,
    _issue_ctp_execution_authority_for_core,
)
from bt_api_execution import (
    CtpCallbackIngressDrainStatus,
    ExecutionAdmissionValidatorRegistry,
    ExecutionBasicAdmissionGate,
    ExecutionProviderValidatorV1,
    OrderIntent,
    OrderType,
    PositionEffect,
    Side,
    TimeInForce,
    WriterLeaseUnavailable,
)
from bt_api_risk import (
    AccountScope,
    DurableRiskAdmissionAdapter,
    DurableRiskGate,
    IntentAction,
    RiskIntent,
    RiskPolicy,
)

import bt_api_py.ctp_execution_runtime as runtime_module
from bt_api_py._ctp_credential_binding import _new_scope, _new_test_verifier
from bt_api_py._ctp_execution_authorization import (
    _CAPABILITY_SEAL,
    APPROVAL_PURPOSE,
    SIMNOW_ENTRY_APPROVAL_SCHEMA_VERSION,
    CtpExecutionApproval,
    _new_runtime_context,
)
from bt_api_py.ctp_admission_authority import CtpNativeDispatchBindingV1
from bt_api_py.ctp_risk_dispatch_bridge import CtpRiskDispatchBridge
from bt_api_py.ctp_execution_runtime import (
    CtpExecutionResourceProvider,
    CtpExecutionRuntimeConfig,
    CtpExecutionRuntimeError,
    CtpTraderConnectionConfig,
)
from bt_api_py.ctp_managed_cancel_port import CtpManagedCancelPort
from bt_api_py.ctp_managed_submit_port import CtpManagedSubmitPort
from bt_api_py.service_components import (
    ComponentResources,
    ResourceOwnership,
    ServiceResourceConstructionError,
    build_component,
)
from bt_api_py.service_execution_risk_cancel import RiskExecutionCancelAdmissionGate
from bt_api_py.service_risk_order_journal import RiskOrderJournal
from tests.unit import test_service_host as service_host_fixtures

_INSTRUMENT = "rb2610"
_BROKER_ID = "9999"
_USER_ID = "investor-1"
_TRADING_DAY = "20260926"
_SIMNOW_PROFILE = "config_front_pair"
_SIMNOW_TD_FRONT = "tcp://managed-test-td.invalid:41001"
_SIMNOW_MD_FRONT = "tcp://managed-test-md.invalid:41002"
_SYNTHETIC_HASH = "a" * 64
_INSTANCE_ID = "execution-owner"


def _prepare_synthetic_test_trader(trader, *, profile, front, md_front):
    """Bind a sourcefake trader whose server already reports settlement."""

    settlement_readback = trader.verify_settlement_confirmation(timeout=0.5)
    if not settlement_readback.complete or trader.is_trading_ready is not True:
        raise AssertionError("synthetic preconfirmed settlement readback failed")

    capability = _issue_ctp_execution_authority_for_core()
    trader.configure_execution_gate(capability)

    owner = SimpleNamespace(exchange_feeds={})
    feed = SimpleNamespace(
        _trader=trader,
        _execution_bound_td_front=front,
        _execution_bound_md_front=md_front,
        _md_client=SimpleNamespace(front=md_front, connection_generation=1),
        _md_stream_generation=1,
    )
    owner.exchange_feeds["CTP___FUTURE"] = feed
    account_fingerprint = f"acct_{trader._account_fingerprint}"
    scope_values = {
        "account_fingerprint": account_fingerprint,
        "account_fingerprint_sha256": hashlib.sha256(
            f"{trader._bound_broker_id}:{trader._bound_user_id}".encode()
        ).hexdigest(),
        "trading_day": trader._trading_day,
        "connection_generation": trader._connection_generation,
        "environment_profile": profile,
        "td_front": front,
        "md_front": md_front,
        "td_front_sha256": hashlib.sha256(front.encode("utf-8")).hexdigest(),
        "md_front_sha256": hashlib.sha256(md_front.encode("utf-8")).hexdigest(),
        "backtrader_sha256": _SYNTHETIC_HASH,
        "backtrader_runtime_sha256": "0" * 64,
        "bt_api_py_sha256": _SYNTHETIC_HASH,
        "bt_api_ctp_sha256": _SYNTHETIC_HASH,
        "bt_api_base_sha256": _SYNTHETIC_HASH,
        "native_sha256": _SYNTHETIC_HASH,
        "dependency_hashes_sha256": _SYNTHETIC_HASH,
        "configuration_sha256": _SYNTHETIC_HASH,
        "strategy_identity_sha256": _SYNTHETIC_HASH,
        "preflight_sha256": _SYNTHETIC_HASH,
        "evidence_sha256": _SYNTHETIC_HASH,
        "md_connection_generation": 1,
        "md_stream_generation": 1,
    }
    credential_scope = _new_scope(scope_values)
    credential_verifier = _new_test_verifier(
        owner,
        lambda: {
            "credential_binding_key_id": "runtime-test-key",
            "credential_binding_hmac_sha256": _SYNTHETIC_HASH,
        },
    )
    credential_binding_values = credential_verifier.refresh(
        credential_scope, owner=owner, operation="test"
    )
    instrument = {"instrument_id": _INSTRUMENT, "exchange_id": "SHFE"}
    context_values = {
        "source": "synthetic_test",
        "context_source": "synthetic_test",
        "candidate_id": "ctp-runtime-test-candidate",
        "strategy_id": "ctp-runtime-test-strategy",
        "strategy_identity_sha256": _SYNTHETIC_HASH,
        "execution_cycle_id": "ctp-runtime-test-cycle",
        "configuration_sha256": _SYNTHETIC_HASH,
        "backtrader_sha256": _SYNTHETIC_HASH,
        "bt_api_py_sha256": _SYNTHETIC_HASH,
        "bt_api_ctp_sha256": _SYNTHETIC_HASH,
        "bt_api_base_sha256": _SYNTHETIC_HASH,
        "native_sha256": _SYNTHETIC_HASH,
        "dependency_hashes_sha256": _SYNTHETIC_HASH,
        "authorized_instruments": [instrument],
        "primary_instrument": instrument,
        "account_fingerprint": account_fingerprint,
        "trading_day": trader._trading_day,
        "connection_generation": trader._connection_generation,
        "environment_profile": profile,
        "preflight_sha256": _SYNTHETIC_HASH,
        "evidence_sha256": _SYNTHETIC_HASH,
        "budget_policy_id": "ctp-runtime-test-budget",
        "budget_limit": "100000",
        "future_reservation_id": "ctp-runtime-test-reservation",
        **credential_binding_values,
    }
    self = owner
    credential_binding_verifier = credential_verifier

    def refresh_context():
        refreshed = credential_binding_verifier.refresh(
            credential_scope, owner=self, operation="test"
        )
        return _new_runtime_context(
            {**context_values, **refreshed}, owner=self, refresh=refresh_context
        )

    context = _new_runtime_context(
        context_values, owner=owner, refresh=refresh_context
    )
    now = datetime.now(UTC)
    expires = (now + timedelta(hours=1)).replace(microsecond=0)
    issued = (now - timedelta(seconds=1)).replace(microsecond=0)

    def iso(value):
        return value.isoformat().replace("+00:00", "Z")

    payload = {
        "purpose": APPROVAL_PURPOSE,
        "schema_version": SIMNOW_ENTRY_APPROVAL_SCHEMA_VERSION,
        "authorized_instruments": [instrument],
        "primary_instrument": instrument,
    }
    payload.update({key: value for key, value in context_values.items() if key != "source"})
    payload.update(
        {
            "approval_id": "ctp-runtime-test-approval",
            "nonce": "ctp-runtime-test-nonce",
            "issued_at": iso(issued),
            "not_before": iso(issued),
            "expires_at": iso(expires),
            "revocation_snapshot_version": 1,
        }
    )
    approval = CtpExecutionApproval(
        payload=MappingProxyType(payload),
        payload_sha256=_SYNTHETIC_HASH,
        trust_root_sha256=_SYNTHETIC_HASH,
        signature=b"synthetic-test-only",
        revocation_snapshot=MappingProxyType(
            {"version": 1, "expires_at": iso(expires)}
        ),
        _seal=_CAPABILITY_SEAL,
    )
    trader.configure_runtime_simnow_credential_binding(
        capability,
        CtpRuntimeSimNowCredentialBinding(
            owner=owner,
            approval_context=context,
            approval=approval,
            credential_binding_verifier=credential_verifier,
            td_front=front,
            md_front=md_front,
            environment_profile=profile,
            write_intent_verifier=lambda _write_scope: True,
        ),
    )
    return trader.get_execution_gate_state()


def _native_factory(*, no_startup_callbacks: bool = False, block_join: bool = False):
    class RuntimeFakeApi(ctp_client_fixtures._FakeTraderApi):
        def __init__(self):
            super().__init__()
            self.init_ingress_state = None
            self.init_sink = None
            self.init_ingress_phase = None
            if block_join:
                self.join_release.clear()

        def RegisterSpi(self, spi):
            self.calls.append(("RegisterSpi",))
            super().RegisterSpi(spi)

        def Init(self):
            self.calls.append(("Init",))
            client = self.spi._c
            self.init_ingress_state = client._callback_ingress
            self.init_sink = getattr(self.init_ingress_state, "append_sink", None)
            self.init_ingress_phase = getattr(self.init_ingress_state, "phase", None)
            if not no_startup_callbacks:
                super().Init()

        def ReqUserLogin(self, field, request_id):
            self.calls.append(("ReqUserLogin", request_id))
            response = SimpleNamespace(
                BrokerID=field.BrokerID,
                UserID=field.UserID,
                TradingDay=_TRADING_DAY,
                FrontID=7,
                SessionID=19,
                MaxOrderRef="17",
            )
            info = SimpleNamespace(ErrorID=0, ErrorMsg="synthetic")
            self.spi.OnRspUserLogin(response, info, request_id, True)
            return 0

        def ReqSettlementInfoConfirm(self, field, request_id):
            self.calls.append(("ReqSettlementInfoConfirm", request_id))
            response = SimpleNamespace(
                BrokerID=field.BrokerID,
                InvestorID=field.InvestorID,
                TradingDay=_TRADING_DAY,
                ConfirmDate=_TRADING_DAY,
            )
            self.spi.OnRspSettlementInfoConfirm(
                response, SimpleNamespace(ErrorID=0, ErrorMsg=""), request_id, True
            )
            return 0

        def ReqQrySettlementInfoConfirm(self, field, request_id):
            self.calls.append(("ReqQrySettlementInfoConfirm", request_id))
            response = SimpleNamespace(
                BrokerID=field.BrokerID,
                InvestorID=field.InvestorID,
                TradingDay=_TRADING_DAY,
                ConfirmDate=_TRADING_DAY,
            )
            self.spi.OnRspQrySettlementInfoConfirm(
                response, SimpleNamespace(ErrorID=0, ErrorMsg=""), request_id, True
            )
            return 0

        def Join(self):
            self.join_started.set()
            self.join_release.wait()
            return 0

    class RuntimeFakeApiFactory:
        instances = []

        @classmethod
        def CreateFtdcTraderApi(cls, _flow_path):
            api = RuntimeFakeApi()
            cls.instances.append(api)
            return api

    return RuntimeFakeApiFactory


def _install_native_leaf(monkeypatch, tmp_path, factory):
    client_module = ctp_client_fixtures.client_module
    monkeypatch.setattr(client_module, "_check_native_module", lambda: None)
    monkeypatch.setattr(client_module, "_flow_dir", lambda _name: str(tmp_path / "flow"))
    monkeypatch.setattr(client_module, "_register_ctp_native_api", lambda _api: None)
    monkeypatch.setattr(client_module, "CThostFtdcTraderApi", factory)


@contextmanager
def _runtime_case(
    tmp_path,
    monkeypatch,
    *,
    no_startup_callbacks: bool = False,
    block_join: bool = False,
    lease_ttl_ns: int = 60_000_000_000,
):
    execution_scope = ctp_owner_fixtures._scope()
    service_scope = WireScope(
        "ctp-runtime-test-domain",
        account_ref=execution_scope.account_ref,
        strategy_ref=execution_scope.strategy_id,
    )
    factory = _native_factory(
        no_startup_callbacks=no_startup_callbacks,
        block_join=block_join,
    )
    _install_native_leaf(monkeypatch, tmp_path, factory)

    journal = RiskOrderJournal(
        tmp_path / "risk-order-journal.sqlite3", writer_id="ctp-runtime-risk-journal"
    )
    risk_gate: DurableRiskGate | None = None
    case_holder: dict[str, object] = {}
    runtime_risk_scope = AccountScope(
        execution_scope.provider,
        execution_scope.account_ref,
        execution_scope.environment,
    )

    def risk_admission_factory(context):
        nonlocal risk_gate
        bootstrap_steps.append("risk_admission_factory_started")
        risk_gate = DurableRiskGate(
            tmp_path / "risk.sqlite3",
            RiskPolicy(
                "ctp-runtime-risk-v1",
                Decimal("100000"),
                10,
                permit_ttl_seconds=60.0,
                notional_unit="USD",
            ),
            execution_journal_authority=journal,
            recovery_close_source_authority=context.recovery_close_source_authority,
        )
        risk_gate.set_strategy_allocation(
            runtime_risk_scope,
            execution_scope.strategy_id,
            "ctp-runtime-allocation-v1",
            max_notional=Decimal("100000"),
            notional_unit="USD",
        )
        current_case = case_holder.get("case")
        if current_case is not None:
            current_case.risk_gate = risk_gate
        cancel_gate = RiskExecutionCancelAdmissionGate(
            risk_gate,
            journal,
            context.store,
            risk_scope=runtime_risk_scope,
            execution_scope=context.execution_scope,
            wire_scope_template=service_scope,
        )

        class RuntimeRiskIntentMapper:
            wire_account_ref = context.execution_scope.account_ref

            def __call__(self, intent):
                return RiskIntent(
                    intent_id=intent.intent_id,
                    scope=runtime_risk_scope,
                    action=(
                        IntentAction.INCREASE
                        if intent.position_effect is PositionEffect.OPEN
                        else IntentAction.REDUCE
                    ),
                    notional=intent.quantity * intent.price,
                    payload_fingerprint=intent.fingerprint,
                    strategy_id=intent.scope.strategy_id,
                    allocation_version="ctp-runtime-allocation-v1",
                    instrument=intent.instrument,
                    quantity=intent.quantity,
                    quantity_unit="contracts",
                    notional_unit="USD",
                    position_side=intent.position_side or "NET",
                    position_bucket=intent.position_bucket or "ALL",
                )

        order_admission = DurableRiskAdmissionAdapter(
            risk_gate, RuntimeRiskIntentMapper()
        )
        bootstrap_steps.append("risk_admission_factory_completed")
        bridge = CtpRiskDispatchBridge(
            order_admission, journal, account_scope=runtime_risk_scope, exchange_name="SHFE"
        )
        return {"order_gate": bridge, "cancel_gate": cancel_gate}

    def basic_admission_factory(store, scope, submit_port):
        bootstrap_steps.append("basic_admission_factory_started")
        validator = ExecutionProviderValidatorV1(
            provider_id=scope.provider,
            validator_id="ctp-runtime-test-v1",
            version="v1",
            dispatch_port=submit_port,
            instrument_map={_INSTRUMENT: _INSTRUMENT},
            quantity_steps={_INSTRUMENT: Decimal("1")},
            max_quantities={_INSTRUMENT: Decimal("100")},
            price_ticks={_INSTRUMENT: Decimal("0.01")},
            supported_order_types=(OrderType.LIMIT,),
            supported_time_in_force=(TimeInForce.DAY,),
            supported_actions=(
                (Side.BUY, PositionEffect.OPEN),
                (Side.BUY, PositionEffect.CLOSE_TODAY),
                (Side.SELL, PositionEffect.CLOSE_TODAY),
            ),
            supports_reduce_only=True,
        )
        gate = ExecutionBasicAdmissionGate(
            store,
            ExecutionAdmissionValidatorRegistry((validator,)),
            retain_claimed_authorizations=True,
        )
        bootstrap_steps.append("basic_admission_factory_completed")
        return gate

    td_front, md_front = _SIMNOW_TD_FRONT, _SIMNOW_MD_FRONT
    prepared = []
    bootstrap_steps = []
    prepare_error_kinds = []

    def prepare_trader(trader):
        bootstrap_steps.append("prepare_trader_started")
        assert trader.front == td_front
        assert trader.md_front == md_front
        try:
            prepared.append(
                _prepare_synthetic_test_trader(
                    trader,
                    profile=_SIMNOW_PROFILE,
                    front=td_front,
                    md_front=md_front,
                )
            )
        except Exception as exc:
            prepare_error_kinds.append(_finite_exception_kind(exc))
            raise
        bootstrap_steps.append("prepare_trader_completed")

    trader_config = CtpTraderConnectionConfig(
        front=td_front,
        md_front=md_front,
        ctp_env_profile=_SIMNOW_PROFILE,
        broker_id=_BROKER_ID,
        user_id=_USER_ID,
        password="synthetic-only",
    )
    config = CtpExecutionRuntimeConfig(
        trader=trader_config,
        execution_scope=execution_scope,
        service_scope=service_scope,
        store_path=tmp_path / "execution-runtime.sqlite3",
        lease_owner_id="ctp-runtime-owner",
        native_binding=CtpNativeDispatchBindingV1(
            account_ref=execution_scope.account_ref,
            scope_key=execution_scope.key,
            investor_id=_USER_ID,
            intent_instrument=_INSTRUMENT,
            provider_instrument=_INSTRUMENT,
            exchange_id="SHFE",
        ),
        basic_admission_factory=basic_admission_factory,
        risk_admission_factory=risk_admission_factory,
        prepare_trader=prepare_trader,
        lease_ttl_ns=lease_ttl_ns,
        startup_timeout_s=0.5,
        stop_timeout_s=0.05 if block_join else 1.0,
        callback_pump_events=32,
        drain_limit=64,
    )
    provider = CtpExecutionResourceProvider(config)
    case = SimpleNamespace(
        config=config,
        provider=provider,
        factory=factory,
        risk_gate=risk_gate,
        journal=journal,
        service_scope=service_scope,
        execution_scope=execution_scope,
        instance_id=_INSTANCE_ID,
        resources=None,
        component=None,
        prepared=prepared,
        bootstrap_steps=bootstrap_steps,
        prepare_error_kinds=prepare_error_kinds,
        bootstrap_error_kinds=[],
        bootstrap_diagnostic=None,
        retain_resources_on_cleanup=False,
        primary_error=None,
    )
    case_holder["case"] = case
    try:
        yield case
    except Exception as primary_error:
        case.primary_error = primary_error
        raise
    finally:
        cleanup_errors = []
        for api in factory.instances:
            try:
                api.join_release.set()
            except Exception as exc:
                cleanup_errors.append(exc)
        may_close_risk_gate = not case.retain_resources_on_cleanup
        if may_close_risk_gate and case.component is not None:
            try:
                case.component.close()
            except Exception as exc:
                cleanup_errors.append(exc)
                may_close_risk_gate = False
        elif may_close_risk_gate and case.resources is not None:
            try:
                case.resources.close()
            except Exception as exc:
                cleanup_errors.append(exc)
                may_close_risk_gate = False
        if may_close_risk_gate and risk_gate is not None:
            try:
                risk_gate.close()
            except Exception as exc:
                cleanup_errors.append(exc)

        if cleanup_errors:
            if case.primary_error is not None:
                for cleanup_error in cleanup_errors:
                    case.primary_error.add_note(
                        "runtime fixture cleanup failed: "
                        f"{_finite_cleanup_code(cleanup_error)}"
                    )
            else:
                first_error, *additional_errors = cleanup_errors
                for cleanup_error in additional_errors:
                    first_error.add_note(
                        "additional runtime fixture cleanup failure: "
                        f"{_finite_cleanup_code(cleanup_error)}"
                    )
                raise first_error


def _finite_exception_kind(exc):
    known = {
        "AssertionError",
        "ContractValidationError",
        "CtpExecutionRuntimeError",
        "InvalidStateTransitionError",
        "RuntimeError",
        "ServiceComponentError",
        "ServiceResourceConstructionError",
        "TimeoutError",
        "TypeError",
        "ValueError",
    }
    name = type(exc).__name__
    return name if name in known else "OTHER_EXCEPTION"


def _finite_cleanup_code(exc):
    allowed_codes = {
        "CALLBACK_DRAIN_FAILED",
        "CALLBACK_DRAIN_INCOMPLETE",
        "CALLBACK_DRAIN_STATUS_INVALID",
        "CALLBACK_DRAIN_STATUS_UNAVAILABLE",
        "NATIVE_STOP_UNCERTAIN",
        "STORE_CLOSE_UNCERTAIN",
    }
    code = getattr(exc, "code", None)
    if type(code) is str and code in allowed_codes:
        return code
    return _finite_exception_kind(exc)


def _capture_bootstrap_diagnostic(case, exc):
    runtime = case.provider.last_runtime
    status = runtime.status if runtime is not None else None
    phase = getattr(status, "phase", None)
    code = getattr(status, "code", None)
    retained_names = (
        "_store",
        "_family_owner",
        "_writer_lease",
        "_callback_owner",
        "_session_adapter",
        "_trader",
        "_callback_verifier",
        "_reconciliation_port",
        "_submit_port",
        "_basic_gate",
        "_execution_owner",
        "_order_gate",
        "_cancel_gate",
        "_cancel_port",
    )
    retained = (
        tuple(
            name.removeprefix("_")
            for name in retained_names
            if getattr(runtime, name, None) is not None
        )
        if runtime is not None
        else ()
    )
    case.bootstrap_diagnostic = (
        _finite_exception_kind(exc),
        phase if type(phase) is str and len(phase) <= 64 else "UNKNOWN_PHASE",
        code if type(code) is str and len(code) <= 64 else "UNKNOWN_CODE",
        tuple(case.bootstrap_steps),
        tuple(case.prepare_error_kinds),
        tuple(case.bootstrap_error_kinds),
        retained,
    )


def _resolve_resources(case, *, allow_construction_failure=False):
    try:
        resources = case.provider(
            "execution",
            case.instance_id,
            {"role": "account_owner"},
            case.service_scope,
        )
    except ServiceResourceConstructionError as exc:
        case.resources = exc.resources
        _capture_bootstrap_diagnostic(case, exc)
        if allow_construction_failure:
            raise
        raise AssertionError(
            "owned runtime bootstrap failed with finite status "
            f"{case.bootstrap_diagnostic!r}"
        ) from None
    case.resources = resources
    return resources


def _build_execution_component(case, monkeypatch):
    """Exercise the built-in account-owner adapter with runtime resources."""

    monkeypatch.setattr(
        service_host_fixtures,
        "_endpoint",
        lambda: "tcp://127.0.0.1:17991",
    )
    captured = []

    def resource_provider(*args):
        try:
            resources = case.provider(*args)
        except ServiceResourceConstructionError as exc:
            case.resources = exc.resources
            _capture_bootstrap_diagnostic(case, exc)
            raise AssertionError(
                "owned runtime bootstrap failed with finite status "
                f"{case.bootstrap_diagnostic!r}"
            ) from None
        case.resources = resources
        captured.append(resources)
        return resources

    component = build_component(
        service_component_fixtures._prepared(),
        scope=case.service_scope,
        resource_provider=resource_provider,
    )
    case.component = component
    assert len(captured) == 1
    return component


class _DeterministicLeaseClock:
    """Control only the runtime monotonic seam and Store wall-clock seam."""

    def __init__(self) -> None:
        self.wall_ns = time.time_ns()
        self.monotonic_value_ns = time.monotonic_ns()

    def wall_now_ns(self) -> int:
        return self.wall_ns

    def monotonic_now_ns(self) -> int:
        return self.monotonic_value_ns

    def advance(self, delta_ns: int) -> None:
        assert type(delta_ns) is int and delta_ns >= 0
        self.wall_ns += delta_ns
        self.monotonic_value_ns += delta_ns


def _install_lease_clock(monkeypatch):
    clock = _DeterministicLeaseClock()
    monkeypatch.setattr(runtime_module, "monotonic_ns", clock.monotonic_now_ns)
    # Replace each consumer's module-local clock reference. Patching the
    # shared stdlib time module would also move Risk/CTP and unrelated clocks.
    monkeypatch.setattr(
        execution_store_module,
        "time",
        SimpleNamespace(time_ns=clock.wall_now_ns, monotonic_ns=time.monotonic_ns),
    )
    monkeypatch.setattr(
        runtime_module,
        "time",
        SimpleNamespace(time_ns=clock.wall_now_ns, monotonic_ns=time.monotonic_ns),
    )
    return clock


@pytest.mark.unit
def test_runtime_installs_callback_owner_before_init_and_shares_real_managed_owner(
    tmp_path, monkeypatch
):
    with _runtime_case(tmp_path, monkeypatch) as case:
        component = _build_execution_component(case, monkeypatch)
        resources = case.resources
        assert resources is not None

        assert type(resources) is ComponentResources
        assert resources.ownership is ResourceOwnership.OWNED
        assert resources.ready() is True
        assert len(case.prepared) == 1
        prepared_state = case.prepared[0]
        assert type(prepared_state) is dict
        runtime = case.provider.last_runtime
        assert runtime is not None
        assert prepared_state["managed"] is True
        assert prepared_state["armed"] is False
        assert prepared_state["runtime_simnow_credential_binding_configured"] is True
        assert prepared_state["runtime_simnow_write_verifier_configured"] is True
        assert runtime._trader.is_trading_ready is True
        assert case.factory.instances
        api = case.factory.instances[0]
        call_names = [call[0] for call in api.calls]
        assert call_names.count("ReqQrySettlementInfoConfirm") == 1
        assert "ReqSettlementInfoConfirm" not in call_names
        assert call_names.index("RegisterSpi") < call_names.index("Init")
        assert api.init_ingress_state is not None
        assert api.init_sink is not None
        assert api.init_ingress_phase == "PRE_LOGIN"

        assert resources.internal_ports == {}
        submit_port = runtime._submit_port
        cancel_port = runtime._cancel_port
        assert type(submit_port) is CtpManagedSubmitPort
        assert type(cancel_port) is CtpManagedCancelPort
        assert submit_port._owner is cancel_port._owner
        assert runtime._execution_owner is submit_port._owner
        assert runtime._trader is submit_port._owner._trader
        assert runtime._session_adapter is submit_port._owner._session_adapter
        assert resources.values["dispatch"] is submit_port
        assert resources.values["cancel_dispatch"] is cancel_port
        assert resources.values["borrowed_writer_lease"] is runtime._writer_lease
        assert resources.values["targets"][case.service_scope] is case.execution_scope
        assert resources.values["cancel_before_dispatch"] == cancel_port.prepare_target
        assert set(resources.values) == {
            "store",
            "targets",
            "dispatch",
            "admission",
            "cancel_dispatch",
            "cancel_before_dispatch",
            "borrowed_writer_lease",
        }
        admission = resources.values["admission"]
        assert set(admission) == {
            "order_gate",
            "cancel_gate",
            "basic_admission_gate",
        }
        assert admission["basic_admission_gate"] is submit_port._gate
        assert admission["cancel_gate"] is cancel_port._admission_gate
        assert type(admission["order_gate"]) is CtpRiskDispatchBridge
        assert admission["order_gate"].is_bound_to(case.risk_gate)
        assert admission["order_gate"]._journal is admission["cancel_gate"]._journal
        assert admission["cancel_gate"]._gate is case.risk_gate
        assert component.component == "execution"
        assert component.instance_id == case.instance_id
        assert component.readiness() is True
        assert {"orders.submit", "orders.cancel"}.issubset(component.handlers)
        service_adapter = component.handlers["orders.submit"].__kwdefaults__["_adapter"]
        order_entry = service_adapter._orders
        assert order_entry._dispatch is submit_port
        assert order_entry._cancel_dispatch is cancel_port
        assert order_entry._cancel_before_dispatch == cancel_port.prepare_target
        assert order_entry._store is resources.values["store"]
        assert order_entry._writer_id == runtime._writer_lease.owner_id
        assert order_entry._borrowed_writer_lease is runtime._writer_lease
        assert component.handlers["orders.cancel"].__kwdefaults__["_adapter"] is service_adapter
        owner = submit_port._owner
        assert owner._trader._api is api
        assert owner._session_adapter.store is resources.values["store"]
        assert owner._store is resources.values["store"]
        assert owner._scope is case.execution_scope
        assert owner._writer_lease.owner_id == case.config.lease_owner_id
        assert runtime._writer_lease is owner._writer_lease
        assert submit_port._owner is cancel_port._owner is owner
        assert api.init_sink is owner._session_adapter.sink

        facts = resources.values["store"].read_ctp_callback_session_context_facts(
            owner._owner_handle
        )
        binding = facts[0]
        assert binding.scope_key == case.execution_scope.key
        assert binding.trading_day == _TRADING_DAY
        assert binding.dispatch_front_id == 7
        assert binding.dispatch_session_id == 19

        component.close()
        case.component = None
        assert runtime.status.closed is True
        assert type(runtime.last_stop_receipt) is ctp_client_fixtures.client_module.CtpNativeStopReceipt
        assert runtime.last_stop_receipt.complete is True
        assert api.release_completed.is_set()


@pytest.mark.unit
def test_runtime_close_is_owner_thread_only_and_leaves_resources_live_on_denial(
    tmp_path, monkeypatch
):
    with _runtime_case(tmp_path, monkeypatch) as case:
        resources = _resolve_resources(case)
        failures = []

        def close_from_foreign_thread():
            try:
                resources.close()
            except Exception as exc:
                failures.append(exc)

        thread = threading.Thread(target=close_from_foreign_thread)
        thread.start()
        thread.join(1.0)
        assert not thread.is_alive()
        assert len(failures) == 1
        assert case.provider.last_runtime.status.ready is True
        assert resources.ready() is True
        assert len(case.prepared) == 1
        api = case.factory.instances[0]
        assert not api.release_completed.is_set()
        resources.close()
        assert case.provider.last_runtime.status.closed is True


@pytest.mark.unit
def test_runtime_readiness_withdraws_when_settlement_readback_refreshes_binding(
    tmp_path, monkeypatch
):
    with _runtime_case(tmp_path, monkeypatch) as case:
        resources = _resolve_resources(case)
        runtime = case.provider.last_runtime
        assert runtime is not None
        trader = runtime._trader
        assert resources.ready() is True
        assert trader.is_trading_ready is True
        before = trader.get_execution_gate_state()
        assert before["managed"] is True
        assert before["armed"] is False
        assert before["runtime_simnow_credential_binding_configured"] is True

        # A real settlement server readback invalidates the one-session
        # managed credential binding before issuing a fresh readback. The
        # fake native API delivers that result through the registered Trader
        # SPI, exercising the normal callback path without changing state.
        api = case.factory.instances[0]
        old_query_count = sum(
            call[0] == "ReqQrySettlementInfoConfirm" for call in api.calls
        )
        readback = trader.verify_settlement_confirmation(timeout=0.5)
        assert readback.complete is True
        assert sum(call[0] == "ReqQrySettlementInfoConfirm" for call in api.calls) == (
            old_query_count + 1
        )

        after = trader.get_execution_gate_state()
        assert after["managed"] is True
        assert after["armed"] is False
        assert after["runtime_simnow_credential_binding_configured"] is False
        assert after["runtime_simnow_write_verifier_configured"] is False
        assert trader.is_trading_ready is True
        assert runtime.is_ready() is False
        assert resources.ready() is False
        assert runtime.status.ready is False


@pytest.mark.unit
def test_runtime_login_timeout_closes_only_after_prepared_inbox_is_proven_empty(
    tmp_path, monkeypatch
):
    with _runtime_case(tmp_path, monkeypatch, no_startup_callbacks=True) as case:
        with pytest.raises(ServiceResourceConstructionError) as caught:
            _resolve_resources(case, allow_construction_failure=True)

        resources = caught.value.resources
        case.resources = resources
        assert type(resources) is ComponentResources
        assert resources.ownership is ResourceOwnership.OWNED
        assert case.provider.last_runtime.status.ready is False
        assert case.provider.last_runtime.status.code
        assert case.factory.instances
        api = case.factory.instances[0]
        assert any(call[0] == "Init" for call in api.calls)
        assert not any(call[0] in {"ReqAuthenticate", "ReqUserLogin"} for call in api.calls)
        assert case.prepared == []
        assert case.bootstrap_diagnostic is not None
        assert case.bootstrap_diagnostic[1] == "BOOTSTRAP_FAILED"
        assert case.bootstrap_diagnostic[2] == "TRADER_LOGIN_TIMEOUT"
        assert "prepare_trader_started" not in case.bootstrap_diagnostic[3]
        assert case.provider.last_runtime._callback_owner is not None
        assert case.provider.last_runtime._callback_owner.owner_intent_id
        runtime = case.provider.last_runtime
        drain = runtime._store.read_ctp_callback_ingress_drain_status(
            runtime._callback_owner,
            writer_lease=runtime._writer_lease,
        )
        assert type(drain) is CtpCallbackIngressDrainStatus
        assert drain.owner_handle is runtime._callback_owner
        assert drain.owner_state == "PREPARED"
        assert drain.poison_code is None
        assert drain.last_source_sequence == 0
        assert drain.applied_source_sequence == 0
        assert drain.pending_count == 0
        assert drain.drained is True

        observed_drain_statuses = [drain]
        read_drain_status = runtime._store.read_ctp_callback_ingress_drain_status

        def observe_shutdown_drain(owner_handle, *, writer_lease):
            status = read_drain_status(owner_handle, writer_lease=writer_lease)
            observed_drain_statuses.append(status)
            return status

        monkeypatch.setattr(
            runtime._store,
            "read_ctp_callback_ingress_drain_status",
            observe_shutdown_drain,
        )

        resources.close()
        assert runtime.status.closed is True
        assert case.provider.last_runtime.last_stop_receipt.complete is True
        assert api.release_completed.is_set()
        assert runtime._store_closed is True
        shutdown_drain = observed_drain_statuses[-1]
        assert shutdown_drain.owner_handle is runtime._callback_owner
        assert shutdown_drain.owner_state == "POISONED"
        assert shutdown_drain.poison_code == "owner_stop"
        assert shutdown_drain.drained is True


@pytest.mark.unit
def test_runtime_incomplete_native_join_retains_owner_until_bounded_close_retry(
    tmp_path, monkeypatch
):
    with _runtime_case(tmp_path, monkeypatch, block_join=True) as case:
        resources = _resolve_resources(case)
        api = case.factory.instances[0]
        assert api.join_started.wait(1.0)

        with pytest.raises(CtpExecutionRuntimeError) as caught:
            resources.close()

        assert caught.value.code == "NATIVE_STOP_UNCERTAIN"
        assert case.provider.last_runtime.status.closed is False
        assert type(case.provider.last_runtime.last_stop_receipt) is (
            ctp_client_fixtures.client_module.CtpNativeStopReceipt
        )
        assert case.provider.last_runtime.last_stop_receipt.complete is False
        retained_submit = case.provider.last_runtime._submit_port
        retained_cancel = case.provider.last_runtime._cancel_port
        assert retained_submit._owner is retained_cancel._owner
        retained_trader = retained_submit._owner._trader
        assert retained_trader._api is None
        assert retained_trader._last_stopped_native_api is api
        assert retained_trader._last_stop_join_required is True
        retained_join_thread = retained_trader._last_stop_join_thread
        assert retained_join_thread is not None
        assert retained_join_thread.is_alive()
        client_module = ctp_client_fixtures.client_module
        with client_module._RETIRED_CTP_NATIVE_SESSIONS_LOCK:
            assert any(
                retained_api is api
                and retained_spi is not None
                and join_thread is retained_join_thread
                for retained_api, retained_spi, join_thread
                in client_module._RETIRED_CTP_NATIVE_SESSIONS
            )
        assert not api.release_completed.is_set()

        api.join_release.set()
        resources.close()
        assert case.provider.last_runtime.status.closed is True
        assert type(case.provider.last_runtime.last_stop_receipt) is (
            ctp_client_fixtures.client_module.CtpNativeStopReceipt
        )
        assert case.provider.last_runtime.last_stop_receipt.complete is True
        assert api.release_completed.is_set()


@pytest.mark.unit
def test_runtime_maintenance_renews_same_writer_fence_and_keeps_sqlite_lease_live(
    tmp_path, monkeypatch
):
    clock = _install_lease_clock(monkeypatch)
    with _runtime_case(tmp_path, monkeypatch, lease_ttl_ns=9_000_000) as case:
        resources = _resolve_resources(case)
        runtime = case.provider.last_runtime
        assert runtime is not None
        store = resources.values["store"]
        original_lease = runtime._writer_lease
        owner = runtime._execution_owner
        initial_expiry = runtime._lease_expires_at_ns
        assert type(initial_expiry) is int
        initial_token = original_lease.fencing_token
        delay = resources.next_maintenance_delay()
        assert type(delay) is float and delay > 0

        # Advance both clocks to the owner-thread renewal deadline. The real
        # SQLite store performs the renewal; the runtime keeps the exact
        # immutable lease object shared by the owner and both managed ports.
        clock.advance(int(delay * 1_000_000_000) + 1)
        assert clock.wall_ns < initial_expiry
        resources.maintain(frozenset({case.instance_id}))
        renewed_expiry = runtime._lease_expires_at_ns
        assert type(renewed_expiry) is int and renewed_expiry > initial_expiry
        assert resources.ready() is True
        assert runtime._writer_lease is original_lease
        assert owner._writer_lease is original_lease
        assert original_lease.fencing_token == initial_token

        # Pass the original expiry without asking the runtime to renew again.
        # A different real Store writer must still be fenced, proving the
        # persisted SQLite expiry advanced rather than only the runtime cache.
        clock.advance(initial_expiry + 1 - clock.wall_ns)
        assert clock.wall_ns < renewed_expiry
        assert store.assert_writer_lease(case.execution_scope, original_lease) is None
        with pytest.raises(WriterLeaseUnavailable):
            store.acquire_or_renew_lease(
                case.execution_scope,
                "ctp-runtime-renewal-probe",
                ttl_ns=case.config.lease_ttl_ns,
                ctp_account_family_owner=runtime._family_owner,
            )

        intent = OrderIntent.limit(
            intent_id="ctp-runtime-renewed-lease-write",
            scope=case.execution_scope,
            signal_id="ctp-runtime-renewed-lease-signal",
            instrument=_INSTRUMENT,
            side=Side.BUY,
            quantity=Decimal("1"),
            price=Decimal("100"),
            time_in_force=TimeInForce.DAY,
        )
        admitted = store.admit_intent(intent, writer_lease=original_lease)
        assert admitted.intent_id == intent.intent_id
        assert admitted.state.value == "PENDING_ADMISSION"

        # The renewal callback is due again, but the original object/token is
        # still accepted after the former expiry and the durable operation.
        resources.maintain(frozenset({case.instance_id}))
        assert runtime._lease_expires_at_ns > renewed_expiry
        assert runtime._writer_lease is original_lease
        assert owner._writer_lease is original_lease
        assert resources.ready() is True
        assert resources.close() is None
        assert runtime.status.closed is True
        assert runtime.last_stop_receipt.complete is True
        assert runtime._store is store
        assert runtime._writer_lease is original_lease
        assert runtime._trader is owner._trader
        assert case.factory.instances[0].release_completed.is_set()


@pytest.mark.unit
def test_runtime_maintenance_withdraws_readiness_after_real_store_fence_takeover(
    tmp_path, monkeypatch
):
    clock = _install_lease_clock(monkeypatch)
    with _runtime_case(tmp_path, monkeypatch, lease_ttl_ns=9_000_000) as case:
        resources = _resolve_resources(case)
        runtime = case.provider.last_runtime
        assert runtime is not None
        store = resources.values["store"]
        original_lease = runtime._writer_lease
        submit_port = runtime._submit_port
        cancel_port = runtime._cancel_port
        api = case.factory.instances[0]
        delay = resources.next_maintenance_delay()
        assert type(delay) is float and delay > 0

        # Expire the exact durable owner through the public Store API, then
        # let a second writer acquire a new fencing generation before the
        # runtime's cached lease expiry. This exercises real fence loss, not
        # a mocked renewal failure.
        clock.advance(int(delay * 1_000_000_000) + 1)
        assert store.release_lease(
            case.execution_scope,
            original_lease.owner_id,
            fencing_token=original_lease.fencing_token,
        ) is True
        takeover = store.acquire_or_renew_lease(
            case.execution_scope,
            "ctp-runtime-fence-takeover",
            ttl_ns=case.config.lease_ttl_ns,
            ctp_account_family_owner=runtime._family_owner,
        )
        case.retain_resources_on_cleanup = True
        assert takeover.fencing_token > original_lease.fencing_token
        assert clock.wall_ns < runtime._lease_expires_at_ns

        with pytest.raises(CtpExecutionRuntimeError) as caught:
            resources.maintain(frozenset({case.instance_id}))
        assert caught.value.code == "WRITER_LEASE_RENEWAL_FAILED"
        assert runtime.status.phase == "LEASE_FAILED"
        assert runtime.status.code == "WRITER_LEASE_RENEWAL_FAILED"
        assert runtime.status.ready is False
        assert resources.ready() is False
        assert runtime._lease_healthy is False
        assert runtime._writer_lease is original_lease
        assert runtime._writer_lease is original_lease
        assert runtime._execution_owner is submit_port._owner
        assert runtime._cancel_port is cancel_port
        assert runtime._trader._api is api
        with pytest.raises(WriterLeaseUnavailable):
            store.assert_writer_lease(case.execution_scope, original_lease)
        assert store.assert_writer_lease(case.execution_scope, takeover) is None

        # Keep the still-owned service/native references observable. The
        # fence takeover leaves native/service custody retained; this test
        # makes no normal-close claim after the foreign writer wins.
