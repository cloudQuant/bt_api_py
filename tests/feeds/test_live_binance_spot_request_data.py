import queue
import time
import random
from bt_api_py.functions.utils import read_yaml_file
from bt_api_py.feeds.live_binance_feed import BinanceRequestDataSpot
from bt_api_py.containers.exchanges.binance_exchange_data import BinanceExchangeDataSpot
from bt_api_py.containers.orderbooks.binance_orderbook import BinanceRequestOrderBookData
from bt_api_py.containers.fundingrates.binance_funding_rate import BinanceRequestFundingRateData
from bt_api_py.containers.balances.binance_balance import BinanceSwapRequestBalanceData, BinanceSpotRequestBalanceData
from bt_api_py.containers.accounts.binance_account import BinanceSwapRequestAccountData, BinanceSpotRequestAccountData
from bt_api_py.containers.requestdatas.request_data import RequestData
from bt_api_py.containers.tickers.binance_ticker import BinanceRequestTickerData
from bt_api_py.containers.bars.binance_bar import BinanceRequestBarData


def test_get_binance_key():
    data = read_yaml_file("account_config.yaml")
    public_key = data['binance']['public_key']
    private_key = data['binance']['private_key']
    assert len(public_key) == 64, "public key is wrong"
    assert len(private_key) == 64, "private key is wrong"


def generate_kwargs(exchange=BinanceExchangeDataSpot):
    data = read_yaml_file("account_config.yaml")
    kwargs = {
        "public_key": data['binance']['public_key'],
        "private_key": data['binance']['private_key']
    }
    return kwargs


def init_req_feed():
    data_queue = queue.Queue()
    kwargs = generate_kwargs()
    live_binance_spot_feed = BinanceRequestDataSpot(data_queue, **kwargs)
    return live_binance_spot_feed


def init_async_feed(data_queue):
    kwargs = generate_kwargs()
    live_binance_spot_feed = BinanceRequestDataSpot(data_queue, **kwargs)
    return live_binance_spot_feed


def test_binance_req_server_time():
    live_binance_swap_feed = init_req_feed()
    data = live_binance_swap_feed.get_server_time()
    assert isinstance(data, RequestData)
    print(data.get_data())


def test_binance_req_tick_data():
    live_binance_spot_feed = init_req_feed()
    data = live_binance_spot_feed.get_tick("BTC-USDT").get_data()
    assert isinstance(data, list)
    tick_data = data[0].init_data()
    assert tick_data.get_server_time() is None
    assert tick_data.get_exchange_name() == "BINANCE"
    assert tick_data.get_symbol_name() == "BTC-USDT"
    assert tick_data.get_bid_price() >= 0
    assert tick_data.get_bid_volume() >= 0
    assert tick_data.get_ask_price() >= 0
    assert tick_data.get_ask_volume() >= 0
    assert tick_data.get_last_price() is None
    assert tick_data.get_last_volume() is None


def test_binance_async_tick_data():
    data_queue = queue.Queue()
    live_binance_spot_feed = init_async_feed(data_queue)
    live_binance_spot_feed.async_get_tick("BTC-USDT", extra_data={"test_async_tick_data": True})
    time.sleep(1)
    try:
        tick_data = data_queue.get(False)
    except queue.Empty:
        tick_data = None
    # 检测tick数据
    assert isinstance(tick_data, RequestData)
    assert isinstance(tick_data.get_data(), list)
    assert isinstance(tick_data.get_data()[0], BinanceRequestTickerData)
    async_tick_data = tick_data.get_data()[0].init_data()
    assert async_tick_data.get_exchange_name() == "BINANCE"
    assert async_tick_data.get_symbol_name() == "BTC-USDT"
    assert async_tick_data.get_bid_price() > 0
    assert async_tick_data.get_bid_volume() >= 0
    assert async_tick_data.get_ask_price() > 0
    assert async_tick_data.get_ask_volume() >= 0
    assert async_tick_data.get_last_price() is None
    assert async_tick_data.get_last_volume() is None


def test_binance_req_kline_data():
    live_binance_spot_feed = init_req_feed()
    data = live_binance_spot_feed.get_kline("BTC-USDT", "1m", count=2).get_data()
    assert isinstance(data, list)
    assert data[0].init_data().get_server_time() is None
    assert data[0].init_data().get_exchange_name() == "BINANCE"
    assert data[0].init_data().get_symbol_name() == "BTC-USDT"
    assert data[0].init_data().get_local_update_time() > 0
    assert data[0].init_data().get_open_price() > 0
    assert data[0].init_data().get_high_price() >= 0
    assert data[0].init_data().get_low_price() > 0
    assert data[0].init_data().get_close_price() >= 0
    assert data[0].init_data().get_volume() >= 0
    assert data[0].init_data().get_taker_buy_base_asset_volume() >= 0
    assert data[0].init_data().get_bar_status() is True


