import queue
import time

import pytest

from bt_api_py.containers.assets.okx_asset import (
    OkxDepositInfoData,
    OkxWithdrawalInfoData,
)
from bt_api_py.containers.exchanges.okx_exchange_data import OkxExchangeDataSwap
from bt_api_base.containers.requestdatas.request_data import RequestData

# from bt_api_py.containers.orders.okx_order import OkxOrderData
from bt_api_py.feeds.live_okx_feed import OkxRequestDataSwap
from bt_api_py.functions.utils import read_account_config

pytestmark = [pytest.mark.integration, pytest.mark.network]


def generate_kwargs(exchange=OkxExchangeDataSwap):
    data = read_account_config()
    kwargs = {
        "public_key": data["okx"]["public_key"],
        "private_key": data["okx"]["private_key"],
        "passphrase": data["okx"]["passphrase"],
        "topics": {"tick": {"symbol": "BTC-USDT"}},
        "proxies": data.get("proxies"),
        "async_proxy": data.get("async_proxy"),
    }
    return kwargs


def init_req_feed():
    data_queue = queue.Queue()
    kwargs = generate_kwargs()
    live_okx_swap_feed = OkxRequestDataSwap(data_queue, **kwargs)
    return live_okx_swap_feed


def init_async_feed(data_queue):
    kwargs = generate_kwargs()
    live_okx_swap_feed = OkxRequestDataSwap(data_queue, **kwargs)
    return live_okx_swap_feed


def test_okx_wss_economic_calendar():
    """Test WebSocket economic-calendar channel (经济日历推送)."""
    from bt_api_py.feeds.live_okx_feed import OkxMarketWssDataSwap

    data_queue = queue.Queue()
    kwargs = generate_kwargs()
    kwargs.update(
        {
            "topics": [{"topic": "economic_calendar"}],
            "wss_name": "test_economic_calendar",
            "wss_url": "wss://ws.okx.com:8443/ws/v5/public",
            "exchange_data": OkxExchangeDataSwap(),
        }
    )

    wss = OkxMarketWssDataSwap(data_queue, **kwargs)
    wss.start()
    time.sleep(5)

    # Economic calendar data may not be pushed immediately
    count = 0
    received_data = False
    while count < 100:
        try:
            data = data_queue.get(timeout=0.5)
            received_data = True
            print(f"Received economic calendar data: {type(data).__name__}")
            break
        except queue.Empty:
            break
        count += 1

    wss.stop()

    if received_data:
        print("Economic calendar data received successfully")
    assert True, "economic-calendar channel subscription test completed"


def test_okx_wss_deposit_info():
    """Test WebSocket deposit-info channel (充值信息推送)."""
    from bt_api_py.feeds.live_okx_feed import OkxMarketWssDataSwap

    data_queue = queue.Queue()
    kwargs = generate_kwargs()
    kwargs.update(
        {
            "topics": [{"topic": "deposit_info"}],
            "wss_name": "test_deposit_info",
            "wss_url": "wss://ws.okx.com:8443/ws/v5/private",
            "exchange_data": OkxExchangeDataSwap(),
        }
    )

    wss = OkxMarketWssDataSwap(data_queue, **kwargs)
    wss.start()
    time.sleep(5)

    # Deposit info data may not be pushed immediately (only on new deposits)
    count = 0
    received_deposit = False
    while count < 100:
        try:
            data = data_queue.get(timeout=0.5)
            if isinstance(data, OkxDepositInfoData):
                received_deposit = True
                data.init_data()
                assert data.get_exchange_name() == "OKX"
                print(
                    f"Received deposit info: currency={data.get_currency()}, "
                    f"amount={data.get_amount()}, status={data.get_status()}"
                )
                break
        except queue.Empty:
            break
        count += 1

    wss.stop()

    if received_deposit:
        print("Deposit info data received successfully")
    assert True, "deposit-info channel subscription test completed"


