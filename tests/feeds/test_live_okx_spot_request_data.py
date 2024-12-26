import queue
import time
import random
from bt_api_py.functions.utils import read_yaml_file, get_public_ip
from bt_api_py.feeds.live_okx_feed import OkxRequestDataSpot
from bt_api_py.containers.exchanges.okx_exchange_data import OkxExchangeDataSpot
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


def generate_kwargs():
    data = read_yaml_file("account_config.yaml")
    kwargs = {
        "public_key": data['okx']['public_key'],
        "private_key": data['okx']['private_key'],
        "passphrase": data['okx']["passphrase"],
        "exchange_data": OkxExchangeDataSpot(),
        "topics": {"tick": {"symbol": "BTC-USDT"}}
    }
    return kwargs


def init_req_feed():
    data_queue = queue.Queue()
    kwargs = generate_kwargs()
    live_okx_spot_feed = OkxRequestDataSpot(data_queue, **kwargs)
    return live_okx_spot_feed


def init_async_feed(data_queue):
    kwargs = generate_kwargs()
    live_okx_spot_feed = OkxRequestDataSpot(data_queue, **kwargs)
    return live_okx_spot_feed


def test_okx_req_get_index_price():
    live_okx_spot_feed = init_req_feed()
    data = live_okx_spot_feed.get_index_price(symbol="OP-USDT")
    assert isinstance(data, RequestData)
    print("index_price_data", data.get_data())


def test_okx_req_spot_tick_data():
    live_okx_spot_feed = init_req_feed()
    data_list = live_okx_spot_feed.get_tick("BTC-USDT").get_data()
    tick_data = data_list[0].init_data()
    assert isinstance(data_list, list)
    assert tick_data.get_server_time() > 0.0
    assert tick_data.get_exchange_name() == "OKX"
    assert tick_data.get_symbol_name() == "BTC-USDT"
    assert tick_data.get_bid_price() > 0
    assert tick_data.get_bid_volume() >= 0
    assert tick_data.get_ask_price() > 0
    assert tick_data.get_ask_volume() >= 0
    assert tick_data.get_last_price() > 0
    assert tick_data.get_last_volume() >= 0


def test_okx_async_spot_tick_data():
    data_queue = queue.Queue()
    live_okx_spot_feed = init_async_feed(data_queue)
    live_okx_spot_feed.async_get_tick("BTC-USDT", extra_data={"test_async_tick_data": True})
    time.sleep(1)
    try:
        tick_data = data_queue.get(False)
    except queue.Empty:
        tick_data = None
    assert isinstance(tick_data, RequestData)
    tick_data_list = tick_data.get_data()
    async_tick_data = tick_data_list[0].init_data()
    # 检测tick数据
    assert tick_data_list is not None
    assert isinstance(tick_data_list, list)
    assert isinstance(async_tick_data, OkxTickerData)
    assert async_tick_data.get_symbol_name() == "BTC-USDT"
    assert async_tick_data.get_bid_price() > 0
    assert async_tick_data.get_ask_price() > 0
    assert async_tick_data.get_last_price() > 0


def test_okx_req_spot_kline_data():
    live_okx_spot_feed = init_req_feed()
    data_list = live_okx_spot_feed.get_kline("BTC-USDT", "1m", count=2).get_data()
    kline_data = data_list[0].init_data()
    assert isinstance(data_list, list)
    assert kline_data.get_server_time() > 1597026383085.0
    assert kline_data.get_exchange_name() == "OKX"
    assert kline_data.get_symbol_name() == "BTC-USDT"
    assert kline_data.get_local_update_time() > 0
    assert kline_data.get_open_price() > 0
    assert kline_data.get_high_price() >= 0
    assert kline_data.get_low_price() > 0
    assert kline_data.get_close_price() >= 0
    assert kline_data.get_volume() >= 0
    assert kline_data.get_quote_asset_volume() >= 0
    assert kline_data.get_base_asset_volume() >= 0
    assert kline_data.get_bar_status() in [0.0, 1.0]


def test_okx_async_spot_kline_data():
    data_queue = queue.Queue()
    live_okx_spot_feed = init_async_feed(data_queue)
    live_okx_spot_feed.async_get_kline("BTC-USDT", period="1m", count=3,
                                       extra_data={"test_async_kline_data": True})

    time.sleep(1)
    try:
        kline_data = data_queue.get(False)
    except queue.Empty:
        kline_data = None
    assert isinstance(kline_data, RequestData)
    target_data_list = kline_data.get_data()
    async_kline_data = target_data_list[0].init_data()
    # print("target_data:", target_data)
    assert target_data_list is not None
    assert isinstance(target_data_list, list)
    assert isinstance(async_kline_data, OkxBarData)
    assert async_kline_data.get_exchange_name() == "OKX"
    assert async_kline_data.get_symbol_name() == "BTC-USDT"
    assert async_kline_data.get_open_price() > 0
    assert async_kline_data.get_high_price() > 0
    assert async_kline_data.get_low_price() > 0
    assert async_kline_data.get_close_price() > 0
    assert async_kline_data.get_volume() >= 0


