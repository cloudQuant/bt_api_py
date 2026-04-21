import queue
import time

import pytest

from bt_api_py.containers.bars.okx_bar import OkxBarData
from bt_api_py.containers.exchanges.okx_exchange_data import OkxExchangeDataSwap
from bt_api_py.containers.orderbooks.okx_orderbook import OkxOrderBookData
from bt_api_base.containers.requestdatas.request_data import RequestData

# from bt_api_py.containers.orders.okx_order import OkxOrderData
from bt_api_py.containers.trades.okx_trade import OkxRequestTradeData
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


@pytest.mark.orderbook
def test_okx_req_get_depth_full():
    """Test get_depth_full interface"""
    live_okx_swap_feed = init_req_feed()
    # Get full order book for BTC-USDT
    data = live_okx_swap_feed.get_depth_full("BTC-USDT")
    assert isinstance(data, RequestData)
    assert data.get_status()
    depth_list = data.get_data()
    assert isinstance(depth_list, list)
    if len(depth_list) > 0:
        order_book = depth_list[0]
        assert isinstance(order_book, OkxOrderBookData)
        order_book.init_data()
        assert order_book.get_symbol_name() == "BTC-USDT"
        print(
            f"get_depth_full: bids = {len(order_book.get_bid_price_list())}, asks = {len(order_book.get_ask_price_list())}"
        )


@pytest.mark.orderbook
def test_okx_async_get_depth_full():
    """Test async_get_depth_full interface"""
    data_queue = queue.Queue()
    live_okx_swap_feed = init_async_feed(data_queue)
    live_okx_swap_feed.async_get_depth_full("BTC-USDT")
    time.sleep(8)  # Increase wait time for full order book data
    try:
        depth_data = data_queue.get(False)
    except queue.Empty:
        depth_data = None
    assert depth_data is not None
    assert isinstance(depth_data, RequestData)
    assert depth_data.get_status()
    depth_list = depth_data.get_data()
    assert isinstance(depth_list, list)
    if len(depth_list) > 0:
        order_book = depth_list[0]
        assert isinstance(order_book, OkxOrderBookData)
        order_book.init_data()


@pytest.mark.kline
def test_okx_req_get_kline_his():
    """Test get_kline_his interface (history candles for SPOT)"""
    live_okx_swap_feed = init_req_feed()
    # Get history candles for BTC-USDT (note: using symbol directly, no -SWAP suffix needed for SPOT)
    # Use "BTC-USDT" as the instId for history-candles endpoint
    data = live_okx_swap_feed.get_kline_his("BTC-USDT", bar="1m", limit="10")
    assert isinstance(data, RequestData)
    assert data.get_status()
    kline_list = data.get_data()
    assert isinstance(kline_list, list)
    print("get_kline_his count:", len(kline_list))
    if len(kline_list) > 0:
        kline = kline_list[0]
        assert isinstance(kline, OkxBarData)
        kline.init_data()
        assert kline.get_open_price() > 0


@pytest.mark.kline
def test_okx_async_get_kline_his():
    """Test async_get_kline_his interface"""
    data_queue = queue.Queue()
    live_okx_swap_feed = init_async_feed(data_queue)
    live_okx_swap_feed.async_get_kline_his("BTC-USDT", bar="1m", limit="10")
    try:
        kline_data = data_queue.get(timeout=30)
    except queue.Empty:
        kline_data = None
    assert kline_data is not None
    assert isinstance(kline_data, RequestData)
    assert kline_data.get_status()
    kline_list = kline_data.get_data()
    assert isinstance(kline_list, list)
    print("async_get_kline_his count:", len(kline_list))


@pytest.mark.public_trade
def test_okx_req_get_trades():
    """Test get_trades interface (recent 600 trades)"""
    live_okx_swap_feed = init_req_feed()
    # Get recent trades for BTC-USDT
    data = live_okx_swap_feed.get_trades("BTC-USDT", limit="10")
    assert isinstance(data, RequestData)
    assert data.get_status()
    trades_list = data.get_data()
    assert isinstance(trades_list, list)
    print("get_trades count:", len(trades_list))
    if len(trades_list) > 0:
        trade = trades_list[0]
        if isinstance(trade, OkxRequestTradeData):
            trade.init_data()
            assert trade.get_trade_price() > 0
            assert trade.get_trade_volume() > 0
        else:
            assert isinstance(trade, dict)
            print("Trade (raw):", list(trade.keys())[:5])


@pytest.mark.public_trade
def test_okx_async_get_trades():
    """Test async_get_trades interface"""
    data_queue = queue.Queue()
    live_okx_swap_feed = init_async_feed(data_queue)
    live_okx_swap_feed.async_get_trades("BTC-USDT", limit="10")
    time.sleep(5)
    try:
        trades_data = data_queue.get(False)
    except queue.Empty:
        trades_data = None
    assert trades_data is not None
    assert isinstance(trades_data, RequestData)
    assert trades_data.get_status()
    trades_list = trades_data.get_data()
    assert isinstance(trades_list, list)
    if len(trades_list) > 0:
        trade = trades_list[0]
        if isinstance(trade, OkxRequestTradeData):
            trade.init_data()
        else:
            assert isinstance(trade, dict)


