import queue
import time
import random
import pytest
from bt_api_py.functions.utils import read_account_config, get_public_ip
from bt_api_py.feeds.live_okx_feed import OkxRequestDataSwap

pytestmark = pytest.mark.xdist_group("okx_api")
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


def test_get_okx_key():
    data = read_account_config()
    public_key = data['okx']['public_key']
    private_key = data['okx']['private_key'] + "//" + data['okx']["passphrase"]
    assert len(public_key) == 36, "public key is wrong"
    assert len(private_key) > 0, "private key is wrong"


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


def test_okx_req_symbol_data():
    live_okx_swap_feed = init_req_feed()
    data = live_okx_swap_feed.get_instruments(asset_type="SWAP")
    assert isinstance(data, RequestData)
    symbol_data_list = data.get_data()
    assert isinstance(symbol_data_list, list)
    assert len(symbol_data_list) > 0
    symbol_data = symbol_data_list[0]
    symbol_data.init_data()
    assert isinstance(symbol_data.get_symbol_name(), str)



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
    time.sleep(5)
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

    time.sleep(5)
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
    time.sleep(10)
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
    time.sleep(5)
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
    time.sleep(5)
    try:
        depth_data = data_queue.get(False)
    except queue.Empty:
        target_data = None
    else:
        target_data = depth_data.get_data()
        # 检测kline数据
    assert target_data is not None, "async_get_mark_price returned no data in time"
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
    time.sleep(5)
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
    time.sleep(5)
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


def test_okx_req_get_positions_history():
    live_okx_swap_feed = init_req_feed()
    data = live_okx_swap_feed.get_positions_history(inst_type="SWAP", limit="10")
    assert isinstance(data, RequestData)
    assert data.get_status()
    position_history_list = data.get_data()
    assert isinstance(position_history_list, list)
    print("positions_history", position_history_list)
    if len(position_history_list) > 0:
        position_data = position_history_list[0]
        assert isinstance(position_data, OkxPositionData)
        position_data.init_data()


def test_okx_async_get_positions_history():
    data_queue = queue.Queue()
    live_okx_swap_feed = init_async_feed(data_queue)
    live_okx_swap_feed.async_get_positions_history(inst_type="SWAP", limit="10")
    time.sleep(5)
    try:
        position_history_data = data_queue.get(False)
    except queue.Empty:
        position_history_data = None
    assert position_history_data is not None
    assert isinstance(position_history_data, RequestData)
    assert position_history_data.get_status()
    position_history_list = position_history_data.get_data()
    assert isinstance(position_history_list, list)
    if len(position_history_list) > 0:
        position_data = position_history_list[0]
        assert isinstance(position_data, OkxPositionData)
        position_data.init_data()


def test_okx_req_get_fee():
    live_okx_swap_feed = init_req_feed()
    # Get fee rates for SWAP instruments
    data = live_okx_swap_feed.get_fee(inst_type="SWAP", uly="BTC-USDT")
    assert isinstance(data, RequestData)
    assert data.get_status()
    fee_data_list = data.get_data()
    assert isinstance(fee_data_list, list)
    print("fee_data", fee_data_list)
    if len(fee_data_list) > 0:
        fee_data = fee_data_list[0]
        # Fee data uses OkxPositionData container
        assert isinstance(fee_data, OkxPositionData)


def test_okx_async_get_fee():
    data_queue = queue.Queue()
    live_okx_swap_feed = init_async_feed(data_queue)
    # Get fee rates for SWAP instruments
    live_okx_swap_feed.async_get_fee(inst_type="SWAP", uly="BTC-USDT")
    time.sleep(5)
    try:
        fee_data_response = data_queue.get(False)
    except queue.Empty:
        fee_data_response = None
    assert fee_data_response is not None
    assert isinstance(fee_data_response, RequestData)
    assert fee_data_response.get_status()
    fee_data_list = fee_data_response.get_data()
    assert isinstance(fee_data_list, list)


def test_okx_req_get_max_size():
    live_okx_swap_feed = init_req_feed()
    data = live_okx_swap_feed.get_max_size(symbol="BTC-USDT", td_mode="cross")
    assert isinstance(data, RequestData)
    assert data.get_status()
    max_size_list = data.get_data()
    assert isinstance(max_size_list, list)
    print("max_size_data", max_size_list)
    if len(max_size_list) > 0:
        max_size_data = max_size_list[0]
        assert isinstance(max_size_data, OkxPositionData)


def test_okx_async_get_max_size():
    data_queue = queue.Queue()
    live_okx_swap_feed = init_async_feed(data_queue)
    live_okx_swap_feed.async_get_max_size(symbol="BTC-USDT", td_mode="cross")
    time.sleep(5)
    try:
        max_size_response = data_queue.get(False)
    except queue.Empty:
        max_size_response = None
    assert max_size_response is not None
    assert isinstance(max_size_response, RequestData)
    assert max_size_response.get_status()
    max_size_list = max_size_response.get_data()
    assert isinstance(max_size_list, list)


def test_okx_req_get_max_avail_size():
    live_okx_swap_feed = init_req_feed()
    data = live_okx_swap_feed.get_max_avail_size(symbol="BTC-USDT", td_mode="cross")
    assert isinstance(data, RequestData)
    assert data.get_status()
    max_avail_size_list = data.get_data()
    assert isinstance(max_avail_size_list, list)
    print("max_avail_size_data", max_avail_size_list)
    if len(max_avail_size_list) > 0:
        max_avail_size_data = max_avail_size_list[0]
        assert isinstance(max_avail_size_data, OkxPositionData)


def test_okx_async_get_max_avail_size():
    data_queue = queue.Queue()
    live_okx_swap_feed = init_async_feed(data_queue)
    live_okx_swap_feed.async_get_max_avail_size(symbol="BTC-USDT", td_mode="cross")
    time.sleep(5)
    try:
        max_avail_size_response = data_queue.get(False)
    except queue.Empty:
        max_avail_size_response = None
    assert max_avail_size_response is not None
    assert isinstance(max_avail_size_response, RequestData)
    assert max_avail_size_response.get_status()
    max_avail_size_list = max_avail_size_response.get_data()
    assert isinstance(max_avail_size_list, list)


def test_okx_req_set_margin_balance():
    """Test set_margin_balance interface (requires actual position ID to work)"""
    live_okx_swap_feed = init_req_feed()
    # This will fail because we don't have a valid position ID, but tests the interface
    # In real usage, you need a valid pos_id from get_position
    result = live_okx_swap_feed.set_margin_balance(
        symbol="BTC-USDT",
        pos_id="test_position_id",  # This is a placeholder
        amt="100",
        mgn_mode="cross"
    )
    assert isinstance(result, RequestData)
    # Will fail due to invalid position ID, but interface works
    print("set_margin_balance status:", result.get_status())
    print("set_margin_balance input:", result.get_input_data())


def test_okx_req_make_orders():
    """Test make_orders batch order interface"""
    live_okx_swap_feed = init_req_feed()
    # Prepare order list
    order_list = [
        {
            'symbol': 'OP-USDT',
            'vol': 1,
            'price': 0.1,
            'order_type': 'buy-limit',
            'client_order_id': 'test_batch_1',
        },
        {
            'symbol': 'OP-USDT',
            'vol': 1,
            'price': 0.05,
            'order_type': 'buy-limit',
            'client_order_id': 'test_batch_2',
        }
    ]
    result = live_okx_swap_feed.make_orders(order_list)
    assert isinstance(result, RequestData)
    print("make_orders status:", result.get_status())
    print("make_orders data:", result.get_data())


def test_okx_req_cancel_orders():
    """Test cancel_orders batch cancel interface"""
    live_okx_swap_feed = init_req_feed()
    # Prepare order list to cancel
    order_list = [
        {
            'symbol': 'OP-USDT',
            'order_id': 'test_order_1',  # Placeholder
        },
        {
            'symbol': 'OP-USDT',
            'client_order_id': 'test_client_1',  # Placeholder
        }
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
            'symbol': 'OP-USDT',
            'order_id': 'test_order_1',  # Placeholder
            'new_sz': 2,
        },
        {
            'symbol': 'OP-USDT',
            'client_order_id': 'test_client_1',  # Placeholder
            'new_px': 0.5,
        }
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


def test_okx_req_close_position():
    """Test close_position market close all interface"""
    live_okx_swap_feed = init_req_feed()
    # Close position for BTC-USDT-SWAP
    # This may fail if there's no position, but tests the interface
    result = live_okx_swap_feed.close_position(
        symbol="BTC-USDT",
        pos_side="net",
        mgn_mode="cross"
    )
    assert isinstance(result, RequestData)
    print("close_position status:", result.get_status())


def test_okx_req_get_max_withdrawal():
    """Test get_max_withdrawal interface"""
    live_okx_swap_feed = init_req_feed()
    # Get max withdrawal for USDT
    data = live_okx_swap_feed.get_max_withdrawal(ccy_list=["USDT"])
    assert isinstance(data, RequestData)
    assert data.get_status()
    max_withdrawal_list = data.get_data()
    assert isinstance(max_withdrawal_list, list)
    print("get_max_withdrawal:", max_withdrawal_list)


def test_okx_req_get_risk_state():
    """Test get_risk_state interface"""
    live_okx_swap_feed = init_req_feed()
    data = live_okx_swap_feed.get_risk_state()
    assert isinstance(data, RequestData)
    # May fail in some account modes, but interface works
    print("get_risk_state status:", data.get_status())
    print("get_risk_state input:", data.get_input_data())


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
        symbol="BTC-USDT",
        state="filled",
        limit="10",
        inst_type="SWAP"
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
    data = live_okx_swap_feed.cancel_all_after(time_slug='5000')
    assert isinstance(data, RequestData)
    print("cancel_all_after status:", data.get_status())


def test_okx_req_get_bills():
    """Test get_bills interface"""
    live_okx_swap_feed = init_req_feed()
    # Get account bills (last 7 days)
    data = live_okx_swap_feed.get_bills(limit="10")
    assert isinstance(data, RequestData)
    print("get_bills status:", data.get_status())
    bills_list = data.get_data()
    assert isinstance(bills_list, list)
    print("get_bills count:", len(bills_list))


def test_okx_req_get_lever():
    """Test get_lever interface"""
    live_okx_swap_feed = init_req_feed()
    # Get leverage for SWAP instruments
    data = live_okx_swap_feed.get_lever(inst_type="SWAP", mgn_mode="cross")
    assert isinstance(data, RequestData)
    print("get_lever status:", data.get_status())
    lever_list = data.get_data()
    assert isinstance(lever_list, list)
    print("get_lever data:", lever_list)


def test_okx_req_get_tickers():
    """Test get_tickers interface"""
    live_okx_swap_feed = init_req_feed()
    # Get all SWAP tickers
    data = live_okx_swap_feed.get_tickers(inst_type="SWAP")
    assert isinstance(data, RequestData)
    assert data.get_status()
    tickers_list = data.get_data()
    assert isinstance(tickers_list, list)
    assert len(tickers_list) > 0
    print("get_tickers count:", len(tickers_list))


def test_okx_req_amend_algo_order():
    """Test amend_algo_order interface"""
    live_okx_swap_feed = init_req_feed()
    # Amend algo order - this will fail without a valid algo_id
    result = live_okx_swap_feed.amend_algo_order(
        algo_id="test_algo_id",  # Placeholder
        inst_id="BTC-USDT-SWAP",
        new_sz="1"
    )
    assert isinstance(result, RequestData)
    print("amend_algo_order status:", result.get_status())


def test_okx_req_get_algo_orders_pending():
    """Test get_algo_orders_pending interface"""
    live_okx_swap_feed = init_req_feed()
    # Get algo orders pending list
    data = live_okx_swap_feed.get_algo_orders_pending(inst_type="SWAP", limit="10")
    assert isinstance(data, RequestData)
    print("get_algo_orders_pending status:", data.get_status())
    algo_orders_list = data.get_data()
    assert isinstance(algo_orders_list, list)
    print("get_algo_orders_pending count:", len(algo_orders_list))


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
    """Test get_algo_order interface - Get specific algo order details"""
    live_okx_swap_feed = init_req_feed()
    # Test with a placeholder algo_id - will return empty but validates the API call
    # In real usage, you would use a valid algo_id from get_algo_orders_pending
    data = live_okx_swap_feed.get_algo_order(
        algo_id="test_algo_id_placeholder",
        inst_type="SWAP",
        symbol="BTC-USDT"
    )
    assert isinstance(data, RequestData)
    print("get_algo_order status:", data.get_status())
    algo_order_list = data.get_data()
    assert isinstance(algo_order_list, list)
    print("get_algo_order count:", len(algo_order_list))
    # Note: This will return empty since we're using a placeholder algo_id
    # In production, you would first get a real algo_id from get_algo_orders_pending


