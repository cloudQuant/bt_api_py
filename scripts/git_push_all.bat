@echo off
rem git_push_all.bat — Windows 入口：通过 Git for Windows 自带的 bash 执行 git_push_all.sh
rem 用法与参数与 git_push_all.sh 完全一致（如 git_push_all.bat -j 4 --no-verify）
setlocal

set "SCRIPT=%~dp0git_push_all.sh"

rem 依次探测 bash：PATH -> 常见 Git for Windows 安装位置
where bash.exe >nul 2>&1
if %errorlevel%==0 (
    bash "%SCRIPT%" %*
    exit /b %errorlevel%
)
if exist "%ProgramFiles%\Git\bin\bash.exe" (
    "%ProgramFiles%\Git\bin\bash.exe" "%SCRIPT%" %*
    exit /b %errorlevel%
)
if exist "%ProgramFiles(x86)%\Git\bin\bash.exe" (
    "%ProgramFiles(x86)%\Git\bin\bash.exe" "%SCRIPT%" %*
    exit /b %errorlevel%
)
if exist "%LocalAppData%\Programs\Git\bin\bash.exe" (
    "%LocalAppData%\Programs\Git\bin\bash.exe" "%SCRIPT%" %*
    exit /b %errorlevel%
)

echo ERROR: bash.exe not found. Install Git for Windows ^(https://gitforwindows.org^) 1>&2
exit /b 1
