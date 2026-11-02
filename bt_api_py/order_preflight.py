"""SDK order-rule and economics preflight and decision bindings.

The host provides lifecycle, route and order-projection callbacks. Decisions
and their retained object identities do not grant provider write authority.
"""

from __future__ import annotations

import threading
from collections.abc import Mapping
from typing import Any, Dict, Protocol

__all__ = ("OrderPreflightCoordinator",)


class _OrderPreflightContext(Protocol):
    """Explicit host inputs used by the SDK preflight owner."""

    @property
    def api(self) -> Any: ...

    @property
    def sdk_mode(self) -> bool: ...

    @property
    def service_read_only(self) -> bool: ...

    @property
    def contract_metadata(self) -> Mapping[str, Any]: ...

    def ensure_api_ready(self) -> Any: ...

    def assert_write_route_identity(
        self, *, expected_api: Any, require_live_route: bool
    ) -> None: ...

    def order_to_payload(self, order: Any) -> Mapping[str, Any]: ...

    def resolve_sdk_route_name(self, api: Any, exchange: Any) -> str: ...

    def sdk_exchange(self, symbol: Any) -> Any: ...

    def account_session_snapshot(self, route: str) -> Any: ...

    def get_market_instrument_key(self, symbol: Any) -> Any: ...

    def sanitize_exception(self, error: BaseException) -> None: ...


