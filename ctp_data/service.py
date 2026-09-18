#!/usr/bin/env python3
"""常驻调度器：把「单时段」的采集进程包成一个长期运行的服务。

采集进程本身刻意是单时段的（跑到本组收盘就退出，见 deploy/collector/README.md）。
本脚本负责长期活着：

    算下一个交易时段的开盘 -> 提前 --lead 秒拉起一次采集 -> 等它退出
    -> 按退出码决定「跳到下一时段 / 短退避重试 / 直接失败」-> 循环

时段表直接复用 ``bt_api_ctp.collector.schedule.SESSION_GROUPS``（组首会话的
开始时刻即开盘时刻），不在这里重抄一份。是否交易日、当晚有没有夜盘仍由采集
进程自己判断（退出码 3 = 正常跳过），本脚本不重复实现日历逻辑。

用法（一般由 service.sh / service.bat 调用，它会准备好解释器与 PYTHONPATH）：

    python ctp_data/service.py                    # 常驻
    python ctp_data/service.py --dry-run          # 只打印调度计划，不启动采集
    python ctp_data/service.py --lead 900 --retry 2 --retry-backoff 300

测试钩子：``SERVICE_COLLECTOR_CMD`` 可覆盖被拉起的命令（默认
``python -m bt_api_ctp.collector``），用于离线跑通循环本身。
"""

from __future__ import annotations

import argparse
import contextlib
import logging
import os
import signal
import subprocess
import sys
import threading
import time
from datetime import datetime, timedelta
from logging.handlers import RotatingFileHandler
from pathlib import Path

from bt_api_ctp.collector.schedule import SESSION_GROUPS

#: 与 cli.py 的退出码保持一致。
EXIT_OK = 0
EXIT_NOT_TRADING_DAY = 3
EXIT_CONFIG_ERROR = 2

#: 一次会话组：组内第一个会话的开始时刻就是这组的开盘时刻。
_SESSION_STARTS: tuple[tuple[str, int], ...] = tuple(
    (group[0].start_hhmm, index) for index, group in enumerate(SESSION_GROUPS)
)

_logger = logging.getLogger("ctp_service")

#: 收到 SIGTERM/SIGINT 时置位；同时用于把优雅停机转达给正在跑的采集子进程。
_stop = threading.Event()
_child: subprocess.Popen | None = None


def next_group_open(now: datetime, *, after: datetime | None = None) -> tuple[datetime, int]:
    """Return the earliest session-group open strictly after ``after or now``.

    ``after`` exists so the caller can force progress: after handling one open,
    passing it back guarantees the next answer is a *later* open instead of the
    same one again (which would spin when a run is skipped before its open).
    """
    reference = after or now
    best: tuple[datetime, int] | None = None
    for offset in (0, 1):
        day = (reference + timedelta(days=offset)).date()
        for start_hhmm, group in _SESSION_STARTS:
            hour, minute = start_hhmm.split(":")
            moment = datetime(day.year, day.month, day.day, int(hour), int(minute))
            if moment > reference and (best is None or moment < best[0]):
                best = (moment, group)
    if best is None:  # pragma: no cover - 两天内必有时段
        raise RuntimeError("no session open found within two days")
    return best


def collector_command(config: Path, *, night: bool) -> list[str]:
    """Build the collection command for one session group."""
    override = os.environ.get("SERVICE_COLLECTOR_CMD")
    command = override.split() if override else [sys.executable, "-m", "bt_api_ctp.collector"]
    command += ["--config", str(config), "--until-close", "--wait-open"]
    if night:
        command.append("--night")
    return command


def _sleep_interruptibly(seconds: float) -> bool:
    """Sleep in small slices so a stop request is honoured quickly.

    Returns ``False`` when a stop was requested while waiting.
    """
    deadline = time.monotonic() + max(seconds, 0.0)
    while not _stop.is_set():
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            return True
        time.sleep(min(remaining, 5.0))
    return False


def _handle_stop(signum, _frame) -> None:
    """Ask the running collector to shut down gracefully, then stop looping.

    The collector only installs a SIGINT handler, so a service manager's
    SIGTERM must be translated rather than forwarded verbatim -- otherwise the
    run would be killed mid-flight and never write its report.
    """
    _logger.warning("received signal %s: stopping after the current run finalises", signum)
    _stop.set()
    child = _child
    if child is not None and child.poll() is None:
        with contextlib.suppress(OSError):  # pragma: no cover - 子进程刚好退出
            child.send_signal(signal.SIGINT)


