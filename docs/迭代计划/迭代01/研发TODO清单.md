# 迭代01 研发 TODO 清单

> 角色：技术经理交付给研发的可执行清单
> 前置文档：完整迭代文档、研发设计文档
> 格式说明：
> - `[ ]` 未开始 / `[~]` 进行中 / `[x]` 完成
> - 每项包含：任务 ID、优先级、复杂度、负责人、依赖、验收脚本

---

## 图例

| 标记 | 含义 |
|------|------|
| 🔴 P0 | 阻塞项，未完成不能开下一个 |
| 🟠 P1 | 本轮必达 |
| 🟡 P2 | 本轮 nice-to-have |
| ⚫ P3 | 不在本轮范围 |
| S | Small: <300 行，当天能 PR |
| M | Medium: 300~1000 行 |
| L | Large: >1000 行或整目录搬运 |
| XL | Extra Large: 跨平台/特殊依赖 |

---

## 阶段 0：前置 sign-off（阻塞后续所有）

### [ ] T000 — 研发设计文档评审 sign-off
- **优先级**：🔴 P0
- **复杂度**：S
- **负责人**：技术经理 + 架构师 + 研发代表
- **依赖**：无
- **产出**：文档末尾加签，评审人明确回复"同意按本设计执行"
- **验收**：至少 3 位评审人 sign-off

### [ ] T001 — 拉出迭代分支
- **优先级**：🔴 P0
- **复杂度**：S
- **依赖**：T000
- **指令**：
  ```bash
  git checkout master && git pull
  git checkout -b iter/01-plugin-refactor
  git push -u origin iter/01-plugin-refactor
  ```
- **验收**：远端分支存在

---

## 阶段 1：bt_api_base 基础包创建

### [ ] T101 — 创建 bt_api_base 包骨架 (PR-01)
- **优先级**：🔴 P0
- **复杂度**：M
- **负责人**：核心研发
- **依赖**：T001
- **涉及文件**：
  - `bt_api_base/pyproject.toml`（新）
  - `bt_api_base/README.md`（新）
  - `bt_api_base/src/bt_api_base/__init__.py`（新）
  - `bt_api_base/src/bt_api_base/exceptions.py`（新）
- **实现要点**：
  - `pyproject.toml` 定义包元数据，依赖 `requests`、`aiohttp`、`websocket-client` 等
  - `exceptions.py` 从 `bt_api_py/exceptions.py` 复制异常层级
- **验收**：
  ```bash
  pip install -e bt_api_base
  python -c "from bt_api_base import __version__; print('OK')"
  ```

### [ ] T102 — 抽取 containers 到 bt_api_base (PR-02)
- **优先级**：🔴 P0
- **复杂度**：L
- **负责人**：核心研发
- **依赖**：T101
- **迁移操作**：
  ```bash
  git mv bt_api_py/containers/* bt_api_base/src/bt_api_base/containers/
  ```
- **注意**：
  - 交易所特定的 containers（如 `binance_exchange_data.py`）不迁
  - 只迁移所有交易所共用的容器
- **验收**：
  ```bash
  python -c "from bt_api_base.containers import Ticker, OrderBook"
  pytest tests/containers/ -v  # 核心 CI 全绿
  ```

### [ ] T103 — 抽取 rate_limiter / balance_utils 到 bt_api_base
- **优先级**：🔴 P0
- **复杂度**：M
- **负责人**：核心研发
- **依赖**：T102
- **迁移**：
  ```bash
  git mv bt_api_py/rate_limiter.py bt_api_base/src/bt_api_base/
  git mv bt_api_py/balance_utils.py bt_api_base/src/bt_api_base/
  ```
- **验收**：
  ```bash
  python -c "from bt_api_base.rate_limiter import RateLimiter"
  python -c "from bt_api_base.balance_utils import simple_balance_handler"
  ```

### [ ] T104 — 抽取 testing/ 到 bt_api_base
- **优先级**：🟠 P1
- **复杂度**：S
- **负责人**：核心研发
- **依赖**：T103
- **迁移**：
  ```bash
  git mv bt_api_py/testing bt_api_base/src/bt_api_base/
  ```
- **目的**：让插件包的测试能用 `from bt_api_base.testing import ...`
- **验收**：
  ```bash
  python -c "from bt_api_base.testing import *"
  ```

---

## 阶段 2：核心插件机制

