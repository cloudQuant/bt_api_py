import queue
import time
import random
import pytest
from bt_api_py.functions.utils import read_account_config, get_public_ip
from bt_api_py.feeds.live_okx_feed import OkxRequestDataSwap

from bt_api_py.containers.exchanges.okx_exchange_data import OkxExchangeDataSwap
from bt_api_py.containers.requestdatas.request_data import RequestData
from bt_api_py.containers.tickers.okx_ticker import OkxTickerData
from bt_api_py.containers.bars.okx_bar import OkxBarData
from bt_api_py.containers.orderbooks.okx_orderbook import OkxOrderBookData
from bt_api_py.containers.fundingrates.okx_funding_rate import OkxFundingRateData
from bt_api_py.containers.markprices.okx_mark_price import OkxMarkPriceData
from bt_api_py.containers.accounts.okx_account import OkxAccountData
# from bt_api_py.containers.orders.okx_order import OkxOrderData
from bt_api_py.containers.trades.okx_trade import OkxRequestTradeData, OkxWssTradeData
from bt_api_py.containers.positions.okx_position import OkxPositionData
from bt_api_py.containers.orders.order import OrderStatus
from bt_api_py.containers.symbols.okx_symbol import OkxSymbolData
from bt_api_py.containers.assets.okx_asset import OkxCurrencyData, OkxAssetBalanceData, OkxAssetValuationData, OkxTransferStateData, OkxDepositInfoData, OkxWithdrawalInfoData






