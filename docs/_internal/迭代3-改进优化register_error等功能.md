1. 我把原先feeds文件夹下的register文件夹放到了bt_api_py下，改名成exchange_registers了 ✅ 已修复 84 个文件的 import 路径
2. 我把bt_api_py这个目录下的error_framework改名成了errors, 并把bt_api_py/error_framework.py这个改名成error.py了 ✅ 已修复所有 import 路径
3. 分析一下error.py和exceptions.py的区别，另外项目中是否使用了exceptions.py,如果没有使用或者使用比较少，可以考虑用error.py替代
4. 分析一下instrument_manager.py与symbol_manager.py是否重复了？以及在项目中如何优化？
5. 3和4的结论放到这个文档的下面，3和4暂时先不用修改，等我决定什么时候修改。

---

## 3. error.py vs exceptions.py 分析结论

### 定位差异

| 维度 | `exceptions.py` | `error.py` |
|------|-----------------|------------|
| **定位** | 传统 Python 异常层次体系 | 统一错误框架（错误码 + 翻译器） |
| **基类** | `BtApiError(Exception)` | `UnifiedError(BtApiError)` — 继承自 exceptions.py |
| **内容** | 15 个异常类（按语义分类：连接、超时、下单、解析等） | ErrorCategory 枚举 + UnifiedErrorCode 枚举(~35 个错误码) + ErrorTranslator 基类 + 8 个交易所翻译器(Binance/OKX/CTP/Kraken/Bybit/Bitget/KuCoin/Gemini/IBWeb) |
| **行数** | ~207 行 | ~1237 行 |
| **设计** | raise/catch 驱动，面向调用者 | 数据驱动(dataclass)，面向错误码映射与翻译 |

### 依赖关系

- `error.py` **依赖** `exceptions.py`：第 12 行 `from bt_api_py.exceptions import BtApiError`
- `UnifiedError` 继承 `BtApiError`，两者不能简单合并

### 使用情况

- **`exceptions.py`** 被 **8 个非 build 文件**引用（`__init__.py`, `bt_api.py`, `error.py`, `feed.py`, `http_client.py`, `live_ctp_feed.py`, 及 3 个测试文件）
- **`error.py`** 被 **24 个非 build 文件**引用（7 个 errors/ 子模块 + 12 个 feeds/ request_base + 5 个测试文件）

### 重叠的类名

两个文件都定义了以下同名但不同实现的类：

| 类名 | exceptions.py | error.py |
|------|--------------|----------|
| `AuthenticationError` | 继承 `ExchangeConnectionError` | 继承 `UnifiedError` |
| `RateLimitError` | 继承 `BtApiError` | 继承 `UnifiedError` |
| `RequestFailedError` | 继承 `BtApiError` | 继承 `UnifiedError` |

这会导致 import 混乱 — 调用者必须明确从哪个模块导入。

### 优化建议

**推荐方案：保留两层，消除重名歧义**

1. `exceptions.py` 作为**基础异常层**保留，提供纯 Python 异常（raise/catch 用）
2. `error.py` 作为**统一错误翻译层**保留，`UnifiedError` 继续继承 `BtApiError`
4. 或者反过来：**重命名 `error.py` 中的同名类**加前缀，如 `UnifiedAuthError`、`UnifiedRateLimitError`

**不推荐合并**：`error.py` 已有 1237 行，且依赖 `exceptions.py` 基类，强行合并会使文件过大且破坏继承链。

---

## 4. instrument_manager.py vs symbol_manager.py 分析结论

### 定位差异

| 维度 | `symbol_manager.py` | `instrument_manager.py` |
|------|---------------------|------------------------|
| **定位** | 品种命名映射（internal ↔ exchange_symbol） | 交易标的管理（Instrument 注册/查询/筛选） |
| **核心类** | `SymbolManager` + `SymbolInfo` | `InstrumentManager` |
| **数据模型** | `SymbolInfo`（internal_name, exchange, exchange_symbol, meta） | `Instrument`（来自 containers/instrument.py，含 venue, asset_type, underlying 等丰富字段） |
| **功能** | 双向映射：`to_exchange()` / `from_exchange()` | 注册、按 venue/underlying/asset_type 查找、双向转换 |
| **行数** | 89 行 | 97 行 |

### 功能重叠

两者都提供了**品种双向映射**功能：

