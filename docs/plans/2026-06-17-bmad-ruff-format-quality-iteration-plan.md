# 2026-06-17 BMad Ruff Format Quality 迭代计划

## 背景

本轮继续执行 `bmad-help` 风格的重复质量迭代。标准 BMad catalog 仍不可用：

- `command -v bmad-help` 无输出。
- 本地未发现 `_bmad/_config/bmad-help.csv`。

因此本轮继续基于本地 `.claude/skills/bmad-help` 流程说明、当前代码扫描和质量门禁结果推进。

## 本轮输入证据

上一轮将 CI quality job 的 mypy 范围与本地门禁统一。继续检查 CI 与本地验证差异后发现：

1. CI quality job 会运行 `ruff format --check bt_api_py tests`。
2. 本地此前重复门禁主要运行 `ruff check bt_api_py tests`，没有显式运行 format check。
3. 当前工作区执行 `ruff format --check bt_api_py tests` 时发现 12 个文件需要格式化。

## 本轮实施

1. 运行 `ruff format bt_api_py tests`。
2. Ruff formatter 机械格式化了 12 个文件：
   - `bt_api_py/brokers/gateway_bridge.py`
   - `bt_api_py/brokers/mock.py`
   - `bt_api_py/forwarding/client.py`
   - `bt_api_py/forwarding/hub.py`
   - `bt_api_py/forwarding/memory.py`
   - `bt_api_py/forwarding/service.py`
   - `bt_api_py/forwarding/transport.py`
   - `bt_api_py/monitoring/elk.py`
   - `bt_api_py/testing/contract_cases.py`
   - `tests/test_broker_contract.py`
   - `tests/test_broker_loader.py`
   - `tests/test_monitoring_contracts.py`

当前工作区已有大量历史改动，因此整体 `git diff --stat` 不等同于本轮 formatter 的全部来源。

## 验收记录

已在本地完成以下验收：

```bash
ruff format --check bt_api_py tests
# 109 files already formatted

ruff check bt_api_py tests
# All checks passed!

mypy bt_api_py tests
# Success: no issues found in 109 source files

bandit -q -r bt_api_py -c pyproject.toml
# exit code 0, no output

pytest -q
# 452 passed in 20.73s
```

## 后续候选项

1. 将 `ruff format --check bt_api_py tests` 纳入本地重复验收口径。
2. 扫描 tests 中 fake aiohttp/session 支架是否可以进一步收敛，避免后续重复。
3. 继续评估 LogstashHandler 是否需要显式 retry/backoff 配置。
