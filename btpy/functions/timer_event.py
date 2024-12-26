import sched
import time
import datetime


def run_on_timer(interval, func, *args, **kwargs):
    s = sched.scheduler(time.time, time.sleep)  # 生成调度器
    s.enter(interval, 1, func, ())
    s.run()


if __name__ == '__main__':
    def print_time():
        now = datetime.datetime.now().strftime('%Y-%m-%d %H:%M:%S')
        print(f'timer: {now}')
    run_on_timer(1, print_time)
    time.sleep(10)
