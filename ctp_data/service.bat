@echo off
rem Background supervisor by default; --foreground runs it in this console.
rem --dry-run prints the next schedule without connecting to CTP.
call "%~dp0start_collector.bat" --service %*
exit /b %ERRORLEVEL%
