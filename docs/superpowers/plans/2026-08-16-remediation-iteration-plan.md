# bt_api_py 质量整改迭代计划(Iteration Plan)

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 消除 P0 级资金风险与数据丢失风险,恢复测试与发布链路的可信度,把 61 仓适配器体系收敛到统一质量基线。

**Architecture:** 6 个迭代,每个迭代独立可发布、独立可验收。迭代 0 止血(子模块改动提交),迭代 1 硬化订单链路,迭代 2 恢复测试诚信(可与 1 并行),迭代 3 修复发布链路,迭代 4 修复适配器正确性,迭代 5 架构收敛与安全加固。所有代码改动 TDD 先行,每个迭代结束过 code-reviewer agent。

**Tech Stack:** Python 3.11–3.13、pytest、pytest-asyncio、git(61 个子模块)、GitHub Actions、PyPI(twine)、mkdocs。

**Spec:** `docs/superpowers/plans/2026-08-16-spec-analysis.md`(本文档所有任务引用其中的 Findings ID,如 A-01)。

## Global Constraints

- 支持的 Python:3.11–3.13(publish 矩阵口径);pyproject `requires-python` 与此对齐。
- TDD 强制:每个代码任务先写失败测试(RED)→ 跑测试确认失败 → 最小实现(GREEN)→ 跑测试确认通过 → 提交;新改模块行覆盖率 ≥80%(`pytest --cov`)。
- 版本单一源:包版本只存在于 pyproject.toml,运行时用 `importlib.metadata` 读取;禁止再引入第二个版本常量。
- 子模块纪律:任何子模块改动先在子模块仓库 commit+push,再回母仓库更新 pin;在迭代 0 完成前,**禁止任何人执行 `git submodule update --force` / `--init --recursive`**(会丢弃 59 仓未提交改动)。
- 安全红线:新代码禁止硬编码密钥/生产 URL;密钥只从 env/config 读;日志必须走脱敏出口;禁止 pickle 加载不可信文件。
- 文件规模:新拆分出的模块 ≤800 行;禁止再向 800 行以上文件追加代码(先在迭代 5 拆分)。
- 提交信息:conventional commits(`fix:`/`refactor:`/`ci:`/`docs:`/`test:`/`chore:`),不加 attribution footer。
- 测试标记:网络类测试必须自行声明 `@pytest.mark.network` / `@pytest.mark.integration` / `@pytest.mark.wss`,禁止任何"失败自动改跳过"机制。
- 验收口径:每个任务列出可执行的验收命令与预期输出;每个迭代结束执行迭代验收清单 + 派发 code-reviewer agent 复审,CRITICAL/HIGH 问题清零后才能进入下一迭代。

## 迭代总览

| 迭代 | 主题 | 对应 Findings | 预估工期 | 依赖 | 里程碑(验收信号) |
|------|------|--------------|---------|------|-----------------|
| 0 | 数据安全止血 | B-01, B-03, B-09 | 1–2 天 | 无 | `git submodule status` 全部 clean;新克隆演练不丢改动 |
| 1 | 订单链路硬化 | A-01~A-08, A-11, A-13, A-14, A-15, A-17 | 3–5 天 | 迭代 0 | 非法参数拒绝测试绿;幂等语义测试绿 |
| 2 | 测试诚信恢复 | C-01~C-09 | 1–2 天 | 迭代 0 | 故意注入失败 → CI 红;收集数恢复到 384+ 全量 |
| 3 | 发布链路修复 | D-01~D-15 | 3–4 天 | 迭代 0 | publish dry-run 通过;docs 构建 strict 绿;tag↔版本校验生效 |
| 4 | 适配器正确性 | B-02, B-04~B-08, B-10~B-24 | 5–8 天 | 迭代 0+2 | OKX 黄金向量通过;61 仓插件发现 smoke 绿 |
| 5 | 架构收敛与安全 | F-01~F-12, E-01~E-07, A-09, A-10, A-12, A-16, A-18~A-20 | 5–8 天 | 迭代 1+3 | 死代码 grep 清零;巨型文件拆分完成;安全测试绿 |

---

## 迭代 0:数据安全止血(1–2 天)

**目标:** 把 59 个子模块工作区未提交的真实改动全部 commit+push,清理 git 跟踪的构建产物,提交母仓库的子模块删除与 pin 更新。消除"一次 submodule update 全丢"的风险。

**验收标准:**
- [ ] `git submodule foreach --recursive 'git status --porcelain'` 输出为空(所有子模块 clean)
- [ ] 每个子模块 remote(cloudQuant)有本地 commit 之后的记录(`git submodule foreach 'git log origin/HEAD..HEAD --oneline'` 为空)
- [ ] 母仓库 commit 后,在**全新临时目录**克隆母仓库 + `git submodule update --init --recursive`,跑 `python -c "import bt_api_py"` 成功
- [ ] 母仓库 CI 绿

### Task 0.1: 盘点 59 个 dirty 子模块的改动内容

**Files:**
- Create: `scripts/audit_submodule_changes.py`

**Interfaces:**
- Produces: `audit_submodule_changes.py` 输出每仓改动分类报告(源文件改动 / 构建产物改动 / 新增未跟踪文件),供 Task 0.2/0.3 使用

- [ ] **Step 1: 写盘点脚本**

```python
#!/usr/bin/env python3
"""盘点所有子模块的未提交改动,按类别输出报告。

用法: python scripts/audit_submodule_changes.py [--json /tmp/report.json]
"""
from __future__ import annotations

import json
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
BT_API = ROOT / "bt_api"

BUILD_ARTIFACT_GLOBS = ("__pycache__", "*.pyc", "*.egg-info", "build/", "dist/")


def is_build_artifact(path: str) -> bool:
    return any(part in path for part in ("__pycache__", ".egg-info", "/build/", "/dist/")) or path.endswith(".pyc")


def audit(repo: Path) -> dict:
    out = subprocess.run(
        ["git", "status", "--porcelain", "-uall"], cwd=repo, capture_output=True, text=True, check=True
    ).stdout
    source, artifacts = [], []
    for line in out.splitlines():
        path = line[3:]
        (artifacts if is_build_artifact(path) else source).append(line)
    return {"repo": repo.name, "source_changes": source, "build_artifacts": artifacts}


def main() -> None:
    report = [audit(p) for p in sorted(BT_API.iterdir()) if (p / ".git").exists()]
    for r in report:
        print(f"{r['repo']}: source={len(r['source_changes'])} artifacts={len(r['build_artifacts'])}")
    if "--json" in __import__("sys").argv:
        out = __import__("sys").argv[__import__("sys").argv.index("--json") + 1]
        Path(out).write_text(json.dumps(report, indent=2, ensure_ascii=False))


if __name__ == "__main__":
    main()
```

- [ ] **Step 2: 运行并人工 review 报告**

Run: `python scripts/audit_submodule_changes.py --json /tmp/submodule_audit.json`
Expected: 59 个仓库出现在列表;抽查 5 个仓(source 改动数最大的)确认无密钥文件混入:`git -C bt_api/bt_api_<name> diff | grep -iE 'secret|api_key|private' | head`

- [ ] **Step 3: 提交脚本**

```bash
git add scripts/audit_submodule_changes.py
git commit -m "chore: add submodule change audit script"
```

**验收:** 报告覆盖 59 仓;抽查无敏感信息;脚本已提交。

### Task 0.2: 从各子模块 git 中移除构建产物跟踪并补 .gitignore

**Files:**
- Modify: 各子模块仓库(bybit 29 .pyc、hyperliquid 34、mexc 30、bitget 40、okx 66 build/lib、bybit 6 egg-info 等,B-09)

**Interfaces:**
- Consumes: Task 0.1 的 `/tmp/submodule_audit.json`(artifacts 列表)

- [ ] **Step 1: 每个含 build_artifacts 的仓执行移除(循环脚本)**

```bash
cd /Users/yunjinqi/Documents/new_projects/bt_api_py
for repo in $(python -c "
import json
report = json.load(open('/tmp/submodule_audit.json'))
print(' '.join(r['repo'].replace('bt_api_', '') for r in report if r['build_artifacts']))"); do
  cd bt_api/bt_api_$repo
  git rm -r --cached --quiet --ignore-unmatch $(git ls-files | grep -E '__pycache__|\.pyc$|\.egg-info|^build/|^dist/' ) 2>/dev/null
  printf '__pycache__/\n*.pyc\n*.egg-info/\nbuild/\ndist/\n' >> .gitignore
  sort -u .gitignore -o .gitignore
  git add .gitignore
  git commit -m "chore: stop tracking build artifacts, ignore pycache/egg-info/build"
  cd ../..
done
```

- [ ] **Step 2: 抽查 3 个仓验证**

Run: `git -C bt_api/bt_api_okx ls-files | grep -c build/` → 期望 `0`;`git -C bt_api/bt_api_bybit ls-files | grep -c '\.pyc'` → 期望 `0`

- [ ] **Step 3: 暂不 push(与 Task 0.3 的源码提交一起推送,避免中间态)**

**验收:** 各仓 `git ls-files | grep -E '__pycache__|\.pyc|egg-info|^build/'` 为空;`.gitignore` 已含规则。

### Task 0.3: 按依赖序提交推送 59 个子模块

**Files:**
- Modify: 59 个子模块仓库(commit + push)

**Interfaces:**
- Consumes: Task 0.2 的干净工作区

- [ ] **Step 1: 先提交 bt_api_base(被全部 60 仓依赖,必须第一个推送)**

```bash
cd bt_api/bt_api_base
git add -A
git status --porcelain   # 人工确认无 keys/ 敏感文件
git commit -m "refactor: consolidate base feed infrastructure (uncommitted local work)"
git push origin HEAD:master   # 分支名以 git branch --show-current 为准
cd ../..
```

- [ ] **Step 2: 按依赖序批量提交推送其余 58 仓(核心大仓先,长尾后)**

```bash
# 顺序: binance okx bybit gateio hyperliquid htx kucoin bitget mexc ...
for repo in binance okx bybit gateio hyperliquid htx kucoin bitget mexc; do
  cd bt_api/bt_api_$repo || continue
  git add -A
  if git diff --cached --quiet; then echo "$repo: nothing to commit"; cd ../..; continue; fi
  git commit -m "refactor: align with latest bt_api_base contract"
  git push origin HEAD:$(git branch --show-current)
  cd ../..
done
# 长尾仓批量
for repo in $(ls bt_api | grep '^bt_api_' | sed 's/bt_api_//' | grep -vE '^(base|binance|okx|bybit|gateio|hyperliquid|htx|kucoin|bitget|mexc)$'); do
  cd bt_api/bt_api_$repo || continue
  git add -A
  if git diff --cached --quiet; then cd ../..; continue; fi
  git commit -m "refactor: align with latest bt_api_base contract"
  git push origin HEAD:$(git branch --show-current)
  cd ../..
done
```

- [ ] **Step 3: 验证推送完成**

Run: `git submodule foreach --recursive 'git status --porcelain; git log origin/HEAD..HEAD --oneline'`
Expected: 每个仓输出为空(clean 且本地无领先远程的 commit)

- [ ] **Step 4: 失败的仓(如有)手工处理并记录原因,不阻塞其余仓**

