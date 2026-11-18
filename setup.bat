@echo off
rem ============================================================
rem  MessageHub one-click installer launcher (ASCII only).
rem  All real logic lives in setup.ps1 (UTF-8 with BOM).
rem  Why: cmd.exe batch parsing of UTF-8 Chinese text is unsafe
rem  (byte-offset desync after chcp 65001), PowerShell reads
rem  .ps1 by BOM and is locale-independent.
rem ============================================================
setlocal
set "ROOT=%~dp0"
if "%ROOT:~-1%"=="\" set "ROOT=%ROOT:~0,-1%"
cd /d "%ROOT%"

rem -ExecutionPolicy Bypass: setup scripts are not signed.
rem -NoProfile: do not load user profile, faster and cleaner.
rem -File "%ROOT%\setup.ps1": absolute path, works from any cwd.
powershell.exe -NoProfile -ExecutionPolicy Bypass -File "%ROOT%\setup.ps1"

rem propagate failure to double-click users (window stays open via ps1's pause)
if errorlevel 1 exit /b 1
exit /b 0
