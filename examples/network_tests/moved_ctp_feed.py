"""已迁移的 CTP feed 测试（占位墓碑）。

原离线用例已迁移至 ``bt_api/bt_api_ctp/tests/test_ctp_feed.py``
（``TestCtpImports`` / ``TestCtpContainerParsing`` / ``TestCtpOrderThreadingRegression``
三类测试与 Mock 工具均在彼处）；
联网用例在 ``examples/network_tests/test_ctp_feed_network.py``。

本文件仅保留指针：既避免旧链接失效，也说明原先正文所引用的
``bt_api_py.ctp`` / ``bt_api_py.feeds.live_ctp_feed`` 等路径已随架构调整
（CTP 迁至 ``bt_api/bt_api_ctp``）而失效，正文副本已删除以免重复维护。

模块级 ``pytest.skip`` 保证 pytest 不会重复收集本文件。
"""

from __future__ import annotations

import pytest

pytest.skip(
    "Moved to bt_api/bt_api_ctp/tests/test_ctp_feed.py (offline) and "
    "examples/network_tests/test_ctp_feed_network.py (network)",
    allow_module_level=True,
)
