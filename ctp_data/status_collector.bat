@echo off
call "%~dp0start_collector.bat" --status %*
exit /b %ERRORLEVEL%
