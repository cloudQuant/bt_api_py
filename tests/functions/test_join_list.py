"""
测试合并list的方法和效率
"""

from itertools import chain


def method_1():
    a = [1, 2, 3, 4, 5, 6, 7, 8, 9, 10]
    b = [1, 2, 3, 4, 5, 6, 7, 8, 9, 10]
    return a + b


def method_2():
    a = [1, 2, 3, 4, 5, 6, 7, 8, 9, 10]
    b = [1, 2, 3, 4, 5, 6, 7, 8, 9, 10]
    a.extend(b)
    return a


def method_3():
    a = [1, 2, 3, 4, 5, 6, 7, 8, 9, 10]
    b = [1, 2, 3, 4, 5, 6, 7, 8, 9, 10]
    for i in b:
        a.append(i)
    return a


def method_4():
    a = [1, 2, 3, 4, 5, 6, 7, 8, 9, 10]
    b = [1, 2, 3, 4, 5, 6, 7, 8, 9, 10]
    return list(chain(a, b))


def method_5():
    a = [1, 2, 3, 4, 5, 6, 7, 8, 9, 10]
    b = [1, 2, 3, 4, 5, 6, 7, 8, 9, 10]
    return [*a, *b]


def get_a_b_c():
    a = [1, 2, 3, 4, 5, 6, 7, 8, 9, 10]
    b = [1, 2, 3, 4, 5, 6, 7, 8, 9, 10]
    c = a + b
    return a, b, c


def test_1(benchmark):
    a, b, c = get_a_b_c()
    result_1 = benchmark(method_1)
    assert result_1 == c


def test_2(benchmark):
    a, b, c = get_a_b_c()
    result_2 = benchmark(method_2)
    # print(f"result_2:{result_2}")
    # print(f"c:{c}")
    assert len(result_2) == len(c)


def test_3(benchmark):
    a, b, c = get_a_b_c()
    result_3 = benchmark(method_3)
    # print(f"result_3:{result_3}")
    # print(f"c:{c}")
    assert len(result_3) == len(c)


def test_4(benchmark):
    a, b, c = get_a_b_c()
    result_4 = benchmark(method_4)
    assert result_4 == c


def test_5(benchmark):
    a, b, c = get_a_b_c()
    result_5 = benchmark(method_5)
    assert result_5 == c


def test_list_comprehension():
    my_list = [i for i in range(0, 11) if i % 2 == 1]
    assert my_list == [1, 3, 5, 7, 9]


def test_list_comprehension_2():
    my_list = [i for i in range(0, 11) if i / 2 == 1]
    assert my_list == [2]