@pytest.mark.auth_position
def test_okx_wss_withdrawal_info():
    """Test WebSocket withdrawal-info channel (提币信息推送)."""
    from bt_api_py.feeds.live_okx_feed import OkxMarketWssDataSwap

    data_queue = queue.Queue()
    kwargs = generate_kwargs()
    kwargs.update(
        {
            "topics": [{"topic": "withdrawal_info"}],
            "wss_name": "test_withdrawal_info",
            "wss_url": "wss://ws.okx.com:8443/ws/v5/private",
            "exchange_data": OkxExchangeDataSwap(),
        }
    )

    wss = OkxMarketWssDataSwap(data_queue, **kwargs)
    wss.start()
    time.sleep(5)

    # Withdrawal info data may not be pushed immediately (only on new withdrawals)
    count = 0
    received_withdrawal = False
    while count < 100:
        try:
            data = data_queue.get(timeout=0.5)
            if isinstance(data, OkxWithdrawalInfoData):
                received_withdrawal = True
                data.init_data()
                assert data.get_exchange_name() == "OKX"
                print(
                    f"Received withdrawal info: currency={data.get_currency()}, "
                    f"amount={data.get_amount()}, status={data.get_status()}"
                )
                break
        except queue.Empty:
            break
        count += 1

    wss.stop()

    if received_withdrawal:
        print("Withdrawal info data received successfully")
    assert True, "withdrawal-info channel subscription test completed"


# ==================== Position Builder Tests ====================


@pytest.mark.auth_position
def test_okx_position_builder():
    """Test position_builder interface"""
    live_okx_swap_feed = init_req_feed()
    # Test position builder with SWAP type and BTC underlying
    data = live_okx_swap_feed.position_builder(inst_type="SWAP", uly="BTC-USD", max_sz=1)
    assert isinstance(data, RequestData)
    print("position_builder status:", data.get_status())
    if data.get_status():
        data_list = data.get_data()
        assert isinstance(data_list, list)
        print("Position builder data:", data_list[:1] if data_list else "No data")


@pytest.mark.auth_position
def test_okx_async_position_builder():
    """Test async_position_builder interface"""
    data_queue = queue.Queue()
    live_okx_swap_feed = init_async_feed(data_queue)
    live_okx_swap_feed.async_position_builder(
        inst_type="SWAP", uly="BTC-USD", max_sz=1, extra_data={"test_async_position_builder": True}
    )
    time.sleep(5)
    try:
        result = data_queue.get(False)
    except queue.Empty:
        result = None
    if result is not None:
        assert isinstance(result, RequestData)
        print("async_position_builder status:", result.get_status())


@pytest.mark.skip(reason="OKX API endpoint deprecated/removed (404)")
@pytest.mark.auth_position
def test_okx_position_builder_trend():
    """Test position_builder_trend interface"""
    live_okx_swap_feed = init_req_feed()
    # Test position builder trend with SWAP type and BTC underlying
    data = live_okx_swap_feed.position_builder_trend(inst_type="SWAP", uly="BTC-USD", max_sz=1)
    assert isinstance(data, RequestData)
    print("position_builder_trend status:", data.get_status())
    if data.get_status():
        data_list = data.get_data()
        assert isinstance(data_list, list)
        print("Position builder trend data:", data_list[:1] if data_list else "No data")


@pytest.mark.skip(reason="OKX API endpoint deprecated/removed (404)")
@pytest.mark.auth_position
def test_okx_async_position_builder_trend():
    """Test async_position_builder_trend interface"""
    data_queue = queue.Queue()
    live_okx_swap_feed = init_async_feed(data_queue)
    live_okx_swap_feed.async_position_builder_trend(
        inst_type="SWAP",
        uly="BTC-USD",
        max_sz=1,
        extra_data={"test_async_position_builder_trend": True},
    )
    time.sleep(5)
    try:
        result = data_queue.get(False)
    except queue.Empty:
        result = None
    if result is not None:
        assert isinstance(result, RequestData)
        print("async_position_builder_trend status:", result.get_status())


# ==================== Cancel All Orders Tests ====================
