#!/usr/bin/env python3
"""常驻调度器：把「单时段」的采集进程包成一个长期运行的服务。

调度器会在当前交易时段继续采集，或在下一个有效开盘前 ``--lead`` 秒启动
采集进程。交易日与夜盘安排复用采集器配置中的 ``TradingCalendar``。

用法（一般由 service.sh / service.bat 调用，它会准备好解释器与 PYTHONPATH）：

    python ctp_data/service.py
    python ctp_data/service.py --dry-run
    python ctp_data/service.py --lead 900 --retry 2 --retry-backoff 300

``--stop-file`` 提供可由服务管理器创建的优雅停止标记。``--ready-file`` 在服务
验证配置并进入调度循环后写入 JSON 就绪标记，服务退出时会将其删除。

测试钩子：``SERVICE_COLLECTOR_CMD`` 可覆盖被拉起的命令（默认
``python -m bt_api_ctp.collector``），用于离线跑通循环本身。
"""

from __future__ import annotations

import argparse
import contextlib
import json
import logging
import os
import shlex
import signal
import subprocess
import sys
import tempfile
import threading
import time
from datetime import date, datetime, timedelta
from datetime import time as datetime_time
from logging.handlers import RotatingFileHandler
from pathlib import Path
from typing import Any

from bt_api_ctp.collector.cli import _calendar_from_config, load_config, validate_config
from bt_api_ctp.collector.schedule import SESSION_GROUPS, TradingCalendar, group_length_seconds

#: 与 cli.py 的退出码保持一致。
EXIT_OK = 0
EXIT_NOT_TRADING_DAY = 3
EXIT_CONFIG_ERROR = 2

#: 一次会话组：组内第一个会话的开始时刻就是这组的开盘时刻。
_SESSION_STARTS: tuple[tuple[str, int], ...] = tuple(
    (group[0].start_hhmm, index) for index, group in enumerate(SESSION_GROUPS)
)

_logger = logging.getLogger("ctp_service")

#: 收到 SIGTERM/SIGINT 或发现 stop-file 时置位。
_stop = threading.Event()
_stop_file: Path | None = None


def _datetime_on(day: date, hhmm: str) -> datetime:
    hour, minute = (int(part) for part in hhmm.split(":"))
    return datetime(day.year, day.month, day.day, hour, minute)


def _valid_group_open(day: date, group: int, calendar: TradingCalendar) -> bool:
    day_key = day.strftime("%Y%m%d")
    if group == 0:
        return calendar.is_trading_day(day_key)
    return calendar.has_night_session(day_key)


def _current_group_open(now: datetime, calendar: TradingCalendar) -> tuple[datetime, int] | None:
    """Return the open of a group whose collection window currently spans ``now``."""
    day_open = _datetime_on(now.date(), _SESSION_STARTS[0][0])
    day_close = day_open + timedelta(seconds=group_length_seconds(0))
    if day_open <= now < day_close and _valid_group_open(now.date(), 0, calendar):
        return day_open, 0

    wall_time = now.time()
    if wall_time >= datetime_time(21, 0):
        night_day = now.date()
    elif wall_time < datetime_time(2, 30):
        night_day = now.date() - timedelta(days=1)
    else:
        return None

    night_open = _datetime_on(night_day, _SESSION_STARTS[1][0])
    night_close = night_open + timedelta(seconds=group_length_seconds(1))
    if night_open <= now < night_close and _valid_group_open(night_day, 1, calendar):
        return night_open, 1
    return None


def next_group_open(
    now: datetime,
    *,
    after: datetime | None = None,
    calendar: TradingCalendar | None = None,
) -> tuple[datetime, int]:
    """Return the current group open or the earliest valid future group open.

    If ``after`` is supplied, the returned open must be strictly later than it.
    This lets the service advance after a skipped or completed group without
    selecting the same group again while it is still open.
    """
    calendar = calendar or TradingCalendar()
    current = _current_group_open(now, calendar)
    if current is not None and (after is None or current[0] > after):
        return current

    threshold = max(now, after) if after is not None else now
    for offset in range(0, 367):
        day = threshold.date() + timedelta(days=offset)
        for start_hhmm, group in _SESSION_STARTS:
            target = _datetime_on(day, start_hhmm)
            if target <= threshold or not _valid_group_open(day, group, calendar):
                continue
            return target, group
    raise RuntimeError("no valid session open found within 367 days")


def collector_command(
    config: Path,
    *,
    night: bool,
    stop_file: Path | None = None,
    session_day: date | None = None,
) -> list[str]:
    """Build the collection command for one session group."""
    override = os.environ.get("SERVICE_COLLECTOR_CMD")
    command = (
        shlex.split(override, posix=True)
        if override
        else [sys.executable, "-m", "bt_api_ctp.collector"]
    )
    command += ["--config", str(config), "--until-close", "--wait-open"]
    if night:
        command.append("--night")
        if session_day is not None:
            # The session's open date is the collector's calendar key, including
            # after midnight when the local date has already advanced.
            command.extend(["--date", session_day.strftime("%Y%m%d")])
    if stop_file is not None:
        command.extend(["--stop-file", str(stop_file)])
    return command


