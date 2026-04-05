# bt_api_py Code Quality Standards

本文档记录项目的代码质量标准、工具链及近期优化，遵循行业最佳实践。

## 质量检查命令

| 命令 | 说明 |
|------|------|
| `make lint` | Ruff 语法/风格检查 |
| `make format` | Ruff 自动格式化 |
| `make type-check` | Mypy 类型检查 |
| `make security-scan` | Bandit 安全扫描 |
| `make check` | lint + type-check |
| `make pre-commit-run` | 运行全部 pre-commit 钩子 |
| `make test` | 运行测试套件 |

## 2025-03 优化（已执行）

### 1. 异常类型注解

- **exceptions.py**：为所有异常类的 `__init__` 参数添加完整类型注解
- 提升 IDE 补全与静态类型检查准确性

### 2. API 返回类型

- **bt_api.py**：`init_logger()` 添加 `-> object` 返回类型及 docstring

### 3. 异常处理改进

- **log_message.py**：`_get_project_logs_dir()` 中的 `except Exception` 增加 `as e` 与 debug 日志
- 便于排查包导入失败时的 fallback 路径

### 4. 安全扫描

- 新增 `bandit[toml]` 为 dev 依赖
- 新增 `make security-scan` 目标
- `pyproject.toml` 配置 `[tool.bandit]` 排除 CTP/tests

### 5. S113 requests timeout（2025-03 第二批）

- **functions/update_data/**：`download_swap_history_bar_from_binance.py`、`download_funding_rate_from_binance.py`、`download_bars_from_okex.py`、`download_spot_history_bar_from_okx.py`、`download_spot_history_bar_from_binance.py`、`update_exchange_symbol_info.py`：所有 `requests.get/post` 添加 `timeout=30`
- **functions/utils.py**：`get_public_ip()` 中 `requests.get` 添加 `timeout=10`
- 消除网络请求无限等待风险，符合安全最佳实践

### 6. pathlib 迁移（2025-03 第二批）

- **config_loader.py**：新增 `get_exchange_config_path(filename)` 辅助函数
- **containers/exchanges/**：`binance_exchange_data.py`、`okx_exchange_data.py`、`kraken_exchange_data.py`、`bitfinex_exchange_data.py` 配置路径迁移至 pathlib
- **functions/log_message.py**：`_get_project_logs_dir()`、`SpdLogManager` 中 `os.path` 迁移至 `pathlib.Path`
- 提升可读性与跨平台兼容性

### 7. 代码质量优化（2025-03 第三批）

- **S113 Kraken**：`live_kraken/request_base.py` 中 `req_lib.post()` 显式传入 `timeout=` 参数，消除 Ruff S113 静态检测误报
- **pathlib 扩展迁移**：`bitflyer`、`bitrue`、`bitunix`、`latoken`、`bigone`、`bithumb`、`satoshitango`、`mercado_bitcoin`、`zebpay`、`coincheck`、`kucoin`、`ib_web` 等 exchange_data 统一使用 `get_exchange_config_path()`
- **PERF 性能优化**：`pancakeswap_pool.py` 使用列表推导替代 `filter_by_tvl`/`filter_by_volume` 循环；`anomaly_detector.py`、`ensemble_model.py` 中 `_dict_to_features` 使用列表推导；`exchange_health.py`、`advanced_websocket_manager.py` 使用列表推导
- **logging_system**：`extra.update(kwargs)` 替代循环赋值
- **S110/S112 异常日志**：`live_ib_web_feed.py` portfolio 端点失败时记录 debug 日志；`monitoring/metrics.py` 中 metric.collect 失败时记录 debug 日志

### 8. 代码质量优化（2025-03 第四批）

- **S110/S112 异常日志**：`my_websocket_app.py` 代理解析、WebSocket 重启失败时增加 `logger.debug`；`monitoring/config.py` 清理资源失败时记录 debug 日志；`monitoring/elk.py` Logstash 发送失败时记录 debug；`monitoring/prometheus.py` 服务循环异常时记录 debug；`audit_logger.py` 解析/读取失败时记录 debug
- **PERF401 性能优化**：`pancakeswap_exchange_data.py` 稳定币与交易对使用 `list.extend` 替代循环 append；`live_cryptocom/request_base.py` 使用 `parts.extend()` 替代循环；`live_dydx/spot.py` K 线归一化使用列表推导
- **S113 requests timeout**：`tests/containers/symbols/test_binance_symbol.py`、`tests/containers/bars/test_ok_request_bar.py` 中 `requests.get` 添加 `timeout=30`

## 编码规范（AGENTS.md 摘要）

- **行宽**：100 字符
- **类型**：公共 API 使用类型注解，优先 3.11+ 语法
- **异常**：使用 `bt_api_py.exceptions` 中的自定义异常
- **文档**：Google 风格 docstring
- **命名**：类 PascalCase，函数 snake_case，常量 UPPER_SNAKE_CASE

## 渐进式改进项

| 项目 | 说明 | 参考 |
|------|------|------|
| S110/S112 | try-except 中增加 `logger.debug()` | ✅ 已修复核心模块（my_websocket、monitoring、audit_logger） |
| S113 | `requests` 调用添加 `timeout=` | ✅ 已修复（含 Kraken、tests） |
| PERF401/402/403 | 用列表/字典推导替代循环 | ✅ 已优化 pancakeswap、cryptocom、dydx 等 |
| pathlib 迁移 | `os.path` → `pathlib.Path` | ✅ exchange_data 已全部迁移 |

## 渐进式 Mypy 加强（2025-03）

- **已启用 `disallow_untyped_defs`**：`bt_api_py.exceptions`、`bt_api_py.event_bus`
- **后续扩展计划**：按模块逐步启用，建议顺序 `registry` → `bt_api` → `logging_*` → `containers` 子模块

## 测试覆盖率目标（2025-03）

- **fail_under**：已从 60 提升至 80
- 若 `make test-cov` 未达标，需补充单元测试；过渡期可临时降至 70

## 参考文档

- [AGENTS.md](https://github.com/cloudQuant/bt_api_py/blob/master/AGENTS.md) - AI 代理开发指南
- [安全实践](./guides/security_best_practices.md) - 安全实践
