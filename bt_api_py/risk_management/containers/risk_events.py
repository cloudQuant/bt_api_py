"""

、、
"""

from __future__ import annotations

import time
from dataclasses import dataclass
from enum import Enum
from typing import Any

from bt_api_base.containers.auto_init_mixin import AutoInitMixin


class RiskEventType(Enum):
    """"""

    # 
    MARKET_VOLATILITY_SPIKE = "market_volatility_spike"  # 
    PRICE_MANIPULATION = "price_manipulation"  # 
    LIQUIDITY_CRISIS = "liquidity_crisis"  # 
    CORRELATION_BREAKDOWN = "correlation_breakdown"  # 
    FLASH_CRASH = "flash_crash"  # 

    # 
    COUNTERPARTY_DEFAULT = "counterparty_default"  # 
    MARGIN_CALL = "margin_call"  # 
    CREDIT_DOWNGRADE = "credit_downgrade"  # 
    SETTLEMENT_FAILURE = "settlement_failure"  # 

    # 
    SYSTEM_OUTAGE = "system_outage"  # 
    DATA_CORRUPTION = "data_corruption"  # 
    CYBER_ATTACK = "cyber_attack"  # 
    HUMAN_ERROR = "human_error"  # 
    PROCESS_FAILURE = "process_failure"  # 

    # 
    REGULATORY_BREACH = "regulatory_breach"  # 
    AML_SUSPICIOUS_ACTIVITY = "aml_suspicious_activity"  # AML
    SANCTIONS_VIOLATION = "sanctions_violation"  # 
    INSIDER_TRADING = "insider_trading"  # 
    REPORTING_FAILURE = "reporting_failure"  # 

    # 
    FUNDING_SHORTAGE = "funding_shortage"  # 
    ASSET_LIQUIDATION = "asset_liquidation"  # 
    MARKET_FREEZE = "market_freeze"  # 

    # 
    CONCENTRATION_RISK = "concentration_risk"  # 
    MODEL_RISK = "model_risk"  # 
    REPUTATION_RISK = "reputation_risk"  # 
    STRATEGIC_RISK = "strategic_risk"  # 


class RiskLevel(Enum):
    """"""

    CRITICAL = "CRITICAL"  #  - 
    HIGH = "HIGH"  #  - 
    MEDIUM = "MEDIUM"  #  - 
    LOW = "LOW"  #  - 
    INFO = "INFO"  #  - 


class EventStatus(Enum):
    """"""

    NEW = "NEW"  # 
    ACKNOWLEDGED = "ACKNOWLEDGED"  # 
    INVESTIGATING = "INVESTIGATING"  # 
    MITIGATING = "MITIGATING"  # 
    RESOLVED = "RESOLVED"  # 
    CLOSED = "CLOSED"  # 
    FALSE_POSITIVE = "FALSE_POSITIVE"  # 


class AlertPriority(Enum):
    """"""

    IMMEDIATE = "IMMEDIATE"  #  - 
    URGENT = "URGENT"  #  - 
    HIGH = "HIGH"  #  - 
    NORMAL = "NORMAL"  #  - 
    LOW = "LOW"  #  - 


class MitigationAction(Enum):
    """"""

    # 
    HALT_TRADING = "halt_trading"  # 
    REDUCE_POSITIONS = "reduce_positions"  # 
    INCREASE_MARGIN = "increase_margin"  # 
    LIMIT_NEW_ORDERS = "limit_new_orders"  # 

    # 
    REBALANCE_PORTFOLIO = "rebalance_portfolio"  # 
    HEDGE_POSITIONS = "hedge_positions"  # 
    DIVERSIFY_EXPOSURE = "diversify_exposure"  # 
    STRESS_TEST_REVIEW = "stress_test_review"  # 

    # 
    SYSTEM_ROLLBACK = "system_rollback"  # 
    EMERGENCY_PROCEDURE = "emergency_procedure"  # 
    MANUAL_OVERRIDE = "manual_override"  # 
    INCREASE_MONITORING = "increase_monitoring"  # 

    # 
    REGULATORY_REPORTING = "regulatory_reporting"  # 
    INTERNAL_AUDIT = "internal_audit"  # 
    POLICY_UPDATE = "policy_update"  # 
    STAFF_TRAINING = "staff_training"  # 


