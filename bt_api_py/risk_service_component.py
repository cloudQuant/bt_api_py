"""ServiceHost built-in ``("risk", "order_service")`` of the independent Risk service (64.09).

The component maps authenticated wire calls on ``service.risk_order`` (strategy
and Execution principals) and ``service.risk_admin`` (operator principals) to
the pre-trade engine ``bt_api_risk.pretrade.PretradeEngine``. Who calls is taken
only from the Registry's authenticated context and the operator-installed
principal table; nothing in the request names the caller, its strategy or its
role. The order view is built from the canonical order payload Risk forwards
unchanged to the Gateway, and the configured instrument map.

This module never imports ``bt_api_risk`` at import time: the Risk process
hands it an engine object, and the engine's value types are imported lazily
(INV64-02 keeps ``bt_api_risk`` out of the strategy and Gateway processes).
Replies are claims of this service; they prove nothing about the venue.
"""

from __future__ import annotations

import hashlib
import json
import logging
import math
from collections import Counter
from collections.abc import Callable, Mapping
from copy import deepcopy
from datetime import UTC, datetime
from decimal import Decimal
from types import MappingProxyType
from typing import Any

from bt_api_base.gateway_direct_protocol import canonical_gateway_payload
from bt_api_base.market_protocol import MarketInstrumentKey
from bt_api_base.risk_service_protocol import (
    OFFSETS,
    RISK_ADMIN_OPERATIONS,
    RISK_ADMIN_PORT,
    RISK_ORDER_OPERATIONS,
    RISK_ORDER_V2_OPERATIONS,
    RISK_REFUSAL_CODES,
    RISK_SERVICE_PORT,
    RISK_SERVICE_SCHEMA,
    SIDES,
    RiskOrderResult,
    RiskOrderView,
    RiskParentView,
    RiskServiceProtocolError,
)
from bt_api_base.service_protocol import WireRole, WireScope

from bt_api_py.gateway_execution_stream_client import _detach_wire

SUPPORTED_OPERATIONS = frozenset(RISK_ORDER_OPERATIONS | RISK_ADMIN_OPERATIONS)
# Registry write operations: a strategy registering any of them holds the
# durable single-run strategy write claim of this Risk host.
ORDER_WRITE_OPERATIONS = frozenset({"risk.order.reserve", "risk.order.dispatch", "risk.order.release"})
OPTIONS_ROLE = "order_service"
_AUDIT_LIMIT = 1000
# The optional Risk Web adapter rejects replies above this many value nodes.
# Count the entire result, not only each record; no schema fields are removed.
_AUDIT_REPLY_NODE_LIMIT = 200_000


def validate_service_options(options: Mapping[str, Any], bind_ports: Any) -> Mapping[str, Any]:
    """The role takes no options beyond its name and binds exactly its two ports."""
    if (
        not isinstance(options, Mapping)
        or dict(options) != {"role": OPTIONS_ROLE}
        or type(bind_ports) not in (tuple, list)
        or len(bind_ports) != 2
        or set(bind_ports) != {RISK_SERVICE_PORT, RISK_ADMIN_PORT}
    ):
        raise ValueError("RISK_ORDER_SERVICE_OPTIONS_INVALID")
    return MappingProxyType({"role": OPTIONS_ROLE})


def _ref(value: object) -> bool:
    return (
        type(value) is str
        and bool(value)
        and value == value.strip()
        and value.isprintable()
        and len(value.encode("utf-8", errors="replace")) <= 256
    )


def _rejected(code: str) -> dict:
    return {"status": "REQUEST_REJECTED", "code": code}


def _audit_nodes_fit(value: Any) -> bool:
    """Count plain value nodes with bounded work and without copying wide lists."""
    remaining = _AUDIT_REPLY_NODE_LIMIT
    stack = [iter((value,))]
    while stack:
        try:
            item = next(stack[-1])
        except StopIteration:
            stack.pop()
            continue
        remaining -= 1
        if remaining < 0:
            return False
        if type(item) is dict:
            stack.append(iter(item.values()))
        elif type(item) is list:
            stack.append(iter(item))
    return True


def _audit_json_copy(value: Any, depth: int = 0) -> Any:
    """Copy only bounded-reader JSON values; unlike policy, finite floats are valid."""
    if depth > 16:
        raise ValueError("RISK_AUDIT_CORRUPT")
    if value is None or type(value) is bool:
        return value
    if type(value) is int:
        if not -(2**63) <= value <= 2**64 - 1:
            raise ValueError("RISK_AUDIT_CORRUPT")
        return value
    if type(value) is float:
        if not math.isfinite(value):
            raise ValueError("RISK_AUDIT_CORRUPT")
        return value
    if type(value) is str:
        value.encode("utf-8", errors="strict")
        return value
    if type(value) is list:
        return [_audit_json_copy(item, depth + 1) for item in value]
    if type(value) is dict:
        if any(type(key) is not str for key in value):
            raise ValueError("RISK_AUDIT_CORRUPT")
        for key in value:
            key.encode("utf-8", errors="strict")
        return {key: _audit_json_copy(item, depth + 1) for key, item in value.items()}
    raise ValueError("RISK_AUDIT_CORRUPT")


def json_safe(value: Any, depth: int = 0) -> Any:
    """Plain JSON values only: Decimals as strings, tuples as lists; no other types."""
    if depth > 16:
        raise ValueError("RISK_REPLY_TOO_DEEP")
    if value is None or type(value) in (bool, int, str):
        return value
    if type(value) is Decimal:
        text = format(value, "f")
        return text
    if isinstance(value, Mapping):
        return {str(key): json_safe(item, depth + 1) for key, item in value.items()}
    if type(value) in (list, tuple):
        return [json_safe(item, depth + 1) for item in value]
    raise ValueError("RISK_REPLY_NOT_JSON")


