"""风险事件数据容器

包含风险事件的定义、类型、级别和处理信息
"""

import time
from dataclasses import dataclass
from enum import Enum
from typing import Any

from ...containers.auto_init_mixin import AutoInitMixin


class RiskEventType(Enum):
    """风险事件类型"""

    # 市场风险事件
    MARKET_VOLATILITY_SPIKE = "market_volatility_spike"  # 市场波动率激增
    PRICE_MANIPULATION = "price_manipulation"  # 价格操纵
    LIQUIDITY_CRISIS = "liquidity_crisis"  # 流动性危机
    CORRELATION_BREAKDOWN = "correlation_breakdown"  # 相关性失效
    FLASH_CRASH = "flash_crash"  # 闪崩

    # 信用风险事件
    COUNTERPARTY_DEFAULT = "counterparty_default"  # 交易对手违约
    MARGIN_CALL = "margin_call"  # 追加保证金
    CREDIT_DOWNGRADE = "credit_downgrade"  # 信用降级
    SETTLEMENT_FAILURE = "settlement_failure"  # 结算失败

    # 操作风险事件
    SYSTEM_OUTAGE = "system_outage"  # 系统故障
    DATA_CORRUPTION = "data_corruption"  # 数据损坏
    CYBER_ATTACK = "cyber_attack"  # 网络攻击
    HUMAN_ERROR = "human_error"  # 人为错误
    PROCESS_FAILURE = "process_failure"  # 流程失败

    # 合规风险事件
    REGULATORY_BREACH = "regulatory_breach"  # 监管违规
    AML_SUSPICIOUS_ACTIVITY = "aml_suspicious_activity"  # AML可疑活动
    SANCTIONS_VIOLATION = "sanctions_violation"  # 制裁违规
    INSIDER_TRADING = "insider_trading"  # 内幕交易
    REPORTING_FAILURE = "reporting_failure"  # 报告失败

    # 流动性风险事件
    FUNDING_SHORTAGE = "funding_shortage"  # 资金短缺
    ASSET_LIQUIDATION = "asset_liquidation"  # 资产清算
    MARKET_FREEZE = "market_freeze"  # 市场冻结

    # 综合风险事件
    CONCENTRATION_RISK = "concentration_risk"  # 集中度风险
    MODEL_RISK = "model_risk"  # 模型风险
    REPUTATION_RISK = "reputation_risk"  # 声誉风险
    STRATEGIC_RISK = "strategic_risk"  # 战略风险


class RiskLevel(Enum):
    """风险级别"""

    CRITICAL = "CRITICAL"  # 严重 - 需要立即行动
    HIGH = "HIGH"  # 高 - 需要快速响应
    MEDIUM = "MEDIUM"  # 中 - 需要监控和计划行动
    LOW = "LOW"  # 低 - 需要定期监控
    INFO = "INFO"  # 信息 - 仅需记录


class EventStatus(Enum):
    """事件状态"""

    NEW = "NEW"  # 新事件
    ACKNOWLEDGED = "ACKNOWLEDGED"  # 已确认
    INVESTIGATING = "INVESTIGATING"  # 调查中
    MITIGATING = "MITIGATING"  # 缓解中
    RESOLVED = "RESOLVED"  # 已解决
    CLOSED = "CLOSED"  # 已关闭
    FALSE_POSITIVE = "FALSE_POSITIVE"  # 误报


class AlertPriority(Enum):
    """告警优先级"""

    IMMEDIATE = "IMMEDIATE"  # 立即 - 最高优先级
    URGENT = "URGENT"  # 紧急 - 高优先级
    HIGH = "HIGH"  # 高 - 中高优先级
    NORMAL = "NORMAL"  # 普通 - 正常优先级
    LOW = "LOW"  # 低 - 低优先级


class MitigationAction(Enum):
    """缓解措施类型"""

    # 交易限制
    HALT_TRADING = "halt_trading"  # 暂停交易
    REDUCE_POSITIONS = "reduce_positions"  # 减少仓位
    INCREASE_MARGIN = "increase_margin"  # 提高保证金
    LIMIT_NEW_ORDERS = "limit_new_orders"  # 限制新订单

    # 风险管理
    REBALANCE_PORTFOLIO = "rebalance_portfolio"  # 重新平衡投资组合
    HEDGE_POSITIONS = "hedge_positions"  # 对冲仓位
    DIVERSIFY_EXPOSURE = "diversify_exposure"  # 分散暴露
    STRESS_TEST_REVIEW = "stress_test_review"  # 压力测试审查

    # 操作措施
    SYSTEM_ROLLBACK = "system_rollback"  # 系统回滚
    EMERGENCY_PROCEDURE = "emergency_procedure"  # 紧急程序
    MANUAL_OVERRIDE = "manual_override"  # 手动覆盖
    INCREASE_MONITORING = "increase_monitoring"  # 增强监控

    # 合规措施
    REGULATORY_REPORTING = "regulatory_reporting"  # 监管报告
    INTERNAL_AUDIT = "internal_audit"  # 内部审计
    POLICY_UPDATE = "policy_update"  # 政策更新
    STAFF_TRAINING = "staff_training"  # 员工培训


