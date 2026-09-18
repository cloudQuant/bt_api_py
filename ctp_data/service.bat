@echo off
rem
rem Long-running collector supervisor -- Windows launcher
rem
rem Difference from start_collector.bat: that one covers a single session group
rem (it exits at the close), this one stays alive and starts the same collection
rem command again before every session open.
rem
rem Usage:
rem   ctp_data\service.bat                      :: run in the foreground, Ctrl+C stops
rem   ctp_data\service.bat --dry-run            :: print the schedule only
rem   set COLLECTOR_SERVICE_ARGS=--lead 600 & ctp_data\service.bat
rem   set PYTHON=C:\path\to\python.exe & ctp_data\service.bat
rem
rem NOTE: .env supports only simple KEY=VALUE lines (# starts a comment; values
rem       must not contain spaces or ! & ^ etc.).
rem NOTE: keep this file pure ASCII -- cmd.exe reads .bat files with the OEM code
rem       page, and non-ASCII text can break the batch parsing on a GBK console.
rem
setlocal

set "SCRIPT_DIR=%~dp0"
for %%I in ("%SCRIPT_DIR%..") do set "REPO_ROOT=%%~fI"
set "ENV_FILE=%REPO_ROOT%\.env"

if exist "%ENV_FILE%" (
    echo [ctp_service] loading env: %ENV_FILE%
    for /f "usebackq eol=# tokens=1* delims==" %%A in ("%ENV_FILE%") do (
        if not "%%~A"=="" set "%%~A=%%~B"
    )
) else (
    echo [ctp_service] WARNING: %ENV_FILE% not found
    echo [ctp_service] make sure CTP_MD_FRONT / CTP_TD_FRONT / CTP_BROKER_ID / CTP_USER_ID / CTP_PASSWORD are set, or fill them into collector.yaml
)

rem Prefer the repo source (same as start_collector.bat): otherwise a stale
rem install in site-packages would be used silently.
set "REPO_SRC=%REPO_ROOT%\bt_api\bt_api_ctp\src"
if exist "%REPO_SRC%\bt_api_ctp" (
    if defined PYTHONPATH set "PYTHONPATH=%REPO_SRC%;%PYTHONPATH%"
    if not defined PYTHONPATH set "PYTHONPATH=%REPO_SRC%"
)

rem Cap the numeric libraries' thread pools (same as start_collector.bat).
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

echo [ctp_service] ERROR: no python interpreter can import bt_api_ctp
echo [ctp_service] run "pip install -e ." in the repo root, or set PYTHON=C:\path\to\python.exe
exit /b 2

:python_ok
set "SVC_ARGS=%COLLECTOR_SERVICE_ARGS%"

echo [ctp_service] interpreter: %PYTHON_BIN%
echo [ctp_service] config: %SCRIPT_DIR%collector.yaml (override with --config)
echo [ctp_service] args: %SVC_ARGS% %*
echo [ctp_service] Ctrl+C stops: the running collection finalises first

pushd "%REPO_ROOT%" >nul
rem Task Scheduler gives no console, so the log goes to a file (service.py
rem rotates it at 5MB x 3). SERVICE_LOG overrides the path; drop --log-file to
rem watch the console when running interactively.
if not defined SERVICE_LOG set "SERVICE_LOG=%SCRIPT_DIR%service.log"
"%PYTHON_BIN%" "%SCRIPT_DIR%service.py" --log-file "%SERVICE_LOG%" %SVC_ARGS% %*
set "RC=%ERRORLEVEL%"
popd >nul
exit /b %RC%
