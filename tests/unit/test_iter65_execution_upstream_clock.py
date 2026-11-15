"""Bounded arithmetic and native-clock fault tests, entirely offline."""
import math
import os
import signal
import threading

import pytest
from bt_api_py import service_runtime as runtime


def test_ttl_conversion_never_rounds_up():
    for value in (1e-9, 0.1, 0.3, 1.0, 86400.125):
        numerator, denominator = value.as_integer_ratio()
        assert runtime._execution_ttl_ns(value) == numerator * 1_000_000_000 // denominator
    for value in (0, -1, 5e-10, float('inf'), float('nan'), True, 1e100):
        with pytest.raises(ValueError):
            runtime._execution_ttl_ns(value)


def test_native_sleep_clock_failure_and_regression_latch(monkeypatch):
    clock = runtime._ExecutionSuspendClock()
    current = clock.now()
    clock._last = current + 10_000_000_000
    with pytest.raises(ValueError, match='unavailable'):
        clock.now()
    clock._last = None
    with pytest.raises(ValueError, match='unavailable'):
        clock.now()
    other = runtime._ExecutionSuspendClock()
    with monkeypatch.context() as local:
        local.setattr(runtime, '_EXECUTION_SLEEP_CLOCK_ID', None)
        with pytest.raises(ValueError, match='unavailable'):
            other.now()
    with pytest.raises(ValueError, match='unavailable'):
        other.now()


@pytest.mark.skipif(not hasattr(os, 'fork'), reason='fork unavailable')
def test_clock_cannot_cross_process_without_touching_foreign_locks():
    clock = runtime._ExecutionSuspendClock()
    # A foreign thread remains active during fork; clock itself has no lock.
    entered, release = threading.Event(), threading.Event()
    def worker():
        entered.set(); release.wait(4)
    thread = threading.Thread(target=worker); thread.start()
    assert entered.wait(2)
    try:
        pid = os.fork()
        if pid == 0:
            signal.alarm(2)
            try:
                clock.now()
            except ValueError:
                os._exit(0)
            os._exit(1)
        _, status = os.waitpid(pid, 0)
        assert os.WIFEXITED(status) and os.WEXITSTATUS(status) == 0
    finally:
        release.set(); thread.join(3)
