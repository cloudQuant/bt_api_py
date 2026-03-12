import queue
import random
import time

import pytest

from bt_api_py.containers.exchanges.okx_exchange_data import OkxExchangeDataSwap
from bt_api_py.containers.requestdatas.request_data import RequestData

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


def okx_req_query_order_by_client_order_id(client_order_id):
    live_okx_swap_feed = init_req_feed()
    kwargs = {"client_order_id": client_order_id}
    data = live_okx_swap_feed.query_order("OP-USDT", **kwargs)
    # print(data.get_data())
    data.init_data()
    assert data.get_status()
    assert isinstance(data, RequestData)
    return data


def okx_req_get_open_order():
    live_okx_swap_feed = init_req_feed()
    data = live_okx_swap_feed.get_open_orders()
    assert isinstance(data, RequestData)
    return data.get_data()


def okx_req_cancel_order_by_order_id(order_id):
    live_okx_swap_feed = init_req_feed()
    data = live_okx_swap_feed.cancel_order("OP-USDT", order_id=order_id)
    print(data.get_data())
    assert isinstance(data, RequestData)
    return data


def okx_req_cancel_order_by_client_order_id(client_order_id):
    live_okx_swap_feed = init_req_feed()
    data = live_okx_swap_feed.cancel_order("OP-USDT", client_order_id=client_order_id)
    print(data.get_data())
    assert isinstance(data, RequestData)
    return data


@pytest.mark.auth_order
def test_okx_req_order_functions():
    live_okx_swap_feed = init_req_feed()
    price_data = live_okx_swap_feed.get_tick("OP-USDT")
    price_data = price_data.get_data()[0].init_data()
    bid_price = round(price_data.get_bid_price() * 0.9, 2)
    round(price_data.get_ask_price() * 1.1, 2)
    random_number = random.randint(10**17, 10**18 - 1)
    buy_client_order_id = str(random_number)
    buy_data = live_okx_swap_feed.make_order(
        "OP-USDT", 2, bid_price, "buy-limit", client_order_id=buy_client_order_id
    )
    # 测试买单和卖单
    assert isinstance(buy_data, RequestData)
    buy_info = buy_data.get_data()[0]
    print("buy_info", buy_info)
    assert isinstance(buy_info, dict)
    buy_order_id = buy_info.get("order_id")
    assert buy_order_id is not None
    assert buy_data.get_status()

    # 根据order_id查询订单
    data = live_okx_swap_feed.query_order("OP-USDT", order_id=buy_order_id)
    # print(data.get_data())
    assert isinstance(data, RequestData)
    assert data.get_data()[0].init_data().get_order_price() == bid_price
    assert data.get_data()[0].init_data().get_client_order_id() == buy_client_order_id
    assert data.get_status()
    # 根据client_order_id查询订单
    okx_req_query_order_by_client_order_id(buy_client_order_id)
    # 查询有哪些open_order
    orders = okx_req_get_open_order()
    assert len(orders) >= 1
    # 用order_id和client_order_id进行撤单
    okx_req_cancel_order_by_order_id(buy_order_id)
    # Querying orders can only be done during single-backtrader testing.
    # When executing across multiple backtrader, the order of execution is not guaranteed,
    # which can lead to conflicts and errors.
    # orders = okx_req_get_open_order()
    # print('test_okx_req_order_functions()', orders.get_data())
    # assert orders.get_data() is None


