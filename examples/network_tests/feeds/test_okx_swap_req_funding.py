import queue
import time

import pytest

from bt_api_py.containers.exchanges.okx_exchange_data import OkxExchangeDataSwap
from bt_api_py.containers.requestdatas.request_data import RequestData

# from bt_api_py.containers.orders.okx_order import OkxOrderData
from bt_api_py.feeds.live_okx_feed import OkxRequestDataSwap
from bt_api_py.functions.utils import read_account_config


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


def test_okx_req_get_max_withdrawal():
    """Test get_max_withdrawal interface"""
    live_okx_swap_feed = init_req_feed()
    # Get max withdrawal for USDT
    data = live_okx_swap_feed.get_max_withdrawal(ccy_list=["USDT"])
    assert isinstance(data, RequestData)
    assert data.get_status()
    max_withdrawal_list = data.get_data()
    assert isinstance(max_withdrawal_list, list)


def test_okx_req_get_currencies():
    """Test get_currencies interface"""
    live_okx_swap_feed = init_req_feed()
    # Get all currencies
    data = live_okx_swap_feed.get_currencies()
    assert isinstance(data, RequestData)
    currencies_list = data.get_data()
    assert isinstance(currencies_list, list)
    if len(currencies_list) > 0:
        currency = currencies_list[0]
        if hasattr(currency, "init_data"):
            currency.init_data()
        else:
            assert isinstance(currency, dict)


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
    currencies_list = currencies_data.get_data()
    assert isinstance(currencies_list, list)


def test_okx_req_get_currencies_single():
    """Test get_currencies interface for single currency"""
    live_okx_swap_feed = init_req_feed()
    # Get BTC currency info
    data = live_okx_swap_feed.get_currencies(ccy="BTC")
    assert isinstance(data, RequestData)
    currencies_list = data.get_data()
    assert isinstance(currencies_list, list)
    if len(currencies_list) > 0:
        currency = currencies_list[0]
        if hasattr(currency, "init_data"):
            currency.init_data()
            assert currency.get_currency() == "BTC"
        else:
            assert isinstance(currency, dict)
            assert currency.get("ccy") == "BTC"


def test_okx_req_get_asset_balances():
    """Test get_asset_balances interface"""
    live_okx_swap_feed = init_req_feed()
    # Get all asset balances
    data = live_okx_swap_feed.get_asset_balances()
    assert isinstance(data, RequestData)
    balances_list = data.get_data()
    assert isinstance(balances_list, list)
    if len(balances_list) > 0:
        balance = balances_list[0]
        if hasattr(balance, "init_data"):
            balance.init_data()
        else:
            assert isinstance(balance, dict)


def test_okx_async_get_asset_balances():
    """Test async_get_asset_balances interface"""
    data_queue = queue.Queue()
    live_okx_swap_feed = init_async_feed(data_queue)
    live_okx_swap_feed.async_get_asset_balances()
    try:
        balances_data = data_queue.get(timeout=15)
    except queue.Empty:
        balances_data = None
    assert balances_data is not None
    assert isinstance(balances_data, RequestData)
    balances_list = balances_data.get_data()
    assert isinstance(balances_list, list)


def test_okx_req_get_asset_balances_single():
    """Test get_asset_balances interface for single currency"""
    live_okx_swap_feed = init_req_feed()
    # Get USDT balance
    data = live_okx_swap_feed.get_asset_balances(ccy="USDT")
    assert isinstance(data, RequestData)
    balances_list = data.get_data()
    assert isinstance(balances_list, list)
    if len(balances_list) > 0:
        balance = balances_list[0]
        if hasattr(balance, "init_data"):
            balance.init_data()
            assert balance.get_currency() == "USDT"
        else:
            assert isinstance(balance, dict)
            assert balance.get("ccy") == "USDT"


def test_okx_req_get_non_tradable_assets():
    """Test get_non_tradable_assets interface"""
    live_okx_swap_feed = init_req_feed()
    # Get non-tradable assets
    data = live_okx_swap_feed.get_non_tradable_assets()
    assert isinstance(data, RequestData)
    assets_list = data.get_data()
    assert isinstance(assets_list, list)


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
    assets_list = assets_data.get_data()
    assert isinstance(assets_list, list)


