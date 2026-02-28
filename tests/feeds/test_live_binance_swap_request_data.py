import queue
import time
import random
import pytest

from bt_api_py.containers import TradeData
from bt_api_py.functions.utils import read_account_config
from bt_api_py.feeds.live_binance_feed import BinanceRequestDataSwap
from bt_api_py.containers.exchanges.binance_exchange_data import BinanceExchangeDataSwap
from bt_api_py.containers.orderbooks.binance_orderbook import BinanceRequestOrderBookData
from bt_api_py.containers.fundingrates.binance_funding_rate import BinanceRequestFundingRateData, \
    BinanceRequestHistoryFundingRateData
from bt_api_py.containers.balances.binance_balance import BinanceSwapRequestBalanceData
from bt_api_py.containers.accounts.binance_account import BinanceSwapRequestAccountData
from bt_api_py.containers.requestdatas.request_data import RequestData
from bt_api_py.containers.positions.binance_position import BinanceRequestPositionData
from bt_api_py.containers.orders.binance_order import BinanceRequestOrderData
from bt_api_py.containers.markprices.binance_mark_price import BinanceRequestMarkPriceData
from bt_api_py.containers.bars.binance_bar import BinanceRequestBarData
from bt_api_py.containers.tickers.binance_ticker import BinanceRequestTickerData