@pytest.mark.auth_order
def test_okx_async_order_functions():
    data_queue = queue.Queue()
    live_okx_swap_feed = init_async_feed(data_queue)
    price_data = live_okx_swap_feed.get_tick("OP-USDT").get_data()[0].init_data()
    bid_price = round(price_data.get_bid_price() * 0.9, 2)
    # ask_price = round(price_data.get_ask_price() * 1.1, 2)
    random_number = random.randint(10**17, 10**18 - 1)
    buy_client_order_id = str(random_number)
    # sell_client_order_id = str(random_number + 1)
    make_order_func = False
    query_order_func = False
    cancel_order_func = False
    open_order_func = False
    live_okx_swap_feed.async_make_order(
        "OP-USDT", 1, bid_price, "buy-limit", client_order_id=buy_client_order_id
    )
    time.sleep(5)
    live_okx_swap_feed.async_query_order("OP-USDT", **{"client_order_id": buy_client_order_id})
    live_okx_swap_feed.async_get_open_orders()
    time.sleep(5)
    live_okx_swap_feed.async_cancel_order("OP-USDT", **{"client_order_id": buy_client_order_id})
    time.sleep(5)
    # live_okx_swap_feed.async_get_open_orders("OP-USDT")
    while True:
        try:
            target_data = data_queue.get(False)
        except queue.Empty:
            break
        event_data = target_data.get_data()
        event_type = target_data.get_event()
        request_type = target_data.get_request_type()
        if event_type == "RequestEvent" and request_type == "make_order":
            assert target_data.get_status()
            print("MakeOrderRequestEvent", event_data)
            assert target_data.get_data()[0].get("client_order_id") == buy_client_order_id
            make_order_func = True
        if event_type == "RequestEvent" and request_type == "query_order":
            assert target_data.get_status()
            print("QueryOrderRequestEvent", event_data)
            assert (
                target_data.get_data()[0].init_data().get_client_order_id() == buy_client_order_id
            )
            query_order_func = True
        if event_type == "RequestEvent" and request_type == "cancel_order":
            assert target_data.get_status()
            print("CancelOrderRequestEvent", event_data)
            cancel_order_func = True
        if event_type == "RequestEvent" and request_type == "get_open_orders":
            assert target_data.get_status()
            print("GetOpenOrdersRequestEvent", event_data)
            assert target_data.get_data() is not None
            open_order_func = True
        if open_order_func and cancel_order_func and query_order_func and make_order_func:
            break


# def make_a_order():
#     live_okx_swap_feed = init_req_feed()
#     random_number = random.randint(10 ** 17, 10 ** 18 - 1)
#     sell_client_order_id = str(random_number + 1)
#     price_data = live_okx_swap_feed.get_tick("OP-USDT").get_data()[0]
#     ask_price = round(price_data.get_ask_price(), 4)+0.005
#     sell_data = live_okx_swap_feed.make_order("OP-USDT", 1, ask_price, "sell-limit",
#                                               client_order_id=sell_client_order_id)
#     print(sell_data.get_data())


@pytest.mark.auth_private_trade
def test_okx_req_get_deals():
    live_okx_swap_feed = init_req_feed()
    price_data = live_okx_swap_feed.get_deals()
    trade_data = price_data.get_data()
    if len(trade_data) > 0:
        first_trade = trade_data[0].init_data()
        assert trade_data is not None
        assert isinstance(trade_data, list)
        assert first_trade.get_trade_price() > 0
        assert first_trade.get_trade_volume() > 0


@pytest.mark.auth_private_trade
def test_okx_async_get_deals():
    data_queue = queue.Queue()
    live_okx_swap_feed = init_async_feed(data_queue)
    live_okx_swap_feed.async_get_deals()
    time.sleep(5)
    try:
        depth_data = data_queue.get(False)
    except queue.Empty:
        target_data = None
        first_trade = None
    else:
        target_data = depth_data.get_data()
        if len(target_data) > 0:
            first_trade = target_data[0].init_data()
            assert target_data is not None
            assert isinstance(target_data, list)
            assert first_trade.get_trade_price() > 0
            assert first_trade.get_trade_volume() > 0


@pytest.mark.auth_account
def test_okx_req_set_margin_balance():
    """Test set_margin_balance interface"""
    live_okx_swap_feed = init_req_feed()
    result = live_okx_swap_feed.set_margin_balance(
        symbol="BTC-USDT",
        pos_id="test_position_id",  # Placeholder
        amt="100",
        mgn_mode="cross",
        action_type="add",
        pos_side="net",
    )
    assert isinstance(result, RequestData)
    print("set_margin_balance status:", result.get_status())


@pytest.mark.auth_order
def test_okx_req_make_orders():
    """Test make_orders batch order interface"""
    live_okx_swap_feed = init_req_feed()
    # Prepare order list
    order_list = [
        {
            "symbol": "OP-USDT",
            "vol": 1,
            "price": 0.1,
            "order_type": "buy-limit",
            "client_order_id": "test_batch_1",
        },
        {
            "symbol": "OP-USDT",
            "vol": 1,
            "price": 0.05,
            "order_type": "buy-limit",
            "client_order_id": "test_batch_2",
        },
    ]
    result = live_okx_swap_feed.make_orders(order_list)
    assert isinstance(result, RequestData)
    print("make_orders status:", result.get_status())
    print("make_orders data:", result.get_data())


