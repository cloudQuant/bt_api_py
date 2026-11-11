"""Trusted composition of the managed CTP execution runtime under SDK admission.

Iteration 62 (62.08, I62-10). This module assembles existing SDK parts into
one ``CtpExecutionRuntimeConfig`` for a Gateway execution host
(``ServiceHost`` execution component, role ``account_owner``):

* ``prepare_trader`` applies **bt_api_py write admission** instead of the
  SimNow approval/credential-binding allow-lists (D62-01/02): it refuses unless
  ``WriteAdmission.is_enabled(route)``, records the admission on the session,
  installs the provider's core capability, confirms today's settlement under
  a one-shot SDK grant and arms the native gate for the runtime's single
  instrument. The native gate keeps its capability, arm and per-write
  revalidation (``TraderClient.arm_execution_for_sdk_admission``).
* ``basic_admission_factory`` registers an ``ExecutionProviderValidatorV1``
  with the instrument's explicit contract facts (price tick, quantity step,
  maximum order quantity, LIMIT/DAY only).
* ``risk_admission_factory`` builds the durable risk gate, risk order journal,
  cancel gate and ``CtpRiskDispatchBridge`` the runtime requires, with
  caller-supplied limits (no account-affordability claim, D62-23).

The runtime dispatches exactly one identity-mapped futures instrument of one
account (``CtpNativeDispatchBindingV1``), so a composition serves one
instrument. Credentials enter only through ``CtpGatewayExecutionSettings``
inside the host process and are never logged here.
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass, field
from decimal import Decimal
from pathlib import Path
from typing import Any

__all__ = (
    "CTP_GATEWAY_EXECUTION_ROUTE",
    "CtpGatewayExecutionComposition",
    "CtpGatewayExecutionError",
    "CtpGatewayExecutionSettings",
    "build_ctp_gateway_execution",
)

CTP_GATEWAY_EXECUTION_ROUTE = "CTP___FUTURE"


class CtpGatewayExecutionError(RuntimeError):
    """Finite, value-free failure of the managed CTP Gateway composition."""

    _CODES = frozenset(
        {
            "GATEWAY_EXECUTION_SETTINGS_INVALID",
            "GATEWAY_EXECUTION_WRITE_ADMISSION_REQUIRED",
            "GATEWAY_EXECUTION_WRITE_ADMISSION_DISABLED",
            "GATEWAY_EXECUTION_SETTLEMENT_UNCONFIRMED",
        }
    )

    def __init__(self, code: str) -> None:
        self.code = code if type(code) is str and code in self._CODES else "GATEWAY_EXECUTION_SETTINGS_INVALID"
        super().__init__(self.code)


@dataclass(frozen=True)
class CtpGatewayExecutionSettings:
    """Explicit facts of one managed CTP account/strategy/instrument runtime."""

    symbol: str
    exchange_id: str
    strategy_ref: str
    trading_day: str
    store_directory: str | Path
    td_front: str
    md_front: str
    broker_id: str
    user_id: str = field(repr=False)
    password: str = field(repr=False)
    #: ``ctp-account-ref.v1:<sha256>``; derived from broker/investor when empty.
    account_ref: str = ""
    price_tick: Decimal = Decimal("1")
    quantity_step: Decimal = Decimal("1")
    app_id: str = "simnow_client_test"
    auth_code: str = field(default="0000000000000000", repr=False)
    domain_ref: str = "paper:bt-ctp-gateway-execution"
    lease_owner_id: str = "bt-ctp-gateway-execution-owner"
    startup_timeout_s: float = 30.0
    #: Risk ledger limits of this managed path. Required, keyword-only and
    #: without invented defaults (iteration 64 plan section 5.4). There is no
    #: per-order quantity cap here any more (D64-14): the per-principal
    #: single-order limit belongs to the selected Risk service.
    max_increase_notional: Decimal = field(kw_only=True)
    max_increase_count: int = field(kw_only=True)

    def __post_init__(self) -> None:
        if self.account_ref == "" and type(self.broker_id) is str and type(self.user_id) is str:
            object.__setattr__(
                self,
                "account_ref",
                "ctp-account-ref.v1:"
                + _sha256("bt-ctp-gateway-account|" + self.broker_id + ":" + self.user_id),
            )
        texts = (
            self.symbol,
            self.exchange_id,
            self.account_ref,
            self.strategy_ref,
            self.td_front,
            self.md_front,
            self.broker_id,
            self.user_id,
            self.password,
            self.domain_ref,
            self.lease_owner_id,
        )
        if (
            any(type(value) is not str or not value or value != value.strip() for value in texts)
            or type(self.trading_day) is not str
            or len(self.trading_day) != 8
            or not self.trading_day.isdigit()
            or any(
                type(value) is not Decimal or not value.is_finite() or value <= 0
                for value in (
                    self.price_tick,
                    self.quantity_step,
                    self.max_increase_notional,
                )
            )
            or type(self.max_increase_count) is not int
            or self.max_increase_count <= 0
        ):
            raise CtpGatewayExecutionError("GATEWAY_EXECUTION_SETTINGS_INVALID")


@dataclass
class CtpGatewayExecutionComposition:
    """The runtime config plus the composition-owned resources to close."""

    config: Any
    execution_scope: Any
    service_scope: Any
    command_binding: dict
    _closeables: list = field(default_factory=list, repr=False)

    def close(self) -> None:
        """Close composition-owned risk resources after the runtime closed."""

        errors = 0
        while self._closeables:
            closeable = self._closeables.pop()
            try:
                closeable.close()
            except Exception:
                errors += 1
        if errors:
            raise CtpGatewayExecutionError("GATEWAY_EXECUTION_SETTINGS_INVALID")


def _sha256(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def build_ctp_gateway_execution(
    settings: CtpGatewayExecutionSettings,
    *,
    write_admission: Any,
    route: str = CTP_GATEWAY_EXECUTION_ROUTE,
) -> CtpGatewayExecutionComposition:
    """Compose one managed CTP runtime config whose writes need SDK admission."""

    from bt_api_base.direct_write_protocol import WriteAdmissionDenied, WriteOperation
    from bt_api_base.service_protocol import WireScope
    from bt_api_execution import (
        ExecutionAdmissionValidatorRegistry,
        ExecutionBasicAdmissionGate,
        ExecutionProviderValidatorV1,
        ExecutionScope,
        OrderType,
        PositionEffect,
        Side,
        TimeInForce,
    )

    from bt_api_py.ctp_admission_authority import CtpNativeDispatchBindingV1
    from bt_api_py.ctp_execution_runtime import (
        CtpExecutionRuntimeConfig,
        CtpTraderConnectionConfig,
    )
    from bt_api_py.write_admission import WriteAdmission

    if type(settings) is not CtpGatewayExecutionSettings:
        raise CtpGatewayExecutionError("GATEWAY_EXECUTION_SETTINGS_INVALID")
    if type(write_admission) is not WriteAdmission:
        raise CtpGatewayExecutionError("GATEWAY_EXECUTION_WRITE_ADMISSION_REQUIRED")

    symbol, exchange_id = settings.symbol, settings.exchange_id
    directory = Path(settings.store_directory)
    execution_scope = ExecutionScope(
        "ctp", "simulation", settings.account_ref, settings.strategy_ref, settings.trading_day
    )
    service_scope = WireScope(
        settings.domain_ref,
        account_ref=settings.account_ref,
        strategy_ref=settings.strategy_ref,
    )
    command_binding = {
        "binding_schema_version": 1,
        "account_key": {
            "provider": "ctp",
            "venue": exchange_id,
            "environment": execution_scope.environment,
            "account_id": settings.account_ref,
        },
        "instruments": [
            {
                "provider": "ctp",
                "venue": exchange_id,
                "environment": execution_scope.environment,
                "product_type": "future",
                "instrument_id": symbol,
                "native_symbol": symbol,
            }
        ],
        "quantity_unit": "contracts",
        "price_currency": "CNY",
        "position_mode": "dual_side",
        "hedge_class": "speculation",
    }
    composition = CtpGatewayExecutionComposition(
        config=None,
        execution_scope=execution_scope,
        service_scope=service_scope,
        command_binding=command_binding,
    )
    strategy_identity = _sha256("bt-ctp-gateway-strategy|" + settings.strategy_ref)
    cycle_id = "bt-ctp-gateway-" + _sha256(settings.strategy_ref + settings.trading_day)[:24]

    def prepare_trader(trader: Any) -> None:
        from bt_api_ctp.ctp.client import _issue_ctp_execution_authority_for_core

        # D62-01/02 (I62-10): the SDK write admission decides; no SimNow
        # approval or front registry is consulted.
        if not write_admission.is_enabled(route):
            raise CtpGatewayExecutionError("GATEWAY_EXECUTION_WRITE_ADMISSION_DISABLED")
        capability = _issue_ctp_execution_authority_for_core()
        trader.configure_execution_gate(capability)
        trader.record_sdk_write_admission()
        generation = trader.get_session_state().get("connection_generation")
        # D62-20: read today's settlement confirmation back first; confirm it
        # under a one-shot SDK grant only when the day is still unconfirmed.
        readback = trader.verify_settlement_confirmation(timeout=15.0)
        if getattr(readback, "complete", False) is not True or trader.is_trading_ready is not True:
            grant = write_admission.issue(route, WriteOperation.SETTLEMENT_CONFIRM, generation)
            try:
                write_admission.consume(
                    grant,
                    exchange_name=route,
                    operation=WriteOperation.SETTLEMENT_CONFIRM,
                    session_generation=generation,
                )
            except WriteAdmissionDenied:
                trader.clear_sdk_write_admission()
                raise
            if trader.confirm_settlement_for_sdk_admission(capability, timeout=15.0) is not True:
                trader.clear_sdk_write_admission()
                raise CtpGatewayExecutionError("GATEWAY_EXECUTION_SETTLEMENT_UNCONFIRMED")
        preflight = _sha256(
            "|".join(("bt-ctp-gateway-preflight", cycle_id, symbol, exchange_id, str(generation)))
        )
        trader.arm_execution_for_sdk_admission(
            capability=capability,
            instrument_id=symbol,
            exchange_id=exchange_id,
            strategy_identity_sha256=strategy_identity,
            execution_cycle_id=cycle_id,
            preflight_sha256=preflight,
        )

    def basic_admission_factory(store: Any, scope: Any, submit_port: Any) -> Any:
        validator = ExecutionProviderValidatorV1(
            provider_id=scope.provider,
            validator_id="bt-ctp-gateway-v1",
            version="v1",
            dispatch_port=submit_port,
            instrument_map={symbol: symbol},
            quantity_steps={symbol: settings.quantity_step},
            price_ticks={symbol: settings.price_tick},
            supported_order_types=(OrderType.LIMIT,),
            supported_time_in_force=(TimeInForce.DAY,),
            supported_actions=tuple(
                (side, effect)
                for side in (Side.BUY, Side.SELL)
                for effect in (
                    PositionEffect.OPEN,
                    PositionEffect.CLOSE,
                    PositionEffect.CLOSE_TODAY,
                    PositionEffect.CLOSE_YESTERDAY,
                )
            ),
            supports_reduce_only=True,
        )
        return ExecutionBasicAdmissionGate(
            store,
            ExecutionAdmissionValidatorRegistry((validator,)),
            retain_claimed_authorizations=True,
        )

    def risk_admission_factory(context: Any) -> dict:
        from bt_api_risk import (
            AccountScope,
            DurableRiskAdmissionAdapter,
            DurableRiskGate,
            IntentAction,
            RiskIntent,
            RiskPolicy,
        )

        from bt_api_py.ctp_risk_dispatch_bridge import CtpRiskDispatchBridge
        from bt_api_py.service_execution_risk_cancel import RiskExecutionCancelAdmissionGate
        from bt_api_py.service_risk_order_journal import RiskOrderJournal

        risk_scope = AccountScope(
            context.execution_scope.provider,
            context.execution_scope.account_ref,
            context.execution_scope.environment,
        )
        journal = RiskOrderJournal(
            directory / "risk-order-journal.sqlite3", writer_id=settings.lease_owner_id + "-risk"
        )
        allocation = "bt-ctp-gateway-allocation-v1"
        risk_gate = DurableRiskGate(
            directory / "risk.sqlite3",
            RiskPolicy(
                "bt-ctp-gateway-risk-v1",
                settings.max_increase_notional,
                settings.max_increase_count,
                permit_ttl_seconds=60.0,
                notional_unit="CNY",
            ),
            execution_journal_authority=journal,
            recovery_close_source_authority=context.recovery_close_source_authority,
        )
        composition._closeables.append(risk_gate)
        risk_gate.set_strategy_allocation(
            risk_scope,
            context.execution_scope.strategy_id,
            allocation,
            max_notional=settings.max_increase_notional,
            notional_unit="CNY",
        )
        cancel_gate = RiskExecutionCancelAdmissionGate(
            risk_gate,
            journal,
            context.store,
            risk_scope=risk_scope,
            execution_scope=context.execution_scope,
            wire_scope_template=service_scope,
        )

        class _IntentMapper:
            wire_account_ref = context.execution_scope.account_ref

            def __call__(self, intent: Any) -> Any:
                return RiskIntent(
                    intent_id=intent.intent_id,
                    scope=risk_scope,
                    action=(
                        IntentAction.INCREASE
                        if intent.position_effect is PositionEffect.OPEN
                        else IntentAction.REDUCE
                    ),
                    notional=intent.quantity * intent.price,
                    payload_fingerprint=intent.fingerprint,
                    strategy_id=intent.scope.strategy_id,
                    allocation_version=allocation,
                    instrument=intent.instrument,
                    quantity=intent.quantity,
                    quantity_unit="contracts",
                    notional_unit="CNY",
                    position_side=intent.position_side or "NET",
                    position_bucket=intent.position_bucket or "ALL",
                )

        bridge = CtpRiskDispatchBridge(
            DurableRiskAdmissionAdapter(risk_gate, _IntentMapper()),
            journal,
            account_scope=risk_scope,
            exchange_name=exchange_id,
        )
        return {"order_gate": bridge, "cancel_gate": cancel_gate}

    composition.config = CtpExecutionRuntimeConfig(
        store_path=directory / "execution.sqlite3",
        execution_scope=execution_scope,
        service_scope=service_scope,
        lease_owner_id=settings.lease_owner_id,
        trader=CtpTraderConnectionConfig(
            front=settings.td_front,
            md_front=settings.md_front,
            broker_id=settings.broker_id,
            user_id=settings.user_id,
            password=settings.password,
            ctp_env_profile="config_front_pair",
            app_id=settings.app_id,
            auth_code=settings.auth_code,
        ),
        native_binding=CtpNativeDispatchBindingV1(
            account_ref=settings.account_ref,
            scope_key=execution_scope.key,
            investor_id=settings.user_id,
            intent_instrument=symbol,
            provider_instrument=symbol,
            exchange_id=exchange_id,
        ),
        prepare_trader=prepare_trader,
        basic_admission_factory=basic_admission_factory,
        risk_admission_factory=risk_admission_factory,
        startup_timeout_s=settings.startup_timeout_s,
        command_binding=command_binding,
    )
    return composition
