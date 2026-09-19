# ctp_data —— CTP 全市场 tick 采集运行目录

本目录既是**采集数据根目录**，也是**采集启动入口**，同时是**盘后数据清洗的权威 tick 库**
（清洗会把各分片机的数据拉回这里合并，并在此目录下生成 K 线）。版本库里只保留"怎么跑"的文件，
行情数据、日志、锁文件、本机配置全部由 `.gitignore` 排除。

| 文件 | 是否入库 | 说明 |
|------|---------|------|
| `README.md` | ✅ | 本文档 |
| `start_collector.sh` | ✅ | macOS / Linux 启动脚本 |
| `start_collector.bat` | ✅ | Windows 启动脚本 |
| `collector.example.yaml` | ✅ | 配置模板，不含敏感信息 |
| `.gitignore` | ✅ | 忽略规则 |
| `collector.yaml` | ❌ | 本机配置，从模板复制 |
| `logs/`、`<交易日>/`、`.staging/`、`.locks/` | ❌ | 采集运行产物 |
| `cleaner.yaml` | ❌ | 清洗本机配置，从模板复制（见第 9 节） |
| `kline/` | ❌ | K 线输出（跨日追加，见第 9 节） |
| `cleaner/`（`manifest.json`、`reports/`、`staging/`） | ❌ | 清洗状态、报告与拉取过渡区 |

> 账号凭证不在本目录：放在**仓库根目录的 `.env`**（同样不入库）。清洗脚本不需要 CTP 账号。

---

## 1. 首次准备

```bash
# ① 安装（仓库根目录执行，只需一次）
pip install -e .

# ② 准备账号：仓库根目录 .env 需要这些键（账号只放这里，不要写进 collector.yaml）
#    CTP_MD_FRONT  CTP_TD_FRONT  CTP_BROKER_ID  CTP_USER_ID  CTP_PASSWORD
#    CTP_APP_ID    CTP_AUTH_CODE
#    这 7 个键当前都已配置且登录验证通过；CTP_APP_ID / CTP_AUTH_CODE 代码里有
#    SimNow 默认值，其余 5 个是必需的。

# ③ 准备配置：复制模板即可。data_root 默认是相对路径 ctp_data（相对仓库根），
#    启动脚本会先 cd 到仓库根目录，所以换机器 / 换目录都不需要修改。
#    想把数据放到别的盘，再把 data_root 改成绝对路径。
cp ctp_data/collector.example.yaml ctp_data/collector.yaml

# ④ 自检（不连行情、不占用 CTP 会话）
python -m bt_api_ctp.collector --config ctp_data/collector.yaml --check-calendar
python -m bt_api_ctp.collector --config ctp_data/collector.yaml --validate-shards
```

`--check-calendar` 会打印 `trading_day` 与 `night_session`，用来确认调度时点是否合理。

---

## 2. 启动

```bash
# macOS / Linux
sh ctp_data/start_collector.sh

# 或
chmod +x ctp_data/start_collector.sh   # 只需一次
./ctp_data/start_collector.sh
```

```bat
:: Windows
ctp_data\start_collector.bat
```

两个脚本行为一致，依次做四件事：

1. 从仓库根目录加载 `.env`（collector 自身不读 `.env`）；
2. 挑一个 **能 `import bt_api_ctp`** 的 python（PATH 上的 `python` 常常不是装了依赖的那个）；
3. `cd` 到仓库根目录；
4. 前台运行 `python -m bt_api_ctp.collector --config ctp_data/collector.yaml --until-close --wait-open`。

**前台运行到收盘**：脚本会一直阻塞，按当前时段自动跑到该时段结束（白盘 → 15:15，夜盘 → 02:30）
再退出，并生成当天的 `report.json`。

启动时会打印实际生效的路径，用于核对数据落盘位置与使用的代码版本：

```
[start_collector] 解释器：/path/to/python
[start_collector] 配置：<仓库根>/ctp_data/collector.yaml
[start_collector] 线程上限：OMP_NUM_THREADS=4 NUMEXPR_MAX_THREADS=4
[start_collector] bt_api_ctp：<仓库根>/bt_api/bt_api_ctp/src/bt_api_ctp/__init__.py
[start_collector] 数据根目录：<仓库根>/ctp_data
```