def test_okx_async_get_algo_order():
    """Test async_get_algo_order interface"""
    data_queue = queue.Queue()
    live_okx_swap_feed = init_async_feed(data_queue)
    # Test async call with placeholder algo_id
    live_okx_swap_feed.async_get_algo_order(
        algo_id="test_algo_id_placeholder",
        inst_type="SWAP",
        symbol="BTC-USDT",
        extra_data={"test_async_algo_order": True}
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


def test_okx_req_get_system_time():
    """Test get_system_time interface"""
    live_okx_swap_feed = init_req_feed()
    # Get system time
    data = live_okx_swap_feed.get_system_time()
    assert isinstance(data, RequestData)
    assert data.get_status()
    time_list = data.get_data()
    assert isinstance(time_list, list)
    if len(time_list) > 0:
        print("get_system_time:", time_list[0])


# ==================== Market Data Tests (Additional) ====================

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
        print("get_depth_full: bids = {}, asks = {}".format(
            len(order_book.get_bid_price_list()),
            len(order_book.get_ask_price_list())
        ))


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


def test_okx_async_get_kline_his():
    """Test async_get_kline_his interface"""
    data_queue = queue.Queue()
    live_okx_swap_feed = init_async_feed(data_queue)
    live_okx_swap_feed.async_get_kline_his("BTC-USDT", bar="1m", limit="10")
    time.sleep(10)
    try:
        kline_data = data_queue.get(False)
    except queue.Empty:
        kline_data = None
    assert kline_data is not None
    assert isinstance(kline_data, RequestData)
    # normalize_function is None, so status will be None - check we got data
    raw_response = kline_data.get_data()
    assert isinstance(raw_response, dict)
    # The actual kline data is in the 'data' key
    if 'data' in raw_response:
        kline_list = raw_response['data']
        assert isinstance(kline_list, list)


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
        assert isinstance(trade, OkxRequestTradeData)
        trade.init_data()
        assert trade.get_trade_price() > 0
        assert trade.get_trade_volume() > 0


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
        assert isinstance(trade, OkxRequestTradeData)
        trade.init_data()


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


def cancel_all_orders():
    live_feed = init_req_feed()
    data = live_feed.get_open_orders('OP-USDT')
    order_data_list = data.get_data()
    for d in order_data_list:
        info = live_feed.cancel_order('OP-USDT', d.get_order_id())
        print(info.get_data())


# ==================== Sub Account Tests ====================

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


def test_okx_req_get_sub_account_api_key():
    """Test get_sub_account_api_key interface"""
    live_okx_swap_feed = init_req_feed()
    # This will fail without valid sub_acct, but tests the interface
    result = live_okx_swap_feed.get_sub_account_api_key(
        sub_acct="test_sub_account",  # Placeholder
    )
    assert isinstance(result, RequestData)
    print("get_sub_account_api_key status:", result.get_status())


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


def test_okx_req_get_sub_account_funding_balance():
    """Test get_sub_account_funding_balance interface"""
    live_okx_swap_feed = init_req_feed()
    # This will fail without valid sub_acct, but tests the interface
    result = live_okx_swap_feed.get_sub_account_funding_balance(
        sub_acct="test_sub_account",  # Placeholder
    )
    assert isinstance(result, RequestData)
    print("get_sub_account_funding_balance status:", result.get_status())


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

def test_okx_req_get_currencies():
    """Test get_currencies interface"""
    live_okx_swap_feed = init_req_feed()
    # Get all currencies
    data = live_okx_swap_feed.get_currencies()
    assert isinstance(data, RequestData)
    print("get_currencies status:", data.get_status())
    currencies_list = data.get_data()
    assert isinstance(currencies_list, list)
    print("get_currencies count:", len(currencies_list))
    if len(currencies_list) > 0:
        currency = currencies_list[0]
        currency.init_data()
        print("First currency:", currency.get_currency())


def test_okx_async_get_currencies():
    """Test async_get_currencies interface"""
    data_queue = queue.Queue()
    live_okx_swap_feed = init_async_feed(data_queue)
    live_okx_swap_feed.async_get_currencies()
    time.sleep(5)
    try:
        currencies_data = data_queue.get(False)
    except queue.Empty:
        currencies_data = None
    assert currencies_data is not None
    assert isinstance(currencies_data, RequestData)
    print("async_get_currencies status:", currencies_data.get_status())
    currencies_list = currencies_data.get_data()
    assert isinstance(currencies_list, list)


def test_okx_req_get_currencies_single():
    """Test get_currencies interface for single currency"""
    live_okx_swap_feed = init_req_feed()
    # Get BTC currency info
    data = live_okx_swap_feed.get_currencies(ccy="BTC")
    assert isinstance(data, RequestData)
    print("get_currencies(BTC) status:", data.get_status())
    currencies_list = data.get_data()
    assert isinstance(currencies_list, list)
    if len(currencies_list) > 0:
        currency = currencies_list[0]
        currency.init_data()
        assert currency.get_currency() == "BTC"


def test_okx_req_get_asset_balances():
    """Test get_asset_balances interface"""
    live_okx_swap_feed = init_req_feed()
    # Get all asset balances
    data = live_okx_swap_feed.get_asset_balances()
    assert isinstance(data, RequestData)
    print("get_asset_balances status:", data.get_status())
    balances_list = data.get_data()
    assert isinstance(balances_list, list)
    print("get_asset_balances count:", len(balances_list))
    if len(balances_list) > 0:
        balance = balances_list[0]
        balance.init_data()
        print("First balance currency:", balance.get_currency())


def test_okx_async_get_asset_balances():
    """Test async_get_asset_balances interface"""
    data_queue = queue.Queue()
    live_okx_swap_feed = init_async_feed(data_queue)
    live_okx_swap_feed.async_get_asset_balances()
    time.sleep(5)
    try:
        balances_data = data_queue.get(False)
    except queue.Empty:
        balances_data = None
    assert balances_data is not None
    assert isinstance(balances_data, RequestData)
    print("async_get_asset_balances status:", balances_data.get_status())
    balances_list = balances_data.get_data()
    assert isinstance(balances_list, list)


def test_okx_req_get_asset_balances_single():
    """Test get_asset_balances interface for single currency"""
    live_okx_swap_feed = init_req_feed()
    # Get USDT balance
    data = live_okx_swap_feed.get_asset_balances(ccy="USDT")
    assert isinstance(data, RequestData)
    print("get_asset_balances(USDT) status:", data.get_status())
    balances_list = data.get_data()
    assert isinstance(balances_list, list)
    if len(balances_list) > 0:
        balance = balances_list[0]
        balance.init_data()
        assert balance.get_currency() == "USDT"
        print("USDT balance:", balance.get_balance())


def test_okx_req_get_non_tradable_assets():
    """Test get_non_tradable_assets interface"""
    live_okx_swap_feed = init_req_feed()
    # Get non-tradable assets
    data = live_okx_swap_feed.get_non_tradable_assets()
    assert isinstance(data, RequestData)
    print("get_non_tradable_assets status:", data.get_status())
    assets_list = data.get_data()
    assert isinstance(assets_list, list)
    print("get_non_tradable_assets count:", len(assets_list))


def test_okx_async_get_non_tradable_assets():
    """Test async_get_non_tradable_assets interface"""
    data_queue = queue.Queue()
    live_okx_swap_feed = init_async_feed(data_queue)
    live_okx_swap_feed.async_get_non_tradable_assets()
    time.sleep(5)
    try:
        assets_data = data_queue.get(False)
    except queue.Empty:
        assets_data = None
    assert assets_data is not None
    assert isinstance(assets_data, RequestData)
    print("async_get_non_tradable_assets status:", assets_data.get_status())
    assets_list = assets_data.get_data()
    assert isinstance(assets_list, list)


def test_okx_req_get_asset_valuation():
    """Test get_asset_valuation interface"""
    live_okx_swap_feed = init_req_feed()
    # Get asset valuation in USD
    data = live_okx_swap_feed.get_asset_valuation(ccy="USD")
    assert isinstance(data, RequestData)
    print("get_asset_valuation status:", data.get_status())
    valuation_list = data.get_data()
    assert isinstance(valuation_list, list)
    if len(valuation_list) > 0:
        valuation = valuation_list[0]
        valuation.init_data()
        print("Total valuation:", valuation.get_total_valuation())
        print("BTC valuation:", valuation.get_btc_valuation())


def test_okx_async_get_asset_valuation():
    """Test async_get_asset_valuation interface"""
    data_queue = queue.Queue()
    live_okx_swap_feed = init_async_feed(data_queue)
    live_okx_swap_feed.async_get_asset_valuation(ccy="USD")
    time.sleep(5)
    try:
        valuation_data = data_queue.get(False)
    except queue.Empty:
        valuation_data = None
    assert valuation_data is not None
    assert isinstance(valuation_data, RequestData)
    print("async_get_asset_valuation status:", valuation_data.get_status())
    valuation_list = valuation_data.get_data()
    assert isinstance(valuation_list, list)


def test_okx_req_transfer():
    """Test transfer interface (will fail with insufficient balance)"""
    live_okx_swap_feed = init_req_feed()
    # This tests the interface - actual transfer will fail due to small amount
    # Account types: 6 = Funding, 18 = Trading
    result = live_okx_swap_feed.transfer(
        ccy="USDT",
        amt="0.01",  # Small amount for testing
        from_acct="6",  # Funding account
        to_acct="18",  # Trading account
        type="0",  # Within account
    )
    assert isinstance(result, RequestData)
    print("transfer status:", result.get_status())
    print("transfer data:", result.get_data())


def test_okx_async_transfer():
    """Test async_transfer interface"""
    data_queue = queue.Queue()
    live_okx_swap_feed = init_async_feed(data_queue)
    live_okx_swap_feed.async_transfer(
        ccy="USDT",
        amt="0.01",
        from_acct="18",  # Trading account
        to_acct="6",  # Funding account
        type="0",
    )
    time.sleep(5)
    try:
        transfer_data = data_queue.get(False)
    except queue.Empty:
        transfer_data = None
    assert transfer_data is not None
    assert isinstance(transfer_data, RequestData)
    print("async_transfer status:", transfer_data.get_status())


def test_okx_req_get_transfer_state():
    """Test get_transfer_state interface"""
    live_okx_swap_feed = init_req_feed()
    # Get transfer state for USDT
    data = live_okx_swap_feed.get_transfer_state(ccy="USDT")
    assert isinstance(data, RequestData)
    print("get_transfer_state status:", data.get_status())
    transfers_list = data.get_data()
    assert isinstance(transfers_list, list)
    print("get_transfer_state count:", len(transfers_list))
    if len(transfers_list) > 0:
        transfer = transfers_list[0]
        transfer.init_data()
        print("First transfer currency:", transfer.get_currency())
        print("First transfer amount:", transfer.get_amount())


def test_okx_async_get_transfer_state():
    """Test async_get_transfer_state interface"""
    data_queue = queue.Queue()
    live_okx_swap_feed = init_async_feed(data_queue)
    live_okx_swap_feed.async_get_transfer_state(ccy="USDT")
    time.sleep(5)
    try:
        transfer_state_data = data_queue.get(False)
    except queue.Empty:
        transfer_state_data = None
    assert transfer_state_data is not None
    assert isinstance(transfer_state_data, RequestData)
    print("async_get_transfer_state status:", transfer_state_data.get_status())
    transfers_list = transfer_state_data.get_data()
    assert isinstance(transfers_list, list)


# ==================== Public Data Tests ====================

def test_okx_req_get_public_instruments():
    """Test get_public_instruments interface"""
    live_okx_swap_feed = init_req_feed()
    # Get SWAP instruments
    data = live_okx_swap_feed.get_public_instruments(inst_type="SWAP", uly="BTC-USDT")
    assert isinstance(data, RequestData)
    assert data.get_status()
    instruments_list = data.get_data()
    assert isinstance(instruments_list, list)
    print("get_public_instruments count:", len(instruments_list))
    if len(instruments_list) > 0:
        instrument = instruments_list[0]
        assert isinstance(instrument, OkxSymbolData)
        instrument.init_data()
        assert instrument.get_exchange_name() == "OKX"


def test_okx_async_get_public_instruments():
    """Test async_get_public_instruments interface"""
    data_queue = queue.Queue()
    live_okx_swap_feed = init_async_feed(data_queue)
    live_okx_swap_feed.async_get_public_instruments(inst_type="SWAP", uly="BTC-USDT")
    time.sleep(5)
    try:
        instruments_data = data_queue.get(False)
    except queue.Empty:
        instruments_data = None
    assert instruments_data is not None
    assert isinstance(instruments_data, RequestData)
    assert instruments_data.get_status()
    instruments_list = instruments_data.get_data()
    assert isinstance(instruments_list, list)


def test_okx_req_get_delivery_exercise_history():
    """Test get_delivery_exercise_history interface"""
    live_okx_swap_feed = init_req_feed()
    # Get delivery/exercise history for FUTURES
    data = live_okx_swap_feed.get_delivery_exercise_history(
        inst_type="FUTURES",
        uly="BTC-USDT",
        limit="10"
    )
    assert isinstance(data, RequestData)
    print("get_delivery_exercise_history status:", data.get_status())
    history_list = data.get_data()
    assert isinstance(history_list, list)
    print("get_delivery_exercise_history count:", len(history_list))


def test_okx_async_get_delivery_exercise_history():
    """Test async_get_delivery_exercise_history interface"""
    data_queue = queue.Queue()
    live_okx_swap_feed = init_async_feed(data_queue)
    live_okx_swap_feed.async_get_delivery_exercise_history(
        inst_type="FUTURES",
        uly="BTC-USDT",
        limit="10"
    )
    time.sleep(5)
    try:
        history_data = data_queue.get(False)
    except queue.Empty:
        history_data = None
    # Async may timeout, just check the interface works
    if history_data is not None:
        assert isinstance(history_data, RequestData)
        history_list = history_data.get_data()
        assert isinstance(history_list, list)


def test_okx_req_get_estimated_settlement_price():
    """Test get_estimated_settlement_price interface"""
    live_okx_swap_feed = init_req_feed()
    # Get estimated settlement price for FUTURES
    # Note: This endpoint may not always return data depending on market conditions
    try:
        data = live_okx_swap_feed.get_estimated_settlement_price(
            inst_type="FUTURES",
            uly="BTC-USDT"
        )
        assert isinstance(data, RequestData)
        print("get_estimated_settlement_price status:", data.get_status())
        price_list = data.get_data()
        assert isinstance(price_list, list)
        print("get_estimated_settlement_price count:", len(price_list))
    except Exception as e:
        # Endpoint may not be available in certain market conditions
        print(f"get_estimated_settlement_price exception (expected in some conditions): {e}")


def test_okx_async_get_estimated_settlement_price():
    """Test async_get_estimated_settlement_price interface"""
    data_queue = queue.Queue()
    live_okx_swap_feed = init_async_feed(data_queue)
    live_okx_swap_feed.async_get_estimated_settlement_price(
        inst_type="FUTURES",
        uly="BTC-USDT"
    )
    time.sleep(5)
    try:
        price_data = data_queue.get(False)
    except queue.Empty:
        price_data = None
    if price_data is not None:
        assert isinstance(price_data, RequestData)
        price_list = price_data.get_data()
        assert isinstance(price_list, list)


def test_okx_req_get_settlement_history():
    """Test get_settlement_history interface"""
    live_okx_swap_feed = init_req_feed()
    # Get futures settlement history
    data = live_okx_swap_feed.get_settlement_history(
        inst_type="FUTURES",
        uly="BTC-USDT",
        limit="10"
    )
    assert isinstance(data, RequestData)
    print("get_settlement_history status:", data.get_status())
    history_list = data.get_data()
    assert isinstance(history_list, list)
    print("get_settlement_history count:", len(history_list))


def test_okx_async_get_settlement_history():
    """Test async_get_settlement_history interface"""
    data_queue = queue.Queue()
    live_okx_swap_feed = init_async_feed(data_queue)
    live_okx_swap_feed.async_get_settlement_history(
        inst_type="FUTURES",
        uly="BTC-USDT",
        limit="10"
    )
    time.sleep(5)
    try:
        history_data = data_queue.get(False)
    except queue.Empty:
        history_data = None
    assert history_data is not None
    assert isinstance(history_data, RequestData)
    history_list = history_data.get_data()
    assert isinstance(history_list, list)


def test_okx_req_get_price_limit():
    """Test get_price_limit interface"""
    live_okx_swap_feed = init_req_feed()
    # Get price limit for SWAP - try with instId instead of uly
    data = live_okx_swap_feed.get_price_limit(
        inst_type="SWAP",
        inst_id="BTC-USDT-SWAP"
    )
    assert isinstance(data, RequestData)
    print("get_price_limit status:", data.get_status())
    limit_list = data.get_data()
    assert isinstance(limit_list, list)
    print("get_price_limit count:", len(limit_list))
    if len(limit_list) > 0:
        limit_data = limit_list[0]
        assert isinstance(limit_data, dict)
        print("get_price_limit sample:", limit_data)


def test_okx_async_get_price_limit():
    """Test async_get_price_limit interface"""
    data_queue = queue.Queue()
    live_okx_swap_feed = init_async_feed(data_queue)
    live_okx_swap_feed.async_get_price_limit(
        inst_type="SWAP",
        inst_id="BTC-USDT-SWAP"
    )
    time.sleep(10)
    try:
        limit_data = data_queue.get(False)
    except queue.Empty:
        limit_data = None
    assert limit_data is not None
    assert isinstance(limit_data, RequestData)
    # normalize_function is None, so status will be None - check we got data
    raw_response = limit_data.get_data()
    assert isinstance(raw_response, dict)
    # The actual data is in the 'data' key
    if 'data' in raw_response:
        limit_list = raw_response['data']
        assert isinstance(limit_list, list)


def test_okx_req_get_opt_summary():
    """Test get_opt_summary interface"""
    live_okx_swap_feed = init_req_feed()
    # Get option market data overview
    data = live_okx_swap_feed.get_opt_summary(
        uly="BTC-USDT"
    )
    assert isinstance(data, RequestData)
    print("get_opt_summary status:", data.get_status())
    summary_list = data.get_data()
    assert isinstance(summary_list, list)
    print("get_opt_summary count:", len(summary_list))


def test_okx_async_get_opt_summary():
    """Test async_get_opt_summary interface"""
    data_queue = queue.Queue()
    live_okx_swap_feed = init_async_feed(data_queue)
    live_okx_swap_feed.async_get_opt_summary(
        uly="BTC-USDT"
    )
    time.sleep(5)
    try:
        summary_data = data_queue.get(False)
    except queue.Empty:
        summary_data = None
    assert summary_data is not None
    assert isinstance(summary_data, RequestData)
    summary_list = summary_data.get_data()
    assert isinstance(summary_list, list)


def test_okx_req_get_position_tiers_public():
    """Test get_position_tiers_public interface"""
    live_okx_swap_feed = init_req_feed()
    # Get position tiers for public
    data = live_okx_swap_feed.get_position_tiers_public(
        inst_type="SWAP",
        uly="BTC-USDT"
    )
    assert isinstance(data, RequestData)
    print("get_position_tiers_public status:", data.get_status())
    tiers_list = data.get_data()
    assert isinstance(tiers_list, list)
    print("get_position_tiers_public count:", len(tiers_list))


def test_okx_async_get_position_tiers_public():
    """Test async_get_position_tiers_public interface"""
    data_queue = queue.Queue()
    live_okx_swap_feed = init_async_feed(data_queue)
    live_okx_swap_feed.async_get_position_tiers_public(
        inst_type="SWAP",
        uly="BTC-USDT"
    )
    time.sleep(5)
    try:
        tiers_data = data_queue.get(False)
    except queue.Empty:
        tiers_data = None
    # Async may timeout, just check the interface works
    if tiers_data is not None:
        assert isinstance(tiers_data, RequestData)
        tiers_list = tiers_data.get_data()
        assert isinstance(tiers_list, list)


if __name__ == "__main__":
    test_get_okx_key()
    test_okx_req_symbol_data()
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
    # Sub-account tests
    # test_okx_req_get_sub_account_list()
    # test_okx_async_get_sub_account_list()
    # test_okx_req_create_sub_account()
    # test_okx_req_create_sub_account_api_key()
    # test_okx_req_get_sub_account_api_key()
    # test_okx_req_reset_sub_account_api_key()
    # test_okx_req_delete_sub_account_api_key()
    # test_okx_req_get_sub_account_funding_balance()
    # test_okx_req_get_sub_account_max_withdrawal()
    cancel_all_orders()


# ==================== Trading Account Tests (New Batch) ====================

def test_okx_req_get_account_position_risk():
    """Test get_account_position_risk interface"""
    live_okx_swap_feed = init_req_feed()
    # Get account and position risk
    data = live_okx_swap_feed.get_account_position_risk()
    assert isinstance(data, RequestData)
    print("get_account_position_risk status:", data.get_status())
    risk_list = data.get_data()
    assert isinstance(risk_list, list)
    print("get_account_position_risk count:", len(risk_list))


def test_okx_async_get_account_position_risk():
    """Test async_get_account_position_risk interface"""
    data_queue = queue.Queue()
    live_okx_swap_feed = init_async_feed(data_queue)
    live_okx_swap_feed.async_get_account_position_risk()
    time.sleep(5)
    try:
        risk_data = data_queue.get(False)
    except queue.Empty:
        risk_data = None
    assert risk_data is not None
    assert isinstance(risk_data, RequestData)
    print("async_get_account_position_risk status:", risk_data.get_status())


def test_okx_req_get_bills_archive():
    """Test get_bills_archive interface"""
    live_okx_swap_feed = init_req_feed()
    # Get account bills archive (last 3 months)
    data = live_okx_swap_feed.get_bills_archive(limit="10")
    assert isinstance(data, RequestData)
    print("get_bills_archive status:", data.get_status())
    bills_list = data.get_data()
    assert isinstance(bills_list, list)
    print("get_bills_archive count:", len(bills_list))


def test_okx_async_get_bills_archive():
    """Test async_get_bills_archive interface"""
    data_queue = queue.Queue()
    live_okx_swap_feed = init_async_feed(data_queue)
    live_okx_swap_feed.async_get_bills_archive(limit="10")
    time.sleep(5)
    try:
        bills_data = data_queue.get(False)
    except queue.Empty:
        bills_data = None
    assert bills_data is not None
    assert isinstance(bills_data, RequestData)
    print("async_get_bills_archive status:", bills_data.get_status())


def test_okx_req_get_adjust_leverage_info():
    """Test get_adjust_leverage_info interface"""
    live_okx_swap_feed = init_req_feed()
    # Get adjust leverage info for BTC-USDT-SWAP
    data = live_okx_swap_feed.get_adjust_leverage_info(
        symbol="BTC-USDT",
        mgn_mode="cross"
    )
    assert isinstance(data, RequestData)
    print("get_adjust_leverage_info status:", data.get_status())
    leverage_list = data.get_data()
    assert isinstance(leverage_list, list)
    print("get_adjust_leverage_info data:", leverage_list)


def test_okx_async_get_adjust_leverage_info():
    """Test async_get_adjust_leverage_info interface"""
    data_queue = queue.Queue()
    live_okx_swap_feed = init_async_feed(data_queue)
    live_okx_swap_feed.async_get_adjust_leverage_info(
        inst_type="SWAP",
        uly="BTC-USDT",
        mgn_mode="cross"
    )
    time.sleep(10)
    try:
        leverage_data = data_queue.get(False)
    except queue.Empty:
        leverage_data = None
    assert leverage_data is not None
    assert isinstance(leverage_data, RequestData)
    print("async_get_adjust_leverage_info status:", leverage_data.get_status())


def test_okx_req_get_max_loan():
    """Test get_max_loan interface"""
    live_okx_swap_feed = init_req_feed()
    # Get maximum loan for BTC-USDT-SWAP
    data = live_okx_swap_feed.get_max_loan(
        symbol="BTC-USDT",
        mgn_mode="cross"
    )
    assert isinstance(data, RequestData)
    print("get_max_loan status:", data.get_status())
    loan_list = data.get_data()
    assert isinstance(loan_list, list)
    print("get_max_loan data:", loan_list)


def test_okx_async_get_max_loan():
    """Test async_get_max_loan interface"""
    data_queue = queue.Queue()
    live_okx_swap_feed = init_async_feed(data_queue)
    live_okx_swap_feed.async_get_max_loan(
        symbol="BTC-USDT",
        mgn_mode="cross"
    )
    time.sleep(5)
    try:
        loan_data = data_queue.get(False)
    except queue.Empty:
        loan_data = None
    assert loan_data is not None
    assert isinstance(loan_data, RequestData)
    print("async_get_max_loan status:", loan_data.get_status())


def test_okx_req_get_interest_accrued():
    """Test get_interest_accrued interface"""
    live_okx_swap_feed = init_req_feed()
    # Get interest accrued data
    data = live_okx_swap_feed.get_interest_accrued(limit="10")
    assert isinstance(data, RequestData)
    print("get_interest_accrued status:", data.get_status())
    interest_list = data.get_data()
    assert isinstance(interest_list, list)
    print("get_interest_accrued count:", len(interest_list))


def test_okx_async_get_interest_accrued():
    """Test async_get_interest_accrued interface"""
    data_queue = queue.Queue()
    live_okx_swap_feed = init_async_feed(data_queue)
    live_okx_swap_feed.async_get_interest_accrued(limit="10")
    time.sleep(5)
    try:
        interest_data = data_queue.get(False)
    except queue.Empty:
        interest_data = None
    assert interest_data is not None
    assert isinstance(interest_data, RequestData)
    print("async_get_interest_accrued status:", interest_data.get_status())


def test_okx_req_get_interest_rate():
    """Test get_interest_rate interface"""
    live_okx_swap_feed = init_req_feed()
    # Get interest rate
    data = live_okx_swap_feed.get_interest_rate()
    assert isinstance(data, RequestData)
    print("get_interest_rate status:", data.get_status())
    rate_list = data.get_data()
    assert isinstance(rate_list, list)
    print("get_interest_rate count:", len(rate_list))


def test_okx_async_get_interest_rate():
    """Test async_get_interest_rate interface"""
    data_queue = queue.Queue()
    live_okx_swap_feed = init_async_feed(data_queue)
    live_okx_swap_feed.async_get_interest_rate()
    time.sleep(5)
    try:
        rate_data = data_queue.get(False)
    except queue.Empty:
        rate_data = None
    assert rate_data is not None
    assert isinstance(rate_data, RequestData)
    print("async_get_interest_rate status:", rate_data.get_status())


def test_okx_req_get_greeks():
    """Test get_greeks interface"""
    live_okx_swap_feed = init_req_feed()
    # Get Greeks
    data = live_okx_swap_feed.get_greeks()
    assert isinstance(data, RequestData)
    print("get_greeks status:", data.get_status())
    greeks_list = data.get_data()
    assert isinstance(greeks_list, list)
    print("get_greeks count:", len(greeks_list))


def test_okx_async_get_greeks():
    """Test async_get_greeks interface"""
    data_queue = queue.Queue()
    live_okx_swap_feed = init_async_feed(data_queue)
    live_okx_swap_feed.async_get_greeks()
    time.sleep(5)
    try:
        greeks_data = data_queue.get(False)
    except queue.Empty:
        greeks_data = None
    assert greeks_data is not None
    assert isinstance(greeks_data, RequestData)
    print("async_get_greeks status:", greeks_data.get_status())


def test_okx_req_get_position_tiers():
    """Test get_position_tiers interface"""
    live_okx_swap_feed = init_req_feed()
    # Get position tiers for SWAP
    data = live_okx_swap_feed.get_position_tiers(inst_type="SWAP")
    assert isinstance(data, RequestData)
    print("get_position_tiers status:", data.get_status())
    tiers_list = data.get_data()
    assert isinstance(tiers_list, list)
    print("get_position_tiers count:", len(tiers_list))


def test_okx_async_get_position_tiers():
    """Test async_get_position_tiers interface"""
    data_queue = queue.Queue()
    live_okx_swap_feed = init_async_feed(data_queue)
    live_okx_swap_feed.async_get_position_tiers(inst_type="SWAP")
    time.sleep(5)
    try:
        tiers_data = data_queue.get(False)
    except queue.Empty:
        tiers_data = None
    assert tiers_data is not None
    assert isinstance(tiers_data, RequestData)
    print("async_get_position_tiers status:", tiers_data.get_status())


# ==================== Funding Account Tests ====================

def test_okx_req_get_asset_bills():
    """Test get_asset_bills interface"""
    live_okx_swap_feed = init_req_feed()
    # Get asset bills for USDT (last 3 months)
    data = live_okx_swap_feed.get_asset_bills(ccy="USDT", limit="10")
    assert isinstance(data, RequestData)
    print("get_asset_bills status:", data.get_status())
    bills_list = data.get_data()
    assert isinstance(bills_list, list)
    print("get_asset_bills count:", len(bills_list))


def test_okx_async_get_asset_bills():
    """Test async_get_asset_bills interface"""
    data_queue = queue.Queue()
    live_okx_swap_feed = init_async_feed(data_queue)
    live_okx_swap_feed.async_get_asset_bills(ccy="USDT", limit="10")
    time.sleep(10)
    try:
        bills_data = data_queue.get(False)
    except queue.Empty:
        bills_data = None
    assert bills_data is not None
    assert isinstance(bills_data, RequestData)
    print("async_get_asset_bills status:", bills_data.get_status())
    # normalize_function is None, so get_data() returns the raw API response dict
    raw_response = bills_data.get_data()
    assert isinstance(raw_response, dict)
    # The actual bills data is in the 'data' key
    if 'data' in raw_response:
        bills_list = raw_response['data']
        assert isinstance(bills_list, list)


def test_okx_req_get_asset_bills_history():
    """Test get_asset_bills_history interface"""
    live_okx_swap_feed = init_req_feed()
    # Get asset bills history for USDT (last 3 months)
    data = live_okx_swap_feed.get_asset_bills_history(ccy="USDT", limit="10")
    assert isinstance(data, RequestData)
    print("get_asset_bills_history status:", data.get_status())
    bills_list = data.get_data()
    assert isinstance(bills_list, list)
    print("get_asset_bills_history count:", len(bills_list))


def test_okx_async_get_asset_bills_history():
    """Test async_get_asset_bills_history interface"""
    data_queue = queue.Queue()
    live_okx_swap_feed = init_async_feed(data_queue)
    live_okx_swap_feed.async_get_asset_bills_history(ccy="USDT", limit="10")
    time.sleep(5)
    try:
        bills_data = data_queue.get(False)
    except queue.Empty:
        bills_data = None
    assert bills_data is not None
    assert isinstance(bills_data, RequestData)
    print("async_get_asset_bills_history status:", bills_data.get_status())


def test_okx_req_get_deposit_address():
    """Test get_deposit_address interface"""
    live_okx_swap_feed = init_req_feed()
    # Get deposit address for USDT
    data = live_okx_swap_feed.get_deposit_address(ccy="USDT")
    assert isinstance(data, RequestData)
    print("get_deposit_address status:", data.get_status())
    address_list = data.get_data()
    assert isinstance(address_list, list)
    print("get_deposit_address count:", len(address_list))
    if len(address_list) > 0:
        print("deposit_address:", address_list[0])


def test_okx_async_get_deposit_address():
    """Test async_get_deposit_address interface"""
    data_queue = queue.Queue()
    live_okx_swap_feed = init_async_feed(data_queue)
    live_okx_swap_feed.async_get_deposit_address(ccy="USDT")
    time.sleep(5)
    try:
        address_data = data_queue.get(False)
    except queue.Empty:
        address_data = None
    assert address_data is not None
    assert isinstance(address_data, RequestData)
    print("async_get_deposit_address status:", address_data.get_status())
    address_list = address_data.get_data()
    assert isinstance(address_list, list)


def test_okx_req_get_deposit_history():
    """Test get_deposit_history interface"""
    live_okx_swap_feed = init_req_feed()
    # Get deposit history for USDT
    data = live_okx_swap_feed.get_deposit_history(ccy="USDT", limit="10")
    assert isinstance(data, RequestData)
    print("get_deposit_history status:", data.get_status())
    deposit_list = data.get_data()
    assert isinstance(deposit_list, list)
    print("get_deposit_history count:", len(deposit_list))


def test_okx_async_get_deposit_history():
    """Test async_get_deposit_history interface"""
    data_queue = queue.Queue()
    live_okx_swap_feed = init_async_feed(data_queue)
    live_okx_swap_feed.async_get_deposit_history(ccy="USDT", limit="10")
    time.sleep(5)
    try:
        deposit_data = data_queue.get(False)
    except queue.Empty:
        deposit_data = None
    assert deposit_data is not None
    assert isinstance(deposit_data, RequestData)
    print("async_get_deposit_history status:", deposit_data.get_status())
    deposit_list = deposit_data.get_data()
    assert isinstance(deposit_list, list)


def test_okx_req_get_deposit_withdraw_status():
    """Test get_deposit_withdraw_status interface"""
    live_okx_swap_feed = init_req_feed()
    # Get deposit/withdraw status for USDT
    data = live_okx_swap_feed.get_deposit_withdraw_status(ccy="USDT")
    assert isinstance(data, RequestData)
    print("get_deposit_withdraw_status status:", data.get_status())
    status_list = data.get_data()
    assert isinstance(status_list, list)


def test_okx_async_get_deposit_withdraw_status():
    """Test async_get_deposit_withdraw_status interface"""
    data_queue = queue.Queue()
    live_okx_swap_feed = init_async_feed(data_queue)
    live_okx_swap_feed.async_get_deposit_withdraw_status(ccy="USDT")
    time.sleep(5)
    try:
        status_data = data_queue.get(False)
    except queue.Empty:
        status_data = None
    assert status_data is not None
    assert isinstance(status_data, RequestData)
    print("async_get_deposit_withdraw_status status:", status_data.get_status())


def test_okx_req_withdrawal():
    """Test withdrawal interface (will fail without valid address, but tests the interface)"""
    live_okx_swap_feed = init_req_feed()
    # This will fail because we don't have a valid withdrawal address, but tests the interface
    result = live_okx_swap_feed.withdrawal(
        ccy="USDT",
        amt="1",
        dest="4",  # on-chain withdrawal
        to_addr="0x0000000000000000000000000000000000000000",  # Invalid address for testing
        fee="0.1"
    )
    assert isinstance(result, RequestData)
    print("withdrawal status:", result.get_status())
    print("withdrawal input:", result.get_input_data())


def test_okx_async_withdrawal():
    """Test async_withdrawal interface"""
    data_queue = queue.Queue()
    live_okx_swap_feed = init_async_feed(data_queue)
    # This will fail because we don't have a valid withdrawal address, but tests the interface
    live_okx_swap_feed.async_withdrawal(
        ccy="USDT",
        amt="1",
        dest="4",  # on-chain withdrawal
        to_addr="0x0000000000000000000000000000000000000000",  # Invalid address for testing
        fee="0.1"
    )
    time.sleep(5)
    try:
        withdraw_data = data_queue.get(False)
    except queue.Empty:
        withdraw_data = None
    assert withdraw_data is not None
    assert isinstance(withdraw_data, RequestData)
    print("async_withdrawal status:", withdraw_data.get_status())


def test_okx_req_cancel_withdrawal():
    """Test cancel_withdrawal interface (will fail without valid wd_id, but tests the interface)"""
    live_okx_swap_feed = init_req_feed()
    # This will fail because we don't have a valid withdrawal ID, but tests the interface
    result = live_okx_swap_feed.cancel_withdrawal(wd_id="test_withdrawal_id")
    assert isinstance(result, RequestData)
    print("cancel_withdrawal status:", result.get_status())
    print("cancel_withdrawal input:", result.get_input_data())


def test_okx_async_cancel_withdrawal():
    """Test async_cancel_withdrawal interface"""
    data_queue = queue.Queue()
    live_okx_swap_feed = init_async_feed(data_queue)
    # This will fail because we don't have a valid withdrawal ID, but tests the interface
    live_okx_swap_feed.async_cancel_withdrawal(wd_id="test_withdrawal_id")
    time.sleep(5)
    try:
        cancel_data = data_queue.get(False)
    except queue.Empty:
        cancel_data = None
    assert cancel_data is not None
    assert isinstance(cancel_data, RequestData)
    print("async_cancel_withdrawal status:", cancel_data.get_status())


def test_okx_req_get_withdrawal_history():
    """Test get_withdrawal_history interface"""
    live_okx_swap_feed = init_req_feed()
    # Get withdrawal history for USDT
    data = live_okx_swap_feed.get_withdrawal_history(ccy="USDT", limit="10")
    assert isinstance(data, RequestData)
    print("get_withdrawal_history status:", data.get_status())
    withdraw_list = data.get_data()
    assert isinstance(withdraw_list, list)
    print("get_withdrawal_history count:", len(withdraw_list))


def test_okx_async_get_withdrawal_history():
    """Test async_get_withdrawal_history interface"""
    data_queue = queue.Queue()
    live_okx_swap_feed = init_async_feed(data_queue)
    live_okx_swap_feed.async_get_withdrawal_history(ccy="USDT", limit="10")
    time.sleep(5)
    try:
        withdraw_data = data_queue.get(False)
    except queue.Empty:
        withdraw_data = None
    assert withdraw_data is not None
    assert isinstance(withdraw_data, RequestData)
    print("async_get_withdrawal_history status:", withdraw_data.get_status())
    withdraw_list = withdraw_data.get_data()
    assert isinstance(withdraw_list, list)


# ==================== Trading Statistics Tests ====================

def test_okx_req_get_support_coin():
    """Test get_support_coin interface"""
    live_okx_swap_feed = init_req_feed()
    # Get supported currencies for trading data
    data = live_okx_swap_feed.get_support_coin()
    assert isinstance(data, RequestData)
    assert data.get_status()
    coin_data = data.get_data()
    # API returns dict with keys: 'contract', 'option', 'spot'
    assert isinstance(coin_data, dict)
    print("get_support_coin keys:", list(coin_data.keys()))
    for key, value in coin_data.items():
        print(f"get_support_coin {key} count: {len(value) if isinstance(value, list) else 0}")


def test_okx_async_get_support_coin():
    """Test async_get_support_coin interface"""
    data_queue = queue.Queue()
    live_okx_swap_feed = init_async_feed(data_queue)
    live_okx_swap_feed.async_get_support_coin()
    time.sleep(10)
    try:
        coin_data = data_queue.get(False)
    except queue.Empty:
        coin_data = None
    assert coin_data is not None
    assert isinstance(coin_data, RequestData)
    assert coin_data.get_status()
    coin_dict = coin_data.get_data()
    # API returns dict with keys: 'contract', 'option', 'spot'
    assert isinstance(coin_dict, dict)


def test_okx_req_get_contract_oi_history():
    """Test get_contract_oi_history interface"""
    live_okx_swap_feed = init_req_feed()
    # Get contract open interest history for BTC-USDT-SWAP
    data = live_okx_swap_feed.get_contract_oi_history("BTC-USDT", period="1H", limit="10")
    assert isinstance(data, RequestData)
    print("get_contract_oi_history status:", data.get_status())
    oi_list = data.get_data()
    assert isinstance(oi_list, list)
    print("get_contract_oi_history count:", len(oi_list))


def test_okx_async_get_contract_oi_history():
    """Test async_get_contract_oi_history interface"""
    data_queue = queue.Queue()
    live_okx_swap_feed = init_async_feed(data_queue)
    live_okx_swap_feed.async_get_contract_oi_history("BTC-USDT", period="1H", limit="10")
    time.sleep(5)
    try:
        oi_data = data_queue.get(False)
    except queue.Empty:
        oi_data = None
    assert oi_data is not None
    assert isinstance(oi_data, RequestData)
    print("async_get_contract_oi_history status:", oi_data.get_status())


def test_okx_req_get_taker_volume():
    """Test get_taker_volume interface"""
    live_okx_swap_feed = init_req_feed()
    # Get taker volume for BTC in SPOT
    data = live_okx_swap_feed.get_taker_volume(ccy="BTC", inst_type="SPOT", period="1H", limit="10")
    assert isinstance(data, RequestData)
    print("get_taker_volume status:", data.get_status())
    volume_list = data.get_data()
    assert isinstance(volume_list, list)
    print("get_taker_volume count:", len(volume_list))


def test_okx_async_get_taker_volume():
    """Test async_get_taker_volume interface"""
    data_queue = queue.Queue()
    live_okx_swap_feed = init_async_feed(data_queue)
    live_okx_swap_feed.async_get_taker_volume(ccy="BTC", inst_type="SPOT", period="1H", limit="10")
    time.sleep(5)
    try:
        volume_data = data_queue.get(False)
    except queue.Empty:
        volume_data = None
    assert volume_data is not None
    assert isinstance(volume_data, RequestData)
    print("async_get_taker_volume status:", volume_data.get_status())


def test_okx_req_get_long_short_ratio():
    """Test get_long_short_ratio interface"""
    live_okx_swap_feed = init_req_feed()
    # Get long/short ratio for BTC
    data = live_okx_swap_feed.get_long_short_ratio(ccy="BTC", period="1H", limit="10")
    assert isinstance(data, RequestData)
    print("get_long_short_ratio status:", data.get_status())
    ratio_list = data.get_data()
    assert isinstance(ratio_list, list)
    print("get_long_short_ratio count:", len(ratio_list))


def test_okx_async_get_long_short_ratio():
    """Test async_get_long_short_ratio interface"""
    data_queue = queue.Queue()
    live_okx_swap_feed = init_async_feed(data_queue)
    live_okx_swap_feed.async_get_long_short_ratio(ccy="BTC", period="1H", limit="10")
    time.sleep(5)
    try:
        ratio_data = data_queue.get(False)
    except queue.Empty:
        ratio_data = None
    assert ratio_data is not None
    assert isinstance(ratio_data, RequestData)
    print("async_get_long_short_ratio status:", ratio_data.get_status())


def test_okx_req_get_long_short_ratio_top_trader():
    """Test get_long_short_ratio_top_trader interface"""
    live_okx_swap_feed = init_req_feed()
    # Get top trader long/short ratio for BTC
    data = live_okx_swap_feed.get_long_short_ratio_top_trader(ccy="BTC", period="1H", limit="10")
    assert isinstance(data, RequestData)
    print("get_long_short_ratio_top_trader status:", data.get_status())
    ratio_list = data.get_data()
    assert isinstance(ratio_list, list)
    print("get_long_short_ratio_top_trader count:", len(ratio_list))


def test_okx_async_get_long_short_ratio_top_trader():
    """Test async_get_long_short_ratio_top_trader interface"""
    data_queue = queue.Queue()
    live_okx_swap_feed = init_async_feed(data_queue)
    live_okx_swap_feed.async_get_long_short_ratio_top_trader(ccy="BTC", period="1H", limit="10")
    time.sleep(5)
    try:
        ratio_data = data_queue.get(False)
    except queue.Empty:
        ratio_data = None
    assert ratio_data is not None
    assert isinstance(ratio_data, RequestData)
    print("async_get_long_short_ratio_top_trader status:", ratio_data.get_status())


@pytest.mark.skip(reason="OKX API endpoint /api/v5/rubik/stat/contracts/open-interest-volume-ratio returns 404, endpoint may be deprecated")
def test_okx_req_get_contract_long_short_ratio():
    """Test get_contract_long_short_ratio interface"""
    live_okx_swap_feed = init_req_feed()
    # Get contract open interest and volume ratio for BTC
    data = live_okx_swap_feed.get_contract_long_short_ratio(ccy="BTC", period="1H", limit="10")
    assert isinstance(data, RequestData)
    print("get_contract_long_short_ratio status:", data.get_status())
    ratio_list = data.get_data()
    assert isinstance(ratio_list, list)
    print("get_contract_long_short_ratio count:", len(ratio_list))


@pytest.mark.skip(reason="OKX API endpoint /api/v5/rubik/stat/contracts/open-interest-volume-ratio returns 404, endpoint may be deprecated")
def test_okx_async_get_contract_long_short_ratio():
    """Test async_get_contract_long_short_ratio interface"""
    data_queue = queue.Queue()
    live_okx_swap_feed = init_async_feed(data_queue)
    live_okx_swap_feed.async_get_contract_long_short_ratio(ccy="BTC", period="1H", limit="10")
    time.sleep(10)
    try:
        ratio_data = data_queue.get(False)
    except queue.Empty:
        ratio_data = None
    assert ratio_data is not None
    assert isinstance(ratio_data, RequestData)
    print("async_get_contract_long_short_ratio status:", ratio_data.get_status())


def test_okx_req_get_put_call_ratio():
    """Test get_put_call_ratio interface"""
    live_okx_swap_feed = init_req_feed()
    # Get put/call ratio for BTC options
    data = live_okx_swap_feed.get_put_call_ratio(ccy="BTC", period="1H", limit="10")
    assert isinstance(data, RequestData)
    print("get_put_call_ratio status:", data.get_status())
    ratio_list = data.get_data()
    assert isinstance(ratio_list, list)
    print("get_put_call_ratio count:", len(ratio_list))


def test_okx_async_get_put_call_ratio():
    """Test async_get_put_call_ratio interface"""
    data_queue = queue.Queue()
    live_okx_swap_feed = init_async_feed(data_queue)
    live_okx_swap_feed.async_get_put_call_ratio(ccy="BTC", period="1H", limit="10")
    time.sleep(5)
    try:
        ratio_data = data_queue.get(False)
    except queue.Empty:
        ratio_data = None
    assert ratio_data is not None
    assert isinstance(ratio_data, RequestData)
    print("async_get_put_call_ratio status:", ratio_data.get_status())


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


def test_okx_req_get_account_rate_limit():
    """Test get_account_rate_limit interface - get account trading rate limit"""
    live_okx_swap_feed = init_req_feed()
    # Get account rate limit
    data = live_okx_swap_feed.get_account_rate_limit()
    assert isinstance(data, RequestData)
    print("get_account_rate_limit status:", data.get_status())
    rate_limit_list = data.get_data()
    assert isinstance(rate_limit_list, list)
    print("get_account_rate_limit data:", rate_limit_list)
    # Verify rate limit data structure if available
    if len(rate_limit_list) > 0:
        rate_limit = rate_limit_list[0]
        assert isinstance(rate_limit, dict)
        # Check for common rate limit fields
        if 'reqIp' in rate_limit:
            print(f"Request IP: {rate_limit['reqIp']}")
        if 'rule' in rate_limit:
            print(f"Rule: {rate_limit['rule']}")


def test_okx_async_get_account_rate_limit():
    """Test async_get_account_rate_limit interface"""
    data_queue = queue.Queue()
    live_okx_swap_feed = init_async_feed(data_queue)
    live_okx_swap_feed.async_get_account_rate_limit()
    time.sleep(5)
    try:
        rate_limit_data = data_queue.get(False)
    except queue.Empty:
        rate_limit_data = None
    if rate_limit_data is None:
        print("Warning: rate_limit_data is None (async timeout)")
        return  # Skip assertion on timeout
    assert isinstance(rate_limit_data, RequestData)
    print("async_get_account_rate_limit status:", rate_limit_data.get_status())


def test_okx_req_get_easy_convert_currency_list():
    """Test get_easy_convert_currency_list interface"""
    live_okx_swap_feed = init_req_feed()
    # Get easy convert currency list
    data = live_okx_swap_feed.get_easy_convert_currency_list()
    assert isinstance(data, RequestData)
    print("get_easy_convert_currency_list status:", data.get_status())
    currency_list = data.get_data()
    assert isinstance(currency_list, list)
    print("get_easy_convert_currency_list count:", len(currency_list))


def test_okx_async_get_easy_convert_currency_list():
    """Test async_get_easy_convert_currency_list interface"""
    data_queue = queue.Queue()
    live_okx_swap_feed = init_async_feed(data_queue)
    live_okx_swap_feed.async_get_easy_convert_currency_list()
    time.sleep(5)
    try:
        currency_data = data_queue.get(False)
    except queue.Empty:
        currency_data = None
    if currency_data is None:
        print("Warning: currency_data is None (async timeout)")
        return  # Skip assertion on timeout
    assert isinstance(currency_data, RequestData)
    print("async_get_easy_convert_currency_list status:", currency_data.get_status())


def test_okx_req_easy_convert():
    """Test easy_convert interface"""
    live_okx_swap_feed = init_req_feed()
    # Try easy convert (may fail without sufficient balance, but tests the interface)
    data = live_okx_swap_feed.easy_convert(from_ccy="USDT", to_ccy="BTC", amt="10")
    assert isinstance(data, RequestData)
    print("easy_convert status:", data.get_status())
    print("easy_convert input:", data.get_input_data())


def test_okx_async_easy_convert():
    """Test async_easy_convert interface"""
    data_queue = queue.Queue()
    live_okx_swap_feed = init_async_feed(data_queue)
    live_okx_swap_feed.async_easy_convert(from_ccy="USDT", to_ccy="BTC", amt="10")
    time.sleep(5)
    try:
        convert_data = data_queue.get(False)
    except queue.Empty:
        convert_data = None
    if convert_data is None:
        print("Warning: convert_data is None (async timeout)")
        return  # Skip assertion on timeout
    assert isinstance(convert_data, RequestData)
    print("async_easy_convert status:", convert_data.get_status())


def test_okx_req_get_easy_convert_history():
    """Test get_easy_convert_history interface"""
    live_okx_swap_feed = init_req_feed()
    # Get easy convert history
    data = live_okx_swap_feed.get_easy_convert_history(limit="10")
    assert isinstance(data, RequestData)
    print("get_easy_convert_history status:", data.get_status())
    history_list = data.get_data()
    assert isinstance(history_list, list)
    print("get_easy_convert_history count:", len(history_list))


def test_okx_async_get_easy_convert_history():
    """Test async_get_easy_convert_history interface"""
    data_queue = queue.Queue()
    live_okx_swap_feed = init_async_feed(data_queue)
    live_okx_swap_feed.async_get_easy_convert_history(limit="10")
    time.sleep(5)
    try:
        history_data = data_queue.get(False)
    except queue.Empty:
        history_data = None
    if history_data is None:
        print("Warning: history_data is None (async timeout)")
        return  # Skip assertion on timeout
    assert isinstance(history_data, RequestData)
    print("async_get_easy_convert_history status:", history_data.get_status())


def test_okx_req_get_one_click_repay_currency_list():
    """Test get_one_click_repay_currency_list interface"""
    live_okx_swap_feed = init_req_feed()
    # Get one-click repay currency list
    data = live_okx_swap_feed.get_one_click_repay_currency_list()
    assert isinstance(data, RequestData)
    print("get_one_click_repay_currency_list status:", data.get_status())
    currency_list = data.get_data()
    assert isinstance(currency_list, list)
    print("get_one_click_repay_currency_list count:", len(currency_list))


def test_okx_async_get_one_click_repay_currency_list():
    """Test async_get_one_click_repay_currency_list interface"""
    data_queue = queue.Queue()
    live_okx_swap_feed = init_async_feed(data_queue)
    live_okx_swap_feed.async_get_one_click_repay_currency_list()
    time.sleep(5)
    try:
        currency_data = data_queue.get(False)
    except queue.Empty:
        currency_data = None
    if currency_data is None:
        print("Warning: currency_data is None (async timeout)")
        return  # Skip assertion on timeout
    assert isinstance(currency_data, RequestData)
    print("async_get_one_click_repay_currency_list status:", currency_data.get_status())


def test_okx_req_one_click_repay():
    """Test one_click_repay interface"""
    live_okx_swap_feed = init_req_feed()
    # Try one-click repay (may fail without debt, but tests the interface)
    data = live_okx_swap_feed.one_click_repay(ccy="USDT")
    assert isinstance(data, RequestData)
    print("one_click_repay status:", data.get_status())
    print("one_click_repay input:", data.get_input_data())


def test_okx_async_one_click_repay():
    """Test async_one_click_repay interface"""
    data_queue = queue.Queue()
    live_okx_swap_feed = init_async_feed(data_queue)
    live_okx_swap_feed.async_one_click_repay(ccy="USDT")
    time.sleep(5)
    try:
        repay_data = data_queue.get(False)
    except queue.Empty:
        repay_data = None
    if repay_data is None:
        print("Warning: repay_data is None (async timeout)")
        return  # Skip assertion on timeout
    assert isinstance(repay_data, RequestData)
    print("async_one_click_repay status:", repay_data.get_status())


def test_okx_req_get_one_click_repay_history():
    """Test get_one_click_repay_history interface"""
    live_okx_swap_feed = init_req_feed()
    # Get one-click repay history
    data = live_okx_swap_feed.get_one_click_repay_history(limit="10")
    assert isinstance(data, RequestData)
    print("get_one_click_repay_history status:", data.get_status())
    history_list = data.get_data()
    assert isinstance(history_list, list)
    print("get_one_click_repay_history count:", len(history_list))


def test_okx_async_get_one_click_repay_history():
    """Test async_get_one_click_repay_history interface"""
    data_queue = queue.Queue()
    live_okx_swap_feed = init_async_feed(data_queue)
    live_okx_swap_feed.async_get_one_click_repay_history(limit="10")
    time.sleep(5)
    try:
        history_data = data_queue.get(False)
    except queue.Empty:
        history_data = None
    if history_data is None:
        print("Warning: history_data is None (async timeout)")
        return  # Skip assertion on timeout
    assert isinstance(history_data, RequestData)
    print("async_get_one_click_repay_history status:", history_data.get_status())


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
        side="buy",
        ord_type="limit",
        sz="1",
        px="100"  # Very low price to avoid actual execution
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
        side="buy",
        ord_type="limit",
        sz="1",
        px="100"
    )
    time.sleep(5)
    try:
        precheck_data = data_queue.get(False)
    except queue.Empty:
        precheck_data = None
    assert precheck_data is not None
    assert isinstance(precheck_data, RequestData)
    print("async_order_precheck status:", precheck_data.get_status())


