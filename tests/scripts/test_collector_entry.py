"""Offline checks for the Windows collector launch modes."""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path
from types import SimpleNamespace
from xml.etree import ElementTree

import pytest

ROOT = Path(__file__).resolve().parents[2]


def test_windows_scheduled_task_xml_is_valid_and_monitors_supervisor():
    tree = ElementTree.parse(ROOT / "ctp_data/deploy/windows/ctp-tick-collector.xml")  # noqa: S314 - trusted repository fixture
    arguments = tree.find(".//{*}Exec/{*}Arguments")
    assert arguments is not None
    assert "service.bat" in arguments.text
    assert "--foreground" in arguments.text


@pytest.fixture
def entry(monkeypatch):
    spec = importlib.util.spec_from_file_location(
        "collector_entry_test", ROOT / "ctp_data/collector_entry.py"
    )
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    monkeypatch.setattr(module, "prepare_runtime", lambda: ROOT)
    monkeypatch.delenv("COLLECTOR_ARGS", raising=False)
    return module


def test_default_launch_and_status_dispatch_to_controller(entry, monkeypatch):
    calls = []
    monkeypatch.setitem(
        sys.modules, "collector_control", SimpleNamespace(main=lambda args: calls.append(args) or 0)
    )
    monkeypatch.setenv("COLLECTOR_SERVICE_ARGS", "--lead 600")
    assert entry.main([]) == 0
    assert entry.main(["--status"]) == 0
    # The controller owns environment service args so they are parsed once.
    assert calls == [["start"], ["status"]]


def test_explicit_duration_does_not_add_until_close(entry, monkeypatch):
    calls = []
    monkeypatch.setitem(
        sys.modules,
        "bt_api_ctp.collector.cli",
        SimpleNamespace(main=lambda args: calls.append(args) or 0),
    )
    assert entry.main(["--foreground", "--duration", "1"]) == 0
    assert calls[0][-2:] == ["--duration", "1"]
    assert "--until-close" not in calls[0]


def test_foreground_service_uses_supervisor(entry, monkeypatch):
    calls = []
    monkeypatch.setitem(
        sys.modules, "service", SimpleNamespace(main=lambda args: calls.append(args) or 0)
    )
    monkeypatch.setenv("COLLECTOR_SERVICE_ARGS", "--lead 600")
    assert entry.main(["--service", "--foreground", "--once"]) == 0
    assert calls == [["--lead", "600", "--once"]]


@pytest.mark.skipif(sys.platform != "win32", reason="Windows command line parser")
def test_windows_environment_argument_paths_preserve_spaces_and_backslashes(entry):
    assert entry.split_environment_args('--config="D:\\my data\\collector.yaml" --lead 600') == [
        "--config=D:\\my data\\collector.yaml",
        "--lead",
        "600",
    ]
