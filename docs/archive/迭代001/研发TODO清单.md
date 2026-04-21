# 迭代001 研发 TODO 清单

> 角色:技术经理交付给研发的可执行清单
> 前置文档:《完整迭代文档.md》、《技术评审与改进建议.md》、《研发设计文档.md》
> 使用方式:每项任务都是一个独立 PR 候选,按顺序领取,不要跳着做
> 格式约定:
> - `[ ]` 未开始 / `[~]` 进行中 / `[x]` 完成
> - 每项包含:任务 ID、优先级、预计复杂度 (S/M/L)、负责人、依赖、验收脚本
> - 不写"预估工时",让执行者自己评估

---

## 图例

| 标记 | 含义 |
|---|---|
| 🔴 P0 | 阻塞项,未完成不能开下一个任务 |
| 🟠 P1 | 本轮必达 |
| 🟡 P2 | 本轮 nice-to-have,允许延后 |
| ⚫ P3 | 不在本轮范围,列出仅供后续迭代参考 |
| S | Small:<300 行代码变更,当天能 PR |
| M | Medium:300~1000 行代码变更 |
| L | Large:>1000 行或涉及整目录搬运 |

---

## 阶段 0:前置 sign-off (阻塞后续所有)

### [ ] T000 — 《研发设计文档.md》正式评审 sign-off
- **优先级**:🔴 P0
- **复杂度**:S
- **负责人**:技术经理 + 架构师 + 两位研发代表
- **依赖**:无
- **产出**:在文档末尾加签字段,填入评审人 + 日期
- **验收**:至少 3 位评审人明确回复"同意按本设计执行"
- **备注**:未 sign-off 前,研发**不得**开始写代码

### [ ] T001 — 拉出迭代分支
- **优先级**:🔴 P0
- **复杂度**:S
- **依赖**:T000
- **指令**:
  ```bash
  git checkout master && git pull
  git checkout -b iter/001-plugin-system
  git push -u origin iter/001-plugin-system
  ```
- **验收**:远端分支存在,受保护分支规则生效 (需 review)

---

## 阶段 1:核心插件机制 (PR-01 ~ PR-04)

### [ ] T101 — 新增 `bt_api_py/plugins/` 子包骨架 (PR-01)
- **优先级**:🔴 P0
- **复杂度**:M
- **负责人**:核心研发
- **依赖**:T001
- **涉及文件**:
  - `bt_api_py/plugins/__init__.py` (新)
  - `bt_api_py/plugins/protocol.py` (新,定义 `PluginInfo`)
  - `bt_api_py/plugins/errors.py` (新,定义异常层级)
  - `bt_api_py/plugins/loader.py` (新,`PluginLoader` 核心逻辑)
- **实现要点**:
  - `PluginInfo` 用 `@dataclass(frozen=True)`,字段按《研发设计文档.md》§3.2
  - `PluginLoader.load_all()` 必须处理 6 种场景 (见设计文档 §3.6)
  - 版本兼容检查使用 `packaging.specifiers.SpecifierSet`
  - 日志 prefix 统一为 `[bt_api_py.plugins]`
- **测试要求**:
  - `tests/plugins/test_loader.py`:覆盖加载成功、ImportError、版本不兼容、重复注册、无插件、注册内部抛异常 6 条 case
  - 测试不依赖真实 entry_points,用 monkey-patch 注入 mock
- **验收脚本**:
  ```bash
  pytest tests/plugins/ -v
  python -c "from bt_api_py.plugins.loader import PluginLoader; print('OK')"
  ```

### [ ] T102 — 新增 `GatewayRuntimeRegistrar` (PR-02 的一部分)
- **优先级**:🔴 P0
- **复杂度**:S
- **负责人**:核心研发
- **依赖**:T101
- **涉及文件**:
  - `bt_api_py/gateway/registrar.py` (新)
- **实现要点**:
  - 按《研发设计文档.md》§3.4 的签名实现
  - 类级别字典,提供 register / get / list / clear (clear 仅供测试)
  - 重复注册同一 exchange_type 时:warn log,不覆盖
- **测试要求**:`tests/gateway/test_registrar.py` 覆盖所有方法

### [ ] T103 — 改造 `GatewayRuntime` 去硬编码 (PR-02 的主体)
- **优先级**:🔴 P0
- **复杂度**:M
- **负责人**:核心研发
- **依赖**:T102
- **涉及文件**:
  - `bt_api_py/gateway/runtime.py` (改)
- **关键变更**:
  - 删除 `ADAPTER_REGISTRY` 类属性 (第 33-39 行)
  - 删除顶部 `from bt_api_py.gateway.adapters import (Binance|Ctp|IbWeb|Mt5|Okx)GatewayAdapter` (第 11-17 行)
  - `_create_adapter` 改为通过 `GatewayRuntimeRegistrar.get_adapter()`
  - **保留** CTP `OrderRefAllocator` 的 if 分支 (第 62-70 行),本轮不动
- **过渡期方案**:
  - 在 `GatewayRuntime.__init__` 开头,临时手工调用一次 "把 5 个内置 adapter 注册到 Registrar",保证旧行为不破坏
  - 该临时调用用 TODO 注释标明"Binance/OKX 插件完成后需清理"
- **验收脚本**:
  ```bash
  pytest tests/gateway/ -v
  # 特别关注 test_runtime*.py 全部通过
  ```

### [ ] T104 — 清理 `bt_api_py/gateway/adapters/__init__.py` 的顶层 re-export
- **优先级**:🟠 P1
- **复杂度**:S
- **负责人**:核心研发
- **依赖**:T103
- **实现**:
  - 不再 `from .binance_adapter import ...`
  - 添加 `__getattr__` 钩子,对旧名字访问给出 DeprecationWarning
- **验收**:`grep -rn "from bt_api_py.gateway.adapters import" bt_api_py/` 不应再依赖顶层名字

### [ ] T105 — 把 `PluginLoader` 接入 `bt_api.py` 启动流程 (PR-03)
- **优先级**:🔴 P0
- **复杂度**:M
- **负责人**:核心研发
- **依赖**:T101, T102, T103
- **涉及文件**:`bt_api_py/bt_api.py`
- **实现要点**:
  - 在当前 `exchange_registers` 扫描代码之前,先调用 `PluginLoader(ExchangeRegistry, GatewayRuntimeRegistrar).load_all()`
  - 保留 legacy 扫描作为兜底
  - 加环境变量开关 `BT_API_DISABLE_LEGACY_SCAN=1` 可禁用 legacy
  - 未装任何插件时,主包导入 + 基本 API 调用不得报错
