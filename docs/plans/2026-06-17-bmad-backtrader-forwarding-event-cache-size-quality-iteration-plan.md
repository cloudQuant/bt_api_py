# BMAD 质量迭代计划 - Backtrader ForwardingStore 透传 event_cache_size

日期: 2026-06-17

## 背景

本轮继续按本地 `.claude/skills/bmad-help/workflow.md` 的方式执行质量迭代。标准 `_bmad/_config/bmad-help.csv` 在当前项目中不可用，因此无法加载完整 BMAD catalog。本轮检查 `bt_api_py.forwarding` 新增客户端实时事件缓存上限后，sibling `backtrader` 适配层是否同步暴露该能力。

`ForwardingClient` 和 `ZmqForwardingClient` 已支持 `event_cache_size`，用于限制慢消费者的本地实时事件缓存。但 `~/Documents/new_projects/backtrader/backtrader/stores/forwardingstore.py` 仍只透传 `command_timeout_ms`，策略用户通过 `ForwardingStore` 无法配置事件缓存上限。

## 问题

- `ForwardingStore` 没有 `event_cache_size` 参数。
- embedded `ForwardingClient` 创建时无法从 Backtrader 层配置缓存上限。
- ZMQ `ZmqForwardingClient` 创建时也无法从 Backtrader 层配置缓存上限。
- Backtrader README 未说明该参数，导致文档与 `bt_api_py.forwarding` 新能力不同步。

## 方案

- 在 `ForwardingStore.__init__` 增加 `event_cache_size: Optional[int] = 4096`。
- 创建 embedded `ForwardingClient` 时传入 `event_cache_size`。
- 创建 `ZmqForwardingClient` 时传入 `event_cache_size`。
- 更新 backtrader 单元测试，覆盖 embedded 和 ZMQ client 参数透传。
- 更新 backtrader README 的 forwarding 配置说明。

## 验收口径

执行并通过:

```bash
pytest tests/unit/stores/test_forwardingstore.py -q
python -m py_compile backtrader/stores/forwardingstore.py tests/unit/stores/test_forwardingstore.py
```

在当前 `bt_api_py` 仓库继续执行完整门禁:

```bash
ruff format --check bt_api_py tests
ruff check bt_api_py tests
mypy bt_api_py tests
bandit -q -r bt_api_py -c pyproject.toml
pytest -q
```

## 已完成变更

- 更新 sibling `backtrader/backtrader/stores/forwardingstore.py`
  - `ForwardingStore.__init__` 新增 `event_cache_size: Optional[int] = 4096`。
  - embedded `ForwardingClient` 创建时透传 `event_cache_size`。
  - ZMQ `ZmqForwardingClient` 创建时透传 `event_cache_size`。
- 更新 sibling `backtrader/tests/unit/stores/test_forwardingstore.py`
  - 增加 embedded client 参数透传测试。
  - 增加 ZMQ client 参数透传测试。
- 更新 sibling `backtrader/README.md`
  - 在英文和中文 forwarding 章节补充 `event_cache_size`。
  - 在 ZeroMQ 和嵌入式示例中展示 `event_cache_size=4096`。

## 验收结果

sibling `backtrader` 已执行并通过:

```bash
pytest tests/unit/stores/test_forwardingstore.py -q
python -m py_compile backtrader/stores/forwardingstore.py tests/unit/stores/test_forwardingstore.py
git diff --check -- backtrader/stores/forwardingstore.py tests/unit/stores/test_forwardingstore.py README.md
```

结果:

- pytest: 7 passed in 1.21s
- py_compile: exit 0
- git diff --check: exit 0

当前 `bt_api_py` 仓库已执行并通过:

```bash
ruff format --check bt_api_py tests
ruff check bt_api_py tests
mypy bt_api_py tests
bandit -q -r bt_api_py -c pyproject.toml
pytest -q
```

结果:

- ruff format: 109 files already formatted
- ruff check: All checks passed
- mypy: Success, no issues found in 109 source files
- bandit: exit 0
- pytest: 492 passed in 15.44s

## 后续候选

- 将 `event_cache_size` 加入 Backtrader forwarding 示例里的生产配置片段。
- 为 Backtrader `ForwardingStore` 增加 `stats()` 代理方法，方便策略端查看 forwarding client 诊断信息。
