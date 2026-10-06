@echo off
rem 把「看板服务」也加入开机启动 —— 双击运行一次即可
rem
rem 为什么需要它：挂件是开机自启的，但它只负责「看」。看板服务不启，
rem 挂件开机后永远是「连不上」。这两件事必须一起解决。
chcp 65001 >nul
cd /d "%~dp0"

rem 用 PATH 上的 pythonw.exe（无控制台窗口的 GUI 解释器）
set VENV_PY=pythonw

echo 正在把看板服务加入开机启动...
reg add "HKCU\Software\Microsoft\Windows\CurrentVersion\Run" /v WorkBuddyBoardService /t REG_SZ /d "\"%VENV_PY%\" \"%~dp0svc.py\" start" /f >nul
if errorlevel 1 (
  echo   失败。请手动把下面这行加到注册表 Run 项：
  echo   "%VENV_PY%" "%~dp0svc.py" start
) else (
  echo   已加入。下次开机看板服务会自动启动。
)
echo.
echo 想取消：reg delete "HKCU\Software\Microsoft\Windows\CurrentVersion\Run" /v WorkBuddyBoardService /f
echo.
pause
