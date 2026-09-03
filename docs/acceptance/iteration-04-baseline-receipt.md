# 迭代 04 本地基线收据

## 身份与范围

- 捕获时间：2026-09-03T03:31:18Z
- 仓库：cloudQuant/bt_api_py
- 基线：origin/dev@fdeb6c1182d8a4be5c9c2b713bba7007f1c03fa7
- 隔离分支：codex/iteration-04-runtime-contract-delivery-trust
- 工作树：/private/tmp/bt_api_py_iter04_impl_20260903
- Python：conda base 的 Python 3.11.8

本收据只证明该 SHA 的本地离线质量基线。它不证明真实交易所、子模块、GitHub 治理设置、TestPyPI、PyPI 或发布状态。

## 命令结果

| 命令 | 结果 | 输出 SHA-256 |
| --- | --- | --- |
| pytest 收集 | 640 tests collected | 0acd078c60524417f635a1abff1e00861cf1305b36ba97e6c53d54b02e294ef5 |
| 离线 pytest 标记集 | 629 passed、5 skipped、6 deselected | 219f12184cec63c1e7078bb2291bf370d035420d05b6cd625b05d48b2488e5fc |
| ruff check | All checks passed | 98733c3446b52dc3e6b6943142b19a142025d9727521f6abc3783a20e4b6d3c6 |
| ruff format --check | 180 files already formatted | db87de558ddedaff9456ae8455c45c4c3c079fc75d0c5ccd2cedbaf808b6c275 |
| mypy | Success: no issues found in 180 source files | 637a08b29fa43db3eed3c12d41f6a3e11d9afe7d86752efaf10cd94057ab92a1 |

完整命令和结构化字段见同名 JSON 收据。

## 环境注意事项

隔离工作树中 60 个 Git 子模块均未初始化，状态摘要 SHA-256 为 dc39b464368086ef172c6333eafdc70f02c7db5f6757bca79641dc28af44aab6。当前 conda 环境还存在位于其他目录的旧 editable bt_api_py 0.15.0；因此本迭代的 wheel 验收必须创建全新安装环境，不能以此基线的 import 成功替代。

当前主工作区的 bt_api/bt_api_ctp gitlink 改动不在本隔离工作树中，亦不属于本迭代候选变更。