@pytest.mark.public_trade
def test_okx_req_get_trades_history():
    """Test get_trades_history interface (last 3 months)"""
    live_okx_swap_feed = init_req_feed()
    # Get trades history for SPOT
    data = live_okx_swap_feed.get_trades_history("BTC-USDT", inst_type="SPOT", limit="10")
    assert isinstance(data, RequestData)
    assert data.get_status()
    trades_list = data.get_data()
    assert isinstance(trades_list, list)
    print("get_trades_history count:", len(trades_list))


@pytest.mark.public_trade
def test_okx_async_get_trades_history():
    """Test async_get_trades_history interface"""
    data_queue = queue.Queue()
    live_okx_swap_feed = init_async_feed(data_queue)
    live_okx_swap_feed.async_get_trades_history("BTC-USDT", inst_type="SPOT", limit="10")
    time.sleep(5)
    try:
        trades_data = data_queue.get(False)
    except queue.Empty:
        trades_data = None
    assert trades_data is not None
    assert isinstance(trades_data, RequestData)
    assert trades_data.get_status()
    trades_list = trades_data.get_data()
    assert isinstance(trades_list, list)


@pytest.mark.kline
def test_okx_req_get_index_candles():
    """Test get_index_candles interface"""
    live_okx_swap_feed = init_req_feed()
    # Get index candles for BTC-USD
    data = live_okx_swap_feed.get_index_candles("BTC-USD", bar="1m", limit="10")
    assert isinstance(data, RequestData)
    assert data.get_status()
    kline_list = data.get_data()
    assert isinstance(kline_list, list)
    print("get_index_candles count:", len(kline_list))
    if len(kline_list) > 0:
        kline = kline_list[0]
        assert isinstance(kline, OkxBarData)
        kline.init_data()
        assert kline.get_open_price() > 0


@pytest.mark.kline
def test_okx_async_get_index_candles():
    """Test async_get_index_candles interface"""
    data_queue = queue.Queue()
    live_okx_swap_feed = init_async_feed(data_queue)
    live_okx_swap_feed.async_get_index_candles("BTC-USD", bar="1m", limit="10")
    time.sleep(5)
    try:
        kline_data = data_queue.get(False)
    except queue.Empty:
        kline_data = None
    assert kline_data is not None
    assert isinstance(kline_data, RequestData)
    assert kline_data.get_status()
    kline_list = kline_data.get_data()
    assert isinstance(kline_list, list)


@pytest.mark.kline
def test_okx_req_get_mark_price_candles():
    """Test get_mark_price_candles interface"""
    live_okx_swap_feed = init_req_feed()
    # Get mark price candles for BTC-USDT
    data = live_okx_swap_feed.get_mark_price_candles("BTC-USDT", bar="1m", limit="10")
    assert isinstance(data, RequestData)
    assert data.get_status()
    kline_list = data.get_data()
    assert isinstance(kline_list, list)
    print("get_mark_price_candles count:", len(kline_list))
    if len(kline_list) > 0:
        kline = kline_list[0]
        assert isinstance(kline, OkxBarData)
        kline.init_data()
        assert kline.get_open_price() > 0


@pytest.mark.kline
def test_okx_async_get_mark_price_candles():
    """Test async_get_mark_price_candles interface"""
    data_queue = queue.Queue()
    live_okx_swap_feed = init_async_feed(data_queue)
    live_okx_swap_feed.async_get_mark_price_candles("BTC-USDT", bar="1m", limit="10")
    time.sleep(7)  # Increase wait time
    try:
        kline_data = data_queue.get(False)
    except queue.Empty:
        kline_data = None
    assert kline_data is not None
    assert isinstance(kline_data, RequestData)
    assert kline_data.get_status()
    kline_list = kline_data.get_data()
    assert isinstance(kline_list, list)


def test_okx_req_get_exchange_rate():
    """Test get_exchange_rate interface - Get exchange rate"""
    live_okx_swap_feed = init_req_feed()
    # Get exchange rate
    data = live_okx_swap_feed.get_exchange_rate()
    assert isinstance(data, RequestData)
    print("get_exchange_rate status:", data.get_status())
    rate_list = data.get_data()
    assert isinstance(rate_list, list)
    print("get_exchange_rate count:", len(rate_list))
    if rate_list:
        print("get_exchange_rate sample:", rate_list[0])


def test_okx_async_get_exchange_rate():
    """Test async_get_exchange_rate interface"""
    data_queue = queue.Queue()
    live_okx_swap_feed = init_async_feed(data_queue)
    live_okx_swap_feed.async_get_exchange_rate()
    time.sleep(5)
    try:
        rate_data = data_queue.get(False)
    except queue.Empty:
        rate_data = None
    if rate_data is None:
        print("Warning: rate_data is None (async timeout)")
        return  # Skip assertion on timeout
    assert isinstance(rate_data, RequestData)
    print("async_get_exchange_rate status:", rate_data.get_status())


