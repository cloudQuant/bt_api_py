"""Retirement boundary for the legacy OrderRouter audit path.

AuditLogger remains an independent utility, but writes now pass through the
supported ServiceExecutionSession and ServiceHost ``service.order_entry``
chain.  Positive service-path order coverage lives in
``tests/bt_api_contract/test_execution_session.py``.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from bt_api_py._contracts.errors import LegacyForwardingRetiredError
from bt_api_py.brokers.mock import MockBrokerAdapter
from bt_api_py.forwarding.memory import InMemoryForwardingBus
from bt_api_py.forwarding.router import OrderRouter
from bt_api_py.security_compliance.core.audit_logger import AuditLogger


class _CountingAdapter(MockBrokerAdapter):
    def __init__(self) -> None:
        super().__init__()
        self.connect_calls = 0
        self.place_calls = 0
        self.cancel_calls = 0

    async def connect(self) -> bool:
        self.connect_calls += 1
        return await super().connect()

    async def place_order(self, request):
        self.place_calls += 1
        return await super().place_order(request)

    async def cancel_order(self, request):
        self.cancel_calls += 1
        return await super().cancel_order(request)


def test_order_router_retires_before_adapter_bus_or_audit_side_effects(tmp_path: Path) -> None:
    adapter = _CountingAdapter()
    bus = InMemoryForwardingBus()
    audit_path = tmp_path / "audit.jsonl"
    audit = AuditLogger(log_file=audit_path)

    with pytest.raises(LegacyForwardingRetiredError) as excinfo:
        OrderRouter(
            adapter,
            bus=bus,
            audit_logger=audit,
            write_enabled=True,
            write_scope=("SIM", "SPOT", "paper"),
        )

    assert "ServiceExecutionSession" in excinfo.value.detail
    assert "service.order_entry" in excinfo.value.detail
    assert adapter.connect_calls == 0
    assert adapter.place_calls == 0
    assert adapter.cancel_calls == 0
    assert adapter.connected is False
    assert bus.stats()["command_handler_registered"] is False
    assert audit_path.exists() is False
