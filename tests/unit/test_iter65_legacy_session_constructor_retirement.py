"""Synthetic tests for retirement of new legacy assembly objects."""
from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import pytest

from bt_api_py.service_execution_assembly import (
    ServiceExecutionAssembly,
    ServiceExecutionAssemblyError,
)
CODE = "LEGACY_SERVICE_EXECUTION_RETIRED"


class _UntouchedResource:
    def __init__(self, calls):
        object.__setattr__(self, "calls", calls)

    def __getattribute__(self, name):
        calls = object.__getattribute__(self, "calls")
        calls.append(("resource_attribute", name))
        raise AssertionError("constructor must not inspect the supplied resource")


class _Cerebro:
    def __init__(self):
        self.calls = []
        self.result = object()

    def run(self, **kwargs):
        self.calls.append(kwargs)
        return self.result


def _new_assembly(calls):
    resource = _UntouchedResource(calls)
    def callback(*args, **kwargs):
        calls.append("callback")
    return ServiceExecutionAssembly(
        resource, execution_client=resource, routes=resource,
        recovery_facade=resource, recovery_flow=resource,
        recovery_callback=callback,
    )


def test_direct_constructor_rejects_without_touching_supplied_objects():
    calls = []
    with pytest.raises(ServiceExecutionAssemblyError) as exc:
        _new_assembly(calls)
    assert exc.value.code == CODE
    assert calls == []


def test_real_bt_session_cannot_run_a_new_assembly():
    session_module = pytest.importorskip("backtrader.service.service_execution_session")
    calls = []
    cerebro = _Cerebro()
    with pytest.raises(ServiceExecutionAssemblyError) as exc:
        session_module.ServiceExecutionSession(_new_assembly(calls)).run(cerebro)
    assert exc.value.code == CODE
    assert calls == []
    assert cerebro.calls == []


def test_unconfigured_cerebro_remains_available():
    session_module = pytest.importorskip("backtrader.service.service_execution_session")
    calls = []
    cerebro = _Cerebro()
    assert session_module.run_configured_cerebro(
        cerebro, None, runtime_factory=lambda config: calls.append("factory"), runonce=False
    ) is cerebro.result
    assert cerebro.calls == [{"runonce": False}]
    assert calls == []


def test_sdk_only_module_collection_and_constructor_without_backtrader():
    script = """
import builtins
import runpy
import sys
import pytest

original_import = builtins.__import__
def no_backtrader(name, *args, **kwargs):
    if name == 'backtrader' or name.startswith('backtrader.'):
        raise ImportError('backtrader intentionally unavailable')
    return original_import(name, *args, **kwargs)
builtins.__import__ = no_backtrader
namespace = runpy.run_path(sys.argv[1])
namespace['test_direct_constructor_rejects_without_touching_supplied_objects']()
session_tests = runpy.run_path(sys.argv[2])
for config in (None, {}, session_tests['_client_config']()):
    with pytest.MonkeyPatch.context() as monkeypatch:
        session_tests['test_assembly_rejects_before_parser_or_runtime_factory'](config, monkeypatch)
"""
    result = subprocess.run(
        [sys.executable, "-c", script, __file__,
         str(Path(__file__).with_name("test_iter65_legacy_session_retirement.py"))],
        capture_output=True, text=True, check=False
    )
    assert result.returncode == 0, result.stderr