def _stop_requested() -> bool:
    if _stop.is_set():
        return True
    if _stop_file is not None and _stop_file.exists():
        _stop.set()
        return True
    return False


def _write_stop_marker() -> None:
    if _stop_file is None:
        return
    try:
        _stop_file.touch(exist_ok=True)
    except OSError:
        _logger.exception("could not write stop marker: %s", _stop_file)


def _sleep_interruptibly(seconds: float) -> bool:
    """Sleep in short slices so a signal or stop-file is honoured promptly."""
    deadline = time.monotonic() + max(seconds, 0.0)
    while not _stop_requested():
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            return True
        time.sleep(min(remaining, 0.2))
    return False


def _handle_stop(signum, _frame) -> None:
    """Record shutdown and notify a running collector through the stop marker."""
    _logger.warning("received signal %s: stopping after the current run finalises", signum)
    _stop.set()
    _write_stop_marker()


def _retry_delay(target: datetime, group: int, *, now: datetime, backoff: float) -> float | None:
    """Return a retry delay only when another attempt can start before close."""
    close_at = target + timedelta(seconds=group_length_seconds(group))
    remaining = (close_at - now).total_seconds()
    delay = max(backoff, 0.0)
    if remaining <= 0 or delay >= remaining:
        return None
    return delay


def _effective_start_at(target: datetime, group: int, lead: float) -> datetime:
    """Clamp lead so a run never starts before the target date's prior close."""
    previous_group = 1 if group == 0 else 0
    earliest_hhmm = SESSION_GROUPS[previous_group][-1].end_hhmm
    earliest = _datetime_on(target.date(), earliest_hhmm)
    requested = target - timedelta(seconds=max(lead, 0.0))
    return max(requested, earliest)


def run_collection(command: list[str], *, repo_root: Path) -> int:
    """Run one collection window in its own process; return its exit code."""
    _logger.info("starting collection: %s", " ".join(command))
    started = time.monotonic()
    process_options = {"cwd": str(repo_root), "stdout": sys.stdout, "stderr": sys.stderr}
    if os.name == "nt":
        # A console application spawned by a console-less supervisor otherwise
        # allocates a new visible console of its own.
        process_options["creationflags"] = subprocess.CREATE_NO_WINDOW
    process = subprocess.Popen(  # noqa: S603 - command is service-owned or an explicit offline test hook.
        command, **process_options
    )
    while True:
        try:
            code = process.wait(timeout=0.2)
            break
        except subprocess.TimeoutExpired:
            if _stop_requested():
                _write_stop_marker()
    _logger.info("collection finished: exit=%d elapsed=%.0fs", code, time.monotonic() - started)
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
    parser.add_argument("--stop-file", default=None, help="存在时请求优雅停止")
    parser.add_argument("--ready-file", default=None, help="服务启动后写入并在退出时删除的就绪标记")
    return parser.parse_args(argv)


def _load_scheduler_calendar(
    config: Path, repo_root: Path
) -> tuple[dict[str, Any], TradingCalendar]:
    """Load and validate only what the supervisor needs, without logging config values."""
    payload = load_config(config)
    calendar_payload = payload.get("calendar") or {}
    holidays_file = calendar_payload.get("holidays_file")
    if holidays_file:
        holidays_path = Path(holidays_file)
        if not holidays_path.is_absolute():
            holidays_path = repo_root / holidays_path
        calendar_payload["holidays_file"] = str(holidays_path)
        payload["calendar"] = calendar_payload

    errors = validate_config(payload)
    if errors:
        # Validation details can contain user-supplied values; keep logs free of
        # configuration contents and credentials.
        raise ValueError(f"collector config has {len(errors)} validation error(s)")
    return payload, _calendar_from_config(payload)


