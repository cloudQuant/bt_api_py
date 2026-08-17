# 2026-06-17 BMad CI MyPy Scope Quality 迭代计划

## 背景

本轮继续执行 `bmad-help` 风格的重复质量迭代。标准 BMad catalog 仍不可用：

- `command -v bmad-help` 无输出。
- 本地未发现 `_bmad/_config/bmad-help.csv`。

因此本轮继续基于本地 `.claude/skills/bmad-help` 流程说明、当前代码扫描和质量门禁结果推进。

## 本轮输入证据

上一轮统一了 CI bandit 命令与 pyproject 配置。继续检查 CI 与本地门禁的一致性后发现：

1. 本地门禁使用 `mypy bt_api_py tests`。
2. `tests` 下已经有大量契约测试和 fake 支架，类型问题同样会影响后续维护。
3. `.github/workflows/tests.yml` 原先只运行 `mypy bt_api_py --ignore-missing-imports`，没有覆盖测试代码。

这会导致本地检查范围和 CI 检查范围不一致。

## 本轮实施

1. `.github/workflows/tests.yml`
   - 将 quality job 的 mypy 命令改为 `mypy bt_api_py tests --ignore-missing-imports`。
   - 保留 CI 原有 `--ignore-missing-imports` 参数，避免引入额外第三方 stub 噪声。

## 验收记录

已在本地完成以下验收：

```bash
mypy bt_api_py tests --ignore-missing-imports
# Success: no issues found in 109 source files

python -c "import yaml; yaml.safe_load(open('.github/workflows/tests.yml'))"
# exit code 0

ruff check bt_api_py tests
# All checks passed!

mypy bt_api_py tests
# Success: no issues found in 109 source files

bandit -q -r bt_api_py -c pyproject.toml
# exit code 0, no output

pytest -q
# 452 passed in 14.81s
```

## 后续候选项

1. 检查 CI quality job 是否需要运行 `ruff format --check` 的本地对应命令。
2. 扫描 tests 中 fake aiohttp/session 支架是否可以进一步收敛，避免后续重复。
3. 继续评估 LogstashHandler 是否需要显式 retry/backoff 配置。
