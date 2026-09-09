@echo off
setlocal
cd /d "%~dp0"
title anim-pipeline
set PY=
python -c "print(1)" >nul 2>nul && set PY=python
if not defined PY ( py -3 -c "print(1)" >nul 2>nul && set PY=py -3 )
if not defined PY (
  echo  [X] 没找到 Python，请先双击 安装.bat
  pause
  exit /b 1
)
echo  [anim-pipeline] 启动网页端 -^> http://127.0.0.1:8765   （关掉本窗口即停止服务）
start "" cmd /c "timeout /t 2 >nul & start http://127.0.0.1:8765"
rem ---- 下面切到 UTF-8 只为了 server.py 的日志不乱码；切完之后的行必须是纯 ASCII
chcp 65001 >nul
%PY% web\server.py
echo.
echo  server stopped.
pause
