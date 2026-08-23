"""策略规则类型与动作类型定义。"""

from __future__ import annotations


class RuleType:
    """"""

    #
    CONDITION_BASED = "condition_based"  #
    THRESHOLD_BASED = "threshold_based"  #
    TIME_BASED = "time_based"  #
    EVENT_BASED = "event_based"  #

    #
    AND_RULE = "and_rule"  # AND
    OR_RULE = "or_rule"  # OR
    NOT_RULE = "not_rule"  # NOT

    #
    ML_PREDICTION = "ml_prediction"  # ML


class ActionType:
    """"""

    #
    HALT_TRADING = "halt_trading"  #
    LIMIT_ORDERS = "limit_orders"  #
    CANCEL_ORDERS = "cancel_orders"  #
    REDUCE_POSITIONS = "reduce_positions"  #

    #
    INCREASE_MARGIN = "increase_margin"  #
    SEND_ALERT = "send_alert"  #
    LOG_EVENT = "log_event"  #
    NOTIFY_MANAGER = "notify_manager"  #

    #
    ADJUST_LIMITS = "adjust_limits"  #
    UPDATE_MODEL = "update_model"  #
    RUN_STRESS_TEST = "run_stress_test"  #
