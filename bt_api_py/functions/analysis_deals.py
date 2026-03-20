"""Analyze hedge deal CSVs without executing work at import time."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd

from bt_api_py.functions import get_package_path


def _require_package_path(package_name: str) -> Path:
    package_path = get_package_path(package_name)
    if package_path is None:
        raise RuntimeError(f"Package path for '{package_name}' is not available")
    return Path(package_path)


def load_trade_frames(data_root: Path) -> tuple[pd.DataFrame, pd.DataFrame]:
    """Load and normalize the Bit/OKEx trade data frames used by the analysis script."""
    bit_df = pd.read_csv(data_root / "tests/base_functions/datas/bit_df.csv", index_col=0)
    bit_df["qty"] = np.where(
        bit_df["side"] == "buy",
        bit_df["vol"] + bit_df["fee"] / bit_df["price"],
        -1 * bit_df["vol"],
    )

    okex_df = pd.read_csv(data_root / "tests/base_functions/datas/okex_df.csv", index_col=0)
    okex_df["qty"] = np.where(okex_df["side"] == "buy", 0.1 * okex_df["vol"], -0.1 * okex_df["vol"])
    return bit_df, okex_df


def analyze_deals(
    bit_df: pd.DataFrame,
    okex_df: pd.DataFrame,
    *,
    debug: bool = False,
) -> tuple[pd.DataFrame, pd.DataFrame, list[list[Any]]]:
    """Analyze hedge results and return normalized Bit data, merged report, and self trades."""
    bit_work_df = bit_df.copy()
    okex_work_df = okex_df.copy()

    if debug:
        print("bit_df['qty'].sum()", bit_work_df["qty"].sum())
        print("okex_df['qty'].sum()", okex_work_df["qty"].sum())

    total_base_qty = 0.008
    hedge_result: list[list[Any]] = []
    self_result: list[list[Any]] = []

    for count, (_index, row) in enumerate(okex_work_df.iterrows(), start=1):
        hedge_ts = row["ts"]
        hedge_qty = row["qty"]
        base_ts: Any = hedge_ts

        df = bit_work_df[bit_work_df["ts"] < hedge_ts].copy()
        df.index = range(len(df))
        if not df.empty:
            base_ts = list(df["ts"])[0]

        sell_df = df[df["qty"] < 0]
        buy_df = df[df["qty"] > 0]
        bit_work_df = bit_work_df[bit_work_df["ts"] >= hedge_ts]

        qty_sum = df["qty"].sum()
        total_base_qty += 0.1 if qty_sum > 0 else -0.1

        if debug:
            print(
                f"第{count}次对冲订单，total_base_qty = {total_base_qty},"
                f"hedge_ts = {hedge_ts}, qty={qty_sum}, hedge_qty = {hedge_qty}"
            )
            print("buy_df\n", buy_df)
            print("sell_df\n", sell_df)
            print("df\n", df)

        if qty_sum > 0:
            buy_amount = buy_df["amt"].sum()
            buy_qty = buy_df["qty"].sum()
            buy_fee = buy_df["fee"].sum()
            buy_avg_price = buy_amount / buy_qty
            buy_avg_fee = buy_fee / buy_qty

            if len(sell_df) > 0:
                sell_amount = sell_df["amt"].sum()
                sell_qty = sell_df["qty"].sum()
                sell_fee = sell_df["fee"].sum()
                sell_avg_price = sell_amount / abs(sell_qty)
                sell_avg_fee = sell_fee / abs(sell_qty)
                profit = (sell_avg_price - buy_avg_price) * abs(sell_qty)
                total_fee = (buy_avg_fee + sell_avg_fee) * abs(sell_qty)
                net_profit = profit + total_fee
                self_result.append(
                    [
                        count,
                        "buy",
                        buy_avg_price,
                        "sell",
                        sell_avg_price,
                        total_fee,
                        profit,
                        net_profit,
                    ]
                )
                new_buy_qty = buy_qty - abs(sell_qty)
                new_buy_avg_price = buy_avg_price
                new_buy_amount = buy_avg_price * new_buy_qty
            else:
                new_buy_qty = buy_qty
                new_buy_avg_price = buy_avg_price
                new_buy_amount = buy_amount

            assert abs(new_buy_qty) >= abs(hedge_qty), "可用对冲量不足，分析程序，检查获取交易数目"
            if new_buy_qty - abs(hedge_qty) > 0:
                total_fee = buy_avg_fee * abs(hedge_qty)
                total_amt = new_buy_amount * abs(hedge_qty) / new_buy_qty
                hedge_result.append(
                    [
                        base_ts,
                        "buy",
                        new_buy_avg_price,
                        abs(hedge_qty),
                        total_amt,
                        total_fee,
                        abs(hedge_qty),
                    ]
                )
                if debug:
                    print(f"当前对冲的笔数：hedge_result = {len(hedge_result)}")

                last_buy_qty = new_buy_qty - abs(hedge_qty)
                last_buy_avg_price = new_buy_avg_price
                last_buy_amount = buy_avg_price * last_buy_qty
                last_buy_fee = buy_avg_fee * last_buy_qty

                new_df = pd.DataFrame(
                    [
                        {
                            "ts": base_ts,
                            "side": "buy",
                            "price": last_buy_avg_price,
                            "vol": abs(last_buy_qty),
                            "amt": last_buy_amount,
                            "fee": last_buy_fee,
                            "qty": abs(last_buy_qty),
                        }
                    ]
                )
                if debug:
                    print("new_df\n", new_df)
                bit_work_df = pd.concat([bit_work_df, new_df]).sort_values("ts")
                bit_work_df.index = range(len(bit_work_df))

        if qty_sum <= 0:
            sell_amount = sell_df["amt"].sum()
            sell_qty = abs(sell_df["qty"].sum())
            sell_fee = sell_df["fee"].sum()
            sell_avg_price = sell_amount / abs(sell_qty)
            sell_avg_fee = sell_fee / abs(sell_qty)

            if len(buy_df) > 0:
                buy_amount = buy_df["amt"].sum()
                buy_qty = buy_df["qty"].sum()
                buy_fee = buy_df["fee"].sum()
                buy_avg_price = buy_amount / buy_qty
                buy_avg_fee = buy_fee / buy_qty
                profit = (sell_avg_price - buy_avg_price) * abs(buy_qty)
                total_fee = (buy_avg_fee + sell_avg_fee) * abs(buy_qty)
                net_profit = profit + total_fee
                self_result.append(
                    [
                        count,
                        "buy",
                        buy_avg_price,
                        "sell",
                        sell_avg_price,
                        total_fee,
                        profit,
                        net_profit,
                    ]
                )
                new_sell_qty = sell_qty - abs(buy_qty)
                new_sell_avg_price = sell_avg_price
            else:
                new_sell_qty = sell_qty
                new_sell_avg_price = sell_avg_price

            assert abs(new_sell_qty) >= abs(hedge_qty), "可用对冲量不足，分析程序，检查获取交易数目"
            if abs(new_sell_qty) - abs(hedge_qty) > 0:
                total_fee = sell_avg_fee * abs(hedge_qty)
                total_amt = sell_avg_price * abs(hedge_qty)
                hedge_result.append(
                    [
                        base_ts,
                        "sell",
                        new_sell_avg_price,
                        abs(hedge_qty),
                        total_amt,
                        total_fee,
                        -1 * abs(hedge_qty),
                    ]
                )
                if debug:
                    print(f"当前对冲的笔数：hedge_result = {len(hedge_result)}")

                last_sell_qty = new_sell_qty - abs(hedge_qty)
                last_sell_avg_price = new_sell_avg_price
                last_sell_amount = sell_avg_price * last_sell_qty
                last_sell_fee = sell_avg_fee * last_sell_qty

                new_df = pd.DataFrame(
                    [
                        {
                            "ts": base_ts,
                            "side": "sell",
                            "price": last_sell_avg_price,
                            "vol": abs(last_sell_qty),
                            "amt": last_sell_amount,
                            "fee": last_sell_fee,
                            "qty": -1 * abs(last_sell_qty),
                        }
                    ]
                )
                if debug:
                    print("new_df\n", new_df)
                bit_work_df = pd.concat([bit_work_df, new_df]).sort_values("ts")
                bit_work_df.index = range(len(bit_work_df))

        if debug:
            print("hedge_result\n", hedge_result)
            print("self_result\n", self_result)
            print("bit_df\n", bit_work_df)

    new_bit_df = pd.DataFrame(
        hedge_result, columns=["ts", "side", "price", "vol", "amt", "fee", "qty"]
    )
    new_bit_df.columns = ["bit_" + str(column) for column in new_bit_df.columns]

    renamed_okex_df = okex_work_df.rename(columns=lambda column: "okex_" + str(column))
    merged_df = pd.concat([new_bit_df, renamed_okex_df], axis=1, join="outer")
    merged_df["profit"] = np.where(
        merged_df["bit_side"] == "buy",
        (merged_df["okex_price"] - merged_df["bit_price"]) * merged_df["bit_vol"]
        + merged_df["bit_fee"]
        + merged_df["okex_fee"],
        (merged_df["bit_price"] - merged_df["okex_price"]) * merged_df["bit_vol"]
        + merged_df["bit_fee"]
        + merged_df["okex_fee"],
    )
    return new_bit_df, merged_df, self_result


def main() -> None:
    data_root = _require_package_path("lv")
    bit_df, okex_df = load_trade_frames(data_root)
    new_bit_df, merged_df, _self_result = analyze_deals(bit_df, okex_df, debug=True)

    print("new_bit_df", new_bit_df)
    print("okex_df", okex_df.rename(columns=lambda column: "okex_" + str(column)))
    print("new_df", merged_df)

    new_bit_df.to_csv("./new_bit_df.csv")
    merged_df.to_csv("./new_df.csv")

    print("all_profit", round(merged_df["profit"].sum(), 4))
    print("profit_percent_from_2023-10-19", round(merged_df["profit"].sum() / 2000, 4))
    print("profit_percent_by_year", round(merged_df["profit"].sum() * 365 / 2000, 4))
    print("win percent", round(len(merged_df[merged_df["profit"] >= 0]) / len(merged_df), 4))


if __name__ == "__main__":
    main()