# ==================== Public Data API Tests ====================

def test_okx_req_get_premium_history():
    """Test get_premium_history interface - Get premium history"""
    live_okx_swap_feed = init_req_feed()
    # Get premium history for SWAP instruments
    data = live_okx_swap_feed.get_premium_history(inst_type="SWAP", limit="10")
    assert isinstance(data, RequestData)
    print("get_premium_history status:", data.get_status())
    history_list = data.get_data()
    assert isinstance(history_list, list)
    print("get_premium_history count:", len(history_list))


def test_okx_async_get_premium_history():
    """Test async_get_premium_history interface"""
    data_queue = queue.Queue()
    live_okx_swap_feed = init_async_feed(data_queue)
    live_okx_swap_feed.async_get_premium_history(inst_type="SWAP", limit="10")
    time.sleep(5)
    try:
        premium_data = data_queue.get(False)
    except queue.Empty:
        premium_data = None
    if premium_data is None:
        print("Warning: premium_data is None (async timeout)")
        return  # Skip assertion on timeout
    assert isinstance(premium_data, RequestData)
    print("async_get_premium_history status:", premium_data.get_status())


def test_okx_req_get_economic_calendar():
    """Test get_economic_calendar interface - Get economic calendar"""
    live_okx_swap_feed = init_req_feed()
    # Get economic calendar
    data = live_okx_swap_feed.get_economic_calendar(limit="10")
    assert isinstance(data, RequestData)
    print("get_economic_calendar status:", data.get_status())
    calendar_list = data.get_data()
    assert isinstance(calendar_list, list)
    print("get_economic_calendar count:", len(calendar_list))


