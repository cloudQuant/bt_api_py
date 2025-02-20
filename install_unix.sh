#!/bin/bash

# 安装 requirements.txt 中的依赖
pip install -U -r requirements.txt

# 切换到上一级目录
cd ..

# 安装 bt_api_py 包
pip install -U --no-build-isolation ./bt_api_py

# 切换到 bt_api_py 目录
cd ./bt_api_py

# Delete all .log files
# 删除所有 .log 文件
echo "Deleting all .log files..."
find . -type f -name "*.log" -exec rm -f {} \;
echo "All .log files deleted."

# 删除名为 logs 的文件夹及其内容
echo "Deleting logs folder if it exists..."
rm -rf logs
echo "Logs folder deleted if it existed."

echo "Operation completed."

# 运行 backtrader 的测试用例，使用 4 个进程并行测试
pytest tests -n 4