**验收:** 59 仓 clean 且已推送;失败清单(如有)写进迭代报告。

### Task 0.4: 母仓库提交 pin 更新 + 子模块删除 + 残留引用清理

**Files:**
- Modify: `.gitmodules`(已 staged)、`bt_api/`(pin 指针)、`_generate_docs.py:451-469`(硬编码清单)、`docs/CODE_QUALITY.md:55`、`.github/workflows/tests.yml`(如需)

**Interfaces:**
- Consumes: Task 0.3 推送后的新 commit

- [ ] **Step 1: 在母仓库暂存全部 pin 更新**

Run: `git add bt_api/ && git status --porcelain bt_api/ | head -70`
Expected: 看到每个子模块一行 `M bt_api/bt_api_xxx`(gitlink 指针更新),外加 5 行 `D bt_api/bt_api_bingx` 等删除

- [ ] **Step 2: 清理 `_generate_docs.py` 硬编码 64 仓清单(B-03 残留)**

把硬编码包清单替换为动态读取 `.gitmodules`:

```python
def _submodule_names() -> list[str]:
    """从 .gitmodules 动态读取子模块清单,替代硬编码 64 仓列表。"""
    import re
    from pathlib import Path

    gitmodules = Path(__file__).resolve().parent.parent / ".gitmodules"
    text = gitmodules.read_text(encoding="utf-8")
    return re.findall(r'path = bt_api/(bt_api_\w+)', text)
```

- [ ] **Step 3: 修正 `docs/CODE_QUALITY.md:55` 对已删仓库的引用**(删掉 bequant/bigone/bingx/bitbank/bitflyer 相关行)

- [ ] **Step 4: 提交**

```bash
git add -A
git commit -m "chore: update 59 submodule pins, drop 5 retired adapters, sync doc generator"
```

- [ ] **Step 5: 全新克隆演练(验证"不再丢改动"的最终证明)**

```bash
tmp=$(mktemp -d)
git clone --recursive . "$tmp/bt_api_py_clone" && cd "$tmp/bt_api_py_clone"
python -m venv .venv && .venv/bin/pip install -e . -q
.venv/bin/python -c "import bt_api_py; print(bt_api_py.__version__)"
```

Expected: 克隆成功、安装成功、版本打印成功。

**验收:** 母仓库工作区只剩本次迭代相关文件;克隆演练通过。

---

## 迭代 1:订单链路硬化(3–5 天)

**目标:** 消除下单链路的静默降级、幂等错配、幽灵订单、资源泄漏等 P0 资金风险(Spec A-01~A-08、A-11、A-13~A-15、A-17)。

**验收标准:**
- [ ] `pytest tests/test_forwarding_bus_router_client.py tests/test_forwarding_zmq_transport.py tests/test_bt_api_helpers.py -v` 全绿
- [ ] 新增参数化测试覆盖:非法 side/order_type 全部拒绝且 adapter 零调用
- [ ] 幂等测试证明:retryable 错误不缓存、终态错误缓存
- [ ] 幽灵订单测试证明:超时命令进入 `pending_commands()` 可查询;loop 内 `start_sync` 不崩溃(A-04/A-05)
- [ ] `pytest --cov=bt_api_py/forwarding --cov-report=term-missing tests/test_forwarding_bus_router_client.py tests/test_forwarding_zmq_transport.py` 覆盖率 ≥80%
- [ ] code-reviewer agent 复审通过(无 CRITICAL/HIGH)

### Task 1.1: router 严格白名单校验 side/order_type(A-01)

**Files:**
- Modify: `bt_api_py/forwarding/router.py:125-140`(OrderRequest 构造段)
- Test: `tests/test_forwarding_bus_router_client.py`(新增 2 个测试)

**Interfaces:**
- Produces: 模块级函数 `_normalize_side(value) -> str`、`_normalize_order_type(value) -> str`,非法值抛 `ValueError`

- [ ] **Step 1: 读现有测试的 fixture 模式**

Read `tests/test_forwarding_bus_router_client.py:302-336`(`test_order_router_enforces_idempotency_and_publishes_private_events`)与 `:378-404`(`test_order_router_rejects_disallowed_symbol_before_adapter_call`),复用其 MockBrokerAdapter/OrderCommand 构造方式。

- [ ] **Step 2: 写失败测试**

```python
async def test_order_router_rejects_invalid_side_instead_of_market_buy() -> None:
    adapter = MockBrokerAdapter()
    placed: list[OrderRequest] = []
    original = adapter.place_order

    async def spy(request: OrderRequest):
        placed.append(request)
        return await original(request)

    adapter.place_order = spy  # type: ignore[method-assign]
    router = OrderRouter(adapter)
    cmd = OrderCommand(
        strategy_id="s", account_id="a", exchange="E", market_type="SPOT",
        symbol="BTCUSDT", side="sdie", size=1.0, order_type="limit",
        price=10.0, client_order_id="c1", idempotency_key="k-invalid-side",
    )
    ack = await router.handle_command(cmd)
    assert ack.success is False
    assert "invalid side" in str(ack.reason).lower()
    assert placed == []  # 绝不发往 adapter


async def test_order_router_rejects_invalid_order_type_instead_of_market() -> None:
    # 同上结构: order_type="LIMT"(拼写错误), side="buy"
    ...
    assert ack.success is False
    assert "invalid order_type" in str(ack.reason).lower()
    assert placed == []
```

- [ ] **Step 3: 跑测试确认失败**

Run: `pytest tests/test_forwarding_bus_router_client.py::test_order_router_rejects_invalid_side_instead_of_market_buy -v`
Expected: FAIL(当前行为是静默变 buy,adapter 被调用)

- [ ] **Step 4: 实现白名单校验**

在 `router.py` 顶部(import 之后)加:

```python
_VALID_SIDES = frozenset({"buy", "sell"})
_VALID_ORDER_TYPES = frozenset({"limit", "market"})


def _normalize_side(value: Any) -> str:
    side = str(value).strip().lower()
    if side not in _VALID_SIDES:
        raise ValueError(f"invalid side {value!r}: must be 'buy' or 'sell'")
    return side


def _normalize_order_type(value: Any) -> str:
    order_type = str(value).strip().lower()
    if order_type not in _VALID_ORDER_TYPES:
        raise ValueError(f"invalid order_type {value!r}: must be 'limit' or 'market'")
    return order_type
```

把 `:133-134` 的两行三元表达式替换为:

```python
        try:
            side = _normalize_side(command.side)
            order_type = _normalize_order_type(command.order_type)
        except ValueError as exc:
            ack = self._reject(command, str(exc), payload={"error_code": "INVALID_PARAM"})
            self._publish_error(command, str(exc), error_code="INVALID_PARAM")
            return ack  # 注意:不 _remember_ack,输入错误是调用方问题,不占幂等表
```

OrderRequest 构造处改用 `side=side, order_type=order_type`。

- [ ] **Step 5: 跑测试确认通过**

Run: `pytest tests/test_forwarding_bus_router_client.py -v`
Expected: 全部 PASS(含既有 10 个 router 测试)

- [ ] **Step 6: 提交**

```bash
git add bt_api_py/forwarding/router.py tests/test_forwarding_bus_router_client.py
git commit -m "fix: reject invalid side/order_type instead of silently defaulting to market buy"
```

**验收:** 上述 2 个新测试 + 既有测试全绿;`grep -n 'else "buy"' bt_api_py/forwarding/router.py` 无结果。

### Task 1.2: client 缺省值改为必填校验(A-01 后半)

**Files:**
- Modify: `bt_api_py/forwarding/client.py:290-300`(OrderCommand 构造段)

**Interfaces:**
- Produces: `ForwardingClient` 构造 OrderCommand 时缺 `side`/`order_type` 直接抛 `ValueError`,不再默认 `"buy"/"market"`

- [ ] **Step 1: 写失败测试**

在 `tests/test_forwarding_bus_router_client.py` 加(复用 Task 1.1 的 client fixture):

```python
def test_forwarding_client_requires_explicit_side_and_order_type() -> None:
    # 用既有测试 :592 附近构造 ForwardingClient 的方式
    client = ...  # 既有 fixture
    with pytest.raises(ValueError, match="side"):
        client.send_order(symbol="BTCUSDT", size=1.0)  # 缺 side
    with pytest.raises(ValueError, match="order_type"):
        client.send_order(symbol="BTCUSDT", side="buy", size=1.0)  # 缺 order_type
```

- [ ] **Step 2: 跑测试确认失败**

Run: `pytest tests/test_forwarding_bus_router_client.py -k requires_explicit_side -v` → FAIL

- [ ] **Step 3: 实现**

把 `:296-297` 的 `str(payload.get("side") or "buy")` 与 `str(payload.get("order_type") or "market")` 替换为:

```python
            side=payload.get("side") or _require(payload, "side"),
            order_type=payload.get("order_type") or _require(payload, "order_type"),
```

并在同文件加:

```python
def _require(payload: dict, key: str) -> str:
    raise ValueError(f"{key} is required; refusing to default to a marketable value")
```

- [ ] **Step 4: 跑测试确认通过** → `pytest tests/test_forwarding_bus_router_client.py -v` 全绿

- [ ] **Step 5: 提交** → `fix: require explicit side/order_type in forwarding client` 格式提交

**验收:** 新增测试绿;既有调用侧(测试/examples)若依赖默认值,改为显式传参并说明原因。

### Task 1.3: 幂等缓存只缓存确定性终态(A-02)

**Files:**
- Modify: `bt_api_py/forwarding/router.py`(`handle_command` 的 except 分支,约 :144-153、:213-223)
- Test: `tests/test_forwarding_bus_router_client.py` 新增 2 测试

**Interfaces:**
- Consumes: `bt_api_py/brokers/errors.py` 的 `BrokerError.retryable` 字段(先读 :26-33 确认字段名与构造签名)

- [ ] **Step 1: 读 `brokers/errors.py:20-40` 确认 `retryable` 字段与构造签名**

- [ ] **Step 2: 写失败测试**

```python
async def test_router_does_not_cache_retryable_errors() -> None:
    calls = 0

    class FlakyAdapter(MockBrokerAdapter):
        async def place_order(self, request: OrderRequest):
            nonlocal calls
            calls += 1
            raise BrokerError(code="NETWORK_ERROR", message="timeout", retryable=True)

    router = OrderRouter(FlakyAdapter())
    cmd = ...  # 固定 idempotency_key="k-retry"
    ack1 = await router.handle_command(cmd)
    ack2 = await router.handle_command(cmd)   # 同 key 重试
    assert ack1.success is False and ack2.success is False
    assert calls == 2  # 重试必须再次到达 adapter,而不是返回缓存的旧拒绝


async def test_router_caches_terminal_rejects() -> None:
    calls = 0

    class TerminalAdapter(MockBrokerAdapter):
        async def place_order(self, request: OrderRequest):
            nonlocal calls
            calls += 1
            raise BrokerError(code="INSUFFICIENT_FUNDS", message="no money", retryable=False)

    router = OrderRouter(TerminalAdapter())
    cmd = ...  # idempotency_key="k-terminal"
    await router.handle_command(cmd)
    await router.handle_command(cmd)
    assert calls == 1  # 终态拒绝命中缓存
```

