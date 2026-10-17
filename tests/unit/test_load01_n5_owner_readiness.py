from __future__ import annotations

from tests.unit._load01_n5_owner_resources import N5OwnerGroupRuntime


class _GateWithNoReadyApi:
    """Model the public DurableRiskGate surface: there is no ``ready`` API."""

    __slots__ = ()


class _CloseStack:
    def __init__(self) -> None:
        self.close_calls = 0

    def close(self) -> None:
        self.close_calls += 1


def test_owner_runtime_readiness_tracks_owned_lifecycle_without_gate_ready() -> None:
    runtime = object.__new__(N5OwnerGroupRuntime)
    runtime._closed = False
    runtime._close_facts = {}
    runtime._stack = _CloseStack()
    runtime.risk_gate = _GateWithNoReadyApi()

    assert runtime.ready is True
    runtime.close()
    assert runtime.ready is False
    runtime.close()
    assert runtime._stack.close_calls == 1
