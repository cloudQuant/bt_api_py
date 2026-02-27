# -*- coding: utf-8 -*-
"""
Tests for Binance Mining API Request Implementation
测试 Binance 矿池 API 请求实现
"""

import queue
from bt_api_py.feeds.live_binance.mining import BinanceRequestDataMining


def test_mining_request_init():
    """测试 Mining Request 初始化"""
    data_queue = queue.Queue()
    mining = BinanceRequestDataMining(
        data_queue,
        public_key="test_public_key",
        private_key="test_private_key"
    )
    assert mining.asset_type == 'MINING'
    assert mining.logger_name == 'binance_mining_feed.log'
    assert mining.exchange_name == 'binance_mining'
    assert mining._params.rest_url == 'https://api.binance.com'


def test_mining_request_has_methods():
    """测试 Mining Request 有所有必需方法"""
    data_queue = queue.Queue()
    mining = BinanceRequestDataMining(data_queue)

    required_methods = [
        'get_mining_algo_list',
        'get_mining_worker_list',
        'get_mining_statistics',
    ]

    for method in required_methods:
        assert hasattr(mining, method), f"Missing method: {method}"
        assert callable(getattr(mining, method)), f"Method not callable: {method}"


def test_mining_request_get_mining_algo_list_params():
    """测试 get_mining_algo_list 参数构建"""
    data_queue = queue.Queue()
    mining = BinanceRequestDataMining(
        data_queue,
        public_key="test_key",
        private_key="test_secret"
    )

    path, params, extra_data = mining._get_mining_algo_list()

    assert path == 'GET /sapi/v1/mining/pub/algoList'
    assert params == {}
    assert extra_data['request_type'] == 'get_mining_algo_list'


def test_mining_request_get_mining_worker_list_params():
    """测试 get_mining_worker_list 参数构建"""
    data_queue = queue.Queue()
    mining = BinanceRequestDataMining(data_queue)

    path, params, extra_data = mining._get_mining_worker_list(
        algo='sha256',
        user_name='test_miner'
    )

    assert path == 'GET /sapi/v1/mining/worker/list'
    assert params['algo'] == 'sha256'
    assert params['userName'] == 'test_miner'


def test_mining_request_get_mining_statistics_params():
    """测试 get_mining_statistics 参数构建"""
    data_queue = queue.Queue()
    mining = BinanceRequestDataMining(data_queue)

    path, params, extra_data = mining._get_mining_statistics(
        algo='sha256',
        user_name='test_miner'
    )

    assert path == 'GET /sapi/v1/mining/statistics/user/status'
    assert params['algo'] == 'sha256'
    assert params['userName'] == 'test_miner'


if __name__ == "__main__":
    import pytest
    pytest.main([__file__, "-v"])
