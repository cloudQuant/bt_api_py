"""bt_api_py 安装脚本（纯 Python 包，元数据在 pyproject.toml）。"""

from setuptools import find_packages, setup

setup(
    packages=find_packages(include=["bt_api_py", "bt_api_py.*"], exclude=["tests"]),
    package_data={
        "bt_api_py": [
            "configs/*",
        ],
    },
)
