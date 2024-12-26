import pandas as pd
import numpy as np
from btpy.functions import get_package_path

root = get_package_path("lv")
bit_df = pd.read_csv(root + "/tests/base_functions/datas/bit_df.csv", index_col=0)
bit_df['qty'] = np.where(bit_df['side'] == "buy", bit_df['vol']+bit_df['fee']/bit_df['price'], -1 * bit_df['vol'])
okex_df = pd.read_csv(root + "/tests/base_functions/datas/okex_df.csv", index_col=0)
okex_df['qty'] = np.where(okex_df['side'] == "buy", 0.1 * okex_df['vol'], -0.1 * okex_df['vol'])


print("bit_df['qty'].sum()", bit_df['qty'].sum())
print("okex_df['qty'].sum()", okex_df['qty'].sum())

total_base_qty = 0.008
count = 0
hedge_result = []
self_result = []
for index, row in okex_df.iterrows():
    count += 1
    hedge_ts = row['ts']
    hedge_qty = row['qty']
    hedge_side = row['side']
    # 获取对冲订单之前的所有订单
    df = bit_df[bit_df['ts'] < hedge_ts]
    df.index = range(len(df))
    sell_df = df[df['qty'] < 0]
    buy_df = df[df['qty'] > 0]
    base_ts = list(df['ts'])[0]
    # 获取对冲订单之后的所有订单
    bit_df = bit_df[bit_df['ts'] >= hedge_ts]
    # 判断对冲之后，剩余多少
    qty_sum = df['qty'].sum()
    total_base_qty += 0.1 if qty_sum > 0 else -0.1
    print(f"第{count}次对冲订单，total_base_qty = {total_base_qty},hedge_ts = {hedge_ts}, qty={qty_sum}, hedge_qty = {hedge_qty}")
    print("buy_df\n", buy_df)
    print("sell_df\n", sell_df)
    print("df\n", df)
    # 如果当前订单是正数
    if qty_sum > 0:
        buy_amount = buy_df['amt'].sum()
        buy_qty = buy_df['qty'].sum()
        buy_fee = buy_df['fee'].sum()
        buy_avg_price = buy_amount / buy_qty
        buy_avg_fee = buy_fee / buy_qty
        # 抵消负数
        if len(sell_df) > 0:
            sell_amount = sell_df['amt'].sum()
            sell_qty = sell_df['qty'].sum()
            sell_fee = sell_df['fee'].sum()
            sell_avg_price = sell_amount / abs(sell_qty)
            sell_avg_fee = sell_fee / abs(sell_qty)
            # 对冲
            profit = (sell_avg_price - buy_avg_price) * abs(sell_qty)
            total_fee = (buy_avg_fee + sell_avg_fee) * abs(sell_qty)
            net_profit = profit + total_fee
            self_result.append([count, "buy", buy_avg_price, "sell", sell_avg_price, total_fee, profit, net_profit])
            # 剩余部分的量、价格、额和手续费
            new_buy_qty = buy_qty - abs(sell_qty)
            new_buy_avg_price = buy_avg_price
            new_buy_amount = buy_avg_price * new_buy_qty
            new_buy_fee = buy_avg_fee * new_buy_qty
        else:
            new_buy_qty = buy_qty
            new_buy_avg_price = buy_avg_price
            new_buy_amount = buy_amount
            new_buy_fee = buy_fee

        # 抵消对冲量
        assert abs(new_buy_qty) >= abs(hedge_qty), "可用对冲量不足，分析程序，检查获取交易数目"
        if new_buy_qty - abs(hedge_qty) > 0:
            total_fee = buy_avg_fee * abs(hedge_qty)
            total_amt = new_buy_amount * abs(hedge_qty) / new_buy_qty
            # 添加到第 count 笔对冲中
            hedge_result.append([base_ts, "buy", new_buy_avg_price, abs(hedge_qty), total_amt, total_fee, abs(hedge_qty)])
            print(f"当前对冲的笔数：hedge_result = {len(hedge_result)}")
            last_buy_qty = new_buy_qty - abs(hedge_qty)
            last_buy_avg_price = new_buy_avg_price
            last_buy_amount = buy_avg_price * last_buy_qty
            last_buy_fee = buy_avg_fee * last_buy_qty

            new_df = pd.DataFrame([{"ts": base_ts,
                                    "side": "buy",
                                    "price": last_buy_avg_price,
                                    "vol": abs(last_buy_qty),
                                    "amt": last_buy_amount,
                                    "fee": last_buy_fee,
                                    "qty": abs(last_buy_qty)}])
            print("new_df\n", new_df)
            bit_df = pd.concat([bit_df, new_df])
            bit_df = bit_df.sort_values("ts")
            bit_df.index = range(len(bit_df))

    if qty_sum <= 0:
        sell_amount = sell_df['amt'].sum()
        sell_qty = abs(sell_df['qty'].sum())
        sell_fee = sell_df['fee'].sum()
        sell_avg_price = sell_amount / abs(sell_qty)
        sell_avg_fee = sell_fee / abs(sell_qty)

        # 抵消正数
        if len(buy_df) > 0:
            buy_amount = buy_df['amt'].sum()
            buy_qty = buy_df['qty'].sum()
            buy_fee = buy_df['fee'].sum()
            buy_avg_price = buy_amount / buy_qty
            buy_avg_fee = buy_fee / buy_qty
            # 对冲
            profit = (sell_avg_price - buy_avg_price) * abs(buy_qty)
            total_fee = (buy_avg_fee + sell_avg_fee) * abs(buy_qty)
            net_profit = profit + total_fee
            self_result.append([count, "buy", buy_avg_price, "sell", sell_avg_price,  total_fee, profit, net_profit])
            # 剩余部分的量、价格、额和手续费
            new_sell_qty = sell_qty - abs(buy_qty)
            new_sell_avg_price = sell_avg_price
            new_sell_amount = sell_avg_price * new_sell_qty
            new_sell_fee = sell_avg_fee * new_sell_qty
        else:
            new_sell_qty = sell_qty
            new_sell_avg_price = sell_avg_price
            new_sell_amount = sell_amount
            new_sell_fee = sell_fee

            # 抵消对冲量
        assert abs(new_sell_qty) >= abs(hedge_qty), "可用对冲量不足，分析程序，检查获取交易数目"
        if abs(new_sell_qty) - abs(hedge_qty) > 0:
            total_fee = sell_avg_fee * abs(hedge_qty)
            total_amt = sell_avg_price * abs(hedge_qty)
            # 添加到第 count 笔对冲中
            hedge_result.append([base_ts, "sell", new_sell_avg_price, abs(hedge_qty), total_amt, total_fee, -1 * abs(hedge_qty)])
            print(f"当前对冲的笔数：hedge_result = {len(hedge_result)}")
            last_sell_qty = new_sell_qty - abs(hedge_qty)
            last_sell_avg_price = new_sell_avg_price
            last_sell_amount = sell_avg_price * last_sell_qty
            last_sell_fee = sell_avg_fee * last_sell_qty

            new_df = pd.DataFrame([{"ts": base_ts,
                                    "side": "sell",
                                    "price": last_sell_avg_price,
                                    "vol": abs(last_sell_qty),
                                    "amt": last_sell_amount,
                                    "fee": last_sell_fee,
                                    "qty": -1 * abs(last_sell_qty)}])
            print("new_df\n", new_df)
            bit_df = pd.concat([bit_df, new_df])
            # bit_df.append(new_df)
            bit_df = bit_df.sort_values("ts")
            bit_df.index = range(len(bit_df))
    print("hedge_result\n", hedge_result)
    print("self_result\n", self_result)
    print("bit_df\n", bit_df)