- **测试要求**:
  - `tests/plugins/test_bt_api_startup.py`:验证"没有任何插件"时 `BtApi()` 仍可构造
  - 验证 legacy 开关能正确关闭扫描
- **验收脚本**:
  ```bash
  BT_API_DISABLE_LEGACY_SCAN=1 python -c "from bt_api_py.bt_api import BtApi; print('no-plugin OK')"
  ```

### [ ] T106 — 清理 `feeds/registry.py:142-151` 硬编码 import
- **优先级**:🟠 P1
- **复杂度**:S
- **负责人**:核心研发
- **依赖**:T105
- **实现**:
  - 把 `initialize_default_feeds()` 内的 3 行硬编码 import 包 try/except
  - 注释说明:这是 legacy,将在插件系统成熟后删除
- **验收**:主包只装核心时,无 `ImportError`

### [ ] T107 — CI 静态检查:核心包禁止 import 插件 (PR-04)
- **优先级**:🔴 P0
- **复杂度**:S
- **负责人**:DevOps + 核心研发
- **依赖**:T101
- **涉及文件**:
  - `scripts/check_core_isolation.py` (新)
  - `.github/workflows/ci.yml` (改,新增一个 step)
- **实现**:按《研发设计文档.md》§7.1
- **验证手法**:在某个分支故意加一行 `from bt_api_binance import foo`,确认 CI red;然后撤销。
- **验收**:CI 步骤名为 `core-plugin-isolation`,阻断 PR 合并

### [ ] T108 — 单元测试基础设施:插件测试公共 fixture
- **优先级**:🟠 P1
- **复杂度**:S
- **负责人**:核心研发
- **依赖**:T101
- **涉及文件**:
  - `bt_api_py/testing/__init__.py` (新,仅测试用,不对外发布)
  - `bt_api_py/testing/fixtures.py` (新,queue stub / event_bus stub / registry isolation)
- **目的**:让插件包的 tests 能 `from bt_api_py.testing import ...`,不用各自重写 mock

---

## 阶段 2:`bt_api_binance` 插件拆分 (PR-05 ~ PR-09)

### [ ] T201 — 创建 `packages/bt_api_binance/` 目录骨架 (PR-05)
- **优先级**:🔴 P0
- **复杂度**:S
- **负责人**:Binance 研发
- **依赖**:T107
- **涉及文件**:
  - `packages/bt_api_binance/pyproject.toml`
  - `packages/bt_api_binance/README.md`
  - `packages/bt_api_binance/src/bt_api_binance/__init__.py` (定义 `__version__ = "2.0.0"`)
  - `packages/bt_api_binance/src/bt_api_binance/plugin.py` (空壳:仅返回 PluginInfo,不做真实注册)
  - `packages/bt_api_binance/tests/test_plugin_registration.py` (验证空壳能被加载)
- **实现**:
  - `pyproject.toml` 按《研发设计文档.md》§4.3
  - `plugin.py` 先写成 "no-op 注册",验证 entry_points 机制
- **验收脚本**:
  ```bash
  pip install -e packages/bt_api_binance
  python -c "
  from bt_api_py.plugins.loader import PluginLoader
  from bt_api_py.registry import ExchangeRegistry
  from bt_api_py.gateway.registrar import GatewayRuntimeRegistrar
  loader = PluginLoader(ExchangeRegistry, GatewayRuntimeRegistrar)
  loader.load_all()
  assert 'bt_api_binance' in loader.loaded
  print('empty-plugin OK')
  "
  ```

### [ ] T202 — 迁移 Binance Gateway Adapter (PR-06)
- **优先级**:🔴 P0
- **复杂度**:M
- **负责人**:Binance 研发
- **依赖**:T201
- **迁移操作**:
  ```bash
  git mv bt_api_py/gateway/adapters/binance_adapter.py \
         packages/bt_api_binance/src/bt_api_binance/gateway/adapter.py
  ```
- **后续处理**:
  - 删除 `bt_api_py/gateway/adapters/__init__.py` 中对 `BinanceGatewayAdapter` 的引用
  - 在 `plugin.py` 里 `GatewayRuntimeRegistrar.register_adapter("BINANCE", BinanceGatewayAdapter)`
  - 删除 T103 中遗留的"临时手工注册 Binance adapter"代码
- **验收**:
  ```bash
  grep -n "binance_adapter" bt_api_py/  # 必须为空
  pytest packages/bt_api_binance/tests/ -v
  ```

### [ ] T203 — 迁移 Binance Feeds 整目录 (PR-07)
- **优先级**:🔴 P0
- **复杂度**:L
- **负责人**:Binance 研发
- **依赖**:T202
- **⚠️ 关键**:用 `git mv`,**不要** `cp + rm`,保留 git 历史
- **迁移操作**:
  ```bash
  git mv bt_api_py/feeds/live_binance \
         packages/bt_api_binance/src/bt_api_binance/feeds
  ```
- **兼容处理**:
  - `bt_api_py/feeds/live_binance_feed.py` 改写为 shim (1 版本过渡):
    ```python
    import warnings
    warnings.warn(
        "bt_api_py.feeds.live_binance_feed is deprecated. "
        "Install bt_api_binance and use bt_api_binance.feeds instead.",
        DeprecationWarning, stacklevel=2,
    )
    from bt_api_binance.feeds import *  # noqa
    ```
  - 注意:shim 依赖插件已安装,写入 README 的迁移说明
- **内部 import 修复**:
  - `request_base.py` 等文件里若有 `from bt_api_py.feeds.live_binance.xxx`,改为相对 import 或 `from bt_api_binance.feeds.xxx`
  - 对 `from bt_api_py.containers...` / `from bt_api_py.balance_utils` 等 **核心包** 的 import 保持不变
- **验收**:
  ```bash
  pytest packages/bt_api_binance/tests/feeds/ -v
  # 以及主仓核心 CI 全绿
  ```

### [ ] T204 — 迁移 Binance Exchange Data 容器 (PR-08 一部分)
- **优先级**:🔴 P0
- **复杂度**:S
- **负责人**:Binance 研发
- **依赖**:T203
- **迁移操作**:
  ```bash
  git mv bt_api_py/containers/exchanges/binance_exchange_data.py \
         packages/bt_api_binance/src/bt_api_binance/exchange_data/binance.py
  ```
- **兼容处理**:
  - 在 `bt_api_py/containers/exchanges/binance_exchange_data.py` 保留 **re-export shim**,带 deprecation warning
- **验收**:主包内其他容器测试全绿,插件包内可正常导入

