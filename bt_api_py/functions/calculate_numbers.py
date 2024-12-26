import random


def round_number(number, min_unit, direction):
    """ get number by min_unit
    param: number: float
    param: direction: 'up' or 'down' or 'random' 向上取整或向下取整或随机
    param: min_unit (TYPE): Description
    return: number: float end with 0.5 or 0.
    """
    str_num = str(min_unit)
    length = len(str_num.split('.')[1]) if '.' in str_num else int(str_num.split('e-')[-1])
    int_part = number // min_unit
    number = int_part * min_unit
    if direction == 'down':
        pass
    elif direction == 'up':
        number += min_unit
    elif direction == 'random':
        i = random.choice([0, 1])
        number = number + i * min_unit
    return round(number, length)


def allocate_value_to_arr(arr, target_value):
    """
    分配仓位，如果当前各档可承受的仓位和小于总体最大分配的仓位，保持不变，如果超过了总体可分配的仓位，那么把总体仓位等比例分配到各档
    :param arr: 每个档位最大分配的仓位
    :param target_value: 总体最大分配的仓位
    :return: 目标分配的仓位
    """
    arr_sum_value = sum(arr)

    if arr_sum_value > 0:
        percent = target_value / sum(arr)
    else:
        percent = 0

    if percent < 1:
        target_arr = [i * percent for i in arr]
    else:
        target_arr = arr
    return target_arr


def normalise_hedge_data_to_base(depth_data, params):
    """
    :param depth_data: 深度行情数据，order_book_data
    :param params: 参数字典
    :return: 标准化之后的数据
    """
    # new_percent = params['percent'] / 100 / params["base_spot_qty_unit"]
    spot_bid_price = depth_data.bid_price_list
    spot_bid_vol = depth_data.bid_volume_list
    spot_ask_price = depth_data.ask_price_list
    spot_ask_vol = depth_data.ask_volume_list
    # qty_unit_equal = params['base_spot_qty_unit'] == params['hedge_swap_qty_unit']
    price_unit_equal = params['base_spot_price_unit'] == params['hedge_swap_price_unit']
    # 如果内外盘价格精度是一样的，不进行标准化，如果不一致，需要进行标准化
    if price_unit_equal:
        base_spot_price_unit = params['base_spot_price_unit']
        spot_bid_price = [round_number(i, base_spot_price_unit, 'down') for i in spot_bid_price]
        spot_ask_price = [round_number(i, base_spot_price_unit, 'up') for i in spot_ask_price]
        spot_bid_vol = [int(i * params['qty_percent'] / 100 / params["base_spot_qty_unit"]) for i in spot_bid_vol]
        spot_ask_vol = [int(i * params['qty_percent'] / 100 / params["base_spot_qty_unit"]) for i in spot_ask_vol]
        return spot_bid_price, spot_bid_vol, spot_ask_price, spot_ask_vol
    else:
        # base_spot_qty_unit = params['base_spot_qty_unit']
        base_spot_price_unit = params['base_spot_price_unit']
        spot_bid_price = [round_number(i, base_spot_price_unit, 'down') for i in spot_bid_price]
        spot_ask_price = [round_number(i, base_spot_price_unit, 'up') for i in spot_ask_price]

        # 循环判断价格是否相等并把价格相等的摆盘量
        new_spot_bid_price = []
        new_spot_bid_volume = []
        new_spot_ask_price = []
        new_spot_ask_volume = []
        data_len = len(spot_bid_price)
        # i==0的时候
        pre_bid_price = spot_bid_price[0]
        pre_bid_volume = spot_bid_vol[0]
        pre_ask_price = spot_ask_price[0]
        pre_ask_volume = spot_ask_vol[0]
        # i=1到i=data_len-2
        for i in range(1, data_len - 1):
            now_bid_price = spot_bid_price[i]
            now_bid_volume = spot_bid_vol[i]
            now_ask_price = spot_ask_price[i]
            now_ask_volume = spot_ask_vol[i]
            # 处理价格相等的情况
            if now_bid_price == pre_bid_price:
                pre_bid_volume += now_bid_volume
            if now_ask_price == pre_ask_price:
                pre_ask_volume += now_ask_volume
            # 处理价格不相等的情况
            if now_bid_price != pre_bid_price:
                # 把前一个数据添加到列表中
                new_spot_bid_price.append(pre_bid_price)
                new_spot_bid_volume.append(pre_bid_volume)
                # 更新上一个的价格和摆盘量
                pre_bid_price = now_bid_price
                pre_bid_volume = now_bid_volume
            if now_ask_price != pre_ask_price:
                # 把前一个数据添加到列表中
                new_spot_ask_price.append(pre_ask_price)
                new_spot_ask_volume.append(pre_ask_volume)
                # 更新上一个的价格和摆盘量
                pre_ask_price = now_ask_price
                pre_ask_volume = now_ask_volume
        # i = data_len-1 最后一行
        now_bid_price = spot_bid_price[data_len - 1]
        now_bid_volume = spot_bid_vol[data_len - 1]
        now_ask_price = spot_ask_price[data_len - 1]
        now_ask_volume = spot_ask_vol[data_len - 1]
        # 处理价格相等的情况
        if now_bid_price == pre_bid_price:
            pre_bid_volume += now_bid_volume
            # 把前一个数据添加到列表中
            new_spot_bid_price.append(pre_bid_price)
            new_spot_bid_volume.append(pre_bid_volume)
        if now_ask_price == pre_ask_price:
            pre_ask_volume += now_ask_volume
            # 把前一个数据添加到列表中
            new_spot_ask_price.append(pre_ask_price)
            new_spot_ask_volume.append(pre_ask_volume)
        # 处理价格不相等的情况
        if now_bid_price != pre_bid_price:
            # 把前一个数据添加到列表中
            new_spot_bid_price.append(pre_bid_price)
            new_spot_bid_volume.append(pre_bid_volume)
            new_spot_bid_price.append(now_bid_price)
            new_spot_bid_volume.append(now_bid_volume)
        if now_ask_price != pre_ask_price:
            # 把前一个数据添加到列表中
            new_spot_ask_price.append(pre_ask_price)
            new_spot_ask_volume.append(pre_ask_volume)
            new_spot_ask_price.append(now_ask_price)
            new_spot_ask_volume.append(now_ask_volume)
        new_spot_bid_volume = [int(i * params['qty_percent'] / 100 / params["base_spot_qty_unit"]) for i in
                               new_spot_bid_volume]
        # todo 这个地方为啥存在一个0.01？
        # new_spot_ask_volume = [int(0.01 * i * params['qty_percent'] / 100 / params["base_spot_qty_unit"]) for i in
        #                        new_spot_ask_volume]
        new_spot_ask_volume = [int(i * params['qty_percent'] / 100 / params["base_spot_qty_unit"]) for i in
                               new_spot_ask_volume]
        return new_spot_bid_price, new_spot_bid_volume, new_spot_ask_price, new_spot_ask_volume


