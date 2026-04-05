"""Tests for OKX Asset containers."""

import json

from bt_api_py.containers.assets import (
    OkxAssetBalanceData,
    OkxAssetValuationData,
    OkxCurrencyData,
    OkxDepositInfoData,
    OkxTransferStateData,
    OkxWithdrawalInfoData,
)


class TestOkxCurrencyData:
    """Tests for OkxCurrencyData container."""

    def test_init_with_dict(self):
        """Test initialization with dict data."""
        data = {
            "ccy": "BTC",
            "name": "Bitcoin",
            "chain": "BTC",
            "minWd": "0.001",
            "wdFee": "0.0005",
            "canWd": "1",
            "canDep": "1",
            "canInternal": "1",
        }
        currency = OkxCurrencyData(data, has_been_json_encoded=True)

        assert currency.currency_info == data
        assert currency.has_been_json_encoded is True
        assert currency.has_been_init_data is False

    def test_init_with_json_string(self):
        """Test initialization with JSON string."""
        data = {
            "ccy": "ETH",
            "name": "Ethereum",
            "chain": "ETH",
            "minWd": "0.01",
            "wdFee": "0.001",
            "canWd": "1",
            "canDep": "1",
            "canInternal": "0",
        }
        json_str = json.dumps(data)
        currency = OkxCurrencyData(json_str, has_been_json_encoded=False)

        currency.init_data()
        assert currency.currency == "ETH"
        assert currency.name == "Ethereum"
        assert currency.chain == "ETH"

    def test_init_data(self):
        """Test init_data method."""
        data = {
            "ccy": "USDT",
            "name": "Tether",
            "chain": "TRC20",
            "minWd": "10",
            "wdFee": "1",
            "canWd": "1",
            "canDep": "1",
            "canInternal": "1",
        }
        currency = OkxCurrencyData(data, has_been_json_encoded=True)

        result = currency.init_data()

        assert result is currency  # Returns self
        assert currency.has_been_init_data is True
        assert currency.currency == "USDT"
        assert currency.name == "Tether"
        assert currency.chain == "TRC20"
        assert currency.min_withdrawal_amt == 10.0
        assert currency.withdrawal_fee == 1.0
        assert currency.can_withdraw is True
        assert currency.can_deposit is True
        assert currency.can_internal is True

    def test_init_data_idempotent(self):
        """Test that init_data is idempotent."""
        data = {"ccy": "BTC", "canWd": "0", "canDep": "0", "canInternal": "0"}
        currency = OkxCurrencyData(data, has_been_json_encoded=True)

        currency.init_data()
        first_currency = currency.currency

        currency.init_data()
        assert currency.currency == first_currency

    def test_getter_methods(self):
        """Test all getter methods."""
        data = {
            "ccy": "BTC",
            "name": "Bitcoin",
            "chain": "BTC",
            "minWd": "0.001",
            "wdFee": "0.0005",
            "canWd": "1",
            "canDep": "1",
            "canInternal": "1",
        }
        currency = OkxCurrencyData(data, has_been_json_encoded=True)
        currency.init_data()

        assert currency.get_currency() == "BTC"
        assert currency.get_name() == "Bitcoin"
        assert currency.get_chain() == "BTC"
        assert currency.get_min_withdrawal_amt() == 0.001
        assert currency.get_withdrawal_fee() == 0.0005
        assert currency.get_can_withdraw() is True
        assert currency.get_can_deposit() is True
        assert currency.get_can_internal() is True

    def test_str_and_repr(self):
        """Test __str__ and __repr__ methods."""
        data = {"ccy": "BTC", "name": "Bitcoin"}
        currency = OkxCurrencyData(data, has_been_json_encoded=True)

        str_result = str(currency)
        repr_result = repr(currency)

        assert "BTC" in str_result
        assert str_result == repr_result
        # Verify it's valid JSON
        parsed = json.loads(str_result)
        assert parsed["currency"] == "BTC"

    def test_boolean_field_parsing(self):
        """Test boolean field parsing from string values."""
        data = {"canWd": "1", "canDep": "0", "canInternal": "1"}
        currency = OkxCurrencyData(data, has_been_json_encoded=True)
        currency.init_data()

        assert currency.can_withdraw is True
        assert currency.can_deposit is False
        assert currency.can_internal is True