路径都不是写死的：脚本按自身所在位置推导 `仓库根`，再解析配置里的 `data_root`
（相对路径 = 相对仓库根），因此把整个仓库换目录或换机器都不需要改任何东西。

`bt_api_ctp` 那一行尤其要留意：**它显示 site-packages 里的路径就说明跑的是安装版，
改过插件代码不会生效**。脚本在源码存在时会自动把 `<仓库根>/bt_api/bt_api_ctp/src`
放到 `PYTHONPATH` 最前，所以正常情况下应指向仓库源码。

### 关于 CPU 占用（不是"8 个进程"）

采集是**单进程**：1 个主线程 + `tick-compact` + `ctp-resubscribe` 两个工作线程，
外加 CTP 原生库自己的线程。日志里那句 `NumExpr defaulting to 8 threads.` 是
`numexpr` 按 **CPU 核数**设置线程池，不是启动了 8 个进程。

真正会吃掉多核的是 pyarrow / numexpr 的并行度，脚本已默认限制为 4：

| 环境变量 | 作用 | 默认（脚本） |
|---|---|---|
| `OMP_NUM_THREADS` | pyarrow 计算线程池（`pa.cpu_count()`） | 4 |
| `NUMEXPR_MAX_THREADS` / `NUMEXPR_NUM_THREADS` | numexpr 线程池 | 4 |

想放开（例如服务器上加快收盘压缩）就在 `.env` 里显式写 `OMP_NUM_THREADS=8`，
脚本只在变量未设置时才填 4。启动日志里的"线程上限"行可用于核对。

如果 CPU 仍然长期打满，先看是不是 IDE / 索引 / 其它进程：采集单进程的稳态 CPU
主要花在收盘压缩（`_dedup_sort` 是纯 Python 循环），那段时间占一两个核属正常。

---

## 3. 参数覆盖

| 需求 | macOS / Linux | Windows |
|------|---------------|---------|
| 追加参数 | `sh ctp_data/start_collector.sh -v` | `ctp_data\start_collector.bat -v` |
| 替换默认参数 | `COLLECTOR_ARGS='--once --duration 60' sh ctp_data/start_collector.sh` | `set COLLECTOR_ARGS=--once --duration 60 & ctp_data\start_collector.bat` |
| 指定解释器 | `PYTHON=/path/to/python sh ctp_data/start_collector.sh` | `set PYTHON=C:\path\to\python.exe & ...` |
| 只要日历/分片自检 | `sh ctp_data/start_collector.sh --check-calendar` | 同左 |
| 夜盘（要求当晚有夜盘） | `COLLECTOR_ARGS='--night --until-close --wait-open' sh ...` | 同左 |

注意事项：

- 默认参数里已有 `--until-close`，而 CLI 不允许 `--until-close` 与 `--duration` 同时出现。
  要指定时长请用 `COLLECTOR_ARGS` 整体替换。
- `--night` 是**启动前预检**：只有"当晚确实有夜盘"才继续，周日与节假日前一晚会被直接拒绝。
- `.env` 只支持简单的 `KEY=VALUE`（`#` 开头为注释，值不要含空格），Windows 批处理尤其如此。
- **手工直接跑 CLI 时必须在仓库根目录执行**：`data_root` 是相对路径，脚本会自动 `cd` 到仓库根，
  但绕过脚本时它会解析到当前工作目录。用启动脚本没有这个问题。
- **账号不需要写进 `collector.yaml`**：取值顺序是 `yaml → 环境变量 → 代码默认值`，
  账号放在仓库根目录的 `.env` 里即可（启动脚本自动加载）。写进 yaml 也能生效（yaml 优先），
  但那会让凭证散落到第二个文件里，不推荐。
- `pa.io_thread_count()`（pyarrow 的 IO 线程池）不受 `OMP_NUM_THREADS` 影响，保持等于核数；
  采集路径上没有密集 IO 计算，实测无影响。

---

## 4. 停止

| 方式 | 效果 |
|------|------|
| 前台 `Ctrl+C` | 优雅停机：停订阅 → flush 缓冲 → 压缩收尾 → 写 `report.json` |
| 后台 `kill -INT <pid>` | 同上 |
| `kill -9` | ❌ 硬杀：`report.json` 不会生成；待压缩段文件留在 `.staging/`，由下一次运行认领合并 |

