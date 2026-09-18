@echo off
rem
rem One-shot installer for bt_api_py plus all its submodule packages -- Windows
rem thin launcher.
rem
rem The real logic lives in scripts\install_bt_api_submodules.py (cross-platform).
rem This script only picks a Python 3.11+ interpreter, calls the installer with
rem the default arguments, then runs the doctor self check.
rem
rem Default (core + all 15 submodules, source-first, editable, reinstall what is
rem already installed):
rem   --with-root --editable --editable-root --upgrade
rem
rem Usage (identical to install_all.sh):
rem   scripts\install_all.bat                     :: core + every submodule
rem   scripts\install_all.bat ctp                 :: subset: positional = packages
rem   scripts\install_all.bat --strategy none     :: report the install status only
rem   scripts\install_all.bat --dry-run           :: print the pip commands only
rem   set PYTHON=C:\path\to\python.exe & scripts\install_all.bat
rem
rem NOTE: keep this file pure ASCII -- cmd.exe reads .bat files with the OEM code
rem       page, and non-ASCII text can break the batch parsing on a GBK console.
rem
setlocal

set "SCRIPT_DIR=%~dp0"
for %%I in ("%SCRIPT_DIR%..") do set "REPO_ROOT=%%~fI"
set "INSTALLER=%SCRIPT_DIR%install_bt_api_submodules.py"

if not exist "%INSTALLER%" (
    echo [install_all] ERROR: installer not found: "%INSTALLER%"
    exit /b 2
)

rem The installer imports tomllib, so Python 3.11+ is required.
set "PYTHON_BIN=%PYTHON%"
if not defined PYTHON_BIN set "PYTHON_BIN=python"
"%PYTHON_BIN%" -c "import tomllib" >nul 2>nul
if not errorlevel 1 goto python_ok

set "PYTHON_BIN=py"
"%PYTHON_BIN%" -c "import tomllib" >nul 2>nul
if not errorlevel 1 goto python_ok

echo [install_all] ERROR: no Python 3.11+ interpreter found (the installer needs tomllib)
echo [install_all] install Python 3.11+, or set PYTHON=C:\path\to\python.exe
exit /b 2

:python_ok
set "ARGS=%INSTALL_ALL_ARGS%"
if not defined ARGS set "ARGS=--with-root --editable --editable-root --upgrade"

echo [install_all] interpreter: %PYTHON_BIN%
echo [install_all] args: %ARGS% %*
echo [install_all] repo root: %REPO_ROOT%
echo.

pushd "%REPO_ROOT%" >nul
"%PYTHON_BIN%" "%INSTALLER%" %ARGS% %*
set "RC=%ERRORLEVEL%"
echo.
echo [install_all] self check (python -m bt_api_py.doctor --bundle core-reference):
"%PYTHON_BIN%" -m bt_api_py.doctor --bundle core-reference
popd >nul

if not "%RC%"=="0" echo [install_all] installer exited with %RC%, check the output above
exit /b %RC%
