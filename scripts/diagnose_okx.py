# -*- coding: utf-8 -*-
"""Diagnostic script: check OKX spot open orders, positions, and account balance."""
import queue
import sys
sys.path.insert(0, '.')

from bt_api_py.functions.utils import read_account_config
from bt_api_py.feeds.live_okx_feed import OkxRequestDataSpot


def init_feed():
    data = read_account_config()
    kwargs = {
        "public_key": data['okx']['public_key'],
        "private_key": data['okx']['private_key'],
        "passphrase": data['okx']["passphrase"],
        "topics": {"tick": {"symbol": "BTC-USDT"}},
        "proxies": data.get('proxies'),
        "async_proxy": data.get('async_proxy'),
    }
    data_queue = queue.Queue()
    return OkxRequestDataSpot(data_queue, **kwargs)


def main():
    feed = init_feed()

    # 0. Check OP-USDT price and simulate order size
    tick = feed.get_tick("OP-USDT")
    tick_data = tick.get_data()[0].init_data()
    bid_price = tick_data.get_bid_price()
    ask_price = tick_data.get_ask_price()
    bid_price_order = round(bid_price * 0.9, 2)
    ask_price_order = round(ask_price * 1.1, 2)
    lots = 0
    while lots * ask_price_order < 10:
        lots += 1
    print("=" * 60)
    print("0. OP-USDT PRICE & ORDER SIMULATION")
    print("=" * 60)
    print(f"   bid={bid_price}, ask={ask_price}")
    print(f"   buy order: {lots} lots @ {bid_price_order} = {lots * bid_price_order:.2f} USDT")
    print(f"   sell order: {lots} lots @ {ask_price_order} = {lots * ask_price_order:.2f} USDT")

    # Try a test make_order and print raw response
    print("\n   Testing make_order (buy) raw response:")
    import random
    cid = str(random.randint(10**17, 10**18-1))
    buy_result = feed.make_order("OP-USDT", lots, bid_price_order, "buy-limit", client_order_id=cid)
    print(f"   status={buy_result.get_status()}")
    print(f"   raw input_data={buy_result.get_input_data()}")
    print(f"   data={buy_result.get_data()}")
    # Cancel if order was placed
    if buy_result.get_status():
        order_id = buy_result.get_data()[0].get("order_id")
        if order_id:
            feed.cancel_order("OP-USDT", order_id=order_id)
            print(f"   Cancelled test order {order_id}")

    # 1. Check open orders (all symbols)
    print("=" * 60)
    print("1. OPEN ORDERS (all symbols)")
    print("=" * 60)
    open_orders = feed.get_open_orders()
    order_list = open_orders.get_data()
    print(f"   Total open orders: {len(order_list)}")
    for i, order in enumerate(order_list):
        od = order.init_data()
        print(f"   [{i}] symbol={od.get_symbol_name()}, order_id={od.get_order_id()}, "
              f"side={od.get_order_side()}, price={od.get_order_price()}, "
              f"volume={od.get_order_volume()}, status={od.get_order_status()}")

    # 2. Check positions
    print("\n" + "=" * 60)
    print("2. POSITIONS")
    print("=" * 60)
    for sym in ["OP-USDT", "BTC-USDT"]:
        positions = feed.get_position(symbol=sym)
        pos_list = positions.get_data()
        print(f"   {sym}: {len(pos_list)} positions")
        for i, pos in enumerate(pos_list):
            try:
                pd = pos.init_data()
                print(f"     [{i}] {pd}")
            except Exception as e:
                print(f"     [{i}] raw: {pos}, error: {e}")

    # 3. Check account balance
    print("\n" + "=" * 60)
    print("3. ACCOUNT BALANCE")
    print("=" * 60)
    account = feed.get_account()
    acc_list = account.get_data()
    for i, acc in enumerate(acc_list):
        ad = acc.init_data()
        print(f"   [{i}] total_margin={ad.get_total_margin()}")

    # 4. Cancel all open orders
    if len(order_list) > 0:
        print("\n" + "=" * 60)
        print("4. CANCELLING ALL OPEN ORDERS")
        print("=" * 60)
        for i, order in enumerate(order_list):
            od = order.init_data()
            order_id = od.get_order_id()
            symbol = od.get_symbol_name()
            if order_id:
                result = feed.cancel_order(symbol, order_id=order_id)
                print(f"   Cancel order {order_id} on {symbol}: status={result.get_status()}, data={result.get_data()}")
        # Re-check open orders after cancellation
        print("\n   Re-checking open orders after cancellation...")
        open_orders2 = feed.get_open_orders()
        order_list2 = open_orders2.get_data()
        print(f"   Remaining open orders: {len(order_list2)}")
    else:
        print("\n   No open orders to cancel.")


if __name__ == "__main__":
    main()