### [ ] T205 — 迁移 Binance 测试 (PR-08 另一部分)
- **优先级**:🟠 P1
- **复杂度**:M
- **负责人**:Binance 研发
- **依赖**:T204
- **迁移操作**:
  ```bash
  git mv tests/test_binance_balance.py \
         packages/bt_api_binance/tests/test_balance.py
  git mv tests/feeds/live_binance \
         packages/bt_api_binance/tests/feeds
  # 可能还有 tests/feeds/live_binance_feed* 若存在
  ```
- **注意**:
  - 迁移后的测试路径里若有 `from bt_api_py.feeds.live_binance...` 要改为 `from bt_api_binance.feeds...`
  - 把集成测试挑出来放到 `packages/bt_api_binance/tests/integration/`
  - 必须执行《研发设计文档.md》§8.5 的硬性约束,不得有任何"先跑通再说"豁免
- **迁移覆盖面**(须全部搬走):
  - `tests/test_binance*.py` (所有以 binance 开头的测试文件)
  - `tests/feeds/live_binance/` (整目录)
  - `tests/integration/*binance*`
  - `tests/conftest.py` 中任何 binance 专用 fixture → 移到 `packages/bt_api_binance/tests/conftest.py`
- **验收(三条 AND)**:
  ```bash
  # 1. 主仓 tests/ 不得残留任何 binance 文件
  find tests/ -iname "*binance*" -type f  # 必须空

  # 2. 主仓 tests/ 不得 import live_binance
  grep -rnE "bt_api_py\.feeds\.live_binance\b" tests/  # 必须空

  # 3. pytest collect 不得发现 binance 用例
  pytest tests/ -k binance --collect-only 2>&1 | grep -q "collected 0"  # 必须命中

  # 4. 插件包测试独立可运行
  cd packages/bt_api_binance && pytest tests/ -v  # 全绿
  ```

### [ ] T206 — 完成 `plugin.py` 真实注册逻辑 + 删除 `register_binance.py` (PR-09)
- **优先级**:🔴 P0
- **复杂度**:M
- **负责人**:Binance 研发
- **依赖**:T201, T202, T203, T204
- **实现**:
  - `plugin.py` 按《研发设计文档.md》§4.2 完成,把 `register_binance.py` 的逻辑全部挪过来
  - 删除 `bt_api_py/exchange_registers/register_binance.py`
  - 或改为空文件 + 注释:"moved to bt_api_binance.plugin"
- **最终验收**:
  ```bash
  # 干净 venv 端到端冒烟
  bash scripts/verify_plugin_smoke.sh bt_api_binance
  ```
- **冒烟脚本产出**:见阶段 4 T401

### [ ] T207 — 删除 T103 中的"临时手工注册 Binance adapter"代码
- **优先级**:🔴 P0
- **复杂度**:S
- **负责人**:核心研发
- **依赖**:T206
- **验证**:清理完后,T105 的 no-plugin 测试仍绿,装了 binance 插件后 Binance 能正常工作

---

## 阶段 3:`bt_api_okx` 插件拆分 (PR-10 ~ PR-13)

> **策略**:等阶段 2 完整跑通后再开始,把 Binance 当作"金标样板"复制,大幅降低返工。

### [ ] T301 — 创建 `packages/bt_api_okx/` 目录骨架 (PR-10)
- **优先级**:🟠 P1
- **复杂度**:S
- **负责人**:OKX 研发
- **依赖**:T206 (即 Binance 插件已完整拆完)
- **操作**:把 `packages/bt_api_binance/` 复制一份作为模板,替换名字
- **验收**:空壳能被 PluginLoader 加载

### [ ] T302 — 迁移 OKX Gateway Adapter (PR-11)
- **优先级**:🟠 P1
- **复杂度**:M
- **负责人**:OKX 研发
- **依赖**:T301
- **操作**:与 T202 同构
- **验收**:`grep -n "okx_adapter" bt_api_py/` 为空

### [ ] T303 — 迁移 OKX Feeds (PR-12)
- **优先级**:🟠 P1
- **复杂度**:L
- **负责人**:OKX 研发
- **依赖**:T302
- **注意**:OKX feeds 目录约 2 万行,用 `git mv` 单次完成
- **验收**:同 T203

### [ ] T304 — 迁移 OKX Exchange Data + 测试 (PR-13 一部分)
- **优先级**:🟠 P1
- **复杂度**:M
- **负责人**:OKX 研发
- **依赖**:T303
- **操作**:同 T204 + T205

### [ ] T305 — 完成 OKX `plugin.py` + 删除 `register_okx.py` (PR-13 收尾)
- **优先级**:🟠 P1
- **复杂度**:M
- **负责人**:OKX 研发
- **依赖**:T304
- **验收**:`bash scripts/verify_plugin_smoke.sh bt_api_okx`

### [ ] T306 — 删除临时"手工注册 OKX adapter"代码
- **优先级**:🟠 P1
- **复杂度**:S
- **依赖**:T305

---

## 阶段 4:验收与收尾 (PR-14 ~ PR-15)

### [ ] T401 — 产出通用冒烟脚本 `scripts/verify_plugin_smoke.sh`
- **优先级**:🔴 P0
- **复杂度**:S
- **负责人**:DevOps
- **依赖**:T105
- **内容**:按《研发设计文档.md》§7.2,接受插件名参数
- **验收**:
  ```bash
  bash scripts/verify_plugin_smoke.sh bt_api_binance
  bash scripts/verify_plugin_smoke.sh bt_api_okx
  ```
  两次都必须 exit 0

### [ ] T402 — 在 CI 中接入插件冒烟测试
- **优先级**:🟠 P1
- **复杂度**:S
- **负责人**:DevOps
- **依赖**:T401
- **实现**:`.github/workflows/ci.yml` 新增 job `plugin-smoke`,分别跑 binance / okx
- **验收**:CI 页面能看到两个 smoke job 的绿勾

### [ ] T403 — 编写《插件开发指南》(PR-14 一部分)
- **优先级**:🟠 P1
- **复杂度**:M
- **负责人**:技术写作 + 架构师
- **依赖**:T305
- **文件**:`docs/plugins/plugin-development.md`
- **内容必须包含**:
  - 插件契约 (复制《研发设计文档.md》§3)
  - 目录骨架样板
  - `plugin.py` 最小示例
  - 如何本地安装调试 (`pip install -e`)
  - 常见坑 (import 循环、entry_points 未生效、版本不兼容)
- **验收**:一位不参与本迭代的研发按文档能独立复现 hello-world 插件

