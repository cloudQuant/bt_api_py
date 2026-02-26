"""
Auto-init mixin for Container classes
提供 _ensure_init() 方法，在访问解析后的字段时自动触发 init_data()
避免用户忘记手动调用 init_data() 导致返回 None

用法:
  在子类的 get_xxx() 方法中调用 self._ensure_init()
  或在基类的抽象 get 方法中统一调用
"""


class AutoInitMixin:
    """自动初始化 mixin，确保 init_data() 在数据访问前被调用"""

    def __init_subclass__(cls, **kwargs):
        super().__init_subclass__(**kwargs)

    def _ensure_init(self):
        """如果尚未初始化，自动调用 init_data()"""
        if not getattr(self, '_initialized', False):
            self.init_data()
            self._initialized = True
        return self
