import queue
import time

import pytest

pytestmark = [pytest.mark.integration, pytest.mark.network]

from bt_api_py.containers.exchanges.okx_exchange_data import OkxExchangeDataSwap
from bt_api_py.containers.requestdatas.request_data import RequestData

# from bt_api_py.containers.orders.okx_order import OkxOrderData
from bt_api_py.feeds.live_okx_feed import OkxRequestDataSwap
from bt_api_py.functions.utils import read_account_config


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


@pytest.mark.auth_account
def test_okx_copytrading_get_config():
    """Test getting copy trading account configuration"""
    live_okx_swap_feed = init_req_feed()
    data = live_okx_swap_feed.copytrading_get_config()
    assert isinstance(data, RequestData)
    print("copytrading_get_config status:", data.get_status())


def test_okx_copytrading_get_copy_settings():
    """Test getting copy trading settings"""
    live_okx_swap_feed = init_req_feed()
    data = live_okx_swap_feed.copytrading_get_copy_settings()
    assert isinstance(data, RequestData)
    print("copytrading_get_copy_settings status:", data.get_status())


@pytest.mark.skip(reason="OKX API endpoint deprecated/removed (404)")
def test_okx_copytrading_get_copy_trading_configuration():
    """Test getting copy trading configuration"""
    live_okx_swap_feed = init_req_feed()
    data = live_okx_swap_feed.copytrading_get_copy_trading_configuration()
    assert isinstance(data, RequestData)
    print("copytrading_get_copy_trading_configuration status:", data.get_status())


def test_okx_copytrading_get_instruments():
    """Test getting copy trading leading instruments"""
    live_okx_swap_feed = init_req_feed()
    data = live_okx_swap_feed.copytrading_get_instruments()
    assert isinstance(data, RequestData)
    print("copytrading_get_instruments status:", data.get_status())


@pytest.mark.auth_position
def test_okx_copytrading_get_current_subpositions():
    """Test getting existing lead positions"""
    live_okx_swap_feed = init_req_feed()
    data = live_okx_swap_feed.copytrading_get_current_subpositions()
    assert isinstance(data, RequestData)
    print("copytrading_get_current_subpositions status:", data.get_status())


@pytest.mark.auth_position
def test_okx_copytrading_get_subpositions_history():
    """Test getting lead position history"""
    live_okx_swap_feed = init_req_feed()
    data = live_okx_swap_feed.copytrading_get_subpositions_history()
    assert isinstance(data, RequestData)
    print("copytrading_get_subpositions_history status:", data.get_status())


def test_okx_copytrading_get_batch_leverage_info():
    """Test getting my lead traders"""
    live_okx_swap_feed = init_req_feed()
    data = live_okx_swap_feed.copytrading_get_batch_leverage_info()
    assert isinstance(data, RequestData)
    print("copytrading_get_batch_leverage_info status:", data.get_status())


def test_okx_copytrading_get_profit_sharing_details():
    """Test getting profit sharing details"""
    live_okx_swap_feed = init_req_feed()
    data = live_okx_swap_feed.copytrading_get_profit_sharing_details()
    assert isinstance(data, RequestData)
    print("copytrading_get_profit_sharing_details status:", data.get_status())


def test_okx_copytrading_get_total_profit_sharing():
    """Test getting total profit sharing"""
    live_okx_swap_feed = init_req_feed()
    data = live_okx_swap_feed.copytrading_get_total_profit_sharing()
    assert isinstance(data, RequestData)
    print("copytrading_get_total_profit_sharing status:", data.get_status())


def test_okx_copytrading_get_unrealized_profit_sharing_details():
    """Test getting unrealized profit sharing details"""
    live_okx_swap_feed = init_req_feed()
    data = live_okx_swap_feed.copytrading_get_unrealized_profit_sharing_details()
    assert isinstance(data, RequestData)
    print("copytrading_get_unrealized_profit_sharing_details status:", data.get_status())