- [ ] **Step 3: 跑测试确认失败**(当前两个 except 分支都无条件 `_remember_ack`)→ FAIL

- [ ] **Step 4: 实现**

`except BrokerError` 分支改为:

```python
        except BrokerError as exc:
            ack = self._reject(command, str(exc), payload={"error_code": str(exc.code)})
            if not exc.retryable:          # 只有确定性终态才进幂等缓存
                self._remember_ack(ack)
            self._publish_error(command, str(exc), error_code=str(exc.code))
            return ack
```

`except Exception` 分支(未知异常,订单真实状态未知)去掉 `_remember_ack` 调用,保留 `_publish_error` 后 return。

- [ ] **Step 5: 跑测试确认通过** → 新 2 测试 + 既有幂等测试(`test_order_router_enforces_idempotency...`)全绿

- [ ] **Step 6: 提交** → `fix: only cache deterministic terminal acks in idempotency store`

**验收:** `pytest tests/test_forwarding_bus_router_client.py -k "retryable or terminal or idempot" -v` 全绿。

### Task 1.4: ZMQ 超时后回读残留应答 + correlation id(A-03)

**Files:**
- Modify: `bt_api_py/forwarding/transport.py:220-267`(ZmqCommandClient.send)
- Test: `tests/test_forwarding_zmq_transport.py` 新增测试

**Interfaces:**
- Produces: `ZmqCommandClient.send` 超时后 drain 残留应答;若引入 correlation id,`CommandAck` 增加 `correlation_id` 字段(schema.py 同步修改)

- [ ] **Step 1: 读 `transport.py:200-270` 与 `tests/test_forwarding_zmq_transport.py` 现有 fake socket 写法**

- [ ] **Step 2: 写失败测试(阶段一:drain)**

用现有 fake socket 构造"延迟应答":第一次 send 不返回,第二次 send 前把上一条的 ack 塞进队列。

```python
def test_zmq_client_does_not_return_stale_ack_after_timeout() -> None:
    # 用现有 fake socket: 第一个请求超时,残留 ack 留在队列
    # 第二个请求应得到自己的 ack(内容匹配第二条命令),而非第一条的
    ...
    reply2 = client.send(cmd2, timeout=...)
    assert reply2.correlation_id == cmd2.id  # 或对应现有标识字段
```

- [ ] **Step 3: 跑测试确认失败**(当前实现会读到上一条 ack)→ FAIL

- [ ] **Step 4: 实现(两阶段,先 drain 后 correlation id)**

阶段一(本任务交付):`send` 捕获 `TimeoutError` 后,用 `socket.poll(0)` 非阻塞循环把队列清空再抛出:

```python
        except TimeoutError:
            # 清空迟到的应答,防止下一条命令读到错配的 ack
            while True:
                stale = self._socket.poll(0)  # 0 = 不等待
                if stale == 0:
                    break
                self._socket.recv()
            raise
```

阶段二(correlation id,若阶段一测试仍暴露错配):给 `CommandAck`/`send` 增加 `correlation_id`,收到 ack 后校验匹配、不匹配则丢弃并继续接收(带短超时)。两阶段均需测试证明。

- [ ] **Step 5: 跑测试确认通过** → `pytest tests/test_forwarding_zmq_transport.py -v` 全绿

- [ ] **Step 6: 提交** → `fix: drain stale zmq replies after send timeout to prevent ack mismatch`

**验收:** 新测试 + 既有 zmq 测试全绿。

### Task 1.5: cancel_order 幂等 key 去除 uuid(A-08)

**Files:**
- Modify: `bt_api_py/forwarding/client.py:260-270`(cancel_order 幂等 key 生成)

- [ ] **Step 1: 写失败测试**

```python
def test_cancel_order_idempotency_key_is_deterministic() -> None:
    client = ...  # 既有 fixture
    k1 = client._cancel_idempotency_key(strategy_id="s", account_id="a", order_ref="ref-1")
    k2 = client._cancel_idempotency_key(strategy_id="s", account_id="a", order_ref="ref-1")
    assert k1 == k2  # 同参数重试必须同 key
```

- [ ] **Step 2: 跑测试确认失败**(当前含 `uuid.uuid4()`)→ FAIL

- [ ] **Step 3: 实现**

key 只由 `(strategy_id, account_id, order_ref)` 构成:

```python
        idempotency_key = f"{self.strategy_id}:{self.account_id}:cancel:{order_ref}"
```

若 `order_ref` 可能为空,先校验非空再构造(空 ref 的撤单请求本身就是非法输入)。

- [ ] **Step 4: 跑测试确认通过** → 提交 `fix: make cancel_order idempotency key deterministic`

**验收:** 测试绿;`grep uuid bt_api_py/forwarding/client.py | grep -i cancel` 无结果。

### Task 1.6: close() 完整关闭 WebSocket 与订阅(A-11)

**Files:**
- Modify: `bt_api_py/bt_api.py:534-545`(`close()`)

- [ ] **Step 1: 读 `bt_api.py:520-560` 与 `bt_api_base` 中 feed 的关闭接口(找 ws 关闭方法名,如 `feed.close_wss` / `feed.stop_stream`)**

- [ ] **Step 2: 写失败测试**(用 FakeBtApi 现有模式,见 `tests/test_forwarding_bus_router_client.py:25` 的 FakeBtApi,或 test_bt_api_helpers.py 中 mock feed)

```python
def test_close_stops_websocket_streams() -> None:
    feed = FakeFeed()          # 记录 close_ws 被调用的 mock
    api = make_api_with_feed(feed)
    api.close()
    assert feed.close_ws_called is True
```

- [ ] **Step 3: 跑测试确认失败** → FAIL

- [ ] **Step 4: 实现**

`close()` 中在关闭 `_http_client` 前后,遍历已注册 feeds 调用其 ws 关闭方法(按 Step 1 查到的真实方法名),并用 `try/except Exception` 逐 feed 收集关闭错误(全部尝试、最后聚合抛出一个带明细的异常,不静默吞):

```python
        errors: list[str] = []
        for feed in self._feeds.values():
            try:
                feed.close_stream()   # 以实际方法名为准
            except Exception as exc:
                errors.append(f"{feed}: {exc}")
        if errors:
            raise RuntimeError("failed to close feeds: " + "; ".join(errors))
```

- [ ] **Step 5: 跑测试确认通过** → 提交 `fix: close websocket streams in BtApi.close()`

**验收:** 测试绿;close() 幂等(连调两次不抛)。

### Task 1.7: kline 部分下载聚合异常(A-06)

**Files:**
- Modify: `bt_api_py/bt_api.py:380-400`(`_download_kline_by_range` 重试耗尽分支)

- [ ] **Step 1: 写失败测试**

```python
def test_kline_download_retry_exhaustion_raises_partial_error() -> None:
    api = ...  # mock feed 每次 raise
    with pytest.raises(PartialDownloadError) as exc_info:
        api._download_kline_by_range(...)
    assert "partial" in str(exc_info.value).lower()
    assert exc_info.value.downloaded_intervals == []   # 无任何成功区间
```

- [ ] **Step 2: 跑测试确认失败**(当前仅 log + return)→ FAIL

- [ ] **Step 3: 实现**

在 `bt_api_py/exceptions.py`(或就近)新增:

```python
class PartialDownloadError(Exception):
    """历史数据部分下载失败:携带已成功下载的区间与失败原因。"""

    def __init__(self, message: str, *, downloaded_intervals: list[tuple[int, int]]) -> None:
        super().__init__(message)
        self.downloaded_intervals = downloaded_intervals
```

重试耗尽分支改为 `raise PartialDownloadError(f"kline download incomplete after N retries: {last_error}", downloaded_intervals=self._collected_intervals)`(收集逻辑按现有循环变量实现)。

- [ ] **Step 4: 跑测试确认通过** → 提交 `fix: raise PartialDownloadError instead of silently returning partial klines`

**验收:** 测试绿;`grep -n "下载失败\|download.*fail" bt_api_py/bt_api.py | head` 确认该路径只有 raise 无裸 return。

### Task 1.8: _parse_time 时区语义统一(A-07)

**Files:**
- Modify: `bt_api_py/bt_api.py:57-71`(`_parse_time`)

- [ ] **Step 1: 写失败测试(参数化)**

```python
import pytest
from datetime import datetime, timezone

@pytest.mark.parametrize("raw,expected_utc", [
    ("2024-01-01T08:00:00", datetime(2024, 1, 1, 8, 0, tzinfo=timezone.utc)),
    (datetime(2024, 1, 1, 8, 0), datetime(2024, 1, 1, 8, 0, tzinfo=timezone.utc)),  # naive 一律按 UTC
])
def test_parse_time_naive_always_utc(raw, expected_utc) -> None:
    result = _parse_time(raw)
    assert result.astimezone(timezone.utc) == expected_utc
```

- [ ] **Step 2: 跑测试确认失败**(当前 naive datetime 走 UTC 但 naive 字符串走本地时区)→ 至少一条 FAIL

- [ ] **Step 3: 实现**

统一规则:**所有 naive 输入按 UTC 解释**;带 tz 输入保持原 tz 并 `.astimezone(timezone.utc)` 返回 aware UTC。修改 `:63` 的本地时区分支,删除对 `datetime.now().astimezone().tzinfo` 的使用。

- [ ] **Step 4: 跑测试确认通过** → 提交 `fix: treat all naive timestamps as UTC in _parse_time`

**验收:** 参数化测试全绿;`grep -rn "astimezone().tzinfo" bt_api_py/` 无结果。

### Task 1.9: 小项批量修复(A-14、A-15、A-17、A-19、A-20)

**Files:**
- Modify: `bt_api_py/forwarding/state.py:22-24`、`router.py:45`、`bt_api.py:473-498`、`forwarding/client.py:235-240, 392-406`

**Interfaces:**
- Consumes: 各文件现有结构

- [ ] **Step 1: A-14 — `state.py` 的 `"memory:"` 改为 `":memory:"`**

写测试:`SQLiteStateStore("memory:")` 不再在磁盘创建 `memory:` 文件(用 tmp_path 断言目录下无新文件,且连接可用)。实现:字符串统一替换。

- [ ] **Step 2: A-15 — `router.py` 幂等表加 LRU 上限**

实现:`_acks_by_idempotency_key` 改为 `collections.OrderedDict`,容量常量 `_MAX_CACHED_ACKS = 10_000`,写入后 `if len(...) > _MAX_CACHED_ACKS: popitem(last=False)`。测试:塞 10_001 个假 ack,断言长度为 10_000 且最旧的被逐出。

- [ ] **Step 3: A-17 — `update_balance` 未注册交易所抛 `ExchangeNotFoundError` 而非裸 KeyError**

先读 `get_cash`(:498)的异常构造方式,`update_balance` 的 `self._feeds[...]` 访问改为 `_get_feed(...)` 同一路径。测试:未注册交易所名 → 抛 ExchangeNotFoundError。

- [ ] **Step 4: A-19 — fetch_open_orders 过滤 `{"submitted", "new"}` 集合**

测试:mock 订单列表含 `status="new"` 的记录,断言出现在结果里。