### [ ] T201 — 新增 bt_api_py/plugins/ 子包 (PR-03)
- **优先级**：🔴 P0
- **复杂度**：M
- **负责人**：核心研发
- **依赖**：T104
- **涉及文件**：
  - `bt_api_py/plugins/__init__.py`（新）
  - `bt_api_py/plugins/protocol.py`（新，定义 `PluginInfo`）
  - `bt_api_py/plugins/errors.py`（新，定义异常层级）
  - `bt_api_py/plugins/loader.py`（新，`PluginLoader` 核心逻辑）
- **实现要点**：
  - `PluginInfo` 用 `@dataclass(frozen=True)`
  - `PluginLoader.load_all()` 处理 6 种场景（见设计文档）
  - 版本兼容检查使用 `packaging.specifiers.SpecifierSet`
- **测试要求**：
  - `tests/plugins/test_loader.py`：覆盖加载成功、ImportError、版本不兼容、重复注册等 6 条 case
- **验收**：
  ```bash
  pytest tests/plugins/ -v
  python -c "from bt_api_py.plugins.loader import PluginLoader; print('OK')"
  ```

### [ ] T202 — 新增 GatewayRuntimeRegistrar (PR-03)
- **优先级**：🔴 P0
- **复杂度**：S
- **负责人**：核心研发
- **依赖**：T201
- **涉及文件**：
  - `bt_api_py/gateway/registrar.py`（新）
- **实现要点**：
  - 类级别字典，提供 register / get / list / clear
  - 重复注册同一 exchange_type 时 warn，不覆盖
- **测试要求**：`tests/gateway/test_registrar.py` 覆盖所有方法
- **验收**：`pytest tests/gateway/test_registrar.py -v`

### [ ] T203 — 改造 GatewayRuntime 去硬编码 (PR-03)
- **优先级**：🔴 P0
- **复杂度**：M
- **负责人**：核心研发
- **依赖**：T202
- **涉及文件**：
  - `bt_api_py/gateway/runtime.py`（改）
- **关键变更**：
  - 删除 `ADAPTER_REGISTRY` 类属性
  - 删除顶部 `from bt_api_py.gateway.adapters import ...` 硬编码导入
  - `_create_adapter` 改为通过 `GatewayRuntimeRegistrar.get_adapter()`
- **过渡期**：临时手工调用一次"把 5 个内置 adapter 注册到 Registrar"
- **验收**：
  ```bash
  pytest tests/gateway/ -v
  ```

### [ ] T204 — 把 PluginLoader 接入 bt_api.py 启动流程 (PR-04)
- **优先级**：🔴 P0
- **复杂度**：M
- **负责人**：核心研发
- **依赖**：T203
- **涉及文件**：`bt_api_py/bt_api.py`
- **实现要点**：
  - 先调用 `PluginLoader.load_all()`
  - 保留 legacy 扫描作为兜底
  - 加环境变量 `BT_API_DISABLE_LEGACY_SCAN=1` 可禁用 legacy
- **测试要求**：
  - `tests/plugins/test_bt_api_startup.py`：验证无插件时 BtApi 可构造
- **验收**：
  ```bash
  BT_API_DISABLE_LEGACY_SCAN=1 python -c "from bt_api_py.bt_api import BtApi; print('no-plugin OK')"
  ```

### [ ] T205 — CI 静态检查：核心包禁止 import 插件 (PR-04)
- **优先级**：🔴 P0
- **复杂度**：S
- **负责人**：DevOps
- **依赖**：T201
- **涉及文件**：
  - `scripts/check_core_isolation.py`（新）
  - `.github/workflows/ci.yml`（改）
- **验证**：故意在核心包加 `from bt_api_binance import foo`，确认 CI red
- **验收**：CI 步骤名为 `core-plugin-isolation`，阻断 PR 合并

---

## 阶段 3：bt_api_binance 插件拆分（样板）

### [ ] T301 — 创建 packages/bt_api_binance/ 目录骨架 (PR-05)
- **优先级**：🔴 P0
- **复杂度**：S
- **负责人**：Binance 研发
- **依赖**：T205
- **涉及文件**：
  - `packages/bt_api_binance/pyproject.toml`
  - `packages/bt_api_binance/README.md`
  - `packages/bt_api_binance/src/bt_api_binance/__init__.py`
  - `packages/bt_api_binance/src/bt_api_binance/plugin.py`（空壳）
- **验收**：
  ```bash
  pip install -e packages/bt_api_binance
  python -c "from bt_api_py.plugins.loader import PluginLoader; print('empty-plugin OK')"
  ```

