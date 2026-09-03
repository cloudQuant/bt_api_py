"""bt_api_py 安装脚本（纯 Python 包，元数据在 pyproject.toml）。"""

import shutil
from pathlib import Path

from setuptools import find_packages, setup
from setuptools.command.build_py import build_py


class BuildPyWithoutBytecode(build_py):
    """Remove stale bytecode from the staging directory before wheel assembly."""

    def run(self) -> None:
        super().run()
        build_root = Path(self.build_lib)
        for cache_dir in build_root.rglob("__pycache__"):
            shutil.rmtree(cache_dir)
        for bytecode in build_root.rglob("*.py[co]"):
            bytecode.unlink()


setup(
    packages=find_packages(include=["bt_api_py", "bt_api_py.*"], exclude=["tests"]),
    cmdclass={"build_py": BuildPyWithoutBytecode},
    package_data={
        "bt_api_py": [
            "configs/*.pkl",
            "configs/*.toml",
            "configs/*.yaml",
            "py.typed",
        ],
    },
)
