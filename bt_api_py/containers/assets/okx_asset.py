# -*- coding: utf-8 -*-
"""
OKX Asset/Funding Account data containers.
"""
import time
import json
from bt_api_py.functions.utils import from_dict_get_string, from_dict_get_float, from_dict_get_int


class OkxCurrencyData:
    """Container for currency information from OKX."""

    def __init__(self, currency_info, has_been_json_encoded=False):
        super(OkxCurrencyData, self).__init__()
        self.currency_info = currency_info if has_been_json_encoded else None
        self.raw_data = currency_info
        self.has_been_json_encoded = has_been_json_encoded
        self.has_been_init_data = False

        # Currency properties
        self.currency = None  # Currency, e.g. BTC
        self.name = None  # Currency name
        self.chain = None  # Chain name
        self.min_withdrawal_amt = None  # Minimum withdrawal amount
        self.withdrawal_fee = None  # Withdrawal fee
        self.can_withdraw = None  # Can withdraw
        self.can_deposit = None  # Can deposit
        self.can_internal = None  # Can internal transfer

    def init_data(self):
        if self.has_been_init_data:
            return self

        if not self.has_been_json_encoded:
            self.currency_info = self.raw_data if isinstance(self.raw_data, dict) else json.loads(self.raw_data)
            self.has_been_json_encoded = True

        self.currency = from_dict_get_string(self.currency_info, "ccy")
        self.name = from_dict_get_string(self.currency_info, "name")
        self.chain = from_dict_get_string(self.currency_info, "chain")
        self.min_withdrawal_amt = from_dict_get_float(self.currency_info, "minWd")
        self.withdrawal_fee = from_dict_get_float(self.currency_info, "wdFee")
        self.can_withdraw = self.currency_info.get("canWd") == "1"
        self.can_deposit = self.currency_info.get("canDep") == "1"
        self.can_internal = self.currency_info.get("canInternal") == "1"

        self.has_been_init_data = True
        return self

    def get_currency(self):
        """Get currency code, e.g. BTC."""
        return self.currency

    def get_name(self):
        """Get currency name."""
        return self.name

    def get_chain(self):
        """Get chain name."""
        return self.chain

    def get_min_withdrawal_amt(self):
        """Get minimum withdrawal amount."""
        return self.min_withdrawal_amt

    def get_withdrawal_fee(self):
        """Get withdrawal fee."""
        return self.withdrawal_fee

    def get_can_withdraw(self):
        """Check if withdrawal is allowed."""
        return self.can_withdraw

    def get_can_deposit(self):
        """Check if deposit is allowed."""
        return self.can_deposit

    def get_can_internal(self):
        """Check if internal transfer is allowed."""
        return self.can_internal

    def __str__(self):
        self.init_data()
        return json.dumps({
            "currency": self.currency,
            "name": self.name,
            "chain": self.chain,
            "min_withdrawal_amt": self.min_withdrawal_amt,
            "withdrawal_fee": self.withdrawal_fee,
            "can_withdraw": self.can_withdraw,
            "can_deposit": self.can_deposit,
            "can_internal": self.can_internal,
        })

    def __repr__(self):
        return self.__str__()