def order_book_value_equals(order_book):
    assert isinstance(order_book, OkxOrderBookData)
    assert order_book.get_server_time() > 0
    assert order_book.get_exchange_name() == "OKX"
    assert order_book.get_symbol_name() == "BTC-USDT"
    assert order_book.get_asset_type() == "SPOT"
    assert order_book.get_bid_price_list()[0] > 0
    assert order_book.get_bid_volume_list()[-1] >= 0
    assert order_book.get_ask_price_list()[-1] > 0
    assert order_book.get_ask_volume_list()[-1] >= 0
    assert order_book.get_bid_trade_nums()[0] >= 0
    assert order_book.get_ask_trade_nums()[-1] >= 0
    assert len(order_book.get_bid_price_list()) == 20


def test_okx_req_spot_depth_data():
    live_okx_spot_feed = init_req_feed()
    data = live_okx_spot_feed.get_depth("BTC-USDT", 20).get_data()
    print("data asset_type", data[0].get_asset_type())
    assert isinstance(data, list)
    order_book_value_equals(data[0].init_data())


def test_okx_async_spot_depth_data():
    data_queue = queue.Queue()
    live_okx_spot_feed = init_async_feed(data_queue)
    live_okx_spot_feed.async_get_depth("BTC-USDT", 20)
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


def assert_mark_price_data_value(bp):
    assert bp.get_server_time() > 0
    assert bp.get_exchange_name() == "OKX"
    assert bp.get_symbol_name() == "BTC-USDT"
    assert bp.get_mark_price() > 0
    assert bp.get_asset_type() == "SPOT"
    assert bp.get_event() == "MarkPriceEvent"


def test_okx_req_spot_mark_price_data():
    live_okx_spot_feed = init_req_feed()
    data = live_okx_spot_feed.get_mark_price("BTC-USDT").get_data()
    assert isinstance(data, list)
    assert isinstance(data[0], OkxMarkPriceData)
    assert_mark_price_data_value(data[0].init_data())


def test_okx_async_spot_mark_price_data():
    data_queue = queue.Queue()
    live_okx_spot_feed = init_async_feed(data_queue)
    live_okx_spot_feed.async_get_mark_price("BTC-USDT")
    time.sleep(1)
    try:
        depth_data = data_queue.get(False)
    except queue.Empty:
        target_data = None
    else:
        target_data = depth_data.get_data()

    assert isinstance(target_data, list)
    assert isinstance(target_data[0], OkxMarkPriceData)
    assert_mark_price_data_value(target_data[0].init_data())


def assert_account_data_value(bp):
    assert bp.get_server_time() > 0
    assert bp.get_exchange_name() == "OKX"
    assert bp.get_total_margin() > 0
    assert bp.get_asset_type() == "SPOT"
    assert bp.get_event() == "AccountEvent"


def test_okx_req_spot_account_data():
    live_okx_spot_feed = init_req_feed()
    data = live_okx_spot_feed.get_account().get_data()
    # print(data)
    assert isinstance(data[0], OkxAccountData)
    assert_account_data_value(data[0].init_data())


def test_okx_async_spot_account_data():
    data_queue = queue.Queue()
    live_okx_spot_feed = init_async_feed(data_queue)
    live_okx_spot_feed.async_get_account()
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


def okx_req_spot_query_order_by_client_order_id(client_order_id):
    live_okx_spot_feed = init_req_feed()
    kwargs = {"client_order_id": client_order_id}
    data = live_okx_spot_feed.query_order("OP-USDT", **kwargs)
    # print(data.get_data())
    data.init_data()
    assert data.get_status()
    assert isinstance(data, RequestData)
    return data


def okx_req_spot_get_open_order():
    live_okx_spot_feed = init_req_feed()
    data = live_okx_spot_feed.get_open_orders()
    print(data.get_data())
    assert isinstance(data, RequestData)
    return data


def okx_req_spot_cancel_order_by_order_id(order_id):
    live_okx_spot_feed = init_req_feed()
    data = live_okx_spot_feed.cancel_order("OP-USDT", order_id=order_id)
    print(data.get_data())
    assert isinstance(data, RequestData)
    return data


def okx_req_spot_cancel_order_by_client_order_id(client_order_id):
    live_okx_spot_feed = init_req_feed()
    data = live_okx_spot_feed.cancel_order("OP-USDT", client_order_id=client_order_id)
    print(data.get_data())
    assert isinstance(data, RequestData)
    return data