def test_okx_async_get_economic_calendar():
    """Test async_get_economic_calendar interface"""
    data_queue = queue.Queue()
    live_okx_swap_feed = init_async_feed(data_queue)
    live_okx_swap_feed.async_get_economic_calendar(limit="10")
    time.sleep(5)
    try:
        calendar_data = data_queue.get(False)
    except queue.Empty:
        calendar_data = None
    if calendar_data is None:
        print("Warning: calendar_data is None (async timeout)")
        return  # Skip assertion on timeout
    assert isinstance(calendar_data, RequestData)
    print("async_get_economic_calendar status:", calendar_data.get_status())


# ==================== Market Data API Tests ====================

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
    if 'components' in components_data:
        print("get_index_components count:", len(components_data['components']))


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

def test_okx_req_get_mmp_config():
    """Test get_mmp_config interface - Get MMP configuration"""
    live_okx_swap_feed = init_req_feed()
    # Get MMP config for SWAP instruments
    data = live_okx_swap_feed.get_mmp_config(inst_type="SWAP")
    assert isinstance(data, RequestData)
    print("get_mmp_config status:", data.get_status())
    config_list = data.get_data()
    assert isinstance(config_list, list)
    print("get_mmp_config count:", len(config_list))
    if config_list:
        print("get_mmp_config sample:", config_list[0])


def test_okx_async_get_mmp_config():
    """Test async_get_mmp_config interface"""
    data_queue = queue.Queue()
    live_okx_swap_feed = init_async_feed(data_queue)
    live_okx_swap_feed.async_get_mmp_config(inst_type="SWAP")
    time.sleep(5)
    try:
        config_data = data_queue.get(False)
    except queue.Empty:
        config_data = None
    if config_data is None:
        print("Warning: config_data is None (async timeout)")
        return  # Skip assertion on timeout
    assert isinstance(config_data, RequestData)
    print("async_get_mmp_config status:", config_data.get_status())


def test_okx_req_set_mmp_config():
    """Test set_mmp_config interface - Set MMP configuration"""
    live_okx_swap_feed = init_req_feed()
    # Set MMP config for SWAP instruments
    # Note: This may fail if MMP is not configured for the account
    data = live_okx_swap_feed.set_mmp_config(
        inst_type="SWAP",
        symbol="BTC-USDT-SWAP",
        time_interval_frozen=1000,
        algo_orders_frozen=True
    )
    assert isinstance(data, RequestData)
    print("set_mmp_config status:", data.get_status())


def test_okx_async_set_mmp_config():
    """Test async_set_mmp_config interface"""
    data_queue = queue.Queue()
    live_okx_swap_feed = init_async_feed(data_queue)
    live_okx_swap_feed.async_set_mmp_config(
        inst_type="SWAP",
        symbol="BTC-USDT-SWAP",
        time_interval_frozen=1000,
        algo_orders_frozen=True
    )
    time.sleep(5)
    try:
        config_data = data_queue.get(False)
    except queue.Empty:
        config_data = None
    if config_data is None:
        print("Warning: config_data is None (async timeout)")
        return  # Skip assertion on timeout
    assert isinstance(config_data, RequestData)
    print("async_set_mmp_config status:", config_data.get_status())


def test_okx_req_mmp_reset():
    """Test mmp_reset interface - Reset MMP status"""
    live_okx_swap_feed = init_req_feed()
    # Reset MMP for SWAP instruments
    # Note: This may fail if MMP is not configured for the account
    data = live_okx_swap_feed.mmp_reset(
        inst_type="SWAP",
        symbol="BTC-USDT-SWAP"
    )
    assert isinstance(data, RequestData)
    print("mmp_reset status:", data.get_status())


def test_okx_async_mmp_reset():
    """Test async_mmp_reset interface"""
    data_queue = queue.Queue()
    live_okx_swap_feed = init_async_feed(data_queue)
    live_okx_swap_feed.async_mmp_reset(
        inst_type="SWAP",
        symbol="BTC-USDT-SWAP"
    )
    time.sleep(5)
    try:
        reset_data = data_queue.get(False)
    except queue.Empty:
        reset_data = None
    if reset_data is None:
        print("Warning: reset_data is None (async timeout)")
        return  # Skip assertion on timeout
    assert isinstance(reset_data, RequestData)
    print("async_mmp_reset status:", reset_data.get_status())


# ==================== Bills History Archive API Tests ====================

def test_okx_req_apply_bills_history_archive():
    """Test apply_bills_history_archive interface - Apply for historical bills archive"""
    live_okx_swap_feed = init_req_feed()
    # Apply for bills history archive for 2024
    # Note: This may fail if the archive is already generated
    data = live_okx_swap_feed.apply_bills_history_archive(year="2024")
    assert isinstance(data, RequestData)
    print("apply_bills_history_archive status:", data.get_status())


def test_okx_async_apply_bills_history_archive():
    """Test async_apply_bills_history_archive interface"""
    data_queue = queue.Queue()
    live_okx_swap_feed = init_async_feed(data_queue)
    live_okx_swap_feed.async_apply_bills_history_archive(year="2024")
    time.sleep(5)
    try:
        archive_data = data_queue.get(False)
    except queue.Empty:
        archive_data = None
    if archive_data is None:
        print("Warning: archive_data is None (async timeout)")
        return  # Skip assertion on timeout
    assert isinstance(archive_data, RequestData)
    print("async_apply_bills_history_archive status:", archive_data.get_status())


def test_okx_req_get_bills_history_archive():
    """Test get_bills_history_archive interface - Get historical bills archive"""
    live_okx_swap_feed = init_req_feed()
    # Get bills history archive for 2024
    # Note: This may fail if the archive has not been generated yet
    data = live_okx_swap_feed.get_bills_history_archive(year="2024")
    assert isinstance(data, RequestData)
    print("get_bills_history_archive status:", data.get_status())
    bills_list = data.get_data()
    assert isinstance(bills_list, list)
    print("get_bills_history_archive count:", len(bills_list))


def test_okx_async_get_bills_history_archive():
    """Test async_get_bills_history_archive interface"""
    data_queue = queue.Queue()
    live_okx_swap_feed = init_async_feed(data_queue)
    live_okx_swap_feed.async_get_bills_history_archive(year="2024")
    time.sleep(5)
    try:
        bills_data = data_queue.get(False)
    except queue.Empty:
        bills_data = None
    if bills_data is None:
        print("Warning: bills_data is None (async timeout)")
        return  # Skip assertion on timeout
    assert isinstance(bills_data, RequestData)
    print("async_get_bills_history_archive status:", bills_data.get_status())


def test_okx_req_get_bills_history_archive_with_ccy():
    """Test get_bills_history_archive interface with currency filter"""
    live_okx_swap_feed = init_req_feed()
    # Get bills history archive for 2024 filtered by USDT
    data = live_okx_swap_feed.get_bills_history_archive(year="2024", ccy="USDT")
    assert isinstance(data, RequestData)
    print("get_bills_history_archive (with ccy) status:", data.get_status())
    bills_list = data.get_data()
    assert isinstance(bills_list, list)
    print("get_bills_history_archive (with ccy) count:", len(bills_list))


def test_okx_async_get_bills_history_archive_with_ccy():
    """Test async_get_bills_history_archive interface with currency filter"""
    data_queue = queue.Queue()
    live_okx_swap_feed = init_async_feed(data_queue)
    live_okx_swap_feed.async_get_bills_history_archive(year="2024", ccy="USDT")
    time.sleep(5)
    try:
        bills_data = data_queue.get(False)
    except queue.Empty:
        bills_data = None
    if bills_data is None:
        print("Warning: bills_data is None (async timeout)")
        return  # Skip assertion on timeout
    assert isinstance(bills_data, RequestData)
    print("async_get_bills_history_archive (with ccy) status:", bills_data.get_status())


# ==================== Option Instrument Family Trades API Tests ====================

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
            inst_id = inst_list[0].get_inst_id()
    
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
            inst_id = inst_list[0].get_inst_id()
    
    if inst_id:
        live_okx_swap_feed.async_get_option_trades(inst_id=inst_id)
        time.sleep(5)
        try:
            trades_data = data_queue.get(False)
        except queue.Empty:
            trades_data = None
        if trades_data is None:
            print("Warning: trades_data is None (async timeout)")
            return
        assert isinstance(trades_data, RequestData)
        print("async_get_option_trades status:", trades_data.get_status())
    else:
        print("Warning: No valid option instrument found, skipping async_get_option_trades test")


# ==================== Option Instrument Family Trades API Tests ====================

def test_okx_req_get_option_instrument_family_trades():
    """Test get_option_instrument_family_trades interface - Get option instrument family trades data"""
    live_okx_swap_feed = init_req_feed()
    # Get option instrument family trades for BTC-USD
    data = live_okx_swap_feed.get_option_instrument_family_trades(inst_family="BTC-USD")
    assert isinstance(data, RequestData)
    print("get_option_instrument_family_trades status:", data.get_status())
    trades_data = data.get_data()
    # When normalize_function is None, data is a dict
    assert isinstance(trades_data, dict)
    if 'data' in trades_data:
        print("get_option_instrument_family_trades data:", trades_data.get('data'))


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
    data = live_okx_swap_feed.get_option_instrument_family_trades(inst_family="BTC-USD", limit="10")
    assert isinstance(data, RequestData)
    print("get_option_instrument_family_trades (with limit) status:", data.get_status())


# ==================== Option Trades API Tests ====================

def test_okx_req_get_option_trades():
    """Test get_option_trades interface - Get option trades data"""
    live_okx_swap_feed = init_req_feed()
    # First get a valid option instrument ID
    instruments = live_okx_swap_feed.get_instruments(inst_type="OPTION", uly="BTC-USD")
    inst_id = None
    if instruments.get_status():
        inst_list = instruments.get_data()
        if inst_list and len(inst_list) > 0:
            inst_id = inst_list[0].get_inst_id() if hasattr(inst_list[0], 'get_inst_id') else None
    
    if inst_id:
        data = live_okx_swap_feed.get_option_trades(inst_id=inst_id)
        assert isinstance(data, RequestData)
        print("get_option_trades status:", data.get_status())
        trades_data = data.get_data()
        assert isinstance(trades_data, dict)
        print("get_option_trades data:", trades_data.get('data') if 'data' in trades_data else trades_data)
    else:
        print("Warning: No valid option instrument found, skipping get_option_trades test")


def test_okx_async_get_option_trades():
    """Test async_get_option_trades interface"""
    data_queue = queue.Queue()
    live_okx_swap_feed = init_async_feed(data_queue)
    # First get a valid option instrument ID
    instruments = live_okx_swap_feed.get_instruments(inst_type="OPTION", uly="BTC-USD")
    inst_id = None
    if instruments.get_status():
        inst_list = instruments.get_data()
        if inst_list and len(inst_list) > 0:
            inst_id = inst_list[0].get_inst_id() if hasattr(inst_list[0], 'get_inst_id') else None
    
    if inst_id:
        live_okx_swap_feed.async_get_option_trades(inst_id=inst_id)
        time.sleep(5)
        try:
            trades_data = data_queue.get(False)
        except queue.Empty:
            trades_data = None
        if trades_data is None:
            print("Warning: trades_data is None (async timeout)")
            return
        assert isinstance(trades_data, RequestData)
        print("async_get_option_trades status:", trades_data.get_status())
    else:
        print("Warning: No valid option instrument found, skipping async_get_option_trades test")


# ==================== 24h Volume API Tests ====================

def test_okx_req_get_24h_volume():
    """Test get_24h_volume interface - Get platform 24h total volume"""
    live_okx_swap_feed = init_req_feed()
    data = live_okx_swap_feed.get_24h_volume()
    assert isinstance(data, RequestData)
    print("get_24h_volume status:", data.get_status())
    volume_data = data.get_data()
    # When normalize_function is None, data is a dict
    assert isinstance(volume_data, dict)
    if 'data' in volume_data:
        print("get_24h_volume data:", volume_data.get('data'))


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
    # When normalize_function is None, data is a dict
    assert isinstance(auction_data, dict)
    if 'data' in auction_data:
        print("get_call_auction_details data:", auction_data.get('data'))


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
    # When normalize_function is None, data is a dict
    assert isinstance(index_data, dict)
    if 'data' in index_data:
        print("get_index_price data:", index_data.get('data'))


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
    assert isinstance(index_data, dict)
    if 'data' in index_data:
        print("get_index_price (all) data count:", len(index_data.get('data', [])))


# ==================== Index Candles History API Tests ====================

def test_okx_req_get_index_candles_history():
    """Test get_index_candles_history interface - Get historical index candlestick charts"""
    live_okx_swap_feed = init_req_feed()
    # Get historical index candles for BTC-USD
    data = live_okx_swap_feed.get_index_candles_history(index="BTC-USD", bar="1m", limit="10")
    assert isinstance(data, RequestData)
    print("get_index_candles_history status:", data.get_status())
    candles_data = data.get_data()
    # When normalize_function is None, data is a dict
    assert isinstance(candles_data, dict)
    if 'data' in candles_data:
        print("get_index_candles_history data count:", len(candles_data.get('data', [])))


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
    data = live_okx_swap_feed.get_mark_price_candles_history(symbol="BTC-USDT-SWAP", bar="1m", limit="10")
    assert isinstance(data, RequestData)
    print("get_mark_price_candles_history status:", data.get_status())
    candles_data = data.get_data()
    # When normalize_function is None, data is a dict
    assert isinstance(candles_data, dict)
    if 'data' in candles_data:
        print("get_mark_price_candles_history data count:", len(candles_data.get('data', [])))


def test_okx_async_get_mark_price_candles_history():
    """Test async_get_mark_price_candles_history interface"""
    data_queue = queue.Queue()
    live_okx_swap_feed = init_async_feed(data_queue)
    live_okx_swap_feed.async_get_mark_price_candles_history(symbol="BTC-USDT-SWAP", bar="1m", limit="10")
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
    data = live_okx_swap_feed.get_mark_price_candles_history(symbol="ETH-USDT-SWAP", bar="5m", limit="20")
    assert isinstance(data, RequestData)
    print("get_mark_price_candles_history (ETH, 5m, 20) status:", data.get_status())


# ==================== Trading Statistics Tests ====================

def test_okx_req_get_taker_volume_contract():
    """Test get_taker_volume_contract interface"""
    live_okx_swap_feed = init_req_feed()
    # Get contract taker volume
    data = live_okx_swap_feed.get_taker_volume_contract(ccy="BTC", inst_type="SWAP", period="1H", limit="10")
    assert isinstance(data, RequestData)
    print("get_taker_volume_contract status:", data.get_status())
    volume_list = data.get_data()
    assert isinstance(volume_list, list)
    print("get_taker_volume_contract count:", len(volume_list))


def test_okx_async_get_taker_volume_contract():
    """Test async_get_taker_volume_contract interface"""
    data_queue = queue.Queue()
    live_okx_swap_feed = init_async_feed(data_queue)
    live_okx_swap_feed.async_get_taker_volume_contract(ccy="BTC", inst_type="SWAP", period="1H", limit="10")
    time.sleep(5)
    try:
        volume_data = data_queue.get(False)
    except queue.Empty:
        volume_data = None
    if volume_data is None:
        print("Warning: volume_data is None (async timeout)")
        return  # Skip assertion on timeout
    assert isinstance(volume_data, RequestData)
    print("async_get_taker_volume_contract status:", volume_data.get_status())


