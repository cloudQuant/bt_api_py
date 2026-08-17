# 2026-06-17 BMad CI Python 3.9 Smoke Quality 迭代计划

## 背景

本轮继续执行 `bmad-help` 风格的重复质量迭代。标准 BMad catalog 仍不可用：

- `command -v bmad-help` 无输出。
- 本地未发现 `_bmad/_config/bmad-help.csv`。

因此本轮继续基于本地 `.claude/skills/bmad-help` 流程说明、当前代码扫描和质量门禁结果推进。

## 本轮输入证据

上一轮修复了 Python 3.9 运行时 union 类型别名问题，但本机没有 `python3.9` 可执行文件。继续检查 CI 后发现：

1. `.github/workflows/tests.yml` 已有 Python 3.9-3.14、Linux/macOS/Windows compatibility matrix。
2. `.github/workflows/optimized-tests.yml` 也已有同样的 extended compatibility matrix。
3. 两处 compatibility smoke 只运行 `tests/test_bt_api_quality.py`，不一定会导入 forwarding 和 monitoring 模块。

因此 CI 已覆盖 Python 3.9 版本矩阵，但 smoke 触达面不足，无法稳定捕获刚修复的 forwarding 运行时导入问题。

## 本轮实施

1. `.github/workflows/tests.yml`
   - 扩展 `SMOKE_TEST_PATHS`，新增：
     - `tests/test_forwarding_schema.py`
     - `tests/test_forwarding_bus_router_client.py`
     - `tests/test_monitoring_contracts.py`

2. `.github/workflows/optimized-tests.yml`
   - 同步扩展 `SMOKE_TEST_PATHS`。

这些测试不依赖外部交易所服务，适合作为跨平台、跨 Python 版本的轻量 smoke。

## 验收记录

已在本地完成以下验收：

```bash
pytest -q tests/test_bt_api_quality.py tests/test_forwarding_schema.py tests/test_forwarding_bus_router_client.py tests/test_monitoring_contracts.py
# 54 passed in 0.71s

python -c "import yaml; [yaml.safe_load(open(path)) for path in ['.github/workflows/tests.yml', '.github/workflows/optimized-tests.yml']]"
# exit code 0

ruff check bt_api_py tests
# All checks passed!

mypy bt_api_py tests
# Success: no issues found in 109 source files

bandit -q -r bt_api_py -c pyproject.toml
# exit code 0, no output

pytest -q
# 452 passed in 14.98s
```

## 后续候选项

1. 扫描 CI 中质量命令与本地 pyproject 配置是否一致，例如 bandit 是否使用同一份配置。
2. 扫描 tests 中 fake aiohttp/session 支架是否可以进一步收敛，避免后续重复。
3. 继续评估 LogstashHandler 是否需要显式 retry/backoff 配置。
