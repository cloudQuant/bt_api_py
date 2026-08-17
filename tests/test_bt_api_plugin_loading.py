"""延迟插件加载测试（A-10）。"""

from __future__ import annotations

import bt_api_py.bt_api as bt_api_module
from bt_api_py.bt_api import BtApi


def test_import_does_not_load_plugins(monkeypatch) -> None:
    """import bt_api_py.bt_api 不应触发插件加载（_plugins_loaded 初始为 False）。"""
    monkeypatch.setattr(bt_api_module, "_plugins_loaded", False)
    # 重新 import 不改变 flag（惰性加载在 BtApi 实例化时触发）
    import importlib

    importlib.reload(bt_api_module)
    assert bt_api_module._plugins_loaded is False


def test_first_bt_api_init_loads_plugins(monkeypatch) -> None:
    """首次 BtApi 实例化触发插件加载。"""
    monkeypatch.setattr(bt_api_module, "_plugins_loaded", False)
    BtApi(None, debug=False)
    assert bt_api_module._plugins_loaded is True


def test_plugin_load_failure_does_not_break_init(monkeypatch) -> None:
    """插件加载抛异常时 BtApi 实例化不崩溃（异常降级为告警）。"""
    monkeypatch.setattr(bt_api_module, "_plugins_loaded", False)

    def broken_load() -> None:
        raise RuntimeError("boom")

    monkeypatch.setattr(
        bt_api_module, "_initialize_plugin_and_legacy_registrations", broken_load
    )
    api = BtApi(None, debug=False)
    assert api is not None
    assert bt_api_module._plugins_loaded is True  # finally 里置 True