class TestOkxAssetBalanceData:
    """Tests for OkxAssetBalanceData container."""

    def test_init_with_dict(self):
        """Test initialization with dict data."""
        data = {
            "ccy": "USDT",
            "bal": "10000.5",
            "availBal": "8000.5",
            "frozenBal": "2000",
        }
        balance = OkxAssetBalanceData(data, has_been_json_encoded=True)

        assert balance.balance_info == data
        assert balance.has_been_json_encoded is True

    def test_init_data(self):
        """Test init_data method."""
        data = {
            "ccy": "BTC",
            "bal": "1.5",
            "availBal": "1.0",
            "frozenBal": "0.5",
        }
        balance = OkxAssetBalanceData(data, has_been_json_encoded=True)

        balance.init_data()

        assert balance.currency == "BTC"
        assert balance.balance == 1.5
        assert balance.available_balance == 1.0
        assert balance.frozen_balance == 0.5

    def test_getter_methods(self):
        """Test all getter methods."""
        data = {
            "ccy": "ETH",
            "bal": "10",
            "availBal": "8",
            "frozenBal": "2",
        }
        balance = OkxAssetBalanceData(data, has_been_json_encoded=True)
        balance.init_data()

        assert balance.get_currency() == "ETH"
        assert balance.get_balance() == 10.0
        assert balance.get_available_balance() == 8.0
        assert balance.get_frozen_balance() == 2.0
        assert balance.get_local_update_time() is not None

    def test_str_representation(self):
        """Test string representation."""
        data = {"ccy": "USDT", "bal": "100", "availBal": "80", "frozenBal": "20"}
        balance = OkxAssetBalanceData(data, has_been_json_encoded=True)

        str_result = str(balance)
        parsed = json.loads(str_result)

        assert parsed["currency"] == "USDT"
        assert parsed["balance"] == 100.0


class TestOkxAssetValuationData:
    """Tests for OkxAssetValuationData container."""

    def test_init_data(self):
        """Test init_data method."""
        data = {
            "ccy": "USD",
            "totalEq": "50000.5",
            "btcEq": "1.5",
            "ts": "1700000000000",
        }
        valuation = OkxAssetValuationData(data, has_been_json_encoded=True)

        valuation.init_data()

        assert valuation.valuation_currency == "USD"
        assert valuation.total_valuation == 50000.5
        assert valuation.btc_valuation == 1.5
        assert valuation.timestamp == 1700000000000

    def test_getter_methods(self):
        """Test all getter methods."""
        data = {
            "ccy": "USD",
            "totalEq": "100000",
            "btcEq": "2.5",
            "ts": "1700000000000",
        }
        valuation = OkxAssetValuationData(data, has_been_json_encoded=True)
        valuation.init_data()

        assert valuation.get_valuation_currency() == "USD"
        assert valuation.get_total_valuation() == 100000.0
        assert valuation.get_btc_valuation() == 2.5
        assert valuation.get_timestamp() == 1700000000000
        assert valuation.get_local_update_time() is not None


