# cython: language_level = 3
# cython: language = c++
# cython: boundscheck=False
# cython: wraparound=False
# cython: cdivision=True
# cython: nonecheck=False
# cython: initializedcheck=False
# @cython.boundscheck(False)
# @cython.wraparound(False)
# @cython.cdivision(True)
# @cython.nonecheck(False)
# @cython.initializedcheck(False)
import cython
from btpy.functions.calculate_numbers import round_number


cpdef double cal_sum_of_key_values(dict zheng_order_dict, str key):
    cdef double sum_value = 0.0
    cdef dict value
    cdef str trade_id
    for trade_id, value in zheng_order_dict.items():
        sum_value += value[key]
    return sum_value


cpdef normalise_hedge_data_to_base(depth_data, dict params):
    # new_percent = params['percent'] / 100 / params["base_spot_qty_unit"]
    cdef list spot_bid_price_list = depth_data.bid_price_list
    cdef list spot_bid_volume_list = depth_data.bid_volume_list
    cdef list spot_ask_price_list = depth_data.ask_price_list
    cdef list spot_ask_volume_list = depth_data.ask_volume_list
    # qty_unit_equal = params['base_spot_qty_unit'] == params['hedge_swap_qty_unit']
    cdef bint price_unit_equal = params['base_spot_price_unit'] == params['hedge_swap_price_unit']

    cdef list new_spot_bid_price_list = []
    cdef list new_spot_bid_volume_list = []
    cdef list new_spot_ask_price_list = []
    cdef list new_spot_ask_volume_list = []
    cdef int i
    # i==0的时候
    cdef double pre_bid_price
    cdef double pre_bid_volume
    cdef double pre_ask_price
    cdef double pre_ask_volume

    cdef double now_bid_price
    cdef double now_bid_volume
    cdef double now_ask_price
    cdef double now_ask_volume
    cdef double v
    cdef int data_len = len(spot_bid_price_list)
    cdef double percent = params['percent']
    cdef double base_spot_price_unit = params['base_spot_price_unit']
    cdef double base_spot_qty_unit = params['base_spot_qty_unit']
    # 如果内外盘价格精度是一样的，不进行标准化，如果不一致，需要进行标准化
    if price_unit_equal:
        for i in range(data_len):
            v = round_number(spot_bid_price_list[i],base_spot_price_unit,"down")
            new_spot_bid_price_list.append(v)
            v = int(spot_bid_volume_list[i] * percent / base_spot_qty_unit)
            new_spot_bid_volume_list.append(v)
            v = round_number(spot_ask_price_list[i], base_spot_price_unit, "up")
            new_spot_ask_price_list.append(v)
            v = int(spot_ask_volume_list[i] * percent / base_spot_qty_unit)
            new_spot_ask_volume_list.append(v)

        return new_spot_bid_price_list, new_spot_bid_volume_list, new_spot_ask_price_list, new_spot_ask_volume_list
    else:
        for i in range(data_len):
            new_spot_bid_price_list.append(round_number(spot_bid_price_list[i], base_spot_price_unit, 'down'))
            new_spot_ask_price_list.append(round_number(spot_ask_price_list[i], base_spot_price_unit, 'up'))
        spot_bid_price_list = new_spot_bid_price_list
        spot_ask_price_list = new_spot_ask_price_list
        new_spot_bid_price_list = []
        new_spot_ask_price_list = []
        # 循环判断价格是否相等并把价格相等的摆盘量
        # i==0的时候
        pre_bid_price = spot_bid_price_list[0]
        pre_bid_volume = spot_bid_volume_list[0]
        pre_ask_price = spot_ask_price_list[0]
        pre_ask_volume = spot_ask_volume_list[0]
        # i=1到i=data_len-2
        for i in range(1, data_len - 1):
            now_bid_price = spot_bid_price_list[i]
            now_bid_volume = spot_bid_volume_list[i]
            now_ask_price = spot_ask_price_list[i]
            now_ask_volume = spot_ask_volume_list[i]
            # 处理价格相等的情况
            if now_bid_price == pre_bid_price:
                pre_bid_volume += now_bid_volume
            if now_ask_price == pre_ask_price:
                pre_ask_volume += now_ask_volume
            # 处理价格不相等的情况
            if now_bid_price != pre_bid_price:
                # 把前一个数据添加到列表中
                new_spot_bid_price_list.append(pre_bid_price)
                new_spot_bid_volume_list.append(pre_bid_volume)
                # 更新上一个的价格和摆盘量
                pre_bid_price = now_bid_price
                pre_bid_volume = now_bid_volume
            if now_ask_price != pre_ask_price:
                # 把前一个数据添加到列表中
                new_spot_ask_price_list.append(pre_ask_price)
                new_spot_ask_volume_list.append(pre_ask_volume)
                # 更新上一个的价格和摆盘量
                pre_ask_price = now_ask_price
                pre_ask_volume = now_ask_volume
        # i = data_len-1 最后一行
        now_bid_price = spot_bid_price_list[data_len - 1]
        now_bid_volume = spot_bid_volume_list[data_len - 1]
        now_ask_price = spot_ask_price_list[data_len - 1]
        now_ask_volume = spot_ask_volume_list[data_len - 1]
        # 处理价格相等的情况
        if now_bid_price == pre_bid_price:
            pre_bid_volume += now_bid_volume
            # 把前一个数据添加到列表中
            new_spot_bid_price_list.append(pre_bid_price)
            new_spot_bid_volume_list.append(pre_bid_volume)
        if now_ask_price == pre_ask_price:
            pre_ask_volume += now_ask_volume
            # 把前一个数据添加到列表中
            new_spot_ask_price_list.append(pre_ask_price)
            new_spot_ask_volume_list.append(pre_ask_volume)
        # 处理价格不相等的情况
        if now_bid_price != pre_bid_price:
            # 把前一个数据添加到列表中
            new_spot_bid_price_list.append(pre_bid_price)
            new_spot_bid_volume_list.append(pre_bid_volume)
            new_spot_bid_price_list.append(now_bid_price)
            new_spot_bid_volume_list.append(now_bid_volume)
        if now_ask_price != pre_ask_price:
            # 把前一个数据添加到列表中
            new_spot_ask_price_list.append(pre_ask_price)
            new_spot_ask_volume_list.append(pre_ask_volume)
            new_spot_ask_price_list.append(now_ask_price)
            new_spot_ask_volume_list.append(now_ask_volume)

        spot_bid_volume = []
        for i in range(data_len):
            v = new_spot_bid_volume_list[i]
            v = int(v * percent / base_spot_qty_unit)
            spot_bid_volume.append(v)
        spot_ask_volume = []
        for i in range(data_len):
            v = new_spot_ask_volume_list[i]
            v = int(v * percent / base_spot_qty_unit)
            spot_ask_volume.append(v)
        return new_spot_bid_price_list, spot_bid_volume, new_spot_ask_price_list, spot_ask_volume