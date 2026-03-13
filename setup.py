"""
bt_api_py 安装脚本

支持平台: Linux (x86_64), Windows (x64), macOS (arm64/x86_64)
支持 Python: 3.11, 3.12, 3.13

包含两个 C/C++ 扩展:
  1. bt_api_py.functions.calculate_number.calculate_numbers_by_cython (Cython)
  2. bt_api_py.ctp._ctp (CTP SWIG C++ 绑定)
"""

import glob
import os
import pathlib
import shutil
import sys
import sysconfig

import numpy as np
from setuptools import Extension, find_packages, setup
from setuptools.command.build_ext import build_ext

# ---------------------------------------------------------------------------
#  CTP wrapper 拆分: 将 SWIG 生成的 ctp.py 拆分为多个分类子模块
# ---------------------------------------------------------------------------
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "scripts"))
try:
    from split_ctp_wrapper import split_ctp_wrapper

    split_ctp_wrapper()
except Exception as e:
    print(f"WARNING: CTP wrapper split skipped: {e}")
finally:
    sys.path.pop(0)

# ---------------------------------------------------------------------------
#  版本
# ---------------------------------------------------------------------------
VERSION = "0.15"
CTP_API_VER = "6.7.7"

# ---------------------------------------------------------------------------
#  CTP API — 平台相关配置
# ---------------------------------------------------------------------------
CTP_PKG_DIR = os.path.join("bt_api_py", "ctp")
CTP_API_DIR = os.path.join(CTP_PKG_DIR, "api", CTP_API_VER)

ctp_package_data = []  # runtime 库文件列表 (相对于 bt_api_py/ctp/)
ctp_inc_dirs = []
ctp_lib_dirs = []
ctp_lib_names = []
ctp_link_args = []
ctp_compile_args = []


def _existing_paths(paths: list[str]) -> list[str]:
    result = []
    seen = set()
    for path in paths:
        if not path or path in seen or not os.path.exists(path):
            continue
        seen.add(path)
        result.append(path)
    return result


def _windows_iconv_prefixes() -> list[str]:
    user_home = os.path.expanduser("~")
    prefixes = _existing_paths(
        [
            os.environ.get("CONDA_PREFIX"),
            os.environ.get("MAMBA_ROOT_PREFIX"),
            os.path.join(user_home, "miniconda3"),
            os.path.join(user_home, "anaconda3"),
            r"C:\miniconda3",
            r"C:\Miniconda3",
            r"C:\anaconda3",
            r"C:\Anaconda3",
        ]
    )
    result = []
    for prefix in prefixes:
        include_file = os.path.join(prefix, "Library", "include", "iconv.h")
        library_file = os.path.join(prefix, "Library", "lib", "iconv.lib")
        if os.path.exists(include_file) and os.path.exists(library_file):
            result.append(prefix)
    return result

if sys.platform.startswith("darwin"):
    # ---- macOS: 使用 .framework ----
    API_PLAT_DIR = os.path.join(CTP_API_DIR, "darwin")
    ctp_inc_dirs = [
        os.path.join(API_PLAT_DIR, "thostmduserapi_se.framework/Versions/A/Headers"),
        os.path.join(API_PLAT_DIR, "thosttraderapi_se.framework/Versions/A/Headers"),
    ]
    ctp_lib_dirs = [API_PLAT_DIR]
    ctp_lib_names = ["iconv"]
    MD_LIB = os.path.join(API_PLAT_DIR, "thostmduserapi_se.framework/Versions/A/thostmduserapi_se")
    TRADER_LIB = os.path.join(
        API_PLAT_DIR, "thosttraderapi_se.framework/Versions/A/thosttraderapi_se"
    )
    ctp_link_args = ["-Wl,-rpath,@loader_path", MD_LIB, TRADER_LIB]
    ctp_compile_args = []
    # 打包时需要复制的 framework 文件 (glob 相对 bt_api_py/ctp/)
    ctp_package_data = [
        "api/%s/darwin/thostmduserapi_se.framework/**/*" % CTP_API_VER,
        "api/%s/darwin/thosttraderapi_se.framework/**/*" % CTP_API_VER,
    ]
    _CTP_RUNTIME_LIBS = [MD_LIB, TRADER_LIB]  # framework dylib 路径

