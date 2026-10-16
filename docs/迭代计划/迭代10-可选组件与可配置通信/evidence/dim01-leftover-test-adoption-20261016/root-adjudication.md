# DIM01 遗留测试收养根裁定（2026-10-16）

**裁定：GO——risk `tests/test_trade_identity_claims.py` 以测试-only 提交收养（risk `f56af2b`），
父仓更新 gitlink `e2bedb3 → f56af2b`；DIM01 保持正式 PASS，不新增 PASS（28/68）。**

## 背景

第160轮 DIM01 trade_id 域隔离正式通过并在 risk 提交 `089b5efd`（admission/exposure）与 SDK 5 文件
（父仓 `bb762465`）。收尾时工作区遗留一份未提交新测试 `tests/test_trade_identity_claims.py`
（878 行、9 节点、sha256 `c0d977ce2d77b8a6cb4f73e54862661b8cd69dbd03dd43f1d24eb04751af13e2`，
docstring 明确写 "DIM01 trade-identity declaration, mode binding, and atomic occupancy"）。
DIM03 独立复核（`.codex-tmp/dim03-review-status.md`）曾将其登记为 FOREIGN（DIM01）保持不动。
收养先例：`ee189896`「test(sdk): adopt leftover journal decimal-match test」。

## 收养验证（2026-10-16，本轮）

- 固定树：`git -C bt_api/bt_api_risk archive e2bedb3 | tar -x -C .../it10dim01-leftover-adopt-20261016-r1/tree`；
  测试文件放入 tree（sha 与工作区同为 `c0d977ce…`）。即"已提交风险生产态 + 本测试文件"＝即将产生的提交树。
- 运行（序列化 ps 检查：仅驻留守护进程 128016/132224，无等待）：
  ```
  cd D:/temp/it10dim01-leftover-adopt-20261016-r1/tree
  PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 PYTHONDONTWRITEBYTECODE=1 PYTHONNOUSERSITE=1 \
  PYTHONPATH="D:/temp/it10dim01-leftover-adopt-20261016-r1/tree/src" \
  C:/anaconda3/python.exe -m pytest --noconftest --import-mode=importlib -p no:cacheprovider -q \
    --basetemp=D:/temp/it10dim01-leftover-adopt-20261016-r1/run1/pytest-tmp \
    --junitxml=D:/temp/it10dim01-leftover-adopt-20261016-r1/run1-junit.xml \
    tests/test_trade_identity_claims.py
  ```
- 结果：EXIT=0；`9 passed, 1 warning in 3.11s`；junit 9 tests / 0 failures / 0 errors / 0 skipped；
  唯一 warning 为 pyproject `[tool.pytest.ini_options]` 未知配置键（无害）。
- 结论：测试与已提交生产（含 DIM01 latch + 域占用、e2bedb3 链）完全兼容，无未提交依赖。

## 提交与保护

- risk `f56af2b`「test(risk): adopt DIM01 leftover trade-identity claims test」（1 file, +878；仅此文件）。
- DIM04 未提交 WIP（`src/bt_api_risk/core/admission.py` +432/-5）与 `.codex-tmp/` 原样保留、未被吞吐。
- 父仓本提交：gitlink `bt_api/bt_api_risk` `e2bedb3 → f56af2b` + 本证据目录。

## 证据

- run1-stdout.txt / run1-stderr.txt / run1-junit.xml（本目录）；完整 tmp 树保留于
  D:/temp/it10dim01-leftover-adopt-20261016-r1/（含 tree/ 提取与 basetemp）。
