from setuptools import setup, Extension
from Cython.Build import cythonize
import Cython.Compiler.Options
import numpy as np
import sys

Cython.Compiler.Options.annotate = True


def setg_optimize_option(arg: str) -> str:
    if sys.platform == 'win32':
        return f'/O{arg}'
    elif sys.platform == 'linux':
        return f'-O{arg}'
    # if


# def

def set_compile_args(arg: str) -> str:
    if sys.platform == 'win32':
        return f'/{arg}'
    elif sys.platform == 'linux':
        return f'-f{arg}'
    # if


# def

def set_extra_link_args(arg: str) -> str:
    if sys.platform == 'win32':
        return f'/{arg}'
    elif sys.platform == 'linux':
        return f'-{arg}'
    # if


# def

def set_cpp_version(ver: str) -> str:
    if sys.platform == 'win32':
        return f'-std:{ver}'
    elif sys.platform == 'linux':
        return f'-std={ver}'
    # if


# def

# -O3 -march=native
ext = Extension(
    "calculate_numbers_by_cython", sources=["calculate_numbers.pyx"],
    include_dirs=[np.get_include()],
    language='c++',
    extra_compile_args=[
        setg_optimize_option(""),
        set_compile_args('openmp'),
        # set_compile_args('lpthread'),
        set_cpp_version('c++17'),
        # "-march=native"
    ],
    extra_link_args=[
        set_extra_link_args('lgomp'),
    ]
)

setup(name="calculate_number_by_cython", ext_modules=cythonize([ext]))
# 编译使用python setup_run.py build_ext --inplace
