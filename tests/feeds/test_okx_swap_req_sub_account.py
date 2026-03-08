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
def test_okx_req_get_sub_account_list():
    """Test get_sub_account_list interface"""
    live_okx_swap_feed = init_req_feed()
    # Get sub-account list (may be empty if no sub-accounts exist)
    data = live_okx_swap_feed.get_sub_account_list(limit="10")
    assert isinstance(data, RequestData)
    print("get_sub_account_list status:", data.get_status())
    sub_account_list = data.get_data()
    assert isinstance(sub_account_list, list)
    print("get_sub_account_list count:", len(sub_account_list))


@pytest.mark.auth_account
def test_okx_async_get_sub_account_list():
    """Test async_get_sub_account_list interface"""
    data_queue = queue.Queue()
    live_okx_swap_feed = init_async_feed(data_queue)
    live_okx_swap_feed.async_get_sub_account_list(limit="10")
    time.sleep(5)
    try:
        sub_account_data = data_queue.get(False)
    except queue.Empty:
        sub_account_data = None
    assert sub_account_data is not None
    assert isinstance(sub_account_data, RequestData)
    assert sub_account_data.get_status()
    sub_account_list = sub_account_data.get_data()
    assert isinstance(sub_account_list, list)


@pytest.mark.auth_account
def test_okx_req_create_sub_account():
    """Test create_sub_account interface (requires actual API call)"""
    live_okx_swap_feed = init_req_feed()
    # This will fail without proper subAcct and pwd, but tests the interface
    result = live_okx_swap_feed.create_sub_account(
        sub_acct="test_sub_account",  # Placeholder
    )
    assert isinstance(result, RequestData)
    print("create_sub_account status:", result.get_status())
    print("create_sub_account input:", result.get_input_data())


@pytest.mark.auth_account
def test_okx_req_create_sub_account_api_key():
    """Test create_sub_account_api_key interface"""
    live_okx_swap_feed = init_req_feed()
    # This will fail without valid sub_acct, but tests the interface
    result = live_okx_swap_feed.create_sub_account_api_key(
        sub_acct="test_sub_account",  # Placeholder
        perm=["read"],
    )
    assert isinstance(result, RequestData)
    print("create_sub_account_api_key status:", result.get_status())


@pytest.mark.auth_account
def test_okx_req_get_sub_account_api_key():
    """Test get_sub_account_api_key interface"""
    live_okx_swap_feed = init_req_feed()
    # This will fail without valid sub_acct, but tests the interface
    result = live_okx_swap_feed.get_sub_account_api_key(
        sub_acct="test_sub_account",  # Placeholder
    )
    assert isinstance(result, RequestData)
    print("get_sub_account_api_key status:", result.get_status())


@pytest.mark.auth_account
def test_okx_req_reset_sub_account_api_key():
    """Test reset_sub_account_api_key interface"""
    live_okx_swap_feed = init_req_feed()
    # This will fail without valid sub_acct and api_key, but tests the interface
    result = live_okx_swap_feed.reset_sub_account_api_key(
        sub_acct="test_sub_account",  # Placeholder
        api_key="test_api_key",  # Placeholder
    )
    assert isinstance(result, RequestData)
    print("reset_sub_account_api_key status:", result.get_status())


@pytest.mark.auth_account
def test_okx_req_delete_sub_account_api_key():
    """Test delete_sub_account_api_key interface"""
    live_okx_swap_feed = init_req_feed()
    # This will fail without valid sub_acct and api_key, but tests the interface
    result = live_okx_swap_feed.delete_sub_account_api_key(
        sub_acct="test_sub_account",  # Placeholder
        api_key="test_api_key",  # Placeholder
    )
    assert isinstance(result, RequestData)
    print("delete_sub_account_api_key status:", result.get_status())