- [ ] **Step 5: A-20 — tick/bar timestamp 统一为毫秒(int)**

先读 `client.py:392-406` 确认两处来源,统一换算为 ms int。测试:构造含秒级 event_time 的输入,断言输出时间戳为 `event_time * 1000`。

- [ ] **Step 6: 全部测试通过后提交**

```bash
git add bt_api_py/forwarding/ bt_api_py/bt_api.py tests/
git commit -m "fix: small correctness fixes in forwarding state, router cache, balances and timestamps"
```

**验收:** 迭代 1 验收清单全绿。

### Task 1.10: 幽灵订单与 async 边界修复(A-04、A-05、A-13)

**Files:**
- Modify: `bt_api_py/forwarding/memory.py:215-242`(`_run_awaitable_sync` 超时线程)、`bt_api_py/forwarding/service.py:84-140`(`start_sync`)、`bt_api_py/bt_api.py:313-321`(subscribe)
- Test: `tests/test_forwarding_bus_router_client.py`、`tests/test_bt_api_helpers.py`

**Interfaces:**
- Produces: `ForwardingClient.pending_commands() -> list[str]`(超时后未决命令 key 可查询,供对账);`SubscribeError` 异常(新增于 `bt_api_py/exceptions.py` 或就近)

- [ ] **Step 1: 读 `memory.py:200-250` 与 `service.py:80-145`,确认超时线程与事件循环边界的现状(哪些连接绑定在哪个 loop)**

- [ ] **Step 2: 写失败测试(超时后不得继续下单)**

```python
async def test_sync_command_timeout_does_not_leave_ghost_order() -> None:
    # 复用 :261 附近 "sync_command_times_out" 的 fixture 模式
    # 用记录型 adapter:place_order 会阻塞直到被放行
    # 1) 发命令 → 超时(TimeoutError)
    # 2) 断言 client.pending_commands() 包含该命令 key(结果未知必须可查询)
    # 3) 放行后断言:要么 ack 可通过 pending 状态取回,要么明确标记为 unknown —— 不允许"静默已下单"
```

```python
async def test_start_sync_inside_running_loop_does_not_crash() -> None:
    # pytest-asyncio 环境下调用 start_sync(当前 asyncio.run 会 RuntimeError)
    runtime = ZmqForwardingRuntime(...)
    runtime.start_sync()
    assert runtime.health()["status"] == "running"
```

- [ ] **Step 3: 跑测试确认失败**(当前:超时后线程继续下单且无查询入口;loop 内 start_sync 崩溃)→ FAIL

- [ ] **Step 4: 实现**

统一 async 边界:`start_sync` 自持**常驻** loop + 守护线程(单例,不反复创建销毁);所有命令执行投递到该常驻 loop;超时语义改为 `TimeoutError("result unknown")` + 命令 key 进入 `pending_commands()` 可查询表(ack 到达后移除);线程内禁止再嵌套 `asyncio.run`。

- [ ] **Step 5: A-13 — subscribe 目标交易所不存在时抛 `SubscribeError`**

`bt_api.py:319-321` 的 `log + return` 改为 `raise SubscribeError(f"exchange {exchange} not registered")`;`:317` 的 `subscribe_bar_num` 累加移到注册成功之后。测试:

```python
def test_subscribe_unknown_exchange_raises() -> None:
    api = make_api_without_feeds()   # 按 test_bt_api_helpers.py 既有模式
    with pytest.raises(SubscribeError):
        api.subscribe("NOT_A_REAL_EXCHANGE", "BTCUSDT", bar_num=10)
```

- [ ] **Step 6: 全部测试通过后提交**

```bash
git add bt_api_py/forwarding/ bt_api_py/bt_api.py bt_api_py/exceptions.py tests/
git commit -m "fix: track timed-out commands as pending, stabilize sync/async loop boundary"
```

**验收:** 新 3 个测试绿;`grep -n "asyncio.run" bt_api_py/forwarding/` 仅剩 start_sync 自持 loop 处(单一入口)。

---

## 迭代 2:测试诚信恢复(1–2 天,可与迭代 1 并行)

**目标:** 删除"失败自动改跳过"机制,修复测试收集失败,清理形式检查测试与重复测试(Spec C-01~C-09)。

**验收标准:**
- [ ] 注入故意失败的测试 → `pytest` 返回非 0(红灯真实)
- [ ] `pytest tests/ --collect-only -q` 收集数 = 384 + 迭代 1 新增(且含此前静默消失的 3 个安全测试文件)
- [ ] 子模块测试从仓库根跑时,网络失败不再被改写为 skipped(由测试自己声明标记)
- [ ] code-reviewer agent 复审通过

### Task 2.1: 删除失败改跳过钩子与路径自动标记(C-01、C-02)

**Files:**
- Modify: `conftest.py:180-212`(路径自动标记)、`:280-330`(`_network_skip_reason` + `pytest_runtest_makereport`)

- [ ] **Step 1: 删除 `pytest_runtest_makereport` 钩子与 `_network_skip_reason`、`_is_real_ctp_network_test` 及其调用**

保留 `pytest_sessionfinish` 中 `_CTP_HARD_EXIT_STATUS` 记录逻辑(与 exitstatus 相关)。

- [ ] **Step 2: 删除按 fspath 自动打 network 标记的 hook**

替换为:在 conftest 顶部仅注册标记 `pytest.ini` 段(pyproject.toml)已声明的 `network`/`integration`/`wss`;需要网络的测试自行声明 `@pytest.mark.network`。对 `examples/network_tests` 下确需豁免的用例,在其目录内放一个局部 conftest 显式 `skipif`(无网络环境变量时跳过),而不是全局改写。

- [ ] **Step 3: 全量收集确认无回归**

Run: `pytest tests/ --collect-only -q 2>&1 | tail -5` → 期望无 error;`pytest tests/ -m "not network" -q` → 期望全绿(若有个别原本依赖钩子掩盖的失败暴露出来,如实记录进迭代报告,归入对应模块修复,不重新掩盖)

- [ ] **Step 4: 提交** → `test: remove failure-to-skip rewrite hook; tests must declare network markers`

**验收:** `grep -n "report.outcome = \"skipped\"" conftest.py` 无结果。

### Task 2.2: 证明红灯真实(C-01 验收动作)

- [ ] **Step 1: 临时添加 `tests/test_red_proof.py`:**

```python
def test_intentionally_fails() -> None:
    assert False, "proving CI turns red on real failures"
```

- [ ] **Step 2: 跑 `pytest tests/test_red_proof.py`** → 期望 FAIL(exit 1)

- [ ] **Step 3: 删除该文件并提交** → `test: verify red-is-red after hook removal`(或与 2.1 同 commit)

**验收:** 记录上述输出进迭代报告作为证据。

### Task 2.3: 修复 security_compliance 可选依赖导入(C-03)

**Files:**
- Modify: `bt_api_py/security_compliance/core/encryption_manager.py:30-60`(依赖守卫)

- [ ] **Step 1: 读该文件 30-60 行,确认当前守卫结构**

- [ ] **Step 2: 写失败测试**

```python
def test_encryption_manager_imports_without_boto3(monkeypatch) -> None:
    # 模拟 boto3 因底层冲突抛 AttributeError(而非 ImportError)的场景
    def broken_import(name, *args, **kwargs):
        if name.startswith("boto3") or name.startswith("hvac"):
            raise AttributeError("cannot import name X from Y")
        return real_import(name, *args, **kwargs)

    real_import = __import__
    monkeypatch.setattr("builtins.__import__", broken_import)
    import importlib
    mod = importlib.import_module("bt_api_py.security_compliance.core.encryption_manager")
    assert mod._BACKENDS_AVAILABLE is False  # 或等价降级标志
```

- [ ] **Step 3: 跑测试确认失败**(当前 AttributeError 逃逸)→ FAIL

- [ ] **Step 4: 实现**

守卫改为捕获 `Exception` 并记录降级日志:

```python
try:
    import boto3  # noqa: F401
    import hvac  # noqa: F401
    _BACKENDS_AVAILABLE = True
except Exception as exc:  # 包括 AttributeError(底层依赖版本冲突)
    logger.warning("remote encryption backends unavailable, degraded to local mode: %s", exc)
    _BACKENDS_AVAILABLE = False
```

- [ ] **Step 5: 跑 `pytest tests/ --collect-only -q`** → 3 个此前消失的测试文件重新出现(收集数 +120 左右)

- [ ] **Step 6: 提交** → `fix: guard optional encryption deps against AttributeError escapes`

**验收:** 收集数恢复;`pytest tests/test_audit_logger.py tests/test_security_compliance.py tests/test_threat_detection.py -m "not network"` 全绿。

### Task 2.4: 删除 quality_batch 形式检查测试,迁移真行为测试(C-04)

**Files:**
- Delete: `tests/test_quality_batch_v3.py`、`tests/test_quality_batch_v5.py`、`tests/test_quality_batch_v6.py`
- Modify: 对应模块的常规测试文件(迁移少量真行为用例)

- [ ] **Step 1: 逐个过一遍三个文件,把 `inspect.getsource()` 类断言全部丢弃;把真行为测试(如 ensemble 除零、加密 roundtrip)复制到对应模块测试(如 `tests/test_risk_management.py`、`tests/test_security_compliance.py`),并跑通**

- [ ] **Step 2: 删除三个文件,跑全量** → `pytest tests/ -m "not network" -q` 全绿

- [ ] **Step 3: 提交** → `test: drop source-inspection quality_batch tests, keep behavioral cases`

**验收:** `ls tests/ | grep quality_batch` 为空;ruff 仍在 CI 承担风格检查。

### Task 2.5: 合并 oauth2 三份测试(C-06)

**Files:**
- Delete: `tests/test_oauth2_provider_quality.py`、`tests/test_oauth2_provider_quality_v2.py`
- Modify: `tests/test_oauth2_provider.py`(补齐 v1/v2 各自独有的用例:token 轮换、可变输入复制等)

- [ ] **Step 1: 用 `git diff --no-index` 对比三份文件,列出 v1 与 v2 各自独有的测试名清单**

- [ ] **Step 2: 把独有用例全部合并进 `test_oauth2_provider.py`,去重后跑绿**

- [ ] **Step 3: 删除两份 quality 文件,提交** → `test: merge oauth2 provider test suites into one`

**验收:** `ls tests/ | grep -c oauth2_provider` == 1;合并后测试数 = 三者去重之和。

### Task 2.6: 从 binance 子模块删除复制的根测试(C-05)

**Files:**
- Delete(在子模块仓库): `bt_api/bt_api_binance/tests/test_monitoring.py`、`tests/test_security_compliance.py`、`tests/test_bt_api_unified.py`

- [ ] **Step 1: 确认这三个文件测的对象是 `bt_api_py.*` 而非 `bt_api_binance.*`(grep import 头 30 行)**

- [ ] **Step 2: 在子模块仓库删除并提交**

```bash
cd bt_api/bt_api_binance
git rm tests/test_monitoring.py tests/test_security_compliance.py tests/test_bt_api_unified.py
git commit -m "test: remove duplicated bt_api_py suite copies, maintained in parent repo"
git push origin HEAD:$(git branch --show-current)
cd ../..
```

