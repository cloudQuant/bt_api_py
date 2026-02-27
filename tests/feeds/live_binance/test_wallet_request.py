# -*- coding: utf-8 -*-
"""
Tests for Binance Wallet API Request Implementation
测试 Binance 钱包 API 请求实现
"""

import queue
from bt_api_py.feeds.live_binance.wallet import BinanceRequestDataWallet


def test_wallet_request_init():
    """测试 Wallet Request 初始化"""
    data_queue = queue.Queue()
    wallet = BinanceRequestDataWallet(
        data_queue,
        public_key="test_public_key",
        private_key="test_private_key"
    )
    assert wallet.asset_type == 'WALLET'
    assert wallet.logger_name == 'binance_wallet_feed.log'
    assert wallet.exchange_name == 'binance_wallet'
    assert wallet._params.rest_url == 'https://api.binance.com'


def test_wallet_request_has_get_wallet_balance():
    """测试 Wallet Request 有 get_wallet_balance 方法"""
    data_queue = queue.Queue()
    wallet = BinanceRequestDataWallet(data_queue)
    assert hasattr(wallet, 'get_wallet_balance')
    assert hasattr(wallet, '_get_wallet_balance')


def test_wallet_request_has_asset_transfer():
    """测试 Wallet Request 有 asset_transfer 方法"""
    data_queue = queue.Queue()
    wallet = BinanceRequestDataWallet(data_queue)
    assert hasattr(wallet, 'asset_transfer')
    assert hasattr(wallet, '_asset_transfer')


def test_wallet_request_has_withdraw():
    """测试 Wallet Request 有 withdraw 方法"""
    data_queue = queue.Queue()
    wallet = BinanceRequestDataWallet(data_queue)
    assert hasattr(wallet, 'withdraw')
    assert hasattr(wallet, '_withdraw')


def test_wallet_request_has_deposit_methods():
    """测试 Wallet Request 有充值相关方法"""
    data_queue = queue.Queue()
    wallet = BinanceRequestDataWallet(data_queue)
    assert hasattr(wallet, 'get_deposit_address')
    assert hasattr(wallet, '_get_deposit_address')
    assert hasattr(wallet, 'get_deposit_history')
    assert hasattr(wallet, '_get_deposit_history')


def test_wallet_request_has_withdraw_methods():
    """测试 Wallet Request 有提现相关方法"""
    data_queue = queue.Queue()
    wallet = BinanceRequestDataWallet(data_queue)
    assert hasattr(wallet, 'get_withdraw_history')
    assert hasattr(wallet, '_get_withdraw_history')
    assert hasattr(wallet, 'get_withdraw_address')
    assert hasattr(wallet, '_get_withdraw_address')


def test_wallet_request_has_dust_methods():
    """测试 Wallet Request 有小额资产转换方法"""
    data_queue = queue.Queue()
    wallet = BinanceRequestDataWallet(data_queue)
    assert hasattr(wallet, 'get_dust')
    assert hasattr(wallet, '_get_dust')
    assert hasattr(wallet, 'dust_transfer')
    assert hasattr(wallet, '_dust_transfer')


def test_wallet_request_has_transfer_methods():
    """测试 Wallet Request 有划转相关方法"""
    data_queue = queue.Queue()
    wallet = BinanceRequestDataWallet(data_queue)
    assert hasattr(wallet, 'get_asset_transfer')
    assert hasattr(wallet, 'transfer_to_futures_main')
    assert hasattr(wallet, 'transfer_to_futures_sub')
    assert hasattr(wallet, 'transfer_to_um')
    assert hasattr(wallet, 'transfer_to_isolated_margin')


def test_wallet_request_get_wallet_balance_params():
    """测试 get_wallet_balance 参数构建"""
    data_queue = queue.Queue()
    wallet = BinanceRequestDataWallet(
        data_queue,
        public_key="test_key",
        private_key="test_secret"
    )

    path, params, extra_data = wallet._get_wallet_balance()

    assert path == 'GET /sapi/v1/asset/wallet/balance'
    assert params == {}
    assert extra_data['request_type'] == 'get_wallet_balance'
    assert extra_data['symbol_name'] == 'ALL'
    assert extra_data['asset_type'] == 'WALLET'


def test_wallet_request_asset_transfer_params():
    """测试 asset_transfer 参数构建"""
    data_queue = queue.Queue()
    wallet = BinanceRequestDataWallet(data_queue)

    path, params, extra_data = wallet._asset_transfer(
        transfer_type='UM',
        asset='USDT',
        amount=100
    )

    assert path == 'POST /sapi/v1/asset/transfer'
    assert params['type'] == 'UM'
    assert params['asset'] == 'USDT'
    assert params['amount'] == 100
    assert extra_data['request_type'] == 'asset_transfer'


def test_wallet_request_withdraw_params():
    """测试 withdraw 参数构建"""
    data_queue = queue.Queue()
    wallet = BinanceRequestDataWallet(data_queue)

    path, params, extra_data = wallet._withdraw(
        coin='USDT',
        address='0x123456789',
        amount=100,
        network='TRC20'
    )

    assert path == 'POST /sapi/v1/capital/withdraw/apply'
    assert params['coin'] == 'USDT'
    assert params['address'] == '0x123456789'
    assert params['amount'] == 100
    assert params['network'] == 'TRC20'


def test_wallet_request_deposit_address_params():
    """测试 get_deposit_address 参数构建"""
    data_queue = queue.Queue()
    wallet = BinanceRequestDataWallet(data_queue)

    path, params, extra_data = wallet._get_deposit_address(
        coin='USDT',
        network='TRC20'
    )

    assert path == 'GET /sapi/v1/capital/deposit/address'
    assert params['coin'] == 'USDT'
    assert params['network'] == 'TRC20'


def test_wallet_request_dust_transfer_params():
    """测试 dust_transfer 参数构建"""
    data_queue = queue.Queue()
    wallet = BinanceRequestDataWallet(data_queue)

    # Test with list
    path, params, extra_data = wallet._dust_transfer(['BTC', 'ETH'])
    assert path == 'POST /sapi/v1/asset/dust/btc'
    assert params['asset'] == 'BTC,ETH'

    # Test with string
    path, params, extra_data = wallet._dust_transfer('BTC,ETH')
    assert params['asset'] == 'BTC,ETH'


def test_wallet_request_all_public_methods():
    """测试所有公开方法是否存在"""
    data_queue = queue.Queue()
    wallet = BinanceRequestDataWallet(data_queue)

    public_methods = [
        # 资产查询
        'get_wallet_balance',
        'get_asset_detail',
        'get_asset_ledger',
        'get_asset_dividend',
        # 资产划转
        'asset_transfer',
        'get_asset_transfer',
        'transfer_to_futures_main',
        'transfer_to_futures_sub',
        'transfer_to_um',
        'transfer_to_isolated_margin',
        # 充值相关
        'get_deposit_address',
        'get_deposit_history',
        # 提现相关
        'withdraw',
        'get_withdraw_history',
        'get_withdraw_address',
        # 小额资产转换
        'get_dust',
        'dust_transfer',
    ]

    for method in public_methods:
        assert hasattr(wallet, method), f"Missing method: {method}"
        assert callable(getattr(wallet, method)), f"Method not callable: {method}"


if __name__ == "__main__":
    import pytest
    pytest.main([__file__, "-v"])
