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


def test_okx_get_system_status():
    """Test get_system_status interface"""
    live_okx_swap_feed = init_req_feed()
    data = live_okx_swap_feed.get_system_status()
    assert isinstance(data, RequestData)
    print("get_system_status status:", data.get_status())
    if data.get_status():
        data_list = data.get_data()
        assert isinstance(data_list, list)
        print("System status data:", data_list[:2] if data_list else "No data")


def test_okx_async_get_system_status():
    """Test async_get_system_status interface"""
    data_queue = queue.Queue()
    live_okx_swap_feed = init_async_feed(data_queue)
    live_okx_swap_feed.async_get_system_status(extra_data={"test_async_system_status": True})
    time.sleep(5)
    try:
        result = data_queue.get(False)
    except queue.Empty:
        result = None
    if result is not None:
        assert isinstance(result, RequestData)
        print("async_get_system_status status:", result.get_status())


def test_okx_get_system_status_scheduled():
    """Test get_system_status with scheduled state (maintenance announcements)"""
    live_okx_swap_feed = init_req_feed()
    data = live_okx_swap_feed.get_system_status(state="scheduled")
    assert isinstance(data, RequestData)
    print("get_system_status (scheduled) status:", data.get_status())


def test_okx_async_get_system_status_scheduled():
    """Test async_get_system_status with scheduled state"""
    data_queue = queue.Queue()
    live_okx_swap_feed = init_async_feed(data_queue)
    live_okx_swap_feed.async_get_system_status(state="scheduled")
    time.sleep(5)
    try:
        result = data_queue.get(False)
    except queue.Empty:
        result = None
    if result is not None:
        assert isinstance(result, RequestData)
        print("async_get_system_status (scheduled) status:", result.get_status())


def test_okx_get_announcements():
    """Test get_announcements interface"""
    live_okx_swap_feed = init_req_feed()
    data = live_okx_swap_feed.get_announcements(limit="20")
    assert isinstance(data, RequestData)
    print("get_announcements status:", data.get_status())
    if data.get_status():
        data_list = data.get_data()
        assert isinstance(data_list, list)
        print("Announcements count:", len(data_list))
        if data_list:
            print("First announcement:", data_list[0])


def test_okx_async_get_announcements():
    """Test async_get_announcements interface"""
    data_queue = queue.Queue()
    live_okx_swap_feed = init_async_feed(data_queue)
    live_okx_swap_feed.async_get_announcements(limit="10")
    time.sleep(5)
    try:
        result = data_queue.get(False)
    except queue.Empty:
        result = None
    if result is not None:
        assert isinstance(result, RequestData)
        print("async_get_announcements status:", result.get_status())


def test_okx_get_announcements_with_type():
    """Test get_announcements with announcement type"""
    live_okx_swap_feed = init_req_feed()
    # First get the announcement types
    types_data = live_okx_swap_feed.get_announcement_types()
    if types_data.get_status():
        type_list = types_data.get_data()
        if type_list and len(type_list) > 0:
            first_type = type_list[0]
            print("Testing announcements with type:", first_type)
            data = live_okx_swap_feed.get_announcements(announcement_type=first_type, limit="10")
            assert isinstance(data, RequestData)
            print("get_announcements (with type) status:", data.get_status())
        else:
            print("No announcement types available")
    else:
        print("Could not get announcement types")


def test_okx_get_announcement_types():
    """Test get_announcement_types interface"""
    live_okx_swap_feed = init_req_feed()
    data = live_okx_swap_feed.get_announcement_types()
    assert isinstance(data, RequestData)
    print("get_announcement_types status:", data.get_status())
    if data.get_status():
        data_list = data.get_data()
        assert isinstance(data_list, list)
        print("Announcement types:", data_list[:5] if data_list else "No data")


def test_okx_async_get_announcement_types():
    """Test async_get_announcement_types interface"""
    data_queue = queue.Queue()
    live_okx_swap_feed = init_async_feed(data_queue)
    live_okx_swap_feed.async_get_announcement_types()
    time.sleep(5)
    try:
        result = data_queue.get(False)
    except queue.Empty:
        result = None
    if result is not None:
        assert isinstance(result, RequestData)
        print("async_get_announcement_types status:", result.get_status())


# ==================== WebSocket Channel Tests ====================
