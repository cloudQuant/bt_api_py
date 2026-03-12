"""
通用余额解析工具函数
将各交易所重复的 balance_handler 逻辑抽取到此处
"""

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from bt_api_py.containers.accounts.account import AccountData


def simple_balance_handler(
    account_list: list["AccountData"],
) -> tuple[dict[str, dict[str, float]], dict[str, dict[str, float]]]:
    """通用余额解析处理函数（适用于 Binance/CTP/IB 等单层账户结构）。

    Args:
        account_list: 账户数据列表，每个元素实现 get_account_type、get_available_margin 等接口。

    Returns:
        (value_result, cash_result): value_result 为 {currency: {"value": float}}，
        cash_result 为 {currency: {"cash": float}}。
    """
    value_result: dict[str, dict[str, float]] = {}
    cash_result: dict[str, dict[str, float]] = {}
    for account in account_list:
        account.init_data()
        currency = account.get_account_type()
        cash_result[currency] = {}
        cash_result[currency]["cash"] = account.get_available_margin()
        value_result[currency] = {}
        value_result[currency]["value"] = account.get_margin() + account.get_unrealized_profit()
    return value_result, cash_result


def nested_balance_handler(
    account_list: list["AccountData"],
) -> tuple[dict[str, dict[str, float]], dict[str, dict[str, float]]]:
    """嵌套余额解析处理函数（适用于 OKX 等多层账户结构）。

    Args:
        account_list: 账户数据列表，每个 account 内含 get_balances() 返回 balance 列表。

    Returns:
        (value_result, cash_result): 同 simple_balance_handler。
    """
    value_result: dict[str, dict[str, float]] = {}
    cash_result: dict[str, dict[str, float]] = {}
    for account in account_list:
        account.init_data()
        for balance in account.get_balances():
            balance.init_data()
            currency = balance.get_symbol_name()
            cash_result[currency] = {}
            cash_result[currency]["cash"] = balance.get_available_margin()
            value_result[currency] = {}
            value_result[currency]["value"] = balance.get_margin() + balance.get_unrealized_profit()
    return value_result, cash_result
