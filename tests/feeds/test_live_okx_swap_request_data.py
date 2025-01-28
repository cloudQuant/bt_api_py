import queue
import time
import random
from bt_api_py.functions.utils import read_yaml_file, get_public_ip
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
# from bt_api_py.containers.trades.okx_trade import OkxRequestTradeData, OkxWssTradeData
from bt_api_py.containers.positions.okx_position import OkxPositionData


def test_get_okx_key():
    data = read_yaml_file("account_config.yaml")
    public_key = data['okx']['public_key']
    private_key = data['okx']['private_key'] + "//" + data['okx']["passphrase"]
    assert len(public_key) == 36, "public key is wrong"
    assert len(private_key) > 0, "private key is wrong"


def generate_kwargs(exchange=OkxExchangeDataSwap):
    data = read_yaml_file("account_config.yaml")
    kwargs = {
        "public_key": data['okx']['public_key'],
        "private_key": data['okx']['private_key'],
        "passphrase": data['okx']["passphrase"],
        "topics": {"tick": {"symbol": "BTC-USDT"}}
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


def test_okx_req_tick_data():
    live_okx_swap_feed = init_req_feed()
    data_list = live_okx_swap_feed.get_tick("BTC-USDT").get_data()
    swap_tick_data = data_list[0].init_data()
    assert isinstance(data_list, list)
    assert swap_tick_data.get_server_time() > 1597026383085.0
    assert swap_tick_data.get_exchange_name() == "OKX"
    assert swap_tick_data.get_symbol_name() == "BTC-USDT"
    assert swap_tick_data.get_bid_price() > 0
    assert swap_tick_data.get_bid_volume() >= 0
    assert swap_tick_data.get_ask_price() > 0
    assert swap_tick_data.get_ask_volume() >= 0
    assert swap_tick_data.get_last_price() > 0
    assert swap_tick_data.get_last_volume() >= 0


def test_okx_async_tick_data():
    data_queue = queue.Queue()
    live_okx_swap_feed = init_async_feed(data_queue)
    live_okx_swap_feed.async_get_tick("BTC-USDT", extra_data={"test_async_tick_data": True})
    time.sleep(1)
    try:
        tick_data = data_queue.get(False)
    except queue.Empty:
        pass
    else:
        tick_data = tick_data.get_data()
        # 检测tick数据
        assert tick_data is not None
        assert isinstance(tick_data, list)
        swap_async_tick_data = tick_data[0].init_data()
        assert isinstance(swap_async_tick_data, OkxTickerData)
        assert "SWAP" not in swap_async_tick_data.get_symbol_name()
        assert swap_async_tick_data.get_bid_price() > 0
        assert swap_async_tick_data.get_ask_price() > 0
        assert swap_async_tick_data.get_last_price() > 0


def test_okx_req_kline_data():
    live_okx_swap_feed = init_req_feed()
    data_list = live_okx_swap_feed.get_kline("BTC-USDT", "1m", count=2).get_data()
    assert isinstance(data_list, list)
    swap_kline_data = data_list[0].init_data()
    assert swap_kline_data.get_server_time() > 1597026383085.0
    assert swap_kline_data.get_exchange_name() == "OKX"
    assert swap_kline_data.get_symbol_name() == "BTC-USDT"
    assert swap_kline_data.get_local_update_time() > 0
    assert swap_kline_data.get_open_price() > 0
    assert swap_kline_data.get_high_price() >= 0
    assert swap_kline_data.get_low_price() > 0
    assert swap_kline_data.get_close_price() >= 0
    assert swap_kline_data.get_volume() >= 0
    assert swap_kline_data.get_quote_asset_volume() >= 0
    assert swap_kline_data.get_base_asset_volume() >= 0
    assert swap_kline_data.get_bar_status() in [0.0, 1.0]


def test_okx_async_kline_data():
    data_queue = queue.Queue()
    live_okx_swap_feed = init_async_feed(data_queue)
    live_okx_swap_feed.async_get_kline("BTC-USDT", period="1m",
                                       extra_data={"test_async_kline_data": True})

    time.sleep(1)
    try:
        kline_data = data_queue.get(False)
    except queue.Empty:
        target_data = None
    else:
        target_data = kline_data.get_data()
        # 检测kline数据
    assert target_data is not None
    assert isinstance(target_data, list)
    swap_async_kline_data = target_data[0].init_data()
    assert isinstance(swap_async_kline_data, OkxBarData)
    assert swap_async_kline_data.get_exchange_name() == "OKX"
    assert swap_async_kline_data.get_symbol_name() == "BTC-USDT"
    assert swap_async_kline_data.get_open_price() > 0
    assert swap_async_kline_data.get_high_price() > 0
    assert swap_async_kline_data.get_low_price() > 0
    assert swap_async_kline_data.get_close_price() > 0
    assert swap_async_kline_data.get_volume() >= 0


def order_book_value_equals(order_book):
    assert isinstance(order_book, OkxOrderBookData)
    assert order_book.get_server_time() > 0
    assert order_book.get_exchange_name() == "OKX"
    assert order_book.get_symbol_name() == "BTC-USDT"
    assert order_book.get_asset_type() == "SWAP"
    assert order_book.get_bid_price_list()[0] > 0
    assert order_book.get_bid_volume_list()[-1] >= 0
    assert order_book.get_ask_price_list()[-1] > 0
    assert order_book.get_ask_volume_list()[-1] >= 0
    assert order_book.get_bid_trade_nums()[0] >= 0
    assert order_book.get_ask_trade_nums()[-1] >= 0
    assert len(order_book.get_bid_price_list()) == 20


def test_okx_req_depth_data():
    live_okx_swap_feed = init_req_feed()
    data = live_okx_swap_feed.get_depth("BTC-USDT", 20).get_data()
    assert isinstance(data, list)
    order_book_value_equals(data[0].init_data())


def test_okx_async_depth_data():
    data_queue = queue.Queue()
    live_okx_swap_feed = init_async_feed(data_queue)
    live_okx_swap_feed.async_get_depth("BTC-USDT", 20)
    time.sleep(1)
    try:
        depth_data = data_queue.get(False)
    except queue.Empty:
        target_data = None
    else:
        target_data = depth_data.get_data()
    assert target_data is not None
    assert isinstance(target_data, list)
    order_book_value_equals(target_data[0].init_data())


def assert_funding_rate_value(bf):
    assert bf.get_pre_funding_rate() is None
    assert bf.get_pre_funding_time() is None
    # assert isinstance(bf.get_next_funding_rate(), float)
    assert bf.get_next_funding_time() > 0
    assert bf.get_server_time() > 0
    assert bf.get_event_type() == "FundingEvent"
    assert isinstance(bf.get_current_funding_rate(), float)
    assert bf.get_current_funding_time() > 0
    assert bf.get_max_funding_rate() >= 0
    assert bf.get_min_funding_rate() <= 0
    assert isinstance(bf.get_settlement_funding_rate(), float)
    assert bf.get_settlement_status() in ["settled", "processing"]
    assert bf.get_method() in ["next_period", "current_period"]


def test_okx_req_funding_rate_data():
    live_okx_swap_feed = init_req_feed()
    data = live_okx_swap_feed.get_funding_rate("BTC-USDT").get_data()
    assert isinstance(data, list)
    assert isinstance(data[0], OkxFundingRateData)
    assert_funding_rate_value(data[0].init_data())


def test_okx_async_funding_rate_data():
    data_queue = queue.Queue()
    live_okx_swap_feed = init_async_feed(data_queue)
    live_okx_swap_feed.async_get_funding_rate("BTC-USDT")
    time.sleep(1)
    try:
        depth_data = data_queue.get(False)
    except queue.Empty:
        target_data = None
    else:
        target_data = depth_data.get_data()
        # 检测kline数据
    assert isinstance(target_data, list)
    assert isinstance(target_data[0], OkxFundingRateData)
    assert_funding_rate_value(target_data[0].init_data())


def assert_mark_price_data_value(bp):
    assert bp.get_server_time() > 0
    assert bp.get_exchange_name() == "OKX"
    assert bp.get_symbol_name() == "BTC-USDT"
    assert bp.get_mark_price() > 0
    assert bp.get_asset_type() == "SPOT"
    assert bp.get_event() == "MarkPriceEvent"


def test_okx_req_mark_price_data():
    live_okx_swap_feed = init_req_feed()
    data = live_okx_swap_feed.get_mark_price("BTC-USDT").get_data()
    assert isinstance(data, list)
    assert isinstance(data[0], OkxMarkPriceData)
    assert_mark_price_data_value(data[0].init_data())


def test_okx_async_mark_price_data():
    data_queue = queue.Queue()
    live_okx_swap_feed = init_async_feed(data_queue)
    live_okx_swap_feed.async_get_mark_price("BTC-USDT")
    time.sleep(1)
    try:
        depth_data = data_queue.get(False)
    except queue.Empty:
        target_data = None
    else:
        target_data = depth_data.get_data()
        # 检测kline数据
    assert isinstance(target_data, list)
    assert isinstance(target_data[0], OkxMarkPriceData)
    assert_mark_price_data_value(target_data[0].init_data())


def assert_account_data_value(bp):
    assert bp.get_server_time() > 0
    assert bp.get_exchange_name() == "OKX"
    assert bp.get_total_margin() > 0
    assert bp.get_asset_type() == "SWAP"
    assert bp.get_event() == "AccountEvent"


def test_okx_req_account_data():
    live_okx_swap_feed = init_req_feed()
    data = live_okx_swap_feed.get_account().get_data()
    # print(data)
    assert isinstance(data[0], OkxAccountData)
    assert_account_data_value(data[0].init_data())


def test_okx_async_account_data():
    data_queue = queue.Queue()
    live_okx_swap_feed = init_async_feed(data_queue)
    live_okx_swap_feed.async_get_account()
    time.sleep(1)
    try:
        depth_data = data_queue.get(False)
    except queue.Empty:
        target_data = None
    else:
        target_data = depth_data.get_data()
        # 检测kline数据
    assert target_data is not None
    assert isinstance(target_data[0], OkxAccountData)
    assert_account_data_value(target_data[0].init_data())


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


def test_okx_req_order_functions():
    live_okx_swap_feed = init_req_feed()
    price_data = live_okx_swap_feed.get_tick("OP-USDT")
    price_data = price_data.get_data()[0].init_data()
    bid_price = round(price_data.get_bid_price() * 0.9, 2)
    ask_price = round(price_data.get_ask_price() * 1.1, 2)
    random_number = random.randint(10 ** 17, 10 ** 18 - 1)
    buy_client_order_id = str(random_number)
    buy_data = live_okx_swap_feed.make_order("OP-USDT", 2, bid_price, "buy-limit",
                                             client_order_id=buy_client_order_id)
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


def test_okx_async_order_functions():
    data_queue = queue.Queue()
    live_okx_swap_feed = init_async_feed(data_queue)
    price_data = live_okx_swap_feed.get_tick("OP-USDT").get_data()[0].init_data()
    bid_price = round(price_data.get_bid_price() * 0.9, 2)
    # ask_price = round(price_data.get_ask_price() * 1.1, 2)
    random_number = random.randint(10 ** 17, 10 ** 18 - 1)
    buy_client_order_id = str(random_number)
    # sell_client_order_id = str(random_number + 1)
    make_order_func = False
    query_order_func = False
    cancel_order_func = False
    open_order_func = False
    live_okx_swap_feed.async_make_order("OP-USDT", 1, bid_price, "buy-limit", client_order_id=buy_client_order_id)
    time.sleep(1)
    live_okx_swap_feed.async_query_order("OP-USDT", **{"client_order_id": buy_client_order_id})
    live_okx_swap_feed.async_get_open_orders()
    time.sleep(1)
    live_okx_swap_feed.async_cancel_order("OP-USDT", **{"client_order_id": buy_client_order_id})
    time.sleep(1)
    # live_okx_swap_feed.async_get_open_orders("OP-USDT")
    while True:
        try:
            target_data = data_queue.get(False)
        except queue.Empty:
            target_data = None
            pass
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
            assert target_data.get_data()[0].init_data().get_client_order_id() == buy_client_order_id
            query_order_func = True
        if event_type == "RequestEvent" and request_type == 'cancel_order':
            assert target_data.get_status()
            print("CancelOrderRequestEvent", event_data)
            cancel_order_func = True
        if event_type == "RequestEvent" and request_type == 'get_open_orders':
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


def test_okx_async_get_deals():
    data_queue = queue.Queue()
    live_okx_swap_feed = init_async_feed(data_queue)
    live_okx_swap_feed.async_get_deals()
    time.sleep(1)
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


def test_okx_req_get_config():
    public_ip = get_public_ip()
    assert isinstance(public_ip, str)
    print("当前出口ip = ", public_ip)
    live_okx_swap_feed = init_req_feed()
    data = live_okx_swap_feed.get_config()
    assert isinstance(data, RequestData)

    config_data = data.get_data()[0]
    print('config_data', config_data)
    api_ip = config_data.get("ip")
    print(public_ip, api_ip)
    assert public_ip in api_ip, "需要绑定当前ip地址到okx的API当中"


def test_okx_req_get_position():
    live_okx_swap_feed = init_req_feed()
    data = live_okx_swap_feed.get_position(symbol="OP-USDT")
    assert isinstance(data, RequestData)
    print("position_data", data.get_data())


def test_okx_async_get_position():
    data_queue = queue.Queue()
    live_okx_swap_feed = init_async_feed(data_queue)
    live_okx_swap_feed.async_get_position(symbol="OP-USDT")
    time.sleep(1)
    try:
        position_data = data_queue.get(False)
        # print("position_data", position_data)
    except queue.Empty:
        position_data = None
    total_position_data = position_data.get_data()
    if len(total_position_data):
        target_data = position_data.get_data()[0]
        assert isinstance(position_data, RequestData)
        assert isinstance(target_data, OkxPositionData)


def cancel_all_orders():
    live_feed = init_req_feed()
    data = live_feed.get_open_orders('OP-USDT')
    order_data_list = data.get_data()
    for d in order_data_list:
        info = live_feed.cancel_order('OP-USDT', d.get_order_id())
        print(info.get_data())


if __name__ == "__main__":
    test_get_okx_key()
    # test_okx_req_tick_data()
    # test_okx_req_get_config()
    # test_okx_async_tick_data()
    # test_okx_req_kline_data()
    # test_okx_async_kline_data()
    # test_okx_req_depth_data()
    # test_okx_async_depth_data()
    # test_okx_req_funding_rate_data()
    # test_okx_async_funding_rate_data()
    # test_okx_req_mark_price_data()
    # test_okx_async_mark_price_data()
    # test_okx_req_account_data()
    # test_okx_async_account_data()
    # test_okx_req_order_functions()
    # test_okx_async_order_functions()
    # test_okx_req_get_deals()
    # test_okx_async_get_deals()
    # make_a_order()
    # test_okx_req_get_config()
    # test_okx_req_get_position()
    # test_okx_async_get_position()
    cancel_all_orders()
