@echo off
rem 看板服务启动器 —— 双击即可
rem 挂件是开机自启的，但它只「读」看板；看板服务得有个东西把它拉起来。
chcp 65001 >nul
cd /d "%~dp0"

where pythonw >nul 2>nul && (start "" pythonw svc.py start) || (
  echo 找不到 Python。请先安装依赖：pip install -r requirements.txt
  pause
)
