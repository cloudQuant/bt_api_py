"""Synthetic pre-existing legacy objects; never a supported construction path."""
from __future__ import annotations

import threading

import pytest

from bt_api_py.service_execution_assembly import (
    ServiceExecutionAssembly,
    ServiceExecutionAssemblyError,
    ServiceExecutionRoutePlan,
)
from bt_api_py.service_runtime import ServiceClientRuntime


CODE = "LEGACY_SERVICE_EXECUTION_RETIRED"
_OWNER_ATTRIBUTE = "_backtrader_service_execution_session_owner"


class _SyntheticRuntime(ServiceClientRuntime):
    def __init__(self):
        self.calls = []
        self.current_state = "new"
        self._unresolved_unregister_count = 0
        self._unregister_uncertainties = {}
        self._cleanup_failures = 0

    @property
    def state(self):
        self.calls.append("state")
        return self.current_state

    def next_maintenance_delay(self):
        self.calls.append("next_maintenance_delay")
        return None

    def start(self):
        self.calls.append("start")
        self.current_state = "registered"

    def maintain(self):
        self.calls.append("maintain")

    def close(self):
        self.calls.append("close")
        self.current_state = "closed"


def _synthetic_old_assembly(state):
    # Testing retained lifecycle only; bypassing retirement is unsupported.
    assembly = object.__new__(ServiceExecutionAssembly)
    runtime = _SyntheticRuntime()
    assembly._owner_thread_id = threading.get_ident()
    assembly._runtime = runtime
    assembly._routes = ServiceExecutionRoutePlan(
        "broker.orders", "writer", "broker.order_query", "query"
    )
    assembly._owned_runtimes = (runtime,)
    assembly._claimed = state != "unclaimed"
    assembly._released = False
    assembly._state = state
    assembly._start_attempted_ids = {id(runtime)} if state == "registered" else set()
    assembly._close_attempted = False
    assembly._runtime_facts = ()
    assembly._close_errors = ()
    if assembly._claimed:
        setattr(runtime, _OWNER_ATTRIBUTE, assembly)
    if state == "registered":
        runtime.current_state = "registered"
    return assembly, runtime


def test_unclaimed_synthetic_legacy_assembly_cannot_claim_runtime():
    assembly, runtime = _synthetic_old_assembly("unclaimed")
    with pytest.raises(ServiceExecutionAssemblyError) as exc:
        assembly.claim_owned_runtimes()
    assert exc.value.code == CODE
    assert runtime.calls == []
    assert not hasattr(runtime, _OWNER_ATTRIBUTE)
    assert assembly.state == "unclaimed"


def test_claimed_synthetic_legacy_assembly_cannot_start_runtime():
    assembly, runtime = _synthetic_old_assembly("claimed")
    with pytest.raises(ServiceExecutionAssemblyError) as exc:
        assembly.start()
    assert exc.value.code == CODE
    assert runtime.calls == []
    assert getattr(runtime, _OWNER_ATTRIBUTE) is assembly
    assert assembly.state == "claimed"
    assert not assembly.start_attempted


def test_registered_synthetic_legacy_assembly_can_maintain_and_close():
    assembly, runtime = _synthetic_old_assembly("registered")
    assembly.maintain()
    assert runtime.calls == ["maintain"]
    assembly.close()
    assert runtime.calls == ["maintain", "close", "state"]
    assert assembly.state == "closed"
    assert assembly.runtime_facts[0].state == "closed"


def test_claimed_unstarted_synthetic_legacy_assembly_can_release_without_close():
    assembly, runtime = _synthetic_old_assembly("claimed")
    assert assembly.release_unstarted() == ()
    assert runtime.calls == []
    assert not hasattr(runtime, _OWNER_ATTRIBUTE)
    assert assembly.state == "released"


def test_claimed_unstarted_synthetic_legacy_assembly_can_explicitly_close():
    assembly, runtime = _synthetic_old_assembly("claimed")
    assembly.close()
    assert runtime.calls == ["close", "state"]
    assert assembly.state == "closed"
