@echo off
call "%~dp0start_collector.bat" --stop %*
exit /b %ERRORLEVEL%
