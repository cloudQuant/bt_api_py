# 每日 20:30 外部计划任务模板（仅模拟，未部署）

D67-19 要求每天 Asia/Shanghai 20:30 触发，包括周末和节假日，不使用交易日历过滤。以下为未部署的计划任务模板，入口仅支持 `SIMULATION_ONLY`，并非正式日历更新工具。不要在未完成配置、验收及运行环境检查前安装或启用。调度触发不证明来源已更新、审核完成或交易日切换。未增加应用内调度器。

## 调用前的缺口与明确参数

`bt_api_py.trading_calendar_schedule_once` 是一次性模拟入口：启动前保存原 operation ID、批次身份和上海请求日期；上次结果不明或仍待审核时保留 pending，不会仅因跨日重新采集。需要已经初始化并具有当前模拟版本的可信本地 POSIX 根目录、原始模拟运行 ID、2 至 8 个不重叠的范围、补充输入和运行预算。实际运行可能访问 AKShare，真实来源不因此获得验证；不能把 mock 或请求日期当作真实交易所安排或生效日。

入口不安装计划任务，不自动发送通知或重试。它只约束本入口的协作运行，不阻止旧程序、其他根或直接调用底层采集接口；未知状态须按原 ID 核查，不按 PID、超时或文件缺失盲目重试。旧调度遗留记录、初始化持久化不明、异常数据库／路径等可能需要人工处理；真实定时、断电和跨平台尚未验收。

独立通知程序可使用原任务的 `operation_id` 核对一次已发布的自动更新；以下为不发送邮件的检查示意，操作方须填入自己的路径和原 ID：

```text
<已配置 SDK 的 Python 绝对路径> -m bt_api_py.trading_calendar_notify_cli --calendar-root <日历根目录> --schedule-operation-id <原 operation_id> --trusted-local-posix --dry-run
```

该入口不自动扫描漏通知的历史任务；实际发送需另行显式配置邮件。它使用既有通知日志去重，并在发送前重新核对任务与发布证据；SMTP 接受不证明邮件已经送达。

运行入口及原 ID 查询形状如下；操作方须自行准备符合下方格式、绝对路径且不含凭据的配置，尖括号仅为占位：

```text
<已配置 SDK 的 Python 绝对路径> -m bt_api_py.trading_calendar_schedule_once --config <配置 JSON 绝对路径> --trusted-local-posix
<已配置 SDK 的 Python 绝对路径> -m bt_api_py.trading_calendar_schedule_once --action inspect-active --root <日历根目录> --trusted-local-posix
<已配置 SDK 的 Python 绝对路径> -m bt_api_py.trading_calendar_schedule_once --action inspect --root <日历根目录> --operation-id <原 operation_id> --trusted-local-posix
<已配置 SDK 的 Python 绝对路径> -m bt_api_py.trading_calendar_schedule_once --action reconcile --root <日历根目录> --operation-id <原 operation_id> --trusted-local-posix
```

`inspect-active` 只读检查当前调度任务的待审核状态，不接受 `--config` 或 `--operation-id`，不创建记录或执行审核、发布与恢复。`NONE`、`PENDING_REVIEW`、`UNKNOWN` 分别以退出码 0、10、25 返回；来源与身份未认证，读取锁释放后状态仍可能变化，后续通知等操作必须重新核验。

配置使用 `bt_api.calendar-schedule-once-config/1`，必需字段为 `schema`、`environment`（必须为 `SIMULATION_ONLY`）、绝对路径 `root`、稳定 `job_key`、`original_run_id`、有序 `scopes` 和 `budget_seconds`；可选的 `supplement` 必须为绝对路径，省略时使用 `<root>/supplement.yaml`，显式空值或无效路径不会回退到默认值。`scopes` 与下方 `--scope-json` 格式相同，按 `(venue, kind, product, mock_contract, valid_from, valid_to)` 升序排列。请求日期由本机 UTC 时钟转换为上海自然日，并标记为未验证；原人工待审保留 pending，确认后须按同一 operation ID 核查／结案。操作方需要留存输出与原 ID；锁忙或记录缺失时可能只能得到 UNKNOWN，不能据此创建新 ID 重跑。

仅供核对 CLI 参数的不可执行示意（尖括号和范围 JSON 均须由操作方配置，不能复制运行）：

```text
<绝对路径到已配置 SDK 的 Python> -m bt_api_py.trading_calendar_observation_run mock-batch
  --calendar-root <已初始化的可信本地 POSIX 模拟根目录>
  --run-id <该模拟根目录的原始运行 ID>
  --scope-json <第一个范围的 JSON 对象>
  --scope-json <第二个范围的 JSON 对象>
  --supplement <补充输入文件的绝对路径>
  --batch-budget <大于 0 且不超过 600 的秒数>
  --trusted-local-posix
```

`--scope-json` 可重复 2–8 次；每个对象必须提供 `contract`、`target`、`assumptions`、`alignment` 和 `timeout`，且与初始化的模拟范围及证据匹配；`alignment` 当前须为 `REPEAT_OVER_MOCK_OWNER_DAY_MAPPING`。`--supplement` 可省略，此时读取根目录下 `supplement.yaml`；文件及基线必须存在。`--batch-budget` 默认 600 秒，不是文件系统或整个执行的硬截止。`--trusted-local-posix` 是调用方对目录条件的显式声明，并不使 Windows 文件系统得到支持。

