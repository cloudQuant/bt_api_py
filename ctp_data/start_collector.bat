@echo off
rem
rem 迭代04 CTP 全市场 tick 采集 —— 启动脚本（Windows）
rem
rem 行为：前台运行到本时段收盘（相当于 --until-close --wait-open）。
rem   * 自动加载仓库根目录的 .env（collector 自己不读 .env）
rem   * 优先使用仓库里的 bt_api_ctp 源码，避免静默命中 site-packages 里的旧安装
rem   * 自动检查 python 能否 import bt_api_ctp
rem   * Ctrl+C 触发优雅停机：停订阅 -> flush -> finalize 写 report.json
rem
rem 用法：
rem   ctp_data\start_collector.bat                 :: 跑到收盘
rem   ctp_data\start_collector.bat -v              :: 额外参数原样透传
rem   set PYTHON=C:\path\to\python.exe & ctp_data\start_collector.bat
rem   set COLLECTOR_ARGS=--once --duration 60 & ctp_data\start_collector.bat
rem
rem 注意：.env 只支持简单的 KEY=VALUE 行，# 开头为注释，值不能含空格或 ! & ^ 等特殊字符。
rem
setlocal

set "SCRIPT_DIR=%~dp0"
set "CONFIG=%SCRIPT_DIR%collector.yaml"
for %%I in ("%SCRIPT_DIR%..") do set "REPO_ROOT=%%~fI"
set "ENV_FILE=%REPO_ROOT%\.env"

if not exist "%CONFIG%" (
    echo [start_collector] 错误: 找不到配置 "%CONFIG%"
    echo [start_collector] 请先复制模板: copy "%SCRIPT_DIR%collector.example.yaml" "%CONFIG%"
    echo [start_collector] 并修改其中的 data_root
    exit /b 2
)

if exist "%ENV_FILE%" (
    echo [start_collector] 加载环境变量: %ENV_FILE%
    for /f "usebackq eol=# tokens=1* delims==" %%A in ("%ENV_FILE%") do (
        if not "%%~A"=="" set "%%~A=%%~B"
    )
) else (
    echo [start_collector] 警告: %ENV_FILE% 不存在
    echo [start_collector] 请确认 CTP_MD_FRONT / CTP_TD_FRONT / CTP_BROKER_ID / CTP_USER_ID / CTP_PASSWORD 已在当前环境中设置
)

rem 限制第三方数值库的线程池大小（默认等于 CPU 核数）。采集本身是单进程：
rem 1 个主线程 + tick-compact + ctp-resubscribe 两个工作线程 + CTP 原生线程；
rem 这里限制的是 pyarrow / numexpr 的并行度。想放开就先 set 这些变量。
if not defined OMP_NUM_THREADS set "OMP_NUM_THREADS=4"
if not defined NUMEXPR_MAX_THREADS set "NUMEXPR_MAX_THREADS=4"
if not defined NUMEXPR_NUM_THREADS set "NUMEXPR_NUM_THREADS=4"

rem 仓库根目录下没有 bt_api_ctp 这个包目录（源码在 bt_api\bt_api_ctp\src\），
rem 不显式指定 PYTHONPATH 时会静默命中 site-packages 里可能已过时的安装版本。
rem 源码目录存在就放到 PYTHONPATH 最前；纯部署环境则回退到已安装版本。
set "REPO_SRC=%REPO_ROOT%\bt_api\bt_api_ctp\src"
if exist "%REPO_SRC%\bt_api_ctp" (
    if defined PYTHONPATH set "PYTHONPATH=%REPO_SRC%;%PYTHONPATH%"
    if not defined PYTHONPATH set "PYTHONPATH=%REPO_SRC%"
)

set "PYTHON_BIN=%PYTHON%"
if not defined PYTHON_BIN set "PYTHON_BIN=python"
"%PYTHON_BIN%" -c "import bt_api_ctp.collector" >nul 2>nul
if not errorlevel 1 goto python_ok

set "PYTHON_BIN=py"
"%PYTHON_BIN%" -c "import bt_api_ctp.collector" >nul 2>nul
if not errorlevel 1 goto python_ok

echo [start_collector] 错误: 找不到能 import bt_api_ctp 的 python
echo [start_collector] 请先在仓库根目录执行 pip install -e .，或用 set PYTHON=C:\path\to\python.exe 指定
exit /b 2

:python_ok
set "ARGS=%COLLECTOR_ARGS%"
if not defined ARGS set "ARGS=--until-close --wait-open"

rem 打印实际使用的 bt_api_ctp 位置：版本漂移只有在这里看得见。
set "MODULE_FILE="
"%PYTHON_BIN%" -c "import bt_api_ctp;print(bt_api_ctp.__file__)" > "%TEMP%\bt_api_ctp_path.txt" 2>nul
if not exist "%TEMP%\bt_api_ctp_path.txt" goto module_path_done
set /p MODULE_FILE=<"%TEMP%\bt_api_ctp_path.txt"
del "%TEMP%\bt_api_ctp_path.txt" >nul 2>nul
:module_path_done

rem 打印 data_root 解析后的绝对路径：相对路径相对仓库根，而脚本下面会 cd 到仓库根，
rem 所以算出来的就是数据真正落盘的位置。仅按行读取 `data_root: <值>`，不要加行尾注释。
set "DATA_ROOT="
for /f "tokens=1,* delims=: " %%A in ('findstr /b /c:"data_root:" "%CONFIG%"') do set "DATA_ROOT=%%B"

echo [start_collector] 解释器: %PYTHON_BIN%
echo [start_collector] 配置: %CONFIG%
echo [start_collector] 线程上限: OMP_NUM_THREADS=%OMP_NUM_THREADS% NUMEXPR_MAX_THREADS=%NUMEXPR_MAX_THREADS%
if not defined MODULE_FILE goto module_report_done
echo [start_collector] bt_api_ctp: %MODULE_FILE%
:module_report_done
if not defined DATA_ROOT goto data_root_done
set "RESOLVED=%DATA_ROOT%"
if "%DATA_ROOT:~1,1%"==":" goto data_root_ready
if "%DATA_ROOT:~0,1%"=="\" goto data_root_ready
set "RESOLVED=%REPO_ROOT%\%DATA_ROOT%"
:data_root_ready
echo [start_collector] 数据根目录: %RESOLVED%
:data_root_done
echo [start_collector] 参数: %ARGS% %*
echo [start_collector] 按 Ctrl+C 优雅停机（不要强杀进程，会丢失未压缩的数据）

pushd "%REPO_ROOT%" >nul
"%PYTHON_BIN%" -m bt_api_ctp.collector --config "%CONFIG%" %ARGS% %*
set "RC=%ERRORLEVEL%"
popd >nul
exit /b %RC%
