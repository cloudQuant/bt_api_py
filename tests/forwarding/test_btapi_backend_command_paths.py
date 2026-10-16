"""The old ZMQ BtApi backend has no client or command-dispatch path."""

from __future__ import annotations

import pytest

from bt_api_py._contracts.errors import LegacyForwardingRetiredError
from bt_api_py.forwarding.btapi_backend import ZmqBtApiBackend


def test_legacy_backend_rejects_before_config_or_client_setup() -> None:
    with pytest.raises(LegacyForwardingRetiredError) as excinfo:
        ZmqBtApiBackend(object())

    assert excinfo.value.target == "bt_api_py.forwarding.btapi_backend.ZmqBtApiBackend"
    assert "ServiceHost" in excinfo.value.detail
    assert "service.order_entry" in excinfo.value.detail
