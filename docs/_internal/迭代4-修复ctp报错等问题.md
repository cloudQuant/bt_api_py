1. 修复ctp的报错，如果测试用例中的import等路径问题可以修改，其他问题尽可能不要修改测试用例，修复相应的源代码：
(base) yunjinqi@yuns-MacBook-Air-4 bt_api_py % pytest tests/test_ctp_feed.py
/Users/yunjinqi/opt/anaconda3/lib/python3.11/site-packages/pytest_asyncio/plugin.py:208: PytestDeprecationWarning: The configuration option "asyncio_default_fixture_loop_scope" is unset.
The event loop scope for asynchronous fixtures will default to the fixture caching scope. Future versions of pytest-asyncio will default the loop scope for asynchronous fixtures to function scope. Set the default fixture loop scope explicitly in order to avoid unexpected behavior in the future. Valid fixture loop scopes are: "function", "class", "module", "package", "session"

  warnings.warn(PytestDeprecationWarning(_DEFAULT_FIXTURE_LOOP_SCOPE_UNSET))
/Users/yunjinqi/opt/anaconda3/lib/python3.11/site-packages/pytest_benchmark/logger.py:39: PytestBenchmarkWarning: Benchmarks are automatically disabled because xdist plugin is active.Benchmarks cannot be performed reliably in a parallelized environment.
  warner(PytestBenchmarkWarning(text))
Test session starts (platform: darwin, Python 3.11.8, pytest 8.3.3, pytest-sugar 1.1.1)
benchmark: 5.1.0 (defaults: timer=time.perf_counter disable_gc=False min_rounds=5 min_time=0.000005 max_time=1.0 calibration_precision=10 warmup=False warmup_iterations=100000)
rootdir: /Users/yunjinqi/Documents/source_code/bt_api_py
configfile: pyproject.toml
plugins: asyncio-0.24.0, cov-6.0.0, Faker-37.5.3, anyio-4.11.0, flask-1.3.0, doctestplus-1.6.0, html-4.1.1, xdist-3.8.0, time-machine-2.16.0, timeout-2.4.0, metadata-3.1.1, rerunfailures-14.0, mysql-3.1.0, playwright-0.7.0, mock-3.14.1, ordering-0.6, sugar-1.1.1, benchmark-5.1.0, allure-pytest-2.15.2, langsmith-0.4.38, base-url-2.1.0, dash-2.18.2, picked-0.5.1, hypothesis-6.148.5, requests-mock-1.12.1, postgresql-7.0.2
asyncio: mode=Mode.STRICT, default_loop_scope=None
collected 23 items

 tests/test_ctp_feed.py ✓✓                                                                      9% ▉