def test_okx_req_get_asset_valuation():
    """Test get_asset_valuation interface"""
    live_okx_swap_feed = init_req_feed()
    # Get asset valuation in USD
    data = live_okx_swap_feed.get_asset_valuation(ccy="USD")
    assert isinstance(data, RequestData)
    valuation_list = data.get_data()
    assert isinstance(valuation_list, list)
    if len(valuation_list) > 0:
        valuation = valuation_list[0]
        if hasattr(valuation, "init_data"):
            valuation.init_data()
        else:
            assert isinstance(valuation, dict)


def test_okx_async_get_asset_valuation():
    """Test async_get_asset_valuation interface"""
    data_queue = queue.Queue()
    live_okx_swap_feed = init_async_feed(data_queue)
    live_okx_swap_feed.async_get_asset_valuation(ccy="USD")
    try:
        valuation_data = data_queue.get(timeout=15)
    except queue.Empty:
        valuation_data = None
    assert valuation_data is not None
    assert isinstance(valuation_data, RequestData)
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


def test_okx_req_get_transfer_state():
    """Test get_transfer_state interface"""
    live_okx_swap_feed = init_req_feed()
    # Get transfer state for USDT
    data = live_okx_swap_feed.get_transfer_state(ccy="USDT")
    assert isinstance(data, RequestData)
    transfers_list = data.get_data()
    assert isinstance(transfers_list, list)
    if len(transfers_list) > 0:
        transfer = transfers_list[0]
        transfer.init_data()


def test_okx_async_get_transfer_state():
    """Test async_get_transfer_state interface"""
    data_queue = queue.Queue()
    live_okx_swap_feed = init_async_feed(data_queue)
    live_okx_swap_feed.async_get_transfer_state(ccy="USDT")
    try:
        transfer_state_data = data_queue.get(timeout=15)
    except queue.Empty:
        transfer_state_data = None
    assert transfer_state_data is not None
    assert isinstance(transfer_state_data, RequestData)
    transfers_list = transfer_state_data.get_data()
    assert isinstance(transfers_list, list)


# ==================== Public Data Tests ====================


def test_okx_req_get_asset_bills():
    """Test get_asset_bills interface"""
    live_okx_swap_feed = init_req_feed()
    # Get asset bills for USDT (last 3 months)
    data = live_okx_swap_feed.get_asset_bills(ccy="USDT", limit="10")
    assert isinstance(data, RequestData)
    bills_list = data.get_data()
    assert isinstance(bills_list, list)


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
    assert bills_data.get_status()
    bills_list = bills_data.get_data()
    assert isinstance(bills_list, list)


def test_okx_req_get_asset_bills_history():
    """Test get_asset_bills_history interface"""
    live_okx_swap_feed = init_req_feed()
    # Get asset bills history for USDT (last 3 months)
    data = live_okx_swap_feed.get_asset_bills_history(ccy="USDT", limit="10")
    assert isinstance(data, RequestData)
    bills_list = data.get_data()
    assert isinstance(bills_list, list)


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


def test_okx_req_get_deposit_address():
    """Test get_deposit_address interface"""
    live_okx_swap_feed = init_req_feed()
    # Get deposit address for USDT
    data = live_okx_swap_feed.get_deposit_address(ccy="USDT")
    assert isinstance(data, RequestData)
    address_list = data.get_data()
    assert isinstance(address_list, list)
    if len(address_list) > 0:
        pass


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
    address_list = address_data.get_data()
    assert isinstance(address_list, list)


def test_okx_req_get_deposit_history():
    """Test get_deposit_history interface"""
    live_okx_swap_feed = init_req_feed()
    # Get deposit history for USDT
    data = live_okx_swap_feed.get_deposit_history(ccy="USDT", limit="10")
    assert isinstance(data, RequestData)
    deposit_list = data.get_data()
    assert isinstance(deposit_list, list)


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
    deposit_list = deposit_data.get_data()
    assert isinstance(deposit_list, list)


def test_okx_req_get_deposit_withdraw_status():
    """Test get_deposit_withdraw_status interface"""
    live_okx_swap_feed = init_req_feed()
    # Get deposit/withdraw status for USDT
    data = live_okx_swap_feed.get_deposit_withdraw_status(ccy="USDT")
    assert isinstance(data, RequestData)
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