后台运行（终端会关闭的场景）：

```bash
nohup sh ctp_data/start_collector.sh > /dev/null 2>&1 &
echo $! > ctp_data/collector.pid
# 停止
kill -INT "$(cat ctp_data/collector.pid)"
```

---

## 5. 长期运行与自动调度

一个交易日 = **夜盘（前一晚 21:00 起，最晚到次日 02:30）+ 白盘（09:00–15:15）**。
夜盘归属次日：周五晚的夜盘与周一的日盘是同一个交易日，周日晚没有夜盘，
法定节假日前一晚夜盘暂停。

采集进程本身是**单时段**的：跑到本组收盘就自己退出（`--until-close`）。所以"长期运行"
有两种做法，推荐第一种。

### 5.1 常驻调度器：一次启动，长期运行（推荐）

[`service.sh`](service.sh) / [`service.bat`](service.bat)（逻辑在 [`service.py`](service.py)）
是一个常驻进程：算好下一个开盘时刻，**提前 15 分钟**拉起一次采集，等它退出后按退出码决定
「跳到下一时段 / 短退避重试 / 直接失败」，如此循环。

```bash
sh ctp_data/service.sh                # 前台常驻，Ctrl+C / SIGTERM 优雅停止
sh ctp_data/service.sh --dry-run      # 只打印调度计划、不采集（部署前先验证时段算得对）
```

行为要点：

| 场景 | 行为 |
|------|------|
| 开盘前 15 分钟 | 拉起与 `start_collector.sh` 相同的采集命令（夜盘段自动加 `--night`） |
| 采集正常结束（exit 0） | 推进到下一个时段 |
| 非交易日 / 当晚无夜盘（exit 3） | 视为正常跳过，不重试，推进到下一时段 |
| 采集失败（其他非 0） | 等 `--retry-backoff`（默认 300s）重试，默认最多 2 次，然后放弃本时段 |
| 配置错误（exit 2） | 立即退出（重试无意义），交给服务管理器告警 |
| 收到 SIGTERM / SIGINT | 转成 SIGINT 交给正在跑的采集进程，等它 finalize 完再退出 |

可选参数：`--lead 900`（提前秒数）、`--retry 2`、`--retry-backoff 300`、`--config <yaml>`、
`--log-file <path>`（5MB × 3 轮转）、`--once`（只处理一个时段，便于验证）。

**平台托管**（让常驻进程开机自启、崩了自动拉起）：

| 平台 | 素材 | 安装要点 |
|---|---|---|
| Ubuntu | [`deploy/systemd/ctp-tick-collector.service`](deploy/systemd/ctp-tick-collector.service) | 改好 `User`/路径 → `cp` 到 `/etc/systemd/system/` → `systemctl enable --now ctp-tick-collector`；日志 `journalctl -u ctp-tick-collector -f` |
| Win11 | [`deploy/windows/ctp-tick-collector.xml`](deploy/windows/ctp-tick-collector.xml) | 按文件头注释改 4 处 → `schtasks /Create /XML ctp-tick-collector.xml /TN "ctp-tick-collector"`；日志 `<仓库>\ctp_data\service.log` |

> systemd 单元刻意用 `KillMode=mixed`：只给常驻进程发 SIGTERM，由它转成 SIGINT 交给采集进程。
> 不要用默认的 `control-group`——那样 SIGTERM 会直接打死采集进程，`finalize()` 不会执行。

### 5.2 外部调度器（另一种模型）

不想多一个常驻进程，就每天触发两次（仓库里 systemd timer / launchd 已按 08:45、20:45 配好，
见 [`bt_api/bt_api_ctp/deploy/collector/`](../bt_api/bt_api_ctp/deploy/collector/)）：

```cron
# crontab -e（cron 环境不加载 .env，脚本会自己去读，所以没问题）
45 8  * * 1-5  cd /path/to/bt_api_py && sh ctp_data/start_collector.sh                    >> ctp_data/cron.log 2>&1
45 20 * * 1-5  cd /path/to/bt_api_py && COLLECTOR_ARGS='--night --until-close --wait-open' sh ctp_data/start_collector.sh >> ctp_data/cron.log 2>&1
```

