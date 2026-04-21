"""Risk manager used by the test-suite and the lightweight risk workflow."""

from __future__ import annotations

import threading
import time
from typing import TYPE_CHECKING, Any

from bt_api_base.event_bus import EventBus
from bt_api_base.exceptions import BtApiError
from bt_api_base.logging_factory import get_logger

from ..containers.risk_events import EventStatus, RiskEvent, RiskEventType, RiskLevel
from .limits_manager import LimitsManager
from .policy_engine import PolicyEngine
from .risk_assessor import RiskAssessor
from .risk_calculator import RiskCalculator

if TYPE_CHECKING:
    from collections.abc import Callable

    from ..containers.risk_metrics import RiskMetrics

__all__ = ["RiskManager", "RiskManagerError"]


class RiskManagerError(BtApiError):
    """Risk manager level exception."""


class RiskManager:
    """Minimal but consistent risk manager implementation."""

    def __init__(
        self, event_bus: EventBus | None = None, config: dict[str, Any] | None = None
    ) -> None:
        self.logger = get_logger("risk_manager")
        self.event_bus = event_bus or EventBus()
        self.config = config or {}

        self.risk_assessor = RiskAssessor(config=self.config.get("assessor", {}))
        self.risk_calculator = RiskCalculator(config=self.config.get("calculator", {}))
        self.limits_manager = LimitsManager(config=self.config.get("limits", {}))
        self.policy_engine = PolicyEngine(config=self.config.get("policy", {}))

        self.risk_metrics_cache: dict[str, RiskMetrics] = {}
        self.active_events: dict[str, RiskEvent] = {}
        self.event_history: list[RiskEvent] = []
        self.risk_callbacks: list[Callable[[RiskMetrics], None]] = []
        self.event_callbacks: list[Callable[[RiskEvent], None]] = []

        self.is_monitoring = False
        self.monitoring_thread: threading.Thread | None = None
        self.last_update_time = 0
        self._lock = threading.RLock()

        self.performance_metrics = {
            "events_processed": 0,
            "risk_assessments": 0,
            "violations_detected": 0,
            "average_processing_time_ms": 0.0,
        }

        self._setup_event_listeners()

    def _setup_event_listeners(self) -> None:
        """Register basic no-op event handlers."""
        self.event_bus.on("TradeEvent", self._on_trade_event)
        self.event_bus.on("OrderEvent", self._on_order_event)
        self.event_bus.on("PositionEvent", self._on_position_event)
        self.event_bus.on("BalanceEvent", self._on_balance_event)
        self.event_bus.on("MarketDataEvent", self._on_market_data_event)
        self.event_bus.on("SystemEvent", self._on_system_event)
        self.event_bus.on("ErrorEvent", self._on_error_event)

    async def start_monitoring(self) -> None:
        """Start the lightweight background monitoring loop."""
        with self._lock:
            if self.is_monitoring:
                return
            self.is_monitoring = True
            self.monitoring_thread = threading.Thread(
                target=self._monitoring_loop,
                daemon=True,
                name="RiskManager-Monitoring",
            )
            self.monitoring_thread.start()

    async def stop_monitoring(self) -> None:
        """Stop the lightweight background monitoring loop."""
        with self._lock:
            if not self.is_monitoring:
                return
            self.is_monitoring = False
            monitoring_thread = self.monitoring_thread

        if monitoring_thread and monitoring_thread.is_alive():
            monitoring_thread.join(timeout=2.0)

    def _monitoring_loop(self) -> None:
        """Run a small periodic maintenance loop."""
        interval = float(self.config.get("monitoring_interval", 1.0))
        while self.is_monitoring:
            start = time.time()
            try:
                self.last_update_time = int(start)
                self._update_performance_metrics((time.time() - start) * 1000)
                time.sleep(interval)
            except Exception as exc:  # pragma: no cover - defensive fallback
                self.logger.error(f"Error in monitoring loop: {exc}")
                time.sleep(min(interval, 1.0))

    def assess_risk(
        self,
        exchange_name: str,
        account_id: str,
        position_data: dict[str, Any] | None = None,
        market_data: dict[str, Any] | None = None,
    ) -> RiskMetrics:
        """Assess risk and cache the resulting metrics."""
        cache_key = f"{exchange_name}:{account_id}"
        with self._lock:
            cached_metrics = self.risk_metrics_cache.get(cache_key)
        if cached_metrics is not None and position_data is None and market_data is None:
            return cached_metrics

        try:
            if position_data is None:
                position_data = {
                    "positions": [],
                    "portfolio_value": 0,
                    "total_exposure": 0,
                    "leverage": 1.0,
                }
            if market_data is None:
                market_data = {
                    "price_history": [100.0] * 120,
                    "market_returns": [0.0] * 120,
                    "asset_returns": {},
                    "volatility": 0.0,
                    "bid_ask_spread": 0.0,
                    "market_depth": 0.0,
                    "liquidity_score": 1.0,
                }

            account_data = {
                "account_id": account_id,
                "exchange_name": exchange_name,
                "balance": 0,
                "used_credit": 0,
                "total_credit": 0,
                "kyc_status": "VERIFIED",
                "aml_flags": [],
            }

            risk_metrics = self.risk_calculator.calculate_risk_metrics(
                exchange_name=exchange_name,
                account_id=account_id,
                account_data=account_data,
                position_data=position_data,
                market_data=market_data,
            )

            assessment = self.risk_assessor.assess_risk(risk_metrics)
            risk_metrics.overall_risk_score = assessment.score
            risk_metrics.risk_level = assessment.level.value

            with self._lock:
                self.risk_metrics_cache[cache_key] = risk_metrics
                self.performance_metrics["risk_assessments"] += 1
                self.last_update_time = int(time.time())

            for callback in list(self.risk_callbacks):
                try:
                    callback(risk_metrics)
                except Exception as exc:  # pragma: no cover - callback isolation
                    self.logger.error(f"Error in risk callback: {exc}")

            return risk_metrics
        except Exception as exc:
            self.logger.error(f"Error assessing risk: {exc}")
            raise RiskManagerError(f"Risk assessment failed: {exc}") from exc

    def create_risk_event(
        self,
        event_type: RiskEventType,
        risk_level: RiskLevel,
        title: str,
        description: str,
        exchange_name: str = "",
        account_id: str = "",
        **kwargs,
    ) -> RiskEvent:
        """Create, store, and dispatch a risk event."""
        risk_event = RiskEvent(
            {
                "event_type": event_type.value,
                "risk_level": risk_level.value,
                "event_status": EventStatus.NEW.value,
                "title": title,
                "description": description,
                "exchange_name": exchange_name,
                "account_id": account_id,
                **kwargs,
            }
        )
        risk_event.event_type = risk_event.event_type.value  # type: ignore[assignment]
        risk_event.risk_level = risk_event.risk_level.value  # type: ignore[assignment]
        risk_event.event_status = risk_event.event_status.value  # type: ignore[assignment]

        with self._lock:
            self.active_events[risk_event.event_id] = risk_event
            self.event_history.append(risk_event)
            self.performance_metrics["events_processed"] += 1
            if risk_level in {RiskLevel.HIGH, RiskLevel.CRITICAL}:
                self.performance_metrics["violations_detected"] += 1

        self._handle_risk_event(risk_event)

        for callback in list(self.event_callbacks):
            try:
                callback(risk_event)
            except Exception as exc:  # pragma: no cover - callback isolation
                self.logger.error(f"Error in event callback: {exc}")

        return risk_event

    def check_order_risk(
        self,
        exchange_name: str,
        account_id: str,
        order_data: dict[str, Any],
        current_metrics: RiskMetrics | None = None,
    ) -> dict[str, Any]:
        """Run a lightweight order risk decision."""
        start = time.time()

        try:
            risk_metrics = current_metrics
            if risk_metrics is None:
                risk_metrics = self.risk_metrics_cache.get(f"{exchange_name}:{account_id}")

            if risk_metrics is None:
                risk_level = "LOW"
                risk_score = 0.0
            else:
                risk_level = str(risk_metrics.risk_level)
                risk_score = float(risk_metrics.overall_risk_score)

            result: dict[str, Any] = {
                "approved": True,
                "risk_level": risk_level,
                "risk_score": risk_score,
                "warnings": [],
                "restrictions": [],
                "mitigation_required": False,
                "detailed_checks": [],
                "triggered_rules": [],
                "actions_executed": 0,
                "evaluation_time_ms": 0.0,
                "timestamp": int(time.time()),
            }

            order_size = float(order_data.get("size", 0)) * float(order_data.get("price", 0))
            if order_size > 500000:
                result["approved"] = False
                result["mitigation_required"] = True
                result["warnings"].append("订单大小超过风险阈值")
                result["restrictions"].append("max_order_size")
            elif order_size >= 100000:
                result["warnings"].append("订单大小较高")

            if risk_level == RiskLevel.CRITICAL.value or risk_score >= 0.9:
                result["approved"] = False
                result["mitigation_required"] = True
                result["restrictions"].append("critical_risk_level")

            limits_result = self.limits_manager.check_pre_trade_limits(
                exchange_name=exchange_name,
                account_id=account_id,
                order_data=order_data,
                current_metrics=risk_metrics,
            )
            policy_result = self.policy_engine.evaluate_order_policy(
                exchange_name=exchange_name,
                account_id=account_id,
                order_data=order_data,
                risk_metrics=risk_metrics,
            )

            result["warnings"].extend(limits_result.get("warnings", []))
            result["warnings"].extend(policy_result.get("warnings", []))
            result["restrictions"].extend(limits_result.get("restrictions", []))
            result["restrictions"].extend(policy_result.get("restrictions", []))
            result["detailed_checks"] = limits_result.get("detailed_checks", [])
            result["triggered_rules"] = policy_result.get("triggered_rules", [])
            result["actions_executed"] = policy_result.get("actions_executed", 0)
            result["mitigation_required"] = result["mitigation_required"] or not result["approved"]
            eval_time_ms = round((time.time() - start) * 1000, 3)
            result["evaluation_time_ms"] = eval_time_ms

            self._update_performance_metrics(float(eval_time_ms))
            return result
        except Exception as exc:
            self.logger.error(f"Error checking order risk: {exc}")
            return {
                "approved": False,
                "risk_level": "UNKNOWN",
                "risk_score": 1.0,
                "warnings": [f"风险检查错误: {exc}"],
                "restrictions": ["system_error"],
                "mitigation_required": True,
                "detailed_checks": [],
                "triggered_rules": [],
                "actions_executed": 0,
                "evaluation_time_ms": 0.0,
                "timestamp": int(time.time()),
            }

    def get_active_events(
        self,
        exchange_name: str | None = None,
        account_id: str | None = None,
        risk_level: RiskLevel | None = None,
    ) -> list[RiskEvent]:
        """Return active events filtered by common fields."""
        with self._lock:
            events = list(self.active_events.values())

        filtered_events = []
        for event in events:
            if exchange_name and event.exchange_name != exchange_name:
                continue
            if account_id and event.account_id != account_id:
                continue
            if risk_level and event.risk_level != risk_level:
                continue
            filtered_events.append(event)
        return filtered_events

    def get_performance_metrics(self) -> dict[str, Any]:
        """Return current manager metrics."""
        with self._lock:
            return {
                **self.performance_metrics,
                "active_events": len(self.active_events),
                "cached_metrics": len(self.risk_metrics_cache),
                "is_monitoring": self.is_monitoring,
                "last_update_time": self.last_update_time,
            }

    def _update_performance_metrics(self, processing_time_ms: float) -> None:
        with self._lock:
            current_average = float(self.performance_metrics["average_processing_time_ms"])
            if current_average == 0:
                updated_average = processing_time_ms
            else:
                updated_average = current_average * 0.9 + processing_time_ms * 0.1
            self.performance_metrics["average_processing_time_ms"] = round(updated_average, 3)

    def _handle_risk_event(self, risk_event: RiskEvent) -> None:
        """Emit the risk event onto the event bus."""
        self.event_bus.emit("RiskEvent", risk_event)

    def _on_trade_event(self, event: Any) -> None:
        self.last_update_time = int(time.time())

    def _on_order_event(self, event: Any) -> None:
        self.last_update_time = int(time.time())

    def _on_position_event(self, event: Any) -> None:
        self.last_update_time = int(time.time())

    def _on_balance_event(self, event: Any) -> None:
        self.last_update_time = int(time.time())

    def _on_market_data_event(self, event: Any) -> None:
        self.last_update_time = int(time.time())

    def _on_system_event(self, event: Any) -> None:
        self.last_update_time = int(time.time())

    def _on_error_event(self, event: Any) -> None:
        self.last_update_time = int(time.time())