―――――――――――――――――――――――――――――――――――― TestCtpImports.test_ctp_feed_import ――――――――――――――――――――――――――――――――――――
tests/test_ctp_feed.py:77: in test_ctp_feed_import
    from bt_api_py.feeds.live_ctp_feed import (
bt_api_py/feeds/live_ctp_feed.py:18: in <module>
    from bt_api_py.containers.ctp.ctp_account import CtpAccountData
bt_api_py/containers/ctp/__init__.py:3: in <module>
    from .ctp_account import CtpAccountData
bt_api_py/containers/ctp/ctp_account.py:11: in <module>
    class CtpAccountData(AutoInitMixin, AccountData):
E   TypeError: Cannot create a consistent method resolution
E   order (MRO) for bases AutoInitMixin, AccountData

 tests/test_ctp_feed.py ⨯                                                                      13% █▍

――――――――――――――――――――――――――――――――― TestCtpImports.test_ctp_containers_import ―――――――――――――――――――――――――――――――――
tests/test_ctp_feed.py:89: in test_ctp_containers_import
    from bt_api_py.containers.ctp import (
bt_api_py/containers/ctp/__init__.py:3: in <module>
    from .ctp_account import CtpAccountData
bt_api_py/containers/ctp/ctp_account.py:11: in <module>
    class CtpAccountData(AutoInitMixin, AccountData):
E   TypeError: Cannot create a consistent method resolution
E   order (MRO) for bases AutoInitMixin, AccountData

 tests/test_ctp_feed.py ⨯                                                                      17% █▊

――――――――――――――――――――――――――――――――――――― TestCtpImports.test_ctp_registry ――――――――――――――――――――――――――――――――――――――
tests/test_ctp_feed.py:98: in test_ctp_registry
    import bt_api_py.exchange_registers.register_ctp  # noqa: F401
bt_api_py/exchange_registers/register_ctp.py:11: in <module>
    from bt_api_py.feeds.live_ctp_feed import CtpMarketStream, CtpRequestDataFuture, CtpTradeStream
bt_api_py/feeds/live_ctp_feed.py:18: in <module>
    from bt_api_py.containers.ctp.ctp_account import CtpAccountData
bt_api_py/containers/ctp/__init__.py:3: in <module>
    from .ctp_account import CtpAccountData
bt_api_py/containers/ctp/ctp_account.py:11: in <module>
    class CtpAccountData(AutoInitMixin, AccountData):
E   TypeError: Cannot create a consistent method resolution
E   order (MRO) for bases AutoInitMixin, AccountData

 tests/test_ctp_feed.py ⨯                                                                      22% ██▎

―――――――――――――――――――――――――――――――――― TestCtpImports.test_btapi_includes_ctp ―――――――――――――――――――――――――――――――――――
tests/test_ctp_feed.py:105: in test_btapi_includes_ctp
    from bt_api_py.bt_api import BtApi
bt_api_py/bt_api.py:31: in <module>
    importlib.import_module(f"bt_api_py.exchange_registers.{_name}")
../../../opt/anaconda3/lib/python3.11/importlib/__init__.py:126: in import_module
    return _bootstrap._gcd_import(name[level:], package, level)
bt_api_py/exchange_registers/register_ctp.py:11: in <module>
    from bt_api_py.feeds.live_ctp_feed import CtpMarketStream, CtpRequestDataFuture, CtpTradeStream
bt_api_py/feeds/live_ctp_feed.py:18: in <module>
    from bt_api_py.containers.ctp.ctp_account import CtpAccountData
bt_api_py/containers/ctp/__init__.py:3: in <module>
    from .ctp_account import CtpAccountData
bt_api_py/containers/ctp/ctp_account.py:11: in <module>
    class CtpAccountData(AutoInitMixin, AccountData):
E   TypeError: Cannot create a consistent method resolution
E   order (MRO) for bases AutoInitMixin, AccountData

 tests/test_ctp_feed.py ⨯✓✓✓✓                                                                  43% ████▍

――――――――――――――――――――――――――――――――― TestCtpContainerParsing.test_account_data ―――――――――――――――――――――――――――――――――
tests/test_ctp_feed.py:170: in test_account_data
    from bt_api_py.containers.ctp.ctp_account import CtpAccountData
bt_api_py/containers/ctp/__init__.py:3: in <module>
    from .ctp_account import CtpAccountData
bt_api_py/containers/ctp/ctp_account.py:11: in <module>
    class CtpAccountData(AutoInitMixin, AccountData):
E   TypeError: Cannot create a consistent method resolution
E   order (MRO) for bases AutoInitMixin, AccountData

 tests/test_ctp_feed.py ⨯                                                                      48% ████▊

―――――――――――――――――――――――――――――――――― TestCtpContainerParsing.test_order_data ――――――――――――――――――――――――――――――――――
tests/test_ctp_feed.py:189: in test_order_data
    from bt_api_py.containers.ctp.ctp_order import CtpOrderData
bt_api_py/containers/ctp/__init__.py:3: in <module>
    from .ctp_account import CtpAccountData
bt_api_py/containers/ctp/ctp_account.py:11: in <module>
    class CtpAccountData(AutoInitMixin, AccountData):
E   TypeError: Cannot create a consistent method resolution
E   order (MRO) for bases AutoInitMixin, AccountData

 tests/test_ctp_feed.py ⨯                                                                      52% █████▎

―――――――――――――――――――――――――――――――― TestCtpContainerParsing.test_position_data ―――――――――――――――――――――――――――――――――
tests/test_ctp_feed.py:207: in test_position_data
    from bt_api_py.containers.ctp.ctp_position import CtpPositionData
bt_api_py/containers/ctp/__init__.py:3: in <module>
    from .ctp_account import CtpAccountData
bt_api_py/containers/ctp/ctp_account.py:11: in <module>
    class CtpAccountData(AutoInitMixin, AccountData):
E   TypeError: Cannot create a consistent method resolution
E   order (MRO) for bases AutoInitMixin, AccountData

 tests/test_ctp_feed.py ⨯                                                                      57% █████▋

―――――――――――――――――――――――――――――――――― TestCtpContainerParsing.test_trade_data ――――――――――――――――――――――――――――――――――
tests/test_ctp_feed.py:221: in test_trade_data
    from bt_api_py.containers.ctp.ctp_trade import CtpTradeData
bt_api_py/containers/ctp/__init__.py:3: in <module>
    from .ctp_account import CtpAccountData
bt_api_py/containers/ctp/ctp_account.py:11: in <module>
    class CtpAccountData(AutoInitMixin, AccountData):
E   TypeError: Cannot create a consistent method resolution
E   order (MRO) for bases AutoInitMixin, AccountData

 tests/test_ctp_feed.py ⨯                                                                      61% ██████▏

――――――――――――――――――――――――――――――――― TestCtpContainerParsing.test_ticker_data ――――――――――――――――――――――――――――――――――
tests/test_ctp_feed.py:235: in test_ticker_data
    from bt_api_py.containers.ctp.ctp_ticker import CtpTickerData
bt_api_py/containers/ctp/__init__.py:3: in <module>
    from .ctp_account import CtpAccountData
bt_api_py/containers/ctp/ctp_account.py:11: in <module>
    class CtpAccountData(AutoInitMixin, AccountData):
E   TypeError: Cannot create a consistent method resolution
E   order (MRO) for bases AutoInitMixin, AccountData

 tests/test_ctp_feed.py ⨯                                                                      65% ██████▌

―――――――――――――――――――――――――――――――― TestCtpContainerParsing.test_field_to_dict ―――――――――――――――――――――――――――――――――
tests/test_ctp_feed.py:251: in test_field_to_dict
    from bt_api_py.feeds.live_ctp_feed import _ctp_field_to_dict
bt_api_py/feeds/live_ctp_feed.py:18: in <module>
    from bt_api_py.containers.ctp.ctp_account import CtpAccountData
bt_api_py/containers/ctp/__init__.py:3: in <module>
    from .ctp_account import CtpAccountData
bt_api_py/containers/ctp/ctp_account.py:11: in <module>
    class CtpAccountData(AutoInitMixin, AccountData):
E   TypeError: Cannot create a consistent method resolution
E   order (MRO) for bases AutoInitMixin, AccountData

 tests/test_ctp_feed.py ⨯                                                                      70% ██████▉

――――――――――――――――――――――――――――――― TestCtpContainerParsing.test_balance_handler ――――――――――――――――――――――――――――――――
tests/test_ctp_feed.py:266: in test_balance_handler
    from bt_api_py.containers.ctp.ctp_account import CtpAccountData
bt_api_py/containers/ctp/__init__.py:3: in <module>
    from .ctp_account import CtpAccountData
bt_api_py/containers/ctp/ctp_account.py:11: in <module>
    class CtpAccountData(AutoInitMixin, AccountData):
E   TypeError: Cannot create a consistent method resolution
E   order (MRO) for bases AutoInitMixin, AccountData

 tests/test_ctp_feed.py ⨯                                                                      74% ███████▍

――――――――――――――――――――― ERROR at setup of TestCtpTraderIntegration.test_01_trader_connect ―――――――――――――――――――――
tests/test_ctp_feed.py:303: in setup_feed
    from bt_api_py.feeds.live_ctp_feed import CtpRequestDataFuture
bt_api_py/feeds/live_ctp_feed.py:18: in <module>
    from bt_api_py.containers.ctp.ctp_account import CtpAccountData
bt_api_py/containers/ctp/__init__.py:3: in <module>
    from .ctp_account import CtpAccountData
bt_api_py/containers/ctp/ctp_account.py:11: in <module>
    class CtpAccountData(AutoInitMixin, AccountData):
E   TypeError: Cannot create a consistent method resolution
E   order (MRO) for bases AutoInitMixin, AccountData
                                                                                               78% ███████▉

――――――――――――――――――――― ERROR at setup of TestCtpTraderIntegration.test_02_query_account ――――――――――――――――――――――
tests/test_ctp_feed.py:303: in setup_feed
    from bt_api_py.feeds.live_ctp_feed import CtpRequestDataFuture
bt_api_py/feeds/live_ctp_feed.py:18: in <module>
    from bt_api_py.containers.ctp.ctp_account import CtpAccountData
bt_api_py/containers/ctp/__init__.py:3: in <module>
    from .ctp_account import CtpAccountData
bt_api_py/containers/ctp/ctp_account.py:11: in <module>
    class CtpAccountData(AutoInitMixin, AccountData):
E   TypeError: Cannot create a consistent method resolution
E   order (MRO) for bases AutoInitMixin, AccountData
                                                                                               83% ████████▍

―――――――――――――――――――― ERROR at setup of TestCtpTraderIntegration.test_03_query_positions ―――――――――――――――――――――
tests/test_ctp_feed.py:303: in setup_feed
    from bt_api_py.feeds.live_ctp_feed import CtpRequestDataFuture
bt_api_py/feeds/live_ctp_feed.py:18: in <module>
    from bt_api_py.containers.ctp.ctp_account import CtpAccountData
bt_api_py/containers/ctp/__init__.py:3: in <module>
    from .ctp_account import CtpAccountData
bt_api_py/containers/ctp/ctp_account.py:11: in <module>
    class CtpAccountData(AutoInitMixin, AccountData):
E   TypeError: Cannot create a consistent method resolution
E   order (MRO) for bases AutoInitMixin, AccountData
                                                                                               87% ████████▊

――――――――――――――――――――――― ERROR at setup of TestCtpTraderIntegration.test_04_make_order ―――――――――――――――――――――――
tests/test_ctp_feed.py:303: in setup_feed
    from bt_api_py.feeds.live_ctp_feed import CtpRequestDataFuture
bt_api_py/feeds/live_ctp_feed.py:18: in <module>
    from bt_api_py.containers.ctp.ctp_account import CtpAccountData
bt_api_py/containers/ctp/__init__.py:3: in <module>
    from .ctp_account import CtpAccountData
bt_api_py/containers/ctp/ctp_account.py:11: in <module>
    class CtpAccountData(AutoInitMixin, AccountData):
E   TypeError: Cannot create a consistent method resolution
E   order (MRO) for bases AutoInitMixin, AccountData
                                                                                               91% █████████▎

――――――――――――――――――――― ERROR at setup of TestCtpTraderIntegration.test_05_feed_via_btapi ―――――――――――――――――――――
tests/test_ctp_feed.py:303: in setup_feed
    from bt_api_py.feeds.live_ctp_feed import CtpRequestDataFuture
bt_api_py/feeds/live_ctp_feed.py:18: in <module>
    from bt_api_py.containers.ctp.ctp_account import CtpAccountData
bt_api_py/containers/ctp/__init__.py:3: in <module>
    from .ctp_account import CtpAccountData
bt_api_py/containers/ctp/ctp_account.py:11: in <module>
    class CtpAccountData(AutoInitMixin, AccountData):
E   TypeError: Cannot create a consistent method resolution
E   order (MRO) for bases AutoInitMixin, AccountData
                                                                                               96% █████████▋

――――――――――――――――――――――― TestCtpMdIntegration.test_md_connect_receive_tick_and_convert ―――――――――――――――――――――――
tests/test_ctp_feed.py:406: in test_md_connect_receive_tick_and_convert
    from bt_api_py.feeds.live_ctp_feed import _ctp_field_to_dict
bt_api_py/feeds/live_ctp_feed.py:18: in <module>
    from bt_api_py.containers.ctp.ctp_account import CtpAccountData
bt_api_py/containers/ctp/__init__.py:3: in <module>
    from .ctp_account import CtpAccountData
bt_api_py/containers/ctp/ctp_account.py:11: in <module>
    class CtpAccountData(AutoInitMixin, AccountData):
E   TypeError: Cannot create a consistent method resolution
E   order (MRO) for bases AutoInitMixin, AccountData

 tests/test_ctp_feed.py ⨯                                                                     100% ██████████
========================================== short test summary info ==========================================
FAILED tests/test_ctp_feed.py::TestCtpImports::test_ctp_feed_import - TypeError: Cannot create a consistent method resolution
FAILED tests/test_ctp_feed.py::TestCtpImports::test_ctp_containers_import - TypeError: Cannot create a consistent method resolution
FAILED tests/test_ctp_feed.py::TestCtpImports::test_ctp_registry - TypeError: Cannot create a consistent method resolution
FAILED tests/test_ctp_feed.py::TestCtpImports::test_btapi_includes_ctp - TypeError: Cannot create a consistent method resolution
FAILED tests/test_ctp_feed.py::TestCtpContainerParsing::test_account_data - TypeError: Cannot create a consistent method resolution
FAILED tests/test_ctp_feed.py::TestCtpContainerParsing::test_order_data - TypeError: Cannot create a consistent method resolution
FAILED tests/test_ctp_feed.py::TestCtpContainerParsing::test_position_data - TypeError: Cannot create a consistent method resolution
FAILED tests/test_ctp_feed.py::TestCtpContainerParsing::test_trade_data - TypeError: Cannot create a consistent method resolution
FAILED tests/test_ctp_feed.py::TestCtpContainerParsing::test_ticker_data - TypeError: Cannot create a consistent method resolution
FAILED tests/test_ctp_feed.py::TestCtpContainerParsing::test_field_to_dict - TypeError: Cannot create a consistent method resolution
FAILED tests/test_ctp_feed.py::TestCtpContainerParsing::test_balance_handler - TypeError: Cannot create a consistent method resolution
FAILED tests/test_ctp_feed.py::TestCtpTraderIntegration::test_01_trader_connect - TypeError: Cannot create a consistent method resolution
FAILED tests/test_ctp_feed.py::TestCtpTraderIntegration::test_02_query_account - TypeError: Cannot create a consistent method resolution
FAILED tests/test_ctp_feed.py::TestCtpTraderIntegration::test_03_query_positions - TypeError: Cannot create a consistent method resolution
FAILED tests/test_ctp_feed.py::TestCtpTraderIntegration::test_04_make_order - TypeError: Cannot create a consistent method resolution
FAILED tests/test_ctp_feed.py::TestCtpTraderIntegration::test_05_feed_via_btapi - TypeError: Cannot create a consistent method resolution
FAILED tests/test_ctp_feed.py::TestCtpMdIntegration::test_md_connect_receive_tick_and_convert - TypeError: Cannot create a consistent method resolution

Results (0.99s):
       6 passed
      12 failed
         - tests/test_ctp_feed.py:75 TestCtpImports.test_ctp_feed_import
         - tests/test_ctp_feed.py:87 TestCtpImports.test_ctp_containers_import
         - tests/test_ctp_feed.py:95 TestCtpImports.test_ctp_registry
         - tests/test_ctp_feed.py:103 TestCtpImports.test_btapi_includes_ctp
         - tests/test_ctp_feed.py:169 TestCtpContainerParsing.test_account_data
         - tests/test_ctp_feed.py:188 TestCtpContainerParsing.test_order_data
         - tests/test_ctp_feed.py:206 TestCtpContainerParsing.test_position_data
         - tests/test_ctp_feed.py:220 TestCtpContainerParsing.test_trade_data
         - tests/test_ctp_feed.py:234 TestCtpContainerParsing.test_ticker_data
         - tests/test_ctp_feed.py:250 TestCtpContainerParsing.test_field_to_dict
         - tests/test_ctp_feed.py:264 TestCtpContainerParsing.test_balance_handler
         - tests/test_ctp_feed.py:403 TestCtpMdIntegration.test_md_connect_receive_tick_and_convert
       5 error
(base) yunjinqi@yuns-MacBook-Air-4 bt_api_py %

## 已完成的修复

### 1. CTP MRO 错误修复

***根因**：`CtpAccountData(AutoInitMixin, AccountData)` 和 `CtpTickerData(AutoInitMixin, TickerData)` 中，`AutoInitMixin` 既作为直接基类又通过 `AccountData`/`TickerData` 间接继承，导致 Python C3 线性化算法无法解析 MRO。

***修复**：
- `ctp_account.py`：`class CtpAccountData(AutoInitMixin, AccountData)` → `class CtpAccountData(AccountData)`，删除多余 import
- `ctp_ticker.py`：`class CtpTickerData(AutoInitMixin, TickerData)` → `class CtpTickerData(TickerData)`，删除多余 import

`AutoInitMixin` 已通过 `AccountData(AutoInitMixin)` / `TickerData(AutoInitMixin)` 继承链获得，无需重复声明。

### 2. dydx / hyperliquid 语法错误修复

auto-discover 机制（迭代3 Task D）使 `bt_api.py` 加载所有 exchange_registers，暴露了之前未触发的语法问题：

- `feeds/live_dydx/request_base.py:86`：缩进错误（多余空格）
- `feeds/live_hyperliquid/request_base.py:24-25`：`get_logger` import 误入 `hyperliquid_types` 的 import 块
- `feeds/live_hyperliquid/spot.py:23-24`：同上

### 测试结果

- `tests/test_ctp_feed.py`：**23 passed**（之前 12 failed + 5 error）
- `tests/test_stage0_infrastructure.py` + `tests/test_stage1_exchange_integration.py` + `tests/test_ctp_feed.py`：**112 passed**