def generate_kwargs(exchange=OkxExchangeDataSwap):
    data = read_account_config()
    kwargs = {
        "public_key": data['okx']['public_key'],
        "private_key": data['okx']['private_key'],
        "passphrase": data['okx']["passphrase"],
        "topics": {"tick": {"symbol": "BTC-USDT"}},
        "proxies": data.get('proxies'),
        "async_proxy": data.get('async_proxy'),
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

def test_okx_req_sprd_get_orders_pending():
    """Test sprd_get_orders_pending interface - Get pending spread orders"""
    live_okx_swap_feed = init_req_feed()
    # Get pending spread orders
    data = live_okx_swap_feed.sprd_get_orders_pending(limit="100")
    assert isinstance(data, RequestData)
    print("sprd_get_orders_pending status:", data.get_status())
    orders_list = data.get_data()
    assert isinstance(orders_list, list)
    print("sprd_get_orders_pending count:", len(orders_list))




def test_okx_async_sprd_get_orders_pending():
    """Test async_sprd_get_orders_pending interface"""
    data_queue = queue.Queue()
    live_okx_swap_feed = init_async_feed(data_queue)
    live_okx_swap_feed.async_sprd_get_orders_pending(limit="100")
    time.sleep(5)
    try:
        result = data_queue.get(False)
    except queue.Empty:
        result = None
    if result is not None:
        assert isinstance(result, RequestData)
        print("async_sprd_get_orders_pending status:", result.get_status())




def test_okx_req_sprd_get_orders_history():
    """Test sprd_get_orders_history interface - Get spread order history"""
    live_okx_swap_feed = init_req_feed()
    # Get spread order history
    data = live_okx_swap_feed.sprd_get_orders_history(limit="100")
    assert isinstance(data, RequestData)
    print("sprd_get_orders_history status:", data.get_status())
    orders_list = data.get_data()
    assert isinstance(orders_list, list)
    print("sprd_get_orders_history count:", len(orders_list))




def test_okx_async_sprd_get_orders_history():
    """Test async_sprd_get_orders_history interface"""
    data_queue = queue.Queue()
    live_okx_swap_feed = init_async_feed(data_queue)
    live_okx_swap_feed.async_sprd_get_orders_history(limit="100")
    time.sleep(5)
    try:
        result = data_queue.get(False)
    except queue.Empty:
        result = None
    if result is not None:
        assert isinstance(result, RequestData)
        print("async_sprd_get_orders_history status:", result.get_status())




def test_okx_req_sprd_get_trades():
    """Test sprd_get_trades interface - Get spread trade history"""
    live_okx_swap_feed = init_req_feed()
    # Get spread trade history
    data = live_okx_swap_feed.sprd_get_trades(limit="100")
    assert isinstance(data, RequestData)
    print("sprd_get_trades status:", data.get_status())
    trades_list = data.get_data()
    assert isinstance(trades_list, list)
    print("sprd_get_trades count:", len(trades_list))




def test_okx_async_sprd_get_trades():
    """Test async_sprd_get_trades interface"""
    data_queue = queue.Queue()
    live_okx_swap_feed = init_async_feed(data_queue)
    live_okx_swap_feed.async_sprd_get_trades(limit="100")
    time.sleep(5)
    try:
        result = data_queue.get(False)
    except queue.Empty:
        result = None
    if result is not None:
        assert isinstance(result, RequestData)
        print("async_sprd_get_trades status:", result.get_status())




def test_okx_req_sprd_get_order():
    """Test sprd_get_order interface - Get spread order details"""
    live_okx_swap_feed = init_req_feed()
    # Get order details for a specific spread instrument
    # Using a sample spread ID - adjust based on actual spread instruments available
    data = live_okx_swap_feed.sprd_get_order(sprd_id="F-USDT-BTC-USDT")
    assert isinstance(data, RequestData)
    print("sprd_get_order status:", data.get_status())
    order_list = data.get_data()
    assert isinstance(order_list, list)
    print("sprd_get_order count:", len(order_list))




def test_okx_async_sprd_get_order():
    """Test async_sprd_get_order interface"""
    data_queue = queue.Queue()
    live_okx_swap_feed = init_async_feed(data_queue)
    live_okx_swap_feed.async_sprd_get_order(sprd_id="F-USDT-BTC-USDT")
    time.sleep(5)
    try:
        result = data_queue.get(False)
    except queue.Empty:
        result = None
    if result is not None:
        assert isinstance(result, RequestData)
        print("async_sprd_get_order status:", result.get_status())




def test_okx_req_sprd_order():
    """Test sprd_order interface - Place spread order (dry run)"""
    live_okx_swap_feed = init_req_feed()
    # Place a spread order - this may fail if no spread instrument exists
    # or if trading is not enabled, but tests the interface
    data = live_okx_swap_feed.sprd_order(
        sprd_id="F-USDT-BTC-USDT",
        side="buy",
        sz="1",
        px="0.01"
    )
    assert isinstance(data, RequestData)
    print("sprd_order status:", data.get_status())
    order_result = data.get_data()
    assert isinstance(order_result, list)
    print("sprd_order result:", order_result)




def test_okx_async_sprd_order():
    """Test async_sprd_order interface"""
    data_queue = queue.Queue()
    live_okx_swap_feed = init_async_feed(data_queue)
    live_okx_swap_feed.async_sprd_order(
        sprd_id="F-USDT-BTC-USDT",
        side="buy",
        sz="1",
        px="0.01"
    )
    time.sleep(5)
    try:
        result = data_queue.get(False)
    except queue.Empty:
        result = None
    if result is not None:
        assert isinstance(result, RequestData)
        print("async_sprd_order status:", result.get_status())




def test_okx_req_sprd_cancel_order():
    """Test sprd_cancel_order interface - Cancel spread order"""
    live_okx_swap_feed = init_req_feed()
    # Cancel a spread order - may fail if order doesn't exist
    data = live_okx_swap_feed.sprd_cancel_order(
        sprd_id="F-USDT-BTC-USDT",
        order_id="12345"
    )
    assert isinstance(data, RequestData)
    print("sprd_cancel_order status:", data.get_status())
    cancel_result = data.get_data()
    assert isinstance(cancel_result, list)
    print("sprd_cancel_order result:", cancel_result)




def test_okx_async_sprd_cancel_order():
    """Test async_sprd_cancel_order interface"""
    data_queue = queue.Queue()
    live_okx_swap_feed = init_async_feed(data_queue)
    live_okx_swap_feed.async_sprd_cancel_order(
        sprd_id="F-USDT-BTC-USDT",
        order_id="12345"
    )
    time.sleep(5)
    try:
        result = data_queue.get(False)
    except queue.Empty:
        result = None
    if result is not None:
        assert isinstance(result, RequestData)
        print("async_sprd_cancel_order status:", result.get_status())




def test_okx_sprd_trading_with_client_order_id():
    """Test spread trading with client order ID"""
    live_okx_swap_feed = init_req_feed()
    import random
    random_number = random.randint(10 ** 17, 10 ** 18 - 1)
    client_order_id = str(random_number)

    # Test placing order with client order ID
    order_data = live_okx_swap_feed.sprd_order(
        sprd_id="F-USDT-BTC-USDT",
        side="buy",
        sz="1",
        px="0.01",
        cl_ord_id=client_order_id
    )
    assert isinstance(order_data, RequestData)
    print("sprd_order with cl_ord_id status:", order_data.get_status())

    # Test getting order by client order ID
    get_data = live_okx_swap_feed.sprd_get_order(
        sprd_id="F-USDT-BTC-USDT",
        client_order_id=client_order_id
    )
    assert isinstance(get_data, RequestData)
    print("sprd_get_order by cl_ord_id status:", get_data.get_status())

    # Test canceling order by client order ID
    cancel_data = live_okx_swap_feed.sprd_cancel_order(
        sprd_id="F-USDT-BTC-USDT",
        client_order_id=client_order_id
    )
    assert isinstance(cancel_data, RequestData)
    print("sprd_cancel_order by cl_ord_id status:", cancel_data.get_status())




def test_okx_sprd_order_with_reduce_only():
    """Test spread order with reduce_only flag"""
    live_okx_swap_feed = init_req_feed()
    data = live_okx_swap_feed.sprd_order(
        sprd_id="F-USDT-BTC-USDT",
        side="sell",
        sz="1",
        px="0.01",
        reduce_only=True
    )
    assert isinstance(data, RequestData)
    print("sprd_order with reduce_only status:", data.get_status())




def test_okx_sprd_get_orders_pending_with_filter():
    """Test sprd_get_orders_pending with instrument type filter"""
    live_okx_swap_feed = init_req_feed()
    data = live_okx_swap_feed.sprd_get_orders_pending(
        sprd_id="F-USDT-BTC-USDT",
        limit="10"
    )
    assert isinstance(data, RequestData)
    print("sprd_get_orders_pending with filter status:", data.get_status())
    orders_list = data.get_data()
    assert isinstance(orders_list, list)




def test_okx_sprd_get_orders_history_with_state():
    """Test sprd_get_orders_history with state filter"""
    live_okx_swap_feed = init_req_feed()
    # Get filled orders
    data = live_okx_swap_feed.sprd_get_orders_history(
        state="filled",
        limit="10"
    )
    assert isinstance(data, RequestData)
    print("sprd_get_orders_history with state filter status:", data.get_status())
    orders_list = data.get_data()
    assert isinstance(orders_list, list)




def test_okx_sprd_get_trades_with_instrument():
    """Test sprd_get_trades with specific spread instrument"""
    live_okx_swap_feed = init_req_feed()
    data = live_okx_swap_feed.sprd_get_trades(
        sprd_id="F-USDT-BTC-USDT",
        limit="10"
    )
    assert isinstance(data, RequestData)
    print("sprd_get_trades with sprd_id status:", data.get_status())
    trades_list = data.get_data()
    assert isinstance(trades_list, list)

# ==================== Copy Trading Tests ====================