new_bit_df = pd.DataFrame(hedge_result, columns=['ts', 'side', 'price', 'vol', 'amt', 'fee', 'qty'])
print("new_bit_df", new_bit_df)
new_bit_df.columns = ["bit_" + str(i) for i in new_bit_df.columns]
print("new_bit_df", new_bit_df)
okex_df.columns = ["okex_" + str(i) for i in okex_df.columns]
print("okex_df", okex_df)
new_df = pd.concat([new_bit_df, okex_df], axis=1, join="outer")
new_df['profit'] = np.where(new_df['bit_side'] == "buy",
                            (new_df['okex_price'] - new_df['bit_price']) * new_df['bit_vol'] + new_df[
                                'bit_fee'] + new_df['okex_fee'],
                            (new_df['bit_price'] - new_df['okex_price']) * new_df['bit_vol'] + new_df[
                                'bit_fee'] + new_df['okex_fee']
                            )
print("new_df", new_df)
new_bit_df.to_csv("./new_bit_df.csv")
new_df.to_csv("./new_df.csv")
print("all_profit", round(new_df['profit'].sum(), 4))
print("profit_percent_from_2023-10-19", round(new_df['profit'].sum() / 2000, 4))
print("profit_percent_by_year", round(new_df['profit'].sum() * 365 / 2000, 4))
print("win percent", round(len(new_df[new_df['profit'] >= 0]) / len(new_df), 4))
