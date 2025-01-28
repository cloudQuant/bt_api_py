#!/bin/bash

# 安装 requirements.txt 中的依赖
# pip install -U -r requirements.txt

# 切换到上一级目录
cd ..

# 安装 bt_api_py 包
pip install -U --no-build-isolation ./bt_api_py

# 切换到 bt_api_py 目录
cd ./bt_api_py

# 运行 backtrader 的测试用例，使用 4 个进程并行测试
pytest tests -n 4