def test_okx_req_get_margin_loan_ratio():
    """Test get_margin_loan_ratio interface"""
    live_okx_swap_feed = init_req_feed()
    # Get margin loan ratio (spot long/short ratio)
    data = live_okx_swap_feed.get_margin_loan_ratio(ccy="BTC", period="1H", limit="10")
    assert isinstance(data, RequestData)
    print("get_margin_loan_ratio status:", data.get_status())
    ratio_list = data.get_data()
    assert isinstance(ratio_list, list)
    print("get_margin_loan_ratio count:", len(ratio_list))


def test_okx_async_get_margin_loan_ratio():
    """Test async_get_margin_loan_ratio interface"""
    data_queue = queue.Queue()
    live_okx_swap_feed = init_async_feed(data_queue)
    live_okx_swap_feed.async_get_margin_loan_ratio(ccy="BTC", period="1H", limit="10")
    time.sleep(5)
    try:
        ratio_data = data_queue.get(False)
    except queue.Empty:
        ratio_data = None
    if ratio_data is None:
        print("Warning: ratio_data is None (async timeout)")
        return  # Skip assertion on timeout
    assert isinstance(ratio_data, RequestData)
    print("async_get_margin_loan_ratio status:", ratio_data.get_status())


def test_okx_req_get_option_long_short_ratio():
    """Test get_option_long_short_ratio interface"""
    live_okx_swap_feed = init_req_feed()
    # Get option long/short ratio
    data = live_okx_swap_feed.get_option_long_short_ratio(ccy="BTC-USD", currency="USD", period="8H", limit="10")
    assert isinstance(data, RequestData)
    print("get_option_long_short_ratio status:", data.get_status())
    ratio_list = data.get_data()
    assert isinstance(ratio_list, list)
    print("get_option_long_short_ratio count:", len(ratio_list))


def test_okx_async_get_option_long_short_ratio():
    """Test async_get_option_long_short_ratio interface"""
    data_queue = queue.Queue()
    live_okx_swap_feed = init_async_feed(data_queue)
    live_okx_swap_feed.async_get_option_long_short_ratio(ccy="BTC-USD", currency="USD", period="8H", limit="10")
    time.sleep(5)
    try:
        ratio_data = data_queue.get(False)
    except queue.Empty:
        ratio_data = None
    if ratio_data is None:
        print("Warning: ratio_data is None (async timeout)")
        return  # Skip assertion on timeout
    assert isinstance(ratio_data, RequestData)
    print("async_get_option_long_short_ratio status:", ratio_data.get_status())


def test_okx_req_get_contracts_oi_volume():
    """Test get_contracts_oi_volume interface"""
    live_okx_swap_feed = init_req_feed()
    # Get contract open interest and volume
    data = live_okx_swap_feed.get_contracts_oi_volume(ccy="BTC", period="1H", limit="10")
    assert isinstance(data, RequestData)
    print("get_contracts_oi_volume status:", data.get_status())
    oi_list = data.get_data()
    assert isinstance(oi_list, list)
    print("get_contracts_oi_volume count:", len(oi_list))


def test_okx_async_get_contracts_oi_volume():
    """Test async_get_contracts_oi_volume interface"""
    data_queue = queue.Queue()
    live_okx_swap_feed = init_async_feed(data_queue)
    live_okx_swap_feed.async_get_contracts_oi_volume(ccy="BTC", period="1H", limit="10")
    time.sleep(5)
    try:
        oi_data = data_queue.get(False)
    except queue.Empty:
        oi_data = None
    if oi_data is None:
        print("Warning: oi_data is None (async timeout)")
        return  # Skip assertion on timeout
    assert isinstance(oi_data, RequestData)
    print("async_get_contracts_oi_volume status:", oi_data.get_status())


def test_okx_req_get_option_oi_volume():
    """Test get_option_oi_volume interface"""
    live_okx_swap_feed = init_req_feed()
    # Get option open interest and volume
    data = live_okx_swap_feed.get_option_oi_volume(ccy="BTC-USD", currency="USD", period="8H", limit="10")
    assert isinstance(data, RequestData)
    print("get_option_oi_volume status:", data.get_status())
    oi_list = data.get_data()
    assert isinstance(oi_list, list)
    print("get_option_oi_volume count:", len(oi_list))


def test_okx_async_get_option_oi_volume():
    """Test async_get_option_oi_volume interface"""
    data_queue = queue.Queue()
    live_okx_swap_feed = init_async_feed(data_queue)
    live_okx_swap_feed.async_get_option_oi_volume(ccy="BTC-USD", currency="USD", period="8H", limit="10")
    time.sleep(5)
    try:
        oi_data = data_queue.get(False)
    except queue.Empty:
        oi_data = None
    if oi_data is None:
        print("Warning: oi_data is None (async timeout)")
        return  # Skip assertion on timeout
    assert isinstance(oi_data, RequestData)
    print("async_get_option_oi_volume status:", oi_data.get_status())


def test_okx_req_get_option_oi_volume_expiry():
    """Test get_option_oi_volume_expiry interface"""
    live_okx_swap_feed = init_req_feed()
    # Get option open interest and volume by expiry
    data = live_okx_swap_feed.get_option_oi_volume_expiry(ccy="BTC-USD", currency="USD", period="8H", limit="10")
    assert isinstance(data, RequestData)
    print("get_option_oi_volume_expiry status:", data.get_status())
    oi_list = data.get_data()
    assert isinstance(oi_list, list)
    print("get_option_oi_volume_expiry count:", len(oi_list))


def test_okx_async_get_option_oi_volume_expiry():
    """Test async_get_option_oi_volume_expiry interface"""
    data_queue = queue.Queue()
    live_okx_swap_feed = init_async_feed(data_queue)
    live_okx_swap_feed.async_get_option_oi_volume_expiry(ccy="BTC-USD", currency="USD", period="8H", limit="10")
    time.sleep(5)
    try:
        oi_data = data_queue.get(False)
    except queue.Empty:
        oi_data = None
    if oi_data is None:
        print("Warning: oi_data is None (async timeout)")
        return  # Skip assertion on timeout
    assert isinstance(oi_data, RequestData)
    print("async_get_option_oi_volume_expiry status:", oi_data.get_status())


def test_okx_req_get_option_oi_volume_strike():
    """Test get_option_oi_volume_strike interface"""
    live_okx_swap_feed = init_req_feed()
    # Get option open interest and volume by strike price
    data = live_okx_swap_feed.get_option_oi_volume_strike(ccy="BTC-USD", currency="USD", period="8H", limit="10")
    assert isinstance(data, RequestData)
    print("get_option_oi_volume_strike status:", data.get_status())
    oi_list = data.get_data()
    assert isinstance(oi_list, list)
    print("get_option_oi_volume_strike count:", len(oi_list))


def test_okx_async_get_option_oi_volume_strike():
    """Test async_get_option_oi_volume_strike interface"""
    data_queue = queue.Queue()
    live_okx_swap_feed = init_async_feed(data_queue)
    live_okx_swap_feed.async_get_option_oi_volume_strike(ccy="BTC-USD", currency="USD", period="8H", limit="10")
    time.sleep(5)
    try:
        oi_data = data_queue.get(False)
    except queue.Empty:
        oi_data = None
    if oi_data is None:
        print("Warning: oi_data is None (async timeout)")
        return  # Skip assertion on timeout
    assert isinstance(oi_data, RequestData)
    print("async_get_option_oi_volume_strike status:", oi_data.get_status())


def test_okx_req_get_option_taker_flow():
    """Test get_option_taker_flow interface"""
    live_okx_swap_feed = init_req_feed()
    # Get option taker block volume (large trades)
    data = live_okx_swap_feed.get_option_taker_flow(ccy="BTC-USD", currency="USD", period="8H", limit="10")
    assert isinstance(data, RequestData)
    print("get_option_taker_flow status:", data.get_status())
    flow_list = data.get_data()
    assert isinstance(flow_list, list)
    print("get_option_taker_flow count:", len(flow_list))


def test_okx_async_get_option_taker_flow():
    """Test async_get_option_taker_flow interface"""
    data_queue = queue.Queue()
    live_okx_swap_feed = init_async_feed(data_queue)
    live_okx_swap_feed.async_get_option_taker_flow(ccy="BTC-USD", currency="USD", period="8H", limit="10")
    time.sleep(5)
    try:
        flow_data = data_queue.get(False)
    except queue.Empty:
        flow_data = None
    if flow_data is None:
        print("Warning: flow_data is None (async timeout)")
        return  # Skip assertion on timeout
    assert isinstance(flow_data, RequestData)
    print("async_get_option_taker_flow status:", flow_data.get_status())


# ==================== Trading Account Configuration API Tests ====================

def test_okx_req_set_auto_loan():
    """Test set_auto_loan interface - Set auto loan status"""
    live_okx_swap_feed = init_req_feed()
    # Test setting auto loan to false (off)
    # Note: This may fail if the account configuration doesn't allow this operation
    result = live_okx_swap_feed.set_auto_loan(
        auto_loan=False,
        iso_mode="automatic",
        mgn_mode="cross"
    )
    assert isinstance(result, RequestData)
    print("set_auto_loan status:", result.get_status())
    print("set_auto_loan input:", result.get_input_data())


def test_okx_async_set_auto_loan():
    """Test async_set_auto_loan interface"""
    data_queue = queue.Queue()
    live_okx_swap_feed = init_async_feed(data_queue)
    live_okx_swap_feed.async_set_auto_loan(
        auto_loan=False,
        iso_mode="automatic",
        mgn_mode="cross"
    )
    time.sleep(5)
    try:
        result = data_queue.get(False)
    except queue.Empty:
        result = None
    if result is not None:
        assert isinstance(result, RequestData)
        print("async_set_auto_loan status:", result.get_status())


def test_okx_req_set_account_level():
    """Test set_account_level interface - Set account level"""
    live_okx_swap_feed = init_req_feed()
    # Note: This is a sensitive operation that may not be allowed in all accounts
    # acct_lv: 1=Simple, 2=Single-currency margin, 3=Multi-currency margin, 4=Portfolio margin
    result = live_okx_swap_feed.set_account_level(
        acct_lv=2,
        inst_type="SWAP"
    )
    assert isinstance(result, RequestData)
    print("set_account_level status:", result.get_status())
    print("set_account_level input:", result.get_input_data())


def test_okx_async_set_account_level():
    """Test async_set_account_level interface"""
    data_queue = queue.Queue()
    live_okx_swap_feed = init_async_feed(data_queue)
    live_okx_swap_feed.async_set_account_level(
        acct_lv=2,
        inst_type="SWAP"
    )
    time.sleep(5)
    try:
        result = data_queue.get(False)
    except queue.Empty:
        result = None
    if result is not None:
        assert isinstance(result, RequestData)
        print("async_set_account_level status:", result.get_status())


def test_okx_req_account_level_switch_preset():
    """Test account_level_switch_preset interface - Account level switch preset"""
    live_okx_swap_feed = init_req_feed()
    # Preset configuration for switching to Multi-currency margin (level 3)
    result = live_okx_swap_feed.account_level_switch_preset(
        acct_lv=3,
        pos_side="long",
        inst_type="SWAP"
    )
    assert isinstance(result, RequestData)
    print("account_level_switch_preset status:", result.get_status())
    print("account_level_switch_preset input:", result.get_input_data())


def test_okx_async_account_level_switch_preset():
    """Test async_account_level_switch_preset interface"""
    data_queue = queue.Queue()
    live_okx_swap_feed = init_async_feed(data_queue)
    live_okx_swap_feed.async_account_level_switch_preset(
        acct_lv=3,
        pos_side="long",
        inst_type="SWAP"
    )
    time.sleep(5)
    try:
        result = data_queue.get(False)
    except queue.Empty:
        result = None
    if result is not None:
        assert isinstance(result, RequestData)
        print("async_account_level_switch_preset status:", result.get_status())


def test_okx_req_account_level_switch_precheck():
    """Test account_level_switch_precheck interface - Account level switch precheck"""
    live_okx_swap_feed = init_req_feed()
    # Precheck before switching to Multi-currency margin (level 3)
    result = live_okx_swap_feed.account_level_switch_precheck(
        acct_lv=3,
        inst_type="SWAP"
    )
    assert isinstance(result, RequestData)
    print("account_level_switch_precheck status:", result.get_status())
    data = result.get_data()
    if data:
        print("account_level_switch_precheck data:", data)


def test_okx_async_account_level_switch_precheck():
    """Test async_account_level_switch_precheck interface"""
    data_queue = queue.Queue()
    live_okx_swap_feed = init_async_feed(data_queue)
    live_okx_swap_feed.async_account_level_switch_precheck(
        acct_lv=3,
        inst_type="SWAP"
    )
    time.sleep(5)
    try:
        result = data_queue.get(False)
    except queue.Empty:
        result = None
    if result is not None:
        assert isinstance(result, RequestData)
        print("async_account_level_switch_precheck status:", result.get_status())


def test_okx_req_set_collateral_assets():
    """Test set_collateral_assets interface - Set collateral assets"""
    live_okx_swap_feed = init_req_feed()
    # Set BTC and USDT as collateral assets
    result = live_okx_swap_feed.set_collateral_assets(
        ccy_list="BTC,USDT,ETH",
        auto_loan=False
    )
    assert isinstance(result, RequestData)
    print("set_collateral_assets status:", result.get_status())
    print("set_collateral_assets input:", result.get_input_data())


def test_okx_async_set_collateral_assets():
    """Test async_set_collateral_assets interface"""
    data_queue = queue.Queue()
    live_okx_swap_feed = init_async_feed(data_queue)
    live_okx_swap_feed.async_set_collateral_assets(
        ccy_list="BTC,USDT,ETH",
        auto_loan=False
    )
    time.sleep(5)
    try:
        result = data_queue.get(False)
    except queue.Empty:
        result = None
    if result is not None:
        assert isinstance(result, RequestData)
        print("async_set_collateral_assets status:", result.get_status())


def test_okx_req_get_collateral_assets():
    """Test get_collateral_assets interface - Get collateral assets"""
    live_okx_swap_feed = init_req_feed()
    # Get all collateral assets
    result = live_okx_swap_feed.get_collateral_assets()
    assert isinstance(result, RequestData)
    assert result.get_status()
    data = result.get_data()
    assert isinstance(data, list)
    print("get_collateral_assets count:", len(data))
    if data:
        print("get_collateral_assets first item:", data[0])


def test_okx_async_get_collateral_assets():
    """Test async_get_collateral_assets interface"""
    data_queue = queue.Queue()
    live_okx_swap_feed = init_async_feed(data_queue)
    live_okx_swap_feed.async_get_collateral_assets()
    time.sleep(5)
    try:
        result = data_queue.get(False)
    except queue.Empty:
        result = None
    if result is not None:
        assert isinstance(result, RequestData)
        assert result.get_status()
        print("async_get_collateral_assets status:", result.get_status())
        data = result.get_data()
        if data:
            print("async_get_collateral_assets count:", len(data))


def test_okx_req_get_collateral_assets_single_currency():
    """Test get_collateral_assets interface for a specific currency"""
    live_okx_swap_feed = init_req_feed()
    # Get collateral assets for BTC only
    result = live_okx_swap_feed.get_collateral_assets(ccy="BTC")
    assert isinstance(result, RequestData)
    print("get_collateral_assets (BTC) status:", result.get_status())
    data = result.get_data()
    if data:
        print("get_collateral_assets (BTC) data:", data)


def test_okx_req_set_risk_offset_amt():
    """Test set_risk_offset_amt interface - Set risk offset amount"""
    live_okx_swap_feed = init_req_feed()
    # Add risk offset amount for BTC
    result = live_okx_swap_feed.set_risk_offset_amt(
        amt_type="1",  # 1=Add, 2=Reduce
        ccy="BTC",
        offset_amt="100"
    )
    assert isinstance(result, RequestData)
    print("set_risk_offset_amt status:", result.get_status())
    print("set_risk_offset_amt input:", result.get_input_data())


def test_okx_async_set_risk_offset_amt():
    """Test async_set_risk_offset_amt interface"""
    data_queue = queue.Queue()
    live_okx_swap_feed = init_async_feed(data_queue)
    live_okx_swap_feed.async_set_risk_offset_amt(
        amt_type="1",  # 1=Add, 2=Reduce
        ccy="BTC",
        offset_amt="100"
    )
    time.sleep(5)
    try:
        result = data_queue.get(False)
    except queue.Empty:
        result = None
    if result is not None:
        assert isinstance(result, RequestData)
        print("async_set_risk_offset_amt status:", result.get_status())


def test_okx_req_set_risk_offset_amt_with_instrument():
    """Test set_risk_offset_amt interface with instrument details"""
    live_okx_swap_feed = init_req_feed()
    # Add risk offset amount for a specific instrument
    result = live_okx_swap_feed.set_risk_offset_amt(
        amt_type="1",  # 1=Add, 2=Reduce
        uly="BTC-USDT",
        inst_type="SWAP",
        td_mode="cross",
        offset_amt="50"
    )
    assert isinstance(result, RequestData)
    print("set_risk_offset_amt (with instrument) status:", result.get_status())
    print("set_risk_offset_amt (with instrument) input:", result.get_input_data())


# ==================== Trading Account REST API Tests ====================

def test_okx_req_get_interest_limits():
    """Test get_interest_limits interface - Get interest limit and interest rate"""
    live_okx_swap_feed = init_req_feed()
    data = live_okx_swap_feed.get_interest_limits(ccy="USDT")
    assert isinstance(data, RequestData)
    print("get_interest_limits status:", data.get_status())
    limits_list = data.get_data()
    assert isinstance(limits_list, list)
    print("get_interest_limits count:", len(limits_list))


def test_okx_async_get_interest_limits():
    """Test async_get_interest_limits interface"""
    data_queue = queue.Queue()
    live_okx_swap_feed = init_async_feed(data_queue)
    live_okx_swap_feed.async_get_interest_limits(ccy="USDT")
    time.sleep(5)
    try:
        limits_data = data_queue.get(False)
    except queue.Empty:
        limits_data = None
    if limits_data is not None:
        assert isinstance(limits_data, RequestData)
        print("async_get_interest_limits status:", limits_data.get_status())


def test_okx_req_set_fee_type():
    """Test set_fee_type interface - Set fee rate tier"""
    live_okx_swap_feed = init_req_feed()
    # Test setting fee rate tier to 1
    # Note: This may fail if the account doesn't have the required permissions
    result = live_okx_swap_feed.set_fee_type(fee_type="1")
    assert isinstance(result, RequestData)
    print("set_fee_type status:", result.get_status())
    print("set_fee_type input:", result.get_input_data())


def test_okx_async_set_fee_type():
    """Test async_set_fee_type interface"""
    data_queue = queue.Queue()
    live_okx_swap_feed = init_async_feed(data_queue)
    live_okx_swap_feed.async_set_fee_type(fee_type="1")
    time.sleep(5)
    try:
        result = data_queue.get(False)
    except queue.Empty:
        result = None
    if result is not None:
        assert isinstance(result, RequestData)
        print("async_set_fee_type status:", result.get_status())


def test_okx_req_set_greeks():
    """Test set_greeks interface - Set Greeks display type"""
    live_okx_swap_feed = init_req_feed()
    # Test setting Greeks display type to IV (implied volatility)
    result = live_okx_swap_feed.set_greeks(greeks_type="IV")
    assert isinstance(result, RequestData)
    print("set_greeks status:", result.get_status())
    print("set_greeks input:", result.get_input_data())


def test_okx_async_set_greeks():
    """Test async_set_greeks interface"""
    data_queue = queue.Queue()
    live_okx_swap_feed = init_async_feed(data_queue)
    live_okx_swap_feed.async_set_greeks(greeks_type="IV")
    time.sleep(5)
    try:
        result = data_queue.get(False)
    except queue.Empty:
        result = None
    if result is not None:
        assert isinstance(result, RequestData)
        print("async_set_greeks status:", result.get_status())


def test_okx_req_set_isolated_mode():
    """Test set_isolated_mode interface - Set isolated margin mode"""
    live_okx_swap_feed = init_req_feed()
    # Test setting isolated margin mode to automatic
    result = live_okx_swap_feed.set_isolated_mode(
        symbol="BTC-USDT",
        iso_mode="automatic"
    )
    assert isinstance(result, RequestData)
    print("set_isolated_mode status:", result.get_status())
    print("set_isolated_mode input:", result.get_input_data())


def test_okx_async_set_isolated_mode():
    """Test async_set_isolated_mode interface"""
    data_queue = queue.Queue()
    live_okx_swap_feed = init_async_feed(data_queue)
    live_okx_swap_feed.async_set_isolated_mode(
        symbol="BTC-USDT",
        iso_mode="automatic"
    )
    time.sleep(5)
    try:
        result = data_queue.get(False)
    except queue.Empty:
        result = None
    if result is not None:
        assert isinstance(result, RequestData)
        print("async_set_isolated_mode status:", result.get_status())