def test_okx_req_spot_order_functions():
    live_okx_spot_feed = init_req_feed()
    price_data = live_okx_spot_feed.get_tick("OP-USDT")
    price_data = price_data.get_data()[0].init_data()
    bid_price = round(price_data.get_bid_price() * 0.9, 2)
    ask_price = round(price_data.get_ask_price() * 1.1, 2)
    random_number = random.randint(10 ** 17, 10 ** 18 - 1)
    buy_client_order_id = str(random_number)
    sell_client_order_id = str(random_number + 1)
    lots = 0
    while lots * ask_price < 10:
        lots += 1
    buy_data = live_okx_spot_feed.make_order("OP-USDT", lots, bid_price, "buy-limit",
                                             client_order_id=buy_client_order_id)
    sell_data = live_okx_spot_feed.make_order("OP-USDT", lots, ask_price, "sell-limit",
                                              client_order_id=sell_client_order_id)
    # 测试买单和卖单
    # assert buy_data.get_status()
    buy_info = buy_data.get_data()[0]
    assert isinstance(buy_data, RequestData)
    assert isinstance(buy_info, dict)
    buy_order_id = buy_info.get("order_id")
    assert buy_order_id is not None

    sell_info = sell_data.get_data()[0]
    assert isinstance(sell_data, RequestData)
    assert isinstance(sell_info, dict)
    sell_order_id = sell_info.get("order_id")
    assert sell_order_id is not None

    # 根据order_id查询订单
    data = live_okx_spot_feed.query_order("OP-USDT", order_id=buy_order_id)
    # print(data.get_data())
    order_info = data.get_data()[0]
    assert data.get_status()
    assert isinstance(data, RequestData)
    assert order_info.init_data().get_order_price() == bid_price
    assert order_info.init_data().get_client_order_id() == buy_client_order_id

    data = live_okx_spot_feed.query_order("OP-USDT", order_id=sell_order_id)
    # print(data.get_data())
    order_info = data.get_data()[0]
    assert data.get_status()
    assert isinstance(data, RequestData)
    assert order_info.init_data().get_order_price() == ask_price
    assert order_info.init_data().get_client_order_id() == sell_client_order_id
    # 根据client_order_id查询订单
    okx_req_spot_query_order_by_client_order_id(buy_client_order_id)
    okx_req_spot_query_order_by_client_order_id(sell_client_order_id)
    # 查询有哪些open_order
    orders = okx_req_spot_get_open_order()
    assert len(orders.get_data()) >= 1
    # 用order_id和client_order_id进行撤单
    okx_req_spot_cancel_order_by_order_id(buy_order_id)
    okx_req_spot_cancel_order_by_client_order_id(sell_client_order_id)
    # Querying orders can only be done during single-backtrader testing.
    # When executing across multiple backtrader, the order of execution is not guaranteed,
    # which can lead to conflicts and errors.
    # orders = okx_req_get_open_order()
    # print('test_okx_req_order_functions()', orders.get_data())
    # assert orders.get_data() is None


def test_okx_async_spot_order_functions():
    data_queue = queue.Queue()
    live_okx_spot_feed = init_async_feed(data_queue)
    price_data = live_okx_spot_feed.get_tick("OP-USDT").get_data()[0].init_data()
    bid_price = round(price_data.get_bid_price() * 0.9, 2)
    # ask_price = round(price_data.get_ask_price() * 1.1, 2)
    random_number = random.randint(10 ** 17, 10 ** 18 - 1)
    buy_client_order_id = str(random_number)
    # sell_client_order_id = str(random_number + 1)
    make_order_func = False
    query_order_func = False
    cancel_order_func = False
    open_order_func = False
    lots = 0
    while lots * bid_price < 10:
        lots += 1
    live_okx_spot_feed.async_make_order("OP-USDT", lots, bid_price, "buy-limit", client_order_id=buy_client_order_id)
    time.sleep(1)
    live_okx_spot_feed.async_query_order("OP-USDT", **{"client_order_id": buy_client_order_id})
    live_okx_spot_feed.async_get_open_orders()
    time.sleep(1)
    live_okx_spot_feed.async_cancel_order("OP-USDT", **{"client_order_id": buy_client_order_id})
    time.sleep(1.5)
    # live_okx_spot_feed.async_get_open_orders("OP-USDT")
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
            print("MakeOrderRequestEvent", target_data.get_data())
            assert target_data.get_data()[0].get("client_order_id") == buy_client_order_id
            make_order_func = True
        if event_type == "RequestEvent" and request_type == "query_order":
            assert target_data.get_status()
            print("QueryOrderRequestEvent", target_data.get_data())
            assert target_data.get_data()[0].init_data().get_client_order_id() == buy_client_order_id
            query_order_func = True
        if event_type == "RequestEvent" and request_type == 'cancel_order':
            assert target_data.get_status()
            print("CancelOrderRequestEvent", target_data.get_data())
            cancel_order_func = True
        if event_type == "RequestEvent" and request_type == 'get_open_orders':
            assert target_data.get_status()
            print("GetOpenOrdersRequestEvent", target_data.get_data())
            assert target_data.get_data() is not None
            open_order_func = True
        if open_order_func and cancel_order_func and query_order_func and make_order_func:
            break


