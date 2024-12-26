from itertools import chain
from collections import ChainMap


def method_1():
    a = {"name": "cloud", "age": 34}
    b = {"score": 90}
    a.update(b)
    return a


def method_2():
    a = {"name": "cloud", "age": 34}
    b = {"score": 90}
    return a | b


def method_3():
    a = {"name": "cloud", "age": 34}
    b = {"score": 90}
    return dict(chain(a.items(), b.items()))


def method_4():
    a = {"name": "cloud", "age": 34}
    b = {"score": 90}
    return dict(ChainMap(a, b))


def method_5():
    a = {"name": "cloud", "age": 34}
    b = {"score": 90}
    return {**a, **b}


def method_6():
    a = {"name": "cloud", "age": 34}
    b = {"score": 90}
    return {k: v for d in [a, b] for k, v in d.items()}


def test_1(benchmark):
    c = {"name": "cloud", "age": 34, "score": 90}
    result_1 = benchmark(method_1)
    assert result_1 == c


def test_2(benchmark):
    c = {"name": "cloud", "age": 34, "score": 90}
    result_2 = benchmark(method_2)
    # print(f"result_2:{result_2}")
    # print(f"c:{c}")
    assert len(result_2) == len(c)


def test_3(benchmark):
    c = {"name": "cloud", "age": 34, "score": 90}
    result_3 = benchmark(method_3)
    # print(f"result_3:{result_3}")
    # print(f"c:{c}")
    assert len(result_3) == len(c)


def test_4(benchmark):
    c = {"name": "cloud", "age": 34, "score": 90}
    result_4 = benchmark(method_4)
    assert len(result_4) == len(c)


def test_5(benchmark):
    c = {"name": "cloud", "age": 34, "score": 90}
    result_5 = benchmark(method_5)
    assert result_5 == c


def test_6(benchmark):
    c = {"name": "cloud", "age": 34, "score": 90}
    result_6 = benchmark(method_6)
    assert result_6 == c