def test_binance_async_kline_data():
    data_queue = queue.Queue()
    live_binance_spot_feed = init_async_feed(data_queue)
    live_binance_spot_feed.async_get_kline("BTC-USDT", period="1m",
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
    bar_data = target_data[0].init_data()
    assert isinstance(bar_data, BinanceRequestBarData)
    assert bar_data.get_exchange_name() == "BINANCE"
    assert bar_data.get_symbol_name() == "BTC-USDT"
    assert bar_data.get_open_price() > 0
    assert bar_data.get_high_price() > 0
    assert bar_data.get_low_price() > 0
    assert bar_data.get_close_price() > 0
    assert bar_data.get_volume() >= 0


def order_book_value_equals(order_book):
    assert isinstance(order_book, BinanceRequestOrderBookData)
    assert order_book.get_server_time() is None
    assert order_book.get_exchange_name() == "BINANCE"
    assert order_book.get_symbol_name() == "BTC-USDT"
    assert order_book.get_asset_type() == "SPOT"
    assert order_book.get_bid_price_list()[0] > 0
    assert order_book.get_bid_volume_list()[-1] >= 0
    assert order_book.get_ask_price_list()[-1] > 0
    assert order_book.get_ask_volume_list()[-1] >= 0
    assert order_book.get_bid_trade_nums() is None
    assert order_book.get_ask_trade_nums() is None
    assert len(order_book.get_bid_price_list()) == 20


def test_binance_req_depth_data():
    live_binance_spot_feed = init_req_feed()
    data = live_binance_spot_feed.get_depth("BTC-USDT", 20).get_data()
    assert isinstance(data, list)
    order_book_value_equals(data[0].init_data())


def test_binance_async_depth_data():
    data_queue = queue.Queue()
    live_binance_spot_feed = init_async_feed(data_queue)
    live_binance_spot_feed.async_get_depth("BTC-USDT", 20)
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


def assert_account_data_value(bp):
    assert bp.get_server_time() >= 0.0
    assert bp.get_exchange_name() == "BINANCE"
    assert bp.get_total_margin() is None
    assert bp.get_asset_type() == "SPOT"
    assert bp.get_event() == "AccountEvent"


def test_binance_req_account_data():
    live_binance_spot_feed = init_req_feed()
    data = live_binance_spot_feed.get_account().get_data()
    # print(data)
    assert isinstance(data[0], BinanceSpotRequestAccountData)
    assert_account_data_value(data[0].init_data())


def test_binance_async_account_data():
    data_queue = queue.Queue()
    live_binance_spot_feed = init_async_feed(data_queue)
    live_binance_spot_feed.async_get_account()
    time.sleep(3)
    try:
        depth_data = data_queue.get(False)
    except queue.Empty:
        target_data = None
    else:
        target_data = depth_data.get_data()
        # 检测kline数据
    assert target_data is not None
    assert isinstance(target_data[0], BinanceSpotRequestAccountData)
    assert_account_data_value(target_data[0].init_data())


def test_binance_req_get_balance():
    live_binance_spot_feed = init_req_feed()
    data = live_binance_spot_feed.get_balance()
    data_list = data.get_data()
    balance_data = data_list[0].init_data().get_balances()[0]
    balance_data.init_data()
    assert isinstance(balance_data, BinanceSpotRequestBalanceData)
    assert isinstance(data, RequestData)
    # assert_account_data_value(data[0])
    # print(type(data))
    print("data.get_data()", balance_data.init_data())


def test_binance_async_get_balance():
    data_queue = queue.Queue()
    live_binance_spot_feed = init_async_feed(data_queue)
    live_binance_spot_feed.async_get_balance()
    time.sleep(1)
    try:
        depth_data = data_queue.get(False)
    except queue.Empty:
        target_data = None
    else:
        target_data = depth_data.get_data()
        # 检测kline数据
    assert target_data is not None
    assert isinstance(target_data[0], BinanceSpotRequestAccountData)


def binance_req_query_order_by_client_order_id(client_order_id):
    live_binance_spot_feed = init_req_feed()
    kwargs = {"client_order_id": client_order_id}
    data = live_binance_spot_feed.query_order("OP-USDT", **kwargs)
    print(data.get_data())
    assert data.get_status()
    assert isinstance(data, RequestData)
    return data


def binance_req_get_open_order():
    live_binance_spot_feed = init_req_feed()
    data = live_binance_spot_feed.get_open_orders()
    print(data.get_data())
    assert isinstance(data, RequestData)
    return data


def binance_req_cancel_order_by_order_id(order_id):
    live_binance_spot_feed = init_req_feed()
    data = live_binance_spot_feed.cancel_order("OP-USDT", order_id=order_id)
    print(data.get_data())
    assert isinstance(data, RequestData)
    return data


def binance_req_cancel_order_by_client_order_id(client_order_id):
    live_binance_spot_feed = init_req_feed()
    data = live_binance_spot_feed.cancel_order("OP-USDT", client_order_id=client_order_id)
    print(data.get_data())
    assert isinstance(data, RequestData)
    return data


def test_binance_make_order_and_cancel_order():
    live_binance_spot_feed = init_req_feed()
    price_data = live_binance_spot_feed.get_tick("OP-USDT")
    price_data = price_data.get_data()[0].init_data()
    bid_price = round(price_data.get_bid_price() * 0.95, 2)
    buy_client_order_id = "testOrder"
    vol = 0
    while vol * bid_price < 10:
        vol += 1
    # https://fapi.binance.com/fapi/v1/order?recvWindow=3000&timestamp=1708936750172&symbol=OPUSDT&side=BUY&quantity=2&price=3.5&type=LIMIT&timeInForce=GTC&positionSide=LONG&signature=72803587914b786ba57b9dccbf5b83c5dfc5c10da9d138c2dfc0bd6f105f7bcf
    buy_data = live_binance_spot_feed.make_order("OP-USDT",
                                                 vol, bid_price,
                                                 "buy-limit",
                                                 client_order_id=buy_client_order_id)

    print("make_order info", buy_data.get_data()[0].init_data())
    # "https://fapi.binance.com/fapi/v1/order?recvWindow=3000&timestamp=1708936667118&symbol=OPUSDT&orderId=9106714922&signature=69492a311ad22b37710e029a4e317ac5a7f43f7beadfdfba2caa20baf19a9a6b"
    # 查询订单信息
    query_data = live_binance_spot_feed.query_order(symbol="OP-USDT", client_order_id=buy_client_order_id)
    print("query_order", query_data.get_data()[0].init_data())
    # get_open_orders
    open_order_data = live_binance_spot_feed.get_open_orders(symbol="OP-USDT")
    print("get_open_orders: ", open_order_data.get_data()[0].init_data())
    cancel_order_data = live_binance_spot_feed.cancel_order("OP-USDT", order_id=None,
                                                            client_order_id=buy_client_order_id)
    print("cancel_order info", cancel_order_data.get_data()[0].init_data())


def test_binance_req_order_functions():
    live_binance_spot_feed = init_req_feed()
    price_data = live_binance_spot_feed.get_tick("OP-USDT")
    price_data = price_data.get_data()[0].init_data()
    bid_price = round(price_data.get_bid_price() * 0.9, 2)
    ask_price = round(price_data.get_ask_price() * 1.1, 2)
    random_number = random.randint(10 ** 17, 10 ** 18 - 1)
    buy_client_order_id = str(random_number)
    lots = 0
    while lots * bid_price < 5:
        lots += 1
    buy_data = live_binance_spot_feed.make_order("OP-USDT", lots,
                                                 bid_price, "buy-limit",
                                                 client_order_id=buy_client_order_id,
                                                 )
    # 没有现货的时候下单会报错
    # sell_data = live_binance_spot_feed.make_order("OP-USDT", 2,
    #                                               ask_price, "sell-limit",
    #                                               client_order_id=sell_client_order_id,
    #                                               )
    # 测试买单和卖单
    buy_info = buy_data.get_data()[0]
    assert buy_data.get_status()
    assert isinstance(buy_data, RequestData)
    buy_order_id = buy_info.init_data().get_order_id()
    assert buy_order_id is not None

    # assert sell_data.get_status()
    # assert isinstance(sell_data, BinanceMakeOrderRequestData)
    # assert isinstance(sell_data.get_data()[0], BinanceRequestOrderData)
    # sell_order_id = sell_data.get_data()[0].get_order_id()
    # assert sell_order_id is not None

    # 根据order_id查询订单
    data = live_binance_spot_feed.query_order("OP-USDT", order_id=int(buy_order_id))
    print("query_order_data", data.get_data()[0].init_data())
    assert data.get_status()
    assert isinstance(data, RequestData)
    assert data.get_data()[0].init_data().get_order_price() == bid_price
    assert data.get_data()[0].init_data().get_client_order_id() == buy_client_order_id
    # 根据client_order_id查询订单
    binance_req_query_order_by_client_order_id(buy_client_order_id)
    # binance_req_query_order_by_client_order_id(sell_client_order_id)
    # 查询有哪些open_order
    orders = binance_req_get_open_order()
    print("open_order", orders.get_data()[0].init_data())
    assert len(orders.get_data()) >= 1
    # 用order_id和client_order_id进行撤单
    binance_req_cancel_order_by_order_id(int(buy_order_id))
    # binance_req_cancel_order_by_client_order_id(sell_client_order_id)
    # Querying orders can only be done during single-backtrader testing.
    # When executing across multiple backtrader, the order of execution is not guaranteed,
    # which can lead to conflicts and errors.
    # orders = binance_req_get_open_order()
    # print('test_binance_req_order_functions()', orders.get_data())
    # assert orders.get_data() is None


def test_binance_async_order_functions():
    data_queue = queue.Queue()
    live_binance_spot_feed = init_async_feed(data_queue)
    price_data = live_binance_spot_feed.get_tick("OP-USDT").get_data()[0].init_data()
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
    while lots * bid_price < 5:
        lots += 1
    live_binance_spot_feed.async_make_order("OP-USDT", lots, bid_price,
                                            "buy-limit",
                                            client_order_id=buy_client_order_id,
                                            )
    time.sleep(2)
    live_binance_spot_feed.async_query_order("OP-USDT", **{"client_order_id": buy_client_order_id})
    time.sleep(1)
    live_binance_spot_feed.async_get_open_orders()
    time.sleep(1)
    live_binance_spot_feed.async_cancel_order("OP-USDT", **{"client_order_id": buy_client_order_id})
    time.sleep(5)
    # live_binance_spot_feed.async_get_open_orders("OP-USDT")
    while True:
        try:
            target_data = data_queue.get(False)
        except queue.Empty:
            target_data = None
            break
        event_data = target_data.get_data()
        event_type = target_data.get_event()
        request_type = target_data.get_request_type()
        if event_type == "RequestEvent" and request_type == "make_order":
            assert target_data.get_status()
            print("MakeOrderRequestEvent", event_data)
            assert target_data.get_data()[0].init_data().get_client_order_id() == buy_client_order_id
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
#     live_binance_spot_feed = init_req_feed()
#     random_number = random.randint(10 ** 17, 10 ** 18 - 1)
#     sell_client_order_id = str(random_number + 1)
#     ask_price = 3.79
#     sell_data = live_binance_spot_feed.make_order("OP-USDT", 2, ask_price, "sell-limit",
#                                                   client_order_id=sell_client_order_id, **{"position_side": "SHORT"})
#     print(sell_data.get_data()[0].init_data())


def test_cancel_all_orders():
    print("cancel_all_orders")
    live_feed = init_req_feed()
    data = live_feed.get_open_orders('OP-USDT')
    order_data_list = data.get_data()
    for d in order_data_list:
        info = live_feed.cancel_order('OP-USDT', d.init_data().get_order_id())
        print(info.get_data()[0].init_data())


def get_my_positions():
    live_binance_spot_feed = init_req_feed()
    data = live_binance_spot_feed.get_position(symbol="OP-USDT")
    print("position: ", data.get_data()[0].init_data())


def test_binance_req_get_deals():
    live_binance_spot_feed = init_req_feed()
    price_data = live_binance_spot_feed.get_deals(symbol="OP-USDT")
    trade_data = price_data.get_data()
    if trade_data:
        first_trade = trade_data[0]
        assert trade_data is not None
        assert isinstance(trade_data, list)
        assert first_trade.init_data().get_trade_price() > 0
        assert first_trade.init_data().get_trade_volume() > 0


def test_binance_async_get_deals():
    data_queue = queue.Queue()
    live_binance_spot_feed = init_async_feed(data_queue)
    live_binance_spot_feed.async_get_deals("OP-USDT")
    time.sleep(1)
    try:
        depth_data = data_queue.get(False)
    except queue.Empty:
        target_data = None
    else:
        target_data = depth_data
    if target_data.get_data():
        first_trade = target_data.get_data()[0].init_data()
        assert target_data is not None
        assert isinstance(target_data, list)
        assert first_trade.get_trade_price() > 0
        assert first_trade.get_trade_volume() > 0


if __name__ == "__main__":
    test_get_binance_key()
    # test_binance_req_tick_data()
    # test_binance_async_tick_data()
    # test_binance_req_kline_data()
    # test_binance_async_kline_data()
    # test_binance_req_depth_data()
    # test_binance_async_depth_data()
    # test_binance_req_get_balance()
    # test_binance_async_get_balance()
    # test_binance_req_account_data()
    # test_binance_async_account_data()
    test_binance_make_order_and_cancel_order()
    test_binance_req_order_functions()
    test_binance_async_order_functions()
    # test_binance_req_get_deals()
    # test_binance_async_get_deals()
    # make_a_order()
    # get_my_positions()
    test_cancel_all_orders()
    # test_binance_req_get_balance()