@dataclass
class RiskEvent(AutoInitMixin):
    """风险事件容器"""

    def __init__(
        self, data: dict[str, Any] | None = None, has_been_json_encoded: bool = False
    ) -> Any | None:
        if data is None:
            data = {}

        self.event = "RiskEvent"
        self.timestamp = int(time.time())
        self.event_id = data.get("event_id", "")  # 事件唯一ID
        self.exchange_name = data.get("exchange_name", "")
        self.user_id = data.get("user_id", "")
        self.account_id = data.get("account_id", "")

        # 事件基本信息
        self.event_type = RiskEventType(data.get("event_type", "MARKET_VOLATILITY_SPIKE"))
        self.risk_level = RiskLevel(data.get("risk_level", "MEDIUM"))
        self.event_status = EventStatus(data.get("event_status", "NEW"))
        self.alert_priority = AlertPriority(data.get("alert_priority", "NORMAL"))

        # 事件描述
        self.title = data.get("title", "")
        self.description = data.get("description", "")
        self.impact_assessment = data.get("impact_assessment", "")
        self.root_cause = data.get("root_cause", "")

        # 事件指标
        self.severity_score = float(data.get("severity_score", 0))  # 严重性评分
        self.urgency_score = float(data.get("urgency_score", 0))  # 紧急性评分
        self.likelihood_score = float(data.get("likelihood_score", 0))  # 可能性评分

        # 相关资产
        self.affected_symbols = data.get("affected_symbols", [])  # 受影响的交易对
        self.affected_accounts = data.get("affected_accounts", [])  # 受影响的账户
        self.affected_systems = data.get("affected_systems", [])  # 受影响的系统

        # 检测信息
        self.detection_method = data.get("detection_method", "")  # 检测方法
        self.detection_time = data.get("detection_time", self.timestamp)  # 检测时间
        self.source_system = data.get("source_system", "")  # 源系统
        self.raw_data = data.get("raw_data", {})  # 原始检测数据

        # 处理信息
        self.assigned_to = data.get("assigned_to", "")  # 分配给
        self.acknowledged_by = data.get("acknowledged_by", "")  # 确认人
        self.acknowledged_time = data.get("acknowledged_time")  # 确认时间
        self.resolved_by = data.get("resolved_by", "")  # 解决人
        self.resolved_time = data.get("resolved_time")  # 解决时间

        # 缓解措施
        self.mitigation_actions = [
            MitigationAction(action) for action in data.get("mitigation_actions", [])
        ]
        self.mitigation_status = data.get(
            "mitigation_status", "NOT_STARTED"
        )  # NOT_STARTED, IN_PROGRESS, COMPLETED

        # 关联信息
        self.parent_event_id = data.get("parent_event_id", "")  # 父事件ID
        self.child_event_ids = data.get("child_event_ids", [])  # 子事件IDs
        self.related_event_ids = data.get("related_event_ids", [])  # 相关事件IDs

        # 历史记录
        self.status_history = data.get("status_history", [])  # 状态变更历史
        self.action_history = data.get("action_history", [])  # 行动历史
        self.notes = data.get("notes", [])  # 备注历史

        # 标签和分类
        self.tags = data.get("tags", [])  # 标签
        self.category = data.get("category", "")  # 分类
        self.subcategory = data.get("subcategory", "")  # 子分类

        # 通知信息
        self.notification_sent = data.get("notification_sent", False)
        self.notification_channels = data.get("notification_channels", [])  # 通知渠道
        self.last_notification_time = data.get("last_notification_time")

        self.has_been_json_encoded = has_been_json_encoded

        # 自动生成的字段
        if not self.event_id:
            self.event_id = f"risk_{self.timestamp}_{hash(self.title) % 10000:04d}"


@dataclass
class EventHistoryEntry:
    """事件历史记录条目"""

    def __init__(self, data: dict[str, Any]) -> Any | None:
        self.timestamp = data.get("timestamp", int(time.time()))
        self.action = data.get("action", "")  # 状态变更或行动
        self.previous_value = data.get("previous_value", "")
        self.new_value = data.get("new_value", "")
        self.performed_by = data.get("performed_by", "")
        self.reason = data.get("reason", "")
        self.additional_data = data.get("additional_data", {})


