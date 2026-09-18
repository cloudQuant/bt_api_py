@echo off
rem
rem 一键安装 bt_api_py 及其全部子模块包 —— Windows 薄启动器
rem
rem 真正的安装逻辑在 scripts\install_bt_api_submodules.py（跨平台）。
rem 本脚本只负责：挑一个 Python 3.11+ 解释器 -> 用默认参数调安装器 -> 跑 doctor 自检。
rem
rem 默认行为（核心 + 全部 15 个子模块，源码优先、editable、已装的也重装）：
rem   --with-root --editable --editable-root --upgrade
rem
rem 用法（与 install_all.sh 完全一致）：
rem   scripts\install_all.bat                     :: 核心 + 全部子模块
rem   scripts\install_all.bat ctp                 :: 只装子集：位置参数 = 包子集
rem   scripts\install_all.bat --strategy none     :: 只体检当前安装状态
rem   scripts\install_all.bat --dry-run           :: 只打印将要执行的 pip 命令
rem   set PYTHON=C:\path\to\python.exe & scripts\install_all.bat
rem
setlocal

set "SCRIPT_DIR=%~dp0"
for %%I in ("%SCRIPT_DIR%..") do set "REPO_ROOT=%%~fI"
set "INSTALLER=%SCRIPT_DIR%install_bt_api_submodules.py"

if not exist "%INSTALLER%" (
    echo [install_all] 错误: 找不到安装器 "%INSTALLER%"
    exit /b 2
)

rem 安装器用 tomllib，因此必须是 Python 3.11+
set "PYTHON_BIN=%PYTHON%"
if not defined PYTHON_BIN set "PYTHON_BIN=python"
"%PYTHON_BIN%" -c "import tomllib" >nul 2>nul
if not errorlevel 1 goto python_ok

set "PYTHON_BIN=py"
"%PYTHON_BIN%" -c "import tomllib" >nul 2>nul
if not errorlevel 1 goto python_ok

echo [install_all] 错误: 找不到 Python 3.11+ 解释器（安装器需要 tomllib）
echo [install_all] 请安装 Python 3.11+，或用 set PYTHON=C:\path\to\python.exe 指定
exit /b 2

:python_ok
set "ARGS=%INSTALL_ALL_ARGS%"
if not defined ARGS set "ARGS=--with-root --editable --editable-root --upgrade"

echo [install_all] 解释器: %PYTHON_BIN%
echo [install_all] 安装参数: %ARGS% %*
echo [install_all] 安装目录: %REPO_ROOT%
echo.

pushd "%REPO_ROOT%" >nul
"%PYTHON_BIN%" "%INSTALLER%" %ARGS% %*
set "RC=%ERRORLEVEL%"
echo.
echo [install_all] 安装结果自检（python -m bt_api_py.doctor --bundle core-reference）:
"%PYTHON_BIN%" -m bt_api_py.doctor --bundle core-reference
popd >nul

if not "%RC%"=="0" echo [install_all] 安装器返回非 0（%RC%），请检查上面的输出
exit /b %RC%
