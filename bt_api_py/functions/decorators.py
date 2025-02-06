import time
from functools import wraps
from typing import Callable, Any
import warnings


def deprecated(msg=None, stack_level=2):
    """
    Used to mark a function as deprecated.
    Parameters
    ----------
    msg : str
        The message to display in the deprecation warning.
    stack_level : int
        How far up the stack the warning needs to go, before
        showing the relevant calling lines.
    -----
    @deprecated(msg='function_a is deprecated! Use function_b instead.')
    def function_a(*args, **kwargs):
    """
    def deprecated_dec(fn):
        @wraps(fn)
        def wrapper(*args, **kwargs):
            warnings.warn(
                msg or "Function %s is deprecated." % fn.__name__,
                category=DeprecationWarning,
                stacklevel=stack_level
            )
            return fn(*args, **kwargs)
        return wrapper
    return deprecated_dec


def time_this_function(func):
    """
    作为装饰器使用，返回普通函数执行需要花费的时间
    :param func: functions
    :return: wrapper
    """
    @wraps(func)
    def wrapper(*args, **kwargs):
        start = time.perf_counter()
        result = func(*args, **kwargs)
        end = time.perf_counter()
        print(f"{func.__name__}, consume: {end - start}s")
        return result
    return wrapper


def time_async_function():
    """
    创建一个异步函数的装饰器，用于检测异步函数耗费时间
    :return: wrapper
    """
    def wrapper(func: Callable) -> Callable:
        @wraps(func)
        async def wrapped(*args, **kwargs) -> Any:
            print(f"start {func} with {args} {kwargs}")
            start = time.perf_counter()
            try:
                return await func(*args, **kwargs)
            finally:
                end = time.perf_counter()
                print(f"{func.__name__}, consume: {end - start}s")
        return wrapped
    return wrapper
