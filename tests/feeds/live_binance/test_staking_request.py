# -*- coding: utf-8 -*-
"""
Tests for Binance Staking API Request Implementation
测试 Binance 质押理财 API 请求实现
"""

import queue
from bt_api_py.feeds.live_binance.staking import BinanceRequestDataStaking


def test_staking_request_init():
    """测试 Staking Request 初始化"""
    data_queue = queue.Queue()
    staking = BinanceRequestDataStaking(
        data_queue,
        public_key="test_public_key",
        private_key="test_private_key"
    )
    assert staking.asset_type == 'STAKING'
    assert staking.logger_name == 'binance_staking_feed.log'
    assert staking.exchange_name == 'binance_staking'
    assert staking._params.rest_url == 'https://api.binance.com'


def test_staking_request_has_methods():
    """测试 Staking Request 有所有必需方法"""
    data_queue = queue.Queue()
    staking = BinanceRequestDataStaking(data_queue)

    required_methods = [
        'get_staking_products',
        'staking_purchase',
        'staking_redeem',
        'get_staking_position',
        'get_staking_history',
    ]

    for method in required_methods:
        assert hasattr(staking, method), f"Missing method: {method}"
        assert callable(getattr(staking, method)), f"Method not callable: {method}"


def test_staking_request_get_staking_products_params():
    """测试 get_staking_products 参数构建"""
    data_queue = queue.Queue()
    staking = BinanceRequestDataStaking(
        data_queue,
        public_key="test_key",
        private_key="test_secret"
    )

    path, params, extra_data = staking._get_staking_products(
        product_type='STAKING',
        asset='USDT'
    )

    assert path == 'GET /sapi/v1/staking/productList'
    assert params['type'] == 'STAKING'
    assert params['asset'] == 'USDT'


def test_staking_request_staking_purchase_params():
    """测试 staking_purchase 参数构建"""
    data_queue = queue.Queue()
    staking = BinanceRequestDataStaking(data_queue)

    path, params, extra_data = staking._staking_purchase(
        product_id=12345,
        amount=100
    )

    assert path == 'POST /sapi/v1/staking/purchase'
    assert params['productId'] == 12345
    assert params['amount'] == 100


def test_staking_request_staking_redeem_params():
    """测试 staking_redeem 参数构建"""
    data_queue = queue.Queue()
    staking = BinanceRequestDataStaking(data_queue)

    path, params, extra_data = staking._staking_redeem(
        product_id=12345,
        amount=100,
        position_id=67890
    )

    assert path == 'POST /sapi/v1/staking/redeem'
    assert params['productId'] == 12345
    assert params['amount'] == 100
    assert params['positionId'] == 67890


def test_staking_request_get_staking_position_params():
    """测试 get_staking_position 参数构建"""
    data_queue = queue.Queue()
    staking = BinanceRequestDataStaking(data_queue)

    path, params, extra_data = staking._get_staking_position(
        product_type='STAKING',
        asset='USDT'
    )

    assert path == 'GET /sapi/v1/staking/position'
    assert params['type'] == 'STAKING'
    assert params['asset'] == 'USDT'


def test_staking_request_get_staking_history_params():
    """测试 get_staking_history 参数构建"""
    data_queue = queue.Queue()
    staking = BinanceRequestDataStaking(data_queue)

    path, params, extra_data = staking._get_staking_history(
        product_type='STAKING',
        asset='USDT'
    )

    assert path == 'GET /sapi/v1/staking/stakingRecord'
    assert params['type'] == 'STAKING'
    assert params['asset'] == 'USDT'


if __name__ == "__main__":
    import pytest
    pytest.main([__file__, "-v"])
