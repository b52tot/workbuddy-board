@echo off
rem 任务挂件启动器 —— 双击即可
rem 用 pythonw.exe（无控制台窗口），GUI 程序不该带一个黑框
chcp 65001 >nul
cd /d "%~dp0"

where pythonw >nul 2>nul && (start "" pythonw host.py %*) || (
  echo 找不到 Python。请先安装 pywebview：pip install pywebview
  pause
)
