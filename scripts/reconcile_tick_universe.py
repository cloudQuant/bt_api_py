#!/usr/bin/env python3
"""Reconcile the collected tick universe: what we subscribed to vs what landed.

Read-only diagnostic for the iteration-04 CTP collector.

``ParquetSink.finalize()`` builds ``report.json`` by scanning the Parquet files
that actually exist, so a contract with zero rows -- or an entire missing
exchange -- simply does not appear anywhere.  This tool answers the question
the report cannot: which contracts produced no data at all, and whether that is
expected (a product with no night session) or suspicious.

Inputs (all optional except ``--data-root`` / ``--day``):

* persisted set: ``<data-root>/<day>/<EXCHANGE>/<instrument>.parquet`` footers
* reference universe: ``<data-root>/<reference>/report.json`` or ``--universe``
* run log: ``<data-root>/logs/collector-<day>.log`` or ``--log``
* close report: ``<data-root>/<day>/report.json``, digested when present

Nothing is written unless ``--json-out`` is given.

Usage:
    python scripts/reconcile_tick_universe.py \
        --data-root /Users/yunjinqi/tick_data --day 20260918 --reference 20260917
"""

from __future__ import annotations

import argparse
import json
import re
from collections import Counter, defaultdict
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any

import pyarrow.parquet as pq

_PRODUCT_RE = re.compile(r"^([A-Za-z]+)")
#: DCE/CFFEX style ``-C-`` plus CZCE style ``509C5000``.
_OPTION_RE = re.compile(r"-C-|-P-|\dC\d|\dP\d")

_HEARTBEAT_RE = re.compile(
    r"^(\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2}),\d+ .*heartbeat: "
    r"received=(\d+) buffered=(\d+) dropped=(\d+)"
)
_FLUSH_RE = re.compile(r"flushed (\d+) instruments / (\d+) ticks")
_COMPACT_RE = re.compile(r"compacted (\d+) staging segment\(s\) into (\d+) instrument file\(s\)")
_SUBSCRIBED_RE = re.compile(r"subscribed (\d+) instruments")
_UNIVERSE_RE = re.compile(r"requested=(\d+) acked=(\d+) failed=(\d+) timed_out=(\d+)")
_ALARM = "collection health alarm:"


def product_of(instrument_id: str) -> str:
    """Return the product prefix (``rb2701`` -> ``RB``, ``IO2509-C-4000`` -> ``IO``)."""
    match = _PRODUCT_RE.match(instrument_id)
    return match.group(1).upper() if match else "?"


def is_option(instrument_id: str) -> bool:
    """Best-effort option detection across the exchanges' naming styles."""
    return bool(_OPTION_RE.search(instrument_id))


@dataclass
class Persisted:
    """What is on disk for one trading day."""

    #: exchange -> instrument -> row count (-1 when the footer is unreadable).
    rows: dict[str, dict[str, int]] = field(default_factory=dict)

    def instruments(self, exchange: str) -> set[str]:
        return set(self.rows.get(exchange, {}))

    @property
    def total_files(self) -> int:
        return sum(len(entries) for entries in self.rows.values())

    @property
    def total_rows(self) -> int:
        return sum(sum(entries.values()) for entries in self.rows.values())


def scan_persisted(day_dir: Path) -> Persisted:
    """Read every final Parquet footer under ``<day>/<exchange>/``."""
    persisted = Persisted()
    if not day_dir.is_dir():
        return persisted
    for exchange_dir in sorted(path for path in day_dir.iterdir() if path.is_dir()):
        counts: dict[str, int] = {}
        for path in sorted(exchange_dir.glob("*.parquet")):
            try:
                counts[path.stem] = pq.ParquetFile(path).metadata.num_rows
            except Exception:  # pragma: no cover - 坏文件不应中断对账
                counts[path.stem] = -1
        if counts:
            persisted.rows[exchange_dir.name] = counts
    return persisted


