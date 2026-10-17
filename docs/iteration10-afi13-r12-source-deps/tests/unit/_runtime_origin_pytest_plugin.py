"""Pytest-only adapter for finite AFI13 runtime receipts."""

from __future__ import annotations

import multiprocessing
import os
import time
from typing import Any

import pytest

from tests.unit._runtime_origin_receipts import (
    capture_runtime_origins,
    summarize_exception,
    write_runtime_receipt,
)


class RuntimeReceiptPlugin:
    def __init__(self) -> None:
        self._nodeids: tuple[str, ...] = ()
        self._reports: list[dict[str, str]] = []
        self._errors: list[dict[str, Any]] = []

    def pytest_sessionstart(self, session: Any) -> None:
        payload = {
            **capture_runtime_origins(
                "strategy-parent", phase="pytest-session-start", check_required=False
            ),
            "rootpath": str(session.config.rootpath),
            "inipath": str(session.config.inipath) if session.config.inipath else None,
        }
        write_runtime_receipt("strategy-parent", "pytest-session-start", payload)

    def pytest_collection_finish(self, session: Any) -> None:
        self._nodeids = tuple(item.nodeid[:512] for item in session.items[:32])
        payload = {
            **capture_runtime_origins("strategy-parent", phase="collection-finish"),
            "nodeids": self._nodeids,
        }
        write_runtime_receipt("strategy-parent", "pytest-collection-finish", payload)

    def record_makereport(self, item: Any, call: Any, report: Any) -> None:
        if len(self._reports) < 32:
            self._reports.append(
                {
                    "nodeid": str(report.nodeid)[:512],
                    "when": str(report.when)[:16],
                    "outcome": str(report.outcome)[:16],
                }
            )
        if call.excinfo is not None and len(self._errors) < 8:
            self._errors.append(
                {
                    "nodeid": str(item.nodeid)[:512],
                    "phase": str(report.when)[:16],
                    "error": summarize_exception(call.excinfo.value),
                }
            )

    def pytest_sessionfinish(self, session: Any, exitstatus: int) -> None:
        payload = {
            **capture_runtime_origins("strategy-parent", phase="pytest-session-finish"),
            "pytest_exit_code": int(exitstatus),
            "nodeids": self._nodeids,
            "reports": tuple(self._reports),
            "errors": tuple(self._errors),
            "monotonic_ns": time.monotonic_ns(),
            "process_name": multiprocessing.current_process().name,
            "pid": os.getpid(),
        }
        write_runtime_receipt("strategy-parent", "pytest-session-finish", payload)


pytest_plugin = RuntimeReceiptPlugin()


def pytest_sessionstart(session: Any) -> None:
    pytest_plugin.pytest_sessionstart(session)


def pytest_collection_finish(session: Any) -> None:
    pytest_plugin.pytest_collection_finish(session)


@pytest.hookimpl(hookwrapper=True)
def pytest_runtest_makereport(item: Any, call: Any):
    outcome = yield
    pytest_plugin.record_makereport(item, call, outcome.get_result())


def pytest_sessionfinish(session: Any, exitstatus: int) -> None:
    pytest_plugin.pytest_sessionfinish(session, exitstatus)