@dataclass
class RiskEvent(AutoInitMixin):
    """"""

    def __init__(
        self, data: dict[str, Any] | None = None, has_been_json_encoded: bool = False
    ) -> None:
        """__init__ method"""
        if data is None:
            data = {}

        self.event = "RiskEvent"
        self.timestamp = int(time.time())
        self.event_id = data.get("event_id", "")  # ID
        self.exchange_name = data.get("exchange_name", "")
        self.user_id = data.get("user_id", "")
        self.account_id = data.get("account_id", "")

        # 
        self.event_type = RiskEventType(data.get("event_type", "MARKET_VOLATILITY_SPIKE"))
        self.risk_level = RiskLevel(data.get("risk_level", "MEDIUM"))
        self.event_status = EventStatus(data.get("event_status", "NEW"))
        self.alert_priority = AlertPriority(data.get("alert_priority", "NORMAL"))

        # 
        self.title = data.get("title", "")
        self.description = data.get("description", "")
        self.impact_assessment = data.get("impact_assessment", "")
        self.root_cause = data.get("root_cause", "")

        # 
        self.severity_score = float(data.get("severity_score", 0))  # 
        self.urgency_score = float(data.get("urgency_score", 0))  # 
        self.likelihood_score = float(data.get("likelihood_score", 0))  # 

        # 
        self.affected_symbols = data.get("affected_symbols", [])  # 
        self.affected_accounts = data.get("affected_accounts", [])  # 
        self.affected_systems = data.get("affected_systems", [])  # 

        # 
        self.detection_method = data.get("detection_method", "")  # 
        self.detection_time = data.get("detection_time", self.timestamp)  # 
        self.source_system = data.get("source_system", "")  # 
        self.raw_data = data.get("raw_data", {})  # 

        # 
        self.assigned_to = data.get("assigned_to", "")  # 
        self.acknowledged_by = data.get("acknowledged_by", "")  # 
        self.acknowledged_time = data.get("acknowledged_time")  # 
        self.resolved_by = data.get("resolved_by", "")  # 
        self.resolved_time = data.get("resolved_time")  # 

        # 
        self.mitigation_actions = [
            MitigationAction(action) for action in data.get("mitigation_actions", [])
        ]
        self.mitigation_status = data.get(
            "mitigation_status", "NOT_STARTED"
        )  # NOT_STARTED, IN_PROGRESS, COMPLETED

        # 
        self.parent_event_id = data.get("parent_event_id", "")  # ID
        self.child_event_ids = data.get("child_event_ids", [])  # IDs
        self.related_event_ids = data.get("related_event_ids", [])  # IDs

        # 
        self.status_history = data.get("status_history", [])  # 
        self.action_history = data.get("action_history", [])  # 
        self.notes = data.get("notes", [])  # 

        # 
        self.tags = data.get("tags", [])  # 
        self.category = data.get("category", "")  # 
        self.subcategory = data.get("subcategory", "")  # 

        # 
        self.notification_sent = data.get("notification_sent", False)
        self.notification_channels = data.get("notification_channels", [])  # 
        self.last_notification_time = data.get("last_notification_time")

        self.has_been_json_encoded = has_been_json_encoded

        # 
        if not self.event_id:
            self.event_id = f"risk_{self.timestamp}_{hash(self.title) % 10000:04d}"


@dataclass
class EventHistoryEntry:
    """"""

    def __init__(self, data: dict[str, Any]) -> None:
        """__init__ method"""
        self.timestamp = data.get("timestamp", int(time.time()))
        self.action = data.get("action", "")  # 
        self.previous_value = data.get("previous_value", "")
        self.new_value = data.get("new_value", "")
        self.performed_by = data.get("performed_by", "")
        self.reason = data.get("reason", "")
        self.additional_data = data.get("additional_data", {})


@dataclass
class EventNote:
    """"""

    def __init__(self, data: dict[str, Any]) -> None:
        """__init__ method"""
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
    """"""

    def __init__(self, data: dict[str, Any]) -> None:
        """__init__ method"""
        self.escalation_level = data.get("escalation_level", 1)  # 
        self.escalation_criteria = data.get("escalation_criteria", [])  # 
        self.escalation_time = data.get("escalation_time")  # 
        self.escalated_to = data.get("escalated_to", [])  # 
        self.escalation_reason = data.get("escalation_reason", "")
        self.auto_escalation = data.get("auto_escalation", False)  # 


@dataclass
class EventMetrics:
    """"""

    def __init__(self, data: dict[str, Any]) -> None:
        """__init__ method"""
        self.detection_latency = data.get("detection_latency", 0)  # ()
        self.resolution_time = data.get("resolution_time", 0)  # ()
        self.mitigation_effectiveness = data.get("mitigation_effectiveness", 0)  # (0-1)
        self.business_impact = data.get("business_impact", 0)  # ()
        self.customer_impact = data.get("customer_impact", 0)  # ()
        self.system_impact = data.get("system_impact", 0)  # ()

        # 
        self.financial_loss = data.get("financial_loss", 0)  # 
        self.recovery_cost = data.get("recovery_cost", 0)  # 
        self.opportunity_cost = data.get("opportunity_cost", 0)  # 

        # 
        self.downtime_duration = data.get("downtime_duration", 0)  # 
        self.users_affected = data.get("users_affected", 0)  # 
        self.transactions_affected = data.get("transactions_affected", 0)  # 

        # 
        self.regulatory_penalties = data.get("regulatory_penalties", 0)  # 
        self.compliance_violations = data.get("compliance_violations", 0)  # 


@dataclass
class EventPattern:
    """"""

    def __init__(self, data: dict[str, Any]) -> None:
        """__init__ method"""
        self.pattern_id = data.get("pattern_id", "")
        self.pattern_name = data.get("pattern_name", "")
        self.pattern_type = data.get("pattern_type", "")
        self.description = data.get("description", "")

        # 
        self.frequency = data.get("frequency", 0)  # 
        self.seasonality = data.get("seasonality", "")  # 
        self.correlation = data.get("correlation", {})  # 
        self.leading_indicators = data.get("leading_indicators", [])  # 

        # 
        self.next_occurrence_probability = data.get(
            "next_occurrence_probability", 0
        )  # 
        self.expected_time_range = data.get("expected_time_range", {})  # 
        self.confidence_level = data.get("confidence_level", 0)  # 

        # 
        self.total_occurrences = data.get("total_occurrences", 0)  # 
        self.average_severity = data.get("average_severity", 0)  # 
        self.average_resolution_time = data.get("average_resolution_time", 0)  # 


def create_risk_event(
    event_type: RiskEventType,
    risk_level: RiskLevel,
    title: str,
    description: str,
    exchange_name: str = "",
    user_id: str = "",
    **kwargs,
) -> RiskEvent:
    """

    Args: event_type:
        risk_level: 
        title: 
        description: 
        exchange_name: 
        user_id: ID
        **kwargs: 

    Returns: RiskEvent:
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
