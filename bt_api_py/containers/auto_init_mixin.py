"""
Auto-init mixin for Container classes
在访问 get_* 方法时自动触发 init_data()，避免用户忘记手动调用导致返回 None

用法:
  让基类继承 AutoInitMixin，所有 get_* 方法会自动在首次调用前触发 init_data()

  class AccountData(AutoInitMixin):
      ...

  # 以下两种写法等价:
  account.init_data()
  account.get_balance()
  # 等价于:
  account.get_balance()  # 自动触发 init_data()
"""

from __future__ import annotations

__all__ = ["AutoInitMixin"]


class AutoInitMixin:
    """自动初始化 mixin，确保 init_data() 在数据访问前被调用

    通过 __getattribute__ 拦截 get_* 方法调用，在首次访问时自动调用 init_data()。
    直接调用 init_data() 仍然有效，不会重复执行。
    """

    def _ensure_init(self) -> AutoInitMixin:
        """如果尚未初始化，自动调用 init_data()"""
        if not getattr(self, "_initialized", False):
            # Guard against re-entrant calls: init_data() may call get_*
            # methods on self, which would trigger _ensure_init() again.
            # Set flag BEFORE init_data() to prevent recursion, but track
            # success to handle exceptions properly.
            self._initialized = True
            try:
                self.init_data()
            except BaseException:
                # Reset flag if init_data() fails, allowing retry
                # BaseException catches all including KeyboardInterrupt/SystemExit
                # but we re-raise immediately, preserving the original exception
                self._initialized = False
                raise
        return self

    def __getattribute__(self, name: str) -> object:
        # 对 get_* 方法（排除 get_event/get_event_type/get_data 等无需解析的方法）
        # 自动触发 _ensure_init()
        attr = super().__getattribute__(name)
        if (
            name.startswith("get_")
            and name not in ("get_event", "get_event_type", "get_data")
            and callable(attr)
        ):
            try:
                initialized = object.__getattribute__(self, "_initialized")
            except AttributeError:
                initialized = False
            if not initialized:
                self._ensure_init()
        return attr