- [ ] **Step 3: 母仓库更新 pin** → `git add bt_api/bt_api_binance && git commit -m "chore: bump binance submodule pin (dedup tests)"`

**验收:** 子模块仓库中三个文件已删除;母仓 pin 已更新。

### Task 2.7: pytest 配置清理(C-07、C-08、C-09)

**Files:**
- Modify: `pyproject.toml:53-58`(addopts)、`conftest.py:354-368`(reset_environment)、`tests/test_security_compliance.py:1133`(tmp_keys 路径)

- [ ] **Step 1: 从 `addopts` 移除 `--dist=loadgroup`**(xdist 参数移入 CI 命令:`pytest -n auto --dist=loadgroup`);同时删除 `python_files/classes/functions` 重复默认声明。裸 `pytest tests/ -m "not network" -q` 必须可用

- [ ] **Step 2: `reset_environment` 从 autouse 改为按需 fixture**(需要环境隔离的测试模块显式 `@pytest.fixture` 引入);全量跑绿验证无环境泄漏

- [ ] **Step 3: `./tmp_keys` 相对路径改为 `tmp_path` fixture**

```python
def test_key_material_written_to_tmp(tmp_path) -> None:
    key_dir = tmp_path / "tmp_keys"
    # 原 "./tmp_keys" 全部替换为 str(key_dir)
```

- [ ] **Step 4: 提交** → `test: clean pytest config and tmp path handling`

**验收:** 迭代 2 验收清单全绿。

---

## 迭代 3:发布链路与工程化修复(3–4 天)

**目标:** 让发布真正可执行、版本单一、CI 覆盖子模块、文档构建稳定(Spec D-01~D-15)。

**验收标准:**
- [ ] `python -m build && twine check dist/*` 通过
- [ ] tag↔版本一致性校验步骤可被本地脚本模拟验证(错 tag 必失败)
- [ ] `mkdocs build --strict` 绿
- [ ] nightly 子模块 workflow 通过 `workflow_dispatch` 手动触发一次成功
- [ ] code-reviewer agent 复审通过

### Task 3.1: 添加 LICENSE(D-01)

- [ ] **Step 1: 从 https://opensource.org/license/mit 取 MIT 标准文本,保存为仓库根 `LICENSE`(Copyright 行填 `Copyright (c) 2026 cloudQuant`)**

- [ ] **Step 2: 确认 `pyproject.toml:10` 改为 PEP 639 写法** `license = "MIT"`(替换 `license = {text = "MIT"}`)

- [ ] **Step 3: 提交** → `docs: add MIT LICENSE file`

**验收:** `git ls-files | grep -i license` 命中 LICENSE。

### Task 3.2: 版本单一源(D-02)

**Files:**
- Delete: `bt_api_py/_version.py`
- Modify: `bt_api_py/__init__.py:9-15`(版本导入段)、`pyproject.toml`(version 字段)

- [ ] **Step 1: `pyproject.toml` 设 `version = "0.15.2"`(与当前 bt_api_base 实际版本对齐,从 `bt_api/bt_api_base/src/bt_api_base/_version.py` 核对)**

- [ ] **Step 2: `__init__.py` 版本段替换为:**

```python
from importlib.metadata import PackageNotFoundError, version as _package_version

try:
    __version__ = _package_version("bt_api_py")
except PackageNotFoundError:  # 未安装(源码树直接 import)时的兜底
    __version__ = "0.0.0.dev0"
```

删除 `bt_api_py/_version.py`,并 grep 全仓确认无其他 `from bt_api_py._version import` / `from bt_api_base._version import` 残留(适配器侧引用 bt_api_base 版本的不动)。

- [ ] **Step 3: 测试** → `python -c "import bt_api_py; print(bt_api_py.__version__)"` 输出 `0.15.2`

- [ ] **Step 4: 提交** → `refactor: single version source via importlib.metadata`

**验收:** `grep -rn "_version" bt_api_py/ | grep -v importlib` 无结果。

### Task 3.3: publish.yml 重写为单次构建 + tag 校验(D-03)

**Files:**
- Modify: `.github/workflows/publish.yml`

- [ ] **Step 1: 用以下结构替换矩阵构建(9 job → 1 job)**

```yaml
jobs:
  build:
    runs-on: ubuntu-latest
    permissions:
      contents: read
    steps:
      - uses: actions/checkout@v4
      - uses: actions/setup-python@v5
        with:
          python-version: '3.11'
      - name: Verify tag matches package version
        run: |
          python -m pip install --quiet .
          PKG_VERSION=$(python -c "import importlib.metadata; print(importlib.metadata.version('bt_api_py'))")
          TAG_VERSION="${GITHUB_REF_NAME#v}"
          if [ "$PKG_VERSION" != "$TAG_VERSION" ]; then
            echo "::error::tag $GITHUB_REF_NAME != package version $PKG_VERSION"
            exit 1
          fi
      - name: Build
        run: |
          python -m pip install --quiet build twine
          python -m build
          twine check dist/*
      - uses: actions/upload-artifact@v4
        with:
          name: dist
          path: dist/
```

发布 job 保持单一平台(不再需要 download-artifact merge)。

- [ ] **Step 2: 本地模拟校验**

```bash
PKG_VERSION=$(python -c "import importlib.metadata; print(importlib.metadata.version('bt_api_py'))")
# 模拟错 tag:v0.9.9 → 校验必须失败(手动把 TAG_VERSION 改成 0.9.9 跑一遍脚本,记录失败输出)
python -m build && twine check dist/*
```

- [ ] **Step 3: 提交** → `ci: simplify publish to single py3-none-any build with tag-version gate`

**验收:** 错 tag 模拟失败(输出留档);build + twine check 通过。

### Task 3.4: 清理 setup.py / MANIFEST.in 的 CTP 死代码(D-07)

**Files:**
- Modify: `setup.py`、`MANIFEST.in`

- [ ] **Step 1: 删除 setup.py 中 CTP/Cython 扩展构建逻辑(`bt_api_py/ctp/`、`functions/` 相关),保留纯 Python 包配置;删除 MANIFEST.in 中引用不存在文件的 include 行(含 `requirements.txt`)**

- [ ] **Step 2: `python -m build` 重跑确认产物仍为纯 wheel**

- [ ] **Step 3: 提交** → `chore: drop CTP build scaffolding that references removed sources`

**验收:** `grep -i ctp setup.py MANIFEST.in` 无结果(除注释);build 通过。

### Task 3.5: Python 版本声明三处对齐(D-09)

- [ ] **Step 1: `pyproject.toml` 的 `requires-python`、classifiers、publish 矩阵统一为:requires-python `>=3.9`(纯 Python 包保守兼容)、classifiers 3.9–3.13、CI/publish 在 3.11 构建 py3-none-any(任何 ≥3.9 可装)**

- [ ] **Step 2: 提交** → `chore: align python version claims across pyproject/setup/ci`

**验收:** 三处一致,无 3.14 classifier。

### Task 3.6: mypy 恢复关键错误码(D-08)

**Files:**
- Modify: `pyproject.toml:213-220`(mypy 配置)

- [ ] **Step 1: 把 `disable_error_code` 至少恢复 `arg-type`、`attr-defined`、`call-arg`、`var-annotated`(先移出名单)**

- [ ] **Step 2: 跑 `mypy bt_api_py` 收集错误,按错误类型归类;能修则修,修不了的逐一显式 `# type: ignore[code]` 并注明原因(禁止批量加 ignore)**

- [ ] **Step 3: CI 的 mypy step 跑绿后提交** → `ci: re-enable core mypy error codes`

**验收:** `mypy bt_api_py --strict` 的错误数在报告中记录;关键错误码已生效(用 `grep disable_error_code pyproject.toml` 验证名单缩短)。

### Task 3.7: _generate_docs.py 修复(D-13、D-14)

**Files:**
- Modify: `_generate_docs.py:212-220`、`:451-469`、`:565,588`

- [ ] **Step 1: `"x" in containers or True` → `"x" in containers`(Supported Operations 表恢复真实)**

- [ ] **Step 2: 硬编码 64 仓清单 → 动态读 `.gitmodules`(代码见迭代 0 Task 0.4 Step 2,如已做则跳过)**

- [ ] **Step 3: 生成的子包 CI 模板中 `pytest tests/ || true`、`mypy || true` 去掉 `|| true`;`checkout@v4` 升级为 v6(与仓库其他 workflow 一致)**

- [ ] **Step 4: 重跑生成器,抽查 3 个交易所 README 的 Supported Operations 与仓库实际一致**

- [ ] **Step 5: 提交** → `fix: generate truthful supported-ops tables and failing-capable subpackage CI`

**验收:** 抽查 README 与源码一致;模板中无 `|| true`。

### Task 3.8: 子模块夜间矩阵 CI + install_and_test_all 报告(D-04、B-19)

**Files:**
- Create: `.github/workflows/submodule-tests.yml`
- Modify: `bt_api/install_and_test_all.py`(SKIP 状态、并行、报告)

- [ ] **Step 1: `install_and_test_all.py` 改造**

- 无 tests/ 的包返回状态 `SKIP` 而非 `success=True`;最终汇总按 PASS/FAIL/SKIP 分列输出
- 加 `--parallel N`(concurrent.futures.ThreadPoolExecutor,注意每仓独立 venv/进程)
- 加 `--report markdown` 输出 `/tmp/submodule_report.md`(或 artifact 路径)

- [ ] **Step 2: 新建 workflow**

```yaml
name: submodule-tests
on:
  schedule:
    - cron: '0 18 * * *'   # 每日 UTC 18:00(北京 02:00)
  workflow_dispatch:
jobs:
  submodule-matrix:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v6
        with:
          submodules: recursive
          fetch-depth: 1
      - uses: actions/setup-python@v5
        with:
          python-version: '3.11'
      - run: python install_and_test_all.py --parallel 4 --report markdown
      - uses: actions/upload-artifact@v4
        with:
          name: submodule-report
          path: /tmp/submodule_report.md
```

- [ ] **Step 3: `workflow_dispatch` 手动触发一次,记录 PASS/FAIL/SKIP 分布;FAIL 清单进入迭代 4 backlog**

- [ ] **Step 4: 提交** → `ci: nightly submodule matrix with honest pass/fail/skip reporting`

**验收:** workflow 触发成功;报告三类状态分列;母仓库 CI 仍绿。

### Task 3.9: 双 changelog 合并 + docs 死引用 + mkdocstrings 路径(D-05、D-06、D-10、D-15)

**Files:**
- Modify: `CHANGELOG.md`、`docs/getting-started/change_log.md`(合并后删除后者)、`mkdocs.yml:71`、`docs/reference/*.md`、`.readthedocs.yaml`

- [ ] **Step 1: 合并两份 changelog 到 `CHANGELOG.md`(按发布内容修订 0.15.0 条目:删除 Registry/EventBus/CTP 等当前不存在的宣称),删除 `docs/getting-started/change_log.md` 并在 mkdocs nav 中更新链接**

- [ ] **Step 2: `mkdocs.yml` 的 mkdocstrings `paths: [src]` → `paths: [.]`;`docs/reference/registry.md`、`event_bus.md`、`auth_config.md` 中不存在模块的 directive 改为指向 `bt_api_base.registry` 等真实路径,或删除未实现功能文档页**