### [ ] T404 — 编写《用户迁移说明》(PR-14 一部分)
- **优先级**:🟠 P1
- **复杂度**:S
- **负责人**:技术写作
- **文件**:`docs/plugins/user-migration.md`
- **内容**:
  - 从旧版本升级的步骤 (就是 `pip install bt_api_binance`)
  - 行为变更清单 (deprecation warning 的文本)
  - 回滚方法 (`pip uninstall bt_api_binance` 后用 `BT_API_DISABLE_LEGACY_SCAN=0`)
- **验收**:README 首屏挂链接

### [ ] T405 — 更新 `CHANGELOG.md`
- **优先级**:🟠 P1
- **复杂度**:S
- **负责人**:技术经理
- **依赖**:T305
- **内容**:迭代 001 条目,列出所有对外可见变化

### [ ] T406 — 性能基线比对
- **优先级**:🟡 P2
- **复杂度**:S
- **负责人**:核心研发
- **依赖**:T305
- **内容**:
  - 对比 `import bt_api_py` 的耗时 (迭代前 vs 迭代后)
  - 对比 `BtApi({"exchange":"BINANCE___SPOT"})` 构造耗时
  - 记录到 `docs/archive/迭代001/性能基线报告.md`
- **验收**:耗时不超过基线的 1.2 倍 (见《技术评审与改进建议》§6 指标 2)

### [ ] T407 — 把插件包发到 test-pypi 做端到端验证
- **优先级**:🟡 P2
- **复杂度**:M
- **负责人**:DevOps
- **依赖**:T305
- **操作**:
  ```bash
  # 为 bt_api_binance / bt_api_okx 各构建 wheel
  cd packages/bt_api_binance && python -m build
  twine upload --repository testpypi dist/*
  # 干净 venv 里验证
  pip install -i https://test.pypi.org/simple/ bt_api_binance
  ```
- **验收**:从 test-pypi 装的插件能被核心发现并注册

### [ ] T408 — 迭代分支合回 master (PR-15)
- **优先级**:🔴 P0
- **复杂度**:S
- **负责人**:技术经理
- **依赖**:所有 P0 / P1 任务完成
- **操作**:提 PR,全量 CI 绿,至少 2 人 review,merge
- **验收**:master 分支上存在 `packages/bt_api_binance`、`packages/bt_api_okx`、`bt_api_py/plugins/` 三个关键路径

---

## 阶段 5:后续迭代的完整拆包路线图 (⚫ P3,不在本轮)

> **说明**:本仓库实际有 **72 个交易所 feed + 71 个 register 文件**,迭代 001 只覆盖 `binance` / `okx`。其余 70 个交易所及相关工程任务在此列出,按**拆包波次 (Wave)** 组织,每个波次对应一次独立迭代。
> 编号规则:`T9WXX`,`W` 是波次号 (2~6),`XX` 是序号。

### 5.0 通用原则(所有波次都适用)🔒

以下原则对迭代 001 / Wave 2~7 **全部波次**都强制生效,不得以"时间紧张""先跑通再说"等理由豁免:

#### 原则 1 — 测试必须跟随被测代码一起迁移

> **核心约束**:某个交易所的代码被迁移到插件包或被删除时,**其对应的测试必须同时迁走或同时删除**。主仓的 `tests/` 目录在拆包/删除动作完成后,**不得保留**任何对该交易所的 import 或测试文件。

**具体要求**:

| 场景 | 代码处理 | 测试处理(必须同步) |
|---|---|---|
| 拆分为插件 (Binance/OKX/Wave 2~5 的大部分) | `git mv bt_api_py/feeds/live_<x>/` → `packages/bt_api_<x>/src/bt_api_<x>/feeds/` | `git mv tests/feeds/live_<x>/` → `packages/bt_api_<x>/tests/feeds/` <br> `git mv tests/test_<x>_*.py` → `packages/bt_api_<x>/tests/` <br> `git mv tests/integration/*<x>*` → `packages/bt_api_<x>/tests/integration/` |
| 直接删除 (Wave 6 Tier C) | `git rm -r bt_api_py/feeds/live_<x>/` | `git rm -r tests/feeds/live_<x>/` <br> `git rm tests/test_<x>_*.py` <br> `git rm tests/integration/*<x>*` |

**验收检查**:每个拆包 / 删除 PR 合并前,必须通过:

```bash
# 主仓 tests/ 不得残留被迁/被删交易所的任何引用
grep -rnE "(bt_api_py\.feeds\.live_<x>|test_<x>_)" tests/  # 必须为 0 条
pytest tests/ -k "<x>" --collect-only                       # 必须无输出
```

**兜底规则**:若某个测试文件同时覆盖多个交易所(跨交易所的对比测试),优先策略是**拆成多个单交易所测试文件**;实在无法拆的,保留在 `tests/cross_exchange/`,并在 PR 描述中明确说明。

#### 原则 2 — 迁移一律用 `git mv`,保留历史

- `git mv` 可保留 git 历史,`git log --follow <file>` 可追溯。
- 删除一律用 `git rm`,不留 shim、不留 re-export。
- 严禁 `cp + rm` 组合,否则 blame 历史断裂。

#### 原则 3 — 每个波次结束时,主仓测试覆盖率**不应退化**

- 拆包将主仓测试基数降低,但按覆盖率应保持不变或提升(因为主仓只剩核心,覆盖率应当上升)。
- 每个波次 PR 最终合并时,必须附上主仓 `pytest --cov=bt_api_py` 报告,和本波次开始时对比,不得出现核心模块覆盖率下降。

#### 原则 4 — 插件包的测试独立可运行

- 每个 `packages/bt_api_<x>/` 必须能 `cd packages/bt_api_<x> && pytest tests/ -v` 独立跑通。
- 测试依赖的 fixture / mock 通过 `bt_api_py.testing`(T108 产出)或 `bt_api_testkit`(T9906 产出)提供,不得反向引用主仓 `tests/` 的任何内容。

---

### 5.1 交易所总盘点 (72 个)