两种模型共同的要点：**为什么提前 15 分钟**——TD 登录 + 六大交易所合约查询 + 全市场约 1.7 万条
分批订阅实测需要 1.5～4 分钟（盘中重启时查询可能耗时 25 分钟并以失败告终），压着 09:00 / 21:00
启动会丢掉开盘头几分钟，而 CTP 行情不可回补。

---

## 6. 数据产物

```
ctp_data/
├── <交易日YYYYMMDD>/<交易所>/<合约>.parquet   # 一个合约一个文件
├── <交易日YYYYMMDD>/report.json               # 收盘完整性报告（每合约行数/缺口/覆盖率）
├── kline/<交易所>/<品种>/<合约>_<N>min.parquet # K 线（清洗产物，跨日追加）
├── cleaner/manifest.json                      # 清洗状态留痕（拉取/校验/合并/删除）
├── cleaner/reports/clean-<交易日>.json         # 每次清洗运行的报告
├── cleaner/staging/<主机>/<交易日>/            # 拉取过渡区（合并成功后自动清理）
├── logs/collector-<交易日>.log                # 采集日志（夜盘记为次日）
├── .staging/<pid>-<nonce>/                    # 待压缩段文件
└── .locks/<交易日>/<交易所>/                   # 跨进程文件锁（只增不减，见下）
```

- 目录日期 = `trading_day`：夜盘（21:00 起）的数据写入**次日**目录。
- Parquet schema 固定，字段对齐 `CThostFtdcDepthMarketDataField`，同时保留
  `trading_day` / `action_day` / `update_millisec` / `local_receive_time` 便于对齐与去重。
- 盘中先追加段文件，攒够 `sink.compact_segments`（默认 64）才合并成最终文件；
  因此数据在合并或收盘后才出现在 `<交易日>/` 下。

---

## 7. 已知限制（跑之前值得知道）

1. **`subscribe not ready ... timed_out=NNNN` 是假警**：全市场首次订阅 ACK 实测约 4 分钟，
   而日志的就绪等待窗口只有 15 秒，所以这条 WARNING 在启动时基本必然出现，不代表订阅失败。
2. **健康守卫开盘头 ~10 分钟可能误报**：订阅瞬间的伪快照会把 1.7 万个合约都记为"最近活跃"，
   5 分钟后集中过期触发"大面积静默"告警；参考集收敛后自行消失。
3. **无夜盘品种在夜盘不会有文件**：`drop_outside_session` 会静默丢弃订阅时的伪快照，
   报告里只体现为一个全局计数 `ticks_outside_session`。**GFEX 夜盘实测 0 条**（CFFEX 本就无夜盘）。
4. **`min_ticks_per_interval` 默认 1 等于只检测"完全停摆"**：全市场规模下这个绝对阈值太小，
   建议按"本分片预期每分钟条数的 1%"设置，否则退化到 30% 覆盖率也不会告警。
5. **覆盖率达不到 99%**：SimNow 单连接订阅 1.7 万合约时远低于应有快照密度，
   全市场压测必须用生产账号。
6. **`.locks/` 只增不减**：每天约 1.7 万个锁文件，需要运维定期清理。
7. **`merge_existing` 必须保持 `true`**：设为 `false` 会让后一次压缩整体覆盖前一次结果。
8. **无人值守必须配 `calendar.holidays_file`**：留空时只按"工作日"判断交易日——节假日白盘会
   "跑完但零数据、退出码 0"（看起来成功），`--night` 判断"节前夜盘暂停"也依赖它。
9. **停机只能用 SIGTERM/SIGINT**：只有它们能让采集 finalize（flush → 压缩 → 写 `report.json`）；
   `kill -9` 会留下未合并的段文件（不会丢数据，由下次运行认领，但当次没有报告）。
10. **磁盘与文件数要规划**：数据每天数百 MB、`.locks/` 每天约 1.7 万个文件、日志按交易日一个
    文件且无保留策略。需要定期清理 `logs/` 与 `.locks/`。
11. **周末/节假日会有几次空启动**：常驻调度器不判断交易日，周末会各拉起一次采集进程，进程
    立即以退出码 3 结束（不连柜台、秒退）。这是有意为之——交易日历只有一处（采集进程内部），
    常驻侧不重复实现。

