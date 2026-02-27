# -*- coding: utf-8 -*-
"""
Tests for Binance Margin API Request Implementation
测试 Binance 杠杆 API 请求实现
"""

import queue
import pytest
from bt_api_py.feeds.live_binance.margin import BinanceRequestDataMargin
from bt_api_py.containers.requestdatas.request_data import RequestData

pytestmark = pytest.mark.xdist_group("binance_api")


def test_margin_request_init():
    """测试 Margin Request 初始化"""
    data_queue = queue.Queue()
    margin = BinanceRequestDataMargin(data_queue)
    assert margin.asset_type == 'MARGIN'
    assert margin.logger_name == 'binance_margin_feed.log'
    assert margin._params.exchange_name == 'binance_margin'


def test_margin_has_cross_margin_data():
    """测试有全仓保证金数据方法"""
    data_queue = queue.Queue()
    margin = BinanceRequestDataMargin(data_queue)
    assert hasattr(margin, 'get_cross_margin_data')
    assert hasattr(margin, '_get_cross_margin_data')


def test_margin_has_isolated_margin_data():
    """测试有逐仓保证金数据方法"""
    data_queue = queue.Queue()
    margin = BinanceRequestDataMargin(data_queue)
    assert hasattr(margin, 'get_isolated_margin_data')
    assert hasattr(margin, '_get_isolated_margin_data')


def test_margin_has_capital_flow():
    """测试有资金流水方法"""
    data_queue = queue.Queue()
    margin = BinanceRequestDataMargin(data_queue)
    assert hasattr(margin, 'get_capital_flow')
    assert hasattr(margin, '_get_capital_flow')


def test_margin_has_bnb_burn_methods():
    """测试有BNB抵扣方法"""
    data_queue = queue.Queue()
    margin = BinanceRequestDataMargin(data_queue)
    assert hasattr(margin, 'get_bnb_burn')
    assert hasattr(margin, 'toggle_bnb_burn')


def test_margin_has_liquidation_methods():
    """测试有清算方法"""
    data_queue = queue.Queue()
    margin = BinanceRequestDataMargin(data_queue)
    assert hasattr(margin, 'manual_liquidation')
    assert hasattr(margin, 'exchange_small_liability')
    assert hasattr(margin, 'get_small_liability_history')


def test_margin_has_set_max_leverage():
    """测试有设置最大杠杆方法"""
    data_queue = queue.Queue()
    margin = BinanceRequestDataMargin(data_queue)
    assert hasattr(margin, 'set_max_leverage')


def test_margin_cross_margin_data_params():
    """测试全仓保证金数据参数构建"""
    data_queue = queue.Queue()
    margin = BinanceRequestDataMargin(data_queue)
    path, params, extra_data = margin._get_cross_margin_data()
    assert path == 'GET /sapi/v1/margin/crossMarginData'
    assert extra_data['request_type'] == 'get_cross_margin_data'


def test_margin_isolated_margin_data_params():
    """测试逐仓保证金数据参数构建"""
    data_queue = queue.Queue()
    margin = BinanceRequestDataMargin(data_queue)
    path, params, extra_data = margin._get_isolated_margin_data(symbols='BTCUSDT,ETHUSDT')
    assert path == 'GET /sapi/v1/margin/isolatedMarginData'
    assert params['symbols'] == 'BTCUSDT,ETHUSDT'


def test_margin_capital_flow_params():
    """测试资金流水参数构建"""
    data_queue = queue.Queue()
    margin = BinanceRequestDataMargin(data_queue)
    path, params, extra_data = margin._get_capital_flow(asset='USDT', limit=10)
    assert path == 'GET /sapi/v1/margin/capital-flow'
    assert params['asset'] == 'USDT'
    assert params['limit'] == 10


def test_margin_bnb_burn_params():
    """测试BNB抵扣参数构建"""
    data_queue = queue.Queue()
    margin = BinanceRequestDataMargin(data_queue)
    path, params, extra_data = margin._get_bnb_burn()
    assert path == 'GET /sapi/v1/bnbBurn'


def test_margin_toggle_bnb_burn_params():
    """测试开关BNB抵扣参数构建"""
    data_queue = queue.Queue()
    margin = BinanceRequestDataMargin(data_queue)
    path, params, extra_data = margin._toggle_bnb_burn()
    assert path == 'POST /sapi/v1/bnbBurn'


def test_margin_manual_liquidation_params():
    """测试手动清算参数构建"""
    data_queue = queue.Queue()
    margin = BinanceRequestDataMargin(data_queue)
    path, params, extra_data = margin._manual_liquidation(symbol='BTC-USDT')
    assert path == 'POST /sapi/v1/margin/manual-liquidation'
    assert params['symbol'] == 'BTCUSDT'


def test_margin_exchange_small_liability_params():
    """测试小额负债兑换参数构建"""
    data_queue = queue.Queue()
    margin = BinanceRequestDataMargin(data_queue)
    path, params, extra_data = margin._exchange_small_liability(asset_names='BTC,ETH')
    assert path == 'POST /sapi/v1/margin/exchange-small-liability'
    assert params['assetNames'] == 'BTC,ETH'


def test_margin_small_liability_history_params():
    """测试小额负债兑换历史参数构建"""
    data_queue = queue.Queue()
    margin = BinanceRequestDataMargin(data_queue)
    path, params, extra_data = margin._get_small_liability_history(asset='USDT', limit=10)
    assert path == 'GET /sapi/v1/margin/exchange-small-liability-history'
    assert params['asset'] == 'USDT'
    assert params['limit'] == 10


def test_margin_set_max_leverage_params():
    """测试设置最大杠杆参数构建"""
    data_queue = queue.Queue()
    margin = BinanceRequestDataMargin(data_queue)
    path, params, extra_data = margin._set_max_leverage(max_leverage=10)
    assert path == 'POST /sapi/v1/margin/max-leverage'
    assert params['maxLeverage'] == 10


if __name__ == "__main__":
    import pytest
    pytest.main([__file__, "-v"])