elif sys.platform.startswith("linux"):
    # ---- Linux: 使用 .so ----
    API_PLAT_DIR = os.path.join(CTP_API_DIR, "linux")
    API_LIBS = glob.glob(os.path.join(API_PLAT_DIR, "*.so"))
    ctp_inc_dirs = [API_PLAT_DIR]
    ctp_lib_dirs = [API_PLAT_DIR]
    ctp_lib_names = [pathlib.Path(p).stem[3:] for p in API_LIBS]  # strip "lib" prefix
    ctp_link_args = ["-Wl,-rpath,$ORIGIN"]
    ctp_compile_args = []
    ctp_package_data = ["api/%s/linux/*.so" % CTP_API_VER]
    _CTP_RUNTIME_LIBS = API_LIBS
    # Add libiconv if available (needed by CTP libraries)
    if os.path.exists(os.path.join(sys.prefix, "lib", "libiconv.so")):
        ctp_lib_names.append("iconv")
        ctp_lib_dirs.append(os.path.join(sys.prefix, "lib"))

elif sys.platform.startswith("win"):
    # ---- Windows: 使用 .dll + .lib ----
    API_PLAT_DIR = os.path.join(CTP_API_DIR, "windows")
    API_DLLS = glob.glob(os.path.join(API_PLAT_DIR, "*.dll"))
    ctp_lib_names = [pathlib.Path(p).stem for p in API_DLLS] + ["iconv"]
    base_prefix = sysconfig.get_config_var("base") or sys.prefix
    iconv_prefixes = _windows_iconv_prefixes()
    ctp_inc_dirs = _existing_paths(
        [
            API_PLAT_DIR,
            os.path.join(base_prefix, "Library", "include"),
            *[os.path.join(prefix, "Library", "include") for prefix in iconv_prefixes],
        ]
    )
    ctp_lib_dirs = _existing_paths(
        [
            API_PLAT_DIR,
            os.path.join(base_prefix, "Library", "lib"),
            *[os.path.join(prefix, "Library", "lib") for prefix in iconv_prefixes],
        ]
    )
    ctp_link_args = []
    ctp_compile_args = ["/utf-8", "/wd4101"]
    ctp_package_data = [
        "api/%s/windows/*.dll" % CTP_API_VER,
    ]
    _CTP_RUNTIME_LIBS = API_DLLS
else:
    print("Warning: Platform", sys.platform, "not supported for CTP extension")
    _CTP_RUNTIME_LIBS = []

# ---------------------------------------------------------------------------
#  CTP Extension
# ---------------------------------------------------------------------------
CTP_EXT = Extension(
    "bt_api_py.ctp._ctp",
    sources=[os.path.join(CTP_PKG_DIR, "ctp_wrap.cpp")],
    include_dirs=ctp_inc_dirs,
    library_dirs=ctp_lib_dirs,
    libraries=ctp_lib_names,
    extra_link_args=ctp_link_args,
    extra_compile_args=ctp_compile_args,
    language="c++",
)

# ---------------------------------------------------------------------------
#  Cython Extension (calculate_numbers)
# ---------------------------------------------------------------------------


def _opt_flag(level: int) -> str:
    return f"/O{level}" if sys.platform == "win32" else f"-O{level}"


def _cpp_std(ver: str) -> str:
    return f"-std:{ver}" if sys.platform == "win32" else f"-std={ver}"


def _link_flag(flag: str) -> str:
    return f"/{flag}" if sys.platform == "win32" else f"-{flag}"


_cython_link_args = []
if sys.platform.startswith("linux"):
    _cython_link_args = ["-lgomp"]
elif sys.platform.startswith("win"):
    _cython_link_args = []  # MSVC 使用 /openmp 编译参数
# macOS: 不链接 gomp (系统 clang 不自带 OpenMP)

