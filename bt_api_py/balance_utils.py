"""
通用余额解析工具函数
将各交易所重复的 balance_handler 逻辑抽取到此处
"""


def simple_balance_handler(account_list):
    """通用余额解析处理函数（适用于 Binance/CTP/IB 等单层账户结构）
    :param account_list: list of AccountData
    :return: (value_result, cash_result)
    """
    value_result = {}
    cash_result = {}
    for account in account_list:
        account.init_data()
        currency = account.get_account_type()
        cash_result[currency] = {}
        cash_result[currency]["cash"] = account.get_available_margin()
        value_result[currency] = {}
        value_result[currency]["value"] = account.get_margin() + account.get_unrealized_profit()
    return value_result, cash_result


def nested_balance_handler(account_list):
    """嵌套余额解析处理函数（适用于 OKX 等多层账户结构，账户下有多个 balance）
    :param account_list: list of AccountData (每个 account 内含 get_balances())
    :return: (value_result, cash_result)
    """
    value_result = {}
    cash_result = {}
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
