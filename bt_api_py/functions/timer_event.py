import datetime
import sched
import time
from collections.abc import Callable
from typing import Any


def run_on_timer(interval: float, func: Callable[..., Any], *args: Any, **kwargs: Any) -> None:
    """Run ``func`` once after ``interval`` seconds."""
    s = sched.scheduler(time.time, time.sleep)
    s.enter(interval, 1, func, argument=args, kwargs=kwargs)
    s.run()


def _main() -> None:
    def print_time() -> None:
        now = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        print(f"timer: {now}")

    run_on_timer(1, print_time)
    time.sleep(10)


if __name__ == "__main__":
    _main()
