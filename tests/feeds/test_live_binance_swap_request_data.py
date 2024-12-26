import queue
import time
import random

from btpy.containers import TradeData
from btpy.functions.utils import read_yaml_file
from btpy.feeds.live_binance_feed import BinanceRequestDataSwap
from btpy.containers.exchanges.binance_exchange_data import BinanceExchangeDataSwap
from btpy.containers.orderbooks.binance_orderbook import BinanceRequestOrderBookData
from btpy.containers.fundingrates.binance_funding_rate import BinanceRequestFundingRateData
from btpy.containers.balances.binance_balance import BinanceSwapRequestBalanceData
from btpy.containers.accounts.binance_account import BinanceSwapRequestAccountData
from btpy.containers.requestdatas.request_data import RequestData
from btpy.containers.positions.binance_position import BinanceRequestPositionData
from btpy.containers.orders.binance_order import BinanceRequestOrderData
from btpy.containers.markprices.binance_mark_price import BinanceRequestMarkPriceData
from btpy.containers.bars.binance_bar import BinanceRequestBarData
from btpy.containers.tickers.binance_ticker import BinanceRequestTickerData


def generate_kwargs(exchange=BinanceExchangeDataSwap):
    data = read_yaml_file("account_config.yaml")
    kwargs = {
        "public_key": data['binance']['public_key'],
        "private_key": data['binance']['private_key'],
        "exchange_data": exchange(),
        "topics": {"tick": {"symbol": "BTC-USDT"}}
    }
    return kwargs


def init_req_feed():
    data_queue = queue.Queue()
    kwargs = generate_kwargs()
    live_binance_swap_feed = BinanceRequestDataSwap(data_queue, **kwargs)
    return live_binance_swap_feed


def init_async_feed(data_queue):
    kwargs = generate_kwargs()
    live_binance_swap_feed = BinanceRequestDataSwap(data_queue, **kwargs)
    return live_binance_swap_feed


def test_binance_req_server_time():
    live_binance_swap_feed = init_req_feed()
    data = live_binance_swap_feed.get_server_time()
    assert isinstance(data, RequestData)
    current_timestamp = time.time()
    current_timestamp_utc = time.mktime(time.gmtime())
    server_time = data.get_data()['serverTime']
    # print(f"Server time: {server_time}, current timestamp: {current_timestamp}, "
    #       f"current timestamp_utc: {current_timestamp_utc}")
    assert abs(server_time / 1000 - current_timestamp) < 3, "服务器时间和本地时间超过3s"


def test_binance_req_tick_data():
    live_binance_swap_feed = init_req_feed()
    data = live_binance_swap_feed.get_tick("BTC-USDT").get_data()
    assert isinstance(data, list)
    tick_data = data[0].init_data()
    assert tick_data.get_server_time() > 1597026383085.0
    assert tick_data.get_exchange_name() == "BINANCE"
    assert tick_data.get_symbol_name() == "BTC-USDT"
    assert tick_data.get_bid_price() > 0
    assert tick_data.get_bid_volume() >= 0
    assert tick_data.get_ask_price() > 0
    assert tick_data.get_ask_volume() >= 0
    assert tick_data.get_last_price() is None
    assert tick_data.get_last_volume() is None


def test_binance_async_tick_data():
    data_queue = queue.Queue()
    live_binance_swap_feed = init_async_feed(data_queue)
    live_binance_swap_feed.async_get_tick("BTC-USDT", extra_data={"test_async_tick_data": True})
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
    live_binance_swap_feed = init_req_feed()
    data = live_binance_swap_feed.get_kline("BTC-USDT", "1m", count=2).get_data()
    assert isinstance(data, list)
    kline_data = data[0].init_data()
    assert kline_data.get_server_time() is None
    assert kline_data.get_exchange_name() == "BINANCE"
    assert kline_data.get_symbol_name() == "BTC-USDT"
    assert kline_data.get_local_update_time() > 0
    assert kline_data.get_open_price() > 0
    assert kline_data.get_high_price() >= 0
    assert kline_data.get_low_price() > 0
    assert kline_data.get_close_price() >= 0
    assert kline_data.get_volume() >= 0
    assert kline_data.get_taker_buy_base_asset_volume() >= 0
    assert kline_data.get_bar_status() is None


