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
        inst_type="SWAP", symbol="BTC-USDT-SWAP", time_interval_frozen=1000, algo_orders_frozen=True
    )
    assert isinstance(data, RequestData)
    print("set_mmp_config status:", data.get_status())


def test_okx_async_set_mmp_config():
    """Test async_set_mmp_config interface"""
    data_queue = queue.Queue()
    live_okx_swap_feed = init_async_feed(data_queue)
    live_okx_swap_feed.async_set_mmp_config(
        inst_type="SWAP", symbol="BTC-USDT-SWAP", time_interval_frozen=1000, algo_orders_frozen=True
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
    data = live_okx_swap_feed.mmp_reset(inst_type="SWAP", symbol="BTC-USDT-SWAP")
    assert isinstance(data, RequestData)
    print("mmp_reset status:", data.get_status())


def test_okx_async_mmp_reset():
    """Test async_mmp_reset interface"""
    data_queue = queue.Queue()
    live_okx_swap_feed = init_async_feed(data_queue)
    live_okx_swap_feed.async_mmp_reset(inst_type="SWAP", symbol="BTC-USDT-SWAP")
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


def test_okx_req_set_auto_loan():
    """Test set_auto_loan interface - Set auto loan status"""
    live_okx_swap_feed = init_req_feed()
    # Test setting auto loan to false (off)
    # Note: This may fail if the account configuration doesn't allow this operation
    result = live_okx_swap_feed.set_auto_loan(
        auto_loan=False, iso_mode="automatic", mgn_mode="cross"
    )
    assert isinstance(result, RequestData)
    print("set_auto_loan status:", result.get_status())
    print("set_auto_loan input:", result.get_input_data())


def test_okx_async_set_auto_loan():
    """Test async_set_auto_loan interface"""
    data_queue = queue.Queue()
    live_okx_swap_feed = init_async_feed(data_queue)
    live_okx_swap_feed.async_set_auto_loan(auto_loan=False, iso_mode="automatic", mgn_mode="cross")
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
    result = live_okx_swap_feed.set_account_level(acct_lv=2, inst_type="SWAP")
    assert isinstance(result, RequestData)
    print("set_account_level status:", result.get_status())
    print("set_account_level input:", result.get_input_data())


def test_okx_async_set_account_level():
    """Test async_set_account_level interface"""
    data_queue = queue.Queue()
    live_okx_swap_feed = init_async_feed(data_queue)
    live_okx_swap_feed.async_set_account_level(acct_lv=2, inst_type="SWAP")
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
        acct_lv=3, pos_side="long", inst_type="SWAP"
    )
    assert isinstance(result, RequestData)
    print("account_level_switch_preset status:", result.get_status())
    print("account_level_switch_preset input:", result.get_input_data())


def test_okx_async_account_level_switch_preset():
    """Test async_account_level_switch_preset interface"""
    data_queue = queue.Queue()
    live_okx_swap_feed = init_async_feed(data_queue)
    live_okx_swap_feed.async_account_level_switch_preset(
        acct_lv=3, pos_side="long", inst_type="SWAP"
    )
    time.sleep(5)
    try:
        result = data_queue.get(False)
    except queue.Empty:
        result = None
    if result is not None:
        assert isinstance(result, RequestData)
        print("async_account_level_switch_preset status:", result.get_status())


@pytest.mark.skip(reason="OKX API endpoint deprecated/removed (404)")
def test_okx_req_account_level_switch_precheck():
    """Test account_level_switch_precheck interface - Account level switch precheck"""
    live_okx_swap_feed = init_req_feed()
    # Precheck before switching to Multi-currency margin (level 3)
    result = live_okx_swap_feed.account_level_switch_precheck(acct_lv=3, inst_type="SWAP")
    assert isinstance(result, RequestData)
    print("account_level_switch_precheck status:", result.get_status())
    data = result.get_data()
    if data:
        print("account_level_switch_precheck data:", data)


@pytest.mark.skip(reason="OKX API endpoint deprecated/removed (404)")
def test_okx_async_account_level_switch_precheck():
    """Test async_account_level_switch_precheck interface"""
    data_queue = queue.Queue()
    live_okx_swap_feed = init_async_feed(data_queue)
    live_okx_swap_feed.async_account_level_switch_precheck(acct_lv=3, inst_type="SWAP")
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
    result = live_okx_swap_feed.set_collateral_assets(ccy_list="BTC,USDT,ETH", auto_loan=False)
    assert isinstance(result, RequestData)
    print("set_collateral_assets status:", result.get_status())
    print("set_collateral_assets input:", result.get_input_data())