### [ ] T302 — 迁移 Binance Feeds 整目录 (PR-06)
- **优先级**：🔴 P0
- **复杂度**：L
- **负责人**：Binance 研发
- **依赖**：T301
- **⚠️ 关键**：用 `git mv`，不要 `cp + rm`
- **迁移操作**：
  ```bash
  git mv bt_api_py/feeds/live_binance \
         packages/bt_api_binance/src/bt_api_binance/feeds/
  ```
- **兼容处理**：
  - `bt_api_py/feeds/live_binance_feed.py` 改写为 shim（带 DeprecationWarning）
- **内部 import 修复**：
  - 对 `from bt_api_base...` 的 import 保持不变
  - 对 `from bt_api_py...` 改为 `from bt_api_binance...`
- **验收**：
  ```bash
  pytest packages/bt_api_binance/tests/feeds/ -v
  # 主仓库核心 CI 全绿
  ```

### [ ] T303 — 迁移 Binance Gateway Adapter (PR-07)
- **优先级**：🔴 P0
- **复杂度**：M
- **负责人**：Binance 研发
- **依赖**：T302
- **迁移操作**：
  ```bash
  git mv bt_api_py/gateway/adapters/binance_adapter.py \
         packages/bt_api_binance/src/bt_api_binance/gateway/adapter.py
  ```
- **后续处理**：
  - 删除 `bt_api_py/gateway/adapters/__init__.py` 中对 `BinanceGatewayAdapter` 的引用
  - 在 `plugin.py` 里注册 adapter
- **验收**：
  ```bash
  grep -n "binance_adapter" bt_api_py/  # 必须为空
  pytest packages/bt_api_binance/tests/ -v
  ```

### [ ] T304 — 迁移 Binance Exchange Data (PR-07)
- **优先级**：🔴 P0
- **复杂度**：S
- **负责人**：Binance 研发
- **依赖**：T303
- **迁移操作**：
  ```bash
  git mv bt_api_py/containers/exchanges/binance_exchange_data.py \
         packages/bt_api_binance/src/bt_api_binance/exchange_data/binance.py
  ```
- **兼容处理**：保留 re-export shim，带 DeprecationWarning
- **验收**：主包内其他容器测试全绿

### [ ] T305 — 迁移 Binance 测试 (PR-08)
- **优先级**：🔴 P0
- **复杂度**：M
- **负责人**：Binance 研发
- **依赖**：T304
- **迁移操作**：
  ```bash
  git mv tests/test_binance*.py packages/bt_api_binance/tests/
  git mv tests/feeds/live_binance packages/bt_api_binance/tests/feeds/
  ```
- **验收（三条 AND）**：
  ```bash
  # 1. 主仓 tests/ 不得残留任何 binance 文件
  find tests/ -iname "*binance*" -type f  # 必须空

  # 2. 主仓 tests/ 不得 import live_binance
  grep -rnE "bt_api_py\.feeds\.live_binance\b" tests/  # 必须空

  # 3. pytest collect 不得发现 binance 用例
  pytest tests/ -k binance --collect-only  # 必须 0 collected

  # 4. 插件包测试独立可运行
  cd packages/bt_api_binance && pytest tests/ -v  # 全绿
  ```

### [ ] T306 — 完成 plugin.py + 删除 register_binance.py (PR-09)
- **优先级**：🔴 P0
- **复杂度**：M
- **负责人**：Binance 研发
- **依赖**：T305
- **实现**：
  - `plugin.py` 完成真实注册逻辑
  - 删除 `bt_api_py/exchange_registers/register_binance.py`（或改为空文件）
- **验收**：
  ```bash
  bash scripts/verify_plugin_smoke.sh bt_api_binance
  ```

---

## 阶段 4：bt_api_okx 插件拆分

> 策略：等阶段 3 完整跑通后，把 Binance 当样板复刻

### [ ] T401 — 创建 packages/bt_api_okx/ 骨架
- **优先级**：🟠 P1
- **复杂度**：S
- **负责人**：OKX 研发
- **依赖**：T306（binance 插件已完整拆完）
- **操作**：复刻 T301~T306 结构

### [ ] T402 — 迁移 OKX Feeds / Gateway / Exchange Data / Tests
- **优先级**：🟠 P1
- **复杂度**：L
- **负责人**：OKX 研发
- **依赖**：T401

### [ ] T403 — 完成 OKX plugin.py
- **优先级**：🟠 P1
- **复杂度**：M
- **负责人**：OKX 研发
- **依赖**：T402
- **验收**：`bash scripts/verify_plugin_smoke.sh bt_api_okx`

---