class RiskOrderService:
    """Owner-thread adapter: one engine, one principal table, one account binding."""

    def __init__(
        self,
        engine: Any,
        ledger: Any,
        *,
        account_scope: str,
        route: str,
        execution_domain_ref: str,
        principals: Mapping[str, Any],
        instrument_for: Callable[[object, object], MarketInstrumentKey | None],
        ready: Callable[[], bool],
        note_run: Callable[[str, str], None] | None = None,
        cancel_status: Callable[..., Mapping | None] | None = None,
        strict_admin_cancel: bool = False,
        parent_v2_execution_enabled: bool = False,
    ) -> None:
        if not all(_ref(x) for x in (account_scope, route, execution_domain_ref)):
            raise ValueError("RISK_ORDER_SERVICE_INVALID")
        if not isinstance(principals, Mapping) or not callable(instrument_for) or not callable(ready):
            raise ValueError("RISK_ORDER_SERVICE_INVALID")
        if type(strict_admin_cancel) is not bool or type(parent_v2_execution_enabled) is not bool:
            raise ValueError("RISK_ORDER_SERVICE_INVALID")
        self._engine, self._ledger = engine, ledger
        self._account, self._route, self._domain = account_scope, route, execution_domain_ref
        self._principals = MappingProxyType(dict(principals))
        self._instrument_for = instrument_for
        self._ready = ready
        self._note_run = note_run
        self._cancel_status = cancel_status
        # Explicitly opt in only when the Registry is configured to issue the
        # matching admission. This mode performs local acceptance only.
        self._strict_admin_cancel = strict_admin_cancel
        # Explicitly opt in for Execution parent-bound v2 child writes
        # (risk.order.reserve.v2 / risk.order.dispatch.v2); v1 stays fail-closed.
        self._parent_v2_execution_enabled = parent_v2_execution_enabled
        # Refusal counts since this process started, per represented strategy (risk.status).
        self._refusals: dict[str, Counter] = {}
        # Admin/operator refusals since this process started, per finite code.
        self._operator_refusals: Counter = Counter()

    # ------------------------------------------------------------------ identity

    def caller(self, context: Any, operation: str) -> tuple[Any, str | None]:
        """Return ``(CallerIdentity, None)`` or ``(None, code)`` from the authenticated context."""
        from bt_api_risk.pretrade import CallerIdentity

        try:
            identity, scope = context.identity, context.scope
            principal = self._principals.get(context.principal_ref)
        except Exception:
            return None, "risk_unauthorized"
        if (
            principal is None
            or type(scope) is not WireScope
            or identity.principal_ref != context.principal_ref
            or identity.delegate_ref is not None
            or identity.actor_ref != principal.actor_ref
            or scope.execution_domain_ref != self._domain
            or scope.account_ref != self._account
            or scope.account_refs
            or scope.manual_book_ref is not None
            or scope.portfolio_ref is not None
            or scope.group_id is not None
            or scope.leg_id is not None
            or operation not in principal.operations
        ):
            return None, "risk_unauthorized"
        role = principal.role
        if role == "operator":
            if (
                identity.role is not WireRole.APPLICATION_CLIENT
                or operation not in RISK_ADMIN_OPERATIONS
                or scope.strategy_ref is not None
                or scope.run_id is not None
            ):
                return None, "risk_unauthorized"
            strategies, run_id = frozenset(), None
        else:
            expected = WireRole.STRATEGY_CLIENT if role == "strategy" else WireRole.SERVICE
            if (
                identity.role is not expected
                or operation not in RISK_ORDER_OPERATIONS
                or scope.strategy_ref not in principal.strategy_refs
                or scope.run_id is None
                or (role == "strategy" and scope.strategy_ref != identity.actor_ref)
            ):
                return None, "risk_unauthorized"
            # The caller acts for the one strategy of its registration, never for another.
            strategies, run_id = frozenset({scope.strategy_ref}), scope.run_id
        try:
            return (
                CallerIdentity(
                    principal_ref=principal.principal_ref,
                    actor_ref=principal.actor_ref,
                    role=role,
                    strategy_refs=strategies,
                    run_id=run_id,
                    operations=frozenset(principal.operations),
                    cancel_account_allowed=getattr(principal, "cancel_account_allowed", False),
                    cancel_strategy_refs=getattr(principal, "cancel_strategy_refs", frozenset()),
                    cancel_instrument_refs=getattr(principal, "cancel_instrument_refs", frozenset()),
                ),
                None,
            )
        except Exception:
            return None, "risk_unauthorized"

    # ------------------------------------------------------------------ order view

    def build_view(
        self,
        caller: Any,
        request_key: str,
        payload: object,
        parent_ref: object,
        child_id: object,
    ) -> tuple[RiskOrderView | None, str | None, str | None]:
        """``(view, canonical_payload, None)`` or ``(None, None, code)``.

        The effect is never taken from the caller: only the explicit offset is
        carried, and the engine derives INCREASE/REDUCE from it (INV64-04).
        """
        from bt_api_py.gateway_order_contract import decode_gateway_order_request

        if type(payload) is not dict or not (
            (parent_ref is None and child_id is None) or (_ref(parent_ref) and _ref(child_id))
        ):
            return None, None, "risk_request_invalid"
        try:
            canonical = canonical_gateway_payload(payload)
            request = decode_gateway_order_request(payload, self._account)
        except Exception:
            return None, None, "risk_request_invalid"
        if request.idempotency_key != request_key or canonical_gateway_payload(
            request.to_dict()
        ) != canonical:
            return None, None, "risk_request_invalid"
        instrument = self._instrument_for(request.exchange_id, request.symbol)
        if type(instrument) is not MarketInstrumentKey:
            # No account binding for this contract: no limit can exist for it.
            return None, None, "risk_limit_unconfigured"
        try:
            order_type = str(request.order_type.value).upper()
            side = str(request.side.value).upper()
            offset = None if request.offset is None else str(request.offset).upper()
            position_side = (
                "NET" if request.position_side is None else str(request.position_side).upper()
            )
            (strategy_ref,) = tuple(caller.strategy_refs)
            view = RiskOrderView(
                account_scope=self._account,
                route=self._route,
                strategy_ref=strategy_ref,
                origin="execution" if caller.role == "execution" else "strategy",
                request_key=request_key,
                instrument=instrument,
                side=side,
                offset=offset,
                position_side=position_side,
                quantity=request.quantity,
                quantity_unit=request.quantity_unit,
                order_type=order_type,
                limit_price=request.price if order_type != "MARKET" else None,
                time_in_force=None if request.time_in_force is None else str(request.time_in_force),
                parent_ref=parent_ref,  # type: ignore[arg-type]
                child_id=child_id,  # type: ignore[arg-type]
            )
        except (RiskServiceProtocolError, TypeError, ValueError, AttributeError):
            return None, None, "risk_request_invalid"
        return view, canonical, None

    # ------------------------------------------------------------------ dispatch

    def _audit_read_rejected(self, caller: Any) -> bool:
        try:
            record = getattr(self._engine, "audit_read_rejection", None)
            if not callable(record):
                return False
            record(caller)
            return True
        except Exception:
            return False

    def handle(self, operation: str, *, context: Any, arguments: Mapping[str, Any]) -> Mapping[str, Any]:
        if operation not in SUPPORTED_OPERATIONS:
            return _rejected("risk_request_invalid")
        if not isinstance(arguments, Mapping):
            return self._refuse_admin_arguments(operation, context)
        raw_arguments = arguments
        try:
            # The Registry hands frozen wire containers; work on bounded plain copies.
            arguments = _detach_wire(arguments)
            if type(arguments) is not dict:
                raise ValueError
        except Exception:
            return self._refuse_admin_arguments(operation, context)
        caller, code = self.caller(context, operation)
        if operation in RISK_ADMIN_OPERATIONS:
            if caller is None:
                refusal = code or "risk_unauthorized"
                self._count_operator_refusal(refusal)
                if operation == "risk.cancel.request":
                    self._record_admin_refusal(
                        operation, None, refusal, context,
                        scope_kind=arguments.get("scope_kind"),
                        scope_value=arguments.get("scope_value"),
                    )
                else:
                    self._record_admin_refusal(operation, None, refusal, context)
                return _rejected(refusal)
            try:
                result = self._admin(operation, caller, arguments, context=context)
            except Exception:
                if operation != "risk.audit.read":
                    raise
                result = _rejected("risk_audit_unavailable")
            if operation == "risk.audit.read" and result.get("status") != "OBSERVED":
                if not self._audit_read_rejected(caller):
                    self._record_admin_refusal(
                        operation, caller, "risk_audit_unavailable", context)
                    return _rejected("risk_audit_unavailable")
            return result
        if operation == "risk.status":
            if caller is None or arguments:
                return _rejected(code or "risk_request_invalid")
            return self._status(caller)
        if operation.startswith("risk.parent."):
            return self._parent(operation, caller, code, arguments)
        if operation in RISK_ORDER_V2_OPERATIONS:
            return self._order_v2(operation, caller, code, context, raw_arguments, arguments)
        return self._order(operation, caller, code, arguments)

    def _order_v2(
        self,
        operation: str,
        caller: Any,
        code: str | None,
        context: Any,
        raw_arguments: Mapping[str, Any],
        arguments: dict,
    ) -> Mapping:
        """Parent-bound Execution v2 child reserve/dispatch via RiskParentChildCoordinator."""
        from dataclasses import replace as _dc_replace

        from bt_api_risk.pretrade import RiskRequestError
        from bt_api_risk.pretrade.v2_child_coordinator import RiskParentChildCoordinator

        from bt_api_py.service_registry import ServiceCallContext, ServiceDispatchAdmission

        key = arguments.get("request_key")
        if not _ref(key):
            return _rejected("risk_request_invalid")
        admission = getattr(context, "dispatch_admission", None)
        try:
            if (
                not self._parent_v2_execution_enabled
                or caller is None
                or caller.role != "execution"
                or type(context) is not ServiceCallContext
                or type(admission) is not ServiceDispatchAdmission
                or not admission.matches_context(context)
                or not admission.matches_operation(operation)
                or not context._is_registry_wire_arguments(raw_arguments)
                or not admission.matches(raw_arguments)
            ):
                if caller is None:
                    return RiskOrderResult(key, "DENIED", code=code or "risk_unauthorized").to_mapping()
                return RiskOrderResult(key, "UNKNOWN", code="risk_permit_mismatch").to_mapping()

            fields = {
                "risk.order.reserve.v2": ({"request_key", "payload", "parent_ref", "child_id"}, set()),
                "risk.order.dispatch.v2": (
                    {"request_key", "permit_id", "reservation_id", "view_digest"},
                    {"market_ref"},
                ),
            }[operation]
            if not fields[0] <= set(arguments) <= fields[0] | fields[1]:
                return RiskOrderResult(key, "DENIED", code="risk_request_invalid").to_mapping()

            mapped_op = (
                "risk.order.reserve"
                if operation == "risk.order.reserve.v2"
                else "risk.order.dispatch"
            )
            exec_caller = _dc_replace(
                caller,
                operations=frozenset(caller.operations | {mapped_op}),
            )
            coordinator = RiskParentChildCoordinator(self._engine)
            (strategy,) = tuple(caller.strategy_refs)
            try:
                if operation == "risk.order.reserve.v2":
                    parent_ref = arguments["parent_ref"]
                    child_id = arguments["child_id"]
                    if not _ref(parent_ref) or not _ref(child_id):
                        result = RiskOrderResult(key, "DENIED", code="risk_request_invalid")
                    else:
                        view, canonical, refusal = self.build_view(
                            exec_caller, key, arguments["payload"], parent_ref, child_id
                        )
                        if refusal is not None or view is None or canonical is None:
                            result = RiskOrderResult(
                                key, "DENIED", code=refusal or "risk_request_invalid"
                            )
                        else:
                            if self._note_run is not None:
                                self._note_run(strategy, caller.run_id)
                            result = coordinator.reserve_child(
                                exec_caller, parent_ref, view, canonical, caller.run_id
                            )
                else:
                    permit_id = arguments.get("permit_id")
                    reservation_id = arguments.get("reservation_id")
                    view_digest = arguments.get("view_digest")
                    if (
                        not _ref(permit_id)
                        or not _ref(reservation_id)
                        or type(view_digest) is not str
                        or len(view_digest) != 64
                        or any(ch not in "0123456789abcdef" for ch in view_digest)
                    ):
                        result = RiskOrderResult(key, "DENIED", code="risk_permit_mismatch")
                    else:
                        if self._note_run is not None:
                            self._note_run(strategy, caller.run_id)
                        result = coordinator.dispatch_child(
                            exec_caller,
                            key,
                            permit_id,
                            reservation_id,
                            view_digest,
                        )
            except RiskRequestError as error:
                result = RiskOrderResult(
                    key,
                    "DENIED",
                    code=error.code if error.code in RISK_REFUSAL_CODES else "risk_request_invalid",
                )
            if type(result) is not RiskOrderResult:
                raise TypeError("RISK_ENGINE_RESULT_INVALID")
            logging.getLogger(__name__).info(
                "risk %s key=%s outcome=%s code=%s gateway_code=%s gateway_state=%s",
                operation,
                key,
                result.outcome,
                result.code,
                result.gateway_code,
                result.gateway_state,
            )
            self._count(caller, result)
            return result.to_mapping()
        finally:
            if type(admission) is ServiceDispatchAdmission:
                try:
                    admission.close()
                except Exception:
                    pass

    def _count(self, caller: Any, result: RiskOrderResult) -> None:
        if caller is None or result.outcome != "DENIED" or result.code not in RISK_REFUSAL_CODES:
            return
        for strategy in caller.strategy_refs:
            self._refusals.setdefault(strategy, Counter())[result.code] += 1

    # ------------------------------------------------------------- admin audit

    def _count_operator_refusal(self, code: Any) -> None:
        if type(code) is str and code:
            self._operator_refusals[code] += 1

    def _record_admin_refusal(
        self, operation: str, caller: Any, code: Any, context: Any,
        *, scope_kind: Any = None, scope_value: Any = None,
    ) -> None:
        """Append one engine-owned audit row; the refusal reply is already fixed."""
        try:
            record = getattr(self._engine, "record_admin_refusal", None)
            if not callable(record):
                return
            subject = caller
            if subject is None:
                subject = getattr(context, "identity", None)
            record(operation, subject, code, scope_kind=scope_kind, scope_value=scope_value)
        except Exception:
            # A storage failure cannot upgrade a refusal into a success.
            return

    def _refuse_admin(
        self, operation: str, caller: Any, code: str, context: Any,
        *, scope_kind: Any = None, scope_value: Any = None,
    ) -> Mapping:
        self._count_operator_refusal(code)
        if operation != "risk.audit.read":
            self._record_admin_refusal(
                operation, caller, code, context,
                scope_kind=scope_kind, scope_value=scope_value,
            )
        return _rejected(code)

    def _refuse_admin_arguments(self, operation: str, context: Any) -> Mapping:
        """Refuse malformed admin arguments while preserving the audit.read writer."""
        if operation not in RISK_ADMIN_OPERATIONS:
            return _rejected("risk_request_invalid")
        caller, _ = self.caller(context, operation)
        if operation == "risk.audit.read":
            if caller is not None and self._audit_read_rejected(caller):
                self._count_operator_refusal("risk_request_invalid")
                return _rejected("risk_request_invalid")
            reply = self._refuse_admin(operation, caller, "risk_request_invalid", context)
            self._record_admin_refusal(operation, caller, "risk_request_invalid", context)
            return reply
        return self._refuse_admin(operation, caller, "risk_request_invalid", context)

    def _order(self, operation: str, caller: Any, code: str | None, arguments: dict) -> Mapping:
        from bt_api_risk.pretrade import RiskOrderLookupKind, RiskRequestError

        fields = {
            "risk.order.reserve": ({"request_key", "payload", "parent_ref", "child_id"}, set()),
            "risk.order.dispatch": (
                {"request_key", "permit_id", "reservation_id", "view_digest"},
                {"market_ref"},
            ),
            "risk.order.release": ({"request_key", "permit_id"}, set()),
            "risk.order.status": ({"request_key"}, set()),
        }[operation]
        key = arguments.get("request_key")
        if not _ref(key):
            return _rejected("risk_request_invalid")
        if caller is not None and operation in ORDER_WRITE_OPERATIONS:
            # V1 has no Registry admission bound to an atomic Gateway first
            # creation and one-shot dispatch proof.  A valid key may already
            # name an older order, even when this request's other fields are
            # malformed; neither a local refusal nor retry proves its outcome.
            return RiskOrderResult(key, "UNKNOWN", code="risk_permit_mismatch").to_mapping()
        if not fields[0] <= set(arguments) <= fields[0] | fields[1]:
            return RiskOrderResult(key, "DENIED", code="risk_request_invalid").to_mapping()
        if caller is None:
            result = RiskOrderResult(key, "DENIED", code=code or "risk_unauthorized")
            return result.to_mapping()
        engine = self._engine
        try:
            if operation == "risk.order.reserve":
                view, canonical, refusal = self.build_view(
                    caller, key, arguments["payload"], arguments["parent_ref"], arguments["child_id"]
                )
                (strategy,) = tuple(caller.strategy_refs)
                lookup = self._lookup_order(
                    caller, operation, strategy, key, view=view, canonical_payload=canonical
                )
                if lookup.kind is RiskOrderLookupKind.RESULT:
                    result = lookup.result
                    # Retain the specific input refusal only when the lookup
                    # has classified the request as a definite new refusal.
                    if (refusal is not None and result.outcome == "DENIED"
                            and result.code == "risk_request_invalid"):
                        result = RiskOrderResult(key, "DENIED", code=refusal)
                elif (lookup.kind is not RiskOrderLookupKind.NEW
                      or refusal is not None or caller.role == "execution"):
                    # An unexpected category cannot open an Execution write
                    # path or turn invalid input into proof of no old order.
                    result = RiskOrderResult(key, "UNKNOWN", code="risk_permit_mismatch")
                else:
                    result = engine.reserve(caller, view, canonical, caller.run_id)
            elif operation == "risk.order.dispatch":
                # A caller's market reference names the caller's own subscription; the
                # Gateway gate accepts only the Risk principal's own (G64-10), which the
                # engine takes from its facts. The argument is accepted and not used.
                result = self._permit_integrity_preflight(caller, operation, key, arguments)
                if result is None:
                    (strategy,) = tuple(caller.strategy_refs)
                    if self._note_run is not None:
                        self._note_run(strategy, caller.run_id)
                    result = engine.dispatch(caller, key, arguments["permit_id"],
                                             arguments["reservation_id"], arguments["view_digest"])
            elif operation == "risk.order.release":
                result = self._permit_integrity_preflight(caller, operation, key, arguments)
                if result is None:
                    (strategy,) = tuple(caller.strategy_refs)
                    if self._note_run is not None:
                        self._note_run(strategy, caller.run_id)
                    result = engine.release(caller, key, arguments["permit_id"])
            else:
                (strategy,) = tuple(caller.strategy_refs)
                lookup = self._lookup_order(caller, operation, strategy, key)
                result = (lookup.result if lookup.kind is RiskOrderLookupKind.RESULT
                          else RiskOrderResult(key, "UNKNOWN", code="risk_permit_mismatch"))
        except RiskRequestError as error:
            result = RiskOrderResult(
                key,
                "DENIED",
                code=error.code if error.code in RISK_REFUSAL_CODES else "risk_request_invalid",
            )
        if type(result) is not RiskOrderResult:
            raise TypeError("RISK_ENGINE_RESULT_INVALID")
        self._count(caller, result)
        return result.to_mapping()

    def _lookup_order(self, caller: Any, operation: str, strategy: str, key: str, **kwargs: Any) -> Any:
        """Use Risk's bounded, non-authorizing snapshot; never decode its ledger here."""
        from bt_api_risk.pretrade import RiskOrderLookup, RiskOrderLookupKind

        try:
            lookup = self._engine.lookup_order(caller, operation, strategy, key, **kwargs)
            if (type(lookup) is RiskOrderLookup
                    and (lookup.kind is not RiskOrderLookupKind.RESULT
                         or (type(lookup.result) is RiskOrderResult and lookup.result.request_key == key))):
                return lookup
        except Exception:
            return RiskOrderLookup(
                RiskOrderLookupKind.RESULT, RiskOrderResult(key, "UNKNOWN", code="risk_permit_mismatch")
            )
        return RiskOrderLookup(
            RiskOrderLookupKind.RESULT, RiskOrderResult(key, "UNKNOWN", code="risk_permit_mismatch")
        )

    def _permit_integrity_preflight(
        self, caller: Any, operation: str, key: str, arguments: dict
    ) -> RiskOrderResult | None:
        """Check bounded local consistency before reading or acting on a permit.

        READY is only a preliminary check: the engine must recheck inside the
        operation.  A failed or unavailable check cannot authorize a write or
        disclose fields from a potentially corrupt or foreign permit.
        """
        permit_id = arguments.get("permit_id")
        if not _ref(permit_id):
            return RiskOrderResult(key, "DENIED", code="risk_permit_mismatch")
        if operation == "risk.order.dispatch":
            digest = arguments.get("view_digest")
            if (
                not _ref(arguments.get("reservation_id"))
                or type(digest) is not str
                or len(digest) != 64
                or any(ch not in "0123456789abcdef" for ch in digest)
            ):
                return RiskOrderResult(key, "DENIED", code="risk_permit_mismatch")
        try:
            checked = self._engine.permit_integrity_status(
                caller, operation, key, permit_id,
                reservation_id=arguments.get("reservation_id"),
                view_digest=arguments.get("view_digest"),
            )
            if type(checked) is str and checked == "READY":
                return None
            if type(checked) is str and checked == "MISMATCH":
                return RiskOrderResult(key, "DENIED", code="risk_permit_mismatch")
            if (
                type(checked) is RiskOrderResult
                and caller.role == "execution"
                and checked.request_key == key
                and checked.outcome == "UNKNOWN"
                and checked.code == "risk_parent_unavailable"
            ):
                return checked
        except Exception:
            return RiskOrderResult(key, "UNKNOWN", code="risk_permit_mismatch")
        return RiskOrderResult(key, "UNKNOWN", code="risk_permit_mismatch")

    def _parent(self, operation: str, caller: Any, code: str | None, arguments: dict) -> Mapping:
        from bt_api_risk.pretrade import RiskRequestError

        if caller is None:
            return _rejected(code or "risk_unauthorized")
        engine = self._engine
        try:
            if operation == "risk.parent.reserve":
                if set(arguments) != {"parent"} or type(arguments["parent"]) is not dict:
                    return _rejected("risk_request_invalid")
                try:
                    parent = RiskParentView.from_mapping(arguments["parent"])
                except RiskServiceProtocolError:
                    return _rejected("risk_request_invalid")
                if (caller.role != "execution" or parent.strategy_ref not in caller.strategy_refs
                    or parent.account_scope != self._account or parent.route != self._route):
                    return _rejected("risk_unauthorized")
                result = engine.parent_reserve(caller, parent)
            else:
                if set(arguments) != {"parent_ref"} or not _ref(arguments["parent_ref"]):
                    return _rejected("risk_request_invalid")
                if operation == "risk.parent.release":
                    if caller.role != "execution":
                        return _rejected("risk_unauthorized")
                    result = engine.parent_release(caller, arguments["parent_ref"])
                else:
                    result = engine.parent_status(caller, arguments["parent_ref"])
        except RiskRequestError as error:
            return _rejected(error.code)
        return {
            "schema": RISK_SERVICE_SCHEMA,
            "schema_version": 1,
            **json_safe(
                {
                    name: getattr(result, name)
                    for name in (
                        "parent_ref",
                        "outcome",
                        "state",
                        "code",
                        "total_quantity",
                        "allocated_quantity",
                        "unallocated_quantity",
                        "policy_version",
                    )
                }
            ),
        }

    def _unsupported_parent(self, parent_ref: str) -> Any:
        """The disabled write path cannot establish any legacy parent outcome."""
        from bt_api_risk.pretrade.engine import RiskParentResult

        return RiskParentResult(parent_ref, "UNKNOWN", code="risk_parent_unavailable")

    def _status(self, caller: Any) -> Mapping:
        summary = self._engine.status_summary()
        try:
            ready = self._ready() is True
        except Exception:
            ready = False
        counts: Counter = Counter()
        for strategy in caller.strategy_refs:
            counts.update(self._refusals.get(strategy, Counter()))
        version = summary.get("policy_version")
        return {
            "schema": RISK_SERVICE_SCHEMA,
            "schema_version": 1,
            "available": bool(summary.get("available")) and ready,
            "policy_version": None if version is None else str(version),
            "refusal_counts": {code: int(count) for code, count in sorted(counts.items())},
        }

    # ------------------------------------------------------------------ admin

    def _admit_cancel_locally(self, caller: Any, context: Any, arguments: dict) -> Mapping[str, Any]:
        """Consume an exact Registry admission around one Risk-only commit.

        This is a trusted in-process composition, not a hostile-code sandbox.
        A committed local claim does not authorize Gateway I/O or a later retry.
        """
        from bt_api_risk.pretrade.engine import PretradeEngine, RiskAdminError
        from bt_api_risk.pretrade.ledger import PretradeLedger

        from bt_api_py.risk_service_config import RiskPrincipal
        from bt_api_py.service_registry import RiskAdminCancelAdmission, ServiceCallContext

        operation = "risk.cancel.request"
        operation_id, kind, value = (arguments["operation_id"], arguments["scope_kind"],
                                     arguments["scope_value"])
        try:
            admission = context.dispatch_admission
            principal_table = self._principals
            principal = principal_table.get(context.principal_ref)
            engine, ledger = self._engine, self._ledger
            # A replaced transaction manager is not used even for the refusal
            # audit: nothing about this request may touch untrusted storage.
            ledger_trusted = (
                type(ledger) is PretradeLedger
                and getattr(ledger.transaction, "__func__", None) is PretradeLedger.transaction
            )
            if (type(context) is not ServiceCallContext or type(admission) is not RiskAdminCancelAdmission
                    or type(principal) is not RiskPrincipal
                    or type(engine) is not PretradeEngine or not ledger_trusted
                    or engine._ledger is not ledger
                    or getattr(engine._accept_operator_cancel_local, "__func__", None)
                    is not PretradeEngine._accept_operator_cancel_local
                    or not admission.matches_context(context) or not admission.matches_operation(operation)
                    or not admission.matches(arguments) or not caller.allows_cancel_scope(kind, value)):
                self._count_operator_refusal("risk_unauthorized")
                if ledger_trusted:
                    self._record_admin_refusal(
                        operation, caller, "risk_unauthorized", context,
                        scope_kind=kind, scope_value=value,
                    )
                return _rejected("risk_unauthorized")
        except Exception:
            self._count_operator_refusal("risk_unauthorized")
            return _rejected("risk_unauthorized")

        try:
            with admission.scope() as recheck:
                def before_commit() -> None:
                    if (self._principals is not principal_table
                            or principal_table.get(context.principal_ref) is not principal
                            or self._engine is not engine or self._ledger is not ledger
                            or engine._ledger is not ledger
                            or not admission.matches_context(context)
                            or not admission.matches_operation(operation)
                            or not admission.matches(arguments)
                            or not admission.matches_scope_recheck(recheck)):
                        raise RuntimeError("RISK_ADMIN_CANCEL_AUTHORITY_CHANGED")
                    current, code = self.caller(context, operation)
                    if code is not None or current != caller or not current.allows_cancel_scope(kind, value):
                        raise RuntimeError("RISK_ADMIN_CANCEL_AUTHORITY_CHANGED")
                    recheck()
                    if (self._principals is not principal_table
                            or principal_table.get(context.principal_ref) is not principal
                            or self._engine is not engine or self._ledger is not ledger
                            or engine._ledger is not ledger
                            or not admission.matches_context(context)
                            or not admission.matches_scope_recheck(recheck)):
                        raise RuntimeError("RISK_ADMIN_CANCEL_AUTHORITY_CHANGED")

                before_commit()
                engine._accept_operator_cancel_local(
                    caller, operation_id, kind, value, _before_commit=before_commit,
                )
        except RiskAdminError as error:
            if error.code == "risk_cancel_local_scope_unsupported":
                # Raised before the ledger transaction opens: a definite refusal
                # with no committed state and no later send.
                return self._refuse_admin(
                    operation, caller, error.code, context,
                    scope_kind=kind, scope_value=value,
                )
            # A transaction or scope close may have committed before failing;
            # never send or retry based on the missing response.
            return {"status": "OBSERVED", "operation_id": operation_id, "state": "UNKNOWN"}
        except Exception:
            # A transaction or scope close may have committed before failing;
            # never send or retry based on the missing response.
            return {"status": "OBSERVED", "operation_id": operation_id, "state": "UNKNOWN"}
        return {"status": "OBSERVED", "operation_id": operation_id, "state": "UNKNOWN"}

    def _admin(self, operation: str, caller: Any, arguments: dict, *, context: Any) -> Mapping:
        from bt_api_risk.pretrade import RiskAdminError

        expected = {
            "risk.admin.status": set(),
            "risk.policy.read": set(),
            "risk.policy.publish": {"expected_version", "document", "reason", "exchange_rules_text"},
            "risk.latch.list": {"active_only"},
            "risk.latch.release": {"latch_id", "expected_version", "reason"},
            "risk.unknown.list": set(),
            "risk.counts.export": {"trading_day", "format"},
            "risk.audit.read": {"after", "limit", "through", "snapshot"},
            "risk.cancel.request": {"operation_id", "scope_kind", "scope_value"},
            "risk.cancel.status": {"operation_id"},
        }[operation]
        if operation == "risk.cancel.status":
            fields_valid = expected <= set(arguments) <= expected | {"after", "limit"}
        else:
            fields_valid = set(arguments) == expected
        if not fields_valid:
            return self._refuse_admin(operation, caller, "risk_request_invalid", context)
        engine = self._engine
        try:
            if operation == "risk.admin.status":
                summary = engine.status_summary()
                summary = dict(summary) if isinstance(summary, Mapping) else {}
                # Process-local operator refusal counts; never order data.
                summary["operator_refusal_counts"] = {
                    code: int(count) for code, count in sorted(self._operator_refusals.items())
                }
                return {"status": "OBSERVED", "summary": json_safe(summary)}
            if operation == "risk.policy.read":
                reply = self._policy_read()
                if reply.get("status") == "REQUEST_REJECTED":
                    return self._refuse_admin(operation, caller, reply["code"], context)
                return reply
            if operation == "risk.policy.publish":
                if (
                    type(arguments["expected_version"]) is not int
                    or type(arguments["document"]) is not dict
                    or type(arguments["reason"]) is not str
                    or (
                        arguments["exchange_rules_text"] is not None
                        and type(arguments["exchange_rules_text"]) is not str
                    )
                ):
                    return self._refuse_admin(operation, caller, "risk_request_invalid", context)
                published = engine.publish_policy(
                    caller,
                    arguments["document"],
                    arguments["expected_version"],
                    arguments["reason"],
                    exchange_rules_text=arguments["exchange_rules_text"],
                )
                return {
                    "status": "OBSERVED",
                    "version": published.version,
                    "policy_id": published.policy_id,
                    "digest": published.digest,
                    "classification": json_safe(published.classification),
                }
            if operation == "risk.latch.list":
                if type(arguments["active_only"]) is not bool:
                    return self._refuse_admin(operation, caller, "risk_request_invalid", context)
                rows = engine.list_latches(active_only=arguments["active_only"])
                return {"status": "OBSERVED", "latches": [_latch(row) for row in rows]}
            if operation == "risk.latch.release":
                if (
                    not _ref(arguments["latch_id"])
                    or type(arguments["expected_version"]) is not int
                    or type(arguments["reason"]) is not str
                ):
                    return self._refuse_admin(operation, caller, "risk_request_invalid", context)
                row = engine.release_latch(
                    caller, arguments["latch_id"], arguments["expected_version"], arguments["reason"]
                )
                return {"status": "OBSERVED", "latch": _latch(row)}
            if operation == "risk.unknown.list":
                # An old Risk deployment has only an unbounded reader. It must
                # be upgraded together with this endpoint; never fall back.
                try:
                    result_fits = context.result_fits
                    read_bounded = getattr(engine, "list_unknown_bounded", None)
                except Exception:
                    return self._refuse_admin(operation, caller, "risk_unknown_list_unavailable", context)
                if not callable(result_fits) or not callable(read_bounded):
                    return self._refuse_admin(operation, caller, "risk_unknown_list_unavailable", context)
                try:
                    rows = read_bounded()
                except RiskAdminError as error:
                    code = error.code if error.code in {
                        "risk_unknown_list_too_large", "risk_unknown_list_corrupt",
                        "risk_unknown_list_unavailable",
                    } else "risk_unknown_list_unavailable"
                    return self._refuse_admin(operation, caller, code, context)
                except Exception:
                    return self._refuse_admin(operation, caller, "risk_unknown_list_unavailable", context)
                if type(rows) is not list:
                    return self._refuse_admin(operation, caller, "risk_unknown_list_unavailable", context)
                if len(rows) > 10_000:
                    return self._refuse_admin(operation, caller, "risk_unknown_list_too_large", context)
                try:
                    permits = []
                    permit_ids = set()
                    request_keys = set()
                    remaining = 900 * 1024
                    for row in rows:
                        permit = _unknown(row)
                        permit_id = permit["permit_id"]
                        request_identity = (permit["strategy_ref"], permit["request_key"])
                        if permit_id in permit_ids or request_identity in request_keys:
                            raise ValueError("duplicate unknown identity")
                        permit_ids.add(permit_id)
                        request_keys.add(request_identity)
                        for value in permit.values():
                            if value is None:
                                continue
                            if type(value) is not str:
                                raise ValueError("invalid unknown field")
                            # Avoid encoding a single already-over-budget string.
                            if len(value) > remaining:
                                raise OverflowError("unknown list too large")
                            remaining -= len(value.encode("utf-8", errors="strict"))
                            if remaining < 0:
                                raise OverflowError("unknown list too large")
                        permits.append(permit)
                except OverflowError:
                    return self._refuse_admin(operation, caller, "risk_unknown_list_too_large", context)
                except Exception:
                    return self._refuse_admin(operation, caller, "risk_unknown_list_corrupt", context)
                response = {"status": "OBSERVED", "permits": permits}
                if not _audit_nodes_fit(response):
                    return self._refuse_admin(operation, caller, "risk_unknown_list_too_large", context)
                # Registry checks its 900 KiB result limit; only a configured
                # frame checker additionally proves the complete envelope.
                # Never give a callback the result that will be returned.
                checks = []
                for _ in range(2):
                    snapshot = deepcopy(response)
                    try:
                        accepted = result_fits(snapshot)
                        if type(accepted) is not bool or snapshot != response:
                            return self._refuse_admin(operation, caller, "risk_unknown_list_unavailable", context)
                    except Exception:
                        return self._refuse_admin(operation, caller, "risk_unknown_list_unavailable", context)
                    checks.append(accepted)
                if checks[0] != checks[1]:
                    return self._refuse_admin(operation, caller, "risk_unknown_list_unavailable", context)
                return response if checks[0] else self._refuse_admin(operation, caller, "risk_unknown_list_too_large", context)
            if operation == "risk.counts.export":
                content = engine.export_counts(arguments["trading_day"], arguments["format"])
                return {"status": "OBSERVED", "format": arguments["format"], "content": content}
            if operation == "risk.audit.read":
                after, limit = arguments["after"], arguments["limit"]
                through, snapshot = arguments["through"], arguments["snapshot"]
                if (
                    type(after) is not int
                    or not 0 <= after <= 2**63 - 1
                    or type(limit) is not int
                    or not 1 <= limit <= _AUDIT_LIMIT
                    or ((through is None) != (snapshot is None))
                    or (through is not None and (
                        type(through) is not int or not after <= through <= 2**63 - 1
                        or type(snapshot) is not int or not 1 <= snapshot <= 2**63 - 1
                    ))
                ):
                    return self._refuse_admin(operation, caller, "risk_request_invalid", context)
                # The Registry enforces its result budget here. A configured
                # ServiceHost frame checker also measures the sealed envelope;
                # a bare Registry without one cannot prove a transport bound.
                try:
                    result_fits = context.result_fits
                except Exception:
                    return self._refuse_admin(operation, caller, "risk_audit_size_unavailable", context)
                if not callable(result_fits):
                    return self._refuse_admin(operation, caller, "risk_audit_size_unavailable", context)
                # The old reader may allocate unbounded audit details before
                # any transport-size check; never silently fall back to it.
                try:
                    read_bounded = getattr(engine, "read_audit_page", None)
                except Exception:
                    return self._refuse_admin(operation, caller, "risk_audit_size_unavailable", context)
                if not callable(read_bounded):
                    return self._refuse_admin(operation, caller, "risk_audit_size_unavailable", context)
                raw_page = read_bounded(caller, after, limit, through, snapshot)
                if (type(raw_page) is not dict
                        or set(raw_page) != {"records", "next_after", "through", "snapshot", "has_more"}
                        or type(raw_page["through"]) is not int
                        or not after <= raw_page["through"] <= 2**63 - 1
                        or type(raw_page["snapshot"]) is not int
                        or not 1 <= raw_page["snapshot"] <= 2**63 - 1
                        or type(raw_page["has_more"]) is not bool
                        or (through is not None and (raw_page["through"] != through
                            or raw_page["snapshot"] != snapshot))):
                    return self._refuse_admin(operation, caller, "risk_audit_size_unavailable", context)
                try:
                    records = _audit_json_copy(raw_page["records"])
                except (ValueError, TypeError, RecursionError, UnicodeError, OverflowError):
                    return self._refuse_admin(operation, caller, "risk_audit_corrupt", context)
                if type(records) is not list or len(records) > limit:
                    return self._refuse_admin(operation, caller, "risk_audit_size_unavailable", context)
                previous = after
                for record in records:
                    if (type(record) is not dict or type(record.get("seq")) is not int
                            or not previous < record["seq"] <= raw_page["through"]):
                        return self._refuse_admin(operation, caller, "risk_audit_size_unavailable", context)
                    previous = record["seq"]
                expected_more = len(records) == limit and previous < raw_page["through"]
                expected_after = previous if expected_more else raw_page["through"]
                if (type(raw_page["next_after"]) is not int or raw_page["next_after"] != expected_after
                        or raw_page["has_more"] != expected_more):
                    return self._refuse_admin(operation, caller, "risk_audit_size_unavailable", context)

                def candidate(count: int) -> dict:
                    truncated = count < len(records)
                    return {"status": "OBSERVED", "records": records[:count],
                            "next_after": (records[count - 1]["seq"] if count else after)
                            if truncated else raw_page["next_after"],
                            "through": raw_page["through"], "snapshot": raw_page["snapshot"],
                            "has_more": True if truncated else raw_page["has_more"]}

                def fits(count: int) -> bool | None:
                    response = candidate(count)
                    if not _audit_nodes_fit(response):
                        return False
                    # Registry callbacks are trusted code, but a malformed
                    # callback must not change or falsely certify our page.
                    snapshot = deepcopy(response)
                    try:
                        accepted = result_fits(snapshot)
                    except Exception:
                        return None
                    if type(accepted) is not bool or snapshot != response:
                        return None
                    return accepted

                total = len(records)
                full = fits(total)
                if full is None:
                    return self._refuse_admin(operation, caller, "risk_audit_size_unavailable", context)
                if full:
                    return candidate(total)
                if total == 0:
                    return self._refuse_admin(operation, caller, "risk_audit_size_unavailable", context)
                first = fits(1)
                if first is None:
                    return self._refuse_admin(operation, caller, "risk_audit_size_unavailable", context)
                if not first:
                    # The caller's cursor remains unchanged, so this record is
                    # never silently skipped or reported as a completed page.
                    return self._refuse_admin(operation, caller, "risk_audit_record_too_large", context)
                # Existing clients interpret len(records) < limit as EOF.
                # Never return a truncated success: let the caller retry the
                # same cursor with a smaller limit instead.
                return self._refuse_admin(operation, caller, "risk_audit_page_too_large", context)
            if operation == "risk.cancel.request":
                kind, value = arguments["scope_kind"], arguments["scope_value"]
                if (
                    not _ref(arguments["operation_id"])
                    or kind not in ("account", "strategy", "instrument")
                    or type(value) is not str
                    or (kind == "account" and value != "")
                    or (kind == "strategy" and not _ref(value))
                    or (kind == "instrument" and (not _ref(value) or value.count(":") != 1))
                ):
                    return self._refuse_admin(
                        operation, caller, "risk_request_invalid", context,
                        scope_kind=kind, scope_value=value,
                    )
                if getattr(self, "_strict_admin_cancel", False) is True:
                    return self._admit_cancel_locally(caller, context, arguments)
                row = engine.request_cancel(caller, arguments["operation_id"], kind, value)
                return {
                    "status": "OBSERVED",
                    "operation_id": row["operation_id"],
                    "state": row["state"],
                }
            # risk.cancel.status
            operation_id = arguments["operation_id"]
            after, limit = arguments.get("after", ""), arguments.get("limit", 256)
            if (not _ref(operation_id) or type(after) is not str or (after and not _ref(after))
                    or type(limit) is not int or not 1 <= limit <= 256):
                return self._refuse_admin(operation, caller, "risk_request_invalid", context)
            if not (caller.cancel_account_allowed or caller.cancel_strategy_refs
                    or caller.cancel_instrument_refs):
                return {"status": "NOT_FOUND", "operation_id": operation_id}
            with self._ledger.transaction() as txn:
                row = txn.cancel_intent(operation_id)
            if row is None:
                return {"status": "NOT_FOUND", "operation_id": operation_id}
            try:
                source = caller.cancel_intent_source()
                local_source = "operator-local-v3:" + source.removeprefix("operator-v2:")
                if (row["source"] not in (source, local_source, "operator:" + caller.principal_ref)
                        or row["latch_id"] is not None or row["wait_cause_id"] is not None
                        or not caller.allows_cancel_scope(row["scope_kind"], row["scope_value"])):
                    return {"status": "NOT_FOUND", "operation_id": operation_id}
            except (KeyError, IndexError, TypeError):
                return {"status": "NOT_FOUND", "operation_id": operation_id}
            # Neither historic intent rows nor the current adapter bind the
            # original Gateway journal and generation, for any scope.
            return {"status": "OBSERVED", "operation_id": operation_id,
                    "state": "UNKNOWN", "gateway": None}
        except RiskAdminError as error:
            if getattr(error, "audited", False):
                # The engine already appended exactly one refusal record.
                self._count_operator_refusal(error.code)
                return _rejected(error.code)
            if operation == "risk.cancel.request":
                return self._refuse_admin(
                    operation, caller, error.code, context,
                    scope_kind=arguments.get("scope_kind"),
                    scope_value=arguments.get("scope_value"),
                )
            return self._refuse_admin(operation, caller, error.code, context)

    def _policy_read(self) -> Mapping:
        summary = self._engine.status_summary()
        with self._ledger.transaction() as txn:
            row = txn.latest_policy()
        if (
            row is None
            or summary.get("policy_version") is None
            or int(row["version"]) != summary["policy_version"]
            or row["digest"] != summary.get("policy_digest")
        ):
            return _rejected("risk_policy_unavailable")
        rules_text = row["exchange_rules_text"]
        return {
            "status": "OBSERVED",
            "version": int(row["version"]),
            "policy_id": row["policy_id"],
            "digest": row["digest"],
            "document": json.loads(row["document_json"]),
            "exchange_rules_digest": None
            if rules_text is None
            else hashlib.sha256(rules_text.encode("utf-8")).hexdigest(),
        }


