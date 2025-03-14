# import json
from bt_api_py.containers.accounts.okx_account import OkxAccountData
from bt_api_py.containers.balances.balance import BalanceData

balance_data = {
    "availBal": "4834.317093622894",
    "availEq": "4834.3170936228935",
    "borrowFroz": "0",
    "cashBal": "4850.435693622894",
    "ccy": "USDT",
    "crossLiab": "0",
    "disEq": "4991.542013297616",
    "eq": "4992.890093622894",
    "eqUsd": "4991.542013297616",
    "fixedBal": "0",
    "frozenBal": "158.573",
    "imr": "",
    "interest": "0",
    "isoEq": "0",
    "isoLiab": "0",
    "isoUpl": "0",
    "liab": "0",
    "maxLoan": "0",
    "mgnRatio": "",
    "mmr": "",
    "notionalLever": "",
    "ordFrozen": "0",
    "spotInUseAmt": "",
    "spotIsoBal": "0",
    "stgyEq": "150",
    "twap": "0",
    "uTime": "1705449605015",
    "upl": "-7.545600000000006",
    "uplLiab": "0"
}


def assert_balance_api(bo):
    assert bo.get_event() == "BalanceEvent"
    assert bo.get_exchange_name() == "OKX"
    assert bo.get_asset_type() == "SWAP"
    assert bo.get_server_time() == 1705449605015.0
    assert isinstance(bo.get_local_update_time(), float)
    assert bo.get_account_id() is None
    assert bo.get_account_type() is None
    assert bo.get_fee_tier() is None
    assert bo.get_max_withdraw_amount() is None
    assert bo.get_margin() == 4992.890093622894
    assert bo.get_used_margin() == 158.573
    assert bo.get_available_margin() == 4834.317093622894
    assert bo.get_maintain_margin() is None
    assert bo.get_open_order_initial_margin() == 158.573
    assert bo.get_position_initial_margin() is None
    assert bo.get_unrealized_profit() == -7.545600000000006
    assert bo.get_interest() == 0.0


def assert_one_symbol_account(bo):
    assert bo.get_event() == "AccountEvent"
    assert bo.get_exchange_name() == "OKX"
    assert bo.get_asset_type() == "SWAP"
    assert bo.get_server_time() > 0.0
    assert isinstance(bo.get_local_update_time(), float)
    assert bo.get_account_id() is None
    assert bo.get_account_type() is None
    assert bo.get_can_deposit() is None
    assert bo.get_can_trade() is None
    assert bo.get_can_withdraw() is None
    assert bo.get_fee_tier() is None
    assert bo.get_max_withdraw_amount() is None
    assert bo.get_total_margin() == 55837.43556134779
    assert bo.get_total_used_margin() == 8.57068529
    assert bo.get_total_available_margin() == 55837.43556134779 - 8.57068529
    assert bo.get_total_maintain_margin() == 0.3428274116
    assert bo.get_total_open_order_initial_margin() == 0.0
    assert bo.get_total_position_initial_margin() is None
    assert bo.get_total_unrealized_profit() == -7.543562688000006


def test_okx_req_one_symbol_account():
    data = {
        "code": "0",
        "data": [
            {
                "adjEq": "55415.624719833286",
                "borrowFroz": "0",
                "details": [
                    balance_data
                ],
                "imr": "8.57068529",
                "isoEq": "0",
                "mgnRatio": "143682.59776662575",
                "mmr": "0.3428274116",
                "notionalUsd": "85.7068529",
                "ordFroz": "0",
                "totalEq": "55837.43556134779",
                "uTime": "1705474164160",
                "upl": "-7.543562688000006"
            }
        ],
        "msg": ""
    }
    bo = OkxAccountData(data['data'][0], "USDT", "SWAP", True)
    bo.init_data()
    assert_one_symbol_account(bo)
    balances = bo.get_balances()
    ba = balances[0]
    ba.init_data()
    assert isinstance(ba, BalanceData)
    assert_balance_api(ba)


if __name__ == "__main__":
    test_okx_req_one_symbol_account()