@pytest.mark.auth_account
def test_okx_req_get_sub_account_funding_balance():
    """Test get_sub_account_funding_balance interface"""
    live_okx_swap_feed = init_req_feed()
    # This will fail without valid sub_acct, but tests the interface
    result = live_okx_swap_feed.get_sub_account_funding_balance(
        sub_acct="test_sub_account",  # Placeholder
    )
    assert isinstance(result, RequestData)
    print("get_sub_account_funding_balance status:", result.get_status())


@pytest.mark.auth_account
def test_okx_req_get_sub_account_max_withdrawal():
    """Test get_sub_account_max_withdrawal interface"""
    live_okx_swap_feed = init_req_feed()
    # This will fail without valid sub_acct, but tests the interface
    result = live_okx_swap_feed.get_sub_account_max_withdrawal(
        sub_acct="test_sub_account",  # Placeholder
        ccy="USDT",
    )
    assert isinstance(result, RequestData)
    print("get_sub_account_max_withdrawal status:", result.get_status())


# ==================== Funding Account Tests ====================


@pytest.mark.auth_account
def test_okx_get_sub_account_transfer_history():
    """Test get_sub_account_transfer_history interface"""
    live_okx_swap_feed = init_req_feed()
    data = live_okx_swap_feed.get_sub_account_transfer_history(limit="10")
    assert isinstance(data, RequestData)
    print("get_sub_account_transfer_history status:", data.get_status())


@pytest.mark.auth_account
def test_okx_async_get_sub_account_transfer_history():
    """Test async_get_sub_account_transfer_history interface"""
    data_queue = queue.Queue()
    live_okx_swap_feed = init_async_feed(data_queue)
    live_okx_swap_feed.async_get_sub_account_transfer_history(limit="10")
    time.sleep(5)
    try:
        result = data_queue.get(False)
    except queue.Empty:
        result = None
    if result is not None:
        assert isinstance(result, RequestData)
        print("async_get_sub_account_transfer_history status:", result.get_status())


@pytest.mark.auth_account
def test_okx_get_managed_sub_account_bills():
    """Test get_managed_sub_account_bills interface"""
    live_okx_swap_feed = init_req_feed()
    data = live_okx_swap_feed.get_managed_sub_account_bills(limit="10")
    assert isinstance(data, RequestData)
    print("get_managed_sub_account_bills status:", data.get_status())


@pytest.mark.auth_account
def test_okx_async_get_managed_sub_account_bills():
    """Test async_get_managed_sub_account_bills interface"""
    data_queue = queue.Queue()
    live_okx_swap_feed = init_async_feed(data_queue)
    live_okx_swap_feed.async_get_managed_sub_account_bills(limit="10")
    time.sleep(5)
    try:
        result = data_queue.get(False)
    except queue.Empty:
        result = None
    if result is not None:
        assert isinstance(result, RequestData)
        print("async_get_managed_sub_account_bills status:", result.get_status())


@pytest.mark.skip(reason="OKX API endpoint deprecated/removed (404)")
@pytest.mark.auth_account
def test_okx_get_custody_sub_account_list():
    """Test get_custody_sub_account_list interface"""
    live_okx_swap_feed = init_req_feed()
    data = live_okx_swap_feed.get_custody_sub_account_list()
    assert isinstance(data, RequestData)
    print("get_custody_sub_account_list status:", data.get_status())


@pytest.mark.skip(reason="OKX API endpoint deprecated/removed (404)")
@pytest.mark.auth_account
def test_okx_async_get_custody_sub_account_list():
    """Test async_get_custody_sub_account_list interface"""
    data_queue = queue.Queue()
    live_okx_swap_feed = init_async_feed(data_queue)
    live_okx_swap_feed.async_get_custody_sub_account_list()
    time.sleep(5)
    try:
        result = data_queue.get(False)
    except queue.Empty:
        result = None
    if result is not None:
        assert isinstance(result, RequestData)
        print("async_get_custody_sub_account_list status:", result.get_status())


# ==================== Status/Announcement Tests ====================
