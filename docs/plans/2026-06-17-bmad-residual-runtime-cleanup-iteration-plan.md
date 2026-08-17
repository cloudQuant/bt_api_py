# 2026-06-17 BMad Residual Runtime Cleanup 迭代计划

## 背景

本轮继续执行 `bmad-help` 风格的重复质量迭代。标准 BMad catalog 仍缺失，因此继续基于本地流程说明、当前代码扫描和质量门禁输出推进。

## 本轮输入证据

上一轮 monitoring 契约收敛后，继续扫描源码：

```bash
rg -n "pass\\s*(#|$)|raise NotImplementedError" bt_api_py -g '*.py'
```

扫描显示：

- `bt_api_py/brokers/base.py` 中的 `raise NotImplementedError` 均位于 `@abstractmethod`，属于抽象接口契约。
- `bt_api_py/feed_registry.py` 的 `pass` 位于 docstring 示例，不是运行时路径。
- `bt_api_py/security_compliance/core/audit_logger.py` 有一个加载历史 audit hash 时的静默 `pass`。
- `bt_api_py/security_compliance/core/identity_manager.py` 有一个 SAML 用户名密码认证分支中的空 `pass`。

## 改进机会

1. **Audit logger hash 加载失败缺少诊断信息**
   - 损坏或不完整的 audit log 尾行会被静默忽略。
   - 对审计系统而言，即使允许容错，也应留下 debug 诊断信息。

2. **SAML 密码认证分支意图不够明确**
   - SAML 不走 username/password，本来应返回 `None`。
   - 空 `pass` 让读者需要依赖后续 fallthrough 才能理解行为。

## 本轮实施

1. `bt_api_py/security_compliance/core/audit_logger.py`
   - 将 `_load_last_hash()` 中的静默 `pass` 改为 `_logger.debug("Could not load last audit hash: %s", exc)`。

2. `bt_api_py/security_compliance/core/identity_manager.py`
   - 将 SAML 分支改为显式 `return None`，并说明 SAML 使用 redirect/assertion flow。

## 验收记录

已在本地完成以下验收：

```bash
rg -n "pass\\s*(#|$)|raise NotImplementedError" bt_api_py -g '*.py'
# 仅剩 feed_registry.py docstring 示例和 brokers/base.py 抽象方法

pytest -q tests/test_audit_logger.py tests/test_security_compliance.py
# 113 passed in 3.82s

ruff check bt_api_py tests
# All checks passed!

mypy bt_api_py tests
# Success: no issues found in 109 source files

pytest -q
# 421 passed in 14.75s
```

## 后续候选项

当前显式 `pass` / 非抽象 `NotImplementedError` 已清理。后续仍可继续做更细的质量改进：

1. 对 `typing.Any` 使用做模块级盘点，优先从 monitoring/security compliance 开始。
2. 对 Prometheus formatter 的 label escaping、histogram formatting 做边界测试。
3. 对 audit logger 的损坏日志恢复策略补更明确的测试。
