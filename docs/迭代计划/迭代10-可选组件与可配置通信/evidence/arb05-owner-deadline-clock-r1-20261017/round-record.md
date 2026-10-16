# ARB05 本机 deadline clock 小轮

- 仓库/分支：`D:/bt_api_py` / `dev`。交付基线：`b09e2db0d49f831b4bbd739ad9167568681481fb`。候选源在 `3b8025b7f81ad664d7da80796497fcb7419eedb3` 上验收；后续基线提交未改本轮源路径。
- 范围：`bt_api_py/service_execution_group_authority.py` 的 deadline-clock 专属 hunks、`tests/integration/test_arb05_owner_deadline_clock.py`，以及本记录。精确 patch 排除同文件既有 `ExecutionStoreGroupCompletionVerifier` / DIM09 hunks；其他工作树改动不属于本轮。
- 行为：新数据库建立可信 wall/monotonic anchor；旧 schema-v1 数据库缺少 clock row 时一律 fail closed。已授权 deadline 按绝对 Unix ns 判断；检测到 wall rollback、monotonic regression 或非法时钟值即持久标记 untrusted。时钟推进与 hold 写入在同一 SQLite 事务内，拒绝不得留下早期 hold。
- 验收：三个新增 focused nodes `3 passed, 1 warning`。作者使用 `PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 python -m pytest -q -o addopts= --noconftest tests/integration/test_arb05_owner_deadline_clock.py`，因为仓库 pytest-asyncio collector 在测试收集前失败；唯一 warning 是禁用插件时的未知 asyncio 配置选项。Ruff、格式和 diff 检查通过。独立静态复核 `GO_STATIC`。
- 证据边界：包含本机 spawned owner restart 与 synthetic SQLite，不是双机或账户验收；双机 slice 明确延期，未声称 ARB05 正式 PASS。
- 提交哈希由根轮次账本在交付后记录；本记录与其自身提交互不引用。
