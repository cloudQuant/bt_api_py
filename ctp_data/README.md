# ctp_data —— CTP 全市场 tick 采集运行目录

本目录既是**采集数据根目录**，也是**启动入口**。版本库里只保留"怎么跑"的文件，
行情数据、日志、锁文件、本机配置全部由 `.gitignore` 排除。

| 文件 | 是否入库 | 说明 |
|------|---------|------|
| `README.md` | ✅ | 本文档 |
| `start_collector.sh` | ✅ | macOS / Linux 启动脚本 |
| `start_collector.bat` | ✅ | Windows 启动脚本 |
| `collector.example.yaml` | ✅ | 配置模板，不含敏感信息 |
| `.gitignore` | ✅ | 忽略规则 |
| `collector.yaml` | ❌ | 本机配置，从模板复制 |
| `logs/`、`<交易日>/`、`.staging/`、`.locks/` | ❌ | 运行产物 |

> 账号凭证不在本目录：放在**仓库根目录的 `.env`**（同样不入库）。

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

## 5. 自动调度

一个交易日 = **夜盘（前一晚 21:00 起，最晚到次日 02:30）+ 白盘（09:00–15:15）**。
夜盘归属次日：周五晚的夜盘与周一的日盘是同一个交易日，周日晚没有夜盘，
法定节假日前一晚夜盘暂停。

| 时段 | 启动时间 | 命令 |
|------|---------|------|
| 白盘 | 交易日 **08:45** | `start_collector.sh` |
| 夜盘 | 交易日当晚 **20:45** | `COLLECTOR_ARGS='--night --until-close --wait-open' start_collector.sh` |

**为什么提前 15 分钟**：TD 登录 + 六大交易所合约查询 + 全市场约 1.7 万条分批订阅，
实测需要 1.5～4 分钟；压着 21:00 或 09:00 启动会丢掉开盘头几分钟的数据，
而 CTP 行情不可回补。

现成的调度单元（systemd / launchd 已按 08:45、20:45 配好）见
[`bt_api/bt_api_ctp/deploy/collector/`](../bt_api/bt_api_ctp/deploy/collector/)：

```cron
# crontab -e（注意 cron 环境不加载 .env，脚本会自己去读，所以没问题）
45 8  * * 1-5  cd /path/to/bt_api_py && sh ctp_data/start_collector.sh                    >> ctp_data/cron.log 2>&1
45 20 * * 1-5  cd /path/to/bt_api_py && COLLECTOR_ARGS='--night --until-close --wait-open' sh ctp_data/start_collector.sh >> ctp_data/cron.log 2>&1
```

---

## 6. 数据产物

```
ctp_data/
├── <交易日YYYYMMDD>/<交易所>/<合约>.parquet   # 一个合约一个文件
├── <交易日YYYYMMDD>/report.json               # 收盘完整性报告（每合约行数/缺口/覆盖率）
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

排障工具（只读，不干扰采集进程）：

```bash
# 落盘全集 vs 上一交易日订阅全集：三态对账 + 报告/日志摘要
python scripts/reconcile_tick_universe.py \
    --data-root ctp_data --day 20260918 --reference 20260917
```

---

## 8. 相关文档

- 需求 / 设计 / 验收 / 整改：[`docs/迭代计划/迭代04-CTP全市场tick数据采集与落盘/`](../docs/迭代计划/迭代04-CTP全市场tick数据采集与落盘/)
- 部署单元（systemd / launchd）：[`bt_api/bt_api_ctp/deploy/collector/`](../bt_api/bt_api_ctp/deploy/collector/)
- 插件说明：[`bt_api/bt_api_ctp/README.md`](../bt_api/bt_api_ctp/README.md)
