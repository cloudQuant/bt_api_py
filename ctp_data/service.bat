@echo off
rem
rem 常驻调度器启动器（Windows）
rem
rem 与 start_collector.bat 的区别：那个只跑一个时段（跑到收盘就退出），这个长期活着，
rem 按下一个开盘时刻反复拉起同一条采集命令。
rem
rem 用法：
rem   ctp_data\service.bat                      :: 常驻前台运行，Ctrl+C 停止
rem   ctp_data\service.bat --dry-run            :: 只打印调度计划，不启动采集
rem   set COLLECTOR_SERVICE_ARGS=--lead 600 & ctp_data\service.bat
rem   set PYTHON=C:\path\to\python.exe & ctp_data\service.bat
rem
rem 注意：.env 只支持简单的 KEY=VALUE 行，# 开头为注释，值不能含空格或 ! & ^ 等特殊字符。
rem
setlocal

set "SCRIPT_DIR=%~dp0"
for %%I in ("%SCRIPT_DIR%..") do set "REPO_ROOT=%%~fI"
set "ENV_FILE=%REPO_ROOT%\.env"

if exist "%ENV_FILE%" (
    echo [ctp_service] 加载环境变量: %ENV_FILE%
    for /f "usebackq eol=# tokens=1* delims==" %%A in ("%ENV_FILE%") do (
        if not "%%~A"=="" set "%%~A=%%~B"
    )
) else (
    echo [ctp_service] 警告: %ENV_FILE% 不存在
    echo [ctp_service] 请确认 CTP_MD_FRONT / CTP_TD_FRONT / CTP_BROKER_ID / CTP_USER_ID / CTP_PASSWORD 已在当前环境中设置
)

rem 仓库源码优先（与 start_collector.bat 一致）：否则会静默用到 site-packages 里的旧安装。
set "REPO_SRC=%REPO_ROOT%\bt_api\bt_api_ctp\src"
if exist "%REPO_SRC%\bt_api_ctp" (
    if defined PYTHONPATH set "PYTHONPATH=%REPO_SRC%;%PYTHONPATH%"
    if not defined PYTHONPATH set "PYTHONPATH=%REPO_SRC%"
)

rem 数值库线程池上限（与 start_collector.bat 一致）。
if not defined OMP_NUM_THREADS set "OMP_NUM_THREADS=4"
if not defined NUMEXPR_MAX_THREADS set "NUMEXPR_MAX_THREADS=4"
if not defined NUMEXPR_NUM_THREADS set "NUMEXPR_NUM_THREADS=4"

set "PYTHON_BIN=%PYTHON%"
if not defined PYTHON_BIN set "PYTHON_BIN=python"
"%PYTHON_BIN%" -c "import bt_api_ctp.collector.schedule" >nul 2>nul
if not errorlevel 1 goto python_ok

set "PYTHON_BIN=py"
"%PYTHON_BIN%" -c "import bt_api_ctp.collector.schedule" >nul 2>nul
if not errorlevel 1 goto python_ok

echo [ctp_service] 错误: 找不到能 import bt_api_ctp 的 python
echo [ctp_service] 请先在仓库根目录执行 pip install -e .，或用 set PYTHON=C:\path\to\python.exe 指定
exit /b 2

:python_ok
set "SVC_ARGS=%COLLECTOR_SERVICE_ARGS%"

echo [ctp_service] 解释器: %PYTHON_BIN%
echo [ctp_service] 配置: %SCRIPT_DIR%collector.yaml（可用 --config 覆盖）
echo [ctp_service] 参数: %SVC_ARGS% %*
echo [ctp_service] Ctrl+C 停止：会先让当前采集优雅收尾再退出

pushd "%REPO_ROOT%" >nul
rem 任务计划程序没有控制台，日志落到文件（service.py 内部按 5MB × 3 轮转）。
rem SERVICE_LOG 可覆盖路径；不作为常驻服务时也可以删掉 --log-file 直接看终端。
if not defined SERVICE_LOG set "SERVICE_LOG=%SCRIPT_DIR%service.log"
"%PYTHON_BIN%" "%SCRIPT_DIR%service.py" --log-file "%SERVICE_LOG%" %SVC_ARGS% %*
set "RC=%ERRORLEVEL%"
popd >nul
exit /b %RC%
