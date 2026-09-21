@echo off
rem Stage root-repository gitlinks from the current direct-submodule HEADs.
rem This Windows entry point runs the POSIX implementation via Git for Windows.
rem Usage: scripts\update_gitlinks.bat
rem        scripts\update_gitlinks.bat --check
setlocal

set "SCRIPT=%~dp0update_gitlinks.sh"
set "BASH="

where bash.exe >nul 2>&1
if not errorlevel 1 set "BASH=bash.exe"
if not defined BASH if exist "%ProgramFiles%\Git\bin\bash.exe" set "BASH=%ProgramFiles%\Git\bin\bash.exe"
if not defined BASH if exist "%ProgramFiles(x86)%\Git\bin\bash.exe" set "BASH=%ProgramFiles(x86)%\Git\bin\bash.exe"
if not defined BASH if exist "%LocalAppData%\Programs\Git\bin\bash.exe" set "BASH=%LocalAppData%\Programs\Git\bin\bash.exe"
if not defined BASH goto no_bash

"%BASH%" "%SCRIPT%" %*
exit /b %errorlevel%

:no_bash
echo ERROR: bash.exe not found. Install Git for Windows at https://gitforwindows.org 1>&2
exit /b 1
