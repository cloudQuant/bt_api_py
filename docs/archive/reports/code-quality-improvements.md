# Code Quality Improvements Report

本文档记录 2025-03 对 bt_api_py 项目执行的代码质量改进及后续建议。

## 2025-03-10 优化（行业最佳实践）

### 配置与工具链

- **pyproject.toml**: 移除重复的 mypy override（`live_yobit.*` 出现两次）
- **ruff**: 新增 S (security)、PERF (performance)、PIE790 (no-unnecessary-pass) 规则
- **ruff --fix**: 自动修复 236+ 处（含 PIE790 冗余 pass、PERF102 items→values/keys 等）

### 代码优化

- **core/services.py**: PERF401 - 使用列表推导替代循环 append
- **abstract_feed.py**: PERF401 - 使用列表推导构建 `missing`
- **live_bitget/spot.py**: PERF401 - Ticker/Balance 解析改用列表推导

### 版本控制清理

- **git rm**: 从版本库移除 8 个 `.bak` 备份文件（已在 .gitignore）

### 暂缓项（渐进修复）

以下规则已启用但部分忽略，建议分阶段修复：

| 规则 | 说明 | 建议 |
|------|------|------|
| S110/S112 | try-except-pass/continue | 增加 `logger.exception()` 或重抛 |
| S113 | requests 无 timeout | 为所有 `requests.get/post` 添加 `timeout=` |
| PERF401/402/403 | 列表/字典推导 | 按模块逐步替换循环 |
| S301 | pickle 反序列化 | ML 模型可考虑 joblib 或 safetensors |
| S506 | yaml.FullLoader | 配置文件可信时可保留，或改用 safe_load |

## 历史优化

### 1. 类型注解增强 (Type Safety)

- **base_stream.py**: 为 `_thread` 添加 `threading.Thread | None` 类型注解，修复 mypy 对线程变量的推断
- **live_mexc/market_wss_base.py**: 为 `ws`、`thread` 添加正确类型
- **live_mexc/account_wss_base.py**: 为 `ws`、`thread`、`listen_key` 添加类型注解
- **monitoring/decorators.py**: 为装饰器 wrapper 的动态属性 `_monitoring_*` 添加 `# type: ignore[attr-defined]`，解决 mypy 对装饰器模式的误报

### 2. 运行时 Bug 修复

- **kucoin_orderbook.py**: 修复 `float + "_orders"` 的 TypeError（Python 中 float 与 str 不能直接相加）
  - 改为使用 `f"{price_float}_orders"` 构建字符串键
  - 为 `bid_dict`、`ask_dict` 添加类型注解 `dict[float | str, float]`
  - 修复 `bid_dict.get(f"{p}_orders", 1)` 的 key 类型不匹配问题

### 3. 数据安全与空值处理

- **dydx_balance.py**:
  - `init_data()`: 添加 `balance_data is None` 检查，避免在未解析 JSON 时访问
  - `get_server_time()`: 返回类型改为 `float | None`（原本返回 None 但声明为 float）
  - `get_symbol_name()` / `get_asset_type()`: 使用 `or ""` 避免返回 `None` 时的类型不一致

### 4. 项目清理

- 删除 `bt_api_py/containers/orders/gateio_order.py.bak` 备份文件
- `.gitignore`: 添加 `*.bak` 忽略模式
- `pyproject.toml` ruff exclude: 添加 `*.bak` 通配符

## 后续建议（需较大改动）

### mypy 类型检查

当前 `make type-check` 仍有约 85 处 mypy 报错，主要集中在：

1. **容器类继承不一致**: `BalanceData.init_data()` 基类返回 `None`，部分子类返回 `self`（如 coinbase、bybit、bitget）用于链式调用
2. **all_data 类型推断**: 多处 `all_data` 变量需要显式类型注解
3. **json.loads 参数类型**: `balance_info` 可能为 `dict | str`，需在调用前做类型判断
4. **no-any-return**: 从 dict 取值后直接 return，mypy 推断为 Any，需显式 cast

**建议**: 分阶段修复
- 短期: 在 `pyproject.toml` 中为特定模块添加 `[[tool.mypy.overrides]]`，放宽 `no-any-return`、`has-type` 等
- 中期: 统一 `BalanceData.init_data()` 签名，考虑使用 `typing.Self` 支持链式调用
- 长期: 逐步为容器类添加完整类型注解

### 其他最佳实践

1. **TODO 项**: 项目中存在若干 `# TODO`（如 gateio WebSocket 解析），建议建 issue 跟踪
2. **测试覆盖率**: 当前 `fail_under = 60`，可逐步提升目标
3. **文档**: 核心 API 可补充 Google-style docstring 与示例
4. **安全**: 敏感配置（`account_config.yaml` 已在 gitignore）确保不进入版本控制

## 验证命令

```bash
make format   # 代码格式化
make lint     # Ruff 检查
make type-check  # mypy 类型检查
make test     # 运行测试
make check    # 全量质量检查
```

## 参考

- [AGENTS.md](../AGENTS.md) - 项目编码规范
- [Python Type Hints - PEP 484](https://peps.python.org/pep-0484/)
- [Ruff Rules](https://docs.astral.sh/ruff/rules/)
