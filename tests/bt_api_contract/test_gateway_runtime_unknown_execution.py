"""Gateway write outcomes survive wire serialization and BtApi normalization."""

from decimal import Decimal
from types import SimpleNamespace
from unittest.mock import Mock

import pytest
from bt_api_base.gateway.config import GatewayConfig
from bt_api_base.gateway.runtime import GatewayRuntime

from bt_api_py import BtApi
from bt_api_py._contracts.models import (
    CancelOrderRequest,
    ForwardingConfig,
    OrderRequest,
    OrderType,
    Side,
)
from bt_api_py.forwarding.schema import CommandAck, OrderCommand


@pytest.fixture
def pipeline(monkeypatch):
    monkeypatch.setattr("bt_api_py.bt_api._ensure_plugins_loaded", lambda: None)
    runtime = GatewayRuntime(
        GatewayConfig(exchange_type="MT5", asset_type="FX", account_id="demo", enable_trading=True)
    )

    class Client:
        def _send_command_sync(self, command):
            ack = runtime._handle_command(OrderCommand.from_dict(command.to_dict()))
            return CommandAck.from_dict(ack.to_dict())

    api = BtApi(
        {"MT5___FX": {}},
        debug=False,
        transport_mode="zmq",
        forwarding_config=ForwardingConfig(
            command_endpoint="inproc://c",
            market_endpoint="inproc://m",
            private_endpoint="inproc://p",
            account_id="demo",
            strategy_id="test",
        ),
    )
    api._backend._client = Client()
    runtime.adapter = SimpleNamespace(place_order=Mock(), cancel_order=Mock())
    return api, runtime


def invoke(api, operation):
    if operation == "place_order":
        return api.make_order(
            "MT5___FX",
            OrderRequest(
                symbol="EURUSD",
                side=Side.BUY,
                order_type=OrderType.MARKET,
                quantity=Decimal(".01"),
                quantity_unit="lots",
                account_id="demo",
                client_order_id="123456789012",
            ),
            normalized=True,
        )
    return api.cancel_order(
        "MT5___FX",
        CancelOrderRequest(
            symbol="EURUSD",
            account_id="demo",
            order_id="order-9",
            client_order_id="123456789012",
        ),
        normalized=True,
    )


@pytest.mark.parametrize("operation", ["place_order", "cancel_order"])
@pytest.mark.parametrize("error", [TimeoutError, ValueError, RuntimeError])
def test_adapter_exception_after_dispatch_is_unknown(pipeline, operation, error):
    api, runtime = pipeline
    method = getattr(runtime.adapter, operation)
    method.side_effect = error("url?signature=NEVER_PRINT")
    result = invoke(api, operation)
    assert result["execution_unknown"] is True
    assert result["terminal_confirmed"] is False and result["definite_reject"] is False
    assert result["status"] == "submitted" and result["client_order_id"] == "123456789012"
    assert method.call_count == 1
    assert "NEVER_PRINT" not in str(result) and "NEVER_PRINT" not in str(runtime._recent_errors)


@pytest.mark.parametrize("operation", ["place_order", "cancel_order"])
@pytest.mark.parametrize("status", ["rejected", "error"])
def test_explicit_adapter_rejection_remains_definite(pipeline, operation, status):
    api, runtime = pipeline
    getattr(runtime.adapter, operation).return_value = {
        "status": status,
        "error": "invalid request",
    }
    result = invoke(api, operation)
    assert result["status"] == "rejected" and result["definite_reject"] is True
    assert result["execution_unknown"] is False and result["terminal_confirmed"] is True


def test_explicit_unknown_takes_precedence_over_adapter_error(pipeline):
    api, runtime = pipeline
    runtime.adapter.place_order.return_value = {
        "status": "error",
        "execution_unknown": True,
    }
    result = invoke(api, "place_order")
    assert result["execution_unknown"] and not result["definite_reject"]


@pytest.mark.parametrize(
    "operation,status", [("place_order", "accepted"), ("cancel_order", "canceled")]
)
def test_normal_adapter_outcome_stays_normal(pipeline, operation, status):
    api, runtime = pipeline
    getattr(runtime.adapter, operation).return_value = {
        "status": status,
        "order_id": "order-9",
    }
    result = invoke(api, operation)
    assert result["status"] == status and result["execution_unknown"] is False
    assert result["order_id"] == "order-9"


def test_logging_failure_cannot_replace_unknown_result(pipeline, monkeypatch):
    api, runtime = pipeline
    logger = SimpleNamespace(warning=Mock(side_effect=TypeError("logger failed")))
    monkeypatch.setattr("bt_api_base.gateway.runtime.logger", logger)
    runtime.adapter.place_order.side_effect = TimeoutError("uncertain")
    result = invoke(api, "place_order")
    assert result["execution_unknown"] and not result["terminal_confirmed"]
    assert len(logger.warning.call_args.args) == 1
