"""账户类,用于确定账户数据的属性和方法。"""

from typing import Any, Self

from bt_api_py.containers.auto_init_mixin import AutoInitMixin


class AccountData(AutoInitMixin):
    """账户数据基类,提供账户信息的统一接口。

    Args:
        account_info: 账户信息数据（dict 或 JSON 字符串）
        has_been_json_encoded: 是否已进行JSON编码
    """

    def __init__(
        self,
        account_info: dict[str, Any] | str,
        has_been_json_encoded: bool = False,
    ) -> None:
        """初始化账户数据。

        Args:
            account_info: 账户信息字典
            has_been_json_encoded: 是否已进行JSON编码,默认为False
        """
        self.event = "AccountEvent"
        self.account_info = account_info
        self.has_been_json_encoded = has_been_json_encoded

    def get_event(self) -> str:
        """获取事件类型。

        Returns:
            事件类型字符串
        """
        return self.event

    def init_data(self) -> Self:
        """初始化账户数据。

        Returns:
            Self for method chaining.

        Raises:
            NotImplementedError: 子类必须实现此方法
        """
        raise NotImplementedError

    def get_all_data(self) -> dict[str, Any]:
        """获取所有账户数据。

        Returns:
            包含所有账户信息的字典

        Raises:
            NotImplementedError: 子类必须实现此方法
        """
        raise NotImplementedError

    def get_exchange_name(self) -> str:
        """获取交易所名称。

        Returns:
            交易所名称字符串

        Raises:
            NotImplementedError: 子类必须实现此方法
        """
        raise NotImplementedError

    def get_asset_type(self) -> str:
        """获取资产类型。

        Returns:
            资产类型字符串

        Raises:
            NotImplementedError: 子类必须实现此方法
        """
        raise NotImplementedError

    def get_server_time(self) -> int | float:
        """获取服务器时间戳。

        Returns:
            服务器时间戳

        Raises:
            NotImplementedError: 子类必须实现此方法
        """
        raise NotImplementedError

    def get_local_update_time(self) -> int | float:
        """获取本地更新时间戳。

        Returns:
            本地更新时间戳

        Raises:
            NotImplementedError: 子类必须实现此方法
        """
        raise NotImplementedError

    def get_account_id(self) -> str:
        """获取账户ID。

        Returns:
            账户ID字符串

        Raises:
            NotImplementedError: 子类必须实现此方法
        """
        raise NotImplementedError

    def get_account_type(self) -> str:
        """获取账户类型。

        Returns:
            账户类型字符串

        Raises:
            NotImplementedError: 子类必须实现此方法
        """
        raise NotImplementedError

    def get_can_deposit(self) -> bool:
        """检查账户是否可以存款。

        Returns:
            是否可以存款

        Raises:
            NotImplementedError: 子类必须实现此方法
        """
        raise NotImplementedError

    def get_can_trade(self) -> bool:
        """检查账户是否可以交易。

        Returns:
            是否可以交易

        Raises:
            NotImplementedError: 子类必须实现此方法
        """
        raise NotImplementedError

    def get_can_withdraw(self) -> bool:
        """检查账户是否可以提现。

        Returns:
            是否可以提现

        Raises:
            NotImplementedError: 子类必须实现此方法
        """
        raise NotImplementedError

    def get_fee_tier(self) -> int | str:
        """获取资金费率等级。

        Returns:
            资金费率等级

        Raises:
            NotImplementedError: 子类必须实现此方法
        """
        raise NotImplementedError

    def get_max_withdraw_amount(self) -> float:
        """获取最大可提现金额。

        Returns:
            最大可提现金额

        Raises:
            NotImplementedError: 子类必须实现此方法
        """
        raise NotImplementedError

    def get_total_margin(self) -> float:
        """获取总保证金。

        Returns:
            总保证金金额

        Raises:
            NotImplementedError: 子类必须实现此方法
        """
        raise NotImplementedError

    def get_total_used_margin(self) -> float:
        """获取已使用的总保证金。

        Returns:
            已使用的总保证金金额

        Raises:
            NotImplementedError: 子类必须实现此方法
        """
        raise NotImplementedError

    def get_total_maintain_margin(self) -> float:
        """获取总维持保证金。

        Returns:
            总维持保证金金额

        Raises:
            NotImplementedError: 子类必须实现此方法
        """
        raise NotImplementedError

    def get_total_available_margin(self) -> float:
        """获取总可用保证金。

        Returns:
            总可用保证金金额

        Raises:
            NotImplementedError: 子类必须实现此方法
        """
        raise NotImplementedError

    def get_total_open_order_initial_margin(self) -> float:
        """获取总开仓订单初始保证金。

        Returns:
            总开仓订单初始保证金金额

        Raises:
            NotImplementedError: 子类必须实现此方法
        """
        raise NotImplementedError

    def get_total_position_initial_margin(self) -> float:
        """获取总持仓初始保证金。

        Returns:
            总持仓初始保证金金额

        Raises:
            NotImplementedError: 子类必须实现此方法
        """
        raise NotImplementedError

    def get_total_unrealized_profit(self) -> float:
        """获取总未实现盈亏。

        Returns:
            总未实现盈亏金额

        Raises:
            NotImplementedError: 子类必须实现此方法
        """
        raise NotImplementedError

    def get_total_wallet_balance(self) -> float:
        """获取总钱包余额。

        Returns:
            总钱包余额

        Raises:
            NotImplementedError: 子类必须实现此方法
        """
        raise NotImplementedError

    def get_balances(self) -> list[dict[str, Any]]:
        """获取账户余额列表。

        Returns:
            包含余额信息的列表,每个元素包含资产、可用和冻结信息

        Raises:
            NotImplementedError: 子类必须实现此方法
        """
        raise NotImplementedError

    def get_positions(self) -> list[dict[str, Any]]:
        """获取持仓数据。

        Returns:
            包含持仓信息的列表

        Raises:
            NotImplementedError: 子类必须实现此方法
        """
        raise NotImplementedError

    def get_spot_maker_commission_rate(self) -> float:
        """获取现货maker佣金费率。

        Returns:
            现货maker佣金费率

        Raises:
            NotImplementedError: 子类必须实现此方法
        """
        raise NotImplementedError

    def get_spot_taker_commission_rate(self) -> float:
        """获取现货taker佣金费率。

        Returns:
            现货taker佣金费率

        Raises:
            NotImplementedError: 子类必须实现此方法
        """
        raise NotImplementedError

    def get_future_maker_commission_rate(self) -> float:
        """获取合约maker佣金费率。

        Returns:
            合约maker佣金费率

        Raises:
            NotImplementedError: 子类必须实现此方法
        """
        raise NotImplementedError

    def get_future_taker_commission_rate(self) -> float:
        """获取合约taker佣金费率。

        Returns:
            合约taker佣金费率

        Raises:
            NotImplementedError: 子类必须实现此方法
        """
        raise NotImplementedError

    def get_option_maker_commission_rate(self) -> float:
        """获取期权maker佣金费率。

        Returns:
            期权maker佣金费率

        Raises:
            NotImplementedError: 子类必须实现此方法
        """
        raise NotImplementedError

    def get_option_taker_commission_rate(self) -> float:
        """获取期权taker佣金费率。

        Returns:
            期权taker佣金费率

        Raises:
            NotImplementedError: 子类必须实现此方法
        """
        raise NotImplementedError

    def __str__(self) -> str:
        """返回账户数据的字符串表示。

        Returns:
            账户数据字符串

        Raises:
            NotImplementedError: 子类必须实现此方法
        """
        raise NotImplementedError

    def __repr__(self) -> str:
        """返回账户数据的正式字符串表示。

        Returns:
            账户数据字符串

        Raises:
            NotImplementedError: 子类必须实现此方法
        """
        raise NotImplementedError