| 类别 | 数量 | 清单 |
|---|---:|---|
| Tier A — 头部 CEX (高流量) | 17 | binance ✅, okx ✅, bybit, bitget, kucoin, htx, mexc, gateio, coinbase, kraken, cryptocom, bingx, hyperliquid, phemex, bitfinex, bitmart, gemini |
| Tier B — 中型 CEX | 14 | bitstamp, bitunix, bydfi, bitrue, coinex, hitbtc, poloniex, upbit, bithumb, bitflyer, ascendex*, aivora*, allinx*, 4e* |
| Tier C — 区域/小型 CEX | 32 | bigone, bitbank, bitbns, bitinka, bitso, bitvavo, btc_markets, btcturk, buda, coincheck, coindcx, coinone, coinspot, coinswitch, exmo, foxbit, giottus, independent_reserve, korbit, latoken, localbitcoins, luno, mercado_bitcoin, ripio, satoshitango, swyftx, valr, wazirx, yobit, zaif, zebpay, bequant |
| Tier D — DEX (Web3 依赖) | 9 | uniswap, sushiswap, pancakeswap, curve, dydx, gmx, cow_swap, raydium, balancer |
| Tier E — 特殊环境依赖 | 4 | ctp (C++ 原生扩展), ib (ib-insync), ib_web (playwright), mt5 (本地 MT5 终端) |

> **`*` 标记**:ascendex / aivora / allinx / 4e 在 `feeds/` 目录下存在但**没有**对应 register 文件,需要在 Wave 2 规划阶段先补齐或标记为"已废弃"。

### 5.2 拆包波次规划

| 波次 | 迭代编号建议 | 覆盖 Tier | 交易所数 | 主要目标 |
|---|---|---|---:|---|
| Wave 2 | 迭代 002 | Tier A 剩余头部 | 17 | 把 90% 日常用户覆盖到 |
| Wave 3 | 迭代 003 | Tier B 中型 CEX | 14 | 完成纯 REST/WSS 拆分收尾 |
| Wave 4 | 迭代 004 | Tier D DEX + Web3 | 9 | 处理 web3.py 依赖 |
| Wave 5 | 迭代 005 | Tier E 特殊依赖 | 4 | 处理原生扩展、浏览器、本地终端 |
| **Wave 6** | **迭代 006** | **Tier C 区域小型(全部废弃)** | **32** | **直接删除代码 + 测试,不拆包** ⚠️ |
| Wave 7 | 迭代 007 | 主包收尾 | — | 删除所有 legacy,主包仅保留核心框架 |

> ⚠️ **Wave 6 决策变更(2026-04-16)**:经评审决定,Tier C 的 32 个区域/小型交易所 **不再拆包,改为直接从仓库删除**。理由、清单、操作步骤见下文 §5.7。

### 5.3 Wave 2 — Tier A 头部 CEX (迭代 002)

> 前置:迭代 001 全部 P0/P1 完成,Binance 插件在 test-pypi 可用。
> 复用:Binance/OKX 样板 + plugin.py 模板,每个插件的工程开销比迭代 001 低 60%。

| TID | 插件名 | Feed 目录 | 复杂度 | 依赖特殊性 |
|---|---|---|---:|---|
| [ ] T9201 | `bt_api_bybit` | `feeds/live_bybit/` | M | 纯 REST/WSS |
| [ ] T9202 | `bt_api_bitget` | `feeds/live_bitget/` | M | 纯 REST/WSS |
| [ ] T9203 | `bt_api_kucoin` | `feeds/live_kucoin/` | M | WSS 需 token 鉴权 |
| [ ] T9204 | `bt_api_htx` | `feeds/live_htx/` | M | 原火币,数据结构独特 |
| [ ] T9205 | `bt_api_mexc` | `feeds/live_mexc/` + `live_mexc_feed.py` | M | 有 wrapper 文件要一并处理 |
| [ ] T9206 | `bt_api_gateio` | `feeds/live_gateio/` | M | 纯 REST/WSS |
| [ ] T9207 | `bt_api_coinbase` | `feeds/live_coinbase/` | M | 鉴权走 JWT/ECDSA,需核对依赖 |
| [ ] T9208 | `bt_api_kraken` | `feeds/live_kraken/` | M | 纯 REST/WSS |
| [ ] T9209 | `bt_api_cryptocom` | `feeds/live_cryptocom/` | M | 纯 REST/WSS |
| [ ] T9210 | `bt_api_bingx` | `feeds/live_bingx/` | S-M | 较新,量小 |
| [ ] T9211 | `bt_api_hyperliquid` | `feeds/live_hyperliquid/` + `live_hyperliquid_feed.py` | M | Perp DEX,含 wrapper |
| [ ] T9212 | `bt_api_phemex` | `feeds/live_phemex/` | S-M | 较小 |
| [ ] T9213 | `bt_api_bitfinex` | `feeds/live_bitfinex/` | M | WSS 消息格式独特 |
| [ ] T9214 | `bt_api_bitmart` | `feeds/live_bitmart/` | M | 纯 REST/WSS |
| [ ] T9215 | `bt_api_gemini` | `feeds/live_gemini/` | S-M | 美国监管链路 |
| [ ] T9216 | `bt_api_bitstamp` | `feeds/live_bitstamp/` | S-M | 欧洲老牌 |
| [ ] T9217 | Wave 2 收尾:主包移除 17 个 register 文件 + 对应 container + **对应所有测试目录/文件** | S | 必须跑 §5.0 原则 1 的验收 grep |

**每个 T92xx 任务(T9201~T9216)的标准迁移范围**(按 §5.0 原则 1):

```
源位置(主仓)                                    → 目标位置(插件包 packages/bt_api_<x>/)
bt_api_py/feeds/live_<x>/                        → src/bt_api_<x>/feeds/
bt_api_py/feeds/live_<x>_feed.py (如有)          → src/bt_api_<x>/feeds/__init__.py re-export
bt_api_py/exchange_registers/register_<x>.py     → (删除,逻辑并入 plugin.py)
bt_api_py/containers/exchanges/<x>_exchange_data.py → src/bt_api_<x>/exchange_data/<x>.py
bt_api_py/gateway/adapters/<x>_adapter.py (如有) → src/bt_api_<x>/gateway/adapter.py
tests/feeds/live_<x>/                            → tests/feeds/  ⚠️ 测试必须跟走
tests/test_<x>_*.py                              → tests/       ⚠️ 测试必须跟走
tests/integration/*<x>*                          → tests/integration/ ⚠️ 测试必须跟走
```

**Wave 2 成功标准**:
1. 用户一条命令 `pip install bt_api_py bt_api_bybit bt_api_kucoin` 即可获得主流 CEX 覆盖,无需再 pull 主仓。
2. 主仓 `grep -rnE "bybit|bitget|kucoin|htx|mexc|gateio|coinbase|kraken|cryptocom|bingx|hyperliquid|phemex|bitfinex|bitmart|gemini|bitstamp" tests/` 返回 0 条。

### 5.4 Wave 3 — Tier B 中型 CEX (迭代 003)

