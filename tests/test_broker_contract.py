import pytest

from bt_api_py.brokers.errors import BrokerError, BrokerErrorCode
from bt_api_py.brokers.gateway_bridge import GatewayBridgeAdapter
from bt_api_py.brokers.mock import MockBrokerAdapter
from bt_api_py.brokers.types import CancelOrderRequest, OrderRequest, OrderSnapshot
from bt_api_py.testing.contract_cases import run_broker_contract_cases


@pytest.mark.asyncio
async def test_mock_broker_adapter_passes_contract_cases() -> None:
    adapter = MockBrokerAdapter()
    report = await run_broker_contract_cases(adapter)

    assert report["passed"] is True
    assert report["method_count"] >= 12
    assert report["capabilities"]["supports_native_paper"] is True


@pytest.mark.asyncio
async def test_contract_report_exposes_case_results() -> None:
    report = await run_broker_contract_cases(MockBrokerAdapter())

    assert "cases" in report
    assert report["cases"][0]["name"] == "connect"
    assert report["cases"][0]["passed"] is True


@pytest.mark.asyncio
async def test_gateway_bridge_write_paths_require_feature_flag(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("BT_API_PY_BRIDGE_ENABLE_WRITE", raising=False)
    adapter = GatewayBridgeAdapter(gateway_service={"health": "ok"})

    with pytest.raises(BrokerError) as exc_info:
        await adapter.place_order(
            OrderRequest(account_id="acct", symbol="RB2510", side="buy", quantity=1)
        )

    assert exc_info.value.code == BrokerErrorCode.NOT_SUPPORTED


def test_gateway_bridge_capabilities_do_not_advertise_unimplemented_writes(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("BT_API_PY_BRIDGE_ENABLE_WRITE", "1")
    adapter = GatewayBridgeAdapter(gateway_service={"health": "ok"})

    capabilities = adapter.capabilities()

    assert capabilities.supports_order_submit is False
    assert capabilities.supports_order_cancel is False
    assert capabilities.supports_destructive_write is False


@pytest.mark.asyncio
async def test_gateway_bridge_write_paths_raise_structured_error_when_flag_is_enabled(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("BT_API_PY_BRIDGE_ENABLE_WRITE", "1")
    adapter = GatewayBridgeAdapter(gateway_service={"health": "ok"})

    with pytest.raises(BrokerError) as place_exc:
        await adapter.place_order(
            OrderRequest(account_id="acct", symbol="RB2510", side="buy", quantity=1)
        )
    with pytest.raises(BrokerError) as cancel_exc:
        await adapter.cancel_order(CancelOrderRequest(account_id="acct", order_id="order-1"))

    assert place_exc.value.code == BrokerErrorCode.NOT_SUPPORTED
    assert cancel_exc.value.code == BrokerErrorCode.NOT_SUPPORTED


@pytest.mark.asyncio
async def test_mock_broker_adapter_get_account_rejects_unknown_account() -> None:
    adapter = MockBrokerAdapter()

    with pytest.raises(BrokerError) as exc_info:
        await adapter.get_account("missing")

    assert exc_info.value.code == BrokerErrorCode.AUTH_FAILED


@pytest.mark.asyncio
async def test_mock_broker_adapter_rejects_non_positive_quantity() -> None:
    adapter = MockBrokerAdapter()
    await adapter.connect()

    with pytest.raises(BrokerError) as exc_info:
        await adapter.place_order(
            OrderRequest(account_id="paper", symbol="RB2510", side="buy", quantity=0)
        )

    assert exc_info.value.code == BrokerErrorCode.INVALID_ORDER


@pytest.mark.asyncio
async def test_mock_broker_adapter_rejects_buy_when_cash_is_insufficient() -> None:
    adapter = MockBrokerAdapter(initial_cash=10.0)
    await adapter.connect()

    with pytest.raises(BrokerError) as exc_info:
        await adapter.place_order(
            OrderRequest(account_id="paper", symbol="RB2510", side="buy", quantity=1, price=3500.0)
        )

    assert exc_info.value.code == BrokerErrorCode.INSUFFICIENT_FUNDS


@pytest.mark.asyncio
async def test_mock_broker_adapter_updates_existing_position_on_repeat_order() -> None:
    adapter = MockBrokerAdapter()
    await adapter.connect()

    first = await adapter.place_order(
        OrderRequest(account_id="paper", symbol="RB2510", side="buy", quantity=1, price=3500.0)
    )
    second = await adapter.place_order(
        OrderRequest(account_id="paper", symbol="RB2510", side="buy", quantity=2, price=3600.0)
    )

    position = adapter.positions["RB2510"]

    assert first.status == "filled"
    assert second.status == "filled"
    assert position.quantity == 3
    assert position.market_price == 3600.0
    assert position.average_price == (1 * 3500.0 + 2 * 3600.0) / 3  # 加权平均


@pytest.mark.asyncio
async def test_mock_broker_adapter_cancel_order_rejects_missing_order() -> None:
    adapter = MockBrokerAdapter()
    await adapter.connect()

    with pytest.raises(BrokerError) as exc_info:
        await adapter.cancel_order(CancelOrderRequest(account_id="paper", order_id="missing"))

    assert exc_info.value.code == BrokerErrorCode.ORDER_NOT_FOUND


@pytest.mark.asyncio
async def test_mock_broker_adapter_cancel_order_returns_filled_order_unchanged() -> None:
    adapter = MockBrokerAdapter()
    await adapter.connect()

    order = await adapter.place_order(
        OrderRequest(account_id="paper", symbol="RB2510", side="buy", quantity=1, price=3500.0)
    )

    cancelled = await adapter.cancel_order(
        CancelOrderRequest(account_id="paper", order_id=order.order_id)
    )

    assert cancelled.status == "filled"
    assert cancelled.order_id == order.order_id


@pytest.mark.asyncio
async def test_mock_broker_adapter_cancel_order_marks_pending_order_cancelled() -> None:
    adapter = MockBrokerAdapter()
    await adapter.connect()
    pending_order = OrderSnapshot(
        order_id="pending-1",
        account_id="paper",
        symbol="RB2510",
        side="buy",
        quantity=1,
        status="submitted",
        price=3500.0,
    )
    adapter.orders[pending_order.order_id] = pending_order

    cancelled = await adapter.cancel_order(
        CancelOrderRequest(account_id="paper", order_id=pending_order.order_id)
    )

    assert cancelled.status == "cancelled"
    assert adapter.orders[pending_order.order_id].status == "cancelled"


@pytest.mark.asyncio
async def test_mock_broker_adapter_keeps_average_price_when_position_returns_to_zero() -> None:
    adapter = MockBrokerAdapter()
    await adapter.connect()

    await adapter.place_order(
        OrderRequest(account_id="paper", symbol="RB2510", side="buy", quantity=1, price=3500.0)
    )
    await adapter.place_order(
        OrderRequest(account_id="paper", symbol="RB2510", side="sell", quantity=1, price=3600.0)
    )

    position = adapter.positions["RB2510"]

    assert position.quantity == 0
    assert position.market_price == 3600.0
    assert position.average_price == 3500.0