## 阶段 5：bt_api_ctp 插件拆分

> ⚠️ 最复杂阶段，提前 PoC 验证

### [ ] T501 — 创建 packages/bt_api_ctp/ 骨架（含 C++ 扩展）
- **优先级**：🟠 P1
- **复杂度**：XL
- **负责人**：CTP 研发
- **依赖**：T306
- **特殊处理**：
  - CTP SWIG 扩展需要跨平台编译
  - 先做 PoC：干净环境编译成功后再开始
- **涉及**：
  - `setup.py` 中 CTP 编译逻辑迁移
  - `bt_api_py/ctp/` 整体迁移
- **验收**：
  ```bash
  # macOS x64/arm64, Linux, Windows 三平台
  pip install packages/bt_api_ctp
  python -c "from bt_api_ctp import CtpGateway; print('CTP OK')"
  ```

### [ ] T502 — 迁移 CTP Feeds / Gateway / Exchange Data / Tests
- **优先级**：🟠 P1
- **复杂度**：L
- **负责人**：CTP 研发
- **依赖**：T501

### [ ] T503 — 完成 CTP plugin.py
- **优先级**：🟠 P1
- **复杂度**：M
- **负责人**：CTP 研发
- **依赖**：T502
- **验收**：`bash scripts/verify_plugin_smoke.sh bt_api_ctp`

---

## 阶段 6：bt_api_ib_web / bt_api_mt5 拆分

### [ ] T601 — 拆分 bt_api_ib_web
- **优先级**：🟡 P2
- **复杂度**：L
- **负责人**：IB Web 研发
- **依赖**：T306
- **特殊**：Playwright 依赖需要 `playwright install chromium` 后置步骤
- **验收**：`bash scripts/verify_plugin_smoke.sh bt_api_ib_web`

### [ ] T602 — 拆分 bt_api_mt5
- **优先级**：🟡 P2
- **复杂度**：M
- **负责人**：MT5 研发
- **依赖**：T306
- **注意**：MT5 只支持 Windows
- **验收**：`bash scripts/verify_plugin_smoke.sh bt_api_mt5`

---

## 阶段 7：验收与收尾

### [ ] T701 — 产出通用冒烟脚本
- **优先级**：🔴 P0
- **复杂度**：S
- **负责人**：DevOps
- **依赖**：T204
- **文件**：`scripts/verify_plugin_smoke.sh`
- **验收**：
  ```bash
  bash scripts/verify_plugin_smoke.sh bt_api_binance
  bash scripts/verify_plugin_smoke.sh bt_api_okx
  bash scripts/verify_plugin_smoke.sh bt_api_ctp
  ```

### [ ] T702 — 编写插件开发指南
- **优先级**：🟠 P1
- **复杂度**：M
- **负责人**：技术写作
- **依赖**：T403
- **文件**：`docs/plugins/plugin-development.md`
- **验收**：一位不参与本迭代的研发按文档能独立复现 hello-world 插件

### [ ] T703 — 编写用户迁移说明
- **优先级**：🟠 P1
- **复杂度**：S
- **负责人**：技术写作
- **依赖**：T403
- **文件**：`docs/migration/v2-migration.md`
- **验收**：README 首屏挂链接

### [ ] T704 — 更新 CHANGELOG.md
- **优先级**：🟠 P1
- **复杂度**：S
- **负责人**：技术经理
- **依赖**：所有 P0/P1 完成
- **验收**：CHANGELOG 迭代 01 条目完整

### [ ] T705 — 迭代分支合回 master
- **优先级**：🔴 P0
- **复杂度**：S
- **负责人**：技术经理
- **依赖**：所有 P0/P1 任务完成
- **验收**：
  - master 分支存在 `bt_api_base/`、`packages/bt_api_binance/` 等
  - CI 全绿
  - 至少 2 人 review

---

## 附录：验收检查清单

技术经理迭代结束前亲自执行：

- [ ] `git log --follow packages/bt_api_binance/src/bt_api_binance/feeds/` 能看到历史
- [ ] 干净 venv：`pip install bt_api_py` 只装核心
- [ ] 干净 venv：装 binance 插件后 Binance 能正常工作
- [ ] CI 中 `core-plugin-isolation` job 为绿
- [ ] CI 中 `plugin-smoke` job 为绿（binance/okx/ctp）
- [ ] `docs/plugins/plugin-development.md` 可独立读懂
- [ ] `CHANGELOG.md` 迭代 01 段落完整
- [ ] 所有 🔴 P0 / 🟠 P1 任务均为 `[x]`
