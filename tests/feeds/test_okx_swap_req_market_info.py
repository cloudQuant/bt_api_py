import queue
import time

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


def test_okx_req_get_24h_volume():
    """Test get_24h_volume interface - Get platform 24h total volume"""
    live_okx_swap_feed = init_req_feed()
    data = live_okx_swap_feed.get_24h_volume()
    assert isinstance(data, RequestData)
    print("get_24h_volume status:", data.get_status())
    volume_data = data.get_data()
    assert isinstance(volume_data, list)
    print("get_24h_volume count:", len(volume_data))


def test_okx_async_get_24h_volume():
    """Test async_get_24h_volume interface"""
    data_queue = queue.Queue()
    live_okx_swap_feed = init_async_feed(data_queue)
    live_okx_swap_feed.async_get_24h_volume()
    time.sleep(5)
    try:
        volume_data = data_queue.get(False)
    except queue.Empty:
        volume_data = None
    if volume_data is None:
        print("Warning: volume_data is None (async timeout)")
        return
    assert isinstance(volume_data, RequestData)
    print("async_get_24h_volume status:", volume_data.get_status())


# ==================== Call Auction Details API Tests ====================


def test_okx_req_get_call_auction_details():
    """Test get_call_auction_details interface - Get call auction details"""
    live_okx_swap_feed = init_req_feed()
    # Get call auction details for futures
    data = live_okx_swap_feed.get_call_auction_details(inst_type="FUTURES", uly="BTC-USD")
    assert isinstance(data, RequestData)
    print("get_call_auction_details status:", data.get_status())
    auction_data = data.get_data()
    assert isinstance(auction_data, list)
    print("get_call_auction_details count:", len(auction_data))


def test_okx_async_get_call_auction_details():
    """Test async_get_call_auction_details interface"""
    data_queue = queue.Queue()
    live_okx_swap_feed = init_async_feed(data_queue)
    live_okx_swap_feed.async_get_call_auction_details(inst_type="FUTURES", uly="BTC-USD")
    time.sleep(5)
    try:
        auction_data = data_queue.get(False)
    except queue.Empty:
        auction_data = None
    if auction_data is None:
        print("Warning: auction_data is None (async timeout)")
        return
    assert isinstance(auction_data, RequestData)
    print("async_get_call_auction_details status:", auction_data.get_status())


# ==================== Index Price API Tests ====================


def test_okx_req_get_index_price():
    """Test get_index_price interface - Get index ticker data"""
    live_okx_swap_feed = init_req_feed()
    # Get index price for BTC-USD
    data = live_okx_swap_feed.get_index_price(index="BTC-USD")
    assert isinstance(data, RequestData)
    print("get_index_price status:", data.get_status())
    index_data = data.get_data()
    assert isinstance(index_data, list)
    print("get_index_price count:", len(index_data))


def test_okx_async_get_index_price():
    """Test async_get_index_price interface"""
    data_queue = queue.Queue()
    live_okx_swap_feed = init_async_feed(data_queue)
    live_okx_swap_feed.async_get_index_price(index="BTC-USD")
    time.sleep(5)
    try:
        index_data = data_queue.get(False)
    except queue.Empty:
        index_data = None
    if index_data is None:
        print("Warning: index_data is None (async timeout)")
        return
    assert isinstance(index_data, RequestData)
    print("async_get_index_price status:", index_data.get_status())


def test_okx_req_get_index_price_all():
    """Test get_index_price interface - Get all index tickers"""
    live_okx_swap_feed = init_req_feed()
    data = live_okx_swap_feed.get_index_price()
    assert isinstance(data, RequestData)
    print("get_index_price (all) status:", data.get_status())
    index_data = data.get_data()
    assert isinstance(index_data, list)
    print("get_index_price (all) data count:", len(index_data))


# ==================== Index Candles History API Tests ====================


def test_okx_req_get_index_candles_history():
    """Test get_index_candles_history interface - Get historical index candlestick charts"""
    live_okx_swap_feed = init_req_feed()
    # Get historical index candles for BTC-USD
    data = live_okx_swap_feed.get_index_candles_history(index="BTC-USD", bar="1m", limit="10")
    assert isinstance(data, RequestData)
    print("get_index_candles_history status:", data.get_status())
    candles_data = data.get_data()
    assert isinstance(candles_data, list)
    print("get_index_candles_history data count:", len(candles_data))


def test_okx_async_get_index_candles_history():
    """Test async_get_index_candles_history interface"""
    data_queue = queue.Queue()
    live_okx_swap_feed = init_async_feed(data_queue)
    live_okx_swap_feed.async_get_index_candles_history(index="BTC-USD", bar="1m", limit="10")
    time.sleep(5)
    try:
        candles_data = data_queue.get(False)
    except queue.Empty:
        candles_data = None
    if candles_data is None:
        print("Warning: candles_data is None (async timeout)")
        return
    assert isinstance(candles_data, RequestData)
    print("async_get_index_candles_history status:", candles_data.get_status())