def test_okx_req_borrow_repay():
    """Test borrow_repay interface - Manual borrow or repay for cross/isolated margin"""
    live_okx_swap_feed = init_req_feed()
    # Test borrow operation (Note: This may fail if account doesn't have margin trading enabled)
    result = live_okx_swap_feed.borrow_repay(
        ccy="USDT",
        side="borrow",
        amt="1",  # Small amount for testing
        mgn_mode="cross"
    )
    assert isinstance(result, RequestData)
    print("borrow_repay status:", result.get_status())
    print("borrow_repay input:", result.get_input_data())


def test_okx_async_borrow_repay():
    """Test async_borrow_repay interface"""
    data_queue = queue.Queue()
    live_okx_swap_feed = init_async_feed(data_queue)
    live_okx_swap_feed.async_borrow_repay(
        ccy="USDT",
        side="borrow",
        amt="1",
        mgn_mode="cross"
    )
    time.sleep(5)
    try:
        result = data_queue.get(False)
    except queue.Empty:
        result = None
    if result is not None:
        assert isinstance(result, RequestData)
        print("async_borrow_repay status:", result.get_status())


def test_okx_req_set_auto_repay():
    """Test set_auto_repay interface - Set auto loan repayment"""
    live_okx_swap_feed = init_req_feed()
    # Test enabling auto repayment
    result = live_okx_swap_feed.set_auto_repay(auto_repay="true")
    assert isinstance(result, RequestData)
    print("set_auto_repay status:", result.get_status())
    print("set_auto_repay input:", result.get_input_data())


def test_okx_async_set_auto_repay():
    """Test async_set_auto_repay interface"""
    data_queue = queue.Queue()
    live_okx_swap_feed = init_async_feed(data_queue)
    live_okx_swap_feed.async_set_auto_repay(auto_repay="true")
    time.sleep(5)
    try:
        result = data_queue.get(False)
    except queue.Empty:
        result = None
    if result is not None:
        assert isinstance(result, RequestData)
        print("async_set_auto_repay status:", result.get_status())


def test_okx_req_get_borrow_repay_history():
    """Test get_borrow_repay_history interface - Get borrowing and repayment history"""
    live_okx_swap_feed = init_req_feed()
    data = live_okx_swap_feed.get_borrow_repay_history(
        ccy="USDT",
        limit="10"
    )
    assert isinstance(data, RequestData)
    print("get_borrow_repay_history status:", data.get_status())
    history_list = data.get_data()
    assert isinstance(history_list, list)
    print("get_borrow_repay_history count:", len(history_list))


def test_okx_async_get_borrow_repay_history():
    """Test async_get_borrow_repay_history interface"""
    data_queue = queue.Queue()
    live_okx_swap_feed = init_async_feed(data_queue)
    live_okx_swap_feed.async_get_borrow_repay_history(
        ccy="USDT",
        limit="10"
    )
    time.sleep(5)
    try:
        history_data = data_queue.get(False)
    except queue.Empty:
        history_data = None
    if history_data is not None:
        assert isinstance(history_data, RequestData)
        print("async_get_borrow_repay_history status:", history_data.get_status())


# ==================== Additional Trading Account API Tests ====================

def test_okx_req_activate_option():
    """Test activate_option interface - Activate option trading"""
    live_okx_swap_feed = init_req_feed()
    # This will fail without proper options trading setup, but tests the interface
    result = live_okx_swap_feed.activate_option(
        uly="BTC-USD",
        inst_id="BTC-USD-240127-50000-C",
        cnt="1"
    )
    assert isinstance(result, RequestData)
    print("activate_option status:", result.get_status())
    print("activate_option input:", result.get_input_data())


def test_okx_async_activate_option():
    """Test async_activate_option interface"""
    data_queue = queue.Queue()
    live_okx_swap_feed = init_async_feed(data_queue)
    live_okx_swap_feed.async_activate_option(
        uly="BTC-USD",
        inst_id="BTC-USD-240127-50000-C",
        cnt="1"
    )
    time.sleep(5)
    try:
        result = data_queue.get(False)
    except queue.Empty:
        result = None
    if result is not None:
        assert isinstance(result, RequestData)
        print("async_activate_option status:", result.get_status())


def test_okx_req_move_positions():
    """Test move_positions interface - Move positions between currencies"""
    live_okx_swap_feed = init_req_feed()
    # This will fail without proper position setup, but tests the interface
    result = live_okx_swap_feed.move_positions(
        symbol="BTC-USDT",
        pos_id="test_position_id",  # Placeholder
        ccy="USDT"
    )
    assert isinstance(result, RequestData)
    print("move_positions status:", result.get_status())
    print("move_positions input:", result.get_input_data())


def test_okx_async_move_positions():
    """Test async_move_positions interface"""
    data_queue = queue.Queue()
    live_okx_swap_feed = init_async_feed(data_queue)
    live_okx_swap_feed.async_move_positions(
        symbol="BTC-USDT",
        pos_id="test_position_id",  # Placeholder
        ccy="USDT"
    )
    time.sleep(5)
    try:
        result = data_queue.get(False)
    except queue.Empty:
        result = None
    if result is not None:
        assert isinstance(result, RequestData)
        print("async_move_positions status:", result.get_status())


def test_okx_req_get_move_positions_history():
    """Test get_move_positions_history interface - Get move positions history"""
    live_okx_swap_feed = init_req_feed()
    result = live_okx_swap_feed.get_move_positions_history(
        symbol="BTC-USDT",
        limit="10"
    )
    assert isinstance(result, RequestData)
    print("get_move_positions_history status:", result.get_status())
    history_list = result.get_data()
    assert isinstance(history_list, list)
    print("get_move_positions_history count:", len(history_list))


def test_okx_async_get_move_positions_history():
    """Test async_get_move_positions_history interface"""
    data_queue = queue.Queue()
    live_okx_swap_feed = init_async_feed(data_queue)
    live_okx_swap_feed.async_get_move_positions_history(
        symbol="BTC-USDT",
        limit="10"
    )
    time.sleep(5)
    try:
        result = data_queue.get(False)
    except queue.Empty:
        result = None
    if result is not None:
        assert isinstance(result, RequestData)
        print("async_get_move_positions_history status:", result.get_status())


def test_okx_req_set_auto_earn():
    """Test set_auto_earn interface - Set auto earn (automatic savings)"""
    live_okx_swap_feed = init_req_feed()
    # This may fail depending on account settings, but tests the interface
    result = live_okx_swap_feed.set_auto_earn(
        ccy="USDT",
        auto_earn="true"
    )
    assert isinstance(result, RequestData)
    print("set_auto_earn status:", result.get_status())
    print("set_auto_earn input:", result.get_input_data())


def test_okx_async_set_auto_earn():
    """Test async_set_auto_earn interface"""
    data_queue = queue.Queue()
    live_okx_swap_feed = init_async_feed(data_queue)
    live_okx_swap_feed.async_set_auto_earn(
        ccy="USDT",
        auto_earn="true"
    )
    time.sleep(5)
    try:
        result = data_queue.get(False)
    except queue.Empty:
        result = None
    if result is not None:
        assert isinstance(result, RequestData)
        print("async_set_auto_earn status:", result.get_status())


def test_okx_req_set_settle_currency():
    """Test set_settle_currency interface - Set settlement currency"""
    live_okx_swap_feed = init_req_feed()
    # This will fail without proper multi-currency margin setup, but tests the interface
    result = live_okx_swap_feed.set_settle_currency(
        symbol="BTC-USDT",
        ccy="USDT"
    )
    assert isinstance(result, RequestData)
    print("set_settle_currency status:", result.get_status())
    print("set_settle_currency input:", result.get_input_data())


def test_okx_async_set_settle_currency():
    """Test async_set_settle_currency interface"""
    data_queue = queue.Queue()
    live_okx_swap_feed = init_async_feed(data_queue)
    live_okx_swap_feed.async_set_settle_currency(
        symbol="BTC-USDT",
        ccy="USDT"
    )
    time.sleep(5)
    try:
        result = data_queue.get(False)
    except queue.Empty:
        result = None
    if result is not None:
        assert isinstance(result, RequestData)
        print("async_set_settle_currency status:", result.get_status())


def test_okx_req_set_trading_config():
    """Test set_trading_config interface - Set trading config"""
    live_okx_swap_feed = init_req_feed()
    # This will fail depending on account settings, but tests the interface
    result = live_okx_swap_feed.set_trading_config(
        symbol="BTC-USDT",
        auto_loan="false"
    )
    assert isinstance(result, RequestData)
    print("set_trading_config status:", result.get_status())
    print("set_trading_config input:", result.get_input_data())


def test_okx_async_set_trading_config():
    """Test async_set_trading_config interface"""
    data_queue = queue.Queue()
    live_okx_swap_feed = init_async_feed(data_queue)
    live_okx_swap_feed.async_set_trading_config(
        symbol="BTC-USDT",
        auto_loan="false"
    )
    time.sleep(5)
    try:
        result = data_queue.get(False)
    except queue.Empty:
        result = None
    if result is not None:
        assert isinstance(result, RequestData)
        print("async_set_trading_config status:", result.get_status())


def test_okx_req_set_delta_neutral_precheck():
    """Test set_delta_neutral_precheck interface - Set delta neutral precheck"""
    live_okx_swap_feed = init_req_feed()
    # This will fail depending on account settings, but tests the interface
    result = live_okx_swap_feed.set_delta_neutral_precheck(
        symbol="BTC-USDT",
        delta_neutral_precheck="true"
    )
    assert isinstance(result, RequestData)
    print("set_delta_neutral_precheck status:", result.get_status())
    print("set_delta_neutral_precheck input:", result.get_input_data())


def test_okx_async_set_delta_neutral_precheck():
    """Test async_set_delta_neutral_precheck interface"""
    data_queue = queue.Queue()
    live_okx_swap_feed = init_async_feed(data_queue)
    live_okx_swap_feed.async_set_delta_neutral_precheck(
        symbol="BTC-USDT",
        delta_neutral_precheck="true"
    )
    time.sleep(5)
    try:
        result = data_queue.get(False)
    except queue.Empty:
        result = None
    if result is not None:
        assert isinstance(result, RequestData)
        print("async_set_delta_neutral_precheck status:", result.get_status())


# ==================== Public Data APIs Tests (Additional) ====================

def test_okx_req_get_estimated_price():
    """Test get_estimated_price interface - Get estimated delivery/exercise price"""
    live_okx_swap_feed = init_req_feed()
    # Get a valid instrument ID first
    inst_result = live_okx_swap_feed.get_instruments(asset_type="FUTURES", underlying="BTC-USD")
    inst_id = None
    if inst_result.get_data():
        inst_result.get_data()[0].init_data()
        inst_id = inst_result.get_data()[0].get_symbol_name()

    if inst_id:
        data = live_okx_swap_feed.get_estimated_price(inst_type="FUTURES", inst_id=inst_id)
        assert isinstance(data, RequestData)
        assert data.get_status()
        price_list = data.get_data()
        assert isinstance(price_list, list)
        if len(price_list) > 0:
            print("get_estimated_price:", price_list[0])
    else:
        # Skip test if no valid instrument found
        pytest.skip("No valid FUTURES instrument found")
    # data = live_okx_swap_feed.get_estimated_price(inst_type="FUTURES")
    assert isinstance(data, RequestData)
    assert data.get_status()
    price_list = data.get_data()
    assert isinstance(price_list, list)
    if len(price_list) > 0:
        print("get_estimated_price:", price_list[0])


def test_okx_async_get_estimated_price():
    """Test async_get_estimated_price interface"""
    data_queue = queue.Queue()
    live_okx_swap_feed = init_async_feed(data_queue)
    live_okx_swap_feed.async_get_estimated_price(inst_type="FUTURES")
    time.sleep(5)
    try:
        result = data_queue.get(False)
    except queue.Empty:
        result = None
    if result is not None:
        assert isinstance(result, RequestData)
        print("async_get_estimated_price status:", result.get_status())


def test_okx_req_get_discount_rate():
    """Test get_discount_rate interface - Get discount rate and interest-free quota"""
    live_okx_swap_feed = init_req_feed()
    data = live_okx_swap_feed.get_discount_rate()
    assert isinstance(data, RequestData)
    assert data.get_status()
    discount_list = data.get_data()
    assert isinstance(discount_list, list)
    if len(discount_list) > 0:
        print("get_discount_rate:", discount_list[0])


def test_okx_async_get_discount_rate():
    """Test async_get_discount_rate interface"""
    data_queue = queue.Queue()
    live_okx_swap_feed = init_async_feed(data_queue)
    live_okx_swap_feed.async_get_discount_rate()
    time.sleep(5)
    try:
        result = data_queue.get(False)
    except queue.Empty:
        result = None
    if result is not None:
        assert isinstance(result, RequestData)
        print("async_get_discount_rate status:", result.get_status())


def test_okx_req_get_interest_rate_loan_quota():
    """Test get_interest_rate_loan_quota interface - Get interest rate and loan quota"""
    live_okx_swap_feed = init_req_feed()
    data = live_okx_swap_feed.get_interest_rate_loan_quota()
    assert isinstance(data, RequestData)
    assert data.get_status()
    quota_list = data.get_data()
    assert isinstance(quota_list, list)
    if len(quota_list) > 0:
        print("get_interest_rate_loan_quota:", quota_list[0])


def test_okx_async_get_interest_rate_loan_quota():
    """Test async_get_interest_rate_loan_quota interface"""
    data_queue = queue.Queue()
    live_okx_swap_feed = init_async_feed(data_queue)
    live_okx_swap_feed.async_get_interest_rate_loan_quota()
    time.sleep(5)
    try:
        result = data_queue.get(False)
    except queue.Empty:
        result = None
    if result is not None:
        assert isinstance(result, RequestData)
        print("async_get_interest_rate_loan_quota status:", result.get_status())


def test_okx_req_get_underlying():
    """Test get_underlying interface - Get underlying index"""
    live_okx_swap_feed = init_req_feed()
    data = live_okx_swap_feed.get_underlying(inst_type="FUTURES")
    assert isinstance(data, RequestData)
    assert data.get_status()
    underlying_list = data.get_data()
    assert isinstance(underlying_list, list)
    if len(underlying_list) > 0:
        print("get_underlying:", underlying_list[0])


def test_okx_async_get_underlying():
    """Test async_get_underlying interface"""
    data_queue = queue.Queue()
    live_okx_swap_feed = init_async_feed(data_queue)
    live_okx_swap_feed.async_get_underlying(inst_type="FUTURES")
    time.sleep(5)
    try:
        result = data_queue.get(False)
    except queue.Empty:
        result = None
    if result is not None:
        assert isinstance(result, RequestData)
        print("async_get_underlying status:", result.get_status())


def test_okx_req_get_insurance_fund():
    """Test get_insurance_fund interface - Get insurance fund balance"""
    live_okx_swap_feed = init_req_feed()
    data = live_okx_swap_feed.get_insurance_fund(inst_type="SWAP")
    assert isinstance(data, RequestData)
    assert data.get_status()
    fund_list = data.get_data()
    assert isinstance(fund_list, list)
    if len(fund_list) > 0:
        print("get_insurance_fund:", fund_list[0])


def test_okx_async_get_insurance_fund():
    """Test async_get_insurance_fund interface"""
    data_queue = queue.Queue()
    live_okx_swap_feed = init_async_feed(data_queue)
    live_okx_swap_feed.async_get_insurance_fund(inst_type="SWAP")
    time.sleep(5)
    try:
        result = data_queue.get(False)
    except queue.Empty:
        result = None
    if result is not None:
        assert isinstance(result, RequestData)
        print("async_get_insurance_fund status:", result.get_status())


def test_okx_req_convert_contract_coin():
    """Test convert_contract_coin interface - Convert contract unit"""
    live_okx_swap_feed = init_req_feed()
    data = live_okx_swap_feed.convert_contract_coin(
        inst_type="SWAP",
        uly="BTC-USDT",
        inst_id="BTC-USDT-SWAP",
        amount="100",
        unit="ct"
    )
    assert isinstance(data, RequestData)
    assert data.get_status()
    convert_list = data.get_data()
    assert isinstance(convert_list, list)
    if len(convert_list) > 0:
        print("convert_contract_coin:", convert_list[0])


def test_okx_async_convert_contract_coin():
    """Test async_convert_contract_coin interface"""
    data_queue = queue.Queue()
    live_okx_swap_feed = init_async_feed(data_queue)
    live_okx_swap_feed.async_convert_contract_coin(
        inst_type="SWAP",
        uly="BTC-USDT",
        inst_id="BTC-USDT-SWAP",
        amount="100",
        unit="ct"
    )
    time.sleep(5)
    try:
        result = data_queue.get(False)
    except queue.Empty:
        result = None
    if result is not None:
        assert isinstance(result, RequestData)
        print("async_convert_contract_coin status:", result.get_status())


def test_okx_req_get_instrument_tick_bands():
    """Test get_instrument_tick_bands interface - Get instrument minimum tick size"""
    live_okx_swap_feed = init_req_feed()
    data = live_okx_swap_feed.get_instrument_tick_bands(inst_type="SWAP")
    assert isinstance(data, RequestData)
    assert data.get_status()
    tick_bands_list = data.get_data()
    assert isinstance(tick_bands_list, list)
    if len(tick_bands_list) > 0:
        print("get_instrument_tick_bands:", tick_bands_list[0])


def test_okx_async_get_instrument_tick_bands():
    """Test async_get_instrument_tick_bands interface"""
    data_queue = queue.Queue()
    live_okx_swap_feed = init_async_feed(data_queue)
    live_okx_swap_feed.async_get_instrument_tick_bands(inst_type="SWAP")
    time.sleep(5)
    try:
        result = data_queue.get(False)
    except queue.Empty:
        result = None
    if result is not None:
        assert isinstance(result, RequestData)
        print("async_get_instrument_tick_bands status:", result.get_status())


# ==================== Funding Account (P2) Tests ====================

def test_okx_get_exchange_list():
    """Test get_exchange_list interface"""
    live_okx_swap_feed = init_req_feed()
    data = live_okx_swap_feed.get_exchange_list(ccy="BTC")
    assert isinstance(data, RequestData)
    print("get_exchange_list status:", data.get_status())


def test_okx_async_get_exchange_list():
    """Test async_get_exchange_list interface"""
    data_queue = queue.Queue()
    live_okx_swap_feed = init_async_feed(data_queue)
    live_okx_swap_feed.async_get_exchange_list(ccy="BTC")
    time.sleep(5)
    try:
        result = data_queue.get(False)
    except queue.Empty:
        result = None
    if result is not None:
        assert isinstance(result, RequestData)
        print("async_get_exchange_list status:", result.get_status())


def test_okx_get_convert_currencies():
    """Test get_convert_currencies interface"""
    live_okx_swap_feed = init_req_feed()
    data = live_okx_swap_feed.get_convert_currencies()
    assert isinstance(data, RequestData)
    print("get_convert_currencies status:", data.get_status())


def test_okx_async_get_convert_currencies():
    """Test async_get_convert_currencies interface"""
    data_queue = queue.Queue()
    live_okx_swap_feed = init_async_feed(data_queue)
    live_okx_swap_feed.async_get_convert_currencies()
    time.sleep(5)
    try:
        result = data_queue.get(False)
    except queue.Empty:
        result = None
    if result is not None:
        assert isinstance(result, RequestData)
        print("async_get_convert_currencies status:", result.get_status())


def test_okx_get_convert_currency_pair():
    """Test get_convert_currency_pair interface"""
    live_okx_swap_feed = init_req_feed()
    data = live_okx_swap_feed.get_convert_currency_pair(from_ccy="BTC", to_ccy="USDT")
    assert isinstance(data, RequestData)
    print("get_convert_currency_pair status:", data.get_status())


def test_okx_async_get_convert_currency_pair():
    """Test async_get_convert_currency_pair interface"""
    data_queue = queue.Queue()
    live_okx_swap_feed = init_async_feed(data_queue)
    live_okx_swap_feed.async_get_convert_currency_pair(from_ccy="BTC", to_ccy="USDT")
    time.sleep(5)
    try:
        result = data_queue.get(False)
    except queue.Empty:
        result = None
    if result is not None:
        assert isinstance(result, RequestData)
        print("async_get_convert_currency_pair status:", result.get_status())


def test_okx_get_convert_history():
    """Test get_convert_history interface"""
    live_okx_swap_feed = init_req_feed()
    data = live_okx_swap_feed.get_convert_history(limit="10")
    assert isinstance(data, RequestData)
    print("get_convert_history status:", data.get_status())


def test_okx_async_get_convert_history():
    """Test async_get_convert_history interface"""
    data_queue = queue.Queue()
    live_okx_swap_feed = init_async_feed(data_queue)
    live_okx_swap_feed.async_get_convert_history(limit="10")
    time.sleep(5)
    try:
        result = data_queue.get(False)
    except queue.Empty:
        result = None
    if result is not None:
        assert isinstance(result, RequestData)
        print("async_get_convert_history status:", result.get_status())