class OkxAssetBalanceData:
    """Container for asset balance information from OKX."""

    def __init__(self, balance_info, has_been_json_encoded=False):
        super(OkxAssetBalanceData, self).__init__()
        self.balance_info = balance_info if has_been_json_encoded else None
        self.raw_data = balance_info
        self.has_been_json_encoded = has_been_json_encoded
        self.has_been_init_data = False
        self.local_update_time = time.time()

        # Balance properties
        self.currency = None  # Currency
        self.balance = None  # Total balance
        self.available_balance = None  # Available balance
        self.frozen_balance = None  # Frozen balance

    def init_data(self):
        if self.has_been_init_data:
            return self

        if not self.has_been_json_encoded:
            self.balance_info = self.raw_data if isinstance(self.raw_data, dict) else json.loads(self.raw_data)
            self.has_been_json_encoded = True

        self.currency = from_dict_get_string(self.balance_info, "ccy")
        self.balance = from_dict_get_float(self.balance_info, "bal")
        self.available_balance = from_dict_get_float(self.balance_info, "availBal")
        self.frozen_balance = from_dict_get_float(self.balance_info, "frozenBal")

        self.has_been_init_data = True
        return self

    def get_currency(self):
        """Get currency code."""
        return self.currency

    def get_balance(self):
        """Get total balance."""
        return self.balance

    def get_available_balance(self):
        """Get available balance."""
        return self.available_balance

    def get_frozen_balance(self):
        """Get frozen balance."""
        return self.frozen_balance

    def get_local_update_time(self):
        """Get local update timestamp."""
        return self.local_update_time

    def __str__(self):
        self.init_data()
        return json.dumps({
            "currency": self.currency,
            "balance": self.balance,
            "available_balance": self.available_balance,
            "frozen_balance": self.frozen_balance,
            "local_update_time": self.local_update_time,
        })

    def __repr__(self):
        return self.__str__()


class OkxAssetValuationData:
    """Container for asset valuation information from OKX."""

    def __init__(self, valuation_info, has_been_json_encoded=False):
        super(OkxAssetValuationData, self).__init__()
        self.valuation_info = valuation_info if has_been_json_encoded else None
        self.raw_data = valuation_info
        self.has_been_json_encoded = has_been_json_encoded
        self.has_been_init_data = False
        self.local_update_time = time.time()

        # Valuation properties
        self.valuation_currency = None  # Valuation currency, e.g. USD
        self.total_valuation = None  # Total valuation
        self.btc_valuation = None  # BTC valuation
        self.timestamp = None  # Timestamp

    def init_data(self):
        if self.has_been_init_data:
            return self

        if not self.has_been_json_encoded:
            self.valuation_info = self.raw_data if isinstance(self.raw_data, dict) else json.loads(self.raw_data)
            self.has_been_json_encoded = True

        self.valuation_currency = from_dict_get_string(self.valuation_info, "ccy")
        self.total_valuation = from_dict_get_float(self.valuation_info, "totalEq")
        self.btc_valuation = from_dict_get_float(self.valuation_info, "btcEq")
        self.timestamp = from_dict_get_int(self.valuation_info, "ts")

        self.has_been_init_data = True
        return self

    def get_valuation_currency(self):
        """Get valuation currency."""
        return self.valuation_currency

    def get_total_valuation(self):
        """Get total valuation."""
        return self.total_valuation

    def get_btc_valuation(self):
        """Get BTC valuation."""
        return self.btc_valuation

    def get_timestamp(self):
        """Get timestamp."""
        return self.timestamp

    def get_local_update_time(self):
        """Get local update timestamp."""
        return self.local_update_time

    def __str__(self):
        self.init_data()
        return json.dumps({
            "valuation_currency": self.valuation_currency,
            "total_valuation": self.total_valuation,
            "btc_valuation": self.btc_valuation,
            "timestamp": self.timestamp,
            "local_update_time": self.local_update_time,
        })

    def __repr__(self):
        return self.__str__()


