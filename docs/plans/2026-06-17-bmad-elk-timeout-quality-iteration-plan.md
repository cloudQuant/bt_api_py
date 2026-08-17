# 2026-06-17 BMad ELK Timeout Quality 迭代计划

## 背景

本轮继续执行 `bmad-help` 风格的重复质量迭代。标准 BMad catalog 仍不可用：

- `command -v bmad-help` 无输出。
- `find .. -path '*/_bmad/_config/bmad-help.csv'` 无输出。

因此本轮继续基于本地 `.claude/skills/bmad-help` 流程说明和当前代码扫描结果推进。

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

随后扫描运行时可靠性风险：

```bash
rg -n "requests\\.|httpx\\.|aiohttp\\.|urlopen\\(|urllib\\.request|subprocess|shell=True|eval\\(|exec\\(|yaml\\.load\\(" bt_api_py tests -g '*.py'
```

扫描结果显示主包里没有动态执行或 shell 调用风险；可收敛点集中在 `bt_api_py/monitoring/elk.py` 的 `aiohttp.ClientSession()` 没有显式 request timeout。

## 改进机会

ELK 集成会连接 Elasticsearch 和 Logstash。未设置 `aiohttp.ClientTimeout` 时，网络抖动、服务不可达或连接悬挂可能拖住监控初始化或日志发送路径。这里应提供默认 timeout，并允许上层配置覆盖。

## 本轮实施

1. `bt_api_py/monitoring/elk.py`
   - 增加 `DEFAULT_REQUEST_TIMEOUT = 10.0`。
   - `ElasticsearchClient` 增加 `request_timeout` 参数，并在 `aiohttp.ClientSession()` 中传入 `aiohttp.ClientTimeout(total=request_timeout)`。
   - `LogstashHandler` 增加 `request_timeout` 参数，并在 `aiohttp.ClientSession()` 中传入 timeout。
   - `ELKIntegration` 增加 `request_timeout` 参数，并传递给 Elasticsearch 和 Logstash 组件。

2. `bt_api_py/monitoring/config.py`
   - `MonitoringConfig` 增加 `elk_request_timeout`，默认 `10.0`。
   - `setup_monitoring()` 调用 `setup_elk_integration()` 时传入该配置。

3. `tests/test_monitoring_contracts.py`
   - 增加 fake `aiohttp`，不依赖网络即可验证 session timeout 参数。
   - 新增 Elasticsearch timeout 传递测试。
   - 新增 Logstash timeout 传递测试。

## 验收记录

已在本地完成以下验收：

```bash
pytest -q tests/test_monitoring_contracts.py
# 8 passed in 0.65s

bandit -q -r bt_api_py -c pyproject.toml
# exit code 0, no output

ruff check bt_api_py tests
# All checks passed!

mypy bt_api_py tests
# Success: no issues found in 109 source files

pytest -q
# 427 passed in 14.03s
```

## 后续候选项

当前网络请求 timeout 已覆盖 ELK session 创建路径。后续可以继续：

1. 对 ELK 搜索和索引响应解析补充更多失败路径测试。
2. 对 `setup_monitoring()` 的失败清理路径补集成级测试，确保部分组件启动失败时能回滚。
3. 将 `bandit`、`ruff`、`mypy`、`pytest` 的组合门禁固化到 CI。