def load_universe(path: Path) -> dict[str, set[str]]:
    """Load a reference universe from a ``report.json`` or an explicit list.

    Accepts either ``{"instruments": [{"exchange_id", "instrument_id"}, ...]}``
    or ``["SHFE/rb2701", ...]``.
    """
    payload = json.loads(path.read_text(encoding="utf-8"))
    universe: dict[str, set[str]] = defaultdict(set)
    if isinstance(payload, dict) and "instruments" in payload:
        for entry in payload["instruments"]:
            exchange = entry.get("exchange_id")
            instrument = entry.get("instrument_id")
            if exchange and instrument:
                universe[str(exchange)].add(str(instrument))
    elif isinstance(payload, list):
        for item in payload:
            exchange, _, instrument = str(item).partition("/")
            if instrument:
                universe[exchange].add(instrument)
    else:
        raise ValueError(f"unsupported universe format: {path}")
    if not universe:
        raise ValueError(f"universe is empty: {path}")
    return dict(universe)


def kind_of(instrument_id: str) -> str:
    """``future`` or ``option`` -- the granularity of a night-session schedule."""
    return "option" if is_option(instrument_id) else "future"


def classify_missing(
    universe: dict[str, set[str]], persisted: Persisted
) -> tuple[dict[str, list[str]], dict[str, list[str]], list[tuple[str, str]], dict[str, int]]:
    """Split reference contracts into missing-in-full-category vs individual.

    The category is ``EXCHANGE/product/future|option`` because an exchange sets
    trading hours per product *and* per kind: DCE quotes ``bz`` futures at night
    while its option series may be entirely quiet, and calling that "individual
    misses" would drown the real signal.  A category with no data at all is the
    expected shape for a product that does not trade at night; a category where
    most contracts landed but a few did not is the suspicious one.

    Returns ``(whole_category, individual, extras, category_totals)``; the
    totals let the caller print a missing *share*, which separates "this whole
    category is quiet" from "two contracts out of seven hundred".
    """
    whole: dict[str, list[str]] = defaultdict(list)
    individual: dict[str, list[str]] = defaultdict(list)
    extras: list[tuple[str, str]] = []
    totals: Counter[str] = Counter()
    for exchange, wanted in sorted(universe.items()):
        have = persisted.instruments(exchange)
        missing = sorted(wanted - have)
        for instrument in wanted:
            totals[f"{exchange}/{product_of(instrument)}/{kind_of(instrument)}"] += 1
        categories_with_data = {f"{product_of(name)}/{kind_of(name)}" for name in have}
        for instrument in missing:
            key = f"{exchange}/{product_of(instrument)}/{kind_of(instrument)}"
            if f"{product_of(instrument)}/{kind_of(instrument)}" in categories_with_data:
                individual[key].append(instrument)
            else:
                whole[key].append(instrument)
        for name in sorted(have - wanted):
            extras.append((exchange, name))
    return dict(whole), dict(individual), extras, dict(totals)


def digest_report(report_path: Path) -> dict[str, Any]:
    """Summarise a collector ``report.json`` (32 MB for a full market)."""
    payload = json.loads(report_path.read_text(encoding="utf-8"))
    instruments = payload.get("instruments") or []
    rows = [int(entry.get("rows") or 0) for entry in instruments]
    coverage = [
        float(entry["coverage"]) for entry in instruments if entry.get("coverage") is not None
    ]
    gaps = [
        float(entry["max_gap_seconds"])
        for entry in instruments
        if entry.get("max_gap_seconds") is not None
    ]
    volume_jumps = sum(int(entry.get("volume_jumps") or 0) for entry in instruments)
    return {
        "generated_at": payload.get("generated_at"),
        "trading_day": payload.get("trading_day"),
        "instruments": len(instruments),
        "rows": sum(rows),
        "single_row_instruments": sum(1 for count in rows if count == 1),
        "zero_row_instruments": sum(1 for count in rows if count == 0),
        "with_coverage": len(coverage),
        "coverage_median": _median(coverage),
        "coverage_below_90pct": sum(1 for value in coverage if value < 0.9),
        "max_gap_seconds_max": max(gaps) if gaps else None,
        "volume_jumps": volume_jumps,
        "ticks_outside_session": payload.get("ticks_outside_session"),
        "dropped_ticks": payload.get("dropped_ticks"),
        "disconnects": len(payload.get("disconnects") or []),
        "connection_generations": len(payload.get("connection_generations") or []),
        "resubscribes": len(payload.get("resubscribes") or []),
        "failed_instruments": len(payload.get("failed_instruments") or {}),
        "compactions": payload.get("compactions"),
        "compaction_failures": payload.get("compaction_failures"),
        "pending_segments": payload.get("pending_segments"),
        "compaction_last_error": payload.get("compaction_last_error"),
        "exchanges": dict(Counter(entry.get("exchange_id") for entry in instruments)),
    }