def merge_zheng_order(zheng_order_dict):
    """
    原来bit策略中的 merge_zhengOrderList
    ：params: zheng_order_dict
    :return:dict,修改后的zheng_order_dict
    """
    order_rows = len(zheng_order_dict)
    # print(f"当前订单数量为:{order_rows}")
    if order_rows == 0:
        return {}
    order_qty_list = [value['qty_wbf'] for value in zheng_order_dict.values()]
    short_qty_list = [i for i in order_qty_list if i < 0]
    long_qty_list = [i for i in order_qty_list if i > 0]

    if len(short_qty_list) == 0 or len(long_qty_list) == 0:
        return zheng_order_dict
    # b = len(set(order_list_new))
    # if b == 1 or b == 0:
    #     return
    # 计算成交量的和
    qty_wbf_sum = sum(order_qty_list)
    # 如果净持仓大于0，那么就把空头寸给删除，计算空的头寸大小
    if qty_wbf_sum > 0:
        short_qty_sum = sum(short_qty_list)
        # 只需要保存多头数据
        long_zheng_order_dict = {key: value for key, value in zheng_order_dict.items() if value['qty_wbf'] > 0}
        # 对zheng_order_dict的多头盈利进行排序，按照从大到小的顺序进行排序
        sorted_long_order = {k: v for k, v in sorted(long_zheng_order_dict.items(),
                                                     key=lambda x: (x[1]["price_binance"] - x[1]["price_wbf"]) / x[1][
                                                         "price_wbf"], reverse=True)}
        key_list = list(sorted_long_order.keys())
        for key in key_list:
            value = sorted_long_order[key]
            short_qty_sum += value['qty_wbf']
            if short_qty_sum <= 0:
                sorted_long_order.pop(key)
            else:
                value['qty_wbf'] = short_qty_sum
                sorted_long_order[key] = value
                # short_qty_sum = 0
                break
        zheng_order_dict = sorted_long_order

    if qty_wbf_sum < 0:
        long_qty_sum = sum(long_qty_list)
        # 只需要保存空头数据，并按照盈利从大到小进行排序
        short_zheng_order_dict = {key: value for key, value in zheng_order_dict.items() if value['qty_wbf'] < 0}
        sorted_short_order = {k: v for k, v in sorted(short_zheng_order_dict.items(),
                                                      key=lambda x: (x[1]["price_wbf"] - x[1]["price_binance"]) / x[1][
                                                          "price_wbf"], reverse=True)}

        key_list = list(sorted_short_order.keys())
        for key in key_list:
            value = sorted_short_order[key]
            long_qty_sum += value['qty_wbf']
            if long_qty_sum >= 0:
                sorted_short_order.pop(key)
            else:
                value['qty_wbf'] = long_qty_sum
                sorted_short_order[key] = value
                # long_qty_sum = 0
                break
        zheng_order_dict = sorted_short_order

    return zheng_order_dict


def cal_sum_of_key_values(zheng_order_dict, key):
    """
    计算字典中很多字典value中key的值
    :param zheng_order_dict: dict
    :param key: str
    :return: float sum_value
    """
    sum_value = 0
    for trade_id, value in zheng_order_dict.items():
        sum_value += value[key]
    return sum_value
