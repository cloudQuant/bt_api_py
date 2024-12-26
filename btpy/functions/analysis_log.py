"""用于分析日志中函数运行开始和结束之间的时间间隔，画出箱体图和统计指标"""
import os
import re
import numpy as np
import pandas as pd
from matplotlib import pyplot as plt
from btpy.functions import get_package_path
from datetime import datetime
from pyecharts import options as opts
from pyecharts.charts import Boxplot, Page
from pyecharts.components import Table
from pyecharts.options import ComponentTitleOpts

# log_filename = './log_print.log'
data_root = get_package_path("lv")
num = 100000
log_filename = data_root+f'/tests/base_functions/datas/swap_hedge_{num}.log'


def time_subtraction(start_time_str, end_time_str):
    start_time = datetime.strptime(start_time_str, "%Y-%m-%d %H:%M:%S.%f")
    end_time = datetime.strptime(end_time_str, "%Y-%m-%d %H:%M:%S.%f")
    sub_time = end_time - start_time
    return sub_time.total_seconds() * 1000


with open(log_filename) as fp:
    all_slam_times = []
    for line in fp.readlines():
        a = "enter deal trade_data"
        b = "exit deal trade_data"
        # pattern = re.compile(r"""(\d{4}-\d{1,2}-\d{1,2}\s\d{1,2}:\d{1,2}:\d{1,2},\d{1,3} --- .*?_begin|\d{4}-\d{1,2}-\d{1,2}\s\d{1,2}:\d{1,2}:\d{1,2},\d{1,3} --- .*?_end])""")
        pattern = re.compile(r"""deal trade_data, time = (\d{4}-\d{1,2}-\d{1,2}\s\d{1,2}:\d{1,2}:\d{1,2}.\d{1,6})""")
        slam_time_list = re.findall(pattern, line)
        if len(slam_time_list) > 0:
            # print(slam_time_list)
            all_slam_times.append(slam_time_list[0])
    # print(all_slam_times)

result = []
for i in range(len(all_slam_times)):
    if i % 2 == 1:
        value = time_subtraction(all_slam_times[i - 1], all_slam_times[i])
        result.append(value)

c = Boxplot()
c.add_xaxis(["时间(ms)"])
c.add_yaxis("swap合约对冲", c.prepare_data([result]))
c.set_global_opts(title_opts=opts.TitleOpts(title="箱体图"))

table = Table()
result_df = pd.DataFrame(result, columns=['consume_time'])
df = result_df[['consume_time']].describe()

headers = list(df.T.columns)
rows = [
    list(df.values)
]
table.add(headers, rows)
table.set_global_opts(
    title_opts=ComponentTitleOpts(title="swap合约对冲时间统计(ms)", subtitle="")
)

page = (
    Page(
        layout=Page.DraggablePageLayout
    )
    .add(c)
    .add(table)
)
page.render(data_root+f'/configs/system_speed/swap合约对冲时间分析_{num}_num.html')
# c.render("./bit新框架对冲时间分析.html")
# page.save_resize_html(
#     # Page 第一次渲染后的 html 文件
#     source=data_root+f'/configs/system_speed/swap合约摆盘时间分析_{num}_num.html',
#     # 布局配置文件
#     cfg_file="./chart_config.json",
#     # 重新生成的 .html 存放路径
#     dest=data_root+f'/configs/system_speed/swap合约摆盘时间分析_{num}_num.html'
# )
