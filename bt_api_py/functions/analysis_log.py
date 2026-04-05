"""Analyze log timing data without side effects during import."""

from __future__ import annotations

import re
from datetime import datetime
from pathlib import Path

import pandas as pd

try:
    from pyecharts import options as opts
    from pyecharts.charts import Boxplot, Page
    from pyecharts.components import Table
    from pyecharts.options import ComponentTitleOpts

    PYECHARTS_AVAILABLE = True
except ImportError:  # pragma: no cover - optional dependency
    PYECHARTS_AVAILABLE = False
    opts = None  # type: ignore[assignment]
    Boxplot = None  # type: ignore[assignment,misc]
    Page = None  # type: ignore[assignment,misc]
    Table = None  # type: ignore[assignment,misc]
    ComponentTitleOpts = None  # type: ignore[assignment,misc]

from bt_api_py.functions import get_package_path

TIME_PATTERN = re.compile(
    r"deal trade_data, time = (\d{4}-\d{1,2}-\d{1,2}\s\d{1,2}:\d{1,2}:\d{1,2}.\d{1,6})"
)


def _require_package_path(package_name: str) -> Path:
    package_path = get_package_path(package_name)
    if package_path is None:
        raise RuntimeError(f"Package path for '{package_name}' is not available")
    return Path(package_path)


def time_subtraction(start_time_str: str, end_time_str: str) -> float:
    """Return the elapsed milliseconds between two log timestamps."""
    start_time = datetime.strptime(start_time_str, "%Y-%m-%d %H:%M:%S.%f")
    end_time = datetime.strptime(end_time_str, "%Y-%m-%d %H:%M:%S.%f")
    return (end_time - start_time).total_seconds() * 1000


def extract_slam_times(log_filename: Path) -> list[str]:
    """Extract ordered timestamps from the raw log file."""
    all_slam_times: list[str] = []
    with log_filename.open(encoding="utf-8") as fp:
        for line in fp:
            all_slam_times.extend(re.findall(TIME_PATTERN, line))
    return all_slam_times


def build_duration_series(log_filename: Path) -> list[float]:
    """Compute the per-pair timing durations from a log file."""
    all_slam_times = extract_slam_times(log_filename)
    result: list[float] = []
    for index in range(1, len(all_slam_times), 2):
        result.append(time_subtraction(all_slam_times[index - 1], all_slam_times[index]))
    return result


def render_report(durations: list[float], output_path: Path) -> pd.DataFrame:
    """Render the timing boxplot and table report to an HTML file."""
    if not PYECHARTS_AVAILABLE:
        raise ImportError(
            "pyecharts is required for render_report. Install with: pip install pyecharts"
        )
    chart = Boxplot()
    chart.add_xaxis(["时间(ms)"])
    chart.add_yaxis("swap合约对冲", chart.prepare_data([durations]))
    chart.set_global_opts(title_opts=opts.TitleOpts(title="箱体图"))

    table = Table()
    result_df = pd.DataFrame(durations, columns=["consume_time"])
    summary_df = result_df[["consume_time"]].describe()

    headers = list(summary_df.T.columns)
    rows = [list(summary_df.values)]
    table.add(headers, rows)
    table.set_global_opts(
        title_opts=ComponentTitleOpts(title="swap合约对冲时间统计(ms)", subtitle="")
    )

    page = Page(layout=Page.DraggablePageLayout).add(chart).add(table)
    page.render(str(output_path))
    return result_df


def main() -> None:
    data_root = _require_package_path("lv")
    num = 100000
    log_filename = data_root / f"tests/base_functions/datas/swap_hedge_{num}.log"
    output_path = data_root / f"configs/system_speed/swap合约对冲时间分析_{num}_num.html"
    durations = build_duration_series(log_filename)
    render_report(durations, output_path)


if __name__ == "__main__":
    main()
