@echo off
rem 把最新构建的挂件复制到桌面。
rem 挂件在运行时会占住 exe，所以要先把进程结束掉。
chcp 65001 >nul
cd /d "%~dp0"

echo 正在结束运行中的挂件...
taskkill /F /IM BoardWidget.exe >nul 2>&1
timeout /t 1 >nul

echo 正在复制到桌面...
copy /Y "dist\BoardWidget.exe" "%USERPROFILE%\Desktop\BoardWidget.exe" >nul
if errorlevel 1 (
  echo   失败：请手动关闭挂件（托盘图标右键 → 退出）后重试
) else (
  echo   完成。桌面挂件已更新。
)
echo.
pause
