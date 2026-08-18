"""插件发现 smoke 测试：验证 bt_api.plugins entry-points 可被发现。

不再以固定数量（如 >=61）判定成功——插件规模由 bundle 清单
（configs/exchange-bundles.toml）和 doctor 命令解释，见 U-08。
"""

from __future__ import annotations

from importlib.metadata import entry_points


def test_plugin_entry_points_are_discoverable() -> None:
    """遍历 bt_api.plugins entry-points，断言非空且每个入口结构合法。"""
    eps = list(entry_points(group="bt_api.plugins"))
    assert eps, "no bt_api.plugins entry points discovered"
    names = {ep.name for ep in eps}
    assert names, "entry point names must be non-empty"
    for ep in eps:
        assert ep.name, "entry point must have a name"
        assert ep.value, f"entry point {ep.name} must have a value (module:attr)"