排障工具（只读，不干扰采集进程）：

```bash
# 落盘全集 vs 上一交易日订阅全集：三态对账 + 报告/日志摘要
python scripts/reconcile_tick_universe.py \
    --data-root ctp_data --day 20260918 --reference 20260917
```

---

## 8. Windows：编译 CTP 原生扩展（首次必做一次）

`bt_api_ctp` 的原生扩展 `_ctp` 是必需的。缺了它 CTP 运行时只会静默降级，采集直接失败：

```
RuntimeWarning: CTP C++ extension (_ctp) failed to load;
  expected=_ctp.cp311-win_amd64.pyd, _ctp.pyd; available=_ctp.cpython-31x-darwin.so
collection failed: CTP runtime ... has no verified native extension
```

仓库源码树里**只提交了 macOS 的** `_ctp*.so`（所以 macOS 免编译），Windows / Linux 需要在本机编译一次。

### 前置

- Python 3.11（与 `expected=_ctp.cp311-win_amd64.pyd` 的 ABI 对应；换 Python 版本要重新编译）
- 编译用的解释器必须与跑采集的**同一个**：`python -c "import sys; print(sys.executable, sys.version)"` 确认是 3.11
- **Visual Studio Build Tools**，勾选"使用 C++ 的桌面开发"（提供 MSVC）
- Windows 版 CTP 依赖已在仓库里：`bt_api/bt_api_ctp/src/bt_api_ctp/ctp/api/6.7.7/windows`

### 编译

先进入 **"x64 Native Tools Command Prompt for VS 2022"**（开始菜单 → Visual Studio 2022），它已经把 MSVC
环境变量准备好：

```bat
cd /d D:\bt_api_py\bt_api\bt_api_ctp
python setup.py build_ext --inplace
```

> **必须在已配置 MSVC 的 shell 里执行**（"x64 Native Tools Command Prompt for VS 2022"，或先 `call
> vcvars64.bat` 的 shell）。这种 shell 里 `setup.py` 会自动设置 `DISTUTILS_USE_SDK=1`/`MSSdk=1`，
> 让 setuptools 直接使用现成的 MSVC 环境、**跳过 `vcvarsall.bat` 探测**。
>
> 那次探测在装了 conda 的机器上会再启动嵌套 `cmd`，被 conda 的自动激活钩子污染后返回非零退出码，报
> `error: Error executing cmd /u /c "...\vcvarsall.bat" x86_amd64 && set`——**此时编译还没开始**，
> 与源码或 CTP 库无关。日志开头若出现若干
> `conda-script.py ... ModuleNotFoundError: No module named 'conda'` 即是此坑。
>
> 用**更旧的检出**（`setup.py` 尚无该自动检测）时，手动补上这两行即可：
> `set DISTUTILS_USE_SDK=1`、`set MSSdk=1`。

`setup.py` 会自动按平台选 API 目录，并把 CTP 运行库拷到扩展旁边。产物应为：

```
D:\bt_api_py\bt_api\bt_api_ctp\src\bt_api_ctp\ctp\_ctp.cp311-win_amd64.pyd
（同目录还会多出若干 CTP 运行库 DLL）
```

> **不要用 `pip install .`（非 editable）**：它编译的是 site-packages 里的副本，而 `start_collector.bat`
> 把仓库源码放在 PYTHONPATH 最前（源码优先），那个 `.pyd` 就用不上了。要用 pip 请用 `pip install -e .`
> 并在同一个 MSVC shell 里设置上述两个环境变量；若报"缺 CTP 构建输入"，再加 `--no-build-isolation`。

### 验证（不连行情）

```powershell
set PYTHONPATH=D:\bt_api_py\bt_api\bt_api_ctp\src
python -c "import bt_api_ctp; print(bt_api_ctp.__file__)"
python -c "from bt_api_ctp.ctp._ctp_base import is_ctp_native_loaded, get_ctp_import_error; print(is_ctp_native_loaded(), get_ctp_import_error())"
```

第一条要指向 `D:\bt_api_py\...\src\bt_api_ctp\__init__.py`（不是 site-packages）；第二条应打印
`True None`（CI 用的同一条判据）。打印 `False ...` 就是没编译成功或 import 错了副本。

