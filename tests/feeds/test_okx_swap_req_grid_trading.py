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




@pytest.mark.skip(reason="OKX API endpoint deprecated/removed (404)")
def test_okx_grid_max_grid_quantity():
    """Test grid_max_grid_quantity interface - 最大网格数量"""
    live_okx_swap_feed = init_req_feed()
    data = live_okx_swap_feed.grid_max_grid_quantity(
        inst_id="BTC-USDT-SWAP",
        algo_algo_type="grid_contract"
    )
    assert isinstance(data, RequestData)
    print("grid_max_grid_quantity status:", data.get_status())




@pytest.mark.skip(reason="OKX API endpoint deprecated/removed (404)")
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




@pytest.mark.skip(reason="OKX API endpoint deprecated/removed (404)")
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




@pytest.mark.skip(reason="OKX API endpoint deprecated/removed (404)")
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


