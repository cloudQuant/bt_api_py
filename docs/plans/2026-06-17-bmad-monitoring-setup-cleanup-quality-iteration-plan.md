# 2026-06-17 BMad Monitoring Setup Cleanup 迭代计划

## 背景

本轮继续执行 `bmad-help` 风格的重复质量迭代。标准 BMad catalog 仍不可用：

- `command -v bmad-help` 无输出。
- `find .. -path '*/_bmad/_config/bmad-help.csv'` 无输出。

因此本轮继续基于本地 `.claude/skills/bmad-help` 流程说明和当前工作区证据推进。

## 本轮输入证据

本轮开始时基础门禁通过：

```bash
ruff check bt_api_py tests
# All checks passed!

mypy bt_api_py tests
# Success: no issues found in 109 source files

bandit -q -r bt_api_py -c pyproject.toml
# exit code 0, no output
```

继续分析 monitoring 可靠性时发现：

1. `bt_api_py.monitoring.config` 导入不存在的 `bt_api_py.logging_system`，导致该模块无法直接导入。
2. `setup_monitoring()` 在启动 metrics collection 后，如果 Prometheus、ELK 或 Grafana 步骤失败，会直接抛出异常，不会调用已有的 `cleanup_monitoring()` 回滚已启动资源。

## 本轮实施

1. `bt_api_py/monitoring/config.py`
   - 改为从 `bt_api_base.logging_factory` 导入 `get_logger`。
   - 在本模块内提供 `setup_logging_for_production()`，创建日志目录并使用标准库 `logging.basicConfig()` 配置日志。
   - `setup_monitoring()` 增加 `resources_started` 状态。
   - 如果后续步骤失败且已有监控资源启动，则先调用 `cleanup_monitoring()`，再重新抛出原始异常。

2. `tests/test_monitoring_contracts.py`
   - 新增 `setup_monitoring()` 失败回滚测试。
   - 测试覆盖：logging、metrics、Prometheus、ELK 已执行，Grafana 失败后调用 cleanup，并保留原始 `RuntimeError`。

3. `bt_api_py/monitoring/config.py`
   - `cleanup_monitoring()` 改为逐项清理 global monitoring、Prometheus exporter、ELK integration。
   - 单个清理步骤失败时记录 debug 信息，但继续执行后续清理步骤。
   - debug 日志调用改为 spdlog 兼容的单字符串形式，避免清理路径二次抛错。

4. `bt_api_py/monitoring/elk.py`
   - Logstash 发送失败路径的 debug 日志调用改为 spdlog 兼容的单字符串形式。

5. `tests/test_monitoring_contracts.py`
   - 新增 `cleanup_monitoring()` 部分失败仍执行所有清理步骤的测试。
   - 新增 Logstash writer 失败不会二次抛错的测试。

## 验收记录

已在本地完成以下验收：

```bash
pytest -q tests/test_monitoring_contracts.py
# 9 passed in 1.13s

ruff check bt_api_py tests
# All checks passed!

mypy bt_api_py tests
# Success: no issues found in 109 source files

bandit -q -r bt_api_py -c pyproject.toml
# exit code 0, no output

pytest -q
# 428 passed in 20.23s

pytest -q tests/test_monitoring_contracts.py
# 11 passed in 1.00s

ruff check bt_api_py tests
# All checks passed!

mypy bt_api_py tests
# Success: no issues found in 109 source files

bandit -q -r bt_api_py -c pyproject.toml
# exit code 0, no output

pytest -q
# 430 passed in 22.04s
```

## 后续候选项

当前 monitoring setup 的导入和失败回滚路径已收敛。后续可以继续：

1. 对 `setup_monitoring()` 成功路径补更完整的配置传递测试。
2. 对 ELK 搜索和索引响应解析补失败路径测试。
3. 对 monitoring 模块中其它 spdlog logger 调用做兼容性扫描。