### 常见失败

| 现象 | 原因 |
|------|------|
| `Required CTP build inputs are missing` | `ctp/api/6.7.7/windows` 不存在（子模块未初始化） |
| `Microsoft Visual C++ 14.0 or greater is required` | 没装 VS Build Tools 的 C++ 工作负载 |
| `Error executing cmd /u /c "...\vcvarsall.bat" x86_amd64 && set` | setuptools 探测 MSVC 环境失败（conda 自动激活钩子污染嵌套 cmd）；在 Native Tools 提示符里重跑，新版 `setup.py` 会自动跳过该探测 |
| 产出 `_ctp.cp310-...pyd` 但运行找 `cp311` | 编译用的 Python 版本 ≠ 跑采集的 Python 版本 |
| 产物存在仍报 `no verified native extension` | import 到的是 site-packages 的旧副本（看上面第一条验证） |
| 不想在本机编译 | 可 `pip install bt_api_ctp`（有 win_amd64 预编译 wheel），但那是 2.0.2 发布版，**不含**批 1/2/3 的修复；而且必须让脚本不要优先仓库源码 |

---

## 9. 盘后数据清洗与 K 线合成（迭代06）

采集侧每个分片机各自保存 `<data_root>/<交易日>/...`；清洗侧把它们集中到本目录、按去重键
合并排序，再合成 1/5/15 分钟 K 线，并在确认拉取校验通过后回收远程已合并的目录。实现在
[`bt_api/bt_api_ctp/src/bt_api_ctp/cleaner/`](../bt_api/bt_api_ctp/src/bt_api_ctp/cleaner/)，
需求/设计/验收见 [`docs/迭代计划/迭代06-CTP数据清洗/`](../docs/迭代计划/迭代06-CTP数据清洗/)。

### 9.1 首次准备

```bash
# ① 复制配置模板（清洗不需要 CTP 账号；账号仍只在仓库根 .env）
#    模板含 rsync / sftp / local 三种主机示例
cp bt_api/bt_api_ctp/examples/cleaner.example.yaml ctp_data/cleaner.yaml
#    编辑 cleaner.yaml：填入各分片机的 backend / host / remote_data_root

# ② 首次务必先"只拉不删"跑通，确认合并正确后再打开删除
#    pull:
#      delete_remote_after_verify: false

# ③ 连通性自检（只读，不传输、不删除）
python -m bt_api_ctp.cleaner --config ctp_data/cleaner.yaml check-hosts
```

### 9.2 常用命令

```bash
# 全流程：拉取 → 校验 → 合并 → 合成 K 线 → 回收远程已合并目录
python -m bt_api_ctp.cleaner --config ctp_data/cleaner.yaml run

# 只看计划不动数据（含删除清单预览；开启删除前建议先看一次）
python -m bt_api_ctp.cleaner --config ctp_data/cleaner.yaml run --dry-run

# 分阶段：只拉取+校验 / 只合并（并补齐缺失 K 线）/ 只合成 K 线
python -m bt_api_ctp.cleaner --config ctp_data/cleaner.yaml pull
python -m bt_api_ctp.cleaner --config ctp_data/cleaner.yaml merge
python -m bt_api_ctp.cleaner --config ctp_data/cleaner.yaml kline --backfill   # 历史回填

# 指定交易日
python -m bt_api_ctp.cleaner --config ctp_data/cleaner.yaml run --day 20260918
```

退出码：`0` 成功、`1` 配置错误、`2` 连接/传输/校验失败、`3` 非交易日（正常跳过）、
`4` 合并或 K 线失败。**退出码 2/4 时远程数据一律保留**，下次运行自动重试。

### 9.3 定时调度：每个交易日 16:00

一个交易日 `T` 的目录 = T−1 晚夜盘 + T 白盘：白盘 15:15 收盘，加 close-grace 后在 ~15:5x
写完 `report.json`，因此 **16:00 时 `T/` 已完整、且没有进程再写它**；当晚 21:00 起的夜盘
写入 `T+1/` 目录，与本轮删除无冲突。所以 16:00 拉取并回收 `T/` 是安全的（周五同理：
周五晚夜盘归下周一目录）。

