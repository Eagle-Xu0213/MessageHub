@echo off
chcp 65001 >nul
rem ============================================================
rem  MessageHub 开机自启安装器
rem  写入当前用户的 Run 注册表键 -> scripts\autostart_silent.vbs
rem  路径自动推导，换机器无需修改
rem  用法: install_autostart.bat [install|uninstall|status]
rem ============================================================
setlocal
set "RUNKEY=HKCU\Software\Microsoft\Windows\CurrentVersion\Run"
set "NAME=MessageHub"
set "VBS=%~dp0autostart_silent.vbs"

if /i "%~1"=="uninstall" goto :uninstall
if /i "%~1"=="status" goto :status

:install
if not exist "%VBS%" (
  echo [错误] 找不到 %VBS%
  exit /b 1
)
reg add "%RUNKEY%" /v %NAME% /t REG_SZ /d "wscript.exe \"%VBS%\"" /f >nul
if %errorlevel% neq 0 (
  echo [错误] 写入注册表失败，请检查权限。
  exit /b 1
)
echo [OK] 已安装开机自启（登录后静默运行，不弹窗口）。
echo   注册表项: %RUNKEY% / %NAME%
echo   取消自启: scripts\install_autostart.bat uninstall
goto :verify

:uninstall
reg delete "%RUNKEY%" /v %NAME% /f >nul 2>&1
echo [OK] 已移除开机自启。
goto :eof

:status
reg query "%RUNKEY%" /v %NAME% >nul 2>&1
if %errorlevel%==0 (
  echo [状态] 开机自启: 已启用
  reg query "%RUNKEY%" /v %NAME%
) else (
  echo [状态] 开机自启: 未启用
)
goto :eof

:verify
reg query "%RUNKEY%" /v %NAME% 2>nul | findstr /i "wscript" >nul && echo [验证] 下次登录生效。
