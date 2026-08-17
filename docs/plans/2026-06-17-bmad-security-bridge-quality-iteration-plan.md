# 2026-06-17 BMad Security and Bridge Quality 迭代计划

## 背景

本轮继续执行 `bmad-help` 风格的重复质量迭代。当前环境仍无法执行标准 BMad catalog 流程：

- `command -v bmad-help` 无输出。
- `find .. -path '*/_bmad/_config/bmad-help.csv'` 无输出。
- 本仓库仅存在 `.claude/skills/bmad-help` 的本地流程说明。

因此本轮继续以本地流程说明、当前工作区状态和质量工具输出作为依据，选择可验证的质量问题进行收敛。

## 本轮输入证据

本轮开始时：

- `ruff check bt_api_py tests` 通过。
- `mypy bt_api_py tests` 通过，但 `pyproject.toml` 仍保留 `bt_api_py.security_compliance.*` 整包 `ignore_errors`。
- `pytest -q` 通过。
- 上一轮计划文档中仍保留两个候选项：
  - 分批取消 `security_compliance.*` 的 mypy ignore。
  - 明确 `GatewayBridgeAdapter` 写路径未实现时的契约。

## 改进机会

1. **`security_compliance` 类型检查覆盖不足**
   - 整包 `ignore_errors` 会让该包内新增类型问题被静默跳过。
   - 使用不加载项目配置的 mypy 诊断后，发现该包自身清晰问题集中在 `OAuth2Provider.validate_jwt()` 返回 `Any`。

2. **`GatewayBridgeAdapter` 能力声明和实现不一致**
   - 环境变量 `BT_API_PY_BRIDGE_ENABLE_WRITE=1` 时，`capabilities()` 曾声明 `supports_destructive_write=True`。
   - 但实际 `place_order()` / `cancel_order()` 仍抛 `NotImplementedError`，调用方无法得到结构化 broker 错误。

## 本轮实施

1. `bt_api_py/security_compliance/auth/oauth2_provider.py`
   - 对 `jwt.decode()` 返回值增加 `cast("dict[str, Any]", payload)`，让 `validate_jwt()` 的类型契约明确。

2. `pyproject.toml`
   - 删除 `bt_api_py.security_compliance.*` 的整包 mypy `ignore_errors`。
   - 现在 `mypy bt_api_py tests` 会直接检查 security compliance 包。

3. `bt_api_py/brokers/gateway_bridge.py`
   - `capabilities()` 明确设置：
     - `supports_order_submit=False`
     - `supports_order_cancel=False`
     - `supports_destructive_write=False`
   - `place_order()` 和 `cancel_order()` 统一抛 `BrokerError(BrokerErrorCode.NOT_SUPPORTED, ...)`。
   - 移除环境变量开启后抛裸 `NotImplementedError` 的路径。

4. `tests/test_broker_contract.py`
   - 增加 gateway bridge 能力声明测试。
   - 增加环境变量开启时写路径仍返回结构化 `BrokerError` 的测试。

## 验收记录

已在本地完成以下验收：

```bash
pytest -q tests/test_oauth2_provider.py tests/test_oauth2_provider_quality.py tests/test_oauth2_provider_quality_v2.py tests/test_security_compliance.py
# 160 passed in 9.99s

pytest -q tests/test_broker_contract.py tests/test_broker_loader.py
# 19 passed in 0.86s

ruff check bt_api_py tests
# All checks passed!

mypy bt_api_py tests
# Success: no issues found in 108 source files

pytest -q
# 419 passed in 14.48s
```

## 后续候选项

当前基础门禁继续保持干净，但仍不能证明项目已经没有任何可改进点。后续建议继续：

1. 对 `monitoring.prometheus.start_prometheus_exporter(async_mode=True)` 的未实现路径做契约收敛：实现、拆分 API，或改为显式能力检查。
2. 对 `monitoring.elk` 的 UDP transport 未实现路径做同样处理，避免运行时裸 `NotImplementedError`。
3. 继续减少 broad `Any` 和运行时动态结构，优先从测试覆盖完整的 security/monitoring 模块开始。
4. 将 `mypy bt_api_py tests`、`ruff check bt_api_py tests`、`pytest -q` 固化为 CI 必跑门禁。