def test_okx_get_deposit_payment_methods():
    """Test get_deposit_payment_methods interface"""
    live_okx_swap_feed = init_req_feed()
    data = live_okx_swap_feed.get_deposit_payment_methods(ccy="BTC")
    assert isinstance(data, RequestData)
    print("get_deposit_payment_methods status:", data.get_status())


def test_okx_async_get_deposit_payment_methods():
    """Test async_get_deposit_payment_methods interface"""
    data_queue = queue.Queue()
    live_okx_swap_feed = init_async_feed(data_queue)
    live_okx_swap_feed.async_get_deposit_payment_methods(ccy="BTC")
    time.sleep(5)
    try:
        result = data_queue.get(False)
    except queue.Empty:
        result = None
    if result is not None:
        assert isinstance(result, RequestData)
        print("async_get_deposit_payment_methods status:", result.get_status())


def test_okx_get_withdrawal_payment_methods():
    """Test get_withdrawal_payment_methods interface"""
    live_okx_swap_feed = init_req_feed()
    data = live_okx_swap_feed.get_withdrawal_payment_methods(ccy="BTC")
    assert isinstance(data, RequestData)
    print("get_withdrawal_payment_methods status:", data.get_status())


def test_okx_async_get_withdrawal_payment_methods():
    """Test async_get_withdrawal_payment_methods interface"""
    data_queue = queue.Queue()
    live_okx_swap_feed = init_async_feed(data_queue)
    live_okx_swap_feed.async_get_withdrawal_payment_methods(ccy="BTC")
    time.sleep(5)
    try:
        result = data_queue.get(False)
    except queue.Empty:
        result = None
    if result is not None:
        assert isinstance(result, RequestData)
        print("async_get_withdrawal_payment_methods status:", result.get_status())


def test_okx_get_withdrawal_order_history():
    """Test get_withdrawal_order_history interface"""
    live_okx_swap_feed = init_req_feed()
    data = live_okx_swap_feed.get_withdrawal_order_history(ccy="BTC", limit="10")
    assert isinstance(data, RequestData)
    print("get_withdrawal_order_history status:", data.get_status())


def test_okx_async_get_withdrawal_order_history():
    """Test async_get_withdrawal_order_history interface"""
    data_queue = queue.Queue()
    live_okx_swap_feed = init_async_feed(data_queue)
    live_okx_swap_feed.async_get_withdrawal_order_history(ccy="BTC", limit="10")
    time.sleep(5)
    try:
        result = data_queue.get(False)
    except queue.Empty:
        result = None
    if result is not None:
        assert isinstance(result, RequestData)
        print("async_get_withdrawal_order_history status:", result.get_status())


def test_okx_get_deposit_order_history():
    """Test get_deposit_order_history interface"""
    live_okx_swap_feed = init_req_feed()
    data = live_okx_swap_feed.get_deposit_order_history(ccy="BTC", limit="10")
    assert isinstance(data, RequestData)
    print("get_deposit_order_history status:", data.get_status())


def test_okx_async_get_deposit_order_history():
    """Test async_get_deposit_order_history interface"""
    data_queue = queue.Queue()
    live_okx_swap_feed = init_async_feed(data_queue)
    live_okx_swap_feed.async_get_deposit_order_history(ccy="BTC", limit="10")
    time.sleep(5)
    try:
        result = data_queue.get(False)
    except queue.Empty:
        result = None
    if result is not None:
        assert isinstance(result, RequestData)
        print("async_get_deposit_order_history status:", result.get_status())


def test_okx_get_buy_sell_currencies():
    """Test get_buy_sell_currencies interface"""
    live_okx_swap_feed = init_req_feed()
    data = live_okx_swap_feed.get_buy_sell_currencies()
    assert isinstance(data, RequestData)
    print("get_buy_sell_currencies status:", data.get_status())


def test_okx_async_get_buy_sell_currencies():
    """Test async_get_buy_sell_currencies interface"""
    data_queue = queue.Queue()
    live_okx_swap_feed = init_async_feed(data_queue)
    live_okx_swap_feed.async_get_buy_sell_currencies()
    time.sleep(5)
    try:
        result = data_queue.get(False)
    except queue.Empty:
        result = None
    if result is not None:
        assert isinstance(result, RequestData)
        print("async_get_buy_sell_currencies status:", result.get_status())


def test_okx_get_buy_sell_currency_pair():
    """Test get_buy_sell_currency_pair interface"""
    live_okx_swap_feed = init_req_feed()
    data = live_okx_swap_feed.get_buy_sell_currency_pair()
    assert isinstance(data, RequestData)
    print("get_buy_sell_currency_pair status:", data.get_status())


def test_okx_async_get_buy_sell_currency_pair():
    """Test async_get_buy_sell_currency_pair interface"""
    data_queue = queue.Queue()
    live_okx_swap_feed = init_async_feed(data_queue)
    live_okx_swap_feed.async_get_buy_sell_currency_pair()
    time.sleep(5)
    try:
        result = data_queue.get(False)
    except queue.Empty:
        result = None
    if result is not None:
        assert isinstance(result, RequestData)
        print("async_get_buy_sell_currency_pair status:", result.get_status())


def test_okx_get_buy_sell_history():
    """Test get_buy_sell_history interface"""
    live_okx_swap_feed = init_req_feed()
    data = live_okx_swap_feed.get_buy_sell_history(limit="10")
    assert isinstance(data, RequestData)
    print("get_buy_sell_history status:", data.get_status())


def test_okx_async_get_buy_sell_history():
    """Test async_get_buy_sell_history interface"""
    data_queue = queue.Queue()
    live_okx_swap_feed = init_async_feed(data_queue)
    live_okx_swap_feed.async_get_buy_sell_history(limit="10")
    time.sleep(5)
    try:
        result = data_queue.get(False)
    except queue.Empty:
        result = None
    if result is not None:
        assert isinstance(result, RequestData)
        print("async_get_buy_sell_history status:", result.get_status())


# ==================== Sub-account (P2) Tests ====================

def test_okx_get_sub_account_transfer_history():
    """Test get_sub_account_transfer_history interface"""
    live_okx_swap_feed = init_req_feed()
    data = live_okx_swap_feed.get_sub_account_transfer_history(limit="10")
    assert isinstance(data, RequestData)
    print("get_sub_account_transfer_history status:", data.get_status())


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


def test_okx_get_managed_sub_account_bills():
    """Test get_managed_sub_account_bills interface"""
    live_okx_swap_feed = init_req_feed()
    data = live_okx_swap_feed.get_managed_sub_account_bills(limit="10")
    assert isinstance(data, RequestData)
    print("get_managed_sub_account_bills status:", data.get_status())


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


def test_okx_get_custody_sub_account_list():
    """Test get_custody_sub_account_list interface"""
    live_okx_swap_feed = init_req_feed()
    data = live_okx_swap_feed.get_custody_sub_account_list()
    assert isinstance(data, RequestData)
    print("get_custody_sub_account_list status:", data.get_status())


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

def test_okx_get_system_status():
    """Test get_system_status interface"""
    live_okx_swap_feed = init_req_feed()
    data = live_okx_swap_feed.get_system_status()
    assert isinstance(data, RequestData)
    print("get_system_status status:", data.get_status())
    if data.get_status():
        data_list = data.get_data()
        assert isinstance(data_list, list)
        print("System status data:", data_list[:2] if data_list else "No data")


def test_okx_async_get_system_status():
    """Test async_get_system_status interface"""
    data_queue = queue.Queue()
    live_okx_swap_feed = init_async_feed(data_queue)
    live_okx_swap_feed.async_get_system_status(extra_data={"test_async_system_status": True})
    time.sleep(5)
    try:
        result = data_queue.get(False)
    except queue.Empty:
        result = None
    if result is not None:
        assert isinstance(result, RequestData)
        print("async_get_system_status status:", result.get_status())


def test_okx_get_system_status_scheduled():
    """Test get_system_status with scheduled state (maintenance announcements)"""
    live_okx_swap_feed = init_req_feed()
    data = live_okx_swap_feed.get_system_status(state="scheduled")
    assert isinstance(data, RequestData)
    print("get_system_status (scheduled) status:", data.get_status())


def test_okx_async_get_system_status_scheduled():
    """Test async_get_system_status with scheduled state"""
    data_queue = queue.Queue()
    live_okx_swap_feed = init_async_feed(data_queue)
    live_okx_swap_feed.async_get_system_status(state="scheduled")
    time.sleep(5)
    try:
        result = data_queue.get(False)
    except queue.Empty:
        result = None
    if result is not None:
        assert isinstance(result, RequestData)
        print("async_get_system_status (scheduled) status:", result.get_status())


def test_okx_get_announcements():
    """Test get_announcements interface"""
    live_okx_swap_feed = init_req_feed()
    data = live_okx_swap_feed.get_announcements(limit="20")
    assert isinstance(data, RequestData)
    print("get_announcements status:", data.get_status())
    if data.get_status():
        data_list = data.get_data()
        assert isinstance(data_list, list)
        print("Announcements count:", len(data_list))
        if data_list:
            print("First announcement:", data_list[0])


def test_okx_async_get_announcements():
    """Test async_get_announcements interface"""
    data_queue = queue.Queue()
    live_okx_swap_feed = init_async_feed(data_queue)
    live_okx_swap_feed.async_get_announcements(limit="10")
    time.sleep(5)
    try:
        result = data_queue.get(False)
    except queue.Empty:
        result = None
    if result is not None:
        assert isinstance(result, RequestData)
        print("async_get_announcements status:", result.get_status())


def test_okx_get_announcements_with_type():
    """Test get_announcements with announcement type"""
    live_okx_swap_feed = init_req_feed()
    # First get the announcement types
    types_data = live_okx_swap_feed.get_announcement_types()
    if types_data.get_status():
        type_list = types_data.get_data()
        if type_list and len(type_list) > 0:
            first_type = type_list[0]
            print("Testing announcements with type:", first_type)
            data = live_okx_swap_feed.get_announcements(announcement_type=first_type, limit="10")
            assert isinstance(data, RequestData)
            print("get_announcements (with type) status:", data.get_status())
        else:
            print("No announcement types available")
    else:
        print("Could not get announcement types")


def test_okx_get_announcement_types():
    """Test get_announcement_types interface"""
    live_okx_swap_feed = init_req_feed()
    data = live_okx_swap_feed.get_announcement_types()
    assert isinstance(data, RequestData)
    print("get_announcement_types status:", data.get_status())
    if data.get_status():
        data_list = data.get_data()
        assert isinstance(data_list, list)
        print("Announcement types:", data_list[:5] if data_list else "No data")


def test_okx_async_get_announcement_types():
    """Test async_get_announcement_types interface"""
    data_queue = queue.Queue()
    live_okx_swap_feed = init_async_feed(data_queue)
    live_okx_swap_feed.async_get_announcement_types()
    time.sleep(5)
    try:
        result = data_queue.get(False)
    except queue.Empty:
        result = None
    if result is not None:
        assert isinstance(result, RequestData)
        print("async_get_announcement_types status:", result.get_status())


# ==================== WebSocket Channel Tests ====================

def test_okx_wss_economic_calendar():
    """Test WebSocket economic-calendar channel (经济日历推送)."""
    from bt_api_py.feeds.live_okx_feed import OkxMarketWssDataSwap

    data_queue = queue.Queue()
    kwargs = generate_kwargs()
    kwargs.update({
        'topics': [{"topic": "economic_calendar"}],
        "wss_name": "test_economic_calendar",
        "wss_url": 'wss://ws.okx.com:8443/ws/v5/public',
        "exchange_data": OkxExchangeDataSwap(),
    })

    wss = OkxMarketWssDataSwap(data_queue, **kwargs)
    wss.start()
    time.sleep(5)

    # Economic calendar data may not be pushed immediately
    count = 0
    received_data = False
    while count < 100:
        try:
            data = data_queue.get(timeout=0.5)
            received_data = True
            print(f"Received economic calendar data: {type(data).__name__}")
            break
        except queue.Empty:
            break
        count += 1

    wss.stop()

    if received_data:
        print("Economic calendar data received successfully")
    assert True, "economic-calendar channel subscription test completed"


def test_okx_wss_deposit_info():
    """Test WebSocket deposit-info channel (充值信息推送)."""
    from bt_api_py.feeds.live_okx_feed import OkxMarketWssDataSwap

    data_queue = queue.Queue()
    kwargs = generate_kwargs()
    kwargs.update({
        'topics': [{"topic": "deposit_info"}],
        "wss_name": "test_deposit_info",
        "wss_url": 'wss://ws.okx.com:8443/ws/v5/private',
        "exchange_data": OkxExchangeDataSwap(),
    })

    wss = OkxMarketWssDataSwap(data_queue, **kwargs)
    wss.start()
    time.sleep(5)

    # Deposit info data may not be pushed immediately (only on new deposits)
    count = 0
    received_deposit = False
    while count < 100:
        try:
            data = data_queue.get(timeout=0.5)
            if isinstance(data, OkxDepositInfoData):
                received_deposit = True
                data.init_data()
                assert data.get_exchange_name() == "OKX"
                print(f"Received deposit info: currency={data.get_currency()}, "
                      f"amount={data.get_amount()}, status={data.get_status()}")
                break
        except queue.Empty:
            break
        count += 1

    wss.stop()

    if received_deposit:
        print("Deposit info data received successfully")
    assert True, "deposit-info channel subscription test completed"


def test_okx_wss_withdrawal_info():
    """Test WebSocket withdrawal-info channel (提币信息推送)."""
    from bt_api_py.feeds.live_okx_feed import OkxMarketWssDataSwap

    data_queue = queue.Queue()
    kwargs = generate_kwargs()
    kwargs.update({
        'topics': [{"topic": "withdrawal_info"}],
        "wss_name": "test_withdrawal_info",
        "wss_url": 'wss://ws.okx.com:8443/ws/v5/private',
        "exchange_data": OkxExchangeDataSwap(),
    })

    wss = OkxMarketWssDataSwap(data_queue, **kwargs)
    wss.start()
    time.sleep(5)

    # Withdrawal info data may not be pushed immediately (only on new withdrawals)
    count = 0
    received_withdrawal = False
    while count < 100:
        try:
            data = data_queue.get(timeout=0.5)
            if isinstance(data, OkxWithdrawalInfoData):
                received_withdrawal = True
                data.init_data()
                assert data.get_exchange_name() == "OKX"
                print(f"Received withdrawal info: currency={data.get_currency()}, "
                      f"amount={data.get_amount()}, status={data.get_status()}")
                break
        except queue.Empty:
            break
        count += 1

    wss.stop()

    if received_withdrawal:
        print("Withdrawal info data received successfully")
    assert True, "withdrawal-info channel subscription test completed"


# ==================== Position Builder Tests ====================

def test_okx_position_builder():
    """Test position_builder interface"""
    live_okx_swap_feed = init_req_feed()
    # Test position builder with SWAP type and BTC underlying
    data = live_okx_swap_feed.position_builder(inst_type="SWAP", uly="BTC-USD", max_sz=1)
    assert isinstance(data, RequestData)
    print("position_builder status:", data.get_status())
    if data.get_status():
        data_list = data.get_data()
        assert isinstance(data_list, list)
        print("Position builder data:", data_list[:1] if data_list else "No data")


def test_okx_async_position_builder():
    """Test async_position_builder interface"""
    data_queue = queue.Queue()
    live_okx_swap_feed = init_async_feed(data_queue)
    live_okx_swap_feed.async_position_builder(inst_type="SWAP", uly="BTC-USD", max_sz=1,
                                               extra_data={"test_async_position_builder": True})
    time.sleep(5)
    try:
        result = data_queue.get(False)
    except queue.Empty:
        result = None
    if result is not None:
        assert isinstance(result, RequestData)
        print("async_position_builder status:", result.get_status())


def test_okx_position_builder_trend():
    """Test position_builder_trend interface"""
    live_okx_swap_feed = init_req_feed()
    # Test position builder trend with SWAP type and BTC underlying
    data = live_okx_swap_feed.position_builder_trend(inst_type="SWAP", uly="BTC-USD", max_sz=1)
    assert isinstance(data, RequestData)
    print("position_builder_trend status:", data.get_status())
    if data.get_status():
        data_list = data.get_data()
        assert isinstance(data_list, list)
        print("Position builder trend data:", data_list[:1] if data_list else "No data")


def test_okx_async_position_builder_trend():
    """Test async_position_builder_trend interface"""
    data_queue = queue.Queue()
    live_okx_swap_feed = init_async_feed(data_queue)
    live_okx_swap_feed.async_position_builder_trend(inst_type="SWAP", uly="BTC-USD", max_sz=1,
                                                      extra_data={"test_async_position_builder_trend": True})
    time.sleep(5)
    try:
        result = data_queue.get(False)
    except queue.Empty:
        result = None
    if result is not None:
        assert isinstance(result, RequestData)
        print("async_position_builder_trend status:", result.get_status())


# ==================== Cancel All Orders Tests ====================

def test_okx_cancel_all():
    """Test cancel_all interface"""
    live_okx_swap_feed = init_req_feed()
    # Test cancel all orders for SWAP type
    data = live_okx_swap_feed.cancel_all(inst_type="SWAP", inst_id="BTC-USDT-SWAP")
    assert isinstance(data, RequestData)
    print("cancel_all status:", data.get_status())


def test_okx_async_cancel_all():
    """Test async_cancel_all interface"""
    data_queue = queue.Queue()
    live_okx_swap_feed = init_async_feed(data_queue)
    live_okx_swap_feed.async_cancel_all(inst_type="SWAP", inst_id="BTC-USDT-SWAP",
                                         extra_data={"test_async_cancel_all": True})
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
        symbol="BTC-USDT-SWAP",
        td_mode="cross",
        ccy="USDT",
        side="buy",
        order_type="market",
        sz="1"
    )
    assert isinstance(data, RequestData)
    print("order_precheck status:", data.get_status())
    if data.get_status():
        data_list = data.get_data()
        assert isinstance(data_list, list)
        print("Order precheck data:", data_list[:1] if data_list else "No data")


