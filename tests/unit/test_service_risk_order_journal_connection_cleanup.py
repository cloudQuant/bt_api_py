"""SQLite journal connections are closed when setup cannot complete."""

import sqlite3
from pathlib import Path

import pytest

from bt_api_py import service_risk_order_journal as journal_module


class _PragmaFailingConnection:
    def __init__(self, fail_on: str) -> None:
        self.fail_on = fail_on
        self.executed: list[str] = []
        self.close_calls = 0
        self.row_factory: object | None = None

    def execute(self, statement: str) -> object:
        self.executed.append(statement)
        if statement == self.fail_on:
            raise sqlite3.OperationalError("synthetic pragma failure")
        return self

    def close(self) -> None:
        self.close_calls += 1


@pytest.mark.parametrize(
    ("fail_on", "expected_statements"),
    [
        ("PRAGMA foreign_keys=ON", ["PRAGMA foreign_keys=ON"]),
        (
            "PRAGMA busy_timeout=30000",
            ["PRAGMA foreign_keys=ON", "PRAGMA busy_timeout=30000"],
        ),
    ],
)
def test_connection_closes_handle_when_pragma_setup_fails(
    monkeypatch: pytest.MonkeyPatch,
    fail_on: str,
    expected_statements: list[str],
) -> None:
    connection = _PragmaFailingConnection(fail_on)
    journal = object.__new__(journal_module.RiskOrderJournal)
    journal._path = Path("synthetic-journal.sqlite")

    def connect(
        path: Path, *, timeout: float, isolation_level: None
    ) -> _PragmaFailingConnection:
        assert path == journal._path
        assert timeout == 30.0
        assert isolation_level is None
        return connection

    monkeypatch.setattr(journal_module.sqlite3, "connect", connect)

    with pytest.raises(sqlite3.OperationalError, match="synthetic pragma failure"):
        journal._connection()

    assert connection.executed == expected_statements
    assert connection.close_calls == 1