- [ ] **Step 3: `.readthedocs.yaml` 的 `fail_on_warning: false` → `true`;决策:保留 GH Pages 为主,RTD 停用(在仓库 README 标注唯一文档地址)**

- [ ] **Step 4: `mkdocs build --strict` 跑绿;`docs/superpowers/` 加入 mkdocs exclude(计划文档不发布)**

- [ ] **Step 5: 提交** → `docs: unify changelog, fix mkdocstrings paths and dead references`

**验收:** docs 构建 strict 绿;`grep -rn "bt_api_base.registry\|bt_api_base.event_bus\|bt_api_base.auth_config" docs/` 无结果。

### Task 3.10: scripts/Makefile CTP 残留与 CI 去重(D-11、D-12)

**Files:**
- Modify: `scripts/run_tests.sh:117`、`Makefile:128`、`.github/workflows/tests.yml:61-63`、`optimized-tests.yml`

- [ ] **Step 1: 删除 `--ignore=tests/test_ctp_feed.py` 与 ctp/api 路径清理(文件已不存在)**

- [ ] **Step 2: `tests.yml` 的 pip-audit 去掉 `|| true`,保留单一失败策略(建议 `continue-on-error: false`)**

- [ ] **Step 3: `optimized-tests.yml` 与 `tests.yml` 的兼容矩阵抽取为 reusable workflow 供两处调用;performance job 的条件改为 `schedule || inputs.run_performance`,并补 `permissions: contents: write`**

- [ ] **Step 4: 提交** → `ci: dedupe matrix via reusable workflow, make pip-audit actually fail`

**验收:** 迭代 3 验收清单全绿。

---

## 迭代 4:适配器正确性(5–8 天)

**目标:** 修复 OKX 签名(私有接口不可用)、接通错误翻译、补齐插件发现、统一限速/重连/日志脱敏,为 61 仓建立最低测试基线(Spec B-02、B-04~B-24)。

**验收标准:**
- [ ] OKX 黄金向量测试通过(公式向量见下,任何人可复算)
- [ ] 插件发现 smoke:遍历 `importlib.metadata.entry_points(group="bt_api.plugins")` 断言 ≥61 个入口
- [ ] 5 大仓(binance/okx/bybit/gateio/hyperliquid)错误翻译测试各 ≥3 个用例
- [ ] 每仓最低测试基线(签名黄金值/normalize 真实报文/错误翻译三选一按仓能力)在子模块仓库 CI 绿
- [ ] URL 配置 env 覆盖测试绿(B-11);base 下沉的 signers/timestamps/rate_limiters 有黄金向量测试(B-15);mojibake 扫描为空(B-20)
- [ ] 迭代 3 建立的 nightly matrix FAIL 清单清零(或显式降级为 SKIP 并在报告中说明)

### Task 4.1: OKX 时间戳 ISO 8601 + 黄金向量(B-02)

**Files:**
- Modify: `bt_api/bt_api_okx/src/bt_api_okx/feeds/live_okx/request_base.py:210-250`、`market_wss_base.py:86`
- Test: `bt_api/bt_api_okx/tests/feeds/test_okx_request_base.py`(新增)

**Interfaces:**
- Produces: 纯函数 `_sign(secret: str, timestamp: str, method: str, request_path: str, body: str) -> str` 与 `_utc_now_iso8601() -> str`

- [ ] **Step 1: 读 `request_base.py:180-260`,把签名计算提取为纯函数(不依赖实例时钟),时间戳生成提取为 `_utc_now_iso8601`:**

```python
from datetime import datetime, timezone


def _utc_now_iso8601() -> str:
    now = datetime.now(timezone.utc)
    return now.strftime("%Y-%m-%dT%H:%M:%S.") + f"{now.microsecond // 1000:03d}" + "Z"
```

`OK-ACCESS-TIMESTAMP` 与 WSS 登录的时间戳全部改用 `_utc_now_iso8601()`(WSS 侧同样替换 `str(round(time.time()))`)。

- [ ] **Step 2: 写黄金向量测试**

```python
def test_okx_sign_golden_vector() -> None:
    # 黄金向量按 OKX V5 文档公式: sign = Base64(HMAC-SHA256(timestamp+method+requestPath+body, secret))
    # 复算命令:
    # python3 -c "import hmac,hashlib,base64; s='F0E1D2C3B4A5968778695A4B3C2D1E0F'; pre='2020-12-08T09:08:57.715ZGET/api/v5/account/balance'; print(base64.b64encode(hmac.new(s.encode(),pre.encode(),hashlib.sha256).digest()).decode())"
    secret = "F0E1D2C3B4A5968778695A4B3C2D1E0F"   # 虚构测试密钥
    timestamp = "2020-12-08T09:08:57.715Z"
    assert _sign(secret, timestamp, "GET", "/api/v5/account/balance", "") == \
        "ymzav0cu8v4AhecjpRnt8sRQ8vOk/6+BT89eeU/sIjQ="


def test_okx_timestamp_is_iso8601() -> None:
    import re
    assert re.fullmatch(r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}\.\d{3}Z", _utc_now_iso8601())
```

- [ ] **Step 3: 跑测试确认失败**(当前 epoch 浮点格式)→ FAIL

- [ ] **Step 4: 实现(Step 1 的提取 + 替换),跑测试确认通过**

- [ ] **Step 5: 子模块仓库提交推送 + 母仓库 pin 更新**

```bash
cd bt_api/bt_api_okx
git add -A && git commit -m "fix: use ISO 8601 OK-ACCESS-TIMESTAMP per OKX V5 spec"
git push origin HEAD:$(git branch --show-current)
cd ../.. && git add bt_api/bt_api_okx && git commit -m "chore: bump okx submodule pin (signature fix)"
```

**验收:** 黄金向量测试绿(该向量任何人可用 Step 2 注释中的命令复算)。

### Task 4.2: 接通 translate_error(B-04)

**Files:**
- Modify: 5 仓 `request_base.py`(binance/okx/bybit/gateio/hyperliquid)+ 各自测试

- [ ] **Step 1: 读 binance `request_base.py:130-160` 的 translate_error 现有实现与响应解析位置**

- [ ] **Step 2: 每仓写失败测试(先 binance,其余仓复制模式)**

```python
def test_binance_error_response_raises_translated_error() -> None:
    base = BinanceRequestBase(...)   # 用假 key 构造
    raw = {"code": -2019, "msg": "Margin is insufficient."}
    with pytest.raises(BinanceError):   # 或框架统一异常
        base._raise_if_error(raw)
```

- [ ] **Step 3: 实现:在响应 JSON 解析后统一调用 `_raise_if_error`(内部调 translate_error,非空即 raise);错误码表按各交易所官方错误码补 3-5 个高频条目**

- [ ] **Step 4: 每仓测试绿后,按 Task 4.1 Step 5 模式 commit+push+pin**

**验收:** 5 仓错误翻译测试各 ≥3 用例绿;`grep -rn "translate_error" bt_api/bt_api_binance/src/ | grep -v def` 显示有真实调用点。

### Task 4.3: 补齐 29 仓插件入口(B-05)

**Files:**
- Modify: 29 个适配器仓库的 pyproject.toml / plugin.py / register 文件

- [ ] **Step 1: 读 bt_api_binance 的 pyproject entry-points 与 plugin.py,提炼模板(入口点格式、plugin.py 注册函数签名)**

- [ ] **Step 2: 写生成脚本 `scripts/fix_plugin_entries.py`:对 29 仓逐一检查 entry-points 缺失 → 按模板补 `[project.entry-points."bt_api.plugins"]` 段与 plugin.py(内容按模板生成,注释标 `# generated, verify register call`);bybit/gmx 等缺 plugin.py 的用 exchange-integration skill 的生成器产出后人工核对**

- [ ] **Step 3: 写发现 smoke 测试(母仓库 `tests/test_plugin_discovery.py`)**

```python
def test_all_plugin_entry_points_discoverable() -> None:
    from importlib.metadata import entry_points
    eps = [ep for ep in entry_points(group="bt_api.plugins")]
    names = {ep.name for ep in eps}
    assert len(names) >= 61, f"expected >=61 adapters, found {len(names)}: {sorted(names)[:10]}..."
```

- [ ] **Step 4: 各仓 commit+push+pin(同 Task 4.1 Step 5 模式);母仓库 smoke 测试绿**

**验收:** smoke 断言 ≥61 入口;`python -c "from bt_api_py.bt_api import BtApi"` 导入时间不劣化(记录前后对比)。

### Task 4.4: binance 空 key fail-fast(B-07)

- [ ] **Step 1: 写失败测试** → 构造 `private_key=None` 的 BinanceRequestData,断言构造时抛 ConfigError(或首次 sign 前抛)

- [ ] **Step 2: 实现** → `sign()` 中 `pk = self.private_key or ""` 改为构造期校验:私钥缺失直接抛框架 ConfigError;若某些公开接口确实无需签名,由调用方显式跳过签名路径,不允许空串签名

- [ ] **Step 3: 测试绿后 commit+push+pin** → `fix: fail fast on missing private key instead of signing with empty string`

**验收:** 测试绿;`grep -n 'or ""' bt_api/bt_api_binance/src/bt_api_binance/feeds/request_base.py | grep private` 无结果。

### Task 4.5: http_client 429/Retry-After(B-13)

**Files:**
- Modify: `bt_api/bt_api_base/src/bt_api_base/feeds/feed.py:194-251`(或 http_client.py 对应处)

- [ ] **Step 1: 写失败测试** → mock 响应 429 且带 `Retry-After: 1`,断言重试发生且第二次成功;5xx 同理(用现有重试测试的 mock 模式)

- [ ] **Step 2: 实现** → 响应 429/5xx 时按 Retry-After(秒)或指数退避重试,重试次数用现有常量;全部重试失败后抛框架统一异常(带最后状态码)

- [ ] **Step 3: 确认 6 个限速器接入统一的 base 工厂(本任务只改 base,适配器迁移到迭代 5 Task 5.8 的拆分时顺手做,或另立小任务)**

- [ ] **Step 4: 测试绿后 commit+push+pin(bt_api_base 排第一,其余仓随之验证)**

**验收:** 429 重试测试绿;母仓库 nightly matrix 中 binance/okx 相关测试不劣化。

### Task 4.6: OKX WSS 登录回执后订阅 + 重连重订阅(B-14)

**Files:**
- Modify: `bt_api/bt_api_okx/src/bt_api_okx/feeds/live_okx/market_wss_base.py:100-130`

- [ ] **Step 1: 读文件全文,定位 `open_rsp` 的 sleep(0.3) 与登录/订阅发送顺序**

- [ ] **Step 2: 写失败测试**(fake ws socket 记录 send 调用序列):

```python
def test_subscribe_waits_for_login_ack() -> None:
    fake = FakeWssSocket()   # 按现有 wss 测试的 fake 模式
    client = MarketWssBase(..., socket=fake)
    client.open_rsp(...)
    assert fake.sent_sequence[0] == "login"
    fake.emit_login_ack()    # 模拟收到登录成功回执
    assert fake.sent_sequence[1] == "subscribe"
```

