# -*- coding: utf-8 -*-
"""
Tests for Binance Sub-account API Request Implementation
测试 Binance 子账户管理 API 请求实现
"""

import queue
from bt_api_py.feeds.live_binance.sub_account import BinanceRequestDataSubAccount


def test_sub_account_request_init():
    """测试 Sub-account Request 初始化"""
    data_queue = queue.Queue()
    sub_account = BinanceRequestDataSubAccount(
        data_queue,
        public_key="test_public_key",
        private_key="test_private_key"
    )
    assert sub_account.asset_type == 'SUB_ACCOUNT'
    assert sub_account.logger_name == 'binance_sub_account_feed.log'
    assert sub_account.exchange_name == 'binance_sub_account'
    assert sub_account._params.rest_url == 'https://api.binance.com'


def test_sub_account_request_has_list_methods():
    """测试 Sub-account Request 有子账户列表方法"""
    data_queue = queue.Queue()
    sub_account = BinanceRequestDataSubAccount(data_queue)
    assert hasattr(sub_account, 'get_sub_account_list')
    assert hasattr(sub_account, '_get_sub_account_list')
    assert hasattr(sub_account, 'get_sub_account_status')
    assert hasattr(sub_account, 'get_sub_account_spot_summary')


def test_sub_account_request_has_transfer_methods():
    """测试 Sub-account Request 有划转方法"""
    data_queue = queue.Queue()
    sub_account = BinanceRequestDataSubAccount(data_queue)
    assert hasattr(sub_account, 'sub_transfer_to_main')
    assert hasattr(sub_account, 'main_transfer_to_sub')
    assert hasattr(sub_account, 'sub_transfer_to_sub')
    assert hasattr(sub_account, 'get_sub_transfer_history')


def test_sub_account_request_has_asset_methods():
    """测试 Sub-account Request 有资产查询方法"""
    data_queue = queue.Queue()
    sub_account = BinanceRequestDataSubAccount(data_queue)
    assert hasattr(sub_account, 'get_sub_account_assets')
    assert hasattr(sub_account, 'get_sub_account_margin_account')
    assert hasattr(sub_account, 'get_sub_account_margin_summary')
    assert hasattr(sub_account, 'get_sub_account_futures_account')


def test_sub_account_request_has_api_key_methods():
    """测试 Sub-account Request 有 API Key 管理方法"""
    data_queue = queue.Queue()
    sub_account = BinanceRequestDataSubAccount(data_queue)
    assert hasattr(sub_account, 'create_sub_api_key')
    assert hasattr(sub_account, 'get_sub_api_key')
    assert hasattr(sub_account, 'delete_sub_api_key')
    assert hasattr(sub_account, 'get_sub_api_ip_restriction')
    assert hasattr(sub_account, 'delete_sub_ip_restriction')


def test_sub_account_request_get_sub_account_list_params():
    """测试 get_sub_account_list 参数构建"""
    data_queue = queue.Queue()
    sub_account = BinanceRequestDataSubAccount(
        data_queue,
        public_key="test_key",
        private_key="test_secret"
    )

    path, params, extra_data = sub_account._get_sub_account_list()

    assert path == 'GET /sapi/v1/sub-account/list'
    assert params == {}
    assert extra_data['request_type'] == 'get_sub_account_list'


def test_sub_account_request_sub_transfer_to_main_params():
    """测试 sub_transfer_to_main 参数构建"""
    data_queue = queue.Queue()
    sub_account = BinanceRequestDataSubAccount(data_queue)

    path, params, extra_data = sub_account._sub_transfer_to_main(
        email='test@example.com',
        asset='USDT',
        amount=100
    )

    assert path == 'POST /sapi/v1/sub-account/transfer/sub-to-main'
    assert params['email'] == 'test@example.com'
    assert params['asset'] == 'USDT'
    assert params['amount'] == 100


def test_sub_account_request_main_transfer_to_sub_params():
    """测试 main_transfer_to_sub 参数构建"""
    data_queue = queue.Queue()
    sub_account = BinanceRequestDataSubAccount(data_queue)

    path, params, extra_data = sub_account._main_transfer_to_sub(
        email='test@example.com',
        asset='USDT',
        amount=100
    )

    assert path == 'POST /sapi/v1/sub-account/transfer/main-to-sub'
    assert params['toEmail'] == 'test@example.com'
    assert params['asset'] == 'USDT'
    assert params['amount'] == 100


def test_sub_account_request_get_sub_account_assets_params():
    """测试 get_sub_account_assets 参数构建"""
    data_queue = queue.Queue()
    sub_account = BinanceRequestDataSubAccount(data_queue)

    path, params, extra_data = sub_account._get_sub_account_assets(
        email='test@example.com'
    )

    assert path == 'GET /sapi/v1/sub-account/assets'
    assert params['email'] == 'test@example.com'


def test_sub_account_request_delete_sub_api_key_params():
    """测试 delete_sub_api_key 参数构建"""
    data_queue = queue.Queue()
    sub_account = BinanceRequestDataSubAccount(data_queue)

    path, params, extra_data = sub_account._delete_sub_api_key(
        email='test@example.com',
        api_key='test_api_key'
    )

    assert path == 'DELETE /sapi/v1/sub-account/apiKey'
    assert params['email'] == 'test@example.com'
    assert params['publicKey'] == 'test_api_key'


def test_sub_account_request_all_public_methods():
    """测试所有公开方法是否存在"""
    data_queue = queue.Queue()
    sub_account = BinanceRequestDataSubAccount(data_queue)

    public_methods = [
        # 子账户管理
        'get_sub_account_list',
        'get_sub_account_status',
        'get_sub_account_spot_summary',
        # 子账户资金划转
        'sub_transfer_to_main',
        'main_transfer_to_sub',
        'sub_transfer_to_sub',
        'get_sub_transfer_history',
        'get_sub_account_universal_transfer',
        # 子账户资产查询
        'get_sub_account_assets',
        'get_sub_account_margin_account',
        'get_sub_account_margin_summary',
        'get_sub_account_futures_account',
        # 子账户 API Key 管理
        'create_sub_api_key',
        'get_sub_api_key',
        'delete_sub_api_key',
        'get_sub_api_ip_restriction',
        'delete_sub_ip_restriction',
    ]

    for method in public_methods:
        assert hasattr(sub_account, method), f"Missing method: {method}"
        assert callable(getattr(sub_account, method)), f"Method not callable: {method}"


if __name__ == "__main__":
    import pytest
    pytest.main([__file__, "-v"])