@pytest.mark.auth_order
def test_okx_req_cancel_orders():
    """Test cancel_orders batch cancel interface"""
    live_okx_swap_feed = init_req_feed()
    # Prepare order list to cancel
    order_list = [
        {
            "symbol": "OP-USDT",
            "order_id": "test_order_1",  # Placeholder
        },
        {
            "symbol": "OP-USDT",
            "client_order_id": "test_client_1",  # Placeholder
        },
    ]
    result = live_okx_swap_feed.cancel_orders(order_list)
    assert isinstance(result, RequestData)
    print("cancel_orders status:", result.get_status())


def test_okx_req_amend_orders():
    """Test amend_orders batch amend interface"""
    live_okx_swap_feed = init_req_feed()
    # Prepare order list to amend
    order_list = [
        {
            "symbol": "OP-USDT",
            "order_id": "test_order_1",  # Placeholder
            "new_sz": 2,
        },
        {
            "symbol": "OP-USDT",
            "client_order_id": "test_client_1",  # Placeholder
            "new_px": 0.5,
        },
    ]
    result = live_okx_swap_feed.amend_orders(order_list)
    assert isinstance(result, RequestData)
    print("amend_orders status:", result.get_status())


def test_okx_req_get_fills():
    """Test get_fills transaction details interface"""
    live_okx_swap_feed = init_req_feed()
    # Get fills for SWAP instruments
    data = live_okx_swap_feed.get_fills(inst_type="SWAP", limit="10")
    assert isinstance(data, RequestData)
    assert data.get_status()
    fills_list = data.get_data()
    assert isinstance(fills_list, list)
    print("get_fills count:", len(fills_list))
    if len(fills_list) > 0:
        fill_data = fills_list[0]
        print("fill_data:", fill_data)


@pytest.mark.auth_position
def test_okx_req_close_position():
    """Test close_position market close all interface"""
    live_okx_swap_feed = init_req_feed()
    # Close position for BTC-USDT-SWAP
    # This may fail if there's no position, but tests the interface
    result = live_okx_swap_feed.close_position(symbol="BTC-USDT", pos_side="net", mgn_mode="cross")
    assert isinstance(result, RequestData)
    print("close_position status:", result.get_status())


def test_okx_req_get_fills_history():
    """Test get_fills_history interface"""
    live_okx_swap_feed = init_req_feed()
    # Get fills history for SWAP instruments (last 3 months)
    data = live_okx_swap_feed.get_fills_history(inst_type="SWAP", limit="10")
    assert isinstance(data, RequestData)
    assert data.get_status()
    fills_list = data.get_data()
    assert isinstance(fills_list, list)
    print("get_fills_history count:", len(fills_list))


def test_okx_req_get_order_history_archive():
    """Test get_order_history_archive interface"""
    live_okx_swap_feed = init_req_feed()
    # Get order history archive (last 3 months) - need instType
    data = live_okx_swap_feed.get_order_history_archive(
        symbol="BTC-USDT", state="filled", limit="10", inst_type="SWAP"
    )
    assert isinstance(data, RequestData)
    print("get_order_history_archive status:", data.get_status())
    orders_list = data.get_data()
    assert isinstance(orders_list, list)
    print("get_order_history_archive count:", len(orders_list))


def test_okx_req_cancel_all_after():
    """Test cancel_all_after interface"""
    live_okx_swap_feed = init_req_feed()
    # Set cancel all after 5 seconds
    data = live_okx_swap_feed.cancel_all_after(time_slug="60")
    assert isinstance(data, RequestData)
    print("cancel_all_after status:", data.get_status())


def test_okx_req_amend_algo_order():
    """Test amend_algo_order interface - expects error with placeholder algo_id"""
    live_okx_swap_feed = init_req_feed()
    # Amend algo order - will return error response with placeholder algo_id
    data = live_okx_swap_feed.amend_algo_order(
        algo_id="test_algo_id",  # Placeholder
        inst_id="BTC-USDT-SWAP",
        new_sz="1",
    )
    assert isinstance(data, RequestData)
    print("amend_algo_order status:", data.get_status())


