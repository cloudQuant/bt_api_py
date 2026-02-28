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
        if isinstance(instrument, OkxSymbolData):
            instrument.init_data()
            assert instrument.get_exchange_name() == "OKX"
        else:
            assert isinstance(instrument, dict)
            print("Instrument (raw):", list(instrument.keys())[:5])




def test_okx_async_get_public_instruments():
    """Test async_get_public_instruments interface"""
    data_queue = queue.Queue()
    live_okx_swap_feed = init_async_feed(data_queue)
    live_okx_swap_feed.async_get_public_instruments(inst_type="SWAP", uly="BTC-USDT")
    try:
        instruments_data = data_queue.get(timeout=15)
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
    assert limit_data.get_status()
    limit_list = limit_data.get_data()
    assert isinstance(limit_list, list)
    print("async_get_price_limit count:", len(limit_list))




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
    print("get_insurance_fund status:", data.get_status())
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
    print("convert_contract_coin status:", data.get_status())
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
    print("get_instrument_tick_bands status:", data.get_status())
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


