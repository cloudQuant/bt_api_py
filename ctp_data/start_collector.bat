@echo off
rem Default: hidden persistent background collector service.
rem Use --foreground for one console run; --status and --stop manage the service.
rem Keep this file ASCII. Python parses .env safely.
setlocal
set "PYTHON_BIN=%PYTHON%"
if defined PYTHON_BIN goto explicit_python
set "PYTHON_BIN=python"
"%PYTHON_BIN%" -c "import sys" >nul 2>nul
if not errorlevel 1 goto python_ok
set "PYTHON_BIN=py"
:explicit_python
"%PYTHON_BIN%" -c "import sys" >nul 2>nul
if not errorlevel 1 goto python_ok
echo [start_collector] ERROR: Python interpreter unavailable: %PYTHON_BIN%
echo [start_collector] Set PYTHON to the Python executable with collector dependencies.
exit /b 2
:python_ok
"%PYTHON_BIN%" -u "%~dp0collector_entry.py" %*
exit /b %ERRORLEVEL%