class TestOkxTransferStateData:
    """Tests for OkxTransferStateData container."""

    def test_init_data(self):
        """Test init_data method."""
        data = {
            "transId": "12345",
            "ccy": "USDT",
            "amt": "100",
            "from": "trading",
            "to": "funding",
            "state": "success",
            "ts": "1700000000000",
        }
        transfer = OkxTransferStateData(data, has_been_json_encoded=True)

        transfer.init_data()

        assert transfer.transfer_id == "12345"
        assert transfer.currency == "USDT"
        assert transfer.amount == 100.0
        assert transfer.from_account == "trading"
        assert transfer.to_account == "funding"
        assert transfer.status == "success"
        assert transfer.timestamp == 1700000000000

    def test_getter_methods(self):
        """Test all getter methods."""
        data = {
            "transId": "transfer123",
            "ccy": "BTC",
            "amt": "0.5",
            "from": "main",
            "to": "trading",
            "state": "pending",
            "ts": "1700000000000",
        }
        transfer = OkxTransferStateData(data, has_been_json_encoded=True)
        transfer.init_data()

        assert transfer.get_transfer_id() == "transfer123"
        assert transfer.get_currency() == "BTC"
        assert transfer.get_amount() == 0.5
        assert transfer.get_from_account() == "main"
        assert transfer.get_to_account() == "trading"
        assert transfer.get_status() == "pending"
        assert transfer.get_timestamp() == 1700000000000


class TestOkxDepositInfoData:
    """Tests for OkxDepositInfoData container."""

    def test_init_data(self):
        """Test init_data method."""
        data = {
            "ccy": "USDT",
            "depId": "deposit123",
            "addr": "0xabc123",
            "amt": "1000",
            "state": "success",
            "ts": "1700000000000",
            "to": "0xdef456",
            "txId": "tx789",
        }
        deposit = OkxDepositInfoData(
            data, symbol_name="USDT", asset_type="SPOT", has_been_json_encoded=True
        )

        deposit.init_data()

        assert deposit.currency == "USDT"
        assert deposit.deposit_id == "deposit123"
        assert deposit.address == "0xabc123"
        assert deposit.amount == 1000.0
        assert deposit.status == "success"
        assert deposit.timestamp == 1700000000000
        assert deposit.to_address == "0xdef456"
        assert deposit.tx_id == "tx789"

    def test_exchange_info(self):
        """Test exchange info methods."""
        data = {
            "ccy": "BTC",
            "depId": "dep1",
            "amt": "1",
            "state": "pending",
            "ts": "1700000000000",
        }
        deposit = OkxDepositInfoData(
            data, symbol_name="BTC", asset_type="SPOT", has_been_json_encoded=True
        )

        assert deposit.get_exchange_name() == "OKX"
        assert deposit.get_symbol_name() == "BTC"
        assert deposit.get_asset_type() == "SPOT"


class TestOkxWithdrawalInfoData:
    """Tests for OkxWithdrawalInfoData container."""

    def test_init_data(self):
        """Test init_data method."""
        data = {
            "ccy": "ETH",
            "wdId": "withdrawal123",
            "addr": "0xabc123",
            "amt": "5",
            "state": "processing",
            "ts": "1700000000000",
            "fee": "0.01",
            "txId": "tx456",
        }
        withdrawal = OkxWithdrawalInfoData(
            data, symbol_name="ETH", asset_type="SPOT", has_been_json_encoded=True
        )

        withdrawal.init_data()

        assert withdrawal.currency == "ETH"
        assert withdrawal.withdrawal_id == "withdrawal123"
        assert withdrawal.address == "0xabc123"
        assert withdrawal.amount == 5.0
        assert withdrawal.status == "processing"
        assert withdrawal.timestamp == 1700000000000
        assert withdrawal.fee == 0.01
        assert withdrawal.tx_id == "tx456"

    def test_getter_methods(self):
        """Test all getter methods."""
        data = {
            "ccy": "USDT",
            "wdId": "wd1",
            "addr": "addr1",
            "amt": "100",
            "state": "success",
            "ts": "1700000000000",
            "fee": "1",
            "txId": "tx1",
        }
        withdrawal = OkxWithdrawalInfoData(
            data, symbol_name="USDT", asset_type="SPOT", has_been_json_encoded=True
        )
        withdrawal.init_data()

        assert withdrawal.get_withdrawal_id() == "wd1"
        assert withdrawal.get_address() == "addr1"
        assert withdrawal.get_amount() == 100.0
        assert withdrawal.get_status() == "success"
        assert withdrawal.get_fee() == 1.0
        assert withdrawal.get_tx_id() == "tx1"