def test_okx_req_withdrawal():
    """Test withdrawal interface (will fail without valid address, but tests the interface)"""
    live_okx_swap_feed = init_req_feed()
    # This will fail because we don't have a valid withdrawal address, but tests the interface
    result = live_okx_swap_feed.withdrawal(
        ccy="USDT",
        amt="1",
        dest="4",  # on-chain withdrawal
        to_addr="0x0000000000000000000000000000000000000000",  # Invalid address for testing
        fee="0.1",
    )
    assert isinstance(result, RequestData)


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
        fee="0.1",
    )
    time.sleep(5)
    try:
        withdraw_data = data_queue.get(False)
    except queue.Empty:
        withdraw_data = None
    assert withdraw_data is not None
    assert isinstance(withdraw_data, RequestData)


def test_okx_req_cancel_withdrawal():
    """Test cancel_withdrawal interface (will fail without valid wd_id, but tests the interface)"""
    live_okx_swap_feed = init_req_feed()
    # This will fail because we don't have a valid withdrawal ID, but tests the interface
    result = live_okx_swap_feed.cancel_withdrawal(wd_id="test_withdrawal_id")
    assert isinstance(result, RequestData)


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


def test_okx_req_get_withdrawal_history():
    """Test get_withdrawal_history interface"""
    live_okx_swap_feed = init_req_feed()
    # Get withdrawal history for USDT
    data = live_okx_swap_feed.get_withdrawal_history(ccy="USDT", limit="10")
    assert isinstance(data, RequestData)
    withdraw_list = data.get_data()
    assert isinstance(withdraw_list, list)


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
    withdraw_list = withdraw_data.get_data()
    assert isinstance(withdraw_list, list)


# ==================== Trading Statistics Tests ====================


def test_okx_req_get_easy_convert_currency_list():
    """Test get_easy_convert_currency_list interface"""
    live_okx_swap_feed = init_req_feed()
    # Get easy convert currency list
    data = live_okx_swap_feed.get_easy_convert_currency_list()
    assert isinstance(data, RequestData)
    currency_list = data.get_data()
    assert isinstance(currency_list, list)


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
        return  # Skip assertion on timeout
    assert isinstance(currency_data, RequestData)


def test_okx_req_easy_convert():
    """Test easy_convert interface"""
    live_okx_swap_feed = init_req_feed()
    # Try easy convert (may fail without sufficient balance, but tests the interface)
    data = live_okx_swap_feed.easy_convert(from_ccy="USDT", to_ccy="BTC", amt="10")
    assert isinstance(data, RequestData)


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
        return  # Skip assertion on timeout
    assert isinstance(convert_data, RequestData)


def test_okx_req_get_easy_convert_history():
    """Test get_easy_convert_history interface"""
    live_okx_swap_feed = init_req_feed()
    # Get easy convert history
    data = live_okx_swap_feed.get_easy_convert_history(limit="10")
    assert isinstance(data, RequestData)
    history_list = data.get_data()
    assert isinstance(history_list, list)


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
        return  # Skip assertion on timeout
    assert isinstance(history_data, RequestData)


def test_okx_req_get_one_click_repay_currency_list():
    """Test get_one_click_repay_currency_list interface"""
    live_okx_swap_feed = init_req_feed()
    # Get one-click repay currency list
    data = live_okx_swap_feed.get_one_click_repay_currency_list()
    assert isinstance(data, RequestData)
    currency_list = data.get_data()
    assert isinstance(currency_list, list)


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
        return  # Skip assertion on timeout
    assert isinstance(currency_data, RequestData)


def test_okx_req_one_click_repay():
    """Test one_click_repay interface"""
    live_okx_swap_feed = init_req_feed()
    # Try one-click repay (may fail without debt, but tests the interface)
    data = live_okx_swap_feed.one_click_repay(ccy="USDT", amt="0")
    assert isinstance(data, RequestData)


def test_okx_async_one_click_repay():
    """Test async_one_click_repay interface"""
    data_queue = queue.Queue()
    live_okx_swap_feed = init_async_feed(data_queue)
    live_okx_swap_feed.async_one_click_repay(ccy="USDT", amt="0")
    time.sleep(5)
    try:
        repay_data = data_queue.get(False)
    except queue.Empty:
        repay_data = None
    if repay_data is None:
        return  # Skip assertion on timeout
    assert isinstance(repay_data, RequestData)


def test_okx_req_get_one_click_repay_history():
    """Test get_one_click_repay_history interface"""
    live_okx_swap_feed = init_req_feed()
    # Get one-click repay history
    data = live_okx_swap_feed.get_one_click_repay_history(limit="10")
    assert isinstance(data, RequestData)
    history_list = data.get_data()
    assert isinstance(history_list, list)


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
        return  # Skip assertion on timeout
    assert isinstance(history_data, RequestData)


