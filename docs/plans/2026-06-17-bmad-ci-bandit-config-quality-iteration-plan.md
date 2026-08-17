# 2026-06-17 BMad CI Bandit Config Quality 迭代计划

## 背景

本轮继续执行 `bmad-help` 风格的重复质量迭代。标准 BMad catalog 仍不可用：

- `command -v bmad-help` 无输出。
- 本地未发现 `_bmad/_config/bmad-help.csv`。

因此本轮继续基于本地 `.claude/skills/bmad-help` 流程说明、当前代码扫描和质量门禁结果推进。

## 本轮输入证据

上一轮增强了 CI compatibility smoke 测试路径。继续检查 CI 质量命令与本地门禁的一致性后发现：

1. 本地安全门禁使用 `bandit -q -r bt_api_py -c pyproject.toml`。
2. `pyproject.toml` 中已有 `[tool.bandit]` 配置，包含 `exclude_dirs` 和 skips。
3. `.github/workflows/tests.yml` 原先使用 `bandit -r bt_api_py -ll --skip B101,B311`，没有读取 pyproject。
4. `.github/workflows/optimized-tests.yml` 原先生成 bandit JSON 报告时也没有读取 pyproject。

这会造成本地与 CI 使用不同安全扫描规则，增加误报或漏报风险。

## 本轮实施

1. `.github/workflows/tests.yml`
   - 将 quality job 的 bandit 命令改为 `bandit -r bt_api_py -c pyproject.toml`。

2. `.github/workflows/optimized-tests.yml`
   - 将 security job 的 JSON 报告命令改为 `bandit -r bt_api_py -c pyproject.toml -f json -o bandit-report.json || true`。

## 验收记录

已在本地完成以下验收：

```bash
bandit -r bt_api_py -c pyproject.toml
# No issues identified.

python -c "import yaml; [yaml.safe_load(open(path)) for path in ['.github/workflows/tests.yml', '.github/workflows/optimized-tests.yml']]"
# exit code 0

ruff check bt_api_py tests
# All checks passed!

mypy bt_api_py tests
# Success: no issues found in 109 source files

bandit -q -r bt_api_py -c pyproject.toml
# exit code 0, no output

pytest -q
# 452 passed in 15.00s
```

## 后续候选项

1. 检查 CI 的 mypy 命令是否应与本地 `mypy bt_api_py tests` 保持一致。
2. 扫描 tests 中 fake aiohttp/session 支架是否可以进一步收敛，避免后续重复。
3. 继续评估 LogstashHandler 是否需要显式 retry/backoff 配置。