def _write_ready_file(path: Path, *, config: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    payload = {
        "pid": os.getpid(),
        "config": str(config),
        "started_at": datetime.now().isoformat(timespec="seconds"),
    }
    temporary.write_text(json.dumps(payload, ensure_ascii=False) + "\n", encoding="utf-8")
    os.replace(temporary, path)


def _run_loop(
    args: argparse.Namespace,
    *,
    config: Path,
    repo_root: Path,
    calendar: TradingCalendar,
    initial_schedule: tuple[datetime, int],
) -> int:
    last_open: datetime | None = None
    scheduled: tuple[datetime, int] | None = initial_schedule
    while not _stop_requested():
        now = datetime.now()
        if scheduled is None:
            scheduled = next_group_open(now, after=last_open, calendar=calendar)
        target, group = scheduled
        scheduled = None
        night = group == 1
        start_at = _effective_start_at(target, group, args.lead)
        launch_at = max(start_at, now)
        _logger.info(
            "next session: %s open at %s (%s), starting at %s",
            "night" if night else "day",
            target.strftime("%Y-%m-%d %H:%M"),
            "夜盘" if night else "白盘",
            launch_at.strftime("%Y-%m-%d %H:%M:%S"),
        )
        if args.dry_run:
            return EXIT_OK
        if not _sleep_interruptibly((launch_at - datetime.now()).total_seconds()):
            break
        close_at = target + timedelta(seconds=group_length_seconds(group))
        if datetime.now() >= close_at:
            _logger.info("scheduled session has closed; recalculating the next session")
            continue

        attempt = 0
        while not _stop_requested():
            command = collector_command(
                config,
                night=night,
                stop_file=_stop_file,
                session_day=target.date(),
            )
            code = run_collection(command, repo_root=repo_root)
            if code in (EXIT_OK, EXIT_NOT_TRADING_DAY):
                break
            if code == EXIT_CONFIG_ERROR:
                _logger.error("配置错误（exit=2）：常驻服务退出，请修复后重启")
                return code
            if _stop_requested():
                break
            attempt += 1
            if attempt > args.retry:
                _logger.error("本时段采集失败 %d 次，放弃并跳到下一时段", attempt)
                break
            retry_delay = _retry_delay(
                target,
                group,
                now=datetime.now(),
                backoff=args.retry_backoff,
            )
            if retry_delay is None:
                _logger.warning("当前时段已收盘或剩余时间不足，不再重试")
                break
            _logger.warning(
                "采集失败（exit=%d），%d/%d 次重试前等待 %.0fs",
                code,
                attempt,
                args.retry,
                retry_delay,
            )
            if not _sleep_interruptibly(retry_delay):
                break

        last_open = target
        if args.once or _stop_requested():
            break

    return EXIT_OK


def main(argv: list[str] | None = None) -> int:
    global _stop_file
    args = _parse_args(argv)
    script_dir = Path(__file__).resolve().parent
    repo_root = script_dir.parent
    config = Path(args.config) if args.config else script_dir / "collector.yaml"
    config = config.resolve()
    ready_file = Path(args.ready_file).resolve() if args.ready_file else None
    if ready_file is not None:
        with contextlib.suppress(OSError):
            ready_file.unlink()

    _configure_logging("INFO", Path(args.log_file).resolve() if args.log_file else None)
    if not config.exists():
        _logger.error("配置不存在：%s（请先复制 collector.example.yaml）", config)
        return EXIT_CONFIG_ERROR
    if args.lead < 0 or args.retry < 0 or args.retry_backoff < 0:
        _logger.error("lead、retry 与 retry-backoff 必须为非负数")
        return EXIT_CONFIG_ERROR

    try:
        _payload, calendar = _load_scheduler_calendar(config, repo_root)
    except Exception:
        _logger.error("配置或交易日历无法读取或验证，请检查配置文件", exc_info=False)
        return EXIT_CONFIG_ERROR

    supplied_stop_file = args.stop_file is not None
    if supplied_stop_file:
        stop_file = Path(args.stop_file).resolve()
        stop_file.parent.mkdir(parents=True, exist_ok=True)
    else:
        handle, path = tempfile.mkstemp(prefix="ctp-service-", suffix=".stop")
        os.close(handle)
        stop_file = Path(path)
        stop_file.unlink()

    _stop.clear()
    _stop_file = stop_file
    _logger.info(
        "service up: config=%s lead=%.0fs retry=%d backoff=%.0fs",
        config,
        args.lead,
        args.retry,
        args.retry_backoff,
    )

    previous_handlers: dict[int, Any] = {}
    try:
        for signum in (signal.SIGTERM, signal.SIGINT):
            previous_handlers[signum] = signal.getsignal(signum)
            signal.signal(signum, _handle_stop)
        if _stop_requested():
            return EXIT_OK
        try:
            initial_schedule = next_group_open(datetime.now(), calendar=calendar)
        except Exception as exc:
            _logger.error("交易时段无法计算（%s），请检查交易日历", type(exc).__name__)
            return EXIT_CONFIG_ERROR
        if ready_file is not None and not _stop_requested():
            _write_ready_file(ready_file, config=config)
        return _run_loop(
            args,
            config=config,
            repo_root=repo_root,
            calendar=calendar,
            initial_schedule=initial_schedule,
        )
    finally:
        for signum, handler in previous_handlers.items():
            signal.signal(signum, handler)
        if ready_file is not None:
            with contextlib.suppress(OSError):
                ready_file.unlink()
        if not supplied_stop_file:
            with contextlib.suppress(OSError):
                stop_file.unlink()
        _stop_file = None
        _logger.info("service stopped")


if __name__ == "__main__":
    raise SystemExit(main())