def iso_utc(ns: object) -> str | None:
    """Integer nanoseconds since the epoch as ISO-8601 UTC (microseconds), or None."""
    if type(ns) is not int or ns < 0:
        return None
    seconds, rest = divmod(ns, 1_000_000_000)
    moment = datetime.fromtimestamp(seconds, tz=UTC).replace(microsecond=rest // 1000)
    return moment.isoformat(timespec="microseconds").replace("+00:00", "Z")


def _latch(row: Mapping) -> dict:
    return {
        "latch_id": row["latch_id"],
        "control": row["control"],
        "scope_kind": row["scope_kind"],
        "scope_value": row["scope_value"],
        "reason": row["reason"],
        "trading_day": row["trading_day"],
        "version": row["row_version"],
        "active": bool(row["active"]),
        "released_by": row["released_by"],
        "released_at": iso_utc(row["released_ns"]),
    }


def _unknown(row: Mapping) -> dict:
    for name in ("request_key", "strategy_ref", "permit_id", "instrument_id"):
        ref = row[name]
        if type(ref) is not str or len(ref) > 256 or not _ref(ref):
            raise ValueError("invalid unknown identity")
    if type(row["side"]) is not str or row["side"] not in SIDES:
        raise ValueError("invalid unknown side")
    if type(row["offset"]) is not str or row["offset"] not in OFFSETS:
        raise ValueError("invalid unknown offset")
    created_ns = row["created_ns"]
    if type(created_ns) is not int or created_ns < 0:
        raise ValueError("invalid unknown timestamp")
    try:
        since = iso_utc(created_ns)
    except (ValueError, OverflowError, OSError):
        raise ValueError("invalid unknown timestamp") from None
    quantity = row["quantity"]
    if type(quantity) is not Decimal:
        raise ValueError("invalid quantity")
    if type(quantity) is Decimal:
        if not quantity.is_finite():
            raise ValueError("non-finite quantity")
        sign, digits, exponent = quantity.as_tuple()
        # Decimal('0E-999999') has one digit but formats to almost 1 MiB.
        # Bound the fixed-point representation before calling format().
        if quantity.is_zero() and exponent >= 0:
            length = 1
        elif exponent >= 0:
            length = len(digits) + exponent
        elif len(digits) + exponent > 0:
            length = len(digits) + 1
        else:
            length = 2 - exponent
        if length + sign > 900 * 1024:
            raise OverflowError("unknown quantity too large")
        if quantity <= 0:
            raise ValueError("nonpositive quantity")
    return {
        "request_key": row["request_key"],
        "strategy_ref": row["strategy_ref"],
        "permit_id": row["permit_id"],
        "reservation_id": row["reservation_id"],
        "state": "UNKNOWN",
        "instrument": row["instrument_id"],
        "side": row["side"],
        "offset": row["offset"],
        "quantity": json_safe(quantity),
        "since": since,
    }


__all__ = [
    "ORDER_WRITE_OPERATIONS",
    "RiskOrderService",
    "SUPPORTED_OPERATIONS",
    "iso_utc",
    "json_safe",
    "validate_service_options",
]