部署素材：[`bt_api/bt_api_ctp/deploy/cleaner/`](../bt_api/bt_api_ctp/deploy/cleaner/)（cron 与 launchd）。

```cron
0 16 * * 1-5 cd /path/to/bt_api_py && python -m bt_api_ctp.cleaner --config ctp_data/cleaner.yaml run >> ctp_data/cron-cleaner.log 2>&1
```

调度器只按星期触发；节假日由 `cleaner.yaml` 的 `calendar.holidays_file` 判断（非交易日退出 3）。

### 9.4 删除远程数据的安全边界

远程目录只有在**同时**满足以下三条时才会被删除；任何一条不满足都只保留并告警：

1. `cleaner/manifest.json` 中该目录状态为 `merged`（已拉取 + 校验通过 + 已合并）；
2. 配置 `pull.delete_remote_after_verify: true`；
3. 目标是 `remote_data_root` 下的合法 `YYYYMMDD` 目录。

K 线在**回收之前**合成：即使合成失败，远程数据仍在，下次运行自动重试。

### 9.5 K 线产物

```
kline/<交易所>/<品种>/<合约>_<N>min.parquet   # N ∈ {1, 5, 15}
```

- **不按日期分目录**：一个合约一个周期只有一个文件，跨日追加（重跑同一天幂等）。
- 品种目录用标的品种代码：期货 `rb2610` → `SHFE/rb/`；期权归标的品种
  （DCE `m2611-C-2500` → `DCE/m/`、CZCE `FG611C1000` → `CZCE/FG/`、
  CFFEX `HO2609-C-2500` → `CFFEX/HO/`）。
- 字段：`datetime`（桶起始，交易所本地时间）、`trading_day`、`exchange_id`、
  `instrument_id`、`open/high/low/close`、`volume`、`amount`、`open_interest`。
  `volume`/`amount` 由交易所**当日累计值差分**得到；1 分钟自 tick 聚合，5/15 分钟自
  1 分钟汇总，因此多周期可相互验证。
- **组合套利合约会被跳过**：`RM701MSC2100`、`c2701-MS-C-2000` 之类既非期货也非期权，
  不进 K 线（每个交易日约 118 个，属预期行为）。

### 9.6 注意事项

1. **`local` 主机的 `remote_data_root` 不能与 `tick_root` 重叠**（相等、父目录或子目录）：
   `local` 把它当真实路径，重叠会导致回收删掉权威库本身。配置加载阶段会直接拒绝。
2. **首次启用删除前先用 `delete_remote_after_verify: false` 空跑 ≥ 1 个交易日**，
   核对 `cleaner/reports/clean-<交易日>.json` 的 `anomalies` 为空。
3. **`sftp` 后端需要 `paramiko`**：`pip install "bt_api_ctp[cleaner]"`；`rsync`/`local` 不需要。
4. **16:00 依赖本机开机**：漏跑不丢数据——远程数据在被删除前一直保留，下次运行会扫描
   所有"已完成且未删除"的目录自动补拉。
5. **磁盘瞬时翻倍**：staging 与权威库并存；`run` 在合并成功后会清理 staging。
6. **清洗与采集互不干扰**：清洗只读采集产物、写 `kline/` 与 `cleaner/`；两者可同时存在
   （16:00 采集进程已退出，21:00 才再次启动）。

---

## 10. 相关文档

- 需求 / 设计 / 实施 / 验收：[`docs/迭代计划/迭代06-CTP数据清洗/`](../docs/迭代计划/迭代06-CTP数据清洗/)
- 采集需求 / 设计 / 验收 / 整改：[`docs/迭代计划/迭代04-CTP全市场tick数据采集与落盘/`](../docs/迭代计划/迭代04-CTP全市场tick数据采集与落盘/)
- 部署单元：清洗 [`bt_api/bt_api_ctp/deploy/cleaner/`](../bt_api/bt_api_ctp/deploy/cleaner/)；采集 [`bt_api/bt_api_ctp/deploy/collector/`](../bt_api/bt_api_ctp/deploy/collector/)
- 插件说明：[`bt_api/bt_api_ctp/README.md`](../bt_api/bt_api_ctp/README.md)