CYTHON_EXT = Extension(
    name="bt_api_py.functions.calculate_number.calculate_numbers_by_cython",
    sources=["bt_api_py/functions/calculate_number/calculate_numbers.pyx"],
    include_dirs=[np.get_include(), "bt_api_py/functions/calculate_number"],
    language="c++",
    extra_compile_args=[_opt_flag(2), _cpp_std("c++11")],
    extra_link_args=_cython_link_args,
)

# ---------------------------------------------------------------------------
#  自定义 BuildExt: 编译后将 CTP runtime 库复制到 _ctp.so 所在目录
#  兼容 pip install / pip install -e . / python setup.py build_ext --inplace
# ---------------------------------------------------------------------------


def _ignore_ds(d, files):
    return [f for f in files if f == ".DS_Store"]


class _BuildExt(build_ext):
    """编译后将 CTP runtime 库 (.so/.dll/.framework) 复制到扩展输出目录

    如果编译失败 (例如 LFS 指针文件未拉取、缺少 CTP 库)，
    则打印警告并继续安装纯 Python 部分。
    """

    def run(self):
        try:
            super().run()
        except Exception as e:
            print(f"\n*** WARNING: Failed to build C/C++ extensions: {e}")
            print("*** Pure-Python install will continue without native extensions.\n")
            return
        self._copy_ctp_runtime_libs()

    def build_extension(self, ext):
        try:
            super().build_extension(ext)
        except Exception as e:
            print(f"\n*** WARNING: Skipping extension '{ext.name}': {e}\n")
            return

    def _copy_ctp_runtime_libs(self):
        # 找到 _ctp extension 的输出路径, 从中推导目标目录
        ctp_ext = None
        for ext in self.extensions:
            if ext.name == "bt_api_py.ctp._ctp":
                ctp_ext = ext
                break
        if ctp_ext is None:
            return

        ext_fullpath = self.get_ext_fullpath(ctp_ext.name)
        dst_ctp_dir = os.path.dirname(ext_fullpath)
        os.makedirs(dst_ctp_dir, exist_ok=True)

        if sys.platform.startswith("darwin"):
            for fw_name in ("thostmduserapi_se.framework", "thosttraderapi_se.framework"):
                src_fw = os.path.join(API_PLAT_DIR, fw_name)
                dst_fw = os.path.join(dst_ctp_dir, fw_name)
                if os.path.exists(dst_fw):
                    shutil.rmtree(dst_fw)
                shutil.copytree(src_fw, dst_fw, symlinks=True, ignore=_ignore_ds)
        else:
            for lib_path in _CTP_RUNTIME_LIBS:
                dst = os.path.join(dst_ctp_dir, os.path.basename(lib_path))
                shutil.copy2(lib_path, dst)


# ---------------------------------------------------------------------------
#  package_data
# ---------------------------------------------------------------------------
pkg_data = {
    "bt_api_py": [
        "configs/*",
        "functions/calculate_number/*",
        "functions/update_data/*",
    ],
    "bt_api_py.ctp": [
        "ctp.py",  # 向后兼容垫片 (auto-generated)
        "_ctp_base.py",  # SWIG 基础设施 (auto-generated)
        "ctp_*.py",  # 拆分后的子模块 (auto-generated)
        "client.py",  # 高层封装
        "api/**/*",  # API 头文件 & 库文件
    ]
    + ctp_package_data,  # runtime 库
}

# ---------------------------------------------------------------------------
#  setup()
#  Metadata (name, author, classifiers, dependencies, …) lives in
#  pyproject.toml — the single source of truth.  setup.py only provides
#  version (dynamic) + build-time artefacts that pyproject.toml cannot express.
# ---------------------------------------------------------------------------
setup(
    version=VERSION,
    packages=find_packages(include=["bt_api_py", "bt_api_py.*"], exclude=["tests"]),
    package_data=pkg_data,
    ext_modules=[CYTHON_EXT, CTP_EXT],
    cmdclass={
        "build_ext": _BuildExt,
    },
)
