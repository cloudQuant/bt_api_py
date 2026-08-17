# 2026-06-17 BMad Python 3.9 Runtime Union Quality 迭代计划

## 背景

本轮继续执行 `bmad-help` 风格的重复质量迭代。标准 BMad catalog 仍不可用：

- `command -v bmad-help` 无输出。
- 本地未发现 `_bmad/_config/bmad-help.csv`。

因此本轮继续基于本地 `.claude/skills/bmad-help` 流程说明、当前代码扫描和质量门禁结果推进。

## 本轮输入证据

上一轮发现 `pyproject.toml` 声明 `target-version = "py39"`。继续扫描 Python 3.9 运行时兼容风险后发现：

1. 注解中的 `X | None` 在启用 `from __future__ import annotations` 的文件里不是本轮主要风险。
2. 赋值表达式中的 `CommandAck | Awaitable[CommandAck]` 会在模块导入时实际求值。
3. `bt_api_py/forwarding/transport.py` 和 `bt_api_py/forwarding/memory.py` 都存在该运行时类型别名。

这在 Python 3.9 运行环境下存在导入失败风险。

## 本轮实施

1. `bt_api_py/forwarding/transport.py`
   - 引入 `typing.Union`。
   - 将 `CommandHandler = Callable[[OrderCommand], CommandAck | Awaitable[CommandAck]]` 改为 `Union[CommandAck, Awaitable[CommandAck]]`。

2. `bt_api_py/forwarding/memory.py`
   - 引入 `typing.Union`。
   - 同样修复 `CommandHandler` 类型别名。

## 验收记录

已在本地完成以下验收：

```bash
pytest -q tests/test_forwarding_bus_router_client.py tests/test_forwarding_schema.py tests/test_forwarding_zmq_transport.py
# 17 passed in 2.39s

ruff check bt_api_py/forwarding tests/test_forwarding_bus_router_client.py tests/test_forwarding_schema.py tests/test_forwarding_zmq_transport.py
# All checks passed!

mypy bt_api_py/forwarding tests/test_forwarding_bus_router_client.py tests/test_forwarding_schema.py tests/test_forwarding_zmq_transport.py
# Success: no issues found in 13 source files

rg "=\\s*[A-Za-z_][A-Za-z0-9_\\.\\[\\], ]*\\|\\s*[A-Za-z_][A-Za-z0-9_\\.\\[\\], ]*" bt_api_py tests -n
# no matches

rg "isinstance\\([^\\n]*\\||issubclass\\([^\\n]*\\|" bt_api_py tests -n
# no matches

python3.9 --version
# command not found

ruff check bt_api_py tests
# All checks passed!

mypy bt_api_py tests
# Success: no issues found in 109 source files

bandit -q -r bt_api_py -c pyproject.toml
# exit code 0, no output

pytest -q
# 452 passed in 15.65s
```

本机没有 `python3.9` 可执行文件，因此本轮无法做 Python 3.9 解释器级导入验证；已通过静态扫描确认确定的运行时 union 别名风险清零。

## 后续候选项

1. 在 CI 中增加 Python 3.9 job，直接验证项目声明的最低 Python 版本。
2. 扫描 tests 中 fake aiohttp/session 支架是否可以进一步收敛，避免后续重复。
3. 继续评估 LogstashHandler 是否需要显式 retry/backoff 配置。