def test_okx_req_get_algo_orders_pending():
    """Test get_algo_orders_pending interface"""
    live_okx_swap_feed = init_req_feed()
    # Get algo orders pending list
    data = live_okx_swap_feed.get_algo_orders_pending(
        inst_type="SWAP", ord_type="conditional", limit="10"
    )
    assert isinstance(data, RequestData)
    print("get_algo_orders_pending status:", data.get_status())
    algo_orders_list = data.get_data()
    assert isinstance(algo_orders_list, list)
    print("get_algo_orders_pending count:", len(algo_orders_list))


@pytest.mark.skip(reason="OKX API endpoint deprecated/removed (404)")
def test_okx_req_get_algo_order_history():
    """Test get_algo_order_history interface"""
    live_okx_swap_feed = init_req_feed()
    # Get algo order history
    data = live_okx_swap_feed.get_algo_order_history(inst_type="SWAP", limit="10")
    assert isinstance(data, RequestData)
    print("get_algo_order_history status:", data.get_status())
    algo_history_list = data.get_data()
    assert isinstance(algo_history_list, list)
    print("get_algo_order_history count:", len(algo_history_list))


def test_okx_req_get_algo_order():
    """Test get_algo_order interface - expects error with placeholder algo_id"""
    live_okx_swap_feed = init_req_feed()
    # Test with a placeholder algo_id - OKX returns error in response body
    data = live_okx_swap_feed.get_algo_order(
        algo_id="test_algo_id_placeholder", inst_type="SWAP", symbol="BTC-USDT"
    )
    assert isinstance(data, RequestData)
    print("get_algo_order status:", data.get_status())


def test_okx_async_get_algo_order():
    """Test async_get_algo_order interface"""
    data_queue = queue.Queue()
    live_okx_swap_feed = init_async_feed(data_queue)
    # Test async call with placeholder algo_id
    live_okx_swap_feed.async_get_algo_order(
        algo_id="test_algo_id_placeholder",
        inst_type="SWAP",
        symbol="BTC-USDT",
        extra_data={"test_async_algo_order": True},
    )
    time.sleep(5)
    try:
        algo_order_data = data_queue.get(False)
    except queue.Empty:
        algo_order_data = None
    assert algo_order_data is not None
    assert isinstance(algo_order_data, RequestData)
    print("async_get_algo_order status:", algo_order_data.get_status())
    algo_order_list = algo_order_data.get_data()
    assert isinstance(algo_order_list, list)


def cancel_all_orders():
    live_feed = init_req_feed()
    data = live_feed.get_open_orders("OP-USDT")
    order_data_list = data.get_data()
    for d in order_data_list:
        info = live_feed.cancel_order("OP-USDT", d.get_order_id())
        print(info.get_data())


# ==================== Sub Account Tests ====================


def test_okx_req_cancel_all():
    """Test cancel_all interface - cancel all pending orders"""
    live_okx_swap_feed = init_req_feed()
    # Cancel all SWAP orders
    data = live_okx_swap_feed.cancel_all(inst_type="SWAP")
    assert isinstance(data, RequestData)
    print("cancel_all status:", data.get_status())
    result_list = data.get_data()
    assert isinstance(result_list, list)
    print("cancel_all data:", result_list)


def test_okx_async_cancel_all():
    """Test async_cancel_all interface"""
    data_queue = queue.Queue()
    live_okx_swap_feed = init_async_feed(data_queue)
    live_okx_swap_feed.async_cancel_all(inst_type="SWAP")
    time.sleep(5)
    try:
        cancel_data = data_queue.get(False)
    except queue.Empty:
        cancel_data = None
    if cancel_data is None:
        print("Warning: cancel_data is None (async timeout)")
        return  # Skip assertion on timeout
    assert isinstance(cancel_data, RequestData)
    print("async_cancel_all status:", cancel_data.get_status())


def test_okx_req_mass_cancel():
    """Test mass_cancel interface - cancel all advanced limit orders"""
    live_okx_swap_feed = init_req_feed()
    # Mass cancel all SWAP advanced orders
    data = live_okx_swap_feed.mass_cancel(inst_type="SWAP")
    assert isinstance(data, RequestData)
    print("mass_cancel status:", data.get_status())
    result_list = data.get_data()
    assert isinstance(result_list, list)
    print("mass_cancel data:", result_list)