应保留完整标准输出及原始 batch、run、review、publication 和 version ID，按输出状态处理人工审核和结果不明；退出码单独不足以判定成功。可用 `mock-batch-show --calendar-root <同一根目录> --run-id <批次输出的 run_id> --batch-id <原 batch_id> --trusted-local-posix` 检查原批次，其 `VISIBLE_UNAUTHENTICATED` 也不证明来源或提交最终成功；结果仍不明时停止并人工核查，不盲目重跑、重新发布或伪造 ID。上述核查不是已自动接线的恢复流程。

## Linux systemd 模板

下面的 `OnCalendar` 明确指定时区，与主机本地时区无关，通配日期不排除周末或节假日。`Persistent=false` 不要求在关机错过触发后补跑。路径是占位符；未经配置和部署核实，不能启用。

`calendar-check.timer`：

```ini
[Unit]
Description=SIMULATION TEMPLATE - daily Shanghai calendar check

[Timer]
OnCalendar=*-*-* 20:30:00 Asia/Shanghai
Persistent=false
Unit=calendar-check.service

[Install]
WantedBy=timers.target
```

`calendar-check.service`：

```ini
[Unit]
Description=SIMULATION TEMPLATE - daily calendar check

[Service]
Type=oneshot
ExecStart=/ABSOLUTE/PATH/TO/PYTHON -m bt_api_py.trading_calendar_schedule_once --config /ABSOLUTE/PATH/TO/SCHEDULE.json --trusted-local-posix
```

系统需支持 systemd 的日历表达式及 Asia/Shanghai 时区数据；实际版本、唤醒和掉电行为须在目标主机验证。本模板没有配置重启或重试。

## macOS launchd 模板

`StartCalendarInterval` 的小时、分钟按主机本地时区解释；plist 内设置进程 `TZ` 不能改变 launchd 的触发时区。仅当主机系统时区配置为 Asia/Shanghai（或经目标系统验证与之等效）时，下面的 20:30 才对应上海时间。主机使用其他时区时本模板不满足要求；不要直接使用固定 UTC 偏移替代跨时区验证。省略星期与日期表示每天。占位程序不存在，不能启用。

```xml
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
  <key>Label</key><string>example.calendar-check.template</string>
  <key>ProgramArguments</key><array>
    <string>/ABSOLUTE/PATH/TO/PYTHON</string>
    <string>-m</string><string>bt_api_py.trading_calendar_schedule_once</string>
    <string>--config</string><string>/ABSOLUTE/PATH/TO/SCHEDULE.json</string>
    <string>--trusted-local-posix</string>
  </array>
  <key>StartCalendarInterval</key><dict>
    <key>Hour</key><integer>20</integer>
    <key>Minute</key><integer>30</integer>
  </dict>
</dict>
</plist>
```

休眠、关机、时区变更和错过触发后的行为依系统版本与配置核验；此处未在 macOS 上安装或实测任务。

## Windows 任务计划程序模板

Windows 每日触发器的 `StartBoundary` 使用 Windows 本机时区语义；这个示意不提供独立的 IANA `Asia/Shanghai` 时区参数。只有目标主机系统时区设置为与上海时间一致的 Windows 时区并经验证时，`20:30` 才符合要求；其他主机时区不能直接使用。SDK 当前入口要求可信本地 POSIX，**不能把此 Windows 触发器当成可运行的 SDK 集成**。以下是界面配置草图，不是安装命令：

```text
触发器: 每天，20:30:00（所有日期；本机时区须匹配上海）
操作:   <Python 的绝对路径>
参数:   <操作方明确配置；禁止包含凭据>
```

不指定补跑、并发或重试策略；这些行为与时区切换须在 Windows 11 目标环境单独验证。

## 验证范围

一次性入口已有本地 fake 回归；本文件中的任务仍是静态模板，未安装或执行真实计划任务、网络、邮件或账户操作。三平台实际调度、非上海主机验证、正式更新入口、自动通知及安全重试均未完成。

## 一次性模拟覆盖声明报告

以下是未安装计划任务的调用示意；操作方必须提供绝对日历根目录，并声明本机 UTC 时钟的最大误差秒数（0 至 86400，非负且有限）。声明不是时钟认证。默认提前 30 个自然日模拟预警，也可设置 `--threshold-days`。

```text
<已配置 SDK 的 Python 绝对路径> -m bt_api_py.trading_calendar_coverage_cli --root <模拟日历根目录绝对路径> --mode SIMULATION_ONLY --clock-error-seconds <最大误差秒数> --threshold-days 30
```

输出 JSON 和进程退出码：0 表示所有模拟声明均超过阈值；1 表示至少一项模拟告警且无未知；2 表示至少一项未知、起始前或结果不可用。本报告只计算本地模拟覆盖声明，不证明真实健康、来源、完整性或交易资格，也不发送邮件或安装任务。

只读入口要求已有可用的本地存储及锁文件，以非阻塞共享锁读取；锁缺失、锁忙、内容损坏或超限均为 UNKNOWN。读取上限为单文件 8 MiB、累计 64 MiB、2048 条发布历史；限额不提供墙钟超时保证。该入口核对本地收据一致性，不认证收据或数据来源。