- [ ] **Step 3: 实现** → 登录后等待 login 成功回调(事件/队列,带超时上限如 5s,超时记错误日志),再发送订阅;维护 `self._pending_subscriptions` 列表,`on_open`(重连)时自动重放订阅

- [ ] **Step 4: 测试绿后 commit+push+pin** → `fix: wait for okx login ack before subscribing; resubscribe on reconnect`

**验收:** 序列测试绿;`grep -n "sleep(0.3)"` 该文件无结果。

### Task 4.7: hyperliquid 签名决策门(B-12)

**决策标准(先做决策,再执行对应分支):**
- 读 Hyperliquid 官方 API 文档确认当前 X-API-Key 模式是否为官方支持的签名模式(agent wallet)。
- **分支 A(仅支持 vault/agent 模式)**:删除 request_base 中私钥加载与 `is_sign` 参数死代码,plugin.py 中 `PluginInfo` 声明支持模式;测试断言无 misleading 参数。
- **分支 B(需 EIP-712)**:用 `eth_account` 实现 Hyperliquid 的 EIP-712 签名(action 结构按官方文档),黄金向量测试用官方文档示例私钥与期望签名;删除仅发 X-API-Key 的误导路径。

- [ ] **Step 1: 读 `request_base.py:70-130` 与官方文档,产出一页决策记录(选择 A 或 B + 理由)放进该仓 `docs/decisions/`**

- [ ] **Step 2: 按分支实现 + 测试(分支 A 的删除以测试断言参数不存在为验收;分支 B 以黄金向量为验收)**

- [ ] **Step 3: commit+push+pin** → `fix: hyperliquid signing support aligned with documented auth modes`(或 `refactor: remove misleading private-key handling`)

**验收:** 决策记录存在;对应分支测试绿。

### Task 4.8: bybit WSS 决策门(B-08)

**决策标准:** 本迭代不实现 WSS(工作量大)。在 bybit 的 `plugin.py` 的 PluginInfo/能力声明中显式标注 `supports_wss: false`(按现有能力声明格式),框架/文档按能力降级(实时行情降级为 REST 轮询)。WSS 实现另立 backlog(引用 exchange-integration skill 的生成器)。

- [ ] **Step 1: 读一个支持 WSS 的仓(如 okx)的 plugin.py,确认能力声明字段**

- [ ] **Step 2: bybit plugin.py 补 `supports_wss: false` + 母仓库文档能力矩阵标注 bybit 无 WSS**

- [ ] **Step 3: commit+push+pin** → `docs: declare bybit wss capability as unsupported`

**验收:** 能力声明存在;能力矩阵文档与代码一致。

### Task 4.9: hyperliquid 日志脱敏 + 共享 logger 名收敛(B-17、B-23)

- [ ] **Step 1: `async_request` 的 f-string URL 日志改为走 base 的 `_sanitize_for_log` 出口(读 binance 对应实现抄模式)**

- [ ] **Step 2: bybit/gateio 等 `get_logger("request")`/`get_logger("async_request")` 改为按仓命名(如 `get_logger("bybit_spot_feed")`),测试断言 logger 名以仓名前缀开头**

- [ ] **Step 3: 各仓 commit+push+pin** → `fix: sanitize request logging and namespace loggers per exchange`

**验收:** `grep -rn 'f"Async Request' bt_api/bt_api_hyperliquid/src/` 无结果;logger 名按仓隔离。

### Task 4.10: gateio ctp 残留与 btbns 空仓处置(B-16)

- [ ] **Step 1: 删除 `bt_api/bt_api_gateio/src/bt_api_ctp/` 残留包,commit+push+pin**

- [ ] **Step 2: btbns 决策门:实现(有真实交易所需求)或从 .gitmodules 移除(暂未实现)。选择移除 → 母仓库 `git rm` 子模块 + .gitmodules 条目 + 残留引用清理;选择实现 → 按 exchange-integration skill 生成器产出并纳入迭代 4 验收**

- [ ] **Step 3: commit+push+pin** → `chore: remove ctp residue from gateio; retire empty btbns submodule`(按决策)

**验收:** gateio 仓无 ctp 目录;btbns 按决策落地且 .gitmodules 与母仓库一致。

### Task 4.11: 每仓最低测试基线(B-10)

**Files:**
- Modify: 61 仓 tests/(生成基线模板 + 逐仓落地)

- [ ] **Step 1: 定义三档基线(按仓能力)并写进母仓库 `docs/code-quality-baseline.md`:**
  - L1(头部 5 仓):签名黄金值 + normalize 真实报文 + 错误翻译,各 ≥3 用例
  - L2(有私有接口的仓):签名黄金值 + 错误翻译
  - L3(纯公开接口仓):normalize 真实报文 ≥2 用例(禁止 exchange_name 单断言空壳)

- [ ] **Step 2: 修掉现有空壳测试(binance 自指签名测试改为调用被测方法断言黄金值;zebpay 单断言测试补齐 normalize 用例)**

- [ ] **Step 3: 各仓逐仓落地基线并 commit+push;母仓库 nightly matrix 全绿(FAIL 清零)**

- [ ] **Step 4: 文档 + pin 提交**

**验收:** 基线文档存在;每仓至少一条"真实行为"测试;nightly matrix 无 FAIL(除显式 SKIP)。

### Task 4.12: 版本节奏统一(B-24)

- [ ] **Step 1: 决策记录:发布列车(每季度一次?跟随 bt_api_base 发版?)+ 各仓 `core_requires` 与母仓 pin 的 bump 脚本(scripts/bump_all_submodules.py:读 .gitmodules → 逐仓 bump patch 版本并 commit tag)**

- [ ] **Step 2: 本迭代只落决策文档与 bump 脚本(执行在下个发版周期)**

**验收:** 决策文档 + 脚本 dry-run 输出正确(bump 列表与 .gitmodules 一致)。

### Task 4.13: URL 配置统一、跨仓工具函数下沉、编码与测试密钥清理(B-11、B-15、B-20、B-21)

**Files:**
- Create: `bt_api/bt_api_base/src/bt_api_base/functions/signers.py`(统一 hmac 签名)、`timestamps.py`(ISO8601/epoch 转换)、`rate_limiters.py`(统一工厂)
- Modify: okx/hyperliquid 的 exchange_data(URL 走 yaml/env)、`bt_api_base` 中文 docstring(mojibake)、`bt_api_binance/tests/test_binance_sign.py:15`(硬编码私钥)

- [ ] **Step 1: bt_api_base 新增 functions 三模块**(B-15:39 处 hmac、15+ 处时间戳转换、6 处限速器工厂收敛为一份实现;接口:`sign_hmac_sha256(secret, payload) -> str`、`utc_now_iso8601() -> str`、`create_default_rate_limiter(exchange) -> RateLimiter`),base 仓先 commit+push+pin,附单测与黄金向量

- [ ] **Step 2: okx/hyperliquid 的 exchange_data 硬编码 URL 改为从 `configs/*.yaml` 读取 + 环境变量覆盖(B-11);测试:monkeypatch env 后 base_url 变化;提供 `testnet:` 配置段作为切换入口**

- [ ] **Step 3: 修复 `bt_api_base` 中文 docstring mojibake(连接 mixin 等 8+ 处),重写为正常 UTF-8 文本;CI 增加编码检查(`python -c "import pathlib,sys; bad=[p for p in pathlib.Path('src').rglob('*.py') if '�' in p.read_text()]"` 为空)(B-20)**

- [ ] **Step 4: `test_binance_sign.py:15` 硬编码 64 位 hex 私钥替换为占位常量 `TEST_PRIVATE_KEY = "0" * 64` 并加注释说明是占位;向密钥所有者确认该 key 未在任何实盘环境启用过(B-21)**

- [ ] **Step 5: 各仓 commit+push+pin;base 下沉后跑 nightly matrix 确认各仓无回归(重点看限速器行为不变化)**

**验收:** base functions 模块存在且有黄金向量测试;env 覆盖测试绿;mojibake 扫描为空;测试密钥已替换。

---

## 迭代 5:架构收敛与安全加固(5–8 天)

**目标:** 删除死代码与平行子系统,修复安全模块自身缺陷,拆分巨型文件,收敛公共 API(Spec F-01~F-12、E-01~E-07、A-09~A-12、A-16、A-18)。

**验收标准:**
- [ ] `grep` 死代码清单(见下)全部零引用且已删除或归档
- [ ] `scripts/check_file_sizes.py`(新增)报告所有源文件 ≤800 行
- [ ] 安全修复项测试全绿(E-02~E-05)
- [ ] `python -c "import bt_api_py"` 导入耗时下降(记录前后对比),且坏插件不再阻断导入
- [ ] code-reviewer agent 复审通过

### Task 5.1: 死代码删除(F-01、F-05、F-02)

**Files:**
- Delete: `bt_api_py/feed_registry.py`、`bt_api_py/_plugin_shims.py`、`bt_api_py/utils/time.py`、`bt_api_py/backtrader/mapping.py`(空)、空 `__init__.py` 视情况
- Modify: `bt_api_py/risk_management/__init__.py:38-70`(`__all__` 双定义)
- Modify: `bt_api_py/__init__.py`(re-export 收敛)

- [ ] **Step 1: 逐文件 `grep -rn "feed_registry\|_plugin_shims\|utils.time" bt_api_py/ tests/ examples/ scripts/` 确认零引用后删除(引用者存在则先改引用再删)**

- [ ] **Step 2: `risk_management/__init__.py` 删除第二个 `__all__` 与 4 个不存在的类名;`import *` 冒烟测试:**

```python
def test_risk_management_star_import() -> None:
    ns: dict = {}
    exec("from bt_api_py.risk_management import *", ns)  # noqa: S102 - 仅测试导出完整性
    assert "RiskCalculator" in ns
```

- [ ] **Step 3: 提交** → `refactor: delete dead facades and fix risk_management exports`

**验收:** 死文件删除;全量测试绿;`grep -rn "bt_api_py.feed_registry" . --include="*.py"` 零结果。

### Task 5.2: 延迟插件加载 + __getattr__ 收敛(A-10、A-09)

**Files:**
- Modify: `bt_api_py/bt_api.py:101-111`(import 期加载)、`:784-801`(`__getattr__`)

- [ ] **Step 1: 插件全量加载从模块 import 期移入首次 `BtApi.__init__` 或显式 `load_plugins()`;单插件异常降级为告警并跳过(不阻断整个包导入)**

- [ ] **Step 2: 写失败测试:**

```python
def test_import_bt_api_py_does_not_load_plugins(monkeypatch) -> None:
    # monkeypatch PluginLoader.load_all 记录调用次数
    import bt_api_py
    assert loader_calls == 0   # import 不触发

def test_broken_plugin_does_not_break_import(monkeypatch) -> None:
    # 注入一个抛异常的 entry point,import 必须成功且告警
```

- [ ] **Step 3: `__getattr__` 动态代理改为:常用 async 方法(place_order/cancel_order/query 等)显式定义并标注返回类型;`hasattr` 恒真问题通过删除无界代理解决(剩余代理仅转发到明确的 `_async_api` 对象且返回 `Awaitable` 注解)**

- [ ] **Step 4: 提交** → `perf: lazy plugin loading; refactor: explicit async facade instead of __getattr__ magic`