| TID | 插件名 | 备注 |
|---|---|---|
| [ ] T9301 | `bt_api_bitunix` | |
| [ ] T9302 | `bt_api_bydfi` | |
| [ ] T9303 | `bt_api_bitrue` | |
| [ ] T9304 | `bt_api_coinex` | |
| [ ] T9305 | `bt_api_hitbtc` | **注意**:`feeds/registry.py:145` 仍硬编码 import 此交易所,迁移时要一并修复 |
| [ ] T9306 | `bt_api_poloniex` | |
| [ ] T9307 | `bt_api_upbit` | 韩国 |
| [ ] T9308 | `bt_api_bithumb` | 韩国 |
| [ ] T9309 | `bt_api_bitflyer` | 日本 |
| [ ] T9310 | `bt_api_ascendex` | ⚠️ 缺 register 文件,先补齐再拆 |
| [ ] T9311 | `bt_api_aivora` | ⚠️ 同上,需先确认是否有效交易所 |
| [ ] T9312 | `bt_api_allinx` | ⚠️ 同上 |
| [ ] T9313 | `bt_api_4e` | ⚠️ 同上 |
| [ ] T9314 | Wave 3 收尾:清理主仓残留 + 验收测试已全部迁走 | |

**Wave 3 迁移范围**:与 Wave 2 相同,每个 T93xx 任务必须按 §5.0 原则 1 同步搬运测试(`tests/feeds/live_<x>/`、`tests/test_<x>_*.py`、`tests/integration/*<x>*`),否则 PR 不予合并。

**Wave 3 前置任务**:产品先审核 ascendex/aivora/allinx/4e 是否仍维护,不维护的**直接按 Wave 6 规则删除**(含代码与测试),不拆包。

### 5.5 Wave 4 — Tier D DEX + Web3 (迭代 004)

> 这一批需要 **web3.py / solana-py / EVM SDK** 等依赖,是一个**新可选依赖组**的引入点。

| TID | 插件名 | 链 | 依赖特殊性 |
|---|---|---|---|
| [ ] T9401 | `bt_api_uniswap` | EVM | web3.py,多版本 ABI 维护 |
| [ ] T9402 | `bt_api_sushiswap` | EVM | 与 uniswap 共享大量逻辑,可考虑共用基类 |
| [ ] T9403 | `bt_api_pancakeswap` | BSC | web3.py |
| [ ] T9404 | `bt_api_curve` | EVM | 特殊 AMM 数学 |
| [ ] T9405 | `bt_api_dydx` | EVM/Starkware | v3 vs v4 协议差异大,需研究产品决策保留哪个版本 |
| [ ] T9406 | `bt_api_gmx` | Arbitrum/Avalanche | 含 keeper 依赖 |
| [ ] T9407 | `bt_api_cow_swap` | EVM | 批次拍卖机制 |
| [ ] T9408 | `bt_api_raydium` | Solana | 引入 solana-py 新依赖栈 |
| [ ] T9409 | `bt_api_balancer` | EVM | |
| [ ] T9410 | 引入 `web3` 可选依赖组 | 前置任务,可在 T9401 之前独立 PR | 改 `pyproject.toml` 和文档 |
| [ ] T9411 | 抽取 `bt_api_web3_common` 共享库 | 可选 | 若 EVM 插件重复代码过多可抽基类包 |

**Wave 4 迁移范围**:与 Wave 2 相同,每个 T94xx 任务必须按 §5.0 原则 1 同步搬运测试。

**Wave 4 风险**:`uniswap_pool.py`、`uniswap_quote.py`、`uniswap_ticker.py` 散落在 `containers/exchanges/` 下,不是简单的 `*_exchange_data.py` 模式,迁移映射表需单独设计。

### 5.6 Wave 5 — Tier E 特殊依赖 (迭代 005) 🚨 高风险

> 这一批每个都需要**单独 PoC 验证**,不可与其他波次并行。

#### [ ] T9501 — `bt_api_ctp` (原生扩展,最复杂)
- **优先级**:🔴 P0 (战略上最需要)
- **复杂度**:XL
- **负责人**:需一位懂 SWIG / C++ / 多平台打包的资深研发
- **涉及改造**:
  - 把 `setup.py:176-185` 的 CTP 扩展构建逻辑整体迁到 `packages/bt_api_ctp/setup.py`
  - 把 `setup.py:230-277` 的 `_BuildExt` 和 `_copy_ctp_runtime_libs` 迁过去
  - 迁移 `bt_api_py/ctp/_ctp.pyd` (Windows) / `_ctp.so` (Linux) / `_ctp.cpython-*.so` (macOS)
  - 迁移 `bt_api_py/ctp/ctp_wrap.cpp` (19.8 MB SWIG 生成)
  - 迁移 `bt_api_py/ctp/ctp_structs_*.py`
  - 从 `gateway/runtime.py:62-70` 抽出 `OrderRefAllocator` 逻辑到 `bt_api_ctp/state_manager.py`
  - 处理 CTP 6.7.7 版本与未来升级路径,编写 `scripts/regenerate_ctp_bindings.sh`
- **跨平台验证矩阵**:
  - macOS x64 / arm64
  - Linux x64 (manylinux2014 / manylinux_2_28)
  - Windows x64
- **发布**:每平台单独 wheel,约 6 个 wheel 文件
- **验收**:
  - [ ] 在 CI 的 3 个平台分别 `pip install bt_api_ctp` 成功
  - [ ] CTP 用户 `pip install bt_api_py` 不再需要本地 C++ 编译器
  - [ ] 主包 `pip install .` 的 wheel 体积下降至少 15 MB

#### [ ] T9502 — `bt_api_ib_web` (Playwright 浏览器自动化)
- **优先级**:🟠 P1
- **复杂度**:L
- **涉及**:
  - `feeds/live_ib_web_feed.py` (852 行)
  - `feeds/live_ib_web_stream.py` (419 行)
  - `functions/ib_web_session.py` (浏览器会话)
  - `containers/exchanges/ib_web_exchange_data.py`
  - `gateway/adapters/ib_web_adapter.py`
  - `exchange_registers/register_ib_web.py`
- **依赖迁移**:`pyproject.toml` 的 `ib_web` 可选依赖组迁到插件包 `dependencies`,核心包彻底不知道 Playwright
- **注意**:Playwright 需 `playwright install chromium` 后置步骤,必须在插件 README 显著位置告知

#### [ ] T9503 — `bt_api_mt5` (本地 MetaTrader5 终端)
- **优先级**:🟠 P1
- **复杂度**:M
- **涉及**:register_mt5.py / gateway/adapters/mt5_adapter.py / 任何 `pymt5` 引用
- **风险**:`pymt5` 是否仍在维护需先确认,若不维护考虑改用 `MetaTrader5` 官方包
- **注意**:MT5 只支持 Windows,插件包的 `pyproject.toml` 需加 `platform_system == "Windows"` 约束