def run_collection(command: list[str], *, repo_root: Path) -> int:
    """Run one collection window in its own process; return its exit code."""
    global _child
    _logger.info("starting collection: %s", " ".join(command))
    started = time.monotonic()
    process = subprocess.Popen(  # noqa: S603 - 命令由本文件的 sys.executable 与配置路径拼成，非外部输入。
        command, cwd=str(repo_root)
    )
    _child = process
    try:
        code = process.wait()
    finally:
        _child = None
    _logger.info(
        "collection finished: exit=%d elapsed=%.0fs", code, time.monotonic() - started
    )
    return code


def _configure_logging(level: str, log_file: Path | None) -> None:
    handlers: list[logging.Handler] = [logging.StreamHandler(sys.stderr)]
    if log_file is not None:
        log_file.parent.mkdir(parents=True, exist_ok=True)
        handlers.append(RotatingFileHandler(log_file, maxBytes=5_000_000, backupCount=3))
    logging.basicConfig(
        level=level,
        format="%(asctime)s %(levelname)s %(message)s",
        handlers=handlers,
        force=True,
    )


def _parse_args(argv: list[str] | None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(prog="ctp_service", description=__doc__.splitlines()[0])
    parser.add_argument("--config", default=None, help="采集配置，默认 <脚本目录>/collector.yaml")
    parser.add_argument(
        "--lead", type=float, default=900.0, help="开盘前多少秒启动采集（默认 900）"
    )
    parser.add_argument("--retry", type=int, default=2, help="单个时段采集失败后的重试次数")
    parser.add_argument(
        "--retry-backoff", type=float, default=300.0, help="重试前的等待秒数（默认 300）"
    )
    parser.add_argument("--log-file", default=None, help="额外写入这个日志文件（5MB × 3 轮转）")
    parser.add_argument("--dry-run", action="store_true", help="只打印调度计划，不启动采集")
    parser.add_argument(
        "--once", action="store_true", help="只处理下一个时段一次，然后退出（便于验证）"
    )
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = _parse_args(argv)
    script_dir = Path(__file__).resolve().parent
    repo_root = script_dir.parent
    config = Path(args.config) if args.config else script_dir / "collector.yaml"
    _configure_logging("INFO", Path(args.log_file) if args.log_file else None)

    if not config.exists():
        _logger.error("配置不存在：%s（请先复制 collector.example.yaml）", config)
        return EXIT_CONFIG_ERROR

    signal.signal(signal.SIGTERM, _handle_stop)
    signal.signal(signal.SIGINT, _handle_stop)

    _logger.info(
        "service up: config=%s lead=%.0fs retry=%d backoff=%.0fs",
        config,
        args.lead,
        args.retry,
        args.retry_backoff,
    )

    last_open: datetime | None = None
    while not _stop.is_set():
        target, group = next_group_open(datetime.now(), after=last_open)
        night = group == 1
        start_at = target - timedelta(seconds=args.lead)
        _logger.info(
            "next session: %s open at %s (%s), starting at %s",
            "night" if night else "day",
            target.strftime("%Y-%m-%d %H:%M"),
            "夜盘" if night else "白盘",
            start_at.strftime("%Y-%m-%d %H:%M:%S"),
        )
        if args.dry_run:
            last_open = target
            if args.once:
                break
            continue
        if not _sleep_interruptibly((start_at - datetime.now()).total_seconds()):
            break

        attempt = 0
        while True:
            code = run_collection(collector_command(config, night=night), repo_root=repo_root)
            if code in (EXIT_OK, EXIT_NOT_TRADING_DAY):
                break
            if code == EXIT_CONFIG_ERROR:
                _logger.error("配置错误（exit=2）：常驻服务退出，请修复后重启")
                return code
            attempt += 1
            if attempt > args.retry:
                _logger.error("本时段采集失败 %d 次，放弃并跳到下一时段", attempt)
                break
            _logger.warning("采集失败（exit=%d），%d/%d 次重试前等待 %.0fs", code, attempt, args.retry, args.retry_backoff)
            if not _sleep_interruptibly(args.retry_backoff):
                break

        last_open = target
        if args.once or _stop.is_set():
            break

    _logger.info("service stopped")
    return EXIT_OK


if __name__ == "__main__":
    raise SystemExit(main())