class OkxTransferStateData:
    """Container for transfer state information from OKX."""

    def __init__(self, transfer_info, has_been_json_encoded=False):
        super(OkxTransferStateData, self).__init__()
        self.transfer_info = transfer_info if has_been_json_encoded else None
        self.raw_data = transfer_info
        self.has_been_json_encoded = has_been_json_encoded
        self.has_been_init_data = False
        self.local_update_time = time.time()

        # Transfer properties
        self.transfer_id = None  # Transfer ID
        self.currency = None  # Currency
        self.amount = None  # Amount
        self.from_account = None  # Source account
        self.to_account = None  # Destination account
        self.status = None  # Status
        self.timestamp = None  # Timestamp

    def init_data(self):
        if self.has_been_init_data:
            return self

        if not self.has_been_json_encoded:
            self.transfer_info = self.raw_data if isinstance(self.raw_data, dict) else json.loads(self.raw_data)
            self.has_been_json_encoded = True

        self.transfer_id = from_dict_get_string(self.transfer_info, "transId")
        self.currency = from_dict_get_string(self.transfer_info, "ccy")
        self.amount = from_dict_get_float(self.transfer_info, "amt")
        self.from_account = from_dict_get_string(self.transfer_info, "from")
        self.to_account = from_dict_get_string(self.transfer_info, "to")
        self.status = from_dict_get_string(self.transfer_info, "state")
        self.timestamp = from_dict_get_int(self.transfer_info, "ts")

        self.has_been_init_data = True
        return self

    def get_transfer_id(self):
        """Get transfer ID."""
        return self.transfer_id

    def get_currency(self):
        """Get currency."""
        return self.currency

    def get_amount(self):
        """Get transfer amount."""
        return self.amount

    def get_from_account(self):
        """Get source account."""
        return self.from_account

    def get_to_account(self):
        """Get destination account."""
        return self.to_account

    def get_status(self):
        """Get transfer status."""
        return self.status

    def get_timestamp(self):
        """Get timestamp."""
        return self.timestamp

    def get_local_update_time(self):
        """Get local update timestamp."""
        return self.local_update_time

    def __str__(self):
        self.init_data()
        return json.dumps({
            "transfer_id": self.transfer_id,
            "currency": self.currency,
            "amount": self.amount,
            "from_account": self.from_account,
            "to_account": self.to_account,
            "status": self.status,
            "timestamp": self.timestamp,
            "local_update_time": self.local_update_time,
        })

    def __repr__(self):
        return self.__str__()


class OkxDepositInfoData:
    """Container for deposit information from OKX WebSocket (deposit-info channel)."""

    def __init__(self, deposit_info, symbol_name="ANY", asset_type="SWAP", has_been_json_encoded=False):
        super(OkxDepositInfoData, self).__init__()
        self.deposit_info = deposit_info if has_been_json_encoded else None
        self.raw_data = deposit_info
        self.has_been_json_encoded = has_been_json_encoded
        self.has_been_init_data = False
        self.local_update_time = time.time()
        self.symbol_name = symbol_name
        self.asset_type = asset_type
        self.exchange_name = 'OKX'

        # Deposit properties
        self.currency = None  # Currency
        self.deposit_id = None  # Deposit ID
        self.address = None  # Deposit address
        self.amount = None  # Deposit amount
        self.status = None  # Deposit status
        self.timestamp = None  # Timestamp
        self.to_address = None  # To address
        self.tx_id = None  # Transaction ID

    def init_data(self):
        if self.has_been_init_data:
            return self

        if not self.has_been_json_encoded:
            self.deposit_info = self.raw_data if isinstance(self.raw_data, dict) else json.loads(self.raw_data)
            self.has_been_json_encoded = True

        self.currency = from_dict_get_string(self.deposit_info, "ccy")
        self.deposit_id = from_dict_get_string(self.deposit_info, "depId")
        self.address = from_dict_get_string(self.deposit_info, "addr")
        self.amount = from_dict_get_float(self.deposit_info, "amt")
        self.status = from_dict_get_string(self.deposit_info, "state")
        self.timestamp = from_dict_get_int(self.deposit_info, "ts")
        self.to_address = from_dict_get_string(self.deposit_info, "to")
        self.tx_id = from_dict_get_string(self.deposit_info, "txId")

        self.has_been_init_data = True
        return self

    def get_exchange_name(self):
        return self.exchange_name

    def get_symbol_name(self):
        return self.symbol_name

    def get_asset_type(self):
        return self.asset_type

    def get_currency(self):
        """Get currency code."""
        return self.currency

    def get_deposit_id(self):
        """Get deposit ID."""
        return self.deposit_id

    def get_address(self):
        """Get deposit address."""
        return self.address

    def get_amount(self):
        """Get deposit amount."""
        return self.amount

    def get_status(self):
        """Get deposit status."""
        return self.status

    def get_timestamp(self):
        """Get timestamp."""
        return self.timestamp

    def get_to_address(self):
        """Get to address."""
        return self.to_address

    def get_tx_id(self):
        """Get transaction ID."""
        return self.tx_id

    def get_local_update_time(self):
        """Get local update timestamp."""
        return self.local_update_time

    def __str__(self):
        self.init_data()
        return json.dumps({
            "exchange_name": self.exchange_name,
            "symbol_name": self.symbol_name,
            "asset_type": self.asset_type,
            "currency": self.currency,
            "deposit_id": self.deposit_id,
            "address": self.address,
            "amount": self.amount,
            "status": self.status,
            "timestamp": self.timestamp,
            "to_address": self.to_address,
            "tx_id": self.tx_id,
            "local_update_time": self.local_update_time,
        })

    def __repr__(self):
        return self.__str__()


