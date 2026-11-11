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
from collections import Counter
from collections.abc import Callable, Mapping
from datetime import UTC, datetime
from decimal import Decimal
from types import MappingProxyType
from typing import Any

from bt_api_base.gateway_direct_protocol import canonical_gateway_payload
from bt_api_base.market_protocol import MarketInstrumentKey
from bt_api_base.risk_service_protocol import (
    RISK_ADMIN_OPERATIONS,
    RISK_ADMIN_PORT,
    RISK_ORDER_OPERATIONS,
    RISK_REFUSAL_CODES,
    RISK_SERVICE_PORT,
    RISK_SERVICE_SCHEMA,
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
_AUDIT_LIMIT = 500


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
        cancel_status: Callable[[str], Mapping | None] | None = None,
    ) -> None:
        if not all(_ref(x) for x in (account_scope, route, execution_domain_ref)):
            raise ValueError("RISK_ORDER_SERVICE_INVALID")
        if not isinstance(principals, Mapping) or not callable(instrument_for) or not callable(ready):
            raise ValueError("RISK_ORDER_SERVICE_INVALID")
        self._engine, self._ledger = engine, ledger
        self._account, self._route, self._domain = account_scope, route, execution_domain_ref
        self._principals = MappingProxyType(dict(principals))
        self._instrument_for = instrument_for
        self._ready = ready
        self._note_run = note_run
        self._cancel_status = cancel_status
        # Refusal counts since this process started, per represented strategy (risk.status).
        self._refusals: dict[str, Counter] = {}

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

    def handle(self, operation: str, *, context: Any, arguments: Mapping[str, Any]) -> Mapping[str, Any]:
        if operation not in SUPPORTED_OPERATIONS or not isinstance(arguments, Mapping):
            return _rejected("risk_request_invalid")
        try:
            # The Registry hands frozen wire containers; work on bounded plain copies.
            arguments = _detach_wire(arguments)
            if type(arguments) is not dict:
                raise ValueError
        except Exception:
            return _rejected("risk_request_invalid")
        caller, code = self.caller(context, operation)
        if operation in RISK_ADMIN_OPERATIONS:
            if caller is None:
                return _rejected(code or "risk_unauthorized")
            return self._admin(operation, caller, arguments)
        if operation == "risk.status":
            if caller is None or arguments:
                return _rejected(code or "risk_request_invalid")
            return self._status(caller)
        if operation.startswith("risk.parent."):
            return self._parent(operation, caller, code, arguments)
        return self._order(operation, caller, code, arguments)

    def _count(self, caller: Any, result: RiskOrderResult) -> None:
        if caller is None or result.outcome != "DENIED" or result.code not in RISK_REFUSAL_CODES:
            return
        for strategy in caller.strategy_refs:
            self._refusals.setdefault(strategy, Counter())[result.code] += 1

    def _order(self, operation: str, caller: Any, code: str | None, arguments: dict) -> Mapping:
        from bt_api_risk.pretrade import RiskRequestError

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
        if not fields[0] <= set(arguments) <= fields[0] | fields[1]:
            return RiskOrderResult(key, "DENIED", code="risk_request_invalid").to_mapping()
        if caller is None:
            result = RiskOrderResult(key, "DENIED", code=code or "risk_unauthorized")
            return result.to_mapping()
        if self._note_run is not None:
            (strategy,) = tuple(caller.strategy_refs)
            self._note_run(strategy, caller.run_id)
        engine = self._engine
        try:
            if operation == "risk.order.reserve":
                view, canonical, refusal = self.build_view(
                    caller, key, arguments["payload"], arguments["parent_ref"], arguments["child_id"]
                )
                if refusal is not None:
                    result = RiskOrderResult(key, "DENIED", code=refusal)
                else:
                    result = engine.reserve(caller, view, canonical, caller.run_id)
            elif operation == "risk.order.dispatch":
                # A caller's market reference names the caller's own subscription; the
                # Gateway gate accepts only the Risk principal's own (G64-10), which the
                # engine takes from its facts. The argument is accepted and not used.
                result = engine.dispatch(
                    caller,
                    key,
                    arguments["permit_id"],
                    arguments["reservation_id"],
                    arguments["view_digest"],
                )
            elif operation == "risk.order.release":
                result = engine.release(caller, key, arguments["permit_id"])
            else:
                (strategy,) = tuple(caller.strategy_refs)
                result = engine.status(caller, key, strategy_ref=strategy)
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
                result = engine.parent_reserve(caller, parent)
            else:
                if set(arguments) != {"parent_ref"} or not _ref(arguments["parent_ref"]):
                    return _rejected("risk_request_invalid")
                method = (
                    engine.parent_release
                    if operation == "risk.parent.release"
                    else engine.parent_status
                )
                result = method(caller, arguments["parent_ref"])
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

    def _admin(self, operation: str, caller: Any, arguments: dict) -> Mapping:
        from bt_api_risk.pretrade import RiskAdminError

        expected = {
            "risk.admin.status": set(),
            "risk.policy.read": set(),
            "risk.policy.publish": {"expected_version", "document", "reason", "exchange_rules_text"},
            "risk.latch.list": {"active_only"},
            "risk.latch.release": {"latch_id", "expected_version", "reason"},
            "risk.unknown.list": set(),
            "risk.counts.export": {"trading_day", "format"},
            "risk.audit.read": {"after", "limit"},
            "risk.cancel.request": {"operation_id", "scope_kind", "scope_value"},
            "risk.cancel.status": {"operation_id"},
        }[operation]
        if set(arguments) != expected:
            return _rejected("risk_request_invalid")
        engine = self._engine
        try:
            if operation == "risk.admin.status":
                return {"status": "OBSERVED", "summary": json_safe(engine.status_summary())}
            if operation == "risk.policy.read":
                return self._policy_read()
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
                    return _rejected("risk_request_invalid")
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
                    return _rejected("risk_request_invalid")
                rows = engine.list_latches(active_only=arguments["active_only"])
                return {"status": "OBSERVED", "latches": [_latch(row) for row in rows]}
            if operation == "risk.latch.release":
                if (
                    not _ref(arguments["latch_id"])
                    or type(arguments["expected_version"]) is not int
                    or type(arguments["reason"]) is not str
                ):
                    return _rejected("risk_request_invalid")
                row = engine.release_latch(
                    caller, arguments["latch_id"], arguments["expected_version"], arguments["reason"]
                )
                return {"status": "OBSERVED", "latch": _latch(row)}
            if operation == "risk.unknown.list":
                return {
                    "status": "OBSERVED",
                    "permits": [_unknown(row) for row in engine.list_unknown()],
                }
            if operation == "risk.counts.export":
                content = engine.export_counts(arguments["trading_day"], arguments["format"])
                return {"status": "OBSERVED", "format": arguments["format"], "content": content}
            if operation == "risk.audit.read":
                after, limit = arguments["after"], arguments["limit"]
                if (
                    type(after) is not int
                    or after < 0
                    or type(limit) is not int
                    or not 1 <= limit <= _AUDIT_LIMIT
                ):
                    return _rejected("risk_request_invalid")
                records = json_safe(engine.read_audit(after, limit))
                next_after = records[-1]["seq"] if records else after
                return {"status": "OBSERVED", "records": records, "next_after": next_after}
            if operation == "risk.cancel.request":
                kind, value = arguments["scope_kind"], arguments["scope_value"]
                if (
                    not _ref(arguments["operation_id"])
                    or kind not in ("account", "instrument")
                    or type(value) is not str
                    or (kind == "account" and value != "")
                    or (kind == "instrument" and (not _ref(value) or value.count(":") != 1))
                ):
                    return _rejected("risk_request_invalid")
                row = engine.request_cancel(caller, arguments["operation_id"], kind, value)
                return {
                    "status": "OBSERVED",
                    "operation_id": row["operation_id"],
                    "state": row["state"],
                }
            # risk.cancel.status
            operation_id = arguments["operation_id"]
            if not _ref(operation_id):
                return _rejected("risk_request_invalid")
            with self._ledger.transaction() as txn:
                row = txn.cancel_intent(operation_id)
            if row is None:
                return {"status": "NOT_FOUND", "operation_id": operation_id}
            gateway = None
            if self._cancel_status is not None:
                try:
                    gateway = self._cancel_status(operation_id)
                    gateway = None if gateway is None else json_safe(gateway)
                except Exception:
                    gateway = None
            return {
                "status": "OBSERVED",
                "operation_id": operation_id,
                "state": row["state"],
                "gateway": gateway,
            }
        except RiskAdminError as error:
            return _rejected(error.code)

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
    return {
        "request_key": row["request_key"],
        "strategy_ref": row["strategy_ref"],
        "permit_id": row["permit_id"],
        "reservation_id": row["reservation_id"],
        "state": "UNKNOWN",
        "instrument": row["instrument_id"],
        "side": row["side"],
        "offset": row["offset"],
        "quantity": json_safe(row["quantity"]),
        "since": iso_utc(row["created_ns"]),
    }


__all__ = [
    "ORDER_WRITE_OPERATIONS",
    "RiskOrderService",
    "SUPPORTED_OPERATIONS",
    "iso_utc",
    "json_safe",
    "validate_service_options",
]