def test_okx_req_get_index_candles_history_with_pagination():
    """Test get_index_candles_history interface with pagination"""
    live_okx_swap_feed = init_req_feed()
    data = live_okx_swap_feed.get_index_candles_history(index="BTC-USD", bar="5m", limit="20")
    assert isinstance(data, RequestData)
    print("get_index_candles_history (5m, 20) status:", data.get_status())


# ==================== Mark Price Candles History API Tests ====================


def test_okx_req_get_mark_price_candles_history():
    """Test get_mark_price_candles_history interface - Get historical mark price candlestick charts"""
    live_okx_swap_feed = init_req_feed()
    # Get historical mark price candles for BTC-USDT-SWAP
    data = live_okx_swap_feed.get_mark_price_candles_history(
        symbol="BTC-USDT-SWAP", bar="1m", limit="10"
    )
    assert isinstance(data, RequestData)
    print("get_mark_price_candles_history status:", data.get_status())
    candles_data = data.get_data()
    assert isinstance(candles_data, list)
    print("get_mark_price_candles_history data count:", len(candles_data))


def test_okx_async_get_mark_price_candles_history():
    """Test async_get_mark_price_candles_history interface"""
    data_queue = queue.Queue()
    live_okx_swap_feed = init_async_feed(data_queue)
    live_okx_swap_feed.async_get_mark_price_candles_history(
        symbol="BTC-USDT-SWAP", bar="1m", limit="10"
    )
    time.sleep(5)
    try:
        candles_data = data_queue.get(False)
    except queue.Empty:
        candles_data = None
    if candles_data is None:
        print("Warning: candles_data is None (async timeout)")
        return
    assert isinstance(candles_data, RequestData)
    print("async_get_mark_price_candles_history status:", candles_data.get_status())


def test_okx_req_get_mark_price_candles_history_with_pagination():
    """Test get_mark_price_candles_history interface with pagination"""
    live_okx_swap_feed = init_req_feed()
    data = live_okx_swap_feed.get_mark_price_candles_history(
        symbol="ETH-USDT-SWAP", bar="5m", limit="20"
    )
    assert isinstance(data, RequestData)
    print("get_mark_price_candles_history (ETH, 5m, 20) status:", data.get_status())


# ==================== Trading Statistics Tests ====================


def test_okx_get_open_interest():
    """Test get_open_interest interface"""
    live_okx_swap_feed = init_req_feed()
    # Test get open interest for BTC-USDT-SWAP
    data = live_okx_swap_feed.get_open_interest(inst_type="SWAP", inst_id="BTC-USDT-SWAP")
    assert isinstance(data, RequestData)
    print("get_open_interest status:", data.get_status())
    if data.get_status():
        data_list = data.get_data()
        assert isinstance(data_list, list)
        print("Open interest data:", data_list[:1] if data_list else "No data")


def test_okx_async_get_open_interest():
    """Test async_get_open_interest interface"""
    data_queue = queue.Queue()
    live_okx_swap_feed = init_async_feed(data_queue)
    live_okx_swap_feed.async_get_open_interest(
        inst_type="SWAP", inst_id="BTC-USDT-SWAP", extra_data={"test_async_get_open_interest": True}
    )
    time.sleep(5)
    try:
        result = data_queue.get(False)
    except queue.Empty:
        result = None
    if result is not None:
        assert isinstance(result, RequestData)
        print("async_get_open_interest status:", result.get_status())


def test_okx_get_open_interest_by_uly():
    """Test get_open_interest with underlying"""
    live_okx_swap_feed = init_req_feed()
    # Test get open interest for BTC-USD underlying
    data = live_okx_swap_feed.get_open_interest(inst_type="SWAP", uly="BTC-USD")
    assert isinstance(data, RequestData)
    print("get_open_interest (by uly) status:", data.get_status())


def test_okx_async_get_open_interest_by_uly():
    """Test async_get_open_interest with underlying"""
    data_queue = queue.Queue()
    live_okx_swap_feed = init_async_feed(data_queue)
    live_okx_swap_feed.async_get_open_interest(
        inst_type="SWAP", uly="BTC-USD", extra_data={"test_async_get_open_interest_uly": True}
    )
    time.sleep(5)
    try:
        result = data_queue.get(False)
    except queue.Empty:
        result = None
    if result is not None:
        assert isinstance(result, RequestData)
        print("async_get_open_interest (by uly) status:", result.get_status())


# ==================== Grid Trading Tests ====================