#### [ ] T9504 — `bt_api_ib` (ib-insync, 相对简单)
- **优先级**:🟡 P2
- **复杂度**:S-M
- **涉及**:`containers/ib/`, `exchange_registers/register_ib.py`
- **依赖**:`ib-insync>=0.9.86` (已在 optional-dependencies)

**Wave 5 测试迁移**:CTP/IB/IB Web/MT5 的测试同样按 §5.0 原则 1 搬运。特别注意:
- CTP:`tests/test_ctp_*.py`、`tests/feeds/live_ctp*` 全部迁到 `packages/bt_api_ctp/tests/`
- IB Web:`tests/test_ib_web*`、`tests/feeds/live_ib_web*` 全部迁到 `packages/bt_api_ib_web/tests/`
- CTP 的跨平台构建测试 (macOS/Linux/Windows CI 矩阵) 也迁到插件包 CI,不留在主仓

### 5.7 Wave 6 — Tier C 区域小型 CEX:**全部删除**(迭代 006)

> **决策**(2026-04-16 评审敲定):Tier C 的 32 个区域/小型交易所**不再拆包,直接从仓库删除**。
>
> **理由**:
> 1. 评估后确认这批交易所**无活跃用户或已停服**,保留纯粹是历史技术债;
> 2. 每拆一个插件即使走脚手架也有持续维护成本(PyPI 发布、版本兼容、CI 绿化),总开销不成比例;
> 3. 主包体积与 CI 时间因此长期被拖累;
> 4. 若未来确有需求,由用户/贡献者基于 Wave 2 的插件模板重新实现,比现在勉强拆包更健康。

#### 5.7.1 删除清单(32 个)

> **⚠️ 操作一律用 `git rm`,不要留 shim、不留 re-export、不留 deprecation warning。**

| 批次 | 交易所 | 代码位置(全部删除) |
|---|---|---|
| [ ] T9601 亚洲 (16) | bitbank, bitbns, coincheck, coinone, coindcx, giottus, korbit, wazirx, yobit, zaif, zebpay, swyftx, coinspot, coinswitch, independent_reserve, luno | `bt_api_py/feeds/live_<name>/`, `bt_api_py/exchange_registers/register_<name>.py`, `bt_api_py/containers/exchanges/<name>_exchange_data.py`, `tests/feeds/live_<name>/`,以及任何 `tests/test_<name>_*.py` |
| [ ] T9602 欧洲 (4) | bitvavo, btcturk, bequant, exmo | 同上 |
| [ ] T9603 美洲 (11) | bitinka, bitso, buda, btc_markets, foxbit, latoken, localbitcoins, mercado_bitcoin, ripio, satoshitango, valr | 同上 |
| [ ] T9604 其他 (1) | bigone | 同上 |

#### 5.7.2 删除操作清单(每个交易所必须执行)

以 `bitbank` 为例,每个删除任务必须执行完整的 9 项清理:

```bash
# 1. 删 feed 目录或文件
git rm -r bt_api_py/feeds/live_bitbank

# 2. 删 register 文件
git rm bt_api_py/exchange_registers/register_bitbank.py

# 3. 删 exchange_data 容器
git rm bt_api_py/containers/exchanges/bitbank_exchange_data.py

# 4. 删 gateway adapter(若存在)
git rm bt_api_py/gateway/adapters/bitbank_adapter.py 2>/dev/null || true

# 5. 删测试目录 / 测试文件
git rm -r tests/feeds/live_bitbank 2>/dev/null || true
git rm tests/test_bitbank*.py 2>/dev/null || true

# 6. 删文档中任何对该交易所的示例
grep -rln "bitbank\|BITBANK" docs/ && # 人工评估后逐条处理

# 7. 检查核心包内是否还有任何 bitbank 引用
grep -rnE "bitbank|BITBANK" bt_api_py/  # 必须为空

# 8. 跑一次核心 CI 确认无 import 链断裂
pytest tests/ -x --ignore-glob='**/live_*'

# 9. 更新 CHANGELOG.md 的 "Removed" 段落
```

#### 5.7.3 验收标准

- [ ] 32 个交易所全部从 `bt_api_py/feeds/`、`bt_api_py/exchange_registers/`、`bt_api_py/containers/exchanges/`、`bt_api_py/gateway/adapters/`、`tests/` 中消失
- [ ] `grep -rnE "(bitbank|bitbns|coincheck|coinone|coindcx|giottus|korbit|wazirx|yobit|zaif|zebpay|swyftx|coinspot|coinswitch|independent_reserve|luno|bitvavo|btcturk|bequant|exmo|bitinka|bitso|buda|btc_markets|foxbit|latoken|localbitcoins|mercado_bitcoin|ripio|satoshitango|valr|bigone)" bt_api_py/ tests/ docs/` 返回 **0** 条(除 CHANGELOG 记录之外)
- [ ] 核心 CI 全绿
- [ ] `CHANGELOG.md` 在 "Breaking Changes / Removed" 段落完整列出 32 个被删除的交易所,并指明"如有需要请自行基于插件模板重新实现"
- [ ] README / 文档中的交易所列表同步更新

#### 5.7.4 沟通与回滚

- **沟通**:Wave 6 发布前至少 2 个版本通过 DeprecationWarning 告知用户("以下交易所将在 3.0 删除");发布 release note 单独列出删除清单
- **回滚预案**:删除操作全部通过 `git rm` 完成,commit 粒度按批次(T9601~T9604),任何批次出问题可用 `git revert <commit>` 恢复
- **用户应对**:在《用户迁移说明》里新增一节 "Removed Exchanges",给出"如果你仍需使用 XXX"的替代方案(例如使用该交易所的原生 SDK,或参考插件模板自行实现)

### 5.8 Wave 7 — 主包最终收尾 (迭代 007)

