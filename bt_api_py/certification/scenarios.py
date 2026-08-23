"""Certification scenario registry for CTP program trading acceptance."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass(frozen=True)
class CertificationScenario:
    """Definition for one certification scenario."""

    scenario_id: str
    name: str
    category: str
    required_events: tuple[str, ...] = ()
    evidence_fields: tuple[str, ...] = ()
    pass_conditions: tuple[str, ...] = ()
    metadata: dict[str, Any] = field(default_factory=dict)


class CertificationScenarioRegistry:
    """Registry with uniqueness checks for certification scenarios."""

    def __init__(self, scenarios: list[CertificationScenario] | None = None) -> None:
        self._items: dict[str, CertificationScenario] = {}
        for scenario in scenarios or []:
            self.register(scenario)

    def register(self, scenario: CertificationScenario) -> None:
        if scenario.scenario_id in self._items:
            raise ValueError(f"duplicate certification scenario: {scenario.scenario_id}")
        self._items[scenario.scenario_id] = scenario

    def get(self, scenario_id: str) -> CertificationScenario:
        return self._items[scenario_id]

    def all(self) -> list[CertificationScenario]:
        return list(self._items.values())

    def to_dicts(self) -> list[dict[str, Any]]:
        return [
            {
                "scenario_id": item.scenario_id,
                "name": item.name,
                "category": item.category,
                "required_events": list(item.required_events),
                "evidence_fields": list(item.evidence_fields),
                "pass_conditions": list(item.pass_conditions),
                "metadata": dict(item.metadata),
            }
            for item in self.all()
        ]


_SCENARIO_ROWS = [
    (
        "AUTH-01",
        "认证登录",
        "接口适应性",
        ("store_auth_success", "store_login_success"),
        ("front_id", "session_id", "trading_day"),
    ),
    (
        "TRADE-OPEN-01",
        "正常下达开仓指令",
        "基础交易",
        ("order_submit_request", "order_status_accepted"),
        ("order_ref", "external_order_id"),
    ),
    (
        "TRADE-CLOSE-01",
        "正常下达平仓指令",
        "基础交易",
        ("order_submit_request", "order_status_accepted"),
        ("order_ref", "external_order_id"),
    ),
    (
        "TRADE-CANCEL-01",
        "正常下达撤单指令",
        "基础交易",
        ("order_cancel_request", "order_status_canceled"),
        ("order_ref", "external_order_id"),
    ),
    (
        "MONITOR-CONN-01",
        "连接成功显示连接成功",
        "连接异常监测",
        ("store_connected",),
        ("gateway_key", "market_connection", "trade_connection"),
    ),
    (
        "MONITOR-CONN-02",
        "连接断开显示连接断开",
        "连接异常监测",
        ("store_disconnected",),
        ("gateway_key", "timestamp"),
    ),
    (
        "MONITOR-CONN-03",
        "断线后显示重连成功",
        "连接异常监测",
        ("store_reconnect_success",),
        ("gateway_key", "timestamp"),
    ),
    (
        "MONITOR-COUNT-01",
        "正常统计报单笔数",
        "报撤单监测",
        ("order_submit_request",),
        ("submitted_order_count",),
    ),
    (
        "MONITOR-COUNT-02",
        "正常统计撤单笔数",
        "报撤单监测",
        ("order_cancel_request",),
        ("cancel_order_count",),
    ),
    (
        "RISK-REPEAT-01",
        "重复开仓报单统计",
        "重复报单监测",
        ("risk_repeat_order_detected",),
        ("repeat_key", "repeat_count"),
    ),
    (
        "RISK-REPEAT-02",
        "重复平仓报单统计",
        "重复报单监测",
        ("risk_repeat_order_detected",),
        ("repeat_key", "repeat_count"),
    ),
    (
        "RISK-REPEAT-03",
        "重复撤单统计",
        "重复报单监测",
        ("risk_repeat_cancel_detected",),
        ("repeat_key", "repeat_count"),
    ),
    (
        "RISK-THRESHOLD-01",
        "报单笔数阈值设置",
        "阈值管理",
        ("risk_threshold_configured",),
        ("order_threshold",),
    ),
    (
        "RISK-THRESHOLD-02",
        "报单笔数达到阈值预警",
        "阈值管理",
        ("risk_threshold_triggered",),
        ("order_threshold", "submitted_order_count"),
    ),
    (
        "RISK-THRESHOLD-03",
        "报撤单笔数阈值设置",
        "阈值管理",
        ("risk_threshold_configured",),
        ("cancel_threshold",),
    ),
    (
        "RISK-THRESHOLD-04",
        "报撤单笔数达到阈值预警",
        "阈值管理",
        ("risk_threshold_triggered",),
        ("cancel_threshold", "cancel_order_count"),
    ),
    (
        "RISK-THRESHOLD-05",
        "重复报单阈值设置",
        "阈值管理",
        ("risk_threshold_configured",),
        ("repeat_threshold", "repeat_window_sec"),
    ),
    (
        "RISK-THRESHOLD-06",
        "重复报单达到阈值预警",
        "阈值管理",
        ("risk_threshold_triggered",),
        ("repeat_threshold", "repeat_count"),
    ),
    (
        "VALIDATION-01",
        "合约代码错误检查并拒绝报单",
        "错误防范",
        ("order_validation_rejected",),
        ("instrument", "error_msg"),
    ),
    (
        "VALIDATION-02",
        "价格最小变动价位错误检查",
        "错误防范",
        ("order_validation_rejected",),
        ("price", "price_tick", "error_msg"),
    ),
    (
        "VALIDATION-03",
        "单笔委托最大手数检查",
        "错误防范",
        ("order_validation_rejected",),
        ("size", "max_order_size", "error_msg"),
    ),
    (
        "ERROR-01",
        "资金不足错误展示",
        "错误提示",
        ("order_reject_remote",),
        ("ErrorID", "ErrorMsg", "StatusMsg"),
    ),
    (
        "ERROR-02",
        "持仓不足错误展示",
        "错误提示",
        ("order_reject_remote",),
        ("ErrorID", "ErrorMsg", "StatusMsg"),
    ),
    (
        "ERROR-03",
        "市场状态不允许错误展示",
        "错误提示",
        ("order_reject_remote",),
        ("ErrorID", "ErrorMsg", "StatusMsg"),
    ),
    (
        "EMERGENCY-01",
        "限制账号交易权限暂停交易",
        "应急处理",
        ("account_trading_disabled",),
        ("account_id_masked", "reason"),
    ),
    (
        "EMERGENCY-02",
        "暂停策略执行",
        "应急处理",
        ("strategy_trading_paused",),
        ("strategy_id", "reason"),
    ),
    (
        "EMERGENCY-03",
        "强制账号退出",
        "应急处理",
        ("gateway_force_logout_requested",),
        ("gateway_key", "reason"),
    ),
    (
        "BATCH-CANCEL-01",
        "多笔部分成交报单批量撤单",
        "批量撤单",
        ("batch_cancel_requested",),
        ("order_refs", "partial_count"),
    ),
    (
        "BATCH-CANCEL-02",
        "多笔已报单批量撤单",
        "批量撤单",
        ("batch_cancel_requested",),
        ("order_refs", "open_order_count"),
    ),
    (
        "LOG-TRADE-01",
        "交易信息记录",
        "日志记录",
        ("order_submit_request", "trade_execution"),
        ("trace_id", "order_ref", "trade_id"),
    ),
    (
        "LOG-SYSTEM-01",
        "系统运行信息记录",
        "日志记录",
        ("store_connected", "store_ready"),
        ("trace_id", "gateway_key"),
    ),
    ("LOG-MONITOR-01", "监测信息记录", "日志记录", ("risk_monitor_event",), ("trace_id", "metric")),
    (
        "LOG-ERROR-01",
        "错误提示信息记录",
        "日志记录",
        ("store_error",),
        ("trace_id", "error_code", "error_msg"),
    ),
]


def default_certification_scenario_registry() -> CertificationScenarioRegistry:
    """Return the built-in 33-scenario certification registry."""

    return CertificationScenarioRegistry(
        [
            CertificationScenario(
                scenario_id=scenario_id,
                name=name,
                category=category,
                required_events=required_events,
                evidence_fields=evidence_fields,
                pass_conditions=("required_events_present", "evidence_fields_present"),
            )
            for scenario_id, name, category, required_events, evidence_fields in _SCENARIO_ROWS
        ]
    )