def test_okx_copytrading_get_total_unrealized_profit_sharing():
    """Test getting total unrealized profit sharing"""
    live_okx_swap_feed = init_req_feed()
    data = live_okx_swap_feed.copytrading_get_total_unrealized_profit_sharing()
    assert isinstance(data, RequestData)
    print("copytrading_get_total_unrealized_profit_sharing status:", data.get_status())


def test_okx_copytrading_public_lead_traders():
    """Test getting lead trader ranks (public)"""
    live_okx_swap_feed = init_req_feed()
    data = live_okx_swap_feed.copytrading_public_lead_traders(inst_type="SWAP", limit="10")
    assert isinstance(data, RequestData)
    print("copytrading_public_lead_traders status:", data.get_status())


def test_okx_copytrading_public_stats():
    """Test getting lead trader stats (public)"""
    live_okx_swap_feed = init_req_feed()
    # Using a sample copy_inst_id format (users should replace with actual ID)
    data = live_okx_swap_feed.copytrading_public_stats(copy_inst_id="BTC-USDT")
    assert isinstance(data, RequestData)
    print("copytrading_public_stats status:", data.get_status())


@pytest.mark.auth_account
def test_async_copytrading_get_config():
    """Test async getting copy trading account configuration"""
    data_queue = queue.Queue()
    live_okx_swap_feed = init_async_feed(data_queue)
    live_okx_swap_feed.async_copytrading_get_config(extra_data={"test_async_copytrading": True})
    time.sleep(5)
    try:
        result = data_queue.get(False)
    except queue.Empty:
        result = None
    if result is not None:
        assert isinstance(result, RequestData)
        print("async_copytrading_get_config status:", result.get_status())


def test_async_copytrading_get_copy_settings():
    """Test async getting copy trading settings"""
    data_queue = queue.Queue()
    live_okx_swap_feed = init_async_feed(data_queue)
    live_okx_swap_feed.async_copytrading_get_copy_settings(
        extra_data={"test_async_copytrading": True}
    )
    time.sleep(5)
    try:
        result = data_queue.get(False)
    except queue.Empty:
        result = None
    if result is not None:
        assert isinstance(result, RequestData)
        print("async_copytrading_get_copy_settings status:", result.get_status())


def test_async_copytrading_get_instruments():
    """Test async getting copy trading leading instruments"""
    data_queue = queue.Queue()
    live_okx_swap_feed = init_async_feed(data_queue)
    live_okx_swap_feed.async_copytrading_get_instruments(
        extra_data={"test_async_copytrading": True}
    )
    time.sleep(5)
    try:
        result = data_queue.get(False)
    except queue.Empty:
        result = None
    if result is not None:
        assert isinstance(result, RequestData)
        print("async_copytrading_get_instruments status:", result.get_status())


@pytest.mark.auth_position
def test_async_copytrading_get_current_subpositions():
    """Test async getting existing lead positions"""
    data_queue = queue.Queue()
    live_okx_swap_feed = init_async_feed(data_queue)
    live_okx_swap_feed.async_copytrading_get_current_subpositions(
        extra_data={"test_async_copytrading": True}
    )
    time.sleep(5)
    try:
        result = data_queue.get(False)
    except queue.Empty:
        result = None
    if result is not None:
        assert isinstance(result, RequestData)
        print("async_copytrading_get_current_subpositions status:", result.get_status())


def test_async_copytrading_public_lead_traders():
    """Test async getting lead trader ranks (public)"""
    data_queue = queue.Queue()
    live_okx_swap_feed = init_async_feed(data_queue)
    live_okx_swap_feed.async_copytrading_public_lead_traders(
        inst_type="SWAP", limit="10", extra_data={"test_async_copytrading": True}
    )
    time.sleep(5)
    try:
        result = data_queue.get(False)
    except queue.Empty:
        result = None
    if result is not None:
        assert isinstance(result, RequestData)
        print("async_copytrading_public_lead_traders status:", result.get_status())