def digest_log(path: Path) -> dict[str, Any]:
    """Extract the runtime signals that only exist in the log file."""
    heartbeats: list[tuple[datetime, int, int]] = []
    alarms: Counter[str] = Counter()
    flush_instruments = 0
    flush_ticks = 0
    flushes = 0
    compactions: list[int] = []
    flush_failures = 0
    tracebacks = 0
    subscribed = 0
    universe: tuple[int, int, int, int] | None = None
    not_ready: list[str] = []

    for raw in path.read_text(encoding="utf-8", errors="replace").splitlines():
        match = _HEARTBEAT_RE.match(raw)
        if match:
            moment = datetime.strptime(match.group(1), "%Y-%m-%d %H:%M:%S")
            heartbeats.append((moment, int(match.group(2)), int(match.group(3))))
            continue
        if _ALARM in raw:
            alarms[raw.split(_ALARM, 1)[1].strip()] += 1
            continue
        match = _SUBSCRIBED_RE.search(raw)
        if match:
            subscribed = int(match.group(1))
            continue
        match = _UNIVERSE_RE.search(raw)
        if match:
            universe = tuple(int(group) for group in match.groups())
            continue
        if "subscribe not ready" in raw:
            not_ready.append(raw.strip())
            continue
        match = _FLUSH_RE.search(raw)
        if match:
            flushes += 1
            flush_instruments += int(match.group(1))
            flush_ticks += int(match.group(2))
            continue
        match = _COMPACT_RE.search(raw)
        if match:
            compactions.append(int(match.group(2)))
            continue
        if "flush failed" in raw:
            flush_failures += 1
        elif "Traceback" in raw:
            tracebacks += 1

    deltas = [
        (current[0] - previous[0]).total_seconds()
        for previous, current in zip(heartbeats, heartbeats[1:], strict=False)
    ]
    median = _median(deltas)
    return {
        "heartbeats": len(heartbeats),
        "last_heartbeat": heartbeats[-1][0].isoformat(sep=" ") if heartbeats else None,
        "received": heartbeats[-1][1] if heartbeats else None,
        "buffered": heartbeats[-1][2] if heartbeats else None,
        "heartbeat_median_sec": median,
        "heartbeat_max_sec": max(deltas) if deltas else None,
        "heartbeat_gaps_over_2x": sum(1 for value in deltas if median and value > 2 * median),
        "alarms": dict(alarms),
        "flush_batches": flushes,
        "flushed_instruments": flush_instruments,
        "flushed_ticks": flush_ticks,
        "flush_failures": flush_failures,
        "tracebacks": tracebacks,
        "subscribed": subscribed,
        "subscribe_ack": universe,
        "subscribe_not_ready": not_ready,
        "compactions": compactions,
    }


