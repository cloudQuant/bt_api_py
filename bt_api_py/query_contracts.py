"""Provider-neutral query, account, and market contracts for SDK consumers.

These names are direct re-exports of the canonical ``bt_api_base`` classes.
This module deliberately adds no wrapper, subclass, conversion, or alternate
contract so identity checks continue to see the original SDK contract types.
"""

from __future__ import annotations

from bt_api_base.account_protocol import (
    AccountBalance,
    AccountOpenOrder,
    AccountPosition,
    AccountValuation,
)
from bt_api_base.execution_query_protocol import (
    CommissionRateRecord,
    ExecutionOrderRecord,
    ExecutionTradeRecord,
    MarginRateRecord,
)
from bt_api_base.market_protocol import MarketInstrumentKey
from bt_api_base.query_protocol import (
    STANDARD_QUERY_FAMILIES,
    STANDARD_QUERY_FILTER_KEYS,
    QueryBatchReceipt,
    QueryCompleteness,
    QueryRequest,
    QuerySnapshot,
    QuerySnapshotLease,
    ProviderQueryPort,
)

__all__ = [
    "AccountBalance",
    "AccountOpenOrder",
    "AccountPosition",
    "AccountValuation",
    "CommissionRateRecord",
    "ExecutionOrderRecord",
    "ExecutionTradeRecord",
    "MarketInstrumentKey",
    "MarginRateRecord",
    "ProviderQueryPort",
    "QueryBatchReceipt",
    "QueryCompleteness",
    "QueryRequest",
    "QuerySnapshot",
    "QuerySnapshotLease",
    "STANDARD_QUERY_FAMILIES",
    "STANDARD_QUERY_FILTER_KEYS",
]
