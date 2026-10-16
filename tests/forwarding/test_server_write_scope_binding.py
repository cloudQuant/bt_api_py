"""The retired SDK forwarding runtimes cannot bind or dispatch adapter writes."""

from __future__ import annotations

import pytest

from bt_api_py._contracts.errors import LegacyForwardingRetiredError
from bt_api_py.brokers.mock import MockBrokerAdapter
from bt_api_py.forwarding.service import ForwardingRuntime, ZmqForwardingRuntime


class CountingAdapter(MockBrokerAdapter):
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


def test_embedded_forwarding_runtime_fails_before_adapter_setup() -> None:
    adapter = CountingAdapter()

    with pytest.raises(LegacyForwardingRetiredError) as excinfo:
        ForwardingRuntime(adapter)

    assert excinfo.value.target == "bt_api_py.forwarding.service.ForwardingRuntime"
    assert "ServiceHost" in excinfo.value.detail
    assert "service.order_entry" in excinfo.value.detail
    assert adapter.connect_calls == 0
    assert adapter.place_calls == 0
    assert adapter.cancel_calls == 0


def test_zmq_forwarding_runtime_fails_before_adapter_or_transport_setup() -> None:
    adapter = CountingAdapter()

    with pytest.raises(LegacyForwardingRetiredError) as excinfo:
        ZmqForwardingRuntime(
            adapter,
            market_endpoint="tcp://127.0.0.1:7001",
            command_endpoint="tcp://127.0.0.1:7002",
            private_endpoint="tcp://127.0.0.1:7003",
            enable_trading=True,
        )

    assert excinfo.value.target == "bt_api_py.forwarding.service.ZmqForwardingRuntime"
    assert "ServiceHost" in excinfo.value.detail
    assert "service.order_entry" in excinfo.value.detail
    assert adapter.connect_calls == 0
    assert adapter.place_calls == 0
    assert adapter.cancel_calls == 0
