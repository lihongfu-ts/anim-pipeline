@echo off
setlocal
cd /d "%~dp0"
title anim-pipeline 一键安装
echo.
echo  [anim-pipeline] 一键安装：Python 依赖 + ffmpeg
echo.

rem ---- 找 Python：python / py -3 都试；微软商店那个假 python 跑不了 print 会被排除
set PY=
python -c "print(1)" >nul 2>nul && set PY=python
if not defined PY ( py -3 -c "print(1)" >nul 2>nul && set PY=py -3 )
if not defined PY (
  echo  [!] 没找到 Python，尝试用 winget 安装 Python 3.12 ...
  where winget >nul 2>nul || goto :nowinget
  winget install -e --id Python.Python.3.12 --accept-source-agreements --accept-package-agreements
  if errorlevel 1 goto :nowinget
  echo.
  echo  [OK] Python 装好了。请关掉本窗口，重新双击 安装.bat（让 PATH 生效）。
  pause
  exit /b 0
)
for /f "tokens=*" %%v in ('%PY% --version 2^>^&1') do echo  [OK] %%v

echo.
echo  [1/2] 安装 Python 依赖（numpy / Pillow / fastapi / uvicorn / python-multipart）...
%PY% -m pip install --disable-pip-version-check -q -r requirements.txt -r web\requirements.txt
if errorlevel 1 (
  echo  [!] 官方源没成，换清华镜像重试 ...
  %PY% -m pip install --disable-pip-version-check -q -r requirements.txt -r web\requirements.txt -i https://pypi.tuna.tsinghua.edu.cn/simple
  if errorlevel 1 (
    echo  [X] 依赖安装失败，把上面的报错发出来。
    pause
    exit /b 1
  )
)
echo  [OK] Python 依赖装好

echo.
echo  [2/2] 安装 ffmpeg（视频抽帧用；已装会直接跳过）...
%PY% tools\install_ffmpeg.py
if errorlevel 1 (
  echo  [X] ffmpeg 没装上，看上面的提示。
  pause
  exit /b 1
)

echo.
echo  [OK] 全部装好。双击 启动.bat 打开网页，在页面里填 key。
echo.
pause
exit /b 0

:nowinget
echo.
echo  [X] 自动装 Python 没成。请手动：
echo      1. 打开 https://www.python.org/downloads/  下载 Python 3.10 以上
echo      2. 安装时勾选 "Add python.exe to PATH"
echo      3. 装完重新双击 安装.bat
pause
exit /b 1