# # def make_a_order():
# #     live_okx_spot_feed = init_req_feed()
# #     random_number = random.randint(10 ** 17, 10 ** 18 - 1)
# #     sell_client_order_id = str(random_number + 1)
# #     ask_price = 3.7800
# #     sell_data = live_okx_spot_feed.make_order("OP-USDT", 1, ask_price, "sell-limit",
# #                                               client_order_id=sell_client_order_id)
# #     print(sell_data.get_data())
#
#
def test_okx_req_spot_get_deals():
    live_okx_spot_feed = init_req_feed()
    price_data = live_okx_spot_feed.get_deals()
    trade_data = price_data.get_data()
    if len(trade_data) > 0:
        first_trade = trade_data[0].init_data()
        print(first_trade)
        assert trade_data is not None, "如果账户没有交易过，忽略此报错"
        assert isinstance(trade_data, list), "如果账户没有交易过，忽略此报错"
        assert first_trade.get_trade_price() > 0, "如果账户没有交易过，忽略此报错"
        assert first_trade.get_trade_volume() > 0, "如果账户没有交易过，忽略此报错"


def test_okx_async_spot_get_deals():
    data_queue = queue.Queue()
    live_okx_spot_feed = init_async_feed(data_queue)
    live_okx_spot_feed.async_get_deals()
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
            assert target_data is not None, "如果账户没有交易过，忽略此报错"
            assert isinstance(target_data, list), "如果账户没有交易过，忽略此报错"
            assert first_trade.get_trade_price() > 0, "如果账户没有交易过，忽略此报错"
            assert first_trade.get_trade_volume() > 0, "如果账户没有交易过，忽略此报错"


def test_okx_req_spot_get_config():
    live_okx_spot_feed = init_req_feed()
    data = live_okx_spot_feed.get_config()
    assert isinstance(data, RequestData)
    public_ip = get_public_ip()
    assert isinstance(public_ip, str)
    config_data = data.get_data()[0]
    print('config_data', config_data)
    api_ip = config_data.get("ip")
    print(public_ip, api_ip)
    assert public_ip in api_ip, "需要绑定当前ip地址到okx的API当中"


def test_okx_req_spot_get_position():
    live_okx_spot_feed = init_req_feed()
    data = live_okx_spot_feed.get_position(symbol="OP-USDT")
    assert isinstance(data, RequestData)
    print("position_data", data.get_data())


def test_okx_async_spot_get_position():
    data_queue = queue.Queue()
    live_okx_spot_feed = init_async_feed(data_queue)
    live_okx_spot_feed.async_get_position(symbol="OP-USDT")
    time.sleep(1)
    try:
        position_data = data_queue.get(False)
        # print("position_data", position_data)
    except queue.Empty:
        position_data = None
    if len(position_data.get_data()) > 0:
        target_data = position_data.get_data()[0]
        assert isinstance(position_data, RequestData)
        assert isinstance(target_data, OkxPositionData)


def test_cancel_all_orders():
    live_feed = init_req_feed()
    data = live_feed.get_open_orders('OP-USDT')
    order_data_list = data.get_data()
    for d in order_data_list:
        d = d.init_data()
        # print("order", d)
        order_id = d.get_order_id()
        print("order_id", order_id)
        if order_id is not None:
            info = live_feed.cancel_order('OP-USDT', order_id=order_id)
            print(info.get_data())
    assert 1


if __name__ == "__main__":
    generate_kwargs()
    test_okx_req_get_index_price()
    # test_okx_req_spot_tick_data()
    # test_okx_async_spot_tick_data()
    # test_okx_req_spot_kline_data()
    # test_okx_async_spot_kline_data()
    # test_okx_async_kline_data()
    # test_okx_req_spot_depth_data()
    # test_okx_async_spot_depth_data()
    # test_okx_req_spot_mark_price_data()
    # test_okx_async_spot_mark_price_data()
    # test_okx_req_spot_account_data()
    # test_okx_async_spot_account_data()
    # test_okx_req_spot_order_functions()
    # test_okx_async_spot_order_functions()
    # test_okx_req_spot_get_deals()
    # test_okx_async_spot_get_deals()
    # make_a_order()
    # test_okx_req_spot_get_config()
    # test_okx_req_spot_get_position()
    # test_okx_async_spot_get_position()
    test_cancel_all_orders()
