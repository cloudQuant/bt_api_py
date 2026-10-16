"""Private snapshots no longer flow through the retired ZMQ backend."""

from __future__ import annotations

import pytest

from bt_api_py._contracts.errors import LegacyForwardingRetiredError
from bt_api_py._contracts.models import ForwardingConfig
from bt_api_py.forwarding.btapi_backend import ZmqBtApiBackend


def test_private_snapshot_backend_requires_migration_to_service_account_read() -> None:
    config = ForwardingConfig(
        command_endpoint="inproc://commands",
        market_endpoint="inproc://market",
        private_endpoint="inproc://private",
        account_id="acct-1",
        strategy_id="strategy-1",
    )

    with pytest.raises(LegacyForwardingRetiredError) as excinfo:
        ZmqBtApiBackend(config)

    assert "ServiceHost" in excinfo.value.detail
    assert "service.order_entry" in excinfo.value.detail
    assert "service.account_read" in excinfo.value.detail