def _median(values: list[float]) -> float | None:
    if not values:
        return None
    ordered = sorted(values)
    return ordered[len(ordered) // 2]


def _print_section(title: str) -> None:
    print(f"\n=== {title} ===")


def _fmt(value: Any, suffix: str = "") -> str:
    """Render ``None`` as ``-`` so missing fields do not print as ``Nones``."""
    return "-" if value is None else f"{value}{suffix}"


def _print_grouped(
    groups: dict[str, list[str]], totals: dict[str, int], limit: int, sort_by_share: bool = False
) -> None:
    def share(key: str) -> float:
        return len(groups[key]) / totals.get(key, len(groups[key]))

    keys = sorted(groups, key=share, reverse=True) if sort_by_share else sorted(groups)
    for key in keys:
        names = groups[key]
        total = totals.get(key, len(names))
        sample = ", ".join(names[:limit])
        suffix = f" ... (+{len(names) - limit})" if len(names) > limit else ""
        print(f"  {key:26} {len(names):>5}/{total:<5} ({share(key):>5.0%}): {sample}{suffix}")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--data-root", required=True, help="collector data_root")
    parser.add_argument("--day", required=True, help="trading day to check, YYYYMMDD")
    parser.add_argument(
        "--reference",
        default=None,
        help="trading day whose report.json supplies the subscribed universe",
    )
    parser.add_argument("--universe", default=None, help="explicit universe JSON file")
    parser.add_argument("--log", default=None, help="collector log file")
    parser.add_argument("--sample", type=int, default=8, help="names printed per group")
    parser.add_argument("--json-out", default=None, help="also write the result as JSON")
    parser.add_argument("--strict", action="store_true", help="exit 1 when something looks wrong")
    args = parser.parse_args(argv)

    root = Path(args.data_root).expanduser()
    day_dir = root / args.day
    persisted = scan_persisted(day_dir)

    live = not (day_dir / "report.json").exists()
    _print_section(f"落盘快照 {args.day}" + ("（采集未收盘，数字会继续增长）" if live else ""))
    if not persisted.rows:
        print(f"  {day_dir} 下没有任何 parquet 文件")
    for exchange in sorted(persisted.rows):
        entries = persisted.rows[exchange]
        rows = sum(entries.values())
        print(f"  {exchange:6} 合约 {len(entries):>6}  行数 {rows:>10}")

    problems: list[str] = []
    result: dict[str, Any] = {
        "day": args.day,
        "persisted_files": persisted.total_files,
        "persisted_rows": persisted.total_rows,
        "persisted_by_exchange": {
            exchange: len(entries) for exchange, entries in sorted(persisted.rows.items())
        },
    }

    if persisted.total_files == 0:
        problems.append("没有任何落盘文件")

    universe: dict[str, set[str]] | None = None
    if args.universe:
        universe = load_universe(Path(args.universe).expanduser())
        source = args.universe
    elif args.reference:
        reference_path = root / args.reference / "report.json"
        if not reference_path.exists():
            print(f"\n未找到参考全集 {reference_path}，跳过对账")
        else:
            universe = load_universe(reference_path)
            source = str(reference_path)
    else:
        source = None
        print("\n未提供 --reference / --universe，跳过对账（只做落盘与报告摘要）")

    if universe is not None:
        wanted = sum(len(names) for names in universe.values())
        whole, individual, extras, totals = classify_missing(universe, persisted)
        _print_section(f"对账：参考全集 {wanted} 个合约（来源 {source}）")
        missing_total = sum(len(names) for names in whole.values()) + sum(
            len(names) for names in individual.values()
        )
        print(
            f"  已有数据 {wanted - missing_total}  完全缺失 {missing_total}  参考集外新增 {len(extras)}"
        )
        if whole:
            print("\n  [1] 整个「交易所/品种/期货或期权」类别缺失：该类今晚无任何数据")
            print(
                "      → 多为无夜盘品种/无夜盘行情，但正是 report.json 不会提示的一类，需人工确认"
            )
            _print_grouped(whole, totals, args.sample)
        if individual:
            print("\n  [2] 个别缺失：同类其余合约有数据（更可疑，需核对该合约是否本就不活跃）")
            _print_grouped(individual, totals, args.sample, sort_by_share=True)
        if extras:
            sample = ", ".join(f"{ex}/{name}" for ex, name in extras[: args.sample])
            print(f"\n  [3] 参考集外新增（次日新挂牌等）{len(extras)} 个: {sample}")
        result.update(
            {
                "reference_source": source,
                "reference_universe": wanted,
                "missing_whole_product": {key: len(v) for key, v in whole.items()},
                "missing_individual": {key: len(v) for key, v in individual.items()},
                "extras": len(extras),
            }
        )
        if individual:
            problems.append(
                f"有 {sum(len(v) for v in individual.values())} 个合约在其同类合约有数据的情况下缺失"
            )

    report_path = day_dir / "report.json"
    if report_path.exists():
        digest = digest_report(report_path)
        _print_section("收盘报告 report.json")
        print(f"  生成时间 {digest['generated_at']}  trading_day={digest['trading_day']}")
        print(
            f"  合约 {digest['instruments']}  行数 {digest['rows']}  "
            f"交易所 {digest['exchanges']}"
        )
        print(
            f"  单条快照合约 {digest['single_row_instruments']}（coverage 记为 null，"
            f"不参与质量评分）"
        )
        print(
            f"  有 coverage 的合约 {digest['with_coverage']}  "
            f"中位数 {_fmt(digest['coverage_median'])}  "
            f"低于 0.9 的 {digest['coverage_below_90pct']}"
        )
        print(
            f"  最大同会话缺口 {_fmt(digest['max_gap_seconds_max'])}s  "
            f"成交量跳变合计 {digest['volume_jumps']}"
        )
        print(
            f"  非交易时段丢弃 {_fmt(digest['ticks_outside_session'])}  "
            f"缓冲丢弃 {digest['dropped_ticks']}"
        )
        print(
            f"  断线窗口 {digest['disconnects']}  代次变化 {digest['connection_generations']}  "
            f"重订阅 {digest['resubscribes']}  失败合约 {digest['failed_instruments']}"
        )
        print(
            f"  压缩 {_fmt(digest['compactions'])} 次  失败 {_fmt(digest['compaction_failures'])}  "
            f"残留段 {_fmt(digest['pending_segments'])}  最近错误 {_fmt(digest['compaction_last_error'])}"
        )
        result["report"] = digest
        if digest["dropped_ticks"]:
            problems.append(f"缓冲区丢弃了 {digest['dropped_ticks']} 条 tick")
        if digest["compaction_failures"] or digest["pending_segments"]:
            problems.append(
                f"压缩未收尾：失败 {digest['compaction_failures']}、残留段 {digest['pending_segments']}"
            )
        if digest["ticks_outside_session"]:
            problems.append(
                f"有 {digest['ticks_outside_session']} 条 tick 因时间戳不在会话表内被丢弃，"
                f"需核对是否含应交易品种"
            )
        if digest["single_row_instruments"]:
            problems.append(
                f"{digest['single_row_instruments']} 个合约只有 1 条快照，其 coverage 为 null"
            )
    else:
        print(f"\n（{report_path} 尚未生成，收盘后重跑本脚本可得到完整指标）")

    log_path = (
        Path(args.log).expanduser() if args.log else (root / "logs" / f"collector-{args.day}.log")
    )
    if log_path.exists():
        digest = digest_log(log_path)
        _print_section(f"运行日志摘要 {log_path.name}")
        print(
            f"  心跳 {digest['heartbeats']} 次  最后 {digest['last_heartbeat']}  "
            f"received={digest['received']} buffered={digest['buffered']}"
        )
        print(
            f"  心跳间隔 中位数 {digest['heartbeat_median_sec']}s  "
            f"最大 {digest['heartbeat_max_sec']}s  "
            f"超过 2 倍中位数的空档 {digest['heartbeat_gaps_over_2x']} 次"
        )
        print(
            f"  订阅 {digest['subscribed']}  acked/请求/失败/超时 {_fmt(digest['subscribe_ack'])}"
        )
        for line in digest["subscribe_not_ready"]:
            print(f"    WARN {line}")
        print(
            f"  刷盘 {digest['flush_batches']} 批 / {digest['flushed_ticks']} 条 tick"
            f"（涉及 {digest['flushed_instruments']} 次合约-批）"
        )
        print(f"  刷盘失败 {digest['flush_failures']}  异常栈 {digest['tracebacks']}")
        print(f"  压缩批次 {len(digest['compactions'])}  写入合约文件数 {digest['compactions']}")
        if digest["alarms"]:
            print("  健康告警：")
            for reason, count in sorted(digest["alarms"].items(), key=lambda kv: -kv[1]):
                print(f"    {count:>4} 次  {reason}")
        result["log"] = digest
        if digest["flush_failures"]:
            problems.append(f"刷盘失败 {digest['flush_failures']} 次（整批回灌，存在活锁风险）")
        if digest["tracebacks"]:
            problems.append(f"日志中有 {digest['tracebacks']} 处异常栈")
        acked = digest["subscribe_ack"]
        if acked and acked[0] and acked[0] > 2000 and acked[3] > acked[0] // 2:
            problems.append(
                f"订阅就绪判据失真：{acked[3]} 个 timed_out 只因 ACK 等待窗口过短，"
                f"全市场启动时必然出现"
            )
        if digest["alarms"] and digest["received"] and digest["flushed_ticks"]:
            problems.append(
                f"运行期触发过健康告警 {sum(digest['alarms'].values())} 次，"
                f"需确认是真的停摆还是参考集伪快照导致的误报"
            )
    else:
        print(f"\n（未找到日志 {log_path}）")

    _print_section("结论")
    if problems:
        for index, problem in enumerate(problems, start=1):
            print(f"  [{index}] {problem}")
    else:
        print("  未发现异常")
    result["problems"] = problems

    if args.json_out:
        out = Path(args.json_out).expanduser()
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
        print(f"\n结果已写入 {out}")

    return 1 if (problems and args.strict) else 0


if __name__ == "__main__":
    raise SystemExit(main())
