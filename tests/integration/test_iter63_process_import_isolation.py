"""INV63-12: a Gateway process and strategy processes never load the Risk or Execution packages.

Risk and Execution are separate services. Neither the Gateway process nor a strategy
process imports ``bt_api_risk`` or ``bt_api_execution``, and without a selected monitor
neither imports ``bt_api_monitor``. The plan asks for this to be observed over the whole
life of isolated processes, not only at import time: each process of an existing
process scenario reports, when its work is over, which of the watched packages its
interpreter holds.

Two Gateway processes are observed: a synthetic one with strategy processes (real
Registry, real journal, CURVE over loopback, the SDK's strategy client), and one built
from the CTP trading profile with a fake native session. No venue and no account.
"""

import functools
import importlib
import importlib.util
import multiprocessing
import os
import queue
import sys

import pytest

from tests.integration import test_iter63_async_market_bootstrap_service as ctp_scenario
from tests.integration import test_iter63_gateway_client_processes as scenario
from tests.integration.test_iter63_ctp_gateway_joint_service import _gateway as _run_ctp_gateway
from tests.integration.test_iter63_gateway_client_processes import (
    _gateway_process as _run_gateway,
)
from tests.integration.test_iter63_gateway_client_processes import (
    _strategy_process as _run_strategy,
)

WATCHED = ("bt_api_execution", "bt_api_monitor", "bt_api_risk")


def _loaded():
    return sorted({name.split(".")[0] for name in sys.modules} & set(WATCHED))


def _observed_gateway(reports, *args):
    try:
        _run_gateway(*args)
    finally:
        reports.put(("gateway", os.getpid(), _loaded()))


def _observed_strategy(reports, *args):
    try:
        _run_strategy(*args)
    finally:
        reports.put(("strategy", os.getpid(), _loaded()))


def _observed_ctp_gateway(reports, *args):
    try:
        _run_ctp_gateway(*args)
    finally:
        reports.put(("ctp-gateway", os.getpid(), _loaded()))


def _control_process(reports):
    # Shows that the observation sees a load: this process imports every watched
    # package that can be imported here and reports the same way.
    for name in WATCHED:
        if importlib.util.find_spec(name) is not None:
            importlib.import_module(name)
    reports.put(("control", os.getpid(), _loaded()))


def _drain(reports):
    seen = []
    while True:
        try:
            seen.append(reports.get(timeout=2))
        except queue.Empty:
            return seen


def test_gateway_and_strategy_processes_load_no_risk_execution_or_monitor(tmp_path, monkeypatch):
    ctx = multiprocessing.get_context("spawn")
    reports = ctx.Queue()
    monkeypatch.setattr(scenario, "_gateway_process", functools.partial(_observed_gateway, reports))
    monkeypatch.setattr(scenario, "_strategy_process", functools.partial(_observed_strategy, reports))
    # One Gateway process, strategy processes a and b placing and cancelling through it,
    # b stopped by the operator and replaced by a new process, then an orderly stop.
    scenario.test_two_strategy_processes_share_only_gateway_owner(tmp_path)
    seen = _drain(reports)
    roles = sorted(role for role, _, _ in seen)
    assert roles == ["gateway", "strategy", "strategy", "strategy"], seen
    assert len({pid for _, pid, _ in seen}) == 4 and os.getpid() not in {pid for _, pid, _ in seen}
    assert all(loaded == [] for _, _, loaded in seen), seen


def test_ctp_profile_gateway_process_loads_no_risk_execution_or_monitor(tmp_path, monkeypatch):
    ctx = multiprocessing.get_context("spawn")
    reports = ctx.Queue()
    monkeypatch.setattr(ctp_scenario, "_gateway", functools.partial(_observed_ctp_gateway, reports))
    running = ctp_scenario.async_gateway.__wrapped__(tmp_path)
    state = next(running)  # the Gateway process built from the CTP trading profile serves
    try:
        # A strategy registers and reserves one order through it, then everything stops.
        session = ctp_scenario.direct_only(state)
        assert ctp_scenario.reserve(session, "isolation-1").outcome == "OBSERVED"
    finally:
        with pytest.raises(StopIteration):
            next(running)  # orderly stop; the scenario asserts exit code 0 and a clean close
    seen = _drain(reports)
    assert [(role, loaded) for role, _, loaded in seen] == [("ctp-gateway", [])], seen
    assert seen[0][1] == state["child"].pid != os.getpid()


def test_the_observation_reports_a_loaded_package():
    ctx = multiprocessing.get_context("spawn")
    reports = ctx.Queue()
    child = ctx.Process(target=_control_process, args=(reports,))
    child.start()
    try:
        role, pid, loaded = reports.get(timeout=30)
    finally:
        child.join(15)
    importable = sorted(name for name in WATCHED if importlib.util.find_spec(name) is not None)
    assert role == "control" and pid == child.pid and child.exitcode == 0
    assert loaded == importable
    # Without at least one importable watched package the first test could not fail.
    assert importable, "none of the watched packages is installed here"
