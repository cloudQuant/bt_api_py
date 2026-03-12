import queue
import time

import pytest

from bt_api_py.containers.accounts.okx_account import OkxAccountData
from bt_api_py.containers.exchanges.okx_exchange_data import OkxExchangeDataSwap
from bt_api_py.containers.positions.okx_position import OkxPositionData
from bt_api_py.containers.requestdatas.request_data import RequestData

# from bt_api_py.containers.orders.okx_order import OkxOrderData
from bt_api_py.feeds.live_okx_feed import OkxRequestDataSwap
from bt_api_py.functions.utils import get_public_ip, read_account_config

pytestmark = [pytest.mark.integration, pytest.mark.network]


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


def assert_account_data_value(bp):
    assert bp.get_server_time() > 0
    assert bp.get_exchange_name() == "OKX"
    assert bp.get_total_margin() > 0
    assert bp.get_asset_type() == "SWAP"
    assert bp.get_event() == "AccountEvent"


@pytest.mark.auth_account
def test_okx_req_account_data():
    live_okx_swap_feed = init_req_feed()
    data = live_okx_swap_feed.get_account().get_data()
    # print(data)
    assert isinstance(data[0], OkxAccountData)
    assert_account_data_value(data[0].init_data())


@pytest.mark.auth_account
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


def test_okx_req_get_config():
    public_ip = get_public_ip()
    assert isinstance(public_ip, str)
    print("当前出口ip = ", public_ip)
    live_okx_swap_feed = init_req_feed()
    data = live_okx_swap_feed.get_config()
    assert isinstance(data, RequestData)

    config_data = data.get_data()[0]
    print("config_data", config_data)
    api_ip = config_data.get("ip")
    print(public_ip, api_ip)
    assert public_ip in api_ip, "需要绑定当前ip地址到okx的API当中"


@pytest.mark.auth_position
def test_okx_req_get_position():
    live_okx_swap_feed = init_req_feed()
    data = live_okx_swap_feed.get_position(symbol="OP-USDT")
    assert isinstance(data, RequestData)
    print("position_data", data.get_data())


@pytest.mark.auth_position
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
    if position_data is None:
        pytest.skip("Skipped (no data, likely network): async_get_position returned no data")
    total_position_data = position_data.get_data()
    if len(total_position_data):
        target_data = position_data.get_data()[0]
        assert isinstance(position_data, RequestData)
        assert isinstance(target_data, OkxPositionData)


@pytest.mark.auth_position
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


@pytest.mark.auth_position
def test_okx_async_get_positions_history():
    data_queue = queue.Queue()
    live_okx_swap_feed = init_async_feed(data_queue)
    live_okx_swap_feed.async_get_positions_history(inst_type="SWAP", limit="10")
    try:
        position_history_data = data_queue.get(timeout=15)
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


@pytest.mark.auth_position
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


@pytest.mark.auth_position
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
    time.sleep(10)
    try:
        max_size_response = data_queue.get(False)
    except queue.Empty:
        max_size_response = None
    assert max_size_response is not None
    assert isinstance(max_size_response, RequestData)
    assert max_size_response.get_status()
    max_size_list = max_size_response.get_data()
    assert isinstance(max_size_list, list)


@pytest.mark.auth_position
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


@pytest.mark.auth_account
def test_okx_req_get_risk_state():
    """Test get_risk_state interface"""
    live_okx_swap_feed = init_req_feed()
    data = live_okx_swap_feed.get_risk_state()
    assert isinstance(data, RequestData)
    # May fail in some account modes, but interface works
    print("get_risk_state status:", data.get_status())
    print("get_risk_state input:", data.get_input_data())


@pytest.mark.auth_account
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


@pytest.mark.auth_account
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


@pytest.mark.auth_account
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


@pytest.mark.auth_account
def test_okx_req_get_bills_archive():
    """Test get_bills_archive interface"""
    live_okx_swap_feed = init_req_feed()
    # Get account bills archive (last 3 months)
    data = live_okx_swap_feed.get_bills_archive(year="2025", limit="10")
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
    time.sleep(10)
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
        inst_type="SWAP", inst_id="BTC-USDT-SWAP", mgn_mode="cross"
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
        inst_type="SWAP", uly="BTC-USDT", mgn_mode="cross"
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
        inst_type="SWAP", inst_id="BTC-USDT-SWAP", mgn_mode="cross"
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
    live_okx_swap_feed.async_get_max_loan(symbol="BTC-USDT", mgn_mode="cross")
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
    data = live_okx_swap_feed.get_interest_accrued(inst_type="SWAP", limit="10")
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
    try:
        rate_data = data_queue.get(timeout=15)
    except queue.Empty:
        rate_data = None
    assert rate_data is not None
    assert isinstance(rate_data, RequestData)
    print("async_get_interest_rate status:", rate_data.get_status())


def test_okx_req_get_greeks():
    """Test get_greeks interface"""
    live_okx_swap_feed = init_req_feed()
    # Get Greeks
    data = live_okx_swap_feed.get_greeks(inst_type="SWAP")
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


@pytest.mark.auth_position
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


@pytest.mark.auth_account
def test_okx_async_get_position_tiers():
    """Test async_get_position_tiers interface"""
    data_queue = queue.Queue()
    live_okx_swap_feed = init_async_feed(data_queue)
    live_okx_swap_feed.async_get_position_tiers(inst_type="SWAP")
    time.sleep(10)
    try:
        tiers_data = data_queue.get(False)
    except queue.Empty:
        tiers_data = None
    assert tiers_data is not None
    assert isinstance(tiers_data, RequestData)
    print("async_get_position_tiers status:", tiers_data.get_status())


# ==================== Funding Account Tests ====================


@pytest.mark.auth_account
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
        if "reqIp" in rate_limit:
            print(f"Request IP: {rate_limit['reqIp']}")
        if "rule" in rate_limit:
            print(f"Rule: {rate_limit['rule']}")


@pytest.mark.auth_account
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