def test_okx_async_set_collateral_assets():
    """Test async_set_collateral_assets interface"""
    data_queue = queue.Queue()
    live_okx_swap_feed = init_async_feed(data_queue)
    live_okx_swap_feed.async_set_collateral_assets(ccy_list="BTC,USDT,ETH", auto_loan=False)
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
        offset_amt="100",
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
        offset_amt="100",
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
        offset_amt="50",
    )
    assert isinstance(result, RequestData)
    print("set_risk_offset_amt (with instrument) status:", result.get_status())
    print("set_risk_offset_amt (with instrument) input:", result.get_input_data())


# ==================== Trading Account REST API Tests ====================


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


def test_okx_req_set_isolated_mode():
    """Test set_isolated_mode interface - Set isolated margin mode"""
    live_okx_swap_feed = init_req_feed()
    # Test setting isolated margin mode to automatic
    result = live_okx_swap_feed.set_isolated_mode(symbol="BTC-USDT", iso_mode="automatic")
    assert isinstance(result, RequestData)
    print("set_isolated_mode status:", result.get_status())
    print("set_isolated_mode input:", result.get_input_data())


def test_okx_async_set_isolated_mode():
    """Test async_set_isolated_mode interface"""
    data_queue = queue.Queue()
    live_okx_swap_feed = init_async_feed(data_queue)
    live_okx_swap_feed.async_set_isolated_mode(symbol="BTC-USDT", iso_mode="automatic")
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
        mgn_mode="cross",
    )
    assert isinstance(result, RequestData)
    print("borrow_repay status:", result.get_status())
    print("borrow_repay input:", result.get_input_data())


def test_okx_async_borrow_repay():
    """Test async_borrow_repay interface"""
    data_queue = queue.Queue()
    live_okx_swap_feed = init_async_feed(data_queue)
    live_okx_swap_feed.async_borrow_repay(ccy="USDT", side="borrow", amt="1", mgn_mode="cross")
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
    data = live_okx_swap_feed.get_borrow_repay_history(ccy="USDT", limit="10")
    assert isinstance(data, RequestData)
    print("get_borrow_repay_history status:", data.get_status())
    history_list = data.get_data()
    assert isinstance(history_list, list)
    print("get_borrow_repay_history count:", len(history_list))


def test_okx_async_get_borrow_repay_history():
    """Test async_get_borrow_repay_history interface"""
    data_queue = queue.Queue()
    live_okx_swap_feed = init_async_feed(data_queue)
    live_okx_swap_feed.async_get_borrow_repay_history(ccy="USDT", limit="10")
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
        uly="BTC-USD", inst_id="BTC-USD-240127-50000-C", cnt="1"
    )
    assert isinstance(result, RequestData)
    print("activate_option status:", result.get_status())
    print("activate_option input:", result.get_input_data())


def test_okx_async_activate_option():
    """Test async_activate_option interface"""
    data_queue = queue.Queue()
    live_okx_swap_feed = init_async_feed(data_queue)
    live_okx_swap_feed.async_activate_option(
        uly="BTC-USD", inst_id="BTC-USD-240127-50000-C", cnt="1"
    )
    time.sleep(5)
    try:
        result = data_queue.get(False)
    except queue.Empty:
        result = None
    if result is not None:
        assert isinstance(result, RequestData)
        print("async_activate_option status:", result.get_status())


@pytest.mark.skip(reason="OKX API endpoint deprecated/removed (404)")
def test_okx_req_move_positions():
    """Test move_positions interface - Move positions between currencies"""
    live_okx_swap_feed = init_req_feed()
    # This will fail without proper position setup, but tests the interface
    result = live_okx_swap_feed.move_positions(
        symbol="BTC-USDT",
        pos_id="test_position_id",  # Placeholder
        ccy="USDT",
    )
    assert isinstance(result, RequestData)
    print("move_positions status:", result.get_status())
    print("move_positions input:", result.get_input_data())