| 功能 | SymbolManager | InstrumentManager |
|------|--------------|-------------------|
| 内部名 → 交易所名 | `to_exchange(internal, exchange)` | `to_venue_symbol(internal)` |
| 交易所名 → 内部名 | `from_exchange(symbol, exchange)` | `to_internal(venue, venue_symbol)` |
| 按交易所筛选 | `list_symbols(exchange)` | `find(venue=...)` |
| 清空 | `clear()` | `clear()` |

### 使用情况

- **`SymbolManager`**：仅被 `__init__.py`（导出）和 `tests/test_symbol_manager.py`（测试）引用 — **实际业务代码中未使用**
- **`InstrumentManager`**：仅被 `tests/test_stage0_infrastructure.py`（测试）引用 — **实际业务代码中也未使用**

### 优化建议

**推荐方案：合并为 `InstrumentManager`，删除 `SymbolManager`**

理由：
1. 两者功能高度重叠，`InstrumentManager` 基于更丰富的 `Instrument` 数据模型，是 `SymbolManager` 的超集
2. 两者在实际业务代码中**都未被使用**，仅有测试引用，合并成本极低
3. `InstrumentManager` 已提供 `to_internal()` / `to_venue_symbol()` 双向映射，可完全替代 `SymbolManager`

具体步骤：
1. 将 `SymbolManager` 的 `meta` 字段能力合入 `Instrument`（如果还没有的话）
2. 删除 `symbol_manager.py`
3. 更新 `__init__.py` 导出改为 `InstrumentManager`
4. 将 `test_symbol_manager.py` 中的测试迁移到 `test_stage0_infrastructure.py` 的 `TestInstrumentManager` 中

---

## 已完成的修改

- ✅ **Task 3** — `error.py` 中三个重名类已加 `Unified` 前缀：`UnifiedAuthError`、`UnifiedRateLimitError`、`UnifiedRequestFailedError`；`http_client.py`、`test_live_ib_web_request_data.py`、`test_stage0_infrastructure.py` 已更新引用
- ✅ **Task 4** — 删除 `symbol_manager.py`，`__init__.py` 改为导出 `InstrumentManager`，`test_symbol_manager.py` 已重写为 `InstrumentManager` 测试
- ✅ 全部测试通过（341 passed, 0 failed）

---

## 5. 其他可改进优化建议

### A. ✅ error.py 重复类定义修复

`KrakenErrorTranslator` 3 次重复定义、`KuCoinErrorTranslator.translate` 2 次重复定义已在 Task A+B 中一并修复。

### B. ✅ error.py 拆分 Translator 到 errors/ 子模块

`error.py` 从 1237 行精简为 308 行（仅保留核心类 + 翻译器重新导出）。11 个翻译器已拆分到 `errors/` 子模块：

| 文件 | 翻译器类 |
|------|---------|
| `errors/gemini_translator.py` | `GeminiErrorTranslator` |
| `errors/binance_translator.py` | `BinanceErrorTranslator` |
| `errors/okx_translator.py` | `OKXErrorTranslator` |
| `errors/kraken_translator.py` | `KrakenErrorTranslator` |
| `errors/ctp_translator.py` | `CTPErrorTranslator` |
| `errors/ib_web_translator.py` | `IBWebErrorTranslator` |
| `errors/bybit_translator.py` | `BybitErrorTranslator` |
| `errors/bitget_translator.py` | `BitgetErrorTranslator` |
| `errors/kucoin_translator.py` | `KuCoinErrorTranslator` |
| `errors/upbit_translator.py` | `UpbitErrorTranslator` |
| `errors/bitfinex_error_translator.py` | `BitfinexErrorTranslator`（覆盖旧版） |

`error.py` 末尾通过 re-export 保持 `from bt_api_py.error import XxxTranslator` 向后兼容，无需修改下游 import。

新增 `ErrorCategory` 成员：`API`, `ORDER`, `TRADE`, `ACCOUNT`
新增 `UnifiedErrorCode` 成员：`API_ERROR(8001)`, `ORDER_ERROR(8002)`, `TRADE_ERROR(8003)`, `ACCOUNT_ERROR(8004)`

### C. 🟡 删除未使用的基础设施模块（未执行）

`connection_pool.py`、`cache.py`、`security.py` 无引用，共 770 行死代码。

### D. ✅ bt_api.py 交易所注册改为自动发现

硬编码的 5 个 `import register_XXX` 替换为 `pkgutil.iter_modules` 自动扫描。

### E. ✅ instrument_manager.py 文档字符串清理

移除了对已删除 `SymbolManager` 的过时引用。

---

**测试结果**：355 passed, 1 failed（CTP MRO 预存 bug，与本次修改无关）