- [ ] T9701 — 删除 `bt_api_py/exchange_registers/` 整个目录
- [ ] T9702 — 删除 `bt_api_py/feeds/live_*/` 所有剩余目录
- [ ] T9703 — 删除 `bt_api_py/gateway/adapters/` 除 `base.py` 外的所有 adapter
- [ ] T9704 — 删除 `bt_api_py/containers/exchanges/*_exchange_data.py` 全部文件
- [ ] T9705 — 删除 `bt_api_py/containers/ctp/` 和 `bt_api_py/containers/ib/` 子目录
- [ ] T9706 — 删除 `bt_api_py/ctp/` (已在 Wave 5 迁走)
- [ ] T9707 — 删除所有在前面波次留下的 re-export shim 与 DeprecationWarning
- [ ] T9708 — 删除 `BT_API_DISABLE_LEGACY_SCAN` 开关及 legacy 扫描代码
- [ ] T9709 — `setup.py` 精简为纯 Python 构建 (无 C++ 扩展)
- [ ] T9710 — `pyproject.toml` 移除所有交易所相关的 optional-dependencies
- [ ] T9711 — 主包 `bt_api_py` 发布 3.0.0 (重大版本)
- [ ] T9712 — 更新 `docs/` 整体架构图,从"多交易所单体"改为"核心 + 插件"
- [ ] T9713 — 迁移指南:从 bt_api_py 2.x 升级到 3.x 完整手册

### 5.9 跨波次的工程技术债任务 (独立 PR,可在任何波次间穿插)

#### 代码质量与工程化

| TID | 任务 | 说明 |
|---|---|---|
| [ ] T9901 | 拆分 `live_binance/request_base.py` (~9.4 万行) 的内部结构 | 本轮 `git mv` 整文件,后续按 market/account/order/position 拆 |
| [ ] T9902 | 拆分 `live_okx/` 超大文件 (~2 万行) | 同上 |
| [ ] T9903 | 统一 `containers/exchanges/` 下交易所特定容器基类 | 可能需要新的 `ExchangeData` 抽象基类 |
| [ ] T9904 | CI 从自定义 grep 升级为 `ruff` `banned-imports` 规则 | |
| [ ] T9905 | `feeds/registry.py` 整体废弃 | 把最后几处 legacy 引用清理 |
| [ ] T9906 | `bt_api_py/testing/` 提取为独立包 `bt_api_testkit` | 插件测试依赖 |
| [ ] T9907 | 建立单仓库 monorepo 工具链 (推荐 `uv` / `pdm workspace`) | 72 个插件若在同仓管理,需 workspace 管理 |
| [ ] T9908 | 插件包版本自动对齐脚本 | 当核心发新版本时,批量 bump 所有插件 |
| [ ] T9909 | 统一文档站点架构 | 每个插件包都要有独立 readthedocs 或子站 |
| [ ] T9910 | 建立"插件健康度看板" | 显示每个插件的测试覆盖率、最近更新时间、核心版本兼容范围 |

#### 发布与治理

| TID | 任务 | 说明 |
|---|---|---|
| [ ] T9920 | 决策:单仓 vs 多仓 | 72 个插件长期是继续放 `packages/` 还是拆独立 repo?评估 |
| [ ] T9921 | pypi 账户与发布权限治理 | 72 个包的 maintainer 权限规划 |
| [ ] T9922 | 建立 `bt_api_meta` 聚合包 | 一键安装所有插件:`pip install bt_api_meta` |
| [ ] T9923 | 插件模板仓库 | 第三方开发者想贡献新交易所插件时,fork 即开发 |
| [ ] T9924 | 签名与供应链安全 | sigstore / pypi attestations,防止插件投毒 |

#### 数据/测试

| TID | 任务 | 说明 |
|---|---|---|
| [ ] T9930 | 每个交易所的 "最小 mock fixture" 标准化 | 让插件 unit test 不依赖真实网络 |
| [ ] T9931 | 集成测试 testnet/sandbox 账户治理 | 72 个交易所的测试账户密钥管理 |
| [ ] T9932 | 跨插件的 ticker 数据一致性回归测试 | 订阅同一币对在不同插件上的字段格式一致性 |

### 5.10 工作量粗估参考

> 仅为粗估,便于排期,不是工时承诺。

| 波次 | 交易所数 | 平均单个复杂度 | 总体规模 |
|---|---:|---|---|
| 迭代 001 (当前) | 2 | M-L (样板开销) | 大 |
| Wave 2 | 17 | M | 大 |
| Wave 3 | 14 | S-M | 中 |
| Wave 4 | 9 | M-L (web3) | 中 |
| Wave 5 | 4 | L-XL (特殊依赖) | 大 |
| Wave 6 | 32 | XS (纯删除) | 小 |
| Wave 7 | — | — | 小 (纯清理) |

**关键观察**:
- Wave 5 (4 个特殊依赖) 与 Wave 2 (17 个头部 CEX) 的总体规模相当。CTP 一个插件的工作量可能超过 Wave 6 的 32 个小型 CEX 之和。
- Wave 6 改为直接删除后,总体规模从"中"降为"小",主要工作量在**沟通与文档**,而不是工程实现。

---

## 附录 A:每日 standup 建议检查项

研发每天 standup 时,至少回答:

1. 昨天推进了哪个 T-ID?
2. 今天计划推进哪个 T-ID?
3. 是否发现任何在《研发设计文档.md》§9 红线之外想"顺手做"的事? (如有,必须停手上报)
4. smoke 脚本 `verify_plugin_smoke.sh` 今天还能跑通吗?

---

## 附录 B:紧急回退预案

如果迭代中期出现严重阻塞 (如 Binance 插件无法被加载、主包安装失败),启动回退:

1. 立即冻结所有 T2xx / T3xx 任务
2. 核心研发确认 T101~T108 是否稳定;如稳定,保留核心插件机制代码
3. 把 Binance / OKX 的 `packages/` 目录移到 `packages-wip/`
4. 恢复 `exchange_registers/register_binance.py` 和 `register_okx.py` 的路径
5. 迭代 001 降级为"只交付核心插件机制,试点推迟到迭代 002"

回退判定责任人:技术经理。

---

## 附录 C:交付后的验证清单 (给技术经理)

迭代结束 release 前,亲自执行:

- [ ] `git log --follow packages/bt_api_binance/src/bt_api_binance/feeds/request_base.py` 能看到历史
- [ ] 干净 venv:`pip install .` 只装核心,`python -c "from bt_api_py.bt_api import BtApi"` 无异常、无 Binance 相关 ImportError
- [ ] 干净 venv:`pip install packages/bt_api_binance`,构造 `BtApi({"exchange":"BINANCE___SPOT"})` 成功
- [ ] CI 中 `core-plugin-isolation` job 为绿
- [ ] CI 中 `plugin-smoke` job 为绿
- [ ] `docs/plugins/plugin-development.md` 可独立读懂
- [ ] `CHANGELOG.md` 迭代 001 段落完整
- [ ] test-pypi 上 `bt_api_binance`、`bt_api_okx` 包已发布并安装验证
- [ ] 本文档所有 🔴 P0 / 🟠 P1 任务均为 `[x]`