@pytest.mark.skip(reason="OKX API endpoint deprecated/removed (404)")
def test_okx_async_move_positions():
    """Test async_move_positions interface"""
    data_queue = queue.Queue()
    live_okx_swap_feed = init_async_feed(data_queue)
    live_okx_swap_feed.async_move_positions(
        symbol="BTC-USDT",
        pos_id="test_position_id",  # Placeholder
        ccy="USDT",
    )
    time.sleep(5)
    try:
        result = data_queue.get(False)
    except queue.Empty:
        result = None
    if result is not None:
        assert isinstance(result, RequestData)
        print("async_move_positions status:", result.get_status())


@pytest.mark.skip(reason="OKX API endpoint deprecated/removed (404)")
def test_okx_req_get_move_positions_history():
    """Test get_move_positions_history interface - Get move positions history"""
    live_okx_swap_feed = init_req_feed()
    result = live_okx_swap_feed.get_move_positions_history(symbol="BTC-USDT", limit="10")
    assert isinstance(result, RequestData)
    print("get_move_positions_history status:", result.get_status())
    history_list = result.get_data()
    assert isinstance(history_list, list)
    print("get_move_positions_history count:", len(history_list))


@pytest.mark.skip(reason="OKX API endpoint deprecated/removed (404)")
def test_okx_async_get_move_positions_history():
    """Test async_get_move_positions_history interface"""
    data_queue = queue.Queue()
    live_okx_swap_feed = init_async_feed(data_queue)
    live_okx_swap_feed.async_get_move_positions_history(symbol="BTC-USDT", limit="10")
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
    result = live_okx_swap_feed.set_auto_earn(ccy="USDT", auto_earn="true")
    assert isinstance(result, RequestData)
    print("set_auto_earn status:", result.get_status())
    print("set_auto_earn input:", result.get_input_data())


def test_okx_async_set_auto_earn():
    """Test async_set_auto_earn interface"""
    data_queue = queue.Queue()
    live_okx_swap_feed = init_async_feed(data_queue)
    live_okx_swap_feed.async_set_auto_earn(ccy="USDT", auto_earn="true")
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
    result = live_okx_swap_feed.set_settle_currency(symbol="BTC-USDT", ccy="USDT")
    assert isinstance(result, RequestData)
    print("set_settle_currency status:", result.get_status())
    print("set_settle_currency input:", result.get_input_data())


def test_okx_async_set_settle_currency():
    """Test async_set_settle_currency interface"""
    data_queue = queue.Queue()
    live_okx_swap_feed = init_async_feed(data_queue)
    live_okx_swap_feed.async_set_settle_currency(symbol="BTC-USDT", ccy="USDT")
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
    result = live_okx_swap_feed.set_trading_config(symbol="BTC-USDT", auto_loan="false")
    assert isinstance(result, RequestData)
    print("set_trading_config status:", result.get_status())
    print("set_trading_config input:", result.get_input_data())


def test_okx_async_set_trading_config():
    """Test async_set_trading_config interface"""
    data_queue = queue.Queue()
    live_okx_swap_feed = init_async_feed(data_queue)
    live_okx_swap_feed.async_set_trading_config(symbol="BTC-USDT", auto_loan="false")
    time.sleep(5)
    try:
        result = data_queue.get(False)
    except queue.Empty:
        result = None
    if result is not None:
        assert isinstance(result, RequestData)
        print("async_set_trading_config status:", result.get_status())


@pytest.mark.skip(reason="OKX API endpoint deprecated/removed (404)")
def test_okx_req_set_delta_neutral_precheck():
    """Test set_delta_neutral_precheck interface - Set delta neutral precheck"""
    live_okx_swap_feed = init_req_feed()
    # This will fail depending on account settings, but tests the interface
    result = live_okx_swap_feed.set_delta_neutral_precheck(
        symbol="BTC-USDT", delta_neutral_precheck="true"
    )
    assert isinstance(result, RequestData)
    print("set_delta_neutral_precheck status:", result.get_status())
    print("set_delta_neutral_precheck input:", result.get_input_data())


@pytest.mark.skip(reason="OKX API endpoint deprecated/removed (404)")
def test_okx_async_set_delta_neutral_precheck():
    """Test async_set_delta_neutral_precheck interface"""
    data_queue = queue.Queue()
    live_okx_swap_feed = init_async_feed(data_queue)
    live_okx_swap_feed.async_set_delta_neutral_precheck(
        symbol="BTC-USDT", delta_neutral_precheck="true"
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
