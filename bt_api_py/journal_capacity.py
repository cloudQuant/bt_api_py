"""Provider-neutral facade for durable execution-journal capacity tracking.

Framework packages may use this boundary to account for one keyed journal row
mutation inside the journal owner's active SQLite transaction.  The facade
does not expose capacity tables, choose a provider, or grant a separate
transaction or budget.
"""

from __future__ import annotations

import sqlite3
from contextlib import contextmanager
from typing import Any, Iterator

from bt_api_execution.durable_capacity import (
    DurableCapacityError as _DurableCapacityError,
    canonical_sql_row_size,
    track_durable_row_change as _track_durable_row_change,
)
from bt_api_py.execution import ExecutionJournalError

__all__ = ("JournalCapacityError", "canonical_sql_row_size", "track_durable_row_change")


class JournalCapacityError(ExecutionJournalError):
    """A public journal-capacity failure retaining the SDK's stable error code."""


@contextmanager
def track_durable_row_change(
    cursor: sqlite3.Cursor,
    owner_table_ref: str,
    key_values: tuple[Any, ...],
    capacity_class: str,
) -> Iterator[None]:
    """Measure one keyed row under the journal's durable-capacity contract.

    The journal owns transaction lifetime and configured limits. SDK-owned row
    keys are handled internally; every application-owned reference and key is
    opaque and resolved by the injected owner port, which returns canonical
    before/final byte sizes. This facade performs no table lookup or SQL and
    never manages the transaction.
    """

    try:
        with _track_durable_row_change(
            cursor, owner_table_ref, key_values, capacity_class
        ):
            yield
    except _DurableCapacityError as error:
        raise JournalCapacityError(error.code) from error
