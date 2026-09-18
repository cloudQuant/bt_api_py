@echo off
rem
rem Iteration 04 CTP whole-market tick collector -- Windows launcher
rem
rem Behaviour: run in the foreground until the end of this session group
rem (= --until-close --wait-open).
rem   * loads the repo-root .env (the collector itself does not read .env)
rem   * prefers the bt_api_ctp source in this repo, so a stale install in
rem     site-packages cannot be picked up silently
rem   * checks that an interpreter can import bt_api_ctp
rem   * Ctrl+C stops gracefully: unsubscribe -> flush -> finalize report.json
rem
rem Usage:
rem   ctp_data\start_collector.bat                 :: run to the session close
rem   ctp_data\start_collector.bat -v              :: extra args are forwarded
rem   set PYTHON=C:\path\to\python.exe & ctp_data\start_collector.bat
rem   set COLLECTOR_ARGS=--once --duration 60 & ctp_data\start_collector.bat
rem
rem NOTE: .env supports only simple KEY=VALUE lines (# starts a comment; values
rem       must not contain spaces or ! & ^ etc.).
rem NOTE: cmd.exe reads .bat files with the OEM code page. Keep this file pure
rem       ASCII: non-ASCII text is mis-decoded on a GBK console and can break
rem       the batch parsing itself.
rem
setlocal

set "SCRIPT_DIR=%~dp0"
set "CONFIG=%SCRIPT_DIR%collector.yaml"
for %%I in ("%SCRIPT_DIR%..") do set "REPO_ROOT=%%~fI"
set "ENV_FILE=%REPO_ROOT%\.env"

if not exist "%CONFIG%" (
    echo [start_collector] ERROR: config not found: "%CONFIG%"
    echo [start_collector] copy the template first:
    echo [start_collector]   copy "%SCRIPT_DIR%collector.example.yaml" "%CONFIG%"
    echo [start_collector] then edit data_root in it
    exit /b 2
)

if exist "%ENV_FILE%" (
    echo [start_collector] loading env: %ENV_FILE%
    for /f "usebackq eol=# tokens=1* delims==" %%A in ("%ENV_FILE%") do (
        if not "%%~A"=="" set "%%~A=%%~B"
    )
) else (
    echo [start_collector] WARNING: %ENV_FILE% not found
    echo [start_collector] make sure CTP_MD_FRONT / CTP_TD_FRONT / CTP_BROKER_ID / CTP_USER_ID / CTP_PASSWORD are set, or fill them into collector.yaml
)

rem Cap the numeric libraries' thread pools (they default to the CPU count).
rem Collection itself is a single process: one main thread, the tick-compact and
rem ctp-resubscribe workers, plus the native CTP threads.
if not defined OMP_NUM_THREADS set "OMP_NUM_THREADS=4"
if not defined NUMEXPR_MAX_THREADS set "NUMEXPR_MAX_THREADS=4"
if not defined NUMEXPR_NUM_THREADS set "NUMEXPR_NUM_THREADS=4"

rem There is no bt_api_ctp package directory at the repo root (the source lives
rem in bt_api\bt_api_ctp\src), so without this PYTHONPATH a possibly stale
rem install in site-packages would be used silently. Prefer the source when it
rem exists; a pure deployment falls back to the installed package.
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

echo [start_collector] ERROR: no python interpreter can import bt_api_ctp
echo [start_collector] run "pip install -e ." in the repo root, or set PYTHON=C:\path\to\python.exe
exit /b 2

:python_ok
set "ARGS=%COLLECTOR_ARGS%"
if not defined ARGS set "ARGS=--until-close --wait-open"

rem Report which bt_api_ctp is actually used: version drift is only visible here.
set "MODULE_FILE="
"%PYTHON_BIN%" -c "import bt_api_ctp;print(bt_api_ctp.__file__)" > "%TEMP%\bt_api_ctp_path.txt" 2>nul
if not exist "%TEMP%\bt_api_ctp_path.txt" goto module_path_done
set /p MODULE_FILE=<"%TEMP%\bt_api_ctp_path.txt"
del "%TEMP%\bt_api_ctp_path.txt" >nul 2>nul
:module_path_done

rem Resolve data_root: a relative value is relative to the repo root (this
rem script cd's there below), so this is where data actually lands. Only a
rem single-line "data_root: <value>" is read, with no trailing comment.
set "DATA_ROOT="
for /f "tokens=1,* delims=: " %%A in ('findstr /b /c:"data_root:" "%CONFIG%"') do set "DATA_ROOT=%%B"

echo [start_collector] interpreter: %PYTHON_BIN%
echo [start_collector] config: %CONFIG%
echo [start_collector] thread caps: OMP_NUM_THREADS=%OMP_NUM_THREADS% NUMEXPR_MAX_THREADS=%NUMEXPR_MAX_THREADS%
if not defined MODULE_FILE goto module_report_done
echo [start_collector] bt_api_ctp: %MODULE_FILE%
:module_report_done
if not defined DATA_ROOT goto data_root_done
set "RESOLVED=%DATA_ROOT%"
if "%DATA_ROOT:~1,1%"==":" goto data_root_ready
if "%DATA_ROOT:~0,1%"=="\" goto data_root_ready
set "RESOLVED=%REPO_ROOT%\%DATA_ROOT%"
:data_root_ready
echo [start_collector] data root: %RESOLVED%
:data_root_done
echo [start_collector] args: %ARGS% %*
echo [start_collector] Ctrl+C stops gracefully (do not kill the process: unflushed data would be lost)

pushd "%REPO_ROOT%" >nul
"%PYTHON_BIN%" -m bt_api_ctp.collector --config "%CONFIG%" %ARGS% %*
set "RC=%ERRORLEVEL%"
popd >nul
exit /b %RC%
