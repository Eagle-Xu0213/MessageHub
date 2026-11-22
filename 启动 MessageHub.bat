@echo off
chcp 65001 >nul
title MessageHub 服务
rem ============================================================
rem  MessageHub 启动入口
rem  自动探测 Python 与项目路径，换机器不用改本文件。
rem  可用环境变量覆盖：MESSAGEHUB_PYTHON / MOECT_EXE
rem ============================================================
setlocal
set "ROOT=%~dp0"
if "%ROOT:~-1%"=="\" set "ROOT=%ROOT:~0,-1%"
cd /d "%ROOT%"

rem ---------- 定位 Python ----------
set "PYTHON=%MESSAGEHUB_PYTHON%"
if not "%PYTHON%"=="" if exist "%PYTHON%" goto :python_ok

rem 1) 项目虚拟环境（推荐：python -m venv .venv）
if exist "%ROOT%\.venv\Scripts\python.exe" (
  set "PYTHON=%ROOT%\.venv\Scripts\python.exe"
  goto :python_ok
)

rem 2) PATH 里的 python
for /f "delims=" %%P in ('where python 2^>nul') do (
  set "PYTHON=%%P"
  goto :python_ok
)

rem 3) py 启动器
for /f "delims=" %%P in ('py -3 -c "import sys;print(sys.executable)" 2^>nul') do (
  set "PYTHON=%%P"
  goto :python_ok
)

echo [错误] 没有找到 Python。
echo   请先安装 Python 3.11+ 并勾选 "Add python.exe to PATH"，
echo   或在项目根目录执行：
echo     python -m venv .venv
echo     .venv\Scripts\pip install -e .
echo   也可以用环境变量 MESSAGEHUB_PYTHON 指定 python.exe 完整路径。
pause
exit /b 1

:python_ok
set "PYTHONPATH=%ROOT%\src"
set "PYTHONIOENCODING=utf-8"
set "COLLECTOR_PYTHON=%PYTHON%"
if exist "%ROOT%\.venv\Scripts\pythonw.exe" set "COLLECTOR_PYTHON=%ROOT%\.venv\Scripts\pythonw.exe"
set "MESSAGEHUB_ROOT=%ROOT%"

rem ---------- 服务已在运行则只打开页面 ----------
rem 服务在跑时不要重复启动 uvicorn（会端口冲突）；采集器有单实例锁会自己退出
curl -s -m 2 http://127.0.0.1:8000/api/health >nul 2>&1
if %errorlevel%==0 (
  echo   MessageHub 已在运行，直接打开页面。
  start "" /B "%COLLECTOR_PYTHON%" "%ROOT%\scripts\moect_live.py" --loop --interval 120 --no-refresh --selected-groups --include-private
  start "" "http://127.0.0.1:8000/live"
  exit /b 0
)

echo ==========================================
echo   MessageHub 服务启动中...
echo ==========================================
echo.
echo   Python : %PYTHON%
echo   项目   : %ROOT%
echo.
echo   工作台      : http://127.0.0.1:8000/
echo   实时消息流  : http://127.0.0.1:8000/live
echo.
echo   保持此窗口开启即可使用，关闭窗口即停止服务。
echo ==========================================
echo.

start "" "http://127.0.0.1:8000/"

rem 后台常驻：Moect 解密快照 -> 实时消息采集（最小化窗口，日志 runtime\moect_live.log）
start "" /B "%COLLECTOR_PYTHON%" "%ROOT%\scripts\moect_live.py" --loop --interval 120 --no-refresh --selected-groups --include-private

"%PYTHON%" -m uvicorn messagehub.app:app --host 127.0.0.1 --port 8000 --log-level warning

echo.
echo 服务已停止。按任意键关闭窗口。
pause >nul