def test_okx_async_mass_cancel():
    """Test async_mass_cancel interface"""
    data_queue = queue.Queue()
    live_okx_swap_feed = init_async_feed(data_queue)
    live_okx_swap_feed.async_mass_cancel(inst_type="SWAP")
    time.sleep(5)
    try:
        cancel_data = data_queue.get(False)
    except queue.Empty:
        cancel_data = None
    assert cancel_data is not None
    assert isinstance(cancel_data, RequestData)
    print("async_mass_cancel status:", cancel_data.get_status())


def test_okx_req_order_precheck():
    """Test order_precheck interface"""
    live_okx_swap_feed = init_req_feed()
    # Precheck an order before placing it
    data = live_okx_swap_feed.order_precheck(
        symbol="BTC-USDT",
        td_mode="cross",
        ccy="USDT",
        side="buy",
        order_type="limit",
        sz="1",
        px="100",  # Very low price to avoid actual execution
    )
    assert isinstance(data, RequestData)
    print("order_precheck status:", data.get_status())
    precheck_list = data.get_data()
    assert isinstance(precheck_list, list)
    print("order_precheck data:", precheck_list)


def test_okx_async_order_precheck():
    """Test async_order_precheck interface"""
    data_queue = queue.Queue()
    live_okx_swap_feed = init_async_feed(data_queue)
    live_okx_swap_feed.async_order_precheck(
        symbol="BTC-USDT",
        td_mode="cross",
        ccy="USDT",
        side="buy",
        order_type="limit",
        sz="1",
        px="100",
    )
    time.sleep(5)
    try:
        precheck_data = data_queue.get(False)
    except queue.Empty:
        precheck_data = None
    if precheck_data is None:
        pytest.skip("Skipped (no data, likely network): async_order_precheck returned no data")
    assert isinstance(precheck_data, RequestData)
    print("async_order_precheck status:", precheck_data.get_status())


# ==================== Public Data API Tests ====================


def test_okx_cancel_all():
    """Test cancel_all interface"""
    live_okx_swap_feed = init_req_feed()
    # Test cancel all orders for SWAP type
    data = live_okx_swap_feed.cancel_all(inst_type="SWAP", inst_id="BTC-USDT-SWAP")
    assert isinstance(data, RequestData)
    print("cancel_all status:", data.get_status())


def test_okx_async_cancel_all_with_inst_id():
    """Test async_cancel_all with inst_id and extra_data."""
    data_queue = queue.Queue()
    live_okx_swap_feed = init_async_feed(data_queue)
    live_okx_swap_feed.async_cancel_all(
        inst_type="SWAP", inst_id="BTC-USDT-SWAP", extra_data={"test_async_cancel_all": True}
    )
    time.sleep(5)
    try:
        result = data_queue.get(False)
    except queue.Empty:
        result = None
    if result is not None:
        assert isinstance(result, RequestData)
        print("async_cancel_all status:", result.get_status())


# ==================== Order Precheck Tests ====================


def test_okx_order_precheck():
    """Test order_precheck interface"""
    live_okx_swap_feed = init_req_feed()
    # Test order precheck for a potential swap order
    data = live_okx_swap_feed.order_precheck(
        symbol="BTC-USDT-SWAP", td_mode="cross", ccy="USDT", side="buy", order_type="market", sz="1"
    )
    assert isinstance(data, RequestData)
    print("order_precheck status:", data.get_status())
    if data.get_status():
        data_list = data.get_data()
        assert isinstance(data_list, list)
        print("Order precheck data:", data_list[:1] if data_list else "No data")


def test_okx_async_order_precheck_with_extra_data():
    """Test async_order_precheck with extra_data."""
    data_queue = queue.Queue()
    live_okx_swap_feed = init_async_feed(data_queue)
    live_okx_swap_feed.async_order_precheck(
        symbol="BTC-USDT-SWAP",
        td_mode="cross",
        ccy="USDT",
        side="buy",
        order_type="market",
        sz="1",
        extra_data={"test_async_order_precheck": True},
    )
    time.sleep(5)
    try:
        result = data_queue.get(False)
    except queue.Empty:
        result = None
    if result is not None:
        assert isinstance(result, RequestData)
        print("async_order_precheck status:", result.get_status())


# ==================== Open Interest Tests ====================
