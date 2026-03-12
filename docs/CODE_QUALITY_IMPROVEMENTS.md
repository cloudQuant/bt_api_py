# Code Quality Analysis & Improvements

本报告基于行业最佳实践对 bt_api_py 项目进行代码质量分析，并记录已执行的优化与后续建议。

## 已执行的优化

### 1. 文档与配置修复

- **AGENTS.md**：修复 `### Linting and Formatting### Linting and Formatting` 重复标题
- **config_loader.py**：使用 `pathlib.Path` 替代 `os.path`，提升可读性与跨平台性
- **balancer_exchange_data.py**：配置加载路径迁移至 pathlib
- **pyproject.toml**：新增 `RUF100` 规则，自动检测并移除未使用的 `noqa` 注释

### 2. 类型注解改进

- **btc_markets_ticker.py**：为 `last_price`、`bid_price` 等添加 `float | None` 显式类型注解
- **gemini_order.py**：移除冗余 `type: ignore`，补充正确的类型注解
- **gemini_bar.py**：移除未使用的 `type: ignore` 注释
- **binance_bar.py**：移除未使用的 `type: ignore` 注释

### 3. 代码清理

- **RUF100 自动修复**：通过 `ruff check --fix` 移除 43 处未使用的 `noqa` 指令

### 4. 工具链一致性

- **pre-commit**：mypy 使用 `--config-file=pyproject.toml`，与项目配置保持一致

## 当前质量基线

| 检查项 | 状态 |
|--------|------|
| Ruff lint | ✅ 通过 |
| Ruff format | ✅ 通过 |
| Mypy | ⚠️ 约 976 个错误（187 个文件） |
| 测试 | 需运行 `make test` 验证 |

## 后续建议（按优先级）

### 高优先级

1. **Mypy 类型检查**
   - `bt_api.py`、`registry.py`：**已通过** ✅
   - `containers/`：约 900+ 个错误，建议按子模块分批修复
   - 考虑在 `pyproject.toml` 中为部分模块启用 `disallow_untyped_defs`

2. **PTH（pathlib）分批迁移**
   - 已迁移：`config_loader.py`、`balancer_exchange_data.py`、`binance_exchange_data.py`、`okx_exchange_data.py`、`kraken_exchange_data.py`、`bitfinex_exchange_data.py`、`log_message.py`
   - 新增 `config_loader.get_exchange_config_path(filename)` 供其余 exchange_data 复用
   - 新代码优先使用 `pathlib.Path`，旧代码在改动时顺带迁移

### 中优先级

3. **提升测试覆盖率**
   - 当前 `fail_under = 60`，建议逐步提升至 80%+
   - 为核心 API 与 feeds 增加单元测试

4. **统一异常处理**
   - 确保对外 API 使用 `bt_api_py.exceptions` 中的异常类型
   - 避免裸露的 `Exception` 或通用异常

5. **文档与 Docstring**
   - 为公共 API 补充 Google 风格 docstring
   - 为复杂业务逻辑添加必要注释

### 低优先级

6. **S（bandit）安全规则评估**
   - 全量启用 `S` 约 82 个问题
   - 建议优先关注：S113（requests 无 timeout）、S506（yaml.load 应改用 safe_load）
   - S105（PASSWORD 枚举）、S101（assert）等多为误报，可忽略或按文件豁免

7. **PERF 性能规则评估**
   - 全量启用 `PERF` 约 39 个问题
   - 多为 PERF102（.items()→.values()/.keys()）、PERF401（list.extend/listcomp）
   - 无自动修复，需人工审查后修改

## 参考命令

```bash
# 运行全部质量检查
make check

# 格式化代码
make format

# 类型检查
make type-check

# 测试（含覆盖率）
make test-cov

# 预提交检查
make pre-commit-run
```

## 参考资源

- [AGENTS.md](../AGENTS.md) - 项目 AI 开发指南
- [Ruff Rules](https://docs.astral.sh/ruff/linter/rules/)
- [PEP 484 – Type Hints](https://peps.python.org/pep-0484/)
- [PEP 561 – Distributing and Packaging Type Information](https://peps.python.org/pep-0561/)
