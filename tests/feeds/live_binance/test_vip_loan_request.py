# -*- coding: utf-8 -*-
"""
Tests for Binance VIP Loan API Request Implementation
测试 Binance VIP借贷 API 请求实现
"""

import queue
from bt_api_py.feeds.live_binance.vip_loan import BinanceRequestDataVipLoan


def test_vip_loan_request_init():
    """测试 VIP Loan Request 初始化"""
    data_queue = queue.Queue()
    vip_loan = BinanceRequestDataVipLoan(
        data_queue,
        public_key="test_public_key",
        private_key="test_private_key"
    )
    assert vip_loan.asset_type == 'VIP_LOAN'
    assert vip_loan.logger_name == 'binance_vip_loan_feed.log'
    assert vip_loan.exchange_name == 'binance_vip_loan'
    assert vip_loan._params.rest_url == 'https://api.binance.com'


def test_vip_loan_request_has_methods():
    """测试 VIP Loan Request 有所有必需方法"""
    data_queue = queue.Queue()
    vip_loan = BinanceRequestDataVipLoan(data_queue)

    required_methods = [
        'get_vip_loan_ongoing_orders',
        'vip_loan_borrow',
        'vip_loan_repay',
        'get_vip_loan_history',
        'get_vip_repayment_history',
    ]

    for method in required_methods:
        assert hasattr(vip_loan, method), f"Missing method: {method}"
        assert callable(getattr(vip_loan, method)), f"Method not callable: {method}"


def test_vip_loan_request_get_vip_loan_ongoing_orders_params():
    """测试 get_vip_loan_ongoing_orders 参数构建"""
    data_queue = queue.Queue()
    vip_loan = BinanceRequestDataVipLoan(
        data_queue,
        public_key="test_key",
        private_key="test_secret"
    )

    path, params, extra_data = vip_loan._get_vip_loan_ongoing_orders(
        loan_coin='USDT',
        collateral_coin='BTC'
    )

    assert path == 'GET /sapi/v1/loan/ongoing/order'
    assert params['loanCoin'] == 'USDT'
    assert params['collateralCoin'] == 'BTC'


def test_vip_loan_request_vip_loan_borrow_params():
    """测试 vip_loan_borrow 参数构建"""
    data_queue = queue.Queue()
    vip_loan = BinanceRequestDataVipLoan(data_queue)

    path, params, extra_data = vip_loan._vip_loan_borrow(
        loan_coin='USDT',
        collateral_coin='BTC',
        loan_amount=1000,
        collateral_amount=0.05
    )

    assert path == 'POST /sapi/v1/loan/borrow'
    assert params['loanCoin'] == 'USDT'
    assert params['collateralCoin'] == 'BTC'
    assert params['loanAmount'] == 1000
    assert params['collateralAmount'] == 0.05


def test_vip_loan_request_vip_loan_repay_params():
    """测试 vip_loan_repay 参数构建"""
    data_queue = queue.Queue()
    vip_loan = BinanceRequestDataVipLoan(data_queue)

    path, params, extra_data = vip_loan._vip_loan_repay(
        loan_coin='USDT',
        collateral_coin='BTC',
        repay_amount=1000
    )

    assert path == 'POST /sapi/v1/loan/repay'
    assert params['loanCoin'] == 'USDT'
    assert params['collateralCoin'] == 'BTC'
    assert params['repayAmount'] == 1000


def test_vip_loan_request_get_vip_loan_history_params():
    """测试 get_vip_loan_history 参数构建"""
    data_queue = queue.Queue()
    vip_loan = BinanceRequestDataVipLoan(data_queue)

    path, params, extra_data = vip_loan._get_vip_loan_history(
        loan_coin='USDT',
        collateral_coin='BTC'
    )

    assert path == 'GET /sapi/v1/loan/loan/history'
    assert params['loanCoin'] == 'USDT'
    assert params['collateralCoin'] == 'BTC'


def test_vip_loan_request_get_vip_repayment_history_params():
    """测试 get_vip_repayment_history 参数构建"""
    data_queue = queue.Queue()
    vip_loan = BinanceRequestDataVipLoan(data_queue)

    path, params, extra_data = vip_loan._get_vip_repayment_history(
        loan_coin='USDT',
        collateral_coin='BTC'
    )

    assert path == 'GET /sapi/v1/loan/repayment/history'
    assert params['loanCoin'] == 'USDT'
    assert params['collateralCoin'] == 'BTC'


if __name__ == "__main__":
    import pytest
    pytest.main([__file__, "-v"])