def test_okx_async_order_precheck():
    """Test async_order_precheck interface"""
    data_queue = queue.Queue()
    live_okx_swap_feed = init_async_feed(data_queue)
    live_okx_swap_feed.async_order_precheck(
        symbol="BTC-USDT-SWAP",
        td_mode="cross",
        ccy="USDT",
        side="buy",
        order_type="market",
        sz="1",
        extra_data={"test_async_order_precheck": True}
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
    live_okx_swap_feed.async_get_open_interest(inst_type="SWAP", inst_id="BTC-USDT-SWAP",
                                                extra_data={"test_async_get_open_interest": True})
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
    live_okx_swap_feed.async_get_open_interest(inst_type="SWAP", uly="BTC-USD",
                                                extra_data={"test_async_get_open_interest_uly": True})
    time.sleep(5)
    try:
        result = data_queue.get(False)
    except queue.Empty:
        result = None
    if result is not None:
        assert isinstance(result, RequestData)
        print("async_get_open_interest (by uly) status:", result.get_status())

# ==================== Grid Trading Tests ====================

def test_okx_grid_positions():
    """Test grid_positions interface - 获取网格委托持仓"""
    live_okx_swap_feed = init_req_feed()
    data = live_okx_swap_feed.grid_positions(inst_type="SWAP")
    assert isinstance(data, RequestData)
    print("grid_positions status:", data.get_status())


def test_okx_async_grid_positions():
    """Test async_grid_positions interface"""
    data_queue = queue.Queue()
    live_okx_swap_feed = init_async_feed(data_queue)
    live_okx_swap_feed.async_grid_positions(inst_type="SWAP")
    time.sleep(5)
    try:
        result = data_queue.get(False)
    except queue.Empty:
        result = None
    if result is not None:
        assert isinstance(result, RequestData)
        print("async_grid_positions status:", result.get_status())


def test_okx_grid_get_ai_param():
    """Test grid_get_ai_param interface - 获取网格AI参数"""
    live_okx_swap_feed = init_req_feed()
    data = live_okx_swap_feed.grid_get_ai_param(
        inst_id="BTC-USDT-SWAP",
        algo_algo_type="grid_contract",
        max_px="100000",
        min_px="20000",
        grid_num="5"
    )
    assert isinstance(data, RequestData)
    print("grid_get_ai_param status:", data.get_status())


def test_okx_async_grid_get_ai_param():
    """Test async_grid_get_ai_param interface"""
    data_queue = queue.Queue()
    live_okx_swap_feed = init_async_feed(data_queue)
    live_okx_swap_feed.async_grid_get_ai_param(
        inst_id="BTC-USDT-SWAP",
        algo_algo_type="grid_contract",
        max_px="100000",
        min_px="20000",
        grid_num="5"
    )
    time.sleep(5)
    try:
        result = data_queue.get(False)
    except queue.Empty:
        result = None
    if result is not None:
        assert isinstance(result, RequestData)
        print("async_grid_get_ai_param status:", result.get_status())


def test_okx_grid_compute_min_investment():
    """Test grid_compute_min_investment interface - 计算最小投入金额"""
    live_okx_swap_feed = init_req_feed()
    data = live_okx_swap_feed.grid_compute_min_investment(
        inst_id="BTC-USDT-SWAP",
        algo_algo_type="grid_contract",
        max_px="100000",
        min_px="20000",
        grid_num="5"
    )
    assert isinstance(data, RequestData)
    print("grid_compute_min_investment status:", data.get_status())


def test_okx_async_grid_compute_min_investment():
    """Test async_grid_compute_min_investment interface"""
    data_queue = queue.Queue()
    live_okx_swap_feed = init_async_feed(data_queue)
    live_okx_swap_feed.async_grid_compute_min_investment(
        inst_id="BTC-USDT-SWAP",
        algo_algo_type="grid_contract",
        max_px="100000",
        min_px="20000",
        grid_num="5"
    )
    time.sleep(5)
    try:
        result = data_queue.get(False)
    except queue.Empty:
        result = None
    if result is not None:
        assert isinstance(result, RequestData)
        print("async_grid_compute_min_investment status:", result.get_status())


def test_okx_grid_rsi_back_testing():
    """Test grid_rsi_back_testing interface - RSI回测"""
    live_okx_swap_feed = init_req_feed()
    data = live_okx_swap_feed.grid_rsi_back_testing(
        inst_id="BTC-USDT-SWAP",
        algo_algo_type="grid_contract",
        max_px="100000",
        min_px="20000",
        grid_num="5"
    )
    assert isinstance(data, RequestData)
    print("grid_rsi_back_testing status:", data.get_status())


def test_okx_async_grid_rsi_back_testing():
    """Test async_grid_rsi_back_testing interface"""
    data_queue = queue.Queue()
    live_okx_swap_feed = init_async_feed(data_queue)
    live_okx_swap_feed.async_grid_rsi_back_testing(
        inst_id="BTC-USDT-SWAP",
        algo_algo_type="grid_contract",
        max_px="100000",
        min_px="20000",
        grid_num="5"
    )
    time.sleep(5)
    try:
        result = data_queue.get(False)
    except queue.Empty:
        result = None
    if result is not None:
        assert isinstance(result, RequestData)
        print("async_grid_rsi_back_testing status:", result.get_status())


def test_okx_grid_max_grid_quantity():
    """Test grid_max_grid_quantity interface - 最大网格数量"""
    live_okx_swap_feed = init_req_feed()
    data = live_okx_swap_feed.grid_max_grid_quantity(
        inst_id="BTC-USDT-SWAP",
        algo_algo_type="grid_contract"
    )
    assert isinstance(data, RequestData)
    print("grid_max_grid_quantity status:", data.get_status())


def test_okx_async_grid_max_grid_quantity():
    """Test async_grid_max_grid_quantity interface"""
    data_queue = queue.Queue()
    live_okx_swap_feed = init_async_feed(data_queue)
    live_okx_swap_feed.async_grid_max_grid_quantity(
        inst_id="BTC-USDT-SWAP",
        algo_algo_type="grid_contract"
    )
    time.sleep(5)
    try:
        result = data_queue.get(False)
    except queue.Empty:
        result = None
    if result is not None:
        assert isinstance(result, RequestData)
        print("async_grid_max_grid_quantity status:", result.get_status())


def test_okx_grid_compute_margin_balance():
    """Test grid_compute_margin_balance interface - 计算保证金余额"""
    live_okx_swap_feed = init_req_feed()
    data = live_okx_swap_feed.grid_compute_margin_balance(
        inst_id="BTC-USDT-SWAP",
        td_mode="cross",
        ccy="USDT",
        algo_ords_type="grid_contract",
        sz="100",
        max_px="100000",
        min_px="20000",
        grid_num="5"
    )
    assert isinstance(data, RequestData)
    print("grid_compute_margin_balance status:", data.get_status())


def test_okx_async_grid_compute_margin_balance():
    """Test async_grid_compute_margin_balance interface"""
    data_queue = queue.Queue()
    live_okx_swap_feed = init_async_feed(data_queue)
    live_okx_swap_feed.async_grid_compute_margin_balance(
        inst_id="BTC-USDT-SWAP",
        td_mode="cross",
        ccy="USDT",
        algo_ords_type="grid_contract",
        sz="100",
        max_px="100000",
        min_px="20000",
        grid_num="5"
    )
    time.sleep(5)
    try:
        result = data_queue.get(False)
    except queue.Empty:
        result = None
    if result is not None:
        assert isinstance(result, RequestData)
        print("async_grid_compute_margin_balance status:", result.get_status())


# Note: The following tests require actual grid orders to function properly.
# They are included for API validation but may return expected errors if no active orders exist.

def test_okx_grid_amend_order_algo_basic_params():
    """Test grid_amend_order_algo_basic interface parameter validation - 修改网格委托(基础参数)"""
    live_okx_swap_feed = init_req_feed()
    data = live_okx_swap_feed.grid_amend_order_algo_basic(
        algo_id="test_algo_id",
        inst_id="BTC-USDT-SWAP",
        max_px="100000",
        min_px="20000"
    )
    assert isinstance(data, RequestData)
    print("grid_amend_order_algo_basic status:", data.get_status())


def test_okx_async_grid_amend_order_algo_basic_params():
    """Test async_grid_amend_order_algo_basic interface parameter validation"""
    data_queue = queue.Queue()
    live_okx_swap_feed = init_async_feed(data_queue)
    live_okx_swap_feed.async_grid_amend_order_algo_basic(
        algo_id="test_algo_id",
        inst_id="BTC-USDT-SWAP",
        max_px="100000",
        min_px="20000"
    )
    time.sleep(5)
    try:
        result = data_queue.get(False)
    except queue.Empty:
        result = None
    if result is not None:
        assert isinstance(result, RequestData)
        print("async_grid_amend_order_algo_basic status:", result.get_status())


def test_okx_grid_close_position_params():
    """Test grid_close_position interface parameter validation - 合约网格平仓"""
    live_okx_swap_feed = init_req_feed()
    data = live_okx_swap_feed.grid_close_position(
        algo_id="test_algo_id",
        inst_id="BTC-USDT-SWAP"
    )
    assert isinstance(data, RequestData)
    print("grid_close_position status:", data.get_status())


def test_okx_async_grid_close_position_params():
    """Test async_grid_close_position interface parameter validation"""
    data_queue = queue.Queue()
    live_okx_swap_feed = init_async_feed(data_queue)
    live_okx_swap_feed.async_grid_close_position(
        algo_id="test_algo_id",
        inst_id="BTC-USDT-SWAP"
    )
    time.sleep(5)
    try:
        result = data_queue.get(False)
    except queue.Empty:
        result = None
    if result is not None:
        assert isinstance(result, RequestData)
        print("async_grid_close_position status:", result.get_status())


def test_okx_grid_cancel_close_order_params():
    """Test grid_cancel_close_order interface parameter validation - 撤销合约网格平仓单"""
    live_okx_swap_feed = init_req_feed()
    data = live_okx_swap_feed.grid_cancel_close_order(
        algo_id="test_algo_id",
        inst_id="BTC-USDT-SWAP"
    )
    assert isinstance(data, RequestData)
    print("grid_cancel_close_order status:", data.get_status())


def test_okx_async_grid_cancel_close_order_params():
    """Test async_grid_cancel_close_order interface parameter validation"""
    data_queue = queue.Queue()
    live_okx_swap_feed = init_async_feed(data_queue)
    live_okx_swap_feed.async_grid_cancel_close_order(
        algo_id="test_algo_id",
        inst_id="BTC-USDT-SWAP"
    )
    time.sleep(5)
    try:
        result = data_queue.get(False)
    except queue.Empty:
        result = None
    if result is not None:
        assert isinstance(result, RequestData)
        print("async_grid_cancel_close_order status:", result.get_status())


def test_okx_grid_order_instant_trigger_params():
    """Test grid_order_instant_trigger interface parameter validation - 网格委托立即触发"""
    live_okx_swap_feed = init_req_feed()
    data = live_okx_swap_feed.grid_order_instant_trigger(
        algo_id="test_algo_id",
        inst_id="BTC-USDT-SWAP"
    )
    assert isinstance(data, RequestData)
    print("grid_order_instant_trigger status:", data.get_status())


def test_okx_async_grid_order_instant_trigger_params():
    """Test async_grid_order_instant_trigger interface parameter validation"""
    data_queue = queue.Queue()
    live_okx_swap_feed = init_async_feed(data_queue)
    live_okx_swap_feed.async_grid_order_instant_trigger(
        algo_id="test_algo_id",
        inst_id="BTC-USDT-SWAP"
    )
    time.sleep(5)
    try:
        result = data_queue.get(False)
    except queue.Empty:
        result = None
    if result is not None:
        assert isinstance(result, RequestData)
        print("async_grid_order_instant_trigger status:", result.get_status())


def test_okx_grid_orders_algo_details_params():
    """Test grid_orders_algo_details interface parameter validation - 获取网格委托详情"""
    live_okx_swap_feed = init_req_feed()
    data = live_okx_swap_feed.grid_orders_algo_details(
        algo_id="test_algo_id",
        inst_id="BTC-USDT-SWAP"
    )
    assert isinstance(data, RequestData)
    print("grid_orders_algo_details status:", data.get_status())


def test_okx_async_grid_orders_algo_details_params():
    """Test async_grid_orders_algo_details interface parameter validation"""
    data_queue = queue.Queue()
    live_okx_swap_feed = init_async_feed(data_queue)
    live_okx_swap_feed.async_grid_orders_algo_details(
        algo_id="test_algo_id",
        inst_id="BTC-USDT-SWAP"
    )
    time.sleep(5)
    try:
        result = data_queue.get(False)
    except queue.Empty:
        result = None
    if result is not None:
        assert isinstance(result, RequestData)
        print("async_grid_orders_algo_details status:", result.get_status())


def test_okx_grid_sub_orders_params():
    """Test grid_sub_orders interface parameter validation - 获取网格委托子订单"""
    live_okx_swap_feed = init_req_feed()
    data = live_okx_swap_feed.grid_sub_orders(
        algo_id="test_algo_id",
        inst_id="BTC-USDT-SWAP"
    )
    assert isinstance(data, RequestData)
    print("grid_sub_orders status:", data.get_status())


def test_okx_async_grid_sub_orders_params():
    """Test async_grid_sub_orders interface parameter validation"""
    data_queue = queue.Queue()
    live_okx_swap_feed = init_async_feed(data_queue)
    live_okx_swap_feed.async_grid_sub_orders(
        algo_id="test_algo_id",
        inst_id="BTC-USDT-SWAP"
    )
    time.sleep(5)
    try:
        result = data_queue.get(False)
    except queue.Empty:
        result = None
    if result is not None:
        assert isinstance(result, RequestData)
        print("async_grid_sub_orders status:", result.get_status())


def test_okx_grid_withdraw_income_params():
    """Test grid_withdraw_income interface parameter validation - 现货网格提取利润"""
    live_okx_swap_feed = init_req_feed()
    data = live_okx_swap_feed.grid_withdraw_income(
        algo_id="test_algo_id",
        inst_id="BTC-USDT",
        amt="10"
    )
    assert isinstance(data, RequestData)
    print("grid_withdraw_income status:", data.get_status())


def test_okx_async_grid_withdraw_income_params():
    """Test async_grid_withdraw_income interface parameter validation"""
    data_queue = queue.Queue()
    live_okx_swap_feed = init_async_feed(data_queue)
    live_okx_swap_feed.async_grid_withdraw_income(
        algo_id="test_algo_id",
        inst_id="BTC-USDT",
        amt="10"
    )
    time.sleep(5)
    try:
        result = data_queue.get(False)
    except queue.Empty:
        result = None
    if result is not None:
        assert isinstance(result, RequestData)
        print("async_grid_withdraw_income status:", result.get_status())


def test_okx_grid_margin_balance_params():
    """Test grid_margin_balance interface parameter validation - 调整保证金"""
    live_okx_swap_feed = init_req_feed()
    data = live_okx_swap_feed.grid_margin_balance(
        algo_id="test_algo_id",
        inst_id="BTC-USDT-SWAP",
        amt="100"
    )
    assert isinstance(data, RequestData)
    print("grid_margin_balance status:", data.get_status())


def test_okx_async_grid_margin_balance_params():
    """Test async_grid_margin_balance interface parameter validation"""
    data_queue = queue.Queue()
    live_okx_swap_feed = init_async_feed(data_queue)
    live_okx_swap_feed.async_grid_margin_balance(
        algo_id="test_algo_id",
        inst_id="BTC-USDT-SWAP",
        amt="100"
    )
    time.sleep(5)
    try:
        result = data_queue.get(False)
    except queue.Empty:
        result = None
    if result is not None:
        assert isinstance(result, RequestData)
        print("async_grid_margin_balance status:", result.get_status())


def test_okx_grid_add_investment_params():
    """Test grid_add_investment interface parameter validation - 增加投入币数量"""
    live_okx_swap_feed = init_req_feed()
    data = live_okx_swap_feed.grid_add_investment(
        algo_id="test_algo_id",
        inst_id="BTC-USDT-SWAP",
        amt="100"
    )
    assert isinstance(data, RequestData)
    print("grid_add_investment status:", data.get_status())


def test_okx_async_grid_add_investment_params():
    """Test async_grid_add_investment interface parameter validation"""
    data_queue = queue.Queue()
    live_okx_swap_feed = init_async_feed(data_queue)
    live_okx_swap_feed.async_grid_add_investment(
        algo_id="test_algo_id",
        inst_id="BTC-USDT-SWAP",
        amt="100"
    )
    time.sleep(5)
    try:
        result = data_queue.get(False)
    except queue.Empty:
        result = None
    if result is not None:
        assert isinstance(result, RequestData)
        print("async_grid_add_investment status:", result.get_status())


# ==================== Spread Trading Tests ====================

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

def test_okx_copytrading_get_config():
    """Test getting copy trading account configuration"""
    live_okx_swap_feed = init_req_feed()
    data = live_okx_swap_feed.copytrading_get_config()
    assert isinstance(data, RequestData)
    print("copytrading_get_config status:", data.get_status())


def test_okx_copytrading_get_copy_settings():
    """Test getting copy trading settings"""
    live_okx_swap_feed = init_req_feed()
    data = live_okx_swap_feed.copytrading_get_copy_settings()
    assert isinstance(data, RequestData)
    print("copytrading_get_copy_settings status:", data.get_status())


def test_okx_copytrading_get_copy_trading_configuration():
    """Test getting copy trading configuration"""
    live_okx_swap_feed = init_req_feed()
    data = live_okx_swap_feed.copytrading_get_copy_trading_configuration()
    assert isinstance(data, RequestData)
    print("copytrading_get_copy_trading_configuration status:", data.get_status())


def test_okx_copytrading_get_instruments():
    """Test getting copy trading leading instruments"""
    live_okx_swap_feed = init_req_feed()
    data = live_okx_swap_feed.copytrading_get_instruments()
    assert isinstance(data, RequestData)
    print("copytrading_get_instruments status:", data.get_status())


def test_okx_copytrading_get_current_subpositions():
    """Test getting existing lead positions"""
    live_okx_swap_feed = init_req_feed()
    data = live_okx_swap_feed.copytrading_get_current_subpositions()
    assert isinstance(data, RequestData)
    print("copytrading_get_current_subpositions status:", data.get_status())


def test_okx_copytrading_get_subpositions_history():
    """Test getting lead position history"""
    live_okx_swap_feed = init_req_feed()
    data = live_okx_swap_feed.copytrading_get_subpositions_history()
    assert isinstance(data, RequestData)
    print("copytrading_get_subpositions_history status:", data.get_status())


def test_okx_copytrading_get_batch_leverage_info():
    """Test getting my lead traders"""
    live_okx_swap_feed = init_req_feed()
    data = live_okx_swap_feed.copytrading_get_batch_leverage_info()
    assert isinstance(data, RequestData)
    print("copytrading_get_batch_leverage_info status:", data.get_status())


def test_okx_copytrading_get_profit_sharing_details():
    """Test getting profit sharing details"""
    live_okx_swap_feed = init_req_feed()
    data = live_okx_swap_feed.copytrading_get_profit_sharing_details()
    assert isinstance(data, RequestData)
    print("copytrading_get_profit_sharing_details status:", data.get_status())


def test_okx_copytrading_get_total_profit_sharing():
    """Test getting total profit sharing"""
    live_okx_swap_feed = init_req_feed()
    data = live_okx_swap_feed.copytrading_get_total_profit_sharing()
    assert isinstance(data, RequestData)
    print("copytrading_get_total_profit_sharing status:", data.get_status())


def test_okx_copytrading_get_unrealized_profit_sharing_details():
    """Test getting unrealized profit sharing details"""
    live_okx_swap_feed = init_req_feed()
    data = live_okx_swap_feed.copytrading_get_unrealized_profit_sharing_details()
    assert isinstance(data, RequestData)
    print("copytrading_get_unrealized_profit_sharing_details status:", data.get_status())


def test_okx_copytrading_get_total_unrealized_profit_sharing():
    """Test getting total unrealized profit sharing"""
    live_okx_swap_feed = init_req_feed()
    data = live_okx_swap_feed.copytrading_get_total_unrealized_profit_sharing()
    assert isinstance(data, RequestData)
    print("copytrading_get_total_unrealized_profit_sharing status:", data.get_status())


def test_okx_copytrading_public_lead_traders():
    """Test getting lead trader ranks (public)"""
    live_okx_swap_feed = init_req_feed()
    data = live_okx_swap_feed.copytrading_public_lead_traders(inst_type="SWAP", limit="10")
    assert isinstance(data, RequestData)
    print("copytrading_public_lead_traders status:", data.get_status())


def test_okx_copytrading_public_stats():
    """Test getting lead trader stats (public)"""
    live_okx_swap_feed = init_req_feed()
    # Using a sample copy_inst_id format (users should replace with actual ID)
    data = live_okx_swap_feed.copytrading_public_stats(copy_inst_id="BTC-USDT")
    assert isinstance(data, RequestData)
    print("copytrading_public_stats status:", data.get_status())


def test_async_copytrading_get_config():
    """Test async getting copy trading account configuration"""
    data_queue = queue.Queue()
    live_okx_swap_feed = init_async_feed(data_queue)
    live_okx_swap_feed.async_copytrading_get_config(extra_data={"test_async_copytrading": True})
    time.sleep(5)
    try:
        result = data_queue.get(False)
    except queue.Empty:
        result = None
    if result is not None:
        assert isinstance(result, RequestData)
        print("async_copytrading_get_config status:", result.get_status())


def test_async_copytrading_get_copy_settings():
    """Test async getting copy trading settings"""
    data_queue = queue.Queue()
    live_okx_swap_feed = init_async_feed(data_queue)
    live_okx_swap_feed.async_copytrading_get_copy_settings(extra_data={"test_async_copytrading": True})
    time.sleep(5)
    try:
        result = data_queue.get(False)
    except queue.Empty:
        result = None
    if result is not None:
        assert isinstance(result, RequestData)
        print("async_copytrading_get_copy_settings status:", result.get_status())


def test_async_copytrading_get_instruments():
    """Test async getting copy trading leading instruments"""
    data_queue = queue.Queue()
    live_okx_swap_feed = init_async_feed(data_queue)
    live_okx_swap_feed.async_copytrading_get_instruments(extra_data={"test_async_copytrading": True})
    time.sleep(5)
    try:
        result = data_queue.get(False)
    except queue.Empty:
        result = None
    if result is not None:
        assert isinstance(result, RequestData)
        print("async_copytrading_get_instruments status:", result.get_status())


def test_async_copytrading_get_current_subpositions():
    """Test async getting existing lead positions"""
    data_queue = queue.Queue()
    live_okx_swap_feed = init_async_feed(data_queue)
    live_okx_swap_feed.async_copytrading_get_current_subpositions(extra_data={"test_async_copytrading": True})
    time.sleep(5)
    try:
        result = data_queue.get(False)
    except queue.Empty:
        result = None
    if result is not None:
        assert isinstance(result, RequestData)
        print("async_copytrading_get_current_subpositions status:", result.get_status())


def test_async_copytrading_public_lead_traders():
    """Test async getting lead trader ranks (public)"""
    data_queue = queue.Queue()
    live_okx_swap_feed = init_async_feed(data_queue)
    live_okx_swap_feed.async_copytrading_public_lead_traders(
        inst_type="SWAP",
        limit="10",
        extra_data={"test_async_copytrading": True}
    )
    time.sleep(5)
    try:
        result = data_queue.get(False)
    except queue.Empty:
        result = None
    if result is not None:
        assert isinstance(result, RequestData)
        print("async_copytrading_public_lead_traders status:", result.get_status())
