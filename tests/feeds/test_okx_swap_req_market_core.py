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



def test_get_okx_key():
    data = read_account_config()
    public_key = data['okx']['public_key']
    private_key = data['okx']['private_key'] + "//" + data['okx']["passphrase"]
    assert len(public_key) == 36, "public key is wrong"
    assert len(private_key) > 0, "private key is wrong"






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
    try:
        depth_data = data_queue.get(timeout=15)
    except queue.Empty:
        depth_data = None
    assert depth_data is not None
    target_data = depth_data.get_data()
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




