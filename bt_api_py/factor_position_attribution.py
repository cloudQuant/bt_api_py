"""Public SDK facade for local execution position attribution storage."""

from functools import wraps

from bt_api_execution.durable_capacity import DurableCapacityError
from bt_api_execution.factor_position_journal_port import (
    FactorPositionAttributionJournalPort as _ExecutionPositionAttributionJournalPort,
)
from bt_api_execution.factor_position_attribution import (
    FACTOR_POSITION_BOOK_SCHEMA_VERSION,
    FACTOR_POSITION_BOOK_EXPECTED_OBJECTS,
    factor_position_user_objects,
    has_exact_factor_position_book_schema,
    factor_position_schema_version,
    preflight_factor_position_journal_schema,
    ensure_factor_position_journal_schema,
    validate_factor_position_journal_schema,
    require_factor_close_reconciliation_inputs,
    provider_trade_reconciliation_unavailable,
    reject_factor_provider_evidence,
    iter_factor_position_capacity_rows,
    snapshot_factor_position_capacity_row_size,
    FactorPositionAttributionSnapshot,
    FactorPositionBook as _ExecutionFactorPositionBook,
    FactorLocalExecutionFill,
    FactorPositionApplyResult,
    FactorPositionBookError,
    apply_factor_execution_fill,
    ensure_factor_position_book_schema,
    record_factor_position_flat_checkpoint,
    record_factor_position_grid,
    validate_factor_position_close_owner,
    read_factor_local_position,
    read_factor_position_snapshot_inputs,
    factor_decimal_or_none,
    factor_aware_utc,
    require_factor_identifier,
)
from bt_api_py.journal_capacity import JournalCapacityError


def _capacity_checked(function):
    @wraps(function)
    def call(*args, **kwargs):
        try:
            return function(*args, **kwargs)
        except DurableCapacityError as exc:
            raise JournalCapacityError(exc.code) from exc
    return call


# Preserve the SDK facade's existing capacity-error contract.
record_factor_position_grid = _capacity_checked(record_factor_position_grid)
record_factor_position_flat_checkpoint = _capacity_checked(record_factor_position_flat_checkpoint)
apply_factor_execution_fill = _capacity_checked(apply_factor_execution_fill)

class FactorPositionAttributionJournalPort(_ExecutionPositionAttributionJournalPort):
    """Execution-owned journal port with the SDK's capacity-error boundary."""

    record_grid = _capacity_checked(_ExecutionPositionAttributionJournalPort.record_grid)
    record_flat_checkpoint = _capacity_checked(
        _ExecutionPositionAttributionJournalPort.record_flat_checkpoint
    )


class FactorPositionBook(_ExecutionFactorPositionBook):
    """Execution-owned attribution with the SDK capacity error boundary."""

    def apply_fill(self, cursor, fill: FactorLocalExecutionFill):
        return apply_factor_execution_fill(cursor, fill=fill)


__all__ = (
    "FactorPositionAttributionJournalPort",
    "FactorPositionAttributionSnapshot",
    "FactorPositionBook",
    "FACTOR_POSITION_BOOK_SCHEMA_VERSION",
    "FACTOR_POSITION_BOOK_EXPECTED_OBJECTS",
    "factor_position_user_objects",
    "has_exact_factor_position_book_schema",
    "factor_position_schema_version",
    "preflight_factor_position_journal_schema",
    "ensure_factor_position_journal_schema",
    "validate_factor_position_journal_schema",
    "require_factor_close_reconciliation_inputs",
    "provider_trade_reconciliation_unavailable",
    "reject_factor_provider_evidence",
    "iter_factor_position_capacity_rows",
    "snapshot_factor_position_capacity_row_size",
    "FactorLocalExecutionFill",
    "FactorPositionApplyResult",
    "FactorPositionBookError",
    "apply_factor_execution_fill",
    "ensure_factor_position_book_schema",
    "record_factor_position_flat_checkpoint",
    "record_factor_position_grid",
    "validate_factor_position_close_owner",
    "read_factor_local_position",
    "read_factor_position_snapshot_inputs",
    "factor_decimal_or_none",
    "factor_aware_utc",
    "require_factor_identifier",
)