class OrderPreflightCoordinator:
    """Own SDK order-rule/economics evaluation and decision bindings.

    An explicit host context supplies API readiness, route identity,
    order payloads, SDK-validated account-session snapshots,
    configured instrument lookup, and safe exception observation. This
    coordinator owns projection of those facts into the neutral rules request
    and the associated decision/evidence lifecycle. SDK contracts remain
    lazily imported by the methods that need them.
    """

    def __init__(
        self,
        context: _OrderPreflightContext,
        *,
        store_error_type,
        missing_dependency_error_type,
    ):
        self._context = context
        self._store_error_type = store_error_type
        self._missing_dependency_error_type = missing_dependency_error_type
        self._decision_lock = threading.RLock()
        self._rule_decisions: Dict[int, Dict[str, Any]] = {}
        self._economics_decisions: Dict[int, Dict[str, Any]] = {}

    def clear(self) -> None:
        """Revoke all preflight decisions when the Store retires its SDK session."""

        with self._decision_lock:
            self._rule_decisions.clear()
            self._economics_decisions.clear()

    def _build_execution_rule_request(
        self, order, *, api, market_instrument_key=None, current_position=None
    ):
        """Project one host order into the neutral SDK rules request."""
        from bt_api_py.facade_protocol import (
            AccountSessionSnapshot,
            ExecutionRuleRequest,
            MarketInstrumentKey,
        )

        payload = self._context.order_to_payload(order)
        symbol = payload.get("symbol")
        route = self._context.resolve_sdk_route_name(api, self._context.sdk_exchange(symbol))
        configured_key = self._context.get_market_instrument_key(symbol)
        if market_instrument_key is not None and (
            type(market_instrument_key) is not MarketInstrumentKey
            or market_instrument_key != configured_key
        ):
            raise self._store_error_type(
                "order instrument key differs from configured feed identity"
            )
        instrument = configured_key
        if type(instrument) is not MarketInstrumentKey:
            raise self._store_error_type(
                "an exact MarketInstrumentKey is required for order rules"
            )

        position = current_position
        if not isinstance(position, Mapping):
            raise self._store_error_type("BT position context is unavailable for order rules")
        position_mode = position.get("position_mode")
        if type(position_mode) is not str or position_mode not in {"net", "dual_side"}:
            raise self._store_error_type("explicit position mode is required for order rules")

        if api is not self._context.api:
            raise self._store_error_type(
                "account-session selector changed the selected route"
            )
        session = self._context.account_session_snapshot(route)
        if (
            api is not self._context.api
            or type(session) is not AccountSessionSnapshot
            or session.exchange_name != route
            or session.available is not True
            or type(session.generation) is not str
            or not session.generation
        ):
            raise self._store_error_type(
                "current SDK account-session generation is unavailable"
            )

        from bt_api_py.execution_rule_preparation import build_execution_rule_request

        request = build_execution_rule_request(
            payload, instrument, session, position_mode, error_type=self._store_error_type,
        )
        return payload, route, session, request

    def _account_session_generation_matches(self, api, route, generation):
        try:
            from bt_api_py.facade_protocol import AccountSessionSnapshot

            if api is not self._context.api:
                return False
            session = self._context.account_session_snapshot(route)
            return (
                api is self._context.api
                and type(session) is AccountSessionSnapshot
                and session.exchange_name == route
                and session.available is True
                and session.generation == generation
            )
        except Exception:
            return False

    def _prepare_execution_rules(self, api, route, instrument, session, hedge_class):
        try:
            from bt_api_py.execution_rule_preparation import prepare_execution_rules
        except ImportError as exc:
            raise self._missing_dependency_error_type(
                "SDK execution-rules preparation contracts are required"
            ) from exc
        return prepare_execution_rules(
            api, route, instrument, session, hedge_class,
            sanitize_exception=self._context.sanitize_exception,
            missing_dependency_error_type=self._missing_dependency_error_type,
        )

    def evaluate_order_rules(
        self,
        order,
        *,
        market_instrument_key=None,
        current_position=None,
    ):
        """Ask the SDK's route-bound rules port for one typed short-lived decision."""
        try:
            from bt_api_py.facade_protocol import (
                ExecutionRuleDecision,
                ExecutionRulesPort,
                ExecutionRulesPreparationPort,
            )
            from bt_api_py.facade_protocol import ExecutionRulesFacade
        except ImportError as exc:
            raise self._missing_dependency_error_type(
                "SDK execution-rules contracts are required"
            ) from exc
        if not self._context.sdk_mode or self._context.service_read_only:
            raise self._store_error_type("execution rules require the unified SDK facade")
        api = self._context.ensure_api_ready()
        self._context.assert_write_route_identity(expected_api=api, require_live_route=True)
        payload, route, session, request = self._build_execution_rule_request(
            order,
            api=api,
            market_instrument_key=market_instrument_key,
            current_position=current_position,
        )
        rules_port, rules_snapshot, preparation_error = self._prepare_execution_rules(
            api,
            route,
            request.instrument,
            session,
            request.hedge_class,
        )
        quantity_unit = payload.get("quantity_unit")
        if quantity_unit in (None, ""):
            metadata = self._context.contract_metadata.get(payload.get("symbol"), {})
            if isinstance(metadata, Mapping):
                quantity_unit = metadata.get("quantity_unit")
        from bt_api_py.execution_rule_preparation import evaluate_rules_with_position_evidence

        request, decision, position_port, position_evidence = evaluate_rules_with_position_evidence(
            api, route, session, request, quantity_unit, rules_port, rules_snapshot,
            preparation_error,
            session_generation_matches=self._account_session_generation_matches,
            sanitize_exception=self._context.sanitize_exception,
            missing_dependency_error_type=self._missing_dependency_error_type,
        )
        with self._decision_lock:
            entry = {
                "order": order,
                "api": api,
                "route": route,
                "payload": payload,
                "instrument": request.instrument,
                "session_generation": session.generation,
                "request": decision.request,
                "request_input": request,
                "decision": decision,
                "port": rules_port,
                "rules_snapshot": rules_snapshot,
                "position_evidence_port": position_port,
                "position_evidence": position_evidence,
                "position_quantity_unit": quantity_unit,
            }
            self._rule_decisions[id(decision)] = entry
            while len(self._rule_decisions) > 4096:
                self._rule_decisions.pop(next(iter(self._rule_decisions)))
        return decision

    def verify_order_rules(self, order, decision) -> bool:
        """Verify Store object binding, route, session generation, and expiry."""
        try:
            from bt_api_py.facade_protocol import (
                ExecutionRuleDecision,
                AccountSessionSnapshot,
            )

            if type(decision) is not ExecutionRuleDecision:
                return False
            with self._decision_lock:
                entry = self._rule_decisions.get(id(decision))
            if (
                entry is None
                or entry.get("decision") is not decision
                or entry.get("order") is not order
                or entry.get("api") is not self._context.api
                or entry.get("request") is not decision.request
                or entry.get("request_input") is not decision.request
                or decision.request.instrument is not entry.get("instrument")
            ):
                return False
            ExecutionRuleDecision.__post_init__(decision)
            self._context.assert_write_route_identity(
                expected_api=entry["api"], require_live_route=True
            )
            payload = self._context.order_to_payload(order)
            route = self._context.resolve_sdk_route_name(
                self._context.api, self._context.sdk_exchange(payload.get("symbol"))
            )
            if route != entry.get("route") or payload != entry.get("payload"):
                return False
            if self._context.get_market_instrument_key(payload["symbol"]) is not entry.get("instrument"):
                return False
            if self._context.api is not entry["api"]:
                return False
            snapshot = self._context.account_session_snapshot(route)
            if (
                self._context.api is not entry["api"]
                or type(snapshot) is not AccountSessionSnapshot
                or snapshot.exchange_name != route
                or snapshot.available is not True
                or snapshot.generation != entry.get("session_generation")
                or decision.request.session_generation != snapshot.generation
            ):
                return False
            from bt_api_py.execution_rule_preparation import verify_execution_rule_evidence

            return verify_execution_rule_evidence(
                entry["api"], route, decision, snapshot.generation,
                rules_port=entry.get("port"),
                rules_snapshot=entry.get("rules_snapshot"),
                position_evidence=entry.get("position_evidence"),
                position_port=entry.get("position_evidence_port"),
                position_quantity_unit=entry.get("position_quantity_unit"),
            )
        except Exception:
            return False

    def evaluate_order_economics(self, order, rules_decision):
        """Ask the SDK's account-economics capability for same-request evidence."""
        try:
            from bt_api_py.facade_protocol import (
                ExecutionEconomicsDecision,
                ExecutionEconomicsPort,
                ExecutionEconomicsRequest,
            )
            from bt_api_py.facade_protocol import ExecutionRuleDecision
            from bt_api_py.facade_protocol import ExecutionEconomicsFacade
        except ImportError as exc:
            raise self._missing_dependency_error_type(
                "SDK execution-economics contracts are required"
            ) from exc
        if type(rules_decision) is not ExecutionRuleDecision:
            raise self._store_error_type("an exact SDK execution-rules decision is required")
        if not self.verify_order_rules(order, rules_decision) or rules_decision.status != "accepted":
            raise self._store_error_type("a current accepted order-rules decision is required")
        with self._decision_lock:
            rules_entry = self._rule_decisions.get(id(rules_decision))
        if rules_entry is None:
            raise self._store_error_type("order-rules decision is not bound to this Store")
        payload = self._context.order_to_payload(order)
        quantity_unit = payload.get("quantity_unit")
        if quantity_unit in (None, ""):
            metadata = self._context.contract_metadata.get(payload.get("symbol"), {})
            if isinstance(metadata, Mapping):
                quantity_unit = metadata.get("quantity_unit")
        if type(quantity_unit) is not str or not quantity_unit or quantity_unit != quantity_unit.strip():
            raise self._store_error_type("an explicit order quantity unit is required for economics")
        from bt_api_py.execution_economics_evaluation import (
            build_execution_economics_request,
            evaluate_execution_economics,
        )

        request = build_execution_economics_request(rules_decision, quantity_unit)
        api = self._context.api
        route = rules_entry["route"]
        port, decision = evaluate_execution_economics(api, route, request)
        with self._decision_lock:
            self._economics_decisions[id(decision)] = {
                "order": order,
                "api": api,
                "route": route,
                "request": decision.request,
                "request_input": request,
                "decision": decision,
                "port": port,
                "rules_decision": rules_decision,
            }
            while len(self._economics_decisions) > 4096:
                self._economics_decisions.pop(
                    next(iter(self._economics_decisions))
                )
        return decision

    def verify_order_economics(self, order, decision) -> bool:
        """Verify exact request/order/rule binding and the current evidence lease."""
        try:
            from bt_api_py.facade_protocol import ExecutionEconomicsDecision

            if type(decision) is not ExecutionEconomicsDecision:
                return False
            with self._decision_lock:
                entry = self._economics_decisions.get(id(decision))
            if (
                entry is None
                or entry.get("decision") is not decision
                or entry.get("order") is not order
                or entry.get("api") is not self._context.api
                or entry.get("request") is not decision.request
                or entry.get("request_input") != decision.request
                or entry.get("rules_decision") is not decision.request.rule_decision
                or not self.verify_order_rules(order, entry.get("rules_decision"))
            ):
                return False
            ExecutionEconomicsDecision.__post_init__(decision)
            self._context.assert_write_route_identity(
                expected_api=entry["api"], require_live_route=True
            )
            from bt_api_py.execution_economics_evaluation import (
                execution_economics_decision_is_current,
            )

            return execution_economics_decision_is_current(decision)
        except Exception:
            return False

