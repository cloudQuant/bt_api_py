"""The retired gateway client cannot bypass the authenticated service writer.

The real spawned ServiceHost path now owns durable ACKED, REJECTED and UNKNOWN
outcome coverage in ``tests.unit.test_service_order_entry_integration`` and
``tests.unit.test_service_execution_integration``.  This file guards the old
wire client's public constructor instead of replaying the removed private
``GatewayRuntime._handle_command`` pipeline.
"""

from unittest.mock import Mock

import pytest

from bt_api_py._contracts.errors import LegacyForwardingRetiredError
from bt_api_py._gateway_wire import GatewayWireClient, GatewayWireConfig


def test_gateway_wire_client_constructor_fails_closed_before_dispatch() -> None:
    dispatch = Mock(side_effect=AssertionError("retired gateway dispatch"))
    config = GatewayWireConfig(
        command_endpoint="inproc://command",
        market_endpoint="inproc://market",
        private_endpoint="inproc://private",
        account_id="account-a",
        strategy_id="strategy-a",
        dispatch=dispatch,
    )

    with pytest.raises(LegacyForwardingRetiredError) as exc_info:
        GatewayWireClient(config)

    assert exc_info.value.target == "GatewayWireClient"
    assert "ServiceExecutionSession" in exc_info.value.detail
    assert "service.order_entry" in exc_info.value.detail
    dispatch.assert_not_called()
