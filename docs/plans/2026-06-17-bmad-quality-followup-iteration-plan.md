# 2026-06-17 BMad Code Quality Follow-up 迭代计划

## 背景

本轮是 `bmad-help` 质量改进的重复迭代。当前仓库仍缺少 `bmad-help` 工作流要求的 `_bmad/_config/bmad-help.csv` catalog，且本机没有可执行的 `bmad-help` 命令。因此本轮继续按 `.claude/skills/bmad-help/workflow.md` 的精神执行：读取本地流程说明、基于当前项目状态和工具输出分析下一步质量改进，再开发并验收。

## 本轮输入证据

已使用以下当前状态作为分析依据：

- `command -v bmad-help`：无输出，说明本机没有该命令。
- `find .. -path '*/_bmad/_config/bmad-help.csv'`：无输出，说明标准 BMad catalog 缺失。
- `ruff check bt_api_py tests --statistics`：发现 2 个 import 排序问题。
- `mypy bt_api_py tests`：清理配置前能通过主包检查，但会输出大量历史 mypy override 噪音；清理后暴露出 2 个测试文件中的枚举比较类型问题。
- `pytest -q`：清理前通过但存在 1 个 scikit-learn / SciPy deprecation warning。

## 改进机会

1. **主包与测试 ruff 基线没有完全收敛**
   - `bt_api_py/__init__.py` 和 `bt_api_py/testing/__init__.py` import block 未排序。

2. **mypy 配置存在大量历史结构遗留项**
   - `pyproject.toml` 中大量 override 指向已迁移或当前代码树不存在的模块，例如旧 `feeds`、`containers`、`functions`、`websocket` 等路径。
   - 这些 unused override 让 `warn_unused_configs` 失去信号价值。

3. **测试断言类型语义不够清晰**
   - `SeverityLevel` 是 `IntEnum`，测试直接拿枚举成员和整数比较。
   - `UserStatus` 状态转换测试在同一个变量上连续比较不同状态，mypy 会把前一次断言推断成窄类型。

4. **测试基线存在 deprecation warning**
   - `RiskEnsembleModel` 的 `LogisticRegression` 默认 `lbfgs` 路径在当前 sklearn/SciPy 组合下触发弃用 warning。

## 本轮实施

1. 使用 `ruff --fix` 修复 `bt_api_py/__init__.py` 和 `bt_api_py/testing/__init__.py` import 排序。
2. 清理 `pyproject.toml` 中已确认失效的 mypy overrides，仅保留当前代码树和测试扫描会使用的 override：
   - `bt_api_py.exceptions`
   - `bt_api_py.ctp_env_selector`
   - `bt_api_py.bt_api`
   - `bt_api_py.security_compliance.*`
   - `tests.*`
3. 修复测试类型断言：
   - `tests/test_audit_logger.py` 改为比较 `SeverityLevel.*.value`。
   - `tests/test_security_compliance.py` 在激活用户后重新通过 manager 查询 identity，再断言状态和属性。
4. 修复 ensemble deprecation warning：
   - `RiskEnsembleModel` 的基础 `logistic_regression` 和 stacking `meta_learner` 均改为 `LogisticRegression(solver="liblinear", max_iter=1000)`。
   - 移除对 `liblinear` 无意义的 `n_jobs=-1`。

## 验收记录

已在本地完成以下验收：

```bash
mypy bt_api_py tests
# Success: no issues found in 108 source files

ruff check bt_api_py tests
# All checks passed!

pytest -q tests/test_audit_logger.py tests/test_security_compliance.py
# 113 passed in 4.11s

pytest -q tests/test_ensemble_model.py -W error::DeprecationWarning
# 29 passed in 4.99s

pytest -q
# 417 passed in 14.32s
```

## 后续候选项

当前 `ruff`、`mypy`、`pytest` 基线已通过且 full pytest 无 warning。但“没有一点代码质量可以改进”仍不能据此证明，后续还可继续从以下方向做下一轮：

1. 对 `bt_api_py/security_compliance.*` 当前仍被 mypy `ignore_errors` 覆盖的模块做分批类型收敛。
2. 对 `bt_api_py/monitoring.prometheus` 的 `async_mode` 和 `bt_api_py/monitoring.elk` 的 UDP transport 未实现路径做产品层面的决策：补实现、显式标注能力不支持，或调整 API。
3. 对 `GatewayBridgeAdapter` 写路径未实现能力补充更明确的契约测试和文档。
4. 在 CI 中增加 `mypy bt_api_py tests` 和 `pytest -W error::DeprecationWarning` 的可选门禁，防止配置噪音和 warning 回归。