@dataclass
class EventNote:
    """事件备注"""

    def __init__(self, data: dict[str, Any]) -> Any | None:
        self.timestamp = data.get("timestamp", int(time.time()))
        self.author = data.get("author", "")
        self.content = data.get("content", "")
        self.note_type = data.get(
            "note_type", "GENERAL"
        )  # GENERAL, INVESTIGATION, ACTION, RESOLUTION
        self.is_internal = data.get("is_internal", True)
        self.attachments = data.get("attachments", [])


@dataclass
class EventEscalation:
    """事件升级信息"""

    def __init__(self, data: dict[str, Any]) -> Any | None:
        self.escalation_level = data.get("escalation_level", 1)  # 升级级别
        self.escalation_criteria = data.get("escalation_criteria", [])  # 升级条件
        self.escalation_time = data.get("escalation_time")  # 升级时间
        self.escalated_to = data.get("escalated_to", [])  # 升级到的对象
        self.escalation_reason = data.get("escalation_reason", "")
        self.auto_escalation = data.get("auto_escalation", False)  # 是否自动升级


@dataclass
class EventMetrics:
    """事件指标"""

    def __init__(self, data: dict[str, Any]) -> Any | None:
        self.detection_latency = data.get("detection_latency", 0)  # 检测延迟(秒)
        self.resolution_time = data.get("resolution_time", 0)  # 解决时间(秒)
        self.mitigation_effectiveness = data.get("mitigation_effectiveness", 0)  # 缓解效果(0-1)
        self.business_impact = data.get("business_impact", 0)  # 业务影响(金额)
        self.customer_impact = data.get("customer_impact", 0)  # 客户影响(数量)
        self.system_impact = data.get("system_impact", 0)  # 系统影响(评分)

        # 财务影响
        self.financial_loss = data.get("financial_loss", 0)  # 财务损失
        self.recovery_cost = data.get("recovery_cost", 0)  # 恢复成本
        self.opportunity_cost = data.get("opportunity_cost", 0)  # 机会成本

        # 操作影响
        self.downtime_duration = data.get("downtime_duration", 0)  # 停机时长
        self.users_affected = data.get("users_affected", 0)  # 受影响用户数
        self.transactions_affected = data.get("transactions_affected", 0)  # 受影响交易数

        # 合规影响
        self.regulatory_penalties = data.get("regulatory_penalties", 0)  # 监管罚款
        self.compliance_violations = data.get("compliance_violations", 0)  # 合规违规数


@dataclass
class EventPattern:
    """事件模式"""

    def __init__(self, data: dict[str, Any]) -> Any | None:
        self.pattern_id = data.get("pattern_id", "")
        self.pattern_name = data.get("pattern_name", "")
        self.pattern_type = data.get("pattern_type", "")
        self.description = data.get("description", "")

        # 模式特征
        self.frequency = data.get("frequency", 0)  # 频率
        self.seasonality = data.get("seasonality", "")  # 季节性
        self.correlation = data.get("correlation", {})  # 相关性
        self.leading_indicators = data.get("leading_indicators", [])  # 先行指标

        # 预测信息
        self.next_occurrence_probability = data.get(
            "next_occurrence_probability", 0
        )  # 下次发生概率
        self.expected_time_range = data.get("expected_time_range", {})  # 预期时间范围
        self.confidence_level = data.get("confidence_level", 0)  # 置信水平

        # 历史统计
        self.total_occurrences = data.get("total_occurrences", 0)  # 总发生次数
        self.average_severity = data.get("average_severity", 0)  # 平均严重性
        self.average_resolution_time = data.get("average_resolution_time", 0)  # 平均解决时间


def create_risk_event(
    event_type: RiskEventType,
    risk_level: RiskLevel,
    title: str,
    description: str,
    exchange_name: str = "",
    user_id: str = "",
    **kwargs,
) -> RiskEvent:
    """创建风险事件的便捷函数

    Args:
        event_type: 事件类型
        risk_level: 风险级别
        title: 事件标题
        description: 事件描述
        exchange_name: 交易所名称
        user_id: 用户ID
        **kwargs: 其他参数

    Returns:
        RiskEvent: 风险事件实例
    """
    data = {
        "event_type": event_type.value,
        "risk_level": risk_level.value,
        "title": title,
        "description": description,
        "exchange_name": exchange_name,
        "user_id": user_id,
        **kwargs,
    }

    return RiskEvent(data)