def generate_kwargs(exchange=BinanceExchangeDataSwap):
    data = read_account_config()
    kwargs = {
        "public_key": data['binance']['public_key'],
        "private_key": data['binance']['private_key'],
        "topics": {"tick": {"symbol": "BTC-USDT"}},
        "proxies": data.get('proxies'),
        "async_proxy": data.get('async_proxy'),
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
    time.sleep(3)
    try:
        tick_data = data_queue.get(timeout=10)
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
    assert kline_data.get_server_time() > 0
    assert kline_data.get_exchange_name() == "BINANCE"
    assert kline_data.get_symbol_name() == "BTC-USDT"
    assert kline_data.get_local_update_time() > 0
    assert kline_data.get_open_price() > 0
    assert kline_data.get_high_price() >= 0
    assert kline_data.get_low_price() > 0
    assert kline_data.get_close_price() >= 0
    assert kline_data.get_volume() >= 0
    assert kline_data.get_taker_buy_base_asset_volume() >= 0
    assert kline_data.get_bar_status() is True


def test_binance_async_kline_data():
    data_queue = queue.Queue()
    live_binance_swap_feed = init_async_feed(data_queue)
    live_binance_swap_feed.async_get_kline("BTC-USDT", period="1m",
                                           extra_data={"test_async_kline_data": True})

    time.sleep(3)
    try:
        kline_data = data_queue.get(timeout=10)
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
    time.sleep(3)
    try:
        depth_data = data_queue.get(timeout=10)
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

def test_binance_req_history_funding_rate_data():
    live_binance_swap_feed = init_req_feed()
    data_list = live_binance_swap_feed.get_history_funding_rate("BTC-USDT", "2024-09-30 00:00:00.000", "2024-12-31 00:00:00.000", 1000).get_data()
    # print(data_list)
    assert isinstance(data_list, list)
    print(type(data_list[0]))
    assert isinstance(data_list[0], BinanceRequestHistoryFundingRateData)
    funding_rate_data = data_list[0].init_data()
    assert funding_rate_data.get_current_funding_rate() is not None
    assert funding_rate_data.get_symbol_name() == "BTC-USDT"
    assert funding_rate_data.get_current_funding_time() > 0


def test_binance_async_funding_rate_data():
    data_queue = queue.Queue()
    live_binance_swap_feed = init_async_feed(data_queue)
    live_binance_swap_feed.async_get_funding_rate("BTC-USDT")
    time.sleep(3)
    try:
        depth_data = data_queue.get(timeout=10)
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
    time.sleep(3)
    try:
        depth_data = data_queue.get(timeout=10)
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
    assert bp.get_total_margin() >= 0.0
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
    time.sleep(5)
    try:
        depth_data = data_queue.get(timeout=10)
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
    time.sleep(3)
    try:
        depth_data = data_queue.get(timeout=10)
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
    time.sleep(5)
    live_binance_swap_feed.async_query_order("OP-USDT", **{"client_order_id": buy_client_order_id})
    live_binance_swap_feed.async_get_open_orders()
    live_binance_swap_feed.async_cancel_order("OP-USDT", **{"client_order_id": buy_client_order_id})
    time.sleep(5)
    # live_binance_swap_feed.async_get_open_orders("OP-USDT")
    while True:
        try:
            target_data = data_queue.get(timeout=1)
        except queue.Empty:
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
    time.sleep(5)
    try:
        request_data = data_queue.get(timeout=10)
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
    time.sleep(5)
    try:
        position_data = data_queue.get(timeout=10)
        # print("position_data", position_data)
    except queue.Empty:
        position_data = None
    assert position_data is not None, "async_get_position returned no data in time"
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


# ===== Tests for new market data endpoints =====

def test_binance_req_agg_trades():
    live_binance_swap_feed = init_req_feed()
    data = live_binance_swap_feed.get_agg_trades("BTC-USDT", count=5)
    assert isinstance(data, RequestData)
    result = data.get_data()
    assert isinstance(result, list)
    assert len(result) == 5
    first = result[0]
    assert 'a' in first  # agg trade id
    assert 'p' in first  # price
    assert 'q' in first  # quantity
    assert 'T' in first  # timestamp


def test_binance_req_open_interest():
    live_binance_swap_feed = init_req_feed()
    data = live_binance_swap_feed.get_open_interest("BTC-USDT")
    assert isinstance(data, RequestData)
    result = data.get_data()
    assert isinstance(result, dict)
    assert 'openInterest' in result
    assert 'symbol' in result
    assert float(result['openInterest']) > 0


def test_binance_req_continuous_kline():
    live_binance_swap_feed = init_req_feed()
    data = live_binance_swap_feed.get_continuous_kline("BTC-USDT", "1h", count=3)
    assert isinstance(data, RequestData)
    result = data.get_data()
    assert isinstance(result, list)
    assert len(result) == 3
    kline = result[0]
    if isinstance(kline, BinanceRequestBarData):
        kline = kline.init_data()
        assert kline.get_open_price() > 0
    else:
        assert isinstance(kline, list)
        assert len(kline) >= 6


def test_binance_req_index_price_kline():
    live_binance_swap_feed = init_req_feed()
    data = live_binance_swap_feed.get_index_price_kline("BTC-USDT", "1h", count=3)
    assert isinstance(data, RequestData)
    result = data.get_data()
    assert isinstance(result, list)
    assert len(result) == 3


def test_binance_req_mark_price_kline():
    live_binance_swap_feed = init_req_feed()
    data = live_binance_swap_feed.get_mark_price_kline("BTC-USDT", "1h", count=3)
    assert isinstance(data, RequestData)
    result = data.get_data()
    assert isinstance(result, list)
    assert len(result) == 3


def test_binance_req_funding_info():
    live_binance_swap_feed = init_req_feed()
    data = live_binance_swap_feed.get_funding_info()
    assert isinstance(data, RequestData)
    result = data.get_data()
    assert isinstance(result, list)
    assert len(result) > 0
    first = result[0]
    assert 'symbol' in first
    assert 'fundingIntervalHours' in first


def test_binance_req_long_short_ratio():
    live_binance_swap_feed = init_req_feed()
    data = live_binance_swap_feed.get_long_short_ratio("BTC-USDT", period="1h", count=5)
    assert isinstance(data, RequestData)
    result = data.get_data()
    assert isinstance(result, list)
    assert len(result) > 0
    first = result[0]
    assert 'symbol' in first
    assert 'longShortRatio' in first
    assert 'longAccount' in first
    assert 'shortAccount' in first


def test_binance_req_taker_buy_sell_volume():
    live_binance_swap_feed = init_req_feed()
    data = live_binance_swap_feed.get_taker_buy_sell_volume("BTC-USDT", period="1h", count=5)
    assert isinstance(data, RequestData)
    result = data.get_data()
    assert isinstance(result, list)
    assert len(result) > 0
    first = result[0]
    assert 'buySellRatio' in first
    assert 'buyVol' in first
    assert 'sellVol' in first


# ===== Tests for new trade endpoints =====

def test_binance_req_get_all_orders():
    live_binance_swap_feed = init_req_feed()
    data = live_binance_swap_feed.get_all_orders("BTC-USDT", count=5)
    assert isinstance(data, RequestData)
    result = data.get_data()
    assert isinstance(result, list)


# ===== Tests for new account endpoints =====

def test_binance_req_get_leverage_bracket():
    live_binance_swap_feed = init_req_feed()
    data = live_binance_swap_feed.get_leverage_bracket("BTC-USDT")
    assert isinstance(data, RequestData)
    result = data.get_data()
    assert isinstance(result, list)
    assert len(result) > 0
    first = result[0]
    assert 'symbol' in first
    assert 'brackets' in first
    assert len(first['brackets']) > 0


def test_binance_req_get_position_mode():
    live_binance_swap_feed = init_req_feed()
    data = live_binance_swap_feed.get_position_mode()
    assert isinstance(data, RequestData)
    result = data.get_data()
    assert isinstance(result, dict)
    assert 'dualSidePosition' in result


def test_binance_req_get_income():
    live_binance_swap_feed = init_req_feed()
    data = live_binance_swap_feed.get_income(count=5)
    assert isinstance(data, RequestData)
    result = data.get_data()
    assert isinstance(result, list)


def test_binance_req_get_fee():
    live_binance_swap_feed = init_req_feed()
    data = live_binance_swap_feed.get_fee("BTC-USDT")
    assert isinstance(data, RequestData)
    result = data.get_data()
    assert isinstance(result, dict)
    assert 'symbol' in result
    assert 'makerCommissionRate' in result
    assert 'takerCommissionRate' in result


# ===== Async tests for new market data endpoints =====

def test_binance_async_agg_trades():
    data_queue = queue.Queue()
    live_binance_swap_feed = init_async_feed(data_queue)
    live_binance_swap_feed.async_get_agg_trades("BTC-USDT", count=5)
    try:
        request_data = data_queue.get(timeout=30)
    except queue.Empty:
        request_data = None
    assert request_data is not None, "async_get_agg_trades returned no data"
    assert isinstance(request_data, RequestData)
    result = request_data.get_data()
    assert isinstance(result, list)
    assert len(result) == 5


def test_binance_async_open_interest():
    data_queue = queue.Queue()
    live_binance_swap_feed = init_async_feed(data_queue)
    live_binance_swap_feed.async_get_open_interest("BTC-USDT")
    time.sleep(3)
    try:
        request_data = data_queue.get(timeout=10)
    except queue.Empty:
        request_data = None
    assert request_data is not None, "async_get_open_interest returned no data"
    assert isinstance(request_data, RequestData)
    result = request_data.get_data()
    assert isinstance(result, dict)
    assert 'openInterest' in result
    assert float(result['openInterest']) > 0


def test_binance_async_continuous_kline():
    data_queue = queue.Queue()
    live_binance_swap_feed = init_async_feed(data_queue)
    live_binance_swap_feed.async_get_continuous_kline("BTC-USDT", "1h", count=3)
    time.sleep(3)
    try:
        request_data = data_queue.get(timeout=10)
    except queue.Empty:
        request_data = None
    assert request_data is not None, "async_get_continuous_kline returned no data"
    assert isinstance(request_data, RequestData)
    result = request_data.get_data()
    assert isinstance(result, list)
    assert len(result) == 3


def test_binance_async_index_price_kline():
    data_queue = queue.Queue()
    live_binance_swap_feed = init_async_feed(data_queue)
    live_binance_swap_feed.async_get_index_price_kline("BTC-USDT", "1h", count=3)
    time.sleep(3)
    try:
        request_data = data_queue.get(timeout=10)
    except queue.Empty:
        request_data = None
    assert request_data is not None, "async_get_index_price_kline returned no data"
    assert isinstance(request_data, RequestData)
    result = request_data.get_data()
    assert isinstance(result, list)
    assert len(result) == 3


def test_binance_async_mark_price_kline():
    data_queue = queue.Queue()
    live_binance_swap_feed = init_async_feed(data_queue)
    live_binance_swap_feed.async_get_mark_price_kline("BTC-USDT", "1h", count=3)
    try:
        request_data = data_queue.get(timeout=30)
    except queue.Empty:
        request_data = None
    assert request_data is not None, "async_get_mark_price_kline returned no data"
    assert isinstance(request_data, RequestData)
    result = request_data.get_data()
    assert isinstance(result, list)
    assert len(result) == 3


def test_binance_async_funding_info():
    data_queue = queue.Queue()
    live_binance_swap_feed = init_async_feed(data_queue)
    live_binance_swap_feed.async_get_funding_info()
    time.sleep(3)
    try:
        request_data = data_queue.get(timeout=10)
    except queue.Empty:
        request_data = None
    assert request_data is not None, "async_get_funding_info returned no data"
    assert isinstance(request_data, RequestData)
    result = request_data.get_data()
    assert isinstance(result, list)
    assert len(result) > 0


def test_binance_async_long_short_ratio():
    data_queue = queue.Queue()
    live_binance_swap_feed = init_async_feed(data_queue)
    live_binance_swap_feed.async_get_long_short_ratio("BTC-USDT", period="1h", count=5)
    time.sleep(3)
    try:
        request_data = data_queue.get(timeout=10)
    except queue.Empty:
        request_data = None
    assert request_data is not None, "async_get_long_short_ratio returned no data"
    assert isinstance(request_data, RequestData)
    result = request_data.get_data()
    assert isinstance(result, list)
    assert len(result) > 0


def test_binance_async_taker_buy_sell_volume():
    data_queue = queue.Queue()
    live_binance_swap_feed = init_async_feed(data_queue)
    live_binance_swap_feed.async_get_taker_buy_sell_volume("BTC-USDT", period="1h", count=5)
    time.sleep(3)
    try:
        request_data = data_queue.get(timeout=10)
    except queue.Empty:
        request_data = None
    assert request_data is not None, "async_get_taker_buy_sell_volume returned no data"
    assert isinstance(request_data, RequestData)
    result = request_data.get_data()
    assert isinstance(result, list)
    assert len(result) > 0


# ===== Async tests for new account endpoints =====

def test_binance_async_get_leverage_bracket():
    data_queue = queue.Queue()
    live_binance_swap_feed = init_async_feed(data_queue)
    live_binance_swap_feed.async_get_leverage_bracket("BTC-USDT")
    time.sleep(3)
    try:
        request_data = data_queue.get(timeout=10)
    except queue.Empty:
        request_data = None
    assert request_data is not None, "async_get_leverage_bracket returned no data"
    assert isinstance(request_data, RequestData)
    result = request_data.get_data()
    assert isinstance(result, list)
    assert len(result) > 0


def test_binance_async_get_position_mode():
    data_queue = queue.Queue()
    live_binance_swap_feed = init_async_feed(data_queue)
    live_binance_swap_feed.async_get_position_mode()
    time.sleep(3)
    try:
        request_data = data_queue.get(timeout=10)
    except queue.Empty:
        request_data = None
    assert request_data is not None, "async_get_position_mode returned no data"
    assert isinstance(request_data, RequestData)
    result = request_data.get_data()
    assert isinstance(result, dict)
    assert 'dualSidePosition' in result


def test_binance_async_get_income():
    data_queue = queue.Queue()
    live_binance_swap_feed = init_async_feed(data_queue)
    live_binance_swap_feed.async_get_income(count=5)
    time.sleep(3)
    try:
        request_data = data_queue.get(timeout=10)
    except queue.Empty:
        request_data = None
    assert request_data is not None, "async_get_income returned no data"
    assert isinstance(request_data, RequestData)
    result = request_data.get_data()
    assert isinstance(result, list)


def test_binance_async_get_fee():
    data_queue = queue.Queue()
    live_binance_swap_feed = init_async_feed(data_queue)
    live_binance_swap_feed.async_get_fee("BTC-USDT")
    try:
        request_data = data_queue.get(timeout=30)
    except queue.Empty:
        request_data = None
    assert request_data is not None, "async_get_fee returned no data"
    assert isinstance(request_data, RequestData)
    result = request_data.get_data()
    assert isinstance(result, dict)
    assert 'makerCommissionRate' in result


# ===== Trade mutation tests (modify_order, cancel_orders, change_leverage, change_margin_type) =====

def test_binance_req_modify_order_flow():
    live_binance_swap_feed = init_req_feed()
    price_data = live_binance_swap_feed.get_tick("OP-USDT").get_data()[0].init_data()
    bid_price = round(price_data.get_bid_price() * 0.9, 2)
    random_number = random.randint(10 ** 17, 10 ** 18 - 1)
    client_order_id = str(random_number)
    lots = 0
    while lots * bid_price < 10:
        lots += 1
    buy_data = live_binance_swap_feed.make_order("OP-USDT", lots, bid_price, "buy-limit",
                                                  client_order_id=client_order_id,
                                                  **{"position_side": "LONG"})
    assert buy_data.get_status()
    order_id = buy_data.get_data()[0].init_data().get_order_id()
    assert order_id is not None

    new_price = round(bid_price * 0.95, 2)
    modify_result = live_binance_swap_feed.modify_order(
        "OP-USDT", order_id=int(order_id), side="BUY",
        quantity=lots, price=new_price)
    assert isinstance(modify_result, RequestData)
    print("modify_order result:", modify_result.get_data())

    cancel_result = live_binance_swap_feed.cancel_order("OP-USDT", order_id=int(order_id))
    assert isinstance(cancel_result, RequestData)


def test_binance_req_cancel_orders_batch():
    live_binance_swap_feed = init_req_feed()
    price_data = live_binance_swap_feed.get_tick("OP-USDT").get_data()[0].init_data()
    bid_price = round(price_data.get_bid_price() * 0.9, 2)
    random_number = random.randint(10 ** 17, 10 ** 18 - 1)
    lots = 0
    while lots * bid_price < 10:
        lots += 1
    order_ids = []
    for i in range(2):
        cid = str(random_number + i)
        result = live_binance_swap_feed.make_order("OP-USDT", lots, bid_price, "buy-limit",
                                                    client_order_id=cid,
                                                    **{"position_side": "LONG"})
        assert result.get_status()
        oid = result.get_data()[0].init_data().get_order_id()
        order_ids.append(int(oid))

    cancel_data = live_binance_swap_feed.cancel_orders("OP-USDT", order_id_list=order_ids)
    assert isinstance(cancel_data, RequestData)
    print("cancel_orders batch result:", cancel_data.get_data())


def test_binance_req_cancel_all_orders_new():
    live_binance_swap_feed = init_req_feed()
    price_data = live_binance_swap_feed.get_tick("OP-USDT").get_data()[0].init_data()
    bid_price = round(price_data.get_bid_price() * 0.9, 2)
    random_number = random.randint(10 ** 17, 10 ** 18 - 1)
    lots = 0
    while lots * bid_price < 10:
        lots += 1
    live_binance_swap_feed.make_order("OP-USDT", lots, bid_price, "buy-limit",
                                      client_order_id=str(random_number),
                                      **{"position_side": "LONG"})
    cancel_data = live_binance_swap_feed.cancel_all_orders("OP-USDT")
    assert isinstance(cancel_data, RequestData)
    print("cancel_all_orders result:", cancel_data.get_data())


def test_binance_req_change_leverage():
    live_binance_swap_feed = init_req_feed()
    data = live_binance_swap_feed.change_leverage("BTC-USDT", leverage=20)
    assert isinstance(data, RequestData)
    result = data.get_data()
    assert isinstance(result, dict)
    assert 'leverage' in result
    assert result['leverage'] == 20
    data2 = live_binance_swap_feed.change_leverage("BTC-USDT", leverage=10)
    assert data2.get_data()['leverage'] == 10


def test_binance_req_change_margin_type():
    live_binance_swap_feed = init_req_feed()
    data = live_binance_swap_feed.change_margin_type("BTC-USDT", margin_type="ISOLATED")
    assert isinstance(data, RequestData)
    result = data.get_data()
    print("change_margin_type result:", result)
    data2 = live_binance_swap_feed.change_margin_type("BTC-USDT", margin_type="CROSSED")
    assert isinstance(data2, RequestData)
    print("change_margin_type back to CROSSED:", data2.get_data())


# ===== Additional coverage: agg_trades with time range, open_interest_hist, price_ticker =====

def test_binance_req_agg_trades_with_time():
    live_binance_swap_feed = init_req_feed()
    server_time_data = live_binance_swap_feed.get_server_time().get_data()
    end_time = server_time_data['serverTime']
    start_time = end_time - 60000  # last 1 minute
    data = live_binance_swap_feed.get_agg_trades("BTC-USDT", start_time=start_time,
                                                  end_time=end_time, count=10)
    assert isinstance(data, RequestData)
    result = data.get_data()
    assert isinstance(result, list)
    if len(result) > 0:
        assert 'a' in result[0]
        assert 'p' in result[0]


def test_binance_req_long_short_ratio_with_time():
    live_binance_swap_feed = init_req_feed()
    data = live_binance_swap_feed.get_long_short_ratio(
        "BTC-USDT", period="5m", count=10)
    assert isinstance(data, RequestData)
    result = data.get_data()
    assert isinstance(result, list)
    assert len(result) > 0
    for item in result:
        assert 'longShortRatio' in item
        assert float(item['longShortRatio']) > 0


def test_binance_req_income_with_type():
    live_binance_swap_feed = init_req_feed()
    data = live_binance_swap_feed.get_income(income_type="TRANSFER", count=5)
    assert isinstance(data, RequestData)
    result = data.get_data()
    assert isinstance(result, list)


def test_binance_req_leverage_bracket_all():
    live_binance_swap_feed = init_req_feed()
    data = live_binance_swap_feed.get_leverage_bracket()
    assert isinstance(data, RequestData)
    result = data.get_data()
    assert isinstance(result, list)
    assert len(result) > 10, "should return brackets for many symbols"


def test_binance_req_get_all_orders_with_time():
    live_binance_swap_feed = init_req_feed()
    server_time_data = live_binance_swap_feed.get_server_time().get_data()
    end_time = server_time_data['serverTime']
    start_time = end_time - 86400000  # last 24 hours
    data = live_binance_swap_feed.get_all_orders("BTC-USDT", start_time=start_time,
                                                  end_time=end_time, count=10)
    assert isinstance(data, RequestData)
    result = data.get_data()
    assert isinstance(result, list)


# ===== Tests for new futures advanced data endpoints =====

def test_binance_has_top_long_short_methods():
    """测试有大户多空比方法"""
    live_binance_swap_feed = init_req_feed()
    assert hasattr(live_binance_swap_feed, 'get_top_long_short_account_ratio')
    assert hasattr(live_binance_swap_feed, 'get_top_long_short_position_ratio')


def test_binance_req_top_long_short_account_ratio():
    """测试获取大户多空比 (账户)"""
    live_binance_swap_feed = init_req_feed()
    data = live_binance_swap_feed.get_top_long_short_account_ratio("BTC-USDT", period="1h", count=5)
    assert isinstance(data, RequestData)
    result = data.get_data()
    assert isinstance(result, list)
    assert len(result) > 0
    first = result[0]
    assert 'symbol' in first
    assert 'longPosition' in first or 'longAccount' in first
    assert 'shortPosition' in first or 'shortAccount' in first


def test_binance_req_top_long_short_position_ratio():
    """测试获取大户多空比 (持仓)"""
    live_binance_swap_feed = init_req_feed()
    data = live_binance_swap_feed.get_top_long_short_position_ratio("BTC-USDT", period="1h", count=5)
    assert isinstance(data, RequestData)
    result = data.get_data()
    assert isinstance(result, list)
    assert len(result) > 0
    first = result[0]
    assert 'symbol' in first


def test_binance_has_liquidation_methods():
    """测试有强平订单方法"""
    live_binance_swap_feed = init_req_feed()
    # NOTE: get_liquidation_orders endpoint discontinued by Binance
    assert hasattr(live_binance_swap_feed, 'get_force_orders')

# NOTE: get_liquidation_orders tests removed - endpoint discontinued by Binance
# Use WebSocket stream !forceOrder@arr for liquidation data instead

def test_binance_req_force_orders():
    """测试获取用户强平订单"""
    live_binance_swap_feed = init_req_feed()
    data = live_binance_swap_feed.get_force_orders(limit=10)
    assert isinstance(data, RequestData)
    result = data.get_data()
    assert isinstance(result, list)


def test_binance_has_open_interest_hist():
    """测试有持仓量历史方法"""
    live_binance_swap_feed = init_req_feed()
    assert hasattr(live_binance_swap_feed, 'get_open_interest_hist')


def test_binance_req_open_interest_hist():
    """测试获取持仓量历史"""
    live_binance_swap_feed = init_req_feed()
    data = live_binance_swap_feed.get_open_interest_hist("BTC-USDT", period="1h", count=5)
    assert isinstance(data, RequestData)
    result = data.get_data()
    assert isinstance(result, list)
    assert len(result) > 0
    first = result[0]
    assert 'symbol' in first
    assert 'sumOpenInterest' in first or 'openInterest' in first
    assert 'timestamp' in first


def test_binance_req_top_long_short_params():
    """测试大户多空比参数构建"""
    live_binance_swap_feed = init_req_feed()
    path, params, extra_data = live_binance_swap_feed._get_top_long_short_account_ratio(
        "BTC-USDT", period="5m", count=30
    )
    assert path == "GET /futures/data/topLongShortAccountRatio"
    assert params['symbol'] == "BTCUSDT"
    assert params['period'] == "5m"
    assert params['limit'] == 30


def test_binance_req_force_orders_params():
    """测试用户强平订单参数构建"""
    live_binance_swap_feed = init_req_feed()
    path, params, extra_data = live_binance_swap_feed._get_force_orders(
        symbol="ETH-USDT", limit=20
    )
    assert path == "GET /fapi/v1/forceOrders"
    assert params['symbol'] == "ETHUSDT"
    assert params['limit'] == 20


def test_binance_req_open_interest_hist_params():
    """测试持仓量历史参数构建"""
    live_binance_swap_feed = init_req_feed()
    path, params, extra_data = live_binance_swap_feed._get_open_interest_hist(
        "BTC-USDT", period="1h", count=100
    )
    assert path == "GET /futures/data/openInterestHist"
    assert params['symbol'] == "BTCUSDT"
    assert params['period'] == "1h"
    assert params['limit'] == 100


# NOTE: get_open_interest_interval tests removed - endpoint not available
# Use get_open_interest_hist for historical open interest data instead


if __name__ == "__main__":
    test_binance_req_server_time()
    # test_binance_req_tick_data()
    # test_binance_async_tick_data()
    # test_binance_req_kline_data()
    # test_binance_async_kline_data()
    # test_binance_req_depth_data()
    # test_binance_async_depth_data()
    # test_binance_req_funding_rate_data()
    # test_binance_async_funding_rate_data()
    test_binance_req_history_funding_rate_data()
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
    # --- New market data endpoint tests (sync) ---
    # test_binance_req_agg_trades()
    # test_binance_req_open_interest()
    # test_binance_req_continuous_kline()
    # test_binance_req_index_price_kline()
    # test_binance_req_mark_price_kline()
    # test_binance_req_funding_info()
    # test_binance_req_long_short_ratio()
    # test_binance_req_taker_buy_sell_volume()
    # --- New market data endpoint tests (async) ---
    # test_binance_async_agg_trades()
    # test_binance_async_open_interest()
    # test_binance_async_continuous_kline()
    # test_binance_async_index_price_kline()
    # test_binance_async_mark_price_kline()
    # test_binance_async_funding_info()
    # test_binance_async_long_short_ratio()
    # test_binance_async_taker_buy_sell_volume()
    # --- New account endpoint tests (sync) ---
    # test_binance_req_get_all_orders()
    # test_binance_req_get_leverage_bracket()
    # test_binance_req_get_position_mode()
    # test_binance_req_get_income()
    # test_binance_req_get_fee()
    # --- New account endpoint tests (async) ---
    # test_binance_async_get_leverage_bracket()
    # test_binance_async_get_position_mode()
    # test_binance_async_get_income()
    # test_binance_async_get_fee()
    # --- Trade mutation tests ---
    # test_binance_req_modify_order_flow()
    # test_binance_req_cancel_orders_batch()
    # test_binance_req_cancel_all_orders_new()
    # test_binance_req_change_leverage()
    # test_binance_req_change_margin_type()
    # --- Additional coverage with parameters ---
    # test_binance_req_agg_trades_with_time()
    # test_binance_req_long_short_ratio_with_time()
    # test_binance_req_income_with_type()
    # test_binance_req_leverage_bracket_all()
    # test_binance_req_get_all_orders_with_time()
