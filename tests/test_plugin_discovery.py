"""插件发现 smoke 测试：验证 bt_api.plugins entry-points 可被发现。"""

from __future__ import annotations


def test_all_plugin_entry_points_discoverable() -> None:
    """遍历 importlib.metadata.entry_points(group="bt_api.plugins") 断言 ≥61 个入口。"""
    from importlib.metadata import entry_points

    eps = [ep for ep in entry_points(group="bt_api.plugins")]
    names = {ep.name for ep in eps}
    assert len(names) >= 61, (
        f"expected >=61 adapters, found {len(names)}: {sorted(names)[:10]}..."
    )