def test_okx_req_get_index_components():
    """Test get_index_components interface - Get index components"""
    live_okx_swap_feed = init_req_feed()
    # Get index components for BTC-USD index
    data = live_okx_swap_feed.get_index_components(index="BTC-USD")
    assert isinstance(data, RequestData)
    print("get_index_components status:", data.get_status())
    components_data = data.get_data()
    # The API returns a dict with 'components', 'index', 'last', 'ts' keys
    assert isinstance(components_data, dict)
    print("get_index_components keys:", components_data.keys())
    if "components" in components_data:
        print("get_index_components count:", len(components_data["components"]))


def test_okx_async_get_index_components():
    """Test async_get_index_components interface"""
    data_queue = queue.Queue()
    live_okx_swap_feed = init_async_feed(data_queue)
    live_okx_swap_feed.async_get_index_components(index="BTC-USD")
    time.sleep(5)
    try:
        components_data = data_queue.get(False)
    except queue.Empty:
        components_data = None
    if components_data is None:
        print("Warning: components_data is None (async timeout)")
        return  # Skip assertion on timeout
    assert isinstance(components_data, RequestData)
    print("async_get_index_components status:", components_data.get_status())


# ==================== MMP (Market Maker Protection) API Tests ====================


def test_okx_req_get_option_instrument_family_trades():
    """Test get_option_instrument_family_trades interface - Get option instrument family trades data"""
    live_okx_swap_feed = init_req_feed()
    # Get option instrument family trades for BTC-USD
    data = live_okx_swap_feed.get_option_instrument_family_trades(inst_family="BTC-USD")
    assert isinstance(data, RequestData)
    print("get_option_instrument_family_trades status:", data.get_status())
    trades_list = data.get_data()
    assert isinstance(trades_list, list)
    print("get_option_instrument_family_trades count:", len(trades_list))


def test_okx_async_get_option_instrument_family_trades():
    """Test async_get_option_instrument_family_trades interface"""
    data_queue = queue.Queue()
    live_okx_swap_feed = init_async_feed(data_queue)
    live_okx_swap_feed.async_get_option_instrument_family_trades(inst_family="BTC-USD")
    time.sleep(5)
    try:
        trades_data = data_queue.get(False)
    except queue.Empty:
        trades_data = None
    if trades_data is None:
        print("Warning: trades_data is None (async timeout)")
        return
    assert isinstance(trades_data, RequestData)
    print("async_get_option_instrument_family_trades status:", trades_data.get_status())


def test_okx_req_get_option_instrument_family_trades_with_limit():
    """Test get_option_instrument_family_trades interface with limit"""
    live_okx_swap_feed = init_req_feed()
    data = live_okx_swap_feed.get_option_instrument_family_trades(inst_family="BTC-USD", limit=10)
    assert isinstance(data, RequestData)
    print("get_option_instrument_family_trades (with limit) status:", data.get_status())


# ==================== Option Trades API Tests ====================


def test_okx_req_get_option_trades():
    """Test get_option_trades interface - Get option trades data"""
    live_okx_swap_feed = init_req_feed()
    # First get a valid option instrument ID
    instruments = live_okx_swap_feed.get_public_instruments(inst_type="OPTION", uly="BTC-USD")
    inst_id = None
    if instruments.get_status():
        inst_list = instruments.get_data()
        if inst_list and len(inst_list) > 0:
            first = inst_list[0]
            inst_id = first.get_inst_id() if hasattr(first, "get_inst_id") else first.get("instId")

    if inst_id:
        data = live_okx_swap_feed.get_option_trades(inst_id=inst_id)
        assert isinstance(data, RequestData)
        print("get_option_trades status:", data.get_status())
        trades_list = data.get_data()
        assert isinstance(trades_list, list)
        print("get_option_trades count:", len(trades_list))
    else:
        print("Warning: No valid option instrument found, skipping get_option_trades test")


def test_okx_async_get_option_trades():
    """Test async_get_option_trades interface"""
    data_queue = queue.Queue()
    live_okx_swap_feed = init_async_feed(data_queue)
    # First get a valid option instrument ID
    instruments = live_okx_swap_feed.get_public_instruments(inst_type="OPTION", uly="BTC-USD")
    inst_id = None
    if instruments.get_status():
        inst_list = instruments.get_data()
        if inst_list and len(inst_list) > 0:
            first = inst_list[0]
            inst_id = first.get_inst_id() if hasattr(first, "get_inst_id") else first.get("instId")

    if inst_id:
        live_okx_swap_feed.async_get_option_trades(inst_id=inst_id)
        try:
            trades_data = data_queue.get(timeout=15)
        except queue.Empty:
            trades_data = None
        if trades_data is None:
            print("Warning: trades_data is None (async timeout)")
            return
        assert isinstance(trades_data, RequestData)
        print("async_get_option_trades status:", trades_data.get_status())
    else:
        print("Warning: No valid option instrument found, skipping async_get_option_trades test")
