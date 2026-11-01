"""Lazy SDK facade for the CTP provider's structural close planner.

The provider owns native position and close rules. This historical SDK import
path does not load the optional provider until a planner symbol is requested.
The planner remains structural only and never establishes execution authority.
"""

from importlib import import_module

__all__ = [
    "CtpCloseAction",
    "CtpClosePlan",
    "CtpClosePlanError",
    "EXECUTION_BLOCK_REASON",
    "PLANNER_CAPABILITY",
    "SCHEMA_VERSION",
    "build_ctp_close_plan",
    "canonical_ctp_close_plan_json",
]


def __getattr__(name):
    if name.startswith("__"):
        raise AttributeError(name)
    return getattr(import_module("bt_api_ctp.ctp_close_plan"), name)