class OkxWithdrawalInfoData:
    """Container for withdrawal information from OKX WebSocket (withdrawal-info channel)."""

    def __init__(self, withdrawal_info, symbol_name="ANY", asset_type="SWAP", has_been_json_encoded=False):
        super(OkxWithdrawalInfoData, self).__init__()
        self.withdrawal_info = withdrawal_info if has_been_json_encoded else None
        self.raw_data = withdrawal_info
        self.has_been_json_encoded = has_been_json_encoded
        self.has_been_init_data = False
        self.local_update_time = time.time()
        self.symbol_name = symbol_name
        self.asset_type = asset_type
        self.exchange_name = 'OKX'

        # Withdrawal properties
        self.currency = None  # Currency
        self.withdrawal_id = None  # Withdrawal ID
        self.address = None  # Withdrawal address
        self.amount = None  # Withdrawal amount
        self.status = None  # Withdrawal status
        self.timestamp = None  # Timestamp
        self.fee = None  # Withdrawal fee
        self.tx_id = None  # Transaction ID

    def init_data(self):
        if self.has_been_init_data:
            return self

        if not self.has_been_json_encoded:
            self.withdrawal_info = self.raw_data if isinstance(self.raw_data, dict) else json.loads(self.raw_data)
            self.has_been_json_encoded = True

        self.currency = from_dict_get_string(self.withdrawal_info, "ccy")
        self.withdrawal_id = from_dict_get_string(self.withdrawal_info, "wdId")
        self.address = from_dict_get_string(self.withdrawal_info, "addr")
        self.amount = from_dict_get_float(self.withdrawal_info, "amt")
        self.status = from_dict_get_string(self.withdrawal_info, "state")
        self.timestamp = from_dict_get_int(self.withdrawal_info, "ts")
        self.fee = from_dict_get_float(self.withdrawal_info, "fee")
        self.tx_id = from_dict_get_string(self.withdrawal_info, "txId")

        self.has_been_init_data = True
        return self

    def get_exchange_name(self):
        return self.exchange_name

    def get_symbol_name(self):
        return self.symbol_name

    def get_asset_type(self):
        return self.asset_type

    def get_currency(self):
        """Get currency code."""
        return self.currency

    def get_withdrawal_id(self):
        """Get withdrawal ID."""
        return self.withdrawal_id

    def get_address(self):
        """Get withdrawal address."""
        return self.address

    def get_amount(self):
        """Get withdrawal amount."""
        return self.amount

    def get_status(self):
        """Get withdrawal status."""
        return self.status

    def get_timestamp(self):
        """Get timestamp."""
        return self.timestamp

    def get_fee(self):
        """Get withdrawal fee."""
        return self.fee

    def get_tx_id(self):
        """Get transaction ID."""
        return self.tx_id

    def get_local_update_time(self):
        """Get local update timestamp."""
        return self.local_update_time

    def __str__(self):
        self.init_data()
        return json.dumps({
            "exchange_name": self.exchange_name,
            "symbol_name": self.symbol_name,
            "asset_type": self.asset_type,
            "currency": self.currency,
            "withdrawal_id": self.withdrawal_id,
            "address": self.address,
            "amount": self.amount,
            "status": self.status,
            "timestamp": self.timestamp,
            "fee": self.fee,
            "tx_id": self.tx_id,
            "local_update_time": self.local_update_time,
        })

    def __repr__(self):
        return self.__str__()