def test_binance_async_kline_data():
    data_queue = queue.Queue()
    live_binance_swap_feed = init_async_feed(data_queue)
    live_binance_swap_feed.async_get_kline("BTC-USDT", period="1m",
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
    async_kline_data = target_data[0].init_data()
    assert isinstance(async_kline_data, BinanceRequestBarData)
    assert async_kline_data.get_exchange_name() == "BINANCE"
    assert async_kline_data.get_symbol_name() == "BTC-USDT"
    assert async_kline_data.get_open_price() > 0
    assert async_kline_data.get_high_price() > 0
    assert async_kline_data.get_low_price() > 0
    assert async_kline_data.get_close_price() > 0
    assert async_kline_data.get_volume() >= 0


def order_book_value_equals(order_book):
    assert isinstance(order_book, BinanceRequestOrderBookData)
    assert order_book.get_server_time() > 0
    assert order_book.get_exchange_name() == "BINANCE"
    assert order_book.get_symbol_name() == "BTC-USDT"
    assert order_book.get_asset_type() == "SWAP"
    assert order_book.get_bid_price_list()[0] > 0
    assert order_book.get_bid_volume_list()[-1] >= 0
    assert order_book.get_ask_price_list()[-1] > 0
    assert order_book.get_ask_volume_list()[-1] >= 0
    assert order_book.get_bid_trade_nums() is None
    assert order_book.get_ask_trade_nums() is None
    assert len(order_book.get_bid_price_list()) == 20


def test_binance_req_depth_data():
    live_binance_swap_feed = init_req_feed()
    data = live_binance_swap_feed.get_depth("BTC-USDT", 20).get_data()
    assert isinstance(data, list)
    order_book_value_equals(data[0].init_data())


def test_binance_async_depth_data():
    data_queue = queue.Queue()
    live_binance_swap_feed = init_async_feed(data_queue)
    live_binance_swap_feed.async_get_depth("BTC-USDT", 20)
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
    assert bf.get_current_funding_time() is None
    assert bf.get_max_funding_rate() is None
    assert bf.get_min_funding_rate() is None
    assert bf.get_settlement_funding_rate() is None
    assert bf.get_settlement_status() is None
    assert bf.get_method() is None


def test_binance_req_funding_rate_data():
    live_binance_swap_feed = init_req_feed()
    data = live_binance_swap_feed.get_funding_rate("BTC-USDT").get_data()
    assert isinstance(data, list)
    assert isinstance(data[0], BinanceRequestFundingRateData)
    assert_funding_rate_value(data[0].init_data())


def test_binance_async_funding_rate_data():
    data_queue = queue.Queue()
    live_binance_swap_feed = init_async_feed(data_queue)
    live_binance_swap_feed.async_get_funding_rate("BTC-USDT")
    time.sleep(1)
    try:
        depth_data = data_queue.get(False)
    except queue.Empty:
        target_data = None
    else:
        target_data = depth_data.get_data()
        # 检测kline数据
    assert isinstance(target_data, list)
    assert isinstance(target_data[0], BinanceRequestFundingRateData)
    assert_funding_rate_value(target_data[0].init_data())


def assert_mark_price_data_value(bp):
    assert bp.get_server_time() > 0
    assert bp.get_exchange_name() == "BINANCE"
    assert bp.get_symbol_name() == "BTC-USDT"
    assert bp.get_mark_price() > 0
    assert bp.get_asset_type() == "SPOT"
    assert bp.get_event() == "MarkPriceEvent"


def test_binance_req_mark_price_data():
    live_binance_swap_feed = init_req_feed()
    data = live_binance_swap_feed.get_mark_price("BTC-USDT").get_data()
    assert isinstance(data, list)
    assert isinstance(data[0], BinanceRequestMarkPriceData)
    assert_mark_price_data_value(data[0].init_data())


def test_binance_async_mark_price_data():
    data_queue = queue.Queue()
    live_binance_swap_feed = init_async_feed(data_queue)
    live_binance_swap_feed.async_get_mark_price("BTC-USDT")
    time.sleep(1)
    try:
        depth_data = data_queue.get(False)
    except queue.Empty:
        target_data = None
    else:
        target_data = depth_data.get_data()
        # 检测kline数据
    assert isinstance(target_data, list)
    assert isinstance(target_data[0], BinanceRequestMarkPriceData)
    assert_mark_price_data_value(target_data[0].init_data())


def assert_account_data_value(bp):
    assert bp.get_server_time() >= 0.0
    assert bp.get_exchange_name() == "BINANCE"
    assert bp.get_total_margin() > 0.0
    assert bp.get_asset_type() == "SWAP"
    assert bp.get_event() == "AccountEvent"


def test_binance_req_account_data():
    live_binance_swap_feed = init_req_feed()
    data = live_binance_swap_feed.get_account().get_data()
    # print(data)
    assert isinstance(data[0], BinanceSwapRequestAccountData)
    assert_account_data_value(data[0].init_data())


def test_binance_async_account_data():
    data_queue = queue.Queue()
    live_binance_swap_feed = init_async_feed(data_queue)
    live_binance_swap_feed.async_get_account()
    time.sleep(1)
    try:
        depth_data = data_queue.get(False)
    except queue.Empty:
        target_data = None
    else:
        target_data = depth_data.get_data()
        # 检测kline数据
    assert target_data is not None
    assert isinstance(target_data[0], BinanceSwapRequestAccountData)
    assert_account_data_value(target_data[0].init_data())


def test_binance_req_get_balance():
    live_binance_swap_feed = init_req_feed()
    data = live_binance_swap_feed.get_balance()
    # print(data)
    assert isinstance(data.get_data()[0], BinanceSwapRequestBalanceData)
    assert isinstance(data, RequestData)
    # assert_account_data_value(data[0])


def test_binance_async_get_balance():
    data_queue = queue.Queue()
    live_binance_swap_feed = init_async_feed(data_queue)
    live_binance_swap_feed.async_get_balance()
    time.sleep(1)
    try:
        depth_data = data_queue.get(False)
    except queue.Empty:
        target_data = None
    else:
        target_data = depth_data.get_data()
        # 检测kline数据
    assert target_data is not None
    assert isinstance(target_data[0], BinanceSwapRequestBalanceData)


def binance_req_query_order_by_client_order_id(client_order_id):
    live_binance_swap_feed = init_req_feed()
    kwargs = {"client_order_id": client_order_id}
    data = live_binance_swap_feed.query_order("OP-USDT", **kwargs)
    print(data.get_data())
    assert data.get_status()
    assert isinstance(data, RequestData)
    return data


def binance_req_get_open_order():
    live_binance_swap_feed = init_req_feed()
    data = live_binance_swap_feed.get_open_orders()
    print(data.get_data())
    assert isinstance(data, RequestData)
    return data


def binance_req_cancel_order_by_order_id(order_id):
    live_binance_swap_feed = init_req_feed()
    data = live_binance_swap_feed.cancel_order("OP-USDT", order_id=order_id)
    assert isinstance(data, RequestData)
    return data


def binance_req_cancel_order_by_client_order_id(client_order_id):
    live_binance_swap_feed = init_req_feed()
    data = live_binance_swap_feed.cancel_order("OP-USDT", client_order_id=client_order_id)
    assert isinstance(data, RequestData)
    return data


def binance_make_order_and_cancel_order():
    live_binance_swap_feed = init_req_feed()
    price_data = live_binance_swap_feed.get_tick("OP-USDT")
    price_data = price_data.get_data()[0].init_data()
    bid_price = round(price_data.get_bid_price() * 0.9, 2)
    buy_client_order_id = "testOrder"
    lots = 0
    while lots * bid_price < 10:
        lots += 1
    # https://fapi.binance.com/fapi/v1/order?recvWindow=3000&timestamp=1708936750172&symbol=OPUSDT&side=BUY&quantity=2&price=3.5&type=LIMIT&timeInForce=GTC&positionSide=LONG&signature=72803587914b786ba57b9dccbf5b83c5dfc5c10da9d138c2dfc0bd6f105f7bcf
    buy_data = live_binance_swap_feed.make_order("OP-USDT", lots, bid_price, "buy-limit",
                                                 client_order_id=buy_client_order_id,
                                                 **{"position_side": "LONG"})
    # print("make_order info", buy_data.get_data())
    # "https://fapi.binance.com/fapi/v1/order?recvWindow=3000&timestamp=1708936667118&symbol=OPUSDT&orderId=9106714922&signature=69492a311ad22b37710e029a4e317ac5a7f43f7beadfdfba2caa20baf19a9a6b"
    # 查询订单信息
    query_data = live_binance_swap_feed.query_order(symbol="OP-USDT", client_order_id=buy_client_order_id)
    # print("query_order", query_data.get_data())
    # get_open_orders
    open_order_data = live_binance_swap_feed.get_open_orders(symbol="OP-USDT")
    # print("get_open_orders: ", open_order_data.get_data())
    cancel_order_data = live_binance_swap_feed.cancel_order("OP-USDT", order_id=None,
                                                            client_order_id=buy_client_order_id)
    # print("cancel_order info", cancel_order_data.get_data())


def test_binance_req_order_functions():
    live_binance_swap_feed = init_req_feed()
    price_data = live_binance_swap_feed.get_tick("OP-USDT")
    price_data = price_data.get_data()[0].init_data()
    bid_price = round(price_data.get_bid_price() * 0.9, 2)
    ask_price = round(price_data.get_ask_price() * 1.1, 2)
    random_number = random.randint(10 ** 17, 10 ** 18 - 1)
    buy_client_order_id = str(random_number)
    sell_client_order_id = str(random_number + 1)
    lots = 0
    while lots * bid_price < 10:
        lots += 1
    buy_data = live_binance_swap_feed.make_order("OP-USDT", lots, bid_price, "buy-limit",
                                                 client_order_id=buy_client_order_id,
                                                 **{"position_side": "LONG"})
    sell_data = live_binance_swap_feed.make_order("OP-USDT", lots, ask_price, "sell-limit",
                                                  client_order_id=sell_client_order_id,
                                                  **{"position_side": "SHORT"})
    # 测试买单和卖单
    buy_info = buy_data.get_data()[0]
    assert buy_data.get_status()
    assert isinstance(buy_data, RequestData)
    buy_order_id = buy_info.init_data().get_order_id()
    assert buy_order_id is not None

    sell_info = sell_data.get_data()[0]
    assert sell_data.get_status()
    assert isinstance(sell_data, RequestData)
    assert isinstance(sell_info, BinanceRequestOrderData)
    sell_order_id = sell_info.init_data().get_order_id()
    assert sell_order_id is not None

    # 根据order_id查询订单
    data = live_binance_swap_feed.query_order("OP-USDT", order_id=int(buy_order_id))
    print(data.get_data())
    assert data.get_status()
    assert isinstance(data, RequestData)
    assert data.get_data()[0].init_data().get_order_price() == bid_price
    assert data.get_data()[0].init_data().get_client_order_id() == buy_client_order_id

    data = live_binance_swap_feed.query_order("OP-USDT", order_id=int(sell_order_id))
    print(data.get_data())
    assert data.get_status()
    assert isinstance(data, RequestData)
    assert data.get_data()[0].init_data().get_order_price() == ask_price
    assert data.get_data()[0].init_data().get_client_order_id() == sell_client_order_id
    # 根据client_order_id查询订单
    binance_req_query_order_by_client_order_id(buy_client_order_id)
    binance_req_query_order_by_client_order_id(sell_client_order_id)
    # 查询有哪些open_order
    orders = binance_req_get_open_order()
    assert len(orders.get_data()) > 1
    # 用order_id和client_order_id进行撤单
    binance_req_cancel_order_by_order_id(int(buy_order_id))
    binance_req_cancel_order_by_client_order_id(sell_client_order_id)
    # Querying orders can only be done during single-backtrader testing.
    # When executing across multiple backtrader, the order of execution is not guaranteed,
    # which can lead to conflicts and errors.
    # orders = binance_req_get_open_order()
    # print('test_binance_req_order_functions()', orders.get_data())
    # assert orders.get_data() is None


def test_binance_async_order_functions():
    data_queue = queue.Queue()
    live_binance_swap_feed = init_async_feed(data_queue)
    price_data = live_binance_swap_feed.get_tick("OP-USDT").get_data()[0].init_data()
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
    live_binance_swap_feed.async_make_order("OP-USDT", lots, bid_price, "buy-limit", client_order_id=buy_client_order_id,
                                            **{"position_side": "LONG"})
    time.sleep(1)
    live_binance_swap_feed.async_query_order("OP-USDT", **{"client_order_id": buy_client_order_id})
    live_binance_swap_feed.async_get_open_orders()
    live_binance_swap_feed.async_cancel_order("OP-USDT", **{"client_order_id": buy_client_order_id})
    time.sleep(1)
    # live_binance_swap_feed.async_get_open_orders("OP-USDT")
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
#     live_binance_swap_feed = init_req_feed()
#     random_number = random.randint(10 ** 17, 10 ** 18 - 1)
#     sell_client_order_id = str(random_number + 1)
#     ask_price = 3.79
#     sell_data = live_binance_swap_feed.make_order("OP-USDT", 2, ask_price, "sell-limit",
#                                                   client_order_id=sell_client_order_id, **{"position_side": "SHORT"})
#     print(sell_data.get_data())


def get_my_positions():
    live_binance_swap_feed = init_req_feed()
    data = live_binance_swap_feed.get_position(symbol="OP-USDT")
    print("position: ", data.get_data())


def test_binance_req_get_deals():
    live_binance_swap_feed = init_req_feed()
    request_data = live_binance_swap_feed.get_deals(symbol="MOVRUSDT")
    request_data.init_data()
    trade_data = request_data.get_data()
    assert isinstance(trade_data, list)
    if len(trade_data) > 0:
        first_trade = trade_data[0].init_data()
        assert trade_data is not None
        assert isinstance(trade_data, list)
        assert first_trade.get_trade_price() > 0
        assert first_trade.get_trade_volume() > 0


def test_binance_async_get_deals():
    data_queue = queue.Queue()
    live_binance_swap_feed = init_async_feed(data_queue)
    live_binance_swap_feed.async_get_deals()
    time.sleep(1)
    try:
        request_data = data_queue.get(False)
    except queue.Empty:
        trade_data = None
    else:
        request_data.init_data()
        trade_data = request_data.get_data()
    assert trade_data is not None
    assert isinstance(trade_data, list)
    if len(trade_data) > 0:
        first_trade = trade_data[0].init_data()
        assert first_trade.get_trade_price() > 0
        assert first_trade.get_trade_volume() > 0


def test_binance_req_get_position():
    live_binance_swap_feed = init_req_feed()
    data = live_binance_swap_feed.get_position(symbol="OP-USDT")
    assert isinstance(data, RequestData)
    assert isinstance(data.get_data()[0], BinanceRequestPositionData)


def test_binance_async_get_position():
    data_queue = queue.Queue()
    live_binance_swap_feed = init_async_feed(data_queue)
    live_binance_swap_feed.async_get_position(symbol="OP-USDT")
    time.sleep(1)
    try:
        position_data = data_queue.get(False)
        # print("position_data", position_data)
    except queue.Empty:
        position_data = None
    target_data = position_data.get_data()
    # print('target_data', target_data)
    assert isinstance(position_data, RequestData)
    assert isinstance(target_data[0], BinanceRequestPositionData)


def test_cancel_all_orders():
    print("Cancelling all orders")
    live_feed = init_req_feed()
    data = live_feed.get_open_orders('OP-USDT')
    order_data_list = data.get_data()
    print('order_data_list', order_data_list)
    for d in order_data_list:
        info = live_feed.cancel_order('OP-USDT', d.init_data().get_order_id())
        print(info.get_data())


if __name__ == "__main__":
    test_binance_req_server_time()
    # test_binance_req_tick_data()
    # test_binance_async_tick_data()
    # test_binance_req_kline_data()
    # test_binance_async_kline_data()
    # test_binance_req_depth_data()
    # test_binance_async_depth_data()
    test_binance_req_funding_rate_data()
    test_binance_async_funding_rate_data()
    # test_binance_req_mark_price_data()
    # test_binance_async_mark_price_data()
    # test_binance_req_get_balance()
    # test_binance_async_get_balance()
    # test_binance_req_get_position()
    # test_binance_async_get_position()
    # test_binance_req_account_data()
    # test_binance_async_account_data()
    # binance_make_order_and_cancel_order()
    # test_binance_req_order_functions()
    # test_binance_async_order_functions()
    # test_binance_req_get_deals()
    # test_binance_async_get_deals()
    # make_a_order()
    # get_my_positions()
    test_cancel_all_orders()
