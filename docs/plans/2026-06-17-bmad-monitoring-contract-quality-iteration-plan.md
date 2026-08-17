# 2026-06-17 BMad Monitoring Contract Quality 迭代计划

## 背景

本轮继续执行 `bmad-help` 风格的重复质量迭代。标准 BMad catalog 仍不可用，因此继续基于本地 `.claude/skills/bmad-help` 流程说明、当前代码扫描和质量门禁结果推进。

## 本轮输入证据

上一轮结束后：

- `ruff check bt_api_py tests` 通过。
- `mypy bt_api_py tests` 通过。
- `pytest -q` 通过。
- 源码扫描 `rg -n "pass\\s*(#|$)|raise NotImplementedError" bt_api_py -g '*.py'` 显示，除抽象基类外，运行时未实现路径集中在 monitoring：
  - `bt_api_py/monitoring/prometheus.py` 的 `async_mode=True`
  - `bt_api_py/monitoring/elk.py` 的 `transport="udp"`

## 改进机会

这两个路径都是用户可通过参数触发的运行时分支。用 `NotImplementedError` 表达这类配置不支持，会让调用方难以区分“抽象方法未实现”和“当前配置不被支持”。更合适的契约是直接返回 `ValueError`，并在测试中锁定。

## 本轮实施

1. `bt_api_py/monitoring/prometheus.py`
   - `start_prometheus_exporter(async_mode=True)` 改为抛 `ValueError("async_mode=True is not supported ...")`。

2. `bt_api_py/monitoring/elk.py`
   - `LogstashHandler(transport="udp").connect()` 改为抛 `ValueError("Unsupported transport: udp")`。

3. `tests/test_monitoring_contracts.py`
   - 新增 Prometheus async mode 不支持契约测试。
   - 新增 Logstash UDP transport 不支持契约测试。

4. `bt_api_py/monitoring/prometheus.py`
   - `PrometheusFormatter.format_labels()` 增加 label value 转义，处理反斜杠、换行和双引号。
   - label 输出按 key 排序，保证 exposition 文本稳定。

5. `tests/test_monitoring_contracts.py`
   - 新增 Prometheus label 空值、排序和特殊字符转义测试。

6. `bt_api_py/monitoring/prometheus.py`
   - 修复 histogram bucket label 解析，避免输出 `le=""0.1""` 这类非法双重引号。
   - 将 infinity bucket 从 `inf` 规范化为 Prometheus 常用的 `+Inf`。

7. `tests/test_monitoring_contracts.py`
   - 新增 histogram bucket exposition 测试，覆盖普通 bucket、`+Inf` bucket 和非法双重引号回归。

8. `bt_api_py/monitoring/prometheus.py`
   - `format_registry()` 的 metric value、HELP、TYPE 输出统一使用 `format_metric_name()` 规范化 metric name。
   - 避免包含 `.`、`-` 等字符的 registry metric 输出非法 Prometheus metric name。

9. `tests/test_monitoring_contracts.py`
   - 新增 registry 输出 metric name 规范化测试。

10. `bt_api_py/monitoring/prometheus.py`
    - `format_metric_name()` 增加首字符保护，避免数字开头的非法 metric name。
    - 新增 `format_label_name()`，按 Prometheus label 规则规范化 label key。

11. `tests/test_monitoring_contracts.py`
    - 新增 metric name 和 label name 规范化测试。

## 验收记录

已在本地完成以下验收：

```bash
pytest -q tests/test_monitoring_contracts.py
# 2 passed in 0.98s

rg -n "raise NotImplementedError" bt_api_py -g '*.py'
# 仅剩 bt_api_py/brokers/base.py 抽象方法

ruff check bt_api_py tests
# All checks passed!

mypy bt_api_py tests
# Success: no issues found in 109 source files

pytest -q
# 421 passed in 16.89s

pytest -q tests/test_monitoring_contracts.py
# 3 passed in 0.63s

ruff check bt_api_py tests
# All checks passed!

mypy bt_api_py tests
# Success: no issues found in 109 source files

pytest -q
# 422 passed in 14.32s

pytest -q tests/test_monitoring_contracts.py
# 4 passed in 0.64s

ruff check bt_api_py tests
# All checks passed!

mypy bt_api_py tests
# Success: no issues found in 109 source files

pytest -q
# 423 passed in 14.46s

pytest -q tests/test_monitoring_contracts.py
# 5 passed in 1.45s

ruff check bt_api_py tests
# All checks passed!

mypy bt_api_py tests
# Success: no issues found in 109 source files

pytest -q
# 424 passed in 14.83s

pytest -q tests/test_monitoring_contracts.py
# 6 passed in 1.37s

ruff check bt_api_py tests
# All checks passed!

mypy bt_api_py tests
# Success: no issues found in 109 source files

pytest -q
# 425 passed in 15.20s
```

## 后续候选项

当前运行时 `NotImplementedError` 已从 monitoring 中移除，剩余项集中在抽象 broker 基类。后续可以继续：

1. 审计剩余 `pass` 是否属于文档示例、受控异常处理，或需要显式日志。
2. 对 monitoring 的 Prometheus formatter 和 ELK handler 补更多边界测试。
3. 扫描 broad `Any` 使用，优先处理测试覆盖较充分的 monitoring 与 security compliance 模块。