**验收:** 测试绿;`python -X importtime -c "import bt_api_py" 2>&1 | tail -3` 耗时记录对比。

### Task 5.3: 12k 行平行子系统决策门(F-06、E-01)

**决策标准(本迭代产出决策+落地第一步,不要求全部接入):**
- **选项 A(接入)**:AuditLogger 接入 forwarding/router.py 的下单/撤单路径(每条命令审计事件);TLSManager 接入 http_client 连接;其余(risk ML、monitoring collector)按需求逐个立项。
- **选项 B(降级)**:在 README/模块 docstring 明确标注"参考实现/未接入生产路径",移除"production-ready"宣称。
- 推荐 A+最小集:本轮接入 AuditLogger(资金审计刚需),其余标注降级;RiskRuleSet 与 risk_management 的收敛另立 backlog。

- [ ] **Step 1: 写决策记录 `docs/decisions/2026-08-16-parallel-subsystems.md`(选项、理由、后续 backlog)**

- [ ] **Step 2: 若选 A:router.py 下单/撤单成功与失败路径各发一条审计事件(测试断言事件已落盘且含脱敏字段);若选 B:模块 docstring + README 标注,并修正 monitoring/README.md 的 production-ready 宣称**

- [ ] **Step 3: 提交** → `feat: wire audit logging into order router`(或 `docs: mark parallel subsystems as reference implementations`)

**验收:** 决策记录存在;对应落地测试/文档改动完成。

### Task 5.4: audit_logger 脱敏 + 真原子写 + 加密参数(E-02)

**Files:**
- Modify: `bt_api_py/security_compliance/core/audit_logger.py:145,158,198,256-280`

- [ ] **Step 1: 写失败测试:**

```python
def test_audit_details_are_redacted() -> None:
    events = log_events([{"api_key": "secret123", "amount": 1.0}])
    payload = json.dumps(events)
    assert "secret123" not in payload

def test_audit_write_is_atomic_rename(tmp_path) -> None:
    # 断言无中间临时文件残留,最终文件是完整 JSON
```

- [ ] **Step 2: 实现** → ①details 键名白名单:key 名含 key/secret/token/password 的值全掩码;②`encryption_key` 参数二选一:实现加密(用 encryption_manager)或删除参数并在 docstring 注明明文模式;③`_write_event_atomic` 用 `tempfile + os.replace` 真原子写,写后 `os.chmod 0o600`**

- [ ] **Step 3: 提交** → `fix: redact secrets in audit logs, atomic rename writes, chmod 0600`

**验收:** 测试绿;审计落盘文件权限 0600。

### Task 5.5: PBKDF2 随机盐 + 密钥文件权限(E-03)

**Files:**
- Modify: `bt_api_py/security_compliance/core/encryption_manager.py:133-175`

- [ ] **Step 1: 写失败测试:**

```python
def test_pbkdf2_salt_is_random_per_encryption() -> None:
    c1 = encrypt("data", password="pw"); c2 = encrypt("data", password="pw")
    assert c1.salt != c2.salt      # 确定性盐(sha256(pw))下此断言失败
    assert decrypt(c1, "pw") == "data"

def test_key_file_permissions(tmp_path) -> None:
    write_key_file(tmp_path / "k")
    assert stat.S_IMODE(os.stat(tmp_path / "k").st_mode) == 0o600
```

- [ ] **Step 2: 实现** → salt 改 `os.urandom(16)` 并与密文一起持久化(兼容读取旧格式:检测到旧格式时记录告警);key_dir 0o700、key 文件 0o600**

- [ ] **Step 3: 提交** → `fix: random per-encryption salt and hardened key file permissions`

**验收:** 测试绿;旧格式兼容测试存在。

### Task 5.6: TLS 后门删除 + Prometheus 默认回环(E-04、E-05)

- [ ] **Step 1: 删除 `tls_manager.py:50-52` 的 `certificate_validation="none"` 分支(或保留参数但 None 时直接 raise NotImplementedError 并文档标注仅内网调试);测试断言无法构造 CERT_NONE 配置**

- [ ] **Step 2: `monitoring/prometheus.py:218` 默认 bind 改 `127.0.0.1`;公网暴露必须显式传 `host="0.0.0.0"` 且打印安全告警日志**

- [ ] **Step 3: 提交** → `security: remove cert-validation backdoor; bind prometheus exporter to loopback by default`

**验收:** `grep -rn "CERT_NONE" bt_api_py/` 无结果;默认 bind 测试绿。

### Task 5.7: 小项安全与正确性修复(E-06、E-07、F-03、A-16、A-12)

- [ ] **Step 1: `ml_base.py` pickle 加载:限制加载路径为包内 `models/` 目录 + 可选 hash 校验(manifest);测试断言包外路径被拒**

- [ ] **Step 2: `certification/audit.py:44` `mask_sensitive` 改全掩码(保留后 4 位 → 全部 `*`);测试断言原值任何子串不出现在结果**

- [ ] **Step 3: `risk_management/ml_models/*.py` sklearn 顶层 import 改惰性导入(函数内 import + ImportError 转友好提示);测试:未装 sklearn 环境 import 包不失败(monkeypatch 模拟)**

- [ ] **Step 4: `brokers/mock.py:112` 平均价改加权平均;测试:两笔不同价成交断言加权价**

- [ ] **Step 5: `bt_api.py:289-291` `get_async_request_api` 改名为 `get_request_api`(旧名保留 deprecated 别名,告警一次);调用方(测试/examples)同步**

- [ ] **Step 6: 提交** → `security: harden ml loading, full masking, lazy sklearn; fix: weighted average in mock broker`

**验收:** 各测试绿;`grep -rn "get_async_request_api" bt_api_py/` 仅剩 deprecated 别名。

### Task 5.8: 巨型文件拆分(F-11、B-18)

**Files:**
- Modify: `bt_api/bt_api_binance/src/bt_api_binance/feeds/request_base.py`(2581 行)、`bt_api_py/risk_management/core/risk_calculator.py`(960)、`limits_manager.py`(932)、`ml_models/anomaly_detector.py`(900)、`policy_engine.py`(825)、`bt_api_py/bt_api.py`(866)
- Create: `scripts/check_file_sizes.py`

**拆分原则:按职责拆,保留原模块 re-export 兼容(`from X import *` 级兼容,分模块内 import 不破坏调用方)。**

- [ ] **Step 1: 写 `scripts/check_file_sizes.py`(扫描所有源文件 >800 行,输出清单,CI 接入此检查)**

- [ ] **Step 2: binance request_base 拆分 → `auth.py`(签名/头)、`rest_market.py`(行情接口)、`rest_trade.py`(下单/撤单)、`normalize.py`(归一化函数);`request_base.py` 变为 re-export + 公共基类。拆分时顺手:①删除 :200-210 注释掉的 `print(self.public_key/private_key)` 调试代码(B-22);②`request()` 签名对齐 base 协议(显式 `is_sign: bool = False` 参数,B-06,okx/gateio/hyperliquid 在各自仓同步对齐并补参数测试)。每步移动后跑该仓全量测试**

- [ ] **Step 3: risk_management 4 个文件按风险类别拆分(如 risk_calculator → position/leverage/liquidity 三个子模块);bt_api.py 拆 `exchange_manager.py`(交易所注册/查询)、`data_downloader.py`(kline/下载)、`balance_manager.py`(余额),`bt_api.py` 保留门面。拆分 data_downloader.py 时统一 `KLINE_PERIOD_DELTAS` 为小写并补齐 2m/4m/1W(A-18),补参数化测试,旧大小写输入保持兼容(归一化后查表)**

- [ ] **Step 4: 各仓/母仓库测试全绿后 commit+push+pin** → `refactor: split oversized modules by responsibility`

**验收:** `check_file_sizes.py` 报告 0 个超限文件;全量测试绿(拆分不得改变行为,只移代码)。

### Task 5.9: 剩余小项(F-07、F-08、F-09、F-10、F-12)

- [ ] **Step 1: 两个占位模块的决策门。①`gateway_bridge.py` 占位方法:无消费方则删除模块并在 broker 文档注明 gateway 通过 ForwardingClient 走;有消费方则实现并补测试。②`backtrader/btapibroker.py` 17 行 stub(F-04):要么实现真正的 BrokerBase/Store 集成(按 backtrader 官方 store 模式,带下单/持仓/行情测试),要么从 `bt_api_py/__init__.py` 顶层导出撤下、模块标注 `@deprecated`,避免给用户"backtrader 集成可用"的假象;决策记录进 `docs/decisions/`**

- [ ] **Step 2: `hub.py:18-34` refcount 死簿记删除或接入 bus 过滤;测试:订阅/退订影响实际投递(若删除则测试 refcount 不存在)**

- [ ] **Step 3: `monitoring/config.py` 反向依赖改从子模块导入**

- [ ] **Step 4: `ctp_env_selector.py` SimNow IP 硬编码移入配置文件(默认值进 configs/,允许 env 覆盖);os.environ 传递改显式参数**

- [ ] **Step 5: `gateway/client.py` 7 个别名参数收敛为单一 endpoint 参数集(别名保留一个迁移期,`DeprecationWarning` 告警)**

- [ ] **Step 6: 提交** → `refactor: resolve parallel-subsystem loose ends`

**验收:** 迭代 5 验收清单全绿。

---

## 跨迭代机制

### 每日/每任务节奏
1. 任务内 TDD:RED → GREEN → commit(提交信息见任务)。
2. 每迭代结束:跑迭代验收清单 → 派发 code-reviewer agent 复审 → CRITICAL/HIGH 清零 → 记录迭代报告到 `docs/superpowers/plans/reports/2026-08-16-iter<N>.md`(含:验收命令输出摘要、遗留问题、时间花费)。

### 子模块变更的标准三步(所有涉及子模块的任务通用)
```bash
cd bt_api/bt_api_<name>
git add -A && git commit -m "<conventional commit>"
git push origin HEAD:$(git branch --show-current)
cd ../..
git add bt_api/bt_api_<name>
git commit -m "chore: bump <name> submodule pin (<reason>)"
```

### 风险清单
| 风险 | 缓解 |
|------|------|
| 迭代 0 前有人执行 `submodule update --force` 丢改动 | 在团队频道/README 置顶警告;迭代 0 排最高优先级,当周完成 |
| OKX 等仓的远端可能无权限推送 | 迭代 0 Step 4 记录失败仓清单,向 cloudQuant 管理员申请权限后再推 |
| 迭代 2 恢复红灯后暴露历史积压失败 | 按模块分流到对应迭代修复,禁止重新引入任何"自动跳过" |
| 拆分巨型文件引入回归 | 纯移动不改逻辑;每步跑全量测试;diff 仅允许 import 路径变化 |
| nightly matrix 长尾仓大量失败 | 允许显式 SKIP(在报告标注原因),FAIL 清零目标只针对有维护价值的仓 |

### 定义完成(DoD)
一个任务"完成"= 测试绿(命令+输出留档)+ 覆盖率达标(代码任务)+ commit 规范 + 验收清单勾选;一个迭代"完成" = 迭代验收标准全部勾选 + code-reviewer 复审通过 + 迭代报告落盘。