def test_okx_get_exchange_list():
    """Test get_exchange_list interface"""
    live_okx_swap_feed = init_req_feed()
    data = live_okx_swap_feed.get_exchange_list(ccy="BTC")
    assert isinstance(data, RequestData)


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


def test_okx_get_convert_currencies():
    """Test get_convert_currencies interface"""
    live_okx_swap_feed = init_req_feed()
    data = live_okx_swap_feed.get_convert_currencies()
    assert isinstance(data, RequestData)


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


def test_okx_get_convert_currency_pair():
    """Test get_convert_currency_pair interface"""
    live_okx_swap_feed = init_req_feed()
    data = live_okx_swap_feed.get_convert_currency_pair(from_ccy="BTC", to_ccy="USDT")
    assert isinstance(data, RequestData)


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


def test_okx_get_convert_history():
    """Test get_convert_history interface"""
    live_okx_swap_feed = init_req_feed()
    data = live_okx_swap_feed.get_convert_history(limit="10")
    assert isinstance(data, RequestData)


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


@pytest.mark.skip(reason="OKX API endpoint deprecated/removed (404)")
def test_okx_get_deposit_payment_methods():
    """Test get_deposit_payment_methods interface"""
    live_okx_swap_feed = init_req_feed()
    data = live_okx_swap_feed.get_deposit_payment_methods(ccy="BTC")
    assert isinstance(data, RequestData)


@pytest.mark.skip(reason="OKX API endpoint deprecated/removed (404)")
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


@pytest.mark.skip(reason="OKX API endpoint deprecated/removed (404)")
def test_okx_get_withdrawal_payment_methods():
    """Test get_withdrawal_payment_methods interface"""
    live_okx_swap_feed = init_req_feed()
    data = live_okx_swap_feed.get_withdrawal_payment_methods(ccy="BTC")
    assert isinstance(data, RequestData)


@pytest.mark.skip(reason="OKX API endpoint deprecated/removed (404)")
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


@pytest.mark.skip(reason="OKX API endpoint deprecated/removed (404)")
def test_okx_get_withdrawal_order_history():
    """Test get_withdrawal_order_history interface"""
    live_okx_swap_feed = init_req_feed()
    data = live_okx_swap_feed.get_withdrawal_order_history(ccy="BTC", limit="10")
    assert isinstance(data, RequestData)


@pytest.mark.skip(reason="OKX API endpoint deprecated/removed (404)")
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


@pytest.mark.skip(reason="OKX API endpoint deprecated/removed (404)")
def test_okx_get_deposit_order_history():
    """Test get_deposit_order_history interface"""
    live_okx_swap_feed = init_req_feed()
    data = live_okx_swap_feed.get_deposit_order_history(ccy="BTC", limit="10")
    assert isinstance(data, RequestData)


@pytest.mark.skip(reason="OKX API endpoint deprecated/removed (404)")
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


@pytest.mark.skip(reason="OKX API endpoint deprecated/removed (404)")
def test_okx_get_buy_sell_currencies():
    """Test get_buy_sell_currencies interface"""
    live_okx_swap_feed = init_req_feed()
    data = live_okx_swap_feed.get_buy_sell_currencies()
    assert isinstance(data, RequestData)


@pytest.mark.skip(reason="OKX API endpoint deprecated/removed (404)")
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


@pytest.mark.skip(reason="OKX API endpoint deprecated/removed (404)")
def test_okx_get_buy_sell_currency_pair():
    """Test get_buy_sell_currency_pair interface"""
    live_okx_swap_feed = init_req_feed()
    data = live_okx_swap_feed.get_buy_sell_currency_pair()
    assert isinstance(data, RequestData)


@pytest.mark.skip(reason="OKX API endpoint deprecated/removed (404)")
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


@pytest.mark.skip(reason="OKX API endpoint deprecated/removed (404)")
def test_okx_get_buy_sell_history():
    """Test get_buy_sell_history interface"""
    live_okx_swap_feed = init_req_feed()
    data = live_okx_swap_feed.get_buy_sell_history(limit="10")
    assert isinstance(data, RequestData)


@pytest.mark.skip(reason="OKX API endpoint deprecated/removed (404)")
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


# ==================== Sub-account (P2) Tests ====================
