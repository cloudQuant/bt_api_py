"""Pure fakes for a synthetic Gateway's unconfirmed cleanup retention."""

import ast
import inspect
import sys
from types import SimpleNamespace

import pytest

from tests.integration import _iter64_recovery_support as support


class HoldObserved(BaseException):
    pass


@pytest.mark.parametrize("cleanup", ["HOST_UNCONFIRMED", "interrupted"])
@pytest.mark.parametrize("report_fails", [False, True])
def test_failed_cleanup_holds_original_resources_even_if_report_fails(monkeypatch, cleanup, report_fails):
    resources = tuple(object() for _ in range(5))
    controls, reports = object(), []

    def close(*args):
        assert args == resources
        if cleanup == "interrupted":
            raise KeyboardInterrupt()
        return cleanup

    def put(row):
        reports.append(row)
        if report_fails:
            raise KeyboardInterrupt()

    def hold(actual_controls):
        assert actual_controls is controls
        frame = sys._getframe(1)
        assert tuple(frame.f_locals[name] for name in
                     ("host", "owner", "stream", "read_service", "read_source")) == resources
        raise HoldObserved()

    monkeypatch.setattr(support, "_close_synthetic_gateway", close)
    monkeypatch.setattr(support, "_hold_synthetic_gateway", hold)
    with pytest.raises(HoldObserved):
        support._finish_synthetic_gateway(*resources, SimpleNamespace(put=put), controls,
                                          "a" * 32, "b" * 32, "c" * 32)
    assert reports == [("gateway_close_unknown", support.os.getpid(), "a" * 32, "CLEANUP_UNKNOWN")]


def test_complete_cleanup_emits_bound_receipt_without_hold(monkeypatch):
    reports = []
    monkeypatch.setattr(support, "_close_synthetic_gateway", lambda *_: "COMPLETE")
    monkeypatch.setattr(support, "_hold_synthetic_gateway", lambda _: pytest.fail("hold"))
    support._finish_synthetic_gateway(*(object() for _ in range(5)),
                                      SimpleNamespace(put=reports.append), object(),
                                      "a" * 32, "b" * 32, "c" * 32)
    assert reports == [("gateway_close", support.os.getpid(), "a" * 32,
                        "b" * 32, "c" * 32, "COMPLETE")]


def test_broken_control_queue_and_interrupt_do_not_escape_wait_step(monkeypatch):
    actions = []

    def fail_get():
        actions.append("get")
        raise KeyboardInterrupt()

    def fail_sleep(seconds):
        actions.append(("sleep", seconds))
        raise KeyboardInterrupt()

    monkeypatch.setattr(support.time, "sleep", fail_sleep)
    support._retained_gateway_wait(SimpleNamespace(get=fail_get))
    assert actions == ["get", ("sleep", 1)]


def test_holder_has_no_command_release_path():
    tree = ast.parse(inspect.getsource(support._hold_synthetic_gateway))
    loop = next(node for node in ast.walk(tree) if isinstance(node, ast.While))
    assert isinstance(loop.test, ast.Constant) and loop.test.value is True
    assert not any(isinstance(node, (ast.Break, ast.Return, ast.Raise)) for node in ast.walk(loop))
